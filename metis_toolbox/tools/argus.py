"""
argus.py — Network Awareness
=============================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Report what this computer is communicating with on the network.
             Visibility, not enforcement. Veins and synapses, not threats.

Contract:    §2's dashboard-only watcher, degrading per field: fetch() for
             Kairos and no TOOL_DEFINITION (since 2026-11-14). Asclepius hands
             handle() to Pythia as get_machine_status part=network. Two
             surfaces over one snapshot: fetch() serves the panel the full
             picture (every connection + the whole timeline); handle() is a
             slimmed, glanceable summary for the LLM (counts, throughput,
             firewall, how many names the DNS cache holds when Windows lets
             Argus read it, top connections). Read-only to the network: it
             queries the connection table, the firewall state, the DNS cache,
             and the NIC counters, and reports them. It never blocks, kills,
             captures packets, or elevates.

             fetch() degrades PER FIELD (the Pheme precedent): a single
             unresolvable PID, an empty or refused DNS cache, or a failed netsh
             query falls back inside the returned dict. fetch() only raises if
             psutil.net_connections() itself throws wholesale — at which point
             Kairos delivers None and the panel holds its last good state,
             stale. handle() never raises: that same failure comes back as
             `unavailable: network snapshot raised <class>`.

             Kairos's worker and Pythia's both call fetch(), and fetch() keeps
             state between calls (the timeline baseline, the rate counters,
             the throttled blocks), so it runs under one lock and hands out
             copies (§6). The timeline file is read by the first fetch(), not
             on import, so a test can point it elsewhere first.

Sources:     psutil.net_connections   — active connections + listeners
             psutil.net_io_counters   — per-NIC byte counters (rate via delta)
             netsh advfirewall        — firewall on/off per profile (Aether's
                                        shell-out precedent; no elevation needed)
             ipconfig /displaydns     — resolver CACHE snapshot (not a history);
                                        Windows 11 shows it only to an
                                        administrator (see Honest limits)
             Both programs run from System32 by absolute path, never a bare
             name (CONVENTIONS §12, 2026-11-17).

Honest limits (surfaced in the UI, never hidden):
   - No per-process bandwidth — psutil exposes none on Windows.
   - DNS is the resolver cache: point-in-time, TTL-bounded, not an append log.
   - The DNS cache needs administrator rights. Measured 2026-11-21 on Windows
     11 (build 26200) as a normal user: ipconfig refuses ("requires
     elevation"), and the DNS API and PowerShell's Get-DnsClientCache return
     nothing. Argus reports that as needs_admin, not as a failure, and
     handle() leaves out the count rather than say 0 (CONVENTIONS §12).
   - Polling (5 s) misses any connection that opens and closes between ticks.

Upstream:    kairos.py (calls fetch every 5 s), tools/asclepius.py (calls
             handle, as get_machine_status part=network)
Downstream:  panels/argus_panel.py (display surface)

Requires:    psutil (already in the stack); collections, copy, ctypes, json,
             logging, math, os, socket, subprocess, threading, time, typing
             (stdlib).

2026-07-25:  walkthrough audit — every docstring field verified honest: fetch()
             raises ONLY on a wholesale net_connections() failure (every other
             source degrades per-field), handle() never-raises to the
             network_unavailable slug (already §2-conformant). No dead code. The
             hand-run seed argus_peek.py was retired (superseded by this module).

2026-11-14:  TOOL_DEFINITION removed. get_network_summary is retired; Pythia
             reaches handle() through Asclepius's get_machine_status, with
             Hephaestus and Aether (CONVENTIONS §12).

V0.03:       Walked through 2026-11-21 (the V0.03 walkthrough, CONVENTIONS
             §3/§12). In the repo since 2026-06-22. Checked: July's two flags
             were fixed the day they were raised (2026-07-25) — the timeline
             key leaves out TCP status, and "top" connections put live ones
             first (socket order within each group, as handle() says); the
             firewall read still works as a normal user. Changed: a DNS cache
             Windows refuses is needs_admin, and handle() leaves its count out
             (the owner's pick); fetch() runs under a lock and hands out
             copies, because two calls at once raised (7 times in 3,000,
             measured); the timeline loads on the first fetch() and drops
             entries the panel couldn't draw; handle()'s failure says §2's
             shared `unavailable` where it said network_unavailable; the
             Contract names its §2 row; Requires names every import.
"""

