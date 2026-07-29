"""
test_harmonia.py — unit tests for harmonia.py (the output-device authority).

No real audio device: sounddevice is stubbed into sys.modules with a fake
sd.OutputStream whose start() spins a daemon that pulls audio through Harmonia's
callback exactly like PortAudio would — so the buffer really drains and
is_playing() really flips. tools.morpheus.stop is mocked so no mpv process is
touched. harmonia.play() is async by design (there is no synchronous escape
hatch — that's the point of the module), so round-trip tests poll is_playing()
with a short timeout instead of asserting instantly.

    python -X utf8 -m unittest tests.test_harmonia
"""

import os
import sys
import threading
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


class _FakeStream:
    """Stand-in for sd.OutputStream. start() launches a daemon that repeatedly
    calls Harmonia's callback (like PortAudio pulling audio), draining the
    buffer; it records the samplerate/latency it was opened with and every
    sample the callback produced (for order assertions)."""

    instances: list = []

    def __init__(self, samplerate, channels, dtype, latency, callback):
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self.latency = latency
        self._callback = callback
        self._stop = threading.Event()
        self._thread = None
        self._emit_lock = threading.Lock()
        self._emitted: list = []
        _FakeStream.instances.append(self)

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        block = 32
        while not self._stop.is_set():
            out = np.zeros((block, self.channels), dtype=np.float32)
            self._callback(out, block, None, None)
            with self._emit_lock:
                self._emitted.extend(out[:, 0].tolist())
            time.sleep(0.002)

    def snapshot(self) -> list:
        with self._emit_lock:
            return list(self._emitted)

    def abort(self):
        self._stop.set()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


class _HarmoniaBase(unittest.TestCase):
    """Fully tear down the stream and reset the buffer around every test, so a
    leftover callback thread or stale sample count can't leak into the next."""

    def setUp(self):
        harmonia.shutdown()          # close any stream a prior test left open
        harmonia._flush_buffer()
        _FakeStream.instances = []

        self._sd_stub = types.ModuleType("sounddevice")
        self._sd_stub.OutputStream = _FakeStream
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

    def test_play_still_plays_if_morpheus_stop_raises(self):
        self.mock_morpheus_stop.side_effect = RuntimeError("pipe gone")
        harmonia.play(np.ones(20000, dtype=np.float32), 24000)   # must not raise
        self.assertTrue(harmonia.is_playing())                   # audio was buffered
        self.assertTrue(_wait_until(lambda: not harmonia.is_playing()))   # and drained


