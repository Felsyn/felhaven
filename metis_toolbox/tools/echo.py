"""
echo.py — Echo, the Scribe of Voices
====================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Turn text into an audio file on disk. Typically Markdown, and
             nothing else. Echo does not PLAY audio (Calliope speaks Pythia's
             answers; Orpheus plays Echo's files), does not control an audio
             engine (that is Morpheus), and does not decide WHAT to narrate —
             it is handed text + a filename and writes one .opus file.

Contract:    text_to_audio(text, filename, *, voice=None) -> dict
                 Strip the Markdown to readable prose, chunk it, synthesize each
                 chunk via Calliope's shared model, concatenate the PCM, and
                 encode it to an Opus file in local_audio/. `voice` names a
                 kokoro voice for this one conversion (None = Calliope's current
                 narration voice), so rendering a file in a different voice never
                 disturbs how Pythia's answers are read aloud. Returns
                 {"path": "<abs .opus path>"} on success, or {"error": "<code>"}
                 on any failure: empty_filename, empty_text, file_exists,
                 ffmpeg_unavailable, synthesis_failed, ffmpeg_encode_failed, or
                 echo_failed (the catch-all). NEVER raises, and a failed
                 conversion leaves no file behind. While ffmpeg encodes, the
                 file grows in place, so ORPHEUS can list it before it is done
                 (16 s for a 25-minute file, measured 2026-11-21).
             available() -> {"ffmpeg": <path or None>, "problem": <slug or None>}
                 Report whether the one external binary is resolvable, mirroring
                 morpheus.available(); "problem" is aegis's slug when
                 bin/ffmpeg.exe was refused. The panel reads this to disable
                 its UI.
             sanitize_filename(name) -> str
                 The bare "<stem>.opus" a typed name becomes, or "" when nothing
                 usable survives. The panel gates its Send button on it.

Write-once:  Echo never replaces a file in local_audio/. A name already there
             is refused (file_exists) before any synthesis, and claimed with
             an exclusive create just before ffmpeg runs, so a name taken
             during a long synthesis is refused too. A failed encode then
             removes only the file Echo itself created. Until 2026-11-21
             ffmpeg overwrote (-y), and a failed encode deleted the old file
             it had never touched (the owner's pick, CONVENTIONS §12).

Source:      One external binary, zero new pip packages.
               - ffmpeg : encodes the assembled WAV to .opus (libopus).
                          tools/aegis.py resolves it: bin/ffmpeg.exe when it
                          matches its manifest entry, else ffmpeg.exe on PATH;
                          a bin/ copy that doesn't match is refused, never
                          swapped for PATH's. Absent, refused, or built
                          without libopus, each degrades to a clean
                          {"error": ...}.
             TTS itself is NOT owned here: Echo calls calliope.synthesize() and
             reuses calliope.take_chunk() / save_wav(), so there is exactly one
             loaded kokoro model and one WAV writer in the process (save_wav owns
             the sample rate internally — see calliope.py "Shared seam").

Markdown:    Echo eats real .md, so its stripper is deliberately MORE thorough
             than Calliope's defensive character strip:
               - DROPS fenced code blocks (``` / ~~~) entirely — reading code
                 aloud is unlistenable and prose is the job. INDENTED code is
                 left AS prose: detecting it without a real Markdown parser risks
                 eating legitimately-indented text, so it is a deliberate,
                 documented limit.
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
             It has the surface of §2's "Local-only ledger / persistence" row
             (plain functions), though what it keeps is audio files, not a
             ledger. A future narrate_to_file brain tool is left open by this
             shape, not built.

Threads:     text_to_audio() runs on EchoPanel's conversion thread. It keeps
             no module state; Calliope's own lock guards the model (see
             Contention).

Upstream:    panels/echo_panel.py (the paste-and-convert UI).
Downstream:  calliope.py (synthesize, take_chunk, save_wav) -> ffmpeg (Opus
             encode), vouched for by tools/aegis.py. tools/orpheus.py plays
             what lands in local_audio/.

Requires:    calliope, tools.aegis; numpy; the ffmpeg binary (bin/ or PATH).
             Stdlib: logging, ntpath, os, re, subprocess, sys, tempfile,
             pathlib, typing.

2026-07-25:  walkthrough audit — never-raise contract verified, error slugs
             already conformant (never in the output allowlist). echo_panel now
             reads available() at build time to disable the tab when ffmpeg is
             absent (§7); SAMPLE_RATE dropped from the reused-seam list (Echo
             never references it — save_wav owns it).

V0.03:       Walked through 2026-11-21 (the V0.03 walkthrough, CONVENTIONS
             §3/§12). Built 2026-07-15. Checked: the Markdown strip and
             chunking against the tests; kokoro-onnx 0.6.1 splits any text
             into batches of at most 510 phonemes itself, so one create() call
             has no length ceiling. Changed: a name already in local_audio/ is
             refused before synthesis and claimed exclusively before ffmpeg,
             so a failed encode removes only Echo's own file (it had deleted
             the old file of that name; the owner's pick); a launch that
             raises cleans up like a failed one; sanitize_filename() refuses
             every name Windows reserves (ntpath.isreserved: "con .opus",
             "COM¹", "CONIN$" got through); the Contract lists every error,
             available()'s "problem" and sanitize_filename(); Upstream dropped
             "Eos later" (Eos is the space-weather module); Requires names
             every import.
"""

