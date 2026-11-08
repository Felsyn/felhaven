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
                     {"playing": False, "files": [], "error": "ffmpeg_unavailable"}):
            self.panel.update(data)
            self.root.update_idletasks()

    def test_stop_starts_disabled(self):
        self.assertEqual(self.panel._stop_btn.cget("cursor"), "arrow")

    def test_a_missing_ffmpeg_says_missing(self):
        from panels.orpheus_panel import _NO_BIN_MSG
        self.assertEqual(self.panel._no_bin_text, _NO_BIN_MSG)

    def test_a_refused_ffmpeg_says_why_not_missing(self):
        """A bin/ copy aegis refused must not read as 'not found': the fix is
        different (record a deliberate update, or replace the file)."""
        from panels.orpheus_panel import OrpheusPanel, _UNVERIFIED_MSG
        orpheus.available = lambda: {"ffmpeg": None,
                                     "problem": "binary_unverified: ffmpeg.exe does not match"}
        panel = OrpheusPanel(self.root)
        try:
            texts = [w.cget("text") for w in panel.winfo_children()
                     if isinstance(w, tk.Label)]
            self.assertIn(_UNVERIFIED_MSG, texts)
        finally:
            panel.destroy()


class _EnabledPanelCase(unittest.TestCase):
    """Shared scaffolding for the ffmpeg-'present' cases: a withdrawn root and
    a panel whose tool calls are all monkeypatched. Holds no tests itself, so
    the concrete classes below don't re-run each other's."""

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
        self._orig = (orpheus.available, orpheus.play_file, orpheus.stop,
                      orpheus.delete_file)
        orpheus.available = lambda: {"ffmpeg": "ffmpeg.exe"}
        orpheus.play_file = lambda name: {"playing": name}
        orpheus.stop = lambda: None
        # Records every delete so a test can assert one did (or didn't) happen.
        # Nothing here ever reaches the real Recycle Bin.
        self.deleted = []
        orpheus.delete_file = lambda name: (self.deleted.append(name)
                                            or {"deleted": name})
        self.panel = OrpheusPanel(self.root)
        self.panel.pack()

    def tearDown(self):
        (orpheus.available, orpheus.play_file, orpheus.stop,
         orpheus.delete_file) = self._orig
        self.panel.destroy()

    def _row_frames(self):
        """The per-file row containers — distinguished from the 1px divider
        Frames by background color (card vs. border)."""
        return [w for w in self.panel._scroll.inner.winfo_children()
                if isinstance(w, tk.Frame) and str(w.cget("bg")) == theme.C["card"]]


class TestOrpheusPanelEnabled(_EnabledPanelCase):
    """ffmpeg 'present' (monkeypatched): full transport + file list render."""

    def test_enabled(self):
        self.assertTrue(self.panel._enabled)

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
        # The ✕ is packed first so it lands on the right, hence child 0.
        labels = [[c.cget("text") for c in row.winfo_children()] for row in rows]
        self.assertIn(["✕", "a.opus", "1:15"], labels)
        self.assertIn(["✕", "b.opus", "duration unknown"], labels)

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


class TestOrpheusPanelRemove(_EnabledPanelCase):
    """The two-click remove glyph. Handlers are driven directly rather than by
    synthesised Tk events, the way the play tests above drive _on_play."""

    def _render(self, *names):
        self.panel.update({"playing": False, "files": [
            {"name": n, "duration": 10.0} for n in names]})
        self.root.update_idletasks()

    def _glyph(self, index=0):
        """The ✕ Label of row `index` — child 0, packed before the title."""
        return self._row_frames()[index].winfo_children()[0]

    def test_every_row_has_a_disarmed_glyph(self):
        self._render("a.opus", "b.opus")
        for i in (0, 1):
            self.assertEqual(self._glyph(i).cget("text"), "✕")
            self.assertFalse(self._glyph(i)._armed)

    def test_first_click_arms_and_deletes_nothing(self):
        self._render("a.opus")
        glyph = self._glyph()
        self.panel._on_remove_file("a.opus", glyph)
        self.assertEqual(glyph.cget("text"), "remove?")
        self.assertEqual(str(glyph.cget("fg")), theme.C["amber"])
        self.assertTrue(glyph._armed)
        self.assertEqual(self.deleted, [])
        self.assertEqual(len(self._row_frames()), 1)

    def test_second_click_deletes_and_drops_the_row(self):
        self._render("a.opus", "b.opus")
        glyph = self._glyph(0)
        self.panel._on_remove_file("a.opus", glyph)
        self.panel._on_remove_file("a.opus", glyph)
        self.root.update_idletasks()

        self.assertEqual(self.deleted, ["a.opus"])
        self.assertEqual(self.panel._status.cget("text"), "removed")
        rows = self._row_frames()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].winfo_children()[1].cget("text"), "b.opus")

    def test_hover_is_suppressed_while_armed(self):
        """The amber prompt must survive the mouse leaving the glyph."""
        self._render("a.opus")
        glyph = self._glyph()
        self.panel._on_remove_file("a.opus", glyph)
        self.panel._glyph_hover(glyph, theme.C["text3"])   # <Leave>
        self.assertEqual(str(glyph.cget("fg")), theme.C["amber"])

    def test_disarm_reverts_an_armed_glyph(self):
        self._render("a.opus")
        glyph = self._glyph()
        self.panel._on_remove_file("a.opus", glyph)
        self.panel._disarm_remove(glyph)
        self.assertEqual(glyph.cget("text"), "✕")
        self.assertEqual(str(glyph.cget("fg")), theme.C["text3"])
        self.assertFalse(glyph._armed)
        self.assertEqual(self.deleted, [])

    def test_disarm_on_a_destroyed_glyph_is_a_no_op(self):
        """A confirmed delete destroys the glyph before the 2.5 s callback
        fires — the winfo_exists() guard is what keeps that from raising."""
        self._render("a.opus")
        glyph = self._glyph()
        self.panel._on_remove_file("a.opus", glyph)
        glyph.destroy()
        self.panel._disarm_remove(glyph)   # must not raise TclError

    def test_delete_failure_keeps_the_row_and_disarms(self):
        orpheus.delete_file = lambda name: {"error": "delete_failed"}
        self._render("a.opus")
        glyph = self._glyph()
        self.panel._on_remove_file("a.opus", glyph)
        self.panel._on_remove_file("a.opus", glyph)
        self.root.update_idletasks()

        self.assertEqual(len(self._row_frames()), 1)
        self.assertEqual(glyph.cget("text"), "✕")
        self.assertFalse(glyph._armed)
        self.assertIn("could not delete", self.panel._status.cget("text"))
        self.assertEqual(str(self.panel._status.cget("fg")), theme.C["amber"])

    def test_flash_clear_does_not_wipe_a_newer_message(self):
        """_status is shared with _on_play, so a lapsed flash timer must not
        blank an error raised after it."""
        self.panel._flash("removed", ok=True)
        self.panel._status.config(text="something newer")
        self.panel._clear_flash("removed")
        self.assertEqual(self.panel._status.cget("text"), "something newer")


if __name__ == "__main__":
    unittest.main(verbosity=2)
