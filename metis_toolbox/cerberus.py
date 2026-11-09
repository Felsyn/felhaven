"""
cerberus.py — The Guardian of Secrets (logic)
=============================================
Metis Toolbox | Anti-Legion: THREE HEADS, ONE GUARDIAN

Job:         Keep the API keys encrypted to this Windows account, behind a PIN
             child lock. Its three heads are the Vault (the keys), Custody (a
             PIN-gated list of config files) and the Ledger (an access log).
             Every module that stores or reads a secret defers here; nothing
             else in the toolbox rolls its own crypto.

             The one deliberate exception to §0's "one file, one job": the
             three heads are facets of one guardian, sharing one session.
             Recorded in CONVENTIONS §12.

At rest:     Each key is encrypted with Windows DPAPI (CryptProtectData) under
             the current Windows user, and the entry's name goes in as DPAPI's
             optional entropy. A copy of cerberus_vault.json (a backup, a synced
             folder, a thumb drive, malware that copies it and leaves) can't be
             read on another account or another PC, and a blob moved into
             another entry's slot fails. Every failure is loud (see Tamper).

             What it does NOT stop: anything running as this Windows user can
             decrypt every key at any time, PIN or no PIN. DPAPI asks Windows,
             not the PIN. A Windows password RESET (a change is fine) or a
             rebuilt profile makes the blobs unreadable; the keys are all
             regenerable, so the answer is to re-enter them.

The PIN:     A CHILD LOCK and a policy gate. It decrypts nothing, so knowing it
             yields no key. It decides when tools may USE the keys (vault_get),
             when the UI may change them (vault_set, retire_vault), and when
             Custody opens a config. cerberus_data.json holds a salted SHA-256
             of it; that hash is fast, and that's fine, because nothing secret
             sits behind it. Changing the PIN touches no key. Three tries per
             process: theater, kept on purpose.

Session:     unlock(pin) opens ONE module-level session shared by every tab and
             tool; lock(reason) closes it. fetch(), Cerberus's own 2 s Kairos
             worker, closes it when the person steps away: Windows locked, the
             PC asleep, or 60 min with no keyboard or mouse input anywhere on
             the PC (tools/hypnos.py reads those). Felhaven's exit closes it
             too. Each lock and unlock is a Ledger row.

Tamper:      Loud, never silent. An old PIN-encrypted (v1) or unparseable vault
             file, an entry that won't decrypt, and a PIN file recreated while
             the vault held keys each write a Ledger row, log an ERROR (Emanon
             shows it) and raise an alarm. fetch() carries the alarms to the
             Cerberus tab, the Dynastic Vault and the Home lamp.

Contract:    preflight()                 — the PIN file loads (else HashFileError)
             verify(pin) -> bool         — PIN check; a miss burns an attempt
             attempts_left() -> int
             unlock(pin) -> bool · lock(reason) · is_unlocked() · lock_reason()
             set_pin(pin)                — write the PIN check; touches no key
             vault_set(name, value)      — encrypt + store; session required
             vault_get(name) -> str      — decrypt; session required
             vault_names() -> list       — names only, never values
             vault_status() -> str       — missing | v2 | v1 | corrupt | io_error
             vault_health() -> dict      — name -> "ok" | "unreadable". Decrypts
                                           and discards, and needs no PIN: under
                                           DPAPI a PIN can't stop a decrypt, and
                                           pretending otherwise is theater.
             retire_vault() -> str       — set a v1/corrupt file aside (renamed,
                                           never deleted); session required
             alarms() -> list · dismiss_alarm(alarm)
             fetch() -> dict             — the Kairos worker (see Session)
             KNOWN_KEYS                  — key name -> what uses it
             manifest_configs() · manifest_config_dir() · custody_open(file)
             ledger_log_custody(file, action) · ledger_entries()
             main(argv) -> int           — the CLI (below)

CLI:         python cerberus.py status   — PIN, vault format, each key readable or
                                           not; never prints a value
             python cerberus.py set NAME — asks for the PIN, then the value
             python cerberus.py setpin   — asks for the current and new PIN
             Secrets come only through hidden prompts (getpass). A value or PIN
             on the command line is refused, because shell history keeps it.

Errors:      VaultError / CustodyError carry a stable `.slug` (§2's error
             vocabulary); the panels map slugs to text.

State:       Session, attempts, alarms and the per-session read map are
             RAM-only, under _SESSION_LOCK, and reset every process start.

Concurrency: Called from worker threads (Midas's pool, Pythia, Kairos's own
             fetch) and the main thread. _SESSION_LOCK guards the session and
             is held across a whole read, so once lock() returns no read can
             succeed. _LEDGER_LOCK guards the Ledger's load→mutate→write. Order
             is always session, then Ledger. Every file is written atomically
             (§9). Recorded in §12.

Upstream:    panels/cerberus_panel.py, panels/midas_panel.py,
             panels/home_panel.py (the lamp), felhaven.py (exit), kairos.py
             (fetch), tools/midas.py, tools/callimachus.py, tools/demeter.py
Downstream:  cerberus_data.json (the PIN check), cerberus_vault.json (DPAPI
             blobs), cerberus_ledger.json (access log) — all gitignored, per
             user; config/cerberus_manifest.json (committed); tools/hypnos.py;
             Windows crypt32 (DPAPI, through ctypes)

Requires:    stdlib only (ctypes for DPAPI). No tkinter, no subprocess.

V0.02:       Audited 2026-07-24 — the deferred Ledger-corruption finding fixed:
             _LEDGER_LOCK + atomic writes (was a non-atomic truncate+rewrite that
             raced under Midas's thread pool). Crypto, salts, and session model
             verified honest and unchanged.
"""

