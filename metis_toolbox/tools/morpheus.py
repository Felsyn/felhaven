"""
morpheus.py — Dream Audio / YouTube Audio Player
=================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Play audio from YouTube — search, play, pause, and skip tracks.
             No video, no thumbnails, no downloads, no transcoding, no play
             history. "I want to listen to this playlist today" — the whole brief.

Contract:    A hybrid flavor, new to the stack — document it here so future-you
             doesn't expect the usual surface:
               - fetch()  : Kairos-polled status read (now-playing, paused,
                            position). READ-ONLY — never mutates playback, and
                            never raises ("morpheus not playing" is a normal
                            state, not a fault — deliberate deviation from the
                            raise-on-failure norm the other fetch()es follow).
                            It DOES carry an "error" slug when a load failed —
                            see "Load failures" below. Read-only still holds:
                            detecting the failure only reads properties.
               - is_audible(): a FREE read of the snapshot fetch() last took,
                            for callers on a fast UI chain (HomePanel's ~16 Hz
                            face tick). No IPC, so it can be one Kairos tick
                            stale. Its docstring says why "running" is not the
                            test — mpv stays up and idle after a track ends.
               - last_snapshot(): the same free read, but the whole dict rather
                            than one derived bool — for a caller that needs more
                            than "is it making noise". Same staleness, same no
                            IPC, and the same reason: status() is a round trip
                            AND judges pending loads, so it must keep exactly
                            one caller (Kairos).
               - mutations: play / toggle_pause / set_paused / next_track /
                            prev_track / stop / set_volume fire from
                            deliberate UI action in
                            MorpheusPanel — plus two non-UI callers: harmonia.py
                            calls duck() at the top of its play() and unduck()
                            from its tick_yield(). duck() runs on whatever
                            thread entered play() — Calliope's release worker
                            (speech) or the UI thread (an Orpheus briefing),
                            never Harmonia's own play worker (which
                            only touches sounddevice); unduck() always runs on
                            the main thread. Safe because _ipc() opens the pipe
                            fresh per call and never raises. Harmonia does NOT
                            call stop() any more — see "Ducking" for why that
                            matters.
             TOOL_DEFINITIONS (plural — see CONVENTIONS §3/§8) exposes two
             tools, split by what they do rather than by verb count:
               - play_music    : "play this song" searches YouTube's top hit
                                 and plays it. STARTS something new. (It
                                 mutates audio, not records, so it's a safe
                                 LLM tool.)
               - control_music : governs what is ALREADY playing — one
                                 `action` enum over pause / resume / stop /
                                 next / previous / replay / status / volume.
                                 See "Control surface" below.
             Five separate transport tools would be five schemas billed on
             every request to a small local model; one enum is the ammit
             (manage_timer) precedent.

Source:      Two external binaries, zero new pip packages, stdlib-only Python.
               - mpv     : headless audio engine, driven over its JSON IPC
                           named pipe. mpv invokes yt-dlp internally to resolve
                           YouTube URLs.
               - yt-dlp  : keyless YouTube search (ytsearch:).
             Resolution (tools/aegis.py, anchored to the app root):
               1. <app root>/bin/mpv.exe and <app root>/bin/yt-dlp.exe
                  (flash-drive-portable), each only if it matches its entry
                  in config/bin_manifest.json; a mismatch is refused and
                  does NOT fall back to step 2
               2. mpv / yt-dlp on PATH, unverified (warned once)
             If either is missing or refused, available() reports it and the
             panel shows a placeholder — we never crash and never raise at
             import time.

Playlists:   config/morpheus_playlists.json under the app root (config-over-code,
             same pattern as pheme_rumormill.json). Adding a playlist is a JSON
             edit, never a code change.

Resume:      mpv saves the playback position on quit (--save-position-on-quit)
             into morpheus_watch_later/. The dir is passed explicitly because
             --no-config disables mpv's default watch-later location; without it
             resume would silently do nothing. Replaying a URL then picks up
             where you left off — long lore videos survive an app restart.
             stop() and play() also force a checkpoint (write-watch-later-config)
             so a mid-session stop or a switch to another video saves your place
             too, not only app close.

Control      control_music is a STATE MACHINE over one `action` enum
surface:     (_CONTROL_ACTIONS), and two of its properties are load-bearing
             rather than incidental:

               - pause/resume are IDEMPOTENT, and route through set_paused(),
                 never toggle_pause(). A toggle is wrong for a caller that
                 cannot see the current state: the model would flip the music
                 back on by retrying a call it thought had timed out. The panel
                 keeps toggle_pause() because a human IS looking at the glyph —
                 two affordances, not duplication.
               - every mutating action is GATED on status()["running"]. _ipc()
                 never raises and silently does nothing when the pipe is down
                 (see _ipc), which is survivable for a button — the human sees
                 nothing happen — but not for a tool, which would otherwise
                 report success into the model's context. Two exceptions, both
                 because they do not depend on a live pipe: replay starts mpv
                 itself via play(), and volume is module state that
                 _ensure_mpv applies on the next spawn.

             Every action returns the post-action status(), so the model can see
             what it did without a second call. Two consequences worth knowing
             before reading a surprising result:
               - after next/previous/replay the title can LAG one Kairos tick,
                 because loadfile is fire-and-forget (see "Load failures").
                 Not worth a sleep to hide; the next poll corrects it.
               - a load failure arrives NESTED, as status["error"]. A top-level
                 "error" means the tool call itself failed; a nested one means
                 the call worked and the player has a problem.

             status() is read-only but not side-effect-free: it runs
             _judge_pending_load, so a tool call can confirm or condemn a
             pending load between Kairos ticks. That is the same verdict Kairos
             would have reached two seconds later, on the same evidence.

Load         loadfile is FIRE-AND-FORGET over IPC: mpv resolves the URL through
failures:    yt-dlp asynchronously and reports nothing back on the write. So a
             load that fails (a dead video, or — the 2026-08-20 outage — a
             yt-dlp too old for YouTube, whose resolved media URL then 403s)
             used to leave the panel showing "(loading…)" forever, with not one
             line in felhaven.log. A whole subsystem could go dark silently.

             The fix is intent-tracking, not event subscription. play() stamps
             _pending_load; status() then watches for mpv having gone back to
             idle with nothing loaded (idle-active True, path None) while a load
             was outstanding, and calls that a failure. Measured signatures
             (2026-08-20, extended 2026-09-16 — real mpv, local file and
             YouTube URL alike):
                 loading   — idle-active False, path <url>, time-pos None,
                             media-title the URL fragment ("watch?v=abc")
                 playing   — idle-active False, path <url>, time-pos advancing
                 ENDED     — idle-active True,  path None,  playlist-count 1
                 skipped   — idle-active True,  path None,  playlist-count 1
                 FAILED    — idle-active True,  path None,  playlist-count 1
                 stopped   — idle-active True,  path None,  playlist-count 0

             Read that table carefully, because it says something stronger than
             "a failed load looks idle". A track that reached its natural end, a
             track ended by next_track(), and a load that never resolved are
             INDISTINGUISHABLE at the property level — same idle-active, same
             path, same playlist-count. The only one any property separates is a
             deliberate stop(), by playlist-count 0.

             So nothing downstream may decide "did that track end or fail?" from
             mpv's properties. It is decided HERE, by whether a load was ever
             confirmed: media that existed and then stopped existing ended, and
             media that never existed failed. That is the same Python-side
             intent this whole section is built on, and the 2026-09-16
             measurement is what promoted it from a claim to a rule.

             mpv never passes through idle-active True during a good load, so
             the signal is definitive rather than a heuristic. _PENDING_GRACE
             exists only to cover the few ms between our loadfile write and mpv
             acting on it — not to guess at how slow a resolve might be.

             Note the trap in that table: `path` is set during LOADING as well
             as during playing, so it means "mpv accepted this URL", not "the
             load worked". Confirmation therefore reads time-pos/duration —
             media existing — and _judge_pending_load explains why at length.

             Since 2026-09-20 status() PUBLISHES `path`, because it is the only
             exact identity in the snapshot and awen needs it to tell its own
             pick from a song the listener started (awen._whose_song). Publishing
             it does not loosen anything above: it still must not confirm a load,
             for exactly the reason in the paragraph before this one. It is also
             stripped from the model's copy (_model_status) — in-process callers
             get it, the model does not.

             Deliberately NOT an event-subscription reader: mpv's end-file event
             would need a persistent pipe connection and a dedicated reader
             thread, and §6 gives timing to Kairos. This rides the 2 s status
             tick that already exists and adds no thread.

             The resulting slug is sticky (_load_error) so the panel can show it
             until the user's next play()/stop(), rather than blinking past in
             one tick. It is a BARE slug — mpv's own text is never spliced in
             (§2: no raw external text into a dict the model and screen read;
             mpv's log lines carry full signed googlevideo URLs). The reason
             goes to the logs instead.

Ducking:     Narration does not STOP music, it lowers it: harmonia.play() calls
             duck() before buffering any audio, and harmonia.tick_yield() calls
             unduck() once its buffer has been quiet for a beat.

             It used to call stop(), and that was a real bug. loadfile is
             fire-and-forget (see "Load failures"), so play_music returned
             {"now_playing": ...} while yt-dlp was still resolving, and the
             answer's own first speech chunk killed the resolve seconds later.
             Measured 2026-09-07 in logs/mpv/mpv.log: loadfile at t=0.223,
             stop at t=2.585, "ytdl_hook: Subprocess failed: killed". Worse,
             stop() disarms load tracking, so the kill reported nothing at
             all — Pythia said the song was playing while mpv sat idle.

             Uses mpv's own `volume` property, NOT `ao-volume` (which is the
             system mixer). Verified settable over this pipe against this
             build (mpv v0.41.0, 2026-08-20): 100.0 -> 30.0 -> 100.0.

             The duck target is a RATIO of the user's level (_DUCK_RATIO), not
             a fixed floor. With the volume at 20, ducking to a constant 30
             would make the music louder during narration — the opposite of
             yielding. At the default the arithmetic is unchanged: 100 × 0.30
             is the same 30.0 the line above was measured at.

             Both calls are WRITE-ONLY — no reply is ever waited on. duck()
             runs on the thread that feeds the audio buffer and unduck() runs
             on the Tk main thread, and _get_props' timeout cannot interrupt a
             readline() already blocking, so a reply-wait on either of those
             threads could stall narration or the UI.

             unduck() restores _user_volume, and it gets that WITHOUT a read.
             This module tracks the level it set, and that tracking is
             AUTHORITATIVE rather than a cache, because nothing else can move
             mpv's volume: --no-config blocks any mpv.conf, there is no window
             and no terminal to take a keypress, --watch-later-options=start
             keeps volume out of resume files (below), and set_volume() is the
             only other writer in the process. So the value a read would return
             is the value we already hold, and the read that would be unsafe on
             these threads is simply not needed.

             This is the load-bearing claim of the whole section. What breaks
             it: a SECOND writer of mpv's volume appearing anywhere, or
             --no-config / --watch-later-options=start being dropped. Any of
             those and the tracked value becomes a guess — at which point the
             read has to come back, and it has to happen off both these
             threads (the Kairos worker is the place).

             The earlier version of this note said a volume control was the
             thing that would break the assumption. It was half right: it broke
             the CONSTANT, not the tracking. Restoring 100 unconditionally
             would have stomped the user's level on the first narrated answer.

             _ducked is a LATCH, not a counter. Harmonia has one buffer and one
             drain, so two overlapping ducks are not a reachable state, and a
             counter would leak a permanent duck on any missed decrement. The
             latch is also what makes the per-chunk call site cheap: only the
             first duck of a turn touches the pipe.

             --watch-later-options=start (see _ensure_mpv) exists FOR this.
             mpv's watch-later files save options that CHANGED since playback
             started, and `volume` is in its default set — so without the flag
             a _checkpoint() fired while ducked would bake volume=30 into that
             track's resume file and quietly restore it on every future play.
             It now keeps a USER-chosen volume out of those files too, which is
             what makes the tracked level above trustworthy.

mpv log:     TWO files in logs/mpv/, because mpv's own log is unusable as-is.

             mpv.log — the raw --log-file. A SCRATCH BUFFER, not something to
             read: ~420 KB per successful track load, dominated by one ~128 KB
             line (mpv logs yt-dlp's whole JSON dump), and it writes that whether
             anything went wrong or not — one measured run was 424 KB and 661
             lines containing zero errors. Its verbosity is NOT tunable:
             --msg-level does not lower it, because mpv forces the log file to at
             least -v -v (verified against the binary, 2026-08-20). Truncated by
             mpv on every start, and deleted by shutdown() once mpv releases the
             handle, so it does not persist between runs.

             mpv.log is CAPPED at _MPV_LOG_MAX_BYTES, checked on each play().
             Nothing else can shrink it: mpv's verbosity is not tunable,
             truncating leaves its file offset stale (the next write re-expands
             the file with NUL padding), and deleting it while mpv holds the
             handle does not return the space on Windows. Restarting mpv does
             all three — it truncates on start and releases the handle — so a
             load that finds the log too big cycles mpv first. Measurements are
             in the constant's comment.

             last_failure.log — the distilled record, and the file the panel
             names. Cut from mpv.log at detection time: a header (wall clock +
             the URL) and mpv's error-level lines from that attempt, ~3 in
             practice. Holds exactly ONE failure, overwritten each time, because
             the most recent is the only one anyone asks for. Survives shutdown.

             Both live in a SUBDIRECTORY of logs/, not beside felhaven.log:
             emanon.py globs logs/*.log and parses every line of every match on
             each tick, and neither file is in the METIS " | " format — a
             subdirectory is invisible to that non-recursive glob.

             Neither is the primary signal: the ERROR line status() writes to
             felhaven.log is what Emanon surfaces on the dashboard, and it stands
             on its own if --log-file is ever dropped entirely.

Upstream:    kairos.py (calls fetch), panels/morpheus_panel.py (UI mutations),
             felhaven.py (calls shutdown() on close)
Tapering:    The volume mpv holds is a COMPOSITION of three factors, written in
             one private place (_apply_volume): the user's level, times the duck
             latch, times a taper gain.

             §12's claim survives intact with one word widened. _user_volume is
             still authoritative and there is still exactly ONE writer — what
             the taper adds is a factor, not a second writer, which is the thing
             that section says would break the model. unduck() routes through
             the same writer for exactly this reason: writing _user_volume flat
             mid-dip would slam the music to full over a fade still running.

             The taper is a bounded DIP — down, hold, up — and its floor is
             never 0. Everything safe about it follows from that: a mispredicted
             handover is one dip-and-swell rather than a silence, is_audible()'s
             `_user_volume > 0` keeps meaning what it says, and nothing ever
             owes a restore, so there is no cancel_taper() to forget. "Cannot
             get stuck" is proved by construction.

             The hold is CONDITIONAL, not timed, since 2026-09-19. taper() arms
             a generous backstop (_TAPER_HOLD_MAX); release_taper() is the real
             release, called by Awen the instant anything becomes audible,
             however long that actually took. Before this split the hold was a
             fixed 6s racing a load that routinely took 4.5-6.5s, so the ramp
             climbed back to full over silence about as often as it caught the
             song — see release_taper()'s own docstring for the measurement.
             The backstop exists only so a load that never lands does not hold
             the floor forever; it is not meant to be the common case.

             tick_fade() rides HomePanel's 60 ms drain, beside
             harmonia.tick_yield() — a passenger on a listed §6 exception, not a
             new one, because Kairos at 2 s cannot ramp anything. Edge-derived
             from one monotonic stamp, so a skipped tick only makes the ramp
             coarser. Free when no taper is running.

             Rate-limited, and that number is measured rather than guessed.
             2026-09-18, against a live mpv on the main thread: _apply_volume()
             is 0.10 ms at the median and 50.7 ms at p95 — the pipe stalls
             occasionally, and this chain is Tk's. At 60 ms a dip costs 85
             writes; at _FADE_MIN_INTERVAL it costs 30.

Metadata:    search() runs --flat-playlist, which is what makes ten results one
             fast call instead of ten slow ones. The cost is that flat entries
             carry no music metadata at all, so the only facts a caller had
             about a candidate were a title, a channel, a duration and a URL.

             describe() is the same call WITHOUT --flat-playlist, for one video,
             for a caller with a reason to pay seconds for it. It REPORTS —
             euterpe owns every judgment about what a song is, exactly as it did
             when a title was all there was (§0).

Downstream:  mpv.exe (audio engine) + yt-dlp.exe (search / URL resolution)

Requires:    mpv + yt-dlp binaries (bin/ or PATH).
             json, logging, os, shutil, subprocess, time (stdlib).

V0.02:       Audited 2026-07-25 — the last output-contract allowlist entry:
             search()'s "search failed" ×3 → "search_failed" (§2 slug), which
             empties the allowlist. Also: dead _get_prop() removed (superseded by
             _get_props); the harmonia caller re-described as running on the
             caller's thread, not a "background play thread"; the playlist path
             corrected to config/. Never-raise verified across fetch/mutations/
             search/play_music/resume_music.

NOTE (2026-08-20, post-audit): --log-file and load-failure detection added after
a real outage in which a stale yt-dlp made every play a no-op — the panel showed
"(loading…)" indefinitely and felhaven.log had not one METIS.morpheus line. The
audit above verified the never-raise contract and the slug vocabulary and both
still hold; what it did not ask was whether a failure could be SEEN. See the
"Load failures" and "mpv log" sections. fetch() gained an "error" key, so the §2
error vocabulary now applies to this module's polled output as well as its tool
handles — the slug is bare and deliberately carries none of mpv's own text.

NOTE (2026-09-19, post-audit): the taper's hold stopped being a fixed 6s and
became a conditional one, released by a new function (release_taper()) rather
than by its own clock. See "Tapering" and release_taper()'s docstring. The
audit above never touched taper() — it did not exist yet — so nothing it
verified is at stake; this is a new capability on the same volume-composition
model, through the same one writer.
"""

