"""
virgil_tooltip.py — The Guide's Voice (the hover tooltip)
==========================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Show a short hover tooltip on any widget, in the words Virgil gives.
             It decides nothing about content: tools/virgil.py turns a tip into
             words, and this module only puts them beside the pointer.

Contract:    attach(widget, tip, when=None) -> None
               `tip` is virgil.module(key) or virgil.term(key). Idempotent per
               widget: attaching again replaces the tip and never stacks a
               second set of handlers. Never raises. The words are asked for
               at show time, so an edited glossary or docstring shows on the
               next hover. `when`, if given, is asked at show time too, and a
               falsy answer means no tip: for a widget that is sometimes
               empty, like Home's status line.
             attach_tag(text, tag, tip) -> None
               The same for a tagged span of a tk.Text, which is not a widget:
               Home's "speak aloud" control and its per-answer stats line.
               Attach to a tag every such span shares, never to a per-span
               tag: those are made for the life of the session, and each
               would cost an entry here.

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
             pointer — as <ButtonPress-1> and its siblings, one per button.
             After a click the widget stays quiet until the pointer has
             really left it.
             Before showing, _fire() checks again that the widget is viewable
             and the pointer inside it, so a missed <Leave> or a view switch
             mid-delay never pops a tip over the wrong screen.

Bindings:    None on the widget itself. attach() puts a bindtag, _TAG, FIRST
             in the widget's bindtags (and _PRESS_TAG first in each
             descendant's), and the handlers are bound once per root, on those
             tags, with bind_class. A widget's own bind() and unbind() act on
             the widget's tag and cannot reach these: Orpheus's stop button
             and Echo's send button unbind <Enter>, <Leave> and <Button-1>
             whenever they go dim, and a tip bound on the widget would go with
             them. First in the list, so a widget handler that returns "break"
             cannot starve the tip either. A panel never has to know a tip is
             there (CONVENTIONS §12, 2026-11-10).
             A span's handlers are tag bindings on the Text, added with
             add="+": Tk offers nothing like a bindtag for a text tag. So the
             one rule left is local to attach_tag(): nothing else may bind on
             the same shared tag without add="+".

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

Upstream:    the panels that call attach() from a tip table (CONVENTIONS §7):
             panels/sidebar.py, the three tab hosts (moderati_panel.py,
             cogitator_panel.py, vox_array_panel.py), hephaestus_panel.py's
             VitalsPanel, Home (vox_controls.py, hestia_panel.py and
             home_panel.py, which also calls attach_tag()), and every other
             panel with a tip table: _TIPS, _ROW_TIPS, _TAB_TIPS,
             _SECTION_TIPS or a _HEADER_TIP. tests/test_virgil_surfaces.py
             lists them all and holds each table to its words; it or the
             panel's own smoke test holds the table to its widgets.
Downstream:  tools/virgil.py (the words), theme.py (C, FONTS).

Requires:    tkinter, logging, typing, weakref (stdlib), tools/virgil.py,
             theme.py.

V0.03:       Walked through 2026-11-18 (the V0.03 walkthrough, CONVENTIONS
             §3), its first audit (born 2026-11-09, #57; bindtags and spans
             #65). One shared window per root, one pending show scheduled on
             the root and cancelled in destroy(), handlers on bindtags that
             no panel's bind() or unbind() can reach, colours from the
             palette, and every Tk error swallowed so a tip never takes a
             panel down. Every widget the Bindings paragraph names as
             rebinding its click still does. Found clean apart from
             Upstream, which names every kind of tip table and Home's
             attach_tag().
"""

from __future__ import annotations

import logging
import tkinter as tk
import weakref
from typing import Any, Callable, NamedTuple, Optional, Union

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
# The click that hides the tip, one sequence per button.
_PRESSES = ("<ButtonPress-1>", "<ButtonPress-2>", "<ButtonPress-3>")

# The bindtags the handlers hang on (see Bindings above). Never bound on a
# widget, so no widget's bind() or unbind() can touch them.
_TAG = "VirgilTip"                # an attached widget
_PRESS_TAG = "VirgilPress"        # an attached widget and its descendants
_SPAN_HOST_TAG = "VirgilSpans"    # a tk.Text with an attached span