import base64
import getpass
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from tools import hypnos

log = logging.getLogger("METIS.cerberus")

# Anchored to __file__ per §1 — cerberus.py sits AT the app root.
_APP_ROOT = Path(__file__).resolve().parent
_DATA_PATH = _APP_ROOT / "cerberus_data.json"      # gitignored: the PIN check
_VAULT_PATH = _APP_ROOT / "cerberus_vault.json"    # gitignored: DPAPI blobs
_MANIFEST_PATH = _APP_ROOT / "config" / "cerberus_manifest.json"  # committed
_LEDGER_PATH = _APP_ROOT / "cerberus_ledger.json"  # gitignored: access log
_CONFIG_DIR = _APP_ROOT / "config"                 # Custody opens nothing outside

# Every key a tool reads, and what reads it. The Cerberus tab shows a row for
# each even before it's set, so a fresh vault can be filled row by row instead
# of by retyping exact names. tests/test_secret_fixtures.py holds it in lockstep
# with every _VAULT_KEY_NAME in tools/.
KNOWN_KEYS: dict[str, str] = {
    "finnhub_api_key": "Midas — market prices",
    "brave_api_key": "Pythia — web search",
    "opentopography_api_key": "Artemis — terrain",
}

# ── Lock policy (fetch) ──────────────────────────────────────────────────────
IDLE_LOCK_S = 60 * 60     # no input anywhere on the PC for an hour
_SLEEP_LOCK_S = 5.0       # asleep longer than this between two checks
_GAP_LOCK_S = 60.0        # or this long between two checks of a 2 s worker

LOCK_MANUAL = "manual"
LOCK_IDLE = "idle"
LOCK_WINDOWS = "windows_lock"
LOCK_SLEEP = "sleep"
LOCK_EXIT = "exit"

# ── Vault format ─────────────────────────────────────────────────────────────
_VAULT_FORMAT = "cerberus-vault"
_VAULT_VERSION = 2
# Prefixed to the entry name to make DPAPI's optional entropy: a blob opens only
# under its own name, and only as a v2 entry.
_ENTROPY_LABEL = b"felhaven/cerberus/v2\x00"
# CRYPTPROTECT_UI_FORBIDDEN, and nothing else: in particular never
# CRYPTPROTECT_LOCAL_MACHINE (0x4), which would let any account on this PC
# decrypt. Without it, DPAPI binds the blob to the current Windows user.
_DPAPI_FLAGS = 0x1

# ── Error slugs (§2) ─────────────────────────────────────────────────────────
ERR_LOCKED = "cerberus_locked"
ERR_NO_ENTRY = "cerberus_no_entry"
ERR_UNREADABLE = "cerberus_unreadable"
ERR_V1_VAULT = "cerberus_v1_vault"
ERR_CORRUPT = "cerberus_corrupt"
ERR_IO = "cerberus_io_error"
ERR_DPAPI_UNAVAILABLE = "cerberus_dpapi_unavailable"
ERR_PROTECT_FAILED = "cerberus_protect_failed"
ERR_WRITE_FAILED = "cerberus_write_failed"
ERR_BAD_INPUT = "cerberus_bad_input"
ERR_NOTHING_TO_RETIRE = "cerberus_nothing_to_retire"
ERR_RETIRE_FAILED = "cerberus_retire_failed"

ERR_NOT_LISTED = "custody_not_listed"
ERR_NO_CONFIG_DIR = "custody_no_config_dir"
ERR_OUTSIDE_CONFIG = "custody_outside_config"
ERR_NOT_JSON = "custody_not_json"
ERR_MISSING = "custody_missing"

# ── Alarms ───────────────────────────────────────────────────────────────────
ALARM_V1_VAULT = "vault_v1"
ALARM_CORRUPT = "vault_corrupt"
ALARM_PIN_RESET = "pin_reset"
ALARM_UNREADABLE = "unreadable:"        # + the entry's name

# ── RAM-only state, guarded by _SESSION_LOCK (never persisted) ───────────────
# An RLock so a public function can call another without deadlocking itself.
_SESSION_LOCK = threading.RLock()
_ATTEMPTS_REMAINING = 3   # resets every process start — soft-lock is impossible
_SESSION_OPEN = False
_LOCK_REASON: str | None = None
_SESSION_READS: dict[str, str] = {}     # key name -> this session's ledger id
_ALARMS: set[str] = set()
_LAST_PRESENCE: hypnos.Presence | None = None
_LAST_CHECK: float | None = None        # time.time() of fetch's last check
_PRESENCE_FAILED = False

