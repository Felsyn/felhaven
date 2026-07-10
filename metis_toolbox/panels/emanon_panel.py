"""
Emanon panel — log-watch display for the Felhaven dashboard.

EmanonPanel — a rolling tail of the stack's log lines. White for INFO,
amber for WARNING, red for ERROR/CRITICAL. A status dot reflects the overall
verdict (green nominal / amber degraded / red failed) and blinks briefly when
the verdict first turns "failed". Event rows are destroyed and rebuilt on each
Kairos tick, mirroring pheme_panel's pattern.

Report-only: this panel reads what emanon.fetch() hands it and shows it.
It never acts on, fixes, or restarts anything. Kairos owns all timing — the
only self.after used here is a one-shot blink that terminates on its own.
"""

import tkinter as tk
from datetime import datetime

from theme import C, FONTS

# Most recent rows to render. emanon.fetch already caps at 40; we show the
# newest slice so the panel stays a fixed, readable height.
_MAX_ROWS = 12

# Truncate long messages so a row never wraps and blow out the layout.
_MSG_MAX = 48

# Fixed pixel height for the events area so the footer doesn't jump as the
# number of rows changes tick to tick.
_EVENTS_H = 220


class _Tooltip:
    """Minimal hover tooltip — reveals the full text of a truncated log line.

    A borderless Toplevel that appears near the pointer on <Enter> and is
    destroyed on <Leave>. Parented to the row label, so when Emanon rebuilds the
    rows every tick, Tk tears any open tip down along with its parent.
    """

    def __init__(self, widget: tk.Widget, text: str):
        self._widget = widget
        self._text = text
        self._tip: "tk.Toplevel | None" = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _e=None) -> None:
        if self._tip is not None or not self._text:
            return
        x = self._widget.winfo_pointerx() + 12
        y = self._widget.winfo_pointery() + 16
        self._tip = tk.Toplevel(self._widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        self._tip.configure(bg=C["border"])   # 1px themed frame around the label
        # wraplength bounds the width so a very long trace wraps onto multiple
        # lines instead of running off the screen edge.
        tk.Label(self._tip, text=self._text, font=FONTS["tiny"], fg=C["text1"],
                 bg=C["bar_bg"], justify="left", anchor="w", padx=6, pady=3,
                 wraplength=560).pack(padx=1, pady=1)

    def _hide(self, _e=None) -> None:
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


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

        # ── Events tail (fixed height, rebuilt each tick) ───────────────────
        self._events_frame = tk.Frame(self, bg=C["card"], height=_EVENTS_H)
        self._events_frame.pack(fill="both", expand=True, pady=(2, 0))
        self._events_frame.pack_propagate(False)

        # ── Footer: timestamp + counts ──────────────────────────────────────
        self._last_lbl = tk.Label(self, text="", font=FONTS["card_header"],
                                  fg=C["text3"], bg=C["card"], anchor="e")
        self._last_lbl.pack(fill="x", pady=(4, 0))

        # Flash state. We only blink on the transition into "failed", and the
        # blink is a chain of one-shot self.after calls that stops itself.
        self._status = "nominal"
        self._flash_after_id = None

    # ── Color helpers ──────────────────────────────────────────────────────

    def _level_color(self, level: str) -> str:
        if level in ("ERROR", "CRITICAL"):
            return C["red"]
        if level == "WARNING":
            return C["amber"]
        return C["text1"]  # INFO and anything unexpected → white

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

    # ── Row building ─────────────────────────────────────────────────────────

    def _clear_events(self) -> None:
        for w in self._events_frame.winfo_children():
            w.destroy()

    def _build_event_row(self, entry: dict) -> None:
        level = entry["level"]
        color = self._level_color(level)

        row = tk.Frame(self._events_frame, bg=C["card"])
        row.pack(fill="x", pady=1)

        # Time portion only — the full date would push every row too wide.
        ts = entry["ts"]
        if " " in ts:
            ts = ts.split(" ")[-1]
        tk.Label(row, text=ts, font=FONTS["tiny"], fg=C["text3"],
                 bg=C["card"], anchor="w").pack(side="left")

        tk.Label(row, text=f" {level:<5}", font=FONTS["tiny"], fg=color,
                 bg=C["card"], anchor="w").pack(side="left")

        logger_name = entry["logger"]
        if logger_name.startswith("METIS."):
            logger_name = logger_name[len("METIS."):]
        tk.Label(row, text=f" {logger_name}", font=FONTS["tiny"], fg=C["text2"],
                 bg=C["card"], anchor="w").pack(side="left")

        full_msg = entry["message"]
        shown = full_msg
        if len(full_msg) > _MSG_MAX:
            shown = full_msg[:_MSG_MAX - 1] + "…"
        msg_lbl = tk.Label(row, text=f"  {shown}", font=FONTS["tiny"], fg=color,
                           bg=C["card"], anchor="w")
        msg_lbl.pack(side="left", fill="x", expand=True)
        # Reveal the full line on hover when it was clipped, so a long WARNING
        # (e.g. an HTTPSConnectionPool trace) isn't unreachable behind the "…".
        if len(full_msg) > _MSG_MAX:
            _Tooltip(msg_lbl, full_msg)

    # ── Kairos update contract ─────────────────────────────────────────────

    def update(self, data) -> None:
        """Called by Kairos every 2s on the main thread. Rebuilds event rows."""
        if data is None:
            self._render_unavailable()
            return

        status = data.get("status", "nominal")
        entries = data.get("entries", [])
        err = data.get("error_count", 0)
        warn = data.get("warning_count", 0)

        # Rebuild the tail (destroy-and-rebuild, like pheme_panel).
        self._clear_events()
        if not entries:
            tk.Label(self._events_frame, text=data.get("note", "no events yet"),
                     font=FONTS["small_italic"], fg=C["text3"], bg=C["card"],
                     anchor="w").pack(anchor="w", pady=(4, 0))
        else:
            for entry in entries[-_MAX_ROWS:]:
                self._build_event_row(entry)

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
        self._clear_events()
        tk.Label(self._events_frame, text="watcher unavailable",
                 font=FONTS["small_italic"], fg=C["text3"], bg=C["card"],
                 anchor="w").pack(anchor="w", pady=(4, 0))
        self._dot.itemconfig(self._dot_id, fill=C["text3"])
        self._status_lbl.config(text="unavailable", fg=C["text3"])
        self._last_lbl.config(text="watcher unavailable")
