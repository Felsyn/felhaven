# The Pantheon — Metis Toolbox Module Guide

Every module in the Metis Toolbox is a Greek deity with **one job** (the
*Anti-Legion* rule: no module does two things). This folder holds one README per
tool module, grouped into folders named after the **Felhaven panel** each module
renders in — the panel's proper name (the text before the em-dash in its card
title).

## Two ways in, two registries

There is no single "the tools list" — **two separate front ends** reach the
Pantheon, each through its own registry, because each has a different job and a
different amount of trust in the caller:

```
Felhaven GUI (tkinter)                      Felhaven home panel
   |  panels register with Kairos              |
   v                                            v
 Kairos (kairos.py)                          pythia.ask(message)
   - owns the clock (WORKERS list)              - Ollama /api/chat tool-calling loop
   - one thread per module.fetch()              - registry built from _TOOL_MODULES
   - fetch() may raise -> panel keeps             (16 modules, 21 TOOLS)
     its last good state on failure             |
   |                                            v
   v                                         tools.<module>.handle()
tools.<module>.fetch()                          |
   (11 workers, panel-only path)                v
                                             (answer text) ── optional ──▶ calliope.speak()
                                                                              - kokoro-onnx TTS
                                                                              - reads text aloud
```

| Interface | Entry point | Registry | Tool surface | Handler called | On failure |
|---|---|---|---|---|---|
| **Felhaven GUI** | [Kairos](#kairos-the-shared-clock) schedules `WORKERS` | `Kairos.WORKERS` (hardcoded list) | 11 panel-backing modules | `fetch()` | may raise; Kairos delivers `None`, panel holds last good state |
| [**Pythia**](Pythia.md) | Felhaven's home chat, `pythia.ask()` | `pythia._TOOL_MODULES` → `TOOLS`/`_DISPATCH` | **16 modules, 21 tools** — every LLM-callable tool that exists, including the two Pythia-only ones | `handle()` | never raises; returns an error dict the model relays as text |

There used to be a **third** way in — a voice loop (`Metis.py`) whose keyword
oracle **Apollo** could invoke a frozen 9-tool allowlist through a registry in
`metis_toolbox/__init__.py`. That whole layer was **retired** when Felhaven went
output-only: voice *input* is gone, so there is no untrusted spoken command
surface left to route or guard. What remains of the voice era is
[**Calliope**](Calliope.md), refactored into a single job — **read Pythia's
already-generated, fully-trusted answer text aloud on demand** (kokoro-onnx TTS).
Calliope invokes no tools, judges nothing, and is in **neither** registry: it is
handed a string and turns it into sound. See [Calliope](Calliope.md).

### Kairos, the shared clock

[Kairos](../kairos.py) is the one piece of infrastructure both the GUI and its
panels depend on but neither Pythia nor Apollo touch. It owns a single Tk
`after()` heartbeat (`TICK_MS = 500`), fires each worker in `WORKERS` on its own
interval from a daemon thread, and drains results through one `queue.Queue` back
onto the main thread — panels never manage their own timing, and worker threads
never touch tkinter directly. `fetch()` is Kairos's contract with a tool module;
`handle()` (below) is Pythia's. Same module, two entry points, opposite
failure policy — see "`handle()` vs `fetch()`" below.

## The panels & their modules

Each panel's card title is shown; its folder is the short name in the link.

| Panel | Modules | LLM tool(s) |
|---|---|---|
| [**Chronometry**](Chronometry) | [Horai](Chronometry/Horai.md) · [Ammit](Chronometry/Ammit.md) | `get_time_context` · `manage_timer` |
| [**Moderati**](Moderati) | [Hephaestus](Moderati/Hephaestus.md) · [Aether](Moderati/Aether.md) · [Emanon](Moderati/Emanon.md) · [Argus](Moderati/Argus.md) · [Cerberus](Moderati/Cerberus.md) · [Themis](Moderati/Themis.md) | `get_system_vitals` · `get_connectivity` · — · `get_network_summary` · — · — |
| [**Atmospherics**](Atmospherics) | [Aura](Atmospherics/Aura.md) · [Helios](Atmospherics/Helios.md) · [Selene](Atmospherics/Selene.md) | `get_weather` · `get_sun_times` · `get_moon_phase` |
| [**Hypatia**](Hypatia) | [Hypatia](Hypatia/Hypatia.md) · [Kepler](Hypatia/Kepler.md) | `get_sky_tonight` · — |
| [**Dynastic Vault**](Vault) | [Midas](Vault/Midas.md) · [Plutus](Vault/Plutus.md) | `get_market_prices` · — |
| [**Scriptorium**](Scriptorium) | [Pheme](Scriptorium/Pheme.md) | `get_news_stories` |
| [**Vox Array**](Vox) | [Morpheus](Vox/Morpheus.md) | `play_music` |
| [**Cogitator**](Cogitator) | [Scribe](Cogitator/Scribe.md) · [Zeno](Cogitator/Zeno.md) · [Eudoxus](Cogitator/Eudoxus.md) · [Callimachus](Cogitator/Callimachus.md)&nbsp;† · [Herodotus](Cogitator/Herodotus.md)&nbsp;† | — · `calculate` · `convert_unit` · `search_web`/`fetch_page` · `list_documents`/`search_documents`/`read_document`/`write_document`/`edit_document` |

**†** [Callimachus](Cogitator/Callimachus.md) (web search) and
[Herodotus](Cogitator/Herodotus.md) (Markdown archive) have **no panel or
tab** — they're grouped with the Cogitator by kinship, not because they render
there. Both are **Pythia-only** tools. (They were also the clearest examples of
why the retired voice router had a smaller allowlist than Pythia — search
shouldn't be a spoken reflex, and a voice command must never mutate the archive.
With voice input gone, Pythia's typed, judged registry is the only tool surface.)

That's **21 LLM-callable tools** across **16 tool modules** (out of 22 tool
modules total in the table above), all reachable through the one live tool
front end, [Pythia](Pythia.md). See
["Two ways in, two registries"](#two-ways-in-two-registries) above.

## Not every module is an LLM tool — and that's deliberate

The `—` above marks modules with **no LLM contract**. They fall into three honest
groups:

- **Panel-only watchers** — [Emanon](Moderati/Emanon.md) (log tail): a display
  surface, not something the model needs to reason over.
- **Out of scope on principle** — [Plutus](Vault/Plutus.md) mutates a
  ledger of real money; [Cerberus](Moderati/Cerberus.md) guards secrets behind its
  own PIN. The LLM is kept *away* from these on purpose.
- **Pure math / unregistered** — [Kepler](Hypatia/Kepler.md) is orbital math
  composed by Hypatia; [Scribe](Cogitator/Scribe.md) is not registered with
  Pythia today (its tool interface was built for the archived brain).
- **Settings infrastructure** — [Themis](Moderati/Themis.md) owns the per-install
  preferences the other tools *read*; it has no `fetch()` and no LLM contract
  because it is configuration, not something the model reasons over or sets.

[Argus](Moderati/Argus.md) is the interesting hybrid: a full-snapshot `fetch()`
for its panel *and* a slimmed `get_network_summary` for the LLM.

## Recurring patterns worth knowing

Reading across the Pantheon, the same design moves repeat — learning them once
makes every module legible:

- **`handle()` vs `fetch()`.** `handle()` is the LLM entry (never raises — returns
  an error dict the model can relay). `fetch()` is the Kairos entry (may raise on
  *total* failure, so Kairos delivers `None` and the panel holds its last good
  state). Same data, opposite failure policy.
- **One fetcher, many readers.** [Aura](Atmospherics/Aura.md) makes the only
  weather network call; [Helios](Atmospherics/Helios.md) and
  [Selene](Atmospherics/Selene.md) just interpret its output. Same with
  [Hypatia](Hypatia/Hypatia.md) composing [Kepler](Hypatia/Kepler.md), and
  [Midas](Vault/Midas.md) beside [Plutus](Vault/Plutus.md).
- **Host a neighbor.** A panel can display a *second* module's widget without
  owning its logic — Horai hosts Ammit's timer, Hephaestus hosts Aether's strip.
- **Config over code.** Feeds, playlists, watchlists, and Custody lists live in
  JSON at the app root — adding one is an edit, never a code change.
- **Degrade, don't crash.** Watchers and fetchers return safe/partial data on
  failure rather than throwing; a dead feed or a missing catalog never takes down
  the panel.

## Running & testing

Most tool modules run standalone for a quick smoke check:

```
python tools/<module>.py
```

The LLM-callable handles share one hermetic suite (data sources mocked — no
network, no audio):

```
python -X utf8 -m unittest tests.test_tool_handles
```

A few modules have their own suites — [Kepler](Hypatia/Kepler.md)
(`test_kepler`, pinned against JPL Horizons) and
[Plutus](Vault/Plutus.md) (`test_plutus`, the average-cost fold) — plus
per-panel smoke tests for the heavier UIs. Each module's README lists its exact
test command.

---

*Anti-Legion: one module, one job. If a README describes a module doing two
things, one of them belongs somewhere else.*
