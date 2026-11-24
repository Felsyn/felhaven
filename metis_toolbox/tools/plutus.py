"""
plutus.py — Holdings Ledger
============================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Track stock buys and sells; derive shares held and cost invested.
             A manual, append-only record — bookkeeping, not market data.

             This is BOOKKEEPING, not market data. Plutus has no network, no
             API key, no Kairos worker, no live prices. It stores what you own;
             what it's worth today is Midas's job, not Plutus's. Closest sibling
             is Scribe (local CRUD), not Midas.

Contract:    Local-only ledger / persistence (§2): plain functions. No
             fetch(), no TOOL_DEFINITION — Plutus is intentionally out of LLM
             scope (Emanon precedent): it mutates a ledger of real money and
             should only ever change via deliberate UI action, never an LLM
             tool call.
             read_events() raises LedgerError (logged) when the file is there
             but can't be read; load_events() is the same read that never
             raises (absent or unreadable reads as []).
             add_event() raises ValueError on bad input, LedgerError when the
             file can't be read (nothing is written: one ADD on a garbled
             file used to replace its whole history), and OSError when the
             write fails (logged here first). positions(), totals() and
             history() skip a record they can't read; unreadable() counts
             them, and add_event() keeps them in the file.

Data model:  Append-only event log, one record per buy/sell:
                 {
                   "date":   "YYYY-MM-DD",   # auto today's date at entry time
                   "ticker": "MP",
                   "action": "buy" | "sell",
                   "shares": 10.0,
                   "price":  18.50           # price paid/received per share
                 }
             Everything else (net shares, cost invested, totals) is DERIVED by
             folding the log. Nothing mutable is stored — the log is the truth.

Persistence: plutus_ledger.json at the app root. {"events": [...]}.
             Written temp-then-replace. Read and written only on the Tk thread
             (the LEDGER tab), so Themis's lock isn't needed (§12, 2026-11-21).

Sell rule:   Average-cost. Selling reduces cost invested proportionally to the
             average cost of shares held at that moment — NOT the sell price.
             This keeps "total invested" meaning "cost of what I still hold" and
             keeps market spikes/dips out of the bookkeeping. The sell price is
             still recorded on the event for an honest history.

Upstream:    panels/midas_panel.py (the LEDGER tab)
Downstream:  plutus_ledger.json

Requires:    json, logging, math, os, datetime, typing (stdlib). No
             third-party deps.

2026-07-24:  walkthrough audit — average-cost fold, atomic write, and the
             deliberate out-of-LLM-scope omission all verified honest; the
             module needed no code change, only the marker.

V0.03:       Walked through 2026-11-22 (the V0.03 walkthrough, CONVENTIONS
             §3; born 2026-06-10). One ADD on a ledger it couldn't read wrote
             a new file of that one event: 40 records became 1 (measured).
             add_event() now refuses with LedgerError. "nan" and "inf" were
             accepted, and a NaN made that ticker vanish from the holdings
             for good; shares and price must be finite. A record the folds
             can't read is skipped and counted, not raised (the LEDGER tab
             stopped drawing). A failed write is logged.
"""

import json
import logging
import math
import os
from datetime import date
from typing import Any

log = logging.getLogger("METIS.plutus")

# Ledger lives at the app root, next to felhaven.py. plutus.py sits in tools/.
_LEDGER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "plutus_ledger.json",
)


class LedgerError(Exception):
    """The ledger file is there but can't be read, so it isn't written over."""


# ── Persistence ───────────────────────────────────────────────────────────────

def read_events() -> list[Any]:
    """The file's event list as it stands, every record readable or not (the
    folds skip the rest); [] when there is no file. Raises LedgerError, after
    logging it, when there is one that can't be read or isn't a ledger."""
    try:
        with open(_LEDGER_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError, RecursionError) as e:
        # ValueError: not JSON, or not UTF-8. RecursionError: nested too deep.
        err = LedgerError(f"the ledger file can't be read ({type(e).__name__})")
        log.error("Plutus: %s", err)
        raise err from e
    events = raw.get("events", []) if isinstance(raw, dict) else None
    if not isinstance(events, list):
        err = LedgerError("the ledger file isn't a ledger")
        log.error("Plutus: %s", err)
        raise err
    return events


def load_events() -> list[dict[str, Any]]:
    """read_events(), but [] when the file can't be read. Never raises."""
    try:
        return read_events()
    except LedgerError:
        return []


def _readable(ev: Any) -> bool:
    """Whether the folds can use a record: a buy or sell of a named ticker,
    a finite positive share count and a finite price of at least zero."""
    if not isinstance(ev, dict):
        return False
    if not isinstance(ev.get("ticker"), str) or not ev["ticker"]:
        return False
    if ev.get("action") not in ("buy", "sell") or not isinstance(ev.get("date"), str):
        return False
    shares: Any = ev.get("shares")
    price: Any = ev.get("price")
    return _finite(shares) and _finite(price) and shares > 0 and price >= 0


