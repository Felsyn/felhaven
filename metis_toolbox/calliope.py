"""
calliope.py — Calliope, the Narrator
====================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Turn a string of text into speech and play it. Nothing else.
             Calliope ORIGINATES nothing and DECIDES nothing — it does not know
             what Pythia said, why, or whether it should be spoken. It is handed
             already-generated, fully-trusted text and reads it aloud on demand.

Contract:    speak(text) -> None
                 Split `text` into sentences and enqueue them for a background
                 worker to synthesize + play in order. NON-BLOCKING: returns at
                 once, so speech on a long answer starts after just the first
                 sentence is synthesized (not the whole paragraph), and the GUI
                 can feed sentences in as an LLM streams them. NEVER raises: a
                 missing model file, a busy audio device, or a synthesis error
                 all degrade to a logged no-op.
             synthesize(text) -> np.ndarray | None
                 The pure, hardware-free half: text in, 24 kHz float32 PCM out
                 (or None on any failure). This is the hermetic-testable seam —
                 mock _load_model() and no audio device or model file is touched.

Shared seam:  synthesize(), take_chunk(), save_wav(), load_wav() and SAMPLE_RATE
              are the public kokoro-audio helpers. Echo (tools/echo.py) reuses
              them so there is ONE source of truth for how kokoro PCM is chunked
              and written to a WAV — Echo owns no model instance and no second
              WAV writer (the same "one source of truth" spirit as the singleton
              model). Do not re-privatise them.
             prewarm() -> Thread
                 Load the kokoro-onnx model on a background thread at startup so
                 the first spoken line isn't delayed by the one-time model load.
             stop() -> None
                 Drop any queued sentences and halt current playback (barge-in,
                 e.g. when a new question is asked).

Auto-speak:  Calliope also owns the single source of truth for the "read every
             answer aloud" toggle (auto_speak_enabled / set_auto_speak /
             toggle_auto_speak). The header narration lamp flips it; the home
             panel reads it. The default lives in calliope_config.json, never in
             code.

Config:      calliope_config.json beside this file — voice, speed, lang, model
             paths, and the auto_speak default. Voice-switching later is a config
             edit, not a code change (the Pheme / Morpheus precedent).

Model files: kokoro-onnx needs two binaries (a *.onnx voice model and
             voices-v1.0.bin), downloaded separately and gitignored. Paths are
             read from the config and resolved relative to this file. See
             README_PANTHEON/Vox/Calliope.md for the one-time download step.

Upstream:    panels/home_panel.py (per-response speak button + auto-speak),
             panels/narrator_panel.py (the header toggle)
Downstream:  kokoro-onnx (synthesis) → sounddevice (playback)

Requires:    kokoro-onnx, sounddevice, numpy. Stdlib otherwise.
"""

import hashlib
import json
import logging
import queue
import random
import re
import threading
import time
import wave
from pathlib import Path
from typing import Any, Optional, cast

import numpy as np

log = logging.getLogger("METIS.calliope")

_APP_DIR = Path(__file__).resolve().parent
_CONFIG_FILE = _APP_DIR / "config" / "calliope_config.json"
_FILLER_DIR = _APP_DIR / "calliope_fillers"

# ── Config (loaded once at import; fail soft to sane defaults) ─────────────────

# These mirror the shipped calliope_config.json (and the table in specs/calliope.md
# §3): the fallback is only "a working narrator" if it names the model that is
# actually on disk — fp32, not int8 — and keeps the fp32-era latency tuning.
_DEFAULTS: dict[str, Any] = {
    "voice": "af_nicole",
    "speed": 1.0,
    "lang": "en-us",
    "model_path": "kokoro_models/kokoro-v1.0.onnx",
    "voices_path": "kokoro_models/voices-v1.0.bin",
    "auto_speak": False,
    "filler_delay_ms": 1000,
    "prebuffer_chunks": 2,
    "first_chunk_max_chars": 70,
    "chunk_max_chars": 70,
}


