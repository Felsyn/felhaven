"""
test_machine_spirit_panel_smoke.py — headless Tk smoke test for
MachineSpiritPanel.

Same approach as test_themis_panel_smoke.py: a withdrawn tk.Tk() root driven
with update_idletasks() (no window flashes, no mainloop).
machine_spirit._DATA_PATH is redirected at a temp file so the real
machine_spirit_config.json is never touched. No exception raised == pass.

    python -X utf8 -m unittest tests.test_machine_spirit_panel_smoke
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
import machine_spirit
from panels.machine_spirit_panel import MachineSpiritPanel


class TestMachineSpiritPanelSmoke(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self._orig = machine_spirit._DATA_PATH
        self._dir = tempfile.mkdtemp(prefix="machine_spirit_panel_")
        machine_spirit._DATA_PATH = os.path.join(self._dir, "machine_spirit_config.json")

    def tearDown(self):
        machine_spirit._DATA_PATH = self._orig
        for f in os.listdir(self._dir):
            os.unlink(os.path.join(self._dir, f))
        os.rmdir(self._dir)

    def test_editor_prefilled_with_effective_prompt(self):
        panel = MachineSpiritPanel(self.root)
        self.root.update_idletasks()
        self.assertEqual(panel._editor.get("1.0", "end").strip(),
                         machine_spirit.DEFAULT_SYSTEM_PROMPT)

    def test_save_persists_override_and_takes_effect_next_load(self):
        panel = MachineSpiritPanel(self.root)
        self.root.update_idletasks()
        panel._editor.delete("1.0", "end")
        panel._editor.insert("1.0", "Speak only in haiku.")
        panel._on_save()
        self.root.update_idletasks()
        self.assertEqual(machine_spirit.effective_prompt(), "Speak only in haiku.")

    def test_revert_previews_default_without_committing(self):
        machine_spirit.save("custom prompt")
        panel = MachineSpiritPanel(self.root)
        self.root.update_idletasks()
        panel._on_revert()
        self.root.update_idletasks()
        self.assertEqual(panel._editor.get("1.0", "end").strip(),
                         machine_spirit.DEFAULT_SYSTEM_PROMPT)
        # Not committed yet — the saved override is untouched until Save.
        self.assertEqual(machine_spirit.effective_prompt(), "custom prompt")

        panel._on_save()
        self.root.update_idletasks()
        self.assertEqual(machine_spirit.effective_prompt(),
                         machine_spirit.DEFAULT_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
