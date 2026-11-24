"""
Hephaestus panel — system vitals display for the Felhaven dashboard.

Job:         Show system vitals: CPU, RAM, drives, GPU, the loaded model and connectivity.

BarMeter      — small labelled percent bar (reusable by any panel that wants one).
AetherWidget  — WiFi / internet / Ollama reachability, fed aether.fetch().
OllamaWidget  — the model Ollama has loaded and its GPU share, fed talos.fetch().
VitalsPanel   — one scrolling column: SYSTEM (CPU, RAM, the boot drive,
                Felhaven's drive, uptime) and GPU, fed hephaestus.fetch(), then
                the OllamaWidget and the AetherWidget.

Upstream:    panels/moderati_panel.py (the HEPHAESTUS tab); felhaven.py
             registers three Kairos panels here: "hephaestus" (VitalsPanel),
             "aether" (AetherWidget) and "talos" (OllamaWidget).
Downstream:  theme.py (colours, fonts, PhosphorScroll), tools/virgil.py and
             panels/virgil_tooltip.py (the tooltips). It calls no tool itself:
             Kairos hands each widget its tool's fetch().

2026-07-24:  walkthrough audit (AetherWidget, session 5) — first tests
             (tests/test_aether_widget_smoke.py); its own "aether" Kairos
             registration and its poll interval checked against felhaven.py
             and kairos.py.

2026-07-25:  walkthrough audit (BarMeter and VitalsPanel, session 22) — first
             Tk smoke coverage (tests/test_hephaestus_panel_smoke.py): it
             builds with the AetherWidget inside, update(None) and update(data)
             drive the bars, and BarMeter.set() clamps to 0–100 and shows "<1%"
             for a tiny nonzero share.

V0.03:       Walked through 2026-11-17 (the V0.03 walkthrough, CONVENTIONS
             §3). Its three class-level V0.02 tags fold into this one marker:
             Daedalus and the version tests read only the module docstring,
             and test_tool_output_contract now holds every file to that. With
             no model loaded, the Ollama line no longer promises "about a
             minute" (gemma4:e2b loads in about 20 s, e4b in 80; the owner's
             pick). Upstream and Downstream added.
"""

import tkinter as tk

from panels import virgil_tooltip
from theme import C, FONTS, PhosphorScroll
from tools import virgil


# ─────────────────────────────────────────────────────────────────────────────
#  BarMeter — labelled horizontal percent bar
# ─────────────────────────────────────────────────────────────────────────────

