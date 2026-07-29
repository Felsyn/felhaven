"""
test_argus.py — unit tests for tools/argus.py (network awareness).

The pure, deterministic seams are the two text parsers (netsh / ipconfig) and
the timeline diff, so those carry the weight; fetch() gets a structure-only
smoke against real psutil (no Tk, no panel). Run from the package root:

    python -X utf8 -m unittest tests.test_argus

_save_timeline is monkeypatched wherever a diff could fire, so no test ever
writes argus_timeline.json; the timeline test also resets the module's baseline
state so seeding is deterministic.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import argus


# ── Firewall parse ────────────────────────────────────────────────────────────

class TestParseFirewall(unittest.TestCase):

    SAMPLE = """
Domain Profile Settings:
----------------------------------------------------------------------
State                                 ON

Private Profile Settings:
----------------------------------------------------------------------
State                                 OFF

Public Profile Settings:
----------------------------------------------------------------------
State                                 ON
"""

    def test_parses_on_off_per_profile(self):
        out = argus._parse_firewall(self.SAMPLE)
        self.assertEqual(out, {"domain": "on", "private": "off", "public": "on"})

    def test_garbage_leaves_dashes(self):
        out = argus._parse_firewall("not netsh output at all")
        self.assertEqual(out, {"domain": "—", "private": "—", "public": "—"})


# ── DNS parse ─────────────────────────────────────────────────────────────────

class TestParseDns(unittest.TestCase):

    SAMPLE = """
Windows IP Configuration

    api.github.com
    ----------------------------------------
    Record Name . . . . . : api.github.com
    Record Type . . . . . : 1
    Time To Live  . . . . : 30
    Data Length . . . . . : 4
    Section . . . . . . . : Answer
    A (Host) Record . . . : 140.82.113.6


    example.com
    ----------------------------------------
    Record Name . . . . . : example.com
    Record Type . . . . . : 5
    Section . . . . . . . : Answer
    CNAME Record  . . . . : cdn.example.net
