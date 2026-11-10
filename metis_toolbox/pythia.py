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
                 cancel=None, system=None, allow_tools=None,
                 tool_overrides=None, on_verbatim=None,
                 on_confirm=None) -> str
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
                 `system` and `allow_tools` narrow ONE turn — a different
                 system prompt, and a tool allowlist enforced at DISPATCH, not
                 merely by omitting schemas. Both default to today's behaviour,
                 so chat is unchanged. They exist for unattended callers
                 (awen.py), which put text they did not write into a small
                 model's context with nobody reading the transcript.
                 `on_verbatim` is the one path a tool's output takes AROUND
                 the model: a result carrying a "verbatim" payload is handed
                 to it and kept out of the model's context, and a round made
                 only of such calls ends the turn (CONVENTIONS §12).
                 `on_confirm` is how a tool that changes files asks the user
                 first: it gets {"tool", "arguments"} and runs the call only
                 on True. Without it those tools are refused.
             Between the argument gate and the handler sits the DISPATCH
             GATE (CONVENTIONS §12, 2026-09-30): fetch_page opens only a URL
             the user wrote or a search_web result gave, and every tool in
             _CONFIRM_TOOLS waits for on_confirm. It fails closed.
             TOOLS / _DISPATCH are built once, at import, by reading each tool
             module's own TOOL_DEFINITION — so the registry can never drift
             out of sync with the handlers. Every call passes an argument gate
             driven by the same schemas before its handler runs (_dispatch;
             CONVENTIONS §12). The DEFAULT system prompt lives in
             machine_spirit.py (effective_prompt(), read fresh each call) —
             Pythia owns no prompt text of its own, and a caller's `system`
             override is that caller's text, never this module's.

             is_busy() -> bool
                 True while any ask() is generating. The is_warming() shape,
                 and it exists because nothing else could answer: HomePanel
                 knows about its own turn through a Tk object on the main
                 thread, which a Kairos worker cannot reach.

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
             eudoxus,argus,helios,hypatia,selene,morpheus,daedalus}.py — each
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
import math
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, Callable, Optional, Sequence

import requests

