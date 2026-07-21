"""
aura.py — Goddess of the Breeze
================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Report current weather: temperature, sky, wind, and forecast.

Contract:    Polled + brain tool. Exposes TOOL_DEFINITION, handle(), and
             fetch(); both take no arguments and return a dict.
             Calls wttr.in (JSON, no API key required).
             handle() (the LLM path) degrades to an {"error": ...} dict and
             never raises. fetch() (the Kairos path) DOES raise on failure,
             per §2 — that is how Kairos delivers None so the panel holds a
             stale reading instead of blanking.

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
    lat/lon pair — e.g. "Moundsville,WV", "26041", "39.9226,-80.7434".
"""

import logging
import os
import sys
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
        return _build(_fetch_raw())
    except requests.Timeout:
        log.warning("Aura: wttr.in request timed out.")
        return {"error": "weather_timeout", "location": _location()}
    except requests.ConnectionError:
        log.warning("Aura: no network connection.")
        return {"error": "weather_offline", "location": _location()}
    except Exception as e:
        log.error(f"Aura: unexpected failure: {e}")
        return {"error": "weather_unavailable", "location": _location()}


def fetch() -> dict[str, Any]:
    """Kairos entry point — raises; Kairos catches and delivers None."""
    return _build(_fetch_raw())


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
