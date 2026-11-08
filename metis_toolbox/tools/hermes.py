"""
hermes.py — Messenger of the Gods, Keeper of the Boundary Stones
================================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Read from the network, bounded: a byte cap and a deadline for the
             whole request. The deadline covers the request, not just each
             socket operation. Named for Hermes, the messenger, whose herms
             were the boundary stones at the edge of every field: a message,
             and a limit it doesn't cross.

Contract:    Plain functions, not a brain tool:
               • get(url, *, cap, timeout, ...) -> Reply. One GET. Raises a
                 NetError subclass for anything the network or the response
                 did; a non-2xx status is a Reply, not an error, until the
                 caller asks reply.raise_for_status() (the requests shape the
                 readers already used).
               • within(seconds, timeout), read_bounded(resp, cap),
                 bounded_dial(dial), TLS_CONTEXT: the same bounds for a
                 caller with its own opener (Callimachus's address guard).
             Errors, one vocabulary, and no message ever carries the URL (a
             query can hold a key):
               NetError ─┬─ Offline          no connection, or it dropped
                         ├─ Timeout ─ DeadlinePassed   (also TimeoutError)
                         │  (ConnectTimeout is both Offline and Timeout)
                         ├─ CertError        TLS could not be established
                         ├─ HTTPStatus       .status; from raise_for_status()
                         ├─ TooLarge         the body passed its cap
                         └─ Refused          a rule here said no (scheme,
                                             redirect off https, encoding)

Bounds:      `timeout=` in urllib and requests limits EACH socket operation.
             A server that sends one byte every few seconds never trips it,
             and a body of any size is read into memory. So:
               • The DEADLINE lives in the socket. Every connect, TLS
                 handshake, send and recv first sets the socket's timeout to
                 min(timeout, time left), so one mechanism bounds the headers
                 as well as the body. No thread is started or abandoned: the
                 read ends in the thread that asked, which matters because
                 Aura and Pheme hold their cache lock across a fetch.
               • The CAP is checked twice: a declared Content-Length over it
                 is refused before reading, then the body is read to one byte
                 past the cap, so an absent or lying length can't sneak past.
               • Bytes counted are bytes held: every request says
                 Accept-Encoding: identity, and a compressed answer is
                 refused rather than inflated. Measured 2026-10-01: no
                 provider Felhaven reads compresses when asked not to.
               • The default deadline is one rule, deadline_for(): twice the
                 per-operation timeout, or long enough to read the whole cap
                 at 100 kB/s, whichever is longer. Each caller states its own
                 cap (measured, dated, beside the constant); none is a default.
             NOT bounded: the DNS lookup. getaddrinfo can't be interrupted,
             and abandoning a thread is the one fix not allowed here. See
             CONVENTIONS §12, 2026-10-01.

Policy:      https only, unless a call passes allow_http=True (Aether's probe
             and the local Ollama server). A redirect may never leave https,
             and is followed at most _MAX_REDIRECTS times. No proxies from
             the environment. TLS is verified, always, and the handshake is
             the one http.client builds for itself (ALPN, post-handshake
             auth): a bare context's is refused by some edges.

Logging:     One WARNING (host only) when a cap or a deadline cuts a request
             short. Those are the events worth seeing in Emanon; every other
             failure is the caller's to log, with its own slug.

Upstream:    every runtime network reader: aura, pheme, midas, aether,
             eratosthenes, demeter, artemis (for hecate and poseidon), and
             callimachus through its own opener.
Downstream:  none. Stdlib only.

Requires:    stdlib only — contextlib, http.client, json, logging, socket,
             ssl, threading, time, urllib.
"""

import contextlib
import http.client
import json
import logging
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Buffer, Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("METIS.hermes")

_MAX_REDIRECTS = 5
_SLOWEST_BYTES_PER_S = 100_000   # the slowest link the default deadline waits for
_USER_AGENT = "Felhaven/1.0"


# ── Errors ────────────────────────────────────────────────────────────────────

class NetError(Exception):
    """The request did not produce a usable response."""


class Offline(NetError):
    """No connection: DNS failed, refused, reset, unreachable, or dropped."""


class Timeout(NetError, TimeoutError):
    """A socket operation timed out. Also a TimeoutError, so code that already
    catches socket.timeout (Callimachus) maps it with no new arm."""


class DeadlinePassed(Timeout):
    """The whole request ran past its deadline."""


class ConnectTimeout(Offline, Timeout):
    """No connection was made within the per-op timeout. Both Offline and
    Timeout on purpose, as requests' ConnectTimeout was both: on Windows a
    connection to a CLOSED local port is refused only after ~2 s (measured
    2026-10-03: 2.03 s), so a probe with a 2 s timeout sees a timeout where
    the truth is "nothing is listening". Callers that ask Timeout first
    (Aura, Pheme) answer as before; Aether's "Offline means offline" holds."""


