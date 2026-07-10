"""
test_morpheus_playlists.py — unit tests for the playlist write layer in
tools/morpheus.py (save_playlist / remove_playlist / _save_playlists).

Covers the round trip through morpheus_playlists.json and the atomic
temp-then-replace write pattern (Argus precedent, tools/argus.py
_save_timeline). No network, no Tk, no mpv/yt-dlp binaries touched — these
tests only exercise the JSON persistence functions. Run from the package
root:

    python -X utf8 -m unittest discover -s tests -p "test_*.py"
    python -X utf8 -m unittest tests.test_morpheus_playlists   # just this file

The real morpheus_playlists.json is never read or written here — every test
patches morpheus._PLAYLISTS_PATH to a TemporaryDirectory in setUp and
restores it in tearDown.
"""

import json
import os
import sys
import tempfile
import unittest

# Make the package root importable no matter where the runner is launched.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import morpheus


class _PatchedPlaylists(unittest.TestCase):
    """Redirect morpheus._PLAYLISTS_PATH into a throwaway temp dir so no test
    ever touches the real playlists file. Restored in tearDown."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_path = morpheus._PLAYLISTS_PATH
        morpheus._PLAYLISTS_PATH = os.path.join(self._tmp.name, "morpheus_playlists.json")

    def tearDown(self):
        morpheus._PLAYLISTS_PATH = self._orig_path
        self._tmp.cleanup()


class TestSaveAndLoad(_PatchedPlaylists):

    def test_round_trip(self):
        self.assertTrue(morpheus.save_playlist("A", "url1"))
        self.assertTrue(morpheus.save_playlist("B", "url2"))

        playlists = morpheus.load_playlists()
        self.assertEqual(len(playlists), 2)
        self.assertEqual(playlists[0], {"label": "A", "url": "url1"})
        self.assertEqual(playlists[1], {"label": "B", "url": "url2"})

    def test_strips_label_and_url(self):
        morpheus.save_playlist("  Lofi  ", "  http://example.com  ")
        playlists = morpheus.load_playlists()
        self.assertEqual(playlists[0], {"label": "Lofi", "url": "http://example.com"})

    def test_first_save_creates_file_with_expected_shape(self):
        self.assertFalse(os.path.exists(morpheus._PLAYLISTS_PATH))
        morpheus.save_playlist("A", "url1")
        self.assertTrue(os.path.exists(morpheus._PLAYLISTS_PATH))

        with open(morpheus._PLAYLISTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data, {"playlists": [{"label": "A", "url": "url1"}]})


class TestRemovePlaylist(_PatchedPlaylists):

    def test_remove_by_index_deletes_only_targeted_row(self):
        morpheus.save_playlist("A", "url1")
        morpheus.save_playlist("B", "url2")
        morpheus.save_playlist("C", "url3")

        self.assertTrue(morpheus.remove_playlist(1))

        playlists = morpheus.load_playlists()
        self.assertEqual(playlists, [
            {"label": "A", "url": "url1"},
            {"label": "C", "url": "url3"},
        ])

    def test_out_of_range_index_returns_false_and_leaves_file_untouched(self):
        morpheus.save_playlist("A", "url1")
        with open(morpheus._PLAYLISTS_PATH, "rb") as f:
            before = f.read()

        self.assertFalse(morpheus.remove_playlist(5))
        self.assertFalse(morpheus.remove_playlist(-1))

        with open(morpheus._PLAYLISTS_PATH, "rb") as f:
            after = f.read()
        self.assertEqual(before, after)


class TestAtomicWrite(_PatchedPlaylists):

    def test_failed_write_leaves_original_file_intact(self):
        morpheus.save_playlist("A", "url1")
        with open(morpheus._PLAYLISTS_PATH, "r", encoding="utf-8") as f:
            original = json.load(f)

        # object() is not JSON-serializable -> json.dump raises inside
        # _save_playlists, before os.replace ever runs.
        with self.assertRaises(TypeError):
            morpheus._save_playlists({"playlists": [object()]})

        with open(morpheus._PLAYLISTS_PATH, "r", encoding="utf-8") as f:
            reloaded = json.load(f)
        self.assertEqual(reloaded, original)

    def test_no_tmp_residue_on_success(self):
        morpheus.save_playlist("A", "url1")
        self.assertFalse(os.path.exists(morpheus._PLAYLISTS_PATH + ".tmp"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
