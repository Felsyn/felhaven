"""
panels/vox_controls.py — the Vox control row
============================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Draw Pythia's three vox controls as words, not glyphs — Vox
             Response, Vox End, Reset Session — stacked in a bordered box, each
             with a state dot, and report a click. Owns the lit/dim styling and
             nothing else: no chat state, no worker, no narration state of its
             own.

Layout:      ┌──────────────────┐
             │ ● Vox Response   │
             │ ● Vox End        │
             │ ● Reset Session  │
             └──────────────────┘
             One box around the group, not one per row. The border is constant;
             the dots and the words carry all the state.

Colours:     This row INVERTS the §7 active/inactive convention, deliberately.
             Elsewhere active = C["text1"] and hover = C["amber"]; here
             active = C["amber"] and inactive = C["text3"], because "amber
             means this is doing something right now" is the rule the whole
             Sonus indicator (and the wireframe face beside it) already reads
             by. Amber therefore cannot also mean hover — an unhovered active
             control and a hovered inactive one would be the same colour — so
             hover BRIGHTENS WITHIN the current state: dim → text1 when off,
             amber → amber_bright when on. See CONVENTIONS §7.

             The dot is the sidebar's dot (same canvas, same 8 px oval) with
             the polarity flipped. SidebarRow lights its dot GREEN when active
             and leaves it amber otherwise — "amber = somewhere you could go,
             green = you are here". Reusing that here would turn a dot green at
             the exact moment its own label turned amber, so each dot follows
             the label beside it instead. Same colour, computed once, painted
             on both surfaces.

State:       Vox Response mirrors calliope's auto-speak flag, which stays the
             single source of truth — this row is the flag's only runtime
             mutator, and it re-reads the real flag after flipping rather than
             keeping a guessed copy. Vox End's lit/dim comes from HestiaBar via
             set_armed(); Reset Session is momentary and only flashes.

Contract:    VoxControls(parent, on_end, on_reset)
                 set_armed(armed: bool)   — lit/dim cue for Vox End
                 flash_reset()            — one amber beat on Reset Session
                 refresh()                — re-read calliope's auto-speak flag

Upstream:    panels/hestia_panel.py (constructs + drives this)
Downstream:  calliope.py (auto-speak flag), theme.py (colors/fonts)

Requires:    tkinter (stdlib); calliope.

Precedent:   Replaces the retired panels/narrator_panel.py speaker lamp. The
             lamp's discipline is carried over verbatim — read the flag, never
             cache it — only the rendering changed from a drawn icon to a word.
             The dot and the bordered box are SidebarRow's chrome
             (panels/sidebar.py), reused rather than reinvented.
"""

from typing import Callable, Optional

import tkinter as tk

import calliope
from theme import C, FONTS

_FLASH_MS = 150         # one amber beat for a momentary control
_DOT = 8                # the sidebar's dot, to the pixel


