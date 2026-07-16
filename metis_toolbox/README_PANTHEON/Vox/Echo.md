# Echo — the Scribe of Voices

*Anti-Legion: ONE JOB*

Echo turns **text into an audio file**. You paste Markdown (or plain prose) and a
filename; Echo hands you back one `.opus` file. That's the whole brief. It is the
**ECHO** tab of the **Vox Array — audio** card, beside Morpheus.

Echo is deliberately *not* the other two Vox jobs:

- **Calliope** reads text aloud *live* (text → speech, played now).
- **Morpheus** *controls an audio engine* (play/pause/skip a stream).
- **Echo** *writes a file* (text → audio on disk, nothing played).

## One model, one WAV writer — reuse, not a second copy

Echo owns **no** TTS model. It calls `calliope.synthesize()` (the pure,
hardware-free `text → 24 kHz float32 PCM` half) and reuses Calliope's
`take_chunk()`, `save_wav()`, and `SAMPLE_RATE`. So there is exactly **one** loaded
kokoro model and **one** WAV writer in the process — the same "one source of truth"
discipline as Calliope's singleton model. A second model instance would double
VRAM/RAM and drift from Calliope's tuning.

**The tradeoff, stated plainly:** because Echo shares Calliope's model and its
`_synth_lock`, a long conversion (a whole document's worth of chunks) **competes
with live narration** for that lock — while Echo renders, a spoken Pythia answer
can stall. This is accepted on purpose, not a bug. Run big conversions when you're
not relying on live narration.

## One binary, zero new pip packages

Echo's only external dependency is **ffmpeg**, used to encode the assembled WAV to
Opus (`libopus`). Resolution follows the Morpheus precedent: `<app root>/bin/ffmpeg.exe`
**wins over** PATH, so the portable copy beats a stale install. `available()`
reports `{"ffmpeg": <path or None>}`; a missing binary is a clean error, never a
crash or an import-time raise.

**Why Opus?** Smaller than MP3 at equal speech quality, and mpv (already in the
stack) plays it natively. Output lands in **`local_audio/`** at the app root —
gitignored runtime state, the same bucket as `morpheus_watch_later/`, with **no
retention cap** by design.

## The Markdown stripper — more thorough than Calliope's

Calliope's strip is a *defensive* character scrub (Pythia already speaks plain
prose). Echo eats **real `.md`**, so it does actual prose extraction:

- **Code fences (```` ``` ````/`~~~`) are dropped entirely** — reading code aloud
  is unlistenable, and readable prose is the job. *Inline* code keeps its text
  (just the backticks go). **Indented** code is left *as prose*: detecting it
  without a real Markdown parser risks eating legitimately-indented text, so that's
  a deliberate, documented limit.
- **Images** `![alt](url)` are dropped; **links** `[text](url)` unwrap to `text`.
- Heading marks, blockquote marks, list bullets, table pipes, horizontal rules,
  and emphasis characters are stripped.

## The contract

- **`text_to_audio(text, filename) -> dict`** — the whole job. Returns
  `{"path": "<abs .opus path>"}` on success or `{"error": "<code>"}` on any
  failure. **Never raises, never leaves a partial file.** Error codes:
  `empty_filename`, `empty_text`, `ffmpeg_unavailable`, `synthesis_failed`,
  `ffmpeg_encode_failed`, `echo_failed`. Note `ffmpeg_encode_failed` also covers an
  ffmpeg that's *present but built without libopus* — a nonzero exit, cleaned up.
- **`sanitize_filename(name) -> str`** — the filename field is **never trusted
  raw**. It strips path separators and Windows-illegal characters
  (`\ / : * ? " < > |`), rejects reserved device names (`CON`, `NUL`, `COM1`…),
  drops leading/trailing dots and spaces, and appends `.opus` if missing. An
  all-illegal name sanitizes to `""` → an `empty_filename` error, so nothing can
  write outside `local_audio/`. The panel shares this function to gate its button.
- **No `fetch()`, no `handle()`/`TOOL_DEFINITION`** for v1. Echo is *request-driven*
  (a button press, or an unattended caller later), not polled, and out of LLM scope
  for now — the "Local-only / plain functions" shape (CONVENTIONS §2). A future
  `narrate_to_file` brain tool is left open by the module shape, not built.

## The panel

`EchoPanel` is a bare `tk.Frame` tab body (the Cogitator-tab shape), **not**
Kairos-registered. A conversion can take real time, so it runs on a **daemon
thread** whose only shared touch is `queue.put(result)` — it never touches a Tk
object. The result is drained by a **bounded, self-terminating `self.after()`
chain** started on Send (the EmanonPanel one-shot-chain precedent, not a periodic
loop competing with Kairos), `winfo_exists()`-guarded and cancelled on teardown. A
**single-flight guard** makes a second click while a conversion is in flight a
no-op. The "Send to Echo" button stays **inert until both fields are usable** (text
non-blank *and* filename survives sanitisation), and any failure surfaces **loud
and red** (the theme's alarm color).

## Files

| File | Committed? | Purpose |
|---|---|---|
| `tools/echo.py` | yes | `text_to_audio` + the Markdown stripper, chunking, ffmpeg encode. |
| `panels/echo_panel.py` → `EchoPanel` | yes | The **ECHO** tab body. |
| `panels/vox_array_panel.py` → `VoxArrayPanel` | yes | The host card (MORPHEUS/ECHO tabs). |
| `bin/ffmpeg.exe` | **no** (large binary) | The Opus encoder; a PATH copy also works. |
| `local_audio/` | **no** (runtime) | Generated `.opus` files. No retention cap. |

## Using it

**In the dashboard** — the **Vox Array** card, **ECHO** tab: paste text, name it,
Send.

**Standalone** (runs a real sample conversion — needs the kokoro model + ffmpeg):

```
python tools/echo.py
```

## Tests

Hermetic — no audio hardware, no model, no ffmpeg, no network (Calliope's
`synthesize` and the ffmpeg subprocess are mocked):

```
python -X utf8 -m unittest tests.test_echo tests.test_vox_array_panel_smoke
```
