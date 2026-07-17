"""
cerberus.py — The Guardian of Secrets (logic)
=============================================
Metis Toolbox | Anti-Legion: THREE HEADS, ONE GUARDIAN

Job:         Guard the toolbox's secrets behind one PIN — the sole encryption
             authority. One PIN gates three jobs: at-rest encryption of API
             keys/secrets (Vault), a manifest
             of PIN-gated config files (Custody), and an access ledger
             (Ledger). Every subsystem that stores or reads a secret defers
             here; nothing else in the toolbox rolls its own crypto.

             This is the one deliberate exception to §0's "one file, one job":
             the three heads are facets of a single identity — the guardian —
             and the brief (Cerberus handoff) mandates one independently
             unit-testable core rather than three modules sharing a PIN and a
             key. Recorded in CONVENTIONS §12.

Threat model: same class as Sphynx — protects against ACCIDENTAL EXPOSURE and
             UNAUTHORIZED LOCAL ACCESS (kids at the keyboard, casual snooping,
             screen-share slips). NOT a defense against a determined attacker
             with tools and time. So: no OS-keychain, no HSM, and the PIN gate
             stays as lightweight as Sphynx's (RAM-only attempt counter, no
             lockout persistence). Only the Vault's encryption key is
             stretched (PBKDF2), because that runs once per unlock, not per
             read.

Crypto:      Stdlib only — no `cryptography` dependency (§11 bars new deps
             casually, and the threat model doesn't justify one). Python's
             stdlib ships PBKDF2 but no AES, so the at-rest cipher is built
             from HMAC-SHA256: PBKDF2 derives 64 bytes of key material (split
             into a 32-byte encryption key + 32-byte MAC key), encryption is
             a HMAC-SHA256 counter-mode keystream XORed with the plaintext,
             and integrity is encrypt-then-MAC (HMAC over nonce||ciphertext,
             verified with a constant-time compare). Adequate for "keep honest
             people honest"; not marketed as more.

PIN paths:   TWO independent salts, per R3 —
               • cerberus_data.json  — verify-salt + a lightweight salted
                 SHA-256 of the PIN. This only gates access; kept cheap.
               • cerberus_vault.json — kdf-salt used by PBKDF2 to derive the
                 encryption key. Different salt, different operation.
             Losing the PIN means losing the Vault contents (accepted: the
             initial contents are re-issuable API keys).

Session:     unlock(pin) verifies, then caches the PIN-derived key in RAM for
             the session so PBKDF2 runs once, not per read. lock() clears it.
             Vault reads are deduped to one ledger entry per key per unlock
             session (with a read-count); a fresh unlock starts a fresh entry.
             Vault WRITES are never deduped — each vault_set() call logs its
             own Ledger entry (name + action="write", never the value), since
             a write is a distinct, deliberate event rather than a repeated
             glance at the same secret.

Contract:    preflight()              — validate cerberus_data.json loads.
             verify(pin) -> bool      — Sphynx-pattern salted-hash check +
                                         RAM-only attempt decrement.
             attempts_left() -> int
             unlock(pin) / lock() / is_unlocked()
             vault_set(name, value)   — encrypt + store (the explicit setup
                                         step; creates the kdf-salt on first
                                         use). Requires unlock. Silent
                                         overwrite of an existing name;
                                         ledgers a (never-deduped) write.
             vault_get(name) -> str   — decrypt + ledger a (deduped) read.
             vault_names() -> list    — key names only, never values.
             manifest_configs()       — Custody rows (file/desc/exists).
             manifest_config_dir()    — resolved config folder path or None.
             ledger_log_custody(file, action)
             ledger_entries()         — newest-first, for display.

State:       _ATTEMPTS_REMAINING, _SESSION_KEY, _SESSION_READS are module-level
             and RAM-only — reset every process start. Nothing about attempts
             or the session key is ever written to disk.

Upstream:    panels/cerberus_panel.py, panels/midas_panel.py (PIN gate),
             future consumers (Sibyl).
Downstream:  cerberus_data.json (PIN hash, committed), cerberus_vault.json
             (encrypted blob, gitignored), cerberus_manifest.json (config
             list, committed), cerberus_ledger.json (access log, gitignored).

Requires:    hashlib, hmac, secrets, json, os, logging, pathlib, datetime
             (stdlib only). No tkinter, no subprocess — stays independently
             unit-testable and UI-free, like sphynx.py.
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("METIS.cerberus")

# Anchored to __file__ per §1 — cerberus.py sits AT the app root, so the same
# rule scribe.py / metis_logging.py use, not the tools/ two-dirname dance.
_APP_ROOT     = Path(__file__).resolve().parent
_DATA_PATH    = _APP_ROOT / "cerberus_data.json"      # committed: PIN hash
_VAULT_PATH   = _APP_ROOT / "cerberus_vault.json"     # gitignored: ciphertext
_MANIFEST_PATH = _APP_ROOT / "config" / "cerberus_manifest.json"  # committed: config list
_LEDGER_PATH  = _APP_ROOT / "cerberus_ledger.json"    # gitignored: access log

# PBKDF2 iterations for the Vault key. Modern default; runs once per unlock,
# not per read, so the cost is paid at the gate and never in the hot path.
_KDF_ITERS = 600_000

_ATTEMPTS_REMAINING = 3   # resets every process start — soft-lock is impossible

# ── RAM-only session state (never persisted) ─────────────────────────────────
_SESSION_KEY: bytes | None = None          # 64 B PBKDF2 material, set on unlock
_SESSION_READS: dict[str, str] = {}        # key name -> this-session ledger id


class HashFileError(Exception):
    """cerberus_data.json is missing, unreadable, or lacks its fields."""


class VaultError(Exception):
    """Vault is locked, missing, corrupt, or a ciphertext failed integrity."""


# ─────────────────────────────────────────────────────────────────────────────
#  PIN verification — the Sphynx pattern, plus a salt (R3)
#  Kept lightweight on purpose: a salted SHA-256, NOT the Vault's stretched
#  KDF. Verifying a PIN and deriving an encryption key are separate operations
#  with separate salts.
# ─────────────────────────────────────────────────────────────────────────────

def _load_verify() -> tuple[bytes, str]:
    """Return (verify_salt_bytes, stored_pin_hash) from cerberus_data.json."""
    try:
        with open(_DATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return bytes.fromhex(data["verify_salt"]), data["pin_hash"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        raise HashFileError(f"cerberus_data.json missing or malformed: {e}") from e


def _hash_pin(pin: str, salt: bytes) -> str:
    """Lightweight salted SHA-256 of the PIN — the access gate, not the KDF."""
    return hashlib.sha256(salt + pin.strip().encode("utf-8")).hexdigest()


def preflight() -> None:
    """Validate the PIN-hash file loads, without checking a PIN. Lets the panel
    fail closed before it draws its gate. Raises HashFileError if broken."""
    _load_verify()


def verify(pin: str) -> bool:
    """Check pin against the stored salted hash. Decrements the remaining-
    attempts counter on a miss; once exhausted, always returns False (even for
    a correct pin) without decrementing further. Raises HashFileError if the
    hash file can't be loaded. Mirrors sphynx.verify's contract exactly."""
    global _ATTEMPTS_REMAINING
    salt, expected = _load_verify()
    if _ATTEMPTS_REMAINING <= 0:
        return False
    if hmac.compare_digest(_hash_pin(pin, salt), expected):
        return True
    _ATTEMPTS_REMAINING -= 1
    return False


def attempts_left() -> int:
    """Read-only peek for the UI to render 'x attempts remaining'. Floors at 0."""
    return max(0, _ATTEMPTS_REMAINING)


# ─────────────────────────────────────────────────────────────────────────────
#  Session — unlock caches the derived key so PBKDF2 runs once, not per read
# ─────────────────────────────────────────────────────────────────────────────

def unlock(pin: str) -> bool:
    """Verify the PIN and, on success, open a session: cache the encryption key
    (if the Vault has been initialized) and reset the read-dedupe map. Returns
    the verify() result; a wrong PIN still burns an attempt via verify()."""
    global _SESSION_KEY, _SESSION_READS
    if not verify(pin):
        return False
    _SESSION_READS = {}
    salt = _vault_salt()
    _SESSION_KEY = _derive_key(pin, salt) if salt is not None else None
    # Vault not yet initialized: defer key derivation to the first vault_set,
    # which mints the salt. Stash the PIN so we can derive then.
    if _SESSION_KEY is None:
        _pending_pin_set(pin)
    return True


def lock() -> None:
    """Close the session: drop the cached key and per-session read state."""
    global _SESSION_KEY, _SESSION_READS
    _SESSION_KEY = None
    _SESSION_READS = {}
    _pending_pin_set(None)


def is_unlocked() -> bool:
    """True while a session is open (a correct PIN was accepted since lock())."""
    return _SESSION_KEY is not None or _pending_pin_get() is not None


# The pending PIN only lives between unlock() and the first vault_set() that
# mints the kdf-salt. It's RAM-only and equivalent in sensitivity to the
# session key it will become. Wrapped in accessors so it's easy to audit.
_PENDING_PIN: str | None = None


def _pending_pin_set(pin: str | None) -> None:
    global _PENDING_PIN
    _PENDING_PIN = pin


def _pending_pin_get() -> str | None:
    return _PENDING_PIN


# ─────────────────────────────────────────────────────────────────────────────
#  Crypto primitives — stdlib only (see the module docstring for the why)
# ─────────────────────────────────────────────────────────────────────────────

def _derive_key(pin: str, salt: bytes) -> bytes:
    """PBKDF2-HMAC-SHA256 -> 64 bytes: [:32] encryption key, [32:] MAC key."""
    return hashlib.pbkdf2_hmac(
        "sha256", pin.strip().encode("utf-8"), salt, _KDF_ITERS, dklen=64
    )


def _keystream(enc_key: bytes, nonce: bytes, n: int) -> bytes:
    """HMAC-SHA256 counter-mode keystream of n bytes. block_i = HMAC(enc_key,
    nonce || counter_i); this is a standard PRF-CTR construction."""
    out = bytearray()
    counter = 0
    while len(out) < n:
        out.extend(hmac.new(enc_key, nonce + counter.to_bytes(8, "big"),
                            hashlib.sha256).digest())
        counter += 1
    return bytes(out[:n])


def _encrypt(key: bytes, plaintext: str) -> dict[str, str]:
    """Encrypt-then-MAC. Fresh random nonce per call so equal plaintexts under
    the same key still differ. Returns hex fields for JSON storage."""
    enc_key, mac_key = key[:32], key[32:]
    nonce = secrets.token_bytes(16)
    data = plaintext.encode("utf-8")
    ct = bytes(a ^ b for a, b in zip(data, _keystream(enc_key, nonce, len(data))))
    mac = hmac.new(mac_key, nonce + ct, hashlib.sha256).hexdigest()
    return {"nonce": nonce.hex(), "ct": ct.hex(), "mac": mac}


def _decrypt(key: bytes, blob: dict[str, str]) -> str:
    """Verify the MAC (constant-time) before decrypting. Raises VaultError on a
    tampered/wrong-key ciphertext rather than returning garbage."""
    enc_key, mac_key = key[:32], key[32:]
    try:
        nonce = bytes.fromhex(blob["nonce"])
        ct = bytes.fromhex(blob["ct"])
        stored_mac = blob["mac"]
    except (KeyError, TypeError, ValueError) as e:
        raise VaultError(f"vault entry malformed: {e}") from e
    expected = hmac.new(mac_key, nonce + ct, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, stored_mac):
        raise VaultError("ciphertext failed integrity check (wrong PIN or tampered)")
    plain = bytes(a ^ b for a, b in zip(ct, _keystream(enc_key, nonce, len(ct))))
    return plain.decode("utf-8")


# ─────────────────────────────────────────────────────────────────────────────
#  Vault — encrypted key/value store (R3)
#  File shape: {"kdf_salt": "<hex>", "entries": {"<name>": {nonce,ct,mac}}}
# ─────────────────────────────────────────────────────────────────────────────

def _vault_load() -> dict[str, Any]:
    """Load the vault, degrading to an empty structure on missing/corrupt —
    never raise from the read path (the panel shows an empty vault, not a
    crash). A corrupt file is logged, not silently reshaped."""
    if not os.path.exists(_VAULT_PATH):
        return {"kdf_salt": None, "entries": {}}
    try:
        with open(_VAULT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("vault root is not an object")
        data.setdefault("kdf_salt", None)
        data.setdefault("entries", {})
        return data
    except (OSError, json.JSONDecodeError, ValueError) as e:
        log.error("cerberus_vault.json unreadable, treating as empty: %s", e)
        return {"kdf_salt": None, "entries": {}}


def _vault_write(vault: dict[str, Any]) -> None:
    with open(_VAULT_PATH, "w", encoding="utf-8") as f:
        json.dump(vault, f, indent=2)


def _vault_salt() -> bytes | None:
    """The kdf-salt bytes if the vault has been initialized, else None."""
    raw = _vault_load().get("kdf_salt")
    return bytes.fromhex(raw) if raw else None


def _require_key() -> bytes:
    """Return the session encryption key, deriving+caching it from a pending
    PIN if the vault was minted after unlock. Raises VaultError if locked."""
    global _SESSION_KEY
    if _SESSION_KEY is not None:
        return _SESSION_KEY
    pin = _pending_pin_get()
    salt = _vault_salt()
    if pin is not None and salt is not None:
        _SESSION_KEY = _derive_key(pin, salt)
        return _SESSION_KEY
    raise VaultError("vault is locked — call unlock(pin) first")


def vault_names() -> list[str]:
    """Key names only, never values — safe to render without unlocking."""
    return sorted(_vault_load().get("entries", {}).keys())


def vault_set(name: str, value: str) -> None:
    """Encrypt `value` under the session key and store it under `name`. This is
    the explicit setup step (R3): on the very first call it mints the kdf-salt.
    Requires an open session (unlock). Overwrites an existing name silently —
    matches this function's own contract, so callers (CLI, panel) don't need
    to add a confirmation layer on top. Records a Ledger write (name + action,
    never the value); unlike reads, writes are NOT deduped — each call is a
    distinct event."""
    if not is_unlocked():
        raise VaultError("vault is locked — call unlock(pin) first")
    vault = _vault_load()
    if not vault.get("kdf_salt"):
        vault["kdf_salt"] = secrets.token_bytes(16).hex()
        _vault_write(vault)          # persist the salt before deriving from it
    key = _require_key()
    vault = _vault_load()            # reload in case the salt write changed it
    vault["entries"][name] = _encrypt(key, value)
    _vault_write(vault)
    log.info("cerberus vault: stored key %r", name)
    _ledger_add_write(name)


def vault_get(name: str) -> str:
    """Decrypt and return the secret stored under `name`, recording a (deduped)
    Ledger read. Requires an open session. Raises VaultError if locked, the
    name is absent, or the ciphertext fails its integrity check."""
    key = _require_key()
    entries = _vault_load().get("entries", {})
    if name not in entries:
        raise VaultError(f"no vault entry named {name!r}")
    plaintext = _decrypt(key, entries[name])
    _ledger_add_read(name)
    return plaintext


# ─────────────────────────────────────────────────────────────────────────────
#  Ledger — access log (R2). Metadata only, never secret values → NOT encrypted.
#  Vault reads dedupe to one entry per key per session (read-count); Custody
#  opens/edits log at full fidelity.
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ledger_load() -> list[dict[str, Any]]:
    """Load the ledger array, degrading to [] on missing/corrupt (logged)."""
    if not os.path.exists(_LEDGER_PATH):
        return []
    try:
        with open(_LEDGER_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError) as e:
        log.error("cerberus_ledger.json unreadable, treating as empty: %s", e)
        return []


def _ledger_write(entries: list[dict[str, Any]]) -> None:
    with open(_LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def _ledger_add_read(name: str) -> None:
    """Record a Vault read, deduped per key per unlock session. The first read
    of a key this session appends a new entry; repeats bump its read-count on
    the same entry. A fresh unlock() clears _SESSION_READS, so the next session
    starts a new entry (R2)."""
    entries = _ledger_load()
    eid = _SESSION_READS.get(name)
    if eid is not None:
        for e in entries:
            if e.get("id") == eid:
                e["count"] = e.get("count", 1) + 1
                e["ts"] = _now()
                _ledger_write(entries)
                return
        # The entry vanished (file rewritten out from under us): fall through
        # and create a fresh one.
    eid = secrets.token_hex(8)
    entries.append({"id": eid, "ts": _now(), "head": "vault",
                    "action": "read", "target": name, "count": 1})
    _SESSION_READS[name] = eid
    _ledger_write(entries)


def _ledger_add_write(name: str) -> None:
    """Record a Vault write. Never deduped (unlike reads) — each vault_set call
    is a distinct event, whether it's a fresh key or a silent overwrite."""
    entries = _ledger_load()
    entries.append({"id": secrets.token_hex(8), "ts": _now(), "head": "vault",
                    "action": "write", "target": name, "count": 1})
    _ledger_write(entries)


