"""
test_artemis_offline.py — the render-no-network invariant, enforced.

CONVENTIONS §12 (2026-08-09) says Artemis render operations MUST NOT perform
network I/O: ingest is online, slow and explicit; load is offline, fast and
pure, and raises rather than helpfully fetching. The failure mode being
guarded against is a reader that quietly grows an `if not cached: fetch()`
branch eighteen months from now — which would break the field case exactly
when it matters and would break it silently, because on a developer's
machine the fetch would simply succeed.

So the socket layer itself is blocked and a real cached area is rendered
through the whole stack: every layer's load(), the derived-file
regeneration paths, the spatial index, the hit test, and a full panel
construct → update → redraw → zoom → hover pass.

Two deliberate choices:

* `unittest.mock.patch` as a CONTEXT MANAGER, never assignment to the socket
  module's globals. Monkeypatching the module poisons every test that runs
  after it in the same process, and the resulting failures look like network
  flakiness rather than test pollution — which is a worse bug than the one
  being prevented.
* A test that proves the guard bites (test_the_block_is_real). A
  hermeticism test that cannot fail is worse than none: it reports safety it
  never checked.

    python -X utf8 -m unittest tests.test_artemis_offline
"""

import array
import contextlib
import os
import shutil
import sys
import tempfile
import unittest
import urllib.request
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
from tools import artemis, hermes
from tools import demeter
from tools import hecate
from tools import poseidon

_FIX = (39.2806, -80.3440)


def _blocked(*args, **kwargs):
    raise AssertionError("a render path performed network I/O")


@contextlib.contextmanager
def _no_network():
    """Every seam Felhaven can reach the network through: hermes.get, which
    every runtime reader uses, and below it the socket calls. hermes is
    blocked directly because a blocked socket under it would surface as a
    translated Offline error, which a reader might quietly swallow."""
    with mock.patch("tools.hermes.get", _blocked), \
            mock.patch("socket.socket", _blocked), \
            mock.patch("socket.create_connection", _blocked):
        yield


