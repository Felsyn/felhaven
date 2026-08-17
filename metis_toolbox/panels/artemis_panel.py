"""
Artemis panel — the Cartograph: terrain, water and roads around the fix.

A full Card view (the Navigation rail group holds full panels, never tabs —
CONVENTIONS §7). Kairos polls tools.artemis.fetch() every 600 s and feeds
update(); the panel re-renders only when the fix's area or its cache
changes, so the tick is cheap and update(None) keeps the last drawn state.

Header status line, all derived locally (cache presence + last ingest
outcome — no connectivity probe, per the house attempt-and-catch rule):
    NOT CACHED            no manifest for the current fix's area
    CACHED <date>         manifest present; date from its "fetched" field,
                          plus a note when terrain was skipped
    INGESTING — <step>    transient, from the worker queue
    INGEST FAILED — <..>  transient, slug mapped to calm text, red

WORLD vs CHROME. Every canvas item carries one of two family tags, and that
split is what makes the rest of this panel simple:

    world   terrain raster, water, roads, range rings, the fix crosshair —
            anchored to (cx, cy) from _view(), clipped by the porthole,
            and the only things a drag moves.
    chrome  the mask, the porthole rim, bearing ticks, N/E/S/W, the compass
            rose, the scale bar — anchored to the canvas and never moved.

The porthole is a fixed instrument you look THROUGH; the world moves under
it. That split is also how the palette divides: the INSTRUMENT — range
rings, ticks, the rim and its range label, the cardinals, the fix crosshair
— draws in phosphor (C["text1"]), while the WORLD draws in the muted map
hues. Brightness therefore tells you what kind of thing you are looking at,
not just how important it is. (The compass rose and scale bar stay muted:
they sit in their own corners and measure nothing on the chart itself.)

Draw order, back to front: terrain → area water → linear water → roads →
range rings → fix crosshair → mask → rim → ticks → cardinals → compass →
scale bar. Rings and the crosshair sit UNDER the mask because they are
world-anchored — once panned they must be clipped by the glass rather than
float past it — while the rim and ticks sit over it because they are the
instrument.

A square is fetched but a CIRCLE is displayed: a range display should be
round. The mask is eight plain shapes — four rectangles outside the ring's
bounding box and four corner wedges between the box and the arc — rather
than one self-intersecting keyhole polygon, because a simple polygon fills
identically under either winding rule and cannot surprise us on a Tk we
have not tested.

ZOOM is a crop factor, not a Tk zoom. Because the raster is resampled to
the canvas (see §12, 2026-08-09), zooming means sampling a smaller centred
part of the cached raster to the same canvas span, and the vectors take
their scale from the same number. Cost is constant per redraw at any stop,
where PhotoImage.zoom(4) would have built a 31 MB intermediate. The stops
only magnify: the cached area is a hard edge on how much world exists, and
at 1x the whole ingest square is already visible. The porthole itself keeps
the SAME pixel radius at every stop (_porthole_r), so zooming reads as
looking closer through one instrument rather than the frame growing.

PANNING. Drag the chart at 2x or 4x and the `world` items move under the
stationary porthole — one canvas.move() per event, never a raster rebuild,
which is what makes a drag smooth when a full redraw costs ~0.2 s. The pan
is held in world metres and enters only _view(), so vectors, rings, the fix
mark and the hit test all follow it without a second code path. It clamps to
DISPLAY_RADIUS_M * (1 - 1/zoom), which is exactly zero at 1x — everything is
already on screen there, so off-centre would be arbitrary — and it resets on
Home, on search, and whenever the displayed area changes.

HIT TESTING happens in geometry space through the cached spatial index, not
via find_closest(): one <Motion> binding on the canvas (never tag_bind per
feature), cursor pixels back-projected to local metres, ~9 index cells of
candidates, exact point-to-segment distance, nearest within tolerance.
Priority is road → water → terrain. A press that travels less than
_CLICK_PX is still a click and still reads, so the chart is usable without a
mouse, and the reading is STICKY: clearing it on exit would blank the line at
the moment you look down to read it.

The PhotoImage reference is held on the panel: Tk garbage-collects an image
no Python name points at, and the canvas silently goes blank.

INGEST THREADING — the Echo pattern (CONVENTIONS §6 documented exception):
request-driven, so no Kairos tick to drain on. One daemon thread runs
artemis.ingest_area(); its ONLY shared touch is q.put() — it never touches
a Tk object. A bounded self.after() chain drains the queue, stops itself on
done/error, is winfo_exists()-guarded, and is cancelled on destroy. An
epoch counter discards stale messages from a cancelled run (home_panel
precedent). A threading.Event cancels between layers; a cancelled or failed
ingest leaves no partial directory (tools-side guarantee).

CACHE AREA is a two-click confirm (the Morpheus remove idiom): first click
arms the label with the download cost, a one-shot after() disarms it in 4 s,
second click starts the ingest. Never a native messagebox. Never automatic —
searching or moving the fix must not trigger a multi-megabyte download.

CACHED LOCATIONS is an overlay pinned above the compass rose, listing every
cached area with its range and bearing FROM THE FIX (which of them you are
actually near is the useful reading, not which the view happens to show).
Each row renames (✎), re-ingests (⟳) or recycles (✕) its area, and clicking
the name jumps the view there — view-only, exactly like search. It is a
child of the canvas positioned with place(), never canvas.create_window():
a canvas window is a canvas ITEM and _redraw() opens with delete("all"),
which would destroy the overlay on every zoom, resize and area load.
Both ⟳ and ✕ are two-click armed, and every handler captures the area_id
rather than a row index, so a tick or a finished ingest re-rendering between
the arming click and the confirming one cannot retarget the row.
"""

import math
import queue
import threading
import tkinter as tk
from typing import Any, Optional

import cerberus
from theme import C, FONTS, Card

from tools import artemis
from tools import demeter
from tools import eratosthenes
from tools import hecate
from tools import poseidon

_DRAIN_MS = 150
_CONFIRM_MS = 4000        # armed CACHE AREA disarms itself after this
_RESIZE_MS = 120          # <Configure> debounce before a rescale+redraw
_PAN_MS = 140             # pause during a drag before the raster is re-cropped

# How far a press may travel and still count as a click. Deliberately NOT
# _HOVER_PX: that is how precisely a hand can POINT, this is how far one slips
# while CLICKING, which is smaller. 4 px is the Windows system drag threshold
# (SM_CXDRAG), so a click here means what it means everywhere else on the box.
_CLICK_PX = 4

# Hover tolerance, in PIXELS, converted to metres at the current scale. The
# spec asks for ~40 m; at the shipped display scale that is under two pixels,
# which reads as a broken hover. The test itself still happens in geometry
# space — which is what the spec's "not pixels" was guarding — and only the
# tolerance's size is set by how precisely a hand can point (§12).
_HOVER_PX = 10

_FEET_PER_M = 3.280839895

# Slug prefix (before any ": detail") -> calm human text.
_ERROR_TEXT = {
    "cancelled": "ingest cancelled",
    "download_failed": "download failed — check the connection and retry",
    "bad_county": "a county file came down malformed — retry",
    "bad_reference": "the county index came down malformed — retry",
    "reference_missing": "county index missing — retry",
    "no_counties": "no counties cover this fix (outside the US?)",
    "write_failed": "could not write the cache — check disk space",
    "ingest_failed": "ingest failed — check the log",
}

