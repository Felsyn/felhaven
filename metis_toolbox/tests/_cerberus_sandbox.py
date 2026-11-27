"""
_cerberus_sandbox.py — point Cerberus at a temp dir for one test.

Every test that touches Cerberus (its own, and the panels that unlock it) needs
the same three things, in this order:

  1. every path constant redirected at a fresh temp dir BEFORE anything runs,
     so the real PIN file, vault and ledger are never read or written;
  2. the RAM-only state reset WITHOUT calling lock(), because lock() writes a
     Ledger row when a session is open, and a row written before step 1 (or
     after the paths are restored) would land in the real ledger;
  3. the same reset again at the end, before the paths go back.

The fixture PIN is obviously fake on purpose: never four digits, so it can't
be someone's real PIN (tests/test_secret_fixtures.py holds that).

A real PIN check costs scrypt at OWASP's floor, about half a second, and every
sandboxed test sets a PIN. So the sandbox (and cheap_pin_cost(), for Sphynx's
tests) drops the cost and its floor to CHEAP_PIN_COST for the test, and puts
both back after. test_cerberus's TestPinCheck runs the real cost once.

Leading underscore => unittest's discover() does not collect this module.
"""

import os
import shutil
import tempfile
import unittest

import cerberus

FAKE_PIN = "fake-pin-for-tests"
WRONG_PIN = "fake-wrong-pin"

# scrypt's (N, r, p) at its smallest legal size: microseconds a check.
CHEAP_PIN_COST = (2, 1, 1)

_PATHS = ("_DATA_PATH", "_VAULT_PATH", "_MANIFEST_PATH", "_LEDGER_PATH",
          "_CONFIG_DIR")


def _lower_pin_cost() -> tuple[object, object]:
    saved = (cerberus._PIN_COST, cerberus._PIN_COST_FLOOR)
    cerberus._PIN_COST = cerberus._PIN_COST_FLOOR = CHEAP_PIN_COST
    return saved


def _restore_pin_cost(saved: tuple[object, object]) -> None:
    cerberus._PIN_COST, cerberus._PIN_COST_FLOOR = saved


def cheap_pin_cost(test: unittest.TestCase) -> None:
    """Make every PIN check in `test` cost microseconds, restored at cleanup.
    For tests that check PINs without the sandbox (Sphynx's)."""
    test.addCleanup(_restore_pin_cost, _lower_pin_cost())


def reset_state() -> None:
    """Close the session and clear every RAM-only field, writing nothing."""
    with cerberus._SESSION_LOCK:
        cerberus._SESSION_OPEN = False
        cerberus._LOCK_REASON = None
        cerberus._ATTEMPTS_REMAINING = 3
        cerberus._SESSION_READS.clear()
        cerberus._ALARMS.clear()
        cerberus._LAST_PRESENCE = None
        cerberus._LAST_CHECK = None
        cerberus._PRESENCE_FAILED = False


class CerberusSandbox:
    """start() redirects and resets; stop() resets and restores. The config
    root (_CONFIG_DIR) is the temp dir itself, so a manifest whose config_dir
    is that dir passes Custody's containment check."""

    def __init__(self, seed_pin: bool = True) -> None:
        self._seed_pin = seed_pin
        self.dir = ""
        self._orig: dict[str, object] = {}
        self._cost: tuple[object, object] | None = None

    def start(self) -> str:
        self.dir = tempfile.mkdtemp(prefix="cerberus_test_")
        self._orig = {k: getattr(cerberus, k) for k in _PATHS}
        self._cost = _lower_pin_cost()
        cerberus._DATA_PATH = os.path.join(self.dir, "cerberus_data.json")
        cerberus._VAULT_PATH = os.path.join(self.dir, "cerberus_vault.json")
        cerberus._MANIFEST_PATH = os.path.join(self.dir, "cerberus_manifest.json")
        cerberus._LEDGER_PATH = os.path.join(self.dir, "cerberus_ledger.json")
        cerberus._CONFIG_DIR = self.dir
        reset_state()
        if self._seed_pin:
            cerberus.set_pin(FAKE_PIN)
        return self.dir

    def stop(self) -> None:
        reset_state()
        for k, v in self._orig.items():
            setattr(cerberus, k, v)
        if self._cost is not None:
            _restore_pin_cost(self._cost)
            self._cost = None
        shutil.rmtree(self.dir, ignore_errors=True)
