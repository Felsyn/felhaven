"""
test_cogitator_panel_smoke.py — headless Tk smoke test for the Cogitator host.

Same approach as test_vox_array_panel_smoke.py: a withdrawn tk.Tk() root driven
with update_idletasks() (no window flashes, no mainloop). No exception raised ==
pass. Cogitator's three tab bodies are self-contained; only Scribe needs the
shared data dict + save callback, so a throwaway dict and a no-op on_save keep
the test off disk entirely (Scribe never touches persistence itself — that is
felhaven's on_save; here it's a no-op that just records the call).

Nothing in Cogitator is a Kairos worker, so — unlike the Moderati host — there is
no "still polls when hidden" case: no tab body has an update().

    python -X utf8 -m unittest tests.test_cogitator_panel_smoke
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme


class TestCogitatorPanelSmoke(unittest.TestCase):
    """Host builds all three tab bodies and switches tabs without crashing."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        from panels.cogitator_panel import CogitatorPanel
        from panels.scribe_panel import ScribePanel
        from panels.zeno_panel import ZenoPanel
        from panels.eudoxus_panel import EudoxusPanel
        self.ScribePanel = ScribePanel
        self.ZenoPanel = ZenoPanel
        self.EudoxusPanel = EudoxusPanel
        # ScribePanel indexes data["tasks"] non-defensively and NotesWidget reads
        # data["notes"]; on_save records calls but persists nothing.
        self._saves: list = []
        self.data = {"tasks": [], "notes": ""}
        self.panel = CogitatorPanel(self.root, self.data,
                                    lambda d: self._saves.append(d))
        self.panel.pack()

    def tearDown(self):
        self.panel.destroy()

    def test_builds_with_all_tab_bodies(self):
        self.assertIsInstance(self.panel.scribe, self.ScribePanel)
        self.assertIsInstance(self.panel.zeno, self.ZenoPanel)
        self.assertIsInstance(self.panel.eudoxus, self.EudoxusPanel)

    def test_starts_on_scribe_tab(self):
        self.assertEqual(self.panel._active, "scribe")

    def test_tab_switch_cycles_every_tab(self):
        for key in ("zeno", "eudoxus", "scribe"):
            self.panel._show_tab(key)
            self.root.update_idletasks()
            self.assertEqual(self.panel._active, key)


if __name__ == "__main__":
    unittest.main(verbosity=2)
