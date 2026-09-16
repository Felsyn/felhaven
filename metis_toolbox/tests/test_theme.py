"""
test_theme.py — first coverage for the shared visual-identity module.

Headless Tk (a withdrawn root driven by update_idletasks, no mainloop — the
same pattern as the panel smoke tests) exercises theme's public seams:
  - the font lifecycle (_init_fonts populates FONTS; rescale_fonts grows,
    caps at 3x, and never shrinks below the base),
  - the shared widgets (Card header + set_header, TabHost tab switching,
    PhosphorScroll's fits-vs-overflows thumb),
  - the right-click menu primitives (themed_menu builds in-theme, popup_menu
    pops and releases the grab).

    python -X utf8 -m unittest tests.test_theme
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme

# Arbitrary baseline geometry for the rescale tests (felhaven's real one).
_BW, _BH = 960, 620


class _FakeHost(theme.TabHost):
    """Minimal TabHost subclass: two empty bodies, default = 'a'. Mirrors how
    Moderati/Cogitator/Vox Array build (super().__init__, then _build_tabs)."""

    def __init__(self, parent):
        super().__init__(parent, "Test Host", theme.C["teal"])
        self.a = tk.Frame(self.body)
        self.b = tk.Frame(self.body)
        self._build_tabs([
            ("a", "TAB A", self.a, theme.C["blue"]),
            ("b", "TAB B", self.b, theme.C["red"]),
        ], default="a")


class ThemeTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        # Reset fonts to their base sizes before every test — rescale_fonts
        # mutates the shared FONTS registry in place, so start each test clean.
        theme._init_fonts(self.root)

    # ── Font lifecycle ────────────────────────────────────────────────────────

    def test_init_fonts_populates_every_spec(self):
        self.assertEqual(set(theme.FONTS), set(theme._FONT_SPECS))
        for name, font in theme.FONTS.items():
            self.assertIsInstance(font, tk.font.Font)

    def test_rescale_grows_proportionally(self):
        base = theme.FONTS["body"].cget("size")
        scale = theme.rescale_fonts(2 * _BW, 2 * _BH, _BW, _BH)
        self.assertEqual(scale, 2.0)
        self.assertEqual(theme.FONTS["body"].cget("size"), base * 2)

    def test_rescale_is_capped_at_3x(self):
        self.assertEqual(theme.rescale_fonts(100 * _BW, 100 * _BH, _BW, _BH), 3.0)

    def test_rescale_only_grows_never_shrinks(self):
        base = theme.FONTS["body"].cget("size")
        scale = theme.rescale_fonts(_BW // 2, _BH // 2, _BW, _BH)
        self.assertEqual(scale, 1.0)
        self.assertEqual(theme.FONTS["body"].cget("size"), base)

    # ── Card ────────────────────────────────────────────────────────────────

    def test_card_header_upcases_and_set_header_updates(self):
        card = theme.Card(self.root, "atmospherics", theme.C["blue"])
        self.addCleanup(card.destroy)
        self.assertEqual(card._header_lbl.cget("text"), "ATMOSPHERICS")
        card.set_header("Atmospherics — London")
        self.assertEqual(card._header_lbl.cget("text"), "ATMOSPHERICS — LONDON")

    # ── TabHost ───────────────────────────────────────────────────────────────

    def test_tabhost_starts_on_default_and_lights_only_it(self):
        host = _FakeHost(self.root)
        self.addCleanup(host.destroy)
        self.assertEqual(host._active, "a")
        self.assertEqual(set(host._tabs), {"a", "b"})
        # Active tab = bright text + its accent underline; inactive = dim + border.
        self.assertEqual(host._tabs["a"].cget("fg"), theme.C["text1"])
        self.assertEqual(host._tabs["b"].cget("fg"), theme.C["text3"])
        self.assertEqual(host._lines["a"].cget("bg"), theme.C["blue"])   # accent
        self.assertEqual(host._lines["b"].cget("bg"), theme.C["border"])

    def test_tabhost_switch_flips_the_active_colors(self):
        host = _FakeHost(self.root)
        self.addCleanup(host.destroy)
        host._show_tab("b")
        self.assertEqual(host._active, "b")
        self.assertEqual(host._tabs["b"].cget("fg"), theme.C["text1"])
        self.assertEqual(host._lines["b"].cget("bg"), theme.C["red"])    # b's accent
        self.assertEqual(host._tabs["a"].cget("fg"), theme.C["text3"])
        self.assertEqual(host._lines["a"].cget("bg"), theme.C["border"])

    def test_tabhost_reshowing_active_tab_is_a_noop(self):
        host = _FakeHost(self.root)
        self.addCleanup(host.destroy)
        host._show_tab("a")           # already active — early return, no crash
        self.assertEqual(host._active, "a")

    # ── PhosphorScroll ──────────────────────────────────────────────────────

    def test_scroll_accepts_string_args_from_yscrollcommand(self):
        bar = theme.PhosphorScroll(self.root)
        self.addCleanup(bar.destroy)
        bar.set("0.0", "0.3")          # tk passes strings, not floats
        self.assertEqual((bar._first, bar._last), (0.0, 0.3))

    def test_scroll_hides_thumb_when_content_fits(self):
        bar = theme.PhosphorScroll(self.root)
        self.addCleanup(bar.destroy)
        bar.set(0.0, 1.0)              # whole content visible → span >= 1
        self.assertIsNone(bar._thumb_box())

    def test_scroll_shows_thumb_when_content_overflows(self):
        bar = theme.PhosphorScroll(self.root)
        self.addCleanup(bar.destroy)
        bar.pack(fill="y", expand=True)
        self.root.update_idletasks()
        bar.set(0.0, 0.5)              # half visible → a thumb exists
        self.assertIsNotNone(bar._thumb_box())

    def test_scroll_requests_no_height_of_its_own(self):
        """A bare tk.Canvas asks for 265 px of height. PhosphorScroll is always
        packed fill="y" beside the thing it scrolls, so that request is pure
        fiction — but geometry managers believe it, and it quietly put a 265 px
        floor under every container holding one. On Home that floor ate the
        status line and the chat entry at any window under ~1280x900: pack
        satisfies requested heights in pack order before it hands the leftover
        to expand=True children, so the transcript's scrollbar outbid the thing
        you type into.

        Asserted rather than trusted because the symptom is invisible on a
        maximised window and no test fails when it regresses — the layout is
        simply wrong at sizes nobody happened to try."""
        bar = theme.PhosphorScroll(self.root)
        self.addCleanup(bar.destroy)
        self.assertLess(bar.winfo_reqheight(), 40)

    def test_scroll_still_claims_its_lane_width(self):
        """The width is NOT fiction — the docstring promises the lane keeps its
        packed width so layout never jumps when the thumb hides."""
        bar = theme.PhosphorScroll(self.root)
        self.addCleanup(bar.destroy)
        self.assertEqual(bar.winfo_reqwidth(), theme.PhosphorScroll.LANE)

    # ── Right-click menu primitives ───────────────────────────────────────────

    def test_themed_menu_is_a_tk_menu_in_theme_colors(self):
        menu = theme.themed_menu(self.root)
        self.assertIsInstance(menu, tk.Menu)
        # tk.Menu.cget returns a Tcl object for color options, not a plain str.
        self.assertEqual(str(menu.cget("bg")), theme.C["card"])
        self.assertEqual(str(menu.cget("fg")), theme.C["text1"])
        self.assertEqual(int(menu.cget("tearoff")), 0)

    def test_popup_menu_pops_at_coords_and_releases_the_grab(self):
        menu = theme.themed_menu(self.root)
        menu.add_command(label="Copy")
        event = tk.Event()
        event.x_root = event.y_root = 42
        with mock.patch.object(tk.Menu, "tk_popup") as pop, \
                mock.patch.object(tk.Menu, "grab_release") as release:
            theme.popup_menu(menu, event)
        pop.assert_called_once_with(42, 42)
        release.assert_called_once()


if __name__ == "__main__":
    unittest.main()
