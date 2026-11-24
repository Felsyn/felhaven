"""
Aura panel — weather display for the Felhaven dashboard.

Job:         Show the weather: conditions now, the 3-day forecast, and the warnings.

WeatherPanel — three tabs, updated by Kairos every 30 min via update():
    NOW       current temp + description + details (feels-like, H/L, wind,
              humidity, rain chance, UV), plus two collapsible sub-widgets,
              each fed by its own Kairos worker rather than by this panel:
                  HeliosWidget — sun  (sunrise, sunset, golden hours, day length)
                  SeleneWidget — moon (phase, illumination, moonrise, moonset)
              and, above the temperature while a warning or watch is active,
              AlertBanner (panels/iris_panel.py); below Selene, the SPACE
              WEATHER fold row, SpaceWeatherWidget (panels/eos_panel.py), on
              its own "eos" worker as `space_widget`.
    FORECAST  3-day outlook — emoji icon + description + H/L + chance of rain
              or snow.
    ALERTS    the Weather Service's warnings: AlertsTab (panels/iris_panel.py).
              The banner and the tab are public (`alert_banner`, `alerts_tab`)
              and fed by AlertWatch on Kairos's "iris" worker, not by this
              panel's update(), the Helios/Selene precedent (felhaven.py).

PlaceBar — the PLACE dropdown at the right of the card's header line: Home, or
    a cached Artemis area. A pick is saved at once (themis.save_atmos_place)
    and refetches "aura", whose dict then carries the place's reading under
    "picked" (tools/aura.py). Only NOW's conditions and FORECAST follow it;
    while another place shows, a dim line on NOW and on ALERTS says that the
    warnings, sun, moon and space weather stay on Home. SETTINGS mounts a
    second PlaceBar on the same setting ("Atmospherics opens on").

Module-level _icon() maps wttr.in (WWO) weather codes to emoji for the forecast.
Aura's Open-Meteo fallback translates its own WMO codes into that same WWO space
before handing the dict over, so this panel never learns which host answered.

Upstream:    felhaven.py (builds WeatherPanel, registers it, its PlaceBar and
             its sub-widgets with Kairos, and hands the warning displays to
             AlertWatch), panels/themis_panel.py (SETTINGS' second PlaceBar).
Downstream:  tools/aura.py (the data's shape: Kairos hands update() its
             fetch(), so nothing here imports it), tools/helios.py and
             tools/selene.py (the same, for the two sub-widgets, except that
             HeliosWidget imports helios for its two polar reasons), themis.py
             (the temperature unit at each render, the PLACE pick and Home's
             coordinates), tools/artemis.py (the cached areas PLACE offers),
             panels/iris_panel.py (AlertBanner, AlertsTab),
             panels/eos_panel.py (SpaceWeatherWidget), theme.py (colours,
             fonts, Card, ScrollColumn), tools/virgil.py and
             panels/virgil_tooltip.py (the tooltips).

2026-07-25: walkthrough audit — three class-scoped tags then, as three
       sessions shared the file. WeatherPanel: update() handles None and the
       full dict; _icon() and _temp_color() verified. HeliosWidget and
       SeleneWidget: None reads "unavailable", an empty illumination "—". No
       code change.

2026-09-15: the None branch says "no weather source reachable" rather than
       "fetch failed" — with a fallback source in place, reaching it means both
       hosts are down, which is a different (and rarer) thing to tell a user.

2026-09-28: the sun and moon are computed, not fetched (V0.03 audit M3). The
       sub-widgets are exposed as `helios_widget` / `selene_widget` and
       registered with Kairos directly (felhaven.py). WeatherPanel no longer
       feeds them, so a weather outage, or Aura's moonless Open-Meteo
       fallback, leaves both on screen.

2026-11-10: the PLACE dropdown (PlaceBar). See CONVENTIONS §12.

2026-11-10: hover tooltips (Virgil) on every tab, fold header and readout:
       the `_TAB_TIPS`, `_HEADER_TIP` and `_TIPS` tables, held to their
       widgets by tests/test_virgil_surfaces.py.

V0.03:       Walked through 2026-11-17 (the V0.03 walkthrough, CONVENTIONS
             §3), the whole file (S21 re-reads the sun and moon widgets
             against their tools). No threads or timers, colours from the
             palette, every tip in a table, the unit read at each render. The
             three class tags folded into the 2026-07-25 paragraph, and the
             widgets' notes that repeated the 2026-09-28 one (naming the
             retired get_sun_times and get_moon_phase) went. WeatherPanel's
             docstring said two tabs and no scrolling; Upstream and
             Downstream were missing. S21 re-read the two widgets on
             2026-11-20: on a polar day or night HeliosWidget showed
             "unavailable" and threw Helios's reason away, so it shows the
             reason now; a comment still named the retired interpret().
"""

