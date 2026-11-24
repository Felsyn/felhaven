"""
test_themis.py — unit tests for themis.py (settings load / save / validate).

The deterministic seam is _DATA_PATH: each test points it at a temp file (or a
missing one) so the real felhaven_settings.json is never read or written. Run
from the package root:

    python -X utf8 -m unittest tests.test_themis
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import themis


class ThemisBase(unittest.TestCase):
    def setUp(self):
        self._orig_path = themis._DATA_PATH
        self._dir = tempfile.mkdtemp(prefix="themis_test_")
        themis._DATA_PATH = os.path.join(self._dir, "felhaven_settings.json")

    def tearDown(self):
        themis._DATA_PATH = self._orig_path
        for f in os.listdir(self._dir):
            os.unlink(os.path.join(self._dir, f))
        os.rmdir(self._dir)

    def _write(self, obj):
        with open(themis._DATA_PATH, "w", encoding="utf-8") as f:
            if isinstance(obj, str):
                f.write(obj)
            else:
                json.dump(obj, f)


class TestLoadDefaults(ThemisBase):
    def test_missing_file_returns_defaults(self):
        # No file written — a fresh clone behaves exactly as the old hardcode.
        self.assertEqual(themis.load(), themis.DEFAULTS)
        self.assertEqual(themis.latitude(), themis.DEFAULTS["latitude"])
        self.assertEqual(themis.longitude(), themis.DEFAULTS["longitude"])

    def test_garbled_file_falls_back_to_defaults(self):
        self._write("{ not valid json")
        with self.assertLogs("METIS.themis", "WARNING"):
            self.assertEqual(themis.load(), themis.DEFAULTS)

    def test_non_object_root_falls_back(self):
        self._write([1, 2, 3])
        with self.assertLogs("METIS.themis", "WARNING"):
            self.assertEqual(themis.load(), themis.DEFAULTS)

    def test_partial_file_merges_over_defaults(self):
        self._write({"latitude": 10.0})
        s = themis.load()
        self.assertEqual(s["latitude"], 10.0)
        self.assertEqual(s["longitude"], themis.DEFAULTS["longitude"])   # untouched

    def test_one_bad_field_degrades_only_itself(self):
        # A junk latitude falls back to default; the good longitude survives.
        self._write({"latitude": "north", "longitude": 5.0})
        with self.assertLogs("METIS.themis", "WARNING") as logs:
            s = themis.load()
        self.assertEqual(s["latitude"], themis.DEFAULTS["latitude"])
        self.assertEqual(s["longitude"], 5.0)
        self.assertEqual(len(logs.output), 1, "one warning, for the latitude")

    def test_out_of_range_field_degrades(self):
        self._write({"latitude": 999.0})
        with self.assertLogs("METIS.themis", "WARNING"):
            self.assertEqual(themis.load()["latitude"], themis.DEFAULTS["latitude"])


class TestSaveRoundTrip(ThemisBase):
    def test_save_then_load(self):
        themis.save(latitude=-33.9, longitude=151.2, weather_location="Sydney",
                    temperature_unit="C", clock_24h=True)
        s = themis.load()
        self.assertEqual(s["latitude"], -33.9)
        self.assertEqual(s["longitude"], 151.2)
        self.assertEqual(s["weather_location"], "Sydney")
        self.assertEqual(s["temperature_unit"], "C")
        self.assertTrue(s["clock_24h"])

    def test_save_normalizes_unit_case_and_strips_location(self):
        themis.save(latitude=0.0, longitude=0.0, weather_location="  Quito  ",
                    temperature_unit="c", clock_24h=False)
        s = themis.load()
        self.assertEqual(s["temperature_unit"], "C")
        self.assertEqual(s["weather_location"], "Quito")

    def test_save_is_atomic_no_tmp_left(self):
        themis.save(latitude=1.0, longitude=2.0)
        leftovers = [f for f in os.listdir(self._dir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class TestSaveValidation(ThemisBase):
    def test_bad_latitude_raises(self):
        with self.assertRaises(themis.SettingsError):
            themis.save(latitude=91.0, longitude=0.0)

    def test_bad_longitude_raises(self):
        with self.assertRaises(themis.SettingsError):
            themis.save(latitude=0.0, longitude=-181.0)

    def test_bad_unit_raises(self):
        with self.assertRaises(themis.SettingsError):
            themis.save(latitude=0.0, longitude=0.0, temperature_unit="K")

    def test_non_numeric_latitude_raises(self):
        with self.assertRaises(themis.SettingsError):
            themis.save(latitude="north", longitude=0.0)  # type: ignore[arg-type]

    def test_failed_validation_does_not_write(self):
        with self.assertRaises(themis.SettingsError):
            themis.save(latitude=200.0, longitude=0.0)
        self.assertFalse(os.path.exists(themis._DATA_PATH))


class TestDerived(ThemisBase):
    def test_weather_query_uses_location_when_set(self):
        self._write({"latitude": 1.0, "longitude": 2.0, "weather_location": "10001"})
        self.assertEqual(themis.weather_query(), "10001")

    def test_weather_query_falls_back_to_coords(self):
        self._write({"latitude": 51.4779, "longitude": -0.0015, "weather_location": ""})
        self.assertEqual(themis.weather_query(), "51.4779,-0.0015")

    def test_is_southern(self):
        self._write({"latitude": -1.0, "longitude": 0.0})
        self.assertTrue(themis.is_southern())
        self._write({"latitude": 1.0, "longitude": 0.0})
        self.assertFalse(themis.is_southern())


class TestPythiaSettings(ThemisBase):
    """Pythia's thinking and temperature (the MACHINE SPIRIT tab)."""

    def test_the_defaults_change_nothing_pythia_sent_before(self):
        self.assertIs(themis.pythia_thinking(), False)
        self.assertIsNone(themis.pythia_temperature())    # the model's own

    def test_save_pythia_round_trips(self):
        themis.save_pythia(thinking=True, temperature=0.35)
        self.assertIs(themis.pythia_thinking(), True)
        self.assertEqual(themis.pythia_temperature(), 0.35)
        themis.save_pythia(thinking=False, temperature=None)
        self.assertIs(themis.pythia_thinking(), False)
        self.assertIsNone(themis.pythia_temperature())

    def test_the_temperature_keeps_two_decimals(self):
        themis.save_pythia(thinking=False, temperature=0.30000000000000004)
        self.assertEqual(themis.pythia_temperature(), 0.3)

    def test_save_pythia_keeps_every_other_setting(self):
        themis.save(latitude=10.5, longitude=-20.25, weather_location="Town",
                    temperature_unit="C", clock_24h=True)
        themis.save_pythia(thinking=True, temperature=1.4)
        s = themis.load()
        self.assertEqual((s["latitude"], s["longitude"], s["weather_location"],
                          s["temperature_unit"], s["clock_24h"]),
                         (10.5, -20.25, "Town", "C", True))

    def test_the_settings_tab_save_keeps_pythias_settings(self):
        """The Settings form doesn't show them, so its save must not reset them."""
        themis.save_pythia(thinking=True, temperature=0.6)
        themis.save(latitude=1.0, longitude=2.0)
        self.assertIs(themis.pythia_thinking(), True)
        self.assertEqual(themis.pythia_temperature(), 0.6)

    def test_save_pythia_refuses_bad_values_and_writes_nothing(self):
        for thinking, temperature in (("yes", None), (1, None), (False, -0.1),
                                      (False, 2.01), (False, "hot"), (False, True)):
            with self.subTest(thinking=thinking, temperature=temperature):
                with self.assertRaises(themis.SettingsError):
                    themis.save_pythia(thinking=thinking, temperature=temperature)
                self.assertFalse(os.path.exists(themis._DATA_PATH))

    def test_the_range_ends_are_allowed(self):
        for value in (themis.TEMPERATURE_MIN, themis.TEMPERATURE_MAX):
            themis.save_pythia(thinking=False, temperature=value)
            self.assertEqual(themis.pythia_temperature(), value)

    def test_the_model_defaults_to_none(self):
        self.assertIsNone(themis.pythia_model())     # pythia's PYTHIA_MODEL default

    def test_save_pythia_model_round_trips_and_resets(self):
        themis.save_pythia_model("  qwen3:4b ")
        self.assertEqual(themis.pythia_model(), "qwen3:4b")
        themis.save_pythia_model("hf.co/org/model:Q4_K_M")
        self.assertEqual(themis.pythia_model(), "hf.co/org/model:Q4_K_M")
        themis.save_pythia_model(None)
        self.assertIsNone(themis.pythia_model())

    def test_a_bad_model_name_is_refused_and_writes_nothing(self):
        for bad in ("", "   ", "two words", "gemma4:e2b; rm -rf", "x" * 201,
                    True, 7, "a\nb"):
            with self.subTest(name=bad):
                with self.assertRaises(themis.SettingsError):
                    themis.save_pythia_model(bad)
                self.assertFalse(os.path.exists(themis._DATA_PATH))

    def test_every_save_keeps_the_model(self):
        themis.save_pythia_model("qwen3:4b")
        themis.save(latitude=1.0, longitude=2.0)
        themis.save_pythia(thinking=True, temperature=0.5)
        self.assertEqual(themis.pythia_model(), "qwen3:4b")
        themis.save_pythia_model("gemma4:e2b")
        s = themis.load()
        self.assertEqual((s["latitude"], s["pythia_thinking"], s["pythia_temperature"]),
                         (1.0, True, 0.5))

    def test_a_bad_hand_edited_model_degrades_to_the_default(self):
        self._write({"pythia_model": "not a model"})
        with self.assertLogs("METIS.themis", "WARNING"):
            self.assertIsNone(themis.load()["pythia_model"])

    def test_a_bad_hand_edit_degrades_to_the_default(self):
        """A string "false" must not switch the slow mode ON (bool("false") is
        True), and a temperature out of range or not a number falls back to
        the model's own."""
        for raw in ({"pythia_thinking": "false"}, {"pythia_thinking": 1},
                    {"pythia_temperature": 9}, {"pythia_temperature": "warm"},
                    {"pythia_temperature": True}):
            with self.subTest(raw=raw):
                self._write(raw)
                with self.assertLogs("METIS.themis", "WARNING"):
                    s = themis.load()
                self.assertIs(s["pythia_thinking"], False)
                self.assertIsNone(s["pythia_temperature"])


