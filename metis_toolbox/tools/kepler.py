"""
kepler.py — Positions of the Wanderers
=========================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Compute the positions of the five classical planets.

Contract:    Pure-function module (the Helios/Selene row of CONVENTIONS §2 —
             no fetch(), no handle(), no TOOL_DEFINITION, absent from
             __init__.py). Imported only by tools/hypatia.py, which composes
             it the way Midas composes Plutus. Out of LLM scope on purpose —
             this is orbital math feeding a display, not a brain tool.

Source:      Low-precision Keplerian elements: J2000 mean elements + secular
             (per-Julian-century) rates from Standish, E.M. (2006),
             "Keplerian Elements for Approximate Positions of the Planets",
             JPL Solar System Dynamics — valid 1800-2050 AD. Table values are
             the widely-published ones at ssd.jpl.nasa.gov.

Method:      Solve Kepler's equation by Newton iteration (few passes, 1e-6
             rad tolerance) for each body's eccentric anomaly, build the
             heliocentric ecliptic position, subtract Earth's own
             heliocentric position for a geocentric vector, rotate ecliptic
             -> equatorial by the mean obliquity, then reuse Hypatia's
             existing RA/Dec -> Alt/Az function verbatim (no duplicate math
             here — see hypatia._altaz). No relativistic or light-time
             correction. Accuracy is on the order of ~1 degree over
             centuries — a few pixels at chart scale. Do not "improve" this;
             it is deliberately low-precision, same philosophy as
             hypatia.py's own alt/az math.

Scope:       Locked to the five classical naked-eye planets — Mercury, Venus,
             Mars, Jupiter, Saturn. No Sun marker (deferred — the math yields
             it for free; if ever wanted it's one dict entry). No
             Uranus/Neptune. No Moon, ever — that is Selene's domain,
             permanently.

Upstream:    tools/hypatia.py (_build_snapshot calls positions())
Downstream:  none

Requires:    math, logging (stdlib only). No network, no state.
"""

import logging
import math
import os
import sys
from typing import Any

# kepler.py is the first tool module that imports a sibling tool module
# rather than being imported only by a panel or __init__.py. Normal
# operation (imported via felhaven.py/kairos.py/tests, all of which run with
# the app root on sys.path already) needs nothing extra; only a bare
# `python tools/kepler.py` standalone run needs the app root added first.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import hypatia

log = logging.getLogger("METIS.kepler")

# ── J2000 mean elements + per-Julian-century secular rates ───────────────────
# a: AU · e: dimensionless · I/L/peri/node: degrees.
# Source: Standish (2006), see module docstring.
_ELEMENTS = {
    "Mercury": {
        "a": 0.38709927, "a_rate": 0.00000037,
        "e": 0.20563593, "e_rate": 0.00001906,
        "I": 7.00497902, "I_rate": -0.00594749,
        "L": 252.25032350, "L_rate": 149472.67411175,
        "peri": 77.45779628, "peri_rate": 0.16047689,
        "node": 48.33076593, "node_rate": -0.12534081,
    },
    "Venus": {
        "a": 0.72333566, "a_rate": 0.00000390,
        "e": 0.00677672, "e_rate": -0.00004107,
        "I": 3.39467605, "I_rate": -0.00078890,
        "L": 181.97909950, "L_rate": 58517.81538729,
        "peri": 131.60246718, "peri_rate": 0.00268329,
        "node": 76.67984255, "node_rate": -0.27769418,
    },
    "Earth": {
        "a": 1.00000261, "a_rate": 0.00000562,
        "e": 0.01671123, "e_rate": -0.00004392,
        "I": -0.00001531, "I_rate": -0.01294668,
        "L": 100.46457166, "L_rate": 35999.37244981,
        "peri": 102.93768193, "peri_rate": 0.32327364,
        "node": 0.0, "node_rate": 0.0,
    },
    "Mars": {
        "a": 1.52371034, "a_rate": 0.00001847,
        "e": 0.09339410, "e_rate": 0.00007882,
        "I": 1.84969142, "I_rate": -0.00813131,
        "L": -4.55343205, "L_rate": 19140.30268499,
        "peri": -23.94362959, "peri_rate": 0.44441088,
        "node": 49.55953891, "node_rate": -0.29257343,
    },
    "Jupiter": {
        "a": 5.20288700, "a_rate": -0.00011607,
        "e": 0.04838624, "e_rate": -0.00013253,
        "I": 1.30439695, "I_rate": -0.00183714,
        "L": 34.39644051, "L_rate": 3034.74612775,
        "peri": 14.72847983, "peri_rate": 0.21252668,
        "node": 100.47390909, "node_rate": 0.20469106,
    },
    "Saturn": {
        "a": 9.53667594, "a_rate": -0.00125060,
        "e": 0.05386179, "e_rate": -0.00050991,
        "I": 2.48599187, "I_rate": 0.00193609,
        "L": 49.95424423, "L_rate": 1222.49362201,
        "peri": 92.59887831, "peri_rate": -0.41897216,
        "node": 113.66242448, "node_rate": -0.28867794,
    },
}

_GLYPHS = {
    "Mercury": "☿",
    "Venus": "♀",
    "Mars": "♂",
    "Jupiter": "♃",
    "Saturn": "♄",
}