import logging
import tkinter as tk
from datetime import datetime
from typing import Callable

import themis
from panels import virgil_tooltip
from panels.eos_panel import SpaceWeatherWidget
from panels.iris_panel import AlertBanner, AlertsTab
from theme import C, FONTS, Card, ScrollColumn
from tools import artemis, helios, virgil

log = logging.getLogger("METIS.aura_panel")


# ─────────────────────────────────────────────────────────────────────────────
#  Weather-code → emoji  (display concern: a lookup feeding a Label, not logic)
#  Keys are WWO condition codes (wttr.in's upstream). Unknown code → 🌡️.
# ─────────────────────────────────────────────────────────────────────────────

_ICONS = {
    "☀️": {113},
    "⛅": {116},
    "☁️": {119, 122},
    "🌫️": {143, 248, 260},
    "🌦️": {176, 263, 266, 293, 296, 353},
    "🌧️": {299, 302, 305, 308, 311, 314, 356, 359, 281, 284},
    "🌨️": {179, 182, 185, 227, 230, 317, 320, 323, 326, 350,
            362, 365, 368, 374, 377},
    "❄️": {329, 332, 335, 338, 371},
    "⛈️": {200, 386, 389, 392, 395},
}
_CODE_TO_ICON = {code: icon for icon, codes in _ICONS.items() for code in codes}


def _icon(code: int) -> str:
    return _CODE_TO_ICON.get(code, "🌡️")


# NOW-temperature color tiers (°F). The theme is monochrome apart from red and
# amber, so we grade by SEVERITY, not by hot/cold hue: red is the theme's alarm
# color, reserved for genuinely dangerous heat OR cold; amber flags a merely hot
# or cold day; the comfortable band in between keeps the normal bright phosphor.
_HOT_ALARM_F  = 95    # >= this  → red   (dangerous heat)
_HOT_WARN_F   = 80    # >= this  → amber (hot)
_COLD_WARN_F  = 40    # <= this  → amber (cold)
_COLD_ALARM_F = 20    # <= this  → red   (dangerous cold)


def _temp_color(temp_f: int) -> str:
    if temp_f >= _HOT_ALARM_F or temp_f <= _COLD_ALARM_F:
        return C["red"]
    if temp_f >= _HOT_WARN_F or temp_f <= _COLD_WARN_F:
        return C["amber"]
    return C["text1"]


# ─────────────────────────────────────────────────────────────────────────────
#  HeliosWidget — sun timing sub-widget
# ─────────────────────────────────────────────────────────────────────────────

