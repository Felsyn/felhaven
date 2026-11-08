"""
test_hermes.py — the bounded network reader.

`timeout=` bounds each socket operation, not a request: a server that drips one
byte every tenth of a second never trips a half-second timeout, and would hold
a reader (and Aura's cache lock) for as long as it liked. These tests play that
server for real, on 127.0.0.1 (tests/_loopback.py), because what hermes bounds
is time spent inside real socket calls, and no mock can drip.

Each drip test gives the request a 1 s deadline and a 0.5 s per-operation
timeout, against a server that sends a byte every 0.1 s for 6 s. The request
must end with DeadlinePassed well before the drip does. Without the deadline it
would run the whole 6 s and end some other way, so both halves of the
assertion (the type and the time) fail red.

The TLS read path past the handshake is tested on an unhandshaken socket
(OpenSSL's read performs the handshake), because a real TLS server here would
need a certificate, and the repo has neither a way to mint one nor a private
key it should publish (CONVENTIONS §12, 2026-10-01).

Run from the package root:
    python -X utf8 -m unittest tests.test_hermes
"""

import http.client
import os
import socket
import ssl
import sys
import threading
import time
import unittest
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import hermes
from tests._loopback import Loopback, drip, read_head

_DEADLINE = 1.0
_OP_TIMEOUT = 0.5
_INTERVAL = 0.1
_DRIP = b"x" * 60          # 6 s at _INTERVAL: far past the deadline
_SLACK = 1.5               # generous for a busy CI runner; a miss is seconds


def _get(url, **kw):
    kw.setdefault("cap", 65536)
    kw.setdefault("timeout", _OP_TIMEOUT)
    kw.setdefault("deadline", _DEADLINE)
    kw.setdefault("allow_http", True)
    return hermes.get(url, **kw)


def _head(status="200 OK", *lines):
    return ("HTTP/1.1 " + status + "\r\n" + "".join(l + "\r\n" for l in lines)
            + "\r\n").encode()


class TheDeadlineEndsTheWholeRequest(unittest.TestCase):

    def _assert_cut_at_deadline(self, script, scheme="http", timeout=_OP_TIMEOUT):
        with Loopback(script) as srv:
            t0 = time.monotonic()
            with self.assertRaises(hermes.DeadlinePassed):
                _get(srv.url(scheme=scheme), timeout=timeout)
            elapsed = time.monotonic() - t0
        self.assertLess(elapsed, _DEADLINE + _SLACK)

    def test_a_dripped_body_is_cut_off_at_the_deadline(self):
        def script(conn, stop):
            read_head(conn)
            conn.sendall(_head("200 OK", "Content-Length: 1000"))
            drip(conn, stop, _DRIP, _INTERVAL)
        self._assert_cut_at_deadline(script)

    def test_dripped_headers_are_cut_off_too(self):
        """A deadline checked only around the body would never see this."""
        def script(conn, stop):
            read_head(conn)
            conn.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
            drip(conn, stop, _DRIP, _INTERVAL)
        self._assert_cut_at_deadline(script)

    def test_a_dripped_tls_handshake_is_cut_off(self):
        """A TLS record header that promises 16 KB, then one byte at a time.
        No certificate is needed: the handshake never gets that far.

        CPython already runs a whole handshake against ONE socket timeout, so
        a handshake drip was never unbounded; what the deadline adds is that
        the handshake gets only what is left. So here the per-op timeout is
        5 s and the deadline 1 s: without hermes's handshake override the
        handshake would wait the full 5."""
        def script(conn, stop):
            conn.recv(65536)                     # the ClientHello
            conn.sendall(b"\x16\x03\x03\x40\x00")
            drip(conn, stop, b"\x00" * 60, _INTERVAL)
        self._assert_cut_at_deadline(script, scheme="https", timeout=5)

    def test_a_stalled_body_ends_at_the_deadline_never_cut_short(self):
        """Half the body, then silence. The per-op timeout is 5 s and the
        deadline 1 s, so it is the deadline that must end it: each recv gets
        only what is left, not a fresh 5 s. And it ends in an error, never a
        50-byte body passed off as the whole thing."""
        def script(conn, stop):
            read_head(conn)
            conn.sendall(_head("200 OK", "Content-Length: 100") + b"y" * 50)
            stop.wait(8)                         # then nothing
        with Loopback(script) as srv:
            t0 = time.monotonic()
            with self.assertRaises(hermes.DeadlinePassed):
                _get(srv.url(), timeout=5)
            self.assertLess(time.monotonic() - t0, _DEADLINE + _SLACK)

    def test_the_default_deadline_follows_the_rule(self):
        self.assertEqual(hermes.deadline_for(65536, 6), 12)
        self.assertEqual(hermes.deadline_for(36_700_160, 120), 36_700_160 / 100_000)


