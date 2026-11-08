"""
aura.py — Goddess of the Breeze
================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Report current weather: temperature, sky, wind, and forecast.

Contract:    Polled + brain tool. Exposes TOOL_DEFINITION, handle(), and
             fetch(); both take no arguments and return a dict.
             Calls wttr.in, falling back to Open-Meteo (both JSON, neither
             needs an API key).
             handle() (the LLM path) degrades to an {"error": ...} dict and
             never raises. fetch() (the Kairos path) DOES raise on failure,
             per §2 — that is how Kairos delivers None so the panel holds a
             stale reading instead of blanking. Either only fails once BOTH
             sources have failed.
             handle() answers from the last good fetch while it is younger
             than CACHE_TTL (the Kairos poll interval) and adds `as_of`. After
             a failed refresh it serves that reading marked `stale: True`, for
             up to _STALE_MAX. fetch() always goes to the network, and its dict
             carries no `as_of`, so the panels' shape is unchanged. See the
             "Freshness" section.

Upstream:    kairos.py (calls fetch), pythia.py (registration + dispatch),
             themis.py (location, read fresh per fetch)
Downstream:  panels/aura_panel.py (display surface), panels/hypatia_panel.py
             (cloud cover). The `astronomy` block is read only by the model,
             through get_weather: since 2026-09-28 Helios and Selene compute
             the sun and moon themselves (V0.03 audit M3).

Requires:    tools/hermes.py (the bounded network read): every fetch has a
             byte cap and a deadline for the whole request.

Location config:
    The Settings tab (Themis / felhaven_settings.json) is canonical — it holds
    the lat/lon and an optional weather-location string, read fresh per fetch so
    an edit takes effect on the next Kairos tick. The AURA_LOCATION env var, if
    set, OVERRIDES the file (headless/CI). Both accept a city name, ZIP, or
    lat/lon pair — e.g. "Greenwich,UK", "10001", "51.4779,0.0".

V0.02:       Audited 2026-07-25 — contract verified: handle() never raises
             (degrades to weather_timeout / weather_offline / weather_unavailable),
             fetch() DOES raise per §2. Added test_aura_contract.py to pin that
             public surface (the _build() shaping was already covered by the
             sibling test_aura_* files). Error slugs conform; no dead code.

2026-09-15:  added Open-Meteo as a fallback source, and the weather_cert_error
             slug. wttr.in is one free host with no SLA and the log shows it:
             runs of 500s, read timeouts, and a nine-hour outage the day this
             was written, when its Let's Encrypt certificate expired. The
             fallback normalizes into _build()'s dict so no panel learns which
             host answered — see the "Fallback source" section for the two
             places the dialects genuinely differ (its WMO weather codes get
             translated into wttr.in's WWO space, and it carries no moon data).

2026-09-28:  handle() answers from a cache (V0.03 audit T7 / M2). Kairos
             already polled every 30 min, yet get_weather, get_sun_times and
             get_moon_phase each fetched again, so one question cost three
             round trips. See "Freshness" for the threading and the stale rule.
"""

import copy
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from typing import Any

# Aura reaches UP to an app-root sibling (themis.py) for the user's location —
# the same guarded-path pattern callimachus.py uses so a bare
# `python tools/aura.py` standalone run still resolves it (Kairos, tests, and
# felhaven already run with the app root on sys.path). Must precede the import.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import themis
from tools import hermes

log = logging.getLogger("METIS.aura")

# ── Location ──────────────────────────────────────────────────────────────────
# The file (Themis) is canonical; the AURA_LOCATION env var, if set, overrides
# it. Resolved per fetch via _location() so a Settings edit needs no restart.
_AURA_LOCATION_ENV = os.environ.get("AURA_LOCATION")


def _location() -> str:
    """The weather location for this fetch: the AURA_LOCATION env var wins if
    set (headless/CI override), else Themis (the Settings tab / file, which
    itself falls back to the old hardcoded default). Read every fetch."""
    if _AURA_LOCATION_ENV:
        return _AURA_LOCATION_ENV
    return themis.weather_query()

