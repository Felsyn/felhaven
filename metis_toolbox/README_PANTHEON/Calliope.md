# Calliope — The Narrator

*Anti-Legion: ONE JOB*

Calliope turns **text into speech**. That is the whole job: she is handed a
string — Pythia's already-generated, typed-path, fully-trusted answer — and reads
it aloud on demand through [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx).
She originates nothing, routes nothing, and decides nothing about *what* to
speak; the GUI decides *when*, Calliope only makes sound.

She lives inside the toolbox at `metis_toolbox/calliope.py` (beside `pythia.py`),
so Felhaven calls her **in-process** on its existing `sys.path` — no subprocess,
no mic, no models loaded until the first time you actually speak.

## What she replaced

Calliope used to be the tail end of a **voice loop**: `Mic → Silero VAD → Whisper
(STT) → Apollo (keyword router) → dispatch() → calliope.narrate(result) → Kokoro
→ Speaker`, all in a separate `Metis.py` process, with a `tools/metis.py`
supervisor and a header flame that toggled it.

That entire voice-**input** layer was **removed**. The reasons, worth keeping:

- **The dependency weight was `kokoro` (the pip package), not the loop.** It pulls
  torch + transformers (gigabytes). Swapping to **kokoro-onnx** (onnxruntime, no
  torch) is the real diet.
- **Output-only deletes a whole trust layer.** Apollo's frozen 9-tool allowlist,
  `apollo_intents.json`, and the "third registry" all existed because *spoken
  input* was an untrusted oracle that could invoke tools. With no voice input
  there is **no untrusted command surface left** — nothing to route, nothing to
  guard. See [the top-level README](README.md#two-ways-in-two-registries).
- **Fit to the rig.** The dev PC (Felhaven) and the gaming PC are separate
  machines with separate audio. A quiet narrator on the dev box is on-theme
  (*Ex tenebris surgit lumen posteris*), not a compromise.

So `Metis.py`, `apollo.py`, `apollo_intents.json`, `metis_config.py`,
`tools/metis.py`, and the old `MetisLamp` are **gone**, and `narrate(tool_name,
result)` became `speak(text)`.

## Contract

| Function | Role |
|---|---|
| `speak(text) -> None` | Split `text` into sentences and **enqueue** them for a background pipeline to synthesize + play in order. **Non-blocking** — returns at once. Feed a whole answer (speak button) or one sentence at a time (streaming). **Never raises** — a missing model, busy device, or synth error is a logged no-op. |
| `synthesize(text) -> np.ndarray \| None` | The **pure, hardware-free** half: text → 24 kHz float32 PCM, or `None` on failure. The hermetic-testable seam. |
| `speak_filler()` | Play a random pre-rendered **filler** line instantly (no synthesis) to mask the opening latency. Self-cancels if the real answer's audio already started. |
| `stop()` | Barge-in: discard queued text/audio + in-flight synth (epoch bump) and halt playback. Called when a new question is asked. |
| `end_turn()` | Signal the answer's text is fully streamed in — releases the prebuffer gate so a short answer plays without waiting. |
| `prewarm() -> Thread` | Load the model **and** render any missing filler WAVs on a background thread at startup. |
| `auto_speak_enabled()` / `set_auto_speak()` / `toggle_auto_speak()` | Single source of truth for the "read every answer aloud" flag. The header lamp flips it; the home panel reads it. |

Anti-Legion check: Calliope speaks; she does not decide *what* to speak. The
"speak this answer" trigger, the sentence boundaries, and the auto-speak policy
live in the **GUI** (`home_panel.py` + `narrator_panel.py`); Calliope owns the
flag's value, the pipeline, and the sound.

## Latency: how it stays gapless on CPU

kokoro on CPU is the tension — synthesis quality vs. real-time playback. Getting
narration to keep up took a stack of moves, each measured:

- **fp32, not int8.** Counter-intuitively the **fp32** model is ~3.5× *faster*
  than int8 on this CPU (RTF **0.44** vs 1.54) — int8's quantize/dequantize
  overhead dominates. fp32 synthesizes faster than real time, which is what makes
  gapless playback possible at all. (fp16 + DirectML were tried and rejected:
  DirectML can't run kokoro's `ConvTranspose` op.)
- **Two-stage pipeline.** A *synth* worker coalesces queued sentences, takes a
  small chunk, synthesizes it, and pushes PCM to a bounded buffer; a *play* worker
  drains that buffer back-to-back. Synthesis runs **ahead** of playback, and
  because RTF < 1 the lead only grows — gaps vanish structurally, not by luck.
- **Small chunks (`chunk_max_chars` ≈ 70).** Each chunk's synth finishes well
  inside the previous chunk's playback, so the pipeline never underruns. (With
  fast synth, *small* chunks win; the opposite of a slow engine.)
