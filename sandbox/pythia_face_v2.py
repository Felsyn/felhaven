"""
sandbox/pythia_face_v2.py — Pythia v2: the hooded wire-frame construct
======================================================================
SANDBOX. Not part of the app: nothing in metis_toolbox/ imports this, and it
sits outside the app root, so neither the suite nor the palette guard ever
reads it. It moves into panels/ (as v1 did on 2026-09-01) once the look is
settled. Until then it is free to break every app rule it needs to.

Job:         Build Pythia v2 as one STATIC frame and paint it on a tk.Canvas.
             A hooded figure drawn entirely as wire mesh: a faceted hood
             converging on a pointed apex, a dark triangulated face mask lit
             by two glowing eyes, a gold circuit trim along the hood's opening
             that crosses at the chest, and cloak strands sweeping down.
             Optional poster dressing behind and around it: alchemical glyphs,
             faint schematic traces, a HUD frame with its footer text.

             Static first, on purpose. No animation loop yet: get the still
             right, then decide what moves.

Contract:    build_scene(view, colourway="reference", seed=7, layers=...)
                 -> Scene. PURE: no tk anywhere in it. A display list of
                 primitives in REFERENCE coordinates (the 1312x1936 poster the
                 look was drawn from) plus the View that maps them to pixels.
                 The same seed draws the same figure.
             draw(canvas, scene) -> int   paints it, returns the item count
             poster_view(w, h) / face_view(w, h) -> View
             COLOURWAYS — "reference" (the poster's blue, gold and crimson)
                          and "phosphor" (Felhaven's own palette.C)

Layers:      "figure"     cloak, hood, cavity, face, eyes, trim
             "particles"  the dust around the figure
             "glyphs"     alchemical marks and schematic traces, behind
             "hud"        frame, ticks and the footer text, in front
             face_view() is meant for the figure alone: at the 190x160 the
             Hestia header mounts v1 at, everything else is noise.

Colour:      Tk has no per-item alpha, so every glow is a colour blended
             toward the background (v1's fake-alpha trick, generalised): a
             primitive carries a ROLE and an INTENSITY, and colour() mixes
             bg -> role colour over 0..1, then role -> burnout above 1, like
             an overdriven phosphor. Lines drawn over a glow therefore have to
             be brighter than it, never dimmer, or they read as dark scratches
             — which is why the face mesh is lit by the eyes (see _face).

Density:     Mesh spacing is set in OUTPUT pixels and converted to reference
             units through the view, so a small canvas gets fewer, larger
             facets rather than the poster's mesh shrunk to mush.

Tk limits:   Lines are aliased (Tk on X11 and on Windows GDI both), and
             overlaps occlude rather than add. The render is an honest preview
             of what the shipped widget can draw — no Pillow, no supersampling.

Requires:    math, random, tkinter (stdlib). palette.py from metis_toolbox/
             for the "phosphor" colourway only.

Demo:        python sandbox/pythia_face_v2.py [poster|face] [reference|phosphor]
             Opens one window. q or Escape closes it.
Stills:      sandbox/render_still.py writes the PNGs in sandbox/renders/.
"""

import math
import os
import random
import sys
from dataclasses import dataclass, field

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
# Shoulder, from under the hem out past the left edge, then the off-canvas
# tail, so the cloak's mesh runs off the edges instead of being hemmed there.
SHOULDER_L = [(276, 1004), (214, 1030), (160, 1066), (104, 1104), (58, 1128),
              (14, 1162), (-90, 1230)]
CLOAK_TAIL_L = [(-90, 2050), (CX, 2050)]

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

