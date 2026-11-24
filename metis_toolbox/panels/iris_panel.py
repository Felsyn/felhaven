"""
Iris panel — the Weather Service's warnings, on every display at once.

Job:         Show weather warnings: the NOW banner, the ALERTS tab, Home's badge, the popup.

Contract:    AlertWatch is the one controller, main thread only, registered to
             Kairos's "iris" worker (felhaven.py). Kairos hands it iris.fetch()'s
             dict, or None when a check failed. It keeps the last good answer
             and when it came, whether the latest check failed, which alerts
             were SEEN (acknowledged) and which have POPPED UP. From those it
             builds one view and hands it to every display's show(view), so
             one acknowledge changes every display together: acknowledging
             calms every display of a state, never one of them (§12,
             2026-09-14, the Ammit alarm).
             Displays: AlertBanner (top of NOW), AlertsTab (the ALERTS tab),
             AlertBadge (a strip on Home), SidebarAlert (the Atmospherics row's
             subtitle), and AlertPopup, which the controller opens itself. A
             display that acts (acknowledge, open the tab) is handed the
             controller through attach(watch) when it is added.

Seen:        An alert is seen once acknowledged: its ACKNOWLEDGE on the ALERTS
             tab, the popup's, or closing the popup (Escape, the window's X),
             as Ammit's popup does. An alert that REFERENCES a seen one under
             the same event name is seen too: the Weather Service reissues a
             long warning under a new id when it extends it, and that must not
             pop up again. An upgrade (a Watch becoming a Warning) is a new
             event, so it pops up. Ids the feed no longer lists are forgotten:
             the Weather Service has ended them. "Seen" lives in memory, so a
             restart pops up a still-active warning once more; that writes no
             file. Seen is amber, "seen · still in effect", on every display.

Pops up:     A warning (or a warning or a watch: Settings › Alerts) not yet
             seen and not yet popped up opens the popup and, when Read aloud
             is on, is spoken once, the Ammit way (calliope stop, speak,
             end_turn). Advisories are only listed. The popup stands on its
             own, with a taskbar button of its own, not tied to the main
             window: Windows hides a window tied to a minimised one, and a
             warning must show while Felhaven is minimised (centred on the
             screen then, else across the main window).

Failure:     None after a good check keeps the last list, dimmed, under
             "couldn't check since <time> · retrying". A failure never reads as
             "none active". Home's badge says so too once the trouble is ten
             minutes old (one missed poll is not news).

Upstream:    felhaven.py (builds AlertWatch, adds the displays, registers it),
             panels/aura_panel.py (hosts AlertBanner and AlertsTab),
             panels/home_panel.py (hosts AlertBadge).
Downstream:  tools/iris.py (the data's shape: Kairos hands AlertWatch its
             fetch(), so nothing here imports it), themis.py (what pops up,
             and whether it is read aloud, read at each check), calliope.py
             (the voice), panels/sidebar.py (SidebarRow.set_alert, on the row
             felhaven.py hands SidebarAlert), theme.py (ScrollColumn, and
             place_alert for the popup),
             tools/virgil.py and panels/virgil_tooltip.py (the ALERTS tab's
             tooltips).

V0.03:       Walked through 2026-11-17 (the V0.03 walkthrough, CONVENTIONS
             §3), its first audit (born 2026-11-10, #61). The popup was tied
             to the main window (`transient`), so Windows opened it hidden
             while Felhaven was minimised (measured); it now stands alone
             (the owner's call). Two warnings at once are spoken "A and B",
             not "A, and B". set_on_open() (no caller) and the popup's
             unused _watch went. sidebar.py moved to Downstream (this module
             calls it), which gained the Virgil pair.
"""

import tkinter as tk
from datetime import datetime
from typing import Any, Callable, Optional

import calliope
import themis
from panels import virgil_tooltip
from theme import C, FONTS, ScrollColumn, place_alert
from tools import virgil

