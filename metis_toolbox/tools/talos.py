"""
talos.py — The Bronze Automaton (Ollama's models)
=================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Report which models Ollama has installed and which it has loaded.
             Named for Talos, the bronze automaton Hephaestus built: the
             machine that runs, the way Ollama runs Pythia's model. This module
             reports on it; it loads, unloads and chats with nothing (pythia.py
             does that).

Contract:    Polled status (CONVENTIONS §2), not a brain tool:
               fetch() -> {"installed": [{"name", "size_gb", "params",
                                          "quant", "capabilities",
                                          "default_temperature"}],
                           "loaded":    [{"name", "size_gb", "vram_gb",
                                          "gpu_pct", "context",
                                          "kept_loaded"}]}
                          Raises when Ollama can't be reached or answers
                          nonsense, so Kairos delivers None and the panels say
                          "Ollama not reachable" (§2's raise-on-total-failure).
               capabilities(name) -> ["completion", "tools", ...] or None
                          For pythia: whether the chosen model can think.
                          None when unknown (Ollama down, an unknown name).
             `capabilities` and `default_temperature` are None for a model
             whose /api/show failed; the rest of the listing still stands.

Sources:     Three local endpoints, all through tools/hermes.py (bounded):
               GET  /api/tags   installed models     (467 B with one model)
               GET  /api/ps     loaded models        (406 B with one loaded)
               POST /api/show   one model's details  (26.5 KB: its license
                                and modelfile are most of it)
             Measured 2026-11-09, Ollama 0.40.13. /api/show runs only for a
             model whose digest hasn't been seen, so a steady poll is two
             small GETs; re-pulling a model changes its digest and re-reads it.

Readings:    gpu_pct is size_vram / size, so 100 means the whole model sits in
             GPU memory and less means part of it spilled to the CPU (slower
             answers). kept_loaded is an expiry more than a century away: the
             year 2319 Ollama reports for keep_alive=-1, pythia's setting.
             default_temperature is the model's own, from its parameters; the
             MACHINE SPIRIT stepper starts there.
             size_gb and vram_gb are Ollama's own figures and are NOT the GPU
             memory a model takes: for gemma4 they leave out most of it (e2b
             reports 0.32 GB, NVML measures 3.08 GB at 16k context; e4b 0.34
             of 4.52 GB; measured 2026-11-10). No panel shows them as GPU
             memory; Brontes's reading is the true total.

Upstream:    kairos.py (the "talos" worker, every 5 s), panels/hephaestus_panel.py
             (OllamaWidget), panels/machine_spirit_panel.py (the MODEL row),
             pythia.py (capabilities()).
Downstream:  tools/hermes.py, tools/aether.py (ollama_base_url).

Requires:    json, logging, os, re, sys, threading, datetime (stdlib).

V0.03:       Walked through 2026-11-17 (the V0.03 walkthrough, CONVENTIONS
             §3), its first audit (born 2026-11-09, #59). Found clean: every
             read goes through Hermes with a measured cap, fetch() raises on
             total failure and capabilities() never does, /api/show is read
             once per digest, and its logs name the exception's class only.
             Requires gained os and sys (the __main__ path).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from datetime import datetime
from typing import Any, Optional

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import aether, hermes

log = logging.getLogger("METIS.talos")

_TIMEOUT = 2                    # a local server; slower than this is not "up"
_LIST_CAP = 64 * 1024           # /api/tags and /api/ps: hundreds of bytes per model
_SHOW_CAP = 256 * 1024          # /api/show: 26.5 KB measured, the license is most
_GB = 1024 ** 3                 # the unit Hephaestus's GB figures use
_KEPT_YEARS = 100               # an expiry this far off means "never unload"
_TEMPERATURE = re.compile(r"^temperature\s+([0-9.]+)\s*$", re.MULTILINE)

_lock = threading.Lock()
# digest -> {"capabilities": [...], "default_temperature": float | None}
_details: dict[str, dict[str, Any]] = {}
# name -> digest, from the latest /api/tags
_digests: dict[str, str] = {}


def _get_json(path: str, cap: int) -> Any:
    reply = hermes.get(aether.ollama_base_url() + path, cap=cap, timeout=_TIMEOUT,
                       allow_http=True)
    reply.raise_for_status()
    return json.loads(reply.content)


def _show(name: str) -> Optional[dict[str, Any]]:
    """One model's capabilities and default temperature, or None (logged)."""
    try:
        reply = hermes.post(aether.ollama_base_url() + "/api/show",
                            json={"model": name}, cap=_SHOW_CAP,
                            timeout=_TIMEOUT, allow_http=True)
        reply.raise_for_status()
        body = json.loads(reply.content)
    except Exception as exc:
        log.warning("Talos: could not read %r's details (%s)", name,
                    type(exc).__name__)
        return None
    caps = body.get("capabilities")
    match = _TEMPERATURE.search(str(body.get("parameters") or ""))
    return {
        "capabilities": [str(c) for c in caps] if isinstance(caps, list) else None,
        "default_temperature": float(match.group(1)) if match else None,
    }


