"""
orpheus.py — Orpheus, Player of the Recorded Voice
===================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Play back one audio file from local_audio/ — the folder Echo
             writes to. Play and stop, nothing else: no pause, no seek, no
             position, no playlists, no folder picker.

Contract:    fetch() -> {"playing": bool, "files": [{"name", "duration"}, ...]}
                 Kairos-polled, 2 s. READ-ONLY, NEVER RAISES — "not playing"
                 is a normal state, not a fault (the morpheus.fetch()
                 precedent). "playing" is harmonia.is_playing() — the
                 completion signal the panel's transport reads to flip back
                 to idle when a briefing finishes on its own. "files" is the
                 current local_audio/ listing, refreshed on every tick — no
                 separate watcher, no manual refresh button — each row
                 carrying its "duration" in seconds (float, or None if it
                 couldn't be read). Durations are cached by name, size and
                 modification time (see "Duration probing" below) so a
                 Kairos tick doesn't re-probe every file every 2 s.
                 Missing ffmpeg adds "error": "ffmpeg_unavailable" — the same
                 slug play_file() and Echo use for that condition (§2's one
                 vocabulary) — and still lists the files, each duration None,
                 so ORPHEUS never says "no files" over files it can't play.
             play_file(name) -> dict
                 Decode `name` (must already be a file in local_audio/) whole
                 into RAM via ffmpeg and hand it to harmonia.play() at
                 24 kHz mono. Mutation — fires only from deliberate UI action
                 in OrpheusPanel, which calls it on its own decode thread
                 (a decode takes seconds; see "Limit"); never polled, never
                 LLM-facing (no handle(), no TOOL_DEFINITION — v1 is
                 panel-only, the Echo precedent). Never raises: a bad name,
                 a missing ffmpeg, or a decode failure all degrade to a
                 logged no-op and a stable {"error": ...} dict.
             stop() -> None
                 harmonia.stop(). Mutation, UI-driven only. Never raises.
             delete_file(name) -> dict
                 Send `name` (must already be a file in local_audio/) to the
                 Recycle Bin. Mutation — fires only from the two-click confirm
                 glyph in OrpheusPanel; never polled, never LLM-facing. Never
                 raises: a bad name or a failed shell operation degrades to a
                 logged no-op and a stable {"error": ...} dict.
                 Recycle Bin, not os.remove: unlike Morpheus's remove (which
                 drops a JSON row you can re-add by pasting the URL back), this
                 destroys an audio file Echo spent real synthesis time on. The
                 two-click confirm is the primary guard; the undo is the
                 backstop for the misfire that gets past it. Costs no new
                 dependency — SHFileOperationW via stdlib ctypes.
             available() -> {"ffmpeg": path_or_None, "problem": slug_or_None}
                 Mirrors morpheus.available() / echo.available(); "problem"
                 is aegis's slug when bin/ffmpeg.exe was refused. The panel
                 reads this once at build time to show a placeholder.

Limit:       Whole-file decode into RAM. ~17 MB for a 3-minute mono 24 kHz
             float32 briefing. Echo has made longer: measured 2026-11-21 on
             the owner's files, a 25-minute one decodes to 142 MB in 3.2 s,
             a 9-minute one to 50 MB in 1.1 s — which is why OrpheusPanel
             decodes off the UI thread. A multi-hour file would not fit;
             that bound is a stated, deliberate limit, not a bug. Streaming
             would need chunked reads off ffmpeg's stdout — a different
             module, not this one.

             _SAMPLE_RATE/_CHANNELS below are 24000/1, not a generic
             "decode anything" default — they match exactly what Echo's
             pipeline produces (kokoro synthesizes mono @ 24 kHz;
             calliope.save_wav()/echo's ffmpeg encode never resample or
             remix, so every .opus in local_audio/ is mono 24 kHz at
             origin). Decoding at a higher rate/channel count than the
             source has doesn't restore anything — it just upsamples and
             duplicates a channel for zero new information, at up to 4x the
             RAM for nothing (measured: a real 6m41s file was 147 MB
             decoded at 48 kHz stereo vs. 37 MB at its native 24 kHz mono).
             This is a second, deliberate limit alongside the RAM bound: it
             assumes local_audio/ only ever holds Echo's own output. The day
             a real music file (or anything not from Echo) lands there,
             forcing 24 kHz mono would audibly degrade it — that's a stated
             assumption to revisit then, not a guard to add now.

Duration     No ffprobe — it isn't bundled alongside ffmpeg in bin/, and
probing:     pulling in a second binary for one number is a bad trade.
             Instead `ffmpeg -i <path>` with no output is read for its own
             "Duration: HH:MM:SS.ss" banner line on stderr — the standard
             ffprobe-free trick, and it never decodes a sample. That call
             always exits nonzero ("at least one output file must be
             specified"), which is expected and ignored; only a missing
             Duration line is treated as failure (-> None, logged, retried
             next tick). A result is cached by (name, size, mtime_ns), and
             each tick keeps only the files still listed, so an unchanged
             file is probed once, not on every 2 s Kairos tick. Keyed by
             name alone (until 2026-11-21) it went wrong: Echo's ffmpeg
             writes the file in place, and a probe mid-write succeeds with
             a partial duration (46 s at 0.8 s into a 16 s encode of a
             24:37 file, measured), which then stuck until a restart. Now a
             file that grows is probed again, and so is one replaced
             outside Felhaven.

ffmpeg       Every path handed to ffmpeg must be absolute, or nothing runs
paths:       (_probe_duration and _decode refuse it). ffmpeg reads an
             argument starting with "-" as an option and one with "name:"
             before its first slash as a protocol (concat:, http:); an
             absolute path can be neither, since ffmpeg reads a leading drive
             letter as a plain file. Paths here are local_audio/ (anchored to
             this file) plus a bare name, so absolute by construction; the
             check makes the day that changes loud (CONVENTIONS §12,
             2026-10-03).

Source:      One external binary, zero new pip packages — ffmpeg, already a
             prerequisite for Echo. tools/aegis.py resolves it, as for
             Morpheus and Echo: bin/ffmpeg.exe when it matches its manifest
             entry, else ffmpeg.exe on PATH; a bin/ copy that doesn't match
             is refused, never swapped for PATH's.
             Import-safety is load-bearing: this module is a Kairos worker
             (Kairos resolves every WORKERS entry via importlib at startup),
             so nothing at import time may raise or probe for ffmpeg — a
             missing binary surfaces only through fetch()'s "error" key and
             available(), never an import-time crash that would take the
             whole app down, not just Orpheus.

Scope:       local_audio/ only (Echo's output folder) — not a general folder
             picker. "Play what Echo produced, and throw away what you no
             longer want," the whole brief. No rename, no move, no subfolders.

Out of LLM   No handle(), no TOOL_DEFINITION for v1 — the same out-of-LLM-scope
scope:       decision as Echo. The module *shape* differs, though: Orpheus is
             the §2 "Polled status + UI-driven mutations" hybrid (it HAS a
             Kairos fetch; Echo has none), not Echo's no-fetch "Local-only /
             plain functions" flavor. A future play_briefing brain tool is left
             open by the shape, not built.

Threads:     fetch() runs on the Kairos worker and is the only code that
             touches _duration_cache (§6). play_file() runs on OrpheusPanel's
             decode thread and keeps no module state; Harmonia locks its own.
             stop() and delete_file() run on the Tk thread and touch none.

Upstream:    kairos.py (calls fetch), panels/orpheus_panel.py (play_file on
             its decode thread, stop and delete_file on a click).
Downstream:  ffmpeg (probe, decode), vouched for by tools/aegis.py ->
             harmonia.py (playback ownership); shell32's SHFileOperationW
             (the Recycle Bin).

Requires:    harmonia, tools.aegis; numpy; the ffmpeg binary (bin/ or PATH).
             Stdlib: ctypes, logging, os, re, subprocess, sys, typing.

2026-07-25:  walkthrough audit — never-raise verified across fetch/play_file/
             stop, error slugs already conformant (never in the output
             allowlist); fetch()'s "no_ffmpeg" renamed to "ffmpeg_unavailable"
             to match play_file()/Echo (§2 one vocabulary); the out-of-scope
             note corrected to the hybrid flavor, not Echo's plain-functions
             shape.

V0.03:       Walked through 2026-11-21 (the V0.03 walkthrough, CONVENTIONS
             §3/§12). Built 2026-07-17; the Recycle Bin remove came
             2026-08-06. Checked: the probe and decode against the owner's 14
             files (probe ~40 ms each); fetch() never raises; the Recycle Bin
             call's double-NUL list. Changed: the duration cache keys on
             name, size and mtime and keeps only listed files, because a
             probe mid-write cached a partial duration for good (measured);
             delete_file() no longer touches the cache, so only the Kairos
             worker does (§6); fetch() without ffmpeg still lists the files;
             play_file() runs on the panel's decode thread (the owner's
             pick: a 25-minute file froze Felhaven 3.2 s); Limit carries
             the measured sizes; the Contract names available()'s
             "problem"; Source names aegis; Requires names every import.
"""

