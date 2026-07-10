"""
Scribe panel — Tkinter widget for the Felhaven dashboard.

ScribePanel(tk.Frame) — tasks input + checklist above, NotesWidget below.
NotesWidget           — embedded scratch text area (same pattern as AmmitWidget).

ScribePanel receives `data` (shared dict) and an `on_save` callback; does not
touch persistence directly. A bare Frame tab body inside CogitatorPanel (the
SCRIBE tab); the open-count readout that used to sit in the Card header now
lives in the body.
"""

import tkinter as tk

from theme import C, FONTS


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
            insertbackground=C["text1"], relief="flat", wrap="word",
            highlightthickness=0, undo=True
        )
        self._text.pack(fill="both", expand=True, pady=(4, 0))
        self._text.insert("1.0", data.get("notes", ""))
        self._text.bind("<KeyRelease>", self._on_edit)

    def _on_edit(self, e=None):
        self._data["notes"] = self._text.get("1.0", "end-1c")
        self._on_save(self._data)


# ─────────────────────────────────────────────────────────────────────────────
#  ScribePanel — tasks + embedded notes
# ─────────────────────────────────────────────────────────────────────────────

class ScribePanel(tk.Frame):
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
            insertbackground=C["text1"], relief="flat",
            highlightbackground=C["border"], highlightthickness=0
        )
        self._entry.insert(0, "+ new task...")
        self._entry.pack(fill="x", pady=(6, 8))
        self._entry.bind("<Return>",   self._add)
        self._entry.bind("<FocusIn>",  self._clear_ph)
        self._entry.bind("<FocusOut>", self._show_ph)

        self._list_frame = tk.Frame(self, bg=C["card"])
        self._list_frame.pack(fill="x")

        self._notes = NotesWidget(self, data, on_save)
        self._notes.pack(fill="both", expand=True)

        self._render()

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

    def _render(self):
        for w in self._list_frame.winfo_children():
            w.destroy()
        open_count = sum(1 for t in self._data["tasks"] if not t["done"])
        self._status_lbl.config(text=f"{open_count} open")

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

            txt_color = C["text3"] if task["done"] else C["text2"]
            font      = FONTS["body_strike"] if task["done"] else FONTS["body"]
            tk.Label(row, text=task["text"], font=font, fg=txt_color,
                     bg=C["card"], anchor="w").pack(side="left", fill="x", expand=True)

            x_btn = tk.Label(row, text="✕", font=FONTS["tiny"],
                             fg=C["text3"], bg=C["card"], cursor="hand2")
            x_btn.pack(side="right")
            x_btn.bind("<Button-1>", lambda e, idx=i: self._delete(idx))
            x_btn.bind("<Enter>",    lambda e, w=x_btn: w.config(fg=C["red"]))
            x_btn.bind("<Leave>",    lambda e, w=x_btn: w.config(fg=C["text3"]))
