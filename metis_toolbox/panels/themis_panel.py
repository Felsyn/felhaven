"""
Themis panel — the Settings face inside Moderati (the SETTINGS tab).
====================================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Draw the Settings UI: labeled entries for latitude / longitude /
             optional weather-location, a temperature-unit toggle (°F/°C) and a
             clock-format toggle (12h/24h), and a Save button. Its ONE job is to
             turn those fields into a themis.save() call — all persistence,
             validation, and defaults live in themis.py; this module never reads
             or writes felhaven_settings.json directly.

Placement:   A bare tk.Frame tab body inside ModeratiPanel (the SETTINGS tab),
             the same shape as ArgusPanel / CerberusPanel. NOT a Card, NOT
             Kairos-registered — Settings has no worker and never ticks; it is
             purely request-driven (the user edits and saves). So there is no
             update(data) here and no after()/thread.

Propagation: on a successful Save, the panel calls its refetch hook (wired by
             felhaven.py to Kairos.refetch) so the location/time workers — aura,
             hypatia, horai — re-fire on the next tick and the dashboard follows
             the new settings without a restart. Before Kairos exists the hook
             is a no-op, so the panel is safe to construct standalone (smoke
             tests) and to Save even if felhaven never wired it.

Upstream:    panels/moderati_panel.py (hosts this as the fifth tab)
Downstream:  themis.py (all persistence + validation), theme.py (colors/fonts)

Requires:    tkinter (stdlib). No JSON handling here — that is themis.py's job.
"""

from typing import Any, Callable

import tkinter as tk

import themis
from theme import C, FONTS


# ─────────────────────────────────────────────────────────────────────────────
#  _SegToggle — a two-option segmented control (°F/°C, 12h/24h). The active
#  option lights to text1, the other dims to text3 — the same active/inactive
#  language the tab bars use (§7).
# ─────────────────────────────────────────────────────────────────────────────

class _SegToggle(tk.Frame):
    def __init__(self, parent: tk.Widget, label: str,
                 options: list[tuple[Any, str]], initial: Any):
        super().__init__(parent, bg=C["card"])
        tk.Label(self, text=label, font=FONTS["small"], fg=C["text2"],
                 bg=C["card"], anchor="w", width=16).pack(side="left")
        self.value = initial
        self._labels: dict[Any, tk.Label] = {}
        for val, text in options:
            lbl = tk.Label(self, text=text, font=FONTS["small_bold"], bg=C["card"],
                           cursor="hand2", padx=8)
            lbl.pack(side="left", padx=(0, 6))
            lbl.bind("<Button-1>", lambda e, v=val: self._select(v))
            self._labels[val] = lbl
        self._restyle()

    def _select(self, val: Any) -> None:
        self.value = val
        self._restyle()

    def _restyle(self) -> None:
        for val, lbl in self._labels.items():
            lbl.config(fg=C["text1"] if val == self.value else C["text3"])


# ─────────────────────────────────────────────────────────────────────────────
#  ThemisPanel
# ─────────────────────────────────────────────────────────────────────────────

class ThemisPanel(tk.Frame):
    """Location + unit + clock settings. A bare Frame tab body inside
    ModeratiPanel; no Kairos worker, no update() — purely request-driven."""

    def __init__(self, parent: tk.Widget):
        super().__init__(parent, bg=C["card"])

        # No-op until felhaven wires Kairos.refetch in (see module docstring).
        self._refetch: Callable[..., None] = lambda *names: None

        s = themis.load()

        tk.Label(self, text="SETTINGS — this install", font=FONTS["card_header"],
                 fg=C["text2"], bg=C["card"], anchor="w").pack(fill="x", pady=(8, 2))
        tk.Label(self,
                 text="One place drives weather, star map, planets and season. "
                      "Enter coordinates (no city lookup); an optional weather "
                      "location overrides them for weather only.",
                 font=FONTS["small_italic"], fg=C["text3"], bg=C["card"],
                 anchor="w", wraplength=380, justify="left").pack(fill="x", pady=(0, 10))

        self._lat = self._field("Latitude", str(s["latitude"]))
        self._lon = self._field("Longitude", str(s["longitude"]))
        self._loc = self._field("Weather location", str(s["weather_location"]))

        self._unit = _SegToggle(self, "Temperature", [("F", "°F"), ("C", "°C")],
                                s["temperature_unit"])
        self._unit.pack(fill="x", pady=(8, 0))
        self._clock = _SegToggle(self, "Clock", [(False, "12-hour"), (True, "24-hour")],
                                 bool(s["clock_24h"]))
        self._clock.pack(fill="x", pady=(6, 0))

        # Save button — a clickable Label with the amber-on-hover language the
        # rest of the stack uses for actions (§7), not a native tk.Button.
        save = tk.Label(self, text="SAVE", font=FONTS["small_bold"], fg=C["text1"],
                        bg=C["card"], cursor="hand2")
        save.pack(anchor="w", pady=(12, 0))
        save.bind("<Button-1>", lambda e: self._on_save())
        save.bind("<Enter>", lambda e: save.config(fg=C["amber"]))
        save.bind("<Leave>", lambda e: save.config(fg=C["text1"]))

        self._status = tk.Label(self, text="", font=FONTS["tiny"], fg=C["text3"],
                                bg=C["card"], anchor="w", wraplength=380,
                                justify="left")
        self._status.pack(fill="x", pady=(8, 0))

    # ── construction helper ────────────────────────────────────────────────────

    def _field(self, label: str, value: str) -> tk.Entry:
        """One labeled entry row (label left, entry right), styled like the
        Cerberus gate's PIN entry. Returns the Entry so the caller can read it."""
        row = tk.Frame(self, bg=C["card"])
        row.pack(fill="x", pady=2)
        tk.Label(row, text=label, font=FONTS["small"], fg=C["text2"], bg=C["card"],
                 anchor="w", width=16).pack(side="left")
        entry = tk.Entry(
            row, font=FONTS["body"], bg=C["bar_bg"], fg=C["text1"],
            insertbackground=C["text1"], highlightbackground=C["border"],
            highlightcolor=C["amber"], highlightthickness=1, borderwidth=0,
        )
        entry.pack(side="left", fill="x", expand=True)
        entry.insert(0, value)
        return entry

    # ── refetch wiring ─────────────────────────────────────────────────────────

    def set_refetch(self, fn: Callable[..., None]) -> None:
        """felhaven.py calls this after Kairos exists, passing Kairos.refetch, so
        a Save nudges the aura/hypatia/horai workers to re-fire promptly."""
        self._refetch = fn

    # ── save ────────────────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        try:
            lat = float(self._lat.get().strip())
            lon = float(self._lon.get().strip())
        except ValueError:
            self._status.config(text="Latitude and longitude must be numbers.",
                                fg=C["red"])
            return
        try:
            themis.save(latitude=lat, longitude=lon,
                        weather_location=self._loc.get(),
                        temperature_unit=self._unit.value,
                        clock_24h=self._clock.value)
        except themis.SettingsError as e:
            self._status.config(text=f"Not saved — {e}", fg=C["red"])
            return
        # Nudge the location/time workers so weather, sky, planets and season
        # follow the change on the next tick rather than waiting out Aura's
        # 30-minute interval.
        self._refetch("aura", "hypatia", "horai")
        self._status.config(
            text="Saved ✓ — weather, sky, and clock will refresh shortly.",
            fg=C["text1"])
