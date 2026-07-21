"""
sphynx.py — Riddle-Posing Guardian (logic)
============================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Verify a PIN against the stored hash, and track how many
             attempts remain. The hash lives in sphynx_data.json.
             Nothing else — no window, no
             subprocess, no persistence.

Contract:    preflight()      — validate the hash file loads; raises
                                 HashFileError if not. Lets the panel fail
                                 closed before it draws anything.
             verify(pin)      — strip + utf-8 encode + sha256 pin, compare
                                 against the stored hash. Decrements the
                                 attempt counter on a miss. Returns False
                                 without decrementing once attempts are
                                 exhausted, even for a correct pin. Raises
                                 HashFileError if the hash file can't be
                                 loaded.
             attempts_left()  — read-only peek for the UI. Floors at 0.

State:       _ATTEMPTS_REMAINING is a plain module-level int, reset to 3
             every time this module is imported (i.e. every process start).
             Nothing about attempts is ever written to disk — that is what
             makes a soft-lock structurally impossible, not a safeguard
             bolted on top. Relaunching the app gives a fresh 3 attempts,
             on purpose.

Threat model: this is a flavor gate, not real security. A SHA-256 of a
             short PIN is trivially brute-forceable by anyone reading the
             source. That's fine — it only needs to stop a child at the
             keyboard. Do not add lockout persistence or key stretching.

First run:   sphynx_data.json is per-user now (gitignored, no longer shipped
             with the author's PIN), so a fresh clone has no file. create()
             writes it — the ONE writer this module has ever had — from the
             panel's first-run setup screen: the user's own riddle + PIN, or a
             "disabled" flag if they skip the gate. Later launches read the
             stored riddle and gate normally, or bypass when disabled.

Upstream:    sphynx_panel.py
Downstream:  sphynx_data.json (per-user; written by create(), read by verify)

Requires:    hashlib, json, os, tempfile, pathlib (stdlib only). No tkinter —
             this module stays independently unit-testable and UI-free.
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

_DATA_PATH = Path(__file__).resolve().parent / "config" / "sphynx_data.json"

_ATTEMPTS_REMAINING = 3   # resets to this every process start — see State above

# Shown when the data file has no riddle of its own (e.g. an older file, or a
# defensive fallback) — a generic prompt rather than a blank gate.
_DEFAULT_RIDDLE = "Speak the word to enter."


class HashFileError(Exception):
    """sphynx_data.json is missing, unreadable, or lacks pin_hash."""


def _hash(pin: str) -> str:
    """strip + utf-8 + sha256 — the one hashing recipe create() and verify()
    share, so a set PIN and a checked PIN can never drift."""
    return hashlib.sha256(pin.strip().encode("utf-8")).hexdigest()


def _load() -> dict[str, Any]:
    """The whole data dict. Raises HashFileError if the file is missing,
    unreadable, or not a JSON object."""
    try:
        with open(_DATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("sphynx_data.json root is not an object")
        return data
    except (OSError, json.JSONDecodeError, ValueError) as e:
        raise HashFileError(f"sphynx_data.json missing or malformed: {e}") from e


def _load_pin_hash() -> str:
    try:
        return str(_load()["pin_hash"])
    except (KeyError, TypeError) as e:
        raise HashFileError(f"sphynx_data.json lacks pin_hash: {e}") from e


def preflight() -> None:
    """Validate the hash file loads AND carries a PIN, without checking it. The
    panel calls this to tell a normal launch (gate) from a first run (no file →
    HashFileError → setup screen)."""
    _load_pin_hash()


def is_disabled() -> bool:
    """True if the user chose to skip the gate on first run (the choice is
    remembered in sphynx_data.json). A missing/broken file reads False, so the
    panel falls through to preflight() and the first-run setup screen."""
    try:
        return bool(_load().get("disabled", False))
    except HashFileError:
        return False


def riddle() -> str:
    """The stored riddle/statement to pose at the gate, or a generic prompt if
    the file is absent or carries no riddle."""
    try:
        return str(_load().get("riddle") or _DEFAULT_RIDDLE)
    except HashFileError:
        return _DEFAULT_RIDDLE


def create(pin: str, riddle: str = "", *, disabled: bool = False) -> None:
    """Write sphynx_data.json — the module's only writer, called once from the
    first-run setup screen. `disabled=True` records a skipped gate (no PIN
    needed); otherwise it stores the user's own riddle + hashed PIN. Atomic
    (tempfile in the same dir + os.replace) so a crash mid-write can't leave a
    half-file that fails closed forever."""
    if disabled:
        payload: dict[str, Any] = {"disabled": True}
    else:
        payload = {"pin_hash": _hash(pin), "riddle": riddle.strip(),
                   "disabled": False}
    directory = os.path.dirname(os.path.abspath(_DATA_PATH))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".sphynx-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, _DATA_PATH)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def verify(pin: str) -> bool:
    """Check pin against the stored hash. Decrements the remaining-attempts
    counter on a miss; once exhausted, always returns False (even for a
    correct pin) without decrementing further. Raises HashFileError if the
    hash file can't be loaded."""
    global _ATTEMPTS_REMAINING
    expected = _load_pin_hash()
    if _ATTEMPTS_REMAINING <= 0:
        return False
    if _hash(pin) == expected:
        return True
    _ATTEMPTS_REMAINING -= 1
    return False


def attempts_left() -> int:
    """Read-only peek for the UI to render 'x attempts remaining'."""
    return max(0, _ATTEMPTS_REMAINING)
