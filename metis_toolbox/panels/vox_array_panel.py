"""
panels/vox_array_panel.py — Vox Array host (Morpheus + Echo + Orpheus + Tunes)
============================================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Host the audio tabs and switch between them. Build the tab bar; pack
             the active tab body; nothing else. No data, no update().

Tabs:        MORPHEUS — YouTube audio player (transport, playlists, search)
             ECHO     — text → audio file (paste-and-convert)
             ORPHEUS  — play back an audio file from local_audio/
             TUNES    — Euterpe's song ledger, as editable JSON

TUNES plays nothing, and that is why it is here rather than anywhere else: it is
the history of what this host played, and the taste the Auto-DJ toggle picks
from. It adds no tenth control surface to the audio stack (specs/
master_vox_handoff.md §4) — there is nothing on it to stop.

Why a host with no update(): MorpheusPanel and OrpheusPanel each keep their own
update() and stay registered with Kairos directly under their own worker name
(felhaven.py reaches through this host to self.morpheus / self.orpheus, the
Moderati precedent). Echo and Tunes are request-driven and not polled at all. So
this host never sees a Kairos tick — do NOT register it: Card (a tk.Frame) has a
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
from panels.euterpe_panel  import EuterpePanel


class VoxArrayPanel(TabHost):
    """Thin tab host (theme.TabHost). Tab bodies are children of self.body and
    ARE the tabs."""

    def __init__(self, parent):
        super().__init__(parent, "Vox Array — audio", C["purple"])

        # ── Tab bodies (each IS its own tab — no wrapper frame) ──────────────
        # felhaven.py registers self.morpheus / self.orpheus under their own
        # Kairos worker names; Echo and Tunes are not polled, so they register
        # nothing.
        self.morpheus = MorpheusPanel(self.body)
        self.echo     = EchoPanel(self.body)
        self.orpheus  = OrpheusPanel(self.body)
        self.euterpe  = EuterpePanel(self.body)

        # (key, label, body, accent). TabHost builds the bar and activates the
        # first; felhaven reaches through to self.morpheus / self.orpheus.
        # TUNES is amber because amber is already Auto-DJ's channel everywhere
        # else in the stack — the dj line in Home's transcript and the toggle's
        # own lamp — and the other three accents are taken.
        self._build_tabs([
            ("morpheus", "MORPHEUS", self.morpheus, C["purple"]),
            ("echo",     "ECHO",     self.echo,     C["blue"]),
            ("orpheus",  "ORPHEUS",  self.orpheus,  C["teal"]),
            ("tunes",    "TUNES",    self.euterpe,  C["amber"]),
        ], default="morpheus")
