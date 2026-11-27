"""
test_echo.py — hermetic tests for tools/echo.py.

No audio hardware, no kokoro model, no ffmpeg binary, no network: calliope's
synthesize() and echo's ffmpeg subprocess call are both mocked, and
local_audio/ is a scratch folder in every conversion test. Covers Markdown
stripping, chunk + concatenation ORDER, filename sanitisation (incl. every
form of reserved device name), the empty-text / empty-filename guards,
missing ffmpeg, write-once (a taken name is refused before synthesis and again
at the claim, and the kept file survives), and the encode-fails and
launch-raises paths.

    python -X utf8 -m unittest tests.test_echo
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import calliope
from tools import echo


class _FakeProc:
    """Stand-in for subprocess.CompletedProcess (only the fields echo reads)."""

    def __init__(self, returncode: int):
        self.returncode = returncode
        self.stderr = b""


class TestStripMarkdown(unittest.TestCase):
    def test_headers_links_images_code_lists(self):
        md = (
            "# Title\n\n"
            "Some **bold** and [link text](https://example.com) and "
            "![alt words](https://img.example.com/x.png).\n\n"
            "```python\nsecret_code_line()\n```\n\n"
            "- a bullet\n"
            "1. numbered\n"
            "> a quote\n"
            "| col a | col b |\n"
        )
        out = echo._strip_markdown(md)

        self.assertIn("Title", out)          # heading text kept
        self.assertIn("link text", out)      # link unwrapped to its text
        self.assertNotIn("https", out)       # url dropped
        self.assertNotIn("alt words", out)   # image dropped entirely
        self.assertNotIn("secret_code_line", out)  # fenced code dropped
        self.assertIn("a bullet", out)       # list text kept, marker gone
        self.assertIn("numbered", out)
        self.assertIn("a quote", out)        # blockquote text kept
        for ch in "#*`>|":
            self.assertNotIn(ch, out)

    def test_inline_code_keeps_text(self):
        self.assertEqual(echo._strip_markdown("run `git status` now"),
                         "run git status now")

    def test_tilde_fence_dropped(self):
        out = echo._strip_markdown("before\n~~~\nDROPPED\n~~~\nafter")
        self.assertNotIn("DROPPED", out)
        self.assertIn("before", out)
        self.assertIn("after", out)


class TestSanitizeFilename(unittest.TestCase):
    def test_appends_opus_when_missing(self):
        self.assertEqual(echo.sanitize_filename("report"), "report.opus")

    def test_keeps_existing_opus(self):
        self.assertEqual(echo.sanitize_filename("report.opus"), "report.opus")

    def test_strips_path_and_illegal_chars(self):
        self.assertEqual(echo.sanitize_filename('a/b\\c:d*?"<>|e'), "abcde.opus")

    def test_all_illegal_is_empty(self):
        self.assertEqual(echo.sanitize_filename('///:*?'), "")

    def test_leading_trailing_dots_and_spaces(self):
        self.assertEqual(echo.sanitize_filename("  ..name..  "), "name.opus")

    def test_every_name_windows_reserves_is_refused(self):
        # The forms a list of the twenty-two plain names let through
        # (measured 2026-11-21): trailing spaces, superscript digits, and the
        # console's own two names.
        for name in ("con .opus", "aux .opus", "COM¹", "LPT².opus",
                     "CONIN$", "CONOUT$.opus"):
            with self.subTest(name=name):
                self.assertEqual(echo.sanitize_filename(name), "")

    def test_reserved_names_rejected(self):
        for reserved in ("CON", "nul", "Com1", "LPT9", "con.opus"):
            self.assertEqual(echo.sanitize_filename(reserved), "",
                             f"{reserved!r} should be rejected")


class TestAvailable(unittest.TestCase):
    """available() is the panel's build-time ffmpeg gate (mirrors orpheus)."""

    def _aegis(self, path, problem=None):
        return mock.patch.object(echo.aegis, "resolve",
                                 return_value=echo.aegis.Resolution(path, problem))

    def test_reports_resolved_ffmpeg(self):
        with self._aegis("C:\\bin\\ffmpeg.exe"):
            self.assertEqual(echo.available(),
                             {"ffmpeg": "C:\\bin\\ffmpeg.exe", "problem": None})

    def test_reports_none_when_missing(self):
        with self._aegis(None):
            self.assertEqual(echo.available(), {"ffmpeg": None, "problem": None})

    def test_reports_a_refused_bin_copy(self):
        with self._aegis(None, "binary_unverified: ffmpeg.exe does not match its manifest entry"):
            self.assertEqual(echo.available(), {"ffmpeg": None, "problem": "binary_unverified: ffmpeg.exe does not match its manifest entry"})


