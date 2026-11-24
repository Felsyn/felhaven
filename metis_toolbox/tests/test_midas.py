"""
test_midas.py — unit tests for tools/midas.py's Vault-backed Finnhub key.

Hermetic: the network seam (hermes.get) and the Cerberus-key seam
(midas._finnhub_key / cerberus.is_unlocked) are mocked, so no test touches the
network or the real Cerberus Vault. Covers the migration off .env/env onto the
Cerberus Vault (mirrors test_callimachus.py's _brave_key coverage):

    python -X utf8 -m unittest tests.test_midas
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cerberus
from tools import hermes, midas


class _FakeResp:
    """Stand-in for hermes.get's return value: .json(), .raise_for_status()."""

    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self._status = status

    def raise_for_status(self) -> None:
        if self._status >= 400:
            raise Exception(f"HTTP {self._status}")

    def json(self) -> dict:
        return self._payload


class TestFetchOneVaultKey(unittest.TestCase):
    def setUp(self) -> None:
        self._unlocked = mock.patch.object(cerberus, "is_unlocked", return_value=True)
        self._unlocked.start()
        self.addCleanup(self._unlocked.stop)

    def test_locked_vault_is_vault_locked(self) -> None:
        with mock.patch.object(cerberus, "is_unlocked", return_value=False):
            out = midas._fetch_one("AAPL")
        self.assertEqual(out, {"symbol": "AAPL", "error": midas.ERR_VAULT_LOCKED})

    def test_unlocked_but_no_key_is_no_key(self) -> None:
        with mock.patch.object(midas, "_finnhub_key",
                               side_effect=cerberus.VaultError("no vault entry")):
            out = midas._fetch_one("AAPL")
        self.assertEqual(out, {"symbol": "AAPL", "error": midas.ERR_NO_KEY})

    def test_success_shapes_result(self) -> None:
        with mock.patch.object(midas, "_finnhub_key", return_value="fake-key"), \
                mock.patch("tools.midas.hermes.get",
                           return_value=_FakeResp({"c": 105.0, "pc": 100.0})) as get:
            out = midas._fetch_one("AAPL")
        self.assertNotIn("error", out)
        self.assertEqual(out["direction"], "up")
        self.assertEqual(out["pct"], 5.0)
        # The Vault key is passed as the request token, never hardcoded.
        self.assertEqual(get.call_args.kwargs["params"]["token"], "fake-key")

    def test_zero_fields_is_no_data(self) -> None:
        with mock.patch.object(midas, "_finnhub_key", return_value="fake-key"), \
                mock.patch("tools.midas.hermes.get", return_value=_FakeResp({"c": 0, "pc": 0})):
            out = midas._fetch_one("BADSYM")
        self.assertEqual(out, {"symbol": "BADSYM", "error": midas.ERR_NO_DATA})

    def test_request_exception_is_fetch_failed(self) -> None:
        with mock.patch.object(midas, "_finnhub_key", return_value="fake-key"), \
                mock.patch("tools.midas.hermes.get", side_effect=Exception("boom")), \
                self.assertLogs("METIS.midas", level="WARNING"):
            out = midas._fetch_one("AAPL")
        self.assertEqual(out, {"symbol": "AAPL", "error": midas.ERR_FETCH_FAILED})

    def test_an_http_error_logs_its_status(self) -> None:
        with mock.patch.object(midas, "_finnhub_key", return_value="fake-key"), \
                mock.patch("tools.midas.hermes.get", side_effect=hermes.HTTPStatus(429)), \
                self.assertLogs("METIS.midas", level="WARNING") as cm:
            out = midas._fetch_one("AAPL")
        self.assertEqual(out["error"], midas.ERR_FETCH_FAILED)
        self.assertIn("HTTPStatus 429", "\n".join(cm.output))

    def test_every_quote_is_capped(self) -> None:
        with mock.patch.object(midas, "_finnhub_key", return_value="fake-key"), \
                mock.patch("tools.midas.hermes.get",
                           return_value=_FakeResp({"c": 1.0, "pc": 1.0})) as get:
            midas._fetch_one("AAPL")
        self.assertEqual(get.call_args.kwargs["cap"], midas._MAX_BYTES)

    def test_key_never_cached_across_calls(self) -> None:
        # Each fetch re-derives the key at call time — never memoized on the
        # module — so a key rotated mid-session takes effect on the next call.
        with mock.patch.object(midas, "_finnhub_key",
                               side_effect=["key-1", "key-2"]) as key_fn, \
                mock.patch("tools.midas.hermes.get",
                           return_value=_FakeResp({"c": 1.0, "pc": 1.0})):
            midas._fetch_one("AAPL")
            midas._fetch_one("AAPL")
        self.assertEqual(key_fn.call_count, 2)


