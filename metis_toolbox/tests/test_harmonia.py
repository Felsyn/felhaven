"""
test_harmonia.py — unit tests for harmonia.py (the output-device authority).

No real audio device: sounddevice is stubbed into sys.modules exactly like the
old test_calliope.py TestPlayDegradation did. tools.morpheus.stop is mocked so
no mpv process is ever touched. harmonia.play() is async by design (there is no
synchronous escape hatch — that's the point of the module), so round-trip tests
poll is_playing() with a short timeout instead of asserting instantly.

    python -X utf8 -m unittest tests.test_harmonia
"""

import os
import sys
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import harmonia


def _wait_until(cond, timeout=2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class _HarmoniaBase(unittest.TestCase):
    """Fully tear down the play thread and reset the epoch/pending counters
    around every test, so a leftover thread or stale count from one test can
    never leak into the next."""

    def setUp(self):
        harmonia.shutdown()          # stop any thread left by a prior test
        harmonia._epoch = 0
        harmonia._pending = 0
        while not harmonia._queue.empty():
            harmonia._queue.get_nowait()

        self._sd_stub = types.ModuleType("sounddevice")
        self._sd_stub.play = mock.Mock()
        self._sd_stub.wait = mock.Mock()
        self._sd_stub.stop = mock.Mock()
        self._modules_patcher = mock.patch.dict(sys.modules, {"sounddevice": self._sd_stub})
        self._modules_patcher.start()

        self._morpheus_patcher = mock.patch.object(harmonia.morpheus, "stop")
        self.mock_morpheus_stop = self._morpheus_patcher.start()

    def tearDown(self):
        harmonia.shutdown()
        self._morpheus_patcher.stop()
        self._modules_patcher.stop()


class TestYieldsMorpheus(_HarmoniaBase):
    def test_play_calls_morpheus_stop_first(self):
        harmonia.play(np.zeros(4, dtype=np.float32), 24000, tag="t")
        self.mock_morpheus_stop.assert_called_once()

    def test_play_still_enqueues_if_morpheus_stop_raises(self):
        self.mock_morpheus_stop.side_effect = RuntimeError("pipe gone")
        harmonia.play(np.zeros(4, dtype=np.float32), 24000)   # must not raise
        self.assertTrue(harmonia.is_playing())
        self.assertTrue(_wait_until(lambda: not harmonia.is_playing()))


class TestPlayRoundTrip(_HarmoniaBase):
    def test_play_reaches_the_device_at_the_right_rate(self):
        # D3: the caller's sample rate must reach sd.play() unchanged — this is
        # the check that catches "every briefing plays at half speed."
        pcm = np.zeros(8, dtype=np.float32)
        harmonia.play(pcm, 48000, tag="orpheus")
        self.assertTrue(_wait_until(lambda: self._sd_stub.play.called))
        args, _ = self._sd_stub.play.call_args
        self.assertIs(args[0], pcm)
        self.assertEqual(args[1], 48000)

    def test_is_playing_true_immediately_then_false_once_drained(self):
        self.assertFalse(harmonia.is_playing())
        harmonia.play(np.zeros(4, dtype=np.float32), 24000)
        self.assertTrue(harmonia.is_playing())    # true synchronously — no race
        self.assertTrue(_wait_until(lambda: not harmonia.is_playing()))

    def test_plays_multiple_items_in_order(self):
        order = []
        self._sd_stub.play.side_effect = lambda pcm, sr: order.append(int(pcm[0]))
        for i in range(3):
            harmonia.play(np.array([i], dtype=np.float32), 24000)
        self.assertTrue(_wait_until(lambda: len(order) == 3))
        self.assertEqual(order, [0, 1, 2])


class TestStop(_HarmoniaBase):
    def test_stop_bumps_epoch_and_calls_sd_stop(self):
        before = harmonia._epoch
        harmonia.stop()
        self.assertEqual(harmonia._epoch, before + 1)
        self._sd_stub.stop.assert_called_once()

    def test_stop_is_a_noop_when_idle(self):
        harmonia.stop()   # must not raise

    def test_stop_eventually_clears_is_playing(self):
        self._sd_stub.play.side_effect = lambda pcm, sr: time.sleep(0.05)
        harmonia.play(np.zeros(2, dtype=np.float32), 24000)
        harmonia.play(np.zeros(2, dtype=np.float32), 24000)
        harmonia.stop()
        self.assertTrue(_wait_until(lambda: not harmonia.is_playing()))


class TestPlayDeviceDegradation(_HarmoniaBase):
    def test_device_error_is_swallowed(self):
        self._sd_stub.play.side_effect = OSError("no output device")
        harmonia.play(np.zeros(4, dtype=np.float32), 24000)   # must not raise
        self.assertTrue(_wait_until(lambda: not harmonia.is_playing()))

    def test_missing_sounddevice_is_swallowed(self):
        with mock.patch.dict(sys.modules, {"sounddevice": None}):
            # sys.modules[name] = None is the documented way to simulate an
            # uninstalled package: import raises ImportError.
            harmonia.play(np.zeros(4, dtype=np.float32), 24000)
            self.assertTrue(_wait_until(lambda: not harmonia.is_playing()))


class TestShutdown(_HarmoniaBase):
    def test_shutdown_stops_the_thread(self):
        harmonia.play(np.zeros(4, dtype=np.float32), 24000)
        self.assertTrue(_wait_until(lambda: not harmonia.is_playing()))
        harmonia.shutdown()
        self.assertIsNone(harmonia._thread)

    def test_shutdown_safe_when_never_started(self):
        harmonia.shutdown()   # must not raise
        harmonia.shutdown()   # idempotent


if __name__ == "__main__":
    unittest.main(verbosity=2)
