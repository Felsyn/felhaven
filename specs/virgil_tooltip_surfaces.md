# Virgil — tooltips for the rest of the panels

**Status:** built (2026-11-10), phases 0 to 5: the bindtag handlers,
`attach_tag()`, `when=`, the per-surface source rule, Home, the glyph
controls, the inner tab bars and section headers, the priority A jargon
readouts and controls, and the queue window's rows. Priority B rows outside
Home are left out by the owner's choice (section 6, decision 2), except the
Rite Log, which the owner asked for afterwards. Every glossary text shipped is a
stand-in until the owner edits it (BACKLOG). Every glossary text shipped so far
is a stand-in; BACKLOG lists the keys that need the owner's edit. Where the build differs from the text below, a *Built:* note
says how; CONVENTIONS §12 (2026-11-10) has the reasons.
**Written:** 2026-11-10, from a read of every
file in `metis_toolbox/panels/` plus `theme.py`, `tools/virgil.py` and
`panels/virgil_tooltip.py`.

**The walkthrough was done on the public snapshot (Felhaven `8a55074`), which
was seven changes behind this repository.** What that means for the text
below:

- **Atmospherics (3.3) is already done**, by #64, and so are the Hephaestus
  depth readouts, the ALERTS tab and the space-weather row. Skip 3.3; the
  glossary keys it proposes are superseded by the ones #64 shipped
  (`high_low`, `uv_index`, `golden_hour`, `moon_illumination`, …).
- **The counts in section 1 and under Totals are stale**: this repository
  had 47 glossary entries before phase 1, not 17, and has 59 after it.
- **Panels changed since the snapshot were not re-walked** for phases 2 to
  5: `hephaestus_panel.py`, `hypatia_panel.py`, `machine_spirit_panel.py`,
  `themis_panel.py` and `sidebar.py`. Re-read each before its phase; the
  rows below for them may be missing a widget or name one already tipped
  (Celestarium already shows `aurora_estimate`).
- Phases 0 and 1 were built against this repository's code, not the
  snapshot's, and Home's two newer strips (weather alerts, aurora) are
  covered.
**Closes:** BACKLOG "More tooltip surfaces" (found 2026-11-09), and goes past
the four surfaces it lists.

## 1. Where Virgil is today

Five `_TIPS` tables, 31 tips:

| Surface | Table | Source |
|---|---|---|
| Nav rail rows and group header | `Sidebar._TIPS` | glossary |
| Moderati, Cogitator, Vox Array tabs | each host's `_TIPS` | module gloss |
| Hephaestus readouts (CPU, RAM, Disk, Wi-Fi, Internet, Ollama) | `VitalsPanel._TIPS` | glossary |

Everything inside a panel body, other than the Hephaestus tab, has no tip.
Home has none at all.

## 2. What has to change in Virgil first (phase 0)

The walkthrough found five things the current mechanism cannot do, or does
unsafely once it is attached more widely. These come before any new table.

### 2.1 Four panels would strip a tip off its own widget

`attach()` binds on the widget with `add="+"`. `<ButtonPress-1>` and
`<Button-1>` are one sequence in Tk, so a later `unbind("<Button-1>")`, or a
`bind()` without `add="+"`, removes the tip's handlers along with the panel's.
Today no tipped widget does that. These would:

| Widget | Where | What it does |
|---|---|---|
| Orpheus ⏹ | `orpheus_panel.py` `_set_stop_enabled` | binds and unbinds `<Button-1>`, `<Enter>`, `<Leave>` on every state change |
| Echo "Send to Echo" | `echo_panel.py:188` | unbinds all three when the gate closes |
| Morpheus search ＋ | `morpheus_panel.py:668` | unbinds all three after a save |
| Artemis CANCEL / CACHE AREA | `artemis_panel.py:1265` | unbinds and rebinds `<Button-1>` |

**Recommended: move the tip's handlers to a bindtag.** `attach()` inserts a
`VirgilTip` tag at the front of `widget.bindtags()` (and a `VirgilPress` tag on
each descendant), and the handlers are bound once per root with `bind_class`.
A panel's own `bind` or `unbind` can then never touch a tip, and the §7 caveat
("bound after `attach()` must use `add="+"`") goes away instead of growing to
cover forty more widgets. First in the tag list means a panel handler that
returns `"break"` cannot starve it either.

