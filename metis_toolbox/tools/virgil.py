"""
virgil.py — The Guide
=====================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Give a tooltip its words: a module's own gloss, or a glossary entry.
             Named for Dante's guide through the underworld, who told the
             traveller who each soul was and how each circle worked. This
             module decides what a tooltip says; it draws nothing
             (panels/virgil_tooltip.py draws).

Contract:    Plain functions, not a brain tool:
               module(key)    -> Tip("module", key)
               term(key)      -> Tip("term", key)
               text_for(tip)  -> Words(title, body, verbatim) or None
               glossary()     -> {key: {"label", "text"}}, a copy
             text_for() never raises. Any miss (an unknown key, a gloss
             Daedalus cannot produce, a missing or malformed glossary) is None,
             plus ONE warning per (kind, key) per process, so a broken key
             hovered fifty times is one line in Emanon, not fifty.

Two sources, each stored the honest way:
             • A MODULE tip is the module's own words: its Job: first sentence
               (CONVENTIONS §0), asked of tools/daedalus.py's gloss() at hover
               time. Daedalus reads source with ast and never imports, because
               importing a module to read its __doc__ would run its threads,
               sockets and audio. The gloss is never copied here: a module's job
               can change, and its docstring is the one place that says so.
               Words.verbatim is True, and the tooltip draws it in
               C["verbatim"], the colour for text shown exactly as a tool gave
               it. The title is the key, spaced and title-cased
               ("machine_spirit" -> "Machine Spirit").
             • A TERM tip is the owner's words, from the glossary below: the
               Gothic UI names (Moderati, Cogitator, …) and the readouts (CPU,
               RAM, Ollama, …), things that don't drift with the code.

Glossary:    config/virgil_glossary.json — {"_comment", "terms": {key:
             {"label", "text"}}}, hand-written config, committed (§4), the
             hypatia_lore.json precedent. File order is display order. It is
             re-read when its (mtime_ns, size) moves, so an edit made while
             Felhaven runs shows on the next hover. A missing or malformed file
             reads as empty (every term tip is then None) and warns once per
             state of the file, never per hover. An entry without a non-empty
             string label and text is skipped with a warning; a duplicate key
             keeps its FIRST entry (plain json keeps the last, silently).
             tests/test_virgil.py holds the shipped file to its rules: keys are
             lowercase snake_case, each text ends in . ! or ? within 240
             characters, and no key equals a Daedalus node key. A module's
             description lives in its docstring only; a glossary entry for one
             would be a second copy that drifts. (So Home's key is `home`:
             `felhaven` is a module.)

Not a brain tool: out of LLM scope in v1 (CONVENTIONS §12, 2026-11-09).
             Module glosses already reach Pythia through Daedalus's
             get_module_docstring enum, and a glossary tool would cost prefix
             tokens on every chat turn. Parked in BACKLOG with its trigger.

Source:      Two local reads: Daedalus's index of the source tree, and one
             JSON file. No network, no threads, no file writes.

Upstream:    panels/virgil_tooltip.py (the tooltip), and the _TIPS tables that
             build tips with module() and term(): panels/sidebar.py, the three
             tab hosts and panels/hephaestus_panel.py.
Downstream:  tools/daedalus.py (gloss()), config/virgil_glossary.json.

Requires:    tools/daedalus.py, and json, logging, os, re, threading,
             typing (stdlib).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from typing import Any, NamedTuple, Optional

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import daedalus

log = logging.getLogger("METIS.virgil")

# ── Config ────────────────────────────────────────────────────────────────────

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GLOSSARY_PATH = os.path.join(_APP_ROOT, "config", "virgil_glossary.json")

# The rules the shipped glossary is held to (tests/test_virgil.py). The loader
# itself does not refuse a long or oddly keyed entry: a tooltip that says a
# little too much is better than none, and the test catches it before it ships.
KEY_SHAPE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
MAX_TEXT = 240

_KINDS = ("module", "term")


# ── Types ─────────────────────────────────────────────────────────────────────

class Tip(NamedTuple):
    """What a widget points at. Built by module() or term(), resolved to
    words only at hover time, so a tip attached at startup always shows the
    current text."""
    kind: str          # "module" | "term"
    key: str


class Words(NamedTuple):
    title: str         # the module's name from its key, or the term's label
    body: str          # the module's gloss, or the glossary text
    verbatim: bool     # True for a module gloss: drawn in C["verbatim"]


# ── Glossary cache ────────────────────────────────────────────────────────────

class _Cache:
    def __init__(self) -> None:
        self.path: Optional[str] = None
        self.stamp: Optional[tuple[int, int]] = None
        self.loaded = False
        self.terms: dict[str, dict[str, str]] = {}


# Only the main thread hovers today; the lock is for the later reader a
# Pythia tool would be (BACKLOG), which runs on a worker thread.
_lock = threading.Lock()
_cache = _Cache()
_warned: set[tuple[str, str]] = set()


def _stamp(path: str) -> Optional[tuple[int, int]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _first_wins(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """json object hook: a duplicate key keeps its first value, and says so."""
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            log.warning("Virgil: the glossary repeats the key %r — keeping "
                        "the first", key)
            continue
        out[key] = value
    return out


def _load(path: str) -> dict[str, dict[str, str]]:
    """{key: {"label", "text"}} in file order, or {} on any failure (logged)."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh, object_pairs_hook=_first_wins)
        entries = raw["terms"]
        if not isinstance(entries, dict):
            raise TypeError("terms is not an object")
    except FileNotFoundError:
        log.warning("Virgil: no glossary at config/%s — glossary tips are "
                    "off", os.path.basename(path))
        return {}
    except Exception as exc:
        log.warning("Virgil: could not read the glossary (%s) — glossary tips "
                    "are off", type(exc).__name__)
        return {}
    terms: dict[str, dict[str, str]] = {}
    for key, entry in entries.items():
        label = entry.get("label") if isinstance(entry, dict) else None
        text = entry.get("text") if isinstance(entry, dict) else None
        if not (isinstance(label, str) and label.strip()
                and isinstance(text, str) and text.strip()):
            log.warning("Virgil: skipping glossary entry %r — it needs a "
                        "label and a text", key)
            continue
        terms[key] = {"label": label.strip(), "text": " ".join(text.split())}
    return terms


