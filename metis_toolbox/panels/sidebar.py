"""
sidebar.py — Felhaven's navigation rail
=======================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Draw the left-hand nav: a clickable row per view, and group headers.
             A group header expands to show indented child rows. A click on a row
             calls the on_select callback (felhaven switches views); set_active
             lights the chosen row. Rows are dumb display surfaces — no timing
             of their own.

Groups:      A name listed in _GROUPS is a header, NOT a view — clicking it only
             expands/collapses its children and never calls on_select, so
             felhaven._views needs no entry for it. Children are ordinary
             SidebarRows (same dot, same lit-border active state) that happen to
             be indented, so a grouped view stays a first-class destination with
             the whole content area — not a tab inside a host panel. Groups open
             collapsed (§7), which keeps the rail short as more views join a
             group.

Scrolling:   The rows sit in a frame inside a canvas, with a PhosphorScroll
             lane on the right (§7's _ScrollFrame pattern, built in here
             rather than copied, because the rail IS the scrolled thing). At
             the default 960 x 620 window the rail is taller than the window
             once Navigation is open, and fonts grow with the window's height,
             so a bigger window doesn't fix that. The mouse wheel scrolls it
             while the pointer is over it, and set_active() scrolls the lit
             row into view.

Special:     The "aura" row is registered with Kairos (felhaven.py) and shows the
             live temperature in its subtitle; its update(data) is swapped for
             _aura_update at construction. The other rows keep the no-op base
             update() — only aura carries live data today.

Upstream:    felhaven.py — constructs Sidebar(on_select=_show_view), registers
             row("aura") with Kairos, and calls set_active()/rescale().
Downstream:  themis.py (temperature_unit at render time), theme.py (colors/fonts).

Requires:    tkinter (stdlib).

V0.02: Audited 2026-07-25 — added this header docstring (the module had none) and
       a smoke test; verified honest, no logic change.
"""

import tkinter as tk

import themis
from panels import virgil_tooltip
from theme import C, FONTS, PhosphorScroll
from tools import virgil


class SidebarRow(tk.Frame):
    def __init__(self, parent, name: str, label: str, subtitle: str, on_click):
        super().__init__(parent, bg=C["bg"],
                         highlightbackground=C["border"], highlightthickness=2)

        inner = tk.Frame(self, bg=C["bg"])
        inner.pack(side="left", fill="both", expand=True, padx=(8, 8), pady=6)

        self._dot = tk.Canvas(inner, width=8, height=8, bg=C["bg"], highlightthickness=0)
        self._dot_item = self._dot.create_oval(1, 1, 7, 7, fill=C["amber"], outline="")
        self._dot.pack(side="left", padx=(0, 8))

        text_frame = tk.Frame(inner, bg=C["bg"])
        text_frame.pack(side="left", fill="both", expand=True)

        self._label_lbl = tk.Label(
            text_frame, text=label, font=FONTS["small_bold"],
            fg=C["text2"], bg=C["bg"], anchor="w",
        )
        self._label_lbl.pack(anchor="w")

        self._subtitle_lbl = tk.Label(
            text_frame, text=subtitle, font=FONTS["tiny"],
            fg=C["text3"], bg=C["bg"], anchor="w",
        )
        self._subtitle_lbl.pack(anchor="w")

        _cb = lambda e, n=name: on_click(n)
        for w in (self, inner, self._dot, text_frame,
                  self._label_lbl, self._subtitle_lbl):
            w.bind("<Button-1>", _cb)
            w.configure(cursor="hand2")

    def set_active(self, active: bool) -> None:
        if active:
            self._dot.itemconfigure(self._dot_item, fill=C["green"])
            self._label_lbl.configure(fg=C["text1"])
            self.configure(highlightbackground=C["green"])    # active border lights up too
        else:
            self._dot.itemconfigure(self._dot_item, fill=C["amber"])
            self._label_lbl.configure(fg=C["text2"])
            self.configure(highlightbackground=C["border"])

    def _set_subtitle(self, text: str) -> None:
        self._subtitle_lbl.configure(text=text)

    def update(self, data) -> None:
        pass


