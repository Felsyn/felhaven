#!/usr/bin/env python3
"""
FELHAVEN — Command Center
"Ex tenebris surgit lumen posteris"

A local-first dashboard built on the Metis toolbox modules: clock and
calendar, weather with warnings and space weather, sky and maps, system
vitals, markets, news, tasks, audio, and Pythia's chat over all of them.
This file is the shell: it builds the window, mounts the panels, and starts
Kairos's workers. The DAEDALUS tab maps every module and what it does.

Runs on Python 3.13 (64-bit) from a venv built from the hashed lock,
requirements.txt (SETUP §2); SETUP's opening section says what a clone
alone can and can't run.

Visual identity: theme.py (fonts, Card base widget), palette.py (colours)

Launched by Felhaven.bat, which starts the Sphynx gate (sphynx_panel.py) under
pythonw; Sphynx spawns this file once the gate opens. So Felhaven has no
console and no stderr (sys.stderr is None, checked by reproducing that chain
on 2026-11-21), and anything printed to stderr is lost. Hence:

Errors:      An exception nothing else caught is logged with its traceback:
             setup_logging() installs the thread and program hooks, and
             metis_logging.log_tk_errors(root) the Tk callback one (each
             distinct failure once a run; see metis_logging.py). Kairos guards
             each panel's update() itself (kairos.py). A failure while Felhaven
             is being built is logged at CRITICAL and shown in an error box,
             because no window would open: the box names only the error's kind,
             never its text, which can carry a path (§2). All three used to go
             nowhere.

Scribe:      Tasks and notes save through _schedule_save, 500 ms debounced, and
             _on_close flushes one still pending. _flush_save hands the SCRIBE
             tab save_data()'s answer, so a failed save says so on its count
             line (the owner's pick, 2026-11-21).

2026-07-25:  The first audit found it honest (banner module list accurate, no
             dead code). The root shell keeps its prose docstring by design: it
             is the entry point, not a tool, so it has no Job/Upstream/
             Downstream shape (§3 skips a rule that doesn't fit). felhaven's
             own root.after() calls (_schedule_save debounce, _on_resize) are
             the shell's timing, outside §6's panel rule.

V0.03: Walked through 2026-11-21 (in the repo's first commit, 2026-06-10;
       first audited 2026-07-25). Errors and Scribe added, both the owner's
       picks where a choice was his. "Build all eight views" was ten; the
       header clock's docstring said 12-hour only (it follows Settings);
       the prewarm comment's "~100s" cold load named one model, and loads
       ran 20 to 80 s by model and disk (2026-11-17). Later that day (S26):
       the hooks moved into metis_logging, so the Sphynx gate shares them.
"""

import logging
import tkinter as tk
from datetime import datetime
from tkinter import messagebox

import calliope
import cerberus
import harmonia
import pythia
import scribe
import themis
import voices
from kairos import Kairos
from metis_logging import log_tk_errors, log_uncaught, setup_logging
from tools import morpheus
from theme import C, FONTS, _init_fonts, _init_widget_defaults, rescale_fonts

from panels.home_panel       import HomePanel
from panels.horai_panel      import HoraiPanel
from panels.moderati_panel   import ModeratiPanel
from panels.aura_panel       import WeatherPanel
from panels.hypatia_panel    import HypatiaPanel
from panels.artemis_panel    import ArtemisPanel
from panels.midas_panel      import MidasPanel
from panels.pheme_panel      import PhemePanel
from panels.vox_array_panel  import VoxArrayPanel
from panels.cogitator_panel  import CogitatorPanel
from panels.sidebar          import Sidebar
from panels.iris_panel       import AlertWatch, SidebarAlert

log = logging.getLogger("METIS.felhaven")

# ── Baseline geometry for Felhaven window ────────────────────────────────────
BASE_W, BASE_H = 960, 620
# The smallest window every view still fits in. 760, not 700: Moderati's
# seventh tab (DAEDALUS) made its tab bar too wide for a 700 px window, and
# pack dropped the tab entirely. The sidebar is fixed-width, so every px here
# is content width. tests/test_moderati_panel_smoke.py holds the bar to it.
MIN_W, MIN_H = 760, 500
# The header's and the body's padding, and the rule between them: named
# because start_height counts them. Above the body's own height, the window
# is exactly these plus the header.
HEADER_PADY = (12, 8)
RULE_H = 2
BODY_PADY = (10, 12)
# Room kept for the title bar and the taskbar (about 31 + 48 px): a window
# grown past BASE_H stops this far short of the screen's height, and
# start_position puts that room at the bottom. Past it, the rail scrolls.
SCREEN_MARGIN = 80


