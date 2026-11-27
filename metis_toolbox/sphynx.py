"""
sphynx.py — Riddle-Posing Guardian (logic)
============================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Verify a PIN against the stored check, and track how many
             attempts remain. The check lives in config/sphynx_data.json.
             Nothing else — no window, no subprocess. The one thing it
             writes is that gate file: create() seeds it on first run, and
             the first right PIN rewrites an old-form check once (see The
             check). Attempt counts never touch disk (see State).

Contract:    preflight()      — validate the gate file loads AND carries a
                                 PIN check that opens; raises HashFileError
                                 if not. The panel uses it to tell a normal
                                 launch (gate) from a first run (a missing
                                 file, or one with no usable check → setup
                                 screen), not to fail closed.
             is_disabled()    — True if the user skipped the gate on first
                                 run. A missing/broken file reads False.
             riddle()         — the stored riddle to pose at the gate, or a
                                 generic prompt if none is stored.
             create(pin, …)   — seed the gate file on first run with a PIN +
                                 riddle, or a skipped-gate flag. Atomic (see
                                 First run). Raises ValueError for an empty
                                 PIN, OSError if the seal or the write fails.
             verify(pin)      — check pin against the stored check.
                                 Decrements the attempt counter on a miss.
                                 Returns False without decrementing once
                                 attempts are exhausted, even for a correct
                                 pin. Raises HashFileError if the gate file
                                 can't be loaded.
             attempts_left()  — read-only peek for the UI. Floors at 0.

The check:   cerberus.pin_record(pin, "sphynx"), Cerberus's recipe (the
             toolbox has one): scrypt at OWASP's floor, about half a second a
             check, its salt, cost and digest sealed with Windows DPAPI under
             this gate's name. A copy of sphynx_data.json opens on no other
             Windows account, Cerberus's check won't open here, and on this
             account every guess still costs scrypt. A file from before
             2026-11-26 holds an unsalted SHA-256 (`pin_hash`): verify() still
             reads it, and the first right PIN rewrites it in the sealed form
             (a WARNING if it can't; the next right PIN tries again).
             CONVENTIONS §12 (2026-11-26).

State:       _ATTEMPTS_REMAINING is a plain module-level int, reset to 3
             every time this module is imported (i.e. every process start).
             Nothing about attempts is ever written to disk — that is what
             makes a soft-lock structurally impossible, not a safeguard
             bolted on top. Relaunching the app gives a fresh 3 attempts,
             on purpose.

Threat model: this is a flavor gate, not real security. It guards one
             door, Felhaven.bat's launch; `python felhaven.py` runs without
             it, and whoever can write the gate file can skip it. The sealed
             check protects the PIN itself: a copied file gives nothing away,
             and anything running as this Windows user still pays scrypt for
             every guess, so a short PIN falls in minutes to hours rather than
             a second. Hence the setup screen still says "use a PIN you use
             nowhere else". It only needs to stop a child at the keyboard. Do
             not add lockout persistence.

Cerberus:    a separate PIN, file and attempt counter, and the same recipe
             under a different name. Neither PIN decrypts anything (Cerberus
             keeps its keys under Windows DPAPI since v2), and passing this
             gate opens no Cerberus session: Felhaven starts as a new process,
             sealed. Cerberus's Custody lists sphynx_data.json, so whoever
             holds Cerberus's PIN can open this file in an editor, and with it
             reset or skip the gate.

First run:   sphynx_data.json is per-user now (gitignored, no longer shipped
             with the author's PIN), so a fresh clone has no file. create()
             writes it from the panel's first-run setup screen: the user's own
             riddle + PIN, or a "disabled" flag if they skip the gate. Later
             launches read the stored riddle and gate normally, or bypass when
             disabled.

Upstream:    sphynx_panel.py
Downstream:  config/sphynx_data.json (per-user, gitignored; written by
             create() and once by verify(), read by preflight/verify/riddle/
             is_disabled); cerberus.py (pin_record / pin_matches)

Requires:    cerberus (the PIN check), hashlib (the old check), hmac, json,
             logging, os, tempfile, pathlib. No tkinter — this module stays
             independently unit-testable and UI-free.

2026-07-24:  walkthrough audit — Contract completed (is_disabled/riddle/
             create were public but undocumented), the stale "no persistence"
             / preflight-"fail closed" lines corrected, and a read-only
             __main__ status block added (§3.7).

V0.03:       Walked through 2026-11-17 (the V0.03 walkthrough, CONVENTIONS
             §3). The code is unchanged since July; the words were not. The
             threat model said the PIN fell to "anyone reading the source",
             but the hash is in the gate file, not the source. It now says
             what the gate guards and how its PIN stands beside Cerberus's,
             which changed under it in v2.

2026-11-26:  the check moved from an unsalted SHA-256 to Cerberus's sealed
             scrypt (see The check). A logger came with it, for the one
             WARNING above; nothing else here logs.
"""

import hashlib
import hmac
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import cerberus

log = logging.getLogger("METIS.sphynx")

_DATA_PATH = Path(__file__).resolve().parent / "config" / "sphynx_data.json"

_PURPOSE = "sphynx"       # this gate's name in cerberus's sealed PIN check

_ATTEMPTS_REMAINING = 3   # resets to this every process start — see State above

