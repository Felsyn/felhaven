# FELHAVEN — Command Center

> *Ex tenebris surgit lumen posteris*

A local-first personal dashboard built on the **Metis Toolbox** — a collection of single-purpose tool modules following the Anti-Legion principle: one daemon, one job.

**Felhaven** is the front-end that renders the toolbox in panels. Its resident AI, **Pythia**, answers questions in the home chat, and **Calliope** can read Pythia's answers aloud (output-only text-to-speech). See [**Calliope — Narration**](#calliope--narration) below. *(An earlier voice-**input** assistant, "Metis", was retired — the toolbox is now typed-in, optionally spoken-back.)*

> **Working on the code?** See [`CONVENTIONS.md`](CONVENTIONS.md) for the module contracts, panel patterns, logging/threading rules, testing setup, and the running decisions-and-deviations log — and [`CHANGELOG.md`](CHANGELOG.md) for the dated project history.

The window is laid out as a fixed header (brand · live clock · motto · narration lamp), a left-hand **sidebar** of selectable rows, and a **content area** that shows one full-size view at a time. Picking a row in the sidebar swaps the content view; only one view is visible at once.

Dedicated Felhaven Email: (Emails are found within NYX) <redacted> (only receives tech newsletters; Pheme's re-scope to "Read-Only Public RSS Feed Aggregation" is now done — public feeds live in `pheme_rumormill.json`)
Kill-The-Newsletter address: <redacted> (unused currently; to surface newsletters in Pheme, route them through this and add the resulting RSS as a feed row in `pheme_rumormill.json`. RSS contained within the physical notebook "NYX" Notes Yield eXecution)

---

## Views

Each sidebar row selects one view in the content area.

*UI display names are gothic-imperial; the code keeps the original deity ('machine spirit') module names — the Module column is the cross-reference.*

| View | Module(s) | Job |
|---|---|---|
| **Felhaven** | `panels/home_panel.py` | Home / landing view (currently blank) |
| **Chronometry** | `panels/horai_panel.py` · `tools/horai.py` | Clock, date, season/cycle badges; hosts the Ammit timer |
| **Moderati** | `panels/moderati_panel.py` · `panels/hephaestus_panel.py` · `panels/emanon_panel.py` · `panels/argus_panel.py` · `tools/hephaestus.py` · `tools/emanon.py` · `tools/argus.py` | Three-tab system-monitoring card: **HEPHAESTUS** (CPU/RAM/disk via `psutil`; hosts the Aether widget), **EMANON** (the Watcher — rolling log tail with a health verdict), and **ARGUS** (read-only network awareness — active connections with PID→process attribution, listening services, interface-level traffic rates, a DNS resolver-cache snapshot, firewall state, and a connection open/close timeline; visibility only, never blocks or captures packets; full contract in [`specs/argus.md`](specs/argus.md)) |
| **Atmospherics** | `panels/aura_panel.py` · `tools/aura.py` · `tools/helios.py` · `tools/selene.py` | Two-tab card: **NOW** (current conditions incl. chance of rain, plus collapsible **Helios** sun and **Selene** moon sub-widgets) and **FORECAST** (3-day outlook — emoji icons + chance of rain). All from `wttr.in` (no API key required) |
| **Celestarium** | `panels/hypatia_panel.py` · `tools/hypatia.py` · `tools/kepler.py` · `hypatia_stars.json` · `hypatia_constellations.json` · `hypatia_lore.json` | Zenith star-map projection (naval-scope style, north up) with clickable, highlightable constellations and mythology blurbs — plus a **Show Constellations** toggle for a stars-only view — and the five classical naked-eye planets (Mercury–Saturn) as alchemical-glyph markers, click for altitude + compass direction; a latitude teaching-mode preset (Current / North Pole / Equator / South Pole) dims the panel and jumps the chart, planets included; an embedded **Observation Conditions** widget (cloud-cover clarity stars, cloud %, moon illumination %) rides Aura's existing weather payload — zero new network calls |
| **Dynastic Vault** | `panels/midas_panel.py` · `tools/midas.py` · `tools/plutus.py` · `midas_watchlist.json` | Two-tab card: **PRICES** (live price + daily % change for a configurable equities watchlist via Finnhub `/quote`) and **LEDGER** (Plutus — a manual buy/sell holdings ledger) |
| **Scriptorium** | `panels/pheme_panel.py` · `tools/pheme.py` · `pheme_rumormill.json` | Config-driven RSS/Atom news aggregator — one independently-scrollable tab per feed (Hacker News, Ars Technica, The Verge, The Register, BBC World, NPR, WV News) |
| **Vox Array** | `panels/morpheus_panel.py` · `tools/morpheus.py` · `morpheus_playlists.json` | YouTube **audio** player (no video, ever): saved playlists, keyless search, and transport controls (⏮ ⏯ ⏭ ⏹). Drives a headless **mpv** over its JSON IPC named pipe; uses **yt-dlp** for keyless search. Two external binaries, zero new pip packages |
| **Cogitator** | `panels/cogitator_panel.py` · `panels/scribe_panel.py` · `panels/zeno_panel.py` · `panels/eudoxus_panel.py` · `tools/zeno.py` · `tools/eudoxus.py` · `scribe.py` | Three-tab utility card: **SCRIBE** (tasks + notes), **ZENO** (safe arithmetic evaluator), and **EUDOXUS** (unit converter). Request-driven; no Kairos worker. |

Five tools are embedded inside other views rather than getting their own row:

- **Ammit** (`tools/ammit.py`) — single countdown timer, embedded in HoraiPanel, persisted to `timer_state.json`.
- **Aether** (`tools/aether.py`) — WiFi + Anthropic's status, embedded in VitalsPanel (the HEPHAESTUS tab of Moderati).
- **Plutus** (`tools/plutus.py`) — manual holdings ledger, embedded as the **LEDGER** tab in **Dynastic Vault** (MidasPanel), persisted to `plutus_ledger.json`. Pure local bookkeeping: no network, no Kairos worker, and deliberately **not** a brain tool — it touches real-money records and changes only via explicit UI action.
- **Helios** (`tools/helios.py`) — interprets sun timing (sunrise, sunset, golden-hour windows, day length) from Aura's `astronomy` block; a collapsible sub-widget in WeatherPanel's NOW tab. Pure functions: no network, no state, deliberately **not** a brain tool (no `TOOL_DEFINITION` — Emanon precedent).
- **Selene** (`tools/selene.py`) — interprets the moon (phase + emoji glyph, illumination, moonrise, moonset) from the same `astronomy` block; a collapsible sub-widget alongside Helios. Same shape: pure, stateless, not a brain tool.

---

## Calliope — Narration

**Calliope** (`metis_toolbox/calliope.py`) reads **Pythia's answers aloud**, on demand.
That is her whole job: text in, speech out. She is handed Pythia's already-generated,
typed-path, fully-trusted answer text and turns it into sound via
[kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) — an ONNX text-to-speech
engine that needs **no torch/transformers**. She invokes no tools and decides nothing
about *what* to speak; the GUI decides *when*.

```
Pythia's answer text ──▶ calliope.speak(text) ──▶ kokoro-onnx (TTS) ──▶ Speaker
                              (never raises; a missing model or busy
                               audio device is a silent no-op)
```

Two triggers, both in the GUI:

- **Per-response button** — every Pythia answer gets a `▶ speak aloud` control in the
  home chat; click it to hear that one answer.
- **Narration lamp** — the header speaker glyph (top-right) is a global **auto-speak**
  toggle: lit (amber) = read every answer the moment it arrives; dim (grey) = silent.
  Off by default.

Voice, speed, language, and model paths live in `calliope_config.json`
(voice-switching is a config edit, not code). The kokoro-onnx model binaries are
downloaded separately and gitignored. Full contract:
[`specs/calliope.md`](specs/calliope.md) and
[`metis_toolbox/README_PANTHEON/Calliope.md`](metis_toolbox/README_PANTHEON/Calliope.md).

> **Retired:** an earlier voice-**input** loop ("Metis" — `Metis.py`, `apollo.py`,
> `metis_config.py`, Whisper/Silero STT, the keyword router) was removed. Voice input
> was an untrusted command surface; with it gone there is nothing to route or guard,
> and Calliope collapsed to a single job. The old LLM conversation layer remains parked
> at `archive/metis_brain.py`.

---

## Requirements

- Python 3.10+
- `pip install -r metis_toolbox/requirements.txt` — `psutil` + `requests` (dashboard core) and `kokoro-onnx` + `sounddevice` + `numpy` (Calliope narration). kokoro-onnx pulls onnxruntime, **not** torch/transformers, so the stack stays light.
- A free **Finnhub** API key for Midas PRICES (register at <https://finnhub.io/register>). Set it either by copying `metis_toolbox/.env.example` → `metis_toolbox/.env` and filling in `FINNHUB_API_KEY=...` (easiest — just relaunch, no shell restart), or as an OS environment variable (`setx FINNHUB_API_KEY "your-key"`, then open a new shell). If both are set, the OS variable wins. Without a key, PRICES rows show a no-key placeholder (no crash); the LEDGER tab works regardless (it makes no network calls).
- For **Calliope** (narration): the kokoro-onnx model binaries (`kokoro-v1.0.int8.onnx` ~88 MB + `voices-v1.0.bin` ~27 MB), downloaded once into `metis_toolbox/kokoro_models/` (gitignored) — see [`README_PANTHEON/Calliope.md`](metis_toolbox/README_PANTHEON/Calliope.md) for the exact URLs. Without them the dashboard runs fine; it just doesn't talk (a logged no-op, never a crash).
- For **Morpheus** (audio): the `mpv` and `yt-dlp` binaries. Drop `mpv.exe` and `yt-dlp.exe` into `metis_toolbox/bin/` (flash-drive-portable, takes precedence) or install them to PATH. No binaries → the Morpheus panel shows a placeholder and its controls are inert (no crash). **Honest caveat:** `yt-dlp` is the one churn-prone moving part in the whole stack — it breaks when YouTube changes its internals; the fix is `yt-dlp -U`. Blast radius is contained: when it breaks, music stops, but the rest of the dashboard is unaffected.

No database. No server. The only API key in the stack is Finnhub's (Midas); every other network call uses a keyless public endpoint.

---

## Usage

**Felhaven** (dashboard) — or just double-click `metis_toolbox/Felhaven.bat` (a no-console launcher):

```bash
cd metis\metis_toolbox
python felhaven.py
```

**Narration** — there is no separate process to launch. In the home chat, click
`▶ speak aloud` under any Pythia answer to hear it, or click the **narration lamp**
(the speaker glyph at the top-right of the header) to have every answer read aloud as it
arrives. Off by default; the kokoro-onnx model loads lazily on the first spoken line.

---

## Configuration

| What | Where |
|---|---|
| Weather location | `AURA_LOCATION` in `tools/aura.py` |
| Star map location | `HYPATIA_LAT` / `HYPATIA_LON` in `tools/hypatia.py` (keep in sync with `AURA_LOCATION` — same place, two representations) |
| Market watchlist | `midas_watchlist.json` (repo root — `{"tickers": [...]}`, US equities only). Shared seam: Plutus's LEDGER ticker dropdown reads the same file. |
| Finnhub API key | `FINNHUB_API_KEY` — setup steps are under [Requirements](#requirements). Never committed (`.env` is gitignored). |
| News feeds | `pheme_rumormill.json` (repo root — ordered `id` / `label` / `url` / `format` rows; tab order follows file order) |
| Playlists (Morpheus) | `morpheus_playlists.json` (repo root — ordered `label` / `url` rows; PLAYLISTS-tab order follows file order). Adding a playlist is a JSON edit, never a code change |
| Audio binaries (Morpheus) | `metis_toolbox/bin/mpv.exe` + `bin/yt-dlp.exe` (preferred), else on PATH. Gitignored. |

---

## Architecture

```
metis_toolbox/          ← THE APP ROOT: every path below is INSIDE this folder (e.g. binaries → metis_toolbox/bin/, not the clone root)
felhaven.py             ← dashboard entry point: header + sidebar + content area
kairos.py               ← central scheduler: one tick loop, all worker threads
metis_logging.py        ← shared logging setup (rotating files → logs/)
pythia.py               ← the LLM oracle (home chat): Ollama tool-calling loop
calliope.py             ← the narrator: text → speech via kokoro-onnx (output-only TTS)
__init__.py             ← empty package marker (the old voice-side registry was retired)
scribe.py               ← tasks & notes persistence layer
theme.py                ← color palette, fonts, Card base widget
calliope_config.json    ← Calliope voice/speed/model-path/auto-speak config
pheme_rumormill.json    ← Pheme feed config (ordered id/label/url/format rows)
morpheus_playlists.json ← Morpheus playlist config (ordered label/url rows)
midas_watchlist.json    ← Midas watchlist + Plutus ticker source ({"tickers":[…]})
plutus_ledger.json      ← Plutus holdings ledger (append-only buy/sell events; gitignored)
.env / .env.example     ← API keys (FINNHUB_API_KEY); .env gitignored, .example committed
panels/
    sidebar.py          ← Sidebar + SidebarRow (left-hand navigation)
    home_panel.py       ← HomePanel (landing view)
    horai_panel.py      ← HoraiPanel (clock + AmmitWidget timer)
    moderati_panel.py   ← ModeratiPanel (HEPHAESTUS + EMANON + ARGUS tabs)
    hephaestus_panel.py ← VitalsPanel (CPU / RAM / disk + AetherWidget; HEPHAESTUS tab body)
    aura_panel.py       ← WeatherPanel (NOW + FORECAST tabs; Helios/Selene sub-widgets)
    hypatia_panel.py    ← HypatiaPanel (zenith star map; embedded ConditionsWidget)
    midas_panel.py      ← MidasPanel (PRICES tab + Plutus LEDGER tab)
    cogitator_panel.py  ← CogitatorPanel (SCRIBE / ZENO / EUDOXUS tabs)
    scribe_panel.py     ← ScribePanel (tasks + NotesWidget; SCRIBE tab body)
    pheme_panel.py      ← PhemePanel (one scrollable tab per configured feed)
    morpheus_panel.py   ← MorpheusPanel (transport row + PLAYLISTS + SEARCH tabs)
    zeno_panel.py       ← ZenoPanel (calculator; ZENO tab body)
    eudoxus_panel.py    ← EudoxusPanel (unit converter; EUDOXUS tab body)
    emanon_panel.py     ← EmanonPanel (rolling log tail; EMANON tab body)
    argus_panel.py      ← ArgusPanel (read-only network awareness; ARGUS tab body)
    narrator_panel.py   ← NarratorLamp (header auto-speak toggle; not a sidebar view)
tools/
    horai.py            ← time
    ammit.py            ← timer (single slot)
    hephaestus.py       ← system vitals
    aura.py             ← weather
    helios.py           ← sun timing from astronomy (sunrise/sunset/golden hours/day length; pure)
    selene.py           ← moon from astronomy (phase/illumination/moon times; pure)
    hypatia.py          ← star-position tool: RA/Dec -> Alt/Az, presets, constellation + lore catalog
    kepler.py           ← planetary positions: low-precision Keplerian elements for the five classical planets (pure functions, composed by hypatia.py only)
    dev_build_hypatia_catalog.py ← one-off star/constellation catalog generator (hand-run, not imported)
    midas.py            ← market prices (Finnhub /quote, US equities)
    plutus.py           ← holdings ledger (local CRUD; no fetch, no brain tool)
    aether.py           ← connectivity (WiFi + Anthropic's status)
    pheme.py            ← RSS/Atom news aggregator (config-driven, concurrent fetch, stdlib ElementTree)
    zeno.py             ← safe arithmetic evaluator (AST reducer)
    eudoxus.py          ← unit converter (length/volume/weight/temp/time)
    emanon.py           ← log watcher (fetch-only)
    argus.py            ← network awareness: connections/listening/traffic/DNS/firewall/timeline (fetch-only)
    argus_peek.py       ← standalone CLI diagnostic (hand-run; raw seed of argus.py, not wired into the dashboard)
    morpheus.py         ← YouTube audio: headless mpv over JSON IPC + yt-dlp search
bin/                    ← mpv.exe + yt-dlp.exe (i.e. metis_toolbox/bin/; or on PATH; gitignored)
kokoro_models/          ← Calliope's kokoro-onnx model + voices binaries (gitignored)
```

### Layout & navigation

The sidebar (`panels/sidebar.py`) renders one row per view — a card with a navigation dot, a label, and a subtitle. Clicking a row swaps the content area to that view and lights the active row (dot and border turn phosphor-green). The dots are purely navigational — uniform amber at rest, green for the active view, never wired to health or data. Two touches are live: the Aura row's subtitle shows the current temperature (refreshed each weather tick), and the header clock ticks once per second as a registered Horai consumer. Sidebar navigation is the only way to move between views. *(How views are built and refreshed — the `_views` dict, the `Card` base widget, the no-`after()`/no-threads rule — lives in [`CONVENTIONS.md`](CONVENTIONS.md) §6–§7.)*

### Module contracts & threading

The module-surface flavors (which modules expose `fetch()` / `handle()` / `TOOL_DEFINITION`, and *why* a few — `plutus`, `morpheus`, `metis` — are deliberately kept out of LLM scope) and the Kairos threading model are specified once in [`CONVENTIONS.md`](CONVENTIONS.md) §2 and §6. They're not repeated here, so the contract can't drift between two documents.

### Scheduler intervals

| Worker | Interval |
|---|---|
| `horai` | 1 s |
| `emanon` | 2 s |
| `morpheus` | 2 s |
| `metis` | 2 s |
| `hephaestus` | 5 s |
| `argus` | 5 s |
| `hypatia` | 60 s |
| `midas` | 60 s |
| `pheme` | 15 min |
| `aura` | 30 min |
| `aether` | 1 hr |

---

## Logging

Logs rotate into `logs/<program>.log` (~1 MB × 3 backups). The `` | ``-delimited line format is **load-bearing**: **Emanon** tails these files and parses them with a plain `str.split(" | ")` — no regex — to build its health verdict, so the delimiter and column order can't change unilaterally. Full logging rules live in [`CONVENTIONS.md`](CONVENTIONS.md) §5.

---

## Data Files

| File / dir | Contents |
|---|---|
| `scribe_data.json` | Tasks and scratch notes (written by `scribe.py`) |
| `plutus_ledger.json` | Holdings ledger — append-only buy/sell events (written by `tools/plutus.py`). Gitignored: personal financial data. |
| `timer_state.json` | Timer state — survives restarts |
| `morpheus_watch_later/` | mpv saved playback positions for Morpheus resume (written by mpv via `--save-position-on-quit`). Gitignored: machine-local state. |
| `argus_timeline.json` | Connection open/close timeline — bounded rolling diff (`maxlen=300`), written on change by `tools/argus.py`. Gitignored: machine-local state. |
| `logs/*.log` | Rotating log files (written by `metis_logging.py`, read by `emanon.py`) |
