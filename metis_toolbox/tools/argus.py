"""
argus.py — Network Awareness
=============================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Report what this computer is communicating with on the network.
             Visibility, not enforcement. Veins and synapses, not threats.

Contract:    Two surfaces over one snapshot. fetch() serves the panel the full
             picture (every connection + the whole timeline). handle() /
             TOOL_DEFINITION expose get_network_summary — a slimmed, glanceable
             summary for the LLM (counts, throughput, firewall, top connections).
             Read-only to the network: it queries the connection table, the
             firewall state, the DNS cache, and the NIC counters, and reports
             them. It never blocks, kills, captures packets, or elevates.

             fetch() degrades PER FIELD (the Pheme precedent): a single
             unresolvable PID, an empty DNS cache, or a failed netsh query
             falls back inside the returned dict. fetch() only raises if
             psutil.net_connections() itself throws wholesale — at which point
             Kairos delivers None and the panel holds its last good state, stale.

Sources:     psutil.net_connections   — active connections + listeners
             psutil.net_io_counters   — per-NIC byte counters (rate via delta)
             netsh advfirewall        — firewall on/off per profile (Aether's
                                        shell-out precedent; no elevation needed)
             ipconfig /displaydns     — resolver CACHE snapshot (not a history)

Honest limits (surfaced in the UI, never hidden):
   - No per-process bandwidth — psutil exposes none on Windows.
   - DNS is the resolver cache: point-in-time, TTL-bounded, not an append log.
   - Polling (5 s) misses any connection that opens and closes between ticks.

Upstream:    kairos.py (calls fetch)
Downstream:  panels/argus_panel.py (display surface)

Requires:    psutil (already in the stack); os, json, time, socket, subprocess,
             ctypes, collections (stdlib).
"""

import ctypes
import json
import logging
import os
import socket
import subprocess
import time
from collections import deque
from typing import Any

import psutil

log = logging.getLogger("METIS.argus")

# ── Config ──────────────────────────────────────────────────────────────────

# App root = one dir up from tools/ (next to felhaven.py), the anchor Midas uses
# for its watchlist. The rolling timeline persists here. Anchored to __file__ so
# the current working directory never matters.
_APP_ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TIMELINE_PATH = os.path.join(_APP_ROOT, "argus_timeline.json")

# The psutil reads are cheap and run every tick. The subprocess polls each spawn
# a process (tens of ms), so they run far less often and serve a cached block in
# between — the Midas cache + stale-fallback pattern. The dict always carries a
# value, never a hole.
_DNS_TTL      = 30   # seconds between ipconfig /displaydns reads
_FW_TTL       = 60   # seconds between netsh firewall reads
_PROC_TIMEOUT = 4    # seconds before we give up on a shell-out

# Hide the console window each shell-out would otherwise flash (Felhaven runs
# under pythonw). getattr so importing on a non-Windows box can't AttributeError.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Bound the persisted timeline and the DNS list so neither grows without limit.
_TIMELINE_MAX = 300
_DNS_MAX      = 60

# PID 0 on Windows is the "System Idle Process". Windows parks ORPHANED sockets
# there — connections whose owning process already closed but whose socket lingers
# in TIME_WAIT. Reporting "System Idle Process → some host" misleads (the idle
# process isn't talking to anything), so we relabel PID 0 to a token that tells
# the truth: a connection in teardown with no live owner. NOTE: PID 4 ("System",
# the real kernel process that legitimately listens on 445 etc.) is left alone —
# only PID 0 is the misleading one. This is the one deviation from specs/argus.md.
_PID0_LABEL = "[system]"


# ── Module state (ephemeral rate/throttle caches; all reset on restart) ──────

_proc_cache: dict[int, str] = {}     # pid -> name memo (pruned to live PIDs each tick)
_prev_io: dict[str, tuple[int, int]] | None = None         # {nic: (bytes_sent, bytes_recv)} from last tick
_prev_io_ts: float = 0.0

_dns_block: dict[str, Any] | None = None       # last DNS result, served while throttled
_dns_ts: float = 0.0
_fw_block: dict[str, Any] | None = None        # last firewall result, served while throttled
_fw_ts: float = 0.0

# Timeline diff state. _known is the IN-MEMORY baseline (conn-key -> detail); it
# is SEPARATE from the persisted history (_timeline). _seeded guards the silent
# baseline: the first tick establishes _known without emitting an "open" for
# every already-established connection — they did not open *now*.
_known: dict[str, dict[str, Any]] = {}
_seeded = False
_timeline: deque[dict[str, Any]] = deque(maxlen=_TIMELINE_MAX)   # loaded from disk on import


# ── Privilege (annotation only — Argus needs no elevation, ever) ─────────────

