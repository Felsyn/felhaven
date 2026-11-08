"""
helios.py — Titan of the Sun
============================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Report sunrise, sunset, golden-hour windows, and day length.
             Computes them from the Settings location and this computer's
             clock. No network.

Contract:    Polled + brain tool, the Horai shape: TOOL_DEFINITION, handle(),
             and fetch = handle for Kairos. Both take no arguments and return a
             dict. fetch() never raises, deliberately (§2 asks why). There is no
             I/O to fail. A polar day or night has no times, but that is a fact
             to report, not a failure for Kairos's retry ladder. An unexpected
             math error is logged and becomes the error slug, as in any handle().

             compute(lat, lon, when) is the pure core that handle() wraps, and
             the thing the tests pin: same inputs, same answer, no clock.

             The panel (HeliosWidget, via Kairos's `helios` worker) and the
             get_sun_times tool call the same function, so they cannot
             disagree. There is no `as_of`. That dates a *fetched* reading (T7),
             and nothing is fetched here, so the answer is as old as the call.

Source:      The NOAA Solar Calculator's equations (after Meeus, Astronomical
             Algorithms, 2nd ed., ch. 25 and 28): the sun's apparent longitude,
             its declination and the equation of time. From those, the hour
             angle at which the sun's centre sits 0.833° below the horizon (34'
             of refraction plus a 16' semidiameter, the almanac convention),
             solved again at each event. It matches the U.S. Naval
             Observatory's almanac to the minute on every date in
             tests/test_helios.py.

             Not Kepler, though Earth's elements there would give the sun too.
             Kepler documents ~1° accuracy in the J2000 frame and asks not to be
             improved. That suits a sky chart, but 1° is ~4 minutes of sunrise.

Upstream:    themis.py (latitude/longitude and the 24-hour clock, read per
             call), kairos.py (fetch),
             tools/hypatia.py (_julian_date, the one conversion to JD)
Downstream:  panels/aura_panel.py → HeliosWidget; tools/selene.py (reads the
             sun's longitude from _sun_position for the moon's phase)

Requires:    stdlib only (math, datetime, logging)

Time zone:   The times are in THIS COMPUTER's zone, as in Horai and
             pythia._now(). wttr.in used the location's own zone, but stdlib
             Python on Windows has no time-zone database (zoneinfo needs the
             tzdata package, §11), so offline that zone cannot be known. This
             only shows when the Settings location is in another zone from the
             PC. Then a sunset can even land after midnight on this clock, and
             day_length() allows for that.

Note on golden hours: the ±1h window is a deliberate heuristic, not a
placeholder. The note used to say real solar-elevation math was out of reach
without latitude and date. That is no longer true, so the reason now is this:
"the sun within 6° of the horizon" would change what the row MEANS, not just
how it is computed. That is a decision for its own change. Do not "upgrade" it
in passing.

V0.02:       Audited 2026-07-25 — tag-only. Pure interpreter verified never-raise
             (interpret / parse_clock / golden_hours / day_length, and handle()
             degrades to the slug sun_times_unavailable). Thorough existing
             coverage (test_helios + test_tool_handles); no dead code — the public
             primitives are directly-tested seams.

2026-09-28:  computes the sun locally (V0.03 audit M3). It used to interpret
             Aura's astronomy dict, so no network meant no sunset, and the tool
             could only be as fresh as the weather. interpret() and parse_clock()
             went with that dict, since nothing else read wttr.in's clock strings.

2026-10-31:  writes its times on the 24-hour clock when Settings picks it, as
             Horai does. They were always 12-hour.
"""

import logging
import math
import os
import sys
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

# Helios reaches UP to app-root themis.py for the location, and across to
# hypatia for _julian_date. Guarded-path pattern (callimachus.py) for a bare
# `python tools/helios.py`. Kairos, tests and felhaven already have it on path.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import themis
from tools import hypatia

log = logging.getLogger("METIS.helios")

# Arbitrary anchor date — time objects can't be added to a timedelta directly,
# so we combine onto a date, shift, then drop back to a time.
_ANCHOR = date(2000, 1, 1)

# Display clocks, e.g. "5:52 AM" (the leading zero is stripped in _pretty), or
# "05:52" when Settings picks the 24-hour clock (Horai's format).
_CLOCK_FMT = "%I:%M %p"
_CLOCK_FMT_24H = "%H:%M"

# The sun's centre at sunrise/sunset, in degrees of altitude: 34' of refraction
# plus its 16' semidiameter below the geometric horizon.
_SUN_H0 = -0.833