def ledger_log_custody(filename: str, action: str = "open") -> None:
    """Record a Custody event at full fidelity — one entry per open/edit, no
    deduping. `action` is 'open' (we can log the handoff to the OS editor) or
    'edit' if a caller can distinguish the two."""
    entries = _ledger_load()
    entries.append({"id": secrets.token_hex(8), "ts": _now(), "head": "custody",
                    "action": action, "target": filename, "count": 1})
    _ledger_write(entries)


def ledger_entries() -> list[dict[str, Any]]:
    """All ledger rows, newest-first, for the panel to render."""
    return list(reversed(_ledger_load()))


# ─────────────────────────────────────────────────────────────────────────────
#  Custody manifest (R1) — a hand-authored WHITELIST of PIN-gated config files.
#  File shape: {"config_dir": "<path>", "entries": [{"file","desc"}, ...]}.
#  The manifest gates visibility: ONLY files it lists appear in Custody. Nothing
#  is auto-discovered from disk — Custody is the curated set of editable configs,
#  not a directory dump. To surface a new config, add an entry here.
# ─────────────────────────────────────────────────────────────────────────────

def _manifest_load() -> dict[str, Any]:
    """Load the manifest, degrading to {} on missing/corrupt (logged)."""
    if not os.path.exists(_MANIFEST_PATH):
        log.warning("cerberus_manifest.json missing — Custody list will be empty")
        return {}
    try:
        with open(_MANIFEST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        log.error("cerberus_manifest.json unreadable, treating as empty: %s", e)
        return {}


def manifest_config_dir() -> str | None:
    """The configured config folder, resolved to an absolute path (relative
    paths and '.' resolve against the app root). None if unset."""
    raw = _manifest_load().get("config_dir")
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = _APP_ROOT / p
    return str(p.resolve())


def manifest_configs() -> list[dict[str, Any]]:
    """Custody rows — the manifest whitelist, in manifest order. ONLY files the
    manifest lists appear; nothing is auto-discovered from disk. A listed file
    missing from disk still shows (exists=False). Each row: {file, desc, exists}."""
    m = _manifest_load()
    cfg_dir = manifest_config_dir()

    def _exists(fname: str) -> bool:
        return cfg_dir is not None and os.path.isfile(os.path.join(cfg_dir, fname))

    rows: dict[str, dict[str, Any]] = {}
    for entry in m.get("entries", []):
        fname = entry.get("file") if isinstance(entry, dict) else None
        if not fname:
            continue
        rows[fname] = {
            "file": fname,
            "desc": entry.get("desc") or "(no description)",
            "exists": _exists(fname),
        }

    return list(rows.values())


# ─────────────────────────────────────────────────────────────────────────────
#  Setup CLI — the "explicit setup step" for the committed PIN file and the
#  bootstrapped Vault. Every module has a __main__ (§3.7); here it doubles as
#  the tool the user runs once to set their PIN and seed secrets.
#
#    python cerberus.py setpin <PIN>          — (re)write cerberus_data.json
#    python cerberus.py set <PIN> <name> <v>  — store a secret in the Vault
#    python cerberus.py get <PIN> <name>      — decrypt one secret (prints it)
#    python cerberus.py status                — show gate/vault/ledger state
# ─────────────────────────────────────────────────────────────────────────────

def set_pin(pin: str) -> None:
    """Create or replace cerberus_data.json: a fresh verify-salt + the salted
    hash of the PIN. This is the one-time setup step, callable from the CLI
    (`setpin`) OR the Cerberus panel's first-run flow on a fresh clone. Atomic
    (tempfile in the same dir + os.replace) so a crash mid-write can't leave a
    half-file that then fails preflight forever."""
    salt = secrets.token_bytes(16)
    payload = {"verify_salt": salt.hex(), "pin_hash": _hash_pin(pin, salt)}
    directory = os.path.dirname(os.path.abspath(_DATA_PATH))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".cerberus-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, _DATA_PATH)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _cli_setpin(pin: str) -> None:
    set_pin(pin)
    print(f"cerberus_data.json written — PIN set (len {len(pin.strip())}).")


def _cli_status() -> None:
    try:
        preflight()
        print("gate:   cerberus_data.json OK")
    except HashFileError as e:
        print(f"gate:   NOT SET ({e})")
    v = _vault_load()
    inited = "yes" if v.get("kdf_salt") else "no (run 'set' to initialize)"
    print(f"vault:  initialized={inited}  keys={sorted(v.get('entries', {}))}")
    print(f"ledger: {len(_ledger_load())} entries")
    print(f"custody: {len(manifest_configs())} config files listed")


if __name__ == "__main__":
    import sys

    argv = sys.argv[1:]
    cmd = argv[0] if argv else "status"

    if cmd == "setpin" and len(argv) == 2:
        _cli_setpin(argv[1])
    elif cmd == "set" and len(argv) == 4:
        if unlock(argv[1]):
            vault_set(argv[2], argv[3])
            print(f"stored {argv[2]!r} in the vault.")
        else:
            print("wrong PIN.")
    elif cmd == "get" and len(argv) == 3:
        if unlock(argv[1]):
            print(vault_get(argv[2]))
        else:
            print("wrong PIN.")
    elif cmd == "status":
        _cli_status()
    else:
        print(__doc__.split("Contract:")[0].strip())
        print("\nUsage: setpin <pin> | set <pin> <name> <value> | "
              "get <pin> <name> | status")