class CertError(NetError):
    """TLS could not be established (certificate verification included)."""


class HTTPStatus(NetError):
    """A status of 400 or more, raised by Reply.raise_for_status()."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


class TooLarge(NetError):
    """The body is bigger than the caller's cap."""


class Refused(NetError):
    """A rule here said no: the scheme, a redirect off https, an encoding."""


# ── The deadline, per thread ──────────────────────────────────────────────────
# Each request runs entirely in its caller's thread, so the deadline is kept
# per thread: Pheme's pool fetches seven feeds at once, each against its own.

_local = threading.local()


def deadline_for(cap: int, timeout: float) -> float:
    """The default deadline: twice the per-operation timeout, or the time to
    read the whole cap at _SLOWEST_BYTES_PER_S, whichever is longer."""
    return max(2.0 * timeout, cap / _SLOWEST_BYTES_PER_S)


@contextlib.contextmanager
def within(seconds: float, timeout: float) -> Iterator[None]:
    """Every socket operation in this thread, inside the block, gets at most
    min(timeout, time left). A nested deadline can shorten the outer one,
    never extend it."""
    outer = (getattr(_local, "end", None), getattr(_local, "op_timeout", None))
    end = time.monotonic() + seconds
    if outer[0] is not None:
        end = min(end, outer[0])
    _local.end, _local.op_timeout = end, timeout
    try:
        yield
    finally:
        _local.end, _local.op_timeout = outer
        _local.deadline_bound = False


def _budget() -> "float | None":
    """The timeout the next socket operation may use: None outside a deadline
    (the socket keeps its own), else min(per-op timeout, time left). Raises
    DeadlinePassed when nothing is left. Notes whether the deadline, not the
    per-op timeout, set the figure, so a timeout can be named correctly."""
    end: "float | None" = getattr(_local, "end", None)
    if end is None:
        _local.deadline_bound = False
        return None
    left = end - time.monotonic()
    if left <= 0:
        raise DeadlinePassed("the request ran past its deadline")
    op_timeout: float = _local.op_timeout
    _local.deadline_bound = left <= op_timeout
    return min(op_timeout, left)


def _arm(sock: socket.socket) -> None:
    budget = _budget()
    if budget is not None:
        sock.settimeout(budget)


def _timed_out() -> Timeout:
    """What a socket timeout means. If the operation that timed out was given
    only what was left of the deadline, the deadline is what ran out; else it
    was one slow operation under the per-op timeout."""
    if getattr(_local, "deadline_bound", False):
        return DeadlinePassed("the request ran past its deadline")
    return Timeout("a network operation timed out")


class _DeadlineSocket(socket.socket):
    """A TCP socket that re-arms its timeout from the deadline before every
    send and recv. http.client reads through makefile() -> SocketIO, which
    calls recv_into, so the headers are bounded as well as the body."""

    def recv_into(self, buffer: Buffer, nbytes: int = 0, flags: int = 0) -> int:
        _arm(self)
        try:
            return super().recv_into(buffer, nbytes, flags)
        except TimeoutError:
            raise _timed_out() from None

    def recv(self, bufsize: int, flags: int = 0) -> bytes:
        _arm(self)
        try:
            return super().recv(bufsize, flags)
        except TimeoutError:
            raise _timed_out() from None

    def send(self, data: Buffer, flags: int = 0) -> int:
        _arm(self)
        try:
            return super().send(data, flags)
        except TimeoutError:
            raise _timed_out() from None

    def sendall(self, data: Buffer, flags: int = 0) -> None:
        _arm(self)
        try:
            super().sendall(data, flags)
        except TimeoutError:
            raise _timed_out() from None


class _DeadlineSSLSocket(ssl.SSLSocket):
    """The TLS side. SSLSocket.recv_into calls self.read and sendall calls
    self.send, so these two plus the handshake cover every blocking call. Each
    of CPython's SSL calls runs to the socket timeout as ONE budget, so
    setting it from the deadline first bounds the whole call."""

    def do_handshake(self, block: bool = False) -> None:
        _arm(self)
        try:
            super().do_handshake(block)
        except TimeoutError:
            raise _timed_out() from None

    def read(self, len: int = 1024, buffer: Any = None) -> Any:
        _arm(self)
        try:
            return super().read(len, buffer)
        except TimeoutError:
            raise _timed_out() from None

    def send(self, data: Buffer, flags: int = 0) -> int:
        _arm(self)
        try:
            return super().send(data, flags)
        except TimeoutError:
            raise _timed_out() from None


