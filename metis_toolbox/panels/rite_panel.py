"""
Rite panel — the Rite Log window (Pythia's per-turn tool diagnostics).
=====================================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Display one turn's tool-call records. Draw a header, a scrollable
             block per rite (name, status, timing, arguments, error, result
             preview), and a footer that copies the records as JSON. That is
             the whole job: this module never runs a tool, never reads a
             config, never asks where the records came from.

Placement:   A tk.Toplevel, not a Card and not a tab body — the first secondary
             window in the toolbox (CONVENTIONS §12). Deliberately NON-modal
             and grab-free: comparing two turns means having two of these open
             at once, and the transcript underneath has to stay scrollable
             while one is up.

Contract:    RiteLog(parent, records, focus_failures=False). `records` is the
             list Pythia put in its stats payload under "tool_records" — see
             pythia.ask()'s docstring for the field-by-field shape. Passing
             None renders the expired view: the turn's records have aged out of
             HomePanel's ring, which is ordinary retention, not a fault.

Pure display: the constructor takes dicts and renders them. It does not parse,
             reshape, validate or enrich them, and it imports NOTHING from
             pythia, the brain tools, or cerberus (tools/virgil.py is the
             glossary its tips come from, not a tool). A diagnostic window that needed
             the vault unlocked in order to report that the vault is locked
             would be useless exactly when it is needed.

Upstream:    panels/home_panel.py (opens one per meta-line click)
Tooltips:    Virgil tips (glossary entries, CONVENTIONS §7) on the parts a
             reader can't guess: each rite's status, its name (a click selects
             it), each field's ▸/▾ caption, and COPY THIS RITE. A tip says
             what the window shows; it imports nothing that knows where the
             records came from.

Downstream:  theme.py (colors/fonts/PhosphorScroll), panels/virgil_tooltip.py
             + tools/virgil.py (the tips)

Requires:    tkinter, json, typing (stdlib). No pip dependency.

V0.03:       First audit, 2026-11-19 (the V0.03 walkthrough's S17; born
             2026-08-20, tips since #57). Nine slugs rendered bare: six of
             Pythia's own refusals (the URL rule, the file-change OK, the
             verbatim display, the allowlist) and Callimachus's
             private_address, Daedalus's parse_failed, Morpheus's
             not_playing. Each has its line now, and a test fails on the
             next one missing. Closing the window with the pointer over the
             list left the app-wide wheel binding on a dead canvas (a
             TclError per wheel turn); _ScrollFrame gives it back on destroy.
             The result preview shows real characters (Pythia's change).
             Checked and true: the status table, the expired view, retention
             (20 = home_panel._MAX_RITE_TURNS), the order, the clipboard.
"""

import json
import tkinter as tk
from typing import Any, Literal, Optional

from panels import virgil_tooltip
from theme import C, FONTS, PhosphorScroll
from tools import virgil

_RETENTION_NOTE = "Retention limit: 20 turns."
_COPY_FLASH_MS = 1000       # how long a copy button reads COPIED before restoring
_PREVIEW_LABEL = "result preview"

# Status → (glyph, label, color key). not_run is dim, NOT red: the operator
# stopped the turn on purpose, and an alarm color would report their own
# decision back to them as a fault.
_STATUS = {
    "ok":      ("✓", "ok",      "text1"),
    "failed":  ("⚠", "failed",  "red"),
    "not_run": ("○", "not run", "text3"),
}

# ─────────────────────────────────────────────────────────────────────────────
#  Slug → plain English.
#
#  Built by reading the error returns of Pythia's dispatcher and of the modules
#  registered in pythia._TOOL_MODULES, with the helpers whose slugs they pass
#  up: the only places a rite's error can come from. Nothing here is invented:
#  a slug no module emits does not belong in this table, and every slug Pythia
#  or a registered module writes has a line (test_rite_panel_smoke holds both).
#  Anything else renders bare (see _explain).
#
#  Where one slug covers several subsystems (the shared core below: offline is
#  Aura's weather and Pheme's feeds alike; too_large is both a download cap and
#  an archive cap) the wording is deliberately true of all of them rather than
#  guessing which one ran. The tool's name sits beside this line, and the
#  detail after the colon says which service, file or thing.
# ─────────────────────────────────────────────────────────────────────────────