WARN = "⚠"
STALE_BADGE_S = 600             # Home mentions a failed check after ten minutes
_POPUP_MAX = 3                  # alerts the popup spells out; the rest are counted
_POP_TIERS = {"warnings": ("warning",), "warnings_watches": ("warning", "watch")}
_HEADLINE_TIERS = ("warning", "watch")      # what the banner, badge and sidebar show
_NOUNS = {"warning": ("Weather warning", "Weather warnings"),
          "watch": ("Weather watch", "Weather watches")}


# ── formatting (display concerns) ─────────────────────────────────────────────

def _local(iso: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(iso)).astimezone()
    except (TypeError, ValueError):
        return None


def _clock(dt: datetime) -> str:
    if themis.clock_24h():
        return dt.strftime("%H:%M")
    return dt.strftime("%I:%M %p").lstrip("0")


def _until(iso: Optional[str]) -> str:
    dt = _local(iso)
    return f"until {dt:%a} {_clock(dt)}" if dt else ""


def _issued(iso: Optional[str]) -> str:
    dt = _local(iso)
    if dt is None:
        return ""
    today = dt.date() == datetime.now().astimezone().date()
    return _clock(dt) if today else f"{dt:%a} {_clock(dt)}"


def _spoken_until(iso: Optional[str]) -> str:
    dt = _local(iso)
    if dt is None:
        return ""
    if themis.clock_24h():
        return f"until {dt:%A} {dt:%H:%M}"
    hour = dt.strftime("%I").lstrip("0")
    time = f"{hour} {dt:%p}" if dt.minute == 0 else f"{hour}:{dt:%M} {dt:%p}"
    return f"until {dt:%A} {time}"


def _paragraphs(text: str) -> str:
    """NWS text is hard-wrapped at about 70 columns; keep its paragraphs only."""
    return "\n\n".join(" ".join(p.split()) for p in text.split("\n\n") if p.strip())


def _short(event: str) -> str:
    """The sidebar's two words: 'Winter Storm Warning' -> 'Storm Warning'."""
    return " ".join(event.split()[-2:])


def _colours(alert: dict[str, Any], dim: bool = False) -> tuple[str, str]:
    """(text, border) for one alert. Red only for a warning nobody has seen;
    seen, a watch or an advisory is amber; a list that may be out of date is
    dimmed."""
    if dim:
        return C["text3"], C["border"]
    if alert["tier"] == "warning" and not alert["seen"]:
        return C["red_bright"], C["red"]
    return C["amber_bright"], C["amber"]


def _headline(view: dict[str, Any]) -> list[dict[str, Any]]:
    return [a for a in view["alerts"] if a["tier"] in _HEADLINE_TIERS]


def _hover_button(parent: tk.Widget, text: str, command: Callable[[], None], *,
                  bg: str, colour: str, border: Optional[str] = None) -> tk.Label:
    """A clickable Label with the house hover flip (§7); a border when asked."""
    lbl = tk.Label(parent, text=text, font=FONTS["small_bold"], fg=colour, bg=bg,
                   cursor="hand2", padx=12 if border else 0, pady=4 if border else 0,
                   highlightthickness=1 if border else 0,
                   highlightbackground=border or bg)
    lbl.bind("<Button-1>", lambda _e: command())
    lbl.bind("<Enter>", lambda _e: lbl.config(fg=C["amber"]))
    lbl.bind("<Leave>", lambda _e: lbl.config(fg=colour))
    return lbl


# ── the controller ────────────────────────────────────────────────────────────

