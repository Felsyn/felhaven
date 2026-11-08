"""
test_pheme.py — unit tests for tools/pheme.py (RSS/Atom news aggregator).

Hermetic: the single network seam (pheme.hermes.get) and the config seam
(pheme._load_config / pheme._CONFIG_PATH) are mocked, so no test touches the
network or the shipped pheme_rumormill.json. Run from the package root:

    python -X utf8 -m unittest tests.test_pheme

Fixtures are trimmed captures of the real feeds (2026-07-23) rather than
hand-built XML (CONVENTIONS §10) — they keep the quirks that matter: BBC's
CDATA titles and named-zone "GMT" dates, Ars's dc:creator wrapped in
whitespace *around* its CDATA, The Verge's default-namespace Atom with both
<published> and <updated>. Small hand-built documents cover only the fallback
branches no captured feed happened to exercise (plain <author>, a missing
title, an entry with no alternate link).

Covers:
  1. Field normalizers — _short_date, _clean_author, _domain, _story.
  2. Both parsers against the captured fixtures + fallback branches.
  3. _fetch_feed's §2 error identifiers, one per failure mode, and the
     per-feed story cap.
  4. _interleave, handle() (mix, narrowing, cap, and its own §2 errors),
     and fetch()'s raise-only-on-unreadable-config contract.
  5. The Scrapstream additions — `ts` (the UTC sort key kept alongside the
     display date), read_key's normalization, and the read-state file.
  7. Freshness (V0.03 audit T7) — handle() answers from the last good
     fan-out, dated with `as_of`; counted at the _fetch_feed seam.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from collections import deque
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import hermes, pheme
from tests._loopback import Loopback, drip, read_head


# ─────────────────────────────────────────────────────────────────────────────
#  Fixtures — trimmed captures of the real feeds (2026-07-23)
# ─────────────────────────────────────────────────────────────────────────────

# BBC World RSS: CDATA titles, "GMT" pubDates, querystringed links, no author.
_BBC_RSS = """<?xml version="1.0" encoding="UTF-8"?><rss xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:atom="http://www.w3.org/2005/Atom" version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
    <channel>
        <title><![CDATA[BBC News]]></title>
        <link>https://www.bbc.co.uk/news/world</link>
        <lastBuildDate>Fri, 24 Jul 2026 03:00:20 GMT</lastBuildDate>
        <item>
            <title><![CDATA[US imposes tariffs on dozens of trade partners over 'forced labour' imports]]></title>
            <description><![CDATA[The tariffs target around 60 trading partners.]]></description>
            <link>https://www.bbc.co.uk/news/articles/cvgj61j6l08o?at_medium=RSS&amp;at_campaign=rss</link>
            <guid isPermaLink="false">https://www.bbc.co.uk/news/articles/cvgj61j6l08o#0</guid>
            <pubDate>Fri, 24 Jul 2026 02:10:00 GMT</pubDate>
            <media:thumbnail width="240" height="135" url="https://ichef.bbci.co.uk/ace/standard/240/x.jpg"/>
        </item>
        <item>
            <title><![CDATA[Oil prices hit $100 for the first time since May]]></title>
            <description><![CDATA[The price of Brent crude rose more than 6% on Thursday.]]></description>
            <link>https://www.bbc.co.uk/news/articles/cx2djnzrqk2o?at_medium=RSS&amp;at_campaign=rss</link>
            <pubDate>Thu, 23 Jul 2026 15:31:00 GMT</pubDate>
        </item>
    </channel>
</rss>"""

# Ars Technica RSS: dc:creator with whitespace wrapped AROUND the CDATA — the
# .text a parser sees is "\n  Scharon Harding\n", so the strip is load-bearing.
_ARS_RSS = """<?xml version="1.0" encoding="UTF-8" standalone="no"?><rss xmlns:atom="http://www.w3.org/2005/Atom" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0">
    <channel>
        <title>Ars Technica - All content</title>
        <link>https://arstechnica.com</link>
        <item>
            <title>Microsoft responds to LG monitors installing McAfee ads on Windows</title>
            <link>https://arstechnica.com/gadgets/2026/07/microsoft-responds/</link>
            <dc:creator>
                <![CDATA[Scharon Harding]]>
            </dc:creator>
            <pubDate>Thu, 23 Jul 2026 20:47:33 +0000</pubDate>
        </item>
    </channel>
</rss>"""

# The Verge Atom: default Atom namespace, <author><name>, rel="alternate"
# entry link, both <published> and <updated> with a numeric offset.
_VERGE_ATOM = """<?xml version="1.0" encoding="UTF-8"?><feed
\txmlns="http://www.w3.org/2005/Atom"
\txmlns:thr="http://purl.org/syndication/thread/1.0"
\txml:lang="en-US"
\t>
\t<title type="text">The Verge</title>
\t<updated>2026-07-23T22:02:15+00:00</updated>
\t<link rel="alternate" type="text/html" href="https://www.theverge.com" />
\t<link rel="self" type="application/atom+xml" href="https://www.theverge.com/rss/index.xml" />
\t<entry>
\t\t<author>
\t\t\t<name>Emma Roth</name>
\t\t</author>
\t\t<title type="html"><![CDATA[Alexa Plus is getting an AI update to handle more complicated instructions]]></title>
\t\t<link rel="alternate" type="text/html" href="https://www.theverge.com/tech/970399/amazon-alexa-plus" />
\t\t<id>https://www.theverge.com/?p=970399</id>
\t\t<updated>2026-07-23T18:02:15-04:00</updated>
\t\t<published>2026-07-22T17:15:06-04:00</published>
\t</entry>
</feed>"""

# Hand-built: the fallback branches no captured feed exercises.
_FALLBACK_RSS = """<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <item>
      <title>Creator beats author</title>
      <link>https://example.com/1</link>
      <dc:creator>Dublin Core</dc:creator>
      <author>plain@example.com</author>
    </item>
    <item>
      <title>Author fallback</title>
      <link>https://example.com/2</link>
      <author>By A. Byline</author>
    </item>
    <item>
      <link>https://example.com/untitled</link>
    </item>
  </channel>