class TestAlertsSettings(ThemisBase):
    """Settings › ALERTS: the switch, what pops up, and the voice."""

    def test_defaults_are_on_warnings_only_read_aloud(self):
        self.assertIs(themis.alerts_enabled(), True)
        self.assertEqual(themis.alerts_popup(), "warnings")
        self.assertIs(themis.alerts_read_aloud(), True)

    def test_save_round_trips_them(self):
        themis.save(latitude=1.0, longitude=2.0, alerts_enabled=False,
                    alerts_popup="warnings_watches", alerts_read_aloud=False)
        self.assertIs(themis.alerts_enabled(), False)
        self.assertEqual(themis.alerts_popup(), "warnings_watches")
        self.assertIs(themis.alerts_read_aloud(), False)

    def test_a_save_that_leaves_them_out_keeps_them(self):
        themis.save(latitude=1.0, longitude=2.0, alerts_enabled=False,
                    alerts_popup="warnings_watches", alerts_read_aloud=False)
        themis.save(latitude=3.0, longitude=4.0)
        themis.save_pythia(thinking=True, temperature=0.5)
        themis.save_pythia_model("gemma4:e2b")
        s = themis.load()
        self.assertEqual((s["latitude"], s["alerts_enabled"], s["alerts_popup"],
                          s["alerts_read_aloud"]), (3.0, False, "warnings_watches", False))

    def test_save_refuses_a_bad_value_and_writes_nothing(self):
        for kw in ({"alerts_enabled": "no"}, {"alerts_popup": "everything"},
                   {"alerts_read_aloud": 0}):
            with self.subTest(kw=kw):
                with self.assertRaises(themis.SettingsError):
                    themis.save(latitude=1.0, longitude=2.0, **kw)
                self.assertFalse(os.path.exists(themis._DATA_PATH))

    def test_the_aurora_notice_is_off_until_saved_on_and_kept_after(self):
        self.assertIs(themis.space_aurora_notice(), False)
        themis.save(latitude=1.0, longitude=2.0, space_aurora_notice=True)
        themis.save(latitude=3.0, longitude=4.0)
        themis.save_pythia(thinking=False, temperature=None)
        self.assertIs(themis.space_aurora_notice(), True)
        with self.assertRaises(themis.SettingsError):
            themis.save(latitude=1.0, longitude=2.0, space_aurora_notice="yes")
        self._write({"space_aurora_notice": 1})
        with self.assertLogs("METIS.themis", "WARNING"):
            self.assertIs(themis.load()["space_aurora_notice"], False)

    def test_a_bad_hand_edit_degrades_to_the_default(self):
        for raw in ({"alerts_enabled": "false"}, {"alerts_popup": "all"},
                    {"alerts_read_aloud": 1}):
            with self.subTest(raw=raw):
                self._write(raw)
                with self.assertLogs("METIS.themis", "WARNING"):
                    s = themis.load()
                self.assertEqual((s["alerts_enabled"], s["alerts_popup"],
                                  s["alerts_read_aloud"]), (True, "warnings", True))


