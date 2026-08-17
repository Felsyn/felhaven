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
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
from panels import pheme_panel
from panels.pheme_panel import PhemePanel
from tools import pheme

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
           date="", domain="example.com", ts="2026-07-24T12:00:00Z"):
    return {"title": title, "url": url, "author": author, "date": date,
            "domain": domain, "ts": ts}


class _PanelTestCase(unittest.TestCase):
    """Headless Tk plus a tmpdir for both runtime files.

    Pheme's read state and the panel's collapse state are redirected before the
    panel is built (its __init__ reads both), and Pheme's in-memory seen-set is
    emptied — otherwise these tests would read, and dim against, whatever the
    developer running them has actually clicked on.
    """

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()          # no window flashes during the run
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.sections_path = os.path.join(self.dir, "pheme_sections.json")
        for module, attr, value in (
                (pheme_panel, "_SECTIONS_PATH", self.sections_path),
                (pheme, "_READ_PATH", os.path.join(self.dir, "pheme_read.json"))):
            p = mock.patch.object(module, attr, value)
            p.start()
            self.addCleanup(p.stop)
        pheme._read_order.clear()
        pheme._read_set.clear()
        self.addCleanup(pheme._read_order.clear)
        self.addCleanup(pheme._read_set.clear)

        self.ids = _config_ids()
        self.panel = self._build()

    def _build(self):
        panel = PhemePanel(self.root)
        panel.pack()
        self.addCleanup(panel.destroy)
        return panel

    # ── readers over the rendered surfaces ──────────────────────────────────

    def _river_titles(self):
        """Titles currently in Scrapstream, in render order. Meta lines are
        indented in the Copy-all mirror, which is what distinguishes them."""
        return [line for line in self.panel._text_river
                if not line.startswith("    ")]

    def _sections(self):
        """One frame per feed section, in config order."""
        return self.panel._fonts.inner.winfo_children()

    def _section_header_texts(self, index):
        header = self._sections()[index].winfo_children()[0]
        return [w.cget("text") for w in header.winfo_children()]

    def _feeds_payload(self, **per_feed):
        return {"feeds": per_feed}


# ─────────────────────────────────────────────────────────────────────────────
#  Construction
# ─────────────────────────────────────────────────────────────────────────────

class TestConstruction(_PanelTestCase):
    def test_two_surfaces_with_scrapstream_first_and_default(self):
        self.assertEqual(list(self.panel._bodies),
                         ["scrapstream", "sourcefonts"])
        self.assertEqual(self.panel._active, "scrapstream")

    def test_one_section_per_configured_feed(self):
        # Lockstep with the shipped config, not a hardcoded seven (§10).
        self.panel.update(self._feeds_payload())
        self.root.update_idletasks()
        self.assertEqual(len(self._sections()), len(self.ids))

    def test_the_scrollbars_are_phosphor_not_tk(self):
        for surface in (self.panel._river, self.panel._fonts):
            bars = [w for w in surface.winfo_children()
                    if isinstance(w, theme.PhosphorScroll)]
            self.assertEqual(len(bars), 1)
            self.assertEqual(
                [w for w in surface.winfo_children()
                 if isinstance(w, tk.Scrollbar)], [])


# ─────────────────────────────────────────────────────────────────────────────
#  Scrapstream — the merge
# ─────────────────────────────────────────────────────────────────────────────

