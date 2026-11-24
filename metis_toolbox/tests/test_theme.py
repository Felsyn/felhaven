"""
test_theme.py — first coverage for the shared visual-identity module.

Headless Tk (a withdrawn root driven by update_idletasks, no mainloop — the
same pattern as the panel smoke tests) exercises theme's public seams:
  - the font lifecycle (_init_fonts populates FONTS; rescale_fonts grows,
    caps at 3x, and never shrinks below the base),
  - the shared widgets (Card header + set_header, TabHost tab switching,
    PhosphorScroll's fits-vs-overflows thumb),
  - the right-click menu primitives (themed_menu builds in-theme, popup_menu
    pops and releases the grab),
  - the widget defaults (_init_widget_defaults: the selection and cursor
    colours no widget sets, and every root that inits fonts inits them too),
  - place_alert, over the main window while it shows (the minimised branch
    is in Iris's and Ammit's popup tests).

    python -X utf8 -m unittest tests.test_theme
"""

import ast
import os
import subprocess
import sys
import unittest
from unittest import mock

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _APP_ROOT)

import tkinter as tk

import theme
from panels.zeno_panel import ZenoPanel

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


class WidgetDefaultsTests(unittest.TestCase):
    """_init_widget_defaults: the colours Tk would otherwise take from Windows.

    Its own root on purpose: each tk.Tk() is a separate Tcl interpreter with
    its own option database, so these defaults cannot leak into ThemeTests."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)
        theme._init_widget_defaults(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _make(self, widget_class, **kw):
        widget = widget_class(self.root, **kw)
        self.addCleanup(widget.destroy)
        return widget

    def test_a_bare_entry_selects_and_types_in_theme(self):
        """Nothing here sets these options; the theme does. Until 2026-09-27
        every Entry selected in Windows blue with a black cursor, and
        test_palette_guard could not see it, because nobody wrote either."""
        entry = self._make(tk.Entry)
        self.assertEqual(entry.cget("selectbackground"), theme.C["select_bg"])
        self.assertEqual(entry.cget("selectforeground"), theme.C["select_fg"])
        self.assertEqual(entry.cget("insertbackground"), theme.C["text1"])

    def test_a_bare_text_keeps_its_selection_when_focus_leaves(self):
        """Tk's default inactiveselectbackground is '' (draw nothing), so a
        Text's selection vanished as soon as focus moved elsewhere."""
        text = self._make(tk.Text)
        self.assertEqual(text.cget("selectbackground"), theme.C["select_bg"])
        self.assertEqual(text.cget("inactiveselectbackground"),
                         theme.C["select_bg"])

    def test_a_real_panel_field_gets_them_too(self):
        """A representative field built by a panel rather than by this test:
        Zeno's entry sets its own bg, fg and cursor, and no selection option."""
        panel = self._make(ZenoPanel)
        self.assertEqual(panel._entry.cget("selectbackground"),
                         theme.C["select_bg"])

    def test_an_explicit_option_still_wins(self):
        """A default, not an override: a panel can still opt out."""
        entry = self._make(tk.Entry, selectbackground=theme.C["red"])
        self.assertEqual(entry.cget("selectbackground"), theme.C["red"])

    def test_the_selection_outranks_tags_made_after_it(self):
        """A Text draws its selection as the "sel" tag, and Tk creates that tag
        first, so every tag a panel adds outranks it. Measured on screen: the
        selected text kept its tag's colour, so Home's chat ignored
        selectforeground. The class binding raises "sel" when it changes."""
        text = self._make(tk.Text)
        text.insert("end", "an answer", "late")
        text.tag_configure("late", foreground=theme.C["amber"])
        text.tag_add("sel", "1.0", "end")
        self.root.update()                  # <<Selection>> is queued, not immediate
        self.assertEqual(text.tag_names()[-1], "sel")   # lowest priority first

    def test_every_root_that_inits_fonts_inits_widget_defaults(self):
        """Felhaven and Sphynx each own a tk.Tk(). A third root that copied
        only _init_fonts would quietly select in Windows blue again. Tracked
        files, not the filesystem (CONVENTIONS §10); tests/ is exempt, since a
        test root may init fonts alone."""
        try:
            out = subprocess.run(["git", "ls-files", "*.py"], cwd=_APP_ROOT,
                                 check=True, capture_output=True, text=True).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git is not available")
        callers = {"_init_fonts": set(), "_init_widget_defaults": set()}
        for rel in (line.replace("\\", "/") for line in out.splitlines()):
            if not rel or rel.startswith("tests/"):
                continue
            with open(os.path.join(_APP_ROOT, rel), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                    if name in callers:
                        callers[name].add(rel)
        self.assertIn("felhaven.py", callers["_init_fonts"])   # the scan found them
        self.assertEqual(callers["_init_fonts"], callers["_init_widget_defaults"])


class ScrollColumnSizeTests(unittest.TestCase):
    """set_size() and scroll_to_end(): THEMIS's form, SCRIBE's task list and
    the Cartograph's cached-areas list reached into _canvas for these (BACKLOG,
    routed to S26). Real geometry needs a mapped root: mapped and invisible."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0.0)
        self.root.overrideredirect(True)
        self.root.geometry("200x300+40+40")
        theme._init_fonts(self.root)
        self.addCleanup(self.root.destroy)
        self.column = theme.ScrollColumn(self.root)
        self.column.pack(side="top", fill="x")
        for i in range(30):
            tk.Label(self.column.body, text=f"row {i}").pack(anchor="w")
        self.root.update()
        if not self.column.winfo_viewable():
            self.skipTest("no mapped window on this display")

    def _need(self, name):
        method = getattr(self.column, name, None)
        self.assertIsNotNone(method, f"ScrollColumn has no {name}()")
        return method

    def test_set_size_asks_for_the_room_and_the_column_scrolls_past_it(self):
        self._need("set_size")(height=120, width=150)
        self.root.update()
        self.assertEqual(self.column._canvas.winfo_height(), 120)
        self.assertEqual(int(self.column._canvas.cget("width")), 150)
        first, last = self.column._canvas.yview()
        self.assertLess(last - first, 1, "thirty rows in 120 px should scroll")

    def test_a_size_it_was_not_given_is_left_alone(self):
        set_size = self._need("set_size")
        set_size(height=120, width=150)
        set_size(height=90)
        self.assertEqual(int(self.column._canvas.cget("width")), 150)
        self.assertEqual(int(self.column._canvas.cget("height")), 90)

    def test_scroll_to_end_shows_the_last_row(self):
        self._need("set_size")(height=120)
        self.root.update()
        self._need("scroll_to_end")()
        self.root.update()
        self.assertEqual(self.column._canvas.yview()[1], 1.0)
        self.column.scroll_to_top()
        self.assertEqual(self.column._canvas.yview()[0], 0.0)


class PlaceAlertTests(unittest.TestCase):
    """Iris's and Ammit's popups stand alone (never `transient`) and are put
    over the main window by place_alert. Mapped and invisible: a withdrawn
    root takes the other branch."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0.0)
        self.root.overrideredirect(True)
        self.root.geometry("600x450+40+40")
        self.addCleanup(self.root.destroy)
        self.root.update()
        if not self.root.winfo_viewable():
            self.skipTest("no mapped window on this display")
        self.popup = tk.Toplevel(self.root)
        self.popup.withdraw()

    def _expected(self, w, h):
        x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 3
        return max(0, x), max(0, y)

    def test_a_fixed_size_is_centred_a_third_down_the_main_window(self):
        with mock.patch.object(self.popup, "geometry") as geometry:
            theme.place_alert(self.popup, 360, 160)
        x, y = self._expected(360, 160)
        geometry.assert_called_once_with(f"360x160+{x}+{y}")

    def test_with_no_size_it_takes_what_the_popup_asks_for(self):
        tk.Label(self.popup, text="a warning " * 4).pack()
        with mock.patch.object(self.popup, "geometry") as geometry:
            theme.place_alert(self.popup)
        x, y = self._expected(self.popup.winfo_reqwidth(), self.popup.winfo_reqheight())
        geometry.assert_called_once_with(f"+{x}+{y}")


class RgbTests(unittest.TestCase):
    """rgb() promised a ValueError for anything that isn't #RRGGBB, but int()
    takes a sign or a space, so "#+7+8+9" read as (7, 8, 9) (2026-11-21)."""

    def test_every_palette_value_parses(self):
        for role, value in theme.C.items():
            with self.subTest(role=role):
                r, g, b = theme.rgb(value)
                self.assertEqual("#%02X%02X%02X" % (r, g, b), value.upper())

    def test_anything_else_is_refused(self):
        good = theme.C["text1"]
        for bad in ("#+7+8+9", "# 7 8 9", "#" + "G" * 6, good[1:] + "0", good[:-1],
                    good + "0", "", "#-1-2-3"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    theme.rgb(bad)


if __name__ == "__main__":
    unittest.main()
