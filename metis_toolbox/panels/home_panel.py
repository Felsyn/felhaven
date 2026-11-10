"""
home_panel.py — Felhaven Home: the Pythia chat
================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Draw the home view's chat with Pythia (the LLM oracle). Take a
             question, hand it to pythia.ask() on a WORKER THREAD, and print
             the answer. Owns tkinter, the worker/queue/history, the cancel
             signal, and the session tallies — no LLM logic, no networking;
             all of that lives in pythia.py.

Hestia:      The title/face/controls block at the top is HestiaBar
             (panels/hestia_panel.py) — a dumb draw-and-delegate widget. This
             panel owns everything it displays: the cancel Event, the worker
             thread, self._history, and the running session totals
             (Scraptoken Flux = tokens, Rites = tool calls). Hestia never
             touches any of that state itself. The two readouts sit on this
             panel's own status row, bottom-right, opposite the in-flight
             status — near the numbers that compute them and clear of the
             controls.

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
             It also halts the TOOLS: any tool call the model had queued but
             that had not been dispatched yet is skipped and recorded not_run,
             so Stop stops the whole turn rather than just its narrator.
             The resulting turn is marked cancelled (via the stats event) and
             is NOT persisted to self._history — only a dim "stopped" marker
             is printed. Refresh does the same cancel (so it abandons pending
             tools too), then clears the transcript, history, session tallies
             and the rite ring, and stops narration — it works even mid-answer.

Rite Log:    The meta-line's tool counts are clickable; each opens a RiteLog
             window on that turn's diagnostic records. This panel stores what
             pythia handed it and passes it straight through — it never parses
             or reshapes a record. The ring is bounded, so an old meta-line
             outlives its records and opens the window's expired view.

Auto-DJ:     Awen decides WHAT to say and when it is ready; this panel decides
             when it is heard and prints it — the same split narration already
             follows. A DJ line lands on its own amber `dj ›` line and never
             enters self._history (see *History* for why that window has to
             stay the operator's own conversation). It is spoken whether or not
             Vox Response is on, the AmmitAlarm precedent, and it does NOT
             barge in: Awen already waited for quiet, so calling calliope.stop()
             first would undo that. Vox End still silences it, because Vox End
             IS calliope.stop().

Verbatim:    Some tool output must be shown EXACTLY — today, a Daedalus
             module docstring. pythia.ask() hands it to on_verbatim instead
             of the model, and it lands here as its own block: a
             `daedalus › <module> · <path>` header line, then the text as
             given, untrimmed, in its own colour. The header is a line of its
             own because a prefix on the docstring's first line would push it
             right and misalign the `====` underline beneath it. The block is
             never spoken and never enters self._history: the turn Pythia
             stores holds only that the text was shown, and the short line she
             ends such a turn on is what Vox Response reads aloud
             (CONVENTIONS §12).

Confirm:     A tool that changes files (write_document, edit_document) runs
             only after an Allow click. pythia.ask() calls on_confirm on the
             worker thread, which queues the question and waits. The main
             thread prints the tool, its arguments and the full content
             (amber header, verbatim-colour content), then shows an
             Allow/Deny bar above the input row. Deny, Stop, Refresh,
             closing the window, or _CONFIRM_WAIT_S without an answer all
             mean no (CONVENTIONS §12, 2026-09-30).

Narration:   Each completed answer gets a "speak aloud" control plus a dim
             meta-line (tokens · wall time · tok/s · tool count, failures in
             red); if Calliope's auto-speak flag (set by Vox Response
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
             queue. The poll's after-id is kept on the panel and cancelled in
             destroy() — see that method for why winfo_exists() alone isn't
             enough to end the chain.

Not Kairos-registered (mostly): the chat itself is event-driven (you press
             Enter), not timed, so it runs its own poll rather than riding
             Kairos's tick. There are two exceptions, and they attach
             differently on purpose.

             AmmitBadge (panels/horai_panel.py) is a display-only Ammit status
             readout on the status row, next to Scraptoken Flux/Rites. It has
             no state of its own worth ticking on this panel's 60 ms poll, so
             this PANEL registers under "horai" in felhaven.py purely to
             refresh that one badge on Kairos's 1 s tick; see update() below.
             Ammit's alarm (popup + spoken announcement) is NOT owned here —
             that lives in AmmitAlarm on HoraiPanel, which persists across view
             switches and fires regardless of which sidebar view is showing.

             DjRider is Auto-DJ's line (awen.py), and the RIDER registers, not
             the panel — because update() is already spoken for by the badge
             above and Kairos passes it the data with no worker name attached.
             Two registrations on one update() could only be told apart by
             sniffing the payload's shape, which would silently depend on
             horai never growing a key of the same name. See DjRider.

             CerberusLamp (panels/cerberus_panel.py) sits first in the same
             readouts row: dim sealed, amber open, red on a tamper alarm. The
             LAMP registers itself under Kairos's "cerberus" worker, for the
             same reason the rider does.

Face:        Hestia's wireframe face is told what Pythia is doing from that
             same 60 ms drain chain — no second timer, the _tick_status trick.
             The mapping is derived, never stored: audible → speaking,
             thinking → a live worker or a warming model, otherwise idle.

Upstream:    felhaven.py (builds it as the "felhaven" view, registers it under
             Kairos's "horai" for the Ammit badge — see above)
Downstream:  pythia.py (the oracle, and via on_verbatim the text some
             tools show exactly), calliope.py (narration),
             awen.py (Auto-DJ's flag, via HestiaBar's vox row),
             panels/hestia_panel.py (title, face, controls),
             panels/horai_panel.py (AmmitBadge; transitively tools/ammit.py),
             theme.py (colors)

Requires:    tkinter, threading, queue (stdlib); pythia, calliope.

V0.02: Audited 2026-07-26 — verified honest, no dead code. Added HomePanel's
pythia.ask worker thread + persistent after()-poll to CONVENTIONS §6's threading
exceptions, and narrowed §6 (a single one-shot UI-feedback after() is now
permitted). Themed right-click menu is still
copy-pasted 3× (echo/emanon_panel); consolidation is theme, session 29.

2026-08-10: the poll chain stores its after-id and is cancelled in a destroy()
override (the EchoPanel precedent). Before this, the suite printed ~53 Tcl
`invalid command name "..._poll"` lines — the chain outlived the widget whose
Tcl command it named. §6 updated to match. (A latency-filler timer was cancelled
alongside it until 2026-09-07, when the pre-rendered fillers were removed.)
"""

