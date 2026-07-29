"""
test_felhaven.py — unit tests for the isolable pieces of felhaven.py.

FelhavenApp itself is the integration shell (it builds every panel, Kairos, and
the prewarm threads); its wiring is covered by test_kairos plus the panel smoke
tests. What is unit-testable in isolation is _ClockCluster, the header clock
registered with Kairos under "horai" — its 12h/24h rendering (driven by
themis.clock_24h) and the AM/PM mark logic. Withdrawn tk.Tk(), no mainloop.

    python -X utf8 -m unittest tests.test_felhaven
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import felhaven
import theme
import themis


class TestClockCluster(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self._orig = themis.clock_24h
        self.clock = felhaven._ClockCluster(self.root)

    def tearDown(self):
        themis.clock_24h = self._orig
        self.clock.destroy()

    def test_24h_blanks_the_ampm_stack(self):
        themis.clock_24h = lambda: True
        self.clock.update(None)
        # 24-hour mode has no AM/PM meaning: the marks are blank spacers.
        self.assertEqual(self.clock._am.cget("text").strip(), "")
        self.assertEqual(self.clock._pm.cget("text").strip(), "")
        self.assertIn(":", self.clock._time_lbl.cget("text"))   # "%H:%M"

    def test_12h_lights_exactly_one_mark(self):
        themis.clock_24h = lambda: False
        self.clock.update(None)
        marks = (self.clock._am.cget("text"), self.clock._pm.cget("text"))
        # Exactly one of AM/PM is bracketed [..]; the other is a padded spacer.
        bracketed = [m for m in marks if m.startswith("[") and m.endswith("]")]
        self.assertEqual(len(bracketed), 1)


if __name__ == "__main__":
    unittest.main()