import json
import logging
import os
import re
import subprocess
import sys
import time
from typing import Any

# Only a bare `python tools/morpheus.py` needs the app root added before the
# tools/ import (the aura placement); everything else already has it.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import aegis

log = logging.getLogger("METIS.morpheus")

# ── Config / paths ──────────────────────────────────────────────────────────
# App root = one dir up from tools/ (next to felhaven.py). The bin/ dir and the
# playlist config both live here. Anchored to __file__, so cwd never matters.
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PLAYLISTS_PATH = os.path.join(_APP_ROOT, "config", "morpheus_playlists.json")

# Where mpv saves playback positions for resume. Explicit because --no-config
# disables mpv's default watch-later location — see _ensure_mpv.
_WATCH_LATER_DIR = os.path.join(_APP_ROOT, "morpheus_watch_later")

# mpv's own log. A SUBDIRECTORY of logs/ on purpose — emanon.py globs
# logs/*.log (non-recursive) and parses every line of every match each tick;
# mpv's format is foreign to that parser and its volume would be scanned for
# nothing. See the "mpv log" docstring section.
_MPV_LOG_DIR = os.path.join(_APP_ROOT, "logs", "mpv")
_MPV_LOG_FILE = os.path.join(_MPV_LOG_DIR, "mpv.log")

# The distilled record — just the last failure, a few lines. This is the file a
# human opens; mpv.log is the scratch buffer it is cut from (see "mpv log").
_FAILURE_LOG_FILE = os.path.join(_MPV_LOG_DIR, "last_failure.log")

# How much of mpv.log's tail to scan when distilling. A failing load writes
# ~25 KB, and its lines are always at the very end, so this is generous; the
# cap is what keeps the read cheap when mpv.log has grown to tens of MB.
_FAILURE_SCAN_BYTES = 256_000
# How many of mpv's error lines to keep. A real failure produces ~3.
_FAILURE_LOG_LINES = 20

