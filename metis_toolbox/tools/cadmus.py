"""
cadmus.py — The Alphabet Bringer (format decoder)
==================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Decode ESRI shapefile bytes (.shp/.dbf) into geometry and
             attribute records.
             Named for Cadmus of Thebes, who carried the Phoenician letters
             into Greece — a decoder of foreign scripts, nothing more
             (historical/mythic naming per CONVENTIONS §12 precedent).

Contract:    Plain functions, not a brain tool — bytes in, Python data out:
               • read_shp(data)        -> list[(shape_type, parts)]
               • read_dbf(data)        -> list[dict[str, str]]
               • read_pair(shp, dbf)   -> list[((shape_type, parts), record)]
               • points(part)          -> the part's (x, y) pairs
             A part is one flat array('d') of x0, y0, x1, y1, …: 16 bytes a
             point, as in the file. A list of (x, y) tuples cost ~112, and
             the real largest county's roads decoded to 6.3x the file
             (§12, 2026-10-03). Read a part through points().
             Raises ValueError with a slug-first message on malformed input
             (bad magic, truncation, unsupported shape type, record-count
             mismatch). Callers that must never raise (the Artemis ingest
             stack) catch it there; a decoder that guesses at garbage would
             be worse than one that refuses.

             Deliberately NOT registered with Pythia: a byte-level parser has
             no question the LLM could ask it directly — everything it decodes
             is served to the brain, when at all, through the Artemis layer
             modules that interpret the fields.

Law:         No network. No filesystem policy — bytes in, data out; opening
             files is the caller's job (the __main__ smoke check is the one
             allowed exception). No domain vocabulary: MTFCC, FULLNAME and
             friends mean nothing here; Hecate and Poseidon interpret what
             Cadmus hands back.

Formats:     .shp — 100-byte big/little-endian mixed header (magic 9994),
             then records of (number, length) big-endian headers + content.
             Only shape types 0 (null), 3 (PolyLine) and 5 (Polygon) are
             accepted — the TIGER/Line and Census cartographic-boundary
             sources Artemis feeds it are all lines and polygons; anything
             else is refused loudly rather than mis-parsed silently.
             .dbf — dBase III: 32-byte header, 32-byte field descriptors to a
             0x0D terminator, fixed-width records with a leading deletion
             flag. Values decode as Latin-1 (TIGER's superset of ASCII) and
             are returned stripped, as strings, keyed by field name.

Upstream:    tools/hecate.py, tools/poseidon.py, tools/artemis.py (reference
             layer) — the Artemis layer stack, per its spec.
Downstream:  none. Cadmus knows nothing about the rest of the stack.

Bounds:      read_pair checks the .dbf's declared record count against the
             .shp's BEFORE decoding a single attribute record. Each record
             becomes a dict, so a .dbf of one-byte records was the worst
             amplifier measured: ~96x, ~5.35 GB from one 53 MiB zip member
             (2026-10-03). Now the .dbf can hold no more records than the
             .shp, whose smallest record is 12 bytes.

Requires:    stdlib only — array, logging, struct, sys.
"""

import array
import logging
import struct
import sys
from collections.abc import Iterator

log = logging.getLogger("METIS.cadmus")

# ── Format constants ──────────────────────────────────────────────────────────

_SHP_MAGIC = 9994
_SHP_HEADER_LEN = 100

# Shape types this decoder accepts (see Formats above).
_TYPE_NULL = 0
_TYPE_POLYLINE = 3
_TYPE_POLYGON = 5

# One ring/part of a shape: its points as one flat array of doubles, x0, y0,
# x1, y1, … (see Contract: above; read it with points()). A ShapeRecord is the
# shape type plus its parts; a null shape has an empty parts list.
ShapePart = array.array[float]
ShapeRecord = tuple[int, list[ShapePart]]

_SWAP = sys.byteorder != "little"   # the file's doubles are little-endian


# ── .shp ──────────────────────────────────────────────────────────────────────

