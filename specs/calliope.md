# specs/calliope.md — Calliope, the Narrator

> Metis Toolbox | Anti-Legion: ONE JOB
> **Job:** Turn a string of text into speech and play it. Nothing else.

Calliope is the narrator: `text → speech`. She **originates nothing** and
**decides nothing** — she is handed Pythia's already-generated, fully-trusted
answer text and reads it aloud on demand via [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx).
She lives inside the toolbox at `metis_toolbox/calliope.py` (beside `pythia.py`)
and is called **in-process** by the Felhaven GUI. She is not a registry member —
no `TOOL_DEFINITION`, no `handle()`, no `fetch()`.

> **History:** Calliope used to be `narrate(tool_name, result_dict) -> str`, the
> tail of a spoken voice loop (`Mic → VAD → Whisper → Apollo → dispatch →
> calliope.narrate → Kokoro`). That voice-**input** layer — `Metis.py`,
> `apollo.py`, `metis_config.py`, `tools/metis.py`, the old `MetisLamp` — was
> **removed**. Calliope was refactored to output-only TTS driven by kokoro-onnx
> (onnxruntime, no torch). See `README_PANTHEON/Calliope.md`.

---

## 1. Contract

```python
def speak(text: str) -> None            # enqueue for the pipeline (NON-blocking)
def speak_filler() -> None              # instant pre-rendered filler, masks the opening
def stop() -> None                      # barge-in: discard queued/in-flight, halt playback
def end_turn() -> None                  # answer fully streamed → release the prebuffer gate
def prewarm() -> threading.Thread       # load model + render fillers at startup
def synthesize(text: str) -> numpy.ndarray | None   # pure text -> PCM (test seam)
def auto_speak_enabled() -> bool
def set_auto_speak(value: bool) -> None
def toggle_auto_speak() -> bool
```

- **`speak(text)`** — split into sentences and **enqueue** them for the background
  pipeline (§2a). **Non-blocking** — returns at once. **Never raises**; a missing
  model, busy device, or synth error is a logged no-op. Feed a whole answer (speak
  button) or one sentence at a time as an LLM streams (home panel).
- **`synthesize(text)`** — the pure, hardware-free half: text → 24 kHz float32 PCM,
  or `None` on any failure. Touches no audio device. The hermetic-test seam.
- **`speak_filler()`** — enqueue a random pre-rendered filler WAV **instantly**
  (no synthesis) so the first *sound* lands immediately while the real answer
  generates. Self-cancels if the real answer's audio already began.
- **`stop()` / `end_turn()`** — barge-in and turn-complete signals for the pipeline
  (§2a). `stop()` fires on a new question; `end_turn()` when the answer's text is
  fully in.
- **auto-speak flag** — single source of truth for "read every answer aloud." The
  header `NarratorLamp` flips it; `home_panel` reads it. Seeded from config;
  runtime-mutable, does **not** rewrite the config.

Calliope speaks; she does not decide *what* to speak. The "speak this" trigger,
sentence boundaries, and the auto-speak policy live in the GUI (Anti-Legion).

---

## 2. Synthesis

`synthesize()` lazily loads a single `kokoro_onnx.Kokoro` instance on first use
and holds it warm (loading the ONNX model is expensive). The load is attempted
**once**; if kokoro-onnx isn't installed or the model files are missing, the
`None` result is cached so a broken install degrades to silence instead of
retrying on every call. Synthesis calls:

```python
audio, _sr = model.create(text, voice=CFG.voice, speed=CFG.speed, lang=CFG.lang)
```

kokoro-onnx emits **24 kHz** float32 PCM natively; that's the playback rate.
`model.create()` runs under a lock (`_synth_lock`) so startup filler generation
can't race the pipeline's synth worker on the tokenizer.

**Model choice matters for latency:** on the target CPU the **fp32** model runs
at RTF **0.44** (faster than real time) vs int8 at **1.54** — int8's QDQ overhead
is the bottleneck, so fp32 is the default despite being larger. DirectML/GPU was
rejected (kokoro's `ConvTranspose` op is unsupported there).

---

## 2a. Playback pipeline (the gapless machinery)

`speak()` does not synthesize inline. It enqueues sentences; two daemon workers do
the rest:

- **Synth worker** — pulls a sentence, **coalesces** any backlog, **takes a chunk**
  (capped at `first_chunk_max_chars` for the first of a turn, `chunk_max_chars`
  after, split at a clause/word boundary), synthesizes it, and pushes PCM to a
  bounded audio queue tagged `is_filler=False`. Because synth is faster than real
  time, it runs **ahead** of playback.
- **Play worker** — drains the audio queue back-to-back. Fillers play immediately;
  the **first answer chunk** waits for a lead of `prebuffer_chunks` (the *prebuffer
  gate*) before starting, so a lead exists before the first word — after which
  RTF < 1 keeps the buffer full. A safety timeout and `end_turn()` release the gate
  for short answers.
- **Barge-in** — an epoch counter (`stop()` bumps it) invalidates queued text,
  buffered audio, and in-flight synth; stale chunks are dropped, not played over
  the next answer.
- **Fillers** — phrases from config are synthesized once at startup (`prewarm()` →
  `generate_fillers()`) and cached as WAVs (hash of phrase+voice+speed+model). At
  turn start `speak_filler()` plays one instantly to mask the opening; the cache is
  gitignored and self-heals on config change.

Small chunks (~70 chars) are the key knob: each chunk's synth finishes inside the
previous chunk's playback, so the pipeline never underruns. Net: first sound
~0.01 s, ~0 s of gaps on a 30 s+ answer, on CPU.

---

## 3. Config over code

`calliope_config.json` (beside the module) holds the tunables so voice-switching
is a config edit, not a code change:

| Key | Default | Meaning |
|---|---|---|
| `voice` | `af_nicole` | kokoro-onnx voice id (v1.0 ships 26; just a string) |
| `speed` | `1.0` | playback rate multiplier |
| `lang` | `en-us` | phonemizer language |
| `model_path` | `kokoro_models/kokoro-v1.0.onnx` | ONNX model — fp32 (fastest on CPU here); relative → resolved against the module dir |
| `voices_path` | `kokoro_models/voices-v1.0.bin` | voice-styles binary |
| `auto_speak` | `false` | default for the narration toggle |
| `filler_delay_ms` | `1000` | beat after Enter before the filler fires |
| `prebuffer_chunks` | `2` | lookahead depth: chunks of lead before the answer plays |
| `first_chunk_max_chars` | `70` | cap for the first chunk of a turn (fast start) |
| `chunk_max_chars` | `70` | cap for steady chunks (small = pipeline stays ahead) |
| `fillers` | *(list)* | phrases pre-rendered to WAV and played to mask the opening |

A missing or garbled config falls back to these defaults (logged), never crashes
import. Model + filler binaries are gitignored — see `README_PANTHEON/Calliope.md`
for the download step.

---

## 4. Tests (see `metis_toolbox/tests/test_calliope.py`)

Hermetic — **no audio hardware, no model files, no network:**

- `synthesize()` returns the model's PCM (kokoro-onnx replaced by a fake whose
  `.create()` returns canned samples), and passes the configured voice/speed/lang.
- Empty/whitespace text → `None`, and loads no model.
- Model unavailable → `None`; a synthesis exception → `None` (no raise).
- `speak()` calls `_play` on success, skips it when synthesis returns `None`, and
  never raises even with the model unavailable.
- `_play()` swallows an audio-device error (driven against a stub `sounddevice`
  whose `play()` raises) — proves graceful degradation.
- The auto-speak flag: default from config, `set`/`toggle` behave.
