"""
pheme.py — RSS / Atom News Aggregator
=====================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Aggregate headlines from the configured RSS/Atom news feeds.

Feeds live in pheme_rumormill.json at the repo root — one row per source
({id, label, url, format}). Adding or removing a feed is a JSON edit, never a
code change. The parser branches on `format` ("rss" 2.0 vs "atom").

Contract:    Exposes TOOL_DEFINITION, handle(), and fetch().

             fetch()  (Kairos): fans every feed out concurrently and returns
                 {"feeds": {feed_id: {"stories": [...]} | {"error": str}}}.
                 Every configured id is present. Raises ONLY on total failure
                 (config unreadable) so Kairos delivers None to the panel.

             _fetch_feed(): never raises — one dead feed yields {"error": ...}
                 for its id only and cannot poison its siblings.

             handle(feed=None)  (Brain): never raises; returns a flat
                 {"stories": [...]} capped at _MAX_STORIES. The optional `feed`
                 arg narrows results to a single source.

             Story shape:
                 {"title": str, "url": str, "author": str,
                  "date": str,  "domain": str}     # missing fields are ""

Requires:    requests (already in Felhaven stack)
             concurrent.futures, json, html, datetime, xml.etree (stdlib)
"""

import html
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from itertools import zip_longest
from urllib.parse import urlparse
from typing import Any
from xml.etree import ElementTree as ET

import requests

log = logging.getLogger("METIS.pheme")

# ── Config ────────────────────────────────────────────────────────────────────

# pheme_rumormill.json sits at the repo root — one directory up from tools/.
# abspath() makes resolution independent of the current working directory.
_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "pheme_rumormill.json",
)

# Browser-plausible RSS-reader User-Agent so picky edges (e.g. TownNews) don't 403.
_USER_AGENT   = "Mozilla/5.0 (compatible; Felhaven/1.0 RSS reader)"
_HTTP_TIMEOUT = 6      # per-feed network timeout, seconds
_MAX_STORIES  = 10     # cap per feed (panel) and on the flattened Brain list

# XML namespaces. Atom puts every element in its own namespace; RSS borrows
# Dublin Core for the author byline.
_ATOM_NS = "http://www.w3.org/2005/Atom"
_DC_NS   = "http://purl.org/dc/elements/1.1/"

# Date strings we know how to shorten to a "Jun 7" style label.
_DATE_FORMATS = (
    "%a, %d %b %Y %H:%M:%S %z",   # RFC-822 with numeric tz   (RSS)
    "%a, %d %b %Y %H:%M:%S",      # RFC-822, no tz
    "%Y-%m-%dT%H:%M:%S%z",        # ISO-8601 with tz          (Atom)
    "%Y-%m-%dT%H:%M:%S.%f%z",     # ISO-8601 with fractional seconds
    "%Y-%m-%dT%H:%M:%S",          # ISO-8601, no tz
)


# ── Config loading ──────────────────────────────────────────────────────────────

def _load_config() -> list[dict[str, Any]]:
    """
    Read the ordered feed list from pheme_rumormill.json.
    Raises (FileNotFoundError / JSONDecodeError / KeyError) if the config is
    unreadable — fetch() lets this propagate so Kairos delivers None.
    """
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        feeds: list[dict[str, Any]] = json.load(f)["feeds"]
        return feeds


# ── Field normalizers ────────────────────────────────────────────────────────────

def _domain(url: str) -> str:
    """Bare hostname, leading www. stripped. '' if unparseable."""
    try:
        netloc = urlparse(url).netloc
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return ""


def _clean_author(raw: str) -> str:
    """Strip a leading 'By ' byline prefix. '' for empty/missing."""
    if not raw:
        return ""
    text = raw.strip()
    if text.lower().startswith("by "):
        text = text[3:]
    return text.strip()