import ctypes
import logging
import os
import re
import subprocess
import sys
from typing import Any, Optional

import numpy as np

# Orpheus reaches UP to an app-root sibling (harmonia.py) for the shared
# device authority — the guarded-path pattern echo.py uses so a bare
# `python tools/orpheus.py` standalone run still resolves it (tests and
# felhaven already run with the app root on sys.path). Must precede the import.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harmonia
from tools import aegis

log = logging.getLogger("METIS.orpheus")

# ── Paths (anchored to __file__, cwd-independent — morpheus precedent) ─────────
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOCAL_AUDIO_DIR = os.path.join(_APP_ROOT, "local_audio")

# Hide the console window ffmpeg would flash on Windows. getattr so importing
# on a non-Windows box can't AttributeError (morpheus precedent).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# The rate/channel count ffmpeg is asked to decode to — matched to what
# Echo's pipeline actually produces (kokoro @ 24 kHz mono), NOT a generic
# default (see the module "Limit" section: decoding higher than the source
# just upsamples/duplicates for zero new information, at real RAM cost).
# Passed to harmonia.play() explicitly every call (D3) — Harmonia itself
# assumes no rate.
_SAMPLE_RATE = 24000
_CHANNELS = 1

_DECODE_TIMEOUT = 120.0   # s — generous for a multi-minute briefing