class TestAtmosPlace(ThemisBase):
    """Atmospherics' PLACE: "" for Home, else a cached area's id. The value
    becomes a folder name under artemis_cache/, so anything that isn't an
    area id's exact shape is refused on save and dropped on load."""

    AREA = "n39p100_w094p579"      # a generic point, not anybody's place

    def test_home_until_a_place_is_picked(self):
        self.assertEqual(themis.atmos_place(), "")

    def test_a_pick_round_trips_and_home_resets_it(self):
        themis.save_atmos_place(self.AREA)
        self.assertEqual(themis.atmos_place(), self.AREA)
        themis.save_atmos_place("")
        self.assertEqual(themis.atmos_place(), "")

    def test_a_pick_keeps_every_other_setting(self):
        themis.save(latitude=1.0, longitude=2.0, alerts_popup="warnings_watches")
        themis.save_pythia_model("gemma4:e2b")
        themis.save_atmos_place(self.AREA)
        s = themis.load()
        self.assertEqual((s["latitude"], s["alerts_popup"], s["pythia_model"]),
                         (1.0, "warnings_watches", "gemma4:e2b"))

    def test_every_other_save_keeps_the_pick(self):
        """Settings' form is built at startup: its Save must not put back the
        place it was built with after a dropdown picked another."""
        themis.save_atmos_place(self.AREA)
        themis.save(latitude=3.0, longitude=4.0)
        themis.save_pythia(thinking=True, temperature=0.5)
        themis.save_pythia_model("gemma4:e2b")
        self.assertEqual(themis.atmos_place(), self.AREA)

    def test_anything_but_an_area_id_is_refused_and_writes_nothing(self):
        for bad in ("..", "..\\x", "../x", "C:\\Windows", "/etc", "n39p100",
                    self.AREA + "\n", self.AREA + "/..", " " + self.AREA,
                    "n39p100_w094p579_x", "N39P100_W094P579",
                    "n\u0663\u0669p100_w094p579",      # Arabic-Indic digits
                    None, 39, ["n39p100_w094p579"]):
            with self.subTest(bad=bad):
                with self.assertRaises(themis.SettingsError):
                    themis.save_atmos_place(bad)
                self.assertFalse(os.path.exists(themis._DATA_PATH))

    def test_a_bad_hand_edit_loads_as_home(self):
        for raw in ("..\\..\\x", self.AREA + "\n", 7):
            with self.subTest(raw=raw):
                self._write({"atmos_place": raw})
                with self.assertLogs("METIS.themis", "WARNING"):
                    self.assertEqual(themis.load()["atmos_place"], "")