def _privilege() -> str:
    """"user" / "admin". This only annotates the honest gap in the dict; Argus
    queries nothing that requires elevation."""
    try:
        return "admin" if ctypes.windll.shell32.IsUserAnAdmin() else "user"
    except Exception:
        return "user"


# ── PID resolution — the #1 risk, handled per §4 of the spec ─────────────────

def _resolve(pid: int | None) -> str:
    """pid -> process name, memoized. Returns "—" for a None pid, an AccessDenied
    process (which happens on Windows even when elevated — a normal fallback, not
    an error), or a PID that died between the snapshot and the lookup. Misses are
    NOT cached, so they retry on the next tick."""
    if pid is None:
        return "—"
    if pid == 0:
        return _PID0_LABEL            # orphaned/closing sockets, not a real owner
    cached = _proc_cache.get(pid)
    if cached is not None:
        return cached
    try:
        name = psutil.Process(pid).as_dict(attrs=["name"], ad_value="—")["name"] or "—"
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        name = "—"
    if name != "—":
        _proc_cache[pid] = name   # cache hits only; let misses retry next tick
    return name


def _addr(a: Any) -> str:
    """A psutil addr tuple -> "ip:port", or "" when empty (listeners / UDP)."""
    return f"{a.ip}:{a.port}" if a else ""


# ── Connections + listening + summary (one pass, shared PID resolution) ──────

