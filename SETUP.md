# SETUP — Installing & Running Felhaven

The single source of truth for **getting Felhaven running on a fresh machine**.
(For *what the modules are*, see [`metis_toolbox/README_PANTHEON/README.md`](metis_toolbox/README_PANTHEON/README.md);
for the project overview, [`README.md`](README.md).)

## Honest portability answer

The Python is portable — every path is `Path`/`%~dp0`-relative, nothing is
hardcoded to one machine. But the **full experience has two heavy, machine-level
dependencies** that a clone alone can't satisfy:

- **Pythia (the chat)** needs a running **Ollama** serving a tool-calling model,
  on a machine with enough GPU/RAM to actually run it. No Ollama → the dashboard
  and panels still work; the home chat just returns a friendly "oracle
  unreachable" string.
- **Calliope (narration)** needs a ~325 MB kokoro voice model downloaded
  separately, plus a working audio device. Missing → the dashboard runs, it just
  doesn't talk.
- **Vox Array audio (Morpheus + Echo + Orpheus)** needs external binaries dropped
  into `metis_toolbox/bin/`: `mpv` + `yt-dlp` for Morpheus (YouTube playback), and
  `ffmpeg` (with libopus) for Echo (text → audio file) **and** Orpheus (playing
  that file back) — one binary, shared, decode instead of encode. Missing →
  those tabs degrade (a placeholder / a clean error); nothing else is affected.
  See §5. All spoken/played audio (Calliope, Orpheus) also needs a working
  output device — Harmonia is the one thing in the process that touches it.

Everything else (weather, system vitals, stocks, news, star map, timer,
calculator, etc.) runs from the base install with no keys.

**Platform:** developed and run on **Windows**; the launcher (`Felhaven.bat`) and
CI are Windows-shaped. The code itself is cross-platform Python, so Linux/macOS
*mostly* works if you launch `python felhaven.py` directly, but that path is
untested — treat it as best-effort.

---

## 1. Prerequisites

- **Python 3.13**, 64-bit. The dependency lock is compiled for 3.13. pip
  refuses it on 3.10, 3.11 and 3.14, and on 3.12, where it would install, the
  test suite fails the venv. CI runs 3.13 too. Install it with the Python install manager (`py install 3.13`) or the
  3.13 Windows installer from <https://www.python.org/downloads/windows/>.
- **git**, and enough disk for the venv (~a few hundred MB) plus the optional
  ~350 MB of voice-model binaries.

## 2. Clone, venv, install

```powershell
git clone https://github.com/Felsyn/metis.git
cd metis\metis_toolbox
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes --only-binary=:all: -r requirements.txt
```

`requirements.txt` is a **lock**: every package Felhaven needs, direct or pulled
in (22 in all), pinned to one version and checked against a hash. pip installs
exactly those files or refuses. `--only-binary=:all:` means nothing is built
from source, so no package runs its own code while installing. The five direct
dependencies are listed in `requirements.in`: `psutil` + `requests` (dashboard
core) and `kokoro-onnx` + `sounddevice` + `numpy` (Calliope's text-to-speech,
onnxruntime-backed, **no torch**). The house style is stdlib-first, so there is
nothing else to install.

**Don't `pip install` anything else into `.venv`.** A test fails on any package
the lock doesn't name. To change a dependency, see CONVENTIONS §11. To rebuild
the venv from scratch, delete `.venv` and run the last three commands again.

> No-activation alternative (sidesteps PowerShell execution-policy prompts): call
> the venv Python directly, e.g. `.\.venv\Scripts\python.exe felhaven.py`.

## 3. Ollama — Pythia's brain (needed for the chat)

Pythia talks to a **local Ollama** over `/api/chat`.

