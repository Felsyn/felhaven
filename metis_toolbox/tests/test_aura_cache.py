"""
test_aura_cache.py — Aura's freshness cache (V0.03 audit T7 / M2).

Kairos already polls Aura every 30 min, yet each weather, sun and moon question
used to fetch again: "weather, sunset and moon?" was three round trips, and
offline each one walked BOTH sources before failing. These tests pin the fix by
counting calls at the real I/O seam (hermes.get):

  - one turn of get_weather + get_sun_times + get_moon_phase is ONE fetch,
    online or off; since V0.03 audit M3 the sun and moon make none at all,
    and answer offline
  - handle() answers from the last good fetch while it is younger than
    CACHE_TTL, keyed on the inputs that shape it (never the old city's weather)
  - a failed refresh serves the last reading marked `stale`, with its original
    `as_of`, for up to _STALE_MAX — and does not retry inside _RETRY_AFTER
  - fetch() (Kairos) always goes to the network, still raises, and never hands
    a panel the cache's own dict (§6)
  - a handle() arriving during a Kairos fetch waits for it instead of fetching
    a second time

    python -X utf8 -m unittest tests.test_aura_cache
"""

import os
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from tools import aura, hermes
from tests._fixtures import load_real_j1, load_real_open_meteo
from tests._loopback import Loopback, drip, read_head
import pythia

_T0 = 1000.0                          # arbitrary monotonic origin, patched clock
_WALL_T0 = datetime(2026, 9, 28, 9, 0)  # the wall clock at _T0


class _FakeResp:
    """Minimal stand-in for a hermes Reply (only what Aura reads)."""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _Net:
    """A counting hermes.get stand-in with a switch.

    `calls` records every URL asked for, so a test counts real fetch attempts
    rather than handle() calls. up()/down() flip both sources at once — the
    offline laptop, not one flaky host.
    """

    def __init__(self):
        self.calls = []
        self.up()

    def up(self):
        self.wttr, self.open_meteo = load_real_j1(), load_real_open_meteo()

    def down(self):
        self.wttr = self.open_meteo = hermes.Offline()

    def __call__(self, url, *args, **kwargs):
        self.calls.append(url)
        target = self.open_meteo if "open-meteo" in url else self.wttr
        if isinstance(target, Exception):
            raise target
        return _FakeResp(target)


class _CacheCase(unittest.TestCase):
    """Starts every test with an empty cache and leaves one behind, so no
    reading leaks into a later test (or a later test module)."""

    def setUp(self):
        aura._clear_cache()
        self.addCleanup(aura._clear_cache)
        self.net = _Net()
        patcher = mock.patch("tools.aura.hermes.get", new=self.net)
        patcher.start()
        self.addCleanup(patcher.stop)


