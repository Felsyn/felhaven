# Orpheus — Player of the Recorded Voice

*Anti-Legion: ONE JOB*

Orpheus plays back **one audio file from `local_audio/`** — the folder Echo
writes to. Play and stop, nothing else: no pause, no seek, no position, no
playlists, no folder picker. It is the **ORPHEUS** tab of the **Vox Array —
audio** card, third alongside Morpheus and Echo.

Orpheus is deliberately *not* the other Vox jobs:

- **Calliope** reads text aloud *live* (text → speech, played now).
- **Morpheus** *controls an audio engine* (play/pause/skip a stream).
- **Echo** *writes a file* (text → audio on disk, nothing played).
- **Orpheus** *plays a file back* (disk → audio, nothing written — the mirror
  image of Echo; see [`Echo.md`](Echo.md)).

## Why Orpheus needed Harmonia to exist first

Before Orpheus, Calliope was the only thing in the process making sound, and it
owned its own `sounddevice` play thread. Orpheus needed to play a *second* kind
of PCM through the same one speaker, and a lock over two callers can't actually
serialize a shared hardware device — it only serializes the Python calls into it.
So **Harmonia** (`harmonia.py`, app root) was carved out first: it is now the
*sole* thing in the process that calls `sounddevice`, and Orpheus (like Calliope)
just hands it PCM. See [`Harmonia.md`](../Harmonia.md) for that story.

## One binary, reused — zero new pip packages

Orpheus's only external dependency is **ffmpeg** — already a prerequisite for
Echo — used in the opposite direction: **decode** instead of encode. Resolution
follows the Morpheus/Echo precedent: `<app root>/bin/ffmpeg.exe` wins over PATH.
`available()` reports `{"ffmpeg": <path or None>}`; a missing binary is a clean
error, never a crash or an import-time raise — which matters here specifically
because Orpheus **is** a Kairos worker (`tools.orpheus.fetch`, resolved via
`importlib` at app startup), so an import-time exception would take down the
whole dashboard, not just this tab.

## Whole-file decode into RAM — a stated limit, not a bug

`play_file(name)` shells out to ffmpeg once per call:

```
ffmpeg -i <path> -f f32le -ar 24000 -ac 1 -   (stdout: raw interleaved PCM)
```

and reshapes the whole result to `(frames, channels)` before handing it to
`harmonia.play(pcm, 24000, tag="orpheus")`. For a 3-minute mono 24 kHz briefing
at float32 that's roughly **17 MB** — fine for what Echo produces. A multi-hour
file would not be; that bound is **stated in the module docstring** so it's a
known limit rather than a later surprise. Streaming would mean chunked reads
off ffmpeg's stdout — a different module, not this one.

**24 kHz mono — matching Echo, not a generic decode.** `_SAMPLE_RATE`/`_CHANNELS`
aren't "the format Orpheus decodes audio to" in general — they're **what Echo's
pipeline actually produces**: kokoro synthesizes mono at 24 kHz, and neither
`calliope.save_wav()` nor Echo's ffmpeg encode step resamples or remixes, so
every `.opus` in `local_audio/` is mono 24 kHz *at origin*. Decoding at a higher
rate or a second channel doesn't recover anything — it just upsamples and
duplicates a channel for zero new information, at real RAM cost (measured on a
real 6m41s file: **147 MB** decoded at 48 kHz stereo vs. **37 MB** at its native
24 kHz mono — exactly 4×, for nothing). This is Orpheus's own explicit
assumption about the folder it reads, not something Harmonia assumes on its
behalf — Harmonia still takes whatever rate/channel count it's given (D3);
`tests/test_orpheus.py` pins the exact numbers reaching `harmonia.play()`.
**A second, stated limit alongside the RAM bound:** the day something that
*isn't* Echo's own output lands in `local_audio/` (real music, a different
source rate), forcing 24 kHz mono will audibly degrade it. That's a limit to
revisit then — not a guard to add now, while the folder holds only Echo's output.

*(This was originally shipped decoding at 48 kHz stereo — a straightforward
handoff-following mistake: the written spec said 48 kHz/stereo, but never
verified against what Echo's pipeline actually emits. Caught in review before
it caused any real problem — see the CHANGELOG.)*

## Duration, without a second binary

