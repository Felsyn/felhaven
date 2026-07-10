# Argus — Network Awareness

*Anti-Legion: ONE JOB*

Argus (the hundred-eyed giant) answers one question: **what is this computer
communicating with?** Connections, listeners, throughput, firewall, DNS cache —
all of it *visibility, not enforcement*. Veins and synapses, never threats. It's
the blue **ARGUS** tab in the Moderati card.

> **History:** Argus began as a dashboard-only watcher (like [Emanon](Emanon.md)),
> exposing `fetch()` alone. The `get_network_summary` brain tool it once listed as
> a "future" idea has since been built — so today it carries both surfaces (see
> the contract below).

## Read-only, always

Argus queries the connection table, firewall state, DNS cache, and NIC counters
and **reports** them. It never blocks, kills, captures packets, or elevates. It
needs **no admin rights** — reading firewall/DNS state doesn't require elevation
(only *modifying* rules does), and the `privilege` field just annotates the
honest gap when running as a normal user.

## Two shapes: fetch() vs handle()

| Function | Consumer | Payload |
|---|---|---|
| **`fetch()`** | The panel (via Kairos, 5 s) | The **full** snapshot — every connection, the whole timeline |
| **`handle()`** | The LLM (`get_network_summary`) | A **glanceable summary** — counts, throughput, firewall, top 8 connections |

The model doesn't need every socket, just the shape of what's happening — so
`handle()` slims `fetch()`'s full table down. This is the two-surface pattern
Ammit uses (direct API + tool contract), applied to a watcher.

## What fetch() assembles

```python
{
  "as_of", "privilege",
  "summary":     {established, listening, other, unresolved_pids},
  "connections": [...],   # remote endpoints, with resolved process names
  "listening":   [...],   # LISTEN sockets + bound UDP
  "traffic":     {up_bps, down_bps, per_nic, window_s},
  "dns":         {state, entries},   # resolver CACHE snapshot
  "firewall":    {state, domain, private, public},
  "timeline":    [...],   # rolling open/close events, persisted
}
```

## The design details that matter

- **Per-field degradation** (the Pheme precedent): an unresolvable PID, an empty
  DNS cache, or a failed netsh query falls back *inside* the dict. `fetch()`
  raises **only** if `psutil.net_connections()` itself throws wholesale — then
  Kairos delivers `None` and the panel holds its last good state, stale.
- **Throttled shell-outs** (the Midas cache pattern): the cheap psutil reads run
  every tick; the expensive subprocess polls don't — DNS every 30 s, firewall
  every 60 s, serving a cached block in between. The dict always carries a value,
  never a hole.
- **Traffic is derived from counters**: per-NIC up/down rate = delta of
  cumulative byte counters ÷ elapsed (same move Aura uses). First tick reads 0
  (no prior); a NIC reset yields a negative delta, clamped to 0.
- **PID 0 → `[system]`**: Windows parks orphaned TIME_WAIT sockets under the
  System Idle Process. Labeling those "System Idle Process → some host" would
  mislead, so PID 0 is relabeled to a token that tells the truth. (PID 4, the
  real kernel process, is left alone.)
- **Silent launch baseline**: the timeline diffs each snapshot against the prior
  known-set to emit open/close events — but the *first* tick seeds the baseline
  silently, so connections already established at launch don't flood in as
  "opened now." The persisted file (`argus_timeline.json`) is history; the live
  diff baseline is separate.

## Honest limits (surfaced in the UI, never hidden)

- **No per-process bandwidth** — psutil exposes none on Windows. Traffic is
  interface-level only.
- **DNS is the resolver cache** — point-in-time, TTL-bounded, not an append log.
- **5 s polling misses** any connection that opens and closes between ticks.

## Contract

`TOOL_DEFINITION` (LLM name **`get_network_summary`**, no params) + `handle()`
(never raises — degrades to `{"error": "network_unavailable"}`) + `fetch()` for
the panel. Requires `psutil` (as does Hephaestus).

## Files

| File | Committed? | Purpose |
|---|---|---|
| `tools/argus.py` | yes | The snapshot logic, both surfaces. |
| `tools/argus_peek.py` | yes | Companion inspector (quick CLI peek). |
| `panels/argus_panel.py` → `ArgusPanel` | yes | The ARGUS tab. |
| `argus_timeline.json` | **no** (runtime) | Persisted open/close timeline; missing/corrupt → empty. |

## Using it

**In the dashboard** — Moderati card → **ARGUS** tab.

**Ask Pythia** — *"what's my machine connected to?"* / *"is the firewall on?"*
routes through `get_network_summary`.

**Standalone** (takes two ticks ~1 s apart so rates and the timeline have a
prior):

```
python tools/argus.py
```

## Tests

Covered by the shared handle suite:

```
python -X utf8 -m unittest tests.test_tool_handles
```