class _Span(NamedTuple):
    """A tagged span of a Text: what attach_tag() attaches to."""
    text: tk.Text
    tag: str


_Target = Union[tk.Misc, _Span]
_Key = Union[str, tuple[str, str]]

# root -> its one shared tip. Weak, so a destroyed root takes its tip with it.
_shared_tips: "weakref.WeakKeyDictionary[tk.Misc, _Tip]" = weakref.WeakKeyDictionary()


def _pointer_xy(widget: tk.Misc) -> tuple[int, int]:
    """The pointer, in screen pixels. A function of its own so a test can put
    the pointer anywhere without moving the real one."""
    return widget.winfo_pointerxy()


def _host(target: _Target) -> tk.Misc:
    """The widget a target lives on: itself, or a span's Text."""
    return target.text if isinstance(target, _Span) else target


def _key(target: _Target) -> _Key:
    if isinstance(target, _Span):
        return (str(target.text), target.tag)
    return str(target)


def _pointer_inside(target: _Target) -> bool:
    """Whether the pointer is inside the widget's on-screen rectangle, and,
    for a span, over a character that carries its tag."""
    widget = _host(target)
    try:
        x, y = _pointer_xy(widget)
        left, top = widget.winfo_rootx(), widget.winfo_rooty()
        if not (left <= x < left + widget.winfo_width()
                and top <= y < top + widget.winfo_height()):
            return False
        if isinstance(target, _Span):
            return target.tag in target.text.tag_names(f"@{x - left},{y - top}")
        return True
    except tk.TclError:
        return False


