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
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
import calliope
import machine_spirit
import pythia
import themis
import voices
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
        # The NARRATION VOICE bar reads the saved voice as the panel is built;
        # never the checkout's real voice_config.json (31 tests read it once).
        self._orig_voices = voices._DATA_PATH
        voices._DATA_PATH = os.path.join(self._dir, "voice_config.json")

    def tearDown(self):
        machine_spirit._DATA_PATH = self._orig
        themis._DATA_PATH = self._orig_themis
        voices._DATA_PATH = self._orig_voices
        for f in os.listdir(self._dir):
            os.unlink(os.path.join(self._dir, f))
        os.rmdir(self._dir)

class TestMachineSpiritPanelSmoke(_PanelCase):

    def test_the_model_thinking_and_temperature_rows_carry_their_tips(self):
        from panels import virgil_tooltip
        shared = virgil_tooltip._shared(self.root)
        panel = MachineSpiritPanel(self.root)
        self.addCleanup(panel.destroy)
        self.assertIs(panel.tip_targets["model"], panel._model_bar.master)
        self.assertIs(panel.tip_targets["thinking"], panel._think_note.master)
        self.assertIs(panel.tip_targets["temperature"], panel._temp_value.master)
        self.assertEqual(sorted(MachineSpiritPanel._TIPS), sorted(panel.tip_targets))
        for key, tip in MachineSpiritPanel._TIPS.items():
            self.assertEqual(shared._tips.get(str(panel.tip_targets[key])), tip, key)


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
        # Committed as NO override, not as the default text stored as one,
        # which would keep this install on it through the next default change.
        self.assertFalse(os.path.exists(machine_spirit._DATA_PATH))

    def test_a_failed_save_says_so_without_its_path(self):
        panel = MachineSpiritPanel(self.root)
        self.root.update_idletasks()
        error = PermissionError(13, "Permission denied",
                                r"C:\Users\someone\machine_spirit_config.json")
        with mock.patch.object(machine_spirit, "save", side_effect=error):
            try:
                panel._on_save()
            except OSError:
                self.fail("a failed save escaped the callback; nothing said so")
        text = panel._status.cget("text")
        self.assertIn("Not saved", text)
        self.assertIn("PermissionError", text)
        self.assertNotIn("someone", text)
        self.assertEqual(str(panel._status.cget("fg")), C["red"])

    def test_a_failed_voice_save_says_so_and_is_not_made_live(self):
        panel = MachineSpiritPanel(self.root)
        self.root.update_idletasks()
        error = OSError(28, "No space left on device",
                        r"C:\Users\someone\voice_config.json")
        with mock.patch.object(voices, "save_pythia_voice", side_effect=error), \
             mock.patch.object(calliope, "set_voice") as set_voice:
            try:
                panel._on_voice(voices.CATALOG[-1])
            except OSError:
                self.fail("a failed voice save escaped the callback; nothing said so")
        set_voice.assert_not_called()
        text = panel._status.cget("text")
        self.assertIn("Not saved", text)
        self.assertNotIn("someone", text)


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
        error = OSError(28, "No space left on device",
                        r"C:\Users\someone\felhaven_settings.json")
        with mock.patch.object(themis, "save_pythia", side_effect=error):
            panel._on_thinking(True)
        self.assertFalse(panel._thinking)
        self.assertTrue(self._lit(panel._think_labels[False]))
        self.assertIn("Not saved", panel._status.cget("text"))
        self.assertNotIn("someone", panel._status.cget("text"))   # §2: no path on screen
        self.assertEqual(panel._status.cget("fg"), C["red"])

    def test_a_settings_error_keeps_its_own_words(self):
        panel = self._panel()
        error = themis.SettingsError("temperature must be between 0 and 2")
        with mock.patch.object(themis, "save_pythia", side_effect=error):
            panel._on_step(+1)
        self.assertEqual(panel._status.cget("text"),
                         "Not saved — temperature must be between 0 and 2")


def _model(name, caps=("completion", "tools", "thinking"), temperature=1.0):
    return {"name": name, "size_gb": 6.7, "params": "5.1B", "quant": "Q4_K_M",
            "capabilities": list(caps) if caps is not None else None,
            "default_temperature": temperature}


