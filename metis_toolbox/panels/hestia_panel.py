"""
panels/hestia_panel.py — Hestia: the Home command surface
===========================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Draw Pythia's command surface — the "PYTHIA / ask the oracle"
             title block, the wireframe face, and the vox control box. Draws
             and delegates — owns NO chat state, no worker, no history, no
             counters of its own. HomePanel wires the controls to its own
             handlers and tells the face what she is doing.

Layout:      PYTHIA                                  ┌──────────────────┐
             ask the oracle      ┌──────────┐        │ ● Vox Response   │
                                 │   face   │        │ ● Vox End        │
                                 └──────────┘        │ ● Auto-DJ        │
                                                     │ ● Reset Session  │
                                                     └──────────────────┘

             Three grid columns, not packed sides. Pack would centre the face
             in the *leftover* space, which sits off the midline whenever the
             two side columns differ in width — and they do (the title is
             ~102 px, the control box ~127). The outer columns share a `uniform`
             group so they are forced to equal width, which is what puts the
             face on the real centre line. The session readouts used to live
             here; they now sit with the status line at the bottom of
             HomePanel, away from the controls.

Contract:    HestiaBar(parent, on_stop, on_refresh, on_notice)
                 set_running(running: bool)  — lit/dim cue for Vox End
                                               (cosmetic only — Vox End is
                                               ALWAYS clickable, since
                                               narration can keep playing after
                                               generation ends; see
                                               panels/vox_controls.py)
                 set_face_state(name: str)   — idle | playing | thinking |
                                               speaking
                 flash_reset()               — one beat on Reset Session
                 refresh_vox()               — re-read the vox row's real flags
                                               (Auto-DJ can turn itself off)

Upstream:    panels/home_panel.py (constructs + drives this)
Downstream:  panels/vox_controls.py (the control box),
             panels/pythia_face.py (the indicator), theme.py (colors/fonts)

Requires:    tkinter (stdlib). calliope only indirectly, via VoxControls.

V0.02: Audited 2026-07-26 — verified draw-and-delegate honest; owns no chat state
of its own. §6-clean: no thread and no after() — HomePanel drives every readout
and wires the Stop/Refresh handlers.

2026-09-01: the icon cluster became three words (panels/vox_controls.py, which
retired the NarratorLamp) and the wireframe face moved in from sandbox/. Hestia
still holds no state: the face's animation is the face's own business (§6), and
the armed/lit cue is still whatever HomePanel last pushed. Then regridded — face
centred and enlarged, controls stacked right, readouts moved out to HomePanel's
status row.
"""

from typing import Callable

import tkinter as tk

from theme import C, FONTS
from panels.pythia_face import PythiaFace
from panels.vox_controls import VoxControls

# Exactly half the 380×320 the face's geometry was tuned at, so its internal
# scale factor lands on 0.5 and every contour steps evenly.
_FACE_W = 190
_FACE_H = 160


class HestiaBar(tk.Frame):
    def __init__(self, parent: tk.Widget, *,
                 on_stop: Callable[[], None],
                 on_refresh: Callable[[], None],
                 on_notice: Callable[[str], None]) -> None:
        super().__init__(parent, bg=C["bg"])

        # The outer columns share a uniform group, so grid gives them equal
        # width whatever their contents ask for — without it, the wider column
        # would push the face off the centre line.
        self.columnconfigure(0, weight=1, uniform="hestia_side")
        self.columnconfigure(2, weight=1, uniform="hestia_side")

        # ── Title block, top left ────────────────────────────────────────────
        titles = tk.Frame(self, bg=C["bg"])
        titles.grid(row=0, column=0, sticky="nw")

        tk.Label(titles, text="PYTHIA", font=FONTS["title"],
                 fg=C["text1"], bg=C["bg"]).pack(anchor="w", pady=(0, 2))
        tk.Label(titles, text="ask the oracle", font=FONTS["subtitle"],
                 fg=C["text3"], bg=C["bg"]).pack(anchor="w")

        # ── The face, centred ────────────────────────────────────────────────
        # bg is inherited from C["bg"] on purpose — the face's glow ramps blend
        # toward the background it sits on.
        self._face = PythiaFace(self, width=_FACE_W, height=_FACE_H)
        self._face.grid(row=0, column=1, padx=12)

        # ── The vox control box, top right ───────────────────────────────────
        self._vox = VoxControls(self, on_end=on_stop, on_reset=on_refresh,
                                on_notice=on_notice)
        self._vox.grid(row=0, column=2, sticky="ne")

    # ── HomePanel-facing setters ─────────────────────────────────────────────

    def set_running(self, running: bool) -> None:
        self._vox.set_armed(running)

    def set_face_state(self, name: str) -> None:
        self._face.set_state(name)

    def flash_reset(self) -> None:
        self._vox.flash_reset()

    def refresh_vox(self) -> None:
        """Re-read the vox row's real flags. HomePanel pushes this on its drain
        chain because Auto-DJ can change with nobody clicking it — the breaker
        turns it off by itself after three turns that played nothing, and a lamp
        left amber over a job that has given up is a lie."""
        self._vox.refresh()
