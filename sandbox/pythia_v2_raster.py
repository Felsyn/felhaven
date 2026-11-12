"""
sandbox/pythia_v2_raster.py — Pythia v2's renderer: scene -> pixels
===================================================================
SANDBOX. See sandbox/README.md.

Job:         Turn a Scene (pythia_face_v2.build_scene) into an RGB image, with
             the two things a tk.Canvas cannot do: light that ADDS where lines
             overlap, and anti-aliased edges. Then a real bloom, so bright
             things glow instead of faking it with wide dim underlays.

Contract:    render(scene) -> numpy uint8 array, shape (h, w, 3)
             photo(img)    -> tk.PhotoImage of it (needs a Tk root)
             write_png(img, path)   stdlib zlib, no Pillow

Model:       Two buffers, walked in scene order.
               emit  — emitted light. Lines, dots, glows and patches ADD into
                       it, so a bundle of strands burns brighter than one.
               base  — painted surface: the background, then fills.
             A fill paints `base` and also dims the light already emitted
             under it, which is how the dark cavity sits in front of the hood
             lines behind it. Light emitted after a fill lands on top of it.
             At the end: base + emit + bloom(emit), tone-mapped, dithered.

Units:       Line intensity is light per pixel of length; a dot's or a glow's
             is its peak. Colours are the palette's RGB taken as linear 0..1;
             above 1 a channel rolls off and spills into the others, so an
             overdriven blue burns toward white like a phosphor.

Cost:        Built for one frame per launch at screen size (about 1 to 3 s at
             1920x1080 to 2560x1440). Animation should re-render only the
             regions that move.

Requires:    numpy (already in the app's requirements.txt, for Calliope),
             zlib and struct (stdlib); tkinter for photo() only.
"""

import math
import struct
import zlib

import numpy as np


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _rgb01(hex_colour):
    return np.array([int(hex_colour[i:i + 2], 16) / 255.0 for i in (1, 3, 5)],
                    dtype=np.float32)


def _box_blur(a, r, axis):
    """Box blur of radius r along one axis, edges clamped. O(n) via cumsum."""
    if r < 1:
        return a
    n = a.shape[axis]
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis, dtype=np.float32)
    hi = [slice(None)] * a.ndim
    lo = [slice(None)] * a.ndim
    hi[axis] = slice(2 * r + 1, 2 * r + 1 + n)
    lo[axis] = slice(0, n)
    out = c[tuple(hi)] - c[tuple(lo)]
    out *= 1.0 / (2 * r + 1)
    return out


def _tight(a):
    """A small gaussian (two [1 2 1] passes per axis) by slicing: the full-
    resolution bloom level, where a cumsum would cost the most."""
    for _ in range(2):
        for axis in (0, 1):
            p = np.pad(a, [(1, 1) if k == axis else (0, 0) for k in range(a.ndim)], mode="edge")
            lo = [slice(None)] * a.ndim
            mid = [slice(None)] * a.ndim
            hi = [slice(None)] * a.ndim
            lo[axis], mid[axis], hi[axis] = slice(0, -2), slice(1, -1), slice(2, None)
            a = (p[tuple(lo)] + 2 * p[tuple(mid)] + p[tuple(hi)]) * 0.25
    return a


def _blur(a, r, passes=3):
    for _ in range(passes):
        a = _box_blur(_box_blur(a, r, 0), r, 1)
    return a


