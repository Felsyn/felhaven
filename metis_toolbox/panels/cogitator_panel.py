"""
panels/cogitator_panel.py — Cogitator host (Scribe + Zeno + Eudoxus tabs)
=========================================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Host the request-driven utility tabs and switch between them. Build
             the tab bar; pack the active tab body; nothing else. No data, no
             update().

Tabs:        SCRIBE  — tasks + notes (persisted via the on_save callback)
             ZENO    — safe arithmetic evaluator
             EUDOXUS — unit converter

Why a host with no update(): unlike Moderati, NOTHING here is polled — neither
Scribe nor Zeno/Eudoxus is a Kairos worker, so no tab body is registered with
Kairos and this host never sees a tick. Do NOT register the host with Kairos —
Card (a tk.Frame) has a built-in update() that takes no data arg, so a Kairos
dispatch would TypeError.

V0.02: Audited 2026-07-26 — verified honest; no polled tab (nothing here is a
       Kairos worker). Scribe gets the shared data dict + save callback; Zeno
       and Eudoxus are self-contained. First smoke coverage.
"""

from theme import C, TabHost
from tools import virgil
from panels import virgil_tooltip

from panels.scribe_panel   import ScribePanel
from panels.zeno_panel     import ZenoPanel
from panels.eudoxus_panel  import EudoxusPanel


class CogitatorPanel(TabHost):
    """Thin tab host (theme.TabHost). Tab bodies are children of self.body and
    ARE the tabs.

    A pure UI container: no update(), not registered with Kairos. Scribe needs
    the shared data dict + save callback; Zeno and Eudoxus are self-contained.
    """

    # Each tab's hover tooltip (Virgil): that module's own gloss. One per tab;
    # test_virgil_surfaces holds it to the _build_tabs list.
    _TIPS = {
        "scribe":  virgil.module("scribe"),
        "zeno":    virgil.module("zeno"),
        "eudoxus": virgil.module("eudoxus"),
    }

    def __init__(self, parent, data: dict, on_save):
        super().__init__(parent, "Cogitator — tasks & tools", C["gray"])

        # ── Tab bodies (each IS its own tab — no wrapper frame) ──────────────
        self.scribe  = ScribePanel(self.body, data, on_save)
        self.zeno    = ZenoPanel(self.body)
        self.eudoxus = EudoxusPanel(self.body)

        # (key, label, body, accent) — each keeps the accent it had as a
        # standalone row/tab. TabHost builds the bar and activates the first.
        self._build_tabs([
            ("scribe",  "SCRIBE",  self.scribe,  C["purple"]),
            ("zeno",    "ZENO",    self.zeno,    C["gray"]),
            ("eudoxus", "EUDOXUS", self.eudoxus, C["blue"]),
        ], default="scribe")
        for key, tip in self._TIPS.items():
            if key in self._tabs:
                virgil_tooltip.attach(self._tabs[key], tip)