# How large mpv.log may get before we recycle mpv to reset it (~120 track
# loads at the measured ~420 KB each). Felhaven gets left running for days, and
# a single mpv process would otherwise grow this without limit.
#
# Recycling is the only thing that actually reclaims the space. Measured
# 2026-09-09 against this build:
#   - lowering mpv's verbosity does not work — --msg-level=all=error and
#     ytdl_hook=error both leave the log the same size (13.8-14.9 KB at idle
#     startup); mpv forces --log-file to at least -v -v, as the "mpv log"
#     section says.
#   - truncating in place does not work — mpv keeps its old file offset, so its
#     next write re-expands the file with a hole of NUL bytes (13,710 of them).
#   - deleting while mpv runs does not work either — Windows holds the space
#     until the last handle closes, so an unlinked file mpv is still writing to
#     still costs disk. It merely hides the problem.
#   - restarting mpv DOES work: it truncates --log-file on every start (three
#     consecutive runs stayed at ~14 KB), and quitting releases the handle.
_MPV_LOG_MAX_BYTES = 50_000_000
# mpv's own error-level marker, e.g. "[   2.284][e][ytdl_hook] ERROR: ...".
_MPV_ERROR_MARK = "[e]"

# mpv's JSON IPC server. A Windows named pipe — this module is Windows-only by
# design (named pipes, CREATE_NO_WINDOW). Felhaven is a Windows dashboard.
_PIPE = r"\\.\pipe\mpv-felhaven"

# Opening the pipe can transiently fail right after mpv starts (or while it's
# serving another client): Windows ERROR_PIPE_BUSY surfaces as errno 22. Retry
# the open a few times with a short backoff before giving up — bounded so a
# genuinely dead pipe still fails fast.
_OPEN_RETRIES = 5
_OPEN_BACKOFF = 0.05   # seconds between open attempts

# Hide the console window mpv / yt-dlp would otherwise flash. Defined via getattr
# so importing this module on a non-Windows box (e.g. running a unit test) can't
# AttributeError — the value is only meaningful on Windows anyway.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# How long after a loadfile write we refuse to call an idle mpv a failure.
# This covers ONLY the gap between our write and mpv acting on it — mpv leaves
# idle-active as soon as it accepts the command, long before the URL resolves,
# so this is not a resolve-time budget (see the "Load failures" docstring).
_PENDING_GRACE = 4.0   # seconds

# mpv's `volume` property is a 0-100 scale. _FULL_VOLUME is where mpv boots and
# the default the user starts from; _DUCK_RATIO is the FRACTION of the user's
# level narration pulls it down to.
#
# A ratio rather than a fixed floor, and not for elegance: with a user volume of
# 20, ducking to a constant 30 would make the music LOUDER during narration. At
# the default the arithmetic is unchanged (100 × 0.30 = 30.0), so this preserves
# the behaviour measured against mpv v0.41.0 exactly.
_DUCK_RATIO = 0.30
_FULL_VOLUME = 100.0

# ── Taper ─────────────────────────────────────────────────────────────────────
# A bounded DIP: down, hold, up, and then it is over. Auto-DJ arms one when it
# hands one song to the next, so the seam sits under the DJ's voice instead of
# being a cut.
#
# The FLOOR is the load-bearing constant, and it is never 0. Three things follow
# from that single choice:
#   - a mispredicted handover is one dip-and-swell over a song that keeps
#     playing, never a silence;
#   - is_audible()'s `_user_volume > 0` keeps meaning what it says;
#   - nothing ever owes a restore, so there is no cancel_taper() to forget.
# "Cannot get stuck" is then proved by construction rather than by remembering
# to call something.
_TAPER_DOWN = 2.5               # seconds fading out
# Until 2026-09-19 this was a FIXED wait — the ramp climbed back to full on this
# clock whether or not anything was audible yet. Measured against a real
# handover: yt-dlp resolving a queued pick takes roughly 4.5-6.5s, against an
# arming window itself uncertain by up to one 2s Kairos tick (awen._TAPER_LEAD),
# so a fixed 6s hold raced a load it could not see and regularly lost — the
# volume climbed back to full over silence, and the song then started abruptly
# once it actually arrived. release_taper() is the real release now, called the
# instant awen sees anything become audible; THIS is only the backstop for a
# load that never lands, generous on purpose because a hold that expires too
# eagerly reproduces the exact bug it exists to fix. See release_taper().
_TAPER_HOLD_MAX = 12.0          # seconds at the floor, worst case, unreleased
_TAPER_UP = 2.5                 # seconds fading back in
_TAPER_FLOOR = 0.15             # lowest gain. NEVER 0 — see above.

# Volume points. Below this the write is skipped, which is what makes the 6 s
# hold cost zero pipe traffic.
_FADE_MIN_STEP = 1.0

# Seconds between ramp writes. MEASURED 2026-09-18 against a live mpv on the
# main thread: _apply_volume() is 0.10 ms at the median but 50.7 ms at p95 —
# occasionally the named pipe stalls, and this chain is the Tk main thread. At
# 60 ms the two ramps cost 85 writes a dip; at 150 ms they cost 33, and 17 steps
# across a 2.5 s ramp is still smooth. The quantiser alone was not enough
# because the ramp crosses 85 volume points and every one of them clears it.
_FADE_MIN_INTERVAL = 0.15

# ── Module state ──────────────────────────────────────────────────────────────
_proc: "subprocess.Popen[bytes] | None" = None   # the headless mpv process, or None
_request_id = 0                            # IPC request id counter (see _ipc)
_last_url: "str | None" = None             # last URL play() actually loaded (resume_music)
# Load tracking (see "Load failures"). _pending_load is the monotonic stamp of
# the last loadfile we sent and have not yet seen land; _load_error is the
# sticky slug status() reports until the next play()/stop() clears it.
_pending_load: "float | None" = None
_load_error: "str | None" = None
# Ducking (see "Ducking"). A latch, not a counter.
_ducked: bool = False

# The running taper as ONE tuple — (started_at, down, hold, up) — or None.
# Rebound whole and never mutated, the trick status() already uses for
# _last_status, so a reader on another thread sees one record or the other and
# never a torn mix. release_taper() rebinds it too, shortening `hold` rather
# than touching the other three fields — see that function.
_fade: "tuple[float, float, float, float] | None" = None

# Where that taper has got to, 0.15-1.0. Derived from _fade every tick and never
# accumulated, so a skipped tick makes the ramp coarser and changes nothing
# about where it ends up.
_fade_gain: float = 1.0

# The last level actually sent, for the step quantiser. -1 means "unknown".
_volume_written: float = -1.0

# When the ramp last wrote, for the interval limiter above.
_fade_last_write: float = 0.0
# The level the user chose, and what unduck() restores to. AUTHORITATIVE, not a
# cache: this module is mpv's only volume writer, so what we last set is what
# mpv has. See "Ducking" for the full argument and what would invalidate it.
_user_volume: float = _FULL_VOLUME
# The last snapshot status() produced, so a caller on a fast UI chain can ask
# what is playing without paying for an IPC round trip (see is_audible).
_last_status: dict[str, Any] = {"running": False}


# ── Binary resolution ─────────────────────────────────────────────────────────

def _resolve(name: str) -> "str | None":
    """The program to run, vouched for by tools/aegis.py: bin/<name>.exe if it
    matches its manifest entry, else a copy on PATH (unverified, warned once).
    A bin/ copy that doesn't match is refused (None), never swapped for PATH's."""
    return aegis.resolve(name).path


def available() -> dict[str, Any]:
    """
    Report which binaries we can find. The panel reads this once at build time:
    if either is None it shows a placeholder and disables the controls.
    Returns {"mpv": path_or_None, "ytdlp": path_or_None, "problem": None, or
    aegis's slug when a bin/ copy of either was refused}.
    """
    return {"mpv": _resolve("mpv"), "ytdlp": _resolve("yt-dlp"),
            "problem": aegis.resolve("mpv").problem or aegis.resolve("yt-dlp").problem}


# ── mpv lifecycle ─────────────────────────────────────────────────────────────

