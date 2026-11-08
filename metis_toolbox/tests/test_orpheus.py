"""
test_orpheus.py — hermetic tests for tools/orpheus.py.

No real ffmpeg process, no real audio device, no network, no real Recycle Bin:
subprocess.run, harmonia.play/stop/is_playing and SHFileOperationW are all
mocked. Covers available(), the local_audio/ listing, the safe-path guard (bad
names never reach ffmpeg or the shell), decode success/failure/timeout, duration
probing (+ its cache), delete_file()'s refusals/cache purge, and fetch()'s
never-raise / no-ffmpeg shape.

    python -X utf8 -m unittest tests.test_orpheus
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import harmonia
from tools import orpheus

# ffmpeg is only ever handed absolute paths (orpheus's "ffmpeg paths"), so the
# fixtures are absolute too; a relative one is refused before ffmpeg runs.
_IN = os.path.abspath("in.opus")
_X = os.path.abspath("x.opus")


class _FakeProc:
    """Stand-in for subprocess.CompletedProcess (only the fields orpheus reads)."""

    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestAvailable(unittest.TestCase):
    def _aegis(self, path, problem=None):
        return mock.patch.object(orpheus.aegis, "resolve",
                                 return_value=orpheus.aegis.Resolution(path, problem))

    def test_reports_resolved_ffmpeg(self):
        with self._aegis("ffmpeg.exe"):
            self.assertEqual(orpheus.available(), {"ffmpeg": "ffmpeg.exe", "problem": None})

    def test_reports_missing_ffmpeg(self):
        with self._aegis(None):
            self.assertEqual(orpheus.available(), {"ffmpeg": None, "problem": None})

    def test_reports_a_refused_bin_copy(self):
        with self._aegis(None, "binary_unverified: ffmpeg.exe does not match its manifest entry"):
            self.assertEqual(orpheus.available(), {"ffmpeg": None, "problem": "binary_unverified: ffmpeg.exe does not match its manifest entry"})


class TestListFiles(unittest.TestCase):
    def test_lists_files_sorted_and_skips_subdirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("b.opus", "a.opus"):
                open(os.path.join(tmp, name), "wb").close()
            os.mkdir(os.path.join(tmp, "a_subdir"))
            with mock.patch.object(orpheus, "_LOCAL_AUDIO_DIR", tmp):
                self.assertEqual(orpheus.list_files(), ["a.opus", "b.opus"])

    def test_missing_dir_is_empty(self):
        with mock.patch.object(orpheus, "_LOCAL_AUDIO_DIR", "Z:/does/not/exist"):
            self.assertEqual(orpheus.list_files(), [])


class TestSafePath(unittest.TestCase):
    def test_rejects_path_traversal_and_separators(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "real.opus"), "wb").close()
            with mock.patch.object(orpheus, "_LOCAL_AUDIO_DIR", tmp):
                for bad in ("../real.opus", "..\\real.opus", "sub/real.opus",
                            "/real.opus", ""):
                    self.assertIsNone(orpheus._safe_path(bad), f"{bad!r} should be rejected")

    def test_rejects_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(orpheus, "_LOCAL_AUDIO_DIR", tmp):
                self.assertIsNone(orpheus._safe_path("nope.opus"))

    def test_accepts_a_real_bare_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "real.opus"), "wb").close()
            with mock.patch.object(orpheus, "_LOCAL_AUDIO_DIR", tmp):
                self.assertEqual(orpheus._safe_path("real.opus"),
                                 os.path.join(tmp, "real.opus"))


class TestDecode(unittest.TestCase):
    def test_success_reshapes_to_frames_by_channels(self):
        raw = np.zeros(4, dtype="<f4").tobytes()   # 4 mono frames (_CHANNELS = 1)
        with mock.patch("tools.orpheus.subprocess.run",
                        return_value=_FakeProc(0, stdout=raw)):
            pcm = orpheus._decode("ffmpeg", _IN)
        self.assertEqual(pcm.shape, (4, 1))

    def test_nonzero_exit_is_none(self):
        with mock.patch("tools.orpheus.subprocess.run",
                        return_value=_FakeProc(1, stderr=b"boom")):
            self.assertIsNone(orpheus._decode("ffmpeg", _IN))

    def test_empty_output_is_none(self):
        with mock.patch("tools.orpheus.subprocess.run",
                        return_value=_FakeProc(0, stdout=b"")):
            self.assertIsNone(orpheus._decode("ffmpeg", _IN))

    def test_timeout_is_none(self):
        import subprocess
        with mock.patch("tools.orpheus.subprocess.run",
                        side_effect=subprocess.TimeoutExpired("ffmpeg", 1)):
            self.assertIsNone(orpheus._decode("ffmpeg", _IN))

    def test_a_path_that_isnt_absolute_never_reaches_ffmpeg(self):
        # The fake ffmpeg would SUCCEED, so only the refusal can make this None.
        raw = np.zeros(4, dtype="<f4").tobytes()
        for path in ("-x.opus", "concat:a.opus|b.opus", "in.opus"):
            with self.subTest(path=path), \
                    mock.patch("tools.orpheus.subprocess.run",
                               return_value=_FakeProc(0, stdout=raw)) as run:
                self.assertIsNone(orpheus._decode("ffmpeg", path))
                run.assert_not_called()


class TestPlayFile(unittest.TestCase):
    def test_bad_name_never_reaches_ffmpeg(self):
        with mock.patch.object(orpheus, "_resolve") as resolve:
            self.assertEqual(orpheus.play_file("../etc/passwd"), {"error": "bad_name"})
            resolve.assert_not_called()

    def test_ffmpeg_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "a.opus"), "wb").close()
            with mock.patch.object(orpheus, "_LOCAL_AUDIO_DIR", tmp), \
                    mock.patch.object(orpheus, "_resolve", return_value=None):
                self.assertEqual(orpheus.play_file("a.opus"),
                                 {"error": "ffmpeg_unavailable"})

    def test_decode_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "a.opus"), "wb").close()
            with mock.patch.object(orpheus, "_LOCAL_AUDIO_DIR", tmp), \
                    mock.patch.object(orpheus, "_resolve", return_value="ffmpeg"), \
                    mock.patch.object(orpheus, "_decode", return_value=None):
                self.assertEqual(orpheus.play_file("a.opus"), {"error": "decode_failed"})

    def test_success_hands_pcm_to_harmonia_at_24k_mono(self):
        # 24 kHz mono, matching Echo's own output exactly (kokoro synthesizes
        # mono @ 24 kHz; neither calliope.save_wav() nor Echo's ffmpeg encode
        # resamples/remixes) — NOT a generic decode target. Decoding at a
        # higher rate/channel count than the source has would just upsample
        # and duplicate a channel for zero new information, at real RAM cost.
        pcm = np.zeros((4, 1), dtype=np.float32)
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "a.opus"), "wb").close()
            with mock.patch.object(orpheus, "_LOCAL_AUDIO_DIR", tmp), \
                    mock.patch.object(orpheus, "_resolve", return_value="ffmpeg"), \
                    mock.patch.object(orpheus, "_decode", return_value=pcm), \
                    mock.patch.object(harmonia, "play") as hplay:
                result = orpheus.play_file("a.opus")
        self.assertEqual(result, {"playing": "a.opus"})
        args, kwargs = hplay.call_args
        self.assertIs(args[0], pcm)
        self.assertEqual(args[1], 24000)          # D3 — the explicit rate check
        self.assertEqual(kwargs.get("tag"), "orpheus")


class TestStop(unittest.TestCase):
    def test_stop_calls_harmonia_stop(self):
        with mock.patch.object(harmonia, "stop") as hstop:
            orpheus.stop()
        hstop.assert_called_once()


class TestProbeDuration(unittest.TestCase):
    """ffmpeg's own metadata banner, not ffprobe — see the module docstring."""

    def test_parses_duration_from_stderr(self):
        stderr = b"Input #0, ogg, from 'x.opus':\n  Duration: 00:03:15.23, start: 0, bitrate: 96 kb/s\n"
        with mock.patch("tools.orpheus.subprocess.run",
                        return_value=_FakeProc(1, stderr=stderr)):
            self.assertAlmostEqual(orpheus._probe_duration("ffmpeg", _X),
                                   3 * 60 + 15.23, places=2)

    def test_nonzero_exit_is_expected_not_a_failure(self):
        # ffmpeg with no output always exits nonzero — that's normal here.
        stderr = b"Duration: 00:00:05.00, start: 0, bitrate: 64 kb/s\n"
        with mock.patch("tools.orpheus.subprocess.run",
                        return_value=_FakeProc(1, stderr=stderr)):
            self.assertEqual(orpheus._probe_duration("ffmpeg", _X), 5.0)

    def test_no_duration_line_is_none(self):
        with mock.patch("tools.orpheus.subprocess.run",
                        return_value=_FakeProc(1, stderr=b"nothing useful here")):
            self.assertIsNone(orpheus._probe_duration("ffmpeg", _X))

    def test_timeout_is_none(self):
        import subprocess
        with mock.patch("tools.orpheus.subprocess.run",
                        side_effect=subprocess.TimeoutExpired("ffmpeg", 1)):
            self.assertIsNone(orpheus._probe_duration("ffmpeg", _X))

    def test_a_path_that_isnt_absolute_never_reaches_ffmpeg(self):
        # The fake ffmpeg would SUCCEED, so only the refusal can make this None.
        stderr = b"Duration: 00:00:05.00, start: 0, bitrate: 64 kb/s\n"
        for path in ("-x.opus", "concat:a.opus|b.opus", "x.opus"):
            with self.subTest(path=path), \
                    mock.patch("tools.orpheus.subprocess.run",
                               return_value=_FakeProc(1, stderr=stderr)) as run:
                self.assertIsNone(orpheus._probe_duration("ffmpeg", path))
                run.assert_not_called()


