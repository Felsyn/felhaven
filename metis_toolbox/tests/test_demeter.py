"""
test_demeter.py — the elevation layer: AAIGrid parsing, the separable
nearest-neighbour resample and its orientation, terrain.bin round-trip,
tercile banding with the NODATA and low-relief guards, the PPM raster and
its regeneration from terrain.bin, and the Vault slugs. No network — the
OpenTopography call is mocked at the urllib seam and the key at the
Cerberus seam.

    python -X utf8 -m unittest tests.test_demeter
"""

import array
import gzip
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cerberus
from tools import artemis
from tools import demeter

_FIX = (39.2806, -80.3440)


def _grid_text(center=_FIX, n=20, nodata=-9999.0, value=None):
    """A synthetic AAIGrid covering the ingest square of `center`.

    Default values encode the ROW index, so a resample that flips north for
    south shows up immediately.
    """
    lat_min, lon_min, lat_max, lon_max = artemis.bbox(*center)
    cellsize = (lon_max - lon_min) / n
    # Pad a little so the square is fully covered in both axes.
    rows = []
    for r in range(n):
        rows.append(" ".join(
            str(value(r, c) if value else r * 10) for c in range(n)))
    header = (f"ncols {n}\nnrows {n}\n"
              f"xllcorner {lon_min}\nyllcorner {lat_max - n * cellsize}\n"
              f"cellsize {cellsize}\nNODATA_value {nodata}\n")
    return header + "\n".join(rows) + "\n"


class TestParseAaiGrid(unittest.TestCase):

    def test_parses_header_and_body(self):
        grid = demeter.parse_aaigrid(_grid_text(n=4))
        self.assertEqual((grid.ncols, grid.nrows), (4, 4))
        self.assertEqual(len(grid.values), 16)
        self.assertEqual(grid.values[0], 0)      # first row
        self.assertEqual(grid.values[-1], 30)    # last row

    def test_floats_round_to_metres(self):
        grid = demeter.parse_aaigrid(
            _grid_text(n=2, value=lambda r, c: 412.6 if r == 0 else 100.4))
        self.assertEqual(grid.values[0], 413)
        self.assertEqual(grid.values[-1], 100)

    def test_nodata_maps_to_sentinel(self):
        grid = demeter.parse_aaigrid(
            _grid_text(n=2, value=lambda r, c: -9999 if r == 0 else 50))
        self.assertEqual(grid.values[0], demeter.NODATA)
        self.assertEqual(grid.values[-1], 50)

    def test_malformed_raises(self):
        with self.assertRaises(ValueError):
            demeter.parse_aaigrid("not a grid at all")
        with self.assertRaises(ValueError):
            demeter.parse_aaigrid("ncols 4\nnrows 4\nxllcorner 0\n"
                                  "yllcorner 0\ncellsize 1\nNODATA_value -9999\n1 2")
        with self.assertRaises(ValueError) as ctx:
            demeter.parse_aaigrid("ncols x\nnrows 4\nxllcorner 0\n"
                                  "yllcorner 0\ncellsize 1\nNODATA_value -9999\n")
        self.assertIn("bad_grid", str(ctx.exception))

    def test_api_error_body_is_a_parse_failure(self):
        # OpenTopography reports quota/auth problems as a short text body.
        with self.assertRaises(ValueError):
            demeter.parse_aaigrid("Invalid API Key")


class TestResample(unittest.TestCase):

    def test_size_and_orientation(self):
        grid = demeter.parse_aaigrid(_grid_text(n=20))
        cells = demeter.resample(grid, _FIX, size=8)
        self.assertEqual(len(cells), 64)
        north_row = cells[0:8]
        south_row = cells[56:64]
        # Values encode the source row, which runs north to south.
        self.assertLess(max(north_row), min(south_row))

    def test_nodata_survives_resampling(self):
        grid = demeter.parse_aaigrid(
            _grid_text(n=8, value=lambda r, c: -9999 if r < 4 else 200))
        cells = demeter.resample(grid, _FIX, size=8)
        self.assertEqual(cells[0], demeter.NODATA)      # north half missing
        self.assertEqual(cells[-1], 200)

    def test_cells_outside_source_are_nodata(self):
        # A source grid covering a far-away place leaves every cell unset.
        grid = demeter.parse_aaigrid(_grid_text(center=(10.0, 10.0), n=8))
        cells = demeter.resample(grid, _FIX, size=8)
        self.assertEqual(set(cells), {demeter.NODATA})


