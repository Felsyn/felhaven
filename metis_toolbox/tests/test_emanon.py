"""
test_emanon.py — the log watcher: the shared wire format, the verdict, and the
promise never to raise.

Emanon shipped 2026-05-28 with no test. It has exactly one job — read what
metis_logging wrote and say what broke — so the interesting question is not
"does it split a string" but "does it still parse a line that metis_logging's
OWN formatter produced". So the lines under test here are not hand-built
" | " strings: _format_line runs a real logging.Formatter built from
metis_logging._LOG_FORMAT / _DATE_FORMAT / _DELIM, the same wire the live stack
writes. If the delimiter, the column order, or the date format ever change, the
round-trip tests break — which is the lockstep CONVENTIONS §5 asks for and, until
now, only a comment enforced.

Hermetic by construction: fetch() is pointed at a fresh TemporaryDirectory each
test (emanon.LOG_DIR is patched), so nothing here touches the real logs/. No
clock, no network.

Run from the package root:
    python -X utf8 -m unittest tests.test_emanon
"""

import logging
import os
import string
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metis_logging
from tools import emanon


def _uniq(n: int) -> str:
    """A short, digit-free token unique per n (bijective base-52 over letters).

    Bulk fixtures need lines that never fold, and — since the fold key now
    wildcards digits — "line 1", "line 2" would collapse. Letters don't.
    """
    letters = string.ascii_letters
    n, out = n + 1, ""
    while n:
        n, r = divmod(n - 1, len(letters))
        out = letters[r] + out
    return out

# emanon logs a debug on an unreadable file and an error on the outer
# safety-net path. No test entry point calls setup_logging (§5), so logging's
# last-resort handler would print those to stderr and bury the suite output.
logging.getLogger("METIS.emanon").addHandler(logging.NullHandler())


def _format_line(level: str, name: str, message: str,
                 when: str = "2026-05-28 14:32:01") -> str:
    """One log line, produced by metis_logging's OWN formatter.

    Not a hand-assembled " | " string on purpose: this is the real wire format,
    so a change to _LOG_FORMAT / _DATE_FORMAT / _DELIM that would blind Emanon
    turns these tests red instead of shipping. `when` sets a deterministic
    timestamp so sort order is under the test's control.
    """
    formatter = logging.Formatter(metis_logging._LOG_FORMAT,
                                  metis_logging._DATE_FORMAT)
    created = time.mktime(time.strptime(when, "%Y-%m-%d %H:%M:%S"))
    record = logging.LogRecord(name, getattr(logging, level), __file__, 0,
                               message, (), None)
    record.created = created
    return formatter.format(record)


class TestTheWireFormatIsShared(unittest.TestCase):
    """The two ends of the log wire must agree, and now a test says so.

    CONVENTIONS §5 requires metis_logging._DELIM and emanon._DELIM to match
    "exactly" and anchors both to the same logs/ directory — but only in prose.
    A rule with no mechanism is a suggestion; these are the mechanism.
    """

    def test_the_delimiter_matches_metis_logging(self):
        self.assertEqual(
            emanon._DELIM, metis_logging._DELIM,
            "emanon._DELIM and metis_logging._DELIM have drifted — every log "
            "line would fail to parse and the watcher would go blind while "
            "reporting 'nominal' (CONVENTIONS §5)")

    def test_both_modules_anchor_to_the_same_logs_directory(self):
        # emanon lives one level deeper (tools/), so it reaches logs/ with an
        # extra .parent. If either file moves, these resolve apart silently.
        self.assertEqual(emanon.LOG_DIR, metis_logging.LOG_DIR)

    def test_a_line_from_metis_loggings_formatter_parses_back(self):
        raw = _format_line("INFO", "METIS.aether", "fetch ok (0.4s)")
        parsed = emanon._parse_line(raw)
        self.assertIsNotNone(parsed)
        assert parsed is not None  # for the type checker
        self.assertEqual(parsed["ts"], "2026-05-28 14:32:01")
        self.assertEqual(parsed["level"], "INFO")
        self.assertEqual(parsed["logger"], "METIS.aether")
        self.assertEqual(parsed["message"], "fetch ok (0.4s)")

    def test_a_padded_level_is_stripped(self):
        # metis_logging pads levelname to 7 chars ("INFO   "); _parse_line
        # strips each field, so the verdict's set membership still matches.
        raw = _format_line("INFO", "METIS.x", "hi")
        self.assertEqual(emanon._parse_line(raw)["level"], "INFO")

    def test_an_appended_field_is_absorbed_into_the_message(self):
        # The metis_logging docstring's promise: a trace-id slot can be added
        # later without breaking the parser — it splits maxsplit=3, so any 5th
        # field rides along inside message rather than making the line invalid.
        raw = _format_line("INFO", "METIS.x", "did a thing") + metis_logging._DELIM + "trace-42"
        parsed = emanon._parse_line(raw)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["message"], "did a thing" + metis_logging._DELIM + "trace-42")