# ── Internals ─────────────────────────────────────────────────────────────────

_WTTR_URL      = "https://wttr.in/{location}?format=j1"
_REQUEST_TIMEOUT = 6    # seconds per socket operation — tight; latency matters
# Each source also has a deadline for its WHOLE request, hermes.deadline_for():
# 12 s at these caps. With two sources a total failure costs up to 24 s, and
# that is also the longest the cache lock (see "Freshness") can be held. That is
# the failure path only — wttr.in still answers in one round trip (the fallback
# is never called speculatively). If that ceiling ever needs to come down,
# lower this constant rather than dropping the second source.

# Byte caps (hermes rule: 4x the largest measured body, rounded up to a power of
# two, never below 64 KiB). Re-measure if a source changes its format.
_WTTR_MAX_BYTES = 256 * 1024        # measured 2026-10-01: 39,427 B (j1, London)
_OPEN_METEO_MAX_BYTES = 64 * 1024   # measured 2026-10-01: 1,170 B (these params); the floor


def _mph_to_label(mph: int) -> str:
    """Beaufort-adjacent wind description for spoken output."""
    if mph < 5:   return "calm"
    if mph < 15:  return "light breeze"
    if mph < 25:  return "moderate wind"
    if mph < 40:  return "strong wind"
    return "very strong wind"