# http.client adds two things to a context it builds itself, and nothing to
# one it is handed: ALPN offering http/1.1, and post-handshake auth. Without
# them the ClientHello has a fingerprint of its own, and some edges block it
# outright: Hacker News answered every fetch 419 "Sorry" while the same request
# with either extension got 200 (measured 2026-11-07). With both, the handshake
# is the one plain urllib sends.
TLS_CONTEXT = ssl.create_default_context()
TLS_CONTEXT.set_alpn_protocols(["http/1.1"])
TLS_CONTEXT.post_handshake_auth = True
TLS_CONTEXT.sslsocket_class = _DeadlineSSLSocket


def _adopt(sock: socket.socket) -> socket.socket:
    """Re-home a connected socket as a _DeadlineSocket (same OS handle).
    Anything that isn't a real socket (a test's stand-in) passes through."""
    if isinstance(sock, _DeadlineSocket) or type(sock) is not socket.socket:
        return sock
    timeout = sock.gettimeout()
    adopted = _DeadlineSocket(sock.family, sock.type, sock.proto, fileno=sock.detach())
    adopted.settimeout(timeout)
    return adopted


_Dial = Callable[..., socket.socket]


def bounded_dial(dial: _Dial) -> _Dial:
    """Wrap a connect function (socket.create_connection's shape) so the
    connect gets the deadline's budget and the socket it returns is a
    _DeadlineSocket. Callimachus wraps its address guard with this."""

    def _dial(address: "tuple[str, int]", timeout: Any = None,
              source_address: "tuple[str, int] | None" = None) -> socket.socket:
        budget = _budget()
        if budget is not None:
            timeout = budget if not isinstance(timeout, (int, float)) else min(timeout, budget)
        try:
            return _adopt(dial(address, timeout, source_address))
        except DeadlinePassed:
            raise
        except TimeoutError:
            err = _timed_out()
            if isinstance(err, DeadlinePassed):
                raise err from None
            raise ConnectTimeout("no connection was made in time") from None

    return _dial


def _connect(address: "tuple[str, int]", timeout: Any = None,
             source_address: "tuple[str, int] | None" = None) -> socket.socket:
    """socket.create_connection, but each address tried gets what is left of
    the deadline, not the full timeout again (a name with two addresses that
    both hang would otherwise cost twice)."""
    host, port = address
    last: "OSError | None" = None
    for family, kind, proto, _, addr in socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM):
        budget = _budget()
        sock = socket.socket(family, kind, proto)
        try:
            if isinstance(timeout, (int, float)):
                sock.settimeout(timeout if budget is None else min(timeout, budget))
            elif budget is not None:
                sock.settimeout(budget)
            if source_address:
                sock.bind(source_address)
            sock.connect(addr)
            return sock
        except OSError as exc:
            sock.close()
            last = exc
    raise last if last is not None else OSError("no address to connect to")


# ── The opener ────────────────────────────────────────────────────────────────

class _HTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = bounded_dial(_connect)


class _HTTPSConnection(http.client.HTTPSConnection):
    """The context is set HERE, not by the handler: HTTPSHandler only passes
    its context to do_open from its own https_open, which an override like
    ours replaces. A connection without TLS_CONTEXT would build plain
    SSLSockets, and the deadline would never reach the handshake."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["context"] = TLS_CONTEXT
        super().__init__(*args, **kwargs)
        self._create_connection = bounded_dial(_connect)


class _HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(_HTTPConnection, req)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(_HTTPSConnection, req)


class _StayOnHttps(urllib.request.HTTPRedirectHandler):
    """Follow redirects, capped, but never from https down to http: a
    provider's redirect would otherwise hand the fetch to anyone on the path.
    The same rule as Callimachus's _HttpsOnlyRedirect."""

    max_redirections = _MAX_REDIRECTS

    def redirect_request(self, req: urllib.request.Request, fp: Any, code: int,
                         msg: str, headers: Any, newurl: str
                         ) -> "urllib.request.Request | None":
        if (req.full_url.lower().startswith("https://")
                and not newurl.lower().startswith("https://")):
            raise Refused("a redirect tried to leave https")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """follow_redirects=False: a 3xx comes back as the Reply itself."""

    def redirect_request(self, req: urllib.request.Request, fp: Any, code: int,
                         msg: str, headers: Any, newurl: str
                         ) -> "urllib.request.Request | None":
        return None


def _build_opener(follow: bool) -> urllib.request.OpenerDirector:
    redirects: urllib.request.HTTPRedirectHandler = _StayOnHttps() if follow else _NoRedirects()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _HTTPHandler(), _HTTPSHandler(), redirects)


_FOLLOW = _build_opener(True)
_NO_FOLLOW = _build_opener(False)


# ── Reading ───────────────────────────────────────────────────────────────────

