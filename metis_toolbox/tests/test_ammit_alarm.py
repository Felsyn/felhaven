"""
test_ammit_alarm.py — headless Tk smoke test for AmmitAlarm/AmmitAlarmPopup.

Covers the edge-detection + latch that turns Ammit's derived `expired` LEVEL
into a one-shot-per-expiry EVENT (popup + spoken announcement, re-announced up
to a cap while the popup stays open, silenced on dismiss). calliope.stop/
speak/end_turn are mocked so no audio device is touched. ammit.STATE_FILE is
swapped for a temp path, same as test_horai_panel_smoke.py, so nothing here
reads or writes the real timer_state.json, and Themis reads a scratch
settings file (HoraiPanel.update reads the clock format). The popup stands
alone, so it is withdrawn before it is placed, or each test would flash it.

    python -X utf8 -m unittest tests.test_ammit_alarm
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
from panels import horai_panel
from panels.horai_panel import HoraiPanel, AmmitAlarm

_SAMPLE = {
    "iso": "2026-07-24T12:00:00-04:00",
    "clock": "Friday, July 24 2026, 12:00 PM",
    "season": "Summer",
    "cycle": {"label": "Afternoon", "hour": 12},
}


def _expire_now(acknowledged=False):
    """Seed an already-expired 60 s timer directly into state."""
    past = (datetime.now().astimezone() - timedelta(minutes=2)).isoformat()
    ammit._save({"timers": [
        {"duration_seconds": 60, "started_at": past, "running": True,
         "acknowledged": acknowledged}
    ]})


def _hidden(place):
    """place_alert, withdrawing the popup first: nothing maps it, and the
    real placement still runs."""
    def hidden_place(win, *args, **kwargs):
        win.withdraw()
        return place(win, *args, **kwargs)
    return hidden_place


class _AmmitAlarmCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self._orig_state = ammit.STATE_FILE
        self._tmp = tempfile.TemporaryDirectory()
        ammit.STATE_FILE = Path(self._tmp.name) / "timer_state.json"
        for target, name, value in (
                (themis, "_DATA_PATH", Path(self._tmp.name) / "felhaven_settings.json"),
                (horai_panel, "place_alert",
                 _hidden(getattr(horai_panel, "place_alert", lambda *a, **k: None)))):
            p = mock.patch.object(target, name, value, create=True)
            p.start()
            self.addCleanup(p.stop)
        self.voice = {}
        for name in ("stop", "speak", "end_turn"):
            p = mock.patch.object(horai_panel.calliope, name)
            self.voice[name] = p.start()
            self.addCleanup(p.stop)

    def panel(self):
        """A HoraiPanel that has had its first tick, its popup closed after."""
        p = HoraiPanel(self.root)
        self.addCleanup(p.destroy)
        p.update(_SAMPLE)
        return p

    def popups(self, p):
        return [w for w in p.winfo_children()
                if isinstance(w, tk.Toplevel) and w.winfo_exists()]

    def tearDown(self):
        ammit.STATE_FILE = self._orig_state
        self._tmp.cleanup()


class TestAmmitAlarmFiring(_AmmitAlarmCase):

    def test_no_alarm_while_never_expired(self):
        ammit.start_timer(0, 300)   # 5 minutes, nowhere near expiry
        with mock.patch("panels.horai_panel.calliope.speak") as speak:
            p = HoraiPanel(self.root)
            p.update(_SAMPLE)
            self.root.update_idletasks()
        speak.assert_not_called()
        self.assertIsNone(p._alarm._popup)

    def test_fires_once_on_the_rising_edge(self):
        _expire_now()
        with mock.patch("panels.horai_panel.calliope.stop"), \
                mock.patch("panels.horai_panel.calliope.speak") as speak, \
                mock.patch("panels.horai_panel.calliope.end_turn"):
            p = HoraiPanel(self.root)
            p.update(_SAMPLE)
            self.root.update_idletasks()
        speak.assert_called_once()
        self.assertIsNotNone(p._alarm._popup)
        p._alarm._popup.destroy()

    def test_repeated_ticks_while_still_expired_do_not_refire_immediately(self):
        _expire_now()
        with mock.patch("panels.horai_panel.calliope.stop"), \
                mock.patch("panels.horai_panel.calliope.speak") as speak, \
                mock.patch("panels.horai_panel.calliope.end_turn"):
            p = HoraiPanel(self.root)
            p.update(_SAMPLE)
            for _ in range(5):
                p.update(_SAMPLE)
            self.root.update_idletasks()
        speak.assert_called_once()   # ANNOUNCE_INTERVAL_S hasn't elapsed
        p._alarm._popup.destroy()

    def test_reannounces_after_the_interval_elapses_up_to_the_cap(self):
        _expire_now()
        with mock.patch("panels.horai_panel.calliope.stop"), \
                mock.patch("panels.horai_panel.calliope.speak") as speak, \
                mock.patch("panels.horai_panel.calliope.end_turn"):
            p = HoraiPanel(self.root)
            p.update(_SAMPLE)                        # 1st announcement
            for _ in range(AmmitAlarm.MAX_ANNOUNCEMENTS + 2):
                p._alarm._last_announce -= AmmitAlarm.ANNOUNCE_INTERVAL_S
                p.update(_SAMPLE)
            self.root.update_idletasks()
        self.assertEqual(speak.call_count, AmmitAlarm.MAX_ANNOUNCEMENTS)
        p._alarm._popup.destroy()

    def test_dismiss_stops_further_reannouncements(self):
        _expire_now()
        with mock.patch("panels.horai_panel.calliope.stop"), \
                mock.patch("panels.horai_panel.calliope.speak") as speak, \
                mock.patch("panels.horai_panel.calliope.end_turn"):
            p = HoraiPanel(self.root)
            p.update(_SAMPLE)                        # 1st announcement, popup opens
            p._alarm._popup._dismiss()
            self.assertIsNone(p._alarm._popup)
            p._alarm._last_announce -= AmmitAlarm.ANNOUNCE_INTERVAL_S
            p.update(_SAMPLE)                         # still expired, but dismissed
            self.root.update_idletasks()
        speak.assert_called_once()

    def test_latch_resets_and_can_fire_again_after_stop_reset_restart(self):
        _expire_now()
        with mock.patch("panels.horai_panel.calliope.stop"), \
                mock.patch("panels.horai_panel.calliope.speak") as speak, \
                mock.patch("panels.horai_panel.calliope.end_turn"):
            p = HoraiPanel(self.root)
            p.update(_SAMPLE)
            self.assertEqual(speak.call_count, 1)
            p._alarm._popup.destroy()
            p._alarm._on_dismiss()

            ammit.reset_timer(0)
            p.update(_SAMPLE)                          # not expired now
            self.assertIsNone(p._alarm._popup)

            _expire_now()
            p.update(_SAMPLE)                          # re-armed: fires again
            self.root.update_idletasks()
        self.assertEqual(speak.call_count, 2)
        p._alarm._popup.destroy()


class TestAmmitAlarmAtLaunch(_AmmitAlarmCase):
    """The first check after a launch finds whatever the file holds. An
    expiry dismissed before the restart reads green on the countdown and
    Home's badge, so the popup stays shut too (the owner's pick, 2026-11-21);
    measured before: it popped up and spoke at every launch until Stop or
    Reset."""

    def test_an_expiry_already_acknowledged_stays_quiet_at_launch(self):
        _expire_now(acknowledged=True)
        p = self.panel()
        self.voice["speak"].assert_not_called()
        self.assertIsNone(p._alarm._popup)
        self.assertEqual(self.popups(p), [])

    def test_one_that_ran_out_while_closed_still_alarms(self):
        _expire_now(acknowledged=False)
        p = self.panel()
        self.voice["speak"].assert_called_once()
        self.assertEqual(len(self.popups(p)), 1)

    def test_after_a_quiet_launch_the_next_timer_still_alarms(self):
        _expire_now(acknowledged=True)
        p = self.panel()
        ammit.start_timer(0, 300)          # a fresh countdown re-arms
        p.update(_SAMPLE)
        _expire_now()
        p.update(_SAMPLE)
        self.voice["speak"].assert_called_once()
        self.assertEqual(len(self.popups(p)), 1)


class TestThePopupFollowsTheTimer(_AmmitAlarmCase):
    """A stop, a reset or a fresh start (from the countdown or from Pythia)
    ends the expiry, and the popup with it. Measured before: it stayed open
    saying the timer had expired, the next expiry opened a second beside it,
    and dismissing the old one acknowledged the new expiry and cut off its
    re-announcements."""

    def test_a_stop_reset_or_restart_closes_it_without_acknowledging(self):
        for name, end in (("stop", lambda: ammit.stop_timer(0)),
                          ("reset", lambda: ammit.reset_timer(0)),
                          ("restart", lambda: ammit.start_timer(0, 300))):
            with self.subTest(name):
                _expire_now()
                p = self.panel()
                popup = p._alarm._popup
                self.assertIsNotNone(popup)
                end()
                p.update(_SAMPLE)
                self.assertFalse(popup.winfo_exists())
                self.assertIsNone(p._alarm._popup)
                self.assertFalse(ammit.query_all()[0]["acknowledged"])

    def test_the_next_expiry_opens_one_popup_whose_dismissal_acknowledges(self):
        _expire_now()
        p = self.panel()
        ammit.reset_timer(0)
        p.update(_SAMPLE)
        _expire_now()
        p.update(_SAMPLE)
        popups = self.popups(p)
        self.assertEqual(len(popups), 1, "the reset left the first popup open")
        popup = popups[0]
        self.assertIs(popup, p._alarm._popup)
        popup._dismiss()
        self.assertTrue(ammit.query_all()[0]["acknowledged"])
        self.assertEqual(self.popups(p), [])


class TestThePopupStandsAlone(_AmmitAlarmCase):
    """Windows hides a window tied (`transient`) to a minimised one: measured
    for Iris's popup on 2026-11-17, a tied popup opened withdrawn while its
    main window was minimised, so a timer running out then went unseen (with
    Read aloud off, unheard too). The tests' root is withdrawn, which takes
    the minimised branch."""

    def test_it_is_not_tied_to_the_main_window(self):
        _expire_now()
        p = self.panel()
        self.assertEqual(str(p._alarm._popup.transient()), "")

    def test_with_the_main_window_away_it_is_centred_on_the_screen(self):
        _expire_now()
        with mock.patch.object(horai_panel.AmmitAlarmPopup, "geometry") as geometry:
            p = self.panel()
        popup = p._alarm._popup
        x = (popup.winfo_screenwidth() - 360) // 2
        y = (popup.winfo_screenheight() - 160) // 3
        geometry.assert_called_once_with(f"360x160+{max(0, x)}+{max(0, y)}")


class TestAmmitAlarmPopup(_AmmitAlarmCase):

    def test_dismiss_destroys_the_window_and_clears_the_owning_alarms_ref(self):
        _expire_now()
        with mock.patch("panels.horai_panel.calliope.stop"), \
                mock.patch("panels.horai_panel.calliope.speak"), \
                mock.patch("panels.horai_panel.calliope.end_turn"):
            p = HoraiPanel(self.root)
            p.update(_SAMPLE)
            self.root.update_idletasks()
            popup = p._alarm._popup
            popup._dismiss()
        self.assertFalse(popup.winfo_exists())
        self.assertIsNone(p._alarm._popup)

    def test_dismiss_acknowledges_the_timer(self):
        # The bug this pins: dismissing used to only clear the popup, so the
        # Home panel's AmmitBadge — a different panel instance that only
        # shares state through ammit.py — had no way to learn the alert had
        # been seen, and stayed red forever until the timer was reset.
        _expire_now()
        with mock.patch("panels.horai_panel.calliope.stop"), \
                mock.patch("panels.horai_panel.calliope.speak"), \
                mock.patch("panels.horai_panel.calliope.end_turn"):
            p = HoraiPanel(self.root)
            p.update(_SAMPLE)
            self.root.update_idletasks()
            self.assertFalse(ammit.query_all()[0]["acknowledged"])
            p._alarm._popup._dismiss()
        self.assertTrue(ammit.query_all()[0]["acknowledged"])

    def test_dismiss_does_not_stop_or_reset_the_timer_itself(self):
        _expire_now()
        with mock.patch("panels.horai_panel.calliope.stop"), \
                mock.patch("panels.horai_panel.calliope.speak"), \
                mock.patch("panels.horai_panel.calliope.end_turn"):
            p = HoraiPanel(self.root)
            p.update(_SAMPLE)
            self.root.update_idletasks()
            p._alarm._popup._dismiss()
        t = ammit.query_all()[0]
        self.assertTrue(t["running"])
        self.assertTrue(t["expired"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
