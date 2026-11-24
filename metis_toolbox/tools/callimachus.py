"""
callimachus.py — The Librarian of Alexandria (web search)
==========================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Ask the web a question and return trimmed text.
             Named for Callimachus of Cyrene, librarian of Alexandria and
             author of the Pinakes — the first catalog of all written works,
             i.e. the inventor of the search index (historical-figure naming
             per CONVENTIONS §12, the Zeno/Hypatia/Kepler precedent).

Contract:    First MULTI-TOOL module — exports TOOL_DEFINITIONS (a list of two)
             instead of the singular TOOL_DEFINITION, TOOL_META, and two
             handlers:
               • search_web(query) -> {"results": [{title, snippet, url}], ...}
               • fetch_page(url)   -> {"url", "text", "truncated"}
             The split lets a small model run the agentic loop — search, read
             snippets, CHOOSE one url, fetch it — inside one Pythia turn instead
             of firehosing whole pages at a 4-bit model. Request-driven (the
             zeno/eudoxus row of CONVENTIONS §2): no fetch(), no Kairos worker.
             Neither handler ever raises; every failure is an {"error": ...}
             dict with a stable identifier first, detail second (Midas pattern).
             Every request goes out through one opener that connects only to
             public internet addresses (no loopback, no LAN), checked on every
             redirect hop, with the checked IP the one dialled. Which URLs
             fetch_page may be CALLED with is Pythia's rule, not this module's
             (pythia._dispatch, URL provenance). fetch_page reads text only: a
             page whose Content-Type isn't text (a PDF, an image) is refused
             before its body is read; it came back as 4,000 characters of
             binary (measured 2026-11-23).

Source:      Brave Search API — one JSON GET over stdlib urllib (free tier,
             ~2k/mo). SearXNG on Obelisk is the documented sovereignty upgrade,
             out of scope here.

Key:         The Brave API key lives ONLY in the Cerberus Vault (the sole
             secrets authority) under the entry name 'brave_api_key'. Set it
             in its row of the CERBERUS tab, or with
             `python cerberus.py set brave_api_key` (hidden prompts).
             It is read at call time, never cached to disk, never in this file,
             the repo, an env var, or a gitignored key file. Because vault_get()
             needs an unlocked session, web search only works in a session where
             Cerberus was unlocked; otherwise search_web returns vault_locked —
             a clean degrade, not a crash.

Config:      The five tunables the handoff called CALLIMACHUS_* live here as
             module constants. This matches the aether/zeno/eudoxus convention:
             a request-driven tool carries its own tunables.

Not:         No fetch()/Kairos worker — request-driven, not polled.
             No panel — Pythia's chat is the only surface.
             No query history — deferred, not forgotten.
             Not a Pheme replacement — Pheme is push (feeds arrive), this is
             pull (questions asked).

Upstream:    pythia.py (registration + dispatch — the sole registry consumer).
             (A second, voice-side registry once existed in
             metis_toolbox/__init__.py; it was retired with voice input, so
             Pythia's typed chat is now the only way any tool is called.)
Downstream:  cerberus.py (vault_get for the Brave key); tools/hermes.py (the
             deadline, the dial wrapper and the bounded read, under this
             module's own opener and its address guard).

Requires:    stdlib only — html.parser, http.client, ipaddress, json, logging,
             os, socket, sys, typing, urllib. No pip deps (no requests, no
             beautifulsoup4). Plus cerberus (app-root sibling) for the key and
             tools/hermes.py.

2026-07-24:  walkthrough audit — every search_failed/fetch_failed error dict
             stopped interpolating the caught exception's message (raw text into
             the LLM context — §2); now only type(exc).__name__ travels. That
             covers the guard-flagged bare {exc} AND the two {exc.reason} the
             guard can't see (URLError.reason is a foreign socket/OS message).
             Contract verified never-raise; regression tests added; off the
             output-contract allowlist.

2026-11-16:  the Pythia tool audit (CONVENTIONS §12). fetch_page is the
             audit's reference for a model-facing cap (T8: truncated), and its
             description now allows a URL the user gave, as the gate has since
             2026-09-30; both descriptions follow §3's grammar. TOOL_META says
             web / live / changes nothing. Its failures use the shared
             unavailable, timeout and vault_locked (T9, 2026-11-16), and no_key
             when the vault is open without a Brave key, as Midas says it.

V0.03:       Walked through 2026-11-23 (the V0.03 walkthrough, S33; born
             2026-07-07). The address guard was tried against every special
             range: Python counts ::/96, the deprecated IPv4-compatible form
             (::127.0.0.1), as global, and the guard took it as public; it is
             refused now (Windows answered "unreachable network", so nothing
             was reached). fetch_page refuses a page that isn't text instead
             of handing the model 4,000 characters of a PDF's or an image's
             bytes; a result's title is capped like its snippet; search_web's
             catch-all, on the request that carries the Brave key, logs the
             exception's class only (BACKLOG). Contract and Requires caught up.
"""

