"""
panels/narrator_panel.py — Narration Toggle Lamp
================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         A header lamp for Pythia's narration. Draw a speaker — lit (amber,
             with sound waves) when auto-speak is on, dim (grey, muted) when off
             — and flip the toggle on click. Display + one action, no state of
             its own.

State:       calliope's auto-speak flag, the single source of truth. This lamp
             is the flag's ONLY mutator; the home panel merely reads it. So —
             unlike the old voice lamp — there is nothing external to poll: not a
             Kairos worker, just a click toggle that re-reads calliope after
             flipping.

Colors:      theme.C only (no hex literals here).
Precedent:   Repurposes the retired Metis voice lamp's header slot and lit/dim
             pattern; the flame became a speaker when voice input was removed.
"""

import tkinter as tk

import calliope
from theme import C


def draw_speaker(canvas: tk.Canvas, cx: float, cy: float, size: int = 20,
                 on: bool = True) -> None:
    """One speaker silhouette, colored by state. cx/cy = center; size = height
    in px. Sound waves are drawn only when on. Redraw-safe via the 'spk' tag."""
    canvas.delete("spk")

    body = C["amber"] if on else C["text3"]
    line = C["coral"] if on else C["border"]

    h = size
    top = cy - h / 2
    # Cabinet (small square) on the left, cone (triangle) opening to the right.
    box_w = h * 0.34
    box_l = cx - h * 0.55
    box_t = cy - h * 0.22
    box_b = cy + h * 0.22
    canvas.create_rectangle(box_l, box_t, box_l + box_w, box_b,
                            fill=body, outline=line, width=1, tags="spk")
    cone_r = cx - h * 0.05
    canvas.create_polygon(
        box_l + box_w, box_t,
        cone_r, top,
        cone_r, top + h,
        box_l + box_w, box_b,
        fill=body, outline=line, width=1, tags="spk",
    )

    if on:
        # Two nested sound-wave arcs to the right of the cone.
        for i, r in enumerate((h * 0.16, h * 0.30)):
            x = cone_r + h * 0.06 + i * h * 0.14
            canvas.create_arc(
                x - r, cy - r, x + r, cy + r,
                start=-55, extent=110, style="arc",
                outline=C["badge_fg"], width=2, tags="spk",
            )
    else:
        # A single muting slash to read as "off / silent".
        x = cone_r + h * 0.10
        canvas.create_line(x, cy - h * 0.28, x + h * 0.34, cy + h * 0.28,
                           fill=C["border"], width=2, tags="spk")


class NarratorLamp(tk.Canvas):
    """Header lamp. Click flips calliope's auto-speak flag, then re-renders from
    the real flag (never a guessed local copy — single source of truth)."""

    _BOX = 30   # canvas box; speaker drawn at ~20 centered, leaving a margin

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, width=self._BOX, height=self._BOX,
                         bg=C["bg"], highlightthickness=0, cursor="hand2")
        self.bind("<Button-1>", self._on_click)
        self._render()

    def _render(self) -> None:
        draw_speaker(self, self._BOX / 2, self._BOX / 2, size=20,
                     on=calliope.auto_speak_enabled())

    def _on_click(self, _event: "tk.Event[tk.Misc]") -> None:
        calliope.toggle_auto_speak()
        self._render()
