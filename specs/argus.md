# specs/argus.md — Argus, Network Awareness (Moderati · tab 3)

> Metis Toolbox | Anti-Legion: ONE JOB
> **Job:** Answer one question — what is my computer communicating with?

**Argus is a *read-only network awareness layer*** — visibility, not enforcement. Veins
and synapses, not threats. It never blocks, kills, or modifies a connection, never
captures packets, and never elevates. If it can't see something, it says so and moves on.

---

## Status — as built (2026-06-22)

Implemented in full, following the §8 build order (1–6). Shipped:

| File | State |
|---|---|
| `tools/argus.py` | new — `fetch()`-only data module |
| `panels/argus_panel.py` | new — `ArgusPanel(tk.Frame)` with `_ScrollFrame` + collapsible sections |
| `panels/moderati_panel.py` | `("argus","ARGUS")` tab added (accent `C["blue"]`) |
| `felhaven.py` | `ArgusPanel` registered under the `argus` worker |
| `kairos.py` | `("argus", 5, "tools.argus.fetch")` added to `WORKERS` |
| `.gitignore` | `argus_timeline.json` ignored (repo root) |
| `tests/test_argus.py` | new — 10 tests (parsers, PID-0, timeline diff, `fetch()` shape) |

**Spike result (§4), run live as a normal user before any code:** 150/150 connections
resolved — **0 `None` PIDs, 0 `AccessDenied`, 100% attribution**. The feared non-admin
PID gap did not materialize on this machine, so the panel leads with the process name as
planned; the `unresolved_pids` count / `privilege` flag / `—` fallback are all still built
(cheap robustness, and the honest-gap surface is mandated regardless).

**One deliberate deviation from this spec:** PID `0` ("System Idle Process") is relabelled
to `[system]` in `_resolve()`. On Windows, PID 0 is where the kernel parks *orphaned*
sockets — connections whose owning process already closed but whose socket lingers in
`TIME_WAIT`. Rendering "System Idle Process → <host>" misleads (the idle process isn't
talking to anything). PID `4` ("System", the real kernel process that legitimately listens
on 445 etc.) is left untouched — only PID 0 is the misleading one. Trivially reverted by
deleting the `if pid == 0` branch.

---

## 1. Where it slots in

The `2026-06-21` Moderati refactor was built for this. `ModeratiPanel` is a thin `Card`
host whose tab bar is a loop over `(key, label)` tuples, with the tab bodies refactored
into **bare `tk.Frame`s** that each keep their own `update()`, registered with Kairos under
their own worker names. Adding Argus is the third tuple plus one new worker — `kairos.py`
gains a single `WORKERS` row (it already maps worker-name → list of subscribers).

A welcome side effect: Argus logs via `logging.getLogger("METIS.argus")` in the standard
`|`-delimited format (routed to the shared `logs/felhaven.log` like every sibling logger),
so its own health surfaces in **Emanon**, the tab right next to it. The watcher watches the
watcher.

---

## 2. Module contract — Emanon flavor

Argus is the network sibling of Emanon: a passive, read-only, polled system-state surface.
It uses the **Dashboard-only watcher** contract —

- exposes **`fetch()`** (Kairos-polled), and
- **no** `handle()` / `TOOL_DEFINITION` — it never enters the LLM's tool list.

The module shape leaves room to bolt on a `get_network_summary` brain tool later (the
Helios/Selene "future contract" pattern) if Metis should ever answer *"what's my PC talking
to?"* aloud.

`fetch()` failure semantics follow the **Pheme** precedent, not the hard-raise one: a single
unresolvable connection, an empty DNS cache, or a failed firewall query degrades to a
per-field fallback *inside* the returned dict. `fetch()` only raises if
`psutil.net_connections()` itself throws wholesale — at which point Kairos delivers `None`
and the panel shows its last good state, stale.

---

## 3. Data sources — capability by capability

| Capability | Source | Reality |
|---|---|---|
| Active connections | `psutil.net_connections(kind='inet')` | Clean; PID→name resolved 100% here (§4) |
| Listening services | same call, `status=='LISTEN'` | TCP listeners; UDP shows **bound** (status `NONE`) |
| Firewall state | `netsh advfirewall show allprofiles state` | On/off per profile; Aether's netsh precedent |
| Outbound traffic | `psutil.net_io_counters(pernic=True)` | Interface-level rates only — **not** per-process |
| DNS | `ipconfig /displaydns` | Resolver **cache** snapshot, not a true history |
| Connection timeline | diff successive snapshots | Misses sub-poll-interval connections |