def _add_bindtag(widget: tk.Misc, tag: str) -> None:
    """Put `tag` first in the widget's bindtags, once."""
    tags = widget.bindtags()
    if tag not in tags:
        widget.bindtags((tag, *tags))


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
        self._owner: Optional[_Target] = None     # pending or shown for
        self._quiet: Optional[_Target] = None     # clicked, until the pointer leaves
        self._shown = False
        # A widget's path, or a span's (text path, tag) -> its tip.
        self._tips: dict[_Key, virgil.Tip] = {}
        self._when: dict[_Key, Callable[[], Any]] = {}
        # One _Span object per attached span, so `is` tells two targets apart
        # the way it does for widgets.
        self._spans: dict[tuple[str, str], _Span] = {}

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

        # The handlers, once per root, on the bindtags attach() hands out.
        # bind_class replaces, so a tip rebuilt for the same root (see
        # _shared) takes these over from the one before it.
        root.bind_class(_TAG, "<Enter>", lambda e: self._for_widget(e, self._on_enter))
        root.bind_class(_TAG, "<Leave>", lambda e: self._for_widget(e, self._on_leave))
        root.bind_class(_TAG, "<Unmap>", lambda e: self._for_widget(e, self._hide_for))
        root.bind_class(_TAG, "<Destroy>", self._on_destroy)
        for press in _PRESSES:
            root.bind_class(_PRESS_TAG, press, self._on_press)
        root.bind_class(_SPAN_HOST_TAG, "<Unmap>", self._on_host_unmap)
        root.bind_class(_SPAN_HOST_TAG, "<Destroy>", self._on_host_destroy)

    # ── attaching ─────────────────────────────────────────────────────────

    def attach(self, widget: tk.Misc, tip: virgil.Tip,
               when: Optional[Callable[[], Any]] = None) -> None:
        path = str(widget)
        _add_bindtag(widget, _TAG)
        for target in [widget, *_descendants(widget)]:
            _add_bindtag(target, _PRESS_TAG)
        self._tips[path] = tip
        if when is None:
            self._when.pop(path, None)
        else:
            self._when[path] = when

    def attach_tag(self, text: tk.Text, tag: str, tip: virgil.Tip) -> None:
        key = (str(text), tag)
        if key not in self._spans:
            span = self._spans[key] = _Span(text, tag)
            _add_bindtag(text, _SPAN_HOST_TAG)
            text.tag_bind(tag, "<Enter>", lambda _e: self._on_enter(span), add="+")
            text.tag_bind(tag, "<Leave>", lambda _e: self._on_leave(span), add="+")
            for press in _PRESSES:
                text.tag_bind(tag, press, lambda _e: self._on_click(span), add="+")
        self._tips[key] = tip

    # ── events ────────────────────────────────────────────────────────────

    def _for_widget(self, event: "tk.Event[Any]",
                    handler: Callable[[tk.Misc], None]) -> None:
        """Hand an event on _TAG to its handler, if its widget is attached."""
        widget = event.widget
        if self._destroyed:
            return
        if isinstance(widget, tk.Misc) and str(widget) in self._tips:
            handler(widget)

    def _on_press(self, event: "tk.Event[Any]") -> None:
        """A click on an attached widget or on one of its descendants: find
        the nearest attached widget at or above it."""
        if self._destroyed:
            return
        path = str(event.widget)
        while path and path not in self._tips:
            path = path.rpartition(".")[0]
        if not path:
            return
        try:
            self._on_click(self.nametowidget(path))
        except (KeyError, tk.TclError):
            pass

    def _on_host_unmap(self, event: "tk.Event[Any]") -> None:
        if (not self._destroyed and isinstance(self._owner, _Span)
                and str(self._owner.text) == str(event.widget)):
            self._hide()

    def _on_host_destroy(self, event: "tk.Event[Any]") -> None:
        if self._destroyed:
            return
        path = str(event.widget)
        for key in [k for k in self._spans if k[0] == path]:
            span = self._spans.pop(key)
            self._tips.pop(key, None)
            if self._quiet is span:
                self._quiet = None
            self._hide_for(span)

    def _on_enter(self, widget: _Target) -> None:
        if self._destroyed or self._quiet is widget:
            return
        if self._owner is widget and (self._shown or self._after_id):
            return          # back from one of its own children: already on it
        self._hide()
        self._owner = widget
        self._arm()

    def _on_leave(self, widget: _Target) -> None:
        if self._destroyed or _pointer_inside(widget):
            return          # onto its own child, or the tip under the pointer
        if self._quiet is widget:
            self._quiet = None
        if self._owner is widget:
            self._hide()

    def _on_click(self, widget: _Target) -> None:
        if self._destroyed:
            return
        self._quiet = widget
        self._hide_for(widget)

    def _on_destroy(self, event: "tk.Event[Any]") -> None:
        """An attached widget is going. Matched by path, not by object: this
        late in a teardown Tk can hand over a name tkinter no longer knows."""
        if self._destroyed:
            return
        path = str(event.widget)
        self._tips.pop(path, None)
        self._when.pop(path, None)
        if self._quiet is not None and _key(self._quiet) == path:
            self._quiet = None
        if self._owner is not None and _key(self._owner) == path:
            self._hide()

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
        key = _key(widget)
        tip = self._tips.get(key)
        host = _host(widget)
        try:
            ready = (tip is not None and bool(host.winfo_exists())
                     and bool(host.winfo_viewable()) and _pointer_inside(widget)
                     and self._wanted(key))
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

    def _wanted(self, key: _Key) -> bool:
        """The answer of the tip's `when`, if it has one. One that raises
        means no tip, like every other miss."""
        when = self._when.get(key)
        if when is None:
            return True
        try:
            return bool(when())
        except Exception:
            return False

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

    def _hide_for(self, widget: _Target) -> None:
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


def attach(widget: tk.Misc, tip: virgil.Tip,
           when: Optional[Callable[[], Any]] = None) -> None:
    """Give `widget` a hover tooltip showing `tip`'s words. With `when`, the
    tip shows only while when() is truthy. Never raises."""
    try:
        _shared(widget._root()).attach(widget, tip, when)
    except Exception as exc:
        log.warning("Virgil: could not attach a tooltip to %s (%s)",
                    widget, type(exc).__name__)


def attach_tag(text: tk.Text, tag: str, tip: virgil.Tip) -> None:
    """Give every span of `text` tagged `tag` a hover tooltip. Never raises."""
    try:
        _shared(text._root()).attach_tag(text, tag, tip)
    except Exception as exc:
        log.warning("Virgil: could not attach a tooltip to the tag %r of %s (%s)",
                    tag, text, type(exc).__name__)
