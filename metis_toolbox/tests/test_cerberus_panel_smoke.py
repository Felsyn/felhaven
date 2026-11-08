"""
test_cerberus_panel_smoke.py — headless Tk smoke test for CerberusPanel and
CerberusLamp.

Same approach as test_morpheus_panel_smoke.py: a real tk.Tk() root we
withdraw() (no window flashes) and drive with update_idletasks() instead of
mainloop().

Cerberus runs inside a CerberusSandbox (tests/_cerberus_sandbox.py), so the
real PIN file, vault and ledger never enter this test, and the fixture PIN is
obviously fake. The vault is real DPAPI (Windows only). The OS-editor handoff
(_open_path) is monkeypatched so Custody clicks never launch an editor.

    python -X utf8 -m unittest tests.test_cerberus_panel_smoke
"""

import base64
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
import cerberus
import panels.cerberus_panel as cpanel
from panels.cerberus_panel import CerberusLamp, CerberusPanel
from tests._cerberus_sandbox import FAKE_PIN, WRONG_PIN, CerberusSandbox

_NEEDS_DPAPI = unittest.skipUnless(sys.platform == "win32", "DPAPI exists only on Windows")


def _texts(widget):
    """Every Label/Entry text under `widget`, for 'is this string on screen?'."""
    out = []
    for w in widget.winfo_children():
        if isinstance(w, tk.Label):
            out.append(str(w.cget("text")))
        elif isinstance(w, tk.Entry):
            out.append(w.get())
        out.extend(_texts(w))
    return out