def _ensure_mpv() -> bool:
    """
    Guarantee a live mpv with its IPC pipe ready. Idempotent: returns True fast
    if mpv is already running. Spawns it otherwise and waits for the named pipe
    to appear (it does not exist until mpv finishes starting up).

    Returns False (logging one ERROR) if a binary is missing or the pipe never
    appears — callers (play) simply do nothing in that case.
    """
    global _proc, _ducked, _fade, _fade_gain, _volume_written, _fade_last_write
    if _proc is not None and _proc.poll() is None:
        return True

    bins = available()
    if not bins["mpv"] or not bins["ytdlp"]:
        log.error("cannot start mpv — missing binary (mpv=%s, yt-dlp=%s)",
                  bins["mpv"], bins["ytdlp"])
        return False

    # Ensure the resume store exists before mpv writes to it. mpv won't reliably
    # create --watch-later-dir itself, and --no-config (below) disables its
    # default location, so we own this directory explicitly.
    try:
        os.makedirs(_WATCH_LATER_DIR, exist_ok=True)
    except OSError as e:
        log.warning("could not create watch-later dir: %s", e)

    # mpv won't create --log-file's directory either. If we can't make it, run
    # without the log rather than risk mpv refusing to start over it — the
    # ERROR line status() writes is the primary signal regardless.
    try:
        os.makedirs(_MPV_LOG_DIR, exist_ok=True)
        log_args = [f"--log-file={_MPV_LOG_FILE}"]
    except OSError as e:
        log.warning("could not create mpv log dir (%s) — running without --log-file", e)
        log_args = []

    args = [
        bins["mpv"],
        # Ignore any user mpv.conf / scripts on the host machine, so Morpheus
        # behaves identically on every PC — the portability rule (CONVENTIONS
        # §11), applied to mpv's own config instead of Python's cwd. Without it,
        # someone who uses mpv normally could have settings that change us.
        "--no-config",
        "--no-video",
        # Never create a video output window, even idle or when a stream carries
        # a video track. --no-video alone isn't enough: a host's force-window=yes
        # (or some ytdl format picks) still pops a black window. CREATE_NO_WINDOW
        # only hides the *console*, not this — hence both are needed.
        "--force-window=no",
        "--idle=yes",
        "--no-terminal",
        "--ytdl-format=bestaudio",
        # Resume long videos across restarts. --save-position-on-quit writes the
        # current position when mpv quits (i.e. on app close via shutdown());
        # replaying the same URL then resumes from there. The dir is explicit
        # because --no-config above disables mpv's default watch-later location,
        # so without it resume would silently do nothing.
        "--save-position-on-quit",
        f"--watch-later-dir={_WATCH_LATER_DIR}",
        # Save ONLY the position in those files. mpv otherwise saves every
        # option that changed since playback started, and `volume` is in its
        # default set — so a checkpoint taken while ducked (see "Ducking")
        # would bake volume=30 into that track's resume file for good. Verified
        # accepted by this build: `mpv --no-config --watch-later-options=start
        # --version` exits 0 (mpv v0.41.0-744, 2026-09-07).
        "--watch-later-options=start",
        f"--input-ipc-server={_PIPE}",
        # Pass our resolved yt-dlp explicitly so the bin/ copy wins over any
        # stale yt-dlp already on PATH.
        f"--script-opts=ytdl_hook-ytdl_path={bins['ytdlp']}",
        # mpv's own diagnostics. --no-terminal + CREATE_NO_WINDOW mean its
        # stderr goes nowhere, so without this a load failure leaves no trace
        # anywhere (see the "mpv log" docstring section).
        *log_args,
    ]
    # A fresh mpv starts at volume 100, so a latch left over from a process
    # that died while ducked would suppress the next duck entirely.
    _ducked = False
    _fade = None
    _fade_gain = 1.0
    _volume_written = -1.0
    _fade_last_write = 0.0
    try:
        _proc = subprocess.Popen(args, creationflags=_NO_WINDOW)
    except Exception as e:
        log.error("failed to spawn mpv: %s", e)
        _proc = None
        return False

    # The pipe is created asynchronously as mpv starts; poll for it before the
    # first command. 10 × 0.2 s == up to 2 s, which is plenty on a cold start.
    for _ in range(10):
        try:
            with open(_PIPE, "r+b", buffering=0):
                pass
        except OSError:
            time.sleep(0.2)
            continue
        # The pipe exists, so a command can land. A fresh mpv boots at 100, and
        # play() recycles mpv when its log grows too big — so without this a
        # respawn silently returns a user who chose 40 to full blast. Skipped at
        # the default, where the write would be a no-op.
        if _user_volume != _FULL_VOLUME:
            _apply_volume()
        return True

    log.error("mpv started but its IPC pipe never appeared")
    return False


def shutdown() -> None:
    """
    Best-effort clean stop. Safe to call when mpv was never started.
    Asks mpv to quit over IPC, then terminates the process if it lingers.
    Hard requirement: no orphan mpv.exe after the app closes.

    Also discards mpv.log once mpv has released it — anything worth keeping was
    already distilled into last_failure.log, which is deliberately NOT touched
    here (a failure you close the app on is the one you come back to read).
    """
    global _proc, _pending_load, _load_error, _ducked, _last_status
    global _fade, _fade_gain, _volume_written, _fade_last_write
    _pending_load = None
    _load_error = None
    _ducked = False
    _fade = None
    _fade_gain = 1.0
    _volume_written = -1.0
    _fade_last_write = 0.0
    _last_status = {"running": False}   # nothing is audible after this
    if _proc is None:
        _discard_mpv_log()
        return
    if _proc.poll() is None:
        _ipc(["quit"])                       # polite ask
        try:
            _proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            _proc.terminate()                # it didn't listen — make it
            try:
                _proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                _proc.kill()                  # still there — belt-and-suspenders
                try:
                    _proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    log.warning("mpv did not exit after terminate()+kill()")
    _proc = None
    _discard_mpv_log()                       # only now is the handle released


def _discard_mpv_log() -> None:
    """Delete the verbose scratch log. Called after mpv has exited — never while
    it holds the handle. Never raises; a log that outlives one run is harmless
    (mpv truncates it on the next start anyway)."""
    try:
        os.remove(_MPV_LOG_FILE)
    except FileNotFoundError:
        pass                                 # never started, or already gone
    except OSError as e:
        log.debug("could not remove mpv log: %s", e)


def _recycle_if_log_is_huge() -> None:
    """Restart mpv when its scratch log has outgrown _MPV_LOG_MAX_BYTES.

    Called from play(), which is both the only thing that grows the log
    (~420 KB per load) and the one moment a restart costs nothing — we are
    about to replace whatever is loaded anyway, so there is no playlist or
    position to lose that loadfile was not already going to discard. mpv quits
    with --save-position-on-quit, so the outgoing track keeps its bookmark.

    Deliberately NOT called from status()/fetch(): those are documented
    READ-ONLY and Kairos drives them every 2 s. A watcher that restarts the
    thing it is watching is not a watcher.

    Never raises — a log we cannot stat is simply not capped."""
    try:
        size = os.path.getsize(_MPV_LOG_FILE)
    except OSError:
        return                               # no log (never started, or no --log-file)
    if size < _MPV_LOG_MAX_BYTES or _proc is None or _proc.poll() is not None:
        return
    log.info("mpv log reached %.1f MB — recycling mpv to reset it", size / 1e6)
    shutdown()          # quits mpv, releases the handle, then deletes the file
    # The caller's _ensure_mpv() respawns. Note the duck latch goes with it
    # (shutdown and _ensure_mpv both clear it): a fresh mpv starts at full
    # volume, so a track loaded mid-narration plays unducked until the next
    # speech chunk re-ducks it, ~one chunk later. Self-correcting, and only
    # reachable once every ~120 loads.


# ── IPC client (stdlib only) ──────────────────────────────────────────────────

def _ipc(command: list[Any], expect_reply: bool = False, timeout: float = 2.0) -> dict[str, Any] | None:
    """
    Send one command to mpv over the IPC pipe. Opens / writes / (reads) / closes
    each call — simple and boring, and at the 2 s status cadence it is cheap and
    avoids stale-handle bookkeeping.

    If expect_reply, read lines until a JSON object whose request_id matches
    ours, skipping mpv's async event objects (multiplexed onto the same pipe).
    The timeout bounds the wait BETWEEN received lines — it is only checked
    after each readline() returns, so it cannot interrupt a single readline()
    that is already blocking. In practice mpv replies fast or streams events,
    so a truly hung read is rare; this is an accepted stdlib tradeoff (fixing
    it would need overlapped I/O, out of scope for this module).

    NEVER raises to callers — a dead pipe is a normal state (mpv idle or gone),
    not an emergency. Returns the reply dict (expect_reply) or None.
    """
    global _request_id
    _request_id += 1
    rid = _request_id
    payload = (json.dumps({"command": command, "request_id": rid}) + "\n").encode("utf-8")

    # Acquire the pipe, retrying the transient "busy" window (errno 22) right
    # after mpv starts. FileNotFoundError is different — the pipe doesn't exist
    # at all (mpv isn't running), so fail fast without retrying.
    pipe = None
    last_err = None
    for _ in range(_OPEN_RETRIES):
        try:
            pipe = open(_PIPE, "r+b", buffering=0)
            break
        except FileNotFoundError:
            return None
        except OSError as e:
            last_err = e
            time.sleep(_OPEN_BACKOFF)
    if pipe is None:
        log.warning("IPC %s: pipe unavailable: %s",
                    command[0] if command else "?", last_err)
        return None

    try:
        with pipe:
            pipe.write(payload)
            if not expect_reply:
                return None

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                line = pipe.readline()
                if not line:
                    break                    # pipe closed / EOF
                try:
                    msg: dict[str, Any] = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                if "event" in msg:
                    continue                 # async event, not our reply
                if msg.get("request_id") == rid:
                    return msg
            return None
    except (OSError, ValueError) as e:
        # FileNotFoundError (pipe gone) is the common, expected case. ValueError
        # guards an odd decode/IO edge. WARNING, not ERROR — this is routine.
        log.warning("IPC %s failed: %s", command[0] if command else "?", e)
        return None


def _get_props(names: list[str], timeout: float = 2.0) -> dict[str, Any]:
    """
    Read several mpv properties over ONE pipe connection — one open/write/read/
    close cycle instead of one per property, so a Kairos tick pays a single
    reply timeout worst-case instead of len(names) of them.

    Returns {name: value_or_None}. Missing/failed reads are None — never
    raises (same contract as _ipc). On any pipe failure returns
    {name: None for name in names}.

    Like _ipc(), the timeout is a single shared deadline checked BETWEEN
    received lines — it does not interrupt a single readline() that is
    already blocking (see _ipc()'s docstring).
    """
    global _request_id
    results = {name: None for name in names}

    # Same open-with-retry logic as _ipc(): retry the transient "busy" window,
    # fail fast if the pipe doesn't exist at all (mpv isn't running).
    pipe = None
    last_err = None
    for _ in range(_OPEN_RETRIES):
        try:
            pipe = open(_PIPE, "r+b", buffering=0)
            break
        except FileNotFoundError:
            return results
        except OSError as e:
            last_err = e
            time.sleep(_OPEN_BACKOFF)
    if pipe is None:
        log.warning("IPC get_props: pipe unavailable: %s", last_err)
        return results

    try:
        with pipe:
            rid_to_name = {}
            for name in names:
                _request_id += 1
                rid = _request_id
                rid_to_name[rid] = name
                payload = (json.dumps({"command": ["get_property", name],
                                       "request_id": rid}) + "\n").encode("utf-8")
                pipe.write(payload)

            pending = set(rid_to_name)
            deadline = time.monotonic() + timeout
            while pending and time.monotonic() < deadline:
                line = pipe.readline()
                if not line:
                    break                    # pipe closed / EOF
                try:
                    msg = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                if "event" in msg:
                    continue                 # async event, not a reply
                rid = msg.get("request_id")
                if rid in pending:
                    results[rid_to_name[rid]] = msg.get("data")
                    pending.discard(rid)
            return results
    except (OSError, ValueError) as e:
        # Same rationale as _ipc(): FileNotFoundError (pipe gone) is the
        # common, expected case. WARNING, not ERROR — this is routine.
        log.warning("IPC get_props failed: %s", e)
        return {name: None for name in names}


