"""
artemis.py — The Huntress (local chart core)
=============================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Own the local-chart cache: projection, area manifest, county
             reference, and the shared vector format.
             Named for Artemis, goddess of the hunt and the wild country the
             chart depicts. The layer modules (Hecate roads, later Poseidon
             water and Demeter elevation) know about formats; Artemis knows
             about the cache and the maths between lat/lon and local metres.
             Neither resolves location — the fix belongs to Themis.

Contract:    Exposes fetch() for Kairos (status-only: never raises, no
             network, no Tk — a missing cache is the normal pre-ingest state,
             the Midas sealed-vault lesson of §12 2026-07-24) plus the ingest
             orchestrator and cache/geometry API the layer stack shares:
               • area_id(lat, lon)           -> deterministic cache dir name
               • bbox(lat, lon)              -> ingest square (radius+margin)
               • project()/unproject()       -> constant-cos local metres
               • clip_polyline()             -> Liang-Barsky clip to the square
               • write_vec()/read_vec()      -> the FVEC vector container
               • read_manifest()             -> a cached area's manifest
               • ensure_reference()          -> county-FIPS lookup bootstrap
               • counties_for_bbox()         -> which TIGER files cover a box
               • ingest_area(lat, lon, cancel, progress) -> orchestrated fetch
               • set_label()/label_of()      -> an area's optional human name
               • delete_area()               -> recycle a cached area
             ingest_area raises IngestError (slug-first message, the
             themis.SettingsError precedent); everything it half-wrote is
             discarded. load-side readers raise FileNotFoundError / ValueError
             — they NEVER fetch. That asymmetry is the render-no-network
             invariant's API teeth: ingest is online, slow and explicit;
             load is offline, fast and pure.

             Deliberately NOT a Pythia brain tool in v1: the chart answers
             spatial questions through the panel; an LLM-facing describe()
             is a possible later addition, out of scope until the render
             stack is whole.

Cache:       <app root>/artemis_cache/<area_id>/ — one directory per cached
             area (manifest.json, roads.vec, later water.vec + terrain.bin +
             derived/), gitignored personal STATE (§9). Ingest writes into
             <area_id>.partial/ and promotes with one os.replace, so a
             half-downloaded area can never be loaded. artemis_cache/
             _reference/ holds the national county index (counties.json,
             GEOID -> name + bbox); the leading underscore cannot collide
             with an area_id. County selection is bbox-vs-bbox with a 1 km
             buffer — over-inclusion costs one redundant county download,
             under-inclusion silently drops roads, so the test is generous
             by design (the spec's polygon-refinement pass is a later trim,
             not a v1 need).

             manifest["label"] is OPTIONAL user metadata — an area's human
             name — preserved across a re-ingest, since a refresh redraws the
             map under the name you gave it. It has to be metadata: area_id is
             derived from the centre coordinates, so the directory name is not
             available to rename. Optional keys are already how schema 2
             carries terrain_skipped and grid, so adding one does not bump it.

Source:      Census cartographic boundary zip (GENZ2025 cb_2025_us_county_500k)
             over HTTPS at ingest time only, via urllib. Road/water/elevation
             sources belong to their layer modules.

Upstream:    kairos.py — the "artemis" worker polls fetch() every 600 s.
             panels/artemis_panel.py — runs ingest_area on its worker thread.
Downstream:  tools/hecate.py (and later poseidon/demeter) — import this
             module for projection, clipping and the FVEC writer. themis.py
             is read at fetch time for the fix, never at import time.

Requires:    stdlib only — ctypes (Recycle Bin), io, json, logging, math, os,
             shutil, struct, tempfile, threading, urllib.request, zipfile.
"""

import ctypes
import io
import json
import logging
import math
import os
import shutil
import struct
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from typing import Any, Callable, TypedDict

# Artemis reaches UP to an app-root sibling (themis.py). Normal operation
# (Pythia, tests, felhaven) already runs with the app root on sys.path; only a
# bare `python tools/artemis.py` needs it added first — the midas/callimachus
# top-of-module placement.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import themis
from tools import cadmus

log = logging.getLogger("METIS.artemis")

# ── Geometry constants (spec §6) ──────────────────────────────────────────────

M_PER_DEG_LAT = 110574.0
_M_PER_DEG_LON_EQUATOR = 111320.0

M_PER_MILE = 1609.344

DISPLAY_RADIUS_M = 8047.0   # the 5-mile ring the panel masks to
INGEST_MARGIN_M = 805.0     # 0.5 mi so strokes/labels don't die at the ring
HALF_WIDTH_M = DISPLAY_RADIUS_M + INGEST_MARGIN_M   # ingest square half-width
_COUNTY_BUFFER_M = 1000.0   # generosity margin for the 1:500k boundaries

RING_RADII_M = (1 * M_PER_MILE, 3 * M_PER_MILE, 5 * M_PER_MILE)
ZOOM_STOPS = (1, 2, 4)      # display scale only; the cache is the world's edge

# North is up. Rotation is out of scope, but the flag exists so a later
# heading-up mode is a render change rather than a projection rewrite — the
# retrofit that is annoying precisely when nothing anticipated it (spec §11).
HEADING_UP = False

# ── Cache constants ───────────────────────────────────────────────────────────

# App root = one dir up from tools/. Tests redirect CACHE_DIR at a temp dir.
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(_APP_ROOT, "artemis_cache")

_REFERENCE_URL = (
    "https://www2.census.gov/geo/tiger/GENZ2025/shp/cb_2025_us_county_500k.zip"
)
_HTTP_TIMEOUT = 120
_HTTP_RETRIES = 1        # a stalled Census download is usually transient
MANIFEST_SCHEMA = 2   # 2 added water + terrain layers, grid info, skip notes
_LABEL_MAX = 24       # a name for an area, not a sentence — and it has to fit
                      # an overlay row beside a range and a bearing
_VEC_MAGIC = b"FVEC"
_VEC_SCHEMA = 1

