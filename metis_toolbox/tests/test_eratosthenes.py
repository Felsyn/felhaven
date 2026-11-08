"""
test_eratosthenes.py — the location search: three coordinate grammars, the
waypoints file, and the resolve() order. The geocoder is mocked at the
hermes seam; nothing here touches the network.

The three grammars are checked against ONE real point (39.2806, -80.3440)
written three ways, so a sign or a minutes/seconds slip shows up as a
disagreement between forms rather than as three separately-plausible numbers.

    python -X utf8 -m unittest tests.test_eratosthenes
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import eratosthenes, hermes

_POINT = (39.2806, -80.3440)


class TestParseCoordinates(unittest.TestCase):

    def _assertPoint(self, text, places=4):
        parsed = eratosthenes.parse_coordinates(text)
        self.assertIsNotNone(parsed, f"{text!r} did not parse")
        self.assertAlmostEqual(parsed[0], _POINT[0], places=places)
        self.assertAlmostEqual(parsed[1], _POINT[1], places=places)

    def test_decimal_degrees(self):
        self._assertPoint("39.2806, -80.3440")
        self._assertPoint("39.2806 -80.3440")
        self._assertPoint("39.2806N 80.3440W")
        self._assertPoint("N39.2806 W80.3440")

    def test_degrees_and_decimal_minutes(self):
        self._assertPoint("39 16.836 N 080 20.640 W")
        self._assertPoint("N 39 16.836 W 080 20.640")

    def test_degrees_minutes_seconds(self):
        self._assertPoint("39 16 50 N 080 20 38 W", places=3)
        self._assertPoint("39°16'50\" N 080°20'38\" W", places=3)

    def test_southern_and_eastern_hemispheres(self):
        parsed = eratosthenes.parse_coordinates("33 51.9 S 151 12.6 E")
        self.assertAlmostEqual(parsed[0], -33.865, places=3)
        self.assertAlmostEqual(parsed[1], 151.21, places=3)

    def test_letters_may_be_given_in_either_order(self):
        # Longitude first, lettered — the letters say which is which.
        parsed = eratosthenes.parse_coordinates("080 20.640 W 39 16.836 N")
        self.assertAlmostEqual(parsed[0], _POINT[0], places=4)
        self.assertAlmostEqual(parsed[1], _POINT[1], places=4)

    def test_rejects_non_coordinates(self):
        for text in ("", "   ", "Dolly Sods", "the elk site",
                     "1600 Pennsylvania Ave"):
            with self.subTest(text=text):
                self.assertIsNone(eratosthenes.parse_coordinates(text))

    def test_rejects_malformed_numbers(self):
        # Odd component counts, out-of-range minutes, impossible latitudes,
        # and two letters naming the same axis.
        for text in ("39.2806", "39 16 50 80", "39 90.0 N 080 20.6 W",
                     "139.5 -80.3", "39.4 -200.1", "39 N 80 S"):
            with self.subTest(text=text):
                self.assertIsNone(eratosthenes.parse_coordinates(text))

    def test_format_round_trips(self):
        label = eratosthenes.format_coordinates(*_POINT)
        self.assertIn("N", label)
        self.assertIn("W", label)
        self._assertPoint(label)


class TestWaypoints(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._orig = eratosthenes._WAYPOINTS_PATH
        eratosthenes._WAYPOINTS_PATH = os.path.join(self._dir, "wp.json")

    def tearDown(self):
        eratosthenes._WAYPOINTS_PATH = self._orig
        shutil.rmtree(self._dir, ignore_errors=True)

    def _write(self, payload):
        with open(eratosthenes._WAYPOINTS_PATH, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def test_missing_file_is_empty_not_an_error(self):
        self.assertEqual(eratosthenes.waypoints(), [])

    def test_malformed_file_is_empty(self):
        with open(eratosthenes._WAYPOINTS_PATH, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(eratosthenes.waypoints(), [])

    def test_bad_entries_are_skipped_not_fatal(self):
        self._write([{"name": "HOME", "lat": 39.4, "lon": -80.3},
                     {"name": "BROKEN"},
                     {"lat": 1.0, "lon": 2.0},
                     "nonsense"])
        saved = eratosthenes.waypoints()
        self.assertEqual([e["name"] for e in saved], ["HOME"])

    def test_resolve_finds_a_waypoint_case_insensitively(self):
        self._write([{"name": "Camp Ridge", "lat": 39.4, "lon": -80.3}])
        result = eratosthenes.resolve("camp ridge")
        self.assertEqual(result["source"], "waypoint")
        self.assertEqual(result["label"], "Camp Ridge")
        self.assertAlmostEqual(result["lat"], 39.4)

    def test_exact_name_beats_a_longer_prefix_match(self):
        self._write([{"name": "HOME", "lat": 1.0, "lon": 2.0},
                     {"name": "HOME RIDGE", "lat": 3.0, "lon": 4.0}])
        self.assertAlmostEqual(eratosthenes.resolve("HOME")["lat"], 1.0)

    def test_ambiguous_prefix_does_not_guess(self):
        self._write([{"name": "CAMP ONE", "lat": 1.0, "lon": 2.0},
                     {"name": "CAMP TWO", "lat": 3.0, "lon": 4.0}])
        with mock.patch("tools.eratosthenes._geocode",
                        return_value={"error": "no_results"}):
            self.assertEqual(eratosthenes.resolve("camp")["error"], "no_results")

    def test_unique_prefix_resolves(self):
        self._write([{"name": "CAMP RIDGE", "lat": 5.0, "lon": 6.0}])
        self.assertAlmostEqual(eratosthenes.resolve("camp")["lat"], 5.0)

    def test_a_waypoint_off_the_map_is_skipped(self):
        # A hand edit: json reads NaN and Infinity, float() reads "nan". The
        # chart names a cache folder from the pair, and its area_status()
        # raised on a non-finite one.
        for lat, lon in ((float("nan"), 1.0), (1.0, float("inf")), ("nan", 1.0),
                         (1e300, 1.0), (91.0, 1.0), (1.0, -181.0)):
            with self.subTest(lat=lat, lon=lon):
                self._write([{"name": "HOME", "lat": 39.4, "lon": -80.3},
                             {"name": "ODD", "lat": lat, "lon": lon}])
                self.assertEqual([e["name"] for e in eratosthenes.waypoints()], ["HOME"])
                with mock.patch("tools.eratosthenes._geocode",
                                return_value={"error": "no_results"}):
                    self.assertEqual(eratosthenes.resolve("odd"), {"error": "no_results"})


def _Resp(payload, status=200):
    """What hermes.get returns: a finished Reply."""
    return hermes.Reply(status, {}, payload, "https://geocoding.example.invalid/")


def _match_payload():
    return json.dumps({"result": {"addressMatches": [
        {"matchedAddress": "1600 PENNSYLVANIA AVE NW, WASHINGTON, DC, 20500",
         "coordinates": {"x": -77.0365, "y": 38.8977}}]}}).encode()


class TestResolveOrder(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._orig = eratosthenes._WAYPOINTS_PATH
        eratosthenes._WAYPOINTS_PATH = os.path.join(self._dir, "wp.json")

    def tearDown(self):
        eratosthenes._WAYPOINTS_PATH = self._orig
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_empty_query(self):
        self.assertEqual(eratosthenes.resolve("  ")["error"], "empty_query")

    def test_coordinates_short_circuit_before_any_network(self):
        with mock.patch("tools.eratosthenes.hermes.get",
                        side_effect=AssertionError("coordinates hit the network")):
            result = eratosthenes.resolve("39.2806, -80.3440")
        self.assertEqual(result["source"], "coordinates")

    def test_waypoints_short_circuit_before_any_network(self):
        with open(eratosthenes._WAYPOINTS_PATH, "w", encoding="utf-8") as fh:
            json.dump([{"name": "THE ELK SITE", "lat": 39.4, "lon": -80.3}], fh)
        with mock.patch("tools.eratosthenes.hermes.get",
                        side_effect=AssertionError("waypoint hit the network")):
            result = eratosthenes.resolve("the elk site")
        self.assertEqual(result["source"], "waypoint")

    def test_address_falls_through_to_the_geocoder(self):
        with mock.patch("tools.eratosthenes.hermes.get",
                        return_value=_Resp(_match_payload())):
            result = eratosthenes.resolve("1600 Pennsylvania Ave NW")
        self.assertEqual(result["source"], "address")
        self.assertAlmostEqual(result["lat"], 38.8977)
        self.assertAlmostEqual(result["lon"], -77.0365)
        self.assertIn("PENNSYLVANIA", result["label"])

    def test_no_matches_is_no_results(self):
        payload = json.dumps({"result": {"addressMatches": []}}).encode()
        with mock.patch("tools.eratosthenes.hermes.get",
                        return_value=_Resp(payload)):
            self.assertEqual(eratosthenes.resolve("nowhere at all")["error"],
                             "no_results")

    def test_offline_geocoder_is_slugged_not_raised(self):
        with mock.patch("tools.eratosthenes.hermes.get",
                        side_effect=hermes.Offline("no route")):
            result = eratosthenes.resolve("1600 Pennsylvania Ave NW")
        self.assertTrue(result["error"].startswith("geocode_failed"))
        # The slug never carries the exception's message (§2).
        self.assertNotIn("no route", result["error"])

    def test_an_http_error_is_slugged(self):
        with mock.patch("tools.eratosthenes.hermes.get",
                        return_value=_Resp(b"busy", status=503)):
            self.assertEqual(eratosthenes.resolve("1600 Pennsylvania Ave NW")["error"],
                             "geocode_failed: HTTPStatus")

    def test_the_geocoder_read_is_capped(self):
        with mock.patch("tools.eratosthenes.hermes.get",
                        return_value=_Resp(_match_payload())) as get:
            eratosthenes.resolve("1600 Pennsylvania Ave NW")
        self.assertEqual(get.call_args.kwargs["cap"], eratosthenes._MAX_BYTES)

    def test_unexpected_response_shape_is_slugged(self):
        with mock.patch("tools.eratosthenes.hermes.get",
                        return_value=_Resp(b'{"nope": true}')):
            self.assertTrue(
                eratosthenes.resolve("somewhere")["error"].startswith(
                    "geocode_failed"))

    def test_a_geocoded_place_off_the_map_is_slugged(self):
        # A broken or hostile reply. It used to come back as a place, and the
        # chart's area_status() raised on a non-finite one.
        for y, x in ((b"NaN", b"-77.0"), (b"38.9", b"Infinity"), (b'"nan"', b"-77.0"),
                     (b"1e300", b"-77.0"), (b"91", b"-77.0"), (b"38.9", b"-181")):
            with self.subTest(y=y, x=x):
                payload = (b'{"result": {"addressMatches": [{"matchedAddress": "X", '
                           b'"coordinates": {"x": ' + x + b', "y": ' + y + b'}}]}}')
                with mock.patch("tools.eratosthenes.hermes.get",
                                return_value=_Resp(payload)):
                    result = eratosthenes.resolve("1600 Pennsylvania Ave NW")
                self.assertEqual(result, {"error": "geocode_failed: unexpected response shape"})


if __name__ == "__main__":
    unittest.main()
