"""
test_aether.py — hermetic unit tests for tools/aether.py (Session 5 audit).

Both probes are mocked, so no netsh shell-out and no HTTP call happen. Covers the
WiFi line parser, the Statuspage indicator -> label mapping driven through the
real code path (not re-derived from _STATUS_MAP), and the never-raise contract of
handle()/fetch(). Run from the package root:
    python -X utf8 -m unittest tests.test_aether
"""

import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from tools import aether


# Captured-real `netsh wlan show interfaces` output (trimmed), connected state.
_NETSH_CONNECTED = """\
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi 6 AX201 160MHz
    GUID                   : 11111111-2222-3333-4444-555555555555
    Physical address       : aa:bb:cc:dd:ee:ff
    Interface type         : Primary
    State                  : connected
    SSID                   : SomeNetwork
    Signal                 : 92%
"""

_NETSH_DISCONNECTED = """\
There is 1 interface on the system:

    Name                   : Wi-Fi
    Physical address       : aa:bb:cc:dd:ee:ff
    State                  : disconnected
"""

# netsh runs fine but the box has no wireless adapter (Ethernet-only, or WiFi
# hardware disabled) — no "State" line at all. That is 'unknown', not 'down'.
_NETSH_NO_INTERFACE = "There is no wireless interface on the system.\n"


def _run_returning(stdout):
    """A subprocess.run stand-in: a CompletedProcess-like object with .stdout."""
    return mock.Mock(stdout=stdout)


class TestCheckWifi(unittest.TestCase):
    def test_connected(self):
        with mock.patch("subprocess.run", return_value=_run_returning(_NETSH_CONNECTED)):
            self.assertEqual(aether._check_wifi(), "connected")

    def test_disconnected(self):
        with mock.patch("subprocess.run", return_value=_run_returning(_NETSH_DISCONNECTED)):
            self.assertEqual(aether._check_wifi(), "disconnected")

    def test_no_wireless_interface_is_unknown(self):
        # A parseable run with no State line must NOT read as a known-bad link.
        with mock.patch("subprocess.run", return_value=_run_returning(_NETSH_NO_INTERFACE)):
            self.assertEqual(aether._check_wifi(), "unknown")

    def test_netsh_missing_is_unknown(self):
        with mock.patch("subprocess.run", side_effect=OSError("netsh not found")):
            self.assertEqual(aether._check_wifi(), "unknown")

    def test_netsh_timeout_is_unknown(self):
        with mock.patch("subprocess.run",
                        side_effect=subprocess.TimeoutExpired("netsh", 2)):
            self.assertEqual(aether._check_wifi(), "unknown")


def _status_resp(indicator):
    """A requests.Response stand-in with a Statuspage-shaped JSON body."""
    resp = mock.Mock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"status": {"indicator": indicator}}
    return resp


class TestCheckApi(unittest.TestCase):
    def test_indicator_maps_to_label(self):
        # The definitional Statuspage indicator -> four-state mapping, asserted as
        # literal expectations and driven through the real json access plus
        # _STATUS_MAP. A mistyped map value or a wrong JSON key path both fail here.
        cases = {
            "none":        "operational",
            "minor":       "degraded",
            "maintenance": "degraded",
            "major":       "down",
            "critical":    "down",
        }
        for indicator, expected in cases.items():
            with self.subTest(indicator=indicator):
                with mock.patch("requests.get", return_value=_status_resp(indicator)):
                    self.assertEqual(aether._check_api(), expected)

    def test_unrecognized_indicator_is_unknown(self):
        with mock.patch("requests.get", return_value=_status_resp("supernova")):
            self.assertEqual(aether._check_api(), "unknown")

    def test_http_error_is_unknown(self):
        resp = mock.Mock()
        resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
        with mock.patch("requests.get", return_value=resp):
            self.assertEqual(aether._check_api(), "unknown")

    def test_request_failure_is_unknown(self):
        with mock.patch("requests.get",
                        side_effect=requests.ConnectionError("no route to host")):
            self.assertEqual(aether._check_api(), "unknown")


class TestHandleContract(unittest.TestCase):
    def test_shape_and_keys(self):
        with mock.patch("tools.aether._check_wifi", return_value="connected"), \
                mock.patch("tools.aether._check_api", return_value="operational"):
            out = aether.handle()
        self.assertEqual(set(out), {"wifi", "api_status"})
        self.assertEqual(out["wifi"], "connected")
        self.assertEqual(out["api_status"], "operational")

    def test_degrades_to_unknown_on_total_io_failure(self):
        # The real never-raise guarantee: handle() does not wrap the probes — it
        # relies on them fail-safing. With BOTH I/O layers raising, handle() must
        # still return unknown/unknown and never propagate the exception.
        with mock.patch("subprocess.run", side_effect=OSError("no netsh")), \
                mock.patch("requests.get", side_effect=RuntimeError("no network")):
            out = aether.handle()
        self.assertEqual(out, {"wifi": "unknown", "api_status": "unknown"})

    def test_fetch_is_handle(self):
        # Kairos polls fetch(); the model calls handle(). They are the same object
        # on purpose (poll path == read path), so the never-raise holds for both.
        self.assertIs(aether.fetch, aether.handle)


if __name__ == "__main__":
    unittest.main(verbosity=2)
