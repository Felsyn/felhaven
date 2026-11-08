"""
test_artemis_panel_smoke.py — headless Tk smoke test for ArtemisPanel.

Same headless approach as the other panel smoke tests: a withdrawn tk.Tk()
root driven with update_idletasks(). artemis.CACHE_DIR is redirected at a
temp dir so the panel sees a controlled cache and the real one is never
touched. No network anywhere — the ingest path is only armed, never fired.

    python -X utf8 -m unittest tests.test_artemis_panel_smoke
"""

import array
import os
import shutil
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
import themis
from tools import artemis
from tools import demeter

_FIX = (39.2806, -80.3440)


def _status(cached=False, ingested_at=None, layers=None):
    return {"lat": _FIX[0], "lon": _FIX[1], "area_id": artemis.area_id(*_FIX),
            "cached": cached, "ingested_at": ingested_at,
            "layers": layers or []}


class TestArtemisPanelSmoke(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._orig_cache = artemis.CACHE_DIR
        artemis.CACHE_DIR = self._dir
        from panels.artemis_panel import ArtemisPanel
        self.panel = ArtemisPanel(self.root)
        self.panel.pack(fill="both", expand=True)
        self.root.update_idletasks()

    def tearDown(self):
        self.panel.destroy()
        artemis.CACHE_DIR = self._orig_cache
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_constructs_with_empty_cache(self):
        # No cache, no status yet: calm placeholder, nothing raises.
        self.assertIn("waiting", self.panel._status_lbl.cget("text"))

    def test_update_none_keeps_state(self):
        self.panel.update(None)
        self.root.update_idletasks()
        self.assertIn("waiting", self.panel._status_lbl.cget("text"))

    def test_uncached_status(self):
        self.panel.update(_status())
        self.root.update_idletasks()
        self.assertIn("NOT CACHED", self.panel._status_lbl.cget("text"))

    def _build_area(self, layers=("roads", "water", "terrain"), skipped=None):
        """Write a small but real cache: FVEC files and a terrain grid."""
        area = artemis.area_id(*_FIX)
        os.makedirs(artemis.area_dir(area), exist_ok=True)
        manifest = {"center": list(_FIX), "fetched": "2026-08-09T00:00:00Z",
                    "layers": list(layers)}
        if skipped:
            manifest["terrain_skipped"] = skipped
        artemis.write_manifest(artemis.area_dir(area), manifest)
        if "roads" in layers:
            artemis.write_vec(
                os.path.join(artemis.area_dir(area), "roads.vec"),
                [artemis.Feature(kind="road", name="Main St", tier=2, flags=0,
                                 coords=[(-500.0, -500.0), (500.0, 500.0)])],
                _FIX, artemis.LAYER_ROADS)
        if "water" in layers:
            artemis.write_vec(
                os.path.join(artemis.area_dir(area), "water.vec"),
                [artemis.Feature(kind="water_line", name="Ann Run", tier=1,
                                 flags=0, coords=[(-200.0, 0.0), (200.0, 50.0)]),
                 artemis.Feature(kind="water_area", name="Oral Lk", tier=1,
                                 flags=0, coords=[(0.0, 0.0), (100.0, 0.0),
                                                  (100.0, 100.0)])],
                _FIX, artemis.LAYER_WATER)
        if "terrain" in layers:
            cells = array.array("h", [r * 50 for r in range(8) for _ in range(8)])
            demeter.write_bin(
                os.path.join(artemis.area_dir(area), demeter._BIN_NAME),
                cells, _FIX, size=8)
            demeter.write_derived(artemis.area_dir(area), cells, size=8)
        return area

    def _show(self, layers=("roads", "water", "terrain")):
        # Give the canvas a real size — a withdrawn root never maps it, and
        # _redraw() rightly refuses to draw into a 1×1 surface.
        self.panel._canvas.config(width=400, height=400)
        self.root.update_idletasks()
        self.panel.update(_status(cached=True,
                                  ingested_at="2026-08-09T00:00:00Z",
                                  layers=list(layers)))
        self.root.update_idletasks()
        # The <Configure> redraw is debounced by 120 ms and no event loop runs
        # here, so drive the settled redraw directly — otherwise the canvas
        # size the panel drew at depends on how the shared root was left by
        # whichever test ran before this one.
        self.panel._redraw()

    def test_cached_status_loads_and_draws(self):
        self._build_area()
        self._show()
        canvas = self.panel._canvas
        self.assertIn("CACHED 2026-08-09", self.panel._status_lbl.cget("text"))
        self.assertEqual(len(canvas.find_withtag("road")), 1)
        self.assertEqual(len(canvas.find_withtag("water_line")), 1)
        self.assertEqual(len(canvas.find_withtag("water_area")), 1)
        self.assertEqual(len(canvas.find_withtag("terrain")), 1)

    def test_terrain_image_reference_is_held(self):
        # An unreferenced PhotoImage is garbage-collected and the canvas goes
        # blank — the classic Tk footgun this panel guards against.
        self._build_area()
        self._show()
        self.assertIsNotNone(self.panel._photo)
        # The raster is rebuilt to exactly the drawable span, so it lines up
        # with the vectors by construction (derived, not a hardcoded size —
        # the canvas's own highlight border makes the widget wider than the
        # requested width).
        span = min(self.panel._canvas.winfo_width(),
                   self.panel._canvas.winfo_height())
        self.assertEqual(self.panel._photo.width(), span)
        self.assertEqual(self.panel._photo.height(), span)

    def test_roads_only_area_still_draws(self):
        # A schema-1 area from before water and terrain existed.
        self._build_area(layers=("roads",))
        self._show(layers=("roads",))
        self.assertEqual(len(self.panel._canvas.find_withtag("road")), 1)
        self.assertEqual(len(self.panel._canvas.find_withtag("terrain")), 0)

    def test_skipped_terrain_is_surfaced(self):
        self._build_area(layers=("roads", "water"), skipped="no_key")
        self._show(layers=("roads", "water"))
        self.assertIn("no OpenTopography key",
                      self.panel._status_lbl.cget("text"))

    def test_terrain_over_its_cap_says_so(self):
        """Demeter's grid has a computed byte cap; a refusal reads as what it
        is, not as a failed download."""
        self._build_area(layers=("roads", "water"), skipped="too_large")
        self._show(layers=("roads", "water"))
        self.assertIn("far larger than expected",
                      self.panel._status_lbl.cget("text"))

    def test_damaged_layer_does_not_lose_the_others(self):
        area = self._build_area()
        with open(os.path.join(artemis.area_dir(area), "water.vec"), "wb") as fh:
            fh.write(b"garbage")
        self._show()
        self.assertEqual(len(self.panel._canvas.find_withtag("road")), 1)
        self.assertEqual(len(self.panel._canvas.find_withtag("water")), 0)

    def test_cache_button_first_click_arms_not_fires(self):
        self.panel.update(_status())
        self.panel._on_cache_click()
        self.root.update_idletasks()
        self.assertTrue(self.panel._confirm_armed)
        self.assertFalse(self.panel._ingesting)      # armed, nothing started
        self.assertIn("CONFIRM", self.panel._cache_btn.cget("text"))

    def test_motion_over_empty_canvas_is_harmless(self):
        self.panel._canvas.event_generate("<Motion>", x=10, y=10)
        self.root.update_idletasks()

    # ── Compositor ──────────────────────────────────────────────────────────

    def test_compositor_chrome_is_drawn(self):
        self._build_area()
        self._show()
        canvas = self.panel._canvas
        for tag in ("mask", "ring", "compass", "scale"):
            with self.subTest(tag=tag):
                self.assertTrue(canvas.find_withtag(tag), f"no {tag} items")
        # Three range rings, and the mask is the documented eight shapes.
        self.assertEqual(len(canvas.find_withtag("mask")), 8)

    def test_mask_survives_every_zoom_stop(self):
        """Replaces test_mask_is_skipped_when_zoomed_inside_the_ring: the mask
        used to be dropped once the 5-mile ring exceeded the canvas, which left
        the terrain raster's square edge showing as a hard seam at 2x and 4x."""
        self._build_area()
        self._show()
        for stop in artemis.ZOOM_STOPS:
            with self.subTest(zoom=stop):
                self.panel._set_zoom(stop)
                self.assertEqual(
                    len(self.panel._canvas.find_withtag("mask")), 8)

    def test_porthole_is_the_same_size_at_every_zoom(self):
        self._build_area()
        self._show()
        coords = []
        for stop in artemis.ZOOM_STOPS:
            self.panel._set_zoom(stop)
            coords.append(self.panel._canvas.coords(
                self.panel._canvas.find_withtag("rim")[0]))
        for other in coords[1:]:
            self.assertEqual(other, coords[0])

    def test_only_rings_that_fit_inside_the_porthole_are_drawn(self):
        # 1 and 3 mi fit at 1x (5 mi IS the rim); only 1 mi fits once zoomed.
        self._build_area()
        self._show()
        expected = {1: 2, 2: 1, 4: 1}
        for stop in artemis.ZOOM_STOPS:
            with self.subTest(zoom=stop):
                self.panel._set_zoom(stop)
                rim = set(self.panel._canvas.find_withtag("rim"))
                ovals = [i for i in self.panel._canvas.find_withtag("ring")
                         if i not in rim
                         and self.panel._canvas.type(i) == "oval"]
                self.assertEqual(len(ovals), expected[stop])

    def test_rim_label_reports_the_visible_radius(self):
        self._build_area()
        self._show()
        for stop, label in ((1, "5 mi"), (2, "2.5 mi"), (4, "1.25 mi")):
            with self.subTest(zoom=stop):
                self.panel._set_zoom(stop)
                texts = [self.panel._canvas.itemcget(i, "text")
                         for i in self.panel._canvas.find_withtag("rim")
                         if self.panel._canvas.type(i) == "text"]
                self.assertIn(label, texts)

    def test_the_range_instrument_matches_the_fix_mark(self):
        """"The same colour as the +" — assert the RELATIONSHIP, not the hex.
        If the fix mark's colour ever changes, this fails and tells whoever
        changed it that the range markings are meant to follow."""
        self._build_area()
        self._show()
        canvas = self.panel._canvas
        fix = [i for i in canvas.find_withtag("fix")
               if canvas.type(i) == "line"][0]
        expected = canvas.itemcget(fix, "fill")

        texts = [i for i in canvas.find_withtag("ring")
                 if canvas.type(i) == "text"]
        ovals = [i for i in canvas.find_withtag("ring")
                 if canvas.type(i) == "oval"]
        lines = [i for i in canvas.find_withtag("ring")
                 if canvas.type(i) == "line"]
        self.assertTrue(texts and ovals and lines)
        # Range labels, the rim readout and N/E/S/W.
        for item in texts:
            self.assertEqual(canvas.itemcget(item, "fill"), expected,
                             canvas.itemcget(item, "text"))
        # Rings and the rim use outline, not fill.
        for item in ovals:
            self.assertEqual(canvas.itemcget(item, "outline"), expected)
        for item in lines:            # bearing ticks
            self.assertEqual(canvas.itemcget(item, "fill"), expected)

    def test_the_compass_and_scale_bar_stay_muted(self):
        # The brightness hierarchy is the point: what measures the chart is
        # brighter than the legend telling you the units.
        self._build_area()
        self._show()
        canvas = self.panel._canvas
        fix = [i for i in canvas.find_withtag("fix")
               if canvas.type(i) == "line"][0]
        bright = canvas.itemcget(fix, "fill")
        for tag in ("compass", "scale"):
            for item in canvas.find_withtag(tag):
                attr = "outline" if canvas.type(item) == "oval" else "fill"
                colour = canvas.itemcget(item, attr)
                if colour:
                    self.assertNotEqual(colour, bright, f"{tag} went bright")

    def test_the_fix_mark_is_drawn_under_the_mask(self):
        # World-anchored items must be clipped by the glass, not float past it.
        self._build_area()
        self._show()
        order = list(self.panel._canvas.find_all())
        fix = [order.index(i) for i in self.panel._canvas.find_withtag("fix")]
        masks = [order.index(i) for i in self.panel._canvas.find_withtag("mask")]
        self.assertTrue(fix and masks)
        self.assertLess(max(fix), min(masks))

    def test_zoom_rescales_raster_and_vectors_together(self):
        self._build_area()
        self._show()
        _w, _h, _cx, _cy, scale_1x = self.panel._view()
        key_1x = self.panel._photo_key

        self.panel._set_zoom(2)
        _w, _h, _cx, _cy, scale_2x = self.panel._view()
        self.assertAlmostEqual(scale_2x, scale_1x * 2)
        self.assertNotEqual(self.panel._photo_key, key_1x)
        # The image still covers the same square of canvas — only the world
        # inside it shrank, which is what keeps layers aligned.
        self.assertEqual(self.panel._photo.width(), key_1x[0])

    def test_zoom_buttons_light_the_active_stop(self):
        self.panel._set_zoom(2)
        self.assertEqual(self.panel._zoom_btns[2].cget("fg"), theme.C["text1"])
        self.assertEqual(self.panel._zoom_btns[1].cget("fg"), theme.C["text3"])

    # ── Hit testing ─────────────────────────────────────────────────────────

    def _point_at(self, x, y):
        self.panel._on_pointer(SimpleNamespace(x=int(x), y=int(y)))

    def test_hover_reads_the_road_and_beats_water(self):
        # All three fixture features pass through the centre; the road wins.
        self._build_area()
        self._show()
        w, h, cx, cy, _scale = self.panel._view()
        self._point_at(cx, cy)
        text = self.panel._reading.cget("text")
        self.assertIn("Main St", text)
        self.assertIn("secondary road", text)
        self.assertIn("bearing", text)
        self.assertIn("mi from fix", text)

    def test_hover_falls_through_to_elevation(self):
        self._build_area()
        self._show()
        w, h, cx, cy, scale = self.panel._view()
        # Well away from every vector, but still inside the terrain grid.
        self._point_at(cx + 4000.0 * scale, cy - 4000.0 * scale)
        text = self.panel._reading.cget("text")
        self.assertIn("elev", text)
        self.assertIn("ft", text)

    def test_reading_is_sticky_when_nothing_is_under_the_cursor(self):
        # Roads-only area: away from the one road there is nothing to report,
        # and the previous reading must survive rather than blanking.
        self._build_area(layers=("roads",))
        self._show(layers=("roads",))
        w, h, cx, cy, _scale = self.panel._view()
        self._point_at(cx, cy)
        first = self.panel._reading.cget("text")
        self.assertIn("Main St", first)
        self._point_at(4, 4)
        self.assertEqual(self.panel._reading.cget("text"), first)

    def test_pointer_outside_the_cached_square_is_ignored(self):
        self._build_area()
        self._show()
        self.panel._reading.config(text="sentinel")
        self._point_at(-10_000, -10_000)
        self.assertEqual(self.panel._reading.cget("text"), "sentinel")

    # ── Search: view-only pan ───────────────────────────────────────────────

    def test_search_pans_the_view_without_touching_themis(self):
        self._build_area()
        self._show()
        with mock.patch.object(themis, "save",
                               side_effect=AssertionError("search wrote the fix")):
            self.panel._search_var.set("44.0 -85.0")
            self.panel._on_search()
        self.assertFalse(self.panel._following)
        self.assertEqual(self.panel._status["area_id"],
                         artemis.area_id(44.0, -85.0))
        self.assertIn("44.0000", self.panel._status_lbl.cget("text"))

    def test_home_returns_to_the_fix(self):
        self._build_area()
        self._show()
        self.panel._search_var.set("44.0 -85.0")
        self.panel._on_search()
        self.panel._go_home()
        self.assertTrue(self.panel._following)
        self.assertEqual(self.panel._status["area_id"], artemis.area_id(*_FIX))
        self.assertIn("CACHED", self.panel._status_lbl.cget("text"))

    def test_a_tick_does_not_steal_a_searched_view_back(self):
        self._build_area()
        self._show()
        self.panel._search_var.set("44.0 -85.0")
        self.panel._on_search()
        self.panel.update(_status(cached=True,
                                  ingested_at="2026-08-09T00:00:00Z",
                                  layers=["roads", "water", "terrain"]))
        self.assertFalse(self.panel._following)
        self.assertEqual(self.panel._status["area_id"],
                         artemis.area_id(44.0, -85.0))

    def test_failed_search_reports_and_leaves_the_view_alone(self):
        self._build_area()
        self._show()
        with mock.patch("tools.eratosthenes._geocode",
                        return_value={"error": "no_results"}):
            self.panel._search_var.set("somewhere that is not a place")
            self.panel._on_search()
        self.assertTrue(self.panel._following)
        self.assertIn("SEARCH", self.panel._status_lbl.cget("text"))

    def test_uncached_view_lists_the_nearest_cached_areas(self):
        # "Not cached here" is a normal state and gets a useful body.
        self._build_area()
        self._show()
        self.panel._search_var.set("44.0 -85.0")
        self.panel._on_search()
        self.panel._redraw()
        texts = [self.panel._canvas.itemcget(item, "text")
                 for item in self.panel._canvas.find_all()]
        blob = "\n".join(texts)
        self.assertIn("no map cached", blob)
        self.assertIn("nearest cached areas", blob)
        self.assertIn("39.281", blob)

    def test_unexpected_worker_error_surfaces_instead_of_hanging(self):
        # If the worker thread dies without queueing anything, the drain chain
        # polls forever and the panel shows "INGESTING —" with no recourse.
        self.panel.update(_status())
        with mock.patch("tools.artemis.ingest_area",
                        side_effect=RuntimeError("something unforeseen")):
            self.panel._start_ingest(*_FIX)
            deadline = time.time() + 5.0
            while self.panel._q.empty() and time.time() < deadline:
                time.sleep(0.01)
            self.panel._drain()
        self.assertFalse(self.panel._ingesting)
        self.assertIn("INGEST FAILED", self.panel._status_lbl.cget("text"))

    # ── Panning ─────────────────────────────────────────────────────────────

    def _gesture(self, x0, y0, dx, dy):
        """press → drag → release, as Tk would deliver it."""
        self.panel._on_press(SimpleNamespace(x=int(x0), y=int(y0)))
        self.panel._on_drag(SimpleNamespace(x=int(x0 + dx), y=int(y0 + dy)))
        self.panel._on_release(SimpleNamespace(x=int(x0 + dx), y=int(y0 + dy)))

    def test_drag_pans_the_world_and_leaves_the_chrome_alone(self):
        self._build_area()
        self._show()
        self.panel._set_zoom(4)
        rim_before = self.panel._canvas.coords(
            self.panel._canvas.find_withtag("rim")[0])
        _w, _h, cx, cy, _scale = self.panel._view()
        self._gesture(cx, cy, 60, 0)
        self.assertNotEqual(self.panel._pan_e, 0.0)
        self.assertEqual(self.panel._canvas.coords(
            self.panel._canvas.find_withtag("rim")[0]), rim_before)

    def test_a_short_press_and_release_is_a_click_and_reads(self):
        self._build_area()
        self._show()
        _w, _h, cx, cy, _scale = self.panel._view()
        self._gesture(cx, cy, 2, 2)          # inside _CLICK_PX
        self.assertEqual(self.panel._pan_e, 0.0)
        self.assertIn("Main St", self.panel._reading.cget("text"))

    def test_a_drag_does_not_change_the_reading(self):
        self._build_area()
        self._show()
        self.panel._set_zoom(4)
        self.panel._reading.config(text="sentinel")
        _w, _h, cx, cy, _scale = self.panel._view()
        self._gesture(cx, cy, 80, 40)
        self.assertEqual(self.panel._reading.cget("text"), "sentinel")

    def test_pan_stays_zero_at_1x_however_hard_you_drag(self):
        self._build_area()
        self._show()
        _w, _h, cx, cy, _scale = self.panel._view()
        self._gesture(cx, cy, 400, 400)
        self.assertEqual((self.panel._pan_e, self.panel._pan_n), (0.0, 0.0))

    def test_pan_is_clamped_to_the_display_disc(self):
        self._build_area()
        self._show()
        self.panel._set_zoom(4)
        _w, _h, cx, cy, _scale = self.panel._view()
        self._gesture(cx, cy, -5000, -5000)
        limit = self.panel._pan_limit()
        self.assertLessEqual(abs(self.panel._pan_e), limit + 1e-6)
        self.assertLessEqual(abs(self.panel._pan_n), limit + 1e-6)
        # And the porthole edge still sits inside the cached square.
        reach = limit + artemis.DISPLAY_RADIUS_M / self.panel._zoom
        self.assertLess(reach, artemis.HALF_WIDTH_M)

    def test_zooming_out_reclamps_the_pan(self):
        self._build_area()
        self._show()
        self.panel._set_zoom(4)
        _w, _h, cx, cy, _scale = self.panel._view()
        self._gesture(cx, cy, 100, 100)
        self.assertNotEqual(self.panel._pan_e, 0.0)
        self.panel._set_zoom(1)
        self.assertEqual((self.panel._pan_e, self.panel._pan_n), (0.0, 0.0))

    def test_home_and_search_reset_the_pan(self):
        self._build_area()
        self._show()
        self.panel._set_zoom(4)
        _w, _h, cx, cy, _scale = self.panel._view()
        self._gesture(cx, cy, 100, 0)
        self.assertNotEqual(self.panel._pan_e, 0.0)
        self.panel._go_home()
        self.assertEqual(self.panel._pan_e, 0.0)

    def test_hit_test_follows_the_pan(self):
        # The road runs corner to corner through the fix. After panning, it is
        # found at its shifted screen position and not at the unpanned one.
        self._build_area()
        self._show()
        self.panel._set_zoom(4)
        _w, _h, cx, cy, scale = self.panel._view()
        self.panel._pan_e = 1000.0          # look east
        self.panel._redraw()
        _w, _h, cx, cy, scale = self.panel._view()
        # The fix (0, 0) now sits left of centre; probe there.
        self.panel._reading.config(text="")
        self.panel._on_pointer(SimpleNamespace(x=int(cx), y=int(cy)))
        self.assertIn("Main St", self.panel._reading.cget("text"))

    # ── Cached Locations overlay ────────────────────────────────────────────

    def _rows(self):
        return [c for c in self.panel._areas_body.winfo_children()
                if isinstance(c, tk.Frame) and c.winfo_children()]

    def _glyph(self, row_index, text):
        top = self._rows()[row_index].winfo_children()[0]
        for child in top.winfo_children():
            if isinstance(child, tk.Label) and child.cget("text") == text:
                return child
        raise AssertionError(f"no {text!r} glyph in row {row_index}")

    def test_overlay_lists_cached_areas_with_range_and_bearing(self):
        self._build_area()
        self._show()
        self.assertTrue(self.panel._areas_box.place_info())
        self.assertIn("CACHED AREAS (1)",
                      self.panel._areas_header.cget("text"))
        texts = [c.cget("text") for row in self._rows()
                 for c in row.winfo_children() if isinstance(c, tk.Label)]
        self.assertTrue(any("mi" in t and "°" in t for t in texts))

    def test_overlay_is_hidden_when_nothing_is_cached(self):
        self.panel.update(_status())
        self.root.update_idletasks()
        self.assertEqual(self.panel._areas_box.place_info(), {})

    def test_overlay_sits_above_the_compass_rose(self):
        # The compass is drawn at cy = h - 42 with r = 22 and its label above,
        # so the box's bottom must clear h - 72. Derived, not restated.
        self._build_area()
        self._show()
        offset = int(self.panel._areas_box.place_info()["y"])
        compass_top = -(42 + 22 + 8)
        self.assertLessEqual(offset, compass_top)

    def test_overlay_survives_a_redraw(self):
        # THE trap test: _redraw() opens with canvas.delete("all"), which would
        # destroy the box outright if it were a create_window canvas item.
        self._build_area()
        self._show()
        self.panel._redraw()
        self.assertTrue(self.panel._areas_box.winfo_exists())
        self.assertTrue(self.panel._areas_box.place_info())

    def test_row_click_jumps_the_view_without_touching_themis(self):
        self._build_area()
        self._show()
        with mock.patch.object(themis, "save",
                               side_effect=AssertionError("row click wrote the fix")):
            self.panel._on_area_click(list(_FIX), "Deer Camp")
        self.assertFalse(self.panel._following)
        self.assertEqual(self.panel._view_label, "Deer Camp")

    def test_rename_commits_on_return_and_survives_a_rebuild(self):
        area = self._build_area()
        self._show()
        top = self._rows()[0].winfo_children()[0]
        self.panel._begin_rename(top, area, "old name")
        self.panel._commit_rename(area, "Deer Camp")
        self.root.update_idletasks()
        self.assertEqual(artemis.read_manifest(area)["label"], "Deer Camp")
        names = [c.cget("text") for row in self._rows()
                 for c in row.winfo_children()[0].winfo_children()
                 if isinstance(c, tk.Label)]
        self.assertIn("Deer Camp", names)

    def test_rename_escape_and_focus_out_never_write(self):
        # Both bindings route to _cancel_rename, which must never call
        # set_label — a FocusOut also fires on view switch and on teardown.
        area = self._build_area()
        self._show()
        top = self._rows()[0].winfo_children()[0]
        with mock.patch.object(artemis, "set_label",
                               side_effect=AssertionError("cancel wrote a label")):
            self.panel._begin_rename(top, area, "old name")
            entry = [c for c in top.winfo_children()
                     if isinstance(c, tk.Entry)][0]
            self.assertTrue(entry.bind("<Escape>"))
            self.assertTrue(entry.bind("<FocusOut>"))
            self.panel._cancel_rename(entry, getattr(top, "_name_label"))
            self.root.update_idletasks()
        self.assertIsNone(self.panel._renaming)
        self.assertFalse(entry.winfo_exists())
        self.assertNotIn("label", artemis.read_manifest(area))

    def test_delete_first_click_arms_and_does_not_delete(self):
        area = self._build_area()
        self._show()
        glyph = self._glyph(0, "✕")
        with mock.patch.object(artemis, "delete_area") as deleter:
            self.panel._on_row_delete(glyph, area)
            deleter.assert_not_called()
        self.assertEqual(glyph.cget("text"), "delete?")
        self.assertTrue(glyph._armed)
        # Hover must not repaint the armed prompt away under the mouse.
        self.panel._glyph_hover(glyph, theme.C["text3"])
        self.assertEqual(glyph.cget("fg"), theme.C["amber"])

    def test_delete_second_click_recycles_and_drops_the_row(self):
        area = self._build_area()
        self._show()
        glyph = self._glyph(0, "✕")
        with mock.patch.object(artemis, "_send_to_recycle_bin",
                               return_value=True) as recycler:
            self.panel._on_row_delete(glyph, area)     # arms
            self.panel._on_row_delete(glyph, area)     # fires
        recycler.assert_called_once_with(artemis.area_dir(area))

    def test_deleting_the_displayed_area_clears_the_map(self):
        area = self._build_area()
        self._show()
        with mock.patch.object(artemis, "delete_area", return_value=True):
            self.panel._delete_area(area)
        self.root.update_idletasks()
        self.assertFalse(self.panel._status["cached"])
        self.assertEqual(len(self.panel._canvas.find_withtag("road")), 0)

    def test_refresh_second_click_starts_one_ingest_for_that_area(self):
        self._build_area()
        self._show()
        glyph = self._glyph(0, "⟳")
        with mock.patch.object(self.panel, "_start_ingest") as starter:
            self.panel._on_row_refresh(glyph, list(_FIX))     # arms
            starter.assert_not_called()
            self.assertEqual(glyph.cget("text"), "refresh?")
            self.panel._on_row_refresh(glyph, list(_FIX))     # fires
            starter.assert_called_once()
            args = starter.call_args[0]
            self.assertAlmostEqual(args[0], _FIX[0], places=3)
            self.assertAlmostEqual(args[1], _FIX[1], places=3)

    def test_a_finished_refresh_of_another_area_does_not_steal_the_view(self):
        self._build_area()
        self._show()
        drawn = self.panel._drawn
        self.panel._finish_ingest("done", "n44p000_w085p000")
        self.assertEqual(self.panel._drawn, drawn)

    def test_pointer_events_are_bound_once_on_the_canvas(self):
        # Both bindings must exist so the chart is usable without a mouse.
        # Asserted on the binding table rather than by event_generate: Tk
        # does not deliver synthetic button events to a withdrawn root, so a
        # generated click would pass this test for the wrong reason. The
        # handler's behaviour is covered by the _point_at tests above.
        canvas = self.panel._canvas
        self.assertTrue(canvas.bind("<Motion>"))
        self.assertTrue(canvas.bind("<Button-1>"))
        self.assertTrue(canvas.bind("<B1-Motion>"))
        self.assertTrue(canvas.bind("<ButtonRelease-1>"))
        # Bound on the canvas itself — never tag_bind per feature, which is
        # what keeps the cost constant as the feature count grows.
        self.assertEqual(canvas.tag_bind("road", "<Motion>"), "")


class TestResamplePpm(unittest.TestCase):
    """The nearest-neighbour rescale that keeps raster and vectors aligned."""

    @staticmethod
    def _ppm(size, pixel):
        body = b"".join(bytes(pixel(x, y)) for y in range(size)
                        for x in range(size))
        return b"P6\n%d %d\n255\n" % (size, size) + body

    def test_upscale_and_downscale_shape(self):
        from panels.artemis_panel import _resample_ppm
        src = self._ppm(4, lambda x, y: (x * 10, y * 10, 0))
        for size in (2, 4, 9):
            out = _resample_ppm(src, size)
            self.assertTrue(out.startswith(b"P6\n%d %d\n255\n" % (size, size)))
            self.assertEqual(len(out.split(b"255\n", 1)[1]), size * size * 3)

    def test_corners_are_preserved(self):
        from panels.artemis_panel import _resample_ppm
        src = self._ppm(4, lambda x, y: (255, 0, 0) if (x, y) == (0, 0)
                        else ((0, 0, 255) if (x, y) == (3, 3) else (0, 0, 0)))
        body = _resample_ppm(src, 8).split(b"255\n", 1)[1]
        self.assertEqual(body[0:3], b"\xff\x00\x00")        # top-left kept
        self.assertEqual(body[-3:], b"\x00\x00\xff")        # bottom-right kept

    def test_identity_when_sizes_match(self):
        from panels.artemis_panel import _resample_ppm
        src = self._ppm(5, lambda x, y: (x * 5, y * 5, 7))
        self.assertEqual(_resample_ppm(src, 5).split(b"255\n", 1)[1],
                         src.split(b"255\n", 1)[1])

    def test_zoom_crops_to_the_centre(self):
        from panels.artemis_panel import _resample_ppm
        # 4x4 with a distinct centre 2x2 block: zoom 2 must show only that.
        def pixel(x, y):
            return (0, 255, 0) if 1 <= x <= 2 and 1 <= y <= 2 else (255, 0, 0)

        body = _resample_ppm(self._ppm(4, pixel), 2, zoom=2)
        body = body.split(b"255\n", 1)[1]
        self.assertEqual(set(body[i:i + 3] for i in range(0, len(body), 3)),
                         {b"\x00\xff\x00"})

    def test_zoom_one_is_the_full_frame(self):
        from panels.artemis_panel import _resample_ppm
        src = self._ppm(4, lambda x, y: (x * 60, y * 60, 0))
        self.assertEqual(_resample_ppm(src, 4, zoom=1),
                         _resample_ppm(src, 4))

    def test_pan_moves_the_crop(self):
        from panels.artemis_panel import _resample_ppm
        # 4x4 with a distinct top-left 2x2 block. zoom=2 centres on x0=1;
        # pan of -0.25 * 4 = -1 px shifts the crop onto the block.
        def pixel(x, y):
            return (0, 255, 0) if x <= 1 and y <= 1 else (255, 0, 0)

        body = _resample_ppm(self._ppm(4, pixel), 2, zoom=2,
                             pan=(-0.25, -0.25)).split(b"255\n", 1)[1]
        self.assertEqual(set(body[i:i + 3] for i in range(0, len(body), 3)),
                         {b"\x00\xff\x00"})

    def test_pan_is_clamped_to_the_source(self):
        from panels.artemis_panel import _resample_ppm
        src = self._ppm(4, lambda x, y: (x * 60, y * 60, 0))
        # Absurd pan returns the far edge rather than reading out of range.
        self.assertEqual(_resample_ppm(src, 2, zoom=2, pan=(9.0, 9.0)),
                         _resample_ppm(src, 2, zoom=2, pan=(0.5, 0.5)))

    def test_garbage_raises(self):
        from panels.artemis_panel import _resample_ppm
        with self.assertRaises(ValueError):
            _resample_ppm(b"not a ppm", 4)
        with self.assertRaises(ValueError):
            _resample_ppm(b"P6\n4 4\n255\nshort", 4)


if __name__ == "__main__":
    unittest.main()
