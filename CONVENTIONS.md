# CONVENTIONS — Metis Toolbox / Felhaven

> *Ex tenebris surgit lumen posteris*

The patterns every module in this stack is expected to follow, and the
deliberate exceptions. If you're about to add a tool, a panel, or a config file,
read the relevant section first — the goal is that a new module looks like it was
always here.

This is a companion to [`readme.txt`](readme.txt) (the user-facing project doc).
Both live at the **outer root** (the clone's top folder, e.g. `metis/`) alongside
`.gitignore`; all the *code* lives one level down in `metis_toolbox/`.

---

## 0. The North Star — Anti-Legion

**One module, one job.** Every file's header docstring opens with
`Anti-Legion: ONE JOB` and a one-line `Job:` statement. If you can't write that
one line without "and", the module is doing too much — split it.

**The `Job:` first sentence is the gloss.** Virgil (and any future hover/tooltip
surface) reads it verbatim, so it must stand on its own: a neutral, functional
statement of what the module *does* — written for a human reading the UI, not
addressed to Metis (`Report CPU, RAM, and disk health.`, not `Tell Metis how the
machine feels.`). One sentence, ≤ ~90 characters, ending in a period. Personality
belongs to the deity name and the README, never the gloss. Any sentences after
the first elaborate and are not shown as the gloss.

A module never reaches across this boundary: tools don't draw, panels don't
fetch, the scheduler doesn't parse data, the log watcher (Emanon) reads logs but
never acts on them. When two responsibilities tempt you into one file, that's a
second module.

---

## 1. Project layout

```
metis\                  ← the clone's outer root (repo name)
    readme.txt          ← user-facing project doc (markdown despite .txt)
    CONVENTIONS.md       ← this file
    .gitignore
    metis_toolbox/      ← THE APP ROOT. Everything below is "the repo" in handoffs.
        felhaven.py     ← dashboard entry point
        kairos.py       ← scheduler
        metis_logging.py
        theme.py        ← colors, fonts, Card + PhosphorScroll shared widgets
        __init__.py     ← brain tool registry + dispatcher
        scribe.py       ← (root-level tool, historical)
        *.json          ← config + persisted state (see §5, §10)
        tools/          ← headless logic modules
        panels/         ← Tk display surfaces
        tests/          ← stdlib unittest (see §11)
        bin/            ← external binaries (mpv.exe, yt-dlp.exe); gitignored
```

**The "app root" is `metis_toolbox/`, not the outer folder.** Config JSONs and
`bin/` live beside `felhaven.py`. A tool in `tools/` finds the app root with
`os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` (see
`tools/midas.py`, `tools/morpheus.py`). A module *at* the app root uses
`Path(__file__).resolve().parent` (see `scribe.py`, `metis_logging.py`).

**Anchor to `__file__`, never to `cwd` or `sys.argv[0]`.** This is a scar, not a
preference: an early path bug anchored to `sys.argv[0]` and broke under relative
invocation (`metis_logging.py:36` documents it). Anchoring to the file means the
stack runs identically whether launched from Felhaven or
`python tools/foo.py` from any directory — which is what makes it
flash-drive-portable.

---

## 2. Module contracts — the six flavors

Not every module exposes the same surface. Pick the flavor that fits and follow
it exactly; the surface is the contract Kairos and the brain rely on.

| Flavor | `fetch()` | `handle()` + `TOOL_DEFINITION` | Examples |
|---|---|---|---|
| **Polled + brain tool** | yes (raises on failure) | yes | `horai`, `hephaestus`, `aura`, `midas`, `aether`, `pheme` |
| **Request-driven brain tool** | no | yes | `zeno`, `eudoxus` |
| **Dashboard-only watcher** | yes | no | `emanon`, `argus` |
| **Polled status + UI-driven mutations** *(hybrid)* | yes (never raises) | no | `morpheus`, `metis` |
| **Local-only ledger / persistence** | no | no (plain functions) | `plutus`, `scribe` |
| **Pure display-logic helper** | no | no (pure functions) | `helios`, `selene` |

Rules that fall out of the table:

- **`handle()` never raises.** A brain tool that throws crashes the dispatcher.
  On any error, return `{"error": "..."}` (see `zeno.handle`, `midas` error
  codes). `handle()` is also the read path for the brain — read-only where the
  module has a separate mutate path (`scribe.handle`).
- **`fetch()` *does* raise on total failure** — that's how Kairos delivers
  `None` so the panel can show a stale/error state without crashing
  (`aura.fetch`, `midas.fetch`). **Two deliberate never-raise exceptions, each
  documented in its own docstring:** `emanon.fetch` (a watcher that crashes is
  worse than useless) and `morpheus.fetch` (an idle audio engine is a normal
  state, not a fault). `argus` is the
  in-between case: it degrades *per-field* (an unresolved connection, an empty
  DNS cache, a failed firewall query each fall back inside the returned dict) but
  still raises on a wholesale `psutil.net_connections()` failure — see
  `specs/argus.md`. If your `fetch()` doesn't raise, say *why* in the docstring.
- **Out-of-LLM-scope is a real, intentional category.** `plutus` (mutates a
  real-money ledger), `morpheus` (controls an audio engine),
  `emanon`/`argus` (read-only watchers),
  `helios`/`selene` (pure formatters) all deliberately omit `TOOL_DEFINITION` so a
  tool call can never reach them. Document the *why* — "out of LLM scope because
  …" — so nobody later "fixes" it by adding a brain tool.

---

## 3. The standard tool-module shape

Copy `tools/midas.py` or `tools/morpheus.py` as the template. A tool module has,
in order:

1. **House-style header docstring** with these sections:
   `Job:` · `Contract:` · `Source:` · `Upstream:` · `Downstream:` · `Requires:`.
   (Plus any module-specific section — e.g. `Key:`, `Playlists:`.) This carries
   the weight when the name doesn't (see `emanon.py`, whose name says nothing).
2. **`log = logging.getLogger("METIS.<name>")`** at module top. Nothing else
   touches logging config — see §6.
3. **Path constants** anchored to `__file__` (§1).
4. **`_load_<thing>()` config loaders** that return a safe empty default (`[]`)
   on *any* failure and log it — never raise, never crash the panel (see
   `midas._load_watchlist`, `morpheus._load_playlists`, `pheme._load_feeds`).
5. **Internals** prefixed `_`.
6. **Public API** — `fetch()` / `handle()` / `TOOL_DEFINITION` per the flavor.
7. **`if __name__ == "__main__":`** standalone test block that prints something
   useful (run the real thing, print the result). Every tool has one.

`TOOL_DEFINITION` shape (when the tool is a brain tool) — see `zeno.py`,
`midas.py`:

```python
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "calculate",                 # snake_case verb_noun
        "description": "What it does + WHEN the model should call it.",
        "parameters": {"type": "object", "properties": {...}, "required": [...]},
    },
}
```

**Multi-tool modules** — a module usually owns exactly one tool (one
`TOOL_DEFINITION` + one `handle()`). A module whose single job is naturally two
tightly-coupled calls may instead export the plural **`TOOL_DEFINITIONS`** (a
list of schemas) and one public function **named exactly for each tool**, in
place of `handle()`. The first is `tools/callimachus.py` (`search_web` +
`fetch_page`: search, then read one chosen result — an agentic pair, not two
jobs). The registry reads each name straight from its schema and binds it to the
like-named function, so definitions and handlers still can't drift. This is a
narrow allowance, not a loophole for §0 — if the two tools aren't one job seen
from two angles, they're two modules. Only the registry that consumes the module
needs to understand the plural export (`pythia.py` does; see §8).

---

## 4. Config over code

Anything a user might reasonably want to change without editing Python lives in a
JSON file at the app root, **ordered**, with **UI order following file order**:

- `pheme_rumormill.json` — feed rows (`id`/`label`/`url`/`format`)
- `midas_watchlist.json` — tickers (also the source for Plutus's dropdown — one
  source of truth, no code coupling)
- `morpheus_playlists.json` — playlist rows (`label`/`url`)

Adding a feed / ticker / playlist is a JSON edit, **never** a code change. The
loader tolerates a missing or malformed file by returning `[]` and logging — a
bad config degrades one panel, it never crashes the app.

---

## 5. Logging

- **Entry points call `setup_logging("<program>")` once**, before anything can
  emit (`felhaven.py` does it first thing in `__init__`). Tool modules **never**
  configure logging — they just `logging.getLogger("METIS.<name>")` and inherit.
- **The format is load-bearing.** Lines are `" | "`-delimited
  (`ts | LEVEL | logger | message`) because **Emanon parses them with a plain
  `str.split(" | ")` — no regex.** Don't change the delimiter or column order
  without updating `emanon._parse_line` in lockstep (`metis_logging._DELIM` and
  `emanon._DELIM` must match exactly).
- **Levels carry meaning Emanon collapses into a health verdict:**
  `ERROR`/`CRITICAL` → *failed*, `WARNING` → *degraded*, `INFO` → *nominal*.
  So: routine heartbeat (worker fired/ok) is **DEBUG** — it would be noise at
  INFO and would drown Emanon. **WARNING** is "expected, recoverable" (a dead
  pipe, one feed down, a stale cache). **ERROR** is "something a human should
  look at." Kairos already logs every worker fire at DEBUG and every failure at
  ERROR, so your `fetch()` rarely needs to log the happy path itself.
- *Known drift:* `scribe.py` reports save errors with `print()` instead of its
  logger, so those failures never reach Emanon. Pre-existing; fix it the way the
  rest of the stack does (`log = logging.getLogger("METIS.scribe")`) if you touch
  that file.

---

## 6. Kairos & the threading rules

`kairos.py` owns the clock. The whole concurrency model rests on one sentence:

> **The result queue is the only object shared between worker threads and the
> main thread.**

Concretely:

- **Panels are dumb display surfaces.** A panel never calls `root.after()` and
  never spawns a thread. It exposes `update(data)` and waits to be called.
- Kairos runs a single 500 ms tick loop on the main thread. Each tick it checks
  which workers are due, fires the due ones as **daemon** threads, drains the
  result queue, and calls `panel.update(data)` **on the main thread**.
- A **pile-up guard** (`_running_threads`) means a slow worker never stacks: if
  last tick's fetch is still alive, this tick skips it.
- Workers write **only** to the queue. They never touch a Tk object. Ever.
- On `fetch()` failure Kairos delivers `data=None`; **every `update(data)` must
  handle `None`** (show stale/idle, don't blank-and-crash).
- Register a worker by adding `(name, interval_seconds, "tools.x.fetch")` to
  `Kairos.WORKERS` (rough descending-interval order) and wiring the panel in
  `felhaven.py` with `kairos.register_panel(name, panel)`.

**Documented exceptions to "no `after()`, no threads"** — there are exactly
three, each justified in code:

1. `AmmitWidget` (in `horai_panel.py`) drives its countdown with `after()`.
2. Emanon's one-shot "failed" status blink uses `after()`.
3. **`MorpheusPanel` spawns one daemon thread** for the blocking `yt-dlp`
   search, because the search takes seconds and Kairos has no request-driven job
   slot. It preserves the contract: the worker thread's only shared touch is
   `self._search_q.put(rows)` (never a Tk object), `update()` drains the queue on
   the main thread, and a single-flight guard ignores a new search while one is
   in flight. See §13 and the header of `panels/morpheus_panel.py`.

If you find yourself reaching for a fourth, stop and reconsider — the answer is
almost always "give Kairos a worker."

---

## 7. Panels

- **Subclass `Card`** (`theme.py`): `super().__init__(parent, "Name — subtitle",
  C["color"])`. Pack content into `self.body`, never into `self` directly.
- **Colors and fonts come from `theme.C` and `theme.FONTS` only.** Never hardcode
  a hex value or a font tuple in a panel — if you need a new color, add it to the
  palette. This is what keeps the whole dashboard visually coherent and lets
  `rescale_fonts` work.
- **Tab bar pattern** (when a panel has tabs): `tk.Label` tabs with a 1 px
  underline `tk.Frame`; active tab = `C["text1"]` text + the panel's accent color
  on the underline, inactive = `C["text3"]` + `C["border"]`. Copy it verbatim
  from `midas_panel.py` / `pheme_panel.py` / `morpheus_panel.py`.
- **Scrolling lists**: reuse the `_ScrollFrame` (Canvas + scrollbar) pattern from
  `pheme_panel.py` / `midas_panel.py` — the frame is still copied per panel, but
  the scrollbar inside it is always `theme.PhosphorScroll`, never `tk.Scrollbar`
  (see §12 — the stock widget can't be themed on Windows). Bind the mouse wheel
  **only while hovering** (`<Enter>`/`<Leave>`) so stacked scroll frames don't
  fight over wheel events.
- **Clickable rows**: `cursor="hand2"`, `<Button-1>` for the action, and a
  hover highlight that flips `fg` to `C["amber"]` on `<Enter>` and back on
  `<Leave>` (Pheme story rows, Morpheus playlist/result rows).
- **Collapsible sub-widgets** (embedded tools like Ammit / Helios / Selene):
  collapsed by default, toggled with a `▶`/`▼` label — copy the `AmmitWidget`
  section-toggle.
- **Placeholders, not crashes**: if a tool can't run (no API key, missing
  binary), render a calm placeholder and disable controls — never raise from a
  panel constructor. See Midas `no_key` and Morpheus's missing-binary path.
- **`update(data)` is the single entry point Kairos calls.** Keep it cheap and
  idempotent; rebuild rows from scratch if that's simplest (Pheme, Plutus ledger
  do this).

---

## 8. Brain registration (only for brain tools)

To make a tool callable by Pythia (the home-chat LLM oracle), add its module to
`pythia._TOOL_MODULES`. Pythia *derives* `TOOLS` + `_DISPATCH` by reflection
(reading each module's `TOOL_DEFINITION` + `handle()`, or plural
`TOOL_DEFINITIONS` for a multi-tool module via `_module_tools()`), so the
registry can never drift from the handlers — there is no separate list to keep in
sync.

If the module is deliberately **not** a brain tool (§2), it is simply left out of
`_TOOL_MODULES` — and that omission is the feature, not an oversight. A
dashboard-only module (`emanon`, `plutus`, `helios`, `selene`) never appears
there.

**One registry now.** There used to be **two**: a **voice** registry in
`metis_toolbox/__init__.py` (`TOOLS` + `dispatch()`, which the keyword router
Apollo and `Metis.py` consumed) sat alongside Pythia's. That voice layer was
**retired with voice input** — `metis_toolbox/__init__.py` is now an empty
package marker, and `pythia.py` is the sole tool registry. The old rule "add a
web-search tool to `pythia` but *not* `__init__.py` so voice can't reach it"
no longer has anything to enforce; keeping a tool off the LLM surface now just
means keeping it out of `_TOOL_MODULES`.

---

## 9. Persistence

- Pattern: **load → mutate → save → return a snapshot dict** (see `scribe.py`,
  `tools/plutus.py`). The snapshot is JSON-serialisable and human-readable.
- **Derive, don't store, what you can recompute.** Plutus folds an append-only
  event log into positions/totals rather than storing balances — the log is the
  source of truth.
- **Personal data is gitignored**: `plutus_ledger.json`, `scribe_data.json`,
  `timer_state.json`, `felhaven_data.json`, `*.log` (see `.gitignore`). Committed
  JSON is *config* (§4); generated/personal JSON is *state* and stays local.

---

## 10. Testing

- **stdlib `unittest`, no pytest.** Run with UTF-8 forced (the glyphs and the
  delimiter need it):

  ```bash
  cd metis_toolbox
  python -X utf8 -m unittest discover -s tests -p "test_*.py"
  ```

- **Headless Tk smoke tests**: this project runs on Windows (no Xvfb). Use a real
  `tk.Tk()` root, `root.withdraw()` so no window flashes, and drive it with
  `root.update_idletasks()` instead of `mainloop()`. *No exception raised == pass.*
  Template: `tests/test_aura_panel_smoke.py`, `tests/test_morpheus_panel_smoke.py`.
- **Kairos internals (`_tick`, `_schedule_workers`) treat unknown workers as
  infinitely overdue** (`_last_run.get(name, 0)` vs. monotonic time). Any test
  calling them directly must patch both `kairos.time` and `threading.Thread`
  first, or it will spawn real threads. See `tests/test_kairos.py`.
- **Prefer captured-real fixtures over hand-built JSON.** A hand-built fixture with
  the "right" types can hide a real-world type bug (the `wttr.in` string-typed
  percentages lesson — `tests/fixtures/wttr_j1.json`). Assert against recomputed
  values so the test can't silently drift from the fixture.
- **Test the degraded paths**, not just the happy one: `None` from Kairos, a
  missing binary/key, an empty config, a single failed feed.

---

## 11. Windows & portability

- The stack targets **Windows** and aims to be **flash-drive-portable** (drop the
  folder on any PC with Python 3.10+ and run).
- **No new pip dependencies casually.** `requirements.txt` is `psutil` + `requests`
  and the bar for adding a third is high. Morpheus added *zero* (it shells out to
  `mpv`/`yt-dlp` binaries instead of importing a library). Prefer stdlib; prefer a
  bundled binary in `bin/` over a package when a binary will do.
- **Hide console windows** when spawning external processes:
  `creationflags=subprocess.CREATE_NO_WINDOW`. Reference it defensively
  (`getattr(subprocess, "CREATE_NO_WINDOW", 0)`) if the module might be imported
  off-Windows (e.g. in CI) — see `morpheus._NO_WINDOW`. This suppresses the
  *console* only — it does **not** stop a GUI/video window the binary opens
  itself (see the next bullet).
- **Neutralize a bundled binary's own user config** — the same instinct as
  anchoring Python to `__file__` instead of cwd (§1), applied one level down. If
  the external tool reads a per-user config on the host machine, disable it so
  the stack behaves identically on Obelisk, Stormcraft, or a stranger's PC in
  2034 — not subtly differently because someone customized that tool. mpv gets
  `--no-config` (ignore any host `mpv.conf`/scripts) **and** `--force-window=no`
  (never open a video output window — `--no-video` alone isn't enough when a host
  has `force-window=yes`, or a ytdl format selection pulls a video track). See
  `morpheus._ensure_mpv`. **Trade-off to watch for:** killing a tool's config can
  also disable features that key off its config *directory* — `--no-config`
  switches off mpv's default watch-later (resume) location, so Morpheus
  re-supplies it explicitly with `--watch-later-dir=<app root>/morpheus_watch_later`
  (and creates that dir itself). If you disable a binary's config, audit what
  else lived there and re-provide it.
- **Binaries resolve `bin/` first, then PATH**, so a portable copy beats a stale
  install (`morpheus._resolve`).

---

## 12. Decisions & deviations log

A running record of choices that go *against* a convention above, so future
readers know they were intentional. Append, don't rewrite.

- **`emanon.fetch()` / `morpheus.fetch()` never raise** (vs. the raise-on-failure
  norm in §2). A watcher that crashes is useless; "nothing playing" is not a
  fault. Both documented in their module docstrings.
- **`MorpheusPanel` spawns a daemon thread** (vs. "no panel spawns threads" in
  §6). The `yt-dlp` search blocks for seconds and Kairos has no request-driven
  slot. Mitigated by the queue-only-shared-object contract + a single-flight
  guard; the worker never touches Tk. *(Added 2026-06-10 with Morpheus.)*
- **Morpheus is out of LLM scope** for now (no `TOOL_DEFINITION`) even though
  play/pause would be a safe voice tool (it mutates audio, not records). Revisit
  if/when the Metis brain needs to speak to the audio engine; the module shape
  already allows bolting on a `handle()`.
- **`scribe.py` logs save errors via `print()`**, not its logger — so they don't
  reach Emanon. Pre-existing drift, noted in §5; fix when next editing the file.
- **The Register's feed is `format: "rss"` despite a `.atom` URL** — it actually
  serves RSS 2.0. Don't "correct" the extension/format mismatch.
- **A full exit clamps Plutus's cost to exactly 0/0 regardless of the sell
  arithmetic** — the dust-clamp in `positions()` zeroes both `shares` and `cost`
  once `|shares| < 1e-9`. This is a correct, desirable property (full exit =
  clean slate), but it has a testing consequence: a re-entry-*after-full-exit*
  test is structurally **blind** to the average-cost sell rule, because the clamp
  launders away any bad cost before the re-buy lands. So `tests/test_plutus.py`
  pins the sell rule with a *partial*-sell re-entry (a live lot survives, no
  clamp fires) in addition to the full-exit case. Lesson for any future
  basis/re-entry test: a **partial** sell catches sell-rule bugs that a full exit
  hides. *(Added 2026-06-10 with the Plutus tests.)*
- **`metis.fetch()` never raises** — the third never-raise case alongside
  `emanon`/`morpheus` in §2. An off voice-loop is a normal state, not a fault, so
  the header lamp simply reads grey. `tools/metis.py` mirrors Morpheus's
  external-process shape (polled status + UI-driven start/stop, no
  `TOOL_DEFINITION`) and is out of LLM scope on purpose — Metis must never start
  or stop its own loop. *(Added 2026-06-21 with Metis voice-loop control.)*
  **~~RETIRED 2026-07-10~~** — voice input was removed (see the 2026-07-10 entry
  below); `tools/metis.py`, `Metis.py`, and the voice lamp are gone, so this
  never-raise case no longer exists.
- **Naming scheme is historical + mythological** (precedent: Zeno, Hypatia).
  Historical figures of the classical world are in-bounds alongside deities —
  the names still enforce Anti-Legion single-responsibility, and "goddess of
  X" was never the actual rule. `tools/kepler.py` (Hypatia Phase 2) follows
  the same expansion. *(Added 2026-07-01 with Hypatia.)*
- **`aura._build()` grew `cloud_cover_pct`** for Hypatia's Observation
  Conditions box — Aura stays the single sky-data fetcher; Hypatia reads,
  never fetches weather. One additive dict key, everything else in Aura
  unchanged. *(Added 2026-07-01 with Hypatia.)*
- **`tools/kepler.py` is the first tool module that imports a sibling tool
  module** (`from tools import hypatia`, to reuse `_julian_date`/`_altaz`
  rather than duplicate them — the explicit point of those two functions
  taking generic arguments back in Phase 1). Every prior cross-tool reuse
  ran through a panel or `__init__.py`; this is tool-to-tool composition,
  same relationship as Midas→Plutus but at the `tools/` layer. Safe because
  both modules bind the *module object* (`from tools import X`, never
  `from tools.x import specific_function`) and only touch the other's
  attributes inside function bodies, never at their own top level — Python's
  partial-module-in-`sys.modules` behavior makes that import order-independent.
  `kepler.py`'s standalone `__main__` block needed one small addition no
  other tool has: a `sys.path` insert gated behind `if __name__ ==
  "__main__"`, since `python tools/kepler.py` alone can't otherwise see the
  `tools` package to satisfy that import — normal operation (Kairos, tests,
  `hypatia.py`'s own import) is unaffected. *(Added 2026-07-01 with Hypatia
  Phase 2.)*
- **`theme.PhosphorScroll` replaces `tk.Scrollbar` everywhere** — not stylistic
  preference but necessity: on Windows, `tk.Scrollbar` is rendered by the native
  theme engine and silently ignores `bg`/`troughcolor`/`activebackground`. The
  proof is in the repo's own history: Midas's ledger passed a complete theming
  kwarg set and Windows discarded it. Do not "simplify" back to `tk.Scrollbar`;
  the gray bars will return. Drop-in contract (`set(first, last)` + `command=`)
  so `yscrollcommand` wiring is untouched — swapping is one constructor line per
  site. The frame around it (`_ScrollFrame`) remains a §7 copied pattern:
  pattern copies, identity shares. *(Added 2026-07-02 with the scrollbar port.)*
- **`cerberus.py` deliberately bundles four facets in one module** (vs. §0's
  "one file, one job"): PIN verification, at-rest encryption (Vault), an access
  ledger, and a config manifest (Custody). They share one PIN, one KDF, and one
  RAM-only session, and the three "heads" (Vault/Custody/Ledger) are facets of a
  single identity — the guardian — not three jobs. The Cerberus handoff mandated
  one independently unit-testable core (like `sphynx.py`) rather than modules
  passing a live encryption key across import boundaries. It is the toolbox's
  **sole** encryption implementation: future consumers (Sibyl, Midas's secure
  notes) call into it rather than rolling their own crypto. **Crypto is stdlib
  only** — no `cryptography` dep (§11): PBKDF2 (`hashlib`) derives the Vault key,
  and the at-rest cipher is HMAC-SHA256 counter-mode + encrypt-then-MAC, sized to
  the Sphynx-class threat model (kids/casual snooping, not a determined
  attacker). The verify hash (`cerberus_data.json`) and the encryption KDF
  (`cerberus_vault.json`) use **separate salts in separate files** on purpose.
  *(Added 2026-07-06 with Cerberus Phase 1.)*
- **`tools/callimachus.py` is the first multi-tool module and the first tool
  wired into Pythia's registry only** (web search: `search_web` + `fetch_page`,
  plural `TOOL_DEFINITIONS` — see §2/§3/§8). Two deliberate deviations from its
  design handoff, both because the handoff's file list predated the
  `pythia.py` / `__init__.py` registry split: (1) it registers in **`pythia.py`**
  (the live dashboard-brain registry, extended with `_module_tools()` to accept a
  plural export), **not** `metis_toolbox/__init__.py` — that one is the *voice*
  registry (Apollo → `Metis.py` `dispatch`), and wiring web search there would put
  it on the voice path, which the handoff's own L9 forbids. (2) its five tunables
  live as module constants in `callimachus.py` (the aether/zeno convention),
  **not** in `metis_config.py`, which is the voice router's config (Whisper / VAD /
  Apollo) that no toolbox tool imports. The Brave API key lives in the Cerberus
  Vault only (`vault_get('brave_api_key')`), read at call time — so web search
  works only in a session where Cerberus was unlocked; a locked vault degrades to
  a `search_failed` error, never a crash. Wiring a `tools/` module to `cerberus.py`
  for the first time pulled `cerberus.py` into the `mypy --strict tools/` graph and
  surfaced 9 latent annotation errors there (bare `dict` / `list[dict]` generics +
  one `str | None` narrowing in `manifest_configs`) — fixed in lockstep
  (annotations only, zero behavior change) so "mypy at zero" stays true and is now
  actually enforced for the guardian. *(Added 2026-07-07 with Callimachus.)*
