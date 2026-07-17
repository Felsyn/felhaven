"""
Orpheus panel — play back a recorded briefing from local_audio/.

OrpheusPanel is a bare tk.Frame tab body inside VoxArrayPanel (the ORPHEUS
tab), the same shape as MorpheusPanel and EchoPanel. Unlike Echo, Orpheus IS
Kairos-registered (the "orpheus" worker, 2 s) — update(data) drives both the
transport (flips back to idle when a briefing finishes on its own, since
Harmonia's sd.wait() blocks inside its own thread and the panel has no other
way to see that) and the file list (no watcher, no manual refresh button —
fetch() returns the local_audio/ listing on the same tick).

Top to bottom inside self:
    Transport row  — now-playing label + ⏹ (enabled only while playing).
    File list      — one clickable row per local_audio/ file (name + its
                     duration, "m:ss" or "duration unknown"); click plays it.

If ffmpeg isn't found, the tab shows a placeholder and stays inert — the
dashboard never crashes over a missing binary (Morpheus/Echo precedent).

orpheus.play_file() runs a local ffmpeg decode (not a network call, not a TTS
synthesis) — fast enough for what Echo produces to call directly from the
click handler, unlike Echo's whole-document conversion or Morpheus's yt-dlp
search. No thread, no queue.

Because Harmonia has no channels (see harmonia.py), "playing" here reflects
ANY sound coming out of the speaker, not only a briefing Orpheus started —
if Calliope is mid-answer, this tab will show "playing" too, and its ⏹ will
stop that. That cross-talk is a deliberate consequence of the one-stream
design, not a bug in this panel (flagged, not fixed, per the handoff).
"""

import tkinter as tk

from theme import C, FONTS, PhosphorScroll

from tools import orpheus

_NO_BIN_MSG = "ffmpeg not found — drop ffmpeg.exe in bin\\ or install to PATH"

# Human-readable status text per Orpheus error code.
_ERROR_TEXT = {
    "bad_name": "invalid filename",
    "ffmpeg_unavailable": "ffmpeg not found",
    "decode_failed": "could not decode that file — check the log",
}


