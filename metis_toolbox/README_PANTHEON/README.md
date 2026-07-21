# The Pantheon

One page per module: what it does, where it shows up, what tool name (if any) the
LLM calls it by, which files it owns, and how to test it. Anything deeper lives in
the module's own docstring, where it cannot drift from the code.

| Module | Job |
|---|---|
| [Aether](aether.md) | Report whether WiFi and the Anthropic API are reachable. |
| [Ammit](ammit.md) | Manage a single countdown timer. |
| [Argus](argus.md) | Report what this computer is communicating with on the network. |
| [Aura](aura.md) | Report current weather: temperature, sky, wind, and forecast. |
| [Callimachus](callimachus.md) | Ask the web a question and return trimmed text. |
| [Calliope](calliope.md) | Turn a string of text into speech and play it. |
| [Cerberus](cerberus.md) | Guard the toolbox's secrets behind one PIN. |
| [Echo](echo.md) | Turn text into an audio file on disk. |
| [Emanon](emanon.md) | Watch the stack's logs and report what broke. |
| [Eudoxus](eudoxus.md) | Convert between units of length, volume, weight, temperature, and time. |
| [Harmonia](harmonia.md) | Own the one audio output device. |
| [Helios](helios.md) | Report sunrise, sunset, golden-hour windows, and day length. |
| [Hephaestus](hephaestus.md) | Report CPU, RAM, and disk health. |
| [Herodotus](herodotus.md) | Manage a local archive of Markdown documents. |
| [Horai](horai.md) | Give the current date, time, season, and time-of-day. |
| [Hypatia](hypatia.md) | Map where every catalog star and planet sits in the sky right now. |
| [Kairos](kairos.md) | Own the clock. Fire workers. Dispatch results to panels. |
| [Kepler](kepler.md) | Compute the positions of the five classical planets. |
| [Machine Spirit](machine_spirit.md) | Own Pythia's system prompt. |
| [Midas](midas.md) | Fetch current price and daily % change for a watchlist. |
| [Morpheus](morpheus.md) | Play audio from YouTube. |
| [Orpheus](orpheus.md) | Play back one audio file from `local_audio/`. |
| [Pheme](pheme.md) | Aggregate headlines from the configured RSS/Atom news feeds. |
| [Plutus](plutus.md) | Track stock buys and sells; derive shares held and cost invested. |
| [Pythia](pythia.md) | Answer a question by talking to the local LLM. |
| [Scribe](scribe.md) | Store and manage the to-do list and notes. |
| [Selene](selene.md) | Report the moon's phase, illumination, moonrise, and moonset. |
| [Sphynx](sphynx.md) | Verify a PIN against the stored hash; track attempts remaining. |
| [Themis](themis.md) | Own the per-install user preferences. |
| [Zeno](zeno.md) | Evaluate Python arithmetic expressions safely, step by step. |

Most tool modules run standalone for a smoke check: `python tools/<module>.py`.
The whole suite: `python -X utf8 -m unittest discover -s tests -p "test_*.py"`.
