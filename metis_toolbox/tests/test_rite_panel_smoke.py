"""
test_rite_panel_smoke.py — headless Tk smoke test for the Rite Log window
(record blocks, failure ordering, the expired view, and the JSON clipboard).

Headless Tk per CONVENTIONS §10, same approach as test_home_panel_smoke.py: a
withdrawn tk.Tk() root driven with update_idletasks() — no window flashes, no
mainloop. Every RiteLog built here is destroyed by the test that built it, so
a stray Toplevel can't outlive its case and confuse the next one.

Clicks are never synthesized; the bound callbacks are invoked directly, which
is what those bindings actually do and does not depend on pointer position.

    python -X utf8 -m unittest tests.test_rite_panel_smoke
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import ast
import glob

import theme
from panels.rite_panel import (_ERROR_TEXT, RiteLog, _ScrollFrame, _explain,
                                _format_duration, _order)
from tests.test_tool_output_contract import _error_slugs, _module_constants

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _slugs_in(path: str) -> set:
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    return {s for s, _ in _error_slugs(tree, _module_constants(tree))}


def _registered_tool_files() -> list:
    """pythia._TOOL_MODULES, read from Pythia's source rather than imported:
    importing Pythia imports every tool, and two of them read this checkout's
    own files at import."""
    with open(os.path.join(_APP_ROOT, "pythia.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) == "_TOOL_MODULES" for t in node.targets)):
            return [os.path.join(_APP_ROOT, "tools", f"{elt.id}.py")
                    for elt in node.value.elts]
    raise AssertionError("pythia._TOOL_MODULES not found")


def _written_slugs() -> set:
    """Every slug a brain-side module writes: the tools, Pythia's dispatcher
    (tool_failed, cancelled, …) and Awen's own play_music stand-in."""
    paths = glob.glob(os.path.join(_APP_ROOT, "tools", "*.py")) + [
        os.path.join(_APP_ROOT, "pythia.py"), os.path.join(_APP_ROOT, "awen.py")]
    slugs = set()
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        slugs |= {s for s, _ in _error_slugs(tree, _module_constants(tree))}
    return slugs


def _record(seq: int, **overrides) -> dict:
    base = {
        "seq": seq, "round": 1, "name": f"tool_{seq}", "arguments": {"q": "x"},
        "arguments_truncated": False, "status": "ok",
        "started_at": "2026-08-18T09:07:12.431", "duration_ms": 4210,
        "error": None, "result_preview": '{"ok": true}',
        "result_truncated": False,
    }
    base.update(overrides)
    return base


_RECORDS = [
    _record(1),
    _record(2, name="get_stock_quote", status="failed", duration_ms=312,
            error="vault_locked", result_preview=None),
    _record(3, name="play_music", status="not_run", duration_ms=None,
            error="cancelled: turn stopped before this rite ran",
            result_preview=None),
]


