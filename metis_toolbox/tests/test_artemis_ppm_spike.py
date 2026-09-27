"""
test_artemis_ppm_spike.py — the Artemis step-0 spike, kept as a permanent
regression guard: tk.PhotoImage must accept a binary P6 PPM passed as RAW
BYTES via its data= option. The whole Demeter terrain-raster path (a
pre-rendered PPM decompressed from cache and handed to one create_image
call) rests on this working.

Spike verdict (Tk 8.6.12, 2026-08-09): the spec's original base64-encoded
form is REJECTED by this build ("couldn't recognize image data") — the PPM
handler only recognizes raw bytes, which tkinter passes through as a Tcl
byte array. Raw-bytes decode of a 1024² PPM measured ~4 ms. A hand-written
stdlib PNG also works (raw or base64) and is the next rung of the fallback
ladder if a future Tk drops raw-PPM data= support; this file going red is
the signal to take that rung.

Same headless approach as the other panel smoke tests: a withdrawn tk.Tk()
root driven with update_idletasks().

    python -X utf8 -m unittest tests.test_artemis_ppm_spike
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme


def _ppm(width, height, pixels):
    """Binary P6 PPM: header then raw RGB triples, row-major."""
    header = f"P6\n{width} {height}\n255\n".encode("ascii")
    body = bytes(v for px in pixels for v in px)
    return header + body


def _rgb(photo, x, y):
    """PhotoImage.get() returns a tuple of ints on modern Tk but a 'r g b'
    string on some builds — normalize to a plain (r, g, b) tuple."""
    got = photo.get(x, y)
    if isinstance(got, str):
        return tuple(int(v) for v in got.split())
    return tuple(got)


class TestPpmSpike(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_raw_p6_roundtrip(self):
        # Four distinct pixels so a channel swap or stride bug shows up as a
        # wrong sample, not a coincidental pass. They are DATA for Tk's
        # decoder, not the app's colours: they happen to be the 2026-08-09
        # map palette, and must NOT follow it if the palette is retuned
        # (CONVENTIONS §12, 2026-09-26 — a test literal is evidence when a
        # palette change would not require editing it).
        pixels = [(11, 46, 24), (26, 92, 49), (44, 138, 74), (224, 169, 74)]
        photo = tk.PhotoImage(data=_ppm(2, 2, pixels))
        self.assertEqual((photo.width(), photo.height()), (2, 2))
        self.assertEqual(_rgb(photo, 0, 0), (11, 46, 24))
        self.assertEqual(_rgb(photo, 1, 0), (26, 92, 49))
        self.assertEqual(_rgb(photo, 0, 1), (44, 138, 74))
        self.assertEqual(_rgb(photo, 1, 1), (224, 169, 74))

    def test_canvas_accepts_the_image(self):
        pixels = [(0, 0, 0)] * 16
        photo = tk.PhotoImage(data=_ppm(4, 4, pixels))
        canvas = tk.Canvas(self.root, width=8, height=8)
        try:
            canvas.create_image(4, 4, image=photo)
            self.root.update_idletasks()
        finally:
            canvas.destroy()

    def test_zoom_and_subsample_stops(self):
        # The three Artemis zoom stops are integer factors of the native
        # raster: zoom(2) for 2x, subsample(2) for 0.5x.
        pixels = [(10 * i, 0, 0) for i in range(16)]
        photo = tk.PhotoImage(data=_ppm(4, 4, pixels))
        doubled = photo.zoom(2)
        self.assertEqual((doubled.width(), doubled.height()), (8, 8))
        # Nearest-neighbour doubling: the 2x2 block matches the source pixel.
        self.assertEqual(_rgb(doubled, 0, 0), _rgb(photo, 0, 0))
        self.assertEqual(_rgb(doubled, 1, 1), _rgb(photo, 0, 0))
        halved = photo.subsample(2)
        self.assertEqual((halved.width(), halved.height()), (2, 2))

    def test_map_scale_decode(self):
        # 1024² is the real terrain grid size; this must stay fast enough to
        # run on every panel switch (measured ~4 ms at spike time).
        row = bytes(range(256)) * 12          # 1024 RGB pixels
        photo = tk.PhotoImage(data=b"P6\n1024 1024\n255\n" + row * 1024)
        self.assertEqual((photo.width(), photo.height()), (1024, 1024))


if __name__ == "__main__":
    unittest.main()
