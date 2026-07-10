"""
_fixtures.py — shared test payloads for the Aura suite.

The base fixture is a *captured real* wttr.in j1 response (tests/fixtures/
wttr_j1.json): 3 days x 8 hourly blocks, every numeric value a string — exactly
the shape that would expose the string-typed `chanceofrain` cast bug a hand-built
minimal fixture could hide (the Midas set_summary lesson).

Edge variants are derived from that real payload (each call reloads a fresh copy
from disk, so mutations never leak between tests) rather than hand-built, so the
surrounding structure stays faithful and only the field under test changes.

Leading underscore => unittest's discover() does not collect this as a test module.
"""

import json
import os

_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "wttr_j1.json")


def load_real_j1() -> dict:
    """A fresh copy of the captured real wttr.in j1 payload."""
    with open(_FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def two_day_payload() -> dict:
    """Real payload trimmed to 2 forecast days (drives the <3-entry path)."""
    raw = load_real_j1()
    raw["weather"] = raw["weather"][:2]
    return raw


def snow_dominant_payload() -> dict:
    """Real payload with day 2's snow pushed above its rain."""
    raw = load_real_j1()
    for h in raw["weather"][2]["hourly"]:
        h["chanceofsnow"] = "90"
        h["chanceofrain"] = "10"
    return raw


def missing_hourly_payload() -> dict:
    """Real payload with day 1's hourly block list removed (degradation path)."""
    raw = load_real_j1()
    del raw["weather"][1]["hourly"]
    return raw