- **Themis settings module + first-run onboarding — making Felhaven
  personal-to-you, not personal-to-the author.** New root module `themis.py`
  owns `felhaven_settings.json` (lat/lon, optional weather-location, temperature
  unit, clock format). `aura`/`hypatia`/`horai` now read Themis **at fetch time**,
  not import time, so a Settings edit takes effect on the next Kairos tick — and
  `Kairos.refetch(*names)` lets the Settings-tab Save nudge those workers to
  re-fire immediately instead of waiting out Aura's 1800 s interval. Three
  deliberate calls: (1) `felhaven_settings.json` is **gitignored** even though
  it's config (§4 config is normally committed) — it's per-user *state* (§9), the
  whole point being not to ship the author's location; a missing file fail-softs
  to the old hardcoded defaults (Moundsville/`HYPATIA_LAT/LON`), which now live on
  only as those fallbacks. (2) Units are **additive**: Aura still emits canonical
  `temp_f` (the `_temp_color` tiers are °F-defined) and now *also* `temp_c` etc.
  from wttr's free Celsius fields; the display layer (aura_panel, sidebar, the two
  clocks) reads `themis` and picks — Aura stays a pure fetcher, no preference
  logic. (3) `AURA_LOCATION` is now an **env override** of the file, not the
  source of truth (kept for headless/CI). Settings live as the fifth **Moderati**
  tab (Themis), one body + one `(key,label)` per §7. **First-run onboarding:**
  the Sphynx and Cerberus PIN-hash files are no longer shipped — `git rm --cached`
  + gitignored, per-user like `cerberus_vault.json`. `sphynx.create()` is the
  module's **first-ever writer** (reversing its old "hand-authored, not
  runtime-generated" line) — the panel shows a setup screen on a missing file
  (own riddle + PIN, or a remembered "skip the gate" `disabled` flag) instead of
  failing closed; `cerberus.set_pin()` (lifted out of `_cli_setpin`, made atomic)
  backs a first-run PIN prompt in the Cerberus tab. All new writes use the
  tempfile + `os.replace` atomic pattern. `SETUP.md` §6/§7 rewritten; `README.md`
  untouched. Anti-Legion holds: the Settings panel only turns fields into a JSON
  file, Sphynx/Cerberus each gained one setup entry-path, every tool still owns
  its own behavior and merely *reads* location. *(Added 2026-07-10.)*