# FVEC layer ids and per-feature flags <-> the Feature "kind" vocabulary.
# Bit 0 of flags encodes the kind (line vs area) within a layer; the higher
# bits are render-style hints the layer modules set at ingest so a restyle
# never needs a re-ingest.
LAYER_ROADS = 1
LAYER_WATER = 2
_FLAG_AREA = 0x01
FLAG_DASHED = 0x02          # vehicular trails, private roads (S1500/S1740)
FLAG_THIN = 0x04            # ramps, service drives (S1630/S1640)
_KIND_TO_LAYER: dict[str, tuple[int, int]] = {
    "road": (LAYER_ROADS, 0),
    "water_line": (LAYER_WATER, 0),
    "water_area": (LAYER_WATER, _FLAG_AREA),
}
_LAYER_TO_KIND = {v: k for k, v in _KIND_TO_LAYER.items()}

# The cache filename per layer. A cache-layout fact, so Artemis owns it rather
# than each layer module spelling its own name.
VEC_NAMES = {LAYER_ROADS: "roads.vec", LAYER_WATER: "water.vec"}
TERRAIN_BIN = "terrain.bin"

BBox = tuple[float, float, float, float]   # (lat_min, lon_min, lat_max, lon_max)
Progress = Callable[[str], None]


class Feature(TypedDict):
    """The one normalized shape every layer serves: local metres, no lat/lon."""
    kind: str                            # "road" | "water_line" | "water_area"
    name: str                            # "" when the source has none
    tier: int                            # render weight, 1 = heaviest
    flags: int                           # FLAG_DASHED / FLAG_THIN style hints
    coords: list[tuple[float, float]]    # metres east/north of the area centre


class IngestError(Exception):
    """Raised by the ingest stack; message is slug-first ("download_failed:
    URLError"), the themis.SettingsError precedent. load-side readers never
    raise this — they raise FileNotFoundError/ValueError and never fetch."""


class NotPublished(Exception):
    """The source has no such file (HTTP 404). That is an ANSWER — plenty of
    inland counties publish no AREAWATER at all — and only callers that pass
    allow_missing ever see it."""


# ── Projection (constant-cos equirectangular, spec §6) ────────────────────────

def project(lat: float, lon: float,
            lat0: float, lon0: float) -> tuple[float, float]:
    """(lat, lon) -> local metres (east, north) of the centre (lat0, lon0)."""
    east = (lon - lon0) * _M_PER_DEG_LON_EQUATOR * math.cos(math.radians(lat0))
    north = (lat - lat0) * M_PER_DEG_LAT
    return (east, north)


def unproject(east: float, north: float,
              lat0: float, lon0: float) -> tuple[float, float]:
    """Local metres back to (lat, lon) — the hit-test direction."""
    lat = lat0 + north / M_PER_DEG_LAT
    lon = lon0 + east / (_M_PER_DEG_LON_EQUATOR * math.cos(math.radians(lat0)))
    return (lat, lon)


def area_id(lat: float, lon: float) -> str:
    """Deterministic cache-directory name from the centre, e.g.
    'n39p281_w080p344' — 3 decimals ≈ 111 m, so the same fix always maps to
    the same directory."""
    lat_milli = round(abs(lat) * 1000)
    lon_milli = round(abs(lon) * 1000)
    ns = "n" if lat >= 0 else "s"
    ew = "e" if lon >= 0 else "w"
    return (f"{ns}{lat_milli // 1000:02d}p{lat_milli % 1000:03d}"
            f"_{ew}{lon_milli // 1000:03d}p{lon_milli % 1000:03d}")


def bbox(lat: float, lon: float) -> BBox:
    """The ingest square (display radius + margin) in degrees."""
    dlat = HALF_WIDTH_M / M_PER_DEG_LAT
    dlon = HALF_WIDTH_M / (_M_PER_DEG_LON_EQUATOR * math.cos(math.radians(lat)))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)


def clip_polyline(points: list[tuple[float, float]],
                  half_width: float = HALF_WIDTH_M) -> list[list[tuple[float, float]]]:
    """Clip a polyline (local metres) to the centred square of the given
    half-width, splitting it where it leaves. Liang-Barsky per segment.

    Clipping is not optional: the FVEC coordinate unit is an int16 half-metre
    (±16.4 km), and an unclipped county-length road would overflow it.
    """
    out: list[list[tuple[float, float]]] = []
    run: list[tuple[float, float]] = []
    for i in range(len(points) - 1):
        seg = _clip_segment(points[i], points[i + 1], half_width)
        if seg is None:
            if run:
                out.append(run)
                run = []
            continue
        a, b = seg
        if not run:
            run = [a]
        elif run[-1] != a:      # segment re-enters at a new boundary point
            out.append(run)
            run = [a]
        run.append(b)
    if run:
        out.append(run)
    return [r for r in out if len(r) >= 2]


def clip_polygon(points: list[tuple[float, float]],
                 half_width: float = HALF_WIDTH_M) -> list[tuple[float, float]]:
    """Clip a closed ring (local metres) to the centred square, returning a
    ring that is still closed and fillable. Sutherland-Hodgman against the
    four edges — polylines cannot be used here: clip_polyline() splits a
    ring into open runs, which renders as loose strokes instead of a lake.

    A concave ring straddling a corner can come back with a degenerate edge
    running along the boundary; it fills correctly, which is all the water
    layer asks of it. Returns [] if the ring falls entirely outside.
    """
    ring = list(points)
    if len(ring) >= 2 and ring[0] == ring[-1]:
        ring = ring[:-1]                      # the closing vertex is implicit
    # (axis, keep_greater, bound): x >= -h, x <= h, y >= -h, y <= h
    for axis, keep_greater, bound in ((0, True, -half_width),
                                      (0, False, half_width),
                                      (1, True, -half_width),
                                      (1, False, half_width)):
        if not ring:
            return []

        def inside(pt: tuple[float, float],
                   _a: int = axis, _g: bool = keep_greater,
                   _b: float = bound) -> bool:
            return pt[_a] >= _b if _g else pt[_a] <= _b

        clipped: list[tuple[float, float]] = []
        prev = ring[-1]
        prev_in = inside(prev)
        for cur in ring:
            cur_in = inside(cur)
            if cur_in != prev_in:
                clipped.append(_axis_crossing(prev, cur, axis, bound))
            if cur_in:
                clipped.append(cur)
            prev, prev_in = cur, cur_in
        ring = clipped
    return ring if len(ring) >= 3 else []


