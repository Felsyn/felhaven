"""
poseidon.py — Lord of Waters (hydrography layer)
=================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Fetch and cache TIGER/Line water for an area as tiered Features.
             Named for Poseidon, who holds the waters — the streams and lakes
             are his; the chart they land on is Artemis's.

Contract:    The same two-verb layer shape as Hecate:
               • ingest(area, center, box, cancel, progress) — online, slow,
                 explicit: downloads every covering county's LINEARWATER and
                 AREAWATER shapefiles, decodes them through Cadmus,
                 projects/clips/normalizes, writes water.vec into the area's
                 PARTIAL directory. Raises IngestError (slug-first); checks
                 the cancel event between counties.
               • load(area) — offline, fast, pure read of the committed
                 water.vec. Raises FileNotFoundError on a missing cache and
                 NEVER fetches (the render-no-network invariant, §12).
             Not a Pythia brain tool and has no fetch()/Kairos worker.

             Linear features clip as polylines; area features clip as
             POLYGONS (artemis.clip_polygon) so a lake straddling the ingest
             square stays closed and fillable — polyline clipping would split
             a ring into loose strokes.

Tiers:       TIGER hydrography carries no stream hierarchy — in a sampled
             county every linear feature is H3010, so MTFCC alone cannot rank
             them. The honest available signal is the NAME: a named run or
             creek is a landmark you navigate by, an unnamed one is a
             drainage line. So H3010/H3013 named -> tier 1 (major), unnamed
             -> tier 2 (minor); canals and ditches (H3020) are infrastructure
             and stay tier 2 however they are labelled; connectors (H1100)
             are artificial non-visible lines and are suppressed.
             Area water is tier 1 for open water (lake, reservoir, bay,
             ocean, glacier) and tier 2 for swamp, marsh, treatment pond and
             quarry. v1 fills both with one colour — the split is stored now
             so a later restyle can dim marshes without a re-ingest.

Source:      TIGER/Line 2025 per-county zips, public domain, over HTTPS via
             artemis.download (tools/hermes.py) at ingest time only:
               .../TIGER2025/LINEARWATER/tl_2025_<GEOID>_linearwater.zip
               .../TIGER2025/AREAWATER/tl_2025_<GEOID>_areawater.zip
             Counties come from artemis.counties_for_bbox. TIGER hydrography
             is coarse; NHD/3DHP is the upgrade path (spec §7), not v1.

Upstream:    tools/artemis.py — ingest_area orchestrates ingest();
             panels/artemis_panel.py — calls load() to draw.
Downstream:  tools/cadmus.py (byte decoding), tools/artemis.py (geometry and
             the FVEC writer).

Requires:    stdlib only — io, logging, os, threading, zipfile; the
             downloads go through artemis.download.
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

log = logging.getLogger("METIS.poseidon")

# ── Config ────────────────────────────────────────────────────────────────────

_LINEAR_URL = ("https://www2.census.gov/geo/tiger/TIGER2025/LINEARWATER/"
               "tl_2025_{geoid}_linearwater.zip")
_AREA_URL = ("https://www2.census.gov/geo/tiger/TIGER2025/AREAWATER/"
             "tl_2025_{geoid}_areawater.zip")
# Caps: 1.5x the largest file in the 2025 vintage, rounded up to a whole MiB.
# Measured 2026-10-01 across every county. LINEARWATER: largest zip 24,438,340 B
# (county 04005), its .shp 37,573,460 B. AREAWATER: largest zip 9,821,219 B
# (county 22075), its .shp 15,759,560 B. A new vintage means measuring again.
_LINEAR_MAX_BYTES = 35 * 1024 * 1024
_LINEAR_MEMBER_MAX_BYTES = 54 * 1024 * 1024
_AREA_MAX_BYTES = 15 * 1024 * 1024
_AREA_MEMBER_MAX_BYTES = 23 * 1024 * 1024
_VEC_NAME = artemis.VEC_NAMES[artemis.LAYER_WATER]

# Linear hydrography. See Tiers: above — the bool is "a name can promote it".
_SUPPRESSED_LINEAR = {"H1100"}            # artificial connectors, not water
_LINEAR_PROMOTABLE = {"H3010", "H3013"}   # stream / river, braided stream
_LINEAR_ALWAYS_MINOR = {"H3020"}          # canal, ditch, aqueduct

# Area hydrography: open water reads as tier 1, wetland/industrial as tier 2.
_AREA_OPEN = {"H2030", "H2040", "H2051", "H2053", "H2081"}
_AREA_LESSER = {"H2025", "H2041", "H2060"}


# ── Ingest (online) ───────────────────────────────────────────────────────────

def ingest(area: str, center: tuple[float, float], box: artemis.BBox,
           cancel: threading.Event, progress: artemis.Progress) -> None:
    """Fetch, decode, normalize and write water.vec into the partial dir."""
    geoids = artemis.counties_for_bbox(box)
    if not geoids:
        raise artemis.IngestError("no_counties: nothing covers this box")
    features: list[artemis.Feature] = []
    for i, geoid in enumerate(geoids, start=1):
        if cancel.is_set():
            raise artemis.IngestError("cancelled")
        features.extend(_county_features(
            geoid, center, progress, f"water {i}/{len(geoids)}"))
    progress(f"water: writing {len(features)} features")
    artemis.write_vec(os.path.join(artemis.partial_dir(area), _VEC_NAME),
                      features, center, artemis.LAYER_WATER)


def _county_features(geoid: str, center: tuple[float, float],
                     progress: artemis.Progress,
                     prefix: str) -> list[artemis.Feature]:
    """One county's two water zips -> normalized, clipped Features.

    A county with no area water at all is normal (an inland county with only
    streams), so an AREAWATER file the Census never published is tolerated.
    Only a genuine 404 counts as "never published" — a timeout or a refused
    connection fails the ingest, because quietly dropping a county's lakes
    would leave the chart wrong in a way nothing on screen could reveal.

    Progress is reported per FILE, not per county: each county is two blocking
    downloads, and a single "county 3/6" message that sits still for a minute
    is indistinguishable from a hang.
    """
    progress(f"{prefix}: county {geoid} streams")
    features = normalize_linear(
        _read_pair(_LINEAR_URL.format(geoid=geoid),
                   f"tl_2025_{geoid}_linearwater", geoid, required=True,
                   cap=_LINEAR_MAX_BYTES, member_cap=_LINEAR_MEMBER_MAX_BYTES),
        center)
    progress(f"{prefix}: county {geoid} lakes")
    features += normalize_area(
        _read_pair(_AREA_URL.format(geoid=geoid),
                   f"tl_2025_{geoid}_areawater", geoid, required=False,
                   cap=_AREA_MAX_BYTES, member_cap=_AREA_MEMBER_MAX_BYTES),
        center)
    return features


def _read_pair(url: str, stem: str, geoid: str, required: bool, *, cap: int,
               member_cap: int) -> list[tuple[cadmus.ShapeRecord, dict[str, str]]]:
    """Download one county zip and decode its .shp/.dbf pair.

    Only the optional AREAWATER fetch opts into allow_missing, so only it can
    ever read a 404 as absence; everything else — timeouts included — fails
    the ingest rather than quietly shipping a chart without a county's water.
    """
    try:
        raw = artemis.download(url, f"county {geoid} {stem.split('_')[-1]}",
                               cap=cap, allow_missing=not required)
    except artemis.NotPublished:
        log.info("Poseidon: county %s publishes no %s", geoid, stem)
        return []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            shp = artemis.read_member(zf, stem + ".shp", member_cap)
            dbf = artemis.read_member(zf, stem + ".dbf", member_cap)
        return cadmus.read_pair(shp, dbf)
    except (KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise artemis.IngestError(
            f"bad_county: {type(exc).__name__} for county {geoid}") from exc


def normalize_linear(pairs: list[tuple[cadmus.ShapeRecord, dict[str, str]]],
                     center: tuple[float, float]) -> list[artemis.Feature]:
    """Cadmus linear-water records -> clipped Features. Pure."""
    lat0, lon0 = center
    features: list[artemis.Feature] = []
    for (_, parts), attrs in pairs:
        mtfcc = attrs.get("MTFCC", "")
        if mtfcc in _SUPPRESSED_LINEAR:
            continue
        name = attrs.get("FULLNAME", "")
        if mtfcc in _LINEAR_ALWAYS_MINOR:
            tier = 2
        elif name and mtfcc in _LINEAR_PROMOTABLE:
            tier = 1
        else:
            tier = 2
        for part in parts:
            local = [artemis.project(y, x, lat0, lon0)
                     for x, y in cadmus.points(part)]
            for run in artemis.clip_polyline(local):
                features.append(artemis.Feature(
                    kind="water_line", name=name, tier=tier, flags=0,
                    coords=run))
    return features


def normalize_area(pairs: list[tuple[cadmus.ShapeRecord, dict[str, str]]],
                   center: tuple[float, float]) -> list[artemis.Feature]:
    """Cadmus area-water records -> clipped, still-closed ring Features."""
    lat0, lon0 = center
    features: list[artemis.Feature] = []
    for (_, parts), attrs in pairs:
        mtfcc = attrs.get("MTFCC", "")
        if mtfcc in _AREA_LESSER:
            tier = 2
        elif mtfcc in _AREA_OPEN:
            tier = 1
        else:
            tier = 2                      # unknown class: drawn, not guessed at
        name = attrs.get("FULLNAME", "")
        for part in parts:
            local = [artemis.project(y, x, lat0, lon0)
                     for x, y in cadmus.points(part)]
            ring = artemis.clip_polygon(local)
            if ring:
                features.append(artemis.Feature(
                    kind="water_area", name=name, tier=tier, flags=0,
                    coords=ring))
    return features


# ── Load (offline) ────────────────────────────────────────────────────────────

def load(area: str) -> list[artemis.Feature]:
    """The committed area's water — pure read; raises FileNotFoundError on a
    missing cache, ValueError on a corrupt one. Never fetches."""
    return artemis.read_vec(os.path.join(artemis.area_dir(area), _VEC_NAME))


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) == 2:
        loaded = load(sys.argv[1])
        lines = [f for f in loaded if f["kind"] == "water_line"]
        areas = [f for f in loaded if f["kind"] == "water_area"]
        named = sum(1 for f in loaded if f["name"])
        print(f"[Poseidon] {sys.argv[1]}: {len(loaded)} water features "
              f"({named} named)")
        print(f"[Poseidon]   linear: {len(lines)} "
              f"(major {sum(1 for f in lines if f['tier'] == 1)}, "
              f"minor {sum(1 for f in lines if f['tier'] == 2)})")
        print(f"[Poseidon]   area:   {len(areas)}")
    else:
        print("[Poseidon] usage: python tools/poseidon.py <area_id>  "
              "(reads a committed cache; ingest runs via tools/artemis.py)")
        print(f"[Poseidon] linear: {len(_LINEAR_PROMOTABLE)} promotable, "
              f"{len(_LINEAR_ALWAYS_MINOR)} always-minor, "
              f"{len(_SUPPRESSED_LINEAR)} suppressed; "
              f"area: {len(_AREA_OPEN)} open, {len(_AREA_LESSER)} lesser")