class TestLogDoesNotLeakKey(unittest.TestCase):
    """Regression for the Finnhub key leaking into logs/*.log (found in the
    session-7 audit via a live felhaven.log). A network error's message carries
    the request URL, and that URL carries ?token=<key> — so _fetch_one must log
    a sanitized identifier, never the raw exception (§2). Emanon renders those
    log lines on screen, so this is a real exposure, not just an at-rest one."""

    def test_request_error_message_is_not_logged(self) -> None:
        leaky = ("502 Server Error for url: "
                 "https://finnhub.io/api/v1/quote?symbol=AAPL&token=SECRETKEY123")
        with mock.patch.object(cerberus, "is_unlocked", return_value=True), \
                mock.patch.object(midas, "_finnhub_key", return_value="SECRETKEY123"), \
                mock.patch("tools.midas.hermes.get", side_effect=Exception(leaky)):
            with self.assertLogs("METIS.midas", level="WARNING") as cm:
                out = midas._fetch_one("AAPL")
        self.assertEqual(out["error"], midas.ERR_FETCH_FAILED)
        joined = "\n".join(cm.output)
        self.assertNotIn("SECRETKEY123", joined)   # the key must never appear
        self.assertNotIn("token=", joined)         # nor the URL that carries it
        self.assertIn("Exception", joined)         # the class name alone is fine


class TestFmtPrice(unittest.TestCase):
    def test_thousands_get_commas(self) -> None:
        self.assertEqual(midas._fmt_price(1234.5), "$1,234.50")

    def test_dollars_two_decimals(self) -> None:
        self.assertEqual(midas._fmt_price(12.3), "$12.30")

    def test_sub_dollar_four_decimals(self) -> None:
        self.assertEqual(midas._fmt_price(0.5), "$0.5000")


def _good(symbol: str, price: float = 100.0) -> dict:
    """A well-shaped success result dict, matching _fetch_one's happy path."""
    return {
        "symbol": symbol, "price": price, "price_fmt": f"${price:.2f}",
        "pct": 1.0, "pct_fmt": "+1.00%", "direction": "up",
    }


class _QueryAllBase(unittest.TestCase):
    """query_all() reads module-level cache globals and the watchlist file.
    Reset the cache and stub the watchlist so every test is isolated and never
    touches the real config or network."""

    def setUp(self) -> None:
        self._orig = (midas._cache, midas._cache_ts, midas.load_watchlist)
        midas._cache = {}
        midas._cache_ts = 0.0

    def tearDown(self) -> None:
        midas._cache, midas._cache_ts, midas.load_watchlist = self._orig


class TestQueryAll(_QueryAllBase):

    def test_cache_serves_within_ttl(self) -> None:
        midas.load_watchlist = lambda: ["AAPL"]
        with mock.patch.object(midas, "_fetch_one",
                               side_effect=lambda s: _good(s)) as fo:
            midas.query_all()          # populates the cache
            midas.query_all()          # within TTL → served from cache, no refetch
        self.assertEqual(fo.call_count, 1)

    def test_force_bypasses_cache(self) -> None:
        midas.load_watchlist = lambda: ["AAPL"]
        with mock.patch.object(midas, "_fetch_one",
                               side_effect=lambda s: _good(s)) as fo:
            midas.query_all()
            midas.query_all(force=True)
        self.assertEqual(fo.call_count, 2)

    def test_result_order_follows_watchlist(self) -> None:
        # Futures finish in arbitrary order; query_all must re-sort to the
        # watchlist so the panel rows never shuffle between ticks.
        midas.load_watchlist = lambda: ["AAA", "BBB", "CCC"]
        with mock.patch.object(midas, "_fetch_one", side_effect=lambda s: _good(s)):
            out = midas.query_all(force=True)
        self.assertEqual([r["symbol"] for r in out], ["AAA", "BBB", "CCC"])

    def test_stale_fallback_dims_not_blanks(self) -> None:
        # A fetch that fails after a good value was cached rides the last good
        # value back with a `stale` flag (no "error" key) — the panel dims it.
        midas.load_watchlist = lambda: ["AAPL"]
        seq = [_good("AAPL"), {"symbol": "AAPL", "error": midas.ERR_FETCH_FAILED}]
        with mock.patch.object(midas, "_fetch_one", side_effect=seq):
            first = midas.query_all(force=True)
            second = midas.query_all(force=True)
        self.assertNotIn("stale", first[0])
        self.assertTrue(second[0].get("stale"))            # flagged stale...
        self.assertNotIn("error", second[0])               # ...not blanked to error
        self.assertEqual(second[0]["price_fmt"], first[0]["price_fmt"])


