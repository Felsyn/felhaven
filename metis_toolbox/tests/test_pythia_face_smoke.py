"""
test_pythia_face_smoke.py — the wireframe face's contract, headless.

Two halves, deliberately separate:

  * the geometry helpers are pure functions with no tk in them, so they are
    tested as arithmetic — the silhouette either closes at the crown and chin
    or it does not, and that needs no window to find out;
  * the widget itself gets a withdrawn tk.Tk() root and update_idletasks(),
    the test_home_panel_smoke approach (no window flashes, no mainloop).

The one behaviour worth naming: set_state() must be a no-op when the state has
not changed. HomePanel pushes it every 60 ms off its drain chain, and the
non-idempotent version reset the syllable envelope on every call, which froze
the speaking animation at zero amplitude. That is asserted here, not assumed.

    python -X utf8 -m unittest tests.test_pythia_face_smoke
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
from panels import pythia_face
from panels.pythia_face import PythiaFace, head_half_width, neck_half_width

_HEX = re.compile(r"^#[0-9a-f]{6}$")


class TestGeometryHelpers(unittest.TestCase):
    """No tk here — these are the shape, and the shape is arithmetic."""

    def test_head_closes_to_nothing_at_crown_and_chin(self):
        for t in (-1.0, 1.0, -1.5, 2.0):
            self.assertEqual(head_half_width(100 + t * 50, 100, 40, 50), 0.0)

    def test_head_is_widest_near_the_middle(self):
        widths = [head_half_width(y, 100, 40, 50) for y in range(51, 150)]
        self.assertGreater(max(widths), 39.0)
        self.assertLess(max(widths), 40.1)

    def test_jaw_is_narrower_than_the_matching_height_above_centre(self):
        # The taper is what stops it reading as an egg.
        above = head_half_width(100 - 30, 100, 40, 50)
        below = head_half_width(100 + 30, 100, 40, 50)
        self.assertLess(below, above)

    def test_neck_flares_downward_and_never_inverts(self):
        widths = [neck_half_width(y, 100, 200, 40) for y in range(100, 201, 5)]
        self.assertEqual(widths, sorted(widths))
        self.assertGreater(widths[-1], widths[0])

    def test_neck_clamps_outside_its_own_span(self):
        self.assertEqual(neck_half_width(50, 100, 200, 40),
                         neck_half_width(100, 100, 200, 40))
        self.assertEqual(neck_half_width(999, 100, 200, 40),
                         neck_half_width(200, 100, 200, 40))


class TestPaletteComesFromTheme(unittest.TestCase):
    """§7: no hex literals in a panel. Every colour must trace back to theme.C,
    and the ramps must bottom out at the background they blend toward — if they
    don't, the widget renders as a visible box on the panel."""

    def test_signal_colours_are_the_theme_ones(self):
        self.assertEqual(pythia_face.BG, theme.C["bg"])
        self.assertEqual(pythia_face._hex(pythia_face.GREEN), theme.C["text1"].lower())
        self.assertEqual(pythia_face._hex(pythia_face.AMBER), theme.C["amber"].lower())

    def test_every_ramp_step_is_a_valid_hex_colour(self):
        for ramp in pythia_face.RAMPS:
            for colour in ramp:
                self.assertRegex(colour, _HEX)

    def test_each_ramp_starts_at_the_background(self):
        for ramp in pythia_face.RAMPS:
            self.assertEqual(ramp[0], theme.C["bg"].lower())

    def test_the_ramp_bank_spans_green_to_amber(self):
        self.assertEqual(pythia_face.GREEN_RAMP, pythia_face.RAMPS[0])
        self.assertEqual(pythia_face.AMBER_RAMP, pythia_face.RAMPS[-1])
        self.assertNotEqual(pythia_face.GREEN_RAMP[-1], pythia_face.AMBER_RAMP[-1])


class TestFaceWidget(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _face(self, **kw) -> PythiaFace:
        face = PythiaFace(self.root, **kw)
        self.root.update_idletasks()
        return face

    def test_it_draws_something_at_the_header_size(self):
        face = self._face()
        self.assertTrue(face.find_all())
        self.assertTrue(face._wave and face._rim and face._halo and face._tendrils)

    def test_unknown_state_is_refused(self):
        face = self._face()
        with self.assertRaises(ValueError):
            face.set_state("sulking")
        self.assertEqual(face.state, "idle")

    def test_repeating_a_state_does_not_restart_the_syllable_envelope(self):
        # The 60 Hz-push trap: a non-idempotent set_state pins _syl at 0 and
        # the speaking animation never swells.
        face = self._face()
        face.set_state("speaking")
        face._syl = 4.2
        face.set_state("speaking")
        self.assertEqual(face._syl, 4.2)

    def test_a_real_state_change_does_restart_it(self):
        face = self._face()
        face.set_state("speaking")
        face._syl = 4.2
        face.set_state("thinking")
        self.assertEqual(face._syl, 0.0)
        self.assertEqual(face._target, PythiaFace.STATES["thinking"][0])

    def test_set_level_clamps_and_none_hands_control_back(self):
        face = self._face()
        face.set_level(5.0)
        self.assertEqual(face._external_level, 1.0)
        face.set_level(-2.0)
        self.assertEqual(face._external_level, 0.0)
        face.set_level(None)
        self.assertIsNone(face._external_level)

    def test_a_tick_moves_the_level_toward_the_state_target(self):
        face = self._face()
        face.set_state("speaking")
        face._running = True
        before = face.level
        face._tick()
        face.stop()
        self.assertGreater(face.level, before)

    def test_stop_is_idempotent_and_start_does_not_double_schedule(self):
        face = self._face()
        face.start()
        face.start()
        self.assertTrue(face._running)
        face.stop()
        face.stop()
        self.assertFalse(face._running)
        self.assertIsNone(face._job)


if __name__ == "__main__":
    unittest.main()
