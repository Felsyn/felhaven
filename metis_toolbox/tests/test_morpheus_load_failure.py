"""
test_morpheus_load_failure.py — the silent-failure guard.

Why this file exists: on 2026-08-20 Morpheus stopped playing anything. A stale
yt-dlp resolved URLs mpv then got a 403 on, so every play() was a no-op — and
NOTHING said so. The panel showed "(loading…)" indefinitely and felhaven.log had
not one METIS.morpheus line across the whole outage. The bug was findable only
by running mpv by hand outside the app.

So these tests are not really about mpv. They are about the property that was
missing: a failed load must become visible. They pin the four states apart
(loading / playing / failed / deliberately stopped), because the failure state
and the stopped state look IDENTICAL to mpv and only Python-side intent
separates them — get that wrong and the panel cries wolf on every stop, or on
every track that reaches its natural end.

No mpv process and no pipe: _get_props is mocked with the real signatures
measured off mpv 0.41 on 2026-08-20 (recorded in morpheus.py's "Load failures"
docstring section).

    python -X utf8 -m unittest tests.test_morpheus_load_failure
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import morpheus


# ── Measured property snapshots (mpv 0.41, 2026-08-20) ───────────────────────
# Exactly what mpv reports in each phase. Keep these honest — they are the whole
# basis for telling the states apart.
#
# `path` is kept here even though status() no longer asks for it, because it is
# the trap: it is set during LOADING as well as playing, and confirming a load
# on it is precisely the bug test_loading_does_not_CONFIRM_the_load guards.
# A stray key in a _get_props stub is harmless — the real one returns only what
# was requested, and nothing reads this one.

_LOADING = {"media-title": "watch?v=abc", "pause": False, "time-pos": None,
            "duration": None, "playlist-pos": 0, "playlist-count": 1,
            "idle-active": False, "path": "https://youtu.be/abc"}

_PLAYING = {"media-title": "Clair de Lune", "pause": False, "time-pos": 7.5,
            "duration": 314.0, "playlist-pos": 0, "playlist-count": 1,
            "idle-active": False, "path": "https://youtu.be/abc"}

# A failed load and a deliberate stop differ ONLY in playlist-count (1 vs 0),
# which is why we track intent in Python instead of trusting this shape.
_FAILED = {"media-title": None, "pause": False, "time-pos": None,
           "duration": None, "playlist-pos": -1, "playlist-count": 1,
           "idle-active": True, "path": None}

_STOPPED = {"media-title": None, "pause": False, "time-pos": None,
            "duration": None, "playlist-pos": -1, "playlist-count": 0,
            "idle-active": True, "path": None}

_DEAD_PIPE = {k: None for k in _PLAYING}


class _LiveProc:
    """Stands in for a running mpv process."""

    def poll(self):
        return None


class _MorpheusStateCase(unittest.TestCase):
    """Saves and restores every module global these tests touch.

    The log paths are redirected into a temp dir for EVERY case here, not just
    the ones that assert on files: detecting a failure writes last_failure.log
    as a side effect, and a test suite has no business writing into the running
    app's own logs/ directory."""

    def setUp(self):
        self._saved = (morpheus._proc, morpheus._pending_load,
                       morpheus._load_error, morpheus._last_url,
                       morpheus._last_status, morpheus._user_volume)
        morpheus._proc = _LiveProc()
        # Reset for the same reason as _last_status below: one process runs the
        # whole suite, and status() now reports this field, so a level leaked
        # from another file breaks the exact-shape assertion in TestErrorLifecycle.
        morpheus._user_volume = morpheus._FULL_VOLUME
        morpheus._pending_load = None
        morpheus._load_error = None
        morpheus._last_url = None
        # status() caches into this for is_audible(); restoring it matters
        # because the whole suite shares one process, and a leaked "playing"
        # would light HomePanel's face in tests/test_home_panel_smoke.py.
        morpheus._last_status = {"running": False}

        self._tmp = tempfile.TemporaryDirectory()
        self._saved_paths = (morpheus._MPV_LOG_DIR, morpheus._MPV_LOG_FILE,
                             morpheus._FAILURE_LOG_FILE)
        morpheus._MPV_LOG_DIR = self._tmp.name
        morpheus._MPV_LOG_FILE = os.path.join(self._tmp.name, "mpv.log")
        morpheus._FAILURE_LOG_FILE = os.path.join(self._tmp.name,
                                                  "last_failure.log")

    def tearDown(self):
        (morpheus._proc, morpheus._pending_load,
         morpheus._load_error, morpheus._last_url,
         morpheus._last_status, morpheus._user_volume) = self._saved
        (morpheus._MPV_LOG_DIR, morpheus._MPV_LOG_FILE,
         morpheus._FAILURE_LOG_FILE) = self._saved_paths
        self._tmp.cleanup()

    def _status(self, props):
        with mock.patch("tools.morpheus._get_props", return_value=dict(props)):
            return morpheus.status()

    def _arm_load(self, age=0.0):
        """Pretend play() sent a loadfile `age` seconds ago."""
        with mock.patch("tools.morpheus._ensure_mpv", return_value=True), \
                mock.patch("tools.morpheus._checkpoint"), \
                mock.patch("tools.morpheus._ipc"):
            morpheus.play("https://youtu.be/abc")
        morpheus._pending_load -= age


