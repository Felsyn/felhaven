"""
test_voice_bar_smoke.py — headless Tk smoke test for the shared VoiceBar.

Real tk.Tk() root, withdraw()n so no window flashes, driven with
update_idletasks() instead of mainloop() (CONVENTIONS §10).

This asserts the widget's actual contract rather than "it constructed": the menu
shows LABELS but current() and on_change speak in IDS, and that one-way mapping
is the only real logic in the file. A test that merely built the bar would pass
with the mapping inverted.

    python -X utf8 -m unittest tests.test_voice_bar_smoke
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
import voices
from panels.voice_bar_panel import VoiceBar


class TestVoiceBarSmoke(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)       # FONTS is empty until a root exists

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.picks = []
        self.bar = VoiceBar(self.root, initial="af_nicole",
                            on_change=self.picks.append)
        self.bar.pack()
        self.root.update_idletasks()

    def tearDown(self):
        self.bar.destroy()

    def test_starts_on_the_initial_voice(self):
        self.assertEqual(self.bar.current(), "af_nicole")

    def test_menu_shows_the_label_not_the_id(self):
        self.assertEqual(self.bar._var.get(), "Nicole — American female")

    def test_picking_reports_the_id_not_the_label(self):
        self.bar._on_pick(voices.label("bm_george"))
        self.assertEqual(self.picks, ["bm_george"])
        self.assertEqual(self.bar.current(), "bm_george")

    def test_every_catalog_voice_is_pickable(self):
        # Guards the {label: id} dict against a collision or a stale label.
        for v in voices.CATALOG:
            self.bar._on_pick(voices.label(v))
            self.assertEqual(self.bar.current(), v)
        self.assertEqual(self.picks, list(voices.CATALOG))

    def test_an_unknown_label_keeps_the_current_voice(self):
        self.bar._on_pick("not a voice")
        self.assertEqual(self.bar.current(), "af_nicole")

    def test_an_unknown_initial_falls_back_to_the_first_voice(self):
        bar = VoiceBar(self.root, initial="jf_alpha", on_change=lambda v: None)
        self.addCleanup(bar.destroy)
        self.assertEqual(bar.current(), voices.CATALOG[0])

    def test_two_bars_are_independent(self):
        # The whole point of the module: the MACHINE SPIRIT bar and the ECHO bar
        # must not share state.
        other_picks = []
        other = VoiceBar(self.root, initial="am_adam",
                         on_change=other_picks.append)
        self.addCleanup(other.destroy)
        self.bar._on_pick(voices.label("bf_emma"))
        self.assertEqual(self.bar.current(), "bf_emma")
        self.assertEqual(other.current(), "am_adam")
        self.assertEqual(other_picks, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
