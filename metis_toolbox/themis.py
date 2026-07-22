"""
themis.py — Keeper of Settled Law (user settings)
=================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Own the per-install user preferences (location, temperature
             unit, clock format). They live in felhaven_settings.json.

Contract:    Root-level persistence flavor (like scribe.py): no fetch(), no
             TOOL_DEFINITION — plain functions plus one exception class. It is
             the SINGLE source of truth for the settings that used to be
             hardcoded in aura/hypatia/horai; those modules read Themis at
             fetch time so a GUI edit takes effect on the next Kairos tick
             without a restart.

               load()            — read the file, coerced + merged over the
                                    fail-soft defaults. Missing/garbled file or
                                    field degrades to the default (logged),
                                    never raises (§4 config-loader rule).
               latitude() ...    — typed getters, each a thin read of load().
               weather_query()   — the string Aura hands wttr.in: the raw
                                    weather_location if set, else "lat,lon".
               is_southern()     — latitude < 0 (Horai inverts its seasons).
               save(...)         — VALIDATE (raise SettingsError on bad input),
                                    then atomic write (tempfile + os.replace,
                                    the plutus/argus pattern). Unlike load,
                                    save is strict: the panel shows the error
                                    rather than silently persisting nonsense.

Defaults:    The old hardcoded values live on here as the fail-soft fallback —
             a fresh clone with no settings file behaves exactly as before,
             and AURA_LOCATION (env) still overrides weather_query() for
             headless/CI use (see aura.py). Nothing personal is REQUIRED in
             source anymore; these are only a floor.

Upstream:    panels/themis_panel.py (the Settings tab), tools/aura.py,
             tools/hypatia.py, tools/horai.py (readers).
Downstream:  felhaven_settings.json (committed? no — it is per-user state, so
             gitignored like the other machine-local JSON in §9).

Requires:    json, logging, os, tempfile, pathlib (stdlib only). No tkinter —
             stays independently unit-testable and UI-free, like sphynx.py /
             cerberus.py.
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger("METIS.themis")

# App-root anchored (§1): themis.py sits AT the app root, so the same rule
# scribe.py / cerberus.py use, not the tools/ two-dirname dance.
_DATA_PATH = Path(__file__).with_name("felhaven_settings.json")

# Fail-soft defaults. A fresh clone with no settings file reads exactly these
# until the user edits the Settings tab (SETUP.md §8). Deliberately the Royal
# Observatory, Greenwich — longitude 0.0 IS the prime meridian, so it is a
# self-explanatory anchor for the star map rather than somebody's house. Do not
# replace this with a real personal location: this file ships publicly, and a
# shipped default is a coordinate you publish about yourself.
DEFAULTS: dict[str, Any] = {
    "latitude":         51.4779,
    "longitude":        0.0,
    "weather_location": "",       # optional; overrides lat,lon for weather only
    "temperature_unit": "F",      # "F" or "C"
    "clock_24h":        False,     # False -> 12-hour, True -> 24-hour
}

_VALID_UNITS = ("F", "C")


class SettingsError(ValueError):
    """save() was handed a value outside its allowed range — the panel surfaces
    this rather than persisting nonsense. load() never raises it (fail-soft)."""


# ── Coercion / load (fail-soft, per-field) ────────────────────────────────────

def _coerce_lat(value: Any) -> float:
    lat = float(value)
    if not -90.0 <= lat <= 90.0:
        raise ValueError(f"latitude {lat} out of range [-90, 90]")
    return lat


def _coerce_lon(value: Any) -> float:
    lon = float(value)
    if not -180.0 <= lon <= 180.0:
        raise ValueError(f"longitude {lon} out of range [-180, 180]")
    return lon


def _coerce_unit(value: Any) -> str:
    unit = str(value).strip().upper()
    if unit not in _VALID_UNITS:
        raise ValueError(f"temperature_unit {value!r} not one of {_VALID_UNITS}")
    return unit


def load() -> dict[str, Any]:
    """Return the settings dict, each field merged over DEFAULTS and coerced to
    the right type/range. A missing or garbled file — or one bad field — falls
    back to the default for that field and logs it; never raises (§4)."""
    out: dict[str, Any] = dict(DEFAULTS)
    try:
        with open(_DATA_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("settings root is not an object")
    except FileNotFoundError:
        return out                      # first run: silent, defaults are correct
    except (OSError, json.JSONDecodeError, ValueError) as e:
        log.warning("themis: settings file unreadable, using defaults: %s", e)
        return out

    # Per-field: a single bad value degrades only itself, not the whole file.
    for key, coerce in (
        ("latitude",  _coerce_lat),
        ("longitude", _coerce_lon),
        ("temperature_unit", _coerce_unit),
    ):
        if key in raw:
            try:
                out[key] = coerce(raw[key])
            except (TypeError, ValueError) as e:
                log.warning("themis: bad %s in settings, using default: %s", key, e)
    if "weather_location" in raw:
        out["weather_location"] = str(raw["weather_location"]).strip()
    if "clock_24h" in raw:
        out["clock_24h"] = bool(raw["clock_24h"])
    return out


# ── Typed getters (read at fetch time) ────────────────────────────────────────

def latitude() -> float:
    return float(load()["latitude"])


def longitude() -> float:
    return float(load()["longitude"])


def weather_location() -> str:
    return str(load()["weather_location"])


def temperature_unit() -> str:
    """'F' or 'C'."""
    return str(load()["temperature_unit"])


def clock_24h() -> bool:
    return bool(load()["clock_24h"])


def weather_query() -> str:
    """The location string Aura hands wttr.in: the raw weather_location verbatim
    if the user set one (city / ZIP), else the "lat,lon" pair — wttr.in accepts
    both. One coordinate pair thus drives weather + sky + planets + season."""
    data = load()
    loc = str(data["weather_location"]).strip()
    if loc:
        return loc
    return f"{data['latitude']},{data['longitude']}"


def is_southern() -> bool:
    """True below the equator — Horai inverts its month->season map when so."""
    return latitude() < 0.0


# ── Save (strict validate + atomic write) ─────────────────────────────────────

def save(
    *,
    latitude: float,
    longitude: float,
    weather_location: str = "",
    temperature_unit: str = "F",
    clock_24h: bool = False,
) -> dict[str, Any]:
    """Validate every field (raise SettingsError on a bad value), then write
    felhaven_settings.json atomically (tempfile in the same dir + os.replace, so
    a crash mid-write can't truncate it). Returns the persisted dict."""
    try:
        payload: dict[str, Any] = {
            "latitude":         _coerce_lat(latitude),
            "longitude":        _coerce_lon(longitude),
            "weather_location": str(weather_location).strip(),
            "temperature_unit": _coerce_unit(temperature_unit),
            "clock_24h":        bool(clock_24h),
        }
    except (TypeError, ValueError) as e:
        raise SettingsError(str(e)) from e

    directory = os.path.dirname(os.path.abspath(_DATA_PATH))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".themis-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, _DATA_PATH)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    log.info("themis: settings saved (%s)", payload)
    return payload


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    s = load()
    print(f"[Themis] lat={s['latitude']} lon={s['longitude']} "
          f"weather_query={weather_query()!r} unit=°{s['temperature_unit']} "
          f"clock_24h={s['clock_24h']} southern={is_southern()}")