def _details_for(name: str, digest: str) -> dict[str, Any]:
    with _lock:
        known = _details.get(digest)
    if known is not None:
        return known
    found = _show(name)
    if found is None:                  # not cached: try again on the next poll
        return {"capabilities": None, "default_temperature": None}
    with _lock:
        _details[digest] = found
    return found


def _kept_loaded(expires_at: Any) -> bool:
    try:
        year = int(str(expires_at)[:4])
    except ValueError:
        return False
    return year > datetime.now().year + _KEPT_YEARS


def _installed(tags: Any) -> list[dict[str, Any]]:
    out = []
    digests: dict[str, str] = {}
    for model in tags.get("models") or []:
        name = str(model["name"])
        digest = str(model.get("digest") or name)
        digests[name] = digest
        details = model.get("details") or {}
        out.append({
            "name": name,
            "size_gb": round(int(model.get("size") or 0) / _GB, 2),
            "params": details.get("parameter_size"),
            "quant": details.get("quantization_level"),
            **_details_for(name, digest),
        })
    with _lock:
        _digests.clear()
        _digests.update(digests)
    return out


def _loaded(ps: Any) -> list[dict[str, Any]]:
    out = []
    for model in ps.get("models") or []:
        size = int(model.get("size") or 0)
        vram = int(model.get("size_vram") or 0)
        out.append({
            "name": str(model["name"]),
            "size_gb": round(size / _GB, 2),
            "vram_gb": round(vram / _GB, 2),
            "gpu_pct": round(100 * vram / size) if size else 0,
            "context": model.get("context_length"),
            "kept_loaded": _kept_loaded(model.get("expires_at")),
        })
    return out


# ── Public API ────────────────────────────────────────────────────────────────

def fetch() -> dict[str, Any]:
    """Kairos entry point. Raises when Ollama is unreachable or its answer
    isn't the shape it documents; Kairos then delivers None."""
    tags = _get_json("/api/tags", _LIST_CAP)
    ps = _get_json("/api/ps", _LIST_CAP)
    return {"installed": _installed(tags), "loaded": _loaded(ps)}


def capabilities(name: str) -> Optional[list[str]]:
    """What `name` can do, from the cached /api/show, reading it now if this
    model hasn't been seen. None when it can't be known. Never raises."""
    try:
        with _lock:
            digest = _digests.get(name)
            known = _details.get(digest) if digest else None
        if known is None:
            found = _show(name)
            if found is None:
                return None
            if digest:
                with _lock:
                    _details[digest] = found
            known = found
        caps = known.get("capabilities")
        return list(caps) if caps is not None else None
    except Exception as exc:
        log.warning("Talos: capabilities(%r) failed (%s)", name, type(exc).__name__)
        return None


def _clear_cache() -> None:
    """Forget every model's details. For tests."""
    with _lock:
        _details.clear()
        _digests.clear()


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(f"[Talos] {aether.ollama_base_url()}")
    try:
        data = fetch()
    except Exception as exc_main:
        print(f"[Talos] Ollama not reachable ({type(exc_main).__name__})")
    else:
        for m in data["installed"]:
            print(f"[Talos] installed {m['name']}: {m['size_gb']} GB, {m['params']} "
                  f"{m['quant']}, caps={m['capabilities']}, "
                  f"temperature={m['default_temperature']}")
        for m in data["loaded"]:
            print(f"[Talos] loaded    {m['name']}: {m['gpu_pct']}% on GPU "
                  f"({m['vram_gb']} of {m['size_gb']} GB), context {m['context']}, "
                  f"kept_loaded={m['kept_loaded']}")
        if not data["loaded"]:
            print("[Talos] nothing loaded")