</rss>"""

_FALLBACK_ATOM = """<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>No alternate link</title>
    <link rel="via" href="https://example.com/via" />
  </entry>
  <entry>
    <title>Updated only</title>
    <link rel="alternate" href="https://example.com/updated-only" />
    <updated>2026-06-07T08:00:00+00:00</updated>
  </entry>
  <entry>
    <title>No link at all</title>
  </entry>
</feed>"""


def _resp(content: bytes, status: "int | None" = None):
    """Stand-in for hermes.get's Reply: .content + .raise_for_status()."""
    r = mock.Mock()
    r.content = content
    if status is None:
        r.raise_for_status.return_value = None
    else:
        r.status_code = status
        r.raise_for_status.side_effect = hermes.HTTPStatus(status)
    return r


_FEED = {"id": "test", "url": "https://example.com/feed", "format": "rss"}


# ─────────────────────────────────────────────────────────────────────────────
#  1. Field normalizers
# ─────────────────────────────────────────────────────────────────────────────

class TestShortDate(unittest.TestCase):
    def test_rfc822_with_numeric_tz(self):
        self.assertEqual(pheme._short_date("Thu, 23 Jul 2026 20:47:33 +0000"),
                         "Jul 23")

    def test_named_gmt_zone_is_normalized(self):
        # BBC's real dates say "GMT", which %z cannot parse un-normalized.
        self.assertEqual(pheme._short_date("Fri, 24 Jul 2026 02:10:00 GMT"),
                         "Jul 24")

    def test_iso8601_with_offset(self):
        self.assertEqual(pheme._short_date("2026-07-22T17:15:06-04:00"),
                         "Jul 22")

    def test_iso8601_fractional_seconds(self):
        self.assertEqual(pheme._short_date("2026-07-23T14:30:00.123456+00:00"),
                         "Jul 23")

    def test_single_digit_day_has_no_leading_zero(self):
        self.assertEqual(pheme._short_date("Sun, 07 Jun 2026 08:00:00 GMT"),
                         "Jun 7")

    def test_garbage_and_empty_drop_out_as_empty(self):
        self.assertEqual(pheme._short_date("yesterday-ish"), "")
        self.assertEqual(pheme._short_date(""), "")


class TestCleanAuthorAndDomain(unittest.TestCase):
    def test_by_prefix_is_stripped_case_insensitively(self):
        self.assertEqual(pheme._clean_author("By Jane Doe"), "Jane Doe")
        self.assertEqual(pheme._clean_author("by jane"), "jane")

    def test_a_name_starting_with_by_is_not_mangled(self):
        self.assertEqual(pheme._clean_author("Byron Smith"), "Byron Smith")

    def test_empty_author_stays_empty(self):
        self.assertEqual(pheme._clean_author(""), "")

    def test_domain_strips_www_only(self):
        self.assertEqual(pheme._domain("https://www.theverge.com/x"),
                         "theverge.com")
        self.assertEqual(pheme._domain("https://news.ycombinator.com/rss"),
                         "news.ycombinator.com")

    def test_unparseable_url_gives_empty_domain(self):
        self.assertEqual(pheme._domain(""), "")


class TestStory(unittest.TestCase):
    def test_entities_are_unescaped_and_fields_normalized(self):
        s = pheme._story("Cats &amp; dogs", " https://example.com ",
                         "By X", "Jul 1")
        self.assertEqual(s["title"], "Cats & dogs")
        self.assertEqual(s["url"], "https://example.com")
        self.assertEqual(s["author"], "X")
        self.assertEqual(s["domain"], "example.com")

    def test_missing_title_becomes_the_placeholder(self):
        self.assertEqual(pheme._story("", "u", "", "")["title"], "(no title)")


# ─────────────────────────────────────────────────────────────────────────────
#  2. Parsers
# ─────────────────────────────────────────────────────────────────────────────

class TestParseRss(unittest.TestCase):
    def test_bbc_capture(self):
        from xml.etree import ElementTree as ET
        stories = pheme._parse_rss(ET.fromstring(_BBC_RSS))
        self.assertEqual(len(stories), 2)
        self.assertEqual(
            stories[0]["title"],
            "US imposes tariffs on dozens of trade partners over "
            "'forced labour' imports")
        self.assertEqual(stories[0]["date"], "Jul 24")
        self.assertEqual(stories[0]["domain"], "bbc.co.uk")
        self.assertEqual(stories[0]["author"], "")

    def test_ars_capture_strips_the_whitespace_around_dc_creator(self):
        from xml.etree import ElementTree as ET
        stories = pheme._parse_rss(ET.fromstring(_ARS_RSS))
        self.assertEqual(stories[0]["author"], "Scharon Harding")

    def test_dc_creator_beats_plain_author(self):
        from xml.etree import ElementTree as ET
        stories = pheme._parse_rss(ET.fromstring(_FALLBACK_RSS))
        self.assertEqual(stories[0]["author"], "Dublin Core")

    def test_plain_author_is_the_fallback_and_loses_its_byline(self):
        from xml.etree import ElementTree as ET
        stories = pheme._parse_rss(ET.fromstring(_FALLBACK_RSS))
        self.assertEqual(stories[1]["author"], "A. Byline")

    def test_a_titleless_item_gets_the_placeholder(self):
        from xml.etree import ElementTree as ET
        stories = pheme._parse_rss(ET.fromstring(_FALLBACK_RSS))
        self.assertEqual(stories[2]["title"], "(no title)")