# Polar details, after the stable slug (§2: the slug is the contract, the
# detail is for the human or the model to read).
_NEVER_RISES = "the sun does not rise today"
_NEVER_SETS = "the sun does not set today"


# ── The sun ───────────────────────────────────────────────────────────────────

def _sin(deg: float) -> float:
    return math.sin(math.radians(deg))


def _cos(deg: float) -> float:
    return math.cos(math.radians(deg))


def _sun_position(jd: float) -> tuple[float, float, float]:
    """(apparent ecliptic longitude°, declination°, equation of time in
    minutes) at Julian Day `jd`: the NOAA Solar Calculator's equations. Selene
    reuses the longitude for the moon's elongation."""
    t = (jd - 2451545.0) / 36525.0                         # Julian centuries since J2000
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0   # mean longitude
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)      # mean anomaly
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)  # orbit eccentricity
    centre = (_sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t))
              + _sin(2 * m) * (0.019993 - 0.000101 * t)
              + _sin(3 * m) * 0.000289)
    omega = 125.04 - 1934.136 * t
    lam = l0 + centre - 0.00569 - 0.00478 * _sin(omega)    # apparent longitude
    eps0 = 23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0
    eps = eps0 + 0.00256 * _cos(omega)                     # obliquity, corrected
    dec = math.degrees(math.asin(_sin(eps) * _sin(lam)))
    y = math.tan(math.radians(eps / 2.0)) ** 2
    eot = 4.0 * math.degrees(
        y * _sin(2 * l0) - 2 * e * _sin(m) + 4 * e * y * _sin(m) * _cos(2 * l0)
        - 0.5 * y * y * _sin(4 * l0) - 1.25 * e * e * _sin(2 * m))
    return lam % 360.0, dec, eot


def _cos_hour_angle(lat: float, dec: float) -> float:
    """cos of the hour angle at which the sun's centre stands at _SUN_H0.
    Above 1 it never climbs that high today; below -1 it never sinks that low."""
    return (_sin(_SUN_H0) - _sin(lat) * _sin(dec)) / (_cos(lat) * _cos(dec))


def _transit(lon: float, eot: float, near: float) -> float:
    """Unix time of the sun's transit (solar noon) nearest `near`."""
    day0 = math.floor(near / 86400.0) * 86400.0            # 00:00 UTC of that UTC date
    t = day0 + (720.0 - 4.0 * lon - eot) * 60.0            # 4 min of time per degree
    return t + 86400.0 * round((near - t) / 86400.0)


def _event(lat: float, lon: float, noon: float, rising: bool) -> float | None:
    """Unix time of the sunrise (or sunset) around the transit nearest `noon`,
    or None when the sun does not cross _SUN_H0. Solved at noon, then solved
    again at that first answer, since the declination and the equation of time
    move through the day."""
    t = noon
    for _ in range(2):
        _, dec, eot = _sun_position(hypatia._julian_date(t))
        cos_h = _cos_hour_angle(lat, dec)
        if not -1.0 <= cos_h <= 1.0:
            return None
        half_day = math.degrees(math.acos(cos_h)) * 240.0  # degrees → seconds of time
        transit = _transit(lon, eot, noon)
        t = transit - half_day if rising else transit + half_day
    return t


def _clock(unix: float, tz: tzinfo | None) -> time:
    """A unix time as a clock time in `tz` (None = this computer's zone),
    rounded to the NEAREST minute. The almanac rounds; strftime alone would
    truncate, and 05:55:39 would read 5:55."""
    at = datetime.fromtimestamp(unix, tz) + timedelta(seconds=30)
    return at.replace(second=0, microsecond=0).time()


# ── Display helpers ───────────────────────────────────────────────────────────

def _shift(t: time, minutes: int) -> time:
    """Return t moved by `minutes` (may be negative), as a time."""
    return (datetime.combine(_ANCHOR, t) + timedelta(minutes=minutes)).time()


def _pretty(t: time, clock_24h: bool = False) -> str:
    """time(5, 52) -> '5:52 AM'  (no leading zero on the hour), or '05:52' on
    the 24-hour clock, where the zero stays (lstrip would turn 00:30 into
    ':30')."""
    if clock_24h:
        return t.strftime(_CLOCK_FMT_24H)
    return t.strftime(_CLOCK_FMT).lstrip("0")


