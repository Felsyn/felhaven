"""
Orpheus panel — play back a recorded briefing from local_audio/.

Job:         Play back a recorded briefing from local_audio/.

OrpheusPanel is a bare tk.Frame tab body inside VoxArrayPanel (the ORPHEUS
tab), the same shape as MorpheusPanel and EchoPanel. Unlike Echo, Orpheus IS
Kairos-registered (the "orpheus" worker, 2 s) — update(data) drives both the
transport (flips back to idle when a briefing finishes on its own, since
Harmonia's sd.wait() blocks inside its own thread and the panel has no other
way to see that) and the file list (no watcher, no manual refresh button —
fetch() returns the local_audio/ listing on the same tick).

Top to bottom inside self:
    Transport row  — now-playing label + ⏹ (enabled only while playing).
    File list      — one row per local_audio/ file (name + its duration,
                     "m:ss" or "duration unknown"); click the name to play it,
                     or the ✕ twice to send it to the Recycle Bin. Play and
                     remove are two separate widgets in the same row, so
                     clicking the title can never delete (MorpheusPanel's
                     playlist-row precedent, glyph and all).

If ffmpeg isn't found, the tab shows a placeholder and stays inert — the
dashboard never crashes over a missing binary (Morpheus/Echo precedent).

─────────────────────────────────────────────────────────────────────────────
DECODE THREADING + DRAIN — the EchoPanel shape (CONVENTIONS §6 names both).

  orpheus.play_file() decodes the whole file before anything plays, and that
  takes seconds for Echo's longer output (measured 2026-11-21: 3.2 s for a
  25-minute file, 1.1 s for a 9-minute one, 0.3 s for 2 minutes). Called from
  the click handler, as it was until then, it froze all of Felhaven for that
  long. So a click starts one daemon thread whose ONLY shared touch is
  self._play_q.put(...), and a bounded after() chain drains the queue and
  stops itself once the result lands. The status line says "decoding …"
  meanwhile; a second click while one decodes is a no-op (single-flight), and
  ⏹ stays dim until something plays, so a decode can't be cancelled. The
  chain is winfo_exists()-guarded and cancelled in destroy().
─────────────────────────────────────────────────────────────────────────────

Because Harmonia has no channels (see harmonia.py), "playing" here reflects
ANY sound coming out of the speaker, not only a briefing Orpheus started —
if Calliope is mid-answer, this tab will show "playing" too, and its ⏹ will
stop that. That cross-talk is a deliberate consequence of the one-stream
design, not a bug in this panel.

Upstream:    panels/vox_array_panel.py (builds it as the ORPHEUS tab),
             felhaven.py (registers it with Kairos's `orpheus` worker,
             tools.orpheus.fetch every 2 s).
Downstream:  tools/orpheus.py (fetch's data; play_file, stop, delete_file,
             available), theme.py, tools/virgil.py and
             panels/virgil_tooltip.py (the tips).

2026-07-25:  walkthrough audit — verified §6-clean (the ffmpeg decode ran
             synchronously from the click handler by design, so no
             after()/thread exception applied); _ERROR_TEXT stays in
             lockstep with play_file()'s three codes. No code change.

2026-08-06:  the two-click remove makes one line of that audit stale rather
             than wrong — _ERROR_TEXT is now in lockstep with delete_file()'s
             codes too. Still §6-clean: the confirm window and the
             status-flash auto-clear are single one-shot after() timers for
             transient feedback, which §6 permits by name ("a status line
             that self-clears, a glyph that resets after a beat") since the
             2026-07-26 narrowing.

2026-08-10:  both timers now go through _after_once() and are cancelled in a
             destroy() override. §6 still permits them; this is only teardown
             hygiene — an uncancelled timer outlives the Tcl command it names
             and makes Tcl print `invalid command name` on stderr.

V0.03:       Walked through 2026-11-21 (the V0.03 walkthrough, CONVENTIONS
             §3/§12). Built 2026-07-17. Checked: _ERROR_TEXT against every
             code play_file() and delete_file() return (now a test); the
             tips' bindtag survives the stop button's rebinds. Changed: a
             click decodes on one daemon thread with a bounded drain chain
             (the owner's pick; a 25-minute file froze Felhaven 3.2 s), and
             §6 names the exception; the stale "No thread, no queue" and
             "per the handoff" lines went; Upstream/Downstream added.
"""

import queue
import threading
import tkinter as tk
from typing import Any

from panels import virgil_tooltip
from theme import C, FONTS, PhosphorScroll

from tools import orpheus, virgil

