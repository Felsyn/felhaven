"""
Queue panel — the Auto-DJ queue window.
=======================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Show the songs Auto-DJ has lined up, in play order, and let one be
             taken off. That is the whole job: this module never chooses a song,
             never plays one, and never asks where the list came from.

Placement:   A tk.Toplevel, the RiteLog precedent (CONVENTIONS §12), and for the
             same two reasons plus one of its own. Non-modal and grab-free: the
             transcript behind it stays scrollable, and a queue you can only
             read with the rest of the app frozen is worse than no queue. The
             third reason is space — the status row it hangs off had about 20 px
             spare at the old 700 px minimum (home_panel.py, *Readouts are packed
             FIRST*), so the ▶/▼ section toggle used elsewhere has nowhere to
             expand into, and expanding downward would push a body between the
             status line and the input box that §7 keeps clear.

Contract:    QueueBox(parent, rows, on_remove)
               rows      — awen.queue_view()'s output: [{"id", "name"}, ...],
                           play order, already named and already a snapshot.
               on_remove — called with one id. Returns True if it was still
                           there; False is ordinary, not an error (the song
                           started while the box was open).
             show(rows) redraws. The caller decides when, which is what keeps
             every timer out of here.

Pure display: it takes rows and a callback and imports NOTHING from awen. The
             RiteLog discipline — a window that took a store would have to know
             when the store changed, and then it would want a timer. A test can
             hand it a list and a lambda.

Single:      HomePanel keeps one and lifts it rather than opening a second.
             RiteLog allows several because each shows a different frozen turn;
             every one of these would show the same live list, and each would
             need refreshing.

Removing:    ONE click, no confirm. §6 does permit the two-click arm/disarm with
             a one-shot after() (MorpheusPanel's remove glyphs), and it is the
             wrong trade here: the action undoes itself in twenty seconds when
             the DJ tops the queue back up, so a confirm costs more than the
             mistake. The row turns red and grows a ✕ on hover, which says
             "this destroys" before the click rather than after.

             Red rather than §7's amber because the click DESTROYS. Amber is
             the informational channel, and this row is not informing.

Upstream:    panels/home_panel.py (opens and refreshes it)
Downstream:  theme.py (colors, fonts)

Requires:    tkinter (stdlib). No pip dependency.
"""

import tkinter as tk
from typing import Any, Callable, Optional

from theme import C, FONTS

# Nothing lined up is a STATE, not a reason to close. A queue draining while the
# box is open must not make the window vanish under the pointer.
_EMPTY_LINE = "Nothing lined up yet."


class QueueBox(tk.Toplevel):

    def __init__(self, parent: tk.Widget,
                 rows: "list[dict[str, Any]]",
                 on_remove: "Callable[[int], bool]") -> None:
        super().__init__(parent, bg=C["bg"])
        self._on_remove = on_remove
        self._body: Optional[tk.Frame] = None
        # id -> its row, rebuilt by every show(). The RiteLog `self._rows`
        # idiom: a click has to name an entry, and an index cannot — the worker
        # pops from the head while this window is open.
        self._rows: "dict[int, tk.Frame]" = {}

        self.title("FELHAVEN — AUTO-DJ QUEUE")
        self.geometry("420x260")
        self.minsize(300, 160)
        self.transient(parent.winfo_toplevel())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._head = tk.Label(self, text="", font=FONTS["small_bold"],
                              fg=C["text1"], bg=C["bg"], anchor="w")
        self._head.pack(fill="x", padx=10, pady=(10, 6))
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", padx=10)

        self._holder = tk.Frame(self, bg=C["bg"])
        self._holder.pack(fill="both", expand=True, padx=10, pady=(6, 4))

        tk.Label(self, text="click a song to drop it", font=FONTS["tiny"],
                 fg=C["text3"], bg=C["bg"], anchor="w"
                 ).pack(fill="x", padx=10, pady=(0, 8))

        self.show(rows)

    # ── Drawing ──────────────────────────────────────────────────────────────

    def show(self, rows: "list[dict[str, Any]]") -> None:
        """Redraw from a fresh snapshot.

        Rebuilt from scratch rather than diffed (§7: rebuild rows if that is
        simplest). At most a handful of rows, and a diff would need row identity
        kept in step with a list another thread is popping from — which is the
        bug this window exists to make visible, not one to import into it.
        """
        if self._body is not None:
            self._body.destroy()
        self._body = tk.Frame(self._holder, bg=C["bg"])
        self._body.pack(fill="both", expand=True)
        self._rows = {}

        count = len(rows)
        self._head.config(text=f"{count} song{'' if count == 1 else 's'} lined up"
                          if count else "AUTO-DJ QUEUE")

        if not rows:
            tk.Label(self._body, text=_EMPTY_LINE, font=FONTS["small"],
                     fg=C["text3"], bg=C["bg"], anchor="w").pack(fill="x")
            return

        for position, row in enumerate(rows, start=1):
            self._build_row(self._body, position, row)

    def _build_row(self, parent: tk.Widget, position: int,
                   row: "dict[str, Any]") -> None:
        entry_id = int(row.get("id") or 0)
        frame = tk.Frame(parent, bg=C["bg"])
        frame.pack(fill="x", pady=1)
        self._rows[entry_id] = frame

        name = tk.Label(frame, text=f"{position}.  {row.get('name') or '?'}",
                        font=FONTS["small"], fg=C["text1"], bg=C["bg"],
                        anchor="w")
        name.pack(side="left", fill="x", expand=True)
        cross = tk.Label(frame, text=" ", font=FONTS["small"], fg=C["red"],
                         bg=C["bg"])
        cross.pack(side="right")

        def enter(_e: "tk.Event[Any]") -> None:
            name.config(fg=C["red"], cursor="hand2")
            cross.config(text="✕")

        def leave(_e: "tk.Event[Any]") -> None:
            name.config(fg=C["text1"], cursor="")
            cross.config(text=" ")

        # Every child, not just the label. Users aim at the text, and a row that
        # only reacts on part of itself reads as broken (the sidebar pattern,
        # and horai_panel's section header).
        for widget in (frame, name, cross):
            widget.bind("<Button-1>", lambda _e, i=entry_id: self._drop(i))
            widget.bind("<Enter>", enter)
            widget.bind("<Leave>", leave)

    # ── Acting ───────────────────────────────────────────────────────────────

    def _drop(self, entry_id: int) -> None:
        """Ask for one entry to be dropped, then stop.

        It does NOT redraw. The caller's refresh watches awen.queue_revision()
        and repaints within a frame, so redrawing here would race that for no
        gain and would make this window the second thing deciding what the
        queue looks like.

        A False return is ignored on purpose: it means the DJ started that song
        while the box was open, and the next refresh drops the row anyway. A
        dialog to report that would be telling the operator off for the timing
        of their own click.
        """
        self._on_remove(entry_id)
