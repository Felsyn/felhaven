"""
test_eos_panel_smoke.py — space weather's three displays.

A withdrawn Tk root drives SpaceWeatherWidget (NOW's fold row), AuroraRow
(Celestarium) and AuroraNotice (Home) with dicts shaped as tools/eos.py
returns them; Themis reads a scratch settings file, and the one test that
builds the whole Atmospherics card a scratch Cartograph cache too.

    python -X utf8 -m unittest tests.test_eos_panel_smoke
"""

import copy
import os
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import theme
import themis
from panels.eos_panel import AuroraNotice, AuroraRow, SpaceWeatherWidget
from tools import artemis

C = theme.C

QUIET = {
    "now": {"R": {"level": 0, "text": "none"}, "S": {"level": 0, "text": "none"},
            "G": {"level": 1, "text": "minor"}},
    "kp": 5.0,
    "days": [{"date": "2026-11-10", "r_minor": 60, "g": 0},
             {"date": "2026-11-11", "r_minor": 55, "g": 1},
             {"date": "2026-11-12", "r_minor": 50, "g": 0}],
    "aurora": {"mlat": 47.5, "kp_needed": 8, "g_needed": 4, "now": False,
               "next_24h": False, "kp_max_24h": 5.33},
}


def _with(**aurora):
    data = copy.deepcopy(QUIET)
    data["aurora"].update(aurora)
    return data


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
        patch = mock.patch.object(themis, "_DATA_PATH", Path(tmp.name) / "s.json")
        patch.start()
        self.addCleanup(patch.stop)
        self.frame = tk.Frame(self.root)
        self.frame.pack()
        self.addCleanup(self.frame.destroy)


class TestTheFoldRow(_Case):

    def setUp(self):
        super().setUp()
        self.w = SpaceWeatherWidget(self.frame)
        self.w.pack()

    def test_starts_folded_and_toggles(self):
        self.assertTrue(self.w.collapsed)
        self.w.fold()
        self.assertFalse(self.w.collapsed)
        self.assertEqual(self.w.toggle.cget("text"), "▼")
        self.w.fold()
        self.assertTrue(self.w.collapsed)

    def test_the_header_and_rows_read_the_scales(self):
        self.w.update(QUIET)
        self.assertEqual(self.w.summary.cget("text"), "R0 · S0 · G1")
        self.assertEqual(self.w.values["G"].cget("text"), "G1 minor · Kp 5")
        self.assertEqual(self.w.values["R"].cget("text"), "R0 none")
        self.assertEqual(self.w.outlook.cget("text"),
                         "next 3 days: G1 expected Wed · flare chance (R1+) up to 60%")
        self.assertEqual(self.w.aurora.cget("text"),
                         "aurora here: unlikely · Kp 5, needs about Kp 8 (G4)")
        self.assertTrue(self.w.source.cget("text").startswith(
            "NOAA Space Weather Prediction Center · checked "))
        self.assertEqual(self.w.title.cget("fg"), C["text3"])

    def test_a_strong_storm_lights_the_header(self):
        data = copy.deepcopy(QUIET)
        data["now"]["G"] = {"level": 3, "text": "strong"}
        self.w.update(data)
        self.assertEqual(self.w.title.cget("fg"), C["amber"])
        self.assertEqual(self.w.values["G"].cget("fg"), C["amber_bright"])

    def test_a_failed_check_keeps_the_reading_and_says_so(self):
        self.w.update(QUIET)
        self.w.update(None)
        self.assertTrue(self.w.source.cget("text").startswith("couldn't check since "))
        self.assertEqual(self.w.source.cget("fg"), C["amber_bright"])

    def test_a_later_failure_shows_on_the_folded_row(self):
        """Folded, the summary is all there is: a reading that's gone stale
        has to look different from a fresh one there, not just in the body."""
        self.w.update(QUIET)
        self.w.update(None)
        self.assertTrue(self.w.collapsed)
        self.assertEqual(self.w.summary.cget("text"), "R0 · S0 · G1 · out of date")
        self.assertEqual(self.w.summary.cget("fg"), C["amber"])

    def test_a_good_check_clears_the_out_of_date_mark(self):
        self.w.update(QUIET)
        self.w.update(None)
        self.w.update(QUIET)
        self.assertEqual(self.w.summary.cget("text"), "R0 · S0 · G1")
        self.assertEqual(self.w.summary.cget("fg"), C["text2"])
        self.assertTrue(self.w.source.cget("text").startswith("NOAA "))

    def test_a_failure_before_any_reading_never_looks_quiet(self):
        self.w.update(None)
        self.assertEqual(self.w.summary.cget("text"), "couldn't check")


