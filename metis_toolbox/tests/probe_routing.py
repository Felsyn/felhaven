"""
probe_routing.py — play the routing corpus against the live model, by hand.

Not a unit test: discover() never collects it (no test_ prefix), and CI has no
model to ask. Run it on the machine that runs Felhaven, with Ollama up:

    python -X utf8 -m tests.probe_routing                 # both prompts, 2 runs
    python -X utf8 -m tests.probe_routing --prompt default --runs 1 --ids 1-5

It prints a Markdown report on stdout and WRITES NOTHING. Redirect it to
keep it (somewhere outside the repo; it is a reading of one machine on one day).

What it measures: which tools pythia.ask() called for each phrasing in
tests/fixtures/broker_routing.json, under the owner's Machine Spirit prompt
(the override, if one is saved) and under DEFAULT_SYSTEM_PROMPT (what a fresh
install runs). Each row runs `--runs` times, because one sample of a sampled
model is an anecdote.

Held still for the whole run, and printed in the header: the model, `think`
(read once from Themis and passed explicitly, so a mid-run flip cannot change
it; a flip re-reads the whole prefix) and the owner's temperature, which ask()
reads itself.

Nothing the probe triggers may change the machine. ask() runs the real tools,
so three are replaced for the run through ask(tool_overrides=), which keeps the
schema the model sees:
  - play_music and control_music, which would start or change real playback.
    The stand-ins answer in the real shape, with "playing" state, so a
    status-then-volume chain (probe 22) can still happen.
  - manage_timer, which writes timer_state.json and would ring an alarm.
write_document and edit_document need ask(on_confirm=) to return True; the
probe always answers False, so they are refused and recorded as a call, which
is all routing needs. Everything else runs for real: reads, some over the
network through Hermes, like any question asked in the chat.

`prefix` is the smallest prompt-token count among answers that called no
tool: one round, so system prompt + tool schemas + the question. ask()'s stats
SUM every round, so a tool-using answer reports about twice that; and on Ollama
0.40.16 the count includes tokens served from the cache (a two-round answer
reported ~2x a one-round one in 3.4 s, measured 2026-11-14). Each prompt opens
with Felhaven's own warm-up question ("ping"), which may itself call a tool.
"""

import argparse
import sys
import time
from typing import Any

import machine_spirit
import pythia
import themis
from tests.test_routing_corpus import load_probes


# ── Stand-ins for the tools that would change the machine ─────────────────────

def _fake_play_music(query: str = "", **_: Any) -> dict[str, Any]:
    return {"now_playing": f"{query or 'something'} (probe stand-in)",
            "channel": "probe", "url": "https://www.youtube.com/watch?v=probe"}


def _fake_control_music(action: str = "status", level: Any = None,
                        **_: Any) -> dict[str, Any]:
    status = {"running": True, "title": "Karma Police (probe stand-in)",
              "paused": False, "volume": 70}
    if action == "volume" and level is not None:
        status["volume"] = level
    return {"action": action, "status": status}


def _fake_manage_timer(action: str = "query", **_: Any) -> dict[str, Any]:
    return {"action": action, "timers": [
        {"slot": 1, "running": action == "start", "remaining_seconds": 1200,
         "display": "20:00", "expired": False, "acknowledged": False}]}


_OVERRIDES = {
    "play_music": _fake_play_music,
    "control_music": _fake_control_music,
    "manage_timer": _fake_manage_timer,
}


# ── One question ──────────────────────────────────────────────────────────────

def _ask(prompt: str, system: str, think: bool,
         history: "list[dict[str, Any]] | None" = None) -> dict[str, Any]:
    """Ask once; return the calls made, the token count and the time taken.
    `history` is the row's earlier turns, as plain messages (ask() accepts
    them; the chat would also carry tool messages, which a probe has none of)."""
    stats: dict[str, Any] = {}
    answer = pythia.ask(
        prompt, history=[dict(m) for m in history or []],
        system=system, think=think, tool_overrides=_OVERRIDES,
        on_confirm=lambda _req: False,         # never write a file
        on_verbatim=lambda _payload: None,     # the chat shows these; we don't
        on_event=stats.update,
    )
    calls = [(r.get("name", "?"), r.get("arguments") or {})
             for r in stats.get("tool_records", [])]
    return {"calls": calls, "answer": answer,
            "prompt_tokens": stats.get("prompt_tokens", 0),
            "wall_ms": stats.get("wall_ms", 0)}


