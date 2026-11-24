"""
test_tool_meta_contract.py — every brain tool says its domain, network use
and whether it changes anything, in a TOOL_META beside its schema.

CONVENTIONS §8 ("TOOL_META") and §12 (2026-11-16). The schema is what the
model reads; TOOL_META is what the code reads about a tool: which capability
it belongs to (`domain`), whether a call can reach the network (`network`),
and whether it changes state outside the turn (`mutates`). Nothing routes on
it yet. It exists so that a Tool Broker, a module-intro post or a reviewer
can ask "which tools touch the network?" of the code instead of someone's
memory (V0.03 audit M6).

Everything is derived by reflection from `pythia._TOOL_MODULES`, never a
hardcoded list of tools, so a tool added tomorrow must bring its meta with it.
The allowed values ARE listed here, on purpose: a new domain is a decision,
and this file is where it is made visible.

Run from the package root:
    python -X utf8 -m unittest tests.test_tool_meta_contract
"""

import inspect
import os
import re
import sys
import unittest
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pythia

# The owner's vocabulary (§12, 2026-11-16): what the model needs, not where a
# panel shows it. Moderati shows both `machine` and `codex`; `web` and
# `archive` have no panel at all.
_DOMAINS = {"time", "weather", "sky", "machine", "markets", "news", "math",
            "music", "web", "archive", "codex"}
# none = local only; cached = answers from a poll or TTL cache, refetching
# when it runs out; live = may go to the network on every call.
_NETWORK = {"none", "cached", "live"}
# `keywords` waits for a reader (§12, 2026-11-16): unchecked, it would drift.
_FIELDS = {"domain", "network", "mutates"}


def _modules() -> list[tuple[Any, list[str]]]:
    """(module, [its tool names]) for every registered brain module."""
    return [(mod, [d["function"]["name"] for d, _ in pythia._module_tools(mod)])
            for mod in pythia._TOOL_MODULES]


def _meta() -> dict[str, dict[str, Any]]:
    """Every tool's meta, by name, read from its own module."""
    out: dict[str, dict[str, Any]] = {}
    for mod, names in _modules():
        table = getattr(mod, "TOOL_META", {})
        for name in names:
            if name in table:
                out[name] = table[name]
    return out


def _texts(definition: dict[str, Any]) -> list[str]:
    """The model-facing prose of one schema: its description and each
    parameter's. An enum's VALUES are not prose (Daedalus's are module keys)."""
    fn = definition["function"]
    props = fn.get("parameters", {}).get("properties", {})
    return [fn.get("description", "")] + [
        str(p.get("description", "")) for p in props.values()]


class TestEveryToolHasMeta(unittest.TestCase):
    def test_every_brain_module_has_a_tool_meta_dict(self):
        for mod, _names in _modules():
            with self.subTest(module=mod.__name__):
                self.assertIsInstance(
                    getattr(mod, "TOOL_META", None), dict,
                    "a brain module needs TOOL_META beside its schema, keyed by "
                    "tool name (CONVENTIONS §8)")

    def test_every_registered_tool_has_meta(self):
        missing = {n for n in pythia._DISPATCH} - set(_meta())
        self.assertFalse(missing, f"tools with no TOOL_META entry: {sorted(missing)}")

    def test_no_meta_for_a_tool_the_module_does_not_register(self):
        # A retired tool's meta left behind is the drift §8 warns about.
        for mod, names in _modules():
            with self.subTest(module=mod.__name__):
                extra = set(getattr(mod, "TOOL_META", {})) - set(names)
                self.assertFalse(
                    extra, f"TOOL_META names tools this module doesn't "
                    f"register: {sorted(extra)}")


class TestMetaValues(unittest.TestCase):
    def test_exactly_the_three_fields(self):
        for name, meta in _meta().items():
            with self.subTest(tool=name):
                self.assertEqual(set(meta), _FIELDS)

    def test_domain_is_one_of_the_owners_words(self):
        for name, meta in _meta().items():
            with self.subTest(tool=name):
                self.assertIn(meta["domain"], _DOMAINS,
                              "a new domain is a decision: add it to _DOMAINS "
                              "and record it in CONVENTIONS §12")

    def test_every_domain_is_used(self):
        # Both directions, the lockstep pattern: a domain no tool claims any
        # more is a stale word in the vocabulary.
        unused = _DOMAINS - {m["domain"] for m in _meta().values()}
        self.assertFalse(unused, f"domains no tool uses: {sorted(unused)}")

    def test_network_is_none_cached_or_live(self):
        for name, meta in _meta().items():
            with self.subTest(tool=name):
                self.assertIn(meta["network"], _NETWORK)

    def test_mutates_is_a_bool(self):
        for name, meta in _meta().items():
            with self.subTest(tool=name):
                self.assertIs(type(meta["mutates"]), bool)


class TestMetaAgreesWithTheCode(unittest.TestCase):
    def test_a_tool_that_asks_first_is_marked_as_changing_something(self):
        # The gate's confirm list and the meta answer the same question.
        meta = _meta()
        for name in pythia._CONFIRM_TOOLS:
            with self.subTest(tool=name):
                self.assertTrue(meta[name]["mutates"])

    def test_a_module_that_imports_hermes_marks_no_tool_network_none(self):
        # tools/hermes.py is the one door to the network (test_network_reads).
        # One direction only: a tool can reach the network without Hermes
        # (play_music's yt-dlp, Asclepius through Aether), so this catches a
        # stale "none", not every wrong answer.
        hermes_import = re.compile(r"^\s*(from tools import [^\n]*\bhermes\b"
                                   r"|import tools\.hermes|from tools\.hermes import)",
                                   re.M)
        meta = _meta()
        for mod, names in _modules():
            if not hermes_import.search(inspect.getsource(mod)):
                continue
            for name in names:
                with self.subTest(tool=name):
                    self.assertNotEqual(meta[name]["network"], "none")

    def test_a_description_names_only_tools_in_its_own_domain(self):
        # V0.03 audit T5. Under a Broker that loads one domain at a time,
        # naming a tool from another domain invites a call the gate refuses.
        meta = _meta()
        names = set(meta)
        for mod, own in _modules():
            for definition, _ in pythia._module_tools(mod):
                tool = definition["function"]["name"]
                prose = " ".join(_texts(definition))
                for other in names - {tool}:
                    if re.search(rf"\b{re.escape(other)}\b", prose):
                        with self.subTest(tool=tool, names=other):
                            self.assertEqual(meta[tool]["domain"],
                                             meta[other]["domain"])


if __name__ == "__main__":
    unittest.main()
