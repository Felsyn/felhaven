# Helios — Titan of the Sun

*Anti-Legion: ONE JOB*

Helios turns raw sun times into **display-ready solar timing** — sunrise, sunset,
the morning and evening golden-hour windows, and the length of the day. It renders
as the Helios strip inside the Atmospherics card.

## A pure interpreter — no network

This is the key idea: Helios **reads no clock, hits no network, holds no state.**
It's a bundle of pure functions over one input — [Aura](Aura.md)'s `astronomy`
dict. Aura is the single fetcher; Helios only interprets what Aura already
pulled from wttr.in. `interpret()` in, display strings out:

```python
interpret({"sunrise": "05:52 AM", "sunset": "08:45 PM", ...})
# -> {"sunrise": "5:52 AM", "sunset": "8:45 PM",
#     "golden_am": "5:52 – 6:52 AM", "golden_pm": "7:45 – 8:45 PM",
#     "day_length": "14h 53m"}
```

It returns **None** (not an error, not a crash) when sunrise/sunset can't be
parsed — a polar "No sunrise" day, or an empty dict. Nothing here raises.

## Golden hour is a heuristic — on purpose

The golden-hour window is simply **±1 hour** around sunrise/sunset. The docstring
is emphatic that this is deliberate, not a placeholder: real solar-elevation math
(sun within 6° of the horizon) needs latitude and date, which is out of
proportion for a glanceable dashboard row. **Don't "upgrade" it.** I'm repeating
that here so a future reader doesn't mistake the simplicity for an oversight.

## The LLM contract wraps the pure core

Helios began contract-free (the [Emanon](../Moderati/Emanon.md) "interpreter
only" precedent). It has since grown the **`get_sun_times`** tool: `handle()`
lazily calls `aura.handle()` for today's astronomy, then feeds it through the
same `interpret()` the widget uses. The pure functions stay the core; the
contract is a thin wrapper.

*(The module docstring used to say "No TOOL_DEFINITION — a future get_sun_times
can be bolted on." That future arrived; the docstring has been corrected to match
the code.)*

## Files

| File | Purpose |
|---|---|
| `tools/helios.py` | The solar-timing interpreter + the `get_sun_times` contract. stdlib only. |
| `panels/aura_panel.py` → `HeliosWidget` | The Helios strip inside the Atmospherics card. |

## Using it

**In the dashboard** — the sun row on the **Atmospherics** card.

**Ask Pythia** — *"when's sunset?"* / *"how long is the day?"* / *"when's golden
hour?"* routes through `get_sun_times`.

**Standalone** (runs `interpret()` on a sample dict):

```
python tools/helios.py
```

## Tests

Covered by the shared handle suite:

```
python -X utf8 -m unittest tests.test_tool_handles
```
