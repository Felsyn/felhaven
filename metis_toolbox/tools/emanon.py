"""
emanon.py — The Unnamed Watcher
================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Watch the stack's logs and report what broke — read-only, never
             act. Surfaces what fired, what responded, and what failed, with
             the reason.

             "Emanon" = "no name" reversed: the watcher is invisible until
             something goes wrong. (The one module whose name won't tell
             future-you what it does — so this docstring carries the weight.)

Contract:    Diagnoses, never treats. Borrowed wholesale from Hygieia:
               - Read-only to the log files. Writes nothing.
               - Never kills, restarts, or signals anything.
               - Makes no decisions based on what it reads.
             fetch() never raises — a watcher that crashes is worse than
             useless. All failure paths return a safe degraded dict.

Data source: logs/*.log, written by metis_logging.py. Each line is
             ` | `-delimited: timestamp | level | logger | message
             Emanon parses by splitting on " | " — no regex.

Status verdict (Hygieia's precedence, collapsed for one sidebar dot):
             failed   — an ERROR/CRITICAL line in the recent window
             degraded — a WARNING line in the recent window
             nominal  — only INFO, everything humming

Polling:     NONE. Kairos owns the clock. fetch() is called on each tick
             and returns a fresh rolling snapshot of the log tail. No
             while-loop, no sleep, no snapshot file — that was Hygieia's
             cross-process daemon design; Emanon lives in-process.

Upstream:    metis_logging.py (writes the files), kairos.py (calls fetch)
Downstream:  panels/emanon_panel.py (display surface)

Requires:    stdlib only (pathlib, collections)
"""

import logging
from collections import deque
from pathlib import Path
from typing import Any

log = logging.getLogger("METIS.emanon")

# ── Config ────────────────────────────────────────────────────────────────────

# Same logs/ directory metis_logging.py writes to. Anchored to this file.
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

# Field delimiter — must match metis_logging._DELIM exactly.
_DELIM = " | "

# How many recent lines to surface to the panel.
_TAIL_LINES = 40

# How many bytes to read from the end of each file. Reading the whole file
# every tick would be wasteful once logs grow; this caps the work and still
# covers far more than _TAIL_LINES worth of text.
_TAIL_BYTES = 64_000

# Levels that escalate the status verdict.
_FAIL_LEVELS = {"ERROR", "CRITICAL"}
_WARN_LEVELS = {"WARNING"}

# Emanon must not report on its own logger — a watcher reporting its own
# "I read the logs" lines is a feedback loop. We drop them silently.
_SELF_LOGGER = "METIS.emanon"


# ── Internals ─────────────────────────────────────────────────────────────────

def _read_tail(path: Path, max_bytes: int = _TAIL_BYTES) -> list[str]:
    """
    Read up to the last max_bytes of a file, return its lines.
    Never raises — returns [] on any failure.
    """
    try:
        size = path.stat().st_size
        with path.open("r", encoding="utf-8", errors="replace") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()  # discard the partial first line after the seek
            return f.read().splitlines()
    except FileNotFoundError:
        return []
    except Exception as e:
        # Logged, not silent (Hygieia's rule) — but at debug, since a missing
        # or locked log file shouldn't spam Emanon's own output every tick.
        log.debug(f"could not read {path.name}: {e}")
        return []


def _parse_line(raw: str) -> dict[str, Any] | None:
    """
    Split one delimited log line into structured fields.

    Returns {"ts", "level", "logger", "message", "raw"} or None if the line
    doesn't match the expected shape (continuation lines, tracebacks, blanks).
    A None return means "show as-is / skip for counting" — never an error.
    """
    parts = raw.split(_DELIM, 3)
    if len(parts) != 4:
        return None
    ts, level, logger_name, message = (p.strip() for p in parts)
    return {
        "ts":      ts,
        "level":   level,
        "logger":  logger_name,
        "message": message,
        "raw":     raw,
    }


def _verdict(entries: list[dict[str, Any]]) -> str:
    """Collapse many entries into one status using Hygieia's precedence."""
    has_warn = False
    for e in entries:
        if e["level"] in _FAIL_LEVELS:
            return "failed"          # highest precedence — short-circuit
        if e["level"] in _WARN_LEVELS:
            has_warn = True
    return "degraded" if has_warn else "nominal"


# ── Public API ────────────────────────────────────────────────────────────────

def fetch() -> dict[str, Any]:
    """
    Kairos entry point. Scan the tail of every logs/*.log file, merge,
    sort by timestamp, and return a rolling snapshot. NEVER raises.

    Returns:
        {
            "status":        "nominal" | "degraded" | "failed",
            "error_count":   int,   # ERROR/CRITICAL in the window
            "warning_count": int,   # WARNING in the window
            "entries":       [ {ts, level, logger, message, raw}, ... ],
                                    # most-recent last, capped at _TAIL_LINES
            "files":         [str, ...],   # which log files were read
        }

    On total failure, returns a safe degraded dict rather than raising —
    a watcher that crashes is worse than one that reports "I can't see."
    """
    try:
        if not LOG_DIR.exists():
            return {
                "status":        "nominal",
                "error_count":   0,
                "warning_count": 0,
                "entries":       [],
                "files":         [],
                "note":          "no logs/ directory yet",
            }

        all_entries: list[dict[str, Any]] = []
        files_read: list[str] = []

        for path in sorted(LOG_DIR.glob("*.log")):
            files_read.append(path.name)
            for raw in _read_tail(path):
                parsed = _parse_line(raw)
                if parsed is None:
                    continue
                if parsed["logger"] == _SELF_LOGGER:
                    continue  # don't report on ourselves — no feedback loop
                all_entries.append(parsed)

        # Sort by timestamp string. The format is ISO-like and fixed-width,
        # so lexical sort == chronological sort. Cheap and correct.
        all_entries.sort(key=lambda e: e["ts"])

        # Count over the full window before trimming for display.
        error_count   = sum(1 for e in all_entries if e["level"] in _FAIL_LEVELS)
        warning_count = sum(1 for e in all_entries if e["level"] in _WARN_LEVELS)

        # Trim to the most recent N for the panel.
        recent = all_entries[-_TAIL_LINES:]

        return {
            "status":        _verdict(recent),
            "error_count":   error_count,
            "warning_count": warning_count,
            "entries":       recent,
            "files":         files_read,
        }

    except Exception as e:
        log.error(f"emanon.fetch failed unexpectedly: {e}")
        return {
            "status":        "degraded",
            "error_count":   0,
            "warning_count": 0,
            "entries":       [],
            "files":         [],
            "note":          f"watcher error: {type(e).__name__}",
        }


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    snap = fetch()
    print(f"status={snap['status']}  "
          f"errors={snap['error_count']}  warnings={snap['warning_count']}  "
          f"files={snap['files']}")
    for e in snap["entries"][-10:]:
        print(f"  {e['ts']}  {e['level']:<7}  {e['logger']}  {e['message']}")
