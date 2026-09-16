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
                     rate. Calls morpheus.duck() FIRST, every time, before
                     buffering anything (see "Ducking Morpheus" below).
                     Never raises: a missing or busy device degrades to a
                     logged no-op. `tag` is for logging only — it carries no
                     routing meaning; there are no channels (see stop()).
                 tick_yield() -> None
                     Give Morpheus its volume back once the buffer has been
                     empty for _UNDUCK_HOLD. MAIN THREAD ONLY, and called from
                     a chain that already exists — HomePanel's 60 ms drain.
                     Free unless it is actually restoring. Never raises.
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
                     Flush and close the owned stream, restoring Morpheus's
                     volume first. Wired into felhaven._on_close BEFORE
                     morpheus.shutdown(), so mpv is still alive to hear it.

Owned stream: Harmonia owns exactly ONE sd.OutputStream, opened lazily on the
             first play() and left running for the app's life (idle = the
             callback emits silence) — with one exception, added 2026-09-09:
             if PortAudio stops the stream underneath us, _ensure_stream
             notices via _stream_is_live() and opens a replacement. Before
             that check existed, a stream killed by a device change was handed
             back to every later play(), which then appended to a buffer
             nothing was draining: narration went silent for the rest of the
             session, is_playing() stuck true (so Vox End stayed lit), and
             nothing was logged. It is exactly the failure the single owned
             stream is otherwise so good at avoiding, so do not assume "one
             stream forever" means "never check it". Only Harmonia's own code touches
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

Ducking      play() calls morpheus.duck() before buffering ANY audio, in
Morpheus:    Harmonia's own code — not in Calliope, not in Orpheus, not in a
             panel. One direction only: Harmonia can quiet Morpheus, but
             Morpheus starting playback is a deliberate UI action, and Morpheus
             never learns Harmonia exists. morpheus.duck() opens its IPC pipe
             fresh each call and never raises, so it is safe from whatever
             thread enters play() — Calliope's release worker (speech) or the
             UI thread (an Orpheus briefing).

             It used to call morpheus.stop(), and that KILLED music the user
             had just asked for: mpv's loadfile is fire-and-forget, so the
             play_music tool returned success while yt-dlp was still resolving
             the URL, and the answer's own first speech chunk stopped it
             mid-resolve (measured 2026-09-07: 2.36 s after the loadfile).
             Ducking dissolves that — there is nothing to interrupt.

             The RESTORE is the asymmetric half. Nothing here can notice the
             buffer going empty on its own: the callback is memcpy-only by
             standing promise (see "Owned stream"), and §6 gives timing to
             Kairos, so Harmonia arms no timer and spawns no thread. Instead
             tick_yield() is a main-thread reconcile that a chain which ALREADY
             exists calls — HomePanel's 60 ms drain, the same one that feeds
             the face. Harmonia supplies the logic; the panel supplies the
             heartbeat.

             _UNDUCK_HOLD is hysteresis, not politeness. The buffer legitimately
             empties MID-answer — between one synthesized chunk and the next,
             or under CPU starvation — so restoring on the first silent tick
             would pump the music up and down inside one sentence. Calliope's
             prebuffer gate (calliope._await_prebuffer) makes those gaps rare
             by building a lead first, but rare is not never.

No channels: One stream, full stops only. Harmonia does not know what speech
             is or what a briefing is — it takes PCM
             and a sample rate, nothing more. It never remembers what it
             interrupted; it only remembers that it OWES a restore, which is a
             single bool, not a memory of what was playing. Harmonia stays
             dumb.

Upstream:    calliope.py (speech PCM @ 24 kHz mono), tools/orpheus.py
             (decoded file PCM @ 24 kHz mono, matching Echo's own output —
             not a generic decode), felhaven.py (calls shutdown() on close).
Downstream:  sounddevice (playback) -> tools/morpheus.py (ducked and
             restored, never called back — Harmonia is a one-way dependency on
             Morpheus).

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

NOTE         The yield became a DUCK. play() lowers mpv's volume instead of
(2026-09-07):stopping it, and the new tick_yield() hands it back. This fixed a
             real bug in which asking Pythia to play a song killed the track
             it had just started, silently — see "Ducking Morpheus" and
             CONVENTIONS §12.
