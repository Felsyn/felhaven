"""
selene.py — Titaness of the Moon
================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Report the moon's phase, illumination, moonrise, and moonset.
             Computes them from the Settings location and this computer's
             clock (with a phase glyph). No network.

Contract:    Polled + brain tool, the Horai shape: TOOL_DEFINITION, handle(),
             and fetch = handle for Kairos. Both take no arguments and return a
             dict. fetch() never raises, deliberately (§2 asks why): there is
             no I/O to fail, and a day with no moonrise is a fact ("—"), not a
             failure. An unexpected math error is logged and becomes the slug.

             compute(lat, lon, when) is the pure core that handle() wraps, and
             the thing the tests pin.

             The panel (SeleneWidget and Hypatia's moon row, via Kairos's
             `selene` worker) and the get_moon_phase tool call the same
             function, so they cannot disagree. There is no `as_of`: nothing is
             fetched (T7 dates readings), so the answer is as old as the call.

Source:      The Astronomical Almanac's low-precision series for the moon
             (valid 1900–2100: ~0.3° in longitude, ~0.2° in latitude), taken to
             RA/Dec of date, then hypatia._altaz for the altitude (the Kepler
             precedent, §12). The frames agree, since the series and GMST are
             both "of date". The moon moves ~13°/day, so rise and set are not
             closed-form. The local day is scanned in 10-minute steps, and each
             sign change of (altitude − h0) is bisected, with h0 = 0.7275 ×
             parallax − 0.5667° (Meeus, Astronomical Algorithms, ch. 15). That
             puts the upper limb on the horizon with refraction and parallax
             allowed for. A day with no crossing is simply a day with no
             moonrise (or moonset).

             Illumination is (1 − cos ψ)/2, with ψ the moon's elongation from
             the sun. The sun's longitude comes from helios._sun_position, one
             copy of the sun formulas.

             The phase NAME follows the U.S. Naval Observatory's rule. Round the
             illumination to a whole percent: 0 is "New Moon", 100 "Full Moon",
             50 a quarter, anything else crescent or gibbous, waxing or waning
             by elongation. USNO applies it at local noon, and this applies it
             at `when`. So the name always agrees with the percentage beside
             it, and at noon it reproduces USNO's table (tests/test_selene.py).
             A quarter is therefore named only in the hour or so around it.
             That is USNO's convention, kept rather than improved.

Upstream:    themis.py (latitude/longitude and the 24-hour clock, read per
             call), kairos.py (fetch),
             tools/hypatia.py (_julian_date, _altaz), tools/helios.py
             (_sun_position, _clock, _pretty)
Downstream:  panels/aura_panel.py → SeleneWidget; panels/hypatia_panel.py →
             ConditionsWidget's moon row

Requires:    stdlib only (math, datetime, logging)

Time zone:   Times are in THIS COMPUTER's zone; see helios.py's note for why.

V0.02:       Audited 2026-07-25 — tag-only. Pure interpreter verified never-raise
             (interpret / _moon_time / _illumination, and handle() degrades to the
             slug moon_unavailable; an unknown phase → 🌙, never a KeyError).
             Thorough existing coverage (test_selene + test_tool_handles); no dead
             code.

2026-09-28:  computes the moon locally (V0.03 audit M3). It used to interpret
             Aura's astronomy dict, so no network meant no moon, and Aura's
             Open-Meteo fallback carried none at all. interpret(), _moon_time()
             and the wttr.in phase strings went with that dict.

2026-10-31:  writes moonrise and moonset on the 24-hour clock when Settings
             picks it, as Helios and Horai do.
"""

import logging
import math
import os
import sys
from datetime import datetime, time, timedelta, tzinfo
from typing import Any

# Selene reaches UP to app-root themis.py for the location, and across to
# hypatia and helios. Guarded-path pattern (callimachus.py) for a bare
# `python tools/selene.py`. Kairos, tests and felhaven already have it on path.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import themis
from tools import helios, hypatia

log = logging.getLogger("METIS.selene")

# Phase name -> glyph. Eight phases, in order.
PHASES = {
    "New Moon":        "🌑",
    "Waxing Crescent": "🌒",
    "First Quarter":   "🌓",
    "Waxing Gibbous":  "🌔",
    "Full Moon":       "🌕",
    "Waning Gibbous":  "🌖",
    "Last Quarter":    "🌗",
    "Waning Crescent": "🌘",
}

