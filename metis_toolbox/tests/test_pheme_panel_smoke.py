"""
test_pheme_panel_smoke.py — the Scriptorium tabs: rendering, routing, and the
error-text map.

Headless Tk per CONVENTIONS §10: a real tk.Tk() root we withdraw() so no window
flashes, driven with update_idletasks() instead of mainloop(). No exception
raised == pass. Nothing touches the network — the panel is fed hand-shaped
Kairos payloads through update().

The guard that matters most is TestErrorTextCoversEveryCode: it reads the
per-feed identifiers out of tools/pheme.py's own AST — scoped to _fetch_feed,
because handle()'s slugs go to Pythia and never reach this panel — and compares
them with _ERROR_TEXT's keys, so the two halves cannot drift (§10: two live
sources, no hand-kept third copy).

    python -X utf8 -m unittest tests.test_pheme_panel_smoke
"""

import ast
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
from panels import pheme_panel
from panels.pheme_panel import PhemePanel

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _per_feed_error_slugs():
    """Every error slug _fetch_feed can return, read from tools/pheme.py's AST.

    Scoped to the _fetch_feed function on purpose: fetch() forwards its dicts
    to this panel verbatim, while handle()'s own errors (config_unreadable,
    unknown_feed, ...) are Pythia-facing and deliberately unmapped here.
    """
    with open(os.path.join(_APP_ROOT, "tools", "pheme.py"),
              encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    fetch_feed = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_fetch_feed")
    slugs = set()
    for node in ast.walk(fetch_feed):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and key.value == "error"):
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                slugs.add(value.value.split(": ")[0])
            elif isinstance(value, ast.JoinedStr) and value.values:
                head = value.values[0]
                if isinstance(head, ast.Constant) and isinstance(head.value, str):
                    slugs.add(head.value.split(": ")[0])
    return slugs


class TestErrorTextCoversEveryCode(unittest.TestCase):
    def test_the_ast_scan_actually_finds_codes(self):
        # A scan that finds nothing would make the test below pass vacuously.
        self.assertGreater(len(_per_feed_error_slugs()), 3)

    def test_map_and_tool_stay_in_lockstep(self):
        self.assertEqual(
            _per_feed_error_slugs(), set(pheme_panel._ERROR_TEXT),
            "panels/pheme_panel.py:_ERROR_TEXT and tools/pheme.py:_fetch_feed "
            "disagree about which error identifiers exist — an unmapped code "
            "renders raw at the user, a stale key is a mapping for something "
            "Pheme cannot emit")

    def test_detail_is_kept_and_the_slug_is_translated(self):
        self.assertEqual(pheme_panel._error_text("http_error: 404"),
                         "feed returned HTTP: 404")

    def test_bare_slug_needs_no_detail(self):
        self.assertEqual(pheme_panel._error_text("feed_timeout"),
                         "feed timed out")

    def test_an_unmapped_code_is_surfaced_raw_not_swallowed(self):
        # Echo's fallback rule: a code nobody mapped is still shown.
        self.assertEqual(pheme_panel._error_text("brand_new_code"),
                         "brand_new_code")
        self.assertEqual(pheme_panel._error_text("brand_new_code: 3"),
                         "brand_new_code: 3")


# ─────────────────────────────────────────────────────────────────────────────
#  Tk smoke
# ─────────────────────────────────────────────────────────────────────────────

def _config_ids():
    with open(os.path.join(_APP_ROOT, "config", "pheme_rumormill.json"),
              encoding="utf-8") as fh:
        return [f["id"] for f in json.load(fh)["feeds"]]


def _story(title="A headline", url="https://example.com/a", author="",
           date="", domain="example.com"):
    return {"title": title, "url": url, "author": author, "date": date,
            "domain": domain}


