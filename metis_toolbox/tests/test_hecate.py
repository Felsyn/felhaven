"""
test_hecate.py — the roads layer against captured-real TIGER bytes: the
MTFCC tier table, normalization (projection + clipping) driven from the
roads_mini fixture, the load()-never-fetches contract, and ingest with the
county download mocked at the hermes seam.

    python -X utf8 -m unittest tests.test_hecate
"""

import array
import io
import os
import shutil
import sys
import tempfile
import threading
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import artemis, hermes
from tools import cadmus
from tools import hecate

_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "fixtures", "artemis")


def _part(*points):
    """A Cadmus part (a flat x, y array) from (x, y) points."""
    return array.array("d", [c for point in points for c in point])


def _fixture_pairs():
    with open(os.path.join(_FIXTURES, "roads_mini.shp"), "rb") as fh:
        shp = fh.read()
    with open(os.path.join(_FIXTURES, "roads_mini.dbf"), "rb") as fh:
        dbf = fh.read()
    return cadmus.read_pair(shp, dbf), shp, dbf


def _fixture_center(pairs):
    """A centre in the middle of the fixture's roads, recomputed from the
    data so the clip square actually contains them."""
    points = [pt for (_, parts), _ in pairs for part in parts
              for pt in cadmus.points(part)]
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return ((min(ys) + max(ys)) / 2.0, (min(xs) + max(xs)) / 2.0)


class TestTierTable(unittest.TestCase):

    def test_spec_tiers(self):
        self.assertEqual(hecate._TIER_BY_MTFCC["S1100"], (1, 0))
        self.assertEqual(hecate._TIER_BY_MTFCC["S1200"], (2, 0))
        self.assertEqual(hecate._TIER_BY_MTFCC["S1400"], (3, 0))
        self.assertEqual(hecate._TIER_BY_MTFCC["S1500"],
                         (3, artemis.FLAG_DASHED))
        self.assertEqual(hecate._TIER_BY_MTFCC["S1630"],
                         (3, artemis.FLAG_THIN))
        self.assertEqual(hecate._TIER_BY_MTFCC["S1740"],
                         (3, artemis.FLAG_DASHED))
        self.assertIn("S1780", hecate._SUPPRESSED)
        self.assertNotIn("S1780", hecate._TIER_BY_MTFCC)


