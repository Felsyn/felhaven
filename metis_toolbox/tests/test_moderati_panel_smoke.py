"""
test_moderati_panel_smoke.py — headless Tk smoke test for the Moderati host.

Same approach as test_vox_array_panel_smoke.py: a real tk.Tk() root we withdraw()
(no window flashes) and drive with update_idletasks() instead of mainloop(). No
exception raised == pass.

The Moderati host builds six real tab bodies. The three file-backed ones are
redirected at a temp dir so no real user state is read or written and
construction is deterministic: cerberus (its data/vault/manifest/ledger paths —
an absent temp data file routes CerberusPanel to its first-run gate),
machine_spirit and themis (their _DATA_PATH → defaults). The polled bodies
(Hephaestus/Aether, Emanon, Argus) construct side-effect-free; they only read the
system on update(), which Kairos drives — never construction.

    python -X utf8 -m unittest tests.test_moderati_panel_smoke
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
import cerberus
import machine_spirit
import themis


class TestModeratiPanelSmoke(unittest.TestCase):
    """Host builds all six tab bodies and switches tabs without crashing."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        from panels.moderati_panel import ModeratiPanel
        from panels.hephaestus_panel import VitalsPanel
        from panels.emanon_panel import EmanonPanel
        from panels.argus_panel import ArgusPanel
        from panels.cerberus_panel import CerberusPanel
        from panels.themis_panel import ThemisPanel
        from panels.machine_spirit_panel import MachineSpiritPanel

        # Redirect every file-backed body at a temp dir so the real vault / PIN /
        # settings / system-prompt files are never touched. An absent cerberus
        # data file routes CerberusPanel to its first-run gate (preflight raises
        # HashFileError → _enter_first_run_mode), a deterministic build.
        self._dir = tempfile.mkdtemp(prefix="moderati_panel_")
        self._orig_cerb = {k: getattr(cerberus, k) for k in
                           ("_DATA_PATH", "_VAULT_PATH", "_MANIFEST_PATH", "_LEDGER_PATH")}
        cerberus._DATA_PATH     = os.path.join(self._dir, "cerberus_data.json")
        cerberus._VAULT_PATH    = os.path.join(self._dir, "cerberus_vault.json")
        cerberus._MANIFEST_PATH = os.path.join(self._dir, "cerberus_manifest.json")
        cerberus._LEDGER_PATH   = os.path.join(self._dir, "cerberus_ledger.json")
        self._orig_ms = machine_spirit._DATA_PATH
        machine_spirit._DATA_PATH = os.path.join(self._dir, "machine_spirit_config.json")
        self._orig_themis = themis._DATA_PATH
        themis._DATA_PATH = os.path.join(self._dir, "felhaven_settings.json")

        self.VitalsPanel = VitalsPanel
        self.EmanonPanel = EmanonPanel
        self.ArgusPanel = ArgusPanel
        self.CerberusPanel = CerberusPanel
        self.ThemisPanel = ThemisPanel
        self.MachineSpiritPanel = MachineSpiritPanel

        self.panel = ModeratiPanel(self.root)
        self.panel.pack()

    def tearDown(self):
        self.panel.destroy()
        for k, v in self._orig_cerb.items():
            setattr(cerberus, k, v)
        machine_spirit._DATA_PATH = self._orig_ms
        themis._DATA_PATH = self._orig_themis
        for f in os.listdir(self._dir):
            os.unlink(os.path.join(self._dir, f))
        os.rmdir(self._dir)

    def test_builds_with_all_tab_bodies(self):
        self.assertIsInstance(self.panel.hephaestus, self.VitalsPanel)
        self.assertIsInstance(self.panel.emanon, self.EmanonPanel)
        self.assertIsInstance(self.panel.argus, self.ArgusPanel)
        self.assertIsInstance(self.panel.cerberus, self.CerberusPanel)
        self.assertIsInstance(self.panel.themis, self.ThemisPanel)
        self.assertIsInstance(self.panel.machine_spirit, self.MachineSpiritPanel)

    def test_starts_on_hephaestus_tab(self):
        self.assertEqual(self.panel._active, "hephaestus")

    def test_tab_switch_cycles_every_tab(self):
        for key in ("emanon", "argus", "cerberus", "themis",
                    "machine_spirit", "hephaestus"):
            self.panel._show_tab(key)
            self.root.update_idletasks()
            self.assertEqual(self.panel._active, key)

    def test_kairos_children_still_update_when_hidden(self):
        # The Kairos-registered children keep working regardless of which tab is
        # visible (felhaven reaches through the host to register them). Switch to
        # a non-polled tab, then drive a None ("stale") tick through each — every
        # one degrades gracefully (§10), none crash.
        self.panel._show_tab("cerberus")
        for child in (self.panel.hephaestus, self.panel.hephaestus.aether,
                      self.panel.emanon, self.panel.argus):
            child.update(None)
            self.root.update_idletasks()


if __name__ == "__main__":
    unittest.main(verbosity=2)
