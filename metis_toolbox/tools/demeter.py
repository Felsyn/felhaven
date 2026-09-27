"""
demeter.py — Goddess of the Cultivated Earth (elevation layer)
===============================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Fetch, resample and cache local elevation, and render the
             terrain raster.
             Named for Demeter, whose domain is the ground itself — the
             shape of the land is hers; the chart it lands on is Artemis's.

Contract:    The layer shape Hecate and Poseidon use, plus the raster:
               • ingest(area, center, box, cancel, progress) -> grid info,
                 online: one OpenTopography clip, resampled to the config
                 grid, written as terrain.bin into the PARTIAL directory,
                 then the derived raster. Raises IngestError (slug-first).
               • load(area) -> (header, array('h')) — offline pure read of
                 terrain.bin; raises FileNotFoundError. NEVER fetches.
               • raster(area) -> bytes — the P6 PPM the panel hands to
                 tk.PhotoImage. Read from derived/terrain.ppm.gz, and
                 REGENERATED from terrain.bin if derived/ was deleted, which
                 is what makes `rm -rf derived/` a supported operation.
             Best-effort by design: artemis.ingest_area catches an
             IngestError from this module and commits a terrain-less area
             rather than losing the roads and water that already succeeded
             (see §12, 2026-08-09). A sealed vault or absent key is
             therefore a skipped layer, never a failed ingest.

             Not a Pythia brain tool and has no fetch()/Kairos worker.

Key:         Reads 'opentopography_api_key' from the Cerberus Vault at call
             time, never cached to disk (the Midas/Callimachus pattern), and
             only during ingest — the offline render path never touches it.
             Seed it with:
               python cerberus.py set <PIN> opentopography_api_key <key>

Raster:      Three tiers split by PERCENTILE (terciles) of the local
             elevation distribution, not equal interval: equal interval
             turns a flat county into one flat rectangle, while percentile
             always shows structure. Two guards the spec insists on:
               • NODATA is excluded BEFORE the thresholds are computed —
                 a clipped corner of missing DEM would otherwise drag both
                 thresholds to the floor and flatten the whole display — and
                 renders as background, so missing data looks missing rather
                 than looking like a valley.
               • Under ~10 m of total relief the terciles are noise, so the
                 whole grid renders at mid tier and the manifest says
                 low_relief; honest beats pretty.
             P6 PPM is handed to Tk as RAW BYTES, never base64: this Tk
             (8.6.12) rejects the base64 form outright — see
             tests/test_artemis_ppm_spike.py, the step-0 spike verdict.

Datum:       3DEP/USGS10m is NAVD88; the global products are EGM96, tens of
             metres apart. vdatum travels in the manifest and sources must
             never be mixed inside one cached area (spec §6).

Source:      OpenTopography REST API, AAIGrid output:
               portal.opentopography.org/API/usgsdem?datasetName=USGS10m
             AAIGrid is a six-line text header then whitespace-separated
             values, row-major, north-to-south — parsing is a split().

Upstream:    tools/artemis.py — ingest_area orchestrates ingest();
             panels/artemis_panel.py — calls raster() to draw.
Downstream:  cerberus.py (the key), palette.py (the band colours),
             tools/artemis.py (geometry, paths, atomic writes).

Requires:    stdlib only — array, gzip, logging, os, struct, threading,
             urllib.parse, urllib.request.
"""

import array
import gzip
import logging
import os
import struct
import sys
import threading
import urllib.parse
import urllib.request
from typing import Any

# Demeter reaches UP to app-root siblings (cerberus.py, palette.py) as well as
# its tools/ siblings; only a bare `python tools/demeter.py` needs the path
# insert, and it must precede `import cerberus` (the midas placement).
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cerberus
from palette import C, rgb
from tools import artemis

log = logging.getLogger("METIS.demeter")

# ── Config ────────────────────────────────────────────────────────────────────