# Landmarks on the left half of the face. The mesh is triangulated on this
# half and mirrored, so the face is exactly symmetric and the hood is not.
FACE_MARKS_L = [
    # eye ring, outer corner round to the inner corner and back
    (502, 600), (522, 587), (550, 581), (578, 589), (601, 606),
    (576, 622), (548, 629), (520, 619),
    # brow
    (488, 556), (528, 541), (574, 544), (616, 566),
    # forehead
    (548, 476), (604, 462), (577, 512), (512, 508), (630, 520),
    # nose flank and nostril
    (628, 642), (622, 702), (612, 752), (630, 781), (645, 797),
    # cheekbone and cheek
    (494, 676), (536, 684), (584, 694), (506, 744), (558, 762),
    (522, 810), (577, 818),
    # mouth
    (598, 848), (628, 836), (628, 869),
    # jaw
    (562, 862), (602, 896), (628, 924),
]
FACE_AXIS_Y = [440, 500, 560, 610, 665, 720, 770, 800, 834, 852, 872, 915]


# --------------------------------------------------------------------------
# colourways
# --------------------------------------------------------------------------

# Prototype literals. The app forbids a colour outside palette.py (CONVENTIONS
# §7, tests/test_palette_guard.py); this file is outside the app, and these
# are exactly the values a promotion would have to argue into palette.py — or
# drop in favour of the phosphor mapping below.
# Sampled off the reference poster, region by region.
REFERENCE = {
    "bg":          "#05080F",
    "cloak_fill":  "#0A1A33",
    "cavity_fill": "#01040A",
    "mesh":        "#2F8FE0",
    "mesh_hot":    "#9EE6FF",
    "eye":         "#2E9BFF",
    "trim":        "#F0B45A",
    "glyph":       "#B04652",
    "ash":         "#8E9098",
    "hud":         "#B88A3C",
    "text":        "#D8CDB0",
    "burnout":     "#FFFFFF",
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
        "burnout":     C["burnout"],
    }


COLOURWAYS = {"reference": lambda: REFERENCE, "phosphor": _phosphor}


def _rgb(h):
    return (int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16))


def _mix(c1, c2, t):
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(c1, c2))


class Palette:
    """role + intensity -> '#rrggbb'. 0 is the background, 1 the role's own
    colour, and up to ~1.4 a burn toward the burnout white. Cached: a poster
    asks for a few thousand colours drawn from a few hundred distinct ones."""

    def __init__(self, roles):
        self.roles = {k: _rgb(v) for k, v in roles.items()}
        self.bg = roles["bg"]
        self._cache = {}

    def __call__(self, role, i):
        q = round(max(0.0, min(i, 1.45)), 3)
        key = (role, q)
        c = self._cache.get(key)
        if c is None:
            base = self.roles[role]
            if q <= 1.0:
                rgb = _mix(self.roles["bg"], base, q)
            else:
                rgb = _mix(base, self.roles["burnout"], min(1.0, (q - 1.0) / 0.45) * 0.75)
            c = "#%02x%02x%02x" % rgb
            self._cache[key] = c
        return c


# --------------------------------------------------------------------------
# geometry — pure functions, no tk
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


def dist_to_path(px, py, path):
    return min(dist_to_seg(px, py, *path[i], *path[i + 1])
               for i in range(len(path) - 1))


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
    """Reference coords -> canvas pixels: X = (x - x0) * scale."""
    w: int
    h: int
    x0: float
    y0: float
    scale: float

    def px(self, x, y):
        return ((x - self.x0) * self.scale, (y - self.y0) * self.scale)


def _cover(w, h, rx0, ry0, rx1, ry1):
    """Fill a w x h canvas with the reference rect, cropping the overflow."""
    scale = max(w / (rx1 - rx0), h / (ry1 - ry0))
    cx, cy = (rx0 + rx1) / 2, (ry0 + ry1) / 2
    return View(w, h, cx - w / scale / 2, cy - h / scale / 2, scale)


def poster_view(w=1312, h=1936):
    return _cover(w, h, 0, 0, REF_W, REF_H)


def face_view(w=190, h=160):
    """The hood and face, framed for the Hestia header's 190x160 slot: the
    apex just inside the top edge, the crossed lapels at the bottom."""
    return _cover(w, h, CX - 560, 40, CX + 560, 1200)


