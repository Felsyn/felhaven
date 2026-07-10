"""
test_aura_panel_smoke.py — headless Tk smoke test for WeatherPanel.

The handoff specced this under Xvfb; this project runs on Windows, where Tk uses
the desktop display directly and there is no Xvfb. The equivalent is a real
tk.Tk() root that we withdraw() (so no window flashes) and drive with
update_idletasks() instead of mainloop(). No exception raised == pass.

Covers the tab restructure plus the forecast paths:
    (a) full data including a 3-day forecast      (captured real payload)
    (b) None  (fetch-failed path; forecast rows must keep their last values)
    (c) a 2-entry forecast (missing third row blanks)
    (d) a day where snow_pct > rain_pct
    + tab switching both directions, and Helios/Selene toggles still work.

    python -X utf8 -m unittest tests.test_aura_panel_smoke
"""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
from tools import aura
from tests._fixtures import load_real_j1, two_day_payload, snow_dominant_payload
from tests.test_aura_astronomy import J1_PAYLOAD

# Realistic panel-ready dicts, built straight through aura._build (the real path).
REAL_DATA = aura._build(load_real_j1())
TWO_DAY_DATA = aura._build(two_day_payload())
SNOW_DATA = aura._build(snow_dominant_payload())

_nm = copy.deepcopy(J1_PAYLOAD)
_nm["weather"][0]["astronomy"][0]["moonrise"] = "No moonrise"
_nm["weather"][0]["astronomy"][0]["moonset"] = "No moonset"
NO_MOON_DATA = aura._build(_nm)


class TestWeatherPanelSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        from panels.aura_panel import WeatherPanel
        self.panel = WeatherPanel(self.root)
        self.panel.pack()

    def tearDown(self):
        self.panel.destroy()

    def _switch_tabs(self):
        self.panel._show_tab("FORECAST")
        self.root.update_idletasks()
        self.assertEqual(self.panel._active, "FORECAST")
        self.panel._show_tab("NOW")
        self.root.update_idletasks()
        self.assertEqual(self.panel._active, "NOW")

    def test_full_then_none_then_two_then_snow(self):
        for data in (REAL_DATA, None, TWO_DAY_DATA, SNOW_DATA):
            self.panel.update(data)
            self.root.update_idletasks()
            self._switch_tabs()

    def test_starts_on_now_tab(self):
        self.assertEqual(self.panel._active, "NOW")

    def test_none_keeps_forecast_rows(self):
        self.panel.update(REAL_DATA)
        self.root.update_idletasks()
        before = self.panel._forecast_rows[0]["day"].cget("text")
        self.panel.update(None)               # fetch failed
        self.root.update_idletasks()
        after = self.panel._forecast_rows[0]["day"].cget("text")
        self.assertEqual(before, after)       # untouched, not blanked
        self.assertEqual(after, "Today")

    def test_two_entry_blanks_third_row(self):
        self.panel.update(TWO_DAY_DATA)
        self.root.update_idletasks()
        self.assertEqual(self.panel._forecast_rows[2]["day"].cget("text"), "—")

    def test_snow_row_shows_snow(self):
        self.panel.update(SNOW_DATA)
        self.root.update_idletasks()
        self.assertTrue(
            self.panel._forecast_rows[2]["precip"].cget("text").startswith("snow ")
        )

    def test_rain_chance_row_present(self):
        self.panel.update(REAL_DATA)
        self.root.update_idletasks()
        self.assertIn("rain chance", self.panel._detail_labels)
        self.assertTrue(
            self.panel._detail_labels["rain chance"].cget("text").endswith("%")
        )

    def test_subwidgets_still_toggle(self):
        self.panel.update(REAL_DATA)
        for widget in (self.panel._helios, self.panel._selene):
            widget._section_toggle()
            self.root.update_idletasks()
            self.assertFalse(widget._collapsed)
            widget._section_toggle()
            self.root.update_idletasks()
            self.assertTrue(widget._collapsed)

    def test_no_moon_shows_em_dash(self):
        self.panel.update(NO_MOON_DATA)
        self.panel._selene._section_toggle()
        self.root.update_idletasks()
        self.assertEqual(self.panel._selene._values["moonrise"].cget("text"), "—")
        self.assertEqual(self.panel._selene._values["moonset"].cget("text"), "—")


if __name__ == "__main__":
    unittest.main(verbosity=2)
