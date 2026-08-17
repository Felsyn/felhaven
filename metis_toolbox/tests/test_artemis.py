"""
test_artemis.py — the chart core: projection maths, cache formats, the
county reference, the ingest orchestrator's atomicity, and the Kairos
status fetch(). No network anywhere — the reference download is fed a zip
built from the captured-real counties_mini fixture, and the orchestrator's
layer step is stubbed at the module seam.

    python -X utf8 -m unittest tests.test_artemis
"""

import contextlib
import io
import json
import math
import os
import shutil
import struct
import sys
import tempfile
import threading
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import artemis

_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "fixtures", "artemis")
_FIX = (39.2806, -80.3440)      # Clarksburg WV — the county seat at the centre
                                # of the fixture county. A civic landmark on
                                # purpose: this file ships publicly, so the
                                # test fix should not be anybody's address.
_EDGE_FIX = (39.4500, -80.3000)  # ~2 km inside the Harrison/Marion line, so an
                                 # ingest box centred here genuinely straddles
                                 # two counties (see TestReference).


def _haversine(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _feature(coords, kind="road", name="", tier=3, flags=0):
    return artemis.Feature(kind=kind, name=name, tier=tier, flags=flags,
                           coords=coords)


def _layer_for(kind):
    return artemis.LAYER_ROADS if kind == "road" else artemis.LAYER_WATER


class _CacheDirBase(unittest.TestCase):
    """Redirects artemis.CACHE_DIR at a temp dir (the midas path idiom)."""

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._orig_cache = artemis.CACHE_DIR
        artemis.CACHE_DIR = self._dir

    def tearDown(self):
        artemis.CACHE_DIR = self._orig_cache
        shutil.rmtree(self._dir, ignore_errors=True)
        # House idiom: an atomic-write path must never leak temp files.
        leftovers = [f for root, _, files in os.walk(self._dir) for f in files
                     if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class TestProjection(unittest.TestCase):

    def test_matches_haversine_near_centre(self):
        lat0, lon0 = _FIX
        for dlat, dlon in ((0.05, 0.0), (0.0, 0.08), (0.04, -0.06)):
            lat, lon = lat0 + dlat, lon0 + dlon
            east, north = artemis.project(lat, lon, lat0, lon0)
            planar = math.hypot(east, north)
            true = _haversine(lat0, lon0, lat, lon)
            # 1% tolerance: the spec's M_PER_DEG_LAT (110574, the equatorial
            # meridian degree) sits ~0.5% under the spherical value at 39°N,
            # and the constant-cos flattening adds a little more. An axis
            # swap or a missing cos() is a >20% error, so this still bites.
            self.assertAlmostEqual(planar, true, delta=true * 0.01)

    def test_axes_point_the_right_way(self):
        east, north = artemis.project(_FIX[0] + 0.01, _FIX[1], *_FIX)
        self.assertGreater(north, 0)
        self.assertAlmostEqual(east, 0)
        east, north = artemis.project(_FIX[0], _FIX[1] - 0.01, *_FIX)
        self.assertLess(east, 0)

    def test_unproject_round_trip(self):
        lat, lon = artemis.unproject(*artemis.project(39.44, -80.21, *_FIX), *_FIX)
        self.assertAlmostEqual(lat, 39.44, places=9)
        self.assertAlmostEqual(lon, -80.21, places=9)

    def test_area_id_format_and_determinism(self):
        self.assertEqual(artemis.area_id(*_FIX), "n39p281_w080p344")
        self.assertEqual(artemis.area_id(-33.8688, 151.2093), "s33p869_e151p209")
        self.assertEqual(artemis.area_id(*_FIX), artemis.area_id(*_FIX))

    def test_bbox_half_width(self):
        lat_min, lon_min, lat_max, lon_max = artemis.bbox(*_FIX)
        self.assertAlmostEqual((lat_max - lat_min) / 2 * artemis.M_PER_DEG_LAT,
                               artemis.HALF_WIDTH_M, delta=1.0)
        self.assertLess(lat_min, _FIX[0])
        self.assertLess(lon_min, _FIX[1])


class TestClipPolyline(unittest.TestCase):

    def test_inside_untouched(self):
        pts = [(0.0, 0.0), (100.0, 50.0), (-200.0, 300.0)]
        self.assertEqual(artemis.clip_polyline(pts, 1000.0), [pts])

    def test_outside_dropped(self):
        pts = [(2000.0, 2000.0), (3000.0, 2500.0)]
        self.assertEqual(artemis.clip_polyline(pts, 1000.0), [])

    def test_crossing_clipped_at_boundary(self):
        runs = artemis.clip_polyline([(0.0, 0.0), (3000.0, 0.0)], 1000.0)
        self.assertEqual(runs, [[(0.0, 0.0), (1000.0, 0.0)]])

    def test_exit_and_reentry_splits(self):
        # Straight across the square, out the east side, back in further north.
        pts = [(0.0, 0.0), (2000.0, 0.0), (2000.0, 500.0), (0.0, 500.0)]
        runs = artemis.clip_polyline(pts, 1000.0)
        self.assertEqual(len(runs), 2)
        for run in runs:
            for x, y in run:
                self.assertLessEqual(abs(x), 1000.0)
                self.assertLessEqual(abs(y), 1000.0)


class TestClipPolygon(unittest.TestCase):

    def test_inside_ring_untouched(self):
        ring = [(-100.0, -100.0), (100.0, -100.0), (100.0, 100.0),
                (-100.0, 100.0)]
        self.assertEqual(artemis.clip_polygon(ring, 1000.0), ring)

    def test_closing_vertex_dropped(self):
        ring = [(-10.0, -10.0), (10.0, -10.0), (10.0, 10.0), (-10.0, -10.0)]
        out = artemis.clip_polygon(ring, 1000.0)
        self.assertEqual(out[0], (-10.0, -10.0))
        self.assertEqual(len(out), 3)

    def test_outside_ring_dropped(self):
        ring = [(5000.0, 5000.0), (6000.0, 5000.0), (6000.0, 6000.0)]
        self.assertEqual(artemis.clip_polygon(ring, 1000.0), [])

    def test_straddling_ring_stays_closed_and_bounded(self):
        # A lake hanging off the east edge must come back fillable, not as
        # loose strokes the way clip_polyline would leave it.
        ring = [(0.0, 0.0), (3000.0, 0.0), (3000.0, 500.0), (0.0, 500.0)]
        out = artemis.clip_polygon(ring, 1000.0)
        self.assertGreaterEqual(len(out), 3)
        for x, y in out:
            self.assertLessEqual(abs(x), 1000.0 + 1e-9)
            self.assertLessEqual(abs(y), 1000.0 + 1e-9)
        # The clipped rectangle keeps its full area on the surviving side.
        self.assertAlmostEqual(min(x for x, _ in out), 0.0)
        self.assertAlmostEqual(max(x for x, _ in out), 1000.0)

    def test_ring_enclosing_the_square_becomes_the_square(self):
        ring = [(-5000.0, -5000.0), (5000.0, -5000.0), (5000.0, 5000.0),
                (-5000.0, 5000.0)]
        out = artemis.clip_polygon(ring, 1000.0)
        self.assertEqual(sorted(out),
                         sorted([(-1000.0, -1000.0), (1000.0, -1000.0),
                                 (1000.0, 1000.0), (-1000.0, 1000.0)]))


class TestVecRoundTrip(_CacheDirBase):

    def _path(self):
        return os.path.join(self._dir, "layer.vec")

    def test_round_trip_preserves_everything(self):
        feats = [
            _feature([(0.0, 0.0), (100.5, -250.0)], name="Old Rte 50", tier=2),
            _feature([(5.0, 5.0), (6.0, 6.5), (7.5, 8.0)], name="", tier=3,
                     flags=artemis.FLAG_DASHED),
            _feature([(-100.0, 40.0), (-90.0, 45.0)], name="Old Rte 50", tier=2),
        ]
        artemis.write_vec(self._path(), feats, _FIX, artemis.LAYER_ROADS)
        back = artemis.read_vec(self._path())
        self.assertEqual(back, feats)

    def test_half_metre_precision(self):
        feats = [_feature([(0.25, 0.74)])]   # rounds to (0.5, 1.5) half-metres
        artemis.write_vec(self._path(), feats, _FIX, artemis.LAYER_ROADS)
        self.assertEqual(artemis.read_vec(self._path())[0]["coords"],
                         [(0.0, 0.5)])

    def test_name_table_dedupes(self):
        feats = [_feature([(0.0, 0.0), (1.0, 1.0)], name="Main St")
                 for _ in range(5)]
        artemis.write_vec(self._path(), feats, _FIX, artemis.LAYER_ROADS)
        with open(self._path(), "rb") as fh:
            data = fh.read()
        (name_count,) = struct.unpack_from("<H", data, len(data) - 2 - 2 - 7)
        # One name entry ("Main St", 7 bytes) despite five features.
        self.assertEqual(name_count, 1)
        self.assertEqual(len(artemis.read_vec(self._path())), 5)

    def test_wrong_layer_raises(self):
        with self.assertRaises(artemis.IngestError):
            artemis.write_vec(self._path(), [_feature([(0.0, 0.0), (1.0, 1.0)])],
                              _FIX, artemis.LAYER_WATER)

    def test_out_of_range_coordinate_raises(self):
        with self.assertRaises(artemis.IngestError):
            artemis.write_vec(self._path(),
                              [_feature([(0.0, 0.0), (20000.0, 0.0)])],
                              _FIX, artemis.LAYER_ROADS)

    def test_missing_file_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            artemis.read_vec(self._path())

    def test_corrupt_file_raises_value_error(self):
        with open(self._path(), "wb") as fh:
            fh.write(b"NOTAVEC" + b"\x00" * 64)
        with self.assertRaises(ValueError):
            artemis.read_vec(self._path())

    def test_disk_full_leaves_no_temp(self):
        # The test_scribe idiom: a failing os.replace must not leak the temp.
        with mock.patch("tools.artemis.os.replace",
                        side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                artemis.write_vec(self._path(),
                                  [_feature([(0.0, 0.0), (1.0, 1.0)])],
                                  _FIX, artemis.LAYER_ROADS)
        self.assertEqual([f for f in os.listdir(self._dir)
                          if f.endswith(".tmp")], [])


class TestManifestAndCommit(_CacheDirBase):

    def test_manifest_round_trip(self):
        area = artemis.area_id(*_FIX)
        os.makedirs(artemis.area_dir(area))
        artemis.write_manifest(artemis.area_dir(area),
                               {"schema": 1, "layers": ["roads"]})
        manifest = artemis.read_manifest(area)
        self.assertEqual(manifest["layers"], ["roads"])

    def test_commit_promotes_and_removes_partial(self):
        area = "n00p000_e000p000"
        os.makedirs(artemis.partial_dir(area))
        with open(os.path.join(artemis.partial_dir(area), "x.bin"), "wb") as fh:
            fh.write(b"payload")
        artemis.commit_area(area)
        self.assertTrue(os.path.isdir(artemis.area_dir(area)))
        self.assertFalse(os.path.exists(artemis.partial_dir(area)))

    def test_commit_replaces_stale_area(self):
        area = "n00p000_e000p000"
        os.makedirs(artemis.area_dir(area))
        with open(os.path.join(artemis.area_dir(area), "old.bin"), "wb") as fh:
            fh.write(b"stale")
        os.makedirs(artemis.partial_dir(area))
        artemis.commit_area(area)
        self.assertEqual(os.listdir(artemis.area_dir(area)), [])

    def test_commit_without_partial_raises(self):
        with self.assertRaises(artemis.IngestError):
            artemis.commit_area("n00p000_e000p000")


class TestSpatialIndex(_CacheDirBase):

    def test_cell_of_corners_and_centre(self):
        half = artemis.HALF_WIDTH_M
        n = artemis.INDEX_CELLS
        self.assertEqual(artemis.cell_of(-half, half), 0)          # NW
        self.assertEqual(artemis.cell_of(half, -half), n * n - 1)  # SE
        self.assertEqual(artemis.cell_of(0.0, 0.0),
                         (n // 2) * n + (n // 2))

    def test_cell_of_clamps_outside_points(self):
        far = artemis.HALF_WIDTH_M * 10
        self.assertEqual(artemis.cell_of(-far, far), 0)
        self.assertEqual(artemis.cell_of(far, -far),
                         artemis.INDEX_CELLS ** 2 - 1)

    def test_neighbour_cells_counts(self):
        self.assertEqual(len(artemis.neighbour_cells(0.0, 0.0)), 9)
        # A corner has only three neighbours plus itself.
        self.assertEqual(
            len(artemis.neighbour_cells(-artemis.HALF_WIDTH_M,
                                        artemis.HALF_WIDTH_M)), 4)

    def test_feature_lands_in_every_cell_it_crosses(self):
        # A road spanning the full width must appear in a whole row of cells.
        half = artemis.HALF_WIDTH_M
        road = _feature([(-half * 0.99, 0.0), (half * 0.99, 0.0)])
        index = artemis.build_index({artemis.LAYER_ROADS: [road]})
        occupied = [i for i, bucket in enumerate(index) if bucket]
        self.assertGreaterEqual(len(occupied), artemis.INDEX_CELLS - 2)
        rows = {i // artemis.INDEX_CELLS for i in occupied}
        self.assertEqual(len(rows), 1)          # a due-east line stays in one row

    def test_token_packs_layer_and_index(self):
        index = artemis.build_index({
            artemis.LAYER_ROADS: [_feature([(0.0, 0.0), (10.0, 10.0)])],
            artemis.LAYER_WATER: [_feature([(0.0, 0.0), (10.0, 10.0)],
                                           kind="water_line")],
        })
        tokens = index[artemis.cell_of(5.0, 5.0)]
        self.assertEqual(sorted(t >> 24 for t in tokens),
                         [artemis.LAYER_ROADS, artemis.LAYER_WATER])
        self.assertEqual({t & 0xFFFFFF for t in tokens}, {0})

    def test_write_read_round_trip(self):
        index = artemis.build_index({
            artemis.LAYER_ROADS: [_feature([(0.0, 0.0), (2000.0, 2000.0)]),
                                  _feature([(-500.0, 100.0), (500.0, 100.0)])],
        })
        path = os.path.join(self._dir, "spatial.idx")
        artemis.write_index(path, index)
        self.assertEqual(artemis.read_index(path), index)

    def test_corrupt_index_raises(self):
        path = os.path.join(self._dir, "spatial.idx")
        with open(path, "wb") as fh:
            fh.write(b"XXXX" + b"\x00" * 64)
        with self.assertRaises(ValueError) as ctx:
            artemis.read_index(path)
        self.assertIn("bad_index", str(ctx.exception))

    def test_ensure_index_rebuilds_after_deletion(self):
        # derived/ must stay safely deletable.
        area = artemis.area_id(*_FIX)
        os.makedirs(artemis.area_dir(area))
        layers = {artemis.LAYER_ROADS: [_feature([(0.0, 0.0), (100.0, 100.0)])]}
        first = artemis.ensure_index(area, layers)
        self.assertTrue(os.path.exists(
            artemis.index_path(artemis.area_dir(area))))
        os.remove(artemis.index_path(artemis.area_dir(area)))
        self.assertEqual(artemis.ensure_index(area, layers), first)


class TestHitTest(unittest.TestCase):

    def test_point_segment_distance(self):
        # Perpendicular, past-the-end, and degenerate cases.
        self.assertAlmostEqual(
            artemis.point_segment_distance(0, 5, -10, 0, 10, 0), 5.0)
        self.assertAlmostEqual(
            artemis.point_segment_distance(20, 0, -10, 0, 10, 0), 10.0)
        self.assertAlmostEqual(
            artemis.point_segment_distance(3, 4, 0, 0, 0, 0), 5.0)

    def test_point_in_ring(self):
        ring = [(-10.0, -10.0), (10.0, -10.0), (10.0, 10.0), (-10.0, 10.0)]
        self.assertTrue(artemis.point_in_ring(0.0, 0.0, ring))
        self.assertFalse(artemis.point_in_ring(50.0, 0.0, ring))

    def test_feature_distance_inside_a_lake_is_zero(self):
        lake = _feature([(-10.0, -10.0), (10.0, -10.0), (10.0, 10.0),
                         (-10.0, 10.0)], kind="water_area")
        self.assertEqual(artemis.feature_distance(lake, 0.0, 0.0), 0.0)
        self.assertAlmostEqual(artemis.feature_distance(lake, 20.0, 0.0), 10.0)

    def test_query_finds_the_nearest_within_tolerance(self):
        near = _feature([(0.0, 100.0), (1000.0, 100.0)], name="Near Rd")
        far = _feature([(0.0, 900.0), (1000.0, 900.0)], name="Far Rd")
        layers = {artemis.LAYER_ROADS: [far, near]}
        index = artemis.build_index(layers)
        hit = artemis.query(index, layers, 500.0, 0.0, 200.0)
        self.assertIsNotNone(hit)
        layer_id, fi, dist = hit
        self.assertEqual(layer_id, artemis.LAYER_ROADS)
        self.assertEqual(layers[layer_id][fi]["name"], "Near Rd")
        self.assertAlmostEqual(dist, 100.0)

    def test_query_respects_tolerance(self):
        layers = {artemis.LAYER_ROADS: [_feature([(0.0, 500.0), (1000.0, 500.0)])]}
        index = artemis.build_index(layers)
        self.assertIsNone(artemis.query(index, layers, 500.0, 0.0, 100.0))

    def test_roads_beat_water_even_when_water_is_nearer(self):
        # A bridge should read as the road you would drive across.
        road = _feature([(0.0, 50.0), (1000.0, 50.0)], name="Bridge St")
        creek = _feature([(0.0, 10.0), (1000.0, 10.0)], kind="water_line",
                         name="Ann Run")
        layers = {artemis.LAYER_ROADS: [road], artemis.LAYER_WATER: [creek]}
        index = artemis.build_index(layers)
        hit = artemis.query(index, layers, 500.0, 0.0, 200.0)
        self.assertEqual(hit[0], artemis.LAYER_ROADS)
        # Reversing the priority order picks the creek instead.
        hit = artemis.query(index, layers, 500.0, 0.0, 200.0,
                            order=(artemis.LAYER_WATER, artemis.LAYER_ROADS))
        self.assertEqual(hit[0], artemis.LAYER_WATER)

    def test_stale_index_entries_are_ignored(self):
        # An index naming features a shorter .vec no longer has must degrade,
        # not IndexError.
        layers = {artemis.LAYER_ROADS: [_feature([(0.0, 0.0), (10.0, 0.0)])]}
        index = artemis.build_index(
            {artemis.LAYER_ROADS: [layers[artemis.LAYER_ROADS][0]] * 5})
        self.assertIsNotNone(artemis.query(index, layers, 5.0, 0.0, 50.0))


class TestPresentationHelpers(unittest.TestCase):

    def test_bearing_of(self):
        self.assertEqual(artemis.bearing_of(0.0, 100.0), 0)      # north
        self.assertEqual(artemis.bearing_of(100.0, 0.0), 90)     # east
        self.assertEqual(artemis.bearing_of(0.0, -100.0), 180)   # south
        self.assertEqual(artemis.bearing_of(-100.0, 0.0), 270)   # west
        self.assertEqual(artemis.bearing_of(100.0, 100.0), 45)

    def test_nice_scale_miles_picks_a_tidy_fit(self):
        self.assertEqual(artemis.nice_scale_miles(4.0), 3.0)
        self.assertEqual(artemis.nice_scale_miles(1.2), 1.0)
        self.assertEqual(artemis.nice_scale_miles(0.3), 0.25)
        # Never returns more than the budget, and never zero.
        self.assertEqual(artemis.nice_scale_miles(0.01), 0.1)

    def test_zoom_stops_only_magnify(self):
        # Zooming out past the cached square is meaningless — the cache is
        # the edge of the world, not a viewport into more data.
        self.assertEqual(min(artemis.ZOOM_STOPS), 1)


def _reference_zip():
    """The national boundary download, faked from the captured-real fixture."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for ext in (".shp", ".dbf"):
            with open(os.path.join(_FIXTURES, "counties_mini" + ext), "rb") as fh:
                zf.writestr("cb_2025_us_county_500k" + ext, fh.read())
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestReference(_CacheDirBase):

    def test_ensure_reference_then_counties_for_bbox(self):
        with mock.patch("tools.artemis.urllib.request.urlopen",
                        return_value=_FakeResponse(_reference_zip())) as opened:
            artemis.ensure_reference(threading.Event(), lambda msg: None)
            artemis.ensure_reference(threading.Event(), lambda msg: None)
        opened.assert_called_once()          # idempotent: second call is a no-op

        fixture_geoids = {"54001", "54017", "54021", "54033", "54041", "54049",
                          "54051", "54061", "54083", "54091", "54095", "54097",
                          "54103", "42051", "42059"}

        # The default fix sits mid-county, so its 11-mile box finds exactly one.
        geoids = artemis.counties_for_bbox(artemis.bbox(*_FIX))
        self.assertEqual(geoids, ["54033"])       # Harrison County itself

        # The multi-county merge is the case worth proving, so ask it directly
        # with a fix near the Harrison/Marion line rather than hoping the
        # default happens to straddle one.
        geoids = artemis.counties_for_bbox(artemis.bbox(*_EDGE_FIX))
        self.assertIn("54033", geoids)
        self.assertTrue(set(geoids) <= fixture_geoids)
        self.assertGreaterEqual(len(geoids), 2)   # neighbours join via buffer

    def test_download_failure_is_slugged(self):
        with mock.patch("tools.artemis.urllib.request.urlopen",
                        side_effect=OSError("no route")):
            with self.assertRaises(artemis.IngestError) as ctx:
                artemis.ensure_reference(threading.Event(), lambda msg: None)
        self.assertIn("download_failed", str(ctx.exception))

    def test_missing_reference_raises(self):
        with self.assertRaises(artemis.IngestError) as ctx:
            artemis.counties_for_bbox(artemis.bbox(*_FIX))
        self.assertIn("reference_missing", str(ctx.exception))


def _write_roads(area, center, box, cancel, progress):
    artemis.write_vec(os.path.join(artemis.partial_dir(area), "roads.vec"),
                      [_feature([(0.0, 0.0), (10.0, 10.0)])], center,
                      artemis.LAYER_ROADS)


def _write_water(area, center, box, cancel, progress):
    artemis.write_vec(os.path.join(artemis.partial_dir(area), "water.vec"),
                      [_feature([(0.0, 0.0), (5.0, 5.0)], kind="water_line")],
                      center, artemis.LAYER_WATER)


def _write_terrain(area, center, box, cancel, progress):
    return {"cols": 1024, "rows": 1024, "vdatum": "NAVD88",
            "elev_min_m": 178, "elev_max_m": 341}


class TestIngestOrchestrator(_CacheDirBase):

    def _stub(self, roads=_write_roads, water=_write_water,
              terrain=_write_terrain):
        """Patch the reference and all three layer seams around ingest_area."""
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch("tools.artemis.ensure_reference"))
        stack.enter_context(mock.patch("tools.artemis.counties_for_bbox",
                                       return_value=["54033"]))
        stack.enter_context(mock.patch("tools.hecate.ingest", side_effect=roads))
        stack.enter_context(mock.patch("tools.poseidon.ingest", side_effect=water))
        stack.enter_context(mock.patch("tools.demeter.ingest", side_effect=terrain))
        return stack

    def test_success_commits_all_three_layers(self):
        with self._stub():
            area = artemis.ingest_area(*_FIX, threading.Event(), lambda m: None)
        manifest = artemis.read_manifest(area)
        self.assertEqual(manifest["layers"], ["roads", "water", "terrain"])
        self.assertEqual(manifest["schema"], artemis.MANIFEST_SCHEMA)
        self.assertEqual(manifest["sources"]["roads"], "TIGER2025/54033")
        self.assertEqual(manifest["sources"]["water"], "TIGER2025/54033")
        self.assertEqual(manifest["sources"]["elevation"],
                         "OpenTopography/USGS10m")
        self.assertEqual(manifest["grid"]["vdatum"], "NAVD88")
        self.assertNotIn("terrain_skipped", manifest)
        self.assertFalse(os.path.exists(artemis.partial_dir(area)))

    def test_terrain_failure_still_commits_roads_and_water(self):
        # The whole point of best-effort terrain: an absent key must not cost
        # the user the roads and water that already downloaded.
        with self._stub(terrain=artemis.IngestError("vault_locked")):
            area = artemis.ingest_area(*_FIX, threading.Event(), lambda m: None)
        manifest = artemis.read_manifest(area)
        self.assertEqual(manifest["layers"], ["roads", "water"])
        self.assertEqual(manifest["terrain_skipped"], "vault_locked")
        self.assertNotIn("elevation", manifest["sources"])
        self.assertTrue(os.path.exists(
            os.path.join(artemis.area_dir(area), "roads.vec")))

    def test_a_refresh_keeps_the_terrain_it_already_had(self):
        """The scar: a re-ingest replaces the whole directory, so a refresh
        with a sealed vault used to downgrade a complete area to a
        terrain-less one — paying for a road update with elevation you
        already owned."""
        with self._stub():
            area = artemis.ingest_area(*_FIX, threading.Event(), lambda m: None)
        # Give the committed area a real terrain.bin, as a good ingest would.
        with open(os.path.join(artemis.area_dir(area), artemis.TERRAIN_BIN),
                  "wb") as fh:
            fh.write(b"ARTDEM-stand-in")
        manifest = artemis.read_manifest(area)
        manifest["grid"] = {"vdatum": "NAVD88", "cols": 1024}
        manifest["sources"]["elevation"] = "OpenTopography/USGS10m"
        artemis.write_manifest(artemis.area_dir(area), manifest)

        with self._stub(terrain=artemis.IngestError("vault_locked")):
            artemis.ingest_area(*_FIX, threading.Event(), lambda m: None)

        after = artemis.read_manifest(area)
        self.assertIn("terrain", after["layers"])
        self.assertEqual(after["terrain_kept"], "vault_locked")
        self.assertNotIn("terrain_skipped", after)
        self.assertEqual(after["grid"]["vdatum"], "NAVD88")
        self.assertEqual(after["sources"]["elevation"],
                         "OpenTopography/USGS10m")
        with open(os.path.join(artemis.area_dir(area), artemis.TERRAIN_BIN),
                  "rb") as fh:
            self.assertEqual(fh.read(), b"ARTDEM-stand-in")

    def test_carrying_terrain_does_not_clobber_the_new_spatial_index(self):
        # derived/ holds the index for the NEW roads and water, so only
        # terrain.bin may be carried forward — never the whole directory.
        with self._stub():
            area = artemis.ingest_area(*_FIX, threading.Event(), lambda m: None)
        with open(os.path.join(artemis.area_dir(area), artemis.TERRAIN_BIN),
                  "wb") as fh:
            fh.write(b"ARTDEM-stand-in")
        manifest = artemis.read_manifest(area)
        manifest["layers"].append("terrain")
        artemis.write_manifest(artemis.area_dir(area), manifest)
        stale = artemis.index_path(artemis.area_dir(area))
        with open(stale, "wb") as fh:
            fh.write(b"STALE-INDEX")

        with self._stub(terrain=artemis.IngestError("no_key")):
            artemis.ingest_area(*_FIX, threading.Event(), lambda m: None)

        with open(artemis.index_path(artemis.area_dir(area)), "rb") as fh:
            self.assertNotEqual(fh.read(), b"STALE-INDEX")

    def test_a_first_ingest_without_terrain_still_reports_it_skipped(self):
        # Nothing to carry forward, so the honest answer is still "skipped".
        with self._stub(terrain=artemis.IngestError("no_key")):
            area = artemis.ingest_area(*_FIX, threading.Event(), lambda m: None)
        manifest = artemis.read_manifest(area)
        self.assertEqual(manifest["terrain_skipped"], "no_key")
        self.assertNotIn("terrain_kept", manifest)
        self.assertNotIn("terrain", manifest["layers"])

    def test_terrain_download_failure_records_bare_slug(self):
        with self._stub(terrain=artemis.IngestError("download_failed: URLError")):
            area = artemis.ingest_area(*_FIX, threading.Event(), lambda m: None)
        self.assertEqual(artemis.read_manifest(area)["terrain_skipped"],
                         "download_failed")

    def test_roads_failure_discards_partial(self):
        with self._stub(roads=artemis.IngestError("download_failed: URLError")):
            with self.assertRaises(artemis.IngestError):
                artemis.ingest_area(*_FIX, threading.Event(), lambda m: None)
        self.assertEqual([d for d in os.listdir(self._dir)
                          if d.endswith(".partial")], [])

    def test_water_failure_discards_partial(self):
        with self._stub(water=artemis.IngestError("bad_county: KeyError")):
            with self.assertRaises(artemis.IngestError):
                artemis.ingest_area(*_FIX, threading.Event(), lambda m: None)
        self.assertEqual([d for d in os.listdir(self._dir)
                          if d.endswith(".partial")], [])

    def test_cancel_discards_partial(self):
        cancel = threading.Event()

        def cancelling_roads(area, center, box, cxl, progress):
            _write_roads(area, center, box, cxl, progress)
            cancel.set()

        with self._stub(roads=cancelling_roads):
            with self.assertRaises(artemis.IngestError) as ctx:
                artemis.ingest_area(*_FIX, cancel, lambda m: None)
        self.assertIn("cancelled", str(ctx.exception))
        self.assertEqual([d for d in os.listdir(self._dir)
                          if d.endswith(".partial")], [])

    def test_cancel_during_terrain_is_not_swallowed(self):
        # Best-effort terrain must not turn a cancellation into a committed
        # area — only genuine layer failures are absorbed.
        cancel = threading.Event()

        def cancelling_terrain(area, center, box, cxl, progress):
            cancel.set()
            raise artemis.IngestError("cancelled")

        with self._stub(terrain=cancelling_terrain):
            with self.assertRaises(artemis.IngestError) as ctx:
                artemis.ingest_area(*_FIX, cancel, lambda m: None)
        self.assertIn("cancelled", str(ctx.exception))
        self.assertEqual(os.listdir(self._dir), [])


class TestLabels(_CacheDirBase):

    def _area(self, **extra):
        area = artemis.area_id(*_FIX)
        os.makedirs(artemis.area_dir(area), exist_ok=True)
        payload = {"center": list(_FIX), "layers": ["roads", "water"],
                   "sources": {"roads": "TIGER2025/54033"},
                   "fetched": "2026-08-10T00:00:00Z",
                   "schema": artemis.MANIFEST_SCHEMA}
        payload.update(extra)
        artemis.write_manifest(artemis.area_dir(area), payload)
        return area

    def test_set_label_round_trips_and_keeps_the_rest_of_the_manifest(self):
        area = self._area()
        self.assertEqual(artemis.set_label(area, "Deer Camp"), "Deer Camp")
        manifest = artemis.read_manifest(area)
        self.assertEqual(manifest["label"], "Deer Camp")
        self.assertEqual(manifest["layers"], ["roads", "water"])
        self.assertEqual(manifest["sources"], {"roads": "TIGER2025/54033"})
        self.assertEqual(manifest["fetched"], "2026-08-10T00:00:00Z")
        self.assertEqual(manifest["schema"], artemis.MANIFEST_SCHEMA)

    def test_empty_label_clears_it_and_label_of_falls_back_to_coordinates(self):
        area = self._area(label="Deer Camp")
        self.assertEqual(artemis.set_label(area, "   "), "")
        manifest = artemis.read_manifest(area)
        self.assertNotIn("label", manifest)
        self.assertEqual(artemis.label_of(manifest), "39.281 -80.344")

    def test_label_is_whitespace_collapsed_and_capped(self):
        area = self._area()
        stored = artemis.set_label(area, "  the   elk    site  ")
        self.assertEqual(stored, "the elk site")
        stored = artemis.set_label(area, "x" * 100)
        self.assertEqual(len(stored), 24)

    def test_set_label_on_an_uncached_area_raises(self):
        with self.assertRaises(FileNotFoundError):
            artemis.set_label("n00p000_e000p000", "nowhere")

    def test_label_of_prefers_the_label(self):
        self.assertEqual(
            artemis.label_of({"label": "Deer Camp", "center": [1.0, 2.0]}),
            "Deer Camp")
        self.assertEqual(artemis.label_of({"area_id": "abc"}), "abc")

    def test_reingest_preserves_a_custom_label(self):
        # A refresh redraws the map under the name you gave it.
        with self._stub():
            area = artemis.ingest_area(*_FIX, threading.Event(), lambda m: None)
        artemis.set_label(area, "Deer Camp")
        with self._stub():
            artemis.ingest_area(*_FIX, threading.Event(), lambda m: None)
        self.assertEqual(artemis.read_manifest(area)["label"], "Deer Camp")

    def _stub(self):
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch("tools.artemis.ensure_reference"))
        stack.enter_context(mock.patch("tools.artemis.counties_for_bbox",
                                       return_value=["54033"]))
        stack.enter_context(mock.patch("tools.hecate.ingest",
                                       side_effect=_write_roads))
        stack.enter_context(mock.patch("tools.poseidon.ingest",
                                       side_effect=_write_water))
        stack.enter_context(mock.patch("tools.demeter.ingest",
                                       side_effect=_write_terrain))
        return stack

    def test_nearest_cached_carries_the_label_and_limit_none_returns_all(self):
        area = self._area(label="Deer Camp")
        rows = artemis.nearest_cached(*_FIX, limit=None)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["label"], "Deer Camp")
        self.assertEqual(rows[0]["area_id"], area)
        self.assertEqual(rows[0]["fetched"], "2026-08-10T00:00:00Z")


class TestDeleteArea(_CacheDirBase):

    def _area(self):
        area = artemis.area_id(*_FIX)
        os.makedirs(artemis.area_dir(area), exist_ok=True)
        artemis.write_manifest(artemis.area_dir(area), {"center": list(_FIX)})
        return area

    def test_delete_area_recycles_the_directory(self):
        area = self._area()
        with mock.patch("tools.artemis._send_to_recycle_bin",
                        return_value=True) as recycler:
            self.assertTrue(artemis.delete_area(area))
        recycler.assert_called_once_with(artemis.area_dir(area))

    def test_delete_area_refuses_anything_that_is_not_a_bare_cached_area(self):
        """The guard's teeth: this is a recursive delete driven by a string."""
        self._area()
        for bad in ("", "..", "a/b", "a\\b", "..\\..\\Windows",
                    os.path.abspath(self._dir), "_reference", "x.partial",
                    "never_cached"):
            with self.subTest(area=bad):
                with mock.patch("tools.artemis._send_to_recycle_bin") as rec:
                    self.assertFalse(artemis.delete_area(bad))
                    rec.assert_not_called()

    def test_delete_area_reports_a_failed_recycle(self):
        area = self._area()
        with mock.patch("tools.artemis._send_to_recycle_bin",
                        return_value=False):
            self.assertFalse(artemis.delete_area(area))

    def test_non_windows_branch_removes_the_tree(self):
        area = self._area()
        with mock.patch("tools.artemis.sys.platform", "linux"):
            self.assertTrue(artemis._send_to_recycle_bin(artemis.area_dir(area)))
        self.assertFalse(os.path.exists(artemis.area_dir(area)))


class TestFetch(_CacheDirBase):

    def setUp(self):
        super().setUp()
        self._themis_patches = [
            mock.patch.object(artemis.themis, "latitude", return_value=_FIX[0]),
            mock.patch.object(artemis.themis, "longitude", return_value=_FIX[1]),
        ]
        for p in self._themis_patches:
            p.start()

    def tearDown(self):
        for p in self._themis_patches:
            p.stop()
        super().tearDown()

    def test_uncached_state(self):
        status = artemis.fetch()
        self.assertEqual(status["area_id"], "n39p281_w080p344")
        self.assertFalse(status["cached"])
        self.assertIsNone(status["ingested_at"])
        self.assertEqual(status["layers"], [])

    def test_cached_state(self):
        area = artemis.area_id(*_FIX)
        os.makedirs(artemis.area_dir(area))
        artemis.write_manifest(artemis.area_dir(area),
                               {"fetched": "2026-08-09T00:00:00Z",
                                "layers": ["roads"]})
        status = artemis.fetch()
        self.assertTrue(status["cached"])
        self.assertEqual(status["ingested_at"], "2026-08-09T00:00:00Z")
        self.assertEqual(status["layers"], ["roads"])

    def test_corrupt_manifest_never_raises(self):
        area = artemis.area_id(*_FIX)
        os.makedirs(artemis.area_dir(area))
        with open(os.path.join(artemis.area_dir(area), "manifest.json"),
                  "w", encoding="utf-8") as fh:
            fh.write("{not json")
        status = artemis.fetch()          # degrades to "not cached", no raise
        self.assertFalse(status["cached"])


if __name__ == "__main__":
    unittest.main()
