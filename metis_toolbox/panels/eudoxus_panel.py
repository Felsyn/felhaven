"""
Eudoxus panel — unit converter for the Felhaven dashboard.

EudoxusPanel(tk.Frame) — unit converter (Eudoxus), with a factor line and a
recent-history strip. A bare Frame tab body inside CogitatorPanel (the EUDOXUS
tab). Split out of zeno_panel.py, where it lived as the CONVERT tab sharing a
card with Zeno — now its own surface.

Request-driven only; no Kairos worker, no root.after(), no threading.

V0.02:  Audited 2026-07-23 — stopped rendering Eudoxus's raw error codes at the
        user and mapped them to human text; fixed the placeholder swallowing a
        real conversion and the last good result vanishing after a failure;
        first tests (tests/test_eudoxus_panel_smoke.py).
"""

import tkinter as tk

from theme import C, FONTS
from tools import eudoxus

# The example conversion shown in an empty entry. Unlike Zeno's "= expression",
# this is a VALID input, so the panel cannot use "does the text equal the
# placeholder?" to decide whether the box is empty — see self._placeholder.
_PLACEHOLDER = "10 mi to km"

# Human text per Eudoxus error slug (CONVENTIONS §2), the panels/echo_panel.py
# precedent by way of zeno_panel: Eudoxus's identifiers can carry detail after
# ": " — "unknown_unit: parsec" — so _error_text splits the slug off before the
# lookup and puts the detail back afterwards. An unmapped code is shown raw
# rather than swallowed.
_ERROR_TEXT = {
    "unknown_unit":        "unknown unit",
    "incompatible_units":  "cannot convert",
    "missing_separator":   "expected 'X to Y'",
    "missing_source_unit": "no unit to convert from",
    "conversion_failed":   "conversion failed",
}


def _error_text(code: str) -> str:
    """Human text for a `slug` or `slug: detail` identifier."""
    slug, sep, detail = code.partition(": ")
    if slug not in _ERROR_TEXT:
        return code
    return f"{_ERROR_TEXT[slug]}: {detail}" if sep else _ERROR_TEXT[slug]