The file list shows each briefing's length. The obvious tool for that is
**ffprobe** — but it isn't bundled in `bin/` alongside `ffmpeg.exe`, and pulling
in a whole second binary just to read one number is a bad trade (CONVENTIONS
§11: prefer stdlib, keep the binary count down). Instead, `_probe_duration()`
runs `ffmpeg -hide_banner -i <path>` with **no output** and reads the
`Duration: HH:MM:SS.ss` line ffmpeg prints to stderr as part of its normal
file-info banner — the standard ffprobe-free trick, and it never decodes a
single sample. That call always exits **nonzero** ("at least one output file
must be specified"); expected, and ignored — only a *missing* Duration line
counts as a real failure (→ `None`, logged).

**Cached by filename, probed once.** `local_audio/` files are write-once (Echo
never overwrites in place), so there's nothing to invalidate a cache on — each
file is probed the first time `fetch()` sees it and never again, instead of
shelling out to ffmpeg for every file on every 2 s Kairos tick. A *failed* probe
is deliberately **not** cached, so a file caught mid-write (a race with Echo
still encoding it) can resolve itself on a later tick rather than sticking at
"duration unknown" forever.

## The contract

- **`fetch() -> {"playing": bool, "files": [{"name", "duration"}, ...]}`** —
  Kairos-polled, 2 s. READ-ONLY, **never raises** (the `morpheus.fetch()`
  precedent — idle is a normal state, not a fault). `"playing"` is just
  `harmonia.is_playing()` — the completion signal the panel reads to flip its
  transport back to idle when a briefing finishes **on its own** (`sd.wait()`
  blocks inside Harmonia's own thread; nothing else can see that directly).
  `"files"` is the current `local_audio/` listing, refreshed on the same tick —
  no separate watcher, no manual refresh button — with each row's `"duration"`
  in seconds (`float`, or `None` if ffmpeg couldn't read it). Missing ffmpeg
  degrades to `{"playing": False, "files": [], "error": "no_ffmpeg"}`.
- **`play_file(name) -> dict`** — decode `name` (which must already be a file in
  `local_audio/`) and hand it to Harmonia. Mutation, fires only from deliberate
  UI action in `OrpheusPanel`. Never raises — a bad name, a missing ffmpeg, or a
  decode failure all degrade to a stable `{"error": "..."}`:
  `bad_name` / `ffmpeg_unavailable` / `decode_failed`.
- **`stop() -> None`** — `harmonia.stop()`. Mutation, UI-driven only.
- **`available() -> {"ffmpeg": path_or_None}`** — mirrors
  `morpheus.available()` / `echo.available()`. The panel reads this once at
  build time to decide whether to show a placeholder.
- **No `handle()`, no `TOOL_DEFINITION`** for v1 — the same "Local-only / plain
  functions" shape as Echo (CONVENTIONS §2). A future `play_briefing` brain tool
  is left open by the module shape, not built.

**`name` is never trusted raw.** `_safe_path()` takes `os.path.basename(name)`
and rejects anything that isn't already a bare filename that exists inside
`local_audio/` — the `echo.sanitize_filename` precedent, applied to *reading*
instead of *writing*. `"../../etc/passwd"` never reaches ffmpeg.

## Scope: `local_audio/` only

Not a general folder-picker — "play what Echo produced," the whole brief. `Echo`
already fixed on this one directory; Orpheus just reads from it.

## The panel

`OrpheusPanel` is a bare `tk.Frame` tab body (the Morpheus/Echo shape). Unlike
Echo, it **is** Kairos-registered (the `orpheus` worker, 2 s) — `update(data)`
drives both the transport and the file list. Each row shows the filename (click
to play) with its formatted duration underneath (`"m:ss"`, or `"duration
unknown"` if ffmpeg couldn't read it — surfaced, not hidden). Clicking a file
calls `orpheus.play_file(name)` **directly on the UI thread**: unlike Echo's
whole-document TTS conversion or Morpheus's multi-second `yt-dlp` search, a local
ffmpeg decode of what Echo produces is fast enough not to need a worker
thread + queue.

**Cross-talk, by design.** Because Harmonia has no channels, `"playing"` reflects
*any* sound coming out of the speaker — if Calliope is mid-answer, the ORPHEUS
tab will show "playing" too, and its ⏹ will stop that speech, not a briefing.
That's the accepted consequence of the one-stream design (see `Harmonia.md`),
not a bug in this panel.

## Files

| File | Committed? | Purpose |
|---|---|---|
| `tools/orpheus.py` | yes | `fetch` / `play_file` / `stop` / `available`. |
| `panels/orpheus_panel.py` → `OrpheusPanel` | yes | The **ORPHEUS** tab body. |
| `panels/vox_array_panel.py` → `VoxArrayPanel` | yes | The host card (MORPHEUS/ECHO/ORPHEUS tabs). |
| `local_audio/` | **no** (runtime) | Echo's `.opus` output — read, never written, by Orpheus. |

## Using it

**In the dashboard** — the **Vox Array** card, **ORPHEUS** tab: click any file to
play it; ⏹ stops it mid-file.

**Standalone** (prints `available()` + the file list; pass a filename to
actually play it — needs ffmpeg and a real audio device):

```
python tools/orpheus.py [filename]
```

## Tests

Hermetic — no real ffmpeg process, no real audio device, no network:

```
python -X utf8 -m unittest tests.test_orpheus tests.test_orpheus_panel_smoke tests.test_vox_array_panel_smoke
```
