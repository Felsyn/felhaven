"""
test_cerberus.py — unit tests for cerberus.py (the Guardian of Secrets).

Like test_sphynx.py, the deterministic seam is the module's path constants:
each test points cerberus at temp files so the real cerberus_data.json /
cerberus_vault.json / ledger / manifest (and the real PIN and secrets) never
enter this file. Run from the package root:

    python -X utf8 -m unittest tests.test_cerberus

Covers Phase 1 acceptance: verify pass/fail, attempt exhaustion, encrypt/decrypt
round-trip + integrity, verify-salt vs KDF-salt separation, ledger per-session
dedupe, manifest missing-description fallback, and the degraded paths (missing/
corrupt vault, ledger, and manifest files).
"""

import hashlib
import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cerberus

_TEST_PIN  = "9137"
_TEST_SALT = b"sixteen_byte_slt"          # 16 bytes, fixed for determinism
_TEST_HASH = hashlib.sha256(_TEST_SALT + _TEST_PIN.encode("utf-8")).hexdigest()


class _Base(unittest.TestCase):
    """Redirect every cerberus path constant at a fresh temp dir, seed a known
    PIN file, and shrink the KDF cost so the round-trip tests stay fast (the
    iteration count isn't what we're testing — the construction is)."""

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._orig = {
            "_DATA_PATH":     cerberus._DATA_PATH,
            "_VAULT_PATH":    cerberus._VAULT_PATH,
            "_MANIFEST_PATH": cerberus._MANIFEST_PATH,
            "_LEDGER_PATH":   cerberus._LEDGER_PATH,
            "_KDF_ITERS":     cerberus._KDF_ITERS,
        }
        cerberus._DATA_PATH     = os.path.join(self._dir, "cerberus_data.json")
        cerberus._VAULT_PATH    = os.path.join(self._dir, "cerberus_vault.json")
        cerberus._MANIFEST_PATH = os.path.join(self._dir, "cerberus_manifest.json")
        cerberus._LEDGER_PATH   = os.path.join(self._dir, "cerberus_ledger.json")
        cerberus._KDF_ITERS     = 1000

        with open(cerberus._DATA_PATH, "w", encoding="utf-8") as f:
            json.dump({"verify_salt": _TEST_SALT.hex(), "pin_hash": _TEST_HASH}, f)

        cerberus._ATTEMPTS_REMAINING = 3
        cerberus.lock()               # clear any session state from prior tests

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(cerberus, k, v)
        cerberus._ATTEMPTS_REMAINING = 3
        cerberus.lock()
        shutil.rmtree(self._dir, ignore_errors=True)


# ── PIN verification (Sphynx-pattern contract) ───────────────────────────────

