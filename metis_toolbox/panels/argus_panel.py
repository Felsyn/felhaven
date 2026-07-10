"""
Argus panel — network awareness display for the Felhaven dashboard.

ArgusPanel — a bare tk.Frame tab body inside ModeratiPanel (the ARGUS tab),
the network sibling of Emanon. A glanceable summary line ("veins and synapses
at a glance") over a scrollable body of collapsible sections: Connections,
Listening, Traffic, DNS cache, Firewall, Timeline.

Report-only: it reads what argus.fetch() hands it and shows it. It never blocks,
kills, or modifies a connection. Kairos owns all timing — there is no after()
here; the only interactivity is the user-driven ▶/▼ section toggles.

The panel HUMANIZES (argus.fetch shapes): bytes/s → "24.5 KB/s", epoch → "22s
ago", firewall profiles → colored dots. Same split as Aura → Helios/Selene.

Kairos contract (single method): update(data).
    data is None  -> summary shows "stale"; sections hold their last good state.
"""

import time
import tkinter as tk
from datetime import datetime

from theme import C, FONTS, PhosphorScroll

# Cap the rows handed to Tk per section. The body scrolls, but rebuilding an
# unbounded list every tick is neither useful nor cheap. Overflow is footnoted.
_MAX_CONN_ROWS = 100
_MAX_TL_ROWS   = 60

# Short codes for socket states so the status column stays narrow.
_STATUS_SHORT = {
    "ESTABLISHED": "EST", "LISTEN": "LSN", "TIME_WAIT": "TW", "CLOSE_WAIT": "CW",
    "SYN_SENT": "SYN", "SYN_RECV": "SYNR", "FIN_WAIT1": "FW1", "FIN_WAIT2": "FW2",
    "LAST_ACK": "LACK", "CLOSING": "CLSG", "CLOSE": "CLSD", "NONE": "—",
}


# ── Humanizers (display concern only — argus.fetch returns raw numbers) ──────

def _human_rate(bps: float) -> str:
    """Bytes/sec -> "24.5 KB/s" (decimal SI, the networking convention)."""
    bps = max(0, bps)
    if bps < 1000:
        return f"{int(bps)} B/s"
    if bps < 1_000_000:
        return f"{bps / 1e3:.1f} KB/s"
    if bps < 1_000_000_000:
        return f"{bps / 1e6:.1f} MB/s"
    return f"{bps / 1e9:.1f} GB/s"


def _ago(t: float, now: float) -> str:
    """Epoch -> "22s ago" / "3m ago" / "1h ago"."""
    d = max(0, int(now - t))
    if d < 60:
        return f"{d}s ago"
    if d < 3600:
        return f"{d // 60}m ago"
    return f"{d // 3600}h ago"


def _trunc(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n - 1] + "…"


def _status_color(status: str) -> str:
    if status == "ESTABLISHED":
        return C["teal"]            # live, calm
    if status == "LISTEN":
        return C["blue"]
    if status in ("SYN_SENT", "SYN_RECV"):
        return C["amber"]           # connecting
    return C["text3"]               # teardown / other — muted


# ─────────────────────────────────────────────────────────────────────────────
#  _ScrollFrame — a vertically scrollable container
#  Copied per the one-_ScrollFrame-per-panel house rule (CONVENTIONS §7), as
#  Pheme and Morpheus do. Pack content into `.inner`.
# ─────────────────────────────────────────────────────────────────────────────

class _ScrollFrame(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        self._canvas = tk.Canvas(self, bg=C["card"], highlightthickness=0, bd=0)
        scroll = PhosphorScroll(self, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scroll.set)

        scroll.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self.inner = tk.Frame(self._canvas, bg=C["card"])
        self._win  = self._canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>", self._on_inner_config)
        self._canvas.bind("<Configure>", self._on_canvas_config)

        # Wheel only while hovering -> stacked scroll frames don't fight.
        self._canvas.bind("<Enter>", self._bind_wheel)
        self._canvas.bind("<Leave>", self._unbind_wheel)

    def _on_inner_config(self, _event) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_config(self, event) -> None:
        self._canvas.itemconfigure(self._win, width=event.width)

    def _bind_wheel(self, _event) -> None:
        self._canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event) -> None:
        self._canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event) -> None:
        self._canvas.yview_scroll(int(-event.delta / 120), "units")


