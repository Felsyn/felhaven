"""
test_cadmus.py — the shapefile/DBF decoder against captured-real TIGER bytes.

The fixtures under tests/fixtures/artemis/ are byte-level excerpts of the
real Harrison County WV roads shapefile (see the README there), so every
assertion here is against values the Census Bureau actually shipped —
recomputed from the fixture, never invented.

    python -X utf8 -m unittest tests.test_cadmus
"""

import array
import os
import struct
import sys
import tracemalloc
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import cadmus

_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "fixtures", "artemis")


def _read(name):
    with open(os.path.join(_FIXTURES, name), "rb") as f:
        return f.read()


def _shp(contents):
    """A minimal .shp: the 100-byte header (magic, file length) and one
    record per content blob, numbered from 1."""
    body = b"".join(struct.pack(">ii", i + 1, len(c) // 2) + c
                    for i, c in enumerate(contents))
    header = bytearray(100)
    struct.pack_into(">i", header, 0, 9994)
    struct.pack_into(">i", header, 24, (100 + len(body)) // 2)
    return bytes(header) + body


def _dbf(record_count, field_len=1):
    """A minimal .dbf with one character field and `record_count` live
    records of `field_len` bytes each."""
    header = bytearray(32)
    struct.pack_into("<IHH", header, 4, record_count, 32 + 32 + 1, 1 + field_len)
    field = bytearray(32)
    field[0:4] = b"NAME"
    field[11] = ord("C")
    field[16] = field_len
    return (bytes(header) + bytes(field) + b"\x0d"
            + (b" " + b"x" * field_len) * record_count)


class TestReadShp(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.shp = _read("roads_mini.shp")
        cls.shapes = cadmus.read_shp(cls.shp)

    def test_record_count(self):
        self.assertEqual(len(self.shapes), 12)

    def test_all_polylines(self):
        self.assertEqual({stype for stype, _ in self.shapes}, {3})

    def test_points_fall_inside_header_bbox(self):
        # The fixture builder recomputed the header bbox from the kept
        # records — every decoded point must land inside it.
        xmin, ymin, xmax, ymax = struct.unpack_from("<4d", self.shp, 36)
        for _, parts in self.shapes:
            for part in parts:
                self.assertTrue(part)  # no empty parts in TIGER lines
                for x, y in cadmus.points(part):
                    self.assertTrue(xmin <= x <= xmax and ymin <= y <= ymax,
                                    f"({x}, {y}) outside header bbox")

    def test_coordinates_look_like_harrison_county(self):
        # Roads from FIPS 54033 are all near 39.4 N, -80.3 W.
        x, y = next(cadmus.points(self.shapes[0][1][0]))
        self.assertAlmostEqual(y, 39.4, delta=0.4)
        self.assertAlmostEqual(x, -80.4, delta=0.4)

    def test_a_part_is_a_flat_array_of_doubles(self):
        # 16 bytes a point, as in the file; a list of (x, y) tuples cost ~112.
        part = self.shapes[0][1][0]
        self.assertIsInstance(part, array.array)
        self.assertEqual(part.typecode, "d")
        self.assertEqual(len(part) % 2, 0)

    def test_every_point_matches_an_independent_decode(self):
        # Walk the file's records with struct alone and compare every point:
        # the array path must change the shape of the data, never a value.
        offset = 100
        for _, parts in self.shapes:
            (content_words,) = struct.unpack_from(">i", self.shp, offset + 4)
            content = offset + 8
            num_parts, num_points = struct.unpack_from("<ii", self.shp, content + 36)
            starts = struct.unpack_from(f"<{num_parts}i", self.shp, content + 44)
            flat = struct.unpack_from(f"<{2 * num_points}d", self.shp,
                                      content + 44 + 4 * num_parts)
            bounds = list(starts) + [num_points]
            for i, part in enumerate(parts):
                expected = [(flat[2 * j], flat[2 * j + 1])
                            for j in range(bounds[i], bounds[i + 1])]
                self.assertEqual(list(cadmus.points(part)), expected)
            offset = content + content_words * 2
        self.assertEqual(offset, len(self.shp))

    def test_points_pairs_x_then_y(self):
        part = array.array("d", [1.0, 2.0, 3.0, 4.0])
        self.assertEqual(list(cadmus.points(part)), [(1.0, 2.0), (3.0, 4.0)])
        self.assertEqual(list(cadmus.points(array.array("d"))), [])

    def test_a_part_costs_about_its_own_bytes(self):
        # The regression guard for the decode: one polyline of 65,536 points
        # (1 MiB of coordinates). As (x, y) tuples it peaked at 8.0x the file
        # (2026-10-03); as one array('d') it is ~1x. The threshold is a
        # literal, twice the file, never a constant under test.
        n = 65536
        coords = (array.array("d", [-80.3, 39.4]) * n).tobytes()
        content = (struct.pack("<i", 3) + bytes(32) + struct.pack("<ii", 1, n)
                   + struct.pack("<i", 0) + coords)
        shp = _shp([content])
        tracemalloc.start()
        try:
            shapes = cadmus.read_shp(shp)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(len(shapes[0][1][0]), 2 * n)
        self.assertLess(peak, 2 * len(shp),
                        f"read_shp peaked at {peak} B for a {len(shp)} B file")

    def test_a_null_shape_has_no_parts(self):
        shp = _shp([struct.pack("<i", 0)])
        self.assertEqual(cadmus.read_shp(shp), [(0, [])])

    def test_bad_magic_raises(self):
        broken = b"\x00" * 4 + self.shp[4:]
        with self.assertRaises(ValueError) as ctx:
            cadmus.read_shp(broken)
        self.assertIn("bad_shp", str(ctx.exception))

    def test_truncation_raises(self):
        with self.assertRaises(ValueError):
            cadmus.read_shp(self.shp[:50])          # shorter than the header
        with self.assertRaises(ValueError):
            cadmus.read_shp(self.shp[:150])         # header claims more bytes

    def test_unsupported_shape_type_raises(self):
        # Flip the first record's shape type to Point (1).
        broken = bytearray(self.shp)
        struct.pack_into("<i", broken, 108, 1)      # 100 header + 8 record header
        with self.assertRaises(ValueError) as ctx:
            cadmus.read_shp(bytes(broken))
        self.assertIn("unsupported_shape", str(ctx.exception))


class TestReadDbf(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.dbf = _read("roads_mini.dbf")
        cls.records = cadmus.read_dbf(cls.dbf)

    def test_record_count_and_fields(self):
        self.assertEqual(len(self.records), 12)
        self.assertEqual(sorted(self.records[0]),
                         ["FULLNAME", "LINEARID", "MTFCC", "RTTYP"])

    def test_values_are_stripped_strings(self):
        for record in self.records:
            for value in record.values():
                self.assertIsInstance(value, str)
                self.assertEqual(value, value.strip())

    def test_known_capture_values(self):
        # The fixture deliberately includes the I-79 record and an unnamed ramp.
        names = [r["FULLNAME"] for r in self.records]
        self.assertIn("I- 79", names)
        self.assertIn("", names)
        codes = {r["MTFCC"] for r in self.records}
        self.assertEqual(codes, {"S1100", "S1200", "S1400", "S1500",
                                 "S1630", "S1730", "S1740", "S1820"})
        for code in codes:
            self.assertRegex(code, r"^S\d{4}$")

    def test_truncation_raises(self):
        with self.assertRaises(ValueError):
            cadmus.read_dbf(self.dbf[:16])
        with self.assertRaises(ValueError):
            cadmus.read_dbf(self.dbf[:len(self.dbf) // 2])

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            cadmus.read_dbf(b"\xff" * 64)


class TestReadPair(unittest.TestCase):

    def test_pairing(self):
        pairs = cadmus.read_pair(_read("roads_mini.shp"), _read("roads_mini.dbf"))
        self.assertEqual(len(pairs), 12)
        for (stype, parts), attrs in pairs:
            self.assertEqual(stype, 3)
            self.assertIn("MTFCC", attrs)

    def test_count_mismatch_raises(self):
        shp = _read("roads_mini.shp")
        dbf = _read("counties_mini.dbf")           # 15 records vs 12 shapes
        with self.assertRaises(ValueError) as ctx:
            cadmus.read_pair(shp, dbf)
        self.assertIn("pair_mismatch", str(ctx.exception))

    def test_the_dbf_count_is_checked_before_any_record_is_decoded(self):
        # One null shape against a .dbf that declares (and holds) 1,000,000
        # one-byte records: decoded, that is a million dicts (~190 MB, the
        # shape that reached ~5.35 GB at a zip member's cap). Refused first.
        shp = _shp([struct.pack("<i", 0)])
        dbf = _dbf(1_000_000)
        tracemalloc.start()
        try:
            with self.assertRaises(ValueError) as ctx:
                cadmus.read_pair(shp, dbf)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(str(ctx.exception),
                         "pair_mismatch: 1 shapes but 1000000 attribute records")
        self.assertLess(peak, 1_000_000)

    def test_a_short_dbf_is_a_bad_dbf_through_read_pair(self):
        # The header read must refuse with its slug, never a struct.error
        # (which the layers' except clauses don't catch).
        with self.assertRaises(ValueError) as ctx:
            cadmus.read_pair(_shp([struct.pack("<i", 0)]), b"\x03" * 10)
        self.assertTrue(str(ctx.exception).startswith("bad_dbf: "))

    def test_matching_counts_still_pair(self):
        pairs = cadmus.read_pair(_shp([struct.pack("<i", 0)] * 3), _dbf(3))
        self.assertEqual(pairs, [((0, []), {"NAME": "x"})] * 3)

    def test_counties_fixture_is_polygons(self):
        pairs = cadmus.read_pair(_read("counties_mini.shp"),
                                 _read("counties_mini.dbf"))
        self.assertEqual(len(pairs), 15)
        self.assertEqual({stype for (stype, _), _ in pairs}, {5})
        geoids = {attrs["GEOID"] for _, attrs in pairs}
        self.assertIn("54033", geoids)             # Harrison County itself


if __name__ == "__main__":
    unittest.main()
