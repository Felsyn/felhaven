"""
test_kepler.py — unit tests for tools/kepler.py (planetary positions).

Pure functions, no network, no Tk. Run from the package root:

    python -X utf8 -m unittest tests.test_kepler
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import hypatia, kepler

# JPL Horizons geocentric astrometric RA/Dec for Mars (command 499), center
# 500@399 (Earth geocenter), retrieved 2026-07-01 via the Horizons API for
# the epoch 2026-Jul-02 00:00 UT:
#   03 59 27.60 +20 15 23.0  ->  RA 59.865 deg, Dec +20.256 deg
_JPL_MARS_UNIX = 1782950400.0   # 2026-07-02 00:00:00 UTC
_JPL_MARS_RA = 59.865
_JPL_MARS_DEC = 20.256


class TestRaDecAgainstJplHorizons(unittest.TestCase):
    def test_mars_within_two_degrees_of_horizons(self):
        ra, dec = kepler._radec("Mars", _JPL_MARS_UNIX)
        self.assertAlmostEqual(ra, _JPL_MARS_RA, delta=2.0)
        self.assertAlmostEqual(dec, _JPL_MARS_DEC, delta=2.0)


class TestKeplerEquationConvergence(unittest.TestCase):
    def test_converges_for_eccentricities_up_to_mercurys_worst_case(self):
        # Mercury's e is ~0.206; sweep past it for margin (locked worst case
        # in the handoff is 0.25).
        for e in (0.0, 0.05, 0.1, 0.206, 0.25):
            for m_deg in (0, 30, 90, 150, 179, 181, 270, 350):
                M = math.radians(m_deg)
                E = kepler._solve_kepler(M, e)
                # Kepler's equation itself, recomputed independently of the
                # solver, is the truth this test pins against.
                residual = E - e * math.sin(E) - M
                self.assertAlmostEqual(residual, 0.0, delta=1e-5)


class TestOnePlanetFailureDegrades(unittest.TestCase):
    def test_bad_element_entry_drops_only_that_planet(self):
        original = kepler._ELEMENTS["Mars"]
        kepler._ELEMENTS["Mars"] = {}   # missing keys -> KeyError inside _radec
        try:
            out = kepler.positions(1782950400.0, hypatia.HYPATIA_LAT, hypatia.HYPATIA_LON)
            names = [p["name"] for p in out]
            self.assertNotIn("Mars", names)
            self.assertEqual(len(out), 4)
        finally:
            kepler._ELEMENTS["Mars"] = original

    def test_never_raises_even_on_total_failure(self):
        original = kepler._ELEMENTS
        kepler._ELEMENTS = {}
        try:
            out = kepler.positions(1782950400.0, hypatia.HYPATIA_LAT, hypatia.HYPATIA_LON)
            self.assertEqual(out, [])
        finally:
            kepler._ELEMENTS = original


class TestPositionsShape(unittest.TestCase):
    def test_five_planets_with_expected_keys(self):
        out = kepler.positions(1782950400.0, hypatia.HYPATIA_LAT, hypatia.HYPATIA_LON)
        names = {p["name"] for p in out}
        self.assertEqual(names, {"Mercury", "Venus", "Mars", "Jupiter", "Saturn"})
        for p in out:
            for key in ("name", "glyph", "alt", "az"):
                self.assertIn(key, p)
            self.assertTrue(0.0 <= p["az"] < 360.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
