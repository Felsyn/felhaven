"""
test_calliope.py — unit tests for calliope.py (the narrator).

Calliope lives INSIDE the toolbox (metis_toolbox/calliope.py, beside pythia.py),
so the path insert climbs two directories (tests -> metis_toolbox), the same
shape every other suite here uses. Run with the house runner:
    cd metis_toolbox
    python -X utf8 -m unittest discover -s tests -p "test_*.py"

Hermetic: no audio hardware, no model files, no network, and the background
synth/release workers are never started (enqueue tests patch _ensure_workers, and
the coalescing logic is exercised via the pure _coalesce() helper). The
kokoro-onnx model is replaced by a fake whose .create() returns canned PCM.
Calliope no longer touches sounddevice at all — every test that used to assert
against playback now mocks harmonia.play() / harmonia.stop() and asserts
Calliope *calls* Harmonia (device-error degradation itself is covered by
tests.test_harmonia, since sounddevice ownership moved there).
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import calliope
import harmonia


def _fake_model(pcm: np.ndarray) -> mock.Mock:
    """A stand-in for kokoro_onnx.Kokoro: .create() yields (pcm, 24000)."""
    m = mock.Mock()
    m.create.return_value = (pcm, 24000)
    return m


class _CalliopeBase(unittest.TestCase):
    """Reset the lazy singleton, flag, epoch, and queues around every test so a
    cached model, a flipped flag, a bumped epoch, or a stale queue item can't
    leak between cases. The workers are never started, so nothing consumes the
    queues behind our back."""

    def setUp(self):
        self._saved = (calliope._model, calliope._model_tried,
                       calliope._auto_speak, calliope._epoch,
                       calliope._FIRST_CHUNK_MAX_CHARS, calliope._CHUNK_MAX_CHARS)
        calliope._model = None
        calliope._model_tried = False
        calliope._real_audio_queued = False
        calliope._answer_produced = 0
        calliope._turn_ended = False
        calliope._filler_active = False
        self._drain()

    def tearDown(self):
        (calliope._model, calliope._model_tried, calliope._auto_speak,
         calliope._epoch, calliope._FIRST_CHUNK_MAX_CHARS,
         calliope._CHUNK_MAX_CHARS) = self._saved
        self._drain()

    @staticmethod
    def _drain():
        for q in (calliope._text_queue, calliope._release_queue):
            while not q.empty():
                q.get_nowait()


class TestConfigDefaults(_CalliopeBase):
    """_load_config() promises a missing/garbled config degrades to a WORKING
    narrator, but nothing exercises that path on a healthy install — so the
    defaults can rot silently (they once still named the retired int8 model,
    which exists on no current install: the fallback synthesized nothing).
    Guard it in lockstep against the shipped config rather than by restating
    the values here, which would rot the same way."""

    def _shipped(self) -> dict:
        with open(calliope._CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)

    def test_defaults_agree_with_shipped_config(self):
        shipped = self._shipped()
        for key, default in calliope._DEFAULTS.items():
            with self.subTest(key=key):
                self.assertIn(key, shipped, f"{key} defaulted but not shipped")
                self.assertEqual(default, shipped[key])

    def test_defaults_cover_every_tunable_config_key(self):
        # 'fillers' is prose, not a tunable: absent, generate_fillers() renders
        # none and the narrator still speaks, so it is deliberately undefaulted.
        missing = set(self._shipped()) - set(calliope._DEFAULTS) - {"fillers"}
        self.assertEqual(missing, set())

    def test_config_absent_still_yields_a_loadable_model_path(self):
        # The fail-soft path: no config at all must still name the model that
        # ships on disk, not a retired one.
        with mock.patch.object(calliope, "_CONFIG_FILE", Path("no-such-config.json")):
            cfg = calliope._load_config()
        self.assertEqual(cfg["model_path"], self._shipped()["model_path"])


class TestSplitSentences(_CalliopeBase):
    def test_splits_on_sentence_ends(self):
        self.assertEqual(
            calliope._split_sentences("Hello there. How are you? Fine!"),
            ["Hello there.", "How are you?", "Fine!"],
        )

    def test_does_not_cut_decimals_or_versions(self):
        self.assertEqual(
            calliope._split_sentences("It is 3.5 miles on v1.0 today."),
            ["It is 3.5 miles on v1.0 today."],
        )

    def test_blank_is_empty(self):
        self.assertEqual(calliope._split_sentences("   "), [])
        self.assertEqual(calliope._split_sentences(""), [])


class TestCleanForSpeech(_CalliopeBase):
    def test_strips_markdown_symbols(self):
        out = calliope._clean_for_speech("**Bold** and `code` and # head")
        for ch in ("*", "`", "#"):
            self.assertNotIn(ch, out)
        self.assertIn("Bold", out)
        self.assertIn("code", out)


class TestTakeChunk(_CalliopeBase):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(calliope.take_chunk("Short one.", 90), ("Short one.", ""))

    def test_splits_long_at_clause_boundary(self):
        text = "Based on the search results, the main objective is to find the informant."
        chunk, rest = calliope.take_chunk(text, 40)
        self.assertEqual(chunk, "Based on the search results,")
        self.assertTrue(rest.startswith("the main objective"))

    def test_prefers_sentence_end_over_clause(self):
        text = "First done. Then a much longer continuation follows here."
        chunk, rest = calliope.take_chunk(text, 40)
        self.assertEqual(chunk, "First done.")
        self.assertTrue(rest.startswith("Then a much"))

    def test_falls_back_to_space(self):
        text = "one two three four five six seven eight nine ten"
        chunk, rest = calliope.take_chunk(text, 12)
        self.assertLessEqual(len(chunk), 13)          # broke at a space near the cap
        self.assertTrue(chunk and rest)
        self.assertEqual(chunk, chunk.strip())


class TestSynthesize(_CalliopeBase):
    def test_returns_model_pcm(self):
        pcm = np.zeros(2400, dtype=np.float32)
        with mock.patch("calliope._load_model", return_value=_fake_model(pcm)) as lm:
            out = calliope.synthesize("hello there")
        self.assertIs(out, pcm)
        lm.assert_called_once()

    def test_passes_cleaned_text_and_config(self):
        pcm = np.zeros(10, dtype=np.float32)
        fake = _fake_model(pcm)
        with mock.patch("calliope._load_model", return_value=fake):
            calliope.synthesize("**read** this")
        args, kwargs = fake.create.call_args
        self.assertEqual(args[0], "read this")          # markdown stripped before synth
        self.assertEqual(kwargs["voice"], calliope._CONFIG["voice"])
        self.assertEqual(kwargs["speed"], float(calliope._CONFIG["speed"]))
        self.assertEqual(kwargs["lang"], calliope._CONFIG["lang"])

    def test_empty_text_is_none_and_loads_nothing(self):
        with mock.patch("calliope._load_model") as lm:
            self.assertIsNone(calliope.synthesize(""))
            self.assertIsNone(calliope.synthesize("   "))
            self.assertIsNone(calliope.synthesize("***"))   # cleans to empty
        lm.assert_not_called()

    def test_model_unavailable_degrades_to_none(self):
        with mock.patch("calliope._load_model", return_value=None):
            self.assertIsNone(calliope.synthesize("anything"))

    def test_synthesis_error_degrades_to_none(self):
        broken = mock.Mock()
        broken.create.side_effect = RuntimeError("onnx blew up")
        with mock.patch("calliope._load_model", return_value=broken):
            self.assertIsNone(calliope.synthesize("boom"))   # must not raise


class TestSpeakEnqueues(_CalliopeBase):
    def test_speak_enqueues_each_sentence(self):
        with mock.patch("calliope._ensure_workers"):
            calliope.speak("First one. Second one.")
        queued = []
        while not calliope._text_queue.empty():
            queued.append(calliope._text_queue.get_nowait())
        self.assertEqual(queued, ["First one.", "Second one."])

    def test_speak_blank_enqueues_nothing(self):
        with mock.patch("calliope._ensure_workers") as ew:
            calliope.speak("   ")
        self.assertTrue(calliope._text_queue.empty())
        ew.assert_not_called()


class TestStop(_CalliopeBase):
    def test_stop_drains_and_bumps_epoch(self):
        calliope._text_queue.put("pending sentence")
        calliope._release_queue.put((0, np.zeros(2, dtype=np.float32)))
        before = calliope._epoch
        with mock.patch.object(harmonia, "stop") as hstop:
            calliope.stop()                              # must not raise
        self.assertTrue(calliope._text_queue.empty())
        self.assertTrue(calliope._release_queue.empty())
        self.assertEqual(calliope._epoch, before + 1)    # in-flight synth discarded
        hstop.assert_called_once()                       # D4: Harmonia actually interrupts


class TestPrewarm(_CalliopeBase):
    def test_prewarm_loads_model_and_generates_fillers(self):
        with mock.patch("calliope._load_model") as lm, \
                mock.patch("calliope.generate_fillers") as gf:
            t = calliope.prewarm()
            t.join(timeout=2)
        lm.assert_called_once()
        gf.assert_called_once()


class TestFillers(_CalliopeBase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_dir = calliope._FILLER_DIR
        calliope._FILLER_DIR = Path(self._tmp.name)

    def tearDown(self):
        calliope._FILLER_DIR = self._orig_dir
        self._tmp.cleanup()
        super().tearDown()

    def test_filler_path_is_deterministic_and_scoped(self):
        p1 = calliope._filler_path("Hello.")
        p2 = calliope._filler_path("Hello.")
        p3 = calliope._filler_path("Different.")
        self.assertEqual(p1, p2)
        self.assertNotEqual(p1, p3)
        self.assertEqual(p1.parent, calliope._FILLER_DIR)

    def test_wav_round_trip(self):
        pcm = (np.sin(np.linspace(0, 20, 2400)) * 0.5).astype(np.float32)
        path = Path(self._tmp.name) / "t.wav"
        calliope.save_wav(path, pcm)
        back = calliope.load_wav(path)
        self.assertEqual(back.shape, pcm.shape)
        self.assertLess(float(np.max(np.abs(back - pcm))), 1e-3)   # int16 quantization

    def test_generate_fillers_renders_missing(self):
        pcm = np.zeros(1200, dtype=np.float32)
        with mock.patch("calliope.synthesize", return_value=pcm) as synth:
            calliope.generate_fillers()
        phrases = calliope._CONFIG["fillers"]
        self.assertEqual(synth.call_count, len(phrases))          # one render each
        for phrase in phrases:
            self.assertTrue(calliope._filler_path(phrase).is_file())

    def test_generate_fillers_skips_existing(self):
        pcm = np.zeros(1200, dtype=np.float32)
        phrases = calliope._CONFIG["fillers"]
        calliope._FILLER_DIR.mkdir(parents=True, exist_ok=True)
        calliope.save_wav(calliope._filler_path(phrases[0]), pcm)   # pre-cache one
        with mock.patch("calliope.synthesize", return_value=pcm) as synth:
            calliope.generate_fillers()
        self.assertEqual(synth.call_count, len(phrases) - 1)      # the cached one skipped

    def test_speak_filler_plays_cached_audio_and_arms_gate(self):
        pcm = np.zeros(600, dtype=np.float32)
        phrase = calliope._CONFIG["fillers"][0]
        calliope._FILLER_DIR.mkdir(parents=True, exist_ok=True)
        calliope.save_wav(calliope._filler_path(phrase), pcm)
        with mock.patch("calliope._ensure_workers"), \
                mock.patch.object(harmonia, "play") as hplay:
            calliope.speak_filler()
        hplay.assert_called_once()
        args, kwargs = hplay.call_args
        self.assertEqual(len(args[0]), 600)                # the cached pcm
        self.assertEqual(args[1], calliope.SAMPLE_RATE)     # explicit rate (D3)
        self.assertEqual(kwargs.get("tag"), "filler")
        self.assertTrue(calliope._filler_active)           # prebuffer gate armed

    def test_speak_filler_noop_when_none_cached(self):
        with mock.patch("calliope._ensure_workers") as ew, \
                mock.patch.object(harmonia, "play") as hplay:
            calliope.speak_filler()                              # empty dir
        hplay.assert_not_called()
        ew.assert_not_called()

    def test_speak_filler_skips_when_real_audio_already_queued(self):
        # A fast answer beat the delayed filler — it must not play over real speech.
        pcm = np.zeros(600, dtype=np.float32)
        phrase = calliope._CONFIG["fillers"][0]
        calliope._FILLER_DIR.mkdir(parents=True, exist_ok=True)
        calliope.save_wav(calliope._filler_path(phrase), pcm)
        calliope._real_audio_queued = True
        with mock.patch("calliope._ensure_workers"), \
                mock.patch.object(harmonia, "play") as hplay:
            calliope.speak_filler()
        hplay.assert_not_called()

    def test_filler_delay_from_config(self):
        self.assertEqual(calliope.filler_delay_ms(),
                         int(calliope._CONFIG.get("filler_delay_ms", 1000)))


class TestPrebuffer(_CalliopeBase):
    def test_no_wait_without_filler(self):
        # The manual speak button (no filler) must never block on the prebuffer.
        calliope._filler_active = False
        calliope._answer_produced = 0
        start = __import__("time").perf_counter()
        calliope._await_prebuffer(calliope._epoch)       # returns at once
        self.assertLess(__import__("time").perf_counter() - start, 0.1)

    def test_end_turn_releases_gate(self):
        calliope._turn_ended = False
        calliope.end_turn()
        self.assertTrue(calliope._turn_ended)

    def test_wait_releases_once_enough_produced(self):
        # With a filler active, the gate opens as soon as prebuffer_chunks land.
        import threading as _t
        import time as _time
        calliope._filler_active = True
        calliope._answer_produced = 0
        calliope._turn_ended = False
        done = _t.Event()

        def waiter():
            calliope._await_prebuffer(calliope._epoch)
            done.set()

        _t.Thread(target=waiter, daemon=True).start()
        _time.sleep(0.1)
        self.assertFalse(done.is_set())                  # still waiting for the lead
        with calliope._cond:                             # simulate the synth worker
            calliope._answer_produced = calliope._PREBUFFER_CHUNKS
            calliope._cond.notify_all()
        self.assertTrue(done.wait(timeout=2))            # gate opened


class TestAutoSpeak(_CalliopeBase):
    def test_default_from_config(self):
        self.assertEqual(calliope.auto_speak_enabled(),
                         bool(calliope._CONFIG.get("auto_speak", False)))

    def test_set_and_toggle(self):
        calliope.set_auto_speak(False)
        self.assertFalse(calliope.auto_speak_enabled())
        self.assertTrue(calliope.toggle_auto_speak())
        self.assertTrue(calliope.auto_speak_enabled())
        self.assertFalse(calliope.toggle_auto_speak())
        self.assertFalse(calliope.auto_speak_enabled())


if __name__ == "__main__":
    unittest.main(verbosity=2)
