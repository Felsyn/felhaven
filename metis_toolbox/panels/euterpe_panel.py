"""
panels/euterpe_panel.py — the TUNES tab inside Vox Array
==========================================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Draw Euterpe's song ledger as plain editable JSON, with SAVE and
             RELOAD. The text box is pre-filled from euterpe.raw_text(). Its ONE
             job is to turn edits into euterpe.replace_all() calls — every
             validation rule and the write itself live in tools/euterpe.py,
             and this module never opens euterpe_ledger.json directly.

Why it       The ledger is what builds out the music taste: taste_anchor()
exists:      returns the last few keys whose source is "user", and nothing
             else steers a DJ pick. So a row attributed to the wrong side is
             not noise, it is the taste. On 2026-09-16 a bug wrote eight of
             the DJ's own picks as the listener's, one of them a gear manual,
             and it steered a whole evening; the only recourse was a script
             and a hand-made backup file. This is that recourse, in the app.

             As of 2026-09-17 the file is the listener's list outright — Awen
             reads it and no longer writes to it at all (awen.py, "Reads, does
             not write"). This tab is therefore the ONLY way a song gets into
             the taste other than playing it, which is why it can add rows and
             not only delete them.

Raw JSON,    A row list with a delete cross would be safer to click and could
not rows:    not express the third thing this is for — pasting a song in to
             seed the taste. The file is small, the schema is eight flat
             fields, and an editor that shows the file itself cannot drift
             from it. The net under that choice is in euterpe: SAVE validates
             before it writes and copies the old file to .bak first.

Placement:   A bare tk.Frame tab body inside VoxArrayPanel (the TUNES tab),
             the same shape as MachineSpiritPanel. NOT Kairos-registered —
             purely request-driven (the operator edits and saves), so there is
             no update(data) here, no thread and no after(), and therefore
             nothing for destroy() to cancel.

Base copy:   Its own row, because RESTORE and TAKE FROM SAVED act on a
             different file (euterpe_ledger.json.base) from SAVE and RELOAD
             above them. RESTORE is PREVIEW-ONLY — it fills the box and writes
             nothing, so the escape hatch from a bad state is not itself a state
             you need an escape hatch from (the machine_spirit_panel "Revert to
             Default" precedent). TAKE FROM SAVED is refused while the box is
             dirty: the base is copied from the ledger on disk, so taking one
             mid-edit would capture the older state and report success.

Staleness:   Awen records plays on a 2 s tick, so this tab can be holding text
             the file no longer matches — a song ending mid-edit. The editor
             keeps the euterpe.stamp() it loaded at and compares it back before
             writing, so SAVE refuses rather than silently clobbering that
             play. RELOAD is the way forward, and it is the operator's call
             because their unsaved text is the thing at risk.

Upstream:    panels/vox_array_panel.py (hosts this as the TUNES tab)
Downstream:  tools/euterpe.py (the text, the validation and the write),
             theme.py (colors/fonts/PhosphorScroll/the context menu)

Requires:    tkinter (stdlib). No JSON handling here — that is euterpe's job.
"""

import time
import tkinter as tk
from typing import Any, Callable, Optional

from theme import C, FONTS, PhosphorScroll, themed_menu, popup_menu
from tools import euterpe


_HINT = ('The songs you chose. Auto-DJ reads this list and never adds to it, so '
         'it only grows when you play something or type it in here. Rows whose '
         'source is "user" are the ones it copies your taste from; a "dj" row '
         'blocks a repeat without steering anything. The "key" field is what it '
         'matches on, not the title, and no two rows may share one. Edits take '
         'effect on the next pick, with no restart.')

