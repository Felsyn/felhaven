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

import os
import sys
import unittest

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
        rows = [w for w in self.panel._results.inner.winfo_children()
                if isinstance(w, tk.Frame)]
        self.assertGreaterEqual(len(rows), 2)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
