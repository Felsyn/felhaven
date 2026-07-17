"""
test_midas_panel_smoke.py — headless Tk smoke test for the Cerberus-gated
MidasPanel (Phase 3: Dynastic Vault behind the guardian's PIN).

Same headless approach as the other panel smoke tests: a withdrawn tk.Tk()
root driven with update_idletasks(). cerberus's paths are redirected at a temp
dir so the real PIN never enters the test. midas._load_watchlist is stubbed so
the panel has a stable set of tickers regardless of the host config.

    python -X utf8 -m unittest tests.test_midas_panel_smoke
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
from tools import midas

_PIN  = "1408"
_SALT = b"sixteen_byte_slt"
_HASH = hashlib.sha256(_SALT + _PIN.encode("utf-8")).hexdigest()


class _Base(unittest.TestCase):

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
        # The gate now calls cerberus.unlock() (not just verify()), which also
        # touches the Vault and Ledger — every path constant must be
        # redirected, not just _DATA_PATH, or these tests would read/write the
        # real cerberus_vault.json / cerberus_ledger.json.
        self._orig = {k: getattr(cerberus, k) for k in
                      ("_DATA_PATH", "_VAULT_PATH", "_MANIFEST_PATH",
                       "_LEDGER_PATH", "_KDF_ITERS")}
        cerberus._DATA_PATH     = os.path.join(self._dir, "cerberus_data.json")
        cerberus._VAULT_PATH    = os.path.join(self._dir, "cerberus_vault.json")
        cerberus._MANIFEST_PATH = os.path.join(self._dir, "cerberus_manifest.json")
        cerberus._LEDGER_PATH   = os.path.join(self._dir, "cerberus_ledger.json")
        cerberus._KDF_ITERS = 1000
        cerberus._ATTEMPTS_REMAINING = 3
        cerberus.lock()
        with open(cerberus._DATA_PATH, "w", encoding="utf-8") as f:
            json.dump({"verify_salt": _SALT.hex(), "pin_hash": _HASH}, f)

        self._orig_wl = midas._load_watchlist
        midas._load_watchlist = lambda: ["AAPL", "MSFT"]

        from panels.midas_panel import MidasPanel
        self.panel = MidasPanel(self.root)
        self.panel.pack()
        self.root.update_idletasks()

    def tearDown(self):
        midas._load_watchlist = self._orig_wl
        self.panel.destroy()
        for k, v in self._orig.items():
            setattr(cerberus, k, v)
        cerberus._ATTEMPTS_REMAINING = 3
        cerberus.lock()
        shutil.rmtree(self._dir, ignore_errors=True)

    def _unlock(self, pin=_PIN):
        self.panel._gate_entry.insert(0, pin)
        self.panel._on_gate_submit()
        self.root.update_idletasks()


class TestMidasGate(_Base):

    def test_starts_sealed(self):
        self.assertFalse(self.panel._unlocked)
        self.assertFalse(self.panel._content_built)

    def test_wrong_pin_stays_sealed(self):
        self._unlock("0000")
        self.assertFalse(self.panel._unlocked)
        self.assertIn("wrong", self.panel._gate_status.cget("text").lower())

    def test_correct_pin_reveals_tabs(self):
        self._unlock()
        self.assertTrue(self.panel._unlocked)
        self.assertTrue(self.panel._content_built)
        self.assertEqual(self.panel._active, "prices")
        self.assertEqual(set(self.panel._tabs), {"prices", "ledger"})

    def test_ticks_buffered_while_sealed_then_applied(self):
        # A tick arrives before unlock: it must not crash (no widgets yet) and
        # must be applied once the gate opens.
        data = {"tickers": [
            {"symbol": "AAPL", "price_fmt": "$100.00", "pct_fmt": "+1.0%",
             "direction": "up"},
        ]}
        self.panel.update(data)                      # sealed → buffered
        self.assertEqual(self.panel._pending, data)
        self._unlock()
        price_lbl, _pct = self.panel._row_widgets["AAPL"]
        self.assertEqual(price_lbl.cget("text"), "$100.00")
        self.assertIsNone(self.panel._pending)

    def test_gate_is_alarm_red_on_black(self):
        # The gate must render in the reserved alarm red on a black background.
        self.assertEqual(str(self.panel._gate.cget("bg")), theme.C["bg"])
        self.assertEqual(str(self.panel._gate_entry.cget("fg")), theme.C["red"])

    def test_tab_switch_after_unlock(self):
        self._unlock()
        self.panel._show_tab("ledger")
        self.root.update_idletasks()
        self.assertEqual(self.panel._active, "ledger")

    def test_missing_pin_file_disables_gate(self):
        cerberus._DATA_PATH = os.path.join(self._dir, "gone.json")
        from panels.midas_panel import MidasPanel
        panel = MidasPanel(self.root)
        panel.pack()
        self.root.update_idletasks()
        self.assertEqual(str(panel._gate_entry.cget("state")), "disabled")
        panel.destroy()

    def test_gate_uses_cerberus_session_not_just_verify(self):
        # The gate opens a real Cerberus session (unlock), not just a PIN
        # check — is_unlocked() must be true afterward, the same shared
        # session the Cerberus tab would see.
        self._unlock()
        self.assertTrue(cerberus.is_unlocked())

    def test_reseals_when_session_locked_elsewhere(self):
        # Simulates the Cerberus tab locking the shared session: Midas must
        # re-seal on its next tick rather than showing stale unlocked UI.
        self._unlock()
        self.assertTrue(self.panel._unlocked)
        cerberus.lock()
        self.panel.update(None)
        self.root.update_idletasks()
        self.assertFalse(self.panel._unlocked)
        self.assertEqual(str(self.panel._gate_entry.cget("state")), "normal")

    def test_reseal_then_reunlock_works(self):
        self._unlock()
        cerberus.lock()
        self.panel.update(None)
        self.root.update_idletasks()
        self._unlock()
        self.assertTrue(self.panel._unlocked)
        self.assertTrue(cerberus.is_unlocked())


if __name__ == "__main__":
    unittest.main(verbosity=2)