class TestNormalize(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.pairs, _, _ = _fixture_pairs()
        cls.center = _fixture_center(cls.pairs)
        cls.features = hecate.normalize(cls.pairs, cls.center)

    def test_produces_clipped_road_features(self):
        self.assertTrue(self.features)
        for feat in self.features:
            self.assertEqual(feat["kind"], "road")
            self.assertGreaterEqual(len(feat["coords"]), 2)
            for east, north in feat["coords"]:
                self.assertLessEqual(abs(east), artemis.HALF_WIDTH_M)
                self.assertLessEqual(abs(north), artemis.HALF_WIDTH_M)

    def test_tiers_and_names_survive(self):
        tiers = {feat["tier"] for feat in self.features}
        self.assertTrue(tiers <= {1, 2, 3, 4})
        names = {feat["name"] for feat in self.features}
        self.assertIn("I- 79", names)         # the fixture's interstate

    def test_interstate_is_tier_one(self):
        interstates = [f for f in self.features if f["name"] == "I- 79"]
        self.assertTrue(interstates)
        self.assertEqual({f["tier"] for f in interstates}, {1})

    def test_suppressed_class_is_dropped(self):
        shape = (3, [_part((-80.3, 39.4), (-80.31, 39.41))])
        parking = [(shape, {"MTFCC": "S1780", "FULLNAME": "Lot Rd"})]
        self.assertEqual(hecate.normalize(parking, (39.4, -80.3)), [])

    def test_unknown_class_stays_visible(self):
        shape = (3, [_part((-80.3, 39.4), (-80.31, 39.41))])
        odd = [(shape, {"MTFCC": "S9999", "FULLNAME": "Mystery Way"})]
        feats = hecate.normalize(odd, (39.4, -80.3))
        self.assertEqual(len(feats), 1)
        self.assertEqual(feats[0]["tier"], 3)


class TestIngestAndLoad(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._orig_cache = artemis.CACHE_DIR
        artemis.CACHE_DIR = self._dir
        self.pairs, self._shp, self._dbf = _fixture_pairs()
        self.center = _fixture_center(self.pairs)
        self.area = artemis.area_id(*self.center)
        self.box = artemis.bbox(*self.center)

    def tearDown(self):
        artemis.CACHE_DIR = self._orig_cache
        shutil.rmtree(self._dir, ignore_errors=True)

    def _county_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("tl_2025_54033_roads.shp", self._shp)
            zf.writestr("tl_2025_54033_roads.dbf", self._dbf)
        return buf.getvalue()

    @staticmethod
    def _Resp(payload):
        """What hermes.get returns: a finished Reply."""
        return hermes.Reply(200, {}, payload, "https://census.example.invalid/x.zip")

    def test_ingest_writes_then_load_reads(self):
        os.makedirs(artemis.partial_dir(self.area))
        with mock.patch("tools.artemis.counties_for_bbox",
                        return_value=["54033"]), \
             mock.patch("tools.artemis.hermes.get",
                        return_value=self._Resp(self._county_zip())) as opened:
            hecate.ingest(self.area, self.center, self.box,
                          threading.Event(), lambda msg: None)
        self.assertIn("54033", opened.call_args[0][0])
        self.assertEqual(opened.call_args.kwargs["cap"], hecate._MAX_BYTES)
        artemis.commit_area(self.area)

        features = hecate.load(self.area)
        expected = hecate.normalize(self.pairs, self.center)
        self.assertEqual(len(features), len(expected))
        for got, want in zip(features, expected):
            self.assertEqual(
                (got["kind"], got["name"], got["tier"], got["flags"]),
                (want["kind"], want["name"], want["tier"], want["flags"]))
            self.assertEqual(len(got["coords"]), len(want["coords"]))
            for (ge, gn), (we, wn) in zip(got["coords"], want["coords"]):
                # The FVEC unit is an int16 half-metre — 0.25 m worst case.
                self.assertAlmostEqual(ge, we, delta=0.25)
                self.assertAlmostEqual(gn, wn, delta=0.25)

    def test_both_members_are_read_under_their_cap(self):
        os.makedirs(artemis.partial_dir(self.area))
        with mock.patch("tools.artemis.counties_for_bbox",
                        return_value=["54033"]), \
             mock.patch("tools.artemis.hermes.get",
                        return_value=self._Resp(self._county_zip())), \
             mock.patch("tools.artemis.read_member",
                        wraps=artemis.read_member) as member:
            hecate.ingest(self.area, self.center, self.box,
                          threading.Event(), lambda msg: None)
        self.assertEqual(sorted(c.args[1] for c in member.call_args_list),
                         ["tl_2025_54033_roads.dbf", "tl_2025_54033_roads.shp"])
        self.assertEqual({c.args[2] for c in member.call_args_list},
                         {hecate._MEMBER_MAX_BYTES})

    def test_cancel_between_counties(self):
        cancel = threading.Event()
        cancel.set()
        with mock.patch("tools.artemis.counties_for_bbox",
                        return_value=["54033"]):
            with self.assertRaises(artemis.IngestError) as ctx:
                hecate.ingest(self.area, self.center, self.box,
                              cancel, lambda msg: None)
        self.assertIn("cancelled", str(ctx.exception))

    def test_download_failure_is_slugged(self):
        os.makedirs(artemis.partial_dir(self.area))
        with mock.patch("tools.artemis.counties_for_bbox",
                        return_value=["54033"]), \
             mock.patch("tools.artemis.hermes.get",
                        side_effect=hermes.Offline("no route")):
            with self.assertRaises(artemis.IngestError) as ctx:
                hecate.ingest(self.area, self.center, self.box,
                              threading.Event(), lambda msg: None)
        self.assertIn("download_failed", str(ctx.exception))

    def test_load_missing_area_raises_and_never_fetches(self):
        with mock.patch("tools.artemis.hermes.get",
                        side_effect=AssertionError("load fetched!")):
            with self.assertRaises(FileNotFoundError):
                hecate.load("n00p000_e000p000")


if __name__ == "__main__":
    unittest.main()
