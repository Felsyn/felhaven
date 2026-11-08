# FELHAVEN — Backlog

Known, not done. **One job:** things found but not fixed yet, so nothing a
session notices is lost when the session ends.

**The rules:**

- **Security issues never go here.** One found in any session is flagged at once
  and fixed in that same session, no exceptions. (This file is also published
  with the public snapshot.)
- **Found something out of scope?** Add a line before the session ends.
- **Fixed it?** Delete its line **in the same commit** as the fix, so this file
  can't go stale. `CHANGELOG.md` records the fix. There is no "done" section and
  no strikethrough.
- **One bullet per item:** what's wrong, and why it matters, then
  `— needs: … · found YYYY-MM-DD (where)`.
- **Reasoning stays in `CONVENTIONS.md` §12.** A line here links to it; it
  doesn't copy it. A deliberate choice ("won't do, because…") belongs in §12,
  not here.

Sections are ordered by what an item needs before work can start.
`tests/test_backlog_format.py` holds the shape.

---

## Needs a gemma4 probe first (changes what the model sees)

- **Aura's `astronomy` block is still in `get_weather`'s output**, though no
  panel reads it since Helios/Selene compute the sun and moon. The model can see
  wttr.in's sunrise beside Helios's, usually within a minute of each other.
  — needs: probe, then remove · found 2026-09-28 (PR #28, §12)
- **The default Machine Spirit prompt keeps a hand-made tool inventory that has
  drifted.** It is missing music, sky, sun, moon, the archive and Daedalus. Its
  position ahead of the style rules is what keeps `write_document` callable
  under the no-Markdown rule (measured 2026-07-31), so don't edit it blind.
  — needs: probe · found 2026-09-27 (V0.03 audit M1)
- **Tool description grammar varies.** Some open "Returns…", some are
  imperative, and `get_time_context` has two overlapping when-clauses. It
  touches every schema. — needs: probe · found 2026-09-27 (V0.03 audit L1/T2)
- **Pythia narrates a stale reading as current.** gemma4:e2b ignored `stale` in
  every form tried (§12, 2026-09-28). The next thing to try is a Machine Spirit
  rule or a bigger model. — needs: probe · found 2026-09-28 (PR #27)
- **`fetch_page`'s description still says to fetch only search results**, though
  the dispatch gate now also opens a URL the user wrote (§12, 2026-09-30). A
  model that obeys the text may search for a link it was already given.
  — needs: probe · found 2026-09-30 (dispatch gate)
- **How gemma4 relays the gate's refusals is unmeasured** (`untrusted_url`,
  `not_confirmed`, `no_confirmation`, `private_address`). The gate holds either
  way; what's unknown is whether the model tells the user plainly or retries
  and invents an answer. — needs: probe · found 2026-09-30 (dispatch gate)
- **Pythia can report a save that never ran.** Live: "search … and save it as
  lich.md" ran search and fetch, then answered "I have saved the information
  to lich.md". No write was called, and the log and `list_documents` agree.
  Every write now shows a `pythia asks ›` block, so a missing block is the
  tell; the claim itself is still false. — needs: probe · found 2026-09-30
  (live gate check)
- **Tool results reach the model as ASCII-escaped JSON.** `ask()` sends
  `json.dumps(result)` with the default `ensure_ascii=True`, so `—` arrives as
  `—`. The model copied `—`, `×` and `\"` literally into an
  `edit_document` of a fetched page. `ensure_ascii=False` is a one-word fix,
  but it changes every tool message the model reads (and probably its token
  count). — needs: probe · found 2026-09-30 (live gate check)
- **The word "archive" steers Pythia to Herodotus even when it names a
  website.** "search the archives of nethys for …" ran `search_documents` on
  the local archive and found nothing. — needs: probe · found 2026-09-30 (live
  gate check)

## Code, ready to start

- **A music load that fails after `play_music` returns never reaches the
  chat.** `play_music` fires the load and answers `now_playing` at once. When
  mpv then goes idle with nothing loaded (e.g. YouTube refusing the stream
  with HTTP 403), Kairos's Morpheus worker logs an ERROR, which Emanon shows,
  and the MORPHEUS tab says so. But the chat keeps the call as a success, and
  Pythia has already said the song is playing. There were 22 such failures
  in the logs from 2026-09-17 to 2026-09-30. The same happens when `play()`
  does nothing at all: it returns None whether mpv failed to start or (since
  2026-10-03) the URL wasn't http(s), and `play_music` still answers
  `now_playing` (seen 2026-10-03, tracing `play()`). Fix options: `play_music`
  waits out the load grace before answering, or a late failure posts a line
  into the chat. — needs: a choice between those, then change + tests · found
  2026-09-30 (live check, reported by the owner)
- **Error slugs have no shared core.** The same condition has several
  spellings (`weather_offline` / `network_error` / `network_unavailable`;
  `no_results` / `no_matches`; five `*_unavailable`), and a small model
  generalizes better from one. Rite's `_ERROR_TEXT` moves with them.
  — needs: medium change · found 2026-09-27 (V0.03 audit L2/T9)
- **No `TOOL_META`** (mutates / network / domain) on any brain tool. It is the
  Tool Broker's prerequisite, and on its own it would generate the module
  intros' "Network / Pythia access" lines that are now hand-written.
  — needs: large change · found 2026-09-27 (V0.03 audit M6/T1)
- **Cancelling an Artemis ingest waits out the current download.** The cancel
  flag is read between downloads, and one census zip or terrain grid can take
  minutes (the terrain grid's deadline is ~18 min). hermes could check a cancel
  event before each socket read. — needs: small change + tests · found
  2026-10-01 (bounded network reads)
- **Artemis's cached-areas list never scrolls.** The overlay is a plain frame
  pinned to the chart's lower left, one row per area, so every new area makes
  it taller over the map. The owner wants it to scroll once it holds 5 areas.
  — needs: small change + a smoke test · found 2026-10-06 (owner request)
- **Artemis downloads again what it already has.** CACHE AREA never checks
  whether the area is cached: it reads CACHE AREA either way and re-downloads
  every layer, though a row's refresh glyph already is the deliberate update.
  Overlapping areas also fetch the same counties' road and water files and the
  shared part of the terrain: two areas cached on 2026-10-06 shared all 3 of
  one's counties and 32% of their terrain box, each paying ~73 MB of grid. The
  owner wants a download once, and again only for an explicit update of a
  cached area. — needs: a design (what CACHE AREA does on a cached area;
  whether and where to keep county files), then change + tests · found
  2026-10-06 (owner request, live check)
- **Demeter's raster stage now costs more memory than its parse.**
  `write_bin` (46 MB) and `write_derived` (95.5 MB) build Python lists over
  the 1024 x 1024 grid on every terrain ingest, with the download still held
  (measured 2026-10-03). Fixed by `GRID`, not by the server, so not a bound to
  enforce; tercile cuts and a PPM built over arrays would cut it.
  — needs: small change + tests · found 2026-10-03 (bounded parsing)
- **Auto-DJ's picks are written nowhere that outlives the process.** Since
  2026-09-17 they live in `_session_plays` (memory) and a DEBUG line, and
  `awen.py` logs nothing at INFO on purpose, so "what did it play while I was
  away?" has no answer. The only trace is mpv's own `logs/mpv/mpv.log`, which
  is overwritten at each launch (the 2026-10-02 run was 44 tracks; the
  earlier runs that day are gone). A persistent play log would reverse the
  rule that the ledger is the listener's list, not a log, so it needs its own
  file, kept apart from `euterpe_ledger.json` and not read back as taste.
  — needs: a choice of file and format, then change + tests · found 2026-10-02
  (afk run, reported by the owner)
- **Emanon's time looks wrong.** The owner left for work around 07:00 on
  2026-10-02 and reports the times Emanon shows don't match that. Not yet
  looked at: could be the displayed timestamps, the "last N" tail ordering, or
  the log's own clock. — needs: reproduce against `felhaven.log` timestamps,
  then diagnose · found 2026-10-02 (owner report)

## Audits

- **Daedalus never had its V0.02 audit** (no marker), and its schema is 37% of
  the tool budget. — needs: an audit per the module walkthrough protocol
  · found 2026-09-27 (V0.03 audit M7)
- **`V0.03:` markers on every module the V0.03 work touched.** Do this last.
  — needs: chore · found 2026-09-27 (V0.03 audit T11)
- **The semgrep tripwire hasn't run over Clio's five new launches** (the face
  renderer, gdigrab and three ffmpeg calls). `test_process_launches` lists
  each one with its sources, but the tripwire is the by-hand check that
  catches a source changing inside a listed call, and its venv isn't
  installed on the Obelisk. — needs: the venv, then the run in the rule's
  header · found 2026-10-28 (Clio)

## Decisions (the owner's call)

- **Horai: a logger, or a written exemption?** It reads only the clock and
  Themis, so it has no failure path to log. It is allowlisted in
  `tests/test_tool_loggers.py`. — needs: decision · found 2026-09-28 (V0.03 audit L5)
- **Golden hour: keep ±1 h, or use sun elevation (−4° to +6°)?** The math is
  there now, but switching changes what the row means.
  — needs: decision · found 2026-09-28 (helios.py docstring)
- **Clio's captures can't show Felhaven's popups.** gdigrab records the
  window by title, and menus and dropdowns are windows of their own, so a
  walkthrough that opens one won't show it. A screen-region mode would record
  them, but also anything lying on top of Felhaven. — needs: decision (add the
  mode, or keep walkthroughs off popups) · found 2026-10-28 (Clio capture)

## Parked, until a trigger

- **Clio's captures would blur on a scaled display.** Felhaven isn't
  DPI-aware, so at a Windows display scale above 100% its window is drawn
  stretched and gdigrab records the stretched pixels. Today the Obelisk runs
  at 100% (96 DPI), so captures are 1:1. — needs: its trigger (a display
  scale above 100%), then a DPI-aware Felhaven or capture at the scaled size
  · found 2026-10-28 (Clio capture)

- **A per-module egress allowlist.** Every runtime read now goes through
  `tools/hermes.py`, the natural home for a list of the hosts each module may
  reach. Today the destinations are constants in code, except Pheme's feeds
  (user-editable config) and `fetch_page` (any public host, behind Pythia's
  dispatch gate), so a list would add little yet. — needs: its trigger (a
  module whose destination becomes user- or model-controlled, or a
  destination nobody expected in a traffic capture) · found 2026-10-01
  (bounded network reads, §12)
- **Write sinks in the semgrep tripwire.** Every path built from the network,
  the model or the keyboard crosses a function boundary before it is written,
  which semgrep can't follow, so sinks would find nothing today (§12,
  2026-10-05). — needs: its trigger (a write path built from such a value
  inside one function), then sinks generated from `tests/test_file_writes.py`
  · found 2026-10-05 (file-write guard)
- **Cerberus's Ledger has no cap.** It grows about 4 KB a day (21 KB since
  2026-09-30), and every vault read rewrites the whole file to bump a count.
  Years before that matters, and capping an access log trades away its
  history. — needs: its trigger (the Ledger past ~1 MB), then a rollover that
  keeps the history · found 2026-10-05 (file-write guard)