# ── Public API — mutations (UI-driven only) ───────────────────────────────────

def _checkpoint() -> None:
    """Force mpv to write the current playback position to its watch-later file
    *without* quitting (write-watch-later-config), so resume survives a
    mid-session stop or a switch to another video — not only app close. No-op
    when nothing is loaded; never raises."""
    _ipc(["write-watch-later-config"])


def play(url: str, *, from_start: bool = False) -> None:
    """Load (and start) a URL, replacing whatever was playing. Starts mpv if
    needed. mpv natively queues a full playlist URL; next/prev then walk it.
    Bookmarks the outgoing track first so switching videos keeps its place.
    Remembers `url` as the resume target (resume_music) once actually loaded —
    not on an early return, so a failed mpv start never overwrites a good one.

    `from_start=True` opens the URL at 0 instead of wherever its watch-later
    file left off. Resume is right for the long lore videos this plays (see
    "Resume") and WRONG for a caller picking songs unattended: a track skipped
    halfway and chosen again later would start halfway. Measured against this
    build (2026-09-16) — stopped at 3.97 s, a plain replay reopened at 4.03 s
    and `start=0` reopened at 0.23 s. It is a per-file option on this one
    loadfile, so nothing about the watch-later mechanism itself changes and
    every other caller keeps resuming.

    Also arms load tracking: loadfile reports nothing back, so status() is what
    finds out whether this actually landed (see "Load failures").

    Caps mpv's scratch log on the way in — this is the only call that grows it
    (see _recycle_if_log_is_huge).

    Takes only an http:// or https:// URL; anything else is refused (logged)
    before mpv starts. mpv's loadfile also opens a local path, and its own
    edl://, lavfi:// and av:// sources. Every caller hands it a search row's
    URL (built by yt-dlp from the video id) or a saved playlist (checked by the
    panel's add form), but the playlists file is hand-editable, so the check
    lives here, at the program boundary, like describe()'s (CONVENTIONS §12,
    2026-10-03). Case-sensitive, so anything unexpected fails closed."""
    global _last_url, _pending_load, _load_error
    if not (isinstance(url, str) and url.startswith(("http://", "https://"))):
        log.warning("play: refused a URL that is not http(s)")
        return
    _recycle_if_log_is_huge()          # before _ensure_mpv, so it respawns clean
    if not _ensure_mpv():
        return
    _checkpoint()                      # bookmark the outgoing track, if any
    if from_start:
        # loadfile <url> <flags> <index> <options> — the -1 is "append at the
        # end", which `replace` makes moot; the options dict is what we are
        # here for. Verified accepted by this build.
        _ipc(["loadfile", url, "replace", -1, {"start": "0"}])
    else:
        _ipc(["loadfile", url, "replace"])
    _last_url = url
    _pending_load = time.monotonic()   # status() confirms or condemns it
    _load_error = None                 # a new attempt clears the last verdict


def toggle_pause() -> None:
    """Flip pause state. For the PANEL's ⏯ button, where a human is looking at
    the glyph and a toggle is what they mean. Callers that cannot see the
    current state want set_paused() instead — see "Control surface"."""
    _ipc(["cycle", "pause"])


def set_paused(flag: bool) -> None:
    """Pause (True) or unpause (False) explicitly. IDEMPOTENT, which is the
    whole point: this is what control_music routes through, because a blind
    caller retrying a toggle turns the music back on."""
    _ipc(["set_property", "pause", flag])


def next_track() -> None:
    """Advance to the next playlist entry — and END the current track when
    there is no next one.

    `force` rather than `weak`, and that is the whole fix: `weak` does NOTHING
    on the LAST playlist entry, and every single track play_music loads is
    exactly that. So ⏭ and control_music(action="next") were silent no-ops for
    every track this module plays — and control_music reported the post-action
    status as if the skip had happened. Measured against this build (mpv
    v0.41, 2026-09-16): with one entry loaded, `playlist-next weak` left
    time-pos advancing 2.96 -> 4.46, while `force` ended it into the
    idle-active/playlist-count 1 shape a natural end leaves (see "Load
    failures").

    The two differ ONLY on the last entry — mid-playlist both advance — so
    walking a real playlist is unchanged.
    """
    _ipc(["playlist-next", "force"])


def prev_track() -> None:
    """Step back one playlist entry. Deliberately still `weak`, unlike
    next_track(): running forward off the END of a playlist means "this track
    is over", which is a real destination, while running back off the FRONT
    would mean ending playback — which is not what ⏮ says it does."""
    _ipc(["playlist-prev", "weak"])


def stop() -> None:
    """Stop playback. mpv stays idle and alive, ready for the next play().
    Bookmarks the current position first, so resume works after a mid-session
    stop and not only on app close.

    Disarms load tracking: a deliberate stop leaves mpv in exactly the idle
    state a failed load does, and it is not a failure. The callers that need
    that are MorpheusPanel's stop button — which can be pressed while a load is
    still resolving — and shutdown(). Narration is NOT one of them any more: it
    ducks the volume instead of stopping (see "Ducking"), precisely because
    this clause, written to suppress a false positive, was also suppressing the
    true one the yield itself was causing."""
    global _pending_load, _load_error
    _pending_load = None
    _load_error = None
    _checkpoint()
    _ipc(["stop"])


# ── Public API — ducking (harmonia's yield) ───────────────────────────────────

def _effective_volume() -> float:
    """What mpv should be holding right now.

    The user's level, times the duck latch, times the taper gain. _user_volume
    stays the USER's level and stays authoritative — §12's claim is unchanged
    with one word widened: the value a read would return is the value this
    module already holds, and "holds" is now this composition rather than one
    variable. There is still exactly one writer and still no read-back.
    """
    level = _user_volume * (_DUCK_RATIO if _ducked else 1.0) * _fade_gain
    return max(0.0, min(_FULL_VOLUME, level))


def _apply_volume(*, force: bool = True) -> None:
    """THE write. Every path that moves any of the three factors ends here.

    Adding a second writer of mpv's volume is what §12 says breaks the whole
    model, so the taper is not one: it moves a factor and calls this.

    Write-only and fire-and-forget, for the reason duck() gives at length — this
    also runs on the Tk main thread at 16 Hz, where waiting on a reply would
    stall the UI as surely as it would stall narration.

    `force=False` applies the step quantiser, for the ramp: below
    _FADE_MIN_STEP volume points nobody can hear the difference and the write is
    skipped, which is what makes the taper's 6 s hold cost zero pipe traffic.
    Never raises (_ipc doesn't).
    """
    global _volume_written
    if _proc is None or _proc.poll() is not None:
        return                              # no mpv — nothing to set
    level = _effective_volume()
    if not force and abs(level - _volume_written) < _FADE_MIN_STEP:
        return
    _ipc(["set_property", "volume", level])
    _volume_written = level


def _gain_at(record: "tuple[float, float, float, float]", now: float) -> float:
    """Where a taper has got to: 1.0 before and after, _TAPER_FLOOR across the
    hold, linear in between.

    Derived from one monotonic stamp rather than accumulated, which is what
    makes a skipped tick harmless and an expiry automatic. `hold` is whatever
    the record currently says it is — release_taper() rewrites it in place of
    the record to cut the hold short, and this function does not need to know
    that happened; it just reads the field.
    """
    start, down, hold, up = record
    elapsed = now - start
    if elapsed <= 0:
        return 1.0
    span = 1.0 - _TAPER_FLOOR
    if elapsed < down:
        return 1.0 - span * (elapsed / down)
    if elapsed < down + hold:
        return _TAPER_FLOOR
    if elapsed < down + hold + up:
        return _TAPER_FLOOR + span * ((elapsed - down - hold) / up)
    return 1.0                              # expired


def taper() -> None:
    """Dip the music and bring it back. Never raises.

    For a handover: the outgoing song falls away, the swap happens under the
    floor, and the incoming one rises. Auto-DJ arms it; this module owns it,
    because this module owns the player (§0).

    Writes NOTHING. The next tick_fade() does, which keeps every volume write on
    the one thread that is allowed to be slow about it.

    The hold armed here is `_TAPER_HOLD_MAX` — a BACKSTOP, not the real release.
    The real one is release_taper(), called the instant the incoming song is
    confirmed audible, however long that actually took. See its docstring for
    why the two are split.

    IGNORED while one is already running, so a trigger that fires repeatedly —
    a snapshot that keeps saying "nearly over" — cannot extend the hold for
    ever. The dip is bounded and expires on its own; nothing ever owes a
    restore, which is why there is no cancel_taper() to forget.
    """
    global _fade, _fade_last_write
    if _fade is not None:
        return
    _fade = (time.monotonic(), _TAPER_DOWN, _TAPER_HOLD_MAX, _TAPER_UP)
    _fade_last_write = 0.0


def release_taper() -> None:
    """End the hold early: the incoming song is confirmed audible, so stop
    waiting and start the up-ramp NOW. Never raises.

    Until 2026-09-19 the hold was a fixed 6s, on a clock that had no idea
    whether yt-dlp had actually resolved anything — a load routinely took
    4.5-6.5s against an arming window uncertain by up to one 2s Kairos tick, so
    the ramp climbed back to full over silence about as often as it caught the
    song. Awen calls this the instant it sees ANY media become audible
    (awen._note_playing), which is the one signal that was always the right
    clock and was simply never wired to this one.

    Rewrites the RECORD rather than the gain: `hold` is shortened to exactly
    "how long the down-ramp and the hold have actually run", which is a no-op
    if release comes after the hold would have ended anyway (nothing to
    shorten) and, if release comes mid-down-ramp, lets that ramp finish before
    the up-ramp begins rather than reversing direction mid-fall. Either way
    _gain_at reads the same record it always did and does not need to know a
    release happened — continuity comes from the arithmetic, not from a special
    case here.

    A no-op with no taper running, or one already past its hold — release_taper
    only ever pulls the up-ramp EARLIER, never later, so a second call (or a
    call after _TAPER_HOLD_MAX has already released it) costs one comparison.
    """
    global _fade
    record = _fade
    if record is None:
        return
    start, down, hold, up = record
    elapsed = time.monotonic() - start
    if elapsed >= down + hold:
        return                              # already released, or expired
    _fade = (start, down, max(0.0, elapsed - down), up)


