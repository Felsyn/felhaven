"""
test_zeno_panel_smoke.py — the ZENO tab body: rendering, history, and the
error-text map.

Headless Tk per CONVENTIONS §10: a real tk.Tk() root we withdraw() so no window
flashes, driven with update_idletasks() instead of mainloop(). No exception
raised == pass. Nothing is mocked — Zeno is pure arithmetic, so the panel can
drive the real tool.

The guard that matters most here is TestErrorTextCoversEveryCode: it reads the
identifiers out of tools/zeno.py's own AST and compares them with _ERROR_TEXT's
keys, so the two halves cannot drift. A hand-written list of "the codes Zeno
emits" would be a third copy, and §10 says to compare two live sources instead.

    python -X utf8 -m unittest tests.test_zeno_panel_smoke
"""

import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
from panels import zeno_panel
from panels.zeno_panel import ZenoPanel

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _zeno_error_slugs():
    """Every error slug tools/zeno.py can return, read from its AST.

    Mirrors how tests/test_tool_output_contract.py finds error identifiers: the
    dict literals are the source of truth, and after the 2026-07-23 audit every
    one of Zeno's is a literal or a literal-headed f-string, so they are all
    visible from here.
    """
    with open(os.path.join(_APP_ROOT, "tools", "zeno.py"), encoding="utf-8") as fh:
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
        self.assertGreater(len(_zeno_error_slugs()), 5)

    def test_map_and_tool_stay_in_lockstep(self):
        self.assertEqual(
            _zeno_error_slugs(), set(zeno_panel._ERROR_TEXT),
            "panels/zeno_panel.py:_ERROR_TEXT and tools/zeno.py disagree about "
            "which error identifiers exist — an unmapped code renders raw at "
            "the user, a stale key is a mapping for something Zeno cannot emit")

    def test_detail_is_kept_and_the_slug_is_translated(self):
        self.assertEqual(zeno_panel._error_text("unknown_function: hypot"),
                         "unknown function: hypot")

    def test_bare_slug_needs_no_detail(self):
        self.assertEqual(zeno_panel._error_text("division_by_zero"),
                         "division by zero")

    def test_an_unmapped_code_is_surfaced_raw_not_swallowed(self):
        # Echo's fallback rule: a code nobody mapped is still shown.
        self.assertEqual(zeno_panel._error_text("brand_new_code"), "brand_new_code")
        self.assertEqual(zeno_panel._error_text("brand_new_code: 3"),
                         "brand_new_code: 3")


