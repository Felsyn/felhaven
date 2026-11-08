"""
test_midas_panel_smoke.py — headless Tk smoke test for the Cerberus-gated
MidasPanel (the Dynastic Vault behind the guardian's PIN).

Same headless approach as the other panel smoke tests: a withdrawn tk.Tk()
root driven with update_idletasks(). Cerberus runs inside a CerberusSandbox
(tests/_cerberus_sandbox.py), so the real PIN file, vault and ledger never
enter the test and the fixture PIN is obviously fake. midas._load_watchlist is
stubbed so the panel has a stable set of tickers regardless of the host config.

    python -X utf8 -m unittest tests.test_midas_panel_smoke
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
import cerberus
from tests._cerberus_sandbox import FAKE_PIN, WRONG_PIN, CerberusSandbox
from tools import midas


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
        # The gate calls cerberus.unlock(), which writes the Ledger: every path
        # must point at the sandbox before the panel exists.
        self._box = CerberusSandbox()
        self._dir = self._box.start()

        self._orig_wl = midas._load_watchlist
        midas._load_watchlist = lambda: ["AAPL", "MSFT"]

        from panels.midas_panel import MidasPanel
        self.panel = MidasPanel(self.root)
        self.panel.pack()
        self.root.update_idletasks()

    def tearDown(self):
        midas._load_watchlist = self._orig_wl
        self.panel.destroy()
        self._box.stop()

    def _unlock(self, pin=FAKE_PIN):
        self.panel._gate_entry.insert(0, pin)
        self.panel._on_gate_submit()
        self.root.update_idletasks()


class TestMidasGate(_Base):

    def test_starts_sealed(self):
        self.assertFalse(self.panel._unlocked)
        self.assertFalse(self.panel._content_built)

    def test_wrong_pin_stays_sealed(self):
        self._unlock(WRONG_PIN)
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
        os.remove(cerberus._DATA_PATH)
        from panels.midas_panel import MidasPanel
        panel = MidasPanel(self.root)
        panel.pack()
        self.root.update_idletasks()
        self.assertEqual(str(panel._gate_entry.cget("state")), "disabled")
        self.assertIn("CERBERUS tab", panel._gate_status.cget("text"))
        self.assertNotIn("cerberus.py", panel._gate_status.cget("text"))
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

    def test_error_slug_maps_to_human_text(self):
        # The five midas codes must render as human text, not a constant "err"
        # and not the raw slug (§2 — the panel maps the slug).
        self._unlock()
        self.panel.update({"tickers": [{"symbol": "AAPL",
                                        "error": midas.ERR_VAULT_LOCKED}]})
        self.root.update_idletasks()
        _price, pct = self.panel._row_widgets["AAPL"]
        self.assertEqual(pct.cget("text"), "locked")

    def test_unmapped_error_falls_back_to_slug(self):
        # An unknown code is surfaced (as itself), never swallowed.
        self._unlock()
        self.panel.update({"tickers": [{"symbol": "AAPL", "error": "some_new_code"}]})
        self.root.update_idletasks()
        _price, pct = self.panel._row_widgets["AAPL"]
        self.assertEqual(pct.cget("text"), "some_new_code")

    def test_stale_price_is_dimmed_not_shown_as_fresh(self):
        self._unlock()
        fresh = {"tickers": [{"symbol": "AAPL", "price_fmt": "$100.00",
                              "pct_fmt": "+1.00%", "direction": "up"}]}
        self.panel.update(fresh)
        self.root.update_idletasks()
        price, _pct = self.panel._row_widgets["AAPL"]
        self.assertEqual(str(price.cget("fg")), theme.C["text1"])   # fresh = bright

        stale = {"tickers": [{"symbol": "AAPL", "price_fmt": "$100.00",
                              "pct_fmt": "+1.00%", "direction": "up", "stale": True}]}
        self.panel.update(stale)
        self.root.update_idletasks()
        price, pct = self.panel._row_widgets["AAPL"]
        self.assertEqual(str(price.cget("fg")), theme.C["text3"])   # stale = dimmed
        self.assertEqual(str(pct.cget("fg")), theme.C["text3"])


class TestSessionRider(_Base):
    """Kairos's 2 s `cerberus` worker reseals the Dynastic Vault through the
    rider, with the reason, without waiting for the 60 s midas tick."""

    def test_the_rider_reseals_with_the_reason(self):
        self._unlock()
        cerberus.lock(cerberus.LOCK_WINDOWS)
        self.panel.session_rider.update({"unlocked": False, "reason": "windows_lock",
                                         "alarms": []})
        self.root.update_idletasks()
        self.assertFalse(self.panel._unlocked)
        self.assertIn("Windows", self.panel._gate_status.cget("text"))
        self.assertIn("PIN again", self.panel._gate_status.cget("text"))

    def test_the_rider_survives_none_and_never_opens_the_gate(self):
        self.panel.session_rider.update(None)
        cerberus.unlock(FAKE_PIN)                   # unlocked from another tab
        self.panel.session_rider.update({"unlocked": True, "reason": None,
                                         "alarms": []})
        self.assertFalse(self.panel._unlocked)

    def test_the_midas_tick_check_names_the_reason_too(self):
        self._unlock()
        cerberus.lock(cerberus.LOCK_IDLE)
        self.panel.update(None)
        self.assertIn("hour", self.panel._gate_status.cget("text"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
