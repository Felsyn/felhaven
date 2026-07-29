"""
test_sidebar_smoke.py — headless Tk smoke + unit tests for panels/sidebar.py.

Withdrawn tk.Tk() driven with update_idletasks() (no window, no mainloop) — the
project's standard headless-Tk approach. Covers the one row with real logic: the
Kairos-fed "aura" row's live-temperature subtitle, including the None/degraded
path (§10) and the °F/°C unit split; plus row construction, set_active, rescale.

    python -X utf8 -m unittest tests.test_sidebar_smoke
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
import themis
from panels.sidebar import Sidebar


class TestSidebarSmoke(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self._orig_unit = themis.temperature_unit
        self.clicks = []
        self.sidebar = Sidebar(self.root, on_select=self.clicks.append)
        self.root.update_idletasks()

    def tearDown(self):
        themis.temperature_unit = self._orig_unit
        self.sidebar.destroy()

    def test_one_row_per_declared_view(self):
        names = {name for name, _label, _sub in Sidebar._ROWS}
        self.assertEqual(set(self.sidebar._rows), names)

    def test_every_row_is_click_wired(self):
        # Unmapped/withdrawn widgets don't reliably receive generated pointer
        # events headless, so assert the <Button-1> binding is installed on each
        # row's label rather than dispatching a click. The binding calls
        # on_select(name) — the `n=name` default-arg capture avoids late binding.
        for name in self.sidebar._rows:
            binding = self.sidebar.row(name)._label_lbl.bind("<Button-1>")
            self.assertTrue(binding, f"{name} row label has no <Button-1> binding")

    def test_set_active_lights_one_row(self):
        self.sidebar.set_active("midas")
        active = self.sidebar.row("midas")
        inactive = self.sidebar.row("horai")
        self.assertEqual(active._dot.itemcget(active._dot_item, "fill"), theme.C["green"])
        self.assertEqual(inactive._dot.itemcget(inactive._dot_item, "fill"), theme.C["amber"])

    def test_aura_row_shows_temperature_fahrenheit(self):
        themis.temperature_unit = lambda: "F"
        self.sidebar.row("aura").update({"temp_f": 72, "temp_c": 22})
        self.assertEqual(
            self.sidebar.row("aura")._subtitle_lbl.cget("text"), "72°F Weather")

    def test_aura_row_shows_temperature_celsius(self):
        themis.temperature_unit = lambda: "C"
        self.sidebar.row("aura").update({"temp_f": 72, "temp_c": 22})
        self.assertEqual(
            self.sidebar.row("aura")._subtitle_lbl.cget("text"), "22°C Weather")

    def test_aura_row_degrades_on_none(self):
        # Kairos delivers None on a fetch failure — the row shows a dash, not a crash.
        themis.temperature_unit = lambda: "F"
        self.sidebar.row("aura").update(None)
        self.assertEqual(
            self.sidebar.row("aura")._subtitle_lbl.cget("text"), "—°F Weather")

    def test_rescale_grows_width(self):
        self.sidebar.rescale(2.0)
        self.root.update_idletasks()
        self.assertEqual(self.sidebar.cget("width"), Sidebar.BASE_WIDTH * 2)


if __name__ == "__main__":
    unittest.main()
