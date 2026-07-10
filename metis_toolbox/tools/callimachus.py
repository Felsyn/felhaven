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
             instead of the singular TOOL_DEFINITION, and two handlers:
               • search_web(query) -> {"results": [{title, snippet, url}], ...}
               • fetch_page(url)   -> {"url", "text", "truncated"}
             The split lets a small model run the agentic loop — search, read
             snippets, CHOOSE one url, fetch it — inside one Pythia turn instead
             of firehosing whole pages at a 4-bit model. Request-driven (the
             zeno/eudoxus row of CONVENTIONS §2): no fetch(), no Kairos worker.
             Neither handler ever raises; every failure is an {"error": ...}
             dict with a stable identifier first, detail second (Midas pattern).

Source:      Brave Search API — one JSON GET over stdlib urllib (free tier,
             ~2k/mo). SearXNG on Obelisk is the documented sovereignty upgrade,
             out of scope here.

Key:         The Brave API key lives ONLY in the Cerberus Vault (the sole
             secrets authority) under the entry name 'brave_api_key'. Seed it
             once with:  python cerberus.py set <PIN> brave_api_key <key>
             It is read at call time, never cached to disk, never in this file,
             the repo, an env var, or a gitignored key file. Because vault_get()
             needs an unlocked session, web search only works in a session where
             Cerberus was unlocked; otherwise search_web returns a search_failed
             error — a clean degrade, not a crash.

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
Downstream:  cerberus.py (vault_get for the Brave key).

Requires:    stdlib only — json, logging, socket, urllib, html.parser. No pip
             deps (no requests, no beautifulsoup4). Plus cerberus (app-root
             sibling) for the key.
"""

import json
import logging
import os
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

log = logging.getLogger("METIS.callimachus")

# ── Config — module constants (the handoff's CALLIMACHUS_* knobs) ─────────────

_RESULT_COUNT       = 3            # search_web returns at most this many results
_PAGE_CHARS         = 4000        # fetch_page text budget before truncation
_TIMEOUT_S          = 10          # socket timeout for every request
_MAX_DOWNLOAD_BYTES = 1_048_576   # 1 MB hard cap on a fetched page
_SAFESEARCH         = "moderate"  # Brave 'safesearch' param passthrough

_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_USER_AGENT      = "Felhaven/1.0 (Callimachus web search)"   # Aether precedent
_SNIPPET_CHARS   = 300            # defensive snippet cap (Brave's are short)
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


class _TooLarge(Exception):
    """A response exceeded _MAX_DOWNLOAD_BYTES (Content-Length or while reading)."""


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


_OPENER = urllib.request.build_opener(_HttpsOnlyRedirect())


def _fetch_bytes(req: urllib.request.Request, cap: "int | None") -> "tuple[bytes, str]":
    """Open req through the https-only opener and return (body, final_url).

    When `cap` is set, refuse an oversize body — first by Content-Length if the
    server declares one, then by reading one byte past the cap and rejecting if
    it arrives (a lying/absent Content-Length can't sneak past). Raises
    _TooLarge / _NonHttpsRedirect / socket.timeout / urllib errors for the
    caller to translate."""
    resp: Any = _OPENER.open(req, timeout=_TIMEOUT_S)
    try:
        final_url = str(resp.geturl())
        if cap is not None:
            declared = resp.headers.get("Content-Length")
            if declared and str(declared).isdigit() and int(declared) > cap:
                raise _TooLarge()
            data = resp.read(cap + 1)
            if len(data) > cap:
                raise _TooLarge()
        else:
            data = resp.read()
        return bytes(data), final_url
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
    the snippet is hard-capped defensively."""
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
            "title":   _strip_html(str(item.get("title", ""))),
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
                          | "search_failed: <detail>"}
    """
    q = (query or "").strip()
    if not q:
        return {"error": "no_results"}

    try:
        key = _brave_key()
    except cerberus.VaultError as exc:
        log.warning("Callimachus: Brave key unavailable (vault): %s", exc)
        return {"error": f"search_failed: {exc}"}

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
            "User-Agent":           _USER_AGENT,
        },
    )

    try:
        raw, _ = _fetch_bytes(req, cap=None)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            return {"error": "rate_limited"}
        return {"error": f"search_failed: HTTP {exc.code}"}
    except socket.timeout:
        return {"error": "timeout"}
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, socket.timeout):
            return {"error": "timeout"}
        return {"error": f"search_failed: {exc.reason}"}
    except Exception as exc:
        log.warning("Callimachus.search_web: unexpected failure: %s", exc)
        return {"error": f"search_failed: {exc}"}

    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except (json.JSONDecodeError, ValueError) as exc:
        return {"error": f"search_failed: {exc}"}
    if not isinstance(data, dict):
        return {"error": "search_failed: response was not a JSON object"}

    results = _shape_results(data)
    if not results:
        return {"error": "no_results"}
    return {"results": results}


# ── fetch_page ────────────────────────────────────────────────────────────────

def fetch_page(url: str) -> dict[str, Any]:
    """Fetch one https page and return its visible text, truncated to
    _PAGE_CHARS. Never raises.

    Success:  {"url": str, "text": str, "truncated": bool}
    Errors:   {"error": "non_https_url" | "timeout" | "too_large"
                          | "fetch_failed: <detail>"}
    """
    u = (url or "").strip()
    # https-only, rejected before any network I/O (and a redirect off https is
    # rejected mid-flight by _HttpsOnlyRedirect).
    if not u.lower().startswith("https://"):
        return {"error": "non_https_url"}

    req = urllib.request.Request(u, headers={"User-Agent": _USER_AGENT})

    try:
        raw, final_url = _fetch_bytes(req, cap=_MAX_DOWNLOAD_BYTES)
    except _NonHttpsRedirect:
        return {"error": "non_https_url"}
    except _TooLarge:
        return {"error": "too_large"}
    except urllib.error.HTTPError as exc:
        return {"error": f"fetch_failed: HTTP {exc.code}"}
    except socket.timeout:
        return {"error": "timeout"}
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, socket.timeout):
            return {"error": "timeout"}
        return {"error": f"fetch_failed: {exc.reason}"}
    except Exception as exc:
        log.warning("Callimachus.fetch_page: unexpected failure: %s", exc)
        return {"error": f"fetch_failed: {exc}"}

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
            "Search the live web for current, recent, or unknown facts — things "
            "you don't already know or that change over time. Returns up to 3 "
            "results as {title, snippet, url}. Read the snippets, then call "
            "fetch_page on the ONE most promising url only if you need the full "
            "text. Call this whenever a question needs up-to-date or external "
            "information rather than guessing."
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
            "Fetch one web page and return its visible text (scripts and styles "
            "stripped, truncated to about 4000 characters). Call this only AFTER "
            "search_web, on a single promising https url from those results — "
            "never invent a url. If the returned 'truncated' is true, the page "
            "continues past the text you were given."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The https:// URL to fetch (from a search result).",
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