class GroupRow(tk.Frame):
    """A section header for a group of child rows. Selects nothing — a click
    only toggles its children.

    Carries the same bordered-card chrome as a SidebarRow so the rail reads as
    one column, but stays distinguishable where it counts: a ▼/▶ glyph in the
    slot SidebarRow gives its dot, and a label that never lights C["green"]
    because it selects nothing.
    """

    def __init__(self, parent, name: str, label: str, subtitle: str, on_toggle):
        super().__init__(parent, bg=C["bg"],
                         highlightbackground=C["border"], highlightthickness=2)

        inner = tk.Frame(self, bg=C["bg"])
        inner.pack(side="left", fill="both", expand=True, padx=(8, 8), pady=6)

        self._glyph = tk.Label(inner, text="▶", font=FONTS["tiny"],
                               fg=C["text3"], bg=C["bg"])
        self._glyph.pack(side="left", padx=(0, 8))

        text_frame = tk.Frame(inner, bg=C["bg"])
        text_frame.pack(side="left", fill="both", expand=True)

        self._label_lbl = tk.Label(
            text_frame, text=label, font=FONTS["small_bold"],
            fg=C["text2"], bg=C["bg"], anchor="w",
        )
        self._label_lbl.pack(anchor="w")

        subtitle_lbl = tk.Label(
            text_frame, text=subtitle, font=FONTS["tiny"],
            fg=C["text3"], bg=C["bg"], anchor="w",
        )
        subtitle_lbl.pack(anchor="w")

        # §7 clickable-row idiom: hand cursor, amber on hover, back on leave.
        # Every widget in the row is bound, so the hover doesn't drop out when
        # the pointer crosses from a frame onto a label. Tk delivers the
        # <Leave>/<Enter> pair before the next idle redraw, so the momentary
        # "off" between them never reaches the screen.
        _cb = lambda e, n=name: on_toggle(n)
        for w in (self, inner, self._glyph, text_frame,
                  self._label_lbl, subtitle_lbl):
            w.bind("<Button-1>", _cb)
            w.bind("<Enter>", lambda e: self._set_hover(True))
            w.bind("<Leave>", lambda e: self._set_hover(False))
            w.configure(cursor="hand2")

    def _set_hover(self, on: bool) -> None:
        self._glyph.configure(fg=C["amber"] if on else C["text3"])
        self._label_lbl.configure(fg=C["amber"] if on else C["text2"])

    def set_expanded(self, expanded: bool) -> None:
        self._glyph.configure(text="▼" if expanded else "▶")


