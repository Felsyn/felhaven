"""
test_sphynx_panel_smoke.py — headless Tk smoke test for sphynx_panel.

SphynxPanel owns its OWN tk.Tk() root (it is the boot entry point launched by
Felhaven.bat, not a Card under panels/), so each test builds and destroys one.
Two hermetic guards make that safe:

  * subprocess.Popen is patched, so a stray _launch_felhaven can never spawn the
    real felhaven.py — the whole point of guarding the gate in a test.
  * sphynx._DATA_PATH is redirected at a temp dir, so no test reads or writes
    the real config/sphynx_data.json (or the author's PIN).

The PIN check is Cerberus's sealed scrypt (DPAPI, so Windows only);
tests/_cerberus_sandbox.cheap_pin_cost() makes it cheap for each test.

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
from tests._cerberus_sandbox import cheap_pin_cost

_PIN = "fake-panel-pin"


@unittest.skipUnless(sys.platform == "win32", "the PIN check needs DPAPI")
class TestSphynxPanelSmoke(unittest.TestCase):

    def setUp(self):
        cheap_pin_cost(self)
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
                # The panel's own after() calls (the 300 ms start, the reveal)
                # outlive a test that ends first; left pending, Tcl later prints
                # "invalid command name ..._start" into whatever test is running.
                for after_id in p.root.tk.splitlist(p.root.tk.call("after", "info")):
                    p.root.after_cancel(after_id)
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
        sphynx.create(_PIN, "Who am I?")

    def _seed_old_pin(self) -> None:
        # The form before 2026-11-26: an unsalted SHA-256.
        with open(sphynx._DATA_PATH, "w", encoding="utf-8") as f:
            json.dump({"pin_hash": hashlib.sha256(_PIN.encode("utf-8")).hexdigest(),
                       "riddle": "Who am I?"}, f)

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
        p._riddle_entry.insert(0, "fake riddle: what has keys but no locks?")
        p._pin_entry.insert(0, "fake-chosen-pin")
        p._confirm_entry.insert(0, "fake-chosen-pin")
        p._on_create()
        p.root.update_idletasks()
        self.assertTrue(os.path.exists(sphynx._DATA_PATH))
        self.assertTrue(sphynx.verify("fake-chosen-pin"))
        self.assertTrue(p._success)

    def test_create_rejects_mismatched_pins(self):
        p = self._panel()
        p._begin_first_run()
        p._riddle_entry.insert(0, "fake riddle")
        p._pin_entry.insert(0, "fake-pin-one")
        p._confirm_entry.insert(0, "fake-pin-two")
        p._on_create()
        p.root.update_idletasks()
        self.assertFalse(os.path.exists(sphynx._DATA_PATH))   # nothing written
        self.assertFalse(p._success)
        self.assertIn("match", p._setup_status.cget("text").lower())

    def test_first_run_says_to_use_a_pin_used_nowhere_else(self):
        # A short PIN can still be guessed from this account (sphynx.py).
        p = self._panel()
        p._begin_first_run()
        self.assertIn("Use a PIN you use nowhere else", p._log.get("1.0", "end"))

    def test_a_gate_that_cannot_be_written_shows_the_class_not_the_path(self):
        # §2: an OSError's text names the file's full path.
        p = self._panel()
        p._begin_first_run()
        p._riddle_entry.insert(0, "fake riddle")
        p._pin_entry.insert(0, "fake-gate-pin")
        p._confirm_entry.insert(0, "fake-gate-pin")
        boom = PermissionError(13, "Access is denied", str(sphynx._DATA_PATH))
        with mock.patch.object(sphynx, "create", side_effect=boom):
            p._on_create()
        text = p._setup_status.cget("text")
        self.assertIn("PermissionError", text)
        self.assertNotIn(self._dir, text)
        self.assertFalse(p._success)

    def test_a_skip_that_cannot_be_saved_shows_the_class_not_the_path(self):
        p = self._panel()
        p._begin_first_run()
        boom = PermissionError(13, "Access is denied", str(sphynx._DATA_PATH))
        with mock.patch.object(sphynx, "create", side_effect=boom):
            p._on_skip()
        text = p._setup_status.cget("text")
        self.assertIn("PermissionError", text)
        self.assertNotIn(self._dir, text)
        self.assertFalse(p._success)

    def test_the_entry_boxes_type_with_the_theme_cursor(self):
        # They set no insertbackground of their own (§7): the theme's option
        # database gives it, so this holds only while the root inits it.
        p = self._panel()
        for entry in (p._entry, p._riddle_entry, p._pin_entry, p._confirm_entry):
            self.assertEqual(entry.cget("insertbackground"), sphynx_panel.C["text1"])

    # ── gate verification (sphynx_panel._on_submit) ─────────────────────

    def test_wrong_pin_decrements_and_stays_gated(self):
        self._seed_pin()
        p = self._panel()
        p._start()
        p._entry.insert(0, "fake-wrong-pin")
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

    def test_an_old_gate_file_still_opens_and_is_sealed(self):
        self._seed_old_pin()
        p = self._panel()
        p._start()
        p.root.update_idletasks()
        self.assertEqual(p._riddle_line, "Who am I?")    # a gate, not first-run setup
        p._entry.insert(0, _PIN)
        p._on_submit()
        self.assertTrue(p._success)
        with open(sphynx._DATA_PATH, encoding="utf-8") as f:
            self.assertNotIn("pin_hash", json.load(f))


class TestTheGateLogsItsErrors(unittest.TestCase):
    """Run by Felhaven.bat under pythonw, the gate has no stderr, and it never
    set up logging, so an exception nothing caught left no trace (BACKLOG,
    routed to S26). The program block is read, not run: running it opens the
    real logs/sphynx.log and the gate window."""

    def test_the_program_block_sets_up_logging_and_tk_s_hook(self):
        import ast
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "sphynx_panel.py")
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        main = next(node for node in tree.body if isinstance(node, ast.If)
                    and ast.unparse(node.test) == "__name__ == '__main__'")
        calls = [ast.unparse(node) for stmt in main.body
                 for node in ast.walk(stmt) if isinstance(node, ast.Call)]
        self.assertIn("setup_logging('sphynx')", calls)
        self.assertIn("log_tk_errors(app.root)", calls)
        setup_at = calls.index("setup_logging('sphynx')")
        self.assertLess(setup_at, calls.index("SphynxPanel()"),
                        "logging must be up before the gate is built")


if __name__ == "__main__":
    unittest.main(verbosity=2)
