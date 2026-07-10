"""
test_tool_handles.py — unit tests for the LLM handle()s added to
argus, helios, hypatia, and selene.

Each handle degrades gracefully (returns an {"error": ...} dict, never raises)
and, on the happy path, shapes a compact answer. Data sources are mocked, so
no network (Aura), no psutil, and no star catalog are touched. Run from the
package root:
    python -X utf8 -m unittest tests.test_tool_handles
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import argus, helios, hypatia, selene, morpheus
import pythia


class TestHeliosHandle(unittest.TestCase):
    def test_success_from_aura_astronomy(self):
        with mock.patch("tools.aura.handle", return_value={
            "astronomy": {"sunrise": "05:52 AM", "sunset": "08:45 PM"}
        }):
            out = helios.handle()
        self.assertNotIn("error", out)
        self.assertEqual(out["sunrise"], "5:52 AM")
        self.assertIn("day_length", out)

    def test_aura_error_degrades(self):
        with mock.patch("tools.aura.handle", return_value={"error": "weather_offline"}):
            self.assertEqual(helios.handle(), {"error": "sun_times_unavailable"})


class TestSeleneHandle(unittest.TestCase):
    def test_success_from_aura_astronomy(self):
        with mock.patch("tools.aura.handle", return_value={
            "astronomy": {"moon_phase": "Full Moon", "moon_illumination": "100",
                          "moonrise": "08:45 PM", "moonset": "05:52 AM"}
        }):
            out = selene.handle()
        self.assertEqual(out["phase"], "Full Moon")
        self.assertEqual(out["illumination"], "100%")

    def test_aura_error_degrades(self):
        with mock.patch("tools.aura.handle", return_value={"error": "weather_offline"}):
            self.assertEqual(selene.handle(), {"error": "moon_unavailable"})


class TestArgusHandle(unittest.TestCase):
    _SNAP = {
        "privilege": "user",
        "summary": {"established": 3, "listening": 5, "other": 1, "unresolved_pids": 0},
        "traffic": {"up_bps": 100, "down_bps": 2000, "window_s": 5, "per_nic": {}},
        "firewall": {"state": "on", "domain": "on", "private": "on", "public": "on"},
        "dns": {"state": "ok", "entries": ["a.com", "b.com"]},
        "connections": [{"proc": "chrome", "raddr": "1.2.3.4:443", "status": "ESTABLISHED"}],
        "listening": [], "timeline": [], "as_of": 0,
    }

    def test_success_summary(self):
        with mock.patch("tools.argus.fetch", return_value=self._SNAP):
            out = argus.handle()
        self.assertEqual(out["established"], 3)
        self.assertEqual(out["dns_cached_names"], 2)
        self.assertEqual(out["firewall"]["public"], "on")
        self.assertEqual(out["top_connections"][0]["remote"], "1.2.3.4:443")

    def test_fetch_failure_degrades(self):
        with mock.patch("tools.argus.fetch", side_effect=OSError("no sockets")):
            self.assertEqual(argus.handle(), {"error": "network_unavailable"})


class TestHypatiaHandle(unittest.TestCase):
    _SNAP = {
        "generated_unix": 0, "lst_deg": 0.0, "preset": "current",
        "stars": {
            1: {"name": "Vega", "mag": 0.03, "alt": 45.0, "az": 90.0},   # up
            2: {"name": None,   "mag": 6.0,  "alt": -5.0, "az": 10.0},   # below
        },
        "constellations": [],
        "planets": [
            {"name": "Mars",  "glyph": "♂", "alt": 20.0, "az": 135.0},  # up
            {"name": "Venus", "glyph": "♀", "alt": -3.0, "az": 300.0},  # below
        ],
    }

    def test_success_digest(self):
        with mock.patch("tools.hypatia._build_snapshot", return_value=self._SNAP):
            out = hypatia.handle()
        self.assertEqual(out["stars_above_horizon"], 1)
        self.assertEqual([p["name"] for p in out["planets_visible"]], ["Mars"])
        self.assertEqual(out["brightest_stars"][0]["name"], "Vega")
        self.assertEqual(out["brightest_stars"][0]["direction"], "E")   # az 90

    def test_empty_catalog_degrades(self):
        with mock.patch("tools.hypatia._build_snapshot",
                        side_effect=RuntimeError("empty catalog")):
            self.assertEqual(hypatia.handle(), {"error": "sky_unavailable"})


class TestMorpheusHandle(unittest.TestCase):
    _AVAIL = {"mpv": "mpv.exe", "ytdlp": "yt-dlp.exe"}
    _HIT = [{"title": "Clair de Lune", "channel": "Debussy",
             "duration": 300, "url": "https://youtu.be/abc"}]

    def test_play_success(self):
        with mock.patch("tools.morpheus.available", return_value=self._AVAIL), \
                mock.patch("tools.morpheus.search", return_value=self._HIT), \
                mock.patch("tools.morpheus.play") as play:
            out = morpheus.handle(query="clair de lune")
        self.assertEqual(out["now_playing"], "Clair de Lune")
        play.assert_called_once_with("https://youtu.be/abc")   # actually played the hit

    def test_empty_query_degrades(self):
        self.assertEqual(morpheus.handle("")["error"], "no_query")

    def test_missing_binaries_degrades(self):
        with mock.patch("tools.morpheus.available",
                        return_value={"mpv": None, "ytdlp": None}):
            self.assertEqual(morpheus.handle("anything")["error"], "player_unavailable")

    def test_no_results_degrades(self):
        with mock.patch("tools.morpheus.available", return_value=self._AVAIL), \
                mock.patch("tools.morpheus.search",
                           return_value=[{"error": "search failed"}]), \
                mock.patch("tools.morpheus.play") as play:
            self.assertEqual(morpheus.handle("obscure")["error"], "no_results")
            play.assert_not_called()               # never play when search fails


class TestNewToolsRegisteredWithPythia(unittest.TestCase):
    def test_new_handles_reach_pythia(self):
        for name in ("get_network_summary", "get_sun_times", "get_sky_tonight",
                     "get_moon_phase", "play_music", "search_web", "fetch_page"):
            self.assertIn(name, pythia._DISPATCH)

    def test_tools_and_dispatch_stay_in_lockstep(self):
        # TOOLS (schemas sent to the model) and _DISPATCH (name -> handler) are
        # built together at import, so every tool definition must have exactly
        # one matching handler and vice versa. This is the real invariant the
        # old hardcoded tool count was standing in for — but it can't rot when
        # a new tool is added, and it still catches a definition with no handler
        # (or a name typo between the two).
        tool_names = [d["function"]["name"] for d in pythia.TOOLS]
        self.assertEqual(sorted(tool_names), sorted(pythia._DISPATCH))


if __name__ == "__main__":
    unittest.main(verbosity=2)
