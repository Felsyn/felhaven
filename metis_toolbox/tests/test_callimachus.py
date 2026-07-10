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
"""

import json
import os
import socket
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cerberus
import pythia
from tools import callimachus


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

    def test_malformed_json_is_search_failed(self):
        with _patch_open(_FakeResp(b"<html>not json</html>")):
            out = callimachus.search_web("q")
        self.assertTrue(out["error"].startswith("search_failed:"))

    def test_vault_locked_is_search_failed(self):
        # Override the setUp key patch with a locked-vault raise.
        with mock.patch.object(callimachus, "_brave_key",
                               side_effect=cerberus.VaultError("vault is locked")):
            out = callimachus.search_web("q")
        self.assertTrue(out["error"].startswith("search_failed:"))
        self.assertIn("locked", out["error"])


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
