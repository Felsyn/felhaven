"""
test_plutus.py — unit tests for tools/plutus.py (holdings ledger).

Pins the average-cost fold permanently: Plutus is the only real-money logic in
the stack and the one module the brain can never touch, so the math has to be
nailed down. No network, no Tk, no sleeps. Run from the package root:

    python -X utf8 -m unittest discover -s tests -p "test_*.py"
    python -X utf8 -m unittest tests.test_plutus            # just this file

The real plutus_ledger.json is live personal financial data and is NEVER read or
written here:
  - Derivation tests pass explicit `events` lists — zero I/O.
  - Mutation/persistence tests patch `plutus._LEDGER_PATH` to a TemporaryDirectory
    in setUp and restore it in tearDown, so writes land in the temp dir only.
"""

import json
import os
import random
import sys
import tempfile
import unittest
from datetime import date

# Make the package root importable no matter where the runner is launched.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import plutus


# ── The handoff's canonical MP sequence ───────────────────────────────────────
#
# | # | event              | shares | cost  | note                              |
# |---|--------------------|--------|-------|-----------------------------------|
# | 1 | buy  10 MP @ 15.00 | 10.0   | 150.0 | avg 15                            |
# | 2 | buy  10 MP @ 25.00 | 20.0   | 400.0 | avg blends to 20                  |
# | 3 | sell  5 MP @ 40.00 | 15.0   | 300.0 | cost falls 5x20=100; the 40 is irrelevant |
# | 4 | sell 15 MP @  1.00 |  —     |  —    | full exit -> clamp lands exact 0/0|
# | 5 | buy   4 MP @ 50.00 |  4.0   | 200.0 | re-entry: basis resets to 50      |

def _canon(n=5, sell3_price=40.00):
    """First `n` events of the canonical sequence. `sell3_price` lets the
    sell-price-invariance test vary event 3's price without touching anything
    else — the surviving position must not move."""
    seq = [
        {"date": "2026-01-05", "ticker": "MP", "action": "buy",  "shares": 10, "price": 15.00},
        {"date": "2026-02-05", "ticker": "MP", "action": "buy",  "shares": 10, "price": 25.00},
        {"date": "2026-03-05", "ticker": "MP", "action": "sell", "shares": 5,  "price": sell3_price},
        {"date": "2026-03-06", "ticker": "MP", "action": "sell", "shares": 15, "price": 1.00},
        {"date": "2026-04-01", "ticker": "MP", "action": "buy",  "shares": 4,  "price": 50.00},
    ]
    return seq[:n]


class _Base(unittest.TestCase):
    """Shared assertion helpers. Float fields use places=9 per the house rule;
    the exact-0.0 clamp cases assert exact equality (the clamp is the behavior
    under test) and so do NOT go through these helpers."""

    def assertPosition(self, pos, ticker, shares, cost):
        self.assertIn(ticker, pos)
        self.assertAlmostEqual(pos[ticker]["shares"], shares, places=9)
        self.assertAlmostEqual(pos[ticker]["cost"],   cost,   places=9)

    def assertTotals(self, tot, shares, cost):
        self.assertAlmostEqual(tot["shares"], shares, places=9)
        self.assertAlmostEqual(tot["cost"],   cost,   places=9)


# ── Derivation math (pure folds — explicit events, no I/O) ─────────────────────