_ERROR_TEXT = {
    # Pythia's own dispatcher
    "tool_failed":           "The tool raised an exception; Pythia caught it so the turn could continue.",
    "unknown_tool":          "The model asked for a tool that is not in the registry.",
    "cancelled":             "You stopped the turn before this call was dispatched.",
    "tool_not_available":    "This conversation does not allow that tool, so the call was refused.",
    "untrusted_url":         "Refused: fetch_page opens only a link you wrote or a search result gave, never one the model made.",
    "no_confirmation":       "Refused: this tool changes files and needs your OK, and this conversation cannot ask for it.",
    "not_confirmed":         "You did not allow this change, so it did not run.",
    "no_display":            "The text had to be shown word for word, and this conversation has nowhere to show it.",
    "display_failed":        "The text could not be shown, so the model was told it failed.",
    # The shared core (CONVENTIONS §2, "The error vocabulary")
    "bad_arguments":         "The call did not give the tool what it needs.",
    "unknown_action":        "The model asked for an action the tool does not have.",
    "offline":               "Could not reach the network.",
    "timeout":               "The request did not answer in time.",
    "unavailable":           "The source failed, or answered with nothing usable.",
    "not_found":             "Nothing by that name exists.",
    "no_results":            "Nothing matched.",
    "vault_locked":          "Cerberus is locked. Unlock the vault to enable this tool.",
    "no_key":                "The vault is open but holds no key for this service.",
    "too_large":             "Refused: the content is over the size cap.",
    # The machine and sky readers have no words of their own here: Hephaestus
    # (2026-11-17), Hypatia, Helios, Selene (2026-11-20) and Argus
    # (2026-11-21) say the shared unavailable since their walkthroughs.
    # Aura
    "cert_error":            "The service's security certificate failed to verify.",
    # Callimachus / Morpheus
    "rate_limited":          "The service refused the call for making too many requests.",
    "non_https_url":         "Refused: the URL is not HTTPS, or redirected off HTTPS mid-fetch.",
    "private_address":       "Refused: the link leads to this computer or the home network.",
    "nothing_to_resume":     "Nothing has been played yet this session.",
    "not_playing":           "Nothing is playing right now.",
    # Daedalus
    "parse_failed":          "The source file could not be parsed, so its docstring could not be read.",
    # Pheme
    "invalid_feed_xml":      "The feed returned something that is not valid feed XML.",
    "config_unreadable":     "The feed configuration could not be read.",
    # Zeno
    "parse_error":           "The expression could not be parsed.",
    "no_previous_result":    "There is no previous result to refer back to.",
    "unknown_name":          "The expression used a name Zeno does not know.",
    "unknown_function":      "The expression called a function Zeno does not have.",
    "unsupported_operation": "The expression used an operation Zeno does not allow.",
    "evaluation_stalled":    "Evaluation hit its step budget and stopped.",
    "division_by_zero":      "The expression divides by zero.",
    "result_too_large":      "The result is too large to represent.",
    "math_domain_error":     "The expression is outside the function's valid domain.",
    "evaluation_failed":     "Evaluation failed unexpectedly.",
    # Eudoxus
    "unknown_unit":          "That unit is not in the conversion table.",
    "incompatible_units":    "Those two units measure different things.",
    "conversion_failed":     "The conversion failed unexpectedly.",
    "missing_separator":     "The phrasing needs an 'X to Y' form.",
    "missing_source_unit":   "No unit was given to convert from.",
    # Herodotus
    "invalid_filename":      "The document name is not allowed.",
    "heading_ambiguous":     "More than one heading matches that text.",
    "target_ambiguous":      "That text appears more than once — more context is needed.",
    "not_utf8":              "The file is not valid UTF-8 text.",
    "read_failed":           "The archive could not be read.",
    "write_failed":          "The file could not be written.",
}


