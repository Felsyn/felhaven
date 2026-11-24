"""
test_eos.py — space weather: NOAA's storm scales, Kp, and the aurora's reach.

Hermetic: tools.eos.hermes.get is patched with canned bodies shaped like
SWPC's (captured 2026-11-10, trimmed), Themis reads a scratch settings file,
and "now" is passed in, so no network, no real place and no clock.

    python -X utf8 -m unittest tests.test_eos
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import themis
from tools import eos, hermes

NOW = datetime(2026, 11, 10, 12, 20, tzinfo=timezone.utc)


def _scales(g_now="0", g_days=("0", "1", "0")):
    def day(date, g):
        return {"DateStamp": date, "TimeStamp": "00:00:00",
                "R": {"Scale": None, "Text": None, "MinorProb": "60", "MajorProb": "15"},
                "S": {"Scale": None, "Text": None, "Prob": "15"},
                "G": {"Scale": g, "Text": "minor" if g == "1" else "none"}}
    return {"-1": {"DateStamp": "2026-11-09", "TimeStamp": "10:56:00",
                   "R": {"Scale": "0", "Text": "none"}, "S": {"Scale": "0", "Text": "none"},
                   "G": {"Scale": "0", "Text": "none"}},
            "0": {"DateStamp": "2026-11-10", "TimeStamp": "12:18:00",
                  "R": {"Scale": "0", "Text": "none", "MinorProb": None, "MajorProb": None},
                  "S": {"Scale": "0", "Text": "none", "Prob": None},
                  "G": {"Scale": g_now, "Text": "minor" if g_now == "1" else "none"}},
            "1": day("2026-11-10", g_days[0]), "2": day("2026-11-11", g_days[1]),
            "3": day("2026-11-12", g_days[2])}


def _kp(rows=None):
    rows = rows or [("2026-11-10T06:00:00", 2.33, "observed"),
                    ("2026-11-10T09:00:00", 3.0, "observed"),
                    ("2026-11-10T12:00:00", 2.33, "estimated"),
                    ("2026-11-10T15:00:00", 2.0, "estimated"),
                    ("2026-11-11T03:00:00", 5.33, "predicted"),
                    ("2026-11-12T00:00:00", 1.67, "predicted")]
    return [{"time_tag": t, "kp": k, "observed": o, "noaa_scale": None} for t, k, o in rows]


class _Reply:
    def __init__(self, body, status=200):
        self.status_code = status
        self.content = json.dumps(body).encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise hermes.HTTPStatus(self.status_code)


class _EosCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.object(themis, "_DATA_PATH", Path(tmp.name) / "s.json")
        patch.start()
        self.addCleanup(patch.stop)
        themis.save(latitude=39.0997, longitude=-94.5786)       # a generic Kansas City point
        self.scales, self.kp = _scales(), _kp()
        get = mock.patch.object(eos.hermes, "get", side_effect=self._get)
        self.get = get.start()
        self.addCleanup(get.stop)

    def _get(self, url, **kw):
        return _Reply(self.scales if url == eos.SCALES_URL else self.kp)


class TestTheRequests(_EosCase):

    def test_asks_only_the_two_swpc_products_bounded(self):
        eos.fetch(NOW)
        urls = [c.args[0] for c in self.get.call_args_list]
        self.assertEqual(sorted(urls), sorted([eos.SCALES_URL, eos.KP_URL]))
        for call in self.get.call_args_list:
            self.assertEqual(call.kwargs["cap"], 64 * 1024)

    def test_the_place_never_leaves_the_pc(self):
        eos.fetch(NOW)
        for call in self.get.call_args_list:
            self.assertNotIn("39.", repr(call))
            self.assertNotIn("94.", repr(call))

    def test_a_failure_raises_so_kairos_delivers_none(self):
        self.get.side_effect = hermes.Offline("down")
        with self.assertRaises(hermes.Offline):
            eos.fetch(NOW)

    def test_an_http_error_raises(self):
        self.get.side_effect = lambda url, **kw: _Reply({}, status=503)
        with self.assertRaises(hermes.HTTPStatus):
            eos.fetch(NOW)

    def test_a_reshaped_feed_raises(self):
        self.kp = [["2026-11-10 06:00:00.000", "2.33", "observed"]]   # the old list-of-lists
        with self.assertRaises(eos.FeedError):
            eos.fetch(NOW)
        self.kp, self.scales = _kp(), {"title": "something else"}
        with self.assertRaises(eos.FeedError):
            eos.fetch(NOW)


class TestTheReadings(_EosCase):

    def test_now_carries_each_scale(self):
        self.scales = _scales(g_now="1")
        now = eos.fetch(NOW)["now"]
        self.assertEqual(now["R"], {"level": 0, "text": "none"})
        self.assertEqual(now["G"], {"level": 1, "text": "minor"})

    def test_carries_only_what_a_display_reads(self):
        """The Contract's fields, no more: a display that wants another adds
        it back (the owner's call, as for Iris)."""
        d = eos.fetch(NOW)
        self.assertEqual(set(d), {"now", "kp", "days", "aurora"})
        self.assertEqual({k for day in d["days"] for k in day}, {"date", "r_minor", "g"})
        self.assertEqual(set(d["aurora"]), {"mlat", "kp_needed", "g_needed", "now",
                                            "next_24h", "kp_max_24h"})

    def test_kp_is_the_bin_holding_now_whatever_its_kind(self):
        """12:20 sits in the estimated 12:00 bin: not the last observed one,
        not the next, and the feed's order doesn't matter."""
        self.kp = _kp([("2026-11-10T12:00:00", 2.33, "estimated"),
                       ("2026-11-10T15:00:00", 4.0, "estimated"),
                       ("2026-11-10T09:00:00", 3.0, "observed")])
        self.assertEqual(eos.fetch(NOW)["kp"], 2.33)

    def test_days_carry_the_flare_chance_and_g(self):
        days = eos.fetch(NOW)["days"]
        self.assertEqual([d["date"] for d in days], ["2026-11-10", "2026-11-11", "2026-11-12"])
        self.assertEqual([d["r_minor"] for d in days], [60, 60, 60])
        self.assertEqual([d["g"] for d in days], [0, 1, 0])


class TestTheAuroraRule(_EosCase):

    def test_geomagnetic_latitude(self):
        self.assertAlmostEqual(eos.geomagnetic_latitude(39.0997, -94.5786), 47.5, delta=0.2)
        self.assertAlmostEqual(eos.geomagnetic_latitude(80.85, -72.76), 90.0, delta=0.01)
        self.assertAlmostEqual(eos.geomagnetic_latitude(-80.85, 107.24), -90.0, delta=0.01)

    def test_kp_needed_follows_swpcs_two_degrees_per_kp_with_a_four_degree_horizon(self):
        cases = {62.0: 0, 66.0: 0, 61.0: 1, 54.0: 4, 52.0: 5, 47.5: 8, 44.0: 9, 43.9: 10}
        for mlat, kp in cases.items():
            with self.subTest(mlat=mlat):
                self.assertEqual(eos.kp_needed(mlat), kp)
                self.assertEqual(eos.kp_needed(-mlat), kp)        # the south reads the same

    def test_g_needed(self):
        self.assertEqual([eos.g_needed(k) for k in (4, 5, 7, 9)], [None, 1, 3, 5])

    def test_kansas_city_needs_kp_8_and_it_is_quiet(self):
        a = eos.fetch(NOW)["aurora"]
        self.assertEqual((a["mlat"], a["kp_needed"], a["g_needed"]), (47.5, 8, 4))
        self.assertEqual((a["now"], a["next_24h"]), (False, False))

    def test_next_24h_looks_ahead_and_now_does_not(self):
        themis.save(latitude=51.4779, longitude=0.0)          # Greenwich, 53.3°: Kp 5
        a = eos.fetch(NOW)["aurora"]
        self.assertEqual(a["kp_needed"], 5)
        self.assertEqual((a["now"], a["next_24h"], a["kp_max_24h"]), (False, True, 5.33))

    def test_out_of_reach_is_never_possible(self):
        themis.save(latitude=25.76, longitude=-80.19)         # far from the pole
        self.kp = _kp([("2026-11-10T12:00:00", 9.0, "observed")])
        a = eos.fetch(NOW)["aurora"]
        self.assertGreater(a["kp_needed"], 9)
        self.assertIsNone(a["g_needed"])
        self.assertEqual((a["now"], a["next_24h"]), (False, False))


if __name__ == "__main__":
    unittest.main()