# Euterpe's refusal slugs (§7: a panel maps slugs, it does not invent text for
# them). A slug may arrive as "slug: detail"; {detail} is that second half.
_ERROR_TEXT = {
    "invalid_json":  "That is not valid JSON — check {detail}. Nothing was saved.",
    "bad_root":      'The file has to be one object with a "plays" list inside '
                     "it. Nothing was saved.",
    "bad_row":       "Play {detail} is missing a field, has one of the wrong "
                     "type, or has a blank title or key. Nothing was saved.",
    "bad_source":    'Play {detail} has a source that is neither "user" nor '
                     '"dj". Nothing was saved.',
    "duplicate_row": "Two of these are the same song — {detail}. Delete one. "
                     "Nothing was saved.",
    "backup_failed": "The .bak backup could not be written, so the save was "
                     "not taken. Nothing was changed.",
    "write_failed":  "The ledger could not be written — see the log. Nothing "
                     "was changed.",
    "no_ledger":     "There is no ledger to take a base copy from yet — play "
                     "something first.",
}

_STALE_MSG = ("Auto-DJ recorded a play while you were editing, so this box is "
              "out of date. Press RELOAD before saving — nothing has been "
              "written, and your text is still here until you do.")


class EuterpePanel(tk.Frame):
    """The song ledger as editable text. A bare Frame tab body inside
    VoxArrayPanel; no Kairos worker, no update() — purely request-driven."""

    def __init__(self, parent: tk.Widget):
        super().__init__(parent, bg=C["card"])

        # The stamp the editor's text was loaded at, re-taken on every load.
        # Set here so _load() below has something to overwrite, not create.
        self._stamp = 0.0

        tk.Label(self, text="TUNES — what Awen has played",
                 font=FONTS["card_header"], fg=C["text2"], bg=C["card"],
                 anchor="w").pack(fill="x", pady=(8, 2))
        tk.Label(self, text=_HINT, font=FONTS["small_italic"], fg=C["text3"],
                 bg=C["card"], anchor="w", wraplength=560,
                 justify="left").pack(fill="x", pady=(0, 6))

        self._summary = tk.Label(self, text="", font=FONTS["small"],
                                 fg=C["text2"], bg=C["card"], anchor="w")
        self._summary.pack(fill="x", pady=(0, 6))

        # height=1 rather than a real line count (§7): this Text is the widget
        # meant to absorb the slack, and a tall request would squeeze the SAVE
        # row and the status line below it to nothing on a small window.
        # wrap="word" because PhosphorScroll is vertical-only — a long title
        # wraps rather than needing a horizontal bar that does not exist.
        self._editor_row = editor_row = tk.Frame(self, bg=C["card"])
        editor_row.pack(fill="both", expand=True)
        self._editor = tk.Text(
            editor_row, font=FONTS["small"], bg=C["bar_bg"], fg=C["text1"],
            insertbackground=C["text1"], highlightbackground=C["border"],
            highlightcolor=C["amber"], highlightthickness=1, borderwidth=0,
            wrap="word", height=1, padx=6, pady=4, undo=True,
        )
        self._editor.pack(side="left", fill="both", expand=True)
        scroll = PhosphorScroll(editor_row, command=self._editor.yview)
        scroll.pack(side="right", fill="y")
        self._editor.configure(yscrollcommand=scroll.set)

        self._editor.bind("<Button-3>", self._show_text_menu)
        # Tk binds Control-a to beginning-of-line. In a box advertised as plainly
        # editable that is the wrong answer, and it is the only default worth
        # overriding — Control-c/v/x and Control-z (undo=True) already behave.
        self._editor.bind("<Control-a>", self._select_all)
        self._editor.bind("<Control-A>", self._select_all)

        self._btn_row = tk.Frame(self, bg=C["card"])
        self._btn_row.pack(fill="x", pady=(8, 0))
        self._button(self._btn_row, "SAVE", C["text1"], self._on_save)
        self._button(self._btn_row, "RELOAD", C["text2"], self._on_reload,
                     pad=(16, 0))

        # ── The base row ─────────────────────────────────────────────────────
        # Its own row, behind its own label, because these two verbs act on a
        # DIFFERENT file from the two above them. RESTORE beside SAVE with no
        # label would read as a third thing that writes the ledger, and it is
        # the one control here that does not write anything at all.
        self._base_row = tk.Frame(self, bg=C["card"])
        self._base_row.pack(fill="x", pady=(6, 0))
        tk.Label(self._base_row, text="BASE COPY", font=FONTS["tiny"],
                 fg=C["text3"], bg=C["card"]).pack(side="left", padx=(0, 10))
        self._button(self._base_row, "RESTORE", C["text2"], self._on_restore)
        self._button(self._base_row, "TAKE FROM SAVED", C["text2"],
                     self._on_take_base, pad=(14, 0))
        self._base_caption = tk.Label(self._base_row, text="", font=FONTS["tiny"],
                                      fg=C["text3"], bg=C["card"])
        self._base_caption.pack(side="left", padx=(14, 0))

        self._status = tk.Label(self, text="", font=FONTS["tiny"], fg=C["text3"],
                                bg=C["card"], anchor="w", wraplength=560,
                                justify="left")
        self._status.pack(fill="x", pady=(8, 0))

        self._load()

    def _button(self, row: tk.Widget, text: str, colour: str,
                command: "Callable[[], None]", pad: tuple = (0, 0)) -> tk.Label:
        """A label-as-button, the house pattern (machine_spirit_panel). Four of
        them here, so the hover pair is written once rather than four times —
        the drift that copy would eventually produce is a button that stops
        lighting up and reads as dead."""
        label = tk.Label(row, text=text, font=FONTS["small_bold"], fg=colour,
                         bg=C["card"], cursor="hand2")
        label.pack(side="left", padx=pad)
        label.bind("<Button-1>", lambda e: command())
        label.bind("<Enter>", lambda e: label.config(fg=C["amber"]))
        label.bind("<Leave>", lambda e: label.config(fg=colour))
        return label

    # ── loading ────────────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Refill the editor from disk and re-take the staleness stamp.

        The stamp is read BEFORE the text, never after: a write landing between
        the two would otherwise leave a stamp that matches a file this box no
        longer shows, which is the exact silent clobber the stamp exists to
        stop. Taking it first can only raise a false alarm, and that costs a
        RELOAD rather than a play.
        """
        self._stamp = euterpe.stamp()
        self._editor.delete("1.0", "end")
        self._editor.insert("1.0", euterpe.raw_text())
        self._editor.edit_reset()      # the loaded text is not an undo step
        self._refresh_summary()
        self._refresh_base_caption()

    def _refresh_base_caption(self) -> None:
        """When the base copy was taken and how big it is — the two facts that
        answer "is my restore point stale?" without opening a file."""
        base = euterpe.base_snapshot()
        if base is None:
            self._base_caption.config(text="none yet")
            return
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(base["at"]))
        size = "unreadable" if base["plays"] is None else f"{base['plays']} plays"
        self._base_caption.config(text=f"{when} · {size}")

    def _is_dirty(self) -> bool:
        """Whether the box holds something the ledger does not. Compared on the
        text rather than tracked with a flag: a flag has to be cleared in every
        path that loads, and the one that gets forgotten is the one that lies."""
        return self._editor.get("1.0", "end-1c") != euterpe.raw_text().rstrip("\n")

    def _refresh_summary(self) -> None:
        """The counts, named the way the FILE names them. "user" and "dj" are
        the values the operator is about to edit, so the summary says those and
        not a friendlier synonym they would then have to translate."""
        plays = euterpe.entries()
        if not plays:
            self._summary.config(
                text="No plays logged yet — the DJ needs a pick of your own "
                     "before it has a taste to follow.")
            return
        user = sum(1 for row in plays if row.get("source") == "user")
        self._summary.config(
            text=f"{len(plays)} plays — {user} user, {len(plays) - user} dj.")

    # ── actions ────────────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        if euterpe.stamp() != self._stamp:
            self._set_status(_STALE_MSG, error=True)
            return

        error = euterpe.replace_all(self._editor.get("1.0", "end"))
        if error is not None:
            self._set_status(self._explain(error), error=True)
            return

        # Reload rather than leave the typed text sitting there: euterpe writes
        # its own indentation and trims to the 200-play cap, so what is on disk
        # can legitimately differ from what was typed. This box promises to show
        # the file, and that promise is worth more than the cursor position.
        self._load()
        self._set_status("Saved. Reloaded, so this is exactly what is on disk "
                         "now — the next pick reads it.", ok=True)

    def _on_reload(self) -> None:
        self._load()
        self._set_status("Reloaded from disk. Any unsaved edits are gone.")

    def _on_restore(self) -> None:
        """Load the base copy into the EDITOR. Nothing is written.

        The machine_spirit_panel "Revert to Default" precedent, and for the same
        reason: this is the escape hatch from a bad state, so it must not itself
        be a state you then need an escape hatch from. You see the whole thing
        first and press SAVE to commit it, or RELOAD to walk away.

        The staleness stamp is deliberately NOT re-taken here. Restoring is the
        most destructive thing this tab can commit — it drops every play since
        the base was made — so if Auto-DJ landed a song meanwhile, SAVE should
        still stop and say so rather than wave it through.
        """
        base = euterpe.base_snapshot()
        if base is None:
            self._set_status(
                "There is no base copy yet. Press TAKE FROM SAVED to make the "
                "ledger as it stands the point you can come back to.",
                error=True)
            return

        self._editor.delete("1.0", "end")
        self._editor.insert("1.0", base["text"])
        size = "" if base["plays"] is None else f" ({base['plays']} plays)"
        self._set_status(
            f"Base copy loaded into the box{size} — nothing is written yet. "
            "Press SAVE to commit it, or RELOAD to go back to the ledger.")

    def _on_take_base(self) -> None:
        """Make the ledger on disk the new base copy.

        Refused while the box has unsaved edits. Taking the base from disk with
        a dirty editor would quietly capture the OLDER state and report success,
        and the operator would find that out on the day they needed it.
        """
        if self._is_dirty():
            self._set_status(
                "Press SAVE first. The base copy is taken from the ledger on "
                "disk, and this box has edits that are not in it yet.",
                error=True)
            return

        error = euterpe.take_base()
        if error is not None:
            self._set_status(self._explain(error), error=True)
            return
        self._refresh_base_caption()
        self._set_status("Base copy taken. Saves and Auto-DJ will not touch it "
                         "— only this button replaces it.", ok=True)

    # ── right-click menu (the echo_panel precedent) ────────────────────────────

    def _show_text_menu(self, event: "tk.Event[Any]") -> None:
        has_selection = bool(self._editor.tag_ranges("sel"))
        menu = themed_menu(self)
        menu.add_command(label="Cut", command=lambda: self._editor.event_generate("<<Cut>>"),
                         state="normal" if has_selection else "disabled")
        menu.add_command(label="Copy", command=lambda: self._editor.event_generate("<<Copy>>"),
                         state="normal" if has_selection else "disabled")
        menu.add_command(label="Paste", command=lambda: self._editor.event_generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="Select All", command=self._select_all)
        popup_menu(menu, event)

    def _select_all(self, event: Optional["tk.Event[Any]"] = None) -> str:
        """Select the whole document. Returns "break" so the key binding
        REPLACES Tk's own Control-a rather than running before it."""
        self._editor.tag_add("sel", "1.0", "end")
        return "break"

    # ── status ─────────────────────────────────────────────────────────────────

    def _explain(self, error: str) -> str:
        """One of euterpe's refusal slugs as a sentence. An unknown slug is
        shown raw rather than swallowed — a save that did not happen must never
        report as silence."""
        slug, _, detail = error.partition(": ")
        template = _ERROR_TEXT.get(slug)
        if template is None:
            return f"The save was refused ({error}). Nothing was changed."
        return template.format(detail=detail)

    def _set_status(self, text: str, *, error: bool = False,
                    ok: bool = False) -> None:
        colour = C["red"] if error else C["text1"] if ok else C["text2"]
        self._status.config(text=text, fg=colour)
