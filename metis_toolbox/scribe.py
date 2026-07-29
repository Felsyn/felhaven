#!/usr/bin/env python3
"""
SCRIBE — Tasks & Notes persistence (Metis tool module, headless)
"Ex tenebris surgit lumen posteris"

Job:         Store the to-do list and notes. Persist the SCRIBE tab's shared
             data dict to scribe_data.json — read it at startup, write it back
             atomically on save. The panel owns all task/note mutation.

Contract:    Root-level persistence flavor (like themis.py): no fetch(), no
             TOOL_DEFINITION, not a Pythia brain tool. Two plain functions:

               load_data()      — read scribe_data.json, or a clean default
                                  ({"tasks": [], "notes": ""}) when it is
                                  missing, unreadable, or not a JSON object.
                                  Never raises; guarantees the tasks/notes keys
                                  the panel indexes directly.
               save_data(data)  — write the dict atomically (tempfile in the
                                  same dir + os.replace, §9) so a crash mid-write
                                  can't truncate the file. Never raises: it runs
                                  on the Tk main thread via felhaven's debounced
                                  _flush_save, so a disk error is logged, not
                                  thrown into the UI.

Upstream:    felhaven.py (load at startup; save through the CogitatorPanel
             on_save callback), panels/scribe_panel.py (mutates the dict).
Downstream:  scribe_data.json (committed? no — per-user state, gitignored, §9).

DATA FILE:   scribe_data.json at the app root, anchored to __file__ (portable).
             Schema: { "tasks": [{"text": str, "done": bool}, ...], "notes": str }

Requires:    json, logging, os, tempfile, pathlib (stdlib only). No tkinter —
             stays independently unit-testable and UI-free, like themis.py.

V0.02: Audited 2026-07-25 — deleted the unused task/notes tool interface
       (add_task/toggle_task/delete_task/set_notes/append_note/handle; the
       panel owns mutation), made save_data atomic (§9), print → logger.
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

    Guarantees the "tasks" and "notes" keys the panel indexes directly, so a
    valid-but-partial file can't KeyError the panel at build time.
    """
    if DATA_FILE.exists():
        try:
            raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("scribe: could not read %s (%s) — using empty default",
                        DATA_FILE.name, type(e).__name__)
        else:
            if isinstance(raw, dict):
                raw.setdefault("tasks", [])
                raw.setdefault("notes", "")
                return raw
            log.warning("scribe: %s root is not an object — using empty default",
                        DATA_FILE.name)
    return {"tasks": [], "notes": ""}


def save_data(data: dict[str, Any]) -> None:
    """Write *data* to scribe_data.json atomically (tempfile + os.replace, §9),
    so a crash mid-write leaves the previous file intact.

    Never raises: this runs on the Tk main thread (felhaven's debounced
    _flush_save), so a disk error is logged and swallowed rather than thrown
    into the UI.
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


if __name__ == "__main__":
    d = load_data()
    print(f"[Scribe] {len(d['tasks'])} task(s), "
          f"{len(str(d['notes']))} note char(s) — {DATA_FILE}")
