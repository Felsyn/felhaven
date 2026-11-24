"""
eos.py — The Dawn (space weather and the aurora's reach)
========================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Report space weather: NOAA's storm scales, the Kp index, and the aurora's reach.
             Named for Eos, the dawn, whom the Romans called Aurora. This
             module reports; the fold row, Celestarium's line and Home's
             notice are panels/eos_panel.py's.

Contract:    Polled status (CONVENTIONS §2), not a brain tool. fetch() uses
             the Settings place; fetch_point(lat, lon) any place.
               fetch() -> {"now":  {"R"|"S"|"G": {"level": int|None, "text": str}},
                           "kp": float,
                           "days": [{"date", "r_minor", "g"}],  # today, tomorrow, the day after
                           "aurora": {"mlat", "kp_needed", "g_needed",
                                      "now", "next_24h", "kp_max_24h"}}
             Only what a display reads: the three days' r_minor is the chance
             of an R1 flare or worse, in percent; g is the G level expected.
             Any failure (offline, a timeout, an HTTP error, a body not shaped
             as below) RAISES, so Kairos delivers None and the panels say
             "couldn't check": a failure never reads as a quiet sky (the rule
             tools/iris.py set).

Sources:     Two SWPC products, keyless JSON, through tools/hermes.py. Measured
             2026-11-10:
               noaa-scales.json  1.1 KB. Key "0" is now (R/S/G Scale + Text);
                 "1", "2", "3" are the rest of today, tomorrow and the day
                 after (R MinorProb/MajorProb %, S Prob %, G Scale).
               noaa-planetary-k-index-forecast.json  6.9 KB. A list of dicts
                 {time_tag (UTC, a 3-hour bin's start), kp, observed:
                 "observed"|"estimated"|"predicted", noaa_scale}.
             `kp` is the bin that holds the present moment, whatever its kind.

Aurora:      A rough guide, computed here from Themis's coordinates; the
             place never leaves the PC.
               - Geomagnetic latitude from the dipole: the north geomagnetic
                 pole for 2025.0 is 80.85°N, 72.76°W (NOAA NCEI, WMM2025).
                 Its absolute value is used, so the southern aurora reads the
                 same way.
               - SWPC ("Tips on viewing the aurora"): at Kp 0 the oval's
                 equatorward edge is about 66° geomagnetic, and it moves about
                 2° equatorward per Kp level (48° at Kp 9); the aurora "can
                 often be observed hundreds of kilometers" equatorward of it.
               - The dipole is an approximation: it can sit a few degrees off
                 the corrected geomagnetic latitude SWPC's oval maps use (most
                 in Europe), so this is a rough guide and says so.
               - Felhaven reads "hundreds of kilometers" as 4° (about 440 km):
                 aurora is POSSIBLE here, low in the north (south), when the
                 geomagnetic latitude is at least (66 - 2·Kp) - 4.
               - So kp_needed = ceil((62 - mlat) / 2), at least 0, and
                 g_needed = kp_needed - 4 (G1 is Kp 5 ... G5 is Kp 9), None
                 below storm level. kp_needed above 9 means out of reach.
             now: the present Kp reaches kp_needed. next_24h: the highest Kp
             in the next 24 hours (any kind) does.

Upstream:    kairos.py (the "eos" worker, every 600 s), panels/eos_panel.py.
Downstream:  tools/hermes.py (the bounded read), themis.py (the place, read at
             every fetch, used only here).

Requires:    json, logging, math, os, sys, datetime (stdlib).

V0.03:       Walked through 2026-11-17 (the V0.03 walkthrough, CONVENTIONS
             §3), its first audit (born 2026-11-10, #62). Both reads go
             through Hermes with a measured cap (re-measured that day: 1,101
             and 6,911 bytes, the shapes unchanged), any failure raises and
             never reads as a quiet sky, the place is read at every fetch and
             never sent, and the log names levels, never the place. Five
             fields no display read (kp_kind, kp_time, and each day's
             r_major, s and kp_max) left the reading (the owner's call, as
             for Iris); Requires gained os and sys (the __main__ path).
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

# The guarded path aura.py uses, so `python tools/eos.py` resolves themis.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import themis
from tools import hermes

log = logging.getLogger("METIS.eos")

_BASE = "https://services.swpc.noaa.gov/products/"
SCALES_URL = _BASE + "noaa-scales.json"
KP_URL = _BASE + "noaa-planetary-k-index-forecast.json"
_TIMEOUT = 10
_CAP = 64 * 1024                # 1.1 KB and 6.9 KB measured 2026-11-10
_POLE_LAT, _POLE_LON = 80.85, -72.76    # NOAA NCEI, WMM2025, epoch 2025.0
_EDGE_AT_KP0 = 66.0             # SWPC: the oval's equatorward edge at Kp 0
_DEG_PER_KP = 2.0               # SWPC: about 2° equatorward per Kp level
_HORIZON_DEG = 4.0              # Felhaven's reading of "hundreds of kilometers"
_KP_MAX = 9


class FeedError(ValueError):
    """SWPC answered, but not in the shape it documents."""


def _get(url: str) -> Any:
    reply = hermes.get(url, cap=_CAP, timeout=_TIMEOUT)
    reply.raise_for_status()
    return json.loads(reply.content)


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _scale(block: Any) -> dict[str, Any]:
    if not isinstance(block, dict):
        raise FeedError("a scale is not an object")
    return {"level": _int(block.get("Scale")), "text": str(block.get("Text") or "")}


def _bin_time(entry: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(entry["time_tag"])).replace(tzinfo=timezone.utc)


def geomagnetic_latitude(lat: float, lon: float) -> float:
    """Dipole geomagnetic latitude, in degrees, signed (south is negative)."""
    phi, lam = math.radians(lat), math.radians(lon)
    phi_p, lam_p = math.radians(_POLE_LAT), math.radians(_POLE_LON)
    s = (math.sin(phi) * math.sin(phi_p)
         + math.cos(phi) * math.cos(phi_p) * math.cos(lam - lam_p))
    return math.degrees(math.asin(max(-1.0, min(1.0, s))))


def kp_needed(mlat: float) -> int:
    """The Kp at which the aurora is possible, low on the horizon, at this
    geomagnetic latitude (its absolute value). 0 in the auroral zone; above
    9 is out of reach."""
    reach = _EDGE_AT_KP0 - _HORIZON_DEG
    return max(0, math.ceil((reach - abs(mlat)) / _DEG_PER_KP))


def g_needed(kp: int) -> int | None:
    """The NOAA G level that Kp is (G1 = Kp 5 ... G5 = Kp 9), None below."""
    return kp - 4 if kp >= 5 else None


def _parse(scales: Any, kp_list: Any, lat: float, lon: float,
           now: datetime) -> dict[str, Any]:
    if not isinstance(scales, dict) or "0" not in scales:
        raise FeedError("noaa-scales has no current entry")
    if not isinstance(kp_list, list) or not kp_list:
        raise FeedError("the Kp forecast is not a list")
    try:
        entries = sorted(((_bin_time(e), float(e["kp"])) for e in kp_list),
                         key=lambda t: t[0])
    except (KeyError, TypeError, ValueError) as exc:
        raise FeedError(f"a Kp entry is malformed ({type(exc).__name__})") from exc

    current = scales["0"]
    now_block = {k: _scale(current.get(k)) for k in ("R", "S", "G")}
    # The last bin to start by now holds the present moment, whatever its kind.
    past = [e for e in entries if e[0] <= now] or entries[:1]
    kp = past[-1][1]

    days = []
    for key in ("1", "2", "3"):
        day = scales.get(key)
        if not isinstance(day, dict):
            continue
        r, g = day.get("R") or {}, day.get("G") or {}
        days.append({"date": str(day.get("DateStamp") or ""),
                     "r_minor": _int(r.get("MinorProb")), "g": _int(g.get("Scale"))})

    mlat = geomagnetic_latitude(lat, lon)
    need = kp_needed(mlat)
    window = [k for t, k in entries if now - timedelta(hours=3) < t <= now + timedelta(hours=24)]
    kp_max_24h = max(window) if window else kp
    return {
        "now": now_block,
        "kp": kp,
        "days": days,
        "aurora": {"mlat": round(mlat, 1), "kp_needed": need,
                   "g_needed": g_needed(need) if need <= _KP_MAX else None,
                   # Kp tops out at 9, so a place needing more is never reached.
                   "now": kp >= need,
                   "next_24h": kp_max_24h >= need,
                   "kp_max_24h": kp_max_24h},
    }


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_point(latitude: float, longitude: float,
                now: datetime | None = None) -> dict[str, Any]:
    """Space weather, with the aurora estimate for one point. Raises as
    fetch() does. Only the two SWPC URLs are requested; the point is not sent."""
    data = _parse(_get(SCALES_URL), _get(KP_URL), latitude, longitude,
                  now or datetime.now(timezone.utc))
    log.debug("Eos: R%s S%s G%s, Kp %s", *(data["now"][k]["level"] for k in "RSG"),
              data["kp"])
    return data


def fetch(now: datetime | None = None) -> dict[str, Any]:
    """Kairos entry point: the Settings place, read now. Raises on any
    failure; Kairos then delivers None."""
    return fetch_point(themis.latitude(), themis.longitude(), now)


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        if len(sys.argv) == 3:          # python tools/eos.py LAT LON
            d = fetch_point(float(sys.argv[1]), float(sys.argv[2]))
        else:
            d = fetch()
    except Exception as exc_main:
        print(f"[Eos] couldn't check ({type(exc_main).__name__})")
    else:
        n, a = d["now"], d["aurora"]
        print(f"[Eos] R{n['R']['level']} S{n['S']['level']} G{n['G']['level']} · "
              f"Kp {d['kp']}")
        for day in d["days"]:
            print(f"[Eos] {day}")
        print(f"[Eos] aurora: geomagnetic {a['mlat']}°, needs Kp {a['kp_needed']} "
              f"(G{a['g_needed']}), now={a['now']}, next 24 h={a['next_24h']} "
              f"(max Kp {a['kp_max_24h']})")