class TestParsing(unittest.TestCase):

    def test_lines_that_are_not_events_return_none(self):
        for junk in ["", "   ", "a plain sentence with no delimiter",
                     "Traceback (most recent call last):",
                     '  File "x.py", line 3, in <module>',
                     "2026-05-28 | INFO | only three fields"]:  # 3 parts, not 4
            with self.subTest(junk=junk):
                self.assertIsNone(emanon._parse_line(junk))

    def test_under_delimited_lines_return_none_and_never_raise(self):
        # A None means "not an event line", not "failure" — and nothing about a
        # short or binary line should raise. These all split into < 4 fields.
        for junk in [metis_logging._DELIM, "only | two", "\x00\x01"]:
            with self.subTest(junk=repr(junk)):
                self.assertIsNone(emanon._parse_line(junk))

    def test_a_line_of_only_delimiters_parses_to_blank_fields_not_a_crash(self):
        # Five delimiters split into four (empty) fields: a valid shape with no
        # content. Emanon must not raise, and the blank level simply is not
        # ERROR/WARNING, so it never touches the verdict.
        parsed = emanon._parse_line(metis_logging._DELIM * 5)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["level"], "")
        self.assertEqual(emanon._verdict([parsed]), "nominal")


class TestVerdict(unittest.TestCase):
    """Hygieia's precedence, collapsed to one dot: failed > degraded > nominal."""

    @staticmethod
    def _entries(*levels):
        return [{"level": lvl} for lvl in levels]

    def test_an_error_anywhere_is_failure(self):
        self.assertEqual(emanon._verdict(self._entries("INFO", "WARNING", "ERROR")), "failed")

    def test_critical_is_also_failure(self):
        self.assertEqual(emanon._verdict(self._entries("INFO", "CRITICAL")), "failed")

    def test_a_warning_without_an_error_is_degraded(self):
        self.assertEqual(emanon._verdict(self._entries("INFO", "WARNING", "INFO")), "degraded")

    def test_only_info_is_nominal(self):
        self.assertEqual(emanon._verdict(self._entries("INFO", "INFO")), "nominal")

    def test_an_empty_window_is_nominal(self):
        self.assertEqual(emanon._verdict([]), "nominal")

    def test_failure_outranks_a_later_warning(self):
        # ERROR short-circuits; a trailing WARNING must not downgrade it.
        self.assertEqual(emanon._verdict(self._entries("ERROR", "WARNING")), "failed")