class WithinSetsTheBudget(unittest.TestCase):

    def test_outside_a_deadline_sockets_keep_their_own_timeout(self):
        self.assertIsNone(hermes._budget())

    def test_the_budget_is_the_smaller_of_timeout_and_time_left(self):
        with hermes.within(10, 0.5):
            self.assertLessEqual(hermes._budget(), 0.5)
        with hermes.within(0.2, 5):
            self.assertLessEqual(hermes._budget(), 0.2)

    def test_a_nested_deadline_can_shorten_but_never_extend(self):
        with hermes.within(0.3, 5):
            with hermes.within(60, 5):
                self.assertLessEqual(hermes._budget(), 0.3)

    def test_the_outer_deadline_comes_back_after_a_nested_one(self):
        with hermes.within(10, 5):
            with hermes.within(0.1, 5):
                pass
            self.assertGreater(hermes._budget(), 1)
        self.assertIsNone(hermes._budget())

    def test_no_time_left_is_a_deadline_error_before_any_io(self):
        with hermes.within(0.01, 5):
            time.sleep(0.03)
            with self.assertRaises(hermes.DeadlinePassed):
                hermes._budget()

    def test_each_thread_has_its_own_deadline(self):
        seen = []
        with hermes.within(0.2, 5):
            t = threading.Thread(target=lambda: seen.append(hermes._budget()))
            t.start()
            t.join()
        self.assertEqual(seen, [None])


class TheSocketsConsultTheBudget(unittest.TestCase):
    """The wiring, one call at a time. A socket whose own timeout is 5 s must
    give up at the deadline instead."""

    def test_a_plain_socket_recv(self):
        a, b = socket.socketpair()
        self.addCleanup(b.close)
        a.settimeout(5)
        sock = hermes._adopt(a)
        self.addCleanup(sock.close)
        self.assertIsInstance(sock, hermes._DeadlineSocket)
        self.assertEqual(sock.gettimeout(), 5)
        t0 = time.monotonic()
        with hermes.within(0.3, 5), self.assertRaises(hermes.DeadlinePassed):
            sock.recv(10)
        self.assertLess(time.monotonic() - t0, 0.3 + _SLACK)

    def test_the_tls_context_builds_deadline_sockets(self):
        self.assertIs(hermes.TLS_CONTEXT.sslsocket_class, hermes._DeadlineSSLSocket)

    def test_every_https_connection_uses_that_context(self):
        """A handler's context= never reaches a connection class passed to
        do_open by an overriding https_open; the connection sets it itself."""
        conn = hermes._HTTPSConnection("example.invalid", 443, timeout=1)
        self.assertIs(conn._context, hermes.TLS_CONTEXT)

    def test_a_tls_read_gives_up_at_the_deadline(self):
        """read() on a socket that hasn't shaken hands makes OpenSSL do the
        handshake inside the read, and the peer never answers. Without the
        override the read would wait the socket's own 5 s."""
        a, b = socket.socketpair()
        self.addCleanup(b.close)
        a.settimeout(5)
        tls = hermes.TLS_CONTEXT.wrap_socket(
            a, server_hostname="example.invalid", do_handshake_on_connect=False)
        self.addCleanup(tls.close)
        t0 = time.monotonic()
        with hermes.within(0.3, 5), self.assertRaises(hermes.DeadlinePassed):
            tls.read(10)
        self.assertLess(time.monotonic() - t0, 0.3 + _SLACK)


