"""
test_callimachus.py — unit tests for tools/callimachus.py (web search brain tool).

Hermetic: the single network seam (callimachus._OPENER.open) and the Brave-key
seam (callimachus._brave_key) are mocked, so no test touches the network or the
Cerberus Vault. Run from the package root:

    python -X utf8 -m unittest tests.test_callimachus

Covers the handoff's three phases:
  1. search_web — shaped results + every error path (429, timeout, no_results,
     bad JSON, vault locked).
  2. fetch_page — the HTML stripper, https-only rejection, the 1 MB cap, the
     char-budget truncation flag, and redirect-to-http rejection.
  3. Wiring — both tool names present exactly once in Pythia's registry and
     dispatch routing kwargs to the right handler.
  4. The address guard (CONVENTIONS §12, 2026-09-30) — only public internet
     addresses are dialled, on every redirect hop, pinned to the IP that was
     checked, with env proxies ignored. These run BELOW the _OPENER.open seam:
     DNS, the TCP dial and the TLS wrap are faked; http.client and urllib are
     real.
  5. The V0.03 walkthrough (S33) — ::/96 is not public, fetch_page refuses a
     page that isn't text, a title is capped, and search_web's catch-all logs
     a class only.
"""

import io
import json
import os
import socket
import ssl
import sys
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cerberus
import pythia
from tools import callimachus, hermes


