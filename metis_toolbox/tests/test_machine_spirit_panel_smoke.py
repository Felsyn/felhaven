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
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
import machine_spirit
import themis
from panels.machine_spirit_panel import MachineSpiritPanel
from theme import C


class _PanelCase(unittest.TestCase):

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
        # The THINKING / TEMPERATURE rows read and write themis; never the
        # checkout's real felhaven_settings.json.
        self._orig_themis = themis._DATA_PATH
        themis._DATA_PATH = os.path.join(self._dir, "felhaven_settings.json")

    def tearDown(self):
        machine_spirit._DATA_PATH = self._orig
        themis._DATA_PATH = self._orig_themis
        for f in os.listdir(self._dir):
            os.unlink(os.path.join(self._dir, f))
        os.rmdir(self._dir)

class TestMachineSpiritPanelSmoke(_PanelCase):

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


class TestHowSheAnswers(_PanelCase):
    """THINKING and TEMPERATURE: each click saves through themis at once."""

    def _panel(self):
        panel = MachineSpiritPanel(self.root)
        self.root.update_idletasks()
        return panel

    def _lit(self, label):
        return label.cget("fg") == C["text1"]

    def test_both_start_from_the_saved_settings(self):
        panel = self._panel()
        self.assertTrue(self._lit(panel._think_labels[False]))
        self.assertFalse(self._lit(panel._think_labels[True]))
        self.assertEqual(panel._temp_value.cget("text"), "default")
        themis.save_pythia(thinking=True, temperature=0.4)
        panel = self._panel()
        self.assertTrue(self._lit(panel._think_labels[True]))
        self.assertEqual(panel._temp_value.cget("text"), "0.4")

    def test_every_control_is_clickable(self):
        """A withdrawn root delivers no generated clicks, so the handlers are
        driven directly below; this proves each control is wired to one."""
        panel = self._panel()
        for lbl in (*panel._think_labels.values(), panel._temp_down,
                    panel._temp_up, panel._temp_reset):
            self.assertTrue(lbl.bind("<Button-1>"), lbl.cget("text"))
            self.assertEqual(str(lbl.cget("cursor")), "hand2")

    def test_turning_thinking_on_saves_it(self):
        panel = self._panel()
        panel._on_thinking(True)
        self.assertIs(themis.pythia_thinking(), True)
        self.assertTrue(self._lit(panel._think_labels[True]))
        self.assertFalse(self._lit(panel._think_labels[False]))
        self.assertIn("Thinking on", panel._status.cget("text"))
        panel._on_thinking(False)
        self.assertIs(themis.pythia_thinking(), False)

    def test_the_first_step_starts_from_the_models_own(self):
        panel = self._panel()
        panel._on_step(+1)
        self.assertEqual(themis.pythia_temperature(), 1.1)
        panel._save_answer(panel._thinking, None)              # DEFAULT
        self.assertIsNone(themis.pythia_temperature())
        self.assertEqual(panel._temp_value.cget("text"), "default")
        panel._on_step(-1)
        self.assertEqual(themis.pythia_temperature(), 0.9)
        self.assertEqual(panel._temp_value.cget("text"), "0.9")

    def test_the_stepper_stops_quietly_at_each_end(self):
        """Themis would refuse a value past the end; the stepper must not ask,
        or a click at the end reads "Not saved" in red."""
        for end, direction in ((themis.TEMPERATURE_MAX, +1),
                               (themis.TEMPERATURE_MIN, -1)):
            with self.subTest(end=end):
                themis.save_pythia(thinking=False, temperature=end)
                panel = self._panel()
                panel._on_step(direction)
                self.assertEqual(themis.pythia_temperature(), end)
                self.assertNotIn("Not saved", panel._status.cget("text"))
                self.assertEqual(panel._status.cget("fg"), C["text1"])

    def test_steps_land_on_tenths(self):
        panel = self._panel()
        for _ in range(7):
            panel._on_step(-1)
        self.assertEqual(themis.pythia_temperature(), 0.3)      # not 0.30000000000000004
        self.assertEqual(panel._temperature, 0.3)               # what themis saved
        self.assertEqual(panel._temp_value.cget("text"), "0.3")

    def test_changing_one_keeps_the_other(self):
        panel = self._panel()
        panel._on_step(-1)
        panel._on_thinking(True)
        self.assertEqual(themis.pythia_temperature(), 0.9)
        panel._on_step(+1)
        self.assertIs(themis.pythia_thinking(), True)

    def test_a_failed_save_says_so_and_changes_nothing(self):
        panel = self._panel()
        with mock.patch.object(themis, "save_pythia", side_effect=OSError("disk full")):
            panel._on_thinking(True)
        self.assertFalse(panel._thinking)
        self.assertTrue(self._lit(panel._think_labels[False]))
        self.assertIn("Not saved", panel._status.cget("text"))
        self.assertEqual(panel._status.cget("fg"), C["red"])


