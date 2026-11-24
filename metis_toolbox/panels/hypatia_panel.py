"""
Hypatia panel — zenith star map ("Celestarium") for the Felhaven dashboard.

Job:         Draw the sky overhead with its constellations, their myths and tonight's clarity.

HypatiaPanel draws an all-sky zenith projection (zenith at center, horizon at
the ring, North up) from the tools/hypatia.fetch() payload. A constellation
list lets you highlight a shape and read its mythology; a latitude preset row
(Current / N Pole / Equator / S Pole) is a teaching-mode jump that dims the
whole panel and shows a simulation notice, since the chart is then showing a
sky this machine isn't actually under. ConditionsWidget is a small embedded
sub-widget (bottom-left, under the canvas) that rides the `aura` worker —
same AetherWidget precedent as Hephaestus's connectivity rows — showing
cloud-cover-derived "clarity" stars and cloud %. Two more rows ride their own
workers through riders: moon illumination % (`moon_row`, the `selene`
worker) and the aurora estimate (`aurora_row`, the `eos` worker).

The panel does no astronomy: RA/Dec -> alt/az lives in tools/hypatia.py. The
only math here is alt/az -> canvas x/y (azimuthal equidistant projection),
plus small display lookups (cloud-cover -> star glyphs, a fixed dim ramp for
the simulation/selection color states).

Upstream:    felhaven.py builds it and registers four Kairos feeds: `hypatia`
             (the panel), `aura` (conditions), `selene` (conditions.moon_row)
             and `eos` (conditions.aurora_row). felhaven._apply_scale calls
             rescale().
Downstream:  tools/hypatia.py (set_preset() on a preset click, compass() for
             a picked planet), panels/eos_panel.py (AuroraRow), theme.py,
             tools/virgil.py and panels/virgil_tooltip.py (the tooltips).

Requires:    math, tkinter, datetime (stdlib).

2026-07-25:  walkthrough audit — dropped this panel's duplicate _compass for
             the shared hypatia.compass, so the on-screen planet direction and
             the LLM digest's direction can never diverge. Verified
             ConditionsWidget is safe against aura's int cloud_cover_pct +
             string moon_illumination (no §10 string-compare bug — the `<=`
             clarity thresholds only ever see the int). No behavior change.

2026-09-28:  the moon row became `moon_row`, fed by the `selene` worker. It
             used to show wttr.in's illumination from the aura payload, which
             went blank offline and on Aura's moonless fallback, and could
             differ from SELENE's (V0.03 audit M3).

2026-11-10:  the aurora row arrived as `aurora_row`, fed by the `eos` worker
             (panels/eos_panel.py), the same pattern as the moon row.

V0.03:       Walked through 2026-11-20 (the V0.03 walkthrough's S20).
             Deselecting a constellation, or picking a planet, left the old
             highlight lit until the next redraw, up to 60 s; it has done so
             since the panel was born (2026-07-01). _apply_highlight() now
             sets the neutral colours first, from the one _NEUTRAL table the
             redraw also draws with. The dead _ScrollFrame.clear() is gone
             (no caller, ever), and the clarity read lost a default it could
             never use. Words: Upstream, Downstream and Requires added, the
             aurora row named, ConditionsWidget's two dated notes moved up
             here (§3), the preset row's width re-measured. Checked and
             true: the projection, the simulation dim, the planet and
             constellation exclusivity, rescale() (felhaven calls it), the
             conditions packed first.
"""

import math
import tkinter as tk
from datetime import datetime

from panels import virgil_tooltip
from panels.eos_panel import AuroraRow
from theme import C, FONTS, Card, PhosphorScroll

from tools import hypatia, virgil


# ─────────────────────────────────────────────────────────────────────────────
#  Dimming ramp — a fixed order down the phosphor palette, shared by the
#  simulation-mode dim and the "non-member constellation" dim so the two
#  effects stack instead of needing separate color tables.
# ─────────────────────────────────────────────────────────────────────────────

_RAMP_KEYS = ["text1", "text2", "text3", "bar_bg"]


