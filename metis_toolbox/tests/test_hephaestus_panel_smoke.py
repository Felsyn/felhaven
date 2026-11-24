"""
test_hephaestus_panel_smoke.py — headless Tk smoke test for BarMeter + VitalsPanel.

The sibling AetherWidget in the same file is covered by test_aether_widget_smoke.py
(session 5); this closes the gap it deferred ("BarMeter/VitalsPanel are session
22"). No window flashes: tk.Tk() + withdraw() + update_idletasks(), no mainloop.
Percents in the fixture are clean integers so round() has no banker's-rounding
ambiguity. Run from the package root:
    python -X utf8 -m unittest tests.test_hephaestus_panel_smoke
"""

import os
import sys
import unittest
from unittest import mock
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import theme
from panels.hephaestus_panel import BarMeter, VitalsPanel


# A hephaestus.fetch()-shaped snapshot with clean integer percents.
_VITALS = {
    "node": "TESTBOX",
    "os": "Windows",
    "cpu": {"usage_percent": 12.0, "cores": 8, "load_avg": "N/A"},
    "memory": {"total_gb": 16.0, "available_gb": 8.0, "percent_used": 50.0},
    "storage": {"total_gb": 931.0, "free_gb": 400.0, "percent_used": 57.0},
    "timestamp": "2026-07-25T00:00:00",
}


class _TkTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()          # no window flashes during the run
        theme._init_fonts(cls.root)  # FONTS is empty until a root exists

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()


class TestBarMeter(_TkTestCase):
    """set() must clamp to 0–100 and format the special sub-1% case."""

    def _meter(self):
        m = BarMeter(self.root, "CPU")
        self.root.update_idletasks()
        return m

    def test_normal_value(self):
        m = self._meter()
        m.set(50)
        self.assertEqual(m._val.cget("text"), "50%")

    def test_clamps_above_100(self):
        m = self._meter()
        m.set(150)                                   # over-range → pinned to 100
        self.assertEqual(m._val.cget("text"), "100%")

    def test_clamps_below_zero(self):
        m = self._meter()
        m.set(-10)                                   # negative → pinned to 0
        self.assertEqual(m._val.cget("text"), "0%")

    def test_tiny_nonzero_shows_lt_one(self):
        m = self._meter()
        m.set(0.5)                                   # 0 < pct < 1 → "<1%"
        self.assertEqual(m._val.cget("text"), "<1%")


class TestVitalsPanel(_TkTestCase):

    def _panel(self):
        p = VitalsPanel(self.root)                   # builds bars + embedded Aether
        self.root.update_idletasks()
        return p

    def test_constructs_without_error(self):
        self._panel()                                # __init__ must not raise

    def test_update_none_shows_unavailable(self):
        # Kairos delivers None on a stale tick; the footer says so, the bars hold.
        p = self._panel()
        p.update(None)
        self.root.update_idletasks()
        self.assertEqual(p.node_lbl.cget("text"), "unavailable")

    def test_update_drives_the_bars_and_footer(self):
        p = self._panel()
        p.update(_VITALS)
        self.root.update_idletasks()
        self.assertEqual(p.node_lbl.cget("text"), "TESTBOX · Windows")
        self.assertEqual(p.cpu._val.cget("text"), "12%")
        self.assertEqual(p.ram._val.cget("text"), "50%")
        self.assertEqual(p.disk._val.cget("text"), "57%")


# The fetch() shape with this round's readings: the boot drive, uptime, the GPU.
_FULL = dict(
    _VITALS,
    storage={"drive": "D:\\", "total_gb": 931.0, "free_gb": 920.0, "percent_used": 1.0},
    boot_storage={"drive": "C:\\", "total_gb": 476.0, "free_gb": 195.0, "percent_used": 59.0},
    uptime_s=3 * 86400 + 4 * 3600 + 120,
    gpu={"name": "NVIDIA GeForce GTX 1660", "temp_c": 48, "memory_used_gb": 1.7,
         "memory_total_gb": 6.0, "memory_pct": 28.3, "load_pct": 12},
)


