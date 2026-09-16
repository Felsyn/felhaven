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
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import numpy as np

import calliope
import harmonia
import theme
from panels import home_panel
from panels.home_panel import HomePanel
from tools import ammit, morpheus

# HomePanel now builds an AmmitBadge (panels/horai_panel.py) at construction,
# which reads ammit.query_all() — swap ammit.STATE_FILE for the whole module
# so no test here touches the real timer_state.json, the same hermetic rule
# tests/test_ammit.py and test_horai_panel_smoke.py already follow.
_orig_ammit_state = None
_ammit_tmp = None


def setUpModule():
    global _orig_ammit_state, _ammit_tmp
    _orig_ammit_state = ammit.STATE_FILE
    _ammit_tmp = tempfile.TemporaryDirectory()
    ammit.STATE_FILE = Path(_ammit_tmp.name) / "timer_state.json"


def tearDownModule():
    ammit.STATE_FILE = _orig_ammit_state
    _ammit_tmp.cleanup()


def _stats(**overrides) -> dict:
    base = {
        "type": "stats", "prompt_tokens": 10, "output_tokens": 5,
        "wall_ms": 2100, "eval_ms": 1000, "tools_called": 1,
        "tools_failed": 0, "failed_tools": [], "cancelled": False,
        "turn_messages": [{"role": "user", "content": "hi"},
                         {"role": "assistant", "content": "hi"}],
        "tool_records": [
            {"seq": 1, "round": 1, "name": "get_weather", "arguments": {},
             "arguments_truncated": False, "status": "ok",
             "started_at": "2026-08-18T09:07:12.431", "duration_ms": 42,
             "error": None, "result_preview": '{"temp": 12}',
             "result_truncated": False},
        ],
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

        turn = [{"role": "user", "content": "what day is it?"},
               {"role": "assistant", "content": "It is Tuesday."}]
        epoch = panel._epoch
        panel._q.put(("delta", "It is Tuesday.", epoch))
        panel._q.put(("stats", _stats(turn_messages=turn), epoch))
        panel._q.put(("done", "It is Tuesday.", epoch))
        panel._poll()
        self.root.update_idletasks()

        text = self._transcript_text(panel)
        self.assertIn("15 tok", text)          # 10 prompt + 5 output
        self.assertIn("tok/s", text)
        self.assertIn("1 tools", text)
        self.assertEqual(panel._flux.cget("text"), "Scraptoken Flux: 15")
        self.assertEqual(panel._rites_main.cget("text"), "Rites: 1")
        # History stores the turn's REAL exchange verbatim, not flattened
        # prose — one entry per turn, each a full message list.
        self.assertEqual(panel._history, [turn])

    def test_persisted_turn_keeps_tool_calls_and_tool_results_verbatim(self):
        # The whole point of storing turn_messages instead of flattened prose:
        # a tool_calls-bearing assistant message and its tool result must
        # survive into self._history exactly as pythia.ask() produced them,
        # not collapsed down to {"role": "assistant", "content": answer}.
        panel = HomePanel(self.root)
        self.root.update_idletasks()

        turn = [
            {"role": "user", "content": "weather?"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "get_weather", "arguments": {}}}]},
            {"role": "tool", "tool_name": "get_weather", "content": '{"temp_f": 70}'},
            {"role": "assistant", "content": "It's 70."},
        ]
        epoch = panel._epoch
        panel._q.put(("delta", "It's 70.", epoch))
        panel._q.put(("stats", _stats(turn_messages=turn, tools_called=1), epoch))
        panel._q.put(("done", "It's 70.", epoch))
        panel._poll()
        self.root.update_idletasks()

        self.assertEqual(panel._history, [turn])
        self.assertIn("tool_calls", panel._history[0][1])
        self.assertEqual(panel._history[0][2]["role"], "tool")

    def test_history_window_keeps_only_the_last_three_turns(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()

        for i in range(4):
            turn = [{"role": "user", "content": f"q{i}"},
                   {"role": "assistant", "content": f"a{i}"}]
            epoch = panel._epoch
            panel._q.put(("delta", f"a{i}", epoch))
            panel._q.put(("stats", _stats(turn_messages=turn), epoch))
            panel._q.put(("done", f"a{i}", epoch))
            panel._poll()
            self.root.update_idletasks()

        self.assertEqual(len(panel._history), 3)
        firsts = [turn[0]["content"] for turn in panel._history]
        self.assertEqual(firsts, ["q1", "q2", "q3"])   # q0 aged out

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
        self.assertEqual(panel._rites_fail.cget("text"), " · 1 failed")

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
        self.assertEqual(panel._flux.cget("text"), "Scraptoken Flux: 0")
        self.assertEqual(panel._rites_main.cget("text"), "Rites: 0")
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
        # Regression: Stop (now "Vox End") used to no-op once Hestia's running
        # flag flipped to False (which happens as soon as text generation
        # ends), even while narration was still audibly playing. The click must
        # always fire, whatever the dim/lit cue says.
        panel = HomePanel(self.root)
        self.root.update_idletasks()
        panel._hestia.set_running(False)

        with mock.patch("panels.home_panel.calliope.stop") as stop:
            panel._hestia._vox._end._on_click()
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

class TestTheChatBarCannotBeSqueezedOut(unittest.TestCase):
    """Nothing above the entry may out-request the entry itself.

    Pack hands out requested heights in pack order and only then gives the
    leftover to expand=True children. The transcript sits above the status line
    and the input row, so anything in it that asks for real height wins the
    argument and the chat bar collapses to 1 px — which is exactly what happened
    (a tk.Text asks for 24 lines, and PhosphorScroll asked for a Canvas's
    default 265 px). It was invisible on a maximised window and no test failed.

    The rule this pins: the transcript asks for almost nothing and grows through
    expand, so the transcript is what shrinks on a small window. It scrolls; the
    thing you type into does not."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_the_transcript_asks_for_almost_no_height(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()
        self.assertLess(panel._log.winfo_reqheight(), 60)

    def test_the_transcript_row_asks_for_less_than_the_rows_below_it(self):
        # The row, not just the Text — this is what caught the scrollbar.
        panel = HomePanel(self.root)
        self.root.update_idletasks()
        transcript_row = panel._log.master
        below = (panel._status.master.winfo_reqheight()
                 + panel._entry.master.winfo_reqheight())
        self.assertLess(
            transcript_row.winfo_reqheight(), below + 60,
            "the transcript row out-requests the status line and the entry row "
            "together — on a small window pack will squeeze the chat bar out")

    def test_the_transcript_still_expands_to_take_the_leftover(self):
        # The other half of the deal: asking for nothing is only correct
        # because expand=True gives it everything that is left.
        panel = HomePanel(self.root)
        self.root.update_idletasks()
        info = panel._log.master.pack_info()
        self.assertEqual(str(info["expand"]), "1")
        self.assertEqual(str(info["fill"]), "both")


class TestFaceStateMapping(unittest.TestCase):
    """What the wireframe face is told, and why it is told from the poll.

    The mapping is derived fresh on every drain rather than stored, so there is
    no third copy of "is she busy" to fall out of step with the thread and the
    audio buffer. These pin the three arms and the one thing the arms exist
    for: Vox End stays armed while narration is still audible, long after the
    text finished."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _panel(self) -> HomePanel:
        panel = HomePanel(self.root)
        self.root.update_idletasks()
        return panel

    def test_idle_when_nothing_is_happening(self):
        panel = self._panel()
        with mock.patch("panels.home_panel.harmonia.is_playing", return_value=False),              mock.patch("panels.home_panel.pythia.is_warming", return_value=False):
            panel._tick_face()
        self.assertEqual(panel._hestia._face.state, "idle")
        self.assertFalse(panel._hestia._vox._end._active)

    def test_thinking_while_the_model_is_still_warming(self):
        panel = self._panel()
        with mock.patch("panels.home_panel.harmonia.is_playing", return_value=False),              mock.patch("panels.home_panel.pythia.is_warming", return_value=True):
            panel._tick_face()
        self.assertEqual(panel._hestia._face.state, "thinking")

    def test_speaking_outranks_a_live_worker(self):
        # Audio is the louder fact: if she is talking, that is what the face
        # should say, even while the next tokens are still arriving.
        panel = self._panel()
        with mock.patch("panels.home_panel.harmonia.is_playing", return_value=True),              mock.patch("panels.home_panel.pythia.is_warming", return_value=True):
            panel._tick_face()
        self.assertEqual(panel._hestia._face.state, "speaking")

    def test_vox_end_stays_armed_while_narration_outlives_the_answer(self):
        # The whole reason the cue is derived here and not in _on_done: synth
        # and playback lag behind generation.
        panel = self._panel()
        panel._on_done("finished")                      # text is complete
        self.assertFalse(panel._hestia._vox._end._active)
        with mock.patch("panels.home_panel.harmonia.is_playing", return_value=True),              mock.patch("panels.home_panel.pythia.is_warming", return_value=False):
            panel._tick_face()
        self.assertTrue(panel._hestia._vox._end._active)

    def test_playing_when_only_the_music_is_making_noise(self):
        panel = self._panel()
        with mock.patch("panels.home_panel.harmonia.is_playing", return_value=False), \
                mock.patch("panels.home_panel.pythia.is_warming", return_value=False), \
                mock.patch("panels.home_panel.morpheus.is_audible", return_value=True):
            panel._tick_face()
        self.assertEqual(panel._hestia._face.state, "playing")

    def test_speaking_outranks_music(self):
        # Not an edge case: narration DUCKS the music rather than stopping it,
        # so both are true on every spoken answer over a playing track. Her own
        # voice wins over her soundtrack.
        panel = self._panel()
        with mock.patch("panels.home_panel.harmonia.is_playing", return_value=True), \
                mock.patch("panels.home_panel.pythia.is_warming", return_value=False), \
                mock.patch("panels.home_panel.morpheus.is_audible", return_value=True):
            panel._tick_face()
        self.assertEqual(panel._hestia._face.state, "speaking")

    def test_thinking_outranks_music(self):
        panel = self._panel()
        with mock.patch("panels.home_panel.harmonia.is_playing", return_value=False), \
                mock.patch("panels.home_panel.pythia.is_warming", return_value=True), \
                mock.patch("panels.home_panel.morpheus.is_audible", return_value=True):
            panel._tick_face()
        self.assertEqual(panel._hestia._face.state, "thinking")

    def test_the_face_tick_never_pays_for_an_mpv_round_trip(self):
        """This runs 16 times a second. status() is IPC over a named pipe;
        calling it from here is the mistake this whole cache exists to stop."""
        panel = self._panel()
        with mock.patch("panels.home_panel.harmonia.is_playing", return_value=False), \
                mock.patch("panels.home_panel.pythia.is_warming", return_value=False), \
                mock.patch("tools.morpheus.status") as status, \
                mock.patch("tools.morpheus._ipc") as ipc:
            for _ in range(20):
                panel._tick_face()
        status.assert_not_called()
        ipc.assert_not_called()

    def test_repeated_ticks_do_not_reset_the_speaking_envelope(self):
        # The poll runs at 60 ms for the panel's whole life, so this is the
        # normal case, not an edge one.
        panel = self._panel()
        with mock.patch("panels.home_panel.harmonia.is_playing", return_value=True),              mock.patch("panels.home_panel.pythia.is_warming", return_value=False):
            panel._tick_face()
            panel._hestia._face._syl = 3.1
            for _ in range(5):
                panel._tick_face()
        self.assertEqual(panel._hestia._face._syl, 3.1)


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
        self.assertFalse(panel._hestia._vox._end._active)

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
        # Vox End is dim (not "armed") most of the time a finished answer is
        # still being narrated. Confirms both halves of the fix: the visual cue
        # stays dim, but the click binding still reaches HomePanel/Calliope
        # regardless.
        panel = HomePanel(self.root)
        self.root.update_idletasks()
        panel._hestia.set_running(False)
        self.assertEqual(panel._hestia._vox._end._label.cget("fg"), theme.C["text3"])

        with mock.patch("panels.home_panel.calliope.stop") as stop:
            panel._hestia._vox._end._on_click()
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
        self.assertFalse(panel._hestia._vox._end._active)

    def test_double_refresh_is_idempotent_and_epoch_advances_by_one_each_time(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()
        start_epoch = panel._epoch

        panel._on_refresh()
        panel._on_refresh()
        self.root.update_idletasks()

        self.assertEqual(panel._epoch, start_epoch + 2)
        self.assertEqual(panel._history, [])
        self.assertEqual(panel._flux.cget("text"), "Scraptoken Flux: 0")

    def test_refresh_resets_hestia_running_flag(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()
        panel._hestia.set_running(True)

        panel._on_refresh()
        self.root.update_idletasks()

        self.assertFalse(panel._hestia._vox._end._active)

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

        self.assertEqual(panel._flux.cget("text"), "Scraptoken Flux: 0")
        self.assertEqual(panel._rites_main.cget("text"), "Rites: 0")
        self.assertEqual(panel._history, [])

    def test_refresh_then_stop_sequence_ends_in_clean_idle_state(self):
        panel = HomePanel(self.root)
        self.root.update_idletasks()

        with mock.patch("panels.home_panel.calliope.stop") as stop:
            panel._on_refresh()
            panel._on_stop()

        self.assertGreaterEqual(stop.call_count, 2)   # refresh's + stop's own call
        self.assertEqual(panel._history, [])
        self.assertFalse(panel._hestia._vox._end._active)

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
        self.assertEqual(panel._flux.cget("text"), "Scraptoken Flux: 0")


class TestHomePanelPollSurvives(unittest.TestCase):
    """The poll chain must outlive any single failing tick.

    It is load-bearing far beyond its own panel: _poll drives _on_delta, which
    is what hands sentences to Calliope. When an exception could escape it, one
    bad tick stopped the chain dead — narration halted mid-answer, the
    transcript froze, and Vox End stayed lit because _tick_face never ran again
    to clear it. And it was silent: Tk prints a failing callback to stderr, and
    Felhaven runs under pythonw.exe, which has none."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _rearms_despite(self, target):
        panel = HomePanel(self.root)
        with mock.patch.object(panel, "_arm_poll") as arm,                 mock.patch(target, side_effect=RuntimeError("boom")):
            panel._poll()                    # must not raise
            arm.assert_called_once()

    def test_a_failing_tick_face_still_rearms(self):
        self._rearms_despite("panels.home_panel.harmonia.is_playing")

    def test_a_failing_tick_yield_still_rearms(self):
        self._rearms_despite("panels.home_panel.harmonia.tick_yield")

    def test_a_failing_warmup_tick_still_rearms(self):
        self._rearms_despite("panels.home_panel.pythia.is_warming")

    def test_a_failing_queue_handler_still_rearms(self):
        panel = HomePanel(self.root)
        panel._q.put(("delta", "hello", panel._epoch))
        with mock.patch.object(panel, "_arm_poll") as arm,                 mock.patch.object(panel, "_on_delta",
                                  side_effect=RuntimeError("boom")):
            panel._poll()
            arm.assert_called_once()

    def test_the_failure_is_logged_rather_than_swallowed_silently(self):
        """Swallowing is only acceptable because the exception lands in
        felhaven.log, where it can actually be found."""
        panel = HomePanel(self.root)
        with mock.patch("panels.home_panel.harmonia.tick_yield",
                        side_effect=RuntimeError("boom")),                 self.assertLogs("METIS.home_panel", level="ERROR") as caught:
            panel._poll()
        self.assertTrue(any("boom" in line for line in caught.output))

    def test_a_persistent_fault_does_not_flood_the_log(self):
        """The chain ticks at 16 Hz. Logging every failure writes ~3.7 KB/s,
        which churns felhaven.log's 1 MB x 3 rotation in under twenty minutes
        and destroys the first traceback — the only one worth reading. Felhaven
        gets left running for days, so this must trickle, not flood."""
        panel = HomePanel(self.root)
        with mock.patch("panels.home_panel.harmonia.tick_yield",
                        side_effect=RuntimeError("boom")),                 self.assertLogs("METIS.home_panel", level="ERROR") as caught:
            for _ in range(200):               # ~12 seconds of ticking
                panel._poll()
        self.assertEqual(len(caught.output), 1,
                         "a persistent fault must report once per quiet period")

    def test_a_suppressed_burst_is_counted_in_the_next_report(self):
        """Suppressed failures must not vanish — the next report says how many
        it stands for, or a fault that settles looks rarer than it was."""
        panel = HomePanel(self.root)
        with mock.patch("panels.home_panel.harmonia.tick_yield",
                        side_effect=RuntimeError("boom")):
            panel._poll()                      # first: reported
            for _ in range(9):
                panel._poll()                  # counted, not written
            panel._poll_fail_reported -= home_panel._POLL_FAIL_QUIET + 1
            with self.assertLogs("METIS.home_panel", level="ERROR") as caught:
                panel._poll()                  # quiet period elapsed
        self.assertIn("10 since last report", caught.output[0])


class TestHomePanelManualSpeak(unittest.TestCase):
    """The speak button hands Calliope a COMPLETE answer, so it must release the
    prebuffer gate itself.

    This used to be free: the gate short-circuited unless a filler was masking
    the wait, and the button never armed one. With the fillers gone the gate is
    unconditional, and a one-chunk answer can never reach the lead it waits
    for — so without end_turn() the button stalls for the full
    _PREBUFFER_TIMEOUT (6 s) before a word is heard."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_speak_releases_the_prebuffer_gate(self):
        panel = HomePanel(self.root)
        with mock.patch("panels.home_panel.calliope.stop"), \
                mock.patch("panels.home_panel.calliope.speak"), \
                mock.patch("panels.home_panel.calliope.end_turn") as end_turn:
            panel._speak("A short answer.")
        end_turn.assert_called_once()

    def test_a_one_chunk_answer_reaches_playback_promptly(self):
        """End to end against the real gate: no mocking of end_turn, so a
        regression here shows up as the actual 6 s stall."""
        panel = HomePanel(self.root)
        calliope.stop()                          # reset the gate for this turn
        with mock.patch("calliope.synthesize",
                        return_value=np.zeros(240, dtype=np.float32)), \
                mock.patch("harmonia.play") as play:
            started = time.monotonic()
            panel._speak("Short.")
            deadline = started + 3.0
            while not play.call_args_list and time.monotonic() < deadline:
                time.sleep(0.01)
            elapsed = time.monotonic() - started
        calliope.stop()
        self.assertTrue(play.call_args_list, "narration never reached Harmonia")
        self.assertLess(elapsed, calliope._PREBUFFER_TIMEOUT,
                        "the speak button stalled on the prebuffer gate")


class TestHomePanelRiteLog(unittest.TestCase):
    """The meta-line's two counts are click targets, and the records behind
    them live in a bounded ring. Queue events are pushed straight in and
    drained by hand, the same as the rest of this file."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()          # no window flashes during the run
        theme._init_fonts(cls.root)  # FONTS is empty until a root exists

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _turn(self, panel: HomePanel, **overrides) -> None:
        """One complete turn: a stats event, then the done event that renders
        the meta-line off it."""
        panel._on_stats(_stats(**overrides))
        panel._on_done("answer")
        self.root.update_idletasks()

    def _rite_tags(self, panel: HomePanel) -> set:
        return {t for t in panel._log.tag_names() if t.startswith("rite")}

    def test_meta_line_text_is_unchanged(self):
        panel = HomePanel(self.root)
        self._turn(panel, tools_failed=1, failed_tools=["a"])
        line = [l for l in panel._log.get("1.0", "end").split("\n")
                if "tok/s" in l]
        self.assertEqual(line, ["   15 tok · 2.1s · 5.0 tok/s · 1 tools · ⚠ 1 failed"])

    def test_both_counts_get_their_own_tag(self):
        panel = HomePanel(self.root)
        self._turn(panel, tools_failed=1, failed_tools=["a"])
        self.assertEqual(self._rite_tags(panel), {"rite1", "ritefail1"})

    def test_a_turn_with_no_tools_is_untagged(self):
        # Nothing to show, so it must not offer a click target that promises
        # otherwise.
        panel = HomePanel(self.root)
        self._turn(panel, tools_called=0, tool_records=[])
        self.assertEqual(self._rite_tags(panel), set())

    def test_records_are_stored_under_the_tag_id(self):
        panel = HomePanel(self.root)
        self._turn(panel)
        self.assertEqual(panel._rites[panel._rite_seq], _stats()["tool_records"])

    def test_the_ring_is_bounded_and_evicts_the_oldest(self):
        panel = HomePanel(self.root)
        for _ in range(25):
            self._turn(panel)
        self.assertEqual(len(panel._rites), 20)
        newest = panel._rite_seq
        self.assertEqual(min(panel._rites), newest - 19)
        self.assertEqual(max(panel._rites), newest)

    def test_clicking_an_evicted_turn_opens_the_expired_view(self):
        panel = HomePanel(self.root)
        for _ in range(25):
            self._turn(panel)
        evicted = panel._rite_seq - 20
        self.assertNotIn(evicted, panel._rites)
        panel._open_rites(evicted, False)     # must not raise
        self.root.update_idletasks()

    def test_refresh_empties_the_ring_but_not_the_counter(self):
        panel = HomePanel(self.root)
        self._turn(panel)
        seq_before = panel._rite_seq
        panel._on_refresh()
        self.root.update_idletasks()
        self.assertEqual(len(panel._rites), 0)
        # A monotonic counter can never hand a new turn a stale tag name.
        self.assertEqual(panel._rite_seq, seq_before)

    def test_hover_lights_the_span_and_restores(self):
        panel = HomePanel(self.root)
        self._turn(panel, tools_failed=1, failed_tools=["a"])
        panel._hover_rite("ritefail1", theme.C["red_bright"], True)
        self.assertEqual(panel._log.tag_cget("ritefail1", "foreground"),
                         theme.C["red_bright"])
        self.assertEqual(str(panel._log.cget("cursor")), "hand2")
        panel._hover_rite("ritefail1", theme.C["red"], False)
        self.assertEqual(panel._log.tag_cget("ritefail1", "foreground"),
                         theme.C["red"])
        self.assertEqual(str(panel._log.cget("cursor")), "")


class TestHomePanelWarmupNotice(unittest.TestCase):
    """The startup window: pythia.prewarm() is loading the model, so the status
    line says so instead of the panel sitting silently for ~a minute."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_notice_shows_while_warming_and_clears_when_ready(self):
        panel = HomePanel(self.root)
        with mock.patch("panels.home_panel.pythia.is_warming",
                        return_value=True):
            panel._poll()
            self.root.update_idletasks()
            self.assertIn("waking", panel._status.cget("text"))

        with mock.patch("panels.home_panel.pythia.is_warming",
                        return_value=False):
            panel._poll()
            self.root.update_idletasks()
            self.assertEqual(panel._status.cget("text"), "")

    def test_warmup_notice_never_stomps_an_in_flight_question(self):
        """A live request owns the status line — 'consulting the oracle…' must
        survive a poll that happens while the warm is still running."""
        panel = HomePanel(self.root)
        panel._status.config(text="consulting the oracle…")
        alive = mock.Mock()
        alive.is_alive.return_value = True
        panel._thread = alive
        with mock.patch("panels.home_panel.pythia.is_warming",
                        return_value=True):
            panel._poll()
            self.root.update_idletasks()
        self.assertIn("consulting", panel._status.cget("text"))


class TestHomePanelMorpheusYield(unittest.TestCase):
    """Harmonia is forbidden a timer of its own (§6), so this standing 60 ms
    drain chain is what hands Morpheus its volume back after narration. If the
    call is ever dropped from _poll, music stays quiet with nothing to blame."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_poll_reconciles_the_morpheus_yield(self):
        panel = HomePanel(self.root)
        with mock.patch("panels.home_panel.harmonia.tick_yield") as tick:
            panel._poll()
            tick.assert_called_once()

    def test_an_idle_drain_generates_no_mpv_ipc(self):
        """The real tick_yield against a real Harmonia with no mpv running:
        60 ms of nothing must cost zero named-pipe opens."""
        panel = HomePanel(self.root)
        saved = morpheus._proc
        morpheus._proc = None
        try:
            with mock.patch("tools.morpheus._ipc") as ipc, \
                    mock.patch("tools.morpheus._get_props") as props:
                for _ in range(5):
                    panel._poll()
                ipc.assert_not_called()
                props.assert_not_called()
        finally:
            morpheus._proc = saved


if __name__ == "__main__":
    unittest.main(verbosity=2)
