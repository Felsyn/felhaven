"""
helios.py — Titan of the Sun
============================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Report sunrise, sunset, golden-hour windows, and day length.
             Interprets Aura's astronomy dict into display-ready strings.

Contract:    Pure functions. No network, no state file, no clock-reading.
             interpret() takes Aura's astronomy dict and returns display
             strings, or None when sunrise/sunset can't be parsed.
             Nothing here raises.

             The get_sun_times LLM tool (TOOL_DEFINITION + handle) is bolted on
             below: handle() sources today's astronomy from Aura, then reuses
             interpret() to shape the answer. The pure functions stay the core;
             the contract is a thin wrapper over them.

Upstream:    tools/aura.py (the astronomy dict it hands back from wttr.in)
Downstream:  panels/aura_panel.py → HeliosWidget (display surface)

Requires:    stdlib only (datetime)

Note on golden hours: the ±1h window is a deliberate heuristic, not a
placeholder. Real solar-elevation math (the sun within 6° of the horizon)
needs latitude and date; that is out of scope and out of proportion for a
glanceable dashboard row. Do not "upgrade" it.
"""

from datetime import date, datetime, time, timedelta
from typing import Any

# Arbitrary anchor date — time objects can't be added to a timedelta directly,
# so we combine onto a date, shift, then drop back to a time.
_ANCHOR = date(2000, 1, 1)

# wttr.in clock format, e.g. "05:52 AM".
_CLOCK_FMT = "%I:%M %p"


# ── Internals ─────────────────────────────────────────────────────────────────

def _shift(t: time, minutes: int) -> time:
    """Return t moved by `minutes` (may be negative), as a time."""
    return (datetime.combine(_ANCHOR, t) + timedelta(minutes=minutes)).time()


def _pretty(t: time) -> str:
    """time(5, 52) -> '5:52 AM'  (strip the leading zero wttr pads on the hour)."""
    return t.strftime(_CLOCK_FMT).lstrip("0")


def _pretty_range(start: time, end: time) -> str:
    """
    Format a golden-hour window. When both ends share a meridiem (the normal
    case — golden hour doesn't straddle noon), show it once at the end:
        time(5, 52), time(6, 52) -> '5:52 – 6:52 AM'
    If they somehow differ, keep both meridiems rather than lie.
    """
    start_str, end_str = _pretty(start), _pretty(end)
    start_clock, start_mer = start_str.rsplit(" ", 1)
    _, end_mer = end_str.rsplit(" ", 1)
    if start_mer == end_mer:
        return f"{start_clock} – {end_str}"
    return f"{start_str} – {end_str}"


# ── Public API ────────────────────────────────────────────────────────────────

def parse_clock(s: str) -> time | None:
    """
    '05:52 AM' -> time(5, 52). Returns None for '', 'No sunrise', 'No sunset',
    or any unparseable input. Never raises.
    """
    try:
        return datetime.strptime((s or "").strip(), _CLOCK_FMT).time()
    except (ValueError, TypeError):
        return None


def golden_hours(sunrise: time, sunset: time) -> dict[str, Any]:
    """
    ±1h heuristic around sunrise and sunset.
        {"am_start": sunrise,      "am_end": sunrise + 1h,
         "pm_start": sunset - 1h,  "pm_end": sunset}
    """
    return {
        "am_start": sunrise,
        "am_end":   _shift(sunrise, 60),
        "pm_start": _shift(sunset, -60),
        "pm_end":   sunset,
    }


def day_length(sunrise: time, sunset: time) -> str:
    """Daylight span as '14h 53m'."""
    delta = datetime.combine(_ANCHOR, sunset) - datetime.combine(_ANCHOR, sunrise)
    minutes = int(delta.total_seconds() // 60)
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m"


def interpret(astro: dict[str, Any]) -> dict[str, Any] | None:
    """
    Top-level entry the widget calls. Takes Aura's astronomy dict and returns
    display-ready strings, or None if sunrise/sunset are unparseable (e.g. a
    polar 'No sunrise' day) or the dict is empty.

        {"sunrise": "5:52 AM", "sunset": "8:45 PM",
         "golden_am": "5:52 – 6:52 AM", "golden_pm": "7:45 – 8:45 PM",
         "day_length": "14h 53m"}
    """
    if not astro:
        return None
    sunrise = parse_clock(astro.get("sunrise", ""))
    sunset  = parse_clock(astro.get("sunset", ""))
    if sunrise is None or sunset is None:
        return None
    g = golden_hours(sunrise, sunset)
    return {
        "sunrise":    _pretty(sunrise),
        "sunset":     _pretty(sunset),
        "golden_am":  _pretty_range(g["am_start"], g["am_end"]),
        "golden_pm":  _pretty_range(g["pm_start"], g["pm_end"]),
        "day_length": day_length(sunrise, sunset),
    }


# ── LLM tool contract ─────────────────────────────────────────────────────────
# Helios is a pure interpreter; the handle sources today's astronomy from Aura
# (its documented upstream) and shapes the solar-timing answer.

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "get_sun_times",
        "description": (
            "Returns today's sunrise, sunset, the morning and evening golden-hour "
            "windows, and the total length of daylight for the dashboard's "
            "location. Call when the user asks about sunrise, sunset, daylight, "
            "golden hour, or how long the day is."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def handle() -> dict[str, Any]:
    """Toolbox entry: fetch today's astronomy via Aura, interpret the solar
    timing. Never raises — a network failure or unparseable times degrade to
    an error dict the model can relay."""
    from tools import aura        # lazy: keeps this module importable standalone
    weather = aura.handle()
    if "error" in weather:
        return {"error": "sun_times_unavailable"}
    info = interpret(weather.get("astronomy", {}))
    if info is None:
        return {"error": "sun_times_unavailable"}
    return info


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    sample = {
        "sunrise": "05:52 AM", "sunset": "08:45 PM",
        "moonrise": "02:31 AM", "moonset": "03:18 PM",
        "moon_phase": "Waning Crescent", "moon_illumination": "32",
    }
    info = interpret(sample)
    if info is None:
        print("[Helios] sun times unavailable")
    else:
        print(
            f"[Helios] sunrise {info['sunrise']} | sunset {info['sunset']} | "
            f"golden AM {info['golden_am']} | golden PM {info['golden_pm']} | "
            f"day {info['day_length']}"
        )
