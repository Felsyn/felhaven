"""
echo.py — Echo, the Scribe of Voices
====================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Turn text (typically Markdown) into an audio FILE on disk. Nothing
             else. Echo does not PLAY audio (that is Calliope), does not control
             an audio engine (that is Morpheus), and does not decide WHAT to
             narrate — it is handed text + a filename and writes one .opus file.

Contract:    text_to_audio(text, filename) -> dict
                 Strip the Markdown to readable prose, chunk it, synthesize each
                 chunk via Calliope's shared model, concatenate the PCM, and
                 encode it to an Opus file in local_audio/. Returns
                 {"path": "<abs .opus path>"} on success, or {"error": "<code>"}
                 on any failure. NEVER raises — a missing model, a missing or
                 mis-built ffmpeg, empty text, or a bad filename all degrade to an
                 {"error": ...} dict, never an exception and never a partial file.
             available() -> {"ffmpeg": <path or None>}
                 Report whether the one external binary is resolvable, mirroring
                 morpheus.available(). The panel reads this to disable its UI.

Source:      One external binary, zero new pip packages.
               - ffmpeg : encodes the assembled WAV to .opus (libopus). Resolved
                          bin/ffmpeg.exe first (flash-drive-portable), then PATH
                          — the Morpheus precedent. Absent, or present but built
                          without libopus, both degrade to a clean {"error": ...}.
             TTS itself is NOT owned here: Echo calls calliope.synthesize() and
             reuses calliope.take_chunk() / save_wav() / SAMPLE_RATE, so there is
             exactly one loaded kokoro model and one WAV writer in the process
             (see calliope.py "Shared seam").

Markdown:    Echo eats real .md, so its stripper is deliberately MORE thorough
             than Calliope's defensive character strip:
               - DROPS fenced code blocks (``` / ~~~) entirely — reading code
                 aloud is unlistenable and prose is the job. INDENTED code is
                 left AS prose: detecting it without a real Markdown parser risks
                 eating legitimately-indented text, so it is a deliberate,
                 documented limit (flagged for review).
               - DROPS images ![alt](url) entirely.
               - UNWRAPS links [text](url) -> text.
               - STRIPS heading marks, blockquote marks, list bullets, table
                 pipes, horizontal rules, and emphasis / inline-code characters
                 (keeping the inline-code TEXT, minus its backticks).

Contention:  Echo shares Calliope's single kokoro model and its _synth_lock, so a
             long conversion (a whole document) COMPETES with live narration for
             that lock — while Echo renders, spoken answers can stall. This is
             accepted on purpose (a second model instance would double VRAM and
             drift from Calliope's tuning); it is NOT a bug. Run big conversions
             when you are not relying on live narration.

Flavor:      Request-driven and out-of-LLM-scope for v1: no fetch() (not polled)
             and no handle()/TOOL_DEFINITION (a tool call cannot reach it yet).
             It is the "Local-only / plain functions" shape (CONVENTIONS §2) — a
             future narrate_to_file brain tool is left open by this shape, not
             built.

Upstream:    panels/echo_panel.py (the paste-and-convert UI); Eos later (an
             unattended caller).
Downstream:  calliope.synthesize() (TTS) -> ffmpeg (Opus encode).

Requires:    ffmpeg binary (bin/ or PATH). numpy (via calliope). Stdlib otherwise.
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np

# Echo reaches UP to an app-root sibling (calliope.py) for the shared TTS model —
# the guarded-path pattern aura.py / callimachus.py use so a bare
# `python tools/echo.py` standalone run still resolves it (tests and felhaven
# already run with the app root on sys.path). Must precede the import.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import calliope

log = logging.getLogger("METIS.echo")

# ── Paths (anchored to __file__, cwd-independent — morpheus precedent) ─────────
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BIN_DIR = os.path.join(_APP_ROOT, "bin")
_LOCAL_AUDIO_DIR = os.path.join(_APP_ROOT, "local_audio")

# Hide the console window ffmpeg would flash on Windows. getattr so importing on
# a non-Windows box can't AttributeError (morpheus precedent).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Per-chunk char cap for a single kokoro create() call. Calliope's proven live
# cap is 240; Echo has no playback-latency budget, so it uses a larger cap to
# amortize the per-call overhead across a whole document. NOTE: no empirical
# single-call ceiling for kokoro-onnx has been measured on this project — 400 is
# a conservative guess above the proven 240; retune if long chunks fail.
_CHUNK_MAX_CHARS = 400

# Opus bitrate for speech — 48 kbps is transparent for a single mono voice.
_OPUS_BITRATE = "48k"


# ── Filename sanitisation ─────────────────────────────────────────────────────
# Never trust the filename field: strip path separators + Windows-illegal chars,
# reject reserved device names, and force a single .opus extension. Without this
# a stray "/" or ":" could write outside local_audio/.
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL",
             *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def sanitize_filename(name: str) -> str:
    """Return a safe bare '<stem>.opus' filename, or '' if nothing usable
    survives (the caller turns '' into an empty_filename error). Strips illegal
    chars and leading/trailing dots+spaces, rejects reserved device names, and
    appends .opus unless it is already present."""
    cleaned = _ILLEGAL.sub("", name).strip().strip(".").strip()
    if not cleaned:
        return ""
    stem = cleaned.split(".")[0]
    if not stem or stem.upper() in _RESERVED:
        return ""
    if not cleaned.lower().endswith(".opus"):
        cleaned += ".opus"
    return cleaned


# ── Markdown stripping (more thorough than Calliope's char strip) ─────────────
_FENCED_CODE = re.compile(r"(?ms)^[ \t]*(```|~~~).*?^[ \t]*\1[ \t]*$")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HRULE = re.compile(r"(?m)^[ \t]*(?:[-*_])(?:[ \t]*[-*_]){2,}[ \t]*$")
_HEADING = re.compile(r"(?m)^[ \t]*#{1,6}[ \t]*")
_BLOCKQUOTE = re.compile(r"(?m)^[ \t]*>+[ \t]?")
_LIST_MARK = re.compile(r"(?m)^[ \t]*(?:[-*+]|\d+\.)[ \t]+")
_EMPHASIS = re.compile(r"[*_`~#|]+")
_WS = re.compile(r"\s+")


def _strip_markdown(text: str) -> str:
    """Reduce Markdown to plain, speakable prose. See the module 'Markdown'
    section for exactly what is dropped vs. unwrapped."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _FENCED_CODE.sub(" ", text)     # drop code fences entirely
    text = _IMAGE.sub("", text)            # drop images
    text = _LINK.sub(r"\1", text)          # [text](url) -> text
    text = _HRULE.sub(" ", text)           # --- *** ___ rules
    text = _HEADING.sub("", text)          # leading #s
    text = _BLOCKQUOTE.sub("", text)       # leading >
    text = _LIST_MARK.sub("", text)        # bullets / numbered markers
    text = _EMPHASIS.sub("", text)         # * _ ` ~ # | (keeps inline-code TEXT)
    return _WS.sub(" ", text).strip()      # collapse whitespace