# ─────────────────────────────────────────────────────────────────────────────
#  _ScrollFrame — vertically scrollable container.
#
#  Copied per panel, not shared (CONVENTIONS §7) — see morpheus_panel.py's
#  _ScrollFrame for the identical, longer-commented original.
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

        self._wrap_labels: list[tuple[tk.Label, int]] = []

        self.inner.bind("<Configure>", self._on_inner_config)
        self._canvas.bind("<Configure>", self._on_canvas_config)
        self._canvas.bind("<Enter>", self._bind_wheel)
        self._canvas.bind("<Leave>", self._unbind_wheel)

    def _on_inner_config(self, _event) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_config(self, event) -> None:
        self._canvas.itemconfigure(self._win, width=event.width)
        for label, pad in self._wrap_labels:
            label.config(wraplength=max(1, event.width - pad))

    def _bind_wheel(self, _event) -> None:
        self._canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event) -> None:
        self._canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event) -> None:
        self._canvas.yview_scroll(int(-event.delta / 120), "units")

    def add_wrap_label(self, label: tk.Label, pad: int = 24) -> None:
        self._wrap_labels.append((label, pad))
        width = self._canvas.winfo_width()
        if width > 1:
            label.config(wraplength=max(1, width - pad))

    def clear(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()
        self._wrap_labels.clear()
        self._canvas.yview_moveto(0)


class OrpheusPanel(tk.Frame):
    """Transport + file list. Status polled by Kairos; play/stop by UI click.

    A bare Frame tab body inside VoxArrayPanel (the ORPHEUS tab)."""

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        avail = orpheus.available()
        self._enabled = bool(avail["ffmpeg"])
        # None (not []) so the very first update() always renders — an empty
        # local_audio/ must still show the "no files" hint, not skip as a no-op.
        self._files: "list | None" = None

        self._build_transport()
        if self._enabled:
            self._scroll = _ScrollFrame(self)
            self._scroll.pack(fill="both", expand=True, pady=(4, 0))
        else:
            tk.Label(self, text=_NO_BIN_MSG, font=FONTS["small_italic"],
                     fg=C["text3"], bg=C["card"], anchor="w",
                     justify="left", wraplength=560).pack(anchor="w", pady=(10, 0))

    # ── transport row ─────────────────────────────────────────────────────────

    def _build_transport(self) -> None:
        tr = tk.Frame(self, bg=C["card"])
        tr.pack(fill="x", pady=(6, 4))

        # ⏹ — pack BEFORE the expanding label so it lands on the right.
        self._stop_btn = tk.Label(tr, text="⏹", font=FONTS["medium"],
                                   fg=C["text3"], bg=C["card"], padx=6)
        self._stop_btn.pack(side="right")

        self._now_lbl = tk.Label(
            tr, text="nothing playing", font=FONTS["small_bold"],
            fg=C["text3"], bg=C["card"], anchor="w", justify="left",
            wraplength=480,
        )
        self._now_lbl.pack(fill="x", anchor="w", side="left", expand=True)

        self._status = tk.Label(self, text="", font=FONTS["tiny"],
                                fg=C["text3"], bg=C["card"], anchor="w")
        self._status.pack(fill="x", pady=(0, 2))

        self._set_stop_enabled(False)

    def _set_stop_enabled(self, enabled: bool) -> None:
        if enabled:
            self._stop_btn.config(fg=C["text1"], cursor="hand2")
            self._stop_btn.bind("<Button-1>", lambda e: self._on_stop())
            self._stop_btn.bind("<Enter>", lambda e: self._stop_btn.config(fg=C["amber"]))
            self._stop_btn.bind("<Leave>", lambda e: self._stop_btn.config(fg=C["text1"]))
        else:
            self._stop_btn.config(fg=C["text3"], cursor="arrow")
            self._stop_btn.unbind("<Button-1>")
            self._stop_btn.unbind("<Enter>")
            self._stop_btn.unbind("<Leave>")

    def _on_stop(self) -> None:
        orpheus.stop()
        # Optimistic: update() self-corrects within one 2 s tick regardless.
        self._now_lbl.config(text="nothing playing", fg=C["text3"])
        self._set_stop_enabled(False)

    # ── file list ────────────────────────────────────────────────────────────

    def _reload_files(self, files: list) -> None:
        if files == self._files:
            return   # cheap no-op: fetch() runs every 2 s, most ticks unchanged
        self._files = files
        self._scroll.clear()
        if not files:
            tk.Label(self._scroll.inner, text="no files — use Echo to make one",
                     font=FONTS["small_italic"], fg=C["text3"], bg=C["card"],
                     anchor="w").pack(anchor="w", pady=(10, 0))
            return
        for row in files:
            name = row["name"]
            outer = tk.Frame(self._scroll.inner, bg=C["card"])
            outer.pack(fill="x", anchor="w", pady=(2, 0))

            title = tk.Label(outer, text=name, font=FONTS["body"],
                             fg=C["text1"], bg=C["card"], anchor="w",
                             justify="left", cursor="hand2")
            title.pack(fill="x", anchor="w")
            self._scroll.add_wrap_label(title, pad=8)
            title.bind("<Button-1>", lambda e, n=name: self._on_play(n))
            title.bind("<Enter>", lambda e, t=title: t.config(fg=C["amber"]))
            title.bind("<Leave>", lambda e, t=title: t.config(fg=C["text1"]))

            tk.Label(outer, text=self._fmt_dur(row.get("duration")),
                    font=FONTS["tiny"], fg=C["text3"], bg=C["card"],
                    anchor="w").pack(fill="x", anchor="w")

            tk.Frame(self._scroll.inner, bg=C["border"], height=1).pack(fill="x", pady=(3, 0))

    @staticmethod
    def _fmt_dur(seconds) -> str:
        """Seconds → 'm:ss' for the meta line; a dim placeholder for None
        (ffmpeg couldn't read it — the morpheus._fmt_dur precedent, adapted
        since an unknown duration here is worth surfacing, not hiding)."""
        if seconds is None:
            return "duration unknown"
        s = int(seconds)
        return f"{s // 60}:{s % 60:02d}"

    def _on_play(self, name: str) -> None:
        result = orpheus.play_file(name)
        if "error" in result:
            self._status.config(
                text=_ERROR_TEXT.get(result["error"], f"error: {result['error']}"),
                fg=C["red"])
            return
        self._status.config(text="")
        self._now_lbl.config(text=f"playing: {name}", fg=C["text1"])
        self._set_stop_enabled(True)

    # ── Kairos update contract ────────────────────────────────────────────────

    def update(self, data) -> None:
        """Called every 2 s by the `orpheus` Kairos worker on the main thread."""
        if not self._enabled:
            return
        if data is None:
            return   # stale tick — keep showing the last known state
        self._reload_files(data.get("files", []))
        if data.get("playing"):
            if self._now_lbl.cget("text") == "nothing playing":
                self._now_lbl.config(text="playing…", fg=C["text1"])
            self._set_stop_enabled(True)
        else:
            self._now_lbl.config(text="nothing playing", fg=C["text3"])
            self._set_stop_enabled(False)
