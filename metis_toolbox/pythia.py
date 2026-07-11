"""
pythia.py — The Oracle (LLM brain)
===================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Answer a question by talking to the local LLM (gemma4:e2b via
             Ollama), letting it call the toolbox's tools when it needs live
             data, and return the final text answer. Nothing else — no
             tkinter, no threads, no persistence. The panel owns the UI and
             the background thread; Pythia is a pure request/response function.

Contract:    ask(message, history=None, on_delta=None, on_event=None,
                 cancel=None) -> str
                 Runs the Ollama tool-calling loop and returns the model's
                 final answer as plain text. Degrades gracefully — on any
                 network/LLM failure it returns a short human-readable string
                 instead of raising, so the UI thread can print it verbatim.
                 on_event fires once at the end with a stats payload (tokens,
                 wall time, tool call/failure counts); cancel is a
                 threading.Event checked mid-stream for cooperative Stop.
             TOOLS / _DISPATCH are built once, at import, by reading each tool
             module's own TOOL_DEFINITION — so the registry can never drift
             out of sync with the handlers. The system prompt itself lives in
             machine_spirit.py (effective_prompt(), read fresh each call) —
             Pythia owns no prompt text of its own.

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
"""

import json
import logging
import os
import threading
import time
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
_NUM_CTX = 8192                 # context window (tokens) — starting point for Obelisk's RAM
_NUM_PREDICT = 1024             # max output tokens per round trip — same
# keep_alive=-1 pins gemma4:e2b resident in Ollama's memory instead of letting it
# unload after ~5 min idle — otherwise an occasional query pays the full ~100s
# cold reload. The model stays loaded until Ollama restarts.
_KEEP_ALIVE = -1


def _ms(start: float, end: Optional[float] = None) -> str:
    """Elapsed milliseconds as a log-friendly string."""
    return f"{((end if end is not None else time.perf_counter()) - start) * 1000:.0f}ms"


# ── Internals ─────────────────────────────────────────────────────────────────

