"""
home_panel.py — Felhaven Home: the Pythia chat
================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Draw the home view's chat with Pythia (the LLM oracle). Take a
             question, hand it to pythia.ask() on a WORKER THREAD, and print
             the answer. Owns tkinter, the worker/queue/history, the cancel
             signal, and the session tallies — no LLM logic, no networking;
             all of that lives in pythia.py.

Hestia:      The title/controls/readouts block at the top is HestiaBar
             (panels/hestia_panel.py) — a dumb draw-and-delegate widget. This
             panel owns everything it displays: the cancel Event, the worker
             thread, self._history, and the running session totals
             (Scraptoken Flux = tokens, Rites = tool calls). Hestia never
             touches any of that state itself.

Streaming:   pythia.ask() streams the answer token-by-token via an on_delta
             callback, so the transcript fills in live instead of appearing all
             at once after a long wait. The worker thread pushes each delta onto
             the queue; the main-thread poll appends it to the transcript.
             pythia.ask() also reports one on_event "stats" payload per call
             (tokens, wall time, tool counts, whether Stop cancelled it, and
             turn_messages — see History below) — the worker pushes that onto
             the same queue as a "stats" item.

History:     self._history is a list of TURNS, not a flat message list — each
             entry is one completed ask() call's turn_messages verbatim (the
             user message, every assistant/tool message its tool-calling loop
             produced, and the final assistant reply), windowed to the last
             _MAX_HISTORY_TURNS. _on_submit flattens it back to one list for
             the next ask() call's `history` param. This matters because
             gemma4:e2b (a 2B model) imitates its own transcript: storing only
             flattened user/assistant prose (the pre-2026-07-29 shape) hid
             every tool_call from the history the model sees, and after a few
             turns it started narrating intent ("I need to call the
             get_weather tool") instead of ever calling a tool again. Keeping
             the real exchange visible — and windowing it so the poison from
             an occasional bad turn ages out — is the fix (CONVENTIONS §12).

Epochs:      Every submit AND every Refresh bumps self._epoch. Each queued item
             (delta/stats/done) carries the epoch it was produced under, so if
             Refresh clears the transcript while an old answer is still
             in-flight, that old thread's late-arriving messages are silently
             dropped instead of leaking into the fresh transcript.

Stop/Refresh: Stop sets the cancel Event; pythia.ask() breaks its stream loop
             and closes the connection, so Ollama halts generation promptly.
             The resulting turn is marked cancelled (via the stats event) and
             is NOT persisted to self._history — only a dim "stopped" marker
             is printed. Refresh does the same cancel, then clears the
             transcript, history, and session tallies, and stops narration —
             it works even mid-answer.

Narration:   Each completed answer gets a "speak aloud" control plus a dim
             meta-line (tokens · wall time · tok/s · tool count, failures in
             red); if Calliope's auto-speak flag (set by the NarratorLamp
             inside Hestia) is on, the answer is read aloud AS IT STREAMS —
             completed sentences are handed to calliope.speak() the moment
             they form, so speech overlaps generation instead of waiting for
             the whole answer. The GUI decides *when* to speak (sentence
             boundaries, auto-speak flag); Calliope only turns that text into
             sound and plays chunks in order. Speech is best-effort:
             calliope.speak() never raises, so a missing model or a busy audio
             device is a silent no-op, never a broken chat.

Why a thread: LLM answers are slow (up to ~100s cold, seconds warm). tkinter
             is single-threaded, so calling pythia.ask() inline would freeze
             the whole window. Instead the worker thread does the slow call
             and drops its tokens on a queue; a persistent after()-poll on the
             main thread picks them up and touches the widgets. Same shape
             Morpheus's search box uses — the worker only ever touches the
             queue.

Not Kairos-registered: this panel is event-driven (you press Enter), not
             timed, so it runs its own poll rather than riding Kairos's tick.

Upstream:    felhaven.py (builds it as the "felhaven" view)
Downstream:  pythia.py (the oracle), calliope.py (narration),
             panels/hestia_panel.py (command surface), theme.py (colors)

Requires:    tkinter, threading, queue (stdlib); pythia, calliope.

V0.02: Audited 2026-07-26 — verified honest, no dead code. Added HomePanel's
pythia.ask worker thread + persistent after()-poll to CONVENTIONS §6's threading
exceptions, and narrowed §6 (a single one-shot UI-feedback after() — e.g. the
latency filler below — is now permitted). Themed right-click menu is still
copy-pasted 3× (echo/emanon_panel); consolidation is theme, session 29.
"""