class TestIsAudible(_MorpheusStateCase):
    """is_audible() drives HomePanel's face, and lives or dies on one
    distinction: mpv being UP is not mpv making NOISE.

    It belongs in this file because it leans on exactly the property this file
    exists for — media-exists is time-pos/duration, never `path` and never
    `running` — and the states it must tell apart are the same four.
    """

    _PLAYING = {"media-title": "Song", "pause": False, "time-pos": 12.0,
                "duration": 200.0, "playlist-pos": 0, "playlist-count": 1,
                "idle-active": False}
    # A track that reached its natural end: mpv stays alive and idle. This is
    # the state that would leave the face lit forever on `running` alone.
    _ENDED = {"media-title": None, "pause": False, "time-pos": None,
              "duration": None, "playlist-pos": -1, "playlist-count": 0,
              "idle-active": True}

    def test_false_before_anything_has_been_polled(self):
        self.assertFalse(morpheus.is_audible())

    def test_true_while_a_track_is_actually_playing(self):
        self._status(self._PLAYING)
        self.assertTrue(morpheus.is_audible())

    def test_false_while_paused(self):
        self._status({**self._PLAYING, "pause": True})
        self.assertFalse(morpheus.is_audible())

    def test_false_when_the_track_ended_and_mpv_sits_idle(self):
        # running is True here. The face must NOT stay lit.
        self._status(self._ENDED)
        self.assertTrue(morpheus._last_status["running"])
        self.assertFalse(morpheus.is_audible())

    def test_false_after_a_failed_load(self):
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        self._status(self._ENDED)
        self.assertEqual(morpheus._last_status.get("error"), "load_failed")
        self.assertFalse(morpheus.is_audible())

    def test_false_while_a_load_is_still_resolving(self):
        # Loading has a path but no media yet — conservative is correct, the
        # face should not claim sound that has not started.
        self._arm_load()
        self._status({**self._PLAYING, "time-pos": None, "duration": None,
                      "media-title": None})
        self.assertFalse(morpheus.is_audible())

    def test_false_at_volume_zero(self):
        # A track playing into a muted output is not making noise, and the
        # Hestia face should not sit on amber over it.
        saved, morpheus._user_volume = morpheus._user_volume, 0.0
        try:
            self._status(self._PLAYING)
            self.assertFalse(morpheus.is_audible())
        finally:
            morpheus._user_volume = saved

    def test_false_when_mpv_is_gone(self):
        self._status(self._PLAYING)
        morpheus._proc = None
        morpheus.status()
        self.assertFalse(morpheus.is_audible())

    def test_shutdown_clears_it(self):
        self._status(self._PLAYING)
        morpheus._proc = None          # avoid the real terminate path
        morpheus.shutdown()
        self.assertFalse(morpheus.is_audible())

    def test_the_cache_cannot_be_corrupted_by_a_caller(self):
        # status() hands out a copy; a caller mutating its own dict (the LLM
        # rite record does exactly this kind of thing) must not reach in here.
        # Note the key: status() renames mpv's `pause` property to `paused` on
        # the way out. Writing "pause" here would land on a field nothing reads
        # and the test would pass whether or not the copy exists.
        out = self._status(self._PLAYING)
        self.assertIn("paused", out)
        out["paused"] = True
        self.assertTrue(morpheus.is_audible())


