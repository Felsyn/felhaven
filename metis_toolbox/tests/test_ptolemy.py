"""
test_ptolemy.py — get_sky_report: one tool over three sky modules.

Ptolemy hands Pythia the three readings that used to be three tools (Helios,
Selene, Hypatia). What it owns, and so what is tested here, is the merge: the
three answers arrive flat in one dict, one failed part never blanks the others,
and nothing raises. The three modules' own math has its own suites; here their
handle()s are mostly patched. The polar case runs the real Helios, because the
point is that Helios's own words survive the merge.

    python -X utf8 -m unittest tests.test_ptolemy
"""

import os
import sys
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pythia
from tools import ptolemy

_SUN = {"sunrise": "6:58 AM", "sunset": "5:02 PM", "day_length": "10h 04m",
        "golden_am": "6:41 – 7:38 AM", "golden_pm": "4:22 – 5:19 PM"}
_MOON = {"phase": "Waxing Crescent", "illumination": "21%", "emoji": "🌒",
         "moonrise": "11:40 AM", "moonset": "9:15 PM"}
_SKY = {"preset": "current", "stars_above_horizon": 492,
        "planets_visible": [{"name": "Saturn", "altitude_deg": 49.1}],
        "brightest_stars": [{"name": "Vega", "magnitude": 0.03}]}

# The three names get_sky_report replaced (CONVENTIONS §12, 2026-11-14).
_RETIRED = ("get_sun_times", "get_moon_phase", "get_sky_tonight")


class _Patched(unittest.TestCase):
    """Each part's module handle() replaced by a canned reading."""

    def setUp(self):
        self.sun = self.patch("tools.helios.handle", _SUN)
        self.moon = self.patch("tools.selene.handle", _MOON)
        self.sky = self.patch("tools.hypatia.handle", _SKY)

    def patch(self, target, value):
        m = mock.Mock(return_value=dict(value))
        p = mock.patch(target, m)
        p.start()
        self.addCleanup(p.stop)
        return m


class TestMerge(_Patched):

    def test_all_three_merge_flat(self):
        self.assertEqual(ptolemy.handle(), {**_SUN, **_MOON, **_SKY})
        for m in (self.sun, self.moon, self.sky):
            self.assertEqual(m.call_count, 1)

    def test_one_failed_part_is_one_key_and_the_rest_survive(self):
        self.sky.return_value = {"error": "unavailable: sky snapshot raised RuntimeError"}
        out = ptolemy.handle()
        self.assertNotIn("error", out)                 # T6: success has none
        self.assertEqual(out["sky"], "unavailable: sky snapshot raised RuntimeError")
        self.assertEqual(out["sunset"], _SUN["sunset"])
        self.assertEqual(out["phase"], _MOON["phase"])

    def test_a_raising_part_is_unavailable_not_a_crash(self):
        self.moon.side_effect = ZeroDivisionError("math at C:\\secret")
        with self.assertLogs("METIS.ptolemy", "WARNING"):
            out = ptolemy.handle()
        self.assertEqual(out["moon"], "unavailable: moon raised ZeroDivisionError")
        self.assertNotIn("secret", str(out))           # the message stays in the log
        self.assertEqual(out["sunrise"], _SUN["sunrise"])

    def test_a_part_that_returns_no_dict_is_unavailable(self):
        self.sun.return_value = None
        with self.assertLogs("METIS.ptolemy", "WARNING"):
            self.assertTrue(ptolemy.handle()["sun"].startswith("unavailable: "))

    def test_every_part_failed_is_an_error(self):
        self.sun.return_value = {"error": "unavailable: sun computation raised ValueError"}
        self.moon.side_effect = ValueError("x")
        self.sky.return_value = {"error": "unavailable: sky snapshot raised RuntimeError"}
        with self.assertLogs("METIS.ptolemy", "WARNING"):
            out = ptolemy.handle()
        self.assertEqual(set(out), {"error"})
        self.assertTrue(out["error"].startswith("unavailable: "))
        self.assertIn("sky: unavailable: sky snapshot raised RuntimeError", out["error"])

    def test_colliding_keys_nest_instead_of_overwriting(self):
        self.sky.return_value = {"sunset": "never", "preset": "current"}
        with self.assertLogs("METIS.ptolemy", "WARNING"):
            out = ptolemy.handle()
        self.assertEqual(out["sunset"], _SUN["sunset"])   # Helios's value kept
        self.assertEqual(out["sky"], {"sunset": "never", "preset": "current"})


class TestPolarDay(unittest.TestCase):
    """The real Helios at Longyearbyen at midsummer: the sun does not set, and
    that sentence, not a generic failure, is what the model gets."""

    def test_helios_reason_survives_under_sun(self):
        with mock.patch("themis.latitude", lambda: 78.22), \
             mock.patch("themis.longitude", lambda: 15.65), \
             mock.patch("themis.clock_24h", lambda: False), \
             mock.patch("tools.helios._now", lambda: datetime(2026, 6, 21, 12)), \
             mock.patch("tools.selene.handle", lambda: dict(_MOON)), \
             mock.patch("tools.hypatia.handle", lambda: dict(_SKY)):
            out = ptolemy.handle()
        self.assertNotIn("error", out)
        self.assertEqual(out["sun"], "unavailable: the sun does not set today")
        self.assertEqual(out["phase"], _MOON["phase"])


class TestRegistration(unittest.TestCase):

    def test_registered_and_dispatched_to_handle(self):
        self.assertIs(pythia._DISPATCH["get_sky_report"], ptolemy.handle)

    def test_the_retired_names_are_gone(self):
        # T10: a retired tool name never comes back as an alias.
        for name in _RETIRED:
            with self.subTest(name=name):
                self.assertNotIn(name, pythia._DISPATCH)

    def test_the_three_modules_are_no_longer_brain_tools(self):
        from tools import helios, hypatia, selene
        registered = set(pythia._TOOL_MODULES)
        for module in (helios, selene, hypatia):
            with self.subTest(module=module.__name__):
                self.assertNotIn(module, registered)
                self.assertFalse(hasattr(module, "TOOL_DEFINITION"))

    def test_it_takes_no_arguments(self):
        params = ptolemy.TOOL_DEFINITION["function"]["parameters"]
        self.assertEqual(params["properties"], {})
        with self.assertLogs("METIS.pythia", "WARNING"):
            out = pythia._dispatch("get_sky_report", {"part": "sun"})
        self.assertTrue(out["error"].startswith("bad_arguments"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
