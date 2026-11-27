"""
test_argus.py — unit tests for tools/argus.py (network awareness).

The pure, deterministic seams are the two text parsers (netsh / ipconfig) and
the timeline diff, so those carry the weight. fetch() runs with psutil and both
programs faked (_FakedSeams): it was a live test against the real psutil,
netsh and ipconfig until 2026-11-21, and the owner chose fakes, so the suite
launches nothing and CI gets the same answer every run. Run from the package
root:

    python -X utf8 -m unittest tests.test_argus

No test reads or writes the real argus_timeline.json: the diff tests silence
_save_timeline, and _FakedSeams points the file at a scratch folder and puts
the module's state back afterwards.
"""

import collections
import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from collections import deque
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


# ── the shell-outs name System32's programs ─────────────────────────────────────

class TestShellOutsUseSystem32(unittest.TestCase):
    """netsh and ipconfig by absolute path: a bare name runs a file of that
    name from the working folder before System32's (measured 2026-11-17)."""

    def setUp(self):
        saved = (argus._fw_block, argus._fw_ts, argus._dns_block, argus._dns_ts)
        self.addCleanup(self._restore, saved)
        argus._fw_block = argus._dns_block = None     # no throttled answer

    @staticmethod
    def _restore(saved):
        argus._fw_block, argus._fw_ts, argus._dns_block, argus._dns_ts = saved

    def _program(self, read):
        with mock.patch.object(argus.subprocess, "run",
                               return_value=mock.Mock(returncode=1, stdout="")) as run:
            read()
        return run.call_args.args[0][0]

    def _assert_system32(self, program, exe):
        self.assertTrue(os.path.isabs(program), program)
        self.assertTrue(os.path.normcase(program).endswith(
            os.path.normcase(os.path.join("System32", exe))), program)

    def test_the_firewall_read_runs_system32_netsh(self):
        self._assert_system32(self._program(argus._firewall), "netsh.exe")

    def test_the_dns_read_runs_system32_ipconfig(self):
        self._assert_system32(self._program(argus._dns), "ipconfig.exe")


# ── fetch() with its seams faked ──────────────────────────────────────────────

_Addr = collections.namedtuple("_Addr", "ip port")
_Conn = collections.namedtuple("_Conn", "fd family type laddr raddr status pid")

# The module state fetch() keeps between calls; _FakedSeams saves and restores
# all of it. _MISSING marks a name an older argus.py doesn't have, so a red
# check against it fails on the behaviour, not on setUp.
_STATE = ("_TIMELINE_PATH", "_timeline", "_timeline_loaded", "_known",
          "_seeded", "_proc_cache", "_prev_io", "_prev_io_ts", "_dns_block",
          "_dns_ts", "_fw_block", "_fw_ts")
_MISSING = object()

# ipconfig's refusal as Windows 11 printed it, measured 2026-11-21 (build
# 26200, a normal user): exit code 1 and this one line.
_REFUSED = "The requested operation requires elevation.\r\n"


class _FakedSeams(unittest.TestCase):
    """fetch() against fake psutil and fake netsh/ipconfig answers, with the
    timeline file in a scratch folder. Nothing outside the test runs."""

    def setUp(self):
        saved = {name: getattr(argus, name, _MISSING) for name in _STATE}
        self.addCleanup(self._restore, saved)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        argus._TIMELINE_PATH = os.path.join(tmp.name, "argus_timeline.json")
        argus._timeline = deque(maxlen=argus._TIMELINE_MAX)
        argus._timeline_loaded = False
        argus._known, argus._seeded, argus._proc_cache = {}, False, {}
        argus._prev_io, argus._prev_io_ts = None, 0.0
        argus._dns_block = argus._fw_block = None

        self.conns = [
            _Conn(-1, socket.AF_INET, socket.SOCK_STREAM, _Addr("10.0.0.2", 50000),
                  _Addr("93.184.215.14", 443), "ESTABLISHED", 101),
            _Conn(-1, socket.AF_INET, socket.SOCK_STREAM, _Addr("10.0.0.2", 50001),
                  _Addr("93.184.215.14", 443), "TIME_WAIT", 0),
            _Conn(-1, socket.AF_INET, socket.SOCK_STREAM, _Addr("0.0.0.0", 445),
                  (), "LISTEN", 4),
        ]
        self.answers = {
            "netsh.exe": mock.Mock(returncode=0, stdout=TestParseFirewall.SAMPLE),
            "ipconfig.exe": mock.Mock(returncode=0, stdout=TestParseDns.SAMPLE),
        }
        self.run = self._patch(argus.subprocess, "run", side_effect=self._answer)
        self._patch(argus.psutil, "net_connections",
                    side_effect=lambda kind: list(self.conns))
        self._patch(argus.psutil, "net_io_counters", return_value={
            "Wi-Fi": mock.Mock(bytes_sent=1000, bytes_recv=5000)})
        self._patch(argus.psutil, "Process", side_effect=self._process)

    def _patch(self, target, name, **kw):
        patcher = mock.patch.object(target, name, **kw)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def _answer(self, argv, **_kw):
        return self.answers[os.path.basename(argv[0]).lower()]

    @staticmethod
    def _process(pid):
        proc = mock.Mock()
        proc.as_dict.return_value = {"name": f"p{pid}.exe"}
        return proc

    @staticmethod
    def _restore(saved):
        for name, value in saved.items():
            if value is _MISSING:
                if hasattr(argus, name):
                    delattr(argus, name)
            else:
                setattr(argus, name, value)


