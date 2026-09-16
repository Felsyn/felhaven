"""
pythia.py — The Oracle (LLM brain)
===================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Answer a question by talking to the local LLM, using toolbox
             tools for live data. Runs gemma4:e2b via Ollama, lets the model
             call the tools, and returns the final text. Nothing else — no
             tkinter, no threads, no persistence. The panel owns the UI and
             the background thread; Pythia is a pure request/response function.

Contract:    ask(message, history=None, on_delta=None, on_event=None,
                 cancel=None) -> str
                 Runs the Ollama tool-calling loop and returns the model's
                 final answer as plain text. Degrades gracefully — on any
                 network/LLM failure it returns a short human-readable string
                 instead of raising, so the UI thread can print it verbatim.
                 on_event fires once at the end with a stats payload (tokens,
                 wall time, tool call/failure counts, tool_records — one
                 diagnostic record per tool invocation — plus turn_messages,
                 the turn's full message exchange, tool calls and results
                 included, for the caller to persist as history); cancel is a
                 threading.Event checked mid-stream AND between tool calls for
                 cooperative Stop.
             TOOLS / _DISPATCH are built once, at import, by reading each tool
             module's own TOOL_DEFINITION — so the registry can never drift
             out of sync with the handlers. The system prompt itself lives in
             machine_spirit.py (effective_prompt(), read fresh each call) —
             Pythia owns no prompt text of its own.

             prewarm() -> threading.Thread / is_warming() -> bool
                 Startup preload, called once by felhaven.py. prewarm() loads
                 the model AND prefills the system+tools prefix on a background
                 thread, so the first real question pays neither; is_warming()
                 is the panel's cue to label that window. Best-effort — silent
                 if Ollama is down.

Regime:      Felhaven runs with metis_toolbox/ on sys.path, so this imports
             the tools top-level (`from tools import ...`), the same way
             felhaven.py and the panels do. This reflection-built registry is
             now the ONLY tool registry; the old voice-side dispatcher in
             metis_toolbox/__init__.py was retired with voice input.

Endpoint:    Reads OLLAMA_HOST (default 127.0.0.1:11435 — the standalone
             Ollama, models on D:). Model from PYTHIA_MODEL (default
             gemma4:e2b). No API key: it's all local.

Upstream:    panels/home_panel.py (calls ask() on a worker thread)
Downstream:  tools/{horai,hephaestus,aura,ammit,midas,aether,pheme,zeno,
             eudoxus,argus,helios,hypatia,selene,morpheus}.py — each
             contributes TOOL_DEFINITION + handle(). tools/callimachus.py and
             tools/herodotus.py are MULTI-TOOL modules (TOOL_DEFINITIONS +
             one function per tool). Pythia is their SOLE registry consumer —
             the typed chat is the only path any tool is called from now that
             the voice-side registry is retired.

Requires:    requests (already in the Felhaven stack). Stdlib otherwise.

V0.02:       Audited 2026-07-24 — the dispatcher's own error returns normalized
             to §2 slugs (unknown_tool / tool_failed), and no caught exception's
             raw message now reaches the LLM context or the transcript (only its
             class name does); "never raises" contract verified, tests added.
"""

import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, Callable, Optional

import requests

import machine_spirit
from tools import (
    horai, hephaestus, aura, ammit, midas, aether, pheme, zeno, eudoxus,
    argus, helios, hypatia, selene, morpheus, callimachus, herodotus,
)

log = logging.getLogger("METIS.pythia")

# ── Tool registry — derived from the modules so it can't drift ────────────────
# Most modules expose TOOL_DEFINITION (the function schema sent to the model) +
# handle() (the code the call routes to). Callimachus is the first MULTI-TOOL
# module: it exposes TOOL_DEFINITIONS (a list) and one like-named function per
# tool name. Either way we read the name straight out of each schema, so TOOLS
# and _DISPATCH stay in lockstep with the handlers.

_TOOL_MODULES = [
    horai, hephaestus, aura, ammit, midas, aether, pheme, zeno, eudoxus,
    argus, helios, hypatia, selene, morpheus, callimachus, herodotus,
]