class _FakeResp:
    """Stand-in for the object callimachus._OPENER.open returns: read(n),
    geturl(), a headers.get(), and close(). No network."""

    def __init__(self, body: bytes, url: str = "https://example.com/final",
                 content_length: "str | None" = None) -> None:
        self._body = body
        self._url = url
        self.headers = {"Content-Length": content_length} if content_length else {}
        self._pos = 0

    def geturl(self) -> str:
        return self._url

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            chunk = self._body[self._pos:]
        else:
            chunk = self._body[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def close(self) -> None:
        pass


def _patch_open(resp_or_exc):
    """Patch the one network seam to return a fake response (or raise)."""
    if isinstance(resp_or_exc, BaseException) or (
        isinstance(resp_or_exc, type) and issubclass(resp_or_exc, BaseException)
    ):
        return mock.patch.object(callimachus._OPENER, "open", side_effect=resp_or_exc)
    return mock.patch.object(callimachus._OPENER, "open", return_value=resp_or_exc)


# Two Brave-shaped result rows, one with <strong> highlight markup to prove the
# snippet/title get HTML-stripped.
_BRAVE_OK = {
    "web": {
        "results": [
            {"title": "Callimachus of <strong>Cyrene</strong>",
             "description": "A <strong>librarian</strong> of Alexandria.",
             "url": "https://en.wikipedia.org/wiki/Callimachus"},
            {"title": "The Pinakes", "description": "The first catalog.",
             "url": "https://example.com/pinakes"},
            {"title": "Three", "description": "third", "url": "https://ex.com/3"},
            {"title": "Four", "description": "fourth", "url": "https://ex.com/4"},
        ]
    }
}


class TestSearchWeb(unittest.TestCase):
    def setUp(self) -> None:
        # Every search test gets a fake key so the Vault is never consulted.
        self._key = mock.patch.object(callimachus, "_brave_key",
                                      return_value="fake-key")
        self._key.start()
        self.addCleanup(self._key.stop)

    def test_shapes_and_caps_results(self):
        with _patch_open(_FakeResp(json.dumps(_BRAVE_OK).encode())):
            out = callimachus.search_web("callimachus")
        self.assertNotIn("error", out)
        self.assertEqual(len(out["results"]), 3)          # 4 offered, capped at 3
        first = out["results"][0]
        self.assertEqual(set(first), {"title", "snippet", "url"})
        self.assertEqual(first["title"], "Callimachus of Cyrene")   # <strong> gone
        self.assertEqual(first["snippet"], "A librarian of Alexandria.")

    def test_the_search_response_is_capped(self):
        """It was read with no cap at all (cap=None); Brave's answer is
        bounded like every other read now."""
        big = _FakeResp(b"{" + b" " * callimachus._SEARCH_MAX_BYTES + b"}")
        with _patch_open(big):
            out = callimachus.search_web("anything")
        self.assertTrue(out["error"].startswith("unavailable: "))
        self.assertIn("size cap", out["error"])

    def test_the_search_runs_inside_a_deadline_and_asks_for_identity(self):
        seen = {}

        def opener(req, timeout=None):
            seen["budget"] = hermes._budget()
            seen["encoding"] = req.get_header("Accept-encoding")
            return _FakeResp(json.dumps(_BRAVE_OK).encode())

        with mock.patch.object(callimachus._OPENER, "open", side_effect=opener):
            callimachus.search_web("callimachus")
        self.assertIsNotNone(seen["budget"])
        self.assertLessEqual(seen["budget"], callimachus._TIMEOUT_S)
        self.assertEqual(seen["encoding"], "identity")
        self.assertIsNone(hermes._budget())            # and none leaks out after

    def test_empty_query_is_no_results(self):
        # Short-circuits before any network call.
        with mock.patch.object(callimachus._OPENER, "open") as opn:
            self.assertEqual(callimachus.search_web("   "), {"error": "no_results"})
            opn.assert_not_called()

    def test_empty_brave_results_is_no_results(self):
        with _patch_open(_FakeResp(json.dumps({"web": {"results": []}}).encode())):
            self.assertEqual(callimachus.search_web("nothing"),
                             {"error": "no_results"})

    def test_http_429_is_rate_limited(self):
        err = urllib.error.HTTPError("https://x", 429, "Too Many Requests", {}, None)
        with _patch_open(err):
            self.assertEqual(callimachus.search_web("q"), {"error": "rate_limited"})

    def test_timeout(self):
        with _patch_open(socket.timeout()):
            self.assertEqual(callimachus.search_web("q"), {"error": "timeout"})

    def test_malformed_json_is_unavailable(self):
        with _patch_open(_FakeResp(b"<html>not json</html>")):
            out = callimachus.search_web("q")
        self.assertTrue(out["error"].startswith("unavailable:"))

    def test_a_sealed_vault_is_vault_locked(self):
        # Override the setUp key patch with Cerberus's own refusals. The word
        # comes from its slug (the shared core, §2), Midas's two words.
        with mock.patch.object(callimachus, "_brave_key", side_effect=
                               cerberus.VaultError(cerberus.ERR_LOCKED)), \
                self.assertLogs("METIS.callimachus", "WARNING"):
            out = callimachus.search_web("q")
        self.assertEqual(out["error"], "vault_locked")

    def test_an_open_vault_with_no_brave_key_is_no_key(self):
        with mock.patch.object(callimachus, "_brave_key", side_effect=
                               cerberus.VaultError(cerberus.ERR_NO_ENTRY, "brave")), \
                self.assertLogs("METIS.callimachus", "WARNING"):
            out = callimachus.search_web("q")
        self.assertEqual(out["error"], "no_key")

    def test_any_other_vault_failure_is_unavailable_class_only(self):
        # Only the exception CLASS reaches the error dict — the VaultError
        # message is kept out of the LLM context (§2).
        with mock.patch.object(callimachus, "_brave_key", side_effect=
                               cerberus.VaultError(cerberus.ERR_CORRUPT, "secret")), \
                self.assertLogs("METIS.callimachus", "WARNING"):
            out = callimachus.search_web("q")
        self.assertEqual(out["error"], "unavailable: VaultError")
        self.assertNotIn("secret", out["error"])   # message gone, class only

    def test_url_error_reports_reason_class_not_message(self):
        # URLError.reason is a foreign socket/OS error that can carry a hostname
        # or errno path — only its class may reach the LLM, never its raw text.
        leaky = urllib.error.URLError(
            OSError("[Errno 11001] getaddrinfo failed: secret.internal.host"))
        with _patch_open(leaky):
            out = callimachus.search_web("q")
        self.assertEqual(out["error"], "unavailable: OSError")
        self.assertNotIn("secret.internal.host", out["error"])


class TestStripHtml(unittest.TestCase):
    def test_drops_script_style_head(self):
        html = ("<head><title>Title</title><style>.x{color:red}</style></head>"
                "<body><script>evil()</script>Hello <b>world</b></body>")
        text = callimachus._strip_html(html)
        self.assertEqual(text, "Hello world")
        for banned in ("Title", "color:red", "evil()"):
            self.assertNotIn(banned, text)

    def test_collapses_whitespace(self):
        self.assertEqual(callimachus._strip_html("<p>a   \n\n  b</p>"), "a b")

    def test_block_tags_keep_words_apart(self):
        # Adjacent blocks must not mash into "ab".
        self.assertEqual(callimachus._strip_html("<p>a</p><p>b</p>"), "a b")

    def test_decodes_entities(self):
        self.assertEqual(callimachus._strip_html("first&nbsp;second"),
                         "first second")


class TestFetchPage(unittest.TestCase):
    def test_non_https_rejected_before_network(self):
        with mock.patch.object(callimachus._OPENER, "open") as opn:
            self.assertEqual(callimachus.fetch_page("http://insecure.example"),
                             {"error": "non_https_url"})
            opn.assert_not_called()

    def test_success_returns_stripped_text(self):
        body = b"<html><body><h1>Title</h1><p>Body text here.</p></body></html>"
        with _patch_open(_FakeResp(body, url="https://site.example/page")):
            out = callimachus.fetch_page("https://site.example/page")
        self.assertEqual(out["url"], "https://site.example/page")
        self.assertFalse(out["truncated"])
        self.assertIn("Body text here.", out["text"])

    def test_truncation_sets_flag(self):
        body = ("<p>" + "x" * 500 + "</p>").encode()
        with mock.patch.object(callimachus, "_PAGE_CHARS", 100), \
                _patch_open(_FakeResp(body)):
            out = callimachus.fetch_page("https://site.example")
        self.assertTrue(out["truncated"])
        self.assertEqual(len(out["text"]), 100)

    def test_oversize_is_too_large(self):
        # Body one byte past a tiny patched cap → too_large (not a silent partial).
        with mock.patch.object(callimachus, "_MAX_DOWNLOAD_BYTES", 10), \
                _patch_open(_FakeResp(b"x" * 11)):
            self.assertEqual(callimachus.fetch_page("https://big.example"),
                             {"error": "too_large"})

    def test_a_page_fetch_runs_inside_a_deadline_and_asks_for_identity(self):
        seen = {}

        def opener(req, timeout=None):
            seen["budget"] = hermes._budget()
            seen["encoding"] = req.get_header("Accept-encoding")
            return _FakeResp(b"<p>hi</p>")

        with mock.patch.object(callimachus._OPENER, "open", side_effect=opener):
            callimachus.fetch_page("https://site.example")
        self.assertIsNotNone(seen["budget"])
        self.assertEqual(seen["encoding"], "identity")

    def test_a_deadline_cut_is_a_timeout(self):
        """hermes.DeadlinePassed is a TimeoutError, so the existing timeout
        arm maps it, whether it surfaces raw or wrapped by urllib."""
        for raised in (hermes.DeadlinePassed("past"),
                       urllib.error.URLError(hermes.DeadlinePassed("past"))):
            with self.subTest(raised=type(raised).__name__), _patch_open(raised):
                self.assertEqual(callimachus.fetch_page("https://slow.example"),
                                 {"error": "timeout"})

    def test_content_length_over_cap_is_too_large(self):
        with mock.patch.object(callimachus, "_MAX_DOWNLOAD_BYTES", 10), \
                _patch_open(_FakeResp(b"short", content_length="9999")):
            self.assertEqual(callimachus.fetch_page("https://big.example"),
                             {"error": "too_large"})

    def test_redirect_to_http_rejected_by_handler(self):
        # The handler is the enforcement point: an http target raises.
        handler = callimachus._HttpsOnlyRedirect()
        with self.assertRaises(callimachus._NonHttpsRedirect):
            handler.redirect_request(
                mock.Mock(), mock.Mock(), 302, "Found", {}, "http://evil.example")

    def test_redirect_to_http_surfaces_as_non_https_error(self):
        with _patch_open(callimachus._NonHttpsRedirect("http://evil.example")):
            self.assertEqual(callimachus.fetch_page("https://start.example"),
                             {"error": "non_https_url"})

    def test_timeout(self):
        with _patch_open(socket.timeout()):
            self.assertEqual(callimachus.fetch_page("https://slow.example"),
                             {"error": "timeout"})

    def test_url_error_reports_reason_class_not_message(self):
        # Same rail as search_web: a URLError.reason's raw text (host/errno)
        # must not ride into the LLM context — only its class.
        leaky = urllib.error.URLError(
            OSError("[Errno 11001] getaddrinfo failed: secret.internal.host"))
        with _patch_open(leaky):
            out = callimachus.fetch_page("https://gone.example")
        self.assertEqual(out["error"], "unavailable: OSError")
        self.assertNotIn("secret.internal.host", out["error"])


def _answers(*ips):
    """A getaddrinfo result: one TCP answer per IP, port 443."""
    return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM,
             6, "", (ip, 443)) for ip in ips]


