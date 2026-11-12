"""
sandbox/pythia_face_v2.py — Pythia v2: the hooded wire-frame construct
======================================================================
SANDBOX. Not part of the app: nothing in metis_toolbox/ imports this, and it
sits outside the app root, so neither the suite nor the palette guard ever
reads it. It moves into the app (as v1 did on 2026-09-01) once the look is
settled. Until then it is free to break every app rule it needs to.

Job:         Build Pythia v2 as one STATIC full-screen frame. A hooded figure
             drawn entirely as wire mesh: a faceted hood converging on a
             pointed apex, a dark triangulated face mask lit by two glowing
             eyes, a gold circuit band along the hood's opening that crosses
             at the chest, and cloak strands sweeping down. Dressing behind
             and around it: alchemical glyphs, faint schematic traces, dust,
             and a HUD frame with its footer text.

             Static first, on purpose. No animation loop yet.

Contract:    build_scene(view, colourway="reference", seed=7, layers=...)
                 -> Scene. PURE: no tk, no numpy. A display list of primitives
                 in REFERENCE coordinates (the 1312x1936 poster the look was
                 drawn from), plus the View that maps them to pixels and the
                 colourway's roles. The same seed draws the same figure.
             screen_view(w, h) -> View   the full-screen framing
             poster_view()     -> View   the reference's own framing, kept
                                         only to compare against it
             show(colourway, seed)       full screen; Escape or q closes it
             COLOURWAYS — "reference" (the poster's blue, gold and crimson)
                          and "phosphor" (Felhaven's own palette.C)

             Pixels come from pythia_v2_raster.render(scene); text is turned
             into strokes by pythia_v2_font, so it glows like the rest.

Stage:       Full screen is wider than the poster. screen_view() fits the
             poster's HEIGHT to the screen and centres the figure; everything
             that is not the figure lays itself out on view.stage() (the
             visible area in reference coordinates): the HUD frame sits at
             the screen's edges, the dust and the schematic run the full
             width, the glyphs ease outward, and the cloak flares on past the
             shoulders instead of stopping at the poster's edge.

Primitives:  line(pts, role, i, width)   light along a polyline
             dot(x, y, r, role, i)       a soft point of light, i at its peak
             glow(x, y, rx, ry, role, i) a wide soft halo
             patch(pts, role, i)         an emissive polygon (pads, bars)
             fill(pts, role, i)          an OPAQUE paint: covers the light
                                         drawn before it (the dark cavity
                                         in front of the hood behind it)
             Real bloom happens in the renderer, so nothing here draws a wide
             dim line under a bright one to fake a glow.

Density:     Mesh spacing is set in OUTPUT pixels and converted to reference
             units through the view, so a small window gets fewer, larger
             facets rather than the full screen's mesh shrunk to mush.

Requires:    math, random (stdlib). palette.py from metis_toolbox/ for the
             "phosphor" colourway only. show() needs tkinter and numpy.

Run:         python sandbox/pythia_face_v2.py [reference|phosphor] [seed]
Stills:      python sandbox/render_still.py   (headless, no display needed)
"""

import math
import os
import random
import sys
from dataclasses import dataclass, field

import pythia_v2_font as font

# --------------------------------------------------------------------------
# reference frame — every coordinate below is a pixel of the 1312x1936 poster
# --------------------------------------------------------------------------

REF_W = 1312.0
REF_H = 1936.0
CX = 656.0                     # the figure's axis of symmetry

APEX = (656.0, 84.0)
EYE_L = (548.0, 607.0)
EYE_R = (2 * CX - EYE_L[0], EYE_L[1])
V_BOTTOM = (656.0, 1196.0)     # where the hood's two front edges cross

# Hood, LEFT half, apex -> widest -> the hem tucking in above the shoulder.
# Measured off the reference row by row: 45 degrees off the apex, a break at
# y~300 where it meets the skull, then nearly straight and steep to y~900.
# Smoothed as an open curve and mirrored, so the apex stays a point.
HOOD_L = [
    APEX,
    (630, 108), (608, 130), (564, 170), (520, 210), (477, 250),
    (443, 290), (424, 330), (411, 370), (398, 410), (384, 450),
    (370, 490), (356, 530), (342, 570), (328, 610), (314, 650),
    (300, 690), (286, 730), (272, 770), (258, 810), (246, 850),
    (236, 885), (234, 912),     # widest: the bright node in the reference
    (244, 948), (258, 978), (276, 1004),   # the hem, resting on the shoulder
]
# Shoulder and arm, from under the hem out and down to below the bottom
# edge. On the poster everything past x=0 is off-canvas; on a wide screen it
# is the cloak's outer silhouette, so it is a real, lit edge.
SHOULDER_L = [(276, 1004), (214, 1030), (160, 1066), (104, 1104), (58, 1128),
              (14, 1162), (-52, 1226), (-112, 1322), (-158, 1450),
              (-190, 1620), (-210, 1830), (-218, 2050)]
CLOAK_TAIL_L = [(CX, 2050)]

# The hood's opening (gold-trimmed), LEFT half, brim peak -> the V at the
# chest. Close round the temples, opening wide below the cheekbones.
INNER_L = [
    (656, 392),
    (600, 404), (540, 424), (492, 452), (458, 480), (432, 524),
    (414, 582), (401, 650), (392, 730), (390, 810), (398, 880),
    (420, 950), (456, 1018), (505, 1074), (556, 1120), (608, 1162),
    V_BOTTOM,
]

# The face mask, LEFT half, forehead -> chin: a shield. Wide and nearly
# straight-sided down to the jaw corner, then a straight jaw to a pointed chin.
FACE_L = [
    (656, 400),
    (610, 405), (560, 418), (516, 440), (484, 470), (468, 520),
    (463, 580), (464, 640), (468, 700), (476, 760), (490, 820),
    (545, 867), (600, 914), (632, 942),
    (656, 962),
]

# The face's skeleton, LEFT half (mirrored; points on the axis stay put).
# What gives the reference its character is a few lines that catch the light
# harder than the rest — a brow that angles down to the nose, a long narrow
# bridge, high cheekbones, a hard jaw — over a filler mesh that stays dim.
# Each feature: (polyline, intensity, width).
FACE_FEATURES_L = {
    "brow":      ([(474, 544), (510, 548), (550, 557), (590, 570), (626, 588), (656, 598)], 0.72, 1.15),
    "bridge":    ([(656, 598), (656, 690), (656, 772)], 0.70, 1.15),
    "nose_side": ([(634, 612), (627, 690), (619, 750), (626, 779), (643, 792), (656, 788)], 0.50, 1.0),
    "cheekbone": ([(466, 640), (494, 688), (538, 706), (584, 712), (619, 750)], 0.72, 1.15),
    "zygoma":    ([(498, 598), (480, 628), (466, 640)], 0.42, 1.0),
    "smile":     ([(619, 768), (606, 810), (597, 850)], 0.48, 1.0),
    "lip_up":    ([(597, 850), (626, 840), (656, 846)], 0.52, 1.0),
    "lip_low":   ([(597, 850), (627, 868), (656, 872)], 0.45, 1.0),
    "jaw":       ([(476, 760), (490, 820), (545, 867), (600, 914), (656, 962)], 0.66, 1.15),
    "chin":      ([(614, 926), (656, 936)], 0.50, 1.0),
}
# The eye's lids, outer corner -> inner. The upper lid falls toward the nose
# with the brow; together they read as a stare, not a pair of lamps.
LID_UP_L = [(498, 598), (526, 587), (562, 587), (602, 605)]
LID_LOW_L = [(498, 598), (530, 619), (570, 623), (602, 605)]
# Where the light catches hardest: brighter nodes, as in the reference.
FACE_HUBS_L = [(474, 544), (626, 588), (538, 706), (490, 820), (597, 850),
               (626, 779), (656, 772), (656, 962), (656, 598)]