def _module_tools(
    module: Any,
) -> list[tuple[dict[str, Any], Callable[..., dict[str, Any]]]]:
    """One module's (definition, handler) pairs. Single-tool → TOOL_DEFINITION
    + handle(); multi-tool → TOOL_DEFINITIONS (a list) with one module function
    named exactly for each tool it advertises."""
    defs = getattr(module, "TOOL_DEFINITIONS", None)
    if defs is None:
        return [(module.TOOL_DEFINITION, module.handle)]
    return [(d, getattr(module, d["function"]["name"])) for d in defs]


TOOLS: list[dict[str, Any]] = []
_DISPATCH: dict[str, Callable[..., dict[str, Any]]] = {}
for _mod in _TOOL_MODULES:
    for _definition, _handler in _module_tools(_mod):
        TOOLS.append(_definition)
        _DISPATCH[_definition["function"]["name"]] = _handler

# ── Config ────────────────────────────────────────────────────────────────────

_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "127.0.0.1:11435")
# OLLAMA_HOST is host:port with no scheme; tolerate a stray scheme just in case.
_BASE_URL = "http://" + _OLLAMA_HOST.replace("http://", "").replace("https://", "")
_CHAT_URL = _BASE_URL + "/api/chat"

_MODEL = os.environ.get("PYTHIA_MODEL", "gemma4:e2b")

# First call cold-loads the model (~100s on this box); later calls are quick.
_REQUEST_TIMEOUT = 180          # seconds
_MAX_TOOL_ROUNDS = 5            # cap tool→answer loops so a confused model can't spin
# gemma4:e2b sometimes ends its turn with nothing at all — no tool call, empty
# content — typically when the tool result it just received doesn't actually
# answer the question (it fetched the time but needs sunset, say). Retrying the
# IDENTICAL request does not help: measured 8/8 blank on a context that blanks,
# because nothing about the request changed. The retry only works if it carries
# a nudge asking the model to answer or name what's missing — measured 0/8 blank
# with one. One retry, bounded by the _MAX_TOOL_ROUNDS budget so it can't spin.
_MAX_BLANK_RETRIES = 1
_BLANK_NUDGE = ("Answer the user's question now, using the tool results above. "
                "If they are not enough, say plainly what information is missing.")
_NUM_CTX = 8192                 # context window (tokens) — starting point for Obelisk's RAM
_NUM_PREDICT = 1024             # max output tokens per round trip — same
# keep_alive=-1 pins gemma4:e2b resident in Ollama's memory instead of letting it
# unload after ~5 min idle — otherwise an occasional query pays the full ~100s
# cold reload. The model stays loaded until Ollama restarts.
_KEEP_ALIVE = -1
# EVERY request to /api/chat must carry this SAME dict. Ollama keys a resident
# model on its runtime options, so a request whose num_ctx differs from the
# loaded runner's forces a full unload+reload — ~70s on this box. That is
# precisely what the prewarm used to cause: it sent no options at all, loaded a
# runner at Ollama's default context, and then the first real question threw
# that runner away and reloaded at 8192. Both _chat() and _warm() read this one
# dict so the two can never drift apart again; the lockstep test enforces it.
_OPTIONS = {"num_ctx": _NUM_CTX, "num_predict": _NUM_PREDICT}

# ── Rite records — per-tool-invocation diagnostics for the Rite Log ───────────
# One record per tool call, handed to the UI in the stats payload. Pythia builds
# them because it is the only place that sees a call, its arguments and its
# result together. Nothing here imports cerberus: the diagnostic system must
# never need to unlock the vault in order to report that the vault is locked.
_PREVIEW_CAP = 1000             # chars — the cap on a stored arguments/result string
_RITE_CANCELLED = "cancelled: turn stopped before this rite ran"