import logging
import queue
import re
import threading
import time
import tkinter as tk
from collections import OrderedDict
from typing import Any, Callable, Optional

import awen
import calliope
import harmonia
import pythia
from panels.queue_panel import QueueBox
from tools import morpheus
from theme import C, FONTS, PhosphorScroll, themed_menu, popup_menu
from panels.cerberus_panel import CerberusLamp
from panels.hestia_panel import HestiaBar
from panels.horai_panel import AmmitBadge
from panels.rite_panel import RiteLog

log = logging.getLogger("METIS.home_panel")

_POLL_MS = 60           # how often the main thread drains the worker's queue
_MAX_HISTORY_TURNS = 3  # how many past turns ride along in the next ask() call
# How many turns of tool-call diagnostics stay openable from the transcript. The
# transcript itself is unbounded, so older meta-lines outlive their records and
# open the Rite Log's expired view — see _open_rites().
_MAX_RITE_TURNS = 20
# Quiet period between reports of a FAILING poll tick. The chain runs at
# _POLL_MS (16 Hz), so logging every failure would write a traceback 16 times a
# second — measured at 3.7 KB/s, which rotates felhaven.log's 1 MB x 3 in under
# twenty minutes and destroys the first traceback, the only one worth reading.
# Felhaven is left running for days at a time, so an unattended fault must cost
# a trickle, not a flood.
_POLL_FAIL_QUIET = 60.0   # seconds
# How long a file-change question waits for Allow or Deny before it counts as
# Deny. Long enough to read a note through; short enough that a question
# nobody saw doesn't hold the turn open all night.
_CONFIRM_WAIT_S = 600.0

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


