"""
horai.py — Goddesses of the Hours and Seasons
==============================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Give the current date, time, season, and time-of-day.

Eunomia  →  clock  (exact datetime)
Dike     →  season (Spring / Summer / Autumn / Winter)
Eirene   →  cycle  (Dawn / Morning / Afternoon / Evening / Night)

Contract:    Exposes TOOL_DEFINITION and handle().
             handle() takes no arguments, returns a dict.

Upstream:    metis_toolbox/__init__.py (registration + dispatch)
Downstream:  metis_brain.py (via toolbox, never directly)
"""

import os
import sys
from datetime import datetime, timezone
from typing import Any

# Horai reaches UP to app-root themis.py for the hemisphere (latitude sign) and
# clock format (Settings tab), read per handle() so an edit applies next tick.
# Guarded-path pattern (callimachus.py) for a bare `python tools/horai.py`.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import themis


# ── Internals ─────────────────────────────────────────────────────────────────

_SOUTHERN = {"Spring": "Autumn", "Summer": "Winter",
             "Autumn": "Spring", "Winter": "Summer"}


def _get_season(dt: datetime, southern: bool = False) -> str:
    """Northern-hemisphere month->season, mirrored six months when southern.
    Hemisphere comes from Themis (latitude sign), so no separate setting."""
    m = dt.month
    if m in (3, 4, 5):     north = "Spring"
    elif m in (6, 7, 8):   north = "Summer"
    elif m in (9, 10, 11): north = "Autumn"
    else:                  north = "Winter"
    return _SOUTHERN[north] if southern else north


def _get_cycle(dt: datetime) -> str:
    h = dt.hour
    if 5  <= h < 8:  return "Dawn"
    if 8  <= h < 12: return "Morning"
    if 12 <= h < 17: return "Afternoon"
    if 17 <= h < 21: return "Evening"
    return "Night"


# ── Contract ──────────────────────────────────────────────────────────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "get_time_context",
        "description": (
            "Returns the current temporal context including exact time, season (adjusted for the configured hemisphere), and time-of-day cycle. Use when reasoning about time, schedules, recency, or contextual awareness. "
            "Call this when the user asks about time, schedules, "
            "how long until something, or when temporal context is relevant."
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
    Called by the toolbox dispatcher when the LLM invokes get_time_context.
    Returns ISO timestamp, human-readable clock, season, and cycle. - nothing more
    """
    now = datetime.now().astimezone()
    time_fmt = "%H:%M" if themis.clock_24h() else "%I:%M %p"
    return {
        "iso": now.isoformat(),
        "clock":  now.strftime(f"%A, %B %d %Y, {time_fmt}"),
        "season": _get_season(now, themis.is_southern()),
        "cycle": {
            "label": _get_cycle(now),
            "hour": now.hour
        }
    }


fetch = handle  # Kairos entry point — same data, no I/O


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    result = handle()
    print(f"[Horai] {result['season']} | {result['cycle']['label']} | {result['clock']}")