# Duration probing (see module "Duration probing" section — no ffprobe dep).
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_PROBE_TIMEOUT = 10.0     # s — reading metadata only, never a full decode
# (name, size, mtime_ns) -> seconds. The Kairos worker's alone (module "Threads").
_duration_cache: dict[tuple[str, int, int], float] = {}


# ── Binary resolution (tools/aegis.py: a verified bin/ copy, else PATH) ───────

def _resolve(name: str) -> Optional[str]:
    """The program to run, vouched for by tools/aegis.py: bin/<name>.exe if it
    matches its manifest entry, else a copy on PATH (unverified, warned once).
    A bin/ copy that doesn't match is refused (None), never swapped for PATH's."""
    return aegis.resolve(name).path


def available() -> dict[str, Optional[str]]:
    """Report whether ffmpeg is resolvable. {"ffmpeg": path_or_None,
    "problem": None, or aegis's slug when bin/ffmpeg.exe was refused}."""
    return {"ffmpeg": _resolve("ffmpeg"), "problem": aegis.resolve("ffmpeg").problem}


# ── Recycle Bin (SHFileOperationW — stdlib ctypes, no new dependency) ──────────
#
# Guarded by sys.platform, NOT imported bare: `from ctypes import wintypes`
# raises on non-Windows, and this module is a Kairos worker resolved via
# importlib at startup — an import-time raise takes the whole dashboard down,
# not just Orpheus (see "Import-safety is load-bearing" above). Same
# defensiveness as _NO_WINDOW's getattr below.

_FO_DELETE          = 0x0003   # wFunc: delete the files named in pFrom
_FOF_SILENT         = 0x0004   # no progress dialog
_FOF_NOCONFIRMATION = 0x0010   # no "are you sure" — the glyph already asked
_FOF_ALLOWUNDO      = 0x0040   # THE point of this call: Recycle Bin, not erase
_FOF_NOERRORUI      = 0x0400   # report failure through the return code only

if sys.platform == "win32":
    from ctypes import wintypes

    class _SHFILEOPSTRUCTW(ctypes.Structure):
        """shell32's SHFILEOPSTRUCTW. Only wFunc/pFrom/fFlags are set; the
        rest must still be present and zeroed for the layout to match."""

        _fields_ = [
            ("hwnd",                  wintypes.HWND),
            ("wFunc",                 wintypes.UINT),
            ("pFrom",                 wintypes.LPCWSTR),
            ("pTo",                   wintypes.LPCWSTR),
            ("fFlags",                ctypes.c_uint16),   # FILEOP_FLAGS = WORD
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings",         wintypes.LPVOID),
            ("lpszProgressTitle",     wintypes.LPCWSTR),
        ]