class _ScratchAudioDir(unittest.TestCase):
    """Points local_audio/ at a scratch folder for every test, so no test
    reads or writes the real one (text_to_audio checks the name there before
    synthesis). A test that patches it again just nests."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.audio_dir = tmp.name
        patcher = mock.patch.object(echo, "_LOCAL_AUDIO_DIR", tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestTextToAudioGuards(_ScratchAudioDir):
    def test_empty_filename(self):
        self.assertEqual(echo.text_to_audio("hello world.", "///"),
                         {"error": "empty_filename"})

    def test_empty_text(self):
        self.assertEqual(echo.text_to_audio("   \n\n  ", "out"),
                         {"error": "empty_text"})

    def test_missing_ffmpeg(self):
        with mock.patch.object(echo, "_resolve", return_value=None):
            self.assertEqual(echo.text_to_audio("hello world.", "out"),
                             {"error": "ffmpeg_unavailable"})


class TestTextToAudioConversion(_ScratchAudioDir):
    def test_success_writes_path_and_concatenates_in_order(self):
        calls: list[str] = []

        def fake_synth(chunk: str, *, voice=None):
            calls.append(chunk)
            # Each chunk contributes its 1-based call index as a marker sample,
            # so the concatenated array reveals both order and completeness.
            return np.array([float(len(calls))], dtype=np.float32)

        captured: dict[str, np.ndarray] = {}

        def fake_save(path, pcm):
            captured["pcm"] = np.array(pcm)

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(echo, "_LOCAL_AUDIO_DIR", tmp), \
                mock.patch.object(echo, "_CHUNK_MAX_CHARS", 10), \
                mock.patch.object(echo, "_resolve", return_value="ffmpeg"), \
                mock.patch.object(calliope, "synthesize", side_effect=fake_synth), \
                mock.patch.object(calliope, "save_wav", side_effect=fake_save), \
                mock.patch("tools.echo.subprocess.run",
                           return_value=_FakeProc(0)):
            text = "one two three four five six seven eight nine ten."
            result = echo.text_to_audio(text, "out")

        self.assertIn("path", result)
        self.assertTrue(result["path"].endswith("out.opus"))
        self.assertGreaterEqual(len(calls), 2)  # the text was actually chunked
        # Concatenation preserved chunk order: [1.0, 2.0, ..., N].
        self.assertEqual(list(captured["pcm"]),
                         [float(i + 1) for i in range(len(calls))])

    def test_voice_reaches_synthesize_on_every_chunk(self):
        # Echo's per-conversion voice must not leak into narration: it travels
        # as a synthesize() kwarg, never through calliope.set_voice().
        seen: list = []

        def fake_synth(chunk: str, *, voice=None):
            seen.append(voice)
            return np.array([0.0], dtype=np.float32)

        before = calliope.narration_voice()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(echo, "_LOCAL_AUDIO_DIR", tmp), \
                mock.patch.object(echo, "_CHUNK_MAX_CHARS", 10), \
                mock.patch.object(echo, "_resolve", return_value="ffmpeg"), \
                mock.patch.object(calliope, "synthesize", side_effect=fake_synth), \
                mock.patch.object(calliope, "save_wav"), \
                mock.patch("tools.echo.subprocess.run",
                           return_value=_FakeProc(0)):
            echo.text_to_audio("one two three four five six seven.", "out",
                               voice="bm_george")

        self.assertGreaterEqual(len(seen), 2)
        self.assertEqual(set(seen), {"bm_george"})
        self.assertEqual(calliope.narration_voice(), before)

    def test_no_voice_defers_to_calliope(self):
        seen: list = []

        def fake_synth(chunk: str, *, voice=None):
            seen.append(voice)
            return np.array([0.0], dtype=np.float32)

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(echo, "_LOCAL_AUDIO_DIR", tmp), \
                mock.patch.object(echo, "_resolve", return_value="ffmpeg"), \
                mock.patch.object(calliope, "synthesize", side_effect=fake_synth), \
                mock.patch.object(calliope, "save_wav"), \
                mock.patch("tools.echo.subprocess.run",
                           return_value=_FakeProc(0)):
            echo.text_to_audio("hello world.", "out")

        self.assertEqual(seen, [None])   # None = "whatever Calliope is set to"

    def test_synthesis_failure(self):
        with mock.patch.object(echo, "_resolve", return_value="ffmpeg"), \
                mock.patch.object(calliope, "synthesize", return_value=None):
            self.assertEqual(echo.text_to_audio("hello world.", "out"),
                             {"error": "synthesis_failed"})

    def _convert(self, run, synth=None):
        """text_to_audio("hello world.", "out") with the model, the WAV
        writer and ffmpeg faked; `run` stands in for subprocess.run."""
        synth = synth or mock.MagicMock(return_value=np.array([0.0], dtype=np.float32))
        with mock.patch.object(echo, "_resolve", return_value="ffmpeg"), \
                mock.patch.object(calliope, "synthesize", synth), \
                mock.patch.object(calliope, "save_wav"), \
                mock.patch("tools.echo.subprocess.run", run):
            return echo.text_to_audio("hello world.", "out")

    def test_encode_failure_removes_partial_file(self):
        out_path = os.path.join(self.audio_dir, "out.opus")

        def half_write(cmd, **kwargs):
            # ffmpeg got partway into the file Echo claimed, then failed.
            with open(cmd[-1], "wb") as f:
                f.write(b"partial")
            return _FakeProc(1)

        with self.assertLogs("METIS.echo", "WARNING"):
            self.assertEqual(self._convert(mock.MagicMock(side_effect=half_write)),
                             {"error": "ffmpeg_encode_failed"})
        self.assertFalse(os.path.exists(out_path),
                         "partial output should be removed on encode failure")

    def test_a_launch_that_raises_leaves_no_file(self):
        # ffmpeg vanishing between the check and the launch: the claimed name
        # must not stay behind as an empty .opus in the ORPHEUS list.
        with self.assertLogs("METIS.echo", "WARNING"):
            result = self._convert(mock.MagicMock(side_effect=FileNotFoundError()))
        self.assertEqual(result, {"error": "ffmpeg_encode_failed"})
        self.assertEqual(os.listdir(self.audio_dir), [])


class TestWriteOnce(_ScratchAudioDir):
    """Echo never replaces a file in local_audio/ (its "Write-once" section).
    Until 2026-11-21 it overwrote, and a failed encode deleted the old file."""

    def _keep(self, content=b"a file the owner kept"):
        path = os.path.join(self.audio_dir, "out.opus")
        with open(path, "wb") as f:
            f.write(content)
        return path

    def test_a_name_already_there_is_refused_before_synthesis(self):
        path = self._keep()
        synth = mock.MagicMock(return_value=np.array([0.0], dtype=np.float32))
        run = mock.MagicMock(return_value=_FakeProc(0))
        with mock.patch.object(echo, "_resolve", return_value="ffmpeg"), \
                mock.patch.object(calliope, "synthesize", synth), \
                mock.patch.object(calliope, "save_wav"), \
                mock.patch("tools.echo.subprocess.run", run):
            self.assertEqual(echo.text_to_audio("hello world.", "out"),
                             {"error": "file_exists"})
        synth.assert_not_called()   # no minutes of synthesis for nothing
        run.assert_not_called()
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"a file the owner kept")

    def test_a_name_taken_during_synthesis_is_refused_and_kept(self):
        # The early check passed; then something else wrote the name while
        # the model ran. The exclusive create catches it, and ffmpeg never
        # runs, so the failure path can't remove that file.
        def synth_while_another_writes(chunk, *, voice=None):
            self._keep(b"written during synthesis")
            return np.array([0.0], dtype=np.float32)

        run = mock.MagicMock(return_value=_FakeProc(1))
        with mock.patch.object(echo, "_resolve", return_value="ffmpeg"), \
                mock.patch.object(calliope, "synthesize",
                                  side_effect=synth_while_another_writes), \
                mock.patch.object(calliope, "save_wav"), \
                mock.patch("tools.echo.subprocess.run", run):
            with self.assertLogs("METIS.echo", "WARNING"):
                self.assertEqual(echo.text_to_audio("hello world.", "out"),
                                 {"error": "file_exists"})
        run.assert_not_called()
        with open(os.path.join(self.audio_dir, "out.opus"), "rb") as f:
            self.assertEqual(f.read(), b"written during synthesis")


class TestEncodeOnlyTakesAbsolutePaths(unittest.TestCase):
    """ffmpeg reads '-x' as an option and 'name:' as a protocol (see
    _encode_opus), so neither path reaches it unless it is absolute. The fake
    ffmpeg would SUCCEED, so only the refusal can produce the error."""

    def test_a_wav_path_that_isnt_absolute_never_reaches_ffmpeg(self):
        out = os.path.abspath("out.opus")
        for wav in ("-x.wav", "concat:a.wav|b.wav", "in.wav"):
            with self.subTest(wav=wav), \
                    mock.patch("tools.echo.subprocess.run",
                               return_value=_FakeProc(0)) as run:
                with self.assertLogs("METIS.echo", "WARNING"):
                    self.assertEqual(echo._encode_opus("ffmpeg", wav, out),
                                     {"error": "ffmpeg_encode_failed"})
                run.assert_not_called()

    def test_an_output_path_that_isnt_absolute_never_reaches_ffmpeg(self):
        wav = os.path.abspath("in.wav")
        for out in ("-y.opus", "http:x.opus", "out.opus"):
            with self.subTest(out=out), \
                    mock.patch("tools.echo.subprocess.run",
                               return_value=_FakeProc(0)) as run:
                with self.assertLogs("METIS.echo", "WARNING"):
                    self.assertEqual(echo._encode_opus("ffmpeg", wav, out),
                                     {"error": "ffmpeg_encode_failed"})
                run.assert_not_called()

    def test_absolute_paths_do(self):
        # The output lands in a scratch folder: _encode_opus claims the name
        # with a real exclusive create before ffmpeg (the fake) runs.
        with tempfile.TemporaryDirectory() as tmp:
            wav, out = os.path.abspath("in.wav"), os.path.join(tmp, "out.opus")
            with mock.patch("tools.echo.subprocess.run",
                            return_value=_FakeProc(0)) as run:
                self.assertEqual(echo._encode_opus("ffmpeg", wav, out), {"path": out})
            run.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
