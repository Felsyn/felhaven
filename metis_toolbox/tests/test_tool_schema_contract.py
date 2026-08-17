"""
test_tool_schema_contract.py — one schema shape across every brain tool.

CONVENTIONS S3 specifies what a TOOL_DEFINITION looks like, including a
description carrying "what it does + WHEN the model should call it". Nothing
enforced it, and by 2026-07-31 five of twenty-two tools had drifted: calculate,
convert_unit, edit_document, manage_timer and read_document all described
themselves without ever saying when to call them. Two of those (manage_timer,
edit_document) MUTATE state, which is the worst place to leave the model
guessing. The registry has grown 9 -> 14 -> 22; unguarded, it drifts again.

Everything here is derived from `pythia.TOOLS` and `pythia._DISPATCH` by
reflection — never a hardcoded list or count — so a tool added tomorrow is
covered the moment it registers (the lockstep-guard pattern, S8).

Two checks earn their place beyond shape:
  * no parameter may admit exactly one value (a fixed min==max, or a
    single-entry enum). Such a parameter cannot carry information; it only asks
    the model to reason about a constant. ammit's `slot` was the live case.
  * schema and handler must agree — every declared property is accepted by the
    handler, and every handler argument without a default is declared required.
    The second half is a latent TypeError: the model omits an argument the
    schema never told it was mandatory, and dispatch raises.

Run from the package root:
    python -X utf8 -m unittest tests.test_tool_schema_contract
"""

import inspect
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pythia

# "What it does + WHEN the model should call it" (S3). Deliberately broad — the
# phrasing is the author's, the presence of call guidance is the contract.
_WHEN_TO_CALL = re.compile(
    r"\b(call (this|when|it)|use (this|when)|ask(s|ed)? (about|for|what))",
    re.I,
)
_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")

# A description short enough to be useless, or long enough to be a manual.
_MIN_DESC = 80
_MAX_DESC = 600

# Tools that legitimately cannot phrase a when-to-call clause. Empty on purpose:
# all 22 conform as of 2026-07-31. Anything added here must also be justified in
# CONVENTIONS S12 — and test_when_to_call_allowlist_has_no_stale_entries will
# fail the moment an entry stops being needed, because a stale allowlist is the
# same drift this guard exists to remove.
_EXPECTED_MISSING_WHEN: set[str] = set()


def _functions():
    """(tool_name, function-schema) for every registered tool."""
    return [(t["function"]["name"], t["function"]) for t in pythia.TOOLS]


def _properties(fn):
    return (fn.get("parameters") or {}).get("properties") or {}


class TestTheGuardIsNotVacuous(unittest.TestCase):
    """Reflection that sees nothing passes for the wrong reason."""

    def test_the_registry_is_actually_populated(self):
        self.assertGreater(
            len(pythia.TOOLS), 15,
            "reflection over pythia.TOOLS found almost no tools — the registry "
            "moved or the import broke, and every test here has gone blind")

    def test_some_tool_actually_declares_parameters(self):
        with_params = [n for n, fn in _functions() if _properties(fn)]
        self.assertTrue(
            with_params,
            "no tool declares any parameter — the parameter checks below are "
            "passing over an empty set")


class TestSchemaShape(unittest.TestCase):
    def test_every_tool_has_the_standard_envelope(self):
        for tool in pythia.TOOLS:
            with self.subTest(tool=tool.get("function", {}).get("name")):
                self.assertEqual(tool.get("type"), "function")
                fn = tool["function"]
                for key in ("name", "description", "parameters"):
                    self.assertIn(key, fn, f"schema is missing {key!r} (S3)")

    def test_names_are_snake_case_and_match_the_dispatcher(self):
        for name, _fn in _functions():
            with self.subTest(tool=name):
                self.assertRegex(name, _SNAKE_CASE, "S3: snake_case verb_noun")
                self.assertIn(
                    name, pythia._DISPATCH,
                    "advertised to the model with nothing bound to handle it")

    def test_parameters_is_always_an_object_even_when_empty(self):
        # A zero-parameter tool still declares the envelope, so the model never
        # has to guess whether omission means "none" or "unspecified".
        for name, fn in _functions():
            with self.subTest(tool=name):
                params = fn.get("parameters")
                self.assertIsInstance(params, dict)
                self.assertEqual(params.get("type"), "object")
                self.assertIn("properties", params)

    def test_every_parameter_is_described(self):
        for name, fn in _functions():
            for prop, spec in _properties(fn).items():
                with self.subTest(tool=name, param=prop):
                    self.assertTrue(
                        (spec.get("description") or "").strip(),
                        "an undescribed parameter is a guess the model has to make")

    def test_required_parameters_exist(self):
        for name, fn in _functions():
            props = _properties(fn)
            for req in (fn.get("parameters") or {}).get("required") or []:
                with self.subTest(tool=name, param=req):
                    self.assertIn(
                        req, props,
                        "listed as required but never declared as a property")


