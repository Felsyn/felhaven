"""
test_iris_panel_smoke.py — weather warnings on every display, from one controller.

A real, withdrawn Tk root drives AlertWatch and its displays (the NOW banner,
the ALERTS tab, Home's badge, the sidebar row, the popup) with dicts shaped as
tools/iris.py returns them. Themis reads a scratch settings file (and the
Atmospherics tests a scratch Cartograph cache) and Calliope is patched, so
nothing is spoken and no real place is read. The popup is
withdrawn as it is built: it isn't tied to the withdrawn root (so it shows
while Felhaven is minimised), and would otherwise flash on screen.

The rules under test are the ones the owner set: acknowledging turns EVERY
display amber together ("seen · still in effect"), a failed check never reads
as "none active", and a warning NWS reissues under a new id stays seen.

    python -X utf8 -m unittest tests.test_iris_panel_smoke
"""

import os
import sys
import tempfile
import tkinter as tk
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import theme
import themis
from panels import iris_panel
from panels.iris_panel import AlertBadge, AlertBanner, AlertsTab, AlertWatch, SidebarAlert
from panels.sidebar import SidebarRow

C = theme.C
T0 = datetime(2026, 11, 10, 13, 16)


def _alert(event="Winter Storm Warning", alert_id="urn:a", tier="warning", refs=()):
    return {"id": alert_id, "event": event, "tier": tier, "severity": "Severe",
            "certainty": "Likely",
            "summary": "Heavy snow expected. Total snow of 6 to 10 inches.",
            "description": "* WHAT...Heavy snow expected.\n\n* WHERE...Somewhere.",
            "instruction": "Travel only in an emergency.",
            "sent": "2026-11-10T04:12:00-06:00",
            "until": "2026-11-11T18:00:00-06:00", "references": list(refs)}


def _data(*alerts, enabled=True, covered=True):
    return {"enabled": enabled, "covered": covered, "alerts": list(alerts)}


