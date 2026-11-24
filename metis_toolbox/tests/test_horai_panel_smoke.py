"""
test_horai_panel_smoke.py — headless Tk smoke test for horai_panel.

Covers both widgets the file hosts — HoraiPanel (clock/date/badges) and the
AmmitWidget countdown it embeds — including the None (Kairos-stale) tick every
update() must survive (CONVENTIONS S7). ammit.STATE_FILE is swapped for a temp
path so the timer widget never reads or writes the real timer_state.json, and
Themis reads a scratch settings file (update() reads the clock format).

No window flashes: tk.Tk() + withdraw() + update_idletasks(), no mainloop.
No exception == pass. Run from the package root:
    python -X utf8 -m unittest tests.test_horai_panel_smoke
"""

import os
import sys
import tempfile
import unittest
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import theme
import themis
from tools import ammit
from panels.horai_panel import HoraiPanel, AmmitWidget, AmmitBadge

# The shape horai.handle() returns — built statically so the panel test does not
# couple to the tool.
_SAMPLE = {
    "iso": "2026-07-24T12:00:00-04:00",
    "clock": "Friday, July 24 2026, 12:00 PM",
    "season": "Summer",
    "cycle": {"label": "Afternoon", "hour": 12},
}


class TestHoraiPanelSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()          # no window flashes during the run
        theme._init_fonts(cls.root)  # FONTS is empty until a root exists

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        # Hermetic: no test reads or writes the real timer_state.json.
        self._orig_state = ammit.STATE_FILE
        self._tmp = tempfile.TemporaryDirectory()
        ammit.STATE_FILE = Path(self._tmp.name) / "timer_state.json"
        p = mock.patch.object(themis, "_DATA_PATH",
                              Path(self._tmp.name) / "felhaven_settings.json")
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        ammit.STATE_FILE = self._orig_state
        self._tmp.cleanup()

    def test_panel_constructs(self):
        HoraiPanel(self.root)            # __init__ must not raise (builds AmmitWidget too)
        self.root.update_idletasks()

    def test_panel_update_none_is_safe(self):
        # Kairos delivers None on a stale tick; update() must return, not crash.
        p = HoraiPanel(self.root)
        p.update(None)
        self.root.update_idletasks()

    def test_panel_update_sets_badges(self):
        p = HoraiPanel(self.root)
        p.update(_SAMPLE)
        self.root.update_idletasks()
        self.assertEqual(p.cycle_lbl.cget("text"), "Afternoon")
        self.assertEqual(p.season_lbl.cget("text"), "Summer")
        self.assertEqual(p.date_lbl.cget("text"), _SAMPLE["clock"])

    def test_ammit_widget_constructs(self):
        AmmitWidget(self.root)
        self.root.update_idletasks()

    def test_ammit_widget_section_toggle(self):
        w = AmmitWidget(self.root)
        self.root.update_idletasks()
        w._section_toggle()             # expand
        w._section_toggle()             # collapse
        self.root.update_idletasks()

    def test_ammit_widget_reflects_a_running_timer(self):
        w = AmmitWidget(self.root)
        ammit.start_timer(0, 300)       # 5 minutes on the shared (temp) state
        w._refresh()
        self.root.update_idletasks()
        self.assertNotEqual(w._display.cget("text"), "00:00:00")

    def test_the_minutes_box_asks_ammits_own_duration_check(self):
        # Measured 2026-11-21: the box did int(minutes * 60), so "0.01" saved
        # a running 0-second countdown, expired at once (the instant alarm
        # handle() stopped on 2026-09-27), and "nan", "inf" and "1e400"
        # raised inside the Enter key's callback.
        w = AmmitWidget(self.root)

        def start(text):
            ammit.reset_timer(0)
            w._entry.delete(0, "end")
            w._entry.insert(0, text)
            try:
                w._start()
            except (ValueError, OverflowError) as e:
                self.fail(f"{text!r} raised {e!r}")
            return ammit.query_all()[0]

        t = start("0.01")                  # 0.6 s rounds to one second
        self.assertTrue(t["running"])
        self.assertFalse(t["expired"])
        self.assertEqual(t["remaining_seconds"], 1)
        for text in ("0.005", "0", "-2", "nan", "inf", "1e400", "ten", ""):
            with self.subTest(text):
                t = start(text)
                self.assertFalse(t["running"])
                self.assertEqual(w._entry.get(), text)   # left as typed

    def test_ammit_badge_constructs(self):
        AmmitBadge(self.root)
        self.root.update_idletasks()

    def test_ammit_badge_idle_state(self):
        b = AmmitBadge(self.root)
        self.root.update_idletasks()
        self.assertEqual(b._label.cget("text"), "AMMIT --:--")
        self.assertEqual(b._label.cget("fg"), theme.C["text3"])

    def test_ammit_badge_reflects_a_running_timer(self):
        b = AmmitBadge(self.root)
        ammit.start_timer(0, 300)
        b.refresh()
        self.root.update_idletasks()
        self.assertIn("AMMIT ", b._label.cget("text"))
        self.assertNotEqual(b._label.cget("text"), "AMMIT --:--")
        self.assertEqual(b._label.cget("fg"), theme.C["amber"])

    def test_ammit_badge_reflects_an_expired_timer(self):
        past = (datetime.now().astimezone() - timedelta(minutes=2)).isoformat()
        ammit._save({"timers": [
            {"duration_seconds": 60, "started_at": past, "running": True,
             "acknowledged": False}
        ]})
        b = AmmitBadge(self.root)
        self.root.update_idletasks()
        self.assertEqual(b._label.cget("text"), "AMMIT TIME'S UP")
        self.assertEqual(b._label.cget("fg"), theme.C["red"])

    def test_ammit_badge_goes_green_not_red_once_acknowledged(self):
        # The bug this pins: the badge used to key off `expired` alone, so
        # dismissing the alarm popup elsewhere never reached it and it stayed
        # red until the timer was actually stopped/reset.
        past = (datetime.now().astimezone() - timedelta(minutes=2)).isoformat()
        ammit._save({"timers": [
            {"duration_seconds": 60, "started_at": past, "running": True,
             "acknowledged": True}
        ]})
        b = AmmitBadge(self.root)
        self.root.update_idletasks()
        self.assertEqual(b._label.cget("text"), "AMMIT 00:00:00")
        self.assertEqual(b._label.cget("fg"), theme.C["green"])

    def test_ammit_widget_goes_green_not_red_once_acknowledged(self):
        # AmmitWidget tracks the same acknowledged flag as AmmitBadge — both
        # read ammit.py's state, so dismissing the popup on any panel calms
        # both displays down together.
        past = (datetime.now().astimezone() - timedelta(minutes=2)).isoformat()
        ammit._save({"timers": [
            {"duration_seconds": 60, "started_at": past, "running": True,
             "acknowledged": True}
        ]})
        w = AmmitWidget(self.root)
        self.root.update_idletasks()
        self.assertEqual(w._display.cget("fg"), theme.C["green"])
        self.assertEqual(w._btn.cget("fg"), theme.C["green"])

    def test_the_countdowns_controls_carry_their_tips(self):
        from panels import virgil_tooltip
        w = AmmitWidget(self.root)
        self.addCleanup(w.destroy)
        self.assertEqual(sorted(AmmitWidget._TIPS), sorted(w.tip_targets))
        self.assertIs(w.tip_targets["minutes"], w._entry)
        self.assertIs(w.tip_targets["run"], w._btn)
        self.assertEqual(w.tip_targets["reset"].cget("text"), "↺")
        shared = virgil_tooltip._shared(self.root)
        self.assertEqual(shared._tips.get(str(w.header)), AmmitWidget._HEADER_TIP)
        for key, tip in AmmitWidget._TIPS.items():
            self.assertEqual(shared._tips.get(str(w.tip_targets[key])), tip, key)


if __name__ == "__main__":
    unittest.main(verbosity=2)
