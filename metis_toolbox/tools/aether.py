"""
aether.py — Connectivity Status
================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Report whether WiFi and the Anthropic API are reachable.

Checks WiFi connection state (Windows-only via netsh) and Anthropic API
status (via the public Statuspage JSON endpoint).

Contract:    Exposes TOOL_DEFINITION, handle(), and fetch().
             Both checks handle their own failures and return safe defaults,
             so neither handle() nor fetch() ever raises.

Requires:    requests (already in Felhaven stack)
"""

import logging
import subprocess
import requests
from typing import Any

log = logging.getLogger("METIS.aether")

# ── Config ────────────────────────────────────────────────────────────────────

_STATUS_URL    = "https://status.anthropic.com/api/v2/status.json"
_USER_AGENT    = "Felhaven/1.0 (Aether connectivity)"
_HTTP_TIMEOUT  = 5
_NETSH_TIMEOUT = 2

# Hide the console window the netsh shell-out would otherwise flash (Felhaven
# runs under pythonw). getattr so importing on a non-Windows box can't
# AttributeError. Mirrors tools/argus.py.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Statuspage indicator → our four-state label
_STATUS_MAP = {
    "none":        "operational",
    "minor":       "degraded",
    "maintenance": "degraded",
    "major":       "down",
    "critical":    "down",
}


# ── Internals ─────────────────────────────────────────────────────────────────

def _check_wifi() -> str:
    """Returns 'connected', 'disconnected', or 'unknown'.

    Tri-state on purpose: 'unknown' means we couldn't determine the state — the
    netsh probe errored, timed out, or reported no wireless interface at all (an
    Ethernet-only box, or WiFi hardware disabled). That is NOT the same as a
    known 'disconnected', so callers can keep the alarm color for genuine loss
    of connectivity instead of lighting up red on a wired machine.
    """
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, text=True, timeout=_NETSH_TIMEOUT,
            creationflags=_NO_WINDOW,
        )
        for line in result.stdout.splitlines():
            parts = line.split(":", 1)
            if len(parts) == 2 and parts[0].strip().lower() == "state":
                return "connected" if parts[1].strip().lower() == "connected" else "disconnected"
        # netsh ran but printed no "State" line — e.g. "There is no wireless
        # interface on the system." No adapter to speak of → unknown, not down.
        return "unknown"
    except Exception as e:
        log.warning(f"Aether: netsh check failed: {e}")
        return "unknown"


def _check_api() -> str:
    """Returns one of: operational / degraded / down / unknown."""
    try:
        resp = requests.get(
            _STATUS_URL,
            headers={"User-Agent": _USER_AGENT},
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        indicator = resp.json()["status"]["indicator"]
        return _STATUS_MAP.get(indicator, "unknown")
    except Exception as e:
        log.warning(f"Aether: status check failed: {e}")
        return "unknown"


# ── Contract ──────────────────────────────────────────────────────────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "get_connectivity",
        "description": (
            "Returns current network connectivity status: the machine's WiFi "
            "state (connected / disconnected / unknown) and whether the "
            "Anthropic API is operational. Call this when the user asks about "
            "internet, network, WiFi, or whether Claude is online."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


def handle() -> dict[str, Any]:
    """Brain entry point — never raises."""
    return {
        "wifi":       _check_wifi(),
        "api_status": _check_api(),
    }


fetch = handle  # Kairos entry point — both checks fail-safe internally


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    r = handle()
    print(f"[Aether] WiFi: {r['wifi']} | API: {r['api_status']}")
