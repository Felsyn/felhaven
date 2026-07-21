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

Contract:    Exposes TOOL_DEFINITION, handle(), and convert().
             All three never raise; error paths return {"error": ...}.
             No fetch() — Eudoxus is request-driven, not polled.

Upstream:    pythia.py (registration + dispatch)
Downstream:  panels/eudoxus_panel.py (EudoxusPanel)

Requires:    stdlib only: re, logging

US-customary defaults (for brain/panel consumers):
    gallon  = US gallon  (3.785 411 784 L)         NOT the Imperial 4.546 L
    cup     = US cup     (236.588 2365 mL)          NOT the metric 250 mL
    pint    = US pint    (473.176 473 mL)
    quart   = US quart   (946.352 946 mL)
    fl_oz   = US fluid ounce (29.573 529 5625 mL)

Fractions not supported in v1 — use decimals (0.5 cup, not 1/2 cup).
"""

import re
import math
import logging
from typing import Any

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


# ── Number formatting (kept in sync with tools/zeno.py — Anti-Legion ok) ────

def _format_number(v: Any) -> str:
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        # Guard non-finite values before the int(v) comparison below, which
        # raises on nan/inf (e.g. `inf - inf` -> nan, then int(nan) -> ValueError).
        if math.isnan(v):
            return "nan"
        if math.isinf(v):
            return "inf" if v > 0 else "-inf"
    if isinstance(v, int):
        try:
            return str(v)
        except ValueError:
            # Int beyond CPython's str-conversion cap — show a placeholder
            # rather than raise. (Conversions won't produce these; guards
            # direct callers only.)
            return f"<{v.bit_length()}-bit integer>"
    # finite float
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    # 6 significant figures: enough precision to stay useful, few enough to hide
    # float noise (0.1+0.2 -> "0.3") and repeating decimals (100°F->C -> "37.7778").
    # %g strips trailing zeros and keeps very small values (1e-8) intact.
    return f"{v:.6g}"


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
            return {"error": f"unknown unit: '{from_unit}'"}
        if to_key is None:
            return {"error": f"unknown unit: '{to_unit}'"}

        from_cat = _CATEGORY_OF.get(from_key)
        to_cat   = _CATEGORY_OF.get(to_key)

        if from_cat is None or to_cat is None:
            return {"error": f"unknown unit: '{from_unit if from_cat is None else to_unit}'"}

        if from_cat != to_cat:
            return {
                "error": f"cannot convert {from_cat} to {to_cat}",
                "from":  from_key,
                "to":    to_key,
            }

        if from_cat == "temperature":
            k_val  = _to_kelvin(float(value), from_key)
            result = _from_kelvin(k_val, to_key)
            formula = _TEMP_FORMULA.get((from_key, to_key),
                                        f"{to_key} = f({from_key})")
            return {
                "value":   value,
                "from":    from_key,
                "to":      to_key,
                "result":  result,
                "display": f"{_format_number(result)} {to_key}",
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
            "display": f"{_format_number(result)} {to_key}",
            "factor":  f"1 {from_key} = {_format_number(unit_factor)} {to_key}",
        }

    except Exception as exc:
        log.error(f"Eudoxus.handle: unexpected failure: {exc}")
        return {"error": f"conversion failed: {type(exc).__name__}"}


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
    Not supported:
        "1/2 cup to ml"           — use 0.5 cup instead
    """
    try:
        s   = expression.lower().strip()
        sep = " to " if " to " in s else (" in " if " in " in s else None)
        if sep is None:
            return {"error": "expected 'X to Y' or 'X in Y'",
                    "expression": expression}

        left, right = s.split(sep, 1)
        left, right = left.strip(), right.strip()

        m = _LEFT_RE.match(left)
        if m and m.group(2).strip():
            value    = float(m.group(1))
            from_str = m.group(2).strip()
        elif m and not m.group(2).strip():
            return {"error": "missing source unit", "expression": expression}
        else:
            value    = 1.0
            from_str = left

        result = handle(value, from_str, right)
        result["expression"] = expression
        return result

    except Exception as exc:
        log.error(f"Eudoxus.convert: unexpected failure: {exc}")
        return {"error": f"conversion failed: {type(exc).__name__}",
                "expression": expression}


# ── Public API ────────────────────────────────────────────────────────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "convert_unit",
        "description": (
            "Convert a value from one unit to another. "
            "Supports length (in/ft/yd/mi/mm/cm/m/km), "
            "volume (tsp/tbsp/cup/fl_oz/pint/quart/gallon/ml/l), "
            "weight (oz/lb/st/mg/g/kg), "
            "temperature (f/c/k), "
            "and time (s/min/hr/day/week). "
            "US-customary defaults: gallon = US gallon (3.785 L), "
            "cup = US cup (236.6 mL), pint/quart = US. "
            "Pass unit names or common abbreviations (e.g. 'miles', 'mi', 'kilometers')."
        ),
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
    import sys
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