# Search slugs from eratosthenes.resolve().
_SEARCH_TEXT = {
    "empty_query": "type coordinates, a waypoint name, or an address",
    "no_results": "no match — try coordinates or a waypoint name",
    "geocode_failed": "address lookup unavailable — coordinates and waypoints "
                      "still work offline",
}

# Why terrain is absent from an otherwise good area (manifest terrain_skipped).
_SKIPPED_TEXT = {
    demeter.ERR_VAULT_LOCKED: "no terrain — vault sealed at ingest",
    demeter.ERR_NO_KEY: "no terrain — no OpenTopography key",
    "download_failed": "no terrain — elevation download failed",
    "bad_grid": "no terrain — elevation data was malformed",
    "no_elevation": "no terrain — no elevation coverage here",
}

# A refresh that could not fetch elevation keeps what the area already had
# (manifest terrain_kept). The map is complete; only the terrain is older.
_KEPT_TEXT = {
    demeter.ERR_VAULT_LOCKED: "terrain kept — vault was sealed",
    demeter.ERR_NO_KEY: "terrain kept — no OpenTopography key",
}

# Road tier -> (theme colour key, stroke width).
_ROAD_STYLE = {
    1: ("map_road_hi", 4),
    2: ("map_road_mid", 3),
    3: ("map_road_lo", 1),
    4: ("map_road_track", 1),
}
_WATER_STYLE = {1: ("map_water", 2), 2: ("map_water_minor", 1)}

# What the status strip calls a feature. TIGER's MTFCC is interpreted into a
# tier at ingest and only the tier is cached, so these read off the tier.
_ROAD_CLASS = {1: "primary road", 2: "secondary road", 3: "local road",
               4: "path or alley"}
_WATER_CLASS = {1: "stream", 2: "ditch or drainage"}
_AREA_CLASS = {1: "lake or pond", 2: "wetland"}

_COMPASS_POINTS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