import queue
import re
import threading
import tkinter as tk
from typing import Any, Optional

import calliope
import pythia
from theme import C, FONTS, PhosphorScroll, themed_menu, popup_menu
from panels.hestia_panel import HestiaBar

_POLL_MS = 60           # how often the main thread drains the worker's queue
_MAX_HISTORY_TURNS = 3  # how many past turns ride along in the next ask() call

# A sentence ends at .!? (optionally closed by a quote/bracket) FOLLOWED by
# whitespace — the trailing space proves the sentence is complete, so "3.5" or
# "v1.0" mid-stream is never spoken as a fragment.
_SENTENCE_END = re.compile(r'[.!?]["\')\]]?\s')


def _format_wall(ms: int) -> str:
    """Wall-clock latency for the meta-line: "2.1s" under a minute, "1m 04s" at
    or above it."""
    secs = ms / 1000
    if secs < 60:
        return f"{secs:.1f}s"
    m, s = divmod(int(round(secs)), 60)
    return f"{m}m {s:02d}s"


class HomePanel(tk.Frame):
    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, bg=C["bg"])

        # Queue events: ("delta", token, epoch), ("stats", stats_dict, epoch),
        # ("done", full_answer, epoch). See module docstring re: epochs.
        self._q: "queue.Queue[tuple[str, Any, int]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()
        self._epoch = 0
        # Running history, one entry per completed turn — each entry is that
        # turn's OWN full message list (pythia.ask()'s turn_messages: the user
        # message, every assistant/tool message its tool-calling loop produced,
        # and the final assistant reply), windowed to the last _MAX_HISTORY_TURNS.
        # Storing the real exchange (not just prose) matters for a small model:
        # it imitates its own transcript, so a history of flattened prose with
        # no visible tool_calls taught it to narrate ("I need to call...")
        # instead of actually calling tools — see CONVENTIONS §12.
        self._history: list[list[dict[str, Any]]] = []
        self._speak_seq = 0                           # unique tag id per speak button
        # Per-turn streaming state (reset on each submit):
        self._resp_started = False                    # printed the "pythia ›" prefix yet
        self._say_turn = False                        # narrate this turn aloud?
        self._say_buf = ""                            # unspoken tail awaiting a sentence end
        self._last_stats: Optional[dict[str, Any]] = None
        # Session totals (Hestia readouts) — reset on Refresh.
        self._sess_tokens = 0
        self._sess_tools = 0
        self._sess_fails = 0

        # ── Hestia — title, narration lamp, stop, refresh, session readouts ──
        self._hestia = HestiaBar(self, on_stop=self._on_stop, on_refresh=self._on_refresh)
        self._hestia.pack(fill="x")

        # ── Transcript ────────────────────────────────────────────────────────
        log_row = tk.Frame(self, bg=C["bg"])
        log_row.pack(fill="both", expand=True)
        self._log = tk.Text(
            log_row, bg=C["card"], fg=C["text1"], font=FONTS["body"],
            borderwidth=0, highlightthickness=1, highlightbackground=C["border"],
            wrap="word", state="disabled", padx=8, pady=6,
        )
        self._log.pack(side="left", fill="both", expand=True)
        scroll = PhosphorScroll(log_row, command=self._log.yview)
        scroll.pack(side="right", fill="y")
        self._log.configure(yscrollcommand=scroll.set)
        self._log.tag_configure("you",     foreground=C["text2"])
        self._log.tag_configure("pythia",  foreground=C["text1"])
        self._log.tag_configure("stopped", foreground=C["text3"])
        # Clickable "speak aloud" control appended after each answer.
        self._log.tag_configure("speak", foreground=C["text3"],
                                font=FONTS["small"])
        # Per-response meta-line (tokens · wall · tok/s · tools).
        self._log.tag_configure("meta",      foreground=C["text3"], font=FONTS["small"])
        self._log.tag_configure("meta_fail", foreground=C["red"],   font=FONTS["small"])
        # Right-click "Copy" — the Text stays state="disabled" (read-only) but
        # selection + clipboard copy work regardless of that state.
        self._log.bind("<Button-3>", self._show_log_menu)

        # ── Status line (only shows while a request is in flight) ──────────────
        self._status = tk.Label(self, text="", font=FONTS["small_italic"],
                                 fg=C["amber"], bg=C["bg"], anchor="w")
        self._status.pack(fill="x", pady=(4, 2))

        # ── Input row ─────────────────────────────────────────────────────────
        row = tk.Frame(self, bg=C["bg"])
        row.pack(fill="x")
        tk.Label(row, text=">", font=FONTS["body"],
                 fg=C["text1"], bg=C["bg"]).pack(side="left", padx=(0, 4))
        self._entry = tk.Entry(
            row, font=FONTS["body"], bg=C["card"], fg=C["text1"],
            insertbackground=C["text1"], highlightbackground=C["border"],
            highlightcolor=C["text1"], highlightthickness=1, borderwidth=0,
        )
        self._entry.pack(side="left", fill="x", expand=True, ipady=3)
        self._entry.bind("<Return>", self._on_submit)
        self._entry.bind("<Button-3>", self._show_entry_menu)

        self._print("Pythia is listening. Ask a question and press Enter.\n",
                    "pythia")

        self.after(_POLL_MS, self._poll)

    # ── Kairos contract (unused — this panel isn't registered/ticked) ─────────
    def update(self, data: Any) -> None:
        pass

    # ── Right-click context menus (transcript: copy; entry: cut/copy/paste) ───
    def _show_log_menu(self, event: "tk.Event[Any]") -> None:
        """Transcript is read-only, but selection + clipboard copy still work
        on a state='disabled' Text — only insert/delete are blocked."""
        has_selection = bool(self._log.tag_ranges("sel"))
        menu = themed_menu(self)
        menu.add_command(label="Copy", command=self._copy_log_selection,
                         state="normal" if has_selection else "disabled")
        menu.add_command(label="Select All", command=self._select_all_log)
        popup_menu(menu, event)

    def _copy_log_selection(self) -> None:
        try:
            text = self._log.get("sel.first", "sel.last")
        except tk.TclError:
            return                              # selection vanished between click and copy
        self.clipboard_clear()
        self.clipboard_append(text)

    def _select_all_log(self) -> None:
        self._log.tag_add("sel", "1.0", "end")

    def _show_entry_menu(self, event: "tk.Event[Any]") -> None:
        has_selection = self._entry.selection_present()
        menu = themed_menu(self)
        menu.add_command(label="Cut", command=lambda: self._entry.event_generate("<<Cut>>"),
                         state="normal" if has_selection else "disabled")
        menu.add_command(label="Copy", command=lambda: self._entry.event_generate("<<Copy>>"),
                         state="normal" if has_selection else "disabled")
        menu.add_command(label="Paste", command=lambda: self._entry.event_generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="Select All", command=lambda: self._entry.select_range(0, "end"))
        popup_menu(menu, event)

    # ── Transcript helpers (main thread only) ─────────────────────────────────
    def _print(self, text: str, tag: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", text, tag)
        self._log.configure(state="disabled")
        self._log.see("end")

    def _append_speak_button(self, answer: str) -> None:
        """Insert a clickable '▶ speak aloud' control bound to THIS answer. Each
        gets its own tag so the click knows which text to read; the shared
        'speak' tag carries the styling, and per-tag Enter/Leave give the row a
        hand cursor + brighten it on hover."""
        self._speak_seq += 1
        tag = f"speak{self._speak_seq}"
        self._log.configure(state="normal")
        self._log.insert("end", "   ▶ speak aloud\n", ("speak", tag))
        self._log.configure(state="disabled")
        self._log.tag_bind(tag, "<Button-1>", lambda _e, t=answer: self._speak(t))
        self._log.tag_bind(tag, "<Enter>",
                           lambda _e, tg=tag: self._hover_speak(tg, True))
        self._log.tag_bind(tag, "<Leave>",
                           lambda _e, tg=tag: self._hover_speak(tg, False))
        self._log.see("end")

    def _hover_speak(self, tag: str, on: bool) -> None:
        self._log.configure(cursor="hand2" if on else "")
        self._log.tag_configure(tag, foreground=C["text1"] if on else C["text3"])

    def _append_meta_line(self, stats: dict[str, Any]) -> None:
        """Render the per-response meta-line: tokens · wall time · tok/s · tool
        count, with a red failure count appended when any tool failed."""
        total_tokens = stats["prompt_tokens"] + stats["output_tokens"]
        wall = _format_wall(stats["wall_ms"])
        tokps = (stats["output_tokens"] / (stats["eval_ms"] / 1000)
                 if stats["eval_ms"] > 0 else 0.0)
        line = (f"   {total_tokens} tok · {wall} · {tokps:.1f} tok/s · "
                f"{stats['tools_called']} tools")
        self._log.configure(state="normal")
        self._log.insert("end", line, "meta")
        if stats["tools_failed"]:
            self._log.insert("end", f" · ⚠ {stats['tools_failed']} failed", "meta_fail")
        self._log.insert("end", "\n")
        self._log.configure(state="disabled")
        self._log.see("end")

    # ── Narration (calliope.speak is non-blocking — enqueues, never blocks) ───
    def _speak(self, text: str) -> None:
        """Speak `text` now, replacing whatever's playing (the speak button is an
        explicit 'read THIS' — barge in on any current/queued narration)."""
        calliope.stop()
        calliope.speak(text)

    def _drain_sentences(self) -> None:
        """Hand every COMPLETE sentence in the say-buffer to Calliope, keeping the
        trailing partial for the next token. Lets speech start mid-answer."""
        while True:
            m = _SENTENCE_END.search(self._say_buf)
            if not m:
                return
            sentence = self._say_buf[:m.end()].strip()
            self._say_buf = self._say_buf[m.end():]
            if sentence:
                calliope.speak(sentence)

    def _maybe_speak_filler(self, epoch: int, cancel: threading.Event) -> None:
        """Play the latency-masking filler only if this turn is still live — a
        Stop (cancel set) or a Refresh / new question (epoch advanced) during the
        filler delay suppresses it, so a filler never blips in after the user has
        already silenced narration."""
        if epoch == self._epoch and not cancel.is_set():
            calliope.speak_filler()

    # ── Submit → worker thread ────────────────────────────────────────────────
    def _on_submit(self, _event: "Optional[tk.Event[Any]]" = None) -> None:
        # Single-flight: ignore Enter while the oracle is still answering.
        if self._thread is not None and self._thread.is_alive():
            return
        msg = self._entry.get().strip()
        if not msg:
            return
        self._entry.delete(0, "end")
        self._print(f"you › {msg}\n", "you")
        self._status.config(text="consulting the oracle…")

        # New question → drop any narration still playing from the last answer.
        calliope.stop()
        # Reset per-turn streaming state. Snapshot auto-speak once so a mid-answer
        # lamp toggle doesn't start/stop narration halfway through.
        self._resp_started = False
        self._say_turn = calliope.auto_speak_enabled()
        self._say_buf = ""
        # Snapshot for the worker thread — flatten the windowed turns back into
        # one flat message list, which is exactly the shape ask()'s `history`
        # param expects.
        history = [m for turn in self._history for m in turn]
        self._epoch += 1
        epoch = self._epoch
        self._cancel = threading.Event()
        cancel = self._cancel
        self._hestia.set_running(True)

        # Mask the synth latency: after a short beat (so she doesn't react the
        # instant you hit Enter), play a pre-rendered filler while the real answer
        # generates — but only if THIS turn is still the live one when the beat
        # elapses. A Stop (sets `cancel`) or a Refresh / new question (bumps the
        # epoch) in that window suppresses it, so a filler never blips in after
        # the user already silenced narration. calliope.speak_filler() also
        # self-cancels once the real answer's audio has started, so a fast reply
        # is never played over.
        if self._say_turn:
            self.after(calliope.filler_delay_ms(),
                       lambda ep=epoch, cxl=cancel: self._maybe_speak_filler(ep, cxl))

        def _run(m: str = msg, h: "list[dict[str, Any]]" = history,
                 ep: int = epoch, cxl: threading.Event = cancel) -> None:
            answer = pythia.ask(
                m, h,
                on_delta=lambda p: self._q.put(("delta", p, ep)),
                on_event=lambda ev: self._q.put(("stats", ev, ep)),
                cancel=cxl,
            )
            self._q.put(("done", answer, ep))

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    # ── Stop / Refresh (Hestia controls) ──────────────────────────────────────
    def _on_stop(self) -> None:
        # Cancel any in-flight ask() (a no-op if the answer already finished
        # generating) AND silence narration directly — audio playback lags
        # behind text generation, so a still-talking answer often has no
        # in-flight thread left for `_cancel` to reach. calliope.stop() is
        # the actual "stop the audible response" action; it's a harmless
        # no-op when nothing is playing.
        self._cancel.set()
        calliope.stop()

    def _on_refresh(self) -> None:
        self._cancel.set()                     # cancel anything in flight
        self._epoch += 1                       # invalidate that thread's late messages
        calliope.stop()
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")
        self._history.clear()
        self._status.config(text="")
        self._resp_started = False
        self._say_buf = ""
        self._last_stats = None
        self._sess_tokens = 0
        self._sess_tools = 0
        self._sess_fails = 0
        self._hestia.set_flux(0)
        self._hestia.set_rites(0, 0)
        self._hestia.set_running(False)
        self._print("Pythia is listening. Ask a question and press Enter.\n",
                    "pythia")

    # ── Poll: drain queued events onto the transcript (main thread) ──────────
    def _poll(self) -> None:
        if not self.winfo_exists():            # window closed mid-flight
            return
        while True:
            try:
                kind, payload, epoch = self._q.get_nowait()
            except queue.Empty:
                break
            if epoch != self._epoch:
                continue                        # stale — from a Refresh-away turn
            if kind == "delta":
                self._on_delta(payload)
            elif kind == "stats":
                self._on_stats(payload)
            else:                               # "done"
                self._on_done(payload)
        self.after(_POLL_MS, self._poll)

    def _on_delta(self, piece: str) -> None:
        """A streamed token arrived: show it, and buffer it for narration."""
        if not self._resp_started:
            self._status.config(text="")
            self._print("pythia › ", "pythia")
            self._resp_started = True
        self._print(piece, "pythia")
        if self._say_turn:
            self._say_buf += piece
            self._drain_sentences()

    def _on_stats(self, stats: dict[str, Any]) -> None:
        """One stats event per ask() call — cache it for _on_done's meta-line,
        and roll it into the session totals Hestia displays."""
        self._last_stats = stats
        self._sess_tokens += stats["prompt_tokens"] + stats["output_tokens"]
        self._sess_tools += stats["tools_called"]
        self._sess_fails += stats["tools_failed"]
        self._hestia.set_flux(self._sess_tokens)
        self._hestia.set_rites(self._sess_tools, self._sess_fails)

    def _on_done(self, answer: str) -> None:
        """The answer is complete (or was cancelled): finalize the transcript,
        flush any unspoken tail, and persist the turn — unless it was stopped."""
        self._hestia.set_running(False)
        cancelled = bool(self._last_stats and self._last_stats.get("cancelled"))

        if cancelled:
            calliope.stop()
            self._say_buf = ""
            self._status.config(text="")
            if not self._resp_started:
                self._print("pythia › ", "pythia")
            self._print("⏹ stopped\n\n", "stopped")
            return

        self._status.config(text="")
        if not self._resp_started:
            # No tokens streamed (an error string, or an empty answer) — print it.
            self._print(f"pythia › {answer}", "pythia")
            if self._say_turn and answer.strip():
                calliope.speak(answer)
        elif self._say_turn and self._say_buf.strip():
            calliope.speak(self._say_buf.strip())   # speak the last partial sentence
        self._say_buf = ""
        if self._say_turn:
            # All text is in — let the prebuffer play a short answer without waiting.
            calliope.end_turn()

        self._print("\n", "pythia")
        if self._last_stats is not None:
            self._append_meta_line(self._last_stats)
        self._append_speak_button(answer)
        self._print("\n", "pythia")

        # Persist the turn's REAL exchange (user msg + every assistant/tool
        # message ask()'s loop produced), not just flattened prose — see the
        # comment on self._history's declaration. Then keep only the last
        # _MAX_HISTORY_TURNS turns so history stays bounded.
        turn = (self._last_stats or {}).get("turn_messages") or []
        if turn:
            self._history.append(list(turn))
            del self._history[:-_MAX_HISTORY_TURNS]
