# FELHAVEN — Backlog

Known, not done. **One job:** things found but not fixed yet, and features
asked for but not built yet, so nothing a session notices or the owner asks
for is lost when the session ends. It is where the owner and the AI sessions
hand work to each other: read it when picking what to do next.

**The rules:**

- **Security issues never go here.** One found in any session is flagged at once
  and fixed in that same session, no exceptions. (This file is also published
  with the public snapshot.)
- **Found something out of scope?** Add a line before the session ends.
- **Want a feature?** Write it as the gap it fills, under "Features", with the
  same `needs` and `found` ending as every other line.
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

- **Pythia narrates a stale reading as current.** gemma4:e2b ignored `stale` in
  every form tried (§12, 2026-09-28). The next thing to try is a Machine Spirit
  rule or a bigger model. — needs: probe · found 2026-09-28 (PR #27)
- **Routing probe row 4 now picks a part that doesn't exist.** "What is
  this PC connected to right now?" calls `get_machine_status(part='argus')`
  4 times in 4, on master and on S17's branch alike (it passed at the
  2026-11-16 baseline, 52/56; today 49/56). `argus` is a Daedalus module
  name, glossed "what this computer is communicating with", not a part;
  the handler refuses it and the answer costs a round. Argus's own text
  hasn't changed since. — needs: probe (what in the prefix moved) · found
  2026-11-19 (V0.03 walkthrough S17)
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
- **The word "archive" steers Pythia to Herodotus even when it names a
  website.** "search the archives of nethys for …" ran `search_documents` on
  the local archive and found nothing; since 2026-11-15 it runs
  `read_archive(search)` the same way (probe row 16). — needs: probe · found
  2026-09-30 (live gate check)
