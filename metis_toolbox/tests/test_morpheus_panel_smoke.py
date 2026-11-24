"""
test_morpheus_panel_smoke.py — headless Tk smoke test for MorpheusPanel.

Same approach as test_aura_panel_smoke.py: a real tk.Tk() root we withdraw()
(no window flashes) and drive with update_idletasks() instead of mainloop().
No exception raised == pass.

mpv / yt-dlp are not assumed present. The DISABLED path (no binaries) is tested
against the real morpheus.available(); the ENABLED path monkeypatches
available()/search()/load_playlists so the full transport + search render runs
without the binaries actually being installed.

    python -X utf8 -m unittest tests.test_morpheus_panel_smoke
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
from tools import morpheus


class TestMorpheusPanelDisabled(unittest.TestCase):
    """No binaries: placeholder shown, controls inert, update() is a no-op."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        from panels.morpheus_panel import MorpheusPanel
        # Force the disabled path regardless of the host machine.
        self._orig_avail = morpheus.available
        morpheus.available = lambda: {"mpv": None, "ytdlp": None}
        self.panel = MorpheusPanel(self.root)
        self.panel.pack()

    def tearDown(self):
        morpheus.available = self._orig_avail
        self.panel.destroy()

    def test_disabled_no_crash_on_update(self):
        self.assertFalse(self.panel._enabled)
        for data in (None, {"running": False},
                     {"running": True, "title": "X", "pos": 1, "dur": 2}):
            self.panel.update(data)
            self.root.update_idletasks()

    def test_missing_binaries_say_missing(self):
        from panels.morpheus_panel import _NO_BIN_MSG
        self.assertEqual(self.panel._no_bin_text, _NO_BIN_MSG)

    def test_a_refused_binary_says_why_not_missing(self):
        from panels.morpheus_panel import MorpheusPanel, _UNVERIFIED_MSG
        morpheus.available = lambda: {"mpv": None, "ytdlp": "yt-dlp.exe",
                                      "problem": "binary_unverified: mpv.exe does not match"}
        panel = MorpheusPanel(self.root)
        try:
            texts = [w.cget("text") for frame in (panel._playlists_frame, panel._search_frame)
                     for w in frame.winfo_children() if isinstance(w, tk.Label)]
            self.assertEqual(texts, [_UNVERIFIED_MSG, _UNVERIFIED_MSG])
        finally:
            panel.destroy()

    def test_starts_on_playlists_tab(self):
        self.assertEqual(self.panel._active, "playlists")


