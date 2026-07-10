"""
Aura panel — weather display for the Felhaven dashboard.

WeatherPanel — two tabs, updated by Kairos every 30 min via update():
    NOW       current temp + description + details (feels-like, H/L, wind,
              humidity, rain chance, UV), plus two collapsible sub-widgets
              fed from the same astronomy block:
                  HeliosWidget — sun  (sunrise, sunset, golden hours, day length)
                  SeleneWidget — moon (phase, illumination, moonrise, moonset)
    FORECAST  3-day outlook — emoji icon + description + H/L + chance of rain.

Module-level _icon() maps wttr.in (WWO) weather codes to emoji for the forecast.
"""

import tkinter as tk
from datetime import datetime

import themis
from theme import C, FONTS, Card

from tools import aura
from tools import helios, selene


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
    Pure display: all interpretation lives in tools/helios.py.
    """

    ROWS = ("sunrise", "sunset", "golden AM", "golden PM", "day length")

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        # Divider
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", pady=(8, 6))

        # Section header row with toggle
        header = tk.Frame(self, bg=C["card"], cursor="hand2")
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

        # Shown in place of the rows when interpret() returns None.
        self._unavail = tk.Label(self._body, text="unavailable", font=FONTS["small"],
                                  fg=C["text3"], bg=C["card"], anchor="w")

        # Key-left / value-right detail rows (same pattern as WeatherPanel).
        self._rows = tk.Frame(self._body, bg=C["card"])
        self._rows.pack(fill="x")
        self._values = {}
        for key in self.ROWS:
            row = tk.Frame(self._rows, bg=C["card"])
            row.pack(fill="x")
            tk.Label(row, text=key, font=FONTS["small"], fg=C["text2"],
                     bg=C["card"], anchor="w").pack(side="left")
            v = tk.Label(row, text="—", font=FONTS["small"], fg=C["text1"],
                         bg=C["card"], anchor="e")
            v.pack(side="right")
            self._values[key] = v

    def _section_toggle(self):
        if self._collapsed:
            self._body.pack(fill="x")
            self._toggle_lbl.config(text="▼")
            self._collapsed = False
        else:
            self._body.pack_forget()
            self._toggle_lbl.config(text="▶")
            self._collapsed = True

    def update(self, astro: dict | None) -> None:
        """Feed the astronomy dict (or None) straight from WeatherPanel."""
        info = helios.interpret(astro)
        if info is None:
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
    Pure display: all interpretation lives in tools/selene.py.
    """

    ROWS = ("illumination", "moonrise", "moonset")

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        # Divider
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", pady=(8, 6))

        # Section header row with toggle
        header = tk.Frame(self, bg=C["card"], cursor="hand2")
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
        for key in self.ROWS:
            row = tk.Frame(self._content, bg=C["card"])
            row.pack(fill="x")
            tk.Label(row, text=key, font=FONTS["small"], fg=C["text2"],
                     bg=C["card"], anchor="w").pack(side="left")
            v = tk.Label(row, text="—", font=FONTS["small"], fg=C["text1"],
                         bg=C["card"], anchor="e")
            v.pack(side="right")
            self._values[key] = v

    def _section_toggle(self):
        if self._collapsed:
            self._body.pack(fill="x")
            self._toggle_lbl.config(text="▼")
            self._collapsed = False
        else:
            self._body.pack_forget()
            self._toggle_lbl.config(text="▶")
            self._collapsed = True

    def update(self, astro: dict | None) -> None:
        """Feed the astronomy dict (or None) straight from WeatherPanel."""
        info = selene.interpret(astro)
        if info is None:
            self._content.pack_forget()
            self._unavail.pack(fill="x")
            return
        self._unavail.pack_forget()
        self._content.pack(fill="x")
        self._phase_lbl.config(text=f"{info['emoji']} {info['phase']}".strip())
        # illumination can be "" when wttr omits it — fall back to the em-dash
        # glyph the other rows use rather than showing a blank value.
        self._values["illumination"].config(text=info["illumination"] or "—")
        self._values["moonrise"].config(text=info["moonrise"])
        self._values["moonset"].config(text=info["moonset"])


# ─────────────────────────────────────────────────────────────────────────────
#  WeatherPanel
# ─────────────────────────────────────────────────────────────────────────────