class TestRitePanelHelpers(unittest.TestCase):
    """Pure functions — no Tk needed."""

    def test_duration_switches_unit_at_a_second(self):
        self.assertEqual(_format_duration(4210), "4.2s")
        self.assertEqual(_format_duration(1000), "1.0s")
        self.assertEqual(_format_duration(312), "312ms")

    def test_a_call_that_never_ran_shows_no_duration_at_all(self):
        # Not "0ms" and not a dash — it did not take zero time, it took none.
        self.assertEqual(_format_duration(None), "")

    def test_known_slugs_explain_themselves(self):
        self.assertIn("Cerberus", _explain("vault_locked"))
        # The slug is the stable half; detail after ": " must not defeat lookup.
        self.assertIn("no key", _explain("no_key: finnhub"))

    def test_every_word_is_for_a_slug_a_tool_still_writes(self):
        # A rename (CONVENTIONS §12, 2026-11-16) must move Rite's words with
        # it. A key no tool writes any more is a sentence nobody will see, and
        # the renamed slug would render bare.
        stale = set(_ERROR_TEXT) - _written_slugs()
        self.assertFalse(stale, f"Rite has words for slugs no tool writes: {sorted(stale)}")

    def test_the_shared_words_are_explained(self):
        # The shared core (§2) is what most failures say now, so each one a
        # tool writes gets a sentence.
        core = {"offline", "timeout", "unavailable", "not_found", "no_results",
                "bad_arguments", "unknown_action", "vault_locked"}
        missing = (core & _written_slugs()) - set(_ERROR_TEXT)
        self.assertFalse(missing, f"shared slugs Rite cannot explain: {sorted(missing)}")

    def test_every_slug_pythia_or_a_registered_tool_writes_is_explained(self):
        # The other direction (V0.03 S17, 2026-11-19): nine slugs once rendered
        # bare, six of them Pythia's own refusals, which are the lines an
        # operator most needs to read. A helper's slug reaches a rite only
        # through a registered tool, so those are kept by hand.
        files = [os.path.join(_APP_ROOT, "pythia.py")] + _registered_tool_files()
        for path in files:
            self.assertTrue(os.path.exists(path), path)
        written = set().union(*(_slugs_in(path) for path in files))
        missing = written - set(_ERROR_TEXT)
        self.assertFalse(missing, f"slugs Rite cannot explain: {sorted(missing)}")

    def test_an_unknown_slug_renders_bare(self):
        # Silence is correct — a generic "something went wrong" adds nothing
        # and trains the operator to stop reading this line.
        self.assertEqual(_explain("wat_is_this"), "")

    def test_default_order_is_chronological(self):
        shuffled = [_RECORDS[2], _RECORDS[0], _RECORDS[1]]
        self.assertEqual([r["seq"] for r in _order(shuffled, False)], [1, 2, 3])

    def test_failure_entry_point_puts_failures_first(self):
        ordered = _order(_RECORDS, True)
        self.assertEqual(ordered[0]["status"], "failed")
        # The rest stay in seq order behind them, so chronology is recoverable.
        self.assertEqual([r["seq"] for r in ordered], [2, 1, 3])