class TestTheColumn(_TkTestCase):
    """SYSTEM and GPU, from hephaestus.fetch()."""

    def _panel(self, data=_FULL):
        p = VitalsPanel(self.root)
        self.addCleanup(p.destroy)
        p.update(data)
        self.root.update_idletasks()
        return p

    def test_both_drives_say_which_they_are(self):
        p = self._panel()
        self.assertEqual(p.boot_disk._label.cget("text"), "C: · boot")
        self.assertEqual(p.boot_disk._val.cget("text"), "59%")
        self.assertEqual(p.disk._label.cget("text"), "D: · Felhaven")
        self.assertEqual(p.disk._val.cget("text"), "1%")

    def test_one_drive_is_one_row(self):
        p = self._panel(dict(_FULL, storage=dict(_FULL["storage"], drive="C:\\"),
                             boot_storage=None))
        self.assertEqual(p.boot_disk.winfo_manager(), "")          # not packed
        self.assertEqual(p.disk._label.cget("text"), "C: · boot · Felhaven")

    def test_the_node_line_carries_the_uptime(self):
        self.assertEqual(self._panel().node_lbl.cget("text"),
                         "TESTBOX · Windows · up 3 d 4 h")

    def test_uptime_reads_in_the_largest_two_units(self):
        from panels.hephaestus_panel import _uptime
        self.assertEqual(_uptime(3 * 86400 + 4 * 3600 + 59), "3 d 4 h")
        self.assertEqual(_uptime(4 * 3600 + 12 * 60), "4 h 12 min")
        self.assertEqual(_uptime(12 * 60 + 30), "12 min")
        self.assertEqual(_uptime(-5), "0 min")

    def test_the_gpu_section(self):
        p = self._panel()
        self.assertEqual(p.gpu_head.title.cget("text"), "GPU — GTX 1660")
        self.assertEqual(p._gpu_temp.cget("text"), "48°C")
        self.assertEqual(p.gpu_memory._val.cget("text"), "1.7 / 6.0 GB")
        self.assertEqual(p.gpu_load._val.cget("text"), "12%")

    def test_no_gpu_is_a_calm_line_not_empty_bars(self):
        p = self._panel(dict(_FULL, gpu={"error": "no_nvidia_gpu"}))
        self.assertEqual(p.gpu_memory.winfo_manager(), "")
        self.assertEqual(p.gpu_load.winfo_manager(), "")
        self.assertEqual(p._gpu_note.cget("text"), "No NVIDIA GPU found on this machine.")
        self.assertEqual(p.gpu_head.title.cget("text"), "GPU")
        p.update(dict(_FULL, gpu={"error": "nvml_failed: nvmlInit_v2 returned 9"}))
        self.assertIn("couldn't be read", p._gpu_note.cget("text"))

    def test_the_gpu_bars_come_back_in_their_place(self):
        p = self._panel(dict(_FULL, gpu={"error": "no_nvidia_gpu"}))
        p.update(_FULL)
        slaves = p._body.pack_slaves()
        self.assertLess(slaves.index(p.gpu_memory), slaves.index(p.gpu_load))
        self.assertLess(slaves.index(p.gpu_load), slaves.index(p.ollama))
        self.assertEqual(p._gpu_note.winfo_manager(), "")

    def test_the_scrollbar_is_packed_before_the_canvas(self):
        p = self._panel()
        slaves = p.pack_slaves()
        self.assertLess(slaves.index(p._scroll), slaves.index(p._canvas))

    def test_the_wheel_stays_bound_while_the_pointer_is_over_the_tab(self):
        """The canvas hears <Leave> as the pointer moves onto its own rows; the
        wheel must keep working there."""
        p = self._panel()
        p._bind_wheel()
        with mock.patch.object(p, "_pointer_inside", return_value=True):
            p._maybe_unbind_wheel()
        self.assertTrue(p._canvas.bind_all("<MouseWheel>"))
        with mock.patch.object(p, "_pointer_inside", return_value=False):
            p._maybe_unbind_wheel()
        self.assertFalse(p._canvas.bind_all("<MouseWheel>"))


_TALOS = {"installed": [], "loaded": [
    {"name": "gemma4:e2b", "size_gb": 1.61, "vram_gb": 1.61, "gpu_pct": 100,
     "context": 16384, "kept_loaded": True}]}


class TestOllamaWidget(_TkTestCase):
    """OLLAMA — LOADED MODEL, from talos.fetch()."""

    def _widget(self, data):
        from panels.hephaestus_panel import OllamaWidget
        w = OllamaWidget(self.root)
        self.addCleanup(w.destroy)
        w.update(data)
        return w

    def _dot(self, w):
        return w._dot.itemcget(w._dot.find_all()[0], "fill")

    def test_all_on_the_gpu(self):
        w = self._widget(_TALOS)
        self.assertEqual(w._name.cget("text"), "gemma4:e2b")
        self.assertEqual(w._share.cget("text"), "100% on GPU")
        # No GB figure: Ollama's under-counts gemma4's GPU memory (see the
        # widget); the GPU memory bar shows the true total.
        self.assertEqual(w._detail.cget("text"), "context 16,384 · kept loaded")
        self.assertNotIn("GB", w._detail.cget("text"))
        self.assertEqual(self._dot(w), theme.C["green"])

    def test_part_spilled_to_the_cpu_is_amber(self):
        w = self._widget({"loaded": [dict(_TALOS["loaded"][0], gpu_pct=62,
                                          kept_loaded=False)]})
        self.assertEqual(w._share.cget("text"), "62% on GPU")
        self.assertIn("spilled to the CPU", w._detail.cget("text"))
        self.assertEqual(self._dot(w), theme.C["amber"])

    def test_nothing_loaded(self):
        w = self._widget({"installed": [], "loaded": []})
        self.assertEqual(w._name.cget("text"), "no model loaded")
        self.assertIn("next question loads one", w._detail.cget("text"))
        # No load time: it depends on the model (gemma4:e2b about 20 s, e4b 80).
        self.assertNotIn("minute", w._detail.cget("text"))
        self.assertEqual(self._dot(w), theme.C["text3"])

    def test_unreachable(self):
        w = self._widget(None)
        self.assertEqual(w._name.cget("text"), "Ollama not reachable")
        self.assertEqual(self._dot(w), theme.C["text3"])

    def test_a_second_loaded_model_is_mentioned(self):
        second = dict(_TALOS["loaded"][0], name="other:7b")
        w = self._widget({"loaded": [_TALOS["loaded"][0], second]})
        self.assertIn("1 more loaded", w._detail.cget("text"))

    def test_an_unloading_model_says_so(self):
        w = self._widget({"loaded": [dict(_TALOS["loaded"][0], kept_loaded=False)]})
        self.assertIn("unloads when idle", w._detail.cget("text"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