# Serializes the Ledger's load-modify-write: Midas reads keys from a 5-thread
# pool, and every read appends or bumps a row. Taken after _SESSION_LOCK, never
# before it. See CONVENTIONS §12 (2026-07-24).
_LEDGER_LOCK = threading.Lock()


class HashFileError(Exception):
    """cerberus_data.json is missing, unreadable, or lacks its fields."""


class VaultError(Exception):
    """The vault is locked, refused, missing a key, or a key won't decrypt.
    `slug` is the stable part (§2); the rest of the message is for humans."""

    def __init__(self, slug: str, detail: str = "") -> None:
        super().__init__(f"{slug}: {detail}" if detail else slug)
        self.slug = slug


class CustodyError(Exception):
    """Custody refused to open a file. `slug` is the stable part (§2)."""

    def __init__(self, slug: str, detail: str = "") -> None:
        super().__init__(f"{slug}: {detail}" if detail else slug)
        self.slug = slug


# ─────────────────────────────────────────────────────────────────────────────
#  The PIN — a salted hash, and a policy gate only
# ─────────────────────────────────────────────────────────────────────────────

def _load_verify() -> tuple[bytes, str, dict[str, Any]]:
    """(verify_salt, pin_hash, the whole file) from cerberus_data.json."""
    try:
        with open(_DATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        salt = bytes.fromhex(data["verify_salt"])
        pin_hash = data["pin_hash"]
        if not isinstance(pin_hash, str):
            raise TypeError("pin_hash is not a string")
        return salt, pin_hash, data
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise HashFileError(
            f"cerberus_data.json missing or malformed: {type(e).__name__}") from e


def _hash_pin(pin: str, salt: bytes) -> str:
    """Salted SHA-256 of the PIN. Fast on purpose: it guards a child lock, not
    a key (see the module docstring)."""
    return hashlib.sha256(salt + pin.strip().encode("utf-8")).hexdigest()


def preflight() -> None:
    """Raise HashFileError unless the PIN file loads. Lets a panel fail closed
    (or offer first-run setup) before it draws its gate."""
    _load_verify()


def _verify(pin: str) -> bool:
    global _ATTEMPTS_REMAINING
    salt, expected, _ = _load_verify()
    if _ATTEMPTS_REMAINING <= 0:
        return False
    if hmac.compare_digest(_hash_pin(pin, salt), expected):
        return True
    _ATTEMPTS_REMAINING -= 1
    return False


def verify(pin: str) -> bool:
    """Check the PIN. A miss decrements the attempts; once they're gone this is
    always False, even for the right PIN. Raises HashFileError if the PIN file
    can't be loaded. Mirrors sphynx.verify's contract."""
    with _SESSION_LOCK:
        return _verify(pin)


def attempts_left() -> int:
    """For the UI's 'x attempts remain'. Floors at 0."""
    with _SESSION_LOCK:
        return max(0, _ATTEMPTS_REMAINING)


def set_pin(pin: str) -> None:
    """Write a fresh salt and the PIN's hash to cerberus_data.json. Touches no
    key: the PIN decrypts nothing, so changing it needs no re-encryption.

    Called by the Cerberus tab's first-run screen and by `setpin`. If no PIN
    file loaded but the vault already holds keys, someone deleted the PIN file
    to get past the child lock (or it broke): the new file keeps a `reset_at`
    mark and the pin_reset alarm stays up until it's dismissed."""
    if not isinstance(pin, str) or not pin.strip():
        raise ValueError(ERR_BAD_INPUT)
    with _SESSION_LOCK:
        try:
            _, _, old = _load_verify()
            had_pin = True
        except HashFileError:
            old, had_pin = {}, False
        _, entries = _vault_read()
        reset = not had_pin and bool(entries)
        salt = secrets.token_bytes(16)
        payload: dict[str, Any] = {"verify_salt": salt.hex(),
                                   "pin_hash": _hash_pin(pin, salt)}
        if reset:
            payload["reset_at"] = _now()
        elif old.get("reset_at"):
            payload["reset_at"] = old["reset_at"]   # a PIN change isn't a dismissal
        _atomic_write_json(_DATA_PATH, payload)
        if reset:
            _ALARMS.discard(ALARM_PIN_RESET)        # a new reset is a new event
            _raise_alarm(ALARM_PIN_RESET, ("session", "pin_reset", ""),
                         "the PIN file was recreated while the vault held keys")
        else:
            _ledger_event("session", "pin_changed" if had_pin else "pin_set")


# ─────────────────────────────────────────────────────────────────────────────
#  Session
# ─────────────────────────────────────────────────────────────────────────────

def unlock(pin: str) -> bool:
    """Check the PIN and, if it's right, open the shared session. Returns the
    check's result; a wrong PIN burns an attempt and is a Ledger row."""
    global _SESSION_OPEN, _LOCK_REASON
    with _SESSION_LOCK:
        if not _verify(pin):
            _ledger_event("session", "wrong_pin")
            return False
        _SESSION_OPEN = True
        _LOCK_REASON = None
        with _LEDGER_LOCK:
            _SESSION_READS.clear()
        _ledger_event("session", "unlock")
        return True


def lock(reason: str = LOCK_MANUAL) -> None:
    """Close the session. Never raises: a Ledger that can't be written must not
    keep the session open, so the session closes first."""
    with _SESSION_LOCK:
        _lock_held(reason)


def _lock_held(reason: str) -> None:
    global _SESSION_OPEN, _LOCK_REASON
    was_open = _SESSION_OPEN
    _SESSION_OPEN = False
    with _LEDGER_LOCK:
        _SESSION_READS.clear()
    if was_open:
        _LOCK_REASON = reason
        _ledger_event("session", "lock", reason)


def is_unlocked() -> bool:
    """True while a session is open."""
    with _SESSION_LOCK:
        return _SESSION_OPEN


def lock_reason() -> str | None:
    """Why the last open session closed (LOCK_*), or None."""
    with _SESSION_LOCK:
        return _LOCK_REASON


# ─────────────────────────────────────────────────────────────────────────────
#  DPAPI — the only cipher in the toolbox
# ─────────────────────────────────────────────────────────────────────────────

def _entropy(name: str) -> bytes:
    return _ENTROPY_LABEL + name.encode("utf-8")


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    for _fn in (_crypt32.CryptProtectData, _crypt32.CryptUnprotectData):
        _fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p,
                        ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                        wintypes.DWORD, ctypes.POINTER(_Blob)]
        _fn.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

    def _dpapi(protect: bool, data: bytes, entropy: bytes) -> bytes:
        """CryptProtectData / CryptUnprotectData. Raises OSError on any failure:
        a wrong entropy, a changed byte and a blob from another account all
        come back as WinError 13 (measured on Obelisk, 2026-09-30)."""
        data_buf = ctypes.create_string_buffer(data, len(data))
        ent_buf = ctypes.create_string_buffer(entropy, len(entropy))
        blob_in = _Blob(len(data), ctypes.cast(data_buf, ctypes.POINTER(ctypes.c_char)))
        blob_ent = _Blob(len(entropy), ctypes.cast(ent_buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = _Blob()
        call = _crypt32.CryptProtectData if protect else _crypt32.CryptUnprotectData
        ok = call(ctypes.byref(blob_in), None, ctypes.byref(blob_ent), None, None,
                  _DPAPI_FLAGS, ctypes.byref(blob_out))
        ctypes.memset(data_buf, 0, len(data))   # best effort: our copy of the input
        if not ok:
            raise OSError(ctypes.get_last_error(), "DPAPI call failed")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            if not protect:                     # best effort: Windows's plaintext copy
                ctypes.memset(blob_out.pbData, 0, blob_out.cbData)
            _kernel32.LocalFree(blob_out.pbData)

else:

    def _dpapi(protect: bool, data: bytes, entropy: bytes) -> bytes:
        raise VaultError(ERR_DPAPI_UNAVAILABLE, "DPAPI exists only on Windows")


def _seal(name: str, value: str) -> dict[str, str]:
    try:
        blob = _dpapi(True, value.encode("utf-8"), _entropy(name))
    except OSError as e:
        raise VaultError(ERR_PROTECT_FAILED, type(e).__name__) from e
    return {"blob": base64.b64encode(blob).decode("ascii")}


def _open_entry(name: str, entry: Any) -> str:
    """Decrypt one entry under its own name. Raises VaultError(ERR_UNREADABLE)
    for a malformed entry, a wrong name, tampering or another account's blob."""
    try:
        if not isinstance(entry, dict) or not isinstance(entry.get("blob"), str):
            raise ValueError("entry has no blob")
        blob = base64.b64decode(entry["blob"], validate=True)
        return _dpapi(False, blob, _entropy(name)).decode("utf-8")
    except (OSError, ValueError) as e:      # binascii.Error, UnicodeDecodeError too
        raise VaultError(ERR_UNREADABLE, name) from e


# ─────────────────────────────────────────────────────────────────────────────
#  Persistence — one atomic JSON writer for every file Cerberus owns (§9)
# ─────────────────────────────────────────────────────────────────────────────

def _atomic_write_json(path: "str | os.PathLike[str]", payload: Any) -> None:
    """A uniquely named temp file in the same directory, flushed to disk, then
    os.replace: a crash can't truncate the file, and a reader sees the whole
    old file or the whole new one. Unique names so a CLI writing beside the
    running app never collides on the temp path."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".cerberus-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ─────────────────────────────────────────────────────────────────────────────
#  Vault — {"format", "version": 2, "entries": {name: {"blob": base64}}}
# ─────────────────────────────────────────────────────────────────────────────

def _vault_read() -> tuple[str, dict[str, Any]]:
    """(state, entries). state is missing | v2 | v1 | corrupt | io_error, and
    entries is empty for anything but v2."""
    if not os.path.exists(_VAULT_PATH):
        return "missing", {}
    try:
        with open(_VAULT_PATH, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return "io_error", {}
    try:
        data = json.loads(raw)
    except ValueError:
        return "corrupt", {}
    if not isinstance(data, dict):
        return "corrupt", {}
    if data.get("format") == _VAULT_FORMAT and data.get("version") == _VAULT_VERSION:
        entries = data.get("entries")
        return ("v2", entries) if isinstance(entries, dict) else ("corrupt", {})
    if "kdf_salt" in data:
        return "v1", {}
    return "corrupt", {}


def _vault_write(entries: dict[str, Any]) -> None:
    _atomic_write_json(_VAULT_PATH, {"format": _VAULT_FORMAT,
                                     "version": _VAULT_VERSION,
                                     "entries": entries})


def _note_vault_state(state: str) -> None:
    """Raise or clear the file-level alarms. Caller holds _SESSION_LOCK."""
    if state == "v1":
        _raise_alarm(ALARM_V1_VAULT, ("vault", "v1_found", "cerberus_vault.json"),
                     "cerberus_vault.json is an old PIN-encrypted (v1) vault and is "
                     "refused; retire it in the Cerberus tab, then re-enter the keys")
    else:
        _ALARMS.discard(ALARM_V1_VAULT)
    if state == "corrupt":
        _raise_alarm(ALARM_CORRUPT, ("vault", "corrupt", "cerberus_vault.json"),
                     "cerberus_vault.json can't be parsed and is refused; set it "
                     "aside in the Cerberus tab, then re-enter the keys")
    else:
        _ALARMS.discard(ALARM_CORRUPT)


def _require_usable(state: str) -> None:
    if state == "v1":
        raise VaultError(ERR_V1_VAULT)
    if state == "corrupt":
        raise VaultError(ERR_CORRUPT)
    if state == "io_error":
        raise VaultError(ERR_IO)


def _unreadable(name: str) -> None:
    _raise_alarm(ALARM_UNREADABLE + name, ("vault", "unreadable", name),
                 f"vault entry {name!r} can't be decrypted on this Windows "
                 f"account; re-enter it")


def vault_names() -> list[str]:
    """Key names in the vault, never values. Empty unless it's a v2 vault."""
    with _SESSION_LOCK:
        return sorted(_vault_read()[1])


def vault_status() -> str:
    """missing | v2 | v1 | corrupt | io_error."""
    with _SESSION_LOCK:
        return _vault_read()[0]


def vault_set(name: str, value: str) -> None:
    """Encrypt `value` under `name` and store it. Session required. Overwrites
    an existing name (that's how REPLACE works) and is always its own Ledger
    row; the value is never logged."""
    name = name.strip() if isinstance(name, str) else ""
    if not name or not isinstance(value, str) or not value:
        raise VaultError(ERR_BAD_INPUT)
    with _SESSION_LOCK:
        if not _SESSION_OPEN:
            raise VaultError(ERR_LOCKED)
        state, entries = _vault_read()
        _note_vault_state(state)
        _require_usable(state)
        entries = dict(entries)
        entries[name] = _seal(name, value)
        try:
            _vault_write(entries)
        except OSError as e:
            raise VaultError(ERR_WRITE_FAILED, type(e).__name__) from e
        _ALARMS.discard(ALARM_UNREADABLE + name)
        log.info("cerberus vault: stored key %r", name)
        _ledger_add_write(name)


def vault_get(name: str) -> str:
    """Decrypt and return the key stored under `name`. Session required; the
    lock is held across the decrypt, so once lock() returns this can't
    succeed. A read is a Ledger row, deduped per key per session."""
    with _SESSION_LOCK:
        if not _SESSION_OPEN:
            raise VaultError(ERR_LOCKED)
        state, entries = _vault_read()
        _note_vault_state(state)
        _require_usable(state)
        if name not in entries:
            raise VaultError(ERR_NO_ENTRY, name)
        try:
            value = _open_entry(name, entries[name])
        except VaultError as e:
            if e.slug == ERR_UNREADABLE:
                _unreadable(name)
            raise
        _ALARMS.discard(ALARM_UNREADABLE + name)
        _ledger_add_read(name)
        return value


def vault_health() -> dict[str, str]:
    """name -> "ok" | "unreadable" for every entry. Decrypts and discards; never
    returns a value. Needs no session (see the module docstring), and raises
    the same alarms a read would, so a bad key shows before any tool trips."""
    with _SESSION_LOCK:
        state, entries = _vault_read()
        _note_vault_state(state)
        out: dict[str, str] = {}
        for name in sorted(entries):
            try:
                _open_entry(name, entries[name])
            except VaultError as e:
                if e.slug != ERR_UNREADABLE:
                    raise
                _unreadable(name)
                out[name] = "unreadable"
            else:
                _ALARMS.discard(ALARM_UNREADABLE + name)
                out[name] = "ok"
        return out


def retire_vault() -> str:
    """Rename a v1 or unparseable vault file aside so a fresh v2 vault can
    start; returns the new file name. Session required. Never deletes and never
    overwrites: deleting the .old file is the owner's step."""
    with _SESSION_LOCK:
        if not _SESSION_OPEN:
            raise VaultError(ERR_LOCKED)
        state, _ = _vault_read()
        if state not in ("v1", "corrupt"):
            raise VaultError(ERR_NOTHING_TO_RETIRE)
        src = Path(_VAULT_PATH)
        kind = "v1" if state == "v1" else "unreadable"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = src.with_name(f"{src.stem}.{kind}-{stamp}.old")
        try:
            if dst.exists():
                raise FileExistsError(dst.name)
            os.rename(src, dst)
        except OSError as e:
            raise VaultError(ERR_RETIRE_FAILED, type(e).__name__) from e
        _note_vault_state("missing")
        log.warning("cerberus: retired the vault file as %s", dst.name)
        _ledger_event("vault", "retired", dst.name)
        return dst.name


# ─────────────────────────────────────────────────────────────────────────────
#  Alarms
# ─────────────────────────────────────────────────────────────────────────────

def _raise_alarm(alarm: str, row: tuple[str, str, str] | None, message: str) -> None:
    """Raise `alarm` once: an ERROR log line (Emanon shows it) and, if `row` is
    given, a Ledger row. Already raised: nothing. Caller holds _SESSION_LOCK."""
    if alarm in _ALARMS:
        return
    _ALARMS.add(alarm)
    log.error("cerberus: %s", message)
    if row is not None:
        _ledger_event(*row)


def _refresh_alarms() -> None:
    """Re-derive the file-level alarms. Caller holds _SESSION_LOCK."""
    _note_vault_state(_vault_read()[0])
    try:
        marked = bool(_load_verify()[2].get("reset_at"))
    except HashFileError:
        marked = False
    if marked:
        # Already written to the Ledger when it happened; after a restart it
        # only comes back up, without a second row.
        _raise_alarm(ALARM_PIN_RESET, None,
                     "the PIN file was recreated while the vault held keys "
                     "(not yet dismissed)")
    else:
        _ALARMS.discard(ALARM_PIN_RESET)


def alarms() -> list[str]:
    """The alarms that are up now, sorted."""
    with _SESSION_LOCK:
        return sorted(_ALARMS)


def dismiss_alarm(alarm: str) -> None:
    """Clear the pin_reset alarm (the others clear when their cause is fixed:
    retire the file, or re-enter the key). Session required; a Ledger row."""
    if alarm != ALARM_PIN_RESET:
        raise ValueError(f"only {ALARM_PIN_RESET} is dismissed by hand")
    with _SESSION_LOCK:
        if not _SESSION_OPEN:
            raise VaultError(ERR_LOCKED)
        _, _, data = _load_verify()
        if data.pop("reset_at", None) is not None:
            _atomic_write_json(_DATA_PATH, data)
        _ALARMS.discard(ALARM_PIN_RESET)
        _ledger_event("session", "dismissed", ALARM_PIN_RESET)


# ─────────────────────────────────────────────────────────────────────────────
#  fetch — Cerberus's Kairos worker: lock when the person steps away
# ─────────────────────────────────────────────────────────────────────────────

def away_reason(now: hypnos.Presence | None, before: hypnos.Presence | None,
                gap_s: float | None) -> str | None:
    """Why the session should close, or None. Pure, so the tests pin it.
    `before` is the previous check's reading; `gap_s` the seconds since it."""
    if now is not None:
        if now.windows_locked:
            return LOCK_WINDOWS
        if before is not None and now.asleep_s - before.asleep_s > _SLEEP_LOCK_S:
            return LOCK_SLEEP
    # A 2 s worker that hasn't run for a minute: the PC slept (or the app was
    # frozen). A second signal for sleep, in case the asleep counter misses it.
    if gap_s is not None and gap_s > _GAP_LOCK_S:
        return LOCK_SLEEP
    if now is not None and now.idle_s >= IDLE_LOCK_S:
        return LOCK_IDLE
    return None


def _wall_clock() -> float:
    """Wall-clock seconds. The gap check uses the wall clock because it keeps
    counting while the PC sleeps. A seam, so tests can move it without moving
    the clock logging reads."""
    return time.time()


def fetch() -> dict[str, Any]:
    """Read presence, lock if the person stepped away, and report the session:
    {"unlocked": bool, "reason": LOCK_* | None, "alarms": [...]}. Runs on a
    Kairos worker thread, so the lock lands before any panel redraws."""
    global _LAST_PRESENCE, _LAST_CHECK, _PRESENCE_FAILED
    presence: hypnos.Presence | None
    try:
        presence = hypnos.presence()
        _PRESENCE_FAILED = False
    except (hypnos.HypnosError, OSError) as e:
        presence = None
        if not _PRESENCE_FAILED:
            log.error("cerberus: can't read presence (%s); the idle, Windows-lock "
                      "and sleep locks are off until it recovers", type(e).__name__)
            _PRESENCE_FAILED = True
    now = _wall_clock()
    with _SESSION_LOCK:
        gap = now - _LAST_CHECK if _LAST_CHECK is not None else None
        reason = away_reason(presence, _LAST_PRESENCE, gap)
        if presence is not None:
            _LAST_PRESENCE = presence
        _LAST_CHECK = now
        if reason is not None and _SESSION_OPEN:
            _lock_held(reason)
        _refresh_alarms()
        return {"unlocked": _SESSION_OPEN, "reason": _LOCK_REASON,
                "alarms": sorted(_ALARMS)}


# ─────────────────────────────────────────────────────────────────────────────
#  Ledger — access log. Names and events only, never a value or a PIN.
#  Vault reads dedupe to one row per key per session; everything else is a row.
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ledger_load() -> list[dict[str, Any]]:
    """The ledger rows, or [] if missing/corrupt (logged)."""
    if not os.path.exists(_LEDGER_PATH):
        return []
    try:
        with open(_LEDGER_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError) as e:
        log.error("cerberus_ledger.json unreadable, treating as empty: %s",
                  type(e).__name__)
        return []


def _ledger_write(entries: list[dict[str, Any]]) -> None:
    _atomic_write_json(_LEDGER_PATH, entries)


def _ledger_failed(e: OSError) -> None:
    log.error("cerberus: couldn't write the Ledger (%s)", type(e).__name__)


def _ledger_event(head: str, action: str, target: str = "") -> None:
    """Append one row. Never raises: the log is best effort, and a full disk
    must not keep a session open or block a lock."""
    try:
        with _LEDGER_LOCK:
            entries = _ledger_load()
            entries.append({"id": secrets.token_hex(8), "ts": _now(), "head": head,
                            "action": action, "target": target, "count": 1})
            _ledger_write(entries)
    except OSError as e:
        _ledger_failed(e)


def _ledger_add_read(name: str) -> None:
    """A vault read, deduped per key per session: the first read appends a row,
    repeats bump its count. Under _LEDGER_LOCK, check-then-set included, so
    Midas's racing threads neither split the row nor lose a count."""
    try:
        with _LEDGER_LOCK:
            entries = _ledger_load()
            eid = _SESSION_READS.get(name)
            if eid is not None:
                for e in entries:
                    if e.get("id") == eid:
                        e["count"] = e.get("count", 1) + 1
                        e["ts"] = _now()
                        _ledger_write(entries)
                        return
            eid = secrets.token_hex(8)
            entries.append({"id": eid, "ts": _now(), "head": "vault",
                            "action": "read", "target": name, "count": 1})
            _SESSION_READS[name] = eid
            _ledger_write(entries)
    except OSError as e:
        _ledger_failed(e)


def _ledger_add_write(name: str) -> None:
    """A vault write. Never deduped: each one is a distinct, deliberate event."""
    _ledger_event("vault", "write", name)


def ledger_log_custody(filename: str, action: str = "open") -> None:
    """A Custody event, at full fidelity (no dedupe)."""
    _ledger_event("custody", action, filename)


def ledger_entries() -> list[dict[str, Any]]:
    """All ledger rows, newest first, for the panel."""
    return list(reversed(_ledger_load()))


# ─────────────────────────────────────────────────────────────────────────────
#  Custody — a hand-authored WHITELIST of config files, opened only inside
#  config/. File shape: {"config_dir": "config", "entries": [{"file","desc"}]}.
#  Nothing is discovered from disk; to surface a config, add an entry.
# ─────────────────────────────────────────────────────────────────────────────

def _manifest_load() -> dict[str, Any]:
    """The manifest, or {} if missing/corrupt (logged)."""
    if not os.path.exists(_MANIFEST_PATH):
        log.warning("cerberus_manifest.json missing — Custody list will be empty")
        return {}
    try:
        with open(_MANIFEST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as e:
        log.error("cerberus_manifest.json unreadable, treating as empty: %s",
                  type(e).__name__)
        return {}


def _config_root() -> Path:
    return Path(_CONFIG_DIR).resolve()


def manifest_config_dir() -> str | None:
    """The manifest's config folder, resolved — or None if it's unset or points
    outside config/. The manifest is itself editable through Custody, so its
    own claim about where configs live is checked, not trusted."""
    raw = _manifest_load().get("config_dir")
    if not raw or not isinstance(raw, str):
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = _APP_ROOT / p
    resolved = p.resolve()
    if not resolved.is_relative_to(_config_root()):
        log.warning("cerberus: the manifest's config_dir is outside config/; "
                    "Custody refuses it")
        return None
    return str(resolved)


def _listed_files(m: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for entry in m.get("entries", []):
        fname = entry.get("file") if isinstance(entry, dict) else None
        if isinstance(fname, str) and fname and fname not in out:
            out.append(fname)
    return out


def _custody_path(fname: str) -> str:
    """The resolved path Custody may open for `fname`, or CustodyError: it must
    be listed, and resolve (links followed) to an existing .json file inside
    config/. `startfile` RUNS an .exe, .bat or .lnk, so nothing else passes."""
    if fname not in _listed_files(_manifest_load()):
        raise CustodyError(ERR_NOT_LISTED, fname)
    cfg = manifest_config_dir()
    if cfg is None:
        raise CustodyError(ERR_NO_CONFIG_DIR)
    resolved = (Path(cfg) / fname).resolve()
    if not resolved.is_relative_to(_config_root()):
        raise CustodyError(ERR_OUTSIDE_CONFIG, fname)
    if resolved.suffix.lower() != ".json":
        raise CustodyError(ERR_NOT_JSON, fname)
    if not resolved.is_file():
        raise CustodyError(ERR_MISSING, fname)
    return str(resolved)


def manifest_configs() -> list[dict[str, Any]]:
    """Custody rows, in manifest order: {file, desc, exists, refused}.
    `refused` is a custody_* slug when the entry could never be opened
    (outside config/, not .json), else None. A missing file still shows."""
    m = _manifest_load()
    descs = {e.get("file"): e.get("desc") for e in m.get("entries", [])
             if isinstance(e, dict)}
    rows: list[dict[str, Any]] = []
    for fname in _listed_files(m):
        exists, refused = True, None
        try:
            _custody_path(fname)
        except CustodyError as e:
            exists = False
            if e.slug != ERR_MISSING:
                refused = e.slug
        rows.append({"file": fname,
                     "desc": descs.get(fname) or "(no description)",
                     "exists": exists, "refused": refused})
    return rows


def custody_open(fname: str) -> str:
    """The path to hand the OS editor for `fname`, after a Ledger 'open' row.
    Session required. Refusals raise CustodyError and are Ledger rows too."""
    with _SESSION_LOCK:
        if not _SESSION_OPEN:
            raise CustodyError(ERR_LOCKED)
    try:
        path = _custody_path(fname)
    except CustodyError as e:
        log.warning("cerberus: Custody refused %r (%s)", fname, e.slug)
        _ledger_event("custody", "refused", fname)
        raise
    ledger_log_custody(fname, "open")
    return path


# ─────────────────────────────────────────────────────────────────────────────
#  CLI — secrets only through hidden prompts
# ─────────────────────────────────────────────────────────────────────────────

_USAGE = """\
usage (from metis_toolbox/, in PowerShell):
  python cerberus.py status          what's set up; whether each key reads back
  python cerberus.py set NAME        store a key: asks for the PIN, then the value
  python cerberus.py setpin          set or change the PIN (no key is touched)"""

_REFUSED = """\
Refused: Cerberus never takes a key or a PIN on the command line, because your
shell keeps every command in its history file. Run `python cerberus.py set NAME`
(or `setpin`) and type or paste at the hidden prompt instead.
If what you typed was a real key, it is in your shell history now: regenerate
it at the provider and remove that line from the history file."""

_STATUS_TEXT = {
    "missing": "no vault yet (it's created by the first key)",
    "v2": "format 2 (Windows DPAPI, this account)",
    "v1": "OLD FORMAT (PIN-encrypted, v1): refused — retire it in the CERBERUS tab",
    "corrupt": "UNREADABLE file: refused — set it aside in the CERBERUS tab",
    "io_error": "can't be read right now (file in use or permissions)",
}


def _cli_status() -> int:
    try:
        preflight()
        print("PIN:     set")
    except HashFileError:
        print("PIN:     not set — run `python cerberus.py setpin`")
    state = vault_status()
    print(f"vault:   {_STATUS_TEXT.get(state, state)}")
    if state in ("v2", "missing"):
        try:
            health = vault_health()
        except VaultError as e:
            print(f"         can't check the keys: {e.slug}")
            health = {}
        for name in list(KNOWN_KEYS) + [n for n in health if n not in KNOWN_KEYS]:
            print(f"  {name:<24} {health.get(name, 'not set')}")
    print(f"alarms:  {', '.join(alarms()) or 'none'}")
    print(f"ledger:  {len(_ledger_load())} entries")
    print(f"custody: {len(manifest_configs())} config files listed")
    return 0


def _cli_set(name: str) -> int:
    try:
        ok = unlock(getpass.getpass("Cerberus PIN: "))
    except HashFileError:
        print("No PIN is set yet — run `python cerberus.py setpin` first.")
        return 1
    if not ok:
        print("Wrong PIN.")
        return 1
    try:
        value = getpass.getpass(f"value for {name} (hidden): ").strip()
        if not value:
            print("Empty value — nothing stored.")
            return 1
        vault_set(name, value)
        print(f"stored {name!r}.")
        return 0
    except VaultError as e:
        print(f"not stored: {e.slug}")
        return 1
    finally:
        lock(LOCK_EXIT)


def _cli_setpin() -> int:
    try:
        preflight()
        has_pin = True
    except HashFileError:
        has_pin = False
    if has_pin and not verify(getpass.getpass("current PIN: ")):
        print("Wrong PIN — nothing changed.")
        return 1
    new = getpass.getpass("new PIN: ")
    again = getpass.getpass("new PIN again: ")
    if not new.strip():
        print("Empty PIN — nothing changed.")
        return 1
    if new != again:
        print("The two PINs don't match — nothing changed.")
        return 1
    set_pin(new)
    print("PIN set. No key was touched: the PIN decrypts nothing.")
    if ALARM_PIN_RESET in alarms():
        print("The vault already held keys, so this was logged as a PIN reset.")
    return 0


def main(argv: list[str]) -> int:
    """The CLI. Returns the exit code: 0 done, 1 failed, 2 refused or usage."""
    cmd, rest = (argv[0], argv[1:]) if argv else ("status", [])
    if cmd == "status" and not rest:
        return _cli_status()
    if cmd == "set" and len(rest) == 1:
        return _cli_set(rest[0])
    if cmd == "setpin" and not rest:
        return _cli_setpin()
    if cmd in ("set", "setpin"):
        print(_REFUSED)
        return 2
    if cmd == "get":
        print("`get` is gone: it printed a key into the terminal. `status` says "
              "whether each key reads back; the CERBERUS tab can reveal one.")
        return 2
    print(_USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
