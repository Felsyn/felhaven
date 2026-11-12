"""
sandbox/render_still.py — Pythia v2 stills, as PNG
==================================================
SANDBOX. See sandbox/README.md.

Writes the exact pixels the full-screen window shows — the same scene through
the same renderer — straight to PNG. No window and no display needed, so it
runs headless anywhere.

Writes into sandbox/renders/:
    pythia_v2_1920x1080.png            full screen, reference colourway
    pythia_v2_phosphor_1920x1080.png   full screen, Felhaven's palette.C
  with --all, also:
    pythia_v2_2560x1440.png            full screen at 1440p
    pythia_v2_poster_1312x1936.png     the reference's own framing, only for
                                       comparing against the reference
Only the first two are committed; the rest regenerate in seconds.

Run:     python sandbox/render_still.py [--all] [--seed N] [--size WxH ...]
"""

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pythia_face_v2 as v2         # noqa: E402
import pythia_v2_raster as raster   # noqa: E402

JOBS = [
    ("pythia_v2_1920x1080.png", (1920, 1080), "reference", False),
    ("pythia_v2_phosphor_1920x1080.png", (1920, 1080), "phosphor", False),
]
MORE = [
    ("pythia_v2_2560x1440.png", (2560, 1440), "reference", False),
    ("pythia_v2_poster_1312x1936.png", (1312, 1936), "reference", True),
]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="also 1440p and the poster framing")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--size", nargs="*", help="only these, e.g. 1920x1080")
    ap.add_argument("--out", default=os.path.join(HERE, "renders"))
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)

    jobs = JOBS + (MORE if args.all else [])
    if args.size:
        jobs = [("pythia_v2_%s.png" % s, tuple(int(n) for n in s.split("x")), "reference", False)
                for s in args.size]
    for name, (w, h), colourway, poster in jobs:
        t0 = time.time()
        view = v2.poster_view(w, h) if poster else v2.screen_view(w, h)
        scene = v2.build_scene(view, colourway, seed=args.seed)
        t1 = time.time()
        img = raster.render(scene)
        t2 = time.time()
        raster.write_png(img, os.path.join(args.out, name))
        print("%-36s %5d prims   scene %.1fs   render %.1fs"
              % (name, len(scene.prims), t1 - t0, t2 - t1))


if __name__ == "__main__":
    main()
