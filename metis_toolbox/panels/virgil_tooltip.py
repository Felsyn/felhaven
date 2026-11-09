"""
virgil_tooltip.py — The Guide's Voice (the hover tooltip)
==========================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Show a short hover tooltip on any widget, in the words Virgil gives.
             It decides nothing about content: tools/virgil.py turns a tip into
             words, and this module only puts them beside the pointer.

Contract:    attach(widget, tip) -> None
               `tip` is virgil.module(key) or virgil.term(key). Idempotent per
               widget: attaching again replaces the tip and never stacks a
               second set of bindings. Never raises. The words are asked for
               at show time, so an edited glossary or docstring shows on the
               next hover.

One window:  One shared tooltip per Tk root, created on the first attach() and
             withdrawn when hidden — never one Toplevel per widget. Borderless
             (overrideredirect, set before it is ever mapped), transient to the
             root, in Card chrome: a C["card"] fill inside a C["border"] edge.
             Many widgets attach; at most one tip is on screen.

Timing:      Shown after _DELAY_MS of hover. One one-shot after() at a time
             (§6's one-at-a-time shape), scheduled on the ROOT, never on the
             attached widget: a widget destroyed while its own timer was pending
             would orphan a Tcl command. _arm() cancels the stored id before it
             re-arms, and _fire() cancels at its top, so a direct call (the
             smoke tests make them) can't orphan one either.

Hiding:      On <Leave>, <ButtonPress>, <Unmap> and <Destroy>. <Leave> is judged
             by the pointer, not the event. A composite widget (a SidebarRow is
             a frame of frames and labels) gets <Leave> when the pointer moves
             onto its own child, so the tip hides only once the pointer is
             outside the widget's on-screen rectangle; the same check keeps a
             tip that lands under the pointer from flickering. A press of each
             mouse button is bound on the widget and on every descendant it has
             when attached, because a click lands on the child under the
             pointer — as <ButtonPress-1> and its siblings, never a bare
             <ButtonPress>, which a widget's own <Button-1> would outrank (Tk
             runs only the most specific binding a widget has). After a
             click the widget stays quiet until the pointer has really left it.
             Before showing, _fire() checks again that the widget is viewable
             and the pointer inside it, so a missed <Leave> or a view switch
             mid-delay never pops a tip over the wrong screen.

Bindings:    Every bind uses add="+". SidebarRow, clickable rows and scroll
             frames already bind <Enter>/<Leave>, and keep theirs.

Placement:   Below-right of the pointer, offset so it never sits under it, and
             flipped left or up to stay on the screen. The wrap width is about
             _WRAP_CHARS characters of FONTS["small"], measured from the live
             font at every show, never a fixed pixel number, so it follows
             rescale_fonts().

Colours:     The title is C["text1"] in FONTS["small_bold"]. A module gloss is
             the module's own words, quoted, so its body is C["verbatim"], the
             colour for text shown exactly as a tool gave it. A glossary
             entry's body is C["text2"].

Teardown:    §6 to the letter. The pending show id lives on the shared tip. An
             attached widget's <Destroy> cancels a pending show it owns and
             hides a tip it owns. The tip window is the root's child, so
             Tk.destroy() destroys it before the root's own Tcl commands go;
             its destroy() override sets _destroyed first, then cancels the
             pending show, and every handler returns early on _destroyed, so an
             event Tk raises mid-teardown can't re-arm one.

Never raises: A tip whose words come back None never shows; Virgil has already
             logged that miss once. A TclError from a widget that is already
             gone is swallowed, and a failed attach() logs one warning, because
             a tooltip must never take a panel down with it (§7).

Upstream:    the panels that call attach() from a _TIPS table (CONVENTIONS §7):
             panels/sidebar.py, the three tab hosts (moderati_panel.py,
             cogitator_panel.py, vox_array_panel.py) and hephaestus_panel.py's
             VitalsPanel. tests/test_virgil_surfaces.py holds each table to
             its widgets.
Downstream:  tools/virgil.py (the words), theme.py (C, FONTS).

Requires:    tkinter, logging, weakref (stdlib), tools/virgil.py, theme.py.
"""

