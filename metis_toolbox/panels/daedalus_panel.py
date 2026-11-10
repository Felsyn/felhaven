"""
panels/daedalus_panel.py — the DAEDALUS face inside Moderati
============================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Draw Daedalus's map of Felhaven's modules and show the chosen
             one's docstring.
             Every module is a dot, every import between two of them a line
             (dashed when the import only runs inside a function). Hover a dot
             to light it and its neighbours; click it to read its docstring
             below, exactly as the module wrote it, and to open the file in
             Notepad.

One parse:   Everything drawn here comes from tools/daedalus.py — index(),
             layout(), docstring(), source_path(). This panel never walks the
             filesystem, never calls ast, and never imports a module to read
             it. If it needs something new, Daedalus exposes it.

Drawing:     Canvas items are built once per index revision and only
             recoloured after that (itemconfig on hover and click), never
             deleted and recreated per interaction. A resize moves them with
             coords(). The force layout runs inside daedalus.layout() the
             first time the tab is SHOWN (~0.2 s), not when Felhaven builds
             the tab at launch, and is cached until a file changes.

Refresh:     <Map> — each time the tab comes into view — asks for the index,
             which costs one stat per file when nothing moved. An edited
             module is therefore current the next time you look. No after()
             loop, no thread, not Kairos-registered: request-driven, like
             Cerberus, Settings and Machine Spirit.

Notepad:     OPEN IN NOTEPAD runs `notepad.exe <path>` — and never
             os.startfile. The default action for a .py on Windows is often to
             RUN it with the Python launcher, which is the one thing a button
             labelled "open" must never do (cerberus_panel's open-folder
             helper uses startfile on a FOLDER, where that is safe; not here).
             The path comes only from daedalus.source_path() and is checked
             again to sit inside metis_toolbox/; anything else is refused.
             Off Windows the button is disabled with a note, with no
             xdg-open/open fallback, for the same reason.

Upstream:    panels/moderati_panel.py (hosts this as the DAEDALUS tab)
Downstream:  tools/daedalus.py (the index, the layout, the docstrings, the
             paths), theme.py (colors/fonts/PhosphorScroll)

Requires:    tkinter, subprocess (stdlib).
"""

import os
import subprocess
import sys
import tkinter as tk
from typing import Any, Optional

from theme import C, FONTS, PhosphorScroll
from tools import daedalus

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_IS_WINDOWS = sys.platform == "win32"

# Folder → palette key. The theme is a phosphor ramp, so folders separate by
# brightness, plus amber for the core that ties them together.
_GROUP_COLOR = {"core": "amber", "tools": "text1", "panels": "text2"}
_R = 4                 # node radius, px
_R_SELECTED = 6