def _client_hello(context):
    """The ClientHello `context` sends, read from memory: no peer, no cert."""
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    tls = context.wrap_bio(incoming, outgoing, server_hostname="example.invalid")
    try:
        tls.do_handshake()
    except ssl.SSLWantReadError:
        pass                                    # waiting for a server that isn't there
    return outgoing.read()


def _extension_types(hello):
    """The extension type numbers in a ClientHello record, in order."""
    i = 5 + 4 + 2 + 32                          # record, handshake, version, random
    i += 1 + hello[i]                           # session id
    i += 2 + int.from_bytes(hello[i:i + 2])     # cipher suites
    i += 1 + hello[i]                           # compression methods
    end = i + 2 + int.from_bytes(hello[i:i + 2])
    i += 2
    types = []
    while i < end:
        types.append(int.from_bytes(hello[i:i + 2]))
        i += 4 + int.from_bytes(hello[i + 2:i + 4])
    return types


class TheHandshakeIsTheOneUrllibSends(unittest.TestCase):
    """Hacker News answered every fetch 419 "Sorry" to a bare
    create_default_context() handshake, and 200 to the one http.client builds
    for itself, which adds ALPN and post-handshake auth (2026-11-07). Each
    ClientHello is read from a MemoryBIO, so no server or certificate is
    needed."""

    def test_it_offers_the_extensions_http_client_offers_on_its_own(self):
        """If a Python release changes what http.client adds, this goes red
        and names the extensions hermes is now missing or has extra."""
        own = http.client.HTTPSConnection("example.invalid")._context
        self.assertEqual(_extension_types(_client_hello(hermes.TLS_CONTEXT)),
                         _extension_types(_client_hello(own)))

    def test_alpn_offers_http_1_1(self):
        # Extension 16 with 11 bytes of data: a 9-byte list, one 8-byte name.
        self.assertIn(b"\x00\x10\x00\x0b\x00\x09\x08http/1.1",
                      _client_hello(hermes.TLS_CONTEXT))


class TheCapRefusesBigBodies(unittest.TestCase):

    def test_a_declared_length_over_the_cap_is_refused_before_reading(self):
        """The server says 1 MB and then sends nothing. Reading would wait;
        refusing on the header is immediate."""
        def script(conn, stop):
            read_head(conn)
            conn.sendall(_head("200 OK", "Content-Length: 1000000"))
            stop.wait(5)
        with Loopback(script) as srv:
            t0 = time.monotonic()
            with self.assertRaises(hermes.TooLarge):
                _get(srv.url(), cap=1000, deadline=5)
            self.assertLess(time.monotonic() - t0, _OP_TIMEOUT)

    def _assert_counted(self, head, body):
        def script(conn, stop):
            read_head(conn)
            conn.sendall(head + body)
        with Loopback(script) as srv:
            with self.assertRaises(hermes.TooLarge):
                _get(srv.url(), cap=1000, deadline=5)

    def test_a_body_with_no_length_is_counted(self):
        self._assert_counted(_head("200 OK", "Connection: close"), b"z" * 1001)

    def test_a_chunked_body_is_counted(self):
        chunks = b"".join(b"200\r\n" + b"c" * 0x200 + b"\r\n" for _ in range(3)) + b"0\r\n\r\n"
        self._assert_counted(_head("200 OK", "Transfer-Encoding: chunked"), chunks)

    def test_a_body_exactly_at_the_cap_is_fine(self):
        def script(conn, stop):
            read_head(conn)
            conn.sendall(_head("200 OK", "Content-Length: 1000") + b"k" * 1000)
        with Loopback(script) as srv:
            self.assertEqual(len(_get(srv.url(), cap=1000, deadline=5).content), 1000)

    def test_a_compressed_body_is_refused_not_inflated(self):
        """We ask for identity; counting a gzip body's bytes would count the
        wrong thing, so it isn't read at all."""
        def script(conn, stop):
            read_head(conn)
            conn.sendall(_head("200 OK", "Content-Encoding: gzip",
                               "Content-Length: 4") + b"\x1f\x8b\x08\x00")
        with Loopback(script) as srv:
            with self.assertRaises(hermes.Refused):
                _get(srv.url(), deadline=5)


