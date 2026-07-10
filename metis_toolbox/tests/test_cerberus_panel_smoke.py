"""
test_cerberus_panel_smoke.py — headless Tk smoke test for CerberusPanel.

Same approach as test_morpheus_panel_smoke.py: a real tk.Tk() root we
withdraw() (no window flashes) and drive with update_idletasks() instead of
mainloop(). No exception raised == pass.

cerberus's path constants are redirected at a temp dir (as in test_cerberus)
so the real PIN / vault / ledger never enter this test, and the KDF cost is
shrunk for speed. The OS-editor handoff (_open_path) is monkeypatched so
Custody clicks never actually launch an editor.

    python -X utf8 -m unittest tests.test_cerberus_panel_smoke
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
import cerberus
import panels.cerberus_panel as cpanel
from panels.cerberus_panel import CerberusPanel

_PIN  = "1408"
_SALT = b"sixteen_byte_slt"
_HASH = hashlib.sha256(_SALT + _PIN.encode("utf-8")).hexdigest()


class _PanelBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._orig = {k: getattr(cerberus, k) for k in
                      ("_DATA_PATH", "_VAULT_PATH", "_MANIFEST_PATH",
                       "_LEDGER_PATH", "_KDF_ITERS")}
        cerberus._DATA_PATH     = os.path.join(self._dir, "cerberus_data.json")
        cerberus._VAULT_PATH    = os.path.join(self._dir, "cerberus_vault.json")
        cerberus._MANIFEST_PATH = os.path.join(self._dir, "cerberus_manifest.json")
        cerberus._LEDGER_PATH   = os.path.join(self._dir, "cerberus_ledger.json")
        cerberus._KDF_ITERS     = 1000
        cerberus._ATTEMPTS_REMAINING = 3
        cerberus.lock()

        with open(cerberus._DATA_PATH, "w", encoding="utf-8") as f:
            json.dump({"verify_salt": _SALT.hex(), "pin_hash": _HASH}, f)

        # Seed one secret and a config file + manifest pointing at the temp dir.
        cerberus.unlock(_PIN)
        cerberus.vault_set("openai_key", "sk-secret")
        cerberus.lock()
        with open(os.path.join(self._dir, "demo_config.json"), "w",
                  encoding="utf-8") as f:
            f.write("{}")
        with open(cerberus._MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump({"config_dir": self._dir,
                       "entries": [{"file": "demo_config.json",
                                    "desc": "a demo config"}]}, f)

        # Never launch a real editor.
        self._orig_open = cpanel._open_path
        self._opened: list[str] = []
        cpanel._open_path = lambda p: self._opened.append(p) or None

        self.panel = CerberusPanel(self.root)
        self.panel.pack()
        self.root.update_idletasks()

    def tearDown(self):
        cpanel._open_path = self._orig_open
        self.panel.destroy()
        for k, v in self._orig.items():
            setattr(cerberus, k, v)
        cerberus._ATTEMPTS_REMAINING = 3
        cerberus.lock()
        shutil.rmtree(self._dir, ignore_errors=True)

    def _unlock(self, pin=_PIN):
        self.panel._entry.insert(0, pin)
        self.panel._on_submit()
        self.root.update_idletasks()


class TestGate(_PanelBase):

    def test_starts_locked(self):
        self.assertFalse(self.panel._unlocked_built)
        self.assertFalse(cerberus.is_unlocked())

    def test_wrong_pin_stays_gated(self):
        self._unlock("0000")
        self.assertFalse(self.panel._unlocked_built)
        self.assertIn("attempt", self.panel._gate_status.cget("text").lower())
        self.assertEqual(cerberus.attempts_left(), 2)

    def test_correct_pin_unlocks_and_builds_sections(self):
        self._unlock()
        self.assertTrue(self.panel._unlocked_built)
        self.assertEqual(set(self.panel._sections), {"vault", "custody", "ledger"})
        self.assertTrue(cerberus.is_unlocked())

    def test_lock_returns_to_gate(self):
        self._unlock()
        self.panel._lock()
        self.root.update_idletasks()
        self.assertFalse(cerberus.is_unlocked())
        self.assertEqual(str(self.panel._entry.cget("state")), "normal")

    def test_missing_pin_file_enters_first_run_setup(self):
        # A fresh clone (no cerberus_data.json) offers in-app PIN setup rather
        # than disabling the gate and punting to the CLI.
        cerberus._DATA_PATH = os.path.join(self._dir, "gone.json")
        panel = CerberusPanel(self.root)
        panel.pack()
        self.root.update_idletasks()
        self.assertTrue(panel._first_run)
        self.assertEqual(str(panel._entry.cget("state")), "normal")
        self.assertEqual(str(panel._enter_btn.cget("text")), "CREATE")
        panel.destroy()

    def test_first_run_create_pin_seals_and_unlocks(self):
        cerberus._DATA_PATH = os.path.join(self._dir, "fresh.json")
        panel = CerberusPanel(self.root)
        panel.pack()
        self.root.update_idletasks()
        panel._entry.insert(0, "2024")
        panel._confirm_entry.insert(0, "2024")
        panel._create_pin()
        self.root.update_idletasks()
        self.assertFalse(panel._first_run)
        self.assertTrue(os.path.exists(cerberus._DATA_PATH))   # PIN persisted
        self.assertTrue(cerberus.is_unlocked())                # straight into vault
        self.assertTrue(panel._unlocked_built)
        panel.destroy()

    def test_first_run_mismatched_pins_do_not_write(self):
        cerberus._DATA_PATH = os.path.join(self._dir, "nope.json")
        panel = CerberusPanel(self.root)
        panel.pack()
        self.root.update_idletasks()
        panel._entry.insert(0, "1111")
        panel._confirm_entry.insert(0, "2222")
        panel._create_pin()
        self.root.update_idletasks()
        self.assertTrue(panel._first_run)                      # still in setup
        self.assertFalse(os.path.exists(cerberus._DATA_PATH))  # nothing written
        panel.destroy()


class TestUnlockedSections(_PanelBase):

    def test_vault_reveal_decrypts_and_logs(self):
        self._unlock()
        val_lbl = tk.Label(self.panel)
        btn = tk.Label(self.panel)
        self.panel._toggle_reveal("openai_key", val_lbl, btn)
        self.assertEqual(val_lbl.cget("text"), "sk-secret")
        self.assertEqual(btn.cget("text"), "hide")
        reads = [e for e in cerberus.ledger_entries()
                 if e["head"] == "vault" and e["target"] == "openai_key"]
        self.assertEqual(len(reads), 1)
        # Toggle back re-masks without another decrypt.
        self.panel._toggle_reveal("openai_key", val_lbl, btn)
        self.assertNotEqual(val_lbl.cget("text"), "sk-secret")

    def test_custody_open_logs_and_hands_off(self):
        self._unlock()
        self.panel._open_config("demo_config.json")
        self.assertEqual(len(self._opened), 1)
        self.assertTrue(self._opened[0].endswith("demo_config.json"))
        opens = [e for e in cerberus.ledger_entries() if e["head"] == "custody"]
        self.assertEqual(len(opens), 1)

    def test_open_folder_hands_off_config_dir(self):
        self._unlock()
        self.panel._open_folder()
        self.assertEqual(len(self._opened), 1)
        self.assertEqual(os.path.realpath(self._opened[0]),
                         os.path.realpath(self._dir))

    def test_sections_render_without_error(self):
        self._unlock()
        for key in ("vault", "custody", "ledger"):
            self.panel._sections[key].toggle()      # expand each
            self.root.update_idletasks()
            self.panel._render_section(key)          # and re-render
            self.root.update_idletasks()

    def test_refresh_open_is_safe(self):
        self._unlock()
        self.panel._refresh_open()
        self.root.update_idletasks()


if __name__ == "__main__":
    unittest.main(verbosity=2)