class Sidebar(tk.Frame):
    BASE_WIDTH  = 200   # px of rows at scale 1.0; rescale() grows it to match the fonts.
                        # The scroll lane (PhosphorScroll.LANE, never scaled) rides on
                        # top, in the gap felhaven used to leave with padx.
    BASE_INDENT = 14    # px at scale 1.0; child-row inset, matches Card's padx
    ROW_PAD     = 3     # px above and below each row (pady), kept in view by _reveal

    _ROWS = [
        ("felhaven",   "Felhaven",        "Home"),
        ("horai",      "Chronometry",     "Temporal Awareness"),
        ("moderati",   "Moderati",        "Vitals · Logs"),
        ("aura",       "Atmospherics",    "—°F Weather"),
        ("navigation", "Navigation",      "Charts · Maps"),   # group header — NOT a view
        ("midas",      "Dynastic Vault",  "Finance"),
        ("pheme",      "Scriptorium",     "Rumors"),
        ("morpheus",   "Vox Array",       "Audio"),
        ("cogitator",  "Cogitator",       "Tasks · Calc · Convert"),
    ]

    # A name in _ROWS that also appears here is a group header rather than a
    # view; its value is the child rows, in the same (name, label, subtitle)
    # shape. Each child IS a full felhaven view — add one here and one to
    # felhaven._views (test_sidebar_view_parity asserts the two agree).
    _GROUPS = {
        "navigation": [
            ("hypatia", "Celestarium", "Star Map"),
            ("artemis", "Cartograph",  "Local Map"),
        ],
    }

    # Each row's hover tooltip (Virgil). The rail says what a PLACE is, so
    # every tip is a glossary entry, never a module gloss. One per row and
    # group header in _ROWS and _GROUPS; test_virgil_surfaces holds the two
    # tables in lockstep. Home is `home`, because `felhaven` names a module.
    _TIPS = {
        "felhaven":   virgil.term("home"),
        "horai":      virgil.term("chronometry"),
        "moderati":   virgil.term("moderati"),
        "aura":       virgil.term("atmospherics"),
        "navigation": virgil.term("navigation"),
        "hypatia":    virgil.term("celestarium"),
        "artemis":    virgil.term("cartograph"),
        "midas":      virgil.term("dynastic_vault"),
        "pheme":      virgil.term("scriptorium"),
        "morpheus":   virgil.term("vox_array"),
        "cogitator":  virgil.term("cogitator"),
    }

    def __init__(self, parent, on_select):
        super().__init__(parent, bg=C["bg"],
                         width=self.BASE_WIDTH + PhosphorScroll.LANE)
        self.pack_propagate(False)
        self._indent = self.BASE_INDENT

        # The scroll frame. width/height=1: a bare Canvas asks for 265 px,
        # and that fiction would reach the geometry manager (see
        # PhosphorScroll). The rail's width is set by this frame, not by it.
        self._canvas = tk.Canvas(self, bg=C["bg"], highlightthickness=0, bd=0,
                                 width=1, height=1)
        self._scroll = PhosphorScroll(self, command=self._canvas.yview, bg=C["bg"])
        self._canvas.configure(yscrollcommand=self._scroll.set)
        self._scroll.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)
        self._inner = tk.Frame(self._canvas, bg=C["bg"])
        self._win = self._canvas.create_window((0, 0), window=self._inner,
                                               anchor="nw")
        self._inner.bind("<Configure>", lambda e: self._sync_region())
        self._canvas.bind("<Configure>", self._on_canvas_config)
        # §7: the wheel is bound only while the pointer is over the rail, so
        # it never fights a panel's own scroll frame for the same events.
        self._canvas.bind("<Enter>", self._bind_wheel)
        self._canvas.bind("<Leave>", self._unbind_wheel)

        self._rows:        dict[str, SidebarRow] = {}   # selectable rows only
        self._groups:      dict[str, GroupRow]   = {}
        self._group_boxes: dict[str, tk.Frame]   = {}
        self._expanded:    dict[str, bool]       = {}
        self._parent_of:   dict[str, str]        = {}   # child name -> group name

        rail = self._inner
        for name, label, subtitle in self._ROWS:
            if name in self._GROUPS:
                self._groups[name] = GroupRow(rail, name, label, subtitle,
                                              self._toggle_group)
                # Children are packed into this box ONCE and never re-packed;
                # collapsing forgets the box, which leaves them untouched inside.
                box = tk.Frame(rail, bg=C["bg"])
                self._group_boxes[name] = box
                # Collapsed on open (§7). set_expanded keeps the glyph in step
                # with the flag, so the two can't drift if this default changes.
                self._expanded[name] = False
                self._groups[name].set_expanded(False)
                for c_name, c_label, c_sub in self._GROUPS[name]:
                    row = SidebarRow(box, c_name, c_label, c_sub, on_select)
                    row.pack(fill="x", pady=self.ROW_PAD, padx=(self._indent, 0))
                    self._rows[c_name] = row
                    self._parent_of[c_name] = name
            else:
                self._rows[name] = SidebarRow(rail, name, label, subtitle, on_select)

        for name, tip in self._TIPS.items():
            row = self._rows.get(name) or self._groups.get(name)
            if row is not None:
                virgil_tooltip.attach(row, tip)

        self._relayout()

        aura_row = self._rows["aura"]

        def _aura_update(data, _row=aura_row):
            unit = themis.temperature_unit()          # "F" or "C"
            if data is None:
                _row._set_subtitle(f"—°{unit} Weather")
            else:
                temp = data["temp_c"] if unit == "C" else data["temp_f"]
                _row._set_subtitle(f"{temp}°{unit} Weather")

        aura_row.update = _aura_update

    def _relayout(self) -> None:
        """Re-pack the rail top-to-bottom in _ROWS order.

        Forget-all-then-pack, the same idiom felhaven._show_view uses. A bare
        pack() on a re-expanded group box would append it to the BOTTOM of the
        rail rather than back into its slot, so the whole rail is rebuilt in
        declaration order instead. Only direct children of the rail are
        touched — the child rows live inside their group box and are never
        disturbed."""
        for w in self._inner.pack_slaves():
            w.pack_forget()
        for name, _label, _subtitle in self._ROWS:
            if name in self._GROUPS:
                # pady gives the bordered cards breathing room; the header takes
                # the same spacing so the rail keeps one rhythm.
                self._groups[name].pack(fill="x", pady=self.ROW_PAD)
                if self._expanded[name]:
                    self._group_boxes[name].pack(fill="x")
            else:
                self._rows[name].pack(fill="x", pady=self.ROW_PAD)

    def _toggle_group(self, name: str) -> None:
        self._expanded[name] = not self._expanded[name]
        self._groups[name].set_expanded(self._expanded[name])
        self._relayout()

    # ── scrolling ────────────────────────────────────────────────────────────

    def _sync_region(self) -> None:
        """Scroll region = the rail's requested size. Read from the request,
        not bbox("all"), so it is right before the inner frame's <Configure>
        has been delivered (set_active reads it straight after a relayout)."""
        self._canvas.configure(scrollregion=(
            0, 0, self._inner.winfo_reqwidth(), self._inner.winfo_reqheight()))

    def _on_canvas_config(self, event) -> None:
        self._canvas.itemconfigure(self._win, width=event.width)

    def _bind_wheel(self, _event) -> None:
        self._canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event) -> None:
        self._canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event) -> None:
        self._canvas.yview_scroll(int(-event.delta / 120), "units")

    def _reveal(self, widget: tk.Widget) -> None:
        """Scroll just far enough that `widget` (a row on the rail) is fully
        in view. Does nothing when the whole rail fits."""
        self.update_idletasks()
        self._sync_region()
        total = self._inner.winfo_reqheight()
        view = self._canvas.winfo_height()
        if view <= 1 or total <= view:
            return
        top = 0
        w = widget
        while w is not self._inner:        # a child row sits inside its group box
            top += w.winfo_y()
            w = w.master
        # Keep the row's padding in view too, so the first row scrolls all the
        # way back to the top of the rail rather than stopping ROW_PAD short.
        bottom = top + widget.winfo_height() + self.ROW_PAD
        top = max(0, top - self.ROW_PAD)
        shown = self._canvas.canvasy(0)    # rail y at the top of the view
        if top < shown:
            self._canvas.yview_moveto(top / total)
        elif bottom > shown + view:
            self._canvas.yview_moveto((bottom - view) / total)

    def rescale(self, scale: float) -> None:
        """Grow the fixed sidebar width in lockstep with the fonts so the nav
        labels never outgrow their box. Uses the same scale theme.rescale_fonts
        applied to the fonts, so the text-to-box fit stays constant at any size.
        The child-row indent scales with it so the nesting stays legible."""
        self.configure(width=int(round(self.BASE_WIDTH * scale)) + PhosphorScroll.LANE)
        self._indent = int(round(self.BASE_INDENT * scale))
        for name in self._parent_of:
            self._rows[name].pack_configure(padx=(self._indent, 0))

    def set_active(self, name: str) -> None:
        """Light the named row. Expands its group first if collapsed, then
        scrolls it into view — the lit row must never be hidden."""
        parent = self._parent_of.get(name)
        if parent is not None and not self._expanded[parent]:
            self._expanded[parent] = True
            self._groups[parent].set_expanded(True)
            self._relayout()
        for row_name, row in self._rows.items():
            row.set_active(row_name == name)
        self._reveal(self._rows[name])

    def row(self, name: str) -> SidebarRow:
        return self._rows[name]