def _downsample(a, f):
    h, w = a.shape[0] // f * f, a.shape[1] // f * f
    a = a[:h, :w]
    return a.reshape(h // f, f, w // f, f, -1).mean(axis=(1, 3))


def _resize_axis(a, n, axis):
    """Linear resample along one axis to length n (pixel-centre aligned)."""
    m = a.shape[axis]
    x = (np.arange(n, dtype=np.float32) + 0.5) * (m / n) - 0.5
    x = np.clip(x, 0, m - 1)
    i0 = np.floor(x).astype(np.int64)
    i1 = np.minimum(i0 + 1, m - 1)
    t = (x - i0).astype(np.float32)
    shape = [1] * a.ndim
    shape[axis] = n
    t = t.reshape(shape)
    return np.take(a, i0, axis=axis) * (1 - t) + np.take(a, i1, axis=axis) * t


def _upsample(a, h, w):
    return _resize_axis(_resize_axis(a, h, 0), w, 1)


# --------------------------------------------------------------------------
# the renderer
# --------------------------------------------------------------------------

class _Raster:
    def __init__(self, scene):
        v = scene.view
        self.v = v
        self.w, self.h = v.w, v.h
        self.roles = {k: _rgb01(c) for k, c in scene.roles.items()}
        self.bg = self.roles["bg"]
        self.base = np.empty((self.h, self.w, 3), np.float32)
        self.base[:] = self.bg
        self.emit = np.zeros((self.h * self.w, 3), np.float32)
        self._segs = []        # pending line segments: x0 y0 x1 y1 r g b width
        self._dots = []        # pending small dots: x y sigma r g b

    # -- batching: additive work queues up until a fill needs it on the page

    def flush(self):
        if self._segs:
            self._draw_segments(np.array(self._segs, np.float32))
            self._segs = []
        if self._dots:
            self._draw_dots(np.array(self._dots, np.float32))
            self._dots = []

    def _splat(self, px, py, rgb):
        """Bilinear splat of N samples (px, py) carrying rgb (N, 3) of light."""
        w, h = self.w, self.h
        fx, fy = px - 0.5, py - 0.5
        ix, iy = np.floor(fx).astype(np.int64), np.floor(fy).astype(np.int64)
        ax, ay = (fx - ix).astype(np.float32), (fy - iy).astype(np.float32)
        idx, wts = [], []
        for dx, dy, wt in ((0, 0, (1 - ax) * (1 - ay)), (1, 0, ax * (1 - ay)),
                           (0, 1, (1 - ax) * ay), (1, 1, ax * ay)):
            cx, cy = ix + dx, iy + dy
            ok = (cx >= 0) & (cx < w) & (cy >= 0) & (cy < h)
            idx.append((cy * w + cx)[ok])
            wts.append((rgb[ok] * wt[ok, None]))
        self._accumulate(np.concatenate(idx), np.concatenate(wts))

    def _accumulate(self, idx, rgb):
        """Add rgb (N, 3) of light at flat pixel indices idx (N,): one
        bincount per channel, however many primitives the batch held."""
        n = self.w * self.h
        for c in range(3):
            self.emit[:, c] += np.bincount(idx, weights=rgb[:, c], minlength=n)

    def _draw_segments(self, S):
        x0, y0, x1, y1 = S[:, 0], S[:, 1], S[:, 2], S[:, 3]
        rgb, width = S[:, 4:7], S[:, 7]
        # Widths over 1.5 px become parallel 1 px lines across the normal;
        # thinner ones stay single and emit in proportion to their width.
        k = np.where(width > 1.5, np.ceil(width), 1).astype(np.int64)
        rep = np.repeat(np.arange(len(S)), k)
        j = np.arange(len(rep)) - np.repeat(np.cumsum(k) - k, k)
        kk = k[rep].astype(np.float32)
        off = (j + 0.5 - kk / 2) * (width[rep] / kk)
        dx, dy = x1[rep] - x0[rep], y1[rep] - y0[rep]
        L = np.hypot(dx, dy)
        nx, ny = -dy / np.maximum(L, 1e-6), dx / np.maximum(L, 1e-6)
        sx0, sy0 = x0[rep] + nx * off, y0[rep] + ny * off
        energy = np.where(k[rep] > 1, 1.0, np.minimum(width[rep], 1.5)).astype(np.float32)
        # Sample every half pixel; each sample carries its share of the length.
        n = np.maximum(1, np.ceil(L / 0.5)).astype(np.int64)
        seg = np.repeat(np.arange(len(rep)), n)
        t = (np.arange(len(seg)) - np.repeat(np.cumsum(n) - n, n) + 0.5) / n[seg]
        px = sx0[seg] + dx[seg] * t
        py = sy0[seg] + dy[seg] * t
        light = rgb[rep][seg] * (L[seg] / n[seg] * energy[seg])[:, None]
        # Clip to a margin round the canvas before the splat does the rest.
        ok = (px > -2) & (px < self.w + 2) & (py > -2) & (py < self.h + 2)
        self._splat(px[ok], py[ok], light[ok])

    def _draw_dots(self, D):
        x, y, sig, rgb = D[:, 0], D[:, 1], D[:, 2], D[:, 3:6]
        rad = np.ceil(sig * 3).astype(np.int64)
        all_idx, all_rgb = [], []
        for r in np.unique(rad):
            m = rad == r
            o = np.arange(-r, r + 1)
            ox, oy = np.meshgrid(o, o)
            ox, oy = ox.ravel(), oy.ravel()
            cx = np.floor(x[m])[:, None].astype(np.int64) + ox[None, :]
            cy = np.floor(y[m])[:, None].astype(np.int64) + oy[None, :]
            d2 = (cx + 0.5 - x[m][:, None]) ** 2 + (cy + 0.5 - y[m][:, None]) ** 2
            g = np.exp(-d2 / (2 * sig[m][:, None] ** 2)).astype(np.float32)
            ok = (cx >= 0) & (cx < self.w) & (cy >= 0) & (cy < self.h)
            all_idx.append((cy * self.w + cx)[ok])
            all_rgb.append((g[:, :, None] * rgb[m][:, None, :])[ok])
        self._accumulate(np.concatenate(all_idx), np.concatenate(all_rgb))

    def glow(self, x, y, sx, sy, rgb):
        """A wide elliptical gaussian, added straight into its own box."""
        x0, x1 = max(0, int(x - 3 * sx)), min(self.w, int(x + 3 * sx) + 1)
        y0, y1 = max(0, int(y - 3 * sy)), min(self.h, int(y + 3 * sy) + 1)
        if x0 >= x1 or y0 >= y1:
            return
        gx = np.exp(-((np.arange(x0, x1) + 0.5 - x) ** 2) / (2 * sx * sx)).astype(np.float32)
        gy = np.exp(-((np.arange(y0, y1) + 0.5 - y) ** 2) / (2 * sy * sy)).astype(np.float32)
        e = self.emit.reshape(self.h, self.w, 3)
        e[y0:y1, x0:x1] += gy[:, None, None] * gx[None, :, None] * rgb

    # -- polygons

    def coverage(self, pts):
        """Anti-aliased coverage of a polygon, as (y0, x0, mask) over its box.
        Four sub-rows per pixel; exact fractional coverage along each row."""
        P = np.array(pts, np.float64)
        xs, ys = P[:, 0], P[:, 1]
        bx0, bx1 = max(0, int(math.floor(xs.min()))), min(self.w, int(math.ceil(xs.max())) + 1)
        by0, by1 = max(0, int(math.floor(ys.min()))), min(self.h, int(math.ceil(ys.max())) + 1)
        if bx0 >= bx1 or by0 >= by1:
            return None
        ex0, ey0 = xs, ys
        ex1, ey1 = np.roll(xs, -1), np.roll(ys, -1)
        mask = np.zeros((by1 - by0, bx1 - bx0), np.float32)
        cols = np.arange(bx0, bx1, dtype=np.float64)
        SUB = 4
        for row in range(by0, by1):
            acc = np.zeros(bx1 - bx0, np.float64)
            for s in range(SUB):
                yy = row + (s + 0.5) / SUB
                hit = (ey0 <= yy) != (ey1 <= yy)
                if not hit.any():
                    continue
                xa = ex0[hit] + (yy - ey0[hit]) * (ex1[hit] - ex0[hit]) / (ey1[hit] - ey0[hit])
                xa.sort()
                for a, b in zip(xa[0::2], xa[1::2]):
                    acc += np.clip(np.minimum(b, cols + 1) - np.maximum(a, cols), 0, 1)
            mask[row - by0] = acc / SUB
        return by0, bx0, mask

    def fill(self, pts, rgb):
        cov = self.coverage(pts)
        if cov is None:
            return
        self.flush()
        y0, x0, m = cov
        h, w = m.shape
        m3 = m[:, :, None]
        b = self.base[y0:y0 + h, x0:x0 + w]
        b *= 1 - m3
        b += rgb * m3
        e = self.emit.reshape(self.h, self.w, 3)[y0:y0 + h, x0:x0 + w]
        e *= 1 - m3

    def patch(self, pts, rgb):
        cov = self.coverage(pts)
        if cov is None:
            return
        y0, x0, m = cov
        h, w = m.shape
        e = self.emit.reshape(self.h, self.w, 3)
        e[y0:y0 + h, x0:x0 + w] += m[:, :, None] * rgb

    # -- the frame

    def finish(self, look):
        self.flush()
        emit = self.emit.reshape(self.h, self.w, 3) * look["gain"]
        # Multi-scale bloom: blur at falling resolutions, add back up.
        k = self.h / 1080.0
        bloom = np.zeros_like(emit)
        for f, r, wt in look["bloom"]:
            f = max(1, int(round(f * k)))
            if f == 1:
                bloom += wt * _tight(emit)
                continue
            small = _blur(_downsample(emit, f), max(1, int(round(r))))
            bloom += wt * _upsample(small, self.h, self.w)
        x = self.base + emit + bloom
        # Tone map: linear to 0.6, then a soft shoulder to 1; any channel that
        # overflows spills into the others, so hot blue burns toward white.
        over = np.clip(x.max(axis=2, keepdims=True) - 1.0, 0, None)
        y = np.where(x < 0.6, x, 1.0 - 0.4 * np.exp(-(x - 0.6) / 0.4))
        y = y + (1.0 - y) * np.clip(over * look["burn"], 0, 0.85)
        # Vignette, then an ordered dither so the long dark gradients of the
        # bloom do not band at 8 bits.
        ux = ((np.arange(self.w, dtype=np.float32) + 0.5) / (self.w / 2) - 1) ** 2 * 0.6
        uy = ((np.arange(self.h, dtype=np.float32) + 0.5) / (self.h / 2) - 1) ** 2
        y *= (1.0 - look["vignette"] * np.clip(uy[:, None] + ux[None, :], 0, 1.6))[:, :, None]
        y *= 255.0
        y += np.tile(_BAYER, (self.h // 8 + 1, self.w // 8 + 1))[:self.h, :self.w, None]
        return np.clip(y, 0, 255).astype(np.uint8)


# 8x8 ordered-dither thresholds, centred on zero, in 8-bit steps.
_B2 = np.array([[0, 2], [3, 1]])
_B4 = np.block([[4 * _B2, 4 * _B2 + 2], [4 * _B2 + 3, 4 * _B2 + 1]])
_BAYER = ((np.block([[4 * _B4, 4 * _B4 + 2], [4 * _B4 + 3, 4 * _B4 + 1]]) + 0.5) / 64 - 0.5).astype(np.float32)


LOOK = {
    "gain": 1.25,
    # (downsample factor at 1080p, blur radius at that resolution, weight)
    "bloom": [(1, 1, 0.30), (2, 3, 0.30), (4, 5, 0.26), (8, 7, 0.22), (16, 9, 0.18)],
    "burn": 0.55,
    "vignette": 0.30,
}


def render(scene, look=LOOK):
    R = _Raster(scene)
    v = scene.view
    s = v.scale
    for p in scene.prims:
        kind = p[0]
        if kind == "line":
            _, pts, role, i, width = p
            if i <= 0.004 or len(pts) < 2:
                continue
            rgb = R.roles[role] * i
            wpx = max(0.6, width * s)
            q = [v.px(x, y) for x, y in pts]
            for (ax, ay), (bx, by) in zip(q, q[1:]):
                R._segs.append((ax, ay, bx, by, rgb[0], rgb[1], rgb[2], wpx))
        elif kind == "dot":
            _, x, y, r, role, i = p
            X, Y = v.px(x, y)
            sig = max(0.42, r * s * 0.6)
            rgb = R.roles[role] * i
            if sig * 3 > 14:
                R.glow(X, Y, sig, sig, rgb)
            else:
                R._dots.append((X, Y, sig, rgb[0], rgb[1], rgb[2]))
        elif kind == "glow":
            _, x, y, rx, ry, role, i = p
            X, Y = v.px(x, y)
            R.glow(X, Y, max(0.5, rx * s / 2), max(0.5, ry * s / 2), R.roles[role] * i)
        elif kind == "fill":
            _, pts, role, i = p
            R.fill([v.px(x, y) for x, y in pts], R.bg + (R.roles[role] - R.bg) * i)
        elif kind == "patch":
            _, pts, role, i = p
            R.patch([v.px(x, y) for x, y in pts], R.roles[role] * i)
        else:
            raise ValueError(kind)
    return R.finish(look)


# --------------------------------------------------------------------------
# out
# --------------------------------------------------------------------------

def photo(img):
    """numpy (h, w, 3) uint8 -> tk.PhotoImage, through raw PPM bytes."""
    import tkinter as tk
    h, w, _ = img.shape
    return tk.PhotoImage(data=b"P6 %d %d 255\n" % (w, h) + img.tobytes(), format="PPM")


def write_png(img, path):
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(h))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
        fh.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        fh.write(chunk(b"IEND", b""))
