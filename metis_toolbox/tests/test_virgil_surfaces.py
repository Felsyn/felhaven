"""
test_virgil_surfaces.py — every tooltip surface's _TIPS table, held in lockstep
with the widgets it covers (CONVENTIONS §10: two live sources, never a count).

The surfaces and the rule for which source each uses (CONVENTIONS §7):
  * the nav rail says what a PLACE is       -> glossary   (Sidebar._TIPS)
  * a tab says who WORKS there              -> module     (each host's _TIPS,
    Atmospherics' and the Dynastic Vault's _TAB_TIPS, and the Helios,
    Selene, Ammit and Aether headers, which name their modules)
  * a tab or section one module fills alone says what the VIEW is
                                            -> glossary   (Scriptorium's and
    Morpheus's _TAB_TIPS, Argus's and Cerberus's _SECTION_TIPS)
  * so does anything one worker draws       -> module     (HestiaBar._TIPS:
    Pythia's face)
  * a readout says what the NUMBER means    -> glossary   (VitalsPanel._TIPS,
    Atmospherics': WeatherPanel, HeliosWidget, SeleneWidget, AlertsTab
    and SpaceWeatherWidget, and Home's status row: HomePanel._TIPS)
  * a control says what a CLICK does        -> glossary   (VoxControls._TIPS,
    HomePanel._TIPS, and the glyph controls: Morpheus, Orpheus, Scribe,
    Ammit, Scriptorium and Cartograph. A glyph built once is in _TIPS; one
    built per row is in _ROW_TIPS, attached by the row's builder)

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
labels carry their tips. Home's three tables are the same: test_vox_controls_smoke
and test_home_panel_smoke build the widgets and hold each table to them, and
so do the smoke tests of Morpheus, Orpheus, Ammit (test_horai_panel_smoke),
Scriptorium and Cartograph. Scribe has no smoke test, so its rows are built here.

Run from the package root:
    python -X utf8 -m unittest tests.test_virgil_surfaces
"""

