"""
helios.py — Titan of the Sun
============================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Report sunrise, sunset, golden-hour windows, and day length.
             Computes them from the Settings location and this computer's
             clock. No network.

Contract:    §2's dashboard-only watcher: fetch() for Kairos and no
             TOOL_DEFINITION (since 2026-11-14). fetch is handle(), and Ptolemy
             hands that same handle() to Pythia as part of get_sky_report.
             Both take no arguments and return a dict. fetch() never raises,
             deliberately (§2 asks why). There is no I/O to fail. A polar day
             or night has no times, but that is a fact to report, not a failure
             for Kairos's retry ladder: it comes back as `unavailable: ` and the
             reason. An unexpected math error is logged and comes back as
             `unavailable: sun computation raised <class>`.

             compute(lat, lon, when) is the pure core that handle() wraps, and
             the thing the tests pin: same inputs, same answer, no clock.

             The panel (HeliosWidget, via Kairos's `helios` worker) and the
             get_sky_report tool (through Ptolemy) call the same function, so
             they cannot disagree. There is no `as_of`. That dates a *fetched*
             reading (T7), and nothing is fetched here, so the answer is as old
             as the call.

Source:      The NOAA Solar Calculator's equations (after Meeus, Astronomical
             Algorithms, 2nd ed., ch. 25 and 28): the sun's apparent longitude,
             its declination and the equation of time. From those, the hour
             angle at which the sun's centre sits 0.833° below the horizon (34'
             of refraction plus a 16' semidiameter, the almanac convention),
             solved again at each event. It matches the U.S. Naval
             Observatory's almanac to the minute on every date in
             tests/test_helios.py. The golden hours solve the same hour angle
             at -4° and +6° (see "Golden hours"), and match JPL Horizons'
             elevations to the minute there too.

             Not Kepler, though Earth's elements there would give the sun too.
             Kepler documents ~1° accuracy in the J2000 frame and asks not to be
             improved. That suits a sky chart, but 1° is ~4 minutes of sunrise.

Upstream:    kairos.py (the `helios` worker calls fetch every 300 s),
             tools/ptolemy.py (handle, as get_sky_report), tools/selene.py
             (_sun_position for the moon's phase; _clock and _pretty for its
             times)
Downstream:  themis.py (latitude/longitude and the 24-hour clock, read per
             call), tools/hypatia.py (_julian_date, the one conversion to JD),
             panels/aura_panel.py → HeliosWidget (Kairos hands it fetch()'s
             answer)

Requires:    themis.py (app root), tools/hypatia.py; logging, math, os, sys,
             datetime, typing (stdlib)

Time zone:   The times are in THIS COMPUTER's zone, as in Horai and
             pythia._now(). wttr.in used the location's own zone, but stdlib
             Python on Windows has no time-zone database (zoneinfo needs the
             tzdata package, §11), so offline that zone cannot be known. This
             only shows when the Settings location is in another zone from the
             PC. Then a sunset can even land after midnight on this clock, and
             day_length() allows for that.

Golden hours: while the sun's centre is between 4° below the horizon and 6°
             above it, true (airless) altitude, as photography almanacs count
             it (the owner's pick, CONVENTIONS §12, 2026-11-20). It used to be
             a plain hour after sunrise and before sunset, which at
             mid-latitudes began 15 to 28 minutes late in the morning, and in
             the evening ended that much early. Above about 60°, near a
             solstice, the sun may never climb to 6°, so the morning and
             evening windows meet at solar noon; or never sink to -4°, so they
             meet at solar midnight.

2026-07-25:  walkthrough audit — tag-only. Pure interpreter verified never-raise
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

2026-11-14:  TOOL_DEFINITION removed. get_sun_times is retired; Pythia reaches
             handle() through Ptolemy's get_sky_report, with Selene and Hypatia
             (CONVENTIONS §12).

V0.03:       Walked through 2026-11-20 (the V0.03 walkthrough, CONVENTIONS
             §3/§12). In the repo since 2026-06-10 as wttr.in's interpreter,
             local since 2026-09-28. Checked: every USNO date in the tests
             still matches to the minute; never-raise at the poles and the
             date line. Changed:
             the golden hours follow the sun's angle (see "Golden hours");
             golden_hours() takes the place and works out the four crossings,
             and the dead _shift() went with the plain hour; both errors say
             the shared `unavailable` (§2) where they said
             sun_times_unavailable; the Contract names its §2 row; Upstream and
             Downstream swapped where they were crossed; Requires names the
             two modules it imports.
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

# Arbitrary anchor date — time objects can't be subtracted directly, so
# day_length() combines both onto one date first.
_ANCHOR = date(2000, 1, 1)

# Display clocks, e.g. "5:52 AM" (the leading zero is stripped in _pretty), or
# "05:52" when Settings picks the 24-hour clock (Horai's format).
_CLOCK_FMT = "%I:%M %p"
_CLOCK_FMT_24H = "%H:%M"

# The sun's centre at sunrise/sunset, in degrees of altitude: 34' of refraction
# plus its 16' semidiameter below the geometric horizon.
_SUN_H0 = -0.833

# Golden hour's ends, in degrees of the sun centre's true altitude (see
# "Golden hours"). No refraction: below the horizon there is none to see, and
# JPL Horizons' airless elevations, which the tests check against, leave it out.
_GOLDEN_LOW = -4.0
_GOLDEN_HIGH = 6.0

# Half a day, in seconds: solar midnight lies this far either side of noon.
_HALF_DAY = 43200.0

# Polar details, after the stable slug (§2: the slug is the contract, the
# detail is for the human or the model to read). Public because HeliosWidget
# shows them in place of "unavailable".
NEVER_RISES = "the sun does not rise today"
NEVER_SETS = "the sun does not set today"


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


def _cos_hour_angle(lat: float, dec: float, h0: float = _SUN_H0) -> float:
    """cos of the hour angle at which the sun's centre stands at altitude h0
    (sunrise's _SUN_H0 unless told otherwise). Above 1 it never climbs that
    high today; below -1 it never sinks that low."""
    return (_sin(h0) - _sin(lat) * _sin(dec)) / (_cos(lat) * _cos(dec))


def _transit(lon: float, eot: float, near: float) -> float:
    """Unix time of the sun's transit (solar noon) nearest `near`."""
    day0 = math.floor(near / 86400.0) * 86400.0            # 00:00 UTC of that UTC date
    t = day0 + (720.0 - 4.0 * lon - eot) * 60.0            # 4 min of time per degree
    return t + 86400.0 * round((near - t) / 86400.0)


def _event(lat: float, lon: float, noon: float, rising: bool,
           h0: float = _SUN_H0) -> float | None:
    """Unix time the sun's centre crosses altitude h0, rising or setting,
    around the transit nearest `noon` (by default the sunrise or sunset), or
    None when it does not cross. Solved at noon, then solved again at that
    first answer, since the declination and the equation of time move
    through the day."""
    t = noon
    for _ in range(2):
        _, dec, eot = _sun_position(hypatia._julian_date(t))
        cos_h = _cos_hour_angle(lat, dec, h0)
        if not -1.0 <= cos_h <= 1.0:
            return None
        half_day = math.degrees(math.acos(cos_h)) * 240.0  # degrees → seconds of time
        transit = _transit(lon, eot, noon)
        t = transit - half_day if rising else transit + half_day
    return t


def _golden_edge(lat: float, lon: float, noon: float, rising: bool,
                 h0: float) -> float:
    """Unix time of one end of a golden hour: the sun crossing h0, rising or
    setting, around the transit nearest `noon`. When it never climbs to h0
    today the end is solar noon, and when it never sinks to h0 it is solar
    midnight on that side, so the morning and evening windows meet there
    rather than vanish."""
    t = _event(lat, lon, noon, rising, h0)
    if t is not None:
        return t
    _, dec, eot = _sun_position(hypatia._julian_date(noon))
    transit = _transit(lon, eot, noon)
    if _cos_hour_angle(lat, dec, h0) > 0:          # never climbs to h0
        return transit
    return transit - _HALF_DAY if rising else transit + _HALF_DAY


def _clock(unix: float, tz: tzinfo | None) -> time:
    """A unix time as a clock time in `tz` (None = this computer's zone),
    rounded to the NEAREST minute. The almanac rounds; strftime alone would
    truncate, and 05:55:39 would read 5:55."""
    at = datetime.fromtimestamp(unix, tz) + timedelta(seconds=30)
    return at.replace(second=0, microsecond=0).time()


# ── Display helpers ───────────────────────────────────────────────────────────

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
    case), show it once at the end:
        time(5, 52), time(6, 52) -> '5:52 – 6:52 AM'
    Far from the equator a window can reach solar noon or midnight, and then
    both meridiems stay: '9:34 AM – 12:58 PM'. The 24-hour clock has no
    meridiem: '05:52 – 06:52'.
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

