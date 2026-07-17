"""
harmonia.py — Harmonia, Keeper of the Output Device
====================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Own the one audio output device. Nothing else in this process
             calls sounddevice directly — Calliope and Orpheus hand Harmonia
             PCM; Harmonia plays it.

Contract:    Neither polled nor LLM-facing — infrastructure, like kairos.py.
             NO fetch(), NO handle(), NO TOOL_DEFINITION.
                 play(pcm, sample_rate, tag="") -> None
                     Enqueue PCM for playback at the given sample rate.
                     Calls morpheus.stop() FIRST, every time, before
                     enqueuing anything (see "Yielding Morpheus" below).
                     Never raises: a missing or busy device degrades to a
                     logged no-op. `tag` is for logging only — it carries no
                     routing meaning; there are no channels (see stop()).
                 stop() -> None
                     Stop everything, unconditionally: sd.stop() (interrupts
                     whatever is mid-playback) + drop everything still
                     queued + bump the epoch so an item already pulled off
                     the queue is skipped instead of played. Global — there
                     are no channels, so this also cuts off a briefing that
                     happened to be mid-playback. A no-op when idle. Never
                     raises.
                 is_playing() -> bool
                     True while there is still audio outstanding — queued or
                     mid-play. Read-only, never raises. This is the
                     completion signal Orpheus's fetch() reads to know when
                     a briefing has finished on its own.
                 shutdown() -> None
                     Stop the play thread cleanly. Wired into
                     felhaven._on_close BEFORE morpheus.shutdown().

Sample rate: Every caller passes its OWN rate explicitly — Harmonia hardcodes
             none. Kokoro (Calliope) synthesizes at 24 kHz mono; Orpheus
             decodes at 24 kHz mono too, but only because that's what Echo's
             pipeline actually produces (see tools/orpheus.py's "Limit"
             section) — that's Orpheus's assumption about local_audio/, not
             a fact Harmonia should ever hardcode on its behalf. Get a
             caller's rate wrong (whichever caller, whatever its own reason)
             and it plays at the wrong speed.

Yielding     play() calls morpheus.stop() before enqueuing ANY audio, in
Morpheus:    Harmonia's own code — not in Calliope, not in Orpheus, not in a
             panel. One direction only: Harmonia can silence Morpheus, but
             Morpheus starting playback is a deliberate UI action, and
             Morpheus never learns Harmonia exists. morpheus.stop() opens
             its IPC pipe fresh each call and never raises, so it is safe to
             call from Harmonia's play thread — a new caller on a new thread
             that morpheus.py's docstring doesn't yet mention (flagged, not
             fixed, per the handoff).

No channels: One stream, full stops only. Harmonia does not know what a
             filler is, what speech is, or what a briefing is — it takes PCM
             and a sample rate, nothing more. It never remembers what it
             interrupted; bringing music back is `tools.morpheus.resume_music`,
             a deliberate, separate, manual tool. Harmonia stays dumb.

Upstream:    calliope.py (speech PCM @ 24 kHz mono), tools/orpheus.py
             (decoded file PCM @ 24 kHz mono, matching Echo's own output —
             not a generic decode), felhaven.py (calls shutdown() on close).
Downstream:  sounddevice (playback) -> tools/morpheus.py (yielded, never
             called back — Harmonia is a one-way dependency on Morpheus).

Requires:    sounddevice, numpy (already in the stack via Calliope). Stdlib
             otherwise.
"""

import logging
import queue
import threading
from typing import Optional

import numpy as np

from tools import morpheus

log = logging.getLogger("METIS.harmonia")

# One queued item is (epoch, pcm, sample_rate, tag); None is the shutdown
# sentinel that tells the play thread to exit.
_QueueItem = Optional[tuple[int, np.ndarray, int, str]]

_queue: "queue.Queue[_QueueItem]" = queue.Queue()

_epoch = 0
_epoch_lock = threading.Lock()

# Outstanding audio work: +1 in play() when an item is enqueued, -1 once the
# play thread has attempted that exact item (played, skipped-as-stale, or
# failed). is_playing() is just "> 0" — a race-safe completion signal without
# needing to peek inside the queue or ask the device anything.
_pending = 0
_pending_lock = threading.Lock()