def _safe_int(value: Any, default: int = 0) -> int:
    """int() that degrades to a default instead of raising on bad/missing input.
    wttr.in delivers every numeric as a string, so one malformed block must not
    take down the whole _build()."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _max_pct(hourly: list[dict[str, Any]], key: str) -> int:
    """Largest casted percentage across a day's hourly blocks; 0 if none.
    wttr has no daily chance-of-rain field — it's the max over the 3-hour
    blocks. A malformed block degrades to 0 for that block, never the field."""
    best = 0
    for block in hourly:
        best = max(best, _safe_int(block.get(key), 0))
    return best


def _block_desc(node: dict[str, Any]) -> str:
    """weatherDesc[0].value from an hourly block (or day), '' if absent."""
    try:
        desc: str = node.get("weatherDesc", [{}])[0].get("value", "")
        return desc
    except (AttributeError, IndexError, TypeError):
        return ""


def _forecast_day(day: dict[str, Any]) -> dict[str, Any]:
    """
    Shape one wttr weather[] day into display-ready forecast scalars. Aura
    flattens to numbers/strings; mapping codes to emoji is the display layer's
    job. Never raises — a missing/empty hourly list yields zeros and code 0.
    """
    hourly = day.get("hourly") or []
    if len(hourly) >= 5:
        mid = hourly[4]          # ~noon block — boring, representative
    elif hourly:
        mid = hourly[0]          # fewer than 5 blocks: fall back to the first
    else:
        mid = {}
    return {
        "date":         day.get("date", ""),
        "weather_code": _safe_int(mid.get("weatherCode"), 0),
        "description":  _block_desc(mid) or _block_desc(day),
        "high_f":       _safe_int(day.get("maxtempF"), 0),
        "low_f":        _safe_int(day.get("mintempF"), 0),
        # Celsius counterparts (wttr ships both units free) so the Settings
        # temperature toggle can switch display without a re-fetch. The display
        # layer picks; Aura stays a pure fetcher of facts (§0).
        "high_c":       _safe_int(day.get("maxtempC"), 0),
        "low_c":        _safe_int(day.get("mintempC"), 0),
        "rain_pct":     _max_pct(hourly, "chanceofrain"),
        "snow_pct":     _max_pct(hourly, "chanceofsnow"),
    }


def _fetch_raw() -> dict[str, Any]:
    """
    Fetch wttr.in JSON for the configured location.
    Returns parsed dict or raises on failure.
    """
    url = _WTTR_URL.format(location=_location().replace(" ", "+"))
    resp = hermes.get(url, cap=_WTTR_MAX_BYTES, timeout=_REQUEST_TIMEOUT)
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    return data


# ── Fallback source: Open-Meteo ───────────────────────────────────────────────
# wttr.in is a single free host with no SLA, and it shows: the log has runs of
# 500s, read timeouts, and a nine-hour outage on 2026-09-15 when its Let's
# Encrypt certificate expired. Open-Meteo is a second keyless source used ONLY
# when wttr.in fails, so one host being down stops costing us the panel.
#
# It speaks a different dialect, so _from_open_meteo() normalizes it into the
# SAME dict _build() returns — Aura's output contract is the panel's contract,
# and the display layer must not learn which host answered. Two consequences
# worth knowing before "fixing" either:
#
#   - weather_code stays a WWO code, wttr.in's code space, because that is what
#     panels/aura_panel.py's _icon() maps. _WMO_TO_WWO translates.
#   - Open-Meteo has no moon data. The astronomy dict keeps its sun fields and
#     leaves moonrise/moonset/phase empty. Nothing on screen reads them any
#     more (Selene computes the moon, M3), so the gap shows only in
#     get_weather's own output.

_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# WMO code -> (nearest WWO code, human description). The WWO code drives the
# forecast emoji; the description is what the NOW tab and the LLM read.
_WMO_TO_WWO: dict[int, tuple[int, str]] = {
    0:  (113, "Clear"),
    1:  (113, "Mainly clear"),
    2:  (116, "Partly cloudy"),
    3:  (122, "Overcast"),
    45: (143, "Fog"),
    48: (248, "Depositing rime fog"),
    51: (266, "Light drizzle"),
    53: (266, "Moderate drizzle"),
    55: (293, "Dense drizzle"),
    56: (281, "Light freezing drizzle"),
    57: (284, "Dense freezing drizzle"),
    61: (296, "Slight rain"),
    63: (302, "Moderate rain"),
    65: (308, "Heavy rain"),
    66: (311, "Light freezing rain"),
    67: (314, "Heavy freezing rain"),
    71: (326, "Slight snowfall"),
    73: (332, "Moderate snowfall"),
    75: (338, "Heavy snowfall"),
    77: (350, "Snow grains"),
    80: (353, "Slight rain showers"),
    81: (356, "Moderate rain showers"),
    82: (359, "Violent rain showers"),
    85: (368, "Slight snow showers"),
    86: (371, "Heavy snow showers"),
    95: (386, "Thunderstorm"),
    96: (389, "Thunderstorm with slight hail"),
    99: (395, "Thunderstorm with heavy hail"),
}

# WMO codes whose precipitation falls as snow. Open-Meteo gives ONE combined
# precipitation probability, so the code decides which of Aura's two percentage
# fields it lands in rather than inventing a second number for the other.
_WMO_SNOW = {71, 73, 75, 77, 85, 86}

_UNKNOWN_WWO = 113  # an unmapped WMO code reads as clear, not as a crash


def _wmo(code: Any) -> tuple[int, str]:
    """WMO code -> (WWO code, description). Unknown/missing degrades, never raises."""
    return _WMO_TO_WWO.get(_safe_int(code, -1), (_UNKNOWN_WWO, ""))


def _f_to_c(temp_f: float) -> int:
    """Open-Meteo answers in ONE unit per request; wttr.in shipped both. Aura's
    dict must carry both so the Settings °F/°C toggle switches display without a
    re-fetch, so the Celsius half is converted here rather than bought with a
    second HTTP round trip."""
    return round((temp_f - 32.0) * 5.0 / 9.0)


def _place_name(query: str) -> str:
    """Tidy the configured location for display, since Open-Meteo resolves no
    place name of its own. Only a plain single-word-or-spaced name is
    title-cased — a ZIP, a lat/lon pair and a "Greenwich,UK" style string are
    all left exactly as typed, because .title() mangles every one of them."""
    if query.replace(" ", "").isalpha():
        return query.title()
    return query


def _om_clock(iso: str) -> str:
    """'2026-09-15T07:02' -> '07:02 AM'. get_weather's astronomy block keeps
    wttr.in's clock shape on the fallback too, not an ISO stamp, so the model
    reads one format whichever host answered."""
    try:
        return datetime.strptime(iso, "%Y-%m-%dT%H:%M").strftime("%I:%M %p")
    except (ValueError, TypeError):
        return ""


def _fetch_open_meteo() -> dict[str, Any]:
    """Fetch Open-Meteo JSON for the configured coordinates. Raises on failure.

    Uses lat/lon from Themis directly — Open-Meteo has no place-name lookup,
    which is also why the returned dict's `location` falls back to the
    configured location string rather than a host-resolved city.
    """
    # Annotated, not inferred: the mixed float/str/int values make mypy widen
    # this to dict[str, object], which hermes' params type rejects (--strict
    # on tools/ is a blocking gate).
    params: dict[str, float | str | int] = {
        "latitude": themis.latitude(),
        "longitude": themis.longitude(),
        "current": ("temperature_2m,relative_humidity_2m,apparent_temperature,"
                    "precipitation,weather_code,cloud_cover,wind_speed_10m,uv_index"),
        "daily": ("weather_code,temperature_2m_max,temperature_2m_min,"
                  "precipitation_probability_max,sunrise,sunset"),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "mm",
        "timezone": "auto",
        "forecast_days": 3,
    }
    resp = hermes.get(_OPEN_METEO_URL, params=params, cap=_OPEN_METEO_MAX_BYTES,
                      timeout=_REQUEST_TIMEOUT)
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    return data


def _from_open_meteo(raw: dict[str, Any]) -> dict[str, Any]:
    """Shape an Open-Meteo response into the same dict _build() returns."""
    cur = raw["current"]
    daily = raw.get("daily") or {}
    days = daily.get("time") or []

    def day_field(key: str, i: int, default: Any = 0) -> Any:
        seq = daily.get(key) or []
        return seq[i] if i < len(seq) else default

    temp_f = round(float(cur["temperature_2m"]))
    feels_f = round(float(cur["apparent_temperature"]))
    wind_mph = round(float(cur.get("wind_speed_10m", 0)))
    _wwo, desc = _wmo(cur.get("weather_code"))

    forecast = []
    for i, date in enumerate(days):
        code, day_desc = _wmo(day_field("weather_code", i))
        precip_pct = _safe_int(day_field("precipitation_probability_max", i), 0)
        snowing = _safe_int(day_field("weather_code", i), -1) in _WMO_SNOW
        high_f = round(float(day_field("temperature_2m_max", i, temp_f)))
        low_f = round(float(day_field("temperature_2m_min", i, temp_f)))
        forecast.append({
            "date":         date,
            "weather_code": code,
            "description":  day_desc,
            "high_f":       high_f,
            "low_f":        low_f,
            "high_c":       _f_to_c(high_f),
            "low_c":        _f_to_c(low_f),
            "rain_pct":     0 if snowing else precip_pct,
            "snow_pct":     precip_pct if snowing else 0,
        })

    today = forecast[0] if forecast else {}
    return {
        "location":     _place_name(_location()),
        "country":      "",
        "description":  desc,
        "temp_f":       temp_f,
        "temp_c":       _f_to_c(temp_f),
        "feels_like_f": feels_f,
        "feels_like_c": _f_to_c(feels_f),
        "high_f":       today.get("high_f", temp_f),
        "low_f":        today.get("low_f", temp_f),
        "high_c":       today.get("high_c", _f_to_c(temp_f)),
        "low_c":        today.get("low_c", _f_to_c(temp_f)),
        "humidity_pct": _safe_int(cur.get("relative_humidity_2m"), 0),
        "wind_mph":     wind_mph,
        "wind_label":   _mph_to_label(wind_mph),
        "precip_mm":    float(cur.get("precipitation", 0) or 0),
        "uv_index":     round(float(cur.get("uv_index", 0) or 0)),
        "rain_chance_pct": today.get("rain_pct", 0),
        "cloud_cover_pct": _safe_int(cur.get("cloud_cover"), 0),
        # Sun only — see the section note. Empty moon fields are the documented
        # cost of the fallback; they reach only get_weather's output.
        "astronomy": {
            "sunrise":           _om_clock(day_field("sunrise", 0, "")),
            "sunset":            _om_clock(day_field("sunset", 0, "")),
            "moonrise":          "",
            "moonset":           "",
            "moon_phase":        "",
            "moon_illumination": "",
        },
        "forecast": forecast,
    }


def _fetch_weather() -> dict[str, Any]:
    """The shaped reading, from wttr.in if it answers and Open-Meteo if it does
    not. Raises only when BOTH sources fail, and re-raises the *primary*
    failure — wttr.in is the one whose behaviour the log is tracking, and a
    fallback error would bury it."""
    try:
        return _build(_fetch_raw())
    except Exception as primary:
        log.warning(f"Aura: wttr.in failed ({type(primary).__name__}); "
                    f"trying Open-Meteo.")
        try:
            data = _from_open_meteo(_fetch_open_meteo())
        except Exception as secondary:
            log.error(f"Aura: Open-Meteo also failed: {type(secondary).__name__}")
            raise primary
        log.info("Aura: served by Open-Meteo (wttr.in unavailable).")
        return data


# ── Freshness (V0.03 audit T7) ────────────────────────────────────────────────
# Kairos polls fetch() every 1800 s for the panels, but handle() used to fetch
# AGAIN on every call, and Helios and Selene each called it then: "weather,
# sunset and moon?" was three round trips, and offline each one walked both
# sources before failing. Now handle() answers from the last good fetch and
# dates it with `as_of`. pheme.py copies this pattern for the news.
#
# Threads: Kairos's worker calls fetch() and Pythia's worker calls handle(), so
# the cache is shared between those two. It is never shared with the Tk main
# thread, which only sees fetch()'s return value through the Kairos queue.
# _lock guards the cache AND is held across the network call, on purpose. The
# usual rule against holding a lock through slow I/O is about stalling threads
# that want something else; the only threads waiting here want the result of
# that very fetch, and waiting beats a second, racing round trip. It is safe
# only because the fetch is bounded: hermes ends each source's request at its
# deadline, so the lock is held 24 s at the very worst, never for as long as a
# slow server likes. Every dict
# going in or out is a deep copy, so no two threads ever hold one mutable
# object (§6).

CACHE_TTL = 1800            # s. Kairos's aura interval, so handle() normally reuses the poll
_RETRY_AFTER = 60           # s. A failed refresh isn't retried sooner: one turn, one attempt
_STALE_MAX = 2 * CACHE_TTL  # s. The oldest reading still served, marked stale, when a refresh fails

# Everything that shapes a reading: the query wttr.in is asked, and the lat/lon
# Open-Meteo is asked. Units are deliberately NOT in it: every reading carries
# both °F and °C, so flipping the Settings toggle must not cost a refetch.
_Key = tuple[str, float, float]


@dataclass(frozen=True)
class _Reading:
    key: _Key
    data: dict[str, Any]    # the cache's own copy; only ever handed out via _answer()
    at: float               # time.monotonic(): ages it, immune to wall-clock changes
    as_of: str              # naive local ISO, pythia._now()'s convention: what the model reads


@dataclass(frozen=True)
class _Failure:
    key: _Key
    at: float               # time.monotonic()
    error: dict[str, Any]   # the §2 error dict that attempt produced


_lock = threading.Lock()
_reading: _Reading | None = None
_failure: _Failure | None = None


def _now() -> datetime:
    """Injectable clock seam for `as_of`, so a test can move the wall clock and
    prove a stale answer keeps its original date. Naive LOCAL time, the same
    convention as pythia._now() (tools/ does not import the brain)."""
    return datetime.now()


def _cache_key() -> _Key:
    """Read fresh per call, like _location(), so a Settings edit is a new key.
    Captured BEFORE a fetch: an edit that lands mid-fetch then costs one extra
    fetch, never the old city's weather filed under the new one."""
    return (_location(), themis.latitude(), themis.longitude())