import http.client
import ipaddress
import json
import logging
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

# Callimachus is a tool module that reaches UP to an app-root sibling
# (cerberus.py), not just a tools/ sibling. Normal operation (Pythia, tests,
# felhaven) already runs with the app root on sys.path; only a bare
# `python tools/callimachus.py` standalone run needs it added first. This block
# must precede `import cerberus` so it runs before the import resolves — the
# same top-of-module placement kepler.py uses for `from tools import hypatia`.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cerberus
from tools import hermes

log = logging.getLogger("METIS.callimachus")

# ── Config — module constants (the handoff's CALLIMACHUS_* knobs) ─────────────

_RESULT_COUNT       = 3            # search_web returns at most this many results
_PAGE_CHARS         = 4000        # fetch_page text budget before truncation
_TIMEOUT_S          = 10          # socket timeout for every request
_MAX_DOWNLOAD_BYTES = 1_048_576   # 1 MB hard cap on a fetched page
_SEARCH_MAX_BYTES = 64 * 1024     # Brave's answer; measured 2026-10-01: 10,334 B at count=3 (4x, power of two)
_SAFESEARCH         = "moderate"  # Brave 'safesearch' param passthrough

_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_USER_AGENT      = "Felhaven/1.0 (Callimachus web search)"   # Aether precedent
_SNIPPET_CHARS   = 300            # defensive cap on a title and a snippet (Brave's are short)
_MAX_REDIRECTS   = 5              # fetch_page follows at most this many hops

# The one vault entry name Callimachus reads. See the Key: section above.
_VAULT_KEY_NAME = "brave_api_key"


# ── HTML → visible text (stdlib html.parser, no pip deps) ─────────────────────
# Drop the non-visible subtrees, keep everything else, and insert a space at
# block boundaries so adjacent blocks don't mash into one word.

_SKIP_TAGS = {"script", "style", "head", "title", "noscript", "template"}
_BLOCK_TAGS = {
    "p", "br", "div", "li", "tr", "section", "article", "header", "footer",
    "ul", "ol", "table", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6",
}


class _TextExtractor(HTMLParser):
    """Collect visible text, skipping the _SKIP_TAGS subtrees. Nested skips are
    depth-counted so a <style> inside <head> still closes cleanly."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def _collapse_ws(s: str) -> str:
    """Collapse every run of whitespace to a single space and trim."""
    return " ".join(s.split())


def _strip_html(html: str) -> str:
    """Best-effort: return the visible text of an HTML string, tags dropped and
    whitespace collapsed. Never raises — a malformed document degrades to
    whatever text was parsed before the error."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:                     # malformed markup is not fatal
        log.debug("Callimachus: HTML parse stopped early: %s", exc)
    return _collapse_ws(parser.text())


# ── HTTP — one opener that enforces https-only redirects, capped hops ─────────

class _NonHttpsRedirect(Exception):
    """A redirect pointed at a non-https URL — rejected (handoff fetch_page)."""


class _NotText(Exception):
    """fetch_page's answer isn't text; `content_type` is what the server said."""

    def __init__(self, content_type: str) -> None:
        super().__init__(content_type)
        self.content_type = content_type