import logging
import ntpath
import os
import re
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
from tools import aegis

log = logging.getLogger("METIS.echo")

# ── Paths (anchored to __file__, cwd-independent — morpheus precedent) ─────────
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOCAL_AUDIO_DIR = os.path.join(_APP_ROOT, "local_audio")

# Hide the console window ffmpeg would flash on Windows. getattr so importing on
# a non-Windows box can't AttributeError (morpheus precedent).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Per-chunk char cap for a single kokoro create() call. Calliope's live cap is
# 240, sized for playback latency; Echo has no latency budget, so it uses a
# larger cap to amortize the per-call overhead across a whole document. There
# is no ceiling to stay under: kokoro-onnx 0.6.1 splits any text into batches
# of at most 510 phonemes itself (checked 2026-11-21).
_CHUNK_MAX_CHARS = 400

# Opus bitrate for speech — 48 kbps is transparent for a single mono voice.
_OPUS_BITRATE = "48k"


# ── Filename sanitisation ─────────────────────────────────────────────────────
# Never trust the filename field: strip path separators + Windows-illegal chars,
# reject reserved device names, and force a single .opus extension. Without this
# a stray "/" or ":" could write outside local_audio/.
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    """Return a safe bare '<stem>.opus' filename, or '' if nothing usable
    survives (the caller turns '' into an empty_filename error). Strips illegal
    chars and leading/trailing dots+spaces, appends .opus unless it is already
    present, and refuses any name Windows reserves for a device.

    ntpath.isreserved, not a hand-kept list: Windows also reserves a device
    name followed by spaces ("con .opus"), one with a superscript digit
    ("COM¹"), and CONIN$/CONOUT$, and the old list of twenty-two plain names
    let all of those through (measured 2026-11-21). ntpath, not os.path, so
    the rule is Windows's on any machine the tests run on."""
    cleaned = _ILLEGAL.sub("", name).strip().strip(".").strip()
    if not cleaned:
        return ""
    if not cleaned.lower().endswith(".opus"):
        cleaned += ".opus"
    if ntpath.isreserved(cleaned):
        return ""
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


# ── Binary resolution (tools/aegis.py: a verified bin/ copy, else PATH) ───────
def _resolve(name: str) -> Optional[str]:
    """The program to run, vouched for by tools/aegis.py: bin/<name>.exe if it
    matches its manifest entry, else a copy on PATH (unverified, warned once).
    A bin/ copy that doesn't match is refused (None), never swapped for PATH's."""
    return aegis.resolve(name).path