- **Tool Broker** (per-turn tool selection). §12 (2026-09-27) says when to
  revisit. — needs: its trigger · found 2026-09-27 (V0.03 audit §6)
- **Freshness cache helper.** Aura and Pheme carry two deliberately parallel
  copies; extract a helper when a third polled tool adopts the pattern (§12,
  2026-09-28). — needs: its trigger · found 2026-09-28 (PR #27)
- **Move to Python 3.14 when kokoro-onnx supports it.** Python 3.13's
  Windows builds stop after its last bugfix release, so later CPython and
  OpenSSL fixes won't reach a Windows install of it. kokoro-onnx still
  declares `<3.14` (upstream issue #187, PR #195); §12 (2026-09-30) says what
  pip does about that. — needs: its trigger (a kokoro-onnx release without
  the cap), then recompile both locks on 3.14 · found 2026-09-30 (pinned
  environment)
- **A passphrase vault for the holdings ledger.** Plutus (the Dynastic Vault's
  LEDGER tab) is unused today, so nothing needs more than DPAPI (§12,
  2026-09-30). Once it holds real holdings, choose between DPAPI and a
  generated six-word passphrase (hyphen-separated, scrypt tuned for unlock
  comfort). — needs: its trigger (real data in Plutus), then that choice
  · found 2026-09-30 (Cerberus v2)