def _ramp_color(base_key: str, dim_steps: int) -> str:
    idx = min(_RAMP_KEYS.index(base_key) + max(0, dim_steps), len(_RAMP_KEYS) - 1)
    return C[_RAMP_KEYS[idx]]


# Each highlightable canvas tag's undimmed ramp key. _redraw() draws with it
# and _apply_highlight() resets to it, so a cleared selection goes back to
# exactly the colours a fresh redraw would give.
_NEUTRAL = {"line": "text2", "starbright": "text1", "stardim": "text2"}


def _clarity_stars(cloud_pct: int) -> str:
    """Cloud-cover threshold map -> a five-star glyph string (display-layer
    lookup, the aura_panel._icon() precedent)."""
    if cloud_pct <= 10: return "★★★★★"
    if cloud_pct <= 30: return "★★★★☆"
    if cloud_pct <= 55: return "★★★☆☆"
    if cloud_pct <= 75: return "★★☆☆☆"
    if cloud_pct <= 90: return "★☆☆☆☆"
    return "☆☆☆☆☆"


_PRESET_LABELS = [
    ("current",    "Current"),
    ("north_pole", "N Pole"),
    ("equator",    "Equator"),
    ("south_pole", "S Pole"),
]

_SIM_NOTICE = {
    "north_pole": "SIMULATED SKY — NORTH POLE",
    "equator":    "SIMULATED SKY — EQUATOR",
    "south_pole": "SIMULATED SKY — SOUTH POLE",
}

# The five classical planets kepler.positions() can ever name — fixed, so
# their canvas tag_binds are registered once at panel construction rather
# than re-bound on every redraw (the constellation-abbr precedent).
_PLANET_NAMES = ["Mercury", "Venus", "Mars", "Jupiter", "Saturn"]


# ─────────────────────────────────────────────────────────────────────────────
#  _ScrollFrame — vertically scrollable container (pheme_panel.py pattern,
#  copied per the one-per-panel house convention, CONVENTIONS §7).
# ─────────────────────────────────────────────────────────────────────────────

class _ScrollFrame(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        self._canvas = tk.Canvas(self, bg=C["card"], highlightthickness=0, bd=0)
        scroll = PhosphorScroll(self, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scroll.set)

        scroll.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self.inner = tk.Frame(self._canvas, bg=C["card"])
        self._win = self._canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self._wrap_labels: list = []

        self.inner.bind("<Configure>", self._on_inner_config)
        self._canvas.bind("<Configure>", self._on_canvas_config)

        self._canvas.bind("<Enter>", self._bind_wheel)
        self._canvas.bind("<Leave>", self._unbind_wheel)

    def _on_inner_config(self, _event) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_config(self, event) -> None:
        self._canvas.itemconfigure(self._win, width=event.width)
        for label, pad in self._wrap_labels:
            label.config(wraplength=max(1, event.width - pad))

    def _bind_wheel(self, _event) -> None:
        self._canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event) -> None:
        self._canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event) -> None:
        self._canvas.yview_scroll(int(-event.delta / 120), "units")

    def add_wrap_label(self, label: tk.Label, pad: int = 16) -> None:
        self._wrap_labels.append((label, pad))
        width = self._canvas.winfo_width()
        if width > 1:
            label.config(wraplength=max(1, width - pad))

    def scroll_to_top(self) -> None:
        self._canvas.yview_moveto(0)


# ─────────────────────────────────────────────────────────────────────────────
#  ConditionsWidget — observation conditions, embedded bottom-left
# ─────────────────────────────────────────────────────────────────────────────

class _MoonRow:
    """The "moon illum." row's own Kairos rider, fed by the `selene` worker.
    The rest of ConditionsWidget rides `aura`, and Kairos passes update() no
    worker name, so one object can't take both feeds (the dj_rider
    precedent). The label stays inside the widget's rows frame, so the
    simulated-sky notice still hides it with the others."""

    def __init__(self, label: tk.Label) -> None:
        self._label = label

    def update(self, info) -> None:
        """selene.fetch()'s dict, the same one SELENE and get_sky_report show.
        An error or None reads "—"."""
        ok = bool(info) and "error" not in info
        self._label.config(text=info["illumination"] if ok else "—")


