"""
test_zeno.py — the AST whitelist, the magnitude guards, and the error vocabulary.

Zeno shipped 2026-05-28 with no test at all, which is the wrong place for a
*whitelist* to be: the whole safety argument is "these node types and no
others", and nothing was checking the "no others" half. So the rejection table
below is the centre of this file — `__import__`, attribute access, subscripts,
comprehensions and bitwise operators each get a named case, and each asserts the
identifier Zeno hands back rather than merely that it failed.

Hermetic by construction: Zeno is pure stdlib arithmetic — no clock, no
network, no filesystem, no config — so nothing here is mocked or patched except
`_MAX_REDUCTIONS`, which is lowered to reach the reduction cap without building
an expression deep enough to blow Python's own recursion limit.

Run from the package root:
    python -X utf8 -m unittest tests.test_zeno
"""

import logging
import os
import re
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import zeno

# Several cases below deliberately reach handle()'s "unforeseen failure" branch,
# which logs at ERROR. No test entry point calls setup_logging (§5 — only real
# entry points do), so logging's last-resort handler would print each one to
# stderr and bury the suite's own output. A NullHandler is enough to stop that.
logging.getLogger("METIS.zeno").addHandler(logging.NullHandler())

# CONVENTIONS S2: a bare slug, or a slug followed by human detail.
_SLUG = re.compile(r"^[a-z][a-z0-9_]*(: .+)?$", re.DOTALL)


class TestWhitelistAllows(unittest.TestCase):
    """Everything the docstring promises actually evaluates."""

    _CASES = [
        # operators
        ("2 + 2", "4"), ("7 - 9", "-2"), ("6 * 7", "42"),
        ("7 / 2", "3.5"), ("7 // 2", "3"), ("7 % 3", "1"), ("2 ** 10", "1024"),
        ("-5", "-5"), ("+5", "5"), ("not 0", "True"),
        # comparisons, including a chain
        ("1 < 2", "True"), ("1 < 2 < 3", "True"), ("3 <= 3", "True"),
        ("2 > 1", "True"), ("2 >= 3", "False"),
        ("1 == 1", "True"), ("1 != 2", "True"),
        # boolean operators
        ("True and False", "False"), ("True or False", "True"),
        # named constants
        ("pi > 3", "True"), ("e > 2", "True"), ("tau > 6", "True"),
        ("inf > 0", "True"),
        # functions, at least one from every group in _FUNCTIONS
        ("sin(0)", "0"), ("cos(0)", "1"), ("tan(0)", "0"),
        ("asin(0)", "0"), ("acos(1)", "0"), ("atan(0)", "0"),
        ("atan2(0, 1)", "0"),
        ("sinh(0)", "0"), ("cosh(0)", "1"), ("tanh(0)", "0"),
        ("log(e)", "1"), ("log2(8)", "3"), ("log10(1000)", "3"),
        ("exp(0)", "1"),
        ("sqrt(144)", "12"), ("floor(2.7)", "2"), ("ceil(2.1)", "3"),
        ("factorial(5)", "120"), ("gcd(12, 18)", "6"),
        ("degrees(pi)", "180"), ("radians(180) == pi", "True"),
        ("abs(-3)", "3"), ("round(2.4)", "2"),
        ("min(3, 1, 2)", "1"), ("max(3, 1, 2)", "3"), ("sum(1, 2, 3)", "6"),
        # nesting and precedence
        ("(2 + 3) * 4", "20"), ("2 + 3 * 4", "14"), ("2 ** 3 ** 2", "512"),
        ("(2 ** 3) ** 2", "64"), ("-2 ** 2", "-4"), ("(-2) ** 2", "4"),
        ("2 ** -2", "0.25"),
    ]

    def test_every_whitelisted_form_evaluates(self):
        for expr, expected in self._CASES:
            with self.subTest(expr=expr):
                out = zeno.handle(expr)
                self.assertNotIn("error", out, out.get("error"))
                self.assertEqual(out["display"], expected)

    def test_ans_substitutes_the_previous_result(self):
        out = zeno.handle("ans + 1", ans=10.0)
        self.assertEqual(out["display"], "11")

    def test_ans_of_zero_is_a_value_not_a_missing_result(self):
        # `if val is None`, not `if not val` — a previous result of 0 is real.
        out = zeno.handle("ans + 1", ans=0.0)
        self.assertEqual(out["display"], "1")