class TestVerify(_Base):

    def test_correct_pin(self):
        self.assertTrue(cerberus.verify(_TEST_PIN))
        self.assertEqual(cerberus.attempts_left(), 3)   # a hit never decrements

    def test_correct_pin_with_whitespace(self):
        self.assertTrue(cerberus.verify(f"  {_TEST_PIN}  \n"))

    def test_wrong_pin_decrements(self):
        self.assertFalse(cerberus.verify("0000"))
        self.assertEqual(cerberus.attempts_left(), 2)

    def test_exhaustion_floors_at_zero(self):
        for _ in range(5):
            cerberus.verify("0000")
        self.assertEqual(cerberus.attempts_left(), 0)

    def test_post_exhaustion_rejects_even_correct_pin(self):
        for _ in range(3):
            cerberus.verify("0000")
        self.assertFalse(cerberus.verify(_TEST_PIN))
        self.assertEqual(cerberus.attempts_left(), 0)

    def test_fresh_import_resets_counter(self):
        cerberus.verify("0000")
        self.assertEqual(cerberus.attempts_left(), 2)
        importlib.reload(cerberus)
        self.assertEqual(cerberus.attempts_left(), 3)

    def test_preflight_ok(self):
        cerberus.preflight()          # must not raise

    def test_missing_hash_file_raises(self):
        cerberus._DATA_PATH = os.path.join(self._dir, "does_not_exist.json")
        with self.assertRaises(cerberus.HashFileError):
            cerberus.verify(_TEST_PIN)
        with self.assertRaises(cerberus.HashFileError):
            cerberus.preflight()

    def test_malformed_hash_file_raises(self):
        with open(cerberus._DATA_PATH, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        with self.assertRaises(cerberus.HashFileError):
            cerberus.verify(_TEST_PIN)

    def test_hash_file_missing_key_raises(self):
        with open(cerberus._DATA_PATH, "w", encoding="utf-8") as f:
            json.dump({"verify_salt": _TEST_SALT.hex()}, f)   # no pin_hash
        with self.assertRaises(cerberus.HashFileError):
            cerberus.verify(_TEST_PIN)


# ── Session + Vault encrypt/decrypt round-trip ───────────────────────────────

class TestVault(_Base):

    def test_locked_by_default(self):
        self.assertFalse(cerberus.is_unlocked())
        with self.assertRaises(cerberus.VaultError):
            cerberus.vault_get("anything")
        with self.assertRaises(cerberus.VaultError):
            cerberus.vault_set("k", "v")

    def test_wrong_pin_does_not_unlock(self):
        self.assertFalse(cerberus.unlock("0000"))
        self.assertFalse(cerberus.is_unlocked())
        self.assertEqual(cerberus.attempts_left(), 2)

    def test_set_get_round_trip(self):
        self.assertTrue(cerberus.unlock(_TEST_PIN))
        cerberus.vault_set("openai_key", "sk-secret-123")
        self.assertEqual(cerberus.vault_get("openai_key"), "sk-secret-123")

    def test_names_never_leak_values(self):
        cerberus.unlock(_TEST_PIN)
        cerberus.vault_set("a", "AAA")
        cerberus.vault_set("b", "BBB")
        self.assertEqual(cerberus.vault_names(), ["a", "b"])
        # The raw file holds ciphertext, not the plaintext value.
        with open(cerberus._VAULT_PATH, "r", encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn("AAA", raw)
        self.assertNotIn("BBB", raw)

    def test_missing_name_raises(self):
        cerberus.unlock(_TEST_PIN)
        cerberus.vault_set("present", "x")
        with self.assertRaises(cerberus.VaultError):
            cerberus.vault_get("absent")

    def test_relock_denies_access(self):
        cerberus.unlock(_TEST_PIN)
        cerberus.vault_set("k", "v")
        cerberus.lock()
        self.assertFalse(cerberus.is_unlocked())
        with self.assertRaises(cerberus.VaultError):
            cerberus.vault_get("k")

    def test_equal_plaintexts_get_distinct_ciphertexts(self):
        """Fresh nonce per encrypt — two identical secrets must not produce
        identical stored blobs (no ECB-style leakage of equality)."""
        cerberus.unlock(_TEST_PIN)
        cerberus.vault_set("x", "same")
        cerberus.vault_set("y", "same")
        entries = cerberus._vault_load()["entries"]
        self.assertNotEqual(entries["x"]["ct"], entries["y"]["ct"])
        self.assertEqual(cerberus.vault_get("x"), cerberus.vault_get("y"))

    def test_tampered_ciphertext_fails_integrity(self):
        cerberus.unlock(_TEST_PIN)
        cerberus.vault_set("k", "value")
        vault = cerberus._vault_load()
        ct = bytearray(bytes.fromhex(vault["entries"]["k"]["ct"]))
        ct[0] ^= 0xFF                          # flip a byte
        vault["entries"]["k"]["ct"] = ct.hex()
        cerberus._vault_write(vault)
        with self.assertRaises(cerberus.VaultError):
            cerberus.vault_get("k")

    def test_wrong_pin_key_cannot_decrypt(self):
        """A blob written under one PIN must not decrypt under a different PIN:
        the wrong derived key fails the MAC (VaultError, not garbage)."""
        cerberus.unlock(_TEST_PIN)
        cerberus.vault_set("k", "value")
        key_ok = cerberus._require_key()
        blob = cerberus._vault_load()["entries"]["k"]
        wrong_key = cerberus._derive_key("0000", cerberus._vault_salt())
        self.assertEqual(cerberus._decrypt(key_ok, blob), "value")
        with self.assertRaises(cerberus.VaultError):
            cerberus._decrypt(wrong_key, blob)


# ── Salt separation (R3) ─────────────────────────────────────────────────────

class TestSaltSeparation(_Base):

    def test_verify_salt_differs_from_kdf_salt(self):
        cerberus.unlock(_TEST_PIN)
        cerberus.vault_set("k", "v")           # mints the kdf-salt
        with open(cerberus._DATA_PATH, "r", encoding="utf-8") as f:
            verify_salt = json.load(f)["verify_salt"]
        kdf_salt = cerberus._vault_load()["kdf_salt"]
        self.assertTrue(kdf_salt)
        self.assertNotEqual(verify_salt, kdf_salt)

    def test_salts_live_in_separate_files(self):
        """The verify hash is in cerberus_data.json; the KDF salt is in
        cerberus_vault.json. They must never share a file."""
        cerberus.unlock(_TEST_PIN)
        cerberus.vault_set("k", "v")
        with open(cerberus._DATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("verify_salt", data)
        self.assertNotIn("kdf_salt", data)
        self.assertIn("kdf_salt", cerberus._vault_load())


# ── Ledger dedupe (R2) ───────────────────────────────────────────────────────

class TestLedger(_Base):

    def test_repeated_reads_dedupe_within_session(self):
        cerberus.unlock(_TEST_PIN)
        cerberus.vault_set("k", "v")
        cerberus.vault_get("k")
        cerberus.vault_get("k")
        cerberus.vault_get("k")
        reads = [e for e in cerberus.ledger_entries()
                 if e["head"] == "vault" and e["action"] == "read" and e["target"] == "k"]
        self.assertEqual(len(reads), 1)
        self.assertEqual(reads[0]["count"], 3)

    def test_distinct_keys_get_distinct_entries(self):
        cerberus.unlock(_TEST_PIN)
        cerberus.vault_set("a", "1")
        cerberus.vault_set("b", "2")
        cerberus.vault_get("a")
        cerberus.vault_get("b")
        targets = sorted(e["target"] for e in cerberus.ledger_entries()
                         if e["head"] == "vault" and e["action"] == "read")
        self.assertEqual(targets, ["a", "b"])

    def test_new_session_starts_new_entry(self):
        cerberus.unlock(_TEST_PIN)
        cerberus.vault_set("k", "v")
        cerberus.vault_get("k")
        cerberus.lock()
        cerberus.unlock(_TEST_PIN)
        cerberus.vault_get("k")
        reads = [e for e in cerberus.ledger_entries()
                 if e["head"] == "vault" and e["action"] == "read" and e["target"] == "k"]
        self.assertEqual(len(reads), 2)       # one per session, not deduped across

    def test_vault_write_is_logged(self):
        cerberus.unlock(_TEST_PIN)
        cerberus.vault_set("k", "v")
        writes = [e for e in cerberus.ledger_entries()
                  if e["head"] == "vault" and e["action"] == "write"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["target"], "k")

    def test_vault_writes_are_never_deduped(self):
        # Unlike reads, each vault_set call — including a silent overwrite of
        # the same name — is its own Ledger entry, not a bumped count.
        cerberus.unlock(_TEST_PIN)
        cerberus.vault_set("k", "v1")
        cerberus.vault_set("k", "v2")
        cerberus.vault_set("k", "v3")
        writes = [e for e in cerberus.ledger_entries()
                  if e["head"] == "vault" and e["action"] == "write"
                  and e["target"] == "k"]
        self.assertEqual(len(writes), 3)

    def test_vault_write_never_logs_the_value(self):
        cerberus.unlock(_TEST_PIN)
        cerberus.vault_set("k", "top-secret-value")
        for e in cerberus.ledger_entries():
            self.assertNotIn("top-secret-value", json.dumps(e))

    def test_custody_logs_full_fidelity(self):
        cerberus.ledger_log_custody("midas_watchlist.json", "open")
        cerberus.ledger_log_custody("midas_watchlist.json", "open")
        opens = [e for e in cerberus.ledger_entries() if e["head"] == "custody"]
        self.assertEqual(len(opens), 2)        # never deduped

    def test_newest_first_ordering(self):
        cerberus.ledger_log_custody("first.json", "open")
        cerberus.ledger_log_custody("second.json", "open")
        entries = cerberus.ledger_entries()
        self.assertEqual(entries[0]["target"], "second.json")

    def test_corrupt_ledger_degrades_to_empty(self):
        with open(cerberus._LEDGER_PATH, "w", encoding="utf-8") as f:
            f.write("{ not json")
        self.assertEqual(cerberus.ledger_entries(), [])


# ── Manifest (R1) ────────────────────────────────────────────────────────────

class TestManifest(_Base):

    def _write_manifest(self, obj):
        with open(cerberus._MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def test_only_manifest_entries_appear(self):
        # Custody is a whitelist: files on disk but absent from the manifest —
        # stray .json, scripts, icons, .env — must never surface.
        for name in ("described.json", "stray.json", "launch.bat", ".env"):
            with open(os.path.join(self._dir, name), "w", encoding="utf-8") as f:
                f.write("{}")
        self._write_manifest({
            "config_dir": self._dir,
            "entries": [{"file": "described.json", "desc": "the described one"}],
        })
        rows = {r["file"]: r for r in cerberus.manifest_configs()}
        self.assertEqual(set(rows), {"described.json"})
        self.assertEqual(rows["described.json"]["desc"], "the described one")
        self.assertTrue(rows["described.json"]["exists"])

    def test_manifest_entry_without_desc_falls_back(self):
        self._write_manifest({
            "config_dir": self._dir,
            "entries": [{"file": "ghost.json"}],   # no desc, not on disk
        })
        rows = {r["file"]: r for r in cerberus.manifest_configs()}
        self.assertEqual(rows["ghost.json"]["desc"], "(no description)")
        self.assertFalse(rows["ghost.json"]["exists"])

    def test_config_dir_resolved(self):
        self._write_manifest({"config_dir": self._dir, "entries": []})
        self.assertEqual(cerberus.manifest_config_dir(), os.path.realpath(self._dir))

    def test_missing_manifest_degrades(self):
        # setUp never created a manifest file here.
        self.assertEqual(cerberus.manifest_configs(), [])
        self.assertIsNone(cerberus.manifest_config_dir())

    def test_corrupt_manifest_degrades(self):
        with open(cerberus._MANIFEST_PATH, "w", encoding="utf-8") as f:
            f.write("{ not json")
        self.assertEqual(cerberus.manifest_configs(), [])


# ── Vault degraded paths ─────────────────────────────────────────────────────

class TestVaultDegraded(_Base):

    def test_missing_vault_lists_empty(self):
        self.assertEqual(cerberus.vault_names(), [])

    def test_corrupt_vault_lists_empty(self):
        with open(cerberus._VAULT_PATH, "w", encoding="utf-8") as f:
            f.write("{ not json")
        self.assertEqual(cerberus.vault_names(), [])


class TestSetPin(_Base):
    """First-run PIN setup — the public writer the Cerberus panel calls on a
    fresh clone (no cerberus_data.json)."""

    def test_set_pin_creates_verifiable_file(self):
        os.remove(cerberus._DATA_PATH)             # simulate a fresh clone
        with self.assertRaises(cerberus.HashFileError):
            cerberus.preflight()
        cerberus.set_pin("2468")
        cerberus.preflight()                       # loads now
        self.assertTrue(cerberus.verify("2468"))

    def test_set_pin_uses_fresh_salt_each_time(self):
        cerberus.set_pin("1111")
        with open(cerberus._DATA_PATH, encoding="utf-8") as f:
            first = json.load(f)
        cerberus.set_pin("1111")
        with open(cerberus._DATA_PATH, encoding="utf-8") as f:
            second = json.load(f)
        self.assertNotEqual(first["verify_salt"], second["verify_salt"])
        self.assertNotEqual(first["pin_hash"], second["pin_hash"])   # salted

    def test_set_pin_atomic_no_tmp_left(self):
        cerberus.set_pin("3690")
        leftovers = [f for f in os.listdir(self._dir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
