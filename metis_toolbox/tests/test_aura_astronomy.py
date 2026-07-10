"""
test_aura_astronomy.py — integration test: astronomy survives aura._build().

Feeds aura._build() a *complete* captured wttr.in j1 payload (the real code
path, not a stripped one). The Midas set_summary() lesson: a fixture that skips
the fields under test proves nothing — so this payload carries everything
_build() reads (current_condition, nearest_area, weather + astronomy), and we
assert both that the new astronomy block is surfaced intact AND that the rest
of _build()'s output still works.

No network: _build() takes the parsed dict directly.
    python -m unittest tests.test_aura_astronomy
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import aura


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
        "areaName": [{"value": "Moundsville"}],
        "country":  [{"value": "United States of America"}],
        "region":   [{"value": "West Virginia"}],
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

    def test_astronomy_key_present(self):
        self.assertIn("astronomy", self.out)

    def test_all_six_astronomy_fields_survive(self):
        a = self.out["astronomy"]
        self.assertEqual(a["sunrise"], "05:52 AM")
        self.assertEqual(a["sunset"], "08:45 PM")
        self.assertEqual(a["moonrise"], "02:31 AM")
        self.assertEqual(a["moonset"], "03:18 PM")
        self.assertEqual(a["moon_phase"], "Waning Crescent")
        self.assertEqual(a["moon_illumination"], "32")

    def test_rest_of_build_still_works(self):
        # Proves we exercised the real path, not just the astronomy branch.
        self.assertEqual(self.out["temp_f"], 68)
        self.assertEqual(self.out["high_f"], 75)
        self.assertEqual(self.out["low_f"], 54)
        # Celsius counterparts (the Settings unit toggle reads these).
        self.assertEqual(self.out["temp_c"], 20)
        self.assertEqual(self.out["feels_like_c"], 21)
        self.assertEqual(self.out["high_c"], 24)
        self.assertEqual(self.out["low_c"], 12)
        self.assertEqual(self.out["location"], "Moundsville, West Virginia")
        self.assertEqual(self.out["description"], "Partly cloudy")

    def test_astronomy_values_are_raw_strings(self):
        # Aura fetches; it does not interpret. Parsing belongs downstream.
        a = self.out["astronomy"]
        for v in a.values():
            self.assertIsInstance(v, str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