class _PanelBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self._box = CerberusSandbox()
        self._dir = self._box.start()

        with open(os.path.join(self._dir, "demo_config.json"), "w",
                  encoding="utf-8") as f:
            f.write("{}")
        with open(os.path.join(self._dir, "launch.bat"), "w", encoding="utf-8") as f:
            f.write("echo hi")
        with open(cerberus._MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump({"config_dir": self._dir,
                       "entries": [{"file": "demo_config.json", "desc": "a demo config"},
                                   {"file": "launch.bat", "desc": "a program"}]}, f)

        # Never launch a real editor.
        self._orig_open = cpanel._open_path
        self._opened: list[str] = []
        cpanel._open_path = lambda p: self._opened.append(p) or None

        self.panel = self._make_panel()

    def tearDown(self):
        cpanel._open_path = self._orig_open
        self.panel.destroy()
        self._box.stop()

    def _make_panel(self):
        panel = CerberusPanel(self.root)
        panel.pack()
        self.root.update_idletasks()
        return panel

    def _seed(self, name, value):
        cerberus.unlock(FAKE_PIN)
        cerberus.vault_set(name, value)
        cerberus.lock()

    def _unlock(self, pin=FAKE_PIN):
        self.panel._entry.insert(0, pin)
        self.panel._on_submit()
        self.root.update_idletasks()

    def _rows(self, head=None, action=None, target=None):
        return [e for e in cerberus.ledger_entries()
                if (head is None or e.get("head") == head)
                and (action is None or e.get("action") == action)
                and (target is None or e.get("target") == target)]


class TestGate(_PanelBase):

    def test_starts_locked(self):
        self.assertFalse(self.panel._unlocked_built)
        self.assertFalse(cerberus.is_unlocked())

    def test_wrong_pin_stays_gated(self):
        self._unlock(WRONG_PIN)
        self.assertFalse(self.panel._unlocked_built)
        self.assertIn("attempt", self.panel._gate_status.cget("text").lower())
        self.assertEqual(cerberus.attempts_left(), 2)

    def test_correct_pin_unlocks_and_builds_sections(self):
        self._unlock()
        self.assertTrue(self.panel._unlocked_built)
        self.assertEqual(set(self.panel._sections), {"vault", "custody", "ledger"})
        self.assertTrue(cerberus.is_unlocked())

    def test_lock_returns_to_gate(self):
        self._unlock()
        self.panel._lock()
        self.root.update_idletasks()
        self.assertFalse(cerberus.is_unlocked())
        self.assertEqual(str(self.panel._entry.cget("state")), "normal")
        self.assertTrue(self._rows("session", "lock", "manual"))

    def test_missing_pin_file_enters_first_run_setup(self):
        os.remove(cerberus._DATA_PATH)
        panel = self._make_panel()
        self.assertTrue(panel._first_run)
        self.assertEqual(str(panel._entry.cget("state")), "normal")
        self.assertEqual(str(panel._enter_btn.cget("text")), "CREATE")
        self.assertIn("decrypts nothing", panel._gate_status.cget("text"))
        panel.destroy()

    def test_first_run_create_pin_seals_and_unlocks(self):
        os.remove(cerberus._DATA_PATH)
        panel = self._make_panel()
        panel._entry.insert(0, "fake-first-pin")
        panel._confirm_entry.insert(0, "fake-first-pin")
        panel._create_pin()
        self.root.update_idletasks()
        self.assertFalse(panel._first_run)
        self.assertTrue(os.path.exists(cerberus._DATA_PATH))
        self.assertTrue(cerberus.is_unlocked())
        self.assertTrue(panel._unlocked_built)
        panel.destroy()

    def test_first_run_mismatched_pins_do_not_write(self):
        os.remove(cerberus._DATA_PATH)
        panel = self._make_panel()
        panel._entry.insert(0, "fake-one")
        panel._confirm_entry.insert(0, "fake-two")
        panel._create_pin()
        self.root.update_idletasks()
        self.assertTrue(panel._first_run)
        self.assertFalse(os.path.exists(cerberus._DATA_PATH))
        panel.destroy()

    @_NEEDS_DPAPI
    def test_first_run_with_keys_present_warns_and_raises_the_alarm(self):
        self._seed("finnhub_api_key", "fake-finnhub-value")
        os.remove(cerberus._DATA_PATH)
        panel = self._make_panel()
        self.assertIn("reset", panel._gate_status.cget("text"))
        panel._entry.insert(0, "fake-reset-pin")
        panel._confirm_entry.insert(0, "fake-reset-pin")
        with self.assertLogs("METIS.cerberus", level="ERROR"):
            panel._create_pin()
        self.root.update_idletasks()
        self.assertIn(cerberus.ALARM_PIN_RESET, panel._alarms)
        self.assertTrue(any("PIN file was recreated" in t for t in _texts(panel._banner)))
        panel.destroy()


@_NEEDS_DPAPI
class TestVaultRows(_PanelBase):

    def test_every_known_key_has_a_row_even_unset(self):
        self._unlock()
        for name in cerberus.KNOWN_KEYS:
            with self.subTest(name=name):
                self.assertEqual(self.panel._rows[name]["value"].cget("text"), "not set")
                self.assertEqual(self.panel._rows[name]["replace"].cget("text"), "set")

    def test_an_unknown_key_in_the_vault_gets_a_row_too(self):
        self._seed("other_key", "fake-other-value")
        self._unlock()
        self.assertIn("other_key", self.panel._rows)

    def test_set_a_key_through_its_row(self):
        self._unlock()
        self.panel._start_replace("brave_api_key")
        self.root.update_idletasks()
        self.panel._editor.insert(0, "  fake-brave-value  ")
        self.panel._save_replace("brave_api_key", self.panel._editor)
        self.root.update_idletasks()
        self.assertEqual(cerberus.vault_get("brave_api_key"), "fake-brave-value")
        self.assertIsNone(self.panel._editing)
        self.assertEqual(self.panel._rows["brave_api_key"]["replace"].cget("text"), "replace")

    def test_replace_never_decrypts_the_old_key(self):
        self._seed("finnhub_api_key", "fake-old-value")
        self._unlock()
        with mock.patch.object(cerberus, "vault_get",
                               side_effect=AssertionError("REPLACE decrypted the old key")):
            self.panel._start_replace("finnhub_api_key")
            self.root.update_idletasks()
            self.assertNotIn("fake-old-value", _texts(self.panel))
            self.panel._editor.insert(0, "fake-new-value")
            self.panel._save_replace("finnhub_api_key", self.panel._editor)
            self.root.update_idletasks()
        self.assertEqual(cerberus.vault_get("finnhub_api_key"), "fake-new-value")
        self.assertTrue(self._rows("vault", "write", "finnhub_api_key"))

    def test_cancel_changes_nothing(self):
        self._seed("finnhub_api_key", "fake-old-value")
        self._unlock()
        self.panel._start_replace("finnhub_api_key")
        self.panel._editor.insert(0, "fake-typed-then-cancelled")
        self.panel._cancel_replace()
        self.root.update_idletasks()
        self.assertIsNone(self.panel._editing)
        self.assertEqual(cerberus.vault_get("finnhub_api_key"), "fake-old-value")

    def test_an_empty_replace_is_refused(self):
        self._seed("finnhub_api_key", "fake-old-value")
        self._unlock()
        self.panel._start_replace("finnhub_api_key")
        self.panel._save_replace("finnhub_api_key", self.panel._editor)
        self.assertEqual(self.panel._editing, "finnhub_api_key")
        self.assertEqual(cerberus.vault_get("finnhub_api_key"), "fake-old-value")

    def test_an_unreadable_key_says_re_enter_and_replace_heals_it(self):
        self._seed("finnhub_api_key", "fake-old-value")
        with open(cerberus._VAULT_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        raw["entries"]["finnhub_api_key"]["blob"] = base64.b64encode(b"junk").decode()
        with open(cerberus._VAULT_PATH, "w", encoding="utf-8") as f:
            json.dump(raw, f)
        with self.assertLogs("METIS.cerberus", level="ERROR"):
            self._unlock()
        self.assertIn("re-enter", self.panel._rows["finnhub_api_key"]["value"].cget("text"))
        self.assertNotIn("reveal", self.panel._rows["finnhub_api_key"])
        self.panel._start_replace("finnhub_api_key")
        self.panel._editor.insert(0, "fake-new-value")
        self.panel._save_replace("finnhub_api_key", self.panel._editor)
        self.root.update_idletasks()
        self.assertEqual(cerberus.alarms(), [])
        self.assertEqual(self.panel._rows["finnhub_api_key"]["value"].cget("text"),
                         cpanel._MASK)

    def test_reveal_decrypts_and_logs(self):
        self._seed("finnhub_api_key", "fake-finnhub-value")
        self._unlock()
        row = self.panel._rows["finnhub_api_key"]
        self.panel._toggle_reveal("finnhub_api_key", row["value"], row["reveal"])
        self.assertEqual(row["value"].cget("text"), "fake-finnhub-value")
        self.assertEqual(row["reveal"].cget("text"), "hide")
        self.assertEqual(len(self._rows("vault", "read", "finnhub_api_key")), 1)
        self.panel._toggle_reveal("finnhub_api_key", row["value"], row["reveal"])
        self.assertEqual(row["value"].cget("text"), cpanel._MASK)


@_NEEDS_DPAPI
class TestReseal(_PanelBase):
    """Kairos's `cerberus` worker drives update(); the tab must follow a lock
    that happened anywhere, and leave no key on screen."""

    def test_an_outside_lock_reseals_and_clears_every_key_from_the_widgets(self):
        self._seed("finnhub_api_key", "fake-finnhub-value")
        self._unlock()
        row = self.panel._rows["finnhub_api_key"]
        self.panel._toggle_reveal("finnhub_api_key", row["value"], row["reveal"])
        self.assertIn("fake-finnhub-value", _texts(self.panel))

        cerberus.lock(cerberus.LOCK_IDLE)
        self.panel.update({"unlocked": False, "reason": "idle", "alarms": []})
        self.root.update_idletasks()

        self.assertFalse(self.panel._showing_unlocked)
        self.assertNotIn("fake-finnhub-value", _texts(self.panel))
        self.assertIn("hour", self.panel._gate_status.cget("text"))

    def test_an_outside_lock_drops_a_half_typed_replace(self):
        self._unlock()
        self.panel._start_replace("brave_api_key")
        self.panel._editor.insert(0, "fake-half-typed")
        cerberus.lock(cerberus.LOCK_WINDOWS)
        self.panel.update({"unlocked": False, "reason": "windows_lock", "alarms": []})
        self.root.update_idletasks()
        self.assertIsNone(self.panel._editing)
        self.assertNotIn("fake-half-typed", _texts(self.panel))
        self.assertIn("Windows", self.panel._gate_status.cget("text"))

    def test_update_survives_none_and_never_opens_the_gate(self):
        self.panel.update(None)
        cerberus.unlock(FAKE_PIN)          # unlocked from another tab
        self.panel.update({"unlocked": True, "reason": None, "alarms": []})
        self.root.update_idletasks()
        self.assertFalse(self.panel._showing_unlocked)


class TestBanner(_PanelBase):

    def _v1(self):
        with open(cerberus._VAULT_PATH, "w", encoding="utf-8") as f:
            json.dump({"kdf_salt": "00" * 16, "entries": {}}, f)

    def test_sealed_banner_names_the_old_vault_without_an_action(self):
        self._v1()
        self.panel.update({"unlocked": False, "reason": None,
                           "alarms": [cerberus.ALARM_V1_VAULT]})
        self.root.update_idletasks()
        texts = _texts(self.panel._banner)
        self.assertTrue(any("PIN-encrypted" in t for t in texts))
        self.assertIn("Unlock to set it aside.", texts)
        self.assertIsNone(self.panel._retire_btn)

    def test_retire_takes_two_clicks_and_renames_the_file(self):
        self._v1()
        self._unlock()          # reads the vault's status only; fetch() raises the alarm
        self.panel.update({"unlocked": True, "reason": None,
                           "alarms": [cerberus.ALARM_V1_VAULT]})
        self.root.update_idletasks()
        self.assertIsNotNone(self.panel._retire_btn)

        self.panel._on_retire_click()                      # arms
        self.assertEqual(cerberus.vault_status(), "v1")
        self.assertIn("CONFIRM", self.panel._retire_btn.cget("text"))
        with self.assertLogs("METIS.cerberus", level="WARNING"):
            self.panel._on_retire_click()                  # fires
        self.root.update_idletasks()

        self.assertEqual(cerberus.vault_status(), "missing")
        self.assertTrue([f for f in os.listdir(self._dir) if f.endswith(".old")])
        self.assertEqual(self.panel._alarms, ())
        self.assertFalse(self.panel._banner.winfo_manager())
        self.assertIn("delete that file", self.panel._status.cget("text"))

    def test_an_armed_retire_disarms_and_its_timer_dies_with_the_panel(self):
        self._v1()
        self._unlock()          # reads the vault's status only; fetch() raises the alarm
        self.panel.update({"unlocked": True, "reason": None,
                           "alarms": [cerberus.ALARM_V1_VAULT]})
        self.panel._on_retire_click()
        after_id = self.panel._retire_after_id
        self.assertIsNotNone(after_id)
        self.panel.destroy()
        self.assertNotIn(after_id, self.root.tk.call("after", "info"))
        self.panel = self._make_panel()          # for tearDown

    @_NEEDS_DPAPI
    def test_pin_reset_can_be_dismissed(self):
        self._seed("finnhub_api_key", "fake-finnhub-value")
        os.remove(cerberus._DATA_PATH)
        with self.assertLogs("METIS.cerberus", level="ERROR"):
            cerberus.set_pin("fake-reset-pin")
        self._unlock("fake-reset-pin")
        self.panel.update({"unlocked": True, "reason": None,
                           "alarms": cerberus.alarms()})
        self.root.update_idletasks()
        self.assertIn("dismiss", _texts(self.panel._banner))
        self.panel._on_dismiss_reset()
        self.root.update_idletasks()
        self.assertEqual(cerberus.alarms(), [])
        self.assertFalse(self.panel._banner.winfo_manager())


class TestCustody(_PanelBase):

    def test_open_logs_and_hands_off(self):
        self._unlock()
        self.panel._open_config("demo_config.json")
        self.assertEqual(len(self._opened), 1)
        self.assertTrue(self._opened[0].endswith("demo_config.json"))
        self.assertEqual(len(self._rows("custody", "open")), 1)

    def test_a_program_is_shown_refused_and_never_handed_off(self):
        self._unlock()
        self.panel._sections["custody"].toggle()
        self.root.update_idletasks()
        self.assertTrue(any("launch.bat" in t and "refused" in t
                            for t in _texts(self.panel._sections["custody"].content)))
        with self.assertLogs("METIS.cerberus", level="WARNING"):
            self.panel._open_config("launch.bat")
        self.assertEqual(self._opened, [])
        self.assertIn("won't open", self.panel._status.cget("text"))
        self.assertTrue(self._rows("custody", "refused", "launch.bat"))

    def test_open_folder_hands_off_config_dir(self):
        self._unlock()
        self.panel._open_folder()
        self.assertEqual(len(self._opened), 1)
        self.assertEqual(os.path.realpath(self._opened[0]), os.path.realpath(self._dir))

    def test_sections_render_without_error(self):
        self._unlock()
        for key in ("vault", "custody", "ledger"):
            self.panel._sections[key].toggle()
            self.root.update_idletasks()
            self.panel._render_section(key)
            self.root.update_idletasks()

    def test_refresh_open_is_safe(self):
        self._unlock()
        self.panel._refresh_open()
        self.root.update_idletasks()


@_NEEDS_DPAPI
class TestVaultForm(_PanelBase):
    """The generic add form at the bottom of the Vault section."""

    def _submit(self, name, value):
        name_entry = tk.Entry(self.panel)
        value_entry = tk.Entry(self.panel)
        name_entry.insert(0, name)
        value_entry.insert(0, value)
        self.panel._on_vault_submit(name_entry, value_entry)
        self.root.update_idletasks()

    def test_submit_adds_new_key(self):
        self._unlock()
        self._submit("other_key", "fake-other-value")
        self.assertEqual(cerberus.vault_get("other_key"), "fake-other-value")
        self.assertIn("other_key", self.panel._rows)

    def test_submit_overwrite_is_not_deduped(self):
        self._seed("other_key", "fake-first")
        self._unlock()
        self._submit("other_key", "fake-second")
        self.assertEqual(cerberus.vault_get("other_key"), "fake-second")
        self.assertEqual(len(self._rows("vault", "write", "other_key")), 2)

    def test_submit_logs_write_without_value(self):
        self._unlock()
        self._submit("other_key", "fake-top-secret-value")
        with open(cerberus._LEDGER_PATH, encoding="utf-8") as f:
            self.assertNotIn("fake-top-secret-value", f.read())

    def test_submit_empty_name_rejected(self):
        self._unlock()
        self._submit("", "fake-value")
        self.assertEqual(cerberus.vault_names(), [])
        self.assertIn("name", self.panel._status.cget("text").lower())

    def test_submit_empty_value_rejected(self):
        self._unlock()
        self._submit("other_key", "")
        self.assertEqual(cerberus.vault_names(), [])
        self.assertIn("value", self.panel._status.cget("text").lower())


class TestLamp(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_sealed_open_alarm(self):
        lamp = CerberusLamp(self.root, bg=theme.C["bg"])
        self.assertEqual(lamp.state, "sealed")
        lamp.update({"unlocked": True, "reason": None, "alarms": []})
        self.assertEqual((lamp.state, lamp.cget("fg")), ("open", theme.C["amber"]))
        lamp.update({"unlocked": True, "reason": None, "alarms": ["vault_v1"]})
        self.assertEqual((lamp.state, lamp.cget("fg")), ("alarm", theme.C["red"]))
        lamp.update({"unlocked": False, "reason": "idle", "alarms": []})
        self.assertEqual((lamp.state, lamp.cget("fg")), ("sealed", theme.C["text3"]))
        lamp.update(None)                       # Kairos's failure signal
        self.assertEqual(lamp.state, "sealed")
        lamp.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
