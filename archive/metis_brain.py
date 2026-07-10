# ARCHIVED v4.0 — 2026-06. Designated v1 escalation layer (LLM fallback on Apollo miss).
# KNOWN BUGS — fix before reactivating:
#   1. _resolve_tool_calls() drops arguments: dispatch(tool_name) must become
#      dispatch(tool_name, **json.loads(call["function"].get("arguments") or "{}")).
#      Ammit/Zeno/Eudoxus have never worked through this path.
#   2. Tool result messages omit "tool_call_id" — required by the OpenAI message
#      format; llama-server tolerates it today, fragile across versions.
#   3. History deque can evict an assistant tool_calls message while its orphaned
#      role:"tool" message survives — malformed history, server may reject.
"""
metis_brain.py — LLM Conversation Layer
==========================================
Metis Local Voice Assistant | v4.0 | Anti-Legion: ONE JOB

Job:         Hold the rolling conversation history and send/receive
             messages to llama-server's /v1/chat/completions endpoint.

Contract:    Brain knows nothing about audio, files, or hotkeys.
             It receives a transcript string and returns response text.

Interface:   brain = MetisBrain()
             response = brain.ask("what time is it?")         # blocking
             for sentence in brain.ask_stream("hello"):       # streaming
                 speak(sentence)
             brain.reset()

Changes from v3:
  - Switched from Ollama /api/chat to llama-server /v1/chat/completions
    (OpenAI-compatible endpoint). Response parsing updated accordingly.
  - ask_stream() now resolves tool calls via a non-streaming request, then
    splits the final reply into sentences for TTS. Removes the "peek at
    first line" approach, which does not work cleanly with OpenAI SSE format.

Upstream:    Metis.py (passes transcripts)
Downstream:  Metis.py (receives response text)
"""

import json
import logging
import re
import requests
from collections import deque

from metis_config import (
    LLM_URL,
    LLM_MODEL,
    LLM_TIMEOUT,
    STREAM_TOKEN_TIMEOUT,
    TEMPERATURE,
    METIS_SYSTEM_PROMPT,
    CONVERSATION_MAX_TURNS,
    FALLBACK_TIMEOUT,
    FALLBACK_OFFLINE,
    FALLBACK_ERROR,
    FALLBACK_NO_AUDIO,
)
from metis_toolbox import TOOLS, dispatch

log = logging.getLogger("METIS.brain")

# Sentence boundary pattern — splits on . ? ! followed by whitespace or end.
# Keeps the punctuation attached to the sentence.
# Negative lookbehinds prevent false splits on common abbreviations.
_SENTENCE_END = re.compile(
    r'(?<!'            # BEGIN negative lookbehinds
    r'Mr'
    r')(?<!'
    r'Mrs'
    r')(?<!'
    r'Ms'
    r')(?<!'
    r'Dr'
    r')(?<!'
    r'Jr'
    r')(?<!'
    r'Sr'
    r')(?<!'
    r'vs'
    r')(?<!'
    r'etc'
    r')(?<!'
    r'approx'
    r')(?<!'           # single-letter abbreviations: U.S., A.M., e.g., i.e.
    r'\b[A-Za-z]'
    r')'
    r'[.?!]\s+'        # actual sentence boundary
)


