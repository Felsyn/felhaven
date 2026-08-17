# CONVENTIONS — Metis Toolbox / Felhaven

> *Ex tenebris surgit lumen posteris*

The patterns every module follows, and the deliberate exceptions. If you're about
to add a tool, a panel, or a config file, read the relevant section first — the
goal is that a new module looks like it was always here.

Companion to [`README.md`](README.md) (what the project is) and
[`CHANGELOG.md`](CHANGELOG.md) (dated history). Per-module detail lives in each
module's own docstring, indexed at
[`README_PANTHEON/`](metis_toolbox/README_PANTHEON/README.md).

---

## 0. The North Star — Anti-Legion

**One module, one job.** Every file's header docstring opens with
`Anti-Legion: ONE JOB` and a one-line `Job:` statement. If you can't write that
line without "and", the module is doing too much — split it.

**The `Job:` first sentence is the gloss.** Any hover/tooltip surface reads it
verbatim, so it must stand alone: a neutral, functional statement of what the
module *does*, written for a human reading the UI. One sentence, ≤ ~90
characters, ending in a period. Personality belongs to the deity name, never the
gloss. Sentences after the first elaborate and are not shown.

A module never reaches across this boundary: tools don't draw, panels don't
fetch, the scheduler doesn't parse data, the log watcher reads logs but never
acts on them. When two responsibilities tempt you into one file, that's a second
module.

---

## 1. Project layout

The clone's outer root holds the docs and `.gitignore`. **The "app root" is
`metis_toolbox/`, not the outer folder** — config JSONs and `bin/` live beside
`felhaven.py`. Below it: `tools/` (headless logic), `panels/` (Tk display
surfaces), `config/` (shipped templates, §4), `tests/` (§10).

A tool in `tools/` finds the app root with
`os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`. A module *at* the
app root uses `Path(__file__).resolve().parent`.

**Anchor to `__file__`, never to `cwd` or `sys.argv[0]`.** This is a scar, not a
preference: an early path bug anchored to `sys.argv[0]` and broke under relative
invocation (`metis_logging.py:36` documents it). Anchoring to the file means the
stack runs identically whether launched from Felhaven or `python tools/foo.py`
from any directory — which is what makes it flash-drive-portable.

---

## 2. Module contracts — the flavors

Not every module exposes the same surface. Pick the flavor that fits and follow
it exactly; the surface is the contract Kairos and Pythia rely on.

| Flavor | `fetch()` | `handle()` + `TOOL_DEFINITION` |
|---|---|---|
| **Polled + brain tool** | yes (raises on failure) | yes |
| **Request-driven brain tool** | no | yes (singular, or plural `TOOL_DEFINITIONS`) |
| **Dashboard-only watcher** | yes | no |
| **Polled status + UI-driven mutations** *(hybrid)* | yes (never raises) | optional |
| **Local-only ledger / persistence** | no | no (plain functions) |
| **Device / infrastructure authority** | no | no |

Membership is not listed here on purpose — it drifts, and it already has. The
ground truth is `pythia._TOOL_MODULES` plus `grep TOOL_DEFINITION tools/`, and
`tests/test_pantheon_docs.py` holds the docs to it.

Rules that fall out of the table:

- **`handle()` never raises.** A brain tool that throws crashes the dispatcher.
  On any error return `{"error": "..."}`. It is also the read path where a module
  has a separate mutate path.
- **`fetch()` *does* raise on total failure** — that's how Kairos delivers `None`
  so the panel shows a stale state without crashing. Deliberate never-raise
  exceptions exist (a watcher that crashes is worse than useless; an idle audio
  engine is a normal state, not a fault) — **if your `fetch()` doesn't raise, say
  why in the docstring.** Per-field degradation is a third option: fall back
  inside the returned dict, and raise only on wholesale failure.
- **Out-of-LLM-scope is a real, intentional category.** A module that mutates a
  real-money ledger, or whose job is a panel verdict rather than an answer,
  deliberately omits `TOOL_DEFINITION` so a tool call can never reach it.
  Document the *why* — "out of LLM scope because …" — so nobody later "fixes" it.
  **The reverse happens too and is not a violation:** modules get promoted into
  the brain when a real use appears. Both directions belong in the decisions log.
  The *category* is deliberate; membership is not frozen.

### The error vocabulary

`{"error": ...}` was the whole rule for a long time, and six competing styles
grew inside it. The identifier is **read by the model and branched on by
panels**, so its shape is a contract, not a formatting preference. It is one of:

- a **bare slug** — `^[a-z][a-z0-9_]*$`, e.g. `no_results`, `vault_locked`
- **slug + human detail** — `^[a-z][a-z0-9_]*: .+$`, e.g.
  `too_large: 4.2 MB exceeds the 1 MB cap`

The **slug is the stable part**; the text after `: ` is for humans and may be
reworded freely. `tools/herodotus.py` is the reference implementation.

- **Never a bare sentence.** `"search failed"` → `search_failed`.
- **Never raw exception text.** `str(exc)` can carry paths, URLs, or key
  material into the LLM's context and onto the screen. At most
  `f"slug: {type(exc).__name__}"` — the class name, never the message.
- **Never a computed-only string.** `f"HTTP {status}"` → `f"http_error: {status}"`.
  In an f-string the slug must come **before** any interpolation, so it stays
  greppable and matchable.
- Extra keys beside `error` are fine — `zeno` returns `expression` and `steps`
  with its errors — but they never replace the slug.
- Three or more codes in one module: lift them to `ERR_*` module constants
  (`tools/midas.py`). Fewer than three: inline literals are fine.

**Panels map the slug to human text**, never render it raw — the
`_ERROR_TEXT` dict in `panels/echo_panel.py`, including its fallback to the raw
code so an unmapped error is surfaced rather than swallowed.

`tests/test_tool_output_contract.py` enforces all of this by parsing the AST
(a text search cannot tell `str(e)` from a literal). Modules that predate the
rule sit in its `_EXPECTED_VIOLATIONS` allowlist and come off as each is
audited; a stale entry fails just as loudly as a new violation.

---

## 3. The standard tool-module shape

Copy `tools/midas.py` or `tools/morpheus.py` as the template. In order:

1. **House-style header docstring**: `Job:` · `Contract:` · `Source:` ·
   `Upstream:` · `Downstream:` · `Requires:`, plus any module-specific section,
   and `V0.02:` last once the module has been audited. This carries the weight
   when the name doesn't.

   **The `V0.02:` field is the audit marker**, and it is the *only* version a
   file names — it replaces any other version line the module carried. Both a
   tool and its panel get it in the same session:

   ```
   V0.02:       Audited 2026-07-21 — errors normalized, contract verified,
                hermetic tests added.
   ```

   Its absence means "not yet audited", so no test asserts every module has one.
   A version *other* than `V0.02` does fail
   (`tests/test_tool_output_contract.py`) — one file, one version, and it is
   this one. A version number nobody maintains is a promise you can't keep; the
   retired `(FOE v.01)` label is the scar (§12).
