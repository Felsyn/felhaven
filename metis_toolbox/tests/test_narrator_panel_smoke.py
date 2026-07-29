"""
test_narrator_panel_smoke.py — headless Tk smoke test for NarratorLamp (the
Pythia narration toggle) and the draw_speaker seam.

Same approach as test_home_panel_smoke.py: a withdrawn tk.Tk() root driven with
update_idletasks() (no window flashes, no mainloop). Fully hermetic — Calliope's
auto-speak flag is just a module int, so toggling it makes NO audio and loads NO
model. Each test resets the flag in setUp/tearDown so the module-global never
leaks between tests (or into test_home_panel_smoke, which snapshots it).

The lit/dim state is asserted through the canvas items draw_speaker emits under
the "spk" tag: ON draws sound-wave *arcs*, OFF draws a single mute-slash *line* —
so the branch is really exercised, not merely "constructed without crashing."

    python -X utf8 -m unittest tests.test_narrator_panel_smoke
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import calliope
import theme
from panels.narrator_panel import NarratorLamp, draw_speaker


def _spk_types(canvas: tk.Canvas) -> set:
    """The set of canvas item types tagged 'spk' — 'arc' iff lit (sound waves),
    'line' iff dim (the mute slash); 'rectangle'/'polygon' (cabinet+cone) always."""
    return {canvas.type(i) for i in canvas.find_withtag("spk")}


class TestNarratorLampSmoke(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        calliope.set_auto_speak(False)

    def tearDown(self):
        calliope.set_auto_speak(False)

    # ── Rendering reflects the flag ──────────────────────────────────────────

    def test_dim_lamp_shows_mute_slash_and_no_waves(self):
        lamp = NarratorLamp(self.root)
        self.root.update_idletasks()
        types = _spk_types(lamp)
        self.assertIn("line", types)       # mute slash
        self.assertNotIn("arc", types)     # no sound waves

    def test_lit_lamp_shows_waves_and_no_mute_slash(self):
        calliope.set_auto_speak(True)
        lamp = NarratorLamp(self.root)
        self.root.update_idletasks()
        types = _spk_types(lamp)
        self.assertIn("arc", types)        # sound waves
        self.assertNotIn("line", types)    # no mute slash

    # ── The click toggles Calliope's flag and re-renders from it ─────────────

    def test_click_flips_flag_and_rerenders_each_time(self):
        lamp = NarratorLamp(self.root)
        self.root.update_idletasks()
        self.assertNotIn("arc", _spk_types(lamp))     # starts dim

        lamp._on_click(None)                          # → on
        self.root.update_idletasks()
        self.assertTrue(calliope.auto_speak_enabled())
        self.assertIn("arc", _spk_types(lamp))        # now lit

        lamp._on_click(None)                          # → off
        self.root.update_idletasks()
        self.assertFalse(calliope.auto_speak_enabled())
        self.assertIn("line", _spk_types(lamp))       # dim again

    def test_render_reads_the_real_flag_not_a_cached_copy(self):
        # Single source of truth: flip the flag underneath the lamp, then
        # re-render — it must reflect Calliope's flag, never a local guess.
        lamp = NarratorLamp(self.root)
        self.root.update_idletasks()
        self.assertNotIn("arc", _spk_types(lamp))

        calliope.set_auto_speak(True)                 # changed externally
        lamp._render()
        self.root.update_idletasks()
        self.assertIn("arc", _spk_types(lamp))

    # ── The public draw_speaker seam, independent of the flag ────────────────

    def test_draw_speaker_seam_is_redraw_safe(self):
        c = tk.Canvas(self.root)
        draw_speaker(c, 15, 15, size=20, on=True)
        self.root.update_idletasks()
        self.assertIn("arc", _spk_types(c))

        # A redraw to the other state must clear the old "spk" items (delete("spk")).
        draw_speaker(c, 15, 15, size=20, on=False)
        self.root.update_idletasks()
        off_types = _spk_types(c)
        self.assertIn("line", off_types)              # mute slash drawn
        self.assertNotIn("arc", off_types)            # old waves cleared


if __name__ == "__main__":
    unittest.main(verbosity=2)
