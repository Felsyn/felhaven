"""
Morpheus panel — YouTube audio player (no video, ever).

MorpheusPanel is a bare tk.Frame tab body inside VoxArrayPanel (the MORPHEUS
tab) — the "Vox Array" Card header + tab bar are owned by that host now. It
keeps its own update() and stays registered with Kairos directly under the
"morpheus" worker (felhaven.py reaches through the host to it). This is exactly
the Moderati refactor (VitalsPanel / EmanonPanel became bare Frame tab bodies);
only the outer shell changed — transport, playlists, search are untouched.

MorpheusPanel hosts, top to bottom inside itself:
    Transport row  — now-playing label + position + ⏮ ⏯ ⏭ ⏹ (always visible).
    Tab bar        — PLAYLISTS / SEARCH (midas tab pattern: tk.Label + underline).
    PLAYLISTS tab  — one clickable row per morpheus_playlists.json entry.
    SEARCH tab     — keyless yt-dlp search; clickable result rows.

If mpv / yt-dlp aren't found, both tab bodies show a placeholder and the
transport controls are inert (rendered dim, never bound) — the dashboard never
crashes over a missing binary (Midas no_key precedent).

Kairos contract: update(data) is called every 2 s with morpheus.fetch()'s dict
(or None). It refreshes the transport row and — importantly — drains the search
result queue (see the threading note below).

─────────────────────────────────────────────────────────────────────────────
SEARCH THREADING — a documented deviation from the house rule.

  House rule: "no panel spawns threads; Kairos owns all timing." morpheus.search()
  blocks for seconds (it shells out to yt-dlp) and Kairos has no request-driven
  job slot, so the panel runs the search on its own daemon thread. The
  queue-is-the-only-shared-object contract from kairos.py is preserved:

    - The worker thread's ONLY shared touch is self._search_q.put(rows).
      It never touches a Tk object.
    - update() (main thread, 2 s cadence) does get_nowait() and renders.
      Worst-case extra latency before results appear: one tick (~2 s). Accepted.
    - A single-flight guard (self._search_thread) ignores Enter while a search
      is already in flight — same idea as Kairos's _running_threads.
─────────────────────────────────────────────────────────────────────────────
"""

import queue
import threading
import tkinter as tk

from theme import C, FONTS, PhosphorScroll

from tools import morpheus

# Placeholder shown in both tabs when a binary is missing. Backslash before the
# space is intentional (the bin\ directory).
_NO_BIN_MSG = ("mpv / yt-dlp not found — drop mpv.exe and yt-dlp.exe in "
               "bin\\ or install to PATH")


# ─────────────────────────────────────────────────────────────────────────────
#  _ScrollFrame — vertically scrollable container.
#
#  Same pattern as pheme_panel._ScrollFrame / midas_panel._LedgerScroll. House
#  convention is one scroll frame per panel (copied, not shared) so panels stay
#  decoupled — see CONVENTIONS §7. Pack content into `.inner`; register labels
#  that should reflow to the width via add_wrap_label(); the mouse wheel binds
#  only while hovering so stacked frames don't fight over wheel events.
# ─────────────────────────────────────────────────────────────────────────────

