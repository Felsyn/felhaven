"""
test_pythia.py — unit tests for pythia.py (the LLM oracle / brain).

Hermetic: requests.post is mocked, so no Ollama server and no network are
touched. Pythia STREAMS (stream=True) — /api/chat returns newline-delimited JSON
objects — so the fake response is a context manager exposing iter_lines(). The
tool-call test also stubs one dispatch entry so no real tool runs. Run from the
package root:
    python -X utf8 -m unittest tests.test_pythia

Covers: streaming plain answer (+ on_delta tokens), the request payload
(stream/keep_alive), a tool-call round-trip (dispatch + result fed back), the
on_event stats payload's turn_messages (the real tool exchange a caller should
persist as history, excluding the system prompt and prior history, present
even on the friendly-degrade error paths), graceful network-failure text, the
TOOLS/_DISPATCH registry invariant, the §2 error-slug shape of the dispatcher's
own returns, that a caught exception's raw message never reaches the LLM
context or the transcript (only its class), and that a `thinking` trace is
dropped (never shown, never forwarded) while still being named in the log as
the reason a round came back empty.
"""

import json
import os
import sys
import threading
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


def _final(content: str, prompt_tokens: int = 5, output_tokens: int = 3) -> dict:
    """A terminal stream chunk carrying `content`, done=true, and token counts
    (Ollama's prompt_eval_count/eval_count/eval_duration)."""
    return {"message": {"role": "assistant", "content": content},
            "done": True, "done_reason": "stop",
            "prompt_eval_count": prompt_tokens, "eval_count": output_tokens,
            "eval_duration": 2_000_000}


def _final_thinking(thinking: str, content: str = "") -> dict:
    """A terminal chunk that carries a `thinking` trace — what Ollama sends when
    the model reasons (measured against gemma4:e2b with think=True: eval_count
    446, content empty, ~2000 chars of thinking). Pythia asks for think=False,
    so this is the shape to guard against, not today's normal traffic."""
    chunk = _final(content)
    chunk["message"]["thinking"] = thinking
    return chunk


