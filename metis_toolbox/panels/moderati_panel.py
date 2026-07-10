"""
panels/moderati_panel.py — Moderati host (Hephaestus + Emanon tabs)
==================================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Host the system-monitoring tabs and switch between them. Build the
             tab bar; pack the active tab body; nothing else. No data, no
             update() — each tab body keeps its own update() and is registered
             with Kairos individually (see felhaven.py).

Tabs:        HEPHAESTUS — system vitals (CPU/RAM/DISK + Aether connectivity)
             EMANON     — rolling log watch
             ARGUS      — network awareness (connections, traffic, firewall, DNS)
             CERBERUS   — secrets guardian (vault, config custody, access log)
             SETTINGS   — location / unit / clock (Themis)

Why a host with no update(): Kairos maps worker-name -> [subscribers]; the tab
bodies register under their existing worker names ("hephaestus", "aether",
"emanon"), so Kairos drives them directly and this host never sees a tick. Do
NOT register the host with Kairos — Card (a tk.Frame) has a built-in update()
that takes no data arg, so a Kairos dispatch would TypeError.
"""

import tkinter as tk

from theme import C, FONTS, Card

from panels.hephaestus_panel import VitalsPanel
from panels.emanon_panel     import EmanonPanel
from panels.argus_panel      import ArgusPanel
from panels.cerberus_panel   import CerberusPanel
from panels.themis_panel     import ThemisPanel


class ModeratiPanel(Card):
    """Thin tab host. Tab bodies are children of self.body and ARE the tabs."""

    def __init__(self, parent):
        super().__init__(parent, "Moderati — vitals, logs, network & settings", C["teal"])

        # ── Tab bodies (each IS its own tab — no wrapper frame) ──────────────
        # Attribute name = deity; felhaven.py registers these under their
        # existing Kairos worker names. self.hephaestus carries .aether inside.
        self.hephaestus = VitalsPanel(self.body)
        self.emanon     = EmanonPanel(self.body)
        self.argus      = ArgusPanel(self.body)
        self.cerberus   = CerberusPanel(self.body)
        self.themis     = ThemisPanel(self.body)

        # key -> (body widget, accent color for the active underline).
        # Each deity keeps the card color it had as a standalone panel.
        self._bodies = {
            "hephaestus": (self.hephaestus, C["teal"]),
            "emanon":     (self.emanon,     C["red"]),
            "argus":      (self.argus,      C["blue"]),
            "cerberus":   (self.cerberus,   C["purple"]),
            "themis":     (self.themis,     C["amber"]),
        }

        # ── Tab bar (loop over (key, label) — add a tab in one line) ─────────
        tab_row = tk.Frame(self.body, bg=C["card"])
        tab_row.pack(fill="x", pady=(6, 0))

        self._tabs:  dict[str, tk.Label] = {}
        self._lines: dict[str, tk.Frame] = {}
        for key, text in (("hephaestus", "HEPHAESTUS"), ("emanon", "EMANON"),
                          ("argus", "ARGUS"), ("cerberus", "CERBERUS"),
                          ("themis", "SETTINGS")):
            wrap = tk.Frame(tab_row, bg=C["card"])
            wrap.pack(side="left", padx=(0, 14))
            lbl = tk.Label(wrap, text=text, font=FONTS["card_header"],
                           fg=C["text3"], bg=C["card"], cursor="hand2")
            lbl.pack()
            line = tk.Frame(wrap, height=1, bg=C["border"])
            line.pack(fill="x")
            lbl.bind("<Button-1>", lambda e, k=key: self._show_tab(k))
            self._tabs[key]  = lbl
            self._lines[key] = line

        # ── Activate the default tab (HEPHAESTUS) ────────────────────────────
        # Bodies are constructed but unpacked; _show_tab packs the active one
        # BELOW tab_row (tab_row was packed first), so the bar stays on top.
        self._active = None
        self._show_tab("hephaestus")

    def _show_tab(self, key: str) -> None:
        if key == self._active:
            return
        # Hide the current body and dim its tab (skipped on the first call).
        if self._active is not None:
            self._bodies[self._active][0].pack_forget()
            self._tabs[self._active].config(fg=C["text3"])
            self._lines[self._active].config(bg=C["border"])

        # Show the chosen body and light its tab.
        body, accent = self._bodies[key]
        body.pack(fill="both", expand=True, pady=(6, 0))
        self._tabs[key].config(fg=C["text1"])
        self._lines[key].config(bg=accent)
        self._active = key