2. **`log = logging.getLogger("METIS.<name>")`** at module top. Nothing else
   touches logging config — see §5.
3. **Path constants** anchored to `__file__` (§1).
4. **`_load_<thing>()` config loaders** that return a safe empty default on *any*
   failure and log it — never raise, never crash the panel.
5. **Internals** prefixed `_`.
6. **Public API** — `fetch()` / `handle()` / `TOOL_DEFINITION` per the flavor.
7. **`if __name__ == "__main__":`** standalone block that runs the real thing and
   prints the result. Every tool has one.

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

**Multi-tool modules** — a module usually owns exactly one tool. A module whose
single job is naturally two tightly-coupled calls may instead export the plural
**`TOOL_DEFINITIONS`** (a list of schemas) and one public function **named exactly
for each tool**, in place of `handle()`. The registry reads each name straight
from its schema and binds it to the like-named function, so definitions and
handlers can't drift. This is a narrow allowance, not a loophole for §0 — if the
two tools aren't one job seen from two angles, they're two modules.

---

## 4. Config over code

Anything a user might reasonably want to change without editing Python lives in a
JSON file under **`metis_toolbox/config/`** (not the app root), **ordered**, with
**UI order following file order**. Adding a feed / ticker / playlist is a JSON
edit, **never** a code change. The loader tolerates a missing or malformed file by
returning `[]` and logging — a bad config degrades one panel, never the app.

**"Config" and "committed" are not synonyms.** Most of `config/` ships, but some
files there are gitignored: they have a config *shape* (ordered rows a user edits
by hand) and personal *content*. Ask "would I want a stranger cloning this to
inherit my copy?" — if no, it's state, wherever it sits (§9). A fresh clone
starting with no playlists is the design, not a missing file; don't "fix" it by
committing one.

---

## 5. Logging

- **Entry points call `setup_logging("<program>")` once**, before anything can
  emit. Tool modules **never** configure logging — they just
  `logging.getLogger("METIS.<name>")` and inherit.
- **The format is load-bearing.** Lines are `" | "`-delimited
  (`ts | LEVEL | logger | message`) because **Emanon parses them with a plain
  `str.split(" | ")` — no regex.** Don't change the delimiter or column order
  without updating `emanon._parse_line` in lockstep (`metis_logging._DELIM` and
  `emanon._DELIM` must match exactly).
- **Levels carry meaning Emanon collapses into a health verdict:**
  `ERROR`/`CRITICAL` → *failed*, `WARNING` → *degraded*, `INFO` → *nominal*. So
  routine heartbeat is **DEBUG** — it would drown Emanon at INFO. **WARNING** is
  "expected, recoverable." **ERROR** is "a human should look at this." Kairos
  already logs every worker fire at DEBUG and every failure at ERROR, so your
  `fetch()` rarely needs to log the happy path.

---

## 6. Kairos & the threading rules

`kairos.py` owns the clock. The whole concurrency model rests on one sentence:

> **The result queue is the only object shared between worker threads and the
> main thread.**

- **Panels are dumb display surfaces.** A panel never spawns a thread and never
  runs an `after()`-driven polling or timing **loop**; it exposes `update(data)`
  and waits to be called. A *single* one-shot `after()` for transient UI
  feedback — a status line that self-clears, a glyph that resets after a beat, a
  latency-masking filler — is fine: it fires once, on the main thread, touching
  only this panel's own widgets.
- Kairos runs a single 500 ms tick loop on the main thread. Each tick it fires
  due workers as **daemon** threads, drains the result queue, and calls
  `panel.update(data)` **on the main thread**.
- A **pile-up guard** means a slow worker never stacks: if last tick's fetch is
  still alive, this tick skips it.