class DaedalusPanel(tk.Frame):
    """Felhaven's import graph and a docstring reader. A bare Frame tab body
    inside ModeratiPanel; no Kairos worker, no update() — request-driven."""

    def __init__(self, parent: tk.Widget):
        super().__init__(parent, bg=C["card"])
        self._revision: Optional[int] = None
        self._nodes: dict[str, dict[str, Any]] = {}
        self._neighbours: dict[str, set[str]] = {}
        self._node_items: dict[str, tuple[int, int]] = {}      # key -> (oval, label)
        self._item_key: dict[int, str] = {}                    # oval/label id -> key
        self._edge_items: list[tuple[int, str, str, bool]] = []  # (id, src, dst, lazy)
        self._hovered: Optional[str] = None
        self._selected: Optional[str] = None
        self._open_enabled = False

        # grid, not pack: the graph and the reader both absorb slack, and pack
        # can only split leftover space evenly. 2:1 favours the map. Both ask
        # for a token height (§7), so neither can squeeze out the rows between.
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=3)
        self.rowconfigure(3, weight=1)

        tk.Label(self, text="DAEDALUS — Felhaven's map of itself",
                 font=FONTS["card_header"], fg=C["text2"], bg=C["card"],
                 anchor="w").grid(row=0, column=0, sticky="ew", pady=(8, 2))

        # ── Docstring pane ────────────────────────────────────────────────────
        pane = tk.Frame(self, bg=C["card"])
        pane.grid(row=3, column=0, sticky="nsew", pady=(6, 0))
        head = tk.Frame(pane, bg=C["card"])
        head.pack(fill="x")
        self._open = tk.Label(head, text="OPEN IN NOTEPAD", font=FONTS["small_bold"],
                              fg=C["text3"], bg=C["card"])
        self._open.pack(side="right")
        self._open.bind("<Button-1>", lambda _e: self._open_in_notepad())
        self._open.bind("<Enter>", lambda _e: self._hover_open(True))
        self._open.bind("<Leave>", lambda _e: self._hover_open(False))
        self._title = tk.Label(head, text="Click a module to read its docstring.",
                               font=FONTS["small_bold"], fg=C["text2"], bg=C["card"],
                               anchor="w")
        self._title.pack(side="left", fill="x", expand=True)
        self._note = tk.Label(pane, text="" if _IS_WINDOWS else "Notepad is Windows-only.",
                              font=FONTS["tiny"], fg=C["text3"], bg=C["card"], anchor="w")
        self._note.pack(fill="x")

        text_row = tk.Frame(pane, bg=C["card"])
        text_row.pack(fill="both", expand=True)
        # wrap="none": the house-style headers are column-aligned, and wrapping
        # would break exactly the alignment a docstring is read for. Shift+wheel
        # scrolls sideways when a line is wider than the pane.
        self._text = tk.Text(
            text_row, font=FONTS["small"], bg=C["bar_bg"], fg=C["text1"],
            highlightbackground=C["border"], highlightthickness=1, borderwidth=0,
            wrap="none", height=1, padx=6, pady=4, state="disabled",
        )
        # The scroll lane first (CONVENTIONS §7): the Text asks for 80
        # characters of width, and packed first it took the whole row of a
        # narrow window and pack unmapped the scrollbar.
        scroll = PhosphorScroll(text_row, command=self._text.yview)
        scroll.pack(side="right", fill="y")
        self._text.pack(side="left", fill="both", expand=True)
        self._text.configure(yscrollcommand=scroll.set)
        self._text.bind("<Shift-MouseWheel>", self._on_shift_wheel)

        # ── Graph ─────────────────────────────────────────────────────────────
        legend = tk.Frame(self, bg=C["card"])
        legend.grid(row=2, column=0, sticky="ew", pady=(2, 0))
        for text, key in (("● core", "amber"), ("● tools", "text1"), ("● panels", "text2")):
            tk.Label(legend, text=text, font=FONTS["tiny"], fg=C[key],
                     bg=C["card"]).pack(side="left", padx=(0, 10))
        tk.Label(legend, text="── import   ┄┄ lazy import",
                 font=FONTS["tiny"], fg=C["text3"], bg=C["card"]).pack(side="left")

        # height=1: a bare Canvas asks for 265 px (§7) — let expand do the growing.
        self._canvas = tk.Canvas(self, bg=C["bar_bg"], height=1, borderwidth=0,
                                 highlightthickness=1, highlightbackground=C["border"])
        self._canvas.grid(row=1, column=0, sticky="nsew")
        self._canvas.tag_bind("node", "<Enter>", self._on_enter)
        self._canvas.tag_bind("node", "<Leave>", self._on_leave)
        self._canvas.tag_bind("node", "<Button-1>", self._on_click)
        self._canvas.bind("<Configure>", lambda _e: self._place())

        self._set_open_enabled(False)
        self.bind("<Map>", lambda _e: self.refresh())

    # ── Index → canvas ────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Re-read the index, and rebuild the canvas only if it changed."""
        snap = daedalus.index()
        if snap.get("revision") == self._revision:
            return
        self._revision = snap.get("revision")
        self._nodes = snap.get("nodes", {})
        self._neighbours = {key: set() for key in self._nodes}
        for edge in snap.get("edges", []):
            self._neighbours[edge["source"]].add(edge["target"])
            self._neighbours[edge["target"]].add(edge["source"])
        self._build(snap.get("edges", []))
        if self._hovered not in self._nodes:
            self._hovered = None
        if self._selected in self._nodes:
            self._show(self._selected)          # its docstring may have changed
        else:
            self._clear_selection()
        self._place()
        self._paint()

    def _build(self, edges: list[dict[str, Any]]) -> None:
        """Create every item once for this revision. Edges first, so they sit
        under the nodes."""
        cv = self._canvas
        cv.delete("all")
        self._node_items.clear()
        self._item_key.clear()
        self._edge_items = []
        for edge in edges:
            item = cv.create_line(0, 0, 0, 0, width=1,
                                  dash=(2, 4) if edge["lazy"] else ())
            self._edge_items.append((item, edge["source"], edge["target"], edge["lazy"]))
        for key in self._nodes:
            oval = cv.create_oval(0, 0, 0, 0, width=1, tags=("node",))
            label = cv.create_text(0, 0, text=key, anchor="n", font=FONTS["tiny"],
                                   tags=("node",))
            self._node_items[key] = (oval, label)
            self._item_key[oval] = key
            self._item_key[label] = key

    def _place(self) -> None:
        """Move every item to daedalus.layout() for the canvas's current size."""
        w, h = self._canvas.winfo_width(), self._canvas.winfo_height()
        if w < 40 or h < 40 or not self._node_items:
            return                                   # not laid out yet
        pos = daedalus.layout(w, h)
        for key, (oval, label) in self._node_items.items():
            if key not in pos:
                continue
            x, y = pos[key]
            r = _R_SELECTED if key == self._selected else _R
            self._canvas.coords(oval, x - r, y - r, x + r, y + r)
            self._canvas.coords(label, x, y + r + 1)
        for item, src, dst, _lazy in self._edge_items:
            if src in pos and dst in pos:
                self._canvas.coords(item, *pos[src], *pos[dst])

    def _paint(self) -> None:
        """Recolour every item for the current hover/selection. The focus is
        the hovered node, else the selected one: it and its neighbours light,
        everything else dims. No focus, no dimming."""
        focus = self._hovered or self._selected
        lit = ({focus} | self._neighbours.get(focus, set())) if focus else None
        cv = self._canvas
        for key, (oval, label) in self._node_items.items():
            node = self._nodes[key]
            color = C[_GROUP_COLOR.get(node["group"], "text2")]
            dim = lit is not None and key not in lit
            fill = C["badge_bg"] if dim else color
            outline = fill
            if node.get("parse_error"):
                outline = C["red"]
            if not node.get("has_docstring"):
                fill = C["bar_bg"]                   # hollow: nothing to read
            selected = key == self._selected
            cv.itemconfig(oval, fill=fill, outline=C["text1"] if selected else outline,
                          width=2 if selected else 1)
            cv.itemconfig(label, fill=(C["badge_bg"] if dim else
                                       C["text1"] if (lit and key in lit) or selected
                                       else C["text3"]))
        for item, src, dst, _lazy in self._edge_items:
            if lit is None:
                color = C["border"]
            elif focus in (src, dst):
                color = C["text1"]
            else:
                color = C["badge_bg"]
            cv.itemconfig(item, fill=color)

    # ── Interaction ───────────────────────────────────────────────────────────

    def _current_key(self) -> Optional[str]:
        hit = self._canvas.find_withtag("current")
        return self._item_key.get(hit[0]) if hit else None

    def _on_enter(self, _event: "tk.Event[Any]") -> None:
        key = self._current_key()
        if key is not None and key != self._hovered:
            self._hovered = key
            self._canvas.configure(cursor="hand2")
            self._paint()

    def _on_leave(self, _event: "tk.Event[Any]") -> None:
        self._hovered = None
        self._canvas.configure(cursor="")
        self._paint()

    def _on_click(self, _event: "tk.Event[Any]") -> None:
        key = self._current_key()
        if key is not None:
            self.select(key)

    def select(self, key: str) -> None:
        """Select a module: ring its dot and show its docstring below."""
        if key not in self._nodes:
            return
        self._selected = key
        self._show(key)
        self._place()                                # the ring is a bigger dot
        self._paint()

    # ── Docstring pane ────────────────────────────────────────────────────────

    def _show(self, key: str) -> None:
        """Fill the pane with exactly what daedalus.docstring() returned."""
        found = daedalus.docstring(key)
        path = found.get("path") or self._nodes.get(key, {}).get("path", "")
        if "error" in found:
            body = f"(this module could not be read — {found['error']})"
        elif found.get("docstring") is None:
            body = "(no module docstring)"
        else:
            body = found["docstring"]
        self._title.config(text=f"{key} · {path}")
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.insert("1.0", body)
        self._text.configure(state="disabled")
        self._text.yview_moveto(0)
        self._text.xview_moveto(0)
        self._note.config(text="" if _IS_WINDOWS else "Notepad is Windows-only.")
        self._set_open_enabled(_IS_WINDOWS)

    def _clear_selection(self) -> None:
        self._selected = None
        self._title.config(text="Click a module to read its docstring.")
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.configure(state="disabled")
        self._set_open_enabled(False)

    def _on_shift_wheel(self, event: "tk.Event[Any]") -> str:
        self._text.xview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    # ── Notepad ───────────────────────────────────────────────────────────────

    def _set_open_enabled(self, on: bool) -> None:
        self._open_enabled = on
        self._open.config(fg=C["text2"] if on else C["badge_bg"],
                          cursor="hand2" if on else "")

    def _hover_open(self, on: bool) -> None:
        if self._open_enabled:
            self._open.config(fg=C["amber"] if on else C["text2"])

    def _open_in_notepad(self) -> None:
        """Open the selected module in Notepad. Never os.startfile — see the
        module docstring — and never a path from anywhere but Daedalus."""
        if not (self._open_enabled and _IS_WINDOWS and self._selected):
            return
        path = daedalus.source_path(self._selected)
        if path is None or not _inside_app_root(path):
            self._note.config(text="Refused: not a Felhaven source file.",
                              fg=C["red"])
            return
        try:
            subprocess.Popen(["notepad.exe", path],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            self._note.config(text=f"Notepad could not start ({type(exc).__name__}).",
                              fg=C["red"])
            return
        self._note.config(text=f"Opened {os.path.basename(path)} in Notepad.",
                          fg=C["text3"])


def _inside_app_root(path: str) -> bool:
    """True only for an existing .py file under metis_toolbox/."""
    root = os.path.realpath(_APP_ROOT)
    full = os.path.realpath(path)
    try:
        inside = os.path.commonpath([root, full]) == root
    except ValueError:                       # a different drive
        return False
    return inside and full.endswith(".py") and os.path.isfile(full)
