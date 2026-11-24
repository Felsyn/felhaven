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
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
import themis
from theme import C
from kairos import Kairos
from panels.themis_panel import ThemisPanel
from tools import artemis

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
        # The PLACE row lists the Artemis cache: an empty one, never this
        # checkout's own areas.
        self._orig_cache = artemis.CACHE_DIR
        artemis.CACHE_DIR = os.path.join(self._dir, "no_cache")

    def tearDown(self):
        themis._DATA_PATH = self._orig
        artemis.CACHE_DIR = self._orig_cache
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

    def test_the_atmospherics_place_saves_when_picked_never_on_save(self):
        """The PLACE row is Atmospherics' own setting: a pick saves at once and
        refetches aura, and SAVE (a form built before that pick) keeps it."""
        area = "n39p100_w094p579"
        panel = ThemisPanel(self.root)
        calls = []
        panel.set_refetch(lambda *names: calls.append(names))
        self.assertEqual(panel.place_bar.current(), "Home")
        panel.place_bar._picked(area, "Somewhere")
        self.assertEqual(themis.atmos_place(), area)
        self.assertEqual(calls, [("aura",)])
        panel._on_save()
        self.assertEqual(themis.atmos_place(), area)

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

    def test_a_failed_write_shows_its_kind_in_red_never_its_path(self):
        """An OSError escaped SAVE's callback: the status line kept what it
        said, and under pythonw nothing showed at all (measured 2026-11-21)."""
        panel = ThemisPanel(self.root)
        calls = []
        panel.set_refetch(lambda *names: calls.append(names))
        failure = PermissionError(13, "Permission denied",
                                  r"C:\Users\someone\felhaven_settings.json")
        with mock.patch.object(themis, "_write", side_effect=failure):
            try:
                panel._on_save()
            except OSError as exc:
                self.fail(f"SAVE let {type(exc).__name__} escape its callback")
        text = panel._status.cget("text")
        self.assertEqual(text, "Not saved — the file could not be written "
                               "(PermissionError).")
        self.assertEqual(panel._status.cget("fg"), C["red"])
        self.assertEqual(calls, [], "a failed save must not refetch")

    def test_a_refused_value_shows_themis_own_words(self):
        panel = ThemisPanel(self.root)
        panel._lat.delete(0, "end"); panel._lat.insert(0, "200")
        panel._on_save()
        self.assertTrue(panel._status.cget("text").startswith("Not saved — latitude"))
        self.assertEqual(panel._status.cget("fg"), C["red"])

    def test_the_failure_words_match_machine_spirit(self):
        """Both tabs' _not_saved say the same thing; compared by AST so the
        test needn't import MACHINE SPIRIT (and Pythia behind it)."""
        bodies = []
        for name in ("themis_panel.py", "machine_spirit_panel.py"):
            with open(os.path.join(_APP_ROOT, "panels", name), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            func = next((node for node in tree.body
                         if isinstance(node, ast.FunctionDef) and node.name == "_not_saved"),
                        None)
            self.assertIsNotNone(func, f"{name} has no _not_saved")
            bodies.append([ast.dump(stmt) for stmt in func.body[1:]])   # past the docstring
        self.assertEqual(bodies[0], bodies[1])

    def test_the_alerts_section_starts_from_and_saves_the_settings(self):
        themis.save(latitude=1.0, longitude=2.0, alerts_popup="warnings_watches")
        panel = ThemisPanel(self.root)
        self.assertEqual((panel._alerts.value, panel._popup.value, panel._read_aloud.value),
                         (True, "warnings_watches", True))
        panel._alerts._select(False)
        panel._popup._select("warnings")
        panel._read_aloud._select(False)
        self.assertIs(panel._aurora.value, False)
        panel._aurora._select(True)
        panel._on_save()
        self.assertEqual((themis.alerts_enabled(), themis.alerts_popup(),
                          themis.alerts_read_aloud(), themis.space_aurora_notice()),
                         (False, "warnings", False, True))


class TestSaveStaysOnScreen(unittest.TestCase):
    """The ALERTS section made the form taller than the tab at Felhaven's
    minimum window. Real geometry needs a mapped root, so this one is mapped
    and invisible (alpha 0, no frame); 488 x 330 is the tab at 760 x 500
    (the machine_spirit test's measurement, 2026-11-09)."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0.0)
        self.root.overrideredirect(True)
        theme._init_fonts(self.root)
        self.addCleanup(self.root.destroy)
        self._orig = themis._DATA_PATH
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        themis._DATA_PATH = os.path.join(self._dir.name, "felhaven_settings.json")
        self.addCleanup(setattr, themis, "_DATA_PATH", self._orig)

    def _panel(self, geometry):
        self.root.geometry(geometry)
        panel = ThemisPanel(self.root)
        panel.pack(fill="both", expand=True)
        self.root.update()
        if not panel.winfo_viewable():
            self.skipTest("no mapped window on this display")
        panel._status.config(text="Saved ✓ — weather, alerts, sky, and clock will "
                                  "refresh shortly.")
        self.root.update()
        return panel

    def test_at_the_minimum_the_form_scrolls_and_save_is_on_screen(self):
        panel = self._panel("488x330+40+40")
        self.assertLess(panel.column.winfo_height(), panel._form.winfo_reqheight(),
                        "the form fits — the case would be vacuous")
        self.assertGreaterEqual(panel.column.winfo_height(),
                                panel.winfo_height() - panel.save_bar.winfo_height() - 2,
                                "the form doesn't use the room above SAVE")
        for widget in (panel.save, panel._status):
            with self.subTest(widget=str(widget)):
                self.assertTrue(widget.winfo_ismapped())
                bottom = widget.winfo_rooty() + widget.winfo_height()
                self.assertLessEqual(bottom, panel.winfo_rooty() + panel.winfo_height(),
                                     "cut off at the bottom")
                self.assertGreater(widget.winfo_height(), 1, "squeezed to nothing")

    def test_with_room_save_follows_the_form(self):
        panel = self._panel("700x900+40+40")
        gap = panel.save.winfo_rooty() - (panel.column.winfo_rooty()
                                          + panel.column.winfo_height())
        self.assertLess(gap, 20, "SAVE floats away from the form")
        self.assertEqual(panel.column.winfo_height(), panel._form.winfo_reqheight(),
                         "the whole form should show when there is room")


if __name__ == "__main__":
    unittest.main(verbosity=2)
