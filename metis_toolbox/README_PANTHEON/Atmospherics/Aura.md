# Aura — Goddess of the Breeze

*Anti-Legion: ONE JOB*

Aura tells Metis **what the sky is doing** — temperature, feels-like, sky
description, humidity, wind, UV, a 3-day outlook, and the raw astronomy
(sun/moon times). It's the **single sky-data fetcher** for the whole toolbox: it
hits the network, everyone else reads what it returns.

## The one fetch, and who reads it

Aura calls **wttr.in** (`?format=j1` JSON, **no API key**) for the configured
location, flattens the sprawling response into display-ready scalars, and returns
one dict. Crucially, it **fetches but does not interpret** — it's the sole
network hop, and three other modules read its output rather than making their own:

| Reader | Reads | For |
|---|---|---|
| [Helios](Helios.md) | `astronomy.sunrise/sunset` | sun times, golden hour |
| [Selene](Selene.md) | `astronomy.moon*` | moon phase, moonrise/set |
| Hypatia's ConditionsWidget | `cloud_cover_pct` | "is the sky clear tonight?" |

This is CONVENTIONS §12 in practice: **one fetcher, many readers.** Helios and
Selene don't touch the network — they parse Aura's `astronomy` strings.

## Location config

One constant, no other setup:

```python
AURA_LOCATION = "Moundsville,WV"   # city, ZIP, or "lat,lon"
```

The panel title reads live from it (`"Atmospherics — {AURA_LOCATION}"`).

## Degrade-don't-crash, everywhere

wttr.in delivers every number as a *string* and occasionally omits blocks, so
Aura is defensive top to bottom: `_safe_int` swallows bad casts to a default,
`_max_pct` tolerates malformed hourly blocks, and one bad field never sinks the
whole build. And the two entry points split on failure policy deliberately:

| Entry | On failure | Why |
|---|---|---|
| `handle()` (LLM) | returns `{"error": "weather_timeout"...}` | the model needs a relayable answer, never an exception |
| `fetch()` (Kairos) | **raises** | Kairos catches it and delivers `None`, so the panel holds its last good state |

Same data, two contracts — because a voice answer and a polling widget want
opposite things when the network hiccups.

## Contract

`TOOL_DEFINITION` (LLM name **`get_weather`**, no params) + `handle()` +
`fetch()`. Timeout is a tight **6 s** — Metis is voice-first, latency matters.
Requires `requests`.

## Files

| File | Purpose |
|---|---|
| `tools/aura.py` | The wttr.in fetch + flatten. The single sky-data source. |
| `panels/aura_panel.py` → `WeatherPanel` | The **Atmospherics** card; hosts the Helios & Selene widgets. |

The panel is registered with Kairos under `aura`, and also feeds the sidebar
weather row and Hypatia's conditions strip — one fetch, drawn in three places.

## Using it

**In the dashboard** — the **Atmospherics** card (sidebar → weather).

**Ask Pythia** — *"what's the weather?"* / *"do I need a jacket?"* routes through
`get_weather`.

**Standalone**:

```
python tools/aura.py
```

## Tests

Covered by the shared handle suite (`requests` mocked — no network):

```
python -X utf8 -m unittest tests.test_tool_handles
```
