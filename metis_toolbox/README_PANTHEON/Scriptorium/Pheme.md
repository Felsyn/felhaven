# Pheme — The Rumor Mill

*Anti-Legion: ONE JOB*

Pheme (goddess of rumor and report) fetches the configured **news feeds** and
hands their stories to the panel. It's the coral **Scriptorium — rumor mill**
card: tech, world, and local headlines aggregated from RSS/Atom sources.

## Config over code

Feeds live in `pheme_rumormill.json` at the app root — one row per source
(`{id, label, url, format}`). **Adding or removing a feed is a JSON edit, never a
code change.** The parser branches on the `format` field: RSS 2.0 (`<item>`) vs
Atom (`<entry>`), two format-specific parsers behind one normalizer.

> A standing gotcha (worth not "fixing"): The Register's feed is served as **RSS
> despite its `.atom` URL** — its config row is correctly marked `"rss"`. Don't
> switch it to `atom` on the assumption the URL knows best.

## One story shape, many messy sources

Every feed, whatever its format, normalizes to the same dict:

```python
{"title": str, "url": str, "author": str, "date": str, "domain": str}
```

The normalizers absorb the real-world grime: `_clean_author` strips "By "
bylines, `_short_date` reduces a dozen RSS/Atom date formats to "Jun 7" (and
normalizes named zones like `GMT`/`UTC` that `%z` can't parse), `_domain` bares
the hostname, and `html.unescape` fixes entity-encoded titles. A field that can't
be parsed just becomes `""` — never an error.

## Two surfaces, two shapes

| Entry | Caller | Returns |
|---|---|---|
| `fetch()` | Kairos | `{"feeds": {id: {"stories": [...]} \| {"error": str}}}` — **per-feed**, every id present |
| `handle(feed=None)` | LLM (`get_news_stories`) | `{"stories": [...]}` — **flat**, interleaved, capped at 10 |

The panel wants stories grouped by source; the LLM wants a flat cross-section. So
`handle()` **round-robin interleaves** (`_interleave`) — one story from each feed,
then the next from each — so a single chatty feed doesn't dominate the model's
view. The optional `feed` arg narrows to one source, and its **enum is built from
the live config** at import, so the tool's allowed values can never drift from the
actual feeds.

## Failure isolation

Three layers of "one dead feed can't poison the rest":

- **`_fetch_feed()` never raises** — a failed source yields `{"error": "HTTP 404"}`
  scoped to its id alone.
- Feeds are fetched **concurrently** (`ThreadPoolExecutor`, up to 8).
- **`fetch()` raises only if the config itself is unreadable** — then Kairos
  delivers `None` and the panel holds stale. Individual feed errors are embedded,
  not raised.

A browser-plausible User-Agent is sent so picky edges (e.g. TownNews) don't 403.

## Files

| File | Purpose |
|---|---|
| `tools/pheme.py` | The fetch + dual-format parse + normalize. |
| `pheme_rumormill.json` | The feed list (id/label/url/format) at the app root. |
| `panels/pheme_panel.py` → `PhemePanel` | The **Scriptorium** card. |

Registered with Kairos under `pheme`. stdlib XML (`xml.etree`) — no feedparser
dependency.

## Using it

**In the dashboard** — the **Scriptorium** card.

**Ask Pythia** — *"what's the news?"* / *"any tech headlines?"* routes through
`get_news_stories` (pass a feed id to narrow it).

**Standalone** (shows both the per-feed and flat views):

```
python tools/pheme.py
```

## Tests

Covered by the shared handle suite (`requests` mocked with sample feed XML):

```
python -X utf8 -m unittest tests.test_tool_handles
```
