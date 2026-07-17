"""
test_vox_array_panel_smoke.py — headless Tk smoke test for the Vox Array host
and the Echo tab body.

Same approach as test_morpheus_panel_smoke.py: a real tk.Tk() root we withdraw()
(no window flashes) and drive with update()/update_idletasks() instead of
mainloop(). No exception raised == pass. Morpheus and Orpheus are both forced
to their disabled (no-binary) path so the test never depends on mpv/yt-dlp/
ffmpeg being installed; Echo's conversion is mocked so no kokoro model or
ffmpeg is touched.

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
from tools import orpheus


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
        from panels.orpheus_panel import OrpheusPanel
        # Force Morpheus's and Orpheus's disabled paths regardless of the host machine.
        self._orig_avail = morpheus.available
        self._orig_orpheus_avail = orpheus.available
        morpheus.available = lambda: {"mpv": None, "ytdlp": None}
        orpheus.available = lambda: {"ffmpeg": None}
        self.MorpheusPanel = MorpheusPanel
        self.EchoPanel = EchoPanel
        self.OrpheusPanel = OrpheusPanel
        self.panel = VoxArrayPanel(self.root)
        self.panel.pack()

    def tearDown(self):
        morpheus.available = self._orig_avail
        orpheus.available = self._orig_orpheus_avail
        self.panel.destroy()

    def test_builds_with_all_tab_bodies(self):
        self.assertIsInstance(self.panel.morpheus, self.MorpheusPanel)
        self.assertIsInstance(self.panel.echo, self.EchoPanel)
        self.assertIsInstance(self.panel.orpheus, self.OrpheusPanel)

    def test_starts_on_morpheus_tab(self):
        self.assertEqual(self.panel._active, "morpheus")

    def test_tab_switch(self):
        for key in ("echo", "orpheus", "morpheus"):
            self.panel._show_tab(key)
            self.root.update_idletasks()
            self.assertEqual(self.panel._active, key)

    def test_morpheus_child_still_polls_when_hidden(self):
        # The Kairos-registered child keeps working regardless of which tab is
        # visible (Moderati precedent). update() on the disabled child is a no-op.
        self.panel._show_tab("echo")
        self.panel.morpheus.update({"running": True, "title": "X", "pos": 1, "dur": 2})
        self.root.update_idletasks()

    def test_orpheus_child_still_polls_when_hidden(self):
        self.panel._show_tab("morpheus")
        self.panel.orpheus.update({"playing": False, "files": []})
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

    def test_success_clears_text_and_filename(self):
        # A successful save clears both fields so the next paste starts fresh
        # — no manual delete needed before the next conversion.
        self._fill(text="hello world", name="out")
        with mock.patch.object(echo, "text_to_audio",
                               return_value={"path": "C:\\x\\out.opus"}):
            self.panel._on_send()
            self._pump_until(lambda: not self.panel._converting)
        self.assertEqual(self.panel._text.get("1.0", "end").strip(), "")
        self.assertEqual(self.panel._fname_var.get(), "")
        self.assertEqual(self.panel._send_btn.cget("cursor"), "arrow")   # gate re-closed

    def test_error_round_trip_is_red(self):
        self._fill()
        with mock.patch.object(echo, "text_to_audio",
                               return_value={"error": "ffmpeg_unavailable"}):
            self.panel._on_send()
            self._pump_until(lambda: not self.panel._converting)
        self.assertEqual(self.panel._status.cget("fg"), theme.C["red"])
        self.assertIsNone(self.panel._drain_after_id)

    def test_error_leaves_fields_intact_for_retry(self):
        self._fill(text="hello world", name="out")
        with mock.patch.object(echo, "text_to_audio",
                               return_value={"error": "ffmpeg_unavailable"}):
            self.panel._on_send()
            self._pump_until(lambda: not self.panel._converting)
        self.assertEqual(self.panel._text.get("1.0", "end").strip(), "hello world")
        self.assertEqual(self.panel._fname_var.get(), "out")

    # ── Right-click context menus (Pythia home_panel precedent) ─────────────

    def test_select_all_then_copy_puts_text_on_clipboard(self):
        self._fill(text="hello world")
        self.panel._select_all_text()
        self.panel._text.event_generate("<<Copy>>")
        self.root.update_idletasks()
        self.assertIn("hello world", self.root.clipboard_get())

    def test_text_menu_disables_cut_copy_without_selection(self):
        self._fill(text="hello world")
        self.panel._text.tag_remove("sel", "1.0", "end")   # ensure no selection
        event = tk.Event()
        event.x_root = event.y_root = 0
        with mock.patch.object(tk.Menu, "tk_popup"), \
                mock.patch.object(tk.Menu, "grab_release"), \
                mock.patch.object(tk.Menu, "add_command") as add_cmd:
            self.panel._show_text_menu(event)
        states = {call.kwargs["label"]: call.kwargs.get("state") for call in add_cmd.call_args_list}
        self.assertEqual(states["Cut"], "disabled")
        self.assertEqual(states["Copy"], "disabled")
        self.assertIsNone(states["Paste"])          # always enabled, no explicit state
        self.assertIsNone(states["Select All"])      # always enabled

    def test_entry_menu_select_all_and_copy(self):
        self.panel._fname_var.set("out")
        self.panel._fname_entry.select_range(0, "end")
        self.panel._fname_entry.event_generate("<<Copy>>")
        self.root.update_idletasks()
        self.assertIn("out", self.root.clipboard_get())

    def _pump_until(self, cond, timeout=3.0):
        """Drive the Tk event loop (processes after() callbacks) until cond()."""
        deadline = time.time() + timeout
        while not cond() and time.time() < deadline:
            self.root.update()
            time.sleep(0.02)


if __name__ == "__main__":
    unittest.main(verbosity=2)