import machine_spirit
import themis
from tools import (
    horai, hephaestus, aura, ammit, midas, aether, pheme, zeno, eudoxus,
    argus, helios, hypatia, selene, morpheus, callimachus, herodotus,
    daedalus,
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
    daedalus,
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
# Each tool's `parameters` object, by name — what the argument gate in
# _dispatch checks a call against. Read from the same definitions as TOOLS,
# so the gate enforces exactly what the model was shown.
_SCHEMAS: dict[str, dict[str, Any]] = {}
for _mod in _TOOL_MODULES:
    for _definition, _handler in _module_tools(_mod):
        TOOLS.append(_definition)
        _DISPATCH[_definition["function"]["name"]] = _handler
        _SCHEMAS[_definition["function"]["name"]] = (
            _definition["function"].get("parameters") or {})

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
# Context window (tokens). 8192 was never breached — 0 of 2,737 logged requests
# truncated, largest prompt 6,558 (2026-09-27) — but the ~4.9k tool-schema
# prefix left thin headroom for a long tool result, and every new tool grows
# it. Raising it is measured as nearly free on Obelisk (32768 cost +0.02 GB of
# VRAM, speed flat, 2026-07-31). It is headroom, not memory: how much history
# rides along is _MAX_HISTORY_TURNS in home_panel.py. CONVENTIONS §12.
_NUM_CTX = 16384
_NUM_PREDICT = 1024             # max output tokens per round trip
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
# Thinking and temperature are the owner's (themis, the MACHINE SPIRIT tab),
# read once per ask(). Neither touches the runner: temperature and num_predict
# are per-request sampling options (measured 2026-11-09: load 3-4 ms after a
# change, versus ~70 s for a num_ctx change). The reasoning a thinking round
# writes counts against num_predict — one measured round spent 656 of 1024
# tokens on it — so a thinking round gets this budget instead. At ~68 tok/s,
# a round that used all of it would take about 45 s.
_NUM_PREDICT_THINKING = 3072


def _request_options(think: bool, temperature: Optional[float]) -> dict[str, Any]:
    """This request's options: _OPTIONS (num_ctx decides the runner, so it never
    varies), the thinking budget when thinking, and the owner's temperature if
    set. With none set, no temperature is sent and the model keeps its own."""
    options: dict[str, Any] = dict(_OPTIONS)
    if think:
        options["num_predict"] = _NUM_PREDICT_THINKING
    if temperature is not None:
        options["temperature"] = temperature
    return options

# ── Degraded answers ─────────────────────────────────────────────────────────
# What ask() returns when there is no answer to give: the model went silent, ran
# out of tool rounds, or Ollama was unreachable. They read as prose, which is
# right for a transcript a human is looking at and WRONG for any caller that
# will act on the text.
#
# Awen learned that the hard way. A DJ turn that played a song and then ran out
# of rounds spoke "The oracle got stuck consulting its tools. Try rephrasing."
# aloud as the song's introduction — advice to rephrase a question the listener
# never asked. So the family is named here rather than left as five literals,
# and is_degraded() is the seam that lets a caller tell "no answer" from "an
# answer".
#
# They all open with the same two words ON PURPOSE. Two of the five interpolate
# (the endpoint, an exception class), so there is no exact set to match against,
# and a prefix is the only honest test. Keep new ones in the family.
_DEGRADED_PREFIX = "The oracle"
_SILENT_ANSWER = "The oracle answered but said nothing. Try asking again."
_STUCK_ANSWER = "The oracle got stuck consulting its tools. Try rephrasing."
_TIMEOUT_ANSWER = ("The oracle is slow to answer (LLM timed out). Is the model "
                   "still loading?")


def is_degraded(answer: str) -> bool:
    """Is this ask() result a FAILURE dressed as prose, rather than an answer?

    For callers that act on the text instead of merely printing it. HomePanel
    prints whatever comes back and is right to — a human reading "the oracle is
    unreachable" is being told something true. A caller that speaks the text to
    someone who asked no question needs to know the difference.

    Cheap and deliberately conservative: a false positive costs a caller its
    fallback path, a false negative costs it nonsense out loud."""
    return (answer or "").strip().startswith(_DEGRADED_PREFIX)


# ── Rite records — per-tool-invocation diagnostics for the Rite Log ───────────
# One record per tool call, handed to the UI in the stats payload. Pythia builds
# them because it is the only place that sees a call, its arguments and its
# result together. Nothing here imports cerberus: the diagnostic system must
# never need to unlock the vault in order to report that the vault is locked.
_PREVIEW_CAP = 1000             # chars — the cap on a stored arguments/result string
# A tool result bigger than this, as the JSON the model reads, is logged. It is
# a tripwire, not a cap: the tools that return long text budget it themselves
# (fetch_page 4000, read_document 6000 chars), so this sits above both and only
# fires on a tool with no budget at all — read_document's old whole-file read.
_LARGE_RESULT_CHARS = 8000
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


# ── The argument gate ─────────────────────────────────────────────────────────
# Every call is checked against its own tool's schema before the handler runs.
# Without it, a model that invented a parameter ("weather in Pittsburgh" →
# location=...) got "tool_failed: ... raised TypeError", which names nothing it
# can fix, and "3" for a number reached arithmetic as a string.
#
# The rule (CONVENTIONS §12): REJECT what no handler could interpret — an
# undeclared parameter, a missing required one, a value of the wrong type —
# with a slug that says what to send instead. NORMALIZE what is unambiguous —
# a numeric string, an enum value in the wrong case, JSON sent as a string,
# null for "not given". LEAVE MEANING TO THE HANDLER: enum membership and
# numeric ranges. Every enum handler already refuses a value it does not know,
# with its own slug; Daedalus deliberately resolves names its enum does not
# list; Morpheus clamps a volume of 150. Refusing those here would break the
# first and cost a small model a retry on the second.
_GATE_DESC_CHARS = 120          # a missing parameter's description, cut to this


class _BadValue(Exception):
    """One argument the gate cannot hand to a handler. The message is the
    detail after "bad_arguments: " — written for the model, not a traceback."""


def _bad(detail: str) -> dict[str, Any]:
    return {"error": f"bad_arguments: {detail}"}


def _cut(text: str) -> str:
    """A parameter description, one line, cut at a word to _GATE_DESC_CHARS."""
    text = " ".join(text.split())
    if len(text) <= _GATE_DESC_CHARS:
        return text
    return text[:_GATE_DESC_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:—-") + "…"


def _as_number(name: str, value: Any, integer: bool) -> Any:
    """A number the schema asked for, from a number or a numeric string.
    A real int or float passes through untouched."""
    kind = "a whole number" if integer else "a number"
    if isinstance(value, bool):                  # an int subclass: True == 1
        raise _BadValue(f"'{name}' must be {kind}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        number = value
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            raise _BadValue(f"'{name}' must be {kind}") from None
    else:
        raise _BadValue(f"'{name}' must be {kind}")
    if not math.isfinite(number):                # float("nan") parses
        raise _BadValue(f"'{name}' must be {kind}")
    if integer:
        if not number.is_integer():
            raise _BadValue(f"'{name}' must be {kind}")
        return int(number)
    return number


def _check_value(name: str, value: Any, spec: dict[str, Any]) -> Any:
    """One argument, checked and normalized against its property schema.
    A type the gate does not know is passed through for the handler."""
    kind = spec.get("type")
    if kind in ("number", "integer"):
        return _as_number(name, value, integer=(kind == "integer"))
    if kind == "string":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        if not isinstance(value, str):
            raise _BadValue(f"'{name}' must be text")
        choices = spec.get("enum") or []
        wanted = value.strip().casefold()
        for choice in choices:
            if isinstance(choice, str) and choice.casefold() == wanted:
                return choice
        return value                             # membership is the handler's
    return value


def _check_arguments(
    tool_name: str, arguments: Any,
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """Check one call's arguments against its tool's schema.

    Returns (arguments to call the handler with, None), or ({}, a §2
    bad_arguments slug). A name with no registered schema — a handler a test
    patched straight into _DISPATCH — is passed through unchecked, because
    there is nothing to check it against. Never raises."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except ValueError:
            arguments = None
    if not isinstance(arguments, dict):
        return {}, _bad("arguments must be an object")
    schema = _SCHEMAS.get(tool_name)
    if schema is None:
        return arguments, None
    props: dict[str, Any] = schema.get("properties") or {}
    given = {k: v for k, v in arguments.items() if v is not None}
    unknown = [k for k in given if k not in props]
    if unknown:
        if not props:
            return {}, _bad(f"{tool_name} takes no parameters")
        return {}, _bad(f"{tool_name} takes only: {', '.join(props)}")
    missing = [p for p in schema.get("required") or [] if p not in given]
    if missing:
        parts = []
        for param in missing:
            about = _cut(str((props.get(param) or {}).get("description") or ""))
            parts.append(f"missing '{param}'" + (f" — {about}" if about else ""))
        return {}, _bad("; ".join(parts))
    clean: dict[str, Any] = {}
    try:
        for key, value in given.items():
            clean[key] = _check_value(key, value, props[key] or {})
    except _BadValue as e:
        return {}, _bad(str(e))
    return clean, None


# ── The dispatch gate — what a turn may do on the model's word alone ─────────
# Pythia reads text strangers wrote (news, search results,
# web pages, song titles, archive notes that began as any of those), can reach
# private data (network summary, location, watchlist, the archive), and holds
# tools that send data out or keep it. Text in a web page can therefore ASK the
# model to read something private and send it out, and before this gate only
# the model's judgement stood in the way. That is difficult, not expensive, and
# it gets weaker as the model gets stronger at following instructions.
#
# So two rules sit here, in code, where no text can talk them round
# (CONVENTIONS §12, 2026-09-30):
#
#   1. URL PROVENANCE. fetch_page opens only a URL the USER wrote, or one a
#      search_web result gave, anywhere in the conversation the model can see.
#      Never one the model assembled: "https://attacker.example/?d=<your data>"
#      is exactly a URL no one but the model wrote. Links inside fetched pages
#      are not trusted either. A page can offer hundreds, and which one the
#      model picks is itself a message.
#   2. CONFIRMATION. A tool that changes files (_CONFIRM_TOOLS) runs only after
#      the user says yes to its exact arguments, through the caller's
#      `on_confirm`. It fails closed: no way to ask, an error, a no, or a Stop
#      all mean the call does not run.
#
# Callimachus separately refuses loopback and LAN addresses at connect time,
# so even a URL the user typed can't reach the home network.
_URL_TOOL = "fetch_page"                 # opens a URL the model names
_URL_SOURCE_TOOL = "search_web"          # whose result URLs are trusted
# Every brain tool that changes files. A new one joins this set (CONVENTIONS
# §8); the lockstep test fails if a name here stops being registered.
_CONFIRM_TOOLS = frozenset({"write_document", "edit_document"})

# An https URL as a user writes it in a sentence: up to the next whitespace.
_USER_URL_RE = re.compile(r"https://\S+", re.IGNORECASE)
# What a sentence can leave stuck to the end of a URL: "(see https://x.org/a)."
_TRAILING_PUNCT = ".,;:!?)]}>'\""


class _TurnGuard:
    """What one ask() turn may do on the model's word alone: the URLs
    fetch_page may open, and how to ask the user about a file change.

    Built from the history and the new message before the first round, and
    fed each search_web result as it arrives. The user's messages are taken
    as the user's, so a caller that composes its "user" message from text it
    did not write must never allow fetch_page (Awen allows only play_music,
    and a test holds it to that)."""

    def __init__(self, messages: Sequence[dict[str, Any]],
                 on_confirm: Optional[Callable[[dict[str, Any]], bool]],
                 cancel: Optional[threading.Event]) -> None:
        self.on_confirm = on_confirm
        self.cancel = cancel
        # A no is final for the rest of the turn. Without this, a model that
        # retries after a Deny asks again, up to _MAX_TOOL_ROUNDS times, and a
        # question asked until the user clicks Allow to make it stop is not a
        # control at all.
        self.denied = False
        self._urls: set[str] = set()
        for message in messages:
            self._note_message(message)

    def _note_message(self, message: dict[str, Any]) -> None:
        content = message.get("content")
        if not isinstance(content, str):
            return
        role = message.get("role")
        if role == "user":
            for token in _USER_URL_RE.findall(content):
                # "…/a)." allows "…/a)." "…/a)" and "…/a" — every way the
                # sentence might end, and nothing the user didn't type.
                self._urls.add(token)
                while token and token[-1] in _TRAILING_PUNCT:
                    token = token[:-1]
                    self._urls.add(token)
        elif role == "tool" and message.get("tool_name") == _URL_SOURCE_TOOL:
            try:
                result = json.loads(content)
            except ValueError:
                return
            if isinstance(result, dict):
                self.note_result(_URL_SOURCE_TOOL, result)

    def note_result(self, tool_name: str, result: dict[str, Any]) -> None:
        """Trust the URLs a search_web result carries. Any other tool's
        result adds nothing — fetched pages in particular."""
        if tool_name != _URL_SOURCE_TOOL:
            return
        rows = result.get("results")
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and isinstance(row.get("url"), str):
                self._urls.add(row["url"].strip())

    def url_allowed(self, url: str) -> bool:
        """Byte for byte: an exact URL the user or a search supplied."""
        return bool(url) and url in self._urls


def _gate(tool_name: str, arguments: dict[str, Any],
          guard: Optional[_TurnGuard]) -> Optional[dict[str, Any]]:
    """The dispatch gate's two rules, for one call whose arguments have
    already passed _check_arguments. Returns None to let the call run, or the
    §2 slug that refuses it. No guard at all — a direct _dispatch with no
    turn behind it — refuses both kinds of call: the gate fails closed."""
    if tool_name == _URL_TOOL:
        url = str(arguments.get("url") or "").strip()
        if guard is None or not guard.url_allowed(url):
            return {"error": "untrusted_url: fetch_page opens only a url the "
                             "user wrote or a search_web result gave. Search "
                             "first, or ask the user for the link."}
    if tool_name in _CONFIRM_TOOLS:
        if guard is None or guard.on_confirm is None:
            return {"error": f"no_confirmation: {tool_name} needs the user's "
                             f"OK, and this conversation cannot ask for it."}
        if guard.denied:
            return {"error": f"not_confirmed: the user already said no this "
                             f"turn, so {tool_name} was not asked about again."}
        try:
            allowed = guard.on_confirm(
                {"tool": tool_name, "arguments": dict(arguments)}) is True
        except Exception as e:                   # the UI's bug means no
            log.error(f"Pythia: on_confirm raised: {type(e).__name__}: {e}")
            allowed = False
        if guard.cancel is not None and guard.cancel.is_set():
            allowed = False                      # Stop wins over a late yes
        if not allowed:
            guard.denied = True
            return {"error": f"not_confirmed: the user did not allow {tool_name}."}
    return None


def _dispatch(tool_name: str, arguments: Any,
              allowed: Optional[set[str]] = None,
              overrides: Optional[dict[str, Callable[..., dict[str, Any]]]] = None,
              guard: Optional[_TurnGuard] = None,
              ) -> dict[str, Any]:
    """Route one tool call to its handler: forward the model's arguments as
    kwargs (call with none if it gave none) and turn any handler failure into
    an error dict so the model can recover rather than the whole turn crashing.
    The error identifier is a §2 slug: the caught exception's *message* is kept
    out of the returned dict (it feeds straight into the LLM context) — only
    its class name travels, the raw text stays in the log.

    `allowed` restricts which tools may actually RUN, and the check sits here —
    ahead of the _DISPATCH lookup — rather than only in the schema list handed
    to the model. Leaving a name out of `tools` is a hint, not a boundary: this
    function runs any name present in _DISPATCH, so a model that emits one
    anyway would have it executed. That matters because ask()'s narrowed-tool
    callers are the unattended ones, feeding text they did not write (YouTube
    titles, page contents) into a small model's context with nobody reading the
    transcript. None means no restriction, which is every chat turn.

    `overrides` swaps the HANDLER behind a tool name for this turn, leaving its
    schema — the text the model reads — exactly as its owning module wrote it.
    It exists because a tool's job and a particular caller's PRECONDITIONS are
    different things. `play_music` searches and plays in one call, so a caller
    that must check something between those two steps cannot express it by
    calling the tool; and those checks belong to the caller, not to the module
    that plays music. Teaching Morpheus about them would give it a second job
    (§0). The override is per-turn and never mutates _DISPATCH, so a chat turn
    running at the same moment still gets the real handler.

    A refused call returns a §2 slug like any other failure, so the model can
    recover inside the same turn, and it is recorded as a failed rite — a
    refusal is a real event, not a silent drop.

    Last, the ARGUMENTS pass the gate (_check_arguments) — after the allowlist
    and the name lookup, so a refusal and an unknown name still read as what
    they are. An override is gated by the registered schema too: it keeps the
    schema, so it keeps the schema's promises. The handler receives the
    cleaned arguments; ask()'s rite record keeps the ones the model sent.

    Then the dispatch GATE (_gate), on the cleaned arguments, so the user is
    asked about exactly what will run: fetch_page's URL must come from the
    user or a search, and a file-changing tool needs the user's yes. `guard`
    carries the turn's side of both; None refuses both (fails closed)."""
    if allowed is not None and tool_name not in allowed:
        log.warning("Pythia: refused out-of-allowlist tool %r (allowed: %s)",
                    tool_name, ", ".join(sorted(allowed)) or "none")
        return {"error": f"tool_not_available: {tool_name!r} cannot be used here."}
    fn = (overrides or {}).get(tool_name) or _DISPATCH.get(tool_name)
    if fn is None:
        return {"error": f"unknown_tool: {tool_name!r}"}
    arguments, refused = _check_arguments(tool_name, arguments)
    if refused is not None:
        log.warning("Pythia: refused a call to %r — %s", tool_name, refused["error"])
        return refused
    refused = _gate(tool_name, arguments, guard)
    if refused is not None:
        log.warning("Pythia: gate refused %r — %s", tool_name, refused["error"])
        return refused
    try:
        return fn(**arguments) if arguments else fn()
    except Exception as e:                       # a tool bug must not kill the chat
        log.error(f"Pythia: tool {tool_name!r} raised: {type(e).__name__}: {e}")
        return {"error": f"tool_failed: {tool_name!r} raised {type(e).__name__}"}


def _split_verbatim(
    result: dict[str, Any],
    on_verbatim: Optional[Callable[[dict[str, Any]], None]],
) -> tuple[dict[str, Any], Optional[str]]:
    """Take a tool's VERBATIM text out of its result before the model sees it.

    A tool that must show the user something exactly — Daedalus's docstrings —
    returns it under "verbatim" ({"label", "title", "text", "ack"}). Anything
    that reaches the model comes back paraphrased, whatever the prompt says, so
    the text goes to `on_verbatim` (the UI) and the model gets the rest of the
    result plus "shown_to_user": True. Returns (what the model sees, the ack
    line to end the turn with) — the ack is None unless the text was shown.

    The text never reaches the model on ANY path: with no sink to show it on,
    or a sink that fails, the model gets a §2 slug instead and can say so.
    Every result without the key passes through untouched, which is every
    other tool."""
    if "verbatim" not in result:
        return result, None
    payload = result["verbatim"]
    if not (isinstance(payload, dict) and isinstance(payload.get("text"), str)):
        log.error("Pythia: malformed verbatim payload withheld from the model")
        return {"error": "tool_failed: malformed verbatim payload"}, None
    if on_verbatim is None:
        log.info("Pythia: verbatim result withheld — this caller has no display")
        return {"error": "no_display: this conversation cannot show text verbatim"}, None
    try:
        on_verbatim(dict(payload))
    except Exception as e:                       # the UI's bug must not kill the turn
        log.error(f"Pythia: on_verbatim raised: {type(e).__name__}: {e}")
        return {"error": "display_failed: the text could not be shown"}, None
    shown = {k: v for k, v in result.items() if k != "verbatim"}
    shown["shown_to_user"] = True
    ack = payload.get("ack")
    return shown, (ack.strip() if isinstance(ack, str) and ack.strip()
                   else "Shown above.")


def _chat(
    messages: list[dict[str, Any]],
    on_delta: Optional[Callable[[str], None]] = None,
    cancel: Optional[threading.Event] = None,
    tools: Optional[list[dict[str, Any]]] = None,
    think: bool = False,
    temperature: Optional[float] = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    """One STREAMING round trip to Ollama's /api/chat. Reads the newline-delimited
    JSON stream, calls on_delta(piece) for each content token as it arrives, and
    `tools` is the schema list sent to the model; None means all of TOOLS,
    which is every chat turn. A narrowed list is a HINT to the model and never a
    guarantee — the boundary that actually holds is _dispatch's `allowed` set.

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
        "tools": TOOLS if tools is None else tools,
        "stream": True,
        # The owner's choice (themis), off by default. Either way the reasoning
        # trace is dropped, not shown — see the docstring — so thinking makes
        # an answer slower and more careful, never longer on screen.
        "think": think,
        "keep_alive": _KEEP_ALIVE,
        "options": _request_options(think, temperature),
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
    # guard.) Reached when thinking is on (themis) and the round spent its whole
    # budget reasoning, or when a model thinks regardless.
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


# ── Turn accounting ───────────────────────────────────────────────────────────
# How many ask() calls are generating right now. A worker→main shared object,
# which §6 otherwise reserves for the result queue; allowed here for exactly the
# reason _WARMING is (below), and guarded rather than atomic because it is a
# counter, not a flag — two callers can be mid-turn at once.
#
# is_busy() exists because nothing else could answer the question. HomePanel
# knows about ITS turn via self._thread, and that is a Tk object on the main
# thread — unreachable from a Kairos worker deciding whether now is a good
# moment to take the model.
_turns_lock = threading.Lock()
_turns_in_flight = 0


def is_busy() -> bool:
    """True while any ask() is generating — the is_warming() shape.

    Counts EVERY turn, chat and otherwise, because it answers "is the model
    busy", not "is someone else busy". A caller using it to yield must
    therefore read it BEFORE starting its own turn; reading it from inside one
    is always True and means nothing.

    It is a courtesy signal, not a lock. Two turns that overlap are handled —
    Ollama queues them — so a caller that loses the race gets a slow answer,
    never a broken one.
    """
    return _turns_in_flight > 0


def _enter_turn() -> None:
    global _turns_in_flight
    with _turns_lock:
        _turns_in_flight += 1


def _leave_turn() -> None:
    global _turns_in_flight
    with _turns_lock:
        _turns_in_flight = max(0, _turns_in_flight - 1)


def _tool_schemas(allow_tools: Optional[Sequence[str]]) -> list[dict[str, Any]]:
    """The schema list for this turn. None means all of TOOLS.

    Filters the live TOOLS rather than rebuilding schemas, so a narrowed turn
    sends the SAME text the full turn would — a hand-written copy would drift
    from the module that owns it the first time a description changed.

    An unregistered name is a caller bug that would otherwise be invisible: the
    filter would quietly yield a shorter list (or none at all) and the model
    would simply stop calling tools. It is logged rather than raised, because
    ask() never raises."""
    if allow_tools is None:
        return TOOLS
    wanted = set(allow_tools)
    unknown = wanted - set(_DISPATCH)
    if unknown:
        log.warning("Pythia: allow_tools names unregistered tool(s): %s",
                    ", ".join(sorted(unknown)))
    return [t for t in TOOLS if t["function"]["name"] in wanted]


# ── Contract ──────────────────────────────────────────────────────────────────

def ask(
    message: str,
    history: Optional[list[dict[str, Any]]] = None,
    on_delta: Optional[Callable[[str], None]] = None,
    on_event: Optional[Callable[[dict[str, Any]], None]] = None,
    cancel: Optional[threading.Event] = None,
    system: Optional[str] = None,
    allow_tools: Optional[Sequence[str]] = None,
    tool_overrides: Optional[dict[str, Callable[..., dict[str, Any]]]] = None,
    on_verbatim: Optional[Callable[[dict[str, Any]], None]] = None,
    on_confirm: Optional[Callable[[dict[str, Any]], bool]] = None,
    think: Optional[bool] = None,
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

    `system` overrides the system prompt for this turn only. None — every chat
    turn — reads machine_spirit.effective_prompt() as before, so Machine Spirit
    remains the owner of PYTHIA's prompt and this module still holds no prompt
    text of its own. It exists for callers that are not the chat: a job with its
    own voice and its own instructions should not have to be expressed as an
    edit to the operator's prompt, which would then apply to every chat answer
    too. A blank string is treated as no override and logged, because silently
    sending an empty system prompt is never what a caller meant.

    `allow_tools` narrows this turn to the named tools, both in the schema list
    the model sees and — the part that matters — at dispatch, so a call outside
    it comes back as a `tool_not_available` slug and never runs. None means the
    full registry. See _dispatch for why the schema list alone is not enough.

    `tool_overrides` replaces the handler behind a tool name for this turn only,
    keeping the schema its module wrote. See _dispatch for why a caller's own
    preconditions do not belong inside the tool they guard.

    All three default to today's behaviour exactly, so no existing caller
    changes.

    `on_verbatim` is where a tool's VERBATIM text goes — a result carrying a
    "verbatim" payload ({"label", "title", "text", "ack"}; Daedalus's
    docstrings) is shown through it and never enters the model's context; the
    model sees only that it was shown (see _split_verbatim). It runs on the
    worker thread, like `on_delta`. If EVERY call in a round was shown this
    way, the turn ends there, with the payloads' "ack" lines as the answer: a
    result that carries nothing is the shape that makes the model go blank,
    and the blank-retry nudge would then have it say it knows nothing about
    text already on screen. A mixed round goes back to the model as usual.
    None — every caller but the chat — means such a result reaches the model
    as a `no_display` slug instead.

    `on_confirm` is asked before any tool in _CONFIRM_TOOLS runs (the ones
    that change files), with {"tool": name, "arguments": the cleaned
    arguments that will run}. It runs on the worker thread and may block until
    the user answers; only a return of True lets the call run. None, an
    exception, anything but True, or `cancel` set by the time it returns all
    refuse the call with a §2 slug, recorded as a failed rite. A caller that
    has no user to ask leaves it None, and those tools never run for it.

    Whatever the caller passes, fetch_page runs only on a URL that appears in
    a user message or a search_web result in `history` or this turn. The
    model's own URLs are refused (see _TurnGuard).

    `think` overrides the owner's thinking setting for this call; None (the
    default) follows it. Awen passes False: a DJ pick runs between songs, and
    thinking would leave the silence 5-20 s longer. The owner's temperature
    always applies. Both are read ONCE here, so a change made mid-answer takes
    effect on the next question, never halfway through this one.

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
    thinking = themis.pythia_thinking() if think is None else bool(think)
    temperature = themis.pythia_temperature()
    if system is not None and not system.strip():
        log.warning("Pythia: blank `system` override ignored — using the "
                    "Machine Spirit prompt")
        system = None
    system_prompt = (machine_spirit.effective_prompt() if system is None
                     else system)
    tools = _tool_schemas(allow_tools)
    allowed = None if allow_tools is None else set(allow_tools)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    n_preamble = len(messages)                   # system + prior history — not this turn's own
    messages.append({"role": "user", "content": message})
    # The dispatch gate's view of this turn: what the user and past searches
    # supplied, plus how to ask about a file change (see _TurnGuard).
    guard = _TurnGuard(messages, on_confirm, cancel)

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

    # Counted from here, not from the top of the function: everything above is
    # assignment, so nothing between the increment and the try can fail and
    # strand the count. The matching decrement is the `finally` on this same
    # try, which every `return` below passes through.
    _enter_turn()
    try:
        for round_i in range(_MAX_TOOL_ROUNDS):
            try:
                reply, stats = _chat(messages, on_delta=on_delta, cancel=cancel,
                                     tools=tools, think=thinking,
                                     temperature=temperature)
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
                silent = _SILENT_ANSWER
                messages.append({"role": "assistant", "content": silent})
                return _emit(silent)

            # Run each requested tool, feed the JSON result back as a tool turn,
            # and record one rite per invocation for the Rite Log.
            acks: list[str] = []                 # one per call shown verbatim
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
                result = _dispatch(name, args, allowed, tool_overrides, guard)
                if "error" not in result:
                    guard.note_result(name, result)  # search URLs become fetchable
                # Before the record and the tool message are built: both must
                # hold what the MODEL saw, never the text it was spared.
                result, ack = _split_verbatim(result, on_verbatim)
                if ack is not None:
                    acks.append(ack)
                record["duration_ms"] = int((time.perf_counter() - t_call) * 1000)
                if "error" in result:
                    record["status"] = "failed"
                    record["error"] = _error_text(result)
                else:
                    record["status"] = "ok"
                    preview, truncated = _result_preview(result)
                    record["result_preview"] = preview
                    record["result_truncated"] = truncated
                content = json.dumps(result)
                if len(content) > _LARGE_RESULT_CHARS:
                    log.warning(
                        "Pythia: %r put %d chars into the model's context "
                        "(tripwire %d) — does it need a size budget?",
                        name, len(content), _LARGE_RESULT_CHARS)
                messages.append({
                    "role": "tool",
                    "tool_name": name,
                    "content": content,
                })

            # Cancelled partway through the round: every remaining call is now
            # recorded not_run, so end the turn here. Falling through would open
            # another _chat connection only to break out of its first line.
            if cancel is not None and cancel.is_set():
                log.info(f"Pythia: ask() cancelled mid-round {_ms(t_start)}")
                return _emit((reply.get("content") or "").strip())

            # Every call this round was shown to the user word for word, so the
            # answer is already on screen — end the turn with the ack lines
            # rather than ask the model to talk about text it never saw.
            if acks and len(acks) == len(tool_calls):
                closing = " ".join(acks)
                messages.append({"role": "assistant", "content": closing})
                log.info(f"Pythia: ask() ended on verbatim output {_ms(t_start)}")
                return _emit(closing)
        # Ran out of rounds still wanting tools — hand back whatever text we have.
        stuck = _STUCK_ANSWER
        messages.append({"role": "assistant", "content": stuck})
        return _emit(stuck)
    except requests.Timeout:
        log.warning("Pythia: Ollama request timed out.")
        timed_out = _TIMEOUT_ANSWER
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
    finally:
        _leave_turn()


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
                # The owner's setting, because it changes the rendered prompt:
                # the first request after flipping `think` re-read the whole
                # prefix (15.4 s against 3.7 s, measured 2026-11-09), so a warm
                # done the other way would cache a prefix the first question
                # can't use.
                "think": themis.pythia_thinking(),
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
