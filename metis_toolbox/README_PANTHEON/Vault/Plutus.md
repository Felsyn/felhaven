# Plutus — Holdings Ledger

*Anti-Legion: ONE JOB*

Plutus keeps a **manual, append-only record of stock buys and sells**, and
derives your net shares and cost invested from it. It shares the Dynastic Vault
card with [Midas](Midas.md), but the two do opposite jobs: Midas says what a
share is *worth today*; Plutus says what you *own and paid*. **Bookkeeping, not
market data.**

Its closest sibling isn't Midas — it's Scribe. Local CRUD over a JSON file. No
network, no API key, no Kairos worker, no live prices.

## The log is the truth

Plutus stores **nothing derived**. The data model is an append-only event log,
one record per transaction:

```json
{"date": "2026-01-05", "ticker": "MP", "action": "buy", "shares": 10, "price": 15.00}
```

Net shares, cost invested, totals, history — all **folded from the log** on
read, never stored. Nothing mutable is kept, so nothing can drift out of sync
with the events. The single write path is `add_event()`; everything else is a
read-only fold. Persistence is `plutus_ledger.json` (temp-then-replace so a crash
mid-write can't truncate it).

## The average-cost sell rule

This is the one accounting decision worth understanding. When you **sell**, cost
invested drops by the **average cost** of shares held at that moment — *not* the
sell price:

```
buy:  shares += s;  cost += s * price
sell: avg = cost / shares;  shares -= s;  cost -= s * avg
```

So "total invested" always means **"cost of what I still hold"**, and market
spikes/dips never leak into the bookkeeping. The sell *price* is still recorded on
the event for an honest history — it just doesn't distort the cost basis. A full
exit clamps float dust to a clean `0 / 0` and drops the ticker from the position
view (it lives on in history).

## Out of LLM scope — on purpose

Plutus has **no `TOOL_DEFINITION`, no `fetch()`** (the Emanon precedent, and here
the docstring is accurate). The reasoning is pointed: it mutates a ledger of
**real money**, so it must only ever change via a deliberate UI action — never an
LLM tool call. Pythia can tell you the *market* (Midas), but it cannot touch your
*book*. That's a safety boundary, not an oversight.

## Public surface (all read-only except add_event)

| Function | Returns |
|---|---|
| `add_event(ticker, action, shares, price)` | the created event; the only writer; raises `ValueError` on bad input |
| `positions()` | `{ticker: {shares, cost}}` for still-held positions |
| `totals()` | top-line `{shares, cost}` |
| `history()` | the scrolling record, newest-first, with per-event cost |

## Files

| File | Committed? | Purpose |
|---|---|---|
| `tools/plutus.py` | yes | The ledger — fold logic + the single write path. stdlib only. |
| `plutus_ledger.json` | **no** (runtime) | The event log; absent/unreadable → empty. |
| (renders in) `panels/midas_panel.py` | yes | The ledger UI inside the Dynastic Vault card. |

## Using it

**In the dashboard** — the ledger section of the **Dynastic Vault** card (behind
the Cerberus PIN): log a buy/sell, see positions and cost update.

**Standalone** (runs a sample buy/buy/sell/buy and prints the fold):

```
python tools/plutus.py
```

## Tests

Plutus has its **own** suite — `tests/test_plutus.py` pins the average-cost fold
and edge cases (over-selling, full exit):

```
python -X utf8 -m unittest tests.test_plutus
```
