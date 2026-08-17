"""
pheme_panel.py — the Scriptorium: two reading surfaces over one set of feeds
============================================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Draw the Scriptorium's two reading surfaces over Pheme's feeds.

Surfaces:    SCRAPSTREAM (first, default) is the read surface: one merged river
             across every feed, newest first. SOURCE FONTS is the browse-by-
             source surface: the same feeds as collapsible sections. Scrapstream
             answers "what's new"; Source Fonts answers "what has The Register
             been posting". Neither tab is a superset of the other.

Scope:       PRESENTATION ONLY — merge order, the per-source cap, collapse
             state, dimming. It never fetches, and it never persists feed
             content. Feed data and read state are tools/pheme.py's (§12).

Upstream:    kairos.py — the "pheme" worker delivers to update() every 900 s.
Downstream:  tools/pheme.py (read state), webbrowser (click-through)

Requires:    json, os, tempfile, tkinter, webbrowser, datetime (stdlib)

Feeds come from config/pheme_rumormill.json in config order — no hardcoded
sources, adding a feed to the JSON adds it to both surfaces.

Merge:  each feed contributes at most _SCRAPSTREAM_PER_SOURCE stories, capped
        BEFORE the sort, then the pool is ordered by pheme's `ts` key descending.
        Capping first is the whole point: Hacker News posts dozens a day and WV
        News twice a week, so an uncapped merge would be permanently HN at the
        top and the local feed would never surface. The full per-source list is
        one click away in Source Fonts. `ts` is a full UTC timestamp, not a day
        — day-level keys across seven feeds are a wall of ties. Stories with no
        parseable date carry ts == "" and land last, never dropped.

Read:   dimming is driven by pheme.read_keys(); clicking through to an article
        is the ONLY thing that marks it read. One seen-set feeds both surfaces,
        and a click recolors every copy of that story in both tabs immediately
        rather than waiting for the next 900 s tick — divergence here would make
        the dimming untrustworthy, which is worse than not dimming at all.

Failure: a merged river hides a dead feed — its stories are simply absent. So
        the footer carries "⚠ N failed" beside the timestamp (the home_panel
        meta-line dialect), and each Source Fonts section header names its own
        failure. Six good feeds and one dead one render six feeds, never a blank
        river.

Collapse state persists to pheme_sections.json (atomic, gitignored runtime
state, §9). Missing or corrupt -> first section open, rest closed, silently. An
explicitly-saved all-closed state IS honoured: overriding a choice the user made
by hand is worse than the empty screen the default guards against.

Each surface owns a _ScrollFrame (Canvas + PhosphorScroll — never tk.Scrollbar,
CONVENTIONS §7/§12). The tab bar is theme.TabHost, not a fourth hand-rolled copy.

Kairos contract (single method): update(data), same payload as before.
    data = {"feeds": {feed_id: {"stories": [...]} | {"error": str}}}
    data is None            -> both surfaces empty, footer "stale"
    a feed's {"error": ...} -> slug mapped through _ERROR_TEXT onto that
                               section's header, and counted in the footer

V0.02: Audited 2026-07-23 — grew the _ERROR_TEXT map (echo_panel pattern) in
       lockstep with pheme's §2 slugs, replacing the raw render of
       result["error"]; tests/test_pheme_panel_smoke.py holds the two halves
       together.
       Restructured 2026-08-02 from seven per-source tabs into Scrapstream +
       Source Fonts; adopted theme.TabHost and the shared right-click helper.
"""

import json
import os
import tempfile
import tkinter as tk
import webbrowser
from datetime import datetime
from typing import Any, Callable

from theme import C, FONTS, PhosphorScroll, TabHost, popup_menu, themed_menu
from tools import pheme

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# pheme_rumormill.json lives in config/ under the app root — two levels up from
# panels/. abspath() makes resolution independent of the current working directory.
_CONFIG_PATH = os.path.join(_APP_ROOT, "config", "pheme_rumormill.json")

