#!/usr/bin/env python3
"""
FELHAVEN — Command Center
"Ex tenebris surgit lumen posteris"

A portable local-first dashboard built on the Metis toolbox modules:
    horai.py      — temporal context (clock, season, cycle)
    ammit.py      — countdown timer (single slot, max 99hrs)
    hephaestus.py — system vitals (CPU, RAM, disk)
    aura.py       — weather (wttr.in)
    midas.py      — market prices (Finnhub /quote) + holdings ledger (Plutus)
    scribe.py     — tasks and notes (consolidated in ScribePanel)
    pheme.py      — RSS aggregator (news feeds)

Drop this folder on a flash drive and run on any PC with Python 3.10+.

Dependencies: psutil, requests (pip install psutil requests)
Visual identity: theme.py (colors, fonts, Card base widget)
"""

import tkinter as tk
from datetime import datetime

import calliope
import pythia
import scribe
import themis
from kairos import Kairos
from metis_logging import setup_logging
from tools import morpheus
from theme import C, FONTS, _init_fonts, rescale_fonts

from panels.home_panel       import HomePanel
from panels.horai_panel      import HoraiPanel
from panels.moderati_panel   import ModeratiPanel
from panels.aura_panel       import WeatherPanel
from panels.hypatia_panel    import HypatiaPanel
from panels.midas_panel      import MidasPanel
from panels.pheme_panel      import PhemePanel
from panels.vox_array_panel  import VoxArrayPanel
from panels.cogitator_panel  import CogitatorPanel
from panels.sidebar          import Sidebar

# ── Baseline geometry for Felhaven window ────────────────────────────────────
BASE_W, BASE_H = 960, 620


class _ClockCluster(tk.Frame):
    """Header clock: 12-hour time + stacked AM/PM indicator.
    Registered with Kairos under 'horai'."""

    def __init__(self, parent):
        super().__init__(parent, bg=C["bg"])
        self._time_lbl = tk.Label(self, font=FONTS["xlarge_bold"],
                                   fg=C["text1"], bg=C["bg"])
        self._time_lbl.pack(side="left")

        stack = tk.Frame(self, bg=C["bg"])
        # anchor="n": flush the marks to the TOP of the tall clock, not its
        # vertical middle — reads like a clock radio's AM/PM segment.
        stack.pack(side="left", anchor="n", padx=(6, 0))
        # Consolas (monospace) marks: keeps the bracket/space width trick in
        # _mark honest, and matches the monospace clock beside it.
        self._am = tk.Label(stack, font=FONTS["small_bold"], bg=C["bg"])
        self._am.pack()
        self._pm = tk.Label(stack, font=FONTS["small_bold"], bg=C["bg"])
        self._pm.pack()

    def update(self, data) -> None:
        now = datetime.now()
        # Settings 24-hour mode: show "%H:%M" and blank the AM/PM stack, which
        # has no meaning on a 24-hour clock.
        if themis.clock_24h():
            self._time_lbl.config(text=now.strftime("%H:%M"))
            self._am.config(text="   ", fg=C["text3"])
            self._pm.config(text="   ", fg=C["text3"])
            return
        self._time_lbl.config(text=now.strftime("%I:%M").lstrip("0"))
        is_pm = now.hour >= 12
        self._mark(self._am, "AM", not is_pm)
        self._mark(self._pm, "PM", is_pm)

    @staticmethod
    def _mark(lbl, text, active) -> None:
        # Inactive line keeps padding spaces so the stack doesn't shift width
        # when the brackets come and go at noon / midnight.
        if active:
            lbl.config(text=f"[{text}]", fg=C["amber"])
        else:
            lbl.config(text=f" {text} ", fg=C["text3"])


# ─────────────────────────────────────────────────────────────────────────────
#  Main app
# ─────────────────────────────────────────────────────────────────────────────

