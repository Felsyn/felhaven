"""
test_panel_after_teardown.py — every panel timer is cancelled on destroy().

ONE JOB: prove the CONVENTIONS §6 teardown rule holds — a panel that schedules
after() work leaves NOTHING pending in the Tcl interpreter once it is destroyed.

Why this needs its own guard: an uncancelled timer does not fail a test. Tk
deletes a widget's registered Tcl commands as it destroys the widget, so the
orphaned after script has no command left to call — Tcl prints
`invalid command name "..._poll"` to stderr and moves on. The suite still says
OK. It only surfaced as ~53 stderr lines in the combined run (many roots created
and destroyed), and not at all when a module ran alone, which is exactly the
shape of thing that rots quietly. So assert it instead of reading stderr.

The assertion is a snapshot diff of `after info` — the interpreter's own list of
pending timer ids — taken before the panel exists and again after it is
destroyed. Each case also asserts the panel actually armed something first, so a
panel that stops scheduling timers can't turn this into a vacuous pass.

    python -X utf8 -m unittest tests.test_panel_after_teardown
"""

import os
import shutil
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
from tools import artemis, morpheus, orpheus


class _TeardownCase(unittest.TestCase):
    """A withdrawn root plus the pending-timer snapshot helpers."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _pending(self):
        """The interpreter's pending after-ids, as a set."""
        return set(self.root.tk.splitlist(self.root.tk.call("after", "info")))

    def assertNoTimersSurvive(self, build, arm):
        """build() → a panel; arm(panel) → schedules its timers. Asserts the
        arming really happened, then that destroy() takes it all back."""
        baseline = self._pending()
        panel = build()
        panel.pack()
        arm(panel)
        self.root.update_idletasks()
        armed = self._pending() - baseline
        self.assertTrue(armed, "nothing was scheduled — the guard would be vacuous")
        panel.destroy()
        self.assertEqual(self._pending() - baseline, set(),
                         "timers outlived the panel that scheduled them")


class TestHomePanelTeardown(_TeardownCase):

    def _build(self):
        from panels.home_panel import HomePanel
        return HomePanel(self.root)

    def test_poll_chain_is_cancelled(self):
        self.assertNoTimersSurvive(self._build, lambda p: None)  # __init__ arms it

    def test_direct_poll_calls_leave_exactly_one_pending_poll(self):
        """_poll() re-arms, and the smoke tests call it directly to drain the
        queue without a mainloop — so it can be entered while its own timer is
        still pending. Re-arming blind would orphan that id where destroy() can
        never reach it, so _arm_poll cancels before it schedules."""
        baseline = self._pending()
        panel = self._build()
        panel.pack()
        for _ in range(3):
            panel._poll()
        self.assertEqual(len(self._pending() - baseline), 1)
        panel.destroy()
        self.assertEqual(self._pending() - baseline, set())

    def test_latency_filler_is_cancelled(self):
        """The filler is armed by _on_submit when auto-speak is on. A long delay
        keeps it pending so destroy() is what clears it, not the clock."""
        def arm(panel):
            panel._entry.insert(0, "what day is it?")
            with mock.patch("panels.home_panel.calliope.auto_speak_enabled",
                            return_value=True), \
                 mock.patch("panels.home_panel.calliope.filler_delay_ms",
                            return_value=60_000), \
                 mock.patch("panels.home_panel.calliope.stop"), \
                 mock.patch("panels.home_panel.pythia.ask", return_value="ok"):
                panel._on_submit()
                panel._thread.join(timeout=5)
            self.assertIsNotNone(panel._filler_after_id)

        self.assertNoTimersSurvive(self._build, arm)

    def test_resubmitting_does_not_orphan_the_previous_filler(self):
        """Only one filler is ever outstanding — _on_submit cancels the last
        one before arming a new one, which is what lets a single stored id
        cover it at teardown."""
        def arm(panel):
            with mock.patch("panels.home_panel.calliope.auto_speak_enabled",
                            return_value=True), \
                 mock.patch("panels.home_panel.calliope.filler_delay_ms",
                            return_value=60_000), \
                 mock.patch("panels.home_panel.calliope.stop"), \
                 mock.patch("panels.home_panel.pythia.ask", return_value="ok"):
                for i in range(3):
                    panel._entry.insert(0, f"q{i}")
                    panel._on_submit()
                    panel._thread.join(timeout=5)

        baseline = self._pending()
        panel = self._build()
        panel.pack()
        arm(panel)
        # One poll + one filler, never three fillers.
        self.assertEqual(len(self._pending() - baseline), 2)
        panel.destroy()
        self.assertEqual(self._pending() - baseline, set())


class TestEmanonPanelTeardown(_TeardownCase):

    def test_blink_chain_is_cancelled(self):
        from panels.emanon_panel import EmanonPanel

        def arm(panel):
            panel._flash(6)
            self.assertIsNotNone(panel._flash_after_id)

        self.assertNoTimersSurvive(lambda: EmanonPanel(self.root), arm)