_API_URL = "https://portal.opentopography.org/API/usgsdem"
_DATASET = "USGS10m"
_VDATUM = "NAVD88"
_HTTP_TIMEOUT = 180
_VAULT_KEY_NAME = "opentopography_api_key"

_BIN_NAME = artemis.TERRAIN_BIN   # a cache-layout fact; Artemis owns the name
_DERIVED_DIR = "derived"
_PPM_NAME = "terrain.ppm.gz"

GRID = 1024                  # cells per side; powers of two only (zoom stops)
NODATA = -32768
LOW_RELIEF_M = 10            # below this total relief, terciles are noise

# terrain.bin: 64-byte header + int16 metres, row-major, north-to-south.
_MAGIC = b"ARTDEM"
_VERSION = 1
_HEADER = struct.Struct("<6sHIIddfhhh8s14x")

ERR_VAULT_LOCKED = "vault_locked"
ERR_NO_KEY = "no_key"

# Band colours, calculated from the palette rather than copied out of it:
# palette.py is Tk-free and annotated, so a tools/ module can import it
# (theme.py is neither). One value, one place — see CONVENTIONS §12, 2026-09-26.
RGB_VALLEY = rgb(C["map_terrain_lo"])
RGB_MID = rgb(C["map_terrain_mid"])
RGB_PEAK = rgb(C["map_terrain_hi"])
RGB_NODATA = rgb(C["bg"])     # the panel background; missing looks missing


# ── Key ───────────────────────────────────────────────────────────────────────

def _api_key() -> str:
    """The OpenTopography key from the Vault. Raises IngestError with the
    Midas slugs so the panel can tell 'sealed' from 'never seeded'."""
    if not cerberus.is_unlocked():
        raise artemis.IngestError(ERR_VAULT_LOCKED)
    try:
        return cerberus.vault_get(_VAULT_KEY_NAME)
    except cerberus.VaultError as exc:
        raise artemis.IngestError(ERR_NO_KEY) from exc


# ── AAIGrid ───────────────────────────────────────────────────────────────────

class AsciiGrid:
    """A parsed AAIGrid: the six header fields plus the value rows."""

    __slots__ = ("ncols", "nrows", "xll", "yll", "cellsize", "nodata", "values")

    def __init__(self, ncols: int, nrows: int, xll: float, yll: float,
                 cellsize: float, nodata: float, values: list[int]) -> None:
        self.ncols = ncols
        self.nrows = nrows
        self.xll = xll
        self.yll = yll
        self.cellsize = cellsize
        self.nodata = nodata
        self.values = values


def parse_aaigrid(text: str) -> AsciiGrid:
    """Six header lines then row-major values, north-to-south. Values may be
    floats (USGS10m ships decimals); they round to int16 metres. Raises
    ValueError (slug-first) on a malformed grid."""
    tokens = text.split()
    if len(tokens) < 12:
        raise ValueError("bad_grid: too short to be an AAIGrid")
    header: dict[str, float] = {}
    pos = 0
    for _ in range(6):
        key = tokens[pos].lower()
        try:
            header[key] = float(tokens[pos + 1])
        except ValueError as exc:
            raise ValueError(f"bad_grid: header field {key!r}") from exc
        pos += 2
    try:
        ncols = int(header["ncols"])
        nrows = int(header["nrows"])
        xll = header["xllcorner"]
        yll = header["yllcorner"]
        cellsize = header["cellsize"]
        nodata = header.get("nodata_value", -9999.0)
    except KeyError as exc:
        raise ValueError(f"bad_grid: missing header field {exc}") from exc
    if ncols <= 0 or nrows <= 0 or cellsize <= 0:
        raise ValueError(f"bad_grid: {ncols}x{nrows} at cellsize {cellsize}")

    body = tokens[pos:]
    if len(body) < ncols * nrows:
        raise ValueError(
            f"bad_grid: header says {ncols * nrows} values, found {len(body)}")
    values: list[int] = []
    for token in body[:ncols * nrows]:
        try:
            value = float(token)
        except ValueError as exc:
            raise ValueError(f"bad_grid: non-numeric value {token!r}") from exc
        if value == nodata or not -32767.0 <= value <= 32767.0:
            values.append(NODATA)
        else:
            values.append(int(round(value)))
    return AsciiGrid(ncols, nrows, xll, yll, cellsize, nodata, values)