# What fetch_page will read: text/*, and the XML and JSON types a page may come
# as. A missing Content-Type is read as text, as a browser would sniff it.
_TEXT_TYPES = {"application/xhtml+xml", "application/xml", "application/json"}

# A Content-Type shaped like one, so the server's own words are bounded before
# they go into an error's detail.
_TYPE_SHAPE = re.compile(r"^[a-z0-9][a-z0-9.+-]{0,40}/[a-z0-9][a-z0-9.+-]{0,60}$")


def _is_text(content_type: str) -> bool:
    """Whether fetch_page may read a body of this Content-Type."""
    ctype = content_type.split(";", 1)[0].strip().lower()
    return (not ctype or ctype.startswith("text/") or ctype in _TEXT_TYPES
            or ctype.endswith(("+xml", "+json")))


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but refuse to leave https and cap the hop count."""

    max_redirections = _MAX_REDIRECTS

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: Any,
        headers: Any,
        newurl: str,
    ) -> "urllib.request.Request | None":
        if not newurl.lower().startswith("https://"):
            raise _NonHttpsRedirect(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# ── The address guard — public internet only (CONVENTIONS §12) ────────────────
# fetch_page takes a URL from the model, and the model reads text strangers
# wrote. Without this, "https://192.168.1.1/" (a router's admin page) or
# "https://localhost:8443/" would be fetched and its text handed back to the
# model: the home network, probed from the inside on a stranger's say-so.
#
# The check lives in the CONNECTION, not in a look at the URL beforehand, for
# two reasons. A redirect is a new connection, so every hop is checked, not
# just the first URL. And the address that was checked is the address that is
# dialled: the name is resolved once, here, and we connect to that IP, so a DNS
# answer that changes between a check and a connect (rebinding) has nothing to
# change. TLS still verifies the certificate against the NAME (server_hostname
# stays self.host), so pinning the IP costs no https guarantee.

# A few IPv6 ranges carry an IPv4 address inside them. The outer address can
# look public while the inner one is 127.0.0.1, so the inner one is checked too.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class _PrivateAddress(OSError):
    """The host resolved to an address that is not on the public internet.
    An OSError, so urllib wraps it in URLError like any failed connect."""


def _embedded_v4(ip: ipaddress.IPv6Address) -> "ipaddress.IPv4Address | None":
    """The IPv4 address an IPv6 address carries inside it, if it carries one."""
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def _is_public(address: str) -> bool:
    """Is this resolved address on the public internet?

    `is_global` does the work: it is False for loopback, the private ranges,
    link-local, carrier-grade NAT (100.64/10), "this network" (0/8), the IPv6
    unique-local and link-local ranges, and the reserved blocks. Multicast is
    ruled out separately, because Python counts some of it as global. Anything
    that doesn't parse is not public."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])   # drop an IPv6 zone
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if int(ip) >> 32 == 0:
            # ::/96, the deprecated IPv4-compatible form (::127.0.0.1), with
            # :: and ::1 inside it. Python counts it global, but it was never
            # on the public internet (RFC 4291 §2.5.5.1); measured 2026-11-23,
            # Windows answers "unreachable network" for ::7f00:1.
            return False
        inner = _embedded_v4(ip)
        if inner is not None and not _is_public(str(inner)):
            return False
    return ip.is_global and not ip.is_multicast


def _connect_public(address: "tuple[str, int]", timeout: "float | None",
                    source_address: "tuple[str, int] | None" = None) -> socket.socket:
    """socket.create_connection, but only to the public internet.

    Resolves the host ONCE and refuses if ANY answer is not public. One bad
    answer is enough: a name that returns a public and a private address is a
    name built to be tried twice. Then it dials the checked addresses
    themselves, in the order the resolver gave them."""
    host, port = address
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ips = [str(info[4][0]) for info in infos]
    if not ips or not all(_is_public(ip) for ip in ips):
        log.warning("Callimachus: refused %s — resolves to a non-public address",
                    host)
        raise _PrivateAddress(f"{host} is not a public internet address")
    for ip in ips[:-1]:
        try:
            return socket.create_connection((ip, port), timeout, source_address)
        except OSError:
            continue                             # the next checked address
    return socket.create_connection((ips[-1], port), timeout, source_address)


