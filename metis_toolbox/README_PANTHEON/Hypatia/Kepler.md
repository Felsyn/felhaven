# Kepler — Positions of the Wanderers

*Anti-Legion: ONE JOB*

Kepler knows **where the wanderers are** — the five classical naked-eye planets
(Mercury, Venus, Mars, Jupiter, Saturn), computed as alt/az for the observer.
It has no panel and no card of its own: it's orbital math feeding
[Hypatia](Hypatia.md)'s star map.

## A pure module, composed by Hypatia

Kepler is the purest shape in the toolbox (the Helios/Selene row of CONVENTIONS
§2): **no `fetch()`, no `handle()`, no `TOOL_DEFINITION`, absent from
`__init__.py`.** It exposes one public function — `positions(unix, lat, lon)` —
and Hypatia's `_build_snapshot()` calls it, the same way Midas composes Plutus.

```python
positions(now, lat, lon)
# -> [{"name": "Mars", "glyph": "♂", "alt": 34.2, "az": 118.7}, ...]
```

**Out of LLM scope on purpose** — the planets still reach the model, but through
Hypatia's `get_sky_tonight` digest, not a Kepler tool. (This is the one docstring
in the neighborhood that's *accurate* about having no contract — unlike Argus,
Helios, Selene, and Hypatia, whose "no tool" claims had gone stale and were
corrected. Kepler genuinely stays pure.)

## The math (and why it's shared, not duplicated)

Low-precision Keplerian elements from **Standish (2006)**, JPL SSD — J2000 mean
elements + per-century secular rates, valid 1800–2050. Per planet:

1. Solve Kepler's equation (Newton iteration, 1e-6 rad) for the eccentric anomaly.
2. Build the heliocentric ecliptic position; subtract Earth's own for a
   geocentric vector.
3. Rotate ecliptic → equatorial by the mean obliquity to get RA/Dec.
4. **Reuse `hypatia._altaz` verbatim** for RA/Dec → Alt/Az — no duplicate trig.

Accuracy is ~1° over centuries — a few pixels at chart scale. Like Hypatia's star
math, it's **deliberately low-precision; don't "improve" it.** No light-time or
relativistic correction, no Sun marker (deferred — the math yields it for free),
no Uranus/Neptune, and **no Moon, ever** — the Moon is [Selene](../Atmospherics/Selene.md)'s
domain, permanently. That boundary is a standing decision, not an omission.

## Degrades per planet

`positions()` never raises. If the math fails for one body — even Earth's own
position, which every planet needs — that planet is dropped and logged. A chart
missing Mars beats a dead Kairos worker. `_radec()` deliberately recomputes
Earth's position on every call rather than threading it through, so a degenerate
timestamp fails identically for Earth and the planet, keeping the per-planet
degrade clean.

## The one import quirk

Kepler is the first tool module to import a *sibling* tool module
(`from tools import hypatia`). Under normal operation (via felhaven / kairos /
tests) the app root is already on `sys.path`, so nothing special is needed — but
a bare `python tools/kepler.py` standalone run inserts the app root first (see
the `__main__` guard). Note the shape: Hypatia imports Kepler *and* Kepler imports
Hypatia — the cycle is safe because Kepler only reaches for Hypatia's stateless
math functions (`_julian_date`, `_altaz`) at call time, not import time.

## Files

| File | Purpose |
|---|---|
| `tools/kepler.py` | The orbital math. stdlib only (math, logging). No network, no state, no panel. |

## Using it

Not user-facing on its own — it renders as the planet glyphs on the Hypatia
Celestarium, and answers via `get_sky_tonight`.

**Standalone** (prints all five planets' current positions):

```
python tools/kepler.py
```

## Tests

Kepler has its **own** suite — `tests/test_kepler.py` pins its geocentric RA/Dec
against JPL Horizons values (the one module precise enough to be worth pinning):

```
python -X utf8 -m unittest tests.test_kepler
```
