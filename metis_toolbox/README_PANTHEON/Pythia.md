# Pythia — The Oracle

*Anti-Legion: ONE JOB*

Pythia is the Metis Toolbox's **LLM brain**. You ask a question in plain
English; she answers, calling the toolbox's tools whenever she needs live data
rather than guessing. She is the piece that connects the local model (gemma4:e2b
via Ollama) to the tools that were already written to be LLM-callable.

## How it works

One function — `ask(message, history=None) -> str` — runs an Ollama `/api/chat`
**tool-calling loop**:

1. Send the conversation + the tool schemas (`TOOLS`) to the model.
2. If the model asks for a tool, run it via the dispatcher and feed the JSON
   result back.
3. Repeat until the model answers in plain text (capped at 5 tool rounds).

She **never raises** — a timeout, an unreachable server, or a tool failure comes
back as a short readable string the UI can print verbatim.

## Config

All local, no API key.

| Setting | Env var | Default |
|---|---|---|
| Ollama endpoint | `OLLAMA_HOST` | `127.0.0.1:11435` |
| Model | `PYTHIA_MODEL` | `gemma4:e2b` |

Requests send `think=false` to skip gemma4's reasoning trace and return just the
answer. (See the local LLM setup in `PC_INFO.txt` — the standalone Ollama runs on
port **11435** with model weights on **D:**.)

## The tools she can call (21, across 16 modules)

| Tool module | LLM tool | What it answers |
|---|---|---|
| horai | `get_time_context` | date / time / season |
| hephaestus | `get_system_vitals` | CPU / RAM / disk |
| aura | `get_weather` | current conditions |
| ammit | `manage_timer` | the countdown timer |
| midas | `get_market_prices` | watchlist quotes |
| aether | `get_connectivity` | online / offline |
| pheme | `get_news_stories` | RSS headlines |
| zeno | `calculate` | arithmetic |
| eudoxus | `convert_unit` | unit conversion |
| argus | `get_network_summary` | connections / firewall / bandwidth |
| helios | `get_sun_times` | sunrise / sunset / golden hour |
| hypatia | `get_sky_tonight` | visible planets + brightest stars |
| selene | `get_moon_phase` | phase / moonrise / moonset |
| morpheus | `play_music` | search YouTube and play a track |
| callimachus | `search_web` | web search (Brave) — current/unknown facts |
| callimachus | `fetch_page` | read one chosen result's page text |
| herodotus | `list_documents` | every archived Markdown doc |
| herodotus | `search_documents` | substring search over the archive |
| herodotus | `read_document` | one document's full text |
| herodotus | `write_document` | create/overwrite a document |
| herodotus | `edit_document` | append/prepend/replace/insert-by-heading |

Most tool modules expose a `TOOL_DEFINITION` (the schema) + a `handle()` (the
code). **Callimachus** and **Herodotus** are the two **multi-tool modules**: each
exports the plural `TOOL_DEFINITIONS` and one function per tool. Pythia builds her
`TOOLS`/`_DISPATCH` registry from `_TOOL_MODULES` at import — reading each name
straight from its schema — so the registry can never drift from the modules
themselves (`test_new_handles_reach_pythia` derives its expected count the same
way, so it can't silently go stale either).

**Pythia is now the only tool front end.** There was once a second, voice-side
registry in `metis_toolbox/__init__.py` (a frozen 9-tool allowlist the keyword
router Apollo could reach); it was **retired with voice input**, so Pythia's
reflection-built registry is the single path any tool is called from. Callimachus
and Herodotus were the clearest reasons that voice allowlist stayed small — search
shouldn't be a spoken reflex, and a voice command must never mutate the archive —
but with no voice input, they're simply Pythia tools like the rest. Callimachus's
Brave key lives in the Cerberus Vault, so search works only once Cerberus is
unlocked. See the top-level [README](README.md#two-ways-in-two-registries).

**Kepler is deliberately absent** — its own contract keeps it "out of LLM scope
on purpose," and its planet data already reaches the model through
`hypatia.get_sky_tonight`.

## Import regime

Pythia lives in the **Felhaven regime** (`metis_toolbox/` on `sys.path`), so she
imports tools top-level (`from tools import ...`) and builds her own registry.
The package-regime dispatcher that once lived in `metis_toolbox/__init__.py`
(consumed by the voice loop) is gone; that file is now an empty package marker.

## Files

| File | Purpose |
|---|---|
| `pythia.py` | The brain — the tool-calling loop. No tkinter, no threads. |
| `panels/home_panel.py` | Felhaven's home view: the Pythia chat box. |

The home panel calls `ask()` on a **daemon worker thread** and marshals the reply
back via a queue + `after()` poll, so a slow LLM call never freezes tkinter.

## Using it

**In the dashboard** — open Felhaven; the home view is the chat. Type a question
and press Enter. Try: *"what's the weather and is the moon up?"* or
*"play clair de lune"*.

**Standalone** (quick smoke test against the live model):

```
python pythia.py
```

## Tests

Hermetic — `requests.post` and each tool's data source are mocked, so no server,
no network, no audio:

```
python -X utf8 -m unittest tests.test_pythia tests.test_tool_handles
```