class TestParseAtom(unittest.TestCase):
    def test_verge_capture(self):
        from xml.etree import ElementTree as ET
        stories = pheme._parse_atom(ET.fromstring(_VERGE_ATOM))
        self.assertEqual(len(stories), 1)
        s = stories[0]
        self.assertEqual(s["author"], "Emma Roth")
        self.assertEqual(s["url"],
                         "https://www.theverge.com/tech/970399/amazon-alexa-plus")
        # <published> (Jul 22) wins over <updated> (Jul 23).
        self.assertEqual(s["date"], "Jul 22")

    def test_link_fallback_and_updated_fallback(self):
        from xml.etree import ElementTree as ET
        stories = pheme._parse_atom(ET.fromstring(_FALLBACK_ATOM))
        # No rel="alternate": the first link with an href is taken.
        self.assertEqual(stories[0]["url"], "https://example.com/via")
        # No <published>: <updated> supplies the date.
        self.assertEqual(stories[1]["date"], "Jun 7")
        # No link at all: url is simply empty (the panel renders unclickable).
        self.assertEqual(stories[2]["url"], "")


# ─────────────────────────────────────────────────────────────────────────────
#  3. _fetch_feed — one §2 identifier per failure mode
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchFeedErrors(unittest.TestCase):
    def test_http_failure_carries_the_status_as_detail(self):
        with mock.patch.object(pheme.hermes, "get",
                               return_value=_resp(b"", status=404)):
            self.assertEqual(pheme._fetch_feed(_FEED),
                             {"error": "http_error: 404"})

    def test_a_feed_over_the_cap_says_so(self):
        with mock.patch.object(pheme.hermes, "get",
                               side_effect=hermes.TooLarge("over the cap")):
            self.assertEqual(pheme._fetch_feed(_FEED),
                             {"error": "feed_too_large"})

    def test_every_fetch_is_capped(self):
        with mock.patch.object(pheme.hermes, "get", return_value=_resp(b"<rss/>")) as get:
            pheme._fetch_feed(_FEED)
        self.assertEqual(get.call_args.kwargs["cap"], pheme._MAX_BYTES)

    def test_timeout(self):
        with mock.patch.object(pheme.hermes, "get",
                               side_effect=hermes.Timeout("6s elapsed")):
            self.assertEqual(pheme._fetch_feed(_FEED),
                             {"error": "feed_timeout"})

    def test_connection_failure(self):
        with mock.patch.object(pheme.hermes, "get",
                               side_effect=hermes.Offline("refused")):
            self.assertEqual(pheme._fetch_feed(_FEED),
                             {"error": "network_error"})

    def test_unparseable_xml(self):
        with mock.patch.object(pheme.hermes, "get",
                               return_value=_resp(b"<html>not a feed")):
            self.assertEqual(pheme._fetch_feed(_FEED),
                             {"error": "invalid_feed_xml"})

    def test_anything_else_reports_the_class_name_never_the_message(self):
        # A config row without "url" — the KeyError message would be fine, but
        # the rule is uniform: class name only (§2), because the same handler
        # catches exceptions whose messages carry paths and URLs.
        with mock.patch.object(pheme.hermes, "get",
                               side_effect=AssertionError("must not be called")):
            self.assertEqual(pheme._fetch_feed({"id": "bad"}),
                             {"error": "fetch_failed: KeyError"})


class TestFetchFeedSuccess(unittest.TestCase):
    def test_a_healthy_feed_parses_and_is_capped(self):
        items = "".join(
            f"<item><title>t{n}</title><link>https://e.com/{n}</link></item>"
            for n in range(pheme._MAX_STORIES + 2))
        xml = f"<rss><channel>{items}</channel></rss>".encode()
        with mock.patch.object(pheme.hermes, "get", return_value=_resp(xml)):
            result = pheme._fetch_feed(_FEED)
        self.assertNotIn("error", result)
        self.assertEqual(len(result["stories"]), pheme._MAX_STORIES)

    def test_the_format_field_selects_the_atom_parser(self):
        with mock.patch.object(pheme.hermes, "get",
                               return_value=_resp(_VERGE_ATOM.encode())):
            result = pheme._fetch_feed({**_FEED, "format": "atom"})
        self.assertEqual(len(result["stories"]), 1)
        self.assertEqual(result["stories"][0]["author"], "Emma Roth")


# ─────────────────────────────────────────────────────────────────────────────
#  4. _interleave / handle() / fetch()
# ─────────────────────────────────────────────────────────────────────────────

class TestInterleave(unittest.TestCase):
    def test_round_robin_dropping_exhausted_feeds(self):
        self.assertEqual(
            pheme._interleave([["a1", "a2", "a3"], ["b1"], ["c1", "c2"]]),
            ["a1", "b1", "c1", "a2", "c2", "a3"])

    def test_no_feeds_is_an_empty_list(self):
        self.assertEqual(pheme._interleave([]), [])


def _story_n(fid, n):
    # Same shape _story() produces, `ts` included — a fake that omitted it
    # would let handle()'s strip pass without ever removing anything.
    return {"title": f"{fid}{n}", "url": "", "author": "", "date": "",
            "domain": "", "ts": f"2026-07-{n + 1:02d}T00:00:00Z"}


