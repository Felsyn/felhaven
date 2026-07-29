"""
test_pheme.py — unit tests for tools/pheme.py (RSS/Atom news aggregator).

Hermetic: the single network seam (pheme.requests.get) and the config seam
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
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from tools import pheme


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
    """Stand-in for requests.get's return: .content + .raise_for_status()."""
    r = mock.Mock()
    r.content = content
    if status is None:
        r.raise_for_status.return_value = None
    else:
        r.status_code = status
        r.raise_for_status.side_effect = requests.HTTPError(response=r)
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
        with mock.patch.object(pheme.requests, "get",
                               return_value=_resp(b"", status=404)):
            self.assertEqual(pheme._fetch_feed(_FEED),
                             {"error": "http_error: 404"})

    def test_http_failure_without_a_response_object(self):
        err = requests.HTTPError()          # e.response is None
        r = mock.Mock()
        r.raise_for_status.side_effect = err
        with mock.patch.object(pheme.requests, "get", return_value=r):
            self.assertEqual(pheme._fetch_feed(_FEED),
                             {"error": "http_error: ?"})

    def test_timeout(self):
        with mock.patch.object(pheme.requests, "get",
                               side_effect=requests.Timeout("6s elapsed")):
            self.assertEqual(pheme._fetch_feed(_FEED),
                             {"error": "feed_timeout"})

    def test_connection_failure(self):
        with mock.patch.object(pheme.requests, "get",
                               side_effect=requests.ConnectionError("refused")):
            self.assertEqual(pheme._fetch_feed(_FEED),
                             {"error": "network_error"})

    def test_unparseable_xml(self):
        with mock.patch.object(pheme.requests, "get",
                               return_value=_resp(b"<html>not a feed")):
            self.assertEqual(pheme._fetch_feed(_FEED),
                             {"error": "invalid_feed_xml"})

    def test_anything_else_reports_the_class_name_never_the_message(self):
        # A config row without "url" — the KeyError message would be fine, but
        # the rule is uniform: class name only (§2), because the same handler
        # catches exceptions whose messages carry paths and URLs.
        with mock.patch.object(pheme.requests, "get",
                               side_effect=AssertionError("must not be called")):
            self.assertEqual(pheme._fetch_feed({"id": "bad"}),
                             {"error": "fetch_failed: KeyError"})


class TestFetchFeedSuccess(unittest.TestCase):
    def test_a_healthy_feed_parses_and_is_capped(self):
        items = "".join(
            f"<item><title>t{n}</title><link>https://e.com/{n}</link></item>"
            for n in range(pheme._MAX_STORIES + 2))
        xml = f"<rss><channel>{items}</channel></rss>".encode()
        with mock.patch.object(pheme.requests, "get", return_value=_resp(xml)):
            result = pheme._fetch_feed(_FEED)
        self.assertNotIn("error", result)
        self.assertEqual(len(result["stories"]), pheme._MAX_STORIES)

    def test_the_format_field_selects_the_atom_parser(self):
        with mock.patch.object(pheme.requests, "get",
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
    return {"title": f"{fid}{n}", "url": "", "author": "", "date": "",
            "domain": ""}


def _fake_fetch_feed(feed):
    """Deterministic stand-in keyed on feed id: 'dead' errors, others yield
    two stories apiece."""
    if feed["id"] == "dead":
        return {"error": "feed_timeout"}
    return {"stories": [_story_n(feed["id"], n) for n in range(2)]}


_TWO_FEEDS = [{"id": "a", "url": "u", "format": "rss"},
              {"id": "b", "url": "u", "format": "rss"}]


class TestHandle(unittest.TestCase):
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
        # The one case that legitimately stays {"stories": []}.
        with mock.patch.object(pheme, "_load_config", return_value=_TWO_FEEDS), \
             mock.patch.object(pheme, "_fetch_feed",
                               lambda f: {"stories": []}):
            self.assertEqual(pheme.handle(), {"stories": []})


class TestFetch(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