def resample(grid: AsciiGrid, center: tuple[float, float],
             size: int = GRID) -> "array.array[int]":
    """Nearest-neighbour resample onto the size x size local grid covering the
    ingest square, row-major north-to-south.

    Nearest neighbour, not bilinear, for two reasons: averaging across a
    NODATA cell poisons its neighbours with a -32768 term, and the output is
    banded into three colours anyway, so interpolation would buy nothing a
    viewer could see. Both grids are axis-aligned in lat/lon (the projection
    is linear in each axis), so the row and column lookups are separable —
    2 x size divisions instead of size^2.
    """
    lat0, lon0 = center
    step = 2.0 * artemis.HALF_WIDTH_M / size
    top_lat = grid.yll + grid.nrows * grid.cellsize

    col_index: list[int] = []
    for c in range(size):
        east = -artemis.HALF_WIDTH_M + (c + 0.5) * step
        _, lon = artemis.unproject(east, 0.0, lat0, lon0)
        col_index.append(int((lon - grid.xll) / grid.cellsize))
    row_index: list[int] = []
    for r in range(size):
        north = artemis.HALF_WIDTH_M - (r + 0.5) * step
        lat, _ = artemis.unproject(0.0, north, lat0, lon0)
        row_index.append(int((top_lat - lat) / grid.cellsize))

    out = array.array("h", [NODATA]) * (size * size)
    values = grid.values
    for r in range(size):
        src_row = row_index[r]
        if not 0 <= src_row < grid.nrows:
            continue
        base = src_row * grid.ncols
        out_base = r * size
        for c in range(size):
            src_col = col_index[c]
            if 0 <= src_col < grid.ncols:
                out[out_base + c] = values[base + src_col]
    return out


# ── terrain.bin ───────────────────────────────────────────────────────────────

def write_bin(path: str, cells: "array.array[int]", center: tuple[float, float],
              size: int = GRID) -> dict[str, Any]:
    """Write terrain.bin atomically; returns the grid info for the manifest."""
    valid = [v for v in cells if v != NODATA]
    if not valid:
        raise artemis.IngestError("no_elevation: every cell is NODATA")
    elev_min, elev_max = min(valid), max(valid)
    m_per_px = 2.0 * artemis.HALF_WIDTH_M / size
    header = _HEADER.pack(_MAGIC, _VERSION, size, size, center[0], center[1],
                          m_per_px, elev_min, elev_max, NODATA,
                          _VDATUM.encode("ascii"))
    body = cells.tobytes() if sys.byteorder == "little" else _swapped(cells)
    artemis._atomic_write_bytes(path, header + body)
    return {"cols": size, "rows": size, "m_per_px": round(m_per_px, 2),
            "elev_min_m": elev_min, "elev_max_m": elev_max, "vdatum": _VDATUM,
            "low_relief": (elev_max - elev_min) < LOW_RELIEF_M}


def _swapped(cells: "array.array[int]") -> bytes:
    """terrain.bin is little-endian on disk regardless of host order."""
    copy = array.array("h", cells)
    copy.byteswap()
    return copy.tobytes()