# Shown when the data file has no riddle of its own (e.g. an older file, or a
# defensive fallback) — a generic prompt rather than a blank gate.
_DEFAULT_RIDDLE = "Speak the word to enter."


class HashFileError(Exception):
    """sphynx_data.json is missing, unreadable, or holds no usable PIN check."""


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


def _load_check() -> dict[str, Any]:
    """The data dict, holding a sealed check that opens or an old-form
    `pin_hash`. Raises HashFileError if not: no file, no check, or a seal that
    won't open (another account's, Cerberus's, changed bytes). The panel then
    offers first-run setup."""
    data = _load()
    try:
        if "pin_check" in data:
            cerberus.check_pin_record(data["pin_check"], _PURPOSE)
        else:
            _legacy_hash(data)
    except (KeyError, TypeError, ValueError, cerberus.PinCheckError) as e:
        raise HashFileError(
            f"sphynx_data.json holds no usable PIN check: {type(e).__name__}") from e
    return data


def _legacy_hash(data: dict[str, Any]) -> str:
    """The old-form `pin_hash`, an unsalted SHA-256 hex digest."""
    pin_hash = data["pin_hash"]
    # 64 characters that are 32 bytes of hex: no spaces, all ASCII.
    if not (isinstance(pin_hash, str) and len(pin_hash) == 64
            and len(bytes.fromhex(pin_hash)) == 32):
        raise TypeError("pin_hash is not a SHA-256 hex digest")
    return pin_hash


def _legacy_matches(pin: str, data: dict[str, Any]) -> bool:
    """The old check, one unsalted SHA-256. Read only so the first right PIN
    can rewrite the file in the sealed form; BACKLOG says when it goes."""
    return hmac.compare_digest(
        hashlib.sha256(pin.strip().encode("utf-8")).hexdigest(), _legacy_hash(data))


def preflight() -> None:
    """Validate the gate file loads AND carries a PIN check that opens, without
    checking a PIN. The panel calls this to tell a normal launch (gate) from a
    first run (a missing file, or one with no usable check → HashFileError →
    setup screen)."""
    _load_check()


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


def _write(payload: dict[str, Any]) -> None:
    """The gate file, atomically: a temp file in the same folder, then
    os.replace, so a crash mid-write can't leave a half-file that fails
    closed forever."""
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


def create(pin: str, riddle: str = "", *, disabled: bool = False) -> None:
    """Write sphynx_data.json from the first-run setup screen. `disabled=True`
    records a skipped gate (no PIN needed); otherwise it stores the user's own
    riddle + a sealed check of the PIN (about half a second). Raises ValueError
    for an empty PIN, OSError if DPAPI or the disk refuses."""
    if disabled:
        payload: dict[str, Any] = {"disabled": True}
    else:
        payload = {"pin_check": cerberus.pin_record(pin, _PURPOSE),
                   "riddle": riddle.strip(), "disabled": False}
    _write(payload)


def _upgrade(pin: str, data: dict[str, Any]) -> None:
    """Rewrite an old-form file with a sealed check, keeping the riddle and
    flag. A failure leaves the old file and logs a WARNING; the next right PIN
    tries again."""
    payload = {k: v for k, v in data.items() if k != "pin_hash"}
    try:
        payload["pin_check"] = cerberus.pin_record(pin, _PURPOSE)
        _write(payload)
    except (OSError, cerberus.VaultError) as e:
        log.warning("sphynx: the PIN check stays in its old form for now (%s)",
                    type(e).__name__)


def verify(pin: str) -> bool:
    """Check pin against the stored check. Decrements the remaining-attempts
    counter on a miss; once exhausted, always returns False (even for a
    correct pin) without decrementing further. Raises HashFileError if the
    gate file can't be loaded. An old-form file is rewritten sealed on the
    first right PIN."""
    global _ATTEMPTS_REMAINING
    data = _load_check()
    if _ATTEMPTS_REMAINING <= 0:
        return False
    if "pin_check" in data:
        try:
            ok = cerberus.pin_matches(pin, data["pin_check"], _PURPOSE)
        except cerberus.PinCheckError as e:     # it opened a moment ago
            raise HashFileError(
                f"sphynx_data.json holds no usable PIN check: {type(e).__name__}") from e
    else:
        ok = _legacy_matches(pin, data)
        if ok:
            _upgrade(pin, data)
    if ok:
        return True
    _ATTEMPTS_REMAINING -= 1
    return False


def attempts_left() -> int:
    """Read-only peek for the UI to render 'x attempts remaining'."""
    return max(0, _ATTEMPTS_REMAINING)


if __name__ == "__main__":
    # Read-only status probe (§3.7): report the gate's current state without
    # touching the attempt counter or writing the file. create()/verify() are
    # side-effecting, so they are deliberately not exercised here.
    print(f"data file:  {_DATA_PATH}")
    print(f"disabled:   {is_disabled()}")
    print(f"riddle:     {riddle()!r}")
    try:
        sealed = "pin_check" in _load_check()
        print("gate:       a PIN is set" if sealed else
              "gate:       a PIN is set, in the old form (the next right PIN seals it)")
    except HashFileError as e:
        print(f"gate:       not set ({e})")
    print(f"attempts:   {attempts_left()}")