class TestHandEdits(ThemisBase):
    """load() never raises and degrades only the bad field (§4); each case
    here got through before 2026-11-21 (measured)."""

    def _load(self):
        try:
            return themis.load()
        except Exception as exc:
            self.fail(f"load() raised {type(exc).__name__}")

    def test_clock_24h_needs_a_real_true(self):
        # bool("false") is True: the trap pythia_thinking's coercer names.
        for raw, expected in (("false", False), ("true", False), (1, False),
                              (True, True), (False, False)):
            with self.subTest(raw=raw):
                self._write({"clock_24h": raw})
                # A real true or false loads quietly; anything else warns.
                quiet = isinstance(raw, bool)
                with (self.assertNoLogs if quiet else self.assertLogs)("METIS.themis", "WARNING"):
                    self.assertIs(self._load()["clock_24h"], expected)

    def test_save_refuses_a_clock_that_isnt_true_or_false(self):
        with self.assertRaises(themis.SettingsError):
            themis.save(latitude=1.0, longitude=2.0, clock_24h="false")
        self.assertFalse(os.path.exists(themis._DATA_PATH))

    def test_numbers_too_big_for_a_float_fall_back(self):
        huge = "9" * 400
        self._write('{"latitude": %s, "pythia_temperature": %s}' % (huge, huge))
        with self.assertLogs("METIS.themis", "WARNING"):
            data = self._load()
        self.assertEqual(data["latitude"], themis.DEFAULTS["latitude"])
        self.assertIsNone(data["pythia_temperature"])

    def test_a_file_nested_too_deep_falls_back(self):
        self._write("[" * 100000 + "]" * 100000)
        with self.assertLogs("METIS.themis", "WARNING"):
            self.assertEqual(self._load(), themis.DEFAULTS)

    def test_a_true_or_false_coordinate_is_refused(self):
        # The save first, while no file exists: save() loads the file before
        # it validates, so after the write below it would warn as well.
        with self.assertRaises(themis.SettingsError):
            themis.save(latitude=True, longitude=2.0)
        self._write({"latitude": True, "longitude": False})
        with self.assertLogs("METIS.themis", "WARNING"):
            data = self._load()
        self.assertEqual((data["latitude"], data["longitude"]),
                         (themis.DEFAULTS["latitude"], themis.DEFAULTS["longitude"]))

    def test_a_refused_coordinate_or_place_is_not_logged(self):
        """S24's rule for the save log (§12) holds for load()'s warnings: a
        latitude or longitude is half a location, a place id is its corner's
        coordinates, and felhaven.log shows on the EMANON tab. float()'s own
        message quoted the text. Made-up values."""
        for raw in ({"latitude": "12.3456N", "longitude": "-65.4321W"},
                    {"latitude": 1234.56, "longitude": -654.321},
                    {"atmos_place": "n12p345_w065p432 x"}):
            with self.subTest(raw=raw):
                self._write(raw)
                with self.assertLogs("METIS.themis", "WARNING") as logs:
                    data = self._load()
                for key in raw:
                    self.assertEqual(data[key], themis.DEFAULTS[key], "still refused")
                said = "\n".join(logs.output)
                for digits in ("12.3456", "65.4321", "1234.56", "654.321", "n12p345"):
                    self.assertNotIn(digits, said)

    def test_a_weather_location_that_isnt_text_falls_back(self):
        self._write({"weather_location": ["a", 1], "latitude": 1.5, "longitude": 2.5})
        with self.assertLogs("METIS.themis", "WARNING"):
            self.assertEqual(themis.weather_query(), "1.5,2.5")
        # A ZIP typed without quotes is still a place.
        self._write({"weather_location": 26301})
        self.assertEqual(themis.weather_query(), "26301")

    def test_a_refused_weather_location_is_not_logged(self):
        self._write({"weather_location": {"street": "12 Elm"}})
        with self.assertLogs("METIS.themis", "WARNING") as logs:
            self.assertEqual(self._load()["weather_location"], "")
        self.assertNotIn("Elm", "\n".join(logs.output))