class TestScrapstreamMerge(_PanelTestCase):
    def test_the_river_is_newest_first_across_feeds(self):
        a, b, c = self.ids[0], self.ids[1], self.ids[2]
        self.panel.update(self._feeds_payload(**{
            a: {"stories": [_story(title="middle", ts="2026-07-20T00:00:00Z")]},
            b: {"stories": [_story(title="newest", ts="2026-07-30T00:00:00Z")]},
            c: {"stories": [_story(title="oldest", ts="2026-07-10T00:00:00Z")]},
        }))
        self.root.update_idletasks()
        self.assertEqual(self._river_titles(), ["newest", "middle", "oldest"])

    def test_same_day_stories_order_by_time_not_by_date(self):
        # The I3 proof: a day-level key would make these a tie, and their order
        # arbitrary. Both carry the same display date on purpose.
        a, b = self.ids[0], self.ids[1]
        self.panel.update(self._feeds_payload(**{
            a: {"stories": [_story(title="morning", date="Jul 24",
                                   ts="2026-07-24T06:00:00Z")]},
            b: {"stories": [_story(title="evening", date="Jul 24",
                                   ts="2026-07-24T21:00:00Z")]},
        }))
        self.root.update_idletasks()
        self.assertEqual(self._river_titles(), ["evening", "morning"])

    def test_a_feed_contributes_at_most_the_per_source_cap(self):
        a = self.ids[0]
        many = [_story(title=f"s{n}", url=f"https://example.com/{n}",
                       ts=f"2026-07-{n + 1:02d}T00:00:00Z") for n in range(30)]
        self.panel.update(self._feeds_payload(**{a: {"stories": many}}))
        self.root.update_idletasks()
        self.assertEqual(len(self._river_titles()),
                         pheme_panel._SCRAPSTREAM_PER_SOURCE)

    def test_the_cap_is_applied_before_the_sort(self):
        # A feed whose newest story sits LAST in its own list. Capping first
        # takes the leading five and that newest story never surfaces; sorting
        # first would promote it. This is the discriminator between the two.
        a = self.ids[0]
        stories = [_story(title=f"s{n}", url=f"https://example.com/{n}",
                          ts=f"2026-07-{n + 1:02d}T00:00:00Z")
                   for n in range(10)]
        buried = _story(title="buried-newest", url="https://example.com/late",
                        ts="2026-07-31T00:00:00Z")
        self.panel.update(self._feeds_payload(**{
            a: {"stories": stories[:5] + [buried] + stories[5:]}}))
        self.root.update_idletasks()
        titles = self._river_titles()
        self.assertEqual(len(titles), pheme_panel._SCRAPSTREAM_PER_SOURCE)
        self.assertNotIn("buried-newest", titles)

    def test_a_dateless_story_sorts_last_and_is_never_dropped(self):
        a, b = self.ids[0], self.ids[1]
        self.panel.update(self._feeds_payload(**{
            a: {"stories": [_story(title="undated", ts="")]},
            b: {"stories": [_story(title="old", ts="2026-01-01T00:00:00Z")]},
        }))
        self.root.update_idletasks()
        self.assertEqual(self._river_titles(), ["old", "undated"])

    def test_several_dateless_stories_keep_config_order(self):
        a, b = self.ids[0], self.ids[1]
        self.panel.update(self._feeds_payload(**{
            a: {"stories": [_story(title="first-feed", ts="")]},
            b: {"stories": [_story(title="second-feed", ts="")]},
        }))
        self.root.update_idletasks()
        self.assertEqual(self._river_titles(), ["first-feed", "second-feed"])

    def test_a_story_missing_the_ts_key_entirely_does_not_raise(self):
        a = self.ids[0]
        legacy = {"title": "no ts key", "url": "https://example.com/a",
                  "author": "", "date": "", "domain": ""}
        self.panel.update(self._feeds_payload(**{a: {"stories": [legacy]}}))
        self.root.update_idletasks()
        self.assertEqual(self._river_titles(), ["no ts key"])

    def test_a_second_update_rebuilds_rather_than_appends(self):
        a = self.ids[0]
        payload = self._feeds_payload(**{a: {"stories": [_story()]}})
        self.panel.update(payload)
        self.panel.update(payload)
        self.root.update_idletasks()
        self.assertEqual(len(self._river_titles()), 1)

    def test_an_unknown_feed_id_in_the_payload_is_ignored(self):
        self.panel.update(self._feeds_payload(never_configured={"stories": []}))
        self.root.update_idletasks()   # reaching here without a raise == pass
        self.assertEqual(self._river_titles(), [])


# ─────────────────────────────────────────────────────────────────────────────
#  Partial failure
# ─────────────────────────────────────────────────────────────────────────────

