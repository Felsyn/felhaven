"""
test_themis_panel_smoke.py — headless Tk smoke test for ThemisPanel.

Same approach as test_cerberus_panel_smoke.py: a withdrawn tk.Tk() root driven
with update_idletasks() (no window flashes, no mainloop). themis._DATA_PATH is
redirected at a temp file so the real felhaven_settings.json is never touched.
No exception raised == pass.

    python -X utf8 -m unittest tests.test_themis_panel_smoke
"""

import ast
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
import themis
from kairos import Kairos
from panels.themis_panel import ThemisPanel

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _workers_reading_themis():
    """Kairos workers whose module imports themis: each computes from the
    Settings, so a Save has to refetch it or the edit sits unseen for a whole
    interval. Derived, not listed, so a new worker can't be forgotten (helios
    joined 2026-09-28)."""
    names = set()
    for name, _interval, dotted in Kairos.WORKERS:
        module_path = dotted.rpartition(".")[0].replace(".", os.sep) + ".py"
        with open(os.path.join(_APP_ROOT, module_path), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        if any(isinstance(node, ast.Import) and any(a.name == "themis" for a in node.names)
               for node in tree.body):
            names.add(name)
    return names


class TestThemisPanelSmoke(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self._orig = themis._DATA_PATH
        self._dir = tempfile.mkdtemp(prefix="themis_panel_")
        themis._DATA_PATH = os.path.join(self._dir, "felhaven_settings.json")

    def tearDown(self):
        themis._DATA_PATH = self._orig
        for f in os.listdir(self._dir):
            os.unlink(os.path.join(self._dir, f))
        os.rmdir(self._dir)

    def test_construct_and_save_valid_nudges_workers(self):
        panel = ThemisPanel(self.root)
        self.root.update_idletasks()
        calls = []
        panel.set_refetch(lambda *names: calls.append(names))

        panel._lat.delete(0, "end"); panel._lat.insert(0, "-33.9")
        panel._lon.delete(0, "end"); panel._lon.insert(0, "151.2")
        panel._loc.delete(0, "end"); panel._loc.insert(0, "Sydney")
        panel._unit._select("C")
        panel._clock._select(True)
        panel._on_save()
        self.root.update_idletasks()

        s = themis.load()
        self.assertEqual(s["latitude"], -33.9)
        self.assertEqual(s["temperature_unit"], "C")
        self.assertTrue(s["clock_24h"])
        self.assertEqual(s["weather_location"], "Sydney")
        self.assertEqual(len(calls), 1)
        self.assertEqual(set(calls[0]), _workers_reading_themis())

    def test_non_numeric_coord_shows_error_and_no_write(self):
        panel = ThemisPanel(self.root)
        self.root.update_idletasks()
        panel._lat.delete(0, "end"); panel._lat.insert(0, "not-a-number")
        panel._on_save()
        self.root.update_idletasks()
        self.assertFalse(os.path.exists(themis._DATA_PATH))

    def test_out_of_range_shows_error_and_no_write(self):
        panel = ThemisPanel(self.root)
        self.root.update_idletasks()
        panel._lat.delete(0, "end"); panel._lat.insert(0, "200")
        panel._on_save()
        self.root.update_idletasks()
        self.assertFalse(os.path.exists(themis._DATA_PATH))


if __name__ == "__main__":
    unittest.main(verbosity=2)