The alternative is to leave `attach()` alone and rewrite those four sites to
the guard pattern Morpheus's transport already uses (bind once, check a flag
at click time). That works, but the hazard stays open for the next widget.

Cost of the bindtag route: `virgil_tooltip.py`'s `attach()` and `_on_destroy`,
and the smoke tests that assert on widget bindings (23 tests in
`test_virgil_tooltip_smoke.py`; the ones on timing, placement and teardown are
unaffected).

### 2.2 Text spans have no tip

Home's `▶ speak aloud` control and the per-answer meta-line are tagged spans
inside one `tk.Text`, not widgets. Add
`virgil_tooltip.attach_tag(text_widget, tag, tip)`:

- binds `<Enter>`/`<Leave>`/`<ButtonPress>` with `tag_bind(..., add="+")`;
- the owner is `(widget, tag)`, and `_fire()`'s re-check asks
  `tag in text.tag_names("@x,y")` instead of the widget's rectangle;
- attached to the two **shared** tags, `speak` and `meta`, once at build. The
  per-answer tags (`speak_<n>`, the per-turn rite tags) are unbounded, and a
  tip per answer would grow `_tips` for the life of the session.

### 2.3 The source rule needs a fourth clause, and the test a per-key answer

§7 says: a place is a term, a tab is a module, a readout is a term. The new
surfaces add two cases.