from __future__ import annotations

import logging
import tkinter as tk
import weakref
from typing import Any, Optional

from theme import C, FONTS
from tools import virgil

log = logging.getLogger("METIS.virgil_tooltip")

_DELAY_MS = 500          # hover this long before the tip shows
_WRAP_CHARS = 42         # wrap width, in characters of FONTS["small"]
_OFFSET_X = 14           # below-right of the pointer, clear of the cursor
_OFFSET_Y = 20
_FLIP_GAP = 6            # pointer-to-edge gap when flipped left or up
_PAD_X, _PAD_Y = 10, 7
_EDGE = 2                # Card's border thickness
# The click that hides the tip, one sequence per button. Not a bare
# <ButtonPress>: Tk runs only the MOST SPECIFIC binding a widget has for an
# event, so a row's own <Button-1> (every SidebarRow and tab label has one)
# would silently outrank it. The same sequence with add="+" appends instead.
_PRESSES = ("<ButtonPress-1>", "<ButtonPress-2>", "<ButtonPress-3>")

# root -> its one shared tip. Weak, so a destroyed root takes its tip with it.
_shared_tips: "weakref.WeakKeyDictionary[tk.Misc, _Tip]" = weakref.WeakKeyDictionary()


def _pointer_xy(widget: tk.Misc) -> tuple[int, int]:
    """The pointer, in screen pixels. A function of its own so a test can put
    the pointer anywhere without moving the real one."""
    return widget.winfo_pointerxy()


def _pointer_inside(widget: tk.Misc) -> bool:
    """Whether the pointer is inside the widget's on-screen rectangle."""
    try:
        x, y = _pointer_xy(widget)
        left, top = widget.winfo_rootx(), widget.winfo_rooty()
        return (left <= x < left + widget.winfo_width()
                and top <= y < top + widget.winfo_height())
    except tk.TclError:
        return False


def _descendants(widget: tk.Misc) -> list[tk.Misc]:
    out: list[tk.Misc] = []
    stack = list(widget.winfo_children())
    while stack:
        child = stack.pop()
        out.append(child)
        stack.extend(child.winfo_children())
    return out