def golden_hours(lat: float, lon: float, noon: float) -> dict[str, float]:
    """
    The day's two golden hours at (lat, lon), as unix times, around the
    transit nearest `noon` (see "Golden hours"):
        {"am_start": rising through -4°,   "am_end": rising through +6°,
         "pm_start": setting through +6°,  "pm_end": setting through -4°}
    An end the sun never reaches is solar noon or midnight (_golden_edge).
    """
    return {
        "am_start": _golden_edge(lat, lon, noon, True, _GOLDEN_LOW),
        "am_end":   _golden_edge(lat, lon, noon, True, _GOLDEN_HIGH),
        "pm_start": _golden_edge(lat, lon, noon, False, _GOLDEN_HIGH),
        "pm_end":   _golden_edge(lat, lon, noon, False, _GOLDEN_LOW),
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
         "golden_am": "5:35 – 6:40 AM", "golden_pm": "5:01 – 6:05 PM",
         "day_length": "11h 49m"}

    or, on a polar day or night, {"error": "unavailable: <NEVER_RISES or
    NEVER_SETS>"}. With `clock_24h` the times read "05:56", "17:45",
    "05:35 – 06:40". It is an argument, not a Themis read, so compute() stays
    pure; handle() reads the setting.

    `when` sets the date and the zone the times are shown in, never the hour:
    a naive datetime is this computer's zone (the app passes datetime.now()),
    an aware one is its own zone (the tests pin USNO values in USNO's zone).
    Day length is worked from the ROUNDED sunrise and sunset, so it always
    matches the two times shown.
    """
    tz = when.tzinfo
    noon = datetime.combine(when.date(), time(12), tzinfo=tz).timestamp()
    rise = _event(lat, lon, noon, rising=True)
    set_ = _event(lat, lon, noon, rising=False)
    if rise is None or set_ is None:
        _, dec, _ = _sun_position(hypatia._julian_date(noon))
        why = NEVER_RISES if _cos_hour_angle(lat, dec) > 0 else NEVER_SETS
        return {"error": f"unavailable: {why}"}
    sunrise, sunset = _clock(rise, tz), _clock(set_, tz)
    g = {end: _clock(t, tz) for end, t in golden_hours(lat, lon, noon).items()}
    return {
        "sunrise":    _pretty(sunrise, clock_24h),
        "sunset":     _pretty(sunset, clock_24h),
        "golden_am":  _pretty_range(g["am_start"], g["am_end"], clock_24h),
        "golden_pm":  _pretty_range(g["pm_start"], g["pm_end"], clock_24h),
        "day_length": day_length(sunrise, sunset),
    }


# ── LLM tool contract ─────────────────────────────────────────────────────────

def _now() -> datetime:
    """Injectable clock seam, aura._now()'s convention: naive LOCAL time, the
    clock on the wall. compute() reads a naive datetime as this zone."""
    return datetime.now()


def handle() -> dict[str, Any]:
    """Toolbox entry, and Kairos's (fetch = handle): today's sun for the
    Settings location, on the Settings clock. Never raises: a polar day or
    night comes back as `unavailable` with its reason, and an unexpected
    failure as `unavailable` with the exception's type name, logged."""
    try:
        return compute(themis.latitude(), themis.longitude(), _now(),
                       clock_24h=themis.clock_24h())
    except Exception as e:
        log.error(f"Helios: sun computation failed: {type(e).__name__}")
        return {"error": f"unavailable: sun computation raised {type(e).__name__}"}


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