- **A control says what a click does: a term.** Vox Response, ⏭, ✕, CACHE AREA.
- **A section headed by a worker's name says who works there: a module.**
  `HELIOS — SUN`, `SELENE — MOON`, `AMMIT — COUNTDOWN`, `AETHER — CONNECTIVITY`
  and the wireframe face are the tab rule applied to a header. Their glosses
  already read as tips ("Report sunrise, sunset, golden-hour windows, and day
  length.").

A panel's table can therefore hold both kinds. `test_each_surface_uses_its_source`
expects one kind per surface; it becomes "the kind is `module` exactly when the
key is listed in that surface's `_MODULE_TIPS`", so the choice stays written
down and reviewed, not inferred.

*Built:* neither. #64 had already settled this for Atmospherics: one kind
per table, and a widget that needs both splits its table (`_TAB_TIPS`,
`_HEADER_TIP`). Home follows it: the face sits in `HestiaBar._TIPS` (module),
the controls and readouts in `VoxControls._TIPS` and `HomePanel._TIPS`
(terms). The test's `expected` dict names each surface's kind, as before.
Later phases should split tables the same way.

### 2.4 A lamp is a readout, even when it carries a module's name

`● Cerberus` and `AMMIT --:--` on Home's status row need to explain their
*colours*, which the module gloss does not ("Keep the API keys encrypted to
this Windows account…"). They get glossary entries, `cerberus_lamp` and
`ammit_badge`. Neither key is a module key, so decision (3) of 2026-11-09
holds: the entry describes the readout, not the module.

### 2.5 Lockstep for panels that are not tab hosts

`VitalsPanel` is the precedent: `_TIPS` plus a `self._tip_targets` dict filled
as the widgets are built, and a test that the two have the same keys and every
target carries its tip. Every new surface follows it. For rows built per item
(one ✕ per playlist), `_tip_targets` holds the *kind* once (`"remove"`), the
builder attaches to each row's glyph, and the test builds one row and checks
it. `_Tip._tips` already drops an entry on the widget's `<Destroy>`, so rebuilt
rows do not leak.

### 2.6 Checked, no change needed

- **Secondary windows.** RiteLog and QueueBox are Toplevels of the main root;
  `_shared(widget._root())` gives them the same tip window and `lift()` raises
  it over them. Needs one smoke test, not code.
- **Composite controls.** A `VoxToggle` is a frame, a dot and a label that
  bind in `__init__`, before any `attach()`. The pointer-inside `<Leave>` rule
  already handles the composite.
- **Dim controls keep their tip.** A dimmed ⏭ that says what it would do, and
  why it is dim, is the most useful tip on the tab.

## 3. The walkthrough

Priority: **A** — jargon, an in-world name, or a bare glyph; a newcomer cannot
guess it. **B** — guessable, but a tip saves a wrong click or explains a
colour. Section 4 lists what was looked at and left out.

`T` = glossary term (new entry), `M` = module gloss (no new text).

### 3.1 Home — `hestia_panel.py`, `vox_controls.py`, `home_panel.py`

| Widget | Tip | Pri | Draft text (the owner's to rewrite) |
|---|---|---|---|
| Vox Response | T `vox_response` | A | When lit, Pythia reads each answer aloud as it is written. Click to turn it on or off. |
| Vox End | T `vox_end` | A | Stops the answer being written and anything being spoken. Lit while there is something to stop; it works either way. |
| Auto-DJ | T `auto_dj` | A | When lit, a new song is chosen and introduced each time one ends, from what you have played before. It turns itself off after three turns that play nothing. |
| Reset Session | T `reset_session` | A | Clears the conversation, Pythia's memory of it, and the session counters. It stops an answer in progress. |
| Wireframe face | M `pythia_face` | B | — |
| `● Cerberus` | T `cerberus_lamp` | A | The key vault's lock. Dim is sealed, amber is open, red means a tamper alarm; see Moderati, Cerberus. |
| Scraptoken Flux | T `scraptoken_flux` | A | Tokens used this session, questions and answers together. A token is roughly three quarters of a word. |
| Rites (and "· n failed") | T `rites` | A | Tool calls Pythia has made this session, such as a weather or clock lookup. The red count is how many failed. |
| `AMMIT --:--` | T `ammit_badge` | A | The countdown timer from Chronometry. Dim is idle, amber is running, red is time's up, green is an alarm you dismissed. |
| Status line | T `status_line` | B | What Pythia is doing right now. When it shows the Auto-DJ queue, click it to see the songs lined up. |
| `▶ speak aloud` (text tag) | T `speak_aloud` | B | Reads this one answer aloud, replacing anything being spoken. |
| Meta-line (text tag) | T `answer_stats` | A | Tokens, time taken, tokens per second, and tool calls for this answer. Click the tool count to open the Rite Log. |
| ALLOW / DENY | T `file_change` | B | Pythia may change a file only after ALLOW. DENY, Vox End, Reset Session or waiting all mean no. |

The `PYTHIA / ask the oracle` title block is covered by the rail's Home tip and
gets none.

*Built:* all thirteen rows, plus the weather-alert strip and the aurora line
above the status row (which reuse Atmospherics' `weather_alert` and
`aurora_estimate`), in three tables (`VoxControls._TIPS`,
`HestiaBar._TIPS`, `HomePanel._TIPS`) and twelve glossary entries. The texts
shipped are close to the drafts above and are still the owner's to rewrite, in
`config/virgil_glossary.json`. One addition: the status line's tip shows only
while the line has text (`attach(..., when=)`), because the label fills its
row and a tip over a blank stretch explained nothing. Each panel's own smoke
test holds its table to its widgets; Home needs Ammit's state file redirected
to build, so it cannot be built in `test_virgil_surfaces.py`.

### 3.2 Chronometry — `horai_panel.py`

| Widget | Tip | Pri |
|---|---|---|
| Time-of-day badge (`cycle_lbl`) | T `day_cycle` | B |
| Season badge | T `season` | B |
| `AMMIT — COUNTDOWN` header | M `ammit` | A |
| `min` entry, ▶/■, ↺ | T `ammit_minutes`, `ammit_run`, `ammit_reset` | A (glyphs) |

### 3.3 Atmospherics — `aura_panel.py`

| Widget | Tip | Pri |
|---|---|---|
| NOW / FORECAST tabs | T `weather_now`, `weather_forecast` | B |
| Detail rows: feels like, H / L, rain chance, UV | T, one each | A for `H / L` and `UV`; B for the rest |
| wind, humidity | — | skipped, self-evident |
| `HELIOS — SUN` header | M `helios` | A |
| golden AM / golden PM rows | T `golden_hour` (one entry, both rows) | A |
| `SELENE — MOON` header | M `selene` | A |
| illumination row | T `moon_illumination` (shared with Celestarium's "moon illum.") | B |

### 3.4 Celestarium — `hypatia_panel.py`

| Widget | Tip | Pri |
|---|---|---|
| Show Constellations toggle | T `show_constellations` | B |
| Presets: Current, N Pole, Equator, S Pole | T `sky_preset` (one entry, all four) | A |
| `OBSERVATION CONDITIONS` header | T `observation_conditions` | B |
| clarity (stars) | T `sky_clarity` | A |
| cloud cover | — | skipped |
| Status line ("stale", "refreshed …") | T `stale` (shared, see 3.12) | B |

### 3.5 Cartograph — `artemis_panel.py`

| Widget | Tip | Pri |
|---|---|---|
| Status line (NOT CACHED / CACHED / INGESTING) | T `map_cache_status` | A |
| CACHE AREA (and its CONFIRM state) | T `cache_area` | A |
| CANCEL | T `cache_cancel` | B |
| 1× 2× 4× | T `map_zoom` (one entry) | B |
| GO TO entry | T `map_go_to` | B |
| ⌂ HOME | T `map_home` | A |
| CACHED AREAS box header | T `cached_areas` | B |
| Reading line under the map | T `map_reading` | B |

### 3.6 Dynastic Vault — `midas_panel.py`

| Widget | Tip | Pri |
|---|---|---|
| PRICES tab | M `midas` | A |
| LEDGER tab | M `plutus` | A |
| A price row's % change | T `price_change` | B |
| Totals line (shares · invested) | T `ledger_totals` | B |
| BUY/SELL toggle | T `ledger_action` | A (it looks like a button that submits) |
| + ADD | T `ledger_add` | B |

The sealed gate is prose already and gets none.

### 3.7 Scriptorium — `pheme_panel.py`

| Widget | Tip | Pri |
|---|---|---|
| SCRAPSTREAM tab | T `scrapstream` | A |
| SOURCE FONTS tab | T `source_fonts` | A |
| ↗ on a headline | T `open_story` | B |
| Footer "⚠ n failed" | T `feeds_failed` | B |

### 3.8 Vox Array — `morpheus_panel.py`, `echo_panel.py`, `orpheus_panel.py`, `euterpe_panel.py`, `voice_bar_panel.py`

| Widget | Tip | Pri |
|---|---|---|
| Morpheus ⏮ ▶/⏸ ⏭ ⏹ | T `transport_prev`, `_play`, `_next`, `_stop` | A (glyphs; the tip also says why they are dim) |
| Morpheus PLAYLISTS / SEARCH tabs | T `playlists_tab`, `search_tab` | B |
| Playlist ✕ ("remove?") | T `remove_twice` (shared with Orpheus) | A |
| `＋ save` | T `playlist_save` | B |
| Search-result ＋ | T `search_save` | A |
| Echo VOICE dropdown | T `echo_voice` | B |
| Echo filename / Send to Echo | T `echo_send` | B |
| Orpheus ⏹ | T `transport_stop` (shared) | A |
| Orpheus file ✕ | T `remove_twice` | A |
| Tunes SAVE / RELOAD | T `ledger_save`, `ledger_reload` | B |
| Tunes BASE COPY, RESTORE, TAKE FROM SAVED | T `base_copy`, `base_restore`, `base_take` | A |

`VoiceBar` is mounted twice with different meanings, so it takes an optional
`tip=` argument and each mount passes its own (`echo_voice`,
`narration_voice`).

### 3.9 Cogitator — `scribe_panel.py`, `zeno_panel.py`, `eudoxus_panel.py`

| Widget | Tip | Pri |
|---|---|---|
| Scribe ☐/☑ and ✕ | T `task_check`, `task_delete` | B |
| `NOTES — SCRATCH` header | T `scratch_notes` (saved as you type) | B |
| Zeno `ANS = …` | T `calc_ans` | A |
| Zeno / Eudoxus history rows (click to reuse or expand) | T `calc_history` | B |

The entries have placeholder text that already says what to type.

### 3.10 Moderati — `hephaestus_panel.py`, `emanon_panel.py`, `argus_panel.py`, `cerberus_panel.py`, `themis_panel.py`, `machine_spirit_panel.py`, `daedalus_panel.py`

| Widget | Tip | Pri |
|---|---|---|
| Hephaestus `AETHER — CONNECTIVITY` header | M `aether` | B |
| Emanon status dot and line | T `log_status` | A |
| Argus summary dot and line | T `network_summary` (the dot shows visibility, never threat) | A |
| Argus section headers ×6 | T `net_connections`, `net_listening`, `net_traffic`, `net_dns`, `net_firewall`, `net_timeline` | A |
| Argus state codes (EST, TW, CW, SYN, FW1 …) | folded into `net_connections` | A |
| Cerberus 🔒 lock, ↻ refresh | T `vault_lock`, `vault_refresh` | B |
| Cerberus VAULT / CUSTODY / LEDGER headers | T `vault_keys`, `vault_custody`, `vault_ledger` | A |
| Cerberus reveal / REPLACE / SET on a key row | T `key_reveal`, `key_replace` | B |
| Themis Latitude, Longitude, Weather location | T `settings_location` (one entry) | B |
| Themis Temperature, Clock toggles | — | skipped |
| Machine Spirit NARRATION VOICE | T `narration_voice` | B |
| Machine Spirit THINKING, TEMPERATURE | T `llm_thinking`, `llm_temperature` | A |
| Machine Spirit REVERT TO DEFAULT | T `prompt_revert` | B |
| Daedalus OPEN IN NOTEPAD | T `open_in_notepad` | B |
| Daedalus legend ("── import ┄┄ lazy import") | T `import_legend` | A |

The Machine Spirit tab already prints a paragraph under THINKING and
TEMPERATURE. If the tips land, that paragraph can shrink to one line; that is
the owner's call, flagged here so the two do not say the same thing twice.

### 3.11 Secondary windows — `rite_panel.py`, `queue_panel.py`

| Widget | Tip | Pri |
|---|---|---|
| Rite Log status glyphs (✓ ok, error, not run) | T `rite_status` | B |
| Rite Log ▸/▾ expander, copy button | T `rite_expand`, `rite_copy` | B |
| Queue row (one click removes, no confirm) | T `queue_remove` | A |

### 3.12 Shared entries

One glossary entry, many widgets, so the words cannot drift apart:

- `stale` — every "stale" footer (Celestarium, Scriptorium, Argus): the last
  good reading is still shown; the latest fetch failed.
- `section_toggle` — the ▶/▼ on every collapsible header (Ammit, Helios,
  Selene, Argus, Cerberus, Source Fonts, the nav rail group).
- `remove_twice`, `transport_stop`, `moon_illumination`, `golden_hour`.

### Totals

About 95 glossary entries (17 today) and 7 more module tips, on roughly 110
widgets across 22 files. Priority A is about 50 of them.

## 4. Looked at and left out

- **Canvas items.** The sky map's stars and planets, the Daedalus graph nodes
  and the Cartograph instrument are canvas items, not widgets. Each already
  answers a hover or click its own way (the info box, the docstring pane, the
  reading line). A second mechanism would need `attach_item()` and would fight
  the first.
- **Data rows.** Headlines, connections, DNS entries, ledger lines, log
  lines. The header above them carries the tip.
- **Prose that already explains itself.** The Cerberus and Vault gates, the
  Themis and Machine Spirit intro paragraphs, Tunes' hint, empty-state lines.
- **Entries with a placeholder**, right-click menus, the OptionMenu's own
  popup, the Ammit alarm popup, the Sphynx gate (its own `tk.Tk()`, shown
  before Felhaven exists, and a riddle is not helped by a tip).
- **Keyboard focus.** Tips stay pointer-only; that is the other half of the
  same BACKLOG entry and a separate decision.

## 5. Order of work

Each phase is one PR that leaves the suite green, and each new table lands
with its lockstep test and its glossary entries in the same commit, since
`test_every_glossary_entry_is_used_or_excused` fails on an entry no surface
shows.

| Phase | What | Files |
|---|---|---|
| 0 | Bindtag handlers, `attach_tag()`, the per-key source rule, the §7 and §12 text | `virgil_tooltip.py`, `test_virgil_tooltip_smoke.py`, `test_virgil_surfaces.py`, `CONVENTIONS.md` |
| 1 | Home (3.1) | `vox_controls.py`, `hestia_panel.py`, `home_panel.py`, glossary |
| 2 | Bare glyphs everywhere: transports, ✕, ＋, ↗, ↺, ☐, zoom, ⌂, section toggles | Morpheus, Orpheus, Scribe, Horai, Pheme, Artemis, the `_Section` classes |
| 3 | Inner tab bars and worker-named headers | Midas, Pheme, Morpheus, Aura, Horai, Hephaestus, Argus, Cerberus |
| 4 | Jargon readouts and controls (the rest of priority A, then B) | Aura, Hypatia, Artemis, Midas, Euterpe, Machine Spirit, Themis, Zeno, Emanon, Daedalus |
| 5 | Rite Log and queue window | `rite_panel.py`, `queue_panel.py` |

*Built, phase 2:* Morpheus (transport, `＋ save`, playlist ✕, search ＋),
Orpheus (⏹, file ✕), Scribe (task box and ✕), Ammit (minutes, ▶/■, ↺),
Scriptorium (↗) and Cartograph (zoom stops, ⌂ HOME): sixteen glossary
entries. Three differences from the tables above:

- `remove_twice` became two entries, `playlist_remove` and `file_remove`: one
  takes a row off a list, the other sends a file to the Recycle Bin.
- **No `section_toggle`.** #64 put a fold header's tip on the whole header,
  ▶ included, so the fold glyphs of Ammit, Argus and Cerberus get their tip
  with their headers in phase 3, and the `_Section` classes were not touched.
- Per-row glyphs live in a `_ROW_TIPS` table beside `_TIPS` (2.5).

Left for phase 4, as planned: Zeno's history chevrons, Celestarium's
constellations box, Cerberus's lock and refresh.

*Built, phase 3:* the Dynastic Vault's tabs (Midas's and Plutus's glosses),
Scriptorium's and Morpheus's tabs, the AMMIT and AETHER headers (their
modules' glosses), Argus's six section headers and Cerberus's three: thirteen
glossary entries. Atmospherics was already done by #64. Differences from the
tables above:

- The Argus connection codes (EST, TW, …) are named in `net_connections`'s
  text, as planned, but only EST is spelled out; the entry has 240
  characters to work with.
- Scriptorium's per-feed fold headers got no tip: each is a feed's name,
  which is data.
- Still phase 4: the Argus and Emanon summary lines, and everything inside
  the Cerberus sections.

*Built, phase 4:* the priority A rows of 3.4 to 3.10 not already done:
Celestarium's presets and clarity, Cartograph's cache status and CACHE AREA,
the Vault's BUY/SELL, Tunes' BASE COPY, RESTORE and TAKE FROM SAVED, Zeno's
ANS, Emanon's status and Argus's summary, Daedalus's legend, and Machine
Spirit's THINKING and TEMPERATURE: fifteen glossary entries. Differences:

- **MODEL added.** Machine Spirit's MODEL row arrived after the walkthrough
  (#59); its name is as much jargon as THINKING, so it got `llm_model`.
- **The Machine Spirit rows' tips sit on the row**, covering the label and
  its controls, not on the label alone.
- **Decision 4 is moot**: Machine Spirit's explanation was already one line.
- **Cerberus's inner rows** (reveal, REPLACE, lock, refresh) are priority B,
  so out by decision 2, as are Celestarium's Show Constellations, Themis's
  fields and the other B rows.

*Built, phase 5:* the queue window's rows (`queue_remove`), and then, at the
owner's request, the Rite Log: each rite's status (`rite_status`), each
field's ▸/▾ caption (`rite_expand`), COPY THIS RITE (`rite_copy`), and one
the table above missed, each rite's name (`rite_select`), because clicking it
is what selects a rite for COPY THIS RITE and nothing on screen says so.

*Built later, at the owner's request (2026-11-10):* the Cartograph's CACHED
AREAS header (`cached_areas`, priority B in 3.5, so out by decision 2 until
then) and the three glyphs on each of its rows, which table 3.5 missed:
✎ (`area_rename`), ⟳ (`area_refresh`) and ✕ (`area_delete`), in
`ArtemisPanel._ROW_TIPS`.

Phase 1 is where the request started and is useful by itself. Phases 2 to 5
are independent of each other once phase 0 is in.

Per phase, the same checklist:

1. Add the entries to `config/virgil_glossary.json` (snake_case key, not a
   module key, ends in `.`, at most 240 characters).
2. Add `_TIPS` (and `_MODULE_TIPS` where a table mixes kinds) and
   `self._tip_targets`; attach in the build method.
3. Extend `test_virgil_surfaces.py`: table equals targets, every target
   carries its tip, every tip has words.
4. Run `python -X utf8 -m unittest tests.test_virgil tests.test_virgil_tooltip_smoke tests.test_virgil_surfaces tests.test_panel_after_teardown`
   and the panel's own smoke test.
5. Update `README_PANTHEON/virgil.md`'s surface list, the Upstream lines in
   both Virgil docstrings, and CHANGELOG.

## 6. Decisions for the owner

1. **Bindtag or four rewrites** (2.1). Recommended: bindtag.
2. **How far down the list.** Recommended: all of priority A, and priority B
   only on Home, where the request started. That is about 55 entries, not 95.
3. **The glossary texts.** The drafts in 3.1 are placeholders in the house
   voice. The glossary is "the owner's words", so every entry wants a read
   before it ships; the other tables above name the key and leave the words
   for that pass.
4. **Machine Spirit's paragraph** (3.10): keep it, or shorten it once the tips
   say the same thing.
