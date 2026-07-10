#!/usr/bin/env python3
"""
argus_peek.py - a quick look at what your PC is talking to right now.

Run it from the metis_toolbox venv (psutil is already installed there):

    python argus_peek.py

It lists every active *outbound* connection: which program -> which remote host.
Read-only. It touches nothing, changes nothing. This is the raw seed of tools/argus.py.

Note: this resolves remote IPs to hostnames so the output is actually readable
("github.com", not "140.82.113.25") - that's the whole point of "where is my info
going." The live panel deliberately won't do this (a 5s poll can't block on slow DNS
lookups), but a one-shot script you run by hand can. Flip RESOLVE_NAMES to False for
raw IPs / instant output.
"""

import socket
import psutil
from collections import defaultdict
from typing import Any

RESOLVE_NAMES = True          # reverse-DNS the remote IPs to hostnames
socket.setdefaulttimeout(0.4)  # don't hang forever on a slow reverse lookup


def program_for(pid: int | None, cache: dict[int, str]) -> str:
    """PID -> program name, with a '?' fallback for what Windows won't show us."""
    if pid is None:
        return "?"
    if pid not in cache:
        try:
            cache[pid] = psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            cache[pid] = "?"   # process died, or it's not ours and we're not admin
    return cache[pid]


def host_for(ip: str, cache: dict[str, str]) -> str:
    """Remote IP -> hostname, falling back to the raw IP when DNS can't help."""
    if not RESOLVE_NAMES:
        return ip
    if ip not in cache:
        try:
            cache[ip] = socket.gethostbyaddr(ip)[0]
        except (socket.herror, socket.gaierror, OSError):
            cache[ip] = ip
    return cache[ip]


def main() -> None:
    name_cache: dict[int, str] = {}
    host_cache: dict[str, str] = {}
    # group by (program, remote_ip) -> ports seen + how many connections
    groups: defaultdict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"ports": set(), "count": 0})
    unmatched = 0

    for c in psutil.net_connections(kind="inet"):
        if not c.raddr:            # no remote end = something listening, not sending
            continue
        name = program_for(c.pid, name_cache)
        if name == "?":
            unmatched += 1
        key = (name, c.raddr.ip)
        groups[key]["ports"].add(c.raddr.port)
        groups[key]["count"] += 1

    if not groups:
        print("Nothing outbound right now - no active connections to show.")
        return

    rows = []
    for (name, ip), info in groups.items():
        target = host_for(ip, host_cache)
        label = f"{target} ({ip})" if target != ip else ip
        ports = ",".join(str(p) for p in sorted(info["ports"]))
        rows.append((name, f"{label}:{ports}", info["count"]))

    # group by program, busiest endpoint first within each
    rows.sort(key=lambda r: (r[0].lower(), -r[2]))

    w_prog = max(len("PROGRAM"), max(len(r[0]) for r in rows))
    w_dest = max(len("TALKING TO"), max(len(r[1]) for r in rows))
    print(f"{'PROGRAM':<{w_prog}}  {'TALKING TO':<{w_dest}}  CONNS")
    print(f"{'-' * w_prog}  {'-' * w_dest}  -----")
    for name, dest, n in rows:
        print(f"{name:<{w_prog}}  {dest:<{w_dest}}  {n:>5}")

    footer = f"\n{len(rows)} endpoints"
    if unmatched:
        footer += (f"  -  {unmatched} connection(s) couldn't be matched to a program "
                   f"(run as administrator to see those)")
    print(footer)


if __name__ == "__main__":
    main()