# ─────────────────────────────────────────────────────────────────────────────
#  _Section — a collapsible ▶/▼ section (the Helios/Selene precedent)
# ─────────────────────────────────────────────────────────────────────────────

class _Section(tk.Frame):
    """Divider + clickable header with a ▶/▼ toggle + a content frame that packs
    and unpacks. `on_show` fires when the section is expanded, so the panel can
    render its rows lazily (and only when visible)."""

    def __init__(self, parent, title: str, collapsed: bool = True, on_show=None):
        super().__init__(parent, bg=C["card"])
        self._on_show = on_show
        self.collapsed = collapsed

        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", pady=(6, 4))

        header = tk.Frame(self, bg=C["card"])
        header.pack(fill="x")
        self._title = tk.Label(header, text=title, font=FONTS["card_header"],
                               fg=C["text3"], bg=C["card"], anchor="w", cursor="hand2")
        self._title.pack(side="left")
        self._tog = tk.Label(header, text="▶" if collapsed else "▼",
                             font=FONTS["card_header"], fg=C["text3"],
                             bg=C["card"], cursor="hand2")
        self._tog.pack(side="right")

        self.content = tk.Frame(self, bg=C["card"])
        if not collapsed:
            self.content.pack(fill="x", pady=(2, 0))

        for w in (self._title, self._tog):
            w.bind("<Button-1>", lambda e: self.toggle())
        self._tog.bind("<Enter>", lambda e: self._tog.config(fg=C["text1"]))
        self._tog.bind("<Leave>", lambda e: self._tog.config(fg=C["text3"]))

    def set_title(self, text: str) -> None:
        self._title.config(text=text)

    def toggle(self) -> None:
        if self.collapsed:
            self.content.pack(fill="x", pady=(2, 0))
            self._tog.config(text="▼")
            self.collapsed = False
            if self._on_show:
                self._on_show()
        else:
            self.content.pack_forget()
            self._tog.config(text="▶")
            self.collapsed = True


# ─────────────────────────────────────────────────────────────────────────────
#  ArgusPanel
# ─────────────────────────────────────────────────────────────────────────────