class _ClockedCase(_CacheCase):
    """Adds patched clocks, so a test can age the cache without sleeping: the
    monotonic one (the kairos test pattern) that drives the TTL, and the wall
    clock behind `as_of`, moved in step so a re-dated answer would show."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch("tools.aura.time")
        self.clock = patcher.start()
        self.addCleanup(patcher.stop)
        self.clock.monotonic.return_value = _T0
        patcher = mock.patch("tools.aura._now", side_effect=lambda: _WALL_T0
                             + timedelta(seconds=self.clock.monotonic() - _T0))
        patcher.start()
        self.addCleanup(patcher.stop)

    def at(self, t):
        self.clock.monotonic.return_value = t


# ── The finding itself: one turn, one fetch ───────────────────────────────────

class TestOneFetchPerTurn(_CacheCase):

    _TURN = ("get_weather", "get_sun_times", "get_moon_phase")

    def _turn(self):
        # Through the real dispatcher, the way ask() runs a turn's tool calls.
        return [pythia._dispatch(name, {}) for name in self._TURN]

    def test_weather_sun_and_moon_make_one_fetch(self):
        results = self._turn()
        for name, out in zip(self._TURN, results):
            self.assertNotIn("error", out, msg=name)
        self.assertEqual(len(self.net.calls), 1)

    def test_sun_and_moon_make_no_fetch_of_their_own(self):
        # Computed from the Settings and the clock since V0.03 audit M3, so
        # only the weather is a reading, and only it carries `as_of`.
        for name in ("get_sun_times", "get_moon_phase"):
            out = pythia._dispatch(name, {})
            self.assertNotIn("error", out, msg=name)
            self.assertNotIn("as_of", out, msg=name)
        self.assertEqual(self.net.calls, [])

    def test_offline_the_turn_makes_one_attempt(self):
        # One attempt is wttr.in then Open-Meteo: 2 GETs. Before the fix a
        # turn made 6, each call walking both sources on its own. The sun and
        # moon answer regardless: they never needed the weather (M3).
        self.net.down()
        results = self._turn()
        self.assertEqual([out.get("error") for out in results],
                         ["weather_offline", None, None])
        self.assertEqual(len(self.net.calls), 2)

    def test_after_a_kairos_poll_the_turn_makes_no_fetch(self):
        aura.fetch()
        self._turn()
        self.assertEqual(len(self.net.calls), 1)     # the poll's, nothing more


# ── Freshness ─────────────────────────────────────────────────────────────────

class TestTTL(_ClockedCase):

    def test_a_reading_inside_the_ttl_is_reused(self):
        aura.handle()
        self.at(_T0 + aura.CACHE_TTL - 1)
        aura.handle()
        self.assertEqual(len(self.net.calls), 1)

    def test_a_reading_at_the_ttl_is_refetched(self):
        aura.handle()
        self.at(_T0 + aura.CACHE_TTL)
        aura.handle()
        self.assertEqual(len(self.net.calls), 2)

    def test_a_fresh_answer_is_not_marked_stale(self):
        # Both fresh paths: the answer that fetched, and the one the cache served.
        self.assertNotIn("stale", aura.handle())
        self.at(_T0 + 1)
        self.assertNotIn("stale", aura.handle())
        self.assertEqual(len(self.net.calls), 1)


class TestAsOf(_CacheCase):

    def test_as_of_is_naive_local_iso(self):
        # pythia._now()'s convention: naive, local, the clock on the wall.
        before = datetime.now().replace(microsecond=0)
        stamp = datetime.fromisoformat(aura.handle()["as_of"])
        self.assertIsNone(stamp.tzinfo)
        self.assertGreaterEqual(stamp, before)
        self.assertLessEqual(stamp, datetime.now())

    def test_errors_carry_no_as_of(self):
        # Nothing was fetched, so there is nothing to date.
        self.net.down()
        self.assertNotIn("as_of", aura.handle())


# ── The key: what shapes a reading ────────────────────────────────────────────

class TestKey(_CacheCase):

    def test_a_location_change_refetches(self):
        with mock.patch("tools.aura._location", return_value="Pittsburgh"):
            aura.handle()
        with mock.patch("tools.aura._location", return_value="Ely"):
            aura.handle()
        self.assertEqual(len(self.net.calls), 2)

    def test_a_coordinate_change_refetches(self):
        # Open-Meteo, the fallback, is asked by lat/lon, not by name.
        aura.handle()
        with mock.patch("tools.aura.themis.latitude", return_value=-33.9):
            aura.handle()
        self.assertEqual(len(self.net.calls), 2)

    def test_a_failed_refresh_never_serves_the_old_city(self):
        # Stale-on-error must not cross a key: Pittsburgh's weather labelled
        # "stale" is still the wrong answer for Ely.
        with mock.patch("tools.aura._location", return_value="Pittsburgh"):
            aura.handle()
        self.net.down()
        with mock.patch("tools.aura._location", return_value="Ely"):
            out = aura.handle()
        self.assertEqual(out.get("error"), "weather_offline")
        self.assertNotIn("temp_f", out)


# ── When the refresh fails ────────────────────────────────────────────────────

class TestStaleOnError(_ClockedCase):

    def test_a_failed_refresh_serves_the_last_reading_marked_stale(self):
        first = aura.handle()
        self.at(_T0 + aura.CACHE_TTL + 1)
        self.net.down()
        out = aura.handle()
        self.assertNotIn("error", out)
        self.assertIs(out.get("stale"), True)
        self.assertEqual(out["temp_f"], first["temp_f"])
        # as_of says when the data was FETCHED; the failed attempt must not
        # move it, or the label would lie about the age it exists to report.
        self.assertEqual(out["as_of"], first["as_of"])

    def test_a_reading_past_stale_max_is_not_served(self):
        aura.handle()
        self.at(_T0 + aura._STALE_MAX)
        self.net.down()
        self.assertEqual(aura.handle().get("error"), "weather_offline")

    def test_a_recent_failure_is_not_retried(self):
        self.net.down()
        aura.handle()
        self.at(_T0 + aura._RETRY_AFTER - 1)
        self.assertEqual(aura.handle().get("error"), "weather_offline")
        self.assertEqual(len(self.net.calls), 2)     # still just the one attempt

    def test_a_failure_is_retried_once_the_window_passes(self):
        self.net.down()
        aura.handle()
        self.at(_T0 + aura._RETRY_AFTER)
        self.net.up()
        self.assertNotIn("error", aura.handle())


# ── The Kairos path ───────────────────────────────────────────────────────────

class TestFetch(_ClockedCase):

    def test_fetch_goes_to_the_network_even_when_the_cache_is_fresh(self):
        # fetch() IS the refresh. If it answered from the cache, a failed poll
        # would stop raising and Kairos's retry ladder would never engage.
        aura.handle()
        aura.fetch()
        self.assertEqual(len(self.net.calls), 2)

    def test_a_failed_fetch_raises_and_keeps_the_last_reading(self):
        aura.fetch()
        self.net.down()
        with self.assertRaises(hermes.Offline):
            aura.fetch()
        self.at(_T0 + 5)
        out = aura.handle()
        self.assertNotIn("error", out)
        self.assertNotIn("stale", out)               # still inside the TTL

    def test_fetch_output_is_unchanged_for_the_panels(self):
        self.assertNotIn("as_of", aura.fetch())

    def test_the_cache_never_shares_its_dict(self):
        # §6: the result queue is the only object shared with the main thread.
        # A panel gets fetch()'s dict; if the cache held that same object, a
        # panel and Pythia's thread would share one mutable dict.
        out = aura.fetch()
        out["temp_f"] = -999
        out["astronomy"]["sunrise"] = "tampered"
        answer = aura.handle()
        self.assertNotEqual(answer["temp_f"], -999)
        self.assertNotEqual(answer["astronomy"]["sunrise"], "tampered")
        answer["astronomy"]["sunset"] = "tampered"
        self.assertNotEqual(aura.handle()["astronomy"]["sunset"], "tampered")


# ── Two threads, one fetch ────────────────────────────────────────────────────

class TestSingleFlight(unittest.TestCase):
    """Kairos's worker and Pythia's worker are separate threads. A handle()
    that arrives while a Kairos fetch is in flight must wait for that fetch,
    not start a second one."""

    def setUp(self):
        aura._clear_cache()
        self.addCleanup(aura._clear_cache)

    def test_a_call_during_a_kairos_fetch_waits_for_it(self):
        calls = []
        first_inside = threading.Event()
        release = threading.Event()
        second_inside = threading.Event()

        def slow_get(url, *args, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                first_inside.set()
                release.wait(5)
            else:
                second_inside.set()
            return _FakeResp(load_real_j1())

        answer = {}
        with mock.patch("tools.aura.hermes.get", new=slow_get):
            kairos = threading.Thread(target=aura.fetch, daemon=True)
            kairos.start()
            self.assertTrue(first_inside.wait(5), "fetch() never reached the network")
            pythia_side = threading.Thread(
                target=lambda: answer.update(out=aura.handle()), daemon=True)
            pythia_side.start()
            # Bounded, so a regression fails in half a second rather than hangs.
            fetched_twice = second_inside.wait(0.5)
            release.set()
            kairos.join(5)
            pythia_side.join(5)

        self.assertFalse(fetched_twice, "handle() fetched while fetch() was in flight")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("error", answer["out"])


# ── A dripping server can't hold the lock ─────────────────────────────────────

class TestTheLockIsHeldOnlyUntilTheDeadline(unittest.TestCase):
    """The single flight above is safe only because the fetch is bounded. Both
    sources here are a real loopback server that sends its status line, then
    a header one byte every 0.1 s. A 0.3 s per-operation timeout never trips
    on that; only hermes' deadline ends it. So handle() answers, and the lock
    is free again, within two deadlines (one per source).

    Plain http, because a TLS server here would need a certificate; the test
    lets hermes take http for its duration. (A dripped TLS handshake would
    prove less: CPython already bounds a handshake by one socket timeout.)"""

    def setUp(self):
        aura._clear_cache()
        self.addCleanup(aura._clear_cache)

    def test_handle_answers_and_releases_the_lock_at_the_deadline(self):
        def script(conn, stop):
            read_head(conn)
            conn.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
            drip(conn, stop, b"x" * 40, 0.1)

        real_get = hermes.get
        with Loopback(script) as srv:
            base = f"http://127.0.0.1:{srv.port}"
            # timeout 0.3 s and 64 KiB caps make each deadline_for() 0.66 s.
            with mock.patch.object(aura, "_WTTR_URL", base + "/{location}"), \
                    mock.patch.object(aura, "_OPEN_METEO_URL", base + "/om"), \
                    mock.patch.object(aura, "_REQUEST_TIMEOUT", 0.3), \
                    mock.patch.object(aura, "_WTTR_MAX_BYTES", 65536), \
                    mock.patch.object(aura, "_OPEN_METEO_MAX_BYTES", 65536), \
                    mock.patch.object(aura.hermes, "get",
                                      lambda *a, **k: real_get(*a, allow_http=True, **k)), \
                    self.assertLogs("METIS.hermes", "WARNING") as logs:
                t0 = time.monotonic()
                out = aura.handle()
                elapsed = time.monotonic() - t0
                free = aura._lock.acquire(blocking=False)
                if free:
                    aura._lock.release()

        self.assertEqual(out.get("error"), "weather_timeout")
        self.assertEqual(sum("deadline" in line for line in logs.output), 2,
                         "both sources should end at hermes' deadline")
        self.assertLess(elapsed, 2 * 0.66 + 1.5)   # two deadlines, CI slack
        self.assertTrue(free, "the cache lock was still held after handle() returned")


if __name__ == "__main__":
    unittest.main(verbosity=2)