class TestMorpheusPanelEnabled(unittest.TestCase):
    """Binaries 'present' (monkeypatched): full transport + search render."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        from panels.morpheus_panel import MorpheusPanel
        self._orig = (morpheus.available, morpheus.search, morpheus.load_playlists)
        morpheus.available = lambda: {"mpv": "mpv.exe", "ytdlp": "yt-dlp.exe"}
        morpheus.load_playlists = lambda: [
            {"label": "Test playlist", "url": "https://www.youtube.com/playlist?list=PL1"}
        ]
        morpheus.search = lambda q, limit=10: [
            {"title": "Result one", "channel": "Chan", "duration": 245,
             "url": "https://www.youtube.com/watch?v=abc"},
            {"title": "Result two", "channel": "", "duration": None,
             "url": "https://www.youtube.com/watch?v=def"},
        ]
        self.panel = MorpheusPanel(self.root)
        self.panel.pack()

    def tearDown(self):
        (morpheus.available, morpheus.search, morpheus.load_playlists) = self._orig
        self.panel.destroy()

    def test_enabled(self):
        self.assertTrue(self.panel._enabled)

    def test_play_glyph_flips_with_state(self):
        self.panel.update(None)
        self.assertEqual(self.panel._btns["play"].cget("text"), "▶")

        self.panel.update({"running": True, "title": "Song", "paused": False,
                           "pos": 65, "dur": 200})
        self.root.update_idletasks()
        self.assertEqual(self.panel._btns["play"].cget("text"), "⏸")
        self.assertEqual(self.panel._now_lbl.cget("text"), "Song")
        self.assertEqual(self.panel._pos_lbl.cget("text"), "1:05 / 3:20")

        self.panel.update({"running": True, "title": "Song", "paused": True,
                           "pos": 65, "dur": 200})
        self.root.update_idletasks()
        self.assertEqual(self.panel._btns["play"].cget("text"), "▶")

    def test_failed_load_renders_red_and_beats_the_idle_line(self):
        """After a failed load mpv really is running-and-idle, so "nothing
        playing" would be an honest-looking lie. The error must win, and it must
        name where the reason is (the slug deliberately carries no detail —
        CONVENTIONS §2, since mpv's own lines hold signed URLs)."""
        from panels.morpheus_panel import _ERROR_TEXT

        # running=True is what status() really reports here — the process is up
        # and the pipe answered; only the load failed. The fixture used to say
        # False, contradicting this docstring, which mattered once `running`
        # started driving the transport gate.
        self.panel.update({"running": True, "error": "load_failed"})
        self.root.update_idletasks()
        self.assertEqual(self.panel._now_lbl.cget("text"),
                         _ERROR_TEXT["load_failed"])
        # The distilled file, not the ~420 KB-per-load scratch buffer.
        self.assertIn("last_failure.log", self.panel._now_lbl.cget("text"))
        self.assertEqual(str(self.panel._now_lbl.cget("fg")), theme.C["red"])
        # ...and the transport stays reachable, because ⏹ is what clears the
        # sticky error. Dimming here would strand the user in the red state.
        self.assertTrue(self.panel._live)

    def test_unmapped_slug_is_surfaced_not_swallowed(self):
        """The echo_panel fallback: a code with no human text still shows."""
        self.panel.update({"running": False, "error": "some_new_code"})
        self.root.update_idletasks()
        self.assertIn("some_new_code", self.panel._now_lbl.cget("text"))

    def test_recovering_from_an_error_restores_the_normal_line(self):
        self.panel.update({"running": False, "error": "load_failed"})
        self.panel.update({"running": True, "title": "Song", "paused": False,
                           "pos": 65, "dur": 200})
        self.root.update_idletasks()
        self.assertEqual(self.panel._now_lbl.cget("text"), "Song")
        self.assertEqual(str(self.panel._now_lbl.cget("fg")), theme.C["text1"])

    def test_transport_starts_dim_because_mpv_is_not_running_yet(self):
        # Binaries present is not the same as mpv up. Before the first tick
        # there is nothing to click, so nothing should look clickable.
        self.assertFalse(self.panel._live)
        for key, b in self.panel._btns.items():
            with self.subTest(button=key):
                self.assertEqual(str(b.cget("fg")), theme.C["text3"])

    def test_transport_lights_and_dims_with_the_pipe(self):
        self.panel.update({"running": True, "title": "Song", "paused": False,
                           "pos": 1, "dur": 2})
        self.root.update_idletasks()
        self.assertTrue(self.panel._live)
        self.assertEqual(str(self.panel._btns["next"].cget("fg")), theme.C["text1"])
        self.assertEqual(str(self.panel._btns["next"].cget("cursor")), "hand2")

        self.panel.update({"running": False})
        self.root.update_idletasks()
        self.assertFalse(self.panel._live)
        self.assertEqual(str(self.panel._btns["next"].cget("fg")), theme.C["text3"])
        self.assertEqual(str(self.panel._btns["next"].cget("cursor")), "")

    def test_a_dead_pipe_swallows_the_click_instead_of_writing_into_the_void(self):
        """The bug: _ipc() never raises and has nobody to deliver to, so the
        click used to 'succeed' silently. Guard, not hope."""
        calls = []
        self.panel.update({"running": False})
        self.panel._on_transport(lambda: calls.append("fired"))
        self.assertEqual(calls, [], "wrote to mpv while the pipe was down")

        self.panel.update({"running": True, "title": "S", "paused": False,
                           "pos": 1, "dur": 2})
        self.panel._on_transport(lambda: calls.append("fired"))
        self.assertEqual(calls, ["fired"], "refused a click mpv could have heard")

    def test_dead_pipe_does_not_let_the_play_glyph_lie(self):
        """_on_toggle_pause flips ⏯ optimistically so it beats the 2 s tick.
        With no mpv that flip advertised a state change that never happened."""
        self.panel.update({"running": False})
        self.root.update_idletasks()
        before = self.panel._btns["play"].cget("text")
        self.panel._on_transport(self.panel._on_toggle_pause)
        self.assertEqual(self.panel._btns["play"].cget("text"), before)

    def test_tab_switch(self):
        self.panel._show_tab("search")
        self.root.update_idletasks()
        self.assertEqual(self.panel._active, "search")
        self.panel._show_tab("playlists")
        self.root.update_idletasks()
        self.assertEqual(self.panel._active, "playlists")

    def test_search_flow_renders_rows(self):
        self.panel._show_tab("search")
        self.panel._search_var.set("lofi")
        self.panel._on_search()
        # Worker thread is trivial here; join it, then drain via update().
        self.panel._search_thread.join(timeout=2)
        self.panel.update(None)
        self.root.update_idletasks()
        # Two result rows + their dividers were rendered into the scroll body.
        rows = [w for w in self.panel._results.body.winfo_children()
                if isinstance(w, tk.Frame)]
        self.assertGreaterEqual(len(rows), 2)

    def _save_glyphs(self):
        """Every '＋' save glyph in the rendered search results."""
        return [child
                for row in self.panel._results.body.winfo_children()
                for child in row.winfo_children()
                if isinstance(child, tk.Label) and child.cget("text") == "＋"]

    def _render_results(self):
        """Render one result row synchronously, on the main thread.

        Deliberately bypasses _on_search's worker thread: these tests are about
        the save glyph, not about how the rows arrived, and joining the thread
        made them depend on its timing (that raced on CI's 3.10 runner, where
        this is the first test in the class to start it). The threaded path has
        its own coverage in test_search_flow_renders_rows."""
        self.panel._show_tab("search")
        self.panel._build_result_row({
            "title": "Result one", "channel": "Chan", "duration": 245,
            "url": "https://www.youtube.com/watch?v=abc",
        })
        self.root.update_idletasks()

    def test_failed_save_arms_a_transient_indicator(self):
        self._orig_save = morpheus.save_playlist
        morpheus.save_playlist = lambda label, url: False
        self.addCleanup(lambda: setattr(morpheus, "save_playlist", self._orig_save))

        self._render_results()
        glyph = self._save_glyphs()[0]
        self.panel._save_from_search("Result one", "https://x/", glyph)
        self.assertEqual(glyph.cget("text"), "!")

        self.panel._reset_save_glyph(glyph)
        self.assertEqual(glyph.cget("text"), "＋")
        self.assertEqual(str(glyph.cget("fg")), theme.C["text3"])

    def test_reset_on_a_destroyed_save_glyph_is_a_no_op(self):
        """A second search inside the 2 s window clears the result rows, so the
        scheduled reset can fire on a destroyed glyph — the winfo_exists()
        guard is what keeps that from raising."""
        self._render_results()
        glyph = self._save_glyphs()[0]
        self.panel._clear_results()        # what a second search does
        self.panel._reset_save_glyph(glyph)   # must not raise TclError



    # ── Virgil tooltips ─────────────────────────────────────────────────────

    def _tip_on(self, widget):
        from panels import virgil_tooltip
        return virgil_tooltip._shared(self.root)._tips.get(str(widget))

    def _labels(self, parent, text):
        found, stack = [], [parent]
        while stack:
            w = stack.pop()
            if isinstance(w, tk.Label) and w.cget("text") == text:
                found.append(w)
            stack.extend(w.winfo_children())
        return found

    def test_the_transport_and_save_carry_their_tips(self):
        from panels.morpheus_panel import MorpheusPanel
        panel = self.panel
        self.assertEqual(sorted(MorpheusPanel._TIPS), sorted(panel.tip_targets))
        for key in ("prev", "play", "next", "stop"):
            self.assertIs(panel.tip_targets[key], panel._btns[key])
        for key, tip in MorpheusPanel._TIPS.items():
            self.assertEqual(self._tip_on(panel.tip_targets[key]), tip, key)

    def test_each_inner_tab_carries_its_tip(self):
        from panels.morpheus_panel import MorpheusPanel
        self.assertEqual(sorted(MorpheusPanel._TAB_TIPS), sorted(self.panel._tabs))
        for key, tip in MorpheusPanel._TAB_TIPS.items():
            self.assertEqual(self._tip_on(self.panel._tabs[key]), tip, key)

    def test_every_playlist_row_and_search_result_carries_its_glyph_tip(self):
        from panels.morpheus_panel import MorpheusPanel
        panel = self.panel
        removes = self._labels(panel._pl_scroll.body, "✕")
        self.assertEqual(len(removes), 1)
        self.assertEqual(self._tip_on(removes[0]),
                         MorpheusPanel._ROW_TIPS["playlist_remove"])
        panel._build_result_row({"title": "Result", "channel": "", "duration": None,
                                 "url": "https://www.youtube.com/watch?v=abc"})
        saves = self._labels(panel._results.body, "＋")
        self.assertEqual(len(saves), 1)
        self.assertEqual(self._tip_on(saves[0]), MorpheusPanel._ROW_TIPS["search_save"])

    def test_a_saved_result_keeps_its_tip_after_it_unbinds_itself(self):
        """A saved ＋ becomes a tick and unbinds its own click and hover. The
        tip rides a bindtag, so it stays."""
        from panels import virgil_tooltip
        from panels.morpheus_panel import MorpheusPanel
        panel = self.panel
        panel._build_result_row({"title": "Result", "channel": "", "duration": None,
                                 "url": "https://www.youtube.com/watch?v=abc"})
        glyph = self._labels(panel._results.body, "＋")[0]
        with mock.patch.object(morpheus, "save_playlist", return_value=True):
            panel._save_from_search("Result", "https://www.youtube.com/watch?v=abc", glyph)
        self.assertEqual(glyph.cget("text"), "✓")
        self.assertFalse(glyph.bind("<Button-1>"))
        self.assertIn(virgil_tooltip._TAG, glyph.bindtags())
        self.assertEqual(self._tip_on(glyph), MorpheusPanel._ROW_TIPS["search_save"])


class TestMorpheusFormatters(unittest.TestCase):
    def test_fmt_t(self):
        from panels.morpheus_panel import MorpheusPanel
        self.assertEqual(MorpheusPanel._fmt_t(None), "-:--")
        self.assertEqual(MorpheusPanel._fmt_t(0), "0:00")
        self.assertEqual(MorpheusPanel._fmt_t(65), "1:05")
        self.assertEqual(MorpheusPanel._fmt_t(3599), "59:59")

    def test_fmt_dur(self):
        from panels.morpheus_panel import MorpheusPanel
        self.assertEqual(MorpheusPanel._fmt_dur(None), "")
        self.assertEqual(MorpheusPanel._fmt_dur(245), "4:05")



# ─────────────────────────────────────────────────────────────────────────────
#  The V0.03 walkthrough (S35, 2026-11-23)
# ─────────────────────────────────────────────────────────────────────────────

class _RealPlaylistsPanel(unittest.TestCase):
    """The panel over a real (temporary) playlists file, read by Morpheus's
    own load_playlists, with the binaries faked as present."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        import tempfile, shutil
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.path = os.path.join(d, "morpheus_playlists.json")
        for target, value in (
                ("_PLAYLISTS_PATH", self.path),
                ("available", lambda: {"mpv": "m", "ytdlp": "y", "problem": None})):
            p = mock.patch.object(morpheus, target, value)
            p.start()
            self.addCleanup(p.stop)

    def _write(self, rows):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"playlists": rows}, f)

    def _build(self, parent=None):
        from panels.morpheus_panel import MorpheusPanel
        try:
            panel = MorpheusPanel(parent or self.root)
        except Exception as e:
            self.fail(f"MorpheusPanel() raised {type(e).__name__}: {e}")
        panel.pack(fill="both", expand=True)
        self.addCleanup(panel.destroy)
        return panel

    def _rows(self, panel):
        return getattr(panel._pl_scroll, "body", None) or panel._pl_scroll.inner


class TestAPlaylistRowItCantUse(_RealPlaylistsPanel):
    """A row that wasn't an object stopped the panel, and so Felhaven, from
    opening (measured 2026-11-23)."""

    def test_the_row_is_skipped_and_removal_still_hits_the_row_shown(self):
        self._write(["junk", {"label": "B", "url": "https://e.example/b"},
                     {"label": "C", "url": "https://e.example/c"}])
        # Clicks reach only a mapped window: a Toplevel made invisible.
        top = tk.Toplevel(self.root)
        top.attributes("-alpha", 0.0)
        top.overrideredirect(True)
        top.geometry("500x400+0+0")
        self.addCleanup(top.destroy)
        panel = self._build(top)
        top.update()
        labels = [w.cget("text") for row in self._rows(panel).winfo_children()
                  for w in row.winfo_children() if isinstance(w, tk.Label)]
        self.assertIn("B", labels)
        self.assertIn("C", labels)
        # Remove "C" through its own glyph: two clicks, file position 2.
        c_row = [row for row in self._rows(panel).winfo_children()
                 if "C" in [w.cget("text") for w in row.winfo_children()]][0]
        glyph = [w for w in c_row.winfo_children() if w.cget("text") == "✕"][0]
        glyph.event_generate("<Button-1>")
        glyph.event_generate("<Button-1>")
        with open(self.path, encoding="utf-8") as f:
            left = json.load(f)["playlists"]
        self.assertEqual(left, ["junk", {"label": "B", "url": "https://e.example/b"}])


class TestTheWheelOverThePlaylists(_RealPlaylistsPanel):
    """The hand-rolled canvas dropped the wheel on <Leave>, which Tk sends as
    the pointer moves onto a row. Pointer events need a mapped window: a
    Toplevel made invisible (the sidebar smoke test's way)."""

    def test_the_wheel_stays_bound_while_the_pointer_is_over_a_row(self):
        self._write([{"label": f"list {n}", "url": f"https://e.example/{n}"}
                     for n in range(30)])
        top = tk.Toplevel(self.root)
        top.attributes("-alpha", 0.0)
        top.overrideredirect(True)
        top.geometry("500x400+0+0")
        self.addCleanup(top.destroy)
        panel = self._build(top)
        top.update()
        scroll = panel._pl_scroll
        self.addCleanup(scroll.unbind_all, "<MouseWheel>")
        for widget in (scroll, scroll._canvas):     # the pointer enters the list
            widget.event_generate("<Enter>")
        top.update()
        self.assertTrue(scroll.bind_all("<MouseWheel>"), "the wheel never bound")
        first = self._rows(panel).winfo_children()[0]
        over = (first.winfo_rootx() + 5, first.winfo_rooty() + 5)
        with mock.patch.object(scroll, "winfo_pointerxy", return_value=over):
            scroll._canvas.event_generate("<Leave>", detail="NotifyInferior")
            top.update()
        self.assertTrue(scroll.bind_all("<MouseWheel>"), "the wheel dropped over a row")


if __name__ == "__main__":
    unittest.main(verbosity=2)
