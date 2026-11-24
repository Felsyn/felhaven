"""
test_argus_panel_smoke.py — headless Tk smoke tests for panels/argus_panel.py.

The ARGUS tab's first tests of its own (2026-11-21, the V0.03 walkthrough's
S22): how it shows a DNS cache Windows won't open to a normal user, what the
summary dot says, and None from Kairos. No window flashes: tk.Tk() +
withdraw() + update_idletasks(), no mainloop. The snapshot is hand-built in
argus.fetch()'s shape; the needs_admin state is what Argus reports for the
refusal measured on Windows 11 that day. Run from the package root:

    python -X utf8 -m unittest tests.test_argus_panel_smoke
"""

import os
import sys
import tkinter as tk
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import theme
from panels.argus_panel import ArgusPanel
from theme import C


def _snap(dns_state="ok", entries=None):
    """An argus.fetch()-shaped snapshot with every source answering."""
    if entries is None:
        entries = ([{"name": "example.com", "records": ["93.184.215.14"]},
                    {"name": "example.org", "records": ["cdn.example.net"]}]
                   if dns_state == "ok" else [])
    return {
        "as_of": 1.0,
        "privilege": "user",
        "summary": {"established": 1, "listening": 1, "other": 0,
                    "unresolved_pids": 0},
        "connections": [{"pid": 101, "proc": "p101.exe", "family": "IPv4",
                         "laddr": "10.0.0.2:50000",
                         "raddr": "93.184.215.14:443",
                         "status": "ESTABLISHED"}],
        "listening": [{"pid": 4, "proc": "System", "proto": "TCP",
                       "laddr": "0.0.0.0:445", "state": "listening"}],
        "traffic": {"window_s": 5.0, "up_bps": 1200, "down_bps": 34000,
                    "per_nic": {}},
        "dns": {"as_of": 1.0, "state": dns_state, "entries": entries},
        "firewall": {"as_of": 1.0, "state": "ok", "domain": "on",
                     "private": "on", "public": "on"},
        "timeline": [],
    }


class TestArgusPanel(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()          # no window flashes during the run
        theme._init_fonts(cls.root)  # FONTS is empty until a root exists

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.panel = ArgusPanel(self.root)
        self.addCleanup(self.panel.destroy)

    def _show(self, data, open_dns=False):
        self.panel.update(data)
        if open_dns and self.panel._sections["dns"].collapsed:
            self.panel._sections["dns"].toggle()
        self.root.update_idletasks()

    def _texts(self, key):
        """Every label's text in one section's rows."""
        out, todo = [], list(self.panel._sections[key].content.winfo_children())
        while todo:
            w = todo.pop(0)
            if isinstance(w, tk.Label):
                out.append(w.cget("text"))
            todo.extend(w.winfo_children())
        return out

    def _dot(self):
        return self.panel._dot.itemcget(self.panel._dot_id, "fill")

    def _dns_title(self):
        return self.panel._sections["dns"]._title.cget("text")

    def test_a_refused_dns_cache_says_it_needs_an_administrator(self):
        self._show(_snap("needs_admin"), open_dns=True)
        self.assertEqual(self._texts("dns"),
                         ["Windows shows the DNS cache only to an administrator"])

    def test_a_failed_dns_read_keeps_its_own_words(self):
        self._show(_snap("unavailable"), open_dns=True)
        self.assertEqual(self._texts("dns"),
                         ["cache unavailable (DNS Client service stopped?)"])

    def test_a_refused_dns_cache_leaves_the_dot_teal(self):
        # A permission, not a failure: the dot stays teal for needs_admin and
        # goes amber only when a source fails.
        self._show(_snap("needs_admin"))
        self.assertEqual(self._dot(), C["teal"])
        self._show(_snap("unavailable"))
        self.assertEqual(self._dot(), C["amber"])

    def test_the_dns_title_counts_only_a_cache_it_read(self):
        for state, title in (("ok", "DNS CACHE (2)"), ("empty", "DNS CACHE (0)"),
                             ("needs_admin", "DNS CACHE"),
                             ("unavailable", "DNS CACHE")):
            with self.subTest(state=state):
                self._show(_snap(state))
                self.assertEqual(self._dns_title(), title)

    def test_none_holds_the_rows_and_greys_the_dot(self):
        self._show(_snap())
        rows = self._texts("connections")
        self._show(None)
        self.assertTrue(self.panel._summary.cget("text").startswith("stale"))
        self.assertEqual(self._dot(), C["text3"])
        self.assertEqual(self._texts("connections"), rows)
        self.assertIn("93.184.215.14:443", rows)


if __name__ == "__main__":
    unittest.main()