class ArtemisPanel(Card):
    """Local chart around the Themis fix; ingest on explicit request only."""

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, "Artemis — Cartograph", C["amber"])

        # _fix_status is the Themis fix's area (what Kairos polls); _status is
        # whatever area is on screen. They differ while a search is showing.
        self._fix_status: "dict[str, Any] | None" = None
        self._status: "dict[str, Any] | None" = None
        self._following = True                         # tracking the Themis fix
        self._view_label = ""                          # searched place's name
        self._drawn: "tuple[str, Any] | None" = None   # (area_id, ingested_at)
        self._layers: dict[int, list[artemis.Feature]] = {}
        self._index: "artemis.Index | None" = None
        self._terrain_ppm: "bytes | None" = None       # cached GRID² source
        self._terrain_info: "dict[str, Any] | None" = None
        self._terrain_cells: Any = None                # array('h'), for hover
        self._photo: "tk.PhotoImage | None" = None     # MUST stay referenced
        self._photo_key: "tuple[Any, ...] | None" = None
        self._skipped: "str | None" = None
        self._kept: "str | None" = None
        self._zoom = 1

        # Pan: the world point (metres east/north of the area centre) currently
        # under the porthole centre. Drag state follows PhosphorScroll's
        # one-attribute-is-flag-and-payload idiom, widened because a pan needs
        # an origin in two spaces at once.
        self._pan_e = 0.0
        self._pan_n = 0.0
        self._drag_from: "tuple[int, int, float, float] | None" = None
        self._drag_moved = False
        self._pan_after_id: Optional[str] = None

        # Several row glyphs can be armed at once, so the overlay needs the
        # SET shape (_after_once) alongside the panel's single-slot ids —
        # CONVENTIONS §6 documents both, and both are now in use here.
        self._pending_after: "set[str]" = set()
        self._areas_key: Any = None
        self._areas_open = True
        self._renaming: "str | None" = None

        self._q: "queue.Queue[tuple[str, Any, int]]" = queue.Queue()
        self._ingesting = False
        self._epoch = 0
        self._cancel = threading.Event()
        self._drain_after_id: Optional[str] = None
        self._confirm_after_id: Optional[str] = None
        self._resize_after_id: Optional[str] = None
        self._confirm_armed = False

        # ── Status row ───────────────────────────────────────────────────────
        row = tk.Frame(self.body, bg=C["card"])
        row.pack(fill="x", pady=(4, 6))
        self._status_lbl = tk.Label(row, text="waiting for the first tick…",
                                    font=FONTS["small"], fg=C["text3"],
                                    bg=C["card"], anchor="w")
        self._status_lbl.pack(side="left", fill="x", expand=True)

        self._cancel_btn = tk.Label(row, text="CANCEL", font=FONTS["small"],
                                    fg=C["red"], bg=C["card"], padx=8)
        # packed only while an ingest runs

        self._cache_btn = tk.Label(row, text="CACHE AREA", font=FONTS["small"],
                                   fg=C["text3"], bg=C["card"], padx=8)
        self._cache_btn.pack(side="right")
        self._cache_btn.bind("<Button-1>", lambda e: self._on_cache_click())

        self._zoom_btns: dict[int, tk.Label] = {}
        for stop in reversed(artemis.ZOOM_STOPS):
            btn = tk.Label(row, text=f"{stop}×", font=FONTS["small"],
                           fg=C["text3"], bg=C["card"], padx=5, cursor="hand2")
            btn.pack(side="right")
            btn.bind("<Button-1>", lambda e, s=stop: self._set_zoom(s))
            self._zoom_btns[stop] = btn
        self._refresh_zoom_buttons()

        # ── Search row ───────────────────────────────────────────────────────
        search = tk.Frame(self.body, bg=C["card"])
        search.pack(fill="x", pady=(0, 6))
        tk.Label(search, text="GO TO", font=FONTS["tiny"], fg=C["text3"],
                 bg=C["card"], anchor="w").pack(side="left", padx=(0, 6))

        self._home_btn = tk.Label(search, text="⌂ HOME", font=FONTS["small"],
                                  fg=C["text3"], bg=C["card"], padx=8,
                                  cursor="hand2")
        self._home_btn.pack(side="right")
        self._home_btn.bind("<Button-1>", lambda e: self._go_home())

        self._search_var = tk.StringVar()
        entry = tk.Entry(search, textvariable=self._search_var,
                         font=FONTS["small"], bg=C["bar_bg"], fg=C["text1"],
                         insertbackground=C["text1"], highlightthickness=1,
                         highlightbackground=C["border"], bd=0)
        entry.pack(fill="x", expand=True, side="left")
        entry.bind("<Return>", lambda e: self._on_search())

        # ── Canvas ───────────────────────────────────────────────────────────
        self._canvas = tk.Canvas(self.body, bg=C["bg"], highlightthickness=1,
                                 highlightbackground=C["border"])
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<Configure>", self._on_configure)
        # Bound ONCE on the canvas, never per feature. A press that does not
        # travel is still a click and still reads, so the chart works without
        # a pointing device; a press that travels pans instead.
        self._canvas.bind("<Motion>", self._on_pointer)
        self._canvas.bind("<Button-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)

        # ── Cached Locations overlay ─────────────────────────────────────────
        # A child of the canvas positioned with place(), NOT canvas
        # .create_window(): a canvas window is a canvas ITEM, and _redraw()
        # opens with canvas.delete("all"), so the overlay would be destroyed on
        # every zoom, resize and area load. place() sits outside the item list
        # entirely. rely=1.0 with a negative y pins it above the compass rose
        # and tracks resizes without a <Configure> handler of its own.
        self._areas_box = tk.Frame(self._canvas, bg=C["card"],
                                   highlightthickness=1,
                                   highlightbackground=C["border"])
        self._areas_header = tk.Label(self._areas_box, font=FONTS["tiny"],
                                      fg=C["text3"], bg=C["card"], anchor="w",
                                      padx=6, cursor="hand2")
        self._areas_header.pack(fill="x")
        self._areas_header.bind("<Button-1>", lambda e: self._toggle_areas())
        self._areas_body = tk.Frame(self._areas_box, bg=C["card"])
        self._areas_body.pack(fill="x")

        # ── Reading strip (sticky) ───────────────────────────────────────────
        self._reading = tk.Label(self.body, text="", font=FONTS["small"],
                                 fg=C["text1"], bg=C["card"], anchor="w")
        self._reading.pack(fill="x", pady=(6, 0))

        # ── Footer ───────────────────────────────────────────────────────────
        tk.Label(self.body,
                 text="TIGER/Line 2025 · USGS 3DEP · orientation aid, "
                      "not survey-grade",
                 font=FONTS["tiny"], fg=C["text3"], bg=C["card"],
                 anchor="w").pack(fill="x", pady=(2, 0))

        self._refresh_controls()

    # ── Kairos entry point ──────────────────────────────────────────────────

    def update(self, data: "dict[str, Any] | None") -> None:  # type: ignore[override]
        """Cheap and idempotent: reload/redraw only when the displayed area or
        its cache changed. None (a failed poll) keeps the last drawn state.

        While a search is on screen the tick still records the fix's status —
        it just doesn't take the view back, or typing a place would be undone
        by the next poll.
        """
        if data is None:
            return
        self._fix_status = data
        if self._following:
            self._apply_status(data)

    def _apply_status(self, status: "dict[str, Any]") -> None:
        """Point the panel at whichever area `status` describes."""
        self._status = status
        if status["cached"]:
            key = (status["area_id"], status["ingested_at"])
            if key != self._drawn:
                self._load_area(status["area_id"], status["ingested_at"])
        else:
            # Nothing cached here: show the placeholder rather than leaving
            # someone else's map on screen under a new place name.
            self._clear_layers()
            self._redraw()
        self._refresh_areas()
        self._refresh_controls()

    # ── Search: moves the VIEW only (§12) ───────────────────────────────────

    def _on_search(self) -> None:
        """Resolve the box and pan the view there.

        This never writes through to Themis. The fix feeds weather, the star
        map and the season as well as this chart, so looking at a map must not
        silently relocate the rest of the dashboard; ⌂ HOME comes straight
        back. Caching the searched area stays a separate, explicit click.
        """
        result = eratosthenes.resolve(self._search_var.get())
        if "error" in result:
            slug = str(result["error"]).split(":", 1)[0]
            self._set_progress(
                f"SEARCH — {_SEARCH_TEXT.get(slug, result['error'])}",
                error=slug != "empty_query")
            return
        self._following = False
        self._view_label = str(result["label"])
        self._reset_pan()
        self._apply_status(artemis.area_status(result["lat"], result["lon"]))

    def _go_home(self) -> None:
        self._following = True
        self._view_label = ""
        self._search_var.set("")
        self._reset_pan()
        if self._fix_status is not None:
            self._apply_status(self._fix_status)

    def _clear_layers(self) -> None:
        # The honest choke point for resetting the pan: it is reached only when
        # the displayed content actually changes. Doing it in _apply_status()
        # would snap a pan set ten minutes ago on the next 600 s tick.
        self._reset_pan()
        self._drawn = None
        self._layers = {}
        self._index = None
        self._terrain_ppm = None
        self._terrain_info = None
        self._terrain_cells = None
        self._photo = None
        self._photo_key = None
        self._skipped = None
        self._kept = None
        self._reading.config(text="")

    def _load_area(self, area: str, ingested_at: Any) -> None:
        """Load whatever layers the manifest says landed. A layer that fails
        to read is dropped, not fatal — a readable road file should still
        draw when the water file is damaged."""
        self._clear_layers()
        try:
            manifest = artemis.read_manifest(area)
        except (OSError, ValueError):
            manifest = {}
        layers = manifest.get("layers", [])
        self._skipped = manifest.get("terrain_skipped")
        self._kept = manifest.get("terrain_kept")

        if "roads" in layers:
            roads = self._try(hecate.load, area)
            if roads is not None:
                self._layers[artemis.LAYER_ROADS] = roads
        if "water" in layers:
            water = self._try(poseidon.load, area)
            if water is not None:
                self._layers[artemis.LAYER_WATER] = water
        if "terrain" in layers:
            self._terrain_ppm = self._try(demeter.raster, area)
            grid = self._try(demeter.load, area)
            if grid is not None:
                self._terrain_info, self._terrain_cells = grid
        if self._layers:
            self._index = self._try(artemis.ensure_index, area, self._layers)
        if self._layers or self._terrain_ppm:
            self._drawn = (area, ingested_at)
        self._redraw()

    @staticmethod
    def _try(loader: Any, *args: Any) -> Any:
        try:
            return loader(*args)
        except (OSError, ValueError):
            return None

    # ── Geometry shared by drawing and hit testing ──────────────────────────

    def _canvas_size(self) -> tuple[int, int]:
        w, h = self._canvas.winfo_width(), self._canvas.winfo_height()
        if w <= 1 or h <= 1:
            # Before the first <Configure> (or under a withdrawn test root)
            # the canvas reports 1×1; fall back to the requested size.
            w, h = self._canvas.winfo_reqwidth(), self._canvas.winfo_reqheight()
        return w, h

    def _view(self) -> tuple[int, int, float, float, float]:
        """(w, h, world-origin x, world-origin y, pixels per metre).

        The PAN enters here and nowhere else on the vector side: every draw
        and the hit test back-project through this same cx/cy, so a panned
        chart and a panned hit test cannot disagree. The porthole is NOT here
        — it stays at the canvas centre at every pan and zoom (_porthole_r).
        """
        w, h = self._canvas_size()
        span = min(w, h)
        view_half_m = artemis.HALF_WIDTH_M / self._zoom
        scale = span / (2.0 * view_half_m)
        return (w, h, w / 2.0 - self._pan_e * scale,
                h / 2.0 + self._pan_n * scale, scale)

    def _pan_limit(self) -> float:
        """Per-axis pan limit in metres.

        Deriving it from DISPLAY_RADIUS_M rather than HALF_WIDTH_M does two
        jobs with one number: it collapses to exactly zero at 1x (the whole
        5-mile disc is already on screen, so there is nowhere to pan to), and
        it keeps the porthole inside the 5-mile disc the chart claims to
        cover. The furthest east ever shown is limit + DISPLAY_RADIUS_M/zoom
        == DISPLAY_RADIUS_M = 8047 m, comfortably inside the cached square's
        8852 m, so there is always data under the glass.
        """
        return artemis.DISPLAY_RADIUS_M * (1.0 - 1.0 / self._zoom)

    def _clamp_pan(self) -> None:
        limit = self._pan_limit()
        self._pan_e = min(limit, max(-limit, self._pan_e))
        self._pan_n = min(limit, max(-limit, self._pan_n))

    def _reset_pan(self) -> None:
        self._pan_e = 0.0
        self._pan_n = 0.0

    # ── Rendering ───────────────────────────────────────────────────────────

    def _on_configure(self, _event: "tk.Event[Any]") -> None:
        """Debounced: a drag-resize fires <Configure> continuously, and each
        redraw rebuilds the raster at the new size."""
        if self._resize_after_id is not None:
            self.after_cancel(self._resize_after_id)
        self._resize_after_id = self.after(_RESIZE_MS, self._on_resize_settled)

    def _on_resize_settled(self) -> None:
        self._resize_after_id = None
        if self.winfo_exists():
            self._redraw()

    def _set_zoom(self, stop: int) -> None:
        if stop == self._zoom:
            return
        self._zoom = stop
        self._clamp_pan()          # zooming out to 1x collapses the pan to 0
        self._refresh_zoom_buttons()
        self._redraw()

    # ── Panning ─────────────────────────────────────────────────────────────

    def _on_press(self, event: "tk.Event[Any]") -> None:
        """Record the gesture's origin in both spaces. Deliberately produces
        no reading — that waits for the release, which is what tells a click
        apart from the start of a drag."""
        self._drag_from = (event.x, event.y, self._pan_e, self._pan_n)
        self._drag_moved = False
        if self._pan_limit() > 0.0:
            # Only promise a drag where there is somewhere to drag to.
            self._canvas.config(cursor="fleur")

    def _on_drag(self, event: "tk.Event[Any]") -> None:
        """Pan by moving the `world` items — one Tcl call, no raster rebuild.

        The pan is recomputed ABSOLUTELY from the press anchor each event and
        the canvas moved by the difference, which keeps the handler correct
        across a redraw landing mid-gesture: after a rebuild the items already
        sit at the current pan, so the next delta still applies cleanly.
        """
        if self._drag_from is None:                  # the PhosphorScroll guard
            return
        x0, y0, pan_e, pan_n = self._drag_from
        dx, dy = event.x - x0, event.y - y0
        if not self._drag_moved:
            if max(abs(dx), abs(dy)) <= _CLICK_PX:
                return                               # still a click
            self._drag_moved = True
        _w, _h, _cx, _cy, scale = self._view()
        if scale <= 0:
            return
        was_e, was_n = self._pan_e, self._pan_n
        self._pan_e = pan_e - dx / scale             # drag right -> look west
        self._pan_n = pan_n + dy / scale             # drag down  -> look north
        self._clamp_pan()
        # Move by the CLAMPED delta or the items drift out of step with the
        # pan state at the edge of travel.
        self._canvas.move("world", (was_e - self._pan_e) * scale,
                          (self._pan_n - was_n) * scale)
        # The raster reaches only ~4.5% of the span beyond the glass, so a
        # drag of more than ~37 px would show background inside the porthole.
        # Re-crop on a pause; a continuous drag never fires this.
        if self._pan_after_id is not None:
            self.after_cancel(self._pan_after_id)
        self._pan_after_id = self.after(_PAN_MS, self._on_pan_settled)

    def _on_pan_settled(self) -> None:
        self._pan_after_id = None
        if self.winfo_exists():
            self._redraw()

    def _on_release(self, event: "tk.Event[Any]") -> None:
        moved = self._drag_moved
        self._drag_from = None
        self._drag_moved = False
        self._canvas.config(cursor="")
        if not moved:
            self._on_pointer(event)      # a click still reports what is under it
            return
        if self._pan_after_id is not None:
            self.after_cancel(self._pan_after_id)
            self._pan_after_id = None
        self._redraw()

    def _refresh_zoom_buttons(self) -> None:
        for stop, btn in self._zoom_btns.items():
            btn.config(fg=C["text1"] if stop == self._zoom else C["text3"])

    def _redraw(self) -> None:
        canvas = self._canvas
        canvas.delete("all")
        w, h, cx, cy, scale = self._view()
        if w < 40 or h < 40:
            return
        if not (self._layers or self._terrain_ppm):
            canvas.create_text(w // 2, h // 2, fill=C["text3"],
                               font=FONTS["small"], justify="center",
                               text=self._placeholder_text())
            return

        # The porthole never moves: it is the instrument, not the world.
        px, py = w / 2.0, h / 2.0
        r = self._porthole_r(scale)

        if self._terrain_ppm is not None:
            self._draw_terrain(min(w, h), px, py)

        water = self._layers.get(artemis.LAYER_WATER, ())
        # Area water first so the linear network draws over its own lakes.
        for feat in water:
            if feat["kind"] != "water_area":
                continue
            canvas.create_polygon(*self._flatten(feat, cx, cy, scale),
                                  fill=C["map_water_area"], outline="",
                                  tags=("world", "water", "water_area"))
        for tier in (2, 1):
            key, width = _WATER_STYLE[tier]
            for feat in water:
                if feat["kind"] != "water_line" or feat["tier"] != tier:
                    continue
                canvas.create_line(*self._flatten(feat, cx, cy, scale),
                                   fill=C[key], width=width,
                                   tags=("world", "water", "water_line"))
        # Heaviest road tier drawn last so it lands on top.
        for tier in (4, 3, 2, 1):
            key, width = _ROAD_STYLE[tier]
            for feat in self._layers.get(artemis.LAYER_ROADS, ()):
                if feat["tier"] != tier:
                    continue
                canvas.create_line(
                    *self._flatten(feat, cx, cy, scale), fill=C[key],
                    width=(1 if feat["flags"] & artemis.FLAG_THIN else width),
                    dash=((4, 3) if feat["flags"] & artemis.FLAG_DASHED else None),
                    tags=("world", "road"))

        self._draw_rings_world(cx, cy, px, py, r, scale)

        # Centre fix: text-green crosshair + dot (spec palette row). World, not
        # chrome — once panned it can leave the porthole, and a crosshair on the
        # black outside the glass would be a lie about where you are.
        canvas.create_line(cx - 7, cy, cx + 7, cy, fill=C["text1"],
                           tags=("world", "fix"))
        canvas.create_line(cx, cy - 7, cx, cy + 7, fill=C["text1"],
                           tags=("world", "fix"))
        canvas.create_oval(cx - 1, cy - 1, cx + 1, cy + 1, fill=C["text1"],
                           outline="", tags=("world", "fix"))

        self._draw_mask(w, h, px, py, r)
        self._draw_rim(px, py, r, scale)
        self._draw_compass(h)
        self._draw_scale_bar(w, h, scale)

    def _placeholder_text(self) -> str:
        """"Nothing cached here" is a normal condition, not an error, so it
        gets a useful body: what IS cached, nearest first, off the manifests.
        Being told the map you want is absent while sitting next to a list of
        the maps you have is the difference between useless and navigable."""
        where = self._view_label or "this fix"
        lines = [f"no map cached for {where}"]
        if self._status is not None:
            nearest = artemis.nearest_cached(self._status["lat"],
                                             self._status["lon"])
            if nearest:
                lines.append("")
                lines.append("nearest cached areas")
                for item in nearest:
                    centre = item["center"]
                    lines.append(
                        f"{centre[0]:.3f} {centre[1]:.3f}"
                        f"     {item['distance_mi']:.0f} mi"
                        f"  {item['bearing']:03d}°")
        lines.append("")
        lines.append("CACHE AREA downloads terrain, water and roads "
                     "for a 5-mile ring")
        return "\n".join(lines)

    @staticmethod
    def _flatten(feat: artemis.Feature, cx: float, cy: float,
                 scale: float) -> list[float]:
        """Local metres -> flat canvas coordinates (north is up, so y flips)."""
        flat: list[float] = []
        for east, north in feat["coords"]:
            flat.append(cx + east * scale)
            flat.append(cy - north * scale)
        return flat

    def _draw_terrain(self, span: int, px: float, py: float) -> None:
        """Sample the cached raster to the canvas size and place it at the
        PORTHOLE centre — the pan rides in the crop offset, not the position,
        so the image always fills the glass. The PhotoImage is kept on self —
        an unreferenced one is collected and the canvas goes blank."""
        frac = (self._pan_e / (2.0 * artemis.HALF_WIDTH_M),
                -self._pan_n / (2.0 * artemis.HALF_WIDTH_M))
        key = (span, self._zoom, round(frac[0], 5), round(frac[1], 5))
        if self._photo is None or self._photo_key != key:
            assert self._terrain_ppm is not None
            try:
                scaled = _resample_ppm(self._terrain_ppm, span, self._zoom,
                                       frac)
                # RAW BYTES, never base64 — this Tk rejects the base64 form
                # (tests/test_artemis_ppm_spike.py).
                self._photo = tk.PhotoImage(data=scaled)
                self._photo_key = key
            except (ValueError, tk.TclError):
                self._terrain_ppm = None       # draw the vectors regardless
                self._photo = None
                return
        self._canvas.create_image(px, py, image=self._photo,
                                  tags=("world", "terrain"))

    def _porthole_r(self, scale: float) -> float:
        """Radius of the on-screen porthole, in pixels.

        FIXED apparent size: at 1x it is exactly the 5-mile ring, and every
        zoom stop clamps to that same pixel radius, so the round frame never
        changes size — only the world inside it does. Since
        `scale == zoom * span / (2 * HALF_WIDTH_M)`, `scale / zoom` is exactly
        the 1x scale, which makes this `0.4546 * span` at every stop. The
        min() is written out rather than folded away because ZOOM_STOPS only
        magnifies today; a zoom-OUT stop must shrink the porthole to the ring,
        not overflow the canvas.
        """
        return artemis.DISPLAY_RADIUS_M * min(scale, scale / self._zoom)

    def _draw_mask(self, w: int, h: int, px: float, py: float,
                   r: float) -> None:
        """Hide everything outside the porthole: four rectangles plus four
        corner wedges (see the module docstring for why not a keyhole).

        Takes the porthole rather than the scale, and never early-returns —
        `_porthole_r` is always 0.4546 * span, hence always inside the canvas,
        so the old "nothing to hide when zoomed in" branch was unreachable.
        """
        bg = C["bg"]
        canvas = self._canvas
        for x0, y0, x1, y1 in ((0, 0, w, py - r), (0, py + r, w, h),
                               (0, py - r, px - r, py + r),
                               (px + r, py - r, w, py + r)):
            if x1 > x0 and y1 > y0:
                canvas.create_rectangle(x0, y0, x1, y1, fill=bg, outline="",
                                        tags=("chrome", "mask"))
        steps = 24
        for quad in range(4):
            start = quad * (math.pi / 2.0)
            corner = (px + r * (1 if quad in (0, 3) else -1),
                      py + r * (1 if quad in (0, 1) else -1))
            pts = [corner]
            for i in range(steps + 1):
                a = start + (math.pi / 2.0) * i / steps
                pts.append((px + r * math.cos(a), py + r * math.sin(a)))
            canvas.create_polygon(*[v for pt in pts for v in pt],
                                  fill=bg, outline="", tags=("chrome", "mask"))

    def _draw_rings_world(self, cx: float, cy: float, px: float, py: float,
                          r: float, scale: float) -> None:
        """Range rings, centred on the FIX and drawn before the mask.

        A range ring means "this far from the fix", so it is world-anchored:
        it slides with a pan and must be clipped by the porthole rather than
        floating past it, which drawing it under the mask gives for free. Only
        rings that fit inside the glass are drawn; the outermost is replaced by
        the rim (`_draw_rim`), which always sits exactly at the edge.
        """
        canvas = self._canvas
        for radius_m in artemis.RING_RADII_M:
            rr = radius_m * scale
            # Within a pixel or two of the rim IS the rim: DISPLAY_RADIUS_M is
            # 8047 m while the 5-mile ring is 8046.72 m, so a bare `rr >= r`
            # test would draw both, 0.28 m apart, as a doubled hairline.
            if rr >= r - 2.0:
                continue                 # outside the glass; the rim covers it
            canvas.create_oval(cx - rr, cy - rr, cx + rr, cy + rr,
                               outline=C["text1"], tags=("world", "ring"))
            label_x = cx + rr * math.sin(math.radians(45))
            label_y = cy - rr * math.cos(math.radians(45))
            # A label the mask bites in half reads as a rendering fault, so
            # skip one that lands too near the rim rather than let it be eaten.
            if math.hypot(label_x - px, label_y - py) > r - 8:
                continue
            canvas.create_text(label_x, label_y,
                               text=f"{radius_m / artemis.M_PER_MILE:.0f} mi",
                               fill=C["text1"], font=FONTS["tiny"],
                               tags=("world", "ring"))

    def _draw_rim(self, px: float, py: float, r: float, scale: float) -> None:
        """The porthole edge, its range label, bearing ticks and the cardinal
        letters — the instrument, drawn over the mask so it is never eaten.

        The rim label carries the radius currently VISIBLE (5 / 2.5 / 1.25 mi),
        so zooming in still answers "how far can I see" instead of dropping the
        only distance reference on the chart.
        """
        canvas = self._canvas
        canvas.create_oval(px - r, py - r, px + r, py + r,
                           outline=C["text1"], tags=("chrome", "ring", "rim"))
        miles = (r / scale) / artemis.M_PER_MILE
        canvas.create_text(px + r * math.sin(math.radians(45)),
                           py - r * math.cos(math.radians(45)),
                           text=f"{miles:.4g} mi", fill=C["text1"],
                           font=FONTS["tiny"], tags=("chrome", "ring", "rim"))
        for deg in range(0, 360, 30):
            a = math.radians(deg)
            sin_a, cos_a = math.sin(a), math.cos(a)
            inner = r * (0.95 if deg % 90 else 0.92)
            canvas.create_line(px + inner * sin_a, py - inner * cos_a,
                               px + r * sin_a, py - r * cos_a,
                               fill=C["text1"], tags=("chrome", "ring"))
        for deg, label in ((0, "N"), (90, "E"), (180, "S"), (270, "W")):
            a = math.radians(deg)
            rr = r * 0.87
            canvas.create_text(px + rr * math.sin(a), py - rr * math.cos(a),
                               text=label, fill=C["text1"],
                               font=FONTS["small_bold"] if deg == 0
                               else FONTS["tiny"], tags=("chrome", "ring"))

    def _draw_compass(self, h: int) -> None:
        """A small 16-point rose, bottom-left. Fixed size — it is chrome, not
        part of the projection."""
        canvas = self._canvas
        cx, cy, r = 42.0, h - 42.0, 22.0
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                           outline=C["map_compass"], tags=("chrome", "compass"))
        for i in range(16):
            a = math.radians(i * 22.5)
            length = r * (0.55 if i % 4 == 0 else (0.75 if i % 2 == 0 else 0.86))
            canvas.create_line(cx + length * math.sin(a), cy - length * math.cos(a),
                               cx + r * math.sin(a), cy - r * math.cos(a),
                               fill=C["map_compass"], tags=("chrome", "compass"))
        canvas.create_polygon(cx, cy - r * 0.62, cx - 4, cy + 3, cx, cy,
                              cx + 4, cy + 3, fill=C["map_compass"],
                              outline="", tags=("chrome", "compass"))
        canvas.create_text(cx, cy - r - 8, text=_COMPASS_POINTS[0],
                           fill=C["map_compass"], font=FONTS["tiny"],
                           tags=("chrome", "compass"))

    def _draw_scale_bar(self, w: int, h: int, scale: float) -> None:
        """Bottom-right, miles primary and metres secondary."""
        canvas = self._canvas
        budget_px = min(160.0, w * 0.25)
        miles = artemis.nice_scale_miles(budget_px / scale / artemis.M_PER_MILE)
        length_px = miles * artemis.M_PER_MILE * scale
        x1, y = w - 18.0, h - 26.0
        x0 = x1 - length_px
        canvas.create_line(x0, y, x1, y, fill=C["map_compass"], tags=("chrome", "scale"))
        for x in (x0, x1):
            canvas.create_line(x, y - 4, x, y + 4, fill=C["map_compass"],
                               tags=("chrome", "scale"))
        label = f"{miles:g} mi" if miles >= 1 else f"{miles:g} mi"
        canvas.create_text((x0 + x1) / 2.0, y - 10, text=label,
                           fill=C["map_compass"], font=FONTS["tiny"],
                           tags=("chrome", "scale"))
        canvas.create_text((x0 + x1) / 2.0, y + 11,
                           text=f"{int(round(miles * artemis.M_PER_MILE))} m",
                           fill=C["map_ring"], font=FONTS["tiny"],
                           tags=("chrome", "scale"))

    # ── Hit testing and the reading strip ───────────────────────────────────

    def _on_pointer(self, event: "tk.Event[Any]") -> None:
        """One handler for <Motion> and <Button-1>. Constant work per event:
        back-project, look up ~9 index cells, measure the candidates."""
        if not (self._layers or self._terrain_cells is not None):
            return
        _w, _h, cx, cy, scale = self._view()
        if scale <= 0:
            return
        east = (self._canvas.canvasx(event.x) - cx) / scale
        north = (cy - self._canvas.canvasy(event.y)) / scale
        if max(abs(east), abs(north)) > artemis.HALF_WIDTH_M:
            return                        # outside the cached square entirely
        reading = self._reading_for(east, north, _HOVER_PX / scale)
        if reading:                       # sticky: no hit leaves the last one
            self._reading.config(text=reading)

    def _reading_for(self, east: float, north: float,
                     tolerance: float) -> str:
        bearing = artemis.bearing_of(east, north)
        miles = math.hypot(east, north) / artemis.M_PER_MILE
        where = f"bearing {bearing:03d}°  ·  {miles:.2f} mi from fix"

        if self._index is not None:
            hit = artemis.query(self._index, self._layers, east, north,
                                tolerance)
            if hit is not None:
                layer_id, fi, _dist = hit
                feat = self._layers[layer_id][fi]
                return f"{self._describe(feat)}  ·  {where}"

        if self._terrain_info is not None and self._terrain_cells is not None:
            metres = demeter.sample(self._terrain_info, self._terrain_cells,
                                    east, north)
            if metres is not None:
                feet = int(round(metres * _FEET_PER_M))
                return f"elev {feet} ft  ·  {where}"
        return ""

    @staticmethod
    def _describe(feat: artemis.Feature) -> str:
        kind, tier = feat["kind"], feat["tier"]
        if kind == "road":
            klass = _ROAD_CLASS.get(tier, "road")
            if feat["flags"] & artemis.FLAG_DASHED:
                klass = "trail or private road"
            name = feat["name"] or "unnamed road"
        elif kind == "water_area":
            klass = _AREA_CLASS.get(tier, "water")
            name = feat["name"] or "unnamed water"
        else:
            klass = _WATER_CLASS.get(tier, "watercourse")
            name = feat["name"] or "unnamed watercourse"
        return f"{name}  ·  {klass}"

    # ── Cached Locations overlay ────────────────────────────────────────────

    def _after_once(self, ms: int, fn: "Any") -> None:
        """A one-shot timer that removes its own id when it fires, so
        destroy() can cancel whatever is still outstanding. Copied from
        MorpheusPanel: winfo_exists() guards are NOT a substitute (§6)."""
        def run() -> None:
            self._pending_after.discard(after_id)
            fn()
        after_id = self.after(ms, run)
        self._pending_after.add(after_id)

    def _toggle_areas(self) -> None:
        self._areas_open = not self._areas_open
        if self._areas_open:
            self._areas_body.pack(fill="x")
        else:
            self._areas_body.pack_forget()
        self._refresh_areas(force=True)

    def _refresh_areas(self, force: bool = False) -> None:
        """Rebuild the overlay from the cache.

        Range and bearing are measured from the THEMIS FIX, not the view
        centre — "which of my cached areas am I actually near" is the useful
        reading. Cheap and idempotent: the equality guard makes the 600 s tick
        a no-op, because rebuilding widgets under an armed delete glyph would
        silently disarm it mid-confirm.
        """
        status = self._fix_status or self._status
        if status is None:
            self._areas_box.place_forget()
            return
        rows = artemis.nearest_cached(status["lat"], status["lon"], limit=None)
        key = (round(float(status["lat"]), 3), round(float(status["lon"]), 3),
               self._areas_open,
               tuple((r["area_id"], r["label"], r["fetched"]) for r in rows))
        if not force and key == self._areas_key:
            return
        self._areas_key = key

        for child in self._areas_body.winfo_children():
            child.destroy()
        if not rows:
            self._areas_box.place_forget()
            return
        self._areas_header.config(
            text=f"{'▼' if self._areas_open else '▶'} CACHED AREAS ({len(rows)})")
        self._areas_box.place(x=10, rely=1.0, y=-76, anchor="sw")
        if self._areas_open:
            for row in rows:
                self._build_area_row(row)

    def _build_area_row(self, row: dict[str, Any]) -> None:
        area_id = str(row["area_id"])          # the KEY, never a row index: a
        centre = row["center"]                 # re-render between arming and
        label = str(row["label"])              # confirming must not retarget
        outer = tk.Frame(self._areas_body, bg=C["card"], padx=6, pady=3)
        outer.pack(fill="x")

        top = tk.Frame(outer, bg=C["card"])
        top.pack(fill="x")
        # Glyphs pack right FIRST or they land in the wrong place.
        for glyph_text in ("✕", "⟳", "✎"):
            glyph = tk.Label(top, text=glyph_text, font=FONTS["small"],
                             fg=C["text3"], bg=C["card"], padx=3,
                             cursor="hand2")
            glyph.pack(side="right")
            glyph._armed = False                              # type: ignore[attr-defined]
            glyph._rest = glyph_text                          # type: ignore[attr-defined]
            glyph.bind("<Enter>", lambda e, g=glyph: self._glyph_hover(g, C["text1"]))
            glyph.bind("<Leave>", lambda e, g=glyph: self._glyph_hover(g, C["text3"]))
            if glyph_text == "✎":
                glyph.bind("<Button-1>",
                           lambda e, o=top, a=area_id, l=label:
                           self._begin_rename(o, a, l))
            elif glyph_text == "⟳":
                glyph.bind("<Button-1>",
                           lambda e, g=glyph, c=centre: self._on_row_refresh(g, c))
            else:
                glyph.bind("<Button-1>",
                           lambda e, g=glyph, a=area_id: self._on_row_delete(g, a))

        name = tk.Label(top, text=label, font=FONTS["small"], fg=C["text1"],
                        bg=C["card"], anchor="w", cursor="hand2")
        name.pack(side="left", fill="x", expand=True)
        name.bind("<Button-1>",
                  lambda e, c=centre, l=label: self._on_area_click(c, l))
        name.bind("<Enter>", lambda e: name.config(fg=C["amber"]))
        name.bind("<Leave>", lambda e: name.config(fg=C["text1"]))
        top._name_label = name                                # type: ignore[attr-defined]

        tk.Label(outer,
                 text=f"{float(row['distance_mi']):.1f} mi  "
                      f"{int(row['bearing']):03d}°  ·  {str(row['fetched'])[:10]}",
                 font=FONTS["tiny"], fg=C["text3"], bg=C["card"],
                 anchor="w").pack(fill="x")
        tk.Frame(self._areas_body, bg=C["border"], height=1).pack(fill="x")

    def _on_row_delete(self, glyph: tk.Label, area_id: str) -> None:
        self._arm_glyph(glyph, "delete?", lambda: self._delete_area(area_id))

    def _on_row_refresh(self, glyph: tk.Label, centre: "list[float]") -> None:
        # Two-click armed like every other download trigger: §12's rule is
        # that nothing multi-megabyte starts on a single click.
        self._arm_glyph(glyph, "refresh?",
                        lambda: self._start_ingest(float(centre[0]),
                                                   float(centre[1])))

    def _glyph_hover(self, glyph: tk.Label, colour: str) -> None:
        # Suppressed while armed, or the amber prompt repaints itself away
        # under the mouse and the confirm looks like it never happened.
        if getattr(glyph, "_armed", False):
            return
        glyph.config(fg=colour)

    def _arm_glyph(self, glyph: tk.Label, prompt: str, action: "Any") -> None:
        if getattr(glyph, "_armed", False):
            glyph._armed = False                              # type: ignore[attr-defined]
            action()
            return
        glyph._armed = True                                   # type: ignore[attr-defined]
        glyph.config(text=prompt, fg=C["amber"])
        self._after_once(2500, lambda g=glyph: self._disarm_glyph(g))

    def _disarm_glyph(self, glyph: tk.Label) -> None:
        if glyph.winfo_exists() and getattr(glyph, "_armed", False):
            glyph._armed = False                              # type: ignore[attr-defined]
            glyph.config(text=getattr(glyph, "_rest", "✕"), fg=C["text3"])

    def _on_area_click(self, centre: "list[float]", label: str) -> None:
        """Jump the view. Same semantics as search — view-only, and it never
        writes themis.save(); ⌂ HOME still returns to the fix."""
        self._following = False
        self._view_label = label
        self._reset_pan()
        self._apply_status(artemis.area_status(float(centre[0]),
                                               float(centre[1])))

    # ── Rename (no precedent in the repo — this is the invention) ───────────

    def _begin_rename(self, top: tk.Frame, area_id: str, current: str) -> None:
        if self._renaming is not None:
            return
        name = getattr(top, "_name_label", None)
        if name is None or not name.winfo_exists():
            return
        self._renaming = area_id
        var = tk.StringVar(value=current)
        entry = tk.Entry(top, textvariable=var, font=FONTS["small"],
                         bg=C["bar_bg"], fg=C["text1"],
                         insertbackground=C["text1"], highlightthickness=1,
                         highlightbackground=C["border"], bd=0, width=16)
        name.pack_forget()
        entry.pack(side="left", fill="x", expand=True)
        entry.focus_set()
        entry.select_range(0, "end")
        entry.bind("<Return>",
                   lambda e: self._commit_rename(area_id, var.get()))
        # Escape and FocusOut CANCEL — they never write. FocusOut also fires
        # when the view switches or the panel is torn down, and a half-typed
        # name committed during teardown is the worst version of this feature.
        entry.bind("<Escape>", lambda e: self._cancel_rename(entry, name))
        entry.bind("<FocusOut>", lambda e: self._cancel_rename(entry, name))

    def _cancel_rename(self, entry: tk.Entry, name: tk.Label) -> None:
        if self._renaming is None:
            return
        self._renaming = None
        if entry.winfo_exists():
            entry.destroy()
        if name.winfo_exists():
            name.pack(side="left", fill="x", expand=True)

    def _commit_rename(self, area_id: str, text: str) -> None:
        self._renaming = None
        try:
            artemis.set_label(area_id, text)
        except (OSError, ValueError):
            self._set_progress("RENAME FAILED — check the log", error=True)
        self._refresh_areas(force=True)

    def _delete_area(self, area_id: str) -> None:
        if not artemis.delete_area(area_id):
            self._set_progress("DELETE FAILED — check the log", error=True)
            self._refresh_areas(force=True)
            return
        if self._status is not None and self._status["area_id"] == area_id:
            # The map on screen no longer exists on disk — don't leave it up.
            self._status["cached"] = False
            self._status["ingested_at"] = None
            self._apply_status(self._status)
        self._refresh_areas(force=True)

    # ── Status line + buttons ───────────────────────────────────────────────

    def _refresh_controls(self) -> None:
        if self._ingesting:
            self._cache_btn.config(fg=C["text3"], cursor="arrow")
            return
        if self._status is None:
            self._cache_btn.config(fg=C["text3"], cursor="arrow")
            self._status_lbl.config(text="waiting for the first tick…",
                                    fg=C["text3"])
            return
        # While a search is showing, name the place — otherwise "CACHED" would
        # look like it referred to the fix.
        where = f"  ·  {self._view_label}" if not self._following else ""
        self._home_btn.config(fg=C["text1"] if not self._following else C["text3"])
        if self._status["cached"]:
            fetched = str(self._status["ingested_at"] or "")[:10]
            text = f"CACHED {fetched}{where}"
            if self._skipped:
                text += "  ·  " + _SKIPPED_TEXT.get(
                    self._skipped, f"no terrain — {self._skipped}")
            elif self._kept:
                text += "  ·  " + _KEPT_TEXT.get(
                    self._kept, f"terrain kept — {self._kept}")
            self._status_lbl.config(text=text, fg=C["text2"])
        else:
            self._status_lbl.config(
                text=f"NOT CACHED — no map data here{where}", fg=C["text3"])
        if not self._confirm_armed:
            self._cache_btn.config(text="CACHE AREA", fg=C["text1"],
                                   cursor="hand2")

    def _set_progress(self, text: str, *, error: bool = False) -> None:
        self._status_lbl.config(text=text, fg=C["red"] if error else C["text2"])

    # ── CACHE AREA: two-click confirm (Morpheus idiom) ──────────────────────

    def _on_cache_click(self) -> None:
        if self._ingesting or self._status is None:
            return
        if not self._confirm_armed:
            self._confirm_armed = True
            # Say up front if terrain will be skipped — ingest still succeeds
            # with roads and water, but the user should not be surprised by a
            # flat map afterwards.
            cost = "~10–30 MB DOWNLOAD?"
            if not cerberus.is_unlocked():
                cost = "~10–30 MB, NO TERRAIN (VAULT SEALED)?"
            self._cache_btn.config(text=f"CONFIRM {cost}", fg=C["amber"])
            self._confirm_after_id = self.after(_CONFIRM_MS, self._disarm)
            return
        self._disarm()
        self._start_ingest(self._status["lat"], self._status["lon"])

    def _disarm(self) -> None:
        if self._confirm_after_id is not None:
            self.after_cancel(self._confirm_after_id)
            self._confirm_after_id = None
        self._confirm_armed = False
        if not self._ingesting:
            self._cache_btn.config(text="CACHE AREA", fg=C["text1"])

    # ── Ingest worker (Echo pattern) ────────────────────────────────────────

    def _start_ingest(self, lat: float, lon: float) -> None:
        """Cache the area centred on (lat, lon).

        The target is passed in rather than read off self._status because a
        row's refresh glyph ingests an area you are not looking at. One
        worker, one epoch, one drain chain serve both callers.
        """
        if self._ingesting:                              # single flight
            return
        self._ingesting = True
        self._epoch += 1
        self._cancel = threading.Event()
        self._set_progress("INGESTING — starting")
        self._cache_btn.config(fg=C["text3"], cursor="arrow")
        self._cancel_btn.pack(side="right")
        self._cancel_btn.bind("<Button-1>", lambda e: self._on_cancel())

        # Snapshots so the thread never reads mutable panel state.
        def _run(lat: float = lat, lon: float = lon, ep: int = self._epoch,
                 cxl: threading.Event = self._cancel,
                 q: "queue.Queue[tuple[str, Any, int]]" = self._q) -> None:
            try:
                area = artemis.ingest_area(
                    lat, lon, cxl, lambda msg: q.put(("progress", msg, ep)))
            except artemis.IngestError as exc:
                q.put(("error", str(exc), ep))
                return
            except Exception as exc:      # noqa: BLE001 — see below
                # The worker MUST put something on the queue on every path.
                # A thread that dies without one leaves the drain chain
                # polling an empty queue forever, so the panel sits on
                # "INGESTING —" with no error, no progress and no way out
                # short of restarting the app — a hang that looks exactly
                # like a slow download.
                q.put(("error", f"ingest_failed: {type(exc).__name__}", ep))
                return
            q.put(("done", area, ep))

        threading.Thread(target=_run, daemon=True).start()
        self._drain_after_id = self.after(_DRAIN_MS, self._drain)

    def _on_cancel(self) -> None:
        self._cancel.set()
        self._set_progress("INGESTING — cancelling…")

    def _drain(self) -> None:
        """Bounded, self-terminating queue poll (Echo/Emanon precedent)."""
        if not self.winfo_exists():
            return
        try:
            kind, payload, epoch = self._q.get_nowait()
        except queue.Empty:
            self._drain_after_id = self.after(_DRAIN_MS, self._drain)
            return
        if epoch != self._epoch:                 # stale message from an old run
            self._drain_after_id = self.after(_DRAIN_MS, self._drain)
            return
        if kind == "progress":
            self._set_progress(f"INGESTING — {payload}")
            self._drain_after_id = self.after(_DRAIN_MS, self._drain)
            return
        # done / error both end the run.
        self._drain_after_id = None
        self._finish_ingest(kind, payload)

    def _finish_ingest(self, kind: str, payload: str) -> None:
        self._ingesting = False
        self._cancel_btn.pack_forget()
        self._cancel_btn.unbind("<Button-1>")
        if kind == "done":
            manifest_date: Any = None
            try:
                manifest_date = artemis.read_manifest(payload).get("fetched")
            except (OSError, ValueError):
                pass
            # Guarded: a row's refresh can finish an area you are not looking
            # at, and that must not yank the view over to it.
            if self._status is not None and self._status["area_id"] == payload:
                self._status["cached"] = True
                self._status["ingested_at"] = manifest_date
                self._load_area(payload, manifest_date)
            self._refresh_areas(force=True)
            self._refresh_controls()
        else:
            slug = payload.split(":", 1)[0]
            text = _ERROR_TEXT.get(slug, f"error: {payload}")
            self._set_progress(f"INGEST FAILED — {text}",
                               error=slug != "cancelled")
            self._cache_btn.config(text="CACHE AREA", fg=C["text1"],
                                   cursor="hand2")

    # ── Housekeeping ────────────────────────────────────────────────────────

    def rescale(self, scale: float) -> None:
        """No fixed-width columns — fonts rescale globally and the canvas is
        fully elastic. Kept so felhaven._apply_scale has somewhere to land."""

    def destroy(self) -> None:
        """Cancel pending after() callbacks so nothing fires into a dead
        widget on app close (Echo precedent); the daemon thread, if any,
        dies with the process."""
        self._cancel.set()
        for pending in list(self._pending_after):
            self.after_cancel(pending)
        self._pending_after.clear()
        for after_id in (self._drain_after_id, self._confirm_after_id,
                         self._resize_after_id, self._pan_after_id):
            if after_id is not None:
                self.after_cancel(after_id)
        self._drain_after_id = None
        self._confirm_after_id = None
        self._resize_after_id = None
        self._pan_after_id = None
        super().destroy()


