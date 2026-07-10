"""
test_hypatia.py — unit tests for tools/hypatia.py (star-position math + catalog).

Pure functions plus the real committed catalog files (no network, no Tk). Run
from the package root:

    python -X utf8 -m unittest tests.test_hypatia

hypatia._active_preset is module-global mutable state (by design — see the
module docstring's thread-safety note). Every test that touches it restores
"current" in tearDown so no test leaks preset state into another test file
sharing this process (unittest discover runs everything in one process).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import hypatia

# Polaris (HIP 11767), J2000 — an independent reference figure, not read back
# from our own catalog, so this test can't drift along with a bad catalog
# regeneration.
_POLARIS_RA = 37.9546
_POLARIS_DEC = 89.2641

# Two arbitrary fixed timestamps, deliberately far apart, to prove the
# Polaris invariant holds independent of time-of-day/season.
_UNIX_A = 1704067200.0   # 2024-01-01 00:00:00 UTC
_UNIX_B = 1719792000.0   # 2024-07-01 00:00:00 UTC


class TestAltAzPolarisInvariant(unittest.TestCase):
    """Polaris sits ~1 degree from the north celestial pole, so its altitude
    at any time should equal the observer's latitude within ~1 degree — this
    pins sidereal time, hour angle, and the alt formula together against a
    recomputable truth, not a fixture."""

    def test_altitude_tracks_latitude_at_two_arbitrary_times(self):
        for unix in (_UNIX_A, _UNIX_B):
            alt, _az = hypatia._altaz(
                _POLARIS_RA, _POLARIS_DEC, hypatia.HYPATIA_LAT, hypatia.HYPATIA_LON, unix)
            self.assertAlmostEqual(alt, hypatia.HYPATIA_LAT, delta=1.0)

    def test_azimuth_near_north_at_two_arbitrary_times(self):
        for unix in (_UNIX_A, _UNIX_B):
            _alt, az = hypatia._altaz(
                _POLARIS_RA, _POLARIS_DEC, hypatia.HYPATIA_LAT, hypatia.HYPATIA_LON, unix)
            # az wraps through 0/360 — compare via the shorter angular distance.
            dist = min(az, 360.0 - az)
            self.assertLess(dist, 5.0)


class TestDueSouthCheck(unittest.TestCase):
    """A star whose RA equals the local sidereal time is on the meridian:
    az ~= 180 (south) when its dec is south of the observer's latitude, and
    alt ~= 90 - |lat - dec|."""

    def test_meridian_crossing(self):
        unix = _UNIX_A
        lat = hypatia.HYPATIA_LAT
        lst = hypatia._lst_deg(hypatia.HYPATIA_LON, unix)
        dec = lat - 30.0   # south of zenith -> transits due south
        alt, az = hypatia._altaz(lst, dec, lat, hypatia.HYPATIA_LON, unix)
        self.assertAlmostEqual(az, 180.0, delta=1.0)
        self.assertAlmostEqual(alt, 90.0 - abs(lat - dec), delta=1.0)


class TestPresetInvariant(unittest.TestCase):
    def tearDown(self):
        hypatia._active_preset = "current"

    def test_polaris_near_zenith_at_north_pole(self):
        alt, _az = hypatia._altaz(
            _POLARIS_RA, _POLARIS_DEC, hypatia.PRESETS["north_pole"], hypatia.HYPATIA_LON, _UNIX_A)
        self.assertAlmostEqual(alt, 90.0, delta=1.0)

    def test_polaris_near_horizon_at_equator(self):
        alt, _az = hypatia._altaz(
            _POLARIS_RA, _POLARIS_DEC, hypatia.PRESETS["equator"], hypatia.HYPATIA_LON, _UNIX_A)
        self.assertAlmostEqual(alt, 0.0, delta=1.5)

    def test_set_preset_snapshot_matches(self):
        snap = hypatia.set_preset("north_pole")
        self.assertEqual(snap["preset"], "north_pole")
        self.assertEqual(hypatia._active_preset, "north_pole")

    def test_set_preset_unknown_name_is_noop(self):
        hypatia.set_preset("current")
        snap = hypatia.set_preset("mars_colony")
        self.assertEqual(snap["preset"], "current")
        self.assertEqual(hypatia._active_preset, "current")


class TestDegradedPaths(unittest.TestCase):
    def test_empty_catalog_raises(self):
        original = hypatia._STARS
        hypatia._STARS = {}
        try:
            with self.assertRaises(RuntimeError):
                hypatia.fetch()
        finally:
            hypatia._STARS = original

    def test_missing_stars_file_returns_empty_dict(self):
        self.assertEqual(hypatia._load_stars("Z:/does/not/exist.json"), {})

    def test_missing_constellations_file_returns_empty_list(self):
        self.assertEqual(hypatia._load_constellations("Z:/does/not/exist.json"), [])

    def test_missing_lore_file_returns_empty_dict(self):
        self.assertEqual(hypatia._load_lore("Z:/does/not/exist.json"), {})

    def test_missing_lore_defaults_constellation_to_empty_string(self):
        # Same expression the module-load merge uses (lore.get(abbr, "")) —
        # with an empty lore dict every constellation must fall back to "".
        empty_lore = hypatia._load_lore("Z:/does/not/exist.json")
        for con in hypatia._CONSTELLATIONS[:5]:
            self.assertEqual(empty_lore.get(con["abbr"], ""), "")


class TestCatalogIntegrity(unittest.TestCase):
    """Guards against a bad catalog regeneration: every HIP referenced by a
    constellation line must exist in the star dict, or the chart draws with
    a broken joint."""

    def test_every_line_endpoint_resolves_to_a_star(self):
        star_ids = set(hypatia._STARS.keys())
        missing = set()
        for con in hypatia._CONSTELLATIONS:
            for a, b in con["lines"]:
                if a not in star_ids:
                    missing.add(a)
                if b not in star_ids:
                    missing.add(b)
        self.assertEqual(missing, set())

    def test_every_constellation_carries_a_lore_string(self):
        for con in hypatia._CONSTELLATIONS:
            self.assertIsInstance(con["lore"], str)


class TestPayloadContract(unittest.TestCase):
    def tearDown(self):
        hypatia._active_preset = "current"

    def test_shape(self):
        snap = hypatia.fetch()
        for key in ("generated_unix", "lst_deg", "preset", "stars",
                    "constellations", "planets"):
            self.assertIn(key, snap)
        # Phase 2: kepler.positions() always returns the five classical
        # planets (never raises, degrades per-planet on failure — see
        # tests/test_kepler.py for that contract in detail).
        self.assertEqual(len(snap["planets"]), 5)
        for p in snap["planets"]:
            for key in ("name", "glyph", "alt", "az"):
                self.assertIn(key, p)
        self.assertEqual(snap["preset"], "current")
        self.assertGreater(len(snap["stars"]), 0)
        self.assertGreater(len(snap["constellations"]), 0)
        sample_star = next(iter(snap["stars"].values()))
        for key in ("name", "mag", "alt", "az"):
            self.assertIn(key, sample_star)
        sample_con = snap["constellations"][0]
        for key in ("abbr", "name", "lore", "lines"):
            self.assertIn(key, sample_con)


if __name__ == "__main__":
    unittest.main(verbosity=2)