- **Voice input retired; Calliope refactored to output-only TTS.** Removed the
  whole voice-**input** layer — `Metis.py` (the mic/VAD/Whisper loop),
  `apollo.py` + `apollo_intents.json` (the keyword router), `metis_config.py`,
  `tools/metis.py` (the subprocess supervisor), the `MetisLamp` header widget, and
  the voice-side registry in `metis_toolbox/__init__.py` (now an empty package
  marker). **Calliope** moved into the toolbox (`metis_toolbox/calliope.py`) and
  became a single job — `speak(text)`: read Pythia's already-generated, trusted
  answer text aloud via **kokoro-onnx** (onnxruntime, no torch/transformers), in
  the Felhaven process, on demand. The trigger is a per-answer `▶ speak aloud`
  button plus a header **narration lamp** (auto-speak toggle; Calliope owns the
  flag, the GUI owns the *when*). TTS failure degrades to a logged no-op, never a
  crash. Config in `calliope_config.json`; model binaries in `kokoro_models/`
  (gitignored). *Why:* voice input was an untrusted command surface — the reason
  Apollo had a frozen 9-tool allowlist and a second registry existed at all.
  Remove it and there's nothing to route or guard, so the three-registry design
  collapsed to Pythia's one. Also dropped torch/faster-whisper from the stack.
  *(Added 2026-07-10.)*