class TestPartialFailure(_PanelTestCase):
    def _all_but_one_healthy(self):
        payload = {fid: {"stories": [_story(title=fid,
                                            url=f"https://example.com/{fid}")]}
                   for fid in self.ids}
        payload[self.ids[3]] = {"error": "feed_timeout"}
        return {"feeds": payload}

    def test_one_dead_feed_leaves_the_rest_rendering(self):
        self.panel.update(self._all_but_one_healthy())
        self.root.update_idletasks()
        self.assertEqual(len(self._river_titles()), len(self.ids) - 1)
        self.assertNotIn(self.ids[3], self._river_titles())

    def test_one_dead_feed_is_counted_in_the_footer(self):
        self.panel.update(self._all_but_one_healthy())
        self.root.update_idletasks()
        self.assertEqual(self.panel._fail_lbl.cget("text"), " · ⚠ 1 failed")
        self.assertTrue(self.panel._footer.cget("text").startswith("updated "))

    def test_the_failing_source_is_named_in_source_fonts(self):
        self.panel.update(self._all_but_one_healthy())
        self.root.update_idletasks()
        texts = self._section_header_texts(3)
        self.assertIn("⚠ feed timed out", texts)
        # The slug is mapped to human text, never rendered raw.
        self.assertNotIn("feed_timeout", texts)

    def test_every_feed_dead_renders_an_empty_river_with_the_full_count(self):
        self.panel.update({"feeds": {fid: {"error": "network_error"}
                                     for fid in self.ids}})
        self.root.update_idletasks()
        self.assertEqual(self._river_titles(), [])
        self.assertEqual(self.panel._fail_lbl.cget("text"),
                         f" · ⚠ {len(self.ids)} failed")
        (placeholder,) = self.panel._river.inner.winfo_children()
        self.assertEqual(placeholder.cget("text"), "no stories available")

    def test_a_feed_absent_from_the_payload_counts_as_failed(self):
        # In a merged river an absent feed looks exactly like a broken one.
        self.panel.update(self._feeds_payload(
            **{self.ids[0]: {"stories": [_story()]}}))
        self.root.update_idletasks()
        self.assertEqual(self.panel._fail_lbl.cget("text"),
                         f" · ⚠ {len(self.ids) - 1} failed")

    def test_none_is_stale_with_no_failure_count(self):
        self.panel.update(None)
        self.root.update_idletasks()
        self.assertEqual(self.panel._footer.cget("text"), "stale")
        self.assertEqual(self.panel._fail_lbl.cget("text"), "")
        self.assertEqual(self._river_titles(), [])


# ─────────────────────────────────────────────────────────────────────────────
#  Read state
# ─────────────────────────────────────────────────────────────────────────────

class TestReadState(_PanelTestCase):
    URL = "https://example.com/shared"

    def _render_shared_story(self):
        # ids[0]'s section is expanded by default, so this story renders in
        # BOTH surfaces — which is the point of the test.
        self.panel.update(self._feeds_payload(
            **{self.ids[0]: {"stories": [_story(url=self.URL)]}}))
        self.root.update_idletasks()
        return pheme.read_key(self.URL)

    def test_an_unread_story_renders_bright(self):
        key = self._render_shared_story()
        title, meta = self.panel._rows_river[key][0]
        self.assertEqual(title.cget("fg"), theme.C["text1"])
        self.assertEqual(meta.cget("fg"), theme.C["text2"])

    def test_the_story_is_registered_in_both_surfaces(self):
        key = self._render_shared_story()
        self.assertEqual(len(self.panel._rows_river[key]), 1)
        self.assertEqual(len(self.panel._rows_fonts[key]), 1)

    def test_the_title_is_wired_to_open_on_click(self):
        key = self._render_shared_story()
        title = self.panel._rows_river[key][0][0]
        self.assertTrue(title.bind("<Button-1>"))
        self.assertEqual(title.cget("cursor"), "hand2")

    def test_marking_read_dims_both_surfaces_at_once(self):
        key = self._render_shared_story()
        self.panel._mark_read(key, self.URL)
        self.root.update_idletasks()
        for rows in (self.panel._rows_river, self.panel._rows_fonts):
            title, meta = rows[key][0]
            self.assertEqual(title.cget("fg"), theme.C["text3"])
            self.assertEqual(meta.cget("fg"), theme.C["text3"])

    def test_read_state_survives_a_rerender(self):
        key = self._render_shared_story()
        self.panel._mark_read(key, self.URL)
        self._render_shared_story()
        self.assertEqual(self.panel._rows_river[key][0][0].cget("fg"),
                         theme.C["text3"])

    def test_read_state_survives_a_new_panel(self):
        key = self._render_shared_story()
        self.panel._mark_read(key, self.URL)
        pheme._read_order.clear()
        pheme._read_set.clear()
        pheme._load_read()
        fresh = self._build()
        fresh.update(self._feeds_payload(
            **{self.ids[0]: {"stories": [_story(url=self.URL)]}}))
        self.root.update_idletasks()
        self.assertEqual(fresh._rows_river[key][0][0].cget("fg"),
                         theme.C["text3"])

    def test_a_story_with_no_url_is_not_registered(self):
        self.panel.update(self._feeds_payload(
            **{self.ids[0]: {"stories": [_story(url="")]}}))
        self.root.update_idletasks()
        self.assertEqual(self.panel._rows_river, {})

    def test_marking_a_destroyed_row_does_not_raise(self):
        key = self._render_shared_story()
        self.panel._river.clear()
        self.panel._mark_read(key, self.URL)   # widgets are gone; must not raise


