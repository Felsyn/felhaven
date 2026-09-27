"""
test_pythia_verbatim.py — the one path a tool's output takes AROUND the model.

Daedalus's docstrings must reach the user word for word, and anything the model
sees, it paraphrases. So ask() hands a "verbatim" payload to on_verbatim and
gives the model only an acknowledgement. What earns a test here:

  * THE MODEL NEVER SEES THE TEXT — not in any request, not in turn_messages
    (which HomePanel stores as history), not in the Rite Log. On every path,
    including a caller with no display and a display that fails.
  * THE TEXT IS EXACT — byte-identical to ast.get_docstring(clean=True).
  * A ROUND OF ONLY VERBATIM CALLS ENDS THE TURN, on the ack line, with no
    second request; a mixed round goes back to the model as before.
  * EVERY OTHER TOOL IS UNTOUCHED.

Drives the REAL get_module_docstring handler against the real tree (local
files only); requests.post is mocked, so no Ollama and no network.

Run from the package root:
    python -X utf8 -m unittest tests.test_pythia_verbatim
"""

import ast
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pythia

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with open(os.path.join(_APP_ROOT, "tools", "aura.py"), encoding="utf-8") as _fh:
    AURA_DOC = ast.get_docstring(ast.parse(_fh.read()), clean=True)


def _chunk(obj) -> bytes:
    return json.dumps(obj).encode("utf-8")


def _stream(*chunks) -> mock.Mock:
    """Fake the streaming response _chat() reads (the test_pythia idiom)."""
    resp = mock.Mock()
    resp.raise_for_status.return_value = None
    resp.iter_lines.return_value = [_chunk(c) for c in chunks]
    resp.__enter__ = mock.Mock(return_value=resp)
    resp.__exit__ = mock.Mock(return_value=False)
    return resp


def _final(text: str) -> dict:
    return {"message": {"role": "assistant", "content": text}, "done": True}


def _tool_round(*calls) -> dict:
    """One assistant round requesting each (name, arguments) in `calls`."""
    return {"message": {"role": "assistant", "content": "",
                        "tool_calls": [{"function": {"name": n, "arguments": a}}
                                       for n, a in calls]},
            "done": True}


_ASK_AURA = ("get_module_docstring", {"module": "aura"})


class _VerbatimCase(unittest.TestCase):
    def setUp(self):
        pythia._turns_in_flight = 0
        self.shown = []
        self.events = []

    def _ask(self, *rounds, sink=True):
        with mock.patch("pythia.requests.post",
                        side_effect=[_stream(r) for r in rounds]) as post:
            answer = pythia.ask(
                "what does the weather module do?",
                on_event=self.events.append,
                on_verbatim=self.shown.append if sink is True else sink)
        self.post = post
        return answer

    def _every_request(self) -> str:
        return "\n".join(json.dumps(c.kwargs["json"]) for c in self.post.call_args_list)

    def _turn(self):
        return self.events[0]["turn_messages"]

    def _tool_message(self):
        return [m for m in self._turn() if m["role"] == "tool"][0]


class TestTheTextIsExact(_VerbatimCase):
    def test_the_sink_gets_the_docstring_byte_for_byte(self):
        self._ask(_tool_round(_ASK_AURA))
        self.assertEqual(len(self.shown), 1)
        self.assertEqual(self.shown[0]["text"], AURA_DOC)
        self.assertEqual(self.shown[0]["label"], "daedalus")
        self.assertEqual(self.shown[0]["title"], "aura · tools/aura.py")