- Workers write **only** to the queue. They never touch a Tk object. Ever.
- On `fetch()` failure Kairos delivers `data=None`; **every `update(data)` must
  handle `None`** (show stale/idle, don't blank-and-crash).
- Register a worker by adding `(name, interval_seconds, "tools.x.fetch")` to
  `Kairos.WORKERS` and wiring the panel in `felhaven.py` with
  `kairos.register_panel(name, panel)`.

**Documented exceptions to "no threads, no `after()` loops"** exist, each
justified in code — Emanon's bounded, self-terminating status-blink chain,
`MorpheusPanel`'s one daemon thread for the blocking `yt-dlp` search (see §12),
`EchoPanel`'s one daemon thread for a whole-document conversion plus its bounded,
self-terminating `after()` drain chain (Echo is request-driven, not a Kairos
worker, so it has no tick to drain the result on), and **`HomePanel`'s one daemon
thread for the slow `pythia.ask()` call plus a persistent main-thread
`after()`-poll to drain it** (HomePanel is event-driven — you press Enter — not a
Kairos worker, so it runs its own standing 60 ms poll instead of riding the tick;
the poll is cancelled in the panel's `destroy()` — see the teardown rule below).
Each preserves the contract: the worker's only shared touch is a queue
`put`, `update()` / the drain runs on the main thread, and a single-flight guard
ignores a second job while one is in flight.

`AmmitWidget`'s countdown is **not** among them, despite once being listed here:
it holds no `after()` and spawns no thread. `HoraiPanel.update()` refreshes it on
horai's 1 s Kairos tick — the compliant path, which is exactly why it needs no
exception.

If you find yourself reaching for a new one, stop — the answer is almost always
"give Kairos a worker."

**Every `after()` you schedule, you cancel on teardown.** Keep the id and cancel
it in a `destroy()` override before `super().destroy()` — the `EchoPanel` /
`ArtemisPanel` shape. This applies to the permitted one-shots too, not just the
chains above.

A `winfo_exists()` guard inside the callback is *not* a substitute. Tk deletes a
widget's registered Tcl commands as it destroys the widget, so an
already-scheduled after script has no command left to call: Tcl reports
`invalid command name "..._poll"` on stderr and Python is never reached. The
guard only covers a callback that is already mid-flight (a glyph destroyed
while the panel lives); it can do nothing once the panel itself is gone.
Cancelling the pending id is the only thing that actually ends the chain.

Two shapes, picked by how many can be outstanding at once:

- **One at a time** → a single `self._xxx_after_id` attribute (`EchoPanel`'s
  drain, `HomePanel`'s poll and latency filler). Re-arm through a helper that
  cancels the stored id first: a callback can be entered *without* its timer
  having fired — the smoke tests call `panel._poll()` directly — and re-arming
  blind orphans an id that `destroy()` can then never reach.
- **Several at a time** → a `self._pending_after` set plus an `_after_once()`
  helper that discards its own id when it fires (`MorpheusPanel`,
  `OrpheusPanel`, where every armed remove glyph owns its own confirm window).
  A single slot would forget all but the last.

Cancelling an id that already fired is a harmless no-op, so neither shape needs
to track whether the timer is still live.

---

## 7. Panels

- **Subclass `Card`** (`theme.py`). Pack content into `self.body`, never into
  `self` directly.
- **Colors and fonts come from `theme.C` and `theme.FONTS` only.** Never hardcode
  a hex value or a font tuple — if you need a new color, add it to the palette.
  This is what keeps the dashboard coherent and lets `rescale_fonts` work.
- **Tab bar pattern**: `tk.Label` tabs with a 1 px underline `tk.Frame`; active =
  `C["text1"]` text + the panel's accent on the underline, inactive =
  `C["text3"]` + `C["border"]`. Copy it verbatim from an existing tabbed panel.
- **Scrolling lists**: reuse the `_ScrollFrame` (Canvas + scrollbar) pattern — the
  frame is copied per panel, but the scrollbar inside it is always
  `theme.PhosphorScroll`, never `tk.Scrollbar` (§12 — the stock widget can't be
  themed on Windows). Bind the mouse wheel **only while hovering**
  (`<Enter>`/`<Leave>`) so stacked scroll frames don't fight over wheel events.
- **Clickable rows**: `cursor="hand2"`, `<Button-1>` for the action, and a hover
  highlight flipping `fg` to `C["amber"]` on `<Enter>` and back on `<Leave>`.
- **Collapsible sub-widgets**: collapsed by default, toggled with a `▶`/`▼`
  label — copy the `AmmitWidget` section-toggle.
- **Grouping views in the nav rail**: a family of related views goes in
  `Sidebar._GROUPS` (`panels/sidebar.py`), never in a tab host. The group header
  is a `GroupRow` that selects nothing; each child stays a full entry in
  `felhaven._views` with the whole content area and the ordinary lit-row active
  state. Adding one is a child tuple plus a `_views` entry —
  `test_sidebar_view_parity` fails if you do only one of the two.
- **Placeholders, not crashes**: if a tool can't run (no API key, missing
  binary), render a calm placeholder and disable controls — never raise from a
  panel constructor.
- **`update(data)` is the single entry point Kairos calls.** Keep it cheap and
  idempotent; rebuild rows from scratch if that's simplest.

---

## 8. Brain registration (only for brain tools)

To make a tool callable by Pythia, add its module to `pythia._TOOL_MODULES`.
Pythia *derives* `TOOLS` + `_DISPATCH` by reflection (reading each module's
`TOOL_DEFINITION` + `handle()`, or plural `TOOL_DEFINITIONS` via
`_module_tools()`), so the registry can never drift from the handlers — there is
no separate list to keep in sync.

If the module is deliberately **not** a brain tool (§2), it is simply left out of
`_TOOL_MODULES`, and that omission is the feature. **`_TOOL_MODULES` is the ground
truth**; anything restating it is a copy that will drift, so don't add one.
`tests/test_pantheon_docs.py` enforces that the module docs stay in step.

Pythia is the **only** registry. A second, voice-side registry existed until
voice input was retired (§12); `metis_toolbox/__init__.py` is now an empty package
marker. Keeping a tool off the LLM surface means keeping it out of
`_TOOL_MODULES`, nothing more.

---

## 9. Persistence

- Pattern: **load → mutate → save → return a snapshot dict**. The snapshot is
  JSON-serialisable and human-readable.
- **Derive, don't store, what you can recompute.** Plutus folds an append-only
  event log into positions/totals rather than storing balances — the log is the
  source of truth.
- **Atomic writes**: tempfile in the same directory + `os.replace`, so a crash
  mid-write can never truncate the original.
- **Personal data is gitignored.** `.gitignore` is the list; committed JSON is
  *config* (§4), generated or personal JSON is *state* and stays local.

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
- **Kairos internals treat unknown workers as infinitely overdue.** Any test
  calling `_tick`/`_schedule_workers` directly must patch both `kairos.time` and
  `threading.Thread` first, or it will spawn real threads.
- **Prefer captured-real fixtures over hand-built JSON.** A hand-built fixture
  with the "right" types can hide a real-world type bug (the `wttr.in`
  string-typed percentages lesson). Assert against recomputed values so the test
  can't silently drift from the fixture.
- **Test the degraded paths**, not just the happy one: `None` from Kairos, a
  missing binary/key, an empty config, a single failed feed.
- **Guard invariants in lockstep, never with a hardcoded count.** A count rots
  red for the wrong reason. Assert that two live sources agree — the shipped
  config against the code defaults, `TOOLS` against `_DISPATCH`, the docs against
  `_TOOL_MODULES`. **And confirm the guard actually fails red before trusting
  it**: a degradation path nothing exercises isn't a safety net, it's a comment.
- **Assert against the repo, not the working directory.** A check that reads the
  filesystem passes on a machine where gitignored state happens to exist and
  fails only in CI. `test_pantheon_docs.py` shipped with exactly that bug — it
  had been verified to fail red, but only against scenarios the author's own
  checkout could produce. Ask what a *fresh clone* has: if the answer differs
  from your disk, check `git ls-files`, not `os.path.exists`.

---

## 11. Windows & portability

- The stack targets **Windows** and aims to be **flash-drive-portable**.
- **No new pip dependencies casually.** The bar is high; Morpheus added *zero*
  (it shells out to binaries instead of importing a library). Prefer stdlib;
  prefer a bundled binary in `bin/` over a package when a binary will do.
- **Hide console windows** when spawning external processes:
  `creationflags=subprocess.CREATE_NO_WINDOW`, referenced defensively
  (`getattr(subprocess, "CREATE_NO_WINDOW", 0)`) if the module might be imported
  off-Windows. This suppresses the *console* only — not a GUI window the binary
  opens itself.
- **Neutralize a bundled binary's own user config** — the same instinct as
  anchoring to `__file__` (§1), one level down. If the external tool reads a
  per-user config on the host, disable it so the stack behaves identically
  everywhere. **Trade-off to watch for:** killing a tool's config can disable
  features that key off its config *directory* — `--no-config` switches off mpv's
  default watch-later location, so Morpheus re-supplies it explicitly. If you
  disable a binary's config, audit what else lived there and re-provide it.
- **Binaries resolve `bin/` first, then PATH**, so a portable copy beats a stale
  install.

---

## 12. Decisions & deviations log

Choices that go *against* a convention above, so future readers know they were
intentional. **Decision · why · date.** Append, don't rewrite. **This is where
the reasoning lives** — [`CHANGELOG.md`](CHANGELOG.md) records only what changed
and when, one line per change, so a decision worth keeping must be written here.

- **`emanon.fetch()` / `morpheus.fetch()` never raise** (vs. §2's raise-on-failure
  norm). A watcher that crashes is useless; "nothing playing" is not a fault.
- **`MorpheusPanel` spawns a daemon thread** (vs. §6's "no panel spawns threads").
  The `yt-dlp` search blocks for seconds and Kairos has no request-driven slot.
  Mitigated by the queue-only contract + a single-flight guard. *(2026-06-10.)*
- **A full exit clamps Plutus's cost to exactly 0/0**, which is correct but makes
  a re-entry-after-*full*-exit test structurally blind to the average-cost sell
  rule — the clamp launders away any bad cost before the re-buy. So the sell rule
  is pinned with a **partial**-sell re-entry instead. Lesson for any future
  basis/re-entry test: a partial sell catches bugs a full exit hides.
  *(2026-06-10.)*
- **The Register's feed is `format: "rss"` despite a `.atom` URL** — it actually
  serves RSS 2.0. Don't "correct" the mismatch.
- **Naming scheme is historical + mythological.** Historical figures of the
  classical world are in-bounds alongside deities; the names still enforce
  single-responsibility, and "goddess of X" was never the rule. *(2026-07-01.)*
- **`aura._build()` grew `cloud_cover_pct`** for Hypatia's observing conditions —
  Aura stays the single sky-data fetcher; Hypatia reads, never fetches weather.
  *(2026-07-01.)*
- **`tools/kepler.py` imports a sibling tool module** (`from tools import
  hypatia`) to reuse `_julian_date`/`_altaz` rather than duplicate them. Safe
  because both bind the *module object* and only touch the other's attributes
  inside function bodies, never at their own top level — Python's
  partial-module-in-`sys.modules` behavior makes that import order-independent.
  Needs a `sys.path` insert gated behind `if __name__ == "__main__"` for
  standalone runs. *(2026-07-01.)*
- **`theme.PhosphorScroll` replaces `tk.Scrollbar` everywhere** — necessity, not
  taste: on Windows `tk.Scrollbar` is rendered by the native theme engine and
  silently ignores `bg`/`troughcolor`/`activebackground`. The proof is in this
  repo's history — Midas's ledger passed a complete theming kwarg set and Windows
  discarded it. **Do not "simplify" back to `tk.Scrollbar`; the gray bars will
  return.** Drop-in contract (`set(first, last)` + `command=`). *(2026-07-02.)*
- **`cerberus.py` deliberately bundles four facets** (PIN verification, Vault
  encryption, Ledger, Custody manifest) vs. §0. They share one PIN, one KDF, and
  one RAM-only session — facets of a single identity, not four jobs. It is the
  toolbox's **sole** encryption implementation; future consumers call into it
  rather than rolling their own. **Crypto is stdlib only** (§11): PBKDF2 for the
  key, HMAC-SHA256 counter-mode + encrypt-then-MAC at rest, sized to the threat
  model (casual snooping, not a determined attacker). The verify hash and the
  encryption KDF use **separate salts in separate files** on purpose.
  *(2026-07-06.)*
- **`tools/callimachus.py` is the first multi-tool module** (plural
  `TOOL_DEFINITIONS`, §2/§3) and the first tool wired into Pythia's registry only.
  Its Brave key lives in the Cerberus Vault, read at call time — so web search
  works only in a session where Cerberus was unlocked; a locked vault degrades to
  an error, never a crash. Wiring a `tools/` module to `cerberus.py` pulled the
  guardian into the `mypy --strict tools/` graph and surfaced 9 latent annotation
  errors, fixed in lockstep. *(2026-07-07.)*
- **Themis owns settings; the PIN files became per-user.** `aura`/`hypatia`/`horai`
  read Themis **at fetch time**, not import time, so a Settings edit applies on the
  next tick, and `Kairos.refetch(*names)` lets Save nudge them immediately.
  `felhaven_settings.json` is gitignored **even though it's config** — the whole
  point is not to ship the author's location (§9). Units are additive: Aura still
  emits canonical `temp_f` and now also `temp_c`; the display layer picks, so Aura
  stays a pure fetcher. `AURA_LOCATION` became an env *override*, not the source of
  truth. The Sphynx/Cerberus PIN files are no longer shipped — first run walks you
  through setting your own. *(2026-07-10.)*
- **Voice input retired; Calliope refactored to output-only TTS.** *Why:* voice
  input was the only untrusted command surface — the sole reason a keyword router
  had a frozen 9-tool allowlist and a second registry existed. Remove it and there
  is nothing to route or guard, so three registries collapsed to Pythia's one.
  Also dropped torch/faster-whisper for kokoro-onnx. *(2026-07-10.)*
- **`harmonia.py` owns the audio device** — a new "Device / infrastructure
  authority" flavor (§2). Three things wanted to make sound and nothing owned the
  device; **a lock across them cannot work**, because a Python mutex serializes
  *commands*, not *audio*, and mpv holds its own device handle in a separate
  process regardless. `play()` calls `morpheus.stop()` first, one direction only.
  **Deliberate behavior change:** `harmonia.stop()` calls `sd.stop()`, so barge-in
  is instant — and because Harmonia has no channels, a new question also silences
  an in-progress Orpheus briefing. That is the intended cost of one stream, one
  truth. *(2026-07-16.)*
- **Correction: Orpheus shipped decoding at 48 kHz stereo instead of 24 kHz
  mono** — 4× the RAM for zero new information (measured: 147 MB vs 37 MB on a
  real 6m41s file). The code matched its written handoff exactly; the handoff was
  stale, because an amendment was given conversationally and never reached the
  doc. **A process gap, not a judgment error** — and `harmonia.py`'s docstring
  repeated the same wrong number, so it couldn't have caught it. The constants are
  now framed as what they are: Orpheus's explicit *assumption* that `local_audio/`
  holds only Echo's output. *(2026-07-17.)*
- **Cerberus's session is now shared across tabs.** Reading the Finnhub key at
  call time forced Midas's gate off `verify()` (a bare hash check) onto `unlock()`
  (a real session) — and since the session key is module-level, unlocking either
  the Midas gate or the Cerberus tab opens both. "One guardian, one session" is
  more consistent with Cerberus's sole-authority role than the split it replaced,
  but it is a real behavior change: any future Vault-reading gate must call
  `unlock()`, not `verify()`, and must add a liveness check on its own tick to
  re-seal if the session dies elsewhere. *(2026-07-17.)*
- **Correction: five tools were promoted to brain tools and this file didn't say
  so.** `argus`, `helios`, `selene`, `ammit`, and `hypatia` gained real contracts
  on 2026-07-06; their docstrings were fixed the next day (`41fac5f`) but §2, §8,
  and `README.md` kept describing them as excluded for ~two weeks. **The process
  lesson:** the docstrings were fixed and the conventions weren't, because nothing
  linked them — a code-local fix felt complete. A hand-maintained copy of a list
  that changes will drift. *(2026-07-20.)*
- **The output guard now rejects `f"slug: {exc}"`** — a literal slug head no
  longer excuses splicing the caught exception in after it; that is `str(exc)`
  behind a hat. Found in session 1 of the walkthrough, amended in session 2.
  Deliberately narrow: only a bare `{exc}` / `{str(exc)}` is flagged, because
  `{exc.detail}` (zeno's own class, safe by construction) and `{exc.reason}`
  (a foreign message) are indistinguishable statically — attribute reads stay a
  human-audit concern on each module's session. The amendment immediately
  caught `herodotus` interpolating `OSError`s (archive paths on screen) —
  the §2 *reference implementation*, which is exactly why the rule is a test
  and not a sentence in this file. Herodotus and callimachus sit in the
  allowlist until sessions 12/13. *(2026-07-23.)*
- **Docs trimmed to one job per layer; module inventories deleted rather than
  corrected.** The entry above prescribed treating a promotion as a three-file
  change "until something enforces it." Enforcing it is the better answer, so the
  copies were removed instead: §2 and §8 no longer list which modules are brain
  tools, the per-module pages became fixed 5-field stubs, and
  `tests/test_pantheon_docs.py` now asserts the docs match `pythia._TOOL_MODULES`
  and `_DISPATCH`. **The standing rule:** if a line asserts something derivable
  from the code, delete it or generate it — prose can only be wrong about that.
  *(2026-07-20.)*
- **`midas.fetch()` does not raise when every ticker failed *only* because the
  Cerberus vault is locked** (vs §2's raise-on-total-failure). A sealed vault is
  the normal pre-unlock state — the panel starts sealed and Kairos polls Midas
  every 60 s regardless — so raising it wrote an ERROR each minute (609 in one
  live log) and made Emanon read Midas as *failed*. Genuine failures
  (`no_key`/`fetch_failed`/`no_data`) still raise, and the message now names the
  dominant slug (`all tickers failed: no_key`) so the log says *why*. Found in
  walkthrough session 7, next to a worse sibling: `_fetch_one` logged the raw
  request exception, whose URL carries `?token=<finnhub key>`, so the
  Vault-protected key was leaking into `logs/*.log` (which Emanon renders on
  screen). Now it logs `type(e).__name__` + the HTTP status only — never the
  message. The output-contract guard (`test_tool_output_contract.py`) inspects
  `{"error": …}` return values, not `log.*()` calls, so it could not have caught
  this; a log-call scan is a possible future guard, flagged for the logging
  session. *(2026-07-24.)*
- **Cerberus's Ledger is shared mutable state across threads, guarded by its own
  lock — a second shared channel §6 doesn't name.** §6 says the Kairos result
  queue is the *only* object shared between worker threads and the main thread,
  but Midas reads the Finnhub key from a `ThreadPoolExecutor(max_workers=5)`
  inside its `fetch()`, and every `vault_get` logs a Ledger entry — so up to five
  worker threads (plus the panel, on the main thread) do a load-modify-write on
  `cerberus_ledger.json` at once. The old `_ledger_write` was a bare
  `open("w")` truncate+rewrite with no lock: interleaved writes corrupted the
  file (a reader caught it mid-truncate → `Expecting value: line 1 column 1`; two
  writes clobbered → `Extra data`) and racing count-bumps were lost, all visible
  in `metis.log`. Fix: a module-level `_LEDGER_LOCK` around the whole
  load→mutate→write (including the `_SESSION_READS` dedupe check-then-set), and
  all of Cerberus's files now write atomically (tempfile + `os.replace`, §9) so a
  lockless reader still never sees a half-written file. The Vault stays
  lock-free — the pool only *reads* it; `vault_set` is main-thread/CLI only —
  but its write is atomic too, for §9 crash-safety (it holds the actual
  secrets). This doesn't break §6's model: worker threads still touch no Tk
  object and reach the main thread only through the queue; the Ledger is a
  private file each caller serializes on, not a hand-off channel. Found and fixed
  in walkthrough session 8. *(2026-07-24.)*
- **§6 narrowed: a single one-shot `after()` for transient UI feedback is no
  longer a deviation.** The rule had quietly accumulated harmless one-shot timers
  — MorpheusPanel's two-click-remove confirm reset, its status-line and glyph
  auto-clears (flagged walkthrough s17), and HomePanel's latency-masking filler —
  all of which fire once on the main thread and touch only their own panel's
  widgets. The hazards §6 actually guards are background threads and standing
  poll/timing loops, so the rule now targets those. Threads and
  self-re-arming/persistent `after()` work stay documented exceptions: HomePanel's
  per-question worker + 60 ms poll (added this session — the §6 IOU standing since
  walkthrough s6), Echo's drain chain, Emanon's blink chain, Morpheus's search
  thread. Retires the s17 MorpheusPanel flag. *(2026-07-26.)*
- **Harmonia owns one persistent `sd.OutputStream`; barge-in is a buffer flush,
  not `sd.stop()`.** The 2026-07-16 entry above had `harmonia.stop()` call
  `sd.stop()` for instant barge-in. That used sounddevice's module-level
  convenience functions — `sd.play()`/`sd.wait()` on the play worker, `sd.stop()`
  on the UI thread — which share ONE global PortAudio stream and are documented as
  unsafe for concurrent multithreaded use. Barge-in (a new question asked while
  narration was still speaking) raced them and **segfaulted the process with no
  traceback, no log, and no OOM dialog** — diagnosed from that exact silent-vanish
  signature, not from a repro. Harmonia now owns a single `sd.OutputStream`, opened
  once and left running; a callback pulls PCM from an internal lock-guarded buffer,
  `play()` appends, and `stop()` clears it. The callback (PortAudio's own thread) is
  the ONLY consumer of audio, so no two threads ever touch the device API at once.
  Barge-in goes from truly-instant to near-instant — silence within ~the stream's
  device latency (a moderate `_LATENCY` chosen so the memcpy-only callback rides out
  CPU starvation without underrunning) — a tiny, imperceptible trade to remove a
  hard crash. The old queue + play-worker thread + module `_epoch` are gone; stop()
  clearing the buffer under the lock is what discards stale audio now. *(2026-07-26.)*
- **`HomePanel._history` stores each turn's real tool exchange, not flattened
  prose, and windows it to 3 turns.** Symptom: Pythia (gemma4:e2b, a 2B model)
  reliably lost tool use after ~3 tool-calling turns in a session, degrading to
  narrating intent ("I need to call the get_weather tool") or hallucinating an
  answer instead of ever calling a tool again. Root cause: `pythia.ask()`'s
  intra-turn loop builds a correct `user → assistant(tool_calls) → tool(result)
  → assistant(answer)` exchange, but only the flattened `{user, assistant}` pair
  survived into the persisted history — every tool_call and tool result was
  discarded at the end of each turn. A small model imitates its own transcript;
  a history of prose-only turns taught it that questions get answered without
  calling anything, and the effect compounded turn over turn. Fix: `pythia.ask()`
  now reports `turn_messages` (this call's own messages, tool exchange included)
  on its stats event; `HomePanel` persists that verbatim, one list per turn, and
  keeps only the last 3 (`_MAX_HISTORY_TURNS`) so an occasional bad turn ages out
  instead of compounding. Not a §2/§6 deviation — a bugfix to what "the
  conversation" means when a tool-calling model reads its own history back.
  *(2026-07-29.)*
- **Pheme owns read state; no separate deity was minted for it.** The Scrapstream
  restructure needed a seen-set shared by both surfaces, and the obvious
  Anti-Legion reading — one job per module, so read state is its own module —
  is the wrong one here. Read state is an attribute OF a headline: meaningful
  only next to one, derived from a story's URL, with exactly one consumer. A
  module for it would have no independent job, and would hand Pheme a
  shared-state coupling back to itself across a file boundary. So `tools/pheme.py`
  gained `read_key`/`read_keys`/`mark_read` and its `Job:` line was left
  unchanged: the module still aggregates headlines, and now remembers which ones
  you opened. The panel keeps presentation only — merge order, the per-source
  cap, collapse state, dimming. *(2026-08-02.)*
- **Read-state identity strips a DENYLIST of tracking parameters, not the whole
  query string.** Blanket query-stripping is the obvious normalization and it is
  wrong for the shipped feeds: a Hacker News text post (Ask HN / Show HN) carries
  its entire identity in the query — `news.ycombinator.com/item?id=NNNNN` — so
  dropping every parameter collapses every text post onto one key, and reading
  one would dim every other. That is news the user has never seen, silently
  greyed out. BBC pulls the other way: it appends `at_medium`/`at_campaign` to
  every link, which must go or the same story keys differently between fetches.
  The denylist (`_TRACKING_PARAMS` + any `utm_` prefix) satisfies both. Its cost
  is that an unrecognized non-tracking parameter re-brightens an item once —
  cosmetic, and deliberately preferred over a silent collision. The key is the
  URL alone, never url+title, so a corrected headline doesn't re-brighten.
  *(2026-08-02.)*
- **Pheme keeps the parsed datetime as `ts`, and `handle()` strips it back out.**
  `_short_date()` built a `datetime` and discarded everything but "Jul 24" one
  line later, which is why the Brain's merge was round-robin: there was nothing
  to sort on. A day-level key across seven feeds is a wall of ties with arbitrary
  intra-day order, so the sort key had to be the full timestamp. `_parse_date()`
  now returns what the feed wrote and two thin formatters split off it —
  `_short_date()` (display, in the feed's own zone, unchanged behavior) and
  `_timestamp()` (sort, always UTC). Naive datetimes are assumed UTC rather than
  left naive: comparing naive against aware raises `TypeError`, and the river
  sorts across feeds that need not agree about offsets. `ts` is a fixed-width
  string, not a `datetime`, so the Kairos payload stays JSON-serialisable (§9)
  and `""` (undated) sorts last under `reverse=True`. `handle()` strips the key
  before returning — the Brain's story shape is deliberately untouched.
  *(2026-08-02.)*
- **Celestarium became a *grouped row*, not a tab in a host panel.** A tab reads
  as part of a panel, and these are first-class destinations. The mechanical
  payoff is larger than the visual one: `HypatiaPanel` stays a `Card` and
  `felhaven.py` needed no edit at all. A tab body must be a plain `tk.Frame` (a
  `Card` inside a `Card` double-borders), so hosting it would have meant demoting
  the panel and reaching *through* the host for its two Kairos registrations and
  its `rescale()`. A grouped row changes only how the rail is drawn. Group
  headers keep the ordinary bordered-card chrome — the rail has to read as one
  column — and stay distinguishable by the ▼/▶ glyph in the dot's slot plus a
  label that never lights green. *(2026-08-08.)*
- **Groups are collapsed on open (§7), and `set_active()` force-expands.** The
  collapsed default is what keeps the rail inside the window: collapsed, a group
  costs one row however many children it holds, so the rail stops growing with
  the view count. The force-expand is the necessary counterpart — a lit row
  hidden inside a collapsed group would leave the nav lying about where you are.
  *(2026-08-08.)*
- **The Artemis chart draws from TIGER/USGS, not OpenStreetMap — a licensing
  call, not a technical one.** OSM has what TIGER lacks (trails, two-tracks,
  surface type, gates) and would be the better map; it is also ODbL
  share-alike, a knot against an MIT repo meant to be cloned. TIGER/Line and
  USGS products are public domain, so the stack stays clean end to end. The
  cost — weak trail coverage, not-survey-grade geometry — is accepted and
  stated in the panel footer. The layer modules keep the fetch/parse/normalize
  seams separate so an OSM adapter stays possible without an architectural
  change; it is architecture now, implementation later or never. *(2026-08-09.)*
- **Artemis render operations never perform network I/O; ingest always does.**
  The API shape is the enforcement: each layer exposes `ingest()` (online,
  slow, explicit, writes into `<area>.partial/` promoted by one `os.replace`)
  and `load()` (offline, pure read) — and `load()` on a missing cache RAISES;
  it does not helpfully fetch. The failure mode being guarded is a reader that
  quietly grows an `if not cached: fetch()` branch eighteen months from now,
  which would make the offline field case dependent on a connection exactly
  when it matters. A socket-seam test hardening this is planned with the
  search milestone. *(2026-08-09.)*
- **Terrain is best-effort inside an otherwise all-or-nothing ingest.** Roads
  and water failing aborts the area — a chart missing its road network is not
  a chart. Elevation is different: it is the only layer behind an API key, and
  failing the whole ingest over a sealed vault or an absent OpenTopography key
  would mean a user without a key loses the roads-and-water map that shipped
  before terrain existed. So `ingest_area` catches `IngestError` from Demeter
  alone, commits the area without it, and records `terrain_skipped: <slug>` in
  the manifest so the panel says what is missing instead of implying a flat
  county. Cancellation is explicitly NOT absorbed — it propagates and discards
  the partial, or a cancelled ingest would commit a half-area. *(2026-08-09.)*
- **The Artemis palette is the one place hue carries meaning, and it lives in
  two files on purpose.** §7's rule is that colours come from `theme.C`, and
  the theme deliberately folds its accents into a monochrome phosphor ramp. A
  map cannot: terrain, water and roads have to be told apart at a glance, so
  the `map_*` keys introduce cyan and amber (near-complementary, and the set
  survives common red-green colour deficiencies), with nothing reaching text
  brightness so the phosphor still reads on top. The three terrain colours
  then exist *twice* — `theme.C` for the panel's vectors and RGB tuples in
  `tools/demeter.py`, which renders the raster headless and must not import
  Tk. Duplication is the lesser evil against dragging Tk into `tools/`;
  `tests/test_artemis_palette.py` holds the two in lockstep so they cannot
  drift silently. *(2026-08-09.)*
- **The terrain raster is resampled to the canvas, not zoomed by Tk.**
  `PhotoImage` rescales only by integer factors, and the cached raster is
  1024² over the ingest square: at native scale the 5-mile ring is ~931 px
  (overflowing a typical canvas) and at `subsample(2)` it is ~465 px in an
  ~810 px canvas, wasting half the view. Either way the vectors would have to
  adopt the raster's scale and the map would stop filling the panel. So the
  panel nearest-neighbour-samples the cached PPM to exactly the drawable span
  once per area change or debounced resize — a few hundred ms, rare — and the
  vector scale is derived from that same span, which makes the layers aligned
  by construction. The cached 1024² PPM stays authoritative and regenerable;
  M4's zoom stops become a factor on the destination size rather than a
  second rendering path. *(2026-08-09.)*
- **The 5-mile ring mask is eight plain shapes, not one keyhole polygon.** The
  usual trick for "fill everything except this circle" is a single polygon
  that traces the rectangle, runs down a zero-width slit into the circle, and
  comes back — which relies on the renderer's winding rule and on a degenerate
  edge behaving. It probably works in Tk; "probably" is the problem, since the
  failure mode is a visible artifact on someone else's machine that no test
  would catch. Four rectangles outside the ring's bounding box plus four
  corner wedges between the box and the arc are all simple, non-self-
  intersecting polygons that fill identically under either rule. Eight canvas
  items against one is not a cost worth reasoning about. *(2026-08-09.)*
- **Zoom crops the cached raster before scaling it, and the fix stays
  centred.** Given the M3 decision to resample to the canvas, a zoom stop is
  just "sample a smaller centred square of the source to the same span" — so
  the work is constant at every stop, where scaling first and cropping after
  would build a 4x-larger intermediate for 4x (~31 MB and seconds of Python).
  The stops only magnify: at 1x the whole ingest square is already visible and
  the cache is a hard edge on how much world exists, so a zoom-out stop would
  show nothing but background. There is no free panning either — this is a
  range display centred on you, and moving the centre is search's job (M5).
  *(2026-08-09.)*
- **Hover tolerance is set in pixels and converted to metres; the test itself
  stays in geometry space.** The spec asks for a fixed ~40 m tolerance,
  explicitly "not pixels" — but that contrast was aimed at `find_closest()`,
  which answers "which canvas item is nearest" and can lose to an unrelated
  layer that happened to drop a vertex closer. That concern is about where the
  comparison happens, and geometry space is preserved: cursor pixels are
  back-projected to local metres, ~9 index cells supply candidates, and exact
  point-to-segment distance decides. Only the tolerance's *size* is derived
  from pixels, because at the shipped display scale 40 m is under two pixels
  and a hover that demands two-pixel precision reads as broken. Ten pixels'
  worth of metres tracks how precisely a hand can actually point, and tightens
  automatically as you zoom in. *(2026-08-09.)*
- **"The source has no such file" and "the download failed" must never be the
  same branch.** Poseidon tolerates a missing AREAWATER file because plenty of
  inland counties genuinely publish none — but the first version caught every
  exception there, so a `TimeoutError` on one county's lakes was recorded as
  "this county has no lakes" and the ingest committed a chart missing real
  water with nothing on screen, and only an INFO line in the log, to say so.
  Found by reading the log after a slow cache looked like a hang. Now only an
  HTTP 404 means absence; anything else raises, and every county download
  retries once first since a stalled TIGER fetch is usually transient. The
  general rule: when a failure path is *tolerated*, the tolerance must be
  narrowed to the exact condition that is genuinely benign — a broad `except`
  under a permissive branch turns every unrelated fault into silent data loss.
  The follow-on: all three ingest downloads now go through one
  `artemis.download()` rather than a copy per layer. The bug was possible
  *because* the logic was duplicated — Hecate's copy was right and Poseidon's
  was wrong — so the fix is one implementation, with 404-means-absence behind
  an explicit `allow_missing` that only the optional AREAWATER fetch passes.
  *(2026-08-10.)*
- **Artemis search moves the VIEW; it never writes the fix.** Themis's
  latitude/longitude feed Atmospherics, Celestarium, Helios/Selene and the
  season as well as the chart, so a search box that wrote through would mean
  looking up a trailhead silently relocated the weather and the star map. The
  searched place becomes a panel-local view centre, ⌂ HOME snaps back to the
  fix, and a Kairos tick deliberately does NOT reclaim the view — otherwise
  typing a place would be undone within ten minutes. Caching the searched area
  stays a separate explicit click; nothing about panning downloads anything.
  This is also why the spec's "Pharos" (a module owning the fix Felhaven-wide)
  was dropped rather than built: Themis already held that job, and the part
  that was genuinely missing — turning text into coordinates — is what
  `tools/eratosthenes.py` does and all it does. *(2026-08-10.)*
- **`tests/test_artemis_offline.py` is the stack's first socket-level
  hermeticism test, and it patches with a context manager.** Every other
  module mocks its own named seam (`requests.get`, `_OPENER.open`), which is
  enough when the rule is about one function. Artemis's rule is about a whole
  category — no render path anywhere may touch the network — so the block goes
  at `socket.socket`/`socket.create_connection` and a real cached area is
  driven through load, render, zoom and hover beneath it. Two things make it
  trustworthy: `mock.patch` as a context manager, never assignment to the
  socket module's globals (that poisons every later test in the process and
  the fallout reads as network flakiness), and a `test_the_block_is_real` case
  proving the guard actually raises — a hermeticism test that cannot fail
  reports safety it never checked. *(2026-08-10.)*
- **ArtemisPanel runs one daemon ingest thread with a bounded `after()` drain
  chain — the Echo pattern, now a listed §6 exception.** A cache ingest is a
  multi-minute, multi-county network job: request-driven (never automatic, a
  two-click confirm), so there is no Kairos tick to drain on, and the panel's
  own `artemis` worker fires only every 600 s. The worker thread's only shared
  touch is `queue.put`; an epoch counter discards messages from a cancelled
  run; a `threading.Event` cancels between counties and the tools side
  guarantees a cancelled or failed ingest leaves no partial directory.
  *(2026-08-09.)*
- **The Artemis porthole is fixed-size chrome; the world moves under it.**
  Canvas items now carry one of two family tags. `world` (terrain raster,
  water, roads, range rings, the fix crosshair) is anchored to `_view()`'s
  origin, drawn *under* the mask so the glass clips it, and is the only thing a
  drag moves. `chrome` (mask, rim, bearing ticks, cardinals, compass, scale
  bar) is anchored to the canvas and never moves. `_porthole_r` is
  `DISPLAY_RADIUS_M * min(scale, scale/zoom)`, which is `0.4546 * span` at
  every stop — so the round frame is the same size at 1x, 2x and 4x, and
  `_draw_mask`'s old "nothing to hide when zoomed in" early return was
  provably unreachable and is gone. What forced this: the terrain raster is a
  `span × span` square while vectors span the full canvas width, so dropping
  the mask at 2x/4x exposed the raster's straight edge as a hard seam with
  roads floating on black beyond it. Range rings that no longer fit are
  replaced by the rim, labelled with the radius actually visible (5 / 2.5 /
  1.25 mi) so zooming never removes the chart's only distance reference. Note
  `DISPLAY_RADIUS_M` (8047 m) exceeds the true 5-mile ring (8046.72 m), so the
  fits-inside test needs a two-pixel margin or it draws ring and rim as a
  doubled hairline. *(2026-08-16.)*
- **Artemis pans by drag, superseding the 2026-08-09 entry that said the fix
  stays centred.** That entry was right for a chart with no mask beyond the
  ring and no way to look off-centre; the porthole change above made a
  centred-only view the thing that *showed* the raster seam, and zoom without
  pan only ever magnifies the middle. The pan is stored in world metres and
  enters `_view()` alone, so vectors, rings, the fix mark and the hit test
  follow it with no second code path — a panned chart and a panned hit test
  cannot disagree. It clamps to `DISPLAY_RADIUS_M * (1 - 1/zoom)`: exactly
  zero at 1x (everything is already on screen, so off-centre would be
  arbitrary) and, since the porthole's world radius is `DISPLAY_RADIUS_M/zoom`,
  the furthest reach is exactly `DISPLAY_RADIUS_M` — inside the cached square,
  so there is always data under the glass. A drag moves the `world` tag and
  never rebuilds the raster; a `_PAN_MS` pause re-crops it, which is needed
  because the raster reaches only ~4.5% of the span past the glass. Click and
  drag are told apart by `_CLICK_PX = 4` (the Windows `SM_CXDRAG` threshold) —
  deliberately not `_HOVER_PX`, which measures how precisely a hand can point
  rather than how far one slips while clicking. *(2026-08-16.)*
- **A cached area's name is optional manifest metadata, and the recycle-bin
  helper is copied rather than shared.** The name can only be metadata:
  `area_id` is a deterministic function of the centre coordinates, so the
  directory name is not available to rename. It rides as an optional
  `manifest["label"]` with no `MANIFEST_SCHEMA` bump — optional keys are
  already how schema 2 carries `terrain_skipped` and `grid` — and
  `ingest_area` reads it back immediately *before* the commit, not at the top,
  so a rename made *during* a download still survives. On the helper:
  `orpheus.delete_file()` is NOT reusable (its `_safe_path` demands
  `os.path.isfile` and is hard-wired to `local_audio/`), and unlike the
  `download()` consolidation this is a copy worth making — the Windows call is
  identical but the non-Windows fallback cannot be (`os.remove` raises on a
  directory), and the download bug came from a duplicated *policy* branch
  where this has no policy at all. Rule of three: lift it to a shared home if
  a third caller appears. `delete_area`'s guard is the real work — bare name,
  not `_`-prefixed, not `.partial`, `isdir`, and a `realpath` parent check,
  which is what a basename test cannot do since a junction inside the cache
  would otherwise pass. *(2026-08-16.)*
- **A refresh keeps the terrain it already had — best-effort must not mean
  best-effort-on-re-ingest.** Terrain was made best-effort so a missing
  OpenTopography key could not cost you the roads and water that already
  downloaded (§12, 2026-08-09). That reasoning holds for a FIRST ingest and
  inverts for a refresh: `ingest_area` replaces the whole area directory, so
  a re-ingest with a sealed vault silently downgraded a complete area to a
  terrain-less one — the user paying for a road update with elevation they
  already owned. Found in the field within minutes of shipping the per-row ⟳.
  `_carry_terrain` now copies the committed `terrain.bin` into the partial
  dir and restores the `grid`/`sources.elevation` entries, recording
  `terrain_kept` rather than `terrain_skipped`; topography does not change,
  so an older grid is strictly better than none, and carrying the grid whole
  is not datum mixing. Only `terrain.bin` is copied — `derived/` already
  holds the freshly built spatial index for the NEW roads and water, and
  `demeter.raster()` rebuilds the raster on demand. This is the Poseidon 404
  lesson recurring: **a tolerated failure must never degrade data that is
  already committed.** Ask of any such branch not just "is this failure
  survivable" but "survivable compared to what was already there".
  *(2026-08-16.)*
- **The Cached Locations overlay is `place()`d on the canvas, expanded by
  default, and does not scroll — three deliberate deviations.** (1) It is a
  child of the canvas positioned with `place()`, never
  `canvas.create_window()`: a canvas window is a canvas *item*, and
  `_redraw()` opens with `canvas.delete("all")`, so the overlay would be
  destroyed on every zoom, resize and area load. A test redraws and asserts
  the box still exists, so converting it back fails immediately rather than
  mysteriously. (2) §7 says collapsible sub-widgets start collapsed; this one
  starts expanded because it is map chrome in the same class as the compass
  and scale bar, which are already permanent — the ▶/▼ toggle is still there
  for anyone who disagrees. (3) §7 says scrolling lists reuse `_ScrollFrame`;
  this is not a scrolling list. Rows are few by construction (each is a
  deliberate 10–30 MB download), a seventh copy of that pattern is heavy
  machinery for two to six rows, and its `bind_all` wheel handling would
  fight a canvas that now pans. Recorded so none of the three reads as an
  oversight. *(2026-08-16.)*
- **`Sidebar._relayout()` forgets the whole rail and re-packs it** rather than
  packing/forgetting just the group box. `pack()` appends to the bottom of its
  container, so re-expanding a group would drop its children below Cogitator
  instead of back into their slot. Forget-all-then-pack-in-declaration-order is
  the same idiom `felhaven._show_view` uses, and it is order-correct by
  construction rather than by remembering a `before=` neighbor.
  *(2026-08-08.)*
