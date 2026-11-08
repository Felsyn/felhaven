"""
test_helios.py — unit tests for tools/helios.py (sunrise, sunset, golden
hours and day length, computed locally).

compute() is pure: a place and a datetime in, the display dict out. So none of
this needs the network, the clock or Tk.

The reference times are the U.S. Naval Observatory's, not ours. A fixture the
same head wrote tends to flatter the code it tests (CONVENTIONS §10).

Reference: USNO Astronomical Applications API, "Complete Sun and Moon Data for
One Day", retrieved 2026-09-28:
    https://aa.usno.navy.mil/api/rstt/oneday?date=<D>&coords=<lat>,<lon>&tz=<h>&dst=false
USNO publishes whole minutes, rounded, so the tolerance is ±1 min: it covers
the rounding and nothing else. When these were retrieved, every case matched
to the minute. It still bites: leaving out refraction and the semidiameter
(h0 0.0° instead of -0.833°) moves Greenwich's sunrise from 5:56 to 6:01, and
dropping the equation of time also fails all seven cases (checked 2026-09-28).

Places are public landmarks, never the owner's location (see themis.DEFAULTS).

    python -X utf8 -m unittest tests.test_helios
(-X utf8 keeps the en-dash in golden-hour ranges printable on Windows.)
"""

import os
import sys
import unittest
from datetime import date, datetime, time, timedelta, timezone

# Make the package root importable no matter where the runner is launched.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import helios

_GREENWICH = (51.4779, 0.0)
_SYDNEY = (-33.8688, 151.2093)
_NEW_YORK = (40.7128, -74.0060)
_TROMSO = (69.6492, 18.9553)

# (place, (lat, lon), USNO tz hours, date, USNO rise, USNO set)
_USNO_SUN = [
    ("Greenwich", _GREENWICH, 0, date(2026, 6, 21), "03:43", "20:21"),
    ("Greenwich", _GREENWICH, 0, date(2026, 9, 26), "05:52", "17:49"),
    ("Greenwich", _GREENWICH, 0, date(2026, 9, 28), "05:56", "17:45"),
    ("Greenwich", _GREENWICH, 0, date(2026, 11, 17), "07:21", "16:08"),
    ("Greenwich", _GREENWICH, 0, date(2026, 12, 21), "08:03", "15:53"),
    ("Sydney", _SYDNEY, 10, date(2026, 9, 28), "05:37", "17:55"),
    ("New York", _NEW_YORK, -4, date(2026, 9, 28), "06:50", "18:43"),
]
_TOLERANCE_MIN = 1


def _noon(d, tz_hours):
    return datetime(d.year, d.month, d.day, 12, tzinfo=timezone(timedelta(hours=tz_hours)))


def _minutes(clock):
    """'5:56 AM' or '05:56' -> minutes after midnight."""
    fmt = "%I:%M %p" if clock.endswith("M") else "%H:%M"
    t = datetime.strptime(clock, fmt)
    return t.hour * 60 + t.minute


class TestAgainstTheAlmanac(unittest.TestCase):

    def test_sunrise_and_sunset_match_usno(self):
        for place, (lat, lon), tz, d, rise, set_ in _USNO_SUN:
            with self.subTest(place=place, date=d.isoformat()):
                out = helios.compute(lat, lon, _noon(d, tz))
                self.assertNotIn("error", out)
                self.assertAlmostEqual(_minutes(out["sunrise"]), _minutes(rise),
                                       delta=_TOLERANCE_MIN)
                self.assertAlmostEqual(_minutes(out["sunset"]), _minutes(set_),
                                       delta=_TOLERANCE_MIN)

    def test_polar_night_says_the_sun_does_not_rise(self):
        # USNO, Tromsø 2026-12-15 (tz +1): "Object continuously below the Horizon".
        out = helios.compute(*_TROMSO, _noon(date(2026, 12, 15), 1))
        self.assertEqual(out, {"error": "sun_times_unavailable: the sun does not rise today"})

    def test_midnight_sun_says_the_sun_does_not_set(self):
        # USNO, Tromsø 2026-06-21 (tz +2): "Object continuously above the Horizon".
        out = helios.compute(*_TROMSO, _noon(date(2026, 6, 21), 2))
        self.assertEqual(out, {"error": "sun_times_unavailable: the sun does not set today"})


