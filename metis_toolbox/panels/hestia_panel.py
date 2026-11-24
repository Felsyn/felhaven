"""
panels/hestia_panel.py — Hestia: the Home command surface
===========================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Draw Pythia's command surface: her title, the wireframe face and
             the vox controls. The title block reads "PYTHIA" over a line
             that says "ask the oracle" while nothing is happening and what
             Pythia or the Auto-DJ is doing while something is ("consulting
             the oracle…"); the controls sit in a bordered box. Draws and
             delegates — owns NO chat state, no worker, no history, no
             counters of its own. HomePanel wires the controls to its own
             handlers, writes the line, and tells the face what she is doing.

Layout:      PYTHIA                                  ┌──────────────────┐
             ask the oracle      ┌──────────┐        │ ● Vox Response   │
                                 │   face   │        │ ● Vox End        │
                                 └──────────┘        │ ● Auto-DJ        │
                                                     │ ● Reset Session  │
                                                     └──────────────────┘

             Three grid columns, not packed sides. Pack would centre the face
             in the *leftover* space, which sits off the midline whenever the
             two side columns differ in width — and they do (the title is
             108 px, the control box 133, measured 2026-11-19). The outer
             columns share a `uniform`
             group so they are forced to equal width, which is what puts the
             face on the real centre line. The session readouts used to live
             here; they now sit on the status bar at the bottom of HomePanel,
             away from the controls.

The line:    `line` is the status line (HomePanel._set_status writes it; a
             blank status puts IDLE_LINE back). It wraps inside the title
             column rather than widening it: a wider column would widen the
             other one too (the uniform group) and push the face off the
             centre line. A second line costs nothing beside a 160 px face.

Contract:    HestiaBar(parent, on_stop, on_refresh, on_notice)
                 line                        — the status line's Label
                 IDLE_LINE                   — what it says when nothing is
                                               happening
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
             panels/pythia_face.py (the indicator),
             panels/virgil_tooltip.py + tools/virgil.py (the face's tip),
             theme.py (colors/fonts)

Requires:    tkinter, typing (stdlib). calliope and awen only indirectly, via
             VoxControls.

2026-07-26:  walkthrough audit — verified draw-and-delegate honest; owns no chat state
of its own. §6-clean: no thread and no after() — HomePanel drives every readout
and wires the Stop/Refresh handlers.

2026-09-01: the icon cluster became three words (panels/vox_controls.py, which
retired the NarratorLamp) and the wireframe face moved in from sandbox/. Hestia
still holds no state: the face's animation is the face's own business (§6), and
the armed/lit cue is still whatever HomePanel last pushed. Then regridded — face
centred and enlarged, controls stacked right, readouts moved out to HomePanel's
status row.

V0.03:       Walked through 2026-11-19 (the V0.03 walkthrough's S19). Words
             only: the two column widths the Layout note gives are
             re-measured (108 and 133 px, were 102 and 127; the point, that
             they differ, holds), and Requires catches up. Checked and true:
             draw-and-delegate, the uniform grid, the line's wrap, every
             setter.
"""

from typing import Callable

import tkinter as tk

from theme import C, FONTS
from panels import virgil_tooltip
from panels.pythia_face import PythiaFace
from panels.vox_controls import VoxControls
from tools import virgil

# Exactly half the 380×320 the face's geometry was tuned at, so its internal
# scale factor lands on 0.5 and every contour steps evenly.
_FACE_W = 190
_FACE_H = 160
_FACE_PADX = 12          # px each side of the face, inside its grid column
# The status line's wrap width until the first <Configure> says how wide the
# title column really is: a little under that column's width at the 760 px
# minimum window (153 px), so a status written before then can't widen it.
# Never below _LINE_WRAP_MIN, so a window caught mid-resize still gets
# readable lines.
_LINE_WRAP_START = 140
_LINE_WRAP_MIN = 80


class HestiaBar(tk.Frame):
    # The face's hover tooltip (Virgil): it is drawn by one module, so the tip
    # is that module's own gloss (CONVENTIONS §7). The vox controls carry
    # their own, in VoxControls._TIPS. The status line's tip is HomePanel's,
    # which writes the line.
    _TIPS = {
        "face": virgil.module("pythia_face"),
    }

    # What the status line says while nothing is happening.
    IDLE_LINE = "ask the oracle"

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
        # The status line: HomePanel writes it (see "The line" above).
        self.line = tk.Label(titles, text=self.IDLE_LINE, font=FONTS["subtitle"],
                             fg=C["text3"], bg=C["bg"], anchor="w",
                             justify="left", wraplength=_LINE_WRAP_START)
        self.line.pack(anchor="w")

        # ── The face, centred ────────────────────────────────────────────────
        # bg is inherited from C["bg"] on purpose — the face's glow ramps blend
        # toward the background it sits on.
        self._face = PythiaFace(self, width=_FACE_W, height=_FACE_H)
        self._face.grid(row=0, column=1, padx=_FACE_PADX)

        # ── The vox control box, top right ───────────────────────────────────
        self._vox = VoxControls(self, on_end=on_stop, on_reset=on_refresh,
                                on_notice=on_notice)
        self._vox.grid(row=0, column=2, sticky="ne")

        self._tip_targets: dict[str, tk.Misc] = {"face": self._face}
        for key, tip in self._TIPS.items():
            if key in self._tip_targets:
                virgil_tooltip.attach(self._tip_targets[key], tip)

        # The bar's width is set by HomePanel (it fills the row), never by
        # the line, so re-wrapping on its <Configure> can't feed back.
        self.bind("<Configure>", self._fit_line, add="+")

    def _fit_line(self, event: "tk.Event[tk.Misc]") -> None:
        """Wrap the status line to the title column's width: the bar's width
        less the face's column, halved, since the two outer columns are
        equal. Asking for no more than that keeps the face centred.
        wraplength is the text's width; the Label adds its border and
        padding on both sides, so those come off first."""
        face_column = self._face.winfo_reqwidth() + 2 * _FACE_PADX
        side = (event.width - face_column) // 2
        edges = 2 * (int(str(self.line.cget("borderwidth")))
                     + int(str(self.line.cget("padx"))))
        self.line.configure(wraplength=max(_LINE_WRAP_MIN, side - edges))

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
