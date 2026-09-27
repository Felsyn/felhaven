"""
test_artemis_palette.py — the map's colour rules.

Until 2026-09-26 this was a lockstep guard for the one colour set that existed
twice: tools/demeter.py carried the terrain bands as RGB tuple copies of
theme.C's map_* keys. Those copies are gone — demeter now calculates its bands
from palette.py, which is Tk-free and annotated, so a tools/ module can import
it (CONVENTIONS §12, 2026-09-26). What remains here are the map's own rules:
which palette role each raster band is drawn from, that every key the panel
draws with exists, and that nothing on the map reaches text brightness.
Keeping ANY colour literal out of every file but palette.py is
test_palette_guard's job, not this file's.

    python -X utf8 -m unittest tests.test_artemis_palette
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import palette
from tools import demeter

# demeter band constant -> the palette role it is drawn from. A decision, not
# a copy: the raster's missing data renders as the panel background, so it
# looks missing rather than looking like a valley.
_BANDS = {
    "RGB_VALLEY": "map_terrain_lo",
    "RGB_MID": "map_terrain_mid",
    "RGB_PEAK": "map_terrain_hi",
    "RGB_NODATA": "bg",
}

# Every map key the panel draws with, so a rename fails here rather than at
# runtime inside a canvas call.
_MAP_KEYS = [
    "map_terrain_lo", "map_terrain_mid", "map_terrain_hi",
    "map_water", "map_water_minor", "map_water_area",
    "map_road_hi", "map_road_mid", "map_road_lo", "map_road_track",
    "map_ring", "map_compass",
]


class TestMapPalette(unittest.TestCase):

    def test_each_band_is_drawn_from_its_role(self):
        for const_name, role in _BANDS.items():
            with self.subTest(constant=const_name):
                self.assertEqual(getattr(demeter, const_name),
                                 palette.rgb(palette.C[role]),
                                 f"demeter.{const_name} is not drawn from "
                                 f"palette.C[{role!r}]")

    def test_every_band_is_claimed(self):
        # Derived, not counted: a new RGB_* constant in demeter must name the
        # palette role it is drawn from here, or this fails.
        declared = {name for name in vars(demeter)
                    if name.startswith("RGB_")}
        self.assertEqual(declared, set(_BANDS))

    def test_map_keys_exist(self):
        for key in _MAP_KEYS:
            with self.subTest(key=key):
                self.assertTrue(key in palette.C,
                                f"{key!r} is drawn by the map but missing "
                                f"from palette.C")

    def test_no_map_key_reaches_text_brightness(self):
        # The chart must stay readable *under* the phosphor text: nothing on
        # the map may be as luminous as C["text1"] (spec §11 palette rule).
        def luminance(hex_colour):
            r, g, b = palette.rgb(hex_colour)
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        text = luminance(palette.C["text1"])
        for key in _MAP_KEYS:
            with self.subTest(key=key):
                self.assertLess(luminance(palette.C[key]), text)


if __name__ == "__main__":
    unittest.main()
