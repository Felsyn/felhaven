"""
Hephaestus panel — system vitals display for the Felhaven dashboard.

BarMeter      — small labelled percent bar (reusable by any panel that wants one).
VitalsPanel   — CPU / RAM / DISK bars + node/OS footer, fed hephaestus.fetch().
"""

import tkinter as tk

from panels import virgil_tooltip
from theme import C, FONTS
from tools import virgil


# ─────────────────────────────────────────────────────────────────────────────
#  BarMeter — labelled horizontal percent bar
# ─────────────────────────────────────────────────────────────────────────────

class BarMeter(tk.Frame):
    """Small labelled percent bar, reusable by any panel that wants one.

    V0.02:  Audited 2026-07-25 — set()'s clamp/format (0–100, "<1%" for a tiny
            nonzero) now covered by tests/test_hephaestus_panel_smoke.py.
            Class-scoped tag: AetherWidget (session 5) shares this file.
    """

    def __init__(self, parent, label: str, color: str = C["amber"]):
        super().__init__(parent, bg=C["card"])
        row = tk.Frame(self, bg=C["card"])
        row.pack(fill="x")
        tk.Label(row, text=label, font=FONTS["small"],
                 fg=C["text2"], bg=C["card"], anchor="w").pack(side="left")
        self._val = tk.Label(row, text="—", font=FONTS["small"],
                             fg=C["text1"], bg=C["card"], anchor="e")
        self._val.pack(side="right")
        bar_bg = tk.Frame(self, bg=C["bar_bg"], height=3)
        bar_bg.pack(fill="x", pady=(2, 4))
        bar_bg.pack_propagate(False)
        self._bar = tk.Frame(bar_bg, bg=color, height=3)
        self._bar.place(relx=0, rely=0, relheight=1, relwidth=0)

    def set(self, pct: float):
        pct = max(0.0, min(100.0, pct))
        text = "<1%" if 0 < pct < 1 else f"{round(pct)}%"
        self._val.config(text=text)
        self._bar.place_configure(relwidth=pct / 100)


# ─────────────────────────────────────────────────────────────────────────────
#  AetherWidget — WiFi, internet and Ollama, embedded in VitalsPanel
# ─────────────────────────────────────────────────────────────────────────────

# Dot colour per state, one map per row. Red is reserved for a KNOWN-bad link;
# "unknown" (and any state not listed) stays dim, so a wired machine or a probe
# that could not decide never reads as an alarm. A captive portal is amber.
_WIFI_DOTS     = {"connected": "green", "disconnected": "red"}
_INTERNET_DOTS = {"online": "green", "limited": "amber", "offline": "red"}
_OLLAMA_DOTS   = {"online": "green", "offline": "red"}


