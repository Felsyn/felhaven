# Midas — Touch of Gold

*Anti-Legion: ONE JOB*

Midas fetches **current price and daily % change** for a watchlist — nothing
more. It's the market-data half of the amber **Dynastic Vault** card (the
bookkeeping half is [Plutus](Plutus.md)). Prices in, no opinions, no advice.

## Source & key

- **Finnhub REST `/quote`** (US equities, free tier), plain `requests`, no SDK.
- Watchlist is `midas_watchlist.json` at the app root (also feeds Plutus's ticker
  dropdown — one list, two readers).
- **`FINNHUB_API_KEY`** is read once at import from the OS environment *or* a
  `.env` at the app root, via a tiny hand-rolled `.env` loader (no
  `python-dotenv` — the stack stays flash-drive-portable). A real OS env var
  wins over the file if both are set. **Never commit the key or the `.env`.**

Missing key → every ticker returns `ERR_NO_KEY` and the panel shows a
placeholder. It never crashes for lack of a key.

## Four honest error states

Distinct codes so the panel can render each differently rather than a generic
"error":

| Code | Meaning |
|---|---|
| `no_key` | `FINNHUB_API_KEY` not set |
| `no_data` | ticker exists but Finnhub returned all-zero fields (bad symbol) |
| `fetch_failed` | network error, timeout, or 429 rate limit |
| `stale_cache` | a cached value served past its TTL — usable, just old |

Finnhub returns *all-zero* fields (not null, not an error status) for invalid
symbols, so Midas treats "no price or no prev-close" as `no_data` — which also
guards the div-by-zero in the % change.

## Cache + concurrency + stale fallback

Three moves keep it fast and polite to the free tier:

1. **60 s cache** (`CACHE_TTL`) — rapid dashboard polls serve from cache, not
   from Finnhub. `force=True` bypasses.
2. **Concurrent fetch** — up to 5 tickers in parallel via `ThreadPoolExecutor`,
   then re-ordered back to watchlist order (futures finish arbitrarily).
3. **Stale fallback** — if a fresh fetch fails but a good cached value exists,
   the cached value is returned flagged `stale` so the panel **dims** it rather
   than blanking it. A transient 429 doesn't wipe the display.

## Contract — three surfaces on failure policy

| Entry | Caller | On total failure |
|---|---|---|
| `handle()` (`get_market_prices`) | LLM | returns per-ticker error dicts — never raises |
| `query_all()` | panel (direct) | returns list with embedded errors |
| `fetch()` | Kairos | **raises** only if *every* ticker failed → panel holds stale |

Same two-surface split as Ammit/Argus — the LLM and the widget want opposite
things when the feed breaks.

## Sealed behind Cerberus

The Dynastic Vault panel is the first consumer to sit **entirely behind the
[Cerberus](../Moderati/Cerberus.md) PIN** (the alarm-red gate) — the proof that
"everyone defers to Cerberus" for secrets. Midas's own Finnhub key, though, still
comes from `.env`/env, not the Vault (that migration is a future step noted in
Cerberus's README).

## Files

| File | Committed? | Purpose |
|---|---|---|
| `tools/midas.py` | yes | The Finnhub fetch, cache, contract. |
| `midas_watchlist.json` | yes | The ticker list (shared with Plutus). |
| `.env` | **no** (gitignored) | Holds `FINNHUB_API_KEY`. |
| `panels/midas_panel.py` → `MidasPanel` | yes | The **Dynastic Vault** card (hosts the Plutus ledger UI). |

## Using it

**In the dashboard** — the **Dynastic Vault** card (behind the Cerberus PIN).

**Ask Pythia** — *"how's the market?"* / *"what's MP trading at?"* routes through
`get_market_prices`.

**Standalone**:

```
python tools/midas.py
```

## Tests

Covered by the shared handle suite (`requests` mocked):

```
python -X utf8 -m unittest tests.test_tool_handles tests.test_midas_panel_smoke
```
