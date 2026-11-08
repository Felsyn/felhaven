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
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from tools import argus, aura, helios, hypatia, selene, morpheus
import pythia


class _ComputedCase(unittest.TestCase):
    """Sun and moon are computed from the Settings location and the clock (V0.03
    audit M3), so these patch Themis and the module's clock seam, and cut the
    network. Aura's cache starts empty: before M3 both tools read the weather
    through aura.handle(), and a warm cache would have hidden that they needed
    the network at all."""

    module = "tools.helios"

    def setUp(self):
        aura._clear_cache()
        self.addCleanup(aura._clear_cache)
        self.net = mock.Mock(side_effect=requests.ConnectionError())
        self.place(51.4779, 0.0)                      # Greenwich, Themis's default
        self.at(datetime(2026, 9, 28, 12, tzinfo=timezone.utc))
        self.clock(twenty_four=False)                 # Themis's default
        for patcher in (mock.patch("requests.get", self.net),
                        mock.patch("themis.latitude", lambda: self._lat),
                        mock.patch("themis.longitude", lambda: self._lon),
                        mock.patch("themis.clock_24h", lambda: self._24h),
                        mock.patch(f"{self.module}._now", lambda: self._when,
                                   create=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def place(self, lat, lon):
        self._lat, self._lon = lat, lon

    def at(self, when):
        self._when = when

    def clock(self, *, twenty_four):
        self._24h = twenty_four


class TestHeliosHandle(_ComputedCase):

    def test_answers_offline_without_touching_the_network(self):
        # The finding itself: no network used to mean no sunset.
        out = helios.handle()
        self.assertNotIn("error", out)
        self.net.assert_not_called()

    def test_answers_for_the_settings_location_on_todays_date(self):
        out = helios.handle()
        self.assertEqual(out["sunrise"], "5:56 AM")   # USNO: 05:56 UT (test_helios)
        self.assertEqual(out["sunset"], "5:45 PM")
        self.assertEqual(out, helios.compute(51.4779, 0.0, self._when))

    def test_follows_the_settings_clock(self):
        self.clock(twenty_four=True)
        out = helios.handle()
        self.assertEqual((out["sunrise"], out["sunset"]), ("05:56", "17:45"))
        self.assertEqual(out, helios.compute(51.4779, 0.0, self._when, clock_24h=True))

    def test_carries_no_as_of(self):
        # as_of dates a fetched reading (T7). This was computed during the
        # call, so it is exactly as old as the answer, and there is no
        # staleness to flag.
        out = helios.handle()
        self.assertNotIn("as_of", out)
        self.assertNotIn("stale", out)

    def test_polar_night_says_so(self):
        self.place(69.6492, 18.9553)                  # Tromsø
        self.at(datetime(2026, 12, 15, 12, tzinfo=timezone(timedelta(hours=1))))
        self.assertEqual(helios.handle(),
                         {"error": "sun_times_unavailable: the sun does not rise today"})

    def test_a_math_failure_degrades_to_the_slug_and_is_logged(self):
        with mock.patch("tools.helios.compute", side_effect=ZeroDivisionError()), \
                self.assertLogs("METIS.helios", "ERROR"):
            self.assertEqual(helios.handle(),
                             {"error": "sun_times_unavailable: ZeroDivisionError"})

    def test_the_panel_polls_the_same_answer(self):
        # Kairos's helios worker calls fetch(); the model calls handle(). One
        # function, so the Helios panel and get_sun_times cannot disagree.
        self.assertIs(helios.fetch, helios.handle)


class TestSeleneHandle(_ComputedCase):

    module = "tools.selene"

    def test_answers_offline_without_touching_the_network(self):
        # The finding itself: no network used to mean no moon, and Aura's
        # Open-Meteo fallback never carried one.
        out = selene.handle()
        self.assertNotIn("error", out)
        self.net.assert_not_called()

    def test_answers_for_the_settings_location_now(self):
        out = selene.handle()
        # USNO, Greenwich 2026-09-28 (test_selene): Waning Gibbous, 96% at noon.
        self.assertEqual(out["phase"], "Waning Gibbous")
        self.assertEqual(out["illumination"], "96%")
        self.assertEqual(out, selene.compute(51.4779, 0.0, self._when))

    def test_follows_the_settings_clock(self):
        self.clock(twenty_four=True)
        out = selene.handle()
        self.assertEqual(out, selene.compute(51.4779, 0.0, self._when, clock_24h=True))
        self.assertNotEqual(out, selene.compute(51.4779, 0.0, self._when))

    def test_carries_no_as_of(self):
        out = selene.handle()
        self.assertNotIn("as_of", out)
        self.assertNotIn("stale", out)

    def test_a_math_failure_degrades_to_the_slug_and_is_logged(self):
        with mock.patch("tools.selene.compute", side_effect=ZeroDivisionError()), \
                self.assertLogs("METIS.selene", "ERROR"):
            self.assertEqual(selene.handle(),
                             {"error": "moon_unavailable: ZeroDivisionError"})

    def test_the_panel_polls_the_same_answer(self):
        self.assertIs(selene.fetch, selene.handle)


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
            out = morpheus.play_music(query="clair de lune")
        self.assertEqual(out["now_playing"], "Clair de Lune")
        play.assert_called_once_with("https://youtu.be/abc")   # actually played the hit

    def test_empty_query_degrades(self):
        self.assertEqual(morpheus.play_music("")["error"], "no_query")

    def test_missing_binaries_degrades(self):
        with mock.patch("tools.morpheus.available",
                        return_value={"mpv": None, "ytdlp": None}):
            self.assertEqual(morpheus.play_music("anything")["error"], "player_unavailable")

    def test_no_results_degrades(self):
        with mock.patch("tools.morpheus.available", return_value=self._AVAIL), \
                mock.patch("tools.morpheus.search",
                           return_value=[{"error": "search_failed"}]), \
                mock.patch("tools.morpheus.play") as play:
            self.assertEqual(morpheus.play_music("obscure")["error"], "no_results")
            play.assert_not_called()               # never play when search fails


class TestMorpheusReplay(unittest.TestCase):
    """The old resume_music, now control_music(action="replay").
    The state machine's own coverage is tests/test_morpheus_control.py."""

    _AVAIL = {"mpv": "mpv.exe", "ytdlp": "yt-dlp.exe"}

    def setUp(self):
        self._saved_last_url = morpheus._last_url

    def tearDown(self):
        morpheus._last_url = self._saved_last_url

    def test_nothing_played_yet_degrades(self):
        morpheus._last_url = None
        self.assertEqual(morpheus.control_music("replay")["error"],
                         "nothing_to_resume")

    def test_missing_mpv_degrades(self):
        morpheus._last_url = "https://youtu.be/abc"
        with mock.patch("tools.morpheus.available",
                        return_value={"mpv": None, "ytdlp": "yt-dlp.exe"}):
            self.assertEqual(morpheus.control_music("replay")["error"],
                             "player_unavailable")

    def test_resumes_last_played_url(self):
        morpheus._last_url = "https://youtu.be/abc"
        with mock.patch("tools.morpheus.available", return_value=self._AVAIL), \
                mock.patch("tools.morpheus.status", return_value={"running": True}), \
                mock.patch("tools.morpheus.play") as play:
            out = morpheus.control_music("replay")
        self.assertEqual(out["action"], "replay")
        play.assert_called_once_with("https://youtu.be/abc")

    def test_play_tracks_last_url(self):
        morpheus._last_url = None
        with mock.patch("tools.morpheus._ensure_mpv", return_value=True), \
                mock.patch("tools.morpheus._checkpoint"), \
                mock.patch("tools.morpheus._ipc"):
            morpheus.play("https://youtu.be/xyz")
        self.assertEqual(morpheus._last_url, "https://youtu.be/xyz")

    def test_play_does_not_track_url_when_mpv_unavailable(self):
        morpheus._last_url = "https://youtu.be/old"
        with mock.patch("tools.morpheus._ensure_mpv", return_value=False):
            morpheus.play("https://youtu.be/new")
        self.assertEqual(morpheus._last_url, "https://youtu.be/old")


class TestNewToolsRegisteredWithPythia(unittest.TestCase):
    def test_new_handles_reach_pythia(self):
        for name in ("get_network_summary", "get_sun_times", "get_sky_tonight",
                     "get_moon_phase", "play_music", "control_music",
                     "search_web", "fetch_page"):
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
