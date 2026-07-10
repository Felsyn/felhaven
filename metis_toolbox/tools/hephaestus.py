"""
hephaestus.py — God of the Forge / System Monitor
==============================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Report CPU, RAM, and disk health.

Usage:       Monitoring CPU, Memory, and Disk health.
Requires:    pip install psutil
"""

import psutil
import platform
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

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
    # Use the drive root of wherever this script lives — correct on any
    # drive letter (C:\, D:\, E:\) and works on Linux/macOS too.
    root = Path(sys.argv[0]).resolve().anchor or "/"
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

def handle() -> dict[str, Any]:
    """
    Called by the toolbox dispatcher when the AI invokes get_system_vitals.
    """
    return {
        "node": platform.node(),
        "os": platform.system(),
        "cpu": _get_cpu_status(),
        "memory": _get_memory_status(),
        "storage": _get_storage_status(),
        "timestamp": datetime.now().isoformat()
    }

fetch = handle  # Kairos entry point — same data, no I/O


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    v = handle()
    print(f"[Vitals] OS: {v['os']} | CPU: {v['cpu']['usage_percent']}% | RAM: {v['memory']['percent_used']}%")
