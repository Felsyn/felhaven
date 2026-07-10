# Selene — Titaness of the Moon

*Anti-Legion: ONE JOB*

Selene turns raw lunar data into **display-ready moon info** — phase (with glyph),
illumination percent, moonrise, moonset. She's the moon strip inside the
Atmospherics card, and Helios's twin: same shape, different celestial body.

## A pure interpreter — no network

Like [Helios](Helios.md), Selene **reads no clock, hits no network, holds no
state.** She interprets [Aura](Aura.md)'s `astronomy` dict — Aura is the single
fetcher, Selene only shapes what it already pulled:

```python
interpret({"moon_phase": "Waning Crescent", "moon_illumination": "32",
           "moonrise": "02:31 AM", "moonset": "03:18 PM"})
# -> {"emoji": "🌘", "phase": "Waning Crescent", "illumination": "32%",
#     "moonrise": "2:31 AM", "moonset": "3:18 PM"}
```

## Degrades, never KeyErrors

Two honest edge cases, both handled softly:

- **Unknown phase string** → falls back to a generic 🌙 with the raw string, never
  a `KeyError`. The eight canonical phases each map to their Unicode glyph.
- **"No moonrise" / "No moonset"** — wttr.in genuinely returns these on days the
  moon doesn't cross the horizon. Those (and anything unparseable) become `—`.

`interpret()` returns None **only** if the astronomy dict itself is empty/None.

## The LLM contract wraps the pure core

Same story as Helios: Selene started interpreter-only (the
[Emanon](../Moderati/Emanon.md) precedent) and has since grown the
**`get_moon_phase`** tool. `handle()` lazily calls `aura.handle()` for tonight's
astronomy, then runs the same `interpret()` the widget uses.

*(The docstring's old "No TOOL_DEFINITION — a future get_moon_phase can be added"
line has been corrected — the contract now exists below it.)*

## Files

| File | Purpose |
|---|---|
| `tools/selene.py` | The lunar interpreter + the `get_moon_phase` contract. stdlib only. |
| `panels/aura_panel.py` → `SeleneWidget` | The moon strip inside the Atmospherics card. |

## Using it

**In the dashboard** — the moon row on the **Atmospherics** card.

**Ask Pythia** — *"what phase is the moon?"* / *"is the moon up tonight?"* routes
through `get_moon_phase`.

**Standalone** (runs `interpret()` on a sample dict):

```
python tools/selene.py
```

## Tests

Covered by the shared handle suite:

```
python -X utf8 -m unittest tests.test_tool_handles
```