def start_height(header_h: int, rail_h: int, screen_h: int) -> int:
    """The window's height on launch: BASE_H, or taller when the nav rail as
    it stands on launch would not fit under the header, so every row shows.
    Grown, it stops SCREEN_MARGIN short of the screen; it is never shrunk
    below BASE_H, so a screen shorter than BASE_H plus the margin still gets
    BASE_H, as it always did.

    Measured, not a second magic number: the rail's height depends on the
    fonts this machine actually has. Taller does not mean bigger type —
    rescale_fonts takes the smaller of the width and height ratios and the
    width stays BASE_W, so the scale stays 1.0 and the rail stays the height
    it was measured at."""
    need = header_h + sum(HEADER_PADY) + RULE_H + sum(BODY_PADY) + rail_h
    return max(BASE_H, min(need, screen_h - SCREEN_MARGIN))


def start_position(screen_w: int, screen_h: int, height: int) -> tuple[int, int]:
    """Where the window opens: centred across the screen, and centred down
    the part of it above SCREEN_MARGIN, so a window grown to the limit opens
    at the very top.

    Placed, not left to Windows: its default spot cascades down the screen,
    and SCREEN_MARGIN only holds if the window starts high enough. Left
    there, a tall window's bottom edge — Home's input row and status bar —
    could open behind the taskbar."""
    x = max(0, (screen_w - BASE_W) // 2)
    y = max(0, (screen_h - SCREEN_MARGIN - height) // 2)
    return x, y


def fit_window(window: "tk.Tk | tk.Toplevel", header: tk.Misc,
               sidebar: Sidebar) -> str:
    """Size and place the window so every nav row shows (start_height,
    start_position), and return the geometry it set.

    Runs before the first view is shown, whose set_active() is the first
    thing to let Tk idle — and idling is what maps the window. So the window
    is withdrawn while geometry runs and the rail is measured, then shown
    once, at the height it keeps, rather than drawn short and stretched a
    moment later. The header and the rail are read only after
    update_idletasks: before it, a frame's requested height is still 1."""
    window.withdraw()
    window.update_idletasks()
    screen_w, screen_h = window.winfo_screenwidth(), window.winfo_screenheight()
    height = start_height(header.winfo_reqheight(), sidebar.rail_height(), screen_h)
    x, y = start_position(screen_w, screen_h, height)
    geometry = f"{BASE_W}x{height}+{x}+{y}"
    window.geometry(geometry)
    window.deiconify()
    return geometry


def _show_start_failure(exc: BaseException) -> None:
    """Felhaven failed while it was being built: Sphynx has closed and no
    window opened, so without this box nothing would show it. The box names
    the error's kind only; its text can carry a path (§2)."""
    try:
        box_root = tk.Tk()
        box_root.withdraw()
        messagebox.showerror(
            "Felhaven",
            f"Felhaven couldn't start ({type(exc).__name__}).\n\n"
            "The details are in logs\\felhaven.log, beside felhaven.py.",
            parent=box_root)
        box_root.destroy()
    except Exception:
        log.warning("could not show the start-failure box", exc_info=True)


class _ClockCluster(tk.Frame):
    """Header clock: the time, 12- or 24-hour as Settings says, and in 12-hour
    mode a stacked AM/PM indicator. Registered with Kairos under 'horai'."""

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
        # Logging online before anything (especially Kairos) can emit. It also
        # sends uncaught thread and program exceptions to the log; Tk callbacks
        # join them once the root exists (see the module docstring's Errors).
        setup_logging("felhaven")

        self.root = tk.Tk()
        log_tk_errors(self.root)
        self.root.title("FELHAVEN — Command Center")
        self.root.configure(bg=C["bg"])
        self.root.geometry(f"{BASE_W}x{BASE_H}")
        self.root.minsize(MIN_W, MIN_H)

        _init_fonts(self.root)
        _init_widget_defaults(self.root)

        self.data = scribe.load_data()
        self._save_timer      = None
        self._resize_after_id = None

        # Header — brand left, clock center, motto right
        header = self._header = tk.Frame(self.root, bg=C["bg"])
        header.pack(fill="x", padx=16, pady=HEADER_PADY)
        tk.Label(header, text="FELHAVEN", font=FONTS["title"],
                 fg=C["text1"], bg=C["bg"]).pack(side="left")
        # The narration lamp lives in Hestia (Home view) now, not the header.
        tk.Label(header, text="Ex tenebris surgit lumen posteris",
                 font=FONTS["subtitle"], fg=C["text3"], bg=C["bg"]).pack(side="right")
        self.header_clock = _ClockCluster(header)
        self.header_clock.pack(expand=True)

        tk.Frame(self.root, bg=C["border"], height=RULE_H).pack(fill="x", padx=16)

        # Body — sidebar left, content area right
        body = tk.Frame(self.root, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=16, pady=BODY_PADY)

        self._sidebar = Sidebar(body, on_select=self._show_view)
        self._sidebar.pack(side="left", fill="y")

        # No padx: the sidebar's scroll lane (8 px, empty while the rail fits)
        # is the gap between the rail and the content, as padx=(8, 0) was.
        self._content_area = tk.Frame(body, bg=C["bg"])
        self._content_area.pack(side="left", fill="both", expand=True)

        # Build every view once; the sidebar's rows and groups name them
        # (test_sidebar_view_parity holds the two in step).
        self._views: dict[str, tk.Widget] = {
            "felhaven":   HomePanel(self._content_area),
            "horai":      HoraiPanel(self._content_area),
            "moderati":   ModeratiPanel(self._content_area),
            "aura":       WeatherPanel(self._content_area),
            "hypatia":    HypatiaPanel(self._content_area),
            "artemis":    ArtemisPanel(self._content_area),
            "midas":      MidasPanel(self._content_area),
            "pheme":      PhemePanel(self._content_area),
            "morpheus":   VoxArrayPanel(self._content_area),
            "cogitator":  CogitatorPanel(self._content_area, self.data, self._schedule_save),
        }

        # Kairos — owns all panel timing from here
        self.kairos = Kairos(self.root)
        self.kairos.register_panel("horai",      self._views["horai"])
        self.kairos.register_panel("horai",      self.header_clock)
        # HomePanel only needs this to refresh its Ammit status badge (its own
        # chat state stays event-driven, not Kairos-timed) — see home_panel.py.
        self.kairos.register_panel("horai",      self._views["felhaven"])
        # Auto-DJ's spoken line, on Awen's own tick. The RIDER is registered,
        # not the view: HomePanel.update() is already taken by the Ammit badge
        # above, and Kairos passes update() no worker name to tell them apart
        # (the moderati.hephaestus precedent, two lines down).
        self.kairos.register_panel("awen",       self._views["felhaven"].dj_rider)
        self.kairos.register_panel("hephaestus", self._views["moderati"].hephaestus)
        self.kairos.register_panel("aether",     self._views["moderati"].hephaestus.aether)
        self.kairos.register_panel("talos",      self._views["moderati"].hephaestus.ollama)
        self.kairos.register_panel("talos",      self._views["moderati"].machine_spirit)
        self.kairos.register_panel("emanon",     self._views["moderati"].emanon)
        self.kairos.register_panel("argus",      self._views["moderati"].argus)
        self.kairos.register_panel("aura",       self._views["aura"])
        self.kairos.register_panel("aura",       self._sidebar.row("aura"))
        self.kairos.register_panel("aura",       self._views["hypatia"].conditions)
        # Settings' copy of Atmospherics' PLACE dropdown shows a place picked
        # there (WeatherPanel refreshes its own bar in update()).
        self.kairos.register_panel("aura",       self._views["moderati"].themis.place_bar)
        # The sun and moon sub-widgets are registered themselves, not through
        # WeatherPanel: each is computed on its own worker, so a weather outage
        # can't blank them. Hypatia's moon row is a RIDER for the same reason
        # as dj_rider: its widget's update() is already taken by `aura`.
        self.kairos.register_panel("helios",     self._views["aura"].helios_widget)
        self.kairos.register_panel("selene",     self._views["aura"].selene_widget)
        self.kairos.register_panel("selene",     self._views["hypatia"].conditions.moon_row)
        self.kairos.register_panel("hypatia",    self._views["hypatia"])
        self.kairos.register_panel("artemis",    self._views["artemis"])
        self.kairos.register_panel("midas",      self._views["midas"])
        # Every face of Cerberus's one session follows its worker: the tab
        # reseals, the Dynastic Vault reseals (a rider: its update() is taken
        # by `midas`), and Home's lamp shows sealed / open / alarm.
        self.kairos.register_panel("cerberus",   self._views["moderati"].cerberus)
        self.kairos.register_panel("cerberus",   self._views["midas"].session_rider)
        self.kairos.register_panel("cerberus",   self._views["felhaven"].cerberus_lamp)
        self.kairos.register_panel("pheme",      self._views["pheme"])
        # Reach through the Vox Array host to the Morpheus/Orpheus tab bodies —
        # the Moderati precedent (register_panel("hephaestus", …moderati.hephaestus)).
        self.kairos.register_panel("morpheus",   self._views["morpheus"].morpheus)
        self.kairos.register_panel("orpheus",    self._views["morpheus"].orpheus)
        # Weather warnings: ONE controller holds what was seen and hands the
        # same view to every display, so acknowledging calms them together.
        self.alert_watch = AlertWatch(self.root, on_open=self._open_alerts)
        self.alert_watch.add_display(self._views["aura"].alert_banner)
        self.alert_watch.add_display(self._views["aura"].alerts_tab)
        self.alert_watch.add_display(self._views["felhaven"].alert_badge)
        self.alert_watch.add_display(SidebarAlert(self._sidebar.row("aura")))
        self.kairos.register_panel("iris",       self.alert_watch)
        # Space weather: three displays, nothing to acknowledge, so each takes
        # the worker's answer itself (Celestarium's row is a rider, as moon_row).
        self.kairos.register_panel("eos",        self._views["aura"].space_widget)
        self.kairos.register_panel("eos",        self._views["hypatia"].conditions.aurora_row)
        self.kairos.register_panel("eos",        self._views["felhaven"].aurora_notice)
        self.kairos.start()

        # Let the Settings tab nudge the location/time workers on Save so a
        # location/unit/clock change is reflected on the next tick rather than
        # after Aura's 30-minute interval (wired now that Kairos exists).
        self._views["moderati"].themis.set_refetch(self.kairos.refetch)
        # And Atmospherics' PLACE pick refetches "aura" (both bars).
        self._views["aura"].set_refetch(self.kairos.refetch)
        # Opening the Dynastic Vault's gate fetches prices now, not at the
        # next 60 s tick.
        self._views["midas"].set_refetch(self.kairos.refetch)

        # Warm both models on background threads at startup so the first
        # question/answer isn't stalled by a cold load: Calliope's kokoro-onnx
        # model (one-time ONNX load) and Pythia's model (an Ollama cold load ran
        # 20 to 80 s, by model and by disk). Both are best-effort and silent if
        # unavailable.
        # Point narration at the user's saved voice before prewarm, so the
        # very first thing spoken is already in the right voice. (This used to
        # be load-bearing — prewarm rendered a voice-keyed filler cache — but
        # the fillers are gone and set_voice() now just sets a global.)
        calliope.set_voice(voices.pythia_voice())
        calliope.prewarm()
        pythia.prewarm()

        # Tall enough for every nav row, and placed so the bottom clears the
        # taskbar. Before _show_view, which is what first lets Tk idle.
        fit_window(self.root, self._header, self._sidebar)
        self._show_view("felhaven")
        self.root.bind("<Configure>", self._on_resize)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _show_view(self, name: str) -> None:
        for view in self._views.values():
            view.pack_forget()
        self._views[name].pack(fill="both", expand=True)
        self._sidebar.set_active(name)

    def _open_alerts(self) -> None:
        """Atmospherics' ALERTS tab: the popup's OPEN ALERTS, the NOW banner's
        "details ›" and Home's badge all land here."""
        self._show_view("aura")
        self._views["aura"].show_tab("ALERTS")

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
        is an honest 'unsaved changes' signal rather than a stale after-id.
        The SCRIBE tab is told whether it worked (see the module docstring)."""
        self._save_timer = None
        saved = scribe.save_data(self.data)
        self._views["cogitator"].scribe.show_saved(saved)

    def _on_close(self):
        """Clean shutdown: flush any pending save so a fast close can't drop
        an edit, then stop Kairos's tick before the root is destroyed so a
        pending after-callback can't fire into a dead window. Cerberus locks
        first: process exit would drop the session anyway, but this way it's
        a Ledger row, and nothing later in the shutdown can hold it open."""
        cerberus.lock(cerberus.LOCK_EXIT)
        if self._save_timer:
            self.root.after_cancel(self._save_timer)
            self._flush_save()
        harmonia.shutdown()    # stop the play thread before Morpheus, its yield target
        morpheus.shutdown()    # best-effort: no orphan mpv.exe after close
        self.kairos.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main() -> None:
    """Build Felhaven and run it. A failure while it is built is logged and
    shown (see the module docstring's Errors), then raised again, so a run
    from a console still prints it and exits non-zero."""
    try:
        app = FelhavenApp()
    except Exception as exc:
        # Logged before the box, which waits for a click. The program hook
        # sees the same failure once it is raised again, and skips it.
        log_uncaught("Felhaven's start", type(exc), exc, exc.__traceback__,
                     logging.CRITICAL)
        _show_start_failure(exc)
        raise
    app.run()


if __name__ == "__main__":
    main()
