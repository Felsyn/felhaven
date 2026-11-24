"""
test_ammit.py — unit tests for ammit.py (the single countdown timer).

ammit persists to a JSON state file at the app root; every test swaps
ammit.STATE_FILE for a throwaway path (the sphynx._DATA_PATH test-seam pattern),
so nothing touches the real timer_state.json and the tests are hermetic and
order-independent.

    python -X utf8 -m unittest tests.test_ammit
"""

import json
import os
import sys
import tempfile
import threading
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
        # The "can't be used" warning is logged once a run; start each test
        # with it unsaid, so no test leans on another's order.
        p = mock.patch.object(ammit, "_unusable_logged", [False], create=True)
        p.start()
        self.addCleanup(p.stop)

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

    def test_a_duration_too_big_for_a_float_is_a_slug_not_a_raise(self):
        # Measured 2026-11-21: 1e308 minutes is a finite float whose * 60 is
        # inf, and round(inf) raised OverflowError; an int of 400 digits
        # (JSON allows it) raised in float(). handle() never raises (§2).
        for bad in (1e308, 10 ** 400, "1e400"):
            with self.subTest(duration_minutes=str(bad)[:12]):
                try:
                    out = ammit.handle(action="start", duration_minutes=bad)
                except OverflowError as e:
                    self.fail(f"handle() raised {e!r}")
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
        with self.assertLogs("METIS.ammit", "WARNING"):
            out = ammit.handle(action="query")
        self.assertIn("timers", out)
        self.assertFalse(out["timers"][0]["running"])

    def test_a_failed_save_is_write_failed_not_the_old_state(self):
        # handle() used to answer with query_all() whatever happened, so a
        # start that wasn't saved came back as {"action": "start", ...} with
        # the old timer in it, for the model to read as done.
        with mock.patch.object(ammit.os, "replace",
                               side_effect=PermissionError(13, "Access is denied")), \
                self.assertLogs("METIS.ammit", "ERROR"):
            out = ammit.handle(action="start", duration_minutes=5)
        self.assertTrue(out.get("error", "").startswith("write_failed: "), out)
        self.assertFalse(ammit.query_all()[0]["running"])


class TestStateFileShape(_TempState):
    """A file that parses but isn't a timer reads as no timer, as a torn one
    does. Each of these raised from query_all() (measured 2026-11-21): into
    Pythia's dispatcher from handle(), and into the countdown, the alarm and
    Home's badge every tick."""

    _NOT_A_TIMER = {
        "an empty object": {},
        "no slots": {"timers": []},
        "a list": [],
        "a slot missing its fields": {"timers": [{"duration_seconds": 5}]},
        "a true/false duration": {"timers": [
            {"duration_seconds": True, "started_at": None, "running": False}]},
        "a start with no UTC offset": {"timers": [
            {"duration_seconds": 60, "started_at": "2026-11-21T10:00:00",
             "running": True}]},
        "a start that isn't a time": {"timers": [
            {"duration_seconds": 60, "started_at": "soon", "running": True}]},
    }

    def _write(self, state):
        ammit.STATE_FILE.write_text(json.dumps(state), encoding="utf-8")

    def test_a_file_that_isnt_a_timer_reads_as_no_timer(self):
        for name, state in self._NOT_A_TIMER.items():
            with self.subTest(name):
                self._write(state)
                with mock.patch.object(ammit, "_unusable_logged", [False], create=True), \
                        self.assertLogs("METIS.ammit", "WARNING"):
                    try:
                        out = ammit.handle(action="query")
                    except (KeyError, IndexError, TypeError, ValueError) as e:
                        self.fail(f"handle() raised {e!r}")
                self.assertEqual(len(out["timers"]), ammit.MAX_SLOTS)
                self.assertFalse(out["timers"][0]["running"])

    def test_it_is_logged_once_until_the_file_is_usable_again(self):
        # The countdown, the alarm and Home's badge read it three times a
        # second; one line says it.
        ammit.STATE_FILE.write_text("{ not json", encoding="utf-8")
        with self.assertLogs("METIS.ammit", "WARNING") as logs:
            for _ in range(5):
                ammit.query_all()
        self.assertEqual(len(logs.records), 1, logs.output)
        ammit.start_timer(0, 300)          # a usable file again...
        ammit.query_all()
        ammit.STATE_FILE.write_text("[", encoding="utf-8")   # ...so the next is said
        with self.assertLogs("METIS.ammit", "WARNING"):
            ammit.query_all()


class TestOneLock(_TempState):
    """Pythia's request thread and the Tk thread both use the file, and on
    Windows a rename fails while another thread has the file open, and an
    open fails while the rename runs. Measured 2026-11-21 unlocked: a tight
    reader made 149 of 300 saves fail, and two writers wiped the timer 53
    times in 300. Every read and every rename now happens under one lock."""

    def _lock_held(self):
        lock = getattr(ammit, "_LOCK", None)
        return isinstance(lock, type(threading.Lock())) and lock.locked()

    def test_every_rename_and_every_read_is_under_the_lock(self):
        seen = []
        real_replace, real_read = os.replace, Path.read_text

        def replace(*args, **kwargs):
            seen.append(("replace", self._lock_held()))
            return real_replace(*args, **kwargs)

        def read_text(path, *args, **kwargs):
            if path == ammit.STATE_FILE:
                seen.append(("read", self._lock_held()))
            return real_read(path, *args, **kwargs)

        with mock.patch.object(ammit.os, "replace", replace), \
                mock.patch.object(Path, "read_text", read_text):
            ammit.start_timer(0, 300)
            ammit.acknowledge_timer(0)
            ammit.stop_timer(0)
            ammit.query_all()
            ammit.reset_timer(0)
        self.assertEqual({kind for kind, _ in seen}, {"replace", "read"})
        self.assertEqual([s for s in seen if not s[1]], [])


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

        with mock.patch.object(Path, "write_text", torn), \
                self.assertLogs("METIS.ammit", "ERROR"):
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