# Value-scrubbing for text an operator will read. Deterministic patterns, not
# a secrets lookup — see the note above. The KEY is kept and only the VALUE
# is replaced, so the reader can still see *which* credential was in play.
_REDACT_PATTERNS = (
    # Query parameters: the value runs to the next &, whitespace, quote, or end.
    (re.compile(r"\b((?:api[_-]?)?key|(?:access[_-]?)?token|auth)=[^&\s\"']*",
                re.IGNORECASE), r"\1=[REDACTED]"),
    # Authorization header: the value runs to a newline, a quote, or the end.
    (re.compile(r"(authorization:\s*bearer\s+)[^\r\n\"']+",
                re.IGNORECASE), r"\1[REDACTED]"),
)


def _ms(start: float, end: Optional[float] = None) -> str:
    """Elapsed milliseconds as a log-friendly string."""
    return f"{((end if end is not None else time.perf_counter()) - start) * 1000:.0f}ms"


# ── Internals ─────────────────────────────────────────────────────────────────

def _now() -> datetime:
    """Injectable clock seam — tests patch this to a fixed datetime so a rite's
    started_at is exactly assertable rather than shape-matched.

    Naive LOCAL time, deliberately. Felhaven is a single-machine, local-first
    system and the diagnostic question is "what happened on my machine, when" —
    a reader comparing a rite against their own memory of the afternoon wants
    the clock on the wall. UTC earns its ugliness only once records are
    aggregated across machines, which these never are. Do not "fix" this.
    """
    return datetime.now()


def _redact(text: str) -> str:
    """Scrub credential VALUES out of operator-facing text (§5).

    Applied to a rite's error and result_preview only — never to its arguments,
    which are what the operator is actually investigating.
    """
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _truncate(text: str) -> tuple[str, bool]:
    """Cap operator-facing text at _PREVIEW_CAP, and say whether it was cut.

    A hard slice with NO ellipsis and no "[truncated]" marker: the flag carries
    that signal and the panel renders it in its own style. Writing a display
    artifact into the data is what makes a flag pointless.
    """
    if len(text) <= _PREVIEW_CAP:
        return text, False
    return text[:_PREVIEW_CAP], True


def _argument_field(arguments: dict[str, Any]) -> tuple[Any, bool]:
    """A rite's arguments field: the dict itself when it serializes under the
    cap, else the truncated JSON string. The type IS the truncation signal's
    companion — arguments_truncated tells the panel which of the two to expect.

    Deliberately NOT redacted (§5): model-authored arguments are precisely what
    the operator opened the Rite Log to read.
    """
    serialized = json.dumps(arguments, default=str)
    if len(serialized) <= _PREVIEW_CAP:
        return arguments, False
    return serialized[:_PREVIEW_CAP], True


def _result_preview(result: dict[str, Any]) -> tuple[str, bool]:
    """A successful result as the operator will read it. Order is load-bearing:
    serialize, THEN redact, THEN truncate — so the cap applies to exactly the
    string that gets stored, and a redaction can never expand past it."""
    return _truncate(_redact(json.dumps(result, default=str)))


def _error_text(result: dict[str, Any]) -> str:
    """A failing result's error identifier, for the rite record.

    Morpheus carries the human half of its error in a separate "detail" key
    rather than after a colon; folding it in normalizes that into the §2
    slug-plus-detail shape the panel already parses, instead of showing the
    operator a bare slug and dropping the sentence that explains it.
    """
    slug = str(result.get("error", ""))
    detail = result.get("detail")
    if ": " not in slug and isinstance(detail, str) and detail.strip():
        slug = f"{slug}: {detail.strip()}"
    return _redact(slug)



