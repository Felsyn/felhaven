"""
test_pythia.py — unit tests for pythia.py (the LLM oracle / brain).

Hermetic: requests.post is mocked, so no Ollama server and no network are
touched. Pythia STREAMS (stream=True) — /api/chat returns newline-delimited JSON
objects — so the fake response is a context manager exposing iter_lines(). The
tool-call test also stubs one dispatch entry so no real tool runs. Run from the
package root:
    python -X utf8 -m unittest tests.test_pythia

Covers: streaming plain answer (+ on_delta tokens), the request payload
(stream/keep_alive), a tool-call round-trip (dispatch + result fed back),
graceful network-failure text, and the TOOLS/_DISPATCH registry invariant.
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

import pythia


def _stream(*objs: dict) -> mock.Mock:
    """Fake a streaming requests Response: a context manager whose iter_lines()
    yields each `objs` dict as a JSON line (bytes), like Ollama's stream=True."""
    r = mock.Mock()
    r.raise_for_status.return_value = None
    r.iter_lines.return_value = [json.dumps(o).encode("utf-8") for o in objs]
    r.__enter__ = mock.Mock(return_value=r)
    r.__exit__ = mock.Mock(return_value=False)
    return r


def _final(content: str) -> dict:
    """A terminal stream chunk carrying `content` and done=true."""
    return {"message": {"role": "assistant", "content": content},
            "done": True, "done_reason": "stop"}


class TestPythiaRegistry(unittest.TestCase):
    def test_tools_and_dispatch_in_lockstep(self):
        # Derived from the same modules, so every advertised tool has a handler.
        names = [t["function"]["name"] for t in pythia.TOOLS]
        self.assertEqual(set(names), set(pythia._DISPATCH))
        self.assertEqual(len(names), len(pythia.TOOLS))

    def test_unknown_tool_is_soft_error(self):
        out = pythia._dispatch("no_such_tool", {})
        self.assertIn("error", out)


class TestPythiaAsk(unittest.TestCase):
    def test_plain_answer_streams(self):
        # Two content chunks then done — assembled into the final answer.
        resp = _stream(
            {"message": {"role": "assistant", "content": "It is "}, "done": False},
            _final("Tuesday."),
        )
        deltas = []
        with mock.patch("pythia.requests.post", return_value=resp) as post:
            out = pythia.ask("what day is it?", on_delta=deltas.append)
        self.assertEqual(out, "It is Tuesday.")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(deltas, ["It is ", "Tuesday."])   # streamed in order

    def test_request_payload_streams_and_pins_model(self):
        with mock.patch("pythia.requests.post", return_value=_stream(_final("hi"))) as post:
            pythia.ask("hi")
        payload = post.call_args.kwargs["json"]
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["keep_alive"], -1)
        self.assertTrue(post.call_args.kwargs["stream"])   # requests streaming on

    def test_tool_call_then_answer(self):
        # Round 1: model asks for get_weather (done, no content). Round 2: answer.
        responses = [
            _stream({"message": {"role": "assistant", "content": "",
                                 "tool_calls": [{"function": {"name": "get_weather",
                                                              "arguments": {}}}]},
                     "done": True}),
            _stream(_final("It's 70 and clear.")),
        ]
        stub = {"get_weather": lambda **_: {"temp_f": 70, "description": "clear"}}
        with mock.patch("pythia.requests.post", side_effect=responses) as post, \
                mock.patch.dict(pythia._DISPATCH, stub):
            out = pythia.ask("weather?")

        self.assertEqual(out, "It's 70 and clear.")
        self.assertEqual(post.call_count, 2)
        # The dispatched result must have been fed back as a 'tool' message.
        second_call_messages = post.call_args_list[1].kwargs["json"]["messages"]
        tool_msgs = [m for m in second_call_messages if m.get("role") == "tool"]
        self.assertTrue(tool_msgs, "tool result was not appended to the conversation")
        self.assertIn("70", tool_msgs[0]["content"])

    def test_connection_error_is_friendly(self):
        with mock.patch("pythia.requests.post",
                        side_effect=requests.ConnectionError()):
            out = pythia.ask("hi")
        self.assertIn("unreachable", out.lower())

    def test_timeout_is_friendly(self):
        with mock.patch("pythia.requests.post", side_effect=requests.Timeout()):
            out = pythia.ask("hi")
        self.assertIn("timed out", out.lower())

    def test_empty_content_does_not_return_blank(self):
        with mock.patch("pythia.requests.post", return_value=_stream(_final(""))):
            out = pythia.ask("hi")
        self.assertTrue(out)   # falls back to a placeholder, never an empty string


class TestPythiaPrewarm(unittest.TestCase):
    def test_warm_posts_empty_message_preload(self):
        resp = mock.Mock()
        resp.raise_for_status.return_value = None
        with mock.patch("pythia.requests.post", return_value=resp) as post:
            pythia._warm()
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["messages"], [])          # empty = load-only, no gen
        self.assertEqual(payload["keep_alive"], -1)

    def test_warm_swallows_when_ollama_down(self):
        with mock.patch("pythia.requests.post",
                        side_effect=requests.ConnectionError()):
            pythia._warm()                                 # must not raise

    def test_prewarm_runs_warm_off_thread(self):
        with mock.patch("pythia._warm") as warm:
            pythia.prewarm().join(timeout=2)
        warm.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
