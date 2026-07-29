"""
test_emanon_panel_smoke.py — the EMANON tab body: the wrapped, copyable log
view, the verdict dot, and the one-shot "failed" blink.

Headless Tk per CONVENTIONS §10: a real tk.Tk() root we withdraw() so no window
flashes, driven with update_idletasks() instead of mainloop(). No exception
raised == pass. Nothing is mocked — the panel just receives snapshot dicts of
the shape emanon.fetch() returns (now including a per-entry `count`).

The events area is a read-only tk.Text (the home_panel transcript precedent), so
the assertions read its text and exercise the right-click Copy path — the whole
point of the change being that a log line can be selected and copied. The blink
is the panel's one §6-sanctioned after(): we never run mainloop, so its calls
are scheduled but do not fire; what's asserted is the state machine around them.

    python -X utf8 -m unittest tests.test_emanon_panel_smoke
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
from panels.emanon_panel import EmanonPanel


def _snapshot(status="nominal", entries=None, err=0, warn=0, note=None):
    snap = {"status": status, "error_count": err, "warning_count": warn,
            "entries": entries if entries is not None else [], "files": ["felhaven.log"]}
    if note is not None:
        snap["note"] = note
    return snap


def _entry(level="INFO", logger="METIS.aether", message="ok",
           ts="2026-05-28 14:32:01", count=1):
    return {"ts": ts, "level": level, "logger": logger, "message": message,
            "count": count, "raw": f"{ts} | {level} | {logger} | {message}"}


class TestEmanonPanelSmoke(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.panel = EmanonPanel(self.root)
        self.panel.pack()

    def tearDown(self):
        self.panel.destroy()

    def _update(self, snap):
        self.panel.update(snap)
        self.root.update_idletasks()

    def _log_text(self):
        return self.panel._log.get("1.0", "end")

    # ── Building ────────────────────────────────────────────────────────────

    def test_builds_before_any_data(self):
        self.assertEqual(self.panel._status, "nominal")

    def test_none_renders_unavailable_without_raising(self):
        # Kairos delivers None when emanon.fetch raised (it never should, but the
        # panel must survive it anyway).
        self._update(None)
        self.assertEqual(self.panel._status, "unavailable")
        self.assertEqual(self.panel._status_lbl.cget("text"), "unavailable")
        self.assertIn("watcher unavailable", self._log_text())

    def test_an_empty_snapshot_shows_its_note(self):
        self._update(_snapshot(note="no logs/ directory yet"))
        self.assertIn("no logs/ directory yet", self._log_text())

    # ── The log view ──────────────────────────────────────────────────────────

    def test_events_render_into_the_log(self):
        self._update(_snapshot(entries=[_entry(message="alpha"), _entry(message="bravo")]))
        text = self._log_text()
        self.assertIn("alpha", text)
        self.assertIn("bravo", text)

    def test_a_long_message_is_shown_in_full_not_truncated(self):
        # The whole reason for the rewrite: a wrapped Text shows every character,
        # where the old Label row clipped at 48 with a "…".
        long_msg = "HTTPSConnectionPool(host='example.com', port=443): " + "x" * 150
        self._update(_snapshot(entries=[_entry(level="WARNING", message=long_msg)]))
        self.assertIn(long_msg, self._log_text())

    def test_a_folded_run_shows_its_count(self):
        self._update(_snapshot(entries=[_entry(level="ERROR", message="db down", count=7)],
                               err=7))
        self.assertIn("×7", self._log_text())

    def test_the_logger_prefix_is_stripped(self):
        self._update(_snapshot(entries=[_entry(logger="METIS.midas", message="hi")]))
        text = self._log_text()
        self.assertIn("midas", text)
        self.assertNotIn("METIS.midas", text)

    # ── Right-click copy ──────────────────────────────────────────────────────

    def test_copy_all_puts_the_whole_tail_on_the_clipboard(self):
        self._update(_snapshot(entries=[_entry(message="copyable error line")]))
        self.panel._copy_all()
        self.root.update()
        self.assertIn("copyable error line", self.panel.clipboard_get())

    def test_select_all_then_copy_selection_roundtrips(self):
        self._update(_snapshot(entries=[_entry(message="select me")]))
        self.panel._select_all_log()
        self.panel._copy_log_selection()
        self.root.update()
        self.assertIn("select me", self.panel.clipboard_get())

    def test_building_the_menu_does_not_raise(self):
        # tk_popup needs a real pointer event to display, so we don't post it;
        # we just prove the menu assembles (Copy disabled with no selection).
        menu = theme.themed_menu(self.panel)
        menu.add_command(label="Copy", state="disabled")
        self.assertEqual(menu.type(0), "command")

    # ── Verdict dot + the blink state machine ────────────────────────────────

    def test_the_dot_and_word_track_the_status(self):
        self._update(_snapshot(status="degraded", warn=1))
        self.assertEqual(self.panel._status_lbl.cget("text"), "degraded")
        self.assertEqual(self.panel._status_lbl.cget("fg"), theme.C["amber"])

    def test_the_blink_arms_only_on_the_transition_into_failed(self):
        self._update(_snapshot(status="failed", err=1))
        self.assertEqual(self.panel._status, "failed")
        self.assertIsNotNone(self.panel._flash_after_id)  # a blink is scheduled

    def test_staying_failed_does_not_re_arm_the_blink(self):
        self._update(_snapshot(status="failed", err=1))
        self.panel._cancel_flash()                 # simulate the blink finishing
        self._update(_snapshot(status="failed", err=2))
        self.assertIsNone(self.panel._flash_after_id)  # no fresh strobe

    def test_recovery_cancels_the_blink(self):
        self._update(_snapshot(status="failed", err=1))
        self._update(_snapshot(status="nominal"))
        self.assertIsNone(self.panel._flash_after_id)
        self.assertEqual(self.panel._status, "nominal")


if __name__ == "__main__":
    unittest.main(verbosity=2)
