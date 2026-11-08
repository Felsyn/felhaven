"""
test_cerberus.py — unit tests for cerberus.py (the Guardian of Secrets).

Every test runs inside a CerberusSandbox (tests/_cerberus_sandbox.py): the path
constants point at a temp dir, so the real PIN file, vault and ledger never
enter this file. The fixture PIN is obviously fake.

The encryption tests use REAL Windows DPAPI, not a fake: it costs ~0.2 ms a
call (measured on Obelisk, 2026-09-30), and a fake would test our idea of
DPAPI rather than DPAPI. They skip off Windows; CI runs on windows-latest.

    python -X utf8 -m unittest tests.test_cerberus
"""

import ast
import base64
import importlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cerberus
from tests._cerberus_sandbox import FAKE_PIN, WRONG_PIN, CerberusSandbox
from tools import hypnos

_ON_WINDOWS = sys.platform == "win32"
_NEEDS_DPAPI = unittest.skipUnless(_ON_WINDOWS, "DPAPI exists only on Windows")

_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Base(unittest.TestCase):

    def setUp(self):
        self._box = CerberusSandbox()
        self._dir = self._box.start()

    def tearDown(self):
        self._box.stop()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _rows(self, head=None, action=None, target=None):
        return [e for e in cerberus.ledger_entries()
                if (head is None or e.get("head") == head)
                and (action is None or e.get("action") == action)
                and (target is None or e.get("target") == target)]

    def _raw_vault(self):
        with open(cerberus._VAULT_PATH, encoding="utf-8") as f:
            return json.load(f)

    def _write_raw_vault(self, obj):
        with open(cerberus._VAULT_PATH, "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def _write_v1_vault(self):
        # The shape v1 wrote: a PBKDF2 salt plus nonce/ct/mac entries.
        self._write_raw_vault({"kdf_salt": "00" * 16, "entries": {
            "finnhub_api_key": {"nonce": "00" * 16, "ct": "00", "mac": "00" * 32}}})

    def _vault_bytes(self):
        with open(cerberus._VAULT_PATH, "rb") as f:
            return f.read()


# ── The PIN check (Sphynx-pattern contract) ──────────────────────────────────

class TestVerify(_Base):

    def test_correct_pin(self):
        self.assertTrue(cerberus.verify(FAKE_PIN))
        self.assertEqual(cerberus.attempts_left(), 3)   # a hit never decrements

    def test_correct_pin_with_whitespace(self):
        self.assertTrue(cerberus.verify(f"  {FAKE_PIN}  \n"))

    def test_wrong_pin_decrements(self):
        self.assertFalse(cerberus.verify(WRONG_PIN))
        self.assertEqual(cerberus.attempts_left(), 2)

    def test_exhaustion_floors_at_zero(self):
        for _ in range(5):
            cerberus.verify(WRONG_PIN)
        self.assertEqual(cerberus.attempts_left(), 0)

    def test_post_exhaustion_rejects_even_correct_pin(self):
        for _ in range(3):
            cerberus.verify(WRONG_PIN)
        self.assertFalse(cerberus.verify(FAKE_PIN))
        self.assertEqual(cerberus.attempts_left(), 0)

    def test_fresh_import_resets_counter(self):
        cerberus.verify(WRONG_PIN)
        self.assertEqual(cerberus.attempts_left(), 2)
        importlib.reload(cerberus)
        self.assertEqual(cerberus.attempts_left(), 3)

    def test_preflight_ok(self):
        cerberus.preflight()          # must not raise

    def test_missing_hash_file_raises(self):
        os.remove(cerberus._DATA_PATH)
        with self.assertRaises(cerberus.HashFileError):
            cerberus.verify(FAKE_PIN)
        with self.assertRaises(cerberus.HashFileError):
            cerberus.preflight()

    def test_malformed_hash_file_raises(self):
        with open(cerberus._DATA_PATH, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        with self.assertRaises(cerberus.HashFileError):
            cerberus.verify(FAKE_PIN)

    def test_hash_file_missing_key_raises(self):
        with open(cerberus._DATA_PATH, "w", encoding="utf-8") as f:
            json.dump({"verify_salt": "00" * 16}, f)   # no pin_hash
        with self.assertRaises(cerberus.HashFileError):
            cerberus.verify(FAKE_PIN)


# ── set_pin: touches no key; a reset is loud ─────────────────────────────────

class TestSetPin(_Base):

    def test_set_pin_creates_verifiable_file(self):
        os.remove(cerberus._DATA_PATH)             # a fresh clone
        with self.assertRaises(cerberus.HashFileError):
            cerberus.preflight()
        cerberus.set_pin("fake-first-pin")
        cerberus.preflight()
        self.assertTrue(cerberus.verify("fake-first-pin"))

    def test_set_pin_uses_fresh_salt_each_time(self):
        cerberus.set_pin("fake-same-pin")
        with open(cerberus._DATA_PATH, encoding="utf-8") as f:
            first = json.load(f)
        cerberus.set_pin("fake-same-pin")
        with open(cerberus._DATA_PATH, encoding="utf-8") as f:
            second = json.load(f)
        self.assertNotEqual(first["verify_salt"], second["verify_salt"])
        self.assertNotEqual(first["pin_hash"], second["pin_hash"])   # salted

    def test_set_pin_atomic_no_tmp_left(self):
        cerberus.set_pin("fake-other-pin")
        leftovers = [f for f in os.listdir(self._dir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_empty_pin_is_refused(self):
        with self.assertRaises(ValueError):
            cerberus.set_pin("   ")

    def test_first_pin_on_an_empty_vault_is_not_a_reset(self):
        os.remove(cerberus._DATA_PATH)
        cerberus.set_pin("fake-first-pin")
        self.assertNotIn(cerberus.ALARM_PIN_RESET, cerberus.alarms())
        self.assertTrue(self._rows("session", "pin_set"))

    @_NEEDS_DPAPI
    def test_changing_the_pin_touches_no_key(self):
        """The PIN decrypts nothing, so a PIN change with keys present leaves
        every blob byte-identical and every key readable under the new PIN."""
        cerberus.unlock(FAKE_PIN)
        cerberus.vault_set("finnhub_api_key", "fake-finnhub-value")
        cerberus.vault_set("brave_api_key", "fake-brave-value")
        before = self._vault_bytes()
        cerberus.lock()

        cerberus.set_pin("fake-new-pin")

        self.assertEqual(self._vault_bytes(), before)
        self.assertFalse(cerberus.unlock(FAKE_PIN))          # the old PIN is gone
        self.assertTrue(cerberus.unlock("fake-new-pin"))
        self.assertEqual(cerberus.vault_get("finnhub_api_key"), "fake-finnhub-value")
        self.assertEqual(cerberus.vault_get("brave_api_key"), "fake-brave-value")
        self.assertTrue(self._rows("session", "pin_changed"))
        self.assertNotIn(cerberus.ALARM_PIN_RESET, cerberus.alarms())


@_NEEDS_DPAPI
class TestPinReset(_Base):
    """Deleting the PIN file is the cheapest way past a child lock. It still
    works (the PIN guards no secret), but it leaves a trace that stays up until
    someone with the new PIN dismisses it."""

    def _reset(self):
        cerberus.unlock(FAKE_PIN)
        cerberus.vault_set("finnhub_api_key", "fake-finnhub-value")
        cerberus.lock()
        os.remove(cerberus._DATA_PATH)
        with self.assertLogs("METIS.cerberus", level="ERROR"):
            cerberus.set_pin("fake-reset-pin")

    def test_reset_raises_the_alarm_and_writes_a_row(self):
        self._reset()
        self.assertIn(cerberus.ALARM_PIN_RESET, cerberus.alarms())
        self.assertEqual(len(self._rows("session", "pin_reset")), 1)
        with open(cerberus._DATA_PATH, encoding="utf-8") as f:
            self.assertTrue(json.load(f).get("reset_at"))

    def test_a_pin_change_after_a_reset_keeps_the_mark(self):
        self._reset()
        cerberus.set_pin("fake-changed-again")
        with open(cerberus._DATA_PATH, encoding="utf-8") as f:
            self.assertTrue(json.load(f).get("reset_at"))
        self.assertIn(cerberus.ALARM_PIN_RESET, cerberus.alarms())

    def test_the_alarm_survives_a_restart_without_a_second_row(self):
        self._reset()
        cerberus._ALARMS.clear()                       # a new process
        with mock.patch.object(hypnos, "presence",
                               return_value=hypnos.Presence(0.0, False, 0.0)), \
             self.assertLogs("METIS.cerberus", level="ERROR"):
            data = cerberus.fetch()
        self.assertIn(cerberus.ALARM_PIN_RESET, data["alarms"])
        self.assertEqual(len(self._rows("session", "pin_reset")), 1)

    def test_dismiss_needs_the_session(self):
        self._reset()
        with self.assertRaises(cerberus.VaultError) as cm:
            cerberus.dismiss_alarm(cerberus.ALARM_PIN_RESET)
        self.assertEqual(cm.exception.slug, cerberus.ERR_LOCKED)

    def test_dismiss_clears_the_mark_and_is_logged(self):
        self._reset()
        cerberus.unlock("fake-reset-pin")
        cerberus.dismiss_alarm(cerberus.ALARM_PIN_RESET)
        self.assertNotIn(cerberus.ALARM_PIN_RESET, cerberus.alarms())
        with open(cerberus._DATA_PATH, encoding="utf-8") as f:
            self.assertNotIn("reset_at", json.load(f))
        self.assertTrue(self._rows("session", "dismissed", cerberus.ALARM_PIN_RESET))

    def test_only_pin_reset_is_dismissed_by_hand(self):
        cerberus.unlock(FAKE_PIN)
        with self.assertRaises(ValueError):
            cerberus.dismiss_alarm(cerberus.ALARM_V1_VAULT)


# ── The session: a policy gate, and a Ledger trail ───────────────────────────

class TestSession(_Base):

    def test_locked_by_default(self):
        self.assertFalse(cerberus.is_unlocked())
        for call in (lambda: cerberus.vault_get("anything"),
                     lambda: cerberus.vault_set("k", "fake-value")):
            with self.assertRaises(cerberus.VaultError) as cm:
                call()
            self.assertEqual(cm.exception.slug, cerberus.ERR_LOCKED)

    def test_wrong_pin_does_not_unlock_and_is_a_row(self):
        self.assertFalse(cerberus.unlock(WRONG_PIN))
        self.assertFalse(cerberus.is_unlocked())
        self.assertEqual(cerberus.attempts_left(), 2)
        self.assertEqual(len(self._rows("session", "wrong_pin")), 1)

    def test_unlock_and_lock_are_rows_with_the_reason(self):
        self.assertTrue(cerberus.unlock(FAKE_PIN))
        self.assertEqual(len(self._rows("session", "unlock")), 1)
        cerberus.lock(cerberus.LOCK_IDLE)
        self.assertFalse(cerberus.is_unlocked())
        self.assertEqual(cerberus.lock_reason(), "idle")
        self.assertEqual(len(self._rows("session", "lock", "idle")), 1)

    def test_locking_a_locked_session_writes_nothing(self):
        cerberus.lock()
        cerberus.lock(cerberus.LOCK_EXIT)
        self.assertEqual(self._rows("session", "lock"), [])

    def test_the_ledger_never_holds_the_pin(self):
        cerberus.unlock(WRONG_PIN)
        cerberus.unlock(FAKE_PIN)
        cerberus.lock()
        with open(cerberus._LEDGER_PATH, encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn(FAKE_PIN, raw)
        self.assertNotIn(WRONG_PIN, raw)

    def test_lock_closes_the_session_even_if_the_ledger_cannot_be_written(self):
        cerberus.unlock(FAKE_PIN)
        os.remove(cerberus._LEDGER_PATH)
        os.mkdir(cerberus._LEDGER_PATH)          # a directory: os.replace fails
        with self.assertLogs("METIS.cerberus", level="ERROR"):
            cerberus.lock()
        self.assertFalse(cerberus.is_unlocked())

    @_NEEDS_DPAPI
    def test_relock_denies_access(self):
        cerberus.unlock(FAKE_PIN)
        cerberus.vault_set("k", "fake-value")
        cerberus.lock()
        with self.assertRaises(cerberus.VaultError):
            cerberus.vault_get("k")

    @_NEEDS_DPAPI
    def test_lock_waits_for_a_read_already_inside_the_decrypt(self):
        """The session lock is held across the whole read, so once lock()
        returns no read is still in flight. A read is parked inside DPAPI; a
        lock() on another thread must not return until that read is done."""
        cerberus.unlock(FAKE_PIN)
        cerberus.vault_set("k", "fake-value")
        inside, release, decrypted = (threading.Event(), threading.Event(),
                                      threading.Event())
        real = cerberus._dpapi

        def parked(protect, data, entropy):
            if not protect:
                inside.set()
                release.wait(5)
            out = real(protect, data, entropy)
            if not protect:
                decrypted.set()
            return out

        got: list[str] = []
        seen_when_locked: list[bool] = []
        with mock.patch.object(cerberus, "_dpapi", side_effect=parked):
            reader = threading.Thread(target=lambda: got.append(cerberus.vault_get("k")))
            reader.start()
            self.assertTrue(inside.wait(5))
            locker = threading.Thread(
                target=lambda: (cerberus.lock(), seen_when_locked.append(decrypted.is_set())))
            locker.start()
            locker.join(0.3)
            self.assertTrue(locker.is_alive(), "lock() returned during a read")
            release.set()
            reader.join(5)
            locker.join(5)
        self.assertEqual(got, ["fake-value"])            # it started before the lock
        self.assertEqual(seen_when_locked, [True])       # and finished before it
        with self.assertRaises(cerberus.VaultError):
            cerberus.vault_get("k")


# ── The vault under real DPAPI ───────────────────────────────────────────────

@_NEEDS_DPAPI
class TestDpapiVault(_Base):

    def setUp(self):
        super().setUp()
        cerberus.unlock(FAKE_PIN)

    def test_round_trip(self):
        cerberus.vault_set("finnhub_api_key", "fake-finnhub-value")
        self.assertEqual(cerberus.vault_get("finnhub_api_key"), "fake-finnhub-value")

    def test_file_is_v2_and_holds_no_plaintext(self):
        cerberus.vault_set("a", "fake-alpha-value")
        raw = self._raw_vault()
        self.assertEqual((raw["format"], raw["version"]), ("cerberus-vault", 2))
        base64.b64decode(raw["entries"]["a"]["blob"], validate=True)
        with open(cerberus._VAULT_PATH, encoding="utf-8") as f:
            self.assertNotIn("fake-alpha-value", f.read())
        self.assertNotIn("kdf_salt", raw)

    def test_equal_values_give_different_blobs(self):
        cerberus.vault_set("x", "fake-same")
        cerberus.vault_set("y", "fake-same")
        entries = self._raw_vault()["entries"]
        self.assertNotEqual(entries["x"]["blob"], entries["y"]["blob"])

    def test_dpapi_is_bound_to_the_user_not_the_machine(self):
        # 0x1 is CRYPTPROTECT_UI_FORBIDDEN. 0x4, CRYPTPROTECT_LOCAL_MACHINE,
        # would let every account on this PC decrypt: it must never be set.
        self.assertEqual(cerberus._DPAPI_FLAGS, 0x1)
        self.assertEqual(cerberus._DPAPI_FLAGS & 0x4, 0)

    def test_a_blob_under_the_wrong_name_fails_loudly(self):
        """The name is DPAPI's entropy. A blob decrypted under another name
        fails at the Windows layer (WinError 13), not just in our code."""
        cerberus.vault_set("finnhub_api_key", "fake-finnhub-value")
        blob = base64.b64decode(self._raw_vault()["entries"]["finnhub_api_key"]["blob"])
        with self.assertRaises(OSError) as cm:
            cerberus._dpapi(False, blob, cerberus._entropy("brave_api_key"))
        self.assertEqual(cm.exception.args[0], 13)

    def test_an_entry_swapped_into_another_slot_is_unreadable_and_loud(self):
        cerberus.vault_set("finnhub_api_key", "fake-finnhub-value")
        cerberus.vault_set("brave_api_key", "fake-brave-value")
        raw = self._raw_vault()
        raw["entries"]["brave_api_key"] = raw["entries"]["finnhub_api_key"]
        self._write_raw_vault(raw)
        with self.assertLogs("METIS.cerberus", level="ERROR"):
            with self.assertRaises(cerberus.VaultError) as cm:
                cerberus.vault_get("brave_api_key")
        self.assertEqual(cm.exception.slug, cerberus.ERR_UNREADABLE)
        self.assertIn("unreadable:brave_api_key", cerberus.alarms())
        self.assertEqual(len(self._rows("vault", "unreadable", "brave_api_key")), 1)
        # The other key still works.
        self.assertEqual(cerberus.vault_get("finnhub_api_key"), "fake-finnhub-value")

    def test_an_unreadable_key_is_one_row_however_often_it_is_read(self):
        cerberus.vault_set("k", "fake-value")
        raw = self._raw_vault()
        raw["entries"]["k"]["blob"] = base64.b64encode(b"not a dpapi blob").decode()
        self._write_raw_vault(raw)
        with self.assertLogs("METIS.cerberus", level="ERROR") as logs:
            for _ in range(3):
                with self.assertRaises(cerberus.VaultError):
                    cerberus.vault_get("k")
        self.assertEqual(len(logs.records), 1)              # loud once, not per read
        self.assertEqual(len(self._rows("vault", "unreadable", "k")), 1)

    def test_a_changed_byte_is_unreadable(self):
        cerberus.vault_set("k", "fake-value")
        raw = self._raw_vault()
        blob = bytearray(base64.b64decode(raw["entries"]["k"]["blob"]))
        blob[len(blob) // 2] ^= 0x01
        raw["entries"]["k"]["blob"] = base64.b64encode(bytes(blob)).decode()
        self._write_raw_vault(raw)
        with self.assertLogs("METIS.cerberus", level="ERROR"):
            with self.assertRaises(cerberus.VaultError) as cm:
                cerberus.vault_get("k")
        self.assertEqual(cm.exception.slug, cerberus.ERR_UNREADABLE)

    def test_a_malformed_entry_is_unreadable(self):
        cerberus.vault_set("k", "fake-value")
        for bad in ({}, {"blob": 7}, {"blob": "***not base64***"}, "a string"):
            with self.subTest(entry=bad):
                raw = self._raw_vault()
                raw["entries"]["k"] = bad
                self._write_raw_vault(raw)
                cerberus._ALARMS.clear()            # each case is its own alarm
                with self.assertLogs("METIS.cerberus", level="ERROR"):
                    with self.assertRaises(cerberus.VaultError) as cm:
                        cerberus.vault_get("k")
                self.assertEqual(cm.exception.slug, cerberus.ERR_UNREADABLE)

    def test_replace_heals_an_unreadable_key(self):
        cerberus.vault_set("k", "fake-old-value")
        raw = self._raw_vault()
        raw["entries"]["k"]["blob"] = base64.b64encode(b"junk").decode()
        self._write_raw_vault(raw)
        with self.assertLogs("METIS.cerberus", level="ERROR"):
            with self.assertRaises(cerberus.VaultError):
                cerberus.vault_get("k")
        cerberus.vault_set("k", "fake-new-value")
        self.assertNotIn("unreadable:k", cerberus.alarms())
        self.assertEqual(cerberus.vault_get("k"), "fake-new-value")

    def test_the_pin_is_not_part_of_the_key(self):
        """Knowing the PIN yields no key, because the PIN isn't in the crypto
        at all: with the session closed, the name and this Windows account are
        all DPAPI needs. That is also the honest residual SECURITY.md states:
        code running as this user can decrypt without any PIN."""
        cerberus.vault_set("k", "fake-value")
        cerberus.lock()
        blob = base64.b64decode(self._raw_vault()["entries"]["k"]["blob"])
        self.assertEqual(cerberus._dpapi(False, blob, cerberus._entropy("k")),
                         b"fake-value")

    def test_missing_name(self):
        cerberus.vault_set("present", "fake-value")
        with self.assertRaises(cerberus.VaultError) as cm:
            cerberus.vault_get("absent")
        self.assertEqual(cm.exception.slug, cerberus.ERR_NO_ENTRY)

    def test_names_never_leak_values(self):
        cerberus.vault_set("b", "fake-bravo")
        cerberus.vault_set("a", "fake-alpha")
        self.assertEqual(cerberus.vault_names(), ["a", "b"])

    def test_bad_input_is_refused(self):
        for name, value in (("", "fake-value"), ("   ", "fake-value"), ("k", "")):
            with self.subTest(name=name, value=value):
                with self.assertRaises(cerberus.VaultError) as cm:
                    cerberus.vault_set(name, value)
                self.assertEqual(cm.exception.slug, cerberus.ERR_BAD_INPUT)

    def test_health_reports_without_values_or_a_session(self):
        cerberus.vault_set("good", "fake-good-value")
        cerberus.vault_set("bad", "fake-bad-value")
        raw = self._raw_vault()
        raw["entries"]["bad"]["blob"] = base64.b64encode(b"junk").decode()
        self._write_raw_vault(raw)
        cerberus.lock()
        with self.assertLogs("METIS.cerberus", level="ERROR"):
            health = cerberus.vault_health()
        self.assertEqual(health, {"bad": "unreadable", "good": "ok"})
        self.assertNotIn("fake-good-value", json.dumps(health))
        self.assertIn("unreadable:bad", cerberus.alarms())


# ── The vault file: v1, corrupt, and retiring them ───────────────────────────

class TestVaultFile(_Base):

    def setUp(self):
        super().setUp()
        cerberus.unlock(FAKE_PIN)

    def test_missing_vault(self):
        self.assertEqual(cerberus.vault_status(), "missing")
        self.assertEqual(cerberus.vault_names(), [])

    def test_a_v1_vault_is_refused_loudly_and_left_untouched(self):
        self._write_v1_vault()
        before = self._vault_bytes()
        self.assertEqual(cerberus.vault_status(), "v1")
        with self.assertLogs("METIS.cerberus", level="ERROR"):
            with self.assertRaises(cerberus.VaultError) as cm:
                cerberus.vault_get("finnhub_api_key")
        self.assertEqual(cm.exception.slug, cerberus.ERR_V1_VAULT)
        with self.assertRaises(cerberus.VaultError) as cm:
            cerberus.vault_set("finnhub_api_key", "fake-value")
        self.assertEqual(cm.exception.slug, cerberus.ERR_V1_VAULT)
        self.assertEqual(self._vault_bytes(), before)
        self.assertIn(cerberus.ALARM_V1_VAULT, cerberus.alarms())
        self.assertEqual(len(self._rows("vault", "v1_found")), 1)   # once, not per call

    def test_a_corrupt_vault_is_refused_loudly(self):
        for content in ("{ not json", "[1, 2]", '{"format": "something-else"}'):
            with self.subTest(content=content):
                with open(cerberus._VAULT_PATH, "w", encoding="utf-8") as f:
                    f.write(content)
                self.assertEqual(cerberus.vault_status(), "corrupt")
                cerberus._ALARMS.clear()            # each case is its own alarm
                with self.assertLogs("METIS.cerberus", level="ERROR"):
                    with self.assertRaises(cerberus.VaultError) as cm:
                        cerberus.vault_get("k")
                self.assertEqual(cm.exception.slug, cerberus.ERR_CORRUPT)
                self.assertIn(cerberus.ALARM_CORRUPT, cerberus.alarms())

    def test_retire_needs_the_session(self):
        self._write_v1_vault()
        cerberus.lock()
        with self.assertRaises(cerberus.VaultError) as cm:
            cerberus.retire_vault()
        self.assertEqual(cm.exception.slug, cerberus.ERR_LOCKED)
        self.assertEqual(cerberus.vault_status(), "v1")

    def test_retire_renames_never_deletes(self):
        self._write_v1_vault()
        before = self._vault_bytes()
        with self.assertLogs("METIS.cerberus", level="WARNING"):
            new_name = cerberus.retire_vault()
        self.assertRegex(new_name, r"^cerberus_vault\.v1-\d{8}-\d{6}\.old$")
        with open(os.path.join(self._dir, new_name), "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(cerberus.vault_status(), "missing")
        self.assertNotIn(cerberus.ALARM_V1_VAULT, cerberus.alarms())
        self.assertTrue(self._rows("vault", "retired", new_name))

    def test_a_corrupt_file_retires_as_unreadable(self):
        with open(cerberus._VAULT_PATH, "w", encoding="utf-8") as f:
            f.write("{ not json")
        with self.assertLogs("METIS.cerberus", level="WARNING"):
            self.assertIn(".unreadable-", cerberus.retire_vault())

    def test_retire_refuses_a_good_vault(self):
        with self.assertRaises(cerberus.VaultError) as cm:
            cerberus.retire_vault()
        self.assertEqual(cm.exception.slug, cerberus.ERR_NOTHING_TO_RETIRE)

    def test_a_v1_file_reappearing_is_a_new_alarm_and_row(self):
        self._write_v1_vault()
        cerberus.vault_status()
        with self.assertLogs("METIS.cerberus", level="ERROR"):
            with self.assertRaises(cerberus.VaultError):
                cerberus.vault_get("k")
        with self.assertLogs("METIS.cerberus", level="WARNING"):
            cerberus.retire_vault()
        self._write_v1_vault()                     # it comes back
        with self.assertLogs("METIS.cerberus", level="ERROR"):
            with self.assertRaises(cerberus.VaultError):
                cerberus.vault_get("k")
        self.assertEqual(len(self._rows("vault", "v1_found")), 2)

    @_NEEDS_DPAPI
    def test_after_retiring_a_fresh_vault_starts(self):
        self._write_v1_vault()
        with self.assertLogs("METIS.cerberus", level="WARNING"):
            cerberus.retire_vault()
        cerberus.vault_set("finnhub_api_key", "fake-finnhub-value")
        self.assertEqual(cerberus.vault_status(), "v2")
        self.assertEqual(cerberus.vault_get("finnhub_api_key"), "fake-finnhub-value")


# ── fetch: lock when the person steps away ───────────────────────────────────

def _p(idle=0.0, locked=False, asleep=0.0):
    return hypnos.Presence(idle_s=idle, windows_locked=locked, asleep_s=asleep)


class TestAwayReason(unittest.TestCase):
    """The policy, pinned with literal thresholds (not the module constants, so
    a wrong constant can't move the test with it)."""

    def test_windows_locked(self):
        self.assertEqual(cerberus.away_reason(_p(locked=True), None, None), "windows_lock")

    def test_unknown_lock_state_is_not_a_lock(self):
        self.assertIsNone(cerberus.away_reason(_p(locked=None), None, None))

    def test_sleep_by_the_asleep_counter(self):
        self.assertEqual(cerberus.away_reason(_p(asleep=106.0), _p(asleep=100.0), 2.0),
                         "sleep")
        self.assertIsNone(cerberus.away_reason(_p(asleep=104.0), _p(asleep=100.0), 2.0))

    def test_sleep_needs_a_previous_reading(self):
        self.assertIsNone(cerberus.away_reason(_p(asleep=9999.0), None, None))

    def test_sleep_by_a_gap_between_checks(self):
        self.assertEqual(cerberus.away_reason(_p(), _p(), 61.0), "sleep")
        self.assertIsNone(cerberus.away_reason(_p(), _p(), 59.0))
        self.assertEqual(cerberus.away_reason(None, None, 61.0), "sleep")

    def test_idle_an_hour(self):
        self.assertEqual(cerberus.away_reason(_p(idle=3600.0), None, None), "idle")
        self.assertIsNone(cerberus.away_reason(_p(idle=3599.0), None, None))

    def test_present(self):
        self.assertIsNone(cerberus.away_reason(_p(), _p(), 2.0))
        self.assertIsNone(cerberus.away_reason(None, None, None))


class TestFetch(_Base):

    def _fetch(self, presence, now=1000.0):
        with mock.patch.object(hypnos, "presence", return_value=presence), \
             mock.patch.object(cerberus, "_wall_clock", return_value=now):
            return cerberus.fetch()

    def test_reports_the_session(self):
        self.assertEqual(self._fetch(_p()), {"unlocked": False, "reason": None,
                                             "alarms": []})
        cerberus.unlock(FAKE_PIN)
        self.assertTrue(self._fetch(_p(), 1002.0)["unlocked"])

    def test_idle_locks_an_open_session_with_a_row(self):
        cerberus.unlock(FAKE_PIN)
        data = self._fetch(_p(idle=3600.0))
        self.assertEqual((data["unlocked"], data["reason"]), (False, "idle"))
        self.assertFalse(cerberus.is_unlocked())
        self.assertEqual(len(self._rows("session", "lock", "idle")), 1)

    def test_windows_lock_locks(self):
        cerberus.unlock(FAKE_PIN)
        self.assertEqual(self._fetch(_p(locked=True))["reason"], "windows_lock")

    def test_sleep_locks_on_the_next_check(self):
        cerberus.unlock(FAKE_PIN)
        self.assertTrue(self._fetch(_p(asleep=50.0), 1000.0)["unlocked"])
        data = self._fetch(_p(asleep=3650.0), 1002.0)
        self.assertEqual((data["unlocked"], data["reason"]), (False, "sleep"))

    def test_a_long_gap_between_checks_locks(self):
        cerberus.unlock(FAKE_PIN)
        self._fetch(_p(), 1000.0)
        self.assertEqual(self._fetch(_p(), 1100.0)["reason"], "sleep")

    def test_away_while_locked_writes_nothing(self):
        self._fetch(_p(idle=99999.0, locked=True))
        self.assertEqual(self._rows("session", "lock"), [])

    def test_a_presence_failure_logs_once_and_never_locks(self):
        cerberus.unlock(FAKE_PIN)
        boom = hypnos.HypnosError("GetLastInputInfo failed")
        with mock.patch.object(hypnos, "presence", side_effect=boom), \
             mock.patch.object(cerberus, "_wall_clock", side_effect=[1000.0, 1002.0]):
            with self.assertLogs("METIS.cerberus", level="ERROR") as logs:
                cerberus.fetch()
                cerberus.fetch()
        self.assertEqual(len(logs.records), 1)
        self.assertTrue(cerberus.is_unlocked())

    def test_carries_file_alarms(self):
        self._write_v1_vault()
        with self.assertLogs("METIS.cerberus", level="ERROR"):
            data = self._fetch(_p())
        self.assertEqual(data["alarms"], [cerberus.ALARM_V1_VAULT])


# ── Ledger: dedupe and content ───────────────────────────────────────────────

@_NEEDS_DPAPI
class TestLedger(_Base):

    def setUp(self):
        super().setUp()
        cerberus.unlock(FAKE_PIN)

    def test_repeated_reads_dedupe_within_session(self):
        cerberus.vault_set("k", "fake-value")
        for _ in range(3):
            cerberus.vault_get("k")
        reads = self._rows("vault", "read", "k")
        self.assertEqual(len(reads), 1)
        self.assertEqual(reads[0]["count"], 3)

    def test_distinct_keys_get_distinct_entries(self):
        cerberus.vault_set("a", "fake-1")
        cerberus.vault_set("b", "fake-2")
        cerberus.vault_get("a")
        cerberus.vault_get("b")
        self.assertEqual(sorted(e["target"] for e in self._rows("vault", "read")),
                         ["a", "b"])

    def test_new_session_starts_new_entry(self):
        cerberus.vault_set("k", "fake-value")
        cerberus.vault_get("k")
        cerberus.lock()
        cerberus.unlock(FAKE_PIN)
        cerberus.vault_get("k")
        self.assertEqual(len(self._rows("vault", "read", "k")), 2)

    def test_vault_writes_are_never_deduped(self):
        for value in ("fake-v1", "fake-v2", "fake-v3"):
            cerberus.vault_set("k", value)
        self.assertEqual(len(self._rows("vault", "write", "k")), 3)

    def test_the_ledger_never_holds_a_value(self):
        cerberus.vault_set("k", "fake-top-secret-value")
        cerberus.vault_get("k")
        with open(cerberus._LEDGER_PATH, encoding="utf-8") as f:
            self.assertNotIn("fake-top-secret-value", f.read())

    def test_custody_logs_full_fidelity(self):
        cerberus.ledger_log_custody("midas_watchlist.json", "open")
        cerberus.ledger_log_custody("midas_watchlist.json", "open")
        self.assertEqual(len(self._rows("custody")), 2)

    def test_newest_first_ordering(self):
        cerberus.ledger_log_custody("first.json", "open")
        cerberus.ledger_log_custody("second.json", "open")
        self.assertEqual(cerberus.ledger_entries()[0]["target"], "second.json")

    def test_corrupt_ledger_degrades_to_empty(self):
        with open(cerberus._LEDGER_PATH, "w", encoding="utf-8") as f:
            f.write("{ not json")
        with self.assertLogs("METIS.cerberus", level="ERROR"):
            self.assertEqual(cerberus.ledger_entries(), [])


class TestLedgerConcurrency(_Base):
    """Midas reads the Finnhub key from a 5-thread pool, and every read appends
    or bumps a Ledger row. The whole load→mutate→write is one critical section
    and the write is atomic, so the file stays valid JSON and no count is
    lost. Before that fix both failed."""

    def _hammer(self, target, n_threads, per_thread):
        start = threading.Barrier(n_threads)
        errors = []

        def worker():
            try:
                start.wait()
                for _ in range(per_thread):
                    cerberus._ledger_add_read(target)
            except BaseException as exc:        # pragma: no cover - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [], f"a ledger writer thread raised: {errors}")

    def test_concurrent_reads_never_corrupt_the_file(self):
        cerberus.unlock(FAKE_PIN)
        for i in range(40):
            cerberus.ledger_log_custody(f"seed_{i}.json", "open")
        self._hammer("finnhub_api_key", n_threads=8, per_thread=25)
        with open(cerberus._LEDGER_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        self.assertIsInstance(raw, list)
        reads = [e for e in raw if e.get("head") == "vault"
                 and e.get("action") == "read" and e.get("target") == "finnhub_api_key"]
        self.assertEqual(len(reads), 1, "the deduped read split into multiple entries")
        self.assertEqual(reads[0]["count"], 8 * 25, "count bumps were lost to a race")
        self.assertEqual(len([e for e in raw if e.get("head") == "custody"]), 40)


# ── Custody: a whitelist, opened only inside config/ ─────────────────────────

class TestCustody(_Base):

    def _manifest(self, entries, config_dir=None):
        with open(cerberus._MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump({"config_dir": config_dir or self._dir, "entries": entries}, f)

    def _touch(self, path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("{}")

    def test_only_manifest_entries_appear(self):
        for name in ("described.json", "stray.json", "launch.bat", ".env"):
            self._touch(os.path.join(self._dir, name))
        self._manifest([{"file": "described.json", "desc": "the described one"}])
        rows = {r["file"]: r for r in cerberus.manifest_configs()}
        self.assertEqual(set(rows), {"described.json"})
        self.assertEqual(rows["described.json"]["desc"], "the described one")
        self.assertTrue(rows["described.json"]["exists"])
        self.assertIsNone(rows["described.json"]["refused"])

    def test_manifest_entry_without_desc_falls_back(self):
        self._manifest([{"file": "ghost.json"}])          # no desc, not on disk
        rows = {r["file"]: r for r in cerberus.manifest_configs()}
        self.assertEqual(rows["ghost.json"]["desc"], "(no description)")
        self.assertFalse(rows["ghost.json"]["exists"])
        self.assertIsNone(rows["ghost.json"]["refused"])

    def test_config_dir_resolved(self):
        self._manifest([])
        self.assertEqual(cerberus.manifest_config_dir(), os.path.realpath(self._dir))

    def test_a_config_dir_outside_config_is_refused(self):
        outside = tempfile.mkdtemp(prefix="cerberus_outside_")
        try:
            self._manifest([], config_dir=outside)
            with self.assertLogs("METIS.cerberus", level="WARNING"):
                self.assertIsNone(cerberus.manifest_config_dir())
        finally:
            os.rmdir(outside)

    def test_missing_manifest_degrades(self):
        with self.assertLogs("METIS.cerberus", level="WARNING"):
            self.assertEqual(cerberus.manifest_configs(), [])

    def test_corrupt_manifest_degrades(self):
        with open(cerberus._MANIFEST_PATH, "w", encoding="utf-8") as f:
            f.write("{ not json")
        with self.assertLogs("METIS.cerberus", level="ERROR"):
            self.assertEqual(cerberus.manifest_configs(), [])

    def test_open_needs_the_session(self):
        self._touch(os.path.join(self._dir, "demo.json"))
        self._manifest([{"file": "demo.json", "desc": "d"}])
        with self.assertRaises(cerberus.CustodyError) as cm:
            cerberus.custody_open("demo.json")
        self.assertEqual(cm.exception.slug, cerberus.ERR_LOCKED)

    def test_open_a_listed_json_inside_config(self):
        self._touch(os.path.join(self._dir, "demo.json"))
        self._manifest([{"file": "demo.json", "desc": "d"}])
        cerberus.unlock(FAKE_PIN)
        path = cerberus.custody_open("demo.json")
        self.assertEqual(path, os.path.join(os.path.realpath(self._dir), "demo.json"))
        self.assertTrue(self._rows("custody", "open", "demo.json"))

    def _refused(self, fname, slug):
        cerberus.unlock(FAKE_PIN)
        with self.assertLogs("METIS.cerberus", level="WARNING"):
            with self.assertRaises(cerberus.CustodyError) as cm:
                cerberus.custody_open(fname)
        self.assertEqual(cm.exception.slug, slug)
        self.assertTrue(self._rows("custody", "refused", fname))
        self.assertFalse(self._rows("custody", "open", fname))

    def test_refuses_a_path_that_climbs_out(self):
        outside = tempfile.mkdtemp(prefix="cerberus_outside_")
        try:
            target = os.path.join(outside, "x.exe")
            self._touch(target)
            fname = os.path.relpath(target, self._dir)        # ..\cerberus_outside_…\x.exe
            self.assertTrue(fname.startswith(".."))
            self._manifest([{"file": fname, "desc": "climbs out"}])
            self._refused(fname, cerberus.ERR_OUTSIDE_CONFIG)
        finally:
            os.remove(target)
            os.rmdir(outside)

    def test_refuses_an_absolute_path(self):
        outside = tempfile.mkdtemp(prefix="cerberus_outside_")
        try:
            target = os.path.join(outside, "settings.json")
            self._touch(target)
            self._manifest([{"file": target, "desc": "absolute"}])
            self._refused(target, cerberus.ERR_OUTSIDE_CONFIG)
        finally:
            os.remove(target)
            os.rmdir(outside)

    def test_refuses_a_program_inside_config(self):
        self._touch(os.path.join(self._dir, "launch.bat"))
        self._manifest([{"file": "launch.bat", "desc": "runs, doesn't open"}])
        self._refused("launch.bat", cerberus.ERR_NOT_JSON)
        rows = {r["file"]: r for r in cerberus.manifest_configs()}
        self.assertEqual(rows["launch.bat"]["refused"], cerberus.ERR_NOT_JSON)

    def test_refuses_an_unlisted_file(self):
        self._touch(os.path.join(self._dir, "stray.json"))
        self._manifest([])
        self._refused("stray.json", cerberus.ERR_NOT_LISTED)

    def test_a_listed_missing_file_is_refused_as_missing(self):
        self._manifest([{"file": "ghost.json", "desc": "d"}])
        self._refused("ghost.json", cerberus.ERR_MISSING)


class TestTheShippedManifestMatchesConfigDir(unittest.TestCase):
    """Hold the REAL Custody manifest to the REAL config/ directory.

    Every other manifest test in this file writes its own synthetic manifest
    into a temp dir, which is right for testing the loader and wrong for
    testing the CONTENT. The result was that nothing in the project ever read
    the shipped manifest, so it was free to drift — and it did:
    config/euterpe_config.json shipped on 2026-09-16 and was not registered,
    the first miss after seven-for-seven.

    This is the §10 lockstep case exactly: two live sources compared, never a
    hardcoded count. It deliberately does NOT use the sandbox — it must see the
    real file.

    Asserted against GIT, not the filesystem (§10: "Ask what a fresh clone
    has"). That matters in both directions here: config/ holds gitignored
    personal files a fresh clone will not have, and it can hold local junk a
    fresh clone will not have either.
    """

    def _git(self, *args):
        return subprocess.run(["git", *args], cwd=_APP,
                              capture_output=True, text=True)

    def _manifest(self):
        path = os.path.join(_APP, "config", "cerberus_manifest.json")
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def _listed(self):
        return {e["file"] for e in self._manifest().get("entries", [])
                if isinstance(e, dict) and e.get("file")}

    def _tracked_configs(self):
        out = self._git("ls-files", "config/")
        self.assertEqual(out.returncode, 0, out.stderr)
        return {os.path.basename(p) for p in out.stdout.split()
                if p.endswith(".json")}

    def test_config_dir_points_at_the_real_folder(self):
        self.assertEqual(self._manifest().get("config_dir"), "config")

    def test_every_tracked_config_is_in_custody(self):
        """The forward direction, and the one that was broken. A shipped config
        nobody registered is invisible behind the PIN — reachable only through
        the Open Folder button, which is why it went unnoticed."""
        missing = self._tracked_configs() - self._listed()
        self.assertEqual(missing, set(),
                         f"these shipped configs are not in Custody: "
                         f"{sorted(missing)}")

    def test_every_entry_names_a_file_git_tracks_or_ignores(self):
        """The reverse direction, and it has to be phrased this way rather than
        "every entry is tracked": morpheus_playlists.json and sphynx_data.json
        are legitimately gitignored — config SHAPE with personal CONTENT (§4) —
        and must stay listed. What this actually catches is a filename that is
        neither, i.e. a typo, which today would render as "(missing)" in the
        panel forever and fail no test."""
        for fname in sorted(self._listed()):
            with self.subTest(file=fname):
                rel = f"config/{fname}"
                tracked = self._git("ls-files", "--error-unmatch",
                                    rel).returncode == 0
                ignored = self._git("check-ignore", rel).returncode == 0
                self.assertTrue(
                    tracked or ignored,
                    f"{rel} is neither tracked nor gitignored — most likely a "
                    f"typo in the manifest, which the panel would show as "
                    f"'(missing)' and no other test would catch")

    def test_every_entry_is_a_json_file_name(self):
        """Custody opens only .json files inside config/, so a shipped entry
        that isn't one would only ever show as refused."""
        for fname in sorted(self._listed()):
            with self.subTest(file=fname):
                self.assertTrue(fname.endswith(".json"))
                self.assertNotIn("..", fname)
                self.assertFalse(os.path.isabs(fname))

    def test_no_entry_is_listed_twice(self):
        """A duplicate would silently collapse to one row rather than error."""
        files = [e.get("file") for e in self._manifest().get("entries", [])
                 if isinstance(e, dict)]
        self.assertEqual(len(files), len(set(files)),
                         f"duplicate entries: {files}")

    def test_every_entry_has_a_description(self):
        """(no description) is the loader's fallback, not an acceptable ship
        state — the description is the only thing telling an operator what they
        are about to open."""
        for entry in self._manifest().get("entries", []):
            with self.subTest(file=entry.get("file")):
                self.assertTrue((entry.get("desc") or "").strip())


# ── The CLI: secrets only through hidden prompts ─────────────────────────────

class TestCli(_Base):

    def _run(self, argv, prompts=()):
        out = io.StringIO()
        with mock.patch("getpass.getpass", side_effect=list(prompts)) as gp, \
             mock.patch("builtins.input", side_effect=AssertionError("input() used")), \
             redirect_stdout(out):
            code = cerberus.main(argv)
        return code, out.getvalue(), gp

    @_NEEDS_DPAPI
    def test_set_reads_the_pin_and_value_from_hidden_prompts(self):
        code, out, gp = self._run(["set", "finnhub_api_key"],
                                  [FAKE_PIN, "  fake-finnhub-value \n"])
        self.assertEqual(code, 0)
        self.assertEqual(gp.call_count, 2)
        self.assertNotIn("fake-finnhub-value", out)
        self.assertFalse(cerberus.is_unlocked())          # the CLI locks on exit
        cerberus.unlock(FAKE_PIN)
        self.assertEqual(cerberus.vault_get("finnhub_api_key"), "fake-finnhub-value")

    def test_set_with_a_wrong_pin_stores_nothing(self):
        code, out, _ = self._run(["set", "finnhub_api_key"], [WRONG_PIN])
        self.assertEqual(code, 1)
        self.assertEqual(cerberus.vault_status(), "missing")

    def test_a_value_on_the_command_line_is_refused(self):
        for argv in (["set", "finnhub_api_key", "fake-argv-value"],
                     ["set", FAKE_PIN, "finnhub_api_key", "fake-argv-value"],
                     ["setpin", "fake-argv-pin"]):
            with self.subTest(argv=argv):
                code, out, gp = self._run(argv)
                self.assertEqual(code, 2)
                self.assertIn("history", out)
                self.assertNotIn("fake-argv-value", out)
                self.assertEqual(gp.call_count, 0)
        self.assertEqual(cerberus.vault_status(), "missing")
        self.assertTrue(cerberus.verify(FAKE_PIN))        # the PIN is unchanged

    def test_get_is_gone(self):
        code, out, _ = self._run(["get", "finnhub_api_key"])
        self.assertEqual(code, 2)
        self.assertIn("gone", out)

    def test_setpin_asks_for_the_current_pin(self):
        code, _, _ = self._run(["setpin"], [WRONG_PIN])
        self.assertEqual(code, 1)
        self.assertTrue(cerberus.verify(FAKE_PIN))
        code, _, _ = self._run(["setpin"], [FAKE_PIN, "fake-new-pin", "fake-new-pin"])
        self.assertEqual(code, 0)
        self.assertTrue(cerberus.verify("fake-new-pin"))

    def test_setpin_refuses_a_mismatch(self):
        code, _, _ = self._run(["setpin"], [FAKE_PIN, "fake-new-pin", "fake-typo-pin"])
        self.assertEqual(code, 1)
        self.assertTrue(cerberus.verify(FAKE_PIN))

    @_NEEDS_DPAPI
    def test_status_never_prints_a_value_and_needs_no_pin(self):
        cerberus.unlock(FAKE_PIN)
        cerberus.vault_set("finnhub_api_key", "fake-finnhub-value")
        cerberus.lock()
        with self.assertLogs("METIS.cerberus", level="WARNING"):   # no manifest here
            code, out, gp = self._run(["status"])
        self.assertEqual(code, 0)
        self.assertEqual(gp.call_count, 0)
        self.assertNotIn("fake-finnhub-value", out)
        self.assertRegex(out, r"finnhub_api_key\s+ok")
        self.assertRegex(out, r"brave_api_key\s+not set")


# ── Nothing secret in the log ────────────────────────────────────────────────

@_NEEDS_DPAPI
class TestNoSecretsInTheLog(_Base):

    def test_a_full_lifecycle_logs_neither_pin_nor_value(self):
        with self.assertLogs("METIS.cerberus", level="DEBUG") as logs:
            cerberus.unlock(WRONG_PIN)
            cerberus.unlock(FAKE_PIN)
            cerberus.vault_set("k", "fake-logged-value")
            cerberus.vault_get("k")
            raw = self._raw_vault()
            raw["entries"]["k"]["blob"] = base64.b64encode(b"junk").decode()
            self._write_raw_vault(raw)
            with self.assertRaises(cerberus.VaultError):
                cerberus.vault_get("k")
            cerberus.lock()
        text = "\n".join(logs.output)
        for secret in (FAKE_PIN, WRONG_PIN, "fake-logged-value"):
            self.assertNotIn(secret, text)


# ── Structure ────────────────────────────────────────────────────────────────

class TestStructure(unittest.TestCase):

    def _tree(self):
        with open(os.path.join(_APP, "cerberus.py"), encoding="utf-8") as f:
            return ast.parse(f.read())

    def test_never_imports_random(self):
        """Salts and ids come from `secrets`; `random` is predictable."""
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Import):
                self.assertNotIn("random", [a.name for a in node.names])
            if isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "random")

    def test_derives_no_key_from_the_pin(self):
        """No key-derivation function anywhere: the PIN is a gate, not a key."""
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, ("pbkdf2_hmac", "scrypt"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
