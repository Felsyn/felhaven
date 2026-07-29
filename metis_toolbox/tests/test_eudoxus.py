"""
test_eudoxus.py — the conversion tables, the expression parser, and the error
vocabulary.

Eudoxus shipped 2026-05-30 with no test at all. For a module that is nothing
but a table of constants, the interesting question is not "does it multiply"
but "are the constants right", so the centre of this file is
TestDefinitionalRatios: it asserts the relationships US customary units are
*defined* by — a gallon is 4 quarts is 8 pints is 16 cups, a mile is 5280 feet
— rather than re-deriving expected values from the same table under test. A
mistyped constant fails there; a test built from the table could not see it.

Hermetic by construction: Eudoxus is pure stdlib arithmetic — no clock, no
network, no filesystem, no config — so nothing here is mocked or patched.

Run from the package root:
    python -X utf8 -m unittest tests.test_eudoxus
"""

import logging
import math
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import eudoxus, zeno

# A few cases below deliberately reach the "unexpected failure" branch, which
# logs at ERROR. No test entry point calls setup_logging (§5 — only real entry
# points do), so logging's last-resort handler would print each one to stderr
# and bury the suite's own output.
logging.getLogger("METIS.eudoxus").addHandler(logging.NullHandler())

# CONVENTIONS S2: a bare slug, or a slug followed by human detail.
_SLUG = re.compile(r"^[a-z][a-z0-9_]*(: .+)?$", re.DOTALL)


class TestDefinitionalRatios(unittest.TestCase):
    """The ratios the units are defined by, not values read back off the table.

    Each case is a fact about the world (16 cups to the US gallon), so a
    mistyped table constant has nowhere to hide. Asserted through handle() so
    the arithmetic path is exercised at the same time.
    """

    _CASES = [
        # volume — US customary
        (1, "gallon", "quart",  4), (1, "gallon", "pint",  8),
        (1, "gallon", "cup",   16), (1, "gallon", "fl_oz", 128),
        (1, "quart",  "pint",   2), (1, "pint",   "cup",   2),
        (1, "cup",    "fl_oz",  8), (1, "cup",    "tbsp",  16),
        (1, "cup",    "tsp",   48), (1, "tbsp",   "tsp",   3),
        (1, "l",      "ml",  1000),
        # length
        (1, "mi", "ft", 5280), (1, "mi", "yd", 1760), (1, "yd", "ft", 3),
        (1, "ft", "in",   12), (1, "km", "m",  1000), (1, "m",  "cm", 100),
        (1, "cm", "mm",   10), (1, "m",  "mm", 1000),
        # weight
        (1, "lb", "oz", 16), (1, "st", "lb", 14), (1, "kg", "g", 1000),
        (1, "g",  "mg", 1000),
        # time
        (1, "min", "s",   60), (1, "hr",  "min", 60), (1, "hr", "s", 3600),
        (1, "day", "hr",  24), (1, "week", "day", 7),
    ]

    def test_every_definitional_ratio_holds(self):
        for value, src, dst, expected in self._CASES:
            with self.subTest(f"{value} {src} -> {dst}"):
                out = eudoxus.handle(value, src, dst)
                self.assertNotIn("error", out, out.get("error"))
                self.assertAlmostEqual(out["result"], expected, places=9)

    def test_the_exact_SI_definitions(self):
        # Exact by international agreement, not approximations.
        self.assertAlmostEqual(eudoxus.handle(1, "in", "cm")["result"], 2.54, places=12)
        self.assertAlmostEqual(eudoxus.handle(1, "mi", "km")["result"], 1.609344, places=12)
        self.assertAlmostEqual(eudoxus.handle(1, "lb", "g")["result"], 453.59237, places=9)
        self.assertAlmostEqual(eudoxus.handle(1, "gallon", "l")["result"], 3.785411784, places=12)

    def test_us_customary_not_imperial(self):
        # The docstring's headline promise: an Imperial gallon is 4.546 L and a
        # metric cup is 250 mL. Eudoxus must be neither.
        self.assertAlmostEqual(eudoxus.handle(1, "gallon", "ml")["result"], 3785.411784, places=6)
        self.assertAlmostEqual(eudoxus.handle(1, "cup", "ml")["result"], 236.5882365, places=7)

    def test_a_round_trip_returns_the_original(self):
        for src, dst in [("mi", "km"), ("lb", "kg"), ("gallon", "l"),
                         ("week", "s"), ("f", "c"), ("c", "k")]:
            with self.subTest(f"{src} <-> {dst}"):
                there = eudoxus.handle(7, src, dst)["result"]
                back  = eudoxus.handle(there, dst, src)["result"]
                self.assertAlmostEqual(back, 7, places=9)