- **Prebuffer / lookahead (`prebuffer_chunks`).** The first answer chunk waits for
  a small lead to build before playback starts — a safety net against bursty LLM
  token streaming. The filler masks this wait; `end_turn()` releases it for short
  answers.
- **Filler masking.** The opening's unavoidable first-chunk latency is hidden by
  an instant pre-rendered filler ("Initiating Cogitator. Please wait."), so the
  first *sound* is ~0.01 s even though the first real word is a beat behind.
- **Streaming.** Pythia streams tokens; the home panel feeds completed sentences
  to `speak()` as they form, so synthesis overlaps generation.

Net measured result: first sound ~0.01 s, and **~0 s of gaps** on a long
(30 s+) streamed answer — on CPU, no GPU.

## The GUI triggers

- **Per-response button.** Every Pythia answer gets a clickable `▶ speak aloud`
  control in the transcript (`panels/home_panel.py`). Click it to hear that one
  answer.
- **Global auto-speak toggle.** The header lamp (`panels/narrator_panel.py` →
  `NarratorLamp`) is a **speaker glyph**: lit (amber, with sound waves) = every
  answer is read aloud the moment it arrives; dim (grey, muted) = silent. It
  repurposes the retired voice lamp's header slot and lit/dim pattern — the flame
  became a speaker. It holds no state of its own; it flips Calliope's flag and
  re-reads it.

Both paths hand text to `calliope.speak()`, which enqueues it for the pipeline
(non-blocking), so a slow synthesis never freezes the window and a TTS failure is
silent, never a broken chat.

## Config over code

Voice, speed, model paths, the filler phrases, and every latency knob live in
`calliope_config.json` beside the module — tuning is a config edit, not a code
change (the Pheme / Morpheus precedent):

```json
{
  "voice": "af_nicole",
  "speed": 1.0,
  "lang": "en-us",
  "model_path": "kokoro_models/kokoro-v1.0.onnx",
  "voices_path": "kokoro_models/voices-v1.0.bin",
  "auto_speak": false,
  "filler_delay_ms": 1000,
  "prebuffer_chunks": 2,
  "first_chunk_max_chars": 70,
  "chunk_max_chars": 70,
  "fillers": ["Initiating Cogitator. Please wait.", "..."]
}
```

`af_nicole` is the intimate/whisper voice. kokoro-onnx v1.0 ships 26 voices; the
voice id is just a string passed at synthesis time, so seeding the config with
alternates (e.g. `af_heart` for a future dropdown) is a phase-2 edit. The latency
knobs (`chunk_max_chars`, `prebuffer_chunks`, `filler_delay_ms`) are documented in
[Latency](#latency-how-it-stays-gapless-on-cpu) above.

## Model files (one-time download)

kokoro-onnx needs two binaries, downloaded separately and **gitignored**. Put them
where `calliope_config.json` points (`kokoro_models/` by default):

```
kokoro_models/
  kokoro-v1.0.onnx         # ~325 MB — fp32; FASTEST on CPU here (see Latency)
  voices-v1.0.bin          # ~27 MB  — the voice styles
```

Download from the kokoro-onnx model release:

```
https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

(int8 ~88 MB and fp16 ~169 MB exist and are smaller, but on this CPU int8 is
~3.5× *slower* — fp32 is the right pick. Swap the filename in the config to
change.) `espeakng-loader` bundles espeak-ng, so a system-level espeak-ng install
is generally **not** required on Windows. If the model files are missing, Calliope
logs it once and every `speak()` is a silent no-op — the dashboard runs fine, it
just doesn't talk.

## Files

| File | Purpose |
|---|---|
| `calliope.py` | Text → speech: the synth/play pipeline, fillers, prebuffer, auto-speak flag. |
| `calliope_config.json` | Voice, speed, model paths, filler phrases, latency knobs. |
| `panels/narrator_panel.py` → `NarratorLamp` | Header speaker glyph; toggles auto-speak. |
| `panels/home_panel.py` | Streams the answer, feeds sentences to `speak()`, fires the filler, the `▶ speak aloud` button. |
| `kokoro_models/` *(gitignored)* | The ONNX voice model + voices binary. |
| `calliope_fillers/` *(gitignored)* | Pre-rendered filler WAVs, regenerated from the config phrases on first launch. |

## Tests

`tests/test_calliope.py` is hermetic — **no audio hardware, no model files, no
network.** The kokoro-onnx model is replaced by a fake whose `.create()` returns
canned PCM, so synthesis is exercised without the ONNX model; playback is either
mocked out or driven against a stub `sounddevice` that raises, proving `speak()`
degrades to a silent no-op instead of crashing. Run it with the house runner:

```
python -X utf8 -m unittest tests.test_calliope
```
