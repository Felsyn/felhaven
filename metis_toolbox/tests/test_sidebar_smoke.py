"""
test_sidebar_smoke.py — headless Tk smoke + unit tests for panels/sidebar.py.

Withdrawn tk.Tk() driven with update_idletasks() (no window, no mainloop) — the
project's standard headless-Tk approach. Covers the one row with real logic: the
Kairos-fed "aura" row's live-temperature subtitle, including the None/degraded
path (§10) and the °F/°C unit split; plus row construction, set_active, rescale,
and the group rows (headers select nothing, collapse/expand, rail order).

Geometry assertions use winfo_manager() ("" when forgotten, "pack" when packed)
rather than winfo_ismapped(): the root is withdrawn, so nothing is ever mapped.

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
        # Derive both halves — a group header is in _ROWS but is not a view, so
        # it must NOT get a SidebarRow; its children must.
        leaves   = {n for n, _l, _s in Sidebar._ROWS if n not in Sidebar._GROUPS}
        children = {n for kids in Sidebar._GROUPS.values() for n, _l, _s in kids}
        self.assertEqual(set(self.sidebar._rows), leaves | children)

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

    # ── Group rows ───────────────────────────────────────────────────────────

    def test_group_header_is_not_selectable(self):
        # A header has no view behind it: no SidebarRow, and toggling it must
        # never reach on_select (felhaven._views has no "navigation" key, so a
        # stray call would be a KeyError in _show_view).
        self.assertNotIn("navigation", self.sidebar._rows)
        self.assertIn("navigation", self.sidebar._groups)
        self.sidebar._toggle_group("navigation")
        self.assertEqual(self.clicks, [])

    def test_group_starts_collapsed(self):
        # §7's collapsed-by-default. Also what keeps the rail inside the window
        # as a group grows: collapsed, a group costs exactly one row no matter
        # how many children it holds.
        self.assertFalse(self.sidebar._expanded["navigation"])
        self.assertEqual(
            self.sidebar._group_boxes["navigation"].winfo_manager(), "")
        self.assertEqual(self.sidebar._groups["navigation"]._glyph.cget("text"), "▶")

    def test_toggle_shows_and_hides_the_group_box(self):
        box = self.sidebar._group_boxes["navigation"]
        self.sidebar._toggle_group("navigation")
        self.root.update_idletasks()
        self.assertTrue(self.sidebar._expanded["navigation"])
        self.assertEqual(box.winfo_manager(), "pack")
        self.assertEqual(self.sidebar._groups["navigation"]._glyph.cget("text"), "▼")

        self.sidebar._toggle_group("navigation")
        self.root.update_idletasks()
        self.assertFalse(self.sidebar._expanded["navigation"])
        self.assertEqual(box.winfo_manager(), "")

    def test_expand_restores_rail_order(self):
        # The bug _relayout exists to prevent: a bare pack() on re-expand
        # appends the box to the BOTTOM of the rail instead of its own slot.
        self.sidebar._toggle_group("navigation")   # expand
        self.sidebar._toggle_group("navigation")   # collapse
        self.sidebar._toggle_group("navigation")   # expand again
        self.root.update_idletasks()
        order = self.sidebar.pack_slaves()
        header = self.sidebar._groups["navigation"]
        box    = self.sidebar._group_boxes["navigation"]
        self.assertEqual(order.index(box), order.index(header) + 1)
        self.assertLess(order.index(box), order.index(self.sidebar.row("midas")))

    def test_set_active_expands_collapsed_group(self):
        # The lit row must never be hidden inside a collapsed group — this is
        # what makes a programmatic _show_view("hypatia") reveal its own row.
        self.sidebar.set_active("hypatia")
        self.root.update_idletasks()
        self.assertTrue(self.sidebar._expanded["navigation"])
        child = self.sidebar.row("hypatia")
        self.assertEqual(child._dot.itemcget(child._dot_item, "fill"),
                         theme.C["green"])

    def test_rescale_grows_child_indent(self):
        self.sidebar.rescale(2.0)
        self.root.update_idletasks()
        padx = self.sidebar.row("hypatia").pack_info()["padx"]
        # Tk returns the two-sided padx as a tuple or a "left right" string
        # depending on version — normalize before comparing.
        left = padx[0] if isinstance(padx, (tuple, list)) else str(padx).split()[0]
        self.assertEqual(int(left), Sidebar.BASE_INDENT * 2)


if __name__ == "__main__":
    unittest.main()
