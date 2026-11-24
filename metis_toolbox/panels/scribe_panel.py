"""
Scribe panel — Tkinter widget for the Felhaven dashboard.

Job:         Keep a task checklist and a scratch notes area.

ScribePanel(tk.Frame) — tasks input + checklist above, NotesWidget below.
NotesWidget           — embedded scratch text area (same pattern as AmmitWidget).

ScribePanel receives `data` (shared dict) and an `on_save` callback; does not
touch persistence directly. A bare Frame tab body inside CogitatorPanel (the
SCRIBE tab); the open-count readout that used to sit in the Card header now
lives in the body.

The panel owns all task/note mutation (add/toggle/delete/edit) directly on the
shared dict; scribe.py is persistence-only (load_data/save_data). This is the
single live mutation path — scribe's unused parallel tool interface was deleted
in the V0.02 audit.

Layout:      The task list sits in a ScrollColumn as tall as its rows, or as
             what is left once the count, the entry and the notes' four lines
             are placed, whichever is less; past that it scrolls, and a task
             just added is scrolled into view (§7: what must stay visible is
             never what gets squeezed). Before, rows past about ten ran off the
             bottom of the 488 x 330 tab (Felhaven's 760 x 500 minimum) and the
             notes shrank to nothing first. A row packs its ✕ before its text,
             so a long task is cut short rather than its ✕ pushed off the row.

Notes:       Every change saves. <<Modified>> hears what <KeyRelease> missed:
             a middle-click paste (Tk's <<PasteSelection>>) edits with no key.
             The notes loaded at startup are not on the undo stack, so Ctrl+Z
             can't erase them; one Ctrl+Z used to, and the empty text was
             saved half a second later (both measured, 2026-11-21).

Saving:      felhaven's _flush_save calls show_saved() with save_data()'s answer.
             A save that failed turns the count line red, "3 open · not saved,
             see the log", until a save works (the owner's pick, 2026-11-21);
             before, only the log knew, and the notes were lost at the next
             start.

Upstream:    panels/cogitator_panel.py (the SCRIBE tab; it passes felhaven's
             shared dict and its debounced save, felhaven._schedule_save);
             felhaven.py (show_saved, after each save).
Downstream:  theme.py (C, FONTS, ScrollColumn), tools/virgil.py and
             panels/virgil_tooltip.py (the row glyphs' tips). Persistence is
             scribe.py's, reached only through on_save.

Requires:    tkinter (stdlib).

2026-07-25:  The first audit changed no logic; it documented the
             panel-owns-mutation split after scribe.py's dead tool interface
             was removed.

V0.03: Walked through 2026-11-21 (in the repo's first commit, 2026-06-10;
       first audited 2026-07-25). Four behaviour changes, each measured
       first: the task list scrolls (the owner's pick), a long task keeps its
       ✕ (a 200-character task hid it, so the task couldn't be deleted), the
       loaded notes are off the undo stack, and a paste with no key is
       saved. The text box and entry stop repeating the theme's cursor
       colour (§7), and the entry drops a highlight colour its zero-width
       ring never drew. Layout, Notes, Upstream, Downstream and Requires
       added. Later that day (S25, the owner's pick): Saving, and
       show_saved().
"""

import tkinter as tk

from panels import virgil_tooltip
from theme import C, FONTS, ScrollColumn
from tools import virgil

# The notes keep at least this many lines when the task list grows; the list
# scrolls instead (see Layout).
_NOTES_MIN_LINES = 4
# The entry's pady, (6, 8): space _fit counts as taken.
_ENTRY_PADY = (6, 8)
# However cramped the tab, the list keeps this much room: about one row.
_LIST_MIN = 24


# ─────────────────────────────────────────────────────────────────────────────
#  NotesWidget — embedded scratch text area
# ─────────────────────────────────────────────────────────────────────────────