class TestArtemisOffline(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # The root is built OUTSIDE the block: Tk talks to the display, not to
        # Python's socket module, and creating it under the patch would only
        # muddy what the test is asserting.
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
        self.area = self._build_real_area()

    def tearDown(self):
        artemis.CACHE_DIR = self._orig_cache
        shutil.rmtree(self._dir, ignore_errors=True)

    def _build_real_area(self) -> str:
        """A genuine cached area, written by the real writers — not fixtures
        hand-placed on disk, so the readers under test face the real bytes."""
        area = artemis.area_id(*_FIX)
        directory = artemis.area_dir(area)
        os.makedirs(directory)
        artemis.write_vec(
            os.path.join(directory, artemis.VEC_NAMES[artemis.LAYER_ROADS]),
            [artemis.Feature(kind="road", name="Old Rte 50", tier=2, flags=0,
                             coords=[(-2000.0, -1000.0), (2000.0, 1000.0)])],
            _FIX, artemis.LAYER_ROADS)
        artemis.write_vec(
            os.path.join(directory, artemis.VEC_NAMES[artemis.LAYER_WATER]),
            [artemis.Feature(kind="water_line", name="Ann Run", tier=1,
                             flags=0, coords=[(-500.0, 200.0), (500.0, 250.0)]),
             artemis.Feature(kind="water_area", name="Oral Lk", tier=1,
                             flags=0, coords=[(0.0, 0.0), (400.0, 0.0),
                                              (400.0, 400.0), (0.0, 400.0)])],
            _FIX, artemis.LAYER_WATER)
        size = 32
        cells = array.array("h", [200 + r for r in range(size)
                                  for _ in range(size)])
        demeter.write_bin(os.path.join(directory, demeter._BIN_NAME), cells,
                          _FIX, size=size)
        demeter.write_derived(directory, cells, size=size)
        artemis.write_manifest(directory, {
            "center": list(_FIX), "radius_m": artemis.DISPLAY_RADIUS_M,
            "layers": ["roads", "water", "terrain"],
            "fetched": "2026-08-10T00:00:00Z", "schema": artemis.MANIFEST_SCHEMA,
        })
        return area

    def _status(self):
        return artemis.area_status(*_FIX)

    # ── The guard itself ────────────────────────────────────────────────────

    def test_the_block_is_real(self):
        """If this ever passes silently, every other test here is theatre."""
        with _no_network():
            with self.assertRaises(AssertionError):
                urllib.request.urlopen("http://example.invalid", timeout=1)
            with self.assertRaises(AssertionError):
                hermes.get("https://example.invalid/", cap=1, timeout=1)

    # ── Tools-side reads ────────────────────────────────────────────────────

    def test_every_layer_load_is_offline(self):
        with _no_network():
            self.assertEqual(len(hecate.load(self.area)), 1)
            self.assertEqual(len(poseidon.load(self.area)), 2)
            info, cells = demeter.load(self.area)
            self.assertEqual(info["vdatum"], "NAVD88")
            self.assertTrue(demeter.raster(self.area).startswith(b"P6"))
            self.assertEqual(artemis.read_manifest(self.area)["schema"],
                             artemis.MANIFEST_SCHEMA)
            self.assertTrue(self._status()["cached"])
            self.assertIsNotNone(
                demeter.sample(info, cells, 0.0, 0.0))

    def test_index_and_hit_test_are_offline(self):
        with _no_network():
            layers = {artemis.LAYER_ROADS: hecate.load(self.area),
                      artemis.LAYER_WATER: poseidon.load(self.area)}
            index = artemis.ensure_index(self.area, layers)
            hit = artemis.query(index, layers, 0.0, 0.0, 500.0)
            self.assertIsNotNone(hit)
            self.assertEqual(hit[0], artemis.LAYER_ROADS)

    def test_derived_regeneration_is_offline(self):
        """`rm -rf derived/` is supported, and rebuilding must not reach out."""
        shutil.rmtree(os.path.join(artemis.area_dir(self.area),
                                   demeter._DERIVED_DIR))
        with _no_network():
            self.assertTrue(demeter.raster(self.area).startswith(b"P6"))
            layers = {artemis.LAYER_ROADS: hecate.load(self.area)}
            self.assertTrue(artemis.ensure_index(self.area, layers))

    def test_missing_area_raises_rather_than_fetching(self):
        with _no_network():
            for loader in (hecate.load, poseidon.load, demeter.load,
                           demeter.raster):
                with self.subTest(loader=loader.__name__):
                    with self.assertRaises(FileNotFoundError):
                        loader("n00p000_e000p000")

    # ── The panel ───────────────────────────────────────────────────────────

    def test_panel_renders_a_cached_area_offline(self):
        from panels.artemis_panel import ArtemisPanel

        with _no_network():
            panel = ArtemisPanel(self.root)
            try:
                panel.pack(fill="both", expand=True)
                panel._canvas.config(width=400, height=400)
                self.root.update_idletasks()
                panel.update(self._status())
                panel._redraw()
                self.assertTrue(panel._canvas.find_withtag("road"))
                self.assertTrue(panel._canvas.find_withtag("water"))
                self.assertTrue(panel._canvas.find_withtag("terrain"))

                for stop in artemis.ZOOM_STOPS:
                    panel._set_zoom(stop)

                # Panning is a render operation too — a drag must not become
                # an excuse to fetch the part of the world it uncovers.
                panel._set_zoom(4)
                _w, _h, cx, cy, _scale = panel._view()
                panel._on_press(SimpleNamespace(x=int(cx), y=int(cy)))
                panel._on_drag(SimpleNamespace(x=int(cx) + 60, y=int(cy) + 30))
                panel._on_release(SimpleNamespace(x=int(cx) + 60,
                                                  y=int(cy) + 30))
                panel._redraw()

                _w, _h, cx, cy, _scale = panel._view()
                panel._on_pointer(SimpleNamespace(x=int(cx), y=int(cy)))
                self.assertTrue(panel._reading.cget("text"))
            finally:
                panel.destroy()

    def test_the_overlay_and_its_edits_are_local_io_only(self):
        """Listing, renaming and deleting a cached area are disk operations —
        none of them may reach for the network."""
        from panels.artemis_panel import ArtemisPanel

        with _no_network():
            panel = ArtemisPanel(self.root)
            try:
                panel.pack(fill="both", expand=True)
                self.root.update_idletasks()
                panel.update(self._status())
                panel._refresh_areas(force=True)
                self.assertTrue(panel._areas_box.place_info())

                panel._commit_rename(self.area, "Deer Camp")
                self.assertEqual(
                    artemis.read_manifest(self.area)["label"], "Deer Camp")

                # Patched so the test never fills a real Recycle Bin.
                with mock.patch.object(artemis, "_send_to_recycle_bin",
                                       return_value=True):
                    panel._delete_area(self.area)
            finally:
                panel.destroy()

    def test_search_by_coordinates_is_offline(self):
        """Coordinates and waypoints resolve with no network; only the
        address geocoder needs one, and it is last in the order."""
        from panels.artemis_panel import ArtemisPanel

        with _no_network():
            panel = ArtemisPanel(self.root)
            try:
                panel.pack(fill="both", expand=True)
                self.root.update_idletasks()
                panel.update(self._status())
                panel._search_var.set(f"{_FIX[0]} {_FIX[1]}")
                panel._on_search()
                self.assertFalse(panel._following)
                self.assertTrue(panel._status["cached"])
                panel._go_home()
                self.assertTrue(panel._following)
            finally:
                panel.destroy()


if __name__ == "__main__":
    unittest.main()