class TestWhitelistRejects(unittest.TestCase):
    """The "no others" half of the whitelist — the sandbox's actual job.

    Every case names the identifier, not just "some error": a rejection that
    silently changed slug would still pass a truthiness check, and the slug is
    what the panel and the model branch on.
    """

    _CASES = [
        # the ones that matter: reaching out of the arithmetic sandbox
        ("__import__('os')",        "unknown_function: __import__"),
        ("open('secrets.txt')",     "unknown_function: open"),
        ("eval('1+1')",             "unknown_function: eval"),
        ("().__class__",            "unsupported_operation: Attribute"),
        ("hypot(3, 4)",             "unknown_function: hypot"),
        ("os",                      "unknown_name: os"),
        # syntax Zeno deliberately has no reducer for
        ("[1, 2]",                  "unsupported_operation: List"),
        ("(1, 2)",                  "unsupported_operation: Tuple"),
        ("{1: 2}",                  "unsupported_operation: Dict"),
        ("[x for x in y]",          "unsupported_operation: ListComp"),
        ("lambda: 1",               "unsupported_operation: Lambda"),
        ("1 if 2 else 3",           "unsupported_operation: IfExp"),
        ("'text'",                  "unsupported_operation: str"),
        ("None",                    "unsupported_operation: NoneType"),
        ("1j",                      "unsupported_operation: complex"),
        # operators outside the arithmetic set
        ("1 | 2",                   "unsupported_operation: BitOr"),
        ("1 & 2",                   "unsupported_operation: BitAnd"),
        ("1 ^ 2",                   "unsupported_operation: BitXor"),
        ("1 << 2",                  "unsupported_operation: LShift"),
        ("~1",                      "unsupported_operation: Invert"),
        ("1 is 2",                  "unsupported_operation: Is"),
        ("1 in 2",                  "unsupported_operation: In"),
        # a whitelisted function, called in a way the reducer does not accept
        ("round(2.567, ndigits=2)", "unsupported_operation: keyword arguments"),
        # not parseable at all
        ("2 +",                     "parse_error"),
        ("",                        "parse_error"),
        ("1; 2",                    "parse_error"),
    ]

    def test_every_rejection_returns_its_identifier(self):
        for expr, expected in self._CASES:
            with self.subTest(expr=expr):
                self.assertEqual(zeno.handle(expr).get("error"), expected)

    def test_an_oversized_int_literal_is_a_parse_error(self):
        # CPython's own parser enforces the int<->str digit cap, so a literal
        # this big never reaches _validate. The docstring claims that; pin it.
        self.assertEqual(zeno.handle("1" + "0" * 5000).get("error"), "parse_error")

    def test_ans_without_a_previous_result(self):
        self.assertEqual(zeno.handle("ans + 1").get("error"), "no_previous_result")


class TestArithmeticFailures(unittest.TestCase):
    """Failures from real numbers rather than from the whitelist."""

    def test_division_by_zero(self):
        for expr in ("1 / 0", "1 // 0", "1 % 0", "0 ** -1"):
            with self.subTest(expr=expr):
                self.assertEqual(zeno.handle(expr).get("error"), "division_by_zero")

    def test_math_domain_errors_do_not_leak_the_exception_message(self):
        # Was `str(exc)` — "math domain error" straight out of CPython. S2 bans
        # raw exception text everywhere, not only where it could carry a path.
        for expr in ("sqrt(-1)", "log(0)", "log(-1)", "asin(2)", "factorial(-1)"):
            with self.subTest(expr=expr):
                self.assertEqual(zeno.handle(expr).get("error"), "math_domain_error")

    def test_a_bad_argument_count_is_reported_as_the_exception_class(self):
        out = zeno.handle("min()")
        self.assertEqual(out.get("error"), "evaluation_failed: TypeError")

    def test_non_finite_results_display_rather_than_error(self):
        self.assertEqual(zeno.handle("inf + inf")["display"], "inf")
        self.assertEqual(zeno.handle("inf - inf")["display"], "nan")
        self.assertEqual(zeno.handle("-inf")["display"], "-inf")


