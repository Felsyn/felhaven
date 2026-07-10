"""
Horai panel — temporal context display for the Felhaven dashboard.

HoraiPanel      — clock + date + cycle/season badges, hosts AmmitWidget.
AmmitWidget     — single countdown timer inside HoraiPanel.

Both widgets read/write state via the horai and ammit tool modules.
"""

import tkinter as tk
from datetime import datetime

import themis
from theme import C, FONTS, Card

from tools import horai
from tools import ammit


# ─────────────────────────────────────────────────────────────────────────────
#  AmmitWidget — three countdown timers
# ─────────────────────────────────────────────────────────────────────────────

class AmmitWidget(tk.Frame):
    """
    Single countdown timer embedded inside HoraiPanel.
    Type a number (minutes) and press Enter or ▶ to start.
    ▶/■ toggles run/pause. ↺ resets.
    Reads/writes via ammit.py — same state file Metis sees.
    """

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        # Divider
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", pady=(8, 6))

        # Section header row with toggle
        header = tk.Frame(self, bg=C["card"], cursor="hand2")
        header.pack(fill="x")

        title_lbl = tk.Label(header, text="AMMIT — COUNTDOWN", font=FONTS["card_header"],
                             fg=C["text3"], bg=C["card"], anchor="w", cursor="hand2")
        title_lbl.pack(side="left")

        self._toggle_lbl = tk.Label(header, text="▶", font=FONTS["card_header"],
                                     fg=C["text3"], bg=C["card"], cursor="hand2")
        self._toggle_lbl.pack(side="right")

        # The whole header row toggles, not just the arrow — bind every child to
        # the same callback (sidebar.py's pattern). Users aim at the title text.
        for w in (header, title_lbl, self._toggle_lbl):
            w.bind("<Button-1>", lambda e: self._section_toggle())
        self._toggle_lbl.bind("<Enter>", lambda e: self._toggle_lbl.config(fg=C["text1"]))
        self._toggle_lbl.bind("<Leave>", lambda e: self._toggle_lbl.config(fg=C["text3"]))

        # Content frame — collapsed by default
        self._body = tk.Frame(self, bg=C["card"])
        self._collapsed = True
        # Do NOT pack self._body here — starts hidden

        row = tk.Frame(self._body, bg=C["card"])
        row.pack(fill="x", pady=2)

        # HH:MM:SS display
        self._display = tk.Label(row, text="00:00:00", font=FONTS["large_bold"],
                                 fg=C["amber"], bg=C["card"], width=8, anchor="w")
        self._display.pack(side="left", padx=(0, 8))

        # Minute input
        self._entry = tk.Entry(row, font=FONTS["small"], bg=C["bar_bg"],
                               fg=C["text1"], insertbackground=C["text1"],
                               relief="flat", width=5,
                               highlightbackground=C["border"], highlightthickness=1)
        self._entry.insert(0, "min")
        self._entry.config(fg=C["text3"])
        self._entry.pack(side="left", padx=(0, 4))
        self._entry.bind("<FocusIn>",  lambda e: self._clear_ph())
        self._entry.bind("<FocusOut>", lambda e: self._show_ph())
        self._entry.bind("<Return>",   lambda e: self._start())

        # Run / Pause toggle
        self._btn = tk.Label(row, text="▶", font=FONTS["body"],
                             fg=C["teal"], bg=C["card"], cursor="hand2")
        self._btn.pack(side="left", padx=(0, 4))
        self._btn.bind("<Button-1>", lambda e: self._toggle())

        # Reset
        rst = tk.Label(row, text="↺", font=FONTS["body"],
                       fg=C["text3"], bg=C["card"], cursor="hand2")
        rst.pack(side="left")
        rst.bind("<Button-1>", lambda e: self._reset())
        rst.bind("<Enter>",    lambda e: rst.config(fg=C["red"]))
        rst.bind("<Leave>",    lambda e: rst.config(fg=C["text3"]))

        self._refresh()

    # ── Section toggle ────────────────────────────────────────────────────────

    def _section_toggle(self):
        if self._collapsed:
            self._body.pack(fill="x")
            self._toggle_lbl.config(text="▼")
            self._collapsed = False
        else:
            self._body.pack_forget()
            self._toggle_lbl.config(text="▶")
            self._collapsed = True

    # ── Placeholder helpers ───────────────────────────────────────────────────

    def _clear_ph(self):
        if self._entry.get() == "min":
            self._entry.delete(0, "end")
            self._entry.config(fg=C["text1"])

    def _show_ph(self):
        if not self._entry.get().strip():
            self._entry.insert(0, "min")
            self._entry.config(fg=C["text3"])

    # ── Timer actions ─────────────────────────────────────────────────────────

    def _start(self):
        raw = self._entry.get().strip()
        try:
            minutes = float(raw)
            if minutes <= 0:
                return
        except ValueError:
            return
        ammit.start_timer(0, int(minutes * 60))
        self._entry.delete(0, "end")
        self._show_ph()
        self._refresh()

    def _toggle(self):
        t = ammit.query_all()[0]
        if t["running"]:
            ammit.stop_timer(0)
        elif t["remaining_seconds"] > 0:
            ammit.start_timer(0, t["remaining_seconds"])
        self._refresh()

    def _reset(self):
        ammit.reset_timer(0)
        self._refresh()

    # ── Display refresh ───────────────────────────────────────────────────────

    def _refresh(self):
        t = ammit.query_all()[0]
        self._display.config(text=t["display"])
        if t["expired"]:
            self._display.config(fg=C["red"])
            self._btn.config(text="■", fg=C["red"])
        elif t["running"]:
            self._display.config(fg=C["amber"])
            self._btn.config(text="■", fg=C["amber"])
        else:
            self._display.config(fg=C["text3"])
            self._btn.config(text="▶", fg=C["teal"])


