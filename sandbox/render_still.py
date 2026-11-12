"""
sandbox/render_still.py — Pythia v2 stills, as PNG
==================================================
SANDBOX. Paints each framing on a real tk.Canvas and grabs the window, so a
still shows exactly what the widget draws — no second renderer.

Writes into sandbox/renders/:
    pythia_v2_poster.png             1312x1936, reference colourway, all layers
    pythia_v2_face_380x320.png       the head at twice the header slot
    pythia_v2_face_190x160.png       the head at the Hestia header's own size
    pythia_v2_phosphor_poster.png    the poster in Felhaven's palette.C
    pythia_v2_phosphor_face_190x160.png

Run:     Linux, headless (grabs with ImageMagick's `import`):
             xvfb-run -s "-screen 0 1400x2000x24" python3 sandbox/render_still.py
         Windows (grabs with Pillow's ImageGrab, needs a screen tall enough
         for the poster, or pass --small to halve it):
             python sandbox\\render_still.py
         --seed N   draws a different, equally valid figure
"""

import argparse
import os
import shutil
import subprocess
import sys
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pythia_face_v2 as v2  # noqa: E402


def _grab(canvas, path):
    canvas.update()
    if os.environ.get("DISPLAY") and shutil.which("import"):
        subprocess.run(["import", "-window", str(canvas.winfo_id()), path], check=True)
        return
    from PIL import ImageGrab   # Windows / macOS
    x, y = canvas.winfo_rootx(), canvas.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x + canvas.winfo_width(), y + canvas.winfo_height())).save(path)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--small", action="store_true", help="poster at half size")
    ap.add_argument("--out", default=os.path.join(HERE, "renders"))
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)

    pw, ph = (656, 968) if args.small else (1312, 1936)
    jobs = [
        ("pythia_v2_poster.png", v2.poster_view(pw, ph), "reference", v2.ALL_LAYERS),
        ("pythia_v2_face_380x320.png", v2.face_view(380, 320), "reference", ("figure",)),
        ("pythia_v2_face_190x160.png", v2.face_view(190, 160), "reference", ("figure",)),
        ("pythia_v2_phosphor_poster.png", v2.poster_view(pw, ph), "phosphor", v2.ALL_LAYERS),
        ("pythia_v2_phosphor_face_190x160.png", v2.face_view(190, 160), "phosphor", ("figure",)),
    ]

    root = tk.Tk()
    root.withdraw()
    for name, view, colourway, layers in jobs:
        top = tk.Toplevel(root)
        top.overrideredirect(True)
        top.geometry("+0+0")
        canvas = tk.Canvas(top, width=view.w, height=view.h, highlightthickness=0, bd=0)
        canvas.pack()
        scene = v2.build_scene(view, colourway, seed=args.seed, layers=layers)
        items = v2.draw(canvas, scene)
        path = os.path.join(args.out, name)
        _grab(canvas, path)
        print("%-40s %4dx%-4d %6d items" % (name, view.w, view.h, items))
        top.destroy()
    root.destroy()


if __name__ == "__main__":
    main()