def _remember(key: _Key, data: dict[str, Any]) -> _Reading:
    """Store a good fetch and clear any failure. Caller holds _lock."""
    global _reading, _failure
    _reading = _Reading(key, copy.deepcopy(data), time.monotonic(),
                        _now().isoformat(timespec="seconds"))
    _failure = None
    return _reading


def _answer(reading: _Reading, stale: bool = False) -> dict[str, Any]:
    """A reading as the model gets it: a copy, dated, and flagged when stale.
    The flag is there because gemma4:e2b is weak at date arithmetic, and can
    relay `stale` without having to work out how old `as_of` is."""
    out = copy.deepcopy(reading.data)
    out["as_of"] = reading.as_of
    if stale:
        out["stale"] = True
    return out


def _clear_cache() -> None:
    """Forget every reading and failure. For tests, so none leaks between them."""
    global _reading, _failure
    with _lock:
        _reading = None
        _failure = None


# ── Contract ──────────────────────────────────────────────────────────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": (
            "Returns current local weather conditions including temperature, "
            "feels-like, sky description, humidity, wind speed, and UV index. "
            "Call this when the user asks about weather, temperature, what to wear, "
            "whether to go outside, or any sky/atmospheric condition."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


def _build(raw: dict[str, Any]) -> dict[str, Any]:
    cur  = raw["current_condition"][0]
    area = raw.get("nearest_area", [{}])[0]
    city    = area.get("areaName", [{}])[0].get("value", _location())
    country = area.get("country",  [{}])[0].get("value", "")
    region  = area.get("region",   [{}])[0].get("value", "")
    temp_f    = int(cur["temp_F"])
    temp_c    = int(cur["temp_C"])
    feels_f   = int(cur["FeelsLikeF"])
    feels_c   = int(cur["FeelsLikeC"])
    humidity  = int(cur["humidity"])
    wind_mph  = int(cur["windspeedMiles"])
    uv        = int(cur.get("uvIndex", 0))
    desc      = cur["weatherDesc"][0]["value"]
    precip_mm = float(cur.get("precipMM", 0))
    today  = raw.get("weather", [{}])[0]
    high_f = int(today.get("maxtempF", temp_f))
    low_f  = int(today.get("mintempF", temp_f))
    high_c = int(today.get("maxtempC", temp_c))
    low_c  = int(today.get("mintempC", temp_c))
    astro  = today.get("astronomy", [{}])[0]
    forecast = [_forecast_day(d) for d in raw.get("weather", [])]
    rain_chance_pct = forecast[0]["rain_pct"] if forecast else 0
    return {
        "location":     f"{city}, {region}" if region else city,
        "country":      country,
        "description":  desc,
        "temp_f":       temp_f,
        "temp_c":       temp_c,
        "feels_like_f": feels_f,
        "feels_like_c": feels_c,
        "high_f":       high_f,
        "low_f":        low_f,
        "high_c":       high_c,
        "low_c":        low_c,
        "humidity_pct": humidity,
        "wind_mph":     wind_mph,
        "wind_label":   _mph_to_label(wind_mph),
        "precip_mm":    precip_mm,
        "uv_index":     uv,
        "rain_chance_pct": rain_chance_pct,   # today's max chance-of-rain
        # Hypatia's ConditionsWidget reads this — Aura stays the single sky-data
        # fetcher, Hypatia only reads (CONVENTIONS §12).
        "cloud_cover_pct": _safe_int(cur.get("cloudcover"), 0),
        # Raw passthrough — Aura fetches, it does not interpret. Only the model
        # reads these now, through get_weather: Helios and Selene compute the
        # sun and moon themselves (M3). wttr.in returns clock strings like
        # "05:52 AM" and, on some days, "No moonrise" / "No moonset".
        "astronomy": {
            "sunrise":           astro.get("sunrise", ""),
            "sunset":            astro.get("sunset", ""),
            "moonrise":          astro.get("moonrise", ""),
            "moonset":           astro.get("moonset", ""),
            "moon_phase":        astro.get("moon_phase", ""),
            "moon_illumination": astro.get("moon_illumination", ""),
        },
        # 3-day outlook (today + 2). One scalar dict per day, in order.
        "forecast": forecast,
    }