class _ScrollFrame(tk.Frame):
    """Canvas + Scrollbar wrapper. Pack content into `.inner`."""

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        self._canvas = tk.Canvas(self, bg=C["card"], highlightthickness=0, bd=0)
        scroll = PhosphorScroll(self, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scroll.set)

        scroll.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self.inner = tk.Frame(self._canvas, bg=C["card"])
        self._win = self._canvas.create_window((0, 0), window=self.inner, anchor="nw")

        # (label, horizontal_padding_px) for width-following wrap.
        self._wrap_labels: list[tuple[tk.Label, int]] = []

        self.inner.bind("<Configure>", self._on_inner_config)
        self._canvas.bind("<Configure>", self._on_canvas_config)
        self._canvas.bind("<Enter>", self._bind_wheel)
        self._canvas.bind("<Leave>", self._unbind_wheel)

    def _on_inner_config(self, _event) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_config(self, event) -> None:
        # Pin inner to canvas width (no horizontal scroll) + reflow wrap labels.
        self._canvas.itemconfigure(self._win, width=event.width)
        for label, pad in self._wrap_labels:
            label.config(wraplength=max(1, event.width - pad))

    def _bind_wheel(self, _event) -> None:
        self._canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event) -> None:
        self._canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event) -> None:
        # Windows wheel deltas arrive in multiples of 120.
        self._canvas.yview_scroll(int(-event.delta / 120), "units")

    def add_wrap_label(self, label: tk.Label, pad: int = 24) -> None:
        """Register a label to wrap to (canvas width - pad)."""
        self._wrap_labels.append((label, pad))
        width = self._canvas.winfo_width()
        if width > 1:
            label.config(wraplength=max(1, width - pad))

    def clear(self) -> None:
        """Destroy all rows, drop stale wrap-label refs, scroll back to top."""
        for w in self.inner.winfo_children():
            w.destroy()
        self._wrap_labels.clear()
        self._canvas.yview_moveto(0)


