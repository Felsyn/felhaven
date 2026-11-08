"""
pheme.py — RSS / Atom News Aggregator
=====================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Aggregate headlines from the configured RSS/Atom news feeds.

Feeds live in config/pheme_rumormill.json under the app root — one row per source
({id, label, url, format}). Adding or removing a feed is a JSON edit, never a
code change. The parser branches on `format` ("rss" 2.0 vs "atom").

Contract:    Exposes TOOL_DEFINITION, handle(), and fetch().

             fetch()  (Kairos): fans every feed out concurrently and returns
                 {"feeds": {feed_id: {"stories": [...]} | {"error": str}}}.
                 Every configured id is present. Raises ONLY on total failure
                 (config unreadable) so Kairos delivers None to the panel.

             _fetch_feed(): never raises — one dead feed yields {"error": ...}
                 for its id only and cannot poison its siblings. Per-feed
                 identifiers (§2 slugs):
                   http_error: <status>       server answered with an error
                   feed_timeout               no answer within its deadline
                   feed_too_large             bigger than _MAX_BYTES
                   network_error              connection-level failure
                   invalid_feed_xml           response was not parseable XML
                   fetch_failed: <ClassName>  anything else — class name only,
                                              never the message (§2)

             handle(feed=None)  (Brain): never raises; returns a flat
                 {"stories": [...]} capped at _MAX_STORIES. The optional `feed`
                 arg narrows results to a single source. Its own failures are
                 §2 errors, so the model can tell "no news" from "broken":
                   config_unreadable          pheme_rumormill.json missing/bad
                   unknown_feed: <id>         `feed` matches no configured id
                   all_feeds_failed           every source errored this call
                   news_failed: <ClassName>   the never-raises safety net
                 An empty `stories` list therefore always means the feeds
                 answered and were empty — never a masked failure.

                 It answers from the last good fan-out while that is younger
                 than CACHE_TTL (the Kairos poll interval), dated with `as_of`,
                 plus `stale: True` when a failed refresh fell back to it. See
                 the "Freshness" section. fetch() always fans out, and its
                 shape is unchanged.

             Story shape:
                 {"title": str, "url": str, "author": str, "date": str,
                  "domain": str, "ts": str}        # missing fields are ""

                 `date` is the display label ("Jul 24") in the feed's own zone.
                 `ts` is the sort key: "YYYY-MM-DDTHH:MM:SSZ", always UTC, ""
                 when the feed's date is missing or unparseable. Fixed width, so
                 a plain string sort orders it; "" sorts last under reverse=True,
                 which is where an undated story belongs. handle() STRIPS `ts`
                 before returning — it is the panel's merge key, and the Brain's
                 story shape is deliberately unchanged.

             Read state (panel-facing; no LLM surface):
                 read_key(url) -> str     stable per-story identity
                 read_keys()   -> set     snapshot of the seen-set
                 mark_read(url)           record a click-through, then persist

                 Persisted to pheme_read.json beside argus_timeline.json —
                 runtime state, gitignored (§9), atomic writes, written on
                 change only. Capped at _READ_MAX (2000), evicted oldest first;
                 evicting a story still on screen re-brightens it, accepted at
                 this cap. A missing or corrupt file means everything reads as
                 unread — it never raises.

Scope:       Feed data and read state. Read state sits here rather than in a
             module of its own because it is an attribute OF a headline —
             meaningful only next to one, with exactly one consumer. Splitting
             it out would buy a deity whose whole job is a shared-state coupling
             back to this file (§12, 2026-08-02).

             NOT in scope: merge order, per-source caps, collapse state and
             dimming — presentation, and the panel's.

             Known limitation: `domain` comes from the item's own link, so for
             an aggregator it names the DESTINATION, not the source — a Hacker
             News story reads as "riscosopen.org". Left as-is deliberately; the
             source-name token that would fix it was declined (§12). SOURCE
             FONTS names the source in its section header.

Source:      The feeds configured in config/pheme_rumormill.json (7 as shipped:
             tech, world, and local news), each read through tools/hermes.py:
             https only, a byte cap, and a deadline for the whole request.

Upstream:    kairos.py — the "pheme" worker polls fetch() every 900 s.
             pythia.py — exposes handle() to the LLM via TOOL_DEFINITION.

Downstream:  panels/pheme_panel.py (display surface; maps the slugs above to
             human text in its _ERROR_TEXT dict)

Requires:    tools/hermes.py (the bounded network read)
             concurrent.futures, json, html, hashlib, tempfile, collections,
             datetime, urllib.parse, xml.etree (stdlib)

V0.02:       Audited 2026-07-23 — error identifiers normalized to §2 slugs
             (one literal per except arm; no more raw str(e)), handle() failures
             made distinguishable from an empty news day, docstring gained
             Source/Upstream/Downstream, first tests added.
             Extended 2026-08-02 for Scrapstream — the parsed datetime is kept
             as `ts` instead of being discarded at _short_date, and read state
             moved in. handle()'s output shape is unchanged.
             Extended 2026-09-28 (V0.03 audit T7): handle() answers from the
             last good fan-out, dated with `as_of`, instead of fetching again
             on every question. The story shape is unchanged.
"""