def _terms() -> dict[str, dict[str, str]]:
    """The glossary, re-read only when the file's (mtime_ns, size) moved —
    one stat per hover otherwise."""
    path = _GLOSSARY_PATH
    stamp = _stamp(path)
    with _lock:
        if not (_cache.loaded and _cache.path == path and _cache.stamp == stamp):
            _cache.terms = _load(path)
            _cache.path, _cache.stamp, _cache.loaded = path, stamp, True
        return _cache.terms


def _miss(tip: Any, why: str) -> None:
    """One WARNING per (kind, key) per process."""
    try:
        mark = (str(tip.kind), str(tip.key))
    except Exception:
        mark = ("?", repr(tip))
    with _lock:
        if mark in _warned:
            return
        _warned.add(mark)
    log.warning("Virgil: no tooltip for %s %r — %s", mark[0], mark[1], why)


def _title(key: str) -> str:
    return " ".join(part.capitalize() for part in key.split("_") if part)


# ── Public API ────────────────────────────────────────────────────────────────

def module(key: str) -> Tip:
    """A tip that shows a module's own gloss, read through Daedalus."""
    return Tip("module", key)


def term(key: str) -> Tip:
    """A tip that shows a glossary entry."""
    return Tip("term", key)


def text_for(tip: Tip) -> Optional[Words]:
    """The words for one tip, or None. Never raises."""
    try:
        if tip.kind == "module":
            gloss = daedalus.gloss(tip.key)
            if not gloss:
                _miss(tip, "Daedalus has no gloss for it")
                return None
            return Words(_title(tip.key), gloss, True)
        if tip.kind == "term":
            entry = _terms().get(tip.key)
            if entry is None:
                _miss(tip, "the glossary has no such entry")
                return None
            return Words(entry["label"], entry["text"], False)
        _miss(tip, f"the kind must be one of {_KINDS}")
        return None
    except Exception as exc:
        _miss(tip, f"failed ({type(exc).__name__})")
        return None


def glossary() -> dict[str, dict[str, str]]:
    """Every glossary entry, in file order. A copy — mutate it freely."""
    return {key: dict(entry) for key, entry in _terms().items()}


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    entries_main = glossary()
    print(f"[Virgil] {len(entries_main)} glossary entr"
          f"{'y' if len(entries_main) == 1 else 'ies'} in "
          f"{os.path.basename(_GLOSSARY_PATH)}")
    tips_main = [term(k) for k in entries_main]
    tips_main += [module(k) for k in ("hephaestus", "machine_spirit", "euterpe",
                                      "cerberus", "virgil")]
    for tip_main in tips_main:
        words_main = text_for(tip_main)
        if words_main is None:
            print(f"[Virgil] {tip_main.kind:6} {tip_main.key:16} (no tooltip)")
        else:
            mark_main = "verbatim" if words_main.verbatim else "glossary"
            print(f"[Virgil] {tip_main.kind:6} {tip_main.key:16} "
                  f"[{mark_main}] {words_main.title}: {words_main.body}")
