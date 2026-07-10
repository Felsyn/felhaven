# Emanon — The Unnamed Watcher

*Anti-Legion: ONE JOB*

**"Emanon" is "no name" reversed** — the watcher is invisible until something
goes wrong. Its job: read the logs the rest of the stack writes, report what
fired / responded / broke, and **never act on it**. It's the red **EMANON** tab
in the Moderati card.

## Diagnoses, never treats

This is the whole ethic (inherited from an earlier module, Hygieia):

- **Read-only** to the log files. Writes nothing.
- **Never** kills, restarts, or signals anything.
- **Makes no decisions** based on what it reads.

Emanon tells you the boiler is hissing; it never touches the valve. That
separation is deliberate — a watcher that also acts is two jobs, and the failure
modes multiply.

## Panel-only — no LLM contract

Unlike its Moderati neighbors, Emanon exposes **`fetch()` only** — no
`handle()`, no `TOOL_DEFINITION`. It never enters Pythia's tool list. (Same
"dashboard-only watcher" shape as Argus's original design.) The reasoning: a
rolling log tail is a *display* surface for a human glancing at the sidebar dot,
not something the LLM needs to reason over.

## How it reads

`metis_logging.py` writes `logs/*.log`, each line ` | `-delimited as
`timestamp | level | logger | message`. Emanon:

1. Reads the **last ~64 KB** of each file (not the whole thing — cheap every
   tick even as logs grow), discarding the partial first line after the seek.
2. Splits on ` | ` — **no regex**. Lines that don't match (tracebacks, blanks)
   are skipped, never errored.
3. **Drops its own** `METIS.emanon` lines — a watcher reporting "I read the logs"
   would be an infinite feedback loop.
4. Merges all files, sorts by timestamp (ISO-like fixed-width, so lexical sort ==
   chronological), counts, and trims to the most recent 40 for display.

## The status verdict

Many log lines collapse to one sidebar dot by precedence:

| Verdict | Trigger in the recent window |
|---|---|
| **failed** | any ERROR / CRITICAL line (short-circuits) |
| **degraded** | any WARNING line |
| **nominal** | only INFO — everything humming |

```python
fetch() -> {"status", "error_count", "warning_count", "entries", "files"}
```

## Never raises

A watcher that crashes is worse than useless, so every failure path returns a
safe dict instead of throwing — a missing `logs/` dir returns `nominal` with a
note; an unexpected error returns `degraded` with `"watcher error: <type>"`. The
panel always gets something to show.

## No polling of its own

Emanon owns **no** clock — no while-loop, no sleep, no snapshot file (that was
Hygieia's cross-process daemon design). It lives **in-process**; Kairos calls
`fetch()` on each tick and gets a fresh tail snapshot. Registered under the
worker name `emanon`.

## Files

| File | Purpose |
|---|---|
| `tools/emanon.py` | The log reader + verdict. stdlib only (pathlib, collections). |
| `panels/emanon_panel.py` → `EmanonPanel` | The EMANON tab — the log-tail display. |

Upstream is `metis_logging.py` (writes the files); the ` | ` delimiter here must
stay in lockstep with that module's `_DELIM`.

## Using it

**In the dashboard** — Moderati card → **EMANON** tab.

**Standalone** (prints the verdict + last 10 lines):

```
python tools/emanon.py
```
