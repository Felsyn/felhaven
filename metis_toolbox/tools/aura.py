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

Upstream:    kairos.py (calls fetch), pythia.py (registration + dispatch),
             themis.py (location, read fresh per fetch)
Downstream:  panels/aura_panel.py (display surface), tools/helios.py and
             tools/selene.py (both read the astronomy dict, never fetch it)

Requires:    requests (already in the Felhaven stack)

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
"""

import logging
import os
import sys
from datetime import datetime

import requests
from typing import Any

# Aura reaches UP to an app-root sibling (themis.py) for the user's location —
# the same guarded-path pattern callimachus.py uses so a bare
# `python tools/aura.py` standalone run still resolves it (Kairos, tests, and
# felhaven already run with the app root on sys.path). Must precede the import.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import themis

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
_REQUEST_TIMEOUT = 6    # seconds — tight; Metis is voice-first, latency matters
# The timeout is per REQUEST, and there are now two sources, so a total failure
# costs up to 12 s rather than 6. That is the failure path only — wttr.in still
# answers in one round trip (the fallback is never called speculatively) — and
# 12 s to get an answer beats 6 s to get an error. If that ceiling ever needs
# to come down, lower this constant rather than dropping the second source.


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
    resp = requests.get(url, timeout=_REQUEST_TIMEOUT)
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
#     leaves moonrise/moonset/phase empty; selene.py already degrades those to
#     "—" per-field (§2), so SELENE reads blank on the fallback rather than
#     crashing. Restoring it means computing phase locally, not a new fetch.

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
    """'2026-09-15T07:02' -> '07:02 AM'. helios.py parses wttr.in's clock
    strings, so the fallback hands it the same shape, not an ISO stamp."""
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
    # this to dict[str, object], which requests' params type rejects (--strict
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
    resp = requests.get(_OPEN_METEO_URL, params=params, timeout=_REQUEST_TIMEOUT)
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
        # cost of the fallback, and selene.py renders them as "—".
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
        # Raw passthrough — Aura fetches, it does not interpret. Helios/Selene
        # parse these strings downstream. wttr.in returns clock strings like
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


def handle() -> dict[str, Any]:
    """
    Called by the toolbox dispatcher when the LLM invokes get_weather.
    Returns current conditions for the configured location (Settings/Themis, or
    the AURA_LOCATION env override). On any failure, returns a degraded dict
    with an error key — never raises.
    """
    try:
        return _fetch_weather()
    except requests.exceptions.SSLError:
        # Its own slug because it is its own problem: a certificate failure is
        # not a flaky link and no amount of retrying clears it. wttr.in served
        # an expired cert for nine hours on 2026-09-15 and every attempt failed
        # identically — worth saying so rather than reporting a generic outage.
        log.error("Aura: TLS certificate verification failed.")
        return {"error": "weather_cert_error", "location": _location()}
    except requests.Timeout:
        log.warning("Aura: weather request timed out.")
        return {"error": "weather_timeout", "location": _location()}
    except requests.ConnectionError:
        log.warning("Aura: no network connection.")
        return {"error": "weather_offline", "location": _location()}
    except Exception as e:
        log.error(f"Aura: unexpected failure: {type(e).__name__}")
        return {"error": "weather_unavailable", "location": _location()}


def fetch() -> dict[str, Any]:
    """Kairos entry point — raises; Kairos catches and delivers None.
    Raises only when both wttr.in and Open-Meteo fail (see _fetch_weather)."""
    return _fetch_weather()


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