class TestNoDeadParameters(unittest.TestCase):
    """A parameter with one legal value carries no information."""

    def test_no_parameter_admits_exactly_one_value(self):
        for name, fn in _functions():
            for prop, spec in _properties(fn).items():
                with self.subTest(tool=name, param=prop):
                    enum = spec.get("enum")
                    if enum is not None:
                        self.assertNotEqual(
                            len(enum), 1,
                            "single-entry enum: drop the parameter and hardcode it")
                    lo, hi = spec.get("minimum"), spec.get("maximum")
                    if lo is not None and hi is not None:
                        self.assertNotEqual(
                            lo, hi,
                            f"minimum == maximum == {lo}: the model must reason "
                            f"about a constant on every call — drop it and let "
                            f"the handler default it")


class TestSchemaMatchesHandler(unittest.TestCase):
    """The schema is a promise about a function; check the function."""

    @staticmethod
    def _signature(name):
        return inspect.signature(pythia._DISPATCH[name])

    def test_every_declared_parameter_is_accepted(self):
        for name, fn in _functions():
            sig = self._signature(name)
            takes_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD
                               for p in sig.parameters.values())
            if takes_kwargs:
                continue
            for prop in _properties(fn):
                with self.subTest(tool=name, param=prop):
                    self.assertIn(
                        prop, sig.parameters,
                        "declared to the model but the handler won't accept it — "
                        "dispatch calls fn(**arguments) and would raise TypeError")

    def test_handler_arguments_without_defaults_are_declared_required(self):
        for name, fn in _functions():
            required = set((fn.get("parameters") or {}).get("required") or [])
            for pname, p in self._signature(name).parameters.items():
                if p.kind in (inspect.Parameter.VAR_POSITIONAL,
                              inspect.Parameter.VAR_KEYWORD):
                    continue
                if p.default is not inspect.Parameter.empty:
                    continue
                with self.subTest(tool=name, param=pname):
                    self.assertIn(
                        pname, required,
                        "the handler has no default for it, but the schema does "
                        "not mark it required — the model may omit it and "
                        "dispatch raises TypeError")


class TestDescriptionsSayWhenToCall(unittest.TestCase):
    def test_descriptions_are_a_sane_length(self):
        for name, fn in _functions():
            with self.subTest(tool=name):
                n = len(fn.get("description", ""))
                self.assertGreaterEqual(
                    n, _MIN_DESC, "too short to say what it does AND when to call it")
                self.assertLessEqual(
                    n, _MAX_DESC,
                    "a schema description is billed on every request — this has "
                    "grown into a manual; move the detail onto the parameters")

    def test_every_description_says_when_to_call(self):
        missing = {name for name, fn in _functions()
                   if not _WHEN_TO_CALL.search(fn.get("description", ""))}
        unexpected = missing - _EXPECTED_MISSING_WHEN
        self.assertFalse(
            unexpected,
            f"CONVENTIONS S3: a description is 'what it does + WHEN the model "
            f"should call it'. These say only what they do: {sorted(unexpected)}")

    def test_when_to_call_allowlist_has_no_stale_entries(self):
        missing = {name for name, fn in _functions()
                   if not _WHEN_TO_CALL.search(fn.get("description", ""))}
        stale = _EXPECTED_MISSING_WHEN - missing
        self.assertFalse(
            stale,
            f"listed as exempt but now conform: {sorted(stale)} — delete them "
            f"from _EXPECTED_MISSING_WHEN")


if __name__ == "__main__":
    unittest.main()
