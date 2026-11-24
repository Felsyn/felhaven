"""
midas.py — Touch of Gold / Market Prices
=========================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Fetch current price and daily % change for a watchlist.

Contract:    Polled + brain tool. Exposes TOOL_DEFINITION, TOOL_META,
             handle(), and fetch(); query_all() is the shared fetch both wrap,
             and load_watchlist() the watchlist the panel builds its rows and
             the LEDGER dropdown from.
             Returns price and % change, dated: {"tickers": [...], "as_of"},
             where as_of is when this set was fetched (naive local ISO, Aura's
             convention), and a ticker served from the last good fetch after
             its refresh failed carries `stale: True` and its own `as_of`.
             handle() never raises. fetch() raises when EVERY ticker failed
             (per §2, so Kairos delivers None) — with ONE exception: an
             all-'vault_locked' result is the normal pre-unlock state, not a
             fault, so fetch() returns it quietly (§12). A partial failure is
             embedded per-ticker instead.
             A set is reused for CACHE_TTL unless it holds a local refusal
             (vault_locked, no_key): that costs no network to check again,
             and the vault may have opened since.

Source:      Finnhub REST /quote (US equities, free tier), read through
             tools/hermes.py — no SDK. Watchlist lives in config/midas_watchlist.json under the
             app root (also the source for Plutus's ticker dropdown); only
             its non-empty strings count, each once.

Key:         The Finnhub key lives ONLY in the Cerberus Vault (the sole
             secrets authority) under the entry name 'finnhub_api_key' —
             mirrors Callimachus's Brave-key pattern exactly. Read at
             call-time via cerberus.vault_get(), never cached to disk or
             across the module lifetime (the session can lock/unlock
             repeatedly). No .env/env fallback: set it in its row of the
             CERBERUS tab, or with `python cerberus.py set finnhub_api_key`
             (hidden prompts).
             Because vault_get() needs an unlocked session, a locked vault
             degrades a fetch to ERR_VAULT_LOCKED (distinct from a vault
             that's unlocked but simply has no key stored, ERR_NO_KEY) —
             the panel shows a placeholder either way, never crashes.

Threads:     Kairos's worker calls fetch() and Pythia's calls handle(), so
             the cache is shared by those two; _lock guards reading and
             replacing it (not the network call: two misses at once fetch
             twice, and the later set wins).

Upstream:    kairos.py (calls fetch every 60 s), pythia.py (registration +
             dispatch)
Downstream:  cerberus.py (vault_get for the key), panels/midas_panel.py
             (display surface; also reads load_watchlist())

Requires:    tools/hermes.py (the bounded network read)
             json, logging, math, os, sys, threading, time, collections,
             concurrent.futures, datetime, typing (stdlib)
             Plus cerberus (app-root sibling) for the key.

2026-07-24:  walkthrough audit — stopped logging raw exceptions (the Finnhub
             key was leaking into logs/*.log via the request URL), made a
             locked vault a quiet non-fault instead of an ERROR every 60 s,
             dropped the unused stale_cache code, contract verified,
             fetch()/handle()/query_all() tests added.

2026-11-16:  Pythia tool audit (CONVENTIONS §12). The description no longer
             promises portfolio performance (the audit's M5) and follows §3's
             grammar. TOOL_META says markets / cached / changes nothing. Left
             to come: `as_of` (T7). A failed or empty fetch is the shared
             unavailable (T9, 2026-11-16).

V0.03:       Walked through 2026-11-22 (the V0.03 walkthrough, CONVENTIONS
             §3). Every answer carries `as_of`, and a stale ticker its own
             (T7, BACKLOG; routing probe run). A cached vault_locked was
             served for up to 60 s after the vault opened, with no network
             call (measured); a local refusal is now never reused. A price
             that isn't a finite number is no data (NaN read "$nan"). The
             watchlist keeps only its non-empty strings, once each: a dict
             in it raised from handle(), and a bare string fetched one
             letter at a time. load_watchlist() went public (the panel used
             the private one).
"""

import json
import logging
import math
import os
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

