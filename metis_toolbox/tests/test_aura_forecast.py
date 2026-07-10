"""
test_aura_forecast.py — unit tests for aura._build() forecast shaping.

Runs against the captured real wttr.in payload (string-typed values) so the
int() casts are genuinely exercised. Aggregation correctness is asserted by
recomputing the expected max from the raw hourly strings and comparing — that
way the test can't drift from the fixture's actual numbers.

    python -m unittest tests.test_aura_forecast
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import aura
from tests._fixtures import (
    load_real_j1, two_day_payload, snow_dominant_payload, missing_hourly_payload,
)

_FORECAST_KEYS = {
    "date", "weather_code", "description", "high_f", "low_f", "high_c", "low_c",
    "rain_pct", "snow_pct",
}
# Pre-existing _build() keys that must survive the forecast addition (plus the
# Celsius counterparts the Settings unit toggle reads).
_EXISTING_KEYS = (
    "location", "country", "description", "temp_f", "temp_c", "feels_like_f",
    "feels_like_c", "high_f", "low_f", "high_c", "low_c", "humidity_pct",
    "wind_mph", "wind_label", "precip_mm", "uv_index", "astronomy",
)


class TestForecastShaping(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = load_real_j1()
        cls.out = aura._build(cls.raw)

    def test_three_entries(self):
        self.assertEqual(len(self.out["forecast"]), len(self.raw["weather"]))
        self.assertEqual(len(self.out["forecast"]), 3)

    def test_each_entry_has_nine_keys(self):
        for day in self.out["forecast"]:
            self.assertEqual(set(day.keys()), _FORECAST_KEYS)

    def test_types_are_scalars(self):
        for day in self.out["forecast"]:
            for k in ("weather_code", "high_f", "low_f", "high_c", "low_c",
                      "rain_pct", "snow_pct"):
                self.assertIsInstance(day[k], int, k)
            self.assertIsInstance(day["date"], str)
            self.assertIsInstance(day["description"], str)

    def test_rain_pct_is_true_max_over_hourly(self):
        for i, raw_day in enumerate(self.raw["weather"]):
            expected = max(int(h["chanceofrain"]) for h in raw_day["hourly"])
            self.assertEqual(self.out["forecast"][i]["rain_pct"], expected)

    def test_snow_pct_is_true_max_over_hourly(self):
        for i, raw_day in enumerate(self.raw["weather"]):
            expected = max(int(h["chanceofsnow"]) for h in raw_day["hourly"])
            self.assertEqual(self.out["forecast"][i]["snow_pct"], expected)

    def test_aggregation_is_real_not_block_0_or_4(self):
        # If a day's true max sits outside blocks 0 and 4, matching it proves
        # we aggregate rather than blindly sampling a fixed block.
        proven = False
        for i, raw_day in enumerate(self.raw["weather"]):
            rains = [int(h["chanceofrain"]) for h in raw_day["hourly"]]
            mx = max(rains)
            if mx != rains[0] and mx != rains[4]:
                self.assertEqual(self.out["forecast"][i]["rain_pct"], mx)
                proven = True
        self.assertTrue(proven, "captured payload should have a max outside blocks 0/4")

    def test_code_and_desc_from_midday_block(self):
        for i, raw_day in enumerate(self.raw["weather"]):
            mid = raw_day["hourly"][4]
            self.assertEqual(self.out["forecast"][i]["weather_code"], int(mid["weatherCode"]))
            self.assertEqual(self.out["forecast"][i]["description"],
                             mid["weatherDesc"][0]["value"])

    def test_rain_chance_pct_equals_day0_rain(self):
        self.assertEqual(self.out["rain_chance_pct"], self.out["forecast"][0]["rain_pct"])

    def test_existing_keys_survive(self):
        for key in _EXISTING_KEYS:
            self.assertIn(key, self.out)


class TestForecastDegradation(unittest.TestCase):
    def test_missing_hourly_degrades_to_zeros(self):
        out = aura._build(missing_hourly_payload())
        day = out["forecast"][1]          # the day whose hourly we removed
        self.assertEqual(day["rain_pct"], 0)
        self.assertEqual(day["snow_pct"], 0)
        self.assertEqual(day["weather_code"], 0)

    def test_snow_dominant_day(self):
        out = aura._build(snow_dominant_payload())
        day = out["forecast"][2]
        self.assertGreater(day["snow_pct"], day["rain_pct"])

    def test_fewer_than_three_days(self):
        out = aura._build(two_day_payload())
        self.assertEqual(len(out["forecast"]), 2)
        self.assertEqual(out["rain_chance_pct"], out["forecast"][0]["rain_pct"])

    def test_fewer_than_five_blocks_uses_first(self):
        # With <5 hourly blocks the representative code/desc fall back to block 0.
        raw = load_real_j1()
        full_midday = int(raw["weather"][0]["hourly"][4]["weatherCode"])
        block0 = int(raw["weather"][0]["hourly"][0]["weatherCode"])
        self.assertNotEqual(block0, full_midday)        # the fixture distinguishes them
        raw["weather"][0]["hourly"] = raw["weather"][0]["hourly"][:3]
        out = aura._build(raw)
        self.assertEqual(out["forecast"][0]["weather_code"], block0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
