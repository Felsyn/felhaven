"""
test_echo.py — hermetic tests for tools/echo.py.

No audio hardware, no kokoro model, no ffmpeg binary, no network: calliope's
synthesize() and echo's ffmpeg subprocess call are both mocked. Covers Markdown
stripping, chunk + concatenation ORDER, filename sanitisation (incl. reserved
device names), the empty-text / empty-filename guards, missing ffmpeg, and the
ffmpeg-present-but-encode-fails path.

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

    def test_reserved_names_rejected(self):
        for reserved in ("CON", "nul", "Com1", "LPT9", "con.opus"):
            self.assertEqual(echo.sanitize_filename(reserved), "",
                             f"{reserved!r} should be rejected")


class TestTextToAudioGuards(unittest.TestCase):
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


class TestTextToAudioConversion(unittest.TestCase):
    def test_success_writes_path_and_concatenates_in_order(self):
        calls: list[str] = []

        def fake_synth(chunk: str):
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

    def test_synthesis_failure(self):
        with mock.patch.object(echo, "_resolve", return_value="ffmpeg"), \
                mock.patch.object(calliope, "synthesize", return_value=None):
            self.assertEqual(echo.text_to_audio("hello world.", "out"),
                             {"error": "synthesis_failed"})

    def test_encode_failure_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "out.opus")
            # Simulate a partial file left by a failed encode.
            with open(out_path, "wb") as f:
                f.write(b"partial")

            with mock.patch.object(echo, "_LOCAL_AUDIO_DIR", tmp), \
                    mock.patch.object(echo, "_resolve", return_value="ffmpeg"), \
                    mock.patch.object(
                        calliope, "synthesize",
                        return_value=np.array([0.0], dtype=np.float32)), \
                    mock.patch.object(calliope, "save_wav"), \
                    mock.patch("tools.echo.subprocess.run",
                               return_value=_FakeProc(1)):
                result = echo.text_to_audio("hello world.", "out")

            self.assertEqual(result, {"error": "ffmpeg_encode_failed"})
            self.assertFalse(os.path.exists(out_path),
                             "partial output should be removed on encode failure")


if __name__ == "__main__":
    unittest.main(verbosity=2)
