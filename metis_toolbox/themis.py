"""
themis.py — Keeper of Settled Law (user settings)
=================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Own the per-install user preferences: location, units, clock, and
             how Pythia answers. That is the place, the temperature unit, the
             clock format, the weather-warning switches, and Pythia's model,
             thinking and temperature. They live in felhaven_settings.json.

Contract:    §2's "Local-only ledger / persistence" flavor (like scribe.py):
             no fetch(), no TOOL_DEFINITION — plain functions plus one
             exception class. It is
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
                                    It carries Pythia's settings over untouched.
                                    A write that fails raises its OSError; the
                                    callers show only its kind. Every save
                                    below works the same way.
               pythia_thinking(), pythia_temperature()
                                 — read by pythia.py at each request, so a
                                    change applies to the next question.
                                    Temperature None = send none, and the model
                                    keeps its own default.
               save_pythia(...)  — the MACHINE SPIRIT tab's save: validates the
                                    two, keeps everything else.
               pythia_model(), save_pythia_model(name)
                                 — which Ollama model Pythia uses; None means
                                    the PYTHIA_MODEL default (pythia.py's).
                                    Saved apart from the two above, because a
                                    pick also starts a model switch.
               alerts_enabled(), alerts_popup(), alerts_read_aloud()
                                 — the Settings tab's ALERTS section: whether
                                    Iris asks the Weather Service at all,
                                    whether watches pop up as well as
                                    warnings, and whether Calliope reads them.
                                    save() takes them; one left None is kept.
               space_aurora_notice()
                                 — whether Home shows a quiet line when the
                                    aurora may be visible here (tools/eos.py,
                                    panels/eos_panel.py). Same save() rule.
               atmos_place(), save_atmos_place(area)
                                 — the place Atmospherics shows: "" for Home,
                                    else a cached Artemis area's id. Saved at
                                    once by either dropdown (Atmospherics' and
                                    Settings'), never by save(), which carries
                                    it over: a form built before a pick must
                                    not put the old one back.

Defaults:    The old hardcoded values live on here as the fail-soft fallback —
             a fresh clone with no settings file behaves exactly as before,
             and AURA_LOCATION (env) still overrides weather_query() for
             headless/CI use (see aura.py). Nothing personal is REQUIRED in
             source anymore; these are only a floor.

Threads:     load() runs on many threads: Kairos workers (horai every second;
             aura, iris, eos, hypatia, helios, selene, artemis), Pythia's
             request thread, and the Tk thread's two clocks every second. The
             saves run on the Tk thread. Windows refuses to rename over a file
             another thread has open, and refuses an open while the rename
             runs, so one lock (_FILE_LOCK) covers each read and each rename.
             Without it, a save racing a reader measured 178 failed saves in
             300, and readers that lost fell back to the defaults (Greenwich)
             for that read (2026-11-21).

Logging:     A save logs the names of the fields it changed, never their
             values: the place is the owner's location, and the EMANON tab
             shows INFO lines on screen (the owner's pick, 2026-11-21).

Upstream:    panels/themis_panel.py (the Settings tab),
             panels/machine_spirit_panel.py (Pythia's model, thinking and
             temperature), panels/aura_panel.py (the PLACE dropdown, saved
             at a pick).
             Readers: tools/aura.py, tools/hypatia.py, tools/horai.py,
             tools/helios.py, tools/selene.py, tools/iris.py, tools/eos.py,
             tools/artemis.py (the Cartograph's home fix), pythia.py,
             felhaven.py and panels/horai_panel.py (the clocks),
             panels/sidebar.py (the temperature unit), panels/iris_panel.py,
             panels/eos_panel.py.
Downstream:  felhaven_settings.json (committed? no — it is per-user state, so
             gitignored like the other machine-local JSON in §9).

Requires:    json, logging, os, re, tempfile, threading, pathlib (stdlib
             only). No tkinter — stays independently unit-testable and
             UI-free, like sphynx.py / cerberus.py.

2026-07-25:  The first audit found it honest (never-raise load, strict atomic
             save) and deleted the unused weather_location() getter
             (weather_query supersedes it; nothing read the raw field alone).

V0.03: Walked through 2026-11-21 (born 2026-07-10 with the Settings tab;
       first audited 2026-07-25). Checked every getter, coercer and save
       against the code and its readers. The rename race above is fixed
       with one lock. load() kept its never-raise promise only for the
       failures it named: a 400-digit number raised OverflowError, a
       deeply nested file RecursionError (measured); both now fall back.
       clock_24h read "false" as true (bool() of a string, the trap
       pythia_thinking's coercer names); a true/false latitude read as
       1/0; a non-text weather location went to wttr.in as "['a', 1]".
       Each now falls back, and save() refuses them. The save log names
       fields, not values. Contract names the §2 row and the OSError;
       Threads, Logging, and four readers Upstream didn't list (artemis,
       felhaven, horai_panel, sidebar).
"""

