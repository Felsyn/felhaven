"""
test_sidebar_smoke.py — headless Tk smoke + unit tests for panels/sidebar.py.

Withdrawn tk.Tk() driven with update_idletasks() (no window, no mainloop) — the
project's standard headless-Tk approach. Covers the one row with real logic: the
Kairos-fed "aura" row's live-temperature subtitle, including the None/degraded
path (§10) and the °F/°C unit split; plus row construction, set_active, rescale,
and the group rows (headers select nothing, collapse/expand, rail order).
TestSidebarScroll covers the rail's scrolling in a mapped, invisible window.

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
        # The rows grow with the fonts; the scroll lane keeps its fixed width.
        self.assertEqual(self.sidebar.cget("width"),
                         Sidebar.BASE_WIDTH * 2 + theme.PhosphorScroll.LANE)

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
        order = self.sidebar._inner.pack_slaves()
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

    def test_the_scrollbar_is_phosphor_not_tk(self):
        # §12: tk.Scrollbar can't be themed on Windows.
        found, todo = [], [self.sidebar]
        while todo:
            w = todo.pop()
            found.append(w)
            todo.extend(w.winfo_children())
        self.assertEqual([w for w in found if isinstance(w, tk.Scrollbar)], [])
        self.assertTrue(any(isinstance(w, theme.PhosphorScroll) for w in found))


class TestSidebarScroll(unittest.TestCase):
    """The rail scrolls when its rows don't fit (found 2026-11-09: at the
    default 960 x 620 window, Vox Array and Cogitator couldn't be reached with
    Navigation open).

    These need real geometry, which a withdrawn window never gets (pack only
    sizes the first level under an unmapped root, so the canvas stays 1 px
    tall). So the sidebar goes in a mapped Toplevel made invisible (alpha 0,
    no frame) at a fixed size: 300 px tall is shorter than the rail, 900 px
    is taller."""

    SHORT, TALL = 300, 900

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.top = tk.Toplevel(self.root)
        self.top.attributes("-alpha", 0.0)
        self.top.overrideredirect(True)
        self.sidebar = Sidebar(self.top, on_select=lambda name: None)
        self.sidebar.pack(side="left", fill="y")
        self._size(self.SHORT)
        if self.sidebar._canvas.winfo_height() <= 1:
            self.top.destroy()
            self.skipTest("no display to map a window on")

    def tearDown(self):
        self.top.destroy()

    def _size(self, height: int) -> None:
        width = Sidebar.BASE_WIDTH + theme.PhosphorScroll.LANE
        self.top.geometry(f"{width}x{height}+0+0")
        self.top.update()

    def _in_view(self, name: str) -> bool:
        """Is the named row wholly inside the visible part of the rail?"""
        row = self.sidebar.row(name)
        top, w = 0, row
        while w is not self.sidebar._inner:
            top += w.winfo_y()
            w = w.master
        shown = self.sidebar._canvas.canvasy(0)
        view = self.sidebar._canvas.winfo_height()
        return shown <= top and top + row.winfo_height() <= shown + view

    def test_a_short_window_cuts_the_rail_and_shows_a_thumb(self):
        self.assertLess(self.sidebar._canvas.yview()[1], 1.0)
        self.assertIsNotNone(self.sidebar._scroll._thumb_box())
        self.assertFalse(self._in_view("cogitator"))

    def test_a_tall_window_has_nothing_to_scroll(self):
        self._size(self.TALL)
        self.assertEqual(self.sidebar._canvas.yview(), (0.0, 1.0))
        self.assertIsNone(self.sidebar._scroll._thumb_box())

    def test_set_active_scrolls_the_lit_row_into_view(self):
        self.sidebar.set_active("cogitator")
        self.top.update()
        self.assertTrue(self._in_view("cogitator"))

    def test_set_active_scrolls_back_to_the_very_top(self):
        self.sidebar.set_active("cogitator")
        self.top.update()
        self.assertGreater(self.sidebar._canvas.canvasy(0), 0.0)   # it did scroll down
        self.sidebar.set_active("felhaven")
        self.top.update()
        self.assertEqual(self.sidebar._canvas.canvasy(0), 0.0)

    def test_a_child_row_is_revealed_after_its_group_opens(self):
        self.sidebar.set_active("artemis")      # opens Navigation first
        self.top.update()
        self.assertTrue(self.sidebar._expanded["navigation"])
        self.assertTrue(self._in_view("artemis"))

    def test_the_wheel_scrolls_the_rail(self):
        class _Wheel:                           # one notch towards the user
            delta = -120
        self.sidebar._on_wheel(_Wheel())
        self.top.update()
        self.assertGreater(self.sidebar._canvas.yview()[0], 0.0)

    def test_every_row_fits_beside_the_lane(self):
        # The lane sits beside the rows instead of taking their width, so no
        # label is cut. Children are measured with Navigation open.
        self.sidebar._toggle_group("navigation")
        self._size(self.TALL)
        for name, row in self.sidebar._rows.items():
            with self.subTest(row=name):
                self.assertLessEqual(row.winfo_reqwidth(), row.winfo_width())


if __name__ == "__main__":
    unittest.main()