# Shown when the moon does not rise (or set) on the day.
_NO_TIME = "—"

# Seconds between altitude samples when scanning a day. The moon's altitude
# changes by at most ~15°/h, so two horizon crossings can't hide in one step.
_STEP = 600.0
_BISECTIONS = 20                 # 600 s / 2**20: far below a displayed minute


# ── The moon ──────────────────────────────────────────────────────────────────

def _sin(deg: float) -> float:
    return math.sin(math.radians(deg))


def _cos(deg: float) -> float:
    return math.cos(math.radians(deg))


def _moon_position(jd: float) -> tuple[float, float, float, float, float]:
    """(RA°, Dec°, horizontal parallax°, ecliptic longitude°, ecliptic
    latitude°) at Julian Day `jd`, geocentric, mean equinox of date: the
    Astronomical Almanac's low-precision series."""
    t = (jd - 2451545.0) / 36525.0
    lon = (218.32 + 481267.881 * t
           + 6.29 * _sin(135.0 + 477198.87 * t)
           - 1.27 * _sin(259.3 - 413335.36 * t)
           + 0.66 * _sin(235.7 + 890534.22 * t)
           + 0.21 * _sin(269.9 + 954397.74 * t)
           - 0.19 * _sin(357.5 + 35999.05 * t)
           - 0.11 * _sin(186.5 + 966404.03 * t))
    lat = (5.13 * _sin(93.3 + 483202.03 * t)
           + 0.28 * _sin(228.2 + 960400.87 * t)
           - 0.28 * _sin(318.3 + 6003.18 * t)
           - 0.17 * _sin(217.6 - 407332.20 * t))
    parallax = (0.9508
                + 0.0518 * _cos(135.0 + 477198.87 * t)
                + 0.0095 * _cos(259.3 - 413335.38 * t)
                + 0.0078 * _cos(235.7 + 890534.23 * t)
                + 0.0028 * _cos(269.9 + 954397.70 * t))
    eps = 23.4393 - 0.0000004 * (jd - 2451545.0)   # mean obliquity, as kepler.py
    # ecliptic -> equatorial (rotate about the x-axis by the obliquity)
    x = _cos(lat) * _cos(lon)
    y = _cos(lat) * _sin(lon)
    z = _sin(lat)
    ye = y * _cos(eps) - z * _sin(eps)
    ze = y * _sin(eps) + z * _cos(eps)
    ra = math.degrees(math.atan2(ye, x)) % 360.0
    dec = math.degrees(math.asin(max(-1.0, min(1.0, ze))))
    return ra, dec, parallax, lon % 360.0, lat


def _elongation(jd: float) -> float:
    """The moon's longitude minus the sun's, in [0, 360): under 180 it is
    waxing, over 180 waning."""
    sun_lon, _, _ = helios._sun_position(jd)
    _, _, _, moon_lon, _ = _moon_position(jd)
    return (moon_lon - sun_lon) % 360.0


def _illumination(jd: float) -> float:
    """Fraction of the disc lit, 0..1: (1 − cos ψ)/2, with ψ the moon's
    angular distance from the sun."""
    sun_lon, _, _ = helios._sun_position(jd)
    _, _, _, moon_lon, moon_lat = _moon_position(jd)
    cos_psi = _cos(moon_lat) * _cos(moon_lon - sun_lon)
    return (1.0 - cos_psi) / 2.0


def _phase_name(pct: int, waxing: bool) -> str:
    """USNO's rule on the whole-percent illumination (see Source)."""
    if pct == 0:
        return "New Moon"
    if pct == 100:
        return "Full Moon"
    if pct == 50:
        return "First Quarter" if waxing else "Last Quarter"
    if pct < 50:
        return "Waxing Crescent" if waxing else "Waning Crescent"
    return "Waxing Gibbous" if waxing else "Waning Gibbous"


def _above_horizon(lat: float, lon: float, unix: float) -> float:
    """Degrees the moon's centre stands above h0, the altitude at which its
    upper limb touches the horizon. Positive means it is up."""
    ra, dec, parallax, _, _ = _moon_position(hypatia._julian_date(unix))
    alt, _ = hypatia._altaz(ra, dec, lat, lon, unix)
    return alt - (0.7275 * parallax - 0.5667)


