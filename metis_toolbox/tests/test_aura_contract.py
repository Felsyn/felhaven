"""
test_aura_contract.py — the public §2 surface of tools/aura.py.

The sibling test_aura_* files cover _build()'s shaping in depth; this pins the
CONTRACT the rest of the stack relies on, by attacking the real I/O seam
(requests.get) the way test_aether does:

  - handle() (the LLM path) NEVER raises — a timeout, an offline network, and a
    malformed body each degrade to a distinct {"error": <slug>} dict.
  - fetch()  (the Kairos path) DOES raise, so Kairos delivers None and the panel
    holds a stale reading instead of blanking (§2).

    python -X utf8 -m unittest tests.test_aura_contract
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from tools import aura
from tests._fixtures import load_real_j1


class _FakeResp:
    """Minimal stand-in for a requests Response (only what _fetch_raw reads)."""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class TestHandleNeverRaises(unittest.TestCase):
    """Every failure mode of the LLM path degrades to its own slug, no raise."""

    def test_timeout_degrades_to_weather_timeout(self):
        with mock.patch("tools.aura.requests.get", side_effect=requests.Timeout):
            out = aura.handle()
        self.assertEqual(out["error"], "weather_timeout")

    def test_connection_error_degrades_to_weather_offline(self):
        with mock.patch("tools.aura.requests.get", side_effect=requests.ConnectionError):
            out = aura.handle()
        self.assertEqual(out["error"], "weather_offline")

    def test_malformed_body_degrades_to_weather_unavailable(self):
        # A 200 whose JSON lacks current_condition -> _build raises KeyError ->
        # the catch-all returns weather_unavailable rather than crashing.
        with mock.patch("tools.aura.requests.get", return_value=_FakeResp({})):
            out = aura.handle()
        self.assertEqual(out["error"], "weather_unavailable")

    def test_success_returns_a_full_dict(self):
        with mock.patch("tools.aura.requests.get",
                        return_value=_FakeResp(load_real_j1())):
            out = aura.handle()
        self.assertNotIn("error", out)
        for key in ("temp_f", "description", "astronomy", "forecast"):
            self.assertIn(key, out)


class TestFetchRaises(unittest.TestCase):
    """The Kairos path must raise so Kairos can deliver None."""

    def test_fetch_raises_on_network_failure(self):
        with mock.patch("tools.aura.requests.get", side_effect=requests.ConnectionError):
            with self.assertRaises(requests.ConnectionError):
                aura.fetch()

    def test_fetch_raises_on_malformed_body(self):
        with mock.patch("tools.aura.requests.get", return_value=_FakeResp({})):
            with self.assertRaises(KeyError):
                aura.fetch()


if __name__ == "__main__":
    unittest.main(verbosity=2)
