"""
eratosthenes.py — The Measurer of the Earth (location search)
==============================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Resolve a typed location query to a latitude and longitude.
             Named for Eratosthenes of Cyrene, who measured the earth and
             laid the first grid of latitude and longitude across it —
             turning a place into a pair of numbers is precisely his
             contribution (historical-figure naming per CONVENTIONS §12,
             the Herodotus/Callimachus/Hypatia/Kepler precedent).

Contract:    Plain functions, not a brain tool:
               • parse_coordinates(text) -> (lat, lon) | None   (pure, offline)
               • format_coordinates(lat, lon) -> "39.2806°N 80.3440°W"
               • waypoints()             -> list of saved places (never raises)
               • resolve(text)           -> {"lat","lon","label","source"}
                                            | {"error": "<slug>"}
             resolve()'s source is "coordinates", "waypoint" or "address";
             its slugs are empty_query, bad_coordinates, no_results and
             geocode_failed: <detail>.
             resolve() NEVER raises and never caches: it answers a question,
             it does not remember anything. Storing the fix is Themis's job
             and always was — this module was deliberately scoped down from
             the spec's "Pharos" (which also owned the fix) once that turned
             out to be work Themis had already done.

Order:       Coordinates, then waypoints, then the geocoder — cheapest and
             most certain first, and the first two work with no network at
             all. The Census geocoder resolves STREET ADDRESSES only; it will
             never find a ridge, a trailhead or a hunting camp, so it is a
             convenience at the end of the list rather than the design
             centre. Named waypoints are the path that actually gets used in
             the field.

             Between the waypoints and the geocoder, text made only of
             numbers and hemisphere letters that didn't parse is refused as
             bad_coordinates. It is a mistyped coordinate, which is a
             location, and the geocoder only guesses at such text: it echoed
             "138.8977 -77.0365" back as an x/y pair at 77°S, and matched
             "1600 20500" to a numbered road in Kansas (2026-11-18). The
             refusal comes after the waypoints, so a waypoint named in
             digits still resolves; an address made only of numbers and
             letters (a grid address with its ZIP) needs its city added.

Grammars:    Three, because that is what you can read off the things you
             actually carry:
               • decimal degrees      39.2806, -80.3440
               • degrees + minutes    39 16.836 N  080 20.640 W  (handheld GPS)
               • degrees/min/sec      39 16 50 N  080 20 38 W    (paper map)
             Hemisphere letters may lead or trail; without them the first
             group is latitude and a leading minus carries the sign. Degree,
             minute and second marks are all accepted as separators.

Waypoints:   config/artemis_waypoints.json — a list of {"name","lat","lon"}.
             Config SHAPE, personal CONTENT, so it is gitignored (§4, the
             morpheus_playlists.json precedent) and adding a place is a JSON
             edit, never a code change. Missing or malformed reads as an
             empty list; a chart that still works without it is the point.

Source:      Census Geocoder onelineaddress endpoint, public domain, no key,
             online only. Addresses only, US only.

On the map:  Every answer, typed, saved or geocoded, is a finite latitude in
             [-90, 90] and a longitude in [-180, 180], or it is refused: such
             a waypoint is skipped as malformed, such a geocoder reply is
             geocode_failed. json reads NaN and Infinity, and the chart
             needs a real place: Artemis names a cache folder from the pair,
             and a NaN there raised out of the search instead of a slug
             (2026-10-05).

Upstream:    panels/artemis_panel.py — the search box.
Downstream:  none. This module knows nothing about the chart it serves; it
             hands back a pair of numbers and forgets them.

Requires:    tools/hermes.py (the bounded network read), and json, logging,
             os, re, sys (stdlib).

V0.03:       Walked through 2026-11-18 (the V0.03 walkthrough, CONVENTIONS
             §3), its first audit (born 2026-08-10, Artemis M5). The
             geocoder read goes through Hermes with a measured cap (re-measured
             live: 673 B for a match, 246 B for none), resolve() never raises,
             no slug carries exception text, and the waypoints file is read
             fresh on every search. A mistyped coordinate no longer goes to
             the geocoder (bad_coordinates, the owner's pick, see Order);
             Contract gained format_coordinates and the slugs, Requires sys.
"""

import json
import logging
import os
import re
import sys
from typing import Any

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import hermes

log = logging.getLogger("METIS.eratosthenes")