class FelhavenApp:
    def __init__(self):
        # Logging online before anything (especially Kairos) can emit.
        setup_logging("felhaven")

        self.root = tk.Tk()
        self.root.title("FELHAVEN — Command Center")
        self.root.configure(bg=C["bg"])
        self.root.geometry(f"{BASE_W}x{BASE_H}")
        self.root.minsize(700, 500)

        _init_fonts(self.root)

        self.data = scribe.load_data()
        self._save_timer      = None
        self._resize_after_id = None

        # Header — brand left, clock center, motto right
        header = tk.Frame(self.root, bg=C["bg"])
        header.pack(fill="x", padx=16, pady=(12, 8))
        tk.Label(header, text="FELHAVEN", font=FONTS["title"],
                 fg=C["text1"], bg=C["bg"]).pack(side="left")
        # The narration lamp lives in Hestia (Home view) now, not the header.
        tk.Label(header, text="Ex tenebris surgit lumen posteris",
                 font=FONTS["subtitle"], fg=C["text3"], bg=C["bg"]).pack(side="right")
        self.header_clock = _ClockCluster(header)
        self.header_clock.pack(expand=True)

        tk.Frame(self.root, bg=C["border"], height=2).pack(fill="x", padx=16)

        # Body — sidebar left, content area right
        body = tk.Frame(self.root, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=16, pady=(10, 12))

        self._sidebar = Sidebar(body, on_select=self._show_view)
        self._sidebar.pack(side="left", fill="y")

        self._content_area = tk.Frame(body, bg=C["bg"])
        self._content_area.pack(side="left", fill="both", expand=True, padx=(8, 0))

        # Build all eight views once
        self._views: dict[str, tk.Widget] = {
            "felhaven":   HomePanel(self._content_area),
            "horai":      HoraiPanel(self._content_area),
            "moderati":   ModeratiPanel(self._content_area),
            "aura":       WeatherPanel(self._content_area),
            "hypatia":    HypatiaPanel(self._content_area),
            "midas":      MidasPanel(self._content_area),
            "pheme":      PhemePanel(self._content_area),
            "morpheus":   VoxArrayPanel(self._content_area),
            "cogitator":  CogitatorPanel(self._content_area, self.data, self._schedule_save),
        }

        # Kairos — owns all panel timing from here
        self.kairos = Kairos(self.root)
        self.kairos.register_panel("horai",      self._views["horai"])
        self.kairos.register_panel("horai",      self.header_clock)
        self.kairos.register_panel("hephaestus", self._views["moderati"].hephaestus)
        self.kairos.register_panel("aether",     self._views["moderati"].hephaestus.aether)
        self.kairos.register_panel("emanon",     self._views["moderati"].emanon)
        self.kairos.register_panel("argus",      self._views["moderati"].argus)
        self.kairos.register_panel("aura",       self._views["aura"])
        self.kairos.register_panel("aura",       self._sidebar.row("aura"))
        self.kairos.register_panel("aura",       self._views["hypatia"].conditions)
        self.kairos.register_panel("hypatia",    self._views["hypatia"])
        self.kairos.register_panel("midas",      self._views["midas"])
        self.kairos.register_panel("pheme",      self._views["pheme"])
        # Reach through the Vox Array host to the Morpheus tab body — the
        # Moderati precedent (register_panel("hephaestus", …moderati.hephaestus)).
        self.kairos.register_panel("morpheus",   self._views["morpheus"].morpheus)
        self.kairos.start()

        # Let the Settings tab nudge the location/time workers on Save so a
        # location/unit/clock change is reflected on the next tick rather than
        # after Aura's 30-minute interval (wired now that Kairos exists).
        self._views["moderati"].themis.set_refetch(self.kairos.refetch)

        # Warm both models on background threads at startup so the first
        # question/answer isn't stalled by a cold load: Calliope's kokoro-onnx
        # model (one-time ONNX load) and Pythia's gemma4:e2b (~100s Ollama cold
        # load). Both are best-effort and silent if unavailable.
        calliope.prewarm()
        pythia.prewarm()

        self._show_view("felhaven")
        self.root.bind("<Configure>", self._on_resize)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _show_view(self, name: str) -> None:
        for view in self._views.values():
            view.pack_forget()
        self._views[name].pack(fill="both", expand=True)
        self._sidebar.set_active(name)

    def _on_resize(self, event):
        if event.widget is not self.root:
            return
        if self._resize_after_id:
            self.root.after_cancel(self._resize_after_id)
        self._resize_after_id = self.root.after(80, self._apply_scale)

    def _apply_scale(self):
        """Resize fonts to the window, then grow the fixed-width sidebar by the
        same factor so the nav labels don't clip when maximized."""
        scale = rescale_fonts(
            self.root.winfo_width(), self.root.winfo_height(), BASE_W, BASE_H
        )
        self._sidebar.rescale(scale)
        # Celestarium's right rail is fixed-width like the sidebar — grow it too
        # so constellation names and the toggle don't clip at large window sizes.
        self._views["hypatia"].rescale(scale)

    def _schedule_save(self, data):
        self.data = data
        if self._save_timer:
            self.root.after_cancel(self._save_timer)
        self._save_timer = self.root.after(500, self._flush_save)

    def _flush_save(self):
        """Write pending data now and clear the debounce timer, so _save_timer
        is an honest 'unsaved changes' signal rather than a stale after-id."""
        self._save_timer = None
        scribe.save_data(self.data)

    def _on_close(self):
        """Clean shutdown: flush any pending save so a fast close can't drop
        an edit, then stop Kairos's tick before the root is destroyed so a
        pending after-callback can't fire into a dead window."""
        if self._save_timer:
            self.root.after_cancel(self._save_timer)
            self._flush_save()
        morpheus.shutdown()   # best-effort: no orphan mpv.exe after close
        self.kairos.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    FelhavenApp().run()