class TestPlayRoundTrip(_HarmoniaBase):
    def test_play_opens_stream_at_the_callers_rate(self):
        # The caller's sample rate must reach the device unchanged — the check
        # that catches "every briefing plays at half speed." _ensure_stream runs
        # synchronously inside play(), so the stream exists on return.
        harmonia.play(np.zeros(8, dtype=np.float32), 48000, tag="orpheus")
        self.assertEqual(harmonia._stream.samplerate, 48000)
        self.assertEqual(harmonia._stream.channels, 1)

    def test_stream_requests_a_moderate_buffer_and_our_format(self):
        # A non-default latency so the memcpy-only callback survives CPU
        # starvation (LLM + kokoro) without underrunning; mono float32 to match
        # the kokoro/Echo PCM the callers hand over.
        harmonia.play(np.zeros(4, dtype=np.float32), 24000)
        self.assertEqual(harmonia._stream.latency, harmonia._LATENCY)
        self.assertEqual(harmonia._stream.dtype, "float32")

    def test_is_playing_true_immediately_then_false_once_drained(self):
        self.assertFalse(harmonia.is_playing())
        harmonia.play(np.ones(20000, dtype=np.float32), 24000)
        self.assertTrue(harmonia.is_playing())    # true synchronously — no race
        self.assertTrue(_wait_until(lambda: not harmonia.is_playing()))

    def test_plays_orpheus_2d_frames_channel_shape(self):
        # Orpheus decodes to a 2D (frames, 1) array — pcm.reshape(-1, _CHANNELS),
        # shaped for the retired sd.play(); kokoro (Calliope) hands over 1D. The
        # callback assumes 1D mono, so play() must flatten. Without that, _fill
        # raises "could not broadcast (N,1) into (N,)", the callback guard emits
        # silence, and _buffered_samples never drains — the briefing is inaudible
        # AND is_playing() sticks true forever. This is the exact regression the
        # stream rewrite introduced (see CONVENTIONS §12). Feed the real Orpheus
        # shape with a distinctive non-zero value and assert those samples reach
        # the device — both that it drains and that it isn't just silence.
        pcm_2d = np.full((5000, 1), 7.0, dtype=np.float32)   # Orpheus's exact shape
        harmonia.play(pcm_2d, 24000, tag="orpheus")
        self.assertTrue(harmonia.is_playing())                            # buffered
        self.assertTrue(_wait_until(lambda: not harmonia.is_playing()))   # and drains
        self.assertIn(7.0, harmonia._stream.snapshot())   # real samples, not silence

    def test_plays_multiple_items_in_order(self):
        for v in (11.0, 22.0, 33.0):
            harmonia.play(np.full(100, v, dtype=np.float32), 24000)
        self.assertTrue(_wait_until(lambda: not harmonia.is_playing()))
        seen = [s for s in harmonia._stream.snapshot() if s != 0.0]
        firsts: list = []                          # collapse each contiguous run
        for s in seen:
            if not firsts or firsts[-1] != s:
                firsts.append(s)
        self.assertEqual(firsts, [11.0, 22.0, 33.0])


class TestStop(_HarmoniaBase):
    def test_stop_flushes_the_buffer_without_stopping_the_stream(self):
        # The whole point of the rewrite: barge-in is a buffer flush, NOT a
        # cross-thread sd.stop(). is_playing() falls false synchronously, but the
        # owned stream keeps running (it just emits silence) so no thread races it.
        harmonia.play(np.ones(48000, dtype=np.float32), 24000)
        self.assertTrue(harmonia.is_playing())
        harmonia.stop()
        self.assertFalse(harmonia.is_playing())    # buffer cleared synchronously
        self.assertIsNotNone(harmonia._stream)     # stream NOT torn down

    def test_stop_is_a_noop_when_idle(self):
        harmonia.stop()   # must not raise

    def test_stop_eventually_clears_is_playing(self):
        harmonia.play(np.ones(24000, dtype=np.float32), 24000)
        harmonia.play(np.ones(24000, dtype=np.float32), 24000)
        harmonia.stop()
        self.assertTrue(_wait_until(lambda: not harmonia.is_playing()))


class TestPlayDeviceDegradation(_HarmoniaBase):
    def test_device_error_is_swallowed(self):
        # Opening the stream fails (no/busy device) → play drops the audio quietly.
        self._sd_stub.OutputStream = mock.Mock(side_effect=OSError("no output device"))
        harmonia.play(np.zeros(4, dtype=np.float32), 24000)   # must not raise
        self.assertFalse(harmonia.is_playing())
        self.assertIsNone(harmonia._stream)

    def test_missing_sounddevice_is_swallowed(self):
        with mock.patch.dict(sys.modules, {"sounddevice": None}):
            # sys.modules[name] = None is the documented way to simulate an
            # uninstalled package: import raises ImportError.
            harmonia.play(np.zeros(4, dtype=np.float32), 24000)
            self.assertFalse(harmonia.is_playing())


class TestShutdown(_HarmoniaBase):
    def test_shutdown_closes_the_stream(self):
        harmonia.play(np.zeros(4, dtype=np.float32), 24000)
        self.assertIsNotNone(harmonia._stream)
        harmonia.shutdown()
        self.assertIsNone(harmonia._stream)

    def test_shutdown_safe_when_never_started(self):
        harmonia.shutdown()   # must not raise
        harmonia.shutdown()   # idempotent


if __name__ == "__main__":
    unittest.main(verbosity=2)
