# Callimachus — The Librarian of Alexandria

*Anti-Legion: ONE JOB*

Callimachus **asks the web a question and returns trimmed text**. Named for
Callimachus of Cyrene, librarian of Alexandria and author of the *Pinakes* — the
first catalog of all written works, i.e. the inventor of the search index.

It sits with the Cogitator's other reasoning tools ([Zeno](Zeno.md),
[Eudoxus](Eudoxus.md)) by kinship, not by panel: **Callimachus has no tab and no
panel.** It is a Pythia-only tool — the LLM reaches for it, the chat shows the
answer, and voice never touches it (see *Contract* below). Where Pheme is *push*
(feeds arriving on their own), Callimachus is *pull* — a question asked on demand.

## Two tools, one job — the agentic split

Callimachus is the toolbox's **first multi-tool module**: it exposes *two* LLM
tools instead of one, because a small quantized model (gemma4:e2b) can't be
handed whole web pages without drowning.

| Tool | Does | When the model should call it |
|---|---|---|
| **`search_web(query)`** | One Brave Search API call → **≤ 3** `{title, snippet, url}` results | For current, recent, or unknown facts — anything worth looking up rather than guessing |
| **`fetch_page(url)`** | Fetches one page → its **visible text**, scripts/styles stripped, ~4000 chars | Only *after* a search, on the **one** most promising `https` url |

That's the loop the split enables: search → read the snippets → **choose** one
result → fetch it, all inside a single Pythia turn — instead of firehosing full
pages at a 4-bit model sharing Obelisk's RAM. Trimming (3 results, short
snippets, a char budget) is what keeps the little model coherent.

## Stdlib only — no new dependencies

The Brave call is one JSON `GET` over stdlib **`urllib`**; the page stripper is a
stdlib **`html.parser`** subclass (drops `<script>`/`<style>`/`<head>`, keeps
visible text, collapses whitespace). **No `requests`, no BeautifulSoup** — same
zero-new-deps discipline as Morpheus shelling out to binaries. The provider is
Brave's free tier (~2k queries/mo); SearXNG on Obelisk is the documented
sovereignty upgrade for later, deliberately out of scope here.

## The key lives in Cerberus — and only there

The Brave API key is stored in the [Cerberus](../Moderati/Cerberus.md) Vault
under the entry name **`brave_api_key`**, read at call time and **never** cached
to disk, committed, or put in a config/env file. Seed it once:

```
python cerberus.py set <PIN> brave_api_key <your-brave-key>
```

Because `vault_get` needs an unlocked session, **web search only works in a
session where Cerberus was unlocked.** A locked vault degrades to a
`search_failed` error — a clean message, never a crash.

## `fetch_page` is defensive on purpose

- **https-only**, rejected *before* any network I/O — and a redirect that tries
  to leave https mid-flight is refused too (max 5 hops).
- **1 MB download cap** — oversize is a `too_large` error, not a silent partial
  (checked against `Content-Length` up front *and* while reading).
- Output truncated to the char budget with **`truncated: true`** set, so the
  model knows the page continues.

## Never raises

Both handlers always return a dict; every failure is a stable identifier first,
detail second: `no_results` · `rate_limited` (HTTP 429) · `timeout` ·
`search_failed: …` for search; `non_https_url` · `timeout` · `too_large` ·
`fetch_failed: …` for fetch. There is **no query history** (deferred, not
forgotten) and **no quota counter** (usage is low; the 429 path handles the rare
overrun).

## Contract

Exports the **plural `TOOL_DEFINITIONS`** (a list of two) plus one module
function named for each tool (`search_web`, `fetch_page`) — the multi-tool shape
(CONVENTIONS §2/§3). **No `fetch()`** — request-driven, not polled. **Wired into
`pythia.py` only**. (There was once a second, voice-side registry in
`metis_toolbox/__init__.py` that kept web search "off the table" for spoken
commands; it was retired with voice input, so Callimachus is now a Pythia tool
like the rest.) No panel — a documented non-decision in the module header so
nobody "fixes" the omission.

## Files

| File | Purpose |
|---|---|
| `tools/callimachus.py` | Brave client (`urllib`), the `html.parser` stripper, both handlers, and the plural `TOOL_DEFINITIONS`. stdlib only + `cerberus` for the key. |
| — | *No panel.* Pythia's chat is the only surface. |

## Using it

**Ask Pythia** — *"search the web for the latest on <thing>"* routes through
`search_web`; if she needs the full text she follows up with `fetch_page` on one
result. (Unlock Cerberus first, and make sure `brave_api_key` is seeded.)

**Standalone** (the HTML stripper always demos; a live search needs
`CERBERUS_PIN` set and the key seeded):

```
python tools/callimachus.py
```

## Tests

Its own hermetic suite — the network seam (`_OPENER.open`) and the key seam
(`_brave_key`) are mocked, so nothing touches the network or the Vault:

```
python -X utf8 -m unittest tests.test_callimachus
```
