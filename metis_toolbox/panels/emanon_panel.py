"""
Emanon panel — log-watch display for the Felhaven dashboard.

EmanonPanel — a rolling tail of the stack's log lines in a read-only,
word-wrapped text view. White for INFO, amber for WARNING, red for
ERROR/CRITICAL. A status dot reflects the overall verdict (green nominal /
amber degraded / red failed) and blinks briefly when the verdict first turns
"failed". Consecutive-duplicate lines arrive already folded by emanon.fetch()
and render with a "×N" count. The tail is cleared and re-inserted each Kairos
tick.

Right-click the log for the standard themed Copy / Select All / Copy all menu
(the home_panel transcript precedent) — so an error and its trace can be
selected and pasted straight to an AI. Selection and clipboard copy work even
though the widget is state="disabled" (read-only); only insert/delete are
blocked.

Report-only: this panel reads what emanon.fetch() hands it and shows it.
It never acts on, fixes, or restarts anything. Kairos owns all timing — the
only self.after used here is a one-shot blink that terminates on its own.

V0.02:  Audited 2026-07-24. Events area rebuilt as a read-only word-wrapped
        tk.Text with the standard right-click Copy/Select-All menu (home_panel
        precedent) and ×N de-dup rendering, replacing the truncating Label rows
        and hover tooltip. Tests: tests/test_emanon_panel_smoke.py.
"""

import tkinter as tk
from datetime import datetime
from typing import Any

from theme import C, FONTS, PhosphorScroll, themed_menu, popup_menu

# Fixed pixel height for the events area so the footer doesn't jump as the
# number of rows changes tick to tick. The Text scrolls within it.
_EVENTS_H = 220