def _split_slug(error: str) -> tuple[str, str]:
    """An error string as (slug, human detail). The slug is the stable half."""
    slug, sep, detail = error.partition(": ")
    return (slug, detail) if sep else (error, "")


def _explain(error: str) -> str:
    """The plain-English line for an error, or "" when the slug is unknown.

    Silence is deliberate. A generic "something went wrong" adds nothing the
    raw slug did not already say, and teaches the operator to stop reading
    this line — which is the one line that has to stay worth reading.
    """
    return _ERROR_TEXT.get(_split_slug(error)[0], "")


def _format_duration(ms: Optional[int]) -> str:
    """"4.2s" at a second or more, "312ms" below it, "" when it never ran."""
    if ms is None:
        return ""
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms}ms"


def _format_arguments(record: dict[str, Any]) -> str:
    """The arguments field as text. It is a dict when it fit under the cap and
    the truncated JSON string when it did not — arguments_truncated says which,
    so the type is read, never guessed."""
    arguments = record.get("arguments")
    if isinstance(arguments, str):
        return arguments
    try:
        return json.dumps(arguments, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(arguments)


def _clock(started_at: str) -> str:
    """Just the wall-clock part of an ISO timestamp, for a rite's own line."""
    return started_at.partition("T")[2] or started_at


def _order(records: list[dict[str, Any]],
           focus_failures: bool) -> list[dict[str, Any]]:
    """Chronological by default. Entered through the failure count, failures
    come first and the rest follow in seq order — every block keeps its seq
    number visible, so the true chronology is never lost."""
    if not focus_failures:
        return sorted(records, key=lambda r: r.get("seq", 0))
    return sorted(records,
                  key=lambda r: (r.get("status") != "failed", r.get("seq", 0)))


# ─────────────────────────────────────────────────────────────────────────────
#  _ScrollFrame — vertically scrollable container (copied per §7's one-per-panel
#  house rule; PhosphorScroll inside, never tk.Scrollbar — see §12).
# ─────────────────────────────────────────────────────────────────────────────

class _ScrollFrame(tk.Frame):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, bg=C["card"])

        self._canvas = tk.Canvas(self, bg=C["card"], highlightthickness=0, bd=0)
        scroll = PhosphorScroll(self, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scroll.set)

        scroll.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self.inner = tk.Frame(self._canvas, bg=C["card"])
        self._win = self._canvas.create_window((0, 0), window=self.inner,
                                               anchor="nw")

        self.inner.bind("<Configure>", self._on_inner_config)
        self._canvas.bind("<Configure>", self._on_canvas_config)
        self._canvas.bind("<Enter>", self._bind_wheel)
        self._canvas.bind("<Leave>", self._unbind_wheel)
        self._wheel_held = False          # is the app-wide wheel binding ours?

    def _on_inner_config(self, _event: "tk.Event[Any]") -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_config(self, event: "tk.Event[Any]") -> None:
        self._canvas.itemconfigure(self._win, width=event.width)

    def _bind_wheel(self, _event: "tk.Event[Any]") -> None:
        self._canvas.bind_all("<MouseWheel>", self._on_wheel)
        self._wheel_held = True

    def _unbind_wheel(self, _event: "tk.Event[Any]") -> None:
        self._canvas.unbind_all("<MouseWheel>")
        self._wheel_held = False

    def _on_wheel(self, event: "tk.Event[Any]") -> None:
        self._canvas.yview_scroll(int(-event.delta / 120), "units")

    def destroy(self) -> None:
        """Give the app-wide wheel binding back if it is still ours.

        The one way this copy differs from the other panels': it lives in a
        window that closes. Closed with the pointer over the list (Escape),
        no <Leave> arrives, and the binding went on calling a canvas that was
        gone: a TclError on every wheel turn anywhere in Felhaven until
        another list took the wheel (measured 2026-11-19)."""
        if self._wheel_held:
            self._canvas.unbind_all("<MouseWheel>")
            self._wheel_held = False
        super().destroy()


