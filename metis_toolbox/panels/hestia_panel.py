"""
panels/hestia_panel.py — Hestia: the Home command surface
===========================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Draw Pythia's command surface — the "PYTHIA / ask the oracle"
             title block, the (relocated) narration lamp, a Stop control, a
             Refresh control, and two session readouts (Scraptoken Flux =
             running token total, Rites = tool-call tally). Draws and
             delegates — owns NO chat state, no worker, no history, no
             counters of its own. HomePanel pushes every number in and wires
             Stop/Refresh to its own handlers.

Layout:      PYTHIA
             ask the oracle   [speaker] [stop] [refresh]   Flux: n   Rites: n

Contract:    HestiaBar(parent, on_stop, on_refresh)
                 set_flux(tokens: int)             — session token total
                 set_rites(called: int, failed: int) — session tool tally
                 set_running(running: bool)          — bright/dim cue for Stop
                                                        (cosmetic only — Stop
                                                        is ALWAYS clickable,
                                                        since narration can
                                                        keep playing after
                                                        generation ends)

Upstream:    panels/home_panel.py (constructs + drives this)
Downstream:  panels/narrator_panel.py (NarratorLamp, reused not forked),
             theme.py (colors/fonts)

Requires:    tkinter (stdlib). calliope only indirectly, via NarratorLamp.
"""

from typing import Callable

import tkinter as tk

from theme import C, FONTS
from panels.narrator_panel import NarratorLamp


class HestiaBar(tk.Frame):
    def __init__(self, parent: tk.Widget, *,
                 on_stop: Callable[[], None],
                 on_refresh: Callable[[], None]) -> None:
        super().__init__(parent, bg=C["bg"])

        tk.Label(self, text="PYTHIA", font=FONTS["title"],
                 fg=C["text1"], bg=C["bg"]).pack(anchor="w", pady=(0, 2))

        row = tk.Frame(self, bg=C["bg"])
        row.pack(fill="x", pady=(0, 8))
        tk.Label(row, text="ask the oracle", font=FONTS["subtitle"],
                 fg=C["text3"], bg=C["bg"]).pack(side="left")

        # ── Controls: narration lamp, stop, refresh ─────────────────────────
        controls = tk.Frame(row, bg=C["bg"])
        controls.pack(side="left", padx=(14, 0))

        NarratorLamp(controls).pack(side="left")

        self._stop = tk.Label(controls, text="⏹", font=FONTS["large_bold"],
                              fg=C["text3"], bg=C["bg"])
        self._stop.pack(side="left", padx=(8, 0))
        self._stop.bind("<Button-1>", lambda _e: self._fire_stop())
        self._stop.bind("<Enter>", lambda _e: self._hover_stop(True))
        self._stop.bind("<Leave>", lambda _e: self._hover_stop(False))

        self._refresh = tk.Label(controls, text="↻", font=FONTS["large_bold"],
                                 fg=C["text1"], bg=C["bg"], cursor="hand2")
        self._refresh.pack(side="left", padx=(8, 0))
        self._refresh.bind("<Button-1>", lambda _e: on_refresh())
        self._refresh.bind("<Enter>", lambda _e: self._refresh.config(fg=C["amber"]))
        self._refresh.bind("<Leave>", lambda _e: self._refresh.config(fg=C["text1"]))

        # ── Session readouts, right-aligned ─────────────────────────────────
        readouts = tk.Frame(row, bg=C["bg"])
        readouts.pack(side="right")

        self._flux = tk.Label(readouts, text="Scraptoken Flux: 0",
                              font=FONTS["small"], fg=C["text3"], bg=C["bg"])
        self._flux.pack(side="left", padx=(0, 12))

        rites_wrap = tk.Frame(readouts, bg=C["bg"])
        rites_wrap.pack(side="left")
        self._rites_main = tk.Label(rites_wrap, text="Rites: 0",
                                    font=FONTS["small"], fg=C["text3"], bg=C["bg"])
        self._rites_main.pack(side="left")
        self._rites_fail = tk.Label(rites_wrap, text="",
                                    font=FONTS["small"], fg=C["red"], bg=C["bg"])
        self._rites_fail.pack(side="left")

        self._on_stop = on_stop
        self._running = False
        self._restyle_stop()

    # ── HomePanel-facing setters ─────────────────────────────────────────────

    def set_flux(self, tokens: int) -> None:
        self._flux.config(text=f"Scraptoken Flux: {tokens}")

    def set_rites(self, called: int, failed: int) -> None:
        self._rites_main.config(text=f"Rites: {called}")
        self._rites_fail.config(text=f" · {failed} failed" if failed else "")

    def set_running(self, running: bool) -> None:
        self._running = running
        self._restyle_stop()

    # ── Stop control ──────────────────────────────────────────────────────────
    # Always clickable — narration can keep playing for seconds after the
    # answer text itself finishes (synth/playback lags behind generation), so
    # gating the click on "still generating" left Stop dead exactly when a
    # still-talking user wanted to silence it. `_running` now only drives the
    # dim/bright cue, never whether a click fires.

    def _fire_stop(self) -> None:
        self._on_stop()

    def _hover_stop(self, on: bool) -> None:
        self._stop.config(fg=C["amber"] if on else (C["text1"] if self._running else C["text3"]))

    def _restyle_stop(self) -> None:
        # cursor stays "hand2" regardless — bright while generating, dim at
        # rest, but clickable either way (see _fire_stop).
        self._stop.config(fg=C["text1"] if self._running else C["text3"], cursor="hand2")
