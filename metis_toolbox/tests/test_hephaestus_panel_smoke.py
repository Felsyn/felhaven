"""
test_hephaestus_panel_smoke.py — headless Tk smoke test for BarMeter + VitalsPanel.

The sibling AetherWidget in the same file is covered by test_aether_widget_smoke.py
(session 5); this closes the gap it deferred ("BarMeter/VitalsPanel are session
22"). No window flashes: tk.Tk() + withdraw() + update_idletasks(), no mainloop.
Percents in the fixture are clean integers so round() has no banker's-rounding
ambiguity. Run from the package root:
    python -X utf8 -m unittest tests.test_hephaestus_panel_smoke
"""

import os
import sys
import unittest
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import theme
from panels.hephaestus_panel import BarMeter, VitalsPanel


# A hephaestus.fetch()-shaped snapshot with clean integer percents.
_VITALS = {
    "node": "TESTBOX",
    "os": "Windows",
    "cpu": {"usage_percent": 12.0, "cores": 8, "load_avg": "N/A"},
    "memory": {"total_gb": 16.0, "available_gb": 8.0, "percent_used": 50.0},
    "storage": {"total_gb": 931.0, "free_gb": 400.0, "percent_used": 57.0},
    "timestamp": "2026-07-25T00:00:00",
}


class _TkTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()          # no window flashes during the run
        theme._init_fonts(cls.root)  # FONTS is empty until a root exists

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()


class TestBarMeter(_TkTestCase):
    """set() must clamp to 0–100 and format the special sub-1% case."""

    def _meter(self):
        m = BarMeter(self.root, "CPU")
        self.root.update_idletasks()
        return m

    def test_normal_value(self):
        m = self._meter()
        m.set(50)
        self.assertEqual(m._val.cget("text"), "50%")

    def test_clamps_above_100(self):
        m = self._meter()
        m.set(150)                                   # over-range → pinned to 100
        self.assertEqual(m._val.cget("text"), "100%")

    def test_clamps_below_zero(self):
        m = self._meter()
        m.set(-10)                                   # negative → pinned to 0
        self.assertEqual(m._val.cget("text"), "0%")

    def test_tiny_nonzero_shows_lt_one(self):
        m = self._meter()
        m.set(0.5)                                   # 0 < pct < 1 → "<1%"
        self.assertEqual(m._val.cget("text"), "<1%")


class TestVitalsPanel(_TkTestCase):

    def _panel(self):
        p = VitalsPanel(self.root)                   # builds bars + embedded Aether
        self.root.update_idletasks()
        return p

    def test_constructs_without_error(self):
        self._panel()                                # __init__ must not raise

    def test_update_none_shows_unavailable(self):
        # Kairos delivers None on a stale tick; the footer says so, the bars hold.
        p = self._panel()
        p.update(None)
        self.root.update_idletasks()
        self.assertEqual(p.node_lbl.cget("text"), "unavailable")

    def test_update_drives_the_bars_and_footer(self):
        p = self._panel()
        p.update(_VITALS)
        self.root.update_idletasks()
        self.assertEqual(p.node_lbl.cget("text"), "TESTBOX · Windows")
        self.assertEqual(p.cpu._val.cget("text"), "12%")
        self.assertEqual(p.ram._val.cget("text"), "50%")
        self.assertEqual(p.disk._val.cget("text"), "57%")


if __name__ == "__main__":
    unittest.main(verbosity=2)