class TestFileRows(unittest.TestCase):
    def setUp(self):
        self._saved_cache = dict(orpheus._duration_cache)
        orpheus._duration_cache.clear()

    def tearDown(self):
        orpheus._duration_cache.clear()
        orpheus._duration_cache.update(self._saved_cache)

    def test_probes_each_file_once_then_caches(self):
        with mock.patch.object(orpheus, "_resolve", return_value="ffmpeg"), \
                mock.patch.object(orpheus, "list_files", return_value=["a.opus"]), \
                mock.patch.object(orpheus, "_probe_duration", return_value=12.5) as probe:
            rows = orpheus._file_rows()
            self.assertEqual(rows, [{"name": "a.opus", "duration": 12.5}])
            orpheus._file_rows()   # second tick: cached, no re-probe
        probe.assert_called_once()

    def test_failed_probe_is_not_cached_and_retried(self):
        with mock.patch.object(orpheus, "_resolve", return_value="ffmpeg"), \
                mock.patch.object(orpheus, "list_files", return_value=["a.opus"]), \
                mock.patch.object(orpheus, "_probe_duration", return_value=None) as probe:
            orpheus._file_rows()
            orpheus._file_rows()
        self.assertEqual(probe.call_count, 2)   # retried, not stuck at None forever

    def test_no_ffmpeg_yields_none_durations_without_probing(self):
        with mock.patch.object(orpheus, "_resolve", return_value=None), \
                mock.patch.object(orpheus, "list_files", return_value=["a.opus"]), \
                mock.patch.object(orpheus, "_probe_duration") as probe:
            self.assertEqual(orpheus._file_rows(), [{"name": "a.opus", "duration": None}])
        probe.assert_not_called()