import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("METIS.themis")

# App-root anchored (§1): themis.py sits AT the app root, so the same rule
# scribe.py / cerberus.py use, not the tools/ two-dirname dance.
_DATA_PATH = Path(__file__).with_name("felhaven_settings.json")

# Held across each read of the file and each rename onto it: Windows refuses
# either while the other runs (see the module docstring's Threads).
_FILE_LOCK = threading.Lock()

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
    # How Pythia answers (the MACHINE SPIRIT tab). Both defaults reproduce
    # what Felhaven sent before these existed: thinking off, and NO temperature
    # at all, so the model keeps its own default (gemma4:e2b's is 1.0).
    "pythia_thinking":    False,
    "pythia_temperature": None,    # None -> the model's own default
    "pythia_model":       None,    # None -> pythia's PYTHIA_MODEL default
    # Weather warnings (Settings › ALERTS; tools/iris.py, panels/iris_panel.py).
    "alerts_enabled":     True,     # ask the National Weather Service at all
    "alerts_popup":       "warnings",   # or "warnings_watches"
    "alerts_read_aloud":  True,     # Calliope reads a popped-up warning
    # Space weather (tools/eos.py): False = panel only; True = Home's aurora notice too.
    "space_aurora_notice": False,
    # The place Atmospherics shows (its PLACE dropdown): "" = Home, the place
    # above; otherwise a cached Artemis area's id. Weather only: warnings, sun,
    # moon, space weather, Celestarium and Pythia all stay on Home.
    "atmos_place": "",
}

_VALID_UNITS = ("F", "C")
# Pythia's temperature, when set. 0 is the most literal; gemma4:e2b's own
# default is 1.0, and past ~1.5 its answers stop making sense.
TEMPERATURE_MIN, TEMPERATURE_MAX = 0.0, 2.0
# An Ollama model name: "gemma4:e2b", "library/llama3.2:3b", "hf.co/org/m:q4".
# Strict, because the name goes into every request Pythia sends.
_MODEL_NAME = re.compile(r"^[A-Za-z0-9._:/-]{1,200}$")
# Which alerts pop up: warnings only, or warnings and watches. Advisories never.
ALERT_POPUPS = ("warnings", "warnings_watches")
# A cached Artemis area's id, as artemis.area_id() makes it: "n51p478_e000p000".
# Strict and fullmatch'd (a `$` would let a trailing newline through), because
# the value becomes a folder name under artemis_cache/: "..", a separator or
# an absolute path must never get that far. ASCII digits only, not \d, which
# also matches other scripts' digits. Artemis keeps its own copy of this
# pattern (it imports Themis, so Themis cannot import it); a test holds the
# two in lockstep with area_id().
_AREA_ID = re.compile(r"[ns][0-9]{2}p[0-9]{3}_[ew][0-9]{3}p[0-9]{3}")


class SettingsError(ValueError):
    """save() was handed a value outside its allowed range — the panel surfaces
    this rather than persisting nonsense. load() never raises it (fail-soft)."""