def _dispatch(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Route one tool call to its handler. Mirrors the toolbox dispatcher:
    forwards arguments as kwargs, calls with none if the model gave none, and
    turns any handler failure into an error dict so the model can recover
    rather than the whole turn crashing."""
    fn = _DISPATCH.get(tool_name)
    if fn is None:
        return {"error": f"Unknown tool: {tool_name!r}"}
    try:
        return fn(**arguments) if arguments else fn()
    except Exception as e:                       # a tool bug must not kill the chat
        log.error(f"Pythia: tool {tool_name!r} raised: {e}")
        return {"error": f"Tool {tool_name!r} failed: {e}"}


def _chat(
    messages: list[dict[str, Any]],
    on_delta: Optional[Callable[[str], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    """One STREAMING round trip to Ollama's /api/chat. Reads the newline-delimited
    JSON stream, calls on_delta(piece) for each content token as it arrives, and
    returns (assembled_message, round_stats). assembled_message carries `content`
    plus any `tool_calls`; round_stats carries this round's prompt_tokens/
    output_tokens/eval_ms (0 for any count Ollama's `done` chunk omits). Raises
    on HTTP/connection error — the caller in ask() converts that to a friendly
    string. Logs time-to-first-token and total round-trip time.

    If `cancel` is set mid-stream, the read loop breaks immediately: exiting the
    `with requests.post(...)` block closes the connection, which makes Ollama
    halt generation. Whatever content/tool_calls arrived so far are returned —
    this never raises on cancellation."""
    payload = {
        "model": _MODEL,
        "messages": messages,
        "tools": TOOLS,
        "stream": True,
        "think": False,     # skip gemma4's reasoning trace — we want the answer
        "keep_alive": _KEEP_ALIVE,
        "options": {"num_ctx": _NUM_CTX, "num_predict": _NUM_PREDICT},
    }
    content_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    role = "assistant"
    t0 = time.perf_counter()
    first_token_at: Optional[float] = None
    stats = {"prompt_tokens": 0, "output_tokens": 0, "eval_ms": 0}

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
            calls = message.get("tool_calls")
            if calls:
                tool_calls.extend(calls)
            if data.get("done"):
                stats["prompt_tokens"] = int(data.get("prompt_eval_count") or 0)
                stats["output_tokens"] = int(data.get("eval_count") or 0)
                stats["eval_ms"] = int((data.get("eval_duration") or 0) / 1_000_000)
                ttft = _ms(t0, first_token_at) if first_token_at else "n/a"
                log.info(
                    f"Pythia: round done_reason={data.get('done_reason')} "
                    f"ttft={ttft} round={_ms(t0)}"
                )

    assembled: dict[str, Any] = {"role": role, "content": "".join(content_parts)}
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
    Answer `message`, using tools as needed. `history` is prior turns as
    {"role": "user"|"assistant", "content": str} dicts; pass the running
    conversation to keep context. Returns the final answer text.

    If `on_delta` is given, it's called with each content token as the model
    streams it (so the UI can render the answer live). It fires on every round,
    including tool rounds — those usually stream no content. `on_delta` runs on
    the caller's (worker) thread, so it must marshal to the GUI thread itself.

    If `on_event` is given, it's called exactly once, right before returning,
    with a {"type": "stats", ...} payload summing prompt/output tokens and eval
    time across every round (a tool-using answer runs multiple rounds), plus
    tool call/failure counts and whether `cancel` fired. Runs on the caller's
    (worker) thread like `on_delta`.

    If `cancel` is given and gets set mid-answer, the in-flight round stops
    promptly (closing the connection halts Ollama) and ask() returns whatever
    text has streamed so far. Never raises for this either.

    Never raises: network/LLM problems come back as a short readable string so
    the panel can print the result straight to the transcript.
    """
    t_start = time.perf_counter()
    system_prompt = machine_spirit.effective_prompt()
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": message})

    prompt_tokens = 0
    output_tokens = 0
    eval_ms = 0
    tools_called = 0
    failed_tools: list[str] = []

    def _emit(answer_text: str = "") -> str:
        if on_event is not None:
            on_event({
                "type": "stats",
                "prompt_tokens": prompt_tokens,
                "output_tokens": output_tokens,
                "wall_ms": int((time.perf_counter() - t_start) * 1000),
                "eval_ms": eval_ms,
                "tools_called": tools_called,
                "tools_failed": len(failed_tools),
                "failed_tools": failed_tools,
                "cancelled": cancel is not None and cancel.is_set(),
            })
        return answer_text

    try:
        for _ in range(_MAX_TOOL_ROUNDS):
            reply, stats = _chat(messages, on_delta=on_delta, cancel=cancel)
            prompt_tokens += stats["prompt_tokens"]
            output_tokens += stats["output_tokens"]
            eval_ms += stats["eval_ms"]
            messages.append(reply)                      # keep the model's turn in context

            if cancel is not None and cancel.is_set():
                log.info(f"Pythia: ask() cancelled {_ms(t_start)}")
                return _emit((reply.get("content") or "").strip())

            tool_calls = reply.get("tool_calls") or []
            if not tool_calls:
                log.info(f"Pythia: ask() total {_ms(t_start)}")
                return _emit((reply.get("content") or "").strip() or "(no answer)")

            # Run each requested tool, feed the JSON result back as a tool turn.
            for call in tool_calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments") or {}
                result = _dispatch(name, args)
                tools_called += 1
                if "error" in result:
                    failed_tools.append(name)
                messages.append({
                    "role": "tool",
                    "tool_name": name,
                    "content": json.dumps(result),
                })
        # Ran out of rounds still wanting tools — hand back whatever text we have.
        return _emit("The oracle got stuck consulting its tools. Try rephrasing.")
    except requests.Timeout:
        log.warning("Pythia: Ollama request timed out.")
        return _emit("The oracle is slow to answer (LLM timed out). Is the model still loading?")
    except requests.ConnectionError:
        log.warning("Pythia: could not reach Ollama.")
        return _emit(f"The oracle is unreachable — no Ollama server at {_BASE_URL}.")
    except Exception as e:
        log.error(f"Pythia: unexpected failure: {e}")
        return _emit(f"The oracle faltered: {e}")


# ── Preload ─────────────────────────────────────────────────────────────────

def _warm() -> None:
    """Load gemma4:e2b into Ollama's memory without generating anything (Ollama
    loads the model when /api/chat is called with empty messages). keep_alive=-1
    then keeps it resident. Best-effort: swallows EVERYTHING — if Ollama isn't
    running at launch, warming is simply skipped and the first real question
    pays the cold load as before."""
    try:
        t0 = time.perf_counter()
        resp = requests.post(
            _CHAT_URL,
            json={"model": _MODEL, "messages": [], "keep_alive": _KEEP_ALIVE,
                  "stream": False},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        log.info(f"Pythia: model warmed in {_ms(t0)}")
    except Exception as e:  # noqa: BLE001 — best-effort; Ollama may be down at launch
        log.info(f"Pythia: prewarm skipped ({type(e).__name__}: {e})")


def prewarm() -> threading.Thread:
    """Preload the model into Ollama on a background thread at startup so the
    first real question doesn't pay the ~100s cold load. Never raises and does
    nothing visible if Ollama is down. Returns the thread (mainly for tests)."""
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
