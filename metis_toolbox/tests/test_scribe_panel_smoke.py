"""
test_scribe_panel_smoke.py — headless Tk tests for the SCRIBE tab
(panels/scribe_panel.py).

The panel takes a plain dict and an on_save callback, so no test here reads
or writes this checkout's scribe_data.json: on_save records what it is
handed, and the one test that goes through scribe.load_data() points it at a
temp file. The layout cases need real geometry, so they map an invisible root
(alpha 0, no frame) at 488 x 330, the tab at Felhaven's 760 x 500 minimum
(test_themis_panel_smoke's measurement).

    python -X utf8 -m unittest tests.test_scribe_panel_smoke
"""

import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import scribe
import theme
from panels.scribe_panel import ScribePanel
from theme import C
from tests import test_scribe


def _tasks(count, text="task"):
    return [{"text": f"{text} {i}", "done": False} for i in range(count)]


def _in_view(widget, panel):
    """Whether `widget` lies wholly inside every ancestor up to `panel`: a row
    scrolled out of the list's canvas, or pushed below the panel, is not."""
    top = widget.winfo_rooty()
    bottom = top + widget.winfo_height()
    if not widget.winfo_ismapped() or widget.winfo_height() <= 1:
        return False
    parent = widget.master
    while parent is not None:
        ptop = parent.winfo_rooty()
        if top < ptop - 1 or bottom > ptop + parent.winfo_height() + 1:
            return False
        if parent is panel:
            return True
        parent = parent.master
    return True


