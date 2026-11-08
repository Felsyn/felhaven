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

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
from tools import aura, helios, selene
from tests._fixtures import load_real_j1, two_day_payload, snow_dominant_payload

# Realistic panel-ready dicts, built straight through aura._build (the real path).
REAL_DATA = aura._build(load_real_j1())
TWO_DAY_DATA = aura._build(two_day_payload())
SNOW_DATA = aura._build(snow_dominant_payload())

# What Kairos's helios worker delivers: the tool's own answer (Greenwich, the
# day test_helios pins against USNO).
SUN = helios.compute(51.4779, 0.0, datetime(2026, 9, 28, 12, tzinfo=timezone.utc))
_SUN_ROWS = (("sunrise", "sunrise"), ("sunset", "sunset"), ("golden AM", "golden_am"),
             ("golden PM", "golden_pm"), ("day length", "day_length"))

# ...and what the selene worker delivers. 2026-12-01 has no moonrise at
# Greenwich (USNO, test_selene), so its row must read the em dash.
MOON = selene.compute(51.4779, 0.0, datetime(2026, 9, 28, 12, tzinfo=timezone.utc))
NO_MOONRISE = selene.compute(51.4779, 0.0, datetime(2026, 12, 1, 12, tzinfo=timezone.utc))


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

    def test_the_sun_widget_shows_the_tools_answer(self):
        # One function feeds this widget and get_sun_times, so what it shows
        # is the tool's dict, field for field.
        w = self.panel.helios_widget
        w.update(SUN)
        self.root.update_idletasks()
        for row, key in _SUN_ROWS:
            self.assertEqual(w._values[row].cget("text"), SUN[key], msg=row)
        self.assertFalse(w._unavail.winfo_manager())

    def test_a_weather_outage_leaves_the_sun_alone(self):
        # M3 on screen: Aura failing (both sources down) used to blank HELIOS
        # with it. The sun is computed now, and rides its own worker.
        w = self.panel.helios_widget
        w.update(SUN)
        self.panel.update(REAL_DATA)
        self.panel.update(None)
        self.root.update_idletasks()
        self.assertEqual(w._values["sunrise"].cget("text"), SUN["sunrise"])
        self.assertFalse(w._unavail.winfo_manager(), "HELIOS went 'unavailable'")

    def test_a_polar_answer_reads_unavailable(self):
        w = self.panel.helios_widget
        w.update({"error": "sun_times_unavailable: the sun does not rise today"})
        self.root.update_idletasks()
        self.assertEqual(w._unavail.winfo_manager(), "pack")

    def test_subwidgets_still_toggle(self):
        self.panel.update(REAL_DATA)
        for widget in (self.panel.helios_widget, self.panel.selene_widget):
            widget._section_toggle()
            self.root.update_idletasks()
            self.assertFalse(widget._collapsed)
            widget._section_toggle()
            self.root.update_idletasks()
            self.assertTrue(widget._collapsed)

    def test_the_moon_widget_shows_the_tools_answer(self):
        w = self.panel.selene_widget
        w.update(MOON)
        self.root.update_idletasks()
        self.assertEqual(w._phase_lbl.cget("text"), f"{MOON['emoji']} {MOON['phase']}")
        for row in ("illumination", "moonrise", "moonset"):
            self.assertEqual(w._values[row].cget("text"), MOON[row], msg=row)
        self.assertFalse(w._unavail.winfo_manager())

    def test_a_weather_outage_leaves_the_moon_alone(self):
        # M3 on screen, and the Open-Meteo gap with it: the moon used to
        # blank whenever the weather did, and read "—" on the fallback.
        w = self.panel.selene_widget
        w.update(MOON)
        self.panel.update(REAL_DATA)
        self.panel.update(None)
        self.root.update_idletasks()
        self.assertEqual(w._values["illumination"].cget("text"), MOON["illumination"])
        self.assertFalse(w._unavail.winfo_manager(), "SELENE went 'unavailable'")

    def test_a_day_without_moonrise_shows_em_dash(self):
        self.assertEqual(NO_MOONRISE["moonrise"], "—")
        w = self.panel.selene_widget
        w.update(NO_MOONRISE)
        w._section_toggle()
        self.root.update_idletasks()
        self.assertEqual(w._values["moonrise"].cget("text"), "—")
        self.assertNotEqual(w._values["moonset"].cget("text"), "—")


if __name__ == "__main__":
    unittest.main(verbosity=2)
