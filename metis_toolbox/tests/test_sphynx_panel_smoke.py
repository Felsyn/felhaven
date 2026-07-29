"""
test_sphynx_panel_smoke.py — headless Tk smoke test for sphynx_panel.

SphynxPanel owns its OWN tk.Tk() root (it is the boot entry point launched by
Felhaven.bat, not a Card under panels/), so each test builds and destroys one.
Two hermetic guards make that safe:

  * subprocess.Popen is patched, so a stray _launch_felhaven can never spawn the
    real felhaven.py — the whole point of guarding the gate in a test.
  * sphynx._DATA_PATH is redirected at a temp dir, so no test reads or writes
    the real config/sphynx_data.json (or the author's PIN).

after()-scheduled callbacks (the boot litany, the 500 ms launch) never fire:
tests drive with update_idletasks(), which processes idle tasks only, not timer
callbacks — and Popen is patched regardless. No mainloop, no window flash
(Windows; no Xvfb — S10). Run from the package root:

    python -X utf8 -m unittest tests.test_sphynx_panel_smoke
"""

import hashlib
import json
import os
import sys
import tempfile
import tkinter as tk
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sphynx
import sphynx_panel

_PIN  = "4661"
_HASH = hashlib.sha256(_PIN.encode("utf-8")).hexdigest()


class TestSphynxPanelSmoke(unittest.TestCase):

    def setUp(self):
        # Never spawn felhaven, whatever a flow does.
        self._popen = mock.patch("subprocess.Popen").start()
        self.addCleanup(mock.patch.stopall)

        self._dir = tempfile.mkdtemp(prefix="sphynx_panel_")
        self._orig_path = sphynx._DATA_PATH
        sphynx._DATA_PATH = os.path.join(self._dir, "sphynx_data.json")
        sphynx._ATTEMPTS_REMAINING = 3
        self._panels: list = []

    def tearDown(self):
        for p in self._panels:
            try:
                p.root.destroy()
            except tk.TclError:
                pass
        sphynx._DATA_PATH = self._orig_path
        sphynx._ATTEMPTS_REMAINING = 3
        for f in os.listdir(self._dir):
            os.unlink(os.path.join(self._dir, f))
        os.rmdir(self._dir)

    def _panel(self) -> "sphynx_panel.SphynxPanel":
        p = sphynx_panel.SphynxPanel()
        p.root.withdraw()            # no window flashes during the run
        p.root.update_idletasks()
        self._panels.append(p)
        return p

    def _seed_pin(self) -> None:
        with open(sphynx._DATA_PATH, "w", encoding="utf-8") as f:
            json.dump({"pin_hash": _HASH, "riddle": "Who am I?"}, f)

    # ── construction ────────────────────────────────────────────────────

    def test_panel_constructs_without_launching(self):
        p = self._panel()
        self.assertIsInstance(p._entry, tk.Entry)
        self.assertIsInstance(p._setup_frame, tk.Frame)
        self._popen.assert_not_called()

    # ── boot branches (sphynx_panel._start) ─────────────────────────────

    def test_no_file_offers_first_run_setup(self):
        # A fresh clone (no gate file) shows the SETUP form, not a locked gate.
        p = self._panel()
        p._start()
        p.root.update_idletasks()
        self.assertEqual(p._setup_frame.winfo_manager(), "pack")   # form shown
        self.assertFalse(p._success)
        self._popen.assert_not_called()

    def test_disabled_gate_stands_aside(self):
        sphynx.create("", disabled=True)
        p = self._panel()
        p._start()
        p.root.update_idletasks()
        self.assertTrue(p._success)          # launch scheduled via after(500)...
        self._popen.assert_not_called()      # ...which never fires in this test

    def test_stored_riddle_is_posed(self):
        self._seed_pin()
        p = self._panel()
        p._start()
        p.root.update_idletasks()
        self.assertEqual(p._riddle_line, "Who am I?")

    # ── first-run setup form ────────────────────────────────────────────

    def test_create_seals_gate_and_flags_success(self):
        p = self._panel()
        p._begin_first_run()
        p._riddle_entry.insert(0, "What has keys but no locks?")
        p._pin_entry.insert(0, "2468")
        p._confirm_entry.insert(0, "2468")
        p._on_create()
        p.root.update_idletasks()
        self.assertTrue(os.path.exists(sphynx._DATA_PATH))
        self.assertTrue(sphynx.verify("2468"))
        self.assertTrue(p._success)

    def test_create_rejects_mismatched_pins(self):
        p = self._panel()
        p._begin_first_run()
        p._riddle_entry.insert(0, "riddle")
        p._pin_entry.insert(0, "1111")
        p._confirm_entry.insert(0, "2222")
        p._on_create()
        p.root.update_idletasks()
        self.assertFalse(os.path.exists(sphynx._DATA_PATH))   # nothing written
        self.assertFalse(p._success)
        self.assertIn("match", p._setup_status.cget("text").lower())

    # ── gate verification (sphynx_panel._on_submit) ─────────────────────

    def test_wrong_pin_decrements_and_stays_gated(self):
        self._seed_pin()
        p = self._panel()
        p._start()
        p._entry.insert(0, "0000")
        p._on_submit()
        p.root.update_idletasks()
        self.assertEqual(sphynx.attempts_left(), 2)
        self.assertFalse(p._success)
        self._popen.assert_not_called()

    def test_correct_pin_flags_success(self):
        self._seed_pin()
        p = self._panel()
        p._start()
        p._entry.insert(0, _PIN)
        p._on_submit()
        p.root.update_idletasks()
        self.assertTrue(p._success)
        self._popen.assert_not_called()      # launch is scheduled via after(500)


if __name__ == "__main__":
    unittest.main(verbosity=2)