def read_shp(data: bytes) -> list[ShapeRecord]:
    """Decode a whole .shp byte stream into shape records, in file order.

    Raises ValueError (slug-first message) on bad magic, truncation, or a
    shape type outside {null, polyline, polygon}.
    """
    if len(data) < _SHP_HEADER_LEN:
        raise ValueError(f"bad_shp: {len(data)} bytes is shorter than the header")
    (magic,) = struct.unpack_from(">i", data, 0)
    if magic != _SHP_MAGIC:
        raise ValueError(f"bad_shp: magic {magic} != {_SHP_MAGIC}")
    (file_words,) = struct.unpack_from(">i", data, 24)
    file_len = file_words * 2
    if file_len > len(data):
        raise ValueError(f"bad_shp: header says {file_len} bytes, have {len(data)}")

    records: list[ShapeRecord] = []
    view = memoryview(data)
    offset = _SHP_HEADER_LEN
    while offset < file_len:
        if offset + 8 > file_len:
            raise ValueError(f"bad_shp: truncated record header at byte {offset}")
        (_, content_words) = struct.unpack_from(">ii", data, offset)
        content_len = content_words * 2
        offset += 8
        if offset + content_len > file_len:
            raise ValueError(f"bad_shp: truncated record content at byte {offset}")
        records.append(_read_shape(data, view, offset, content_len))
        offset += content_len
    return records


def _read_shape(data: bytes, view: memoryview, offset: int,
                content_len: int) -> ShapeRecord:
    """Decode one record's content: shape type, then the type's geometry."""
    if content_len < 4:
        raise ValueError(f"bad_shp: record content of {content_len} bytes at {offset}")
    (shape_type,) = struct.unpack_from("<i", data, offset)
    if shape_type == _TYPE_NULL:
        return (shape_type, [])
    if shape_type not in (_TYPE_POLYLINE, _TYPE_POLYGON):
        raise ValueError(f"unsupported_shape: type {shape_type} at byte {offset}")
    # PolyLine and Polygon share a layout: bbox (4 doubles), numParts,
    # numPoints, part start indices, then the points.
    num_parts, num_points = struct.unpack_from("<ii", data, offset + 36)
    need = 44 + 4 * num_parts + 16 * num_points
    if num_parts < 0 or num_points < 0 or content_len < need:
        raise ValueError(
            f"bad_shp: record at {offset} declares {num_parts} parts / "
            f"{num_points} points but holds {content_len} bytes"
        )
    part_starts = list(
        struct.unpack_from(f"<{num_parts}i", data, offset + 44)
    )
    coords_off = offset + 44 + 4 * num_parts

    # Each part is copied straight from the file's bytes into its own array:
    # no tuple of every float on the way, and no object per point.
    parts: list[ShapePart] = []
    bounds = part_starts + [num_points]
    for i in range(num_parts):
        lo, hi = bounds[i], bounds[i + 1]
        if not 0 <= lo <= hi <= num_points:
            raise ValueError(f"bad_shp: part index {lo}..{hi} outside {num_points} points")
        part = array.array("d")
        part.frombytes(view[coords_off + 16 * lo:coords_off + 16 * hi])
        if _SWAP:
            part.byteswap()
        parts.append(part)
    return (shape_type, parts)


def points(part: ShapePart) -> Iterator[tuple[float, float]]:
    """A part's points as (x, y) pairs, in file order: x is the longitude,
    y the latitude, for the Census files Artemis reads."""
    coords = iter(part)
    return zip(coords, coords)


# ── .dbf ──────────────────────────────────────────────────────────────────────