class TestZenoPanelSmoke(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.panel = ZenoPanel(self.root)
        self.panel.pack()

    def tearDown(self):
        self.panel.destroy()

    def _enter(self, expression):
        self.panel._entry.delete(0, "end")
        self.panel._entry.insert(0, expression)
        self.panel._evaluate()
        self.root.update_idletasks()

    # ── Building and the placeholder ────────────────────────────────────────

    def test_builds_with_the_placeholder_and_no_ans(self):
        self.assertEqual(self.panel._entry.get(), "= expression")
        self.assertEqual(self.panel._ans_lbl.cget("text"), "")

    def test_the_placeholder_is_not_evaluated(self):
        self.panel._evaluate()
        self.assertEqual(self.panel._history, [])

    def test_blank_input_is_not_evaluated(self):
        self._enter("   ")
        self.assertEqual(self.panel._history, [])

    # ── Success path ────────────────────────────────────────────────────────

    def test_a_result_renders_amber_with_its_steps(self):
        self._enter("(2 + 3) * 4")
        self.assertEqual(self.panel._val_lbl.cget("text"), "20")
        self.assertEqual(self.panel._val_lbl.cget("fg"), theme.C["amber"])
        self.assertEqual(len(self.panel._steps_frame.winfo_children()), 3)

    def test_ans_readout_uses_the_tools_own_formatter(self):
        self._enter("0.1 + 0.2")
        # zeno.format_number, not str() — "0.3", not "0.30000000000000004".
        self.assertEqual(self.panel._ans_lbl.cget("text"), "ANS = 0.3")

    def test_ans_feeds_the_next_expression(self):
        self._enter("2 + 2")
        self._enter("ans * 10")
        self.assertEqual(self.panel._val_lbl.cget("text"), "40")

    def test_a_boolean_result_shows_but_does_not_become_ans(self):
        # bool is an int subclass; ANS is a number, so `1 < 2` must not set it.
        self._enter("1 < 2")
        self.assertEqual(self.panel._val_lbl.cget("text"), "True")
        self.assertIsNone(self.panel._ans)

    def test_the_entry_is_cleared_back_to_the_placeholder(self):
        self._enter("2 + 2")
        self.assertEqual(self.panel._entry.get(), "= expression")

    # ── Error path ──────────────────────────────────────────────────────────

    def test_an_error_renders_red_as_human_text_not_a_slug(self):
        self._enter("1 / 0")
        self.assertEqual(self.panel._val_lbl.cget("fg"), theme.C["red"])
        self.assertEqual(self.panel._val_lbl.cget("text"), "division by zero")

    def test_an_error_with_detail_keeps_the_detail(self):
        self._enter("hypot(3, 4)")
        self.assertEqual(self.panel._val_lbl.cget("text"), "unknown function: hypot")

    def test_an_error_does_not_touch_ans_or_history(self):
        self._enter("2 + 2")
        self._enter("1 / 0")
        self.assertEqual(self.panel._ans, 4.0)
        self.assertEqual(len(self.panel._history), 1)

    def test_the_last_good_result_survives_an_error_on_screen(self):
        # The strip renders _history[1:] on the assumption that _history[0] is
        # the current row. After an error nothing was appended, so it has to
        # render from 0 or the newest result vanishes from the panel entirely.
        self._enter("2 + 2")
        self._enter("1 / 0")
        shown = [w for w in self.panel._history_frame.winfo_children()]
        self.assertTrue(shown, "the last successful result disappeared")

    # ── History strip ───────────────────────────────────────────────────────

    def test_history_is_capped_at_five(self):
        for n in range(8):
            self._enter(f"{n} + 1")
        self.assertEqual(len(self.panel._history), 5)
        self.assertEqual(self.panel._history[0]["expression"], "7 + 1")

    def test_history_shows_everything_but_the_current_row(self):
        for n in range(3):
            self._enter(f"{n} + 1")
        self.assertEqual(len(self.panel._history_frame.winfo_children()), 2)

    def test_expanding_a_row_shows_its_steps(self):
        self._enter("2 + 2")
        self._enter("3 + 3")
        before = len(self.panel._history_frame.winfo_children())
        self.panel._toggle_expand(1)
        self.root.update_idletasks()
        self.assertIn(1, self.panel._expanded)
        self.assertGreater(len(self.panel._history_frame.winfo_children()), before)
        self.panel._toggle_expand(1)
        self.assertNotIn(1, self.panel._expanded)

    def test_an_expanded_row_follows_its_entry_down_the_strip(self):
        self._enter("2 + 2")
        self._enter("3 + 3")
        self.panel._toggle_expand(1)          # the 2 + 2 row
        self._enter("4 + 4")
        self.assertEqual(self.panel._expanded, {2})   # same entry, new index

    def test_an_expanded_row_is_forgotten_when_it_falls_off_the_cap(self):
        self._enter("0 + 1")
        self._enter("1 + 1")
        self.panel._toggle_expand(1)
        for n in range(5):
            self._enter(f"{n} + 2")
        self.assertEqual(self.panel._expanded, set())

    def test_recall_puts_an_expression_back_in_the_entry(self):
        self._enter("2 + 2")
        self.panel._recall("2 + 2")
        self.assertEqual(self.panel._entry.get(), "2 + 2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
