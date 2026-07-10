"""
Zeno panel — calculator for the Felhaven dashboard.

ZenoPanel(tk.Frame) — safe expression evaluator (Zeno), with step traces and a
recent-history strip. A bare Frame tab body inside CogitatorPanel (the ZENO
tab); the ANS readout that used to sit in the Card header now lives in the body.

Request-driven only; no Kairos worker, no root.after(), no threading. The unit
converter that once shared this card is now panels/eudoxus_panel.py.
"""

import tkinter as tk

from theme import C, FONTS
from tools import zeno


class ZenoPanel(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        # ── State ─────────────────────────────────────────────────────────────
        self._history: list[dict] = []      # most-recent first, cap 5
        self._ans: float | None = None      # last successful result
        self._expanded: set = set()         # expanded history indices

        # ── ANS readout (was the Card header "ANS = …"; now in the body) ──────
        self._ans_lbl = tk.Label(
            self, text="", font=FONTS["card_header"],
            fg=C["text3"], bg=C["card"], anchor="w",
        )
        self._ans_lbl.pack(fill="x")

        # ── Entry ─────────────────────────────────────────────────────────────
        self._entry = tk.Entry(
            self, font=FONTS["body"], bg=C["bar_bg"], fg=C["text3"],
            insertbackground=C["text1"], relief="flat",
            highlightbackground=C["border"], highlightthickness=1,
        )
        self._entry.insert(0, "= expression")
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

        # ── Steps trace ───────────────────────────────────────────────────────
        self._steps_frame = tk.Frame(self, bg=C["card"])
        self._steps_frame.pack(fill="x", pady=(2, 0))

        # ── Divider ───────────────────────────────────────────────────────────
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", pady=(8, 6))

        # ── History strip ─────────────────────────────────────────────────────
        self._history_frame = tk.Frame(self, bg=C["card"])
        self._history_frame.pack(fill="x")

    # ── Placeholder helpers ─────────────────────────────────────────────────────

    def _clear_ph(self):
        if self._entry.get() == "= expression":
            self._entry.delete(0, "end")
            self._entry.config(fg=C["text1"])

    def _show_ph(self):
        if not self._entry.get().strip():
            self._entry.delete(0, "end")
            self._entry.insert(0, "= expression")
            self._entry.config(fg=C["text3"])

    # ── Evaluation ──────────────────────────────────────────────────────────────

    def _evaluate(self):
        raw = self._entry.get().strip()
        if not raw or raw == "= expression":
            return

        result = zeno.handle(raw, ans=self._ans)

        if "error" not in result:
            val = result["result"]
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                self._ans = float(val)
            self._history.insert(0, result)
            if len(self._history) > 5:
                self._history = self._history[:5]
            # Shift expanded indices up by one; drop any that overflow cap
            self._expanded = {i + 1 for i in self._expanded if i + 1 < 5}

        self._entry.delete(0, "end")
        self._show_ph()

        self._render_current(result)
        self._render_history()
        self._update_ans()

    # ── ANS readout ─────────────────────────────────────────────────────────────

    def _update_ans(self):
        """Show ANS in the body label, blank until the first good result."""
        if self._ans is None:
            self._ans_lbl.config(text="")
        else:
            self._ans_lbl.config(text=f"ANS = {zeno._format_number(self._ans)}")

    # ── Rendering ───────────────────────────────────────────────────────────────

    def _render_current(self, result: dict):
        self._expr_lbl.config(text=result.get("expression", ""))
        if "error" in result:
            self._val_lbl.config(text=result["error"], fg=C["red"])
        else:
            self._val_lbl.config(text=result.get("display", ""), fg=C["amber"])

        for w in self._steps_frame.winfo_children():
            w.destroy()
        for step in result.get("steps", []):
            tk.Label(
                self._steps_frame, text=step, font=FONTS["tiny"],
                fg=C["text2"], bg=C["card"], anchor="w",
            ).pack(fill="x", padx=(16, 0))
        if "error" in result:
            tk.Label(
                self._steps_frame, text=f"  ✕ {result['error']}",
                font=FONTS["tiny"], fg=C["red"], bg=C["card"], anchor="w",
            ).pack(fill="x", padx=(16, 0))

    def _render_history(self):
        for w in self._history_frame.winfo_children():
            w.destroy()
        for rel_idx, item in enumerate(self._history[1:]):
            self._render_history_row(rel_idx + 1, item)

    def _render_history_row(self, idx: int, item: dict):
        is_open = idx in self._expanded
        chevron = "▾" if is_open else "▸"

        row = tk.Frame(self._history_frame, bg=C["card"])
        row.pack(fill="x", pady=1)

        chev_lbl = tk.Label(row, text=chevron, font=FONTS["tiny"],
                            fg=C["text3"], bg=C["card"], cursor="hand2")
        chev_lbl.pack(side="left", padx=(0, 4))
        chev_lbl.bind("<Button-1>", lambda e, i=idx: self._toggle_expand(i))

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

        if is_open:
            step_frame = tk.Frame(self._history_frame, bg=C["card"])
            step_frame.pack(fill="x")
            for step in item.get("steps", []):
                tk.Label(step_frame, text=step, font=FONTS["tiny"],
                         fg=C["text3"], bg=C["card"], anchor="w").pack(
                    fill="x", padx=(24, 0))
            if "error" in item:
                tk.Label(step_frame, text=f"  ✕ {item['error']}",
                         font=FONTS["tiny"], fg=C["red"], bg=C["card"],
                         anchor="w").pack(fill="x", padx=(24, 0))

    # ── Interactions ────────────────────────────────────────────────────────────

    def _toggle_expand(self, idx: int):
        if idx in self._expanded:
            self._expanded.discard(idx)
        else:
            self._expanded.add(idx)
        self._render_history()

    def _recall(self, expression: str):
        self._entry.delete(0, "end")
        self._entry.insert(0, expression)
        self._entry.config(fg=C["text1"])
        self._entry.focus_set()
