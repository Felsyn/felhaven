"""
Horai panel — temporal context display for the Felhaven dashboard.

HoraiPanel      — clock + date + cycle/season badges, hosts AmmitWidget.
AmmitWidget     — single countdown timer inside HoraiPanel.
AmmitAlarm      — edge-detects Ammit's expiry into a popup + spoken event.
AmmitAlarmPopup — the red warning Toplevel AmmitAlarm opens.
AmmitBadge      — display-only Ammit status readout for OTHER panels (Home).

Both widgets read/write state via the horai and ammit tool modules.

V0.02: Audited 2026-07-24 — AmmitWidget's countdown is Kairos-driven (via
       HoraiPanel.update under horai's 1 s poll), not after()-based; §6 corrected.

2026-09-09: added AmmitAlarm/AmmitAlarmPopup/AmmitBadge. Ammit's own docstring
       used to say "no alarm (deliberate)" — that decision is now overridden by
       request. AmmitAlarm is owned by HoraiPanel (built once at startup and
       never destroyed, so it fires regardless of which sidebar view is
       showing) and rides the same Kairos "horai" tick AmmitWidget already
       does — no new thread or after() loop. AmmitBadge lets HomePanel mirror
       Ammit's state without duplicating the alarm-firing logic.
"""

import time
import tkinter as tk
from datetime import datetime
from typing import Optional

import calliope
import themis
from theme import C, FONTS, Card

from tools import horai
from tools import ammit


# ─────────────────────────────────────────────────────────────────────────────
#  AmmitWidget — single countdown timer
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
        if t["expired"] and not t["acknowledged"]:
            self._display.config(fg=C["red"])
            self._btn.config(text="■", fg=C["red"])
        elif t["expired"]:   # acknowledged — the alert was seen, calm back down
            self._display.config(fg=C["green"])
            self._btn.config(text="■", fg=C["green"])
        elif t["running"]:
            self._display.config(fg=C["amber"])
            self._btn.config(text="■", fg=C["amber"])
        else:
            self._display.config(fg=C["text3"])
            self._btn.config(text="▶", fg=C["teal"])


# ─────────────────────────────────────────────────────────────────────────────
#  AmmitAlarm / AmmitAlarmPopup — turns expiry into a popup + spoken event
# ─────────────────────────────────────────────────────────────────────────────

class AmmitAlarm:
    """Turns Ammit's derived `expired` LEVEL into an EVENT: fires on the
    False->True transition (popup + spoken announcement), then re-announces
    every ANNOUNCE_INTERVAL_S while the popup stays open, up to
    MAX_ANNOUNCEMENTS total. Holds until `expired` drops back to False, which
    happens automatically the moment the operator stops/resets/restarts the
    timer on the Horai view (ammit.stop_timer/reset_timer/start_timer all
    clear `running` or `started_at`) — no separate reset hook needed.

    Owned by HoraiPanel, which is built once at startup and never destroyed
    (felhaven._show_view only pack_forget()s it), so this fires regardless of
    which sidebar view is showing — the alarm has to interrupt Home's chat
    view just as readily as Horai's own.

    Dismiss also calls ammit.acknowledge_timer() — the only way to reach
    AmmitWidget and AmmitBadge, which are different panel instances that only
    share state through that module (see its 2026-09-09 docstring note). This
    does NOT stop or reset the timer itself (`running`/`remaining_seconds` are
    untouched) — it only tells both displays the alert has been SEEN, so they
    drop out of red/alarm styling back to green, in place of Stop/Reset which
    would actually clear the countdown.
    """

    ANNOUNCE_INTERVAL_S = 30.0
    MAX_ANNOUNCEMENTS = 3

    def __init__(self, parent: tk.Widget) -> None:
        self._parent = parent
        self._was_expired = False
        self._popup: Optional["AmmitAlarmPopup"] = None
        self._announce_count = 0
        self._last_announce = 0.0

    def check(self) -> None:
        t = ammit.query_all()[0]
        expired = t["expired"]
        if expired and not self._was_expired:
            self._fire()
        elif expired and self._popup is not None:
            self._maybe_reannounce()
        self._was_expired = expired

    def _fire(self) -> None:
        self._announce_count = 0
        self._popup = AmmitAlarmPopup(self._parent, on_dismiss=self._on_dismiss)
        self._announce()

    def _maybe_reannounce(self) -> None:
        if self._announce_count >= self.MAX_ANNOUNCEMENTS:
            return
        if time.monotonic() - self._last_announce >= self.ANNOUNCE_INTERVAL_S:
            self._announce()

    def _announce(self) -> None:
        calliope.stop()
        calliope.speak("Ammit: your timer has expired.")
        calliope.end_turn()
        self._announce_count += 1
        self._last_announce = time.monotonic()

    def _on_dismiss(self) -> None:
        ammit.acknowledge_timer(0)
        self._popup = None


