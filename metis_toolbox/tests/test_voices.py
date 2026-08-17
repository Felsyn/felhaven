"""
test_voices.py — hermetic tests for the voice roster + Pythia's saved pick.

No kokoro model, no audio device, no real user state: _DATA_PATH is redirected
at a temp dir for every persistence test (the test_machine_spirit.py pattern).

The one test that CAN'T be hermetic is TestCatalogMatchesTheModel: CATALOG is
hardcoded because kokoro_models/ is gitignored, so the only thing keeping it
honest is reading the real voices-v1.0.bin when a developer happens to have it.
It skips in CI and on a fresh clone — that is the point, not a gap.

    python -X utf8 -m unittest tests.test_voices
"""

import json
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import voices


class TestCatalog(unittest.TestCase):

    def test_every_id_is_well_formed_and_english(self):
        for v in voices.CATALOG:
            prefix, _, name = v.partition("_")
            self.assertEqual(len(prefix), 2, v)
            self.assertIn(prefix[0], "ab", f"{v} is not an en-us voice")
            self.assertIn(prefix[1], "fm", v)
            self.assertTrue(name, v)

    def test_no_duplicates(self):
        self.assertEqual(len(voices.CATALOG), len(set(voices.CATALOG)))

    def test_labels_are_unique_so_the_menu_can_map_back(self):
        # VoiceBar builds a {label: id} dict — a collision would silently make
        # one voice unpickable.
        labels = [voices.label(v) for v in voices.CATALOG]
        self.assertEqual(len(labels), len(set(labels)))

    def test_label_reads_as_prose(self):
        self.assertEqual(voices.label("af_nicole"), "Nicole — American female")
        self.assertEqual(voices.label("bm_george"), "George — British male")

    def test_label_passes_through_an_unknown_id(self):
        # Never mangle into a blank menu entry (see label()'s docstring).
        for junk in ("", "nonsense", "xx_bob", "af", "zz_"):
            self.assertEqual(voices.label(junk), junk)


class TestCatalogMatchesTheModel(unittest.TestCase):
    """Lockstep guard: the hardcoded roster vs. the real voices file.

    voices-v1.0.bin is an NPZ archive — a zip whose members are '<voice>.npy'.
    Skipped when it is absent (gitignored: CI and a fresh clone never have it).
    """

    def test_catalog_is_the_english_subset_of_the_bin(self):
        import calliope
        bin_path = calliope._resolve(calliope._CONFIG["voices_path"])
        if not bin_path.is_file():
            self.skipTest(f"{bin_path.name} not present (kokoro_models/ is gitignored)")
        with zipfile.ZipFile(bin_path) as z:
            shipped = {n[:-4] if n.endswith(".npy") else n for n in z.namelist()}
        english = {v for v in shipped if v[:1] in ("a", "b")}
        self.assertEqual(set(voices.CATALOG), english)


class TestPersistence(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="voices_")
        self._orig = voices._DATA_PATH
        voices._DATA_PATH = os.path.join(self._dir, "voice_config.json")

    def tearDown(self):
        voices._DATA_PATH = self._orig
        for f in os.listdir(self._dir):
            os.unlink(os.path.join(self._dir, f))
        os.rmdir(self._dir)

    def test_no_file_falls_back_to_default(self):
        self.assertEqual(voices.pythia_voice(), voices.default())

    def test_save_then_read_round_trips(self):
        voices.save_pythia_voice("bm_george")
        self.assertEqual(voices.pythia_voice(), "bm_george")

    def test_save_is_read_fresh_not_cached(self):
        voices.save_pythia_voice("bm_george")
        voices.save_pythia_voice("af_bella")
        self.assertEqual(voices.pythia_voice(), "af_bella")

    def test_unknown_voice_raises_and_writes_nothing(self):
        with self.assertRaises(voices.VoiceError):
            voices.save_pythia_voice("jf_alpha")     # a real kokoro voice, not en
        self.assertFalse(os.path.exists(voices._DATA_PATH))

    def test_garbled_file_falls_back_instead_of_raising(self):
        with open(voices._DATA_PATH, "w", encoding="utf-8") as f:
            f.write("{not json at all")
        self.assertEqual(voices.pythia_voice(), voices.default())

    def test_non_dict_json_falls_back(self):
        with open(voices._DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(["af_bella"], f)
        self.assertEqual(voices.pythia_voice(), voices.default())

    def test_retired_voice_falls_back(self):
        # A pick saved by an older build whose id has since left CATALOG must
        # not be handed to kokoro.
        with open(voices._DATA_PATH, "w", encoding="utf-8") as f:
            json.dump({"pythia": "af_retired"}, f)
        self.assertEqual(voices.pythia_voice(), voices.default())

    def test_no_temp_files_survive_a_save(self):
        voices.save_pythia_voice("am_adam")
        self.assertEqual(os.listdir(self._dir), ["voice_config.json"])


class TestDefault(unittest.TestCase):

    def test_default_is_always_offerable(self):
        self.assertIn(voices.default(), voices.CATALOG)


if __name__ == "__main__":
    unittest.main(verbosity=2)
