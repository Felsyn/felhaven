# Horai — Goddesses of the Hours and Seasons

*Anti-Legion: ONE JOB*

Horai answers a single question: **when is Metis?** It turns the system clock
into temporal *context* — not just the time, but the season and the part of the
day — so anything downstream can reason about "now" without doing its own date
math.

## The three goddesses

| Goddess | Job | Output |
|---|---|---|
| **Eunomia** | The clock | exact datetime (ISO + human-readable) |
| **Dike** | The season | Spring / Summer / Autumn / Winter |
| **Eirene** | The cycle | Dawn / Morning / Afternoon / Evening / Night |

Season is **Northern-Hemisphere, by calendar month** (Mar–May Spring, etc.) — a
deliberate simplification, no equinox astronomy. Cycle is by hour: Dawn 5–8,
Morning 8–12, Afternoon 12–17, Evening 17–21, Night otherwise.

## Contract

Like every tool in the box, Horai exposes a `TOOL_DEFINITION` (the LLM schema)
and a `handle()` (the code). `handle()` takes **no arguments** and returns a dict:

```python
{
    "iso":    "2026-07-07T14:32:10-04:00",
    "clock":  "Tuesday, July 07 2026, 02:32 PM",
    "season": "Summer",
    "cycle":  {"label": "Afternoon", "hour": 14},
}
```

The LLM tool is named **`get_time_context`** (no parameters). It's the first row
in Pythia's tool table — the model calls it whenever a question touches time,
schedules, recency, or "how long until…".

No network, no files, no I/O — just `datetime.now().astimezone()` and two lookup
functions. It cannot fail in any interesting way, which is why there's no
error path to handle.

## `fetch` — the Kairos entry point

```python
fetch = handle   # same data, no I/O
```

Kairos (the toolbox's single clock owner) ticks Horai **once per second** via
`tools.horai.fetch`. Because the call is pure and cheap, a 1 Hz refresh costs
nothing. The same tick drives both the Horai panel and the header clock.

## Who calls it

| Consumer | How |
|---|---|
| **Pythia** | Lists it in `TOOLS`; the LLM invokes it by name. |
| **Kairos** | Polls `horai.fetch` every 1 s and feeds the result to the panel + header clock. |

(It was also the first entry in the retired voice-side dispatcher —
`get_time_context → horai.handle` in `metis_toolbox/__init__.py` — but that
registry went away with voice input.)

Nothing calls Horai directly for its own date math — that's the point. One source
of "now."

## Files

| File | Purpose |
|---|---|
| `tools/horai.py` | Core logic — `TOOL_DEFINITION`, `handle()`, `fetch`. No tkinter. |
| `panels/horai_panel.py` | `HoraiPanel` — the **Chronometry** card in Felhaven. |

### The panel

`HoraiPanel` is the amber **Chronometry** card: a big HH:MM:SS clock, the full
date line, and two badges (cycle + season). Kairos pushes fresh `horai` data into
its `update()` every second.

It also **hosts AmmitWidget** — the collapsible countdown timer — because a timer
belongs next to the clock in the UI. Note the seam: the *timer* logic is Ammit's
(`tools/ammit.py`, its own tool + state file), Horai just gives it a home on
screen. Horai the tool knows nothing about timers.

## Using it

**In the dashboard** — the Chronometry card is on Felhaven's home layout; it
ticks on its own. Click **AMMIT — COUNTDOWN** to expand the timer, type minutes,
press Enter.

**Ask Pythia** — *"what season is it?"* or *"is it evening yet?"* routes through
`get_time_context`.

**Standalone** (quick smoke check):

```
python tools/horai.py
```

Prints a one-line `[Horai] Summer | Afternoon | Tuesday, July 07 2026, 02:32 PM`.

## Tests

Horai's `handle()` is exercised alongside every other tool in the shared handle
suite (contract shape, key presence, return type):

```
python -X utf8 -m unittest tests.test_tool_handles
```