class AlertWatch:
    """See the module docstring. `now` is a seam for tests (the ten minutes)."""

    def __init__(self, parent: tk.Misc, on_open: Optional[Callable[[], None]] = None,
                 now: Callable[[], datetime] = datetime.now) -> None:
        self._parent = parent
        self._on_open = on_open or (lambda: None)
        self._now = now
        self._displays: list[Any] = []
        self._data: Optional[dict[str, Any]] = None
        self._checked_at: Optional[datetime] = None
        self._first_failure: Optional[datetime] = None
        self._failed = False
        self._seen: dict[str, str] = {}                     # id -> event
        self._popped: dict[str, tuple[str, datetime]] = {}  # id -> (event, when)
        self.popup: Optional["AlertPopup"] = None

    # ── wiring ──
    def add_display(self, display: Any) -> None:
        attach = getattr(display, "attach", None)
        if attach is not None:
            attach(self)
        self._displays.append(display)
        display.show(self.view())

    # ── Kairos ──
    def update(self, data: Optional[dict[str, Any]]) -> None:
        if data is None:
            self._failed = True
            if self._first_failure is None:
                self._first_failure = self._now()
        else:
            self._failed = False
            self._first_failure = None
            self._data = data
            self._checked_at = self._now()
            self._carry_over(data.get("alerts") or [])
            self._pop_new(data.get("alerts") or [])
        self._render()

    # ── actions (from the displays) ──
    def acknowledge(self, ids: list[str]) -> None:
        for alert in self._alerts():
            if alert["id"] in ids:
                self._seen[alert["id"]] = alert["event"]
        self._sync_popup()
        self._render()

    def acknowledge_popup(self) -> None:
        self.acknowledge([a["id"] for a in self._pending()])

    def open_alerts(self) -> None:
        self._close_popup()
        self._on_open()

    # ── the view ──
    def view(self) -> dict[str, Any]:
        data = self._data
        live = bool(data and data.get("enabled") and data.get("covered"))
        if self._failed:
            state = "stale" if live else "failing"
        elif data is None:
            state = "unchecked"
        elif not data.get("enabled"):
            state = "off"
        elif not data.get("covered"):
            state = "outside"
        else:
            state = "checked"
        since = self._checked_at or self._first_failure
        trouble_s = ((self._now() - since).total_seconds()
                     if self._failed and since is not None else None)
        tiers = self._pop_tiers()
        alerts = []
        if state in ("checked", "stale") and data is not None:
            for a in data.get("alerts") or []:
                popped = self._popped.get(a["id"])
                alerts.append(dict(a, seen=a["id"] in self._seen,
                                   pops=a["tier"] in tiers,
                                   popped_at=popped[1] if popped else None))
        return {"state": state, "checked_at": self._checked_at, "since": since,
                "trouble_s": trouble_s, "alerts": alerts}

    # ── internals ──
    def _alerts(self) -> list[dict[str, Any]]:
        return list((self._data or {}).get("alerts") or [])

    @staticmethod
    def _pop_tiers() -> tuple[str, ...]:
        return _POP_TIERS.get(themis.alerts_popup(), _POP_TIERS["warnings"])

    def _carry_over(self, alerts: list[dict[str, Any]]) -> None:
        """Seen and popped follow a reissued alert under the same event name;
        alerts the feed no longer lists are forgotten (the NWS ended them)."""
        for a in alerts:
            refs = a.get("references") or []
            if a["id"] not in self._seen and any(
                    self._seen.get(r) == a["event"] for r in refs):
                self._seen[a["id"]] = a["event"]
            if a["id"] not in self._popped:
                for r in refs:
                    old = self._popped.get(r)
                    if old is not None and old[0] == a["event"]:
                        self._popped[a["id"]] = old
                        break
        live = {a["id"] for a in alerts}
        self._seen = {i: e for i, e in self._seen.items() if i in live}
        self._popped = {i: p for i, p in self._popped.items() if i in live}

    def _pending(self) -> list[dict[str, Any]]:
        """What the popup lists: popped up, not yet seen, still active."""
        return [a for a in self._alerts()
                if a["id"] in self._popped and a["id"] not in self._seen]

    def _pop_new(self, alerts: list[dict[str, Any]]) -> None:
        tiers = self._pop_tiers()
        new = [a for a in alerts if a["tier"] in tiers
               and a["id"] not in self._seen and a["id"] not in self._popped]
        if not new:
            self._sync_popup()
            return
        now = self._now()
        for a in new:
            self._popped[a["id"]] = (a["event"], now)
        read_aloud = themis.alerts_read_aloud()
        if self.popup is None:
            self.popup = AlertPopup(self._parent, self)
        self.popup.show(self._pending(), read_aloud)
        if read_aloud:
            self._announce(new)

    def _sync_popup(self) -> None:
        if self.popup is None:
            return
        pending = self._pending()
        if pending:
            self.popup.show(pending, themis.alerts_read_aloud())
        else:
            self._close_popup()

    def _close_popup(self) -> None:
        if self.popup is not None:
            try:
                self.popup.destroy()
            except tk.TclError:
                pass                    # already gone with its window
            self.popup = None

    def _announce(self, new: list[dict[str, Any]]) -> None:
        tier = "warning" if any(a["tier"] == "warning" for a in new) else "watch"
        one, many = _NOUNS[tier]
        if len(new) == 1:
            a = new[0]
            until = _spoken_until(a["until"])
            text = f"{one}: {a['event']}{', ' + until if until else ''}. {a['summary']}"
        else:
            last = new[-1]["event"]
            names = (f"{new[0]['event']} and {last}" if len(new) == 2 else
                     ", ".join(a["event"] for a in new[:-1]) + f", and {last}")
            text = f"{many}: {names}. {new[0]['summary']}"
        calliope.stop()
        calliope.speak(text)
        calliope.end_turn()

    def _render(self) -> None:
        view = self.view()
        for display in self._displays:
            display.show(view)


