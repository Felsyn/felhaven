"""
hephaestus.py — God of the Forge / System Monitor
==============================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Report CPU, RAM, and disk health.

Contract:    Polled + brain tool. Both entry points read the same vitals and do
             no I/O beyond psutil's own reads; both take no arguments and return
             a dict. They differ only in how they fail, per §2: fetch() (Kairos)
             raises, so Kairos logs the cause and hands the panel None to hold
             its last state; handle() (Pythia) guards that call and degrades to
             {"error": "vitals_unavailable"}, because a brain tool that throws
             crashes the dispatcher. They were once the same object — a psutil
             failure could only propagate or only degrade, never both.

Source:      psutil (CPU / memory / disk) + platform (node, OS name). The disk
             read anchors to __file__, never sys.argv[0] (§1) — argv[0] is the
             launcher, so it reports whichever drive Felhaven was started from.

Upstream:    kairos.py (calls fetch), pythia.py (registration + dispatch)
Downstream:  panels/hephaestus_panel.py (VitalsPanel — display surface)

Requires:    psutil (already in the Felhaven stack)
"""

import logging
import psutil
import platform
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("METIS.hephaestus")

# ── Internals ─────────────────────────────────────────────────────────────────

def _get_cpu_status() -> dict[str, Any]:
    return {
        "usage_percent": psutil.cpu_percent(interval=1),
        "cores": psutil.cpu_count(),
        "load_avg": [round(x, 2) for x in psutil.getloadavg()] if hasattr(psutil, "getloadavg") else "N/A"
    }

def _get_memory_status() -> dict[str, Any]:
    mem = psutil.virtual_memory()
    return {
        "total_gb": round(mem.total / (1024**3), 2),
        "available_gb": round(mem.available / (1024**3), 2),
        "percent_used": mem.percent
    }

def _get_storage_status() -> dict[str, Any]:
    # The drive root of wherever this module lives — correct on any drive
    # letter (C:\, D:\, E:\) and works on Linux/macOS too. Anchored to
    # __file__, never sys.argv[0] (§1): argv[0] is the launcher, so a Felhaven
    # started from another drive would report that drive's free space instead.
    root = Path(__file__).resolve().anchor or "/"
    disk = psutil.disk_usage(root)
    return {
        "total_gb": round(disk.total / (1024**3), 2),
        "free_gb": round(disk.free / (1024**3), 2),
        "percent_used": disk.percent
    }

# ── Contract ──────────────────────────────────────────────────────────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "get_system_vitals",
        "description": (
            "Returns real-time hardware metrics including CPU load, RAM availability, and disk space. "
            "Use this when the user asks about performance, system health, or if the 'machine' is tired."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

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
        "timestamp": datetime.now().isoformat()
    }

def handle() -> dict[str, Any]:
    """
    Called by the toolbox dispatcher when the AI invokes get_system_vitals.
    Same reading as fetch(), but degrades to an error dict if psutil can't be
    read — never raises, because a throw here crashes Pythia's dispatcher.
    """
    try:
        return fetch()
    except Exception as e:
        log.warning(f"Hephaestus: vitals read failed: {e}")
        return {"error": "vitals_unavailable"}


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    v = handle()
    if "error" in v:
        print(f"[Vitals] {v['error']}")
    else:
        print(f"[Vitals] OS: {v['os']} | CPU: {v['cpu']['usage_percent']}% | RAM: {v['memory']['percent_used']}%")