def _short_date(raw: str) -> str:
    """
    Reduce an RSS/Atom date to a short 'Jun 7' label.
    Returns '' if the string can't be parsed — date is optional in the meta line,
    so an unparseable date just drops out gracefully.
    """
    if not raw:
        return ""
    # Named UTC zones (BBC uses 'GMT') aren't understood by %z — normalize them.
    text = raw.strip().replace(" GMT", " +0000").replace(" UTC", " +0000")
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
            # str(dt.day) drops the leading zero without %-d/%#d (portable).
            return f"{dt.strftime('%b')} {dt.day}"
        except ValueError:
            continue
    return ""


def _story(title: str, url: str, author: str, date: str) -> dict[str, str]:
    """Assemble the normalized story dict shared by every feed format."""
    return {
        "title":  html.unescape(title).strip() or "(no title)",
        "url":    url.strip(),
        "author": _clean_author(html.unescape(author)),
        "date":   date,
        "domain": _domain(url),
    }


# ── Element helpers ──────────────────────────────────────────────────────────────

def _text(el: ET.Element, tag: str) -> str:
    """Text of a plain (non-namespaced) child element, or ''."""
    child = el.find(tag)
    return child.text.strip() if (child is not None and child.text) else ""


def _text_ns(el: ET.Element, ns: str, tag: str) -> str:
    """Text of a namespaced child element, or ''."""
    child = el.find(f"{{{ns}}}{tag}")
    return child.text.strip() if (child is not None and child.text) else ""


# ── Format-specific parsers ──────────────────────────────────────────────────────

def _parse_rss(root: ET.Element) -> list[dict[str, str]]:
    """Parse RSS 2.0 <item> elements into stories."""
    stories = []
    for item in root.findall(".//item"):
        title  = _text(item, "title")
        url    = _text(item, "link")
        # Author byline lives in Dublin Core; fall back to plain <author>.
        author = _text_ns(item, _DC_NS, "creator") or _text(item, "author")
        date   = _short_date(_text(item, "pubDate"))
        stories.append(_story(title, url, author, date))
    return stories


def _atom_link(entry: ET.Element) -> str:
    """
    Atom links carry the URL in the href attribute, not the element text.
    Prefer rel='alternate' (the canonical article link); fall back to the first
    link that has an href.
    """
    links = entry.findall(f"{{{_ATOM_NS}}}link")
    for link in links:
        href = link.get("href")
        if link.get("rel", "alternate") == "alternate" and href:
            return href
    for link in links:
        href = link.get("href")
        if href:
            return href
    return ""


def _atom_author(entry: ET.Element) -> str:
    """Atom author is <author><name>…</name></author>."""
    author = entry.find(f"{{{_ATOM_NS}}}author")
    if author is not None:
        return _text_ns(author, _ATOM_NS, "name")
    return ""


def _parse_atom(root: ET.Element) -> list[dict[str, str]]:
    """Parse Atom <entry> elements into stories."""
    stories = []
    for entry in root.findall(f".//{{{_ATOM_NS}}}entry"):
        title  = _text_ns(entry, _ATOM_NS, "title")
        url    = _atom_link(entry)
        author = _atom_author(entry)
        # Prefer <published>; fall back to <updated>.
        date   = _short_date(
            _text_ns(entry, _ATOM_NS, "published")
            or _text_ns(entry, _ATOM_NS, "updated")
        )
        stories.append(_story(title, url, author, date))
    return stories


# ── Per-feed fetch ───────────────────────────────────────────────────────────────