class WeatherPanel(Card):
    """
    Receives weather data from Kairos via update(). Two tabs (the Pheme tab-bar
    pattern, minus the scroll frame — three forecast rows don't scroll):
        NOW       — current conditions + Helios (sun) + Selene (moon)
        FORECAST  — 3-day outlook with emoji icons and chance of rain
    """

    # Detail rows for the NOW tab. "rain chance" sits after humidity, before UV.
    _DETAIL_KEYS = ("feels like", "H / L", "wind", "humidity", "rain chance", "UV")

    def __init__(self, parent):
        # Generic title at construction; update() re-titles it to the live
        # weather location (data["location"]) once the first fetch lands, so a
        # Settings-tab location change is reflected without a restart.
        super().__init__(parent, "Atmospherics", C["blue"])

        # Tab bar across the top, content area beneath it.
        tab_row = tk.Frame(self.body, bg=C["card"])
        tab_row.pack(fill="x", pady=(6, 0))
        self._content = tk.Frame(self.body, bg=C["card"])
        self._content.pack(fill="both", expand=True)

        self._tabs: dict[str, dict] = {}     # name -> {"tab", "line", "frame"}
        self._active: str | None = None
        for name in ("NOW", "FORECAST"):
            self._build_tab(tab_row, name)

        self._build_now_tab(self._tabs["NOW"]["frame"])
        self._build_forecast_tab(self._tabs["FORECAST"]["frame"])

        self._show_tab("NOW")                # NOW active by default

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
            prev["tab"].config(fg=C["text3"])
            prev["line"].config(bg=C["border"])
        self._active = name
        cur = self._tabs[name]
        cur["frame"].pack(fill="both", expand=True)
        cur["tab"].config(fg=C["text1"])
        cur["line"].config(bg=C["blue"])

    # ── NOW tab ────────────────────────────────────────────────────────────────

    def _build_now_tab(self, parent: tk.Frame) -> None:
        self.temp_lbl = tk.Label(parent, text="fetching...", font=FONTS["xlarge_bold"],
                                 fg=C["text1"], bg=C["card"], anchor="w")
        self.temp_lbl.pack(fill="x", pady=(6, 0))
        self.desc_lbl = tk.Label(parent, text="", font=FONTS["small"],
                                 fg=C["text2"], bg=C["card"], anchor="w")
        self.desc_lbl.pack(fill="x")
        self.details_frame = tk.Frame(parent, bg=C["card"])
        self.details_frame.pack(fill="x", pady=(4, 0))
        self._detail_labels = {}
        for key in self._DETAIL_KEYS:
            row = tk.Frame(self.details_frame, bg=C["card"])
            row.pack(fill="x")
            tk.Label(row, text=key, font=FONTS["small"], fg=C["text2"],
                     bg=C["card"], anchor="w").pack(side="left")
            v = tk.Label(row, text="—", font=FONTS["small"], fg=C["text1"],
                         bg=C["card"], anchor="e")
            v.pack(side="right")
            self._detail_labels[key] = v

        # Sun + moon sub-widgets, fed from the same astronomy block.
        self._helios = HeliosWidget(parent)
        self._helios.pack(fill="x")
        self._selene = SeleneWidget(parent)
        self._selene.pack(fill="x")

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
        return {"day": day, "icon": icon, "desc": desc, "hl": hl, "precip": precip}

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

    # ── Kairos update contract ─────────────────────────────────────────────────

    def update(self, data: dict) -> None:
        """Called by Kairos every 30 min on the main thread."""
        if data is None:
            # Reset the color too, so a stale "hot" red doesn't linger on the
            # "unavailable" placeholder after a fetch miss.
            self.temp_lbl.config(text="unavailable", font=FONTS["medium"],
                                 fg=C["text1"])
            self.desc_lbl.config(text="fetch failed")
            self._helios.update(None)
            self._selene.update(None)
            # Forecast rows keep their last values — the NOW tab already signals
            # staleness; blanking the outlook on a transient fetch miss is noise.
            return
        # Settings temperature unit ("F"/"C"): pick the matching Aura field and
        # suffix. The color tier still grades on °F canonical (the thresholds in
        # _temp_color are defined in °F), independent of the displayed unit.
        unit = themis.temperature_unit()
        suf = "c" if unit == "C" else "f"
        # Re-title the card to the live, wttr-resolved location so a Settings
        # location change is visible without a restart.
        loc = data.get("location")
        if loc:
            self.set_header(f"Atmospherics — {loc}")
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
        self._helios.update(data.get("astronomy") if data else None)
        self._selene.update(data.get("astronomy") if data else None)
        self._update_forecast(data.get("forecast", []), suf)