def tick_fade() -> None:
    """Advance a running taper. Call from the MAIN THREAD, on a chain that
    already exists — HomePanel's 60 ms drain, beside harmonia.tick_yield().
    Never raises.

    A passenger on a listed §6 exception, not a new one: Kairos at 2 s cannot
    ramp anything, and a thread or an after() loop of its own would be a new
    exception in a section that says to stop and give Kairos a worker instead.

    FREE unless a taper is running — one identity check — so an idle dashboard
    does zero IPC at 16 Hz, the same bargain tick_yield() already makes.

    Edge-derived: every tick re-reads the clock and recomputes the gain, so a
    skipped tick simply makes the ramp one step coarser. There is no counter to
    fall behind and no state that a missed call can strand.
    """
    global _fade, _fade_gain, _fade_last_write
    record = _fade
    if record is None:
        return
    start, down, hold, up = record
    now = time.monotonic()
    # Expiry is read off the CLOCK, never off the gain. The gain is legitimately
    # 1.0 at the very start of the ramp as well as at its end, so "gain >= 1.0"
    # means finished only if you never tick on the same instant you armed —
    # which the 60 ms chain is under no obligation to avoid.
    if now - start >= down + hold + up:
        _fade = None
        _fade_gain = 1.0
        _apply_volume()                     # forced: land exactly on the level
        return
    # Rate-limited, and the EXPIRY above deliberately is not: the last write of
    # a dip must land exactly on the user's level whatever the clock says, or a
    # taper can leave the music a point or two low for ever.
    if now - _fade_last_write < _FADE_MIN_INTERVAL:
        return
    _fade_last_write = now
    _fade_gain = _gain_at(record, now)
    _apply_volume(force=False)


def duck() -> None:
    """Lower mpv's volume so narration can be heard over it. Harmonia calls
    this before it buffers ANY audio — ONCE PER CHUNK, on whatever thread
    entered its play().

    That cadence is why _ducked is a latch: the first call of a turn costs one
    pipe transaction, every later one returns after a poll() and a bool.

    Costs nothing when mpv is not running: the same _proc guard status() uses,
    so an idle dashboard generates no IPC churn. Never raises (_ipc doesn't).

    WRITE-ONLY, and that is load-bearing rather than a simplification. This runs
    on the thread that FEEDS THE AUDIO BUFFER, and _ipc() without expect_reply
    opens, writes and closes without ever waiting on a reply. It must never read
    the volume back first — _get_props' own timeout "cannot interrupt a single
    readline() that is already blocking", so a jammed pipe would stall narration
    itself. It does not need to: _user_volume already IS the answer a read would
    return (see "Ducking").

    Note what it does NOT touch: _pending_load. That is the entire point — a
    still-resolving loadfile now survives narration (see "Ducking").

    Known and accepted: starting a track from the panel WHILE narration is
    speaking begins it ducked, and it comes up when the narration ends. Having
    play() clear the latch would defeat the yield on exactly the turn the user
    is most likely to be listening to."""
    global _ducked
    if _proc is None or _proc.poll() is not None:
        return                              # no mpv — nothing to duck
    if _ducked:
        return                              # already quiet; no pipe traffic
    _ducked = True
    _apply_volume()


def unduck() -> None:
    """Put the volume back up — to the USER's level, not to full. Harmonia
    calls this from tick_yield() once its buffer has been quiet long enough.

    Free when nothing is ducked — one bool — so it is safe to call at whatever
    cadence the caller's existing chain runs at (60 ms, today). That matters:
    the caller is the Tk MAIN THREAD, so like duck() this is write-only and
    never waits for a reply. Measured worst case against a live mpv with a
    Kairos-rate poller running alongside: 63 ms.

    The latch is cleared FIRST, so an mpv that died while ducked still leaves
    clean state behind. Never raises."""
    global _ducked
    if not _ducked:
        return
    _ducked = False
    # Through the composed writer, NOT a bare _user_volume. Mid-taper at gain
    # 0.4 with the user at 100 this writes 40 and the ramp carries on from
    # there; writing 100 here would slam the music to full over the top of a
    # fade that is still running.
    _apply_volume()


def set_volume(level: float) -> float:
    """Set the music volume, 0-100. Returns the level actually stored (clamped).

    Deliberately does NOT write to mpv while ducked. Narration is speaking in
    that window, and pushing the new level straight through would undo the duck
    — the music would jump to full over the answer. The level is recorded and
    unduck() applies it when the duck lifts, which is a beat later and is what
    the user meant anyway. This is the one branch here worth reading twice.

    Write-only like duck()/unduck(), and for a weaker reason: this one is
    called from the Kairos worker or the UI, not the audio thread. It stays
    write-only for consistency, and because there is nothing to read back.
    Never raises."""
    global _user_volume
    _user_volume = max(0.0, min(100.0, float(level)))
    if _ducked:
        return _user_volume         # unduck() will apply it — see above
    _apply_volume()             # no-op when mpv is not running
    return _user_volume


# ── Public API — keyless search (BLOCKING — caller threads it) ────────────────

