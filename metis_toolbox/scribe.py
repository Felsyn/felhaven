#!/usr/bin/env python3
"""
SCRIBE — Tasks & Notes persistence (Metis tool module, headless)
"Ex tenebris surgit lumen posteris"

Job:         Store the to-do list and notes. Persist the SCRIBE tab's shared
             data dict to scribe_data.json — read it at startup, write it back
             atomically on save. The panel owns all task/note mutation.

Contract:    §2's "Local-only ledger / persistence" flavor: no fetch(), no
             TOOL_DEFINITION, not a Pythia brain tool. Two plain functions:

               load_data()      — read scribe_data.json, or a clean default
                                  ({"tasks": [], "notes": ""}) when it is
                                  missing, unreadable, or not a JSON object.
                                  Never raises. What it returns always has the
                                  shape the panel indexes: "tasks" a list of
                                  {"text": str, "done": bool}, "notes" a str.
                                  A task without text is dropped, a "done"
                                  that isn't true reads as not done, and notes
                                  that aren't text read as empty, each logged;
                                  the next save writes the file without them.
               save_data(data)  — write the dict atomically (tempfile in the
                                  same dir + os.replace, §9) so a crash mid-write
                                  can't truncate the file. Never raises: it runs
                                  on the Tk main thread via felhaven's debounced
                                  _flush_save, so a disk error is logged, not
                                  thrown into the UI. Returns True when the
                                  file was written, False when it wasn't;
                                  felhaven hands that to the SCRIBE tab, which
                                  says so on its count line.

Threads:     Both run on the Tk main thread only (startup, the debounced save,
             and the flush in felhaven's _on_close), so the file has no
             reader on another thread and needs no lock (themis.py has one).

Upstream:    felhaven.py (load_data at startup into the dict it hands
             CogitatorPanel; every edit reaches save_data through _schedule_save,
             500 ms debounced, and _on_close flushes a pending one),
             panels/scribe_panel.py (mutates the dict).
Downstream:  scribe_data.json (committed? no — per-user state, gitignored, §9).

DATA FILE:   scribe_data.json at the app root, anchored to __file__ (portable).
             Schema: { "tasks": [{"text": str, "done": bool}, ...], "notes": str }

Requires:    json, logging, os, tempfile, pathlib (stdlib only). No tkinter —
             stays independently unit-testable and UI-free, like themis.py.

2026-07-25:  The first audit deleted the unused task/notes tool interface
             (add_task/toggle_task/delete_task/set_notes/append_note/handle; the
             panel owns mutation), made save_data atomic (§9), print → logger.

V0.03: Walked through 2026-11-21 (in the repo's first commit, 2026-06-10;
       first audited 2026-07-25). Checked every claim against the code and felhaven's save
       path. load_data promised the panel's keys but not their shape: a
       hand-edited file with a task missing "done", a non-list "tasks" or
       null notes made ScribePanel raise while Felhaven built it, so Felhaven
       didn't start (measured). It now hands back only what the panel can
       draw. Contract names the §2 row; Threads added. Later that day (S25,
       the owner's pick): save_data returns whether it saved, so a failed
       save shows on the SCRIBE tab instead of only in the log.
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger("METIS.scribe")

# ── Portable data path ───────────────────────────────────────────────────────
# Anchored to this file, not the launching script — stable regardless of how
# the program is started. scribe.py sits at the package root, so .parent is it.
APP_DIR   = Path(__file__).resolve().parent
DATA_FILE = APP_DIR / "scribe_data.json"


# ─────────────────────────────────────────────────────────────────────────────
#  DATA LAYER
# ─────────────────────────────────────────────────────────────────────────────

def load_data() -> dict[str, Any]:
    """Return persisted data, or a clean default when the file is missing,
    unreadable, or not a JSON object. Never raises.

    Guarantees the shape the panel indexes directly ("tasks" a list of
    {"text": str, "done": bool}, "notes" a str), so a hand-edited or partial
    file can't stop the panel, and with it Felhaven, from being built.
    """
    if DATA_FILE.exists():
        try:
            raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("scribe: could not read %s (%s) — using empty default",
                        DATA_FILE.name, type(e).__name__)
        else:
            if isinstance(raw, dict):
                return _drawable(raw)
            log.warning("scribe: %s root is not an object — using empty default",
                        DATA_FILE.name)
    return {"tasks": [], "notes": ""}


def _drawable(raw: dict[str, Any]) -> dict[str, Any]:
    """*raw* with "tasks" and "notes" in the shape the panel draws. Other keys
    are kept as they are; what can't be drawn is dropped and logged."""
    tasks = raw.get("tasks", [])
    if not isinstance(tasks, list):
        log.warning("scribe: %s tasks is not a list — starting with none",
                    DATA_FILE.name)
        tasks = []
    kept = []
    for task in tasks:
        if isinstance(task, dict) and isinstance(task.get("text"), str):
            task["done"] = task.get("done") is True
            kept.append(task)
    if len(kept) != len(tasks):
        log.warning("scribe: %s had %d task(s) without text — left out",
                    DATA_FILE.name, len(tasks) - len(kept))
    raw["tasks"] = kept
    notes = raw.get("notes", "")
    if not isinstance(notes, str):
        log.warning("scribe: %s notes are not text — starting empty", DATA_FILE.name)
        notes = ""
    raw["notes"] = notes
    return raw


def save_data(data: dict[str, Any]) -> bool:
    """Write *data* to scribe_data.json atomically (tempfile + os.replace, §9),
    so a crash mid-write leaves the previous file intact. Returns whether it
    was written.

    Never raises: this runs on the Tk main thread (felhaven's debounced
    _flush_save), so a disk error is logged and answered with False rather
    than thrown into the UI.
    """
    try:
        directory = os.path.dirname(os.path.abspath(DATA_FILE))
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".scribe-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, DATA_FILE)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    except Exception as e:
        log.warning("scribe: save error: %s", e)
        return False
    return True


if __name__ == "__main__":
    d = load_data()
    print(f"[Scribe] {len(d['tasks'])} task(s), "
          f"{len(str(d['notes']))} note char(s) — {DATA_FILE}")
