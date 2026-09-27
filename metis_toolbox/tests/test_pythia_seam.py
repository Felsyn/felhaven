"""
test_pythia_seam.py — the narrow seam that lets a non-chat caller use the oracle.

ask() grew two optional parameters and the module grew a busy signal. All three
are for callers that are NOT the chat: a job that runs unattended, with its own
voice and its own instructions, feeding text it did not write (YouTube titles,
fetched pages) into a small model's context with nobody reading the transcript.

What earns a test here:

  * THE ALLOWLIST IS ENFORCED AT DISPATCH, not by omitting schemas. Leaving a
    tool out of `tools` is a hint to the model; _DISPATCH will still run any
    name it recognises. This is the file that proves the boundary is real —
    that a refused call comes back as a slug and the handler is never entered.
  * DEFAULTS ARE UNCHANGED. Every existing caller passes neither parameter, so
    the strongest thing these tests can say is "chat behaves exactly as it did".
    tests/test_pythia.py still covers that end; here it is asserted explicitly
    against the new code paths.
  * THE COUNTER CANNOT LEAK. is_busy() is read by a worker deciding whether to
    take the model. A count stranded by an exception or an early return would
    make it permanently busy and silently stop that job forever — a failure with
    no symptom except silence, which is the shape this stack has been bitten by
    before (see CONVENTIONS §12 on HomePanel._poll).

Hermetic: requests.post is mocked throughout, so no Ollama, no network, no
model. Nothing here touches a real tool handler.

Run from the package root:
    python -X utf8 -m unittest tests.test_pythia_seam
"""

import json
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pythia


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


def _tool_round(name: str, arguments: dict) -> dict:
    return {"message": {"role": "assistant", "content": "",
                        "tool_calls": [{"function": {"name": name,
                                                     "arguments": arguments}}]},
            "done": True}


class _SeamCase(unittest.TestCase):
    """Guarantees the turn counter is back to zero after every test, so one
    leaking test cannot make every later is_busy() read True."""

    def setUp(self):
        pythia._turns_in_flight = 0
        self.addCleanup(self._reset)

    def _reset(self):
        pythia._turns_in_flight = 0

    def _post(self, *chunks):
        return mock.patch("pythia.requests.post", return_value=_stream(*chunks))


class TestSystemOverride(_SeamCase):
    def test_none_still_reads_the_machine_spirit_prompt(self):
        """The default path, and the one every chat turn is on."""
        with mock.patch("pythia.machine_spirit.effective_prompt",
                        return_value="SPIRIT") as effective, \
                self._post(_final("hi")) as post:
            pythia.ask("hi")
        effective.assert_called_once()
        messages = post.call_args.kwargs["json"]["messages"]
        self.assertEqual(messages[0], {"role": "system", "content": "SPIRIT"})

    def test_an_override_replaces_it_without_consulting_machine_spirit(self):
        """Not merely "the override wins" — Machine Spirit must not be READ.
        A job with its own voice should not be expressed as an edit to the
        operator's prompt, which would then apply to every chat answer too."""
        with mock.patch("pythia.machine_spirit.effective_prompt",
                        return_value="SPIRIT") as effective, \
                self._post(_final("hi")) as post:
            pythia.ask("hi", system="I AM THE RADIO HOST")
        effective.assert_not_called()
        messages = post.call_args.kwargs["json"]["messages"]
        self.assertEqual(messages[0],
                         {"role": "system", "content": "I AM THE RADIO HOST"})

    def test_the_override_does_not_persist_to_the_next_turn(self):
        """One turn only. A sticky override would silently re-voice chat."""
        with mock.patch("pythia.machine_spirit.effective_prompt",
                        return_value="SPIRIT"), self._post(_final("a")):
            pythia.ask("one", system="DJ VOICE")
        with mock.patch("pythia.machine_spirit.effective_prompt",
                        return_value="SPIRIT"), self._post(_final("b")) as post:
            pythia.ask("two")
        self.assertEqual(
            post.call_args.kwargs["json"]["messages"][0]["content"], "SPIRIT")

    def test_a_blank_override_falls_back_and_says_so(self):
        """Sending an empty system prompt is never what a caller meant, and a
        silent fallback would leave a DJ turn quietly speaking as chat."""
        for blank in ("", "   ", "\n"):
            with self.subTest(blank=repr(blank)):
                with mock.patch("pythia.machine_spirit.effective_prompt",
                                return_value="SPIRIT"), \
                        self._post(_final("hi")) as post:
                    with self.assertLogs("METIS.pythia", level="WARNING"):
                        pythia.ask("hi", system=blank)
                self.assertEqual(
                    post.call_args.kwargs["json"]["messages"][0]["content"],
                    "SPIRIT")


