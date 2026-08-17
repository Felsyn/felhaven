"""
hecate.py — Goddess of Crossroads (roads layer)
================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Fetch and cache TIGER/Line roads for an area as tiered Features.
             Named for Hecate, keeper of crossroads and waymarks — the roads
             are hers; the chart they land on is Artemis's.

Contract:    The two-verb layer shape the Artemis spec mandates:
               • ingest(area, center, box, cancel, progress) — online, slow,
                 explicit: downloads every covering county's ROADS shapefile,
                 decodes it through Cadmus, projects/clips/normalizes, writes
                 roads.vec into the area's PARTIAL directory. Raises
                 IngestError (slug-first); checks the cancel event between
                 counties so a multi-county fetch stays interruptible.
               • load(area) — offline, fast, pure read of the committed
                 roads.vec. Raises FileNotFoundError on a missing cache and
                 NEVER fetches — that refusal is the render-no-network
                 invariant's teeth, so do not "helpfully" add a fallback
                 fetch here.
             Not a Pythia brain tool and has no fetch()/Kairos worker: the
             layer is orchestrated by artemis.ingest_area and read by the
             panel; the LLM has no direct question for it.

Tiers:       The MTFCC -> render-tier table from the Artemis spec, a module
             constant. 1 = interstates (heaviest stroke) down to 4 = paths;
             parking-lot roads (S1780) are suppressed; vehicular trails and
             private roads carry FLAG_DASHED, ramps/service drives FLAG_THIN,
             so restyling never needs a re-ingest. Codes the table doesn't
             know render at tier 3 — an unknown road class should show up as
             a road, not vanish.

Source:      TIGER/Line 2025 per-county ROADS zips
             (www2.census.gov/geo/tiger/TIGER2025/ROADS/
             tl_2025_<GEOID>_roads.zip), public domain, over HTTPS via
             urllib at ingest time only. Counties come from
             artemis.counties_for_bbox. Known accepted weaknesses (spec §7):
             not survey-grade, thin trail coverage outside parks.

Upstream:    tools/artemis.py — ingest_area orchestrates ingest();
             panels/artemis_panel.py — calls load() to draw.
Downstream:  tools/cadmus.py (byte decoding), tools/artemis.py (geometry and
             the FVEC writer).

Requires:    stdlib only — io, logging, os, threading, urllib.request,
             zipfile.
"""

import io
import logging
import os
import sys
import threading
import zipfile

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import artemis
from tools import cadmus

log = logging.getLogger("METIS.hecate")

# ── Config ────────────────────────────────────────────────────────────────────

_ROADS_URL = "https://www2.census.gov/geo/tiger/TIGER2025/ROADS/tl_2025_{geoid}_roads.zip"
_HTTP_TIMEOUT = 120
_VEC_NAME = artemis.VEC_NAMES[artemis.LAYER_ROADS]

# MTFCC -> (tier, style flags). None suppresses the class entirely.
_SUPPRESSED = {"S1780"}                   # parking-lot roads: noise, not ways
_TIER_BY_MTFCC: dict[str, tuple[int, int]] = {
    "S1100": (1, 0),                      # primary (interstate / US hwy)
    "S1200": (2, 0),                      # secondary (state / county hwy)
    "S1400": (3, 0),                      # local / rural road, city street
    "S1500": (3, artemis.FLAG_DASHED),    # vehicular trail (4WD)
    "S1630": (3, artemis.FLAG_THIN),      # ramp
    "S1640": (3, artemis.FLAG_THIN),      # service drive
    "S1710": (4, 0),                      # walkway
    "S1720": (4, 0),                      # stairway
    "S1730": (4, 0),                      # alley
    "S1740": (3, artemis.FLAG_DASHED),    # private road (logging, oil field)
    "S1820": (4, 0),                      # bike path
    "S1830": (4, 0),                      # bridle path
}
_DEFAULT_TIER = (3, 0)                    # unknown class: visible, ordinary


# ── Ingest (online) ───────────────────────────────────────────────────────────

def ingest(area: str, center: tuple[float, float], box: artemis.BBox,
           cancel: threading.Event, progress: artemis.Progress) -> None:
    """Fetch, decode, normalize and write roads.vec into the partial dir."""
    geoids = artemis.counties_for_bbox(box)
    if not geoids:
        raise artemis.IngestError("no_counties: nothing covers this box")
    features: list[artemis.Feature] = []
    for i, geoid in enumerate(geoids, start=1):
        if cancel.is_set():
            raise artemis.IngestError("cancelled")
        progress(f"roads {i}/{len(geoids)}: county {geoid}")
        features.extend(_county_features(geoid, center))
    progress(f"roads: writing {len(features)} features")
    artemis.write_vec(os.path.join(artemis.partial_dir(area), _VEC_NAME),
                      features, center, artemis.LAYER_ROADS)


def _county_features(geoid: str,
                     center: tuple[float, float]) -> list[artemis.Feature]:
    """One county's zip -> normalized, clipped Features. Raises IngestError."""
    # Shared downloader: retries once, and keeps the 404-vs-failure rule in
    # one place for every layer (see artemis.download).
    raw = artemis.download(_ROADS_URL.format(geoid=geoid),
                           f"county {geoid} roads")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            shp = zf.read(f"tl_2025_{geoid}_roads.shp")
            dbf = zf.read(f"tl_2025_{geoid}_roads.dbf")
        pairs = cadmus.read_pair(shp, dbf)
    except (KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise artemis.IngestError(
            f"bad_county: {type(exc).__name__} for county {geoid}") from exc
    return normalize(pairs, center)


def normalize(pairs: list[tuple[cadmus.ShapeRecord, dict[str, str]]],
              center: tuple[float, float]) -> list[artemis.Feature]:
    """Cadmus records -> clipped road Features in local metres. Pure — the
    seam the tests drive without any network."""
    lat0, lon0 = center
    features: list[artemis.Feature] = []
    for (_, parts), attrs in pairs:
        mtfcc = attrs.get("MTFCC", "")
        if mtfcc in _SUPPRESSED:
            continue
        tier, flags = _TIER_BY_MTFCC.get(mtfcc, _DEFAULT_TIER)
        name = attrs.get("FULLNAME", "")
        for part in parts:
            local = [artemis.project(y, x, lat0, lon0) for x, y in part]
            for run in artemis.clip_polyline(local):
                features.append(artemis.Feature(
                    kind="road", name=name, tier=tier, flags=flags,
                    coords=run))
    return features


# ── Load (offline) ────────────────────────────────────────────────────────────

def load(area: str) -> list[artemis.Feature]:
    """The committed area's roads — pure read; raises FileNotFoundError on a
    missing cache, ValueError on a corrupt one. Never fetches."""
    return artemis.read_vec(os.path.join(artemis.area_dir(area), _VEC_NAME))


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) == 2:
        loaded = load(sys.argv[1])
        tiers: dict[int, int] = {}
        for feat in loaded:
            tiers[feat["tier"]] = tiers.get(feat["tier"], 0) + 1
        named = sum(1 for feat in loaded if feat["name"])
        print(f"[Hecate] {sys.argv[1]}: {len(loaded)} road features "
              f"({named} named)")
        for tier in sorted(tiers):
            print(f"[Hecate]   tier {tier}: {tiers[tier]}")
    else:
        print("[Hecate] usage: python tools/hecate.py <area_id>  "
              "(reads a committed cache; ingest runs via tools/artemis.py)")
        print(f"[Hecate] {len(_TIER_BY_MTFCC)} MTFCC classes mapped, "
              f"{len(_SUPPRESSED)} suppressed")
