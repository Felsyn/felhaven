"""
Horai panel — temporal context display for the Felhaven dashboard.

Job:         Show the clock, date, season and cycle, and run the countdown timer.

HoraiPanel      — clock + date + cycle/season badges, hosts AmmitWidget.
AmmitWidget     — single countdown timer inside HoraiPanel.
AmmitAlarm      — edge-detects Ammit's expiry into a popup + spoken event.
AmmitAlarmPopup — the red warning Toplevel AmmitAlarm opens.
AmmitBadge      — display-only Ammit status readout for OTHER panels (Home).

Horai's reading arrives through Kairos; every Ammit class reads (and the
countdown's controls write) through tools/ammit.py's functions.

Contract:    HoraiPanel.update(data) takes horai.fetch()'s dict, or None
             (kept as it is). AmmitBadge.refresh() is for another panel's own
             tick. Everything runs on the Tk thread, on horai's 1 s Kairos
             tick: no after() loop and no thread of its own (§6).
Upstream:    felhaven.py (builds HoraiPanel and registers it under Kairos's
             "horai" worker, tools.horai.fetch every 1 s),
             panels/home_panel.py (AmmitBadge, refreshed from Home's own
             "horai" registration)
Downstream:  tools/ammit.py (the timer's state), themis.py (the clock
             format, read per tick), calliope.py (the alarm's voice),
             theme.py (place_alert), tools/virgil.py and
             panels/virgil_tooltip.py (the countdown's tooltips)
Requires:    time, tkinter, datetime, typing (stdlib).

2026-07-24:  walkthrough audit — AmmitWidget's countdown is Kairos-driven
             (via HoraiPanel.update under horai's 1 s poll), not
             after()-based; §6 corrected.

2026-09-09: added AmmitAlarm/AmmitAlarmPopup/AmmitBadge. Ammit's own docstring
       used to say "no alarm (deliberate)" — that decision is now overridden by
       request. AmmitAlarm is owned by HoraiPanel (built once at startup and
       never destroyed, so it fires regardless of which sidebar view is
       showing) and rides the same Kairos "horai" tick AmmitWidget already
       does — no new thread or after() loop. AmmitBadge lets HomePanel mirror
       Ammit's state without duplicating the alarm-firing logic.

V0.03:       Walked through 2026-11-21 (the V0.03 walkthrough, CONVENTIONS
             §3; born 2026-06-10). The popup was tied to the main window, so
             Windows opened it hidden while Felhaven was minimised; it now
             stands alone, as Iris's does (BACKLOG). An acknowledged expiry
             no longer alarms again at every launch (the owner's pick), and
             a stop, reset or restart closes an open popup: one left open
             had acknowledged the next expiry when dismissed. The minutes
             box asks Ammit's minutes_to_seconds(): "0.01" started an
             instant alarm and "nan" raised. The unused horai import and the
             entry's own cursor colour went (§7).
"""

import time
import tkinter as tk
from datetime import datetime
from typing import Optional

import calliope
import themis
from panels import virgil_tooltip
from theme import C, FONTS, Card, place_alert

from tools import ammit
from tools import virgil


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

    # Hover tooltips (Virgil) for the countdown's controls, each a glossary
    # entry (CONVENTIONS §7). Keys name the widgets in self.tip_targets.
    # The fold header names its module, so it shows that module's gloss.
    _HEADER_TIP = virgil.module("ammit")
    _TIPS = {
        "minutes": virgil.term("ammit_minutes"),
        "run": virgil.term("ammit_run"),
        "reset": virgil.term("ammit_reset"),
    }

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
                               fg=C["text1"], relief="flat", width=5,
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

        self.tip_targets: dict[str, tk.Misc] = {
            "minutes": self._entry, "run": self._btn, "reset": rst}
        for key, tip in self._TIPS.items():
            virgil_tooltip.attach(self.tip_targets[key], tip)
        self.header = header
        virgil_tooltip.attach(header, self._HEADER_TIP)

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
        # Ammit's own check, the one the model's number meets: a whole number
        # of seconds, at least 1. Anything else leaves the box as typed.
        seconds = ammit.minutes_to_seconds(self._entry.get().strip())
        if seconds is None:
            return
        ammit.start_timer(0, seconds)
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
    happens automatically the moment the timer is stopped, reset or
    restarted, from the countdown or by Pythia (ammit.stop_timer/
    reset_timer/start_timer all clear `running` or `started_at`) — no
    separate reset hook needed. That drop also closes a popup still open:
    it no longer says anything true, and dismissing it would acknowledge
    whatever expired next.

    An expiry already ACKNOWLEDGED doesn't fire: at launch, the first check
    finds a timer dismissed before the restart still expired, and the
    countdown and Home's badge show it green, so the popup stays shut too
    (the owner's pick, §12). One that ran out while Felhaven was closed
    fires on that first check.

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
            if not t["acknowledged"]:
                self._fire()
        elif expired and self._popup is not None:
            self._maybe_reannounce()
        elif not expired and self._popup is not None:
            self._close()
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

    def _close(self) -> None:
        """Close the popup without acknowledging: there is nothing to see."""
        popup, self._popup = self._popup, None
        if popup is not None and popup.winfo_exists():
            popup.destroy()


class AmmitAlarmPopup(tk.Toplevel):
    """Red warning popup: no grab_set(), Escape and the X both dismiss, and
    -topmost, since an alarm that can be buried behind another window
    defeats the point. Not `transient` to the main window: Windows hides a
    window tied to a minimised one, so a timer that ran out while Felhaven
    was minimised opened unseen (measured 2026-11-21: tied, it stayed
    withdrawn; Iris's popup had the same gap, §12 2026-11-17). It stands
    alone, with its own taskbar button, centred over Felhaven, or over the
    screen while Felhaven is minimised."""

    def __init__(self, parent: tk.Widget, on_dismiss) -> None:
        super().__init__(parent, bg=C["bg"])
        self._on_dismiss = on_dismiss

        self.title("FELHAVEN — TIMER EXPIRED")
        self.resizable(False, False)
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
        place_alert(self, 360, 160)

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
    expired -> text3/amber/red, and a paused timer reads as idle — no
    controls, no side effects, safe to tick from multiple panels at once,
    same as AmmitWidget already is.

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
    Shows horai.fetch()'s reading, which Kairos hands to update().
    Hosts AmmitWidget, and owns the one AmmitAlarm.
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