class TestToolSchemaNarrowing(_SeamCase):
    def test_none_sends_the_whole_registry(self):
        with self._post(_final("hi")) as post:
            pythia.ask("hi")
        self.assertEqual(post.call_args.kwargs["json"]["tools"], pythia.TOOLS)

    def test_an_allowlist_sends_only_those_schemas(self):
        with self._post(_final("hi")) as post:
            pythia.ask("hi", allow_tools=["play_music"])
        sent = post.call_args.kwargs["json"]["tools"]
        self.assertEqual([t["function"]["name"] for t in sent], ["play_music"])

    def test_the_narrowed_schema_is_the_registry_s_own_text(self):
        """Filtered from the live TOOLS, never rebuilt. A hand-written copy
        would drift from the module that owns it the first time a description
        changed, and the model would be reading a stale manual."""
        with self._post(_final("hi")) as post:
            pythia.ask("hi", allow_tools=["play_music"])
        sent = post.call_args.kwargs["json"]["tools"][0]
        original = next(t for t in pythia.TOOLS
                        if t["function"]["name"] == "play_music")
        self.assertIs(sent, original)

    def test_an_unregistered_name_is_logged_rather_than_silently_dropped(self):
        """A typo in a caller's allowlist would otherwise yield a shorter list —
        or none at all — and the only symptom would be a model that mysteriously
        stopped calling tools."""
        with self._post(_final("hi")):
            with self.assertLogs("METIS.pythia", level="WARNING") as caught:
                pythia.ask("hi", allow_tools=["play_music", "play_muzik"])
        self.assertTrue(any("play_muzik" in line for line in caught.output))

    def test_an_empty_allowlist_sends_no_tools(self):
        """Distinct from None. [] means "no tools this turn", and must not be
        mistaken for "unrestricted" by a falsy check."""
        with self._post(_final("hi")) as post:
            pythia.ask("hi", allow_tools=[])
        self.assertEqual(post.call_args.kwargs["json"]["tools"], [])


