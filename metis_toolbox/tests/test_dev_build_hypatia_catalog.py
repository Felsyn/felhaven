"""
test_dev_build_hypatia_catalog.py — first coverage for the hand-run catalog
generator (the walkthrough finale).

No network: the generator's pure builders are fed small fixture strings, so the
parsing logic that silently rots when an upstream format shifts (the docstring
records one such shift — Stellarium dropping .fab for index.json) is pinned.
_fetch_text / main() hit the network and are left to the hand-run itself.

    python -X utf8 -m unittest tests.test_dev_build_hypatia_catalog
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import dev_build_hypatia_catalog as dev

# A tiny HYG-shaped CSV. Only the columns the builder reads are present.
# ra is decimal HOURS (the builder multiplies by 15 → degrees).
_CSV = """hip,proper,ra,dec,mag
32349,Sirius,6.0,-16.0,-1.46
,NoHip,1.0,1.0,2.0
99999,Dim,1.0,1.0,9.9
11767,Polaris,2.0,89.0,1.97
11767,PolarisDim,2.0,89.0,3.5
22222,DimFirst,4.0,4.0,4.0
22222,BrightSecond,4.0,4.0,2.0
44444,,3.0,3.0,4.0
badrow,X,1.0,1.0,1.0
"""


class BuildStarsTests(unittest.TestCase):

    def setUp(self):
        self.stars = dev._build_stars(_CSV)

    def test_keeps_only_valid_hip_rows_at_or_below_the_mag_limit(self):
        # Blank hip, unparseable hip, and mag 9.9 (> 4.5) are all excluded.
        self.assertEqual(set(self.stars), {32349, 11767, 22222, 44444})

    def test_converts_ra_hours_to_degrees_and_shapes_the_hypatia_record(self):
        sirius = self.stars[32349]
        self.assertEqual(sirius["ra"], 90.0)      # 6.0 h * 15
        self.assertEqual(sirius["dec"], -16.0)
        self.assertEqual(sirius["mag"], -1.46)
        self.assertEqual(sirius["id"], 32349)     # hypatia keys on "id"
        self.assertEqual(sirius["name"], "Sirius")

    def test_empty_proper_name_becomes_none(self):
        self.assertIsNone(self.stars[44444]["name"])

    def test_duplicate_hip_keeps_the_brightest_row_either_order(self):
        # bright-then-dim (Polaris 1.97 before 3.5) and dim-then-bright
        # (22222 4.0 before 2.0) both settle on the lowest mag.
        self.assertEqual(self.stars[11767]["mag"], 1.97)
        self.assertEqual(self.stars[22222]["mag"], 2.0)


class AddMissingEndpointsTests(unittest.TestCase):

    def test_pulls_in_a_below_cutoff_endpoint_without_disturbing_others(self):
        stars = dev._build_stars(_CSV)          # 99999 (mag 9.9) is NOT in here
        needed = {99999, 32349}                 # 32349 already present
        pulled = dev._add_missing_endpoints(stars, _CSV, needed)
        self.assertEqual(pulled, 1)
        self.assertIn(99999, stars)             # pulled in despite mag > limit
        self.assertEqual(stars[99999]["mag"], 9.9)
        self.assertEqual(stars[32349]["mag"], -1.46)   # untouched

    def test_returns_zero_when_nothing_is_missing(self):
        stars = dev._build_stars(_CSV)
        self.assertEqual(dev._add_missing_endpoints(stars, _CSV, {32349}), 0)


class BuildConstellationsTests(unittest.TestCase):

    def setUp(self):
        index = {"constellations": [
            {"id": "CON modern Ori", "lines": [[1, 2, 3]]},
            {"id": "CON modern Aql", "lines": [[10, 11], [20, 21]]},
            {"id": "CON modern Xyz", "lines": [[5, 6]]},   # unknown abbr
            {"id": "CON modern ",    "lines": [[9, 9]]},   # empty abbr → skipped
        ]}
        self.out = dev._build_constellations(index)

    def test_skips_empty_abbr_and_sorts_by_name(self):
        self.assertEqual([c["name"] for c in self.out], ["Aquila", "Orion", "Xyz"])

    def test_expands_polylines_into_consecutive_pairs(self):
        orion = next(c for c in self.out if c["abbr"] == "Ori")
        self.assertEqual(orion["lines"], [[1, 2], [2, 3]])
        aquila = next(c for c in self.out if c["abbr"] == "Aql")
        self.assertEqual(aquila["lines"], [[10, 11], [20, 21]])

    def test_unknown_abbr_falls_back_to_the_abbr_as_name(self):
        xyz = next(c for c in self.out if c["abbr"] == "Xyz")
        self.assertEqual(xyz["name"], "Xyz")


class AtomicWriteTests(unittest.TestCase):

    def test_round_trips_json_and_leaves_no_tmp_residue(self):
        d = tempfile.mkdtemp(prefix="dev_catalog_")
        try:
            path = os.path.join(d, "out.json")
            payload = {"_source": "x", "stars": [{"id": 1, "mag": 2.0}]}
            dev._atomic_write_json(path, payload)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), payload)
            self.assertFalse([f for f in os.listdir(d) if f.endswith(".tmp")])
        finally:
            for f in os.listdir(d):
                os.remove(os.path.join(d, f))
            os.rmdir(d)


if __name__ == "__main__":
    unittest.main()