class TestDisplayShape(unittest.TestCase):

    def test_greenwich_on_the_day_these_were_retrieved(self):
        # The same shape interpret() used to build from wttr.in's strings, so
        # the widget and the model see no difference in form.
        out = helios.compute(*_GREENWICH, _noon(date(2026, 9, 28), 0))
        self.assertEqual(out, {
            "sunrise":    "5:56 AM",
            "sunset":     "5:45 PM",
            "golden_am":  "5:56 – 6:56 AM",
            "golden_pm":  "4:45 – 5:45 PM",
            "day_length": "11h 49m",
        })

    def test_the_day_counts_not_the_hour(self):
        # "Today's sunrise" is a fact about the date: asking at 00:30 and at
        # 23:30 must give the same answer.
        tz = timezone.utc
        early = helios.compute(*_GREENWICH, datetime(2026, 9, 28, 0, 30, tzinfo=tz))
        late = helios.compute(*_GREENWICH, datetime(2026, 9, 28, 23, 30, tzinfo=tz))
        self.assertEqual(early, late)


class TestTwentyFourHourClock(unittest.TestCase):
    """Settings' 24-hour clock (themis.clock_24h, which handle() reads). The
    minutes don't change, only how they are written, in Horai's format."""

    def test_greenwich_on_the_24_hour_clock(self):
        out = helios.compute(*_GREENWICH, _noon(date(2026, 9, 28), 0), clock_24h=True)
        self.assertEqual(out, {
            "sunrise":    "05:56",
            "sunset":     "17:45",
            "golden_am":  "05:56 – 06:56",
            "golden_pm":  "16:45 – 17:45",
            "day_length": "11h 49m",
        })

    def test_every_almanac_case_is_the_same_minutes_on_both_clocks(self):
        for place, (lat, lon), tz, d, _, _ in _USNO_SUN:
            with self.subTest(place=place, date=d.isoformat()):
                twelve = helios.compute(lat, lon, _noon(d, tz))
                twenty_four = helios.compute(lat, lon, _noon(d, tz), clock_24h=True)
                for field in ("sunrise", "sunset"):
                    self.assertEqual(_minutes(twenty_four[field]), _minutes(twelve[field]))

    def test_the_hour_keeps_its_leading_zero(self):
        # The 12-hour clock strips it ("5:56 AM"). Here the same lstrip("0")
        # would turn half past midnight into ":30".
        self.assertEqual(helios._pretty(time(0, 30), clock_24h=True), "00:30")


class TestClockRounding(unittest.TestCase):
    """USNO rounds to the nearest minute, and strftime alone would truncate:
    05:55:39 has to read 5:56, not 5:55."""

    def test_rounds_half_up_to_the_minute(self):
        utc = timezone.utc
        at = lambda *hms: datetime(2026, 9, 28, *hms, tzinfo=utc).timestamp()
        self.assertEqual(helios._clock(at(5, 55, 30), utc), time(5, 56))
        self.assertEqual(helios._clock(at(5, 55, 29), utc), time(5, 55))
        self.assertEqual(helios._clock(at(23, 59, 45), utc), time(0, 0))


class TestLocalClock(unittest.TestCase):

    def test_a_naive_time_means_this_computers_zone(self):
        # The app passes datetime.now(), naive and local (pythia._now()'s
        # convention). That must mean "this computer's zone", the same answer
        # as an explicit zone with this machine's offset for that moment.
        naive = datetime(2026, 9, 28, 12)
        self.assertEqual(helios.compute(*_GREENWICH, naive),
                         helios.compute(*_GREENWICH, naive.astimezone()))


class TestNeverRaises(unittest.TestCase):

    def test_poles_and_the_date_line(self):
        for lat in (90.0, -90.0, 89.99, 0.0):
            for lon in (180.0, -180.0, 0.0):
                for d in (date(2026, 3, 20), date(2026, 6, 21), date(2026, 12, 21)):
                    with self.subTest(lat=lat, lon=lon, date=d.isoformat()):
                        out = helios.compute(lat, lon, _noon(d, 0))
                        if "error" in out:
                            self.assertTrue(out["error"].startswith("sun_times_unavailable: "))
                        else:
                            self.assertIn("sunrise", out)


class TestGoldenHours(unittest.TestCase):
    def test_both_ends(self):
        g = helios.golden_hours(time(5, 52), time(20, 45))
        self.assertEqual(g["am_start"], time(5, 52))   # sunrise
        self.assertEqual(g["am_end"],   time(6, 52))   # sunrise + 1h
        self.assertEqual(g["pm_start"], time(19, 45))  # sunset  - 1h
        self.assertEqual(g["pm_end"],   time(20, 45))  # sunset


class TestDayLength(unittest.TestCase):
    def test_known(self):
        self.assertEqual(helios.day_length(time(5, 52), time(20, 45)), "14h 53m")

    def test_a_sunset_past_midnight_on_this_clock(self):
        # A Settings location far from this computer's zone can put sunset
        # after midnight on this clock (see helios's "Time zone" note). The
        # day is still 11h 49m, not negative.
        self.assertEqual(helios.day_length(time(19, 56), time(7, 45)), "11h 49m")


if __name__ == "__main__":
    unittest.main(verbosity=2)