# ── Config ────────────────────────────────────────────────────────────────────

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WAYPOINTS_PATH = os.path.join(_APP_ROOT, "config", "artemis_waypoints.json")

_GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
_BENCHMARK = "Public_AR_Current"
_HTTP_TIMEOUT = 10
# Measured 2026-10-01: a one-match answer is 665 B, no match 234 B (673 B and
# 246 B on 2026-11-18). The cap is hermes' 64 KiB floor.
_MAX_BYTES = 64 * 1024
_USER_AGENT = "Felhaven/1.0 (Artemis chart search)"

# Hemisphere letter -> (is_latitude, sign).
_HEMISPHERES = {"n": (True, 1.0), "s": (True, -1.0),
                "e": (False, 1.0), "w": (False, -1.0)}

# Degree/minute/second marks and separators all collapse to whitespace.
_SEPARATORS = re.compile(r"[,;°º'′\"″´`]+")
_NUMBER = re.compile(r"^[+-]?\d+(\.\d+)?$")


# ── Coordinates ───────────────────────────────────────────────────────────────

def _coordinate_tokens(text: str) -> "tuple[list[float], list[str]] | None":
    """The numbers and hemisphere letters in `text`, or None when it holds a
    word or no number at all. Text this returns something for is
    COORDINATE-SHAPED: if it then fails to parse, resolve() refuses it as
    bad_coordinates rather than send a mistyped location to the geocoder."""
    cleaned = _SEPARATORS.sub(" ", text.strip().lower())
    numbers: list[float] = []
    letters: list[str] = []
    for token in cleaned.split():
        # A hemisphere letter may be glued to its number ("39.4n", "n39.4").
        head = token[0]
        tail = token[-1]
        if head in _HEMISPHERES and not _NUMBER.match(token):
            letters.append(head)
            token = token[1:]
        elif tail in _HEMISPHERES and not _NUMBER.match(token):
            letters.append(tail)
            token = token[:-1]
        if not token:
            continue
        if not _NUMBER.match(token):
            return None                    # a word: not coordinates
        numbers.append(float(token))
    if not numbers:
        return None
    return numbers, letters


def parse_coordinates(text: str) -> "tuple[float, float] | None":
    """Parse the three grammars above. None (not an exception) when the text
    isn't coordinates at all — the caller then tries waypoints and the
    geocoder, so "unparseable" is a normal outcome here."""
    tokens = _coordinate_tokens(text)
    if tokens is None:
        return None
    numbers, letters = tokens

    if len(numbers) not in (2, 4, 6) or len(letters) not in (0, 2):
        return None
    per_group = len(numbers) // 2
    groups = (numbers[:per_group], numbers[per_group:])

    values = []
    for group in groups:
        value = _to_degrees(group)
        if value is None:
            return None
        values.append(value)

    if letters:
        first, second = _HEMISPHERES[letters[0]], _HEMISPHERES[letters[1]]
        if first[0] == second[0]:
            return None                    # two latitudes, or two longitudes
        # Magnitude comes from the digits; the letter alone carries direction.
        signed = [abs(values[0]) * first[1], abs(values[1]) * second[1]]
        lat, lon = (signed[0], signed[1]) if first[0] else (signed[1], signed[0])
    else:
        lat, lon = values[0], values[1]

    if not _on_map(lat, lon):
        return None
    return (lat, lon)


def _on_map(lat: float, lon: float) -> bool:
    """A real latitude and longitude. NaN fails every comparison, so it fails
    here; so do the infinities and anything past a pole or the date line."""
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def _to_degrees(group: list[float]) -> "float | None":
    """[d] | [d, m] | [d, m, s] -> signed decimal degrees. None if a minutes
    or seconds field is out of range, which is how "39 90" gets rejected
    instead of quietly becoming 40.5°."""
    sign = -1.0 if group[0] < 0 else 1.0
    total = abs(group[0])
    for i, part in enumerate(group[1:], start=1):
        if part < 0 or part >= 60.0:
            return None
        total += part / (60.0 ** i)
    return sign * total


def format_coordinates(lat: float, lon: float) -> str:
    """The label a resolved coordinate wears in the UI."""
    return (f"{abs(lat):.4f}°{'N' if lat >= 0 else 'S'} "
            f"{abs(lon):.4f}°{'E' if lon >= 0 else 'W'}")


# ── Waypoints ─────────────────────────────────────────────────────────────────

