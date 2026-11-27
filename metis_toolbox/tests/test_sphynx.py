"""
test_sphynx.py — unit tests for sphynx.py (PIN verification + attempt count).

The deterministic seam is _DATA_PATH: each test points it at a temp json
file, so the real sphynx_data.json (and the real PIN) never enters this file.
The PIN check is Cerberus's sealed scrypt, so these tests need Windows (DPAPI)
and borrow tests/_cerberus_sandbox.cheap_pin_cost() to make scrypt cheap; that
import also puts the fixture PINs under test_secret_fixtures, hence "fake".
Run from the package root:

    python -X utf8 -m unittest tests.test_sphynx
"""

import hashlib
import importlib
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cerberus
import sphynx
from tests._cerberus_sandbox import cheap_pin_cost

_TEST_PIN = "fake-sphynx-pin"
_WRONG_PIN = "fake-wrong-sphynx-pin"

_NEEDS_DPAPI = unittest.skipUnless(sys.platform == "win32", "DPAPI exists only on Windows")


class _Base(unittest.TestCase):
    """A fresh temp dir per test; the gate file path points into it."""

    def setUp(self):
        cheap_pin_cost(self)
        self._orig_path = sphynx._DATA_PATH
        self._dir = tempfile.mkdtemp(prefix="sphynx_test_")
        sphynx._DATA_PATH = os.path.join(self._dir, "sphynx_data.json")
        sphynx._ATTEMPTS_REMAINING = 3

    def tearDown(self):
        sphynx._DATA_PATH = self._orig_path
        sphynx._ATTEMPTS_REMAINING = 3
        for f in os.listdir(self._dir):
            os.unlink(os.path.join(self._dir, f))
        os.rmdir(self._dir)

    def _write(self, data) -> None:
        with open(sphynx._DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _data(self):
        with open(sphynx._DATA_PATH, encoding="utf-8") as f:
            return json.load(f)


@_NEEDS_DPAPI
class TestSphynx(_Base):

    def setUp(self):
        super().setUp()
        sphynx.create(_TEST_PIN, "riddle")

    # ── verify() ────────────────────────────────────────────────────────

    def test_correct_pin(self):
        self.assertTrue(sphynx.verify(_TEST_PIN))
        self.assertEqual(sphynx.attempts_left(), 3)   # a hit never decrements

    def test_correct_pin_with_whitespace(self):
        self.assertTrue(sphynx.verify(f"  {_TEST_PIN}  \n"))

    def test_wrong_pin_decrements(self):
        self.assertFalse(sphynx.verify(_WRONG_PIN))
        self.assertEqual(sphynx.attempts_left(), 2)

    def test_exhaustion(self):
        for _ in range(3):
            sphynx.verify(_WRONG_PIN)
        self.assertEqual(sphynx.attempts_left(), 0)

    def test_post_exhaustion_call_stays_false_and_floored(self):
        for _ in range(3):
            sphynx.verify(_WRONG_PIN)
        # Even the correct PIN must fail once attempts are exhausted.
        self.assertFalse(sphynx.verify(_TEST_PIN))
        self.assertEqual(sphynx.attempts_left(), 0)

    def test_fresh_import_resets_counter(self):
        """Proves the counter is in-memory-only: a fresh module import (i.e.
        a fresh process) always starts back at 3 attempts."""
        sphynx.verify(_WRONG_PIN)
        self.assertEqual(sphynx.attempts_left(), 2)
        path = sphynx._DATA_PATH
        importlib.reload(sphynx)
        sphynx._DATA_PATH = path          # the reload pointed it back at the real one
        self.assertEqual(sphynx.attempts_left(), 3)

    # ── the stored check ────────────────────────────────────────────────

    def test_the_gate_file_holds_nothing_in_the_clear(self):
        with open(sphynx._DATA_PATH, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(set(json.loads(text)), {"pin_check", "riddle", "disabled"})
        for word in (_TEST_PIN, "pin_hash", "salt", "digest"):
            self.assertNotIn(word, text)

    def test_cerberuss_check_wont_open_here(self):
        """The same recipe under another name: a Cerberus check moved into the
        gate file counts as no PIN, so it can't stand in for this one."""
        self._write({"pin_check": cerberus.pin_record(_TEST_PIN, "cerberus"),
                     "riddle": "riddle", "disabled": False})
        with self.assertRaises(sphynx.HashFileError):
            sphynx.preflight()
        with self.assertRaises(sphynx.HashFileError):
            sphynx.verify(_TEST_PIN)

    def test_a_garbled_check_is_no_pin(self):
        data = self._data()
        data["pin_check"]["blob"] = "Zm FrZQ=="
        self._write(data)
        with self.assertRaises(sphynx.HashFileError):
            sphynx.preflight()

    # ── hash file failures ──────────────────────────────────────────────

    def test_missing_hash_file_raises(self):
        os.unlink(sphynx._DATA_PATH)
        with self.assertRaises(sphynx.HashFileError):
            sphynx.verify(_TEST_PIN)

    def test_malformed_hash_file_raises(self):
        with open(sphynx._DATA_PATH, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        with self.assertRaises(sphynx.HashFileError):
            sphynx.verify(_TEST_PIN)

    def test_hash_file_with_no_check_raises(self):
        self._write({})
        with self.assertRaises(sphynx.HashFileError):
            sphynx.verify(_TEST_PIN)

    # ── preflight() ─────────────────────────────────────────────────────

    def test_preflight_ok_when_file_valid(self):
        sphynx.preflight()   # must not raise

    def test_preflight_raises_on_missing_file(self):
        os.unlink(sphynx._DATA_PATH)
        with self.assertRaises(sphynx.HashFileError):
            sphynx.preflight()


@_NEEDS_DPAPI
class TestOldGateFile(_Base):
    """A gate file from before 2026-11-26 holds an unsalted SHA-256. It still
    opens the gate, and the first right PIN rewrites it sealed."""

    def setUp(self):
        super().setUp()
        self._write({"pin_hash": hashlib.sha256(_TEST_PIN.encode("utf-8")).hexdigest(),
                     "riddle": "Who am I?", "disabled": False})

    def test_the_first_right_pin_seals_it_and_keeps_the_riddle(self):
        sphynx.preflight()
        self.assertTrue(sphynx.verify(_TEST_PIN))
        data = self._data()
        self.assertEqual(set(data), {"pin_check", "riddle", "disabled"})
        self.assertEqual(sphynx.riddle(), "Who am I?")
        self.assertTrue(sphynx.verify(_TEST_PIN))        # the sealed check works

    def test_a_wrong_pin_leaves_it_alone(self):
        before = self._data()
        self.assertFalse(sphynx.verify(_WRONG_PIN))
        self.assertEqual(self._data(), before)

    def test_a_failed_rewrite_keeps_the_old_file_and_warns(self):
        before = self._data()
        with mock.patch.object(cerberus, "pin_record", side_effect=OSError("fake")), \
                self.assertLogs("METIS.sphynx", level="WARNING"):
            self.assertTrue(sphynx.verify(_TEST_PIN))
        self.assertEqual(self._data(), before)
        self.assertTrue(sphynx.verify(_TEST_PIN))        # the next right PIN retries
        self.assertIn("pin_check", self._data())

    def test_a_garbled_old_hash_is_no_pin(self):
        for pin_hash in ("zz" * 32, "ab" * 31, 7, None):
            with self.subTest(pin_hash=pin_hash):
                self._write({"pin_hash": pin_hash})
                with self.assertRaises(sphynx.HashFileError):
                    sphynx.preflight()


@_NEEDS_DPAPI
class TestSphynxCreate(_Base):
    """First-run writer: create() sets up a fresh gate (or a skipped one)."""

    def test_create_then_verify_roundtrip(self):
        self.assertFalse(os.path.exists(sphynx._DATA_PATH))
        sphynx.create("fake-pin-one", "What walks on four legs?")
        self.assertTrue(sphynx.verify("fake-pin-one"))
        self.assertEqual(sphynx.riddle(), "What walks on four legs?")
        self.assertFalse(sphynx.is_disabled())

    def test_create_strips_pin_whitespace(self):
        sphynx.create("  fake-pin-two  ", "riddle")
        self.assertTrue(sphynx.verify("fake-pin-two"))

    def test_create_refuses_an_empty_pin(self):
        with self.assertRaises(ValueError):
            sphynx.create("   ", "riddle")
        self.assertFalse(os.path.exists(sphynx._DATA_PATH))

    def test_create_disabled_bypasses_gate(self):
        sphynx.create("", disabled=True)
        self.assertTrue(sphynx.is_disabled())
        with self.assertRaises(sphynx.HashFileError):
            sphynx.preflight()            # a disabled file carries no PIN check
        self.assertEqual(sphynx.riddle(), sphynx._DEFAULT_RIDDLE)

    def test_create_is_atomic_no_tmp_left(self):
        sphynx.create("fake-pin-three", "riddle")
        leftovers = [f for f in os.listdir(self._dir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_riddle_and_disabled_fall_back_when_file_absent(self):
        self.assertEqual(sphynx.riddle(), sphynx._DEFAULT_RIDDLE)
        self.assertFalse(sphynx.is_disabled())


if __name__ == "__main__":
    unittest.main(verbosity=2)