def _resample_ppm(ppm: bytes, size: int, zoom: int = 1,
                  pan: tuple[float, float] = (0.0, 0.0)) -> bytes:
    """Crop a 1/zoom square out of a square P6 PPM and nearest-neighbour it to
    size x size.

    Cropping BEFORE scaling is what keeps zoom affordable: the work is size²
    slice copies at every stop, where scaling first and cropping after would
    build a 4x-larger intermediate for 4x zoom (~31 MB, seconds of Python).
    Each output row is a byte-level gather from one source row.

    `pan` offsets the crop's centre as a fraction of the SOURCE width/height
    (x east, y south — row 0 is north), so the caller keeps its world units
    and this stays a pure byte function. The offset is clamped to the source,
    so a bad pan can only ever produce an edge view, never an out-of-range read.
    """
    if not ppm.startswith(b"P6"):
        raise ValueError("bad_ppm: not a P6 raster")
    fields: list[bytes] = []
    pos = 2
    while len(fields) < 3:                      # width, height, maxval
        while pos < len(ppm) and ppm[pos:pos + 1].isspace():
            pos += 1
        if ppm[pos:pos + 1] == b"#":            # comment line
            while pos < len(ppm) and ppm[pos:pos + 1] not in (b"\n", b""):
                pos += 1
            continue
        start = pos
        while pos < len(ppm) and not ppm[pos:pos + 1].isspace():
            pos += 1
        fields.append(ppm[start:pos])
    src_w, src_h = int(fields[0]), int(fields[1])
    body = ppm[pos + 1:]
    if src_w <= 0 or src_h <= 0 or len(body) < src_w * src_h * 3:
        raise ValueError("bad_ppm: truncated body")

    crop_w = max(1, src_w // zoom)
    crop_h = max(1, src_h // zoom)
    x0 = (src_w - crop_w) // 2 + int(round(pan[0] * src_w))
    y0 = (src_h - crop_h) // 2 + int(round(pan[1] * src_h))
    x0 = min(max(0, x0), src_w - crop_w)
    y0 = min(max(0, y0), src_h - crop_h)
    cols = [(x0 + (c * crop_w) // size) * 3 for c in range(size)]
    out = [b"P6\n%d %d\n255\n" % (size, size)]
    for r in range(size):
        base = (y0 + (r * crop_h) // size) * src_w * 3
        out.append(b"".join([body[base + o:base + o + 3] for o in cols]))
    return b"".join(out)