# ── Coercion / load (fail-soft, per-field) ────────────────────────────────────

def _coerce_lat(value: Any) -> float:
    return _coerce_degrees(value, "latitude", 90.0)


def _coerce_lon(value: Any) -> float:
    return _coerce_degrees(value, "longitude", 180.0)


def _coerce_degrees(value: Any, key: str, limit: float) -> float:
    """A number in [-limit, limit]. A refusal names the field and why, never
    the value: a coordinate is half a location, and load() logs its refusals
    to felhaven.log, which the EMANON tab shows (§12, S24's rule for the save
    log). float()'s own message quotes the text, so it is replaced, and
    `from None` keeps it out of a traceback too."""
    # A bool is refused: float(True) is 1.0, which is no place.
    if isinstance(value, bool):
        raise ValueError(f"{key} is true or false, not a number")
    try:
        degrees = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{key} is not a number") from None
    if not -limit <= degrees <= limit:          # NaN fails this too
        raise ValueError(f"{key} out of range [{-limit:g}, {limit:g}]")
    return degrees


def _coerce_location(value: Any) -> str:
    """Text, stripped, or a whole number (a ZIP typed without quotes); str()
    of anything else would hand wttr.in "['a', 1]". The message names the
    type, not the value: a weather location is often an address."""
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if not isinstance(value, str):
        raise ValueError(f"weather_location is {type(value).__name__}, not text")
    return value.strip()


def _coerce_unit(value: Any) -> str:
    unit = str(value).strip().upper()
    if unit not in _VALID_UNITS:
        raise ValueError(f"temperature_unit {value!r} not one of {_VALID_UNITS}")
    return unit


def _coerce_thinking(value: Any) -> bool:
    # Strictly a bool: bool("false") is True, and a hand-edited file that says
    # "false" must not switch the slow mode ON.
    if not isinstance(value, bool):
        raise ValueError(f"pythia_thinking {value!r} is not true or false")
    return value


def _coerce_temperature(value: Any) -> "float | None":
    """None (the model's own default) or a number in range, kept to two
    decimals. A bool is refused: float(True) is 1.0, which is no setting."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"pythia_temperature {value!r} is not a number")
    temperature = float(value)
    if not TEMPERATURE_MIN <= temperature <= TEMPERATURE_MAX:
        raise ValueError(f"pythia_temperature {temperature} out of range "
                         f"[{TEMPERATURE_MIN}, {TEMPERATURE_MAX}]")
    return round(temperature, 2)


def _coerce_model(value: Any) -> "str | None":
    """None (the default model) or a plausible Ollama model name."""
    if value is None:
        return None
    if not isinstance(value, str) or not _MODEL_NAME.match(value.strip()):
        raise ValueError(f"pythia_model {value!r} is not an Ollama model name")
    return value.strip()


def _coerce_flag(value: Any, key: str) -> bool:
    """Strictly a bool, for the same reason as pythia_thinking."""
    if not isinstance(value, bool):
        raise ValueError(f"{key} {value!r} is not true or false")
    return value


def _coerce_popup(value: Any) -> str:
    if value not in ALERT_POPUPS:
        raise ValueError(f"alerts_popup {value!r} not one of {ALERT_POPUPS}")
    return str(value)


def _coerce_place(value: Any) -> str:
    """Home (the empty string) or the shape of a cached area's id; nothing else."""
    if value == "":
        return ""
    if not isinstance(value, str) or not _AREA_ID.fullmatch(value):
        # Not the value: an area's id is its corner's coordinates.
        raise ValueError("atmos_place is not a cached area's id")
    return value


