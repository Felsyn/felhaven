"""
test_morpheus_duck.py — narration must quiet the music, never kill it.

Why this file exists: on 2026-09-07 "Pythia, play <song>" did nothing. The tool
was called, yt-dlp found the track, mpv accepted the loadfile — and then the
answer's own first spoken chunk stopped it 2.36 s later, while yt-dlp was still
resolving the URL (logs/mpv/mpv.log: loadfile t=0.223, stop t=2.585,
"ytdl_hook: Subprocess failed: killed"). harmonia.play() called morpheus.stop()
before buffering any audio, and stop() disarms load tracking, so the kill
reported nothing at all. Pythia said the song was playing; mpv sat idle.

So these tests are about two properties, not about mpv:
  - narration LOWERS the volume and puts it back, using pure writes that never
    wait for a reply (duck() runs on the audio-feeding thread and unduck() on
    the Tk main thread — a reply-wait on either can stall), and
  - ducking never touches the load tracking that makes a real failure visible
    (test_ducking_does_not_disarm_load_detection is the bug, in one assertion).

No mpv process and no pipe: _ipc and _get_props are mocked throughout.

    python -X utf8 -m unittest tests.test_morpheus_duck
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import morpheus

from tests.test_morpheus_load_failure import _FAILED, _LiveProc


class _DuckCase(unittest.TestCase):
    """Saves and restores every module global these tests touch."""

    def setUp(self):
        self._saved = (morpheus._proc, morpheus._ducked, morpheus._pending_load,
                       morpheus._load_error, morpheus._last_url,
                       morpheus._user_volume, morpheus._fade,
                       morpheus._fade_gain, morpheus._volume_written)
        morpheus._proc = _LiveProc()
        morpheus._ducked = False
        morpheus._pending_load = None
        morpheus._load_error = None
        morpheus._last_url = None
        morpheus._user_volume = morpheus._FULL_VOLUME
        # The third factor in the volume model, reset for the same reason as the
        # duck latch: a leaked gain would silently scale a later test's writes.
        morpheus._fade = None
        morpheus._fade_gain = 1.0
        morpheus._volume_written = -1.0

    def tearDown(self):
        (morpheus._proc, morpheus._ducked, morpheus._pending_load,
         morpheus._load_error, morpheus._last_url,
         morpheus._user_volume, morpheus._fade,
         morpheus._fade_gain, morpheus._volume_written) = self._saved

    def _duck(self):
        """duck() with the pipe mocked. Returns (ipc_mock, get_props_mock).

        _get_props is patched purely so the test FAILS if duck() ever starts
        reading again — the read is what could stall the audio thread."""
        with mock.patch("tools.morpheus._ipc") as ipc, \
                mock.patch("tools.morpheus._get_props") as props:
            morpheus.duck()
        return ipc, props

    def _unduck(self):
        with mock.patch("tools.morpheus._ipc") as ipc:
            morpheus.unduck()
        return ipc


class TestDuckIsFreeWhenIdle(_DuckCase):
    """An idle dashboard must generate zero IPC. harmonia calls duck() once per
    audio chunk and tick_yield() calls unduck() every 60 ms, so anything that
    reaches the pipe unconditionally would be a constant drip of named-pipe
    opens for nothing."""

    def test_duck_with_no_mpv_touches_the_pipe_at_all(self):
        morpheus._proc = None
        ipc, props = self._duck()
        ipc.assert_not_called()
        props.assert_not_called()
        self.assertFalse(morpheus._ducked)

    def test_duck_with_a_dead_mpv_touches_the_pipe_at_all(self):
        morpheus._proc = mock.Mock(poll=mock.Mock(return_value=1))
        ipc, props = self._duck()
        ipc.assert_not_called()
        props.assert_not_called()

    def test_neither_call_ever_waits_for_a_reply(self):
        """duck() runs on the thread that feeds the audio buffer and unduck()
        on the Tk main thread. _get_props' timeout cannot interrupt a readline()
        already blocking, so a reply-wait on either would stall narration or the
        UI. Both must be pure writes."""
        with mock.patch("tools.morpheus._ipc") as ipc, \
                mock.patch("tools.morpheus._get_props") as props:
            morpheus.duck()
            morpheus.unduck()
        props.assert_not_called()
        for call in ipc.call_args_list:
            self.assertNotIn("expect_reply", call.kwargs)
            self.assertEqual(len(call.args), 1)      # command only, no reply flag

    def test_unduck_when_not_ducked_is_free(self):
        self._unduck().assert_not_called()


class TestDuckRoundTrip(_DuckCase):

    def test_duck_lowers_the_volume_without_reading_it(self):
        ipc, props = self._duck()
        props.assert_not_called()
        ipc.assert_called_once_with(["set_property", "volume", 30.0])
        self.assertTrue(morpheus._ducked)

    def test_the_duck_target_is_a_fraction_of_the_user_level(self):
        """With a fixed 30.0 floor, ducking a volume of 20 would make the music
        LOUDER during narration. The ratio is identical at the default."""
        morpheus._user_volume = 20.0
        ipc, _ = self._duck()
        ipc.assert_called_once_with(["set_property", "volume", 6.0])

    def test_unduck_restores_the_user_level_not_full_volume(self):
        """THE tripwire, in one assertion. unduck() used to write the
        _FULL_VOLUME constant, so the first narrated answer after any volume
        change would have snapped the music back to 100."""
        morpheus._user_volume = 40.0
        self._duck()
        ipc = self._unduck()
        ipc.assert_called_once_with(["set_property", "volume", 40.0])
        self.assertFalse(morpheus._ducked)

    def test_unduck_puts_it_back_to_full_at_the_default(self):
        # Kept as the default-case companion to the test above — on its own it
        # proves nothing, because the default IS _FULL_VOLUME.
        self._duck()
        ipc = self._unduck()
        ipc.assert_called_once_with(
            ["set_property", "volume", morpheus._FULL_VOLUME])
        self.assertFalse(morpheus._ducked)

    def test_a_second_duck_costs_nothing(self):
        """The latch is what makes the per-chunk call site cheap: only the
        first duck of a turn touches the pipe."""
        self._duck()
        ipc, props = self._duck()
        ipc.assert_not_called()
        props.assert_not_called()

    def test_unduck_after_mpv_died_clears_the_latch_without_the_pipe(self):
        """Otherwise a stale latch survives into the next mpv and suppresses
        the duck that process actually needs."""
        self._duck()
        morpheus._proc = None
        self._unduck().assert_not_called()
        self.assertFalse(morpheus._ducked)


class TestDuckLeavesLoadTrackingAlone(_DuckCase):

    def _arm_load(self, age=0.0):
        with mock.patch("tools.morpheus._ensure_mpv", return_value=True), \
                mock.patch("tools.morpheus._checkpoint"), \
                mock.patch("tools.morpheus._ipc"):
            morpheus.play("https://youtu.be/abc")
        morpheus._pending_load -= age

    def test_ducking_does_not_disarm_load_detection(self):
        """THE regression guard.

        stop() clears _pending_load — deliberately, so the panel's stop button
        cannot cry wolf. When narration used stop() to yield, that clause also
        swallowed the failure the yield itself was causing: a genuinely broken
        load became invisible. duck() must leave the tracking untouched, so a
        real failure still reports."""
        self._arm_load(age=morpheus._PENDING_GRACE + 1)
        armed = morpheus._pending_load
        self._duck()
        self.assertEqual(morpheus._pending_load, armed)
        with mock.patch("tools.morpheus._get_props", return_value=dict(_FAILED)), \
                mock.patch("tools.morpheus._write_failure_report"):
            out = morpheus.status()
        self.assertEqual(out.get("error"), "load_failed")

    def test_ducking_a_still_resolving_track_does_not_stop_it(self):
        """The 2026-09-07 bug, from the mpv side: no stop, no checkpoint."""
        self._arm_load(age=0.0)
        with mock.patch("tools.morpheus._ipc") as ipc, \
                mock.patch("tools.morpheus._checkpoint") as checkpoint, \
                mock.patch("tools.morpheus._get_props"):
            morpheus.duck()
        checkpoint.assert_not_called()
        for call in ipc.call_args_list:
            self.assertNotIn("stop", call[0][0])


class TestDuckStateLifecycle(_DuckCase):

    def test_shutdown_clears_the_duck_state(self):
        self._duck()
        morpheus._proc = None
        with mock.patch("tools.morpheus._discard_mpv_log"):
            morpheus.shutdown()
        self.assertFalse(morpheus._ducked)

    def test_spawning_a_fresh_mpv_clears_a_stale_latch(self):
        """A fresh mpv starts at volume 100. A latch left by a process that
        died while ducked would suppress the next duck entirely."""
        morpheus._proc = None
        morpheus._ducked = True
        with mock.patch("tools.morpheus.available",
                        return_value={"mpv": "mpv.exe", "ytdlp": "yt-dlp.exe"}), \
                mock.patch("tools.morpheus.os.makedirs"), \
                mock.patch("tools.morpheus.subprocess.Popen"), \
                mock.patch("tools.morpheus.open", mock.mock_open(), create=True):
            morpheus._ensure_mpv()
        self.assertFalse(morpheus._ducked)


class TestVolumeIsNeverPersisted(unittest.TestCase):
    """mpv's watch-later files save every option that CHANGED since playback
    started, and `volume` is in its default set. Without
    --watch-later-options=start, a checkpoint taken while ducked would bake
    volume=30 into that track's resume file and quietly restore it on every
    future play — a permanently quiet song, with a cause nobody would find."""

    def setUp(self):
        self._saved_proc = morpheus._proc
        morpheus._proc = None

    def tearDown(self):
        morpheus._proc = self._saved_proc

    def _spawn_args(self):
        with mock.patch("tools.morpheus.available",
                        return_value={"mpv": "mpv.exe", "ytdlp": "yt-dlp.exe"}), \
                mock.patch("tools.morpheus.os.makedirs"), \
                mock.patch("tools.morpheus.subprocess.Popen") as popen, \
                mock.patch("tools.morpheus.open", mock.mock_open(), create=True):
            morpheus._ensure_mpv()
        return popen.call_args[0][0] if popen.call_args else []

    def test_watch_later_saves_only_the_position(self):
        self.assertIn("--watch-later-options=start", self._spawn_args())


class TestSetVolume(_DuckCase):
    """The user's level, and the one branch that is not obvious: setting it
    mid-narration must NOT reach mpv, or the music jumps to full over the
    answer."""

    def _set(self, level):
        with mock.patch("tools.morpheus._ipc") as ipc:
            returned = morpheus.set_volume(level)
        return ipc, returned

    def test_sets_the_level_and_writes_it(self):
        ipc, returned = self._set(40)
        self.assertEqual(returned, 40.0)
        self.assertEqual(morpheus._user_volume, 40.0)
        ipc.assert_called_once_with(["set_property", "volume", 40.0])

    def test_clamps_both_ends_and_writes_the_clamped_value(self):
        for given, expected in ((250, 100.0), (-30, 0.0)):
            with self.subTest(given=given):
                ipc, returned = self._set(given)
                self.assertEqual(returned, expected)
                ipc.assert_called_once_with(
                    ["set_property", "volume", expected])

    def test_setting_while_ducked_records_without_writing(self):
        # Narration is speaking. Pushing the level through here would undo the
        # duck and talk over the answer.
        self._duck()
        ipc, _ = self._set(70)
        ipc.assert_not_called()
        self.assertEqual(morpheus._user_volume, 70.0)

    def test_unduck_then_applies_the_level_set_mid_narration(self):
        self._duck()
        self._set(70)
        ipc = self._unduck()
        ipc.assert_called_once_with(["set_property", "volume", 70.0])

    def test_setting_with_no_mpv_records_without_writing(self):
        morpheus._proc = None
        ipc, _ = self._set(55)
        ipc.assert_not_called()
        self.assertEqual(morpheus._user_volume, 55.0)

    def test_it_never_reads_the_volume_back(self):
        # The read is what could stall a thread; _user_volume is authoritative
        # precisely so no caller ever needs one.
        with mock.patch("tools.morpheus._ipc"), \
                mock.patch("tools.morpheus._get_props") as props:
            morpheus.set_volume(33)
        props.assert_not_called()


class _TaperCase(_DuckCase):
    """The taper composes with the duck, which is why it is tested beside it
    rather than in a file of its own: the two share one writer, and the bug
    worth catching is one of them writing past the other."""

    def setUp(self):
        super().setUp()
        self._clock = 1000.0
        patcher = mock.patch("tools.morpheus.time.monotonic",
                             side_effect=lambda: self._clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _tick(self, at=None):
        """One tick_fade() at `at` seconds after the taper started."""
        if at is not None:
            self._clock = 1000.0 + at
        with mock.patch("tools.morpheus._ipc") as ipc:
            morpheus.tick_fade()
        return ipc

    def _levels(self, ipc):
        return [call.args[0][2] for call in ipc.call_args_list]


class TestTheTaperIsABoundedDip(_TaperCase):
    """The floor is never 0 and the dip expires on its own — via the backstop,
    _TAPER_HOLD_MAX, when nothing ever calls release_taper(). Everything safe
    about this feature follows from those two facts: a mispredicted handover is
    one dip-and-swell rather than a silence, nothing ever owes a restore, and
    so there is no cancel_taper() anybody can forget to call.

    These tests never call release_taper(), on purpose — they pin the BACKSTOP,
    which is what a load that never confirms falls back on. The early-release
    path is TestReleaseTaperEndsTheHoldEarly, below."""

    def test_it_returns_to_FULL_and_clears_itself(self):
        morpheus.taper()
        ipc = self._tick(morpheus._TAPER_DOWN + morpheus._TAPER_HOLD_MAX
                         + morpheus._TAPER_UP + 0.1)
        self.assertIsNone(morpheus._fade)
        self.assertEqual(morpheus._fade_gain, 1.0)
        self.assertEqual(self._levels(ipc)[-1], morpheus._user_volume)

    def test_a_SKIPPED_tick_still_ends_at_full(self):
        """Edge-derived, not accumulated: the gain is recomputed from one
        monotonic stamp every tick, so a chain that stalls simply makes the ramp
        coarser. A step counter would end up short."""
        morpheus.taper()
        self._tick(0.0)
        ipc = self._tick(morpheus._TAPER_DOWN + morpheus._TAPER_HOLD_MAX
                         + morpheus._TAPER_UP + 5.0)   # the chain stalled hard
        self.assertEqual(self._levels(ipc)[-1], morpheus._user_volume)
        self.assertIsNone(morpheus._fade)

    def test_the_gain_NEVER_reaches_silence(self):
        """A floor of 0 would turn a mispredicted handover into a silence, and
        would make is_audible()'s `_user_volume > 0` a lie.

        Asserted against an ABSOLUTE threshold, not against _TAPER_FLOOR. The
        obvious version — assertGreaterEqual(gain, _TAPER_FLOOR) — is vacuous
        the moment somebody sets that constant to 0, which is precisely the
        change it is supposed to catch. Verified by making that mutation: the
        self-referential form stayed green.
        """
        self.assertGreater(morpheus._TAPER_FLOOR, 0.0,
                           "the dip must never reach silence")
        morpheus.taper()
        span = (morpheus._TAPER_DOWN + morpheus._TAPER_HOLD_MAX
                + morpheus._TAPER_UP)
        for step in range(0, int(span * 10) + 1):
            with self.subTest(at=step / 10):
                gain = morpheus._gain_at(morpheus._fade, 1000.0 + step / 10)
                self.assertGreaterEqual(gain, 0.05)

    def test_it_actually_dips(self):
        morpheus.taper()
        ipc = self._tick(morpheus._TAPER_DOWN + 0.5)     # into the hold
        self.assertAlmostEqual(self._levels(ipc)[-1],
                               morpheus._user_volume * morpheus._TAPER_FLOOR,
                               places=4)

    def test_a_second_taper_does_not_extend_the_hold(self):
        """A trigger that keeps firing — a snapshot that keeps saying "nearly
        over" — must not hold the music down for ever."""
        morpheus.taper()
        first = morpheus._fade
        self._clock += 1.0
        morpheus.taper()
        self.assertIs(morpheus._fade, first)


