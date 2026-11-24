"""
test_talos.py — Ollama's installed and loaded models.

Hermetic: tools.talos.hermes.get and .post are patched with canned replies
shaped like Ollama 0.40.13's (captured 2026-11-09 from the Obelisk, trimmed),
so no Ollama is needed.

    python -X utf8 -m unittest tests.test_talos
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import aether, hermes, talos

_GB = 1024 ** 3

# Captured from /api/tags, /api/ps and /api/show (license and modelfile cut).
_TAGS = {"models": [{"name": "gemma4:e2b", "model": "gemma4:e2b",
                     "size": 7162394941, "digest": "7fbdbf8f",
                     "details": {"parameter_size": "5.1B",
                                 "quantization_level": "Q4_K_M"}}]}
_PS = {"models": [{"name": "gemma4:e2b", "size": 1726742527,
                   "size_vram": 1726742527, "context_length": 16384,
                   "expires_at": "2319-02-19T19:33:15.888301907-05:00"}]}
_SHOW = {"capabilities": ["completion", "vision", "audio", "tools", "thinking"],
         "parameters": "temperature                    1\ntop_k                          64\n"
                       "top_p                          0.95"}


class _Reply:
    def __init__(self, body, status=200):
        self.status_code = status
        self.content = json.dumps(body).encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise hermes.HTTPStatus(self.status_code)


class _TalosCase(unittest.TestCase):
    def setUp(self):
        talos._clear_cache()
        self.addCleanup(talos._clear_cache)
        self.tags, self.ps, self.show = _TAGS, _PS, _SHOW
        self.get = self._patch("get", side_effect=self._get)
        self.post = self._patch("post", side_effect=self._post)

    def _patch(self, name, **kw):
        patch = mock.patch.object(talos.hermes, name, **kw)
        started = patch.start()
        self.addCleanup(patch.stop)
        return started

    def _get(self, url, **kw):
        return _Reply(self.tags if url.endswith("/api/tags") else self.ps)

    def _post(self, url, **kw):
        return _Reply(self.show)


class TestFetch(_TalosCase):

    def test_installed_carries_its_details(self):
        (model,) = talos.fetch()["installed"]
        self.assertEqual(model["name"], "gemma4:e2b")
        self.assertEqual(model["size_gb"], round(7162394941 / _GB, 2))
        self.assertEqual((model["params"], model["quant"]), ("5.1B", "Q4_K_M"))
        self.assertEqual(model["capabilities"], _SHOW["capabilities"])
        self.assertEqual(model["default_temperature"], 1.0)

    def test_loaded_says_how_much_is_on_the_gpu_and_that_it_stays(self):
        (model,) = talos.fetch()["loaded"]
        self.assertEqual(model["gpu_pct"], 100)
        self.assertEqual(model["vram_gb"], model["size_gb"])
        self.assertEqual(model["context"], 16384)
        self.assertTrue(model["kept_loaded"])

    def test_a_spill_to_the_cpu_shows_in_gpu_pct(self):
        self.ps = {"models": [dict(_PS["models"][0], size_vram=int(1726742527 * 0.62))]}
        self.assertEqual(talos.fetch()["loaded"][0]["gpu_pct"], 62)

    def test_an_expiry_soon_is_not_kept_loaded(self):
        self.ps = {"models": [dict(_PS["models"][0],
                                   expires_at="2026-11-09T20:05:00-05:00")]}
        self.assertFalse(talos.fetch()["loaded"][0]["kept_loaded"])

    def test_nothing_loaded_is_an_empty_list(self):
        self.ps = {"models": []}
        self.assertEqual(talos.fetch()["loaded"], [])

    def test_show_runs_once_per_digest(self):
        talos.fetch()
        talos.fetch()
        self.assertEqual(self.post.call_count, 1)
        self.tags = {"models": [dict(_TAGS["models"][0], digest="re-pulled")]}
        talos.fetch()
        self.assertEqual(self.post.call_count, 2)

    def test_a_failed_show_leaves_the_listing_and_is_tried_again(self):
        self.post.side_effect = hermes.Timeout("slow")
        with self.assertLogs("METIS.talos", "WARNING"):
            (model,) = talos.fetch()["installed"]
        self.assertIsNone(model["capabilities"])
        self.assertIsNone(model["default_temperature"])
        self.post.side_effect = self._post
        self.assertEqual(talos.fetch()["installed"][0]["capabilities"],
                         _SHOW["capabilities"])

    def test_an_unreachable_ollama_raises_for_kairos(self):
        self.get.side_effect = hermes.Offline("down")
        with self.assertRaises(hermes.Offline):
            talos.fetch()

    def test_a_nonsense_answer_raises(self):
        self.get.side_effect = lambda url, **kw: _Reply({"models": [{"no": "name"}]})
        with self.assertRaises(KeyError):
            talos.fetch()

    def test_the_reads_are_bounded_and_go_to_ollama(self):
        talos.fetch()
        for call in self.get.call_args_list:
            self.assertTrue(call.args[0].startswith(aether.ollama_base_url()))
            self.assertEqual(call.kwargs["cap"], talos._LIST_CAP)
            self.assertTrue(call.kwargs["allow_http"])
        show = self.post.call_args
        self.assertEqual(show.args[0], aether.ollama_base_url() + "/api/show")
        self.assertEqual(show.kwargs["json"], {"model": "gemma4:e2b"})
        self.assertEqual(show.kwargs["cap"], talos._SHOW_CAP)


class TestCapabilities(_TalosCase):

    def test_after_a_fetch_it_answers_from_the_cache(self):
        talos.fetch()
        self.post.reset_mock()
        self.assertIn("thinking", talos.capabilities("gemma4:e2b"))
        self.post.assert_not_called()

    def test_an_unseen_model_is_read_now(self):
        self.assertIn("tools", talos.capabilities("other:7b"))
        self.assertEqual(self.post.call_args.kwargs["json"], {"model": "other:7b"})

    def test_unknown_is_none_and_never_raises(self):
        self.post.side_effect = hermes.Offline("down")
        with self.assertLogs("METIS.talos", "WARNING"):
            self.assertIsNone(talos.capabilities("gemma4:e2b"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
