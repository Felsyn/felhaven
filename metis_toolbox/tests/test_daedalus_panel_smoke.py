"""
test_daedalus_panel_smoke.py — headless Tk smoke test for the DAEDALUS tab.

A withdrawn tk.Tk() root driven with update_idletasks() (no mainloop), the
test_moderati_panel_smoke approach. daedalus._APP_ROOT points at a small temp
tree so the graph is small and known. What earns a test here:

  * NOTHING HAPPENS AT CONSTRUCTION — Felhaven builds every Moderati tab at
    launch, so the index and the layout wait for the tab to be shown.
  * ITEMS ARE BUILT ONCE and recoloured after that — hover and click never
    delete and recreate them.
  * THE PANE SHOWS EXACTLY WHAT docstring() RETURNS.
  * NOTEPAD, NEVER os.startfile — and never a path from outside the app root.

    python -X utf8 -m unittest tests.test_daedalus_panel_smoke
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
from panels import daedalus_panel
from panels.daedalus_panel import DaedalusPanel
from theme import C
from tools import daedalus

_REAL_ROOT = daedalus._APP_ROOT

_FILES = {
    "hub.py": '"""\nhub.py — the middle\n====================\nJob:         Tie it together.\n"""\nfrom tools import alpha\nfrom panels import face\n',
    "loner.py": '"""Job: Import nothing."""\n',
    "tools/alpha.py": '"""Job: Do the alpha thing."""\ndef later():\n    import hub\n',
    "tools/bare.py": "x = 1\n",
    "panels/face.py": '"""Job: Draw the face."""\nimport hub\n',
}


class TestDaedalusPanelSmoke(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.tree = tempfile.mkdtemp(prefix="daedalus_panel_")
        for rel, src in _FILES.items():
            path = os.path.join(self.tree, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(src)
        daedalus._APP_ROOT = self.tree
        self.addCleanup(self._restore)
        # The panel's own containment check, pointed at the same tree.
        root_patch = mock.patch.object(daedalus_panel, "_APP_ROOT", self.tree)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def _restore(self):
        daedalus._APP_ROOT = _REAL_ROOT
        shutil.rmtree(self.tree, ignore_errors=True)

    def _panel(self) -> DaedalusPanel:
        panel = DaedalusPanel(self.root)
        panel.pack(fill="both", expand=True)
        self.addCleanup(panel.destroy)
        self.root.update_idletasks()
        return panel

    def _shown(self, panel) -> DaedalusPanel:
        panel.refresh()
        self.root.update_idletasks()
        return panel

    def _pane_text(self, panel) -> str:
        return panel._text.get("1.0", "end-1c")

    # ── construction ────────────────────────────────────────────────────────
    def test_construction_reads_nothing(self):
        """Felhaven builds this tab at launch; the index waits to be shown."""
        with mock.patch("panels.daedalus_panel.daedalus.index") as index, \
                mock.patch("panels.daedalus_panel.daedalus.layout") as layout:
            self._panel()
        index.assert_not_called()
        layout.assert_not_called()

    def test_construction_arms_no_timer(self):
        before = set(self.root.tk.call("after", "info"))
        panel = self._shown(self._panel())
        panel.select("hub")
        self.assertEqual(set(self.root.tk.call("after", "info")), before)

    # ── the graph ───────────────────────────────────────────────────────────
    def test_refresh_builds_one_dot_and_label_per_module_and_a_line_per_import(self):
        panel = self._shown(self._panel())
        self.assertEqual(set(panel._node_items), {"hub", "loner", "alpha", "bare", "face"})
        self.assertEqual(len(panel._edge_items), 4)
        self.assertEqual(len(panel._canvas.find_all()), 5 * 2 + 4)

    def test_a_lazy_import_is_dashed(self):
        panel = self._shown(self._panel())
        for item, src, dst, lazy in panel._edge_items:
            with self.subTest(edge=(src, dst)):
                dash = panel._canvas.itemcget(item, "dash")
                self.assertEqual(bool(dash), lazy)
                self.assertEqual(lazy, (src, dst) == ("alpha", "hub"))

    def test_an_unchanged_index_is_not_redrawn(self):
        panel = self._shown(self._panel())
        items = panel._canvas.find_all()
        self._shown(panel)
        self.assertEqual(panel._canvas.find_all(), items)

    def test_hover_and_click_recolour_rather_than_rebuild(self):
        panel = self._shown(self._panel())
        items = panel._canvas.find_all()
        panel._hovered = "hub"
        panel._paint()
        panel.select("alpha")
        panel._hovered = None
        panel._paint()
        self.assertEqual(panel._canvas.find_all(), items)

    def test_hover_lights_the_neighbours_and_dims_the_rest(self):
        panel = self._shown(self._panel())
        panel._hovered = "hub"
        panel._paint()
        label = lambda key: panel._canvas.itemcget(panel._node_items[key][1], "fill")
        for key in ("hub", "alpha", "face"):
            self.assertEqual(label(key), C["text1"], key)
        for key in ("loner", "bare"):
            self.assertEqual(label(key), C["badge_bg"], key)

    def test_an_edited_module_is_redrawn_next_time_the_tab_shows(self):
        panel = self._shown(self._panel())
        panel.select("loner")
        path = os.path.join(self.tree, "loner.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('"""Job: Import one thing now."""\nimport hub\n')
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        self._shown(panel)
        self.assertEqual(len(panel._edge_items), 5)
        self.assertEqual(self._pane_text(panel), "Job: Import one thing now.")

    # ── the docstring pane ──────────────────────────────────────────────────
    def test_selecting_shows_exactly_what_docstring_returns(self):
        panel = self._shown(self._panel())
        panel.select("hub")
        self.assertEqual(self._pane_text(panel), daedalus.docstring("hub")["docstring"])
        self.assertEqual(panel._title.cget("text"), "hub · hub.py")

    def test_a_module_without_a_docstring_says_so(self):
        panel = self._shown(self._panel())
        panel.select("bare")
        self.assertEqual(self._pane_text(panel), "(no module docstring)")

    # ── Notepad ─────────────────────────────────────────────────────────────
    def _notepad(self, windows=True):
        stack = [
            mock.patch.object(daedalus_panel, "_IS_WINDOWS", windows),
            mock.patch("panels.daedalus_panel.subprocess.Popen"),
            mock.patch("os.startfile", create=True),
        ]
        started = [p.start() for p in stack]
        for p in stack:
            self.addCleanup(p.stop)
        return started[1], started[2]

    def test_notepad_opens_the_file_and_startfile_is_never_touched(self):
        popen, startfile = self._notepad()
        panel = self._shown(self._panel())
        panel.select("alpha")
        panel._open_in_notepad()
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0],
                         ["notepad.exe", daedalus.source_path("alpha")])
        startfile.assert_not_called()

    def test_a_path_outside_the_app_root_is_refused(self):
        popen, startfile = self._notepad()
        panel = self._shown(self._panel())
        panel.select("alpha")
        with mock.patch("panels.daedalus_panel.daedalus.source_path",
                        return_value=os.path.abspath(__file__)):
            panel._open_in_notepad()
        popen.assert_not_called()
        startfile.assert_not_called()
        self.assertIn("Refused", panel._note.cget("text"))

    def test_nothing_opens_with_nothing_selected(self):
        popen, _startfile = self._notepad()
        panel = self._shown(self._panel())
        panel._open_in_notepad()
        popen.assert_not_called()

    def test_off_windows_the_button_is_disabled_and_says_why(self):
        popen, startfile = self._notepad(windows=False)
        panel = self._shown(self._panel())
        panel.select("alpha")
        self.assertFalse(panel._open_enabled)
        self.assertIn("Windows-only", panel._note.cget("text"))
        panel._open_in_notepad()
        popen.assert_not_called()
        startfile.assert_not_called()

    def test_a_notepad_that_will_not_start_is_reported_not_raised(self):
        popen, _startfile = self._notepad()
        popen.side_effect = FileNotFoundError("notepad.exe")
        panel = self._shown(self._panel())
        panel.select("alpha")
        panel._open_in_notepad()
        self.assertIn("could not start (FileNotFoundError)", panel._note.cget("text"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