class TestReleaseTaperEndsTheHoldEarly(_TaperCase):
    """release_taper() is the REAL release since 2026-09-19; taper()'s own
    _TAPER_HOLD_MAX is only the backstop for a load that never confirms. Awen
    calls this the instant anything becomes audible — see awen._note_playing.
    """

    def test_release_pulls_the_up_ramp_forward(self):
        """Released 1s into the hold, the ramp must be BACK UP well before
        _TAPER_HOLD_MAX would have ended it on its own — that gap is the whole
        point of having a release at all."""
        morpheus.taper()
        self._tick(morpheus._TAPER_DOWN + 1.0)          # settle into the hold
        morpheus.release_taper()
        # Long before the backstop would fire, comfortably after a release-
        # driven ramp would have finished.
        ipc = self._tick(morpheus._TAPER_DOWN + 1.0 + morpheus._TAPER_UP + 0.1)
        self.assertEqual(self._levels(ipc)[-1], morpheus._user_volume)
        self.assertIsNone(morpheus._fade)

    def test_the_up_ramp_after_release_still_takes_TAPER_UP_seconds(self):
        """Not instant, and not the backstop's schedule either — release just
        moves WHEN the up-ramp starts; its own length is untouched."""
        morpheus.taper()
        self._tick(morpheus._TAPER_DOWN + 1.0)
        morpheus.release_taper()
        midway = self._tick(morpheus._TAPER_DOWN + 1.0
                            + morpheus._TAPER_UP / 2)
        level = self._levels(midway)[-1]
        floor = morpheus._user_volume * morpheus._TAPER_FLOOR
        self.assertGreater(level, floor)
        self.assertLess(level, morpheus._user_volume)

    def test_release_does_not_jump_the_gain_at_the_moment_it_fires(self):
        """Continuity: release must land exactly where the hold already was —
        the floor — and rise from there, never above or below it."""
        morpheus.taper()
        self._tick(morpheus._TAPER_DOWN + 1.0)
        morpheus.release_taper()
        gain = morpheus._gain_at(morpheus._fade, 1000.0 + morpheus._TAPER_DOWN
                                 + 1.0)
        self.assertAlmostEqual(gain, morpheus._TAPER_FLOOR, places=6)

    def test_release_with_no_taper_running_is_a_quiet_noop(self):
        morpheus.release_taper()
        self.assertIsNone(morpheus._fade)

    def test_release_after_the_backstop_already_fired_is_a_noop(self):
        """Late is safe: nothing left to pull forward once the backstop has
        already released it."""
        morpheus.taper()
        self._tick(morpheus._TAPER_DOWN + morpheus._TAPER_HOLD_MAX
                  + morpheus._TAPER_UP + 0.1)
        self.assertIsNone(morpheus._fade)
        morpheus.release_taper()
        self.assertIsNone(morpheus._fade)

    def test_a_second_release_changes_nothing(self):
        morpheus.taper()
        self._tick(morpheus._TAPER_DOWN + 1.0)
        morpheus.release_taper()
        first = morpheus._fade
        morpheus.release_taper()
        self.assertEqual(morpheus._fade, first)

    def test_release_mid_down_ramp_lets_the_fall_finish_before_rising(self):
        """A release before the floor is even reached must not reverse
        direction mid-fall: the down-ramp still owns the gain until it
        completes, and only then does the (now immediate) up-ramp begin."""
        morpheus.taper()
        self._tick(morpheus._TAPER_DOWN / 2)            # still descending
        morpheus.release_taper()
        at_the_floor = morpheus._gain_at(
            morpheus._fade, 1000.0 + morpheus._TAPER_DOWN)
        self.assertAlmostEqual(at_the_floor, morpheus._TAPER_FLOOR, places=6)
        rising = morpheus._gain_at(
            morpheus._fade, 1000.0 + morpheus._TAPER_DOWN + morpheus._TAPER_UP / 2)
        self.assertGreater(rising, morpheus._TAPER_FLOOR)
        self.assertLess(rising, 1.0)

    def test_a_released_taper_still_never_reaches_silence(self):
        """The floor invariant does not depend on which path ended the hold."""
        self.assertGreater(morpheus._TAPER_FLOOR, 0.0,
                           "the dip must never reach silence")
        morpheus.taper()
        self._tick(morpheus._TAPER_DOWN + 1.0)
        morpheus.release_taper()
        span = morpheus._TAPER_DOWN + 1.0 + morpheus._TAPER_UP
        for step in range(0, int(span * 10) + 1):
            with self.subTest(at=step / 10):
                gain = morpheus._gain_at(morpheus._fade, 1000.0 + step / 10)
                self.assertGreaterEqual(gain, 0.05)


