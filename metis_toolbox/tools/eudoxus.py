"""
eudoxus.py — Theory of Proportions
=====================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Convert between units of length, volume, weight, temperature,
             and time. Parse natural "X to Y" / "X in Y" expressions or
             accept structured (value, from_unit, to_unit) calls.
             Named for Eudoxus of Cnidus (~390–337 BC), Greek astronomer
             and mathematician who developed the theory of proportions —
             the mathematical foundation of unit conversion.

Contract:    Exposes TOOL_DEFINITION, handle() and convert().
             handle(value, from_unit, to_unit) is the brain tool; convert()
             parses an expression and calls it. Neither raises — a brain tool
             that throws crashes Pythia's dispatcher (§2) — and every failure
             path returns {"error": <slug>} instead.
             No fetch() — Eudoxus is request-driven, not polled.

Errors:      Identifiers are §2 slugs; the text after ": " is human detail:

               unknown_unit: X          X is not in _ALIASES — as the caller
                                        wrote it, not normalized
               incompatible_units: X    a category mismatch, "length to weight"
               missing_separator        no " to " / " in " in the expression
               missing_source_unit      a number with no unit after it
               conversion_failed: X     unforeseen; X is the exception CLASS
                                        name, never its message (§2)

             The last two are reachable only through convert(). Pythia calls
             handle() directly, so the parser's errors are panel-facing.

US-customary defaults (for brain/panel consumers):
    gallon  = US gallon  (3.785 411 784 L)         NOT the Imperial 4.546 L
    cup     = US cup     (236.588 2365 mL)          NOT the metric 250 mL
    pint    = US pint    (473.176 473 mL)
    quart   = US quart   (946.352 946 mL)
    fl_oz   = US fluid ounce (29.573 529 5625 mL)

Fractions are not supported — use decimals (0.5 cup, not 1/2 cup).

Upstream:    pythia.py (registration + dispatch)
Downstream:  panels/eudoxus_panel.py (EudoxusPanel)

Requires:    stdlib: re, os, sys, logging, typing. Plus tools/zeno.py for
             format_number() — the kepler→hypatia precedent (§12). Eudoxus
             kept its own copy of those rules until 2026-07-23, marked "kept
             in sync with tools/zeno.py", which is a promise no file can keep.

V0.02:       Audited 2026-07-23 — error sentences normalized to §2 slugs, the
             duplicated number formatter replaced by zeno's public one, the
             " in " separator no longer mis-split "3 in in cm", first tests.
"""

import os
import re
import sys
import logging
from typing import Any

# The kepler.py precedent (§12): a tool importing a sibling tool. Every real
# entry point (felhaven, kairos, pythia, the tests) already runs with the app
# root on sys.path; only a bare `python tools/eudoxus.py` needs it added, and
# the insert must precede the import.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import zeno

log = logging.getLogger("METIS.eudoxus")


# ── Conversion tables (base units listed in comments) ────────────────────────

_LENGTH = {      # base: meter
    "in":  0.0254,
    "ft":  0.3048,
    "yd":  0.9144,
    "mi":  1609.344,
    "mm":  0.001,
    "cm":  0.01,
    "m":   1.0,
    "km":  1000.0,
}

_VOLUME = {      # base: milliliter (US customary)
    # Precise NIST values so integer ratios (gallon=16cup, etc.) come out clean
    "tsp":    4.92892159375,
    "tbsp":   14.78676478125,
    "fl_oz":  29.5735295625,
    "cup":    236.5882365,
    "pint":   473.176473,
    "quart":  946.352946,
    "gallon": 3785.411784,
    "ml":     1.0,
    "l":      1000.0,
}

_WEIGHT = {      # base: gram
    "oz": 28.349523125,
    "lb": 453.59237,
    "st": 6350.29318,
    "mg": 0.001,
    "g":  1.0,
    "kg": 1000.0,
}

_TIME = {        # base: second
    "s":    1.0,
    "min":  60.0,
    "hr":   3600.0,
    "day":  86400.0,
    "week": 604800.0,
}

# Reverse map: canonical unit key → category name
_CATEGORY_OF: dict[str, str] = {}
for _u in _LENGTH: _CATEGORY_OF[_u] = "length"
for _u in _VOLUME: _CATEGORY_OF[_u] = "volume"
for _u in _WEIGHT: _CATEGORY_OF[_u] = "weight"
for _u in _TIME:   _CATEGORY_OF[_u] = "time"
for _u in ("f", "c", "k"): _CATEGORY_OF[_u] = "temperature"

_TABLES = {
    "length": _LENGTH,
    "volume": _VOLUME,
    "weight": _WEIGHT,
    "time":   _TIME,
}


# ── Temperature (affine — special-cased) ─────────────────────────────────────

def _to_kelvin(value: float, unit: str) -> float:
    if unit == "k": return value
    if unit == "c": return value + 273.15
    if unit == "f": return (value - 32) * 5 / 9 + 273.15
    raise ValueError(f"unknown temp unit: {unit}")