class ConditionsWidget(tk.Frame):
    """
    Rides the `aura` worker (AetherWidget precedent — an embedded widget with
    its own Kairos registration). update(data) receives the FULL aura
    payload (or None); missing keys / an {"error": ...} dict all degrade to
    "—", never a crash. Collapses to a one-line local-only notice while
    Hypatia is showing a simulated (non-current) sky. The moon and aurora
    rows are not aura's: `moon_row` rides `selene` and `aurora_row` rides
    `eos`.
    """

    _ROWS = ("clarity", "cloud cover", "moon illum.", "aurora")

    # Hover tooltip (Virgil) for the star rating, a glossary entry
    # (CONVENTIONS §7). The aurora row's tip is attached beside its rider.
    _TIPS = {
        "clarity": virgil.term("sky_clarity"),
    }

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        tk.Label(self, text="OBSERVATION CONDITIONS", font=FONTS["card_header"],
                 fg=C["text3"], bg=C["card"], anchor="w").pack(fill="x")

        self._rows_frame = tk.Frame(self, bg=C["card"])
        self._rows_frame.pack(fill="x")
        self._values = {}
        for key in self._ROWS:
            row = tk.Frame(self._rows_frame, bg=C["card"])
            row.pack(fill="x")
            tk.Label(row, text=key, font=FONTS["small"], fg=C["text2"],
                     bg=C["card"], anchor="w").pack(side="left")
            v = tk.Label(row, text="—", font=FONTS["small"], fg=C["text1"],
                         bg=C["card"], anchor="e")
            v.pack(side="right")
            self._values[key] = v
        self.moon_row = _MoonRow(self._values["moon illum."])
        # The aurora here, on Kairos's "eos" worker: a rider like moon_row.
        self.aurora_row = AuroraRow(self._values["aurora"])
        virgil_tooltip.attach(self._values["aurora"].master,
                              virgil.term("aurora_estimate"))
        self.tip_targets: dict[str, tk.Misc] = {
            "clarity": self._values["clarity"].master}
        for key, tip in self._TIPS.items():
            virgil_tooltip.attach(self.tip_targets[key], tip)

        self._sim_lbl = tk.Label(
            self, text="local conditions — current location only",
            font=FONTS["small_italic"], fg=C["text3"], bg=C["card"], anchor="w",
        )

        self._simulated = False
        self._last = None

    def set_simulated(self, simulated: bool) -> None:
        if simulated == self._simulated:
            return
        self._simulated = simulated
        self._render()

    def update(self, data) -> None:
        self._last = data
        self._render()

    def _render(self) -> None:
        if self._simulated:
            self._rows_frame.pack_forget()
            self._sim_lbl.pack(fill="x")
            return
        self._sim_lbl.pack_forget()
        self._rows_frame.pack(fill="x")

        # Only the weather rows: "moon illum." is moon_row's, fed by Selene,
        # and "aurora" is aurora_row's, fed by Eos.
        data = self._last
        if not data or "error" in data or "cloud_cover_pct" not in data:
            for key in ("clarity", "cloud cover"):
                self._values[key].config(text="—")
            return

        cloud = data["cloud_cover_pct"]
        self._values["clarity"].config(text=_clarity_stars(cloud))
        self._values["cloud cover"].config(text=f"{cloud}%")


# ─────────────────────────────────────────────────────────────────────────────
#  HypatiaPanel
# ─────────────────────────────────────────────────────────────────────────────