def _fake_fetch_feed(feed):
    """Deterministic stand-in keyed on feed id: 'dead' errors, others yield
    two stories apiece."""
    if feed["id"] == "dead":
        return {"error": "feed_timeout"}
    return {"stories": [_story_n(feed["id"], n) for n in range(2)]}


_TWO_FEEDS = [{"id": "a", "url": "u", "format": "rss"},
              {"id": "b", "url": "u", "format": "rss"}]


class _FreshCache(unittest.TestCase):
    """handle() answers from Pheme's cache and remembers a failure (T7, §7
    below). The same config is the same cache key, so without this one test's
    stories — or its failure — would answer the next test's handle()."""

    def setUp(self):
        pheme._clear_cache()
        self.addCleanup(pheme._clear_cache)


class TestHandle(_FreshCache):
    def test_stories_are_interleaved_across_feeds_in_config_order(self):
        with mock.patch.object(pheme, "_load_config", return_value=_TWO_FEEDS), \
             mock.patch.object(pheme, "_fetch_feed", _fake_fetch_feed):
            titles = [s["title"] for s in pheme.handle()["stories"]]
        self.assertEqual(titles, ["a0", "b0", "a1", "b1"])

    def test_the_flat_list_is_capped(self):
        many = [{"id": f"f{n}", "url": "u", "format": "rss"} for n in range(9)]
        def fat(feed):
            return {"stories": [_story_n(feed["id"], n) for n in range(5)]}
        with mock.patch.object(pheme, "_load_config", return_value=many), \
             mock.patch.object(pheme, "_fetch_feed", fat):
            self.assertEqual(len(pheme.handle()["stories"]), pheme._MAX_STORIES)

    def test_feed_narrows_to_a_single_source(self):
        with mock.patch.object(pheme, "_load_config", return_value=_TWO_FEEDS), \
             mock.patch.object(pheme, "_fetch_feed", _fake_fetch_feed):
            titles = [s["title"] for s in pheme.handle(feed="b")["stories"]]
        self.assertEqual(titles, ["b0", "b1"])

    def test_a_dead_feed_is_skipped_not_fatal(self):
        feeds = _TWO_FEEDS + [{"id": "dead", "url": "u", "format": "rss"}]
        with mock.patch.object(pheme, "_load_config", return_value=feeds), \
             mock.patch.object(pheme, "_fetch_feed", _fake_fetch_feed):
            result = pheme.handle()
        self.assertNotIn("error", result)
        self.assertEqual(len(result["stories"]), 4)

    # ── handle()'s own §2 errors ────────────────────────────────────────────

    def test_an_unknown_feed_id_is_an_error_not_a_quiet_empty_list(self):
        with mock.patch.object(pheme, "_load_config", return_value=_TWO_FEEDS):
            self.assertEqual(pheme.handle(feed="nope"),
                             {"error": "unknown_feed: nope"})

    def test_an_unreadable_config_is_an_error(self):
        with mock.patch.object(pheme, "_load_config",
                               side_effect=FileNotFoundError("gone")):
            self.assertEqual(pheme.handle(), {"error": "config_unreadable"})

    def test_every_feed_failing_is_an_error(self):
        dead = [{"id": "dead", "url": "u", "format": "rss"}]
        with mock.patch.object(pheme, "_load_config", return_value=dead), \
             mock.patch.object(pheme, "_fetch_feed", _fake_fetch_feed):
            self.assertEqual(pheme.handle(), {"error": "all_feeds_failed"})

    def test_healthy_feeds_with_zero_items_is_not_an_error(self):
        # The one case that legitimately has no stories. It was an exact
        # {"stories": []} until the answer gained `as_of` (T7).
        with mock.patch.object(pheme, "_load_config", return_value=_TWO_FEEDS), \
             mock.patch.object(pheme, "_fetch_feed",
                               lambda f: {"stories": []}):
            result = pheme.handle()
        self.assertNotIn("error", result)
        self.assertEqual(result["stories"], [])


class TestFetch(_FreshCache):
    def test_raises_when_the_config_is_unreadable(self):
        # The documented contract: Kairos gets the raise and delivers None.
        missing = os.path.join(os.path.dirname(pheme._CONFIG_PATH),
                               "no_such_rumormill.json")
        with mock.patch.object(pheme, "_CONFIG_PATH", missing):
            with self.assertRaises(FileNotFoundError):
                pheme.fetch()

    def test_every_configured_id_is_present_even_when_one_died(self):
        feeds = _TWO_FEEDS + [{"id": "dead", "url": "u", "format": "rss"}]
        with mock.patch.object(pheme, "_load_config", return_value=feeds), \
             mock.patch.object(pheme, "_fetch_feed", _fake_fetch_feed):
            result = pheme.fetch()
        self.assertEqual(set(result["feeds"]), {"a", "b", "dead"})
        self.assertEqual(result["feeds"]["dead"], {"error": "feed_timeout"})
        self.assertEqual(len(result["feeds"]["a"]["stories"]), 2)


# ─────────────────────────────────────────────────────────────────────────────
#  5. Scrapstream — the UTC sort key
# ─────────────────────────────────────────────────────────────────────────────