class HeliosWidget(tk.Frame):
    """
    Solar timing sub-widget inside WeatherPanel — sunrise, sunset, golden
    hours, day length. Collapsed by default; toggled with ▶/▼ like Ammit.
    Pure display: the sun is computed in tools/helios.py, and Kairos's helios
    worker delivers that answer here (registered in felhaven.py).
    """

    ROWS = ("sunrise", "sunset", "golden AM", "golden PM", "day length")

    # Hover tooltips (Virgil, CONVENTIONS §7): the header names its module, so
    # it shows Helios's own gloss; each row says what it means.
    _HEADER_TIP = virgil.module("helios")
    _TIPS = {
        "sunrise": virgil.term("sun_times"),
        "sunset": virgil.term("sun_times"),
        "golden AM": virgil.term("golden_hour"),
        "golden PM": virgil.term("golden_hour"),
        "day length": virgil.term("day_length"),
    }

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        # Divider
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", pady=(8, 6))

        # Section header row with toggle
        header = self.header = tk.Frame(self, bg=C["card"], cursor="hand2")
        header.pack(fill="x")
        title_lbl = tk.Label(header, text="HELIOS — SUN", font=FONTS["card_header"],
                             fg=C["text3"], bg=C["card"], anchor="w", cursor="hand2")
        title_lbl.pack(side="left")

        self._toggle_lbl = tk.Label(header, text="▶", font=FONTS["card_header"],
                                     fg=C["text3"], bg=C["card"], cursor="hand2")
        self._toggle_lbl.pack(side="right")

        # Whole header row toggles, not just the arrow (sidebar.py's bind-all pattern).
        for w in (header, title_lbl, self._toggle_lbl):
            w.bind("<Button-1>", lambda e: self._section_toggle())
        self._toggle_lbl.bind("<Enter>", lambda e: self._toggle_lbl.config(fg=C["text1"]))
        self._toggle_lbl.bind("<Leave>", lambda e: self._toggle_lbl.config(fg=C["text3"]))

        # Content frame — collapsed by default; do NOT pack here.
        self._body = tk.Frame(self, bg=C["card"])
        self._collapsed = True

        # Shown in place of the rows when the reading is an error or None:
        # Helios's own reason on a polar day or night, else "unavailable".
        self._unavail = tk.Label(self._body, text="unavailable", font=FONTS["small"],
                                  fg=C["text3"], bg=C["card"], anchor="w")

        # Key-left / value-right detail rows (same pattern as WeatherPanel).
        self._rows = tk.Frame(self._body, bg=C["card"])
        self._rows.pack(fill="x")
        self._values = {}
        self.tip_targets: dict[str, tk.Misc] = {}
        for key in self.ROWS:
            row = self.tip_targets[key] = tk.Frame(self._rows, bg=C["card"])
            row.pack(fill="x")
            tk.Label(row, text=key, font=FONTS["small"], fg=C["text2"],
                     bg=C["card"], anchor="w").pack(side="left")
            v = tk.Label(row, text="—", font=FONTS["small"], fg=C["text1"],
                         bg=C["card"], anchor="e")
            v.pack(side="right")
            self._values[key] = v
        virgil_tooltip.attach(self.header, self._HEADER_TIP)
        for key, tip in self._TIPS.items():
            virgil_tooltip.attach(self.tip_targets[key], tip)

    def _section_toggle(self):
        if self._collapsed:
            self._body.pack(fill="x")
            self._toggle_lbl.config(text="▼")
            self._collapsed = False
        else:
            self._body.pack_forget()
            self._toggle_lbl.config(text="▶")
            self._collapsed = True

    def update(self, info: dict | None) -> None:
        """Kairos's helios worker delivers helios.fetch(): the same dict
        get_sky_report hands the model. A polar day or night shows Helios's
        reason ("the sun does not set today") in place of the rows; any other
        error, or None, reads "unavailable"."""
        if not info or "error" in info:
            reason = str(info["error"]).partition(": ")[2] if info else ""
            polar = reason in (helios.NEVER_RISES, helios.NEVER_SETS)
            self._unavail.config(text=reason if polar else "unavailable")
            self._rows.pack_forget()
            self._unavail.pack(fill="x")
            return
        self._unavail.pack_forget()
        self._rows.pack(fill="x")
        self._values["sunrise"].config(text=info["sunrise"])
        self._values["sunset"].config(text=info["sunset"])
        self._values["golden AM"].config(text=info["golden_am"])
        self._values["golden PM"].config(text=info["golden_pm"])
        self._values["day length"].config(text=info["day_length"])


# ─────────────────────────────────────────────────────────────────────────────
#  SeleneWidget — moon phase sub-widget
# ─────────────────────────────────────────────────────────────────────────────