# A few extra seeds where the skeleton leaves the mesh to guess.
FACE_MARKS_L = [(548, 476), (604, 462), (577, 512), (512, 508), (630, 522),
                (520, 760), (562, 772), (530, 812), (566, 830), (566, 892)]
FACE_AXIS_Y = [440, 500, 560, 640, 730, 810, 900]


# --------------------------------------------------------------------------
# colourways
# --------------------------------------------------------------------------

# Prototype literals, sampled off the reference poster region by region. The
# app forbids a colour outside palette.py (CONVENTIONS §7,
# tests/test_palette_guard.py); this file is outside the app, and these are
# exactly the values a promotion would have to argue into palette.py — or
# drop in favour of the phosphor mapping below.
REFERENCE = {
    "bg":          "#05080F",
    "cloak_fill":  "#0A1A33",
    "cavity_fill": "#01040A",
    "mesh":        "#2F8FE0",
    "mesh_hot":    "#9EE6FF",
    "eye":         "#2E9BFF",
    "trim":        "#F5A83E",
    "glyph":       "#C2384A",
    "ash":         "#8E9098",
    "hud":         "#B88A3C",
    "text":        "#D8CDB0",
}


def _phosphor():
    """The same roles mapped onto Felhaven's own palette — what v2 looks like
    if it keeps the app's green-and-amber phosphor instead of the poster's
    blue."""
    here = os.path.dirname(os.path.abspath(__file__))
    app_root = os.path.join(os.path.dirname(here), "metis_toolbox")
    if app_root not in sys.path:
        sys.path.insert(0, app_root)
    from palette import C
    return {
        "bg":          C["bg"],
        "cloak_fill":  C["badge_bg"],
        "cavity_fill": C["bg"],
        "mesh":        C["text2"],
        "mesh_hot":    C["badge_fg"],
        "eye":         C["text1"],
        "trim":        C["amber"],
        "glyph":       C["red"],
        "ash":         C["text3"],
        "hud":         C["amber"],
        "text":        C["text1"],
    }


COLOURWAYS = {"reference": lambda: REFERENCE, "phosphor": _phosphor}


# --------------------------------------------------------------------------
# geometry — pure functions
# --------------------------------------------------------------------------

def catmull(pts, k=6):
    """Catmull-Rom through pts (open curve), k samples per span."""
    if len(pts) < 3:
        return list(pts)
    P = [pts[0]] + list(pts) + [pts[-1]]
    out = []
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        for s in range(k):
            t = s / k
            t2, t3 = t * t, t * t * t
            out.append(tuple(
                0.5 * (2 * p1[a] + (-p0[a] + p2[a]) * t
                       + (2 * p0[a] - 5 * p1[a] + 4 * p2[a] - p3[a]) * t2
                       + (-p0[a] + 3 * p1[a] - 3 * p2[a] + p3[a]) * t3)
                for a in (0, 1)))
    out.append(tuple(pts[-1]))
    return out


def mirror_x(pts):
    return [(2 * CX - x, y) for x, y in pts]


def close_halves(left):
    """Left half (centre top -> centre bottom) -> the full closed outline."""
    return list(left) + mirror_x(list(reversed(left[1:-1])))