class HypatiaPanel(Card):
    # Hover tooltips (Virgil) for the four sky presets: one glossary entry
    # says what all four do (CONVENTIONS §7). Keys are the preset names in
    # self.tip_targets.
    _TIPS = {name: virgil.term("sky_preset") for name, _text in _PRESET_LABELS}

    def __init__(self, parent):
        super().__init__(parent, "Hypatia — Celestarium", C["amber"])

        self._last: "dict | None" = None
        self._selected: "str | None" = None
        self._selected_planet: "str | None" = None
        self._const_by_abbr: dict = {}
        self._planet_by_name: dict = {}
        self._list_rows: dict = {}
        self._preset_lbls: dict = {}
        self._list_built = False
        self._show_constellations = True
        self._cx = self._cy = self._R = 0.0

        main_row = tk.Frame(self.body, bg=C["card"])
        main_row.pack(fill="both", expand=True)

        # ── Left: canvas + status + conditions ─────────────────────────────
        left_col = tk.Frame(main_row, bg=C["card"])
        left_col.pack(side="left", fill="both", expand=True)

        # The conditions and the status line are packed FIRST, at the bottom,
        # and the map takes what is left (§7: what must stay visible is never
        # what gets squeezed). Packed after the map, the fourth conditions
        # row (aurora) fell off the bottom at the 760 x 500 minimum window.
        self.conditions = ConditionsWidget(left_col)
        self.conditions.pack(side="bottom", fill="x", anchor="w")
        self._status_lbl = tk.Label(left_col, text="fetching...", font=FONTS["tiny"],
                                    fg=C["text3"], bg=C["card"], anchor="w")
        self._status_lbl.pack(side="bottom", fill="x", pady=(4, 4))

        canvas_frame = tk.Frame(left_col, bg=C["card"])
        canvas_frame.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(canvas_frame, bg=C["card"], highlightthickness=0,
                                 width=1, height=1)
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        for _name in _PLANET_NAMES:
            self._canvas.tag_bind(_name, "<Button-1>", lambda e, n=_name: self._on_planet_click(n))


        # ── Right: preset row, constellation list, info box ────────────────
        # Fixed-width column. Fonts grow up to 3x when the window is maximized
        # (theme.rescale_fonts), so this width must grow in lockstep or the
        # constellation names and the "Show Constellations" toggle clip on the
        # right edge — felhaven._apply_scale calls self.rescale() to keep pace.
        self._right_col_base_w = 200
        right_col = tk.Frame(main_row, bg=C["card"], width=self._right_col_base_w)
        right_col.pack(side="left", fill="y", padx=(10, 0))
        right_col.pack_propagate(False)
        self._right_col = right_col

        self._build_constellations_toggle(right_col)
        self._build_preset_row(right_col)

        self._list = _ScrollFrame(right_col)
        self._list.pack(fill="both", expand=True, pady=(4, 6))

        # Info box (constellation name + mythology from hypatia_lore.json) —
        # a fixed-height viewport with its own scrollbar. Some blurbs run
        # long; without a bounded height + scroll, the last packed widget in
        # this column just gets squeezed by the expanding list above it and
        # the tail of the text is silently clipped with no way to reach it.
        info_frame = tk.Frame(right_col, bg=C["card"], height=120)
        info_frame.pack(fill="x")
        info_frame.pack_propagate(False)
        self._info_scroll = _ScrollFrame(info_frame)
        self._info_scroll.pack(fill="both", expand=True)

        self._info_name_lbl = tk.Label(self._info_scroll.inner, text="",
                                       font=FONTS["small_bold"], fg=C["text1"],
                                       bg=C["card"], anchor="w", justify="left")
        self._info_name_lbl.pack(fill="x", anchor="w")
        self._info_scroll.add_wrap_label(self._info_name_lbl, pad=16)

        self._info_body_lbl = tk.Label(self._info_scroll.inner, text="",
                                       font=FONTS["small"], fg=C["text2"],
                                       bg=C["card"], anchor="w", justify="left")
        self._info_body_lbl.pack(fill="x", anchor="w")
        self._info_scroll.add_wrap_label(self._info_body_lbl, pad=16)

    # ── responsive width ────────────────────────────────────────────────────

    def rescale(self, scale: float) -> None:
        """Grow the fixed-width right column in lockstep with the fonts, the same
        way Sidebar.rescale does. Without this the column stays 200px while the
        text scales up to 3x on a maximized window and clips. Called from
        felhaven._apply_scale with theme.rescale_fonts's returned scale factor."""
        self._right_col.configure(
            width=int(round(self._right_col_base_w * scale)))

    # ── constellations toggle ───────────────────────────────────────────────

    def _build_constellations_toggle(self, parent: tk.Frame) -> None:
        self._const_toggle_lbl = tk.Label(
            parent, text="", font=FONTS["tiny"], fg=C["text1"], bg=C["card"],
            cursor="hand2", anchor="w",
        )
        self._const_toggle_lbl.pack(fill="x", pady=(0, 6))
        self._const_toggle_lbl.bind("<Button-1>", lambda e: self._on_toggle_constellations())
        self._update_const_toggle_label()

    def _update_const_toggle_label(self) -> None:
        glyph = "☑" if self._show_constellations else "☐"
        fg = C["text1"] if self._show_constellations else C["text3"]
        self._const_toggle_lbl.config(text=f"{glyph} Show Constellations", fg=fg)

    def _on_toggle_constellations(self) -> None:
        self._show_constellations = not self._show_constellations
        self._update_const_toggle_label()
        if self._last is not None:
            self._redraw()

    # ── preset row ──────────────────────────────────────────────────────────
    # A 2x2 grid, not a single row — four labels ("Current"/"N Pole"/"Equator"/
    # "S Pole") need ~254px in one row at their 12px gaps (measured
    # 2026-11-20), wider than the 200px right column, which silently clipped
    # "S Pole" off the visible area.

    def _build_preset_row(self, parent: tk.Frame) -> None:
        row = tk.Frame(parent, bg=C["card"])
        row.pack(fill="x", pady=(0, 6))
        for i, (name, text) in enumerate(_PRESET_LABELS):
            lbl = tk.Label(row, text=text, font=FONTS["tiny"], fg=C["text2"],
                           bg=C["card"], cursor="hand2", anchor="w")
            lbl.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 12), pady=1)
            lbl.bind("<Button-1>", lambda e, n=name: self._on_preset_click(n))
            lbl.bind("<Enter>", lambda e, l=lbl: l.config(fg=C["amber"]))
            lbl.bind("<Leave>", lambda e, n=name, l=lbl: self._unhover_preset(l, n))
            self._preset_lbls[name] = lbl
            virgil_tooltip.attach(lbl, self._TIPS[name])
        self.tip_targets: dict[str, tk.Misc] = dict(self._preset_lbls)

    def _unhover_preset(self, lbl: tk.Label, name: str) -> None:
        active = self._last["preset"] if self._last else "current"
        lbl.config(fg=C["text1"] if name == active else C["text2"])

    def _update_preset_row(self, preset: str) -> None:
        for name, lbl in self._preset_lbls.items():
            lbl.config(fg=C["text1"] if name == preset else C["text2"])

    def _on_preset_click(self, name: str) -> None:
        self.update(hypatia.set_preset(name))

    # ── constellation list ─────────────────────────────────────────────────

    def _build_list(self, constellations: list) -> None:
        for con in constellations:
            abbr = con["abbr"]
            row = tk.Frame(self._list.inner, bg=C["card"])
            row.pack(fill="x", pady=1)
            lbl = tk.Label(row, text=con["name"], font=FONTS["small"], fg=C["text2"],
                           bg=C["card"], anchor="w", justify="left", cursor="hand2")
            lbl.pack(fill="x")
            self._list.add_wrap_label(lbl, pad=16)
            lbl.bind("<Button-1>", lambda e, a=abbr: self._on_const_click(a))
            lbl.bind("<Enter>", lambda e, l=lbl: l.config(fg=C["amber"]))
            lbl.bind("<Leave>", lambda e, l=lbl, a=abbr: l.config(
                fg=C["text1"] if self._selected == a else C["text2"]))
            self._list_rows[abbr] = lbl
            # Canvas items carrying this abbr as a tag route clicks here too —
            # tag_bind attaches to the tag name on the canvas widget itself,
            # so registering it once (here) covers every future redraw.
            self._canvas.tag_bind(abbr, "<Button-1>", lambda e, a=abbr: self._on_const_click(a))

    def _update_list_active(self) -> None:
        for abbr, lbl in self._list_rows.items():
            lbl.config(fg=C["text1"] if self._selected == abbr else C["text2"])

    def _on_const_click(self, abbr: str) -> None:
        self._selected = None if self._selected == abbr else abbr
        if self._selected is not None:
            self._selected_planet = None   # info box shows one thing at a time
        self._update_list_active()
        self._render_info()
        self._apply_highlight()

    def _on_planet_click(self, name: str) -> None:
        self._selected_planet = None if self._selected_planet == name else name
        if self._selected_planet is not None:
            self._selected = None
            self._update_list_active()
            self._apply_highlight()
        self._render_info()

    def _render_info(self) -> None:
        if self._selected_planet is not None:
            planet = self._planet_by_name.get(self._selected_planet)
            if planet is not None:
                self._info_name_lbl.config(text=f"{planet['glyph']} {planet['name']}")
                self._info_body_lbl.config(
                    text=f"{planet['alt']:.0f}° above the horizon, {hypatia.compass(planet['az'])}")
                self._info_scroll.scroll_to_top()
                return
            self._selected_planet = None   # planet dropped out of this tick's list

        if self._selected is None:
            self._info_name_lbl.config(text="")
            self._info_body_lbl.config(text="")
            return
        con = self._const_by_abbr.get(self._selected, {})
        self._info_name_lbl.config(text=con.get("name", ""))
        self._info_body_lbl.config(text=con.get("lore", ""))
        self._info_scroll.scroll_to_top()   # start a new blurb at the top

    # ── canvas resize ───────────────────────────────────────────────────────

    def _on_canvas_configure(self, event) -> None:
        size = min(event.width, event.height)
        self._cx = event.width / 2
        self._cy = event.height / 2
        self._R = max(10.0, size / 2 - 22)
        if self._last is not None:
            self._redraw()

    # ── drawing ─────────────────────────────────────────────────────────────

    def _project(self, alt: float, az: float) -> tuple:
        r = self._R * (90.0 - alt) / 90.0
        x = self._cx + r * math.sin(math.radians(az))
        y = self._cy - r * math.cos(math.radians(az))
        return x, y

    def _redraw(self) -> None:
        data = self._last
        c = self._canvas
        c.delete("all")
        cx, cy, R = self._cx, self._cy, self._R
        if R <= 0:
            return

        simulated = data["preset"] != "current"
        sim_steps = 1 if simulated else 0
        border_color = C["text3"] if simulated else C["border"]

        # ── chart frame ──────────────────────────────────────────────────
        c.create_oval(cx - R, cy - R, cx + R, cy + R, outline=border_color, width=2)
        r45 = R * (90.0 - 45.0) / 90.0
        c.create_oval(cx - r45, cy - r45, cx + r45, cy + r45,
                      outline=_ramp_color("text3", sim_steps), width=1)
        c.create_line(cx, cy - 4, cx, cy + 4, fill=_ramp_color("text3", sim_steps))
        c.create_line(cx - 4, cy, cx + 4, cy, fill=_ramp_color("text3", sim_steps))
        for label, dx, dy in (("N", 0, -1), ("E", 1, 0), ("S", 0, 1), ("W", -1, 0)):
            c.create_text(cx + dx * (R + 12), cy + dy * (R + 12), text=label,
                          fill=_ramp_color("text2", sim_steps), font=FONTS["tiny"])

        stars = data["stars"]

        # HIP -> set(abbr) index, built fresh each redraw (cheap at ~1000 items).
        hip_to_abbrs: dict = {}
        for con in data["constellations"]:
            abbr = con["abbr"]
            for a, b in con["lines"]:
                hip_to_abbrs.setdefault(a, set()).add(abbr)
                hip_to_abbrs.setdefault(b, set()).add(abbr)

        # ── constellation lines (first, so stars draw on top) ─────────────
        # Gated by the "Show Constellations" toggle — stars still carry their
        # abbr tags either way, so map/list selection keeps working with the
        # lines hidden.
        if self._show_constellations:
            for con in data["constellations"]:
                abbr = con["abbr"]
                for a, b in con["lines"]:
                    sa, sb = stars.get(a), stars.get(b)
                    if not sa or not sb or sa["alt"] <= 0 or sb["alt"] <= 0:
                        continue
                    x1, y1 = self._project(sa["alt"], sa["az"])
                    x2, y2 = self._project(sb["alt"], sb["az"])
                    c.create_line(x1, y1, x2, y2,
                                  fill=_ramp_color(_NEUTRAL["line"], sim_steps),
                                  width=1, tags=("line", abbr))

        # ── stars ───────────────────────────────────────────────────────────
        for hip, s in stars.items():
            if s["alt"] <= 0:
                continue
            x, y = self._project(s["alt"], s["az"])
            mag = s["mag"]
            radius = 3 if mag <= 1.0 else (2 if mag <= 2.5 else 1)
            tier_tag = "starbright" if mag <= 2.5 else "stardim"
            fill = _ramp_color(_NEUTRAL[tier_tag], sim_steps)
            tags = ("star", tier_tag, *hip_to_abbrs.get(hip, ()))
            c.create_oval(x - radius, y - radius, x + radius, y + radius,
                          fill=fill, outline="", tags=tags)

        # ── planets — the five classical naked-eye planets from kepler.py ──
        for p in data.get("planets", []):
            if p.get("alt", -1) <= 0:
                continue
            x, y = self._project(p["alt"], p["az"])
            # Amber is the neutral (unselected) planet color, same family as
            # the ring border — both dim into the green ramp under
            # simulation rather than staying eternally bright, since planets
            # follow the same "the whole panel dims" rule as everything else.
            fill = _ramp_color("text2", sim_steps) if simulated else C["amber"]
            c.create_text(x, y, text=p.get("glyph", "?"), fill=fill,
                          font=FONTS["small"], tags=("planet", p.get("name", "")))

        self._apply_highlight()

    def _apply_highlight(self) -> None:
        if self._last is None:
            return
        c = self._canvas
        simulated = self._last["preset"] != "current"
        sim_steps = 1 if simulated else 0
        sel = self._selected

        # Neutral first, every time: a selection that was just cleared (a
        # second click, or a planet picked) must not stay lit until the next
        # redraw, which can be a minute away.
        for tag, key in _NEUTRAL.items():
            c.itemconfigure(tag, fill=_ramp_color(key, sim_steps))
        c.itemconfigure("line", width=1)
        if sel is None:
            return

        for tag, key in _NEUTRAL.items():
            c.itemconfigure(f"{tag}&&!{sel}", fill=_ramp_color(key, sim_steps + 1))
        c.itemconfigure(f"line&&{sel}", fill=C["green"], width=2)
        c.itemconfigure(f"star&&{sel}", fill=C["amber"])

    # ── Kairos contract ─────────────────────────────────────────────────────

    def update(self, data) -> None:
        if data is None:
            self._status_lbl.config(text="stale", fg=C["text3"])
            return

        self._last = data
        if not self._list_built:
            self._build_list(data["constellations"])
            self._list_built = True
        self._const_by_abbr = {c["abbr"]: c for c in data["constellations"]}
        self._planet_by_name = {p["name"]: p for p in data.get("planets", [])}

        self._redraw()
        self._update_preset_row(data["preset"])
        self._render_info()   # refresh live alt/az if a planet is selected

        simulated = data["preset"] != "current"
        self.conditions.set_simulated(simulated)
        if simulated:
            self._status_lbl.config(
                text=_SIM_NOTICE.get(data["preset"], "SIMULATED SKY"), fg=C["amber"])
        else:
            ts = datetime.fromtimestamp(data["generated_unix"]).strftime("%H:%M")
            self._status_lbl.config(text=f"refreshed {ts}", fg=C["text3"])