def _axis_crossing(p: tuple[float, float], q: tuple[float, float],
                   axis: int, bound: float) -> tuple[float, float]:
    """Where segment p->q crosses the line (axis == bound)."""
    span = q[axis] - p[axis]
    t = 0.0 if span == 0.0 else (bound - p[axis]) / span
    return (p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1]))


def _clip_segment(p: tuple[float, float], q: tuple[float, float],
                  half: float) -> "tuple[tuple[float, float], tuple[float, float]] | None":
    """Liang-Barsky: clip segment p->q to [-half, half]²; None if outside."""
    x0, y0 = p
    dx, dy = q[0] - x0, q[1] - y0
    t0, t1 = 0.0, 1.0
    for delta, low_gap, high_gap in ((dx, x0 + half, half - x0),
                                     (dy, y0 + half, half - y0)):
        for d, gap in ((-delta, low_gap), (delta, high_gap)):
            if d == 0.0:
                if gap < 0.0:
                    return None
                continue
            t = gap / d
            if d < 0.0:
                if t > t1:
                    return None
                t0 = max(t0, t)
            else:
                if t < t0:
                    return None
                t1 = min(t1, t)
    return ((x0 + t0 * dx, y0 + t0 * dy), (x0 + t1 * dx, y0 + t1 * dy))


# ── Cache paths and atomic writes ─────────────────────────────────────────────
# Every path function reads CACHE_DIR at call time so tests can redirect it.

def area_dir(area: str) -> str:
    return os.path.join(CACHE_DIR, area)


def partial_dir(area: str) -> str:
    return area_dir(area) + ".partial"


def discard_partial(area: str) -> None:
    shutil.rmtree(partial_dir(area), ignore_errors=True)


def commit_area(area: str) -> None:
    """Promote <area>.partial/ to <area>/ with one os.replace — the only
    moment a new area becomes loadable."""
    partial = partial_dir(area)
    final = area_dir(area)
    if not os.path.isdir(partial):
        raise IngestError(f"commit_failed: no partial dir for {area}")
    if os.path.isdir(final):
        shutil.rmtree(final)
    os.replace(partial, final)


def _atomic_write_bytes(path: str, data: bytes) -> None:
    """The herodotus write shape: mkstemp in the destination directory,
    fsync, one os.replace, temp unlinked on any failure. Raises OSError."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".artemis-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── FVEC — the shared vector container (spec §10) ─────────────────────────────
# header : magic "FVEC", u16 schema, u8 layer_id, u8 reserved,
#          f64 lat0, f64 lon0, u32 feature_count, u32 coord_count
# index  : per feature -> u32 coord_offset, u16 coord_count,
#                         u8 tier, u8 flags, u16 name_id
# coords : int16 pairs, units of 0.5 m east/north from centre
# names  : u16 count, then u16-length-prefixed UTF-8

_VEC_HEADER = struct.Struct("<4sHBBddII")
_VEC_INDEX = struct.Struct("<IHBBH")


def write_vec(path: str, features: list[Feature],
              center: tuple[float, float], layer_id: int) -> None:
    """Serialize features (already clipped to the ingest square) atomically.
    Raises IngestError if a feature doesn't belong to layer_id or exceeds the
    format's limits — those are ingest bugs, not conditions to render past."""
    names: list[str] = []
    name_ids: dict[str, int] = {}
    index = bytearray()
    coords = bytearray()
    coord_count = 0
    for feat in features:
        layer, kind_flag = _KIND_TO_LAYER.get(feat["kind"], (0, 0))
        if layer != layer_id:
            raise IngestError(
                f"bad_layer: {feat['kind']!r} feature in layer {layer_id} file")
        flags = kind_flag | (feat["flags"] & ~_FLAG_AREA)
        if len(feat["coords"]) > 0xFFFF:
            raise IngestError(
                f"bad_feature: {len(feat['coords'])} points exceeds u16")
        name_id = name_ids.get(feat["name"])
        if name_id is None:
            name_id = len(names)
            if name_id > 0xFFFF:
                raise IngestError("bad_names: more than 65535 distinct names")
            name_ids[feat["name"]] = name_id
            names.append(feat["name"])
        index += _VEC_INDEX.pack(coord_count, len(feat["coords"]),
                                 feat["tier"], flags, name_id)
        for east, north in feat["coords"]:
            he, hn = round(east * 2), round(north * 2)
            if not (-32768 <= he <= 32767 and -32768 <= hn <= 32767):
                raise IngestError(
                    f"bad_coord: ({east:.1f}, {north:.1f}) m outside int16 range")
            coords += struct.pack("<hh", he, hn)
        coord_count += len(feat["coords"])

    name_block = bytearray(struct.pack("<H", len(names)))
    for name in names:
        encoded = name.encode("utf-8")
        name_block += struct.pack("<H", len(encoded)) + encoded

    header = _VEC_HEADER.pack(_VEC_MAGIC, _VEC_SCHEMA, layer_id, 0,
                              center[0], center[1], len(features), coord_count)
    _atomic_write_bytes(path, header + bytes(index) + bytes(coords) + bytes(name_block))


def read_vec(path: str) -> list[Feature]:
    """Deserialize an FVEC file — a pure read; raises FileNotFoundError if
    the cache is absent and ValueError (slug-first) if it's malformed."""
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < _VEC_HEADER.size:
        raise ValueError(f"bad_vec: {len(data)} bytes is shorter than the header")
    magic, schema, layer_id, _, _lat0, _lon0, feature_count, coord_count = (
        _VEC_HEADER.unpack_from(data, 0))
    if magic != _VEC_MAGIC:
        raise ValueError(f"bad_vec: magic {magic!r}")
    if schema != _VEC_SCHEMA:
        raise ValueError(f"bad_vec: schema {schema}, this build reads {_VEC_SCHEMA}")

    index_off = _VEC_HEADER.size
    coords_off = index_off + feature_count * _VEC_INDEX.size
    names_off = coords_off + coord_count * 4
    if names_off + 2 > len(data):
        raise ValueError("bad_vec: truncated body")

    (name_count,) = struct.unpack_from("<H", data, names_off)
    names = []
    pos = names_off + 2
    for _ in range(name_count):
        (length,) = struct.unpack_from("<H", data, pos)
        pos += 2
        names.append(data[pos:pos + length].decode("utf-8"))
        pos += length

    features: list[Feature] = []
    for i in range(feature_count):
        offset, count, tier, flags, name_id = _VEC_INDEX.unpack_from(
            data, index_off + i * _VEC_INDEX.size)
        kind = _LAYER_TO_KIND.get((layer_id, flags & _FLAG_AREA))
        if kind is None or offset + count > coord_count or name_id >= len(names):
            raise ValueError(f"bad_vec: feature {i} index out of range")
        pts = struct.unpack_from(f"<{2 * count}h", data, coords_off + offset * 4)
        features.append(Feature(
            kind=kind, name=names[name_id], tier=tier,
            flags=flags & ~_FLAG_AREA,
            coords=[(pts[2 * j] / 2.0, pts[2 * j + 1] / 2.0)
                    for j in range(count)],
        ))
    return features