def _send_to_recycle_bin(path: str) -> bool:
    """Move `path` to the Recycle Bin. True on success, False on any failure
    (logged, never raised).

    Non-Windows falls back to os.remove — a permanent delete with no undo.
    Felhaven is a Windows app; that branch exists so the module stays
    importable and testable elsewhere, not as a supported configuration."""
    if sys.platform != "win32":
        try:
            os.remove(path)
            return True
        except OSError as e:
            log.error("Orpheus: delete failed for %r: %s", path, e)
            return False

    op = _SHFILEOPSTRUCTW()
    op.wFunc = _FO_DELETE
    # Double-NUL terminated: pFrom is a LIST of paths, so shell32 keeps reading
    # past a single terminator until it finds an empty one. One NUL here would
    # hand it whatever follows in memory.
    op.pFrom = os.path.abspath(path) + "\0\0"
    op.fFlags = (_FOF_ALLOWUNDO | _FOF_NOCONFIRMATION
                 | _FOF_NOERRORUI | _FOF_SILENT)
    try:
        res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    except OSError as e:
        log.error("Orpheus: SHFileOperationW failed to run for %r: %s", path, e)
        return False
    if int(res) != 0:
        log.error("Orpheus: SHFileOperationW returned %s for %r", res, path)
        return False
    return True


# ── local_audio/ listing ───────────────────────────────────────────────────────

def _safe_path(name: str) -> Optional[str]:
    """Resolve `name` to an absolute path strictly inside local_audio/, or
    None if it isn't a bare filename or doesn't exist there. Defensive even
    though v1 is panel-only, not LLM-facing — never trust a string argument
    (the echo.sanitize_filename precedent, applied to reading instead of
    writing)."""
    base = os.path.basename(name)
    if not base or base != name:
        return None
    path = os.path.join(_LOCAL_AUDIO_DIR, base)
    return path if os.path.isfile(path) else None


def list_files() -> list[str]:
    """Bare filenames in local_audio/, sorted. [] if the folder doesn't exist
    yet or on any read error — never raises."""
    try:
        return sorted(
            f for f in os.listdir(_LOCAL_AUDIO_DIR)
            if os.path.isfile(os.path.join(_LOCAL_AUDIO_DIR, f))
        )
    except OSError:
        return []


