"""
test_eudoxus_panel_smoke.py — the EUDOXUS tab body: the placeholder, history,
and the error-text map.

Headless Tk per CONVENTIONS §10: a real tk.Tk() root we withdraw() so no window
flashes, driven with update_idletasks() instead of mainloop(). No exception
raised == pass. Nothing is mocked — Eudoxus is pure arithmetic, so the panel
drives the real tool.

The guard that matters most is TestErrorTextCoversEveryCode: it reads the error
identifiers out of tools/eudoxus.py's own AST and compares them with
_ERROR_TEXT's keys, so the two halves cannot drift. A hand-written list of "the
codes Eudoxus emits" would be a third copy, and §10 says to compare two live
sources instead. This mirrors tests/test_zeno_panel_smoke.py.

    python -X utf8 -m unittest tests.test_eudoxus_panel_smoke
"""

import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
from panels import eudoxus_panel
from panels.eudoxus_panel import EudoxusPanel

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _eudoxus_error_slugs():
    """Every error slug tools/eudoxus.py can return, read from its AST.

    Mirrors how tests/test_tool_output_contract.py finds error identifiers: the
    dict literals are the source of truth, and after the 2026-07-23 audit every
    one of Eudoxus's is a literal or a literal-headed f-string.
    """
    with open(os.path.join(_APP_ROOT, "tools", "eudoxus.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    slugs = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and key.value == "error"):
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                slugs.add(value.value.split(": ")[0])
            elif isinstance(value, ast.JoinedStr) and value.values:
                head = value.values[0]
                if isinstance(head, ast.Constant) and isinstance(head.value, str):
                    slugs.add(head.value.split(": ")[0])
    return slugs


class TestErrorTextCoversEveryCode(unittest.TestCase):
    def test_the_ast_scan_actually_finds_codes(self):
        # A scan that finds nothing would make the test below pass vacuously.
        self.assertGreater(len(_eudoxus_error_slugs()), 3)

    def test_map_and_tool_stay_in_lockstep(self):
        self.assertEqual(
            _eudoxus_error_slugs(), set(eudoxus_panel._ERROR_TEXT),
            "panels/eudoxus_panel.py:_ERROR_TEXT and tools/eudoxus.py disagree "
            "about which error identifiers exist — an unmapped code renders raw "
            "at the user, a stale key maps something Eudoxus cannot emit")

    def test_detail_is_kept_and_the_slug_is_translated(self):
        self.assertEqual(eudoxus_panel._error_text("unknown_unit: parsec"),
                         "unknown unit: parsec")

    def test_bare_slug_needs_no_detail(self):
        self.assertEqual(eudoxus_panel._error_text("missing_source_unit"),
                         "no unit to convert from")

    def test_an_unmapped_code_is_surfaced_raw_not_swallowed(self):
        self.assertEqual(eudoxus_panel._error_text("brand_new_code"), "brand_new_code")
        self.assertEqual(eudoxus_panel._error_text("brand_new_code: 3"),
                         "brand_new_code: 3")


class TestEudoxusPanelSmoke(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.panel = EudoxusPanel(self.root)
        self.panel.pack()

    def tearDown(self):
        self.panel.destroy()

    def _enter(self, expression):
        """Type as a user would: focus clears the placeholder, then the text."""
        self.panel._clear_ph()
        self.panel._entry.delete(0, "end")
        self.panel._entry.insert(0, expression)
        self.panel._placeholder = False
        self.panel._evaluate()
        self.root.update_idletasks()

    # ── Building and the placeholder ────────────────────────────────────────

    def test_builds_showing_the_placeholder(self):
        self.assertEqual(self.panel._entry.get(), eudoxus_panel._PLACEHOLDER)
        self.assertTrue(self.panel._placeholder)

    def test_the_untouched_placeholder_is_not_evaluated(self):
        self.panel._evaluate()
        self.assertEqual(self.panel._history, [])

    def test_typing_the_example_string_by_hand_is_evaluated(self):
        # The regression this whole flag exists for: "10 mi to km" is a real
        # conversion, and comparing text against the placeholder made typing it
        # a no-op. With the flag, an actively-typed placeholder value evaluates.
        self._enter("10 mi to km")
        self.assertEqual(len(self.panel._history), 1)
        self.assertEqual(self.panel._val_lbl.cget("text"), "16.0934 km")

    def test_blank_input_is_not_evaluated(self):
        # Return on a blank box is a no-op (the placeholder returns on FocusOut,
        # not here) — matches the pre-audit behaviour and zeno_panel.
        self._enter("   ")
        self.assertEqual(self.panel._history, [])

    # ── Success path ────────────────────────────────────────────────────────

    def test_a_result_renders_amber_with_its_factor(self):
        self._enter("10 mi to km")
        self.assertEqual(self.panel._val_lbl.cget("text"), "16.0934 km")
        self.assertEqual(self.panel._val_lbl.cget("fg"), theme.C["amber"])
        self.assertEqual(self.panel._factor_lbl.cget("text"), "1 mi = 1.60934 km")

    def test_the_entry_resets_to_the_placeholder_after_a_conversion(self):
        self._enter("10 mi to km")
        self.assertEqual(self.panel._entry.get(), eudoxus_panel._PLACEHOLDER)
        self.assertTrue(self.panel._placeholder)

    # ── Error path ──────────────────────────────────────────────────────────

    def test_an_error_renders_red_as_human_text_not_a_slug(self):
        self._enter("10 mi to cup")
        self.assertEqual(self.panel._val_lbl.cget("fg"), theme.C["red"])
        self.assertEqual(self.panel._val_lbl.cget("text"),
                         "cannot convert: length to volume")

    def test_an_error_with_detail_keeps_the_detail(self):
        self._enter("10 parsec to km")
        self.assertEqual(self.panel._val_lbl.cget("text"), "unknown unit: parsec")

    def test_an_error_clears_the_factor_line(self):
        self._enter("10 mi to km")
        self._enter("10 parsec to km")
        self.assertEqual(self.panel._factor_lbl.cget("text"), "")

    def test_an_error_does_not_touch_history(self):
        self._enter("10 mi to km")
        self._enter("10 parsec to km")
        self.assertEqual(len(self.panel._history), 1)

    def test_the_last_good_result_survives_an_error_on_screen(self):
        # The strip renders _history[_history_start:]; after an error nothing was
        # appended, so _history_start must drop to 0 or the newest result — now
        # at _history[0] — vanishes from the panel entirely.
        self._enter("10 mi to km")
        self._enter("10 parsec to km")
        shown = self.panel._history_frame.winfo_children()
        self.assertTrue(shown, "the last successful result disappeared")

    # ── History strip ───────────────────────────────────────────────────────

    def test_history_is_capped_at_five(self):
        for n in range(8):
            self._enter(f"{n + 1} mi to km")
        self.assertEqual(len(self.panel._history), 5)
        self.assertEqual(self.panel._history[0]["expression"], "8 mi to km")

    def test_history_shows_everything_but_the_current_row(self):
        for n in range(3):
            self._enter(f"{n + 1} mi to km")
        self.assertEqual(len(self.panel._history_frame.winfo_children()), 2)

    def test_recall_puts_an_expression_back_in_the_entry(self):
        self._enter("10 mi to km")
        self.panel._recall("10 mi to km")
        self.assertEqual(self.panel._entry.get(), "10 mi to km")
        self.assertFalse(self.panel._placeholder)

    def test_recall_survives_the_focus_that_follows_it(self):
        # _recall sets focus, which fires <FocusIn> -> _clear_ph. If the flag
        # were still True, _clear_ph would wipe the just-recalled expression.
        self._enter("5 kg to lb")
        self.panel._recall("5 kg to lb")
        self.root.update_idletasks()
        self.assertEqual(self.panel._entry.get(), "5 kg to lb")


if __name__ == "__main__":
    unittest.main(verbosity=2)