def search(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """
    Keyless YouTube search via yt-dlp's ytsearch. BLOCKS for seconds —
    MorpheusPanel calls this on a daemon thread, never the main thread.

    Returns up to `limit` rows: {"title", "channel", "duration", "url"}.
    On any failure returns [{"error": "search_failed"}] so the panel can render
    a single error row.
    """
    ytdlp = _resolve("yt-dlp")
    if not ytdlp:
        log.warning("search: yt-dlp not found")
        return [{"error": "search_failed"}]

    args = [ytdlp, f"ytsearch{limit}:{query}", "--flat-playlist", "-J", "--no-warnings"]
    try:
        proc = subprocess.run(args, capture_output=True, timeout=20,
                              creationflags=_NO_WINDOW)
        if proc.returncode != 0:
            log.warning("search: yt-dlp exit %s: %s",
                        proc.returncode, proc.stderr.decode("utf-8", "replace")[:200])
            return [{"error": "search_failed"}]
        data = json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        log.warning("search failed: %s", e)
        return [{"error": "search_failed"}]

    rows = []
    for entry in data.get("entries") or []:
        vid = entry.get("id")
        rows.append({
            "title":    entry.get("title") or "(untitled)",
            "channel":  entry.get("channel") or entry.get("uploader") or "",
            "duration": entry.get("duration"),      # seconds, may be None
            "url":      entry.get("url") or (f"https://www.youtube.com/watch?v={vid}"
                                         if vid else ""),
        })
    return rows


# What describe() keeps. Everything else yt-dlp returns is dropped, and two
# omissions are deliberate: `description` and `tags` are unbounded,
# uploader-controlled text heading into a small model's context, and they
# decide nothing that categories/track/artist do not decide better.
_DESCRIBE_FIELDS = ("title", "channel", "duration", "categories",
                    "track", "artist", "album")

# The only URL describe() hands to yt-dlp: one YouTube watch page, by its
# 11-character id. The url comes from yt-dlp's own search JSON, which builds it
# from the id today, so nothing we know of can make it start with "-". But it
# is still network data in an argument slot, so it is checked here and rebuilt
# from the id, and "--" goes before it (CONVENTIONS §12, 2026-10-01).
# Used with fullmatch(), not "^...$": "$" also matches before a final newline.
_WATCH_URL = re.compile(r"https://(?:www\.)?youtube\.com/watch\?v=([A-Za-z0-9_-]{11})")


def describe(url: str) -> dict[str, Any]:
    """One FULL yt-dlp extraction for a single video. BLOCKS for seconds.

    search() runs with --flat-playlist, which is right for a search — it is what
    makes ten results one fast call instead of ten slow ones. The cost is that
    flat entries carry no music metadata at all, so the only facts any caller
    had about a candidate were a title, a channel, a duration and a URL. That is
    how a video about sharpening a chainsaw was played and announced as Linkin
    Park: no rule that reads only a title and a duration could have caught it.

    This is the same call WITHOUT --flat-playlist, for one video, when a caller
    has a reason to pay for it. Reports; decides nothing — euterpe.is_a_song()
    and euterpe.has_music_evidence() own the judgment, exactly as they did when
    a title was all there was (§0).

    Returns the _DESCRIBE_FIELDS it could read, or {"error": "describe_failed"}
    on any failure — a bare slug (§2), never yt-dlp's stderr, which carries
    signed URLs. Never raises.

    Takes only a youtube.com watch URL (_WATCH_URL). Anything else is refused
    before yt-dlp runs, and what does run is the URL rebuilt from its id,
    after "--", so yt-dlp can never read it as an option.
    """
    found = _WATCH_URL.fullmatch(url)
    if found is None:
        log.warning("describe: refused a URL that is not a YouTube watch URL")
        return {"error": "describe_failed"}
    watch = f"https://www.youtube.com/watch?v={found.group(1)}"

    ytdlp = _resolve("yt-dlp")
    if not ytdlp:
        log.warning("describe: yt-dlp not found")
        return {"error": "describe_failed"}

    args = [ytdlp, "-J", "--no-playlist", "--no-warnings", "--", watch]
    try:
        proc = subprocess.run(args, capture_output=True, timeout=20,
                              creationflags=_NO_WINDOW)
        if proc.returncode != 0:
            log.warning("describe: yt-dlp exit %s: %s",
                        proc.returncode,
                        proc.stderr.decode("utf-8", "replace")[:200])
            return {"error": "describe_failed"}
        data = json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        log.warning("describe failed: %s", e)
        return {"error": "describe_failed"}

    if not isinstance(data, dict):
        return {"error": "describe_failed"}
    out: dict[str, Any] = {}
    for field in _DESCRIBE_FIELDS:
        if field in data:
            out[field] = data[field]
    # Same fallback search() uses, so the two agree about what a channel is.
    if not out.get("channel") and data.get("uploader"):
        out["channel"] = data["uploader"]
    return out


# ── Public API — status / Kairos entry point ──────────────────────────────────

def status() -> dict[str, Any]:
    """
    Current playback snapshot. {"running": False} when mpv isn't up — and we
    return that WITHOUT touching the pipe, so an idle dashboard generates no IPC
    churn and no Emanon noise (idle is the normal state).

    Also the load-failure detector (see "Load failures"): it CONFIRMS a pending
    load the moment mpv reports something loaded, and CONDEMNS one that left mpv
    idle-with-nothing-loaded. Read-only either way — it only reads properties;
    the verdict is Python-side state.
    """
    global _pending_load, _load_error, _last_status

    if _proc is None or _proc.poll() is not None:
        _last_status = {"running": False}
        return dict(_last_status)

    props = _get_props(["media-title", "pause", "time-pos", "duration",
                        "playlist-pos", "playlist-count", "idle-active",
                        "path"])

    # Liveness check: if EVERY property came back None, the pipe is dead or
    # unresponsive even though the process is alive (rare) — fall back to
    # idle. A missing title ALONE (everything else live) is not a liveness
    # failure — the panel renders that as "(loading…)".
    #
    # Note what a resolving stream actually reports, because this comment used
    # to say the opposite: media-title is the URL FRAGMENT, not None —
    # "watch?v=tCDvOQI3pco" (measured 2026-09-16, matching the _LOADING fixture
    # in tests/test_morpheus_load_failure.py). None appears only in the
    # sub-tick between our loadfile write and mpv accepting it. Either way the
    # title is not the evidence; time-pos/duration is (see _judge_pending_load).
    #
    # A live mpv always answers idle-active, so all-None really does mean the
    # pipe is gone; we must NOT read that as a load failure.
    if all(v is None for v in props.values()):
        _last_status = {"running": False}
        return dict(_last_status)

    _judge_pending_load(props)

    out: dict[str, Any] = {
        "running": True,
        "title":  props["media-title"],         # str | None
        "paused": props["pause"],                # bool | None
        "pos":    props["time-pos"],             # float seconds | None
        "dur":    props["duration"],             # float seconds | None
        "pl_pos": props["playlist-pos"],         # int | None
        "pl_n":   props["playlist-count"],       # int | None
        # The two fields that separate mpv's three idle-looking states. Both
        # were already computed — idle-active is read for _judge_pending_load
        # and _pending_load is its verdict — and simply never published, which
        # left NO caller able to tell a stream still resolving from one that
        # has ended. See "Load failures" for the full measured table.
        "idle":    props["idle-active"],         # bool | None
        # WHAT is loaded, as opposed to what it is CALLED. The one exact
        # identity in this snapshot: media-title is text, and yt-dlp and mpv
        # spell the same upload differently often enough that a caller deciding
        # "is this the thing I asked for?" off two spellings is deciding it off
        # a coincidence (awen._whose_song, and the 2026-09-20 scar there).
        #
        # Published rather than _last_url deliberately, and the difference is
        # the one this module is built on. _last_url is INTENT — it is set the
        # instant loadfile is written, and it still names a URL after that load
        # FAILED, is a whole track stale after a playlist walk or next_track(),
        # and already names the new song in the window where mpv is still
        # sounding the old one. `path` is what mpv HOLDS. The question a caller
        # asks here is "which URL produced the audio I can hear", and only one
        # of those two answers it.
        #
        # This does NOT loosen _judge_pending_load. `path` is set while yt-dlp
        # is still resolving (see "Load failures"), so it must never confirm a
        # load; that stays time-pos/duration, and the field being in `props`
        # now is not permission to read it there.
        "path":    props["path"],                # str | None — URL or file
        "loading": _pending_load is not None,    # bool — a load not yet judged
        # Not read off the pipe — we are mpv's only volume writer, so this is
        # exact and free. It is also what lets the model answer "turn it down a
        # bit": read the level, do the arithmetic, set the result.
        "volume": _user_volume,                  # float 0-100
    }
    if _load_error is not None:
        out["error"] = _load_error              # bare slug (§2)
    _last_status = out
    return dict(out)


def is_audible() -> bool:
    """True when music is actually making sound right now.

    Reads the LAST snapshot status() produced instead of taking a fresh one,
    because the caller is HomePanel's face tick at ~16 Hz and an IPC round trip
    at that cadence is exactly what Kairos's 2 s poll exists to avoid. So the
    answer can be up to one tick stale — the right trade for a mood light, and
    the wrong one for anything that acts on it. Never does IPC, never raises.

    "Running" is NOT the test. mpv stays alive and idle after a track ends or a
    load fails, so running alone would leave the face lit forever on a finished
    playlist. Audible means media EXISTS and is not paused, and media-exists is
    time-pos/duration — the same evidence _judge_pending_load uses to confirm a
    load, for the same reason (`path` is set while yt-dlp is still resolving).

    Volume 0 is silence, so it is not audible either — a track playing into a
    muted output should not sit the face on amber.
    """
    return bool(
        _last_status.get("running")
        and not _last_status.get("paused")
        and (_last_status.get("pos") is not None
             or _last_status.get("dur") is not None)
        and _user_volume > 0
    )


def last_snapshot() -> dict[str, Any]:
    """A COPY of the snapshot status() last took. FREE — never touches the pipe,
    never raises, and mutating the result cannot reach this module.

    The is_audible() bargain, generalised: a caller that wants more than one
    field would otherwise call status(), which is an mpv IPC round trip AND runs
    _judge_pending_load — so a second caller polling it would start confirming
    and condemning loads on its own schedule, between Kairos's ticks. This hands
    over the same dict Kairos's 2 s poll already paid for, so the answer can be
    up to one tick stale and costs nothing.

    Safe to read off another thread: status() REBINDS _last_status to a freshly
    built dict rather than mutating the live one, so a reader sees either the
    whole old snapshot or the whole new one, never a torn mix.

    {"running": False} before anything has been polled — the same shape status()
    returns for a dead or absent mpv, so callers need no special first-run case.
    """
    return dict(_last_status)


def _mpv_error_lines() -> list[str]:
    """mpv's error-level lines from the tail of its log, most recent last.

    Bounded read: mpv.log can be tens of MB, and everything we want is at the
    end. Never raises — a missing or unreadable log just means no lines."""
    try:
        size = os.path.getsize(_MPV_LOG_FILE)
        with open(_MPV_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            if size > _FAILURE_SCAN_BYTES:
                f.seek(size - _FAILURE_SCAN_BYTES)
                f.readline()           # drop the partial line the seek landed in
            lines = f.read().splitlines()
    except OSError as e:
        log.debug("could not read mpv log: %s", e)
        return []
    return [ln for ln in lines if _MPV_ERROR_MARK in ln][-_FAILURE_LOG_LINES:]


def _write_failure_report(url: "str | None") -> None:
    """Cut the last failure out of mpv.log into last_failure.log.

    Overwritten every time, holding exactly ONE failure — the most recent, which
    is the only one anyone asks for. This is the file the panel names and a human
    opens: a few lines instead of the ~420 KB per track load mpv writes whether
    anything went wrong or not (its verbosity is not tunable — see "mpv log").

    Best-effort by design: it is a convenience over the ERROR line already in
    felhaven.log, so any failure here is logged at debug and swallowed rather
    than allowed to disturb a status() tick. Never raises.
    """
    errors = _mpv_error_lines()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    body = "\n".join(errors) if errors else (
        "(no error-level lines found in mpv.log — mpv may not have flushed yet, "
        "or was started without --log-file)")
    try:
        os.makedirs(_MPV_LOG_DIR, exist_ok=True)
        with open(_FAILURE_LOG_FILE, "w", encoding="utf-8") as f:
            f.write(f"Morpheus load failure — {stamp}\n"
                    f"url: {url}\n"
                    f"{'-' * 60}\n{body}\n")
    except OSError as e:
        log.debug("could not write failure report: %s", e)


def _judge_pending_load(props: dict[str, Any]) -> None:
    """Confirm or condemn an outstanding load from one property snapshot.

    Confirming matters as much as condemning: a track that plays to its natural
    end leaves mpv in the SAME idle state a failed load does, so an unconfirmed
    stamp left lying around would report every finished track as a failure.
    Kairos polls every 2 s and tracks run minutes, so a good load is always
    confirmed long before its EOF.

    Confirmation is time-pos/duration — MEDIA EXISTING — and deliberately not
    mpv's `path`. path is set the instant mpv accepts the URL, while yt-dlp is
    still resolving it and long before anyone knows whether it will work; a
    failing load therefore passes through a tick with path set and no media.
    Confirming on that silently disarmed the detector and let the exact outage
    this was written for slip through again (caught end-to-end against real mpv,
    2026-08-20 — tests/test_morpheus_load_failure.py pins it).

    `path` is in `props` since 2026-09-20 because status() publishes it for
    awen. That is not permission to read it HERE, and the paragraph above is
    the whole reason — it arrives in this function now, and it must go on being
    ignored by it.
    """
    global _pending_load, _load_error
    if _pending_load is None:
        return

    # Media actually exists — the load landed.
    if props["time-pos"] is not None or props["duration"] is not None:
        _pending_load = None
        return

    # mpv is back to idle with nothing loaded. Past the in-flight grace, that
    # is a failed load. The reason is in logs/mpv/mpv.log — never spliced into
    # the slug (§2: no raw external text; those lines carry signed URLs).
    if props["idle-active"] is True and (time.monotonic() - _pending_load) > _PENDING_GRACE:
        _pending_load = None
        _load_error = "load_failed"
        _write_failure_report(_last_url)
        log.error("load failed for %r — mpv went idle with nothing loaded; "
                  "see %s", _last_url, _FAILURE_LOG_FILE)


def fetch() -> dict[str, Any]:
    """Kairos entry point — just status(). Never raises (see module docstring)."""
    return status()


# ── Playlist config ───────────────────────────────────────────────────────────

def _save_playlists(data: dict[str, Any]) -> None:
    """Write the full playlists dict. Raises on failure so the caller can
    catch it. Temp-then-replace so a crash mid-write can't truncate it."""
    tmp = _PLAYLISTS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _PLAYLISTS_PATH)


def load_playlists() -> list[dict[str, Any]]:
    """
    Ordered playlist rows from morpheus_playlists.json (UI order follows file
    order). Returns [] on any failure (panel shows a hint row), logs ERROR.
    Called from MorpheusPanel._reload_playlists().
    """
    try:
        with open(_PLAYLISTS_PATH, "r", encoding="utf-8") as f:
            playlists: list[dict[str, Any]] = json.load(f).get("playlists", [])
            return playlists
    except FileNotFoundError:
        return []
    except Exception as e:
        log.error("failed to load playlists: %s", e)
        return []


def save_playlist(label: str, url: str) -> bool:
    """
    Append one entry to morpheus_playlists.json.
    Creates the file with an empty playlists list if it does not exist.
    Returns True on success, False on any write failure (logs ERROR).
    Called from MorpheusPanel._on_save_playlist() and _save_from_search().
    """
    try:
        try:
            with open(_PLAYLISTS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            data = {"playlists": []}

        data.setdefault("playlists", []).append({
            "label": label.strip(),
            "url":   url.strip(),
        })

        _save_playlists(data)

        log.info("playlist saved: %r", label)
        return True
    except Exception as e:
        log.error("save_playlist failed: %s", e)
        return False


def remove_playlist(index: int) -> bool:
    """
    Remove the entry at `index` (0-based, in load_playlists() / file order)
    from morpheus_playlists.json. Returns True on success, False on any failure
    or out-of-range index (logs ERROR) — the file is left untouched on failure.
    Removal is by position, not by content, so duplicate labels delete exactly
    the row the caller targeted. Called from MorpheusPanel._on_remove_playlist().
    """
    try:
        with open(_PLAYLISTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        playlists = data.get("playlists", [])
        if not 0 <= index < len(playlists):
            log.error("remove_playlist: index %d out of range (have %d)",
                      index, len(playlists))
            return False

        removed = playlists.pop(index)

        _save_playlists(data)

        log.info("playlist removed: %r", removed.get("label"))
        return True
    except FileNotFoundError:
        log.error("remove_playlist: %s not found", _PLAYLISTS_PATH)
        return False
    except Exception as e:
        log.error("remove_playlist failed: %s", e)
        return False


# ── LLM tool contract ─────────────────────────────────────────────────────────
# TOOL_DEFINITIONS (plural — the Callimachus precedent, CONVENTIONS §3/§8):
# two tightly-coupled calls on one job, each with a like-named function
# replacing the old singular handle(). play_music STARTS something new
# (searches the top YouTube hit and plays it); control_music governs what is
# already playing. The split is start-vs-govern, and both descriptions point at
# the other one, because "play the next song" is the ambiguity a small model
# will actually hit.
#
# The old resume_music tool is now control_music(action="replay") — it was
# named the same as a pause button's "resume" while meaning something else
# entirely (reload _last_url), which is a distinction the model had to make on
# every music turn and could only get wrong. CONVENTIONS §12 records why.

_CONTROL_ACTIONS = ("pause", "resume", "stop", "next", "previous",
                    "replay", "status", "volume")

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "play_music",
            "description": (
                "Search YouTube for a song, artist, or any audio and play the top "
                "result through the local audio engine (mpv). Call whenever the user "
                "asks to play, put on, or listen to music or a specific track. To "
                "change or inspect audio that is ALREADY playing — pause, skip, stop, "
                "or what is on right now — use control_music instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to play — a song title, artist, or search terms.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_music",
            "description": (
                "Control audio that is already playing, or report on it. Call when "
                "the user asks to pause, unpause, stop, skip, go back, put the last "
                "track back on, or asks what is playing right now. To start "
                "something new, use play_music instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": list(_CONTROL_ACTIONS),
                        # The per-action matrix lives here, not in the function
                        # description: that one is billed on every request and
                        # capped at 600 chars (herodotus.edit_document precedent).
                        "description": (
                            "pause and resume are idempotent — pausing twice leaves "
                            "it paused. stop ends playback and bookmarks the "
                            "position. next and previous walk the current playlist. "
                            "replay puts the most recently played track back on, "
                            "from where it left off — for after a stop, not for "
                            "skipping. status reports the title, paused state, "
                            "position and volume, and changes nothing. volume "
                            "sets the level and needs the level parameter."
                        ),
                    },
                    "level": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 100,
                        "description": (
                            "Loudness 0-100, for action=volume only; 100 is full. "
                            "For a relative change (\"a bit quieter\") read the "
                            "current level from action=status first, then send the "
                            "result."
                        ),
                    },
                },
                "required": ["action"],
            },
        },
    },
]


