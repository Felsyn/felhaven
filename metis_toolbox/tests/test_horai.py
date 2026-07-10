"""
test_horai.py — unit tests for horai.py (hemisphere seasons + clock format).

_get_season() is pure (takes an explicit `southern` flag), so it needs no
patching. handle() reads Themis for the hemisphere and clock format, so those
tests swap themis.is_southern / themis.clock_24h for stubs and restore them.

    python -X utf8 -m unittest tests.test_horai
"""

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import themis
from tools import horai


class TestSeasonHemisphere(unittest.TestCase):
    # (month, northern, southern)
    _CASES = [
        (1,  "Winter", "Summer"),
        (4,  "Spring", "Autumn"),
        (7,  "Summer", "Winter"),
        (10, "Autumn", "Spring"),
    ]

    def test_northern_and_southern_are_mirrored(self):
        for month, north, south in self._CASES:
            dt = datetime(2026, month, 15)
            self.assertEqual(horai._get_season(dt, southern=False), north, month)
            self.assertEqual(horai._get_season(dt, southern=True), south, month)


class TestHandleReadsThemis(unittest.TestCase):
    def setUp(self):
        self._orig_southern = themis.is_southern
        self._orig_clock = themis.clock_24h

    def tearDown(self):
        themis.is_southern = self._orig_southern
        themis.clock_24h = self._orig_clock

    def test_season_follows_hemisphere(self):
        now = datetime.now().astimezone()
        themis.clock_24h = lambda: False
        themis.is_southern = lambda: True
        self.assertEqual(horai.handle()["season"], horai._get_season(now, southern=True))
        themis.is_southern = lambda: False
        self.assertEqual(horai.handle()["season"], horai._get_season(now, southern=False))

    def test_clock_24h_has_no_meridiem(self):
        themis.is_southern = lambda: False
        themis.clock_24h = lambda: True
        clock = horai.handle()["clock"]
        self.assertNotIn("AM", clock)
        self.assertNotIn("PM", clock)

    def test_clock_12h_has_meridiem(self):
        themis.is_southern = lambda: False
        themis.clock_24h = lambda: False
        clock = horai.handle()["clock"]
        self.assertTrue(clock.endswith("AM") or clock.endswith("PM"), clock)


if __name__ == "__main__":
    unittest.main(verbosity=2)
