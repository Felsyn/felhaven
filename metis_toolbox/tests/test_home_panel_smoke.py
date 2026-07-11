"""
test_home_panel_smoke.py — headless Tk smoke test for HomePanel (Hestia
controls, per-response metrics, stop/refresh, epoch-gated stale messages).

Same approach as test_themis_panel_smoke.py: a withdrawn tk.Tk() root driven
with update_idletasks() (no window flashes, no mainloop, no real network/LLM
calls — pythia.ask() is never invoked here). Queue events are pushed directly
and drained with panel._poll(), bypassing the worker thread entirely.

    python -X utf8 -m unittest tests.test_home_panel_smoke
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
from panels.home_panel import HomePanel


def _stats(**overrides) -> dict:
    base = {
        "type": "stats", "prompt_tokens": 10, "output_tokens": 5,
        "wall_ms": 2100, "eval_ms": 1000, "tools_called": 1,
        "tools_failed": 0, "failed_tools": [], "cancelled": False,
    }
    base.update(overrides)
    return base


class TestHomePanelSmoke(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _transcript_text(self, panel: HomePanel) -> str:
        return panel._log.get("1.0", "end")

    def test_construct(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()
        self.assertIn("listening", self._transcript_text(panel))

    def test_stats_event_updates_meta_line_and_session_readouts(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()

        epoch = panel._epoch
        panel._q.put(("delta", "It is Tuesday.", epoch))
        panel._q.put(("stats", _stats(), epoch))
        panel._q.put(("done", "It is Tuesday.", epoch))
        panel._poll()
        self.root.update_idletasks()

        text = self._transcript_text(panel)
        self.assertIn("15 tok", text)          # 10 prompt + 5 output
        self.assertIn("tok/s", text)
        self.assertIn("1 tools", text)
        self.assertEqual(panel._hestia._flux.cget("text"), "Scraptoken Flux: 15")
        self.assertEqual(panel._hestia._rites_main.cget("text"), "Rites: 1")
        self.assertEqual(panel._history[-1], {"role": "assistant", "content": "It is Tuesday."})

    def test_failed_tool_shows_in_meta_line(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()

        epoch = panel._epoch
        panel._q.put(("delta", "oops", epoch))
        panel._q.put(("stats", _stats(tools_failed=1), epoch))
        panel._q.put(("done", "oops", epoch))
        panel._poll()
        self.root.update_idletasks()

        self.assertIn("1 failed", self._transcript_text(panel))
        self.assertEqual(panel._hestia._rites_fail.cget("text"), " · 1 failed")

    def test_stop_marks_turn_cancelled_and_does_not_persist(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()

        epoch = panel._epoch
        panel._on_stop()   # sets the cancel Event; no real thread to react to it
        panel._q.put(("delta", "Par", epoch))
        panel._q.put(("stats", _stats(cancelled=True), epoch))
        panel._q.put(("done", "Par", epoch))
        panel._poll()
        self.root.update_idletasks()

        self.assertIn("stopped", self._transcript_text(panel))
        self.assertEqual(panel._history, [])   # partial turn never persisted

    def test_refresh_clears_transcript_history_and_readouts(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()

        epoch = panel._epoch
        panel._q.put(("delta", "hi", epoch))
        panel._q.put(("stats", _stats(), epoch))
        panel._q.put(("done", "hi", epoch))
        panel._poll()
        self.root.update_idletasks()
        self.assertNotEqual(panel._history, [])

        panel._on_refresh()
        self.root.update_idletasks()

        self.assertEqual(panel._history, [])
        self.assertEqual(panel._hestia._flux.cget("text"), "Scraptoken Flux: 0")
        self.assertEqual(panel._hestia._rites_main.cget("text"), "Rites: 0")
        self.assertIn("listening", self._transcript_text(panel))
        self.assertNotIn("you › hi", self._transcript_text(panel))

    def test_stop_silences_narration_even_with_no_thread_in_flight(self):
        # Regression: narration can still be playing seconds after the answer
        # text itself finished generating (synth/playback lags behind), so a
        # completed turn leaves no thread for `_cancel` to reach. Stop must
        # still silence Calliope directly in that case.
        panel = HomePanel(self.root)
        self.root.update_idletasks()
        self.assertIsNone(panel._thread)   # nothing in flight

        with mock.patch("panels.home_panel.calliope.stop") as stop:
            panel._on_stop()
        stop.assert_called_once()

    def test_hestia_stop_always_fires_regardless_of_running_state(self):
        # Regression: Stop used to no-op once Hestia's running flag flipped to
        # False (which happens as soon as text generation ends), even while
        # narration was still audibly playing. The click must always fire.
        panel = HomePanel(self.root)
        self.root.update_idletasks()
        panel._hestia.set_running(False)

        with mock.patch("panels.home_panel.calliope.stop") as stop:
            panel._hestia._fire_stop()
        stop.assert_called_once()

    def test_select_all_then_copy_puts_transcript_on_clipboard(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()

        panel._select_all_log()
        panel._copy_log_selection()
        self.root.update_idletasks()

        self.assertIn("listening", self.root.clipboard_get())

    def test_copy_with_no_selection_is_a_silent_no_op(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()
        panel._copy_log_selection()   # must not raise even with nothing selected

    def test_refresh_discards_stale_messages_from_prior_epoch(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()

        stale_epoch = panel._epoch
        panel._on_refresh()   # bumps the epoch, invalidating stale_epoch
        panel._q.put(("delta", "ghost text", stale_epoch))
        panel._q.put(("done", "ghost text", stale_epoch))
        panel._poll()
        self.root.update_idletasks()

        self.assertNotIn("ghost text", self._transcript_text(panel))


# ─────────────────────────────────────────────────────────────────────────────
#  Stop / Refresh — dedicated edge-case coverage
# ─────────────────────────────────────────────────────────────────────────────

class TestStopRefreshEdgeCases(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _transcript_text(self, panel: HomePanel) -> str:
        return panel._log.get("1.0", "end")

    # ── Stop: never-started / idempotence / doesn't disturb other state ──────

    def test_stop_before_anything_was_ever_asked_is_safe(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()
        self.assertIsNone(panel._thread)

        panel._on_stop()   # must not raise
        self.root.update_idletasks()

        self.assertEqual(panel._history, [])
        self.assertFalse(panel._hestia._running)

    def test_double_stop_is_idempotent(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()

        with mock.patch("panels.home_panel.calliope.stop") as stop:
            panel._on_stop()
            panel._on_stop()   # a second click before anything reacts

        self.assertEqual(stop.call_count, 2)   # both calls land, neither raises
        self.assertTrue(panel._cancel.is_set())

    def test_stop_does_not_touch_transcript_or_history_of_a_finished_turn(self):
        # Stop pressed purely to silence trailing narration after a turn has
        # already completed normally must not retroactively alter what was
        # already printed/persisted.
        panel = HomePanel(self.root)
        self.root.update_idletasks()

        epoch = panel._epoch
        panel._q.put(("delta", "It is Tuesday.", epoch))
        panel._q.put(("stats", _stats(), epoch))
        panel._q.put(("done", "It is Tuesday.", epoch))
        panel._poll()
        self.root.update_idletasks()

        before_text = self._transcript_text(panel)
        before_history = list(panel._history)

        panel._on_stop()
        self.root.update_idletasks()

        self.assertEqual(self._transcript_text(panel), before_text)
        self.assertEqual(panel._history, before_history)

    def test_stop_button_is_dim_yet_still_wired_when_idle(self):
        # The button is dim (not "running") most of the time a finished
        # answer is still being narrated. Confirms both halves of the fix:
        # the visual cue stays dim, but _fire_stop (what the <Button-1>
        # binding calls) still reaches HomePanel/Calliope regardless.
        panel = HomePanel(self.root)
        self.root.update_idletasks()
        panel._hestia.set_running(False)
        self.assertEqual(panel._hestia._stop.cget("fg"), theme.C["text3"])   # dim

        with mock.patch("panels.home_panel.calliope.stop") as stop:
            panel._hestia._fire_stop()
        stop.assert_called_once()

    # ── Refresh: idle / mid-stream / idempotence / stale-data isolation ──────

    def test_refresh_when_idle_is_a_safe_no_op_on_history(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()

        panel._on_refresh()   # nothing was ever asked — must not raise
        self.root.update_idletasks()

        self.assertEqual(panel._history, [])
        self.assertIn("listening", self._transcript_text(panel))

    def test_refresh_mid_stream_clears_partial_answer_before_it_finishes(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()

        epoch = panel._epoch
        panel._q.put(("delta", "The answer is still generat", epoch))
        panel._poll()
        self.root.update_idletasks()
        self.assertIn("still generat", self._transcript_text(panel))

        panel._on_refresh()
        self.root.update_idletasks()

        self.assertNotIn("still generat", self._transcript_text(panel))
        self.assertEqual(panel._history, [])
        self.assertFalse(panel._resp_started)
        self.assertEqual(panel._status.cget("text"), "")
        self.assertFalse(panel._hestia._running)

    def test_double_refresh_is_idempotent_and_epoch_advances_by_one_each_time(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()
        start_epoch = panel._epoch

        panel._on_refresh()
        panel._on_refresh()
        self.root.update_idletasks()

        self.assertEqual(panel._epoch, start_epoch + 2)
        self.assertEqual(panel._history, [])
        self.assertEqual(panel._hestia._flux.cget("text"), "Scraptoken Flux: 0")

    def test_refresh_resets_hestia_running_flag(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()
        panel._hestia.set_running(True)

        panel._on_refresh()
        self.root.update_idletasks()

        self.assertFalse(panel._hestia._running)

    def test_stale_stats_after_refresh_does_not_pollute_session_tallies(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()

        stale_epoch = panel._epoch
        panel._on_refresh()   # invalidates stale_epoch and zeroes tallies
        panel._q.put(("stats", _stats(prompt_tokens=999, output_tokens=999,
                                      tools_called=7), stale_epoch))
        panel._q.put(("done", "ghost", stale_epoch))
        panel._poll()
        self.root.update_idletasks()

        self.assertEqual(panel._hestia._flux.cget("text"), "Scraptoken Flux: 0")
        self.assertEqual(panel._hestia._rites_main.cget("text"), "Rites: 0")
        self.assertEqual(panel._history, [])

    def test_refresh_then_stop_sequence_ends_in_clean_idle_state(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()

        with mock.patch("panels.home_panel.calliope.stop") as stop:
            panel._on_refresh()
            panel._on_stop()

        self.assertGreaterEqual(stop.call_count, 2)   # refresh's + stop's own call
        self.assertEqual(panel._history, [])
        self.assertFalse(panel._hestia._running)

    def test_stop_then_refresh_sequence_leaves_no_stopped_marker_behind(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()

        epoch = panel._epoch
        panel._on_stop()
        panel._q.put(("delta", "Par", epoch))
        panel._q.put(("stats", _stats(cancelled=True), epoch))
        panel._q.put(("done", "Par", epoch))
        panel._poll()
        self.root.update_idletasks()
        self.assertIn("stopped", self._transcript_text(panel))

        panel._on_refresh()
        self.root.update_idletasks()

        self.assertNotIn("stopped", self._transcript_text(panel))
        self.assertEqual(panel._history, [])

    def test_refresh_during_a_cancelled_turns_late_arrival_is_still_clean(self):
        # Stop, then Refresh BEFORE the (already-cancelled) turn's queued
        # messages are drained — the stale epoch must still be dropped even
        # though the turn was itself a cancellation, not a normal answer.
        panel = HomePanel(self.root)
        self.root.update_idletasks()

        stale_epoch = panel._epoch
        panel._on_stop()
        panel._q.put(("delta", "Par", stale_epoch))
        panel._on_refresh()   # jumps ahead before stats/done are polled
        panel._q.put(("stats", _stats(cancelled=True), stale_epoch))
        panel._q.put(("done", "Par", stale_epoch))
        panel._poll()
        self.root.update_idletasks()

        self.assertNotIn("stopped", self._transcript_text(panel))
        self.assertNotIn("Par", self._transcript_text(panel))
        self.assertEqual(panel._history, [])
        self.assertEqual(panel._hestia._flux.cget("text"), "Scraptoken Flux: 0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
