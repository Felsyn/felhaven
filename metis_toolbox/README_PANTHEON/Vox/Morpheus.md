# Morpheus — Dream Audio

*Anti-Legion: ONE JOB*

Morpheus controls an **audio engine** — play a YouTube URL, pause, skip, stop,
and search for a track. No video, no thumbnails, no downloads, no transcoding, no
play history. *"I want to listen to this playlist today"* is the whole brief. It's
the purple **Vox Array — audio** card.

## Two binaries, zero new pip packages

Morpheus drives external binaries over stdlib-only Python — the stack stays
flash-drive-portable:

- **mpv** — headless audio engine, driven over its **JSON IPC named pipe**. mpv
  invokes yt-dlp internally to resolve YouTube URLs.
- **yt-dlp** — keyless YouTube search (`ytsearch:`).

Resolution order (Midas's precedent): `<app root>/bin/*.exe` **wins over** PATH,
so the portable copy beats a stale install. If either is missing, `available()`
reports it and the panel shows a placeholder — **never crashes, never raises at
import.** This is **Windows-only by design** (named pipes, `CREATE_NO_WINDOW`) —
Felhaven is a Windows dashboard.

## The contract is a deliberate oddity — read it

Morpheus's `fetch()` **breaks the toolbox norm on purpose**, and the module
docstring flags it so future-you isn't surprised:

- **`fetch()` / `status()` is read-only and never raises.** "Morpheus not
  playing" is a *normal state*, not a fault — the opposite of the raise-on-failure
  policy Aura/Midas/Hypatia's `fetch()`es follow. And when mpv is down it returns
  `{"running": False}` **without touching the pipe**, so an idle dashboard
  generates zero IPC churn and zero Emanon log noise.
- **Mutations fire only from deliberate UI action** — `play`, `toggle_pause`,
  `next_track`, `prev_track`, `stop`.

## Resume across restarts

mpv saves playback position on quit (`--save-position-on-quit`) into
`morpheus_watch_later/`. That directory is passed **explicitly** because
`--no-config` (used so a host's personal `mpv.conf` can't change Morpheus's
behavior) disables mpv's default watch-later location — without the explicit dir,
resume would silently do nothing. `stop()` and `play()` also force a checkpoint
(`write-watch-later-config`), so a mid-session stop or a switch to another video
saves your place too, not only app close. Long lore videos survive a restart.

## The stdlib IPC client

Talking to mpv is hand-rolled JSON over the named pipe (no python-mpv dep). Two
details worth knowing:

- **Batched reads** (`_get_props`) fetch several properties over **one** pipe
  open/write/read cycle, so a Kairos tick pays a single reply-timeout worst case,
  not one per property.
- **Open-with-retry** absorbs Windows' transient `ERROR_PIPE_BUSY` right after mpv
  starts (bounded, so a genuinely dead pipe still fails fast). A `FileNotFoundError`
  means the pipe doesn't exist at all (mpv not running) → fail fast, no retry.
- The read timeout is a shared deadline checked *between* lines — an accepted
  stdlib tradeoff (a truly hung single `readline()` would need overlapped I/O,
  out of scope).

`shutdown()` guarantees **no orphan mpv.exe** after close: polite `quit` over IPC,
then `terminate()`, then `kill()` — belt and suspenders. Felhaven calls it on
window close.

## The LLM tool

`handle()` / **`play_music`** — "play this song": searches YouTube's top hit and
plays it. It **blocks for seconds** on the yt-dlp search, so it must be threaded;
Pythia already runs tool calls on a worker thread. Playback controls stay
panel-driven for now. Never raises — a missing binary, empty query, or no result
degrades to an error dict.

*(The module docstring's old "NO handle(), NO TOOL_DEFINITION — out of LLM scope"
line has been corrected — `play_music` now exists below it.)*

`search()` is likewise blocking (up to 20 s) and the panel calls it on a daemon
thread, never the main thread.

## Playlists

`morpheus_playlists.json` at the app root (config-over-code, like Pheme's feeds).
`load/save/remove_playlist` back the panel's playlist manager — removal is **by
position, not content**, so duplicate labels delete exactly the targeted row.

## Files

| File | Committed? | Purpose |
|---|---|---|
| `tools/morpheus.py` | yes | Engine control, IPC, search, the `play_music` contract. |
| `morpheus_playlists.json` | yes | Saved playlists (config). |
| `bin/mpv.exe`, `bin/yt-dlp.exe` | **no** (large binaries) | The engines; PATH copies also work. |
| `morpheus_watch_later/` | **no** (runtime) | mpv's resume checkpoints. |
| `panels/morpheus_panel.py` → `MorpheusPanel` | yes | The **Vox Array** card. |

## Using it

**In the dashboard** — the **Vox Array** card: pick a playlist, search, play.

**Ask Pythia** — *"play clair de lune"* / *"put on some lofi"* routes through
`play_music`.

**Standalone** (checks binaries + runs a sample search):

```
python tools/morpheus.py
```

## Tests

Covered by the shared handle suite + a panel smoke test (mpv/yt-dlp mocked — no
audio, no network):

```
python -X utf8 -m unittest tests.test_tool_handles tests.test_morpheus_panel_smoke
```