def waypoints() -> list[dict[str, Any]]:
    """Saved places. Returns [] on any failure and logs (§4) — a missing or
    broken waypoints file must never stop the chart from working."""
    try:
        with open(_WAYPOINTS_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return []
    except Exception as exc:
        log.error("Eratosthenes: failed to load waypoints: %s", exc)
        return []
    out = []
    for entry in raw if isinstance(raw, list) else []:
        try:
            lat, lon = float(entry["lat"]), float(entry["lon"])
            if not _on_map(lat, lon):
                raise ValueError("not a place on the map")
            out.append({"name": str(entry["name"]), "lat": lat, "lon": lon})
        except (KeyError, TypeError, ValueError):
            log.warning("Eratosthenes: skipping malformed waypoint %r", entry)
    return out


def _match_waypoint(text: str) -> "dict[str, Any] | None":
    """Exact name first, then unique prefix — so HOME never resolves to
    HOME RIDGE, but typing CAMP for one CAMP RIDGE still works."""
    query = text.strip().lower()
    saved = waypoints()
    for entry in saved:
        if entry["name"].lower() == query:
            return entry
    hits = [e for e in saved if e["name"].lower().startswith(query)]
    return hits[0] if len(hits) == 1 else None


# ── Geocoder ──────────────────────────────────────────────────────────────────

def _geocode(text: str) -> dict[str, Any]:
    try:
        reply = hermes.get(_GEOCODER_URL,
                           params={"address": text, "benchmark": _BENCHMARK,
                                   "format": "json"},
                           headers={"User-Agent": _USER_AGENT},
                           cap=_MAX_BYTES, timeout=_HTTP_TIMEOUT)
        reply.raise_for_status()
        payload = reply.json()
    except Exception as exc:
        return {"error": f"geocode_failed: {type(exc).__name__}"}
    try:
        matches = payload["result"]["addressMatches"]
    except (KeyError, TypeError):
        return {"error": "geocode_failed: unexpected response shape"}
    if not matches:
        return {"error": "no_results"}
    best = matches[0]
    try:
        lat, lon = float(best["coordinates"]["y"]), float(best["coordinates"]["x"])
        if not _on_map(lat, lon):
            raise ValueError("not a place on the map")
        return {"lat": lat, "lon": lon,
                "label": str(best.get("matchedAddress", text)),
                "source": "address"}
    except (KeyError, TypeError, ValueError):
        return {"error": "geocode_failed: unexpected response shape"}


# ── Public API ────────────────────────────────────────────────────────────────

def resolve(text: str) -> dict[str, Any]:
    """Coordinates -> waypoint -> geocoder. Never raises. Coordinate-shaped
    text that reaches the geocoder's turn stops at bad_coordinates instead
    (see Order: it is taken as a mistyped location, and a word is what makes
    text an address)."""
    if not text or not text.strip():
        return {"error": "empty_query"}

    coords = parse_coordinates(text)
    if coords is not None:
        return {"lat": coords[0], "lon": coords[1],
                "label": format_coordinates(*coords), "source": "coordinates"}

    entry = _match_waypoint(text)
    if entry is not None:
        return {"lat": entry["lat"], "lon": entry["lon"],
                "label": entry["name"], "source": "waypoint"}

    if _coordinate_tokens(text) is not None:
        return {"error": "bad_coordinates"}
    return _geocode(text)


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    samples = ["39.2806, -80.3440", "39 16.836 N 080 20.640 W",
               "39 16 50 N 080 20 38 W", "not a place at all"]
    for sample in samples:
        print(f"[Eratosthenes] {sample!r:32} -> {parse_coordinates(sample)}")
    saved_main = waypoints()
    print(f"[Eratosthenes] {len(saved_main)} saved waypoint(s) in "
          f"{os.path.basename(_WAYPOINTS_PATH)}")
    for entry_main in saved_main:
        print(f"[Eratosthenes]   {entry_main['name']}  "
              f"{entry_main['lat']:.4f} {entry_main['lon']:.4f}")
    # Live half — one real geocoder call, only on explicit request:
    #   python tools/eratosthenes.py "1600 Pennsylvania Ave NW, Washington DC"
    if len(sys.argv) == 2:
        print(f"[Eratosthenes] resolving {sys.argv[1]!r} ...")
        print(f"[Eratosthenes] {resolve(sys.argv[1])}")
