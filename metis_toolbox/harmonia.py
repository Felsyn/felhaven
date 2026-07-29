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
                     Append PCM to the playback buffer at the given sample
                     rate. Calls morpheus.stop() FIRST, every time, before
                     buffering anything (see "Yielding Morpheus" below).
                     Never raises: a missing or busy device degrades to a
                     logged no-op. `tag` is for logging only — it carries no
                     routing meaning; there are no channels (see stop()).
                 stop() -> None
                     Barge-in: FLUSH the buffer so the callback falls silent.
                     Does NOT touch sounddevice — the owned stream keeps
                     running and simply emits silence, so no other thread ever
                     races the device API (see "Owned stream" below). Global —
                     there are no channels, so this also silences a briefing
                     that happened to be mid-playback. A no-op when idle.
                     Never raises.
                 is_playing() -> bool
                     True while there are unplayed samples in the buffer. Read-
                     only, never raises. This is the completion signal
                     Orpheus's fetch() reads to know when a briefing has
                     finished on its own.
                 shutdown() -> None
                     Flush and close the owned stream. Wired into
                     felhaven._on_close BEFORE morpheus.shutdown().

Owned stream: Harmonia owns exactly ONE sd.OutputStream, opened lazily on the
             first play() and left running for the app's life (idle = the
             callback emits silence). Only Harmonia's own code touches
             sounddevice, and only through this one object's callback — never
             the module-level sd.play()/sd.stop()/sd.wait() convenience
             functions, which share a single global stream and are documented
             as unsafe for concurrent multithreaded use. That sharing was a
             real crash: the play worker's sd.play()/sd.wait() raced the UI
             thread's sd.stop() during barge-in and segfaulted PortAudio (see
             CONVENTIONS §12, 2026-07-26). Now the callback (PortAudio's own
             thread) is the ONLY consumer of the buffer; play() appends and
             stop() clears it, both under one lock. No two threads ever call
             into the device API at once.

Sample rate: Every caller passes its OWN rate explicitly — Harmonia hardcodes
             none. Kokoro (Calliope) synthesizes at 24 kHz mono; Orpheus
             decodes at 24 kHz mono too, but only because that's what Echo's
             pipeline actually produces (see tools/orpheus.py's "Limit"
             section) — that's Orpheus's assumption about local_audio/, not
             a fact Harmonia should ever hardcode on its behalf. The stream is
             (re)opened at whatever rate the current caller passes; a rate
             change (never happens at runtime — both callers are 24 kHz) flushes
             and reopens. Get a caller's rate wrong and it plays at the wrong
             speed.

Latency:     The output stream requests a MODERATE device latency (_LATENCY) —
             deep enough that the trivial, memcpy-only callback rides out CPU
             starvation from concurrent LLM + kokoro work without underrunning
             mid-word, but shallow enough that a stop() flush falls silent
             within ~that latency. Tunable by ear: lower for snappier barge-in,
             raise if narration stutters under load.

Yielding     play() calls morpheus.stop() before buffering ANY audio, in
Morpheus:    Harmonia's own code — not in Calliope, not in Orpheus, not in a
             panel. One direction only: Harmonia can silence Morpheus, but
             Morpheus starting playback is a deliberate UI action, and
             Morpheus never learns Harmonia exists. morpheus.stop() opens
             its IPC pipe fresh each call and never raises, so it is safe from
             whatever thread enters play() — Calliope's release worker (speech)
             or the UI thread (a filler or an Orpheus briefing).
             morpheus.py's own docstring now documents this caller.

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

V0.02:       Audited 2026-07-24; stream rewrite 2026-07-26. Harmonia now owns a
             single persistent sd.OutputStream with a callback that pulls from
             an internal buffer, and barge-in (stop()) is a buffer FLUSH rather
             than a cross-thread sd.stop(). This is the "Stage 2" stream fix the
             prior note scoped: it removes the shared-global-stream race that
             segfaulted the app on barge-in (a new question while narration was
             still speaking) — see CONVENTIONS §12. The never-raise contract
             (play/stop/is_playing/shutdown, plus the callback) is preserved;
             morpheus yielding and the per-caller sample rate are unchanged.
"""

import logging
import threading
from collections import deque
from typing import Any, Optional

import numpy as np

from tools import morpheus

log = logging.getLogger("METIS.harmonia")

# Moderate output latency (seconds). Deep enough that the memcpy-only callback
# survives CPU starvation (LLM + kokoro + playback on a 6-core box) without
# underrunning; shallow enough that a stop() flush falls silent within ~this
# window. Tunable by ear — see the "Latency" docstring section.
_LATENCY = 0.2
_DTYPE = "float32"

# ── Playback buffer — the callback drains it, play() appends, stop() clears ────
# One lock guards all three. Held only for fast memcpy/append/clear, never
# across a device call, so it can't glitch the audio callback.
_buffer: "deque[np.ndarray]" = deque()
_head = 0                      # samples already played from _buffer[0]
_buffered_samples = 0          # total unplayed samples across the whole buffer
_buffer_lock = threading.Lock()

# ── The one owned output stream ────────────────────────────────────────────────
_stream: Any = None
_stream_rate: Optional[int] = None
_stream_lock = threading.Lock()


def _fill(outdata: np.ndarray, frames: int) -> None:
    """Copy up to `frames` samples from the buffer into `outdata`, spanning
    segments as needed; zero-fill the remainder on underrun (idle, a flush, or
    CPU starvation). Runs on PortAudio's callback thread."""
    global _head, _buffered_samples
    written = 0
    with _buffer_lock:
        while written < frames and _buffer:
            seg = _buffer[0]
            take = min(len(seg) - _head, frames - written)
            outdata[written:written + take, 0] = seg[_head:_head + take]
            written += take
            _head += take
            _buffered_samples = max(0, _buffered_samples - take)
            if _head >= len(seg):
                _buffer.popleft()
                _head = 0
    if written < frames:
        outdata[written:, 0] = 0.0     # underrun / idle → silence, never a click