class ThePolicy(unittest.TestCase):

    def test_plain_http_needs_allow_http(self):
        with self.assertRaises(hermes.Refused):
            hermes.get("http://127.0.0.1:9/", cap=1000, timeout=1)

    def test_other_schemes_are_refused_before_any_handler_runs(self):
        """urllib's opener also has file:, ftp: and data: handlers. A URL from
        a config file must never reach them."""
        for url in ("file:///C:/Windows/win.ini", "ftp://example.invalid/x",
                    "data:text/plain,hi", "no-scheme-at-all", ""):
            with self.subTest(url=url), self.assertRaises(hermes.Refused):
                hermes.get(url, cap=1000, timeout=1, allow_http=True)

    def test_a_redirect_may_not_leave_https(self):
        req = urllib.request.Request("https://a.example.invalid/x")
        with self.assertRaises(hermes.Refused):
            hermes._StayOnHttps().redirect_request(
                req, None, 302, "Found", {}, "http://b.example.invalid/y")

    def test_a_redirect_that_stays_on_https_is_followed(self):
        req = urllib.request.Request("https://a.example.invalid/x")
        new = hermes._StayOnHttps().redirect_request(
            req, None, 302, "Found", {}, "https://b.example.invalid/y")
        self.assertEqual(new.full_url, "https://b.example.invalid/y")

    def test_redirects_are_followed_by_default(self):
        def script(conn, stop):
            head = read_head(conn)
            if head.startswith(b"GET /final"):
                conn.sendall(_head("200 OK", "Content-Length: 2") + b"ok")
            else:
                conn.sendall(_head("302 Found", "Location: /final", "Content-Length: 0"))
        with Loopback(script) as srv:
            reply = _get(srv.url("/start"), deadline=5)
        self.assertEqual((reply.status_code, reply.content), (200, b"ok"))
        self.assertTrue(reply.url.endswith("/final"))

    def test_follow_redirects_false_hands_back_the_3xx(self):
        def script(conn, stop):
            read_head(conn)
            conn.sendall(_head("302 Found", "Location: /elsewhere", "Content-Length: 0"))
        with Loopback(script) as srv:
            reply = _get(srv.url(), deadline=5, follow_redirects=False)
        self.assertEqual(reply.status_code, 302)
        self.assertEqual(reply.headers.get("Location"), "/elsewhere")

    def test_an_error_status_is_a_reply_until_asked(self):
        def script(conn, stop):
            read_head(conn)
            conn.sendall(_head("404 Not Found", "Content-Length: 4") + b"gone")
        with Loopback(script) as srv:
            reply = _get(srv.url(), deadline=5)
        self.assertEqual((reply.status_code, reply.content), (404, b"gone"))
        with self.assertRaises(hermes.HTTPStatus) as caught:
            reply.raise_for_status()
        self.assertEqual(caught.exception.status, 404)

    def test_nothing_listening_is_offline(self):
        probe = socket.create_server(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        with self.assertRaises(hermes.Offline):
            hermes.get(f"http://127.0.0.1:{port}/", cap=1000, timeout=5, allow_http=True)

    def test_a_connect_that_times_out_is_offline_as_well_as_a_timeout(self):
        """Windows refuses a closed local port only after ~2 s, so a 2 s
        probe of a stopped Ollama times out instead of being refused. requests
        counted that as a connection error too, and Aether's "offline" relied
        on it."""
        with mock.patch.object(socket.socket, "connect",
                               side_effect=TimeoutError("timed out")):
            with self.assertRaises(hermes.ConnectTimeout) as caught:
                hermes.get("http://127.0.0.1:9/", cap=1000, timeout=1,
                           deadline=30, allow_http=True)
        self.assertIsInstance(caught.exception, hermes.Offline)
        self.assertIsInstance(caught.exception, hermes.Timeout)
        self.assertNotIsInstance(caught.exception, hermes.DeadlinePassed)

    def test_a_deadline_that_ends_during_the_connect_stays_a_deadline(self):
        def slow(addr):
            time.sleep(0.3)
            raise TimeoutError("timed out")
        with mock.patch.object(socket.socket, "connect", side_effect=slow):
            with self.assertRaises(hermes.DeadlinePassed):
                hermes.get("http://127.0.0.1:9/", cap=1000, timeout=5,
                           deadline=0.2, allow_http=True)

    def test_every_request_asks_for_identity_and_names_felhaven(self):
        def script(conn, stop):
            read_head(conn, srv.requests)
            conn.sendall(_head("200 OK", "Content-Length: 0"))
        with Loopback(script) as srv:
            _get(srv.url(), deadline=5)
            _get(srv.url(), deadline=5, headers={"Accept-Encoding": "gzip"})
        for head in srv.requests:
            self.assertIn(b"accept-encoding: identity", head.lower())
            self.assertIn(b"user-agent: felhaven/1.0", head.lower())

    def test_a_url_with_spaces_or_accents_is_percent_encoded(self):
        """requests did this quietly, and a Themis location such as "Zürich"
        (Aura puts it in the path) depends on it: http.client would refuse
        the raw string. An escape that is already there stays as it is."""
        def script(conn, stop):
            read_head(conn, srv.requests)
            conn.sendall(_head("200 OK", "Content-Length: 0"))
        with Loopback(script) as srv:
            _get(srv.url("/Zürich am See?q=a%2Fb c"), deadline=5)
        line = srv.requests[0].split(b"\r\n", 1)[0]
        self.assertEqual(line, b"GET /Z%C3%BCrich%20am%20See?q=a%2Fb%20c HTTP/1.1")

    def test_a_refusal_never_carries_the_url(self):
        """Midas's key travels in the query string."""
        def script(conn, stop):
            read_head(conn)
            conn.sendall(_head("200 OK", "Content-Length: 5000"))
        with Loopback(script) as srv:
            with self.assertLogs("METIS.hermes", "WARNING") as logs, \
                    self.assertRaises(hermes.TooLarge) as caught:
                _get(srv.url("/q?token=fake-secret-token"), cap=1000, deadline=5)
        self.assertNotIn("fake-secret-token", str(caught.exception))
        self.assertNotIn("fake-secret-token", "\n".join(logs.output))


class TheReply(unittest.TestCase):

    def _reply(self, status=200, body=b"", ctype="text/plain; charset=utf-8"):
        def script(conn, stop):
            read_head(conn)
            conn.sendall(_head(f"{status} X", f"Content-Type: {ctype}",
                               f"Content-Length: {len(body)}") + body)
        with Loopback(script) as srv:
            return _get(srv.url(), deadline=5)

    def test_text_uses_the_declared_charset(self):
        reply = self._reply(body="café".encode("latin-1"),
                            ctype="text/plain; charset=latin-1")
        self.assertEqual(reply.text, "café")

    def test_json(self):
        self.assertEqual(self._reply(body=b'{"a": 1}').json(), {"a": 1})

    def test_raise_for_status_draws_the_line_at_400(self):
        self._reply(status=399).raise_for_status()
        with self.assertRaises(hermes.HTTPStatus):
            self._reply(status=500).raise_for_status()


if __name__ == "__main__":
    unittest.main()
