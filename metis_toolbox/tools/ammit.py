"""
ammit.py — Devourer of Time
============================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Manage a single countdown timer.

State:       Persists to timer_state.json at the app root — anchored to
             __file__, never to the launching script (CONVENTIONS §1).
             Survives restarts; the panel and Pythia read the same file.

Contract:    Exposes TOOL_DEFINITION and handle().
             Also exposes start_timer(), stop_timer(), reset_timer(),
             query_all() for direct dashboard use.

Upstream:    pythia.py (registration + dispatch)
Downstream:  panels/horai_panel.py (AmmitWidget — the countdown display)

v0.01:       Countdown only. No alarm. That's next.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("METIS.ammit")

# ── State file ────────────────────────────────────────────────────────────────
# Anchored to this file, not the launching script. ammit.py lives in tools/, so
# climb up one level (.parent.parent) to keep timer_state.json at the package
# root, beside scribe_data.json.
APP_DIR    = Path(__file__).resolve().parent.parent
STATE_FILE = APP_DIR / "timer_state.json"

MAX_SLOTS   = 1
EMPTY_TIMER = {"duration_seconds": 0, "started_at": None, "running": False}


# ── Persistence ───────────────────────────────────────────────────────────────

def _load() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            state: dict[str, Any] = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return state
        except Exception:
            pass
    return {"timers": [dict(EMPTY_TIMER) for _ in range(MAX_SLOTS)]}


def _save(state: dict[str, Any]) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        log.error(f"Ammit: save failed: {e}")


# ── Internals ─────────────────────────────────────────────────────────────────

def _remaining(timer: dict[str, Any]) -> int:
    """Returns remaining seconds. Freezes at 0 when expired."""
    duration: int = timer["duration_seconds"]
    if not timer["running"] or not timer["started_at"]:
        return duration
    elapsed = (
        datetime.now().astimezone()
        - datetime.fromisoformat(timer["started_at"])
    ).total_seconds()
    return max(0, duration - int(elapsed))


def fmt(seconds: int) -> str:
    """HH:MM:SS string. Public so the dashboard can call it directly."""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


# ── Public API ────────────────────────────────────────────────────────────────

def start_timer(slot: int, duration_seconds: int) -> None:
    """slot is 0-indexed internally."""
    state = _load()
    t = state["timers"][slot]
    t["duration_seconds"] = duration_seconds
    t["started_at"]       = datetime.now().astimezone().isoformat()
    t["running"]          = True
    _save(state)


def stop_timer(slot: int) -> None:
    """Pause: freeze remaining time so it can resume later."""
    state = _load()
    t = state["timers"][slot]
    t["duration_seconds"] = _remaining(t)
    t["started_at"]       = None
    t["running"]          = False
    _save(state)


def reset_timer(slot: int) -> None:
    state = _load()
    state["timers"][slot] = dict(EMPTY_TIMER)
    _save(state)


def query_all() -> list[dict[str, Any]]:
    """Returns a list of dicts, one per slot, 1-indexed for readability."""
    state = _load()
    out = []
    for i, t in enumerate(state["timers"]):
        rem = _remaining(t)
        out.append({
            "slot":             i + 1,
            "running":          t["running"],
            "remaining_seconds": rem,
            "display":          fmt(rem),
            "expired":          t["running"] and rem == 0,
        })
    return out


# ── Metis contract ────────────────────────────────────────────────────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "manage_timer",
        "description": (
            "Manage a single countdown timer. "
            "Actions: start (requires duration_minutes), "
            "stop (pause), reset (clear), query (read state)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start", "stop", "reset", "query"],
                    "description": "Operation to perform.",
                },
                "slot": {
                    "type": "integer",
                    "description": "Timer slot (always 1).",
                    "minimum": 1,
                    "maximum": 1,
                },
                "duration_minutes": {
                    "type": "number",
                    "description": "Duration in minutes. Required for start action.",
                },
            },
            "required": ["action"],
        },
    },
}


def handle(action: str = "query", slot: int = 1, duration_minutes: float = 0) -> dict[str, Any]:
    """
    Called by Pythia's dispatcher when the model invokes manage_timer.
    slot is 1-indexed in the contract; converted to 0-indexed internally.
    """
    idx = max(0, min(MAX_SLOTS - 1, slot - 1))

    if action == "start":
        start_timer(idx, int(duration_minutes * 60))
        return {"ok": True, "slot": slot, "duration_minutes": duration_minutes}
    elif action == "stop":
        stop_timer(idx)
        return {"ok": True, "slot": slot}
    elif action == "reset":
        reset_timer(idx)
        return {"ok": True, "slot": slot}
    else:  # query
        return {"timers": query_all()}


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    timers = query_all()
    for t in timers:
        status = "▶" if t["running"] else "■"
        print(f"[Ammit] Slot {t['slot']} {status}  {t['display']}")
