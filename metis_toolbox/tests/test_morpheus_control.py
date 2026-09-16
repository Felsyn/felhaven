"""
test_morpheus_control.py — the control_music state machine.

control_music is the only brain tool in the stack that MUTATES a running
subprocess, and two of its properties are easy to "simplify" back into bugs:

  * pause/resume must be idempotent writes (set_property), never mpv's
    `cycle pause`. A toggle is safe for the panel's button, where a human reads
    the glyph first, and unsafe for a model that cannot see the current state —
    a retried call turns the music back on. TestPauseIsNotAToggle pins that.
  * every mutating action must refuse when mpv is not running. _ipc() never
    raises and silently does nothing on a dead pipe, so without the gate the
    tool reports success for a command nothing heard.

Hermetic: _ipc and status are patched throughout, so no mpv, no pipe, no
binaries, no network, no Tk.

Run from the package root:
    python -X utf8 -m unittest tests.test_morpheus_control
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pythia
from tools import morpheus

_PLAYING = {"running": True, "title": "Clair de Lune", "paused": False,
            "pos": 12.0, "dur": 300.0, "pl_pos": 0, "pl_n": 1}
_IDLE = {"running": False}


class _LiveProc:
    """Stands in for a running mpv process (the test_morpheus_duck idiom)."""

    def poll(self):
        return None


# Extra arguments an action needs beyond its name. The loops below walk the
# live enum rather than a list, so an action that takes a parameter gets one
# entry here instead of a rewritten loop.
_ACTION_ARGS: "dict[str, dict[str, object]]" = {"volume": {"level": 50}}


class _ControlCase(unittest.TestCase):
    """Shared rig: a live-looking player and a captured _ipc."""

    def setUp(self):
        self._saved_last_url = morpheus._last_url
        self.ipc = mock.patch("tools.morpheus._ipc").start()
        self.addCleanup(mock.patch.stopall)

    def tearDown(self):
        morpheus._last_url = self._saved_last_url

    @staticmethod
    def _playing():
        return mock.patch("tools.morpheus.status", return_value=dict(_PLAYING))

    @staticmethod
    def _idle():
        return mock.patch("tools.morpheus.status", return_value=dict(_IDLE))

    def _commands(self):
        """Every IPC command issued, as lists."""
        return [c.args[0] for c in self.ipc.call_args_list if c.args]


class TestActionsIssueTheRightCommand(_ControlCase):
    def test_stop_next_previous_reach_mpv(self):
        for action, expected in (("next", ["playlist-next", "weak"]),
                                 ("previous", ["playlist-prev", "weak"])):
            with self.subTest(action=action):
                self.ipc.reset_mock()
                with self._playing():
                    morpheus.control_music(action=action)
                self.assertIn(expected, self._commands())

    def test_stop_checkpoints_before_stopping(self):
        # stop() bookmarks the position first, so resume survives a mid-session
        # stop — the order is the feature, not just the pair of calls.
        with self._playing():
            morpheus.control_music(action="stop")
        cmds = self._commands()
        self.assertIn(["write-watch-later-config"], cmds)
        self.assertIn(["stop"], cmds)
        self.assertLess(cmds.index(["write-watch-later-config"]), cmds.index(["stop"]))

    def test_every_action_returns_a_status_snapshot(self):
        morpheus._last_url = "https://youtu.be/abc"
        for action in morpheus._CONTROL_ACTIONS:
            with self.subTest(action=action):
                with self._playing(), mock.patch("tools.morpheus.play"), \
                        mock.patch("tools.morpheus.available",
                                   return_value={"mpv": "mpv.exe", "ytdlp": "y.exe"}):
                    out = morpheus.control_music(
                        action=action, **_ACTION_ARGS.get(action, {}))
                self.assertNotIn("error", out)
                self.assertEqual(out["action"], action)
                self.assertIn("status", out)


class TestPauseIsNotAToggle(_ControlCase):
    """The assertion that stops someone routing this back through cycle."""

    def test_pause_and_resume_set_the_property_explicitly(self):
        for action, flag in (("pause", True), ("resume", False)):
            with self.subTest(action=action):
                self.ipc.reset_mock()
                with self._playing():
                    morpheus.control_music(action=action)
                self.assertEqual(self._commands(), [["set_property", "pause", flag]])

    def test_no_action_ever_issues_cycle_pause(self):
        morpheus._last_url = "https://youtu.be/abc"
        for action in morpheus._CONTROL_ACTIONS:
            with self.subTest(action=action):
                self.ipc.reset_mock()
                with self._playing(), mock.patch("tools.morpheus.play"), \
                        mock.patch("tools.morpheus.available",
                                   return_value={"mpv": "mpv.exe", "ytdlp": "y.exe"}):
                    morpheus.control_music(action=action)
                self.assertNotIn(["cycle", "pause"], self._commands())

    def test_pausing_twice_leaves_it_paused(self):
        # The real regression: a toggle would un-pause on the second call, so a
        # model retrying a call it thought had failed restarts the music.
        with self._playing():
            morpheus.control_music(action="pause")
            morpheus.control_music(action="pause")
        self.assertEqual(self._commands(),
                         [["set_property", "pause", True]] * 2)


class TestDeadPlayerIsRefused(_ControlCase):
    def test_mutating_actions_refuse_when_mpv_is_not_running(self):
        # replay is exempt — it starts mpv itself, via play().
        for action in ("pause", "resume", "stop", "next", "previous"):
            with self.subTest(action=action):
                self.ipc.reset_mock()
                with self._idle():
                    out = morpheus.control_music(action=action)
                self.assertTrue(out["error"].startswith("not_playing"), out["error"])
                self.assertEqual(self._commands(), [],
                                 "refused, but still wrote to the pipe")

    def test_status_still_answers_when_nothing_is_playing(self):
        # Not playing is a normal state, not a fault — the model must be able
        # to ask "what's on?" and be told "nothing".
        with self._idle():
            out = morpheus.control_music(action="status")
        self.assertNotIn("error", out)
        self.assertEqual(out["status"], _IDLE)


class TestReplay(_ControlCase):
    _AVAIL = {"mpv": "mpv.exe", "ytdlp": "yt-dlp.exe"}

    def test_replays_the_last_played_url(self):
        morpheus._last_url = "https://youtu.be/abc"
        with self._playing(), \
                mock.patch("tools.morpheus.available", return_value=self._AVAIL), \
                mock.patch("tools.morpheus.play") as play:
            out = morpheus.control_music(action="replay")
        play.assert_called_once_with("https://youtu.be/abc")
        self.assertEqual(out["action"], "replay")

    def test_replay_works_from_a_stopped_player(self):
        # The whole point of replay: mpv is idle, and this brings it back.
        morpheus._last_url = "https://youtu.be/abc"
        with self._idle(), \
                mock.patch("tools.morpheus.available", return_value=self._AVAIL), \
                mock.patch("tools.morpheus.play") as play:
            out = morpheus.control_music(action="replay")
        self.assertNotIn("error", out)
        play.assert_called_once_with("https://youtu.be/abc")

    def test_nothing_played_yet_degrades(self):
        morpheus._last_url = None
        with self._playing():
            self.assertEqual(morpheus.control_music(action="replay")["error"],
                             "nothing_to_resume")

    def test_missing_mpv_degrades(self):
        morpheus._last_url = "https://youtu.be/abc"
        with self._playing(), \
                mock.patch("tools.morpheus.available",
                           return_value={"mpv": None, "ytdlp": "yt-dlp.exe"}), \
                mock.patch("tools.morpheus.play") as play:
            self.assertEqual(morpheus.control_music(action="replay")["error"],
                             "player_unavailable")
        play.assert_not_called()


class TestVolumeAction(_ControlCase):
    def setUp(self):
        super().setUp()
        self._saved_volume = morpheus._user_volume
        self.addCleanup(setattr, morpheus, "_user_volume", self._saved_volume)
        # set_volume() gates on _proc directly (the same check status() makes
        # first), so the class's mocked status() is not enough on its own.
        self._saved_proc = morpheus._proc
        self.addCleanup(setattr, morpheus, "_proc", self._saved_proc)
        morpheus._proc = _LiveProc()

    def test_it_sets_the_level_and_reports_it_back(self):
        with self._playing():
            out = morpheus.control_music(action="volume", level=35)
        self.assertEqual(out["volume"], 35.0)
        self.assertEqual(morpheus._user_volume, 35.0)
        self.assertIn(["set_property", "volume", 35.0], self._commands())

    def test_no_level_is_a_slug_and_changes_nothing(self):
        with self._playing():
            out = morpheus.control_music(action="volume")
        self.assertTrue(out["error"].startswith("no_level:"), out["error"])
        self.assertEqual(morpheus._user_volume, self._saved_volume)
        self.assertEqual(self._commands(), [])

    def test_it_works_with_no_mpv_running(self):
        # The level is module state and _ensure_mpv applies it on the next
        # spawn, so "turn it down" before pressing play is meaningful. This is
        # a deliberate exception to the dead-pipe gate.
        morpheus._proc = None
        with self._idle():
            out = morpheus.control_music(action="volume", level=25)
        self.assertNotIn("error", out)
        self.assertEqual(morpheus._user_volume, 25.0)

    def test_the_status_it_returns_carries_the_new_level(self):
        # status() is mocked here, so assert on the module state the real one
        # reads — the wiring is covered in test_morpheus_load_failure.
        with self._playing():
            morpheus.control_music(action="volume", level=15)
        self.assertEqual(morpheus._user_volume, 15.0)


class TestUnknownAction(_ControlCase):
    def test_unknown_action_is_a_slug_and_touches_nothing(self):
        with self._playing():
            out = morpheus.control_music(action="obliterate")
        self.assertTrue(out["error"].startswith("unknown_action:"), out["error"])
        self.assertEqual(self._commands(), [])

    def test_the_detail_lists_the_legal_actions(self):
        # So a model that guessed can correct itself without another round trip.
        with self._playing():
            out = morpheus.control_music(action="skip")
        for action in morpheus._CONTROL_ACTIONS:
            self.assertIn(action, out["error"])

    def test_default_action_is_the_harmless_one(self):
        # A model that omits `action` must read state, never mutate it.
        with self._playing():
            out = morpheus.control_music()
        self.assertEqual(out["action"], "status")
        self.assertEqual(self._commands(), [])


class TestRegistration(unittest.TestCase):
    def test_control_music_reached_pythia_and_resume_music_did_not_linger(self):
        self.assertIn("control_music", pythia._DISPATCH)
        self.assertNotIn("resume_music", pythia._DISPATCH,
                         "absorbed into control_music(action='replay') — two "
                         "meanings of 'resume' on one tool surface is the bug")

    def test_the_schema_enum_is_derived_from_the_constant(self):
        # Restating the actions in the schema is a copy that drifts; the enum
        # must BE _CONTROL_ACTIONS, not a literal that happens to match today.
        schema = next(d for d in morpheus.TOOL_DEFINITIONS
                      if d["function"]["name"] == "control_music")
        enum = schema["function"]["parameters"]["properties"]["action"]["enum"]
        self.assertEqual(enum, list(morpheus._CONTROL_ACTIONS))

    def test_every_action_is_handled(self):
        # The enum is what the model is told it may send; anything advertised
        # and unhandled would fall through to the previous() branch.
        with mock.patch("tools.morpheus.status", return_value=dict(_PLAYING)), \
                mock.patch("tools.morpheus._ipc"), \
                mock.patch("tools.morpheus.play"), \
                mock.patch("tools.morpheus.available",
                           return_value={"mpv": "mpv.exe", "ytdlp": "y.exe"}):
            saved, morpheus._last_url = morpheus._last_url, "https://youtu.be/abc"
            try:
                for action in morpheus._CONTROL_ACTIONS:
                    with self.subTest(action=action):
                        self.assertNotIn("error", morpheus.control_music(
                            action, **_ACTION_ARGS.get(action, {})))
            finally:
                morpheus._last_url = saved


if __name__ == "__main__":
    unittest.main()