@dataclass
class Scene:
    view: View
    pal: Palette
    prims: list = field(default_factory=list)

    # every primitive is a tuple; draw() switches on the first element
    def line(self, pts, role, i, width=1.0, dash=None):
        self.prims.append(("line", pts, role, i, width, dash))

    def poly(self, pts, role, i):
        self.prims.append(("poly", pts, role, i))

    def dot(self, x, y, r, role, i):
        self.prims.append(("dot", x, y, r, role, i))

    def ring(self, x, y, rx, ry, role, i, width=1.0):
        self.prims.append(("ring", x, y, rx, ry, role, i, width))

    def text(self, x, y, s, role, i, size, anchor="center", bold=False):
        self.prims.append(("text", x, y, s, role, i, size, anchor, bold))


ALL_LAYERS = ("glyphs", "particles", "figure", "hud")


def build_scene(view, colourway="reference", seed=7, layers=ALL_LAYERS):
    rng = random.Random(seed)
    sc = Scene(view, Palette(COLOURWAYS[colourway]()))
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


# --------------------------------------------------------------------------
# backdrop: a faint radial lift behind the head, then the schematic
# --------------------------------------------------------------------------

def _backdrop(sc, rng):
    # The barest lift behind the head, so the black is not dead flat.
    for r, i in ((900, 0.10), (680, 0.18), (480, 0.26)):
        sc.prims.append(("oval_fill", CX, 720, r * 0.85, r, "cloak_fill", i))

    # Manhattan traces: the faint red circuitry either side of the hood.
    for zx0, zx1 in ((60, 470), (850, 1260)):
        for _ in range(16):
            x = rng.uniform(zx0, zx1)
            y = rng.uniform(220, 1060)
            pts = [(x, y)]
            horiz = rng.random() < 0.5
            for _seg in range(rng.randint(2, 5)):
                L = rng.uniform(30, 170) * rng.choice((-1, 1))
                x = min(max(x + (L if horiz else 0), zx0), zx1)
                y = min(max(y + (0 if horiz else L), 200), 1080)
                pts.append((x, y))
                horiz = not horiz
            sc.line(pts, "glyph", rng.uniform(0.16, 0.32))
            if rng.random() < 0.5:
                sc.ring(*pts[-1], 4, 4, "glyph", 0.32)
        for _ in range(4):
            x = rng.uniform(zx0, zx1 - 100)
            y = rng.uniform(240, 1000)
            w, h = rng.uniform(50, 120), rng.uniform(30, 90)
            sc.line([(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)],
                    "glyph", rng.uniform(0.14, 0.24))


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
    g = "glyph"
    # Sol — the alchemical mark for gold — top left, "Au" beneath.
    x, y = 270, 268
    sc.ring(x, y, 48, 48, g, 0.75, 2)
    sc.ring(x, y, 30, 30, g, 0.40)
    sc.dot(x, y, 7, g, 0.85)
    sc.line([(x, y - 92), (x, y - 48)], g, 0.45)
    sc.line([(x, y + 48), (x, y + 78)], g, 0.45)
    sc.line([(x - 84, y), (x - 48, y)], g, 0.40)
    sc.line([(x + 48, y), (x + 84, y)], g, 0.40)
    sc.text(x, y + 82, "Au", g, 0.70, 26)
    sc.text(405, 265, "Au", g, 0.45, 18)

    # The element itself, top right, in ash grey, under a small ring.
    sc.text(1012, 246, "Au", "ash", 0.85, 74)
    sc.ring(983, 170, 15, 15, "ash", 0.75, 2)
    sc.line([(983, 185), (983, 214)], "ash", 0.75, 2)
    sc.text(1000, 292, "Au", "ash", 0.65, 22)

    # Luna — a crescent right of the hood, engraved with a second inner line.
    sc.line(_crescent(1104, 402, 74, 66, 30), g, 0.85, 2)
    sc.line(_crescent(1106, 402, 64, 60, 30), g, 0.35)

    # Moon within the circle, mid left, with its small finial.
    x, y = 125, 868
    sc.ring(x, y, 62, 62, g, 0.60, 2)
    sc.ring(x, y, 54, 54, g, 0.25)
    sc.line(_crescent(x + 6, y, 40, 36, 18), g, 0.60)
    sc.ring(x, y - 82, 9, 9, g, 0.55)
    sc.line([(x, y - 73), (x, y - 62)], g, 0.5)
    for a in (200, 340):
        ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
        sc.line([(x + 62 * ca, y + 62 * sa), (x + 92 * ca, y + 92 * sa)], g, 0.4)

    # Lattice — a square of diagonals and a rotated square, mid right.
    x, y, s = 1190, 858, 80
    sq = [(x - s, y - s), (x + s, y - s), (x + s, y + s), (x - s, y + s), (x - s, y - s)]
    sc.line(sq, g, 0.65, 2)
    sc.line([(x - s, y - s), (x + s, y + s)], g, 0.45)
    sc.line([(x + s, y - s), (x - s, y + s)], g, 0.45)
    sc.line([(x, y - s), (x + s, y), (x, y + s), (x - s, y), (x, y - s)], g, 0.45)
    for k in (-0.5, 0.5):
        sc.line([(x + k * s, y - s), (x + k * s, y + s)], g, 0.25)
        sc.line([(x - s, y + k * s), (x + s, y + k * s)], g, 0.25)
    sc.line([(x, y - s - 40), (x, y - s)], g, 0.35)
    sc.line([(x - s - 30, y), (x - s, y)], g, 0.35)

    sc.text(46, 1100, "Au", "ash", 0.55, 16)


