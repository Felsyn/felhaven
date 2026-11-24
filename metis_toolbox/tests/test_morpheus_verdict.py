"""
test_morpheus_verdict.py — play_music answers with the load's verdict.

play_music used to answer now_playing the moment loadfile was written, so a
track YouTube then refused was already "playing" in Pythia's answer (BACKLOG,
2026-09-30; the owner picked "wait for the verdict", 2026-11-23). These pin
each outcome of that wait, and play()'s own yes/no.

Hermetic: search, play, available, status and the clock are faked, so no mpv,
no yt-dlp, no pipe, no network and no real waiting.

Run from the package root:
    python -X utf8 -m unittest tests.test_morpheus_verdict
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import morpheus

_URL = "https://www.youtube.com/watch?v=abcdefghijk"
_ROW = {"title": "Clair de Lune", "channel": "Chan", "duration": 300, "url": _URL}
_STARTED = {"now_playing": "Clair de Lune", "channel": "Chan", "url": _URL}

_LOADING = {"running": True, "pos": None, "dur": None, "loading": True}
_PLAYING = {"running": True, "pos": 1.0, "dur": 300.0, "loading": False}
_FAILED = {"running": True, "pos": None, "dur": None, "loading": False,
           "error": "load_failed"}
_GONE = {"running": False}
_STOPPED = {"running": True, "pos": None, "dur": None, "loading": False}


class _Clock:
    """A monotonic clock that only moves when the code under test sleeps."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _VerdictCase(unittest.TestCase):
    """play_music with a scripted run of status() snapshots."""

    def setUp(self):
        saved = morpheus._last_url
        self.addCleanup(setattr, morpheus, "_last_url", saved)
        self.clock = _Clock()
        self.snaps: list = []
        self.polls = 0
        for target, value in (
                ("available", mock.Mock(return_value={"mpv": "m", "ytdlp": "y", "problem": None})),
                ("search", mock.Mock(return_value=[dict(_ROW)])),
                ("play", mock.Mock(side_effect=self._play)),
                ("status", mock.Mock(side_effect=self._status)),
                ("time", self.clock)):
            p = mock.patch.object(morpheus, target, value)
            p.start()
            self.addCleanup(p.stop)

    def _play(self, url, **_kw):
        morpheus._last_url = url
        return True

    def _status(self):
        self.polls += 1
        snap = self.snaps.pop(0) if len(self.snaps) > 1 else self.snaps[0]
        return dict(snap)


class TestTheVerdict(_VerdictCase):

    def test_a_load_that_becomes_audible_is_now_playing(self):
        self.snaps = [_LOADING, _LOADING, _PLAYING]
        self.assertEqual(morpheus.play_music("clair de lune"), _STARTED)

    def test_a_load_that_fails_is_unavailable(self):
        # It answered now_playing while YouTube refused the stream.
        self.snaps = [_LOADING, _FAILED]
        self.assertEqual(morpheus.play_music("clair de lune"),
                         {"error": "unavailable",
                          "detail": "The track was found but would not load."})

    def test_a_player_that_goes_away_is_unavailable(self):
        self.snaps = [_LOADING, _GONE]
        self.assertEqual(morpheus.play_music("clair de lune").get("error"), "unavailable")

    def test_a_stop_before_the_load_lands_is_not_playing(self):
        self.snaps = [_LOADING, _STOPPED]
        self.assertTrue(morpheus.play_music("clair de lune").get("error", "")
                        .startswith("not_playing"))

    def test_another_play_replacing_ours_still_says_ours_started(self):
        def replaced():
            morpheus._last_url = "https://www.youtube.com/watch?v=zzzzzzzzzzz"
            return dict(_LOADING)
        morpheus.status.side_effect = replaced
        self.assertEqual(morpheus.play_music("clair de lune"), _STARTED)

    def test_past_the_bound_it_says_still_loading_and_stops_asking(self):
        self.snaps = [_LOADING]
        out = morpheus.play_music("clair de lune")
        self.assertEqual(out, {**_STARTED, "loading": True})
        wait = getattr(morpheus, "_VERDICT_WAIT", 15.0)
        poll = getattr(morpheus, "_VERDICT_POLL", 0.5)
        self.assertLessEqual(self.polls, int(wait / poll) + 2)


class TestPlaySaysWhetherItSentAnything(unittest.TestCase):

    def test_a_player_that_cannot_start_is_unavailable_at_once(self):
        with mock.patch.object(morpheus, "available",
                               return_value={"mpv": "m", "ytdlp": "y", "problem": None}), \
                mock.patch.object(morpheus, "search", return_value=[dict(_ROW)]), \
                mock.patch.object(morpheus, "play", return_value=False), \
                mock.patch.object(morpheus, "status") as status:
            out = morpheus.play_music("clair de lune")
        self.assertEqual(out, {"error": "unavailable",
                               "detail": "The player could not start."})
        status.assert_not_called()

    def test_play_returns_false_for_a_refused_url(self):
        with self.assertLogs("METIS.morpheus", "WARNING"):
            self.assertIs(morpheus.play("file:///C:/song.mp3"), False)

    def test_play_returns_false_when_mpv_will_not_start(self):
        with mock.patch.object(morpheus, "_recycle_if_log_is_huge"), \
                mock.patch.object(morpheus, "_ensure_mpv", return_value=False):
            self.assertIs(morpheus.play(_URL), False)

    def test_play_returns_true_once_loadfile_is_sent(self):
        saved = (morpheus._last_url, morpheus._pending_load, morpheus._load_error)
        self.addCleanup(lambda: (setattr(morpheus, "_last_url", saved[0]),
                                 setattr(morpheus, "_pending_load", saved[1]),
                                 setattr(morpheus, "_load_error", saved[2])))
        with mock.patch.object(morpheus, "_recycle_if_log_is_huge"), \
                mock.patch.object(morpheus, "_ensure_mpv", return_value=True), \
                mock.patch.object(morpheus, "_ipc") as ipc:
            self.assertIs(morpheus.play(_URL), True)
        self.assertIn(["loadfile", _URL, "replace"], [c.args[0] for c in ipc.call_args_list])

    def test_replay_with_a_player_that_cannot_start_is_unavailable(self):
        saved = morpheus._last_url
        self.addCleanup(setattr, morpheus, "_last_url", saved)
        morpheus._last_url = _URL
        with mock.patch.object(morpheus, "available",
                               return_value={"mpv": "m", "ytdlp": "y", "problem": None}), \
                mock.patch.object(morpheus, "play", return_value=False):
            self.assertEqual(morpheus.control_music("replay"),
                             {"error": "unavailable", "detail": "The player could not start."})


class TestAVolumeThatIsntANumberChangesNothing(unittest.TestCase):
    """min() keeps its first argument against a NaN, so set_volume(nan) set
    full volume (measured 2026-11-23)."""

    def test_nan_keeps_the_level(self):
        saved = morpheus._user_volume
        self.addCleanup(setattr, morpheus, "_user_volume", saved)
        morpheus._user_volume = 40.0
        with mock.patch.object(morpheus, "_apply_volume"):
            self.assertEqual(morpheus.set_volume(float("nan")), 40.0)
        self.assertEqual(morpheus._user_volume, 40.0)

    def test_a_number_out_of_range_is_still_clamped(self):
        saved = morpheus._user_volume
        self.addCleanup(setattr, morpheus, "_user_volume", saved)
        with mock.patch.object(morpheus, "_apply_volume"):
            self.assertEqual(morpheus.set_volume(250), 100.0)
            self.assertEqual(morpheus.set_volume(-5), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