class TestFetchShape(_FakedSeams):

    def test_dict_has_contract_keys(self):
        snap = argus.fetch()
        self.assertEqual(set(snap), {"as_of", "privilege", "summary",
                                     "connections", "listening", "traffic",
                                     "dns", "firewall", "timeline"})
        self.assertIn(snap["privilege"], ("user", "admin"))
        self.assertEqual(snap["summary"], {"established": 1, "listening": 1,
                                           "other": 1, "unresolved_pids": 0})
        self.assertEqual(snap["connections"][1]["proc"], argus._PID0_LABEL)
        self.assertEqual(snap["listening"][0]["proc"], "p4.exe")
        self.assertEqual(set(snap["traffic"]),
                         {"window_s", "up_bps", "down_bps", "per_nic"})
        self.assertEqual(snap["dns"]["state"], "ok")
        self.assertEqual(len(snap["dns"]["entries"]), 2)
        self.assertEqual(snap["firewall"]["state"], "ok")
        self.assertEqual(snap["timeline"], [])        # first tick seeds silently
        self.assertEqual(self.run.call_count, 2)      # the two fakes, nothing else

    def test_a_refused_dns_read_is_needs_admin(self):
        # Windows 11 refuses a normal user the cache: a permission, not a
        # failure, so it gets its own state (the owner's pick, 2026-11-21).
        self.answers["ipconfig.exe"] = mock.Mock(returncode=1, stdout=_REFUSED)
        dns = argus.fetch()["dns"]
        self.assertEqual(dns["state"], "needs_admin")
        self.assertEqual(dns["entries"], [])

    def test_any_other_dns_failure_stays_unavailable(self):
        self.answers["ipconfig.exe"] = mock.Mock(
            returncode=1, stdout="Could not display the DNS Resolver Cache.\r\n")
        self.assertEqual(argus.fetch()["dns"]["state"], "unavailable")

    def test_a_failed_firewall_read_is_unavailable(self):
        self.answers["netsh.exe"] = mock.Mock(returncode=1, stdout="")
        fw = argus.fetch()["firewall"]
        self.assertEqual(fw["state"], "unavailable")
        self.assertEqual(fw["domain"], "—")


# ── state shared by Kairos's worker and Pythia's (§6) ─────────────────────────

class TestSharedState(_FakedSeams):

    def test_a_second_fetch_waits_for_the_first(self):
        # Kairos's tick and a get_machine_status call can overlap. Unlocked,
        # two fetches raised 7 times in 3,000 calls (measured 2026-11-21).
        entered, release, calls = threading.Event(), threading.Event(), []

        def slow(kind):
            calls.append(threading.current_thread().name)
            if len(calls) == 1:
                entered.set()
                release.wait(5)
            return list(self.conns)

        argus.psutil.net_connections.side_effect = slow
        first = threading.Thread(target=argus.fetch, name="kairos", daemon=True)
        second = threading.Thread(target=argus.fetch, name="pythia", daemon=True)
        try:
            first.start()
            self.assertTrue(entered.wait(5))
            second.start()
            second.join(0.3)          # long enough to reach the table if unlocked
            self.assertEqual(calls, ["kairos"])
        finally:
            release.set()
            first.join(5)
            second.join(5)
        self.assertEqual(calls, ["kairos", "pythia"])

    def test_what_fetch_hands_out_is_a_copy(self):
        # The panel gets the snapshot through Kairos's queue while Argus keeps
        # its cached blocks and timeline; changing one must not reach the other.
        argus.fetch()                                       # seed
        self.conns.append(_Conn(-1, socket.AF_INET, socket.SOCK_STREAM,
                                _Addr("10.0.0.2", 50002),
                                _Addr("198.51.100.7", 443), "ESTABLISHED", 102))
        snap = argus.fetch()
        snap["dns"]["entries"].clear()
        snap["firewall"]["state"] = "changed"
        snap["timeline"][0]["proc"] = "changed"
        again = argus.fetch()                               # blocks still cached
        self.assertEqual(len(again["dns"]["entries"]), 2)
        self.assertEqual(again["firewall"]["state"], "ok")
        self.assertEqual(again["timeline"][0]["proc"], "p102.exe")