# ── the displays ──────────────────────────────────────────────────────────────

class AlertBanner(tk.Frame):
    """Top of NOW: the most severe warning or watch, packed before `before`
    (the temperature) while one is active, and gone otherwise."""

    def __init__(self, parent: tk.Widget, before: tk.Widget) -> None:
        super().__init__(parent, bg=C["card"], highlightthickness=1,
                         highlightbackground=C["red"], padx=8, pady=5)
        self._before = before
        self._watch: Optional[AlertWatch] = None
        self.shown = False
        top = tk.Frame(self, bg=C["card"])
        top.pack(fill="x")
        self.until = tk.Label(top, text="", font=FONTS["tiny"], fg=C["text2"],
                              bg=C["card"])
        self.until.pack(side="right")
        self.title = tk.Label(top, text="", font=FONTS["small_bold"], bg=C["card"],
                              anchor="w")
        self.title.pack(side="left", fill="x", expand=True)
        row = tk.Frame(self, bg=C["card"])
        row.pack(fill="x", pady=(2, 0))
        self.details = _hover_button(row, "details ›", self._open, bg=C["card"],
                                     colour=C["text1"])
        self.details.config(font=FONTS["tiny"])
        self.details.pack(side="right", anchor="s")
        self.summary = tk.Label(row, text="", font=FONTS["tiny"], fg=C["text2"],
                                bg=C["card"], anchor="w", justify="left")
        self.summary.pack(side="left", fill="x", expand=True)
        self.note = tk.Label(self, text="", font=FONTS["tiny"], fg=C["amber_bright"],
                             bg=C["card"], anchor="w")
        self.bind("<Configure>", self._on_resize)

    def attach(self, watch: AlertWatch) -> None:
        self._watch = watch

    def _open(self) -> None:
        if self._watch is not None:
            self._watch.open_alerts()

    def _on_resize(self, event) -> None:
        self.summary.config(wraplength=max(120, event.width - 110))

    def show(self, view: dict[str, Any]) -> None:
        heads = _headline(view)
        if not heads:
            if self.shown:
                self.pack_forget()
                self.shown = False
            return
        lead = heads[0]
        text_c, border_c = _colours(lead, dim=view["state"] == "stale")
        self.config(highlightbackground=border_c)
        self.title.config(text=f"{WARN} {lead['event'].upper()}", fg=text_c)
        self.until.config(text=_until(lead["until"]))
        self.summary.config(text=lead["summary"])
        bits = ["SEEN · STILL IN EFFECT"] if lead["seen"] else []
        if len(heads) > 1:
            bits.append(f"+{len(heads) - 1} more")
        if bits:
            self.note.config(text=" · ".join(bits),
                             fg=C["amber_bright"] if lead["seen"] else C["text2"])
            self.note.pack(fill="x", pady=(2, 0))
        else:
            self.note.pack_forget()
        if not self.shown:
            self.pack(fill="x", pady=(6, 0), before=self._before)
            self.shown = True


