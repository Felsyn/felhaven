"""
test_scribe.py — unit tests for scribe.py (tasks/notes persistence).

The deterministic seam is scribe.DATA_FILE: each test points it at a temp file
so the real scribe_data.json is never read or written. Run from the package
root:

    python -X utf8 -m unittest tests.test_scribe
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scribe


class ScribeBase(unittest.TestCase):
    def setUp(self):
        self._orig = scribe.DATA_FILE
        self._dir = tempfile.mkdtemp(prefix="scribe_test_")
        scribe.DATA_FILE = Path(self._dir) / "scribe_data.json"

    def tearDown(self):
        scribe.DATA_FILE = self._orig
        for f in os.listdir(self._dir):
            os.unlink(os.path.join(self._dir, f))
        os.rmdir(self._dir)

    def _write(self, obj):
        with open(scribe.DATA_FILE, "w", encoding="utf-8") as f:
            if isinstance(obj, str):
                f.write(obj)
            else:
                json.dump(obj, f)


class TestLoadDefaults(ScribeBase):
    def test_missing_file_returns_clean_default(self):
        # A fresh clone with no data file behaves as an empty list + notes.
        self.assertEqual(scribe.load_data(), {"tasks": [], "notes": ""})

    def test_garbled_file_falls_back_to_default(self):
        self._write("{ not valid json")
        self.assertEqual(scribe.load_data(), {"tasks": [], "notes": ""})

    def test_non_object_root_falls_back(self):
        # A valid-JSON but non-object file must not reach the panel as a list.
        self._write([1, 2, 3])
        self.assertEqual(scribe.load_data(), {"tasks": [], "notes": ""})

    def test_partial_object_gets_missing_keys(self):
        # notes-but-no-tasks must not KeyError the panel, which indexes ["tasks"].
        self._write({"notes": "hello"})
        data = scribe.load_data()
        self.assertEqual(data["notes"], "hello")
        self.assertEqual(data["tasks"], [])

    def test_load_never_raises_on_read_error(self):
        # An unreadable existing file degrades to the default, never propagates.
        self._write({"tasks": [], "notes": "x"})
        with mock.patch.object(Path, "read_text", side_effect=OSError("boom")):
            self.assertEqual(scribe.load_data(), {"tasks": [], "notes": ""})


class TestSaveRoundTrip(ScribeBase):
    def test_save_then_load(self):
        data = {"tasks": [{"text": "milk", "done": False}], "notes": "buy soon"}
        scribe.save_data(data)
        self.assertEqual(scribe.load_data(), data)

    def test_save_is_atomic_no_tmp_left(self):
        scribe.save_data({"tasks": [], "notes": "x"})
        leftovers = [f for f in os.listdir(self._dir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_unicode_notes_survive_round_trip(self):
        data = {"tasks": [], "notes": "café — naïve ☑"}
        scribe.save_data(data)
        self.assertEqual(scribe.load_data()["notes"], "café — naïve ☑")


class TestSaveAtomicity(ScribeBase):
    def test_failed_commit_leaves_original_intact(self):
        # Seed a known-good file, then make the commit step (os.replace) fail
        # mid-save. The atomic write must leave the ORIGINAL untouched — the old
        # truncate-in-place write would have already destroyed it.
        good = {"tasks": [{"text": "keep me", "done": False}], "notes": "safe"}
        scribe.save_data(good)

        with mock.patch("scribe.os.replace", side_effect=OSError("disk full")):
            scribe.save_data({"tasks": [], "notes": "clobbered"})

        # Original survived, no temp litter, no exception raised.
        self.assertEqual(scribe.load_data(), good)
        leftovers = [f for f in os.listdir(self._dir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_save_never_raises_on_write_error(self):
        with mock.patch("scribe.tempfile.mkstemp", side_effect=OSError("nope")):
            try:
                scribe.save_data({"tasks": [], "notes": "x"})
            except Exception as e:  # pragma: no cover - the assertion is the point
                self.fail(f"save_data raised {e!r} — it must swallow and log")


if __name__ == "__main__":
    unittest.main()