def _flush_buffer() -> None:
    """Drop every buffered sample (barge-in / reopen). The callback then emits
    silence. Never raises."""
    global _head, _buffered_samples
    with _buffer_lock:
        _buffer.clear()
        _head = 0
        _buffered_samples = 0


def _callback(outdata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
    """PortAudio pulls audio through here. Must NEVER raise — a raising callback
    crashes PortAudio — so any error degrades to a block of silence."""
    try:
        _fill(outdata, frames)
    except Exception as e:  # noqa: BLE001 — a callback error must not crash the device
        log.error("Harmonia: callback error (%s) — emitting silence.", e)
        try:
            outdata.fill(0)
        except Exception:  # noqa: BLE001 — last resort; nothing more we can do
            pass


def _close_stream_locked() -> None:
    """Abort + close the owned stream and clear the ref. Caller holds
    _stream_lock. Never raises."""
    global _stream, _stream_rate
    s, _stream, _stream_rate = _stream, None, None
    if s is None:
        return
    for op in (s.abort, s.close):
        try:
            op()
        except Exception as e:  # noqa: BLE001 — a broken device must not crash shutdown
            log.debug("Harmonia: stream teardown (%s)", e)


def _ensure_stream(rate: int) -> Any:
    """Return the one owned OutputStream, opening it (or reopening at a new rate)
    on demand. Returns None if sounddevice or the device is unavailable — logged,
    never raised. sounddevice is imported lazily so importing this module never
    needs an audio backend."""
    global _stream, _stream_rate
    with _stream_lock:
        if _stream is not None and _stream_rate == rate:
            return _stream
        if _stream is not None:            # rate changed (never at runtime): reopen
            _close_stream_locked()
            _flush_buffer()                # old-rate audio can't play on a new stream
        try:
            import sounddevice as sd  # type: ignore[import-untyped]
        except Exception as e:  # noqa: BLE001 — no backend installed
            log.error("Harmonia: sounddevice unavailable (%s) — cannot play.", e)
            return None
        try:
            stream = sd.OutputStream(
                samplerate=rate, channels=1, dtype=_DTYPE,
                latency=_LATENCY, callback=_callback,
            )
            stream.start()
        except Exception as e:  # noqa: BLE001 — no/busy output device
            log.error("Harmonia: could not open output stream (%s).", e)
            return None
        _stream, _stream_rate = stream, rate
        return _stream


# ── Public API ────────────────────────────────────────────────────────────────

def play(pcm: np.ndarray, sample_rate: int, tag: str = "") -> None:
    """Append `pcm` to the playback buffer at `sample_rate`. NON-BLOCKING —
    returns at once. Yields Morpheus first (see module docstring). Never raises;
    a missing/busy device drops the audio quietly."""
    try:
        morpheus.stop()
    except Exception as e:  # noqa: BLE001 — yielding music must never block audio
        log.error("Harmonia: could not yield Morpheus: %s", e)

    if _ensure_stream(sample_rate) is None:
        return                              # no device — drop the audio, never raise

    global _buffered_samples
    # Flatten to 1D mono. Harmonia owns the device and historically accepted 2D
    # (frames, 1) PCM via the old sd.play(); the new callback assumes 1D mono
    # (the stream is opened channels=1), so normalize here. Both current callers
    # are mono — Orpheus decodes -ac 1 (shaped (frames, 1)), kokoro is already 1D
    # — so flattening is lossless. A future STEREO caller would need channel-aware
    # handling here AND a multi-channel stream in _ensure_stream.
    chunk = np.ascontiguousarray(pcm, dtype=np.float32).reshape(-1)
    with _buffer_lock:
        _buffer.append(chunk)
        _buffered_samples += len(chunk)     # is_playing() true synchronously


def stop() -> None:
    """Barge-in: flush the buffer so the callback falls silent within ~_LATENCY.
    Touches no device API — the owned stream keeps running and emits silence, so
    nothing races it. Global (no channels). A no-op when idle. Never raises."""
    _flush_buffer()


def is_playing() -> bool:
    """True while unplayed samples remain in the buffer. Read-only, never raises."""
    with _buffer_lock:
        return _buffered_samples > 0


def shutdown() -> None:
    """Flush the buffer and close the owned stream. Safe to call even if the
    stream never opened; idempotent."""
    stop()
    with _stream_lock:
        _close_stream_locked()


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
