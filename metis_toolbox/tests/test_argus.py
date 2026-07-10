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


if __name__ == "__main__":
    unittest.main()
