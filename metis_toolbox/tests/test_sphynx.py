"""
test_sphynx.py — unit tests for sphynx.py (PIN verification + attempt count).

The deterministic seam is _DATA_PATH: each test points it at a temp json
file holding a hash for a synthetic test PIN, so the real sphynx_data.json
(and the real PIN) never enters this file. Run from the package root:

    python -X utf8 -m unittest tests.test_sphynx
"""

import hashlib
import importlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sphynx

_TEST_PIN  = "9137"
_TEST_HASH = hashlib.sha256(_TEST_PIN.encode("utf-8")).hexdigest()


class TestSphynx(unittest.TestCase):

    def setUp(self):
        self._orig_path = sphynx._DATA_PATH
        self._tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump({"pin_hash": _TEST_HASH}, self._tmp)
        self._tmp.close()
        sphynx._DATA_PATH = self._tmp.name
        sphynx._ATTEMPTS_REMAINING = 3

    def tearDown(self):
        sphynx._DATA_PATH = self._orig_path
        sphynx._ATTEMPTS_REMAINING = 3
        os.unlink(self._tmp.name)

    # ── verify() ────────────────────────────────────────────────────────

    def test_correct_pin(self):
        self.assertTrue(sphynx.verify(_TEST_PIN))
        self.assertEqual(sphynx.attempts_left(), 3)   # a hit never decrements

    def test_correct_pin_with_whitespace(self):
        self.assertTrue(sphynx.verify(f"  {_TEST_PIN}  \n"))

    def test_wrong_pin_decrements(self):
        self.assertFalse(sphynx.verify("0000"))
        self.assertEqual(sphynx.attempts_left(), 2)

    def test_exhaustion(self):
        for _ in range(3):
            sphynx.verify("0000")
        self.assertEqual(sphynx.attempts_left(), 0)

    def test_post_exhaustion_call_stays_false_and_floored(self):
        for _ in range(3):
            sphynx.verify("0000")
        # Even the correct PIN must fail once attempts are exhausted.
        self.assertFalse(sphynx.verify(_TEST_PIN))
        self.assertEqual(sphynx.attempts_left(), 0)

    def test_fresh_import_resets_counter(self):
        """Proves the counter is in-memory-only: a fresh module import (i.e.
        a fresh process) always starts back at 3 attempts."""
        sphynx.verify("0000")
        self.assertEqual(sphynx.attempts_left(), 2)
        importlib.reload(sphynx)
        self.assertEqual(sphynx.attempts_left(), 3)

    # ── hash file failures ──────────────────────────────────────────────

    def test_missing_hash_file_raises(self):
        sphynx._DATA_PATH = os.path.join(tempfile.gettempdir(), "does_not_exist_sphynx.json")
        with self.assertRaises(sphynx.HashFileError):
            sphynx.verify(_TEST_PIN)

    def test_malformed_hash_file_raises(self):
        bad = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        bad.write("{not valid json")
        bad.close()
        sphynx._DATA_PATH = bad.name
        try:
            with self.assertRaises(sphynx.HashFileError):
                sphynx.verify(_TEST_PIN)
        finally:
            os.unlink(bad.name)

    def test_hash_file_missing_pin_hash_key_raises(self):
        empty = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump({}, empty)
        empty.close()
        sphynx._DATA_PATH = empty.name
        try:
            with self.assertRaises(sphynx.HashFileError):
                sphynx.verify(_TEST_PIN)
        finally:
            os.unlink(empty.name)

    # ── preflight() ─────────────────────────────────────────────────────

    def test_preflight_ok_when_file_valid(self):
        sphynx.preflight()   # must not raise

    def test_preflight_raises_on_missing_file(self):
        sphynx._DATA_PATH = os.path.join(tempfile.gettempdir(), "does_not_exist_sphynx2.json")
        with self.assertRaises(sphynx.HashFileError):
            sphynx.preflight()


class TestSphynxCreate(unittest.TestCase):
    """First-run writer: create() sets up a fresh gate (or a skipped one)."""

    def setUp(self):
        self._orig = sphynx._DATA_PATH
        self._dir = tempfile.mkdtemp(prefix="sphynx_create_")
        sphynx._DATA_PATH = os.path.join(self._dir, "sphynx_data.json")
        sphynx._ATTEMPTS_REMAINING = 3

    def tearDown(self):
        sphynx._DATA_PATH = self._orig
        sphynx._ATTEMPTS_REMAINING = 3
        for f in os.listdir(self._dir):
            os.unlink(os.path.join(self._dir, f))
        os.rmdir(self._dir)

    def test_create_then_verify_roundtrip(self):
        self.assertFalse(os.path.exists(sphynx._DATA_PATH))
        sphynx.create("7788", "What walks on four legs?")
        self.assertTrue(sphynx.verify("7788"))
        self.assertEqual(sphynx.riddle(), "What walks on four legs?")
        self.assertFalse(sphynx.is_disabled())

    def test_create_strips_pin_whitespace(self):
        sphynx.create("  4242  ", "riddle")
        self.assertTrue(sphynx.verify("4242"))

    def test_create_disabled_bypasses_gate(self):
        sphynx.create("", disabled=True)
        self.assertTrue(sphynx.is_disabled())
        with self.assertRaises(sphynx.HashFileError):
            sphynx.preflight()            # a disabled file carries no pin_hash
        self.assertEqual(sphynx.riddle(), sphynx._DEFAULT_RIDDLE)

    def test_create_is_atomic_no_tmp_left(self):
        sphynx.create("1234", "riddle")
        leftovers = [f for f in os.listdir(self._dir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_riddle_and_disabled_fall_back_when_file_absent(self):
        self.assertEqual(sphynx.riddle(), sphynx._DEFAULT_RIDDLE)
        self.assertFalse(sphynx.is_disabled())


if __name__ == "__main__":
    unittest.main(verbosity=2)