# ── Spatial index (spec §12) ──────────────────────────────────────────────────
# A coarse uniform grid over the ingest square: every feature is listed in each
# cell its segments touch, so a cursor hit tests ~9 cells' worth of candidates
# instead of every feature on the chart. Constant work per motion event,
# independent of feature count AND of canvas draw order — which is the whole
# reason find_closest() is not used: that answers "which canvas item is
# nearest", and an unrelated layer that happened to drop a vertex nearby wins.
#
# Cell membership is computed from each segment's BOUNDING BOX, so a long
# diagonal claims a few cells it only passes near. Over-inclusion costs one
# extra distance test; under-inclusion would silently lose a road. The
# asymmetry decides it.

INDEX_CELLS = 32
DERIVED_DIR = "derived"
_INDEX_NAME = "spatial.idx"
_IDX_MAGIC = b"AIDX"
_IDX_SCHEMA = 1
_IDX_HEADER = struct.Struct("<4sHHI")   # magic, schema, cells/side, entries
_IDX_CELL = struct.Struct("<II")        # offset, count

Index = list[list[int]]                 # cell -> packed (layer << 24) | feature


def cell_of(east: float, north: float) -> int:
    """Cell index for a local-metre point, clamped to the grid."""
    side = 2.0 * HALF_WIDTH_M / INDEX_CELLS
    col = int((east + HALF_WIDTH_M) / side)
    row = int((HALF_WIDTH_M - north) / side)
    col = min(INDEX_CELLS - 1, max(0, col))
    row = min(INDEX_CELLS - 1, max(0, row))
    return row * INDEX_CELLS + col


def neighbour_cells(east: float, north: float) -> list[int]:
    """The cursor's cell plus its eight neighbours, clipped at the edges."""
    centre = cell_of(east, north)
    row, col = divmod(centre, INDEX_CELLS)
    out = []
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            r, c = row + dr, col + dc
            if 0 <= r < INDEX_CELLS and 0 <= c < INDEX_CELLS:
                out.append(r * INDEX_CELLS + c)
    return out


def _is_ring(feat: Feature) -> bool:
    return feat["kind"] == "water_area"


def build_index(layers: dict[int, list[Feature]]) -> Index:
    """One pass over every feature; see the note above for the bbox rule."""
    buckets: list[set[int]] = [set() for _ in range(INDEX_CELLS * INDEX_CELLS)]
    side = 2.0 * HALF_WIDTH_M / INDEX_CELLS
    for layer_id, feats in layers.items():
        for fi, feat in enumerate(feats):
            token = (layer_id << 24) | fi
            pts = feat["coords"]
            if len(pts) < 2:
                continue
            span = len(pts) if _is_ring(feat) else len(pts) - 1
            for i in range(span):
                ax, ay = pts[i]
                bx, by = pts[(i + 1) % len(pts)]
                lo_c = int((min(ax, bx) + HALF_WIDTH_M) / side)
                hi_c = int((max(ax, bx) + HALF_WIDTH_M) / side)
                lo_r = int((HALF_WIDTH_M - max(ay, by)) / side)
                hi_r = int((HALF_WIDTH_M - min(ay, by)) / side)
                for r in range(max(0, lo_r), min(INDEX_CELLS - 1, hi_r) + 1):
                    for c in range(max(0, lo_c), min(INDEX_CELLS - 1, hi_c) + 1):
                        buckets[r * INDEX_CELLS + c].add(token)
    return [sorted(bucket) for bucket in buckets]


def write_index(path: str, index: Index) -> None:
    """Serialize the index atomically. Lives under derived/ — deleting it is
    always safe, since build_index reproduces it from the .vec files."""
    entries = bytearray()
    table = bytearray()
    offset = 0
    for bucket in index:
        table += _IDX_CELL.pack(offset, len(bucket))
        for token in bucket:
            entries += struct.pack("<I", token)
        offset += len(bucket)
    header = _IDX_HEADER.pack(_IDX_MAGIC, _IDX_SCHEMA, INDEX_CELLS, offset)
    _atomic_write_bytes(path, header + bytes(table) + bytes(entries))


def read_index(path: str) -> Index:
    """Pure read; raises FileNotFoundError if absent, ValueError if stale."""
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < _IDX_HEADER.size:
        raise ValueError(f"bad_index: {len(data)} bytes is shorter than the header")
    magic, schema, cells, entry_count = _IDX_HEADER.unpack_from(data, 0)
    if magic != _IDX_MAGIC:
        raise ValueError(f"bad_index: magic {magic!r}")
    if schema != _IDX_SCHEMA or cells != INDEX_CELLS:
        raise ValueError(
            f"bad_index: schema {schema}/{cells} cells, this build reads "
            f"{_IDX_SCHEMA}/{INDEX_CELLS}")
    table_off = _IDX_HEADER.size
    entries_off = table_off + cells * cells * _IDX_CELL.size
    if entries_off + entry_count * 4 > len(data):
        raise ValueError("bad_index: truncated body")
    index: Index = []
    for i in range(cells * cells):
        offset, count = _IDX_CELL.unpack_from(data, table_off + i * _IDX_CELL.size)
        if offset + count > entry_count:
            raise ValueError(f"bad_index: cell {i} runs past the entry table")
        index.append(list(struct.unpack_from(
            f"<{count}I", data, entries_off + offset * 4)))
    return index


