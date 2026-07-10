# Eudoxus — Theory of Proportions

*Anti-Legion: ONE JOB*

Eudoxus **converts between units** — length, volume, weight, temperature, and
time. It's the **EUDOXUS** tab in the Cogitator card. Named for Eudoxus of Cnidus
(~390–337 BC), who developed the theory of proportions — the mathematical
foundation of unit conversion itself.

## Two ways to ask

Eudoxus accepts both a **structured** call and a **natural-language** string:

| Entry | Form | Example |
|---|---|---|
| `handle(value, from_unit, to_unit)` | structured (this is the LLM tool `convert_unit`) | `handle(10, "mi", "km")` |
| `convert(expression)` | natural "X to Y" / "X in Y" | `convert("10 mi to km")` |

`convert()` parses the messy human forms — `"10mi to km"` (no space), `"10 fl oz
to ml"` (unit with an internal space), `"1.5 cups in liters"`, `"1e3 m to km"`
(scientific notation), `"-40 f to c"` (negative), and bare `"gallon to cup"`
(value defaults to 1) — then delegates to the same `handle()`. A big **alias
table** maps "miles"/"mile"/"mi" and dozens of others to canonical keys.

## US-customary by default — stated, not assumed

The one place a converter can quietly be *wrong* is which "gallon" it means.
Eudoxus commits to **US customary** and documents it in the tool description so
the model relays it honestly:

- gallon = **US** gallon (3.785 L), *not* Imperial (4.546 L)
- cup = **US** cup (236.6 mL), *not* metric (250 mL)
- pint / quart / fl_oz = US

Volume factors use precise NIST values so integer ratios come out clean
(gallon = 16 cup exactly, not 15.9998).

## Temperature is special-cased

Length/volume/weight/time are all **linear** — convert via a base unit and a
single ratio, and the result carries a `"1 mi = 1.60934 km"`-style factor string.
Temperature is **affine** (offset, not just scale), so it routes through Kelvin
(`_to_kelvin` → `_from_kelvin`) and returns the actual **formula**
(`"°C = (°F − 32) × 5/9"`) instead of a ratio. Same result shape, honest label.

## Never raises; refuses nonsense cleanly

`handle()` and `convert()` always return a dict. Cross-category conversions are
caught explicitly — `convert("mi to cup")` returns *"cannot convert length to
volume"* rather than a garbage number. Unknown units, missing source units, and
unparseable expressions each get their own error string. **Fractions aren't
supported** (`"1/2 cup"`) — use decimals; that's a documented v1 limit, not a bug.

## Contract

`TOOL_DEFINITION` (LLM name **`convert_unit`**, requires `value` / `from_unit` /
`to_unit`) + `handle()` + `convert()`. **No `fetch()`** — request-driven, not
polled. Shares `_format_number` with [Zeno](Zeno.md).

## Files

| File | Purpose |
|---|---|
| `tools/eudoxus.py` | Conversion tables, the affine temperature path, the NL parser. stdlib only (`re`, `math`). |
| `panels/eudoxus_panel.py` → `EudoxusPanel` | The **EUDOXUS** tab. |

## Using it

**In the dashboard** — the **EUDOXUS** tab of the **Cogitator** card.

**Ask Pythia** — *"what's 72°F in Celsius?"* / *"how many cups in a gallon?"*
routes through `convert_unit`.

**Standalone** (runs a spread incl. cross-category + unknown-unit refusals):

```
python tools/eudoxus.py
```

## Tests

Covered by the shared handle suite:

```
python -X utf8 -m unittest tests.test_tool_handles
```
