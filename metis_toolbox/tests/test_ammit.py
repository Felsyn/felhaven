"""
test_ammit.py — unit tests for ammit.py (the single countdown timer).

ammit persists to a JSON state file at the app root; every test swaps
ammit.STATE_FILE for a throwaway path (the sphynx._DATA_PATH test-seam pattern),
so nothing touches the real timer_state.json and the tests are hermetic and
order-independent.

    python -X utf8 -m unittest tests.test_ammit
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import ammit


class _TempState(unittest.TestCase):
    """Point ammit.STATE_FILE at a fresh temp file per test, then restore it."""

    def setUp(self):
        self._orig_state = ammit.STATE_FILE
        self._tmp = tempfile.TemporaryDirectory()
        ammit.STATE_FILE = Path(self._tmp.name) / "timer_state.json"

    def tearDown(self):
        ammit.STATE_FILE = self._orig_state
        self._tmp.cleanup()


class TestFormat(_TempState):
    """_fmt renders HH:MM:SS. Assert the definitional cases, not a value
    re-derived from the same arithmetic under test (the session-3 lesson)."""

    def test_zero(self):
        self.assertEqual(ammit._fmt(0), "00:00:00")

    def test_seconds_only(self):
        self.assertEqual(ammit._fmt(59), "00:00:59")

    def test_one_hour_exactly(self):
        self.assertEqual(ammit._fmt(3600), "01:00:00")

    def test_hours_minutes_seconds(self):
        self.assertEqual(ammit._fmt(3661), "01:01:01")


class TestStartStopReset(_TempState):
    def test_start_then_query_is_running(self):
        ammit.start_timer(0, 300)
        t = ammit.query_all()[0]
        self.assertTrue(t["running"])
        self.assertGreater(t["remaining_seconds"], 0)
        self.assertLessEqual(t["remaining_seconds"], 300)
        self.assertFalse(t["expired"])

    def test_stop_freezes_remaining(self):
        ammit.start_timer(0, 300)
        ammit.stop_timer(0)
        t = ammit.query_all()[0]
        self.assertFalse(t["running"])
        # Pause preserves the remaining time (frozen), it does not reset it.
        self.assertGreater(t["remaining_seconds"], 0)
        self.assertLessEqual(t["remaining_seconds"], 300)

    def test_reset_clears(self):
        ammit.start_timer(0, 300)
        ammit.reset_timer(0)
        t = ammit.query_all()[0]
        self.assertFalse(t["running"])
        self.assertEqual(t["remaining_seconds"], 0)
        self.assertEqual(t["display"], "00:00:00")


class TestRemainingFreezesAtZero(_TempState):
    def test_expired_timer_reads_zero_and_flags_expired(self):
        # A 60 s timer started two minutes ago has long since run out.
        past = (datetime.now().astimezone() - timedelta(minutes=2)).isoformat()
        ammit._save({"timers": [
            {"duration_seconds": 60, "started_at": past, "running": True}
        ]})
        t = ammit.query_all()[0]
        self.assertEqual(t["remaining_seconds"], 0)   # max(0, ...) floor
        self.assertTrue(t["expired"])
        self.assertEqual(t["display"], "00:00:00")

    def test_a_state_file_predating_acknowledged_defaults_it_false(self):
        # Older timer_state.json files never wrote this key — query_all()'s
        # .get() must not raise, and must read as "not yet acknowledged".
        past = (datetime.now().astimezone() - timedelta(minutes=2)).isoformat()
        ammit._save({"timers": [
            {"duration_seconds": 60, "started_at": past, "running": True}
        ]})
        self.assertFalse(ammit.query_all()[0]["acknowledged"])


class TestAcknowledge(_TempState):
    def test_fresh_timer_reads_not_acknowledged(self):
        ammit.start_timer(0, 300)
        self.assertFalse(ammit.query_all()[0]["acknowledged"])

    def test_acknowledge_sets_the_flag(self):
        ammit.start_timer(0, 300)
        ammit.acknowledge_timer(0)
        self.assertTrue(ammit.query_all()[0]["acknowledged"])

    def test_acknowledge_does_not_touch_expired_or_running(self):
        # Acknowledging silences the passive badge only — it must not be a
        # backdoor way to clear the timer itself.
        past = (datetime.now().astimezone() - timedelta(minutes=2)).isoformat()
        ammit._save({"timers": [
            {"duration_seconds": 60, "started_at": past, "running": True,
             "acknowledged": False}
        ]})
        ammit.acknowledge_timer(0)
        t = ammit.query_all()[0]
        self.assertTrue(t["running"])
        self.assertTrue(t["expired"])
        self.assertTrue(t["acknowledged"])

    def test_restarting_the_timer_re_arms_acknowledged(self):
        ammit.start_timer(0, 60)
        ammit.acknowledge_timer(0)
        self.assertTrue(ammit.query_all()[0]["acknowledged"])
        ammit.start_timer(0, 300)   # a fresh countdown re-arms the badge
        self.assertFalse(ammit.query_all()[0]["acknowledged"])

    def test_reset_clears_acknowledged(self):
        ammit.start_timer(0, 60)
        ammit.acknowledge_timer(0)
        ammit.reset_timer(0)
        self.assertFalse(ammit.query_all()[0]["acknowledged"])


class TestHandle(_TempState):
    def test_start_returns_the_timer_it_started(self):
        # The state it PRODUCED, so the model can say "set for 5 minutes" from
        # data rather than from its own assumption (was {"ok": True}).
        out = ammit.handle(action="start", duration_minutes=5)
        self.assertEqual(out["action"], "start")
        (t,) = out["timers"]
        self.assertTrue(t["running"])
        self.assertGreater(t["remaining_seconds"], 290)
        self.assertLessEqual(t["remaining_seconds"], 300)

    def test_stop_and_reset_return_the_resulting_state(self):
        ammit.handle(action="start", duration_minutes=5)
        stopped = ammit.handle(action="stop")
        self.assertFalse(stopped["timers"][0]["running"])
        self.assertGreater(stopped["timers"][0]["remaining_seconds"], 0)
        reset = ammit.handle(action="reset")
        self.assertEqual(reset["timers"][0]["remaining_seconds"], 0)

    def test_query_reads_every_slot(self):
        out = ammit.handle(action="query")
        self.assertIn("timers", out)
        self.assertEqual(len(out["timers"]), ammit.MAX_SLOTS)

    def test_start_without_a_duration_does_not_fire_the_alarm(self):
        # The bug: duration defaulted to 0, so "start a timer" saved a running
        # 0-second countdown — expired=True on the next read, which is exactly
        # the edge AmmitAlarm fires its popup and spoken announcement on.
        # 0.001 min rounds to 0 s — the same instant alarm by another route.
        for bad in (None, 0, -5, 0.001):
            with self.subTest(duration_minutes=bad):
                out = ammit.handle(action="start", duration_minutes=bad)
                self.assertTrue(out["error"].startswith("bad_arguments: "), out)
                t = ammit.query_all()[0]
                self.assertFalse(t["running"])
                self.assertFalse(t["expired"])

    def test_a_duration_that_is_not_a_number_is_a_slug_not_a_raise(self):
        # "ten" * 60 used to build "tenten…" and int() raised ValueError (§2:
        # handle() never raises). The gate catches this for the model; a
        # direct caller must be safe too.
        for bad in ("ten", float("nan"), float("inf"), [5]):
            with self.subTest(duration_minutes=bad):
                out = ammit.handle(action="start", duration_minutes=bad)
                self.assertTrue(out["error"].startswith("bad_arguments: "), out)
                self.assertFalse(ammit.query_all()[0]["running"])

    def test_a_numeric_string_duration_still_starts(self):
        out = ammit.handle(action="start", duration_minutes="2")
        self.assertTrue(out["timers"][0]["running"])

    def test_an_unknown_action_is_refused_and_changes_nothing(self):
        # Used to run `query` silently.
        ammit.handle(action="start", duration_minutes=5)
        out = ammit.handle(action="explode")
        self.assertTrue(out["error"].startswith("unknown_action: "), out)
        self.assertTrue(ammit.query_all()[0]["running"])

    def test_the_handler_takes_only_what_the_schema_declares(self):
        # `slot` used to be a hidden parameter the model was never shown.
        import inspect
        declared = set(ammit.TOOL_DEFINITION["function"]["parameters"]["properties"])
        self.assertEqual(set(inspect.signature(ammit.handle).parameters), declared)

    def test_handle_never_raises_on_corrupt_state(self):
        # Attack the real _load seam: a garbled file must degrade to defaults,
        # not raise into Pythia's dispatcher (CONVENTIONS S2 — handle() never
        # raises).
        ammit.STATE_FILE.write_text("{ not json", encoding="utf-8")
        out = ammit.handle(action="query")
        self.assertIn("timers", out)
        self.assertFalse(out["timers"][0]["running"])


class TestSaveIsAtomic(_TempState):
    """The state is written beside the file and os.replace()d into place, so a
    crash mid-write leaves the old timer whole (CONVENTIONS §9)."""

    def test_a_crash_mid_write_leaves_the_old_timer_whole(self):
        ammit.start_timer(0, 300)
        before = ammit.STATE_FILE.read_text(encoding="utf-8")
        real_write = Path.write_text

        def torn(path, text, *args, **kwargs):
            real_write(path, text[:10], *args, **kwargs)   # half a file, then the crash
            raise OSError("disk pulled mid-write")

        with mock.patch.object(Path, "write_text", torn):
            ammit.stop_timer(0)
        self.assertEqual(ammit.STATE_FILE.read_text(encoding="utf-8"), before)

    def test_a_save_lands_in_the_state_file_and_leaves_no_temp(self):
        ammit.start_timer(0, 300)
        # The write really moved here (STATE_FILE is read at call time), and
        # the temp file was replaced into place, not left beside it.
        self.assertEqual(os.listdir(self._tmp.name), ["timer_state.json"])
        self.assertTrue(ammit.query_all()[0]["running"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