def index_path(directory: str) -> str:
    return os.path.join(directory, DERIVED_DIR, _INDEX_NAME)


def ensure_index(area: str, layers: dict[int, list[Feature]]) -> Index:
    """The committed area's index, rebuilt and re-cached if derived/ was
    deleted. Local I/O only — never fetches."""
    path = index_path(area_dir(area))
    try:
        return read_index(path)
    except (OSError, ValueError):
        pass
    index = build_index(layers)
    try:
        write_index(path, index)
    except OSError as exc:                 # read-only media: still usable
        log.warning("Artemis: could not cache spatial index: %s", exc)
    return index


# ── Hit testing ───────────────────────────────────────────────────────────────

def point_segment_distance(px: float, py: float, ax: float, ay: float,
                           bx: float, by: float) -> float:
    """Exact distance from a point to a segment, in the same units in."""
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = min(1.0, max(0.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def point_in_ring(px: float, py: float,
                  ring: list[tuple[float, float]]) -> bool:
    """Ray casting. Lets a hover inside a lake read as the lake itself rather
    than as its nearest shoreline."""
    inside = False
    n = len(ring)
    for i in range(n):
        ax, ay = ring[i]
        bx, by = ring[(i + 1) % n]
        if (ay > py) != (by > py):
            x_cross = ax + (py - ay) * (bx - ax) / (by - ay)
            if px < x_cross:
                inside = not inside
    return inside


def feature_distance(feat: Feature, px: float, py: float) -> float:
    """Distance from a point to a feature's geometry, 0 inside a water ring."""
    pts = feat["coords"]
    if len(pts) < 2:
        return math.inf
    if _is_ring(feat) and point_in_ring(px, py, pts):
        return 0.0
    span = len(pts) if _is_ring(feat) else len(pts) - 1
    best = math.inf
    for i in range(span):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % len(pts)]
        d = point_segment_distance(px, py, ax, ay, bx, by)
        if d < best:
            best = d
    return best


def query(index: Index, layers: dict[int, list[Feature]], east: float,
          north: float, tolerance: float,
          order: tuple[int, ...] = (LAYER_ROADS, LAYER_WATER),
          ) -> "tuple[int, int, float] | None":
    """Nearest feature to a local-metre point: (layer_id, feature index,
    distance), or None if nothing is within tolerance.

    `order` is the tie priority — roads beat water at equal footing, so a
    bridge reads as the road you would drive. Everything happens in geometry
    space; canvas draw order never influences the answer.
    """
    candidates = set()
    for cell in neighbour_cells(east, north):
        candidates.update(index[cell])
    best_by_layer: dict[int, tuple[int, float]] = {}
    for token in candidates:
        layer_id, fi = token >> 24, token & 0xFFFFFF
        feats = layers.get(layer_id)
        if feats is None or fi >= len(feats):
            continue                      # index is stale against the .vec
        d = feature_distance(feats[fi], east, north)
        if d > tolerance:
            continue
        current = best_by_layer.get(layer_id)
        if current is None or d < current[1]:
            best_by_layer[layer_id] = (fi, d)
    for layer_id in order:
        hit = best_by_layer.get(layer_id)
        if hit is not None:
            return (layer_id, hit[0], hit[1])
    return None


# ── Presentation helpers ──────────────────────────────────────────────────────

def nice_scale_miles(max_miles: float) -> float:
    """The largest tidy distance that fits the scale bar's budget."""
    steps = (0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0)
    best = steps[0]
    for step in steps:
        if step <= max_miles:
            best = step
    return best


def bearing_of(east: float, north: float) -> int:
    """Compass bearing (0-359, north = 0) of a local-metre offset."""
    return int(round(math.degrees(math.atan2(east, north)))) % 360


# ── Recycle Bin (SHFileOperationW — stdlib ctypes, no new dependency) ─────────
#
# A TREE-shaped copy of tools/orpheus.py's file-shaped helper, deliberately not
# shared. The Windows call is identical, but the non-Windows fallback cannot be
# (os.remove raises on a directory, so this one needs shutil.rmtree), and a
# chart module importing an audio module to borrow a shell call is the wrong
# dependency. This is NOT the tools/artemis.download() situation: that helper
# was consolidated because a duplicated POLICY branch (404-means-absent vs
# swallow-everything) silently lost data, and this one has no policy — one
# shell call, fixed flags, boolean out. Rule of three: if a third caller
# appears, lift it into a shared home then (§12, 2026-08-16).
#
# Guarded by sys.platform, NOT imported bare: `from ctypes import wintypes`
# raises on non-Windows, and Kairos importlib-resolves this module at startup,
# so an import-time raise takes the whole dashboard down.

_FO_DELETE = 0x0003            # wFunc: delete the files named in pFrom
_FOF_SILENT = 0x0004           # no progress dialog
_FOF_NOCONFIRMATION = 0x0010   # no "are you sure" — the glyph already asked
_FOF_ALLOWUNDO = 0x0040        # THE point of this call: Recycle Bin, not erase
_FOF_NOERRORUI = 0x0400        # report failure through the return code only

if sys.platform == "win32":
    from ctypes import wintypes

    class _SHFILEOPSTRUCTW(ctypes.Structure):
        """shell32's SHFILEOPSTRUCTW. Only wFunc/pFrom/fFlags are set; the
        rest must still be present and zeroed for the layout to match."""

        _fields_ = [
            ("hwnd",                  wintypes.HWND),
            ("wFunc",                 wintypes.UINT),
            ("pFrom",                 wintypes.LPCWSTR),
            ("pTo",                   wintypes.LPCWSTR),
            ("fFlags",                ctypes.c_uint16),   # FILEOP_FLAGS = WORD
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings",         wintypes.LPVOID),
            ("lpszProgressTitle",     wintypes.LPCWSTR),
        ]


def _send_to_recycle_bin(path: str) -> bool:
    """Move a file OR DIRECTORY TREE to the Recycle Bin. True on success,
    False on any failure (logged, never raised).

    Non-Windows falls back to shutil.rmtree — a permanent delete with no undo.
    Felhaven is a Windows app; that branch exists so the module stays
    importable and testable elsewhere, not as a supported configuration."""
    if sys.platform != "win32":
        try:
            shutil.rmtree(path)
            return True
        except OSError as exc:
            log.error("Artemis: delete failed for %r: %s", path, exc)
            return False

    op = _SHFILEOPSTRUCTW()
    op.wFunc = _FO_DELETE
    # Double-NUL terminated: pFrom is a LIST of paths, so shell32 keeps reading
    # past a single terminator until it finds an empty one.
    op.pFrom = os.path.abspath(path) + "\0\0"
    op.fFlags = (_FOF_ALLOWUNDO | _FOF_NOCONFIRMATION
                 | _FOF_NOERRORUI | _FOF_SILENT)
    try:
        res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    except OSError as exc:
        log.error("Artemis: SHFileOperationW failed to run for %r: %s", path, exc)
        return False
    if int(res) != 0:
        log.error("Artemis: SHFileOperationW returned %s for %r", res, path)
        return False
    return True


def delete_area(area: str) -> bool:
    """Send a cached area's directory to the Recycle Bin. True on success,
    False on any refusal or failure — logged, never raised.

    The guard is the point. This is a RECURSIVE delete driven by a string, so
    `area` must be a bare name resolving to a direct child of CACHE_DIR: no
    separators, no "..", not the "_reference" index, not a ".partial" — the
    cached_areas() vocabulary exactly. The realpath check is what a bare
    basename test cannot do, since a junction inside the cache pointing at
    Documents would otherwise pass it.

    orpheus.delete_file() is NOT reusable here: its _safe_path requires
    os.path.isfile and is hard-wired to local_audio/. Don't retry that idea.
    """
    if (not area or area != os.path.basename(area)
            or area.startswith("_") or area.endswith(".partial")):
        log.warning("Artemis: refusing to delete %r — not a bare area name", area)
        return False
    path = area_dir(area)
    if not os.path.isdir(path):
        return False
    if os.path.dirname(os.path.realpath(path)) != os.path.realpath(CACHE_DIR):
        log.warning("Artemis: refusing to delete %r — outside the cache", area)
        return False
    ok = _send_to_recycle_bin(path)
    if ok:
        log.info("Artemis: recycled cached area %s", area)
    return ok


# ── Manifest ──────────────────────────────────────────────────────────────────

def read_manifest(area: str) -> dict[str, Any]:
    """A cached area's manifest — pure read, raises FileNotFoundError."""
    with open(os.path.join(area_dir(area), "manifest.json"),
              encoding="utf-8") as fh:
        payload: dict[str, Any] = json.load(fh)
    return payload


def write_manifest(directory: str, payload: dict[str, Any]) -> None:
    """Write a manifest into the given (usually partial) area directory."""
    raw = json.dumps(payload, indent=2).encode("utf-8")
    _atomic_write_bytes(os.path.join(directory, "manifest.json"), raw)


def set_label(area: str, label: str) -> str:
    """Give a cached area a human name, or clear it with "".

    Read-modify-write: write_manifest() rewrites the WHOLE payload, so the
    manifest has to be read back first or a rename would silently drop the
    layers, sources and fetch date. Whitespace is collapsed and the result
    capped at _LABEL_MAX. Returns the stored label. Raises FileNotFoundError
    if the area is not cached, OSError if the write fails.

    The label is metadata and can only ever be metadata: area_id is a
    deterministic function of the centre coordinates, so the DIRECTORY name is
    not available to rename.
    """
    manifest = read_manifest(area)              # raises FileNotFoundError
    clean = " ".join(label.split())[:_LABEL_MAX]
    if clean:
        manifest["label"] = clean
    else:
        manifest.pop("label", None)
    manifest.pop("area_id", None)               # cached_areas() injects this;
    write_manifest(area_dir(area), manifest)    # it is not manifest content
    return clean


def label_of(manifest: dict[str, Any]) -> str:
    """An area's display name: its custom label if it has one, else its centre
    coordinates. Takes the manifest rather than the area id so building a list
    is one directory pass instead of an extra file read per row."""
    label = str(manifest.get("label") or "").strip()
    if label:
        return label
    centre = manifest.get("center")
    if isinstance(centre, list) and len(centre) == 2:
        return f"{float(centre[0]):.3f} {float(centre[1]):.3f}"
    return str(manifest.get("area_id") or "unknown area")


def cached_areas() -> list[dict[str, Any]]:
    """Every committed area's manifest, for the panel and the smoke check.
    Never raises; unreadable manifests are skipped with a log line."""
    out: list[dict[str, Any]] = []
    try:
        entries = sorted(os.listdir(CACHE_DIR))
    except OSError:
        return out
    for entry in entries:
        if entry.startswith("_") or entry.endswith(".partial"):
            continue
        try:
            manifest = read_manifest(entry)
        except (OSError, ValueError) as exc:
            log.warning("Artemis: unreadable manifest for %s: %s", entry, exc)
            continue
        manifest["area_id"] = entry
        out.append(manifest)
    return out


# ── Ingest downloads ──────────────────────────────────────────────────────────

def download(url: str, what: str, *, allow_missing: bool = False) -> bytes:
    """Fetch bytes for an ingest step, retrying once before giving up.

    ONE implementation for every layer on purpose. When each module carried
    its own copy, Poseidon's swallowed a TimeoutError as "this county has no
    lakes" and committed a chart missing real water (§12, 2026-08-10) — a
    second copy is exactly how that class of bug comes back. Callers that can
    legitimately meet an absent file pass allow_missing and handle
    NotPublished; for everyone else a 404 is a failure like any other.
    """
    last: "Exception | None" = None
    for attempt in range(_HTTP_RETRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:
                return bytes(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and allow_missing:
                raise NotPublished(what) from exc
            last = exc
        except Exception as exc:
            last = exc
        if attempt < _HTTP_RETRIES:
            log.warning("Artemis: retrying %s after %s",
                        what, type(last).__name__)
    raise IngestError(f"download_failed: {type(last).__name__} for {what}")


# ── County reference layer (spec §8) ──────────────────────────────────────────

def _reference_path() -> str:
    return os.path.join(CACHE_DIR, "_reference", "counties.json")


def ensure_reference(cancel: threading.Event, progress: Progress) -> None:
    """Download and index the national county boundaries, once. Idempotent:
    a present counties.json is trusted until the TIGER vintage rolls (delete
    _reference/ to refresh). Raises IngestError on failure."""
    if os.path.exists(_reference_path()):
        return
    progress("reference: downloading county boundaries (~11 MB)")
    raw = download(_REFERENCE_URL, "county boundaries")
    if cancel.is_set():
        raise IngestError("cancelled")
    progress("reference: indexing counties")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            shp = zf.read("cb_2025_us_county_500k.shp")
            dbf = zf.read("cb_2025_us_county_500k.dbf")
        pairs = cadmus.read_pair(shp, dbf)
    except (KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise IngestError(f"bad_reference: {type(exc).__name__}") from exc

    counties: dict[str, dict[str, Any]] = {}
    for (_, parts), attrs in pairs:
        xs = [x for part in parts for x, _ in part]
        ys = [y for part in parts for _, y in part]
        if not xs:
            continue
        counties[attrs["GEOID"]] = {
            "name": attrs.get("NAME", ""),
            "bbox": [min(ys), min(xs), max(ys), max(xs)],  # BBox order
        }
    try:
        _atomic_write_bytes(_reference_path(),
                            json.dumps(counties).encode("utf-8"))
    except OSError as exc:
        raise IngestError(f"write_failed: {type(exc).__name__}") from exc
    log.info("Artemis: county reference indexed (%d counties)", len(counties))


def counties_for_bbox(box: BBox) -> list[str]:
    """GEOIDs of every county whose 1:500k bbox intersects the query box,
    buffered 1 km outward — generous on purpose (see Cache: above)."""
    try:
        with open(_reference_path(), encoding="utf-8") as fh:
            counties: dict[str, dict[str, Any]] = json.load(fh)
    except FileNotFoundError as exc:
        raise IngestError("reference_missing: run ensure_reference first") from exc
    lat_min, lon_min, lat_max, lon_max = box
    mid_lat = (lat_min + lat_max) / 2.0
    dlat = _COUNTY_BUFFER_M / M_PER_DEG_LAT
    dlon = _COUNTY_BUFFER_M / (
        _M_PER_DEG_LON_EQUATOR * math.cos(math.radians(mid_lat)))
    lat_min, lat_max = lat_min - dlat, lat_max + dlat
    lon_min, lon_max = lon_min - dlon, lon_max + dlon
    hits = []
    for geoid, info in counties.items():
        c_lat_min, c_lon_min, c_lat_max, c_lon_max = info["bbox"]
        if (c_lat_min <= lat_max and c_lat_max >= lat_min
                and c_lon_min <= lon_max and c_lon_max >= lon_min):
            hits.append(geoid)
    return sorted(hits)


# ── Ingest orchestrator ───────────────────────────────────────────────────────

def _carry_terrain(area: str, partial: str, manifest: dict[str, Any]) -> bool:
    """Copy the committed area's elevation into the partial dir so a refresh
    that could not fetch terrain KEEPS the terrain it already had.

    A re-ingest replaces the whole directory, so without this a refresh with
    a sealed vault silently downgrades a complete area to a terrain-less one
    — paying for a road update with elevation you already owned. Best-effort
    terrain is right for a FIRST ingest (don't lose roads over a missing
    key); applied to a re-ingest unguarded it is the Poseidon 404 bug again,
    a tolerated failure degrading committed data (§12, 2026-08-16).

    Only terrain.bin is copied: it is authoritative, and demeter.raster()
    rebuilds derived/terrain.ppm.gz on demand — which is also why the whole
    derived/ directory must NOT be copied, since it now holds the freshly
    built spatial index for the NEW roads and water.

    Topography does not change, so an older grid is strictly better than
    none. This is not datum mixing: the grid is carried whole, with its own
    vdatum, exactly as it was ingested.
    """
    source = os.path.join(area_dir(area), TERRAIN_BIN)
    if not os.path.isfile(source):
        return False
    try:
        previous = read_manifest(area)
    except (OSError, ValueError):
        return False
    if "terrain" not in previous.get("layers", []):
        return False
    try:
        shutil.copy2(source, os.path.join(partial, TERRAIN_BIN))
    except OSError as exc:
        log.warning("Artemis: could not carry terrain forward for %s: %s",
                    area, exc)
        return False
    manifest["layers"].append("terrain")
    if "grid" in previous:
        manifest["grid"] = previous["grid"]
    elevation = previous.get("sources", {}).get("elevation")
    if elevation:
        manifest["sources"]["elevation"] = elevation
    return True


def ingest_area(lat: float, lon: float, cancel: threading.Event,
                progress: Progress) -> str:
    """Fetch every layer for the area centred on (lat, lon) into a partial
    directory and commit it atomically. Online, slow, explicit — the only
    function in this module that touches the network (via the layers).
    Raises IngestError; a failed or cancelled ingest leaves no partial dir."""
    area = area_id(lat, lon)
    box = bbox(lat, lon)
    try:
        ensure_reference(cancel, progress)
        if cancel.is_set():
            raise IngestError("cancelled")
        discard_partial(area)
        os.makedirs(partial_dir(area), exist_ok=True)

        # Lazy imports: the layer modules import artemis for the shared
        # geometry/formats, so the orchestrator binds them at call time (the
        # helios->aura shape).
        from tools import demeter, hecate, poseidon

        counties = counties_for_bbox(box)
        tiger = "TIGER2025/" + ",".join(counties)
        manifest: dict[str, Any] = {
            "center": [lat, lon],
            "radius_m": DISPLAY_RADIUS_M,
            "bbox": list(box),
            "layers": [],
            "sources": {},
            "fetched": _utc_now(),
            "schema": MANIFEST_SCHEMA,
        }

        hecate.ingest(area, (lat, lon), box, cancel, progress)
        manifest["layers"].append("roads")
        manifest["sources"]["roads"] = tiger
        if cancel.is_set():
            raise IngestError("cancelled")

        poseidon.ingest(area, (lat, lon), box, cancel, progress)
        manifest["layers"].append("water")
        manifest["sources"]["water"] = tiger
        if cancel.is_set():
            raise IngestError("cancelled")

        # Built here, from the vectors just written, so the first hover after
        # an ingest is instant. It lives under derived/ and ensure_index()
        # rebuilds it on demand, so this is a convenience, not a dependency.
        progress("index: bucketing features")
        partial = partial_dir(area)
        write_index(index_path(partial), build_index({
            layer: read_vec(os.path.join(partial, name))
            for layer, name in VEC_NAMES.items()
        }))

        # Terrain is BEST EFFORT: an absent key, a sealed vault or a failed
        # elevation fetch must not throw away the roads and water that already
        # landed — losing a whole area over an optional layer would be a
        # regression on the roads-only chart that shipped first. The reason is
        # recorded so the panel can say what is missing rather than pretending
        # the area is complete. Cancellation is NOT best-effort; it propagates.
        try:
            manifest["grid"] = demeter.ingest(area, (lat, lon), box,
                                              cancel, progress)
            manifest["layers"].append("terrain")
            manifest["sources"]["elevation"] = "OpenTopography/USGS10m"
        except IngestError as exc:
            slug = str(exc).split(":", 1)[0]
            if slug == "cancelled":
                raise
            if _carry_terrain(area, partial, manifest):
                manifest["terrain_kept"] = slug
                log.info("Artemis: kept the previous terrain for %s (%s)",
                         area, exc)
                progress(f"terrain kept from the previous cache ({slug})")
            else:
                manifest["terrain_skipped"] = slug
                log.warning("Artemis: terrain skipped for %s (%s)", area, exc)
                progress(f"terrain skipped ({slug})")

        # A custom label is USER data, not fetched data: a re-ingest refreshes
        # the map under the name you gave it. Read here rather than at the top
        # so a rename made DURING the download still survives the commit. The
        # committed directory exists throughout — commit_area replaces it only
        # on the next line.
        try:
            previous = str(read_manifest(area).get("label") or "")
        except (OSError, ValueError):
            previous = ""
        if previous:
            manifest["label"] = previous

        progress("manifest: committing area")
        write_manifest(partial_dir(area), manifest)
        commit_area(area)
    except IngestError:
        discard_partial(area)
        raise
    except Exception as exc:                      # noqa: BLE001 — boundary
        discard_partial(area)
        log.error("Artemis: ingest failed for %s: %s", area, exc)
        raise IngestError(f"ingest_failed: {type(exc).__name__}") from exc
    log.info("Artemis: cached %s", area)
    return area


def _utc_now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


# ── Kairos contract ───────────────────────────────────────────────────────────

def nearest_cached(lat: float, lon: float,
                   limit: "int | None" = 3) -> list[dict[str, Any]]:
    """Cached areas ordered by distance from a point — what to offer when the
    place asked for isn't cached and there is no network. Straight off the
    manifests, so it costs a directory listing and never raises.

    limit=None returns every cached area, which is what the panel's Cached
    Locations overlay wants; the default keeps the placeholder's short list.
    """
    out = []
    for manifest in cached_areas():
        centre = manifest.get("center")
        if not (isinstance(centre, list) and len(centre) == 2):
            continue
        east, north = project(centre[0], centre[1], lat, lon)
        out.append({
            "area_id": manifest["area_id"],
            "center": centre,
            "label": label_of(manifest),
            "fetched": manifest.get("fetched"),
            "distance_mi": math.hypot(east, north) / M_PER_MILE,
            "bearing": bearing_of(east, north),
        })
    out.sort(key=lambda item: float(item["distance_mi"]))
    return out if limit is None else out[:limit]


def area_status(lat: float, lon: float) -> dict[str, Any]:
    """Whether the area centred on a point is cached, and what it holds.
    Never raises, no network, no Tk — a missing cache is a normal state, not
    a fault (the Midas sealed-vault lesson, §12 2026-07-24)."""
    area = area_id(lat, lon)
    status: dict[str, Any] = {
        "lat": lat, "lon": lon, "area_id": area,
        "cached": False, "ingested_at": None, "layers": [],
    }
    try:
        manifest = read_manifest(area)
        status["cached"] = True
        status["ingested_at"] = manifest.get("fetched")
        status["layers"] = manifest.get("layers", [])
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        log.warning("Artemis: unreadable manifest for %s: %s", area, exc)
    return status


def fetch() -> dict[str, Any]:
    """Kairos entry point. Reads the Themis fix at call time (the documented
    invariant) and reports that area's status."""
    return area_status(themis.latitude(), themis.longitude())


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Running this file directly binds it as __main__, while the layer modules
    # import it as tools.artemis — TWO module objects, so two different
    # IngestError classes, and `except IngestError` here would not catch the
    # one Demeter raises (best-effort terrain would abort the whole ingest).
    # Drive the CLI through the canonical module object instead.
    from tools import artemis as _self

    # Offline half — always works (callimachus split): current status + cache.
    result = _self.fetch()
    state = "cached " + str(result["ingested_at"]) if result["cached"] else "not cached"
    print(f"[Artemis] fix {result['lat']:.4f} {result['lon']:.4f} "
          f"-> {result['area_id']} ({state})")
    areas = _self.cached_areas()
    print(f"[Artemis] {len(areas)} cached area(s) in {_self.CACHE_DIR}")
    for m in areas:
        skip = m.get("terrain_skipped")
        print(f"[Artemis]   {m['area_id']}  fetched {m.get('fetched')}  "
              f"layers {','.join(m.get('layers', []))}"
              f"{'  (terrain skipped: ' + skip + ')' if skip else ''}")
    # Live half — real network ingest, only on explicit request:
    #   python tools/artemis.py ingest 39.2806 -80.3440
    # The vault is sealed in a bare CLI run, so terrain is skipped here by
    # design; cache from the panel (with Cerberus unlocked) to get elevation.
    if len(sys.argv) == 4 and sys.argv[1] == "ingest":
        lat_arg, lon_arg = float(sys.argv[2]), float(sys.argv[3])
        print(f"[Artemis] ingesting {lat_arg} {lon_arg} ...")
        done = _self.ingest_area(lat_arg, lon_arg, threading.Event(),
                                 lambda msg: print(f"[Artemis]   {msg}"))
        print(f"[Artemis] committed {done}")
    elif len(sys.argv) > 1:
        print("[Artemis] usage: python tools/artemis.py [ingest LAT LON]")