class NotesWidget(tk.Frame):
    def __init__(self, parent, data: dict, on_save):
        super().__init__(parent, bg=C["card"])
        self._data    = data
        self._on_save = on_save

        # Divider
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", pady=(8, 6))

        # Header
        tk.Label(self, text="NOTES — SCRATCH", font=FONTS["card_header"],
                 fg=C["text3"], bg=C["card"], anchor="w").pack(fill="x")

        self._text = tk.Text(
            self, font=FONTS["body"], bg=C["card"], fg=C["text1"],
            relief="flat", wrap="word", highlightthickness=0, undo=True,
            height=_NOTES_MIN_LINES,
        )
        self._text.pack(fill="both", expand=True, pady=(4, 0))
        self._text.insert("1.0", data.get("notes", ""))
        # The loaded notes are where this session starts, not an edit: off the
        # undo stack, so Ctrl+Z can't erase them, and the modified flag clear,
        # so the first real edit is heard (see the module docstring's Notes).
        self._text.edit_reset()
        self._text.edit_modified(False)
        self._text.bind("<<Modified>>", self._on_modified)

    def _on_modified(self, e=None):
        """Any change (a key, a paste, an undo) sets the modified flag and
        fires <<Modified>> once. Clearing the flag arms it for the next change;
        the clear fires <<Modified>> too, which finds the flag down and stops."""
        if not self._text.edit_modified():
            return
        self._text.edit_modified(False)
        self._data["notes"] = self._text.get("1.0", "end-1c")
        self._on_save(self._data)


# ─────────────────────────────────────────────────────────────────────────────
#  ScribePanel — tasks + embedded notes
# ─────────────────────────────────────────────────────────────────────────────