_AS_OF = "2026-11-22T09:30:00"


class TestFetchContract(unittest.TestCase):
    """fetch()'s raise-on-wholesale-failure contract, and its one deliberate
    exception (§12): an all-'vault_locked' result is the normal sealed state and
    must NOT raise — raising it logged an ERROR every 60 s. handle() never
    raises regardless."""

    def test_partial_success_does_not_raise(self) -> None:
        mixed = [_good("AAPL"), {"symbol": "MSFT", "error": midas.ERR_NO_DATA}]
        with mock.patch.object(midas, "_query", return_value=(mixed, _AS_OF)):
            self.assertEqual(midas.fetch(), {"tickers": mixed, "as_of": _AS_OF})

    def test_all_failed_raises_naming_dominant_slug(self) -> None:
        allbad = [
            {"symbol": "AAPL", "error": midas.ERR_NO_KEY},
            {"symbol": "MSFT", "error": midas.ERR_NO_KEY},
            {"symbol": "CAT",  "error": midas.ERR_FETCH_FAILED},
        ]
        with mock.patch.object(midas, "_query", return_value=(allbad, _AS_OF)):
            with self.assertRaises(RuntimeError) as ctx:
                midas.fetch()
        # The dominant cause is named so the log says *why*, not just "failed".
        self.assertIn(midas.ERR_NO_KEY, str(ctx.exception))

    def test_all_vault_locked_is_quiet(self) -> None:
        locked = [
            {"symbol": "AAPL", "error": midas.ERR_VAULT_LOCKED},
            {"symbol": "MSFT", "error": midas.ERR_VAULT_LOCKED},
        ]
        with mock.patch.object(midas, "_query", return_value=(locked, _AS_OF)):
            out = midas.fetch()                    # must not raise
        self.assertEqual(out, {"tickers": locked, "as_of": _AS_OF})

    def test_empty_watchlist_does_not_raise(self) -> None:
        with mock.patch.object(midas, "_query", return_value=([], _AS_OF)):
            self.assertEqual(midas.fetch(), {"tickers": [], "as_of": _AS_OF})

    def test_handle_wraps_tickers_and_never_raises(self) -> None:
        allbad = [{"symbol": "AAPL", "error": midas.ERR_VAULT_LOCKED}]
        with mock.patch.object(midas, "_query", return_value=(allbad, _AS_OF)):
            self.assertEqual(midas.handle(), {"tickers": allbad, "as_of": _AS_OF})


class _Fresh(unittest.TestCase):
    """An empty cache, a stubbed watchlist, an open vault holding a key and a
    fake wall clock; the network is hermes.get, patched per test. The patches
    create what's missing, so a red check against older code fails rather
    than errors."""

    def setUp(self) -> None:
        self.clock = [datetime(2026, 11, 22, 9, 30, 0)]
        self.unlocked = [True]
        for name, value in (
                ("_cache", {}), ("_cache_ts", 0.0), ("_cache_as_of", ""),
                ("load_watchlist", lambda: ["AAPL", "MSFT"]),
                ("_load_watchlist", lambda: ["AAPL", "MSFT"]),
                ("_now", lambda: self.clock[0]),
                ("_finnhub_key", lambda: "fake-key")):
            p = mock.patch.object(midas, name, value, create=True)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(cerberus, "is_unlocked", lambda: self.unlocked[0])
        p.start()
        self.addCleanup(p.stop)

    def _expire_cache(self) -> None:
        midas._cache_ts -= midas.CACHE_TTL + 1


