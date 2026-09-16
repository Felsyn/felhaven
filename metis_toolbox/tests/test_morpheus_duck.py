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
                       morpheus._user_volume)
        morpheus._proc = _LiveProc()
        morpheus._ducked = False
        morpheus._pending_load = None
        morpheus._load_error = None
        morpheus._last_url = None
        morpheus._user_volume = morpheus._FULL_VOLUME

    def tearDown(self):
        (morpheus._proc, morpheus._ducked, morpheus._pending_load,
         morpheus._load_error, morpheus._last_url,
         morpheus._user_volume) = self._saved

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


class TestRespawnReappliesTheVolume(unittest.TestCase):
    """A fresh mpv boots at 100, and play() recycles mpv when its log grows too
    big — so without this a respawn returns a user who chose 40 to full blast.

    Note this patches _ipc EXPLICITLY. The sibling spawn tests patch
    `tools.morpheus.open` only, so a write added inside _ensure_mpv would land
    on that mock and pass silently rather than be asserted on.
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
                mock.patch("tools.morpheus.subprocess.Popen"), \
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