class _GuardedHTTPSConnection(http.client.HTTPSConnection):
    """An HTTPSConnection whose TCP connect goes through _connect_public.

    http.client dials through its _create_connection hook, and everything
    else — TLS, the certificate check against the name, TCP_NODELAY — stays
    stdlib's own. Swapping one hook is less code to get wrong than a
    hand-written connect().

    hermes adds the deadline without touching the guard: bounded_dial wraps
    _connect_public (the same addresses, checked the same way) so the socket
    it returns re-arms its timeout from the deadline, and hermes.TLS_CONTEXT
    does the same for the TLS socket. The context is set here because a
    handler's context= never reaches a connection class passed to do_open."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["context"] = hermes.TLS_CONTEXT
        super().__init__(*args, **kwargs)
        self._create_connection = hermes.bounded_dial(_connect_public)


class _GuardedHTTPSHandler(urllib.request.HTTPSHandler):
    """Route every https request, redirects included, through the guard."""

    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(_GuardedHTTPSConnection, req)


# ProxyHandler({}) turns off proxies from the environment (HTTPS_PROXY). With a
# proxy, the guard would check the PROXY's address and the proxy would dial the
# real target unchecked. search_web shares this opener: Brave's host is public,
# so the guard changes nothing there (CONVENTIONS §12, 2026-09-30).
def _build_opener() -> urllib.request.OpenerDirector:
    """The one opener both tools use. A function so a test can build it with a
    proxy in the environment and see that none is honoured."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _GuardedHTTPSHandler(), _HttpsOnlyRedirect())


_OPENER = _build_opener()


def _fetch_bytes(req: urllib.request.Request, cap: int,
                 text_only: bool = False) -> "tuple[bytes, str]":
    """Open req through the guarded opener and return (body, final_url).

    The whole request, connect to last byte, runs inside a hermes deadline
    (hermes.deadline_for: 20 s at these caps), and the body is read by
    hermes.read_bounded: refused by Content-Length, then by reading one byte
    past the cap. With text_only, a Content-Type that isn't text raises
    _NotText before the body is read. Raises hermes.TooLarge /
    _NonHttpsRedirect / _NotText / socket.timeout (hermes.DeadlinePassed is
    one) / urllib errors for the caller to translate."""
    with hermes.within(hermes.deadline_for(cap, _TIMEOUT_S), _TIMEOUT_S):
        resp: Any = _OPENER.open(req, timeout=_TIMEOUT_S)
        try:
            if text_only:
                content_type = str(resp.headers.get("Content-Type") or "")
                if not _is_text(content_type):
                    raise _NotText(content_type)
            return hermes.read_bounded(resp, cap), str(resp.geturl())
        finally:
            resp.close()


# ── Brave key (Cerberus Vault only) ───────────────────────────────────────────

def _brave_key() -> str:
    """Return the Brave API key from the Cerberus Vault. Requires an unlocked
    session; raises cerberus.VaultError if the vault is locked or the key is
    absent. Never cached to disk (handoff L7)."""
    return cerberus.vault_get(_VAULT_KEY_NAME)


# ── search_web ────────────────────────────────────────────────────────────────

def _shape_results(data: dict[str, Any]) -> list[dict[str, str]]:
    """Pull up to _RESULT_COUNT {title, snippet, url} out of a Brave response.
    Titles/snippets are HTML-stripped (Brave marks matches with <strong>) and
    both are hard-capped defensively: Pythia sends a result to the model whole."""
    web = data.get("web")
    items = web.get("results", []) if isinstance(web, dict) else []
    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not url:
            continue
        out.append({
            "title":   _strip_html(str(item.get("title", "")))[:_SNIPPET_CHARS],
            "snippet": _strip_html(str(item.get("description", "")))[:_SNIPPET_CHARS],
            "url":     str(url),
        })
        if len(out) >= _RESULT_COUNT:
            break
    return out