import copy
import hashlib
import html
import json
import logging
import os
import sys
import tempfile
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import zip_longest
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from typing import Any
from xml.etree import ElementTree as ET

# Only a bare `python tools/pheme.py` needs the app root added before the
# tools/ import (the aura placement); everything else already has it.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import hermes

log = logging.getLogger("METIS.pheme")

# ── Config ────────────────────────────────────────────────────────────────────

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# pheme_rumormill.json lives in config/ under the app root — two levels up from
# tools/. abspath() makes resolution independent of the current working directory.
_CONFIG_PATH = os.path.join(_APP_ROOT, "config", "pheme_rumormill.json")

# Read state — a runtime artifact beside argus_timeline.json, gitignored (§9).
_READ_PATH = os.path.join(_APP_ROOT, "pheme_read.json")
_READ_MAX  = 2000      # seen-set cap; oldest evicted first

# Browser-plausible RSS-reader User-Agent so picky edges (e.g. TownNews) don't 403.
_USER_AGENT   = "Mozilla/5.0 (compatible; Felhaven/1.0 RSS reader)"
_HTTP_TIMEOUT = 6      # per socket operation, seconds; the whole request: 12 s
# Per-feed byte cap (hermes rule: 4x the largest measured body, rounded up to a
# power of two). Measured 2026-10-01, the 7 shipped feeds: largest 244,346 B
# (theregister), then 141,717 (theverge) and 77,457 (arstechnica); the rest
# under 27 KB. Feeds are user-editable, so a new one may need this raised.
_MAX_BYTES = 1024 * 1024
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

# Query parameters dropped when deriving an item's read-state key. A DENYLIST,
# not a blanket query strip: Hacker News text posts (Ask HN / Show HN) carry
# their whole identity in the query — https://news.ycombinator.com/item?id=NNN —
# so dropping every parameter would collapse them all onto one key and reading
# one would dim the rest. BBC, by contrast, appends at_medium/at_campaign to
# every link, which must go or the same story keys differently between fetches.
# Prefix match on "utm_" is handled separately in _read_key.
_TRACKING_PARAMS = frozenset({
    "at_medium", "at_campaign", "at_custom1", "at_custom2", "at_custom3",
    "at_custom4", "at_bbc_team", "fbclid", "gclid", "ito", "ref", "src",
    "cmpid", "smid", "partner",
})


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