import copy
import ctypes
import json
import logging
import math
import os
import socket
import subprocess
import threading
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

# What ipconfig says when Windows won't show a normal user the DNS cache
# (exit code 1, measured 2026-11-21 on Windows 11 build 26200). English-locale,
# like the two parsers below; any other failure stays "unavailable".
_ELEVATION_REFUSAL = "requires elevation"

# Hide the console window each shell-out would otherwise flash (Felhaven runs
# under pythonw). getattr so importing on a non-Windows box can't AttributeError.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Both programs by absolute path. A bare name is looked for in Python's folder
# and the working folder before System32, so a netsh.exe or ipconfig.exe left
# in either would run in its place (measured 2026-11-17; CONVENTIONS §12).
_SYSTEM32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
_NETSH    = os.path.join(_SYSTEM32, "netsh.exe")
_IPCONFIG = os.path.join(_SYSTEM32, "ipconfig.exe")

# Bound the persisted timeline and the DNS list so neither grows without limit.
_TIMELINE_MAX = 300
_DNS_MAX      = 60

# PID 0 on Windows is the "System Idle Process". Windows parks ORPHANED sockets
# there — connections whose owning process already closed but whose socket lingers
# in TIME_WAIT. Reporting "System Idle Process → some host" misleads (the idle
# process isn't talking to anything), so we relabel PID 0 to a token that tells
# the truth: a connection in teardown with no live owner. NOTE: PID 4 ("System",
# the real kernel process that legitimately listens on 445 etc.) is left alone —
# only PID 0 is the misleading one.
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
_timeline: deque[dict[str, Any]] = deque(maxlen=_TIMELINE_MAX)   # loaded by the first fetch()
_timeline_loaded = False

# Kairos's worker and Pythia's (through Asclepius) both call fetch(), and every
# bit of state above changes inside it. One lock, held across the whole call,
# shell-outs included (the Aura shape, §6). Measured 2026-11-21 without it: two
# threads calling fetch() at once raised 7 times in 3,000 calls (a PID deleted
# twice from _proc_cache; a dict changing size mid-loop).
_lock = threading.Lock()


# ── Privilege (annotation only — Argus never asks for elevation) ─────────────

def _privilege() -> str:
    """"user" / "admin". This only annotates the honest gaps in the dict: a
    process Windows won't name, and the DNS cache, which Windows 11 shows only
    to an administrator. Argus never asks for elevation."""
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
            [_NETSH, "advfirewall", "show", "allprofiles", "state"],
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
    """Throttled DNS resolver-cache snapshot (Midas cache pattern). "empty" =
    read, but nothing cached; "needs_admin" = Windows refused a normal user
    (_ELEVATION_REFUSAL); "unavailable" = the command failed some other way
    (e.g. the DNS Client service is stopped). All non-fatal."""
    global _dns_block, _dns_ts
    now = time.monotonic()
    if _dns_block is not None and (now - _dns_ts) < _DNS_TTL:
        return _dns_block
    block = {"as_of": time.time(), "state": "unavailable", "entries": []}
    try:
        r = subprocess.run(
            [_IPCONFIG, "/displaydns"],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=_PROC_TIMEOUT, creationflags=_NO_WINDOW,
        )
        if r.returncode == 0:
            entries = _parse_dns(r.stdout)
            block.update(state="ok" if entries else "empty", entries=entries)
        elif _ELEVATION_REFUSAL in (r.stdout or "").lower():
            block.update(state="needs_admin")
    except Exception as e:
        log.warning("Argus: DNS cache query failed: %s", e)
    _dns_block, _dns_ts = block, now
    return block


# ── Timeline — bounded rolling diff, persisted, silent launch baseline ───────

def _valid_event(ev: Any) -> bool:
    """One persisted event, shaped as _update_timeline writes it. The panel
    draws these on the main thread, so one it can't draw would raise there."""
    if not isinstance(ev, dict) or ev.get("event") not in ("open", "close"):
        return False
    t = ev.get("t")
    return (isinstance(t, (int, float)) and math.isfinite(t)
            and isinstance(ev.get("proc"), str)
            and isinstance(ev.get("raddr"), str))


