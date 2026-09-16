"""
test_harmonia.py — unit tests for harmonia.py (the output-device authority).

No real audio device: sounddevice is stubbed into sys.modules with a fake
sd.OutputStream whose start() spins a daemon that pulls audio through Harmonia's
callback exactly like PortAudio would — so the buffer really drains and
is_playing() really flips. tools.morpheus.duck/unduck are mocked so no mpv
process is touched — morpheus.stop is deliberately left REAL, so that
test_play_never_stops_morpheus can catch a regression to the old kill-the-music
yield rather than silently swallowing it. harmonia.play() is async by design (there is no synchronous escape
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
        # The two flags sounddevice exposes and Harmonia now reads to tell a
        # live stream from one PortAudio tore down under it.
        self.active = False
        self.closed = False
        _FakeStream.instances.append(self)

    def start(self):
        self.active = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        # Pull a realistic PortAudio-sized block, NOT a tiny one. The drain rate
        # is block / sleep-interval samples/sec, and it must clear the largest
        # test buffer (20 000 samples) within _wait_until's 2 s window. On Python
        # <=3.10 (Windows) time.sleep()'s floor is ~15 ms, not the ~0.1 ms of
        # 3.11+, so a 32-sample block took ~625 loops (~9 s) and the drain tests
        # timed out on 3.10 only. 1024 clears it in ~20 loops even at the coarse
        # granularity — the size real PortAudio would request anyway.
        block = 1024
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
        """Also what PortAudio does to us on a device change."""
        self.active = False
        self._stop.set()

    def close(self):
        self.active = False
        self.closed = True
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


class _ManualStream(_FakeStream):
    """A _FakeStream whose daemon never starts — the test pulls blocks itself,
    on the main thread.

    Order assertions need a deterministic pull. With the daemon running, the
    only completion signal a test has is is_playing(), and that falls false the
    instant the callback returns — strictly BEFORE _run records what the
    callback produced. The main thread can win that window and snapshot a
    recording that is still empty, which is how test_plays_multiple_items_in_
    order failed on CI's slower 3.10 leg (run 31112638613) while asserting
    nothing about ordering at all. Pulling by hand closes the window; the
    threaded drain keeps its coverage in the is_playing()/drain tests."""

    def start(self):
        self.active = True          # live, just not self-draining

    def pull(self, frames: int = 1024) -> None:
        """Run one callback block on the calling thread, and record it."""
        out = np.zeros((frames, self.channels), dtype=np.float32)
        self._callback(out, frames, None, None)
        with self._emit_lock:
            self._emitted.extend(out[:, 0].tolist())


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

        # duck/unduck only. morpheus.stop stays real on purpose — see the
        # module docstring and test_play_never_stops_morpheus.
        self._duck_patcher = mock.patch.object(harmonia.morpheus, "duck")
        self.mock_duck = self._duck_patcher.start()
        self._unduck_patcher = mock.patch.object(harmonia.morpheus, "unduck")
        self.mock_unduck = self._unduck_patcher.start()

        harmonia._yielded = False        # no restore owed from a prior test
        harmonia._silent_since = None

    def tearDown(self):
        harmonia.shutdown()
        harmonia._yielded = False
        harmonia._silent_since = None
        self._duck_patcher.stop()
        self._unduck_patcher.stop()
        self._modules_patcher.stop()


class TestYieldsMorpheus(_HarmoniaBase):
    def test_play_ducks_morpheus_first(self):
        harmonia.play(np.zeros(4, dtype=np.float32), 24000, tag="t")
        self.mock_duck.assert_called_once()

    def test_play_never_stops_morpheus(self):
        """The bug, in one assertion.

        harmonia.play() used to call morpheus.stop(), which killed a track
        whose URL yt-dlp was still resolving — so asking Pythia to play a song
        stopped the song it had just started, silently. Narration must lower
        the volume and never stop playback."""
        with mock.patch.object(harmonia.morpheus, "stop") as never:
            harmonia.play(np.zeros(4, dtype=np.float32), 24000)
            never.assert_not_called()

    def test_play_still_plays_if_morpheus_duck_raises(self):
        self.mock_duck.side_effect = RuntimeError("pipe gone")
        harmonia.play(np.ones(20000, dtype=np.float32), 24000)   # must not raise
        self.assertTrue(harmonia.is_playing())                   # audio was buffered
        self.assertTrue(_wait_until(lambda: not harmonia.is_playing()))   # and drained

    def test_play_arms_the_restore_even_with_no_output_device(self):
        """_arm_yield() runs BEFORE play()'s no-device early return. A duck
        taken on a box whose stream will not open must still be handed back,
        or the music stays quiet forever."""
        self._sd_stub.OutputStream = mock.Mock(side_effect=RuntimeError("no device"))
        harmonia.play(np.zeros(4, dtype=np.float32), 24000)
        self.assertFalse(harmonia.is_playing())      # nothing buffered
        self.assertTrue(harmonia._yielded)           # but a restore is owed


class TestYieldRestore(_HarmoniaBase):
    """tick_yield() — the main-thread reconcile that gives the volume back."""

    def _age_the_silence(self):
        """Pretend the buffer went quiet longer ago than _UNDUCK_HOLD."""
        harmonia._silent_since -= harmonia._UNDUCK_HOLD + 0.05

    def test_tick_yield_is_free_when_nothing_was_ever_played(self):
        harmonia.tick_yield()
        self.mock_unduck.assert_not_called()

    def test_tick_yield_does_not_restore_while_audio_is_buffered(self):
        self._sd_stub.OutputStream = _ManualStream
        harmonia.play(np.ones(20000, dtype=np.float32), 24000)
        for _ in range(5):
            harmonia.tick_yield()
        self.mock_unduck.assert_not_called()

    def test_tick_yield_waits_out_the_hold_before_restoring(self):
        harmonia.play(np.zeros(4, dtype=np.float32), 24000)
        self.assertTrue(_wait_until(lambda: not harmonia.is_playing()))
        harmonia.tick_yield()                    # stamps _silent_since
        self.mock_unduck.assert_not_called()     # ...but the hold has not run
        self._age_the_silence()
        harmonia.tick_yield()
        self.mock_unduck.assert_called_once()

    def test_a_new_chunk_inside_the_hold_cancels_the_restore(self):
        """Why _UNDUCK_HOLD exists. Calliope's buffer legitimately empties
        mid-answer (between one synthesized chunk and the next), so a restore
        counting down must be cancelled by the next chunk — otherwise the music
        pumps up and down inside one sentence."""
        harmonia.play(np.zeros(4, dtype=np.float32), 24000)
        self.assertTrue(_wait_until(lambda: not harmonia.is_playing()))
        harmonia.tick_yield()                    # starts the countdown
        self._age_the_silence()
        harmonia.play(np.zeros(4, dtype=np.float32), 24000)   # the next chunk
        harmonia.tick_yield()
        self.mock_unduck.assert_not_called()
        self.assertIsNone(harmonia._silent_since)

    def test_restore_happens_once_not_every_tick(self):
        harmonia.play(np.zeros(4, dtype=np.float32), 24000)
        self.assertTrue(_wait_until(lambda: not harmonia.is_playing()))
        harmonia.tick_yield()
        self._age_the_silence()
        for _ in range(5):
            harmonia.tick_yield()
        self.mock_unduck.assert_called_once()

    def test_barge_in_then_silence_restores(self):
        harmonia.play(np.ones(200000, dtype=np.float32), 24000)
        harmonia.stop()                          # Vox End mid-narration
        self.assertFalse(harmonia.is_playing())
        harmonia.tick_yield()
        self._age_the_silence()
        harmonia.tick_yield()
        self.mock_unduck.assert_called_once()

    def test_shutdown_restores_morpheus(self):
        """The safety net: teardown hands the volume back while mpv is still
        alive to hear it (felhaven calls harmonia.shutdown() first)."""
        harmonia.play(np.zeros(4, dtype=np.float32), 24000)
        harmonia.shutdown()
        self.mock_unduck.assert_called_once()

    def test_shutdown_does_not_unduck_when_nothing_was_owed(self):
        harmonia.shutdown()
        self.mock_unduck.assert_not_called()

    def test_tick_yield_never_raises_when_unduck_fails(self):
        self.mock_unduck.side_effect = RuntimeError("pipe gone")
        harmonia.play(np.zeros(4, dtype=np.float32), 24000)
        self.assertTrue(_wait_until(lambda: not harmonia.is_playing()))
        harmonia.tick_yield()
        self._age_the_silence()
        harmonia.tick_yield()                    # must not raise
        self.mock_unduck.assert_called_once()
        self.assertFalse(harmonia._yielded)       # and the debt is cleared


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
        # Wait on the recording itself, not on is_playing(): the callback drains
        # the buffer before _run records the block it produced, so is_playing()
        # going false does NOT mean the samples are in the snapshot yet.
        self.assertTrue(_wait_until(lambda: 7.0 in harmonia._stream.snapshot()),
                        "real samples never reached the device (only silence)")

    def test_plays_multiple_items_in_order(self):
        # Pull the block by hand on the main thread (see _ManualStream). What
        # this test asserts is the ORDER samples leave the buffer, which needs a
        # deterministic pull — not a race between is_playing() and the recorder.
        self._sd_stub.OutputStream = _ManualStream
        for v in (11.0, 22.0, 33.0):
            harmonia.play(np.full(100, v, dtype=np.float32), 24000)
        harmonia._stream.pull()             # one 1024 block takes all 300 samples
        self.assertFalse(harmonia.is_playing())
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


class TestStreamOutlivesTheDevice(_HarmoniaBase):
    """PortAudio can stop the stream underneath us — a device change or
    removal, a driver reset, another app taking the device in exclusive mode,
    a sleep/resume. Harmonia keeps ONE stream for the app's life, so before
    2026-09-09 it handed the dead one back to every later play() and appended
    to a buffer nothing drained: narration went silent for the rest of the
    session, is_playing() stuck true (Vox End stayed lit), and nothing was
    logged. Felhaven gets left running for days, so this is not exotic."""

    def _first_stream(self):
        harmonia.play(np.zeros(4, dtype=np.float32), 24000)
        self.assertTrue(_wait_until(lambda: not harmonia.is_playing()))
        return harmonia._stream

    def test_a_live_stream_is_reused(self):
        """The reopen must be rare. Churning a new stream per chunk would be
        the shared-stream race this module exists to avoid."""
        first = self._first_stream()
        harmonia.play(np.zeros(4, dtype=np.float32), 24000)
        self.assertIs(harmonia._stream, first)
        self.assertEqual(len(_FakeStream.instances), 1)

    def test_a_dead_stream_is_replaced(self):
        first = self._first_stream()
        first.abort()                        # the device went away
        harmonia.play(np.zeros(4, dtype=np.float32), 24000)
        self.assertIsNot(harmonia._stream, first)
        self.assertEqual(len(_FakeStream.instances), 2)

    def test_audio_plays_again_on_the_replacement(self):
        """The point of the whole thing: narration comes back."""
        self._first_stream().abort()
        harmonia.play(np.ones(20000, dtype=np.float32), 24000)
        self.assertTrue(_wait_until(lambda: not harmonia.is_playing()),
                        "narration never drained after the device came back")

    def test_the_audio_in_flight_when_it_died_is_dropped(self):
        """What was mid-answer when the device went is stale by the time one
        comes back. Playing it on the replacement would talk over whatever the
        user asked next. (_ManualStream so nothing drains behind the test.)"""
        self._sd_stub.OutputStream = _ManualStream
        harmonia.play(np.ones(20000, dtype=np.float32), 24000)
        self.assertEqual(harmonia._buffered_samples, 20000)
        harmonia._stream.abort()             # died mid-answer, audio still queued
        harmonia.play(np.ones(8, dtype=np.float32), 24000)    # the recovering call
        self.assertEqual(harmonia._buffered_samples, 8)

    def test_the_replacement_is_logged(self):
        """It was silent before, which is why it took two bug reports."""
        self._first_stream().abort()
        with self.assertLogs("METIS.harmonia", level="WARNING") as caught:
            harmonia.play(np.zeros(4, dtype=np.float32), 24000)
        self.assertTrue(any("went dead" in line for line in caught.output))

    def test_a_backend_without_liveness_flags_is_treated_as_live(self):
        """Guessing "dead" would reopen the stream on every single call."""
        class _Opaque:
            def __getattr__(self, name):
                raise AttributeError(name)
        self.assertTrue(harmonia._stream_is_live(_Opaque()))

    def test_a_closed_stream_is_dead_even_if_active_lies(self):
        class _Closed:
            closed = True
            active = True
        self.assertFalse(harmonia._stream_is_live(_Closed()))


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