**Listening:** `status == 'LISTEN'` (TCP). UDP has no LISTEN state (status `NONE`), so a
UDP bound socket renders as "bound", not hidden.

**Firewall:** reading state needs **no elevation** (only *modifying* rules does, and Argus
never modifies). Non-zero exit / unparseable → `state: "unavailable"`, render `—`.

**Outbound traffic — interface-level only (honest limit).** Rate = delta of cumulative
per-NIC counters ÷ interval, prior snapshot kept in a module `_prev` (Midas cache; ephemeral,
resets on restart, never persisted). psutil exposes **no per-process network I/O on Windows**,
so "which app is uploading" is out of scope. The UI says "interface-level only · not
per-process".

**DNS — resolver cache, not history (honest limit).** Point-in-time and TTL-bounded.
`state`: `ok` | `empty` (service running, nothing cached) | `unavailable` (command failed,
e.g. DNS Client stopped). The UI labels it "resolver cache snapshot".

**Timeline — bounded rolling diff, persisted.** Diff each snapshot of *remote* connections
against the prior set (key `pid|raddr|laddr|status`) → `open`/`close` events, appended to a
bounded list (`maxlen=300`), persisted to `argus_timeline.json` **write-on-change only**
(temp-then-replace). The first tick **seeds the baseline silently** — already-established
connections did not open *now*, so no false flood at launch. The loaded file is history; the
live diff baseline is a separate in-memory set. The UI states the resolution: "polled every
5s · sub-interval connections not shown".

---

## 4. PID resolution — handled

`net_connections()` works as a normal user; `pid` can be `None`, and `Process(pid).name()`
can raise `AccessDenied` (even when elevated, on Windows). Handled via
`as_dict(attrs=['name'], ad_value='—')`, `try/except (NoSuchProcess, AccessDenied)`, a
module-level pid→name **memo** (pruned to live PIDs each tick — bounds growth and prevents a
reused PID showing a stale name), and an honest `summary.unresolved_pids` count +
`privilege` flag. Misses are not cached, so they retry next tick. Spike confirmed 100%
attribution on this machine (see Status).

---

## 5. `fetch()` output shape + cadence

`fetch()` returns display-*ready* numbers/enums/lists; the **panel** humanizes (bytes →
`24.5 KB/s`, epoch → `22s ago`, status → colored dot). See the dict shape in the handoff
(unchanged). **Cadence — one `argus` worker (5 s), internal throttle:** psutil reads every
tick; `ipconfig /displaydns` throttled ~30 s; `netsh` firewall throttled ~60 s. The throttle
lives in `argus.py` via last-run timestamps, returning the last cached block (with its own
`as_of`) in between — the Midas cache + stale-fallback pattern.

---

## 6. The panel — `ArgusPanel(tk.Frame)`

A bare `tk.Frame` with its own `update(data)` (no `Card`, no `set_summary`). A glanceable
summary line (`12 established · 7 listening · ▲24.5 KB/s ▼1.3 MB/s · 🛡 on`, with a
level-coded dot) over a single `_ScrollFrame` of collapsible `▶/▼` sections (Connections —
expanded by default — Listening, Traffic, DNS cache, Firewall, Timeline). `_ScrollFrame` is
copied per the one-per-panel house rule (CONVENTIONS §7). Collapsed sections render lazily
on expand (and only when open), so the tab opens glanceable, not as a wall of rows. No
`after()` outside Kairos; palette/fonts from `theme.py`.

---

## 7. Out of scope — hard lines

No enforcement (kill/block/throttle/rule-writing). No packet capture / DPI. No per-process
bandwidth (psutil can't, on Windows). No admin elevation, ever. Not a brain tool (v1). No
GeoIP / reverse-DNS enrichment — connections render as raw `IP:port`.

---

## 8. Decisions — locked

| Decision | Choice |
|---|---|
| Brain tool | **No** — `fetch()`-only; future `get_network_summary` left open by the module shape |
| Timeline persistence | **`argus_timeline.json`** — gitignored, bounded, write-on-change, silent launch baseline |
| raddr enrichment | **None in v1** — raw `IP:port` |
| Cadence | **5 s** psutil; DNS ~30 s, firewall ~60 s — one worker, in-module throttle |
| Panel class | **`ArgusPanel`** — deity-named, matches `EmanonPanel` |