"""

    def test_parses_names_and_records(self):
        out = argus._parse_dns(self.SAMPLE)
        self.assertEqual(out, [
            {"name": "api.github.com", "records": ["140.82.113.6"]},
            {"name": "example.com",    "records": ["cdn.example.net"]},
        ])

    def test_service_stopped_message_yields_empty(self):
        out = argus._parse_dns("Could not display the DNS Resolver Cache.")
        self.assertEqual(out, [])


# ── PID resolution edge cases ─────────────────────────────────────────────────

class TestResolve(unittest.TestCase):

    def test_none_pid_is_dash(self):
        self.assertEqual(argus._resolve(None), "—")

    def test_pid_zero_relabelled(self):
        # PID 0 = System Idle Process — Windows parks orphaned/closing sockets
        # there; the deliberate relabel keeps the panel from misleading.
        self.assertEqual(argus._resolve(0), argus._PID0_LABEL)


# ── Timeline diff ─────────────────────────────────────────────────────────────

class TestTimelineDiff(unittest.TestCase):

    def setUp(self):
        # Reset the live baseline and silence disk writes for every diff test.
        self._orig_save = argus._save_timeline
        argus._save_timeline = lambda: None
        argus._seeded = False
        argus._known = {}
        argus._timeline.clear()

    def tearDown(self):
        argus._save_timeline = self._orig_save
        argus._seeded = False
        argus._known = {}
        argus._timeline.clear()

    @staticmethod
    def _conn(pid, raddr, status="ESTABLISHED", proc="a.exe", laddr="10.0.0.1:5000"):
        return {"pid": pid, "proc": proc, "raddr": raddr,
                "laddr": laddr, "status": status}

    def test_first_tick_seeds_silently(self):
        argus._update_timeline([self._conn(1, "1.2.3.4:443")])
        self.assertEqual(len(argus._timeline), 0)   # no false flood at launch
        self.assertTrue(argus._seeded)

    def test_open_emitted_on_second_tick(self):
        base = [self._conn(1, "1.2.3.4:443")]
        argus._update_timeline(base)                       # seed
        argus._update_timeline(base + [self._conn(2, "5.6.7.8:443", proc="b.exe")])
        events = list(argus._timeline)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "open")
        self.assertEqual(events[0]["proc"], "b.exe")
        self.assertEqual(events[0]["raddr"], "5.6.7.8:443")

    def test_close_emitted_when_connection_gone(self):
        base = [self._conn(1, "1.2.3.4:443")]
        argus._update_timeline(base)                       # seed
        argus._update_timeline([])                         # everything closed
        events = list(argus._timeline)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "close")
        self.assertEqual(events[0]["raddr"], "1.2.3.4:443")

    def test_status_transition_emits_nothing(self):
        # A single socket moving ESTABLISHED -> TIME_WAIT during teardown is the
        # SAME connection (same pid/raddr/laddr); it must NOT emit a phantom
        # close+open pair. Identity excludes the TCP status.
        argus._update_timeline([self._conn(1, "1.2.3.4:443",
                                           status="ESTABLISHED")])   # seed
        argus._update_timeline([self._conn(1, "1.2.3.4:443",
                                           status="TIME_WAIT")])     # same socket
        self.assertEqual(len(argus._timeline), 0)


# ── fetch() shape (integration against real psutil) ───────────────────────────

class TestFetchShape(unittest.TestCase):

    def setUp(self):
        self._orig_save = argus._save_timeline
        argus._save_timeline = lambda: None

    def tearDown(self):
        argus._save_timeline = self._orig_save

    def test_dict_has_contract_keys(self):
        snap = argus.fetch()
        for key in ("as_of", "privilege", "summary", "connections",
                    "listening", "traffic", "dns", "firewall", "timeline"):
            self.assertIn(key, snap)

        self.assertIn(snap["privilege"], ("user", "admin"))

        for key in ("established", "listening", "other", "unresolved_pids"):
            self.assertIn(key, snap["summary"])
            self.assertIsInstance(snap["summary"][key], int)

        for key in ("window_s", "up_bps", "down_bps", "per_nic"):
            self.assertIn(key, snap["traffic"])

        self.assertIn(snap["dns"]["state"], ("ok", "empty", "unavailable"))
        self.assertIn(snap["firewall"]["state"], ("ok", "unavailable"))
        self.assertIsInstance(snap["connections"], list)
        self.assertIsInstance(snap["listening"], list)


# ── handle() / fetch() §2 contract (the LLM surface + the one raise) ───────────

class TestHandleContract(unittest.TestCase):
    """handle() is the brain-facing surface: it must slim fetch()'s full snapshot
    and never raise. fetch() is allowed exactly one raise — a wholesale
    net_connections() failure — which Kairos turns into a stale panel. These pin
    both, mocking the seams (the Aura precedent) so no real network read is
    needed."""

    _SNAP = {
        "as_of": 1.0,
        "privilege": "user",
        "summary": {"established": 2, "listening": 1, "other": 3,
                    "unresolved_pids": 1},
        # 10 connections so the [:8] slim is actually exercised.
        "connections": [
            {"pid": i, "proc": f"p{i}.exe", "family": "IPv4",
             "laddr": f"10.0.0.1:{5000 + i}", "raddr": f"1.2.3.{i}:443",
             "status": "ESTABLISHED"}
            for i in range(10)
        ],
        "listening": [{"pid": 9, "proc": "svc.exe", "proto": "TCP",
                       "laddr": "0.0.0.0:445", "state": "listening"}],
        "traffic": {"window_s": 5.0, "up_bps": 100, "down_bps": 200,
                    "per_nic": {}},
        "dns": {"as_of": 1.0, "state": "ok",
                "entries": [{"name": "a.example", "records": ["1.1.1.1"]}]},
        "firewall": {"as_of": 1.0, "state": "ok", "domain": "on",
                     "private": "on", "public": "off"},
        "timeline": [{"t": 1.0, "event": "open", "proc": "p0.exe",
                      "raddr": "1.2.3.0:443"}],
    }

    def test_handle_slims_the_snapshot(self):
        with mock.patch.object(argus, "fetch", return_value=self._SNAP):
            out = argus.handle()
        # Summary counts pass through (unresolved_pids -> unattributed_pids).
        self.assertEqual(out["established"], 2)
        self.assertEqual(out["listening"], 1)
        self.assertEqual(out["unattributed_pids"], 1)
        self.assertEqual(out["dns_cached_names"], 1)
        self.assertEqual(out["firewall"]["state"], "ok")
        # The heavy payloads never reach the model: no full connection table,
        # no timeline. Only a capped top-8 sample of connections.
        self.assertEqual(len(out["top_connections"]), 8)
        self.assertNotIn("connections", out)
        self.assertNotIn("timeline", out)
        self.assertEqual(set(out["top_connections"][0]),
                         {"proc", "remote", "status"})

    def test_top_connections_rank_established_first(self):
        # With no per-connection metric on Windows, "top" must at least mean
        # "live first": a TIME_WAIT teardown entry ahead of an ESTABLISHED one in
        # the raw table must NOT out-rank the live connection in the top-8 sample.
        snap = dict(self._SNAP)
        snap["connections"] = [
            {"pid": 1, "proc": "tw.exe", "family": "IPv4",
             "laddr": "10.0.0.1:1", "raddr": "9.9.9.9:443",
             "status": "TIME_WAIT"},
            {"pid": 2, "proc": "live.exe", "family": "IPv4",
             "laddr": "10.0.0.1:2", "raddr": "8.8.8.8:443",
             "status": "ESTABLISHED"},
        ]
        with mock.patch.object(argus, "fetch", return_value=snap):
            out = argus.handle()
        self.assertEqual(out["top_connections"][0]["status"], "ESTABLISHED")
        self.assertEqual(out["top_connections"][0]["proc"], "live.exe")

    def test_handle_never_raises_on_snapshot_failure(self):
        # fetch() raising wholesale (the net_connections() case) must degrade to
        # a §2 slug, not propagate — the model can recover from a dict.
        with mock.patch.object(argus, "fetch",
                               side_effect=OSError("net table gone")):
            out = argus.handle()
        self.assertEqual(out, {"error": "network_unavailable"})

    def test_fetch_raises_on_wholesale_net_connections_failure(self):
        # The documented single raise: if the connection table itself can't be
        # read, fetch() lets it through so Kairos delivers None (panel stays stale).
        with mock.patch.object(argus.psutil, "net_connections",
                               side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                argus.fetch()


if __name__ == "__main__":
    unittest.main()