def point_in_poly(x, y, poly):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def dist_to_seg(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    qx, qy = ax + t * dx, ay + t * dy
    return math.hypot(px - qx, py - qy)


class Near:
    """Distance from a point to a polyline, exact up to rmax and capped there.

    The segments are bucketed on a grid of rmax cells, so a lookup tests only
    the handful near the point. Every caller here only cares within a few
    facets of an edge (rim light, keep-outs), and the brute-force version was
    nearly all of the scene's build time.
    """

    def __init__(self, path, rmax):
        self.r = float(rmax)
        self.grid = {}
        for (ax, ay), (bx, by) in zip(path, path[1:]):
            for gx in range(int(min(ax, bx) // rmax) - 1, int(max(ax, bx) // rmax) + 2):
                for gy in range(int(min(ay, by) // rmax) - 1, int(max(ay, by) // rmax) + 2):
                    self.grid.setdefault((gx, gy), []).append((ax, ay, bx, by))

    def __call__(self, x, y):
        segs = self.grid.get((int(x // self.r), int(y // self.r)))
        if not segs:
            return self.r
        return min(self.r, min(dist_to_seg(x, y, *seg) for seg in segs))


def densify(path, maxlen):
    """The path's own vertices, plus evenly spaced points on any span longer
    than maxlen — so the corners survive, unlike resample()."""
    out = [tuple(path[0])]
    for (ax, ay), (bx, by) in zip(path, path[1:]):
        n = max(1, int(math.ceil(math.hypot(bx - ax, by - ay) / maxlen)))
        out += [(ax + (bx - ax) * k / n, ay + (by - ay) * k / n) for k in range(1, n + 1)]
    return out


def resample(path, spacing, closed=False):
    """Points every ~spacing along a polyline."""
    pts = list(path) + ([path[0]] if closed else [])
    out = [pts[0]]
    carry = 0.0
    for i in range(len(pts) - 1):
        (ax, ay), (bx, by) = pts[i], pts[i + 1]
        seg = math.hypot(bx - ax, by - ay)
        d = spacing - carry
        while d <= seg:
            t = d / seg
            out.append((ax + (bx - ax) * t, ay + (by - ay) * t))
            d += spacing
        carry = seg - (d - spacing)
    if not closed and math.hypot(out[-1][0] - pts[-1][0], out[-1][1] - pts[-1][1]) > spacing * 0.4:
        out.append(pts[-1])
    return out


def normals(path):
    """Unit left-normals per vertex, averaged across the two adjacent spans."""
    out = []
    n = len(path)
    for i in range(n):
        ax, ay = path[max(i - 1, 0)]
        bx, by = path[min(i + 1, n - 1)]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy) or 1.0
        out.append((-dy / L, dx / L))
    return out


def offset(path, d, nrm=None):
    nrm = nrm or normals(path)
    return [(x + nx * d, y + ny * d) for (x, y), (nx, ny) in zip(path, nrm)]


def bezier(p0, p1, p2, p3, n=24):
    out = []
    for s in range(n + 1):
        t = s / n
        u = 1 - t
        out.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return out


def scatter(rng, bbox, spacing_at, keep, cell, tries, seeds=()):
    """Dart-throwing Poisson disc with a spacing that varies over the plane.

    `seeds` are fixed points (landmarks, boundary samples) that the scatter
    keeps its distance from but never returns.
    """
    x0, y0, x1, y1 = bbox
    grid = {}

    def put(p):
        grid.setdefault((int(p[0] // cell), int(p[1] // cell)), []).append(p)

    for p in seeds:
        put(p)
    out = []
    for _ in range(tries):
        x = x0 + rng.random() * (x1 - x0)
        y = y0 + rng.random() * (y1 - y0)
        if not keep(x, y):
            continue
        r = spacing_at(x, y)
        gx, gy, reach = int(x // cell), int(y // cell), int(math.ceil(r / cell))
        ok = True
        for i in range(gx - reach, gx + reach + 1):
            for j in range(gy - reach, gy + reach + 1):
                for qx, qy in grid.get((i, j), ()):
                    if (qx - x) ** 2 + (qy - y) ** 2 < r * r:
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                break
        if ok:
            put((x, y))
            out.append((x, y))
    return out


def delaunay(pts):
    """Bowyer-Watson. Triangles as index triples into pts. O(n^2) — fine for
    the few hundred to ~1500 points a region holds."""
    n = len(pts)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    mx, my = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    d = max(max(xs) - min(xs), max(ys) - min(ys)) * 20 + 1
    P = list(pts) + [(mx - d, my - d), (mx + d, my - d), (mx, my + d)]

    def circ(a, b, c):
        ax, ay = P[a]
        bx, by = P[b]
        cx, cy = P[c]
        dd = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
        if abs(dd) < 1e-12:
            return (0.0, 0.0, float("inf"))
        a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
        ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / dd
        uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / dd
        return (ux, uy, (ax - ux) ** 2 + (ay - uy) ** 2)

    tris = {(n, n + 1, n + 2): circ(n, n + 1, n + 2)}
    for i in range(n):
        px, py = P[i]
        bad = [t for t, (ux, uy, r2) in tris.items()
               if (px - ux) ** 2 + (py - uy) ** 2 < r2]
        rim = {}
        for t in bad:
            for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
                k = (a, b) if a < b else (b, a)
                rim[k] = rim.get(k, 0) + 1
            del tris[t]
        for (a, b), count in rim.items():
            if count == 1:
                tris[(a, b, i)] = circ(a, b, i)
    return [t for t in tris if max(t) < n]


def tri_edges(pts, tris, keep_tri):
    """Unique edges of the kept triangles, as index pairs."""
    edges = set()
    for t in tris:
        if keep_tri(*(pts[i] for i in t)):
            for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
                edges.add((a, b) if a < b else (b, a))
    return sorted(edges)


def smoothstep(e0, e1, x):
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


# --------------------------------------------------------------------------
# outlines, built once
# --------------------------------------------------------------------------

def _outlines():
    hood_l = catmull(HOOD_L, 5)
    shoulder_l = catmull(SHOULDER_L, 4)
    cloak = close_halves(hood_l + shoulder_l[1:] + CLOAK_TAIL_L)
    inner_l = catmull(INNER_L, 5)
    face_l = catmull(FACE_L, 5)
    return {
        "hood_l": hood_l, "hood_r": mirror_x(hood_l),
        "shoulder_l": shoulder_l, "shoulder_r": mirror_x(shoulder_l),
        "cloak": cloak,
        "inner_l": inner_l, "inner_r": mirror_x(inner_l),
        "cavity": close_halves(inner_l),
        "face_l": face_l, "face": close_halves(face_l),
    }


OUT = _outlines()

# --------------------------------------------------------------------------
# view + scene
# --------------------------------------------------------------------------

@dataclass
class View:
    """Reference coords -> pixels: X = (x - x0) * scale."""
    w: int
    h: int
    x0: float
    y0: float
    scale: float

    def px(self, x, y):
        return ((x - self.x0) * self.scale, (y - self.y0) * self.scale)

    @property
    def stage(self):
        """The visible area, in reference coordinates: (x0, y0, x1, y1)."""
        return (self.x0, self.y0,
                self.x0 + self.w / self.scale, self.y0 + self.h / self.scale)


def screen_view(w, h):
    """The full-screen framing: the whole poster fits (its height, on any
    landscape screen), centred; the stage runs on past it on either side."""
    scale = min(w / REF_W, h / REF_H)
    return View(w, h, CX - w / scale / 2, REF_H / 2 - h / scale / 2, scale)


def poster_view(w=1312, h=1936):
    """The reference's own portrait framing — only for comparing against it."""
    return screen_view(w, h)


@dataclass
class Scene:
    view: View
    roles: dict
    prims: list = field(default_factory=list)

    def line(self, pts, role, i, width=1.0):
        self.prims.append(("line", pts, role, i, width))

    def dot(self, x, y, r, role, i):
        self.prims.append(("dot", x, y, r, role, i))

    def glow(self, x, y, rx, ry, role, i):
        self.prims.append(("glow", x, y, rx, ry, role, i))

    def patch(self, pts, role, i):
        self.prims.append(("patch", pts, role, i))

    def fill(self, pts, role, i):
        self.prims.append(("fill", pts, role, i))

    def ring(self, x, y, rx, ry, role, i, width=1.0):
        n = max(16, int(max(rx, ry) / 3))
        self.line([(x + rx * math.cos(2 * math.pi * k / n), y + ry * math.sin(2 * math.pi * k / n))
                   for k in range(n + 1)], role, i, width)

    def text(self, x, y, s, role, i, size, anchor="center", min_px=0):
        """Stroke text. min_px floors the size in screen pixels, so the HUD
        stays legible however far the poster is scaled down to fit."""
        size = max(size, min_px / self.view.scale)
        width = max(1.0, size * self.view.scale * 0.075) / self.view.scale
        for stroke in font.strokes(s, x, y, size, anchor):
            self.line(stroke, role, i, width)


ALL_LAYERS = ("glyphs", "particles", "figure", "hud")


def build_scene(view, colourway="reference", seed=7, layers=ALL_LAYERS):
    rng = random.Random(seed)
    sc = Scene(view, COLOURWAYS[colourway]())
    # Reference px per output px. Mesh spacing is chosen in output pixels.
    per_px = 1.0 / view.scale

    if "glyphs" in layers:
        _backdrop(sc, rng)
        _glyphs(sc, rng)
    if "particles" in layers:
        _particles(sc, rng, behind=True)
    if "figure" in layers:
        _cloak(sc, rng, per_px)
        _strands(sc, rng)
        _cavity(sc, rng, per_px)
        _face(sc, rng, per_px)
        _trim(sc, rng)
    if "particles" in layers:
        _particles(sc, rng, behind=False)
    if "hud" in layers:
        _hud(sc, rng)
    return sc


def _visible(sc, x, margin=0.0):
    sx0, _, sx1, _ = sc.view.stage
    return sx0 - margin <= x <= sx1 + margin


# --------------------------------------------------------------------------
# backdrop: a faint lift behind the head, then the schematic
# --------------------------------------------------------------------------

def _backdrop(sc, rng):
    sx0, _, sx1, _ = sc.view.stage
    # The barest lift behind the head, so the black is not dead flat.
    sc.glow(CX, 760, 1500, 1700, "cloak_fill", 0.14)

    # Manhattan traces: faint red circuitry either side of the hood, densest
    # near the figure and thinning toward the screen's edges.
    for near, far in ((470, sx0 + 80), (850, sx1 - 80)):
        lo, hi = min(near, far), max(near, far)
        count = int(16 * (hi - lo) / 410)
        for _ in range(count):
            x = near + (far - near) * rng.random() ** 1.5
            y = rng.uniform(220, 1060)
            pts = [(x, y)]
            horiz = rng.random() < 0.5
            for _seg in range(rng.randint(2, 5)):
                L = rng.uniform(30, 170) * rng.choice((-1, 1))
                x = min(max(x + (L if horiz else 0), lo), hi)
                y = min(max(y + (0 if horiz else L), 200), 1080)
                pts.append((x, y))
                horiz = not horiz
            sc.line(pts, "glyph", rng.uniform(0.18, 0.36))
            if rng.random() < 0.5:
                sc.ring(*pts[-1], 4, 4, "glyph", 0.36)
        for _ in range(max(2, count // 4)):
            x = near + (far - near) * rng.random() ** 1.5
            x = min(max(x, lo), hi - 100)
            y = rng.uniform(240, 1000)
            w, h = rng.uniform(50, 120), rng.uniform(30, 90)
            sc.line([(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)],
                    "glyph", rng.uniform(0.16, 0.26))


def _crescent(cx, cy, R, r, d, step=5):
    """Outline of a crescent lit on the right: disc (cx, cy, R) minus the disc
    (cx - d, cy, r). Returns one closed polyline, horns on the left."""
    u = (r * r - R * R - d * d) / (2 * d)       # x of the horns, from cx
    h = math.sqrt(max(R * R - u * u, 0.0))
    t0 = math.degrees(math.atan2(h, u))         # outer arc: -t0 .. t0 via 0
    p0 = math.degrees(math.atan2(h, u + d))     # inner arc: p0 .. -p0 via 0
    outer = [(cx + R * math.cos(math.radians(a)), cy + R * math.sin(math.radians(a)))
             for a in _span(-t0, t0, step)]
    inner = [(cx - d + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
             for a in _span(p0, -p0, step)]
    return outer + inner + outer[:1]


def _span(a, b, step):
    n = max(2, int(abs(b - a) / step))
    return [a + (b - a) * k / n for k in range(n + 1)]


def _glyphs(sc, rng):
    """The alchemical marks flanking the hood. On a wide stage each one eases
    outward (up to 1.45x its distance from the axis) as a whole, so the
    figure gets room without the marks drifting off into the margins."""
    sx0, _, sx1, _ = sc.view.stage
    spread = min(1.45, max(1.0, (sx1 - sx0) / REF_W))

    def at(x):
        """How far a mark anchored at x moves."""
        return (x - CX) * (spread - 1.0)

    g = "glyph"
    # Sol — the alchemical mark for gold — top left, "Au" beneath.
    x, y = 270 + at(270), 268
    sc.ring(x, y, 48, 48, g, 0.85, 2)
    sc.ring(x, y, 30, 30, g, 0.45)
    sc.dot(x, y, 7, g, 0.95)
    sc.line([(x, y - 92), (x, y - 48)], g, 0.5)
    sc.line([(x, y + 48), (x, y + 78)], g, 0.5)
    sc.line([(x - 84, y), (x - 48, y)], g, 0.45)
    sc.line([(x + 48, y), (x + 84, y)], g, 0.45)
    sc.text(x, y + 84, "Au", g, 0.75, 26, min_px=13)
    sc.text(405 + at(405), 265, "Au", g, 0.50, 18, min_px=11)

    # The element itself, top right, in ash grey, under a small ring.
    dx = at(1012)
    sc.text(1012 + dx, 246, "Au", "ash", 0.85, 74)
    sc.ring(978 + dx, 168, 15, 15, "ash", 0.75, 2)
    sc.line([(978 + dx, 183), (978 + dx, 212)], "ash", 0.75, 2)
    sc.text(1004 + dx, 298, "Au", "ash", 0.65, 22, min_px=12)

    # Luna — a crescent right of the hood, engraved with a second inner line.
    dx = at(1104)
    sc.line(_crescent(1104 + dx, 402, 74, 66, 30), g, 0.90, 2)
    sc.line(_crescent(1106 + dx, 402, 64, 60, 30), g, 0.38)

    # Moon within the circle, mid left, with its small finial.
    x, y = 125 + at(125), 868
    sc.ring(x, y, 62, 62, g, 0.65, 2)
    sc.ring(x, y, 54, 54, g, 0.28)
    sc.line(_crescent(x + 6, y, 40, 36, 18), g, 0.65)
    sc.ring(x, y - 82, 9, 9, g, 0.6)
    sc.line([(x, y - 73), (x, y - 62)], g, 0.55)
    for a in (200, 340):
        ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
        sc.line([(x + 62 * ca, y + 62 * sa), (x + 92 * ca, y + 92 * sa)], g, 0.45)

    # Lattice — a square of diagonals and a rotated square, mid right.
    x, y, s = 1190 + at(1190), 858, 80
    sq = [(x - s, y - s), (x + s, y - s), (x + s, y + s), (x - s, y + s), (x - s, y - s)]
    sc.line(sq, g, 0.70, 2)
    sc.line([(x - s, y - s), (x + s, y + s)], g, 0.5)
    sc.line([(x + s, y - s), (x - s, y + s)], g, 0.5)
    sc.line([(x, y - s), (x + s, y), (x, y + s), (x - s, y), (x, y - s)], g, 0.5)
    for k in (-0.5, 0.5):
        sc.line([(x + k * s, y - s), (x + k * s, y + s)], g, 0.28)
        sc.line([(x - s, y + k * s), (x + s, y + k * s)], g, 0.28)
    sc.line([(x, y - s - 40), (x, y - s)], g, 0.4)
    sc.line([(x - s - 30, y), (x - s, y)], g, 0.4)


# --------------------------------------------------------------------------
# the figure
# --------------------------------------------------------------------------

def _cloak(sc, rng, per_px):
    """Hood, shoulders and arms as one triangulated shell round the opening.

    Bright and fine in the hood, coarser and dimmer down the cloak — the
    strands carry the cloak, the mesh only gives it a surface.
    """
    cloak, cavity = OUT["cloak"], OUT["cavity"]
    # left shoulder -> up the left of the hood -> apex -> down the right
    hood_edge = OUT["hood_l"][::-1] + OUT["hood_r"][1:]
    shoulders = (OUT["shoulder_l"], OUT["shoulder_r"])
    rim_edge = OUT["inner_l"][::-1] + OUT["inner_r"][1:]

    # Facet size in output pixels, converted to reference units.
    base = max(per_px * 12.0, 23.0)

    def spacing_at(x, y):
        return base * (0.9 + 1.4 * smoothstep(250, 1500, y))

    sc.fill(cloak, "cloak_fill", 0.30)
    arms = [p for p in OUT["shoulder_l"][::-1] if p[1] < 2000]
    arms += [None] + [p for p in OUT["shoulder_r"] if p[1] < 2000]
    near_out = Near(hood_edge, base * 2.0)
    near_arm = Near(arms[:arms.index(None)], base * 2.0)
    near_arm_r = Near(arms[arms.index(None) + 1:], base * 2.0)
    near_in = Near(rim_edge, base * 1.8)

    def keep(x, y):
        return (_visible(sc, x, base * 3) and near_in(x, y) > base * 0.45
                and point_in_poly(x, y, cloak) and not point_in_poly(x, y, cavity))

    seeds = [APEX]
    seeds += resample(hood_edge, base * 0.9)
    for sh in shoulders:
        seeds += resample(sh, base * 1.3)[1:]
    seeds += resample(rim_edge, base * 0.8)
    seeds = [p for p in seeds if _visible(sc, p[0], base * 3) and p[1] < 2060]
    bx0 = max(-400.0, sc.view.stage[0] - base * 3)
    bx1 = min(REF_W + 400.0, sc.view.stage[2] + base * 3)
    area = (bx1 - bx0) * REF_H * 0.55
    fill = scatter(rng, (bx0, 60, bx1, 2050), spacing_at, keep,
                   cell=base * 0.9, tries=int(area / (base * base) * 7), seeds=seeds)
    pts = seeds + fill
    # break the exact ties a mirrored outline makes for the Delaunay test
    pts = [(x + rng.uniform(-0.3, 0.3), y + rng.uniform(-0.3, 0.3)) for x, y in pts]
    tris = delaunay(pts)

    def keep_tri(a, b, c):
        mx, my = (a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3
        if not point_in_poly(mx, my, cloak) or point_in_poly(mx, my, cavity):
            return False
        lim = spacing_at(mx, my) * 2.6
        return all(math.dist(p, q) < lim for p, q in ((a, b), (b, c), (c, a)))

    edges = tri_edges(pts, tris, keep_tri)

    def lit(x, y):
        """(glow, body): rim light off the silhouette and the opening, and
        how much of the hood's brightness this point gets — full in the hood,
        falling away down the cloak."""
        d_out = near_out(x, y)
        d_arm = min(near_arm(x, y), near_arm_r(x, y))
        d_in = near_in(x, y)
        glow = (0.45 * max(0.0, 1 - d_out / (base * 2.0))
                + 0.30 * max(0.0, 1 - d_arm / (base * 2.0)) * (1 - 0.5 * smoothstep(1300, 2000, y))
                + 0.35 * max(0.0, 1 - d_in / (base * 1.8)))
        in_hood = y < 1010 and abs(x - CX) < 430
        if in_hood:
            body = 1.0
        else:
            body = (0.68 - 0.36 * smoothstep(1150, 1900, y)) * (1 - 0.40 * smoothstep(320, 760, abs(x - CX)))
        return glow, body

    for a, b in edges:
        (ax, ay), (bx, by) = pts[a], pts[b]
        glow, body = lit((ax + bx) / 2, (ay + by) / 2)
        sc.line([(ax, ay), (bx, by)], "mesh", rng.uniform(0.55, 0.95) * body + glow)

    # Meridians: the hood's long facets, converging on the apex.
    for side in (OUT["inner_l"], OUT["inner_r"]):
        for idx in (4, 12, 22, 34):
            tx, ty = side[idx]
            mx, my = (APEX[0] + tx) / 2, (APEX[1] + ty) / 2
            bow = (mx - CX) * 0.25
            sc.line(bezier(APEX, (mx + bow, my - 30), (tx + bow * 0.5, ty - 80), (tx, ty), 16),
                    "mesh", 0.80)
    for side in (OUT["hood_l"], OUT["hood_r"]):
        for idx in (40, 70, 95):
            tx, ty = side[idx]
            sc.line(bezier(APEX, (APEX[0] + (tx - CX) * 0.35, 260),
                           (tx * 0.85 + CX * 0.15, ty - 120), (tx, ty), 16), "mesh", 0.62)
    sc.line([APEX, (656, 392)], "mesh_hot", 1.05, 1.5)     # the ridge

    # Silhouette: the hot edge. Shoulders and arms a step dimmer.
    sc.line(hood_edge, "mesh_hot", 0.95, 1.5)
    for sh in shoulders:
        sc.line(sh, "mesh_hot", 0.70, 1.2)
    for sign in (-1, 1):
        hem = [(CX + sign * (CX - x), y) for x, y in
               bezier((276, 1004), (330, 1080), (420, 1150), (560, 1214), 20)]
        sc.line(hem, "mesh_hot", 0.80, 1.4)

    # Nodes at the vertices; a few flare.
    sx0, sy0, sx1, sy1 = sc.view.stage
    for x, y in pts:
        if not (sx0 <= x <= sx1 and sy0 <= y <= sy1):
            continue
        glow, body = lit(x, y)
        roll = rng.random()
        if roll < 0.10:
            sc.dot(x, y, 8, "mesh", 0.30 * body + 0.1)
            sc.dot(x, y, 3.0, "mesh_hot", 1.20 + glow * 0.3)
        elif roll < 0.70:
            sc.dot(x, y, 1.9, "mesh_hot", (0.50 + glow) * body + 0.1)
    # The hub nodes the reference flares hardest: the hood's widest points
    # and the shoulder tips.
    for x, y in ((234, 912), (2 * CX - 234, 912), (58, 1128), (2 * CX - 58, 1128)):
        sc.dot(x, y, 14, "mesh", 0.30)
        sc.dot(x, y, 7, "mesh_hot", 0.80)
        sc.dot(x, y, 3.5, "mesh_hot", 1.40)


def _strands(sc, rng):
    """Long cloak strands: fans out of a few hub points on each side that
    sweep down, cross the chest, and run off the bottom edge — plus, on a wide
    stage, drapes down the arms. They, not the mesh, are what make the cloak
    read as cloth."""
    for sign in (-1, 1):
        def sx(x):
            return CX + sign * (x - CX)
        hubs = [((58, 1128), 12), ((234, 912), 8), ((300, 1150), 10),
                ((470, 1060), 8), ((160, 1066), 8)]
        for (hx, hy), n in hubs:
            for k in range(n):
                t = (k + rng.random()) / n
                p0 = (sx(hx + rng.uniform(-10, 10)), hy + rng.uniform(-10, 10))
                # cross the chest to the far side, or fall down the near side
                if rng.random() < 0.72:
                    p3 = (sx(600 + 420 * t + rng.uniform(-40, 40)), 2010)
                    p1 = (sx(hx + 120 + 140 * t), hy + 160 + 100 * t)
                    p2 = (sx(560 + 260 * t + rng.uniform(-40, 40)), 1480 + rng.uniform(-80, 80))
                else:
                    p3 = (sx(hx - 120 + 260 * t), 2010)
                    p1 = (sx(hx + 30), hy + 260)
                    p2 = (sx(hx - 60 + 200 * t), 1650)
                pts = bezier(p0, p1, p2, p3, 30)
                roll = rng.random()
                if roll < 0.12:
                    sc.line(pts, "trim", rng.uniform(0.40, 0.62))
                elif roll < 0.32:
                    sc.line(pts, "mesh_hot", rng.uniform(0.55, 0.90))
                else:
                    sc.line(pts, "mesh", rng.uniform(0.65, 1.10))
                for _ in range(rng.randint(0, 3)):
                    x, y = pts[rng.randint(3, len(pts) - 4)]
                    sc.dot(x, y, 2.4, "mesh_hot", rng.uniform(0.8, 1.3))
        # the hood's front drape: long lines from the hem down past the lapels
        for k in range(7):
            x0 = sx(rng.uniform(300, 470))
            y0 = rng.uniform(1010, 1120)
            sc.line(bezier((x0, y0), (sx(500), y0 + 220), (sx(760 + 60 * k), 1500),
                           (sx(820 + 70 * k), 2010), 26), "mesh", rng.uniform(0.60, 1.05))
        # down the arms, outside the poster's edge
        if _visible(sc, sx(-120)):
            for k in range(8):
                t = (k + rng.random()) / 8
                top = (sx(60 - 200 * t), 1140 + 260 * t ** 1.4)
                bot = (sx(110 - 300 * t + rng.uniform(-24, 24)), 2010)
                # bellying outward over the arm, then back in at the hem
                sc.line(bezier(top, (top[0] - sign * 60, top[1] + 260),
                               (bot[0] - sign * 90, 1720), bot, 24),
                        "mesh", rng.uniform(0.30, 0.55))


def _cavity(sc, rng, per_px):
    """The dark inside of the hood: near-black, a dim web from rim to mask."""
    cavity, face = OUT["cavity"], OUT["face"]
    sc.fill(cavity, "cavity_fill", 1.0)
    base = max(per_px * 14.0, 32.0)
    seeds = resample(cavity, base, closed=True) + resample(face, base, closed=True)
    fill = scatter(rng, (360, 380, 960, 1200), lambda x, y: base * 1.4,
                   lambda x, y: point_in_poly(x, y, cavity) and not point_in_poly(x, y, face),
                   cell=base, tries=500, seeds=seeds)
    pts = [(x + rng.uniform(-0.3, 0.3), y + rng.uniform(-0.3, 0.3)) for x, y in seeds + fill]

    def keep_tri(a, b, c):
        mx, my = (a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3
        return point_in_poly(mx, my, cavity) and not point_in_poly(mx, my, face)

    for a, b in tri_edges(pts, delaunay(pts), keep_tri):
        sc.line([pts[a], pts[b]], "mesh", rng.uniform(0.30, 0.60))
    for x, y in fill:
        if rng.random() < 0.6:
            sc.dot(x, y, 2.0, "mesh_hot", rng.uniform(0.45, 1.0))
    # the neck: a few lines from the chin down into the V
    for k in range(7):
        x0 = CX + rng.uniform(-60, 60)
        sc.line(bezier((x0, 955), (x0 + rng.uniform(-40, 40), 1030),
                       (CX + rng.uniform(-120, 120), 1110), (CX + rng.uniform(-50, 50), 1190), 14),
                "mesh", rng.uniform(0.30, 0.65))


def _face_shade(x, y):
    """How much light the face gets at a point, about 0.15 .. 1.3.

    The reference is lit unevenly, and that is most of its character: the
    forehead sits in the brim's shadow, the eyes burn inside dark sockets,
    the light gathers down the nose, the cheekbones and the mouth, and the
    cheeks fall into hollows under the bone.
    """
    s = 0.42 + 0.58 * smoothstep(430, 585, y)              # under the brim
    for ex, ey in (EYE_L, EYE_R):                          # the sockets
        d = ((x - ex) / 80.0) ** 2 + ((y - ey + 6) / 42.0) ** 2
        s *= 0.28 + 0.72 * smoothstep(0.55, 1.7, d)
    ax = abs(x - CX)                                       # cheek hollows
    hollow = smoothstep(70, 130, ax) * smoothstep(712, 770, y) * (1 - smoothstep(850, 905, y))
    s *= 1 - 0.50 * hollow
    s += 0.38 * math.exp(-((x - CX) / 105.0) ** 2 - ((y - 745) / 115.0) ** 2)   # midface
    return s


def _face(sc, rng, per_px):
    """The mask: a skeleton of feature lines over a dim triangulated mesh,
    lit unevenly (_face_shade). Triangulated on the left half and mirrored,
    so it is exactly symmetric. It has no outline of its own — it is told
    from the cavity by its mesh — and each eye burns inside a socket cut out
    of that mesh, under a brow that angles down to the nose."""
    face_l = OUT["face_l"]
    half = face_l + [(CX, face_l[-1][1]), (CX, face_l[0][1])]

    def mirror(p):
        return (2 * CX - p[0], p[1])

    # Denser down the middle of the face, where the features crowd; sparser
    # at the temples and up the shaded forehead.
    base = max(per_px * 9.0, 30.0)

    def spacing_at(x, y):
        return (base * (0.80 + 0.50 * smoothstep(50, 190, abs(x - CX)))
                * (1.0 + 0.35 * (1 - smoothstep(470, 570, y))))

    seeds = [(float(x), float(y)) for x, y in FACE_MARKS_L]
    seeds += [(CX, float(y)) for y in FACE_AXIS_Y]
    for line, _i, _w in FACE_FEATURES_L.values():
        seeds += [p for p in densify(line, base * 1.1) if p[0] <= CX]
    seeds += LID_UP_L + LID_LOW_L[1:-1]
    seeds += resample(face_l, base * 1.1)
    seeds = list(dict.fromkeys((round(x, 2), round(y, 2)) for x, y in seeds))
    fill = scatter(rng, (440, 400, CX - 6, 970), spacing_at,
                   lambda x, y: point_in_poly(x, y, half) and x < CX - 10,
                   cell=base * 0.8, tries=900, seeds=seeds)
    pts = seeds + fill

    def keep_tri(a, b, c):
        mx, my = (a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3
        return point_in_poly(mx, my, half)

    # The socket is the lids' own almond: drop every edge across the eye.
    almond = LID_UP_L + LID_LOW_L[::-1][1:-1]
    edges = [(a, b) for a, b in tri_edges(pts, delaunay(pts), keep_tri)
             if not point_in_poly((pts[a][0] + pts[b][0]) / 2,
                                  (pts[a][1] + pts[b][1]) / 2, almond)]

    # The filler mesh: dim, and dimmer still toward the mask's edge, so the
    # skeleton carries the face.
    near_rim = Near(face_l, 40.0)
    for a, b in edges:
        pa, pb = pts[a], pts[b]
        mx, my = (pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2
        fade = 0.55 + 0.45 * near_rim(mx, my) / 40.0
        i = rng.uniform(0.42, 0.66) * _face_shade(mx, my) * fade
        sc.line([pa, pb], "mesh_hot", i)
        if not (pa[0] == CX and pb[0] == CX):
            sc.line([mirror(pa), mirror(pb)], "mesh_hot", i)

    # The skeleton, over it: span by span, each a little brighter or dimmer
    # than its neighbours, so it reads as mesh edges that happen to line up
    # along the bone, not as a stroke drawn on top.
    for line, i, width in FACE_FEATURES_L.values():
        on_axis = all(x == CX for x, _ in line)
        for a, b in zip(line, line[1:]):
            k = i * rng.uniform(0.70, 1.15)
            sc.line([a, b], "mesh_hot", k, width)
            if not on_axis:
                sc.line([mirror(a), mirror(b)], "mesh_hot", k, width)

    # Nodes: dim where the face is in shadow, a few catching hard; the hubs
    # of the skeleton brightest of all.
    hubs = set(FACE_HUBS_L)
    for x, y in pts:
        if (x, y) in hubs:
            continue
        for p in ((x, y), mirror((x, y))) if x < CX else ((x, y),):
            hot = rng.random() < 0.12
            sc.dot(*p, 2.4 if hot else 1.8, "mesh_hot",
                   (0.30 + 0.45 * _face_shade(*p)) * (1.7 if hot else 1.0))
    for x, y in FACE_HUBS_L:
        for p in ((x, y), mirror((x, y))) if x < CX else ((x, y),):
            sc.dot(*p, 8, "mesh", 0.30)
            sc.dot(*p, 3.2, "mesh_hot", 1.35)

    # The eyes: lids traced in the eye's own light, then a small, intense
    # core with a short flare. Small on purpose — a stare, not headlamps.
    for lid, i in ((LID_UP_L, 0.42), (LID_LOW_L, 0.20)):
        sc.line(lid, "eye", i, 1.3)
        sc.line([mirror(p) for p in lid], "eye", i, 1.3)
    for ex, ey in (EYE_L, EYE_R):
        sc.glow(ex, ey, 120, 70, "eye", 0.07)
        sc.glow(ex, ey, 44, 24, "eye", 0.36)
        sc.glow(ex, ey, 17, 10, "eye", 1.00)
        sc.dot(ex, ey, 3.6, "mesh_hot", 1.9)
        sc.line([(ex - 36, ey), (ex + 36, ey)], "eye", 0.30)
        sc.line([(ex - 14, ey), (ex + 14, ey)], "eye", 0.80, 1.3)


def _trim(sc, rng):
    """Gold circuit band along the hood's opening: rails with rungs and
    traces between and off them, crossing at the chest into the lapels."""
    il = OUT["inner_l"]
    lapel = [V_BOTTOM, (742, 1268), (812, 1330), (868, 1396)]
    # left trim: lapel end (lower right) -> the V -> up the left rim -> brim
    left = lapel[::-1][:-1] + il[::-1]
    for path in (left, mirror_x(left)):
        nrm = normals(path)
        # choose the side away from the cavity
        probe = len(path) * 2 // 3
        px, py = path[probe]
        nx, ny = nrm[probe]
        sgn = -1 if point_in_poly(px + nx * 15, py + ny * 15, OUT["cavity"]) else 1
        a = offset(path, 5 * sgn, nrm)
        m = offset(path, 15 * sgn, nrm)
        b = offset(path, 36 * sgn, nrm)
        sc.fill(offset(path, 1 * sgn, nrm) + offset(path, 42 * sgn, nrm)[::-1], "cloak_fill", 0.55)
        # rungs across the band, like a ladder of traces
        for idx in range(3, len(path) - 2, 4):
            if rng.random() < 0.5:
                sc.line([a[idx], b[idx]], "trim", rng.uniform(0.25, 0.45))
        sc.line(a, "trim", 0.10, 10)          # halo round the main rail
        sc.line(a, "trim", 0.62, 3.2)
        sc.line(m, "trim", 0.42, 1.0)
        sc.line(b, "trim", 0.60, 1.8)
        # a broken fourth rail inside the band
        q = offset(path, 26 * sgn, nrm)
        k = 2
        while k < len(q) - 4:
            run = rng.randint(3, 8)
            sc.line(q[k:k + run], "trim", rng.uniform(0.35, 0.60), 1.2)
            k += run + rng.randint(2, 6)

        # circuit traces stepping outward off the outer rail
        for idx in range(5, len(b) - 3, 6):
            if rng.random() < 0.40:
                continue
            x, y = b[idx]
            nx, ny = nrm[idx][0] * sgn, nrm[idx][1] * sgn
            tx, ty = ny, -nx
            out = rng.uniform(10, 34)
            run = rng.uniform(14, 52) * rng.choice((-1, 1))
            p1 = (x + nx * out, y + ny * out)
            p2 = (p1[0] + tx * run, p1[1] + ty * run)
            sc.line([(x, y), p1, p2], "trim", rng.uniform(0.40, 0.62))
            if rng.random() < 0.5:
                sc.patch([(p2[0] - 5, p2[1] - 3), (p2[0] + 5, p2[1] - 3),
                          (p2[0] + 5, p2[1] + 3), (p2[0] - 5, p2[1] + 3)], "trim", 0.70)
            else:
                sc.dot(*p2, 3.2, "trim", 0.90)

    # broken gold along the hood's outer edge, upper half
    for side, inward in ((OUT["hood_l"], 1), (OUT["hood_r"], -1)):
        seg = [(x + 14 * inward, y) for x, y in side[20:70]]
        for k in range(0, len(seg) - 6, 9):
            sc.line(seg[k:k + 6], "trim", rng.uniform(0.40, 0.60), 1.3)
            sc.dot(*seg[k], 2.6, "trim", 0.9)

    # gold down the robe: long runs following the cloak's X, each break
    # capped with a pad, like a trace leaving the board
    for sign in (-1, 1):
        def sx(x):
            return CX + sign * (x - CX)
        for p0, p3 in (((300, 1150), (900, 2010)), ((420, 1240), (760, 2010)),
                       ((140, 1180), (420, 2010))):
            curve = bezier((sx(p0[0]), p0[1]), (sx(p0[0] + 140), p0[1] + 220),
                           (sx(p3[0] - 120), 1650), (sx(p3[0]), p3[1]), 48)
            k = rng.randint(0, 6)
            while k < len(curve) - 2:
                run = rng.randint(8, 18)
                piece = curve[k:k + run]
                if len(piece) > 1:
                    sc.line(piece, "trim", rng.uniform(0.50, 0.75), 1.6)
                    sc.dot(*piece[-1], 3.2, "trim", 0.90)
                k += run + rng.randint(2, 5)

    # the crossing, where the two trims meet
    sc.dot(*V_BOTTOM, 10, "trim", 0.35)
    sc.dot(*V_BOTTOM, 3.8, "trim", 1.05)


def _particles(sc, rng, behind):
    """Dust: gathered round the figure, thinning out across the stage."""
    sx0, sy0, sx1, sy1 = sc.view.stage
    n = int((320 if behind else 170) * max(1.0, (sx1 - sx0) / REF_W) ** 0.8)
    face = OUT["face"]
    for _ in range(n):
        if rng.random() < 0.6:
            x = rng.gauss(CX, 430)
            y = rng.gauss(900, 560)
        else:
            x, y = rng.uniform(sx0, sx1), rng.uniform(sy0, sy1)
        if not (sx0 + 10 < x < sx1 - 10 and sy0 + 10 < y < sy1 - 10):
            continue
        if not behind and point_in_poly(x, y, face):
            continue
        roll = rng.random()
        role = ("mesh_hot" if roll < 0.55 else "trim" if roll < 0.82
                else "text" if roll < 0.92 else "glyph")
        if rng.random() < 0.06:
            sc.dot(x, y, rng.uniform(5, 9), role, rng.uniform(0.10, 0.22))
        else:
            near = math.exp(-(((x - CX) / 520) ** 2 + ((y - 820) / 620) ** 2))
            sc.dot(x, y, rng.uniform(1.0, 2.8), role, rng.uniform(0.30, 0.90) + 0.45 * near)


# --------------------------------------------------------------------------
# HUD: frame, ticks, side readouts, footer — on the stage's edges
# --------------------------------------------------------------------------

def _hud(sc, rng):
    h = "hud"
    X0, Y0, X1, Y1 = sc.view.stage
    m = 22
    sc.line([(X0 + m, Y0 + m), (X1 - m, Y0 + m), (X1 - m, Y1 - m),
             (X0 + m, Y1 - m), (X0 + m, Y0 + m)], h, 0.30)
    L = 74
    for cx, cy, dx, dy in ((X0 + m, Y0 + m, 1, 1), (X1 - m, Y0 + m, -1, 1),
                           (X0 + m, Y1 - m, 1, -1), (X1 - m, Y1 - m, -1, -1)):
        sc.line([(cx, cy + dy * L), (cx, cy), (cx + dx * L, cy)], h, 0.75, 2.5)
        ix, iy = cx + dx * 26, cy + dy * 26
        sc.line([(ix, iy + dy * 40), (ix, iy), (ix + dx * 40, iy)], h, 0.40)
    # ruler along the top
    x = X0 + 130
    k = 0
    while x < X1 - 130:
        tick = 10 if k % 5 == 0 else 5
        sc.line([(x, Y0 + 34), (x, Y0 + 34 + tick)], h, 0.32)
        x += 13
        k += 1
    sc.line([(X0 + 130, Y0 + 34), (X1 - 130, Y0 + 34)], h, 0.22)
    # side bars and readouts
    for side_x, d in ((X0 + 28, 1), (X1 - 28, -1)):
        y = Y0 + 120
        while y < Y1 - 160:
            hh = rng.uniform(6, 40)
            if rng.random() < 0.55:
                sc.patch([(side_x, y), (side_x + d * rng.uniform(4, 12), y),
                          (side_x + d * rng.uniform(4, 12), y + hh), (side_x, y + hh)],
                         h, rng.uniform(0.18, 0.40))
            y += hh + rng.uniform(10, 60)
    lh = max(12, 11 / sc.view.scale) * 1.45          # line height, reference units
    for i, s in enumerate(("0xB3C6", "8E01", "Au", "0x4A3E")):
        sc.text(X0 + 56, 1046 + i * lh, s, h, 0.55, 12, anchor="w", min_px=11)
    for i, s in enumerate(("0xE436", "CBF936", "A0B2", "ΔA7F2")):
        sc.text(X1 - 56, 1046 + i * lh, s, h, 0.55, 12, anchor="e", min_px=11)
    for i, s in enumerate(("// 0x39", "37 66", "SIG 7")):
        sc.text(X1 - 56, 220 + i * lh, s, h, 0.45, 11, anchor="e", min_px=10)
    # footer, on a band of background so the strands run under it, not
    # through the text
    sc.fill([(X0 + m + 2, 1834), (X1 - m - 2, 1834), (X1 - m - 2, Y1 - m - 2),
             (X0 + m + 2, Y1 - m - 2)], h, 0.0)
    sc.line([(X0 + 100, 1842), (X1 - 100, 1842)], h, 0.22)
    sc.text(X0 + 106, 1866, "Felhaven OS :: PYTHIA v3.14 :: ORACLE/AI //", "text", 0.85, 15,
            anchor="w", min_px=14)
    sc.text(X1 - 106, 1866, "// NODE: ORACLE_CORE // STATUS: ACTIVE // SIGNAL: 0xA7F2 //",
            "text", 0.85, 15, anchor="e", min_px=14)
    sc.text(CX, 1902, "PYTHIA — ORACLE AI • FELHAVEN OPERATING ENVIRONMENT "
            "• WIRE FRAME CONSTRUCT", "trim", 1.0, 18, min_px=16)


# --------------------------------------------------------------------------
# full screen
# --------------------------------------------------------------------------

def show(colourway="reference", seed=7):
    """Render at the screen's own resolution and show it full screen."""
    import tkinter as tk
    import pythia_v2_raster as raster

    if sys.platform == "win32":
        # Without this, Windows reports a scaled-down screen on a high-DPI
        # display and stretches the frame back up — a soft, blurred image.
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass

    roles = COLOURWAYS[colourway]()
    root = tk.Tk()
    root.title("Pythia")
    root.configure(bg=roles["bg"], cursor="none")
    root.attributes("-fullscreen", True)
    w, h = root.winfo_screenwidth(), root.winfo_screenheight()
    label = tk.Label(root, bd=0, highlightthickness=0, bg=roles["bg"])
    label.pack(fill="both", expand=True)
    root.bind("<Escape>", lambda _e: root.destroy())
    root.bind("q", lambda _e: root.destroy())
    root.update()

    img = raster.render(build_scene(screen_view(w, h), colourway, seed))
    label.image = raster.photo(img)
    label.configure(image=label.image)
    root.mainloop()


if __name__ == "__main__":
    show(sys.argv[1] if len(sys.argv) > 1 else "reference",
         int(sys.argv[2]) if len(sys.argv) > 2 else 7)