class _WireSocket:
    """A TCP socket with a canned HTTP reply and no network. http.client
    writes the request into it and reads the reply back out of makefile()."""

    def __init__(self, reply: bytes) -> None:
        self._reply = reply
        self.sent = b""

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def makefile(self, mode: str, *args, **kwargs):
        return io.BytesIO(self._reply)

    def setsockopt(self, *args) -> None:
        pass

    def close(self) -> None:
        pass


class TestAddressGuard(unittest.TestCase):
    """fetch_page reaches the public internet only.

    Everything below the opener is exercised for real (http.client, urllib's
    redirect handling, the guarded connection); only name resolution, the TCP
    dial and the TLS wrap are faked, so no test touches the network."""

    _PUBLIC = ("8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "64:ff9b::808:808")
    _NOT_PUBLIC = (
        "127.0.0.1", "127.1.2.3", "::1",                      # this computer
        "10.0.0.5", "172.16.0.1", "192.168.1.1",              # the LAN
        "169.254.169.254", "fe80::1", "fe80::1%3",            # link-local
        "fd00::1", "100.64.0.1",                              # ULA, carrier NAT
        "0.0.0.0", "255.255.255.255", "224.0.0.1", "ff02::1",  # special, multicast
        "::ffff:127.0.0.1", "2002:c0a8:101::1", "64:ff9b::7f00:1",  # v4 inside v6
        "192.0.2.1", "not-an-ip",                             # reserved, junk
    )

    def test_public_addresses_pass(self):
        for ip in self._PUBLIC:
            with self.subTest(ip=ip):
                self.assertTrue(callimachus._is_public(ip))

    def test_local_and_reserved_addresses_are_refused(self):
        for ip in self._NOT_PUBLIC:
            with self.subTest(ip=ip):
                self.assertFalse(callimachus._is_public(ip))

    def test_private_answer_is_refused_before_any_dial(self):
        with mock.patch("socket.getaddrinfo", return_value=_answers("10.0.0.5")), \
                mock.patch("socket.create_connection") as dial, self.assertLogs("METIS.callimachus", "WARNING"):
            with self.assertRaises(callimachus._PrivateAddress):
                callimachus._connect_public(("router.example", 443), 5)
        dial.assert_not_called()

    def test_one_private_answer_among_public_ones_is_enough(self):
        # A name answering public AND private is built to be tried twice.
        with mock.patch("socket.getaddrinfo",
                        return_value=_answers("8.8.8.8", "192.168.1.1")), \
                mock.patch("socket.create_connection") as dial, self.assertLogs("METIS.callimachus", "WARNING"):
            with self.assertRaises(callimachus._PrivateAddress):
                callimachus._connect_public(("mixed.example", 443), 5)
        dial.assert_not_called()

    def test_dials_the_checked_ip_not_the_name(self):
        # Pinning: the address that passed the check is the address dialled,
        # so a DNS answer that changes afterwards has nothing to change.
        with mock.patch("socket.getaddrinfo", return_value=_answers("8.8.8.8")), \
                mock.patch("socket.create_connection") as dial:
            callimachus._connect_public(("site.example", 443), 5)
        dial.assert_called_once_with(("8.8.8.8", 443), 5, None)

    def test_falls_through_to_the_next_checked_ip(self):
        sock = object()
        with mock.patch("socket.getaddrinfo",
                        return_value=_answers("8.8.8.8", "1.1.1.1")), \
                mock.patch("socket.create_connection",
                           side_effect=[OSError("unreachable"), sock]) as dial:
            self.assertIs(callimachus._connect_public(("site.example", 443), 5), sock)
        self.assertEqual([c.args[0] for c in dial.call_args_list],
                         [("8.8.8.8", 443), ("1.1.1.1", 443)])

    def test_connection_dials_through_the_guard_and_verifies_the_name(self):
        # The IP is pinned for TCP, but TLS still checks the certificate
        # against the NAME — pinning must cost no https guarantee.
        conn = callimachus._GuardedHTTPSConnection("site.example", 443, timeout=5)
        wire = _WireSocket(b"")
        with mock.patch("socket.getaddrinfo", return_value=_answers("8.8.8.8")), \
                mock.patch("socket.create_connection", return_value=wire) as dial, \
                mock.patch.object(conn._context, "wrap_socket",
                                  return_value=wire) as tls:
            conn.connect()
        dial.assert_called_once_with(("8.8.8.8", 443), 5, None)
        self.assertEqual(tls.call_args.kwargs["server_hostname"], "site.example")

    def test_fetch_page_reports_private_address(self):
        with mock.patch("socket.getaddrinfo", return_value=_answers("192.168.1.1")), \
                mock.patch("socket.create_connection") as dial, self.assertLogs("METIS.callimachus", "WARNING"):
            out = callimachus.fetch_page("https://router.example/admin")
        self.assertEqual(out, {"error": "private_address"})
        dial.assert_not_called()

    def test_every_redirect_hop_is_checked(self):
        # A public page that redirects to a LAN host: the first hop is dialled,
        # the second is refused at its own connect. Real urllib redirect
        # handling, real http.client; only DNS, the dial and TLS are faked.
        dns = {"start.example": _answers("8.8.8.8"),
               "internal.example": _answers("10.0.0.7")}
        wire = _WireSocket(b"HTTP/1.1 302 Found\r\n"
                           b"Location: https://internal.example/secret\r\n"
                           b"Content-Length: 0\r\n\r\n")
        with mock.patch("socket.getaddrinfo",
                        side_effect=lambda host, *a, **k: dns[host]), \
                mock.patch("socket.create_connection", return_value=wire) as dial, \
                mock.patch.object(ssl.SSLContext, "wrap_socket",
                                  lambda self, sock, **kw: sock), \
                self.assertLogs("METIS.callimachus", "WARNING"):
            out = callimachus.fetch_page("https://start.example/")
        self.assertEqual(out, {"error": "private_address"})
        self.assertIn(b"Host: start.example", wire.sent)       # hop 1 went out
        dial.assert_called_once_with(("8.8.8.8", 443), callimachus._TIMEOUT_S, None)

    def test_opener_ignores_a_proxy_in_the_environment(self):
        # Through a proxy, the guard would check the PROXY's address and the
        # proxy would dial the real target unchecked. urllib only registers a
        # ProxyHandler that has proxies to route, so "none registered" while
        # HTTPS_PROXY is set means the environment was ignored.
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.example:8080",
                                          "https_proxy": "http://proxy.example:8080"}):
            opener = callimachus._build_opener()
        self.assertFalse([h for h in opener.handlers
                          if isinstance(h, urllib.request.ProxyHandler)])

    def test_the_guarded_connection_carries_the_deadline(self):
        """The guard is unchanged; hermes only wraps it. The connection uses
        hermes' TLS context (deadline-aware TLS sockets) and a dial that
        hands back a deadline-aware TCP socket."""
        a, b = socket.socketpair()
        self.addCleanup(b.close)
        with mock.patch.object(callimachus, "_connect_public", return_value=a) as guard:
            conn = callimachus._GuardedHTTPSConnection("site.example", 443, timeout=5)
            sock = conn._create_connection(("site.example", 443), 5, None)
        self.assertIs(conn._context, hermes.TLS_CONTEXT)
        self.addCleanup(sock.close)
        guard.assert_called_once()
        self.assertIsInstance(sock, hermes._DeadlineSocket)

    def test_opener_routes_https_through_the_guard(self):
        https = [h for h in callimachus._OPENER.handlers
                 if isinstance(h, urllib.request.HTTPSHandler)]
        self.assertEqual([type(h) for h in https], [callimachus._GuardedHTTPSHandler])