"""

import logging
import threading
import time
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

# ── Yielding Morpheus — the restore side ──────────────────────────────────────
# How long the buffer must stay EMPTY before we hand the volume back. This is
# hysteresis, not politeness: the buffer legitimately empties mid-answer (see
# the "Ducking Morpheus" docstring section), so restoring on the first silent
# tick would pump the music up and down inside one sentence. Long enough to
# ride out those gaps, short enough that the music is back before you notice
# the answer ended. Tunable by ear.
_UNDUCK_HOLD = 0.75            # seconds of continuous silence before restoring

_yielded = False                          # Morpheus is ducked on our behalf
_silent_since: Optional[float] = None     # when the buffer last went empty
_yield_lock = threading.Lock()            # guards the two above, nothing else

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


def _stream_is_live(stream: Any) -> bool:
    """True while PortAudio still considers the stream usable.

    A callback stream stays `active` from start() until something stops it —
    our callback never raises and never returns a stop flag, so on a healthy
    device this is always True, idle or not (an idle stream is still active,
    it just pulls silence). It goes False when PortAudio tears the stream down
    UNDERNEATH us: a device change or removal, a driver reset, another app
    taking the device in exclusive mode, or a sleep/resume cycle.

    Never raises. A backend that does not expose these attributes is treated as
    live, because guessing "dead" would reopen the stream on every call."""
    try:
        if stream.closed:
            return False
        return bool(stream.active)
    except Exception:  # noqa: BLE001 — an exotic backend must not break playback
        return True


def _ensure_stream(rate: int) -> Any:
    """Return the one owned OutputStream, opening it on demand — and REOPENING
    it if the rate changed or the device died under us. Returns None if
    sounddevice or the device is unavailable — logged, never raised.
    sounddevice is imported lazily so importing this module never needs an
    audio backend."""
    global _stream, _stream_rate
    with _stream_lock:
        if _stream is not None and _stream_rate == rate and _stream_is_live(_stream):
            return _stream
        if _stream is not None:
            # Either the rate changed (never at runtime) or PortAudio stopped
            # the stream on us. Both need a new stream, and both must DROP what
            # is buffered: old-rate audio can't play on a new stream, and audio
            # queued against a dead one is a backlog of answers to questions
            # asked long ago — replaying minutes of stale narration on recovery
            # would be worse than the silence it followed.
            if _stream_rate == rate:
                log.warning("Harmonia: the output stream went dead (device change, "
                            "driver reset, or another app taking the device) — "
                            "reopening and dropping %d buffered samples.",
                            _buffered_samples)
            _close_stream_locked()
            _flush_buffer()
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
    returns at once. Ducks Morpheus first and records that a restore is owed
    (see module docstring). Never raises; a missing/busy device drops the audio
    quietly."""
    try:
        morpheus.duck()                     # lower the music — do NOT kill it
    except Exception as e:  # noqa: BLE001 — yielding music must never block audio
        log.error("Harmonia: could not duck Morpheus: %s", e)
    # Armed BEFORE the no-device early return below: a duck taken on a machine
    # with no output stream must still be handed back on the next tick.
    _arm_yield()

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


def _arm_yield() -> None:
    """Record that Morpheus is ducked on our behalf and a restore is owed, and
    cancel any restore that was counting down. Called from play() on whatever
    thread got there; cheap enough to run per chunk."""
    global _yielded, _silent_since
    with _yield_lock:
        _yielded = True
        _silent_since = None


def _release_yield() -> None:
    """Clear the owed restore and hand Morpheus its volume back, unconditionally.
    The teardown path; tick_yield() does the same thing once its hold expires."""
    global _yielded, _silent_since
    with _yield_lock:
        owed, _yielded, _silent_since = _yielded, False, None
    if not owed:
        return
    try:
        morpheus.unduck()
    except Exception as e:  # noqa: BLE001 — a stuck pipe must not reach the UI
        log.error("Harmonia: could not restore Morpheus: %s", e)


def tick_yield() -> None:
    """Hand Morpheus its volume back once the buffer has been empty for
    _UNDUCK_HOLD seconds. Never raises.

    Call from the MAIN THREAD, on a chain that already exists — HomePanel's
    60 ms drain. Harmonia arms no timer and spawns no thread of its own: §6
    gives timing to Kairos and to the few chains already justified there, and
    this is not a new exception, it is a passenger on one.

    Free unless it is actually restoring: one bool under an uncontended lock,
    and morpheus.unduck() is itself free when nothing is ducked. An idle
    dashboard therefore does zero IPC at 60 ms, which is the point.

    Cannot get stuck. The hold is edge-cancelled rather than scheduled — there
    is no timer to miss — so every tick re-derives the answer from is_playing()
    and a skipped restore is simply retried 60 ms later."""
    global _yielded, _silent_since
    # Read the buffer state BEFORE taking _yield_lock: never hold two of this
    # module's locks at once.
    playing = is_playing()
    with _yield_lock:
        if not _yielded:
            return
        if playing:
            _silent_since = None            # audio again — the hold restarts
            return
        now = time.monotonic()
        if _silent_since is None:
            _silent_since = now
            return
        if now - _silent_since < _UNDUCK_HOLD:
            return
        _yielded, _silent_since = False, None
    try:
        morpheus.unduck()
    except Exception as e:  # noqa: BLE001 — a stuck pipe must not reach the UI
        log.error("Harmonia: could not restore Morpheus: %s", e)


def shutdown() -> None:
    """Flush the buffer and close the owned stream, giving Morpheus its volume
    back first — felhaven calls this BEFORE morpheus.shutdown(), so mpv is still
    alive to hear it. Safe to call even if the stream never opened; idempotent."""
    stop()
    _release_yield()
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
