"""
test_morpheus_load_failure.py — the silent-failure guard.

Why this file exists: on 2026-08-20 Morpheus stopped playing anything. A stale
yt-dlp resolved URLs mpv then got a 403 on, so every play() was a no-op — and
NOTHING said so. The panel showed "(loading…)" indefinitely and felhaven.log had
not one METIS.morpheus line across the whole outage. The bug was findable only
by running mpv by hand outside the app.

So these tests are not really about mpv. They are about the property that was
missing: a failed load must become visible. They pin the states apart
(loading / playing / ended / skipped / failed / deliberately stopped), because
most of those look IDENTICAL to mpv and only Python-side intent separates them —
get that wrong and the panel cries wolf on every stop, or on every track that
reaches its natural end.

This file used to say the failed and STOPPED states were the identical pair.
That was backwards, and the fixtures carried the error: a deliberate stop is the
one state a property does separate (playlist-count 0), while ended, skipped and
failed are the three that are indistinguishable. Corrected 2026-09-16 — see the
module-level _ENDED.

No mpv process and no pipe: _get_props is mocked with the real signatures
measured off mpv 0.41 on 2026-08-20, extended 2026-09-16 (recorded in
morpheus.py's "Load failures" docstring section).

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
# `path` is READ now — status() has published it since 2026-09-20, because it
# is the only exact identity in the snapshot and awen needs it to tell its own
# pick from a song the listener started. It used to sit here unread, purely as
# documentation of the trap, and its VALUE was an assumption nothing checked.
#
# Measured against a live mpv on 2026-09-21, because the assumption was doing
# real work by then: `path` is the watch URL handed to loadfile, NOT the signed
# googlevideo stream URL yt-dlp resolves it to (ytdl_hook rewrites
# stream-open-filename, and leaves this alone). That distinction is the whole
# fixture. Had it gone the other way, euterpe.video_id() would return None on
# every real load, awen._whose_song would fall back to the title comparison
# every time, and the id half of the 2026-09-20 fix would be dead code with
# this entire file still green — a check that never runs looks exactly like one
# that passes.
#
# The trap still stands, and publishing the field did not soften it: `path` is
# set during LOADING as well as playing, so confirming a load on it is precisely
# the bug test_loading_does_not_CONFIRM_the_load guards. It is in props now and
# must go on being ignored by _judge_pending_load.
#
# Every fixture here must therefore CARRY it. The real _get_props returns one
# entry per requested name — None for anything it could not read — so a stub
# that omits a key models something the real function cannot produce, and
# status() indexing it would KeyError on a state that cannot happen.

_LOADING = {"media-title": "watch?v=abc", "pause": False, "time-pos": None,
            "duration": None, "playlist-pos": 0, "playlist-count": 1,
            "idle-active": False, "path": "https://youtu.be/abc"}

_PLAYING = {"media-title": "Clair de Lune", "pause": False, "time-pos": 7.5,
            "duration": 314.0, "playlist-pos": 0, "playlist-count": 1,
            "idle-active": False, "path": "https://youtu.be/abc"}

_FAILED = {"media-title": None, "pause": False, "time-pos": None,
           "duration": None, "playlist-pos": -1, "playlist-count": 1,
           "idle-active": True, "path": None}

# A track that reached its natural end. IDENTICAL to _FAILED, field for field —
# and that is not this fixture being lazy, it is what mpv actually does.
#
# This file used to model a natural end as _STOPPED (playlist-count 0), which
# was simply wrong, and wrong in the direction that flatters the code: it made
# the two states look separable by a property when they are not. The tests
# passed anyway, because a real natural end has its load CONFIRMED long before
# EOF, so nothing was outstanding either way — the bad fixture was never the
# thing keeping them green. Corrected 2026-09-16 against real mpv (local file
# and YouTube URL, same result):
#     ended -> idle-active True, path None, playlist-pos -1, playlist-count 1
#
# Kept as its own name rather than aliased to _FAILED so a test reads as the
# state it means, and so the day mpv does start distinguishing them there is one
# place to change.
_ENDED = {"media-title": None, "pause": False, "time-pos": None,
          "duration": None, "playlist-pos": -1, "playlist-count": 1,
          "idle-active": True, "path": None}

# A DELIBERATE stop — the only one of the four idle shapes any property
# separates, by playlist-count 0. Ended, skipped and failed all leave 1, so
# intent tracked in Python is the only thing that tells those three apart.
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
                "idle-active": False, "path": "https://youtu.be/abc"}
    # A track that reached its natural end: mpv stays alive and idle. This is
    # the state that would leave the face lit forever on `running` alone.
    # playlist-count 1, not 0 — see the module-level _ENDED for the measurement
    # and for why the 0 this used to carry was wrong.
    _ENDED = {"media-title": None, "pause": False, "time-pos": None,
              "duration": None, "playlist-pos": -1, "playlist-count": 1,
              "idle-active": True, "path": None}

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
        """A finished track leaves mpv in the EXACT idle state a failure does —
        _ENDED and _FAILED are identical dicts. Confirmation while it played is
        the only thing separating them, which is the point.

        This asserted against _STOPPED until 2026-09-16, so it was really
        testing the stopped shape under the natural-end name."""
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        self._status(_PLAYING)                     # confirms the load
        self.assertIsNone(morpheus._pending_load)
        self.assertNotIn("error", self._status(_ENDED))

    def test_the_ended_and_failed_shapes_really_are_identical(self):
        """The load-bearing fact, asserted rather than left in a comment: if a
        future mpv starts separating them, this fails and the docstring's
        "decided in Python, never from properties" rule can be revisited."""
        self.assertEqual(_ENDED, _FAILED)

    def test_only_a_deliberate_stop_differs_by_playlist_count(self):
        self.assertEqual(_ENDED["playlist-count"], 1)
        self.assertEqual(_FAILED["playlist-count"], 1)
        self.assertEqual(_STOPPED["playlist-count"], 0)

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
                               "pl_pos": 0, "pl_n": 1, "volume": 100.0,
                               "idle": False, "loading": False,
                               "path": "https://youtu.be/abc"})

    def test_status_publishes_the_loaded_path(self):
        """The only exact identity in the snapshot. awen compares video ids
        with it rather than comparing two spellings of a title, which is what
        filed the DJ's own picks as the listener's taste until 2026-09-20."""
        self.assertEqual(self._status(_PLAYING)["path"],
                         "https://youtu.be/abc")

    def test_a_failed_load_publishes_no_path_though_last_url_still_names_it(self):
        """The argument for publishing `path` rather than _last_url, made
        executable. _last_url is INTENT — it records what play() asked for and
        goes on naming it after the load failed. `path` is what mpv HOLDS, and
        a caller asking "which URL produced the audio I can hear" is asking
        about the second one. Attributing a song off the first would credit the
        DJ with a pick that never made a sound."""
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        out = self._status(_FAILED)
        self.assertIsNone(out["path"])
        self.assertEqual(morpheus._last_url, "https://youtu.be/abc")

    def test_path_still_does_not_CONFIRM_a_load(self):
        """It is in props now. That is not permission to read it here — `path`
        is set the instant mpv accepts the URL, while yt-dlp is still resolving
        and long before anyone knows whether it will work."""
        self._arm_load()
        self._status(_LOADING)
        self.assertIsNotNone(morpheus._pending_load,
                             "a load was confirmed on `path` alone")

    def test_the_model_never_sees_the_path(self):
        """control_music hands its result straight into the model's context,
        and a resolved YouTube URL is not a thing to put there. Measured off a
        real load failure: the stream URL behind one song was 1110 characters
        of signed, expiring, IP-bound query string. In-process callers get the
        field; the model does not."""
        with mock.patch("tools.morpheus._get_props",
                        return_value=dict(_PLAYING)):
            out = morpheus.control_music(action="status")
        self.assertNotIn("path", out["status"])
        self.assertEqual(out["status"]["title"], "Clair de Lune")


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
        with mock.patch("tools.morpheus._MPV_LOG_MAX_BYTES", self._CAP), \
                mock.patch("tools.morpheus.shutdown") as shutdown, \
                mock.patch("tools.morpheus._ensure_mpv", return_value=True), \
                mock.patch("tools.morpheus._checkpoint"), \
                mock.patch("tools.morpheus._ipc"):
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
        with mock.patch("tools.morpheus._MPV_LOG_MAX_BYTES", self._CAP), \
                mock.patch("tools.morpheus.shutdown") as shutdown:
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


class TestIdleAndLoadingAreReported(_MorpheusStateCase):
    """status() reads idle-active for _judge_pending_load and knows whether a
    load is outstanding, and until 2026-09-16 published neither — so NO caller
    could tell a stream still resolving from one that had ended. Both look the
    same from outside: no title, no time-pos, no duration.

    That gap is why these two keys exist. They are not new information, they are
    information that was being computed and thrown away.
    """

    def test_playing_is_neither_idle_nor_loading(self):
        out = self._status(_PLAYING)
        self.assertIs(out["idle"], False)
        self.assertIs(out["loading"], False)

    def test_a_resolving_stream_reads_as_loading_and_not_idle(self):
        self._arm_load()
        out = self._status(_LOADING)
        self.assertIs(out["loading"], True)
        self.assertIs(out["idle"], False)

    def test_an_ended_track_reads_as_idle_and_not_loading(self):
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        self._status(_PLAYING)                     # confirm the load
        out = self._status(_ENDED)
        self.assertIs(out["idle"], True)
        self.assertIs(out["loading"], False)

    def test_loading_and_ended_are_now_distinguishable(self):
        """The whole point, stated as one assertion. These two produce the same
        title/pos/dur, so before this pair there was nothing to branch on."""
        self._arm_load()
        loading = self._status(_LOADING)
        self._status(_PLAYING)                     # confirm, so nothing pends
        ended = self._status(_ENDED)
        self.assertEqual((loading["title"], loading["pos"], loading["dur"]),
                         (loading["title"], None, None))
        self.assertEqual((ended["pos"], ended["dur"]), (None, None))
        self.assertNotEqual((loading["idle"], loading["loading"]),
                            (ended["idle"], ended["loading"]))

    def test_a_failed_load_is_no_longer_loading(self):
        """The verdict is in, so the caller must stop waiting on it."""
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        out = self._status(_FAILED)
        self.assertIs(out["loading"], False)
        self.assertEqual(out["error"], "load_failed")

    def test_inside_the_grace_window_still_reads_as_loading(self):
        """Undecided is not decided. Reporting this as ended would let a caller
        act on a load that is about to be confirmed."""
        self._arm_load(age=0.0)
        self.assertIs(self._status(_FAILED)["loading"], True)

    def test_a_dead_pipe_reports_neither(self):
        """{"running": False} keeps its exact shape — a caller that branches on
        `running` first must not find half a snapshot behind it."""
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        self.assertEqual(self._status(_DEAD_PIPE), {"running": False})


class TestLastSnapshot(_MorpheusStateCase):
    """last_snapshot() is is_audible()'s bargain generalised: the whole cached
    dict, free, so a caller wanting more than one field still never becomes a
    second status() caller. That matters beyond cost — status() runs
    _judge_pending_load, so a second poller would confirm and condemn loads on
    its own schedule, between Kairos's ticks."""

    def test_empty_shape_before_anything_is_polled(self):
        self.assertEqual(morpheus.last_snapshot(), {"running": False})

    def test_it_returns_what_status_last_saw(self):
        out = self._status(_PLAYING)
        self.assertEqual(morpheus.last_snapshot(), out)

    def test_it_never_touches_the_pipe(self):
        """The reason it exists. _get_props is patched to explode: a single IPC
        read from here would be a round trip on somebody's fast chain."""
        self._status(_PLAYING)
        with mock.patch("tools.morpheus._get_props",
                        side_effect=AssertionError("last_snapshot did IPC")):
            self.assertTrue(morpheus.last_snapshot()["running"])

    def test_the_caller_cannot_corrupt_the_cache(self):
        """A copy, not the live dict — the TestIsAudible precedent. A caller
        that mutates its own snapshot must not reach in here."""
        self._status(_PLAYING)
        snap = morpheus.last_snapshot()
        snap["title"] = "something else"
        snap["running"] = False
        self.assertEqual(morpheus.last_snapshot()["title"], "Clair de Lune")
        self.assertTrue(morpheus.last_snapshot()["running"])

    def test_it_carries_the_error_slug_through(self):
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        self._status(_FAILED)
        self.assertEqual(morpheus.last_snapshot()["error"], "load_failed")

    def test_it_follows_mpv_going_away(self):
        self._status(_PLAYING)
        morpheus._proc = None
        morpheus.status()
        self.assertEqual(morpheus.last_snapshot(), {"running": False})


if __name__ == "__main__":
    unittest.main()
