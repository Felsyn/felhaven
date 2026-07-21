# FELHAVEN — Command Center

> *Ex tenebris surgit lumen posteris*

A local-first personal dashboard built on the **Metis Toolbox** — single-purpose
modules following the Anti-Legion principle: one module, one job. No database, no
server, no cloud.

**Felhaven** renders the toolbox in panels. **Pythia** answers questions in the
home chat, and **Calliope** can read her answers aloud. *(An earlier voice-input
assistant, "Metis", was retired — the toolbox is typed-in, optionally spoken-back.)*

- [`SETUP.md`](SETUP.md) — installing on a fresh machine
- [`CONVENTIONS.md`](CONVENTIONS.md) — module contracts, patterns, and the decisions log
- [`CHANGELOG.md`](CHANGELOG.md) — dated history
- [`metis_toolbox/README_PANTHEON/`](metis_toolbox/README_PANTHEON/README.md) — one page per module

---

## How the modules connect

Three pieces of shared infrastructure tie ~30 independent modules together. Each
module knows about these; none know about each other.

| | What it owns | The seam |
|---|---|---|
| **Kairos** (`kairos.py`) | The clock. One Tk `after()` heartbeat, one thread per worker, results drained back to the main thread. | Calls `fetch()`. May raise — the panel then holds its last good state. |
| **Pythia** (`pythia.py`) | The only tool registry, built by reflection from `_TOOL_MODULES`. | Calls `handle()`. Never raises — errors come back as a dict the model relays. |
| **Harmonia** (`harmonia.py`) | The one audio output device. | Calliope and Orpheus hand it PCM; nothing else touches `sounddevice`. |

Same module, two entry points, opposite failure policy — that split is the core
of the design. Details in [`CONVENTIONS.md`](CONVENTIONS.md) §2 and §6.

Code lives in three places: `metis_toolbox/` (the app root — entry point, shared
infrastructure, config), `metis_toolbox/tools/` (headless logic), and
`metis_toolbox/panels/` (Tk display surfaces).

---

## Views

One sidebar row per view; picking a row swaps the content area. UI names are
gothic-imperial, the code keeps the deity module names.

| View | Shows |
|---|---|
| **Felhaven** | Home: Pythia's chat, with per-answer narration and session token/tool tallies |
| **Chronometry** | Clock, date, season badges; hosts the Ammit timer |
| **Moderati** | Six tabs: system vitals, log tail, network awareness, the Cerberus secrets gate, settings, and Pythia's editable system prompt |
| **Atmospherics** | Current conditions and a 3-day forecast, with sun and moon sub-widgets |
| **Celestarium** | Zenith star map with clickable constellations, the five classical planets, and observing conditions |
| **Dynastic Vault** | Live watchlist prices and a manual holdings ledger (behind the Cerberus PIN) |
| **Scriptorium** | RSS/Atom news aggregator, one tab per feed |
| **Vox Array** | Three audio tabs: YouTube playback, text-to-audio-file, and playback of those files |
| **Cogitator** | Three utility tabs: tasks and notes, a calculator, a unit converter |

Two brain tools have no view — **Callimachus** (web search) and **Herodotus**
(a local Markdown archive) are reached only through Pythia's chat.

---

## Running it

Double-click `metis_toolbox/Felhaven.bat`, or:

```bash
cd metis/metis_toolbox
python felhaven.py
```

The `.bat` launches the Sphynx boot gate first, which spawns `felhaven.py` on a
correct PIN. Running `felhaven.py` directly skips the gate.

Narration has no separate process: click the speak control under any answer, or
light the narration lamp in the home view's command row to have every answer read
aloud as it arrives. Off by default.

---

## Configuration

| What | Where |
|---|---|
| Location, units, clock | **SETTINGS** tab (Moderati) — writes `felhaven_settings.json`. One coordinate pair drives weather, the star map, planets, and the season's hemisphere. `AURA_LOCATION` env var overrides the weather location only, for headless use. |
| Feeds, playlists, watchlist, lore, voice | JSON files in `metis_toolbox/config/` — ordered, and UI order follows file order. Adding a feed or playlist is an edit, never a code change. |
| Finnhub + Brave API keys | The encrypted **Cerberus vault** only — never `.env`, never an env var, never the repo. Seed with `python cerberus.py set <PIN> <name> <key>`. |
| Audio binaries | `metis_toolbox/bin/` (mpv, yt-dlp, ffmpeg), preferred over PATH. Gitignored. |
| Worker intervals | `Kairos.WORKERS` in `kairos.py`. |

Both API keys are optional; every other network call uses a keyless public
endpoint. A locked vault, a missing key, or a missing binary degrades to a
placeholder or a clean error — never a crash.

---

## Logging

Logs rotate into `metis_toolbox/logs/`. The `` | ``-delimited line format is
**load-bearing**: Emanon tails these files and parses them with a plain
`str.split(" | ")` — no regex — so the delimiter and column order cannot change
unilaterally. Full rules in [`CONVENTIONS.md`](CONVENTIONS.md) §5.

---

## License

The public snapshot of Felhaven is released under the **MIT License** — see
[`LICENSE.md`](LICENSE.md). Use, copy, modify, and redistribute freely; just keep
the copyright notice and license text with it. No warranty.
