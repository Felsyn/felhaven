"""
test_artemis_palette.py — lockstep guard for the one colour set that exists
twice.

tools/demeter.py renders the terrain raster headless (a tools/ module must
not import Tk), so it carries the three band colours as RGB tuples while
panels/artemis_panel.py draws vectors from theme.C's map_* keys. Two copies
of one palette is a drift waiting to happen: the raster would band in one
green ramp and the legend/vectors in another, and nothing would fail. This
asserts the pair agrees — derived from the actual constants, never a
hardcoded duplicate of either.

    python -X utf8 -m unittest tests.test_artemis_palette
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import theme
from tools import demeter

# demeter constant name -> the theme.C key it must equal.
_PAIRS = {
    "RGB_VALLEY": "map_terrain_lo",
    "RGB_MID": "map_terrain_mid",
    "RGB_PEAK": "map_terrain_hi",
    "RGB_NODATA": "bg",          # missing data renders as the panel background
}

# Every map key the panel draws with, so a rename fails here rather than at
# runtime inside a canvas call.
_MAP_KEYS = [
    "map_terrain_lo", "map_terrain_mid", "map_terrain_hi",
    "map_water", "map_water_minor", "map_water_area",
    "map_road_hi", "map_road_mid", "map_road_lo", "map_road_track",
    "map_ring", "map_compass",
]


def _rgb(hex_colour):
    value = hex_colour.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


class TestPaletteLockstep(unittest.TestCase):

    def test_demeter_bands_match_theme(self):
        for const_name, theme_key in _PAIRS.items():
            with self.subTest(constant=const_name):
                self.assertEqual(getattr(demeter, const_name),
                                 _rgb(theme.C[theme_key]),
                                 f"demeter.{const_name} and theme.C"
                                 f"[{theme_key!r}] have drifted apart")

    def test_every_pair_is_covered(self):
        # Derived, not counted: a new RGB_* constant in demeter must be
        # claimed here or this fails.
        declared = {name for name in vars(demeter)
                    if name.startswith("RGB_")}
        self.assertEqual(declared, set(_PAIRS))

    def test_map_keys_exist_and_are_hex(self):
        for key in _MAP_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, theme.C)
                self.assertRegex(theme.C[key], r"^#[0-9A-Fa-f]{6}$")

    def test_no_map_key_reaches_text_brightness(self):
        # The chart must stay readable *under* the phosphor text: nothing on
        # the map may be as luminous as C["text1"] (spec §11 palette rule).
        def luminance(hex_colour):
            r, g, b = _rgb(hex_colour)
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        text = luminance(theme.C["text1"])
        for key in _MAP_KEYS:
            with self.subTest(key=key):
                self.assertLess(luminance(theme.C[key]), text)


if __name__ == "__main__":
    unittest.main()
