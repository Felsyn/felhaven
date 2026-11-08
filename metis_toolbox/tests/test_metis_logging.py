"""
test_metis_logging.py — first coverage for the shared logging setup.

Hermetic: setup_logging() configures the *global* root logger, and the rest of
the suite deliberately never calls it (§5), so each test here fully snapshots
and restores the root logger's handlers / level / guard attrs, and redirects
LOG_DIR to a throwaway temp dir. Handlers are closed in tearDown so Windows
releases the log file before the temp dir is removed.

Covers the module's promises: the delimited on-disk format Emanon parses,
one entry per record (a line break in a message or a traceback can't start an
entry of its own), idempotency (the guard that stops a second caller doubling
every line), and the never-crash degrade paths.

    python -X utf8 -m unittest tests.test_metis_logging
"""

import io
import logging
import logging.handlers
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metis_logging
from tools import emanon


class MetisLoggingTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="metis_log_"))
        self._orig_log_dir = metis_logging.LOG_DIR
        metis_logging.LOG_DIR = self.tmp

        # Snapshot the root logger, then reset it so setup_logging starts clean.
        self.root = logging.getLogger()
        self._saved_handlers = self.root.handlers[:]
        self._saved_level = self.root.level
        self._saved_guard = getattr(self.root, metis_logging._GUARD_ATTR, None)
        self._saved_path = getattr(self.root, "_metis_log_path", None)
        self.root.handlers = []
        self._clear_guard()

    def tearDown(self):
        # Close the handlers this test added so the temp log file is released.
        for h in self.root.handlers[:]:
            h.close()
        self.root.handlers = self._saved_handlers
        self.root.setLevel(self._saved_level)
        self._clear_guard()
        if self._saved_guard is not None:
            setattr(self.root, metis_logging._GUARD_ATTR, self._saved_guard)
        if self._saved_path is not None:
            setattr(self.root, "_metis_log_path", self._saved_path)
        metis_logging.LOG_DIR = self._orig_log_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _clear_guard(self):
        for attr in (metis_logging._GUARD_ATTR, "_metis_log_path"):
            if hasattr(self.root, attr):
                delattr(self.root, attr)

    @staticmethod
    def _flush():
        for h in logging.getLogger().handlers:
            h.flush()

    # ── Path + format ───────────────────────────────────────────────────────

    def test_returns_the_named_log_path_under_log_dir(self):
        self.assertEqual(metis_logging.setup_logging("felhaven"),
                         self.tmp / "felhaven.log")

    def test_writes_a_delimited_line_emanon_can_split(self):
        metis_logging.setup_logging("prog", level=logging.INFO)
        logging.getLogger("METIS.demo").info("did a thing")
        self._flush()
        text = (self.tmp / "prog.log").read_text(encoding="utf-8")
        line = next(ln for ln in text.splitlines() if "did a thing" in ln)
        fields = line.split(metis_logging._DELIM)
        self.assertEqual(len(fields), 4)          # ts | LEVEL | name | message
        _ts, level, name, message = fields
        self.assertEqual(level.strip(), "INFO")   # padded to 7 chars in the file
        self.assertEqual(name, "METIS.demo")
        self.assertEqual(message, "did a thing")

    # ── One record, one entry (quoted text can't forge a line) ────────────────

    _FORGED = "2026-10-05 12:00:00 | ERROR   | METIS.cerberus | forged"

    def _quiet_setup(self):
        """setup_logging with its console handler writing to a buffer, so the
        hostile lines below stay off the test run's stderr."""
        with mock.patch.object(metis_logging.sys, "stderr", io.StringIO()):
            return metis_logging.setup_logging("prog")

    def _entries(self, path):
        """The log as Emanon reads it: its own tail reader and its own parser."""
        self._flush()
        return [e for e in map(emanon._parse_line, emanon._read_tail(path)) if e]

    def _last(self, path):
        """The newest entry; a failure, not an IndexError, when none parses."""
        entries = self._entries(path)
        self.assertTrue(entries, "no line of the log parses as an entry")
        return entries[-1]

    def _forged(self, path):
        return sum(e["logger"] == "METIS.cerberus" for e in self._entries(path))

    def test_the_breaks_are_every_character_splitlines_ends_a_line_at(self):
        # Emanon splits with str.splitlines(), so ask Python which characters
        # that is, rather than trusting a hand-written list.
        breaks = {c for c in map(chr, range(0x110000))
                  if len(("a" + c + "b").splitlines()) == 2}
        self.assertEqual(set(metis_logging._LINE_BREAKS), breaks)

    def test_a_line_break_in_a_message_cannot_forge_an_entry(self):
        path = self._quiet_setup()
        log = logging.getLogger("METIS.demo")
        for c in metis_logging._LINE_BREAKS:
            with self.subTest(char=ascii(c)):
                before = self._forged(path)
                log.warning("first 200 bytes: junk%s%s", c, self._FORGED)
                self.assertEqual(self._forged(path), before,
                                 f"{ascii(c)} in a message started an entry of its own")
                last = self._last(path)
                self.assertEqual((last["level"], last["logger"]), ("WARNING", "METIS.demo"))
                self.assertIn(ascii(c)[1:-1] + self._FORGED, last["message"])

    def test_a_traceback_cannot_forge_an_entry(self):
        path = self._quiet_setup()
        try:
            raise ValueError("bad reply\n" + self._FORGED)
        except ValueError:
            logging.getLogger("METIS.demo").exception("tick failed")
        self.assertEqual(self._forged(path), 0)
        last = self._last(path)
        self.assertEqual((last["level"], last["logger"], last["message"]),
                         ("ERROR", "METIS.demo", "tick failed"))
        # The traceback keeps its lines; the one quoting the reply can't parse.
        lines = emanon._read_tail(path)
        self.assertTrue(any(ln.startswith("Traceback") for ln in lines))
        quoted = [ln for ln in lines if ln.endswith("forged")]
        self.assertEqual(len(quoted), 1)
        self.assertIn(metis_logging._CONTINUATION_DELIM, quoted[0])

    def test_the_entry_line_keeps_the_delimiters_in_its_message(self):
        path = self._quiet_setup()
        logging.getLogger("METIS.demo").info("a | b")
        self.assertEqual(self._last(path)["message"], "a | b")

    # ── Idempotency (the whole point of centralizing) ─────────────────────────

    def test_second_call_adds_no_handlers_and_returns_same_path(self):
        p1 = metis_logging.setup_logging("prog")
        count = len(self.root.handlers)
        p2 = metis_logging.setup_logging("prog")
        self.assertEqual(p2, p1)
        self.assertEqual(len(self.root.handlers), count)

    def test_idempotent_does_not_double_log_lines(self):
        metis_logging.setup_logging("prog")
        metis_logging.setup_logging("prog")       # must be a no-op
        logging.getLogger("METIS.demo").warning("just once")
        self._flush()
        text = (self.tmp / "prog.log").read_text(encoding="utf-8")
        self.assertEqual(text.count("just once"), 1)

    # ── Never-crash degrade paths ─────────────────────────────────────────────

    def test_mkdir_failure_does_not_propagate(self):
        # A logging failure must never take down the dashboard: mkdir raises,
        # setup_logging still returns the intended path without crashing.
        with mock.patch.object(Path, "mkdir", side_effect=OSError("denied")):
            p = metis_logging.setup_logging("prog")
        self.assertEqual(p, self.tmp / "prog.log")

    def test_file_handler_failure_degrades_to_console_only(self):
        with mock.patch.object(logging.handlers, "RotatingFileHandler",
                               side_effect=OSError("locked")):
            metis_logging.setup_logging("prog")
        # Only the plain console StreamHandler survived; no rotating file handler.
        self.assertTrue(self.root.handlers)
        self.assertFalse(any(isinstance(h, logging.handlers.RotatingFileHandler)
                             for h in self.root.handlers))


if __name__ == "__main__":
    unittest.main()
