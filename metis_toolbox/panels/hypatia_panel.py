"""
Hypatia panel — zenith star map ("Celestarium") for the Felhaven dashboard.

HypatiaPanel draws an all-sky zenith projection (zenith at center, horizon at
the ring, North up) from the tools/hypatia.fetch() payload. A constellation
list lets you highlight a shape and read its mythology; a latitude preset row
(Current / N Pole / Equator / S Pole) is a teaching-mode jump that dims the
whole panel and shows a simulation notice, since the chart is then showing a
sky this machine isn't actually under. ConditionsWidget is a small embedded
sub-widget (bottom-left, under the canvas) that rides the `aura` worker —
same AetherWidget precedent as Hephaestus's connectivity rows — showing
cloud-cover-derived "clarity" stars, cloud %, and moon illumination %.

The panel does no astronomy: RA/Dec -> alt/az lives in tools/hypatia.py. The
only math here is alt/az -> canvas x/y (azimuthal equidistant projection),
plus small display lookups (cloud-cover -> star glyphs, a fixed dim ramp for
the simulation/selection color states).
"""

import math
import tkinter as tk
from datetime import datetime

from theme import C, FONTS, Card, PhosphorScroll

from tools import hypatia


# ─────────────────────────────────────────────────────────────────────────────
#  Dimming ramp — a fixed order down the phosphor palette, shared by the
#  simulation-mode dim and the "non-member constellation" dim so the two
#  effects stack instead of needing separate color tables.
# ─────────────────────────────────────────────────────────────────────────────

_RAMP_KEYS = ["text1", "text2", "text3", "bar_bg"]


def _ramp_color(base_key: str, dim_steps: int) -> str:
    idx = min(_RAMP_KEYS.index(base_key) + max(0, dim_steps), len(_RAMP_KEYS) - 1)
    return C[_RAMP_KEYS[idx]]


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

_COMPASS_16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
               "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def _compass(az: float) -> str:
    return _COMPASS_16[int(((az % 360.0) + 11.25) // 22.5) % 16]


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

    def clear(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()
        self._wrap_labels.clear()
        self._canvas.yview_moveto(0)


# ─────────────────────────────────────────────────────────────────────────────
#  ConditionsWidget — observation conditions, embedded bottom-left
# ─────────────────────────────────────────────────────────────────────────────

class ConditionsWidget(tk.Frame):
    """
    Rides the `aura` worker (AetherWidget precedent — an embedded widget with
    its own Kairos registration). update(data) receives the FULL aura
    payload (or None); missing keys / an {"error": ...} dict all degrade to
    "-", never a crash. Collapses to a one-line local-only notice while
    Hypatia is showing a simulated (non-current) sky.
    """

    _ROWS = ("clarity", "cloud cover", "moon illum.")

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

        data = self._last
        if not data or "error" in data or "cloud_cover_pct" not in data:
            for v in self._values.values():
                v.config(text="—")
            return

        cloud = data.get("cloud_cover_pct", 0)
        self._values["clarity"].config(text=_clarity_stars(cloud))
        self._values["cloud cover"].config(text=f"{cloud}%")
        moon = (data.get("astronomy") or {}).get("moon_illumination", "")
        self._values["moon illum."].config(text=f"{moon}%" if moon not in (None, "") else "—")


# ─────────────────────────────────────────────────────────────────────────────
#  HypatiaPanel
# ─────────────────────────────────────────────────────────────────────────────

class HypatiaPanel(Card):
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

        canvas_frame = tk.Frame(left_col, bg=C["card"])
        canvas_frame.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(canvas_frame, bg=C["card"], highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        for _name in _PLANET_NAMES:
            self._canvas.tag_bind(_name, "<Button-1>", lambda e, n=_name: self._on_planet_click(n))

        self._status_lbl = tk.Label(left_col, text="fetching...", font=FONTS["tiny"],
                                    fg=C["text3"], bg=C["card"], anchor="w")
        self._status_lbl.pack(fill="x", pady=(4, 4))

        self.conditions = ConditionsWidget(left_col)
        self.conditions.pack(fill="x", anchor="w")

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
    # "S Pole") plus separators need ~257px in one row, wider than the 200px
    # right column, which silently clipped "S Pole" off the visible area.

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
                    text=f"{planet['alt']:.0f}° above the horizon, {_compass(planet['az'])}")
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
                    c.create_line(x1, y1, x2, y2, fill=_ramp_color("text2", sim_steps),
                                  width=1, tags=("line", abbr))

        # ── stars ───────────────────────────────────────────────────────────
        for hip, s in stars.items():
            if s["alt"] <= 0:
                continue
            x, y = self._project(s["alt"], s["az"])
            mag = s["mag"]
            radius = 3 if mag <= 1.0 else (2 if mag <= 2.5 else 1)
            bright = mag <= 2.5
            tier_tag = "starbright" if bright else "stardim"
            fill = _ramp_color("text1" if bright else "text2", sim_steps)
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

        if sel is None:
            return  # neutral colors already set by _redraw's creation pass

        c.itemconfigure(f"line&&!{sel}", fill=_ramp_color("text2", sim_steps + 1))
        c.itemconfigure(f"starbright&&!{sel}", fill=_ramp_color("text1", sim_steps + 1))
        c.itemconfigure(f"stardim&&!{sel}", fill=_ramp_color("text2", sim_steps + 1))
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