def read_bounded(resp: Any, cap: int) -> bytes:
    """The body of an open response, refused if it passes `cap`: first by a
    declared Content-Length, then by reading one byte past the cap. Inside a
    deadline, every recv under this read gets only what is left, so a slow
    drip ends at the deadline with an error, never a cut-short body."""
    encoding = str(resp.headers.get("Content-Encoding") or "identity").strip().lower()
    if encoding != "identity":
        raise Refused("a compressed body (identity was asked for)")
    declared = resp.headers.get("Content-Length")
    if declared and str(declared).strip().isdigit() and int(declared) > cap:
        raise TooLarge(f"declared {int(declared)} bytes, over the {cap}-byte cap")
    data = resp.read(cap + 1)
    if len(data) > cap:
        raise TooLarge(f"over the {cap}-byte cap")
    return bytes(data)


@dataclass(frozen=True)
class Reply:
    """A finished response: the subset of requests.Response the readers use."""

    status_code: int
    headers: Any            # http.client.HTTPMessage: case-insensitive .get()
    content: bytes
    url: str                # the final URL; may carry a key, so never log it

    @property
    def text(self) -> str:
        charset = None
        get_charset = getattr(self.headers, "get_content_charset", None)
        if callable(get_charset):
            charset = get_charset()
        try:
            return self.content.decode(charset or "utf-8", "replace")
        except LookupError:                     # a charset name Python lacks
            return self.content.decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self.content)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPStatus(self.status_code)


def _translate(exc: BaseException) -> NetError:
    """One vocabulary out of urllib's, http.client's, ssl's and socket's."""
    if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError):
        reason = exc.reason
        if isinstance(reason, NetError):
            return reason
        if isinstance(reason, TimeoutError):
            return _timed_out()
        if isinstance(reason, ssl.SSLError):
            return CertError("TLS could not be established")
        if isinstance(reason, OSError):
            return Offline("no connection")
        return NetError("the request could not be made")
    if isinstance(exc, NetError):
        return exc
    if isinstance(exc, TimeoutError):
        return _timed_out()
    if isinstance(exc, ssl.SSLError):
        return Offline("the TLS connection failed")
    if isinstance(exc, OSError):
        return Offline("the connection failed")
    if isinstance(exc, http.client.HTTPException):
        return NetError("a malformed or incomplete response")
    return NetError(type(exc).__name__)


def _with_params(url: str, params: "Mapping[str, str | int | float] | None") -> str:
    if not params:
        return url
    return url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)


# The characters requests' requote_uri leaves alone: the URL's own syntax, and
# "%" so an escape already there stays one. Everything else (a space, "ü", a
# control character) is percent-encoded as UTF-8. http.client refuses such a
# URL outright; requests quietly encoded it, and a Themis location like
# "Zürich" or a feed URL typed into config relies on that.
_URL_SAFE = "!#$%&'()*+,/:;=?@[]~"


def _requote(url: str) -> str:
    return urllib.parse.quote(url, safe=_URL_SAFE)


def get(url: str, *, cap: int, timeout: float, deadline: "float | None" = None,
        params: "Mapping[str, str | int | float] | None" = None,
        headers: "Mapping[str, str] | None" = None,
        follow_redirects: bool = True, allow_http: bool = False) -> Reply:
    """One bounded GET. See the module docstring for the bounds and the
    errors. `deadline` defaults to deadline_for(cap, timeout)."""
    full = _requote(_with_params(url, params))
    parts = urllib.parse.urlsplit(full)
    scheme = parts.scheme.lower()
    if scheme != "https" and not (allow_http and scheme == "http"):
        raise Refused(f"the {scheme or 'missing'} scheme is not allowed here")
    host = parts.hostname or "?"
    request = urllib.request.Request(
        full, headers={"User-Agent": _USER_AGENT, **(headers or {}),
                       "Accept-Encoding": "identity"})
    opener = _FOLLOW if follow_redirects else _NO_FOLLOW
    seconds = deadline if deadline is not None else deadline_for(cap, timeout)
    try:
        with within(seconds, timeout):
            try:
                resp: Any = opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as exc:
                resp = exc                      # a non-2xx is still a response
            try:
                body = read_bounded(resp, cap)
                status = int(getattr(resp, "status", None) or resp.code)
                return Reply(status, resp.headers, body, str(resp.geturl()))
            finally:
                resp.close()
    except Exception as exc:
        err = _translate(exc)
        if isinstance(err, (TooLarge, DeadlinePassed)):
            log.warning("Hermes: %s refused: %s", host, err)
        if err is exc:
            raise
        raise err from exc


if __name__ == "__main__":
    reply = get("http://www.msftconnecttest.com/connecttest.txt",
                cap=65536, timeout=5, allow_http=True)
    print(reply.status_code, repr(reply.text))
