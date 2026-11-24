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
    + the PLACE dropdown (TestPlace): its list, a pick, the picked reading and
      the "stays on Home" lines.

Settings and the Artemis cache point at an empty temp folder for the whole
module (setUpModule), so no test reads this checkout's own place or areas.

    python -X utf8 -m unittest tests.test_aura_panel_smoke
"""

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
import themis
from tools import artemis, aura, helios, selene
from tests._fixtures import load_real_j1, two_day_payload, snow_dominant_payload

_saved = {}


def setUpModule():
    folder = _saved["dir"] = tempfile.mkdtemp(prefix="aura_panel_")
    _saved["settings"], _saved["cache"] = themis._DATA_PATH, artemis.CACHE_DIR
    themis._DATA_PATH = Path(folder) / "felhaven_settings.json"
    artemis.CACHE_DIR = os.path.join(folder, "artemis_cache")


def tearDownModule():
    themis._DATA_PATH, artemis.CACHE_DIR = _saved["settings"], _saved["cache"]
    shutil.rmtree(_saved["dir"], ignore_errors=True)

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
        # One function feeds this widget and get_sky_report, so what it shows
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

    def test_a_polar_answer_shows_helios_reason(self):
        # Helios writes the reason for the human to read (its §2 detail); a
        # bare "unavailable" would call a polar night a failure.
        w = self.panel.helios_widget
        for reason in (helios.NEVER_RISES, helios.NEVER_SETS):
            with self.subTest(reason=reason):
                w.update({"error": f"unavailable: {reason}"})
                self.root.update_idletasks()
                self.assertEqual(w._unavail.winfo_manager(), "pack")
                self.assertEqual(w._unavail.cget("text"), reason)

    def test_any_other_sun_failure_reads_unavailable(self):
        w = self.panel.helios_widget
        w.update({"error": f"unavailable: {helios.NEVER_SETS}"})    # a polar reason first
        for info in ({"error": "unavailable: sun computation raised ZeroDivisionError"}, None):
            with self.subTest(info=info):
                w.update(info)
                self.root.update_idletasks()
                self.assertEqual(w._unavail.winfo_manager(), "pack")
                self.assertEqual(w._unavail.cget("text"), "unavailable")

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


# Home and three cached areas, all generic public places: one ON Home (it folds
# into Home's entry), two farther out, plus a stray folder that is no area id.
_HOME = (39.0997, -94.5786)
_AREAS = (((39.1000, -94.5790), "Downtown"),       # ~0.02 mi: folds into Home
          ((39.0473, -95.6752), "Topeka"),         # ~59 mi
          ((38.9717, -95.2353), "Lawrence"))       # ~36 mi


class TestPlace(unittest.TestCase):
    """Atmospherics' PLACE dropdown and what NOW shows for a picked place."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        themis.save(latitude=_HOME[0], longitude=_HOME[1])
        self.ids = {}
        for (lat, lon), label in _AREAS:
            area = self.ids[label] = artemis.area_id(lat, lon)
            os.makedirs(artemis.area_dir(area))
            artemis.write_manifest(artemis.area_dir(area),
                                   {"center": [lat, lon], "label": label,
                                    "schema": artemis.MANIFEST_SCHEMA})
        os.makedirs(os.path.join(artemis.CACHE_DIR, "notes"))
        artemis.write_manifest(os.path.join(artemis.CACHE_DIR, "notes"),
                               {"center": [39.2, -94.6], "label": "Stray"})
        self.addCleanup(shutil.rmtree, artemis.CACHE_DIR, True)
        self.addCleanup(lambda: themis._DATA_PATH.unlink(missing_ok=True))
        from panels.aura_panel import WeatherPanel
        self.panel = WeatherPanel(self.root)
        self.panel.pack()
        self.addCleanup(self.panel.destroy)
        self.refetched = []
        self.panel.set_refetch(lambda *names: self.refetched.extend(names))

    def _picked(self, label, data=None):
        reading = dict(data or aura._build(two_day_payload()))
        reading.update({"area": self.ids[label], "name": label})
        return {**REAL_DATA, "picked": reading}

    def _note_on_top(self):
        slaves = self.panel.now_column.body.pack_slaves()
        return bool(slaves) and slaves[0] is self.panel.away_lbl

    def test_home_first_with_the_area_on_it_folded_in_then_nearest_first(self):
        self.assertEqual(self.panel.place_bar.options(),
                         [("", "Home · Downtown"),
                          (self.ids["Lawrence"], "Lawrence"),
                          (self.ids["Topeka"], "Topeka")])
        self.assertEqual(self.panel.place_bar.current(), "Home · Downtown")

    def test_a_pick_saves_at_once_refetches_aura_and_says_fetching(self):
        self.panel.place_bar._picked(self.ids["Lawrence"], "Lawrence")
        self.assertEqual(themis.atmos_place(), self.ids["Lawrence"])
        self.assertEqual(self.refetched, ["aura"])
        self.assertEqual(self.panel.desc_lbl.cget("text"), "fetching Lawrence…")
        self.panel.place_bar._picked("", "Home · Downtown")
        self.assertEqual(themis.atmos_place(), "")

    def test_the_bar_shows_the_saved_place_and_home_once_it_is_recycled(self):
        themis.save_atmos_place(self.ids["Topeka"])
        self.panel.update(REAL_DATA)
        self.assertEqual(self.panel.place_bar.current(), "Topeka")
        shutil.rmtree(artemis.area_dir(self.ids["Topeka"]))
        self.panel.place_bar.refresh()
        self.assertEqual(self.panel.place_bar.current(), "Home · Downtown")

    def test_a_picked_reading_is_what_now_shows_with_the_home_lines(self):
        data = self._picked("Lawrence")
        self.panel.update(data)
        self.root.update_idletasks()
        self.assertEqual(self.panel._header_lbl.cget("text"), "ATMOSPHERICS — LAWRENCE")
        self.assertEqual(self.panel.desc_lbl.cget("text"), data["picked"]["description"])
        self.assertTrue(self.panel.temp_lbl.cget("text").startswith(
            f"{data['picked']['temp_f']}°"))
        self.assertTrue(self._note_on_top())
        self.assertEqual(self.panel.away_lbl.cget("text"),
                         "Warnings, sun, moon and space weather stay on Home.")
        self.assertTrue(self.panel.alerts_away.winfo_manager())
        self.panel.update(REAL_DATA)                       # back on Home
        self.root.update_idletasks()
        self.assertFalse(self.panel.away_lbl.winfo_manager())
        self.assertFalse(self.panel.alerts_away.winfo_manager())
        self.assertIn(REAL_DATA["location"].upper(), self.panel._header_lbl.cget("text"))

    def test_the_home_line_sits_above_a_warning_banner_shown_first(self):
        self.panel.alert_banner.pack(fill="x", before=self.panel.temp_lbl)
        self.panel.update(self._picked("Topeka"))
        self.assertTrue(self._note_on_top())

    def test_a_picked_failure_keeps_that_places_reading_never_homes(self):
        data = self._picked("Lawrence")
        self.panel.update(data)
        temp = self.panel.temp_lbl.cget("text")
        failed = {**REAL_DATA, "picked": {"area": self.ids["Lawrence"],
                                          "name": "Lawrence", "failed": True}}
        self.panel.update(failed)
        self.assertEqual(self.panel.temp_lbl.cget("text"), temp)
        self.assertEqual(self.panel.desc_lbl.cget("text"),
                         "couldn't reach the weather for Lawrence — "
                         "retrying at the next check")
        self.assertTrue(self._note_on_top())
        # A place NOW wasn't showing yet: "unavailable", never Home's numbers.
        failed["picked"] = {"area": self.ids["Topeka"], "name": "Topeka", "failed": True}
        self.panel.update(failed)
        self.assertEqual(self.panel.temp_lbl.cget("text"), "unavailable")
        self.assertEqual(self.panel._detail_labels["feels like"].cget("text"), "—")
        self.assertEqual(self.panel._header_lbl.cget("text"), "ATMOSPHERICS — TOPEKA")

    def test_a_recycled_pick_shows_home_and_says_so(self):
        self.panel.update({**REAL_DATA, "picked": {"area": self.ids["Topeka"],
                                                   "missing": True}})
        self.root.update_idletasks()
        self.assertTrue(self._note_on_top())
        self.assertIn("no longer cached", self.panel.away_lbl.cget("text"))
        self.assertFalse(self.panel.alerts_away.winfo_manager())
        self.assertEqual(self.panel.desc_lbl.cget("text"), REAL_DATA["description"])