class TestMagnitudeGuards(unittest.TestCase):
    """The two hazards the docstring separates: compute hang and display size."""

    def test_huge_exponent_is_refused_before_computing(self):
        self.assertEqual(zeno.handle("2 ** 100000000").get("error"), "result_too_large")

    def test_huge_base_with_a_small_exponent_is_refused(self):
        # The bit_length * exponent branch, not the |exponent| branch.
        self.assertEqual(zeno.handle("(10 ** 100) ** 1000").get("error"),
                         "result_too_large")

    def test_huge_factorial_is_refused_before_computing(self):
        self.assertEqual(zeno.handle("factorial(999999999)").get("error"),
                         "result_too_large")

    def test_cheap_to_compute_but_too_big_to_display(self):
        # Passes both pre-compute guards and is caught by _too_large afterwards.
        self.assertEqual(zeno.handle("10 ** 5000").get("error"), "result_too_large")
        self.assertEqual(zeno.handle("factorial(10000)").get("error"),
                         "result_too_large")

    def test_overflow_from_a_math_function(self):
        self.assertEqual(zeno.handle("exp(100000)").get("error"), "result_too_large")

    def test_large_but_displayable_still_works(self):
        self.assertEqual(zeno.handle("2 ** 100")["display"], str(2 ** 100))

    def test_the_guards_return_promptly(self):
        # The point of the pre-compute guards is that these do not hang. The
        # budget is deliberately loose (CI is a shared Windows runner); without
        # the guards `factorial(999999999)` does not finish at all.
        started = time.monotonic()
        for expr in ("9 ** 9 ** 9 ** 9", "2 ** 100000000",
                     "999999999999999 ** 999999999999999",
                     "factorial(999999999)"):
            self.assertEqual(zeno.handle(expr).get("error"), "result_too_large")
        self.assertLess(time.monotonic() - started, 10.0,
                        "the magnitude guards are no longer short-circuiting")


class TestReductionCap(unittest.TestCase):
    """The runaway-loop backstop, exercised rather than assumed.

    Lowering _MAX_REDUCTIONS is the only patch in this file: reaching the real
    cap of 500 needs a 500-deep tree, and _unparse/_find_reducible recurse once
    per level, so the expression that proves the cap would trip Python's
    recursion limit first and prove something else.
    """

    def setUp(self):
        self._saved = zeno._MAX_REDUCTIONS

    def tearDown(self):
        zeno._MAX_REDUCTIONS = self._saved

    def test_running_out_of_reductions_is_reported_not_answered(self):
        zeno._MAX_REDUCTIONS = 2
        out = zeno.handle("1 + 1 + 1 + 1 + 1")
        self.assertEqual(out.get("error"), "evaluation_stalled")
        self.assertNotIn("result", out)

    def test_the_cap_is_not_hit_by_ordinary_expressions(self):
        zeno._MAX_REDUCTIONS = self._saved
        self.assertNotIn("error", zeno.handle("1 + 1 + 1 + 1 + 1"))


class TestSteps(unittest.TestCase):
    """The step readout is the feature; an answer without it is a calculator."""

    def test_first_step_is_the_expression_and_last_is_the_answer(self):
        out = zeno.handle("(2 + 3) * 4")
        self.assertEqual(out["steps"][0], "(2 + 3) * 4")
        self.assertEqual(out["steps"][-1], out["display"])

    def test_every_node_is_resolved_one_at_a_time(self):
        self.assertEqual(zeno.handle("(2 + 3) * 4")["steps"],
                         ["(2 + 3) * 4", "5 * 4", "20"])

    def test_failed_expressions_keep_the_steps_taken_so_far(self):
        out = zeno.handle("(2 + 3) / 0")
        self.assertEqual(out["error"], "division_by_zero")
        self.assertEqual(out["steps"], ["(2 + 3) / 0", "5 / 0"])

    def test_a_rejected_expression_has_no_steps(self):
        # _validate runs before the first snapshot, so nothing is shown.
        self.assertEqual(zeno.handle("[1, 2]")["steps"], [])

    def test_parenthesisation_survives_a_reduction(self):
        # -2 reduced under a ** must print as (-2) ** 2, not -2 ** 2, which
        # Python reads as -(2 ** 2) — the step readout would be a lie.
        self.assertIn("(-2) ** 2", zeno.handle("(-2) ** 2")["steps"])


