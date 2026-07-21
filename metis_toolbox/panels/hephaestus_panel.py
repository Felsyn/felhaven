"""
Hephaestus panel — system vitals display for the Felhaven dashboard.

BarMeter      — small labelled percent bar (reusable by any panel that wants one).
VitalsPanel   — CPU / RAM / DISK bars + node/OS footer, fed hephaestus.fetch().
"""

import tkinter as tk

from theme import C, FONTS


# ─────────────────────────────────────────────────────────────────────────────
#  BarMeter — labelled horizontal percent bar
# ─────────────────────────────────────────────────────────────────────────────

class BarMeter(tk.Frame):
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
#  AetherWidget — WiFi + Anthropic's status, embedded in VitalsPanel
# ─────────────────────────────────────────────────────────────────────────────

class AetherWidget(tk.Frame):
    """
    Connectivity status embedded inside VitalsPanel.
    Two rows: WiFi state and Anthropic's status, each with a colored dot.
    Receives data from Kairos via update() — registered as its own panel.
    """

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        # Divider
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", pady=(8, 6))

        # Header
        tk.Label(self, text="AETHER — CONNECTIVITY", font=FONTS["card_header"],
                 fg=C["text3"], bg=C["card"], anchor="w").pack(fill="x")

        self._wifi_dot, self._wifi_lbl = self._build_row("WiFi")
        self._api_dot,  self._api_lbl  = self._build_row("Anthropic's Status")

        self._draw_dot(self._wifi_dot, C["text3"])
        self._draw_dot(self._api_dot,  C["text3"])

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
        return dot, val

    def _draw_dot(self, canvas: tk.Canvas, color: str):
        canvas.delete("all")
        canvas.create_oval(1, 1, 7, 7, fill=color, outline="")

    def update(self, data) -> None:
        """Called by Kairos every hour on the main thread."""
        if data is None:
            self._draw_dot(self._wifi_dot, C["text3"])
            self._draw_dot(self._api_dot,  C["text3"])
            self._wifi_lbl.config(text="—")
            self._api_lbl.config(text="—")
            return

        # Tri-state: red is reserved for a KNOWN-bad link; "unknown" (no adapter,
        # netsh probe failed) stays dim so a wired machine doesn't read as an alarm.
        wifi = data["wifi"]
        wifi_dot = {
            "connected":    C["green"],
            "disconnected": C["red"],
        }.get(wifi, C["text3"])
        self._draw_dot(self._wifi_dot, wifi_dot)
        self._wifi_lbl.config(
            text=wifi, fg=C["text3"] if wifi == "unknown" else C["text1"])

        api_status = data["api_status"]
        color_map = {
            "operational": C["green"],
            "degraded":    C["amber"],
            "down":        C["red"],
            "unknown":     C["text3"],
        }
        self._draw_dot(self._api_dot, color_map.get(api_status, C["text3"]))
        self._api_lbl.config(text=api_status)


# ─────────────────────────────────────────────────────────────────────────────
#  VitalsPanel — CPU / RAM / DISK + node·OS footer
# ─────────────────────────────────────────────────────────────────────────────

class VitalsPanel(tk.Frame):
    """Displays real system vitals, fetched by Kairos via hephaestus.fetch().

    A bare Frame tab body inside ModeratiPanel (the HEPHAESTUS tab); keeps its
    own update() and is registered with Kairos directly.
    """

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

    def update(self, data: dict) -> None:
        """Called by Kairos every 5 s on the main thread."""
        if data is None:
            self.node_lbl.config(text="unavailable")
            return
        self.cpu.set(data["cpu"]["usage_percent"])
        self.ram.set(data["memory"]["percent_used"])
        self.disk.set(data["storage"]["percent_used"])
        self.node_lbl.config(text=f"{data['node']} · {data['os']}")