def _load_timeline() -> None:
    """Load the persisted timeline, once, from the first fetch() (under _lock).
    Missing/corrupt file -> empty; entries _valid_event refuses are dropped."""
    global _timeline_loaded
    _timeline_loaded = True
    try:
        with open(_TIMELINE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return
    except Exception as e:
        log.warning("Argus: could not load timeline: %s", e)
        return
    if not isinstance(data, list):
        log.warning("Argus: timeline file holds no list; starting empty")
        return
    kept = [ev for ev in data if _valid_event(ev)]
    if len(kept) < len(data):
        log.warning("Argus: dropped %d malformed timeline entries",
                    len(data) - len(kept))
    _timeline.extend(kept[-_TIMELINE_MAX:])


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
    # Identity is the socket 4-tuple (pid + remote + local endpoint), NOT its TCP
    # state: one connection moves ESTABLISHED -> TIME_WAIT during teardown, and
    # including `status` here would emit a phantom close+open pair for that single
    # socket. Keep status OUT of the key so the timeline records only genuinely
    # new and genuinely closed connections. (Do NOT re-add status — that churn is
    # exactly the bug this key shape fixes.)
    cur = {
        f"{c['pid']}|{c['raddr']}|{c['laddr']}": c
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


# ── Public API ──────────────────────────────────────────────────────────────

def fetch() -> dict[str, Any]:
    """Kairos entry point (5 s); handle() calls it too. Assemble the
    network-awareness snapshot.

    Raises ONLY if net_connections() itself throws wholesale — Kairos then
    delivers None and the panel holds its last good state. Every other source
    degrades inside the returned dict (the Pheme precedent). Runs under _lock,
    and the throttled blocks and timeline go out as copies, so no caller holds
    an object Argus keeps (§6)."""
    with _lock:
        if not _timeline_loaded:
            _load_timeline()   # history; the live diff baseline (_known) is separate
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
            "dns":         copy.deepcopy(_dns()),
            "firewall":    copy.deepcopy(_firewall()),
            "timeline":    copy.deepcopy(list(_timeline)),
        }


# ── LLM tool contract ────────────────────────────────────────────────────────
# handle() slims fetch()'s full snapshot (which carries the whole connection
# table and timeline) down to a glanceable summary — the model doesn't need
# every socket, just the shape of what's happening.

def handle() -> dict[str, Any]:
    """Toolbox entry: a glanceable network-awareness summary for the LLM.
    Degrades to an error dict if the connection table itself can't be read —
    never raises."""
    try:
        snap = fetch()
    except Exception as e:
        log.warning(f"Argus: network snapshot failed: {e}")
        return {"error": f"unavailable: network snapshot raised {type(e).__name__}"}

    s, t, fw, dns = snap["summary"], snap["traffic"], snap["firewall"], snap["dns"]
    # ESTABLISHED connections are the live ones worth surfacing; teardown and
    # half-open states sink below them (stable sort preserves table order within
    # each group) so the 8-row sample the model sees isn't dominated by TIME_WAIT
    # noise. There's no per-connection metric to rank by (psutil exposes none on
    # Windows), so "top" means "live first", not "busiest".
    ranked = sorted(snap["connections"], key=lambda c: c["status"] != "ESTABLISHED")
    top = [
        {"proc": c["proc"], "remote": c["raddr"], "status": c["status"]}
        for c in ranked[:8]
    ]
    out: dict[str, Any] = {
        "privilege":         snap["privilege"],
        "established":       s["established"],
        "listening":         s["listening"],
        "other":             s["other"],
        "unattributed_pids": s["unresolved_pids"],
        "up_bps":            t["up_bps"],
        "down_bps":          t["down_bps"],
        "firewall":          {"state": fw["state"], "domain": fw["domain"],
                              "private": fw["private"], "public": fw["public"]},
    }
    # Only a cache Argus could read has a count. A refused or failed read
    # would say 0 names, which is false, so the key is left out (§12,
    # 2026-11-21).
    if dns["state"] in ("ok", "empty"):
        out["dns_cached_names"] = len(dns["entries"])
    out["top_connections"] = top
    return out


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