# How often the drain chain polls the decode result while one runs.
_DRAIN_MS = 100

_NO_BIN_MSG = "ffmpeg not found — drop ffmpeg.exe in bin\\ or install to PATH"
# bin\ffmpeg.exe is there, but aegis refused it (tools/aegis.py).
_UNVERIFIED_MSG = ("ffmpeg in bin\\ isn't recorded, or doesn't match what was "
                   "recorded, so Felhaven won't run it. If it's the copy you "
                   "meant, record it: SETUP.md section 5.")

# Human-readable status text per Orpheus error code — every code play_file()
# and delete_file() can return (bad_name is shared by both).
_ERROR_TEXT = {
    "bad_name": "invalid filename",
    "ffmpeg_unavailable": "ffmpeg not found",
    "decode_failed": "could not decode that file — check the log",
    "delete_failed": "could not delete that file — check the log",
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

    # Hover tooltips (Virgil), each a glossary entry (CONVENTIONS §7). _TIPS
    # names the widgets built once, in self.tip_targets; _ROW_TIPS names the
    # glyph each file row builds. The stop button binds and unbinds its own
    # handlers as it lights and dims; the tip rides a bindtag and stays.
    _TIPS = {
        "stop": virgil.term("transport_stop"),
    }
    _ROW_TIPS = {
        "file_remove": virgil.term("file_remove"),
    }

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        avail = orpheus.available()
        self._enabled = bool(avail["ffmpeg"])
        self._no_bin_text = _UNVERIFIED_MSG if avail.get("problem") else _NO_BIN_MSG
        # None (not []) so the very first update() always renders — an empty
        # local_audio/ must still show the "no files" hint, not skip as a no-op.
        self._files: "list | None" = None

        # Pending one-shot after() ids (see _after_once / destroy).
        self._pending_after: "set[str]" = set()

        # The decode thread's hand-off (module docstring, DECODE THREADING).
        self._play_q: "queue.Queue[tuple[str, dict[str, Any]]]" = queue.Queue()
        self._decoding = False
        self._drain_after_id: "str | None" = None

        self._build_transport()
        if self._enabled:
            self._scroll = _ScrollFrame(self)
            self._scroll.pack(fill="both", expand=True, pady=(4, 0))
        else:
            tk.Label(self, text=self._no_bin_text, font=FONTS["small_italic"],
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
        self.tip_targets: dict[str, tk.Misc] = {"stop": self._stop_btn}
        virgil_tooltip.attach(self._stop_btn, self._TIPS["stop"])

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

            # ── Remove glyph — pack BEFORE the title so it lands on the right
            # (the ⏹ precedent in _build_transport). Captures `name`, not an
            # index: Orpheus deletes by filename, so a re-render between the
            # arming click and the confirming one can't retarget the row.
            glyph = tk.Label(
                outer, text="✕", font=FONTS["small"],
                fg=C["text3"], bg=C["card"], cursor="hand2", padx=4,
            )
            glyph._armed = False   # two-click confirm state (see _on_remove_file)
            glyph.pack(side="right")
            glyph.bind("<Enter>", lambda e, g=glyph: self._glyph_hover(g, C["text1"]))
            glyph.bind("<Leave>", lambda e, g=glyph: self._glyph_hover(g, C["text3"]))
            glyph.bind("<Button-1>",
                       lambda e, n=name, g=glyph: self._on_remove_file(n, g))
            virgil_tooltip.attach(glyph, self._ROW_TIPS["file_remove"])

            title = tk.Label(outer, text=name, font=FONTS["body"],
                             fg=C["text1"], bg=C["card"], anchor="w",
                             justify="left", cursor="hand2")
            title.pack(fill="x", anchor="w")
            self._scroll.add_wrap_label(title, pad=36)   # 8 (PhosphorScroll lane) + 28 (✕ glyph)
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

    # ── play (daemon decode thread + bounded drain chain) ────────────────────

    def _on_play(self, name: str) -> None:
        """Start decoding `name` off the UI thread. Single-flight: a click
        while one decodes is a no-op, not a queued second job."""
        if self._decoding:
            return
        self._decoding = True
        self._status.config(text=f"decoding {name} …", fg=C["text3"])
        # The thread's only shared touch is the queue put — no Tk.
        threading.Thread(
            target=lambda n=name: self._play_q.put((n, orpheus.play_file(n))),
            daemon=True,
        ).start()
        self._drain_after_id = self.after(_DRAIN_MS, self._drain)

    def _drain(self) -> None:
        """Bounded, self-terminating poll of the decode result: re-arms only
        while the queue is empty, stops once the result arrives."""
        if not self.winfo_exists():
            return
        try:
            name, result = self._play_q.get_nowait()
        except queue.Empty:
            self._drain_after_id = self.after(_DRAIN_MS, self._drain)
            return
        self._drain_after_id = None
        self._decoding = False
        self._deliver_play(name, result)

    def _deliver_play(self, name: str, result: "dict[str, Any]") -> None:
        if "error" in result:
            self._status.config(
                text=_ERROR_TEXT.get(result["error"], f"error: {result['error']}"),
                fg=C["red"])
            return
        self._status.config(text="")
        self._now_lbl.config(text=f"playing: {name}", fg=C["text1"])
        self._set_stop_enabled(True)

    # ── one-shot timers (the MorpheusPanel _after_once pattern) ──────────────

    def _after_once(self, ms: int, fn) -> None:
        """Schedule a one-shot UI-feedback timer, remembering its id so destroy()
        can cancel it.

        A set, not a single `_xxx_after_id` attribute like EchoPanel's: several
        of these can be outstanding at the same time (each armed remove glyph
        owns its own confirm window), so a single slot would forget all but the
        last and leave the rest to fire into a dead widget. The callbacks'
        winfo_exists() guards do not help there — Tk deletes a widget's Tcl
        commands as it destroys the widget, so a still-scheduled after script
        fails with `invalid command name` before Python is reached."""
        def run() -> None:
            self._pending_after.discard(after_id)
            fn()

        after_id = self.after(ms, run)
        self._pending_after.add(after_id)

    def destroy(self) -> None:
        """Cancel every pending one-shot timer, and the decode drain, before
        teardown. A decode still in flight is left to its daemon thread; its
        result goes to a queue nobody reads."""
        for after_id in list(self._pending_after):
            self.after_cancel(after_id)
        self._pending_after.clear()
        if self._drain_after_id is not None:
            self.after_cancel(self._drain_after_id)
            self._drain_after_id = None
        super().destroy()

    # ── remove (two-click confirm — the MorpheusPanel playlist-row pattern) ───

    def _glyph_hover(self, glyph: tk.Label, color: str) -> None:
        """Hover highlight for a remove glyph — suppressed while it is armed, so
        the amber 'remove?' prompt stays put when the mouse moves off it."""
        if not glyph._armed:
            glyph.config(fg=color)

    def _on_remove_file(self, name: str, glyph: tk.Label) -> None:
        """Two-click confirm. First click arms the glyph ('remove?'); a second
        click within 2.5 s recycles the file, otherwise it auto-cancels. A
        single stray click only ever arms — it can never delete."""
        if not glyph._armed:
            glyph._armed = True
            glyph.config(text="remove?", fg=C["amber"])
            self._after_once(2500, lambda g=glyph: self._disarm_remove(g))
            return

        result = orpheus.delete_file(name)
        if "error" in result:
            glyph._armed = False
            glyph.config(text="✕", fg=C["text3"])
            self._flash(_ERROR_TEXT.get(result["error"], f"error: {result['error']}"),
                        ok=False)
            return

        # Re-render from local state rather than re-fetching: the next Kairos
        # tick (≤ 2 s) delivers the real listing, which now equals this one, so
        # _reload_files's equality guard makes it a no-op.
        self._reload_files([r for r in (self._files or []) if r["name"] != name])
        self._flash("removed", ok=True)

    def _disarm_remove(self, glyph: tk.Label) -> None:
        """Revert an armed remove glyph after the confirm window lapses. Guards
        winfo_exists() because a confirmed delete destroys the glyph before this
        scheduled callback fires."""
        if glyph.winfo_exists() and glyph._armed:
            glyph._armed = False
            glyph.config(text="✕", fg=C["text3"])

    def _flash(self, msg: str, ok: bool) -> None:
        """Transient status message under the transport row. Clears after 2.5 s
        — unlike _on_play's error text, which is a state worth leaving up.

        Diverges from MorpheusPanel._pl_flash by clearing only if the message is
        still ours: this label is shared with _on_play, where Morpheus's flash
        label is its own. An unconditional clear would wipe a play error raised
        inside a previous flash's window."""
        self._status.config(text=msg, fg=C["text1"] if ok else C["amber"])
        self._after_once(2500, lambda m=msg: self._clear_flash(m))

    def _clear_flash(self, msg: str) -> None:
        """Blank the status line, but only if it still shows `msg` (see _flash)."""
        if self._status.cget("text") == msg:
            self._status.config(text="")

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