class TestRegistryWiring(unittest.TestCase):
    def test_both_tools_registered_exactly_once(self):
        names = [t["function"]["name"] for t in pythia.TOOLS]
        for tool in ("search_web", "fetch_page"):
            self.assertEqual(names.count(tool), 1, f"{tool} not present exactly once")
            self.assertIn(tool, pythia._DISPATCH)

    def test_dispatch_binds_to_callimachus_functions(self):
        self.assertIs(pythia._DISPATCH["search_web"], callimachus.search_web)
        self.assertIs(pythia._DISPATCH["fetch_page"], callimachus.fetch_page)

    def test_dispatch_round_trips_kwargs(self):
        stub = {"search_web": lambda **kw: {"echo": kw}}
        with mock.patch.dict(pythia._DISPATCH, stub):
            out = pythia._dispatch("search_web", {"query": "hello"})
        self.assertEqual(out, {"echo": {"query": "hello"}})



# ─────────────────────────────────────────────────────────────────────────────
#  5. The V0.03 walkthrough (S33, 2026-11-23)
# ─────────────────────────────────────────────────────────────────────────────

class TestIPv4CompatibleIsNotPublic(unittest.TestCase):
    """Python counts ::/96, the deprecated IPv4-compatible form, as global, so
    the guard took ::127.0.0.1 for the public internet (measured; Windows
    answered "unreachable network", so nothing was reached)."""

    def test_the_ipv4_compatible_range_is_refused(self):
        for ip in ("::127.0.0.1", "::7f00:1", "::10.0.0.5", "::8.8.8.8"):
            with self.subTest(ip=ip):
                self.assertFalse(callimachus._is_public(ip))

    def test_an_ipv4_compatible_answer_is_never_dialled(self):
        with mock.patch("socket.getaddrinfo", return_value=_answers("::7f00:1")), \
                mock.patch("socket.create_connection") as dial, \
                self.assertLogs("METIS.callimachus", "WARNING"):
            with self.assertRaises(callimachus._PrivateAddress):
                callimachus._connect_public(("evil.example", 443), 5)
        dial.assert_not_called()

    def test_ordinary_public_ipv6_still_passes(self):
        for ip in ("2606:4700:4700::1111", "2001:4860:4860::8888", "64:ff9b::808:808"):
            with self.subTest(ip=ip):
                self.assertTrue(callimachus._is_public(ip))