def available() -> dict[str, Optional[str]]:
    """Report whether ffmpeg is resolvable. The panel reads this once at build
    time; None means the convert UI is disabled. {"ffmpeg": path_or_None,
    "problem": None, or aegis's slug when bin/ffmpeg.exe was refused}."""
    return {"ffmpeg": _resolve("ffmpeg"), "problem": aegis.resolve("ffmpeg").problem}


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
    """Claim out_path, then encode wav_path into it (.opus) via ffmpeg/libopus.

    The claim is an exclusive create (O_EXCL): if out_path already exists,
    nothing runs and the answer is {"error": "file_exists"}, so the file
    ffmpeg then overwrites (-y) is always the empty one Echo just made (see
    the module "Write-once" section). A missing-libopus build, any other
    encode failure, or a launch that raises -> {"error":
    "ffmpeg_encode_failed"}, and Echo's own file is removed, so no
    half-written file survives.

    Both paths must be absolute, or nothing runs (same error). ffmpeg reads an
    argument starting with "-" as an option and one with "name:" before its
    first slash as a protocol (concat:, http:); an absolute path can be
    neither, since ffmpeg reads a leading drive letter as a plain file. Both
    are absolute by construction (local_audio/ is anchored to this file, and
    mkstemp returns an absolute path), so this only makes the day that changes
    loud (CONVENTIONS §12, 2026-10-03)."""
    if not (os.path.isabs(wav_path) and os.path.isabs(out_path)):
        log.error("Echo: refused to hand ffmpeg a path that isn't absolute")
        return {"error": "ffmpeg_encode_failed"}
    try:
        os.close(os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL))
    except FileExistsError:
        log.warning("Echo: %s appeared during synthesis; not replacing it",
                    os.path.basename(out_path))
        return {"error": "file_exists"}
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "wav", "-i", wav_path,
           "-c:a", "libopus", "-b:a", _OPUS_BITRATE, out_path]
    try:
        proc = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW)
        failed = proc.returncode != 0
        if failed:
            log.error("Echo: ffmpeg failed (rc=%s): %s", proc.returncode,
                      proc.stderr.decode("utf-8", "replace").strip())
    except OSError as e:
        log.error("Echo: ffmpeg failed to run: %s", type(e).__name__)
        failed = True
    if failed:
        try:
            os.remove(out_path)
        except OSError:
            pass
        return {"error": "ffmpeg_encode_failed"}
    return {"path": os.path.abspath(out_path)}


# ── Public API ────────────────────────────────────────────────────────────────
def text_to_audio(text: str, filename: str, *,
                  voice: Optional[str] = None) -> dict[str, str]:
    """Markdown text + filename -> one .opus file in local_audio/. Returns
    {"path": abs} or {"error": code}. Never raises; never leaves a partial file.

    `voice` is a kokoro voice id for this conversion only; None uses Calliope's
    current narration voice. An id kokoro doesn't know surfaces as
    synthesis_failed like any other synthesis failure — voices.CATALOG is the
    gate, and the panel only ever offers ids from it.

    A name already in local_audio/ is refused here, before minutes of
    synthesis, and again by _encode_opus's exclusive create (the module
    "Write-once" section).

    Error codes: empty_filename, empty_text, file_exists, ffmpeg_unavailable,
    synthesis_failed, ffmpeg_encode_failed, echo_failed (catch-all)."""
    try:
        safe_name = sanitize_filename(filename)
        if not safe_name:
            return {"error": "empty_filename"}

        prose = _strip_markdown(text or "")
        if not prose:
            return {"error": "empty_text"}

        out_path = os.path.join(_LOCAL_AUDIO_DIR, safe_name)
        if os.path.lexists(out_path):
            return {"error": "file_exists"}

        ffmpeg = _resolve("ffmpeg")
        if not ffmpeg:
            return {"error": "ffmpeg_unavailable"}

        chunks = _chunk(prose)
        if not chunks:
            return {"error": "empty_text"}

        pcms: list[np.ndarray] = []
        for chunk in chunks:
            pcm = calliope.synthesize(chunk, voice=voice)
            if pcm is None:
                return {"error": "synthesis_failed"}
            pcms.append(pcm)
        audio = np.concatenate(pcms)

        os.makedirs(_LOCAL_AUDIO_DIR, exist_ok=True)

        # Assemble the WAV in a temp file (stdlib wave via calliope.save_wav),
        # claim the name and encode to Opus, then always delete the temp WAV.
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