class TestTheTaperAndTheDuckShareOneWriter(_TaperCase):
    """§12: `_user_volume` is authoritative because nothing else can move mpv's
    volume, and "what breaks it is a SECOND writer appearing anywhere". The
    taper is not one — it moves a factor and calls the same writer."""

    def test_unduck_mid_taper_does_NOT_jump_to_full(self):
        """THE tripwire. unduck() used to write _user_volume flat; mid-dip that
        slams the music to full straight over a fade that is still running."""
        morpheus._fade_gain = 0.3
        morpheus._ducked = True
        ipc = self._unduck()
        ipc.assert_called_once_with(
            ["set_property", "volume", morpheus._user_volume * 0.3])

    def test_duck_mid_taper_multiplies_rather_than_replaces(self):
        morpheus._fade_gain = 0.5
        ipc, _ = self._duck()
        ipc.assert_called_once_with(
            ["set_property", "volume",
             morpheus._user_volume * morpheus._DUCK_RATIO * 0.5])

    def test_set_volume_mid_taper_is_scaled_not_slammed(self):
        morpheus._fade_gain = 0.5
        with mock.patch("tools.morpheus._ipc") as ipc:
            morpheus.set_volume(80.0)
        ipc.assert_called_once_with(["set_property", "volume", 40.0])

    def test_status_still_reports_the_USERS_level_not_the_dipped_one(self):
        """control_music(action="volume") does arithmetic on this. A model
        reading 15 mid-dip would do the wrong sum."""
        morpheus._fade_gain = morpheus._TAPER_FLOOR
        morpheus._user_volume = 80.0
        with mock.patch("tools.morpheus._get_props",
                        return_value={"media-title": "x", "pause": False,
                                      "time-pos": 1.0, "duration": 200.0,
                                      "playlist-pos": 0, "playlist-count": 1,
                                      "idle-active": False,
                                      "path": "https://youtu.be/abc"}):
            self.assertEqual(morpheus.status()["volume"], 80.0)