class TestTheAuroraRow(_Case):

    def setUp(self):
        super().setUp()
        self.label = tk.Label(self.frame)
        self.row = AuroraRow(self.label)

    def test_its_states(self):
        cases = [
            (QUIET, "unlikely · Kp 5, needs about Kp 8 (G4)", C["text3"]),
            (_with(next_24h=True, kp_max_24h=8.33),
             "possible in the next 24 h, low in the north · Kp 8.3 forecast", C["amber_bright"]),
            (_with(now=True, next_24h=True), "possible now, low in the north · Kp 5",
             C["amber_bright"]),
            (_with(mlat=-49.5, now=True), "possible now, low in the south · Kp 5",
             C["amber_bright"]),
            (_with(kp_needed=0, now=True), "in the auroral zone · Kp 5", C["text1"]),
            (_with(kp_needed=14, g_needed=None), "out of reach here · Kp 5", C["text3"]),
            (None, "unknown · couldn't check", C["text3"]),
        ]
        for data, text, colour in cases:
            with self.subTest(text=text):
                self.row.update(data)
                self.assertEqual((self.label.cget("text"), self.label.cget("fg")),
                                 (text, colour))


class TestTheHomeNotice(_Case):

    def setUp(self):
        super().setUp()
        self.status = tk.Label(self.frame, text="status")
        self.status.pack()
        self.notice = AuroraNotice(self.frame, before=self.status)

    def test_hidden_with_panel_only_even_when_possible(self):
        self.notice.update(_with(now=True, next_24h=True))
        self.assertFalse(self.notice.shown)

    def test_shown_with_the_setting_and_a_chance_then_gone(self):
        themis.save(latitude=1.0, longitude=2.0, space_aurora_notice=True)
        self.notice.update(QUIET)
        self.assertFalse(self.notice.shown)
        self.notice.update(_with(next_24h=True, kp_max_24h=8.33))
        self.assertTrue(self.notice.shown)
        self.assertEqual(self.notice.label.cget("text"),
                         "✦ aurora possible here in the next 24 h, low in the north · "
                         "Kp 8.3 forecast")
        self.assertEqual(self.notice.label.cget("fg"), C["teal"])
        order = self.frame.pack_slaves()
        self.assertLess(order.index(self.notice), order.index(self.status))
        self.notice.update(None)
        self.assertFalse(self.notice.shown)


class TestNowScrollsAtTheMinimum(unittest.TestCase):
    """With Helios, Selene and space weather open, NOW is taller than the tab
    at Felhaven's 760 x 500 minimum; it sits in a ScrollColumn so the last
    rows can be reached. Mapped root, alpha 0, no frame. Themis and the
    Cartograph cache (the PLACE dropdown's areas) point at a scratch folder."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for target, name, value in (
                (themis, "_DATA_PATH", Path(tmp.name) / "s.json"),
                (artemis, "CACHE_DIR", os.path.join(tmp.name, "artemis_cache"))):
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def test_the_open_folds_overflow_and_the_lane_is_there(self):
        from panels.aura_panel import WeatherPanel
        root = tk.Tk()
        self.addCleanup(root.destroy)
        root.attributes("-alpha", 0.0)
        root.overrideredirect(True)
        theme._init_fonts(root)
        root.geometry("530x410+40+40")
        panel = WeatherPanel(root)
        panel.pack(fill="both", expand=True)
        for fold in (panel.helios_widget._section_toggle, panel.selene_widget._section_toggle,
                     panel.space_widget.fold):
            fold()
        panel.space_widget.update(QUIET)
        root.update()
        if not panel.winfo_viewable():
            self.skipTest("no mapped window on this display")
        _first, last = panel.now_column._canvas.yview()
        self.assertLess(last, 1.0, "NOW fits — the case would be vacuous")
        self.assertTrue(panel.now_column._scroll.winfo_ismapped())
        panel.now_column._canvas.yview_moveto(1.0)
        root.update()
        source = panel.space_widget.source
        self.assertLessEqual(source.winfo_rooty() + source.winfo_height(),
                             panel.winfo_rooty() + panel.winfo_height(),
                             "the last row can't be scrolled into view")


if __name__ == "__main__":
    unittest.main()
