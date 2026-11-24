"""
iris.py — The Rainbow Messenger (weather warnings)
==================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Report the National Weather Service's active alerts for Felhaven's place.
             Named for Iris, messenger of the gods, whose road is the rainbow:
             she carries the sky's warnings. This module reports them; the
             popup, the voice and "seen" belong to panels/iris_panel.py.

Contract:    Polled status (CONVENTIONS §2), not a brain tool:
               fetch() -> {"enabled": bool, "covered": bool,
                           "alerts": [{"id", "event", "tier", "severity",
                                       "certainty", "summary", "description",
                                       "instruction", "sent", "until",
                                       "references"}]}
               enabled False  Settings › Alerts is off. Nothing was asked.
               covered False  The place is outside the Weather Service's area
                              (the US and its territories). A checked answer:
                              alerts is empty because there is nothing to ask.
             Anything else that fails (offline, a timeout, an HTTP error, a
             body not shaped as documented) RAISES, so Kairos delivers None
             and the panels say "couldn't check". A failure must never read
             as "none active" (§2's raise-on-total-failure).

Source:      GET https://api.weather.gov/alerts/active?point=LAT,LON&status=actual
             GeoJSON, keyless. Measured 2026-11-10: 233 B with nothing active;
             the largest single alert among the 391 then active in the US was
             11.9 KB; a point outside the US answers 400 with "out of bounds"
             in its detail. The NWS asks every client for a User-Agent. This
             one names the app and carries no contact details, because every
             install sends it.

Readings:    tier      "warning": the event ends in Warning or Emergency, or
                       is "Evacuation Immediate" or "Civil Emergency Message"
                       (two that need acting on but don't say so), or its
                       severity is Extreme.
                       "watch": the event ends in Watch. "advisory": the rest
                       (advisories, statements). The panel pops up warnings,
                       or warnings and watches (Settings › Alerts), and only
                       lists advisories.
             summary   The description's "* WHAT..." paragraph, else its first
                       sentence, else the headline: the one line the banner,
                       the popup and the voice use.
             until     When the event ends (`ends`), else when this message
                       expires (`expires`): NWS reissues a long warning under
                       a new id before `expires`, so `ends` is the real end.
             references The ids of the messages this one updates. An extended
                       warning arrives as a new id that references the old
                       one, which is how the panel keeps it "seen".
             Times stay ISO strings, as sent; the panel shows them in local time.
             Alerts come warnings first, then watches, then advisories,
             newest first within each.

Upstream:    kairos.py (the "iris" worker, every 60 s), panels/iris_panel.py
             (AlertWatch).
Downstream:  tools/hermes.py (the bounded read), themis.py (the place and the
             alerts switch, read at every fetch).

Requires:    json, logging, os, re, sys, threading, datetime (stdlib).

V0.03:       Walked through 2026-11-17 (the V0.03 walkthrough, CONVENTIONS
             §3), its first audit (born 2026-11-10, #61). The read goes
             through Hermes with a measured cap, a failed check raises and
             never reads as "none active", the place and the switch are read
             at every fetch, and its logs name events, never the place. Four
             fields no display read (headline, onset, sender, area) left the
             alert dict (the owner's call); Requires gained os and sys (the
             __main__ path).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from datetime import datetime
from typing import Any

# Iris reaches up to an app-root sibling (themis.py) for the place, the
# guarded-path pattern aura.py uses so a bare `python tools/iris.py` resolves it.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import themis
from tools import hermes

log = logging.getLogger("METIS.iris")

_URL = "https://api.weather.gov/alerts/active"
_HEADERS = {"User-Agent": "Felhaven weather dashboard",
            "Accept": "application/geo+json"}
_TIMEOUT = 10
# 11.9 KB was the largest single alert of 391 (2026-11-10); 512 KiB holds about
# forty of those for one point, more than any place has had at once.
_CAP = 512 * 1024
_DESCRIPTION_MAX = 2000         # characters kept for the panel; NWS text is long
_INSTRUCTION_MAX = 1000
_SUMMARY_MAX = 200
_OUT_OF_BOUNDS = "out of bounds"
_TIER_RANK = {"warning": 0, "watch": 1, "advisory": 2}
_WARNING_EVENTS = ("evacuation immediate", "civil emergency message")
_WHAT = re.compile(r"\*\s*WHAT\.\.\.(.+?)(?:\n\s*\n|\n\s*\*|\Z)", re.DOTALL)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")

_lock = threading.Lock()
_last_ids: frozenset[str] = frozenset()     # for logging only what changed
_last_covered: bool | None = None


class FeedError(ValueError):
    """The Weather Service answered, but not in the shape it documents."""


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _flat(text: str) -> str:
    """NWS text is hard-wrapped at about 70 columns; join it back up."""
    return " ".join(text.split())


def _tier(event: str, severity: str) -> str:
    name = event.strip().lower()
    if (name.endswith(("warning", "emergency")) or name in _WARNING_EVENTS
            or severity == "Extreme"):
        return "warning"
    if name.endswith("watch"):
        return "watch"
    return "advisory"


def _summary(description: str, headline: str) -> str:
    what = _WHAT.search(description)
    if what:
        text = what.group(1)
    else:
        first = description.strip().split("\n\n")[0]
        text = _SENTENCE_END.split(_flat(first), maxsplit=1)[0] if first else ""
    return _clip(_flat(text) or _flat(headline), _SUMMARY_MAX)


def _when(iso: str) -> float:
    """Seconds since the epoch, for sorting; an unreadable time sorts last."""
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _text(props: dict[str, Any], key: str) -> str:
    value = props.get(key)
    return value if isinstance(value, str) else ""


def _alert(props: dict[str, Any]) -> dict[str, Any] | None:
    alert_id, event = _text(props, "id"), _text(props, "event").strip()
    if not alert_id or not event:
        return None                     # nothing to name it by, or to acknowledge
    severity = _text(props, "severity")
    description = _text(props, "description").strip()
    headline = _text(props, "headline").strip()
    references = [str(ref["identifier"]) for ref in props.get("references") or []
                  if isinstance(ref, dict) and ref.get("identifier")]
    return {
        "id": alert_id,
        "event": event,
        "tier": _tier(event, severity),
        "severity": severity,
        "certainty": _text(props, "certainty"),
        "summary": _summary(description, headline),
        "description": _clip(description, _DESCRIPTION_MAX),
        "instruction": _clip(_text(props, "instruction").strip(), _INSTRUCTION_MAX),
        "sent": _text(props, "sent"),
        "until": _text(props, "ends") or _text(props, "expires"),
        "references": references,
    }


def _parse(body: Any) -> list[dict[str, Any]]:
    if not isinstance(body, dict) or not isinstance(body.get("features"), list):
        raise FeedError("no features list")
    alerts = []
    for feature in body["features"]:
        props = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(props, dict) or props.get("status") != "Actual":
            continue                    # a test or exercise message, or junk
        alert = _alert(props)
        if alert is None:
            log.warning("Iris: skipped an alert with no id or event")
            continue
        alerts.append(alert)
    alerts.sort(key=lambda a: (_TIER_RANK[a["tier"]], -_when(a["sent"])))
    return alerts


def _out_of_bounds(reply: hermes.Reply) -> bool:
    if reply.status_code != 400:
        return False
    try:
        detail = json.loads(reply.content).get("detail", "")
    except (ValueError, AttributeError):
        return False
    return isinstance(detail, str) and _OUT_OF_BOUNDS in detail


def _note(covered: bool, alerts: list[dict[str, Any]]) -> None:
    """Log what changed, not every poll: the worker runs once a minute."""
    global _last_ids, _last_covered
    ids = frozenset(a["id"] for a in alerts)
    with _lock:
        changed_cover = covered != _last_covered
        changed_ids = ids != _last_ids
        _last_covered, _last_ids = covered, ids
    if changed_cover and not covered:
        log.info("Iris: this place is outside the National Weather Service's area")
    if changed_ids:
        names = ", ".join(a["event"] for a in alerts) or "none"
        log.info("Iris: %d active (%s)", len(alerts), names)


def fetch_point(latitude: float, longitude: float) -> dict[str, Any]:
    """The alerts at one point. Raises as fetch() does."""
    url = f"{_URL}?point={latitude:.4f},{longitude:.4f}&status=actual"
    reply = hermes.get(url, cap=_CAP, timeout=_TIMEOUT, headers=_HEADERS)
    if _out_of_bounds(reply):
        _note(False, [])
        return {"enabled": True, "covered": False, "alerts": []}
    reply.raise_for_status()
    alerts = _parse(json.loads(reply.content))
    _note(True, alerts)
    return {"enabled": True, "covered": True, "alerts": alerts}


# ── Public API ────────────────────────────────────────────────────────────────

def fetch() -> dict[str, Any]:
    """Kairos entry point. The place and the switch are read from Themis now,
    so a Settings save takes effect on the next poll."""
    if not themis.alerts_enabled():
        return {"enabled": False, "covered": True, "alerts": []}
    return fetch_point(themis.latitude(), themis.longitude())


def _clear_state() -> None:
    """Forget what was last logged. For tests."""
    global _last_ids, _last_covered
    with _lock:
        _last_ids, _last_covered = frozenset(), None


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        if len(sys.argv) == 3:          # python tools/iris.py LAT LON
            data = fetch_point(float(sys.argv[1]), float(sys.argv[2]))
        else:
            data = fetch()
    except Exception as exc_main:
        print(f"[Iris] couldn't check ({type(exc_main).__name__})")
    else:
        if not data["enabled"]:
            print("[Iris] weather alerts are off (Settings › Alerts)")
        elif not data["covered"]:
            print("[Iris] outside the National Weather Service's area (US only)")
        elif not data["alerts"]:
            print("[Iris] checked: none active")
        for a in data["alerts"]:
            print(f"[Iris] {a['tier']:8} {a['event']} until {a['until']}: {a['summary']}")
