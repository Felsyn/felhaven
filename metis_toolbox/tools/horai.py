"""
horai.py — Goddesses of the Hours and Seasons
==============================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Give the current date, time, season, and time-of-day.

Eunomia  →  clock  (exact datetime)
Dike     →  season (Spring / Summer / Autumn / Winter, by whole months:
            March–May is spring in the north; the south's is six months on)
Eirene   →  cycle  (Dawn 05–08, Morning 08–12, Afternoon 12–17, Evening
            17–21, Night; by the clock's hour, not the sun's)

Contract:    Polled + brain tool. Exposes TOOL_DEFINITION, TOOL_META and
             handle(), with fetch = handle as the Kairos entry point (same
             data). Takes no arguments, returns a dict.
             fetch() never raises, deliberately (§2 asks why): it reads only
             the local clock and Themis, whose loaders fall back rather than
             fail, so there is no failure mode left to report.
             No logger, by a written exemption (test_tool_loggers): with no
             failure of its own, it has nothing to log, and Themis logs its
             own file's problems.

Source:      The local system clock (datetime.now) plus themis.py for the
             configured clock format and hemisphere: two reads of Themis's
             settings file a call. No network.
Upstream:    kairos.py (calls fetch every second), pythia.py (registration +
             dispatch)
Downstream:  themis.py (clock format + hemisphere), panels/horai_panel.py
             (display surface)
Requires:    stdlib only (datetime, os, sys, typing); themis.py for settings.

2026-07-24:  walkthrough audit — never-raise verified (Themis falls back to
             defaults), contract fields completed.

2026-11-16:  Pythia tool audit (CONVENTIONS §12). Its description's two
             overlapping when-clauses became one (§3, "Description grammar").
             TOOL_META says time / local / changes nothing. No error slugs to
             share: it never fails. Left open: whether it gets a logger
             (BACKLOG, the audit's L5).

V0.03:       Walked through 2026-11-21 (the V0.03 walkthrough, CONVENTIONS
             §3). The logger question is settled: a written exemption (the
             owner's pick, §12). "No I/O" was false (each call reads Themis's
             file twice) and the season's months and the cycle's hours are
             now said. The unused `timezone` import went; Themis moved to
             Downstream (Horai calls it).
"""

import os
import sys
from datetime import datetime
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
            "Returns the current date and exact time, the season (adjusted for "
            "the configured hemisphere) and the time-of-day cycle. Call when "
            "the user asks about the time, the date, schedules or how long "
            "until something, or when an answer depends on the current time "
            "or recency."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

# What the code reads about each tool here; the model never sees it
# (CONVENTIONS §8, "TOOL_META"). network: none = local only, cached = a poll
# or TTL cache, live = may reach the network on every call. mutates = it
# changes state outside the turn.
TOOL_META: dict[str, dict[str, Any]] = {
    "get_time_context": {"domain": "time", "network": "none", "mutates": False},
}


def handle() -> dict[str, Any]:
    """
    Called by Pythia's dispatcher when the model invokes get_time_context, and
    by Kairos as fetch. Returns the ISO timestamp, a readable clock, the
    season and the cycle, and nothing more.
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


fetch = handle  # Kairos entry point — same data


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    result = handle()
    print(f"[Horai] {result['season']} | {result['cycle']['label']} | {result['clock']}")