def _rise_set(lat: float, lon: float, start: float,
              end: float) -> tuple[float | None, float | None]:
    """(moonrise, moonset) as unix times within [start, end), either None when
    it does not happen in that span. Scans in _STEP samples, then bisects the
    sample pair that straddles each crossing."""
    rise: float | None = None
    set_: float | None = None
    t0 = start
    f0 = _above_horizon(lat, lon, t0)
    while t0 < end:
        t1 = min(t0 + _STEP, end)
        f1 = _above_horizon(lat, lon, t1)
        if (f0 < 0.0) != (f1 < 0.0):
            a, b, fa = t0, t1, f0
            for _ in range(_BISECTIONS):
                mid = (a + b) / 2.0
                fm = _above_horizon(lat, lon, mid)
                if (fa < 0.0) != (fm < 0.0):
                    b = mid
                else:
                    a, fa = mid, fm
            crossing = (a + b) / 2.0
            if f0 < 0.0 and rise is None:
                rise = crossing
            elif f0 >= 0.0 and set_ is None:
                set_ = crossing
        t0, f0 = t1, f1
    return rise, set_


def _show(unix: float | None, tz: tzinfo | None, clock_24h: bool = False) -> str:
    """A moonrise/moonset for display: '2:31 AM' ('02:31' on the 24-hour
    clock), or '—' when there is none."""
    if unix is None:
        return _NO_TIME
    return helios._pretty(helios._clock(unix, tz), clock_24h)


# ── Public API ────────────────────────────────────────────────────────────────

def compute(lat: float, lon: float, when: datetime, *,
            clock_24h: bool = False) -> dict[str, Any]:
    """
    The moon at `when`, and its rise and set on `when`'s date, at (lat, lon):

        {"emoji": "🌖", "phase": "Waning Gibbous",
         "illumination": "96%",
         "moonrise": "5:56 PM",   # "—" on a day it does not rise
         "moonset": "8:15 AM"}    # "—" on a day it does not set

    A naive `when` is this computer's zone (the app passes datetime.now());
    an aware one is its own zone (the tests pin USNO values in USNO's zone).
    Phase and illumination are for the moment `when`; rise and set are for
    its whole local day. With `clock_24h` they read "17:56", as in
    helios.compute(); handle() reads the setting.
    """
    tz = when.tzinfo
    day = when.date()
    start = datetime.combine(day, time(0), tzinfo=tz).timestamp()
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz).timestamp()
    jd = hypatia._julian_date(when.timestamp())
    pct = int(_illumination(jd) * 100.0 + 0.5)   # half-up, as the almanac rounds
    phase = _phase_name(pct, _elongation(jd) < 180.0)
    rise, set_ = _rise_set(lat, lon, start, end)
    return {
        "emoji":        PHASES[phase],
        "phase":        phase,
        "illumination": f"{pct}%",
        "moonrise":     _show(rise, tz, clock_24h),
        "moonset":      _show(set_, tz, clock_24h),
    }


# ── LLM tool contract ─────────────────────────────────────────────────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "get_moon_phase",
        "description": (
            "Returns the current moon phase (with illumination percent) plus "
            "tonight's moonrise and moonset for the dashboard's location. Call "
            "when the user asks about the moon, its phase, how full it is, or "
            "moonrise/moonset."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def _now() -> datetime:
    """Injectable clock seam, aura._now()'s convention: naive LOCAL time, the
    clock on the wall. compute() reads a naive datetime as this zone."""
    return datetime.now()


def handle() -> dict[str, Any]:
    """Toolbox entry, and Kairos's (fetch = handle): the moon now, and today's
    moonrise and moonset, for the Settings location, on the Settings clock.
    Never raises: an
    unexpected failure comes back as the slug with the exception's type name,
    logged."""
    try:
        return compute(themis.latitude(), themis.longitude(), _now(),
                       clock_24h=themis.clock_24h())
    except Exception as e:
        log.error(f"Selene: moon computation failed: {type(e).__name__}")
        return {"error": f"moon_unavailable: {type(e).__name__}"}


fetch = handle  # Kairos entry point: the same answer the model gets, no I/O


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    info = handle()
    if "error" in info:
        print(f"[Selene] {info['error']}")
    else:
        print(
            f"[Selene] {info['emoji']} {info['phase']} | "
            f"illum {info['illumination']} | "
            f"rise {info['moonrise']} | set {info['moonset']}"
        )
