"""
home_panel.py — Felhaven Home: the Pythia chat
================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Draw the home view's chat with Pythia (the LLM oracle). Take a
             question, hand it to pythia.ask() on a WORKER THREAD, and print
             the answer. Owns tkinter only — no LLM logic, no networking; all
             of that lives in pythia.py.

Streaming:   pythia.ask() streams the answer token-by-token via an on_delta
             callback, so the transcript fills in live instead of appearing all
             at once after a long wait. The worker thread pushes each delta onto
             the queue; the main-thread poll appends it to the transcript.

Narration:   Each answer gets a "speak aloud" control; if Calliope's auto-speak
             flag (set by the header NarratorLamp) is on, the answer is read
             aloud AS IT STREAMS — completed sentences are handed to
             calliope.speak() the moment they form, so speech overlaps
             generation instead of waiting for the whole answer. The GUI decides
             *when* to speak (sentence boundaries, auto-speak flag); Calliope
             only turns that text into sound and plays chunks in order. Speech is
             best-effort: calliope.speak() never raises, so a missing model or a
             busy audio device is a silent no-op, never a broken chat.

Why a thread: LLM answers are slow (up to ~100s cold, seconds warm). tkinter
             is single-threaded, so calling pythia.ask() inline would freeze
             the whole window. Instead the worker thread does the slow call
             and drops its tokens on a queue; a short after()-poll on the main
             thread picks them up and touches the widgets. Same shape Morpheus's
             search box uses — the worker only ever touches the queue.

Not Kairos-registered: this panel is event-driven (you press Enter), not
             timed, so it runs its own poll rather than riding Kairos's tick.

Upstream:    felhaven.py (builds it as the "felhaven" view)
Downstream:  pythia.py (the oracle), calliope.py (narration), theme.py (colors)

Requires:    tkinter, threading, queue (stdlib); pythia, calliope.
"""

import queue
import re
import threading
import tkinter as tk
from typing import Any, Optional

import calliope
import pythia
from theme import C, FONTS

_POLL_MS = 60           # how often the main thread drains the worker's tokens

# A sentence ends at .!? (optionally closed by a quote/bracket) FOLLOWED by
# whitespace — the trailing space proves the sentence is complete, so "3.5" or
# "v1.0" mid-stream is never spoken as a fragment.
_SENTENCE_END = re.compile(r'[.!?]["\')\]]?\s')


class HomePanel(tk.Frame):
    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, bg=C["bg"])

        # Queue events: ("delta", token) as the answer streams, then ("done",
        # full_answer) when it's complete.
        self._q: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._history: list[dict[str, Any]] = []     # running {role, content} turns
        self._speak_seq = 0                           # unique tag id per speak button
        # Per-turn streaming state (reset on each submit):
        self._pending_user = ""                       # user msg awaiting its answer
        self._resp_started = False                    # printed the "pythia ›" prefix yet
        self._say_turn = False                        # narrate this turn aloud?
        self._say_buf = ""                            # unspoken tail awaiting a sentence end

        # ── Title ─────────────────────────────────────────────────────────────
        tk.Label(self, text="PYTHIA", font=FONTS["title"],
                 fg=C["text1"], bg=C["bg"]).pack(anchor="w", pady=(0, 2))
        tk.Label(self, text="ask the oracle", font=FONTS["subtitle"],
                 fg=C["text3"], bg=C["bg"]).pack(anchor="w", pady=(0, 8))

        # ── Transcript ────────────────────────────────────────────────────────
        self._log = tk.Text(
            self, bg=C["card"], fg=C["text1"], font=FONTS["body"],
            borderwidth=0, highlightthickness=1, highlightbackground=C["border"],
            wrap="word", state="disabled", padx=8, pady=6,
        )
        self._log.pack(fill="both", expand=True)
        self._log.tag_configure("you",    foreground=C["text2"])
        self._log.tag_configure("pythia", foreground=C["text1"])
        # Clickable "speak aloud" control appended after each answer.
        self._log.tag_configure("speak", foreground=C["text3"],
                                font=FONTS["small"])

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

        self._print("Pythia is listening. Ask a question and press Enter.\n",
                    "pythia")

    # ── Kairos contract (unused — this panel isn't registered/ticked) ─────────
    def update(self, data: Any) -> None:
        pass

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
        self._pending_user = msg
        self._resp_started = False
        self._say_turn = calliope.auto_speak_enabled()
        self._say_buf = ""
        # Mask the synth latency: after a short beat (so she doesn't react the
        # instant you hit Enter), play a pre-rendered filler while the real answer
        # generates. calliope.speak_filler() self-cancels if the real answer's
        # audio has already started, so a fast reply is never played over.
        if self._say_turn:
            self.after(calliope.filler_delay_ms(), calliope.speak_filler)

        history = list(self._history)          # snapshot for the worker thread

        def _run(m: str = msg, h: "list[dict[str, Any]]" = history) -> None:
            answer = pythia.ask(m, h, on_delta=lambda p: self._q.put(("delta", p)))
            self._q.put(("done", answer))

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        self.after(_POLL_MS, self._poll)

    # ── Poll: drain streamed tokens onto the transcript (main thread) ─────────
    def _poll(self) -> None:
        if not self.winfo_exists():            # window closed mid-flight
            return
        done = False
        while True:
            try:
                kind, text = self._q.get_nowait()
            except queue.Empty:
                break
            if kind == "delta":
                self._on_delta(text)
            else:                              # "done"
                self._on_done(text)
                done = True
        if not done:
            self.after(_POLL_MS, self._poll)   # more tokens coming — check again

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

    def _on_done(self, answer: str) -> None:
        """The answer is complete: finalize the transcript, flush any unspoken
        tail, add the speak button, and persist the turn."""
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
        self._append_speak_button(answer)
        self._print("\n", "pythia")

        self._history.append({"role": "user", "content": self._pending_user})
        self._history.append({"role": "assistant", "content": answer})