class TestTemperature(unittest.TestCase):
    """Affine, so it is the one category that cannot be a ratio."""

    def test_the_reference_points(self):
        cases = [
            (32,  "f", "c", 0), (212, "f", "c", 100), (-40, "f", "c", -40),
            (100, "c", "f", 212), (0, "c", "k", 273.15),
            (273.15, "k", "c", 0), (-459.67, "f", "k", 0),
        ]
        for value, src, dst, expected in cases:
            with self.subTest(f"{value} {src} -> {dst}"):
                self.assertAlmostEqual(
                    eudoxus.handle(value, src, dst)["result"], expected, places=9)

    def test_same_unit_is_the_identity(self):
        for unit in ("f", "c", "k"):
            self.assertAlmostEqual(eudoxus.handle(37.5, unit, unit)["result"], 37.5)

    def test_the_factor_line_is_a_formula_not_a_ratio(self):
        # A ratio would be wrong for an affine scale — 72 F is not 22.2x
        # anything. Every f/c/k pair has an explicit formula string.
        self.assertEqual(eudoxus.handle(72, "f", "c")["factor"], "°C = (°F − 32) × 5/9")

    def test_every_pair_has_its_own_formula(self):
        # Which is what makes _TEMP_FORMULA's .get default unreachable; the
        # module says so in a comment, so the claim belongs in a test.
        units = ("f", "c", "k")
        self.assertEqual(
            {(a, b) for a in units for b in units},
            set(eudoxus._TEMP_FORMULA),
            "_TEMP_FORMULA no longer covers every f/c/k pair — handle() would "
            "fall through to its generic 'to = f(from)' line")


class TestTheTablesAreWellFormed(unittest.TestCase):
    """Invariants the module leans on, asserted rather than assumed."""

    def test_every_alias_resolves_to_a_real_unit(self):
        for alias, key in eudoxus._ALIASES.items():
            with self.subTest(alias=alias):
                self.assertIn(key, eudoxus._CATEGORY_OF,
                              f"alias {alias!r} points at {key!r}, which has no "
                              f"category — handle() would degrade to unknown_unit")

    def test_every_table_unit_is_reachable_by_at_least_one_alias(self):
        targets = set(eudoxus._ALIASES.values())
        for unit in eudoxus._CATEGORY_OF:
            with self.subTest(unit=unit):
                self.assertIn(unit, targets,
                              f"{unit!r} is in a conversion table but no alias "
                              f"reaches it — dead weight in the table")

    def test_every_category_has_a_table_except_temperature(self):
        categories = set(eudoxus._CATEGORY_OF.values())
        self.assertEqual(categories - {"temperature"}, set(eudoxus._TABLES))

    def test_base_units_are_exactly_one(self):
        # Each table's base unit is the one whose factor is 1.0; the comment
        # above each table names it, so the code and the comment agree here.
        for category, base in [("length", "m"), ("volume", "ml"),
                               ("weight", "g"), ("time", "s")]:
            with self.subTest(category=category):
                self.assertEqual(eudoxus._TABLES[category][base], 1.0)


