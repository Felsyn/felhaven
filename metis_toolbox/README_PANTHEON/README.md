# The Pantheon

One page per module: what it does, where it shows up, what tool name (if any) the
LLM calls it by, which files it owns, and how to test it. Anything deeper lives in
the module's own docstring, where it cannot drift from the code.

| Module | Job |
|---|---|
| [Aegis](aegis.md) | Vouch for the programs in bin/ before Felhaven runs them. |
| [Aether](aether.md) | Report whether WiFi, the internet and Ollama are reachable. |
| [Ammit](ammit.md) | Manage a single countdown timer. |
| [Argus](argus.md) | Report what this computer is communicating with on the network. |
| [Artemis](artemis.md) | Own the local-chart cache: its projection, area manifest and shared vector format. |
| [Aura](aura.md) | Report current weather: temperature, sky, wind, and forecast. |
| [Awen](awen.md) | When a song ends with Auto-DJ on, choose and play the next one and introduce it. |
| [Cadmus](cadmus.md) | Decode ESRI shapefile bytes (.shp/.dbf) into geometry and attribute records. |
| [Callimachus](callimachus.md) | Ask the web a question and return trimmed text. |
| [Calliope](calliope.md) | Turn a string of text into speech and play it. |
| [Cerberus](cerberus.md) | Keep the API keys encrypted to this Windows account, behind a PIN child lock. |
| [Daedalus](daedalus.md) | Map Felhaven's own modules, their docstrings and their imports, from the source. |
| [Demeter](demeter.md) | Fetch, resample and cache local elevation, and render the terrain raster. |
| [Echo](echo.md) | Turn text into an audio file on disk. |
| [Emanon](emanon.md) | Watch the stack's logs and report what broke — read-only, never act. |
| [Eratosthenes](eratosthenes.md) | Resolve a typed location query to a latitude and longitude. |
| [Eudoxus](eudoxus.md) | Convert between units of length, volume, weight, temperature, and time. |
| [Euterpe](euterpe.md) | Remember what music has played, and what the listener seems to like. |
| [Harmonia](harmonia.md) | Own the one audio output device. |
| [Hecate](hecate.md) | Fetch and cache TIGER/Line roads for an area as tiered Features. |
| [Helios](helios.md) | Report sunrise, sunset, golden-hour windows, and day length. |
| [Hermes](hermes.md) | Read from the network, bounded: a byte cap and a deadline for the whole request. |
| [Hephaestus](hephaestus.md) | Report CPU, RAM, and disk health. |
| [Herodotus](herodotus.md) | Manage a local archive of Markdown documents. |
| [Horai](horai.md) | Give the current date, time, season, and time-of-day. |
| [Hypatia](hypatia.md) | Map where every catalog star and planet sits in the sky right now. |
| [Hypnos](hypnos.md) | Tell whether the person has stepped away: locked, asleep or idle. |
| [Kairos](kairos.md) | Own the clock. Fire workers. Dispatch results to panels. |
| [Kepler](kepler.md) | Compute the positions of the five classical planets. |
| [Machine Spirit](machine_spirit.md) | Own Pythia's system prompt — the default text plus an optional per-install override. |
| [Midas](midas.md) | Fetch current price and daily % change for a watchlist. |
| [Morpheus](morpheus.md) | Play audio from YouTube — search, play, pause, and skip tracks. |
| [Orpheus](orpheus.md) | Play back one audio file from local_audio/ — the folder Echo writes to. |
| [Pheme](pheme.md) | Aggregate headlines from the configured RSS/Atom news feeds. |
| [Plutus](plutus.md) | Track stock buys and sells; derive shares held and cost invested. |
| [Poseidon](poseidon.md) | Fetch and cache TIGER/Line water for an area as tiered Features. |
| [Pythia](pythia.md) | Answer a question by talking to the local LLM, using toolbox tools for live data. |
| [Scribe](scribe.md) | Store the to-do list and notes. |
| [Selene](selene.md) | Report the moon's phase, illumination, moonrise, and moonset. |
| [Sphynx](sphynx.md) | Verify a PIN against the stored hash, and track how many attempts remain. |
| [Themis](themis.md) | Own the per-install user preferences: location, units, clock, and how Pythia answers. |
| [Virgil](virgil.md) | Give a tooltip its words: a module's own gloss, or a glossary entry. |
| [Voices](voices.md) | Resolve the voice Felhaven should speak in. |
| [Zeno](zeno.md) | Evaluate Python arithmetic expressions safely, step by step. |

Most tool modules run standalone for a smoke check: `python tools/<module>.py`.
The whole suite: `python -X utf8 -m unittest discover -s tests -p "test_*.py"`.