def _from_kelvin(value: float, unit: str) -> float:
    if unit == "k": return value
    if unit == "c": return value - 273.15
    if unit == "f": return (value - 273.15) * 9 / 5 + 32
    raise ValueError(f"unknown temp unit: {unit}")


_TEMP_FORMULA = {
    ("f", "c"): "°C = (°F − 32) × 5/9",
    ("c", "f"): "°F = °C × 9/5 + 32",
    ("f", "k"): "K = (°F − 32) × 5/9 + 273.15",
    ("k", "f"): "°F = (K − 273.15) × 9/5 + 32",
    ("c", "k"): "K = °C + 273.15",
    ("k", "c"): "°C = K − 273.15",
    ("f", "f"): "°F = °F",
    ("c", "c"): "°C = °C",
    ("k", "k"): "K = K",
}


# ── Aliases (user-facing strings → canonical keys) ───────────────────────────

_ALIASES: dict[str, str] = {
    # length
    "in": "in", "inch": "in", "inches": "in",
    "ft": "ft", "foot": "ft", "feet": "ft",
    "yd": "yd", "yard": "yd", "yards": "yd",
    "mi": "mi", "mile": "mi", "miles": "mi",
    "mm": "mm", "millimeter": "mm", "millimeters": "mm",
    "cm": "cm", "centimeter": "cm", "centimeters": "cm",
    "m":  "m",  "meter": "m",  "meters": "m",  "metre": "m",  "metres": "m",
    "km": "km", "kilometer": "km", "kilometers": "km",
    # volume
    "tsp": "tsp", "teaspoon": "tsp", "teaspoons": "tsp",
    "tbsp": "tbsp", "tablespoon": "tbsp", "tablespoons": "tbsp",
    "cup": "cup", "cups": "cup",
    "fl_oz": "fl_oz", "fl oz": "fl_oz", "floz": "fl_oz",
    "fluid ounce": "fl_oz", "fluid ounces": "fl_oz",
    "pint": "pint", "pints": "pint", "pt": "pint",
    "quart": "quart", "quarts": "quart", "qt": "quart",
    "gallon": "gallon", "gallons": "gallon", "gal": "gallon",
    "ml": "ml", "milliliter": "ml", "milliliters": "ml",
    "l":  "l",  "liter": "l", "liters": "l", "litre": "l", "litres": "l",
    # weight
    "oz": "oz", "ounce": "oz", "ounces": "oz",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "st": "st", "stone": "st", "stones": "st",
    "mg": "mg", "milligram": "mg", "milligrams": "mg",
    "g":  "g",  "gram": "g",  "grams": "g",
    "kg": "kg", "kilogram": "kg", "kilograms": "kg",
    # temperature
    "f": "f", "fahrenheit": "f", "°f": "f",
    "c": "c", "celsius": "c", "centigrade": "c", "°c": "c",
    "k": "k", "kelvin": "k",
    # time
    "s": "s", "sec": "s", "secs": "s", "second": "s", "seconds": "s",
    "min": "min", "mins": "min", "minute": "min", "minutes": "min",
    "hr": "hr", "hrs": "hr", "hour": "hr", "hours": "hr",
    "day": "day", "days": "day",
    "week": "week", "weeks": "week",
}


# ── Structured API ────────────────────────────────────────────────────────────

def handle(value: float, from_unit: str, to_unit: str) -> dict[str, Any]:
    """
    Convert value from from_unit to to_unit. Never raises.

    Success:
        {"value": float, "from": str, "to": str, "result": float,
         "display": str, "factor": str}
    Failure:
        {"error": str}   (+ "from"/"to" for cross-category errors)
    """
    try:
        from_key = _ALIASES.get(str(from_unit).lower().strip())
        to_key   = _ALIASES.get(str(to_unit).lower().strip())

        if from_key is None:
            return {"error": f"unknown_unit: {from_unit}"}
        if to_key is None:
            return {"error": f"unknown_unit: {to_unit}"}

        from_cat = _CATEGORY_OF.get(from_key)
        to_cat   = _CATEGORY_OF.get(to_key)

        # Unreachable while every _ALIASES target has a category — which is
        # what tests/test_eudoxus.py asserts, rather than trusting it. Kept so
        # an alias added without a table entry degrades instead of KeyError-ing.
        if from_cat is None or to_cat is None:
            return {"error": f"unknown_unit: {from_unit if from_cat is None else to_unit}"}

        if from_cat != to_cat:
            return {
                "error": f"incompatible_units: {from_cat} to {to_cat}",
                "from":  from_key,
                "to":    to_key,
            }

        if from_cat == "temperature":
            k_val  = _to_kelvin(float(value), from_key)
            result = _from_kelvin(k_val, to_key)
            # The .get default is unreachable while _TEMP_FORMULA holds all
            # nine f/c/k pairs (asserted in tests/test_eudoxus.py); it exists
            # so a fourth temperature unit degrades to a generic formula line.
            formula = _TEMP_FORMULA.get((from_key, to_key),
                                        f"{to_key} = f({from_key})")
            return {
                "value":   value,
                "from":    from_key,
                "to":      to_key,
                "result":  result,
                "display": f"{zeno.format_number(result)} {to_key}",
                "factor":  formula,
            }

        table      = _TABLES[from_cat]
        base       = float(value) * table[from_key]
        result     = base / table[to_key]
        unit_factor = table[from_key] / table[to_key]
        return {
            "value":   value,
            "from":    from_key,
            "to":      to_key,
            "result":  result,
            "display": f"{zeno.format_number(result)} {to_key}",
            "factor":  f"1 {from_key} = {zeno.format_number(unit_factor)} {to_key}",
        }

    except Exception as exc:
        log.error(f"Eudoxus.handle: unexpected failure: {type(exc).__name__}: {exc}")
        return {"error": f"conversion_failed: {type(exc).__name__}"}


