"""
test_aura_fallback.py — Aura's second weather source (Open-Meteo).

wttr.in is one free host with no SLA. This pins the fallback that keeps the
Atmospherics panel alive when it goes down, and the one rule that makes the
fallback safe: it must normalize into the SAME dict _build() returns, because
that dict is the panel's contract and no display code may learn which host
answered.

Covered here:
  - the failover decision itself (primary wins, secondary rescues, both-fail
    re-raises the PRIMARY error)
  - shape parity between the two sources
  - the two places the dialects genuinely differ: WMO->WWO codes, and the
    missing moon block

    python -X utf8 -m unittest discover -s tests -p "test_aura_fallback.py"
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from tools import aura, hermes
from tests._fixtures import (load_real_j1, load_real_open_meteo,
                             open_meteo_snow_payload)


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _routed(wttr, open_meteo):
    """A hermes.get stand-in that answers each host differently.

    Either value may be an Exception instance (raised) or a payload (returned).
    Routing on the URL is what lets one test assert "wttr failed AND Open-Meteo
    was actually consulted" rather than just "something returned".
    """
    def _get(url, *args, **kwargs):
        target = open_meteo if "open-meteo" in url else wttr
        if isinstance(target, Exception):
            raise target
        return _FakeResp(target)
    return _get


class _FreshCache(unittest.TestCase):
    """fetch() stores what it gets and handle() answers from it (T7). Each test
    starts and ends with an empty cache, so a reading one test fetched can
    never answer another's handle()."""

    def setUp(self):
        aura._clear_cache()
        self.addCleanup(aura._clear_cache)


# ── The failover decision ─────────────────────────────────────────────────────

class TestFailover(_FreshCache):

    def test_primary_success_never_calls_the_fallback(self):
        """Open-Meteo is a fallback, not a second opinion: a healthy wttr.in
        must not cost a second HTTP round trip."""
        calls = []

        def _get(url, *args, **kwargs):
            calls.append(url)
            return _FakeResp(load_real_j1())

        with mock.patch("tools.aura.hermes.get", side_effect=_get):
            out = aura.fetch()
        self.assertNotIn("error", out)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("open-meteo", calls[0])

    def test_fallback_rescues_a_failed_primary(self):
        with mock.patch("tools.aura.hermes.get",
                        side_effect=_routed(hermes.Offline(),
                                            load_real_open_meteo())):
            out = aura.fetch()
        self.assertNotIn("error", out)
        self.assertIn("temp_f", out)

    def test_fallback_rescues_an_expired_certificate(self):
        """The failure that prompted all this: wttr.in served an expired cert
        for nine hours on 2026-09-15. SSLError subclasses ConnectionError, so
        this also guards the except-ordering in handle()."""
        with mock.patch("tools.aura.hermes.get",
                        side_effect=_routed(hermes.CertError(),
                                            load_real_open_meteo())):
            out = aura.fetch()
        self.assertNotIn("error", out)

    def test_both_sources_failing_reraises_the_primary_error(self):
        """The log tracks wttr.in; surfacing the fallback's error instead would
        bury the failure anyone is actually watching."""
        with mock.patch("tools.aura.hermes.get",
                        side_effect=_routed(hermes.Timeout(),
                                            hermes.Offline())):
            with self.assertRaises(hermes.Timeout):
                aura.fetch()

    def test_handle_degrades_when_both_fail(self):
        """handle() still never raises (§2), fallback or no fallback."""
        with mock.patch("tools.aura.hermes.get",
                        side_effect=_routed(hermes.CertError(),
                                            hermes.Offline())):
            out = aura.handle()
        self.assertEqual(out["error"], "weather_cert_error")


# ── Shape parity — the contract that keeps panels ignorant of the source ──────

class TestShapeParity(_FreshCache):

    def _both(self):
        with mock.patch("tools.aura.hermes.get",
                        return_value=_FakeResp(load_real_j1())):
            primary = aura.fetch()
        with mock.patch("tools.aura.hermes.get",
                        side_effect=_routed(hermes.Offline(),
                                            load_real_open_meteo())):
            fallback = aura.fetch()
        return primary, fallback

    def test_top_level_keys_match_exactly(self):
        primary, fallback = self._both()
        self.assertEqual(set(primary), set(fallback))

    def test_astronomy_keys_match_exactly(self):
        primary, fallback = self._both()
        self.assertEqual(set(primary["astronomy"]), set(fallback["astronomy"]))

    def test_forecast_day_keys_match_exactly(self):
        primary, fallback = self._both()
        self.assertTrue(fallback["forecast"])
        self.assertEqual(set(primary["forecast"][0]), set(fallback["forecast"][0]))

    def test_value_types_match(self):
        """A panel formats these with f-strings and arithmetic; a str where the
        primary gives an int is exactly the bug shape parity is meant to catch."""
        primary, fallback = self._both()
        for key in ("temp_f", "temp_c", "feels_like_f", "high_f", "low_f",
                    "humidity_pct", "wind_mph", "uv_index", "rain_chance_pct",
                    "cloud_cover_pct"):
            self.assertIsInstance(fallback[key], int, msg=key)
            self.assertIsInstance(primary[key], int, msg=key)
        self.assertIsInstance(fallback["precip_mm"], float)
        self.assertIsInstance(fallback["wind_label"], str)