# --------------------------------------------------------------------------
# the figure
# --------------------------------------------------------------------------

def _cloak(sc, rng, per_px):
    """Hood and shoulders as one triangulated shell around the opening.

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

    sc.poly(cloak, "cloak_fill", 0.30)

    def keep(x, y):
        return (point_in_poly(x, y, cloak) and not point_in_poly(x, y, cavity)
                and dist_to_path(x, y, rim_edge) > base * 0.45)

    seeds = [APEX]
    seeds += resample(hood_edge, base * 0.9)
    for sh in shoulders:
        seeds += resample(sh[:-1], base * 1.3)[1:]
    seeds += resample(rim_edge, base * 0.8)
    seeds = [p for p in seeds if -100 < p[0] < REF_W + 100]
    area = REF_W * REF_H * 0.65
    fill = scatter(rng, (-90, 60, REF_W + 90, 2050), spacing_at, keep,
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
        """(glow, body): rim light off the hood's silhouette and opening, and
        how much of the hood's brightness this point gets — full in the hood,
        falling away down the cloak."""
        d_out = dist_to_path(x, y, hood_edge)
        d_in = dist_to_path(x, y, rim_edge)
        glow = 0.45 * max(0.0, 1 - d_out / (base * 2.0)) + 0.35 * max(0.0, 1 - d_in / (base * 1.8))
        in_hood = y < 1010 and abs(x - CX) < 430
        body = 1.0 if in_hood else 0.90 - 0.45 * smoothstep(1150, 1900, y)
        return glow, body

    for a, b in edges:
        (ax, ay), (bx, by) = pts[a], pts[b]
        glow, body = lit((ax + bx) / 2, (ay + by) / 2)
        i = rng.uniform(0.55, 0.95) * body + glow
        if rng.random() < 0.5:
            sc.line([(ax, ay), (bx, by)], "mesh", 0.22 * body + 0.1 * glow, 4)
        sc.line([(ax, ay), (bx, by)], "mesh", i)

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
    sc.line([APEX, (656, 392)], "mesh_hot", 0.30, 6)       # ridge bloom
    sc.line([APEX, (656, 392)], "mesh_hot", 1.05, 1.5)     # the ridge itself

    # Silhouette: a bloom, then the hot edge. Shoulders a step dimmer.
    sc.line(hood_edge, "mesh", 0.38, 7)
    sc.line(hood_edge, "mesh_hot", 0.95, 1.5)
    for sh in shoulders:
        sc.line(sh, "mesh", 0.30, 6)
        sc.line(sh, "mesh_hot", 0.70, 1.2)
    for sign in (-1, 1):
        hem = [(CX + sign * (CX - x), y) for x, y in
               bezier((276, 1004), (330, 1080), (420, 1150), (560, 1214), 20)]
        sc.line(hem, "mesh", 0.30, 6)
        sc.line(hem, "mesh_hot", 0.80, 1.4)

    # Nodes at the vertices; a few flare.
    for x, y in pts:
        if not (0 <= x <= REF_W and 0 <= y <= REF_H):
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
    sweep down, cross the chest, and run off the bottom edge. They, not the
    mesh, are what make the cloak read as cloth."""
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
                    sc.line(pts, "trim", rng.uniform(0.55, 0.90))
                elif roll < 0.32:
                    sc.line(pts, "mesh_hot", rng.uniform(0.55, 0.90))
                else:
                    if rng.random() < 0.4:
                        sc.line(pts, "mesh", 0.22, 4)
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