- **Pythia picks °F or °C on its own when it talks temperature.**
  `get_weather` hands the model both (`temp_f`/`temp_c`, highs, lows,
  feels-like), while the panels follow Settings' `themis.temperature_unit()`,
  so a spoken answer can disagree with the screen. Either `handle()` sends
  only the chosen unit, or the reading says which one to use. — needs: probe ·
  found 2026-11-15 (owner's live test of the sky merge)
- **"Weather, sunset and moon?" sometimes skips the weather.** About one run
  in eight calls only `get_sky_report` and answers without `get_weather`, on
  both prompts and on master as well as the archive branch (probe row 9,
  rechecked 2026-11-15). Perhaps one sky report reads as "the outdoors"; the
  sky description could say it carries no weather. — needs: probe · found
  2026-11-15 (Phase 3 recheck)

## Code, ready to start

- **The module intros' "Network / Pythia access" lines are still written by
  hand**, though `TOOL_META` (2026-11-16) now holds what they say. A small
  generator could print them; it needs the owner's line format first.
  — needs: owner · found 2026-09-27 (V0.03 audit M6)
- **Cancelling an Artemis ingest waits out the current download.** The cancel
  flag is read between downloads, and one census zip or terrain grid can take
  minutes (the terrain grid's deadline is ~18 min). hermes could check a cancel
  event before each socket read. — needs: small change + tests · found
  2026-10-01 (bounded network reads)
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
- **The Cartograph's address lookup can freeze Felhaven for up to 20 s.**
  `ArtemisPanel._on_search` calls `eratosthenes.resolve()` on the Tk thread,
  and the Census request runs under hermes's default deadline, twice its
  10 s timeout. A live answer took 0.15 to 0.21 s (2026-11-18), but a hung
  connection stops the clock, the music controls and every other view until
  it gives up. Typed coordinates and waypoints never wait: they resolve
  before any request, and a mistyped coordinate is now refused offline.
  — needs: a design (the search on the panel's ingest worker and queue,
  which widens ArtemisPanel's §6 exception), then change + tests · found
  2026-11-18 (V0.03 walkthrough S11)
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

## Features (need a design first)

- **A fresh clone has no way to get a model.** Pythia answers nothing until
  the user installs Ollama and pulls one by hand, and the MACHINE SPIRIT
  MODEL row lists only what is installed. The owner wants a download option
  there for a short tested list, each with its requirements beside it.
  Candidates were tested 2026-11-10 (CONVENTIONS §12): gemma4:e2b chose
  every tool right, and neither gemma4:e4b nor qwen3.5:4b did, so the
  tested list is gemma4:e2b alone for now, its November build (re-pulled
  and tested the same day). Its requirements, measured: a 4.6 GB download,
  about 3.1 GB of GPU memory at Felhaven's 16k context (NVML; Ollama's own
  figure says 0.3), about 20 s to load. For the download itself: pull
  through Ollama's `/api/pull`
  for progress; check free space on the model store's drive
  (`OLLAMA_MODELS`, not Felhaven's); stop on no progress rather than on a
  whole-request deadline; and make Cancel work (the Artemis cancel item
  above is the same gap). — needs: a design, then change and tests · found
  2026-11-09 (owner request)
- **No brain tool for weather warnings or space weather.** Iris and Eos
  (2026-11-10) are panels only: Pythia can't answer "any weather warnings?"
  or "will I see the aurora tonight?". It waits on the stale-reading item
  above, since both answers go stale in minutes. — needs: the stale-reading
  fix first, then a design · found 2026-11-09 (owner request)

## Audits


- **`fetch_page` decodes every page as UTF-8** (`tools/callimachus.py`),
  whatever charset the server's Content-Type names, so a Latin-1 or
  Windows-1252 page reads with replacement characters where its accents
  were. Not measured on a live page yet. — needs: one non-UTF-8 page
  measured, then decode by the header's charset (falling back to UTF-8 for
  a name Python doesn't know) · found 2026-11-23 (V0.03 walkthrough S33)
- **Other files saved with `os.replace` and read on another thread may race
  on Windows the way Themis's did** (§12, 2026-11-21): Windows refuses the
  rename while a reader has the file open, and the open while the rename
  runs. `machine_spirit_config.json` is one: Pythia's request thread reads
  it every question while MACHINE SPIRIT's SAVE renames it on the Tk thread
  (a lost read answers one question with the default prompt). Ammit's
  state raced too and is locked (S28, §12). Plutus's ledger can't: only the
  Tk thread reads or writes it (S29), and the same holds for Pheme's read
  list and the Scriptorium's open sections (S30). Cerberus's files weren't
  checked. — needs: a check per file, and Themis's lock where one races ·
  found 2026-11-21 (V0.03 walkthrough S24)
- **Home's layout tests hang when more copies run than the PC has cores.**
  Eight copies of `TestTheHomeLayoutAtRealWidths` at once on a 6-core PC:
  every one sat in `top.update()` inside `_bar_at` past 60 s, on master
  too and with Home's poll stopped; four at once finished 24 runs in 24.
  A serial run (CI, the full suite, the publish script) isn't affected.
  Likely a timer that comes due again before a starved process empties
  the queue, so `update()` never returns. — needs: which timer (a stack
  or `after info` from a stuck copy), then a bounded settle · found
  2026-11-23 (public snapshot review)

## Decisions (the owner's call)

- **A second ORPHEUS click while a file plays queues it, under the wrong
  label.** Harmonia appends every `play()` to one buffer, so clicking file B
  while A plays starts B only when A ends, but the transport says "playing:
  B" at once; clicking A twice plays it twice. Stopping first would also cut
  Pythia off mid-answer, since Harmonia has no channels. — needs: decision
  (replace what plays, refuse while something plays, or keep the queue and
  say so) · found 2026-11-21 (V0.03 walkthrough S23)
- **Clio's captures can't show Felhaven's popups.** gdigrab records the
  window by title, and menus and dropdowns are windows of their own, so a
  walkthrough that opens one won't show it. A screen-region mode would record
  them, but also anything lying on top of Felhaven. — needs: decision (add the
  mode, or keep walkthroughs off popups) · found 2026-10-28 (Clio capture)
- **More tooltip surfaces.** Virgil covers the nav rail, the Moderati,
  Cogitator and Vox Array tabs, the Hephaestus readouts, every tab and
  readout in Atmospherics, Home, the glyph controls (transports, remove
  and save glyphs, the Ammit controls, headline arrows, Cartograph's zoom and
  HOME), the inner tab bars and section headers (Dynastic Vault,
  Scriptorium, Morpheus, Ammit, Aether, Argus, Cerberus), and the jargon
  readouts and controls a newcomer can't guess (phase 4, priority A), and
  both secondary windows, the Auto-DJ queue and the Rite Log (phase 5).
  Not yet: the priority B rows outside Home, left out by the owner's choice
  and listed in `specs/virgil_tooltip_surfaces.md`; take one up only
  if a newcomer stumbles on it. Tips are pointer-only,
  too: a widget with keyboard focus shows none. Each is one `_TIPS` table
  and, for a term, a glossary entry (CONVENTIONS §7). — needs: build, one
  phase at a time · found 2026-11-09 (Virgil)
- **Sixty-six glossary entries are stand-in drafts, not the owner's
  words.** `tools/virgil.py` calls the glossary "the owner's words", and
  these were written by a session so the tips could ship: Home's thirteen
  (`vox_response`, `vox_end`, `auto_dj`, `reset_session`, `cerberus_lamp`,
  `scraptoken_flux`, `rites`, `ammit_badge`, `status_line`, `foe_version`,
  `speak_aloud`, `answer_stats`, `file_change`) and the glyph controls' sixteen
  (`transport_prev`, `transport_play`, `transport_next`, `transport_stop`,
  `playlist_save`, `playlist_remove`, `search_save`, `file_remove`,
  `task_check`, `task_delete`, `ammit_minutes`, `ammit_run`, `ammit_reset`,
  `open_story`, `map_zoom`, `map_home`), and the tabs' and section headers'
  thirteen (`scrapstream`, `source_fonts`, `playlists_tab`, `search_tab`,
  `net_connections`, `net_listening`, `net_traffic`, `net_dns`,
  `net_firewall`, `net_timeline`, `vault_keys`, `vault_custody`,
  `vault_ledger`), and the jargon readouts' fifteen (`sky_preset`,
  `sky_clarity`, `map_cache_status`, `cache_area`, `ledger_action`,
  `base_copy`, `base_restore`, `base_take`, `calc_ans`, `log_status`,
  `network_summary`, `import_legend`, `llm_model`, `llm_thinking`,
  `llm_temperature`), the queue window's `queue_remove`, the Rite Log's
  four (`rite_status`, `rite_select`, `rite_expand`, `rite_copy`), and the
  Cartograph's cached-areas four (`cached_areas`, `area_rename`,
  `area_refresh`, `area_delete`). Each says what
  the code does; none
  has had a human read. Edit `config/virgil_glossary.json` while Felhaven
  runs and the next hover shows the change. Later phases add to this list.
  — needs: the owner's edit, entry by entry · found 2026-11-10 (Virgil
  phases 1 to 5)

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
- **Cerberus's Ledger has no cap.** It held 21 KB by 2026-10-05 and 27.8 KB on
  2026-11-14, about 0.2 KB a day since, and every vault read rewrites the whole
  file to bump a count.
  Years before that matters, and capping an access log trades away its
  history. — needs: its trigger (the Ledger past ~1 MB), then a rollover that
  keeps the history · found 2026-10-05 (file-write guard)
- **Tool Broker** (per-turn tool selection). §12 (2026-09-27) says when to
  revisit. — needs: its trigger · found 2026-09-27 (V0.03 audit §6)
- **A glossary tool for Pythia.** Virgil's glossary explains the UI's names
  and readouts on hover, but Pythia can't read it, so "what is the Cogitator?"
  gets a guess. Module glosses already reach her through Daedalus; a glossary
  tool would add prompt tokens to every chat turn on gemma4:e2b. — needs: its
  trigger (other people in the household start using Felhaven), then a
  design session · found 2026-11-09 (Virgil)
- **`read_archive(list)` returns every document.** One entry is about 100 to
  150 characters of JSON, so past ~50 documents the listing passes Pythia's
  8,000-character tripwire, and Pythia sends a tool result to the model
  whole. The archive holds 6 today (2026-11-23). — needs: its trigger (the
  archive past ~50 documents), then a design (the newest N plus a total,
  say) and the probe · found 2026-11-23 (V0.03 walkthrough S32)
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