class TestThePlaceStaysOnScreen(unittest.TestCase):
    """At the width NOW gets in Felhaven's 760 x 500 minimum window, a long
    place name in the header is clipped and the PLACE dropdown is not. Mapped
    root, alpha 0, no frame."""

    def test_a_long_header_never_squeezes_the_dropdown(self):
        from panels.aura_panel import WeatherPanel
        root = tk.Tk()
        self.addCleanup(root.destroy)
        root.attributes("-alpha", 0.0)
        root.overrideredirect(True)
        theme._init_fonts(root)
        root.geometry("530x410+40+40")
        panel = WeatherPanel(root)
        panel.pack(fill="both", expand=True)
        panel.set_header("Atmospherics — " + "a very long place name, " * 3)
        root.update()
        if not panel.winfo_viewable():
            self.skipTest("no mapped window on this display")
        bar = panel.place_bar
        self.assertGreater(panel._header_lbl.winfo_reqwidth(), panel.header.winfo_width(),
                           "the header fits — the case would be vacuous")
        self.assertGreaterEqual(bar.winfo_width(), bar.winfo_reqwidth(), "squeezed")
        self.assertLessEqual(bar.winfo_rootx() + bar.winfo_width(),
                             panel.header.winfo_rootx() + panel.header.winfo_width())


if __name__ == "__main__":
    unittest.main(verbosity=2)
