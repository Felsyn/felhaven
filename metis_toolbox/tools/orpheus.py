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
                 couldn't be read). Durations are cached by filename (see
                 "Duration probing" below) so a Kairos tick doesn't re-probe
                 every file every 2 s.
                 Missing ffmpeg degrades to
                 {"playing": False, "files": [], "error": "no_ffmpeg"}.
             play_file(name) -> dict
                 Decode `name` (must already be a file in local_audio/) whole
                 into RAM via ffmpeg and hand it to harmonia.play() at
                 48 kHz. Mutation — fires only from deliberate UI action in
                 OrpheusPanel; never polled, never LLM-facing (no handle(),
                 no TOOL_DEFINITION — v1 is panel-only, the Echo precedent).
                 Never raises: a bad name, a missing ffmpeg, or a decode
                 failure all degrade to a logged no-op and a stable
                 {"error": ...} dict.
             stop() -> None
                 harmonia.stop(). Mutation, UI-driven only. Never raises.
             available() -> {"ffmpeg": path_or_None}
                 Mirrors morpheus.available() / echo.available(). The panel
                 reads this once at build time to show a placeholder.

Limit:       Whole-file decode into RAM. ~70 MB for a 3-minute stereo 48 kHz
             float32 briefing — fine for what Echo produces. A multi-hour
             file would not be; that bound is a stated, deliberate limit, not
             a bug. Streaming would need chunked reads off ffmpeg's stdout —
             a different module, not this one.

Duration     No ffprobe — it isn't bundled alongside ffmpeg in bin/, and
probing:     pulling in a second binary for one number is a bad trade.
             Instead `ffmpeg -i <path>` with no output is read for its own
             "Duration: HH:MM:SS.ss" banner line on stderr — the standard
             ffprobe-free trick, and it never decodes a sample. That call
             always exits nonzero ("at least one output file must be
             specified"), which is expected and ignored; only a missing
             Duration line is treated as failure (-> None, logged). Results
             are cached by filename for the life of the process — local_audio/
             files are write-once (Echo never overwrites in place), so a file
             is probed exactly once, not on every 2 s Kairos tick.

Source:      One external binary, zero new pip packages — ffmpeg, already a
             prerequisite for Echo. Resolution follows the Morpheus/Echo
             precedent: <app root>/bin/ffmpeg.exe wins over PATH.
             Import-safety is load-bearing: this module is a Kairos worker
             (Kairos resolves every WORKERS entry via importlib at startup),
             so nothing at import time may raise or probe for ffmpeg — a
             missing binary surfaces only through fetch()'s "error" key and
             available(), never an import-time crash that would take the
             whole app down, not just Orpheus.

Scope:       local_audio/ only (Echo's output folder) — not a general folder
             picker. "Play what Echo produced," the whole brief.

Out of LLM   No handle(), no TOOL_DEFINITION for v1 (same "Local-only / plain
scope:       functions" shape as Echo — CONVENTIONS §2). A future
             play_briefing brain tool is left open by the module shape, not
             built.

Upstream:    kairos.py (calls fetch), panels/orpheus_panel.py (UI mutations)
Downstream:  ffmpeg (decode) -> harmonia.py (playback ownership)

Requires:    ffmpeg binary (bin/ or PATH). numpy. Stdlib otherwise.
"""

import logging
import os
import re
import shutil
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

log = logging.getLogger("METIS.orpheus")

# ── Paths (anchored to __file__, cwd-independent — morpheus precedent) ─────────
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BIN_DIR = os.path.join(_APP_ROOT, "bin")
_LOCAL_AUDIO_DIR = os.path.join(_APP_ROOT, "local_audio")

# Hide the console window ffmpeg would flash on Windows. getattr so importing
# on a non-Windows box can't AttributeError (morpheus precedent).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# The rate ffmpeg is asked to decode to. Passed to harmonia.play() explicitly
# every call (D3) — Harmonia itself assumes no rate.
_SAMPLE_RATE = 48000
_CHANNELS = 2

_DECODE_TIMEOUT = 120.0   # s — generous for a multi-minute briefing

# Duration probing (see module "Duration probing" section — no ffprobe dep).
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_PROBE_TIMEOUT = 10.0     # s — reading metadata only, never a full decode
_duration_cache: dict[str, float] = {}   # filename -> seconds; write-once files


# ── Binary resolution (morpheus/echo precedent: bin/ wins over PATH) ───────────

def _resolve(name: str) -> Optional[str]:
    """bin/<name>.exe wins over PATH so the portable copy beats a stale install."""
    local = os.path.join(_BIN_DIR, f"{name}.exe")
    if os.path.isfile(local):
        return local
    return shutil.which(name)


def available() -> dict[str, Optional[str]]:
    """Report whether ffmpeg is resolvable. {"ffmpeg": path_or_None}."""
    return {"ffmpeg": _resolve("ffmpeg")}


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
    None on any failure (unreadable file, no Duration line, timeout) —
    logged, never raised."""
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
    """local_audio/ listing, each row {"name", "duration"} — duration is
    probed once per filename and cached (see module docstring); a probe
    failure leaves it uncached so a transient issue (e.g. Echo still writing
    the file) can succeed on a later tick instead of sticking as None
    forever. No ffmpeg -> every row's duration is None."""
    ffmpeg = _resolve("ffmpeg")
    rows: list[dict[str, Any]] = []
    for name in list_files():
        duration = _duration_cache.get(name)
        if duration is None and ffmpeg:
            duration = _probe_duration(ffmpeg, os.path.join(_LOCAL_AUDIO_DIR, name))
            if duration is not None:
                _duration_cache[name] = duration
        rows.append({"name": name, "duration": duration})
    return rows


# ── Decode (whole-file, RAM-bound — see the module "Limit" section) ────────────

def _decode(ffmpeg: str, path: str) -> Optional[np.ndarray]:
    """Whole-file decode via ffmpeg to interleaved float32 PCM, reshaped to
    (frames, channels) for sounddevice. None on any failure (bad file, ffmpeg
    crash, timeout) — logged, never raised."""
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
    harmonia.play() at 48 kHz. Never raises.
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


# ── Kairos entry point ─────────────────────────────────────────────────────────

def fetch() -> dict[str, Any]:
    """Kairos-polled, 2 s. READ-ONLY, NEVER RAISES: idle is a normal state,
    not a fault (the morpheus.fetch() precedent). Also returns the current
    local_audio/ listing (each row carrying its duration) so the panel's file
    list refreshes itself with no separate watcher."""
    if not available()["ffmpeg"]:
        return {"playing": False, "files": [], "error": "no_ffmpeg"}
    return {"playing": harmonia.is_playing(), "files": _file_rows()}


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