def play_music(query: str = "") -> dict[str, Any]:
    """Toolbox entry: search + play the top hit. BLOCKS for a few seconds on the
    yt-dlp search, so the caller must thread it — Pythia already runs tool calls
    on a worker thread. Never raises: a missing binary, empty query, or no
    result degrades to an error dict the model can relay."""
    query = (query or "").strip()
    if not query:
        return {"error": "no_query", "detail": "No song was specified."}

    bins = available()
    if not bins["ytdlp"]:
        return {"error": "player_unavailable", "detail": "yt-dlp binary not found."}
    if not bins["mpv"]:
        return {"error": "player_unavailable", "detail": "mpv binary not found."}

    results = search(query, limit=1)
    if not results or "error" in results[0] or not results[0].get("url"):
        return {"error": "no_results", "detail": f"Nothing found for {query!r}."}

    top = results[0]
    play(top["url"])
    return {
        "now_playing": top["title"],
        "channel":     top.get("channel", ""),
        "url":         top["url"],
    }


def _replay() -> "dict[str, Any] | None":
    """The replay action's guards and effect. Returns an error dict, or None
    once the load has been fired. Split out because it is the only action with
    preconditions of its own — everything else is one _ipc write."""
    if _last_url is None:
        return {"error": "nothing_to_resume", "detail": "Nothing has been played yet."}
    if not available()["mpv"]:
        return {"error": "player_unavailable", "detail": "mpv binary not found."}
    play(_last_url)
    return None


def _model_status() -> dict[str, Any]:
    """The snapshot MINUS what the model has no business holding.

    One key today: `path`. It is in the snapshot for awen's attribution check,
    which runs in THIS process and compares video ids — but control_music hands
    its result straight into the model's context, and a resolved YouTube URL is
    not a thing to put there. Measured 2026-09-20 off logs/mpv/last_failure.log:
    the stream URL behind one song was 1110 characters of signed, expiring,
    IP-bound query string. Even the watch?v= form is context the model cannot
    use — it names songs by title, and every tool that takes a URL is handed one
    by this module rather than by the model.

    A DENYLIST rather than an allowlist, deliberately. An allowlist would make
    every future snapshot field invisible to the model until somebody remembered
    to add it, which is the failure that is silent; a denylist makes the next
    sensitive field visible until somebody removes it, which is the failure that
    gets noticed. The fields here are few and the judgment is per-field.
    """
    out = status()
    out.pop("path", None)
    return out


def control_music(action: str = "status",
                  level: "float | None" = None) -> dict[str, Any]:
    """Toolbox entry: the transport state machine over whatever is playing.

    Returns {"action": ..., "status": {...}} — the post-action snapshot, so the
    caller sees the result without a second call. See "Control surface" in the
    module docstring for why pause/resume are idempotent, why the mutations are
    gated on a live mpv, and why the title can lag a tick after a load.

    `action` defaults to "status" — the harmless one — so a model that omits it
    reads state rather than mutating something. Never raises: an unknown action,
    a dead player, or nothing to replay all degrade to an error dict.
    """
    if action not in _CONTROL_ACTIONS:
        # Slug first, then detail (§2). The detail lists the legal values so a
        # model that guessed can correct itself without another round trip.
        return {"error": f"unknown_action: {action!r} is not one of "
                         f"{', '.join(_CONTROL_ACTIONS)}."}

    if action == "status":
        return {"action": action, "status": _model_status()}

    # Volume is the one action that works with no mpv running: the level is
    # module state, and _ensure_mpv applies it on the next spawn. Gating it on
    # a live pipe would refuse a perfectly meaningful "turn it down for later".
    if action == "volume":
        if level is None:
            return {"error": "no_level: volume needs a level from 0 to 100."}
        return {"action": action, "volume": set_volume(level),
                "status": _model_status()}

    # Every remaining action mutates. _ipc() is silent when the pipe is down
    # (see "Control surface"), so refuse rather than report a success nothing
    # heard. replay is exempt — it starts mpv itself, via play() — and skips
    # this read entirely rather than paying for a snapshot it would discard.
    if action != "replay" and not status().get("running"):
        return {"error": "not_playing: nothing is playing right now."}

    if action == "replay":
        failure = _replay()
        if failure is not None:
            return failure
    elif action == "pause":
        set_paused(True)
    elif action == "resume":
        set_paused(False)
    elif action == "stop":
        stop()
    elif action == "next":
        next_track()
    else:                       # "previous" — the enum check above admits no other
        prev_track()

    return {"action": action, "status": _model_status()}


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("[morpheus] available():", available())
    # Safe to run cold: with no mpv up this reads {"running": False} without
    # touching the pipe, and mutating actions refuse rather than no-op.
    print("[morpheus] control_music('status'):", control_music("status"))
    print("[morpheus] searching 'lofi' ...")
    for r in search("lofi", limit=5):
        if "error" in r:
            print(f"  ! {r['error']}")
        else:
            mins = "" if r["duration"] is None else f"{int(r['duration'])//60}:{int(r['duration'])%60:02d}"
            print(f"  • {r['title']}  [{r['channel']} {mins}]")