def _verdict(probe: dict[str, Any], calls: list[tuple[str, dict]]) -> str:
    names = [n for n, _ in calls]
    expect = probe["expect"]
    if not all(e in names for e in expect):
        return "MISS"
    if probe.get("exact") and set(names) - set(expect):
        return "EXTRA"
    for key, want in (probe.get("args") or {}).items():
        first = next(a for n, a in calls if n == expect[0])
        accepted = want if isinstance(want, list) else [want]
        got = str(first.get(key, "")).lower()
        if got not in {str(w).lower() for w in accepted}:
            return "ARGS"
    return "pass"


def _show(calls: list[tuple[str, dict]]) -> str:
    if not calls:
        return "(none)"
    out = []
    for name, args in calls:
        brief = ", ".join(f"{k}={v!r}" for k, v in args.items())
        out.append(f"{name}({brief})" if brief else name)
    return "; ".join(out).replace("|", "/")


# ── A whole pass under one prompt ─────────────────────────────────────────────

def _run_variant(label: str, system: str, think: bool, runs: int,
                 probes: list[dict[str, Any]]) -> tuple[int, int]:
    warm = _ask(pythia._WARM_PROBE, system, think)
    single = [warm["prompt_tokens"]] if not warm["calls"] else []
    print(f"\n## {label} prompt\n")
    print(f"warm-up ({pythia._WARM_PROBE!r}): {warm['prompt_tokens']} tokens, "
          f"{warm['wall_ms']} ms, calls: {_show(warm['calls'])}\n")
    head = " | ".join(f"run {i + 1}" for i in range(runs))
    print(f"| # | Prompt | {head} | Verdicts | Tokens (prompt, per run) | ms |")
    print("|---|---|" + "---|" * runs + "---|---|---|")
    passed = total = 0
    for probe in probes:
        results = [_ask(probe["prompt"], system, think, probe.get("history"))
                   for _ in range(runs)]
        verdicts = [_verdict(probe, r["calls"]) for r in results]
        single += [r["prompt_tokens"] for r in results if not r["calls"]]
        passed += verdicts.count("pass")
        total += len(verdicts)
        note = " (known miss)" if probe.get("known_miss") else ""
        if probe.get("history"):
            note += f" (after {len(probe['history']) // 2} earlier turn(s))"
        cells = " | ".join(_show(r["calls"]) for r in results)
        toks = ", ".join(str(r["prompt_tokens"]) for r in results)
        ms = ", ".join(str(r["wall_ms"]) for r in results)
        print(f"| {probe['id']} | {probe['prompt']}{note} | {cells} | "
              f"{' / '.join(verdicts)} | {toks} | {ms} |", flush=True)
    print(f"\n**{label}: {passed}/{total} pass.**")
    if single:
        print(f"\nprefix: **~{min(single)} tokens** (smallest prompt count of "
              f"{len(single)} answers that called no tool, so one round each)")
    return passed, total


def _parse_ids(spec: str, probes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not spec:
        return probes
    wanted: set[int] = set()
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        wanted.update(range(int(lo), int(hi or lo) + 1))
    return [p for p in probes if p["id"] in wanted]


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--prompt", choices=("both", "default", "override"),
                    default="both")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--ids", default="", help="e.g. 1-5,9,23")
    args = ap.parse_args(argv)

    probes = _parse_ids(args.ids, load_probes())
    think = themis.pythia_thinking() and pythia._can_think(pythia.current_model())
    override = machine_spirit.effective_prompt()
    default = machine_spirit.DEFAULT_SYSTEM_PROMPT
    same = override.strip() == default.strip()

    print("# Pythia routing probe\n")
    print(f"- date: {time.strftime('%Y-%m-%d %H:%M')}")
    print(f"- model: {pythia.current_model()}")
    print(f"- think: {think}; temperature: {themis.pythia_temperature()}")
    print(f"- tools: {len(pythia.TOOLS)}; runs per row: {args.runs}; "
          f"rows: {len(probes)}")
    print(f"- override saved: {'no (it IS the default)' if same else 'yes'}")
    print("- stand-ins: play_music, control_music, manage_timer; "
          "file writes always refused")

    variants = []
    if args.prompt in ("both", "default"):
        variants.append(("Default", default))
    if args.prompt in ("both", "override") and not (same and variants):
        variants.append(("Override", override))
    summary = [(label, *_run_variant(label, system, think, args.runs, probes))
               for label, system in variants]

    print("\n## Summary\n")
    for label, passed, total in summary:
        print(f"- {label}: {passed}/{total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
