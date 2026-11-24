"""
test_hypatia_panel_smoke.py — headless Tk smoke test for HypatiaPanel.

Real tk.Tk() root, withdraw()'d so no window flashes, driven with
update_idletasks() instead of mainloop() (the test_aura_panel_smoke.py
template — no Xvfb on Windows). No exception raised == pass.

Covers: None (stale), a real fetch() payload, a selection toggle (+
deselect), a planet click (+ mutual exclusion with constellation selection,
+ surviving a refresh tick), a preset switch to north_pole (simulation
path), conditions.update() with a captured-real aura fixture and an
{"error": ...} dict, and the moon row, which rides Selene rather than Aura.

Every snapshot here reads the observer's place from Themis, so the module
runs on a scratch settings file (Themis's defaults), never this checkout's
own; the real snapshot is taken in setUpModule, once that is in place.

    python -X utf8 -m unittest tests.test_hypatia_panel_smoke
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from typing import Any
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
from tools import aura, hypatia, selene
from tests._fixtures import ScratchSettings, load_real_j1

_SETTINGS = ScratchSettings()
REAL_HYPATIA_DATA: Any = None   # set in setUpModule, on the scratch settings


def setUpModule():
    global REAL_HYPATIA_DATA
    _SETTINGS.start()
    REAL_HYPATIA_DATA = hypatia.fetch()


def tearDownModule():
    _SETTINGS.stop()


REAL_AURA_DATA = aura._build(load_real_j1())
# What Kairos's selene worker delivers (Greenwich, a day test_selene pins).
MOON = selene.compute(51.4779, 0.0, datetime(2026, 9, 28, 12, tzinfo=timezone.utc))
_WEATHER_ROWS = ("clarity", "cloud cover")


class TestHypatiaPanelSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        from panels.hypatia_panel import HypatiaPanel
        self.panel = HypatiaPanel(self.root)
        self.panel.pack()
        self.root.update_idletasks()
        # Give the canvas real geometry so projection math has a radius —
        # set directly rather than relying on a <Configure> event to have
        # been dispatched by the time the test body runs.
        self.panel._cx, self.panel._cy, self.panel._R = 150.0, 150.0, 130.0

    def tearDown(self):
        self.panel.destroy()
        hypatia._active_preset = "current"

    def test_none_shows_stale(self):
        self.panel.update(None)
        self.root.update_idletasks()
        self.assertEqual(self.panel._status_lbl.cget("text"), "stale")

    def test_real_payload_renders(self):
        self.panel.update(REAL_HYPATIA_DATA)
        self.root.update_idletasks()
        self.assertEqual(len(self.panel._list_rows), len(REAL_HYPATIA_DATA["constellations"]))
        self.assertGreater(len(self.panel._canvas.find_all()), 0)

    def test_selection_toggle_and_deselect(self):
        self.panel.update(REAL_HYPATIA_DATA)
        self.root.update_idletasks()
        abbr = REAL_HYPATIA_DATA["constellations"][0]["abbr"]
        self.panel._on_const_click(abbr)
        self.root.update_idletasks()
        self.assertEqual(self.panel._selected, abbr)
        self.assertNotEqual(self.panel._info_name_lbl.cget("text"), "")

        self.panel._on_const_click(abbr)
        self.root.update_idletasks()
        self.assertIsNone(self.panel._selected)
        self.assertEqual(self.panel._info_name_lbl.cget("text"), "")

    def test_a_cleared_selection_goes_back_to_the_neutral_colours(self):
        """A second click on a constellation, or picking a planet, clears the
        selection. Both left the highlight lit (its lines green and wide, the
        rest dimmed) until the next Kairos redraw, up to a minute away."""
        panel, c = self.panel, self.panel._canvas
        snap = hypatia._build_snapshot(1782950400.0)   # a fixed sky
        panel.update(snap)
        abbr = next(con["abbr"] for con in snap["constellations"]
                    if c.find_withtag(f"line&&{con['abbr']}"))

        def look():
            return {i: (c.itemcget(i, "fill"), c.itemcget(i, "width"))
                    for i in c.find_withtag("line") + c.find_withtag("star")}

        neutral = look()
        for clear in ("a second click", "a planet pick"):
            with self.subTest(cleared_by=clear):
                panel._on_const_click(abbr)
                self.assertNotEqual(look(), neutral, "the highlight never came on")
                if clear == "a second click":
                    panel._on_const_click(abbr)
                else:
                    panel._on_planet_click("Mars")
                self.assertIsNone(panel._selected)
                now = look()
                still_lit = [i for i in neutral if now[i] != neutral[i]]
                self.assertEqual(len(still_lit), 0,
                                 f"{len(still_lit)} canvas items kept the highlight")

    def test_planet_click_shows_info_and_excludes_constellation(self):
        self.panel.update(REAL_HYPATIA_DATA)
        self.root.update_idletasks()
        abbr = REAL_HYPATIA_DATA["constellations"][0]["abbr"]
        self.panel._on_const_click(abbr)
        self.root.update_idletasks()
        self.assertEqual(self.panel._selected, abbr)

        self.panel._on_planet_click("Mars")
        self.root.update_idletasks()
        self.assertEqual(self.panel._selected_planet, "Mars")
        self.assertIsNone(self.panel._selected)   # mutually exclusive
        self.assertIn("Mars", self.panel._info_name_lbl.cget("text"))
        self.assertIn("horizon", self.panel._info_body_lbl.cget("text"))

        # Selection survives a refresh tick, and the info box reflects the
        # fresh alt/az (not stale numbers from selection time).
        self.panel.update(hypatia.fetch())
        self.root.update_idletasks()
        self.assertEqual(self.panel._selected_planet, "Mars")
        self.assertIn("Mars", self.panel._info_name_lbl.cget("text"))

        self.panel._on_planet_click("Mars")
        self.root.update_idletasks()
        self.assertIsNone(self.panel._selected_planet)
        self.assertEqual(self.panel._info_name_lbl.cget("text"), "")

    def test_preset_switch_dims_and_shows_notice(self):
        self.panel.update(REAL_HYPATIA_DATA)
        self.root.update_idletasks()
        self.panel._on_preset_click("north_pole")
        self.root.update_idletasks()
        self.assertEqual(hypatia._active_preset, "north_pole")
        self.assertTrue(self.panel.conditions._simulated)
        self.assertIn("SIMULATED SKY", self.panel._status_lbl.cget("text"))

        self.panel._on_preset_click("current")
        self.root.update_idletasks()
        self.assertFalse(self.panel.conditions._simulated)

    def test_preset_click_on_an_empty_catalog_shows_stale(self):
        # A broken install (no star catalog): the click degrades like a failed
        # Kairos fetch instead of raising out of the Tk callback.
        self.panel.update(REAL_HYPATIA_DATA)
        self.root.update_idletasks()
        with mock.patch.object(hypatia, "_STARS", {}), \
                self.assertLogs("METIS.hypatia", "WARNING"):
            self.panel._on_preset_click("equator")
        self.root.update_idletasks()
        self.assertEqual(self.panel._status_lbl.cget("text"), "stale")

    def test_conditions_widget_real_and_error(self):
        self.panel.conditions.update(REAL_AURA_DATA)
        self.root.update_idletasks()
        self.assertNotEqual(self.panel.conditions._values["cloud cover"].cget("text"), "—")

        self.panel.conditions.update({"error": "offline"})
        self.root.update_idletasks()
        for key in _WEATHER_ROWS:
            self.assertEqual(self.panel.conditions._values[key].cget("text"), "—")

        self.panel.conditions.update(None)
        self.root.update_idletasks()
        for key in _WEATHER_ROWS:
            self.assertEqual(self.panel.conditions._values[key].cget("text"), "—")

    def test_the_moon_row_rides_selene_not_the_weather(self):
        # M3: this row showed wttr.in's number, so it went blank offline and
        # on Aura's moonless fallback, and could differ from SELENE's. It is
        # Selene's answer now, and a weather outage leaves it alone.
        c = self.panel.conditions
        c.moon_row.update(MOON)
        c.update(REAL_AURA_DATA)
        c.update({"error": "offline"})
        self.root.update_idletasks()
        self.assertEqual(c._values["moon illum."].cget("text"), MOON["illumination"])

    def test_a_moon_error_reads_dash(self):
        c = self.panel.conditions
        c.moon_row.update(MOON)
        c.moon_row.update({"error": "unavailable: moon computation raised ValueError"})
        self.root.update_idletasks()
        self.assertEqual(c._values["moon illum."].cget("text"), "—")

    def test_the_aurora_row_rides_eos_not_the_weather(self):
        """Its own rider on the eos worker, like the moon row: a weather
        update leaves it alone, and a failed space-weather check says so."""
        from tests.test_eos_panel_smoke import QUIET
        c = self.panel.conditions
        c.aurora_row.update(QUIET)
        c.update({"error": "offline"})
        self.root.update_idletasks()
        self.assertEqual(c._values["aurora"].cget("text"),
                         "unlikely · Kp 5, needs about Kp 8 (G4)")
        c.aurora_row.update(None)
        self.assertEqual(c._values["aurora"].cget("text"), "unknown · couldn't check")


class TestHypatiaTooltips(unittest.TestCase):
    """Virgil: the four presets and the clarity row (CONVENTIONS §7)."""

    setUpClass = TestHypatiaPanelSmoke.__dict__["setUpClass"]
    tearDownClass = TestHypatiaPanelSmoke.__dict__["tearDownClass"]
    setUp = TestHypatiaPanelSmoke.setUp
    tearDown = TestHypatiaPanelSmoke.tearDown

    def test_every_preset_and_the_clarity_row_carry_their_tips(self):
        from panels import virgil_tooltip
        from panels.hypatia_panel import ConditionsWidget, HypatiaPanel
        shared = virgil_tooltip._shared(self.root)
        panel = self.panel
        self.assertEqual(sorted(panel.tip_targets), sorted(panel._preset_lbls))
        self.assertEqual(sorted(HypatiaPanel._TIPS), sorted(panel.tip_targets))
        for key, tip in HypatiaPanel._TIPS.items():
            self.assertEqual(shared._tips.get(str(panel.tip_targets[key])), tip, key)
        self.assertEqual(sorted(ConditionsWidget._TIPS), sorted(panel.conditions.tip_targets))
        for key, tip in ConditionsWidget._TIPS.items():
            self.assertEqual(shared._tips.get(str(panel.conditions.tip_targets[key])), tip, key)


class TestTheConditionsStayOnScreen(unittest.TestCase):
    """A fourth conditions row (aurora) fell off the bottom at Felhaven's
    760 x 500 minimum while the star map, packed first, kept its size. Two
    fixes, each enough alone (red-checked: this fails only with both
    undone): the conditions are packed first, and the map's canvas asks for
    a token 1 x 1, so the map is what shrinks. Real
    geometry needs a mapped root: alpha 0, no frame. 530 x 410 is the view's
    card at the minimum window (2026-11-10 screenshot)."""

    def test_every_conditions_row_is_inside_the_panel_at_the_minimum(self):
        from panels.hypatia_panel import HypatiaPanel
        root = tk.Tk()
        self.addCleanup(root.destroy)
        root.attributes("-alpha", 0.0)
        root.overrideredirect(True)
        theme._init_fonts(root)
        root.geometry("530x410+40+40")
        panel = HypatiaPanel(root)
        panel.pack(fill="both", expand=True)
        root.update()
        if not panel.winfo_viewable():
            self.skipTest("no mapped window on this display")
        bottom = panel.winfo_rooty() + panel.winfo_height()
        for key, label in panel.conditions._values.items():
            with self.subTest(row=key):
                self.assertTrue(label.winfo_ismapped())
                self.assertLessEqual(label.winfo_rooty() + label.winfo_height(), bottom,
                                     f"the {key} row is cut off at the bottom")
        self.assertGreater(panel._canvas.winfo_height(), 50, "the map was squeezed out")


if __name__ == "__main__":
    unittest.main(verbosity=2)
