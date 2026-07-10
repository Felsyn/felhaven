"""
test_hypatia_panel_smoke.py — headless Tk smoke test for HypatiaPanel.

Real tk.Tk() root, withdraw()'d so no window flashes, driven with
update_idletasks() instead of mainloop() (the test_aura_panel_smoke.py
template — no Xvfb on Windows). No exception raised == pass.

Covers: None (stale), a real fetch() payload, a selection toggle (+
deselect), a planet click (+ mutual exclusion with constellation selection,
+ surviving a refresh tick), a preset switch to north_pole (simulation
path), and conditions.update() with a captured-real aura fixture and an
{"error": ...} dict.

    python -X utf8 -m unittest tests.test_hypatia_panel_smoke
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
from tools import aura, hypatia
from tests._fixtures import load_real_j1

REAL_HYPATIA_DATA = hypatia.fetch()
REAL_AURA_DATA = aura._build(load_real_j1())


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

    def test_conditions_widget_real_and_error(self):
        self.panel.conditions.update(REAL_AURA_DATA)
        self.root.update_idletasks()
        self.assertNotEqual(self.panel.conditions._values["cloud cover"].cget("text"), "—")

        self.panel.conditions.update({"error": "weather_offline"})
        self.root.update_idletasks()
        for v in self.panel.conditions._values.values():
            self.assertEqual(v.cget("text"), "—")

        self.panel.conditions.update(None)
        self.root.update_idletasks()
        for v in self.panel.conditions._values.values():
            self.assertEqual(v.cget("text"), "—")


if __name__ == "__main__":
    unittest.main(verbosity=2)
