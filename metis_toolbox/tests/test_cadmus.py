"""
test_cadmus.py — the shapefile/DBF decoder against captured-real TIGER bytes.

The fixtures under tests/fixtures/artemis/ are byte-level excerpts of the
real Harrison County WV roads shapefile (see the README there), so every
assertion here is against values the Census Bureau actually shipped —
recomputed from the fixture, never invented.

    python -X utf8 -m unittest tests.test_cadmus
"""

import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import cadmus

_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "fixtures", "artemis")


def _read(name):
    with open(os.path.join(_FIXTURES, name), "rb") as f:
        return f.read()


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
                for x, y in part:
                    self.assertTrue(xmin <= x <= xmax and ymin <= y <= ymax,
                                    f"({x}, {y}) outside header bbox")

    def test_coordinates_look_like_harrison_county(self):
        # Roads from FIPS 54033 are all near 39.4 N, -80.3 W.
        x, y = self.shapes[0][1][0][0]
        self.assertAlmostEqual(y, 39.4, delta=0.4)
        self.assertAlmostEqual(x, -80.4, delta=0.4)

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

    def test_counties_fixture_is_polygons(self):
        pairs = cadmus.read_pair(_read("counties_mini.shp"),
                                 _read("counties_mini.dbf"))
        self.assertEqual(len(pairs), 15)
        self.assertEqual({stype for (stype, _), _ in pairs}, {5})
        geoids = {attrs["GEOID"] for _, attrs in pairs}
        self.assertIn("54033", geoids)             # Harrison County itself


if __name__ == "__main__":
    unittest.main()