class TestLoadFailureDetected(_MorpheusStateCase):

    def test_failed_load_reports_the_slug(self):
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        out = self._status(_FAILED)
        self.assertEqual(out["error"], "load_failed")

    def test_failed_load_is_logged_with_the_url_and_log_path(self):
        """The log line is the primary signal — Emanon surfaces felhaven.log on
        the dashboard, and it must survive --log-file ever being dropped."""
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        with self.assertLogs("METIS.morpheus", level="ERROR") as cm:
            self._status(_FAILED)
        joined = "\n".join(cm.output)
        self.assertIn("https://youtu.be/abc", joined)
        # It points at the distilled file, not the 420 KB-per-load buffer.
        self.assertIn("last_failure.log", joined)

    def test_slug_is_bare_and_carries_no_mpv_text(self):
        """CONVENTIONS §2: mpv's own lines hold signed googlevideo URLs, so the
        slug must never be built from them."""
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        err = self._status(_FAILED)["error"]
        self.assertRegex(err, r"^[a-z][a-z0-9_]*$")

    def test_error_is_sticky_until_the_next_action(self):
        """One 2 s tick is not enough for a human to read. It must persist."""
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        self._status(_FAILED)
        for _ in range(3):
            self.assertEqual(self._status(_FAILED)["error"], "load_failed")

    def test_failure_is_only_logged_once(self):
        """Sticky in the dict, but not re-logged every tick — a 2 s cadence
        would otherwise bury felhaven.log (and Emanon's panel) in repeats."""
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        with self.assertLogs("METIS.morpheus", level="ERROR") as cm:
            for _ in range(5):
                self._status(_FAILED)
        self.assertEqual(len(cm.output), 1)


class TestNoFalsePositives(_MorpheusStateCase):
    """The expensive failure mode: crying wolf on a perfectly good state."""

    def test_still_loading_is_not_a_failure(self):
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        self.assertNotIn("error", self._status(_LOADING))

    def test_loading_does_not_CONFIRM_the_load(self):
        """The bug this whole file exists to prevent, in miniature.

        mpv sets `path` the moment it accepts a URL — while yt-dlp is still
        resolving, before anyone knows if it will work. Confirming on that
        disarms the detector one tick BEFORE the failure appears, and the
        outage becomes invisible again. It must stay pending until real media
        shows up (caught end-to-end against real mpv, 2026-08-20; the unit
        tests alone were happy because "no error while loading" is true either
        way — only the still-armed state tells the two apart)."""
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        self._status(_LOADING)
        self.assertIsNotNone(morpheus._pending_load)
        # ...and the failure that follows is therefore still caught.
        self.assertEqual(self._status(_FAILED)["error"], "load_failed")

    def test_inside_the_grace_window_is_not_a_failure(self):
        """Covers the few ms between our loadfile write and mpv acting on it."""
        self._arm_load(age=0.0)
        self.assertNotIn("error", self._status(_FAILED))

    def test_natural_end_of_track_is_not_a_failure(self):
        """A finished track leaves mpv in the same idle state a failure does.
        The load was confirmed while it played, so nothing is outstanding."""
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        self._status(_PLAYING)                     # confirms the load
        self.assertIsNone(morpheus._pending_load)
        self.assertNotIn("error", self._status(_STOPPED))

    def test_idle_with_no_pending_load_is_not_a_failure(self):
        """Nothing was ever asked for — e.g. a freshly started mpv."""
        self.assertNotIn("error", self._status(_STOPPED))

    def test_deliberate_stop_disarms_detection(self):
        """MorpheusPanel's stop button can be pressed while a load is still
        resolving, and that leaves mpv in exactly the shape a failed load
        does. A user stop must not be reported as the track failing.

        Narration is NOT a caller here any more — it ducks the volume instead
        (see tests/test_morpheus_duck.py). This clause, written to suppress a
        false positive from that old yield, was also suppressing the true one
        the yield was causing."""
        self._arm_load(age=0.0)
        with mock.patch("tools.morpheus._checkpoint"), \
                mock.patch("tools.morpheus._ipc"):
            morpheus.stop()
        self.assertIsNone(morpheus._pending_load)
        self.assertNotIn("error", self._status(_FAILED))

    def test_dead_pipe_is_not_a_failed_load(self):
        """All-None means the pipe died, not that the track was bad."""
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        out = self._status(_DEAD_PIPE)
        self.assertEqual(out, {"running": False})
        self.assertIsNone(morpheus._load_error)

    def test_duration_alone_confirms_the_load(self):
        """Media exists as soon as mpv knows how long it is, even on a tick
        where time-pos hasn't been published yet."""
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        self._status(dict(_PLAYING, **{"time-pos": None}))
        self.assertIsNone(morpheus._pending_load)


