"""
machine_spirit.py — Keeper of the Oracle's Rite (Pythia's system prompt)
=========================================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Own Pythia's system prompt — the default text plus an optional
             per-install override. Sole holder of DEFAULT_SYSTEM_PROMPT.

Contract:    Root-level persistence flavor (themis.py precedent): no
             tkinter, no fetch(), just plain functions.
               effective_prompt() — override if present & non-empty, else
                                    DEFAULT_SYSTEM_PROMPT. Never raises.
               save(text)         — persist an override (atomic write).
                                    Blank/whitespace-only text CLEARS the
                                    override instead of storing it.
               revert()           — clear the override; effective_prompt()
                                    falls back to DEFAULT.

Storage:     machine_spirit_config.json beside this file, holding only the
             custom prompt when one exists — the default text is NEVER
             written to disk, so a future default-text change takes effect
             for every install that hasn't overridden it. Gitignored (per-
             user state, the felhaven_settings.json precedent).

Upstream:    panels/machine_spirit_panel.py (the MACHINE SPIRIT tab), pythia.py
             (reads effective_prompt() at ask() time — no restart needed).
Downstream:  machine_spirit_config.json

Requires:    json, logging, os, tempfile, pathlib (stdlib only).
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger("METIS.machine_spirit")

_DATA_PATH = Path(__file__).with_name("machine_spirit_config.json")

DEFAULT_SYSTEM_PROMPT = (
    "You are Pythia, Felhaven's Resident AI. You have tools "
    "that fetch live local data — weather, time, system vitals, a countdown "
    "timer, market prices, network status, news headlines, math, and unit "
    "conversions — plus web search: use search_web for current or unknown "
    "facts, then fetch_page on one promising result if you need its full text. "
    "Call a tool whenever the question needs current or computed data rather "
    "than guessing. Keep answers concise and plain-spoken. "
    "Reply in plain prose — complete sentences and short paragraphs. Do NOT use "
    "Markdown: no asterisks, bullet points, numbered lists, headers, or bold or "
    "italic markers. Your answers are read aloud, so write them to be spoken."
)


def _load_override() -> str:
    """The stored override, or "" if none/unreadable (fail-soft, never raises)."""
    try:
        with open(_DATA_PATH, "r", encoding="utf-8") as f:
            raw: Any = json.load(f)
        if not isinstance(raw, dict):
            return ""
        return str(raw.get("prompt", "")).strip()
    except FileNotFoundError:
        return ""
    except (OSError, json.JSONDecodeError, ValueError) as e:
        log.warning("machine_spirit: config unreadable, using default: %s", e)
        return ""


def effective_prompt() -> str:
    """The override if one is set, else DEFAULT_SYSTEM_PROMPT. Read fresh from
    disk every call so a Save takes effect on the very next ask()."""
    override = _load_override()
    return override or DEFAULT_SYSTEM_PROMPT


def save(text: str) -> None:
    """Persist `text` as the override. Blank/whitespace-only text CLEARS the
    override (same as revert()) rather than storing an empty string. Atomic
    write: tempfile in the same dir + os.replace (the themis.py pattern)."""
    stripped = text.strip()
    if not stripped:
        revert()
        return

    directory = os.path.dirname(os.path.abspath(_DATA_PATH))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".machine_spirit-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"prompt": stripped}, f, indent=2)
        os.replace(tmp, _DATA_PATH)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    log.info("machine_spirit: prompt override saved (%d chars)", len(stripped))


def revert() -> None:
    """Clear the override, if any. effective_prompt() then returns DEFAULT."""
    try:
        os.unlink(_DATA_PATH)
        log.info("machine_spirit: override cleared")
    except FileNotFoundError:
        pass


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"[Machine Spirit] override set: {bool(_load_override())}")
    print(f"[Machine Spirit] effective prompt:\n{effective_prompt()}")