class TestPythiaRegistry(unittest.TestCase):
    def test_tools_and_dispatch_in_lockstep(self):
        # Derived from the same modules, so every advertised tool has a handler.
        names = [t["function"]["name"] for t in pythia.TOOLS]
        self.assertEqual(set(names), set(pythia._DISPATCH))
        self.assertEqual(len(names), len(pythia.TOOLS))

    def test_unknown_tool_is_soft_error(self):
        out = pythia._dispatch("no_such_tool", {})
        self.assertIn("error", out)
        # A §2 slug, not a bare sentence — the model branches on the identifier.
        self.assertTrue(out["error"].startswith("unknown_tool:"), out["error"])

    def test_tool_failure_returns_slug_without_the_exception_message(self):
        # A tool bug must not crash the chat, and the caught exception's raw
        # text must not ride into the LLM context — only its class name does.
        # (The Midas lesson: an exception message can carry a path or a key.)
        secret = "token=SUPERSECRET123"

        def _boom(**_):
            raise RuntimeError(secret)

        with mock.patch.dict(pythia._DISPATCH, {"boom": _boom}):
            out = pythia._dispatch("boom", {})
        self.assertTrue(out["error"].startswith("tool_failed:"), out["error"])
        self.assertIn("RuntimeError", out["error"])       # class name is useful
        self.assertNotIn("SUPERSECRET123", out["error"])  # message is not leaked


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

    def test_turn_messages_carries_the_real_tool_exchange(self):
        # The stats event's turn_messages must be the caller's persistence
        # source: user -> assistant(tool_calls) -> tool(result) -> assistant
        # (final), in order — the exchange a flattened user/assistant history
        # used to throw away, which is what taught the small model to narrate
        # tool use instead of ever actually calling a tool (CONVENTIONS §12).
        responses = [
            _stream({"message": {"role": "assistant", "content": "",
                                 "tool_calls": [{"function": {"name": "get_weather",
                                                              "arguments": {}}}]},
                     "done": True}),
            _stream(_final("It's 70 and clear.")),
        ]
        stub = {"get_weather": lambda **_: {"temp_f": 70, "description": "clear"}}
        prior_history = [{"role": "user", "content": "old question"},
                         {"role": "assistant", "content": "old answer"}]
        events = []
        with mock.patch("pythia.requests.post", side_effect=responses), \
                mock.patch.dict(pythia._DISPATCH, stub):
            pythia.ask("weather?", history=prior_history, on_event=events.append)

        turn = events[0]["turn_messages"]
        roles = [m["role"] for m in turn]
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])
        self.assertEqual(turn[0]["content"], "weather?")
        self.assertIn("tool_calls", turn[1])
        self.assertEqual(turn[2]["role"], "tool")
        self.assertEqual(turn[3]["content"], "It's 70 and clear.")
        # Neither the system prompt nor the passed-in prior history leaked in.
        self.assertNotIn("old question", json.dumps(turn))
        self.assertFalse(any(m.get("role") == "system" for m in turn))

    def test_turn_messages_present_on_connection_error(self):
        # Even the friendly-degrade paths must end the turn with an assistant
        # message, so a caller that persists turn_messages always gets a
        # well-formed user->assistant exchange, never a dangling user message.
        events = []
        with mock.patch("pythia.requests.post",
                        side_effect=requests.ConnectionError()):
            out = pythia.ask("hi", on_event=events.append)

        turn = events[0]["turn_messages"]
        self.assertEqual(turn[0], {"role": "user", "content": "hi"})
        self.assertEqual(turn[-1]["role"], "assistant")
        self.assertEqual(turn[-1]["content"], out)

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
        # Two empty rounds: the first is retried, the second exhausts the budget.
        with mock.patch("pythia.requests.post",
                        side_effect=[_stream(_final("")), _stream(_final(""))]):
            out = pythia.ask("hi")
        self.assertTrue(out.strip())      # readable line, never an empty string
        self.assertNotIn("(no answer)", out)   # and never a bare placeholder

    def test_empty_reply_is_retried_and_the_retry_wins(self):
        # gemma4:e2b sometimes ends a turn with no content at all.
        with mock.patch("pythia.requests.post",
                        side_effect=[_stream(_final("")),
                                     _stream(_final("the real answer"))]) as post:
            out = pythia.ask("hi")
        self.assertEqual(out, "the real answer")
        self.assertEqual(post.call_count, 2)

    def test_blank_retry_carries_the_nudge(self):
        # Re-asking the IDENTICAL context reproduces the same silence (measured
        # 8/8 blank); the nudge is the part that actually changes the outcome.
        # ask() mutates one messages list in place, and mock records it by
        # reference — so snapshot each call's messages as they were sent.
        sent: list[list[dict]] = []
        streams = [_stream(_final("")), _stream(_final("answer"))]

        def _post(*args, **kwargs):
            sent.append([dict(m) for m in kwargs["json"]["messages"]])
            return streams.pop(0)

        with mock.patch("pythia.requests.post", side_effect=_post):
            pythia.ask("hi")

        self.assertEqual(sent[1][-1]["content"], pythia._BLANK_NUDGE)
        # ...and the empty assistant turn is gone from the retry's context.
        self.assertNotIn("", [m.get("content") for m in sent[1][1:]])

    def test_nudge_is_dropped_even_when_the_retry_round_fails(self):
        # The error handlers still emit turn_messages, so a nudged round that
        # dies mid-flight must not leave the synthetic message behind.
        events: list[dict] = []
        with mock.patch("pythia.requests.post",
                        side_effect=[_stream(_final("")),
                                     requests.ConnectionError()]):
            pythia.ask("hi", on_event=events.append)
        contents = [m.get("content") for m in events[0]["turn_messages"]]
        self.assertNotIn(pythia._BLANK_NUDGE, contents)

    def test_nudge_never_reaches_turn_messages(self):
        # turn_messages is persisted as history — a user message the user never
        # typed must not end up in it.
        events: list[dict] = []
        with mock.patch("pythia.requests.post",
                        side_effect=[_stream(_final("")), _stream(_final("answer"))]):
            pythia.ask("hi", on_event=events.append)
        contents = [m.get("content") for m in events[0]["turn_messages"]]
        self.assertNotIn(pythia._BLANK_NUDGE, contents)
        self.assertEqual(contents, ["hi", "answer"])

    def test_blank_retry_is_bounded(self):
        # One try plus one retry, then it degrades — it must not burn the whole
        # _MAX_TOOL_ROUNDS budget re-asking a model that keeps going silent.
        with mock.patch("pythia.requests.post",
                        side_effect=[_stream(_final("")) for _ in range(5)]) as post:
            pythia.ask("hi")
        self.assertEqual(post.call_count, 1 + pythia._MAX_BLANK_RETRIES)

    def test_empty_turn_never_reaches_turn_messages(self):
        # The caller persists turn_messages as history, so an empty assistant
        # turn left in place would teach the model that a blank reply is a valid
        # turn — the transcript-imitation trap documented in home_panel.py.
        events: list[dict] = []
        with mock.patch("pythia.requests.post",
                        side_effect=[_stream(_final("")),
                                     _stream(_final("real answer"))]):
            pythia.ask("hi", on_event=events.append)
        contents = [m.get("content") for m in events[0]["turn_messages"]]
        self.assertEqual(contents, ["hi", "real answer"])

    def test_degraded_blank_answer_is_what_gets_persisted(self):
        # When it stays silent, history must show the line the user actually saw.
        events: list[dict] = []
        with mock.patch("pythia.requests.post",
                        side_effect=[_stream(_final("")), _stream(_final(""))]):
            out = pythia.ask("hi", on_event=events.append)
        contents = [m.get("content") for m in events[0]["turn_messages"]]
        self.assertEqual(contents, ["hi", out])

    def test_thinking_is_never_shown_and_never_forwarded(self):
        # A round of pure reasoning must not become the answer, must not stream
        # to the UI, and must not ride back into the next round's context — the
        # transcript is for answers, not for the model's internal monologue.
        reasoning = "First I should check the time, then work out the sunset..."
        sent: list[list[dict]] = []
        streams = [_stream(_final_thinking(reasoning)), _stream(_final("Sunset is 8:41pm."))]

        def _post(*args, **kwargs):
            sent.append([dict(m) for m in kwargs["json"]["messages"]])
            return streams.pop(0)

        deltas: list[str] = []
        with mock.patch("pythia.requests.post", side_effect=_post):
            out = pythia.ask("when is sunset?", on_delta=deltas.append)

        self.assertEqual(out, "Sunset is 8:41pm.")
        self.assertNotIn("First I should", out)
        self.assertNotIn("First I should", "".join(deltas))
        retry_context = json.dumps(sent[1])
        self.assertNotIn("First I should", retry_context)
        self.assertNotIn("thinking", retry_context)

    def test_thinking_only_round_is_logged_as_the_reason_it_was_blank(self):
        # The whole point: a thinking-only round is indistinguishable from a
        # model that said nothing, unless the log says which one happened.
        reasoning = "x" * 1992     # the measured 2026-07-31 shape
        with mock.patch("pythia.requests.post",
                        side_effect=[_stream(_final_thinking(reasoning)),
                                     _stream(_final("answer"))]):
            with self.assertLogs("METIS.pythia", level="WARNING") as logs:
                pythia.ask("hi")
        blob = "\n".join(logs.output)
        self.assertIn("thinking", blob)
        self.assertIn("1992", blob)             # the size, so it's diagnosable
        # _chat names the cause, and ask()'s retry warning carries it too.
        self.assertTrue(any("think=False" in r for r in logs.output), logs.output)
        self.assertTrue(any("retrying with nudge" in r and "thinking" in r
                            for r in logs.output), logs.output)

    def test_thinking_alongside_an_answer_is_not_flagged(self):
        # Only the blank case is confusing. A round that thinks AND answers is
        # a normal round; it must not spam the log.
        with mock.patch("pythia.requests.post",
                        return_value=_stream(_final_thinking("musing", "the answer"))):
            with self.assertLogs("METIS.pythia", level="INFO") as logs:
                out = pythia.ask("hi")
        self.assertEqual(out, "the answer")
        self.assertFalse([r for r in logs.output if "thinking" in r], logs.output)

    def test_thinking_on_a_tool_round_is_not_flagged(self):
        # Tool rounds legitimately carry no content — that's not a blank turn.
        tool_round = {"message": {"role": "assistant", "content": "",
                                  "thinking": "I need the weather tool",
                                  "tool_calls": [{"function": {"name": "get_weather",
                                                               "arguments": {}}}]},
                      "done": True}
        stub = {"get_weather": lambda **_: {"temp_f": 70}}
        with mock.patch("pythia.requests.post",
                        side_effect=[_stream(tool_round), _stream(_final("70F."))]), \
                mock.patch.dict(pythia._DISPATCH, stub):
            with self.assertLogs("METIS.pythia", level="INFO") as logs:
                out = pythia.ask("weather?")
        self.assertEqual(out, "70F.")
        self.assertFalse([r for r in logs.output if "thinking" in r], logs.output)

    def test_chat_reports_thinking_chars_in_round_stats(self):
        # ask() reads this to explain WHY a round came back empty.
        with mock.patch("pythia.requests.post",
                        return_value=_stream(_final_thinking("abcde"))):
            reply, stats = pythia._chat([{"role": "user", "content": "hi"}])
        self.assertEqual(stats["thinking_chars"], 5)
        self.assertEqual(reply["content"], "")      # and it stayed out of content
        self.assertNotIn("thinking", reply)

    def test_unexpected_failure_keeps_exception_text_off_the_transcript(self):
        # The catch-all in ask() prints to the transcript verbatim, so it must
        # surface only the exception's class name — never its raw message, which
        # could carry a path or key onto the screen (§2).
        secret = "SUPERSECRET at C:/keys/finnhub.txt"
        with mock.patch("pythia.requests.post", side_effect=RuntimeError(secret)):
            out = pythia.ask("hi")
        self.assertIn("RuntimeError", out)
        self.assertNotIn("supersecret", out.lower())