# ── Binary resolution (morpheus precedent: bin/ wins over PATH) ────────────────
def _resolve(name: str) -> Optional[str]:
    """bin/<name>.exe wins over PATH so the portable copy beats a stale install."""
    local = os.path.join(_BIN_DIR, f"{name}.exe")
    if os.path.isfile(local):
        return local
    return shutil.which(name)


def available() -> dict[str, Optional[str]]:
    """Report whether ffmpeg is resolvable. The panel reads this once at build
    time; None means the convert UI is disabled. {"ffmpeg": path_or_None}."""
    return {"ffmpeg": _resolve("ffmpeg")}


# ── Chunking + synthesis (reuses Calliope's shared seam) ──────────────────────
def _chunk(text: str) -> list[str]:
    """Split prose into <=_CHUNK_MAX_CHARS pieces at natural boundaries, reusing
    calliope.take_chunk (one source of truth for kokoro chunking)."""
    chunks: list[str] = []
    rest = text
    while rest:
        chunk, rest = calliope.take_chunk(rest, _CHUNK_MAX_CHARS)
        if not chunk:
            break
        chunks.append(chunk)
    return chunks


def _encode_opus(ffmpeg: str, wav_path: str, out_path: str) -> dict[str, str]:
    """Encode wav_path -> out_path (.opus) via ffmpeg/libopus. A missing-libopus
    build (or any encode failure) surfaces as a nonzero exit -> {"error":
    "ffmpeg_encode_failed"}, and any partial output is removed so no half-written
    file survives."""
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "wav", "-i", wav_path,
           "-c:a", "libopus", "-b:a", _OPUS_BITRATE, out_path]
    proc = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW)
    if proc.returncode != 0:
        log.error("Echo: ffmpeg failed (rc=%s): %s", proc.returncode,
                  proc.stderr.decode("utf-8", "replace").strip())
        if os.path.isfile(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass
        return {"error": "ffmpeg_encode_failed"}
    return {"path": os.path.abspath(out_path)}


# ── Public API ────────────────────────────────────────────────────────────────
def text_to_audio(text: str, filename: str) -> dict[str, str]:
    """Markdown text + filename -> one .opus file in local_audio/. Returns
    {"path": abs} or {"error": code}. Never raises; never leaves a partial file.

    Error codes: empty_filename, empty_text, ffmpeg_unavailable,
    synthesis_failed, ffmpeg_encode_failed, echo_failed (catch-all)."""
    try:
        safe_name = sanitize_filename(filename)
        if not safe_name:
            return {"error": "empty_filename"}

        prose = _strip_markdown(text or "")
        if not prose:
            return {"error": "empty_text"}

        ffmpeg = _resolve("ffmpeg")
        if not ffmpeg:
            return {"error": "ffmpeg_unavailable"}

        chunks = _chunk(prose)
        if not chunks:
            return {"error": "empty_text"}

        pcms: list[np.ndarray] = []
        for chunk in chunks:
            pcm = calliope.synthesize(chunk)
            if pcm is None:
                return {"error": "synthesis_failed"}
            pcms.append(pcm)
        audio = np.concatenate(pcms)

        os.makedirs(_LOCAL_AUDIO_DIR, exist_ok=True)
        out_path = os.path.join(_LOCAL_AUDIO_DIR, safe_name)

        # Assemble the WAV in a temp file (stdlib wave via calliope.save_wav),
        # encode to Opus, then always delete the temp WAV.
        fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            calliope.save_wav(Path(wav_path), audio)
            return _encode_opus(ffmpeg, wav_path, out_path)
        finally:
            try:
                os.remove(wav_path)
            except OSError:
                pass
    except Exception as e:  # noqa: BLE001 — the whole point is to never raise
        log.error("Echo: conversion failed: %s", e)
        return {"error": "echo_failed"}


# ── Standalone demo ───────────────────────────────────────────────────────────
# Runs a REAL conversion (needs the kokoro model + ffmpeg present, unlike the
# hermetic tests/test_echo.py) and prints the result dict.
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sample = (
        "# Echo\n\n"
        "This is a **small** sample with a [link](https://example.com) and "
        "some `inline code`.\n\n"
        "```python\nprint('this fenced block is dropped')\n```\n\n"
        "- a bullet\n- another bullet\n"
    )
    print("[Echo] ffmpeg:", available()["ffmpeg"])
    print("[Echo] stripped:", _strip_markdown(sample))
    print("[Echo] converting sample -> echo_demo.opus ...")
    print("[Echo] result:", text_to_audio(sample, "echo_demo"))
