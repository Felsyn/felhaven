"""
test_selene.py — unit tests for tools/selene.py (moon phase, illumination,
moonrise and moonset, computed locally).

compute() is pure: a place and a datetime in, the display dict out, so these
need no network, no clock and no Tk. Every reference value is an almanac's,
not ours (CONVENTIONS §10), retrieved 2026-09-28:

  USNO Astronomical Applications API, "Complete Sun and Moon Data for One Day":
      https://aa.usno.navy.mil/api/rstt/oneday?date=<D>&coords=<lat>,<lon>&tz=<h>&dst=false
    Moonrise/moonset in whole minutes. Tolerance ±3 min, from the series'
    stated accuracy (~0.3° in longitude is ~2 min of rise time at these
    latitudes), not from how close we happened to land. When these were
    retrieved, all 26 events below agreed within 1 min. Leaving the parallax
    out of h0 fails every one of them (checked 2026-09-28).
    `curphase` and `fracillum`, which USNO gives for LOCAL NOON. Its rule,
    worked out from these cases: round the illumination to a whole percent;
    0 is "New Moon", 100 "Full Moon", 50 a quarter, anything else crescent
    or gibbous. So the day a quarter falls at 20:44 is still "Waxing
    Crescent" (47%). USNO's curphase says "Third Quarter" where its own phase
    list, and this module, say "Last Quarter".

  JPL Horizons, Moon (301) seen from the geocentre (500@399), quantity 10
  (illuminated fraction), at exact instants. Tolerance ±0.5 points: the
  display rounds to whole percent. Matched to 0.01 when retrieved.

Places are public landmarks, never the owner's location (see themis.DEFAULTS).

    python -X utf8 -m unittest tests.test_selene
(-X utf8 keeps phase emoji and the em-dash placeholder printable on Windows.)
"""

import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import hypatia, selene

_GREENWICH = (51.4779, 0.0)
_SYDNEY = (-33.8688, 151.2093)
_NEW_YORK = (40.7128, -74.0060)
_TROMSO = (69.6492, 18.9553)
_NONE = None  # USNO lists no such event that day

# (place, (lat, lon), USNO tz hours, date, moonrise, moonset)
_USNO_RISE_SET = [
    ("Greenwich", _GREENWICH, 0, date(2026, 6, 21), "11:40", "23:45"),
    ("Greenwich", _GREENWICH, 0, date(2026, 9, 18), "14:26", "21:02"),
    ("Greenwich", _GREENWICH, 0, date(2026, 9, 26), "17:27", "05:31"),
    ("Greenwich", _GREENWICH, 0, date(2026, 9, 28), "17:56", "08:15"),
    ("Greenwich", _GREENWICH, 0, date(2026, 10, 10), "06:05", "16:48"),
    ("Greenwich", _GREENWICH, 0, date(2026, 11, 17), "13:13", "23:29"),
    ("Greenwich", _GREENWICH, 0, date(2026, 12, 1), _NONE, "12:38"),
    ("Greenwich", _GREENWICH, 0, date(2026, 12, 17), "11:55", _NONE),
    ("Greenwich", _GREENWICH, 0, date(2026, 12, 21), "13:09", "05:06"),
    ("Greenwich", _GREENWICH, 0, date(2026, 12, 24), "16:08", "08:56"),
    ("Sydney", _SYDNEY, 10, date(2026, 9, 28), "19:41", "06:02"),
    ("New York", _NEW_YORK, -4, date(2026, 9, 28), "19:27", "09:02"),
    ("Tromsø", _TROMSO, 1, date(2026, 12, 15), "12:14", "21:21"),
    ("Tromsø", _TROMSO, 2, date(2026, 6, 21), "12:18", "00:37"),
]
_RISE_SET_TOLERANCE_MIN = 3