class TestTheParser(unittest.TestCase):

    def test_the_documented_forms_all_parse(self):
        cases = [
            ("gallon to cup",       16),      # no value -> 1
            ("10 mi to km",         16.09344),
            ("10mi to km",          16.09344),  # no space after the number
            ("10 fl oz to ml",      295.735295625),  # unit with a space in it
            ("1.5 cups in liters",  0.35488235475),
            ("1e3 m to km",         1.0),     # scientific notation
            ("-40 f to c",          -40),     # negative value
            ("  5 KG TO LB  ",      11.023113109243879),  # case + whitespace
        ]
        for expression, expected in cases:
            with self.subTest(expression):
                out = eudoxus.convert(expression)
                self.assertNotIn("error", out, out.get("error"))
                self.assertAlmostEqual(out["result"], expected, places=9)

    def test_in_is_a_unit_as_well_as_a_separator(self):
        # "3 in in cm" split on the FIRST " in " until 2026-07-23, leaving "3"
        # as the left side and reporting a missing source unit.
        out = eudoxus.convert("3 in in cm")
        self.assertNotIn("error", out, out.get("error"))
        self.assertAlmostEqual(out["result"], 7.62, places=9)
        self.assertEqual(out["from"], "in")
        self.assertEqual(out["to"], "cm")

    def test_a_unit_whose_name_merely_contains_the_separator(self):
        for expression, src in [("1 inch in mm", "in"), ("5 min in hr", "min"),
                                ("2 pint in cup", "pint")]:
            with self.subTest(expression):
                out = eudoxus.convert(expression)
                self.assertNotIn("error", out, out.get("error"))
                self.assertEqual(out["from"], src)

    def test_to_wins_when_both_separators_appear(self):
        out = eudoxus.convert("3 in to cm")
        self.assertEqual((out["from"], out["to"]), ("in", "cm"))

    def test_the_expression_is_echoed_back_verbatim(self):
        # The panel keys its history rows on this, so it must survive both the
        # success and the failure path unchanged (original case, not lowered).
        self.assertEqual(eudoxus.convert("10 MI to KM")["expression"], "10 MI to KM")
        self.assertEqual(eudoxus.convert("what is a meter")["expression"],
                         "what is a meter")

    def test_fractions_are_not_supported(self):
        # Documented, not a bug: "1/2 cup" is not a number the parser accepts.
        self.assertIn("error", eudoxus.convert("1/2 cup to ml"))


class TestErrorVocabulary(unittest.TestCase):
    """CONVENTIONS §2 — every identifier is a slug, and the right one."""

    _FAILURES = [
        (lambda: eudoxus.handle(1, "parsec", "mi"),   "unknown_unit"),
        (lambda: eudoxus.handle(1, "mi", "parsec"),   "unknown_unit"),
        (lambda: eudoxus.handle(1, "mi", "cup"),      "incompatible_units"),
        (lambda: eudoxus.handle(1, "f", "kg"),        "incompatible_units"),
        (lambda: eudoxus.convert("what is a meter"),  "missing_separator"),
        (lambda: eudoxus.convert("10 to km"),         "missing_source_unit"),
        (lambda: eudoxus.handle("many", "mi", "km"),  "conversion_failed"),
    ]

    def test_each_failure_returns_its_slug(self):
        for call, slug in self._FAILURES:
            with self.subTest(slug=slug):
                out = call()
                self.assertIn("error", out)
                self.assertEqual(out["error"].partition(": ")[0], slug)

    def test_every_identifier_matches_the_S2_shape(self):
        for call, slug in self._FAILURES:
            with self.subTest(slug=slug):
                self.assertRegex(call()["error"], _SLUG)

    def test_the_unknown_unit_detail_is_what_the_caller_wrote(self):
        self.assertEqual(eudoxus.handle(1, "parsec", "mi")["error"],
                         "unknown_unit: parsec")

    def test_the_mismatch_error_still_reports_both_units(self):
        out = eudoxus.handle(1, "mi", "cup")
        self.assertEqual(out["error"], "incompatible_units: length to volume")
        self.assertEqual((out["from"], out["to"]), ("mi", "cup"))

    def test_an_unexpected_failure_names_the_class_not_the_message(self):
        # §2: str(exc) can carry paths or key material into the LLM's context.
        out = eudoxus.handle("many", "mi", "km")
        self.assertEqual(out["error"], "conversion_failed: ValueError")


