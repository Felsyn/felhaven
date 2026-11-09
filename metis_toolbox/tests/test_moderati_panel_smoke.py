"""
test_moderati_panel_smoke.py — headless Tk smoke test for the Moderati host.

Same approach as test_vox_array_panel_smoke.py: a real tk.Tk() root we withdraw()
(no window flashes) and drive with update_idletasks() instead of mainloop(). No
exception raised == pass.

The Moderati host builds seven real tab bodies. The three file-backed ones are
redirected at a temp dir so no real user state is read or written and
construction is deterministic: cerberus (its data/vault/manifest/ledger paths —
an absent temp data file routes CerberusPanel to its first-run gate),
machine_spirit and themis (their _DATA_PATH → defaults). The polled bodies
(Hephaestus/Aether, Emanon, Argus) construct side-effect-free; they only read the
system on update(), which Kairos drives — never construction. DAEDALUS reads
nothing until its tab is shown (tests/test_daedalus_panel_smoke.py).

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
import voices


class TestModeratiPanelSmoke(unittest.TestCase):
    """Host builds all seven tab bodies and switches tabs without crashing."""

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
        from panels.daedalus_panel import DaedalusPanel

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
        # The MACHINE SPIRIT tab's narration-voice bar reads this at build time.
        self._orig_voices = voices._DATA_PATH
        voices._DATA_PATH = os.path.join(self._dir, "voice_config.json")

        self.VitalsPanel = VitalsPanel
        self.EmanonPanel = EmanonPanel
        self.ArgusPanel = ArgusPanel
        self.CerberusPanel = CerberusPanel
        self.ThemisPanel = ThemisPanel
        self.MachineSpiritPanel = MachineSpiritPanel
        self.DaedalusPanel = DaedalusPanel

        self.panel = ModeratiPanel(self.root)
        self.panel.pack()

    def tearDown(self):
        self.panel.destroy()
        for k, v in self._orig_cerb.items():
            setattr(cerberus, k, v)
        machine_spirit._DATA_PATH = self._orig_ms
        themis._DATA_PATH = self._orig_themis
        voices._DATA_PATH = self._orig_voices
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
        self.assertIsInstance(self.panel.daedalus, self.DaedalusPanel)

    def test_every_tab_label_carries_its_tooltip(self):
        from panels import virgil_tooltip
        tips = virgil_tooltip._shared(self.root)._tips
        self.assertEqual(sorted(self.panel._TIPS), sorted(self.panel._tabs))
        for key, label in self.panel._tabs.items():
            self.assertEqual(tips.get(str(label)), self.panel._TIPS[key], key)

    def test_starts_on_hephaestus_tab(self):
        self.assertEqual(self.panel._active, "hephaestus")

    def test_tab_switch_cycles_every_tab(self):
        for key in ("emanon", "argus", "cerberus", "themis",
                    "machine_spirit", "daedalus", "hephaestus"):
            self.panel._show_tab(key)
            self.root.update_idletasks()
            self.assertEqual(self.panel._active, key)

    def test_every_tab_fits_at_the_minimum_window(self):
        """A tab that does not fit is not clipped — pack DROPS it. The seventh
        tab (DAEDALUS) vanished at the old 700 px minimum; this holds the bar to
        felhaven.MIN_W so an eighth cannot do it silently.

        Requested widths only (the root is withdrawn, so nothing has a real
        size): the bar's request, less the last tab's trailing pad, which the
        bar never needs, plus the Card's own chrome around its body."""
        import felhaven
        from panels.sidebar import Sidebar
        # felhaven.py: body padx=16 each side, the content area's padx=(8, 0).
        content_w = felhaven.MIN_W - 2 * 16 - 8 - Sidebar.BASE_WIDTH
        self.root.update_idletasks()
        last = self.panel._tabs["daedalus"].master
        bar = last.master
        pad = last.pack_info()["padx"]               # (left, right), or one int
        trailing = int(pad[-1] if isinstance(pad, tuple) else pad)
        chrome = self.panel.winfo_reqwidth() - self.panel.body.winfo_reqwidth()
        needed = bar.winfo_reqwidth() - trailing + chrome
        self.assertLessEqual(
            needed, content_w,
            f"the Moderati tab bar needs {needed} px but a {felhaven.MIN_W} px "
            f"window leaves {content_w} — the last tab would be dropped")

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