1. Install Ollama (<https://ollama.com>).
2. Pull the model Pythia expects:
   ```
   ollama pull gemma4:e2b
   ```
   This is the default (`PYTHIA_MODEL`, in `pythia.py`) and the tag this project
   was developed against. To use a different tool-calling model, set the
   `PYTHIA_MODEL` environment variable to its Ollama tag instead — any model
   Ollama can serve that supports tool calling will do.
3. Endpoint: Pythia reads `OLLAMA_HOST` (default **`127.0.0.1:11435`**). If your
   Ollama listens on the standard `11434`, set `OLLAMA_HOST=127.0.0.1:11434`.

Without a reachable Ollama, the chat degrades gracefully (a readable "unreachable"
message) — it never crashes the app.

## 4. Narration models — Calliope (optional)

To hear Pythia's answers read aloud, download the two kokoro-onnx binaries into
`metis_toolbox/kokoro_models/` (gitignored):

```
https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx   (~325 MB, fp32)
https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin   (~27 MB)
```

fp32 is used on purpose — on a CPU it is ~3.5× *faster* than the smaller int8
build (RTF 0.44 vs 1.54; int8's quantize/dequantize overhead dominates).
All voice and latency tuning lives in `metis_toolbox/config/calliope_config.json`.
If the models are absent, Calliope logs it once and stays silent — nothing else
is affected.

## 5. Audio binaries — Vox Array (optional)

The **Vox Array** card's three tabs need two external binaries between them (Echo
and Orpheus share one). All follow the same rule: a copy in `metis_toolbox/bin/`
**wins over** PATH (flash-drive-portable), **but only once you have recorded it**
(below), and absence degrades cleanly — never a crash. All are gitignored, so a
fresh clone won't have them: this is a per-machine setup step.

- **Morpheus** (YouTube audio playback) — `mpv.exe` + `yt-dlp.exe`. yt-dlp from
  its official releases, <https://github.com/yt-dlp/yt-dlp/releases>; mpv from
  shinchiro's Windows builds, <https://github.com/shinchiro/mpv-winbuild-cmake/releases>
  (the builds mpv.io points to). Drop them in `metis_toolbox/bin/` or install to
  PATH. Missing → the MORPHEUS tab shows a placeholder with inert controls.
  *Caveat:* `yt-dlp` is the one churn-prone piece in the whole stack — it breaks
  when YouTube changes its internals; the fix is to update it and record the
  update (below).
- **Echo** (text → audio *file*) and **Orpheus** (play that file back) —
  `ffmpeg.exe`, **built with libopus** (the Opus codec both use, Echo to encode
  and Orpheus to decode). A Windows build with libopus is the "release-essentials"
  package at <https://www.gyan.dev/ffmpeg/builds/> (also published, with a
  checksum per file, at <https://github.com/GyanD/codexffmpeg/releases>) — unzip
  and drop its `bin/ffmpeg.exe` into `metis_toolbox/bin/`. Missing, or built without libopus →
  ECHO returns a clean error and writes nothing, and ORPHEUS shows a placeholder
  with an empty file list. Echo's synthesis reuses Calliope's kokoro model (§4),
  so it needs those binaries too. Orpheus needs no model at all — it only decodes
  what Echo already wrote.

### Checked before they run

`config/bin_manifest.json` records, for each program in **this machine's**
`bin/`, where it came from, the checksum its publisher published, and the sha256
of your copy. Like `bin/` it is per machine and gitignored, so a fresh clone has
none: **after putting a program in `bin/`, record it** (the commands below).
Recording checks your download against its publisher's checksum, so each
machine vouches for its own copies. Felhaven checks each `bin/` copy against
the manifest once per launch (`tools/aegis.py`):

- **A copy that isn't recorded, or doesn't match, is refused**, and Felhaven does
  not fall back to a copy on PATH instead. Its tab says so, and Emanon shows the
  ERROR. This is what you will see on a new machine before recording, and after
  updating a program without recording it.
- **A program only on PATH** (nothing in `bin/`) runs unverified, with one
  WARNING per launch. That copy is your own install, outside the manifest.

Check everything at any time, from `metis_toolbox\`:

```powershell
.\.venv\Scripts\python.exe -m tools.aegis verify
```

**Recording yt-dlp** (the common one, after a fresh download or an update):
update it with its own updater, then record it. `record` first checks the file
against the latest release's published `SHA2-256SUMS` without running it, then
reads its version and checks that release's sums too, and refuses unless the
file matches both. If it says the file isn't the latest release, run `-U`
again:

```powershell
.\bin\yt-dlp.exe -U
```
```powershell
.\.venv\Scripts\python.exe -m tools.aegis record yt-dlp
```

There is nothing to commit: the manifest stays on this machine.

**Recording mpv or ffmpeg**: download the official archive, take the publisher's
checksum for it (GitHub shows a sha256 beside every release file), put the new
exe in `bin/`, and pass both. For a `.zip`, aegis finds the exe inside and
checks it is the one in `bin/`; for a `.7z`, extract it yourself (Windows'
`tar -xf` reads `.7z`) and pass it as `--extracted`:

```powershell
.\.venv\Scripts\python.exe -m tools.aegis record ffmpeg --version 8.1.2 --source https://github.com/GyanD/codexffmpeg/releases/download/8.1.2/ffmpeg-8.1.2-essentials_build.zip --expect PUBLISHED_SHA256 --archive D:\path\to\ffmpeg-8.1.2-essentials_build.zip
```

Echo's output `.opus` files land in `metis_toolbox/local_audio/` (gitignored,
machine-local, no retention cap) — the same folder Orpheus's ORPHEUS tab lists
and plays from. Both Calliope's narration and Orpheus's playback go through
**Harmonia** (`metis_toolbox/harmonia.py`), the one module that actually touches
the audio device — no separate setup step, just the same working output device
Calliope already needs.

## 6. Keys — seed your own (both optional, both degrade gracefully)

These are **your** keys; none are shipped in the repo. They live only in the
Cerberus vault, encrypted to your Windows account (never in a file, an env var
or the repo). Set your Cerberus PIN first (§7). Then, in the **CERBERUS** tab
(under **Moderati**), each key below has its own row: click **set** and paste
the key into the masked box. Or, from `metis_toolbox/` in PowerShell:

```
python cerberus.py set finnhub_api_key
```

It asks for your PIN and then the key at hidden prompts. **Never type a key on
the command line:** your shell keeps every command in its history file, so
Cerberus refuses a key there.

- **Finnhub** (`finnhub_api_key`) — Midas market prices (PRICES tab). Get a free
  key at <https://finnhub.io/register>. No key, or a locked vault → PRICES
  shows a placeholder; the LEDGER tab (local bookkeeping) works regardless.
- **Brave Search** (`brave_api_key`) — Callimachus web search (a Pythia tool).
  No key → web search returns a clean "search unavailable" error; the rest of
  the chat is unaffected.
- **OpenTopography** (`opentopography_api_key`) — Demeter's terrain for the
  Cartograph map (Navigation → Cartograph). Get a free key at
  <https://portal.opentopography.org/requestService?service=api>. The key is
  read only while caching an area, never while drawing one. No key, or a vault
  that was sealed at ingest → the area still caches its roads and water and
  simply has no terrain; the panel says so, and re-caching later fills it in.

To change a key later (say, after regenerating it at the provider), use the
row's **replace**: the old key is never shown. `python cerberus.py status`
lists which keys read back, without printing any. If your Windows password is
ever *reset* (a normal change is fine) or your profile rebuilt, the keys can't
be decrypted any more: the tab says so, and **replace** puts them back.

### Waypoints (optional, no key, offline)

The Cartograph's **GO TO** box takes coordinates in three forms
(`38.8895, -77.0353`, `38 53.370 N 077 02.118 W`, `38 53 22 N 077 02 07 W`),
street addresses (online, via the Census geocoder), and your own saved places.
Saved places live in `metis_toolbox/config/artemis_waypoints.json` — gitignored,
because where you camp is yours. Create it if you want it:

```json
[
  {"name": "HOME",       "lat": 38.8895, "lon": -77.0353},
  {"name": "CAMP RIDGE", "lat": 38.7350, "lon": -77.2400}
]
```

Searching only moves the map's view; it never changes the location in Settings,
so your weather and star map stay put. **⌂ HOME** returns to your fix.

## 7. First run — the boot gates set themselves up

Launch via the portable launcher (double-click, or from a shell):

```
metis_toolbox\Felhaven.bat
```

It starts console-less through **Sphynx**, a soft boot gate (*"family-misclick
theater,"* not real security; the code says as much). The PIN files are **not
shipped** — they're per-user — so on a **fresh clone the gates walk you through
setting your own** instead of inheriting the author's:

- **Sphynx** — on first launch `sphynx_data.json` is absent, so Sphynx shows a
  one-time **setup screen**: write your own riddle/statement and choose a PIN, or
  click **skip the gate** (it's soft theater; a solo user may not want it — the
  choice is remembered). Later launches pose your riddle and take your PIN; three
  wrong tries close the window (relaunch for a fresh three — nothing is ever
  locked on disk).
- **Cerberus** — guards your API keys (§6). The keys are encrypted to your
  Windows account; the PIN is a child lock that decides when tools may use
  them, and decrypts nothing (`SECURITY.md` says what that does and doesn't
  protect). On first launch the **CERBERUS** tab (under **Moderati**) shows a
  **set a PIN** prompt when `cerberus_data.json` is absent; choose one and
  confirm and it opens straight into your (empty) vault. Use a PIN you use
  nowhere else. The CLI works too: `python cerberus.py setpin` asks for it at a
  hidden prompt. Cerberus locks itself when you close Felhaven, lock Windows,
  put the PC to sleep, or leave it untouched for an hour.

Both PIN files are gitignored, so they never leave your machine and a future clone
starts fresh. If you only want the dashboard panels (no chat, no keys), you can
skip the launcher and run `python felhaven.py` directly — but you'll still hit the
Sphynx gate, which `Felhaven.bat` normally fronts.

## 8. Personalize

- **Location, units & clock** — open the **SETTINGS** tab (under **Moderati**) and
  enter your latitude / longitude, plus an optional weather-location string (a city
  or ZIP) that overrides the coordinates for weather only. One coordinate pair
  drives weather, the star map, planet positions, and the season's hemisphere —
  Save and the dashboard follows within a tick, no source edit. The same tab sets
  your temperature unit (°F/°C) and clock format (12h/24h).
  - *Headless/CI override:* set the `AURA_LOCATION` env var (city, ZIP, or
    `lat,lon`) to override the weather location without touching the settings file.

- **Auto-DJ's taste** — `metis_toolbox/config/euterpe_config.json` holds the
  dials Auto-DJ picks by. `no_repeat_window` is how far back "don't play this
  again" reaches, in songs; `taste_anchor` is how many of your OWN picks steer
  the choice. They are config rather than constants on purpose — a narrow window
  lets a small model settle into a loop the length of that window, and widening
  it trades that for more misses, so the right values are found by listening
  rather than by reasoning.

  The rest of the file answers "is this even a song?". `min_song_seconds` and
  `max_song_seconds` bound the length, keeping out both a 26-second room-echo
  demo and an hour-long lore video. `not_music` is a list of phrases that are
  matched anywhere in a title, case-insensitively — it exists because duration
  alone does not separate a song from a 282-second gear manual, and one evening
  of Auto-DJ produced a user manual, a hardware review, a documentary and a
  reaction video.

  **`not_music` is a denylist, so expect to edit it.** One day it will refuse a
  real track whose title happens to contain "explained" or "reaction"; delete
  that line and it plays. An empty list `[]` turns the check off entirely.

  Edits take effect on the next pick; no restart.

  **The dials are in that file; the list itself is in the app.** Vox Array →
  **TUNES** shows `euterpe_ledger.json` as editable JSON. It holds the songs
  *you* chose — **Auto-DJ reads it and never adds to it**, so the list stays
  yours and only grows when you play something or type it in. That tab is where
  you delete a song you never want back or paste one in to steer the next
  evening. SAVE checks the JSON before it writes and keeps one
  `euterpe_ledger.json.bak` beside the log.

  **One row per song.** Playing something already on the list moves it to the
  bottom instead of adding a second copy, so the list stays as long as the
  number of songs you actually like. If you type a song in twice, SAVE refuses
  and tells you which two rows collide — it will not quietly delete one of them
  for you.

  A consequence worth knowing: the DJ's own picks are remembered only while
  Felhaven is running, just long enough to stop it repeating itself. There is no
  record of what it played last night. If it finds you something good, add it to
  TUNES — which is the same act as telling it you liked the song.

  **Once the taste is how you want it, press TAKE FROM SAVED.** That copies the
  ledger to `euterpe_ledger.json.base` — a restore point nothing else ever
  touches, unlike the `.bak`, which is overwritten by your next save. When a few
  evenings of Auto-DJ have dragged the taste somewhere you did not ask for,
  RESTORE loads the base copy back into the box for you to look at; it writes
  nothing until you press SAVE. Both files are gitignored, like the log itself.

## 9. Optional — run the tests

Hermetic (no network, no audio, no models needed). Run them with the venv's
Python (activated as in §2); one test checks that the venv is exactly the lock.
The check of the programs in `bin/` against your manifest (§5) runs only where
`bin/` exists; without it, that check is skipped.

```
cd metis_toolbox
python -X utf8 -m unittest discover -s tests -p "test_*.py"
```

To run the type check CI runs, add the dev lock (mypy and its stubs) to the
venv. The test above accepts the venv with or without it:

```
python -m pip install --require-hashes --only-binary=:all: -r requirements.txt -r requirements-dev.txt
python -m mypy --strict --explicit-package-bases tools/
```