class TestRitePanelSmoke(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()          # no window flashes during the run
        theme._init_fonts(cls.root)  # FONTS is empty until a root exists

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _open(self, records, **kw) -> RiteLog:
        win = RiteLog(self.root, records, **kw)
        self.addCleanup(win.destroy)
        self.root.update_idletasks()
        return win

    def _header(self, win: RiteLog) -> str:
        return win.winfo_children()[0].winfo_children()[0].cget("text")

    def test_construct(self):
        win = self._open(_RECORDS)
        self.assertEqual(win.title(), "FELHAVEN — RITE LOG")
        self.assertEqual(self._header(win), "RITE LOG — 3 rites · 1 failed")

    def test_header_says_when_failures_are_shown_first(self):
        win = self._open(_RECORDS, focus_failures=True)
        self.assertIn("showing failures first", self._header(win))

    def test_expired_view_is_calm_not_an_error(self):
        win = self._open(None)
        labels = self._all_text(win)
        self.assertIn("RITE RECORD EXPIRED", labels)
        self.assertTrue(any("no longer retained" in t for t in labels))
        self.assertTrue(any("Retention limit" in t for t in labels))
        # Rendered in the panel's normal voice — red is for genuine failure.
        self.assertNotIn(theme.C["red"], self._all_colors(win))

    def test_a_turn_that_called_nothing_says_so(self):
        win = self._open([])
        self.assertTrue(any("called no tools" in t for t in self._all_text(win)))

    def test_a_cancelled_rite_is_dim_not_red(self):
        # The operator stopped the turn on purpose; an alarm color would report
        # their own decision back to them as a fault.
        win = self._open([_RECORDS[2]])
        self.assertNotIn(theme.C["red"], self._all_colors(win))
        self.assertIn(theme.C["text3"], self._all_colors(win))

    def test_a_failed_rite_shows_slug_and_plain_english(self):
        win = self._open([_RECORDS[1]])
        text = self._all_text(win)
        self.assertIn("vault_locked", text)
        self.assertTrue(any("Unlock the vault" in t for t in text))

    def test_truncation_notice_comes_from_the_flag_not_the_string(self):
        win = self._open([_record(1, result_truncated=True)])
        self.assertTrue(any("result preview · truncated" in t
                            for t in self._all_text(win)))

    def test_copy_all_puts_valid_json_on_the_clipboard(self):
        win = self._open(_RECORDS)
        win._on_copy_all()
        self.root.update_idletasks()
        self.assertEqual([r["seq"] for r in json.loads(self.root.clipboard_get())],
                         [1, 2, 3])
        self.assertEqual(win._copy_all.cget("text"), "COPIED")

    def test_copy_this_rite_is_inert_until_one_is_selected(self):
        win = self._open(_RECORDS)
        win._on_copy_one()                       # nothing selected — no-op
        self.assertEqual(win._copy_one.cget("text"), "COPY THIS RITE")
        win._select(2)
        win._on_copy_one()
        self.root.update_idletasks()
        self.assertEqual([r["seq"] for r in json.loads(self.root.clipboard_get())],
                         [2])

    def test_selecting_the_same_rite_twice_deselects_it(self):
        win = self._open(_RECORDS)
        win._select(2)
        self.assertEqual(win._selected, 2)
        win._select(2)
        self.assertIsNone(win._selected)

    def test_destroy_cancels_the_pending_copied_restore(self):
        # Tk deletes a widget's Tcl commands as it destroys it, so an after()
        # script still queued against a closed window has nothing left to call.
        win = RiteLog(self.root, _RECORDS)
        win._on_copy_all()
        self.assertIsNotNone(win._flash_after)
        win.destroy()
        self.assertIsNone(win._flash_after)

    def test_closing_under_the_pointer_gives_the_wheel_back(self):
        # Escape with the pointer over the list: no <Leave> arrives, and the
        # app-wide binding used to go on calling a canvas that was gone, a
        # TclError on every wheel turn anywhere in Felhaven.
        self.addCleanup(self.root.unbind_all, "<MouseWheel>")
        win = RiteLog(self.root, _RECORDS)
        self.root.update_idletasks()
        (body,) = [w for w in win.winfo_children() if isinstance(w, _ScrollFrame)]
        body._bind_wheel(None)                   # the pointer entered the list
        self.assertTrue(self.root.bind_all("<MouseWheel>"))
        win.destroy()
        self.assertEqual(self.root.bind_all("<MouseWheel>"), "")

    def test_two_windows_can_be_open_at_once(self):
        first = self._open(_RECORDS)
        second = self._open(_RECORDS, focus_failures=True)
        self.assertTrue(first.winfo_exists())
        self.assertTrue(second.winfo_exists())

    # ── helpers ──────────────────────────────────────────────────────────────

    def _walk(self, widget: tk.Misc) -> list:
        found = [widget]
        for child in widget.winfo_children():
            found.extend(self._walk(child))
        return found

    def _all_text(self, win: RiteLog) -> list:
        return [w.cget("text") for w in self._walk(win)
                if isinstance(w, tk.Label)]

    def _all_colors(self, win: RiteLog) -> list:
        return [str(w.cget("fg")) for w in self._walk(win)
                if isinstance(w, tk.Label)]

    # ── Virgil tooltips ─────────────────────────────────────────────────────

    def test_every_rite_and_field_carries_its_tips(self):
        """Each rite's status and name, each field caption, and COPY THIS RITE."""
        from panels import virgil_tooltip
        shared = virgil_tooltip._shared(self.root)
        win = self._open(_RECORDS)
        self.assertEqual(sorted(RiteLog._TIPS), sorted(win.tip_targets))
        for key, tip in RiteLog._TIPS.items():
            self.assertEqual(shared._tips.get(str(win.tip_targets[key])), tip, key)
        found = {key: 0 for key in RiteLog._ROW_TIPS}
        stack = [win]
        while stack:
            widget = stack.pop()
            stack.extend(widget.winfo_children())
            tip = shared._tips.get(str(widget))
            for key, row_tip in RiteLog._ROW_TIPS.items():
                if tip == row_tip:
                    found[key] += 1
        self.assertEqual(found["status"], len(_RECORDS))
        self.assertEqual(found["select"], len(_RECORDS))
        self.assertGreaterEqual(found["expand"], len(_RECORDS))   # arguments, at least
        for name in win._rows.values():
            self.assertEqual(shared._tips.get(str(name)), RiteLog._ROW_TIPS["select"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