class _Case(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.object(themis, "_DATA_PATH",
                                  Path(tmp.name) / "felhaven_settings.json")
        patch.start()
        self.addCleanup(patch.stop)
        self.voice = {}
        for name in ("stop", "speak", "end_turn"):
            p = mock.patch.object(iris_panel.calliope, name)
            self.voice[name] = p.start()
            self.addCleanup(p.stop)
        real_init = iris_panel.AlertPopup.__init__

        def built_hidden(popup, *args, **kwargs):
            real_init(popup, *args, **kwargs)
            popup.withdraw()
        p = mock.patch.object(iris_panel.AlertPopup, "__init__", built_hidden)
        p.start()
        self.addCleanup(p.stop)
        self.clock = [T0]
        self.opened = []
        self.frame = tk.Frame(self.root)
        self.frame.pack()
        self.addCleanup(self.frame.destroy)
        self.temp = tk.Label(self.frame, text="31°F")
        self.temp.pack()
        self.banner = AlertBanner(self.frame, before=self.temp)
        self.labels = []
        self.tab = AlertsTab(self.frame, on_label=lambda t, c: self.labels.append((t, c)))
        self.tab.pack()
        self.status_row = tk.Label(self.frame, text="status")
        self.status_row.pack()
        self.badge = AlertBadge(self.frame, before=self.status_row)
        self.row = SidebarRow(self.frame, "aura", "Atmospherics", "31°F Weather",
                              on_click=lambda _n: None)
        self.watch = AlertWatch(self.root, on_open=lambda: self.opened.append(True),
                                now=lambda: self.clock[0])
        for display in (self.banner, self.tab, self.badge, SidebarAlert(self.row)):
            self.watch.add_display(display)
        self.addCleanup(self.watch._close_popup)

    def settings(self, **kw):
        themis.save(latitude=39.0997, longitude=-94.5786, **kw)

    def tick(self, data, minutes=1):
        self.clock[0] += timedelta(minutes=minutes)
        self.watch.update(data)
        self.root.update_idletasks()

    def card_texts(self):
        out = []
        def walk(w):
            for child in w.winfo_children():
                if isinstance(child, tk.Label):
                    out.append(child.cget("text"))
                walk(child)
        walk(self.tab.column.body)
        return out

    def subtitle(self):
        return self.row._subtitle_lbl.cget("text"), self.row._subtitle_lbl.cget("fg")


class TestNothingActive(_Case):

    def test_before_the_first_check_it_says_so(self):
        self.assertEqual(self.tab.status.cget("text"), "not checked yet")

    def test_a_checked_empty_answer_is_none_active_and_shows_nothing_else(self):
        self.tick(_data())
        self.assertIn("none active", self.tab.status.cget("text"))
        self.assertIn("Nothing active for this location.", self.card_texts())
        self.assertFalse(self.banner.shown)
        self.assertFalse(self.badge.shown)
        self.assertEqual(self.subtitle()[0], "31°F Weather")
        self.assertIsNone(self.watch.popup)
        self.assertEqual(self.labels[-1], ("ALERTS", None))

    def test_off_and_outside_say_so(self):
        self.tick(_data(enabled=False))
        self.assertIn("off", self.tab.status.cget("text"))
        self.tick(_data(covered=False))
        self.assertIn("outside the National Weather Service's area",
                      self.tab.status.cget("text"))


class TestAWarning(_Case):

    def test_pops_up_reads_aloud_and_every_display_is_red(self):
        self.tick(_data(_alert()))
        self.assertIsNotNone(self.watch.popup)
        self.voice["speak"].assert_called_once()
        spoken = self.voice["speak"].call_args.args[0]
        self.assertTrue(spoken.startswith("Weather warning: Winter Storm Warning, until "))
        self.assertIn("Heavy snow expected", spoken)
        self.assertTrue(self.banner.shown)
        self.assertEqual(self.banner.title.cget("fg"), C["red_bright"])
        self.assertEqual(self.badge.label.cget("text"), "⚠ WINTER STORM WARNING")
        self.assertEqual(self.badge.label.cget("fg"), C["red_bright"])
        self.assertEqual(self.subtitle(), ("⚠ Storm Warning", C["red_bright"]))
        self.assertEqual(self.labels[-1], ("ALERTS · 1", C["red_bright"]))
        self.assertIn("ACKNOWLEDGE", self.card_texts())

    def test_the_banner_sits_above_the_temperature(self):
        self.tick(_data(_alert()))
        order = self.frame.pack_slaves()
        self.assertLess(order.index(self.banner), order.index(self.temp))
        order = self.frame.pack_slaves()
        self.assertLess(order.index(self.badge), order.index(self.status_row))

    def test_the_badge_packs_above_its_above_while_that_shows(self):
        """`above` is the strip it must sit over (Home's aurora line); with
        that strip hidden it packs above `before`, as before."""
        over = tk.Label(self.frame, text="aurora")
        badge = AlertBadge(self.frame, before=self.status_row, above=over)
        view = {"alerts": [{"tier": "warning", "seen": False, "event": "Tornado Warning"}],
                "trouble_s": None, "since": None}
        over.pack(before=self.status_row)
        badge.show(view)
        order = self.frame.pack_slaves()
        self.assertEqual(order.index(badge) + 1, order.index(over), "just above it")
        badge.show({"alerts": [], "trouble_s": None, "since": None})
        over.pack_forget()
        badge.show(view)
        order = self.frame.pack_slaves()
        self.assertEqual(order.index(badge) + 1, order.index(self.status_row))

    def test_one_acknowledge_turns_every_display_amber_together(self):
        self.tick(_data(_alert()))
        self.watch.acknowledge_popup()
        self.root.update_idletasks()
        self.assertIsNone(self.watch.popup)
        self.assertEqual(self.banner.title.cget("fg"), C["amber_bright"])
        self.assertIn("SEEN · STILL IN EFFECT", self.banner.note.cget("text"))
        self.assertEqual(self.badge.label.cget("text"), "⚠ WINTER STORM WARNING · seen")
        self.assertEqual(self.badge.label.cget("fg"), C["amber_bright"])
        self.assertEqual(self.subtitle(), ("⚠ Storm Warning", C["amber_bright"]))
        self.assertEqual(self.labels[-1], ("ALERTS · 1", C["amber_bright"]))
        self.assertIn("SEEN · STILL IN EFFECT", self.card_texts())
        self.assertNotIn("ACKNOWLEDGE", self.card_texts())

    def test_acknowledging_on_the_tab_closes_the_popup_too(self):
        self.tick(_data(_alert()))
        self.watch.acknowledge(["urn:a"])
        self.assertIsNone(self.watch.popup)
        self.assertEqual(self.badge.label.cget("fg"), C["amber_bright"])

    def test_closing_the_popup_acknowledges(self):
        """The window's X (and Escape, bound to the same) is "I've seen it",
        as Ammit's popup is. A withdrawn root gets no key events, so the X's
        protocol command is called the way the window manager would."""
        self.tick(_data(_alert()))
        popup = self.watch.popup
        self.assertTrue(popup.bind("<Escape>"))
        self.root.tk.call(popup.protocol("WM_DELETE_WINDOW"))
        self.assertIsNone(self.watch.popup)
        self.assertEqual(self.badge.label.cget("fg"), C["amber_bright"])

    def test_open_alerts_opens_the_tab_and_leaves_it_unseen(self):
        self.tick(_data(_alert()))
        self.watch.open_alerts()
        self.assertEqual(self.opened, [True])
        self.assertIsNone(self.watch.popup)
        self.assertEqual(self.badge.label.cget("fg"), C["red_bright"])

    def test_the_next_poll_neither_pops_up_nor_speaks_again(self):
        self.tick(_data(_alert()))
        popup = self.watch.popup
        self.tick(_data(_alert()))
        self.tick(_data(_alert()))
        self.assertIs(self.watch.popup, popup)
        self.voice["speak"].assert_called_once()

    def test_closed_with_open_alerts_it_does_not_pop_up_again(self):
        self.tick(_data(_alert()))
        self.watch.open_alerts()
        self.tick(_data(_alert()))
        self.assertIsNone(self.watch.popup)

    def test_read_aloud_off_pops_up_silently(self):
        self.settings(alerts_read_aloud=False)
        self.tick(_data(_alert()))
        self.assertIsNotNone(self.watch.popup)
        self.voice["speak"].assert_not_called()

    def test_two_at_once_are_spoken_a_and_b(self):
        self.tick(_data(_alert("Tornado Warning", "urn:t"),
                        _alert("Flood Warning", "urn:f")))
        self.assertTrue(self.voice["speak"].call_args.args[0].startswith(
            "Weather warnings: Tornado Warning and Flood Warning. "))

    def test_three_at_once_are_a_list(self):
        self.tick(_data(_alert("Tornado Warning", "urn:t"),
                        _alert("Flood Warning", "urn:f"),
                        _alert("Winter Storm Warning", "urn:w")))
        self.assertTrue(self.voice["speak"].call_args.args[0].startswith(
            "Weather warnings: Tornado Warning, Flood Warning, and Winter Storm Warning. "))

    def test_the_end_of_a_warning_clears_every_display(self):
        self.tick(_data(_alert()))
        self.row._set_subtitle("29°F Weather")       # the temperature moved meanwhile
        self.tick(_data())
        self.assertIsNone(self.watch.popup)
        self.assertFalse(self.banner.shown)
        self.assertFalse(self.badge.shown)
        self.assertEqual(self.subtitle(), ("29°F Weather", C["text3"]))


class TestThePopupStandsAlone(_Case):
    """Windows hides a window tied (`transient`) to a minimised one: measured
    2026-11-17, a tied popup opened withdrawn while its main window was
    minimised, so a warning arriving then went unseen until Felhaven was
    restored. The tests' root is withdrawn, which takes the same branch."""

    def test_it_is_not_tied_to_the_main_window(self):
        self.tick(_data(_alert()))
        self.assertEqual(str(self.watch.popup.transient()), "")

    def test_with_the_main_window_away_it_is_centred_on_the_screen(self):
        with mock.patch.object(iris_panel.AlertPopup, "geometry") as geometry:
            self.tick(_data(_alert()))
        popup = self.watch.popup
        x = (popup.winfo_screenwidth() - popup.winfo_reqwidth()) // 2
        y = (popup.winfo_screenheight() - popup.winfo_reqheight()) // 3
        geometry.assert_called_once_with(f"+{max(0, x)}+{max(0, y)}")


class TestSeenFollowsAReissue(_Case):

    def test_an_extension_under_the_same_event_stays_seen(self):
        self.tick(_data(_alert(alert_id="urn:a")))
        self.watch.acknowledge_popup()
        self.tick(_data(_alert(alert_id="urn:b", refs=("urn:a",))))
        self.assertIsNone(self.watch.popup)
        self.voice["speak"].assert_called_once()
        self.assertEqual(self.badge.label.cget("fg"), C["amber_bright"])

    def test_an_unacknowledged_extension_does_not_pop_up_twice(self):
        self.tick(_data(_alert(alert_id="urn:a")))
        self.watch.open_alerts()
        self.tick(_data(_alert(alert_id="urn:b", refs=("urn:a",))))
        self.assertIsNone(self.watch.popup)
        self.voice["speak"].assert_called_once()

    def test_once_it_has_ended_a_new_issue_pops_up_again(self):
        """Ended means forgotten: a warning issued after the last one ended is
        news, even when it names the old one."""
        self.tick(_data(_alert(alert_id="urn:a")))
        self.watch.acknowledge_popup()
        self.tick(_data())
        self.tick(_data(_alert(alert_id="urn:b", refs=("urn:a",))))
        self.assertIsNotNone(self.watch.popup)
        self.assertEqual(self.voice["speak"].call_count, 2)

    def test_an_upgrade_to_another_event_pops_up(self):
        self.settings(alerts_popup="warnings_watches")
        self.tick(_data(_alert("Winter Storm Watch", "urn:w", tier="watch")))
        self.watch.acknowledge_popup()
        self.tick(_data(_alert("Winter Storm Warning", "urn:x", refs=("urn:w",))))
        self.assertIsNotNone(self.watch.popup)
        self.assertEqual(self.voice["speak"].call_count, 2)
        self.assertEqual(self.badge.label.cget("fg"), C["red_bright"])


class TestWatchesAndAdvisories(_Case):

    def test_a_watch_is_listed_not_popped_under_warnings_only(self):
        self.tick(_data(_alert("Winter Storm Watch", "urn:w", tier="watch")))
        self.assertIsNone(self.watch.popup)
        self.assertEqual(self.banner.title.cget("fg"), C["amber_bright"])
        self.assertEqual(self.subtitle(), ("⚠ Storm Watch", C["amber_bright"]))
        self.assertTrue(any("watches don't pop up" in t for t in self.card_texts()))

    def test_a_watch_pops_up_when_settings_say_so(self):
        self.settings(alerts_popup="warnings_watches")
        self.tick(_data(_alert("Winter Storm Watch", "urn:w", tier="watch")))
        self.assertIsNotNone(self.watch.popup)
        self.assertTrue(self.voice["speak"].call_args.args[0].startswith("Weather watch: "))

    def test_an_advisory_is_only_listed(self):
        self.tick(_data(_alert("Wind Advisory", "urn:v", tier="advisory")))
        self.assertIsNone(self.watch.popup)
        self.assertFalse(self.banner.shown)
        self.assertFalse(self.badge.shown)
        self.assertEqual(self.subtitle()[0], "31°F Weather")
        self.assertEqual(self.labels[-1], ("ALERTS · 1", C["amber_bright"]))
        self.assertIn("listed only: advisories don't pop up (Settings › Alerts)",
                      self.card_texts())


class TestAFailedCheck(_Case):

    def test_after_a_good_empty_check_it_says_couldnt_check_not_none(self):
        self.tick(_data())
        self.tick(None)
        text = self.tab.status.cget("text")
        self.assertIn("couldn't check since", text)
        self.assertNotIn("none active", text)
        self.assertNotIn("Nothing active for this location.", self.card_texts())

    def test_before_any_good_check_it_says_couldnt_check_yet(self):
        self.tick(None)
        self.assertEqual(self.tab.status.cget("text"), "couldn't check yet · retrying")

    def test_the_last_list_stays_dimmed_and_the_warning_stays_up(self):
        self.tick(_data(_alert()))
        self.tick(None)
        (card,) = self.tab.cards
        self.assertEqual(card.cget("highlightbackground"), C["border"])
        self.assertEqual(self.tab.status.cget("fg"), C["amber_bright"])
        self.assertTrue(self.badge.shown)
        self.assertEqual(self.subtitle()[0], "⚠ Storm Warning")

    def test_home_mentions_it_only_after_ten_minutes(self):
        self.tick(_data())
        self.tick(None, minutes=5)
        self.assertFalse(self.badge.shown)
        self.tick(None, minutes=6)
        self.assertTrue(self.badge.shown)
        self.assertTrue(self.badge.label.cget("text").startswith(
            "weather alerts: couldn't check since "))
        self.tick(_data())
        self.assertFalse(self.badge.shown)


class TestInsideAtmospherics(_Case):
    """The banner and the tab where they live: WeatherPanel's NOW and ALERTS."""

    def setUp(self):
        super().setUp()
        from panels.aura_panel import WeatherPanel
        from tools import artemis
        # The card's PLACE dropdown lists cached Cartograph areas: an empty
        # scratch folder, never the real cache.
        cache = tempfile.TemporaryDirectory()
        self.addCleanup(cache.cleanup)
        patch = mock.patch.object(artemis, "CACHE_DIR",
                                  os.path.join(cache.name, "artemis_cache"))
        patch.start()
        self.addCleanup(patch.stop)
        self.panel = WeatherPanel(self.root)
        self.addCleanup(self.panel.destroy)
        self.atmos = AlertWatch(self.root, now=lambda: self.clock[0])
        self.atmos.add_display(self.panel.alert_banner)
        self.atmos.add_display(self.panel.alerts_tab)
        self.addCleanup(self.atmos._close_popup)

    def test_a_warning_banners_now_and_counts_on_the_tab(self):
        self.atmos.update(_data(_alert()))
        now = self.panel.temp_lbl.master.pack_slaves()
        self.assertLess(now.index(self.panel.alert_banner), now.index(self.panel.temp_lbl))
        tab = self.panel._tabs["ALERTS"]["tab"]
        self.assertEqual((tab.cget("text"), tab.cget("fg")), ("ALERTS · 1", C["red_bright"]))
        self.panel.show_tab("ALERTS")
        self.assertEqual(tab.cget("fg"), C["red_bright"])       # keeps its colour lit
        self.atmos.update(_data())
        self.assertNotIn(self.panel.alert_banner,
                         self.panel.temp_lbl.master.pack_slaves())
        self.assertEqual((tab.cget("text"), tab.cget("fg")), ("ALERTS", C["text1"]))


class TestTheSidebarRow(unittest.TestCase):
    """set_alert covers the subtitle and gives it back, current."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_override_and_restore(self):
        row = SidebarRow(self.root, "aura", "Atmospherics", "—°F Weather", lambda _n: None)
        row._set_subtitle("31°F Weather")
        row.set_alert("⚠ Flood Warning", C["red_bright"])
        row._set_subtitle("30°F Weather")
        self.assertEqual(row._subtitle_lbl.cget("text"), "⚠ Flood Warning")
        row.set_alert(None)
        self.assertEqual(row._subtitle_lbl.cget("text"), "30°F Weather")
        self.assertEqual(row._subtitle_lbl.cget("fg"), C["text3"])


if __name__ == "__main__":
    unittest.main()