class MetisBrain:
    """
    Stateful conversation manager for llama-server (OpenAI-compatible API).

    Maintains a rolling deque of message dicts:
        {"role": "user"|"assistant", "content": "..."}

    System prompt is prepended to every request but never stored.
    """

    def __init__(self):
        self._history: deque[dict] = deque(
            maxlen=CONVERSATION_MAX_TURNS * 2
        )
        log.info(
            f"MetisBrain ready. Model: {LLM_MODEL} | "
            f"Window: {CONVERSATION_MAX_TURNS} turns | "
            f"Temp: {TEMPERATURE} | "
            f"Tools: {len(TOOLS)}"
        )

    def _build_messages(self) -> list[dict]:
        """System prompt + rolling history."""
        return [
            {"role": "system", "content": METIS_SYSTEM_PROMPT},
            *self._history,
        ]

    def _post(self, stream: bool) -> requests.Response:
        """Single place for the LLM POST — keeps ask() and ask_stream() DRY."""
        return requests.post(
            LLM_URL,
            json={
                "model":       LLM_MODEL,
                "messages":    self._build_messages(),
                "tools":       TOOLS,
                "stream":      stream,
                "temperature": TEMPERATURE,
            },
            timeout=LLM_TIMEOUT,
            stream=stream,
        )

    def _resolve_tool_calls(self, msg: dict) -> None:
        """
        Consume all tool calls in msg, dispatch each, append results to
        history. Mutates self._history in place. Does not re-request the LLM —
        callers handle that loop themselves.
        """
        self._history.append(msg)
        for call in msg.get("tool_calls", []):
            tool_name = call["function"]["name"]
            result    = dispatch(tool_name)
            log.info(f"Tool: {tool_name!r} → {result}")
            self._history.append({
                "role":    "tool",
                "content": json.dumps(result),
            })

    # ── Blocking interface ─────────────────────────────────────────────────────

    def ask(self, user_text: str) -> str:
        """
        Send user_text to the LLM, wait for full response.
        Resolves any tool calls transparently before returning.
        Returns plain string. Never raises.
        """
        if not user_text.strip():
            return FALLBACK_NO_AUDIO

        self._history.append({"role": "user", "content": user_text})

        try:
            resp = self._post(stream=False)
        except requests.Timeout:
            log.error(f"LLM timeout after {LLM_TIMEOUT}s.")
            self._history.pop()
            return FALLBACK_TIMEOUT
        except requests.ConnectionError:
            log.error("LLM connection refused. Is llama-server running?")
            self._history.pop()
            return FALLBACK_OFFLINE
        except Exception as e:
            log.error(f"LLM request failed: {e}")
            self._history.pop()
            return FALLBACK_ERROR

        try:
            while True:
                data = resp.json()
                msg  = data["choices"][0]["message"]

                if not msg.get("tool_calls"):
                    break

                self._resolve_tool_calls(msg)

                try:
                    resp = self._post(stream=False)
                except Exception as e:
                    log.error(f"LLM request failed after tool call: {e}")
                    self._history.pop()
                    return FALLBACK_ERROR

            reply = msg.get("content", "").strip()

        except Exception as e:
            log.error(f"Parse error: {e} | raw: {resp.text[:200]}")
            self._history.pop()
            return FALLBACK_ERROR

        if not reply:
            log.warning("LLM returned empty content.")
            self._history.pop()
            return FALLBACK_ERROR

        self._history.append({"role": "assistant", "content": reply})
        self._log_turn(user_text, reply)
        return reply

    # ── Streaming interface ────────────────────────────────────────────────────

    def ask_stream(self, user_text: str):
        """
        Generator that yields complete sentences for sentence-at-a-time TTS.

        Usage:
            for sentence in brain.ask_stream("hello"):
                speak(sentence)

        On failure, yields a single fallback string.
        The full response is stored in history automatically.

        Tool calls are resolved via a non-streaming request first. Once all
        tool calls are resolved, the final reply is split into sentences and
        yielded. This avoids reassembling fragmented tool_calls deltas from
        the OpenAI SSE stream.
        """
        if not user_text.strip():
            yield FALLBACK_NO_AUDIO
            return

        self._history.append({"role": "user", "content": user_text})

        # Resolve tool calls (non-streaming)
        try:
            resp = self._post(stream=False)
        except requests.Timeout:
            log.error(f"LLM timeout after {LLM_TIMEOUT}s.")
            self._history.pop()
            yield FALLBACK_TIMEOUT
            return
        except requests.ConnectionError:
            log.error("LLM connection refused. Is llama-server running?")
            self._history.pop()
            yield FALLBACK_OFFLINE
            return
        except Exception as e:
            log.error(f"LLM request failed: {e}")
            self._history.pop()
            yield FALLBACK_ERROR
            return

        try:
            while True:
                data = resp.json()
                msg  = data["choices"][0]["message"]

                if not msg.get("tool_calls"):
                    break

                self._resolve_tool_calls(msg)
                log.info("Tool resolved — re-requesting.")

                try:
                    resp = self._post(stream=False)
                except Exception as e:
                    log.error(f"LLM request failed after tool call: {e}")
                    self._history.pop()
                    yield FALLBACK_ERROR
                    return

        except Exception as e:
            log.error(f"Parse error: {e}")
            self._history.pop()
            yield FALLBACK_ERROR
            return

        reply = msg.get("content", "").strip()

        if not reply:
            log.warning("LLM returned empty content.")
            self._history.pop()
            yield FALLBACK_ERROR
            return

        self._history.append({"role": "assistant", "content": reply})
        self._log_turn(user_text, reply)

        # Yield sentence by sentence for TTS
        parts = _SENTENCE_END.split(reply)
        for part in parts:
            sentence = part.strip()
            if sentence:
                yield sentence

    # ── Internals ──────────────────────────────────────────────────────────────

    def _log_turn(self, user_text: str, reply: str):
        log.info(
            f"[turn {len(self._history) // 2}/{CONVERSATION_MAX_TURNS}] "
            f"Q: {user_text[:60]!r} → A: {reply[:60]!r}..."
        )

    def reset(self):
        """Wipe conversation history."""
        self._history.clear()
        log.info("Conversation history cleared.")

    @property
    def turn_count(self) -> int:
        return len(self._history) // 2