def _finite(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def unreadable(events: list[Any] | None = None) -> int:
    """How many records the folds skip."""
    if events is None:
        events = load_events()
    return sum(1 for ev in events if not _readable(ev))


def _save_events(events: list[Any]) -> None:
    """Write the event log. Raises on failure so the caller can surface it.
    Temp-then-replace so a crash mid-write can't truncate it."""
    tmp = _LEDGER_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"events": events}, f, indent=2)
    os.replace(tmp, _LEDGER_PATH)


# ── Mutation (the only write path) ────────────────────────────────────────────

def add_event(ticker: str, action: str, shares: float, price: float) -> dict[str, Any]:
    """
    Append one buy/sell event, dated today, and persist.
    Returns the created event dict. Raises ValueError on bad input,
    LedgerError when the ledger can't be read (nothing written), OSError
    when the write fails (Contract).
    """
    ticker = (ticker or "").strip().upper()
    action = (action or "").strip().lower()

    if not ticker:
        raise ValueError("ticker required")
    if action not in ("buy", "sell"):
        raise ValueError("action must be 'buy' or 'sell'")
    if not _finite(shares) or shares <= 0:
        raise ValueError("shares must be a number > 0")
    if not _finite(price) or price < 0:
        raise ValueError("price must be a number >= 0")

    event = {
        "date":   date.today().isoformat(),
        "ticker": ticker,
        "action": action,
        "shares": float(shares),
        "price":  float(price),
    }
    events = read_events()       # raises rather than write over what it can't read
    events.append(event)
    try:
        _save_events(events)
    except OSError as e:
        log.error("Plutus: could not save the ledger: %s", e)
        raise
    return event


# ── Derivation (read-only folds over the log) ─────────────────────────────────

def positions(events: list[dict[str, Any]] | None = None) -> dict[str, dict[str, float]]:
    """
    Fold the event log into per-ticker {shares, cost} using the average-cost
    rule for sells. Returns {ticker: {"shares": float, "cost": float}} for
    tickers with a non-trivial remaining position. Skips a record it can't
    read (unreadable() counts them).

    Buy:  shares += s; cost += s * price
    Sell: avg = cost / shares (if shares > 0); shares -= s; cost -= s * avg
          (cost reduced at average cost, not sell price)
    """
    if events is None:
        events = load_events()

    acc: dict[str, dict[str, float]] = {}   # ticker -> {"shares": float, "cost": float}
    for ev in events:
        if not _readable(ev):
            continue
        t = ev["ticker"]
        s = float(ev["shares"])
        p = float(ev["price"])
        pos = acc.setdefault(t, {"shares": 0.0, "cost": 0.0})

        if ev["action"] == "buy":
            pos["shares"] += s
            pos["cost"]   += s * p
        else:  # sell — reduce at average cost
            if pos["shares"] > 0:
                avg = pos["cost"] / pos["shares"]
                sold = min(s, pos["shares"])      # never go negative on a sell
                pos["shares"] -= sold
                pos["cost"]   -= sold * avg
            # selling with no held shares is a no-op on cost (logged anyway)

        # Clamp tiny float dust to zero so a full exit lands cleanly at 0/0.
        if abs(pos["shares"]) < 1e-9:
            pos["shares"] = 0.0
            pos["cost"]   = 0.0

    # Drop fully-exited tickers from the position view (they live on in history).
    return {t: v for t, v in acc.items() if v["shares"] > 0}


def totals(events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    Top-line summary across all positions.
    Returns {"shares": float, "cost": float} — both 0.0 when empty.
    """
    pos = positions(events)
    return {
        "shares": sum(v["shares"] for v in pos.values()),
        "cost":   sum(v["cost"]   for v in pos.values()),
    }


def history(events: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """
    The scrolling record, newest-first. Each row carries the per-event cost
    (shares * price) so the panel doesn't recompute it. Skips a record it
    can't read.
    Returns a list of:
        {date, ticker, action, shares, price, cost}
    """
    if events is None:
        events = load_events()
    rows = []
    for ev in events:
        if not _readable(ev):
            continue
        rows.append({
            "date":   ev["date"],
            "ticker": ev["ticker"],
            "action": ev["action"],
            "shares": float(ev["shares"]),
            "price":  float(ev["price"]),
            "cost":   float(ev["shares"]) * float(ev["price"]),
        })
    rows.reverse()   # newest first
    return rows


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    evs = [
        {"date": "2026-01-05", "ticker": "MP",  "action": "buy",  "shares": 10, "price": 15.00},
        {"date": "2026-02-05", "ticker": "MP",  "action": "buy",  "shares": 10, "price": 25.00},
        {"date": "2026-03-05", "ticker": "MP",  "action": "sell", "shares": 5,  "price": 40.00},
        {"date": "2026-03-06", "ticker": "CAT", "action": "buy",  "shares": 2,  "price": 300.00},
    ]
    print("positions:", positions(evs))
    print("totals:   ", totals(evs))
    for r in history(evs):
        print(f"  {r['date']}  {r['ticker']:>5}  {r['action']:>4}  {r['shares']:>6}sh  ${r['cost']:.2f}")