class _Tip(tk.Toplevel):
    """The one tooltip window of a root, and the hover state behind it."""

    def __init__(self, root: tk.Misc) -> None:
        super().__init__(root, bg=C["card"])
        self.withdraw()                    # never mapped before it is borderless
        self.overrideredirect(True)
        self.transient(root)
        self._root_w = root
        self._destroyed = False
        self._after_id: Optional[str] = None
        self._owner: Optional[tk.Misc] = None     # pending or shown for
        self._quiet: Optional[tk.Misc] = None     # clicked, until the pointer leaves
        self._shown = False
        self._tips: dict[str, virgil.Tip] = {}    # widget path -> its tip

        frame = tk.Frame(self, bg=C["card"], padx=_PAD_X, pady=_PAD_Y,
                         highlightbackground=C["border"],
                         highlightcolor=C["border"], highlightthickness=_EDGE)
        frame.pack()
        self._title = tk.Label(frame, bg=C["card"], fg=C["text1"],
                               font=FONTS["small_bold"], anchor="w",
                               justify="left")
        self._title.pack(anchor="w")
        self._body = tk.Label(frame, bg=C["card"], fg=C["text2"],
                              font=FONTS["small"], anchor="w", justify="left")
        self._body.pack(anchor="w", pady=(3, 0))

    # ── attaching ─────────────────────────────────────────────────────────

    def attach(self, widget: tk.Misc, tip: virgil.Tip) -> None:
        path = str(widget)
        bound = path in self._tips
        self._tips[path] = tip
        if bound:
            return
        widget.bind("<Enter>", lambda _e, w=widget: self._on_enter(w), add="+")
        widget.bind("<Leave>", lambda _e, w=widget: self._on_leave(w), add="+")
        widget.bind("<Unmap>", lambda _e, w=widget: self._hide_for(w), add="+")
        widget.bind("<Destroy>", lambda e, w=widget: self._on_destroy(e, w),
                    add="+")
        for target in [widget, *_descendants(widget)]:
            for press in _PRESSES:
                target.bind(press, lambda _e, w=widget: self._on_click(w),
                            add="+")

    # ── events ────────────────────────────────────────────────────────────

    def _on_enter(self, widget: tk.Misc) -> None:
        if self._destroyed or self._quiet is widget:
            return
        if self._owner is widget and (self._shown or self._after_id):
            return          # back from one of its own children: already on it
        self._hide()
        self._owner = widget
        self._arm()

    def _on_leave(self, widget: tk.Misc) -> None:
        if self._destroyed or _pointer_inside(widget):
            return          # onto its own child, or the tip under the pointer
        if self._quiet is widget:
            self._quiet = None
        if self._owner is widget:
            self._hide()

    def _on_click(self, widget: tk.Misc) -> None:
        if self._destroyed:
            return
        self._quiet = widget
        self._hide_for(widget)

    def _on_destroy(self, event: "tk.Event[Any]", widget: tk.Misc) -> None:
        if str(event.widget) != str(widget):
            return
        self._tips.pop(str(widget), None)
        if self._quiet is widget:
            self._quiet = None
        self._hide_for(widget)

    # ── the timer ─────────────────────────────────────────────────────────

    def _arm(self) -> None:
        self._cancel()
        self._after_id = self._root_w.after(_DELAY_MS, self._fire)

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self._root_w.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _fire(self) -> None:
        self._cancel()      # by the timer or called directly: nothing stays pending
        widget = self._owner
        if self._destroyed or widget is None:
            return
        tip = self._tips.get(str(widget))
        try:
            ready = (tip is not None and bool(widget.winfo_exists())
                     and bool(widget.winfo_viewable()) and _pointer_inside(widget))
        except tk.TclError:
            ready = False
        words = virgil.text_for(tip) if ready and tip is not None else None
        if words is None:
            self._owner = None
            return
        try:
            self._draw(words)
            self._place()
            self.deiconify()
            self.lift()
            self._shown = True
        except tk.TclError:
            self._owner = None

    # ── drawing ───────────────────────────────────────────────────────────

    def _draw(self, words: virgil.Words) -> None:
        wrap = FONTS["small"].measure("0") * _WRAP_CHARS
        self._title.configure(text=words.title, wraplength=wrap)
        self._body.configure(text=words.body, wraplength=wrap,
                             fg=C["verbatim"] if words.verbatim else C["text2"])

    def _place(self) -> None:
        self.update_idletasks()
        width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        px, py = _pointer_xy(self._root_w)
        x, y = px + _OFFSET_X, py + _OFFSET_Y
        if x + width > self.winfo_screenwidth():
            x = px - _FLIP_GAP - width
        if y + height > self.winfo_screenheight():
            y = py - _FLIP_GAP - height
        self.geometry(f"+{x}+{y}")

    # ── hiding and teardown ───────────────────────────────────────────────

    def _hide_for(self, widget: tk.Misc) -> None:
        if not self._destroyed and self._owner is widget:
            self._hide()

    def _hide(self) -> None:
        self._cancel()
        self._owner = None
        if self._shown:
            self._shown = False
            try:
                self.withdraw()
            except tk.TclError:
                pass

    def destroy(self) -> None:
        self._destroyed = True
        self._cancel()
        self._owner = self._quiet = None
        super().destroy()


def _shared(root: tk.Misc) -> _Tip:
    """The root's one tip, created on first use (or again, if something
    destroyed the last one)."""
    tip = _shared_tips.get(root)
    if tip is None or tip._destroyed:
        tip = _Tip(root)
        _shared_tips[root] = tip
    return tip


def attach(widget: tk.Misc, tip: virgil.Tip) -> None:
    """Give `widget` a hover tooltip showing `tip`'s words. Never raises."""
    try:
        _shared(widget._root()).attach(widget, tip)
    except Exception as exc:
        log.warning("Virgil: could not attach a tooltip to %s (%s)",
                    widget, type(exc).__name__)
