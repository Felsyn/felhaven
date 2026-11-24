"""
Themis panel — the Settings face inside Moderati (the SETTINGS tab).
====================================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Draw the Settings form and turn its fields into a themis.save() call.
             The form is labeled entries for latitude / longitude / optional
             weather-location, a temperature-unit toggle (°F/°C), a
             clock-format toggle (12h/24h), an ALERTS section (weather alerts
             on/off, what pops up, read aloud, the aurora notice), and a Save
             button. All persistence, validation, and defaults live in
             themis.py; this module never reads or writes
             felhaven_settings.json directly.
             One row is NOT saved by Save: "Atmospherics opens on", the same
             PlaceBar as Atmospherics' PLACE dropdown (panels/aura_panel.py),
             saves the moment a place is picked, so a form built before a pick
             made there can never put the old place back.

Layout:      The form sits in a ScrollColumn sized to its content, with SAVE
             and the status line right under it. At Felhaven's 760 x 500
             minimum the tab is about 488 x 330, shorter than the form, so the
             column shrinks to what is left and scrolls, and SAVE stays on
             screen (§7: what must stay visible is never what gets squeezed).

Placement:   A bare tk.Frame tab body inside ModeratiPanel (the SETTINGS tab),
             the same shape as ArgusPanel / CerberusPanel. NOT a Card, NOT
             Kairos-registered — Settings has no worker and never ticks; it is
             purely request-driven (the user edits and saves). So there is no
             update(data) here and no after()/thread. The one exception is the
             PlaceBar (`place_bar`), registered on "aura" by felhaven.py so it
             shows a place picked in Atmospherics.

Propagation: on a successful Save, the panel calls its refetch hook (wired by
             felhaven.py to Kairos.refetch) so every worker that reads the
             settings (aura, iris, eos, hypatia, horai, artemis, helios,
             selene; a test derives the list from Kairos's workers) re-fires
             on the next tick and the dashboard follows the new settings
             without a restart. Before Kairos exists the hook is a no-op, so
             the panel is safe to construct standalone (smoke tests) and to
             Save even if felhaven never wired it.

Errors:      A value Themis refuses shows its SettingsError's own words in red.
             A write that fails shows only the OSError's kind, never its text,
             which carries the file's path (§2), in MACHINE SPIRIT's words.

Upstream:    panels/moderati_panel.py (hosts this as the fifth tab),
             felhaven.py (set_refetch, and registers place_bar on "aura").
Downstream:  themis.py (all persistence + validation), panels/aura_panel.py
             (PlaceBar), theme.py (colors, fonts, ScrollColumn).

Requires:    tkinter, typing (stdlib). No JSON handling here — that is
             themis.py's job.

2026-07-25:  The first audit found it honest and changed no logic (§6-clean:
             no threads/after; refetch hook + SettingsError handling intact).

V0.03: Walked through 2026-11-21 (born 2026-07-10 with Themis; first audited
       2026-07-25). A write that failed escaped SAVE's callback (measured
       with a PermissionError: the status line kept whatever it said, and
       under pythonw nothing showed at all); it now says "Not saved" in red,
       as MACHINE SPIRIT's saves do. The latitude, longitude and location
       boxes stop repeating the theme's cursor colour (§7). Propagation's
       worker list was three of eight; Errors, Upstream, Downstream and
       Requires caught up.
"""

from typing import Any, Callable

import tkinter as tk

import themis
from panels.aura_panel import PlaceBar
from theme import C, FONTS, ScrollColumn