class _CacheBase(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._orig_cache = artemis.CACHE_DIR
        artemis.CACHE_DIR = self._dir
        self.area = artemis.area_id(*_FIX)

    def tearDown(self):
        artemis.CACHE_DIR = self._orig_cache
        shutil.rmtree(self._dir, ignore_errors=True)
        leftovers = [f for _, _, files in os.walk(self._dir) for f in files
                     if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class TestTerrainBin(_CacheBase):

    def _cells(self, size=8):
        return array.array("h", [100 + i for i in range(size * size)])

    def test_round_trip(self):
        path = os.path.join(self._dir, demeter._BIN_NAME)
        cells = self._cells()
        info = demeter.write_bin(path, cells, _FIX, size=8)
        self.assertEqual(info["cols"], 8)
        self.assertEqual(info["elev_min_m"], 100)
        self.assertEqual(info["elev_max_m"], 163)
        self.assertEqual(info["vdatum"], "NAVD88")

        back_info, back_cells = demeter.read_bin(path)
        self.assertEqual(list(back_cells), list(cells))
        self.assertEqual(back_info["vdatum"], "NAVD88")
        self.assertAlmostEqual(back_info["center"][0], _FIX[0], places=9)
        self.assertEqual(back_info["nodata"], demeter.NODATA)

    def test_header_is_64_bytes(self):
        self.assertEqual(demeter._HEADER.size, 64)

    def test_all_nodata_raises(self):
        path = os.path.join(self._dir, demeter._BIN_NAME)
        cells = array.array("h", [demeter.NODATA] * 16)
        with self.assertRaises(artemis.IngestError) as ctx:
            demeter.write_bin(path, cells, _FIX, size=4)
        self.assertIn("no_elevation", str(ctx.exception))

    def test_corrupt_raises(self):
        path = os.path.join(self._dir, demeter._BIN_NAME)
        with open(path, "wb") as fh:
            fh.write(b"NOPE" + b"\x00" * 200)
        with self.assertRaises(ValueError) as ctx:
            demeter.read_bin(path)
        self.assertIn("bad_terrain", str(ctx.exception))

    def test_missing_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            demeter.read_bin(os.path.join(self._dir, "absent.bin"))

    def test_low_relief_flagged_in_info(self):
        path = os.path.join(self._dir, demeter._BIN_NAME)
        cells = array.array("h", [200, 201, 202, 203])
        info = demeter.write_bin(path, cells, _FIX, size=2)
        self.assertTrue(info["low_relief"])


class TestBanding(unittest.TestCase):

    def test_terciles_ignore_nodata(self):
        # Without the guard the NODATA sentinel would drag both cuts down.
        cells = array.array("h", list(range(300, 600)) + [demeter.NODATA] * 100)
        low, high = demeter.band_thresholds(cells)
        self.assertGreater(low, 300)
        self.assertGreater(high, low)
        self.assertLess(high, 600)

    def test_low_relief_returns_none(self):
        cells = array.array("h", [400, 402, 405, 408])
        self.assertIsNone(demeter.band_thresholds(cells))

    def test_all_nodata_returns_none(self):
        cells = array.array("h", [demeter.NODATA] * 10)
        self.assertIsNone(demeter.band_thresholds(cells))

    def test_thresholds_split_roughly_evenly(self):
        cells = array.array("h", list(range(0, 900)))
        low, high = demeter.band_thresholds(cells)
        self.assertAlmostEqual(low, 300, delta=5)
        self.assertAlmostEqual(high, 600, delta=5)


class TestRaster(_CacheBase):

    def _banded_cells(self, size=6):
        # A clean north-to-south ramp with one NODATA cell.
        cells = array.array("h", [r * 100 for r in range(size)
                                  for _ in range(size)])
        cells[0] = demeter.NODATA
        return cells

    def test_ppm_shape_and_colours(self):
        size = 6
        ppm = demeter.render_ppm(self._banded_cells(size), size=size)
        self.assertTrue(ppm.startswith(b"P6\n6 6\n255\n"))
        body = ppm.split(b"255\n", 1)[1]
        self.assertEqual(len(body), size * size * 3)
        self.assertEqual(body[0:3], bytes(demeter.RGB_NODATA))   # missing cell
        self.assertEqual(body[-3:], bytes(demeter.RGB_PEAK))     # southern ridge

    def test_low_relief_renders_one_band(self):
        cells = array.array("h", [400, 401, 402, 403])
        body = demeter.render_ppm(cells, size=2).split(b"255\n", 1)[1]
        self.assertEqual(set(body[i:i + 3] for i in range(0, len(body), 3)),
                         {bytes(demeter.RGB_MID)})

    def test_write_derived_then_raster_reads_it(self):
        size = 6
        cells = self._banded_cells(size)
        area_dir = artemis.area_dir(self.area)
        os.makedirs(area_dir)
        demeter.write_bin(os.path.join(area_dir, demeter._BIN_NAME),
                          cells, _FIX, size=size)
        demeter.write_derived(area_dir, cells, size=size)
        ppm_path = os.path.join(area_dir, demeter._DERIVED_DIR,
                                demeter._PPM_NAME)
        self.assertTrue(os.path.exists(ppm_path))
        with open(ppm_path, "rb") as fh:
            self.assertEqual(gzip.decompress(fh.read()),
                             demeter.render_ppm(cells, size=size))
        self.assertEqual(demeter.raster(self.area),
                         demeter.render_ppm(cells, size=size))

    def test_derived_is_regenerated_after_deletion(self):
        # `rm -rf derived/` is a supported operation — the invariant that
        # nothing under derived/ holds information terrain.bin lacks.
        size = 6
        cells = self._banded_cells(size)
        area_dir = artemis.area_dir(self.area)
        os.makedirs(area_dir)
        demeter.write_bin(os.path.join(area_dir, demeter._BIN_NAME),
                          cells, _FIX, size=size)
        demeter.write_derived(area_dir, cells, size=size)
        shutil.rmtree(os.path.join(area_dir, demeter._DERIVED_DIR))

        self.assertEqual(demeter.raster(self.area),
                         demeter.render_ppm(cells, size=size))
        self.assertTrue(os.path.exists(os.path.join(
            area_dir, demeter._DERIVED_DIR, demeter._PPM_NAME)))

    def test_raster_without_terrain_raises(self):
        with self.assertRaises(FileNotFoundError):
            demeter.raster(self.area)

    def test_elevation_at(self):
        size = 8
        cells = array.array("h", [r * 10 for r in range(size)
                                  for _ in range(size)])
        area_dir = artemis.area_dir(self.area)
        os.makedirs(area_dir)
        demeter.write_bin(os.path.join(area_dir, demeter._BIN_NAME),
                          cells, _FIX, size=size)
        north = demeter.elevation_at(self.area, 0.0,
                                     artemis.HALF_WIDTH_M * 0.9)
        south = demeter.elevation_at(self.area, 0.0,
                                     -artemis.HALF_WIDTH_M * 0.9)
        self.assertLess(north, south)
        self.assertIsNone(demeter.elevation_at(self.area, 1e7, 0.0))


class TestIngest(_CacheBase):

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def test_sealed_vault_is_slugged_before_any_network(self):
        with mock.patch.object(cerberus, "is_unlocked", return_value=False), \
             mock.patch("tools.demeter.urllib.request.urlopen",
                        side_effect=AssertionError("fetched with a sealed vault")):
            with self.assertRaises(artemis.IngestError) as ctx:
                demeter.ingest(self.area, _FIX, artemis.bbox(*_FIX),
                               threading.Event(), lambda m: None)
        self.assertEqual(str(ctx.exception), demeter.ERR_VAULT_LOCKED)

    def test_missing_key_is_slugged(self):
        with mock.patch.object(cerberus, "is_unlocked", return_value=True), \
             mock.patch.object(cerberus, "vault_get",
                               side_effect=cerberus.VaultError("absent")):
            with self.assertRaises(artemis.IngestError) as ctx:
                demeter.ingest(self.area, _FIX, artemis.bbox(*_FIX),
                               threading.Event(), lambda m: None)
        self.assertEqual(str(ctx.exception), demeter.ERR_NO_KEY)

    def _unlocked(self):
        return (mock.patch.object(cerberus, "is_unlocked", return_value=True),
                mock.patch.object(cerberus, "vault_get", return_value="KEY"))

    def test_successful_ingest_writes_bin_and_derived(self):
        os.makedirs(artemis.partial_dir(self.area))
        p1, p2 = self._unlocked()
        with p1, p2, mock.patch(
                "tools.demeter.urllib.request.urlopen",
                return_value=self._Resp(_grid_text(n=32).encode())) as opened, \
             mock.patch.object(demeter, "GRID", 16):
            info = demeter.ingest(self.area, _FIX, artemis.bbox(*_FIX),
                                  threading.Event(), lambda m: None)
        # The key travels in the query string, not a header.
        self.assertIn("API_Key=KEY", opened.call_args[0][0])
        self.assertIn("datasetName=USGS10m", opened.call_args[0][0])
        self.assertEqual(info["vdatum"], "NAVD88")
        partial = artemis.partial_dir(self.area)
        self.assertTrue(os.path.exists(os.path.join(partial, demeter._BIN_NAME)))
        self.assertTrue(os.path.exists(os.path.join(
            partial, demeter._DERIVED_DIR, demeter._PPM_NAME)))

    def test_download_failure_is_slugged(self):
        p1, p2 = self._unlocked()
        with p1, p2, mock.patch("tools.demeter.urllib.request.urlopen",
                                side_effect=OSError("no route")):
            with self.assertRaises(artemis.IngestError) as ctx:
                demeter.ingest(self.area, _FIX, artemis.bbox(*_FIX),
                               threading.Event(), lambda m: None)
        self.assertIn("download_failed", str(ctx.exception))

    def test_cancel_after_download(self):
        cancel = threading.Event()
        cancel.set()
        p1, p2 = self._unlocked()
        with p1, p2, mock.patch(
                "tools.demeter.urllib.request.urlopen",
                return_value=self._Resp(_grid_text(n=8).encode())):
            with self.assertRaises(artemis.IngestError) as ctx:
                demeter.ingest(self.area, _FIX, artemis.bbox(*_FIX),
                               cancel, lambda m: None)
        self.assertIn("cancelled", str(ctx.exception))

    def test_load_never_fetches(self):
        with mock.patch("tools.demeter.urllib.request.urlopen",
                        side_effect=AssertionError("load fetched!")):
            with self.assertRaises(FileNotFoundError):
                demeter.load(self.area)


if __name__ == "__main__":
    unittest.main()
