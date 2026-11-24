"""
Eos panel — space weather on NOW, the aurora on Celestarium, a notice on Home.

Job:         Show space weather: NOW's fold row, Celestarium's aurora line, Home's notice.

Contract:    Three displays, each registered to Kairos's "eos" worker
             (felhaven.py) and each taking eos.fetch()'s dict, or None when a
             check failed. There is nothing to acknowledge, so there is no
             controller: each keeps its own last good reading.
               SpaceWeatherWidget  the SPACE WEATHER fold row on NOW, after
                                   Selene, in Helios's shape: R/S/G now, Kp,
                                   the next three days, the aurora here, and
                                   when SWPC was last checked.
               AuroraRow           a rider for Celestarium's "aurora" row (the
                                   moon_row pattern, panels/hypatia_panel.py).
               AuroraNotice        a quiet strip on Home, under the warning
                                   strip, only with Settings "Aurora notice
                                   too": no popup and no voice, because an
                                   aurora is not an emergency.

Failure:     None after a good reading keeps it, under "couldn't check since
             <time> · retrying" (the Iris rule: a failure never reads as a
             quiet sky). Celestarium's row says "unknown"; Home's notice hides.

Aurora:      The estimate is tools/eos.py's (SWPC's 2° per Kp rule from the
             geomagnetic latitude, with a 4° horizon margin): a rough guide,
             and its tooltip says so.

Upstream:    felhaven.py (registers all three), panels/aura_panel.py (hosts the
             fold row), panels/hypatia_panel.py (hosts the aurora row),
             panels/home_panel.py (hosts the notice).
Downstream:  tools/eos.py (the data's shape: Kairos hands each display its
             fetch()'s dict, so nothing here imports it), themis.py (the clock
             format, and whether the notice shows), theme.py (colours, fonts),
             tools/virgil.py and panels/virgil_tooltip.py (the fold row's
             tooltips).

V0.03:       Walked through 2026-11-17 (the V0.03 walkthrough, CONVENTIONS
             §3), its first audit (born 2026-11-10, #62; tooltips #64, the
             folded row's "out of date" #71). No threads or timers, colours
             from the palette, every tip in _TIPS, and a failure never reads
             as a quiet sky on any of the three displays. Downstream gained
             theme.py and the Virgil pair, and says why tools/eos.py isn't
             imported; _need()'s docstring lost a phrase that meant nothing.
"""

import tkinter as tk
from datetime import date, datetime
from typing import Any, Optional

import themis
from panels import virgil_tooltip
from theme import C, FONTS
from tools import virgil

_ROWS = (("R", "radio blackouts (solar flares)"),
         ("S", "solar radiation storms"),
         ("G", "geomagnetic storms"))
_LOUD = 3                       # a level this high on any scale lights the header


def _clock(dt: datetime) -> str:
    if themis.clock_24h():
        return dt.strftime("%H:%M")
    return dt.strftime("%I:%M %p").lstrip("0")


def _kp(value: float) -> str:
    """Kp in thirds (2.33, 2.67) shown to one decimal; whole values bare."""
    return f"{value:.0f}" if abs(value - round(value)) < 0.05 else f"{value:.1f}"


def _level(block: dict[str, Any], letter: str) -> str:
    level = block.get("level")
    return f"{letter}{level if level is not None else '?'}"


def _side(aurora: dict[str, Any]) -> str:
    return "south" if aurora.get("mlat", 0) < 0 else "north"


def _need(aurora: dict[str, Any]) -> str:
    """'Kp 8 (G4)', or 'Kp 3' below storm level."""
    g = aurora.get("g_needed")
    return f"Kp {aurora['kp_needed']}" + (f" (G{g})" if g else "")


def aurora_text(data: dict[str, Any]) -> tuple[str, str]:
    """(text, colour) for the aurora here, shared by NOW and Celestarium."""
    a, kp = data["aurora"], _kp(data["kp"])
    if a["kp_needed"] == 0:
        return f"in the auroral zone · Kp {kp}", C["text1"]
    if a["kp_needed"] > 9:
        return f"out of reach here · Kp {kp}", C["text3"]
    if a["now"]:
        return f"possible now, low in the {_side(a)} · Kp {kp}", C["amber_bright"]
    if a["next_24h"]:
        return (f"possible in the next 24 h, low in the {_side(a)} · "
                f"Kp {_kp(a['kp_max_24h'])} forecast"), C["amber_bright"]
    return f"unlikely · Kp {kp}, needs about {_need(a)}", C["text3"]


