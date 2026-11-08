"""
test_demeter.py — the elevation layer: AAIGrid parsing, the separable
nearest-neighbour resample and its orientation, terrain.bin round-trip,
tercile banding with the NODATA and low-relief guards, the PPM raster and
its regeneration from terrain.bin, and the Vault slugs. No network — the
OpenTopography call is mocked at the hermes seam and the key at the
Cerberus seam.

    python -X utf8 -m unittest tests.test_demeter
"""

import array
import gzip
import os
import shutil
import struct
import sys
import tempfile
import threading
import tracemalloc
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cerberus
from tools import artemis
from tools import demeter
from tools import hermes

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


def _opentopography_grid(n):
    """An n x n grid covering _FIX's ingest square, its values shaped like
    OpenTopography's: float32 heights written out in full, 16-18 characters
    (measured 2026-10-01: 17.99 bytes a cell at ~560 m, 18 at most)."""
    lat_min, lon_min, lat_max, lon_max = artemis.bbox(*_FIX)
    cellsize = (lon_max - lon_min) / n
    row = " ".join(
        repr(struct.unpack("<f", struct.pack("<f", 300.0 + 0.7731 * c))[0])
        for c in range(n))
    header = (f"ncols {n}\nnrows {n}\nxllcorner {lon_min}\n"
              f"yllcorner {lat_max - n * cellsize}\ncellsize {cellsize}\n"
              f"NODATA_value -999999\n")
    return (header + "\n".join([row] * n) + "\n").encode("ascii")


def _parse(text, max_cells=1_000_000):
    """parse_aaigrid on the bytes the download would hand it."""
    return demeter.parse_aaigrid(text.encode("utf-8"), max_cells)


def _header(ncols=2, nrows=2, nodata="-9999"):
    head = (f"ncols {ncols}\nnrows {nrows}\nxllcorner 0\nyllcorner 0\n"
            f"cellsize 1\n")
    return head + (f"NODATA_value {nodata}\n" if nodata is not None else "")


def _oracle(text):
    """The parse as it was before 2026-10-03: the whole text split at once.
    Kept as the reference the windowed parser must agree with on any honest
    grid, wherever its window edges fall."""
    tokens = text.split()
    header = {tokens[i].lower(): float(tokens[i + 1]) for i in range(0, 12, 2)}
    count = int(header["ncols"]) * int(header["nrows"])
    nodata = header.get("nodata_value", -9999.0)
    out = []
    for token in tokens[12:12 + count]:
        value = float(token)
        if value == nodata or not -32767.0 <= value <= 32767.0:
            out.append(demeter.NODATA)
        else:
            out.append(int(round(value)))
    return out