def _collect(conns: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Walk the socket table once, producing the connections list, the listening
    list, and the summary counts."""
    connections, listening = [], []
    established = other = unresolved = 0

    for c in conns:
        proc   = _resolve(c.pid)
        fam    = "IPv6" if c.family == socket.AF_INET6 else "IPv4"
        proto  = "UDP" if c.type == socket.SOCK_DGRAM else "TCP"
        raddr  = _addr(c.raddr)
        laddr  = _addr(c.laddr)
        status = c.status            # ESTABLISHED / LISTEN / NONE / TIME_WAIT / ...

        if proc == "—":
            unresolved += 1

        # Listeners (TCP LISTEN) and bound UDP sockets (status NONE, no remote)
        # are the "listening" surface. UDP has no LISTEN state — say "bound", do
        # not hide it.
        if status == "LISTEN" or (proto == "UDP" and not raddr):
            listening.append({
                "pid":   c.pid, "proc": proc, "proto": proto,
                "laddr": laddr or "—",
                "state": "listening" if status == "LISTEN" else "bound",
            })
            continue

        # Anything with a remote end is something we're communicating with.
        if raddr:
            connections.append({
                "pid":  c.pid, "proc": proc, "family": fam,
                "laddr": laddr, "raddr": raddr, "status": status,
            })
            if status == "ESTABLISHED":
                established += 1
            else:
                other += 1

    # Steady-state hygiene: drop memo entries for PIDs that are gone, so the
    # cache can't grow without bound and a reused PID can't show a stale name.
    live = {c.pid for c in conns if c.pid is not None}
    for dead in [p for p in _proc_cache if p not in live]:
        del _proc_cache[dead]

    summary = {
        "established":     established,
        "listening":       len(listening),
        "other":           other,
        "unresolved_pids": unresolved,
    }
    return connections, listening, summary


# ── Outbound traffic — interface-level rate from cumulative counters ─────────

def _traffic() -> dict[str, Any]:
    """Per-NIC up/down rate from the delta of cumulative byte counters (the same
    derive-from-counters move Aura uses). The first call has no prior, so rates
    read 0 until the next tick. Interface-level only — psutil exposes no
    per-process network I/O on Windows. Never raises."""
    global _prev_io, _prev_io_ts
    now = time.monotonic()
    try:
        counters = psutil.net_io_counters(pernic=True)
    except Exception as e:
        log.warning("Argus: net_io_counters failed: %s", e)
        return {"window_s": 0, "up_bps": 0, "down_bps": 0, "per_nic": {}}

    cur = {nic: (c.bytes_sent, c.bytes_recv) for nic, c in counters.items()}
    per_nic, up_total, down_total = {}, 0.0, 0.0
    dt = now - _prev_io_ts if _prev_io else 0.0

    if _prev_io and dt > 0:
        for nic, (sent, recv) in cur.items():
            psent, precv = _prev_io.get(nic, (sent, recv))
            # A NIC reset (down/up) yields a negative delta — clamp it to 0.
            up   = max(0, sent - psent) / dt
            down = max(0, recv - precv) / dt
            if up or down:
                per_nic[nic] = {"up_bps": int(up), "down_bps": int(down)}
            up_total   += up
            down_total += down

    _prev_io, _prev_io_ts = cur, now
    return {
        "window_s": round(dt, 1),
        "up_bps":   int(up_total),
        "down_bps": int(down_total),
        "per_nic":  per_nic,
    }


# ── Firewall — throttled netsh read (Aether's shell-out, no elevation) ───────

def _parse_firewall(text: str) -> dict[str, Any]:
    """Parse `netsh advfirewall show allprofiles state` into on/off per profile.
    English-locale parse — the same assumption Aether's netsh reader makes."""
    profiles = {"domain": "—", "private": "—", "public": "—"}
    current = None
    for line in text.splitlines():
        s = line.strip().lower()
        if s.startswith("domain profile"):
            current = "domain"
        elif s.startswith("private profile"):
            current = "private"
        elif s.startswith("public profile"):
            current = "public"
        elif s.startswith("state") and current:
            parts = s.split(None, 1)
            val = parts[1].strip() if len(parts) > 1 else ""
            profiles[current] = "on" if val.startswith("on") else "off"
            current = None
    return profiles


def _firewall() -> dict[str, Any]:
    """Throttled firewall state (Midas cache pattern). Reading state needs no
    elevation — only *modifying* rules does, and Argus never modifies."""
    global _fw_block, _fw_ts
    now = time.monotonic()
    if _fw_block is not None and (now - _fw_ts) < _FW_TTL:
        return _fw_block
    block = {"as_of": time.time(), "state": "unavailable",
             "domain": "—", "private": "—", "public": "—"}
    try:
        r = subprocess.run(
            ["netsh", "advfirewall", "show", "allprofiles", "state"],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=_PROC_TIMEOUT, creationflags=_NO_WINDOW,
        )
        if r.returncode == 0:
            profiles = _parse_firewall(r.stdout)
            if any(v in ("on", "off") for v in profiles.values()):
                block.update(state="ok", **profiles)
    except Exception as e:
        log.warning("Argus: firewall query failed: %s", e)
    _fw_block, _fw_ts = block, now
    return block


# ── DNS — throttled resolver-cache snapshot (NOT a history) ──────────────────

def _parse_dns(text: str) -> list[dict[str, Any]]:
    """Parse `ipconfig /displaydns` into [{name, records:[...]}]. The output is
    dotted-leader "Key . . . : Value" lines grouped per record. We keep the
    record name and any A / AAAA / CNAME answer. English-locale field names; key
    normalization strips the dotted leaders and parentheses to a plain phrase."""
    entries: list[dict[str, Any]] = []
    name: str | None = None
    records: list[str] = []

    def flush() -> None:
        if name and records:
            # dict.fromkeys de-dupes while preserving order.
            entries.append({"name": name, "records": list(dict.fromkeys(records))})

    for line in text.splitlines():
        if ":" not in line:
            continue
        raw_key, _, value = line.partition(":")
        # "Record Name . . . . ." / "A (Host) Record . . ." -> "record name" / "a host record"
        key = "".join(ch if (ch.isalpha() or ch == " ") else " " for ch in raw_key.lower())
        key = " ".join(key.split())
        value = value.strip()
        if not value:
            continue
        if key == "record name":
            flush()
            name, records = value, []
        elif key in ("a host record", "aaaa host record", "cname record"):
            records.append(value)
    flush()
    return entries[:_DNS_MAX]


def _dns() -> dict[str, Any]:
    """Throttled DNS resolver-cache snapshot (Midas cache pattern). No elevation
    needed. "empty" = service running but nothing cached; "unavailable" = the
    command failed (e.g. the DNS Client service is stopped). Both non-fatal."""
    global _dns_block, _dns_ts
    now = time.monotonic()
    if _dns_block is not None and (now - _dns_ts) < _DNS_TTL:
        return _dns_block
    block = {"as_of": time.time(), "state": "unavailable", "entries": []}
    try:
        r = subprocess.run(
            ["ipconfig", "/displaydns"],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=_PROC_TIMEOUT, creationflags=_NO_WINDOW,
        )
        if r.returncode == 0:
            entries = _parse_dns(r.stdout)
            block.update(state="ok" if entries else "empty", entries=entries)
    except Exception as e:
        log.warning("Argus: DNS cache query failed: %s", e)
    _dns_block, _dns_ts = block, now
    return block


# ── Timeline — bounded rolling diff, persisted, silent launch baseline ───────

def _load_timeline() -> None:
    """Load the persisted timeline on import. Missing/corrupt file -> empty."""
    try:
        with open(_TIMELINE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        for ev in data[-_TIMELINE_MAX:]:
            _timeline.append(ev)
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("Argus: could not load timeline: %s", e)


def _save_timeline() -> None:
    """Write-on-change only (the Plutus/Scribe rewrite-on-mutation pattern, not a
    per-tick flush). Temp-then-replace so a crash mid-write can't truncate it."""
    try:
        tmp = _TIMELINE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(list(_timeline), f)
        os.replace(tmp, _TIMELINE_PATH)
    except Exception as e:
        log.warning("Argus: could not save timeline: %s", e)


def _update_timeline(connections: list[dict[str, Any]]) -> None:
    """Diff this snapshot of remote connections against the prior known-set, emit
    open/close events, and persist ONLY when something actually changed.

    The first tick seeds the baseline SILENTLY — connections already established
    at launch did not open *now*, so emitting "open" for all of them would be a
    false flood. The loaded file is history; this live baseline is separate."""
    global _known, _seeded
    cur = {
        f"{c['pid']}|{c['raddr']}|{c['laddr']}|{c['status']}": c
        for c in connections
    }
    if not _seeded:
        _known = cur
        _seeded = True
        return                       # silent baseline; emit from the 2nd tick on

    now = time.time()
    changed = False
    for key, c in cur.items():
        if key not in _known:
            _timeline.append({"t": now, "event": "open",
                              "proc": c["proc"], "raddr": c["raddr"]})
            changed = True
    for key, c in _known.items():
        if key not in cur:
            _timeline.append({"t": now, "event": "close",
                              "proc": c["proc"], "raddr": c["raddr"]})
            changed = True

    _known = cur
    if changed:
        _save_timeline()


_load_timeline()   # history on import; the live diff baseline (_known) is separate


# ── Public API ──────────────────────────────────────────────────────────────

def fetch() -> dict[str, Any]:
    """Kairos entry point (5 s). Assemble the network-awareness snapshot.

    Raises ONLY if net_connections() itself throws wholesale — Kairos then
    delivers None and the panel holds its last good state. Every other source
    degrades inside the returned dict (the Pheme precedent)."""
    conns = psutil.net_connections(kind="inet")    # the one call allowed to raise

    connections, listening, summary = _collect(conns)
    _update_timeline(connections)

    return {
        "as_of":       time.time(),
        "privilege":   _privilege(),
        "summary":     summary,
        "connections": connections,
        "listening":   listening,
        "traffic":     _traffic(),
        "dns":         _dns(),
        "firewall":    _firewall(),
        "timeline":    list(_timeline),
    }


# ── LLM tool contract ────────────────────────────────────────────────────────
# handle() slims fetch()'s full snapshot (which carries the whole connection
# table and timeline) down to a glanceable summary — the model doesn't need
# every socket, just the shape of what's happening.

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "get_network_summary",
        "description": (
            "Returns a read-only summary of this computer's current network "
            "activity: counts of established and listening connections, upload "
            "and download throughput, firewall state per profile, DNS cache "
            "size, and the top active connections. Call when the user asks what "
            "the machine is connected to, network or firewall status, or how "
            "much bandwidth is in use right now."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def handle() -> dict[str, Any]:
    """Toolbox entry: a glanceable network-awareness summary for the LLM.
    Degrades to an error dict if the connection table itself can't be read —
    never raises."""
    try:
        snap = fetch()
    except Exception as e:
        log.warning(f"Argus: network snapshot failed: {e}")
        return {"error": "network_unavailable"}

    s, t, fw, dns = snap["summary"], snap["traffic"], snap["firewall"], snap["dns"]
    top = [
        {"proc": c["proc"], "remote": c["raddr"], "status": c["status"]}
        for c in snap["connections"][:8]
    ]
    return {
        "privilege":         snap["privilege"],
        "established":       s["established"],
        "listening":         s["listening"],
        "other":             s["other"],
        "unattributed_pids": s["unresolved_pids"],
        "up_bps":            t["up_bps"],
        "down_bps":          t["down_bps"],
        "firewall":          {"state": fw["state"], "domain": fw["domain"],
                              "private": fw["private"], "public": fw["public"]},
        "dns_cached_names":  len(dns["entries"]),
        "top_connections":   top,
    }


# ── Standalone test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Two ticks ~1 s apart so traffic rates and the timeline diff have a prior to
    # work from (a single shot would show 0 B/s and a freshly seeded baseline).
    fetch()
    time.sleep(1.2)
    snap = fetch()

    s = snap["summary"]
    print(f"[Argus] privilege={snap['privilege']}  "
          f"{s['established']} established · {s['listening']} listening · "
          f"{s['other']} other · {s['unresolved_pids']} unattributed")
    t = snap["traffic"]
    print(f"  traffic: up {t['up_bps']} B/s  down {t['down_bps']} B/s  "
          f"({t['window_s']}s window, {len(t['per_nic'])} active NICs)")
    fw = snap["firewall"]
    print(f"  firewall[{fw['state']}]: domain={fw['domain']} "
          f"private={fw['private']} public={fw['public']}")
    dns = snap["dns"]
    print(f"  dns[{dns['state']}]: {len(dns['entries'])} cached names")
    print(f"  timeline: {len(snap['timeline'])} events")
    print("  — top connections —")
    for c in snap["connections"][:12]:
        print(f"    {c['proc']:<22} {c['raddr']:<26} {c['status']}")