# ─────────────────────────────────────────────────────────────────────────────
#  HoraiPanel — clock, date, cycle/season, + Ammit timers
# ─────────────────────────────────────────────────────────────────────────────

class HoraiPanel(Card):
    """
    Uses horai.handle() for temporal context.
    Hosts AmmitWidget for countdown timers.
    """

    def __init__(self, parent):
        super().__init__(parent, "Chronometry — temporal context", C["amber"])

        self.clock_lbl = tk.Label(self.body, text="", font=FONTS["xlarge_bold"],
                                  fg=C["text1"], bg=C["card"], anchor="w")
        self.clock_lbl.pack(fill="x", pady=(6, 0))

        self.date_lbl = tk.Label(self.body, text="", font=FONTS["small"],
                                 fg=C["text2"], bg=C["card"], anchor="w")
        self.date_lbl.pack(fill="x")

        self.badge_frame = tk.Frame(self.body, bg=C["card"])
        self.badge_frame.pack(fill="x", pady=(4, 0))

        self.cycle_lbl = tk.Label(self.badge_frame, text="", font=FONTS["tiny"],
                                  fg=C["badge_fg"], bg=C["badge_bg"], padx=6, pady=1)
        self.cycle_lbl.pack(side="left", padx=(0, 4))

        self.season_lbl = tk.Label(self.badge_frame, text="", font=FONTS["tiny"],
                                   fg=C["season_fg"], bg=C["season_bg"], padx=6, pady=1)
        self.season_lbl.pack(side="left")

        # Ammit lives here
        self._ammit = AmmitWidget(self.body)
        self._ammit.pack(fill="x")

    def update(self, data: dict) -> None:
        """Called by Kairos every 1 s on the main thread."""
        if data is None:
            return
        now = datetime.now()
        # Settings clock format: 24-hour "%H:%M:%S", else 12-hour with AM/PM.
        clock_fmt = "%H:%M:%S" if themis.clock_24h() else "%I:%M:%S %p"
        self.clock_lbl.config(text=now.strftime(clock_fmt))
        self.date_lbl.config(text=data["clock"])
        self.cycle_lbl.config(text=data["cycle"]["label"])
        self.season_lbl.config(text=data["season"])
        self._ammit._refresh()