class TestPhemePanelSmoke(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.panel = PhemePanel(self.root)
        self.panel.pack()
        self.ids = _config_ids()

    def tearDown(self):
        self.panel.destroy()

    def _inner(self, fid):
        return self.panel._tabs[fid]["scroll"].inner

    def _footer(self, fid):
        return self.panel._tabs[fid]["footer"].cget("text")

    # ── construction ────────────────────────────────────────────────────────

    def test_one_tab_per_configured_feed_first_active(self):
        # Lockstep with the shipped config, not a hardcoded count (§10).
        self.assertEqual(list(self.panel._tabs), self.ids)
        self.assertEqual(self.panel._active, self.ids[0])

    # ── update(None) — the Kairos stale contract ────────────────────────────

    def test_none_marks_every_tab_stale(self):
        self.panel.update(None)
        self.root.update_idletasks()
        for fid in self.ids:
            self.assertEqual(self._footer(fid), "stale")

    # ── routing one payload across tabs ─────────────────────────────────────

    def test_stories_error_and_absence_route_to_their_own_tabs(self):
        healthy, dead, empty = self.ids[0], self.ids[1], self.ids[2]
        self.panel.update({"feeds": {
            healthy: {"stories": [_story(), _story(title="Second",
                                                  author="Jane Doe",
                                                  date="Jul 23")]},
            dead:    {"error": "http_error: 404"},
            empty:   {"stories": []},
            # the rest of the ids are absent -> stale
        }})
        self.root.update_idletasks()

        # Healthy: 2 stories x (row frame + divider) = 4 children.
        self.assertEqual(len(self._inner(healthy).winfo_children()), 4)
        self.assertTrue(self._footer(healthy).startswith("updated "))

        # Dead: the slug is mapped to human text, never rendered raw.
        (label,) = self._inner(dead).winfo_children()
        self.assertEqual(label.cget("text"), "feed returned HTTP: 404")
        self.assertNotIn("http_error", label.cget("text"))
        self.assertTrue(self._footer(dead).startswith("failed "))

        # Empty: the calm placeholder.
        (placeholder,) = self._inner(empty).winfo_children()
        self.assertEqual(placeholder.cget("text"), "no stories available")

        # Absent from the payload: stale, exactly like data=None.
        for fid in self.ids[3:]:
            self.assertEqual(self._footer(fid), "stale")

    def test_an_unknown_feed_id_in_the_payload_is_ignored(self):
        self.panel.update({"feeds": {"never_configured": {"stories": []}}})
        self.root.update_idletasks()   # reaching here without a raise == pass

    def test_a_second_update_rebuilds_rather_than_appends(self):
        fid = self.ids[0]
        payload = {"feeds": {fid: {"stories": [_story()]}}}
        self.panel.update(payload)
        self.panel.update(payload)
        self.root.update_idletasks()
        self.assertEqual(len(self._inner(fid).winfo_children()), 2)

    # ── story rows ──────────────────────────────────────────────────────────

    def test_a_story_without_meta_renders_only_title_and_divider(self):
        fid = self.ids[0]
        bare = _story(author="", date="", domain="")
        self.panel.update({"feeds": {fid: {"stories": [bare]}}})
        self.root.update_idletasks()
        outer, _divider = self._inner(fid).winfo_children()
        # outer holds line1 only — no meta line was packed.
        self.assertEqual(len(outer.winfo_children()), 1)

    def test_a_storyless_url_renders_unclickable(self):
        fid = self.ids[0]
        self.panel.update({"feeds": {fid: {"stories": [_story(url="")]}}})
        self.root.update_idletasks()
        outer = self._inner(fid).winfo_children()[0]
        line1 = outer.winfo_children()[0]
        title = [w for w in line1.winfo_children()
                 if isinstance(w, tk.Label) and w.cget("text") != "↗"][0]
        self.assertNotEqual(title.cget("cursor"), "hand2")

    # ── tab switching ───────────────────────────────────────────────────────

    def test_switching_tabs_flips_the_active_colors(self):
        first, second = self.ids[0], self.ids[1]
        self.panel._show_tab(second)
        self.root.update_idletasks()
        self.assertEqual(self.panel._active, second)
        self.assertEqual(self.panel._tabs[second]["tab"].cget("fg"),
                         theme.C["text1"])
        self.assertEqual(self.panel._tabs[second]["line"].cget("bg"),
                         theme.C["coral"])
        self.assertEqual(self.panel._tabs[first]["tab"].cget("fg"),
                         theme.C["text3"])
        self.assertEqual(self.panel._tabs[first]["line"].cget("bg"),
                         theme.C["border"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
