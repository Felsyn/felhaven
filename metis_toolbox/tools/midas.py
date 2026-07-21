"""
midas.py — Touch of Gold / Market Prices
=========================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Fetch current price and daily % change for a watchlist.

Contract:    Polled + brain tool. Exposes TOOL_DEFINITION, handle(), and
             fetch(); also query_all() for direct dashboard use.
             Returns price and % change. Nothing more.
             handle() never raises. fetch() raises only when EVERY ticker
             failed (per §2, so Kairos delivers None); a partial failure is
             embedded per-ticker instead.

Source:      Finnhub REST /quote (US equities, free tier). Plain `requests` —
             no SDK. Watchlist lives in config/midas_watchlist.json under the
             app root (also the source for Plutus's ticker dropdown).

Key:         The Finnhub key lives ONLY in the Cerberus Vault (the sole
             secrets authority) under the entry name 'finnhub_api_key' —
             mirrors Callimachus's Brave-key pattern exactly. Read at
             call-time via cerberus.vault_get(), never cached to disk or
             across the module lifetime (the session can lock/unlock
             repeatedly). No .env/env fallback: seed it once with
             `python cerberus.py set <PIN> finnhub_api_key <key>`.
             Because vault_get() needs an unlocked session, a locked vault
             degrades a fetch to ERR_VAULT_LOCKED (distinct from a vault
             that's unlocked but simply has no key stored, ERR_NO_KEY) —
             the panel shows a placeholder either way, never crashes.

Upstream:    kairos.py (calls fetch), pythia.py (registration + dispatch)
Downstream:  cerberus.py (vault_get for the key), panels/midas_panel.py
             (display surface)

Requires:    requests (already in Felhaven stack)
             os, sys, json, time, concurrent.futures (stdlib)
             Plus cerberus (app-root sibling) for the key.
"""

import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

# Midas is a tool module that reaches UP to an app-root sibling (cerberus.py),
# not just a tools/ sibling. Normal operation (Pythia, tests, felhaven) already
# runs with the app root on sys.path; only a bare `python tools/midas.py`
# standalone run needs it added first. This block must precede `import
# cerberus` so it runs before the import resolves — the same top-of-module
# placement callimachus.py uses.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cerberus

log = logging.getLogger("METIS.midas")

# ── Error types ───────────────────────────────────────────────────────────────
# Distinct codes so the dashboard panel can display each state differently.
#   NO_DATA       — ticker exists but Finnhub returned all-zero fields
#                   (invalid symbol, market never traded)
#   FETCH_FAILED  — network error, timeout, 429 rate limit, unexpected exception
#   STALE_CACHE   — cache hit, but older than CACHE_TTL (informational, usable)
#   NO_KEY        — vault is unlocked but holds no 'finnhub_api_key' entry
#   VAULT_LOCKED  — Cerberus session isn't open, so the Vault can't be read

ERR_NO_DATA      = "no_data"
ERR_FETCH_FAILED = "fetch_failed"
ERR_STALE_CACHE  = "stale_cache"
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


def _finnhub_key() -> str:
    """Return the Finnhub API key from the Cerberus Vault. Requires an
    unlocked session; raises cerberus.VaultError if the vault is locked or the
    key is absent. Never cached to disk (mirrors callimachus._brave_key)."""
    return cerberus.vault_get(_VAULT_KEY_NAME)


# Watchlist config lives in config/ under the app root (CONVENTIONS §4).
_WATCHLIST_PATH = os.path.join(_APP_ROOT, "config", "midas_watchlist.json")


def _load_watchlist() -> list[str]:
    """
    Read the ticker list from midas_watchlist.json.
    Returns [] on any failure (panel shows nothing; Emanon surfaces the log).
    """
    try:
        with open(_WATCHLIST_PATH, "r", encoding="utf-8") as f:
            tickers: list[str] = json.load(f).get("tickers", [])
            return tickers
    except Exception as e:
        log.error(f"Midas: failed to load watchlist: {e}")
        return []


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
        resp = requests.get(
            _QUOTE_URL,
            params={"symbol": symbol, "token": key},
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        price = data.get("c", 0)   # current price
        prev  = data.get("pc", 0)  # previous close

        # Finnhub returns all-zero fields for invalid/empty symbols rather than
        # null or an error status. Zero previous-close also guards div-by-zero.
        if not price or not prev:
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
        log.warning(f"Midas: failed to fetch {symbol}: {e}")
        return {"symbol": symbol, "error": ERR_FETCH_FAILED}


# ── Cache ──────────────────────────────────────────────────────────────────────
# Prevents hammering Finnhub if the dashboard polls rapidly.
# CACHE_TTL is in seconds.  Set to 0 to disable caching.

CACHE_TTL = 60                      # seconds — don't re-fetch within this window
_MAX_WORKERS = 5                    # concurrent fetch threads

_cache: dict[str, dict[str, Any]] = {}                   # symbol -> result dict
_cache_ts: float = 0.0              # monotonic timestamp of last full fetch


# ── Public API ────────────────────────────────────────────────────────────────

def query_all(force: bool = False) -> list[dict[str, Any]]:
    """
    Fetch all watchlist tickers, concurrently.

    Returns a list of result dicts — one per ticker.
    Failures are included as error dicts so the panel can show a placeholder.

    Results are cached for CACHE_TTL seconds.  Pass force=True to bypass.
    If the cache is stale but a fresh fetch fails for a symbol, the stale
    value is returned with a 'stale' flag so the panel can dim it rather
    than blank it out.
    """
    global _cache, _cache_ts

    watchlist = _load_watchlist()
    now = time.monotonic()
    if not force and _cache and (now - _cache_ts) < CACHE_TTL:
        log.debug("Midas: serving from cache (%.1fs old)", now - _cache_ts)
        return [_cache[sym] for sym in watchlist if sym in _cache]

    # ── Concurrent fetch ──────────────────────────────────────────────────
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_one, sym): sym for sym in watchlist}
        for future in as_completed(futures):
            sym = futures[future]
            result = future.result()          # _fetch_one never raises

            # On failure, fall back to stale cache if available
            if "error" in result and sym in _cache and "error" not in _cache[sym]:
                stale = dict(_cache[sym])
                stale["stale"] = True
                stale["_original_error"] = result["error"]
                results[sym] = stale
                log.debug("Midas: %s fetch failed, serving stale cache", sym)
            else:
                results[sym] = result

    _cache = results
    _cache_ts = now

    # Return in watchlist order (futures finish in arbitrary order)
    return [results[sym] for sym in watchlist if sym in results]


# ── Metis contract ────────────────────────────────────────────────────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "get_market_prices",
        "description": (
            "Returns current price and daily percentage change for the Felhaven watchlist. "
            "Call this when the user asks about stocks, market prices, "
            "portfolio performance, or any ticker by name."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


def handle() -> dict[str, Any]:
    """Called by the toolbox dispatcher when the LLM invokes get_market_prices."""
    return {"tickers": query_all()}


def fetch() -> dict[str, Any]:
    """Kairos entry point — raises on total failure; per-ticker errors are embedded."""
    results = query_all()
    if results and all("error" in r for r in results):
        raise RuntimeError("Midas: all tickers failed")
    return {"tickers": results}


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
