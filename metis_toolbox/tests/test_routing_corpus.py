"""
test_routing_corpus.py — the routing probe's phrasings name tools that exist.

tests/fixtures/broker_routing.json maps a phrasing to the tool(s) Pythia should
call. tests/probe_routing.py plays it against the live model by hand, because
which tool a model picks can only be measured with the model. What CAN be
checked without it is that the file still describes the registry: a tool that
gets merged or renamed must take its probe rows with it, or the next baseline
silently scores every one of them as a miss.

So this guard holds the file's shape and its names, never a model's answer:
unique ids in order, every expected tool registered in pythia._DISPATCH, and
every `args` key a parameter that tool really has.

    python -X utf8 -m unittest tests.test_routing_corpus
"""

import json
import os
import unittest

import pythia

_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "broker_routing.json")
_ROW_KEYS = {"id", "prompt", "expect", "exact", "args", "known_miss", "domain",
             "history"}


def load_probes():
    """The probe rows, oldest first. Shared with tests/probe_routing.py."""
    with open(_FIXTURE, encoding="utf-8") as f:
        return json.load(f)["probes"]


class RoutingCorpusTests(unittest.TestCase):

    def setUp(self):
        self.probes = load_probes()

    def test_ids_are_one_to_n(self):
        ids = [p["id"] for p in self.probes]
        self.assertEqual(ids, list(range(1, len(ids) + 1)))

    def test_rows_use_only_known_keys(self):
        for p in self.probes:
            with self.subTest(id=p["id"]):
                self.assertLessEqual(set(p), _ROW_KEYS)
                self.assertTrue(p["prompt"].strip())
                self.assertIsInstance(p["expect"], list)
                self.assertTrue(p["domain"].strip())
                self.assertIsInstance(p.get("exact", False), bool)
                self.assertIsInstance(p.get("known_miss", False), bool)

    def test_history_is_plain_alternating_turns(self):
        # Plain {"role", "content"} pairs, user first, ending on the assistant,
        # so the probe's phrasing is always the next user turn.
        for p in self.probes:
            history = p.get("history")
            if history is None:
                continue
            with self.subTest(id=p["id"]):
                self.assertTrue(history)
                roles = [m["role"] for m in history]
                self.assertEqual(roles, ["user", "assistant"] * (len(roles) // 2))
                for m in history:
                    self.assertEqual(set(m), {"role", "content"})
                    self.assertTrue(m["content"].strip())

    def test_every_expected_tool_is_registered(self):
        for p in self.probes:
            for name in p["expect"]:
                with self.subTest(id=p["id"], tool=name):
                    self.assertIn(name, pythia._DISPATCH,
                                  f"probe {p['id']} expects {name!r}, which "
                                  f"is not a registered tool; update the row")

    def test_args_name_real_parameters(self):
        for p in self.probes:
            args = p.get("args") or {}
            if not args:
                continue
            with self.subTest(id=p["id"]):
                self.assertEqual(len(p["expect"]), 1,
                                 "args apply to a single expected tool")
                props = pythia._SCHEMAS[p["expect"][0]].get("properties", {})
                self.assertLessEqual(set(args), set(props))
                # A list means "any one of these"; each must be a value the
                # parameter can take, or the row can never pass.
                for key, want in args.items():
                    accepted = want if isinstance(want, list) else [want]
                    self.assertTrue(accepted)
                    enum = props[key].get("enum")
                    if enum:
                        self.assertLessEqual(set(accepted), set(enum))

    def test_verdict_accepts_any_listed_value(self):
        from tests.probe_routing import _verdict
        row = {"expect": ["get_machine_status"],
               "args": {"part": ["vitals", "all"]}}
        for part, verdict in (("vitals", "pass"), ("all", "pass"),
                              ("network", "ARGS")):
            with self.subTest(part=part):
                calls = [("get_machine_status", {"part": part})]
                self.assertEqual(_verdict(row, calls), verdict)
        self.assertEqual(_verdict(row, []), "MISS")


if __name__ == "__main__":
    unittest.main(verbosity=2)
