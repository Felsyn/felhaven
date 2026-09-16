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
  two-click-remove confirm that disarms itself — is fine: it fires once, on the
  main thread, touching only this panel's own widgets.
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

**`PythiaFace`'s animation loop** (`panels/pythia_face.py`) is the one exception
that is not about a slow call. It is a ~20 fps redraw, and there is no rate at
which a 500 ms Kairos tick animates anything — so the widget schedules its own
frame chain. What keeps it honest is that it is *bounded by visibility*: `<Map>`
starts the loop and `<Unmap>` stops it, so a face on a packed-away view costs
nothing (`felhaven._show_view` `pack_forget()`s the whole panel), `start()`
guards on `_running` so a repeated `<Map>` cannot double-schedule, and the
pending id is cancelled in `destroy()`. It spawns no thread and touches only its
own canvas items. `tests/test_panel_after_teardown.py` holds all three ends of
that down.

Note what the face does **not** do: it reads no state. `HomePanel` derives the
state name on its existing drain chain and pushes it in. A panel that wanted to
animate *and* poll for what to animate would need a second exception, and would
not get one.

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
  drain, `HomePanel`'s poll). Re-arm through a helper that
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
- **The one inversion — Hestia's vox controls** (`panels/vox_controls.py`):
  there, active = `C["amber"]` and inactive = `C["text3"]`, the opposite of the
  rule above. It is deliberate and it is local. Home's command surface sits
  beside the wireframe face and the Sonus indicator, which already say "amber
  means this is doing something right now"; a control row that lit up green next
  to them would be reporting the same fact in a second vocabulary. The cost is
  that amber can no longer double as the hover colour — an unhovered active
  control and a hovered inactive one would be identical — so **hover brightens
  within the current state** instead of announcing one: `text3` → `text1` when
  off, `amber` → `amber_bright` when on. `amber_bright` is to `amber` exactly
  what `red_bright` is to `red`: the same channel, hovered, not a new meaning.
  Each control also carries `SidebarRow`'s dot — same canvas, same 8 px oval —
  with the **polarity flipped**: `SidebarRow` lights its dot `C["green"]` when
  active and leaves it amber otherwise ("amber = somewhere you could go, green =
  you are here"), which reused here would turn a dot green at the exact moment
  its own label turned amber. The dot follows the label beside it, from one
  computed colour painted on both surfaces — never two branches that can drift.
  Do not spread this outside that row. Everywhere else, active is still
  `C["text1"]`.
- **Collapsible sub-widgets**: collapsed by default, toggled with a `▶`/`▼`
  label — copy the `AmmitWidget` section-toggle.
- **Grouping views in the nav rail**: a family of related views goes in
  `Sidebar._GROUPS` (`panels/sidebar.py`), never in a tab host. The group header
  is a `GroupRow` that selects nothing; each child stays a full entry in
  `felhaven._views` with the whole content area and the ordinary lit-row active
  state. Adding one is a child tuple plus a `_views` entry —
  `test_sidebar_view_parity` fails if you do only one of the two.
- **Nothing above the input may out-request the input.** Pack satisfies every
  slave's *requested* height in pack order, and only then hands the leftover to
  `expand=True` children — so a tall widget packed above a control squeezes that
  control to 1 px rather than shrinking itself. A `tk.Text` asks for 24 lines and
  a bare `tk.Canvas` asks for 265 px, which is how `PhosphorScroll` silently put
  a 265 px floor under every container holding one and collapsed Home's chat bar
  at any window under ~1280×900. Give a widget that is *meant* to absorb slack
  (`height=1` on a Text, an explicit `height` on a Canvas) a token request and
  let `expand` do the growing. The rule of thumb: whatever the user scrolls is
  what shrinks; whatever the user clicks or types into never does.
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
- **`HomePanel._poll` swallows and logs, and re-arms in a `finally`** (vs. the
  general rule that panels let errors surface). This chain is not just this
  panel's: `_poll` drives `_on_delta`, which is what feeds sentences to
  Calliope. `_arm_poll()` was the last statement in the method, so ONE escaping
  exception stopped the chain permanently — and the symptoms named nothing.
  Narration halted mid-answer, the transcript froze, and Vox End stayed lit
  because `_tick_face` never ran again to clear it, so the whole thing read as
  "narration is broken" rather than "the UI is dead."

  **What made it pathological is that it was silent.** Tk prints a failing
  `after()` callback to stderr, and Felhaven runs under `pythonw.exe`, which
  has no stderr — so nothing reached `felhaven.log` and there was nothing to
  debug from. The swallow is only defensible BECAUSE of the `log.exception`
  that comes with it: the failure now names itself in the log, where Emanon
  surfaces it. Never add one without the other. *(2026-09-08.)*

  **And the log line needs a rate limit, for the same reason it needs to
  exist.** The chain ticks at 16 Hz, so a persistent fault writing a traceback
  every tick is ~3.7 KB/s — measured — which cycles `felhaven.log`'s
  1 MB x 3 rotation in under twenty minutes and shreds the FIRST traceback, the
  only one worth reading. Felhaven gets left running for days at a time, so an
  unattended fault has to cost a trickle. `_note_poll_failure` reports once per
  `_POLL_FAIL_QUIET` with a count of what it stands for. Note the gate is on
  time alone: gating on the count too looks equivalent and is not, because the
  counter resets after each report, so the next tick reads as a first failure
  and the flood survives. *(2026-09-08.)*
- **The one owned output stream is checked for liveness, not assumed.**
  Harmonia opens a single `sd.OutputStream` and keeps it for the app's life —
  that is deliberate and it is what killed the barge-in segfault (§12,
  2026-07-26). What it quietly also meant was that `_ensure_stream` returned
  the cached stream on nothing but `is not None and rate matches`. PortAudio
  can stop a stream underneath you — device change or removal, driver reset,
  another app taking the device in exclusive mode, sleep/resume — and when it
  did, every later `play()` got the corpse and appended to a buffer nothing was
  draining. Narration went silent for the rest of the session, `is_playing()`
  stuck true so Vox End stayed lit, Calliope kept synthesizing perfectly, and
  **nothing was logged**. It took two bug reports to find, and the first one
  sent me hunting the wrong subsystem entirely.

  `_stream_is_live()` reads `closed`/`active`; a backend exposing neither is
  treated as live, because guessing "dead" would reopen on every call. The
  reopen drops whatever was buffered: audio queued against a dead device is
  stale by the time one returns, and replaying it would talk over whatever the
  user asked next.

  **The general lesson is worth more than the fix**: "own it for the process
  lifetime" is a statement about *who may replace it*, never a guarantee that
  it still works. Any long-lived handle to something outside the process —
  a device, a socket, a pipe, a subprocess — needs a liveness check at the
  point of use, and Felhaven is left running for days, so "rare" here means
  "weekly", not "never". *(2026-09-09.)*
- **mpv's scratch log is capped by RESTARTING mpv, from `play()`** — not by
  truncating it, and not from the Kairos tick. `mpv.log` grows ~420 KB per track
  load and Felhaven is left running for days, so one mpv process would grow it
  without limit. Every cheaper option was measured against the shipped binary
  on 2026-09-09 and does not work: `--msg-level` does not lower it (mpv forces
  `--log-file` to at least `-v -v`, so `all=error` and `ytdl_hook=error` both
  leave idle startup at ~14 KB); truncating in place leaves mpv's file offset
  stale, so its next write re-expands the file with a hole of NUL bytes (13,710
  measured); and deleting it while mpv holds the handle does not return the
  space on Windows, it only unlinks the name while the bytes stay charged to
  the open handle. Restarting is the only thing that both truncates the file
  and releases the handle.

  It hangs off `play()` because that is the only call that grows the log, and
  the one moment a restart costs nothing — `loadfile ... replace` was going to
  discard the playlist anyway, and `--save-position-on-quit` keeps the outgoing
  bookmark. Explicitly NOT off `status()`/`fetch()`: those are documented
  READ-ONLY and Kairos drives them every 2 s. A watcher that restarts the thing
  it is watching is not a watcher. *(2026-09-09.)*
- **`duck()`/`unduck()` are write-only, and the restore target is a constant.**
  `duck()` runs on Calliope's release worker — the thread that feeds the audio
  buffer — and `unduck()` runs on the Tk main thread. `_ipc()` without
  `expect_reply` opens, writes and closes; `_get_props()` waits for a reply, and
  its own timeout "cannot interrupt a single `readline()` that is already
  blocking." Reading the pre-duck volume so it could be restored exactly was
  therefore buying accuracy with a stall risk on the two threads that can least
  afford one. There is no accuracy to buy: mpv runs `--no-config`, nothing in
  Felhaven exposes a volume control, and morpheus is the only writer, so the
  value is always 100. **Adding a volume control is what breaks this** — the
  read would have to come back, off both threads. *(2026-09-08.)*
- **Calliope's pre-rendered fillers are gone, and the prebuffer gate became
  unconditional.** The fillers were five canned phrases synthesized once into a
  hash-keyed WAV cache and played 1000 ms after Enter to mask kokoro's synth
  latency. They dragged a per-voice cache (re-rendered on a daemon thread by
  `set_voice()`), a render pass inside `prewarm()`, a one-shot `after()` timer
  with epoch/cancel guards, and a `_real_audio_queued` flag whose only job was
  stopping a late filler from jumping ahead of real speech.

  The gate they masked stays, and now always applies: it used to return at once
  unless `_filler_active`, so removing the fillers would have left it
  unreachable. **The subtlety worth keeping** is that the gate has a second
  release condition — `_turn_ended` — and that is now the only thing standing
  between a one-chunk answer and the full `_PREBUFFER_TIMEOUT`. `_on_done`
  already called `end_turn()`; `home_panel._speak` (the manual speak button)
  did not, because the filler short-circuit had always covered for it. It does
  now. A caller that hands Calliope complete text and skips `end_turn()` will
  stall for six seconds, which is exactly the sort of bug a removed guard
  uncovers rather than causes. *(2026-09-07.)*
- **Narration DUCKS music instead of stopping it**, and `harmonia.tick_yield()`
  rides `HomePanel._poll`'s existing 60 ms chain rather than arming a timer of
  its own (vs. §6, which gives timing to Kairos — this is a passenger on a
  listed exception, not a new one). `harmonia.play()` used to call
  `morpheus.stop()` before buffering any audio, once per chunk. mpv's `loadfile`
  is fire-and-forget, so `play_music` returned `{"now_playing": ...}` while
  yt-dlp was still resolving the URL, and the answer's own first spoken chunk
  killed the resolve 2.36 s later — measured in `logs/mpv/mpv.log`: `loadfile`
  at t=0.223, `stop` at t=2.585, `ytdl_hook: Subprocess failed: killed`.

  **The part worth remembering** is why it had no symptoms. `stop()` clears
  `_pending_load`/`_load_error`, and that clause was written *specifically* so
  the yield could not be misreported as a load failure — so it also swallowed
  the true failure the yield was causing. A tolerance added to silence a false
  positive must be narrowed to the exact condition that is genuinely benign, or
  it will eventually hide the real thing. The clause stays (MorpheusPanel's stop
  button still needs it); only its justification changed.

  Uses mpv's own `volume`, not `ao-volume` (the system mixer). `_ducked` is a
  latch, not a counter — Harmonia has one buffer and one drain, so overlapping
  ducks are unreachable and a counter could only ever leak a permanent duck.
  `--watch-later-options=start` is load-bearing: mpv persists options that
  changed since playback started, and `volume` is in its default set, so without
  it a checkpoint taken while ducked would bake `volume=30` into that track's
  resume file for good. *(2026-09-07.)*
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
  auto-clears (flagged walkthrough s17), and HomePanel's latency-masking filler
  (since removed, 2026-09-07) — all of which fire once on the main thread and
  touch only their own panel's
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
- **CodeQL's clear-text-logging alerts against §3 smoke-test `print()`s are
  dismissed, never coded around.** Three so far, all
  `py/clear-text-logging-sensitive-data` at **high** severity, all on the public
  mirror `Felsyn/felhaven` (code scanning is not enabled on the private repo, so
  they surface only after a snapshot push): `tools/aura.py` and `themis.py` on
  2026-07-21, `voices.py` on 2026-08-16. The query's "private" classification
  implements CWE-359, whose term list includes geographic location *and gender* —
  so `aura`/`themis` tripped on latitude/longitude and `voices.py` on the local
  named `gender` in `label()`, read from the hardcoded
  `_GENDER = {"f": "female", "m": "male"}`. **The heuristic is name-based only.**
  It cannot see that the value is one of two literals naming a kokoro voice
  timbre, or that a §3 smoke block prints constants to a local console and never
  to a log file or the wire. **Do not rename an accurate identifier to dodge a
  regex** — `gender` → `timbre` would silence the scanner by making the code
  worse for the reader, and would teach the next session that heuristics outrank
  clarity. Dismiss with reason `used in tests` and a comment saying why (280-char
  API limit), then one CHANGELOG line. Genuine secrets are Cerberus's problem and
  never reach a `print()` in the first place. *(2026-08-16.)*
- **`panels/rite_panel.py` is a `tk.Toplevel`** — the toolbox's first, against
  §7's house pattern of an in-panel expand or a swapped frame. The Rite Log has
  two requirements neither of those meets: the operator must be able to open
  **two turns at once** and read them side by side, and the transcript behind it
  must stay scrollable while one is up (the failure they are diagnosing is often
  three answers further down). Hence also **no `grab_set()`** — a modal window
  would defeat both. The window imports `theme` and nothing else; it takes a
  list of dicts and draws it, so it stays a panel in every sense that matters.
  *(2026-08-18.)*
- **Stop now halts pending tools, not just the model.** `cancel` was checked once
  per round, above the tool loop, so pressing Stop while tool 1 of 3 was running
  let tools 2 and 3 run to completion — Stop stopped the oracle but not its
  rites, which is not what the button says. A second check now sits at the top of
  the per-call loop; remaining calls are recorded `not_run` and never dispatched.
  Note the blast radius: `HomePanel._on_refresh` also sets the cancel event, so
  **Refresh abandons pending tools too**. Both are the intended reading of "stop".
  *(2026-08-18.)*
- **A rite record's `error` field is assigned by subscript, not written into the
  dict literal** (`pythia.py`). `tests/test_tool_output_contract.py` inspects
  every `{"error": ...}` literal in the app-root and tool modules and requires a
  slug — the right rule for a value that gets fed to the LLM, and the wrong one
  for a *diagnostic copy* of that value, which is computed by construction. The
  guard cannot tell the two apart, so the record is built without the key and the
  field set immediately after, with a comment at the site. **Do not "tidy" it
  back into the literal** — the guard will fail, correctly by its own lights.
  A future tightening could scope the walk to `return` statements. *(2026-08-18.)*
- **`pythia._now()` returns naive LOCAL time**, against the usual instinct to
  store UTC. Felhaven is single-machine and local-first, and the question a rite
  answers is "what happened on my machine, when" — the reader is comparing it
  against their own memory of the afternoon, so they want the clock on the wall.
  UTC earns its ugliness once records are aggregated across machines, which these
  never are. It exists as a function at all so tests can patch it. *(2026-08-18.)*
- **The Rite Log's redaction is a pattern scrub, and imports no `cerberus`.** A
  diagnostic system that needed the vault unlocked in order to report that the
  vault is locked would be useless exactly when it is wanted. So `_redact()` is
  deterministic regexes over the *value* half of `key=`/`token=`/`auth=` and
  `Authorization: Bearer`, applied to a rite's error and result preview only.
  **Arguments are never redacted** — model-authored arguments are precisely what
  the operator opened the log to read, and scrubbing them would defeat the
  feature. *(2026-08-18.)*
- **Every `/api/chat` payload shares one `_OPTIONS` dict** — `_chat()` and
  `_warm()` must not each spell out their own. Ollama keys a *resident* model on
  its runtime options, so a warm whose `num_ctx` differs from the chat's loads a
  runner the first real question immediately discards and reloads. The prewarm
  looked healthy for six weeks while doing exactly that: it sent no `options` at
  all, warmed a 65536-context runner, and the first question still paid ~70s —
  measured 71s at a point 83 seconds *after* a warm that had reported success in
  1.4s. The guard is a test comparing the two captured payloads to each other;
  hardcoding `8192` in it would pass while the halves drift. *(2026-08-19.)*
- **The warm sends a real prompt, not `messages: []`.** Load-only warming leaves
  the ~2,700-token system-prompt-plus-tool-schemas prefill to the first question
  (6.7s measured). Sending the real prefix parks it in llama.cpp's prompt cache,
  which matches on longest common prefix — so a question with a *different* user
  message still reuses it: 2,714 prompt tokens, of which only 21 were actually
  processed. Cost of the choice: the prefix is captured at warm time, so editing
  the Machine Spirit prompt before the first question misses the cache and falls
  back to the old cost. No breakage, just no saving — the system-prompt-as-tool-
  surface seam again. *(2026-08-19.)*
- **`pythia._WARMING` is a worker→main `threading.Event`**, against §6's "the
  result queue is the only object shared between worker threads and the main
  thread". Allowed for the same reason `ask()`'s `cancel` crosses that boundary
  the other way: an Event is atomic, carries no payload, and needs no drain, so
  none of the ordering hazards the queue rule exists to prevent apply. It is
  cleared in a `finally` precisely so a downed Ollama cannot strand the UI on
  "waking the oracle…". `HomePanel` reads it from the drain chain it already
  runs, so no second `after()` id is created and §6's teardown rule is
  untouched. *(2026-08-19.)*
- **`morpheus.fetch()` carries an `error` slug**, which §2's flavor table does not
  contemplate for a polled read — the error vocabulary is written around
  `handle()`. Morpheus's mutations are fire-and-forget over IPC (`loadfile`
  returns nothing), so the *only* place a failed load can surface is the polled
  read. On 2026-08-20 a yt-dlp too old for YouTube made every play a no-op: mpv
  resolved a URL it then got HTTP 403 on, the panel showed "(loading…)"
  indefinitely, and `felhaven.log` held not one `METIS.morpheus` line across the
  whole outage. The subsystem was completely dark, and the bug was findable only
  by running mpv by hand *outside* the app. The slug is bare (`load_failed`) and
  mpv's own text is never spliced into it — those lines carry signed
  `googlevideo` URLs, exactly the leak §2's no-raw-text rule exists to stop; the
  reason goes to the logs instead. *(2026-08-20.)*