# (place, (lat, lon), USNO tz hours, date, curphase, fracillum) at local noon
_USNO_PHASE = [
    ("Greenwich", _GREENWICH, 0, date(2026, 6, 21), "Waxing Crescent", 46),
    ("Greenwich", _GREENWICH, 0, date(2026, 9, 18), "Waxing Crescent", 47),
    ("Greenwich", _GREENWICH, 0, date(2026, 9, 26), "Full Moon", 100),
    ("Greenwich", _GREENWICH, 0, date(2026, 9, 28), "Waning Gibbous", 96),
    ("Greenwich", _GREENWICH, 0, date(2026, 10, 2), "Waning Gibbous", 62),
    ("Greenwich", _GREENWICH, 0, date(2026, 10, 3), "Waning Gibbous", 51),
    ("Greenwich", _GREENWICH, -1, date(2026, 10, 3), "Third Quarter", 50),
    ("Greenwich", _GREENWICH, 0, date(2026, 10, 4), "Waning Crescent", 39),
    ("Greenwich", _GREENWICH, 0, date(2026, 10, 10), "New Moon", 0),
    ("Greenwich", _GREENWICH, 0, date(2026, 11, 17), "First Quarter", 50),
    ("Greenwich", _GREENWICH, 0, date(2026, 12, 1), "Waning Crescent", 47),
    ("Greenwich", _GREENWICH, 0, date(2026, 12, 9), "New Moon", 0),
    ("Greenwich", _GREENWICH, 0, date(2026, 12, 17), "Waxing Gibbous", 53),
    ("Greenwich", _GREENWICH, 0, date(2026, 12, 21), "Waxing Gibbous", 90),
    ("Greenwich", _GREENWICH, 0, date(2026, 12, 24), "Full Moon", 100),
    ("Sydney", _SYDNEY, 10, date(2026, 9, 28), "Waning Gibbous", 98),
    ("New York", _NEW_YORK, -4, date(2026, 9, 28), "Waning Gibbous", 95),
    ("Tromsø", _TROMSO, 1, date(2026, 12, 15), "Waxing Crescent", 33),
]
_USNO_TO_OURS = {"Third Quarter": "Last Quarter"}

# (UTC instant, JPL Horizons Illu%)
_JPL_ILLUMINATION = [
    (datetime(2026, 9, 28, 0, tzinfo=timezone.utc), 97.81423),
    (datetime(2026, 9, 28, 1, tzinfo=timezone.utc), 97.67722),
    (datetime(2026, 9, 28, 2, tzinfo=timezone.utc), 97.53592),
    (datetime(2026, 10, 3, 12, tzinfo=timezone.utc), 50.80117),
    (datetime(2026, 10, 7, 12, tzinfo=timezone.utc), 11.21667),
    (datetime(2026, 11, 17, 0, tzinfo=timezone.utc), 45.37378),
    (datetime(2026, 12, 1, 12, tzinfo=timezone.utc), 47.47730),
    (datetime(2026, 12, 20, 0, tzinfo=timezone.utc), 77.90180),
]


def _noon(d, tz_hours):
    return datetime(d.year, d.month, d.day, 12, tzinfo=timezone(timedelta(hours=tz_hours)))


def _minutes(clock):
    """'5:31 AM' or '05:31' -> minutes after midnight."""
    fmt = "%I:%M %p" if clock.endswith("M") else "%H:%M"
    t = datetime.strptime(clock, fmt)
    return t.hour * 60 + t.minute


class TestRiseAndSetAgainstUSNO(unittest.TestCase):

    def test_moonrise_and_moonset(self):
        for place, (lat, lon), tz, d, rise, set_ in _USNO_RISE_SET:
            out = selene.compute(lat, lon, _noon(d, tz))
            for field, want in (("moonrise", rise), ("moonset", set_)):
                with self.subTest(place=place, date=d.isoformat(), field=field):
                    if want is _NONE:
                        self.assertEqual(out[field], "—")
                    else:
                        self.assertAlmostEqual(_minutes(out[field]), _minutes(want),
                                               delta=_RISE_SET_TOLERANCE_MIN)

    def test_the_day_counts_not_the_hour(self):
        # Moonrise and moonset are facts about the date. Asked at 00:30 or
        # 23:30, the times must agree (the phase may not: it is "now").
        tz = timezone.utc
        early = selene.compute(*_GREENWICH, datetime(2026, 9, 28, 0, 30, tzinfo=tz))
        late = selene.compute(*_GREENWICH, datetime(2026, 9, 28, 23, 30, tzinfo=tz))
        self.assertEqual((early["moonrise"], early["moonset"]),
                         (late["moonrise"], late["moonset"]))