class _WithdrawnRoot(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _panel(self, data):
        saved = []
        panel = ScribePanel(self.root, data, lambda d: saved.append(d["notes"]))
        self.addCleanup(panel.destroy)
        self.root.update()
        return panel, saved


class _MappedRoot(unittest.TestCase):
    """Real geometry needs a mapped root: mapped and invisible."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0.0)
        self.root.overrideredirect(True)
        theme._init_fonts(self.root)
        self.addCleanup(self.root.destroy)

    def _panel(self, data, geometry="488x330+40+40"):
        self.root.geometry(geometry)
        panel = ScribePanel(self.root, data, lambda d: None)
        panel.pack(fill="both", expand=True)
        self.root.update()
        if not panel.winfo_viewable():
            self.skipTest("no mapped window on this display")
        return panel


class TestBuildsFromAnyFile(_WithdrawnRoot):

    def test_the_panel_builds_and_adds_from_every_bad_file(self):
        """A hand-edited scribe_data.json made the panel raise while Felhaven
        built it, so Felhaven didn't start (measured 2026-11-21)."""
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(scribe, "DATA_FILE", Path(folder) / "scribe_data.json"):
            for bad in test_scribe.TestLoadShape.BAD_FILES:
                with self.subTest(file=bad):
                    scribe.DATA_FILE.write_text(json.dumps(bad), encoding="utf-8")
                    logging.disable(logging.WARNING)
                    try:
                        data = scribe.load_data()
                    finally:
                        logging.disable(logging.NOTSET)
                    try:
                        panel = ScribePanel(self.root, data, lambda d: None)
                        panel._entry.delete(0, "end")
                        panel._entry.insert(0, "new")
                        panel._add()
                        panel.destroy()
                    except Exception as exc:
                        self.fail(f"the panel raised {type(exc).__name__}")


class TestNotes(_WithdrawnRoot):

    def test_undo_cannot_erase_the_loaded_notes(self):
        """The loading insert was on the undo stack: one Ctrl+Z emptied the
        notes, and the empty text was saved (measured 2026-11-21)."""
        panel, saved = self._panel({"tasks": [], "notes": "kept"})
        text = panel._notes._text
        text.event_generate("<<Undo>>")
        self.root.update()
        self.assertEqual(text.get("1.0", "end-1c"), "kept")
        self.assertEqual(saved, [])

    def test_a_paste_with_no_key_is_saved(self):
        """A middle-click paste (<<PasteSelection>>) edits with no key, and
        <KeyRelease> never heard it."""
        panel, saved = self._panel({"tasks": [], "notes": "abc"})
        text = panel._notes._text
        text.tag_add("sel", "1.0", "1.2")
        text.event_generate("<<PasteSelection>>", x=2, y=2)
        self.root.update()
        self.assertNotEqual(text.get("1.0", "end-1c"), "abc", "nothing was pasted")
        self.assertEqual(saved[-1:], [text.get("1.0", "end-1c")])

    def test_every_change_saves_once(self):
        panel, saved = self._panel({"tasks": [], "notes": "a"})
        text = panel._notes._text
        expected = []
        for step in ("insert", "insert", "<<Undo>>", "<<Redo>>"):
            if step == "insert":
                text.insert("end", "+")
            else:
                text.event_generate(step)
            self.root.update()
            expected.append(text.get("1.0", "end-1c"))
        self.assertEqual(saved, expected)


class TestSaveResult(_WithdrawnRoot):
    """A failed save turns the count line red until a save works (the owner's
    pick, 2026-11-21); felhaven's _flush_save calls show_saved()."""

    def _show(self, panel, saved):
        show = getattr(panel, "show_saved", None)
        self.assertIsNotNone(show, "the panel has no show_saved()")
        show(saved)

    def test_a_failed_save_says_so_until_one_works(self):
        panel, _saved = self._panel({"tasks": _tasks(3), "notes": ""})
        self._show(panel, False)
        self.assertEqual(panel._status_lbl.cget("text"), "3 open · not saved, see the log")
        self.assertEqual(panel._status_lbl.cget("fg"), C["red"])
        # A change while it is failing keeps saying so.
        panel._toggle(0)
        self.assertEqual(panel._status_lbl.cget("text"), "2 open · not saved, see the log")
        self._show(panel, True)
        self.assertEqual(panel._status_lbl.cget("text"), "2 open")
        self.assertEqual(panel._status_lbl.cget("fg"), C["text3"])


class TestRows(_MappedRoot):

    def test_a_long_task_keeps_its_delete_glyph(self):
        """A 200-character task pushed its own ✕ off the row, so it couldn't
        be deleted (measured 2026-11-21)."""
        panel = self._panel({"tasks": [{"text": "x" * 300, "done": False}],
                             "notes": ""})
        row = panel._list_frame.winfo_children()[0]
        cross = [w for w in row.winfo_children() if w.cget("text") == "✕"][0]
        self.assertTrue(cross.winfo_ismapped(), "the ✕ was pushed off the row")
        self.assertGreater(cross.winfo_width(), 1)


class TestTaskListScrolls(_MappedRoot):

    def test_at_the_minimum_the_notes_keep_four_lines(self):
        """Past about ten tasks the rows ran off the bottom and the notes went
        to nothing first."""
        panel = self._panel({"tasks": _tasks(30), "notes": ""})
        text = panel._notes._text
        line = text.tk.call("font", "metrics", text.cget("font"), "-linespace")
        self.assertGreaterEqual(text.winfo_height(), 4 * line)
        self.assertTrue(_in_view(panel._notes, panel), "the notes were pushed off")
        first, last = panel._list._canvas.yview()
        self.assertLess(last - first, 1, "the list should scroll, not fit")

    def test_a_task_just_added_is_scrolled_into_view(self):
        panel = self._panel({"tasks": [], "notes": ""})
        missed = []
        for n in range(1, 21):
            panel._entry.delete(0, "end")
            panel._entry.insert(0, f"task {n}")
            panel._add()
            self.root.update()
            rows = panel._list_frame.winfo_children()
            if not _in_view(rows[-1], panel):
                missed.append(n)
        self.assertEqual(missed, [], "added tasks out of view")

    def test_with_room_the_whole_list_shows(self):
        panel = self._panel({"tasks": _tasks(5), "notes": ""}, "700x900+40+40")
        rows = panel._list_frame.winfo_children()
        self.assertTrue(all(_in_view(row, panel) for row in rows))
        # The scroll column's canvas, or the plain frame the list once was.
        holder = panel._list_frame.master
        if holder is panel:
            holder = panel._list_frame
        self.assertEqual(holder.winfo_height(), panel._list_frame.winfo_reqheight(),
                         "the list should be as tall as its rows when there is room")


if __name__ == "__main__":
    unittest.main(verbosity=2)