class MorpheusPanel(tk.Frame):
    """Transport + playlists + search. Status polled by Kairos; mutations by UI.

    A bare Frame tab body inside VoxArrayPanel (the MORPHEUS tab); the Card
    header + tab bar are the host's now."""

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        avail = morpheus.available()
        self._enabled = bool(avail["mpv"] and avail["ytdlp"])

        # Search threading (see module docstring).
        self._search_q: queue.Queue = queue.Queue()
        self._search_thread: "threading.Thread | None" = None

        self._build_transport()
        self._build_tabs()

    # ── time formatting ─────────────────────────────────────────────────────

    @staticmethod
    def _fmt_t(sec) -> str:
        """Seconds → 'm:ss'. '-:--' for None (idle / unknown)."""
        if sec is None:
            return "-:--"
        s = int(sec)
        return f"{s // 60}:{s % 60:02d}"

    # ── transport row ─────────────────────────────────────────────────────────

    def _build_transport(self) -> None:
        tr = tk.Frame(self, bg=C["card"])
        tr.pack(fill="x", pady=(6, 4))

        self._now_lbl = tk.Label(
            tr, text="nothing playing", font=FONTS["small_bold"],
            fg=C["text3"], bg=C["card"], anchor="w", justify="left",
            wraplength=560,
        )
        self._now_lbl.pack(fill="x", anchor="w")

        ctrl = tk.Frame(tr, bg=C["card"])
        ctrl.pack(fill="x", pady=(2, 0))

        self._pos_lbl = tk.Label(
            ctrl, text="-:-- / -:--", font=FONTS["tiny"],
            fg=C["text3"], bg=C["card"], anchor="w",
        )
        self._pos_lbl.pack(side="left")

        # ⏮ ⏯ ⏭ ⏹ — packed right. Iterate reversed so visual order reads
        # prev · play/pause · next · stop left-to-right.
        self._btns: dict[str, tk.Label] = {}
        spec = [
            ("prev", "⏮", morpheus.prev_track),
            ("play", "▶", self._on_toggle_pause),   # optimistic flip; update() corrects
            ("next", "⏭", morpheus.next_track),
            ("stop", "⏹", morpheus.stop),
        ]
        for key, glyph, fn in reversed(spec):
            b = tk.Label(
                ctrl, text=glyph, font=FONTS["medium"],
                fg=C["text1"] if self._enabled else C["text3"],
                bg=C["card"], padx=6,
            )
            b.pack(side="right")
            if self._enabled:
                b.config(cursor="hand2")
                b.bind("<Button-1>", lambda e, f=fn: f())
            self._btns[key] = b

    def _on_toggle_pause(self) -> None:
        """Play/pause click handler. Flips the ⏯ glyph immediately instead of
        waiting up to one Kairos tick (~2 s) for update() to reflect it — the
        next tick then corrects it from real state, so a failed IPC
        self-corrects within one tick."""
        morpheus.toggle_pause()
        b = self._btns["play"]
        b.config(text="⏸" if b.cget("text") == "▶" else "▶")

    # ── tab bar ─────────────────────────────────────────────────────────────

    def _build_tabs(self) -> None:
        tab_row = tk.Frame(self, bg=C["card"])
        tab_row.pack(fill="x", pady=(6, 0))

        self._tabs: dict[str, tk.Label] = {}
        self._lines: dict[str, tk.Frame] = {}
        for key, text in (("playlists", "PLAYLISTS"), ("search", "SEARCH")):
            wrap = tk.Frame(tab_row, bg=C["card"])
            wrap.pack(side="left", padx=(0, 14))
            lbl = tk.Label(wrap, text=text, font=FONTS["card_header"],
                           fg=C["text3"], bg=C["card"], cursor="hand2")
            lbl.pack()
            line = tk.Frame(wrap, height=1, bg=C["border"])
            line.pack(fill="x")
            lbl.bind("<Button-1>", lambda e, k=key: self._show_tab(k))
            self._tabs[key] = lbl
            self._lines[key] = line

        self._playlists_frame = tk.Frame(self, bg=C["card"])
        self._search_frame = tk.Frame(self, bg=C["card"])

        if self._enabled:
            self._build_playlists(self._playlists_frame)
            self._build_search(self._search_frame)
        else:
            for frame in (self._playlists_frame, self._search_frame):
                tk.Label(frame, text=_NO_BIN_MSG, font=FONTS["small_italic"],
                         fg=C["text3"], bg=C["card"], anchor="w",
                         justify="left", wraplength=560).pack(anchor="w", pady=(10, 0))

        # Activate PLAYLISTS.
        self._active = "playlists"
        self._playlists_frame.pack(fill="both", expand=True)
        self._tabs["playlists"].config(fg=C["text1"])
        self._lines["playlists"].config(bg=C["purple"])

    def _show_tab(self, key: str) -> None:
        if key == self._active:
            return
        self._playlists_frame.pack_forget()
        self._search_frame.pack_forget()
        self._tabs[self._active].config(fg=C["text3"])
        self._lines[self._active].config(bg=C["border"])

        self._active = key
        frame = self._playlists_frame if key == "playlists" else self._search_frame
        frame.pack(fill="both", expand=True)
        self._tabs[key].config(fg=C["text1"])
        self._lines[key].config(bg=C["purple"])

    # ── PLAYLISTS tab ─────────────────────────────────────────────────────────

    def _build_playlists(self, parent: tk.Frame) -> None:
        # Fixed add-form — always visible, never scrolls away.
        self._build_pl_add_form(parent)

        # 1-px separator between the form and the list.
        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", pady=(6, 0))

        # Scrollable playlist list — stored so _reload_playlists() can clear it.
        self._pl_scroll = _ScrollFrame(parent)
        self._pl_scroll.pack(fill="both", expand=True, pady=(4, 0))

        self._reload_playlists()

    def _build_pl_add_form(self, parent: tk.Frame) -> None:
        """Fixed (non-scrolling) form for adding a new playlist entry."""
        self._pl_label_var = tk.StringVar()
        self._pl_url_var   = tk.StringVar()

        form = tk.Frame(parent, bg=C["card"])
        form.pack(fill="x", pady=(4, 0))

        # ── Row 1: label field ───────────────────────────────────────────────────
        row1 = tk.Frame(form, bg=C["card"])
        row1.pack(fill="x", pady=(0, 2))

        tk.Label(
            row1, text="label", font=FONTS["tiny"],
            fg=C["text3"], bg=C["card"], width=5, anchor="w",
        ).pack(side="left")

        tk.Entry(
            row1, textvariable=self._pl_label_var, font=FONTS["small"],
            bg=C["bar_bg"], fg=C["text1"], insertbackground=C["text1"],
            highlightthickness=1, highlightbackground=C["border"], bd=0,
        ).pack(fill="x", expand=True, side="left")

        # ── Row 2: url field + save button ───────────────────────────────────────
        row2 = tk.Frame(form, bg=C["card"])
        row2.pack(fill="x")

        tk.Label(
            row2, text="url", font=FONTS["tiny"],
            fg=C["text3"], bg=C["card"], width=5, anchor="w",
        ).pack(side="left")

        # Save button — pack BEFORE the expanding entry so it lands on the right.
        save_btn = tk.Label(
            row2, text="＋ save", font=FONTS["small"],
            fg=C["text3"], bg=C["card"], cursor="hand2", padx=8,
        )
        save_btn.pack(side="right")
        save_btn.bind("<Button-1>", lambda e: self._on_save_playlist())
        save_btn.bind("<Enter>",    lambda e: save_btn.config(fg=C["text1"]))
        save_btn.bind("<Leave>",    lambda e: save_btn.config(fg=C["text3"]))

        url_entry = tk.Entry(
            row2, textvariable=self._pl_url_var, font=FONTS["small"],
            bg=C["bar_bg"], fg=C["text1"], insertbackground=C["text1"],
            highlightthickness=1, highlightbackground=C["border"], bd=0,
        )
        url_entry.pack(fill="x", expand=True, side="left")
        # <Return> on the url field triggers save — natural flow: label → url → Enter.
        url_entry.bind("<Return>", lambda e: self._on_save_playlist())

        # ── Status row ────────────────────────────────────────────────────────────
        self._pl_status_lbl = tk.Label(
            form, text="", font=FONTS["tiny"],
            fg=C["text3"], bg=C["card"], anchor="w",
        )
        self._pl_status_lbl.pack(fill="x", pady=(2, 0))

    def _reload_playlists(self) -> None:
        """Clear and re-render the playlist scroll list from disk. UI-triggered only.

        Each row carries a two-click remove glyph. The captured `idx` is the
        entry's position in the freshly-loaded list; any add or remove re-renders
        the whole list, so a captured index can never go stale within the app."""
        self._pl_scroll.clear()
        playlists = morpheus.load_playlists()
        if not playlists:
            tk.Label(
                self._pl_scroll.inner,
                text="no playlists — add one above",
                font=FONTS["small_italic"], fg=C["text3"], bg=C["card"], anchor="w",
            ).pack(anchor="w", pady=(10, 0))
            return
        for idx, p in enumerate(playlists):
            url = p.get("url", "")

            outer = tk.Frame(self._pl_scroll.inner, bg=C["card"])
            outer.pack(fill="x", pady=2)

            # ── Remove glyph — pack BEFORE the label so it lands on the right.
            glyph = tk.Label(
                outer, text="✕", font=FONTS["small"],
                fg=C["text3"], bg=C["card"], cursor="hand2", padx=4,
            )
            glyph._armed = False   # two-click confirm state (see _on_remove_playlist)
            glyph.pack(side="right")
            glyph.bind("<Enter>", lambda e, g=glyph: self._pl_glyph_hover(g, C["text1"]))
            glyph.bind("<Leave>", lambda e, g=glyph: self._pl_glyph_hover(g, C["text3"]))
            glyph.bind("<Button-1>",
                       lambda e, i=idx, g=glyph: self._on_remove_playlist(i, g))

            row = tk.Label(
                outer, text=p.get("label", "(unnamed)"),
                font=FONTS["body"], fg=C["text1"], bg=C["card"],
                anchor="w", justify="left",
            )
            row.pack(fill="x", anchor="w", side="left", expand=True)
            self._pl_scroll.add_wrap_label(row, pad=36)   # 8 (PhosphorScroll lane) + 28 (✕ glyph)
            if url:
                row.config(cursor="hand2")
                row.bind("<Button-1>", lambda e, u=url: morpheus.play(u))
                row.bind("<Enter>",    lambda e, r=row: r.config(fg=C["amber"]))
                row.bind("<Leave>",    lambda e, r=row: r.config(fg=C["text1"]))

    def _pl_glyph_hover(self, glyph: tk.Label, color: str) -> None:
        """Hover highlight for a remove glyph — suppressed while it is armed, so
        the amber 'remove?' prompt stays put when the mouse moves off it."""
        if not glyph._armed:
            glyph.config(fg=color)

    def _on_remove_playlist(self, index: int, glyph: tk.Label) -> None:
        """Two-click confirm. First click arms the glyph ('remove?'); a second
        click within 2.5 s deletes, otherwise it auto-cancels. A single stray
        click only ever arms — it can never delete."""
        if not glyph._armed:
            glyph._armed = True
            glyph.config(text="remove?", fg=C["amber"])
            self.after(2500, lambda g=glyph: self._disarm_remove(g))
            return

        if morpheus.remove_playlist(index):
            self._reload_playlists()      # destroys this glyph + re-renders fresh
            self._pl_flash("removed", ok=True)
        else:
            glyph._armed = False
            glyph.config(text="✕", fg=C["text3"])
            self._pl_flash("remove failed — check log", ok=False)

    def _disarm_remove(self, glyph: tk.Label) -> None:
        """Revert an armed remove glyph after the confirm window lapses. Guards
        winfo_exists() because a confirmed delete destroys the glyph before this
        scheduled callback fires."""
        if glyph.winfo_exists() and glyph._armed:
            glyph._armed = False
            glyph.config(text="✕", fg=C["text3"])

    def _on_save_playlist(self) -> None:
        """Save button / Return handler for the add-form."""
        label = self._pl_label_var.get().strip()
        url   = self._pl_url_var.get().strip()

        if not label or not url:
            self._pl_flash("label and url required", ok=False)
            return
        if not url.startswith(("http://", "https://")):
            self._pl_flash("url must start with http:// or https://", ok=False)
            return

        if morpheus.save_playlist(label, url):
            self._pl_label_var.set("")
            self._pl_url_var.set("")
            self._reload_playlists()
            self._pl_flash("saved", ok=True)
        else:
            self._pl_flash("save failed — check log", ok=False)

    def _pl_flash(self, msg: str, ok: bool) -> None:
        """Show a transient status message under the add-form. Clears after 2.5 s."""
        self._pl_status_lbl.config(
            text=msg,
            fg=C["text1"] if ok else C["amber"],
        )
        self.after(2500, lambda: self._pl_status_lbl.config(text=""))

    # ── SEARCH tab ──────────────────────────────────────────────────────────

    def _build_search(self, parent: tk.Frame) -> None:
        bar = tk.Frame(parent, bg=C["card"])
        bar.pack(fill="x", pady=(8, 4))
        self._search_var = tk.StringVar()
        entry = tk.Entry(bar, textvariable=self._search_var, font=FONTS["small"],
                         bg=C["bar_bg"], fg=C["text1"], insertbackground=C["text1"],
                         highlightthickness=1, highlightbackground=C["border"], bd=0)
        entry.pack(fill="x")
        entry.bind("<Return>", lambda e: self._on_search())

        # Results scroll independently below the fixed search bar.
        self._results = _ScrollFrame(parent)
        self._results.pack(fill="both", expand=True, pady=(4, 0))

    def _clear_results(self) -> None:
        self._results.clear()

    def _result_msg(self, text: str) -> None:
        self._clear_results()
        tk.Label(self._results.inner, text=text, font=FONTS["small_italic"],
                 fg=C["text3"], bg=C["card"], anchor="w").pack(anchor="w", pady=(6, 0))

    def _on_search(self) -> None:
        # Single-flight: ignore Enter while a search thread is still alive.
        if self._search_thread is not None and self._search_thread.is_alive():
            return
        query = self._search_var.get().strip()
        if not query:
            return
        self._result_msg("searching…")
        # The lambda runs on the worker thread; it touches only the queue.
        self._search_thread = threading.Thread(
            target=lambda q=query: self._search_q.put(morpheus.search(q)),
            daemon=True,
        )
        self._search_thread.start()

    def _drain_search(self) -> None:
        """Main-thread delivery point for search results (called from update())."""
        try:
            rows = self._search_q.get_nowait()
        except queue.Empty:
            return

        if not rows:
            self._result_msg("no results")
            return
        if len(rows) == 1 and "error" in rows[0]:
            self._result_msg("search failed")
            return

        self._clear_results()
        for r in rows:
            self._build_result_row(r)

    def _build_result_row(self, r: dict) -> None:
        parent = self._results.inner
        url = r.get("url", "")
        clickable = url.startswith(("http://", "https://"))

        outer = tk.Frame(parent, bg=C["card"])
        outer.pack(fill="x", pady=(2, 0))

        # ── Save glyph (clickable rows only) — pack BEFORE title so it lands right.
        if clickable:
            save_glyph = tk.Label(
                outer, text="＋", font=FONTS["small"],
                fg=C["text3"], bg=C["card"], cursor="hand2", padx=4,
            )
            save_glyph.pack(side="right")
            save_glyph.bind("<Enter>",    lambda e, g=save_glyph: g.config(fg=C["text1"]))
            save_glyph.bind("<Leave>",    lambda e, g=save_glyph: g.config(fg=C["text3"]))
            save_glyph.bind(
                "<Button-1>",
                lambda e, t=r.get("title", "(untitled)"), u=url, g=save_glyph:
                    self._save_from_search(t, u, g),
            )

        title = tk.Label(
            outer, text=r.get("title", "(untitled)"),
            font=FONTS["small"], fg=C["text1"], bg=C["card"],
            anchor="w", justify="left",
        )
        title.pack(fill="x", anchor="w", side="left", expand=True)
        # 36 = 8 (PhosphorScroll lane) + ~28 (＋ glyph + padx). Use 8 when no glyph.
        self._results.add_wrap_label(title, pad=36 if clickable else 8)

        meta = " · ".join(p for p in (r.get("channel", ""),
                                      self._fmt_dur(r.get("duration"))) if p)
        if meta:
            tk.Label(
                outer, text=meta, font=FONTS["tiny"],
                fg=C["text3"], bg=C["card"], anchor="w",
            ).pack(fill="x", anchor="w")

        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", pady=(3, 0))

        if clickable:
            title.config(cursor="hand2")
            title.bind("<Button-1>", lambda e, u=url: morpheus.play(u))
            title.bind("<Enter>",    lambda e, l=title: l.config(fg=C["amber"]))
            title.bind("<Leave>",    lambda e, l=title: l.config(fg=C["text1"]))

    def _save_from_search(self, title: str, url: str, glyph: tk.Label) -> None:
        """Save a search result row to morpheus_playlists.json (Option C)."""
        if morpheus.save_playlist(title, url):
            # Tombstone: ✓ stays, hover/click bindings removed.
            glyph.config(text="✓", fg=C["text1"])
            glyph.unbind("<Enter>")
            glyph.unbind("<Leave>")
            glyph.unbind("<Button-1>")
            self._reload_playlists()
        else:
            # Transient failure indicator — resets after 2 s.
            glyph.config(text="!", fg=C["amber"])
            self.after(2000, lambda: glyph.config(text="＋", fg=C["text3"]))

    @staticmethod
    def _fmt_dur(sec) -> str:
        """Seconds → 'm:ss' for the meta line; '' for None (omitted from the join)."""
        if sec is None:
            return ""
        s = int(sec)
        return f"{s // 60}:{s % 60:02d}"

    # ── Kairos update contract ────────────────────────────────────────────────

    def update(self, data) -> None:
        """Called every 2 s by the `morpheus` Kairos worker on the main thread."""
        if not self._enabled:
            return

        if data is None or not data.get("running"):
            self._now_lbl.config(text="nothing playing", fg=C["text3"])
            self._pos_lbl.config(text="-:-- / -:--")
            self._btns["play"].config(text="▶")
        else:
            self._now_lbl.config(text=data.get("title") or "(loading…)", fg=C["text1"])
            self._pos_lbl.config(
                text=f"{self._fmt_t(data.get('pos'))} / {self._fmt_t(data.get('dur'))}")
            # ▶ ("press to play") when paused; ⏸ ("press to pause") when playing.
            self._btns["play"].config(text="▶" if data.get("paused") else "⏸")

        # Deliver any completed search regardless of playback state.
        self._drain_search()