class EudoxusPanel(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        # ── State ─────────────────────────────────────────────────────────────
        self._history: list[dict] = []      # most-recent first, cap 5
        # Where the strip starts. 1 while the current row IS _history[0]; 0
        # after a failed conversion, which appends nothing — otherwise the last
        # good result is in neither the current row nor the strip.
        self._history_start = 0
        # True while the entry is showing _PLACEHOLDER rather than user text.
        self._placeholder = True

        # ── Entry ─────────────────────────────────────────────────────────────
        self._entry = tk.Entry(
            self, font=FONTS["body"], bg=C["bar_bg"], fg=C["text3"],
            insertbackground=C["text1"], relief="flat",
            highlightbackground=C["border"], highlightthickness=1,
        )
        self._entry.insert(0, _PLACEHOLDER)
        self._entry.pack(fill="x", pady=(6, 6))
        self._entry.bind("<FocusIn>",  lambda e: self._clear_ph())
        self._entry.bind("<FocusOut>", lambda e: self._show_ph())
        self._entry.bind("<Return>",   lambda e: self._evaluate())

        # ── Current result row ────────────────────────────────────────────────
        self._result_row = tk.Frame(self, bg=C["card"])
        self._result_row.pack(fill="x")

        self._expr_lbl = tk.Label(
            self._result_row, text="", font=FONTS["small"],
            fg=C["text3"], bg=C["card"], anchor="w",
        )
        self._expr_lbl.pack(side="left")

        self._val_lbl = tk.Label(
            self._result_row, text="", font=FONTS["large_bold"],
            fg=C["amber"], bg=C["card"], anchor="e",
        )
        self._val_lbl.pack(side="right")

        # ── Factor line ───────────────────────────────────────────────────────
        self._factor_lbl = tk.Label(
            self, text="", font=FONTS["tiny"],
            fg=C["text3"], bg=C["card"], anchor="w",
        )
        self._factor_lbl.pack(fill="x", pady=(2, 0))

        # ── Divider ───────────────────────────────────────────────────────────
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", pady=(8, 6))

        # ── History strip ─────────────────────────────────────────────────────
        self._history_frame = tk.Frame(self, bg=C["card"])
        self._history_frame.pack(fill="x")

    # ── Placeholder helpers ─────────────────────────────────────────────────────

    def _clear_ph(self):
        if self._placeholder:
            self._entry.delete(0, "end")
            self._entry.config(fg=C["text1"])
            self._placeholder = False

    def _show_ph(self):
        if not self._entry.get().strip():
            self._entry.delete(0, "end")
            self._entry.insert(0, _PLACEHOLDER)
            self._entry.config(fg=C["text3"])
            self._placeholder = True

    # ── Evaluation ──────────────────────────────────────────────────────────────

    def _evaluate(self):
        # The flag, not the text: "10 mi to km" is a conversion someone may
        # genuinely want, and comparing against the placeholder string made
        # typing the example do nothing at all.
        if self._placeholder:
            return
        raw = self._entry.get().strip()
        if not raw:
            return

        result = eudoxus.convert(raw)

        if "error" not in result:
            self._history.insert(0, result)
            if len(self._history) > 5:
                self._history = self._history[:5]

        self._history_start = 1 if "error" not in result else 0

        self._entry.delete(0, "end")
        self._show_ph()

        self._render_current(result)
        self._render_history()

    # ── Rendering ───────────────────────────────────────────────────────────────

    def _render_current(self, result: dict):
        self._expr_lbl.config(text=result.get("expression", ""))
        if "error" in result:
            self._val_lbl.config(text=_error_text(result["error"]), fg=C["red"])
            self._factor_lbl.config(text="")
        else:
            self._val_lbl.config(text=result.get("display", ""), fg=C["amber"])
            self._factor_lbl.config(text=result.get("factor", ""))

    def _render_history(self):
        for w in self._history_frame.winfo_children():
            w.destroy()
        # Skip _history[0] only when it IS the current row (a success). After a
        # failure nothing was appended, so the newest result is at [0] and the
        # strip must render from there or it vanishes from the panel entirely.
        for item in self._history[self._history_start:]:
            self._render_history_row(item)

    def _render_history_row(self, item: dict):
        """History row — no chevron, just expr = result (clickable to recall)."""
        row = tk.Frame(self._history_frame, bg=C["card"])
        row.pack(fill="x", pady=1)

        expr_lbl = tk.Label(row, text=item.get("expression", ""),
                            font=FONTS["small"], fg=C["text2"], bg=C["card"],
                            anchor="w", cursor="hand2")
        expr_lbl.pack(side="left")
        expr_lbl.bind("<Button-1>",
                      lambda e, ex=item.get("expression", ""): self._recall(ex))
        expr_lbl.bind("<Enter>", lambda e, w=expr_lbl: w.config(fg=C["text1"]))
        expr_lbl.bind("<Leave>", lambda e, w=expr_lbl: w.config(fg=C["text2"]))

        tk.Label(row, text=" = ", font=FONTS["small"],
                 fg=C["text3"], bg=C["card"]).pack(side="left")

        res_text = "err" if "error" in item else item.get("display", "")
        res_fg   = C["red"] if "error" in item else C["text1"]
        tk.Label(row, text=res_text, font=FONTS["small"],
                 fg=res_fg, bg=C["card"]).pack(side="left")

    # ── Interactions ────────────────────────────────────────────────────────────

    def _recall(self, expression: str):
        # Clear the flag BEFORE focus_set(): focus fires <FocusIn> -> _clear_ph,
        # which would wipe the expression we just inserted if it still thought
        # the box held the placeholder.
        self._placeholder = False
        self._entry.delete(0, "end")
        self._entry.insert(0, expression)
        self._entry.config(fg=C["text1"])
        self._entry.focus_set()
