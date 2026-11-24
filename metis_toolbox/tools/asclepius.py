"""
asclepius.py — The Physician (the machine's status, for Pythia)
===============================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Answer Pythia's questions about this computer: vitals, connection and network.
             Three modules already measure the machine, and each used to be
             its own brain tool: Hephaestus (CPU, RAM, disks, uptime, GPU),
             Aether (WiFi, internet, Ollama) and Argus (connections,
             throughput, firewall). Asclepius is the one tool the model sees
             in their place, get_machine_status, and its `part` argument picks
             which of them to ask. It measures nothing itself.

Contract:    Brain tool only: TOOL_DEFINITION + handle(part). No fetch(): the
             panels and Kairos workers read the three modules directly, exactly
             as before. handle() calls each module's own handle(), the function
             that already answered the model, so the panel and the tool still
             read one function each and nothing is derived twice. Never raises.

             part = vitals / online / network returns that module's dict
             unchanged, its own error dict included. part = all merges the
             three flat (their keys don't overlap, checked 2026-11-14 and
             2026-11-21); a part that failed becomes one key named for the
             part, holding its error string, so one failure never blanks the
             rest. A part whose keys
             would collide with one already merged is nested under its name
             instead of overwriting. Every part failed → {"error":
             "unavailable: ..."}. A part outside the enum reaches the handler
             (the argument gate leaves enum membership to handlers, §8) and
             gets unknown_action.

Source:      handle() of tools/hephaestus.py, tools/aether.py, tools/argus.py,
             one after another. Measured on the Obelisk, online, each part's
             first call (2026-11-14, and again 2026-11-21): Hephaestus 1.4 s
             (psutil's one-second CPU sample), Aether 0.6 s, Argus 0.1–0.2 s
             (its first call starts netsh and ipconfig). Aether's internet
             probe can wait 5 s, which is why `part` exists: "how's the CPU?"
             never runs it.

Upstream:    pythia.py (registration + dispatch, as get_machine_status).
Downstream:  tools/hephaestus.py, tools/aether.py, tools/argus.py.

Requires:    tools/hephaestus.py, tools/aether.py, tools/argus.py; logging, os,
             sys, types, typing (stdlib).

2026-11-16:  Audited (the Pythia tool audit, CONVENTIONS §12). Born in that
             audit (2026-11-14), so this was its first marker: one tool in
             place of three, with `part` so "how's the CPU?" never waits on the
             internet probe, and a description in §3's grammar. TOOL_META says
             machine / live (through Aether) / changes nothing. Its own slugs
             are the shared unknown_action and unavailable (T9); its parts keep
             theirs.

V0.03:       Walked through 2026-11-21 (the V0.03 walkthrough, CONVENTIONS
             §3/§12), after its three parts. Checked: their keys still don't
             overlap; every part now fails with the shared `unavailable`
             (Argus's network_unavailable went today); a part outside the enum
             still reaches handle() and gets unknown_action (the routing
             probe's row 4 asks for part='argus', BACKLOG). Changed: Source
             re-measured; Requires names typing. No code changed.
"""

import logging
import os
import sys
from types import ModuleType
from typing import Any

# Only a bare `python tools/asclepius.py` needs the app root added before the
# tools/ import (the aura placement); everything else already has it.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import aether, argus, hephaestus

log = logging.getLogger("METIS.asclepius")

# ── Parts ─────────────────────────────────────────────────────────────────────
# part → the module whose handle() answers it, in the order `all` merges them.
# Modules, not their functions, so the call is looked up when it is made.
_PARTS: dict[str, ModuleType] = {
    "vitals":  hephaestus,
    "online":  aether,
    "network": argus,
}
_ALL = "all"

# ── Contract ──────────────────────────────────────────────────────────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "get_machine_status",
        "description": (
            "Returns this computer's status. vitals: CPU, RAM, disks, uptime "
            "and GPU. online: WiFi, whether the internet is reachable (limited "
            "means a captive portal) and whether the local Ollama server that "
            "runs Pythia is up. network: connections, bandwidth and firewall. "
            "Call when the user asks how the computer is doing, whether they "
            "are online, whether the model server is running, or what the "
            "machine is connected to."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "part": {
                    "type": "string",
                    "enum": [*_PARTS, _ALL],
                    "description": (
                        "vitals = hardware load and health; online = WiFi, "
                        "internet and Ollama; network = connections, bandwidth, "
                        "firewall; all = every part (slowest)."
                    ),
                },
            },
            "required": ["part"],
        },
    },
}

# What the code reads about each tool here; the model never sees it
# (CONVENTIONS §8, "TOOL_META"). network: none = local only, cached = a poll
# or TTL cache, live = may reach the network on every call. mutates = it
# changes state outside the turn.
TOOL_META: dict[str, dict[str, Any]] = {
    "get_machine_status": {"domain": "machine", "network": "live", "mutates": False},
}


def _read(part: str) -> dict[str, Any]:
    """One part's own handle(), guarded: those never raise either, but a
    throw here would reach Pythia's dispatcher, so this answers for them."""
    try:
        result = _PARTS[part].handle()
    except Exception as e:  # noqa: BLE001 — never raise into the dispatcher
        log.warning("Asclepius: %s raised %s", part, type(e).__name__)
        return {"error": f"unavailable: {part} raised {type(e).__name__}"}
    if not isinstance(result, dict):
        log.warning("Asclepius: %s returned %s, not a dict", part,
                    type(result).__name__)
        return {"error": f"unavailable: {part} returned no reading"}
    return result


def _read_all() -> dict[str, Any]:
    """Every part, merged flat. A failed part is one key holding its error;
    a part whose keys collide is nested under its name; all failed → error."""
    out: dict[str, Any] = {}
    failures: list[str] = []
    for part in _PARTS:
        result = _read(part)
        if "error" in result:
            out[part] = result["error"]
            failures.append(f"{part}: {result['error']}")
        elif out.keys() & result.keys():
            log.warning("Asclepius: %s keys collide with an earlier part's; "
                        "nesting it", part)
            out[part] = result
        else:
            out.update(result)
    if len(failures) == len(_PARTS):
        return {"error": f"unavailable: {'; '.join(failures)}"}
    return out


def handle(part: str) -> dict[str, Any]:
    """Brain entry point (get_machine_status) — never raises."""
    if part == _ALL:
        return _read_all()
    if part not in _PARTS:
        return {"error": f"unknown_action: {part!r} is not one of "
                         f"{', '.join([*_PARTS, _ALL])}"}
    return _read(part)


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    for name in (*_PARTS, _ALL):
        r = handle(name)
        print(f"[Asclepius] {name}: {sorted(r)}")