def _load_config() -> dict[str, Any]:
    """Read calliope_config.json. Never raises: on any error fall back to the
    defaults so a missing/garbled config degrades to a working narrator rather
    than crashing the GUI at import."""
    cfg = dict(_DEFAULTS)
    try:
        with open(_CONFIG_FILE, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        log.warning("Calliope: %s not found — using defaults.", _CONFIG_FILE.name)
    except Exception as e:  # noqa: BLE001 — a bad config must not break import
        log.error("Calliope: could not read config (%s) — using defaults.", e)
    return cfg


_CONFIG = _load_config()

# Auto-speak is runtime-mutable state seeded from the config default.
_auto_speak: bool = bool(_CONFIG.get("auto_speak", False))


def _resolve(path_str: str) -> Path:
    """Resolve a config path against the app dir unless it's absolute. Anchored
    at the app root (not config/), so kokoro_models/… paths keep resolving there."""
    p = Path(path_str)
    return p if p.is_absolute() else (_APP_DIR / p)


# ── kokoro-onnx model (lazy singleton, loaded once and held warm) ─────────────

_model: Any = None
_model_tried = False           # so a failed load isn't retried on every call
_model_lock = threading.Lock()
_synth_lock = threading.Lock()  # serialize model.create() (synth worker vs filler gen)


def _load_model() -> Any:
    """Return the shared Kokoro instance, loading it on first use. Returns None
    (once) if kokoro-onnx isn't installed or the model files are missing —
    loading is attempted a single time, then the None result is cached so a
    broken install degrades to silence instead of stalling every speak()."""
    global _model, _model_tried
    with _model_lock:
        if _model is not None or _model_tried:
            return _model
        _model_tried = True
        model_path = _resolve(_CONFIG["model_path"])
        voices_path = _resolve(_CONFIG["voices_path"])
        if not model_path.is_file() or not voices_path.is_file():
            log.error(
                "Calliope: model files missing (model=%s exists=%s, voices=%s "
                "exists=%s) — narration disabled. See Calliope.md for the "
                "download step.",
                model_path, model_path.is_file(), voices_path, voices_path.is_file(),
            )
            return None
        try:
            from kokoro_onnx import Kokoro
            log.info("Calliope: loading kokoro-onnx (%s)...", model_path.name)
            _model = Kokoro(str(model_path), str(voices_path))
            log.info("Calliope: narrator ready (voice=%s).", _CONFIG["voice"])
        except Exception as e:  # noqa: BLE001 — import/model failure = silent narrator
            log.error("Calliope: failed to load kokoro-onnx: %s", e)
            _model = None
        return _model


def prewarm() -> threading.Thread:
    """On a background thread at startup: load the model (so the first speak()
    isn't stalled by the one-time ONNX load) and render any missing filler audio
    to disk. Returns the thread (mainly so tests can join it)."""
    def _warm() -> None:
        _load_model()
        generate_fillers()
    t = threading.Thread(target=_warm, name="calliope-prewarm", daemon=True)
    t.start()
    return t


# ── Synthesis (pure: text -> PCM, no audio hardware) ──────────────────────────

# Markdown / symbols that read badly aloud. Pythia is prompted for plain prose,
# but strip defensively so a stray "*" is never spoken as "asterisk".
_STRIP_CHARS = re.compile(r"[*#`_>~|]+")


def _clean_for_speech(text: str) -> str:
    """Drop markdown noise and collapse whitespace so the voice reads cleanly."""
    return re.sub(r"\s+", " ", _STRIP_CHARS.sub("", text)).strip()


def synthesize(text: str) -> Optional[np.ndarray]:
    """Render `text` to 24 kHz float32 PCM samples, or None on any failure.
    Hardware-free — this is the half the hermetic test exercises."""
    cleaned = _clean_for_speech(text) if text else ""
    if not cleaned:
        return None
    model = _load_model()
    if model is None:
        return None
    try:
        t0 = time.perf_counter()
        with _synth_lock:       # onnxruntime is thread-safe, but the tokenizer isn't
            audio, _sample_rate = model.create(
                cleaned,
                voice=_CONFIG["voice"],
                speed=float(_CONFIG["speed"]),
                lang=_CONFIG["lang"],
            )
        log.info(
            "Calliope: synth %d chars -> %.2fs audio in %.0fms",
            len(cleaned), len(audio) / SAMPLE_RATE, (time.perf_counter() - t0) * 1000,
        )
        # kokoro-onnx is untyped, so model.create() is Any; the contract is a
        # float32 ndarray. cast keeps the public return type honest (no runtime op).
        return cast(np.ndarray, audio)
    except Exception as e:  # noqa: BLE001 — a synthesis bug must not crash the GUI
        log.error("Calliope: synthesis failed: %s", e)
        return None


# ── Playback (serialized; one utterance at a time) ────────────────────────────

SAMPLE_RATE = 24000           # kokoro-onnx emits 24 kHz natively
_play_lock = threading.Lock()


def _play(audio: np.ndarray) -> None:
    """Play PCM through the default output device. Never raises: a busy or
    absent device degrades to a logged no-op. sounddevice is imported lazily so
    the pure synthesize() path (and its test) never needs an audio backend."""
    try:
        import sounddevice as sd  # type: ignore[import-untyped]
    except Exception as e:  # noqa: BLE001
        log.error("Calliope: sounddevice unavailable (%s) — cannot play.", e)
        return
    with _play_lock:            # never overlap two utterances on one device
        try:
            sd.play(audio, SAMPLE_RATE)
            sd.wait()
        except Exception as e:  # noqa: BLE001 — device busy / no output device
            log.error("Calliope: playback failed: %s", e)


# ── Sentence chunking ─────────────────────────────────────────────────────────
# Split on whitespace that follows a sentence ender. The lookbehind keeps the
# ender with its sentence, and requiring trailing whitespace means "3.5" or
# "v1.0" is never cut mid-number.
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+')


def _split_sentences(text: str) -> list[str]:
    """Break `text` into speakable sentence chunks (empty list for blank text)."""
    if not text or not text.strip():
        return []
    return [chunk.strip() for chunk in _SENTENCE_SPLIT.split(text.strip())
            if chunk.strip()]


# ── Two-stage playback pipeline ───────────────────────────────────────────────
# kokoro-onnx on CPU synthesizes SLOWER than real time (RTF > 1) and pays a big
# fixed cost per create() call, so the naive "synthesize one sentence, play it,
# repeat" stalls for a whole synth before every sentence. Instead:
#   • a SYNTH worker keeps a text buffer, COALESCES whatever has piled up, and
#     takes a chunk (split at a natural boundary) to synthesize into ONE
#     create() call — amortizing the per-call overhead — then pushes the PCM
#     onto a small audio buffer, so synthesis runs AHEAD of playback;
#   • a PLAY worker drains that buffer back-to-back, so once audio exists it
#     plays with no synth gap between chunks.
# The FIRST chunk of a turn is capped SMALL (_FIRST_CHUNK_MAX_CHARS) and split at
# a clause boundary, so a long opening sentence doesn't make you wait its whole
# synth before the first word — speech starts in a couple of seconds. Later
# chunks grow to _CHUNK_MAX_CHARS. An epoch counter gives clean barge-in: stop()
# bumps it, and any synth already in flight (or buffered text) is discarded.

_FIRST_CHUNK_MAX_CHARS = int(_CONFIG.get("first_chunk_max_chars", _DEFAULTS["first_chunk_max_chars"]))
_CHUNK_MAX_CHARS = int(_CONFIG.get("chunk_max_chars", _DEFAULTS["chunk_max_chars"]))
# Lookahead depth: hold the answer until this many chunks are synthesized before
# playback starts, so a lead exists before the first word. Because synth is now
# faster than real time (RTF < 1), once the lead exists it only grows — gaps
# vanish structurally. The filler covers this wait. Only applies to auto-speak
# turns (a filler is playing); the manual speak button plays immediately.
_PREBUFFER_CHUNKS = int(_CONFIG.get("prebuffer_chunks", _DEFAULTS["prebuffer_chunks"]))
_PREBUFFER_TIMEOUT = 6.0       # s — safety cap so prebuffer can never hang
_AUDIO_BUFFER = 16             # max synthesized chunks held ahead of playback

_text_queue: "queue.Queue[str]" = queue.Queue()
# Audio items are (epoch, pcm, is_filler); fillers bypass the prebuffer gate.
_audio_queue: "queue.Queue[tuple[int, np.ndarray, bool]]" = queue.Queue(maxsize=_AUDIO_BUFFER)
_workers_started = False
_workers_lock = threading.Lock()
_epoch = 0
_epoch_lock = threading.Lock()
_real_audio_queued = False     # has the synth worker produced answer audio this turn?

# Prebuffer state, guarded by _cond: how many answer chunks have been synthesized
# this turn, whether the turn's text is fully in, and whether a filler is masking.
_cond = threading.Condition()
_answer_produced = 0
_turn_ended = False
_filler_active = False

# Boundaries to break a long chunk at, best first: sentence end, then a clause
# mark, then any space. Each keeps its punctuation with the left side.
_CHUNK_BOUNDARIES = (
    re.compile(r'[.!?]["\')\]]?\s'),
    re.compile(r'[,;:]\s'),
    re.compile(r'\s'),
)


def take_chunk(text: str, cap: int) -> tuple[str, str]:
    """Split `text` into (chunk, rest): a chunk of at most ~`cap` chars broken at
    the latest natural boundary before the cap, and the remainder. If the text
    already fits, the whole thing is the chunk. A single over-long token with no
    boundary is hard-cut at `cap`."""
    text = text.strip()
    if len(text) <= cap:
        return text, ""
    window = text[:cap]
    for boundary in _CHUNK_BOUNDARIES:
        matches = list(boundary.finditer(window))
        if matches:
            split = matches[-1].end()
            return text[:split].strip(), text[split:].strip()
    return text[:cap].strip(), text[cap:].strip()


def _synth_worker() -> None:
    """Keep a text buffer, coalesce the backlog, take a chunk (small + clause-split
    for the first of a turn, larger after), synthesize, and buffer the PCM ahead
    of playback. Stale text/results (a barged-in turn) are dropped."""
    with _epoch_lock:
        worker_epoch = _epoch
    buf = ""
    first_done = False
    while True:
        if buf:
            with _epoch_lock:
                ep = _epoch
            if ep != worker_epoch:            # barged in while holding leftover text
                worker_epoch, buf, first_done = ep, "", False
                continue
        else:
            buf = _text_queue.get()           # block until there's something to say
            with _epoch_lock:
                ep = _epoch
            if ep != worker_epoch:            # new turn: reset the fast-start cap
                worker_epoch, first_done = ep, False
        # Coalesce whatever else has piled up into the buffer.
        while True:
            try:
                buf = (buf + " " + _text_queue.get_nowait()).strip()
            except queue.Empty:
                break
        cap = _CHUNK_MAX_CHARS if first_done else _FIRST_CHUNK_MAX_CHARS
        chunk, buf = take_chunk(buf, cap)
        first_done = True
        if not chunk:
            continue
        audio = synthesize(chunk)
        if audio is None:
            continue
        global _real_audio_queued
        with _epoch_lock:
            stale = worker_epoch != _epoch
            if not stale:
                _real_audio_queued = True     # real answer audio has begun this turn
        if stale:
            continue
        _audio_queue.put((worker_epoch, audio, False))    # blocks if full (backpressure)
        with _cond:                           # release the prebuffer gate as the lead builds
            global _answer_produced
            _answer_produced += 1
            _cond.notify_all()


def _play_worker() -> None:
    """Drain the audio buffer, playing each chunk back-to-back. Fillers play at
    once; the FIRST answer chunk of a turn waits for a prebuffered lead (while the
    filler covers the silence), after which RTF < 1 keeps the buffer ahead. Skips
    chunks left over from a barged-in turn (epoch mismatch)."""
    started_epoch = -1
    while True:
        ep, audio, is_filler = _audio_queue.get()
        if ep != _epoch:                      # stale (barged-in) — drop it
            continue
        if not is_filler and started_epoch != ep:
            _await_prebuffer(ep)              # build a lead before the first word
            if ep != _epoch:                  # barged in while we waited
                continue
            started_epoch = ep
        _play(audio)


def _await_prebuffer(ep: int) -> None:
    """Block until _PREBUFFER_CHUNKS answer chunks are synthesized (or the turn's
    text is fully in, or a safety timeout) — but only when a filler is masking the
    wait. Without a filler (the manual speak button) playback starts immediately."""
    deadline = time.monotonic() + _PREBUFFER_TIMEOUT
    with _cond:
        if not _filler_active:
            return
        while (_answer_produced < _PREBUFFER_CHUNKS and not _turn_ended
               and ep == _epoch and time.monotonic() < deadline):
            _cond.wait(timeout=0.2)


def _ensure_workers() -> None:
    """Start the synth + play workers on first use (daemons; live for the app)."""
    global _workers_started
    with _workers_lock:
        if not _workers_started:
            threading.Thread(target=_synth_worker, name="calliope-synth",
                             daemon=True).start()
            threading.Thread(target=_play_worker, name="calliope-play",
                             daemon=True).start()
            _workers_started = True


# ── Public API ────────────────────────────────────────────────────────────────

def speak(text: str) -> None:
    """Enqueue `text` (split into sentences) for the pipeline to speak, in order.
    NON-BLOCKING — returns at once; never raises. Feed it a whole answer (speak
    button) or one sentence at a time (streaming); the synth worker coalesces
    whatever has piled up into efficient create() calls."""
    sentences = _split_sentences(text)
    if not sentences:
        return
    _ensure_workers()
    for sentence in sentences:
        _text_queue.put(sentence)


def _drain(q: "queue.Queue[Any]") -> None:
    """Empty a queue without blocking."""
    try:
        while True:
            q.get_nowait()
    except queue.Empty:
        pass


def stop() -> None:
    """Barge-in: bump the epoch (so in-flight synthesis is discarded), drop
    everything queued, and halt the current utterance. Used when a new question
    is asked so stale narration doesn't pile up. Never raises."""
    global _epoch, _real_audio_queued, _answer_produced, _turn_ended, _filler_active
    with _epoch_lock:
        _epoch += 1
        _real_audio_queued = False
    with _cond:                    # reset the prebuffer gate for the new turn
        _answer_produced = 0
        _turn_ended = False
        _filler_active = False
        _cond.notify_all()
    _drain(_text_queue)
    _drain(_audio_queue)
    try:
        import sounddevice as sd
        sd.stop()
    except Exception as e:  # noqa: BLE001 — nothing playing / no backend
        log.debug("Calliope: stop() no-op (%s)", e)


# ── Filler audio ──────────────────────────────────────────────────────────────
# kokoro on CPU is slower than real time, so the opening of an answer always has
# some synth latency. Rather than fight it, we mask it: a short filler line
# ("Initiating Cogitator. Please wait.") is synthesized ONCE at startup, cached
# to disk as a WAV, and played INSTANTLY (no synthesis) when a question is asked
# — buying the real answer a few seconds to generate and get the pipeline ahead.
# Filler phrases live in the config; the cache filename hashes phrase+voice+speed
# so changing any of them regenerates cleanly.


def _filler_path(phrase: str) -> Path:
    # Include the model so switching models (e.g. int8 -> fp32) regenerates fillers.
    key = f"{phrase}|{_CONFIG['voice']}|{_CONFIG['speed']}|{_CONFIG['model_path']}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return _FILLER_DIR / f"filler_{digest}.wav"


def save_wav(path: Path, pcm: np.ndarray) -> None:
    """Write float32 [-1, 1] PCM as a 24 kHz mono 16-bit WAV (stdlib only)."""
    ints = np.clip(pcm * 32767.0, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(ints.tobytes())


def load_wav(path: Path) -> np.ndarray:
    """Read a 16-bit WAV back to float32 [-1, 1] PCM."""
    with wave.open(str(path), "rb") as w:
        frames = w.readframes(w.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0


def generate_fillers() -> None:
    """Synthesize any not-yet-cached filler phrase to disk. Called from prewarm();
    each phrase is rendered once, then reused across launches. Never raises."""
    phrases = _CONFIG.get("fillers") or []
    if not phrases:
        return
    try:
        _FILLER_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        log.error("Calliope: could not create filler dir (%s)", e)
        return
    for phrase in phrases:
        path = _filler_path(phrase)
        if path.is_file():
            continue
        audio = synthesize(phrase)
        if audio is None:
            continue
        try:
            save_wav(path, audio)
            log.info("Calliope: cached filler %r", phrase)
        except Exception as e:  # noqa: BLE001
            log.error("Calliope: could not save filler %r (%s)", phrase, e)


def filler_delay_ms() -> int:
    """How long the GUI should wait after Enter before firing the filler (a beat
    so it doesn't react the instant you hit the key). Config-driven, ms."""
    return int(_CONFIG.get("filler_delay_ms", _DEFAULTS["filler_delay_ms"]))


def speak_filler() -> None:
    """Play a random PRE-RENDERED filler line immediately (no synthesis), to cover
    the wait while the real answer generates. No-op if no filler is cached yet, or
    if the real answer's audio has already begun (a fast answer beat us to it — a
    late filler must never jump ahead of real speech). NON-BLOCKING and never
    raises — safe to call from the UI thread."""
    with _epoch_lock:
        if _real_audio_queued:
            return
    phrases = _CONFIG.get("fillers") or []
    ready = [p for p in phrases if _filler_path(p).is_file()]
    if not ready:
        return
    try:
        pcm = load_wav(_filler_path(random.choice(ready)))
    except Exception as e:  # noqa: BLE001
        log.error("Calliope: could not load filler (%s)", e)
        return
    _ensure_workers()
    with _cond:
        global _filler_active
        _filler_active = True                 # arms the prebuffer gate to mask the wait
    with _epoch_lock:
        ep = _epoch
    try:
        _audio_queue.put_nowait((ep, pcm, True))   # is_filler=True: plays immediately
    except queue.Full:
        pass


def end_turn() -> None:
    """Signal that the answer's text is fully streamed in — releases the prebuffer
    gate so a short answer (fewer than _PREBUFFER_CHUNKS chunks) plays without
    waiting. Called by the GUI when Pythia finishes. Never raises."""
    global _turn_ended
    with _cond:
        _turn_ended = True
        _cond.notify_all()


# ── Auto-speak toggle (single source of truth) ────────────────────────────────

def auto_speak_enabled() -> bool:
    """True if Pythia's answers should be read aloud automatically."""
    return _auto_speak


def set_auto_speak(value: bool) -> None:
    """Set the auto-speak flag (runtime only — does not rewrite the config)."""
    global _auto_speak
    _auto_speak = bool(value)


def toggle_auto_speak() -> bool:
    """Flip auto-speak and return the new state."""
    set_auto_speak(not _auto_speak)
    return _auto_speak


# ── Standalone demo ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"[Calliope] config={_CONFIG_FILE.name} voice={_CONFIG['voice']} "
          f"speed={_CONFIG['speed']} auto_speak={_auto_speak}")
    print("[Calliope] speaking a two-sentence line (chunked)...")
    speak("Ex tenebris surgit lumen posteris. Calliope is online.")
    time.sleep(0.2)
    while not (_text_queue.empty() and _audio_queue.empty()):
        time.sleep(0.2)          # let the pipeline drain (demo only; speak() doesn't block)
    time.sleep(3)                # let the final chunk finish playing
    print("[Calliope] done.")