class TestErrorLifecycle(_MorpheusStateCase):

    def test_a_new_play_clears_the_previous_error(self):
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        self._status(_FAILED)
        self.assertEqual(morpheus._load_error, "load_failed")
        self._arm_load()
        self.assertIsNone(morpheus._load_error)

    def test_stop_clears_the_error(self):
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        self._status(_FAILED)
        with mock.patch("tools.morpheus._checkpoint"), \
                mock.patch("tools.morpheus._ipc"):
            morpheus.stop()
        self.assertIsNone(morpheus._load_error)
        self.assertNotIn("error", self._status(_STOPPED))

    def test_shutdown_clears_tracking(self):
        self._arm_load()
        morpheus._proc = None          # nothing to terminate
        morpheus.shutdown()
        self.assertIsNone(morpheus._pending_load)
        self.assertIsNone(morpheus._load_error)

    def test_a_good_load_after_a_failure_reports_clean(self):
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        self._status(_FAILED)
        self._arm_load()
        out = self._status(_PLAYING)
        self.assertNotIn("error", out)
        self.assertEqual(out["title"], "Clair de Lune")

    def test_status_is_unchanged_otherwise(self):
        """The existing shape is a contract the panel reads — don't drift it."""
        out = self._status(_PLAYING)
        # `volume` is not read off the pipe — it is _user_volume, which this
        # module owns outright (see "Ducking"), so it costs no property read.
        self.assertEqual(out, {"running": True, "title": "Clair de Lune",
                               "paused": False, "pos": 7.5, "dur": 314.0,
                               "pl_pos": 0, "pl_n": 1, "volume": 100.0})


