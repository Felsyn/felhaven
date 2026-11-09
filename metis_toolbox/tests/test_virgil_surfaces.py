"""
test_virgil_surfaces.py — every tooltip surface's _TIPS table, held in lockstep
with the widgets it covers (CONVENTIONS §10: two live sources, never a count).

The surfaces and the rule for which source each uses (CONVENTIONS §7):
  * the nav rail says what a PLACE is       -> glossary   (Sidebar._TIPS)
  * a tab says who WORKS there              -> module     (each host's _TIPS)
  * a readout says what the NUMBER means    -> glossary   (VitalsPanel._TIPS)

What earns a test here:
  * TABLE == WIDGETS — the sidebar's table names every row and group header,
    each host's names every tab in its _build_tabs list, and the vitals'
    names every readout. A new row or tab without a tip goes red.
  * EVERY TIP HAS WORDS — on every surface, so a renamed glossary key or a
    module that lost its docstring is a failing test, not a silent blank.
  * A SHOWN GLOSS KEEPS §0 — one sentence, a period, at most 90 characters.
  * NO GLOSSARY ENTRY IS ORPHANED — each is used somewhere, or listed in
    UNUSED_OK with its reason.

The hosts are not built here: they need the file redirections their own smoke
tests set up. Their tab keys are read from the _build_tabs literal with ast
(as Daedalus reads them), and each host's smoke test checks that its live tab
labels carry their tips.

Run from the package root:
    python -X utf8 -m unittest tests.test_virgil_surfaces
"""

import ast
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
from panels import virgil_tooltip
from panels.cogitator_panel import CogitatorPanel
from panels.hephaestus_panel import VitalsPanel
from panels.moderati_panel import ModeratiPanel
from panels.sidebar import Sidebar
from panels.vox_array_panel import VoxArrayPanel
from tools import virgil

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HOSTS = {
    ModeratiPanel: "panels/moderati_panel.py",
    CogitatorPanel: "panels/cogitator_panel.py",
    VoxArrayPanel: "panels/vox_array_panel.py",
}

# Glossary keys no surface uses yet, each with its reason. Empty in v1: every
# entry was written for a surface that shows it.
UNUSED_OK: dict[str, str] = {}

_GLOSS_MAX = 90


def _tab_keys(rel):
    """The keys of the one _build_tabs([...]) call in a host's source."""
    with open(os.path.join(_APP_ROOT, *rel.split("/")), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr == "_build_tabs"]
    assert len(calls) == 1, f"{rel}: expected one _build_tabs call, found {len(calls)}"
    return [ast.literal_eval(entry.elts[0]) for entry in calls[0].args[0].elts]


def _every_table():
    """(surface name, its _TIPS) for every surface."""
    yield "Sidebar", Sidebar._TIPS
    for host in HOSTS:
        yield host.__name__, host._TIPS
    yield "VitalsPanel", VitalsPanel._TIPS


class _BuiltCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def assertCarries(self, widget, tip, where):
        shared = virgil_tooltip._shared(self.root)
        self.assertEqual(shared._tips.get(str(widget)), tip,
                         f"{where} does not carry its tip")


class TestTablesMatchTheirWidgets(_BuiltCase):

    def test_the_sidebar_tips_every_row_and_group_header(self):
        names = [name for name, _label, _sub in Sidebar._ROWS]
        names += [c for children in Sidebar._GROUPS.values()
                  for c, _label, _sub in children]
        self.assertEqual(sorted(Sidebar._TIPS), sorted(names))
        sidebar = Sidebar(self.root, on_select=lambda name: None)
        self.addCleanup(sidebar.destroy)
        for name, tip in Sidebar._TIPS.items():
            row = sidebar._rows.get(name) or sidebar._groups.get(name)
            self.assertCarries(row, tip, f"the sidebar row {name!r}")

    def test_each_host_tips_every_tab(self):
        for host, rel in HOSTS.items():
            with self.subTest(host=host.__name__):
                self.assertEqual(sorted(host._TIPS), sorted(_tab_keys(rel)))

    def test_the_vitals_tip_every_readout(self):
        panel = VitalsPanel(self.root)
        self.addCleanup(panel.destroy)
        self.assertEqual(sorted(VitalsPanel._TIPS), sorted(panel._tip_targets))
        self.assertEqual(sorted(panel._tip_targets),
                         sorted(["cpu", "ram", "disk", *panel.aether.rows]))
        for key, tip in VitalsPanel._TIPS.items():
            self.assertCarries(panel._tip_targets[key], tip, f"the {key} readout")


class TestEveryTipHasWords(unittest.TestCase):

    def test_every_tip_on_every_surface_resolves(self):
        for surface, table in _every_table():
            for key, tip in table.items():
                with self.subTest(surface=surface, key=key):
                    self.assertIsInstance(tip, virgil.Tip)
                    self.assertIsInstance(virgil.text_for(tip), virgil.Words)

    def test_each_surface_uses_its_source(self):
        """The rail and the readouts say what a thing IS (glossary); a tab says
        who works there (module gloss)."""
        expected = {"Sidebar": "term", "VitalsPanel": "term",
                    **{host.__name__: "module" for host in HOSTS}}
        for surface, table in _every_table():
            for key, tip in table.items():
                with self.subTest(surface=surface, key=key):
                    self.assertEqual(tip.kind, expected[surface])

    def test_every_shown_gloss_keeps_conventions_0(self):
        for surface, table in _every_table():
            for key, tip in table.items():
                if tip.kind != "module":
                    continue
                with self.subTest(surface=surface, key=key):
                    body = virgil.text_for(tip).body
                    self.assertTrue(body.endswith("."), body)
                    self.assertLessEqual(len(body), _GLOSS_MAX, body)
                    self.assertIsNone(re.search(r"[.!?]\s", body),
                                      f"more than one sentence: {body!r}")


class TestNoOrphanedEntry(unittest.TestCase):

    def test_every_glossary_entry_is_used_or_excused(self):
        used = {tip.key for _surface, table in _every_table()
                for tip in table.values() if tip.kind == "term"}
        glossary = set(virgil.glossary())
        self.assertEqual(sorted(glossary - used - set(UNUSED_OK)), [],
                         "glossary entries no surface shows")
        self.assertEqual(sorted(set(UNUSED_OK) - glossary), [],
                         "UNUSED_OK names entries the glossary no longer has")
        self.assertEqual(sorted(set(UNUSED_OK) & used), [],
                         "UNUSED_OK names entries a surface now uses")


if __name__ == "__main__":
    unittest.main(verbosity=2)
