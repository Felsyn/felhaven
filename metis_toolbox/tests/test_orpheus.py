"""
test_orpheus.py — hermetic tests for tools/orpheus.py.

No real ffmpeg process, no real audio device, no network: subprocess.run and
harmonia.play/stop/is_playing are all mocked. Covers available(), the
local_audio/ listing, the safe-path guard (bad names never reach ffmpeg),
decode success/failure/timeout, duration probing (+ its cache), and fetch()'s
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


class _FakeProc:
    """Stand-in for subprocess.CompletedProcess (only the fields orpheus reads)."""

    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestAvailable(unittest.TestCase):
    def test_reports_resolved_ffmpeg(self):
        with mock.patch.object(orpheus, "_resolve", return_value="ffmpeg.exe"):
            self.assertEqual(orpheus.available(), {"ffmpeg": "ffmpeg.exe"})

    def test_reports_missing_ffmpeg(self):
        with mock.patch.object(orpheus, "_resolve", return_value=None):
            self.assertEqual(orpheus.available(), {"ffmpeg": None})


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
            pcm = orpheus._decode("ffmpeg", "in.opus")
        self.assertEqual(pcm.shape, (4, 1))

    def test_nonzero_exit_is_none(self):
        with mock.patch("tools.orpheus.subprocess.run",
                        return_value=_FakeProc(1, stderr=b"boom")):
            self.assertIsNone(orpheus._decode("ffmpeg", "in.opus"))

    def test_empty_output_is_none(self):
        with mock.patch("tools.orpheus.subprocess.run",
                        return_value=_FakeProc(0, stdout=b"")):
            self.assertIsNone(orpheus._decode("ffmpeg", "in.opus"))

    def test_timeout_is_none(self):
        import subprocess
        with mock.patch("tools.orpheus.subprocess.run",
                        side_effect=subprocess.TimeoutExpired("ffmpeg", 1)):
            self.assertIsNone(orpheus._decode("ffmpeg", "in.opus"))


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
            self.assertAlmostEqual(orpheus._probe_duration("ffmpeg", "x.opus"),
                                   3 * 60 + 15.23, places=2)

    def test_nonzero_exit_is_expected_not_a_failure(self):
        # ffmpeg with no output always exits nonzero — that's normal here.
        stderr = b"Duration: 00:00:05.00, start: 0, bitrate: 64 kb/s\n"
        with mock.patch("tools.orpheus.subprocess.run",
                        return_value=_FakeProc(1, stderr=stderr)):
            self.assertEqual(orpheus._probe_duration("ffmpeg", "x.opus"), 5.0)

    def test_no_duration_line_is_none(self):
        with mock.patch("tools.orpheus.subprocess.run",
                        return_value=_FakeProc(1, stderr=b"nothing useful here")):
            self.assertIsNone(orpheus._probe_duration("ffmpeg", "x.opus"))

    def test_timeout_is_none(self):
        import subprocess
        with mock.patch("tools.orpheus.subprocess.run",
                        side_effect=subprocess.TimeoutExpired("ffmpeg", 1)):
            self.assertIsNone(orpheus._probe_duration("ffmpeg", "x.opus"))


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


class TestFetch(unittest.TestCase):
    def test_no_ffmpeg_degrades(self):
        with mock.patch.object(orpheus, "available", return_value={"ffmpeg": None}):
            self.assertEqual(orpheus.fetch(),
                             {"playing": False, "files": [], "error": "no_ffmpeg"})

    def test_reports_playing_and_files(self):
        rows = [{"name": "a.opus", "duration": 12.5}]
        with mock.patch.object(orpheus, "available", return_value={"ffmpeg": "ffmpeg"}), \
                mock.patch.object(harmonia, "is_playing", return_value=True), \
                mock.patch.object(orpheus, "_file_rows", return_value=rows):
            self.assertEqual(orpheus.fetch(), {"playing": True, "files": rows})


if __name__ == "__main__":
    unittest.main(verbosity=2)
