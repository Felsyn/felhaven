"""
test_kairos.py — unit tests for kairos.py (central scheduler).

Kairos owns the clock, fires workers on threads, and dispatches results to
panels. None of that is safe to exercise with the real thing: real threads,
real time.sleep, or a real tk.Tk() would make this suite slow and flaky.
Instead every seam is faked:

    - FakeRoot stands in for tk.Tk() — Kairos only ever calls
      root.after(ms, fn) / root.after_cancel(id).
    - FakeThread stands in for threading.Thread — start() is a no-op; tests
      invoke the recorded target directly to run a worker synchronously.
    - FakeClock replaces the *name* `kairos.time` (not the global time
      module) so tests can jump the clock without sleeping.

Run from metis_toolbox/:

    python -X utf8 -m unittest discover -s tests -p "test_*.py"
    python -X utf8 -m unittest tests.test_kairos        (this file alone)

Behavioral tests (Groups B/C/D) patch Kairos.WORKERS before instantiation so
construction resolves test stub fetch functions instead of importing the
eleven real tools modules. Registry-integrity tests (Group A) are the one
deliberate exception: they construct against the real WORKERS table, because
catching a renamed fetch or typo'd dotted path at that boundary is the point.
"""

import logging
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kairos
from kairos import Kairos


# ── Test helpers ───────────────────────────────────────────────────────────

class FakeRoot:
    """Records after() calls; never draws. Kairos only needs after/after_cancel."""

    def __init__(self):
        self.scheduled = []      # list of (id, ms, callback, args)
        self.cancelled = []      # list of ids passed to after_cancel
        self._next_id = 0

    def after(self, ms, fn=None, *args):
        self._next_id += 1
        self.scheduled.append((self._next_id, ms, fn, args))
        return self._next_id

    def after_cancel(self, after_id):
        self.cancelled.append(after_id)


class FakeThread:
    """Drop-in for threading.Thread: records, never runs, alive is settable."""

    instances = []  # class-level log, cleared in setUp

    def __init__(self, target=None, args=(), daemon=None):
        self.target, self.args, self.daemon = target, args, daemon
        self.alive = False
        FakeThread.instances.append(self)

    def start(self):
        pass  # deliberate no-op — tests run targets directly

    def is_alive(self):
        return self.alive


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def monotonic(self):
        return self.now


class RecorderPanel:
    """Dumb display surface for tests: remembers every payload."""

    def __init__(self):
        self.updates = []

    def update(self, data):
        self.updates.append(data)


def fetch_ok():
    return {"ok": True}


def fetch_boom():
    raise RuntimeError("synthetic failure")


# The fakes' dotted path is however this file was imported: "test_kairos"
# under discover -s tests, "tests.test_kairos" under -m unittest. A literal
# "test_kairos." resolved only under discover, so the other run broke on
# imports, not on Kairos.
_FAKES = __name__

TEST_WORKERS = [
    ("alpha", 60, f"{_FAKES}.fetch_ok"),
    ("beta", 5, f"{_FAKES}.fetch_ok"),
]


# ── Group A — Registry integrity (real WORKERS table) ──────────────────────

class TestRegistryIntegrity(unittest.TestCase):

    def test_real_workers_table_resolves(self):
        k = Kairos(FakeRoot())
        self.assertEqual(len(k._fetch_fns), len(Kairos.WORKERS))
        for name, _interval, _dotted in Kairos.WORKERS:
            self.assertIn(name, k._fetch_fns)
            self.assertTrue(callable(k._fetch_fns[name]))

    def test_real_worker_names_unique(self):
        names = [name for name, _interval, _dotted in Kairos.WORKERS]
        self.assertEqual(len(names), len(set(names)))


# ── Shared base for behavioral tests (patched WORKERS) ──────────────────────