# Midas is a tool module that reaches UP to an app-root sibling (cerberus.py),
# not just a tools/ sibling. Normal operation (Pythia, tests, felhaven) already
# runs with the app root on sys.path; only a bare `python tools/midas.py`
# standalone run needs it added first. This block must precede `import
# cerberus` so it runs before the import resolves — the same top-of-module
# placement callimachus.py uses.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cerberus
from tools import hermes

log = logging.getLogger("METIS.midas")

# ── Error types ───────────────────────────────────────────────────────────────
# Distinct codes so the dashboard panel can display each state differently.
#   NO_DATA       — ticker exists but Finnhub returned all-zero fields
#                   (invalid symbol, market never traded)
#   FETCH_FAILED  — network error, timeout, 429 rate limit, unexpected exception
#   NO_KEY        — vault is unlocked but holds no 'finnhub_api_key' entry
#   VAULT_LOCKED  — Cerberus session isn't open, so the Vault can't be read
# (A cache hit older than CACHE_TTL is not an error — it rides back as a good
#  result carrying a `stale` flag, see query_all(); the panel dims it.)

ERR_NO_DATA      = "unavailable: no usable data"
ERR_FETCH_FAILED = "unavailable"
ERR_NO_KEY       = "no_key"
ERR_VAULT_LOCKED = "vault_locked"

# The one vault entry name Midas reads. See the Key: section above.
_VAULT_KEY_NAME = "finnhub_api_key"

# ── Config ────────────────────────────────────────────────────────────────────

# App root = one dir up from tools/ (next to felhaven.py). The watchlist lives
# here. Anchored to __file__, so cwd never matters.
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_QUOTE_URL = "https://finnhub.io/api/v1/quote"
_HTTP_TIMEOUT = 6
_MAX_BYTES = 64 * 1024   # measured 2026-10-01: a quote is 93 B (57 with no data); hermes' floor


def _finnhub_key() -> str:
    """Return the Finnhub API key from the Cerberus Vault. Requires an
    unlocked session; raises cerberus.VaultError if the vault is locked or the
    key is absent. Never cached to disk (mirrors callimachus._brave_key)."""
    return cerberus.vault_get(_VAULT_KEY_NAME)


# Watchlist config lives in config/ under the app root (CONVENTIONS §4).
_WATCHLIST_PATH = os.path.join(_APP_ROOT, "config", "midas_watchlist.json")