class SeleneWidget(tk.Frame):
    """
    Lunar sub-widget inside WeatherPanel — phase glyph + name, illumination,
    moonrise, moonset. Collapsed by default; toggled with ▶/▼ like Ammit.
    Pure display: the moon is computed in tools/selene.py, and Kairos's
    selene worker delivers that answer here (registered in felhaven.py).
    """

    ROWS = ("illumination", "moonrise", "moonset")

    # Hover tooltips (Virgil, CONVENTIONS §7), as in HeliosWidget.
    _HEADER_TIP = virgil.module("selene")
    _TIPS = {
        "phase": virgil.term("moon_phase"),
        "illumination": virgil.term("moon_illumination"),
        "moonrise": virgil.term("moon_times"),
        "moonset": virgil.term("moon_times"),
    }

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        # Divider
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", pady=(8, 6))

        # Section header row with toggle
        header = self.header = tk.Frame(self, bg=C["card"], cursor="hand2")
        header.pack(fill="x")
        title_lbl = tk.Label(header, text="SELENE — MOON", font=FONTS["card_header"],
                             fg=C["text3"], bg=C["card"], anchor="w", cursor="hand2")
        title_lbl.pack(side="left")

        self._toggle_lbl = tk.Label(header, text="▶", font=FONTS["card_header"],
                                     fg=C["text3"], bg=C["card"], cursor="hand2")
        self._toggle_lbl.pack(side="right")

        # Whole header row toggles, not just the arrow (sidebar.py's bind-all pattern).
        for w in (header, title_lbl, self._toggle_lbl):
            w.bind("<Button-1>", lambda e: self._section_toggle())
        self._toggle_lbl.bind("<Enter>", lambda e: self._toggle_lbl.config(fg=C["text1"]))
        self._toggle_lbl.bind("<Leave>", lambda e: self._toggle_lbl.config(fg=C["text3"]))

        # Content frame — collapsed by default; do NOT pack here.
        self._body = tk.Frame(self, bg=C["card"])
        self._collapsed = True

        self._unavail = tk.Label(self._body, text="unavailable", font=FONTS["small"],
                                  fg=C["text3"], bg=C["card"], anchor="w")

        self._content = tk.Frame(self._body, bg=C["card"])
        self._content.pack(fill="x")

        # Phase line is its own label: "🌘 Waning Crescent".
        self._phase_lbl = tk.Label(self._content, text="—", font=FONTS["small"],
                                   fg=C["text1"], bg=C["card"], anchor="w")
        self._phase_lbl.pack(fill="x")

        # Remaining rows use the key-left / value-right detail pattern.
        self._values = {}
        self.tip_targets: dict[str, tk.Misc] = {"phase": self._phase_lbl}
        for key in self.ROWS:
            row = self.tip_targets[key] = tk.Frame(self._content, bg=C["card"])
            row.pack(fill="x")
            tk.Label(row, text=key, font=FONTS["small"], fg=C["text2"],
                     bg=C["card"], anchor="w").pack(side="left")
            v = tk.Label(row, text="—", font=FONTS["small"], fg=C["text1"],
                         bg=C["card"], anchor="e")
            v.pack(side="right")
            self._values[key] = v
        virgil_tooltip.attach(self.header, self._HEADER_TIP)
        for key, tip in self._TIPS.items():
            virgil_tooltip.attach(self.tip_targets[key], tip)

    def _section_toggle(self):
        if self._collapsed:
            self._body.pack(fill="x")
            self._toggle_lbl.config(text="▼")
            self._collapsed = False
        else:
            self._body.pack_forget()
            self._toggle_lbl.config(text="▶")
            self._collapsed = True

    def update(self, info: dict | None) -> None:
        """Kairos's selene worker delivers selene.fetch(): the same dict
        get_sky_report hands the model. An error or None reads "unavailable".
        A day with no moonrise or moonset already arrives as "—"."""
        if not info or "error" in info:
            self._content.pack_forget()
            self._unavail.pack(fill="x")
            return
        self._unavail.pack_forget()
        self._content.pack(fill="x")
        self._phase_lbl.config(text=f"{info['emoji']} {info['phase']}")
        self._values["illumination"].config(text=info["illumination"])
        self._values["moonrise"].config(text=info["moonrise"])
        self._values["moonset"].config(text=info["moonset"])


# ─────────────────────────────────────────────────────────────────────────────
#  WeatherPanel
# ─────────────────────────────────────────────────────────────────────────────

class PlaceBar(tk.Frame):
    """PLACE: Home, or a cached Artemis area, for Atmospherics' NOW and
    FORECAST. MACHINE SPIRIT's _ModelBar styling. The list is rebuilt each
    time the menu opens, so an area ingested, renamed or recycled in the
    Cartograph shows up without a poll.

    A pick is saved at once and asks Kairos for a fresh "aura" reading. The
    bar always shows what Themis holds, so the two bars (Atmospherics' and
    SETTINGS') agree once either one's pick has been delivered."""

    HOME = ""
    # An area this close to Home is Home: the same weather, and no second fetch.
    FOLD_MI = 0.6

    def __init__(self, parent: tk.Misc, *, caption: str = "PLACE",
                 on_pick: "Callable[[str, str], None] | None" = None) -> None:
        super().__init__(parent, bg=C["card"])
        self._on_pick = on_pick
        self._refetch: Callable[..., None] = lambda *names: None
        self._choices: list[tuple[str, str]] = []      # (area, label)
        if caption:
            tk.Label(self, text=caption, font=FONTS["tiny"], fg=C["text3"],
                     bg=C["card"], anchor="w").pack(side="left", padx=(0, 6))
        self._var = tk.StringVar(value="Home")
        self._menu = tk.OptionMenu(self, self._var, "Home")
        self._menu.config(font=FONTS["small"], bg=C["bar_bg"], fg=C["text1"],
                          activebackground=C["border"], activeforeground=C["text1"],
                          highlightthickness=0, bd=0, width=18, anchor="w")
        self._menu["menu"].config(bg=C["bar_bg"], fg=C["text1"],
                                  postcommand=self.refresh)
        self._menu.pack(side="left")
        self.refresh()

    def set_refetch(self, fn: Callable[..., None]) -> None:
        """felhaven.py passes Kairos.refetch once Kairos exists."""
        self._refetch = fn

    def options(self) -> list[tuple[str, str]]:
        """[(area, label)]: Home first ("Home · <label>" when a cached area sits
        on it), then the other cached areas, nearest Home first. A folder whose
        name is not an area id is never offered: Themis would refuse it."""
        home = "Home"
        rest: list[tuple[str, str]] = []
        rows = artemis.nearest_cached(themis.latitude(), themis.longitude(),
                                      limit=None)
        for row in rows:
            area = str(row["area_id"])
            if not artemis.is_area_id(area):
                continue
            if float(row["distance_mi"]) <= self.FOLD_MI:
                if home == "Home":
                    home = f"Home · {row['label']}"
                continue
            rest.append((area, str(row["label"])))
        return [(self.HOME, home), *rest]

    def refresh(self) -> None:
        """Rebuild the list and show the saved place. A saved place no longer
        listed (recycled in the Cartograph) shows as Home, which is what Aura
        shows in its place."""
        self._choices = self.options()
        menu = self._menu["menu"]
        menu.delete(0, "end")
        for area, label in self._choices:
            menu.add_command(label=label,
                             command=lambda a=area, l=label: self._picked(a, l))
        saved = themis.atmos_place()
        labels = dict(self._choices)
        self._var.set(labels.get(saved, labels[self.HOME]))

    def current(self) -> str:
        return self._var.get()

    def update(self, data: "dict | None") -> None:
        """Kairos "aura" (the SETTINGS bar): show the place now saved."""
        self.refresh()

    def _picked(self, area: str, label: str) -> None:
        self._var.set(label)
        try:
            themis.save_atmos_place(area)
        except (themis.SettingsError, OSError) as exc:
            log.warning("Atmospherics: couldn't save the place picked: %s", exc)
            self.refresh()
            return
        self._refetch("aura")
        if self._on_pick is not None:
            self._on_pick(area, label)


