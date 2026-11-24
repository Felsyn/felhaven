"""
ammit.py — Devourer of Time
============================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Manage a single countdown timer.

State:       Persists to timer_state.json at the app root — anchored to
             __file__, never to the launching script (CONVENTIONS §1).
             Survives restarts; the panel and Pythia read the same file.
             One lock (_LOCK) covers every read of it and each public call's
             whole read-change-save: Pythia's request thread and the Tk
             thread both use the file, and on Windows a rename fails while
             another thread has the file open, and an open fails while the
             rename runs (§12, 2026-11-21).

Contract:    Request-driven brain tool (§2): TOOL_DEFINITION, TOOL_META and
             handle(action, duration_minutes), which never raises. It returns
             the timer state each action produced, or bad_arguments,
             unknown_action, or write_failed when the file wasn't saved.
             For the dashboard: start_timer(), stop_timer(), reset_timer()
             and acknowledge_timer() (each True once saved), query_all(), and
             minutes_to_seconds(), the one check of a typed duration that
             handle() and the countdown's minutes box share.

Source:      The local clock and timer_state.json. No network.
Upstream:    pythia.py (registration + dispatch)
Downstream:  panels/horai_panel.py (AmmitWidget — the countdown display and
             its controls; AmmitAlarm — popup + spoken announcement on
             expiry; AmmitBadge — a passive status readout on other panels,
             e.g. Home)
Requires:    stdlib only (json, logging, math, os, threading, datetime,
             pathlib, typing).

2026-07-24:  walkthrough audit — countdown display only, no alarm (deliberate);
             contract verified, _fmt privatized, hermetic tests added.

2026-09-09:  the "no alarm" decision above was overridden by request — see
             AmmitAlarm in horai_panel.py. `acknowledged` was added here (not
             there) because it has to survive across panel instances: Home's
             AmmitBadge and Horai's AmmitWidget/AmmitAlarm are different
             objects that only share state through this file. Acknowledging
             does NOT touch `running`/`remaining_seconds` — the countdown
             itself is untouched, only the alarm styling both displays draw
             from `expired` calms down (red -> green) once the alert has been
             seen. Stop/Reset is still the only way to actually clear it.

2026-09-27:  handle() hardened (Pythia tools audit, H1/H2). `start` with no,
             zero, negative or non-numeric duration used to save a running
             0-second countdown — expired on the next read, which is the edge
             AmmitAlarm fires on, so "start a timer" set the alarm off at
             once; "ten" raised. Both are now bad_arguments slugs that save
             nothing. An unknown action is unknown_action instead of a silent
             query, every action returns the timer state it produced (was
             {"ok": True}), and the undeclared `slot` parameter is gone.

2026-10-05:  _save() writes timer_state.json.tmp beside the file and
             os.replace()s it into place (the argus/plutus shape). It used to
             write the file where it stood, so a crash mid-write left a torn
             file, which _load() reads as "no timer" (CONVENTIONS §9).

2026-11-16:  Pythia tool audit (CONVENTIONS §12). Every action returns the
             timer state it produced (T6) and a bad start is bad_arguments
             (the 2026-09-27 note); the description opens with what it does
             (§3). TOOL_META says time / local / changes state. Its slugs are
             already the shared ones (T9).

V0.03:       Walked through 2026-11-21 (the V0.03 walkthrough, CONVENTIONS
             §3). Every read and save is under _LOCK. Measured unlocked: a
             tight reader made 149 of 300 saves fail, and two writers wiped
             the timer 53 times in 300 (none at real rates). A file that
             parses but isn't a timer reads as no timer and logs once, like
             a torn one; it raised. A duration too big for a float is
             bad_arguments; it raised OverflowError. minutes_to_seconds()
             went public so the minutes box shares the check. A failed save
             is write_failed, no longer the old state passed off as the new.
"""

import json
import logging
import math
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("METIS.ammit")

# ── State file ────────────────────────────────────────────────────────────────
# Anchored to this file, not the launching script. ammit.py lives in tools/, so
# climb up one level (.parent.parent) to keep timer_state.json at the package
# root, beside scribe_data.json.
APP_DIR    = Path(__file__).resolve().parent.parent
STATE_FILE = APP_DIR / "timer_state.json"