_CLASSICAL_FIVE = ["Mercury", "Venus", "Mars", "Jupiter", "Saturn"]


# ── Internals ──────────────────────────────────────────────────────────────

def _solve_kepler(mean_anomaly_rad: float, e: float, tol: float = 1e-6,
                   max_iter: int = 30) -> float:
    """Eccentric anomaly via Newton iteration. Converges in a handful of
    passes for e up to Mercury's ~0.206 (and well beyond)."""
    E = mean_anomaly_rad if e < 0.8 else math.pi
    for _ in range(max_iter):
        delta = (E - e * math.sin(E) - mean_anomaly_rad) / (1 - e * math.cos(E))
        E -= delta
        if abs(delta) < tol:
            break
    return E


def _heliocentric_ecliptic(name: str, t_centuries: float) -> tuple[float, float, float]:
    """Heliocentric ecliptic (x, y, z) in AU for one body at T Julian
    centuries since J2000."""
    el = _ELEMENTS[name]
    a = el["a"] + el["a_rate"] * t_centuries
    e = el["e"] + el["e_rate"] * t_centuries
    incl = math.radians(el["I"] + el["I_rate"] * t_centuries)
    L = el["L"] + el["L_rate"] * t_centuries
    peri = el["peri"] + el["peri_rate"] * t_centuries
    node = el["node"] + el["node_rate"] * t_centuries

    mean_anomaly = math.radians((L - peri) % 360.0)
    arg_peri = math.radians(peri - node)
    node_rad = math.radians(node)

    E = _solve_kepler(mean_anomaly, e)
    xp = a * (math.cos(E) - e)
    yp = a * math.sqrt(1 - e * e) * math.sin(E)

    cw, sw = math.cos(arg_peri), math.sin(arg_peri)
    co, so = math.cos(node_rad), math.sin(node_rad)
    ci, si = math.cos(incl), math.sin(incl)

    x = (cw * co - sw * so * ci) * xp + (-sw * co - cw * so * ci) * yp
    y = (cw * so + sw * co * ci) * xp + (-sw * so + cw * co * ci) * yp
    z = (sw * si) * xp + (cw * si) * yp
    return x, y, z


# ── Public surface ────────────────────────────────────────────────────────

def positions(unix: float, lat: float, lon: float) -> list[dict[str, Any]]:
    """
    [{"name": "Mars", "glyph": "♂", "alt": float, "az": float}, ...] for
    the five classical planets, alt/az computed for an observer at (lat, lon)
    at the given unix timestamp. Never raises — a math failure for one planet
    (including a failure computing Earth's own position, needed by every
    planet) drops that planet and logs it, a chart missing Mars beats a dead
    worker.
    """
    out = []
    for name in _CLASSICAL_FIVE:
        try:
            ra, dec = _radec(name, unix)
            alt, az = hypatia._altaz(ra, dec, lat, lon, unix)
            out.append({
                "name": name,
                "glyph": _GLYPHS[name],
                "alt": round(alt, 3),
                "az": round(az, 3),
            })
        except Exception as exc:
            log.error(f"kepler: failed to compute {name}: {exc}")
            continue
    return out


def _radec(name: str, unix: float) -> tuple[float, float]:
    """Geocentric equatorial (ra_deg, dec_deg) for one classical planet at a
    unix timestamp — the same quantity JPL Horizons reports, which is what
    tests/test_kepler.py pins against. Recomputes Earth's own heliocentric
    position on every call (cheap arithmetic) rather than threading it
    through as a parameter, so a single degenerate case (a bad T value) fails
    the same way for Earth and for the planet — positions() then degrades
    per-planet instead of needing a separate up-front Earth special case."""
    jd = hypatia._julian_date(unix)
    t_centuries = (jd - 2451545.0) / 36525.0
    days_since_j2000 = jd - 2451545.0
    obliquity = math.radians(23.4393 - 0.0000004 * days_since_j2000)

    ex, ey, ez = _heliocentric_ecliptic("Earth", t_centuries)
    px, py, pz = _heliocentric_ecliptic(name, t_centuries)
    x, y, z = px - ex, py - ey, pz - ez

    # ecliptic -> equatorial (rotate about the x-axis by obliquity)
    xe = x
    ye = y * math.cos(obliquity) - z * math.sin(obliquity)
    ze = y * math.sin(obliquity) + z * math.cos(obliquity)

    ra = math.degrees(math.atan2(ye, xe)) % 360.0
    r = math.sqrt(xe * xe + ye * ye + ze * ze)
    dec = math.degrees(math.asin(max(-1.0, min(1.0, ze / r))))
    return ra, dec


# ── Standalone test ──────────────────────────────────────────────────────

_COMPASS_16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
               "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def _compass(az: float) -> str:
    return _COMPASS_16[int(((az % 360.0) + 11.25) // 22.5) % 16]


if __name__ == "__main__":
    import time
    now = time.time()
    for p in positions(now, hypatia.HYPATIA_LAT, hypatia.HYPATIA_LON):
        where = f"alt {p['alt']:6.1f}, az {p['az']:6.1f} ({_compass(p['az'])})" \
            if p["alt"] > 0 else f"below horizon (alt {p['alt']:.1f})"
        print(f"[Kepler] {p['glyph']} {p['name']:<8} {where}")