class TestAllowlistIsEnforcedAtDispatch(_SeamCase):
    """The boundary. Omitting a schema is a HINT; _DISPATCH runs any name it
    recognises, so without this the model could call anything by simply naming
    it — which is exactly what a prompt-injected YouTube title would try."""

    def test_a_disallowed_tool_is_refused_and_never_dispatched(self):
        with mock.patch.dict(pythia._DISPATCH,
                             {"control_music": mock.Mock()}, clear=False):
            handler = pythia._DISPATCH["control_music"]
            with mock.patch("pythia.requests.post",
                            side_effect=[_stream(_tool_round("control_music",
                                                             {"action": "stop"})),
                                         _stream(_final("done"))]):
                with self.assertLogs("METIS.pythia", level="WARNING"):
                    pythia.ask("hi", allow_tools=["play_music"])
            handler.assert_not_called()

    def test_the_refusal_is_a_slug_the_model_can_recover_from(self):
        """§2 shape, so the model can re-pick inside the same turn rather than
        reading prose it has to interpret."""
        result = pythia._dispatch("control_music", {}, {"play_music"})
        self.assertIn("error", result)
        slug = result["error"].split(":", 1)[0]
        self.assertRegex(slug, r"^[a-z][a-z0-9_]*$")
        self.assertEqual(slug, "tool_not_available")

    def test_the_refusal_reaches_the_model_as_a_tool_message(self):
        """It has to come back IN the conversation, or the model has nothing to
        re-pick from and the turn just stalls."""
        with mock.patch("pythia.requests.post",
                        side_effect=[_stream(_tool_round("control_music", {})),
                                     _stream(_final("ok"))]) as post:
            with self.assertLogs("METIS.pythia", level="WARNING"):
                pythia.ask("hi", allow_tools=["play_music"])
        second_round = post.call_args_list[1].kwargs["json"]["messages"]
        tool_messages = [m for m in second_round if m.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 1)
        self.assertIn("tool_not_available", tool_messages[0]["content"])

    def test_an_allowed_tool_still_runs(self):
        """The guard must not be so keen it refuses the one tool it was given."""
        with mock.patch.dict(pythia._DISPATCH,
                             {"play_music": mock.Mock(return_value={"ok": 1})},
                             clear=False):
            handler = pythia._DISPATCH["play_music"]
            with mock.patch("pythia.requests.post",
                            side_effect=[_stream(_tool_round("play_music",
                                                             {"query": "x"})),
                                         _stream(_final("done"))]):
                pythia.ask("hi", allow_tools=["play_music"])
            handler.assert_called_once_with(query="x")

    def test_chat_turns_are_not_restricted(self):
        with mock.patch.dict(pythia._DISPATCH,
                             {"control_music": mock.Mock(return_value={"ok": 1})},
                             clear=False):
            handler = pythia._DISPATCH["control_music"]
            with mock.patch("pythia.requests.post",
                            side_effect=[_stream(_tool_round("control_music",
                                                             {"action": "stop"})),
                                         _stream(_final("done"))]):
                pythia.ask("stop the music")
            handler.assert_called_once()

    def test_a_refused_call_is_recorded_as_a_failed_rite(self):
        """A refusal is a real event. Dropping it from the diagnostics would
        leave an operator staring at a turn where the model did nothing, with
        no record of what it tried."""
        events: list = []
        with mock.patch("pythia.requests.post",
                        side_effect=[_stream(_tool_round("control_music", {})),
                                     _stream(_final("ok"))]):
            with self.assertLogs("METIS.pythia", level="WARNING"):
                pythia.ask("hi", allow_tools=["play_music"],
                           on_event=events.append)
        records = events[0]["tool_records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["name"], "control_music")
        self.assertEqual(records[0]["status"], "failed")
        self.assertIn("tool_not_available", records[0]["error"])

    def test_the_check_sits_ahead_of_the_registry_lookup(self):
        """Order matters for the message, not just the outcome: a disallowed
        name must read as disallowed, and an unknown one as unknown, or nobody
        debugging an allowlist can tell a typo from a refusal."""
        refused = pythia._dispatch("control_music", {}, {"play_music"})
        unknown = pythia._dispatch("no_such_tool", {}, {"no_such_tool"})
        self.assertIn("tool_not_available", refused["error"])
        self.assertIn("unknown_tool", unknown["error"])

    def test_an_empty_allowlist_restricts_EVERYTHING(self):
        """[] is not None, and the difference is a falsy check away from being a
        security hole: `set(x) if x else None` reads as tidier and silently turns
        "no tools at all" into "every tool". The schema list going empty is not
        enough to catch it — that happens either way — so this asserts at
        DISPATCH, where the boundary actually is.

        Found by a red-check: the earlier empty-allowlist test only inspected
        the schemas and stayed green while dispatch was wide open."""
        with mock.patch.dict(pythia._DISPATCH,
                             {"play_music": mock.Mock(return_value={"ok": 1})},
                             clear=False):
            handler = pythia._DISPATCH["play_music"]
            with mock.patch("pythia.requests.post",
                            side_effect=[_stream(_tool_round("play_music",
                                                             {"query": "x"})),
                                         _stream(_final("done"))]):
                with self.assertLogs("METIS.pythia", level="WARNING"):
                    pythia.ask("hi", allow_tools=[])
            handler.assert_not_called()

    def test_an_empty_allowlist_refuses_at_the_dispatcher_directly(self):
        result = pythia._dispatch("play_music", {"query": "x"}, set())
        self.assertIn("tool_not_available", result["error"])

    def test_no_allowlist_means_no_restriction(self):
        with mock.patch.dict(pythia._DISPATCH,
                             {"play_music": mock.Mock(return_value={"ok": 1})},
                             clear=False):
            self.assertEqual(pythia._dispatch("play_music", {}, None), {"ok": 1})


class TestIsBusy(_SeamCase):
    """Read by a worker deciding whether to take the model. A count stranded by
    an exception would make it permanently busy and stop that job forever, with
    no symptom but silence."""

    def test_false_when_idle(self):
        self.assertFalse(pythia.is_busy())

    def test_true_while_a_turn_is_generating(self):
        seen: list = []

        def _peek(*_a, **_kw):
            seen.append(pythia.is_busy())
            return _stream(_final("hi"))

        with mock.patch("pythia.requests.post", side_effect=_peek):
            pythia.ask("hi")
        self.assertEqual(seen, [True])

    def test_false_again_once_the_turn_finishes(self):
        with self._post(_final("hi")):
            pythia.ask("hi")
        self.assertFalse(pythia.is_busy())

    def test_the_count_survives_a_connection_error(self):
        import requests
        with mock.patch("pythia.requests.post",
                        side_effect=requests.ConnectionError("down")):
            pythia.ask("hi")
        self.assertFalse(pythia.is_busy())

    def test_the_count_survives_a_timeout(self):
        import requests
        with mock.patch("pythia.requests.post",
                        side_effect=requests.Timeout("slow")):
            pythia.ask("hi")
        self.assertFalse(pythia.is_busy())

    def test_the_count_survives_an_unexpected_failure(self):
        with mock.patch("pythia.requests.post",
                        side_effect=RuntimeError("boom")):
            pythia.ask("hi")
        self.assertFalse(pythia.is_busy())

    def test_the_count_survives_a_cancelled_turn(self):
        cancel = threading.Event()
        cancel.set()
        with self._post(_final("hi")):
            pythia.ask("hi", cancel=cancel)
        self.assertFalse(pythia.is_busy())

    def test_it_counts_rather_than_latches(self):
        """Two overlapping turns are a reachable state — a chat question while
        a background job is mid-turn. A bool would clear on the first finish and
        report idle while the other was still generating."""
        pythia._enter_turn()
        pythia._enter_turn()
        self.assertTrue(pythia.is_busy())
        pythia._leave_turn()
        self.assertTrue(pythia.is_busy())
        pythia._leave_turn()
        self.assertFalse(pythia.is_busy())

    def test_it_never_goes_negative(self):
        """A stray decrement must not drive the count below zero, where a later
        real turn would read as idle."""
        pythia._leave_turn()
        pythia._leave_turn()
        pythia._enter_turn()
        self.assertTrue(pythia.is_busy())


class TestDegradedAnswers(_SeamCase):
    """ask() returns prose when there is no answer to give, and a caller that
    ACTS on the text has to be able to tell that apart from an answer.

    Written after Auto-DJ spoke "The oracle got stuck consulting its tools. Try
    rephrasing." aloud as a song introduction — advice to rephrase a question
    the listener never asked.

    Every path is driven for real rather than compared against a list, because
    two of the five interpolate (the endpoint, an exception class) so there is
    no exact set to hold. What is asserted is the property that matters: every
    way ask() can give up is recognisable by is_degraded().
    """

    def test_a_real_answer_is_not_degraded(self):
        with self._post(_final("Here's Dreams by Fleetwood Mac.")):
            answer = pythia.ask("go")
        self.assertFalse(pythia.is_degraded(answer), answer)

    def test_running_out_of_tool_rounds_is_degraded(self):
        """The exact path from the transcript: the model keeps asking for tools
        until the round budget is gone."""
        rounds = [_stream(_tool_round("play_music", {"query": "x"}))
                  for _ in range(pythia._MAX_TOOL_ROUNDS)]
        with mock.patch.dict(pythia._DISPATCH,
                             {"play_music": mock.Mock(return_value={"ok": 1})},
                             clear=False), \
                mock.patch("pythia.requests.post", side_effect=rounds):
            answer = pythia.ask("go")
        self.assertTrue(pythia.is_degraded(answer), answer)

    def test_a_model_that_says_nothing_is_degraded(self):
        with mock.patch("pythia.requests.post",
                        side_effect=[_stream(_final("")), _stream(_final("")),
                                     _stream(_final(""))]):
            answer = pythia.ask("go")
        self.assertTrue(pythia.is_degraded(answer), answer)

    def test_an_unreachable_ollama_is_degraded(self):
        import requests
        with mock.patch("pythia.requests.post",
                        side_effect=requests.ConnectionError("down")):
            answer = pythia.ask("go")
        self.assertTrue(pythia.is_degraded(answer), answer)

    def test_a_timeout_is_degraded(self):
        import requests
        with mock.patch("pythia.requests.post",
                        side_effect=requests.Timeout("slow")):
            answer = pythia.ask("go")
        self.assertTrue(pythia.is_degraded(answer), answer)

    def test_an_unexpected_failure_is_degraded(self):
        with mock.patch("pythia.requests.post",
                        side_effect=RuntimeError("boom")):
            answer = pythia.ask("go")
        self.assertTrue(pythia.is_degraded(answer), answer)

    def test_blank_and_None_are_not_degraded(self):
        """"Nothing" is a different problem with a different fix — a caller
        checks for empty itself. is_degraded must not swallow that case."""
        for value in ("", "   ", None):
            with self.subTest(value=repr(value)):
                self.assertFalse(pythia.is_degraded(value))

    def test_the_whole_family_shares_the_prefix(self):
        """The prefix IS the test, so the constants have to keep earning it."""
        for literal in (pythia._SILENT_ANSWER, pythia._STUCK_ANSWER,
                        pythia._TIMEOUT_ANSWER):
            with self.subTest(literal=literal[:30]):
                self.assertTrue(literal.startswith(pythia._DEGRADED_PREFIX))


class TestChatIsUnchanged(_SeamCase):
    """The strongest thing this seam can claim: no existing caller changed."""

    def test_the_payload_still_carries_the_shared_options(self):
        """Ollama keys a resident runner on its options — a narrowed turn that
        sent different ones would force a ~70s reload between every DJ pick and
        every chat answer (pythia.py's own note)."""
        with self._post(_final("hi")) as post:
            pythia.ask("hi", system="DJ", allow_tools=["play_music"])
        narrowed = post.call_args.kwargs["json"]
        with self._post(_final("hi")) as post:
            pythia.ask("hi")
        plain = post.call_args.kwargs["json"]
        self.assertEqual(narrowed["options"], plain["options"])
        self.assertEqual(narrowed["options"], dict(pythia._OPTIONS))

    def test_keep_alive_and_think_are_the_same_on_both_paths(self):
        with self._post(_final("hi")) as post:
            pythia.ask("hi", allow_tools=["play_music"])
        narrowed = post.call_args.kwargs["json"]
        self.assertEqual(narrowed["keep_alive"], pythia._KEEP_ALIVE)
        self.assertIs(narrowed["think"], False)

    def test_a_narrowed_turn_still_returns_its_answer_and_stats(self):
        events: list = []
        with self._post(_final("Now playing.")):
            answer = pythia.ask("go", system="DJ", allow_tools=["play_music"],
                                on_event=events.append)
        self.assertEqual(answer, "Now playing.")
        self.assertEqual(events[0]["type"], "stats")
        self.assertIn("turn_messages", events[0])


if __name__ == "__main__":
    unittest.main()