class KairosBehaviorTestCase(unittest.TestCase):
    """Common setup for Groups B, C, D: patched WORKERS, fake root."""

    def setUp(self):
        FakeThread.instances = []
        patcher = mock.patch.object(Kairos, "WORKERS", TEST_WORKERS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = FakeRoot()
        self.kairos = Kairos(self.root)


# ── Group B — Dispatch (_drain_queue) ───────────────────────────────────────

class TestDrainQueue(KairosBehaviorTestCase):

    def test_drain_dispatches_to_registered_panel(self):
        panel = RecorderPanel()
        self.kairos.register_panel("alpha", panel)
        payload = {"ok": True}
        self.kairos.result_queue.put(("alpha", payload))
        self.kairos._drain_queue()
        self.assertEqual(panel.updates, [payload])

    def test_drain_dispatches_to_all_panels_on_name(self):
        panel_a = RecorderPanel()
        panel_b = RecorderPanel()
        self.kairos.register_panel("alpha", panel_a)
        self.kairos.register_panel("alpha", panel_b)
        payload = {"ok": True}
        self.kairos.result_queue.put(("alpha", payload))
        self.kairos._drain_queue()
        self.assertEqual(panel_a.updates, [payload])
        self.assertEqual(panel_b.updates, [payload])

    def test_drain_ignores_unregistered_name(self):
        self.kairos.result_queue.put(("nobody", {"x": 1}))
        self.kairos._drain_queue()  # must not raise
        self.assertTrue(self.kairos.result_queue.empty())

    def test_drain_empties_queue_in_one_pass(self):
        panel = RecorderPanel()
        self.kairos.register_panel("alpha", panel)
        self.kairos.result_queue.put(("alpha", 1))
        self.kairos.result_queue.put(("alpha", 2))
        self.kairos.result_queue.put(("alpha", 3))
        self.kairos._drain_queue()
        self.assertEqual(panel.updates, [1, 2, 3])
        self.assertTrue(self.kairos.result_queue.empty())


# ── Group C — Scheduling (_schedule_workers / start) ────────────────────────

class TestScheduling(KairosBehaviorTestCase):

    def setUp(self):
        super().setUp()
        self.clock = FakeClock(1000.0)
        patcher = mock.patch.object(kairos, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        thread_patcher = mock.patch("kairos.threading.Thread", FakeThread)
        thread_patcher.start()
        self.addCleanup(thread_patcher.stop)

    def test_start_backdates_all_workers(self):
        self.kairos.start()
        self.assertEqual(len(FakeThread.instances), len(TEST_WORKERS))
        spawned_names = {inst.args[0] for inst in FakeThread.instances}
        self.assertEqual(spawned_names, {name for name, _i, _d in TEST_WORKERS})

    def test_interval_holds_before_elapsed(self):
        self.kairos.start()
        FakeThread.instances = []
        self.clock.now += 59  # beta interval is 5s so it's already due again; check alpha (60s)
        self.kairos._schedule_workers()
        alpha_spawns = [inst for inst in FakeThread.instances if inst.args[0] == "alpha"]
        self.assertEqual(alpha_spawns, [])

    def test_interval_fires_at_elapsed(self):
        self.kairos.start()
        FakeThread.instances = []
        self.clock.now += 60
        self.kairos._schedule_workers()
        alpha_spawns = [inst for inst in FakeThread.instances if inst.args[0] == "alpha"]
        self.assertEqual(len(alpha_spawns), 1)

    def test_pileup_guard_skips_while_alive(self):
        self.kairos.start()
        alpha_thread = self.kairos._running_threads["alpha"]
        alpha_thread.alive = True
        FakeThread.instances = []
        self.clock.now += 60
        self.kairos._schedule_workers()
        alpha_spawns = [inst for inst in FakeThread.instances if inst.args[0] == "alpha"]
        self.assertEqual(alpha_spawns, [])

    def test_pileup_guard_refires_promptly_after_death(self):
        self.kairos.start()
        alpha_thread = self.kairos._running_threads["alpha"]
        alpha_thread.alive = True
        self.clock.now += 60
        self.kairos._schedule_workers()  # skipped while alive; _last_run untouched
        alpha_thread.alive = False
        FakeThread.instances = []
        self.kairos._schedule_workers()  # interval already elapsed -> fires immediately
        alpha_spawns = [inst for inst in FakeThread.instances if inst.args[0] == "alpha"]
        self.assertEqual(len(alpha_spawns), 1)


# ── Group D — Worker execution + lifecycle ──────────────────────────────────

class TestWorkerExecutionAndLifecycle(KairosBehaviorTestCase):

    def test_run_worker_success_enqueues_result(self):
        self.kairos._run_worker("alpha", fetch_ok)
        name, data = self.kairos.result_queue.get_nowait()
        self.assertEqual(name, "alpha")
        self.assertEqual(data, {"ok": True})

    def test_run_worker_failure_enqueues_none_and_logs(self):
        with self.assertLogs("METIS.kairos", "ERROR") as cm:
            self.kairos._run_worker("alpha", fetch_boom)
        name, data = self.kairos.result_queue.get_nowait()
        self.assertEqual(name, "alpha")
        self.assertIsNone(data)
        self.assertTrue(any("RuntimeError" in line for line in cm.output))

    def test_stop_before_start_is_safe(self):
        self.kairos.stop()  # must not raise

    def test_stop_cancels_pending_after(self):
        with mock.patch.object(kairos, "time", FakeClock(1000.0)), \
             mock.patch("kairos.threading.Thread", FakeThread):
            self.kairos._tick()
        after_id = self.kairos._after_id
        self.kairos.stop()
        self.assertIn(after_id, self.root.cancelled)

    def test_end_to_end_logic_loop(self):
        clock = FakeClock(1000.0)
        with mock.patch.object(kairos, "time", clock), \
             mock.patch("kairos.threading.Thread", FakeThread):
            panel = RecorderPanel()
            self.kairos.register_panel("alpha", panel)
            self.kairos.start()
            alpha_thread = next(
                inst for inst in FakeThread.instances if inst.args[0] == "alpha"
            )
            alpha_thread.target(*alpha_thread.args)
            self.kairos._drain_queue()
        self.assertEqual(panel.updates, [{"ok": True}])


# ── Group E — refetch (Settings-tab nudge) ─────────────────────────────────

class TestRefetch(KairosBehaviorTestCase):

    def test_refetch_forces_immediate_refire(self):
        clock = FakeClock(1000.0)
        with mock.patch.object(kairos, "time", clock), \
             mock.patch("kairos.threading.Thread", FakeThread):
            self.kairos.start()                      # initial fire of alpha+beta
            base_alpha = len([i for i in FakeThread.instances if i.args[0] == "alpha"])
            self.kairos.refetch("alpha")             # nudge alpha only
            self.kairos._schedule_workers()          # same instant — no time passed
        alpha = [i for i in FakeThread.instances if i.args[0] == "alpha"]
        beta  = [i for i in FakeThread.instances if i.args[0] == "beta"]
        self.assertEqual(base_alpha, 1)              # fired once on start
        self.assertEqual(len(alpha), 2)              # re-fired despite 60 s unmet
        self.assertEqual(len(beta), 1)              # beta not nudged -> no re-fire

    def test_refetch_unknown_name_is_ignored(self):
        self.kairos.start()
        self.kairos.refetch("nonexistent")           # must not raise
        # No phantom worker gets scheduled for an unknown name.
        self.assertNotIn("nonexistent", self.kairos._last_run)


# ── Group F — Retry ladder (_note_outcome) ─────────────────────────────────

class TestRetryLadder(KairosBehaviorTestCase):
    """A failed fetch pulls the next attempt forward instead of waiting out the
    worker's whole interval — the bug that pinned Aura at "unavailable" for 30
    minutes after a blip that cleared in seconds.

    TEST_WORKERS' "alpha" has a 60 s interval and "beta" a 5 s one, which is
    exactly the pair worth testing: the ladder's first rung (60 s) is shorter
    than neither, so alpha is left alone as "already polls this often" and beta
    even more so. These tests therefore patch in their own long-interval worker
    where the ladder actually has something to give.
    """

    def _long_kairos(self, interval=1800):
        """A Kairos whose single worker polls slowly enough for the ladder to bite."""
        workers = [("slow", interval, f"{_FAKES}.fetch_ok")]
        with mock.patch.object(Kairos, "WORKERS", workers):
            return Kairos(FakeRoot())

    def test_failure_pulls_next_attempt_to_first_rung(self):
        k = self._long_kairos()
        clock = FakeClock(1000.0)
        with mock.patch.object(kairos, "time", clock):
            k._last_run["slow"] = clock.now          # just fired
            k.result_queue.put(("slow", None))       # ...and failed
            k._drain_queue()
        # Next fire is due 60 s from the drain, not 1800 s.
        self.assertAlmostEqual(k._last_run["slow"], 1000.0 - 1800 + 60)
        self.assertEqual(k._fail_counts["slow"], 1)

    def test_ladder_escalates_then_runs_out(self):
        k = self._long_kairos()
        clock = FakeClock(1000.0)
        with mock.patch.object(kairos, "time", clock):
            for expected in Kairos.RETRY_DELAYS:     # 60, 300, 900
                k._last_run["slow"] = clock.now
                k.result_queue.put(("slow", None))
                k._drain_queue()
                self.assertAlmostEqual(k._last_run["slow"], 1000.0 - 1800 + expected)
            # Ladder spent: a further failure stops overriding the schedule, so
            # a sustained outage settles back to the normal cadence.
            k._last_run["slow"] = clock.now
            k.result_queue.put(("slow", None))
            k._drain_queue()
            self.assertAlmostEqual(k._last_run["slow"], 1000.0)

    def test_success_clears_the_ladder(self):
        k = self._long_kairos()
        clock = FakeClock(1000.0)
        with mock.patch.object(kairos, "time", clock):
            k.result_queue.put(("slow", None))
            k._drain_queue()
            self.assertEqual(k._fail_counts["slow"], 1)
            k.result_queue.put(("slow", {"ok": True}))
            k._drain_queue()
        self.assertNotIn("slow", k._fail_counts)

    def test_short_interval_worker_is_left_alone(self):
        """beta polls every 5 s; the 60 s first rung would SLOW it down."""
        self.kairos._last_run["beta"] = 1000.0
        with mock.patch.object(kairos, "time", FakeClock(1000.0)):
            self.kairos.result_queue.put(("beta", None))
            self.kairos._drain_queue()
        self.assertEqual(self.kairos._last_run["beta"], 1000.0)   # untouched

    def test_unregistered_name_does_not_schedule(self):
        self.kairos.result_queue.put(("nobody", None))
        self.kairos._drain_queue()                   # must not raise
        self.assertNotIn("nobody", self.kairos._last_run)
        self.assertNotIn("nobody", self.kairos._fail_counts)

    def test_refetch_clears_ladder_progress(self):
        k = self._long_kairos()
        with mock.patch.object(kairos, "time", FakeClock(1000.0)):
            k.result_queue.put(("slow", None))
            k._drain_queue()
        self.assertEqual(k._fail_counts["slow"], 1)
        k.refetch("slow")
        self.assertNotIn("slow", k._fail_counts)

    def test_every_real_worker_has_an_interval(self):
        """The ladder reads _intervals; a worker missing from it would silently
        never retry. Holds the two dicts in lockstep against the real table."""
        k = Kairos(FakeRoot())
        self.assertEqual(set(k._intervals), set(k._fetch_fns))


if __name__ == "__main__":
    unittest.main()