class TestModelRow(_PanelCase):
    """MODEL: Talos's list, the status line, and a pick's save + switch."""

    def setUp(self):
        super().setUp()
        self.default = pythia._DEFAULT_MODEL
        switch = mock.patch.object(pythia, "switch_model")
        self.switch = switch.start()
        self.addCleanup(switch.stop)
        warming = mock.patch.object(pythia, "is_warming", return_value=False)
        self.warming = warming.start()
        self.addCleanup(warming.stop)

    def _panel(self, data=None):
        panel = MachineSpiritPanel(self.root)
        if data is not False:
            panel.update(data)
        return panel

    def _talos(self, *installed, loaded=()):
        return {"installed": list(installed), "loaded": list(loaded)}

    def _menu_labels(self, panel):
        menu = panel._model_bar._menu["menu"]
        return [menu.entrycget(i, "label") for i in range(menu.index("end") + 1)
                if menu.type(i) == "command"]

    def test_it_starts_on_the_current_model(self):
        self.assertEqual(self._panel(False)._model_bar.current(), self.default)

    def test_only_models_that_can_use_tools_are_listed(self):
        panel = self._panel(self._talos(_model(self.default), _model("qwen3:4b"),
                                        _model("chatty:1b", caps=("completion",))))
        labels = self._menu_labels(panel)
        self.assertEqual(labels[:-1], sorted([self.default, "qwen3:4b"]))
        self.assertEqual(labels[-1], panel._model_bar.PULL_HINT)
        menu = panel._model_bar._menu["menu"]
        self.assertEqual(str(menu.entrycget(menu.index("end"), "state")), "disabled")

    def test_a_saved_model_that_is_not_installed_stays_shown(self):
        themis.save_pythia_model("gone:7b")
        panel = self._panel(self._talos(_model(self.default)))
        self.assertIn("gone:7b", self._menu_labels(panel))
        self.assertEqual(panel._model_bar.current(), "gone:7b")
        self.assertIn("ollama pull gone:7b", panel._model_status.cget("text"))

    def test_the_status_line(self):
        loaded = {"name": self.default, "vram_gb": 1.61, "size_gb": 1.61,
                  "gpu_pct": 100, "context": 16384, "kept_loaded": True}
        cases = [
            (None, "Ollama not reachable"),
            (self._talos(_model(self.default)), "not loaded"),
            (self._talos(_model(self.default), loaded=[loaded]), "loaded · all of it on the GPU"),
            (self._talos(_model(self.default), loaded=[dict(loaded, gpu_pct=62)]),
             "62% on the GPU, the rest on the CPU"),
        ]
        for data, _ in cases:
            with self.subTest(gb_free=data is not None):
                # Ollama's GB figure under-counts gemma4: never shown as GPU memory.
                self.assertNotIn("GB", self._panel(data)._model_status.cget("text"))
        for data, expected in cases:
            with self.subTest(expected=expected):
                text = self._panel(data)._model_status.cget("text")
                self.assertIn(expected, text)
                # No load time promised: 20 to 80 s by model and disk (the
                # owner's HEPHAESTUS call, 2026-11-17).
                self.assertNotIn("minute", text)
        self.warming.return_value = True
        text = self._panel(self._talos(_model(self.default)))._model_status.cget("text")
        self.assertIn("loading", text)
        self.assertNotIn("minute", text)

    def test_a_pick_saves_and_starts_the_switch(self):
        panel = self._panel(self._talos(_model(self.default), _model("qwen3:4b")))
        panel._model_bar._picked("qwen3:4b")
        self.assertEqual(themis.pythia_model(), "qwen3:4b")
        self.switch.assert_called_once_with(self.default)       # the one to unload
        self.assertIn("Model: qwen3:4b", panel._status.cget("text"))
        self.assertNotIn("minute", panel._status.cget("text"))

    def test_picking_the_current_model_does_nothing(self):
        panel = self._panel(self._talos(_model(self.default)))
        panel._model_bar._picked(self.default)
        self.switch.assert_not_called()
        self.assertIsNone(themis.pythia_model())

    def test_a_failed_save_says_so_and_switches_nothing(self):
        panel = self._panel(self._talos(_model(self.default), _model("qwen3:4b")))
        error = OSError(28, "No space left on device",
                        r"C:\Users\someone\felhaven_settings.json")
        with mock.patch.object(themis, "save_pythia_model", side_effect=error):
            panel._model_bar._picked("qwen3:4b")
        self.switch.assert_not_called()
        self.assertIn("Not saved", panel._status.cget("text"))
        self.assertNotIn("someone", panel._status.cget("text"))   # §2: no path on screen
        self.assertEqual(panel._model_bar.current(), self.default)

    def test_a_model_that_cannot_think_greys_thinking(self):
        themis.save_pythia(thinking=True, temperature=None)
        panel = self._panel(self._talos(_model(self.default, caps=("completion", "tools"))))
        self.assertEqual(panel._think_note.cget("text"), "this model can't think")
        self.assertNotEqual(panel._think_labels[True].cget("fg"), C["text1"])
        self.assertIs(themis.pythia_thinking(), True)        # the setting is kept

    def test_the_stepper_starts_from_the_models_own_temperature(self):
        panel = self._panel(self._talos(_model(self.default, temperature=0.7)))
        panel._on_step(+1)
        self.assertEqual(themis.pythia_temperature(), 0.8)

    def test_an_unchanged_list_is_not_rebuilt(self):
        data = self._talos(_model(self.default), _model("qwen3:4b"))
        panel = self._panel(data)
        with mock.patch.object(panel._model_bar._menu["menu"], "delete") as delete:
            panel.update(data)
        delete.assert_not_called()


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
        self.addCleanup(shutil.rmtree, self._dir, True)
        for module, name in ((machine_spirit, "machine_spirit_config.json"),
                             (themis, "felhaven_settings.json"),
                             (voices, "voice_config.json")):
            patch = mock.patch.object(module, "_DATA_PATH",
                                      os.path.join(self._dir, name))
            patch.start()
            self.addCleanup(patch.stop)

    def test_save_and_its_status_stay_on_screen_at_the_minimum(self):
        """488 x 330 is the tab at the 760 x 500 minimum window (measured from a
        screenshot, 2026-11-09). The MODEL row pushed SAVE off the bottom when
        the editor was packed before it; the editor is what shrinks now."""
        self.root.geometry("488x330+40+40")
        panel = MachineSpiritPanel(self.root)
        panel.pack(fill="both", expand=True)
        self.root.update()
        if not panel.winfo_viewable():
            self.skipTest("no mapped window on this display")
        panel._status.config(text="Saved ✓ — takes effect on the next question.")
        self.root.update()
        bottom = panel.winfo_height()
        for widget in (panel._btn_row, panel._status):
            with self.subTest(widget=str(widget)):
                self.assertTrue(widget.winfo_ismapped())
                top = widget.winfo_y()
                self.assertGreater(widget.winfo_height(), 1, "squeezed to nothing")
                self.assertLessEqual(top + widget.winfo_reqheight(), bottom,
                                     "cut off at the bottom")
        self.assertGreater(panel._editor.winfo_height(), 1)

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
