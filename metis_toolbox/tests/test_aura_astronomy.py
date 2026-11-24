"""
test_aura_astronomy.py — integration test: astronomy does NOT leave aura._build().

wttr.in still sends a sun-and-moon block with every reading. Until 2026-11-14
Aura passed it on as `astronomy`, and since M3 only the model read it, through
get_weather: a second sunrise beside Helios's, and empty moon fields whenever
Open-Meteo answered. The sun and moon now reach the model through
get_sky_report alone (CONVENTIONS §12), so this pins the block's absence.

The payload is a *complete* wttr.in j1 shape with the astronomy block in it
(the Midas set_summary() lesson: a fixture without the field under test proves
nothing), and the rest of _build()'s output is checked too, so the real path
ran.

No network: _build() takes the parsed dict directly.
    python -m unittest tests.test_aura_astronomy
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import aura
from tests._fixtures import ScratchSettings, load_real_open_meteo

# The fallback names no place, so its reading takes Home's location: from a
# scratch settings file, never this checkout's own.
_settings = ScratchSettings()


def setUpModule():
    _settings.start()


def tearDownModule():
    _settings.stop()


# A full j1-shaped payload — every key _build() actually touches.
J1_PAYLOAD = {
    "current_condition": [{
        "temp_F": "68",
        "temp_C": "20",
        "FeelsLikeF": "70",
        "FeelsLikeC": "21",
        "humidity": "55",
        "windspeedMiles": "8",
        "uvIndex": "4",
        "weatherDesc": [{"value": "Partly cloudy"}],
        "precipMM": "0.0",
    }],
    "nearest_area": [{
        "areaName": [{"value": "Greenwich"}],
        "country":  [{"value": "United Kingdom"}],
        "region":   [{"value": "England"}],
    }],
    "weather": [{
        "maxtempF": "75",
        "mintempF": "54",
        "maxtempC": "24",
        "mintempC": "12",
        "astronomy": [{
            "sunrise": "05:52 AM",
            "sunset": "08:45 PM",
            "moonrise": "02:31 AM",
            "moonset": "03:18 PM",
            "moon_phase": "Waning Crescent",
            "moon_illumination": "32",
        }],
    }],
}


class TestAuraAstronomy(unittest.TestCase):
    def setUp(self):
        self.out = aura._build(J1_PAYLOAD)

    def test_astronomy_is_not_passed_on(self):
        self.assertNotIn("astronomy", self.out)

    def test_no_sun_or_moon_value_leaks_anywhere(self):
        # Not under another key either: none of wttr.in's six values appears.
        text = repr(self.out)
        for value in ("05:52 AM", "08:45 PM", "02:31 AM", "03:18 PM",
                      "Waning Crescent"):
            self.assertNotIn(value, text)

    def test_the_fallback_carries_none_either(self):
        out = aura._from_open_meteo(load_real_open_meteo())
        self.assertNotIn("astronomy", out)
        self.assertNotIn("sunrise", repr(out))

    def test_rest_of_build_still_works(self):
        # Proves we exercised the real path, not an early return.
        self.assertEqual(self.out["temp_f"], 68)
        self.assertEqual(self.out["high_f"], 75)
        self.assertEqual(self.out["low_f"], 54)
        # Celsius counterparts (the Settings unit toggle reads these).
        self.assertEqual(self.out["temp_c"], 20)
        self.assertEqual(self.out["feels_like_c"], 21)
        self.assertEqual(self.out["high_c"], 24)
        self.assertEqual(self.out["low_c"], 12)
        self.assertEqual(self.out["location"], "Greenwich, England")
        self.assertEqual(self.out["description"], "Partly cloudy")


if __name__ == "__main__":
    unittest.main(verbosity=2)