class TestTimestamp(unittest.TestCase):
    def test_offsets_are_normalized_to_utc(self):
        self.assertEqual(pheme._timestamp("2026-07-22T17:15:06-04:00"),
                         "2026-07-22T21:15:06Z")

    def test_a_naive_date_is_assumed_utc_rather_than_left_naive(self):
        # _parse_date hands back exactly what the feed wrote...
        self.assertIsNone(pheme._parse_date("2026-07-23T14:30:00").tzinfo)
        # ...and _timestamp is where the zone is decided.
        self.assertEqual(pheme._timestamp("2026-07-23T14:30:00"),
                         "2026-07-23T14:30:00Z")

    def test_named_gmt_zone_is_normalized(self):
        self.assertEqual(pheme._timestamp("Fri, 24 Jul 2026 02:10:00 GMT"),
                         "2026-07-24T02:10:00Z")

    def test_fractional_seconds_are_dropped_to_keep_the_key_fixed_width(self):
        self.assertEqual(pheme._timestamp("2026-07-23T14:30:00.123456+00:00"),
                         "2026-07-23T14:30:00Z")
        self.assertEqual(len(pheme._timestamp("2026-07-23T14:30:00")), 20)

    def test_garbage_and_empty_give_an_empty_key(self):
        self.assertEqual(pheme._timestamp("yesterday-ish"), "")
        self.assertEqual(pheme._timestamp(""), "")

    def test_mixed_naive_and_aware_feeds_sort_without_raising(self):
        # The crash this normalization exists to prevent: comparing a naive
        # datetime against an aware one raises TypeError, and the merged river
        # sorts across seven feeds that need not agree about offsets.
        keys = [pheme._timestamp(r) for r in
                ("2026-07-23T14:30:00",                  # naive
                 "Thu, 23 Jul 2026 20:47:33 +0000",      # aware
                 "2026-07-22T17:15:06-04:00",            # aware, offset
                 "not a date")]                          # unparseable
        self.assertEqual(sorted(keys, reverse=True),
                         ["2026-07-23T20:47:33Z", "2026-07-23T14:30:00Z",
                          "2026-07-22T21:15:06Z", ""])

    def test_string_order_matches_time_order(self):
        earlier = pheme._timestamp("Thu, 23 Jul 2026 20:47:33 +0000")
        later   = pheme._timestamp("Thu, 23 Jul 2026 20:47:34 +0000")
        self.assertLess(earlier, later)

    def test_the_display_date_still_reads_in_the_feeds_own_zone(self):
        # Deliberately divergent: a story datelined Wednesday evening in New
        # York shows "Jul 22" but sorts as the UTC Jul 23 it actually is.
        raw = "2026-07-22T21:00:00-04:00"
        self.assertEqual(pheme._short_date(raw), "Jul 22")
        self.assertEqual(pheme._timestamp(raw), "2026-07-23T01:00:00Z")


class TestStoriesCarryTheSortKey(_FreshCache):
    def test_rss_parser_fills_ts(self):
        from xml.etree import ElementTree as ET
        stories = pheme._parse_rss(ET.fromstring(_BBC_RSS))
        self.assertEqual(stories[0]["ts"], "2026-07-24T02:10:00Z")
        self.assertEqual(stories[0]["date"], "Jul 24")

    def test_atom_parser_fills_ts_from_published(self):
        from xml.etree import ElementTree as ET
        stories = pheme._parse_atom(ET.fromstring(_VERGE_ATOM))
        self.assertEqual(stories[0]["ts"], "2026-07-22T21:15:06Z")

    def test_a_dateless_item_gets_an_empty_ts_and_is_not_dropped(self):
        from xml.etree import ElementTree as ET
        stories = pheme._parse_rss(ET.fromstring(_FALLBACK_RSS))
        self.assertEqual(len(stories), 3)
        self.assertEqual(stories[0]["ts"], "")

    def test_handle_strips_ts_so_its_output_shape_is_unchanged(self):
        # The Brain's story shape is deliberately out of scope for Scrapstream.
        with mock.patch.object(pheme, "_load_config", return_value=_TWO_FEEDS), \
             mock.patch.object(pheme, "_fetch_feed", _fake_fetch_feed):
            story = pheme.handle()["stories"][0]
        self.assertEqual(set(story),
                         {"title", "url", "author", "date", "domain"})

    def test_fetch_keeps_ts_for_the_panel(self):
        with mock.patch.object(pheme, "_load_config", return_value=_TWO_FEEDS), \
             mock.patch.object(pheme, "_fetch_feed", _fake_fetch_feed):
            story = pheme.fetch()["feeds"]["a"]["stories"][0]
        self.assertIn("ts", story)


# ─────────────────────────────────────────────────────────────────────────────
#  6. Read state — identity (I2) and the runtime file
# ─────────────────────────────────────────────────────────────────────────────

class TestReadKey(unittest.TestCase):
    def test_tracking_params_are_stripped(self):
        # BBC appends these to every link in the real feed.
        bare = "https://www.bbc.co.uk/news/articles/cvgj61j6l08o"
        self.assertEqual(
            pheme.read_key(bare + "?at_medium=RSS&at_campaign=rss"),
            pheme.read_key(bare))

    def test_utm_prefixed_params_are_stripped(self):
        self.assertEqual(pheme.read_key("https://a.com/x?utm_source=rss"),
                         pheme.read_key("https://a.com/x"))

    def test_hacker_news_text_posts_keep_distinct_keys(self):
        # The reason this is a denylist and not a blanket query strip: an
        # Ask HN post's whole identity is its query. Stripping it would
        # collapse every text post onto one key, so reading one would dim
        # every other — news the user has never seen, silently greyed out.
        self.assertNotEqual(
            pheme.read_key("https://news.ycombinator.com/item?id=44123456"),
            pheme.read_key("https://news.ycombinator.com/item?id=44123457"))

    def test_an_unrecognized_param_is_kept(self):
        self.assertNotEqual(pheme.read_key("https://a.com/x?id=1"),
                            pheme.read_key("https://a.com/x"))

    def test_the_fragment_is_always_dropped(self):
        self.assertEqual(pheme.read_key("https://a.com/x#comments"),
                         pheme.read_key("https://a.com/x"))

    def test_host_case_and_www_are_normalized(self):
        self.assertEqual(pheme.read_key("HTTPS://WWW.A.com/x"),
                         pheme.read_key("https://a.com/x"))

    def test_a_different_path_is_a_different_story(self):
        self.assertNotEqual(pheme.read_key("https://a.com/x"),
                            pheme.read_key("https://a.com/y"))

    def test_the_key_is_bounded_and_a_malformed_url_does_not_raise(self):
        for url in ("", "not a url", "http://[oops", "https://a.com/x"):
            self.assertEqual(len(pheme.read_key(url)), 16)