_thread: Optional[threading.Thread] = None
_thread_lock = threading.Lock()


def _play_pcm(pcm: np.ndarray, sample_rate: int, tag: str) -> None:
    """Push PCM to the default output device and block until it finishes.
    Never raises: a busy or absent device (or no sounddevice at all)
    degrades to a logged no-op. sounddevice is imported lazily so importing
    this module never needs an audio backend."""
    try:
        import sounddevice as sd  # type: ignore[import-untyped]
    except Exception as e:  # noqa: BLE001
        log.error("Harmonia: sounddevice unavailable (%s) — cannot play.", e)
        return
    try:
        sd.play(pcm, sample_rate)
        sd.wait()
    except Exception as e:  # noqa: BLE001 — device busy / no output device
        log.error("Harmonia: playback failed (tag=%r): %s", tag, e)


def _play_worker() -> None:
    """Drain the queue in order, one item at a time. Exits on the None
    shutdown sentinel. Skips items left over from a barged-in epoch instead
    of playing them."""
    while True:
        item = _queue.get()
        if item is None:
            return
        ep, pcm, sample_rate, tag = item
        try:
            with _epoch_lock:
                stale = ep != _epoch
            if not stale:
                _play_pcm(pcm, sample_rate, tag)
        finally:
            global _pending
            with _pending_lock:
                _pending = max(0, _pending - 1)


def _ensure_worker() -> None:
    """Start the play thread on first use (daemon; lives for the app)."""
    global _thread
    with _thread_lock:
        if _thread is None:
            _thread = threading.Thread(target=_play_worker, name="harmonia-play",
                                        daemon=True)
            _thread.start()


# ── Public API ────────────────────────────────────────────────────────────────

def play(pcm: np.ndarray, sample_rate: int, tag: str = "") -> None:
    """Enqueue `pcm` for playback at `sample_rate`. NON-BLOCKING — returns at
    once. Yields Morpheus first (see module docstring). Never raises."""
    try:
        morpheus.stop()
    except Exception as e:  # noqa: BLE001 — yielding music must never block audio
        log.error("Harmonia: could not yield Morpheus: %s", e)

    _ensure_worker()
    global _pending
    with _epoch_lock:
        ep = _epoch
    with _pending_lock:
        _pending += 1
    try:
        _queue.put((ep, pcm, sample_rate, tag))
    except Exception as e:  # noqa: BLE001 — a full/broken queue must not crash the caller
        with _pending_lock:
            _pending = max(0, _pending - 1)
        log.error("Harmonia: could not enqueue playback (tag=%r): %s", tag, e)


def stop() -> None:
    """Stop everything: interrupt in-flight playback, drop everything
    queued, bump the epoch. Global — there are no channels. A no-op when
    idle. Never raises."""
    global _epoch, _pending
    with _epoch_lock:
        _epoch += 1
    try:
        while True:
            _queue.get_nowait()
            with _pending_lock:
                _pending = max(0, _pending - 1)
    except queue.Empty:
        pass
    try:
        import sounddevice as sd
        sd.stop()
    except Exception as e:  # noqa: BLE001 — nothing playing / no backend
        log.debug("Harmonia: stop() no-op (%s)", e)


def is_playing() -> bool:
    """True while audio is queued or mid-playback. Read-only, never raises."""
    with _pending_lock:
        return _pending > 0


def shutdown() -> None:
    """Stop the play thread cleanly. Safe to call even if it never started.
    Interrupts immediately (via stop()) rather than waiting out whatever is
    mid-playback."""
    global _thread
    with _thread_lock:
        thread = _thread
        _thread = None
    if thread is None:
        return
    stop()
    _queue.put(None)
    thread.join(timeout=2.0)


# ── Standalone demo ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import time

    logging.basicConfig(level=logging.INFO)
    tone = (np.sin(2 * np.pi * 440 * np.arange(24000) / 24000) * 0.2).astype(np.float32)
    print("[Harmonia] playing a 1s 440Hz tone at 24kHz...")
    play(tone, 24000, tag="demo")
    while is_playing():
        time.sleep(0.1)
    print("[Harmonia] done.")
    shutdown()
