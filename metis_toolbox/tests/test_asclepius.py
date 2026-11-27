"""
test_asclepius.py — get_machine_status: one tool over three readers.

Asclepius hands Pythia the three readings that used to be three tools
(Hephaestus, Aether, Argus). What it owns, and so what is tested here, is the
routing and the merge: a single part comes back exactly as its module said it,
`all` merges flat, one failed part never blanks the others, and nothing raises.
The three modules' own readings have their own suites; here their handle()s
are patched, so no psutil, no network and no netsh are touched.

    python -X utf8 -m unittest tests.test_asclepius
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pythia
from tools import asclepius

_VITALS = {"cpu": {"usage_percent": 12.0}, "memory": {"percent_used": 40.0},
           "uptime_s": 3600}
_ONLINE = {"wifi": "connected", "internet": "online", "ollama": "online"}
_NETWORK = {"established": 9, "listening": 4, "firewall": {"state": "on"}}

# The three names get_machine_status replaced (CONVENTIONS §12, 2026-11-14).
_RETIRED = ("get_system_vitals", "get_connectivity", "get_network_summary")


class _Patched(unittest.TestCase):
    """Each part's module handle() replaced by a canned reading."""

    def setUp(self):
        self.vitals = self.patch("tools.hephaestus.handle", _VITALS)
        self.online = self.patch("tools.aether.handle", _ONLINE)
        self.network = self.patch("tools.argus.handle", _NETWORK)

    def patch(self, target, value):
        m = mock.Mock(return_value=dict(value))
        p = mock.patch(target, m)
        p.start()
        self.addCleanup(p.stop)
        return m


class TestOnePart(_Patched):

    def test_each_part_asks_only_its_module(self):
        cases = (("vitals", _VITALS, self.vitals),
                 ("online", _ONLINE, self.online),
                 ("network", _NETWORK, self.network))
        for part, reading, called in cases:
            with self.subTest(part=part):
                for m in (self.vitals, self.online, self.network):
                    m.reset_mock()
                self.assertEqual(asclepius.handle(part), reading)
                self.assertEqual(called.call_count, 1)
                others = {self.vitals, self.online, self.network} - {called}
                self.assertTrue(all(m.call_count == 0 for m in others))

    def test_vitals_never_waits_on_the_internet_probe(self):
        # The reason `part` exists: Aether can wait 5 s for the internet.
        asclepius.handle("vitals")
        self.online.assert_not_called()

    def test_a_part_error_comes_back_unchanged(self):
        self.vitals.return_value = {"error": "unavailable: vitals read raised OSError"}
        self.assertEqual(asclepius.handle("vitals"),
                         {"error": "unavailable: vitals read raised OSError"})

    def test_a_raising_part_is_unavailable_not_a_crash(self):
        self.network.side_effect = OSError("netsh exploded at C:\\secret")
        with self.assertLogs("METIS.asclepius", "WARNING"):
            out = asclepius.handle("network")
        self.assertTrue(out["error"].startswith("unavailable: "))
        self.assertIn("OSError", out["error"])
        self.assertNotIn("secret", out["error"])   # the message stays in the log

    def test_a_part_that_returns_no_dict_is_unavailable(self):
        self.online.return_value = None
        with self.assertLogs("METIS.asclepius", "WARNING"):
            self.assertTrue(asclepius.handle("online")["error"]
                            .startswith("unavailable: "))

    def test_a_part_outside_the_enum_is_unknown_action(self):
        out = asclepius.handle("cpu")
        self.assertTrue(out["error"].startswith("unknown_action: "))
        for part in ("vitals", "online", "network", "all"):
            self.assertIn(part, out["error"])
        for m in (self.vitals, self.online, self.network):
            m.assert_not_called()


class TestAll(_Patched):

    def test_all_merges_flat(self):
        self.assertEqual(asclepius.handle("all"),
                         {**_VITALS, **_ONLINE, **_NETWORK})

    def test_one_failed_part_is_one_key_and_the_rest_survive(self):
        self.network.return_value = {"error": "unavailable: network snapshot raised OSError"}
        out = asclepius.handle("all")
        self.assertNotIn("error", out)                 # T6: success has none
        self.assertEqual(out["network"], "unavailable: network snapshot raised OSError")
        self.assertEqual(out["cpu"], _VITALS["cpu"])
        self.assertEqual(out["internet"], "online")

    def test_a_failure_reason_survives_the_merge(self):
        # The part's own words, not a generic "failed": they are what the
        # model can repeat to the user.
        self.vitals.side_effect = RuntimeError("boom")
        with self.assertLogs("METIS.asclepius", "WARNING"):
            out = asclepius.handle("all")
        self.assertEqual(out["vitals"], "unavailable: vitals raised RuntimeError")

    def test_every_part_failed_is_an_error(self):
        self.vitals.return_value = {"error": "unavailable: vitals read raised OSError"}
        self.online.side_effect = ValueError("x")
        self.network.return_value = {"error": "unavailable: network snapshot raised OSError"}
        with self.assertLogs("METIS.asclepius", "WARNING"):
            out = asclepius.handle("all")
        self.assertEqual(set(out), {"error"})
        self.assertTrue(out["error"].startswith("unavailable: "))
        self.assertIn("vitals read raised OSError", out["error"])
        self.assertIn("network snapshot raised OSError", out["error"])

    def test_colliding_keys_nest_instead_of_overwriting(self):
        self.network.return_value = {"uptime_s": 1, "established": 2}
        with self.assertLogs("METIS.asclepius", "WARNING"):
            out = asclepius.handle("all")
        self.assertEqual(out["uptime_s"], 3600)        # vitals' value kept
        self.assertEqual(out["network"], {"uptime_s": 1, "established": 2})


class TestRegistration(unittest.TestCase):

    def test_registered_and_dispatched_to_handle(self):
        self.assertIs(pythia._DISPATCH["get_machine_status"], asclepius.handle)

    def test_the_retired_names_are_gone(self):
        # T10: a retired tool name never comes back as an alias.
        for name in _RETIRED:
            with self.subTest(name=name):
                self.assertNotIn(name, pythia._DISPATCH)

    def test_the_three_readers_are_no_longer_brain_tools(self):
        from tools import aether, argus, hephaestus
        registered = set(pythia._TOOL_MODULES)
        for module in (hephaestus, aether, argus):
            with self.subTest(module=module.__name__):
                self.assertNotIn(module, registered)
                self.assertFalse(hasattr(module, "TOOL_DEFINITION"))

    def test_the_enum_is_the_parts_plus_all(self):
        part = asclepius.TOOL_DEFINITION["function"]["parameters"]["properties"]["part"]
        self.assertEqual(part["enum"], ["vitals", "online", "network", "all"])

    def test_the_gate_refuses_a_call_with_no_part(self):
        with self.assertLogs("METIS.pythia", "WARNING"):
            out = pythia._dispatch("get_machine_status", {})
        self.assertTrue(out["error"].startswith("bad_arguments"))

    def test_nothing_points_at_anthropic(self):
        # Moved from test_aether when get_connectivity was retired: Pythia is
        # gemma4 on a local Ollama, and the old probe was a Claude-API leftover.
        desc = asclepius.TOOL_DEFINITION["function"]["description"].lower()
        for word in ("anthropic", "claude"):
            self.assertNotIn(word, desc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