def _probe_duration(ffmpeg: str, path: str) -> Optional[float]:
    """Read `path`'s duration (seconds) from ffmpeg's own metadata banner —
    no ffprobe dependency, no decode (see module "Duration probing" section).
    None on any failure (unreadable file, no Duration line, timeout, a path
    that isn't absolute — see "ffmpeg paths") — logged, never raised."""
    if not os.path.isabs(path):
        log.warning("Orpheus: refused to hand ffmpeg a path that isn't absolute: %r", path)
        return None
    try:
        proc = subprocess.run([ffmpeg, "-hide_banner", "-i", path],
                              capture_output=True, timeout=_PROBE_TIMEOUT,
                              creationflags=_NO_WINDOW)
    except (subprocess.TimeoutExpired, OSError) as e:
        log.warning("Orpheus: duration probe failed for %r: %s", path, e)
        return None
    match = _DURATION_RE.search(proc.stderr.decode("utf-8", "replace"))
    if not match:
        log.warning("Orpheus: no Duration line for %r", path)
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _file_rows() -> list[dict[str, Any]]:
    """local_audio/ listing, each row {"name", "duration"}. A duration is
    cached by (name, size, mtime_ns), so a file is probed again whenever it
    changes, and the cache keeps only the files listed this time (see module
    "Duration probing"). A probe failure stays uncached and is retried next
    tick. A file gone between the listing and its stat is left out. No
    ffmpeg -> nothing is probed; a row shows a duration only if one was
    cached before."""
    ffmpeg = _resolve("ffmpeg")
    rows: list[dict[str, Any]] = []
    kept: dict[tuple[str, int, int], float] = {}
    for name in list_files():
        path = os.path.join(_LOCAL_AUDIO_DIR, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        key = (name, st.st_size, st.st_mtime_ns)
        duration = _duration_cache.get(key)
        if duration is None and ffmpeg:
            duration = _probe_duration(ffmpeg, path)
        if duration is not None:
            kept[key] = duration
        rows.append({"name": name, "duration": duration})
    _duration_cache.clear()
    _duration_cache.update(kept)
    return rows


# ── Decode (whole-file, RAM-bound — see the module "Limit" section) ────────────

def _decode(ffmpeg: str, path: str) -> Optional[np.ndarray]:
    """Whole-file decode via ffmpeg to interleaved float32 PCM, reshaped to
    (frames, channels) for sounddevice. None on any failure (bad file, ffmpeg
    crash, timeout, a path that isn't absolute — see "ffmpeg paths") — logged,
    never raised."""
    if not os.path.isabs(path):
        log.error("Orpheus: refused to hand ffmpeg a path that isn't absolute: %r", path)
        return None
    cmd = [ffmpeg, "-loglevel", "error", "-i", path, "-f", "f32le",
           "-ar", str(_SAMPLE_RATE), "-ac", str(_CHANNELS), "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=_DECODE_TIMEOUT,
                              creationflags=_NO_WINDOW)
    except (subprocess.TimeoutExpired, OSError) as e:
        log.error("Orpheus: ffmpeg decode failed to run: %s", e)
        return None
    if proc.returncode != 0:
        log.error("Orpheus: ffmpeg decode failed (rc=%s): %s", proc.returncode,
                  proc.stderr.decode("utf-8", "replace").strip())
        return None
    pcm = np.frombuffer(proc.stdout, dtype="<f4")
    if pcm.size == 0:
        log.error("Orpheus: ffmpeg produced no audio for %r", path)
        return None
    return pcm.reshape(-1, _CHANNELS)


# ── Public API — mutations (UI-driven only) ───────────────────────────────────

def play_file(name: str) -> dict[str, Any]:
    """Decode `name` (a file already in local_audio/) and hand it to
    harmonia.play() at 24 kHz mono — Echo's native format, not a generic
    rate (see the module "Limit" section). Blocks for the whole decode, so
    OrpheusPanel calls it on its own decode thread. Never raises.
    Error codes: bad_name, ffmpeg_unavailable, decode_failed."""
    path = _safe_path(name)
    if path is None:
        return {"error": "bad_name"}
    ffmpeg = _resolve("ffmpeg")
    if not ffmpeg:
        return {"error": "ffmpeg_unavailable"}
    pcm = _decode(ffmpeg, path)
    if pcm is None:
        return {"error": "decode_failed"}
    harmonia.play(pcm, _SAMPLE_RATE, tag="orpheus")
    return {"playing": os.path.basename(path)}


def stop() -> None:
    """Stop whatever Harmonia is playing. UI-driven only. Never raises."""
    harmonia.stop()


def delete_file(name: str) -> dict[str, Any]:
    """Send `name` (a file already in local_audio/) to the Recycle Bin — see
    the module "Contract" section for why the bin and not os.remove. Fires
    only from OrpheusPanel's two-click confirm glyph. Never raises.
    Error codes: bad_name, delete_failed.

    Deleting a file that is currently playing is safe and deliberately not
    blocked: play_file() decodes the whole file into RAM before handing it to
    Harmonia, so nothing holds an open handle on disk."""
    path = _safe_path(name)
    if path is None:
        log.warning("Orpheus: refusing to delete %r — not a file in local_audio/", name)
        return {"error": "bad_name"}
    if not _send_to_recycle_bin(path):
        return {"error": "delete_failed"}
    # The duration cache is left to fetch(): a later file under this name has
    # its own size and mtime, and the next tick drops this one's entry.
    log.info("Orpheus: recycled %r", name)
    return {"deleted": name}


# ── Kairos entry point ─────────────────────────────────────────────────────────

def fetch() -> dict[str, Any]:
    """Kairos-polled, 2 s. READ-ONLY, NEVER RAISES: idle is a normal state,
    not a fault (the morpheus.fetch() precedent). Also returns the current
    local_audio/ listing (each row carrying its duration) so the panel's file
    list refreshes itself with no separate watcher. Without ffmpeg the
    listing still comes, beside "error": "ffmpeg_unavailable": until
    2026-11-21 it came back empty, and a tab built while ffmpeg was there
    then said "no files — use Echo to make one" over files it couldn't
    play."""
    data: dict[str, Any] = {"playing": harmonia.is_playing(), "files": _file_rows()}
    if not _resolve("ffmpeg"):
        data["error"] = "ffmpeg_unavailable"
    return data


# ── Standalone demo ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import time

    logging.basicConfig(level=logging.INFO)
    print("[Orpheus] ffmpeg:", available()["ffmpeg"])
    for row in _file_rows():
        dur = "?" if row["duration"] is None else f"{row['duration']:.1f}s"
        print(f"[Orpheus]   {row['name']}  ({dur})")
    if len(sys.argv) > 1:
        name = sys.argv[1]
        print(f"[Orpheus] playing {name!r}...")
        print("[Orpheus] result:", play_file(name))
        while harmonia.is_playing():
            time.sleep(0.2)
        print("[Orpheus] done.")
    harmonia.shutdown()