class TestFailureReport(_MorpheusStateCase):
    """The distilled record. mpv writes ~420 KB per load and its verbosity is
    not tunable, so the useful few lines have to be cut out of it."""

    # A realistic slice of mpv.log: two error lines buried in verbose noise.
    _MPV_LOG = "\n".join(
        [f"[   0.{i:03d}][v][cplayer] Set property: user-data/mpv/ytdl/json="
         + "x" * 400 for i in range(50)]
        + ["[   2.284][e][ytdl_hook] ERROR: [youtube] abc: Video unavailable",
           "[   2.284][e][ytdl_hook] youtube-dl failed: unexpected error occurred",
           "[   2.628][v][cplayer] Opening failed or was aborted"]
    )

    def _write_mpv_log(self, text):
        with open(morpheus._MPV_LOG_FILE, "w", encoding="utf-8") as f:
            f.write(text)

    def _report(self):
        with open(morpheus._FAILURE_LOG_FILE, encoding="utf-8") as f:
            return f.read()

    def _fail_a_load(self):
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        self._status(_FAILED)

    def test_report_keeps_the_error_lines_and_drops_the_noise(self):
        self._write_mpv_log(self._MPV_LOG)
        self._fail_a_load()
        report = self._report()
        self.assertIn("Video unavailable", report)
        self.assertIn("youtube-dl failed", report)
        self.assertNotIn("json=xxxx", report)

    def test_report_is_small(self):
        """The whole point: readable at a glance, not 420 KB."""
        self._write_mpv_log(self._MPV_LOG)
        self._fail_a_load()
        self.assertLess(len(self._report()), 4000)

    def test_report_names_the_url_that_failed(self):
        self._write_mpv_log(self._MPV_LOG)
        self._fail_a_load()
        self.assertIn("https://youtu.be/abc", self._report())

    def test_report_holds_only_the_most_recent_failure(self):
        """Overwritten, not appended — 'the last one' is what gets asked for."""
        self._write_mpv_log(self._MPV_LOG)
        self._fail_a_load()
        self._write_mpv_log("[   1.000][e][ytdl_hook] ERROR: second failure here")
        self._fail_a_load()
        report = self._report()
        self.assertIn("second failure here", report)
        self.assertNotIn("Video unavailable", report)

    def test_only_the_tail_of_a_huge_log_is_scanned(self):
        """mpv.log can be tens of MB; the read must stay bounded and still find
        the failure, which is always at the end."""
        noise = "[   0.001][v][cplayer] noise " + "y" * 200 + "\n"
        self._write_mpv_log(noise * 3000 + self._MPV_LOG)
        self.assertGreater(os.path.getsize(morpheus._MPV_LOG_FILE),
                           morpheus._FAILURE_SCAN_BYTES)
        self._fail_a_load()
        self.assertIn("Video unavailable", self._report())

    def test_missing_mpv_log_still_writes_a_report(self):
        """--log-file may have been skipped (unwritable dir). The failure is
        still real and still worth recording."""
        self._fail_a_load()
        self.assertIn("no error-level lines", self._report())

    def test_a_broken_report_never_disturbs_status(self):
        """Best-effort: the ERROR line in felhaven.log is the real signal."""
        self._write_mpv_log(self._MPV_LOG)
        with mock.patch("tools.morpheus.open", side_effect=OSError("nope")):
            self._arm_load(age=morpheus._PENDING_GRACE + 1)
            out = self._status(_FAILED)
        self.assertEqual(out["error"], "load_failed")

    def test_shutdown_discards_the_scratch_log_but_keeps_the_report(self):
        self._write_mpv_log(self._MPV_LOG)
        self._fail_a_load()
        morpheus._proc = None
        morpheus.shutdown()
        self.assertFalse(os.path.exists(morpheus._MPV_LOG_FILE))
        self.assertTrue(os.path.exists(morpheus._FAILURE_LOG_FILE))

    def test_shutdown_without_a_log_is_fine(self):
        morpheus._proc = None
        morpheus.shutdown()          # must not raise