def _fetch_or_error() -> dict[str, Any]:
    """One network attempt as handle() reports it: the reading, or the §2 error
    dict for why there isn't one. Never raises."""
    try:
        return _fetch_weather()
    except hermes.CertError:
        # Its own slug because it is its own problem: a certificate failure is
        # not a flaky link and no amount of retrying clears it. wttr.in served
        # an expired cert for nine hours on 2026-09-15 and every attempt failed
        # identically — worth saying so rather than reporting a generic outage.
        log.error("Aura: TLS certificate verification failed.")
        return {"error": "weather_cert_error", "location": _location()}
    except hermes.Timeout:
        log.warning("Aura: weather request timed out.")
        return {"error": "weather_timeout", "location": _location()}
    except hermes.Offline:
        log.warning("Aura: no network connection.")
        return {"error": "weather_offline", "location": _location()}
    except Exception as e:
        log.error(f"Aura: unexpected failure: {type(e).__name__}")
        return {"error": "weather_unavailable", "location": _location()}


def handle() -> dict[str, Any]:
    """
    Called by the toolbox dispatcher when the LLM invokes get_weather. Returns
    current conditions for the configured location (Settings/Themis, or the
    AURA_LOCATION env override), dated with `as_of`. Never raises.

    Answers from the last good fetch while it is younger than CACHE_TTL; only a
    stale or empty cache goes to the network. When that refresh fails, the last
    reading for the SAME location comes back for up to _STALE_MAX marked
    `stale: True`, its `as_of` unchanged, since that is when the data was
    fetched. Past that, or with nothing to fall back on, it is the error dict,
    and a failure is not retried inside _RETRY_AFTER.
    """
    global _failure
    key = _cache_key()
    with _lock:
        now = time.monotonic()
        last = _reading if _reading is not None and _reading.key == key else None
        if last is not None and now - last.at < CACHE_TTL:
            return _answer(last)
        failed = _failure if _failure is not None and _failure.key == key else None
        if failed is None or now - failed.at >= _RETRY_AFTER:
            result = _fetch_or_error()
            if "error" not in result:
                return _answer(_remember(key, result))
            failed = _failure = _Failure(key, time.monotonic(), result)
        if last is not None and now - last.at < _STALE_MAX:
            return _answer(last, stale=True)
        return copy.deepcopy(failed.error)


def fetch() -> dict[str, Any]:
    """Kairos entry point — raises; Kairos catches and delivers None.
    Raises only when both wttr.in and Open-Meteo fail (see _fetch_weather).

    Always goes to the network, even when the cache is fresh: this poll IS the
    refresh. If it answered from the cache, a failed poll would stop raising
    and Kairos's retry ladder would never engage. A good result is stored for
    handle(); a failure leaves the last reading where it was."""
    key = _cache_key()
    with _lock:
        data = _fetch_weather()
        _remember(key, data)
    return data


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    result = handle()
    if "error" in result:
        print(f"[Aura] Error: {result['error']}")
    else:
        print(
            f"[Aura] {result['location']} | {result['description']} | "
            f"{result['temp_f']}°F (feels {result['feels_like_f']}°F) | "
            f"H:{result['high_f']} L:{result['low_f']} | "
            f"Wind: {result['wind_label']} ({result['wind_mph']} mph) | "
            f"UV: {result['uv_index']}"
        )
