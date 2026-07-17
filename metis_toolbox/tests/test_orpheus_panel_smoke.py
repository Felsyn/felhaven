"""
test_orpheus_panel_smoke.py — headless Tk smoke test for OrpheusPanel.

Same approach as test_morpheus_panel_smoke.py: a real tk.Tk() root we
withdraw() (no window flashes) and drive with update()/update_idletasks()
instead of mainloop(). No exception raised == pass.

ffmpeg is not assumed present. The DISABLED path (no ffmpeg) is tested
against a monkeypatched orpheus.available(); the ENABLED path monkeypatches
available()/play_file()/stop() so the full transport + file list render runs
without ffmpeg actually being installed.

    python -X utf8 -m unittest tests.test_orpheus_panel_smoke
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
from tools import orpheus


class TestOrpheusPanelDisabled(unittest.TestCase):
    """No ffmpeg: placeholder shown, controls inert, update() is a no-op."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        from panels.orpheus_panel import OrpheusPanel
        self._orig_avail = orpheus.available
        orpheus.available = lambda: {"ffmpeg": None}
        self.panel = OrpheusPanel(self.root)
        self.panel.pack()

    def tearDown(self):
        orpheus.available = self._orig_avail
        self.panel.destroy()

    def test_disabled_no_crash_on_update(self):
        self.assertFalse(self.panel._enabled)
        for data in (None, {"playing": False, "files": []},
                     {"playing": False, "files": [], "error": "no_ffmpeg"}):
            self.panel.update(data)
            self.root.update_idletasks()

    def test_stop_starts_disabled(self):
        self.assertEqual(self.panel._stop_btn.cget("cursor"), "arrow")


class TestOrpheusPanelEnabled(unittest.TestCase):
    """ffmpeg 'present' (monkeypatched): full transport + file list render."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        from panels.orpheus_panel import OrpheusPanel
        self._orig = (orpheus.available, orpheus.play_file, orpheus.stop)
        orpheus.available = lambda: {"ffmpeg": "ffmpeg.exe"}
        orpheus.play_file = lambda name: {"playing": name}
        orpheus.stop = lambda: None
        self.panel = OrpheusPanel(self.root)
        self.panel.pack()

    def tearDown(self):
        (orpheus.available, orpheus.play_file, orpheus.stop) = self._orig
        self.panel.destroy()

    def test_enabled(self):
        self.assertTrue(self.panel._enabled)

    def _row_frames(self):
        """The per-file row containers — distinguished from the 1px divider
        Frames by background color (card vs. border)."""
        return [w for w in self.panel._scroll.inner.winfo_children()
                if isinstance(w, tk.Frame) and str(w.cget("bg")) == theme.C["card"]]

    def test_file_list_renders_and_updates_on_change(self):
        self.panel.update({"playing": False, "files": [
            {"name": "a.opus", "duration": 75.0},
            {"name": "b.opus", "duration": None},
        ]})
        self.root.update_idletasks()
        self.assertEqual(len(self._row_frames()), 2)

    def test_duration_displayed_per_file(self):
        self.panel.update({"playing": False, "files": [
            {"name": "a.opus", "duration": 75.0},
            {"name": "b.opus", "duration": None},
        ]})
        self.root.update_idletasks()
        rows = self._row_frames()
        labels = [[c.cget("text") for c in row.winfo_children()] for row in rows]
        self.assertIn(["a.opus", "1:15"], labels)
        self.assertIn(["b.opus", "duration unknown"], labels)

    def test_empty_file_list_shows_hint(self):
        self.panel.update({"playing": False, "files": []})
        self.root.update_idletasks()
        rows = [w for w in self.panel._scroll.inner.winfo_children()
                if isinstance(w, tk.Label)]
        self.assertEqual(len(rows), 1)
        self.assertIn("no files", rows[0].cget("text"))

    def test_playing_flips_transport_and_stop_button(self):
        self.panel.update({"playing": False, "files": []})
        self.root.update_idletasks()
        self.assertEqual(self.panel._now_lbl.cget("text"), "nothing playing")
        self.assertEqual(self.panel._stop_btn.cget("cursor"), "arrow")

        self.panel.update({"playing": True, "files": []})
        self.root.update_idletasks()
        self.assertNotEqual(self.panel._now_lbl.cget("text"), "nothing playing")
        self.assertEqual(self.panel._stop_btn.cget("cursor"), "hand2")

        self.panel.update({"playing": False, "files": []})
        self.root.update_idletasks()
        self.assertEqual(self.panel._now_lbl.cget("text"), "nothing playing")
        self.assertEqual(self.panel._stop_btn.cget("cursor"), "arrow")

    def test_click_row_plays_and_sets_now_playing(self):
        self.panel.update({"playing": False, "files": [{"name": "a.opus", "duration": 10.0}]})
        self.root.update_idletasks()
        self.panel._on_play("a.opus")
        self.assertEqual(self.panel._now_lbl.cget("text"), "playing: a.opus")
        self.assertEqual(self.panel._stop_btn.cget("cursor"), "hand2")

    def test_click_row_error_shows_red_status(self):
        orpheus.play_file = lambda name: {"error": "decode_failed"}
        self.panel.update({"playing": False, "files": [{"name": "a.opus", "duration": 10.0}]})
        self.root.update_idletasks()
        self.panel._on_play("a.opus")
        self.assertEqual(self.panel._status.cget("fg"), theme.C["red"])

    def test_stop_click_resets_transport(self):
        self.panel.update({"playing": True, "files": []})
        self.root.update_idletasks()
        self.panel._on_stop()
        self.assertEqual(self.panel._now_lbl.cget("text"), "nothing playing")
        self.assertEqual(self.panel._stop_btn.cget("cursor"), "arrow")


if __name__ == "__main__":
    unittest.main(verbosity=2)