class _TypedResp(_FakeResp):
    """A _FakeResp that names its Content-Type."""

    def __init__(self, body: bytes, content_type: str) -> None:
        super().__init__(body)
        self.headers = {"Content-Type": content_type}


class TestFetchPageReadsTextOnly(unittest.TestCase):
    """A PDF or an image came back as 4,000 characters of its bytes, about
    1,700 of them replacement characters, all handed to the model (measured
    live 2026-11-23)."""

    def test_a_pdf_is_refused_before_its_body_is_read(self):
        resp = _TypedResp(b"%PDF-1.4 " + bytes(range(256)) * 20, "application/pdf")
        with _patch_open(resp):
            out = callimachus.fetch_page("https://example.com/manual.pdf")
        self.assertEqual(out, {"error": "unavailable: not a text page (application/pdf)"})
        self.assertEqual(resp._pos, 0)

    def test_a_type_not_shaped_like_one_isnt_quoted(self):
        resp = _TypedResp(b"\x00\x01", "x" * 300)
        with _patch_open(resp):
            out = callimachus.fetch_page("https://example.com/odd")
        self.assertEqual(out, {"error": "unavailable: not a text page"})

    def test_text_xml_json_and_no_type_are_read(self):
        for ctype in ("text/html; charset=utf-8", "text/plain", "application/xhtml+xml",
                      "application/json", "application/rss+xml", ""):
            with self.subTest(ctype=ctype), _patch_open(_TypedResp(b"<p>hello</p>", ctype)):
                self.assertEqual(callimachus.fetch_page("https://example.com/").get("text"),
                                 "hello")


class TestSearchResultsAreBounded(unittest.TestCase):
    def test_a_long_title_is_capped_like_a_snippet(self):
        # Pythia sends a tool result to the model whole.
        data = {"web": {"results": [{"title": "t" * 5000, "description": "d",
                                     "url": "https://example.com/"}]}}
        (row,) = callimachus._shape_results(data)
        self.assertEqual(len(row["title"]), callimachus._SNIPPET_CHARS)


class TestSearchCatchAllLogsAClassOnly(unittest.TestCase):
    """The request carries the Brave key, and a keyed request's text is never
    logged unchecked (§12, 2026-11-18; BACKLOG from S10)."""

    def test_the_log_names_the_class_not_the_message(self):
        with mock.patch.object(callimachus, "_brave_key", return_value="fake-key"), \
                _patch_open(RuntimeError("detail that must stay out of the log")), \
                self.assertLogs("METIS.callimachus", "WARNING") as logs:
            out = callimachus.search_web("anything")
        self.assertEqual(out, {"error": "unavailable: RuntimeError"})
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("detail that must stay out", logs.output[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