def search_web(query: str) -> dict[str, Any]:
    """Search the web via Brave and return at most _RESULT_COUNT shaped results.
    Never raises.

    Success:  {"results": [{"title": str, "snippet": str, "url": str}, ...]}
    Errors:   {"error": "no_results" | "rate_limited" | "timeout"
                          | "vault_locked" | "no_key"
                          | "unavailable: <detail>"}
    """
    q = (query or "").strip()
    if not q:
        return {"error": "no_results"}

    try:
        key = _brave_key()
    except cerberus.VaultError as exc:
        log.warning("Callimachus: Brave key unavailable (vault): %s", exc)
        # Cerberus's own reason picks the word: sealed, or open with no
        # Brave key (Midas's words); anything else is the vault failing.
        if exc.slug == cerberus.ERR_LOCKED:
            return {"error": "vault_locked"}
        if exc.slug == cerberus.ERR_NO_ENTRY:
            return {"error": "no_key"}
        return {"error": f"unavailable: {type(exc).__name__}"}

    params = urllib.parse.urlencode({
        "q":          q,
        "count":      _RESULT_COUNT,
        "safesearch": _SAFESEARCH,
    })
    req = urllib.request.Request(
        f"{_SEARCH_ENDPOINT}?{params}",
        headers={
            "X-Subscription-Token": key,
            "Accept":               "application/json",
            "Accept-Encoding":      "identity",
            "User-Agent":           _USER_AGENT,
        },
    )

    try:
        raw, _ = _fetch_bytes(req, cap=_SEARCH_MAX_BYTES)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            return {"error": "rate_limited"}
        return {"error": f"unavailable: HTTP {exc.code}"}
    except socket.timeout:
        return {"error": "timeout"}
    except hermes.TooLarge:
        return {"error": "unavailable: response over the size cap"}
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, socket.timeout):
            return {"error": "timeout"}
        # exc.reason is a FOREIGN message (a socket/OS error) — it can carry a
        # hostname or errno detail, so only its class travels (§2), not its text.
        return {"error": f"unavailable: {type(exc.reason).__name__}"}
    except Exception as exc:
        # The class only: this request carries the Brave key, and a keyed
        # request's text is never logged unchecked (§12, 2026-11-18).
        log.warning("Callimachus.search_web: unexpected failure: %s",
                    type(exc).__name__)
        return {"error": f"unavailable: {type(exc).__name__}"}

    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except (json.JSONDecodeError, ValueError) as exc:
        return {"error": f"unavailable: {type(exc).__name__}"}
    if not isinstance(data, dict):
        return {"error": "unavailable: response was not a JSON object"}

    results = _shape_results(data)
    if not results:
        return {"error": "no_results"}
    return {"results": results}


# ── fetch_page ────────────────────────────────────────────────────────────────

def fetch_page(url: str) -> dict[str, Any]:
    """Fetch one https page and return its visible text, truncated to
    _PAGE_CHARS. Never raises.

    Success:  {"url": str, "text": str, "truncated": bool}
    Errors:   {"error": "non_https_url" | "private_address" | "timeout"
                          | "too_large" | "unavailable: <detail>"}

    "unavailable: not a text page (application/pdf)": the server said the
    page isn't text, so its body is never read. The type is named only when
    it is shaped like one.

    private_address: the host, or a host it redirected to, is this computer
    or the local network. Refused at connect time by _GuardedHTTPSConnection,
    even for a URL the user typed — a browser is the tool for a router page.
    """
    u = (url or "").strip()
    # https-only, rejected before any network I/O (and a redirect off https is
    # rejected mid-flight by _HttpsOnlyRedirect).
    if not u.lower().startswith("https://"):
        return {"error": "non_https_url"}

    req = urllib.request.Request(u, headers={"User-Agent": _USER_AGENT,
                                             "Accept-Encoding": "identity"})

    try:
        raw, final_url = _fetch_bytes(req, cap=_MAX_DOWNLOAD_BYTES, text_only=True)
    except _NonHttpsRedirect:
        return {"error": "non_https_url"}
    except _NotText as exc:
        ctype = exc.content_type.split(";", 1)[0].strip().lower()
        if _TYPE_SHAPE.match(ctype):
            return {"error": f"unavailable: not a text page ({ctype})"}
        return {"error": "unavailable: not a text page"}
    except hermes.TooLarge:
        return {"error": "too_large"}
    except _PrivateAddress:                      # urllib wraps it; belt and braces
        return {"error": "private_address"}
    except urllib.error.HTTPError as exc:
        return {"error": f"unavailable: HTTP {exc.code}"}
    except socket.timeout:
        return {"error": "timeout"}
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, socket.timeout):
            return {"error": "timeout"}
        if isinstance(exc.reason, _PrivateAddress):
            return {"error": "private_address"}
        # Foreign message (socket/OS error) — class only, never its text (§2).
        return {"error": f"unavailable: {type(exc.reason).__name__}"}
    except Exception as exc:
        log.warning("Callimachus.fetch_page: unexpected failure: %s", exc)
        return {"error": f"unavailable: {type(exc).__name__}"}

    text = _strip_html(raw.decode("utf-8", "replace"))
    truncated = len(text) > _PAGE_CHARS
    if truncated:
        text = text[:_PAGE_CHARS]
    return {"url": final_url, "text": text, "truncated": truncated}


