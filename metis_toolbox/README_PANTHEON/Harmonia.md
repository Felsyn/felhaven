# Harmonia — Keeper of the Output Device

*Anti-Legion: ONE JOB*

Harmonia owns **the one audio output device**. Nothing else in the process calls
`sounddevice` directly — Calliope and Orpheus hand her PCM; she plays it. She lives
at the **app root** (`harmonia.py`, beside `kairos.py` and `calliope.py`), not in
`tools/` — she has neither a `fetch()` nor a `handle()`, she's infrastructure the
panels and tools depend on, exactly like Kairos.

## Why she exists

Before Harmonia, **Calliope** owned a module-local `_play_lock` + play thread, and
that was the *only* Python-side thing making sound. Then **Orpheus** arrived,
needing to play a *second* kind of PCM (a decoded `.opus` file) through the same
one speaker. Two Python-side device owners is the same class of bug as three would
be, just quieter — a lock over both **cannot work**, because a Python mutex
serializes *commands*, not *audio*: two `sd.play()` calls from two different
locks would still fight over the one real device. The fix was to make the problem
smaller before solving it — give both callers to **one** owner instead.

```
Calliope  ──PCM──┐
                 ├──> Harmonia ──> output device
Orpheus   ──PCM──┘        │
                          └──stop──> Morpheus ──> output device
```

**Morpheus** (the YouTube player) is the deliberate exception — it drives `mpv` in
a separate process with its own device handle, and that's the honest price of
using mpv. Harmonia never takes Morpheus's device; she only ever tells it to
`stop()` first.

## The contract

- **`play(pcm, sample_rate, tag="")`** — enqueue PCM for playback at the given
  sample rate. Calls `morpheus.stop()` **first**, every time (see "Yielding
  Morpheus," below). Never raises: a missing or busy device degrades to a logged
  no-op. `tag` is for logging only — it carries no routing meaning, because there
  are no channels.
- **`stop()`** — stop *everything*, unconditionally: `sd.stop()` (interrupts
  whatever is mid-playback) + drop everything still queued + bump an internal
  epoch so an item already pulled off the queue is skipped instead of played.
  Global, because there are no channels — this also cuts off a briefing Orpheus
  had mid-playback. A no-op when idle.
- **`is_playing()`** — `True` while there is still audio outstanding (queued or
  mid-play). Read-only, never raises. This is the completion signal Orpheus's
  `fetch()` reads to know when a briefing has finished on its own — `sd.wait()`
  blocks inside Harmonia's own thread, so nothing else can see that directly.
- **`shutdown()`** — stop the play thread cleanly. Wired into
  `felhaven._on_close`, **before** `morpheus.shutdown()`.

No `fetch()`, no `handle()`, no `TOOL_DEFINITION` — Harmonia is neither polled nor
LLM-facing, the same shape as `kairos.py`.

## The explicit sample rate — the actual reason this module exists

`play()` takes `sample_rate` as a **required argument**. Kokoro (Calliope) emits
24 kHz natively; ffmpeg (Orpheus) is asked to decode at 48 kHz. If Harmonia
hardcoded one of those — the way Calliope's old `_play()` hardcoded
`SAMPLE_RATE = 24000` — the other caller would play at the wrong speed. A
half-speed, half-pitch briefing is the concrete bug this design prevents; it's
not a hypothetical.

## Yielding Morpheus — one direction, in one place

`harmonia.play()` calls `morpheus.stop()` before enqueuing *any* audio, and that
call lives in **Harmonia's own code** — not in Calliope, not in Orpheus, not in a
panel. If the rule lived in every caller instead, it would be a dual (triple)
source of truth, and every *future* caller would have to remember to add it too.
It only runs one direction: Harmonia can silence Morpheus, but Morpheus starting
playback is a deliberate UI action, and Morpheus never learns Harmonia exists —
clicking ▶ on Morpheus while Orpheus is mid-briefing will play both at once (a
known, accepted gap; see Findings below).

`morpheus.stop()` opens its IPC pipe fresh on every call and never raises, so
calling it from Harmonia's background play thread — a new caller on a new thread
morpheus.py's own docstring doesn't (yet) mention — is safe.

## No channels — deliberately dumb

Harmonia does not know what a filler is, what speech is, what a briefing is, or
what Morpheus is playing. She takes PCM and a sample rate, full stop. She never
remembers what she interrupted, and bringing music back is a **separate, manual**
tool — `tools.morpheus.resume_music` — not something Harmonia does automatically.
Teaching her that would mean teaching her Morpheus's semantics (what "resume"
means, what state to keep), which turns a ~200-line module into a subsystem with
two directions of coupling instead of one. The cost of staying dumb is close to
zero: Morpheus already checkpoints its position on every `stop()`/`play()` via
mpv's own watch-later file, so "resume" is just "play the same URL again" — mpv
restores the position itself.

## The prebuffer gate stays with Calliope

Calliope does **not** hand Harmonia a chunk until it has synthesized a small lead
(`_PREBUFFER_CHUNKS`) — masking synth latency behind a filler line is a *speech*
concern, and Harmonia has no notion of what a filler is. Concretely: Calliope's
synth worker still produces PCM ahead of playback into its own internal release
queue; a small release worker gates only the *first* chunk of a turn on that lead
(waiting only while a filler is masking the wait) and then calls
`harmonia.play()` for every chunk after that, in order. Harmonia's own queue does
the actual back-to-back device playback — the release worker's only remaining job
is deciding **when** to make that first call.

## A behavior change, made on purpose

Calliope's old `stop()` only bumped an epoch, which discarded *queued* audio but
could not interrupt an in-flight `sd.wait()` — barge-in latency was one chunk
(~70 chars), unnoticeable at conversational speed. Harmonia's `stop()` calls
`sd.stop()`, which actually cuts off whatever is playing. Two consequences:

1. **Calliope's barge-in is now instant**, not one-chunk-latent — an improvement.
2. **Because Harmonia has no channels, `calliope.stop()` also stops Orpheus.** A
   new Pythia question will silence an in-progress briefing. This is the intended
   consequence of "one stream, one truth" — not a bug — but it's worth confirming
   it feels right in practice before it calcifies as expected behavior.

## Findings flagged, not fixed

- `morpheus.stop()`/`morpheus.play()` are now called from Harmonia's background
  play thread, not only from the main thread via UI action — a new caller on a
  new thread that `morpheus.py`'s own docstring doesn't mention yet. Flagged for
  a documentation pass; not fixed here, since the exact wording is a house-style
  call for the project owner.
- Clicking ▶ on Morpheus while Orpheus has a briefing playing produces **both at
  once** — Harmonia's yield only runs one direction (she can stop Morpheus;
  Morpheus starting is a human decision she doesn't intercept). Worth deciding
  whether the Orpheus/Morpheus panels should gray out each other's transport
  while `harmonia.is_playing()`.

## Files

| File | Committed? | Purpose |
|---|---|---|
| `harmonia.py` | yes | The device authority: `play`/`stop`/`is_playing`/`shutdown`. |
| `tests/test_harmonia.py` | yes | Hermetic — stubs `sounddevice`, mocks `morpheus.stop`. |

## Tests

Hermetic — no audio hardware, no network:

```
python -X utf8 -m unittest tests.test_harmonia
```