class TestParseAaiGrid(unittest.TestCase):

    def test_parses_header_and_body(self):
        grid = _parse(_grid_text(n=4))
        self.assertEqual((grid.ncols, grid.nrows), (4, 4))
        self.assertEqual(len(grid.values), 16)
        self.assertEqual(grid.values[0], 0)      # first row
        self.assertEqual(grid.values[-1], 30)    # last row

    def test_floats_round_to_metres(self):
        grid = _parse(
            _grid_text(n=2, value=lambda r, c: 412.6 if r == 0 else 100.4))
        self.assertEqual(grid.values[0], 413)
        self.assertEqual(grid.values[-1], 100)

    def test_nodata_maps_to_sentinel(self):
        grid = _parse(
            _grid_text(n=2, value=lambda r, c: -9999 if r == 0 else 50))
        self.assertEqual(grid.values[0], demeter.NODATA)
        self.assertEqual(grid.values[-1], 50)

    def test_malformed_raises(self):
        with self.assertRaises(ValueError):
            _parse("not a grid at all")
        with self.assertRaises(ValueError):
            _parse("ncols 4\nnrows 4\nxllcorner 0\n"
                                  "yllcorner 0\ncellsize 1\nNODATA_value -9999\n1 2")
        with self.assertRaises(ValueError) as ctx:
            _parse("ncols x\nnrows 4\nxllcorner 0\n"
                                  "yllcorner 0\ncellsize 1\nNODATA_value -9999\n")
        self.assertIn("bad_grid", str(ctx.exception))

    def test_api_error_body_is_a_parse_failure(self):
        # OpenTopography reports quota/auth problems as a short text body.
        with self.assertRaises(ValueError) as ctx:
            _parse("Invalid API Key")
        self.assertEqual(str(ctx.exception),
                         "bad_grid: too short to be an AAIGrid")

    def test_values_are_an_int16_array(self):
        # Two bytes a cell, not a Python int and a list slot (~36 bytes).
        grid = _parse(_grid_text(n=4))
        self.assertIsInstance(grid.values, array.array)
        self.assertEqual(grid.values.typecode, "h")

    def test_rounding_is_half_to_even(self):
        # round() is banker's rounding, as int(round(x)) always was.
        grid = _parse(_header(2, 2) + "412.5 413.5 -0.5 -1.5\n")
        self.assertEqual(list(grid.values), [412, 414, 0, -2])

    def test_negative_and_exponent_values(self):
        grid = _parse(_header(2, 2) + "-86.4 1e3 2.5E1 -1.2e2\n")
        self.assertEqual(list(grid.values), [-86, 1000, 25, -120])

    def test_nodata_written_either_way(self):
        # The header says -9999.0, a value says -9999: equal as numbers.
        grid = _parse(_header(2, 1, nodata="-9999.0") + "-9999 5\n")
        self.assertEqual(list(grid.values), [demeter.NODATA, 5])
        grid = _parse(_header(2, 1, nodata="-9999") + "-9999.0 5\n")
        self.assertEqual(list(grid.values), [demeter.NODATA, 5])

    def test_absent_nodata_line_defaults_to_minus_9999(self):
        # Five header lines: the first two body tokens are read as the sixth
        # header pair, as they always were, and NODATA falls back to -9999.
        # This pins the old behaviour; it doesn't bless it.
        text = _header(1, 2, nodata=None) + "extra 1 -9999 8\n"
        self.assertEqual(list(_parse(text).values), [demeter.NODATA, 8])

    def test_out_of_range_and_non_finite_values_are_nodata(self):
        grid = _parse(_header(5, 1) + "32768 -32768 nan inf 32767\n")
        self.assertEqual(list(grid.values), [demeter.NODATA] * 4 + [32767])

    def test_crlf_and_no_trailing_newline(self):
        lf = _header(2, 2) + "1 2\n3 4\n"
        crlf = lf.replace("\n", "\r\n")
        bare = lf.rstrip("\n")
        for text in (lf, crlf, bare):
            self.assertEqual(list(_parse(text).values), [1, 2, 3, 4])

    def test_extra_trailing_values_are_ignored(self):
        grid = _parse(_header(2, 1) + "1 2 3 4 junk\n")
        self.assertEqual(list(grid.values), [1, 2])

    def test_short_body_names_both_counts(self):
        with self.assertRaises(ValueError) as ctx:
            _parse(_header(2, 2) + "1 2 3\n")
        self.assertEqual(str(ctx.exception),
                         "bad_grid: header says 4 values, found 3")

    def test_a_non_finite_header_field_is_refused(self):
        # int(inf) used to escape as OverflowError, past ingest's ValueError
        # catch, failing the whole area instead of skipping its terrain.
        for key, bad in (("ncols", "inf"), ("nrows", "nan"),
                         ("xllcorner", "-inf"), ("cellsize", "nan")):
            lines = [f"{key} {bad}" if line.split()[0] == key else line
                     for line in _header().splitlines()]
            text = "\n".join(lines) + "\n1 2 3 4\n"
            with self.subTest(key=key):
                with self.assertRaises(ValueError) as ctx:
                    _parse(text)
                self.assertEqual(str(ctx.exception),
                                 f"bad_grid: header field {key!r}")

    def test_window_edges_never_change_the_values(self):
        # The windowed parse against the whole-text split, with the window
        # shrunk so its edges land everywhere: inside values, on separators,
        # inside CRLF pairs and runs of whitespace.
        import random
        rng = random.Random(20261003)
        pool = ["412.89990234375", "-86.4", "1e3", "-9999", "-9999.0", "0",
                "561.8394775390625", "40000", "nan", "-inf", "17", "2.5"]
        seps = [" ", "  ", "\t", "\n", "\r\n", " \r\n"]
        for trial in range(30):
            ncols, nrows = rng.randint(1, 9), rng.randint(1, 9)
            body = "".join(rng.choice(pool) + rng.choice(seps)
                           for _ in range(ncols * nrows + rng.randint(0, 3)))
            text = _header(ncols, nrows) + body
            for chunk in (1, 2, 3, 5, 8, 13, 64):
                with self.subTest(trial=trial, chunk=chunk), \
                     mock.patch.object(demeter, "_CHUNK", chunk):
                    self.assertEqual(list(_parse(text).values), _oracle(text))

    def test_a_long_value_is_refused_and_never_quoted_whole(self):
        # A value is at most 18 characters; a megabyte of one must not reach
        # the log (it is logged twice, and the log rotates at 1 MB).
        for chunk in (demeter._CHUNK, 7):
            for text in (_header(1, 1) + "x" * 1_000_000,
                         _header(2, 1) + "1 " + "9" * 1_000_000,
                         "n" * 1_000_000 + " 1\n" + _header(1, 1) + "5",
                         "n" * 1_000_000 + " x\n" + _header(1, 1) + "5"):
                with self.subTest(chunk=chunk, head=text[:12]), \
                     mock.patch.object(demeter, "_CHUNK", chunk):
                    with self.assertRaises(ValueError) as ctx:
                        _parse(text)
                    self.assertTrue(str(ctx.exception).startswith("bad_grid: "))
                    self.assertLess(len(str(ctx.exception)), 200)

    def test_a_header_over_the_limit_is_refused_before_allocating(self):
        # ncols x nrows comes from the server, and the array is sized from
        # it. 4,000,000 cells would be an 8 MB array; the limit refuses the
        # header while the parse has built nothing that size.
        text = _header(2000, 2000) + "1 2 3\n"
        tracemalloc.start()
        try:
            with self.assertRaises(ValueError) as ctx:
                _parse(text, max_cells=1000)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(str(ctx.exception), "bad_grid: header says 4000000 "
                         "values, more than the 1000 the box allows")
        self.assertLess(peak, 1_000_000)

    def test_an_absurd_header_is_a_bad_grid(self):
        # Finite but enormous: refused by the limit, not by MemoryError.
        with self.assertRaises(ValueError) as ctx:
            _parse(_header("1e300", "1e300") + "1\n")
        self.assertIn("the box allows", str(ctx.exception))

    def test_the_limit_is_inclusive(self):
        grid = _parse(_header(2, 2) + "1 2 3 4\n", max_cells=4)
        self.assertEqual(list(grid.values), [1, 2, 3, 4])

    def test_the_length_limit_does_not_depend_on_the_windows(self):
        # A 65-character value well inside one window is refused just as one
        # straddling a window edge is; otherwise where the edges fell would
        # decide whether a grid parses. (As a number it would be 1.1e64,
        # which once read silently as NODATA.)
        text = _header(3, 1) + "1 " + "1" * 65 + " 2\n"
        for chunk in (demeter._CHUNK, 3, 70):
            with self.subTest(chunk=chunk), \
                 mock.patch.object(demeter, "_CHUNK", chunk):
                with self.assertRaises(ValueError) as ctx:
                    _parse(text)
                self.assertEqual(str(ctx.exception),
                                 "bad_grid: a value longer than 64 characters")

    def test_a_bad_value_is_quoted_short(self):
        token = "q" * 60                       # under the length limit
        with self.assertRaises(ValueError) as ctx:
            _parse(_header(1, 1) + token)
        self.assertEqual(str(ctx.exception),
                         "bad_grid: non-numeric value '" + "q" * 40 + "'")


