"""
Pheme panel — RSS / Atom news aggregator for the Felhaven dashboard.

PhemePanel builds one tab per feed listed in pheme_rumormill.json (repo root),
in config order, with the first feed active by default. There are no hardcoded
tabs — adding a feed to the JSON adds a tab.

Each tab owns a vertically-scrollable body (_ScrollFrame: Canvas + Scrollbar).
The mouse wheel is bound only while the pointer is over that tab's canvas, so
stacked scroll frames don't fight over wheel events.

Story rows render the full title wrapped to ~2 lines (wraplength, no manual
truncation) and a meta line of "author · date · domain" (whichever are present).
Rows are destroyed and rebuilt on every update() call.

Kairos contract (single method): update(data) routes each feed's result to its
own tab.
    data = {"feeds": {feed_id: {"stories": [...]} | {"error": str}}}
    data is None                  -> every tab shows "stale"
    a feed's {"error": ...}       -> inline "feed unavailable (…)" in that tab
                                     only; footer reads "failed HH:MM".
"""

import json
import os
import tkinter as tk
import webbrowser
from datetime import datetime

from theme import C, FONTS, Card, PhosphorScroll

# pheme_rumormill.json sits at the repo root — one directory up from panels/.
# abspath() makes resolution independent of the current working directory.
_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "pheme_rumormill.json",
)


def _load_feeds() -> list:
    """Ordered feed list from config. [] (empty panel) if config is unreadable —
    a bad config degrades the panel, it never crashes the app."""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)["feeds"]
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  _ScrollFrame — a vertically scrollable container
# ─────────────────────────────────────────────────────────────────────────────

class _ScrollFrame(tk.Frame):
    """
    Canvas + Scrollbar wrapper. Pack content into `.inner`.

    Labels that should wrap to the available width register via add_wrap_label();
    their wraplength is recomputed whenever the canvas is resized, so titles
    reflow correctly even though this tab wasn't visible (sized) when first built.
    """

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        self._canvas = tk.Canvas(self, bg=C["card"], highlightthickness=0, bd=0)
        scroll = PhosphorScroll(self, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scroll.set)

        scroll.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self.inner = tk.Frame(self._canvas, bg=C["card"])
        self._win  = self._canvas.create_window((0, 0), window=self.inner, anchor="nw")

        # (label, horizontal_padding_px) for width-following wrap.
        self._wrap_labels: list[tuple[tk.Label, int]] = []

        self.inner.bind("<Configure>", self._on_inner_config)
        self._canvas.bind("<Configure>", self._on_canvas_config)

        # Wheel only while hovering -> stacked scroll frames don't fight.
        self._canvas.bind("<Enter>", self._bind_wheel)
        self._canvas.bind("<Leave>", self._unbind_wheel)

    # ── geometry ────────────────────────────────────────────────────────────
    def _on_inner_config(self, _event) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_config(self, event) -> None:
        # Pin the inner frame to the canvas width (no horizontal scroll) and
        # reflow every registered wrap label to the new width.
        self._canvas.itemconfigure(self._win, width=event.width)
        for label, pad in self._wrap_labels:
            label.config(wraplength=max(1, event.width - pad))

    # ── wheel ───────────────────────────────────────────────────────────────
    def _bind_wheel(self, _event) -> None:
        self._canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event) -> None:
        self._canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event) -> None:
        # Windows wheel deltas arrive in multiples of 120.
        self._canvas.yview_scroll(int(-event.delta / 120), "units")

    # ── content ─────────────────────────────────────────────────────────────
    def add_wrap_label(self, label: tk.Label, pad: int = 24) -> None:
        """Register a label to wrap to (canvas width - pad)."""
        self._wrap_labels.append((label, pad))
        width = self._canvas.winfo_width()
        if width > 1:
            label.config(wraplength=max(1, width - pad))

    def clear(self) -> None:
        """Destroy all rows and drop stale wrap-label references."""
        for w in self.inner.winfo_children():
            w.destroy()
        self._wrap_labels.clear()
        self._canvas.yview_moveto(0)


# ─────────────────────────────────────────────────────────────────────────────
#  PhemePanel
# ─────────────────────────────────────────────────────────────────────────────

