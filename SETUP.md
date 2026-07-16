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
- **Vox Array audio (Morpheus + Echo)** needs external binaries dropped into
  `metis_toolbox/bin/`: `mpv` + `yt-dlp` for Morpheus (YouTube playback), and
  `ffmpeg` (with libopus) for Echo (text → audio file). Missing → those two tabs
  degrade (a placeholder / a clean error); nothing else is affected. See §5.

Everything else (weather, system vitals, stocks, news, star map, timer,
calculator, etc.) runs from the base install with no keys.

**Platform:** developed and run on **Windows**; the launcher (`Felhaven.bat`) and
CI are Windows-shaped. The code itself is cross-platform Python, so Linux/macOS
*mostly* works if you launch `python felhaven.py` directly, but that path is
untested — treat it as best-effort.

---

## 1. Prerequisites

- **Python 3.10+** (PEP 604 `X | Y` type unions are used in the codebase, so 3.9
  and below will not import; CI runs 3.10 and 3.13).
- **git**, and enough disk for the venv (~a few hundred MB) plus the optional
  ~350 MB of voice-model binaries.

## 2. Clone, venv, install

```powershell
git clone https://github.com/Felsyn/metis.git
cd metis\metis_toolbox
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

`requirements.txt` is the **only** dependency file. It pulls: `psutil` +
`requests` (dashboard core) and `kokoro-onnx` + `sounddevice` + `numpy`
(Calliope's text-to-speech — onnxruntime-backed, **no torch**). That's the whole
runtime; the house style is stdlib-first, so there is nothing else to install.

> No-activation alternative (sidesteps PowerShell execution-policy prompts): call
> the venv Python directly, e.g. `.\.venv\Scripts\python.exe felhaven.py`.

## 3. Ollama — Pythia's brain (needed for the chat)

Pythia talks to a **local Ollama** over `/api/chat`.

1. Install Ollama (<https://ollama.com>).
2. Pull the model Pythia expects:
   ```
   ollama pull gemma4:e2b
   ```
   This is the default (`PYTHIA_MODEL`, in `pythia.py`). To use a different
   tool-calling model, set the `PYTHIA_MODEL` environment variable to its Ollama
   tag instead. TODO(verify: `gemma4:e2b` is the tag this project was developed
   against — confirm your Ollama can pull/serve it, or substitute an equivalent
   tool-calling model).
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
build (see [`README_PANTHEON/Calliope.md`](metis_toolbox/README_PANTHEON/Calliope.md)).
Filler audio and all voice/latency tuning live in `metis_toolbox/calliope_config.json`.
If the models are absent, Calliope logs it once and stays silent — nothing else
is affected.

## 5. Audio binaries — Vox Array (optional)

The **Vox Array** card's two tabs each need one external binary. Both follow the
same rule: a copy in `metis_toolbox/bin/` **wins over** PATH (flash-drive-portable),
and absence degrades cleanly — never a crash. All are gitignored, so a fresh clone
won't have them: this is a per-machine setup step.

- **Morpheus** (YouTube audio playback) — `mpv.exe` + `yt-dlp.exe`. Drop them in
  `metis_toolbox/bin/` or install to PATH. Missing → the MORPHEUS tab shows a
  placeholder with inert controls. *Caveat:* `yt-dlp` is the one churn-prone piece
  in the whole stack — it breaks when YouTube changes its internals; the fix is
  `yt-dlp -U`.
- **Echo** (text → audio *file*) — `ffmpeg.exe`, **built with libopus** (the Opus
  encoder Echo uses). A Windows build with libopus is the "release-essentials"
  package at <https://www.gyan.dev/ffmpeg/builds/> — unzip and drop its
  `bin/ffmpeg.exe` into `metis_toolbox/bin/`. Missing, or built without libopus →
  Echo returns a clean error and writes nothing. Echo's synthesis reuses Calliope's
  kokoro model (§4), so it needs those binaries too.

Echo's output `.opus` files land in `metis_toolbox/local_audio/` (gitignored,
machine-local, no retention cap).

## 6. Keys — seed your own (both optional, both degrade gracefully)

These are **your** keys; none are shipped in the repo.

- **Finnhub** — Midas market prices (PRICES tab). Get a free key at
  <https://finnhub.io/register>, then in `metis_toolbox/`:
  ```
  copy .env.example .env      # then edit .env: FINNHUB_API_KEY=your-key
  ```
  `.env` is gitignored — never commit it. No key → PRICES shows a placeholder;
  the LEDGER tab (local bookkeeping) works regardless.
- **Brave Search** — Callimachus web search (a Pythia tool). The key lives only in
  the Cerberus vault, never in a file:
  ```
  python cerberus.py set <PIN> brave_api_key <your-brave-key>
  ```
  (`<PIN>` is your Cerberus PIN — see §7.) No key → web search returns a clean
  "search unavailable" error; the rest of the chat is unaffected.

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
- **Cerberus** — the *real* secrets gate (guards the vault where the Brave key
  lives). On first launch the **CERBERUS** tab (under **Moderati**) shows a **set
  a PIN** prompt when `cerberus_data.json` is absent; choose one and confirm and
  it opens straight into your (empty) vault. You can still do it from the CLI if
  you prefer: `python cerberus.py setpin <new-pin>`.

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

## 9. Optional — run the tests

Hermetic (no network, no audio, no models needed):

```
cd metis_toolbox
python -X utf8 -m unittest discover -s tests -p "test_*.py"
```