def _parse_date(raw: str) -> "datetime | None":
    """
    Parse an RSS/Atom date string, exactly as the feed wrote it — the result may
    be naive or aware depending on which of _DATE_FORMATS matched. None when no
    format fits. The two callers below want different things from it, so neither
    normalization nor formatting happens here.
    """
    if not raw:
        return None
    # Named UTC zones (BBC uses 'GMT') aren't understood by %z — normalize them.
    text = raw.strip().replace(" GMT", " +0000").replace(" UTC", " +0000")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _short_date(raw: str) -> str:
    """
    Reduce an RSS/Atom date to a short 'Jun 7' label, in the zone the feed
    published it (a story datelined Tuesday evening in New York reads 'Jul 22',
    not the UTC 'Jul 23'). Returns '' if the string can't be parsed — date is
    optional in the meta line, so an unparseable date just drops out gracefully.
    """
    dt = _parse_date(raw)
    if dt is None:
        return ""
    # str(dt.day) drops the leading zero without %-d/%#d (portable).
    return f"{dt.strftime('%b')} {dt.day}"


def _timestamp(raw: str) -> str:
    """
    A sortable UTC key for a feed date: 'YYYY-MM-DDTHH:MM:SSZ', or '' when the
    date is missing or unparseable.

    Fixed width and single-zone, so plain string comparison orders it correctly
    and the payload stays JSON-serialisable (§9) — the panel merges seven feeds
    on this key without reconstructing datetimes. Naive values are ASSUMED UTC:
    two of _DATE_FORMATS carry no offset, and comparing a naive datetime against
    an aware one raises TypeError, which would crash a merged render the moment
    one feed omitted its zone. Sub-second precision is dropped; ties fall to the
    caller's stable sort.
    """
    dt = _parse_date(raw)
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _story(title: str, url: str, author: str, date: str,
           ts: str = "") -> dict[str, str]:
    """Assemble the normalized story dict shared by every feed format.

    `ts` defaults to '' so a caller that only has a display date still builds a
    valid story; both real parsers pass it. An empty ts sorts last (see fetch()).
    """
    url = url.strip()
    return {
        "title":  html.unescape(title).strip() or "(no title)",
        "url":    url,
        "author": _clean_author(html.unescape(author)),
        "date":   date,
        "domain": _domain(url),
        "ts":     ts,
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
        raw    = _text(item, "pubDate")
        stories.append(
            _story(title, url, author, _short_date(raw), _timestamp(raw)))
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
        raw    = (_text_ns(entry, _ATOM_NS, "published")
                  or _text_ns(entry, _ATOM_NS, "updated"))
        stories.append(
            _story(title, url, author, _short_date(raw), _timestamp(raw)))
    return stories


# ── Per-feed fetch ───────────────────────────────────────────────────────────────

# Failure identifiers are inline literals at each dict site, not ERR_*
# constants, even though there are more than three (vs. §2's recommendation):
# tests/test_tool_output_contract.py verifies an f-string identifier by its
# literal head, which a spliced-in constant would hide — and every code below
# has exactly one site anyway. The zeno "literal at the dict site" precedent,
# without the exception-class machinery zeno needed (its errors are decided
# deep in a recursive walk; ours are decided right here at the except arm).

def _fetch_feed(feed: dict[str, Any]) -> dict[str, Any]:
    """
    Fetch and parse one feed. NEVER raises: a failure becomes {"error": str}
    scoped to this feed alone, so a single dead source can't poison siblings.
    Identifiers are the §2 slugs listed in the header docstring.
    """
    try:
        resp = hermes.get(
            feed["url"],
            headers={"User-Agent": _USER_AGENT},
            cap=_MAX_BYTES,
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        if feed.get("format") == "atom":
            stories = _parse_atom(root)
        else:
            stories = _parse_rss(root)

        return {"stories": stories[:_MAX_STORIES]}

    # Order matters: HTTPStatus, Timeout and TooLarge all subclass NetError,
    # and ParseError must be named before the bare Exception net.
    except hermes.HTTPStatus as e:
        # Surface the status code so the panel can show "HTTP 404".
        log.warning(f"Pheme: feed {feed.get('id')} HTTP {e.status}")
        return {"error": f"http_error: {e.status}"}
    except hermes.Timeout:
        log.warning(f"Pheme: feed {feed.get('id')} timed out")
        return {"error": "feed_timeout"}
    except hermes.TooLarge:
        log.warning(f"Pheme: feed {feed.get('id')} is over the {_MAX_BYTES}-byte cap")
        return {"error": "feed_too_large"}
    except hermes.NetError as e:
        # The message stays in the log (not the LLM surface, §2): it names
        # which layer refused, which the slug deliberately hides. hermes never
        # puts the URL in it.
        log.warning(f"Pheme: feed {feed.get('id')} network failure: {e}")
        return {"error": "network_error"}
    except ET.ParseError as e:
        log.warning(f"Pheme: feed {feed.get('id')} sent unparseable XML: {e}")
        return {"error": "invalid_feed_xml"}
    except Exception as e:
        log.warning(f"Pheme: feed {feed.get('id')} failed: "
                    f"{type(e).__name__}: {e}")
        return {"error": f"fetch_failed: {type(e).__name__}"}


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


# ── Read state ────────────────────────────────────────────────────────────────

# Read state lives here rather than in a module of its own: it is an attribute
# OF a headline, meaningful only alongside one, with exactly one consumer. A
# separate deity would be a shared-state coupling straight back to Pheme for no
# gain (the Anti-Legion call, CONVENTIONS §12).

_read_order: "deque[str]" = deque(maxlen=_READ_MAX)   # oldest first, for eviction
_read_set:   set[str]     = set()                     # same contents, O(1) lookup


def _atomic_write_json(path: str, payload: Any) -> None:
    """Write `payload` as JSON to `path` atomically: a uniquely-named temp file
    in the same directory, then os.replace. A crash mid-write can never truncate
    the destination (§9). The house pattern, per-module by convention — see
    cerberus._atomic_write_json, which this follows."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".pheme-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_key(url: str) -> str:
    """
    Stable identity for a story, used as its read-state key.

    The URL alone, normalized — NOT url + title, because a feed that corrects a
    typo in a headline would otherwise re-brighten an already-read story.
    Normalization lowercases scheme and host, drops a leading "www.", always
    drops the fragment, and drops the _TRACKING_PARAMS query keys (plus any
    utm_* prefix) while KEEPING the rest of the query — see _TRACKING_PARAMS for
    why a blanket query strip is wrong here.

    Known failure mode: a feed that starts appending an unrecognized,
    non-tracking parameter (a rotating cache-buster, say) re-brightens its items
    once. That is a cosmetic miss, deliberately preferred over the alternative —
    collapsing distinct stories onto one key, which silently dims news the user
    has never seen.

    Hashed, not stored raw, so pheme_read.json stays bounded and is not a
    plaintext browsing history.
    """
    text = url.strip()
    try:
        parts = urlparse(text)
    except Exception:
        # urlparse is near-total but can raise on malformed IPv6 literals; a key
        # for the raw string is still stable, which is all this needs to be.
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]

    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    query = urlencode([
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith("utm_")
    ])
    normalized = urlunparse((parts.scheme.lower(), netloc, parts.path,
                             parts.params, query, ""))   # "" == fragment dropped
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def read_keys() -> set[str]:
    """Snapshot of the seen-set for one render pass. A copy, so a click during
    a render can't mutate the set being iterated."""
    return set(_read_set)


def mark_read(url: str) -> None:
    """
    Record a click-through — the ONLY thing that marks a story read. No hover,
    no dwell timer, no scroll-past heuristic: dimming has to mean "you opened
    this" or it can't be trusted.

    A no-op when already read, so re-opening an old story doesn't refresh its
    position and evict something newer. Writes on change only, never per tick
    (the argus_timeline pattern).
    """
    if not url:
        return
    key = read_key(url)
    if key in _read_set:
        return
    # deque(maxlen) drops the leftmost silently on append — drop it from the
    # companion set in the same breath, or the two halves diverge and evicted
    # keys stay "read" forever.
    if len(_read_order) == _READ_MAX:
        _read_set.discard(_read_order[0])
    _read_order.append(key)
    _read_set.add(key)
    _save_read()


def _load_read() -> None:
    """Load the seen-set on import. Missing or corrupt file -> everything reads
    as unread; never raises, because an unreadable runtime file must not stop
    the news from rendering."""
    try:
        with open(_READ_PATH, encoding="utf-8") as f:
            keys: Any = json.load(f)["read"]
    except FileNotFoundError:
        return
    except Exception as e:
        log.warning("Pheme: could not load read state (%s: %s) — "
                    "treating everything as unread", type(e).__name__, e)
        return
    if not isinstance(keys, list):
        log.warning("Pheme: read state was not a list — "
                    "treating everything as unread")
        return
    for key in keys[-_READ_MAX:]:
        if isinstance(key, str) and key not in _read_set:
            _read_order.append(key)
            _read_set.add(key)


def _save_read() -> None:
    """Persist the seen-set. A failed write costs the dimming across a restart,
    never the render — so it warns rather than raising."""
    try:
        _atomic_write_json(_READ_PATH, {"read": list(_read_order)})
    except Exception as e:
        log.warning("Pheme: could not save read state: %s: %s",
                    type(e).__name__, e)


_load_read()


# ── Freshness (V0.03 audit T7) ────────────────────────────────────────────────
# tools/aura.py's "Freshness" section is the reference for this pattern, and
# for why the lock is held across the fan-out; it isn't repeated here. The
# fan-out is bounded: the feeds run in parallel, each ending at its hermes
# deadline (12 s), so the lock is held about that long at the very worst. Kairos
# polls fetch() every 900 s, and handle() answers from the last good fan-out
# instead of fetching again, dated with `as_of`. Two things differ from Aura:
#
#   - The key is the feed config itself, so an edit to pheme_rumormill.json is
#     a new key and never answers from the old feeds' stories.
#   - A fan-out is good when at least ONE feed answered. A partial snapshot is
#     what the panel shows too; an all-failed one is a failure, and never
#     replaces the last good snapshot.

CACHE_TTL = 900             # s. Kairos's pheme interval, so handle() normally reuses the poll
_RETRY_AFTER = 60           # s. A failed refresh isn't retried sooner: one turn, one attempt
_STALE_MAX = 2 * CACHE_TTL  # s. The oldest snapshot still served, marked stale, when a refresh fails

_Key = tuple[tuple[str, str, str], ...]     # (id, url, format) per configured feed


@dataclass(frozen=True)
class _Snapshot:
    key: _Key
    results: dict[str, Any]   # fetch()'s per-feed dict; the cache's own copy
    at: float                 # time.monotonic()
    as_of: str                # naive local ISO, pythia._now()'s convention


@dataclass(frozen=True)
class _Failure:
    key: _Key
    at: float                 # time.monotonic()


_lock = threading.Lock()
_snapshot: _Snapshot | None = None
_failure: _Failure | None = None


def _now() -> datetime:
    """Injectable clock seam for `as_of`. Naive local time, as aura._now()."""
    return datetime.now()


def _config_key(feeds: list[dict[str, Any]]) -> _Key:
    """Everything that shapes a fan-out: which feeds, where, in what format."""
    return tuple((str(f.get("id", "")), str(f.get("url", "")),
                  str(f.get("format", ""))) for f in feeds)


def _any_answered(results: dict[str, Any]) -> bool:
    """A fan-out worth keeping: at least one feed answered. (An empty config
    has nothing to fail, so its empty snapshot counts.)"""
    return not results or any("error" not in r for r in results.values())


def _remember(key: _Key, results: dict[str, Any]) -> _Snapshot:
    """Store a good fan-out and clear any failure. Caller holds _lock."""
    global _snapshot, _failure
    _snapshot = _Snapshot(key, copy.deepcopy(results), time.monotonic(),
                          _now().isoformat(timespec="seconds"))
    _failure = None
    return _snapshot


def _current(feeds: list[dict[str, Any]]) -> tuple[dict[str, Any], str, bool] | None:
    """The per-feed results handle() answers from, as (results, as_of, stale),
    or None when nothing usable is left. Fans out only when the cache is stale
    or empty and no refresh failed inside _RETRY_AFTER. The results are a copy,
    since handle() reads them after the lock is released."""
    global _failure
    key = _config_key(feeds)
    with _lock:
        now = time.monotonic()
        last = _snapshot if _snapshot is not None and _snapshot.key == key else None
        if last is not None and now - last.at < CACHE_TTL:
            return copy.deepcopy(last.results), last.as_of, False
        failed = _failure if _failure is not None and _failure.key == key else None
        if failed is None or now - failed.at >= _RETRY_AFTER:
            results = _fetch_all(feeds)
            if _any_answered(results):
                # `results` is the fan-out's own dict; the cache kept a copy.
                return results, _remember(key, results).as_of, False
            _failure = _Failure(key, time.monotonic())
        if last is not None and now - last.at < _STALE_MAX:
            return copy.deepcopy(last.results), last.as_of, True
        return None


def _clear_cache() -> None:
    """Forget every snapshot and failure. For tests, so none leaks between them."""
    global _snapshot, _failure
    with _lock:
        _snapshot = None
        _failure = None


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
    Brain entry point — never raises. Returns a flat {"stories": [...]} capped
    at _MAX_STORIES, dated with `as_of`; `feed` narrows to a single source,
    omit for a mix of all. Failures are the §2 errors in the header docstring,
    so an outage never masquerades as a quiet news day.

    Answers from the last good fan-out while it is younger than CACHE_TTL (see
    "Freshness"). A refresh always fans out to every feed, even for one `feed`,
    because the snapshot is shared by every question and by the panel's poll.
    """
    try:
        feeds = _load_config()
    except Exception as e:
        log.error(f"Pheme: handle() could not read config: "
                  f"{type(e).__name__}: {e}")
        return {"error": "config_unreadable"}
    try:
        selected = feeds
        if feed:
            selected = [f for f in feeds if f["id"] == feed]
            if not selected:
                return {"error": f"unknown_feed: {feed}"}
        current = _current(feeds)
        if current is None:
            return {"error": "all_feeds_failed"}
        results, as_of, stale = current
        if selected and all("error" in results[f["id"]] for f in selected):
            return {"error": "all_feeds_failed"}
        # Pull per-feed lists in config order; skip feeds that errored.
        per_feed = [results[f["id"]].get("stories", []) for f in selected]
        merged = _interleave(per_feed)[:_MAX_STORIES]
        # `ts` is the panel's merge key, not part of this contract — strip it so
        # the Brain's story shape is exactly what it was before Scrapstream.
        answer: dict[str, Any] = {
            "stories": [{k: v for k, v in s.items() if k != "ts"} for s in merged],
            "as_of": as_of,
        }
        if stale:
            answer["stale"] = True
        return answer
    except Exception as e:
        # Nothing in the fan-out is expected to raise (_fetch_feed never
        # does); this net is the never-raises contract, not a code path.
        log.error(f"Pheme: handle() failed: {type(e).__name__}: {e}")
        return {"error": f"news_failed: {type(e).__name__}"}


def fetch() -> dict[str, Any]:
    """
    Kairos entry point — fans every feed out concurrently and tags each result
    by feed id. Raises only if the config itself is unreadable, so Kairos
    delivers None to the panel; a single dead feed yields {"error":…} instead.

    Always fans out, even when the cache is fresh: this poll IS the refresh.
    A fan-out where any feed answered is stored for handle(); an all-failed
    one still reaches the panel, but leaves the last good snapshot in place.
    """
    feeds = _load_config()
    key = _config_key(feeds)
    with _lock:
        results = _fetch_all(feeds)
        if _any_answered(results):
            _remember(key, results)
    return {"feeds": results}


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