class TestMpvLogIsCapped(_MorpheusStateCase):
    """mpv.log is a scratch buffer that grows ~420 KB per track load, and
    Felhaven gets left running for days. Nothing but recycling mpv shrinks it:
    its verbosity is not tunable, truncating leaves mpv's file offset stale so
    the next write re-expands the file with NUL padding, and deleting it while
    mpv holds the handle does not return the space on Windows."""

    # The cap is shrunk rather than the file grown: asserting against a real
    # 50 MB write would put 100 MB through the disk on every test run, in CI
    # too, to prove something about one integer comparison.
    _CAP = 512

    def _write_log(self, size):
        with open(morpheus._MPV_LOG_FILE, "wb") as f:
            f.write(b"x" * size)

    def _play(self):
        """play() against a stub mpv. Returns the patched shutdown()."""
        with mock.patch("tools.morpheus._MPV_LOG_MAX_BYTES", self._CAP),                 mock.patch("tools.morpheus.shutdown") as shutdown,                 mock.patch("tools.morpheus._ensure_mpv", return_value=True),                 mock.patch("tools.morpheus._checkpoint"),                 mock.patch("tools.morpheus._ipc"):
            morpheus.play("https://youtu.be/abc")
        return shutdown

    def test_a_small_log_does_not_recycle_mpv(self):
        self._write_log(self._CAP // 2)
        self._play().assert_not_called()

    def test_an_oversized_log_recycles_mpv(self):
        self._write_log(self._CAP + 1)
        self._play().assert_called_once()

    def test_a_missing_log_is_simply_not_capped(self):
        """No --log-file (unwritable dir) must not break playback."""
        self._play().assert_not_called()

    def test_a_dead_mpv_is_not_recycled(self):
        """Nothing is holding the log, so there is nothing to reclaim — and
        _ensure_mpv() is about to spawn a fresh one that truncates it anyway."""
        self._write_log(self._CAP + 1)
        morpheus._proc = None
        self._play().assert_not_called()

    def test_recycling_still_remembers_the_resume_target(self):
        """shutdown() clears the load tracking but not _last_url — otherwise a
        cap would quietly break resume_music."""
        self._write_log(self._CAP + 1)
        self._play()
        self.assertEqual(morpheus._last_url, "https://youtu.be/abc")

    def test_the_watcher_never_recycles_what_it_is_watching(self):
        """status()/fetch() are READ-ONLY and Kairos drives them every 2 s. The
        cap belongs on play(), which is the only thing that grows the log."""
        self._write_log(self._CAP + 1)
        with mock.patch("tools.morpheus._MPV_LOG_MAX_BYTES", self._CAP),                 mock.patch("tools.morpheus.shutdown") as shutdown:
            self._status(_PLAYING)
            morpheus.fetch()
        shutdown.assert_not_called()


class TestMpvLogWiring(unittest.TestCase):
    """--log-file must actually reach mpv's argv, and must never block startup."""

    def setUp(self):
        self._saved_proc = morpheus._proc
        morpheus._proc = None

    def tearDown(self):
        morpheus._proc = self._saved_proc

    def _spawn_args(self, makedirs_side_effect=None):
        with mock.patch("tools.morpheus.available",
                        return_value={"mpv": "mpv.exe", "ytdlp": "yt-dlp.exe"}), \
                mock.patch("tools.morpheus.os.makedirs",
                           side_effect=makedirs_side_effect), \
                mock.patch("tools.morpheus.subprocess.Popen") as popen, \
                mock.patch("tools.morpheus.open",
                           mock.mock_open(), create=True):
            morpheus._ensure_mpv()
        return popen.call_args[0][0] if popen.call_args else []

    def test_log_file_is_passed_to_mpv(self):
        args = self._spawn_args()
        self.assertTrue(any(a.startswith("--log-file=") for a in args),
                        f"--log-file missing from {args}")

    def test_log_lives_below_logs_not_beside_felhaven_log(self):
        """emanon.py globs logs/*.log non-recursively and parses every match.
        mpv's format is foreign to that parser, so it must stay out of reach."""
        flag = next(a for a in self._spawn_args() if a.startswith("--log-file="))
        path = flag.split("=", 1)[1]
        self.assertEqual(os.path.basename(os.path.dirname(path)), "mpv")
        self.assertEqual(os.path.basename(path), "mpv.log")

    def test_unwritable_log_dir_still_starts_mpv(self):
        """A log is a nicety; playing music is the job."""
        args = self._spawn_args(makedirs_side_effect=OSError("read-only"))
        self.assertTrue(args, "mpv was not spawned at all")
        self.assertFalse(any(a.startswith("--log-file=") for a in args))


if __name__ == "__main__":
    unittest.main()
