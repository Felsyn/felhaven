"""
voices.py — the Voice Roster
============================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Resolve the voice Felhaven should speak in.

             The roster is the domain of valid answers; the saved pick is the
             current answer. Both halves are the same question, so they live in
             one module. voices.py knows nothing about synthesis (Calliope),
             files-on-disk audio (Echo), or tkinter — it only names voices and
             remembers which one Pythia is set to.

Contract:    Root-level persistence flavor (machine_spirit.py / themis.py
             precedent): no tkinter, no fetch(), just plain functions.
               CATALOG               — the selectable voice ids, display order.
               label(voice)          — "Nicole — American female" for a menu.
               default()             — Calliope's configured voice, validated
                                       against CATALOG. Never raises.
               pythia_voice()        — the saved pick, else default(). Read
                                       fresh from disk every call. Never raises.
               save_pythia_voice(v)  — persist the pick (atomic write).
                                       VoiceError on an id outside CATALOG.

English:     CATALOG is the en-us subset of kokoro's 54 voices — the 28 ids
             starting af_/am_/bf_/bm_. The other 26 are Spanish, French, Hindi,
             Italian, Japanese, Portuguese and Chinese; Calliope pins lang to
             "en-us", so offering them would only mispronounce English. Adding
             them is a lang-switching feature, not a longer list.

Roster:      CATALOG is HARDCODED, not scanned from voices-v1.0.bin, because
             kokoro_models/ is gitignored — a fresh clone and CI have no .bin to
             read. tests/test_voices.py keeps it honest in lockstep: when the
             .bin IS present it opens it (an NPZ zip archive) and asserts CATALOG
             equals its af_/am_/bf_/bm_ subset; when it is absent the test skips.

Storage:     voice_config.json beside this file, holding only Pythia's pick.
             Nothing is written until the user chooses a voice, so a future
             change to Calliope's configured default takes effect for every
             install that hasn't picked one. Gitignored (per-user state, the
             machine_spirit_config.json precedent).

Echo:        Echo's per-conversion voice is deliberately NOT stored here. It is
             a per-job choice, held in the panel for the session and seeded from
             pythia_voice() at build time — see panels/echo_panel.py.

Upstream:    panels/voice_bar_panel.py (the dropdown, via its two hosts),
             felhaven.py (seeds Calliope from pythia_voice() at startup)
Downstream:  voice_config.json; calliope.py (read-only, for default())

Requires:    json, logging, os, tempfile, pathlib (stdlib only).
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import calliope

log = logging.getLogger("METIS.voices")

_DATA_PATH = Path(__file__).with_name("voice_config.json")


class VoiceError(Exception):
    """Raised by save_pythia_voice() for an id outside CATALOG (the themis.py
    SettingsError precedent — input validation, not an I/O failure)."""


# The 28 en-us kokoro voices, grouped American-then-British, female-then-male,
# alphabetical within each group — the order they appear in the dropdown.
CATALOG: tuple[str, ...] = (
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore",
    "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael",
    "am_onyx", "am_puck", "am_santa",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
)

# kokoro's id prefix -> how to say it in a menu. First letter = language
# (a=American, b=British), second = voice gender.
_ACCENT = {"a": "American", "b": "British"}
_GENDER = {"f": "female", "m": "male"}


def label(voice: str) -> str:
    """A human-readable menu label: "af_nicole" -> "Nicole — American female".
    An unrecognised id is returned unchanged rather than mangled, so a roster
    edit can never produce a blank menu entry."""
    prefix, _, name = voice.partition("_")
    if len(prefix) != 2 or not name:
        return voice
    accent = _ACCENT.get(prefix[0])
    gender = _GENDER.get(prefix[1])
    if accent is None or gender is None:
        return voice
    return f"{name.replace('_', ' ').title()} — {accent} {gender}"


def default() -> str:
    """Calliope's configured voice if it is one we offer, else the first entry
    in CATALOG. Never raises — a garbled config must not empty the dropdown."""
    configured = str(calliope.configured_voice() or "")
    return configured if configured in CATALOG else CATALOG[0]


def _load_pick() -> str:
    """The stored pick, or "" if none/unreadable/unknown (fail-soft, never
    raises). An id that has since left CATALOG reads as "" so the caller falls
    back to default() instead of asking kokoro for a voice we no longer offer."""
    try:
        with open(_DATA_PATH, "r", encoding="utf-8") as f:
            raw: Any = json.load(f)
        if not isinstance(raw, dict):
            return ""
        pick = str(raw.get("pythia", "")).strip()
        return pick if pick in CATALOG else ""
    except FileNotFoundError:
        return ""
    except (OSError, json.JSONDecodeError, ValueError) as e:
        log.warning("voices: config unreadable, using default: %s", e)
        return ""


def pythia_voice() -> str:
    """The voice Pythia's answers are read aloud in: the saved pick if one is
    set, else default(). Read fresh from disk every call."""
    return _load_pick() or default()


def save_pythia_voice(voice: str) -> None:
    """Persist `voice` as Pythia's narration voice. Atomic write: tempfile in
    the same dir + os.replace (the machine_spirit.py pattern).

    Raises VoiceError if `voice` is not in CATALOG — an unknown id would reach
    kokoro as a KeyError deep inside synthesis, long after the UI could say so."""
    if voice not in CATALOG:
        raise VoiceError(f"unknown voice: {voice!r}")

    directory = os.path.dirname(os.path.abspath(_DATA_PATH))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".voices-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pythia": voice}, f, indent=2)
        os.replace(tmp, _DATA_PATH)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    log.info("voices: Pythia's narration voice set to %s", voice)


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"[Voices] {len(CATALOG)} voices offered")
    for _v in CATALOG:
        print(f"  {_v:<14} {label(_v)}")
    print(f"[Voices] calliope default: {default()}")
    print(f"[Voices] Pythia is set to: {pythia_voice()}")