class TestPhaseAgainstUSNO(unittest.TestCase):

    def test_phase_name_and_illumination_at_local_noon(self):
        for place, (lat, lon), tz, d, phase, pct in _USNO_PHASE:
            with self.subTest(place=place, date=d.isoformat(), tz=tz):
                out = selene.compute(lat, lon, _noon(d, tz))
                self.assertEqual(out["phase"], _USNO_TO_OURS.get(phase, phase))
                self.assertEqual(out["illumination"], f"{pct}%")
                self.assertEqual(out["emoji"], selene.PHASES[out["phase"]])


class TestIlluminationAgainstJPL(unittest.TestCase):

    def test_illuminated_fraction(self):
        for when, pct in _JPL_ILLUMINATION:
            with self.subTest(when=when.isoformat()):
                jd = hypatia._julian_date(when.timestamp())
                self.assertAlmostEqual(selene._illumination(jd) * 100.0, pct, delta=0.5)


class TestPhaseNames(unittest.TestCase):
    """USNO's rule, one case per branch. The name always agrees with the
    percentage shown next to it: "Full Moon" is never 97%."""

    def test_every_branch(self):
        cases = [
            (0, True, "New Moon"), (0, False, "New Moon"),
            (1, True, "Waxing Crescent"), (49, False, "Waning Crescent"),
            (50, True, "First Quarter"), (50, False, "Last Quarter"),
            (51, True, "Waxing Gibbous"), (99, False, "Waning Gibbous"),
            (100, True, "Full Moon"), (100, False, "Full Moon"),
        ]
        for pct, waxing, name in cases:
            with self.subTest(pct=pct, waxing=waxing):
                self.assertEqual(selene._phase_name(pct, waxing), name)

    def test_all_eight_names_have_a_glyph(self):
        self.assertEqual(set(selene.PHASES), {
            "New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous",
            "Full Moon", "Waning Gibbous", "Last Quarter", "Waning Crescent"})
        self.assertEqual(len(set(selene.PHASES.values())), 8)


class TestTwentyFourHourClock(unittest.TestCase):

    def test_rise_and_set_are_the_same_minutes_on_both_clocks(self):
        # Settings' 24-hour clock changes how the times are written, not
        # which minutes they are, and leaves the phase alone.
        for place, (lat, lon), tz, d, _, _ in _USNO_RISE_SET:
            with self.subTest(place=place, date=d.isoformat()):
                twelve = selene.compute(lat, lon, _noon(d, tz))
                twenty_four = selene.compute(lat, lon, _noon(d, tz), clock_24h=True)
                for field in ("emoji", "phase", "illumination"):
                    self.assertEqual(twenty_four[field], twelve[field])
                for field in ("moonrise", "moonset"):
                    if twelve[field] == "—":
                        self.assertEqual(twenty_four[field], "—")
                    else:
                        self.assertNotIn("M", twenty_four[field])
                        self.assertEqual(_minutes(twenty_four[field]),
                                         _minutes(twelve[field]))


class TestLocalClock(unittest.TestCase):

    def test_a_naive_time_means_this_computers_zone(self):
        # The app passes datetime.now(), naive and local.
        naive = datetime(2026, 9, 28, 12)
        self.assertEqual(selene.compute(*_GREENWICH, naive),
                         selene.compute(*_GREENWICH, naive.astimezone()))


class TestNeverRaises(unittest.TestCase):

    def test_poles_and_the_date_line(self):
        for lat in (90.0, -90.0, 89.99, 0.0):
            for lon in (180.0, -180.0, 0.0):
                with self.subTest(lat=lat, lon=lon):
                    out = selene.compute(lat, lon, _noon(date(2026, 12, 21), 0))
                    self.assertEqual(set(out), {"emoji", "phase", "illumination",
                                                "moonrise", "moonset"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