def _cavity(sc, rng, per_px):
    """The dark inside of the hood: near-black, a dim web from rim to mask."""
    cavity, face = OUT["cavity"], OUT["face"]
    sc.poly(cavity, "cavity_fill", 1.0)
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


def _face(sc, rng, per_px):
    """The mask: triangulated on the left half, mirrored for exact symmetry,
    and lit by the eyes. The face has no outline of its own — it is told
    from the cavity by the density and brightness of its mesh, as in the
    reference — and each eye burns inside a dark almond socket."""
    face_l = OUT["face_l"]
    half = face_l + [(CX, face_l[-1][1]), (CX, face_l[0][1])]

    base = max(per_px * 10.0, 34.0)
    if per_px > 3:
        # At widget size the landmarks alone are the mesh; extras are mush.
        marks = FACE_MARKS_L[::2]
        axis = FACE_AXIS_Y[::2]
    else:
        marks = list(FACE_MARKS_L)
        axis = list(FACE_AXIS_Y)
    seeds = [(float(x), float(y)) for x, y in marks]
    seeds += [(CX, float(y)) for y in axis]
    rim = resample(face_l, base * 1.1)
    seeds += rim
    fill = scatter(rng, (440, 400, CX - 6, 970), lambda x, y: base * 1.3,
                   lambda x, y: point_in_poly(x, y, half) and x < CX - 10,
                   cell=base, tries=600, seeds=seeds)
    pts = seeds + fill

    def keep_tri(a, b, c):
        mx, my = (a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3
        return point_in_poly(mx, my, half)

    # The socket is the eye ring's own landmarks: drop every edge that would
    # cross the eye, and the dark almond falls out of the mesh by itself.
    ring = [(float(x), float(y)) for x, y in FACE_MARKS_L[:8]]
    edges = [(a, b) for a, b in tri_edges(pts, delaunay(pts), keep_tri)
             if not point_in_poly((pts[a][0] + pts[b][0]) / 2,
                                  (pts[a][1] + pts[b][1]) / 2, ring)]

    def eyelight(x, y):
        d = min(math.dist((x, y), EYE_L), math.dist((x, y), EYE_R))
        return 0.55 * math.exp(-(d / 115.0) ** 2)

    # a wide, faint halo under the mesh — the mesh sits on top, brighter
    for r, i in ((72, 0.05), (52, 0.09), (36, 0.14)):
        for ex, ey in (EYE_L, EYE_R):
            sc.prims.append(("oval_fill", ex, ey, r * 1.3, r, "eye", i))

    drawn = []
    for a, b in edges:
        pa, pb = pts[a], pts[b]
        mx, my = (pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2
        on_axis = pa[0] == CX and pb[0] == CX
        # dimmer up the forehead, where the brim shades it; dimmer at the
        # very edge, so the mask fades into the cavity instead of being cut
        shade = (0.62 + 0.38 * smoothstep(430, 560, my))
        edge = min(1.0, dist_to_path(mx, my, face_l) / 40.0)
        i = rng.uniform(0.55, 0.82) * shade * (0.65 + 0.35 * edge) + eyelight(mx, my)
        if on_axis:
            i += 0.15
        drawn.append((pa, pb, i))
        drawn.append(((2 * CX - pa[0], pa[1]), (2 * CX - pb[0], pb[1]), i))
    for pa, pb, i in drawn:
        sc.line([pa, pb], "mesh_hot", i)

    for x, y in pts:
        for px in ((x, y), (2 * CX - x, y)):
            hot = rng.random() < 0.18
            j = 0.62 + eyelight(*px) + (0.55 if hot else 0.0)
            sc.dot(*px, 2.6 if hot else 1.9, "mesh_hot", j)

    # The eyes, burning inside their sockets. They are the whole signal at
    # header size, so below ~2.5 reference px per pixel they stop shrinking
    # with the rest of the face: the core never drops under a pixel.
    g = max(1.0, per_px / 2.5)
    for ex, ey in (EYE_L, EYE_R):
        for r, i in ((20, 0.28), (15, 0.45), (11, 0.70), (8, 0.98), (5.2, 1.25), (3, 1.45)):
            r = min(r * g, 44)
            sc.prims.append(("oval_fill", ex, ey, r * 1.35, r, "eye", i))
        sc.line([(ex - 54, ey), (ex + 54, ey)], "eye", 0.32)
        sc.line([(ex - 22 * g, ey), (ex + 22 * g, ey)], "eye", 0.85)
        sc.line([(ex, ey - 20), (ex, ey + 18)], "eye", 0.50)


def _trim(sc, rng):
    """Gold circuit band along the hood's opening: two rails with rungs and
    traces between and off them, crossing at the chest into the lapels."""
    il = OUT["inner_l"]
    lapel = [V_BOTTOM, (742, 1268), (812, 1330), (868, 1396)]
    # left trim: lapel end (lower right) -> the V -> up the left rim -> brim
    left = lapel[::-1][:-1] + il[::-1]
    paths = [left, mirror_x(left)]

    for path in paths:
        nrm = normals(path)
        # choose the side away from the cavity
        probe = len(path) * 2 // 3
        px, py = path[probe]
        nx, ny = nrm[probe]
        sgn = -1 if point_in_poly(px + nx * 15, py + ny * 15, OUT["cavity"]) else 1
        a = offset(path, 5 * sgn, nrm)
        m = offset(path, 15 * sgn, nrm)
        b = offset(path, 36 * sgn, nrm)
        sc.line(a, "trim", 0.36, 9)
        sc.line(b, "trim", 0.22, 6)
        # rungs across the band, like a ladder of traces
        for idx in range(3, len(path) - 2, 4):
            if rng.random() < 0.5:
                sc.line([a[idx], b[idx]], "trim", rng.uniform(0.40, 0.70))
        sc.line(a, "trim", 1.20, 2.6)
        sc.line(m, "trim", 0.70, 1.0)
        sc.line(b, "trim", 0.95, 1.6)
        # a broken fourth rail inside the band
        q = offset(path, 26 * sgn, nrm)
        k = 2
        while k < len(q) - 4:
            run = rng.randint(3, 8)
            sc.line(q[k:k + run], "trim", rng.uniform(0.55, 0.95), 1.2)
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
            sc.line([(x, y), p1, p2], "trim", rng.uniform(0.55, 0.90))
            if rng.random() < 0.5:
                sc.poly([(p2[0] - 5, p2[1] - 3), (p2[0] + 5, p2[1] - 3),
                         (p2[0] + 5, p2[1] + 3), (p2[0] - 5, p2[1] + 3)], "trim", 1.0)
            else:
                sc.dot(*p2, 3.2, "trim", 1.15)

    # broken gold along the hood's outer edge, upper half
    for side, inward in ((OUT["hood_l"], 1), (OUT["hood_r"], -1)):
        seg = [(x + 14 * inward, y) for x, y in side[20:70]]
        for k in range(0, len(seg) - 6, 9):
            sc.line(seg[k:k + 6], "trim", rng.uniform(0.55, 0.85), 1.3)
            sc.dot(*seg[k], 2.6, "trim", 1.1)

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
                    sc.line(piece, "trim", 0.26, 6)
                    sc.line(piece, "trim", rng.uniform(0.75, 1.10), 1.6)
                    sc.dot(*piece[-1], 3.2, "trim", 1.15)
                k += run + rng.randint(2, 5)

    # the crossing, where the two trims meet
    sc.dot(*V_BOTTOM, 10, "trim", 0.35)
    sc.dot(*V_BOTTOM, 3.8, "trim", 1.35)


def _particles(sc, rng, behind):
    n = 320 if behind else 170
    face = OUT["face"]
    for _ in range(n):
        if rng.random() < 0.6:
            x = rng.gauss(CX, 430)
            y = rng.gauss(900, 560)
        else:
            x, y = rng.uniform(0, REF_W), rng.uniform(0, REF_H)
        if not (10 < x < REF_W - 10 and 10 < y < REF_H - 10):
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
# HUD: frame, ticks, side readouts, footer
# --------------------------------------------------------------------------

def _hud(sc, rng):
    h = "hud"
    m = 22
    W, H = REF_W, REF_H
    sc.line([(m, m), (W - m, m), (W - m, H - m), (m, H - m), (m, m)], h, 0.30)
    L = 74
    for cx, cy, dx, dy in ((m, m, 1, 1), (W - m, m, -1, 1), (m, H - m, 1, -1), (W - m, H - m, -1, -1)):
        sc.line([(cx, cy + dy * L), (cx, cy), (cx + dx * L, cy)], h, 0.75, 2.5)
        ix, iy = cx + dx * 26, cy + dy * 26
        sc.line([(ix, iy + dy * 40), (ix, iy), (ix + dx * 40, iy)], h, 0.40)
    # ruler along the top
    x = 130
    k = 0
    while x < W - 130:
        tick = 10 if k % 5 == 0 else 5
        sc.line([(x, 34), (x, 34 + tick)], h, 0.32)
        x += 13
        k += 1
    sc.line([(130, 34), (W - 130, 34)], h, 0.22)
    # side bars and readouts
    for side_x, d in ((28, 1), (W - 28, -1)):
        y = 120
        while y < H - 160:
            hh = rng.uniform(6, 40)
            if rng.random() < 0.55:
                sc.poly([(side_x, y), (side_x + d * rng.uniform(4, 12), y),
                         (side_x + d * rng.uniform(4, 12), y + hh), (side_x, y + hh)],
                        h, rng.uniform(0.18, 0.40))
            y += hh + rng.uniform(10, 60)
    for i, s in enumerate(("0xB3C6", "8E01", "Au", "0x4A3E")):
        sc.text(50, 1046 + i * 18, s, h, 0.45, 12, anchor="w")
    for i, s in enumerate(("0xE436", "CBF936", "A0B2", "ΔA7F2")):
        sc.text(W - 50, 1046 + i * 18, s, h, 0.45, 12, anchor="e")
    for i, s in enumerate(("// 0x39", "37 66", "SIG 7")):
        sc.text(W - 50, 220 + i * 18, s, h, 0.35, 11, anchor="e")
    # footer, on a band of background so the strands run under it, not
    # through the text
    sc.poly([(m + 2, 1838), (W - m - 2, 1838), (W - m - 2, H - m - 2), (m + 2, H - m - 2)], h, 0.0)
    sc.line([(100, 1842), (W - 100, 1842)], h, 0.22)
    sc.text(106, 1866, "Felhaven OS :: PYTHIA v3.14 :: ORACLE/AI //", "text", 0.85, 15, anchor="w")
    sc.text(W - 106, 1866, "// NODE: ORACLE_CORE // STATUS: ACTIVE // SIGNAL: 0xA7F2 //",
            "text", 0.85, 15, anchor="e")
    sc.text(CX, 1900, "PYTHIA — ORACLE AI • FELHAVEN OPERATING ENVIRONMENT "
            "• WIRE FRAME CONSTRUCT", "trim", 1.0, 18, bold=True)


# --------------------------------------------------------------------------
# tk backend — the only part that touches tkinter
# --------------------------------------------------------------------------

_MONO = ("Consolas", "DejaVu Sans Mono", "Courier New", "Courier")
_SANS = ("Georgia", "DejaVu Serif", "Times New Roman")


def _family(canvas, wanted):
    import tkinter.font as tkfont
    have = set(tkfont.families(canvas))
    return next((f for f in wanted if f in have), "TkFixedFont")


def draw(canvas, scene):
    """Paint the scene. Returns the number of canvas items created."""
    v, pal = scene.view, scene.pal
    canvas.configure(bg=pal.bg)
    mono = _family(canvas, _MONO)
    serif = _family(canvas, _SANS)
    s = v.scale
    n = 0

    def flat(pts):
        out = []
        for x, y in pts:
            out += v.px(x, y)
        return out

    for p in scene.prims:
        kind = p[0]
        if kind == "line":
            _, pts, role, i, width, dash = p
            if i <= 0.004:
                continue
            canvas.create_line(*flat(pts), fill=pal(role, i),
                               width=max(1.0, round(width * s * 2) / 2),
                               dash=dash, capstyle="round", joinstyle="round")
        elif kind == "poly":
            _, pts, role, i = p
            canvas.create_polygon(*flat(pts), fill=pal(role, i), outline="")
        elif kind in ("dot", "oval_fill"):
            if kind == "dot":
                _, x, y, r, role, i = p
                rx = ry = r
            else:
                _, x, y, rx, ry, role, i = p
            X, Y = v.px(x, y)
            RX, RY = rx * s, ry * s
            if RX < 0.75:
                if i * min(1.0, RX / 0.75) < 0.05:
                    continue
                canvas.create_rectangle(X, Y, X + 1, Y + 1, outline="",
                                        fill=pal(role, i * min(1.0, 0.4 + RX)))
            else:
                canvas.create_oval(X - RX, Y - RY, X + RX, Y + RY,
                                   fill=pal(role, i), outline="")
        elif kind == "ring":
            _, x, y, rx, ry, role, i, width = p
            X, Y = v.px(x, y)
            canvas.create_oval(X - rx * s, Y - ry * s, X + rx * s, Y + ry * s,
                               outline=pal(role, i), width=max(1.0, width * s))
        elif kind == "text":
            _, x, y, txt, role, i, size, anchor, bold = p
            px = int(round(size * s))
            if px < 6:
                continue
            fam = serif if size >= 60 else mono
            canvas.create_text(*v.px(x, y), text=txt, anchor=anchor,
                               fill=pal(role, i),
                               font=(fam, -px, "bold" if bold else "normal"))
        else:
            raise ValueError(kind)
        n += 1
    return n


# --------------------------------------------------------------------------
# demo
# --------------------------------------------------------------------------

def _demo(argv):
    import tkinter as tk
    framing = argv[1] if len(argv) > 1 else "poster"
    colourway = argv[2] if len(argv) > 2 else "reference"
    root = tk.Tk()
    root.title("Pythia v2 — %s / %s" % (framing, colourway))
    if framing == "face":
        view, layers = face_view(380, 320), ("figure",)
    else:
        h = min(root.winfo_screenheight() - 120, 1936)
        view, layers = poster_view(int(h * REF_W / REF_H), h), ALL_LAYERS
    canvas = tk.Canvas(root, width=view.w, height=view.h, highlightthickness=0, bd=0)
    canvas.pack()
    draw(canvas, build_scene(view, colourway, layers=layers))
    root.bind("<Key>", lambda e: root.destroy() if e.keysym in ("q", "Escape") else None)
    root.mainloop()


if __name__ == "__main__":
    _demo(sys.argv)