class _ReadStateTestCase(unittest.TestCase):
    """Redirects _READ_PATH into a tmpdir and empties the in-memory seen-set,
    so no test reads or writes the developer's own pheme_read.json."""

    def setUp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.path = os.path.join(d, "pheme_read.json")
        p = mock.patch.object(pheme, "_READ_PATH", self.path)
        p.start()
        self.addCleanup(p.stop)
        self._clear()
        self.addCleanup(self._clear)

    def _clear(self):
        pheme._read_order.clear()
        pheme._read_set.clear()

    def _use_cap(self, cap):
        """Shrink the seen-set cap. The deque's maxlen is fixed at construction,
        so the cap can't be patched on its own — the deque is replaced too."""
        for target, value in (("_READ_MAX", cap),
                              ("_read_order", deque(maxlen=cap))):
            p = mock.patch.object(pheme, target, value)
            p.start()
            self.addCleanup(p.stop)
        pheme._read_set.clear()


class TestReadState(_ReadStateTestCase):
    def test_round_trip_survives_a_reload(self):
        url = "https://a.com/story"
        pheme.mark_read(url)
        self.assertIn(pheme.read_key(url), pheme.read_keys())
        self._clear()
        self.assertNotIn(pheme.read_key(url), pheme.read_keys())
        pheme._load_read()
        self.assertIn(pheme.read_key(url), pheme.read_keys())

    def test_marking_twice_does_not_duplicate_or_reorder(self):
        pheme.mark_read("https://a.com/1")
        pheme.mark_read("https://a.com/2")
        pheme.mark_read("https://a.com/1")
        with open(self.path, encoding="utf-8") as f:
            stored = json.load(f)["read"]
        self.assertEqual(stored, [pheme.read_key("https://a.com/1"),
                                  pheme.read_key("https://a.com/2")])

    def test_an_empty_url_is_a_no_op(self):
        pheme.mark_read("")
        self.assertEqual(pheme.read_keys(), set())
        self.assertFalse(os.path.exists(self.path))

    def test_read_keys_is_a_copy_not_the_live_set(self):
        pheme.mark_read("https://a.com/1")
        snapshot = pheme.read_keys()
        pheme.mark_read("https://a.com/2")
        self.assertEqual(len(snapshot), 1)

    def test_the_set_is_evicted_oldest_first_at_the_cap(self):
        self._use_cap(3)
        for n in range(4):
            pheme.mark_read(f"https://a.com/{n}")
        keys = pheme.read_keys()
        self.assertEqual(len(keys), 3)
        # The oldest fell out of BOTH halves — a key left in the set but not
        # the deque would stay "read" forever and never be evicted again.
        self.assertNotIn(pheme.read_key("https://a.com/0"), keys)
        self.assertIn(pheme.read_key("https://a.com/3"), keys)
        self.assertEqual(len(pheme._read_order), len(pheme._read_set))

    def test_only_the_last_cap_entries_are_loaded_from_an_oversized_file(self):
        self._use_cap(2)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"read": ["k0", "k1", "k2"]}, f)
        pheme._load_read()
        self.assertEqual(pheme.read_keys(), {"k1", "k2"})

    def test_the_write_is_atomic_and_leaves_no_tmp_behind(self):
        pheme.mark_read("https://a.com/1")
        leftovers = [n for n in os.listdir(os.path.dirname(self.path))
                     if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class TestReadStateDegrades(_ReadStateTestCase):
    def test_a_missing_file_means_everything_is_unread(self):
        pheme._load_read()
        self.assertEqual(pheme.read_keys(), set())

    def test_corrupt_json_means_everything_is_unread_and_does_not_raise(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        pheme._load_read()
        self.assertEqual(pheme.read_keys(), set())

    def test_a_payload_that_is_not_a_list_is_ignored(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"read": {"nope": True}}, f)
        pheme._load_read()
        self.assertEqual(pheme.read_keys(), set())

    def test_non_string_entries_are_skipped_not_fatal(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"read": ["good", 7, None]}, f)
        pheme._load_read()
        self.assertEqual(pheme.read_keys(), {"good"})

    def test_an_unwritable_path_costs_persistence_not_the_render(self):
        with mock.patch.object(pheme, "_atomic_write_json",
                               side_effect=OSError("read-only")):
            pheme.mark_read("https://a.com/1")      # must not raise
        self.assertIn(pheme.read_key("https://a.com/1"), pheme.read_keys())


# ─────────────────────────────────────────────────────────────────────────────
#  7. Freshness (V0.03 audit T7) — the last good fan-out answers the Brain
# ─────────────────────────────────────────────────────────────────────────────

_T0 = 1000.0                            # arbitrary monotonic origin, patched clock
_WALL_T0 = datetime(2026, 9, 28, 9, 0)  # the wall clock at _T0


