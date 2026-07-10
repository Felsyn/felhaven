# Ammit — Devourer of Time

*Anti-Legion: ONE JOB*

Ammit runs **one countdown timer**. Not two, not a stopwatch, not an alarm — a
single slot that counts down and freezes at zero. It shares the Chronometry card
with Horai because a timer belongs next to the clock, but the two are entirely
separate tools: Horai says *when it is*, Ammit says *how long is left*.

## The one slot

`MAX_SLOTS = 1`, on purpose. The contract still carries a `slot` parameter
(always `1`) so a future multi-timer version won't break the schema, but today
there is exactly one timer. Start it, pause it, reset it, read it — that's the
whole surface.

## State that survives restarts

The timer lives in **`timer_state.json`** at the package root (beside
`scribe_data.json`), not in memory. Close Felhaven mid-countdown, reopen it, and
the timer is still ticking — because "running" is stored as a **start timestamp +
duration**, not a live counter. Remaining time is *computed* on every read:

```
remaining = duration − (now − started_at),  floored at 0
```

That's the key design choice: nothing has to tick to keep the timer honest. The
dashboard polls, Metis polls, both derive the same remaining seconds from the
same file. Pausing (`stop`) writes the frozen remaining time back as the new
duration so it can resume later.

## Two ways in

Ammit exposes **both** the standard tool contract *and* a direct API, because it
has two very different callers:

| Surface | Who uses it | Functions |
|---|---|---|
| **Tool contract** | Metis / Pythia (the LLM) | `TOOL_DEFINITION` + `handle()` |
| **Direct API** | Felhaven's `AmmitWidget` | `start_timer`, `stop_timer`, `reset_timer`, `query_all`, `fmt` |

The LLM tool is **`manage_timer`** with one required arg `action`
(`start` / `stop` / `reset` / `query`); `start` also needs `duration_minutes`.
`handle()` is a thin router over the direct API — it converts the 1-indexed
contract slot to the 0-indexed internal slot and delegates.

The dashboard **skips `handle()` entirely** and calls the direct functions,
because the UI thinks in widget events (Enter pressed, ▶ clicked), not tool
actions. Same state file, so the two never disagree.

## `query_all()` — the read shape

```python
[{
    "slot": 1,
    "running": True,
    "remaining_seconds": 754,
    "display": "00:12:34",   # via fmt() — HH:MM:SS
    "expired": False,        # True only when running AND hit 0
}]
```

`expired` is the alarm signal the panel watches — when it flips True the display
goes red and the button becomes a ■. (There's no *sound* yet; the module header
flags "No alarm. That's next.")

## Where it renders

`AmmitWidget` inside `panels/horai_panel.py` — the collapsible **AMMIT —
COUNTDOWN** section under the clock. Type minutes, Enter or ▶ to start, ■ to
pause, ↺ to reset. Kairos refreshes it via Horai's 1 Hz tick, so the display
stays live without Ammit owning its own timer loop.

## Files

| File | Committed? | Purpose |
|---|---|---|
| `tools/ammit.py` | yes | Timer logic — persistence, the direct API, the tool contract. No tkinter. |
| `timer_state.json` | **no** (runtime) | The live timer; regenerated empty if missing or corrupt. |
| `panels/horai_panel.py` → `AmmitWidget` | yes | The UI (shared with Horai). |

## Using it

**In the dashboard** — expand **AMMIT — COUNTDOWN** on the Chronometry card.

**Ask Pythia** — *"set a 10 minute timer"* / *"how long is left?"* routes through
`manage_timer`.

**Standalone** (read current state):

```
python tools/ammit.py
```

## Tests

Covered by the shared handle suite (start/stop/reset/query round-trips through
`handle()`):

```
python -X utf8 -m unittest tests.test_tool_handles
```
