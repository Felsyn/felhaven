"""
test_aether.py — hermetic unit tests for tools/aether.py (Session 5 audit).

Every probe is mocked, so no netsh shell-out and no HTTP call happen. Covers the
WiFi line parser, the internet probe's four answers (captive portal included),
the Ollama probe and its lockstep with pythia's address, and the never-raise
contract of handle()/fetch(). (2026-09-27: the Anthropic Statuspage check this
file used to cover is gone.) Run from the package root:
    python -X utf8 -m unittest tests.test_aether
"""

import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import aether, hermes


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


def _http(status=200, text=""):
    """A hermes.Reply stand-in: status code and body text."""
    return mock.Mock(status_code=status, text=text)


class TestCheckInternet(unittest.TestCase):
    """Windows' own NCSI probe: a plain-HTTP page with a known body. Plain HTTP
    on purpose — a captive portal can only intercept what it can read."""

    def test_the_expected_body_is_online(self):
        with mock.patch("tools.aether.hermes.get",
                        return_value=_http(200, "Microsoft Connect Test")) as get:
            self.assertEqual(aether._check_internet(), "online")
        # A redirect is itself the captive-portal signal, so it must not be
        # followed to a page that happens to say 200.
        self.assertIs(get.call_args.kwargs["follow_redirects"], False)
        # Plain http on purpose, and only here and for Ollama.
        self.assertIs(get.call_args.kwargs["allow_http"], True)
        self.assertEqual(get.call_args.kwargs["cap"], aether._MAX_BYTES)

    def test_a_redirect_is_limited(self):
        # The hotel / campground login page.
        with mock.patch("tools.aether.hermes.get", return_value=_http(302, "")):
            self.assertEqual(aether._check_internet(), "limited")

    def test_the_wrong_body_is_limited(self):
        with mock.patch("tools.aether.hermes.get",
                        return_value=_http(200, "<html>Please log in</html>")):
            self.assertEqual(aether._check_internet(), "limited")

    def test_no_route_is_offline(self):
        for exc in (hermes.Offline("no route to host"),
                    hermes.Timeout("timed out"),
                    hermes.DeadlinePassed("past the deadline")):
            with self.subTest(exc=type(exc).__name__):
                with mock.patch("tools.aether.hermes.get", side_effect=exc):
                    self.assertEqual(aether._check_internet(), "offline")

    def test_a_page_too_big_to_be_the_probe_is_limited(self):
        # The real page is 22 bytes; anything over the cap is a login page.
        with mock.patch("tools.aether.hermes.get", side_effect=hermes.TooLarge("big")):
            self.assertEqual(aether._check_internet(), "limited")

    def test_a_server_error_is_unknown_not_limited(self):
        # The probe's own server failing says nothing about this network.
        with mock.patch("tools.aether.hermes.get", return_value=_http(503, "")):
            self.assertEqual(aether._check_internet(), "unknown")

    def test_anything_else_is_unknown(self):
        with mock.patch("tools.aether.hermes.get", side_effect=RuntimeError("odd")):
            self.assertEqual(aether._check_internet(), "unknown")


class TestCheckOllama(unittest.TestCase):
    def test_a_version_answer_is_online(self):
        with mock.patch("tools.aether.hermes.get", return_value=_http(200, '{"version":"0.34.2"}')) as get:
            self.assertEqual(aether._check_ollama(), "online")
        self.assertTrue(get.call_args.args[0].endswith("/api/version"))

    def test_a_connect_timeout_is_offline(self):
        # Windows refuses a closed local port only after ~2 s, the probe's
        # whole budget, so a stopped Ollama often shows up as a connect
        # timeout rather than a refusal (seen live 2026-10-03).
        with mock.patch("tools.aether.hermes.get",
                        side_effect=hermes.ConnectTimeout("no connection")):
            self.assertEqual(aether._check_ollama(), "offline")

    def test_connection_refused_is_offline(self):
        with mock.patch("tools.aether.hermes.get",
                        side_effect=hermes.Offline("refused")):
            self.assertEqual(aether._check_ollama(), "offline")

    def test_an_odd_answer_is_unknown(self):
        with mock.patch("tools.aether.hermes.get", return_value=_http(500, "")):
            self.assertEqual(aether._check_ollama(), "unknown")
        with mock.patch("tools.aether.hermes.get", side_effect=hermes.Timeout("slow")):
            self.assertEqual(aether._check_ollama(), "unknown")

    def test_it_asks_the_same_ollama_pythia_talks_to(self):
        # tools/ must not import the brain, so the address is derived twice —
        # this is what keeps the two derivations in lockstep.
        import pythia
        self.assertEqual(aether._OLLAMA_VERSION_URL, pythia._BASE_URL + "/api/version")

    def test_a_stray_scheme_in_the_host_is_tolerated(self):
        for host in ("127.0.0.1:11435", "http://127.0.0.1:11435",
                     "https://127.0.0.1:11435"):
            with self.subTest(host=host):
                self.assertEqual(aether._base_url(host), "http://127.0.0.1:11435")


class TestHandleContract(unittest.TestCase):
    def test_shape_and_keys(self):
        with mock.patch("tools.aether._check_wifi", return_value="connected"), \
                mock.patch("tools.aether._check_internet", return_value="online"), \
                mock.patch("tools.aether._check_ollama", return_value="offline"):
            out = aether.handle()
        self.assertEqual(out, {"wifi": "connected", "internet": "online",
                               "ollama": "offline"})

    def test_degrades_to_unknown_on_total_io_failure(self):
        # The real never-raise guarantee: handle() does not wrap the probes — it
        # relies on them fail-safing. With every I/O layer raising something
        # unexpected, handle() must still answer and never propagate.
        with mock.patch("subprocess.run", side_effect=OSError("no netsh")), \
                mock.patch("tools.aether.hermes.get", side_effect=RuntimeError("no network")):
            out = aether.handle()
        self.assertEqual(out, {"wifi": "unknown", "internet": "unknown",
                               "ollama": "unknown"})

    def test_nothing_still_points_at_anthropic(self):
        # Pythia is gemma4 on a local Ollama; the Anthropic Statuspage check was
        # a leftover from the retired Claude-API brain.
        import inspect
        source = inspect.getsource(aether).lower()
        self.assertNotIn("anthropic.com", source)
        desc = aether.TOOL_DEFINITION["function"]["description"].lower()
        for word in ("anthropic", "claude"):
            self.assertNotIn(word, desc)

    def test_fetch_is_handle(self):
        # Kairos polls fetch(); the model calls handle(). They are the same object
        # on purpose (poll path == read path), so the never-raise holds for both.
        self.assertIs(aether.fetch, aether.handle)


if __name__ == "__main__":
    unittest.main(verbosity=2)