def _outlook(days: list[dict[str, Any]]) -> str:
    """'next 3 days: G1 expected Wed · flare chance (R1+) up to 60%'."""
    storms = []
    for day in days:
        try:
            when = date.fromisoformat(day["date"]).strftime("%a")
        except (TypeError, ValueError):
            when = "?"
        if day.get("g"):
            storms.append(f"G{day['g']} expected {when}")
    parts = [", ".join(storms) if storms else "no geomagnetic storm expected"]
    flare = max((d["r_minor"] for d in days if d.get("r_minor") is not None), default=None)
    if flare:
        parts.append(f"flare chance (R1+) up to {flare}%")
    return "next 3 days: " + " · ".join(parts)


class SpaceWeatherWidget(tk.Frame):
    """The SPACE WEATHER fold row: collapsed by default, toggled like Helios."""

    # Hover tooltips (Virgil): the header says what R/S/G mean, each scale's
    # row what it measures, the outlook what it forecasts, and the aurora line
    # how rough its guess is. test_virgil_surfaces holds the table.
    _TIPS = {
        "header": virgil.term("space_weather"),
        "R": virgil.term("radio_blackouts"),
        "S": virgil.term("solar_radiation_storms"),
        "G": virgil.term("geomagnetic_storms"),
        "outlook": virgil.term("space_outlook"),
        "aurora": virgil.term("aurora_estimate"),
    }

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, bg=C["card"])
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", pady=(8, 6))
        header = self.header = tk.Frame(self, bg=C["card"], cursor="hand2")
        header.pack(fill="x")
        self.title = tk.Label(header, text="SPACE WEATHER", font=FONTS["card_header"],
                              fg=C["text3"], bg=C["card"], anchor="w", cursor="hand2")
        self.title.pack(side="left")
        self.toggle = tk.Label(header, text="▶", font=FONTS["card_header"],
                               fg=C["text3"], bg=C["card"], cursor="hand2")
        self.toggle.pack(side="right")
        self.summary = tk.Label(header, text="", font=FONTS["card_header"],
                                fg=C["text2"], bg=C["card"], cursor="hand2")
        self.summary.pack(side="left", padx=(12, 0))
        for widget in (header, self.title, self.summary, self.toggle):
            widget.bind("<Button-1>", lambda _e: self.fold())
        self.toggle.bind("<Enter>", lambda _e: self.toggle.config(fg=C["text1"]))
        self.toggle.bind("<Leave>", lambda _e: self.toggle.config(fg=C["text3"]))

        self.body = tk.Frame(self, bg=C["card"])     # collapsed: not packed
        self.collapsed = True
        self.values: dict[str, tk.Label] = {}
        rows: dict[str, tk.Frame] = {}
        for key, label in _ROWS:
            row = rows[key] = tk.Frame(self.body, bg=C["card"])
            row.pack(fill="x")
            tk.Label(row, text=label, font=FONTS["small"], fg=C["text2"], bg=C["card"],
                     anchor="w").pack(side="left")
            value = tk.Label(row, text="—", font=FONTS["small"], fg=C["text1"],
                             bg=C["card"], anchor="e")
            value.pack(side="right")
            self.values[key] = value
        self.outlook = self._dim("not checked yet")
        self.aurora = self._dim("")
        self.source = self._dim("")
        self.body.bind("<Configure>", self._on_resize)

        self._last: Optional[dict[str, Any]] = None
        self._checked_at: Optional[datetime] = None
        self.tip_targets: dict[str, tk.Misc] = {"header": self.header, **rows,
                                                "outlook": self.outlook,
                                                "aurora": self.aurora}
        for key, tip in self._TIPS.items():
            virgil_tooltip.attach(self.tip_targets[key], tip)

    def _dim(self, text: str) -> tk.Label:
        lbl = tk.Label(self.body, text=text, font=FONTS["tiny"], fg=C["text3"],
                       bg=C["card"], anchor="w", justify="left")
        lbl.pack(fill="x", pady=(2, 0))
        return lbl

    def _on_resize(self, event) -> None:
        for lbl in (self.outlook, self.aurora, self.source):
            lbl.config(wraplength=max(120, event.width - 8))

    def fold(self) -> None:
        if self.collapsed:
            self.body.pack(fill="x")
            self.toggle.config(text="▼")
        else:
            self.body.pack_forget()
            self.toggle.config(text="▶")
        self.collapsed = not self.collapsed

    def update(self, data: Optional[dict[str, Any]]) -> None:
        if data is None:
            if self._last is None:
                self.summary.config(text="couldn't check", fg=C["amber"])
                self.outlook.config(text="couldn't check yet · retrying")
                return
            # The folded row is all most people see, so it says so too: the
            # last reading stays, marked out of date, until a check succeeds.
            now = self._last["now"]
            self.summary.config(text=" · ".join(_level(now[k], k) for k in "RSG")
                                     + " · out of date", fg=C["amber"])
            self.source.config(text=f"couldn't check since {_clock(self._checked_at)} · "
                                    "retrying · this may be out of date",
                               fg=C["amber_bright"])
            return
        self._last, self._checked_at = data, datetime.now()
        now = data["now"]
        loud = any((now[k].get("level") or 0) >= _LOUD for k in "RSG")
        self.title.config(fg=C["amber"] if loud else C["text3"])
        self.summary.config(text=" · ".join(_level(now[k], k) for k in "RSG"),
                            fg=C["amber_bright"] if loud else C["text2"])
        for key, _label in _ROWS:
            text = f"{_level(now[key], key)} {now[key]['text'] or ''}".strip()
            if key == "G":
                text += f" · Kp {_kp(data['kp'])}"
            self.values[key].config(text=text, fg=C["amber_bright"]
                                    if (now[key].get("level") or 0) >= _LOUD else C["text1"])
        self.outlook.config(text=_outlook(data.get("days") or []))
        text, colour = aurora_text(data)
        self.aurora.config(text=f"aurora here: {text}", fg=colour)
        self.source.config(text="NOAA Space Weather Prediction Center · checked "
                                f"{_clock(self._checked_at)}", fg=C["text3"])