# ── Where the dialects genuinely differ ───────────────────────────────────────

class TestWmoTranslation(_FreshCache):
    """weather_code must stay a WWO code: panels/aura_panel.py's _icon() maps
    that space, and leaking a raw WMO code would silently show the wrong emoji
    (WMO 3 "overcast" would read as WWO 3, which is not a code at all)."""

    def test_codes_land_in_the_wwo_space(self):
        from panels import aura_panel
        with mock.patch("tools.aura.hermes.get",
                        side_effect=_routed(hermes.Offline(),
                                            load_real_open_meteo())):
            out = aura.fetch()
        for day in out["forecast"]:
            self.assertIn(day["weather_code"], aura_panel._CODE_TO_ICON,
                          msg=f"{day['weather_code']} is not a mapped WWO code")

    def test_every_mapped_code_is_a_known_wwo_code(self):
        """Holds the translation table against the panel's icon table, so adding
        a WMO row that maps to a code no icon knows fails here, not on screen."""
        from panels import aura_panel
        for wmo, (wwo, _desc) in aura._WMO_TO_WWO.items():
            self.assertIn(wwo, aura_panel._CODE_TO_ICON,
                          msg=f"WMO {wmo} -> unmapped WWO {wwo}")

    def test_unknown_wmo_code_degrades(self):
        code, desc = aura._wmo(1234)
        self.assertEqual(code, aura._UNKNOWN_WWO)
        self.assertEqual(desc, "")

    def test_missing_wmo_code_degrades(self):
        self.assertEqual(aura._wmo(None)[0], aura._UNKNOWN_WWO)

    def test_snow_code_routes_probability_to_snow(self):
        """Open-Meteo gives ONE precipitation probability; the code decides which
        of Aura's two fields it lands in rather than inventing the other."""
        out = aura._from_open_meteo(open_meteo_snow_payload())
        self.assertEqual(out["forecast"][0]["snow_pct"], 80)
        self.assertEqual(out["forecast"][0]["rain_pct"], 0)

    def test_rain_code_routes_probability_to_rain(self):
        out = aura._from_open_meteo(load_real_open_meteo())
        for day in out["forecast"]:
            self.assertEqual(day["snow_pct"], 0)


class TestMissingMoon(unittest.TestCase):
    """Open-Meteo has no moon data, so the fallback's moon fields are empty.
    Since V0.03 audit M3 nothing on screen reads them (Selene computes the
    moon), but get_weather's output must keep the same keys either way."""

    def test_moon_fields_are_present_but_empty(self):
        out = aura._from_open_meteo(load_real_open_meteo())
        astro = out["astronomy"]
        for key in ("moonrise", "moonset", "moon_phase", "moon_illumination"):
            self.assertEqual(astro[key], "", msg=key)

    def test_sun_fields_survive_in_wttr_clock_format(self):
        """get_weather's astronomy block reads the same whichever host
        answered: wttr.in's "07:02 AM" strings, not Open-Meteo's ISO stamps.
        (Helios no longer reads this block; it computes the sun, M3.)"""
        out = aura._from_open_meteo(load_real_open_meteo())
        for key in ("sunrise", "sunset"):
            self.assertRegex(out["astronomy"][key], r"^\d{2}:\d{2} [AP]M$", msg=key)


class TestPlaceName(unittest.TestCase):
    """Open-Meteo resolves no place name, so Aura tidies the configured one —
    without mangling the formats that are not names."""

    def test_plain_name_is_title_cased(self):
        self.assertEqual(aura._place_name("shinnston"), "Shinnston")

    def test_spaced_name_is_title_cased(self):
        self.assertEqual(aura._place_name("new york"), "New York")

    def test_zip_is_left_alone(self):
        self.assertEqual(aura._place_name("10001"), "10001")

    def test_latlon_is_left_alone(self):
        self.assertEqual(aura._place_name("51.4779,0.0"), "51.4779,0.0")

    def test_country_suffixed_name_is_left_alone(self):
        """Greenwich,UK -> .title() gives Greenwich,Uk — worse than leaving it."""
        self.assertEqual(aura._place_name("Greenwich,UK"), "Greenwich,UK")


class TestUnitConversion(unittest.TestCase):
    """Open-Meteo answers in one unit; Aura's dict carries both so the Settings
    toggle switches display without a re-fetch."""

    def test_freezing(self):
        self.assertEqual(aura._f_to_c(32), 0)

    def test_boiling(self):
        self.assertEqual(aura._f_to_c(212), 100)

    def test_negative(self):
        self.assertEqual(aura._f_to_c(-40), -40)

    def test_both_units_present_and_consistent(self):
        out = aura._from_open_meteo(load_real_open_meteo())
        self.assertEqual(out["temp_c"], aura._f_to_c(out["temp_f"]))
        for day in out["forecast"]:
            self.assertEqual(day["high_c"], aura._f_to_c(day["high_f"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