class TestDerivation(_Base):

    def test_single_buy(self):
        self.assertPosition(plutus.positions(_canon(1)), "MP", 10.0, 150.0)

    def test_blended_average(self):
        self.assertPosition(plutus.positions(_canon(2)), "MP", 20.0, 400.0)

    def test_sell_reduces_at_average_not_sell_price(self):
        # THE core invariant. Vary event 3's sell price wildly; the surviving
        # position must be byte-for-byte identical because the sell price never
        # touches cost — only the average cost of held shares does.
        for sell_price in (1.00, 40.00, 1000.00):
            with self.subTest(sell_price=sell_price):
                pos = plutus.positions(_canon(3, sell3_price=sell_price))
                self.assertPosition(pos, "MP", 15.0, 300.0)

    def test_full_exit_drops_ticker(self):
        evs = _canon(4)
        self.assertNotIn("MP", plutus.positions(evs))
        # Clamp lands an exact 0/0 — assert exact equality, not almost-equal.
        self.assertEqual(plutus.totals(evs), {"shares": 0.0, "cost": 0.0})

    def test_reentry_resets_basis(self):
        # Event 4 fully exits (clamped to 0/0); event 5 rebuys 4 @ 50 with no
        # contamination from the dead lot -> avg resets to 50.
        self.assertPosition(plutus.positions(_canon(5)), "MP", 4.0, 200.0)

    def test_reentry_after_partial_sell_keeps_basis(self):
        # Companion to test_reentry_resets_basis. There the FULL exit clamps cost
        # to 0/0, which launders any sell-rule error away -> a full-exit re-entry
        # is blind to whether sells reduce at average cost or at sell price.
        # Here the sell is PARTIAL: a live lot survives, no clamp fires, so the
        # preserved basis is exactly what a later re-buy blends onto. A buggy sell
        # that touched cost would contaminate that basis and this test would see it.
        evs = [
            {"date": "2026-01-05", "ticker": "MP", "action": "buy",  "shares": 10, "price": 15.00},
            {"date": "2026-02-05", "ticker": "MP", "action": "buy",  "shares": 10, "price": 25.00},
            {"date": "2026-03-05", "ticker": "MP", "action": "sell", "shares": 5,  "price": 40.00},
            {"date": "2026-04-01", "ticker": "MP", "action": "buy",  "shares": 5,  "price": 30.00},
        ]
        # After the partial sell: 15 sh, cost 300 (5 sold at avg 20; the 40 is irrelevant).
        self.assertPosition(plutus.positions(evs[:3]), "MP", 15.0, 300.0)
        # Re-buy 5 @ 30 blends onto the preserved 300 basis: 20 sh, cost 300 + 150 = 450.
        self.assertPosition(plutus.positions(evs), "MP", 20.0, 450.0)

    def test_float_dust_clamps_exactly_zero(self):
        # The classic 0.1 + 0.2 != 0.3. Before the drop-filter, the clamp must
        # have landed EXACTLY 0.0/0.0 — assert exact equality.
        evs = [
            {"date": "d", "ticker": "MP", "action": "buy",  "shares": 0.1, "price": 10},
            {"date": "d", "ticker": "MP", "action": "buy",  "shares": 0.2, "price": 10},
            {"date": "d", "ticker": "MP", "action": "sell", "shares": 0.3, "price": 12},
        ]
        self.assertNotIn("MP", plutus.positions(evs))
        self.assertEqual(plutus.totals(evs), {"shares": 0.0, "cost": 0.0})

    def test_oversell_clamps_never_negative(self):
        evs = [
            {"date": "d", "ticker": "MP", "action": "buy",  "shares": 5,  "price": 10},
            {"date": "d", "ticker": "MP", "action": "sell", "shares": 10, "price": 10},
        ]
        self.assertNotIn("MP", plutus.positions(evs))
        self.assertEqual(plutus.totals(evs), {"shares": 0.0, "cost": 0.0})

    def test_sell_without_position_is_noop(self):
        evs = [{"date": "d", "ticker": "MP", "action": "sell", "shares": 5, "price": 10}]
        self.assertEqual(plutus.positions(evs), {})          # no KeyError, no negative
        self.assertEqual(plutus.totals(evs), {"shares": 0.0, "cost": 0.0})

    def test_multi_ticker_isolation(self):
        evs = [
            {"date": "d", "ticker": "MP",  "action": "buy",  "shares": 10, "price": 15},
            {"date": "d", "ticker": "CAT", "action": "buy",  "shares": 2,  "price": 300},
            {"date": "d", "ticker": "MP",  "action": "buy",  "shares": 10, "price": 25},
            {"date": "d", "ticker": "MP",  "action": "sell", "shares": 5,  "price": 40},
        ]
        pos = plutus.positions(evs)
        self.assertPosition(pos, "MP",  15.0, 300.0)
        self.assertPosition(pos, "CAT",  2.0, 600.0)
        # CAT is exactly what it would be on its own — MP's churn never moved it.
        cat_alone = plutus.positions(
            [{"date": "d", "ticker": "CAT", "action": "buy", "shares": 2, "price": 300}]
        )
        self.assertEqual(pos["CAT"], cat_alone["CAT"])
        # totals is the sum of both tickers.
        self.assertTotals(plutus.totals(evs), 17.0, 900.0)

    def test_zero_price_buy_is_legal_in_fold(self):
        # Grants / free shares: a zero-price lot folds to cost 0.0 and a later
        # sell at average cost leaves cost untouched (avg of a 0-cost lot is 0).
        evs = [
            {"date": "d", "ticker": "MP", "action": "buy",  "shares": 10, "price": 0},
            {"date": "d", "ticker": "MP", "action": "sell", "shares": 5,  "price": 12},
        ]
        self.assertPosition(plutus.positions(evs), "MP", 5.0, 0.0)

    def test_empty_log(self):
        self.assertEqual(plutus.positions([]), {})
        self.assertEqual(plutus.totals([]), {"shares": 0.0, "cost": 0.0})
        self.assertEqual(plutus.history([]), [])


# ── History (the honest, newest-first record) ──────────────────────────────────

class TestHistory(_Base):

    def test_history_complete_and_newest_first(self):
        rows = plutus.history(_canon(4))
        self.assertEqual(len(rows), 4)

        # Newest first: row 0 is event 4 — the 15-share exit sell @ 1.00.
        self.assertEqual(rows[0]["action"], "sell")
        self.assertEqual(rows[0]["date"],   "2026-03-06")
        self.assertAlmostEqual(rows[0]["shares"], 15.0, places=9)
        self.assertAlmostEqual(rows[0]["price"],   1.0, places=9)

        # Honest history: every row carries cost = shares * price, INCLUDING
        # sells. Row 1 is event 3 (sell 5 @ 40) -> 200.0 of sell *proceeds*, not
        # the average cost the fold used. Literals are hand-computed, newest->oldest.
        expected_cost = [15.0, 200.0, 250.0, 150.0]
        for row, exp in zip(rows, expected_cost):
            self.assertAlmostEqual(row["cost"], exp, places=9)
            self.assertAlmostEqual(row["cost"], row["shares"] * row["price"], places=9)


