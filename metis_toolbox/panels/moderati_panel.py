"""
panels/moderati_panel.py — Moderati host (vitals, logs, network, secrets, settings, spirit)
===========================================================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Host the Moderati tabs and switch between them. Build the tab bar;
             pack the active tab body; nothing else. No data, no update() — the
             polled tab bodies (Hephaestus/Aether, Emanon, Argus) keep their own
             update() and register with Kairos individually; the request-driven
             ones (Cerberus, Settings, Machine Spirit) don't (see felhaven.py).

Tabs:        HEPHAESTUS     — system vitals (CPU/RAM/DISK + Aether connectivity)
             EMANON         — rolling log watch
             ARGUS          — network awareness (connections, traffic, firewall, DNS)
             CERBERUS       — secrets guardian (vault, config custody, access log)
             SETTINGS       — location / unit / clock (Themis)
             MACHINE SPIRIT — Pythia's editable system prompt

Why a host with no update(): Kairos maps worker-name -> [subscribers]; the polled
tab bodies register under their existing worker names ("hephaestus", "aether",
"emanon", "argus"), so Kairos drives them directly and this host never sees a
tick. Do NOT register the host with Kairos — Card (a tk.Frame) has a built-in
update() that takes no data arg, so a Kairos dispatch would TypeError.

V0.02: Audited 2026-07-26 — verified honest against felhaven.py wiring; freshened
       the stale 2-of-6-tabs header, split the Job into polled vs request-driven
       tabs, and added "argus" to the polled-worker list. First smoke coverage.
"""

from theme import C, TabHost

from panels.hephaestus_panel import VitalsPanel
from panels.emanon_panel     import EmanonPanel
from panels.argus_panel      import ArgusPanel
from panels.cerberus_panel   import CerberusPanel
from panels.themis_panel     import ThemisPanel
from panels.machine_spirit_panel import MachineSpiritPanel


class ModeratiPanel(TabHost):
    """Thin tab host (theme.TabHost). Tab bodies are children of self.body and
    ARE the tabs."""

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
        self.machine_spirit = MachineSpiritPanel(self.body)

        # (key, label, body, accent) — each deity keeps the card color it had
        # as a standalone panel. TabHost builds the bar and activates the first.
        self._build_tabs([
            ("hephaestus",     "HEPHAESTUS",     self.hephaestus,     C["teal"]),
            ("emanon",         "EMANON",         self.emanon,         C["red"]),
            ("argus",          "ARGUS",          self.argus,          C["blue"]),
            ("cerberus",       "CERBERUS",       self.cerberus,       C["purple"]),
            ("themis",         "SETTINGS",       self.themis,         C["amber"]),
            ("machine_spirit", "MACHINE SPIRIT", self.machine_spirit, C["green"]),
        ], default="hephaestus")