class TestDeleteFile(unittest.TestCase):
    """_send_to_recycle_bin is mocked throughout — no test ever touches the
    real Recycle Bin, and the shell call is never made."""

    def setUp(self):
        self._saved_cache = dict(orpheus._duration_cache)
        orpheus._duration_cache.clear()

    def tearDown(self):
        orpheus._duration_cache.clear()
        orpheus._duration_cache.update(self._saved_cache)

    def test_bad_names_are_refused_before_the_shell_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "real.opus"), "wb").close()
            with mock.patch.object(orpheus, "_LOCAL_AUDIO_DIR", tmp), \
                    mock.patch.object(orpheus, "_send_to_recycle_bin") as recycle:
                for bad in ("../real.opus", "..\\real.opus", "sub/real.opus",
                            "/real.opus", "", "nope.opus"):
                    self.assertEqual(orpheus.delete_file(bad), {"error": "bad_name"},
                                     f"{bad!r} should be refused")
                recycle.assert_not_called()

    def test_success_recycles_the_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.opus")
            open(path, "wb").close()
            with mock.patch.object(orpheus, "_LOCAL_AUDIO_DIR", tmp), \
                    mock.patch.object(orpheus, "_send_to_recycle_bin",
                                      return_value=True) as recycle:
                self.assertEqual(orpheus.delete_file("a.opus"), {"deleted": "a.opus"})
            recycle.assert_called_once_with(path)

    def test_recycle_failure_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "a.opus"), "wb").close()
            with mock.patch.object(orpheus, "_LOCAL_AUDIO_DIR", tmp), \
                    mock.patch.object(orpheus, "_send_to_recycle_bin",
                                      return_value=False):
                self.assertEqual(orpheus.delete_file("a.opus"),
                                 {"error": "delete_failed"})

    def test_success_purges_the_duration_cache(self):
        # Left behind, a stale entry would give a later same-named file the
        # dead one's duration.
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "a.opus"), "wb").close()
            orpheus._duration_cache["a.opus"] = 12.5
            with mock.patch.object(orpheus, "_LOCAL_AUDIO_DIR", tmp), \
                    mock.patch.object(orpheus, "_send_to_recycle_bin",
                                      return_value=True):
                orpheus.delete_file("a.opus")
        self.assertNotIn("a.opus", orpheus._duration_cache)

    def test_failure_leaves_the_duration_cache_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "a.opus"), "wb").close()
            orpheus._duration_cache["a.opus"] = 12.5
            with mock.patch.object(orpheus, "_LOCAL_AUDIO_DIR", tmp), \
                    mock.patch.object(orpheus, "_send_to_recycle_bin",
                                      return_value=False):
                orpheus.delete_file("a.opus")
        self.assertEqual(orpheus._duration_cache.get("a.opus"), 12.5)


