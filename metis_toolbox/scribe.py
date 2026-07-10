#!/usr/bin/env python3
"""
SCRIBE — Tasks & Notes (Metis tool module, headless)
"Ex tenebris surgit lumen posteris"

Job:         Store and manage the to-do list and notes (local CRUD).

Pure task & notes state management for Felhaven. No GUI, no Tkinter.
Persists to scribe_data.json beside the launching script.

Public API:
    handle()                -> dict   # full snapshot (read-only)
    add_task(text)          -> dict
    toggle_task(index)      -> dict
    delete_task(index)      -> dict
    set_notes(text)         -> dict
    append_note(line)       -> dict
    load_data()             -> dict
    save_data(data)         -> None

DATA FILE — scribe_data.json, stored next to the launching script (portable).
            Schema: { "tasks": [{"text": str, "done": bool}, ...], "notes": str }

Dependencies: stdlib only.
"""

import json
from pathlib import Path

# ── Portable data path ───────────────────────────────────────────────────────
# Anchored to this file, not the launching script — stable regardless of how
# the program is started. scribe.py sits at the package root, so .parent is it.
APP_DIR   = Path(__file__).resolve().parent
DATA_FILE = APP_DIR / "scribe_data.json"


# ─────────────────────────────────────────────────────────────────────────────
#  DATA LAYER
# ─────────────────────────────────────────────────────────────────────────────

def load_data() -> dict:
    """Return persisted data, or a clean default if none exists."""
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"tasks": [], "notes": ""}


def save_data(data: dict) -> None:
    """Write data to disk. Silently logs on failure (never crashes Metis)."""
    try:
        DATA_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        print(f"[scribe] save error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  METIS TOOL INTERFACE
#  All public functions load → mutate → save → return a snapshot dict.
#  The snapshot is human- and machine-readable JSON-serialisable.
# ─────────────────────────────────────────────────────────────────────────────

def _snapshot(data: dict) -> dict:
    """
    Produce the canonical Metis-readable snapshot.

    {
        "tasks":      [{"index": int, "text": str, "done": bool}, ...],
        "open_count": int,
        "done_count": int,
        "notes":      str,
        "data_file":  str   ← absolute path, for Metis context
    }
    """
    tasks = [
        {"index": i, "text": t["text"], "done": t["done"]}
        for i, t in enumerate(data.get("tasks", []))
    ]
    open_count = sum(1 for t in tasks if not t["done"])
    return {
        "tasks":      tasks,
        "open_count": open_count,
        "done_count": len(tasks) - open_count,
        "notes":      data.get("notes", ""),
        "data_file":  str(DATA_FILE),
    }


def handle() -> dict:
    """Return a full snapshot of the current tasks and notes. Read-only."""
    return _snapshot(load_data())


def add_task(text: str) -> dict:
    """Append a new open task. Returns updated snapshot."""
    text = text.strip()
    if not text:
        return handle()
    data = load_data()
    data["tasks"].append({"text": text, "done": False})
    save_data(data)
    return _snapshot(data)


def toggle_task(index: int) -> dict:
    """Flip the done/open state of task at *index*. Returns updated snapshot."""
    data = load_data()
    tasks = data.get("tasks", [])
    if 0 <= index < len(tasks):
        tasks[index]["done"] = not tasks[index]["done"]
        save_data(data)
    return _snapshot(data)


def delete_task(index: int) -> dict:
    """Remove task at *index*. Returns updated snapshot."""
    data = load_data()
    tasks = data.get("tasks", [])
    if 0 <= index < len(tasks):
        tasks.pop(index)
        save_data(data)
    return _snapshot(data)


def set_notes(text: str) -> dict:
    """Overwrite the notes field entirely. Returns updated snapshot."""
    data = load_data()
    data["notes"] = text
    save_data(data)
    return _snapshot(data)


def append_note(line: str) -> dict:
    """Append *line* to notes (separated by newline). Returns updated snapshot."""
    data = load_data()
    existing = data.get("notes", "").rstrip()
    data["notes"] = (existing + "\n" + line.strip()) if existing else line.strip()
    save_data(data)
    return _snapshot(data)