class ScribePanel(tk.Frame):
    # Hover tooltips (Virgil) for the two glyphs each task row builds, each a
    # glossary entry (CONVENTIONS §7). _render attaches them.
    _ROW_TIPS = {
        "check": virgil.term("task_check"),
        "delete": virgil.term("task_delete"),
    }

    def __init__(self, parent, data: dict, on_save):
        super().__init__(parent, bg=C["card"])
        self._data    = data
        self._on_save = on_save

        # Open-count readout — was the Card header ("SCRIBE — N OPEN"); now a
        # body label, since Cogitator's shared header can't carry per-tab state.
        self._status_lbl = tk.Label(self, text="0 open", font=FONTS["card_header"],
                                    fg=C["text3"], bg=C["card"], anchor="w")
        self._status_lbl.pack(fill="x")

        self._entry = tk.Entry(
            self, font=FONTS["body"], bg=C["card"], fg=C["text3"],
            relief="flat", highlightthickness=0
        )
        self._entry.insert(0, "+ new task...")
        self._entry.pack(fill="x", pady=_ENTRY_PADY)
        self._entry.bind("<Return>",   self._add)
        self._entry.bind("<FocusIn>",  self._clear_ph)
        self._entry.bind("<FocusOut>", self._show_ph)

        # The task list scrolls once it would squeeze the notes (see Layout).
        self._list = ScrollColumn(self)
        self._list.pack(fill="x")
        self._list_frame = self._list.body
        self._show_new = False      # set by _add: scroll the new row into view
        self._save_failed = False   # set by show_saved (see Saving)

        self._notes = NotesWidget(self, data, on_save)
        self._notes.pack(fill="both", expand=True)

        self.bind("<Configure>", self._fit, add="+")
        self._list_frame.bind("<Configure>", self._on_list_resized, add="+")

        self._render()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _fit(self, e=None):
        """The list is as tall as its rows, or as what is left once the count,
        the entry and the notes' minimum are placed, whichever is less."""
        taken = (self._status_lbl.winfo_reqheight() + self._entry.winfo_reqheight()
                 + sum(_ENTRY_PADY) + self._notes.winfo_reqheight())
        room = self.winfo_height() - taken
        want = self._list_frame.winfo_reqheight()
        # The floor is on the room, never on the rows (the Cartograph's rule):
        # a short list must not get blank space under it.
        height = min(want, max(_LIST_MIN, room)) if room > 1 else want
        self._list.set_size(height=height)

    def _on_list_resized(self, e=None):
        """The rows changed. ScrollColumn's own binding has already set the
        scroll region, so a task just added can be scrolled to. The list is
        resized more than once while _render rebuilds it, so the scroll is
        settled only once the last row, the new one, is laid out."""
        self._fit()
        if self._show_new:
            self._list.scroll_to_end()
            rows = self._list_frame.winfo_children()
            if rows and rows[-1].winfo_ismapped():
                self._show_new = False

    # ── Placeholder helpers ───────────────────────────────────────────────────

    def _clear_ph(self, e):
        if self._entry.get() == "+ new task...":
            self._entry.delete(0, "end")
            self._entry.config(fg=C["text1"])

    def _show_ph(self, e):
        if not self._entry.get().strip():
            self._entry.delete(0, "end")
            self._entry.insert(0, "+ new task...")
            self._entry.config(fg=C["text3"])

    # ── Task actions ──────────────────────────────────────────────────────────

    def _add(self, e=None):
        text = self._entry.get().strip()
        if not text or text == "+ new task...":
            return
        self._data["tasks"].append({"text": text, "done": False})
        self._entry.delete(0, "end")
        self._save()
        self._show_new = True
        self._render()

    def _toggle(self, idx):
        self._data["tasks"][idx]["done"] = not self._data["tasks"][idx]["done"]
        self._save()
        self._render()

    def _delete(self, idx):
        self._data["tasks"].pop(idx)
        self._save()
        self._render()

    def _save(self):
        self._on_save(self._data)

    def show_saved(self, saved: bool) -> None:
        """Whether the last save reached the disk (felhaven's _flush_save). A
        failure stays on the count line until a save works (see Saving)."""
        self._save_failed = not saved
        self._show_count()

    def _show_count(self):
        open_count = sum(1 for t in self._data["tasks"] if not t["done"])
        if self._save_failed:
            self._status_lbl.config(text=f"{open_count} open · not saved, see the log",
                                    fg=C["red"])
        else:
            self._status_lbl.config(text=f"{open_count} open", fg=C["text3"])

    def _render(self):
        for w in self._list_frame.winfo_children():
            w.destroy()
        self._show_count()

        if not self._data["tasks"]:
            tk.Label(
                self._list_frame, text="no tasks bound",
                font=FONTS["small_italic"], fg=C["text3"], bg=C["card"]
            ).pack(anchor="w")
            return

        for i, task in enumerate(self._data["tasks"]):
            row = tk.Frame(self._list_frame, bg=C["card"])
            row.pack(fill="x", pady=1)

            check_text  = "☑" if task["done"] else "☐"
            check_color = C["amber"] if task["done"] else C["text3"]
            chk = tk.Label(row, text=check_text, font=FONTS["medium"],
                           fg=check_color, bg=C["card"], cursor="hand2")
            chk.pack(side="left", padx=(0, 6))
            chk.bind("<Button-1>", lambda e, idx=i: self._toggle(idx))
            virgil_tooltip.attach(chk, self._ROW_TIPS["check"])

            # The ✕ is packed before the text: pack squeezes the last-packed
            # first, so a long task is cut short and its ✕ stays on the row.
            x_btn = tk.Label(row, text="✕", font=FONTS["tiny"],
                             fg=C["text3"], bg=C["card"], cursor="hand2")
            x_btn.pack(side="right")
            x_btn.bind("<Button-1>", lambda e, idx=i: self._delete(idx))
            x_btn.bind("<Enter>",    lambda e, w=x_btn: w.config(fg=C["red"]))
            x_btn.bind("<Leave>",    lambda e, w=x_btn: w.config(fg=C["text3"]))
            virgil_tooltip.attach(x_btn, self._ROW_TIPS["delete"])

            txt_color = C["text3"] if task["done"] else C["text2"]
            font      = FONTS["body_strike"] if task["done"] else FONTS["body"]
            tk.Label(row, text=task["text"], font=font, fg=txt_color,
                     bg=C["card"], anchor="w").pack(side="left", fill="x", expand=True)