class AmmitAlarmPopup(tk.Toplevel):
    """Red warning popup, copying RiteLog's Toplevel shape (transient, no
    grab_set(), Escape/WM_DELETE_WINDOW both dismiss). Diverges from RiteLog
    on one point: -topmost, since an alarm that can be buried behind another
    window defeats the point."""

    def __init__(self, parent: tk.Widget, on_dismiss) -> None:
        super().__init__(parent, bg=C["bg"])
        self._on_dismiss = on_dismiss

        self.title("FELHAVEN — TIMER EXPIRED")
        self.geometry("360x160")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.attributes("-topmost", True)
        self.bind("<Escape>", lambda _e: self._dismiss())
        self.protocol("WM_DELETE_WINDOW", self._dismiss)

        tk.Label(self, text="AMMIT", font=FONTS["xlarge_bold"],
                 fg=C["red"], bg=C["bg"]).pack(pady=(20, 4))
        tk.Label(self, text="Your timer has expired.", font=FONTS["small"],
                 fg=C["text2"], bg=C["bg"]).pack()

        foot = tk.Frame(self, bg=C["bg"])
        foot.pack(side="bottom", fill="x", padx=10, pady=10)
        self._button(foot, "DISMISS", self._dismiss, side="right")

    def _button(self, parent: tk.Widget, text: str, command, side: str = "left") -> tk.Label:
        """A clickable tk.Label with the house hover flip (§7)."""
        lbl = tk.Label(parent, text=text, font=FONTS["small"], fg=C["text3"],
                       bg=C["bg"], cursor="hand2", padx=6)
        lbl.pack(side=side)
        lbl.bind("<Button-1>", lambda _e: command())
        lbl.bind("<Enter>", lambda _e: lbl.config(fg=C["text1"]))
        lbl.bind("<Leave>", lambda _e: lbl.config(fg=C["text3"]))
        return lbl

    def _dismiss(self) -> None:
        self._on_dismiss()
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────
#  AmmitBadge — display-only Ammit status for panels other than HoraiPanel
# ─────────────────────────────────────────────────────────────────────────────

class AmmitBadge(tk.Frame):
    """Display-only Ammit status for panels other than HoraiPanel (currently
    just HomePanel). Same read as AmmitWidget._refresh() — idle/running/
    expired -> text3/amber/red — no controls, no side effects, safe to tick
    from multiple panels at once, same as AmmitWidget already is.

    An ACKNOWLEDGED expiry (the alarm popup was dismissed — see AmmitAlarm)
    reads as green rather than red — dismissing says "I've seen it", not "it's
    handled", so it deliberately does not fall back to the ordinary amber
    "running" look either. AmmitWidget does the same on acknowledgment, in
    step with this badge (both read the same ammit.py state)."""

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, bg=C["bg"])
        self._dot = tk.Canvas(self, width=8, height=8, bg=C["bg"], highlightthickness=0)
        self._dot_item = self._dot.create_oval(1, 1, 7, 7, fill=C["text3"], outline="")
        self._dot.pack(side="left", padx=(0, 4))
        self._label = tk.Label(self, text="AMMIT --:--", font=FONTS["small"],
                                fg=C["text3"], bg=C["bg"])
        self._label.pack(side="left")
        self.refresh()

    def refresh(self) -> None:
        t = ammit.query_all()[0]
        if t["expired"] and not t["acknowledged"]:
            colour, text = C["red"], "AMMIT TIME'S UP"
        elif t["expired"]:   # acknowledged — the alert was seen, calm back down
            colour, text = C["green"], f"AMMIT {t['display']}"
        elif t["running"]:
            colour, text = C["amber"], f"AMMIT {t['display']}"
        else:
            colour, text = C["text3"], "AMMIT --:--"
        self._label.config(fg=colour, text=text)
        self._dot.itemconfigure(self._dot_item, fill=colour)


# ─────────────────────────────────────────────────────────────────────────────
#  HoraiPanel — clock, date, cycle/season, + Ammit countdown
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
        self._alarm = AmmitAlarm(self)

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
        self._alarm.check()