# ── Expression parser ─────────────────────────────────────────────────────────

_LEFT_RE = re.compile(r'^(-?\d*\.?\d+(?:[eE][+-]?\d+)?)\s*(.*)$')


def convert(expression: str) -> dict[str, Any]:
    """
    Parse a natural-language conversion expression and call handle().
    Accepts "X to Y" and "X in Y" forms. Never raises.

    Supported:
        "gallon to cup"           →  value=1, from=gallon
        "10 mi to km"             ✓
        "10mi to km"              ✓  (no space between number and unit)
        "10 fl oz to ml"          ✓  (unit with internal space)
        "1.5 cups in liters"      ✓
        "1e3 m to km"             ✓  (scientific notation)
        "-40 f to c"              ✓  (negative value)
        "3 in in cm"              ✓  ("in" is also a unit — see below)
    Not supported:
        "1/2 cup to ml"           — use 0.5 cup instead
    """
    try:
        s   = expression.lower().strip()
        sep = " to " if " to " in s else (" in " if " in " in s else None)
        if sep is None:
            return {"error": "missing_separator", "expression": expression}

        # rsplit, not split: "in" is a unit as well as a separator, so
        # "3 in in cm" has two candidates and only the LAST one is the
        # separator. No unit name contains " to " or " in ", so the right-hand
        # side can never hold one — splitting from the right is always safe.
        left, right = s.rsplit(sep, 1)
        left, right = left.strip(), right.strip()

        m = _LEFT_RE.match(left)
        if m and m.group(2).strip():
            value    = float(m.group(1))
            from_str = m.group(2).strip()
        elif m and not m.group(2).strip():
            return {"error": "missing_source_unit", "expression": expression}
        else:
            value    = 1.0
            from_str = left

        result = handle(value, from_str, right)
        result["expression"] = expression
        return result

    except Exception as exc:
        log.error(f"Eudoxus.convert: unexpected failure: {type(exc).__name__}: {exc}")
        return {"error": f"conversion_failed: {type(exc).__name__}",
                "expression": expression}


# ── Public API ────────────────────────────────────────────────────────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "convert_unit",
        "description": (
            "Convert a value from one unit to another. Call when the user asks "
            "how much one unit is in another, or to restate a measurement in "
            "different units. "
            "Supports length (in/ft/yd/mi/mm/cm/m/km), "
            "volume (tsp/tbsp/cup/fl_oz/pint/quart/gallon/ml/l), "
            "weight (oz/lb/st/mg/g/kg), "
            "temperature (f/c/k), "
            "and time (s/min/hr/day/week). "
            "Unit names or common abbreviations both work ('miles', 'mi')."
        ),
        # The US-customary reference values (gallon = 3.785 L, cup = 236.6 mL,
        # US pint/quart) live in this module's header docstring. They are how the
        # conversion behaves, not something the model can act on when deciding to
        # call — and in here they were billed on every request.
        "parameters": {
            "type": "object",
            "properties": {
                "value": {
                    "type": "number",
                    "description": "The numeric value to convert.",
                },
                "from_unit": {
                    "type": "string",
                    "description": "Source unit (e.g. 'miles', 'mi', 'kilograms', 'kg').",
                },
                "to_unit": {
                    "type": "string",
                    "description": "Target unit (e.g. 'km', 'kilometers', 'lb').",
                },
            },
            "required": ["value", "from_unit", "to_unit"],
        },
    },
}


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import io
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    cases = [
        "gallon to cup",
        "10 mi to km",
        "5 kg to lb",
        "72 f to c",
        "-40 f to c",
        "90 min to hr",
        "10 parsec to mi",
        "mi to cup",
        "what is a meter",
    ]
    for expr in cases:
        r = convert(expr)
        print(f"\n  {expr!r}")
        if "error" in r:
            print(f"  ERROR: {r['error']}")
        else:
            print(f"  = {r['display']}")
            print(f"    {r['factor']}")