class PhemePanel(Card):
    """One tab per configured feed; fed by the single `pheme` Kairos worker."""

    def __init__(self, parent):
        super().__init__(parent, "Scriptorium — rumor mill", C["coral"])

        self._feeds = _load_feeds()
        self._tabs: dict[str, dict] = {}   # feed_id -> widget bundle
        self._active: str | None = None

        # Tab bar across the top, content area beneath it.
        tab_row = tk.Frame(self.body, bg=C["card"])
        tab_row.pack(fill="x", pady=(6, 0))
        self._content = tk.Frame(self.body, bg=C["card"])
        self._content.pack(fill="both", expand=True)

        for feed in self._feeds:
            self._build_tab(tab_row, feed)

        if self._feeds:
            self._show_tab(self._feeds[0]["id"])
        else:
            tk.Label(
                self._content, text="no feeds configured",
                font=FONTS["small_italic"], fg=C["text3"], bg=C["card"],
            ).pack(anchor="w", pady=(8, 0))

    # ── construction ──────────────────────────────────────────────────────────

    def _build_tab(self, tab_row: tk.Frame, feed: dict) -> None:
        fid = feed["id"]

        wrap = tk.Frame(tab_row, bg=C["card"])
        wrap.pack(side="left", padx=(0, 10))
        tab = tk.Label(
            wrap, text=feed["label"], font=FONTS["card_header"],
            fg=C["text3"], bg=C["card"], cursor="hand2",
        )
        tab.pack()
        line = tk.Frame(wrap, height=1, bg=C["border"])
        line.pack(fill="x")
        tab.bind("<Button-1>", lambda e, i=fid: self._show_tab(i))

        frame  = tk.Frame(self._content, bg=C["card"])
        scroll = _ScrollFrame(frame)
        scroll.pack(fill="both", expand=True, pady=(4, 0))
        footer = tk.Label(
            frame, text="", font=FONTS["card_header"],
            fg=C["text3"], bg=C["card"], anchor="e",
        )
        footer.pack(fill="x", pady=(4, 0))

        self._tabs[fid] = {
            "tab": tab, "line": line, "frame": frame,
            "scroll": scroll, "footer": footer,
        }

    # ── tab switching ───────────────────────────────────────────────────────

    def _show_tab(self, fid: str) -> None:
        if self._active == fid or fid not in self._tabs:
            return

        if self._active is not None:
            prev = self._tabs[self._active]
            prev["frame"].pack_forget()
            prev["tab"].config(fg=C["text3"])
            prev["line"].config(bg=C["border"])

        self._active = fid
        cur = self._tabs[fid]
        cur["frame"].pack(fill="both", expand=True)
        cur["tab"].config(fg=C["text1"])
        cur["line"].config(bg=C["coral"])

    # ── story rows ────────────────────────────────────────────────────────────

    def _build_story_row(self, scroll: _ScrollFrame, story: dict) -> None:
        """Title (full text, wrapped) + open arrow, then an optional meta line."""
        parent    = scroll.inner
        url       = story.get("url", "")
        clickable = url.startswith(("http://", "https://"))

        outer = tk.Frame(parent, bg=C["card"])
        outer.pack(fill="x", pady=(2, 0))

        # ── Line 1: title + open arrow ────────────────────────────────────────
        line1 = tk.Frame(outer, bg=C["card"])
        line1.pack(fill="x")

        arrow = tk.Label(
            line1, text="↗", font=FONTS["small"],
            fg=C["teal"] if clickable else C["text3"], bg=C["card"],
        )
        arrow.pack(side="right", anchor="n")

        title = tk.Label(
            line1, text=story.get("title", "(no title)"), font=FONTS["small"],
            fg=C["text1"], bg=C["card"], anchor="w", justify="left",
        )
        title.pack(side="left", fill="x", expand=True)
        scroll.add_wrap_label(title, pad=16)   # leave room for arrow + PhosphorScroll's 8px lane

        # ── Line 2: author · date · domain (whichever are present) ────────────
        meta = " · ".join(
            p for p in (story.get("author", ""), story.get("date", ""),
                        story.get("domain", "")) if p
        )
        if meta:
            meta_lbl = tk.Label(
                outer, text=meta, font=FONTS["tiny"],
                fg=C["text2"], bg=C["card"], anchor="w", justify="left",
            )
            meta_lbl.pack(fill="x")
            scroll.add_wrap_label(meta_lbl, pad=24)

        # ── Divider ───────────────────────────────────────────────────────────
        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", pady=(3, 0))

        if clickable:
            def _open(e, u=url):       webbrowser.open(u)
            def _hover_in(e,  l=title): l.config(fg=C["amber"])
            def _hover_out(e, l=title): l.config(fg=C["text1"])
            title.config(cursor="hand2")
            title.bind("<Button-1>", _open)
            title.bind("<Enter>",    _hover_in)
            title.bind("<Leave>",    _hover_out)
            arrow.bind("<Button-1>", _open)

    # ── rendering ─────────────────────────────────────────────────────────────

    def _render_feed(self, fid: str, result) -> None:
        """Route one feed's Kairos result into its tab."""
        tab = self._tabs.get(fid)
        if tab is None:
            return
        scroll, footer = tab["scroll"], tab["footer"]
        scroll.clear()

        # No data at all from Kairos (config unreadable / worker failed).
        if result is None:
            footer.config(text="stale")
            return

        # This feed failed but its siblings may be fine — show it inline.
        if "error" in result:
            tk.Label(
                scroll.inner, text=f"feed unavailable ({result['error']})",
                font=FONTS["small_italic"], fg=C["text3"], bg=C["card"],
                anchor="w", justify="left",
            ).pack(anchor="w", pady=(8, 0))
            footer.config(text=f"failed {datetime.now():%H:%M}")
            return

        stories = result.get("stories", [])
        if not stories:
            tk.Label(
                scroll.inner, text="no stories available",
                font=FONTS["small_italic"], fg=C["text3"], bg=C["card"],
            ).pack(anchor="w", pady=(8, 0))
        else:
            for story in stories:
                self._build_story_row(scroll, story)

        footer.config(text=f"updated {datetime.now():%H:%M}")

    # ── Kairos update contract ──────────────────────────────────────────────────

    def update(self, data) -> None:
        """Called by the `pheme` Kairos worker. Routes each feed to its tab."""
        if data is None:
            for fid in self._tabs:
                self._render_feed(fid, None)
            return

        feeds = data.get("feeds", {})
        for fid in self._tabs:
            self._render_feed(fid, feeds.get(fid))