def load_watchlist() -> list[str]:
    """
    Read the ticker list from midas_watchlist.json: its non-empty strings, in
    order, each once. Returns [] on any failure (panel shows nothing; Emanon
    surfaces the log). Anything else in the list is left out and logged: a
    dict raised from query_all() (it can't be a cache key), and a bare string
    in place of the list was fetched one letter at a time.
    """
    try:
        with open(_WATCHLIST_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        log.error("Midas: failed to load watchlist: %s", e)
        return []
    tickers = raw.get("tickers") if isinstance(raw, dict) else None
    if not isinstance(tickers, list):
        log.error("Midas: the watchlist's 'tickers' is not a list")
        return []
    kept = list(dict.fromkeys(t.strip() for t in tickers
                              if isinstance(t, str) and t.strip()))
    if len(kept) != len(tickers):
        log.warning("Midas: %d watchlist entries left out (not a ticker, or repeated)",
                    len(tickers) - len(kept))
    return kept


# ── Internals ─────────────────────────────────────────────────────────────────

def _fmt_price(price: float) -> str:
    """Format price with commas, 2 decimal places."""
    if price >= 1_000:
        return f"${price:,.2f}"
    if price >= 1:
        return f"${price:.2f}"
    return f"${price:.4f}"


def _fetch_one(symbol: str) -> dict[str, Any]:
    """Fetch a single ticker from Finnhub /quote. Returns a result dict — never raises."""
    if not cerberus.is_unlocked():
        return {"symbol": symbol, "error": ERR_VAULT_LOCKED}
    try:
        key = _finnhub_key()
    except cerberus.VaultError:
        return {"symbol": symbol, "error": ERR_NO_KEY}

    try:
        resp = hermes.get(
            _QUOTE_URL,
            params={"symbol": symbol, "token": key},
            cap=_MAX_BYTES,
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        price = data.get("c", 0)   # current price
        prev  = data.get("pc", 0)  # previous close

        # Finnhub returns all-zero fields for invalid/empty symbols rather than
        # null or an error status. Zero previous-close also guards div-by-zero.
        # json reads NaN and Infinity, which would show as "$nan".
        if not (_usable_number(price) and _usable_number(prev)):
            return {"symbol": symbol, "error": ERR_NO_DATA}

        pct = (price - prev) / prev * 100

        return {
            "symbol":    symbol,
            "price":     price,
            "price_fmt": _fmt_price(price),
            "pct":       round(pct, 2),
            "pct_fmt":   f"{'+' if pct >= 0 else ''}{pct:.2f}%",
            "direction": "up" if pct >= 0 else "down",
        }
    except Exception as e:
        # Includes 429 rate limit (raise_for_status) — transient, falls back to
        # stale cache in query_all() and retries next tick.
        #
        # NEVER log the raw exception (§2): the request URL carries
        # `?...&token=<finnhub key>`, and an exception's message is the one
        # place a URL can ride along into logs/*.log (which Emanon renders on
        # screen). hermes keeps URLs out of its messages, but the rule doesn't
        # lean on that: log the class name and, for an HTTP error, its status
        # code — never the message.
        status = getattr(e, "status", None)
        detail = type(e).__name__ + (f" {status}" if status else "")
        log.warning("Midas: failed to fetch %s: %s", symbol, detail)
        return {"symbol": symbol, "error": ERR_FETCH_FAILED}


def _usable_number(value: Any) -> bool:
    """A real, finite, non-zero number (a bool is not one)."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value != 0)


# ── Cache ──────────────────────────────────────────────────────────────────────
# Prevents hammering Finnhub if the dashboard polls rapidly.
# CACHE_TTL is in seconds.  Set to 0 to disable caching.

CACHE_TTL = 60                      # seconds — don't re-fetch within this window
_MAX_WORKERS = 5                    # concurrent fetch threads

_cache: dict[str, dict[str, Any]] = {}                   # symbol -> result dict
_cache_ts: float = 0.0              # monotonic timestamp of last full fetch
_cache_as_of: str = ""              # when that fetch ran, as the answer says it
_lock = threading.Lock()            # guards the three above (Threads, above)

# Refusals decided here, with no network: worth checking again every time.
_LOCAL_ERRORS = (ERR_VAULT_LOCKED, ERR_NO_KEY)


def _now() -> datetime:
    """Injectable clock seam for `as_of`. Naive LOCAL time, Aura's convention."""
    return datetime.now()


# ── Public API ────────────────────────────────────────────────────────────────

def _query(force: bool = False) -> tuple[list[dict[str, Any]], str]:
    """query_all()'s tickers and the as_of they were fetched at."""
    global _cache, _cache_ts, _cache_as_of

    watchlist = load_watchlist()
    now = time.monotonic()
    with _lock:
        cache, cache_ts, cache_as_of = _cache, _cache_ts, _cache_as_of
    refusal = any(r.get("error") in _LOCAL_ERRORS for r in cache.values())
    if not force and cache and not refusal and (now - cache_ts) < CACHE_TTL:
        log.debug("Midas: serving from cache (%.1fs old)", now - cache_ts)
        return [cache[sym] for sym in watchlist if sym in cache], cache_as_of

    # ── Concurrent fetch ──────────────────────────────────────────────────
    as_of = _now().isoformat(timespec="seconds")
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_one, sym): sym for sym in watchlist}
        for future in as_completed(futures):
            sym = futures[future]
            result = future.result()          # _fetch_one never raises

            # On failure, fall back to stale cache if available. It keeps the
            # as_of it was fetched at, since that is when the price is from.
            if "error" in result and sym in cache and "error" not in cache[sym]:
                stale = dict(cache[sym])
                stale["stale"] = True
                stale.setdefault("as_of", cache_as_of)
                results[sym] = stale
                log.debug("Midas: %s fetch failed, serving stale cache", sym)
            else:
                results[sym] = result

    with _lock:
        _cache, _cache_ts, _cache_as_of = results, now, as_of

    # Return in watchlist order (futures finish in arbitrary order)
    return [results[sym] for sym in watchlist if sym in results], as_of


