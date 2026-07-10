# Themis — Keeper of Settled Law

*Anti-Legion: ONE JOB*

Themis owns `felhaven_settings.json` — the handful of **per-install
preferences** that used to be hardcoded in source. It is the single source of
truth for those values, and its one job is to load them (fail-soft), save them
(validated + atomic), and expose typed getters. It draws nothing, fetches
nothing, and calls no other module.

This is what makes Felhaven *personal-to-you* instead of personal-to-the author:
your location, units, and clock live in a file you edit from the dashboard, not
in a `.py` file you have to patch.

## What it holds

| Setting | Type | Drives |
|---|---|---|
| `latitude` / `longitude` | float (`[-90,90]` / `[-180,180]`) | weather, star map, planet positions, and — via the latitude's sign — the season's hemisphere |
| `weather_location` | str, optional | if set (a city or ZIP), overrides the coordinates **for weather only** |
| `temperature_unit` | `"F"` or `"C"` | how Aura's temperatures render (weather card + sidebar) |
| `clock_24h` | bool | 12-hour (AM/PM) vs 24-hour clocks (header, Chronometry) |

One coordinate pair thus drives weather **and** sky **and** planets **and**
season together — enter it once.

## One source, many readers

The tools read Themis **at fetch time**, not at import — so a Settings edit takes
effect on the next Kairos tick without a restart:

- [**Aura**](../Atmospherics/Aura.md) — builds its wttr.in location per fetch
  from `weather_query()` (the raw `weather_location`, else `"lat,lon"`), and emits
  both °F **and** °C so the unit toggle switches display with no re-fetch.
- [**Hypatia**](../Hypatia/Hypatia.md) — reads `latitude`/`longitude` per sky
  snapshot (the teaching presets still override latitude; longitude stays yours).
- [**Horai**](../Chronometry/Horai.md) — derives the hemisphere from
  `is_southern()` (inverting its month→season map below the equator) and the clock
  format from `clock_24h()`.

Because Aura's worker runs only every 30 minutes, the Settings tab's **Save**
also calls `Kairos.refetch("aura", "hypatia", "horai")`, nudging those workers to
re-fire on the next tick so the whole dashboard follows a change promptly.

## Fail-soft, and the env override

- **Missing or garbled file → defaults**, logged, never a crash (the §4
  config-loader rule). One bad field degrades only itself. The defaults are the
  old hardcoded values (Moundsville, WV / the Hypatia lat-lon), which now live on
  *only* as this floor.
- **`AURA_LOCATION` (env var)** still overrides the weather location for
  headless/CI use — the file is canonical, the env var is an escape hatch.

## Files

| File | Committed? | Purpose |
|---|---|---|
| `themis.py` | yes | Core logic — no tkinter, independently unit-testable (like `cerberus.py`). |
| `felhaven_settings.json` | **no** (gitignored) | Your settings. Per-user *state*, not shipped config — so the author's location never travels with a clone. Absent on a fresh clone → defaults. |
| `panels/themis_panel.py` | yes | UI: the SETTINGS tab in Moderati. |

## Using it

**In the dashboard** — open the **SETTINGS** tab (5th tab in Moderati): enter
latitude / longitude and an optional weather location, pick °F/°C and 12h/24h,
and **Save**. Invalid input (a non-numeric or out-of-range coordinate) is
rejected with a message and nothing is written; a good save persists atomically
and nudges the workers, and the status line confirms it.

**From code** — `themis.load()` for the whole dict, or the typed getters
(`latitude()`, `weather_query()`, `temperature_unit()`, `clock_24h()`,
`is_southern()`). `themis.save(...)` validates and writes.

## Tests

```
python -X utf8 -m unittest tests.test_themis \
    tests.test_themis_panel_smoke tests.test_horai
```
