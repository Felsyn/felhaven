"""
palette.py — The Palette
========================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Hold every colour value Felhaven uses, in one place.

             The single source of truth for colour: no other file may contain
             a colour value. theme.py re-exports C (and
             rgb) so panels keep importing colours from theme; a headless
             tools/ module imports this file directly. It imports nothing — no
             tkinter — and is fully annotated, because tools/ runs under
             mypy --strict and mypy follows every import it is given.

Contract:    C                — {role: "#RRGGBB"}. Every value is six-digit hex.
             rgb(hex_colour)  — "#7CFC7C" -> (124, 252, 124), for code that
                                blends or rasterises in RGB (demeter's terrain,
                                pythia_face's glow ramps). ValueError on
                                anything that is not "#RRGGBB".

Same value:  Two keys holding the same colour are one of two things, and the
             file says which:
               identity    — they MEAN the same colour. The second is listed
                             in _SAME_AS and calculated from the first, so
                             retuning one retunes both.
               coincidence — they happen to match today and must be free to
                             drift apart (verbatim and map_water). Both stay
                             literals; tests/test_palette_guard.py holds the
                             declared list, and fails on any undeclared pair.

Guard:       tests/test_palette_guard.py fails red if a colour literal appears
             in any other file — a hex string, a colour name in a colour
             option, or an RGB triple. See CONVENTIONS §7 and §12 (2026-09-26).

Upstream:    theme.py (re-exports C and rgb to every panel), tools/demeter.py
             (terrain raster RGB), tests/test_palette_guard.py
Downstream:  none (imports nothing)
"""


# ─────────────────────────────────────────────────────────────────────────────
#  Colours with a value of their own
# ─────────────────────────────────────────────────────────────────────────────

C: dict[str, str] = {
    "bg":        "#0A0E0A",   # black — faintest green cast (set to #000000 for pure black)
    "card":      "#0C120C",   # panel fill — one hair above bg
    "border":    "#2E7D32",   # phosphor border (rendered at thickness 2 now)
    "text1":     "#7CFC7C",   # bright phosphor — primary / active text
    "text2":     "#4FB04F",   # mid phosphor — labels
    "text3":     "#2F7A2F",   # dim phosphor — subtitles / inactive
    "amber":     "#D6A11F",   # THE uniform nav dot
    # Text shown EXACTLY as a tool gave it, not spoken by anyone — a Daedalus
    # docstring in the chat (pythia's on_verbatim). The one cyan outside the
    # map, so a quotation can never be mistaken for Pythia (green), the DJ
    # (amber) or a failure (red). Same value as map_water today, but its own
    # key: retuning the rivers must never recolour the chat.
    "verbatim":  "#2E8B99",
    # The colour an overdriven phosphor burns out toward. Only the very top of
    # pythia_face's glow ramps reaches for it (and only ~55% of the way), so it
    # never appears flat on screen — it is what makes the hottest edge read as
    # glowing rather than merely bright.
    "burnout":   "#FFFFFF",
    # ── genuinely semantic — kept distinct ──
    "red":       "#E24B4A",   # alarm / expired timer ONLY
    # The SAME alarm channel, hovered — not a second meaning. Reserved for the
    # hover state of a red control (the Rite Log's "N failed" span). Amber would
    # be wrong here: it is the informational/road channel, and flipping an alarm
    # to it on hover reports a failure as a notice.
    "red_bright": "#FF7A79",
    # The SAME informational channel as `amber`, hovered — not a second
    # meaning, exactly as `red_bright` is to `red`. Needed because the Hestia
    # vox controls invert §7 (active = amber, inactive = dim), so amber can no
    # longer double as the hover colour: hover has to brighten *within* the
    # control's current state instead of announcing one.
    "amber_bright": "#F5C24A",
    # ── badges & bars ──
    "badge_bg":  "#0F2A0F",
    "badge_fg":  "#9DFF9D",
    "season_bg": "#0C240C",
    "bar_bg":    "#0E160E",
    # ── Artemis chart — the one place hue carries meaning ──────────────────
    # A map's layers have to be told apart at a glance, so these deliberately
    # do NOT fold into the phosphor ramp above: cyan water and amber roads are
    # near-complementary, both separate cleanly from green terrain, and the
    # set survives common red-green colour deficiencies. Nothing reaches text
    # brightness, so the phosphor text still sits on top of the map.
    #
    # These are the WORLD's colours. The chart's INSTRUMENT — range rings,
    # bearing ticks, the porthole rim and its range label, the cardinals, the
    # centre fix — draws in text1 instead, so brightness says what kind of
    # thing you are looking at. That is why these keys stay muted rather than
    # being brightened to match.
    # tools/demeter.py rasterises the three terrain colours from these keys
    # through rgb() — they exist once, here.
    "map_terrain_lo":  "#0B2E18",   # valley — barely above background
    "map_terrain_mid": "#1A5C31",
    "map_terrain_hi":  "#2C8A4A",   # peak — ~30% of text luminance
    "map_water":       "#2E8B99",   # named runs, creeks, rivers
    "map_water_minor": "#1F5F69",   # unnamed drainage, ditches, canals
    "map_water_area":  "#17454D",   # lakes/ponds fill, no outline
    "map_road_hi":     "#E0A94A",   # primary — brightest non-text element
    "map_road_mid":    "#B9862F",   # secondary
    "map_road_lo":     "#8A6524",   # local streets
    "map_road_track":  "#6B4F1E",   # vehicular trail / private, dashed
    "map_ring":        "#264A33",   # dim chrome — the scale bar's metric line
    "map_compass":     "#3E7A55",   # the compass rose and the scale bar
}


# ─────────────────────────────────────────────────────────────────────────────
#  Roles that are the SAME colour as another key, by design
#  alias -> the key it is calculated from. Identity, not coincidence: change
#  the target and every alias follows. To give an alias a colour of its own,
#  move it out of this table and into C above as a literal — a visible act.
# ─────────────────────────────────────────────────────────────────────────────

_SAME_AS: dict[str, str] = {
    "green":     "text1",   # active nav dot (== text1, so the active item "lights up")
    "season_fg": "text1",   # the season badge's text is the phosphor text itself
    # Selected text in every Entry/Text, set app-wide by
    # theme._init_widget_defaults. Deliberately the look of a hovered menu
    # item (themed_menu): the app's one answer to "this is the picked thing".
    "select_bg": "border",
    "select_fg": "text1",
    # ── decorative accents, folded into the phosphor ramp so the theme reads monochrome ──
    "teal":      "text2",
    "blue":      "text2",
    "purple":    "text2",
    "coral":     "amber",
    "gray":      "text3",
}

C.update({alias: C[target] for alias, target in _SAME_AS.items()})


def rgb(hex_colour: str) -> tuple[int, int, int]:
    """'#7CFC7C' -> (124, 252, 124). The one hex parser in the stack: code that
    blends or rasterises in RGB converts a palette value here rather than
    carrying its own copy of the numbers."""
    if len(hex_colour) != 7 or hex_colour[0] != "#":
        raise ValueError(f"not a #RRGGBB colour: {hex_colour!r}")
    return (int(hex_colour[1:3], 16), int(hex_colour[3:5], 16),
            int(hex_colour[5:7], 16))