def query_all(force: bool = False) -> list[dict[str, Any]]:
    """
    Fetch all watchlist tickers, concurrently.

    Returns a list of result dicts — one per ticker.
    Failures are included as error dicts so the panel can show a placeholder.

    Results are cached for CACHE_TTL seconds.  Pass force=True to bypass. A
    cached vault_locked or no_key is never reused (Contract).
    If the cache is stale but a fresh fetch fails for a symbol, the stale
    value is returned with a 'stale' flag and its own `as_of`, so the panel
    can dim it rather than blank it out.
    """
    return _query(force)[0]


# ── Metis contract ────────────────────────────────────────────────────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "get_market_prices",
        "description": (
            "Returns current price and daily percentage change for the Felhaven watchlist. "
            "Call when the user asks about stocks, market prices, "
            "or any ticker by name. It lists watched symbols only: it does not "
            "know what the user owns, so it cannot answer questions about "
            "their holdings or portfolio."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

# What the code reads about each tool here; the model never sees it
# (CONVENTIONS §8, "TOOL_META"). network: none = local only, cached = a poll
# or TTL cache, live = may reach the network on every call. mutates = it
# changes state outside the turn.
TOOL_META: dict[str, dict[str, Any]] = {
    "get_market_prices": {"domain": "markets", "network": "cached", "mutates": False},
}


def handle() -> dict[str, Any]:
    """Called by Pythia's dispatcher when the model invokes get_market_prices.
    The tickers, dated with `as_of` (Contract). Never raises."""
    tickers, as_of = _query()
    return {"tickers": tickers, "as_of": as_of}


def fetch() -> dict[str, Any]:
    """Kairos entry point. Per-ticker errors ride back inside the returned dict;
    fetch() raises only on WHOLESALE failure, so Kairos delivers None and the
    panel holds its last state (§2).

    One deliberate exception (§12): if EVERY ticker failed only because the
    Cerberus vault is locked, fetch() does NOT raise. A sealed vault is the
    normal pre-unlock state — the panel starts sealed and Kairos polls it
    regardless — not a fault a human should be paged about. Raising it logged
    an ERROR every 60 s and made Emanon read Midas as failed. Genuine failures
    (no_key, unavailable) still raise, naming the dominant cause so
    the log line says *why* instead of a bare "all tickers failed"."""
    results, as_of = _query()
    if results and all("error" in r for r in results):
        errors = [r["error"] for r in results]
        if all(e == ERR_VAULT_LOCKED for e in errors):
            return {"tickers": results, "as_of": as_of}   # expected sealed state
        dominant = Counter(errors).most_common(1)[0][0]
        raise RuntimeError(f"all tickers failed: {dominant}")
    return {"tickers": results, "as_of": as_of}


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Live prices only work if Cerberus is unlocked and holds the Finnhub key.
    # Unlock here from an env PIN purely for a manual smoke test; normal
    # callers never do this — the panel relies on an already-unlocked session.
    pin = os.environ.get("CERBERUS_PIN")
    if pin and not cerberus.unlock(pin):
        print("[Midas] CERBERUS_PIN set but wrong — running locked (ERR_VAULT_LOCKED).")
    elif not pin:
        print("[Midas] set CERBERUS_PIN (and seed 'finnhub_api_key' in the "
              "vault) to smoke-test live prices; running locked otherwise.")

    results = query_all()
    for r in results:
        if "error" in r:
            print(f"[Midas] {r['symbol']:>8}  —  {r['error']}")
        else:
            arrow = "▲" if r["direction"] == "up" else "▼"
            stale = " (stale)" if r.get("stale") else ""
            print(f"[Midas] {r['symbol']:>8}  {r['price_fmt']:>12}  {arrow} {r['pct_fmt']}{stale}")
