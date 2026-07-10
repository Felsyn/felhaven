# Hephaestus — God of the Forge

*Anti-Legion: ONE JOB*

Hephaestus answers **"how does the machine feel?"** — CPU load, RAM headroom,
disk space, right now. It's the first tab in the Moderati card and the smith at
the forge: it watches the hardware so nothing else has to.

## What it reports

`handle()` takes no arguments and returns a flat vitals snapshot:

```python
{
    "node": "FELHAVEN-PC",         # machine name
    "os":   "Windows",
    "cpu":    {"usage_percent": 12.4, "cores": 16, "load_avg": [...] | "N/A"},
    "memory": {"total_gb": 32.0, "available_gb": 18.7, "percent_used": 41.5},
    "storage":{"total_gb": 931.5, "free_gb": 402.1, "percent_used": 56.8},
    "timestamp": "2026-07-07T14:32:10",
}
```

Two portability details worth knowing:

- **CPU% blocks for 1 second** (`psutil.cpu_percent(interval=1)`) to sample a real
  window rather than returning a meaningless instantaneous 0. That's why the tool
  isn't quite free to call.
- **Disk is measured on the drive Felhaven runs from**, derived from
  `sys.argv[0]`'s anchor — so it reads C:\ on a fixed install and E:\ off a flash
  drive, and still resolves `/` on Linux/macOS. `load_avg` degrades to `"N/A"`
  where the OS has no equivalent (Windows).

## Contract

Standard toolbox shape — `TOOL_DEFINITION` (LLM name **`get_system_vitals`**, no
params) + `handle()`. Plus `fetch = handle` as the Kairos entry point, so the
panel and the LLM read the exact same numbers.

## Requires psutil

This is one of the two modules (with Argus) that needs the `psutil` dependency —
it's how Felhaven reads hardware counters portably. `pip install psutil`.

## Where it renders

`VitalsPanel` (`panels/hephaestus_panel.py`), the **HEPHAESTUS** tab in the
Moderati card. Kairos ticks it under the worker name `hephaestus`.

**It also hosts Aether** — the connectivity strip inside the vitals panel is
[Aether](Aether.md), registered with Kairos separately as `hephaestus.aether`.
Same host-a-neighbor pattern as Chronometry (Horai hosting Ammit): the smith
watches the hardware, Aether watches the wire, they just share a tab.

## Files

| File | Purpose |
|---|---|
| `tools/hephaestus.py` | Vitals logic — the psutil reads, the contract. No tkinter. |
| `panels/hephaestus_panel.py` → `VitalsPanel` | The HEPHAESTUS tab; hosts the Aether strip. |

## Using it

**In the dashboard** — Moderati card → **HEPHAESTUS** tab.

**Ask Pythia** — *"is the machine tired?"* / *"how much RAM is free?"* routes
through `get_system_vitals`.

**Standalone**:

```
python tools/hephaestus.py
```

## Tests

Covered by the shared handle suite:

```
python -X utf8 -m unittest tests.test_tool_handles
```
