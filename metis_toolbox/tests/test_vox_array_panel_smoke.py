"""
test_vox_array_panel_smoke.py — headless Tk smoke test for the Vox Array host
and the Echo tab body.

Same approach as test_morpheus_panel_smoke.py: a real tk.Tk() root we withdraw()
(no window flashes) and drive with update()/update_idletasks() instead of
mainloop(). No exception raised == pass. Morpheus is forced to its disabled
(no-binary) path so the test never depends on mpv/yt-dlp being installed; Echo's
conversion is mocked so no kokoro model or ffmpeg is touched.

    python -X utf8 -m unittest tests.test_vox_array_panel_smoke
"""

import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
from tools import morpheus
from tools import echo


class TestVoxArrayPanelSmoke(unittest.TestCase):
    """Host builds, exposes both tab bodies, and switches tabs without crashing."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        from panels.vox_array_panel import VoxArrayPanel
        from panels.morpheus_panel import MorpheusPanel
        from panels.echo_panel import EchoPanel
        # Force Morpheus's disabled path regardless of the host machine.
        self._orig_avail = morpheus.available
        morpheus.available = lambda: {"mpv": None, "ytdlp": None}
        self.MorpheusPanel = MorpheusPanel
        self.EchoPanel = EchoPanel
        self.panel = VoxArrayPanel(self.root)
        self.panel.pack()

    def tearDown(self):
        morpheus.available = self._orig_avail
        self.panel.destroy()

    def test_builds_with_both_tab_bodies(self):
        self.assertIsInstance(self.panel.morpheus, self.MorpheusPanel)
        self.assertIsInstance(self.panel.echo, self.EchoPanel)

    def test_starts_on_morpheus_tab(self):
        self.assertEqual(self.panel._active, "morpheus")

    def test_tab_switch(self):
        self.panel._show_tab("echo")
        self.root.update_idletasks()
        self.assertEqual(self.panel._active, "echo")
        self.panel._show_tab("morpheus")
        self.root.update_idletasks()
        self.assertEqual(self.panel._active, "morpheus")

    def test_morpheus_child_still_polls_when_hidden(self):
        # The Kairos-registered child keeps working regardless of which tab is
        # visible (Moderati precedent). update() on the disabled child is a no-op.
        self.panel._show_tab("echo")
        self.panel.morpheus.update({"running": True, "title": "X", "pos": 1, "dur": 2})
        self.root.update_idletasks()


class TestEchoPanelSmoke(unittest.TestCase):
    """The Echo tab body: gating, single-flight, and the daemon+drain round-trip."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        from panels.echo_panel import EchoPanel
        self.panel = EchoPanel(self.root)
        self.panel.pack()

    def tearDown(self):
        self.panel.destroy()

    def _fill(self, text="hello world", name="out"):
        self.panel._text.delete("1.0", "end")
        self.panel._text.insert("1.0", text)
        self.panel._fname_var.set(name)
        self.panel._refresh_gate()

    def test_button_gated_until_both_fields_ready(self):
        # The button switches cursor hand2 (enabled) <-> arrow (disabled).
        # Nothing entered → disabled.
        self.assertEqual(self.panel._send_btn.cget("cursor"), "arrow")
        # Only text → still disabled (filename sanitises to empty).
        self._fill(text="hi", name="")
        self.assertEqual(self.panel._send_btn.cget("cursor"), "arrow")
        # A filename that is all-illegal also stays disabled.
        self._fill(text="hi", name="///")
        self.assertEqual(self.panel._send_btn.cget("cursor"), "arrow")
        # Both usable → enabled.
        self._fill(text="hi", name="out")
        self.assertEqual(self.panel._send_btn.cget("cursor"), "hand2")

    def test_single_flight_guard(self):
        self._fill()
        self.panel._converting = True   # pretend a conversion is in flight
        with mock.patch.object(echo, "text_to_audio") as m:
            self.panel._on_send()
            m.assert_not_called()

    def test_success_round_trip(self):
        self._fill()
        with mock.patch.object(echo, "text_to_audio",
                               return_value={"path": "C:\\x\\out.opus"}):
            self.panel._on_send()
            self._pump_until(lambda: not self.panel._converting)
        self.assertFalse(self.panel._converting)
        self.assertEqual(self.panel._status.cget("fg"), theme.C["green"])
        self.assertIn("out.opus", self.panel._status.cget("text"))
        self.assertIsNone(self.panel._drain_after_id)   # chain stopped itself

    def test_error_round_trip_is_red(self):
        self._fill()
        with mock.patch.object(echo, "text_to_audio",
                               return_value={"error": "ffmpeg_unavailable"}):
            self.panel._on_send()
            self._pump_until(lambda: not self.panel._converting)
        self.assertEqual(self.panel._status.cget("fg"), theme.C["red"])
        self.assertIsNone(self.panel._drain_after_id)

    def _pump_until(self, cond, timeout=3.0):
        """Drive the Tk event loop (processes after() callbacks) until cond()."""
        deadline = time.time() + timeout
        while not cond() and time.time() < deadline:
            self.root.update()
            time.sleep(0.02)


if __name__ == "__main__":
    unittest.main(verbosity=2)