class _Feeds:
    """A counting _fetch_feed stand-in with a switch. `fetched` lists every
    feed id asked for, so a test counts fan-outs rather than handle() calls;
    `dead` is the offline laptop — every feed fails at once."""

    def __init__(self):
        self.fetched = []
        self.dead = False

    def __call__(self, feed):
        self.fetched.append(feed["id"])
        if self.dead:
            return {"error": "network_error"}
        return _fake_fetch_feed(feed)


class _CacheCase(_FreshCache):
    """Patched config, counted feeds, and both clocks: the monotonic one that
    ages the cache and the wall clock behind `as_of`, moved in step."""

    def setUp(self):
        super().setUp()
        self.feeds = _Feeds()
        self.config = mock.Mock(return_value=_TWO_FEEDS)
        self.clock = mock.Mock()
        self.clock.monotonic.return_value = _T0
        wall = mock.Mock(side_effect=lambda: _WALL_T0 + timedelta(
            seconds=self.clock.monotonic() - _T0))
        for name, new in (("_load_config", self.config), ("_fetch_feed", self.feeds),
                          ("time", self.clock), ("_now", wall)):
            patcher = mock.patch.object(pheme, name, new)
            patcher.start()
            self.addCleanup(patcher.stop)

    def at(self, t):
        self.clock.monotonic.return_value = t

    def _three_questions(self):
        return [pheme.handle(), pheme.handle(feed="a"), pheme.handle(feed="b")]


class TestNewsOneFetch(_CacheCase):

    def test_three_questions_make_one_fan_out(self):
        answers = self._three_questions()
        for out in answers:
            self.assertNotIn("error", out)
        self.assertEqual(sorted(self.feeds.fetched), ["a", "b"])  # each feed once
        self.assertEqual(len({out.get("as_of") for out in answers}), 1)

    def test_offline_the_questions_make_one_attempt(self):
        self.feeds.dead = True
        answers = self._three_questions()
        self.assertEqual([out.get("error") for out in answers],
                         ["all_feeds_failed"] * 3)
        self.assertEqual(sorted(self.feeds.fetched), ["a", "b"])

    def test_after_a_kairos_poll_the_questions_make_no_fetch(self):
        pheme.fetch()
        self._three_questions()
        self.assertEqual(len(self.feeds.fetched), 2)   # the poll's, nothing more

    def test_an_unknown_feed_costs_no_fetch(self):
        # Answerable from the config alone, so it must not touch the network.
        self.assertEqual(pheme.handle(feed="nope"), {"error": "unknown_feed: nope"})
        self.assertEqual(self.feeds.fetched, [])


class TestNewsTTL(_CacheCase):

    def test_a_snapshot_inside_the_ttl_is_reused(self):
        pheme.handle()
        self.at(_T0 + pheme.CACHE_TTL - 1)
        pheme.handle()
        self.assertEqual(len(self.feeds.fetched), 2)

    def test_a_snapshot_at_the_ttl_is_refetched(self):
        pheme.handle()
        self.at(_T0 + pheme.CACHE_TTL)
        pheme.handle()
        self.assertEqual(len(self.feeds.fetched), 4)

    def test_a_fresh_answer_is_not_marked_stale(self):
        # Both fresh paths: the answer that fetched, and the one the cache served.
        self.assertNotIn("stale", pheme.handle())
        self.at(_T0 + 1)
        self.assertNotIn("stale", pheme.handle())
        self.assertEqual(len(self.feeds.fetched), 2)

    def test_as_of_is_the_fetch_time_in_naive_local_iso(self):
        stamp = pheme.handle()["as_of"]
        self.assertEqual(stamp, "2026-09-28T09:00:00")
        self.assertIsNone(datetime.fromisoformat(stamp).tzinfo)

    def test_errors_carry_no_as_of(self):
        self.feeds.dead = True
        self.assertNotIn("as_of", pheme.handle())


class TestNewsKey(_CacheCase):

    def test_a_config_edit_refetches(self):
        pheme.handle()
        self.config.return_value = _TWO_FEEDS + [{"id": "c", "url": "u", "format": "rss"}]
        out = pheme.handle()
        self.assertEqual(len(self.feeds.fetched), 5)   # 2, then all 3 again
        self.assertIn("c0", [s["title"] for s in out.get("stories", [])])

    def test_a_failed_refresh_never_serves_another_configs_stories(self):
        pheme.handle()
        self.feeds.dead = True
        self.config.return_value = [{"id": "a", "url": "elsewhere", "format": "rss"}]
        self.assertEqual(pheme.handle(), {"error": "all_feeds_failed"})


class TestNewsStaleOnError(_CacheCase):

    def test_a_failed_refresh_serves_the_last_stories_marked_stale(self):
        first = pheme.handle()
        self.at(_T0 + pheme.CACHE_TTL + 1)
        self.feeds.dead = True
        out = pheme.handle()
        self.assertNotIn("error", out)
        self.assertIs(out.get("stale"), True)
        self.assertEqual(out["stories"], first["stories"])
        # as_of says when the stories were FETCHED; the failed attempt must not
        # move it (it would read 09:15:01 if it did).
        self.assertEqual(out["as_of"], "2026-09-28T09:00:00")

    def test_a_snapshot_past_stale_max_is_not_served(self):
        pheme.handle()
        self.at(_T0 + pheme._STALE_MAX)
        self.feeds.dead = True
        self.assertEqual(pheme.handle(), {"error": "all_feeds_failed"})

    def test_a_recent_failure_is_not_retried(self):
        self.feeds.dead = True
        pheme.handle()
        self.at(_T0 + pheme._RETRY_AFTER - 1)
        self.assertEqual(pheme.handle(), {"error": "all_feeds_failed"})
        self.assertEqual(len(self.feeds.fetched), 2)   # still just the one attempt

    def test_a_failure_is_retried_once_the_window_passes(self):
        self.feeds.dead = True
        pheme.handle()
        self.at(_T0 + pheme._RETRY_AFTER)
        self.feeds.dead = False
        self.assertNotIn("error", pheme.handle())