class BarMeter(tk.Frame):
    """Small labelled percent bar, reusable by any panel that wants one."""

    def __init__(self, parent, label: str, color: str = C["amber"]):
        super().__init__(parent, bg=C["card"])
        row = tk.Frame(self, bg=C["card"])
        row.pack(fill="x")
        self._label = tk.Label(row, text=label, font=FONTS["small"],
                               fg=C["text2"], bg=C["card"], anchor="w")
        self._label.pack(side="left")
        self._val = tk.Label(row, text="—", font=FONTS["small"],
                             fg=C["text1"], bg=C["card"], anchor="e")
        self._val.pack(side="right")
        bar_bg = tk.Frame(self, bg=C["bar_bg"], height=3)
        bar_bg.pack(fill="x", pady=(2, 4))
        bar_bg.pack_propagate(False)
        self._bar = tk.Frame(bar_bg, bg=color, height=3)
        self._bar.place(relx=0, rely=0, relheight=1, relwidth=0)

    def set(self, pct: float, text: "str | None" = None):
        """Fill to `pct`. The value reads as the percent unless `text` says
        something better (GPU memory reads "1.7 / 6.0 GB")."""
        pct = max(0.0, min(100.0, pct))
        if text is None:
            text = "<1%" if 0 < pct < 1 else f"{round(pct)}%"
        self._val.config(text=text)
        self._bar.place_configure(relwidth=pct / 100)

    def set_label(self, label: str) -> None:
        self._label.config(text=label)


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
    """

    # The header names its module, so it shows that module's gloss (Virgil,
    # CONVENTIONS §7). The three rows carry glossary entries, from
    # VitalsPanel._TIPS.
    _HEADER_TIP = virgil.module("aether")

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        # Divider
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", pady=(8, 6))

        # Header
        self.header = tk.Label(self, text="AETHER — CONNECTIVITY",
                               font=FONTS["card_header"], fg=C["text3"],
                               bg=C["card"], anchor="w")
        self.header.pack(fill="x")
        virgil_tooltip.attach(self.header, self._HEADER_TIP)

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
#  OllamaWidget — the model Ollama has loaded, fed by Talos
# ─────────────────────────────────────────────────────────────────────────────

def _section(parent: tk.Widget, title: str, divider: bool = True) -> tk.Frame:
    """A section head in the house recipe (AetherWidget's): a 1 px divider and
    a card_header label in text3. Returns the head row, so a section can put a
    value on its right (the GPU's temperature)."""
    if divider:
        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", pady=(8, 6))
    head = tk.Frame(parent, bg=C["card"])
    head.pack(fill="x")
    head.title = tk.Label(head, text=title, font=FONTS["card_header"],
                          fg=C["text3"], bg=C["card"], anchor="w")
    head.title.pack(side="left")
    return head


class OllamaWidget(tk.Frame):
    """OLLAMA — LOADED MODEL: which model Ollama holds, how much of it fits in
    GPU memory, and whether it stays loaded. Registered with Kairos under
    "talos" (tools/talos.py), so it follows a model switch within 5 s.

    The dot follows the GPU share: green when the whole model is on the GPU,
    amber when part spilled to the CPU (answers slow down), dim when nothing is
    loaded or Ollama can't be reached. Red stays reserved for a known-bad link
    (AetherWidget's rule), and an unloaded model isn't one."""

    def __init__(self, parent: tk.Widget):
        super().__init__(parent, bg=C["card"])
        _section(self, "OLLAMA — LOADED MODEL")
        self.row = tk.Frame(self, bg=C["card"])
        self.row.pack(fill="x", pady=1)
        self._dot = tk.Canvas(self.row, width=8, height=8, bg=C["card"],
                              highlightthickness=0)
        self._dot.pack(side="left", padx=(0, 6))
        self._name = tk.Label(self.row, text="—", font=FONTS["small"],
                              fg=C["text1"], bg=C["card"], anchor="w")
        self._name.pack(side="left")
        self._share = tk.Label(self.row, text="—", font=FONTS["small"],
                               fg=C["text1"], bg=C["card"], anchor="e")
        self._share.pack(side="right")
        self._detail = tk.Label(self, text="", font=FONTS["tiny"], fg=C["text3"],
                                bg=C["card"], anchor="w", justify="left",
                                wraplength=380)
        self._detail.pack(fill="x")
        self._show("text3", "—", "—", "text3", "")

    def _show(self, dot: str, name: str, share: str, share_colour: str,
              detail: str) -> None:
        self._dot.delete("all")
        self._dot.create_oval(1, 1, 7, 7, fill=C[dot], outline="")
        self._name.config(text=name)
        self._share.config(text=share, fg=C[share_colour])
        self._detail.config(text=detail)

    def update(self, data) -> None:
        """Called by Kairos every 5 s on the main thread; None = unreachable."""
        if data is None:
            self._show("text3", "Ollama not reachable", "—", "text3",
                       "the model line comes back when Ollama answers")
            return
        loaded = data.get("loaded") or []
        if not loaded:
            self._show("text3", "no model loaded", "—", "text3",
                       "the next question loads one first")
            return
        model = loaded[0]
        pct = int(model.get("gpu_pct") or 0)
        kept = "kept loaded" if model.get("kept_loaded") else "unloads when idle"
        context = model.get("context")
        context_text = f"context {context:,}" if isinstance(context, int) else ""
        more = f" · {len(loaded) - 1} more loaded" if len(loaded) > 1 else ""
        if pct >= 100:
            # No GB figure: Ollama's size_vram leaves out most of what gemma4
            # holds in GPU memory (e2b reports 0.32 GB, NVML measures 3.08 GB;
            # e4b reports 0.34 GB of 4.52 GB, 2026-11-10). The GPU memory bar
            # above is the true total; the share here is still right.
            detail = " · ".join(t for t in (context_text, kept) if t)
            self._show("green", model["name"], "100% on GPU", "text1", detail + more)
        elif pct > 0:
            detail = " · ".join(t for t in (
                "part of it spilled to the CPU, so answers are slower",
                context_text) if t)
            self._show("amber", model["name"], f"{pct}% on GPU", "amber_bright",
                       detail + more)
        else:
            self._show("amber", model["name"], "on the CPU", "amber_bright",
                       "none of it fits on the GPU, so answers are slow" + more)


# ─────────────────────────────────────────────────────────────────────────────
#  VitalsPanel — one column: SYSTEM, GPU, OLLAMA, AETHER
# ─────────────────────────────────────────────────────────────────────────────

def _uptime(seconds: int) -> str:
    """3 d 4 h / 4 h 12 min / 12 min."""
    minutes = max(0, int(seconds)) // 60
    days, rem = divmod(minutes, 24 * 60)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days} d {hours} h"
    if hours:
        return f"{hours} h {mins} min"
    return f"{mins} min"


def _drive(root: str) -> str:
    """"D:\\" -> "D:"; "/" stays "/"."""
    return root.rstrip("\\/") or root


def _gpu_name(name: str) -> str:
    """"NVIDIA GeForce GTX 1660" -> "GTX 1660": the brand is the section."""
    for brand in ("NVIDIA ", "GeForce "):
        name = name.replace(brand, "")
    return name.strip()


_GPU_TROUBLE = {
    "no_nvidia_gpu": "No NVIDIA GPU found on this machine.",
    "nvml_failed": "The GPU couldn't be read just now (NVML).",
}


class VitalsPanel(tk.Frame):
    """Displays real system vitals, fetched by Kairos via hephaestus.fetch().

    A bare Frame tab body inside ModeratiPanel (the HEPHAESTUS tab); keeps its
    own update() and is registered with Kairos directly. One column, top to
    bottom: SYSTEM (CPU, RAM, the boot drive, Felhaven's drive, node · OS ·
    uptime), GPU (name, temperature, memory, load — from Brontes inside
    hephaestus.fetch()), OLLAMA (an OllamaWidget, its own "talos" panel) and
    AETHER (its own "aether" panel).

    Scrolls: the column is taller than the tab at Felhaven's 760 x 500
    minimum, so it sits in the §7 scroll frame — the PhosphorScroll packed
    first (test_scroll_lanes' rule, for Text; the same reason here), and a
    canvas with a token size so it asks for nothing (§7). The wheel is bound
    while the pointer is over the tab and judged by the pointer, not by
    <Leave>: the canvas hears <Leave> as the pointer moves onto its own rows.
    """

    # Each readout's hover tooltip (Virgil): a readout says what the number
    # means, so each is a glossary entry. Keys name the widgets in
    # self._tip_targets; test_virgil_surfaces holds the two in lockstep.
    _TIPS = {
        "cpu":          virgil.term("cpu"),
        "ram":          virgil.term("ram"),
        "boot_disk":    virgil.term("boot_drive"),
        "disk":         virgil.term("disk"),
        "uptime":       virgil.term("uptime"),
        "gpu":          virgil.term("gpu"),
        "gpu_memory":   virgil.term("gpu_memory"),
        "gpu_load":     virgil.term("gpu_load"),
        "loaded_model": virgil.term("loaded_model"),
        "wifi":         virgil.term("wifi"),
        "internet":     virgil.term("internet"),
        "ollama":       virgil.term("ollama"),
    }

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        # ── the scroll frame (§7) ──
        self._canvas = tk.Canvas(self, bg=C["card"], highlightthickness=0, bd=0,
                                 width=1, height=1)
        self._scroll = PhosphorScroll(self, command=self._canvas.yview, bg=C["card"])
        self._canvas.configure(yscrollcommand=self._scroll.set)
        self._scroll.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)
        body = self._body = tk.Frame(self._canvas, bg=C["card"])
        self._win = self._canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _e: self._sync_region())
        self._canvas.bind("<Configure>", self._on_canvas_config)
        self.bind("<Enter>", self._bind_wheel, add="+")
        self.bind("<Leave>", self._maybe_unbind_wheel, add="+")

        # ── SYSTEM ──
        _section(body, "SYSTEM", divider=False)
        self.cpu = BarMeter(body, "CPU", C["amber"])
        self.cpu.pack(fill="x")
        self.ram = BarMeter(body, "RAM", C["teal"])
        self.ram.pack(fill="x")
        self.boot_disk = BarMeter(body, "C: · boot", C["blue"])
        self.boot_disk.pack(fill="x")
        self.disk = BarMeter(body, "DISK", C["blue"])
        self.disk.pack(fill="x")
        self.node_lbl = tk.Label(body, text="", font=FONTS["tiny"],
                                 fg=C["text3"], bg=C["card"], anchor="w")
        self.node_lbl.pack(fill="x", pady=(4, 0))

        # ── GPU ──
        self.gpu_head = _section(body, "GPU")
        self._gpu_temp = tk.Label(self.gpu_head, text="", font=FONTS["small"],
                                  fg=C["text1"], bg=C["card"], anchor="e")
        self._gpu_temp.pack(side="right")
        self.gpu_memory = BarMeter(body, "GPU memory", C["purple"])
        self.gpu_load = BarMeter(body, "GPU load", C["purple"])
        self._gpu_note = tk.Label(body, text="", font=FONTS["tiny"], fg=C["text3"],
                                  bg=C["card"], anchor="w")
        self._show_gpu_bars(True)

        # ── OLLAMA, AETHER (their own Kairos panels) ──
        self.ollama = OllamaWidget(body)
        self.ollama.pack(fill="x")
        self.aether = AetherWidget(body)
        self.aether.pack(fill="x")

        self._tip_targets: dict[str, tk.Misc] = {
            "cpu": self.cpu, "ram": self.ram, "boot_disk": self.boot_disk,
            "disk": self.disk, "uptime": self.node_lbl, "gpu": self.gpu_head,
            "gpu_memory": self.gpu_memory, "gpu_load": self.gpu_load,
            "loaded_model": self.ollama.row, **self.aether.rows,
        }
        for key, tip in self._TIPS.items():
            if key in self._tip_targets:
                virgil_tooltip.attach(self._tip_targets[key], tip)

    # ── scrolling ──────────────────────────────────────────────────────────────

    def _sync_region(self) -> None:
        self._canvas.configure(scrollregion=(
            0, 0, self._body.winfo_reqwidth(), self._body.winfo_reqheight()))

    def _on_canvas_config(self, event) -> None:
        self._canvas.itemconfigure(self._win, width=event.width)

    def _bind_wheel(self, _event=None) -> None:
        self._canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _pointer_inside(self) -> bool:
        try:
            x, y = self.winfo_pointerxy()
            left, top = self.winfo_rootx(), self.winfo_rooty()
            return (left <= x < left + self.winfo_width()
                    and top <= y < top + self.winfo_height())
        except tk.TclError:
            return False

    def _maybe_unbind_wheel(self, _event=None) -> None:
        if not self._pointer_inside():          # onto a row is still "over the tab"
            self._canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event) -> None:
        self._canvas.yview_scroll(int(-event.delta / 120), "units")

    # ── data ───────────────────────────────────────────────────────────────────

    def _show_gpu_bars(self, readable: bool) -> None:
        for widget in (self.gpu_memory, self.gpu_load, self._gpu_note):
            widget.pack_forget()
        if readable:
            self.gpu_memory.pack(fill="x", before=self.ollama if hasattr(self, "ollama") else None)
            self.gpu_load.pack(fill="x", after=self.gpu_memory)
        else:
            self._gpu_note.pack(fill="x", after=self.gpu_head)

    def _update_gpu(self, gpu) -> None:
        if not isinstance(gpu, dict) or "error" in gpu:
            slug = str((gpu or {}).get("error", "nvml_failed")).split(":", 1)[0]
            self.gpu_head.title.config(text="GPU")
            self._gpu_temp.config(text="")
            self._gpu_note.config(text=_GPU_TROUBLE.get(slug, _GPU_TROUBLE["nvml_failed"]))
            self._show_gpu_bars(False)
            return
        self.gpu_head.title.config(text=f"GPU — {_gpu_name(gpu['name'])}")
        self._gpu_temp.config(text=f"{gpu['temp_c']}°C")
        self.gpu_memory.set(gpu["memory_pct"],
                            f"{gpu['memory_used_gb']:.1f} / {gpu['memory_total_gb']:.1f} GB")
        self.gpu_load.set(gpu["load_pct"])
        self._show_gpu_bars(True)

    def update(self, data: dict) -> None:
        """Called by Kairos every 5 s on the main thread."""
        if data is None:
            self.node_lbl.config(text="unavailable")
            return
        self.cpu.set(data["cpu"]["usage_percent"])
        self.ram.set(data["memory"]["percent_used"])
        storage = data["storage"]
        boot = data.get("boot_storage")
        here = _drive(storage.get("drive", ""))
        if boot:
            self.boot_disk.set_label(f"{_drive(boot['drive'])} · boot")
            self.boot_disk.set(boot["percent_used"])
            self.boot_disk.pack(fill="x", before=self.disk)
            self.disk.set_label(f"{here} · Felhaven" if here else "DISK")
        else:
            self.boot_disk.pack_forget()
            self.disk.set_label(f"{here} · boot · Felhaven" if here else "DISK")
        self.disk.set(storage["percent_used"])
        parts = [data.get("node"), data.get("os")]
        if data.get("uptime_s") is not None:
            parts.append(f"up {_uptime(data['uptime_s'])}")
        self.node_lbl.config(text=" · ".join(p for p in parts if p))
        self._update_gpu(data.get("gpu"))