# Which sections were left open. Runtime state, gitignored (§9).
_SECTIONS_PATH = os.path.join(_APP_ROOT, "pheme_sections.json")

# Stories each feed may contribute to one Scrapstream render, applied before the
# merge sort. A module constant, not config (the aether/zeno convention): it is a
# layout property of this river, not a knob a user tunes.
_SCRAPSTREAM_PER_SOURCE = 5

_CHEVRON_OPEN   = "▾"
_CHEVRON_CLOSED = "▸"

# A feed row from config, and one rendered story's recolorable labels (the meta
# line is absent when a story has no author, date or domain).
_Feed = dict[str, Any]
_Row  = tuple[tk.Label, "tk.Label | None"]
_Rows = dict[str, list[_Row]]
_Handler = Callable[["tk.Event[Any]"], None]


def _load_feeds() -> list[_Feed]:
    """Ordered feed list from config. [] (empty panel) if config is unreadable —
    a bad config degrades the panel, it never crashes the app."""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            feeds: list[_Feed] = json.load(f)["feeds"]
            return feeds
    except Exception:
        return []


# ── Collapse state ───────────────────────────────────────────────────────────

def _load_sections(feeds: list[_Feed]) -> set[str]:
    """Feed ids whose Source Fonts section starts expanded.

    Missing or corrupt file -> the first feed only. Never all-closed by default:
    seven headers over no content is a dead screen, and makes reading a two-click
    operation. Ids no longer in config are dropped rather than kept as ghosts.
    """
    default: set[str] = {feeds[0]["id"]} if feeds else set()
    try:
        with open(_SECTIONS_PATH, encoding="utf-8") as f:
            raw = json.load(f)["expanded"]
    except FileNotFoundError:
        return default
    except Exception:
        return default
    if not isinstance(raw, list):
        return default
    valid = {f["id"] for f in feeds}
    return {fid for fid in raw if isinstance(fid, str) and fid in valid}


def _save_sections(expanded: set[str]) -> None:
    """Persist collapse state atomically (§9: mkstemp in the same directory,
    then os.replace). Written on toggle only, never per tick. A failed write
    costs the restore across a restart, never the render."""
    try:
        # Same directory as the destination, derived from it — os.replace is
        # only atomic within a filesystem, and on Windows fails outright across
        # drives (which is also what makes the path patchable from a test).
        directory = os.path.dirname(os.path.abspath(_SECTIONS_PATH))
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".pheme-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"expanded": sorted(expanded)}, f)
            os.replace(tmp, _SECTIONS_PATH)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    except Exception:
        pass


# ── Error text ───────────────────────────────────────────────────────────────

# Human text per per-feed error slug from tools/pheme.py._fetch_feed (anything
# unknown falls back to the raw code so a new code is surfaced, never
# swallowed — the echo_panel rule). handle()'s slugs are Pythia-facing and
# never reach this panel, so they are deliberately not mapped here.
_ERROR_TEXT = {
    "http_error":       "feed returned HTTP",       # detail: the status code
    "feed_timeout":     "feed timed out",
    "network_error":    "network error",
    "invalid_feed_xml": "feed sent unreadable data",
    "fetch_failed":     "feed fetch failed",        # detail: exception class
}


def _error_text(code: str) -> str:
    """Human text for a `slug` or `slug: detail` identifier."""
    slug, sep, detail = code.partition(": ")
    if slug not in _ERROR_TEXT:
        return code
    return f"{_ERROR_TEXT[slug]}: {detail}" if sep else _ERROR_TEXT[slug]


# ─────────────────────────────────────────────────────────────────────────────
#  _ScrollFrame — a vertically scrollable container
# ─────────────────────────────────────────────────────────────────────────────

