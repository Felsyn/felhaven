# Aether — Connectivity Status

*Anti-Legion: ONE JOB*

Aether answers **"is the world reachable?"** — two checks, nothing more: is this
machine on WiFi, and is the Anthropic API up. It renders as the connectivity
strip inside the Hephaestus tab, but it's its own tool module with its own
contract.

## The two checks

| Check | How | Result |
|---|---|---|
| **WiFi** | `netsh wlan show interfaces` (Windows) | `connected` / `disconnected` / `unknown` |
| **API** | Anthropic's public Statuspage JSON | `operational` / `degraded` / `down` / `unknown` |

```python
handle() -> {"wifi": "connected", "api_status": "operational"}
```

## Why WiFi is *tri*-state

`unknown` is deliberate and load-bearing. It means Aether **couldn't determine**
the state — the netsh probe errored, timed out, or found no wireless interface at
all (an Ethernet-only desktop, or WiFi hardware disabled). That is **not** the
same as a known `disconnected`, and conflating them would light the alarm color
red on a perfectly-online wired machine. So a wired box reads `unknown`, not
`disconnected`, and the UI stays calm.

The API check maps Statuspage's five indicators down to four states via
`_STATUS_MAP` (`minor`/`maintenance` → `degraded`, `major`/`critical` → `down`),
with anything unexpected falling to `unknown`.

## Never raises

Both checks catch their own failures and return the safe default (`unknown`), so
`handle()` and `fetch()` **cannot throw**. A connectivity monitor that crashes
when the network is down would be exactly backwards.

Two portability notes it shares with Argus: the netsh shell-out runs with
`CREATE_NO_WINDOW` so no console flashes under `pythonw`, and it parses the
English-locale "State" line.

## Contract

`TOOL_DEFINITION` (LLM name **`get_connectivity`**, no params) + `handle()` +
`fetch = handle` for Kairos. Requires `requests` (already in the stack for the
Statuspage call).

## Where it renders

Not a tab of its own — Aether is the connectivity strip **inside** `VitalsPanel`
([Hephaestus](Hephaestus.md)). Felhaven reaches it as
`moderati.hephaestus.aether` and registers it with Kairos under the worker name
`aether`, so it ticks on its own cadence even though it lives in another module's
tab.

## Files

| File | Purpose |
|---|---|
| `tools/aether.py` | The two checks + the contract. |
| (renders in) `panels/hephaestus_panel.py` | As the Aether strip inside VitalsPanel. |

## Using it

**In the dashboard** — Moderati card → **HEPHAESTUS** tab; the connectivity strip
is Aether.

**Ask Pythia** — *"am I online?"* / *"is Claude up?"* routes through
`get_connectivity`.

**Standalone**:

```
python tools/aether.py
```

## Tests

Covered by the shared handle suite (both probes mocked — no netsh, no network):

```
python -X utf8 -m unittest tests.test_tool_handles
```