# What NOW and ALERTS say while another place than Home is shown.
_AWAY_NOW = "Warnings, sun, moon and space weather stay on Home."
_AWAY_ALERTS = "Warnings stay on Home, whatever place is picked."
_MISSING = "The place picked is no longer cached in the Cartograph; showing Home."


class WeatherPanel(Card):
    """
    Receives weather data from Kairos via update(). Three tabs (the Pheme
    tab-bar pattern):
        NOW       — current conditions, then the Helios, Selene and space
                    weather fold rows; it scrolls (a ScrollColumn), since with
                    the folds open it outgrows the minimum window
        FORECAST  — 3-day outlook with emoji icons and chance of rain or snow;
                    three rows, so it doesn't scroll
        ALERTS    — Iris's AlertsTab, fed by AlertWatch, not update()
    """

    # Detail rows for the NOW tab. "rain chance" sits after humidity, before UV.
    _DETAIL_KEYS = ("feels like", "H / L", "wind", "humidity", "rain chance", "UV")

    # Hover tooltips (Virgil, CONVENTIONS §7). A tab says who works there (the
    # module's own gloss); a readout says what it means (a glossary entry).
    # tests/test_virgil_surfaces.py holds both tables to their widgets.
    _TAB_TIPS = {
        "NOW": virgil.module("aura"),
        "FORECAST": virgil.module("aura"),
        "ALERTS": virgil.module("iris"),
    }
    _TIPS = {
        "place": virgil.term("atmos_place"),
        "temperature": virgil.term("temperature"),
        "feels like": virgil.term("feels_like"),
        "H / L": virgil.term("high_low"),
        "wind": virgil.term("wind"),
        "humidity": virgil.term("humidity"),
        "rain chance": virgil.term("rain_chance"),
        "UV": virgil.term("uv_index"),
        "day 1": virgil.term("forecast_day"),
        "day 2": virgil.term("forecast_day"),
        "day 3": virgil.term("forecast_day"),
    }

    def __init__(self, parent):
        # Generic title at construction; update() re-titles it to the live
        # weather location (data["location"]) once the first fetch lands, so a
        # Settings-tab location change is reflected without a restart.
        super().__init__(parent, "Atmospherics", C["blue"])
        self.place_bar = PlaceBar(self.header, on_pick=self._on_place_picked)
        # Packed before the title (§7): a long place name is what gets clipped
        # at the minimum window, never the dropdown.
        self.place_bar.pack(side="right", before=self._header_lbl)
        self._shown_area = PlaceBar.HOME      # whose weather NOW shows

        # Tab bar across the top, content area beneath it.
        tab_row = tk.Frame(self.body, bg=C["card"])
        tab_row.pack(fill="x", pady=(6, 0))
        self._content = tk.Frame(self.body, bg=C["card"])
        self._content.pack(fill="both", expand=True)

        self._tabs: dict[str, dict] = {}     # name -> {"tab", "line", "frame"}
        self._active: str | None = None
        # A tab whose label carries its own colour (ALERTS while one is active).
        self._tab_colour: dict[str, str | None] = {}
        for name in ("NOW", "FORECAST", "ALERTS"):
            self._build_tab(tab_row, name)

        self._build_now_tab(self._tabs["NOW"]["frame"])
        self._build_forecast_tab(self._tabs["FORECAST"]["frame"])
        self.alerts_tab = AlertsTab(self._tabs["ALERTS"]["frame"],
                                    on_label=self._set_alerts_label)
        self.alerts_tab.pack(fill="both", expand=True)
        # Packed above the tab while another place than Home is shown.
        self.alerts_away = tk.Label(self._tabs["ALERTS"]["frame"], text=_AWAY_ALERTS,
                                    font=FONTS["small_italic"], fg=C["text3"],
                                    bg=C["card"], anchor="w", justify="left",
                                    wraplength=380)

        self._show_tab("NOW")                # NOW active by default

        for name, tip in self._TAB_TIPS.items():
            virgil_tooltip.attach(self._tabs[name]["tab"], tip)
        self.tip_targets: dict[str, tk.Misc] = {
            "place": self.place_bar, "temperature": self.temp_lbl,
            **self._detail_rows,
            **{f"day {i}": row["row"] for i, row in enumerate(self._forecast_rows, 1)},
        }
        for key, tip in self._TIPS.items():
            virgil_tooltip.attach(self.tip_targets[key], tip)

    # ── tab construction / switching (mirrors PhemePanel) ──────────────────────

    def _build_tab(self, tab_row: tk.Frame, name: str) -> None:
        wrap = tk.Frame(tab_row, bg=C["card"])
        wrap.pack(side="left", padx=(0, 12))
        tab = tk.Label(wrap, text=name, font=FONTS["card_header"],
                       fg=C["text3"], bg=C["card"], cursor="hand2")
        tab.pack()
        line = tk.Frame(wrap, height=1, bg=C["border"])
        line.pack(fill="x")
        tab.bind("<Button-1>", lambda e, n=name: self._show_tab(n))
        frame = tk.Frame(self._content, bg=C["card"])
        self._tabs[name] = {"tab": tab, "line": line, "frame": frame}

    def _show_tab(self, name: str) -> None:
        if self._active == name or name not in self._tabs:
            return
        if self._active is not None:
            prev = self._tabs[self._active]
            prev["frame"].pack_forget()
            prev["tab"].config(fg=self._tab_colour.get(self._active) or C["text3"])
            prev["line"].config(bg=C["border"])
        self._active = name
        cur = self._tabs[name]
        cur["frame"].pack(fill="both", expand=True)
        cur["tab"].config(fg=self._tab_colour.get(name) or C["text1"])
        cur["line"].config(bg=C["blue"])

    def show_tab(self, name: str) -> None:
        """Switch to a tab by name ("NOW", "FORECAST", "ALERTS"): the popup's
        OPEN ALERTS and the banner's "details ›" land here."""
        self._show_tab(name)

    def _set_alerts_label(self, text: str, colour: str | None) -> None:
        """AlertsTab's callback: "ALERTS · 2" in the tier colour, or plain."""
        self._tab_colour["ALERTS"] = colour
        idle = C["text1"] if self._active == "ALERTS" else C["text3"]
        self._tabs["ALERTS"]["tab"].config(text=text, fg=colour or idle)

    # ── NOW tab ────────────────────────────────────────────────────────────────

    def _build_now_tab(self, tab: tk.Frame) -> None:
        # NOW scrolls (§7): with the three fold rows open it is taller than
        # the tab at the 760 x 500 minimum window.
        self.now_column = ScrollColumn(tab)
        self.now_column.pack(fill="both", expand=True)
        parent = self.now_column.body
        # Packed at the very top while another place than Home is shown, or
        # when the place picked has been recycled (see _set_note).
        self.away_lbl = tk.Label(parent, text="", font=FONTS["small_italic"],
                                 fg=C["text3"], bg=C["card"], anchor="w",
                                 justify="left", wraplength=380)
        self.temp_lbl = tk.Label(parent, text="fetching...", font=FONTS["xlarge_bold"],
                                 fg=C["text1"], bg=C["card"], anchor="w")
        self.temp_lbl.pack(fill="x", pady=(6, 0))
        self.desc_lbl = tk.Label(parent, text="", font=FONTS["small"],
                                 fg=C["text2"], bg=C["card"], anchor="w")
        self.desc_lbl.pack(fill="x")
        # Hidden until AlertWatch shows a warning; then it packs above the temperature.
        self.alert_banner = AlertBanner(parent, before=self.temp_lbl)
        self.details_frame = tk.Frame(parent, bg=C["card"])
        self.details_frame.pack(fill="x", pady=(4, 0))
        self._detail_labels = {}
        self._detail_rows: dict[str, tk.Frame] = {}
        for key in self._DETAIL_KEYS:
            row = self._detail_rows[key] = tk.Frame(self.details_frame, bg=C["card"])
            row.pack(fill="x")
            tk.Label(row, text=key, font=FONTS["small"], fg=C["text2"],
                     bg=C["card"], anchor="w").pack(side="left")
            v = tk.Label(row, text="—", font=FONTS["small"], fg=C["text1"],
                         bg=C["card"], anchor="e")
            v.pack(side="right")
            self._detail_labels[key] = v

        # Sun + moon sub-widgets. Public because felhaven.py hands them to
        # Kairos's helios/selene workers (the HypatiaPanel.conditions precedent).
        self.helios_widget = HeliosWidget(parent)
        self.helios_widget.pack(fill="x")
        self.selene_widget = SeleneWidget(parent)
        self.selene_widget.pack(fill="x")
        # Space weather, on Kairos's "eos" worker (registered in felhaven.py).
        self.space_widget = SpaceWeatherWidget(parent)
        self.space_widget.pack(fill="x")

    # ── FORECAST tab ───────────────────────────────────────────────────────────

    def _build_forecast_tab(self, parent: tk.Frame) -> None:
        # Three persistent rows, built once (no destroy-and-rebuild — the row
        # count is fixed, only the text in each row changes per update).
        self._forecast_rows = [self._build_forecast_row(parent) for _ in range(3)]

    def _build_forecast_row(self, parent: tk.Frame) -> dict:
        row = tk.Frame(parent, bg=C["card"])
        row.pack(fill="x", pady=(4, 0))

        # Line 1: day | icon + description ............ high / low
        line1 = tk.Frame(row, bg=C["card"])
        line1.pack(fill="x")
        day = tk.Label(line1, text="—", font=FONTS["small"], fg=C["text1"],
                       bg=C["card"], width=9, anchor="w")
        day.pack(side="left")
        hl = tk.Label(line1, text="—", font=FONTS["small"], fg=C["text1"],
                      bg=C["card"], anchor="e")
        hl.pack(side="right")
        icon = tk.Label(line1, text="", font=FONTS["small"], fg=C["text1"],
                        bg=C["card"], anchor="w")
        icon.pack(side="left", padx=(0, 4))
        desc = tk.Label(line1, text="", font=FONTS["small"], fg=C["text2"],
                        bg=C["card"], anchor="w")
        desc.pack(side="left")

        # Line 2: ............................................ rain/snow %
        line2 = tk.Frame(row, bg=C["card"])
        line2.pack(fill="x")
        precip = tk.Label(line2, text="—", font=FONTS["tiny"], fg=C["text2"],
                          bg=C["card"], anchor="e")
        precip.pack(side="right")

        # Divider between days.
        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", pady=(4, 0))
        return {"row": row, "day": day, "icon": icon, "desc": desc, "hl": hl,
                "precip": precip}

    def _day_label(self, date_str: str, index: int) -> str:
        if index == 0:
            return "Today"
        if index == 1:
            return "Tomorrow"
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").strftime("%A")
        except (ValueError, TypeError):
            return date_str or "—"

    def _precip_text(self, day: dict) -> str:
        rain = day.get("rain_pct", 0)
        snow = day.get("snow_pct", 0)
        if snow > rain:
            return f"snow {snow}%"
        if rain > 0:
            return f"rain {rain}%"
        return "—"

    def _update_forecast(self, forecast: list, suf: str = "f") -> None:
        for i, w in enumerate(self._forecast_rows):
            if i < len(forecast):
                day = forecast[i]
                w["day"].config(text=self._day_label(day.get("date", ""), i))
                w["icon"].config(text=_icon(day.get("weather_code", 0)))
                w["desc"].config(text=day.get("description", ""))
                w["hl"].config(text=f"{day.get(f'high_{suf}', 0)}° / {day.get(f'low_{suf}', 0)}°")
                w["precip"].config(text=self._precip_text(day))
            else:
                # Fewer than 3 days: blank the value fields, clear icon/desc.
                w["day"].config(text="—")
                w["icon"].config(text="")
                w["desc"].config(text="")
                w["hl"].config(text="—")
                w["precip"].config(text="—")

    # ── The place shown ────────────────────────────────────────────────────────

    def set_refetch(self, fn: Callable[..., None]) -> None:
        """felhaven.py passes Kairos.refetch: a PLACE pick refetches "aura"."""
        self.place_bar.set_refetch(fn)

    def _on_place_picked(self, area: str, label: str) -> None:
        """Until the worker answers, say that the new place is on its way."""
        if area != self._shown_area:
            self.desc_lbl.config(text=f"fetching {label}…")

    def _set_note(self, now_text: "str | None", alerts: bool) -> None:
        """Show or hide NOW's top line and ALERTS' line. NOW's goes above
        everything, the warning banner included, whatever was packed first."""
        if now_text:
            self.away_lbl.config(text=now_text)
            slaves = self.now_column.body.pack_slaves()
            if self.away_lbl not in slaves:
                self.away_lbl.pack(fill="x", pady=(6, 0), before=slaves[0])
        else:
            self.away_lbl.pack_forget()
        if alerts:
            self.alerts_away.pack(fill="x", pady=(6, 0), before=self.alerts_tab)
        else:
            self.alerts_away.pack_forget()

    # ── Kairos update contract ─────────────────────────────────────────────────

    def update(self, data: dict | None) -> None:
        """Called by Kairos every 30 min on the main thread. Renders
        data["picked"] when a place other than Home is picked, else Home;
        None when both sources failed for Home."""
        if data is None:
            # Reset the color too, so a stale "hot" red doesn't linger on the
            # "unavailable" placeholder after a fetch miss.
            self.temp_lbl.config(text="unavailable", font=FONTS["medium"],
                                 fg=C["text1"])
            # "fetch failed" said nothing a user could act on. Aura only reaches
            # here once BOTH wttr.in and Open-Meteo have failed, which is worth
            # naming — it points at the link rather than at one flaky host, and
            # Kairos is already retrying on its short ladder.
            self.desc_lbl.config(text="no weather source reachable — retrying")
            # HELIOS and SELENE are not touched: they ride their own Kairos
            # workers and need no weather (V0.03 audit M3).
            # Forecast rows keep their last values — the NOW tab already signals
            # staleness; blanking the outlook on a transient fetch miss is noise.
            return
        self.place_bar.refresh()
        picked = data.get("picked")
        if picked and picked.get("missing"):
            self._render(data, data.get("location"))
            self._shown_area = PlaceBar.HOME
            self._set_note(_MISSING, alerts=False)
            return
        if picked and picked.get("failed"):
            # Home is fine; the place could not be reached. Keep its last
            # reading if that is what NOW shows; never show Home's under its name.
            name = str(picked.get("name") or "the place picked")
            self.set_header(f"Atmospherics — {name}")
            if self._shown_area != picked.get("area"):
                self.temp_lbl.config(text="unavailable", font=FONTS["medium"],
                                     fg=C["text1"])
                for label in self._detail_labels.values():
                    label.config(text="—")
                self._update_forecast([])
                self._shown_area = str(picked.get("area"))
            self.desc_lbl.config(
                text=f"couldn't reach the weather for {name} — retrying at the next check")
            self._set_note(_AWAY_NOW, alerts=True)
            return
        if picked:
            self._render(picked, str(picked.get("name") or ""))
            self._shown_area = str(picked.get("area"))
            self._set_note(_AWAY_NOW, alerts=True)
            return
        self._render(data, data.get("location"))
        self._shown_area = PlaceBar.HOME
        self._set_note(None, alerts=False)

    def _render(self, data: dict, place: "str | None") -> None:
        """Draw one reading (Home's, or the picked place's) on NOW and FORECAST."""
        # Settings temperature unit ("F"/"C"): pick the matching Aura field and
        # suffix. The color tier still grades on °F canonical (the thresholds in
        # _temp_color are defined in °F), independent of the displayed unit.
        unit = themis.temperature_unit()
        suf = "c" if unit == "C" else "f"
        # Re-title the card to the place shown: the wttr-resolved location for
        # Home (so a Settings change shows without a restart), the label for a
        # picked area.
        if place:
            self.set_header(f"Atmospherics — {place}")
        temp_f = data["temp_f"]
        self.temp_lbl.config(text=f"{data[f'temp_{suf}']}°{unit}", font=FONTS["xlarge_bold"],
                             fg=_temp_color(temp_f))
        self.desc_lbl.config(text=data["description"])
        self._detail_labels["feels like"].config(text=f"{data[f'feels_like_{suf}']}°{unit}")
        self._detail_labels["H / L"].config(
            text=f"{data[f'high_{suf}']}° / {data[f'low_{suf}']}°")
        self._detail_labels["wind"].config(text=f"{data['wind_label']} ({data['wind_mph']} mph)")
        self._detail_labels["humidity"].config(text=f"{data['humidity_pct']}%")
        self._detail_labels["rain chance"].config(text=f"{data.get('rain_chance_pct', 0)}%")
        self._detail_labels["UV"].config(text=str(data["uv_index"]))
        self._update_forecast(data.get("forecast", []), suf)