class TestPythiaStats(unittest.TestCase):
    def test_stats_event_sums_across_tool_rounds(self):
        responses = [
            _stream({"message": {"role": "assistant", "content": "",
                                 "tool_calls": [{"function": {"name": "get_weather",
                                                              "arguments": {}}}]},
                     "done": True, "prompt_eval_count": 10, "eval_count": 2,
                     "eval_duration": 1_000_000}),
            _stream(_final("It's 70 and clear.", prompt_tokens=20, output_tokens=8)),
        ]
        stub = {"get_weather": lambda **_: {"temp_f": 70, "description": "clear"}}
        events = []
        with mock.patch("pythia.requests.post", side_effect=responses), \
                mock.patch.dict(pythia._DISPATCH, stub):
            pythia.ask("weather?", on_event=events.append)

        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["type"], "stats")
        self.assertEqual(ev["prompt_tokens"], 30)     # 10 + 20
        self.assertEqual(ev["output_tokens"], 10)     # 2 + 8
        self.assertEqual(ev["tools_called"], 1)
        self.assertEqual(ev["tools_failed"], 0)
        self.assertFalse(ev["cancelled"])

    def test_stats_event_counts_tool_failure(self):
        responses = [
            _stream({"message": {"role": "assistant", "content": "",
                                 "tool_calls": [{"function": {"name": "boom",
                                                              "arguments": {}}}]},
                     "done": True}),
            _stream(_final("Sorry, that failed.")),
        ]
        events = []
        with mock.patch("pythia.requests.post", side_effect=responses):
            pythia.ask("do a thing?", on_event=events.append)

        self.assertEqual(events[0]["tools_called"], 1)
        self.assertEqual(events[0]["tools_failed"], 1)
        self.assertEqual(events[0]["failed_tools"], ["boom"])

    def test_cancel_stops_stream_and_reports_cancelled(self):
        # Simulate a stream where cancel fires between two chunks: iter_lines
        # yields once, the test sets cancel, then the loop must stop reading.
        cancel = threading.Event()

        def _lines():
            yield json.dumps({"message": {"role": "assistant", "content": "Par"},
                              "done": False}).encode()
            cancel.set()
            yield json.dumps(_final("tial answer never reached")).encode()

        r = mock.Mock()
        r.raise_for_status.return_value = None
        r.iter_lines.return_value = _lines()
        r.__enter__ = mock.Mock(return_value=r)
        r.__exit__ = mock.Mock(return_value=False)

        events = []
        with mock.patch("pythia.requests.post", return_value=r):
            out = pythia.ask("long question", on_event=events.append, cancel=cancel)

        self.assertEqual(out, "Par")             # the second chunk was never applied
        self.assertTrue(events[0]["cancelled"])

    def test_system_prompt_comes_from_machine_spirit(self):
        with mock.patch("pythia.machine_spirit.effective_prompt",
                        return_value="CUSTOM PROMPT") as eff, \
                mock.patch("pythia.requests.post", return_value=_stream(_final("hi"))) as post:
            pythia.ask("hi")
        eff.assert_called_once()
        messages = post.call_args.kwargs["json"]["messages"]
        self.assertEqual(messages[0], {"role": "system", "content": "CUSTOM PROMPT"})


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
