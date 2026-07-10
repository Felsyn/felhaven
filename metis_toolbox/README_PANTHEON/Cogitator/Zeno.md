# Zeno — Achilles Always Finishes

*Anti-Legion: ONE JOB*

Zeno evaluates **arithmetic expressions safely, step by step** — and shows every
reduction along the way. It's the **ZENO** tab in the Cogitator card. Named for
Zeno of Elea (the paradoxes-of-infinity philosopher), not Zeno of Citium the
Stoic — the joke being that here, unlike in the paradox, the calculation always
*reaches* its end.

## Safe by construction — an AST reducer, not `eval`

The whole point is that **it never runs your string as code.** Zeno parses the
expression to an AST, **whitelists** every allowed node (numbers, the arithmetic
operators, a fixed set of math functions, the constants `pi/e/tau/inf`, and
`ans`), and rejects everything else with a clean error. Then it **reduces**
leftmost-innermost, one node at a time, snapshotting the expression after each
step — so `calculate` returns not just a result but a `steps` array showing the
work. That's the teaching feature: it explains *how*, not just *what*.

## Three deliberate deviations from Python

Because Zeno is a **step visualizer, not an interpreter**, it intentionally
differs from real Python — and the docstring is explicit so nobody "fixes" them:

- **No short-circuiting.** `False and (1/0)` reports *"division by zero"*, not
  `False` — every node is reduced so the readout is complete.
- **bool flows as int.** `True + True` → `2`.
- **Banker's rounding.** `round(2.5)` → `2`.

## Two hazards with big integers, guarded separately

Arbitrary-precision ints create two distinct ways to hang or crash, and Zeno
guards each at a different point — a genuinely thoughtful bit of defense:

| Hazard | Example | Guard | When |
|---|---|---|---|
| **Compute hang** | `2 ** 100000000`, `factorial(999999999)` | `_guard_pow` / factorial cap (checks integer magnitude *before* computing) | pre-compute |
| **Display overflow** | `10 ** 5000` (cheap to compute, 5001 digits to render) | `_too_large` (CPython caps int→str at ~4300 digits) | post-compute |

Both surface a clean **"result too large"** instead of hanging or throwing an
opaque `ValueError`. Casting to float to check magnitude would corrupt the int
(`2**10` → `1024.0`), so the guards check `bit_length()` directly.

## Never raises

`handle(expression, ans=None)` always returns a dict — `{result, display, steps}`
on success, `{error, steps}` on failure. A syntax error, an unknown name, a
division by zero, an oversized result: each is a tidy error string the panel or
the LLM can show. The reduction loop is also capped (500 iterations) as a
belt-and-suspenders against any pathological non-terminating case.

## Contract

`TOOL_DEFINITION` (LLM name **`calculate`**) + `handle()`. **No `fetch()`** —
Zeno is request-driven, not polled. It shares its `_format_number` helper with
[Eudoxus](Eudoxus.md) (6 sig figs, hides float noise like `0.1 + 0.2 → 0.3`).

## Files

| File | Purpose |
|---|---|
| `tools/zeno.py` | The parser, validator, and step reducer. stdlib only (`ast`, `math`). |
| `panels/zeno_panel.py` → `ZenoPanel` | The **ZENO** tab (shows the step readout). |

## Using it

**In the dashboard** — the **ZENO** tab of the **Cogitator** card. `ans` refers to
the previous result.

**Ask Pythia** — *"what's sqrt(144) + log(e)?"* routes through `calculate`, and
the model can relay the steps.

**Standalone** (runs a wide battery incl. the DoS-guard cases):

```
python tools/zeno.py
```

## Tests

Covered by the shared handle suite:

```
python -X utf8 -m unittest tests.test_tool_handles
```