class ArgusPanel(tk.Frame):
    """Receives network snapshots from Kairos via update() every 5 s.

    A bare Frame tab body inside ModeratiPanel (the ARGUS tab); keeps its own
    update() and is registered with Kairos directly. There is no Card header /
    set_summary here — the summary line lives in self.
    """

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        # ── Summary line (always visible) — the veins-and-synapses glance ────
        sumrow = tk.Frame(self, bg=C["card"])
        sumrow.pack(fill="x", pady=(6, 2))
        self._dot = tk.Canvas(sumrow, width=8, height=8, bg=C["card"],
                              highlightthickness=0)
        self._dot_id = self._dot.create_oval(1, 1, 7, 7, fill=C["teal"], outline="")
        self._dot.pack(side="left", padx=(0, 6))
        self._summary = tk.Label(sumrow, text="reading network…",
                                 font=FONTS["card_header"], fg=C["text2"],
                                 bg=C["card"], anchor="w")
        self._summary.pack(side="left")

        # ── Scrollable body of collapsible sections ──────────────────────────
        self._scroll = _ScrollFrame(self)
        self._scroll.pack(fill="both", expand=True, pady=(4, 0))
        body = self._scroll.inner

        self._payload: dict = {}        # section key -> latest data for lazy render
        self._sections: dict[str, _Section] = {}
        # Connections expanded by default (it answers the core question); the
        # rest opt-in so the tab opens glanceable, not as a wall of rows.
        for key, title, collapsed in (
            ("connections", "CONNECTIONS", False),
            ("listening",   "LISTENING",   True),
            ("traffic",     "TRAFFIC",     True),
            ("dns",         "DNS CACHE",   True),
            ("firewall",    "FIREWALL",    True),
            ("timeline",    "TIMELINE",    True),
        ):
            sec = _Section(body, title, collapsed=collapsed,
                           on_show=lambda k=key: self._render_section(k))
            sec.pack(fill="x")
            self._sections[key] = sec

        # ── Footer ───────────────────────────────────────────────────────────
        self._footer = tk.Label(self, text="", font=FONTS["card_header"],
                                fg=C["text3"], bg=C["card"], anchor="e")
        self._footer.pack(fill="x", pady=(4, 0))

    # ── Kairos update contract ───────────────────────────────────────────────

    def update(self, data) -> None:
        """Called by Kairos every 5 s on the main thread."""
        if data is None:
            # Hold the last good sections; signal staleness in the summary only.
            self._dot.itemconfig(self._dot_id, fill=C["text3"])
            self._summary.config(text="stale — network read unavailable",
                                 fg=C["text3"])
            self._footer.config(text="stale")
            return

        self._payload = {
            "connections": (data.get("connections", []), data.get("summary", {})),
            "listening":   data.get("listening", []),
            "traffic":     data.get("traffic", {}),
            "dns":         data.get("dns", {}),
            "firewall":    data.get("firewall", {}),
            "timeline":    data.get("timeline", []),
        }
        self._update_summary(data)
        self._refresh_titles(data)
        # Re-render only the sections the user has open; collapsed ones render
        # lazily on expand (via _Section.on_show) from the stored payload.
        for key, sec in self._sections.items():
            if not sec.collapsed:
                self._render_section(key)
        self._footer.config(
            text=f"updated {datetime.now():%H:%M:%S} · polled every 5s"
        )

    # ── Summary line ─────────────────────────────────────────────────────────

    def _update_summary(self, data: dict) -> None:
        s  = data.get("summary", {})
        t  = data.get("traffic", {})
        fw = data.get("firewall", {})
        self._summary.config(
            text=(f"{s.get('established', 0)} established · "
                  f"{s.get('listening', 0)} listening · "
                  f"▲{_human_rate(t.get('up_bps', 0))} "
                  f"▼{_human_rate(t.get('down_bps', 0))} · "
                  f"{self._shield(fw)}"),
            fg=C["text1"],
        )
        # Dot is health, not threat (Argus shows, never judges): teal when the
        # picture is complete, amber when visibility is degraded (a source down
        # or PIDs we couldn't attribute).
        degraded = (s.get("unresolved_pids", 0) > 0
                    or data.get("dns", {}).get("state") == "unavailable"
                    or fw.get("state") == "unavailable")
        self._dot.itemconfig(self._dot_id,
                             fill=C["amber"] if degraded else C["teal"])

    def _shield(self, fw: dict) -> str:
        if fw.get("state") != "ok":
            return "🛡 —"
        vals = [fw.get("domain"), fw.get("private"), fw.get("public")]
        if all(v == "on" for v in vals):
            return "🛡 on"
        if all(v == "off" for v in vals):
            return "🛡 off"
        return "🛡 partial"

    def _refresh_titles(self, data: dict) -> None:
        self._sections["connections"].set_title(
            f"CONNECTIONS ({len(data.get('connections', []))})")
        self._sections["listening"].set_title(
            f"LISTENING ({len(data.get('listening', []))})")
        self._sections["dns"].set_title(
            f"DNS CACHE ({len(data.get('dns', {}).get('entries', []))})")
        self._sections["timeline"].set_title(
            f"TIMELINE ({len(data.get('timeline', []))})")

    # ── Section rendering (destroy + rebuild the section's own content only) ──

    def _render_section(self, key: str) -> None:
        sec = self._sections[key]
        for w in sec.content.winfo_children():
            w.destroy()
        getattr(self, f"_render_{key}")(sec.content)

    def _render_connections(self, parent: tk.Frame) -> None:
        conns, summary = self._payload.get("connections", ([], {}))
        if not conns:
            self._empty(parent, "no active connections")
            return
        for c in conns[:_MAX_CONN_ROWS]:
            row = tk.Frame(parent, bg=C["card"])
            row.pack(fill="x")
            proc = c.get("proc", "—")
            tk.Label(row, text=_trunc(proc, 18), font=FONTS["tiny"],
                     fg=C["text1"] if proc != "—" else C["text3"], bg=C["card"],
                     width=18, anchor="w").pack(side="left")
            status = c.get("status", "")
            tk.Label(row, text=_STATUS_SHORT.get(status, status[:4]),
                     font=FONTS["tiny"], fg=_status_color(status), bg=C["card"],
                     width=5, anchor="e").pack(side="right")
            tk.Label(row, text=c.get("raddr", ""), font=FONTS["tiny"],
                     fg=C["text2"], bg=C["card"], anchor="w").pack(
                         side="left", fill="x", expand=True)
        notes = []
        extra = len(conns) - _MAX_CONN_ROWS
        if extra > 0:
            notes.append(f"+{extra} more")
        un = summary.get("unresolved_pids", 0)
        if un:
            notes.append(f"{un} unattributed (run as admin to see owners)")
        if notes:
            self._note(parent, " · ".join(notes))

    def _render_listening(self, parent: tk.Frame) -> None:
        rows = self._payload.get("listening", [])
        if not rows:
            self._empty(parent, "nothing listening")
            return
        for r in rows:
            row = tk.Frame(parent, bg=C["card"])
            row.pack(fill="x")
            proc = r.get("proc", "—")
            tk.Label(row, text=_trunc(proc, 18), font=FONTS["tiny"],
                     fg=C["text1"] if proc != "—" else C["text3"], bg=C["card"],
                     width=18, anchor="w").pack(side="left")
            tk.Label(row, text=r.get("proto", ""), font=FONTS["tiny"],
                     fg=C["text3"], bg=C["card"], width=4, anchor="w").pack(side="left")
            tk.Label(row, text=r.get("laddr", ""), font=FONTS["tiny"],
                     fg=C["text2"], bg=C["card"], anchor="w").pack(
                         side="left", fill="x", expand=True)

    def _render_traffic(self, parent: tk.Frame) -> None:
        t = self._payload.get("traffic", {})
        total = tk.Frame(parent, bg=C["card"])
        total.pack(fill="x")
        tk.Label(total, text="total", font=FONTS["tiny"], fg=C["text2"],
                 bg=C["card"], anchor="w").pack(side="left")
        tk.Label(total,
                 text=f"▲{_human_rate(t.get('up_bps', 0))}  "
                      f"▼{_human_rate(t.get('down_bps', 0))}",
                 font=FONTS["tiny"], fg=C["text1"], bg=C["card"],
                 anchor="e").pack(side="right")
        for nic, r in (t.get("per_nic") or {}).items():
            row = tk.Frame(parent, bg=C["card"])
            row.pack(fill="x")
            tk.Label(row, text=_trunc(nic, 16), font=FONTS["tiny"], fg=C["text3"],
                     bg=C["card"], anchor="w").pack(side="left")
            tk.Label(row,
                     text=f"▲{_human_rate(r['up_bps'])}  ▼{_human_rate(r['down_bps'])}",
                     font=FONTS["tiny"], fg=C["text2"], bg=C["card"],
                     anchor="e").pack(side="right")
        self._note(parent,
                   f"interface-level only · {t.get('window_s', 0)}s window · "
                   f"not per-process")

    def _render_dns(self, parent: tk.Frame) -> None:
        dns = self._payload.get("dns", {})
        state = dns.get("state")
        entries = dns.get("entries", [])
        if state == "unavailable":
            self._empty(parent, "cache unavailable (DNS Client service stopped?)")
            return
        if not entries:
            self._empty(parent, "cache empty")
            return
        for e in entries:
            row = tk.Frame(parent, bg=C["card"])
            row.pack(fill="x")
            tk.Label(row, text=_trunc(e.get("name", ""), 30), font=FONTS["tiny"],
                     fg=C["text1"], bg=C["card"], anchor="w").pack(side="left")
            tk.Label(row, text=_trunc(", ".join(e.get("records", [])), 28),
                     font=FONTS["tiny"], fg=C["text2"], bg=C["card"],
                     anchor="e").pack(side="right")
        age = _ago(dns["as_of"], time.time()) if dns.get("as_of") else "—"
        self._note(parent, f"resolver cache snapshot · refreshed {age}")

    def _render_firewall(self, parent: tk.Frame) -> None:
        fw = self._payload.get("firewall", {})
        if fw.get("state") != "ok":
            self._empty(parent, "firewall state unavailable")
            return
        for prof in ("domain", "private", "public"):
            val = fw.get(prof, "—")
            row = tk.Frame(parent, bg=C["card"])
            row.pack(fill="x")
            dot = tk.Canvas(row, width=8, height=8, bg=C["card"], highlightthickness=0)
            dot.create_oval(1, 1, 7, 7,
                            fill={"on": C["green"], "off": C["red"]}.get(val, C["text3"]),
                            outline="")
            dot.pack(side="left", padx=(0, 6))
            tk.Label(row, text=prof, font=FONTS["tiny"], fg=C["text2"],
                     bg=C["card"], anchor="w").pack(side="left")
            tk.Label(row, text=val, font=FONTS["tiny"], fg=C["text1"],
                     bg=C["card"], anchor="e").pack(side="right")

    def _render_timeline(self, parent: tk.Frame) -> None:
        events = self._payload.get("timeline", [])
        if not events:
            self._empty(parent, "no open/close events yet")
            return
        now = time.time()
        for ev in reversed(events[-_MAX_TL_ROWS:]):   # newest first
            opened = ev.get("event") == "open"
            row = tk.Frame(parent, bg=C["card"])
            row.pack(fill="x")
            tk.Label(row, text="＋" if opened else "－", font=FONTS["tiny"],
                     fg=C["green"] if opened else C["text3"], bg=C["card"],
                     width=2, anchor="w").pack(side="left")
            tk.Label(row, text=_ago(ev.get("t", 0), now), font=FONTS["tiny"],
                     fg=C["text3"], bg=C["card"], width=8, anchor="w").pack(side="left")
            tk.Label(row, text=_trunc(ev.get("proc", "—"), 14), font=FONTS["tiny"],
                     fg=C["text2"], bg=C["card"], width=14, anchor="w").pack(side="left")
            tk.Label(row, text=ev.get("raddr", ""), font=FONTS["tiny"],
                     fg=C["text2"], bg=C["card"], anchor="w").pack(
                         side="left", fill="x", expand=True)
        self._note(parent, "polled every 5s · sub-interval connections not shown")

    # ── small shared row helpers ─────────────────────────────────────────────

    def _empty(self, parent: tk.Frame, text: str) -> None:
        tk.Label(parent, text=text, font=FONTS["small_italic"], fg=C["text3"],
                 bg=C["card"], anchor="w").pack(anchor="w", pady=(2, 0))

    def _note(self, parent: tk.Frame, text: str) -> None:
        tk.Label(parent, text=text, font=FONTS["tiny"], fg=C["text3"],
                 bg=C["card"], anchor="w").pack(anchor="w", pady=(2, 0))
