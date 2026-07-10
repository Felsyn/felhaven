"""
selene.py — Titaness of the Moon
================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Report the moon's phase, illumination, moonrise, and moonset.
             Interprets Aura's astronomy dict into display strings (with a
             phase glyph).

Contract:    Pure functions. No network, no state file. interpret() takes
             Aura's astronomy dict and returns display strings, or None only
             when the dict itself is empty/None. Nothing here raises — an
             unknown phase string degrades to a generic moon, never a KeyError.

             The get_moon_phase LLM tool (TOOL_DEFINITION + handle) is added
             below: handle() sources tonight's astronomy from Aura, then reuses
             interpret() to shape the answer — a thin wrapper over the pure core.

Upstream:    tools/aura.py (the astronomy dict it hands back from wttr.in)
Downstream:  panels/aura_panel.py → SeleneWidget (display surface)

Requires:    stdlib only (datetime)

Note: wttr.in genuinely returns 'No moonrise' / 'No moonset' on days the moon
doesn't cross the horizon. Those (and anything unparseable) become '—'.
"""

from datetime import datetime
from typing import Any

# Canonical wttr.in phase string -> (glyph, label). Eight phases, in order.
PHASES = {
    "New Moon":        ("🌑", "New Moon"),
    "Waxing Crescent": ("🌒", "Waxing Crescent"),
    "First Quarter":   ("🌓", "First Quarter"),
    "Waxing Gibbous":  ("🌔", "Waxing Gibbous"),
    "Full Moon":       ("🌕", "Full Moon"),
    "Waning Gibbous":  ("🌖", "Waning Gibbous"),
    "Last Quarter":    ("🌗", "Last Quarter"),
    "Waning Crescent": ("🌘", "Waning Crescent"),
}

# Shown when a moon time is absent ('No moonrise') or unparseable.
_NO_TIME = "—"

_CLOCK_FMT = "%I:%M %p"


# ── Internals ─────────────────────────────────────────────────────────────────

def _moon_time(s: str) -> str:
    """
    '02:31 AM' -> '2:31 AM'. 'No moonrise' / '' / anything unparseable -> '—'.
    Never raises.
    """
    try:
        t = datetime.strptime((s or "").strip(), _CLOCK_FMT)
    except (ValueError, TypeError):
        return _NO_TIME
    return t.strftime(_CLOCK_FMT).lstrip("0")


def _illumination(s: str) -> str:
    """'32' -> '32%'. Missing / unparseable -> ''."""
    try:
        return f"{int((s or '').strip())}%"
    except (ValueError, TypeError):
        return ""


# ── Public API ────────────────────────────────────────────────────────────────

def interpret(astro: dict[str, Any]) -> dict[str, Any] | None:
    """
    Top-level entry the widget calls. Takes Aura's astronomy dict and returns
    display-ready strings, or None only if astro itself is None/empty.

        {"emoji": "🌘", "phase": "Waning Crescent",
         "illumination": "32%",   # "" if missing/unparseable
         "moonrise": "2:31 AM",   # "—" if "No moonrise"
         "moonset": "3:18 PM"}    # "—" if "No moonset"

    An unrecognised phase string degrades to ("🌙", <raw string>).
    """
    if not astro:
        return None
    phase_raw = (astro.get("moon_phase") or "").strip()
    emoji, phase = PHASES.get(phase_raw, ("🌙", phase_raw))
    return {
        "emoji":        emoji,
        "phase":        phase,
        "illumination": _illumination(astro.get("moon_illumination", "")),
        "moonrise":     _moon_time(astro.get("moonrise", "")),
        "moonset":      _moon_time(astro.get("moonset", "")),
    }


# ── LLM tool contract ─────────────────────────────────────────────────────────
# Selene is a pure interpreter; the handle sources tonight's astronomy from Aura
# (its documented upstream) and shapes the lunar answer.

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "get_moon_phase",
        "description": (
            "Returns the current moon phase (with illumination percent) plus "
            "tonight's moonrise and moonset for the dashboard's location. Call "
            "when the user asks about the moon, its phase, how full it is, or "
            "moonrise/moonset."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def handle() -> dict[str, Any]:
    """Toolbox entry: fetch tonight's astronomy via Aura, interpret the lunar
    data. Never raises — a network failure or empty astronomy degrades to an
    error dict the model can relay."""
    from tools import aura        # lazy: keeps this module importable standalone
    weather = aura.handle()
    if "error" in weather:
        return {"error": "moon_unavailable"}
    info = interpret(weather.get("astronomy", {}))
    if info is None:
        return {"error": "moon_unavailable"}
    return info


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    sample = {
        "sunrise": "05:52 AM", "sunset": "08:45 PM",
        "moonrise": "02:31 AM", "moonset": "03:18 PM",
        "moon_phase": "Waning Crescent", "moon_illumination": "32",
    }
    info = interpret(sample)
    if info is None:
        print("[Selene] moon data unavailable")
    else:
        print(
            f"[Selene] {info['emoji']} {info['phase']} | "
            f"illum {info['illumination'] or '—'} | "
            f"rise {info['moonrise']} | set {info['moonset']}"
        )
