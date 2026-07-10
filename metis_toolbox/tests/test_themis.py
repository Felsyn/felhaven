"""
test_themis.py — unit tests for themis.py (settings load / save / validate).

The deterministic seam is _DATA_PATH: each test points it at a temp file (or a
missing one) so the real felhaven_settings.json is never read or written. Run
from the package root:

    python -X utf8 -m unittest tests.test_themis
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import themis


class ThemisBase(unittest.TestCase):
    def setUp(self):
        self._orig_path = themis._DATA_PATH
        self._dir = tempfile.mkdtemp(prefix="themis_test_")
        themis._DATA_PATH = os.path.join(self._dir, "felhaven_settings.json")

    def tearDown(self):
        themis._DATA_PATH = self._orig_path
        for f in os.listdir(self._dir):
            os.unlink(os.path.join(self._dir, f))
        os.rmdir(self._dir)

    def _write(self, obj):
        with open(themis._DATA_PATH, "w", encoding="utf-8") as f:
            if isinstance(obj, str):
                f.write(obj)
            else:
                json.dump(obj, f)


class TestLoadDefaults(ThemisBase):
    def test_missing_file_returns_defaults(self):
        # No file written — a fresh clone behaves exactly as the old hardcode.
        self.assertEqual(themis.load(), themis.DEFAULTS)
        self.assertEqual(themis.latitude(), themis.DEFAULTS["latitude"])
        self.assertEqual(themis.longitude(), themis.DEFAULTS["longitude"])

    def test_garbled_file_falls_back_to_defaults(self):
        self._write("{ not valid json")
        self.assertEqual(themis.load(), themis.DEFAULTS)

    def test_non_object_root_falls_back(self):
        self._write([1, 2, 3])
        self.assertEqual(themis.load(), themis.DEFAULTS)

    def test_partial_file_merges_over_defaults(self):
        self._write({"latitude": 10.0})
        s = themis.load()
        self.assertEqual(s["latitude"], 10.0)
        self.assertEqual(s["longitude"], themis.DEFAULTS["longitude"])   # untouched

    def test_one_bad_field_degrades_only_itself(self):
        # A junk latitude falls back to default; the good longitude survives.
        self._write({"latitude": "north", "longitude": 5.0})
        s = themis.load()
        self.assertEqual(s["latitude"], themis.DEFAULTS["latitude"])
        self.assertEqual(s["longitude"], 5.0)

    def test_out_of_range_field_degrades(self):
        self._write({"latitude": 999.0})
        self.assertEqual(themis.load()["latitude"], themis.DEFAULTS["latitude"])


class TestSaveRoundTrip(ThemisBase):
    def test_save_then_load(self):
        themis.save(latitude=-33.9, longitude=151.2, weather_location="Sydney",
                    temperature_unit="C", clock_24h=True)
        s = themis.load()
        self.assertEqual(s["latitude"], -33.9)
        self.assertEqual(s["longitude"], 151.2)
        self.assertEqual(s["weather_location"], "Sydney")
        self.assertEqual(s["temperature_unit"], "C")
        self.assertTrue(s["clock_24h"])

    def test_save_normalizes_unit_case_and_strips_location(self):
        themis.save(latitude=0.0, longitude=0.0, weather_location="  Quito  ",
                    temperature_unit="c", clock_24h=False)
        s = themis.load()
        self.assertEqual(s["temperature_unit"], "C")
        self.assertEqual(s["weather_location"], "Quito")

    def test_save_is_atomic_no_tmp_left(self):
        themis.save(latitude=1.0, longitude=2.0)
        leftovers = [f for f in os.listdir(self._dir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class TestSaveValidation(ThemisBase):
    def test_bad_latitude_raises(self):
        with self.assertRaises(themis.SettingsError):
            themis.save(latitude=91.0, longitude=0.0)

    def test_bad_longitude_raises(self):
        with self.assertRaises(themis.SettingsError):
            themis.save(latitude=0.0, longitude=-181.0)

    def test_bad_unit_raises(self):
        with self.assertRaises(themis.SettingsError):
            themis.save(latitude=0.0, longitude=0.0, temperature_unit="K")

    def test_non_numeric_latitude_raises(self):
        with self.assertRaises(themis.SettingsError):
            themis.save(latitude="north", longitude=0.0)  # type: ignore[arg-type]

    def test_failed_validation_does_not_write(self):
        with self.assertRaises(themis.SettingsError):
            themis.save(latitude=200.0, longitude=0.0)
        self.assertFalse(os.path.exists(themis._DATA_PATH))


class TestDerived(ThemisBase):
    def test_weather_query_uses_location_when_set(self):
        self._write({"latitude": 1.0, "longitude": 2.0, "weather_location": "26041"})
        self.assertEqual(themis.weather_query(), "26041")

    def test_weather_query_falls_back_to_coords(self):
        self._write({"latitude": 39.9226, "longitude": -80.7434, "weather_location": ""})
        self.assertEqual(themis.weather_query(), "39.9226,-80.7434")

    def test_is_southern(self):
        self._write({"latitude": -1.0, "longitude": 0.0})
        self.assertTrue(themis.is_southern())
        self._write({"latitude": 1.0, "longitude": 0.0})
        self.assertFalse(themis.is_southern())


if __name__ == "__main__":
    unittest.main(verbosity=2)