class TestAsOf(_Fresh):
    """Every answer says when its prices are from (the audit's T7, BACKLOG):
    a cached set is up to 60 s old, and a stale ticker older still."""

    def test_an_answer_carries_the_time_its_set_was_fetched(self) -> None:
        with mock.patch.object(hermes, "get", return_value=_FakeResp({"c": 100.0, "pc": 99.0})):
            first = midas.handle()
            self.clock[0] = datetime(2026, 11, 22, 9, 30, 40)
            again = midas.handle()                 # served from the cache
            polled = midas.fetch()
        self.assertEqual(first.get("as_of"), "2026-11-22T09:30:00")
        self.assertEqual(again.get("as_of"), "2026-11-22T09:30:00")
        self.assertEqual(polled.get("as_of"), "2026-11-22T09:30:00")

    def test_a_stale_ticker_keeps_the_time_its_price_is_from(self) -> None:
        with mock.patch.object(hermes, "get", return_value=_FakeResp({"c": 100.0, "pc": 99.0})):
            midas.handle()
        self._expire_cache()
        self.clock[0] = datetime(2026, 11, 22, 9, 31, 30)
        with mock.patch.object(hermes, "get", side_effect=OSError("down")), \
                self.assertLogs("METIS.midas", "WARNING"):
            out = midas.handle()
        self.assertEqual(out.get("as_of"), "2026-11-22T09:31:30")
        for t in out["tickers"]:
            self.assertTrue(t.get("stale"), t)
            self.assertEqual(t.get("as_of"), "2026-11-22T09:30:00", t)


class TestARefusalIsNeverReused(_Fresh):
    """Measured 2026-11-22: locked, then unlocked a second later, Midas kept
    answering vault_locked from its 60 s cache with no network call, so
    Pythia said "locked" for a minute after the vault opened."""

    def test_prices_come_as_soon_as_the_vault_opens(self) -> None:
        self.unlocked[0] = False
        self.assertEqual({t.get("error") for t in midas.handle()["tickers"]},
                         {midas.ERR_VAULT_LOCKED})
        self.unlocked[0] = True
        with mock.patch.object(hermes, "get",
                               return_value=_FakeResp({"c": 100.0, "pc": 99.0})) as get:
            out = midas.handle()
        self.assertEqual(get.call_count, 2)
        self.assertEqual([t.get("price_fmt") for t in out["tickers"]], ["$100.00"] * 2)

    def test_prices_come_as_soon_as_a_key_is_stored(self) -> None:
        with mock.patch.object(midas, "_finnhub_key",
                               side_effect=cerberus.VaultError("no such entry")):
            midas.handle()
        with mock.patch.object(hermes, "get",
                               return_value=_FakeResp({"c": 100.0, "pc": 99.0})) as get:
            midas.handle()
        self.assertEqual(get.call_count, 2)


class TestOnlyARealPriceIsAPrice(_Fresh):
    """json reads NaN and Infinity: a NaN price showed as "$nan" and "nan%"."""

    def test_a_price_that_isnt_a_finite_number_is_no_data(self) -> None:
        for payload in ({"c": float("nan"), "pc": 9.0}, {"c": float("inf"), "pc": 9.0},
                        {"c": 10.0, "pc": float("nan")}, {"c": "12", "pc": 9.0},
                        {"c": True, "pc": 9.0}):
            with self.subTest(payload=str(payload)), \
                    mock.patch.object(hermes, "get", return_value=_FakeResp(payload)):
                self.assertEqual(midas._fetch_one("AAPL").get("error"), midas.ERR_NO_DATA)


class TestWatchlistShape(unittest.TestCase):
    """Only the watchlist's non-empty strings count, each once. Measured
    2026-11-22: a dict in it raised from handle(), and a bare string in place
    of the list was fetched one letter at a time."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "midas_watchlist.json")
        p = mock.patch.object(midas, "_WATCHLIST_PATH", self.path)
        p.start()
        self.addCleanup(p.stop)
        self.load = getattr(midas, "load_watchlist", None) or midas._load_watchlist

    def _write(self, text: str) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_it_keeps_each_ticker_once_and_nothing_else(self) -> None:
        self._write('{"tickers": [{"a": 1}, "MP", null, 5, "  ", "MP", " CAT "]}')
        with self.assertLogs("METIS.midas", "WARNING"):
            self.assertEqual(self.load(), ["MP", "CAT"])

    def test_a_list_that_isnt_one_is_no_watchlist(self) -> None:
        for text in ('{"tickers": "MP"}', '[]', '{"tickers": 7}'):
            with self.subTest(text):
                self._write(text)
                with self.assertLogs("METIS.midas", "ERROR"):
                    self.assertEqual(self.load(), [])

    def test_handle_never_raises_on_a_dict_in_the_watchlist(self) -> None:
        self._write('{"tickers": [{"a": 1}, "MP"]}')
        with mock.patch.object(midas, "_cache", {}), \
                mock.patch.object(midas, "_cache_ts", 0.0), \
                mock.patch.object(cerberus, "is_unlocked", return_value=False), \
                self.assertLogs("METIS.midas", "WARNING"):
            try:
                out = midas.handle()
            except TypeError as e:
                self.fail(f"handle() raised {e!r}")
        self.assertEqual([t["symbol"] for t in out["tickers"]], ["MP"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