def load() -> dict[str, Any]:
    """Return the settings dict, each field merged over DEFAULTS and coerced to
    the right type/range. A missing or garbled file — or one bad field — falls
    back to the default for that field and logs it; never raises (§4)."""
    out: dict[str, Any] = dict(DEFAULTS)
    try:
        with _FILE_LOCK, open(_DATA_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("settings root is not an object")
    except FileNotFoundError:
        return out                      # first run: silent, defaults are correct
    except (OSError, ValueError, RecursionError) as e:
        # ValueError covers JSONDecodeError and a file that isn't UTF-8;
        # RecursionError, a file nested deeper than the parser goes.
        log.warning("themis: settings file unreadable, using defaults: %s", e)
        return out

    # Per-field: a single bad value degrades only itself, not the whole file.
    for key, coerce in (
        ("latitude",  _coerce_lat),
        ("longitude", _coerce_lon),
        ("weather_location", _coerce_location),
        ("temperature_unit", _coerce_unit),
        ("clock_24h", lambda v: _coerce_flag(v, "clock_24h")),
        ("pythia_thinking", _coerce_thinking),
        ("pythia_temperature", _coerce_temperature),
        ("pythia_model", _coerce_model),
        ("alerts_enabled", lambda v: _coerce_flag(v, "alerts_enabled")),
        ("alerts_popup", _coerce_popup),
        ("alerts_read_aloud", lambda v: _coerce_flag(v, "alerts_read_aloud")),
        ("space_aurora_notice", lambda v: _coerce_flag(v, "space_aurora_notice")),
        ("atmos_place", _coerce_place),
    ):
        if key in raw:
            try:
                out[key] = coerce(raw[key])
            except (TypeError, ValueError, OverflowError) as e:
                # OverflowError: float() of an integer too big for a float.
                log.warning("themis: bad %s in settings, using default: %s", key, e)
    return out


# ── Typed getters (read at fetch time) ────────────────────────────────────────

def latitude() -> float:
    return float(load()["latitude"])


def longitude() -> float:
    return float(load()["longitude"])


def temperature_unit() -> str:
    """'F' or 'C'."""
    return str(load()["temperature_unit"])


def clock_24h() -> bool:
    return bool(load()["clock_24h"])


def pythia_thinking() -> bool:
    """Whether Pythia reasons before she answers (slower, more careful)."""
    return bool(load()["pythia_thinking"])


def pythia_temperature() -> "float | None":
    """Pythia's sampling temperature, or None for the model's own default."""
    value = load()["pythia_temperature"]
    return None if value is None else float(value)


def pythia_model() -> "str | None":
    """The Ollama model the owner picked, or None for pythia's default."""
    value = load()["pythia_model"]
    return None if value is None else str(value)


def alerts_enabled() -> bool:
    """Whether Iris asks the National Weather Service for warnings at all."""
    return bool(load()["alerts_enabled"])


def alerts_popup() -> str:
    """'warnings' or 'warnings_watches': which alerts pop up."""
    return str(load()["alerts_popup"])


def alerts_read_aloud() -> bool:
    """Whether Calliope reads a popped-up warning aloud."""
    return bool(load()["alerts_read_aloud"])


def space_aurora_notice() -> bool:
    """Whether Home shows a quiet notice when the aurora may be visible here."""
    return bool(load()["space_aurora_notice"])


def atmos_place() -> str:
    """The place Atmospherics shows: "" for Home, else a cached area's id. It
    may name an area since deleted; Aura says so and shows Home."""
    return str(load()["atmos_place"])


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
    alerts_enabled: "bool | None" = None,
    alerts_popup: "str | None" = None,
    alerts_read_aloud: "bool | None" = None,
    space_aurora_notice: "bool | None" = None,
) -> dict[str, Any]:
    """Validate every field (raise SettingsError on a bad value), then write
    felhaven_settings.json atomically (tempfile in the same dir + os.replace, so
    a crash mid-write can't truncate it). Returns the persisted dict.

    The Settings tab's save. Pythia's settings are carried over as they are,
    because this form doesn't show them and must not reset them; so is each
    alerts setting left as None (a caller that doesn't show them)."""
    current = load()
    try:
        payload: dict[str, Any] = {
            "latitude":         _coerce_lat(latitude),
            "longitude":        _coerce_lon(longitude),
            "weather_location": _coerce_location(weather_location),
            "temperature_unit": _coerce_unit(temperature_unit),
            "clock_24h":        _coerce_flag(clock_24h, "clock_24h"),
            "alerts_enabled":   current["alerts_enabled"] if alerts_enabled is None
                                else _coerce_flag(alerts_enabled, "alerts_enabled"),
            "alerts_popup":     current["alerts_popup"] if alerts_popup is None
                                else _coerce_popup(alerts_popup),
            "alerts_read_aloud": current["alerts_read_aloud"] if alerts_read_aloud is None
                                 else _coerce_flag(alerts_read_aloud, "alerts_read_aloud"),
            "space_aurora_notice": (current["space_aurora_notice"]
                                    if space_aurora_notice is None
                                    else _coerce_flag(space_aurora_notice,
                                                      "space_aurora_notice")),
        }
    except (TypeError, ValueError) as e:
        raise SettingsError(str(e)) from e
    payload["pythia_thinking"] = current["pythia_thinking"]
    payload["pythia_temperature"] = current["pythia_temperature"]
    payload["pythia_model"] = current["pythia_model"]
    payload["atmos_place"] = current["atmos_place"]
    return _write(payload)