MAX_SLOTS   = 1
EMPTY_TIMER = {"duration_seconds": 0, "started_at": None, "running": False,
               "acknowledged": False}

# Held across every read of the file and each public call's whole
# read-change-save, so Pythia's thread and the Tk thread never meet inside a
# rename (State, above). A lock rather than a retry: both are in this process.
_LOCK = threading.Lock()

# True while the log already says the file can't be used, so the panel's
# three reads a second don't repeat it; a usable read clears it.
_unusable_logged = [False]


# ── Persistence ───────────────────────────────────────────────────────────────

def _empty() -> dict[str, Any]:
    return {"timers": [dict(EMPTY_TIMER) for _ in range(MAX_SLOTS)]}


def _usable(state: Any) -> bool:
    """Whether parsed JSON has the shape every reader here indexes: MAX_SLOTS
    timers, each with a whole, non-negative duration, a true/false `running`
    (and `acknowledged`, when present), and a `started_at` that is None or a
    time carrying its UTC offset."""
    if not isinstance(state, dict):
        return False
    timers = state.get("timers")
    if not isinstance(timers, list) or len(timers) != MAX_SLOTS:
        return False
    for t in timers:
        if not isinstance(t, dict):
            return False
        duration = t.get("duration_seconds")
        if type(duration) is not int or duration < 0:
            return False
        if type(t.get("running")) is not bool:
            return False
        if type(t.get("acknowledged", False)) is not bool:
            return False
        started = t.get("started_at")
        if started is None:
            continue
        if not isinstance(started, str):
            return False
        try:
            if datetime.fromisoformat(started).tzinfo is None:
                return False
        except ValueError:
            return False
    return True


def _load() -> dict[str, Any]:
    """The saved timers, or no timer when there is no file or it can't be
    used (torn, hand-edited, not a timer file), which logs once. The caller
    holds _LOCK."""
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty()
    except (OSError, ValueError, RecursionError) as e:
        # ValueError: not JSON, or not UTF-8. RecursionError: nested deeper
        # than the parser goes.
        problem = type(e).__name__
    else:
        if _usable(state):
            _unusable_logged[0] = False
            loaded: dict[str, Any] = state
            return loaded
        problem = "not a timer file"
    if not _unusable_logged[0]:
        _unusable_logged[0] = True
        log.warning("Ammit: timer_state.json can't be used (%s); reading no timer",
                    problem)
    return _empty()


def _save(state: dict[str, Any]) -> bool:
    """Temp-then-replace so a crash mid-write can't truncate the timer. False,
    with an ERROR line, when the write fails. The caller holds _LOCK."""
    tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        log.error("Ammit: save failed: %s", e)
        return False
    return True


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


def _fmt(seconds: int) -> str:
    """HH:MM:SS string. Internal — the dashboard reads query_all()['display']."""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


# ── Public API ────────────────────────────────────────────────────────────────

def start_timer(slot: int, duration_seconds: int) -> bool:
    """Count down duration_seconds from now (slot is 0-indexed). True once
    saved."""
    with _LOCK:
        state = _load()
        t = state["timers"][slot]
        t["duration_seconds"] = duration_seconds
        t["started_at"]       = datetime.now().astimezone().isoformat()
        t["running"]          = True
        t["acknowledged"]     = False   # a fresh countdown re-arms the badge
        return _save(state)


def acknowledge_timer(slot: int) -> bool:
    """Silence the passive badge for the CURRENT expiry without touching the
    timer itself — cleared again the next time this slot starts (fresh
    countdown) or resets. See the 2026-09-09 module docstring note. True
    once saved."""
    with _LOCK:
        state = _load()
        state["timers"][slot]["acknowledged"] = True
        return _save(state)


def stop_timer(slot: int) -> bool:
    """Pause: freeze remaining time so it can resume later. True once saved."""
    with _LOCK:
        state = _load()
        t = state["timers"][slot]
        t["duration_seconds"] = _remaining(t)
        t["started_at"]       = None
        t["running"]          = False
        return _save(state)


