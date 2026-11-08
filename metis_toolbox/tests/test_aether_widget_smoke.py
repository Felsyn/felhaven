"""
test_aether_widget_smoke.py — headless Tk smoke test for AetherWidget.

Scoped to AetherWidget only (BarMeter/VitalsPanel are session 22). Drives the
widget with every connectivity state and with None (the Kairos-stale case).
No window flashes: tk.Tk() + withdraw() + update_idletasks(), no mainloop.
No exception == pass. Run from the package root:
    python -X utf8 -m unittest tests.test_aether_widget_smoke
"""

import os
import sys
import unittest
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import theme
from panels.hephaestus_panel import AetherWidget


class TestAetherWidgetSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()          # no window flashes during the run
        theme._init_fonts(cls.root)  # FONTS is empty until a root exists

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _widget(self):
        w = AetherWidget(self.root)
        self.root.update_idletasks()
        return w

    def test_constructs_without_error(self):
        self._widget()               # __init__ must not raise

    def test_update_none_shows_placeholder(self):
        # Kairos delivers None on a stale tick; the widget must fall back, not blank
        # and crash (CONVENTIONS S7 — every update(data) handles None).
        w = self._widget()
        w.update(None)
        self.root.update_idletasks()
        for lbl in (w._wifi_lbl, w._internet_lbl, w._ollama_lbl):
            self.assertEqual(lbl.cget("text"), "—")        # em dash placeholder

    def test_update_every_state(self):
        w = self._widget()
        for wifi in ("connected", "disconnected", "unknown"):
            for internet in ("online", "limited", "offline", "unknown"):
                for ollama in ("online", "offline", "unknown"):
                    with self.subTest(wifi=wifi, internet=internet, ollama=ollama):
                        w.update({"wifi": wifi, "internet": internet,
                                  "ollama": ollama})
                        self.root.update_idletasks()
                        self.assertEqual(w._wifi_lbl.cget("text"), wifi)
                        self.assertEqual(w._internet_lbl.cget("text"), internet)
                        self.assertEqual(w._ollama_lbl.cget("text"), ollama)

    def test_only_a_known_bad_state_is_red(self):
        # Red is reserved for a KNOWN-bad link; "unknown" stays dim and a
        # captive portal is amber, not an alarm.
        from theme import C
        w = self._widget()
        dot = lambda canvas: canvas.itemcget(canvas.find_all()[0], "fill")
        w.update({"wifi": "connected", "internet": "limited", "ollama": "unknown"})
        self.assertEqual(dot(w._internet_dot), C["amber"])
        self.assertEqual(dot(w._ollama_dot), C["text3"])
        w.update({"wifi": "connected", "internet": "offline", "ollama": "offline"})
        self.assertEqual(dot(w._internet_dot), C["red"])
        self.assertEqual(dot(w._ollama_dot), C["red"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
