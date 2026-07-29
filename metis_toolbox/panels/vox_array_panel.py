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

V0.02: Audited 2026-07-26 — verified honest; felhaven reaches through to
       self.morpheus / self.orpheus for Kairos, Echo is request-driven. Host
       smoke coverage already exists (test_vox_array_panel_smoke.py).
"""

from theme import C, TabHost

from panels.morpheus_panel import MorpheusPanel
from panels.echo_panel     import EchoPanel
from panels.orpheus_panel  import OrpheusPanel


class VoxArrayPanel(TabHost):
    """Thin tab host (theme.TabHost). Tab bodies are children of self.body and
    ARE the tabs."""

    def __init__(self, parent):
        super().__init__(parent, "Vox Array — audio", C["purple"])

        # ── Tab bodies (each IS its own tab — no wrapper frame) ──────────────
        # felhaven.py registers self.morpheus / self.orpheus under their own
        # Kairos worker names; Echo is not polled, so it registers nothing.
        self.morpheus = MorpheusPanel(self.body)
        self.echo     = EchoPanel(self.body)
        self.orpheus  = OrpheusPanel(self.body)

        # (key, label, body, accent). TabHost builds the bar and activates the
        # first; felhaven reaches through to self.morpheus / self.orpheus.
        self._build_tabs([
            ("morpheus", "MORPHEUS", self.morpheus, C["purple"]),
            ("echo",     "ECHO",     self.echo,     C["blue"]),
            ("orpheus",  "ORPHEUS",  self.orpheus,  C["teal"]),
        ], default="morpheus")