class TestEditing(_PanelCase):
    """What every editable box has (euterpe_panel's precedent)."""

    def _menu_states(self, panel):
        event = tk.Event()
        event.x_root = event.y_root = 0
        with mock.patch.object(tk.Menu, "tk_popup"), \
                mock.patch.object(tk.Menu, "grab_release") as release, \
                mock.patch.object(tk.Menu, "add_command") as add_cmd:
            panel._show_text_menu(event)
        release.assert_called_once()                  # input never wedges
        return {call.kwargs["label"]: call.kwargs.get("state")
                for call in add_cmd.call_args_list}

    def test_right_click_opens_the_menu(self):
        panel = MachineSpiritPanel(self.root)
        self.assertTrue(panel._editor.bind("<Button-3>"))
        self.assertTrue(panel._editor.bind("<Control-a>"))

    def test_the_menu_greys_cut_and_copy_with_nothing_selected(self):
        panel = MachineSpiritPanel(self.root)
        panel._editor.tag_remove("sel", "1.0", "end")
        states = self._menu_states(panel)
        self.assertEqual(list(states), ["Cut", "Copy", "Paste", "Select All"])
        self.assertEqual(states["Cut"], "disabled")
        self.assertEqual(states["Copy"], "disabled")
        self.assertIsNone(states["Paste"])            # always enabled
        self.assertIsNone(states["Select All"])

    def test_the_menu_enables_cut_and_copy_with_a_selection(self):
        panel = MachineSpiritPanel(self.root)
        panel._select_all()
        states = self._menu_states(panel)
        self.assertEqual(states["Cut"], "normal")
        self.assertEqual(states["Copy"], "normal")

    def test_select_all_selects_everything_and_breaks_the_chain(self):
        panel = MachineSpiritPanel(self.root)
        self.assertEqual(panel._select_all(), "break")    # replaces Tk's own
        self.assertEqual(panel._editor.get("sel.first", "sel.last").strip(),
                         machine_spirit.DEFAULT_SYSTEM_PROMPT)

    def test_copy_reaches_the_clipboard(self):
        panel = MachineSpiritPanel(self.root)
        panel._select_all()
        self.root.clipboard_clear()
        panel._editor.event_generate("<<Copy>>")
        self.assertIn("You are Pythia", self.root.clipboard_get())

    def test_the_prefill_cannot_be_undone_into_an_empty_box(self):
        panel = MachineSpiritPanel(self.root)
        with self.assertRaises(tk.TclError):              # nothing to undo
            panel._editor.edit_undo()
        self.assertEqual(panel._editor.get("1.0", "end").strip(),
                         machine_spirit.DEFAULT_SYSTEM_PROMPT)

    def test_revert_is_one_undo_step(self):
        panel = MachineSpiritPanel(self.root)
        panel._editor.delete("1.0", "end")
        panel._editor.insert("1.0", "My own words.")
        panel._on_revert()
        panel._editor.edit_undo()
        self.assertEqual(panel._editor.get("1.0", "end").strip(), "My own words.")


class TestTheScrollbarSurvivesANarrowWindow(unittest.TestCase):
    """A tk.Text asks for 80 characters (574 px). Packed before its scrollbar,
    it took the whole row of a narrow window and pack unmapped the bar, at
    exactly the size where the prompt overflows. Real geometry needs a mapped
    root, so this one is mapped and invisible (alpha 0, no frame); 488 px is
    the tab's width at Felhaven's 760 px minimum window."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0.0)
        self.root.overrideredirect(True)
        theme._init_fonts(self.root)
        self.addCleanup(self.root.destroy)
        self._dir = tempfile.mkdtemp(prefix="machine_spirit_scroll_")
        for module, name in ((machine_spirit, "machine_spirit_config.json"),
                             (themis, "felhaven_settings.json")):
            patch = mock.patch.object(module, "_DATA_PATH",
                                      os.path.join(self._dir, name))
            patch.start()
            self.addCleanup(patch.stop)

    def test_the_lane_is_on_screen_while_the_prompt_overflows(self):
        self.root.geometry("488x350+40+40")
        panel = MachineSpiritPanel(self.root)
        panel.pack(fill="both", expand=True)
        self.root.update()
        if not panel.winfo_viewable():
            self.skipTest("no mapped window on this display")
        _first, last = panel._editor.yview()
        self.assertLess(last, 1.0, "the prompt fits — the case would be vacuous")
        self.assertTrue(panel._scroll.winfo_ismapped(), "the scrollbar was squeezed out")
        self.assertEqual(panel._scroll.winfo_width(), theme.PhosphorScroll.LANE)
        row = panel._editor.master
        self.assertLessEqual(panel._editor.winfo_width() + panel._scroll.winfo_width(),
                             row.winfo_width())


if __name__ == "__main__":
    unittest.main(verbosity=2)
