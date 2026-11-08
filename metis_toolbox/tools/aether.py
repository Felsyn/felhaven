"""
aether.py — Connectivity Status
================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Report whether WiFi, the internet and Ollama are reachable.

Three probes, three answers: the WiFi adapter's state (Windows-only via
netsh), whether the internet actually answers (Windows' own connectivity
probe), and whether the local Ollama server that runs Pythia is up.

Contract:    Exposes TOOL_DEFINITION, handle(), and fetch().
             Every check handles its own failures and returns a safe label,
             so neither handle() nor fetch() ever raises. fetch() is aliased to
             handle() — the poll path is the read path — so its never-raise is
             §2's brain-tool rule, not an emanon-style watcher exception.

Source:      netsh wlan show interfaces (2 s timeout);
             http://www.msftconnecttest.com/connecttest.txt — the page Windows'
             Network Connectivity Status Indicator reads, whose whole body is
             "Microsoft Connect Test". It is plain HTTP ON PURPOSE: a captive
             portal (hotel, campground) can only intercept what it can read, so
             a redirect or a different body means "limited". Redirects are not
             followed, or the portal's own login page would read as online.
             5 s timeout;
             <OLLAMA_HOST>/api/version (2 s timeout) — the same address
             pythia.py talks to, derived the same way; tests hold the two in
             lockstep, since tools/ must not import the brain.

Upstream:    kairos.py — the "aether" worker polls fetch() every 300 s.
             pythia.py — exposes handle() to the LLM as get_connectivity.

Downstream:  panels/hephaestus_panel.py → AetherWidget (embedded in VitalsPanel,
             the HEPHAESTUS tab; registered with Kairos as its own "aether" panel,
             fed directly rather than relayed through VitalsPanel).

Requires:    tools/hermes.py (the bounded network read; both probes are plain
             http, so they pass allow_http=True)
             os, subprocess, sys, logging (stdlib); netsh is Windows-only and
             degrades to "unknown" off-Windows.

V0.02:       Audited 2026-07-24 — docstring gained Source/Upstream/Downstream;
             first test coverage (tests/test_aether.py,
             tests/test_aether_widget_smoke.py); contract verified — handle()/
             fetch() never raise and emit no error dict, so no §2 slug work.

2026-09-27:  Repointed (Pythia tools audit, M4). The second probe used to be
             Anthropic's Statuspage — a leftover of the retired Claude-API
             brain, telling the model "whether Claude is online" when Pythia is
             gemma4 on a local Ollama. It now answers the questions Felhaven
             actually asks: is the internet up, and is Ollama up. The poll went
             from hourly to 300 s — an hour-old "the internet is up" misleads,
             and both new probes are cheap. See CONVENTIONS §12.
"""

import logging
import os
import subprocess
import sys
from typing import Any

# Only a bare `python tools/aether.py` needs the app root added before the
# tools/ import (the aura placement); everything else already has it.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import hermes

log = logging.getLogger("METIS.aether")

# ── Config ────────────────────────────────────────────────────────────────────

_INTERNET_URL  = "http://www.msftconnecttest.com/connecttest.txt"
_INTERNET_BODY = "Microsoft Connect Test"
_USER_AGENT    = "Felhaven/1.0 (Aether connectivity)"
_HTTP_TIMEOUT  = 5
_OLLAMA_TIMEOUT = 2              # a local server; slower than this is not "up"
# Both answers are tiny: measured 2026-10-01, the probe page is 22 B and
# Ollama's /api/version 20 B. The cap is hermes' 64 KiB floor, and the probe
# page is so small that anything over the cap is certainly not it (limited).
_MAX_BYTES = 64 * 1024
_NETSH_TIMEOUT = 2

# Hide the console window the netsh shell-out would otherwise flash (Felhaven
# runs under pythonw). getattr so importing on a non-Windows box can't
# AttributeError. Mirrors tools/argus.py.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _base_url(host: str) -> str:
    """OLLAMA_HOST is host:port with no scheme; tolerate a stray scheme.
    The same derivation as pythia._BASE_URL — tests/test_aether.py holds the
    two together."""
    return "http://" + host.replace("http://", "").replace("https://", "")


_OLLAMA_VERSION_URL = (
    _base_url(os.environ.get("OLLAMA_HOST", "127.0.0.1:11435")) + "/api/version")


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


def _check_internet() -> str:
    """Returns 'online', 'limited', 'offline', or 'unknown'.

    online  — the probe page came back with its known body.
    limited — something answered, but not with that page: a redirect or a
              login page. A captive portal, or a filtered network.
    offline — nothing answered at all (no route, no DNS, timed out).
    unknown — the probe's own server failed, or something unexpected broke;
              neither says anything about this network.
    """
    try:
        resp = hermes.get(
            _INTERNET_URL,
            headers={"User-Agent": _USER_AGENT},
            cap=_MAX_BYTES,
            timeout=_HTTP_TIMEOUT,
            follow_redirects=False,
            allow_http=True,                 # the probe is plain http by design
        )
    except (hermes.Offline, hermes.Timeout) as e:
        # A normal state at a campsite, not a fault — info, not warning.
        log.info(f"Aether: internet probe got no answer: {type(e).__name__}")
        return "offline"
    except hermes.TooLarge:
        # Something answered with far more than the 22-byte probe page: a
        # login page, not the internet.
        return "limited"
    except Exception as e:
        log.warning(f"Aether: internet probe failed: {e}")
        return "unknown"
    if resp.status_code == 200 and resp.text.strip() == _INTERNET_BODY:
        return "online"
    if resp.status_code == 200 or 300 <= resp.status_code < 400:
        return "limited"
    return "unknown"


def _check_ollama() -> str:
    """Returns 'online', 'offline', or 'unknown'.

    A refused connection to a local port is a definite answer — nothing is
    listening. A slow or odd answer is not, so it is 'unknown'."""
    try:
        resp = hermes.get(_OLLAMA_VERSION_URL, cap=_MAX_BYTES,
                          timeout=_OLLAMA_TIMEOUT, allow_http=True)
    except hermes.Offline:
        return "offline"
    except Exception as e:
        log.warning(f"Aether: Ollama probe failed: {type(e).__name__}")
        return "unknown"
    return "online" if resp.status_code == 200 else "unknown"


# ── Contract ──────────────────────────────────────────────────────────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "get_connectivity",
        "description": (
            "Returns this machine's connectivity: its WiFi state (connected / "
            "disconnected / unknown), whether the internet is reachable (online; "
            "limited, meaning a captive portal or filtered network; offline; or "
            "unknown), and whether the local Ollama server that runs Pythia is "
            "up. Call this when the user asks whether they are online, about "
            "the internet or WiFi, or whether the model server is running. For "
            "bandwidth, open connections or the firewall, use get_network_summary."
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
        "wifi":     _check_wifi(),
        "internet": _check_internet(),
        "ollama":   _check_ollama(),
    }


fetch = handle  # Kairos entry point — every check fail-safes internally


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    r = handle()
    print(f"[Aether] WiFi: {r['wifi']} | Internet: {r['internet']} | "
          f"Ollama: {r['ollama']}")
