"""
test_felhaven.py — unit tests for the isolable pieces of felhaven.py.

FelhavenApp itself is the integration shell (it builds every panel, Kairos, and
the prewarm threads); its wiring is covered by test_kairos plus the panel smoke
tests. What is unit-testable in isolation is _ClockCluster, the header clock
registered with Kairos under "horai" — its 12h/24h rendering (driven by
themis.clock_24h) and the AM/PM mark logic — and the launch fit: start_height
(tall enough for every nav row), start_position (placed so the bottom clears
the taskbar) and fit_window, which measures and applies both. Withdrawn
tk.Tk(), no mainloop; the checks that need real geometry map an invisible
window, as test_sidebar_smoke does. Also that the app wires the error hooks
(metis_logging's; tests.test_metis_logging tests them), main()'s start-failure
box (the box itself mocked), and _flush_save handing the SCRIBE tab its answer.

    python -X utf8 -m unittest tests.test_felhaven
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import felhaven
import theme
import themis
from panels.sidebar import Sidebar


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


class TestStartHeight(unittest.TestCase):
    """The launch height: BASE_H at least, the rail's whole height when that
    is more, never past the screen."""

    # The window's height above and below the body, padding and rule only.
    _CHROME = sum(felhaven.HEADER_PADY) + felhaven.RULE_H + sum(felhaven.BODY_PADY)
    _SCREEN = 1080

    def test_a_rail_that_already_fits_keeps_the_base_height(self):
        self.assertEqual(felhaven.start_height(40, 100, self._SCREEN), felhaven.BASE_H)

    def test_a_taller_rail_opens_a_taller_window_by_exactly_what_it_needs(self):
        header, rail = 50, 600
        self.assertEqual(felhaven.start_height(header, rail, self._SCREEN),
                         header + self._CHROME + rail)

    def test_never_past_the_screen(self):
        got = felhaven.start_height(50, 5000, self._SCREEN)
        self.assertEqual(got, self._SCREEN - felhaven.SCREEN_MARGIN)

    def test_a_small_screen_still_gets_the_base_height(self):
        # The old behaviour: below BASE_H the window was never shrunk.
        self.assertEqual(felhaven.start_height(50, 5000, 600), felhaven.BASE_H)

    def test_taller_alone_does_not_grow_the_fonts(self):
        """Why measuring once is enough: the rail is measured at scale 1.0,
        and a window that is only taller than BASE_H stays at scale 1.0."""
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        theme._init_fonts(root)
        scale = theme.rescale_fonts(felhaven.BASE_W, felhaven.BASE_H + 400,
                                    felhaven.BASE_W, felhaven.BASE_H)
        self.assertEqual(scale, 1.0)


class TestStartPosition(unittest.TestCase):
    """Where the window opens: centred, with SCREEN_MARGIN at the bottom, so
    Windows' cascading default spot can't push the bottom under the taskbar."""

    def test_a_window_grown_to_the_limit_opens_at_the_top(self):
        screen_w, screen_h = 1366, 768
        height = felhaven.start_height(50, 5000, screen_h)
        self.assertEqual(felhaven.start_position(screen_w, screen_h, height)[1], 0)

    def test_the_bottom_always_clears_the_margin(self):
        """For any height start_height can give a screen that holds it."""
        for screen_h in (720, 768, 900, 1080, 1440):
            for rail in (100, 600, 700, 5000):
                height = felhaven.start_height(50, rail, screen_h)
                if height > screen_h - felhaven.SCREEN_MARGIN:
                    continue                    # a short screen: BASE_H, as before
                _x, y = felhaven.start_position(1920, screen_h, height)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(y + height, screen_h - felhaven.SCREEN_MARGIN,
                                     f"screen {screen_h}, rail {rail}")

    def test_centred_across(self):
        x, _y = felhaven.start_position(1920, 1080, felhaven.BASE_H)
        self.assertEqual(x, (1920 - felhaven.BASE_W) // 2)

    def test_never_off_the_top_left(self):
        """A screen narrower or shorter than the window: pinned at 0, 0."""
        self.assertEqual(felhaven.start_position(800, 600, felhaven.BASE_H), (0, 0))


class TestFitWindow(unittest.TestCase):
    """fit_window, the sequence FelhavenApp runs on launch: withdraw, let
    geometry run, measure the header and the rail, size and place the window,
    show it."""

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
        self.addCleanup(self.top.destroy)
        # A header with something in it, so its requested height is only
        # right once geometry has run (before that, a frame asks for 1 px).
        self.header = tk.Frame(self.top)
        self.header.pack(fill="x", pady=felhaven.HEADER_PADY)
        tk.Label(self.header, text="FELHAVEN", font=theme.FONTS["title"]).pack()
        tk.Frame(self.top, height=felhaven.RULE_H).pack(fill="x")
        body = tk.Frame(self.top)
        body.pack(fill="both", expand=True, pady=felhaven.BODY_PADY)
        self.sidebar = Sidebar(body, on_select=lambda name: None)
        self.sidebar.pack(side="left", fill="y")
        self.geometry = felhaven.fit_window(self.top, self.header, self.sidebar)

    def test_it_measures_after_geometry_has_run(self):
        """The size it set is the one the measured header and rail call for,
        read now that geometry has run — not the 1 px a frame asks for
        before it."""
        screen_w = self.top.winfo_screenwidth()
        screen_h = self.top.winfo_screenheight()
        self.assertGreater(self.header.winfo_reqheight(), 1)
        height = felhaven.start_height(self.header.winfo_reqheight(),
                                       self.sidebar.rail_height(), screen_h)
        x, y = felhaven.start_position(screen_w, screen_h, height)
        self.assertEqual(self.geometry, f"{felhaven.BASE_W}x{height}+{x}+{y}")

    def test_the_window_is_shown_again(self):
        self.assertEqual(self.top.state(), "normal")


class TestEveryRowShowsOnLaunch(unittest.TestCase):
    """The rail, given what start_height gives it, shows every row with
    nothing to scroll — the bug was Cogitator cut off at 960 x 620."""

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
        self.addCleanup(self.top.destroy)
        # The shell's own layout: a header frame, the rule, then the body.
        header = tk.Frame(self.top, height=48)
        header.pack(fill="x", pady=felhaven.HEADER_PADY)
        tk.Frame(self.top, height=felhaven.RULE_H).pack(fill="x")
        body = tk.Frame(self.top)
        body.pack(fill="both", expand=True, pady=felhaven.BODY_PADY)
        self.sidebar = Sidebar(body, on_select=lambda name: None)
        self.sidebar.pack(side="left", fill="y")
        self.top.update_idletasks()
        self.height = felhaven.start_height(header.winfo_reqheight(),
                                            self.sidebar.rail_height(), 10_000)
        self.top.geometry(f"{felhaven.BASE_W}x{self.height}+0+0")
        self.top.update()
        if self.sidebar._canvas.winfo_height() <= 1:
            self.skipTest("no display to map a window on")

    def test_nothing_to_scroll(self):
        self.assertEqual(self.sidebar._canvas.yview(), (0.0, 1.0))
        self.assertIsNone(self.sidebar._scroll._thumb_box())

    def test_the_last_row_is_wholly_in_view(self):
        last = Sidebar._ROWS[-1][0]
        row = self.sidebar.row(last)
        bottom = row.winfo_y() + row.winfo_height()
        self.assertLessEqual(bottom, self.sidebar._canvas.winfo_height())

    def test_the_lane_is_still_there(self):
        """Kept on purpose: an open group or a row added later can still
        outgrow the window."""
        self.assertEqual(self.sidebar._scroll.winfo_manager(), "pack")
        self.sidebar._toggle_group("navigation")
        self.top.update()
        if self.sidebar.rail_height() <= self.sidebar._canvas.winfo_height():
            self.skipTest("the open rail fits this window too")
        self.assertLess(self.sidebar._canvas.yview()[1], 1.0)
        self.assertIsNotNone(self.sidebar._scroll._thumb_box())


def _hook(name):
    """One of felhaven's error hooks, or None where it doesn't exist yet."""
    return getattr(felhaven, name, None)


class TestErrorsReachTheLog(unittest.TestCase):
    """Felhaven runs under pythonw (Felhaven.bat → Sphynx → felhaven.py), so
    sys.stderr is None and an exception Tk or a thread printed there went
    nowhere: no log line (reproduced 2026-11-21). The hooks live in
    metis_logging since S26 (tests.test_metis_logging tests them); setup_logging
    installs the thread and program ones, and the app adds Tk's."""

    def test_the_app_logs_its_root_s_callback_errors(self):
        """FelhavenApp builds every panel and starts models, too much for a
        test to construct, so its __init__ is read for the two calls."""
        import ast
        with open(felhaven.__file__, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        app = next(node for node in tree.body
                   if isinstance(node, ast.ClassDef) and node.name == "FelhavenApp")
        init = next(node for node in app.body
                    if isinstance(node, ast.FunctionDef) and node.name == "__init__")
        calls = [ast.unparse(node) for node in ast.walk(init)
                 if isinstance(node, ast.Call)]
        self.assertIn("setup_logging('felhaven')", calls)
        self.assertIn("log_tk_errors(self.root)", calls)


class TestStartFailure(unittest.TestCase):

    def setUp(self):
        import metis_logging
        seen = getattr(metis_logging, "_seen_failures", None)
        if seen is not None:
            saved = set(seen)
            seen.clear()        # each failure is logged once a run; start clean
            self.addCleanup(lambda: (seen.clear(), seen.update(saved)))

    def test_a_failure_while_building_is_logged_shown_and_raised(self):
        main = _hook("main")
        self.assertIsNotNone(main, "felhaven has no main()")
        failure = KeyError(r"C:\Users\someone\scribe_data.json")
        with mock.patch.object(felhaven, "FelhavenApp", side_effect=failure), \
                mock.patch.object(felhaven, "_show_start_failure") as show:
            with self.assertLogs(level="CRITICAL"):
                with self.assertRaises(KeyError):
                    main()
        show.assert_called_once_with(failure)

    def test_the_box_names_the_kind_never_the_text(self):
        show = _hook("_show_start_failure")
        self.assertIsNotNone(show, "felhaven has no start-failure box")
        with mock.patch.object(felhaven.messagebox, "showerror") as box:
            show(KeyError(r"C:\Users\someone\scribe_data.json"))
        text = box.call_args.args[1]
        self.assertIn("KeyError", text)
        self.assertNotIn("someone", text)
        self.assertIn("felhaven.log", text)


class TestScribeSaveResult(unittest.TestCase):
    """A failed SCRIBE save was only logged (the owner's pick, 2026-11-21:
    say so on the tab's count line)."""

    def test_flush_save_hands_the_tab_the_answer(self):
        tab = mock.Mock()
        app = mock.Mock(data={"tasks": [], "notes": ""},
                        _views={"cogitator": mock.Mock(scribe=tab)})
        for answer in (False, True):
            with self.subTest(saved=answer), \
                    mock.patch.object(felhaven.scribe, "save_data", return_value=answer):
                tab.reset_mock()
                felhaven.FelhavenApp._flush_save(app)
                tab.show_saved.assert_called_once_with(answer)


if __name__ == "__main__":
    unittest.main()