def _dispatch(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Route one tool call to its handler: forward the model's arguments as
    kwargs (call with none if it gave none) and turn any handler failure into
    an error dict so the model can recover rather than the whole turn crashing.
    The error identifier is a §2 slug: the caught exception's *message* is kept
    out of the returned dict (it feeds straight into the LLM context) — only
    its class name travels, the raw text stays in the log."""
    fn = _DISPATCH.get(tool_name)
    if fn is None:
        return {"error": f"unknown_tool: {tool_name!r}"}
    try:
        return fn(**arguments) if arguments else fn()
    except Exception as e:                       # a tool bug must not kill the chat
        log.error(f"Pythia: tool {tool_name!r} raised: {type(e).__name__}: {e}")
        return {"error": f"tool_failed: {tool_name!r} raised {type(e).__name__}"}


def _chat(
    messages: list[dict[str, Any]],
    on_delta: Optional[Callable[[str], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    """One STREAMING round trip to Ollama's /api/chat. Reads the newline-delimited
    JSON stream, calls on_delta(piece) for each content token as it arrives, and
    returns (assembled_message, round_stats). assembled_message carries `content`
    plus any `tool_calls`; round_stats carries this round's prompt_tokens/
    output_tokens/eval_ms/load_ms/prompt_ms (0 for any count Ollama's `done`
    chunk omits) plus thinking_chars. Raises on HTTP/connection error — the caller in ask()
    converts that to a friendly string. Logs time-to-first-token and total
    round-trip time.

    A chunk's `message` can also carry `thinking` — the model's reasoning trace,
    which is NOT an answer. It is deliberately dropped: never streamed to
    on_delta, never concatenated into `content` (raw reasoning in the user's
    transcript is bad UX), and never carried back into the next round's context.
    Only its length survives, as round_stats["thinking_chars"], because a round
    that is all thinking and no content otherwise looks like an unexplained
    blank turn to ask() and to whoever is reading the log.

    If `cancel` is set mid-stream, the read loop breaks immediately: exiting the
    `with requests.post(...)` block closes the connection, which makes Ollama
    halt generation. Whatever content/tool_calls arrived so far are returned —
    this never raises on cancellation."""
    payload = {
        "model": _MODEL,
        "messages": messages,
        "tools": TOOLS,
        "stream": True,
        # Skip gemma4's reasoning trace — we want the answer. If a round returns
        # `thinking` anyway (this flipped, or a model that thinks regardless),
        # it is dropped, not shown; see the docstring.
        "think": False,
        "keep_alive": _KEEP_ALIVE,
        "options": dict(_OPTIONS),
    }
    content_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    thinking_chars = 0
    role = "assistant"
    t0 = time.perf_counter()
    first_token_at: Optional[float] = None
    stats = {"prompt_tokens": 0, "output_tokens": 0, "eval_ms": 0,
             "thinking_chars": 0, "load_ms": 0, "prompt_ms": 0}

    with requests.post(
        _CHAT_URL, json=payload, stream=True, timeout=_REQUEST_TIMEOUT
    ) as resp:
        resp.raise_for_status()
        for raw in resp.iter_lines():
            if cancel is not None and cancel.is_set():
                break
            if not raw:
                continue
            data: dict[str, Any] = json.loads(raw)
            message: dict[str, Any] = data.get("message") or {}
            if message.get("role"):
                role = message["role"]
            piece = message.get("content") or ""
            if piece:
                if first_token_at is None:
                    first_token_at = time.perf_counter()
                content_parts.append(piece)
                if on_delta is not None:
                    on_delta(piece)
            # Counted, never kept — see the `thinking` paragraph above.
            thinking_chars += len(message.get("thinking") or "")
            calls = message.get("tool_calls")
            if calls:
                tool_calls.extend(calls)
            if data.get("done"):
                stats["prompt_tokens"] = int(data.get("prompt_eval_count") or 0)
                stats["output_tokens"] = int(data.get("eval_count") or 0)
                stats["eval_ms"] = int((data.get("eval_duration") or 0) / 1_000_000)
                # load_ms is THE cold-start diagnostic: Ollama reports it nonzero
                # only when it had to (re)load the model for this very request —
                # i.e. the prewarm missed and we are paying the load in the
                # user's face. prompt_ms is the prefill of system prompt + tool
                # schemas, which stays small while the warm's prefix is cached.
                stats["load_ms"] = int((data.get("load_duration") or 0) / 1_000_000)
                stats["prompt_ms"] = int(
                    (data.get("prompt_eval_duration") or 0) / 1_000_000)
                ttft = _ms(t0, first_token_at) if first_token_at else "n/a"
                log.info(
                    f"Pythia: round done_reason={data.get('done_reason')} "
                    f"ttft={ttft} round={_ms(t0)} "
                    f"load={stats['load_ms']}ms prompt={stats['prompt_ms']}ms"
                )

    stats["thinking_chars"] = thinking_chars
    content = "".join(content_parts)
    # All reasoning, no answer, no tool call — the round is blank to everyone
    # downstream. Say so here, or it looks like the model returned nothing at
    # all. (A tool round with empty content is normal, hence the tool_calls
    # guard.) Reached today only if someone flips `think` above, or a future
    # PYTHIA_MODEL thinks anyway.
    if thinking_chars and not content.strip() and not tool_calls:
        log.warning(
            f"Pythia: round returned no content and {thinking_chars} chars of "
            f"`thinking` (request think={payload['think']}). Reasoning is dropped "
            f"by design, so this round counts as an empty reply."
        )

    assembled: dict[str, Any] = {"role": role, "content": content}
    if tool_calls:
        assembled["tool_calls"] = tool_calls
    return assembled, stats


# ── Contract ──────────────────────────────────────────────────────────────────

def ask(
    message: str,
    history: Optional[list[dict[str, Any]]] = None,
    on_delta: Optional[Callable[[str], None]] = None,
    on_event: Optional[Callable[[dict[str, Any]], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> str:
    """
    Answer `message`, using tools as needed. `history` is prior turns —
    each turn is the flattened list of messages from a prior ask() call's
    `turn_messages` (see on_event below): a user message, any assistant
    replies carrying `tool_calls`, the matching `tool` result messages, and
    the final assistant answer. Plain {"role": "user"|"assistant", "content":
    str} dicts also work (a caller that hasn't kept tool messages degrades
    gracefully). Pass the running conversation to keep context. Returns the
    final answer text.

    If `on_delta` is given, it's called with each content token as the model
    streams it (so the UI can render the answer live). It fires on every round,
    including tool rounds — those usually stream no content. `on_delta` runs on
    the caller's (worker) thread, so it must marshal to the GUI thread itself.

    If `on_event` is given, it's called exactly once, right before returning,
    with a {"type": "stats", ...} payload summing prompt/output tokens and eval
    time across every round (a tool-using answer runs multiple rounds), plus
    tool call/failure counts, whether `cancel` fired, and `turn_messages` — this
    call's own messages (the new user message, every assistant/tool message the
    tool-calling loop produced, and the final assistant reply), EXCLUDING the
    system prompt and whatever was passed in as `history`. A caller persisting
    conversation state should store `turn_messages` as one turn (not just
    message.content) — that keeps the model's own transcript showing real
    tool_call → result → answer exchanges instead of flattened prose, which is
    what a small model reliably imitates on the next call. Runs on the caller's
    (worker) thread like `on_delta`.

    The payload also carries `tool_records`: one dict per tool invocation, in
    call order, with the call's arguments, status (ok / failed / not_run), start
    time, duration, error slug and a capped result preview. The counts above are
    derived from it. This is a DIAGNOSTIC record for the operator, not context
    for the model — nothing here is fed back to the LLM.

    If `cancel` is given and gets set mid-answer, the in-flight round stops
    promptly (closing the connection halts Ollama) and ask() returns whatever
    text has streamed so far. Tools stop too: a call the user cancelled before
    it was dispatched is recorded not_run rather than run anyway. Never raises
    for this either.

    Never raises: network/LLM problems come back as a short readable string so
    the panel can print the result straight to the transcript. An empty reply
    (no content, no tool call — including a round that produced only a dropped
    `thinking` trace, which the log names as such) is retried once, carrying a
    nudge to answer or name what's missing — a bare re-ask of the same context
    reproduces the same silence, so the nudge is the part that works. Neither
    the empty turn nor the nudge survives into `turn_messages`. Only if that
    retry is also silent does it degrade to a readable string. The return value
    is never empty and never a bare placeholder.
    """
    t_start = time.perf_counter()
    system_prompt = machine_spirit.effective_prompt()
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    n_preamble = len(messages)                   # system + prior history — not this turn's own
    messages.append({"role": "user", "content": message})

    prompt_tokens = 0
    output_tokens = 0
    eval_ms = 0
    # One rite record per tool invocation, oldest first, spanning the whole turn.
    # The derived counts below are read off this list rather than kept alongside
    # it, so a record and its count can never disagree.
    records: list[dict[str, Any]] = []
    rite_seq = 0

    def _emit(answer_text: str = "") -> str:
        if on_event is not None:
            ran = [r for r in records if r["status"] != "not_run"]
            failed = [r for r in records if r["status"] == "failed"]
            on_event({
                "type": "stats",
                "prompt_tokens": prompt_tokens,
                "output_tokens": output_tokens,
                "wall_ms": int((time.perf_counter() - t_start) * 1000),
                "eval_ms": eval_ms,
                "tools_called": len(ran),
                "tools_failed": len(failed),
                "failed_tools": [r["name"] for r in failed],
                # Full per-invocation diagnostics for the Rite Log. Consumers
                # read this defensively — a stats dict without the key means no
                # records are available, not that something broke.
                "tool_records": records,
                "cancelled": cancel is not None and cancel.is_set(),
                # This turn's own messages only (user + every assistant/tool
                # message the loop produced), for the caller to persist as one
                # history turn — see the on_event paragraph in ask()'s docstring.
                "turn_messages": messages[n_preamble:],
            })
        return answer_text

    blank_rounds = 0
    nudge_pending = False

    try:
        for round_i in range(_MAX_TOOL_ROUNDS):
            try:
                reply, stats = _chat(messages, on_delta=on_delta, cancel=cancel)
            finally:
                # Drop the nudge the moment its round is over — including a round
                # that raised, whose handler below still persists turn_messages.
                # The caller stores those as history, and a user message the user
                # never typed does not belong there.
                if nudge_pending:
                    messages.pop()
                    nudge_pending = False
            prompt_tokens += stats["prompt_tokens"]
            output_tokens += stats["output_tokens"]
            eval_ms += stats["eval_ms"]
            messages.append(reply)                      # keep the model's turn in context

            if cancel is not None and cancel.is_set():
                log.info(f"Pythia: ask() cancelled {_ms(t_start)}")
                return _emit((reply.get("content") or "").strip())

            tool_calls = reply.get("tool_calls") or []
            if not tool_calls:
                answer = (reply.get("content") or "").strip()
                if answer:
                    log.info(f"Pythia: ask() total {_ms(t_start)}")
                    return _emit(answer)
                # The model ended its turn with nothing to say. Drop that empty
                # turn before retrying: left in place it would both prime the
                # retry with its own silence and — because the caller persists
                # turn_messages as history — teach the model that a blank reply
                # is a valid turn, which is the same transcript-imitation trap
                # documented in home_panel.py.
                messages.pop()
                blank_rounds += 1
                # A round can be empty because the model said nothing at all, or
                # because it spent the round reasoning (`thinking`), which we
                # drop. Same retry either way, but the log should say which.
                thought = stats.get("thinking_chars", 0)
                why = f" (round was {thought} chars of thinking)" if thought else ""
                if (blank_rounds <= _MAX_BLANK_RETRIES
                        and round_i < _MAX_TOOL_ROUNDS - 1):
                    log.warning(
                        f"Pythia: empty reply{why}, retrying with nudge "
                        f"({blank_rounds}/{_MAX_BLANK_RETRIES})"
                    )
                    messages.append({"role": "user", "content": _BLANK_NUDGE})
                    nudge_pending = True
                    continue
                log.warning(
                    f"Pythia: still empty after {blank_rounds} round(s){why}"
                )
                silent = "The oracle answered but said nothing. Try asking again."
                messages.append({"role": "assistant", "content": silent})
                return _emit(silent)

            # Run each requested tool, feed the JSON result back as a tool turn,
            # and record one rite per invocation for the Rite Log.
            for call in tool_calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments") or {}
                rite_seq += 1
                arguments, arguments_truncated = _argument_field(args)
                record: dict[str, Any] = {
                    "seq": rite_seq,
                    "round": round_i + 1,
                    "name": name,
                    "arguments": arguments,
                    "arguments_truncated": arguments_truncated,
                    "status": "not_run",
                    "started_at": _now().isoformat(timespec="milliseconds"),
                    "duration_ms": None,
                    "result_preview": None,
                    "result_truncated": False,
                }
                # "error" is set by subscript rather than written into the literal
                # above on purpose: tests/test_tool_output_contract.py inspects
                # every {"error": ...} literal in this file and requires a slug,
                # which is the right rule for a value the LLM reads and the wrong
                # one for a diagnostic copy of it. See CONVENTIONS §12.
                record["error"] = None
                records.append(record)

                # Stop halts the tools too, not just the model. A call the user
                # cancelled is recorded as requested-and-deliberately-not-run —
                # a real event — rather than quietly run anyway or omitted.
                if cancel is not None and cancel.is_set():
                    record["error"] = _RITE_CANCELLED
                    continue

                t_call = time.perf_counter()
                result = _dispatch(name, args)
                record["duration_ms"] = int((time.perf_counter() - t_call) * 1000)
                if "error" in result:
                    record["status"] = "failed"
                    record["error"] = _error_text(result)
                else:
                    record["status"] = "ok"
                    preview, truncated = _result_preview(result)
                    record["result_preview"] = preview
                    record["result_truncated"] = truncated
                messages.append({
                    "role": "tool",
                    "tool_name": name,
                    "content": json.dumps(result),
                })

            # Cancelled partway through the round: every remaining call is now
            # recorded not_run, so end the turn here. Falling through would open
            # another _chat connection only to break out of its first line.
            if cancel is not None and cancel.is_set():
                log.info(f"Pythia: ask() cancelled mid-round {_ms(t_start)}")
                return _emit((reply.get("content") or "").strip())
        # Ran out of rounds still wanting tools — hand back whatever text we have.
        stuck = "The oracle got stuck consulting its tools. Try rephrasing."
        messages.append({"role": "assistant", "content": stuck})
        return _emit(stuck)
    except requests.Timeout:
        log.warning("Pythia: Ollama request timed out.")
        timed_out = "The oracle is slow to answer (LLM timed out). Is the model still loading?"
        messages.append({"role": "assistant", "content": timed_out})
        return _emit(timed_out)
    except requests.ConnectionError:
        log.warning("Pythia: could not reach Ollama.")
        unreachable = f"The oracle is unreachable — no Ollama server at {_BASE_URL}."
        messages.append({"role": "assistant", "content": unreachable})
        return _emit(unreachable)
    except Exception as e:
        log.error(f"Pythia: unexpected failure: {type(e).__name__}: {e}")
        # Only the class name reaches the transcript — the raw exception text
        # could carry a path or key onto the screen (§2). The message is logged.
        faltered = f"The oracle faltered ({type(e).__name__})."
        messages.append({"role": "assistant", "content": faltered})
        return _emit(faltered)


# ── Preload ─────────────────────────────────────────────────────────────────

# Set while the prewarm thread is in flight, cleared when it finishes — success
# OR failure, so a downed Ollama can never leave a caller waiting forever. The
# panel reads it through is_warming() to label the startup window. This is a
# worker→main shared object, which §6 otherwise reserves for the result queue;
# it is allowed here for the same reason ask()'s `cancel` crosses that boundary
# the other way — an Event is atomic, carries no payload, and needs no drain.
_WARMING = threading.Event()

# The warm's own generation cap. num_predict is a SAMPLING parameter, not a
# runtime one, so overriding it does not change which runner Ollama loads — the
# warm still lands on exactly the runner _chat() will ask for. One token rather
# than zero: it proves the prefill actually ran, without leaning on llama.cpp's
# n_predict=0 edge behaviour.
_WARM_NUM_PREDICT = 1
# A throwaway turn whose only job is to make Ollama process the prefix.
_WARM_PROBE = "ping"


def is_warming() -> bool:
    """True from prewarm() until the warm finishes. The UI's cue that the oracle
    is loading; False both when she is ready and when warming was skipped."""
    return _WARMING.is_set()


def _warm() -> None:
    """Load the model AND prefill the prompt, so the first real question pays
    neither.

    Two distinct costs hide behind "cold start", and warming has to kill both:

    1. The model load. Ollama keys a resident runner on its runtime options, so
       this MUST send the same `options` _chat() sends — hence the shared
       _OPTIONS. Warming without them loads a runner at Ollama's default
       context which the first real question then discards and reloads, costing
       the full ~70s anyway. That was the old bug: the warm "succeeded" in 1.4s
       and the question 83 seconds later still paid 71s.

    2. The prefill. `messages: []` is load-only, so it left the ~2,700 tokens of
       system prompt + tool schemas to be processed on first use (7-25s
       measured). Sending the real prefix instead puts it in llama.cpp's prompt
       cache, which matches on longest common prefix — so a real question with a
       different user message still reuses the system+tools portion.

    If the operator edits the Machine Spirit prompt between warm and first
    question the prefix simply misses and the cost falls back to what it was
    before: no breakage, no saving.

    Best-effort: swallows EVERYTHING. If Ollama isn't running at launch, warming
    is skipped silently and the first real question pays the cold load as it
    always did."""
    try:
        t0 = time.perf_counter()
        resp = requests.post(
            _CHAT_URL,
            json={
                "model": _MODEL,
                # The same prefix a real turn opens with — see docstring (2).
                "messages": [
                    {"role": "system", "content": machine_spirit.effective_prompt()},
                    {"role": "user", "content": _WARM_PROBE},
                ],
                "tools": TOOLS,
                "think": False,
                "keep_alive": _KEEP_ALIVE,
                # Same runner as _chat() will want — see docstring (1).
                "options": dict(_OPTIONS, num_predict=_WARM_NUM_PREDICT),
                "stream": False,
            },
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        # Ollama's own split of the bill, so a slow warm can be read rather than
        # guessed at: load= the weights, prompt= the prefix we just cached.
        body = resp.json() if resp.content else {}
        load_ms = int((body.get("load_duration") or 0) / 1_000_000)
        prompt_ms = int((body.get("prompt_eval_duration") or 0) / 1_000_000)
        log.info(
            f"Pythia: model warmed in {_ms(t0)} "
            f"(load={load_ms}ms prompt={prompt_ms}ms "
            f"prefix={body.get('prompt_eval_count') or 0} tokens)"
        )
    except Exception as e:  # noqa: BLE001 — best-effort; Ollama may be down at launch
        log.info(f"Pythia: prewarm skipped ({type(e).__name__}: {e})")
    finally:
        # Cleared on BOTH paths: a failed warm must not strand the UI on
        # "waking the oracle…" forever.
        _WARMING.clear()


def prewarm() -> threading.Thread:
    """Preload the model into Ollama on a background thread at startup so the
    first real question doesn't pay the ~70s cold load. Never raises and does
    nothing visible if Ollama is down. Returns the thread (mainly for tests).

    The flag is set HERE, not in _warm(), so is_warming() is already true by the
    time this returns — otherwise a panel polling immediately after launch could
    read False before the thread has been scheduled."""
    _WARMING.set()
    t = threading.Thread(target=_warm, name="pythia-prewarm", daemon=True)
    t.start()
    return t


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"[Pythia] endpoint={_CHAT_URL} model={_MODEL}")
    print(f"[Pythia] {len(TOOLS)} tools: {', '.join(_DISPATCH)}")
    q = "What's the weather right now, and what time is it?"
    print(f"[you]    {q}")
    print(f"[pythia] {ask(q)}")
