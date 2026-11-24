"""
ptolemy.py — The Almagest (the sky, for Pythia)
===============================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Answer Pythia's questions about the sky: the sun, the moon and what is up now.
             Three modules already compute the sky, and each used to be its own
             brain tool: Helios (sunrise, sunset, golden hours, day length),
             Selene (phase, illumination, moonrise, moonset) and Hypatia (the
             planets and brightest stars above the horizon). Ptolemy is the one
             tool the model sees in their place, get_sky_report, and it always
             asks all three. It computes nothing itself.

Contract:    Brain tool only: TOOL_DEFINITION + handle(), no arguments. No
             fetch(): the panels and Kairos workers read the three modules
             directly, exactly as before. handle() calls each module's own
             handle(), the function that already answered the model, so the
             panel and the tool still read one function each and nothing is
             derived twice. Never raises.

             The three answers merge flat (their keys don't overlap, checked
             2026-11-14). A part that failed becomes one key named for the part
             (sun / moon / sky), holding its error string, so one failure never
             blanks the rest: a polar day keeps Helios's own reason, "the sun
             does not set today", under `sun`. A part whose keys would collide
             with one already merged is nested under its name instead of
             overwriting. Every part failed → {"error": "unavailable: ..."}.

             No `part` argument, unlike Asclepius: all three are local
             arithmetic, about 5 ms together (4.6 ms median, measured
             2026-11-20), so there is nothing slow to skip, and nothing for
             the model to pick wrong. The price is about 1,000 characters per
             answer (800 to 1,200 over a fortnight at three places, measured
             2026-11-20), planets and stars included when only sunset was
             asked. The merge rule is Asclepius's, written
             out twice rather than shared: two facades do not yet earn a module
             of their own (CONVENTIONS §12, 2026-11-14).

Source:      handle() of tools/helios.py, tools/selene.py, tools/hypatia.py,
             one after another. No network, so it answers offline.

Upstream:    pythia.py (registration + dispatch, as get_sky_report).
Downstream:  tools/helios.py, tools/selene.py, tools/hypatia.py.

Requires:    tools/helios.py, tools/selene.py, tools/hypatia.py; logging, os,
             sys, types, typing (stdlib).

2026-11-16:  the Pythia tool audit (CONVENTIONS §12). Born in that audit
             (2026-11-14), so this was its first marker: one tool in place of
             three, its description keeping their trigger words and following
             §3's grammar. TOOL_META says sky / local / changes nothing. Its
             one slug of its own is the shared unavailable (T9); its parts
             keep theirs.

V0.03:       Walked through 2026-11-20 (the V0.03 walkthrough, CONVENTIONS
             §3/§12), after its three parts (Hypatia in S20, Helios and Selene
             here), so every error it carries now says the shared
             `unavailable`. Checked: the three parts' keys still don't overlap;
             no caller but pythia.py; never raises. Changed: the cost and size
             re-measured (about 5 ms, about 1,000 characters, where it said
             under 5 ms and ~900); Requires gains typing. Code untouched.
"""

import logging
import os
import sys
from types import ModuleType
from typing import Any

# Only a bare `python tools/ptolemy.py` needs the app root added before the
# tools/ import (the aura placement); everything else already has it.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import helios, hypatia, selene

log = logging.getLogger("METIS.ptolemy")

# ── Parts ─────────────────────────────────────────────────────────────────────
# part → the module whose handle() answers it, in merge order. Modules, not
# their functions, so the call is looked up when it is made.
_PARTS: dict[str, ModuleType] = {
    "sun":  helios,
    "moon": selene,
    "sky":  hypatia,
}

# ── Contract ──────────────────────────────────────────────────────────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "get_sky_report",
        "description": (
            "Returns the sky for the dashboard's location: today's sunrise, "
            "sunset, golden hours and day length; the moon's phase, "
            "illumination, moonrise and moonset; and which planets and bright "
            "stars are above the horizon now. Call when the user asks about "
            "sunrise or sunset, daylight, the moon, what is in the sky, or "
            "whether a planet is up."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

# What the code reads about each tool here; the model never sees it
# (CONVENTIONS §8, "TOOL_META"). network: none = local only, cached = a poll
# or TTL cache, live = may reach the network on every call. mutates = it
# changes state outside the turn.
TOOL_META: dict[str, dict[str, Any]] = {
    "get_sky_report": {"domain": "sky", "network": "none", "mutates": False},
}


def _read(part: str) -> dict[str, Any]:
    """One part's own handle(), guarded: those never raise either, but a
    throw here would reach Pythia's dispatcher, so this answers for them."""
    try:
        result = _PARTS[part].handle()
    except Exception as e:  # noqa: BLE001 — never raise into the dispatcher
        log.warning("Ptolemy: %s raised %s", part, type(e).__name__)
        return {"error": f"unavailable: {part} raised {type(e).__name__}"}
    if not isinstance(result, dict):
        log.warning("Ptolemy: %s returned %s, not a dict", part,
                    type(result).__name__)
        return {"error": f"unavailable: {part} returned no reading"}
    return result


def handle() -> dict[str, Any]:
    """Brain entry point (get_sky_report): every part, merged flat. A failed
    part is one key holding its error; a part whose keys collide is nested
    under its name; all failed → error. Never raises."""
    out: dict[str, Any] = {}
    failures: list[str] = []
    for part in _PARTS:
        result = _read(part)
        if "error" in result:
            out[part] = result["error"]
            failures.append(f"{part}: {result['error']}")
        elif out.keys() & result.keys():
            log.warning("Ptolemy: %s keys collide with an earlier part's; "
                        "nesting it", part)
            out[part] = result
        else:
            out.update(result)
    if len(failures) == len(_PARTS):
        return {"error": f"unavailable: {'; '.join(failures)}"}
    return out


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"[Ptolemy] {handle()}")