def reset_timer(slot: int) -> bool:
    """Clear the slot back to no timer. True once saved."""
    with _LOCK:
        state = _load()
        state["timers"][slot] = dict(EMPTY_TIMER)
        return _save(state)


def query_all() -> list[dict[str, Any]]:
    """Returns a list of dicts, one per slot, 1-indexed for readability."""
    with _LOCK:
        state = _load()
    out = []
    for i, t in enumerate(state["timers"]):
        rem = _remaining(t)
        out.append({
            "slot":             i + 1,
            "running":          t["running"],
            "remaining_seconds": rem,
            "display":          _fmt(rem),
            "expired":          t["running"] and rem == 0,
            # .get(): older timer_state.json files predate this field.
            "acknowledged":     t.get("acknowledged", False),
        })
    return out


# ── Metis contract ────────────────────────────────────────────────────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "manage_timer",
        "description": (
            "Starts, stops, resets or reads the state of a single countdown timer. "
            "Call when the user wants to be timed or reminded after a set number "
            "of minutes, or asks how long is left on a running timer. "
            "This changes timer state — do not call it to answer a question "
            "about the current time or date."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start", "stop", "reset", "query"],
                    "description": (
                        "start begins a countdown (needs duration_minutes), "
                        "stop pauses it, reset clears it, query reads its state."
                    ),
                },
                # No `slot` parameter: there is exactly one timer, so declaring it
                # only asks the model to reason about a value that can never vary.
                # handle() always uses slot 0. If MAX_SLOTS ever grows, it comes
                # back — declared here AND accepted by handle(), never just one.
                "duration_minutes": {
                    "type": "number",
                    "description": "Duration in minutes. Required for start action.",
                },
            },
            "required": ["action"],
        },
    },
}

# What the code reads about each tool here; the model never sees it
# (CONVENTIONS §8, "TOOL_META"). network: none = local only, cached = a poll
# or TTL cache, live = may reach the network on every call. mutates = it
# changes state outside the turn.
TOOL_META: dict[str, dict[str, Any]] = {
    "manage_timer": {"domain": "time", "network": "none", "mutates": True},
}


def minutes_to_seconds(duration_minutes: object) -> Optional[int]:
    """duration_minutes as a whole number of seconds, at least 1 — or None.

    Seconds, not minutes, are what get checked: 0.001 min rounds to a 0-second
    countdown, which is the same instant alarm as no duration at all. Takes
    `object` because a direct caller can pass anything; Pythia's argument gate
    has already made it a number on the model's path. The countdown's minutes
    box asks here too, so typed text and the model's number meet one rule."""
    if duration_minutes is None or isinstance(duration_minutes, bool):
        return None
    if not isinstance(duration_minutes, (int, float, str)):
        return None
    try:
        minutes = float(duration_minutes)
    except (ValueError, OverflowError):
        # OverflowError: an int too big for a float (JSON has no size limit).
        return None
    # A float near its top is finite while minutes * 60 is not.
    if not math.isfinite(minutes * 60):
        return None
    seconds = round(minutes * 60)
    return seconds if seconds >= 1 else None


def handle(action: str, duration_minutes: Optional[float] = None) -> dict[str, Any]:
    """
    Called by Pythia's dispatcher when the model invokes manage_timer. Never
    raises. Every action returns {"action", "timers"} — the state it produced,
    in query's shape — or a slug: bad_arguments (start without a usable
    duration; nothing is saved), unknown_action, or write_failed (the file
    wasn't saved, so the timer is as it was).
    """
    if action == "start":
        seconds = minutes_to_seconds(duration_minutes)
        if seconds is None:
            return {"error": "bad_arguments: start needs a positive duration_minutes"}
        saved = start_timer(0, seconds)
    elif action == "stop":
        saved = stop_timer(0)
    elif action == "reset":
        saved = reset_timer(0)
    elif action == "query":
        saved = True
    else:
        return {"error": f"unknown_action: {action!r} — use start, stop, reset or query"}
    if not saved:
        return {"error": "write_failed: the timer was not saved, so it is as it was"}
    return {"action": action, "timers": query_all()}


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    timers = query_all()
    for t in timers:
        status = "▶" if t["running"] else "■"
        print(f"[Ammit] Slot {t['slot']} {status}  {t['display']}")