class AlertsTab(tk.Frame):
    """The ALERTS tab: a status line that always says whether the list was
    checked, then the alerts, each with what it is and what was done about it.
    `on_label(text, colour)` lets the host colour its tab ("ALERTS · 2")."""

    _ALL_CLEAR = ("A checked answer. When the feed can't be reached, "
                  "this tab says so instead.")

    # Hover tooltips (Virgil): each readout says what it means, so each is a
    # glossary entry. "status" is the line above the list; the tags are on
    # every card. test_virgil_surfaces holds this table to its widgets.
    _TIPS = {
        "status":    virgil.term("weather_alert"),
        "severity":  virgil.term("alert_severity"),
        "certainty": virgil.term("alert_certainty"),
    }

    def __init__(self, parent: tk.Widget,
                 on_label: Optional[Callable[[str, Optional[str]], None]] = None) -> None:
        super().__init__(parent, bg=C["card"])
        self._on_label = on_label or (lambda _t, _c: None)
        self._watch: Optional[AlertWatch] = None
        self._signature: Any = None
        self._wrapped: list[tuple[tk.Label, int]] = []     # (label, px kept clear)
        self._wrap = 420
        # The footer is packed first, so the list can't push it out (§7).
        tk.Label(self, text="A second channel: keep your phone's emergency alerts on.",
                 font=FONTS["tiny"], fg=C["text3"], bg=C["card"], anchor="w",
                 ).pack(side="bottom", fill="x", pady=(6, 0))
        status = tk.Frame(self, bg=C["card"])
        status.pack(fill="x", pady=(8, 6))
        self._dot = tk.Canvas(status, width=8, height=8, bg=C["card"],
                              highlightthickness=0)
        self._dot_item = self._dot.create_oval(1, 1, 7, 7, fill=C["text3"], outline="")
        self._dot.pack(side="left", padx=(0, 6))
        self.status = tk.Label(status, text="not checked yet", font=FONTS["small"],
                               fg=C["text2"], bg=C["card"], anchor="w", justify="left")
        self.status.pack(side="left", fill="x", expand=True)
        virgil_tooltip.attach(self.status, self._TIPS["status"])
        self.column = ScrollColumn(self)
        self.column.pack(fill="both", expand=True)
        self.column.bind("<Configure>", self._on_resize, add="+")
        self.cards: list[tk.Frame] = []
        # Widgets carrying each tip, for test_virgil_surfaces (one per key).
        self.tip_targets: dict[str, tk.Misc] = {"status": self.status}

    def attach(self, watch: AlertWatch) -> None:
        self._watch = watch

    def _on_resize(self, event) -> None:
        wrap = max(160, event.width - 40)
        if abs(wrap - self._wrap) < 8:
            return
        self._wrap = wrap
        self.status.config(wraplength=wrap)
        for label, clear in self._wrapped:
            label.config(wraplength=max(80, wrap - clear))

    def _acknowledge(self, alert_id: str) -> None:
        if self._watch is not None:
            self._watch.acknowledge([alert_id])

    @staticmethod
    def _status(view: dict[str, Any]) -> tuple[str, str, str]:
        """(dot, text colour, text) for the line above the list."""
        state, alerts = view["state"], view["alerts"]
        checked = _clock(view["checked_at"]) if view["checked_at"] else ""
        if state == "off":
            return C["text3"], C["text2"], "weather alerts are off (Settings › Alerts)"
        if state == "outside":
            return (C["text3"], C["text2"], f"checked {checked} · outside the National "
                    "Weather Service's area (US only)")
        if state == "failing":
            return C["amber"], C["amber_bright"], "couldn't check yet · retrying"
        if state == "stale":
            return (C["amber"], C["amber_bright"], f"couldn't check since {checked} · "
                    "retrying · this list may be out of date")
        if state == "checked":
            count = f"{len(alerts)} active" if alerts else "none active"
            return C["green"], C["text2"], f"checked {checked} · National Weather Service · {count}"
        return C["text3"], C["text2"], "not checked yet"

    def show(self, view: dict[str, Any]) -> None:
        dot, colour, text = self._status(view)
        self._dot.itemconfigure(self._dot_item, fill=dot)
        self.status.config(text=text, fg=colour)
        alerts = view["alerts"]
        if not alerts:
            self._on_label("ALERTS", None)
        else:
            unseen_warning = any(a["tier"] == "warning" and not a["seen"] for a in alerts)
            self._on_label(f"ALERTS · {len(alerts)}",
                           C["red_bright"] if unseen_warning else C["amber_bright"])
        signature = (view["state"], tuple((a["id"], a["seen"], a["pops"],
                                           a["popped_at"]) for a in alerts))
        if signature != self._signature:
            self._signature = signature
            self._rebuild(view)

    def _label(self, parent: tk.Widget, text: str, *, font: str = "tiny",
               fg: str = C["text2"], bg: str = C["bg"], wrap: bool = False,
               clear: int = 24, centre: bool = False, **pack: Any) -> tk.Label:
        """A label in the list; `wrap` keeps it `clear` px inside the column
        as the tab is resized."""
        lbl = tk.Label(parent, text=text, font=FONTS[font], fg=fg, bg=bg,
                       anchor="center" if centre else "w",
                       justify="center" if centre else "left",
                       wraplength=max(80, self._wrap - clear) if wrap else 0)
        lbl.pack(**({"fill": "x"} | pack))
        if wrap:
            self._wrapped.append((lbl, clear))
        return lbl

    def _rebuild(self, view: dict[str, Any]) -> None:
        for child in self.column.body.winfo_children():
            child.destroy()
        self._wrapped, self.cards = [], []
        self.tip_targets = {"status": self.status}
        if view["state"] == "checked" and not view["alerts"]:
            tk.Label(self.column.body, text="Nothing active for this location.",
                     font=FONTS["small"], fg=C["text1"], bg=C["card"]).pack(pady=(30, 4))
            self._label(self.column.body, self._ALL_CLEAR, fg=C["text3"], bg=C["card"],
                        wrap=True, centre=True)
            return
        dim = view["state"] == "stale"
        for alert in view["alerts"]:
            self.cards.append(self._card(alert, dim))
        self.column.scroll_to_top()

    def _card(self, a: dict[str, Any], dim: bool) -> tk.Frame:
        text_c, border_c = _colours(a, dim)
        bg = C["bg"]
        card = tk.Frame(self.column.body, bg=bg, highlightthickness=1,
                        highlightbackground=border_c, padx=8, pady=6)
        card.pack(fill="x", pady=(0, 8), padx=(0, 6))
        top = tk.Frame(card, bg=bg)
        top.pack(fill="x")
        for key in ("certainty", "severity"):                # right to left
            if a[key]:
                tag = tk.Label(top, text=a[key], font=FONTS["tiny"], fg=C["text2"],
                               bg=bg, padx=4, highlightthickness=1,
                               highlightbackground=C["border"])
                tag.pack(side="right", padx=(6, 0))
                virgil_tooltip.attach(tag, self._TIPS[key])
                self.tip_targets.setdefault(key, tag)
        warn = f"{WARN} " if a["tier"] == "warning" else ""
        tk.Label(top, text=warn + a["event"].upper(), font=FONTS["small_bold"],
                 fg=text_c, bg=bg, anchor="w").pack(side="left", fill="x", expand=True)
        when = " · ".join(t for t in (
            f"in effect {_until(a['until'])}" if a["until"] else "",
            f"issued {_issued(a['sent'])}" if a["sent"] else "") if t)
        self._label(card, when, fg=C["text3"], pady=(2, 0))
        if a["description"]:
            self._label(card, _paragraphs(a["description"]), wrap=True, pady=(5, 0))
        if a["instruction"]:
            self._label(card, _paragraphs(a["instruction"]), fg=C["text3"], wrap=True,
                        pady=(5, 0))
        actions = tk.Frame(card, bg=bg)
        actions.pack(fill="x", pady=(8, 0))
        if a["tier"] in _HEADLINE_TIERS:
            if a["seen"]:
                tk.Label(actions, text="SEEN · STILL IN EFFECT", font=FONTS["small_bold"],
                         fg=C["amber_bright"], bg=bg).pack(side="left")
                note = "amber on every display until the Weather Service ends it"
            else:
                _hover_button(actions, "ACKNOWLEDGE", lambda i=a["id"]: self._acknowledge(i),
                              bg=bg, colour=C["text1"]).pack(side="left")
                note = (f"popped up at {_clock(a['popped_at'])}" if a["popped_at"]
                        else "")
            if not a["pops"]:
                note = " · ".join(t for t in (note, "listed only: watches don't pop up "
                                              "(Settings › Alerts)") if t)
        else:
            note = "listed only: advisories don't pop up (Settings › Alerts)"
        if note:
            # Beside the action, wrapped short of the card's edge (the clear
            # leaves room for ACKNOWLEDGE / SEEN · STILL IN EFFECT).
            beside = a["tier"] in _HEADLINE_TIERS
            self._label(actions, note, fg=C["text3"], bg=bg, wrap=True,
                        clear=230 if beside else 24, side="left", fill="none",
                        padx=(14 if beside else 0, 0))
        return card