# ── the timeline file ─────────────────────────────────────────────────────────

class TestTimelineFile(_FakedSeams):

    _EVENT = {"t": 1700000000.0, "event": "open", "proc": "a.exe",
              "raddr": "198.51.100.7:443"}

    def _write(self, data):
        with open(argus._TIMELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_importing_argus_opens_no_file(self):
        # Importing Pythia imports every tool, so a read at import ran before
        # any test could point the file elsewhere (BACKLOG, 2026-11-19).
        spec = importlib.util.spec_from_file_location("argus_fresh", argus.__file__)
        fresh = importlib.util.module_from_spec(spec)
        with mock.patch("builtins.open", wraps=open) as opened:
            spec.loader.exec_module(fresh)
        self.assertEqual(opened.call_args_list, [])

    def test_the_first_fetch_reads_the_file_where_it_points(self):
        self._write([self._EVENT])
        self.assertEqual(argus.fetch()["timeline"], [self._EVENT])

    def test_entries_the_panel_could_not_draw_are_dropped(self):
        # The panel draws these on the main thread, where a raise would stop
        # Kairos's tick for every panel (measured 2026-11-21; BACKLOG).
        junk = ["x", 5, {"t": "soon", "event": "open", "proc": "a", "raddr": "b"},
                {"t": float("inf"), "event": "open", "proc": "a", "raddr": "b"},
                {"t": 1.0, "event": "opened", "proc": "a", "raddr": "b"},
                {"t": 1.0, "event": "close", "proc": None, "raddr": "b"}]
        self._write(junk + [self._EVENT])
        with self.assertLogs("METIS.argus", "WARNING"):
            argus._load_timeline()
        self.assertEqual(list(argus._timeline), [self._EVENT])

    def test_a_file_holding_no_list_loads_nothing(self):
        for data in ("abc", {"t": 1.0}):
            with self.subTest(data=data):
                argus._timeline.clear()
                self._write(data)
                with self.assertLogs("METIS.argus", "WARNING"):
                    argus._load_timeline()
                self.assertEqual(list(argus._timeline), [])


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
        with self.assertLogs("METIS.argus", "WARNING"):
            with mock.patch.object(argus, "fetch",
                                   side_effect=OSError("net table gone")):
                out = argus.handle()
        self.assertEqual(out, {"error": "unavailable: network snapshot raised OSError"})

    def test_a_cache_argus_could_not_read_has_no_count(self):
        # A refused or failed read has no entries; reporting 0 names would be
        # false, so the key is left out (the owner's pick, 2026-11-21).
        for state in ("needs_admin", "unavailable"):
            with self.subTest(state=state):
                snap = dict(self._SNAP, dns={"as_of": 1.0, "state": state,
                                             "entries": []})
                with mock.patch.object(argus, "fetch", return_value=snap):
                    out = argus.handle()
                self.assertNotIn("dns_cached_names", out)
                self.assertIn("top_connections", out)
        with mock.patch.object(argus, "fetch", return_value=self._SNAP):
            keys = list(argus.handle())
        self.assertEqual(keys[-2:], ["dns_cached_names", "top_connections"])


class TestFetchRaises(_FakedSeams):
    """fetch()'s one raise, on the faked seams: the first fetch() reads the
    timeline file, so a real fetch() would read the checkout's own."""

    def test_fetch_raises_on_wholesale_net_connections_failure(self):
        # The documented single raise: if the connection table itself can't be
        # read, fetch() lets it through so Kairos delivers None (panel stays stale).
        argus.psutil.net_connections.side_effect = OSError("boom")
        with self.assertRaises(OSError):
            argus.fetch()


if __name__ == "__main__":
    unittest.main()
