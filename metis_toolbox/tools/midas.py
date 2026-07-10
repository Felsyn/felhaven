"""
midas.py — Touch of Gold / Market Prices
=========================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Fetch current price and daily % change for a watchlist.

Contract:    Exposes TOOL_DEFINITION and handle().
             Also exposes query_all() for direct dashboard use.
             Returns price and % change. Nothing more.

Source:      Finnhub REST /quote (US equities, free tier). Plain `requests` —
             no SDK. Watchlist lives in midas_watchlist.json at the repo root
             (also the source for Plutus's ticker dropdown).

Key:         FINNHUB_API_KEY, read once at module load from the OS environment
             or a .env file at the app root (a real env var wins if both set).
             Missing key -> every ticker returns ERR_NO_KEY (panel shows a
             placeholder, never crashes). Never commit the key or the .env.

Upstream:    metis_toolbox/__init__.py (registration + dispatch)
Downstream:  metis_brain.py (via toolbox) | felhaven.py (direct import)

Requires:    requests (already in Felhaven stack)
             os, json, time, concurrent.futures (stdlib)
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

log = logging.getLogger("METIS.midas")

# ── Error types ───────────────────────────────────────────────────────────────
# Distinct codes so the dashboard panel can display each state differently.
#   NO_DATA       — ticker exists but Finnhub returned all-zero fields
#                   (invalid symbol, market never traded)
#   FETCH_FAILED  — network error, timeout, 429 rate limit, unexpected exception
#   STALE_CACHE   — cache hit, but older than CACHE_TTL (informational, usable)
#   NO_KEY        — FINNHUB_API_KEY not set in the environment

ERR_NO_DATA      = "no_data"
ERR_FETCH_FAILED = "fetch_failed"
ERR_STALE_CACHE  = "stale_cache"
ERR_NO_KEY       = "no_key"

# ── Config ────────────────────────────────────────────────────────────────────

# App root = one dir up from tools/ (next to felhaven.py). The watchlist and the
# optional .env both live here. Anchored to __file__, so cwd never matters.
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv() -> None:
    """
    Minimal .env loader — no python-dotenv dependency, so the stack stays
    flash-drive-portable. Reads KEY=VALUE lines from <app root>/.env into
    os.environ, but only for keys not already set, so a real OS environment
    variable (e.g. one set via `setx`) always wins over the file. Blank lines
    and '#' comments are ignored; surrounding quotes are stripped. Silent if
    the file is absent.
    """
    env_path = os.path.join(_APP_ROOT, ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning(f"Midas: failed to read .env: {e}")


_load_dotenv()
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")

_QUOTE_URL = "https://finnhub.io/api/v1/quote"
_HTTP_TIMEOUT = 6

# Watchlist config lives at the app root, next to felhaven.py and .env.
_WATCHLIST_PATH = os.path.join(_APP_ROOT, "midas_watchlist.json")


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
    if not FINNHUB_API_KEY:
        return {"symbol": symbol, "error": ERR_NO_KEY}

    try:
        resp = requests.get(
            _QUOTE_URL,
            params={"symbol": symbol, "token": FINNHUB_API_KEY},
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
    """Called by the toolbox dispatcher when Metis invokes get_market_prices."""
    return {"tickers": query_all()}


def fetch() -> dict[str, Any]:
    """Kairos entry point — raises on total failure; per-ticker errors are embedded."""
    results = query_all()
    if results and all("error" in r for r in results):
        raise RuntimeError("Midas: all tickers failed")
    return {"tickers": results}


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    results = query_all()
    for r in results:
        if "error" in r:
            print(f"[Midas] {r['symbol']:>8}  —  {r['error']}")
        else:
            arrow = "▲" if r["direction"] == "up" else "▼"
            stale = " (stale)" if r.get("stale") else ""
            print(f"[Midas] {r['symbol']:>8}  {r['price_fmt']:>12}  {arrow} {r['pct_fmt']}{stale}")