def read_dbf(data: bytes) -> list[dict[str, str]]:
    """Decode a dBase III .dbf byte stream into one dict per live record,
    field name -> stripped string value. Deleted records are skipped.

    Raises ValueError (slug-first message) on truncation or a malformed
    field-descriptor block.
    """
    record_count, header_size, record_size = _dbf_header(data)

    # Field descriptors: 32 bytes each from byte 32 up to the 0x0D terminator.
    fields: list[tuple[str, int]] = []  # (name, byte length)
    offset = 32
    while True:
        if offset >= len(data):
            raise ValueError("bad_dbf: no field-descriptor terminator")
        if data[offset] == 0x0D:
            break
        if offset + 32 > len(data):
            raise ValueError(f"bad_dbf: truncated field descriptor at byte {offset}")
        raw_name = data[offset:offset + 11].split(b"\x00", 1)[0]
        name = raw_name.decode("latin-1")
        length = data[offset + 16]
        fields.append((name, length))
        offset += 32
    if 1 + sum(length for _, length in fields) != record_size:
        raise ValueError(
            f"bad_dbf: fields sum to {sum(l for _, l in fields)}+1 bytes, "
            f"header says records are {record_size}"
        )
    if header_size + record_count * record_size > len(data):
        raise ValueError(
            f"bad_dbf: header says {record_count} records of {record_size} "
            f"bytes, have {len(data)} total"
        )

    records: list[dict[str, str]] = []
    for i in range(record_count):
        base = header_size + i * record_size
        if data[base:base + 1] == b"*":  # deletion flag
            continue
        record: dict[str, str] = {}
        pos = base + 1
        for name, length in fields:
            record[name] = data[pos:pos + length].decode("latin-1").strip()
            pos += length
        records.append(record)
    return records


def _dbf_header(data: bytes) -> tuple[int, int, int]:
    """The .dbf header's record count, header size and record size."""
    if len(data) < 32:
        raise ValueError(f"bad_dbf: {len(data)} bytes is shorter than the header")
    record_count, header_size, record_size = struct.unpack_from("<IHH", data, 4)
    return record_count, header_size, record_size


# ── Pairing ───────────────────────────────────────────────────────────────────

def read_pair(shp: bytes, dbf: bytes) -> list[tuple[ShapeRecord, dict[str, str]]]:
    """Decode a .shp/.dbf pair and zip them record-for-record.

    Raises ValueError if the two files disagree on record count — a mismatch
    means attributes would silently attach to the wrong geometry, the worst
    possible failure for a map. The count the .dbf declares is checked
    before any of its records is decoded (see Bounds: above); the count it
    actually holds (deleted records are skipped) is checked again after.
    """
    shapes = read_shp(shp)
    declared, _, _ = _dbf_header(dbf)
    if declared != len(shapes):
        raise ValueError(
            f"pair_mismatch: {len(shapes)} shapes but {declared} attribute records"
        )
    attrs = read_dbf(dbf)
    if len(shapes) != len(attrs):
        raise ValueError(
            f"pair_mismatch: {len(shapes)} shapes but {len(attrs)} attribute records"
        )
    return list(zip(shapes, attrs))


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    import sys

    if len(sys.argv) != 2 or not sys.argv[1].lower().endswith(".shp"):
        print("[Cadmus] usage: python tools/cadmus.py <path-to.shp>  "
              "(reads the sibling .dbf too)")
        raise SystemExit(2)
    shp_path = sys.argv[1]
    dbf_path = shp_path[:-4] + ".dbf"
    with open(shp_path, "rb") as f:
        shp_bytes = f.read()
    shapes_main = read_shp(shp_bytes)
    all_points = [pt for _, parts_main in shapes_main for part in parts_main
                  for pt in points(part)]
    xs = [x for x, _ in all_points]
    ys = [y for _, y in all_points]
    print(f"[Cadmus] {os.path.basename(shp_path)}: {len(shapes_main)} shapes, "
          f"{len(all_points)} points")
    if all_points:
        print(f"[Cadmus] bbox: x {min(xs):.6f}..{max(xs):.6f}  "
              f"y {min(ys):.6f}..{max(ys):.6f}")
    if os.path.exists(dbf_path):
        with open(dbf_path, "rb") as f:
            attrs_main = read_dbf(f.read())
        field_names = sorted(attrs_main[0]) if attrs_main else []
        print(f"[Cadmus] {os.path.basename(dbf_path)}: {len(attrs_main)} records, "
              f"fields: {', '.join(field_names)}")
    else:
        print(f"[Cadmus] no sibling .dbf at {dbf_path}")