# ── Mutation & validation (add_event — patched ledger path) ────────────────────

class _PatchedLedger(_Base):
    """Redirect plutus._LEDGER_PATH into a throwaway temp dir so no test ever
    touches the real ledger. Restored in tearDown."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_path = plutus._LEDGER_PATH
        plutus._LEDGER_PATH = os.path.join(self._tmp.name, "plutus_ledger.json")

    def tearDown(self):
        plutus._LEDGER_PATH = self._orig_path
        self._tmp.cleanup()


class TestAddEvent(_PatchedLedger):

    def test_normalization(self):
        ev = plutus.add_event("  mp  ", "BUY", 10, 15)
        self.assertEqual(ev["ticker"], "MP")              # stripped + upper
        self.assertEqual(ev["action"], "buy")             # stripped + lower
        self.assertEqual(ev["date"], date.today().isoformat())
        self.assertIsInstance(ev["shares"], float)        # coerced
        self.assertIsInstance(ev["price"], float)
        self.assertEqual(ev["shares"], 10.0)
        self.assertEqual(ev["price"], 15.0)

    def test_rejections_do_not_persist(self):
        bad = [
            ("",    "buy",  10, 15),     # empty ticker
            ("   ", "buy",  10, 15),     # whitespace ticker
            ("MP",  "hold", 10, 15),     # not buy/sell
            ("MP",  "buy",   0, 15),     # shares == 0
            ("MP",  "buy",  -1, 15),     # shares < 0
            ("MP",  "buy", None, 15),    # shares None
            ("MP",  "buy",  10, -0.01),  # price < 0
            ("MP",  "buy",  10, None),   # price None
        ]
        for args in bad:
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    plutus.add_event(*args)
        # Every call above raised before persisting -> the ledger has zero events.
        self.assertEqual(plutus.load_events(), [])

    def test_persistence_round_trip(self):
        plutus.add_event("MP", "buy", 10, 15)
        plutus.add_event("MP", "buy", 10, 25)

        evs = plutus.load_events()
        self.assertEqual(len(evs), 2)
        self.assertEqual([e["ticker"] for e in evs], ["MP", "MP"])
        self.assertEqual(evs[0]["price"], 15.0)            # order preserved
        self.assertEqual(evs[1]["price"], 25.0)

        # The no-arg fold reads the patched file and matches the explicit fold.
        self.assertEqual(plutus.positions(), plutus.positions(evs))
        self.assertPosition(plutus.positions(), "MP", 20.0, 400.0)


# ── Robustness (load_events — patched path) ────────────────────────────────────

class TestLoadEvents(_PatchedLedger):

    def test_missing_file_returns_empty(self):
        self.assertFalse(os.path.exists(plutus._LEDGER_PATH))  # fresh temp dir
        self.assertEqual(plutus.load_events(), [])

    def test_corrupted_file_returns_empty(self):
        with open(plutus._LEDGER_PATH, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(plutus.load_events(), [])

    def test_wrong_shape_returns_empty(self):
        with open(plutus._LEDGER_PATH, "w", encoding="utf-8") as f:
            json.dump({"foo": 1}, f)                        # valid JSON, no "events"
        self.assertEqual(plutus.load_events(), [])


# ── Invariant sweep (seeded, deterministic) ────────────────────────────────────

class TestInvariants(_Base):

    def test_random_prefixes_hold_invariants(self):
        # Not a mirror of the fold — only properties that must hold for ANY
        # correct average-cost ledger, checked after every prefix.
        random.seed(42)
        tickers = ["MP", "CAT", "DOG"]
        events = [
            {
                "date":   "2026-01-01",
                "ticker": random.choice(tickers),
                "action": random.choice(["buy", "sell"]),
                "shares": round(random.uniform(0.5, 50.0), 4),
                "price":  round(random.uniform(0.0, 500.0), 2),
            }
            for _ in range(200)
        ]

        for k in range(1, len(events) + 1):
            prefix = events[:k]
            pos = plutus.positions(prefix)
            tot = plutus.totals(prefix)

            for ticker, v in pos.items():
                # No zero/negative-share ticker may survive in the position view,
                # and cost never goes meaningfully negative.
                self.assertGreater(v["shares"], 0.0, msg=f"prefix {k}, {ticker}")
                self.assertGreaterEqual(v["cost"], -1e-9, msg=f"prefix {k}, {ticker}")

            # totals is the consistent sum over the surviving positions.
            self.assertAlmostEqual(tot["shares"],
                                   sum(v["shares"] for v in pos.values()), places=9)
            self.assertAlmostEqual(tot["cost"],
                                   sum(v["cost"] for v in pos.values()), places=9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