class _ScrollFrame(tk.Frame):
    """
    Canvas + PhosphorScroll wrapper. Pack content into `.inner`.

    Labels that should wrap to the available width register via add_wrap_label();
    their wraplength is recomputed whenever the canvas is resized, so titles
    reflow correctly even though this tab wasn't visible (sized) when first built.
    """

    def __init__(self, parent: tk.Misc) -> None:
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
    def _on_inner_config(self, _event: "tk.Event[Any]") -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_config(self, event: "tk.Event[Any]") -> None:
        # Pin the inner frame to the canvas width (no horizontal scroll) and
        # reflow every registered wrap label to the new width.
        self._canvas.itemconfigure(self._win, width=event.width)
        for label, pad in self._wrap_labels:
            label.config(wraplength=max(1, event.width - pad))

    # ── wheel ───────────────────────────────────────────────────────────────
    def _bind_wheel(self, _event: "tk.Event[Any]") -> None:
        self._canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event: "tk.Event[Any]") -> None:
        self._canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event: "tk.Event[Any]") -> None:
        # Windows wheel deltas arrive in multiples of 120.
        self._canvas.yview_scroll(int(-event.delta / 120), "units")

    # ── content ─────────────────────────────────────────────────────────────
    def add_wrap_label(self, label: tk.Label, pad: int = 24) -> None:
        """Register a label to wrap to (canvas width - pad)."""
        self._wrap_labels.append((label, pad))
        width = self._canvas.winfo_width()
        if width > 1:
            label.config(wraplength=max(1, width - pad))

    def bind_menu(self, handler: _Handler) -> None:
        """Route right-clicks on the empty canvas and on the inner frame to
        `handler`. Row widgets bind it themselves as they are built — a Label
        swallows the click that would otherwise reach the frame beneath it."""
        self._canvas.bind("<Button-3>", handler)
        self.inner.bind("<Button-3>", handler)

    def clear(self) -> None:
        """Destroy all rows and drop stale wrap-label references."""
        for w in self.inner.winfo_children():
            w.destroy()
        self._wrap_labels.clear()
        self._canvas.yview_moveto(0)


# ─────────────────────────────────────────────────────────────────────────────
#  PhemePanel
# ─────────────────────────────────────────────────────────────────────────────