class TestTheTaperIsFreeWhenIdle(_TaperCase):
    """It rides HomePanel's 60 ms chain at 16 Hz. Anything that reaches the pipe
    unconditionally would be a constant drip of named-pipe opens for nothing."""

    def test_no_taper_means_no_pipe_traffic(self):
        ipc = self._tick()
        ipc.assert_not_called()

    def test_the_HOLD_costs_no_writes(self):
        """The step quantiser: a held gain, however long the backstop runs, is
        one write, not a hundred. Not released here on purpose — this is the
        backstop's own hold, which is the one that can run long."""
        morpheus.taper()
        self._tick(morpheus._TAPER_DOWN + 0.1)
        writes = 0
        at = morpheus._TAPER_DOWN + 0.2
        while at < morpheus._TAPER_DOWN + morpheus._TAPER_HOLD_MAX:
            writes += len(self._levels(self._tick(at)))
            at += 0.06
        self.assertEqual(writes, 0)

    def test_a_whole_dip_costs_a_BOUNDED_number_of_writes(self):
        """MEASURED 2026-09-18 against a live mpv on the main thread:
        _apply_volume() is 0.10 ms at the median and 50.7 ms at p95 — the named
        pipe stalls occasionally, and this chain is the Tk main thread. At 60 ms
        the two ramps cost 85 writes; the interval limiter brings that to 30.
        The hold (backstop or released) itself never adds to that — the step
        quantiser keeps a held gain at zero writes regardless of how long it
        runs, which is the property test_the_HOLD_costs_no_writes pins.

        The number is asserted as a CEILING rather than an equality, so tuning
        the ramp lengths does not break it and quietly uncapping it does."""
        morpheus.taper()
        span = (morpheus._TAPER_DOWN + morpheus._TAPER_HOLD_MAX
                + morpheus._TAPER_UP)
        writes = 0
        at = 0.0
        while at < span + 0.5:
            writes += len(self._levels(self._tick(at)))
            at += 0.06                          # HomePanel's _POLL_MS
        self.assertLessEqual(writes, 40)
        self.assertGreater(writes, 10, "a ramp this coarse would be audible")

    def test_a_dead_mpv_is_never_written_to(self):
        morpheus._proc = None
        morpheus.taper()
        ipc = self._tick(0.5)
        ipc.assert_not_called()