class TestNothingRaises(unittest.TestCase):
    """§2: a brain tool that throws crashes Pythia's dispatcher."""

    _HOSTILE = [None, "", "  ", [], {}, object(), float("nan"), float("inf"),
                "1e400", True]

    def test_handle_survives_hostile_values(self):
        for value in self._HOSTILE:
            with self.subTest(value=repr(value)):
                out = eudoxus.handle(value, "mi", "km")
                self.assertIsInstance(out, dict)

    def test_handle_survives_hostile_units(self):
        for unit in self._HOSTILE:
            with self.subTest(unit=repr(unit)):
                self.assertIsInstance(eudoxus.handle(1, unit, "km"), dict)
                self.assertIsInstance(eudoxus.handle(1, "km", unit), dict)

    def test_convert_survives_hostile_expressions(self):
        # These are all malformed enough to error — but the contract under test
        # is "returns a dict, never raises", so that is what's asserted. ("in in
        # in" is deliberately NOT here: it parses to a valid 1 in -> in, because
        # "in" is a unit as well as the separator — see the parser tests.)
        for expression in self._HOSTILE + ["to", " to ", "to to to", "parsec"]:
            with self.subTest(expression=repr(expression)):
                out = eudoxus.convert(expression)
                self.assertIsInstance(out, dict)
                self.assertIn("error", out)

    def test_a_degenerate_but_parseable_expression_does_not_raise(self):
        # "in in in" -> "in" to "in": harmless, and must not raise.
        out = eudoxus.convert("in in in")
        self.assertNotIn("error", out)
        self.assertEqual((out["from"], out["to"]), ("in", "in"))

    def test_non_finite_values_convert_without_raising(self):
        # zeno.format_number guards int(nan)/int(inf) — the display would raise
        # without it, which is the reason the guard is in the shared formatter.
        self.assertEqual(eudoxus.handle(float("inf"), "mi", "km")["display"], "inf km")
        self.assertTrue(math.isnan(eudoxus.handle(float("nan"), "mi", "km")["result"]))


class TestTheFormatterIsShared(unittest.TestCase):
    """One formatter, not two kept 'in sync' by hand."""

    def test_eudoxus_keeps_no_copy_of_its_own(self):
        self.assertFalse(
            hasattr(eudoxus, "_format_number"),
            "the private copy of zeno's number formatter is back — it was "
            "deleted 2026-07-23 in favour of the public zeno.format_number, "
            "because a comment saying 'kept in sync' is not a mechanism")

    def test_the_display_string_is_zenos_formatting(self):
        for value, src, dst in [(1, "min", "hr"), (100, "f", "c"),
                                (1, "mi", "km"), (2, "st", "lb")]:
            with self.subTest(f"{value} {src} -> {dst}"):
                out = eudoxus.handle(value, src, dst)
                self.assertEqual(out["display"],
                                 f"{zeno.format_number(out['result'])} {dst}")

    def test_float_noise_is_hidden_the_same_way_zeno_hides_it(self):
        # 2 stone is 27.999999999999996 lb in binary floating point.
        self.assertEqual(eudoxus.handle(2, "st", "lb")["display"], "28 lb")


class TestTheToolDefinition(unittest.TestCase):
    """§3/§8 — the schema Pythia reflects on."""

    def test_the_schema_names_the_handler_arguments(self):
        params = eudoxus.TOOL_DEFINITION["function"]["parameters"]
        self.assertEqual(set(params["required"]), {"value", "from_unit", "to_unit"})
        self.assertEqual(set(params["properties"]), {"value", "from_unit", "to_unit"})

    def test_calling_it_the_way_pythia_would(self):
        out = eudoxus.handle(**{"value": 10, "from_unit": "mi", "to_unit": "km"})
        self.assertEqual(out["display"], "16.0934 km")


if __name__ == "__main__":
    unittest.main(verbosity=2)
