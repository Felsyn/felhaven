"""
test_aura_temp_color.py — unit tests for the NOW-temperature color tiers.

_temp_color() is module-level in panels/aura_panel.py (display formatting, not
logic), so this needs no Tk root — importing the module is enough. Mirrors
test_aura_icon.py.

    python -X utf8 -m unittest tests.test_aura_temp_color
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from theme import C
from panels.aura_panel import (
    _temp_color, _HOT_ALARM_F, _HOT_WARN_F, _COLD_WARN_F, _COLD_ALARM_F,
)


class TestTempColor(unittest.TestCase):
    def test_comfortable_band_is_normal(self):
        for t in (41, 50, 68, 72, 79):
            self.assertEqual(_temp_color(t), C["text1"], t)

    def test_hot_warn_band_is_amber(self):
        # [80, 94] inclusive is amber; 95 crosses into alarm.
        self.assertEqual(_temp_color(_HOT_WARN_F), C["amber"])          # 80
        self.assertEqual(_temp_color(_HOT_ALARM_F - 1), C["amber"])     # 94
        self.assertEqual(_temp_color(_HOT_WARN_F - 1), C["text1"])      # 79

    def test_hot_alarm_band_is_red(self):
        self.assertEqual(_temp_color(_HOT_ALARM_F), C["red"])           # 95
        self.assertEqual(_temp_color(_HOT_ALARM_F + 20), C["red"])      # 115

    def test_cold_warn_band_is_amber(self):
        # [21, 40] inclusive is amber; 20 crosses into alarm.
        self.assertEqual(_temp_color(_COLD_WARN_F), C["amber"])         # 40
        self.assertEqual(_temp_color(_COLD_ALARM_F + 1), C["amber"])    # 21
        self.assertEqual(_temp_color(_COLD_WARN_F + 1), C["text1"])     # 41

    def test_cold_alarm_band_is_red(self):
        self.assertEqual(_temp_color(_COLD_ALARM_F), C["red"])          # 20
        self.assertEqual(_temp_color(_COLD_ALARM_F - 20), C["red"])     # 0 / below


if __name__ == "__main__":
    unittest.main(verbosity=2)
