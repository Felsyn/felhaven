"""
test_poseidon.py — the water layer against captured-real TIGER bytes: the
name-driven tier rule, polygon clipping that keeps lakes closed, the
load()-never-fetches contract, and ingest with both county downloads mocked
at the urllib seam (including the tolerated missing-AREAWATER case).

    python -X utf8 -m unittest tests.test_poseidon
"""

import io
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import artemis
from tools import cadmus
from tools import poseidon

_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "fixtures", "artemis")


def _pairs(stem):
    with open(os.path.join(_FIXTURES, stem + ".shp"), "rb") as fh:
        shp = fh.read()
    with open(os.path.join(_FIXTURES, stem + ".dbf"), "rb") as fh:
        dbf = fh.read()
    return cadmus.read_pair(shp, dbf), shp, dbf


def _center(pairs):
    points = [pt for (_, parts), _ in pairs for part in parts for pt in part]
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return ((min(ys) + max(ys)) / 2.0, (min(xs) + max(xs)) / 2.0)


class TestNormalizeLinear(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.pairs, _, _ = _pairs("water_line_mini")
        cls.center = _center(cls.pairs)
        cls.features = poseidon.normalize_linear(cls.pairs, cls.center)

    def test_all_water_lines_clipped(self):
        self.assertTrue(self.features)
        for feat in self.features:
            self.assertEqual(feat["kind"], "water_line")
            self.assertGreaterEqual(len(feat["coords"]), 2)
            for east, north in feat["coords"]:
                self.assertLessEqual(abs(east), artemis.HALF_WIDTH_M)
                self.assertLessEqual(abs(north), artemis.HALF_WIDTH_M)

    def test_named_streams_are_major_unnamed_are_minor(self):
        # The fixture holds six named runs/creeks and four unnamed ones.
        for feat in self.features:
            self.assertEqual(feat["tier"], 1 if feat["name"] else 2)
        names = {f["name"] for f in self.features if f["name"]}
        self.assertIn("West Fork Riv", names)

    def test_canal_stays_minor_even_when_named(self):
        shape = (3, [[(-80.3, 39.4), (-80.301, 39.401)]])
        canal = [(shape, {"MTFCC": "H3020", "FULLNAME": "Big Ditch"})]
        feats = poseidon.normalize_linear(canal, (39.4, -80.3))
        self.assertEqual(feats[0]["tier"], 2)

    def test_connector_is_suppressed(self):
        shape = (3, [[(-80.3, 39.4), (-80.301, 39.401)]])
        conn = [(shape, {"MTFCC": "H1100", "FULLNAME": ""})]
        self.assertEqual(poseidon.normalize_linear(conn, (39.4, -80.3)), [])


class TestNormalizeArea(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.pairs, _, _ = _pairs("water_area_mini")
        cls.center = _center(cls.pairs)
        cls.features = poseidon.normalize_area(cls.pairs, cls.center)

    def test_rings_stay_closed_and_fillable(self):
        self.assertTrue(self.features)
        for feat in self.features:
            self.assertEqual(feat["kind"], "water_area")
            # A fillable ring needs at least a triangle.
            self.assertGreaterEqual(len(feat["coords"]), 3)

    def test_lakes_and_reservoir_are_open_water(self):
        self.assertTrue(self.features)
        self.assertEqual({f["tier"] for f in self.features}, {1})
        names = {f["name"] for f in self.features if f["name"]}
        self.assertIn("Buffalo Creek Reservoir", names)   # the lone H2040

    def test_marsh_is_lesser_tier(self):
        ring = [(-80.30, 39.40), (-80.29, 39.40), (-80.29, 39.41),
                (-80.30, 39.41)]
        marsh = [((5, [ring]), {"MTFCC": "H2025", "FULLNAME": "Big Swamp"})]
        feats = poseidon.normalize_area(marsh, (39.405, -80.295))
        self.assertEqual(feats[0]["tier"], 2)

    def test_ring_entirely_outside_is_dropped(self):
        ring = [(-70.0, 20.0), (-70.01, 20.0), (-70.01, 20.01)]
        far = [((5, [ring]), {"MTFCC": "H2030", "FULLNAME": "Elsewhere"})]
        self.assertEqual(poseidon.normalize_area(far, (39.4, -80.3)), [])


class TestIngestAndLoad(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._orig_cache = artemis.CACHE_DIR
        artemis.CACHE_DIR = self._dir
        self.lin_pairs, self._lin_shp, self._lin_dbf = _pairs("water_line_mini")
        self.area_pairs, self._area_shp, self._area_dbf = _pairs("water_area_mini")
        self.center = _center(self.lin_pairs)
        self.area = artemis.area_id(*self.center)
        self.box = artemis.bbox(*self.center)

    def tearDown(self):
        artemis.CACHE_DIR = self._orig_cache
        shutil.rmtree(self._dir, ignore_errors=True)

    def _zip(self, stem, shp, dbf):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(stem + ".shp", shp)
            zf.writestr(stem + ".dbf", dbf)
        return buf.getvalue()

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _urlopen(self, area_error=None):
        """Serve the right fixture zip for whichever URL is asked for."""
        def opener(url, timeout=None):
            if "linearwater" in url:
                return self._Resp(self._zip("tl_2025_54033_linearwater",
                                            self._lin_shp, self._lin_dbf))
            if area_error is not None:
                raise area_error
            return self._Resp(self._zip("tl_2025_54033_areawater",
                                        self._area_shp, self._area_dbf))
        return opener

    @staticmethod
    def _http_404():
        return urllib.error.HTTPError("http://example/x", 404, "Not Found",
                                      {}, None)

    def test_ingest_writes_then_load_reads(self):
        os.makedirs(artemis.partial_dir(self.area))
        with mock.patch("tools.artemis.counties_for_bbox",
                        return_value=["54033"]), \
             mock.patch("tools.artemis.urllib.request.urlopen",
                        side_effect=self._urlopen()):
            poseidon.ingest(self.area, self.center, self.box,
                            threading.Event(), lambda msg: None)
        artemis.commit_area(self.area)

        features = poseidon.load(self.area)
        kinds = {f["kind"] for f in features}
        self.assertEqual(kinds, {"water_line", "water_area"})
        self.assertEqual(
            len([f for f in features if f["kind"] == "water_line"]),
            len(poseidon.normalize_linear(self.lin_pairs, self.center)))

    def test_progress_names_each_file_not_just_the_county(self):
        # Each county is two blocking downloads; one message per county reads
        # as a hang while the second file comes down.
        os.makedirs(artemis.partial_dir(self.area))
        seen = []
        with mock.patch("tools.artemis.counties_for_bbox",
                        return_value=["54033"]), \
             mock.patch("tools.artemis.urllib.request.urlopen",
                        side_effect=self._urlopen()):
            poseidon.ingest(self.area, self.center, self.box,
                            threading.Event(), seen.append)
        self.assertIn("water 1/1: county 54033 streams", seen)
        self.assertIn("water 1/1: county 54033 lakes", seen)

    def test_unpublished_area_water_is_tolerated(self):
        # An inland county with only streams is normal; a genuine 404 on
        # AREAWATER must not fail the layer.
        os.makedirs(artemis.partial_dir(self.area))
        with mock.patch("tools.artemis.counties_for_bbox",
                        return_value=["54033"]), \
             mock.patch("tools.artemis.urllib.request.urlopen",
                        side_effect=self._urlopen(area_error=self._http_404())):
            poseidon.ingest(self.area, self.center, self.box,
                            threading.Event(), lambda msg: None)
        artemis.commit_area(self.area)
        self.assertEqual(
            {f["kind"] for f in poseidon.load(self.area)}, {"water_line"})

    def test_timed_out_area_water_fails_instead_of_vanishing(self):
        """The scar: a TimeoutError on AREAWATER was swallowed as "this county
        has no lakes", so a whole county's water disappeared from a committed
        area with nothing on screen to reveal it."""
        os.makedirs(artemis.partial_dir(self.area))
        with mock.patch("tools.artemis.counties_for_bbox",
                        return_value=["54033"]), \
             mock.patch("tools.artemis.urllib.request.urlopen",
                        side_effect=self._urlopen(area_error=TimeoutError())):
            with self.assertRaises(artemis.IngestError) as ctx:
                poseidon.ingest(self.area, self.center, self.box,
                                threading.Event(), lambda msg: None)
        self.assertIn("download_failed", str(ctx.exception))

    def test_a_transient_failure_is_retried_before_giving_up(self):
        os.makedirs(artemis.partial_dir(self.area))
        calls = {"area": 0}
        good = self._urlopen()

        def flaky(url, timeout=None):
            if "areawater" in url:
                calls["area"] += 1
                if calls["area"] == 1:
                    raise TimeoutError()
            return good(url, timeout)

        with mock.patch("tools.artemis.counties_for_bbox",
                        return_value=["54033"]), \
             mock.patch("tools.artemis.urllib.request.urlopen",
                        side_effect=flaky):
            poseidon.ingest(self.area, self.center, self.box,
                            threading.Event(), lambda msg: None)
        artemis.commit_area(self.area)
        self.assertEqual(calls["area"], 2)
        self.assertIn("water_area",
                      {f["kind"] for f in poseidon.load(self.area)})

    def test_a_404_on_linear_water_is_fatal(self):
        """Only the optional AREAWATER fetch opts into allow_missing, so a
        404 on the required file is an ordinary download failure — every
        county publishes LINEARWATER, and a missing one means something is
        wrong rather than "this county has no streams"."""
        os.makedirs(artemis.partial_dir(self.area))

        def all_404(url, timeout=None):
            raise self._http_404()

        with mock.patch("tools.artemis.counties_for_bbox",
                        return_value=["54033"]), \
             mock.patch("tools.artemis.urllib.request.urlopen",
                        side_effect=all_404):
            with self.assertRaises(artemis.IngestError) as ctx:
                poseidon.ingest(self.area, self.center, self.box,
                                threading.Event(), lambda msg: None)
        self.assertIn("download_failed", str(ctx.exception))
        self.assertIn("linearwater", str(ctx.exception))

    def test_missing_linear_water_is_fatal(self):
        os.makedirs(artemis.partial_dir(self.area))
        with mock.patch("tools.artemis.counties_for_bbox",
                        return_value=["54033"]), \
             mock.patch("tools.artemis.urllib.request.urlopen",
                        side_effect=OSError("no route")):
            with self.assertRaises(artemis.IngestError) as ctx:
                poseidon.ingest(self.area, self.center, self.box,
                                threading.Event(), lambda msg: None)
        self.assertIn("download_failed", str(ctx.exception))

    def test_cancel_between_counties(self):
        cancel = threading.Event()
        cancel.set()
        with mock.patch("tools.artemis.counties_for_bbox",
                        return_value=["54033"]):
            with self.assertRaises(artemis.IngestError) as ctx:
                poseidon.ingest(self.area, self.center, self.box,
                                cancel, lambda msg: None)
        self.assertIn("cancelled", str(ctx.exception))

    def test_load_missing_area_raises_and_never_fetches(self):
        with mock.patch("tools.artemis.urllib.request.urlopen",
                        side_effect=AssertionError("load fetched!")):
            with self.assertRaises(FileNotFoundError):
                poseidon.load("n00p000_e000p000")


if __name__ == "__main__":
    unittest.main()