class TestSaveLog(ThemisBase):
    """A save names the fields it changed, never their values: the place is
    the owner's location, and EMANON shows INFO lines on screen (the owner's
    pick, 2026-11-21)."""

    AREA = "n12p346_w065p432"

    def test_a_save_logs_field_names_never_values(self):
        with self.assertLogs("METIS.themis", "INFO") as logs:
            themis.save(latitude=12.3456, longitude=-65.4321,
                        weather_location="12 Elm St")
            themis.save_atmos_place(self.AREA)
        text = "\n".join(logs.output)
        for name in ("latitude", "longitude", "weather_location", "atmos_place"):
            self.assertIn(name, text)
        for value in ("12.3456", "65.4321", "Elm", self.AREA):
            self.assertNotIn(value, text)

    def test_a_save_that_changes_nothing_says_so(self):
        themis.save(latitude=1.0, longitude=2.0)
        with self.assertLogs("METIS.themis", "INFO") as logs:
            themis.save(latitude=1.0, longitude=2.0)
        self.assertIn("changed: nothing", logs.output[-1])


class TestReadersAndSavesRace(ThemisBase):
    """Windows refuses a rename over a file another thread has open, and an
    open while the rename runs. Kairos workers and Pythia read the settings
    while the Tk thread saves: unlocked, a tight reader made 215 of 300 saves
    fail and 78 reads fall back to Greenwich (measured 2026-11-21)."""

    def test_a_save_never_fails_and_a_reader_never_falls_back(self):
        import threading
        themis.save(latitude=10.0, longitude=20.0)
        stop, reading = threading.Event(), threading.Event()
        wrong = []

        def reader():
            while not stop.is_set():
                if themis.latitude() != 10.0:
                    wrong.append(1)
                reading.set()

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(stop.set)
        self.assertTrue(reading.wait(5), "the reader never ran")
        failed = 0
        for _ in range(150):
            try:
                themis.save(latitude=10.0, longitude=20.0)
            except OSError:
                failed += 1
        stop.set()
        thread.join(5)
        self.assertEqual((failed, len(wrong)), (0, 0),
                         "(failed saves, reads that fell back)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
