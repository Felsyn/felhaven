"""
panels/vox_array_panel.py — Vox Array host (Morpheus + Echo + Orpheus tabs)
============================================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Host the audio tabs and switch between them. Build the tab bar; pack
             the active tab body; nothing else. No data, no update().

Tabs:        MORPHEUS — YouTube audio player (transport, playlists, search)
             ECHO     — text → audio file (paste-and-convert)
             ORPHEUS  — play back an audio file from local_audio/

Why a host with no update(): MorpheusPanel and OrpheusPanel each keep their own
update() and stay registered with Kairos directly under their own worker name
(felhaven.py reaches through this host to self.morpheus / self.orpheus, the
Moderati precedent). Echo is request-driven and not polled at all. So this
host never sees a Kairos tick — do NOT register it: Card (a tk.Frame) has a
built-in update() that takes no data arg, so a Kairos dispatch against the
host would TypeError.
"""

import tkinter as tk

from theme import C, FONTS, Card

from panels.morpheus_panel import MorpheusPanel
from panels.echo_panel     import EchoPanel
from panels.orpheus_panel  import OrpheusPanel


class VoxArrayPanel(Card):
    """Thin tab host. Tab bodies are children of self.body and ARE the tabs."""

    def __init__(self, parent):
        super().__init__(parent, "Vox Array — audio", C["purple"])

        # ── Tab bodies (each IS its own tab — no wrapper frame) ──────────────
        # felhaven.py registers self.morpheus / self.orpheus under their own
        # Kairos worker names; Echo is not polled, so it registers nothing.
        self.morpheus = MorpheusPanel(self.body)
        self.echo     = EchoPanel(self.body)
        self.orpheus  = OrpheusPanel(self.body)

        # key -> (body widget, accent color for the active underline).
        self._bodies = {
            "morpheus": (self.morpheus, C["purple"]),
            "echo":     (self.echo,     C["blue"]),
            "orpheus":  (self.orpheus,  C["teal"]),
        }

        # ── Tab bar (loop over (key, label)) ─────────────────────────────────
        tab_row = tk.Frame(self.body, bg=C["card"])
        tab_row.pack(fill="x", pady=(6, 0))

        self._tabs:  dict[str, tk.Label] = {}
        self._lines: dict[str, tk.Frame] = {}
        for key, text in (("morpheus", "MORPHEUS"), ("echo", "ECHO"),
                          ("orpheus", "ORPHEUS")):
            wrap = tk.Frame(tab_row, bg=C["card"])
            wrap.pack(side="left", padx=(0, 14))
            lbl = tk.Label(wrap, text=text, font=FONTS["card_header"],
                           fg=C["text3"], bg=C["card"], cursor="hand2")
            lbl.pack()
            line = tk.Frame(wrap, height=1, bg=C["border"])
            line.pack(fill="x")
            lbl.bind("<Button-1>", lambda e, k=key: self._show_tab(k))
            self._tabs[key]  = lbl
            self._lines[key] = line

        # ── Activate the default tab (MORPHEUS) ──────────────────────────────
        # Bodies are constructed but unpacked; _show_tab packs the active one
        # BELOW tab_row (tab_row was packed first), so the bar stays on top.
        self._active = None
        self._show_tab("morpheus")

    def _show_tab(self, key: str) -> None:
        if key == self._active:
            return
        # Hide the current body and dim its tab (skipped on the first call).
        if self._active is not None:
            self._bodies[self._active][0].pack_forget()
            self._tabs[self._active].config(fg=C["text3"])
            self._lines[self._active].config(bg=C["border"])

        # Show the chosen body and light its tab.
        body, accent = self._bodies[key]
        body.pack(fill="both", expand=True, pady=(6, 0))
        self._tabs[key].config(fg=C["text1"])
        self._lines[key].config(bg=accent)
        self._active = key