class EmanonPanel(tk.Frame):
    """Receives log snapshots from Kairos via update() every 2 seconds.

    A bare Frame tab body inside ModeratiPanel (the EMANON tab); keeps its own
    update() and one-shot "failed" blink, and is registered with Kairos directly.
    """

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        # ── Status line: live verdict dot + word ────────────────────────────
        status_row = tk.Frame(self, bg=C["card"])
        status_row.pack(fill="x", pady=(6, 2))

        self._dot = tk.Canvas(status_row, width=8, height=8, bg=C["card"],
                              highlightthickness=0)
        self._dot_id = self._dot.create_oval(1, 1, 7, 7, fill=C["green"],
                                             outline="")
        self._dot.pack(side="left", padx=(0, 6))

        self._status_lbl = tk.Label(status_row, text="watching…",
                                    font=FONTS["card_header"], fg=C["text2"],
                                    bg=C["card"], anchor="w")
        self._status_lbl.pack(side="left")

        # ── Events tail: fixed height; a read-only, word-wrapped, copyable log ─
        self._events_frame = tk.Frame(self, bg=C["card"], height=_EVENTS_H)
        self._events_frame.pack(fill="both", expand=True, pady=(2, 0))
        self._events_frame.pack_propagate(False)

        self._log = tk.Text(
            self._events_frame, bg=C["card"], fg=C["text1"], font=FONTS["tiny"],
            wrap="word", state="disabled", borderwidth=0, highlightthickness=0,
            padx=2, pady=2,
        )
        self._log.pack(side="left", fill="both", expand=True)
        scroll = PhosphorScroll(self._events_frame, command=self._log.yview)
        scroll.pack(side="right", fill="y")
        self._log.configure(yscrollcommand=scroll.set)

        # Level colours as text tags (replacing the old per-Label colouring).
        self._log.tag_configure("ts",     foreground=C["text3"])
        self._log.tag_configure("logger", foreground=C["text2"])
        self._log.tag_configure("info",   foreground=C["text1"])
        self._log.tag_configure("warn",   foreground=C["amber"])
        self._log.tag_configure("error",  foreground=C["red"])
        self._log.tag_configure("count",  foreground=C["text3"])
        self._log.tag_configure("muted",  foreground=C["text3"],
                                font=FONTS["small_italic"])

        # Right-click Copy / Select All / Copy all.
        self._log.bind("<Button-3>", self._show_log_menu)

        # ── Footer: timestamp + counts ──────────────────────────────────────
        self._last_lbl = tk.Label(self, text="", font=FONTS["card_header"],
                                  fg=C["text3"], bg=C["card"], anchor="e")
        self._last_lbl.pack(fill="x", pady=(4, 0))

        # Flash state. We only blink on the transition into "failed", and the
        # blink is a chain of one-shot self.after calls that stops itself.
        self._status = "nominal"
        self._flash_after_id = None

    # ── Color helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _level_tag(level: str) -> str:
        if level in ("ERROR", "CRITICAL"):
            return "error"
        if level == "WARNING":
            return "warn"
        return "info"  # INFO and anything unexpected → white

    def _status_color(self, status: str) -> str:
        return {"failed": C["red"], "degraded": C["amber"]}.get(status, C["green"])

    # ── Flashing (one-shot chain, no threads, no competing loop) ────────────

    def _cancel_flash(self) -> None:
        if self._flash_after_id is not None:
            self.after_cancel(self._flash_after_id)
            self._flash_after_id = None

    def _flash(self, remaining: int) -> None:
        # Guard against firing into a destroyed widget on app shutdown.
        if not self.winfo_exists():
            return
        if remaining <= 0:
            self._dot.itemconfig(self._dot_id, fill=C["red"])  # rest on red
            self._flash_after_id = None
            return
        on = (remaining % 2 == 0)
        self._dot.itemconfig(self._dot_id, fill=C["red"] if on else C["card"])
        self._flash_after_id = self.after(180, lambda: self._flash(remaining - 1))

    # ── Right-click context menu (Pythia home_panel transcript precedent) ────

    def _show_log_menu(self, event: "tk.Event[Any]") -> None:
        """The log is read-only, but selection + clipboard copy still work on a
        state='disabled' Text — only insert/delete are blocked."""
        has_selection = bool(self._log.tag_ranges("sel"))
        menu = themed_menu(self)
        menu.add_command(label="Copy", command=self._copy_log_selection,
                         state="normal" if has_selection else "disabled")
        menu.add_command(label="Select All", command=self._select_all_log)
        menu.add_separator()
        menu.add_command(label="Copy all", command=self._copy_all)
        popup_menu(menu, event)

    def _copy_log_selection(self) -> None:
        try:
            text = self._log.get("sel.first", "sel.last")
        except tk.TclError:
            return                              # selection vanished between click and copy
        self.clipboard_clear()
        self.clipboard_append(text)

    def _select_all_log(self) -> None:
        self._log.tag_add("sel", "1.0", "end")

    def _copy_all(self) -> None:
        """One-click grab of the whole tail — the common 'paste this to an AI'
        case, no manual selection needed."""
        text = self._log.get("1.0", "end").strip("\n")
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)

    # ── Rendering ────────────────────────────────────────────────────────────

    def _render_events(self, entries: list, note: "str | None") -> None:
        """Clear and re-insert the log tail. Each entry is one wrapped line;
        a folded run shows its message plus a muted ×N."""
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        if not entries:
            self._log.insert("end", note or "no events yet", ("muted",))
        else:
            for e in entries:
                ts = e["ts"]
                if " " in ts:
                    ts = ts.split(" ")[-1]      # time only — the date is too wide
                logger_name = e["logger"]
                if logger_name.startswith("METIS."):
                    logger_name = logger_name[len("METIS."):]
                tag = self._level_tag(e["level"])
                self._log.insert("end", ts, ("ts",))
                self._log.insert("end", f"  {e['level']:<5} ", (tag,))
                self._log.insert("end", f"{logger_name}  ", ("logger",))
                self._log.insert("end", e["message"], (tag,))
                if e.get("count", 1) > 1:
                    self._log.insert("end", f"  ×{e['count']}", ("count",))
                self._log.insert("end", "\n")
        self._log.see("end")                    # keep the newest line in view
        self._log.configure(state="disabled")

    # ── Kairos update contract ─────────────────────────────────────────────

    def update(self, data) -> None:
        """Called by Kairos every 2s on the main thread. Rebuilds the tail."""
        if data is None:
            self._render_unavailable()
            return

        status = data.get("status", "nominal")
        entries = data.get("entries", [])
        err = data.get("error_count", 0)
        warn = data.get("warning_count", 0)

        self._render_events(entries, data.get("note"))

        # Verdict dot + word.
        self._dot.itemconfig(self._dot_id, fill=self._status_color(status))
        self._status_lbl.config(text=status, fg=self._status_color(status))

        # Blink only when the verdict FIRST flips to failed; static red while it
        # stays failed (no continuous strobe), and stop any blink on recovery.
        if status == "failed" and self._status != "failed":
            self._cancel_flash()
            self._flash(6)
        elif status != "failed":
            self._cancel_flash()
        self._status = status

        # Footer.
        self._last_lbl.config(
            text=f"updated {datetime.now().strftime('%H:%M')}  ·  "
                 f"{err} err · {warn} warn"
        )

    def _render_unavailable(self) -> None:
        """Kairos delivers None when emanon.fetch raised — degrade gracefully."""
        self._cancel_flash()
        self._status = "unavailable"
        self._render_events([], "watcher unavailable")
        self._dot.itemconfig(self._dot_id, fill=C["text3"])
        self._status_lbl.config(text="unavailable", fg=C["text3"])
        self._last_lbl.config(text="watcher unavailable")
