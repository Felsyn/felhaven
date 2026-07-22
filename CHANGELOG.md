# FELHAVEN — Changelog

Dated history for the Felhaven dashboard (and the now-retired Metis voice
assistant), newest first.

**One line per change.** What changed, and when. The *why* behind a decision
lives in [`CONVENTIONS.md`](CONVENTIONS.md) §12; the code describes itself.

**Paths are written as they were at the time.** A file that has since moved or
been renamed keeps its old name in older entries. A dated record that gets
quietly edited to match the present isn't a record.

New here? Start with [`README.md`](README.md) for what Felhaven is,
[`SETUP.md`](SETUP.md) to run it, and [`CONVENTIONS.md`](CONVENTIONS.md) for how
the code is put together.

- `2026-07-21` — Default coordinates moved to Royal Observatory, Greenwich (51.4779, 0.0).
- `2026-07-21` — Coordinates swept out of `hypatia.py`, `aura.py`'s docstring, two tests, and `tests/fixtures/wttr_j1.json`.
- `2026-07-21` — `.github/workflows/ci.yml` given a top-level `permissions: contents: read`.
- `2026-07-21` — Two CodeQL clear-text-logging alerts against smoke-test `print()`s dismissed as false positives.
- `2026-07-21` — Hephaestus: `fetch = handle` split into two functions — `handle()` never raises, `fetch()` raises on failure.
- `2026-07-21` — Hephaestus disk usage re-anchored to `__file__` instead of `sys.argv[0]`.
- `2026-07-21` — New `tests/test_hephaestus.py`.
- `2026-07-21` — Pantheon module docstrings corrected — dead upstreams and downstreams, stale config paths, contracts that disagreed with the code.
- `2026-07-21` — `tests/test_pantheon_docs.py` gained a job-line ↔ docstring `Job:` check.
- `2026-07-21` — `CONTRIBUTING.md` rewritten around GitHub Discussions, with a routing table for bugs, questions, ideas, and builds.
- `2026-07-21` — `tests/test_pantheon_docs.py` now asserts against `git ls-files`, not `os.path.exists`.
- `2026-07-21` — CONVENTIONS §10: assert against a fresh clone, not your own checkout.
- `2026-07-21` — `publish/README.md` and `publish/CONTRIBUTING.md` deduplicated against `publish-snapshot.sh` and `SETUP.md`.
- `2026-07-21` — `.gitignore` gained `*.log.[0-9]*` — `*.log` never matched rotated logs.
- `2026-07-21` — Docs trimmed to one job per layer: 4,320 → ~1,320 lines.
- `2026-07-21` — `README_PANTHEON/` flattened to one ~11-line stub per module, matching `tools/*.py`.
- `2026-07-21` — New `tests/test_pantheon_docs.py` pins the stubs to `pythia._TOOL_MODULES` and `_DISPATCH`.
- `2026-07-17` — Finnhub key migrated from `.env` into the Cerberus Vault, read at call time; the local `.env` deleted.
- `2026-07-17` — Midas gained a fifth error code, `vault_locked`, distinct from `no_key`.
- `2026-07-17` — Cerberus Vault gained a generic add/update form, and logs every write to the Ledger — name and action, never the value.
- `2026-07-17` — Cerberus's unlock session is now shared across Midas and the Cerberus tab; `MidasPanel` re-seals within one tick if locked elsewhere.
- `2026-07-17` — New `tests/test_midas.py`.
- `2026-07-17` — Orpheus decodes at 24 kHz mono, not 48 kHz stereo — 4× less RAM (147 MB → 37 MB on a 6m41s file).
- `2026-07-17` — Orpheus's file list shows each file's duration, probed once per file from ffmpeg's stderr banner (no ffprobe).
- `2026-07-17` — Echo's text box and filename entry gained the themed right-click context menu.
- `2026-07-17` — Echo clears both fields on a successful conversion, and leaves them intact on error.
- `2026-07-16` — New `harmonia.py` — sole owner of the audio output device, and the only caller of `sounddevice`.
- `2026-07-16` — Calliope refactored onto Harmonia; its own `_play`, `_play_lock`, and play-worker removed.
- `2026-07-16` — Barge-in is now instant (`sd.stop()`) — and a new question also silences an in-progress Orpheus briefing.
- `2026-07-16` — New `tools/orpheus.py` + `panels/orpheus_panel.py` — the ORPHEUS tab, play/stop for one `.opus` from `local_audio/`.
- `2026-07-16` — `tools/morpheus.py` converted to plural `TOOL_DEFINITIONS`: `play_music`, plus a new `resume_music`.
- `2026-07-16` — CONVENTIONS §2 gained a "Device / infrastructure authority" module flavor.
- `2026-07-16` — Calliope's `_DEFAULTS` pointed at the retired int8 model — corrected to `kokoro-v1.0.onnx`, speed 1.3 → 1.0.
- `2026-07-16` — Four inline literals (`filler_delay_ms`, `prebuffer_chunks`, `first_chunk_max_chars`, `chunk_max_chars`) folded into Calliope's `_DEFAULTS`.
- `2026-07-16` — `readme.txt` renamed to `README.md` so GitHub renders it; four pointers retargeted.
- `2026-07-16` — README's install steps cut in favor of `SETUP.md` — the duplicate had drifted to the abandoned int8 download.
- `2026-07-15` — `publish/CONTRIBUTING.md`: the public snapshot is a periodically refreshed read-only mirror, not "frozen."
- `2026-07-15` — The orphan `(FOE v.01)` label retired; FOE spelled out as Felhaven Operating Environment.
- `2026-07-15` — `readme.txt` corrected to name both API keys — Finnhub in `.env`, Brave in the Cerberus Vault only.
- `2026-07-15` — Public snapshot is now MIT-licensed; `publish/LICENSE` re-applied by `publish-snapshot.sh` on every run.
- `2026-07-15` — `SETUP.md` gained §5 Audio binaries — mpv, yt-dlp, and ffmpeg-with-libopus in `bin/`.
- `2026-07-15` — New Echo (`tools/echo.py` + `panels/echo_panel.py`) — text in, one `.opus` file in `local_audio/` out.
- `2026-07-15` — Four Calliope internals promoted to a public shared seam: `take_chunk`, `save_wav`, `load_wav`, `SAMPLE_RATE`.
- `2026-07-15` — Echo strips Markdown, dropping fenced code blocks entirely, and never trusts a filename raw (`sanitize_filename`).
- `2026-07-15` — `VoxArrayPanel` became a thin tab host (MORPHEUS / ECHO); `MorpheusPanel` refactored `Card` → bare `tk.Frame`.
- `2026-07-12` — New `panels/hestia_panel.py` — the home chat's command surface: narration lamp, Stop, Refresh, Scraptoken Flux, Rites.
- `2026-07-12` — Each answer gained a meta-line — tokens, wall time, tok/s, tool count, failures in red.
- `2026-07-12` — `pythia.ask()` gained an `on_event` callback and a cooperative `cancel` Event.
- `2026-07-12` — Pythia's system prompt moved to `machine_spirit.py`, editable live from a new MACHINE SPIRIT tab.
- `2026-07-12` — Bug: Stop didn't silence the voice — the control is now always clickable and calls `calliope.stop()` unconditionally.
- `2026-07-12` — Right-click context menus added to the chat transcript and input entry.
- `2026-07-10` — Narration made real-time: ~15 s to first word → ~0.01 s, multi-second gaps → ~0 s.
- `2026-07-10` — `pythia.ask()` now streams tokens, with `keep_alive: -1` and a launch-time `prewarm()`.
- `2026-07-10` — Calliope's `speak()` became non-blocking — separate synth and play workers, epoch counter for barge-in.
- `2026-07-10` — Switched to the fp32 kokoro model: RTF 0.44 vs int8's 1.54 on this CPU.
- `2026-07-10` — `chunk_max_chars` set to 70; filler phrases synthesized once at startup and cached in `calliope_fillers/`.
- `2026-07-10` — Voice input retired — `Metis.py`, `apollo.py`, `metis_config.py`, `tools/metis.py`, `panels/metis_panel.py` deleted.
- `2026-07-10` — Three tool registries collapsed to two; `metis_toolbox/__init__.py` is now an empty package marker.
- `2026-07-10` — Calliope moved to `metis_toolbox/calliope.py` and became output-only TTS on kokoro-onnx — no torch.
- `2026-07-10` — Narration lamp added to the header; per-answer `▶ speak aloud` in the home chat.
- `2026-07-07` — New `tools/callimachus.py` — the toolbox's first multi-tool module: `search_web` and `fetch_page`.
- `2026-07-07` — Brave API key lives in the Cerberus Vault only, read at call time.
- `2026-07-07` — Wiring a `tools/` module to `cerberus.py` surfaced and fixed 9 latent `mypy --strict` errors there.
- `2026-07-02` — `morpheus._load_playlists()` promoted to `load_playlists()`.
- `2026-07-02` — `morpheus.status()` batches its property reads over one pipe — worst-case stall ~12 s → ~2 s.
- `2026-07-02` — `MorpheusPanel` flips the ⏯ glyph optimistically instead of waiting a Kairos tick.
- `2026-07-02` — `morpheus.shutdown()` now reaps: terminate, wait, escalate to kill.
- `2026-07-02` — Atomic writes (temp file + `os.replace`) ported to `plutus_ledger.json` and `morpheus_playlists.json`.
- `2026-07-02` — New `tests/test_morpheus_playlists.py`.
- `2026-07-01` — New `tools/kepler.py` — the five classical naked-eye planets as alchemical glyphs on the Celestarium.
- `2026-07-01` — Kepler verified against a live JPL Horizons pull: Mars 0.002° off in RA, 0.004° in Dec.
- `2026-07-01` — First tool module to import a sibling tool module (`from tools import hypatia`).
- `2026-07-01` — Constellation lines brightened one tier; a "Show Constellations" toggle added.
- `2026-07-01` — Celestarium preset row rebuilt as a 2×2 grid — the South Pole button was overflowing the 200px column.
- `2026-07-01` — The constellation lore box got a fixed-height scroll frame, resetting to top on each selection.
- `2026-07-01` — New Hypatia — Celestarium (`tools/hypatia.py` + `panels/hypatia_panel.py`), a live zenith star map.
- `2026-07-01` — ~1,025 stars at mag ≤ 4.5, all 88 IAU constellations, lore from a hand-curated `hypatia_lore.json`.
- `2026-07-01` — Latitude presets (Current / N Pole / Equator / S Pole) dim the panel and show a SIMULATED SKY notice.
- `2026-07-01` — Embedded ConditionsWidget shows clarity, cloud %, and moon illumination; `aura._build()` gained `cloud_cover_pct`.
- `2026-07-01` — CONVENTIONS §12: the naming scheme now admits historical figures of the classical world.
- `2026-06-24` — Sidebar width now scales with the fonts — `rescale_fonts` returns its factor, `Sidebar.rescale()` applies it.
- `2026-06-24` — Phosphor-green monochrome reskin — the `theme.py` `C` palette refolded, zero per-panel edits.
- `2026-06-24` — Sidebar rows became bordered cards with a status dot; structural borders thickened 1 → 2.
- `2026-06-22` — Scribe and Zeno consolidated into a Cogitator view with SCRIBE / ZENO / EUDOXUS tabs.
- `2026-06-22` — The converter moved out of `zeno_panel.py` into new `panels/eudoxus_panel.py`.
- `2026-06-22` — Panels renamed for the gothic-imperial register: Horai→Chronometry, Aura→Atmospherics, Midas→Dynastic Vault, Pheme→Scriptorium, Morpheus→Vox Array. Deity names unchanged underneath.
- `2026-06-22` — New `tools/argus.py` + `panels/argus_panel.py` — read-only network awareness, the third Moderati tab.
- `2026-06-22` — Argus is visibility only: never blocks, kills, captures packets, or elevates.
- `2026-06-22` — Argus surfaces its limits in the UI — interface-level traffic, a DNS cache snapshot, a 5 s-polled timeline.
- `2026-06-22` — PID 0 relabelled `[system]`; ships `tools/argus_peek.py` as a hand-run CLI.
- `2026-06-21` — Hephaestus and Emanon consolidated into a Moderati view with HEPHAESTUS and EMANON tabs.
- `2026-06-21` — Both tab bodies refactored `Card` → bare `tk.Frame`; `kairos.py` needed zero changes.
- `2026-06-12` — Metis V0.1 — the voice assistant went pure-router: `apollo.py` routes, `calliope.py` narrates, the LLM layer is gone.
- `2026-06-12` — `metis_brain.py` archived to `archive/` as the designated escalation layer.
- `2026-06-12` — Whisper moved to `tiny.en` / cpu / int8 — no llama-server, no network at startup.
- `2026-06-12` — Voice stack consolidated into the single `metis_toolbox/.venv` via a new `requirements-metis.txt`.
- `2026-06-10` — New `tests/test_plutus.py` — 20 tests pinning the average-cost fold, verified by mutation.
- `2026-06-10` — CONVENTIONS §12: a full exit clamps cost to 0/0, so re-entry is pinned with a *partial* sell instead.
- `2026-06-10` — Morpheus resumes long videos where you left off (`--save-position-on-quit` plus an explicit `--watch-later-dir`).
- `2026-06-10` — Morpheus PLAYLISTS and SEARCH tabs are independently scrollable.
- `2026-06-10` — New Morpheus (`tools/morpheus.py` + `panels/morpheus_panel.py`) — a YouTube audio player driving headless mpv over JSON IPC.
- `2026-06-10` — Binaries resolve `bin/` first, then PATH; a missing binary degrades to inert controls, never a crash.
- `2026-06-10` — `MorpheusPanel` spawns one daemon thread for the blocking yt-dlp search — the no-threads-in-panels rule's one documented deviation.
- `2026-06-10` — `morpheus.shutdown()` wired into `felhaven._on_close`: no orphan `mpv.exe`.
- `2026-06-09` — Aura split into NOW and FORECAST tabs, with a 3-day outlook and a chance-of-rain row.
- `2026-06-09` — `aura._build()` gained `rain_chance_pct` and a `forecast` list, aggregated from hourly blocks it already fetched.
- `2026-06-09` — New `tools/helios.py` and `tools/selene.py` — sun and moon sub-widgets in Aura's NOW tab.
- `2026-06-09` — `aura._build()` gained an `astronomy` passthrough; all parsing lives downstream.
- `2026-06-07` — Midas gained a stdlib `.env` loader; `FINNHUB_API_KEY` reads from `.env` or the OS environment, OS wins.
- `2026-06-07` — Midas rewritten off `yfinance` onto Finnhub `/quote`; the watchlist moved to `midas_watchlist.json`.
- `2026-06-07` — Midas gained a `no_key` state; crypto and commodities dropped — Finnhub's free `/quote` is US equities.
- `2026-06-07` — New `tools/plutus.py` — an append-only holdings ledger; shares and cost are derived by folding the log, never stored.
- `2026-06-07` — Plutus sells reduce cost at average, not sell price, so "total invested" always means the cost of what you still hold.
- `2026-06-07` — Pheme rewritten from a Hacker-News fetcher into a config-driven RSS/Atom aggregator (`pheme_rumormill.json`, seven feeds).
- `2026-06-07` — `get_hn_stories` and `get_wvnews_stories` collapsed into one `get_news_stories`; `tools/wvnews.py` removed.
- `2026-06-07` — CONVENTIONS §12: The Register's `.atom` URL serves RSS 2.0 — its config row is `format: "rss"` on purpose.
- `2026-06-05` — Aether's second row relabelled "API" → "Anthropic's Status".
- `2026-06-05` — `Card` lost its collapse toggle and header summary; `AmmitWidget` gained its own section toggle.
- `2026-05-29` — WV News added as a third Pheme tab (`tools/wvnews.py`), on a new 15-minute Kairos worker.
- `2026-05-28` — New Emanon (`tools/emanon.py` + `panels/emanon_panel.py`) — a read-only log watcher on a 2 s worker.
- `2026-05-28` — New `metis_logging.py` — one rotating file per program in `logs/`, `|`-delimited so Emanon parses without regex.
- `2026-05-28` — New Zeno (`tools/zeno.py` + CALC tab) — a safe AST-whitelist arithmetic evaluator that shows its work.
- `2026-05-28` — New Eudoxus (`tools/eudoxus.py` + CONVERT tab) — length, volume, weight, temperature, time.
- `2026-05-28` — GUI refactored from a grid of cards into a sidebar + content-area layout; header gained a live clock.
- `2026-05-19` — `Card` gained a collapse toggle, a header summary line, and a `self.body` frame.
- `2026-05-19` — New Pheme (`tools/pheme.py` + `PhemePanel`) — top 5 Hacker News stories on a 15-minute worker.
- `2026-05-19` — New `tools/aether.py` + `AetherWidget` in `VitalsPanel` — Wi-Fi via netsh, API status from status.anthropic.com.
- `2026-05-19` — `BarMeter.set()` accepts floats and shows `<1%` — meters no longer read 0% at idle.
- `2026-05-12` — `TasksPanel` and `NotesPanel` consolidated into `ScribePanel` with an embedded `NotesWidget`.
- `2026-05-12` — New `kairos.py` — the central scheduler; every timed panel now receives data via `update(data)` on the main thread.
- `2026-05-12` — `Kairos.stop()` cancels the pending `after` ID on shutdown.
- `2026-05-12` — `hephaestus` CPU reading changed to the blocking `cpu_percent(interval=1)` — correct now that `fetch()` runs off-thread.
- `2026-05-12` — `hephaestus` disk reading gained `.resolve()` on `sys.argv[0]` for the right drive anchor.
- `2026-05-11` — `horai`, `ammit`, `hephaestus`, `aura`, `midas` moved into `tools/`; `scribe` stayed at root.
- `2026-05-11` — Ammit reduced to a single timer (`MAX_SLOTS = 1`); `AmmitWidget` rewritten as a flat single row.