def _not_saved(error: Exception) -> str:
    """The status line for a save that failed. A settings error's message is
    written for the reader; an OSError's carries a file path, so only its
    kind is shown (§2). MACHINE SPIRIT's saves use the same words."""
    if isinstance(error, OSError):
        return f"Not saved — the file could not be written ({type(error).__name__})."
    return f"Not saved — {error}"


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
    """Location, unit, clock and alerts settings. A bare Frame tab body inside
    ModeratiPanel; no Kairos worker, no update() — purely request-driven."""

    _PLACE_NOTE = ("Saved as soon as it's picked, like Atmospherics' own PLACE "
                   "list. Only the weather follows it; everything else stays "
                   "on the place above.")

    _SECOND_CHANNEL = ("A second channel, not the warning system: nothing arrives "
                       "while the PC sleeps or Felhaven is closed. Keep your "
                       "phone's emergency alerts on.")

    def __init__(self, parent: tk.Widget):
        super().__init__(parent, bg=C["card"])

        # No-op until felhaven wires Kairos.refetch in (see module docstring).
        self._refetch: Callable[..., None] = lambda *names: None

        s = themis.load()

        # The form scrolls; SAVE and the status line follow it (see Layout).
        self.column = ScrollColumn(self)
        self.column.pack(side="top", fill="x")
        self.save_bar = tk.Frame(self, bg=C["card"])
        self.save_bar.pack(side="top", fill="x")
        form = self._form = self.column.body

        tk.Label(form, text="SETTINGS — this install", font=FONTS["card_header"],
                 fg=C["text2"], bg=C["card"], anchor="w").pack(fill="x", pady=(8, 2))
        tk.Label(form,
                 text="One place drives weather, alerts, star map, planets and "
                      "season. Enter coordinates (no city lookup); an optional "
                      "weather location overrides them for weather only.",
                 font=FONTS["small_italic"], fg=C["text3"], bg=C["card"],
                 anchor="w", wraplength=380, justify="left").pack(fill="x", pady=(0, 10))

        self._lat = self._field("Latitude", str(s["latitude"]))
        self._lon = self._field("Longitude", str(s["longitude"]))
        self._loc = self._field("Weather location", str(s["weather_location"]))
        # Saved when picked, not by SAVE (see the module docstring).
        place_row = tk.Frame(form, bg=C["card"])
        place_row.pack(fill="x", pady=(6, 0))
        tk.Label(place_row, text="Atmospherics opens on", font=FONTS["small"],
                 fg=C["text2"], bg=C["card"], anchor="w").pack(side="left", padx=(0, 6))
        self.place_bar = PlaceBar(place_row, caption="")
        self.place_bar.pack(side="left")
        tk.Label(form, text=self._PLACE_NOTE, font=FONTS["tiny"], fg=C["text3"],
                 bg=C["card"], anchor="w", wraplength=380, justify="left",
                 ).pack(fill="x", pady=(2, 0))

        self._unit = _SegToggle(form, "Temperature", [("F", "°F"), ("C", "°C")],
                                s["temperature_unit"])
        self._unit.pack(fill="x", pady=(8, 0))
        self._clock = _SegToggle(form, "Clock", [(False, "12-hour"), (True, "24-hour")],
                                 bool(s["clock_24h"]))
        self._clock.pack(fill="x", pady=(6, 0))

        # ── ALERTS (tools/iris.py, panels/iris_panel.py) ──
        tk.Frame(form, bg=C["border"], height=1).pack(fill="x", pady=(14, 6))
        tk.Label(form, text="ALERTS — National Weather Service, US only",
                 font=FONTS["card_header"], fg=C["text3"], bg=C["card"],
                 anchor="w").pack(fill="x")
        self._alerts = _SegToggle(form, "Weather alerts", [(True, "On"), (False, "Off")],
                                  bool(s["alerts_enabled"]))
        self._alerts.pack(fill="x", pady=(6, 0))
        self._popup = _SegToggle(form, "Pop up for",
                                 [("warnings", "Warnings"),
                                  ("warnings_watches", "Warnings and watches")],
                                 s["alerts_popup"])
        self._popup.pack(fill="x", pady=(6, 0))
        self._read_aloud = _SegToggle(form, "Read aloud", [(True, "On"), (False, "Off")],
                                      bool(s["alerts_read_aloud"]))
        self._read_aloud.pack(fill="x", pady=(6, 0))
        self._aurora = _SegToggle(form, "Space weather",
                                  [(False, "Panel only"), (True, "Aurora notice too")],
                                  bool(s["space_aurora_notice"]))
        self._aurora.pack(fill="x", pady=(6, 0))
        tk.Label(form, text=self._SECOND_CHANNEL, font=FONTS["tiny"], fg=C["text3"],
                 bg=C["card"], anchor="w", wraplength=380, justify="left",
                 ).pack(fill="x", pady=(8, 4))

        # Save button — a clickable Label with the amber-on-hover language the
        # rest of the stack uses for actions (§7), not a native tk.Button.
        save = self.save = tk.Label(self.save_bar, text="SAVE", font=FONTS["small_bold"],
                                    fg=C["text1"], bg=C["card"], cursor="hand2")
        save.pack(anchor="w", pady=(8, 0))
        save.bind("<Button-1>", lambda e: self._on_save())
        save.bind("<Enter>", lambda e: save.config(fg=C["amber"]))
        save.bind("<Leave>", lambda e: save.config(fg=C["text1"]))

        self._status = tk.Label(self.save_bar, text="", font=FONTS["tiny"], fg=C["text3"],
                                bg=C["card"], anchor="w", wraplength=380,
                                justify="left")
        self._status.pack(fill="x", pady=(6, 0))

        self.bind("<Configure>", self._fit, add="+")
        form.bind("<Configure>", self._fit, add="+")
        self.save_bar.bind("<Configure>", self._fit, add="+")

    # ── layout ─────────────────────────────────────────────────────────────────

    def _fit(self, _event=None) -> None:
        """The column is as tall as the form, or as what is left above SAVE
        and the status line, whichever is less; past that it scrolls."""
        room = self.winfo_height() - self.save_bar.winfo_reqheight()
        want = self._form.winfo_reqheight()
        height = max(40, min(want, room)) if room > 1 else want
        self.column.set_size(height=height)

    # ── construction helper ────────────────────────────────────────────────────

    def _field(self, label: str, value: str) -> tk.Entry:
        """One labeled entry row (label left, entry right), styled like the
        Cerberus gate's PIN entry. Returns the Entry so the caller can read it."""
        row = tk.Frame(self._form, bg=C["card"])
        row.pack(fill="x", pady=2)
        tk.Label(row, text=label, font=FONTS["small"], fg=C["text2"], bg=C["card"],
                 anchor="w", width=16).pack(side="left")
        entry = tk.Entry(
            row, font=FONTS["body"], bg=C["bar_bg"], fg=C["text1"],
            highlightbackground=C["border"], highlightcolor=C["amber"],
            highlightthickness=1, borderwidth=0,
        )
        entry.pack(side="left", fill="x", expand=True)
        entry.insert(0, value)
        return entry

    # ── refetch wiring ─────────────────────────────────────────────────────────

    def set_refetch(self, fn: Callable[..., None]) -> None:
        """felhaven.py calls this after Kairos exists, passing Kairos.refetch, so
        a Save nudges the location and time workers to re-fire promptly. The
        PlaceBar gets it too: its pick refetches "aura"."""
        self._refetch = fn
        self.place_bar.set_refetch(fn)

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
                        clock_24h=self._clock.value,
                        alerts_enabled=self._alerts.value,
                        alerts_popup=self._popup.value,
                        alerts_read_aloud=self._read_aloud.value,
                        space_aurora_notice=self._aurora.value)
        except (themis.SettingsError, OSError) as e:
            self._status.config(text=_not_saved(e), fg=C["red"])
            return
        # Nudge the location/time workers so weather, alerts, sky, planets,
        # season, sun, moon and the local chart follow the change on the next
        # tick rather than waiting out their intervals.
        self._refetch("aura", "iris", "eos", "hypatia", "horai", "artemis", "helios",
                      "selene")
        self._status.config(
            text="Saved ✓ — weather, alerts, sky, and clock will refresh shortly.",
            fg=C["text1"])
