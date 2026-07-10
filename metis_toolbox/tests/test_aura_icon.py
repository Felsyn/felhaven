"""
test_aura_icon.py — unit tests for the WWO weather-code -> emoji lookup.

_icon() is module-level in panels/aura_panel.py (display formatting, not logic),
so this needs no Tk root — importing the module is enough.

    python -X utf8 -m unittest tests.test_aura_icon
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panels.aura_panel import _icon, _ICONS


class TestIcon(unittest.TestCase):
    def test_every_mapped_code_resolves(self):
        for icon, codes in _ICONS.items():
            for code in codes:
                self.assertEqual(_icon(code), icon, code)

    def test_unknown_code_falls_back(self):
        self.assertEqual(_icon(999), "🌡️")   # never KeyError

    def test_spot_checks(self):
        self.assertEqual(_icon(113), "☀️")    # Sunny
        self.assertEqual(_icon(176), "🌦️")    # Patchy rain nearby
        self.assertEqual(_icon(395), "⛈️")    # Thundery snow showers


if __name__ == "__main__":
    unittest.main(verbosity=2)
