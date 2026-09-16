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

import theme
from panels.rite_panel import RiteLog, _explain, _format_duration, _order


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