class VoxToggle(tk.Frame):
    """One dotted, word-shaped control.

    `active` is the lit state; `flash()` borrows the lit look for a beat
    without touching it, which is how a momentary control (Reset Session) shows
    that it fired. Both are cosmetic — a click always fires, whatever the
    colour says (see VoxControls' note on Vox End).
    """

    def __init__(self, parent: tk.Widget, text: str,
                 on_click: Callable[[], None]) -> None:
        super().__init__(parent, bg=C["bg"])

        self._dot = tk.Canvas(self, width=_DOT, height=_DOT, bg=C["bg"],
                              highlightthickness=0)
        self._dot_item = self._dot.create_oval(1, 1, _DOT - 1, _DOT - 1,
                                               fill=C["text3"], outline="")
        self._dot.pack(side="left", padx=(0, 8))

        self._label = tk.Label(self, text=text, font=FONTS["small_bold"],
                               fg=C["text3"], bg=C["bg"], anchor="w")
        self._label.pack(side="left")

        self._on_click = on_click
        self._active = False
        self._hover = False
        self._flashing = False
        self._flash_after_id: Optional[str] = None

        # Every sub-widget, the SidebarRow way: bind only the label and the
        # hover drops out as the pointer crosses onto the frame, and the dot
        # becomes a dead 8 px target that looks clickable.
        for w in (self, self._dot, self._label):
            w.bind("<Button-1>", lambda _e: self._on_click())
            w.bind("<Enter>", lambda _e: self._set_hover(True))
            w.bind("<Leave>", lambda _e: self._set_hover(False))
            w.configure(cursor="hand2")

    # ── Public ───────────────────────────────────────────────────────────────

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return                          # idempotent: pushed every 60 ms
        self._active = active
        self._restyle()

    def flash(self) -> None:
        """Light this control for one beat, then hand it back to set_active.

        A single one-shot after() for transient UI feedback, which §6 permits —
        but the id is stored and cancelled in destroy() all the same. Re-arming
        cancels first, so a double-click can never orphan an id.
        """
        if self._flash_after_id is not None:
            self.after_cancel(self._flash_after_id)
        self._flashing = True
        self._restyle()
        self._flash_after_id = self.after(_FLASH_MS, self._end_flash)

    def destroy(self) -> None:
        if self._flash_after_id is not None:
            self.after_cancel(self._flash_after_id)
            self._flash_after_id = None
        super().destroy()

    # ── Internals ────────────────────────────────────────────────────────────

    def _end_flash(self) -> None:
        self._flash_after_id = None
        self._flashing = False
        self._restyle()

    def _set_hover(self, hover: bool) -> None:
        self._hover = hover
        self._restyle()

    def _restyle(self) -> None:
        """One computed colour, painted on both surfaces. Two branches — one
        for the word, one for the dot — is exactly how they drift apart."""
        lit = self._active or self._flashing
        if lit:
            colour = C["amber_bright"] if self._hover else C["amber"]
        else:
            colour = C["text1"] if self._hover else C["text3"]
        self._label.config(fg=colour)
        self._dot.itemconfigure(self._dot_item, fill=colour)


class VoxControls(tk.Frame):
    """The bordered box holding Vox Response · Vox End · Reset Session.

    Vox End is ALWAYS clickable, whatever set_armed() last said. Narration can
    keep playing for seconds after the answer text finishes — synth and
    playback lag behind generation — so gating the click on "still generating"
    left the control dead at exactly the moment a still-talking user wanted to
    silence it. The armed flag drives the dim/lit cue and nothing else. (This
    was a real fix once; it must not be re-broken by the redesign.)
    """

    def __init__(self, parent: tk.Widget, *,
                 on_end: Callable[[], None],
                 on_reset: Callable[[], None]) -> None:
        super().__init__(parent, bg=C["bg"],
                         highlightbackground=C["border"], highlightthickness=2)

        inner = tk.Frame(self, bg=C["bg"])
        inner.pack(fill="both", expand=True, padx=8, pady=6)

        self._response = VoxToggle(inner, "Vox Response", self._toggle_response)
        self._response.pack(fill="x")

        self._end = VoxToggle(inner, "Vox End", on_end)
        self._end.pack(fill="x", pady=(4, 0))

        self._reset = VoxToggle(inner, "Reset Session", on_reset)
        self._reset.pack(fill="x", pady=(4, 0))

        self.refresh()

    # ── HestiaBar-facing ─────────────────────────────────────────────────────

    def set_armed(self, armed: bool) -> None:
        """Lit while there is something for Vox End to end — a turn generating,
        or narration still audible. Cosmetic only."""
        self._end.set_active(armed)

    def flash_reset(self) -> None:
        self._reset.flash()

    def refresh(self) -> None:
        """Re-read calliope's auto-speak flag — never a local copy of it."""
        self._response.set_active(calliope.auto_speak_enabled())

    # ── Vox Response ─────────────────────────────────────────────────────────

    def _toggle_response(self) -> None:
        calliope.toggle_auto_speak()
        self.refresh()