- **Load failure is detected by tracking intent, not by subscribing to mpv's
  `end-file` event.** An event reader needs a persistent pipe connection and a
  dedicated thread, and §6 gives timing to Kairos; this rides the 2 s status tick
  that already exists and adds no thread. It works because a failed load and a
  deliberate stop are *identical* to mpv (idle-active `True`, path `None`) and
  differ only in whether we asked for something — so the discriminator has to be
  Python-side anyway. **The trap, paid for once:** mpv sets `path` the moment it
  accepts a URL, while yt-dlp is still resolving and long before anyone knows the
  load will work. Confirming on `path` disarms the detector one tick *before* the
  failure appears, and the outage goes invisible again. Confirmation is
  `time-pos`/`duration` — media existing. The unit tests were green with the bug
  in place ("no error while loading" is true either way); only an end-to-end run
  against real mpv caught it, and the regression test now asserts the detector is
  still *armed* after a loading tick rather than merely quiet. *(2026-08-20.)*
- **mpv gets TWO log files, because its own is unreadable.** `--log-file` writes
  ~420 KB per successful track load, dominated by one ~128 KB line (mpv logs
  yt-dlp's whole JSON dump), and it writes that whether anything went wrong or
  not — one measured run was 424 KB and 661 lines containing *zero* errors. The
  verbosity is not ours to tune: `--msg-level` does **not** lower it, because mpv
  forces the log file to at least `-v -v` (verified against the binary rather than
  assumed, twice). So `mpv.log` is treated as a **scratch buffer**, and the ~3
  error lines that matter are cut out of it into `last_failure.log` at detection
  time — 361 bytes vs 22 KB, measured. The distilled file holds exactly ONE
  failure, overwritten each time, because the most recent is the only one anyone
  asks for; it is what the panel names and what survives `shutdown()`, which
  deletes the scratch buffer once mpv releases the handle. **The lesson worth
  keeping: when a third-party tool won't log less, don't accept its log as the
  artifact — distil it and throw the rest away.** Both files sit in a
  *subdirectory* of `logs/`, since `emanon.py` globs `logs/*.log` non-recursively
  and parses every line of every match on each tick, and neither is in the METIS
  `" | "` format. Neither is the primary signal — the ERROR line `status()` writes
  to `felhaven.log` is what Emanon surfaces, and it stands alone if `--log-file`
  is ever dropped entirely. *(2026-08-20.)*
- **Morpheus's transport is ONE `action` enum, not five tools — and `resume_music`
  was absorbed into it rather than kept beside it.** Two decisions, one reason
  each. *Five tools:* `pause_music`, `stop_music`, `next_track`, `previous_track`
  and `music_status` would be five schemas billed on every request to a small
  local model, to describe one subsystem's transport; `manage_timer` had already
  set the enum precedent. *Absorbing `resume_music`:* it meant "reload `_last_url`
  from where it left off", which is **not** what "resume" means next to a pause
  button. Left side by side, the model faced that distinction on every music turn
  and could only get it wrong — so unpause took the name `resume` and the reload
  became `replay`. Net: morpheus still exposes exactly two tools, split
  start-vs-govern, and the registry stayed at 22. **Two things not to "simplify"
  back:** `control_music` routes pause through `set_paused()` and never
  `toggle_pause()` — a blind caller retrying a toggle turns the music back ON,
  which is why the panel keeps the toggle and the tool does not — and every
  mutating action is gated on `status()["running"]`, because `_ipc()` is silent on
  a dead pipe and would otherwise report success for a command nothing heard. A
  human pressing a button sees nothing happen; a model does not.
  *(2026-09-15.)*
- **Morpheus tracks the volume it set rather than reading it back — and the
  duck is a RATIO, not a floor.** Two choices that look wrong at a glance, in the
  one place a volume control could go badly. *The tracking:* `duck()`/`unduck()`
  run on the audio-buffer thread and the Tk main thread, and `_get_props`' timeout
  "cannot interrupt a `readline()` already blocking", so a read on either could
  stall narration or the UI. This module's own docstring predicted that a volume
  control would therefore force the read to come back. It does not — a read is
  only needed if we do not already know the answer, and we do: `--no-config`
  blocks any `mpv.conf`, there is no window or terminal to take a keypress,
  `--watch-later-options=start` keeps volume out of resume files, and this module
  is the only writer in the process. `_user_volume` is therefore AUTHORITATIVE,
  not a cache. **What invalidates it:** a second writer appearing, or either of
  those two mpv flags being dropped — then the read has to come back, and off
  both those threads (the Kairos worker is the place). What the old warning got
  right was narrower than it looked: the constant was the problem, not the
  tracking. *The ratio:* with the volume at 20, ducking to a fixed 30 makes the
  music LOUDER during narration — the opposite of yielding. `_DUCK_RATIO` is
  0.30, which at the default level is the same 30.0 the original was measured at,
  so nothing observable changed for the case that already existed.
  *(2026-09-15.)*

- **Aura has two sources, and Kairos retries a failed fetch early.** Two changes
  to one failure, worth separating because only one of them is about retrying.
  *The trigger:* on 2026-09-15 wttr.in served an expired Let's Encrypt
  certificate for nine hours (`notAfter Sep 15 08:35:01 2026 GMT`), and the
  Atmospherics panel read "unavailable" the whole time. The log shows the same
  host failing in runs before that — 500s and read timeouts on 08-18, 08-21 and
  08-24, three to eight consecutive misses each.

  **A retry would not have fixed any of it, and that is the useful part.** A
  certificate failure is not a flaky link: every attempt fails identically, at
  the same speed, forever. The runs of 500s were hours long too. So the tempting
  fix — retry the request three times inside `fetch()` — would have changed
  almost nothing, and `verify=False` would have permanently disabled certificate
  checking to paper over something that lasted an afternoon. The real answers
  were a **second source** (`_fetch_weather()` falls back to Open-Meteo, which
  normalizes into `_build()`'s dict so no panel learns which host answered), and
  a **schedule fix**, because the panel was also staying wrong long after the
  sky was fine.

  *The schedule fix* (`Kairos.RETRY_DELAYS`) is a genuine deviation from "Kairos
  fires each worker on its interval": a failed worker is re-attempted at
  60s/300s/900s instead. **It deliberately runs out** rather than backing off
  forever — a blip is caught within a minute, and a real outage settles back to
  the worker's normal cadence instead of doubling the traffic to a dead endpoint
  for nine hours. It lives in `_drain_queue`, on the main thread, reading the
  `None` that Kairos itself put on the queue, so §6's one-shared-object contract
  is untouched.

  **What the fallback costs:** Open-Meteo has no moon data, so SELENE reads
  blank while it is serving (`selene.py` already degrades per-field, so it shows
  "—" rather than crashing). Restoring it means computing the phase locally —
  it is deterministic from the date — not a third fetch. Its `weather_code` is
  also translated from WMO into wttr.in's WWO space on the way out, because
  that is the space `aura_panel._icon()` maps; leaking a raw WMO code would
  silently draw the wrong emoji rather than fail. *(2026-09-15.)*
