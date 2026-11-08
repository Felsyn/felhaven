"""
test_pythia_gate.py — the argument gate every tool call passes through.

Before the gate, _dispatch handed the model's arguments straight to the handler
as fn(**arguments). A model that invented a parameter got back
"tool_failed: 'get_weather' raised TypeError", which tells it nothing it can
act on, and a number sent as a string slipped through to arithmetic
(calculate(ans="3") evaluated '3' * 2 == '33').

The gate is driven by each tool's own schema. Its rule, in CONVENTIONS §12:

  * REJECT what no handler could interpret: an undeclared parameter, a missing
    required one, a value of the wrong type. The slug says what to send
    instead, so the model can fix the call inside the same turn.
  * NORMALIZE what is unambiguous: "3" for a number, "Pause" for an enum
    value that is declared as "pause", a JSON string where an object belongs.
  * LEAVE MEANING TO THE HANDLER: enum membership and numeric ranges. Every
    enum handler already refuses a value it does not know, with its own slug,
    and Daedalus deliberately resolves names its enum does not list. Morpheus
    clamps volume rather than refusing 150.

Hermetic: handlers are mocked wherever a real one would touch the network or
change state. calculate and convert_unit run for real because they are pure.

Run from the package root:
    python -X utf8 -m unittest tests.test_pythia_gate
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pythia


def _stream(*chunks) -> mock.Mock:
    """Fake the streaming response _chat() reads (the test_pythia idiom)."""
    resp = mock.Mock()
    resp.raise_for_status.return_value = None
    resp.iter_lines.return_value = [json.dumps(c).encode("utf-8") for c in chunks]
    resp.__enter__ = mock.Mock(return_value=resp)
    resp.__exit__ = mock.Mock(return_value=False)
    return resp


def _tool_round(name: str, arguments) -> mock.Mock:
    return _stream({"message": {"role": "assistant", "content": "",
                                "tool_calls": [{"function": {
                                    "name": name, "arguments": arguments}}]},
                    "done": True})


def _final(text: str) -> mock.Mock:
    return _stream({"message": {"role": "assistant", "content": text},
                    "done": True})


class _GateCase(unittest.TestCase):
    """One mocked handler per test, and a way to call through the real gate."""

    def _mock(self, name: str, result=None) -> mock.Mock:
        handler = mock.Mock(return_value=result if result is not None else {"v": 1})
        patcher = mock.patch.dict(pythia._DISPATCH, {name: handler})
        patcher.start()
        self.addCleanup(patcher.stop)
        return handler

    def assertRefused(self, result, text=None):
        self.assertIn("error", result, f"expected a refusal, got {result!r}")
        self.assertTrue(result["error"].startswith("bad_arguments: "),
                        result["error"])
        if text is not None:
            self.assertIn(text, result["error"])


class TestTheGateRejectsWhatNoHandlerCouldInterpret(_GateCase):

    def test_a_parameter_on_a_no_parameter_tool_never_reaches_the_handler(self):
        # The likely case: "weather in Pittsburgh" and the model invents
        # `location`. Running anyway would report HOME's weather as Pittsburgh's.
        handler = self._mock("get_weather")
        out = pythia._dispatch("get_weather", {"location": "Pittsburgh"})
        self.assertEqual(out["error"],
                         "bad_arguments: get_weather takes no parameters")
        handler.assert_not_called()

    def test_timezone_on_get_time_context_is_refused(self):
        handler = self._mock("get_time_context")
        self.assertRefused(pythia._dispatch("get_time_context", {"timezone": "UTC"}),
                           "takes no parameters")
        handler.assert_not_called()

    def test_a_misnamed_parameter_is_answered_with_the_real_names(self):
        handler = self._mock("calculate")
        out = pythia._dispatch("calculate", {"expr": "1+1"})
        self.assertEqual(out["error"],
                         "bad_arguments: calculate takes only: expression, ans")
        handler.assert_not_called()

    def test_a_missing_required_parameter_is_named_with_its_description(self):
        # The description rides along so the refusal carries the same guidance
        # the schema gave — for a DJ turn, "an artist and a song title".
        handler = self._mock("calculate")
        out = pythia._dispatch("calculate", {})
        self.assertRefused(out, "missing 'expression'")
        desc = pythia._SCHEMAS["calculate"]["properties"]["expression"]["description"]
        self.assertIn(desc[:30], out["error"])
        handler.assert_not_called()

    def test_every_missing_parameter_is_named_not_just_the_first(self):
        out = pythia._dispatch("convert_unit", {})
        for name in ("value", "from_unit", "to_unit"):
            self.assertIn(f"missing '{name}'", out["error"])

    def test_a_word_where_a_number_belongs_is_refused_not_raised(self):
        out = pythia._dispatch("convert_unit",
                               {"value": "five", "from_unit": "km", "to_unit": "mi"})
        self.assertEqual(out["error"], "bad_arguments: 'value' must be a number")

    def test_nan_and_infinity_are_not_numbers_here(self):
        # float("nan") parses, which is exactly why this needs its own test.
        for bad in ("nan", "inf", "-Infinity", float("nan"), float("inf")):
            with self.subTest(value=bad):
                self.assertRefused(pythia._dispatch(
                    "convert_unit", {"value": bad, "from_unit": "km", "to_unit": "mi"}),
                    "must be a number")

    def test_a_bool_is_not_a_number(self):
        # bool is an int subclass in Python; True would silently become 1.
        self.assertRefused(pythia._dispatch(
            "convert_unit", {"value": True, "from_unit": "km", "to_unit": "mi"}),
            "must be a number")

    def test_an_object_where_text_belongs_is_refused(self):
        handler = self._mock("search_web")
        self.assertRefused(pythia._dispatch("search_web", {"query": {"q": "x"}}),
                           "'query' must be text")
        handler.assert_not_called()

    def test_arguments_that_are_not_an_object_are_refused(self):
        for bad in (["x"], "[1, 2]", "{not json", 7):
            with self.subTest(arguments=bad):
                self.assertEqual(pythia._dispatch("calculate", bad),
                                 {"error": "bad_arguments: arguments must be an object"})


class TestTheGateNormalizesTheUnambiguous(_GateCase):

    def test_a_numeric_string_for_ans_is_a_number_again(self):
        # Was '3' * 2 == '33' → evaluation_failed. Real zeno, no mock.
        out = pythia._dispatch("calculate", {"expression": "ans*2", "ans": "3"})
        self.assertNotIn("error", out)
        self.assertEqual(out["result"], 6)

    def test_a_numeric_string_value_reaches_the_handler_as_a_float(self):
        handler = self._mock("convert_unit")
        pythia._dispatch("convert_unit",
                         {"value": " 5 ", "from_unit": "km", "to_unit": "mi"})
        handler.assert_called_once_with(value=5.0, from_unit="km", to_unit="mi")

    def test_a_real_number_is_passed_through_untouched(self):
        handler = self._mock("convert_unit")
        pythia._dispatch("convert_unit", {"value": 5, "from_unit": "km", "to_unit": "mi"})
        self.assertEqual(handler.call_args.kwargs["value"], 5)

    def test_a_number_where_text_belongs_becomes_text(self):
        handler = self._mock("search_web")
        pythia._dispatch("search_web", {"query": 1984})
        handler.assert_called_once_with(query="1984")

    def test_enum_values_are_matched_without_regard_to_case_or_spaces(self):
        handler = self._mock("control_music")
        for sent, expected in (("Pause", "pause"), (" STOP ", "stop"), ("next", "next")):
            with self.subTest(sent=sent):
                handler.reset_mock()
                pythia._dispatch("control_music", {"action": sent})
                handler.assert_called_once_with(action=expected)

    def test_null_means_not_given(self):
        # Models send null for an optional parameter they are not using. The
        # handler's own default applies — and for a required one, it is missing.
        handler = self._mock("get_news_stories")
        pythia._dispatch("get_news_stories", {"feed": None})
        handler.assert_called_once_with()
        self.assertRefused(pythia._dispatch("calculate", {"expression": None}),
                           "missing 'expression'")
        weather = self._mock("get_weather")
        pythia._dispatch("get_weather", {"location": None})
        weather.assert_called_once_with()

    def test_arguments_sent_as_a_json_string_are_parsed(self):
        out = pythia._dispatch("calculate", '{"expression": "1+1"}')
        self.assertEqual(out["result"], 2)

    def test_integer_parameters_take_whole_numbers_only(self):
        fake = {"type": "object",
                "properties": {"n": {"type": "integer", "description": "A count."}},
                "required": ["n"]}
        handler = self._mock("fake_tool")
        with mock.patch.dict(pythia._SCHEMAS, {"fake_tool": fake}):
            for sent, expected in (("3", 3), (3.0, 3), (4, 4)):
                with self.subTest(sent=sent):
                    handler.reset_mock()
                    pythia._dispatch("fake_tool", {"n": sent})
                    handler.assert_called_once_with(n=expected)
                    self.assertIsInstance(handler.call_args.kwargs["n"], int)
            for bad in ("3.5", 2.5, "three"):
                with self.subTest(bad=bad):
                    self.assertRefused(pythia._dispatch("fake_tool", {"n": bad}),
                                       "'n' must be a whole number")


class TestTheGateLeavesMeaningToTheHandler(_GateCase):

    def test_an_enum_value_it_does_not_know_goes_to_the_handler(self):
        # Morpheus answers this with its own unknown_action slug listing the
        # choices; the gate refusing first would just say the same thing twice.
        handler = self._mock("control_music")
        pythia._dispatch("control_music", {"action": "obliterate"})
        handler.assert_called_once_with(action="obliterate")

    def test_daedalus_still_resolves_names_its_enum_does_not_list(self):
        # A sidebar name is not an enum key, and Daedalus resolves it on
        # purpose. Strict enum rejection would break that.
        handler = self._mock("get_module_docstring")
        pythia._dispatch("get_module_docstring", {"module": "Atmospherics"})
        handler.assert_called_once_with(module="Atmospherics")

    def test_a_declared_daedalus_key_is_still_canonicalized(self):
        handler = self._mock("get_module_docstring")
        pythia._dispatch("get_module_docstring", {"module": "ZENO"})
        handler.assert_called_once_with(module="zeno")

    def test_a_number_out_of_range_goes_to_the_handler(self):
        # Morpheus clamps 150 to 100; a refusal would cost the model a retry.
        handler = self._mock("control_music")
        pythia._dispatch("control_music", {"action": "volume", "level": 150})
        handler.assert_called_once_with(action="volume", level=150)

    def test_an_empty_string_for_a_required_parameter_is_present(self):
        # Awen's own no_query guard owns what an empty query means.
        handler = self._mock("play_music")
        pythia._dispatch("play_music", {"query": ""})
        handler.assert_called_once_with(query="")

    def test_a_handler_with_no_registered_schema_is_not_gated(self):
        handler = self._mock("boom")
        pythia._dispatch("boom", {"anything": 1})
        handler.assert_called_once_with(anything=1)


class TestTheGateCoversEveryTool(unittest.TestCase):
    """Reflection over the live registry — no hardcoded list or count."""

    def test_every_registered_tool_has_a_schema_for_the_gate(self):
        self.assertEqual(set(pythia._SCHEMAS), set(pythia._DISPATCH))

    def test_an_empty_call_names_exactly_the_required_parameters(self):
        with_required = without = 0
        for tool in pythia.TOOLS:
            name = tool["function"]["name"]
            required = tool["function"]["parameters"].get("required") or []
            with self.subTest(tool=name):
                _clean, error = pythia._check_arguments(name, {})
                if not required:
                    without += 1
                    self.assertIsNone(error)
                    continue
                with_required += 1
                self.assertIsNotNone(error)
                for param in required:
                    self.assertIn(f"missing '{param}'", error["error"])
        # Non-vacuous: both branches actually ran.
        self.assertGreater(with_required, 0)
        self.assertGreater(without, 0)


class TestTheGateInsideATurn(_GateCase):

    def test_a_per_turn_override_is_gated_by_the_same_schema(self):
        override = mock.Mock(return_value={"v": 1})
        out = pythia._dispatch("play_music", {}, None, {"play_music": override})
        self.assertRefused(out, "missing 'query'")
        override.assert_not_called()

    def test_the_refusal_reaches_the_model_and_the_rite_log(self):
        self._mock("get_weather")
        events: list = []
        with mock.patch("pythia.requests.post",
                        side_effect=[_tool_round("get_weather", {"location": "Ely"}),
                                     _final("Home only.")]) as post:
            with self.assertLogs("METIS.pythia", level="WARNING"):
                pythia.ask("weather in Ely?", on_event=events.append)
        second = post.call_args_list[1].kwargs["json"]["messages"]
        tool_msgs = [m for m in second if m.get("role") == "tool"]
        self.assertIn("bad_arguments", tool_msgs[0]["content"])
        (rec,) = events[0]["tool_records"]
        self.assertEqual(rec["status"], "failed")
        self.assertTrue(rec["error"].startswith("bad_arguments: "))

    def test_the_rite_keeps_what_the_model_sent_not_what_the_gate_cleaned(self):
        # The operator opened the Rite Log to see the model's call.
        handler = self._mock("control_music")
        events: list = []
        with mock.patch("pythia.requests.post",
                        side_effect=[_tool_round("control_music", {"action": "Pause"}),
                                     _final("Paused.")]):
            pythia.ask("pause", on_event=events.append)
        handler.assert_called_once_with(action="pause")
        self.assertEqual(events[0]["tool_records"][0]["arguments"], {"action": "Pause"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