# ── Public API — the first plural export (CONVENTIONS §2/§3) ──────────────────

SEARCH_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": (
            "Searches the live web for current, recent, or unknown facts — "
            "things you don't already know or that change over time — and "
            "returns up to 3 results as {title, snippet, url}. Call whenever a "
            "question needs up-to-date or external information rather than "
            "guessing. Read the snippets, then call fetch_page on the ONE most "
            "promising url only if you need the full text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
            },
            "required": ["query"],
        },
    },
}

FETCH_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "fetch_page",
        "description": (
            "Fetches one web page and returns its visible text (scripts and "
            "styles stripped, truncated to about 4000 characters). Call only "
            "on an https url the user gave you, or on the single most promising "
            "url a search_web result returned — never invent a url. If the "
            "returned 'truncated' is true, the page continues past the text you "
            "were given."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": ("The https:// URL to fetch (one the user "
                                    "gave, or from a search result)."),
                },
            },
            "required": ["url"],
        },
    },
}

# Plural export: a multi-tool module contributes a LIST of definitions. The
# registry (pythia.py) splats these in and maps each tool name to the like-named
# module function (search_web / fetch_page). See CONVENTIONS §2/§3.
TOOL_DEFINITIONS = [SEARCH_TOOL_DEFINITION, FETCH_TOOL_DEFINITION]

# What the code reads about each tool here; the model never sees it
# (CONVENTIONS §8, "TOOL_META"). network: none = local only, cached = a poll
# or TTL cache, live = may reach the network on every call. mutates = it
# changes state outside the turn.
TOOL_META: dict[str, dict[str, Any]] = {
    "search_web": {"domain": "web", "network": "live", "mutates": False},
    "fetch_page": {"domain": "web", "network": "live", "mutates": False},
}


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import io

    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    logging.basicConfig(level=logging.INFO)

    # The HTML stripper runs without network or a key — always demonstrable.
    sample = (
        "<html><head><title>Ignore me</title><style>.x{color:red}</style></head>"
        "<body><h1>Hello</h1><p>First para.</p><script>evil()</script>"
        "<p>Second&nbsp;para.</p></body></html>"
    )
    print("[Callimachus] strip_html demo:")
    print("  ", _strip_html(sample))

    # Live search only works if Cerberus is unlocked and holds the Brave key.
    # Unlock here from an env PIN purely for a manual smoke test; normal callers
    # never do this — Pythia relies on an already-unlocked session.
    pin = os.environ.get("CERBERUS_PIN")
    if pin and cerberus.unlock(pin):
        print("\n[Callimachus] live search: 'who is callimachus of cyrene'")
        r = search_web("who is callimachus of cyrene")
        if "error" in r:
            print("  ERROR:", r["error"])
        else:
            for hit in r["results"]:
                print(f"  - {hit['title']}\n    {hit['url']}\n    {hit['snippet'][:120]}")
    else:
        print("\n[Callimachus] set CERBERUS_PIN (and seed 'brave_api_key' in the "
              "vault) to smoke-test a live search.")
