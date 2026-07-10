"""
test_selene.py — unit tests for tools/selene.py (lunar interpreter).

Pure functions, no network, no Tk. Run from the package root:
    python -X utf8 -m unittest tests.test_selene
(-X utf8 keeps phase emoji and the em-dash placeholder printable on Windows.)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import selene


class TestSelene(unittest.TestCase):
    def test_all_eight_phases_map(self):
        for name, (emoji, label) in selene.PHASES.items():
            out = selene.interpret({
                "moon_phase": name, "moon_illumination": "50",
                "moonrise": "02:31 AM", "moonset": "03:18 PM",
            })
            self.assertEqual(out["emoji"], emoji, name)
            self.assertEqual(out["phase"], label, name)

    def test_unknown_phase_falls_back(self):
        out = selene.interpret({"moon_phase": "Blood Moon"})  # not a real wttr phase
        self.assertEqual(out["emoji"], "🌙")
        self.assertEqual(out["phase"], "Blood Moon")  # raw string preserved, no KeyError

    def test_no_moonrise_no_moonset(self):
        out = selene.interpret({"moonrise": "No moonrise", "moonset": "No moonset"})
        self.assertEqual(out["moonrise"], "—")
        self.assertEqual(out["moonset"], "—")

    def test_moon_times_reformatted(self):
        out = selene.interpret({"moonrise": "02:31 AM", "moonset": "03:18 PM"})
        self.assertEqual(out["moonrise"], "2:31 AM")
        self.assertEqual(out["moonset"], "3:18 PM")

    def test_illumination_percent(self):
        self.assertEqual(selene.interpret({"moon_illumination": "32"})["illumination"], "32%")

    def test_illumination_missing(self):
        # Non-empty astro that simply lacks illumination -> "" (not None, no crash).
        self.assertEqual(selene.interpret({"moon_phase": "Full Moon"})["illumination"], "")

    def test_none_and_empty(self):
        self.assertIsNone(selene.interpret(None))
        self.assertIsNone(selene.interpret({}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