class TestReadTail(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_missing_file_returns_empty_not_an_error(self):
        self.assertEqual(emanon._read_tail(self.dir / "nope.log"), [])

    def test_a_small_file_returns_every_line(self):
        p = self.dir / "small.log"
        p.write_text("line 0\nline 1\nline 2\n", encoding="utf-8")
        self.assertEqual(emanon._read_tail(p, max_bytes=64_000),
                         ["line 0", "line 1", "line 2"])

    def test_a_large_file_returns_only_the_tail_with_no_partial_first_line(self):
        p = self.dir / "big.log"
        p.write_text("".join(f"line {n:04d}\n" for n in range(1000)),
                     encoding="utf-8")
        tail = emanon._read_tail(p, max_bytes=50)
        # Only a suffix was read...
        self.assertLess(len(tail), 1000)
        self.assertEqual(tail[-1], "line 0999")
        # ...and the first line kept is a whole line, not the fragment left by
        # the seek landing mid-line (the readline() discard).
        self.assertRegex(tail[0], r"^line \d{4}$")


class TestFetch(unittest.TestCase):
    """The Kairos entry point, against a throwaway logs/ directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self._patch = mock.patch.object(emanon, "LOG_DIR", self.dir)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def _write(self, name, *lines):
        (self.dir / name).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_the_snapshot_has_the_documented_shape(self):
        self._write("a.log", _format_line("INFO", "METIS.aether", "ok"))
        snap = emanon.fetch()
        self.assertEqual(set(snap),
                         {"status", "error_count", "warning_count", "entries", "files"})
        self.assertEqual(snap["status"], "nominal")
        self.assertEqual(snap["files"], ["a.log"])
        self.assertEqual(len(snap["entries"]), 1)

    def test_a_missing_logs_directory_is_nominal_with_a_note(self):
        self._patch.stop()  # point at a dir that does not exist
        with mock.patch.object(emanon, "LOG_DIR", self.dir / "gone"):
            snap = emanon.fetch()
        self._patch.start()  # so tearDown's stop() is balanced
        self.assertEqual(snap["status"], "nominal")
        self.assertEqual(snap["entries"], [])
        self.assertIn("note", snap)

    def test_counts_span_the_full_window_while_the_verdict_uses_the_visible_tail(self):
        # Two ERRORs older than the last _TAIL_LINES INFO lines. They fall out
        # of the trimmed `entries` (so the dot is nominal), but error_count is
        # computed before trimming, so it still sees them. This is the module's
        # deliberate design — the footer count is broader than the dot.
        # (Digit-free distinct messages so nothing folds — the fold key wildcards
        # digits, so "boom 1"/"ok 1" would collapse; the de-dup path has its own tests.)
        old_errors = [_format_line("ERROR", "METIS.midas", f"boom {_uniq(n)}",
                                   when=f"2026-05-28 14:00:{n:02d}") for n in range(2)]
        fresh_info = [_format_line("INFO", "METIS.aether", f"ok {_uniq(n)}",
                                   when=f"2026-05-28 14:30:{n:02d}")
                      for n in range(emanon._TAIL_LINES)]
        self._write("a.log", *(old_errors + fresh_info))
        snap = emanon.fetch()
        self.assertEqual(snap["error_count"], 2)
        self.assertEqual(len(snap["entries"]), emanon._TAIL_LINES)
        self.assertEqual(snap["status"], "nominal")  # the errors scrolled out of view

    def test_a_repeated_error_folds_to_one_row_but_still_counts_each(self):
        # Three identical consecutive errors: the panel sees one row with ×3,
        # but the severity count is the true occurrence total, not the fold.
        self._write("a.log", *[_format_line("ERROR", "METIS.midas", "db down",
                              when=f"2026-05-28 14:32:0{n}") for n in range(3)])
        snap = emanon.fetch()
        self.assertEqual(len(snap["entries"]), 1)
        self.assertEqual(snap["entries"][0]["count"], 3)
        self.assertEqual(snap["error_count"], 3)
        self.assertEqual(snap["status"], "failed")

    def test_every_entry_carries_a_count(self):
        self._write("a.log",
                    _format_line("INFO", "METIS.a", "one", when="2026-05-28 10:00:00"),
                    _format_line("INFO", "METIS.b", "two", when="2026-05-28 10:00:01"))
        self.assertTrue(all("count" in e for e in emanon.fetch()["entries"]))

    def test_a_recent_error_drives_the_verdict_to_failed(self):
        self._write("a.log",
                    _format_line("INFO", "METIS.aether", "ok"),
                    _format_line("ERROR", "METIS.midas", "down", when="2026-05-28 14:32:05"))
        snap = emanon.fetch()
        self.assertEqual(snap["status"], "failed")
        self.assertEqual(snap["error_count"], 1)

    def test_a_warning_counts_and_degrades(self):
        self._write("a.log",
                    _format_line("WARNING", "METIS.aura", "stale", when="2026-05-28 14:32:02"))
        snap = emanon.fetch()
        self.assertEqual(snap["status"], "degraded")
        self.assertEqual(snap["warning_count"], 1)

    def test_entries_are_capped_at_tail_lines(self):
        self._write("a.log", *[_format_line("INFO", "METIS.x", f"event {_uniq(n)}",
                                            when=f"2026-05-28 14:30:{n % 60:02d}")
                               for n in range(80)])
        self.assertEqual(len(emanon.fetch()["entries"]), emanon._TAIL_LINES)

    def test_entries_from_several_files_are_merged_and_sorted_by_timestamp(self):
        self._write("a.log",
                    _format_line("INFO", "METIS.a", "a0", when="2026-05-28 10:00:00"),
                    _format_line("INFO", "METIS.a", "a2", when="2026-05-28 10:00:02"))
        self._write("b.log",
                    _format_line("INFO", "METIS.b", "b1", when="2026-05-28 10:00:01"))
        snap = emanon.fetch()
        self.assertEqual([e["message"] for e in snap["entries"]], ["a0", "b1", "a2"])
        self.assertEqual(snap["files"], ["a.log", "b.log"])

    def test_emanon_never_reports_on_its_own_logger(self):
        # A "I read the logs" line from METIS.emanon would be a feedback loop.
        self._write("a.log",
                    _format_line("INFO", "METIS.emanon", "scanned the logs"),
                    _format_line("INFO", "METIS.aether", "real event"))
        entries = emanon.fetch()["entries"]
        loggers = {e["logger"] for e in entries}
        self.assertNotIn("METIS.emanon", loggers)
        self.assertIn("METIS.aether", loggers)

    def test_non_event_lines_are_skipped_not_counted(self):
        self._write("a.log",
                    _format_line("ERROR", "METIS.midas", "real error", when="2026-05-28 14:32:09"),
                    "Traceback (most recent call last):",
                    '  File "midas.py", line 12, in fetch')
        snap = emanon.fetch()
        self.assertEqual(len(snap["entries"]), 1)   # only the real event line
        self.assertEqual(snap["error_count"], 1)


class TestDeDuplication(unittest.TestCase):
    """_collapse_consecutive folds runs of identical adjacent lines, with a count.

    Tested on the helper directly (crafted entry dicts) — the fetch()-level
    invariant that error_count still counts every occurrence lives in TestFetch.
    """

    @staticmethod
    def _e(level, logger, message, ts):
        return {"ts": ts, "level": level, "logger": logger, "message": message,
                "raw": f"{ts} | {level} | {logger} | {message}"}

    def test_a_run_of_identical_lines_folds_with_a_count(self):
        entries = [self._e("ERROR", "METIS.midas", "boom", f"2026-05-28 14:00:0{n}")
                   for n in range(3)]
        out = emanon._collapse_consecutive(entries)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["count"], 3)

    def test_the_folded_row_keeps_the_newest_timestamp(self):
        entries = [self._e("ERROR", "METIS.midas", "boom", "2026-05-28 14:00:00"),
                   self._e("ERROR", "METIS.midas", "boom", "2026-05-28 14:00:09")]
        self.assertEqual(emanon._collapse_consecutive(entries)[0]["ts"],
                         "2026-05-28 14:00:09")

    def test_non_consecutive_repeats_do_not_fold(self):
        # A, B, A stays three rows — only adjacent duplicates merge, so the
        # chronology survives.
        entries = [self._e("INFO", "METIS.a", "x", "2026-05-28 14:00:00"),
                   self._e("INFO", "METIS.b", "y", "2026-05-28 14:00:01"),
                   self._e("INFO", "METIS.a", "x", "2026-05-28 14:00:02")]
        out = emanon._collapse_consecutive(entries)
        self.assertEqual([e["message"] for e in out], ["x", "y", "x"])
        self.assertTrue(all(e["count"] == 1 for e in out))

    def test_a_single_differing_field_breaks_the_run(self):
        # Same message + logger, different level → two rows, not one.
        entries = [self._e("WARNING", "METIS.a", "x", "2026-05-28 14:00:00"),
                   self._e("ERROR",   "METIS.a", "x", "2026-05-28 14:00:01")]
        self.assertEqual(len(emanon._collapse_consecutive(entries)), 2)

    def test_a_singleton_carries_count_one(self):
        out = emanon._collapse_consecutive(
            [self._e("INFO", "METIS.a", "solo", "2026-05-28 14:00:00")])
        self.assertEqual(out[0]["count"], 1)

    def test_it_does_not_mutate_the_input_entries(self):
        # The inputs are the dicts _parse_line handed back; the fold works on
        # copies so a later pass never sees a stray "count".
        first = self._e("INFO", "METIS.a", "x", "2026-05-28 14:00:00")
        emanon._collapse_consecutive([first, dict(first)])
        self.assertNotIn("count", first)

    def test_a_changing_number_in_the_message_still_folds(self):
        # The midas case that motivated this: the same error every tick, but the
        # duration in the text changes ("after 0.01s / 0.00s / 0.02s"). Digits
        # are wildcarded in the fold key, so all of it collapses to one row.
        entries = [self._e("ERROR", "kairos",
                          f"midas fetch failed after 0.0{n}s: all tickers failed",
                          f"2026-05-28 14:00:0{n}") for n in range(4)]
        out = emanon._collapse_consecutive(entries)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["count"], 4)

    def test_the_folded_row_shows_the_latest_real_message_not_the_key(self):
        # The display keeps the real (last) value; only the match key is blanked.
        entries = [self._e("ERROR", "kairos", "failed after 0.01s", "2026-05-28 14:00:00"),
                   self._e("ERROR", "kairos", "failed after 0.99s", "2026-05-28 14:00:05")]
        self.assertEqual(emanon._collapse_consecutive(entries)[0]["message"],
                         "failed after 0.99s")

    def test_two_templates_that_both_carry_numbers_stay_separate(self):
        # The cerberus case: only digits are wildcarded, not the words, so
        # "Expecting value…" and "Extra data…" must not merge into each other.
        entries = [self._e("ERROR", "cerberus",
                          "Expecting value: line 1 column 1 (char 0)", "2026-05-28 14:00:00"),
                   self._e("ERROR", "cerberus",
                          "Extra data: line 10 column 4 (char 167)", "2026-05-28 14:00:01")]
        self.assertEqual(len(emanon._collapse_consecutive(entries)), 2)


class TestNeverRaises(unittest.TestCase):
    """§12: emanon.fetch() never raises — a watcher that crashes is useless.

    The docstring says so and the whole body is wrapped; these prove the net
    actually holds, and that when an internal blows up the degraded note names
    the exception CLASS only — never its message, which could carry a path.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self._patch = mock.patch.object(emanon, "LOG_DIR", self.dir)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_garbage_files_never_raise(self):
        (self.dir / "binary.log").write_bytes(b"\x00\x81\xfe not utf-8 \xff\xfe")
        (self.dir / "empty.log").write_text("", encoding="utf-8")
        (self.dir / "noise.log").write_text("no delimiters here\nor here\n", encoding="utf-8")
        snap = emanon.fetch()
        self.assertIsInstance(snap, dict)
        self.assertIn("status", snap)

    def test_an_exploding_internal_degrades_instead_of_raising(self):
        (self.dir / "a.log").write_text(
            _format_line("INFO", "METIS.x", "ok"), encoding="utf-8")
        with mock.patch.object(emanon, "_verdict", side_effect=RuntimeError("boom")):
            snap = emanon.fetch()  # must NOT raise
        self.assertEqual(snap["status"], "degraded")
        self.assertEqual(snap["note"], "watcher error: RuntimeError")

    def test_the_degraded_note_never_leaks_the_exception_message(self):
        # §2's spirit even though Emanon is not a brain tool: str(exc) can carry
        # a filesystem path into the panel. Only the class name is allowed out.
        (self.dir / "a.log").write_text(
            _format_line("INFO", "METIS.x", "ok"), encoding="utf-8")
        secret = "C:/Users/secret/leak/path"
        with mock.patch.object(emanon, "_verdict", side_effect=RuntimeError(secret)):
            note = emanon.fetch()["note"]
        self.assertNotIn(secret, note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