class PhemePanel(TabHost):
    """Scrapstream (merged river) + Source Fonts (collapsible per-source
    sections), both fed by the single `pheme` Kairos worker."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, "Scriptorium — rumor mill", C["coral"])

        self._feeds:    list[_Feed] = _load_feeds()
        self._expanded: set[str]    = _load_sections(self._feeds)
        self._read:     set[str]    = pheme.read_keys()

        # key -> [(title_label, meta_label_or_None), ...], one registry per
        # surface, so one click dims every copy of a story without a re-render.
        # Kept apart because a Source Fonts toggle rebuilds only that surface —
        # a single shared dict would accumulate dead widgets on every toggle.
        self._rows_river: _Rows = {}
        self._rows_fonts: _Rows = {}

        # Last payload rendered, so a collapse toggle can rebuild Source Fonts
        # without refetching (and without disturbing the river's scroll).
        self._last_results: dict[str, Any] = {}

        # Plain-text mirror of what each surface currently shows, for "Copy all".
        self._text_river: list[str] = []
        self._text_fonts: list[str] = []

        # Footer is packed FIRST and side="bottom" so it owns the bottom edge.
        # TabHost._show_tab re-packs the active body on every switch, which puts
        # that body last in the pack order — an expanding body packed after a
        # side="top" footer would swallow the footer's space entirely.
        footer_row = tk.Frame(self.body, bg=C["card"])
        footer_row.pack(side="bottom", fill="x", pady=(4, 0))
        self._fail_lbl = tk.Label(
            footer_row, text="", font=FONTS["card_header"],
            fg=C["red"], bg=C["card"], anchor="e",
        )
        self._fail_lbl.pack(side="right")
        self._footer = tk.Label(
            footer_row, text="", font=FONTS["card_header"],
            fg=C["text3"], bg=C["card"], anchor="e",
        )
        self._footer.pack(side="right")

        self._river = _ScrollFrame(self.body)
        self._fonts = _ScrollFrame(self.body)
        self._river.bind_menu(self._show_menu)
        self._fonts.bind_menu(self._show_menu)

        self._build_tabs(
            [("scrapstream", "SCRAPSTREAM",  self._river, C["coral"]),
             ("sourcefonts", "SOURCE FONTS", self._fonts, C["coral"])],
            default="scrapstream",
        )

    # ── right-click menu (theme.themed_menu / popup_menu — the shared helper) ──

    def _show_menu(self, event: "tk.Event[Any]") -> None:
        """Copy all only: these rows are tk.Labels, which have no selection
        model, so the Text-widget Copy/Select All of the home/echo/emanon menus
        has nothing to act on here."""
        lines = self._visible_lines()
        menu = themed_menu(self)
        menu.add_command(label="Copy all", command=self._copy_all,
                         state="normal" if lines else "disabled")
        popup_menu(menu, event)

    def _visible_lines(self) -> list[str]:
        """The active surface's rendered rows as plain text. Source Fonts
        reports expanded sections only — what is on screen is what is copied."""
        if self._active == "sourcefonts":
            return self._text_fonts
        return self._text_river

    def _copy_all(self) -> None:
        lines = self._visible_lines()
        if not lines:
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))

    # ── story rows ────────────────────────────────────────────────────────────

    def _build_story_row(self, scroll: _ScrollFrame, parent: tk.Frame,
                         story: dict[str, Any], sink: list[str],
                         rows: _Rows) -> None:
        """Title (full text, wrapped) + open arrow, then an optional meta line.

        `scroll` is only the wrap-width authority; `parent` is where the widgets
        actually pack — the river packs into scroll.inner, a Source Fonts row
        packs into its section body so collapsing hides it. `sink` collects the
        plain-text form for Copy all, `rows` the read-key registry to dim from.
        """
        url       = story.get("url", "")
        clickable = url.startswith(("http://", "https://"))
        key       = pheme.read_key(url) if url else ""
        is_read   = bool(key) and key in self._read

        outer = tk.Frame(parent, bg=C["card"])
        outer.pack(fill="x", pady=(2, 0))
        outer.bind("<Button-3>", self._show_menu)

        # ── Line 1: title + open arrow ────────────────────────────────────────
        line1 = tk.Frame(outer, bg=C["card"])
        line1.pack(fill="x")

        arrow = tk.Label(
            line1, text="↗", font=FONTS["small"],
            fg=C["teal"] if clickable else C["text3"], bg=C["card"],
        )
        arrow.pack(side="right", anchor="n")

        title_text = story.get("title", "(no title)")
        title = tk.Label(
            line1, text=title_text, font=FONTS["small"],
            fg=C["text3"] if is_read else C["text1"], bg=C["card"],
            anchor="w", justify="left",
        )
        title.pack(side="left", fill="x", expand=True)
        scroll.add_wrap_label(title, pad=16)   # room for arrow + PhosphorScroll's 8px lane
        title.bind("<Button-3>", self._show_menu)
        arrow.bind("<Button-3>", self._show_menu)
        sink.append(title_text)

        # ── Line 2: author · date · domain (whichever are present) ────────────
        meta = " · ".join(
            p for p in (story.get("author", ""), story.get("date", ""),
                        story.get("domain", "")) if p
        )
        meta_lbl: "tk.Label | None" = None
        if meta:
            meta_lbl = tk.Label(
                outer, text=meta, font=FONTS["tiny"],
                fg=C["text3"] if is_read else C["text2"], bg=C["card"],
                anchor="w", justify="left",
            )
            meta_lbl.pack(fill="x")
            scroll.add_wrap_label(meta_lbl, pad=24)
            meta_lbl.bind("<Button-3>", self._show_menu)
            sink.append(f"    {meta}")

        # ── Divider ───────────────────────────────────────────────────────────
        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", pady=(3, 0))

        # Register under the read key so a click in either surface dims both.
        if key:
            rows.setdefault(key, []).append((title, meta_lbl))

        if clickable:
            def _open(_e: "tk.Event[Any]", u: str = url, k: str = key) -> None:
                self._mark_read(k, u)
                webbrowser.open(u)

            def _hover_in(_e: "tk.Event[Any]", l: tk.Label = title) -> None:
                l.config(fg=C["amber"])

            def _hover_out(_e: "tk.Event[Any]", l: tk.Label = title,
                           k: str = key) -> None:
                l.config(fg=C["text3"] if k in self._read else C["text1"])

            title.config(cursor="hand2")
            title.bind("<Button-1>", _open)
            title.bind("<Enter>",    _hover_in)
            title.bind("<Leave>",    _hover_out)
            arrow.bind("<Button-1>", _open)

    def _mark_read(self, key: str, url: str) -> None:
        """Click-through: persist via Pheme, then recolor every rendered copy of
        this story in both surfaces. Widgets may already be destroyed if a Kairos
        tick landed between the click and here — that is not an error."""
        if not key:
            return
        pheme.mark_read(url)
        self._read.add(key)
        for rows in (self._rows_river, self._rows_fonts):
            for title_lbl, meta_lbl in rows.get(key, []):
                try:
                    title_lbl.config(fg=C["text3"])
                    if meta_lbl is not None:
                        meta_lbl.config(fg=C["text3"])
                except tk.TclError:
                    continue

    # ── placeholders ─────────────────────────────────────────────────────────

    def _note(self, parent: tk.Misc, text: str) -> None:
        lbl = tk.Label(parent, text=text, font=FONTS["small_italic"],
                       fg=C["text3"], bg=C["card"], anchor="w", justify="left")
        lbl.pack(anchor="w", pady=(8, 0))
        lbl.bind("<Button-3>", self._show_menu)

    # ── Scrapstream ──────────────────────────────────────────────────────────

    def _render_river(self, results: dict[str, Any]) -> None:
        """Cap each feed at _SCRAPSTREAM_PER_SOURCE, THEN sort the pool by ts
        descending. Cap-before-sort is what keeps a high-volume feed from owning
        the top of the river."""
        self._river.clear()
        self._text_river = []
        self._rows_river = {}

        pool: list[dict[str, Any]] = []
        for feed in self._feeds:
            result = results.get(feed["id"])
            if not isinstance(result, dict) or "error" in result:
                continue
            pool.extend(result.get("stories", [])[:_SCRAPSTREAM_PER_SOURCE])

        # Stable sort: ts == "" (missing or unparseable date) is the smallest
        # string, so reverse=True puts those last, and ties keep config order.
        pool.sort(key=lambda s: s.get("ts", ""), reverse=True)

        if not pool:
            self._note(self._river.inner, "no stories available")
            return
        for story in pool:
            self._build_story_row(self._river, self._river.inner, story,
                                  self._text_river, self._rows_river)

    # ── Source Fonts ─────────────────────────────────────────────────────────

    def _render_sections(self, results: dict[str, Any]) -> None:
        """One collapsible section per feed, uncapped by this panel (pheme's
        own per-feed fetch cap is the ceiling)."""
        self._fonts.clear()
        self._text_fonts = []
        self._rows_fonts = {}

        if not self._feeds:
            self._note(self._fonts.inner, "no feeds configured")
            return

        for feed in self._feeds:
            self._build_section(feed, results.get(feed["id"]))

    def _build_section(self, feed: _Feed, result: Any) -> None:
        fid   = feed["id"]
        open_ = fid in self._expanded

        outer = tk.Frame(self._fonts.inner, bg=C["card"])
        outer.pack(fill="x", pady=(0, 2))

        header = tk.Frame(outer, bg=C["card"], cursor="hand2")
        header.pack(fill="x")

        chevron = tk.Label(
            header, text=_CHEVRON_OPEN if open_ else _CHEVRON_CLOSED,
            font=FONTS["card_header"], fg=C["text2"], bg=C["card"],
        )
        chevron.pack(side="left", padx=(0, 4))
        name = tk.Label(header, text=feed["label"], font=FONTS["card_header"],
                        fg=C["text1"] if open_ else C["text3"], bg=C["card"])
        name.pack(side="left")

        # Per-source truth about failure lives here — this is the tab where the
        # user can see WHICH source is down, not just that one is.
        if result is None:
            status = "no data"
        elif isinstance(result, dict) and "error" in result:
            status = _error_text(result["error"])
        else:
            status = ""
        if status:
            tk.Label(header, text=f"⚠ {status}", font=FONTS["card_header"],
                     fg=C["red"], bg=C["card"], anchor="e").pack(side="right")

        tk.Frame(outer, bg=C["border"], height=1).pack(fill="x", pady=(2, 0))

        body = tk.Frame(outer, bg=C["card"])
        if open_:
            body.pack(fill="x", padx=(12, 0))

        # Rows are built for collapsed sections too — only the body frame goes
        # unpacked — so toggling is instant and needs no re-render. Copy all,
        # though, mirrors the screen: a collapsed section contributes nothing.
        sink: list[str] = self._text_fonts if open_ else []
        if open_:
            sink.append(feed["label"] + (f"  ⚠ {status}" if status else ""))

        stories: list[dict[str, Any]] = []
        if isinstance(result, dict) and "error" not in result:
            stories = result.get("stories", [])
        if status:
            self._note(body, status)
        elif not stories:
            self._note(body, "no stories available")
        else:
            for story in stories:
                self._build_story_row(self._fonts, body, story,
                                      sink, self._rows_fonts)

        toggler = self._toggler(fid)
        for widget in (header, chevron, name):
            widget.bind("<Button-1>", toggler)
            widget.bind("<Button-3>", self._show_menu)

    def _toggler(self, fid: str) -> _Handler:
        """Bound toggle handler for one section. A named closure rather than the
        `lambda e, i=fid:` default-argument idiom — same late-binding fix, but
        the type of the captured id is stated instead of inferred."""
        def _handler(_e: "tk.Event[Any]") -> None:
            self._toggle(fid)
        return _handler

    def _toggle(self, fid: str) -> None:
        """Expand/collapse one section and persist. Rebuilds Source Fonts from
        the last payload rather than re-fetching — presentation only."""
        if fid in self._expanded:
            self._expanded.discard(fid)
        else:
            self._expanded.add(fid)
        _save_sections(self._expanded)
        self._render_sections(self._last_results)

    # ── Kairos update contract ──────────────────────────────────────────────────

    def update(self, data: Any) -> None:   # type: ignore[override]
        """Called by the `pheme` Kairos worker. Renders both surfaces from one
        payload and counts failures for the footer.

        The override of tk.Misc.update() is the house Kairos panel contract, not
        an accident — every polled panel takes the payload here.
        """
        self._read = pheme.read_keys()

        if data is None:
            # Kairos delivered nothing at all (config unreadable / worker died).
            self._last_results = {}
            self._render_river({})
            self._render_sections({})
            self._footer.config(text="stale")
            self._fail_lbl.config(text="")
            return

        results = data.get("feeds", {}) or {}
        self._last_results = results

        self._render_river(results)
        self._render_sections(results)

        # A configured feed missing from the payload counts as failed too: in a
        # merged river an absent feed is indistinguishable from a broken one.
        failed = sum(
            1 for feed in self._feeds
            if not isinstance(results.get(feed["id"]), dict)
            or "error" in results[feed["id"]]
        )
        self._footer.config(text=f"updated {datetime.now():%H:%M}")
        self._fail_lbl.config(text=f" · ⚠ {failed} failed" if failed else "")