class TestSendToRecycleBin(unittest.TestCase):
    """The shell call itself — mocked at the ctypes boundary, so no file is
    ever really binned and the suite stays hermetic."""

    @unittest.skipUnless(sys.platform == "win32", "SHFileOperationW is Windows-only")
    def test_passes_a_double_nul_terminated_abspath_with_allowundo(self):
        captured = {}

        def fake_shfileop(ref):
            op = ref._obj
            captured["pFrom"] = op.pFrom
            captured["fFlags"] = op.fFlags
            captured["wFunc"] = op.wFunc
            return 0

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.opus")
            open(path, "wb").close()
            with mock.patch("ctypes.windll.shell32.SHFileOperationW",
                            side_effect=fake_shfileop):
                self.assertTrue(orpheus._send_to_recycle_bin(path))

        # ctypes strips the trailing NULs off a LPCWSTR read-back, so assert on
        # the flags and the path proper; the terminator is set in the source.
        self.assertEqual(captured["wFunc"], orpheus._FO_DELETE)
        self.assertTrue(os.path.isabs(captured["pFrom"].rstrip("\0")))
        self.assertTrue(captured["fFlags"] & orpheus._FOF_ALLOWUNDO,
                        "ALLOWUNDO must be set or the file is erased, not binned")

    @unittest.skipUnless(sys.platform == "win32", "SHFileOperationW is Windows-only")
    def test_nonzero_return_is_false_not_an_exception(self):
        with mock.patch("ctypes.windll.shell32.SHFileOperationW", return_value=124):
            self.assertFalse(orpheus._send_to_recycle_bin("C:\\nope\\a.opus"))

    @unittest.skipUnless(sys.platform == "win32", "SHFileOperationW is Windows-only")
    def test_oserror_is_swallowed(self):
        with mock.patch("ctypes.windll.shell32.SHFileOperationW",
                        side_effect=OSError("boom")):
            self.assertFalse(orpheus._send_to_recycle_bin("C:\\nope\\a.opus"))


class TestFetch(unittest.TestCase):
    def test_no_ffmpeg_degrades(self):
        # The slug matches play_file()/Echo — one vocabulary for "ffmpeg missing".
        with mock.patch.object(orpheus, "available", return_value={"ffmpeg": None}):
            self.assertEqual(orpheus.fetch(),
                             {"playing": False, "files": [], "error": "ffmpeg_unavailable"})

    def test_reports_playing_and_files(self):
        rows = [{"name": "a.opus", "duration": 12.5}]
        with mock.patch.object(orpheus, "available", return_value={"ffmpeg": "ffmpeg"}), \
                mock.patch.object(harmonia, "is_playing", return_value=True), \
                mock.patch.object(orpheus, "_file_rows", return_value=rows):
            self.assertEqual(orpheus.fetch(), {"playing": True, "files": rows})


if __name__ == "__main__":
    unittest.main(verbosity=2)