class AlertBadge(tk.Frame):
    """Home's strip, above the chat input: the most severe warning or watch
    while one is active, or a failed check once it is ten minutes old. A click
    opens the ALERTS tab. Its own row, not a status-bar readout: that bar has
    next to no room to spare at the 760 px minimum window.

    It packs just above `before`, or just above `above` while that is showing
    (_anchor). Home passes its aurora line as `above`: that line packs above
    the same dock, so without it whichever strip showed later would sit lower,
    and a warning arriving after the aurora line would land under it."""

    def __init__(self, parent: tk.Widget, before: tk.Widget,
                 above: Optional[tk.Widget] = None) -> None:
        super().__init__(parent, bg=C["bg"], cursor="hand2")
        self._before = before
        self._above = above
        self._watch: Optional[AlertWatch] = None
        self.shown = False
        self._dot = tk.Canvas(self, width=8, height=8, bg=C["bg"], highlightthickness=0)
        self._dot_item = self._dot.create_oval(1, 1, 7, 7, fill=C["red"], outline="")
        self._dot.pack(side="left", padx=(0, 6))
        self.label = tk.Label(self, text="", font=FONTS["small_bold"], fg=C["red_bright"],
                              bg=C["bg"], anchor="w", cursor="hand2")
        self.label.pack(side="left", fill="x", expand=True)
        for widget in (self, self._dot, self.label):
            widget.bind("<Button-1>", lambda _e: self._open())

    def attach(self, watch: AlertWatch) -> None:
        self._watch = watch

    def _open(self) -> None:
        if self._watch is not None:
            self._watch.open_alerts()

    def show(self, view: dict[str, Any]) -> None:
        heads = _headline(view)
        trouble = (view["trouble_s"] is not None and view["trouble_s"] >= STALE_BADGE_S)
        since = f"couldn't check since {_clock(view['since'])}" if view["since"] else ""
        if heads:
            lead = heads[0]
            colour = _colours(lead)[0]
            text = f"{WARN} {lead['event'].upper()}" + (" · seen" if lead["seen"] else "")
            if len(heads) > 1:
                text += f"  +{len(heads) - 1} more"
            if trouble:
                text += f" · {since}"
        elif trouble:
            colour, text = C["amber"], f"weather alerts: {since}"
        else:
            if self.shown:
                self.pack_forget()
                self.shown = False
            return
        self.label.config(text=text, fg=colour)
        self._dot.itemconfigure(self._dot_item, fill=colour)
        if not self.shown:
            self.pack(fill="x", pady=(4, 0), before=self._anchor())
            self.shown = True

    def _anchor(self) -> tk.Widget:
        """The widget to pack just above: `above` while it is showing, else
        `before`. A strip that shows after this one packs above `before` too,
        which puts it below this one, so the order holds both ways round."""
        if self._above is not None and self._above.winfo_manager():
            return self._above
        return self._before