class TestTheModelNeverSeesIt(_VerbatimCase):
    def test_no_request_and_no_history_carries_the_text(self):
        self._ask(_tool_round(_ASK_AURA))
        self.assertNotIn(AURA_DOC, self._every_request())
        self.assertNotIn(AURA_DOC, json.dumps(self._turn()))
        self.assertEqual(json.loads(self._tool_message()["content"]),
                         {"module": "aura", "path": "tools/aura.py",
                          "shown_to_user": True})

    def test_the_rite_log_holds_what_the_model_saw(self):
        self._ask(_tool_round(_ASK_AURA))
        record = self.events[0]["tool_records"][0]
        self.assertEqual(record["status"], "ok")
        self.assertNotIn("Report current weather", record["result_preview"])
        self.assertIn('"shown_to_user": true', record["result_preview"])

    def test_with_no_display_the_model_gets_a_slug_not_the_text(self):
        answer = self._ask(_tool_round(_ASK_AURA),
                           _final("I can't show that here."), sink=None)
        self.assertEqual(answer, "I can't show that here.")
        self.assertEqual(self.shown, [])
        self.assertNotIn(AURA_DOC, self._every_request())
        self.assertTrue(json.loads(self._tool_message()["content"])["error"]
                        .startswith("no_display: "))
        self.assertEqual(self.events[0]["tool_records"][0]["status"], "failed")

    def test_a_failing_display_does_not_break_the_turn(self):
        def broken(_payload):
            raise RuntimeError("the transcript is gone")
        answer = self._ask(_tool_round(_ASK_AURA), _final("Sorry."), sink=broken)
        self.assertEqual(answer, "Sorry.")
        self.assertNotIn(AURA_DOC, self._every_request())
        self.assertTrue(json.loads(self._tool_message()["content"])["error"]
                        .startswith("display_failed: "))


class TestTheTurnEndsOnTheAck(_VerbatimCase):
    def test_one_request_and_the_ack_is_the_answer(self):
        answer = self._ask(_tool_round(_ASK_AURA))
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(answer, "Here is the docstring for aura.")
        self.assertFalse(pythia.is_degraded(answer))
        self.assertEqual([m["role"] for m in self._turn()],
                         ["user", "assistant", "tool", "assistant"])
        self.assertEqual(self._turn()[-1]["content"], answer)

    def test_two_verbatim_calls_end_on_both_acks(self):
        answer = self._ask(_tool_round(_ASK_AURA,
                                       ("get_module_docstring", {"module": "kairos"})))
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(len(self.shown), 2)
        self.assertEqual(answer, "Here is the docstring for aura. "
                                 "Here is the docstring for kairos.")

    def test_a_mixed_round_goes_back_to_the_model(self):
        answer = self._ask(_tool_round(_ASK_AURA, ("get_time_context", {})),
                           _final("It is noon, and the docstring is above."))
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(answer, "It is noon, and the docstring is above.")
        self.assertEqual(len(self.shown), 1)
        self.assertNotIn(AURA_DOC, self._every_request())

    def test_a_miss_goes_back_to_the_model(self):
        """Nothing was shown, so there is nothing to end the turn on."""
        answer = self._ask(_tool_round(("get_module_docstring", {"module": "wether"})),
                           _final("I couldn't find that module."))
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(self.shown, [])
        self.assertEqual(answer, "I couldn't find that module.")


class TestEverythingElseIsUntouched(unittest.TestCase):
    def test_an_ordinary_result_passes_through_as_the_same_object(self):
        sink = mock.Mock()
        result = {"temp": 70, "error_free": True}
        out, ack = pythia._split_verbatim(result, sink)
        self.assertIs(out, result)
        self.assertIsNone(ack)
        sink.assert_not_called()

    def test_a_malformed_payload_is_withheld(self):
        sink = mock.Mock()
        for bad in ("just a string", {"title": "no text"}, {"text": 42}):
            with self.subTest(payload=bad):
                out, ack = pythia._split_verbatim({"verbatim": bad}, sink)
                self.assertTrue(out["error"].startswith("tool_failed: "))
                self.assertIsNone(ack)
        sink.assert_not_called()

    def test_a_payload_without_an_ack_still_ends_cleanly(self):
        out, ack = pythia._split_verbatim({"verbatim": {"text": "x"}}, mock.Mock())
        self.assertEqual(ack, "Shown above.")
        self.assertEqual(out, {"shown_to_user": True})


if __name__ == "__main__":
    unittest.main(verbosity=2)