class _ConfirmReply:
    """One file-change question's answer, carried from the main thread (the
    Allow/Deny click) to the worker waiting inside pythia.ask().

    The main thread writes `allowed` and THEN sets `done`; the worker reads
    `allowed` only once `done` is set, and an Event guarantees that order —
    the same main→worker crossing the `cancel` Event already makes (§6). The
    first answer is final, so a late click can't flip a question already
    answered by Stop or the timeout."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.allowed = False

    def answer(self, allowed: bool) -> None:
        if not self.done.is_set():
            self.allowed = allowed
            self.done.set()


class DjRider:
    """Kairos rider for Awen's payload: print one DJ line and speak it.

    A DEDICATED rider rather than a branch inside HomePanel.update(), and the
    reason is structural. Kairos hands update() the data and never the worker
    name, and HomePanel is already registered under "horai" for the Ammit
    badge — so telling the two apart would mean sniffing the payload's shape,
    which silently depends on horai never growing a key with the same name.
    Registering the sub-object instead is the AmmitBadge / Moderati precedent
    (felhaven.py registers `moderati.hephaestus`, not the view).

    It holds the panel, not widgets: everything it does goes through one method,
    so the transcript's formatting stays in one place.

    Runs on the MAIN thread — Kairos drains its queue there — which is what
    makes touching Tk and Calliope from here legal at all.
    """

    def __init__(self, panel: "HomePanel") -> None:
        self._panel = panel

    def update(self, data: Any) -> None:
        """Kairos contract. `data` is awen.fetch()'s payload, or None if it
        somehow failed — and every update() must survive None (§6)."""
        if not isinstance(data, dict):
            return
        text = data.get("say")
        if text:
            self._panel.dj_says(str(text))


class HomePanel(tk.Frame):
    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, bg=C["bg"])

        # Queue events: ("delta", token, epoch), ("stats", stats_dict, epoch),
        # ("verbatim", payload, epoch), ("confirm", (request, reply), epoch),
        # ("confirm_expired", reply, epoch), ("done", full_answer, epoch). See
        # module docstring re: epochs.
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
        self._rite_seq = 0                            # unique tag id per meta-line
        # Tool-call diagnostics per turn, keyed by the _rite_seq in its tags.
        # Bounded: the oldest turn is evicted once _MAX_RITE_TURNS is exceeded.
        self._rites: "OrderedDict[int, list[dict[str, Any]]]" = OrderedDict()
        # Per-turn streaming state (reset on each submit):
        self._resp_started = False                    # printed the "pythia ›" prefix yet
        self._say_turn = False                        # narrate this turn aloud?
        self._say_buf = ""                            # unspoken tail awaiting a sentence end
        self._last_stats: Optional[dict[str, Any]] = None
        # The pending after() id, so destroy() can cancel it (see destroy()).
        self._poll_after_id: Optional[str] = None
        # Whether the status line is currently showing the warmup notice. Kept
        # so _poll() only writes on a transition — otherwise it would repaint
        # every 60 ms and could clobber a message set by the request path.
        # What _tick_status last wrote, so it only repaints on a real change.
        # Was _showing_warmup (a bool) until Auto-DJ became a third claimant on
        # the same label and the state stopped being two-valued.
        self._status_shown = ""
        # True while the status line is showing the Auto-DJ queue count, which
        # is the ONE thing on that label you can click. Kept because four other
        # sites write the label directly and every one of them has to be able
        # to hand the affordance back.
        self._status_clickable = False
        # The one queue window, or None. One rather than RiteLog's many: those
        # each show a different frozen turn, and every one of these would show
        # the same live list and need its own refresh.
        self._dj_queue_box = None
        # The queue revision the open box was last drawn from.
        self._queue_drawn = -1
        # Failing-tick bookkeeping (see _note_poll_failure).
        self._poll_fails = 0
        self._poll_fail_reported = 0.0
        # Session totals (Hestia readouts) — reset on Refresh.
        self._sess_tokens = 0
        self._sess_tools = 0
        self._sess_fails = 0
        # The file-change question on screen, or None (see _on_confirm). One at
        # a time: a turn's tool calls run one after another on one worker.
        self._pending_confirm: Optional[_ConfirmReply] = None

        # ── Hestia — title, narration lamp, stop, refresh, session readouts ──
        self._hestia = HestiaBar(self, on_stop=self._on_stop,
                                 on_refresh=self._on_refresh,
                                 on_notice=self.dj_says)
        self._hestia.pack(fill="x")

        # Registered under Kairos's "awen" worker in felhaven.py — see DjRider
        # for why it is a rider and not a branch in update().
        self.dj_rider = DjRider(self)

        # ── Transcript ────────────────────────────────────────────────────────
        log_row = tk.Frame(self, bg=C["bg"])
        log_row.pack(fill="both", expand=True)
        # height=1 is load-bearing, not cosmetic. A tk.Text defaults to asking
        # for 24 lines (~446 px), and pack hands out requested heights in pack
        # order BEFORE it distributes the leftover to expand=True children — so
        # the transcript claimed the window and squeezed the status line and the
        # entry row below it to 1 px each. The chat bar simply vanished at any
        # window under ~1280x900, which is why it went unnoticed on a maximised
        # window. Asking for one line and taking the rest through expand puts
        # the priority the right way round: the transcript shrinks (it scrolls),
        # the thing you type into never does.
        self._log = tk.Text(
            log_row, bg=C["card"], fg=C["text1"], font=FONTS["body"],
            borderwidth=0, highlightthickness=1, highlightbackground=C["border"],
            wrap="word", state="disabled", padx=8, pady=6, height=1,
        )
        # The scroll lane first (CONVENTIONS §7): a Text also asks for 80
        # characters of width, 658 px in this font, and packed first it took
        # the whole row of a narrow window and pack unmapped the scrollbar.
        scroll = PhosphorScroll(log_row, command=self._log.yview)
        scroll.pack(side="right", fill="y")
        self._log.pack(side="left", fill="both", expand=True)
        self._log.configure(yscrollcommand=scroll.set)
        self._log.tag_configure("you",     foreground=C["text2"])
        self._log.tag_configure("pythia",  foreground=C["text1"])
        self._log.tag_configure("stopped", foreground=C["text3"])
        # Auto-DJ speaks on its own line and in its own colour, so a song intro
        # is never mistaken for an answer to something you asked.
        self._log.tag_configure("dj", foreground=C["amber"])
        # Verbatim tool output (a Daedalus docstring): one colour for the
        # header and the text, so the block reads as one thing that is not
        # Pythia's own words — the palette's one non-map cyan.
        self._log.tag_configure("verbatim_head", foreground=C["verbatim"])
        self._log.tag_configure("verbatim", foreground=C["verbatim"])
        # A file-change question (see _on_confirm): amber, the colour this
        # panel already uses for "something needs you", over the exact content
        # in the verbatim colour, because it IS text that will be written as is.
        self._log.tag_configure("confirm_head", foreground=C["amber"])
        # Clickable "speak aloud" control appended after each answer.
        self._log.tag_configure("speak", foreground=C["text3"],
                                font=FONTS["small"])
        # Per-response meta-line (tokens · wall · tok/s · tools).
        self._log.tag_configure("meta",      foreground=C["text3"], font=FONTS["small"])
        self._log.tag_configure("meta_fail", foreground=C["red"],   font=FONTS["small"])
        # Right-click "Copy" — the Text stays state="disabled" (read-only) but
        # selection + clipboard copy work regardless of that state.
        self._log.bind("<Button-3>", self._show_log_menu)

        # ── Status line + session readouts ────────────────────────────────────
        # One row, opposite ends: the in-flight status on the left, the session
        # totals on the right, clear of Hestia's controls.
        status_row = tk.Frame(self, bg=C["bg"])
        status_row.pack(fill="x", pady=(4, 2))

        # Readouts are packed FIRST on purpose. Pack allocates in order, so
        # claiming their width up front means a long status string is clipped
        # rather than shoving the counters off the edge — at the old 700 px
        # minimum window this row had only ~20 px to spare (~80 at today's 760).
        readouts = tk.Frame(status_row, bg=C["bg"])
        readouts.pack(side="right")
        # Cerberus at a glance. felhaven.py registers it on Kairos's `cerberus`
        # worker; like AmmitBadge it holds no state of this panel's.
        self.cerberus_lamp = CerberusLamp(readouts, bg=C["bg"])
        self.cerberus_lamp.pack(side="left")
        self._flux = tk.Label(readouts, text="Scraptoken Flux: 0",
                              font=FONTS["small"], fg=C["text3"], bg=C["bg"])
        self._flux.pack(side="left", padx=(12, 0))
        rites_wrap = tk.Frame(readouts, bg=C["bg"])
        rites_wrap.pack(side="left", padx=(12, 0))
        self._rites_main = tk.Label(rites_wrap, text="Rites: 0",
                                    font=FONTS["small"], fg=C["text3"], bg=C["bg"])
        self._rites_main.pack(side="left")
        self._rites_fail = tk.Label(rites_wrap, text="",
                                    font=FONTS["small"], fg=C["red"], bg=C["bg"])
        self._rites_fail.pack(side="left")
        self._ammit_badge = AmmitBadge(readouts)
        self._ammit_badge.pack(side="left", padx=(12, 0))

        self._status = tk.Label(status_row, text="", font=FONTS["small_italic"],
                                fg=C["amber"], bg=C["bg"], anchor="w")
        self._status.pack(side="left", fill="x", expand=True)
        self._status.bind("<Button-1>", self._on_status_click)
        self._status.bind("<Enter>", self._on_status_enter)
        self._status.bind("<Leave>", self._on_status_leave)

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

        # ── File-change question (hidden until one is asked) ──────────────────
        # Packed just above the input row only while a question is open. Clicks
        # only: no key answers it, so an Enter meant for the next question can
        # never say yes to this one.
        self._input_row = row
        self._confirm_bar = tk.Frame(self, bg=C["bg"])
        tk.Label(self._confirm_bar, text="Pythia asks to change a file. Allow it?",
                 font=FONTS["small"], fg=C["amber"], bg=C["bg"]).pack(side="left")
        self._confirm_deny = self._bar_button("DENY", self._on_deny)
        self._confirm_allow = self._bar_button("ALLOW", self._on_allow)

        self._print("Pythia is listening. Ask a question and press Enter.\n",
                    "pythia")

        self._arm_poll()

    # ── Kairos contract ─────────────────────────────────────────────────────
    def update(self, data: Any) -> None:
        """Registered under "horai" (felhaven.py) purely to refresh the Ammit
        status badge on Kairos's 1 s tick — nothing else on this panel is
        timer-driven; the chat itself stays event-driven with its own poll
        chain (see module docstring). `data` is horai's own payload and
        unused here."""
        self._ammit_badge.refresh()

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
        count, with a red failure count appended when any tool failed.

        The two counts are click targets, each carrying its own tag so the click
        knows which turn it belongs to — the _append_speak_button pattern. The
        turn's diagnostic records are stashed under the same id. A turn that
        called no tools renders un-tagged: there is nothing to show, so it gets
        no hand cursor and no hover to promise otherwise.
        """
        total_tokens = stats["prompt_tokens"] + stats["output_tokens"]
        wall = _format_wall(stats["wall_ms"])
        tokps = (stats["output_tokens"] / (stats["eval_ms"] / 1000)
                 if stats["eval_ms"] > 0 else 0.0)
        called, failed = stats["tools_called"], stats["tools_failed"]

        self._rite_seq += 1
        seq = self._rite_seq
        self._store_rites(seq, stats.get("tool_records", []))

        self._log.configure(state="normal")
        self._log.insert("end", f"   {total_tokens} tok · {wall} · "
                                f"{tokps:.1f} tok/s · ", "meta")
        tools_tag = f"rite{seq}"
        self._log.insert("end", f"{called} tools",
                         ("meta", tools_tag) if called else "meta")
        if failed:
            fail_tag = f"ritefail{seq}"
            self._log.insert("end", " · ", "meta")
            self._log.insert("end", f"⚠ {failed} failed", ("meta_fail", fail_tag))
        self._log.insert("end", "\n")
        self._log.configure(state="disabled")

        if called:
            self._bind_rite_tag(tools_tag, seq, C["text3"], C["text1"], False)
        if failed:
            self._bind_rite_tag(fail_tag, seq, C["red"], C["red_bright"], True)
        self._log.see("end")

    def _store_rites(self, seq: int, records: list[dict[str, Any]]) -> None:
        """Keep this turn's records, evicting the oldest turns past the cap."""
        self._rites[seq] = list(records)
        while len(self._rites) > _MAX_RITE_TURNS:
            self._rites.popitem(last=False)

    def _bind_rite_tag(self, tag: str, seq: int, rest: str, lit: str,
                       focus_failures: bool) -> None:
        """Make one meta-line span open the Rite Log, and light up on hover."""
        self._log.tag_bind(tag, "<Button-1>",
                           lambda _e: self._open_rites(seq, focus_failures))
        self._log.tag_bind(tag, "<Enter>",
                           lambda _e: self._hover_rite(tag, lit, True))
        self._log.tag_bind(tag, "<Leave>",
                           lambda _e: self._hover_rite(tag, rest, False))

    def _hover_rite(self, tag: str, color: str, on: bool) -> None:
        self._log.configure(cursor="hand2" if on else "")
        self._log.tag_configure(tag, foreground=color)

    def _open_rites(self, seq: int, focus_failures: bool) -> None:
        """Open the Rite Log for one turn. A turn whose records have aged out of
        the ring passes None, which the window renders as its expired view — the
        tag stays live and clicking it is never an error."""
        RiteLog(self, self._rites.get(seq), focus_failures=focus_failures)

    # ── Narration (calliope.speak is non-blocking — enqueues, never blocks) ───
    def _speak(self, text: str) -> None:
        """Speak `text` now, replacing whatever's playing (the speak button is an
        explicit 'read THIS' — barge in on any current/queued narration)."""
        calliope.stop()
        calliope.speak(text)
        # All the text is in already, so release the prebuffer gate at once. The
        # gate stopped being filler-conditional in 2026-09-07, and without this a
        # one-chunk answer would wait out _PREBUFFER_TIMEOUT for a lead that can
        # never arrive.
        calliope.end_turn()

    def dj_says(self, text: str) -> None:
        """Print and speak one Auto-DJ line. Main thread only.

        Spoken whether or not Vox Response is on, the AmmitAlarm precedent: that
        flag is about narrating ANSWERS, and a radio host that silently played
        songs would not be a radio host. Vox End still silences it, because Vox
        End is calliope.stop() and that reaches everything Harmonia is playing.

        It does NOT barge in. AmmitAlarm calls calliope.stop() before speaking,
        which is right for a timer going off and wrong for a song introduction —
        Awen has already waited for quiet before producing this text (see its
        *Trigger*), so cutting off whatever is talking would undo that.

        Never enters self._history: this is not a turn Pythia took on Matthew's
        behalf, and a few songs of patter would evict his actual conversation
        from a three-turn window (see *History*)."""
        self._print(f"dj › {text}\n\n", "dj")
        calliope.speak(text)
        calliope.end_turn()

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
        self._set_status("consulting the oracle…")

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

        def _run(m: str = msg, h: "list[dict[str, Any]]" = history,
                 ep: int = epoch, cxl: threading.Event = cancel) -> None:
            answer = pythia.ask(
                m, h,
                on_delta=lambda p: self._q.put(("delta", p, ep)),
                on_event=lambda ev: self._q.put(("stats", ev, ep)),
                cancel=cxl,
                on_verbatim=lambda v: self._q.put(("verbatim", v, ep)),
                on_confirm=lambda r: self._confirm_from_worker(r, ep, cxl),
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
        self._close_confirm(False, "✗ not allowed\n")   # Stop answers no

    def _on_refresh(self) -> None:
        self._hestia.flash_reset()             # one beat, so the click reads
        self._cancel.set()                     # cancel anything in flight
        self._epoch += 1                       # invalidate that thread's late messages
        calliope.stop()
        self._close_confirm(False, "")         # an open question is a no
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")
        self._history.clear()
        # The transcript those tags lived in is gone, so the records behind them
        # are unreachable. _rite_seq deliberately does NOT reset (the _speak_seq
        # rule): a monotonic counter can never hand a new turn a stale tag name.
        self._rites.clear()
        self._set_status("")
        self._resp_started = False
        self._say_buf = ""
        self._last_stats = None
        self._sess_tokens = 0
        self._sess_tools = 0
        self._sess_fails = 0
        self._set_flux(0)
        self._set_rites(0, 0)
        self._hestia.set_running(False)
        self._print("Pythia is listening. Ask a question and press Enter.\n",
                    "pythia")

    # ── Poll: drain queued events onto the transcript (main thread) ──────────
    def _arm_poll(self) -> None:
        """(Re-)arm the drain chain, keeping exactly ONE poll outstanding.

        Cancels first because _poll can be entered without its scheduled timer
        having fired — the smoke tests call panel._poll() directly to drain the
        queue without a mainloop. Re-arming blind would orphan the pending id,
        and an orphan is precisely what destroy() can no longer cancel.
        Cancelling an id that already fired is a harmless no-op."""
        if self._poll_after_id is not None:
            self.after_cancel(self._poll_after_id)
        self._poll_after_id = self.after(_POLL_MS, self._poll)

    def _poll(self) -> None:
        """Drain the worker queue and run the riders, then ALWAYS re-arm.

        The re-arm is in a `finally` and the body is guarded, because this chain
        is load-bearing far beyond its own panel: it feeds _on_delta, which is
        what hands sentences to Calliope. A single escaping exception used to
        stop the chain dead — and then narration halted mid-answer, the
        transcript froze, and Vox End stayed lit because _tick_face never ran
        again to clear it. Worse, it was INVISIBLE: Tk prints a failing callback
        to stderr, and Felhaven runs under pythonw.exe, which has none. So the
        one symptom of a dead UI was silence.

        Swallowing here is deliberate and is not a licence to be sloppy in the
        riders: the exception is logged to felhaven.log (where Emanon can see
        it), so a bug that used to vanish now names itself."""
        if not self.winfo_exists():            # window closed mid-flight
            return                             # destroy() cancels the pending id
        try:
            while True:
                try:
                    kind, payload, epoch = self._q.get_nowait()
                except queue.Empty:
                    break
                if epoch != self._epoch:
                    continue                    # stale — from a Refresh-away turn
                if kind == "delta":
                    self._on_delta(payload)
                elif kind == "stats":
                    self._on_stats(payload)
                elif kind == "verbatim":
                    self._on_verbatim(payload)
                elif kind == "confirm":
                    self._on_confirm(payload)
                elif kind == "confirm_expired":
                    if payload is self._pending_confirm:
                        self._close_confirm(False, "✗ not allowed (no answer)\n\n")
                else:                           # "done"
                    self._on_done(payload)
            self._tick_status()
            self._tick_face()
            # Harmonia is forbidden a timer of its own (§6), so this standing
            # chain supplies its heartbeat: it hands Morpheus its volume back
            # once narration has actually finished. Free when nothing is ducked.
            harmonia.tick_yield()
            # The SECOND passenger on this chain, and for the same reason: a
            # taper is a ramp, and Kairos at 2 s cannot ramp anything. Free
            # when no taper is running — one identity check, no IPC.
            morpheus.tick_fade()
            # The queue window, when one is open. Free otherwise — one int.
            self._tick_queue()
        except Exception:  # noqa: BLE001 — the chain must outlive any one tick
            self._note_poll_failure()
        finally:
            self._arm_poll()

    def _note_poll_failure(self) -> None:
        """Report a failing tick without flooding the log.

        At most one traceback per _POLL_FAIL_QUIET, carrying how many failures
        it stands for. A persistent fault at 16 Hz would otherwise bury its own
        first traceback inside twenty minutes of rotation — turning the log line
        that makes this debuggable into the thing that destroys the evidence.
        The count is never reset, so an intermittent fault still reports, and
        the CURRENT exception is what gets traced each time."""
        self._poll_fails += 1
        now = time.monotonic()
        # Gate on TIME ONLY. Gating on the count as well looks equivalent and
        # is not: the counter resets after each report, so the very next tick
        # would look like a first failure and report again — the flood, intact.
        if self._poll_fail_reported and now - self._poll_fail_reported < _POLL_FAIL_QUIET:
            return                             # counted, not written
        log.exception("HomePanel: poll tick failed (%d since last report; "
                      "chain kept alive)", self._poll_fails)
        self._poll_fails = 0
        self._poll_fail_reported = now

    def _set_status(self, text: str, *, clickable: bool = False) -> None:
        """Write the status line, and say whether it is a control right now.

        FIVE sites write this label — this method, _on_submit, _on_refresh,
        _on_delta and _on_done — and only one of them ever sets a clickable
        state. Without one owner, the hand cursor from "Auto-DJ: 4 queued"
        survives into "consulting the oracle…" and stays for the whole answer,
        because _tick_status returns early while a request is in flight and
        never gets to repaint. A cursor that promises a click nothing handles
        is worse than no cursor.
        """
        self._status_shown = text
        self._status_clickable = clickable
        self._status.config(text=text, cursor="hand2" if clickable else "",
                            fg=C["amber"])

    def _on_status_enter(self, _event: "tk.Event[Any]") -> None:
        """Brighten WITHIN the state, never into amber. The label is already
        amber, so amber cannot double as its hover colour — the Hestia
        inversion, verbatim (§7, vox_controls.py)."""
        if self._status_clickable:
            self._status.config(fg=C["amber_bright"])

    def _on_status_leave(self, _event: "tk.Event[Any]") -> None:
        self._status.config(fg=C["amber"])

    def _on_status_click(self, _event: "tk.Event[Any]") -> None:
        """Open the queue, or lift the one already open."""
        if not self._status_clickable:
            return
        if self._dj_queue_box is not None and self._dj_queue_box.winfo_exists():
            self._dj_queue_box.lift()
            return
        self._queue_drawn = awen.queue_revision()
        self._dj_queue_box = QueueBox(self, awen.queue_view(),
                                      awen.remove_queued)

    def _tick_queue(self) -> None:
        """Repaint an open queue window when the queue has changed.

        Rides this drain chain rather than arming a timer, the tick_status /
        tick_face / harmonia.tick_yield / morpheus.tick_fade bargain — so the
        window needs no destroy() of its own and test_panel_after_teardown's
        "HomePanel leaves exactly ONE pending timer" stays literally true.

        FREE unless something moved: awen.queue_revision() is one int, and only
        a change pays for the snapshot. That is what lets this sit at 16 Hz
        beside four other passengers.
        """
        box = self._dj_queue_box
        if box is None:
            return
        if not box.winfo_exists():
            self._dj_queue_box = None       # closed by Escape or the X
            return
        revision = awen.queue_revision()
        if revision == self._queue_drawn:
            return
        self._queue_drawn = revision
        box.show(awen.queue_view())

    def _tick_status(self) -> None:
        """Own the status line whenever the request path does not.

        Rides the drain chain rather than arming a timer of its own, so there is
        no second after() id for destroy() to chase (§6). Writes only on a
        TRANSITION — at 16 Hz an unconditional repaint would fight whatever the
        request path last set, and would reset the label's own idea of itself
        sixteen times a second for nothing.

        One label, three claimants, in strict priority:

          1. a question in flight. 'consulting the oracle…' is set by
             _on_submit and cleared by _on_done, and nothing here may touch the
             line while that is true — Matthew asked, so Matthew's turn wins.
          2. the startup warm. Only reachable before anything else can be
             happening, so the order between it and Auto-DJ is academic; it is
             written down anyway because "academic today" is how precedence
             bugs are born.
          3. Auto-DJ, via awen.status_line() — a free read, the same bargain
             morpheus.is_audible() makes for this chain.

        Auto-DJ earns a place here because a DJ turn is a SILENT GAP: measured
        4.2s typically and 17.1s at worst, during which the song has stopped and
        the screen used to say nothing whatsoever. That is indistinguishable
        from Auto-DJ being broken."""
        if self._thread is not None and self._thread.is_alive():
            return                              # the request path owns the label
        if pythia.is_warming():
            wanted = "waking the oracle…"
        else:
            wanted = awen.status_line()
        if wanted == self._status_shown:
            return                              # nothing changed; leave it alone
        # Clickable only while it is showing the QUEUE. "waking the oracle…"
        # and the two Auto-DJ silence lines are facts, not controls.
        self._set_status(wanted, clickable=wanted.endswith("queued"))

    # ── Session readouts (bottom row) ────────────────────────────────────────
    # Owned here rather than by Hestia: this panel computes the totals, so
    # putting the labels a frame away only added a hop that could drift.

    def _set_flux(self, tokens: int) -> None:
        self._flux.config(text=f"Scraptoken Flux: {tokens}")

    def _set_rites(self, called: int, failed: int) -> None:
        self._rites_main.config(text=f"Rites: {called}")
        self._rites_fail.config(text=f" · {failed} failed" if failed else "")

    def _tick_face(self) -> None:
        """Push Hestia's face state and the Vox End cue, derived fresh each
        drain — like _tick_status, it rides the existing chain rather than
        arming a timer of its own (§6).

        Both setters are idempotent, so pushing at _POLL_MS (16 Hz) costs
        nothing: the face early-returns on an unchanged state (feeding it the
        same name every tick would otherwise reset its syllable envelope), and
        a VoxToggle repaints only on a real change.

        `harmonia.is_playing()` is the reason this can't live in _on_done:
        synth and playback lag behind generation, so narration is still
        audible for seconds after the answer text is complete — which is
        exactly the window in which Vox End must still read as armed.

        Priority is speaking > thinking > playing > idle, and the first pair
        matters because narration DUCKS music rather than stopping it: both are
        routinely true at once, and the face has one state. Pythia's own voice
        wins over her soundtrack. `morpheus.is_audible()` is a free read of the
        last Kairos snapshot — never call status() from here, it is an mpv IPC
        round trip and this runs 16 times a second.
        """
        busy = self._thread is not None and self._thread.is_alive()
        audible = harmonia.is_playing()
        if audible:
            state = "speaking"
        elif busy or pythia.is_warming():
            state = "thinking"
        elif morpheus.is_audible():
            state = "playing"
        else:
            state = "idle"
        self._hestia.set_face_state(state)
        self._hestia.set_running(busy or audible)
        # Auto-DJ can turn ITSELF off (its breaker gives up after three turns
        # that played nothing), so the lamp is re-read here rather than only on
        # a click. Idempotent, like the two setters above — a VoxToggle repaints
        # only on a real change.
        self._hestia.refresh_vox()

    def _on_delta(self, piece: str) -> None:
        """A streamed token arrived: show it, and buffer it for narration."""
        if not self._resp_started:
            self._set_status("")
            self._print("pythia › ", "pythia")
            self._resp_started = True
        self._print(piece, "pythia")
        if self._say_turn:
            self._say_buf += piece
            self._drain_sentences()

    def _on_verbatim(self, payload: dict[str, Any]) -> None:
        """Print a tool's text EXACTLY as given, as its own block. Not spoken.

        The "verbatim" tag covers the text and nothing else — the header and
        the blank line after are tagged separately — so the transcript holds
        a range that is byte-for-byte the string the tool produced.

        If the model said something before calling the tool, that line is
        closed first (and its unspoken tail handed to Calliope), so any text
        the model streams afterwards starts on a fresh `pythia ›` line
        instead of running on from under the block."""
        if self._resp_started:
            self._print("\n", "pythia")
            if self._say_turn and self._say_buf.strip():
                calliope.speak(self._say_buf.strip())
            self._say_buf = ""
            self._resp_started = False
        label = str(payload.get("label") or "tool")
        title = str(payload.get("title") or "")
        self._print(f"{label} › {title}\n", "verbatim_head")
        self._print(str(payload.get("text", "")), "verbatim")
        self._print("\n\n", "pythia")

    # ── File-change questions (pythia.ask's on_confirm) ───────────────────────
    # A tool that changes files (pythia._CONFIRM_TOOLS: write_document,
    # edit_document) runs only after Matthew says yes to its exact arguments:
    # a web page Pythia read must not be able to talk her into rewriting his
    # notes. See CONVENTIONS §12, 2026-09-30.

    def _bar_button(self, text: str,
                    command: "Callable[[tk.Event[Any]], None]") -> tk.Label:
        """A clickable label on the question bar, with the house hover flip
        (§7; AmmitAlarmPopup._button). Packed right, so the first made sits
        rightmost. `command` is bound as a NAMED method, never a lambda, so a
        test can read which answer each label gives: a swapped pair would make
        DENY say yes."""
        lbl = tk.Label(self._confirm_bar, text=text, font=FONTS["small"],
                       fg=C["text3"], bg=C["bg"], cursor="hand2", padx=8)
        lbl.pack(side="right")
        lbl.bind("<Button-1>", command)
        lbl.bind("<Enter>", lambda _e: lbl.config(fg=C["text1"]))
        lbl.bind("<Leave>", lambda _e: lbl.config(fg=C["text3"]))
        return lbl

    def _confirm_from_worker(self, request: dict[str, Any], epoch: int,
                             cancel: threading.Event) -> bool:
        """pythia.ask()'s on_confirm. Runs on the WORKER thread, so it touches
        only the queue: the question goes to the main thread, and the worker
        waits for the click, a Stop or Refresh (`cancel`), or _CONFIRM_WAIT_S.
        Anything but an Allow click is a no."""
        reply = _ConfirmReply()
        self._q.put(("confirm", (request, reply), epoch))
        deadline = time.monotonic() + _CONFIRM_WAIT_S
        while not reply.done.wait(0.1):
            if cancel.is_set():
                return False
            if time.monotonic() >= deadline:
                self._q.put(("confirm_expired", reply, epoch))
                return False
        return reply.allowed

    def _on_confirm(self, payload: "tuple[dict[str, Any], _ConfirmReply]") -> None:
        """Show a file-change question: which tool, every argument, and the
        content that would be written, exactly, then the Allow/Deny bar.

        Nothing is trimmed. A preview could hide a line at the end, and the
        end is where a hidden line would go. The model's output cap (1024
        tokens) already keeps the content to a few thousand characters."""
        request, reply = payload
        self._close_confirm(False, "")           # never two questions open
        if self._resp_started:                   # the _on_verbatim line break
            self._print("\n", "pythia")
            if self._say_turn and self._say_buf.strip():
                calliope.speak(self._say_buf.strip())
            self._say_buf = ""
            self._resp_started = False
        tool = str(request.get("tool") or "a tool")
        arguments = request.get("arguments") or {}
        self._print(f"pythia asks › {tool}\n", "confirm_head")
        for key, value in arguments.items():
            if key != "content":
                self._print(f"   {key}: {value}\n", "confirm_head")
        if tool == "write_document":
            self._print("   (replaces the file if it already exists)\n", "confirm_head")
        content = arguments.get("content")
        if content:
            self._print(f"{content}\n", "verbatim")
        self._pending_confirm = reply
        self._confirm_bar.pack(fill="x", pady=(2, 2), before=self._input_row)
        self._set_status("waiting for your OK…")

    def _on_allow(self, _event: "Optional[tk.Event[Any]]" = None) -> None:
        """The ALLOW click."""
        self._close_confirm(True, "✓ allowed\n\n")

    def _on_deny(self, _event: "Optional[tk.Event[Any]]" = None) -> None:
        """The DENY click."""
        self._close_confirm(False, "✗ not allowed\n\n")

    def _close_confirm(self, allowed: bool, note: str) -> None:
        """Answer the open question (if any), hide the bar, and say what
        happened. Every way a question ends comes through here: a click, Stop,
        Refresh, the timeout, and the turn ending."""
        reply = self._pending_confirm
        if reply is None:
            return
        self._pending_confirm = None
        reply.answer(allowed)
        self._confirm_bar.pack_forget()
        if note:
            self._print(note, "meta")
        # The turn goes on after a click or the timeout, so the line goes back
        # to what it said; after Stop or Refresh it is ending, and _on_done or
        # _on_refresh owns the line.
        if (self._thread is not None and self._thread.is_alive()
                and not self._cancel.is_set()):
            self._set_status("consulting the oracle…")

    def _on_stats(self, stats: dict[str, Any]) -> None:
        """One stats event per ask() call — cache it for _on_done's meta-line,
        and roll it into the session totals Hestia displays."""
        self._last_stats = stats
        self._sess_tokens += stats["prompt_tokens"] + stats["output_tokens"]
        self._sess_tools += stats["tools_called"]
        self._sess_fails += stats["tools_failed"]
        self._set_flux(self._sess_tokens)
        self._set_rites(self._sess_tools, self._sess_fails)

    def _on_done(self, answer: str) -> None:
        """The answer is complete (or was cancelled): finalize the transcript,
        flush any unspoken tail, and persist the turn — unless it was stopped."""
        self._hestia.set_running(False)
        self._close_confirm(False, "")         # a turn can't end on an open question
        cancelled = bool(self._last_stats and self._last_stats.get("cancelled"))

        if cancelled:
            calliope.stop()
            self._say_buf = ""
            self._set_status("")
            if not self._resp_started:
                self._print("pythia › ", "pythia")
            self._print("⏹ stopped\n\n", "stopped")
            return

        self._set_status("")
        if not self._resp_started:
            # No tokens streamed (an error string, or an empty answer) — print it.
            self._print(f"pythia › {answer}", "pythia")
            if self._say_turn and answer.strip():
                calliope.speak(answer)
        elif self._say_turn and self._say_buf.strip():
            calliope.speak(self._say_buf.strip())   # speak the last partial sentence
        self._say_buf = ""
        if self._say_turn:
            # All text is in — release the prebuffer gate so a short answer
            # plays without waiting for a lead that will never fill.
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

    # ── Teardown ──────────────────────────────────────────────────────────────
    def destroy(self) -> None:
        """Cancel the poll chain before teardown (the EchoPanel precedent).

        The winfo_exists() guard inside _poll is NOT enough on its own: Tk
        deletes a widget's registered Tcl commands as it destroys the widget, so
        an already-scheduled after script has nothing left to call and Tcl
        reports `invalid command name "..._poll"` before Python is ever reached.
        Cancelling the pending id is what actually stops it; the guard only
        covers the callback that is already mid-flight."""
        self._cancel.set()                     # let any in-flight ask() wind down
        if self._poll_after_id is not None:
            self.after_cancel(self._poll_after_id)
        self._poll_after_id = None
        super().destroy()
