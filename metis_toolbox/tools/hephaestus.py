"""
hephaestus.py — God of the Forge / System Monitor
==============================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Report the machine's vitals: CPU, RAM, disks, uptime and the GPU.

Contract:    Polled + Pythia's vitals reading (through Asclepius since
             2026-11-14; no TOOL_DEFINITION of its own). Both entry points read
             the same vitals and do no I/O beyond psutil's reads and Brontes's
             NVML ones; both take no arguments and return a dict. They differ
             only in how they fail, per §2: fetch() (Kairos) raises, so Kairos
             logs the cause and hands the panel None to hold its last state;
             handle() (Pythia) guards that call and degrades to {"error":
             "unavailable: vitals read raised <class>"}, because a brain tool
             that throws crashes the dispatcher. They were once the same
             object — a psutil failure could only propagate or only degrade,
             never both.

Source:      psutil (CPU / memory / disks / boot time) + platform (node, OS
             name) + tools/brontes.py (the GPU, through NVML). "storage" is the
             drive Felhaven lives on, anchored to __file__, never sys.argv[0]
             (§1) — argv[0] is the launcher, so it would report whichever drive
             Felhaven was started from. "boot_storage" is %SystemDrive%, the
             drive that fills up, and is None when the two are one drive.
             "gpu" is Brontes's reading or its error dict: a machine without an
             NVIDIA card degrades that one field, never the whole read.

Upstream:    kairos.py (calls fetch), tools/asclepius.py (calls handle, as
             get_machine_status part=vitals)
Downstream:  panels/hephaestus_panel.py (VitalsPanel — display surface),
             tools/brontes.py (the GPU).

Requires:    psutil (already in the Felhaven stack); tools/brontes.py; logging,
             os, platform, sys, time, datetime, pathlib (stdlib).

2026-07-25:  walkthrough audit — fetch()/handle() split verified honest: fetch()
             raises for Kairos, handle() degrades to the vitals_unavailable slug
             (already §2-conformant) and never raises. No dead code; the disk read
             is §1-anchored to __file__. Requires line completed; VitalsPanel and
             BarMeter given their first Tk smoke coverage.

2026-11-14:  TOOL_DEFINITION removed. get_system_vitals is retired; Pythia
             reaches handle() through Asclepius's get_machine_status, with
             Aether and Argus (CONVENTIONS §12).

V0.03:       Walked through 2026-11-17 (the V0.03 walkthrough, CONVENTIONS
             §3). handle()'s failure says §2's shared word, unavailable, with
             the exception's class after it, in place of vitals_unavailable
             (Rite's words follow). The Contract counts Brontes's NVML read
             beside psutil's, and load_avg lost a fallback no psutil this lock
             allows could reach.
"""

import logging
import os
import platform
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import brontes

log = logging.getLogger("METIS.hephaestus")

# ── Internals ─────────────────────────────────────────────────────────────────

def _get_cpu_status() -> dict[str, Any]:
    return {
        "usage_percent": psutil.cpu_percent(interval=1),
        "cores": psutil.cpu_count(),
        # psutil has had getloadavg on every platform since 5.6.2 (an emulation
        # on Windows, zeros for its first 5 s); the lock pins 7.x.
        "load_avg": [round(x, 2) for x in psutil.getloadavg()],
    }

def _get_memory_status() -> dict[str, Any]:
    mem = psutil.virtual_memory()
    return {
        "total_gb": round(mem.total / (1024**3), 2),
        "available_gb": round(mem.available / (1024**3), 2),
        "percent_used": mem.percent
    }

def _felhaven_drive() -> str:
    # The drive root of wherever this module lives — correct on any drive
    # letter (C:\, D:\, E:\) and works on Linux/macOS too. Anchored to
    # __file__, never sys.argv[0] (§1): argv[0] is the launcher, so a Felhaven
    # started from another drive would report that drive's free space instead.
    return Path(__file__).resolve().anchor or "/"


def _boot_drive() -> str:
    """The drive Windows boots from (%SystemDrive%), the one that fills up
    with updates and caches; "/" off Windows."""
    drive = os.environ.get("SystemDrive", "")
    return drive.rstrip("\\/") + "\\" if drive else "/"


def _disk(root: str) -> dict[str, Any]:
    disk = psutil.disk_usage(root)
    return {
        "drive": root,
        "total_gb": round(disk.total / (1024**3), 2),
        "free_gb": round(disk.free / (1024**3), 2),
        "percent_used": disk.percent
    }


def _get_storage_status() -> dict[str, Any]:
    return _disk(_felhaven_drive())


def _get_boot_storage_status() -> "dict[str, Any] | None":
    """The boot drive, or None when it IS Felhaven's drive (one row, not two)."""
    boot = _boot_drive()
    if os.path.normcase(boot) == os.path.normcase(_felhaven_drive()):
        return None
    return _disk(boot)


def _uptime_s() -> int:
    return int(time.time() - psutil.boot_time())

# ── Contract ──────────────────────────────────────────────────────────────────

def fetch() -> dict[str, Any]:
    """
    Kairos entry point — the raw vitals read. Raises on a psutil failure, which
    is how Kairos logs the cause and delivers None so VitalsPanel holds its last
    state instead of crashing (§2).
    """
    return {
        "node": platform.node(),
        "os": platform.system(),
        "cpu": _get_cpu_status(),
        "memory": _get_memory_status(),
        "storage": _get_storage_status(),
        "boot_storage": _get_boot_storage_status(),
        "uptime_s": _uptime_s(),
        "gpu": brontes.read(),            # never raises: a missing GPU is a field
        "timestamp": datetime.now().isoformat()
    }

def handle() -> dict[str, Any]:
    """
    Pythia's reading, through Asclepius (get_machine_status, part=vitals).
    Same reading as fetch(), but degrades to an error dict if psutil can't be
    read — never raises, because a throw here crashes Pythia's dispatcher.
    """
    try:
        return fetch()
    except Exception as e:
        log.warning(f"Hephaestus: vitals read failed: {e}")
        return {"error": f"unavailable: vitals read raised {type(e).__name__}"}


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    v = handle()
    if "error" in v:
        print(f"[Vitals] {v['error']}")
    else:
        print(f"[Vitals] OS: {v['os']} | CPU: {v['cpu']['usage_percent']}% | RAM: {v['memory']['percent_used']}%")
        boot = v["boot_storage"]
        print(f"[Vitals] Felhaven's drive {v['storage']['drive']} {v['storage']['percent_used']}%"
              + (f" | boot {boot['drive']} {boot['percent_used']}%" if boot else " (also the boot drive)")
              + f" | up {v['uptime_s'] // 3600} h")
        print(f"[Vitals] GPU: {v['gpu']}")
