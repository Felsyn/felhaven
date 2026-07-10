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
"""

import tkinter as tk

from theme import C, FONTS, Card

from panels.scribe_panel   import ScribePanel
from panels.zeno_panel     import ZenoPanel
from panels.eudoxus_panel  import EudoxusPanel


class CogitatorPanel(Card):
    """Thin tab host. Tab bodies are children of self.body and ARE the tabs.

    A pure UI container: no update(), not registered with Kairos. Scribe needs
    the shared data dict + save callback; Zeno and Eudoxus are self-contained.
    """

    def __init__(self, parent, data: dict, on_save):
        super().__init__(parent, "Cogitator — tasks & tools", C["gray"])

        # ── Tab bodies (each IS its own tab — no wrapper frame) ──────────────
        self.scribe  = ScribePanel(self.body, data, on_save)
        self.zeno    = ZenoPanel(self.body)
        self.eudoxus = EudoxusPanel(self.body)

        # key -> (body widget, accent color for the active underline).
        # Each deity keeps the accent it had as a standalone row/tab.
        self._bodies = {
            "scribe":  (self.scribe,  C["purple"]),
            "zeno":    (self.zeno,    C["gray"]),
            "eudoxus": (self.eudoxus, C["blue"]),
        }

        # ── Tab bar (loop over (key, label) — add a fourth tab in one line) ──
        tab_row = tk.Frame(self.body, bg=C["card"])
        tab_row.pack(fill="x", pady=(6, 0))

        self._tabs:  dict[str, tk.Label] = {}
        self._lines: dict[str, tk.Frame] = {}
        for key, text in (("scribe", "SCRIBE"), ("zeno", "ZENO"),
                          ("eudoxus", "EUDOXUS")):
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

        # ── Activate the default tab (SCRIBE) ────────────────────────────────
        # Bodies are constructed but unpacked; _show_tab packs the active one
        # BELOW tab_row (tab_row was packed first), so the bar stays on top.
        self._active = None
        self._show_tab("scribe")

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