def save_pythia(*, thinking: bool, temperature: "float | None") -> dict[str, Any]:
    """Validate Pythia's two settings (raise SettingsError on a bad value) and
    write them, keeping every other setting as it is. The MACHINE SPIRIT tab's
    save; it applies to her next question, because pythia.py reads these at
    each request. Returns the persisted dict."""
    try:
        thinking = _coerce_thinking(thinking)
        temperature = _coerce_temperature(temperature)
    except (TypeError, ValueError) as e:
        raise SettingsError(str(e)) from e
    payload = load()
    payload["pythia_thinking"] = thinking
    payload["pythia_temperature"] = temperature
    return _write(payload)


def save_pythia_model(name: "str | None") -> dict[str, Any]:
    """Validate and save the model Pythia uses (None: back to the default),
    keeping every other setting. The MACHINE SPIRIT tab's MODEL row; the
    switch itself (unload the old, load the new) is pythia.switch_model()'s.
    Returns the persisted dict."""
    try:
        name = _coerce_model(name)
    except (TypeError, ValueError) as e:
        raise SettingsError(str(e)) from e
    payload = load()
    payload["pythia_model"] = name
    return _write(payload)


def save_atmos_place(area: str) -> dict[str, Any]:
    """Validate and save the place Atmospherics shows ("" for Home), keeping
    every other setting. Both PLACE dropdowns call this the moment a place is
    picked. Returns the persisted dict."""
    try:
        area = _coerce_place(area)
    except (TypeError, ValueError) as e:
        raise SettingsError(str(e)) from e
    payload = load()
    payload["atmos_place"] = area
    return _write(payload)


def _write(payload: dict[str, Any]) -> dict[str, Any]:
    """Write felhaven_settings.json atomically: tempfile in the same folder,
    then os.replace, so a crash mid-write can't truncate it. The rename runs
    under _FILE_LOCK (see Threads). Logs the names of the fields that changed,
    never their values (see Logging)."""
    before = load()
    directory = os.path.dirname(os.path.abspath(_DATA_PATH))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".themis-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        with _FILE_LOCK:
            os.replace(tmp, _DATA_PATH)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    changed = [key for key in payload if payload[key] != before.get(key)]
    log.info("themis: settings saved (changed: %s)", ", ".join(changed) or "nothing")
    return payload


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    s = load()
    print(f"[Themis] lat={s['latitude']} lon={s['longitude']} "
          f"weather_query={weather_query()!r} unit=°{s['temperature_unit']} "
          f"clock_24h={s['clock_24h']} southern={is_southern()}")
    print(f"[Themis] pythia_thinking={s['pythia_thinking']} "
          f"pythia_temperature={s['pythia_temperature']} "
          f"(None = the model's own default)")