class AetherWidget(tk.Frame):
    """
    Connectivity status embedded inside VitalsPanel.
    Three rows — WiFi, Internet, Ollama — each with a colored dot.
    Receives data from Kairos via update() — registered as its own panel.

    2026-09-27: the second row was "Anthropic's Status" (the retired
            Claude-API brain's Statuspage); Aether now probes the internet and
            the local Ollama instead — see tools/aether.py.

    V0.02:  Audited 2026-07-24 — first tests (tests/test_aether_widget_smoke.py);
            the "own panel" / "every hour" claims verified against felhaven.py:156
            (its own "aether" registration) and kairos.py:31 (3600 s). Tag scoped
            to this class, not the module docstring — BarMeter/VitalsPanel are
            session 22.
    """

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        # Divider
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", pady=(8, 6))

        # Header
        tk.Label(self, text="AETHER — CONNECTIVITY", font=FONTS["card_header"],
                 fg=C["text3"], bg=C["card"], anchor="w").pack(fill="x")

        # The whole row of each, by readout, for VitalsPanel's tooltips: the
        # label, the dot and the value all count as hovering it.
        self.rows: dict[str, tk.Frame] = {}
        wifi,     self._wifi_dot,     self._wifi_lbl     = self._build_row("WiFi")
        internet, self._internet_dot, self._internet_lbl = self._build_row("Internet")
        ollama,   self._ollama_dot,   self._ollama_lbl   = self._build_row("Ollama")
        self.rows.update(wifi=wifi, internet=internet, ollama=ollama)

        for dot in (self._wifi_dot, self._internet_dot, self._ollama_dot):
            self._draw_dot(dot, C["text3"])

    def _build_row(self, label_text: str):
        row = tk.Frame(self, bg=C["card"])
        row.pack(fill="x", pady=1)
        dot = tk.Canvas(row, width=8, height=8, bg=C["card"], highlightthickness=0)
        dot.pack(side="left", padx=(0, 6))
        tk.Label(row, text=label_text, font=FONTS["small"],
                 fg=C["text2"], bg=C["card"], anchor="w").pack(side="left")
        val = tk.Label(row, text="—", font=FONTS["small"],
                       fg=C["text1"], bg=C["card"], anchor="e")
        val.pack(side="right")
        return row, dot, val

    def _draw_dot(self, canvas: tk.Canvas, color: str):
        canvas.delete("all")
        canvas.create_oval(1, 1, 7, 7, fill=color, outline="")

    def _show(self, dot: tk.Canvas, lbl: tk.Label, state: str,
              dots: dict) -> None:
        """One row: the dot from its map (dim when unlisted), the state as text,
        dimmed too when the probe could not decide."""
        self._draw_dot(dot, C[dots.get(state, "text3")])
        lbl.config(text=state, fg=C["text3"] if state == "unknown" else C["text1"])

    def update(self, data) -> None:
        """Called by Kairos every 300 s on the main thread."""
        rows = ((self._wifi_dot, self._wifi_lbl),
                (self._internet_dot, self._internet_lbl),
                (self._ollama_dot, self._ollama_lbl))
        if data is None:
            for dot, lbl in rows:
                self._draw_dot(dot, C["text3"])
                lbl.config(text="—")
            return
        self._show(self._wifi_dot, self._wifi_lbl, data["wifi"], _WIFI_DOTS)
        self._show(self._internet_dot, self._internet_lbl, data["internet"],
                   _INTERNET_DOTS)
        self._show(self._ollama_dot, self._ollama_lbl, data["ollama"],
                   _OLLAMA_DOTS)


# ─────────────────────────────────────────────────────────────────────────────
#  VitalsPanel — CPU / RAM / DISK + node·OS footer
# ─────────────────────────────────────────────────────────────────────────────

class VitalsPanel(tk.Frame):
    """Displays real system vitals, fetched by Kairos via hephaestus.fetch().

    A bare Frame tab body inside ModeratiPanel (the HEPHAESTUS tab); keeps its
    own update() and is registered with Kairos directly.

    V0.02:  Audited 2026-07-25 — first Tk smoke coverage
            (tests/test_hephaestus_panel_smoke.py): constructs (incl. the embedded
            AetherWidget), update(None) shows "unavailable", update(data) drives
            the CPU/RAM/DISK bars. Class-scoped tag — this file is shared with
            AetherWidget (session 5).
    """

    # Each readout's hover tooltip (Virgil): a readout says what the number
    # means, so each is a glossary entry. Keys name the widgets in
    # self._tip_targets; test_virgil_surfaces holds the two in lockstep.
    _TIPS = {
        "cpu":      virgil.term("cpu"),
        "ram":      virgil.term("ram"),
        "disk":     virgil.term("disk"),
        "wifi":     virgil.term("wifi"),
        "internet": virgil.term("internet"),
        "ollama":   virgil.term("ollama"),
    }

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])
        self.cpu = BarMeter(self, "CPU", C["amber"])
        self.cpu.pack(fill="x")
        self.ram = BarMeter(self, "RAM", C["teal"])
        self.ram.pack(fill="x")
        self.disk = BarMeter(self, "DISK", C["blue"])
        self.disk.pack(fill="x")
        self.node_lbl = tk.Label(self, text="", font=FONTS["tiny"],
                                 fg=C["text3"], bg=C["card"], anchor="w")
        self.node_lbl.pack(fill="x", pady=(4, 0))

        self.aether = AetherWidget(self)
        self.aether.pack(fill="x")

        self._tip_targets: dict[str, tk.Misc] = {
            "cpu": self.cpu, "ram": self.ram, "disk": self.disk,
            **self.aether.rows,
        }
        for key, tip in self._TIPS.items():
            if key in self._tip_targets:
                virgil_tooltip.attach(self._tip_targets[key], tip)

    def update(self, data: dict) -> None:
        """Called by Kairos every 5 s on the main thread."""
        if data is None:
            self.node_lbl.config(text="unavailable")
            return
        self.cpu.set(data["cpu"]["usage_percent"])
        self.ram.set(data["memory"]["percent_used"])
        self.disk.set(data["storage"]["percent_used"])
        self.node_lbl.config(text=f"{data['node']} · {data['os']}")