class TestNewsFetch(_CacheCase):

    def test_fetch_fans_out_even_when_the_cache_is_fresh(self):
        # fetch() IS the refresh: Kairos's poll must never answer from the cache.
        pheme.handle()
        pheme.fetch()
        self.assertEqual(len(self.feeds.fetched), 4)

    def test_an_all_failed_poll_keeps_the_last_snapshot(self):
        # The panel still gets every feed's error; the Brain keeps the last
        # stories that actually arrived instead of losing them to an outage.
        pheme.fetch()
        self.feeds.dead = True
        polled = pheme.fetch()
        self.assertEqual(polled["feeds"]["a"], {"error": "network_error"})
        self.at(_T0 + 5)
        out = pheme.handle()
        self.assertNotIn("error", out)
        self.assertNotIn("stale", out)               # still inside the TTL

    def test_fetch_output_is_unchanged_for_the_panel(self):
        self.assertEqual(set(pheme.fetch()), {"feeds"})

    def test_the_cache_never_shares_its_dict(self):
        # §6: the panel gets fetch()'s dict on the main thread; the cache must
        # hold its own copy, or Pythia's thread would share it.
        polled = pheme.fetch()
        polled["feeds"]["a"]["stories"][0]["title"] = "tampered"
        titles = [s["title"] for s in pheme.handle(feed="a")["stories"]]
        self.assertNotIn("tampered", titles)


class TestNewsSingleFlight(_FreshCache):
    """A handle() arriving while Kairos's fan-out is in flight waits for it
    rather than fanning out a second time."""

    def test_a_question_during_a_kairos_poll_waits_for_it(self):
        calls = []
        first_inside = threading.Event()
        release = threading.Event()
        second_inside = threading.Event()

        def slow_feed(feed):
            calls.append(feed["id"])
            if len(calls) == 1:
                first_inside.set()
                release.wait(5)
            else:
                second_inside.set()
            return _fake_fetch_feed(feed)

        one_feed = [{"id": "a", "url": "u", "format": "rss"}]
        answer = {}
        with mock.patch.object(pheme, "_load_config", return_value=one_feed), \
             mock.patch.object(pheme, "_fetch_feed", slow_feed):
            kairos = threading.Thread(target=pheme.fetch, daemon=True)
            kairos.start()
            self.assertTrue(first_inside.wait(5), "fetch() never reached a feed")
            pythia_side = threading.Thread(
                target=lambda: answer.update(out=pheme.handle()), daemon=True)
            pythia_side.start()
            fanned_out_twice = second_inside.wait(0.5)
            release.set()
            kairos.join(5)
            pythia_side.join(5)

        self.assertFalse(fanned_out_twice, "handle() fetched while fetch() was in flight")
        self.assertEqual(calls, ["a"])
        self.assertNotIn("error", answer["out"])


class TestNewsLockHasAnEnd(_FreshCache):
    """The single flight above is safe only because each feed's read is
    bounded. Here a real loopback "feed" sends its status line, then a header
    one byte every 0.1 s: a 0.3 s per-operation timeout never trips on that,
    and before hermes nothing else would have ended it. Now it ends at the
    feed's deadline, the healthy feed beside it is unharmed, and the cache
    lock is free again. (Plain http: hermes is allowed it for this test only,
    because a TLS server would need a certificate.)"""

    def test_a_dripping_feed_ends_at_its_deadline_and_frees_the_lock(self):
        def script(conn, stop):
            head = read_head(conn)
            if head.startswith(b"GET /good"):
                body = b"<rss><channel><item><title>t</title></item></channel></rss>"
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(body) + body)
                return
            conn.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
            drip(conn, stop, b"x" * 40, 0.1)

        real_get = hermes.get
        with Loopback(script) as srv:
            feeds = [{"id": "slow", "url": srv.url("/slow"), "format": "rss"},
                     {"id": "good", "url": srv.url("/good"), "format": "rss"}]
            # timeout 0.3 s and a 64 KiB cap make deadline_for() 0.66 s.
            with mock.patch.object(pheme, "_load_config", return_value=feeds), \
                    mock.patch.object(pheme, "_HTTP_TIMEOUT", 0.3), \
                    mock.patch.object(pheme, "_MAX_BYTES", 65536), \
                    mock.patch.object(pheme.hermes, "get",
                                      lambda *a, **k: real_get(*a, allow_http=True, **k)), \
                    self.assertLogs("METIS.hermes", "WARNING") as logs:
                t0 = time.monotonic()
                out = pheme.fetch()
                elapsed = time.monotonic() - t0
                free = pheme._lock.acquire(blocking=False)
                if free:
                    pheme._lock.release()

        self.assertEqual(out["feeds"]["slow"], {"error": "feed_timeout"})
        self.assertNotIn("error", out["feeds"]["good"])
        self.assertTrue(any("deadline" in line for line in logs.output))
        self.assertLess(elapsed, 0.66 + 1.5)       # one deadline (parallel), CI slack
        self.assertTrue(free, "the cache lock was still held after fetch() returned")


if __name__ == "__main__":
    unittest.main(verbosity=2)
