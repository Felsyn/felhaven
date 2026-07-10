"""
test_helios.py — unit tests for tools/helios.py (solar timing interpreter).

Pure functions, no network, no Tk. Run from the package root:
    python -X utf8 -m unittest tests.test_helios
(-X utf8 keeps the en-dash in golden-hour ranges printable on Windows.)
"""

import os
import sys
import unittest
from datetime import time

# Make the package root importable no matter where the runner is launched.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import helios


class TestParseClock(unittest.TestCase):
    def test_am(self):
        self.assertEqual(helios.parse_clock("05:52 AM"), time(5, 52))

    def test_pm(self):
        self.assertEqual(helios.parse_clock("08:45 PM"), time(20, 45))

    def test_no_sunrise(self):
        self.assertIsNone(helios.parse_clock("No sunrise"))

    def test_no_sunset(self):
        self.assertIsNone(helios.parse_clock("No sunset"))

    def test_empty(self):
        self.assertIsNone(helios.parse_clock(""))

    def test_garbage(self):
        self.assertIsNone(helios.parse_clock("garbage"))


class TestGoldenHours(unittest.TestCase):
    def test_both_ends(self):
        g = helios.golden_hours(time(5, 52), time(20, 45))
        self.assertEqual(g["am_start"], time(5, 52))   # sunrise
        self.assertEqual(g["am_end"],   time(6, 52))   # sunrise + 1h
        self.assertEqual(g["pm_start"], time(19, 45))  # sunset  - 1h
        self.assertEqual(g["pm_end"],   time(20, 45))  # sunset


class TestDayLength(unittest.TestCase):
    def test_known(self):
        self.assertEqual(helios.day_length(time(5, 52), time(20, 45)), "14h 53m")


class TestInterpret(unittest.TestCase):
    def test_empty_dict(self):
        self.assertIsNone(helios.interpret({}))

    def test_none(self):
        self.assertIsNone(helios.interpret(None))

    def test_unparseable_sun(self):
        # "No sunrise" days must collapse to None, not a half-built dict.
        self.assertIsNone(helios.interpret({"sunrise": "No sunrise", "sunset": "08:45 PM"}))

    def test_full_sample(self):
        astro = {
            "sunrise": "05:52 AM", "sunset": "08:45 PM",
            "moonrise": "02:31 AM", "moonset": "03:18 PM",
            "moon_phase": "Waning Crescent", "moon_illumination": "32",
        }
        out = helios.interpret(astro)
        for key in ("sunrise", "sunset", "golden_am", "golden_pm", "day_length"):
            self.assertIn(key, out)
        self.assertEqual(out["sunrise"], "5:52 AM")
        self.assertEqual(out["sunset"], "8:45 PM")
        self.assertEqual(out["golden_am"], "5:52 – 6:52 AM")
        self.assertEqual(out["golden_pm"], "7:45 – 8:45 PM")
        self.assertEqual(out["day_length"], "14h 53m")


if __name__ == "__main__":
    unittest.main(verbosity=2)
