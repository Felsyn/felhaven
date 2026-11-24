"""
test_aura_place.py — Atmospherics' PLACE pick in Aura's worker.

fetch() fetches Home as always and, when themis.atmos_place() names a cached
Artemis area, that place too, under data["picked"]. The rules pinned here:

  - no pick: no "picked" key and no second request;
  - a pick asks both sources for the AREA's coordinates, never Home's;
  - a picked failure never raises (Home's displays keep their reading);
  - a Home failure still raises, so Kairos's retry ladder engages;
  - a recycled area says "missing" and costs no request;
  - the picked reading never enters handle()'s cache, which stays Home.

    python -X utf8 -m unittest tests.test_aura_place
"""

import copy
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import artemis, aura
from tests._fixtures import load_real_j1, load_real_open_meteo

_AT = (51.4779, 0.0)                     # the picked area: Greenwich, generic
_HOME = (39.0997, -94.5786)              # Home: a generic Kansas City point
_PICK_QUERY = "51.4779,0.0000"


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return copy.deepcopy(self._payload)


def _picked_j1() -> dict:
    """The real wttr.in fixture with a temperature no Home reading has."""
    raw = load_real_j1()
    raw["current_condition"][0]["temp_F"] = "99"
    return raw


class _PlaceBase(unittest.TestCase):

    def setUp(self):
        aura._clear_cache()
        self.addCleanup(aura._clear_cache)
        cache = tempfile.mkdtemp(prefix="aura_place_")
        self.addCleanup(shutil.rmtree, cache, True)
        patch = mock.patch.object(artemis, "CACHE_DIR", cache)
        patch.start()
        self.addCleanup(patch.stop)
        self.area = artemis.area_id(*_AT)
        os.makedirs(artemis.area_dir(self.area))
        artemis.write_manifest(artemis.area_dir(self.area),
                               {"center": list(_AT), "label": "Observatory",
                                "schema": artemis.MANIFEST_SCHEMA})
        for name, value in (("_location", lambda: "Kansas City"),
                            ("themis.latitude", lambda: _HOME[0]),
                            ("themis.longitude", lambda: _HOME[1])):
            patch = mock.patch(f"tools.aura.{name}", value)
            patch.start()
            self.addCleanup(patch.stop)
        self.pick = ""
        patch = mock.patch("themis.atmos_place", lambda: self.pick)
        patch.start()
        self.addCleanup(patch.stop)
        self.calls: list[tuple[str, dict]] = []

    def _net(self, *, home_wttr=True, pick_wttr=True, home_om=True, pick_om=True):
        """hermes.get routed by host and place. True answers with a fixture,
        False raises, as an unreachable host would."""
        def get(url, *args, params=None, **kwargs):
            self.calls.append((url, dict(params or {})))
            if "open-meteo" in url:
                is_pick = (params or {}).get("latitude") == _AT[0]
                ok = pick_om if is_pick else home_om
                payload = load_real_open_meteo()
            else:
                is_pick = _PICK_QUERY in url
                ok = pick_wttr if is_pick else home_wttr
                payload = _picked_j1() if is_pick else load_real_j1()
            if not ok:
                raise ConnectionError("host down")
            return _FakeResp(payload)
        patch = mock.patch("tools.aura.hermes.get", side_effect=get)
        patch.start()
        self.addCleanup(patch.stop)


class TestNoPick(_PlaceBase):

    def test_home_alone_is_one_request_and_no_picked_key(self):
        self._net()
        data = aura.fetch()
        self.assertNotIn("picked", data)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("Kansas+City", self.calls[0][0])


class TestAPick(_PlaceBase):

    def setUp(self):
        super().setUp()
        self.pick = self.area

    def test_the_pick_is_asked_for_by_its_own_coordinates(self):
        self._net()
        data = aura.fetch()
        self.assertEqual(len(self.calls), 2)
        self.assertIn(_PICK_QUERY, self.calls[1][0])
        picked = data["picked"]
        self.assertEqual((picked["area"], picked["name"], picked["temp_f"]),
                         (self.area, "Observatory", 99))
        self.assertNotEqual(data["temp_f"], 99)          # the rest stays Home

    def test_open_meteo_gets_the_areas_lat_lon_on_fallback(self):
        self._net(pick_wttr=False)
        picked = aura.fetch()["picked"]
        self.assertNotIn("failed", picked)
        om = [params for url, params in self.calls if "open-meteo" in url]
        self.assertEqual(len(om), 1)
        self.assertEqual((om[0]["latitude"], om[0]["longitude"]), _AT)
        self.assertEqual(picked["location"], _PICK_QUERY)

    def test_a_picked_failure_never_raises_and_home_is_intact(self):
        self._net(pick_wttr=False, pick_om=False)
        with self.assertLogs("METIS.aura", "WARNING"):
            data = aura.fetch()
        self.assertEqual(data["picked"],
                         {"area": self.area, "name": "Observatory", "failed": True})
        self.assertIn("temp_f", data)

    def test_a_home_failure_still_raises_and_skips_the_pick(self):
        self._net(home_wttr=False, home_om=False)
        with self.assertLogs("METIS.aura", "WARNING"):
            with self.assertRaises(ConnectionError):
                aura.fetch()
        self.assertFalse(any(_PICK_QUERY in url for url, _ in self.calls))

    def test_a_recycled_area_is_missing_and_costs_no_request(self):
        shutil.rmtree(artemis.area_dir(self.area))
        self._net()
        data = aura.fetch()
        self.assertEqual(data["picked"], {"area": self.area, "missing": True})
        self.assertEqual(len(self.calls), 1)

    def test_the_pick_never_enters_handles_cache(self):
        """Pythia's get_weather stays Home: after a poll that fetched a pick,
        handle() answers Home from the cache with no request and no "picked"."""
        self._net()
        aura.fetch()
        before = len(self.calls)
        out = aura.handle()
        self.assertEqual(len(self.calls), before)
        self.assertNotIn("picked", out)
        self.assertNotEqual(out["temp_f"], 99)


if __name__ == "__main__":
    unittest.main(verbosity=2)
