"""
test_iris.py — the National Weather Service's active alerts.

Hermetic: tools.iris.hermes.get is patched with canned replies shaped like
api.weather.gov's (fields as captured 2026-11-10; the text is made up), and
Themis reads a scratch settings file, so no network and no real place.

    python -X utf8 -m unittest tests.test_iris
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import themis
from tools import hermes, iris


def _feature(event, *, alert_id=None, severity="Severe", sent="2026-11-10T04:12:00-06:00",
             description="* WHAT...Heavy snow expected. Total snow of 6 to 10\ninches.\n\n"
                         "* WHERE...Somewhere County.\n\n* WHEN...Until 6 PM Tuesday.",
             status="Actual", ends="2026-11-11T18:00:00-06:00",
             expires="2026-11-10T16:00:00-06:00", references=()):
    alert_id = alert_id or f"urn:oid:2.49.0.1.840.0.{abs(hash((event, sent))):x}.001.1"
    return {"id": f"https://api.weather.gov/alerts/{alert_id}", "type": "Feature",
            "properties": {
                "id": alert_id, "event": event, "severity": severity,
                "certainty": "Likely", "urgency": "Expected", "status": status,
                "messageType": "Alert", "sent": sent, "effective": sent, "onset": sent,
                "ends": ends, "expires": expires, "senderName": "NWS Somewhere",
                "headline": f"{event} issued November 10 by NWS Somewhere",
                "description": description,
                "instruction": "Travel only in an emergency.",
                "areaDesc": "Somewhere County",
                "references": [{"@id": f"https://api.weather.gov/alerts/{r}",
                                "identifier": r, "sender": "w-nws.webmaster@noaa.gov",
                                "sent": "2026-11-09T20:55:00-06:00"} for r in references]}}


def _collection(*features):
    return {"type": "FeatureCollection", "title": "Current watches, warnings, and advisories",
            "features": list(features)}


_OUT_OF_BOUNDS = {"title": "Invalid Parameter", "status": 400,
                  "detail": 'Parameter "point" is invalid: out of bounds',
                  "type": "https://api.weather.gov/problems/InvalidParameter"}


class _Reply:
    def __init__(self, body, status=200):
        self.status_code = status
        self.content = json.dumps(body).encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise hermes.HTTPStatus(self.status_code)


class _IrisCase(unittest.TestCase):
    def setUp(self):
        iris._clear_state()
        self.addCleanup(iris._clear_state)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "felhaven_settings.json"
        patch = mock.patch.object(themis, "_DATA_PATH", path)
        patch.start()
        self.addCleanup(patch.stop)
        themis.save(latitude=39.0997, longitude=-94.5786)
        self.reply = _Reply(_collection())
        get = mock.patch.object(iris.hermes, "get", side_effect=lambda url, **kw: self.reply)
        self.get = get.start()
        self.addCleanup(get.stop)


class TestTheRequest(_IrisCase):

    def test_asks_for_actual_alerts_at_the_settings_point_to_four_decimals(self):
        themis.save(latitude=39.099712, longitude=-94.578631)
        iris.fetch()
        url = self.get.call_args.args[0]
        self.assertEqual(url, "https://api.weather.gov/alerts/active"
                              "?point=39.0997,-94.5786&status=actual")

    def test_never_sends_the_weather_location_string(self):
        themis.save(latitude=39.0997, longitude=-94.5786, weather_location="Somewhere, KS")
        iris.fetch()
        self.assertNotIn("Somewhere", self.get.call_args.args[0])

    def test_is_bounded_and_names_the_app_without_contact_details(self):
        iris.fetch()
        kw = self.get.call_args.kwargs
        self.assertEqual(kw["cap"], 512 * 1024)
        self.assertEqual(kw["headers"]["User-Agent"], "Felhaven weather dashboard")
        self.assertNotIn("@", kw["headers"]["User-Agent"])

    def test_off_in_settings_asks_nothing(self):
        themis.save(latitude=39.0997, longitude=-94.5786, alerts_enabled=False)
        self.assertEqual(iris.fetch(), {"enabled": False, "covered": True, "alerts": []})
        self.get.assert_not_called()


class TestTheAnswer(_IrisCase):

    def test_none_active_is_a_checked_empty_list(self):
        self.assertEqual(iris.fetch(), {"enabled": True, "covered": True, "alerts": []})

    def test_outside_the_us_is_a_checked_answer_not_a_failure(self):
        self.reply = _Reply(_OUT_OF_BOUNDS, status=400)
        self.assertEqual(iris.fetch(), {"enabled": True, "covered": False, "alerts": []})

    def test_another_400_raises(self):
        self.reply = _Reply({"detail": 'Parameter "status" is invalid'}, status=400)
        with self.assertRaises(hermes.HTTPStatus):
            iris.fetch()

    def test_a_server_error_raises_so_kairos_delivers_none(self):
        self.reply = _Reply({"detail": "oops"}, status=503)
        with self.assertRaises(hermes.HTTPStatus):
            iris.fetch()

    def test_a_network_failure_raises(self):
        self.get.side_effect = hermes.Offline("down")
        with self.assertRaises(hermes.Offline):
            iris.fetch()

    def test_a_body_without_features_raises(self):
        self.reply = _Reply({"title": "something else"})
        with self.assertRaises(iris.FeedError):
            iris.fetch()

    def test_carries_each_field(self):
        self.reply = _Reply(_collection(_feature("Winter Storm Warning", alert_id="urn:a",
                                                 references=("urn:old",))))
        (a,) = iris.fetch()["alerts"]
        self.assertEqual(set(a), {"id", "event", "tier", "severity", "certainty",
                                  "summary", "description", "instruction", "sent",
                                  "until", "references"}, "the Contract's fields, no more")
        self.assertEqual(a["id"], "urn:a")
        self.assertEqual(a["event"], "Winter Storm Warning")
        self.assertEqual((a["severity"], a["certainty"]), ("Severe", "Likely"))
        self.assertEqual(a["until"], "2026-11-11T18:00:00-06:00")
        self.assertEqual(a["references"], ["urn:old"])
        self.assertEqual(a["instruction"], "Travel only in an emergency.")

    def test_until_falls_back_to_expires_when_no_end_is_given(self):
        self.reply = _Reply(_collection(_feature("Flood Warning", ends=None)))
        self.assertEqual(iris.fetch()["alerts"][0]["until"], "2026-11-10T16:00:00-06:00")

    def test_test_and_exercise_messages_are_dropped(self):
        self.reply = _Reply(_collection(_feature("Tornado Warning", status="Test"),
                                        _feature("Tornado Warning", status="Exercise")))
        self.assertEqual(iris.fetch()["alerts"], [])

    def test_an_alert_with_no_id_is_skipped_not_fatal(self):
        bad = _feature("Flood Warning")
        bad["properties"]["id"] = ""
        self.reply = _Reply(_collection(bad, _feature("Wind Advisory")))
        with self.assertLogs("METIS.iris", "WARNING"):
            self.assertEqual([a["event"] for a in iris.fetch()["alerts"]], ["Wind Advisory"])


class TestTiersAndOrder(_IrisCase):

    def test_tiers(self):
        cases = {
            ("Winter Storm Warning", "Severe"): "warning",
            ("Civil Emergency Message", "Unknown"): "warning",
            ("Child Abduction Emergency", "Moderate"): "warning",
            ("Evacuation Immediate", "Unknown"): "warning",
            ("Special Weather Statement", "Extreme"): "warning",
            ("Winter Storm Watch", "Severe"): "watch",
            ("Wind Advisory", "Moderate"): "advisory",
            ("Special Weather Statement", "Moderate"): "advisory",
        }
        for (event, severity), tier in cases.items():
            with self.subTest(event=event, severity=severity):
                self.assertEqual(iris._tier(event, severity), tier)

    def test_warnings_then_watches_then_advisories_newest_first(self):
        self.reply = _Reply(_collection(
            _feature("Wind Advisory", sent="2026-11-10T09:00:00-06:00"),
            _feature("Flood Watch", sent="2026-11-10T08:00:00-06:00"),
            _feature("Flood Warning", sent="2026-11-10T01:00:00-06:00"),
            _feature("Winter Storm Warning", sent="2026-11-10T07:00:00-06:00")))
        self.assertEqual([a["event"] for a in iris.fetch()["alerts"]],
                         ["Winter Storm Warning", "Flood Warning", "Flood Watch",
                          "Wind Advisory"])


class TestTheSummary(_IrisCase):

    def test_is_the_what_paragraph_joined_up(self):
        self.reply = _Reply(_collection(_feature("Winter Storm Warning")))
        self.assertEqual(iris.fetch()["alerts"][0]["summary"],
                         "Heavy snow expected. Total snow of 6 to 10 inches.")

    def test_without_a_what_paragraph_is_the_first_sentence(self):
        self.reply = _Reply(_collection(_feature(
            "Special Weather Statement",
            description="Patchy dense fog has been observed along\nthe highway. Use "
                        "extra caution.\n\nMore later.")))
        self.assertEqual(iris.fetch()["alerts"][0]["summary"],
                         "Patchy dense fog has been observed along the highway.")

    def test_without_a_description_is_the_headline(self):
        self.reply = _Reply(_collection(_feature("Flood Warning", description="")))
        self.assertEqual(iris.fetch()["alerts"][0]["summary"],
                         "Flood Warning issued November 10 by NWS Somewhere")

    def test_long_text_is_clipped(self):
        long = "* WHAT..." + "snow " * 200 + "\n\n* WHERE...here."
        self.reply = _Reply(_collection(_feature("Winter Storm Warning", description=long)))
        a = iris.fetch()["alerts"][0]
        self.assertEqual(len(a["summary"]), 200)
        self.assertTrue(a["summary"].endswith("…"))
        self.reply = _Reply(_collection(_feature("Winter Storm Warning",
                                                 description="x" * 5000)))
        self.assertEqual(len(iris.fetch()["alerts"][0]["description"]), 2000)


if __name__ == "__main__":
    unittest.main()