class TestArtemisPanelTeardown(_TeardownCase):
    """Three independent timers: the resize debounce, the CACHE AREA confirm
    window, and the ingest drain chain."""

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._orig_cache = artemis.CACHE_DIR
        artemis.CACHE_DIR = self._dir

    def tearDown(self):
        artemis.CACHE_DIR = self._orig_cache
        shutil.rmtree(self._dir, ignore_errors=True)

    def _build(self):
        from panels.artemis_panel import ArtemisPanel
        return ArtemisPanel(self.root)

    @staticmethod
    def _status():
        return artemis.area_status(39.2806, -80.3440)

    def test_resize_debounce_is_cancelled(self):
        def arm(panel):
            panel._on_configure(None)
            self.assertIsNotNone(panel._resize_after_id)

        self.assertNoTimersSurvive(self._build, arm)

    def test_pan_debounce_is_cancelled(self):
        def arm(panel):
            panel.update(self._status())
            panel._zoom = 4                  # 1x has no pan, so nothing to arm
            panel._on_press(SimpleNamespace(x=100, y=100))
            panel._on_drag(SimpleNamespace(x=160, y=140))
            self.assertIsNotNone(panel._pan_after_id)

        self.assertNoTimersSurvive(self._build, arm)

    def test_confirm_window_is_cancelled(self):
        def arm(panel):
            panel.update(self._status())
            panel._on_cache_click()          # arms, does not start an ingest
            self.assertIsNotNone(panel._confirm_after_id)

        self.assertNoTimersSurvive(self._build, arm)

    def test_several_armed_row_glyphs_are_all_cancelled(self):
        """Each armed glyph owns its own confirm window, which is why the
        panel needs the _pending_after SET beside its single-slot ids."""
        def arm(panel):
            glyphs = [tk.Label(panel, text="✕") for _ in range(2)]
            for i, glyph in enumerate(glyphs):
                glyph._armed = False
                glyph._rest = "✕"
                panel._on_row_delete(glyph, f"n0{i}p000_e000p000")
            self.assertEqual(len(panel._pending_after), 2)

        self.assertNoTimersSurvive(self._build, arm)

    def test_ingest_drain_chain_is_cancelled(self):
        """The worker is held open for the duration so the drain chain is
        genuinely in flight when destroy() runs — the case a finished ingest
        would hide."""
        release = threading.Event()

        def fake_ingest(lat, lon, cancel, progress):
            release.wait(5)
            return artemis.area_id(lat, lon)

        def arm(panel):
            panel.update(self._status())
            panel._start_ingest(39.2806, -80.3440)
            self.assertIsNotNone(panel._drain_after_id)

        try:
            with mock.patch("tools.artemis.ingest_area", side_effect=fake_ingest):
                self.assertNoTimersSurvive(self._build, arm)
        finally:
            release.set()


class TestMorpheusPanelTeardown(_TeardownCase):

    def setUp(self):
        self._orig = (morpheus.available, morpheus.search,
                      morpheus.load_playlists, morpheus.save_playlist)
        morpheus.available = lambda: {"mpv": "mpv.exe", "ytdlp": "yt-dlp.exe"}
        morpheus.search = lambda q: []
        morpheus.load_playlists = lambda: [{"label": "a", "url": "https://a/"},
                                           {"label": "b", "url": "https://b/"}]
        morpheus.save_playlist = lambda label, url: False   # forces the ! glyph

    def tearDown(self):
        (morpheus.available, morpheus.search,
         morpheus.load_playlists, morpheus.save_playlist) = self._orig

    def _build(self):
        from panels.morpheus_panel import MorpheusPanel
        return MorpheusPanel(self.root)

    def test_status_flash_is_cancelled(self):
        self.assertNoTimersSurvive(self._build,
                                   lambda p: p._pl_flash("saved", ok=True))

    def test_several_armed_glyphs_are_all_cancelled(self):
        """Two remove glyphs armed at once = two live confirm windows. A single
        stored after-id would forget the first; the _pending_after set doesn't."""
        def arm(panel):
            glyphs = [tk.Label(panel, text="✕") for _ in range(2)]
            for i, g in enumerate(glyphs):
                g._armed = False
                panel._on_remove_playlist(i, g)
            self.assertEqual(len(panel._pending_after), 2)

        self.assertNoTimersSurvive(self._build, arm)


class TestOrpheusPanelTeardown(_TeardownCase):

    def setUp(self):
        self._orig = (orpheus.available, orpheus.play_file, orpheus.stop,
                      orpheus.delete_file)
        orpheus.available = lambda: {"ffmpeg": "ffmpeg.exe"}
        orpheus.play_file = lambda name: {"playing": name}
        orpheus.stop = lambda: None
        orpheus.delete_file = lambda name: {"deleted": name}

    def tearDown(self):
        (orpheus.available, orpheus.play_file, orpheus.stop,
         orpheus.delete_file) = self._orig

    def _build(self):
        from panels.orpheus_panel import OrpheusPanel
        return OrpheusPanel(self.root)

    def test_status_flash_is_cancelled(self):
        self.assertNoTimersSurvive(self._build,
                                   lambda p: p._flash("removed", ok=True))

    def test_several_armed_glyphs_are_all_cancelled(self):
        def arm(panel):
            glyphs = [tk.Label(panel, text="✕") for _ in range(2)]
            for name, g in zip(("a.opus", "b.opus"), glyphs):
                g._armed = False
                panel._on_remove_file(name, g)
            self.assertEqual(len(panel._pending_after), 2)

        self.assertNoTimersSurvive(self._build, arm)


if __name__ == "__main__":
    unittest.main()