class TestFormatNumber(unittest.TestCase):
    """The public display seam, shared with zeno_panel's ANS readout."""

    def test_float_noise_is_hidden(self):
        self.assertEqual(zeno.format_number(0.1 + 0.2), "0.3")

    def test_whole_floats_print_as_integers(self):
        self.assertEqual(zeno.format_number(12.0), "12")

    def test_six_significant_figures(self):
        self.assertEqual(zeno.format_number(100 / 3), "33.3333")

    def test_small_values_survive(self):
        self.assertEqual(zeno.format_number(1e-8), "1e-08")

    def test_non_finite(self):
        self.assertEqual(zeno.format_number(float("nan")), "nan")
        self.assertEqual(zeno.format_number(float("inf")), "inf")
        self.assertEqual(zeno.format_number(float("-inf")), "-inf")

    def test_bools_stay_bools(self):
        # bool is an int subclass; without the guard True would print as 1.
        self.assertEqual(zeno.format_number(True), "True")

    def test_big_ints_get_a_placeholder_rather_than_raising(self):
        # Defence for direct callers (the panel formatting a stored ans);
        # handle() rejects these upstream via _too_large.
        self.assertEqual(zeno.format_number(10 ** 5000), "<16610-bit integer>")


class TestDocumentedDeviationsFromPython(unittest.TestCase):
    """Zeno is a visualiser, not an interpreter.

    Each of these is listed under `Semantics:` in the module docstring as a
    deliberate choice. Pinning them means a future change has to be a decision
    rather than an accident — and if one is ever changed on purpose, the
    docstring and this class fail together.
    """

    def test_and_or_do_not_short_circuit(self):
        self.assertEqual(zeno.handle("False and (1 / 0)").get("error"),
                         "division_by_zero")

    def test_bool_flows_through_arithmetic_as_int(self):
        self.assertEqual(zeno.handle("True + True")["display"], "2")

    def test_round_uses_bankers_rounding(self):
        self.assertEqual(zeno.handle("round(2.5)")["display"], "2")
        self.assertEqual(zeno.handle("round(3.5)")["display"], "4")

    def test_sum_takes_positional_arguments(self):
        # The builtin sum() wants an iterable, and Zeno rejects list syntax, so
        # the bare builtin would be uncallable here. _sum_args is the wrapper.
        self.assertEqual(zeno.handle("sum(1, 2, 3)")["display"], "6")


class TestHandleNeverRaises(unittest.TestCase):
    """CONVENTIONS S2: a brain tool that throws crashes Pythia's dispatcher."""

    _HOSTILE = [
        "", "   ", "\x00", "1 +", ")" * 50, "(" * 60 + "1" + ")" * 60,
        "1 " * 400, "+" * 100, "sqrt", "sqrt(", "sqrt()", "round(1, 2, 3)",
        "factorial(1.5)", "gcd(1.5, 2)", "min()", "log(2, 0)", "log(2, 1)",
        "0 ** -1", "inf * 0", "ans", "ans ** ans", "2 ** 2 ** 2 ** 2 ** 2",
        "1 + " * 300 + "1",
    ]

    def test_hostile_input_always_returns_a_dict(self):
        for expr in self._HOSTILE:
            with self.subTest(expr=expr[:30]):
                out = zeno.handle(expr)
                self.assertIsInstance(out, dict)
                self.assertEqual(out["expression"], expr)
                self.assertTrue("error" in out or "display" in out)

    def test_a_non_string_expression_is_still_a_dict(self):
        # Pythia hands over whatever the model produced; a wrong type must not
        # escape as a TypeError.
        for bad in (None, 42, ["1 + 1"]):
            with self.subTest(value=bad):
                self.assertIn("error", zeno.handle(bad))  # type: ignore[arg-type]


class TestErrorVocabulary(unittest.TestCase):
    """Every identifier Zeno emits is a S2 slug.

    tests/test_tool_output_contract.py proves this statically over the source;
    this proves it over the values that actually come back, which is the half
    an AST walk cannot see.
    """

    _EVERY_FAILURE = (
        [expr for expr, _ in TestWhitelistRejects._CASES]
        + ["ans + 1", "1 / 0", "sqrt(-1)", "min()", "2 ** 100000000",
           "factorial(999999999)", "10 ** 5000", "exp(100000)"]
    )

    def test_no_identifier_is_a_sentence(self):
        for expr in self._EVERY_FAILURE:
            with self.subTest(expr=expr):
                code = zeno.handle(expr)["error"]
                self.assertRegex(code, _SLUG)
                self.assertNotIn(" ", code.split(": ")[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