def _pretty_range(start: time, end: time, clock_24h: bool = False) -> str:
    """
    Format a golden-hour window. When both ends share a meridiem (the normal
    case — golden hour doesn't straddle noon), show it once at the end:
        time(5, 52), time(6, 52) -> '5:52 – 6:52 AM'
    If they somehow differ, keep both meridiems rather than lie. The 24-hour
    clock has no meridiem: '05:52 – 06:52'.
    """
    if clock_24h:
        return f"{_pretty(start, True)} – {_pretty(end, True)}"
    start_str, end_str = _pretty(start), _pretty(end)
    start_clock, start_mer = start_str.rsplit(" ", 1)
    _, end_mer = end_str.rsplit(" ", 1)
    if start_mer == end_mer:
        return f"{start_clock} – {end_str}"
    return f"{start_str} – {end_str}"


# ── Public API ────────────────────────────────────────────────────────────────

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
    """Daylight span as '14h 53m'. A sunset earlier on the clock than the
    sunrise fell after midnight in this zone (see "Time zone"), so it counts
    as the next day rather than going negative."""
    delta = datetime.combine(_ANCHOR, sunset) - datetime.combine(_ANCHOR, sunrise)
    if delta < timedelta(0):
        delta += timedelta(days=1)
    minutes = int(delta.total_seconds() // 60)
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m"


def compute(lat: float, lon: float, when: datetime, *,
            clock_24h: bool = False) -> dict[str, Any]:
    """
    The sun on `when`'s date at (lat, lon), as display strings:

        {"sunrise": "5:56 AM", "sunset": "5:45 PM",
         "golden_am": "5:56 – 6:56 AM", "golden_pm": "4:45 – 5:45 PM",
         "day_length": "11h 49m"}

    or, on a polar day or night, the §2 error dict whose detail says which.
    With `clock_24h` the times read "05:56", "17:45", "05:56 – 06:56". It is
    an argument, not a Themis read, so compute() stays pure; handle() reads
    the setting.

    `when` sets the date and the zone the times are shown in, never the hour:
    a naive datetime is this computer's zone (the app passes datetime.now()),
    an aware one is its own zone (the tests pin USNO values in USNO's zone).
    Golden hours and day length are worked from the ROUNDED times, so the
    row always adds up to what it shows.
    """
    tz = when.tzinfo
    noon = datetime.combine(when.date(), time(12), tzinfo=tz).timestamp()
    rise = _event(lat, lon, noon, rising=True)
    set_ = _event(lat, lon, noon, rising=False)
    if rise is None or set_ is None:
        _, dec, _ = _sun_position(hypatia._julian_date(noon))
        why = _NEVER_RISES if _cos_hour_angle(lat, dec) > 0 else _NEVER_SETS
        return {"error": f"sun_times_unavailable: {why}"}
    sunrise, sunset = _clock(rise, tz), _clock(set_, tz)
    g = golden_hours(sunrise, sunset)
    return {
        "sunrise":    _pretty(sunrise, clock_24h),
        "sunset":     _pretty(sunset, clock_24h),
        "golden_am":  _pretty_range(g["am_start"], g["am_end"], clock_24h),
        "golden_pm":  _pretty_range(g["pm_start"], g["pm_end"], clock_24h),
        "day_length": day_length(sunrise, sunset),
    }


# ── LLM tool contract ─────────────────────────────────────────────────────────

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


def _now() -> datetime:
    """Injectable clock seam, aura._now()'s convention: naive LOCAL time, the
    clock on the wall. compute() reads a naive datetime as this zone."""
    return datetime.now()


def handle() -> dict[str, Any]:
    """Toolbox entry, and Kairos's (fetch = handle): today's sun for the
    Settings location, on the Settings clock. Never raises: a polar day or
    night comes back as the slug with its reason, and an unexpected failure
    as the slug with the exception's type name, logged."""
    try:
        return compute(themis.latitude(), themis.longitude(), _now(),
                       clock_24h=themis.clock_24h())
    except Exception as e:
        log.error(f"Helios: sun computation failed: {type(e).__name__}")
        return {"error": f"sun_times_unavailable: {type(e).__name__}"}


fetch = handle  # Kairos entry point: the same answer the model gets, no I/O


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    info = handle()
    if "error" in info:
        print(f"[Helios] {info['error']}")
    else:
        print(
            f"[Helios] sunrise {info['sunrise']} | sunset {info['sunset']} | "
            f"golden AM {info['golden_am']} | golden PM {info['golden_pm']} | "
            f"day {info['day_length']}"
        )