# ─────────────────────────────────────────────────────────────────────────────
#  Source Fonts — collapse state
# ─────────────────────────────────────────────────────────────────────────────

class TestCollapseState(_PanelTestCase):
    def test_the_first_section_is_open_by_default(self):
        # Never all-closed on a fresh install: seven headers over no content is
        # a dead screen.
        self.assertEqual(self.panel._expanded, {self.ids[0]})

    def test_a_collapsed_sections_body_is_not_packed(self):
        self.panel.update(self._feeds_payload(
            **{fid: {"stories": [_story()]} for fid in self.ids}))
        self.root.update_idletasks()
        open_body   = self._sections()[0].winfo_children()[-1]
        closed_body = self._sections()[1].winfo_children()[-1]
        self.assertTrue(open_body.winfo_manager())
        self.assertFalse(closed_body.winfo_manager())

    def test_toggling_persists_and_a_new_panel_restores_it(self):
        self.panel._toggle(self.ids[1])
        self.assertEqual(self.panel._expanded, {self.ids[0], self.ids[1]})
        self.assertEqual(pheme_panel._load_sections(self.panel._feeds),
                         {self.ids[0], self.ids[1]})
        self.assertEqual(self._build()._expanded, {self.ids[0], self.ids[1]})

    def test_toggling_twice_returns_to_collapsed(self):
        self.panel._toggle(self.ids[1])
        self.panel._toggle(self.ids[1])
        self.assertEqual(self.panel._expanded, {self.ids[0]})

    def test_an_explicit_all_closed_state_is_honoured(self):
        # Overriding a choice the user made by hand is worse than the empty
        # screen the DEFAULT guards against.
        self.panel._toggle(self.ids[0])
        self.assertEqual(self._build()._expanded, set())

    def test_the_write_is_atomic_and_leaves_no_tmp_behind(self):
        self.panel._toggle(self.ids[1])
        self.assertEqual([n for n in os.listdir(self.dir)
                          if n.endswith(".tmp")], [])

    def test_corrupt_json_falls_back_to_the_first_section(self):
        with open(self.sections_path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(self._build()._expanded, {self.ids[0]})

    def test_a_payload_that_is_not_a_list_falls_back(self):
        with open(self.sections_path, "w", encoding="utf-8") as fh:
            json.dump({"expanded": "hackernews"}, fh)
        self.assertEqual(self._build()._expanded, {self.ids[0]})

    def test_a_stale_feed_id_is_dropped_rather_than_kept_as_a_ghost(self):
        with open(self.sections_path, "w", encoding="utf-8") as fh:
            json.dump({"expanded": [self.ids[2], "retired_feed"]}, fh)
        self.assertEqual(self._build()._expanded, {self.ids[2]})


# ─────────────────────────────────────────────────────────────────────────────
#  Right-click menu (theme.themed_menu / popup_menu — the shared helper)
# ─────────────────────────────────────────────────────────────────────────────

class TestCopyAll(_PanelTestCase):
    def test_copy_all_takes_the_active_surface(self):
        self.panel.update(self._feeds_payload(
            **{self.ids[0]: {"stories": [_story(title="Headline",
                                                date="Jul 24")]}}))
        self.root.update_idletasks()

        self.panel._copy_all()
        river = self.root.clipboard_get()
        self.assertIn("Headline", river)
        self.assertIn("Jul 24 · example.com", river)

        self.panel._show_tab("sourcefonts")
        self.panel._copy_all()
        self.assertIn("Headline", self.root.clipboard_get())

    def test_a_collapsed_section_contributes_nothing_to_copy_all(self):
        self.panel.update(self._feeds_payload(
            **{self.ids[1]: {"stories": [_story(title="Hidden")]}}))
        self.root.update_idletasks()
        self.panel._show_tab("sourcefonts")
        self.assertNotIn("Hidden", "\n".join(self.panel._visible_lines()))

    def test_copy_all_on_an_empty_surface_is_a_no_op(self):
        self.panel.update(None)
        self.root.update_idletasks()
        self.assertEqual(self.panel._visible_lines(), [])
        self.panel._copy_all()          # must not raise


if __name__ == "__main__":
    unittest.main(verbosity=2)