def read_bin(path: str) -> tuple[dict[str, Any], "array.array[int]"]:
    """Parse terrain.bin — pure read; raises FileNotFoundError if absent and
    ValueError (slug-first) if it is not a terrain grid this build reads."""
    with open(path, "rb") as fh:
        raw = fh.read()
    if len(raw) < _HEADER.size:
        raise ValueError(f"bad_terrain: {len(raw)} bytes is shorter than the header")
    (magic, version, cols, rows, lat0, lon0, m_per_px,
     elev_min, elev_max, nodata, vdatum) = _HEADER.unpack_from(raw, 0)
    if magic != _MAGIC:
        raise ValueError(f"bad_terrain: magic {magic!r}")
    if version != _VERSION:
        raise ValueError(
            f"bad_terrain: version {version}, this build reads {_VERSION}")
    expected = cols * rows * 2
    if len(raw) - _HEADER.size < expected:
        raise ValueError(f"bad_terrain: body is {len(raw) - _HEADER.size} of "
                         f"{expected} bytes")
    cells = array.array("h")
    cells.frombytes(raw[_HEADER.size:_HEADER.size + expected])
    if sys.byteorder != "little":
        cells.byteswap()
    info = {"cols": cols, "rows": rows, "center": [lat0, lon0],
            "m_per_px": m_per_px, "elev_min_m": elev_min,
            "elev_max_m": elev_max, "nodata": nodata,
            "vdatum": vdatum.rstrip(b"\x00").decode("ascii")}
    return info, cells


# ── Banding and the raster ────────────────────────────────────────────────────