def _fetch_feed(feed: dict[str, Any]) -> dict[str, Any]:
    """
    Fetch and parse one feed. NEVER raises: a failure becomes {"error": str}
    scoped to this feed alone, so a single dead source can't poison siblings.
    """
    try:
        resp = requests.get(
            feed["url"],
            headers={"User-Agent": _USER_AGENT},
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        if feed.get("format") == "atom":
            stories = _parse_atom(root)
        else:
            stories = _parse_rss(root)

        return {"stories": stories[:_MAX_STORIES]}

    except requests.HTTPError as e:
        # Surface the status code so the panel can show "feed unavailable (HTTP 404)".
        status = e.response.status_code if e.response is not None else "?"
        log.warning(f"Pheme: feed {feed.get('id')} HTTP {status}")
        return {"error": f"HTTP {status}"}
    except Exception as e:
        log.warning(f"Pheme: feed {feed.get('id')} failed: {e}")
        return {"error": str(e)}


def _fetch_all(feeds: list[dict[str, Any]]) -> dict[str, Any]:
    """Fetch every feed concurrently. Returns {id: {"stories":…}|{"error":…}}."""
    results: dict[str, Any] = {}
    if not feeds:
        return results
    with ThreadPoolExecutor(max_workers=min(len(feeds), 8)) as pool:
        future_to_id = {pool.submit(_fetch_feed, f): f["id"] for f in feeds}
        for future in as_completed(future_to_id):
            results[future_to_id[future]] = future.result()  # never raises
    return results


def _interleave(per_feed: list[list[dict[str, str]]]) -> list[dict[str, str]]:
    """
    Round-robin merge so the flat Brain list shows a cross-section: one story
    from each feed, then the next from each, dropping exhausted feeds.
    """
    merged: list[dict[str, str]] = []
    for tier in zip_longest(*per_feed):
        merged.extend(s for s in tier if s is not None)
    return merged


# ── Contract ──────────────────────────────────────────────────────────────────

def _feed_ids() -> list[str]:
    """Feed ids from config, for the tool's `feed` enum. [] if config missing."""
    try:
        return [f["id"] for f in _load_config()]
    except Exception:
        return []


# Built from the live config so the enum can't drift from the actual feeds.
_FEED_PARAM: dict[str, Any] = {
    "type": "string",
    "description": (
        "Optional. Narrow results to a single source by its feed id "
        "(e.g. 'hackernews', 'bbcworld'). Omit for a mix across all sources."
    ),
}
_IDS = _feed_ids()
if _IDS:
    _FEED_PARAM["enum"] = _IDS

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "get_news_stories",
        "description": (
            "Returns current news headlines aggregated from several RSS/Atom "
            "feeds (tech, world, and local news), each with title, URL, author, "
            "date, and source domain. Call this when the user asks about the "
            "news, tech news, world news, what's trending, or local headlines. "
            "Pass `feed` to limit results to one source."
        ),
        "parameters": {
            "type": "object",
            "properties": {"feed": _FEED_PARAM},
            "required": [],
        },
    },
}


def handle(feed: str | None = None) -> dict[str, Any]:
    """
    Brain entry point — never raises. Returns a flat {"stories": [...]} capped at
    _MAX_STORIES. `feed` narrows to a single source; omit for a mix of all.
    """
    try:
        feeds = _load_config()
        if feed:
            feeds = [f for f in feeds if f["id"] == feed]
        results = _fetch_all(feeds)
        # Pull per-feed lists in config order; skip feeds that errored.
        per_feed = [results[f["id"]].get("stories", []) for f in feeds]
        return {"stories": _interleave(per_feed)[:_MAX_STORIES]}
    except Exception as e:
        log.error(f"Pheme: handle() failed: {e}")
        return {"stories": []}


def fetch() -> dict[str, Any]:
    """
    Kairos entry point — fans every feed out concurrently and tags each result
    by feed id. Raises only if the config itself is unreadable, so Kairos
    delivers None to the panel; a single dead feed yields {"error":…} instead.
    """
    feeds = _load_config()
    return {"feeds": _fetch_all(feeds)}


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Per-feed view (mirrors what the panel receives) — shows which feeds parse.
    print("=== per-feed (fetch) ===")
    try:
        for fid, res in fetch()["feeds"].items():
            if "error" in res:
                print(f"  {fid:12} ERROR: {res['error']}")
            else:
                print(f"  {fid:12} {len(res['stories'])} stories")
    except Exception as e:
        print(f"  fetch() failed: {e}")

    # Flat view (mirrors what the Brain receives).
    print("\n=== flat (handle) ===")
    for s in handle().get("stories", []):
        meta = " · ".join(p for p in (s["author"], s["date"], s["domain"]) if p)
        print(f"  {s['title']}")
        print(f"      {meta}")