class AuroraRow:
    """Celestarium's "aurora" row: a rider, because ConditionsWidget's own
    update() is taken by `aura` (the moon_row precedent)."""

    def __init__(self, label: tk.Label) -> None:
        self._label = label

    def update(self, data: Optional[dict[str, Any]]) -> None:
        if data is None:
            self._label.config(text="unknown · couldn't check", fg=C["text3"])
            return
        text, colour = aurora_text(data)
        self._label.config(text=text, fg=colour)


class AuroraNotice(tk.Frame):
    """Home's quiet aurora line, shown only with Settings "Aurora notice too"
    while the aurora may be visible here now or in the next 24 hours."""

    def __init__(self, parent: tk.Widget, before: tk.Widget) -> None:
        super().__init__(parent, bg=C["bg"])
        self._before = before
        self.shown = False
        self.label = tk.Label(self, text="", font=FONTS["small"], fg=C["teal"],
                              bg=C["bg"], anchor="w")
        self.label.pack(side="left", fill="x", expand=True)

    def update(self, data: Optional[dict[str, Any]]) -> None:
        a = (data or {}).get("aurora") or {}
        possible = a.get("now") or a.get("next_24h")
        if not (data and possible and themis.space_aurora_notice()):
            if self.shown:
                self.pack_forget()
                self.shown = False
            return
        if a["now"]:
            text = f"✦ aurora possible here now, low in the {_side(a)} · Kp {_kp(data['kp'])}"
        else:
            text = (f"✦ aurora possible here in the next 24 h, low in the {_side(a)} · "
                    f"Kp {_kp(a['kp_max_24h'])} forecast")
        self.label.config(text=text)
        if not self.shown:
            self.pack(fill="x", pady=(2, 0), before=self._before)
            self.shown = True