import ast
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
import themis
from panels import virgil_tooltip
from panels.artemis_panel import ArtemisPanel
from panels.argus_panel import ArgusPanel
from panels.aura_panel import HeliosWidget, SeleneWidget, WeatherPanel
from panels.cerberus_panel import CerberusPanel
from panels.daedalus_panel import DaedalusPanel
from panels.emanon_panel import EmanonPanel
from panels.euterpe_panel import EuterpePanel
from panels.cogitator_panel import CogitatorPanel
from panels.hephaestus_panel import AetherWidget, VitalsPanel
from panels.hypatia_panel import ConditionsWidget, HypatiaPanel
from panels.machine_spirit_panel import MachineSpiritPanel
from panels.hestia_panel import HestiaBar
from panels.home_panel import HomePanel
from panels.midas_panel import MidasPanel
from panels.horai_panel import AmmitWidget
from panels.eos_panel import SpaceWeatherWidget
from panels.iris_panel import AlertsTab
from panels.moderati_panel import ModeratiPanel
from panels.morpheus_panel import MorpheusPanel
from panels.orpheus_panel import OrpheusPanel
from panels.pheme_panel import PhemePanel
from panels.queue_panel import QueueBox
from panels.rite_panel import RiteLog
from panels.scribe_panel import ScribePanel
from panels.sidebar import Sidebar
from panels.vox_array_panel import VoxArrayPanel
from panels.vox_controls import VoxControls
from panels.zeno_panel import ZenoPanel
from tools import artemis, virgil
from tests._fixtures import ScratchSettings

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Some surfaces read Themis while they build (the alerts tab's clock format):
# each reads a scratch settings file, never this checkout's own.
_settings = ScratchSettings()


def setUpModule():
    _settings.start()


def tearDownModule():
    _settings.stop()

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
    yield "AlertsTab", AlertsTab._TIPS
    yield "SpaceWeatherWidget", SpaceWeatherWidget._TIPS
    yield "WeatherPanel tabs", WeatherPanel._TAB_TIPS
    yield "WeatherPanel", WeatherPanel._TIPS
    yield "HeliosWidget header", {"header": HeliosWidget._HEADER_TIP}
    yield "HeliosWidget", HeliosWidget._TIPS
    yield "SeleneWidget header", {"header": SeleneWidget._HEADER_TIP}
    yield "SeleneWidget", SeleneWidget._TIPS
    yield "VoxControls", VoxControls._TIPS
    yield "HestiaBar", HestiaBar._TIPS
    yield "HomePanel", HomePanel._TIPS
    yield "MorpheusPanel", MorpheusPanel._TIPS
    yield "MorpheusPanel rows", MorpheusPanel._ROW_TIPS
    yield "OrpheusPanel", OrpheusPanel._TIPS
    yield "OrpheusPanel rows", OrpheusPanel._ROW_TIPS
    yield "ScribePanel rows", ScribePanel._ROW_TIPS
    yield "AmmitWidget", AmmitWidget._TIPS
    yield "PhemePanel rows", PhemePanel._ROW_TIPS
    yield "ArtemisPanel", ArtemisPanel._TIPS
    yield "ArtemisPanel rows", ArtemisPanel._ROW_TIPS
    yield "MidasPanel tabs", MidasPanel._TAB_TIPS
    yield "PhemePanel tabs", PhemePanel._TAB_TIPS
    yield "MorpheusPanel tabs", MorpheusPanel._TAB_TIPS
    yield "AmmitWidget header", {"header": AmmitWidget._HEADER_TIP}
    yield "AetherWidget header", {"header": AetherWidget._HEADER_TIP}
    yield "ArgusPanel sections", ArgusPanel._SECTION_TIPS
    yield "CerberusPanel sections", CerberusPanel._SECTION_TIPS
    yield "HypatiaPanel", HypatiaPanel._TIPS
    yield "ConditionsWidget", ConditionsWidget._TIPS
    yield "MidasPanel", MidasPanel._TIPS
    yield "EuterpePanel", EuterpePanel._TIPS
    yield "ZenoPanel", ZenoPanel._TIPS
    yield "EmanonPanel", EmanonPanel._TIPS
    yield "ArgusPanel", ArgusPanel._TIPS
    yield "DaedalusPanel", DaedalusPanel._TIPS
    yield "MachineSpiritPanel", MachineSpiritPanel._TIPS
    yield "QueueBox rows", QueueBox._ROW_TIPS
    yield "RiteLog", RiteLog._TIPS
    yield "RiteLog rows", RiteLog._ROW_TIPS


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
        # Every bar the panel builds is a readout, found by walking its widgets
        # rather than listed here, so a new bar without a tip goes red; then the
        # readouts that aren't bars.
        from panels.hephaestus_panel import BarMeter
        bars, stack = [], [panel]
        while stack:
            widget = stack.pop()
            if isinstance(widget, BarMeter):
                bars.append(widget)
            stack.extend(widget.winfo_children())
        targets = list(panel._tip_targets.values())
        self.assertTrue(bars, "no BarMeter found — the walk would be vacuous")
        for bar in bars:
            self.assertIn(bar, targets, f"the {bar._label.cget('text')} bar has no tip")
        for other in (panel.node_lbl, panel.gpu_head, panel.ollama.row,
                      *panel.aether.rows.values()):
            self.assertIn(other, targets)
        for key, tip in VitalsPanel._TIPS.items():
            self.assertCarries(panel._tip_targets[key], tip, f"the {key} readout")

    def test_argus_tips_every_section_header(self):
        panel = ArgusPanel(self.root)
        self.addCleanup(panel.destroy)
        self.assertEqual(sorted(ArgusPanel._TIPS), sorted(panel.tip_targets))
        self.assertIs(panel.tip_targets["summary"], panel._summary.master)
        for key, tip in ArgusPanel._TIPS.items():
            self.assertCarries(panel.tip_targets[key], tip, f"the Argus {key}")
        self.assertEqual(sorted(ArgusPanel._SECTION_TIPS), sorted(panel._sections))
        for key, tip in ArgusPanel._SECTION_TIPS.items():
            self.assertCarries(panel._sections[key].header, tip,
                               f"the {key} section header")

    def test_the_aether_header_carries_its_modules_gloss(self):
        panel = VitalsPanel(self.root)
        self.addCleanup(panel.destroy)
        self.assertCarries(panel.aether.header, AetherWidget._HEADER_TIP,
                           "the Aether header")

    def test_every_task_row_tips_its_two_glyphs(self):
        tasks = [{"text": "one", "done": False}, {"text": "two", "done": True}]
        panel = ScribePanel(self.root, {"tasks": tasks, "notes": ""}, lambda data: None)
        self.addCleanup(panel.destroy)
        rows = panel._list_frame.winfo_children()
        self.assertEqual(len(rows), len(tasks))
        for row in rows:
            by_text = {w.cget("text"): w for w in row.winfo_children()}
            check = by_text.get("☐") or by_text.get("☑")
            self.assertCarries(check, ScribePanel._ROW_TIPS["check"], "a task's box")
            self.assertCarries(by_text["✕"], ScribePanel._ROW_TIPS["delete"],
                               "a task's delete")
        panel._toggle(0)                     # a re-render builds new rows
        for row in panel._list_frame.winfo_children():
            glyphs = [w for w in row.winfo_children() if w.cget("text") in "☐☑✕"]
            self.assertEqual(len(glyphs), 2)
            for glyph in glyphs:
                self.assertIsNotNone(virgil_tooltip._shared(self.root)
                                     ._tips.get(str(glyph)))

    def test_the_alerts_tab_tips_its_readouts(self):
        """The status line, and the severity and certainty tags on a card."""
        from datetime import datetime
        tab = AlertsTab(self.root)
        self.addCleanup(tab.destroy)
        alert = {"id": "urn:a", "event": "Flood Warning", "tier": "warning",
                 "severity": "Severe", "certainty": "Likely", "headline": "",
                 "summary": "", "description": "", "instruction": "", "sent": "",
                 "onset": "", "until": "", "sender": "", "area": "", "references": [],
                 "seen": False, "pops": True, "popped_at": None}
        tab.show({"state": "checked", "checked_at": datetime.now(), "since": None,
                  "trouble_s": None, "alerts": [alert]})
        self.assertEqual(sorted(AlertsTab._TIPS), sorted(tab.tip_targets))
        for key, tip in AlertsTab._TIPS.items():
            self.assertCarries(tab.tip_targets[key], tip, f"the alerts {key}")

    def test_the_space_weather_row_tips_its_header_scales_and_lines(self):
        widget = SpaceWeatherWidget(self.root)
        self.addCleanup(widget.destroy)
        self.assertEqual(sorted(SpaceWeatherWidget._TIPS), sorted(widget.tip_targets))
        for scale in widget.values:          # each R/S/G row the widget builds
            self.assertIn(scale, widget.tip_targets)
        for key, tip in SpaceWeatherWidget._TIPS.items():
            self.assertCarries(widget.tip_targets[key], tip, f"the space weather {key}")

    def _atmospherics(self):
        """WeatherPanel, built against an empty settings file and an empty
        Artemis cache: its PLACE list reads both."""
        folder = tempfile.mkdtemp(prefix="virgil_atmos_")
        for target, name, value in (
                (themis, "_DATA_PATH", Path(folder) / "felhaven_settings.json"),
                (artemis, "CACHE_DIR", os.path.join(folder, "artemis_cache"))):
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        panel = WeatherPanel(self.root)
        self.addCleanup(panel.destroy)
        return panel

    def test_atmospherics_tips_every_tab_and_readout(self):
        panel = self._atmospherics()
        self.assertEqual(sorted(WeatherPanel._TAB_TIPS), sorted(panel._tabs))
        for name, tip in WeatherPanel._TAB_TIPS.items():
            self.assertCarries(panel._tabs[name]["tab"], tip, f"the {name} tab")
        self.assertEqual(sorted(WeatherPanel._TIPS), sorted(panel.tip_targets))
        targets = list(panel.tip_targets.values())
        # Every NOW detail row and FORECAST row the panel builds, found from
        # the panel rather than listed here, so a new one without a tip is red.
        for key, row in panel._detail_rows.items():
            self.assertIn(row, targets, f"the {key} row has no tip")
        self.assertEqual(len(panel._detail_rows), len(WeatherPanel._DETAIL_KEYS))
        for i, row in enumerate(panel._forecast_rows, 1):
            self.assertIn(row["row"], targets, f"forecast day {i} has no tip")
        for other in (panel.place_bar, panel.temp_lbl):
            self.assertIn(other, targets)
        for key, tip in WeatherPanel._TIPS.items():
            self.assertCarries(panel.tip_targets[key], tip, f"the {key} readout")
        for widget in (panel.helios_widget, panel.selene_widget):
            self.assertCarries(widget.header, widget._HEADER_TIP,
                               f"the {type(widget).__name__} header")

    def test_the_sun_and_moon_rows_each_carry_a_tip(self):
        for cls, rows in ((HeliosWidget, HeliosWidget.ROWS),
                          (SeleneWidget, ("phase", *SeleneWidget.ROWS))):
            with self.subTest(widget=cls.__name__):
                widget = cls(self.root)
                self.addCleanup(widget.destroy)
                self.assertEqual(sorted(cls._TIPS), sorted(rows))
                self.assertEqual(sorted(cls._TIPS), sorted(widget.tip_targets))
                for key, tip in cls._TIPS.items():
                    self.assertCarries(widget.tip_targets[key], tip,
                                       f"the {cls.__name__} {key} row")


class TestEveryTipHasWords(unittest.TestCase):

    def test_every_tip_on_every_surface_resolves(self):
        for surface, table in _every_table():
            for key, tip in table.items():
                with self.subTest(surface=surface, key=key):
                    self.assertIsInstance(tip, virgil.Tip)
                    self.assertIsInstance(virgil.text_for(tip), virgil.Words)

    def test_each_surface_uses_its_source(self):
        """The rail, the readouts and the controls say what a thing IS or
        DOES (glossary); a tab or a face says who works there (module gloss).
        One kind per surface: a widget that needs both splits its table, as
        Atmospherics' tabs and Hestia's face do."""
        expected = {"Sidebar": "term", "VitalsPanel": "term", "AlertsTab": "term",
                    "SpaceWeatherWidget": "term", "WeatherPanel": "term",
                    "HeliosWidget": "term", "SeleneWidget": "term",
                    "WeatherPanel tabs": "module", "HeliosWidget header": "module",
                    "SeleneWidget header": "module",
                    "VoxControls": "term", "HomePanel": "term",
                    "HestiaBar": "module",
                    **{surface: "term" for surface in (
                        "MorpheusPanel", "MorpheusPanel rows", "OrpheusPanel",
                        "OrpheusPanel rows", "ScribePanel rows", "AmmitWidget",
                        "PhemePanel rows", "ArtemisPanel", "ArtemisPanel rows",
                        "PhemePanel tabs",
                        "MorpheusPanel tabs", "ArgusPanel sections",
                        "CerberusPanel sections", "HypatiaPanel",
                        "ConditionsWidget", "MidasPanel", "EuterpePanel",
                        "ZenoPanel", "EmanonPanel", "ArgusPanel",
                        "DaedalusPanel", "MachineSpiritPanel", "QueueBox rows",
                        "RiteLog", "RiteLog rows")},
                    "MidasPanel tabs": "module", "AmmitWidget header": "module",
                    "AetherWidget header": "module",
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