class TestResample(unittest.TestCase):

    def test_size_and_orientation(self):
        grid = _parse(_grid_text(n=20))
        cells = demeter.resample(grid, _FIX, size=8)
        self.assertEqual(len(cells), 64)
        north_row = cells[0:8]
        south_row = cells[56:64]
        # Values encode the source row, which runs north to south.
        self.assertLess(max(north_row), min(south_row))

    def test_nodata_survives_resampling(self):
        grid = _parse(
            _grid_text(n=8, value=lambda r, c: -9999 if r < 4 else 200))
        cells = demeter.resample(grid, _FIX, size=8)
        self.assertEqual(cells[0], demeter.NODATA)      # north half missing
        self.assertEqual(cells[-1], 200)

    def test_cells_outside_source_are_nodata(self):
        # A source grid covering a far-away place leaves every cell unset.
        grid = _parse(_grid_text(center=(10.0, 10.0), n=8))
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

    @staticmethod
    def _Resp(payload, status=200):
        """What hermes.get returns: a finished Reply."""
        return hermes.Reply(status, {}, payload, "https://ot.example.invalid/")

    def test_sealed_vault_is_slugged_before_any_network(self):
        with mock.patch.object(cerberus, "is_unlocked", return_value=False), \
             mock.patch("tools.demeter.hermes.get",
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
                "tools.demeter.hermes.get",
                return_value=self._Resp(_grid_text(n=32).encode())) as opened, \
             mock.patch.object(demeter, "GRID", 16):
            info = demeter.ingest(self.area, _FIX, artemis.bbox(*_FIX),
                                  threading.Event(), lambda m: None)
        # The key travels in the query string, not a header.
        self.assertEqual(opened.call_args.kwargs["params"]["API_Key"], "KEY")
        self.assertEqual(opened.call_args.kwargs["params"]["datasetName"], "USGS10m")
        # And the read is capped by the box it asked for.
        self.assertEqual(opened.call_args.kwargs["cap"],
                         demeter.grid_cap(artemis.bbox(*_FIX)))
        self.assertEqual(info["vdatum"], "NAVD88")
        # The GRID patch reached every stage: a 16 x 16 grid, not 1024 x 1024.
        self.assertEqual((info["cols"], info["rows"]), (16, 16))
        partial = artemis.partial_dir(self.area)
        self.assertTrue(os.path.exists(os.path.join(partial, demeter._BIN_NAME)))
        self.assertTrue(os.path.exists(os.path.join(
            partial, demeter._DERIVED_DIR, demeter._PPM_NAME)))

    def test_download_failure_is_slugged(self):
        p1, p2 = self._unlocked()
        with p1, p2, mock.patch("tools.demeter.hermes.get",
                                side_effect=hermes.Offline("no route")):
            with self.assertRaises(artemis.IngestError) as ctx:
                demeter.ingest(self.area, _FIX, artemis.bbox(*_FIX),
                               threading.Event(), lambda m: None)
        self.assertIn("download_failed", str(ctx.exception))

    def test_a_grid_over_its_cap_is_too_large(self):
        p1, p2 = self._unlocked()
        with p1, p2, mock.patch("tools.demeter.hermes.get",
                                side_effect=hermes.TooLarge("over")):
            with self.assertRaises(artemis.IngestError) as ctx:
                demeter.ingest(self.area, _FIX, artemis.bbox(*_FIX),
                               threading.Event(), lambda m: None)
        self.assertTrue(str(ctx.exception).startswith("too_large: "))

    def test_an_http_error_is_a_download_failure(self):
        p1, p2 = self._unlocked()
        with p1, p2, mock.patch("tools.demeter.hermes.get",
                                return_value=self._Resp(b"Unauthorized", status=401)):
            with self.assertRaises(artemis.IngestError) as ctx:
                demeter.ingest(self.area, _FIX, artemis.bbox(*_FIX),
                               threading.Event(), lambda m: None)
        self.assertEqual(str(ctx.exception), "download_failed: HTTPStatus")

    def test_the_cap_grows_with_the_box(self):
        lat, lon = _FIX
        small = demeter.grid_cap((lat, lon, lat + 0.01, lon + 0.01))
        large = demeter.grid_cap((lat, lon, lat + 0.02, lon + 0.02))
        self.assertGreater(large, 3 * small // 2)
        # An Artemis ingest box, at the shipped grid and measured value size.
        box_cells = ((artemis.bbox(*_FIX)[2] - artemis.bbox(*_FIX)[0]) / demeter._CELL_DEG)
        self.assertGreater(demeter.grid_cap(artemis.bbox(*_FIX)), box_cells)

    def test_an_ingest_costs_less_memory_than_its_download(self):
        # The regression guard for the parse. The whole-text split this
        # replaced peaked at 7.5x the download (a full box, 2026-10-03), and
        # decoding the body whole is 1x on its own; the windowed parse into
        # int16 is a fraction. The threshold is a literal, the download's own
        # size, never a constant under test. The stages after the parse are
        # faked (succeeding): they cost the same for any download, fixed by
        # GRID, and would drown the parse (resample's size=GRID default is
        # bound when the module loads, so patching GRID can't shrink them).
        body = _opentopography_grid(400)          # 160,000 cells, ~2.9 MB
        p1, p2 = self._unlocked()
        with p1, p2, mock.patch("tools.demeter.hermes.get",
                                return_value=self._Resp(body)), \
             mock.patch.object(demeter, "resample",
                               return_value=array.array("h", [100] * 4)), \
             mock.patch.object(demeter, "write_bin",
                               return_value={"vdatum": "NAVD88"}), \
             mock.patch.object(demeter, "write_derived", return_value=None):
            tracemalloc.start()
            try:
                demeter.ingest(self.area, _FIX, artemis.bbox(*_FIX),
                               threading.Event(), lambda m: None)
                peak = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
        self.assertLess(peak, len(body),
                        f"ingest peaked at {peak} B for a {len(body)} B grid")

    def test_grid_cells_covers_the_box_at_a_third_of_an_arc_second(self):
        lat_min, lon_min, lat_max, lon_max = artemis.bbox(*_FIX)
        exact = (lat_max - lat_min) * 10800 * (lon_max - lon_min) * 10800
        cells = demeter.grid_cells(artemis.bbox(*_FIX))
        self.assertGreaterEqual(cells, exact)
        self.assertLess(cells, exact * 1.01)       # just the rounding and edges

    def test_a_grid_twice_its_box_is_refused(self):
        # The limit comes from the box asked for. Twice the cells it needs
        # (a literal 2, not the margin under test) must be refused by the
        # limit itself, not later by a short body.
        lat_min, lon_min, lat_max, lon_max = artemis.bbox(*_FIX)
        rows = int((lat_max - lat_min) * 10800)
        cols = int((lon_max - lon_min) * 10800)
        text = _header(2 * cols, rows) + "1 2 3\n"
        p1, p2 = self._unlocked()
        with p1, p2, mock.patch("tools.demeter.hermes.get",
                                return_value=self._Resp(text.encode())), \
             self.assertLogs("METIS.demeter", "ERROR"):
            with self.assertRaises(artemis.IngestError) as ctx:
                demeter.ingest(self.area, _FIX, artemis.bbox(*_FIX),
                               threading.Event(), lambda m: None)
        self.assertTrue(str(ctx.exception).startswith("bad_grid: header says "))
        self.assertIn("the box allows", str(ctx.exception))

    def test_ingest_logs_the_cells_against_the_limit(self):
        # The live check reads this line, as it reads the bytes-vs-cap one.
        os.makedirs(artemis.partial_dir(self.area))
        p1, p2 = self._unlocked()
        with p1, p2, mock.patch(
                "tools.demeter.hermes.get",
                return_value=self._Resp(_grid_text(n=32).encode())), \
             self.assertLogs("METIS.demeter", "INFO") as logs:
            demeter.ingest(self.area, _FIX, artemis.bbox(*_FIX),
                           threading.Event(), lambda m: None)
        self.assertTrue(any("grid 32 x 32 = 1024 cells, limit " in line
                            for line in logs.output), logs.output)

    def test_a_parse_failure_logs_a_short_excerpt(self):
        # The excerpt is the first 200 bytes, decoded; the message is short.
        text = _header(1, 1) + "x" * 100_000
        p1, p2 = self._unlocked()
        with p1, p2, mock.patch("tools.demeter.hermes.get",
                                return_value=self._Resp(text.encode())), \
             self.assertLogs("METIS.demeter", "ERROR") as logs:
            with self.assertRaises(artemis.IngestError):
                demeter.ingest(self.area, _FIX, artemis.bbox(*_FIX),
                               threading.Event(), lambda m: None)
        self.assertEqual(len(logs.output), 1)
        self.assertLess(len(logs.output[0]), 500)

    def test_cancel_after_download(self):
        cancel = threading.Event()
        cancel.set()
        p1, p2 = self._unlocked()
        with p1, p2, mock.patch(
                "tools.demeter.hermes.get",
                return_value=self._Resp(_grid_text(n=8).encode())):
            with self.assertRaises(artemis.IngestError) as ctx:
                demeter.ingest(self.area, _FIX, artemis.bbox(*_FIX),
                               cancel, lambda m: None)
        self.assertIn("cancelled", str(ctx.exception))

    def test_load_never_fetches(self):
        with mock.patch("tools.demeter.hermes.get",
                        side_effect=AssertionError("load fetched!")):
            with self.assertRaises(FileNotFoundError):
                demeter.load(self.area)


if __name__ == "__main__":
    unittest.main()