class TestTheTaperDoesNotSurviveTheProcess(_TaperCase):
    """A fresh mpv boots at 100 with no idea a taper was running."""

    def test_shutdown_clears_it(self):
        """Reset beside the duck latch and BEFORE the early return, so the
        no-mpv path clears it too — a taper outliving the process it was dipping
        would scale the next one's volume for no reason."""
        morpheus.taper()
        morpheus._fade_gain = 0.4
        morpheus._volume_written = 40.0
        morpheus._proc = None
        with mock.patch("tools.morpheus._discard_mpv_log"):
            morpheus.shutdown()
        self.assertIsNone(morpheus._fade)
        self.assertEqual(morpheus._fade_gain, 1.0)
        self.assertEqual(morpheus._volume_written, -1.0)


class TestRespawnReappliesTheVolume(unittest.TestCase):
    """A fresh mpv boots at 100, and play() recycles mpv when its log grows too
    big — so without this a respawn returns a user who chose 40 to full blast.

    Note this patches _ipc EXPLICITLY. The sibling spawn tests patch
    `tools.morpheus.open` only, so a write added inside _ensure_mpv would land
    on that mock and pass silently rather than be asserted on.

    Popen's double returns poll() -> None, i.e. a LIVE process. A bare
    MagicMock's poll() returns another Mock, which every liveness check in this
    module reads as "the process has exited". That went unnoticed while the
    volume write here bypassed those checks; routing it through _apply_volume()
    put it behind one, and the double had to start telling the truth.
    """

    def setUp(self):
        self._saved = (morpheus._proc, morpheus._user_volume)
        morpheus._proc = None

    def tearDown(self):
        (morpheus._proc, morpheus._user_volume) = self._saved

    def _spawn(self):
        with mock.patch("tools.morpheus.available",
                        return_value={"mpv": "mpv.exe", "ytdlp": "yt-dlp.exe"}), \
                mock.patch("tools.morpheus.os.makedirs"), \
                mock.patch("tools.morpheus.subprocess.Popen",
                           return_value=mock.Mock(poll=lambda: None)), \
                mock.patch("tools.morpheus.open", mock.mock_open(), create=True), \
                mock.patch("tools.morpheus._ipc") as ipc:
            ok = morpheus._ensure_mpv()
        return ok, ipc

    def test_a_non_default_level_is_pushed_to_the_fresh_process(self):
        morpheus._user_volume = 40.0
        ok, ipc = self._spawn()
        self.assertTrue(ok)
        ipc.assert_called_once_with(["set_property", "volume", 40.0])

    def test_the_default_level_costs_no_pipe_traffic(self):
        # mpv already boots at 100; writing it again is pure churn.
        morpheus._user_volume = morpheus._FULL_VOLUME
        ok, ipc = self._spawn()
        self.assertTrue(ok)
        ipc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