class SidebarAlert:
    """The Atmospherics row's subtitle: "⚠ Storm Warning" while a warning or
    watch is active, back to the temperature when it ends."""

    def __init__(self, row: Any) -> None:
        self._row = row

    def show(self, view: dict[str, Any]) -> None:
        heads = _headline(view)
        if not heads:
            self._row.set_alert(None)
            return
        lead = heads[0]
        self._row.set_alert(f"{WARN} {_short(lead['event'])}", _colours(lead)[0])


class AlertPopup(tk.Toplevel):
    """Ammit's popup shape (no grab, -topmost, Escape and the X both dismiss,
    and dismissing is acknowledging), listing the warnings that popped up and
    haven't been seen. Unlike Ammit's, it is not `transient` to the main
    window: Windows hides a window tied to a minimised one, so a warning that
    arrived while Felhaven was minimised opened unseen."""

    def __init__(self, parent: tk.Misc, watch: AlertWatch) -> None:
        super().__init__(parent, bg=C["card"], highlightthickness=2,
                         highlightbackground=C["red"])
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.bind("<Escape>", lambda _e: watch.acknowledge_popup())
        self.protocol("WM_DELETE_WINDOW", watch.acknowledge_popup)
        foot = tk.Frame(self, bg=C["card"])
        foot.pack(side="bottom", fill="x", padx=18, pady=(6, 16))
        self.ack = _hover_button(foot, "ACKNOWLEDGE", watch.acknowledge_popup,
                                 bg=C["card"], colour=C["text1"], border=C["text1"])
        self.ack.pack(side="left")
        self.open = _hover_button(foot, "OPEN ALERTS", watch.open_alerts, bg=C["card"],
                                  colour=C["text2"], border=C["border"])
        self.open.pack(side="left", padx=(12, 0))
        self._body = tk.Frame(self, bg=C["card"])
        self._body.pack(fill="both", expand=True, padx=18, pady=(16, 0))
        self._placed = False

    def show(self, alerts: list[dict[str, Any]], read_aloud: bool) -> None:
        for child in self._body.winfo_children():
            child.destroy()
        warning = any(a["tier"] == "warning" for a in alerts)
        self.title("FELHAVEN — WEATHER WARNING" if warning else "FELHAVEN — WEATHER WATCH")
        self.config(highlightbackground=C["red"] if warning else C["amber"])
        for i, a in enumerate(alerts[:_POPUP_MAX]):
            text_c = C["red_bright"] if a["tier"] == "warning" else C["amber_bright"]
            tk.Label(self._body, text=f"{WARN} {a['event'].upper()}", font=FONTS["large_bold"],
                     fg=text_c, bg=C["card"], anchor="w").pack(fill="x", pady=(10 if i else 0, 0))
            tk.Label(self._body, text=" · ".join(t for t in (
                         _until(a["until"]), "National Weather Service") if t),
                     font=FONTS["tiny"], fg=C["text3"], bg=C["card"], anchor="w",
                     ).pack(fill="x", pady=(2, 0))
            tk.Label(self._body, text=a["summary"], font=FONTS["small"], fg=C["text2"],
                     bg=C["card"], anchor="w", justify="left", wraplength=400,
                     ).pack(fill="x", pady=(6, 0))
        if len(alerts) > _POPUP_MAX:
            tk.Label(self._body, text=f"+{len(alerts) - _POPUP_MAX} more — OPEN ALERTS lists them",
                     font=FONTS["tiny"], fg=C["text2"], bg=C["card"], anchor="w",
                     ).pack(fill="x", pady=(8, 0))
        if read_aloud:
            tk.Label(self._body, text="read aloud by Calliope", font=FONTS["tiny"],
                     fg=C["text3"], bg=C["card"], anchor="w").pack(fill="x", pady=(8, 0))
        if not self._placed:
            place_alert(self)
            self._placed = True