# ─────────────────────────────────────────────────────────────────────────────
#  RiteLog
# ─────────────────────────────────────────────────────────────────────────────

class RiteLog(tk.Toplevel):

    # Hover tooltips (Virgil), each a glossary entry (CONVENTIONS §7). _TIPS
    # names the footer button in self.tip_targets; _ROW_TIPS names the parts
    # each rite block builds, attached by _build_rite and _field.
    _TIPS = {
        "copy_one": virgil.term("rite_copy"),
    }
    _ROW_TIPS = {
        "status": virgil.term("rite_status"),
        "select": virgil.term("rite_select"),
        "expand": virgil.term("rite_expand"),
    }

    def __init__(self, parent: tk.Widget,
                 records: "list[dict[str, Any]] | None",
                 focus_failures: bool = False) -> None:
        super().__init__(parent, bg=C["bg"])
        self._records = list(records) if records is not None else None
        self._focus_failures = focus_failures
        self._selected: Optional[int] = None          # seq of the selected rite
        self._rows: dict[int, tk.Label] = {}          # seq -> its header label
        self._flash_after: Optional[str] = None       # pending COPIED restore

        self.title("FELHAVEN — RITE LOG")
        self.geometry("760x580")
        self.minsize(460, 300)
        self.transient(parent.winfo_toplevel())
        # No grab_set(): two Rite Logs must be openable side by side, and the
        # transcript behind them has to stay scrollable while one is up.
        self.bind("<Escape>", lambda _e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._build_header()
        body = _ScrollFrame(self)
        body.pack(fill="both", expand=True, padx=10)
        self._build_footer()

        if self._records is None:
            self._build_expired(body.inner)
        elif not self._records:
            self._build_empty(body.inner)
        else:
            for record in _order(self._records, focus_failures):
                self._build_rite(body.inner, record)

    # ── Chrome ───────────────────────────────────────────────────────────────

    def _build_header(self) -> None:
        head = tk.Frame(self, bg=C["bg"])
        head.pack(fill="x", padx=10, pady=(10, 6))

        if self._records is None:
            title, stamp = "RITE LOG", ""
        else:
            failed = sum(1 for r in self._records
                         if r.get("status") == "failed")
            rites = len(self._records)
            title = (f"RITE LOG — {rites} rite{'' if rites == 1 else 's'} · "
                     f"{failed} failed")
            if self._focus_failures:
                title += " · showing failures first"
            # The turn's timestamp is the first rite's. An empty list has none,
            # and that is a legitimate state to be handed, not a crash.
            first = self._records[0] if self._records else {}
            stamp = (first.get("started_at", "") or "").replace("T", " ")

        tk.Label(head, text=title, font=FONTS["small_bold"], fg=C["text1"],
                 bg=C["bg"], anchor="w").pack(fill="x")
        if stamp:
            tk.Label(head, text=stamp, font=FONTS["tiny"], fg=C["text3"],
                     bg=C["bg"], anchor="w").pack(fill="x")
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", padx=10)

    def _build_footer(self) -> None:
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", padx=10,
                                                      pady=(6, 0))
        foot = tk.Frame(self, bg=C["bg"])
        foot.pack(fill="x", padx=10, pady=8)

        self._copy_all = self._button(foot, "COPY ALL", self._on_copy_all)
        self._copy_one = self._button(foot, "COPY THIS RITE", self._on_copy_one)
        self._button(foot, "CLOSE", lambda: self.destroy(), side="right")
        self._refresh_copy_one()
        self.tip_targets: dict[str, tk.Misc] = {"copy_one": self._copy_one}
        for key, tip in self._TIPS.items():
            virgil_tooltip.attach(self.tip_targets[key], tip)

    def _button(self, parent: tk.Widget, text: str, command: Any,
                side: Literal["left", "right"] = "left") -> tk.Label:
        """A clickable tk.Label with the house hover flip (§7)."""
        lbl = tk.Label(parent, text=text, font=FONTS["small"], fg=C["text3"],
                       bg=C["bg"], cursor="hand2", padx=6)
        lbl.pack(side=side)
        lbl.bind("<Button-1>", lambda _e: command())
        lbl.bind("<Enter>", lambda _e: self._hover(lbl, True))
        lbl.bind("<Leave>", lambda _e: self._hover(lbl, False))
        return lbl

    def _hover(self, label: tk.Label, on: bool) -> None:
        """Light a button on hover — unless it is the disabled COPY THIS RITE,
        which must not offer feedback for a click that would do nothing."""
        if label is getattr(self, "_copy_one", None) and self._selected is None:
            return
        label.config(fg=C["text1"] if on else C["text3"])

    # ── Bodies ───────────────────────────────────────────────────────────────

    def _build_expired(self, parent: tk.Widget) -> None:
        """Records aged out of HomePanel's ring. Ordinary retention, so it is
        rendered in the panel's normal voice — not an error color, not a
        dialog."""
        for text, font, color in (
            ("RITE RECORD EXPIRED", "small_bold", "text2"),
            ("This diagnostic record is no longer retained.", "small", "text3"),
            (_RETENTION_NOTE, "small", "text3"),
        ):
            tk.Label(parent, text=text, font=FONTS[font], fg=C[color],
                     bg=C["card"], anchor="w").pack(fill="x", pady=(6, 0))

    def _build_empty(self, parent: tk.Widget) -> None:
        tk.Label(parent, text="This turn called no tools.", font=FONTS["small"],
                 fg=C["text3"], bg=C["card"], anchor="w").pack(fill="x",
                                                               pady=(8, 0))

    def _build_rite(self, parent: tk.Widget, record: dict[str, Any]) -> None:
        seq = int(record.get("seq", 0))
        status = str(record.get("status", "not_run"))
        glyph, word, color_key = _STATUS.get(status, _STATUS["not_run"])

        block = tk.Frame(parent, bg=C["card"])
        block.pack(fill="x", pady=(8, 0))

        duration = _format_duration(record.get("duration_ms"))
        right = f"{glyph} {word}" + (f" · {duration}" if duration else "")
        header = tk.Frame(block, bg=C["card"])
        header.pack(fill="x")
        name = tk.Label(header, text=f"{seq} · {record.get('name', '')}",
                        font=FONTS["small_bold"], fg=C["text1"], bg=C["card"],
                        anchor="w", cursor="hand2")
        name.pack(side="left")
        status_lbl = tk.Label(header, text=right, font=FONTS["small"],
                              fg=C[color_key], bg=C["card"], anchor="e")
        status_lbl.pack(side="right")
        name.bind("<Button-1>", lambda _e: self._select(seq))
        virgil_tooltip.attach(status_lbl, self._ROW_TIPS["status"])
        virgil_tooltip.attach(name, self._ROW_TIPS["select"])
        self._rows[seq] = name

        tk.Label(block, text=_clock(str(record.get("started_at", ""))),
                 font=FONTS["tiny"], fg=C["text3"], bg=C["card"],
                 anchor="w").pack(fill="x")

        self._field(block, "arguments", _format_arguments(record),
                    truncated=bool(record.get("arguments_truncated")),
                    collapsed=False)

        error = record.get("error")
        if error:
            self._error_field(block, str(error))

        preview = record.get("result_preview")
        if preview:
            self._field(block, _PREVIEW_LABEL, str(preview),
                        truncated=bool(record.get("result_truncated")),
                        collapsed=True)

        tk.Frame(block, bg=C["border"], height=1).pack(fill="x", pady=(8, 0))

    def _field(self, parent: tk.Widget, label: str, value: str,
               truncated: bool, collapsed: bool) -> None:
        """One labelled field. `arguments` opens expanded — it is the primary
        diagnostic and almost always short. `result preview` opens collapsed —
        a search response runs to thousands of characters and would bury the
        two lines the operator came for."""
        head = tk.Frame(parent, bg=C["card"], cursor="hand2")
        head.pack(fill="x", pady=(4, 0))
        chevron = tk.Label(head, text="▸" if collapsed else "▾",
                           font=FONTS["tiny"], fg=C["text2"], bg=C["card"])
        chevron.pack(side="left", padx=(0, 4))
        caption = label + (" · truncated" if truncated else "")
        tk.Label(head, text=caption, font=FONTS["tiny"], fg=C["text3"],
                 bg=C["card"]).pack(side="left")

        # The body is built either way and only its packing changes, so
        # toggling is instant and needs no re-render (the PhemePanel note).
        body = tk.Label(parent, text=value, font=FONTS["small"], fg=C["text2"],
                        bg=C["card"], anchor="w", justify="left", padx=14)
        if not collapsed:
            body.pack(fill="x")

        def toggle(_event: "tk.Event[Any]") -> None:
            if body.winfo_manager():
                body.pack_forget()
                chevron.config(text="▸")
            else:
                body.pack(fill="x")
                chevron.config(text="▾")

        for widget in (head, chevron):
            widget.bind("<Button-1>", toggle)
        virgil_tooltip.attach(head, self._ROW_TIPS["expand"])

    def _error_field(self, parent: tk.Widget, error: str) -> None:
        """The raw slug, and beneath it a plain-English line when the slug is
        one we know. The explanation is a fixed lookup — the model never gets
        to paraphrase its own failures."""
        tk.Label(parent, text="error", font=FONTS["tiny"], fg=C["text3"],
                 bg=C["card"], anchor="w").pack(fill="x", pady=(4, 0))
        tk.Label(parent, text=error, font=FONTS["small"], fg=C["text2"],
                 bg=C["card"], anchor="w", justify="left",
                 padx=14).pack(fill="x")
        explanation = _explain(error)
        if explanation:
            tk.Label(parent, text=explanation, font=FONTS["small_italic"],
                     fg=C["text3"], bg=C["card"], anchor="w", justify="left",
                     padx=14).pack(fill="x")

    # ── Selection & clipboard ────────────────────────────────────────────────

    def _select(self, seq: int) -> None:
        """Click a rite's name to select it; click it again to deselect."""
        self._selected = None if self._selected == seq else seq
        for row_seq, label in self._rows.items():
            lit = row_seq == self._selected
            label.config(fg=C["badge_fg"] if lit else C["text1"])
        self._refresh_copy_one()

    def _refresh_copy_one(self) -> None:
        """COPY THIS RITE is dim and inert until exactly one rite is selected —
        disabled rather than guessing which one was meant."""
        self._copy_one.config(
            fg=C["text3"] if self._selected is not None else C["border"],
            cursor="hand2" if self._selected is not None else "")

    def _on_copy_all(self) -> None:
        self._copy(self._records or [], self._copy_all, "COPY ALL")

    def _on_copy_one(self) -> None:
        if self._selected is None:
            return
        chosen = [r for r in (self._records or [])
                  if r.get("seq") == self._selected]
        self._copy(chosen, self._copy_one, "COPY THIS RITE")

    def _copy(self, payload: list[dict[str, Any]], button: tk.Label,
              restore: str) -> None:
        """JSON only. These get pasted into a coding agent, and JSON survives
        that trip where a pretty-printed text block does not."""
        self.clipboard_clear()
        self.clipboard_append(json.dumps(payload, indent=2, ensure_ascii=False))
        button.config(text="COPIED")
        if self._flash_after is not None:
            self.after_cancel(self._flash_after)
        self._flash_after = self.after(
            _COPY_FLASH_MS, lambda: self._restore(button, restore))

    def _restore(self, button: tk.Label, text: str) -> None:
        self._flash_after = None
        if button.winfo_exists():
            button.config(text=text)

    # ── Teardown ─────────────────────────────────────────────────────────────

    def destroy(self) -> None:
        """Cancel the pending COPIED restore first (the HomePanel precedent):
        Tk deletes a widget's Tcl commands as it destroys it, so an after()
        script still queued against a closed window has nothing left to call."""
        if self._flash_after is not None:
            self.after_cancel(self._flash_after)
            self._flash_after = None
        super().destroy()