def band_thresholds(cells: "array.array[int]") -> "tuple[int, int] | None":
    """Tercile cuts of the VALID elevations, or None when relief is too low
    to band honestly. NODATA is filtered before the split (see Raster:)."""
    valid = sorted(v for v in cells if v != NODATA)
    if not valid:
        return None
    if valid[-1] - valid[0] < LOW_RELIEF_M:
        return None
    return (valid[len(valid) // 3], valid[2 * len(valid) // 3])


def render_ppm(cells: "array.array[int]", size: int = GRID) -> bytes:
    """Three-tier P6 PPM, row-major, matching terrain.bin's orientation."""
    cuts = band_thresholds(cells)
    lut = (bytes(RGB_NODATA), bytes(RGB_VALLEY), bytes(RGB_MID), bytes(RGB_PEAK))
    out = bytearray(b"P6\n%d %d\n255\n" % (size, size))
    if cuts is None:
        # Low relief (or no data at all): one honest mid band, no fake structure.
        rows = [lut[2] if v != NODATA else lut[0] for v in cells]
    else:
        low, high = cuts
        rows = []
        for v in cells:
            if v == NODATA:
                rows.append(lut[0])
            elif v < low:
                rows.append(lut[1])
            elif v < high:
                rows.append(lut[2])
            else:
                rows.append(lut[3])
    out += b"".join(rows)
    return bytes(out)


def write_derived(directory: str, cells: "array.array[int]",
                  size: int = GRID) -> None:
    """Write derived/terrain.ppm.gz. Everything under derived/ must stay
    safely deletable — this is the function that puts it back."""
    path = os.path.join(directory, _DERIVED_DIR, _PPM_NAME)
    artemis._atomic_write_bytes(path, gzip.compress(render_ppm(cells, size)))


def raster(area: str) -> bytes:
    """The P6 PPM for a committed area, regenerating derived/ if it was
    deleted. Pure local I/O — raises FileNotFoundError if terrain was never
    ingested for this area."""
    directory = artemis.area_dir(area)
    ppm_path = os.path.join(directory, _DERIVED_DIR, _PPM_NAME)
    try:
        with open(ppm_path, "rb") as fh:
            return gzip.decompress(fh.read())
    except (FileNotFoundError, OSError, EOFError, gzip.BadGzipFile):
        pass
    info, cells = read_bin(os.path.join(directory, _BIN_NAME))
    size = int(info["cols"])
    log.info("Demeter: rebuilding derived raster for %s", area)
    try:
        write_derived(directory, cells, size)
    except OSError as exc:                 # read-only media: still render
        log.warning("Demeter: could not cache derived raster: %s", exc)
    return render_ppm(cells, size)


# ── Ingest (online) ───────────────────────────────────────────────────────────

def ingest(area: str, center: tuple[float, float], box: artemis.BBox,
           cancel: threading.Event, progress: artemis.Progress) -> dict[str, Any]:
    """Fetch one elevation clip, resample, write terrain.bin + derived raster
    into the partial dir. Returns grid info for the manifest."""
    key = _api_key()                       # slugs before any network work
    lat_min, lon_min, lat_max, lon_max = box
    query = urllib.parse.urlencode({
        "datasetName": _DATASET, "south": lat_min, "north": lat_max,
        "west": lon_min, "east": lon_max, "outputFormat": "AAIGrid",
        "API_Key": key,
    })
    progress("terrain: downloading elevation")
    try:
        with urllib.request.urlopen(f"{_API_URL}?{query}",
                                    timeout=_HTTP_TIMEOUT) as resp:
            text = resp.read().decode("utf-8", "replace")
    except Exception as exc:
        raise artemis.IngestError(f"download_failed: {type(exc).__name__}") from exc
    if cancel.is_set():
        raise artemis.IngestError("cancelled")

    progress("terrain: parsing grid")
    try:
        grid = parse_aaigrid(text)
    except ValueError as exc:
        # The API reports quota/auth problems as a short text body, which
        # lands here as a parse failure — keep its own slug, not a guess.
        log.error("Demeter: grid parse failed (%s); first 200 chars: %s",
                  exc, text[:200].replace("\n", " "))
        raise artemis.IngestError(str(exc)) from exc
    if cancel.is_set():
        raise artemis.IngestError("cancelled")

    progress("terrain: resampling")
    cells = resample(grid, center)
    info = write_bin(os.path.join(artemis.partial_dir(area), _BIN_NAME),
                     cells, center)
    progress("terrain: rendering raster")
    write_derived(artemis.partial_dir(area), cells)
    return info


# ── Load (offline) ────────────────────────────────────────────────────────────

def load(area: str) -> tuple[dict[str, Any], "array.array[int]"]:
    """The committed area's elevation grid — pure read. Never fetches."""
    return read_bin(os.path.join(artemis.area_dir(area), _BIN_NAME))


def sample(info: dict[str, Any], cells: "array.array[int]",
           east: float, north: float) -> "int | None":
    """Metres at a local offset, or None outside the grid / at NODATA.

    Pure and I/O-free on purpose: the status strip calls this on every mouse
    motion, so the caller holds the grid in memory rather than re-reading a
    2 MB file per event. terrain.bin stays authoritative for elevation.
    """
    size = int(info["cols"])
    step = 2.0 * artemis.HALF_WIDTH_M / size
    col = int((east + artemis.HALF_WIDTH_M) / step)
    row = int((artemis.HALF_WIDTH_M - north) / step)
    if not (0 <= col < size and 0 <= row < size):
        return None
    value = cells[row * size + col]
    return None if value == NODATA else int(value)


def elevation_at(area: str, east: float, north: float) -> "int | None":
    """sample() with the grid read from disk — the one-shot convenience for
    the CLI and tests. Panels hold the grid and call sample() directly."""
    info, cells = load(area)
    return sample(info, cells, east, north)


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) == 2:
        grid_info, loaded = load(sys.argv[1])
        cuts_main = band_thresholds(loaded)
        valid_main = sum(1 for v in loaded if v != NODATA)
        print(f"[Demeter] {sys.argv[1]}: {grid_info['cols']}x{grid_info['rows']} "
              f"@ {grid_info['m_per_px']:.1f} m/px, {grid_info['vdatum']}")
        print(f"[Demeter]   elevation {grid_info['elev_min_m']}.."
              f"{grid_info['elev_max_m']} m, {valid_main}/{len(loaded)} cells valid")
        print(f"[Demeter]   bands: {'low relief — single mid band' if cuts_main is None else cuts_main}")
        print(f"[Demeter]   raster: {len(raster(sys.argv[1]))} bytes of PPM")
    else:
        print("[Demeter] usage: python tools/demeter.py <area_id>  "
              "(reads a committed cache; ingest runs via tools/artemis.py)")
        print(f"[Demeter] grid {GRID}x{GRID}, vault key {_VAULT_KEY_NAME!r}, "
              f"vault {'unlocked' if cerberus.is_unlocked() else 'sealed'}")
