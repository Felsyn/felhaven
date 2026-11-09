"""
daedalus.py — The Artificer (Felhaven's map of itself)
======================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Map Felhaven's own modules, their docstrings and their
             imports, from the source.
             Named for the craftsman who built the Labyrinth and was the one
             man who could draw it. Every module becomes a node, every import
             between two of them an edge, and every node carries its module
             docstring exactly as `ast.get_docstring(tree, clean=True)` reads
             it. This is the only thing in Felhaven that reads source files to
             describe them.

Contract:    Request-driven brain tool (CONVENTIONS §2) plus plain functions
             for its panel:
               index()               -> {"revision", "nodes", "edges",
                                         "collisions"}
               docstring(name)       -> {"module", "path", "docstring"}
                                        or {"error": "unknown_module: ...",
                                            "did_you_mean": [...]}
               gloss(name)           -> the module's gloss (CONVENTIONS §0),
                                        or None
               source_path(name)     -> absolute path inside the app root,
                                        or None
               layout(width, height) -> {key: (x, y)}, deterministic
               handle(module)        -> the get_module_docstring tool
             `name` may be a key ("aura"), a filename ("tools/aura.py") or a
             sidebar/tab label ("Atmospherics"). Nothing here raises to a
             caller: handle() turns every failure into a §2 slug.

Never imports: Every file is read with `ast` and nothing else. Several modules
             start threads, open sockets or touch audio devices the moment
             they are imported, so importing one to read its `__doc__` would
             run all of that. tests/test_daedalus.py proves it with a fixture
             that would leave a sentinel behind if it ever ran.

Scope:       The app root, tools/ and panels/ — each read flat, never
             recursively, so .venv/, caches and data folders are never walked.
             The three __init__.py package markers are left out: nothing
             imports them by name. A module's key is its import name as
             Felhaven spells it (`aura`, `home_panel`, `pythia`); two files
             with one stem would collide, and the real-tree test fails red on
             that rather than letting this module guess a naming scheme.

Edges:       One edge per import that lands on another node, resolved the way
             Felhaven's sys.path resolves it (metis_toolbox/ on the path):
             `import x`, `from x import y`, `from tools import x`,
             `from tools.x import Y`, and the panels/ spellings of the same.
             Stdlib and third-party imports fall away because they name no
             node. `lazy` is true when every import of that target sits inside
             a function body. Kairos's workers are loaded from strings, not
             imports, so they are deliberately NOT edges — `emanon` has none.

Cache:       In memory only. Each public call stats the scope (~4 ms) and
             re-parses only the files whose (mtime, size) moved; a full build
             is ~0.2 s on this tree (77 files, 1.4 MB), measured 2026-09-25.
             Each tree is discarded as soon as it has been read, because
             holding them all nearly doubled the parse time.

The tool:    get_module_docstring(module) — the model picks WHICH module, from
             an enum built out of this index at import (so it regenerates on
             restart, never by hand). The docstring itself never enters the
             model's context: handle() returns it under "verbatim", and
             pythia.ask() hands that straight to the UI and gives the model
             only an acknowledgement (CONVENTIONS §12). The glosses in the
             enum's description are each module's Job: first sentence, the
             same gloss CONVENTIONS §0 defines.

Out of LLM scope: Opening a file, or starting any process, is UI-only. The
             DAEDALUS tab's Notepad button does that; no tool can, because a
             model that could launch programs on this machine would need a
             much better reason than "show me the code". The tool reads
             docstrings only — never source bodies, config or data — so it may
             describe Cerberus or Plutus freely: describing a module is not
             touching it.

Upstream:    pythia.py (registration + dispatch), panels/daedalus_panel.py
             (the graph and the docstring pane), tools/virgil.py (a tooltip's
             module gloss, through gloss()).
Downstream:  the .py files it reads; panels/sidebar.py and every TabHost
             `_build_tabs([...])` call, for display names — read as source,
             never imported.

Requires:    stdlib only — ast, difflib, logging, math, os, random, re, stat,
             threading.
"""

from __future__ import annotations

import ast
import copy
import difflib
import logging
import math
import os
import random
import re
import stat
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Optional

log = logging.getLogger("METIS.daedalus")

# ── Paths & scope ─────────────────────────────────────────────────────────────

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (group, folder relative to the app root). Each is listed flat, never walked.
_FOLDERS: tuple[tuple[str, str], ...] = (
    ("core", ""),
    ("tools", "tools"),
    ("panels", "panels"),
)
_GROUP_ORDER = {group: i for i, (group, _folder) in enumerate(_FOLDERS)}
# An import that starts with one of these names a FOLDER, and the module is
# the next part: `from tools import aura`, `import panels.sidebar`.
_PACKAGES = {folder: group for group, folder in _FOLDERS if folder}

# ── Tool surface knobs ────────────────────────────────────────────────────────
# Every name in the enum costs prefix tokens on every chat turn, so how much of
# the index the model sees is a measured choice (CONVENTIONS §12), not a given.
_ENUM_GROUPS: tuple[str, ...] = ("core", "tools", "panels")
_GLOSS_CHARS = 60              # a gloss longer than this is cut at a word

_NO_DOCSTRING = "(no module docstring)"
_SUGGESTIONS = 3

# ── Layout knobs ──────────────────────────────────────────────────────────────
_LAYOUT_SEED = 1729
_LAYOUT_ITERATIONS = 150
# Where each folder's nodes are pulled, in the unit square. Core sits between
# the tools it drives and the panels that draw them.
_ANCHORS = {"core": (0.5, 0.5), "tools": (0.18, 0.5), "panels": (0.82, 0.5)}
_GRAVITY = 0.2
_HUB_DAMPING = 1.0            # attraction divided by (max endpoint degree) ** this
_SPREAD = 1.0                 # multiplier on the ideal edge length

# The gloss: the `Job:` field's first sentence (CONVENTIONS §0). The same
# pattern tests/test_pantheon_docs.py holds the README_PANTHEON copies to.
_JOB_FIELD = re.compile(r"^Job:\s*(.+?)(?=^\w[\w ]*:|\Z)", re.MULTILINE | re.DOTALL)
_SENTENCE = re.compile(r"(.*?[.!?])(\s|$)")


# ── Per-file record ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Record:
    """What one file contributes to the index — everything the tree was read
    for, so the tree itself can be thrown away."""
    key: str
    path: str                                # relative to the app root, "/"-separated
    group: str
    stamp: tuple[int, int]                   # (mtime_ns, size) it was parsed at
    docstring: Optional[str]
    gloss: Optional[str]
    error: Optional[str]                     # set when the file could not be parsed
    error_line: Optional[int]
    imports: tuple[tuple[str, str, bool], ...]   # (group, name, lazy)
    labels: tuple[tuple[str, str], ...]          # (view/tab key, display label)


def _first_sentence(text: str) -> str:
    flat = " ".join(text.split())
    m = _SENTENCE.match(flat)
    return m.group(1) if m else flat


def _gloss(doc: Optional[str]) -> Optional[str]:
    """The Job: first sentence, or — for the older docstrings that have no
    Job: field — the docstring's first non-empty line."""
    if not doc:
        return None
    m = _JOB_FIELD.search(doc)
    if m:
        return _first_sentence(m.group(1))
    for line in doc.splitlines():
        if line.strip():
            return line.strip()
    return None


def _walk_statements(body: list[ast.stmt], lazy: bool = False
                     ) -> Iterator[tuple[ast.stmt, bool]]:
    """Every statement in a body, nested ones included, with whether it runs
    only when some function is called.

    Statements only — imports are statements, and never descending into
    expressions is what makes this ~10x cheaper than ast.walk here. Class
    bodies run at import, so they stay module-level; function bodies do not.
    """
    for stmt in body:
        yield stmt, lazy
        inner = lazy or isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
        for name in ("body", "orelse", "finalbody"):
            child = getattr(stmt, name, None)
            if isinstance(child, list):
                yield from _walk_statements(child, inner)
        for handler in getattr(stmt, "handlers", None) or []:
            yield from _walk_statements(handler.body, inner)
        for case in getattr(stmt, "cases", None) or []:
            yield from _walk_statements(case.body, inner)


def _import_targets(stmt: ast.stmt) -> Iterator[tuple[str, str]]:
    """(group, module name) for each thing an import statement could name.

    Only (group, name) pairs that match a node become edges, so `import os`
    yields ("core", "os") and simply never lands. Relative imports are skipped:
    Felhaven has none, and guessing their base would be worse than nothing.
    """
    if isinstance(stmt, ast.Import):
        for alias in stmt.names:
            parts = alias.name.split(".")
            if parts[0] in _PACKAGES:
                if len(parts) > 1:
                    yield _PACKAGES[parts[0]], parts[1]
            else:
                yield "core", parts[0]
    elif isinstance(stmt, ast.ImportFrom):
        if stmt.level or not stmt.module:
            return
        parts = stmt.module.split(".")
        if parts[0] in _PACKAGES:
            if len(parts) > 1:
                yield _PACKAGES[parts[0]], parts[1]
            else:
                for alias in stmt.names:
                    yield _PACKAGES[parts[0]], alias.name
        else:
            yield "core", parts[0]


def _literal(node: Optional[ast.expr]) -> Any:
    """ast.literal_eval that answers None instead of raising."""
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None


def _sidebar_labels(tree: ast.Module) -> Iterator[tuple[str, str]]:
    """(view key, label) from class Sidebar's _ROWS and _GROUPS literals."""
    for cls in tree.body:
        if not (isinstance(cls, ast.ClassDef) and cls.name == "Sidebar"):
            continue
        for stmt in cls.body:
            if not (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)):
                continue
            value = _literal(stmt.value)
            rows: list[Any] = []
            if stmt.targets[0].id == "_ROWS" and isinstance(value, list):
                rows = value
            elif stmt.targets[0].id == "_GROUPS" and isinstance(value, dict):
                rows = [row for group in value.values()
                        if isinstance(group, list) for row in group]
            for row in rows:
                if (isinstance(row, tuple) and len(row) >= 2
                        and isinstance(row[0], str) and isinstance(row[1], str)):
                    yield row[0], row[1]


def _tab_labels(stmt: ast.stmt) -> Iterator[tuple[str, str]]:
    """(tab key, label) from a `self._build_tabs([(key, label, ...), ...])`
    statement — the TabHost shape every tab host uses."""
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return
    call = stmt.value
    if not (isinstance(call.func, ast.Attribute) and call.func.attr == "_build_tabs"
            and call.args and isinstance(call.args[0], (ast.List, ast.Tuple))):
        return
    for elt in call.args[0].elts:
        if not (isinstance(elt, ast.Tuple) and len(elt.elts) >= 2):
            continue
        key, label = elt.elts[0], elt.elts[1]
        if (isinstance(key, ast.Constant) and isinstance(key.value, str)
                and isinstance(label, ast.Constant) and isinstance(label.value, str)):
            yield key.value, label.value


def _parse_file(full: str, rel: str, group: str, stamp: tuple[int, int]) -> _Record:
    """Read one file with ast and keep only what the index needs.

    A file that will not parse is still a node — with its error, no docstring
    and no edges — so one broken module never costs the whole map.
    """
    key = os.path.splitext(os.path.basename(rel))[0]
    try:
        with open(full, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=rel)
    except SyntaxError as exc:
        log.warning("Daedalus: %s does not parse (line %s) — indexed without "
                    "a docstring or edges", rel, exc.lineno)
        return _Record(key, rel, group, stamp, None, None,
                       "syntax_error", exc.lineno, (), ())
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        log.warning("Daedalus: could not read %s (%s)", rel, type(exc).__name__)
        return _Record(key, rel, group, stamp, None, None,
                       "unreadable", None, (), ())

    doc = ast.get_docstring(tree, clean=True)
    imports: list[tuple[str, str, bool]] = []
    labels: list[tuple[str, str]] = list(_sidebar_labels(tree))
    for stmt, lazy in _walk_statements(tree.body):
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            imports.extend((g, name, lazy) for g, name in _import_targets(stmt))
        else:
            labels.extend(_tab_labels(stmt))
    return _Record(key, rel, group, stamp, doc, _gloss(doc), None, None,
                   tuple(imports), tuple(labels))


# ── The index ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _View:
    """One revision of the index, published as a single reference so a reader
    on another thread can never see half of a rebuild."""
    root: str
    revision: int
    snapshot: dict[str, Any]
    by_key: dict[str, _Record]
    lookup: dict[str, str]                          # normalized label -> key


@dataclass
class _State:
    root: str = ""
    records: dict[str, _Record] | None = None       # rel path -> record
    revision: int = 0
    view: _View | None = None
    unit_layout: tuple[int, dict[str, tuple[float, float]]] | None = None


_lock = threading.Lock()
_state = _State()


def _scan(root: str) -> dict[str, tuple[str, tuple[int, int]]]:
    """rel path -> (group, (mtime_ns, size)) for every file in scope."""
    found: dict[str, tuple[str, tuple[int, int]]] = {}
    for group, folder in _FOLDERS:
        base = os.path.join(root, folder) if folder else root
        try:
            names = os.listdir(base)
        except OSError as exc:
            log.warning("Daedalus: cannot list %s (%s)", folder or ".",
                        type(exc).__name__)
            continue
        for name in sorted(names):
            if not name.endswith(".py") or name == "__init__.py":
                continue
            try:
                st = os.stat(os.path.join(base, name))
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                rel = f"{folder}/{name}" if folder else name
                found[rel] = (group, (st.st_mtime_ns, st.st_size))
    return found


def _normalize(name: str) -> str:
    """Forgive the spellings a model or a person is likely to use for a key:
    case, a .py suffix, a folder prefix, spaces or hyphens for underscores."""
    text = name.strip().casefold().replace("\\", "/")
    for prefix in ("metis_toolbox/",) + tuple(f"{f}/" for f in _PACKAGES) \
            + tuple(f"{f}." for f in _PACKAGES):
        if text.startswith(prefix):
            text = text[len(prefix):]
    if text.endswith(".py"):
        text = text[:-3]
    return re.sub(r"[\s\-]+", "_", text)


def _build(records: dict[str, _Record], revision: int
           ) -> tuple[dict[str, Any], dict[str, _Record], dict[str, str]]:
    """The public snapshot, plus the two private lookups behind resolution."""
    ordered = sorted(records.values(),
                     key=lambda r: (_GROUP_ORDER.get(r.group, 99), r.path))
    by_key: dict[str, _Record] = {}
    collisions: dict[str, list[str]] = {}
    for rec in ordered:
        if rec.key in by_key:
            collisions.setdefault(rec.key, [by_key[rec.key].path]).append(rec.path)
            log.warning("Daedalus: %s and %s share the key %r — keeping the first",
                        by_key[rec.key].path, rec.path, rec.key)
            continue
        by_key[rec.key] = rec

    # Display names: a view or tab key names the node with that key, or else
    # its _panel. Labels that only restate the key add nothing and are dropped.
    display: dict[str, list[str]] = {key: [] for key in by_key}
    for rec in ordered:
        for view, label in rec.labels:
            target = view if view in by_key else f"{view}_panel"
            if target not in by_key:
                continue
            shown = label.title() if label.isupper() else label
            said = _normalize(shown)
            if said in (target, target.removesuffix("_panel")):
                continue
            if shown not in display[target]:
                display[target].append(shown)

    lookup: dict[str, str] = {}
    for key, names in display.items():
        for shown in names:
            lookup.setdefault(_normalize(shown), key)

    edges: dict[tuple[str, str], bool] = {}
    for rec in ordered:
        if by_key.get(rec.key) is not rec:
            continue
        for group, name, lazy in rec.imports:
            landed = by_key.get(name)
            if landed is None or landed.group != group or name == rec.key:
                continue
            pair = (rec.key, name)
            edges[pair] = edges.get(pair, True) and lazy     # module-level wins

    nodes = {
        rec.key: {
            "key": rec.key,
            "path": rec.path,
            "group": rec.group,
            "gloss": rec.gloss,
            "display": display[rec.key],
            "has_docstring": rec.docstring is not None,
            "parse_error": rec.error,
            "error_line": rec.error_line,
        }
        for rec in by_key.values()
    }
    snapshot = {
        "revision": revision,
        "nodes": nodes,
        "edges": [{"source": s, "target": t, "lazy": lazy}
                  for (s, t), lazy in sorted(edges.items())],
        "collisions": collisions,
    }
    return snapshot, by_key, lookup


def _refresh() -> _View:
    """Bring the index up to date and return the current view. Parses only
    what moved; a call with nothing changed costs one stat per file."""
    with _lock:
        root = _APP_ROOT
        if _state.root != root or _state.records is None:
            _state.root = root
            _state.records = {}
            _state.view = None
        records = _state.records
        found = _scan(root)
        changed = False
        for rel in list(records):
            if rel not in found:
                del records[rel]
                changed = True
        for rel, (group, st) in found.items():
            rec = records.get(rel)
            if rec is None or rec.stamp != st or rec.group != group:
                records[rel] = _parse_file(os.path.join(root, *rel.split("/")),
                                           rel, group, st)
                changed = True
        if changed or _state.view is None:
            _state.revision += 1
            snapshot, by_key, lookup = _build(records, _state.revision)
            _state.view = _View(root, _state.revision, snapshot, by_key, lookup)
        return _state.view


def _resolve(view: _View, name: Any) -> Optional[str]:
    """A key for `name`, or None: exact key, then a forgiving spelling, then a
    sidebar/tab label."""
    if not isinstance(name, str) or not name.strip():
        return None
    if name in view.by_key:
        return name
    norm = _normalize(name)
    if norm in view.by_key:
        return norm
    return view.lookup.get(norm)


def _suggest(view: _View, name: Any) -> list[str]:
    """Up to _SUGGESTIONS keys that `name` might have meant."""
    if not isinstance(name, str):
        return []
    choices = {key: key for key in view.by_key}
    choices.update(view.lookup)
    out: list[str] = []
    for hit in difflib.get_close_matches(_normalize(name), list(choices),
                                         n=10, cutoff=0.6):
        key = choices[hit]
        if key not in out:
            out.append(key)
    return out[:_SUGGESTIONS]


# ── Public API (the panel's) ──────────────────────────────────────────────────

def index() -> dict[str, Any]:
    """Every node and edge, for drawing. A copy — mutate it freely."""
    return copy.deepcopy(_refresh().snapshot)


def docstring(name: str) -> dict[str, Any]:
    """{"module", "path", "docstring"} for one module. "docstring" is exactly
    what ast.get_docstring(tree, clean=True) returned — None when the module
    has none, which is an answer, not an error."""
    view = _refresh()
    key = _resolve(view, name)
    if key is None:
        return {"error": f"unknown_module: {name!r}",
                "did_you_mean": _suggest(view, name)}
    rec = view.by_key[key]
    if rec.error is not None:
        where = f" line {rec.error_line}" if rec.error_line else ""
        return {"error": f"parse_failed: {rec.path}{where}",
                "module": key, "path": rec.path}
    return {"module": key, "path": rec.path, "docstring": rec.docstring}


def gloss(name: str) -> Optional[str]:
    """One module's gloss, exactly as index() carries it in its node: the
    Job: first sentence (CONVENTIONS §0). None for an unknown name, a file
    that will not parse, or a module with no docstring. `name` resolves as it
    does for docstring().

    A tooltip asks this on every hover, so it answers from the current view
    and never deep-copies the whole index the way index() does."""
    view = _refresh()
    key = _resolve(view, name)
    if key is None:
        return None
    return view.by_key[key].gloss


def source_path(name: str) -> Optional[str]:
    """The absolute path of one module's file — only ever inside the app root,
    so nothing that takes a path from here can be walked out of it."""
    view = _refresh()
    key = _resolve(view, name)
    if key is None:
        return None
    root = os.path.realpath(view.root)
    full = os.path.realpath(os.path.join(root, *view.by_key[key].path.split("/")))
    try:
        inside = os.path.commonpath([root, full]) == root
    except ValueError:                  # different drives on Windows
        inside = False
    return full if inside and os.path.isfile(full) else None


def _unit_layout(snapshot: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Positions in the unit square — a seeded force-directed pass.

    Fruchterman–Reingold, plus a pull toward each folder's anchor so the map
    reads as core / tools / panels rather than a hairball, and attraction
    damped by degree so a hub like theme.py (imported by ~30 modules) cannot
    drag the whole graph onto itself. Deterministic: sorted input, a fixed
    seed, no clock.
    """
    nodes = snapshot["nodes"]
    keys = sorted(nodes, key=lambda k: (_GROUP_ORDER.get(nodes[k]["group"], 99), k))
    n = len(keys)
    if n == 0:
        return {}
    if n == 1:
        return {keys[0]: (0.5, 0.5)}
    at = {key: i for i, key in enumerate(keys)}
    links = [(at[e["source"]], at[e["target"]]) for e in snapshot["edges"]]
    degree = [0] * n
    for a, b in links:
        degree[a] += 1
        degree[b] += 1
    anchors = [_ANCHORS.get(nodes[key]["group"], (0.5, 0.5)) for key in keys]

    rnd = random.Random(_LAYOUT_SEED)
    xs = [ax + (rnd.random() - 0.5) * 0.3 for ax, _ay in anchors]
    ys = [ay + (rnd.random() - 0.5) * 0.6 for _ax, ay in anchors]
    k = _SPREAD * math.sqrt(1.0 / n)
    k2 = k * k
    temp = 0.08
    cool = temp / (_LAYOUT_ITERATIONS + 1)
    for _ in range(_LAYOUT_ITERATIONS):
        dx = [0.0] * n
        dy = [0.0] * n
        for i in range(n):
            xi, yi = xs[i], ys[i]
            for j in range(i + 1, n):
                ddx = xi - xs[j]
                ddy = yi - ys[j]
                d2 = ddx * ddx + ddy * ddy
                if d2 < 1e-10:
                    ddx, ddy, d2 = 1e-5 * (i - j), 1e-5, 1e-10
                f = k2 / d2
                dx[i] += ddx * f
                dy[i] += ddy * f
                dx[j] -= ddx * f
                dy[j] -= ddy * f
        for a, b in links:
            ddx = xs[a] - xs[b]
            ddy = ys[a] - ys[b]
            d = math.sqrt(ddx * ddx + ddy * ddy)
            f = d / k / max(degree[a], degree[b]) ** _HUB_DAMPING
            dx[a] -= ddx * f
            dy[a] -= ddy * f
            dx[b] += ddx * f
            dy[b] += ddy * f
        for i in range(n):
            gx = dx[i] + (anchors[i][0] - xs[i]) * _GRAVITY / k
            gy = dy[i] + (anchors[i][1] - ys[i]) * _GRAVITY / k
            d = math.sqrt(gx * gx + gy * gy)
            if d > 0:
                step = min(d, temp)
                xs[i] += gx / d * step
                ys[i] += gy / d * step
        temp -= cool

    lo_x, hi_x, lo_y, hi_y = min(xs), max(xs), min(ys), max(ys)
    span_x, span_y = (hi_x - lo_x) or 1.0, (hi_y - lo_y) or 1.0
    return {key: ((xs[i] - lo_x) / span_x, (ys[i] - lo_y) / span_y)
            for i, key in enumerate(keys)}


def layout(width: float, height: float) -> dict[str, tuple[float, float]]:
    """Node positions for a width x height canvas.

    The force pass runs once per index revision (~0.2 s, measured 2026-09-25)
    and is cached in unit coordinates; a resize only rescales them. Same
    index, same map — nodes stay put between visits.
    """
    view = _refresh()
    cached = _state.unit_layout
    if cached is None or cached[0] != view.revision:
        # Computed outside the lock: a chat turn's handle() must not wait
        # ~0.2 s on a drawing. Two racing computes agree, so either may land.
        cached = (view.revision, _unit_layout(view.snapshot))
        _state.unit_layout = cached
    unit = cached[1]
    mx = max(24.0, width * 0.09)          # labels hang half their width past a dot
    my = max(14.0, height * 0.07)
    w = max(1.0, width - 2 * mx)
    h = max(1.0, height - 2 * my)
    return {key: (mx + x * w, my + y * h) for key, (x, y) in unit.items()}


# ── LLM tool contract ─────────────────────────────────────────────────────────

def _short(text: str) -> str:
    """A gloss cut to _GLOSS_CHARS at a word boundary."""
    if len(text) <= _GLOSS_CHARS:
        return text
    cut = text[:_GLOSS_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:—-")
    return cut + "…"


def _tool_definition() -> dict[str, Any]:
    """The schema, built from the index so the enum can never drift from the
    tree. Never raises — this runs at import, inside pythia's import."""
    try:
        nodes = index().get("nodes", {})
    except Exception as exc:  # noqa: BLE001 — an import-time raise kills Felhaven
        log.error("Daedalus: could not build the module index (%s)",
                  type(exc).__name__)
        nodes = {}
    keys = sorted((k for k, v in nodes.items() if v["group"] in _ENUM_GROUPS),
                  key=lambda k: (_GROUP_ORDER.get(nodes[k]["group"], 99), k))
    if _GLOSS_CHARS > 0:
        lines = []
        for key in keys:
            node = nodes[key]
            line = f"{key} — {_short(node['gloss'] or 'no docstring')}"
            if node["display"]:
                line += f" ({', '.join(node['display'])})"
            lines.append(line)
        about = ("Which Felhaven module to show, by key. Each line is "
                 "key — what it does (its name in the sidebar):\n" + "\n".join(lines))
    else:
        # Keys only: the enum already lists them, so the description carries
        # just the sidebar names a user might say instead.
        named = [f"{', '.join(nodes[k]['display'])} = {k}"
                 for k in keys if nodes[k]["display"]]
        about = ("Which Felhaven module to show, by key. Sidebar names: "
                 + "; ".join(named) + ".")
    module: dict[str, Any] = {"type": "string", "description": about}
    # One legal value carries no information, and none is not a schema.
    if len(keys) >= 2:
        module["enum"] = keys
    return {
        "type": "function",
        "function": {
            "name": "get_module_docstring",
            "description": (
                "Shows the user one of Felhaven's own modules' documentation "
                "(its module docstring), word for word. Call this when the user "
                "asks what a Felhaven module, panel or tool does, how it works, "
                "or what it is for — questions about Felhaven's own code, not "
                "live data. The docstring is shown to the user directly, so do "
                "not restate or summarize it."
            ),
            "parameters": {
                "type": "object",
                "properties": {"module": module},
                "required": ["module"],
            },
        },
    }


def handle(module: str) -> dict[str, Any]:
    """Toolbox entry: one module's docstring, marked for VERBATIM delivery.

    The text rides under "verbatim"; pythia.ask() gives it to the UI and keeps
    it out of the model's context (CONVENTIONS §12). "ack" is the one line
    that ends the turn in its place. Never raises."""
    try:
        found = docstring(module)
        if "error" in found:
            return found
        key, path, text = found["module"], found["path"], found["docstring"]
        spoken = key.replace("_", " ")
        return {
            "module": key,
            "path": path,
            "verbatim": {
                "label": "daedalus",
                "title": f"{key} · {path}",
                "text": text if text is not None else _NO_DOCSTRING,
                "ack": (f"Here is the docstring for {spoken}." if text is not None
                        else f"{spoken} has no module docstring."),
            },
        }
    except Exception as exc:  # noqa: BLE001 — a tool must never raise (§2)
        log.error("Daedalus: handle(%r) failed: %s", module, type(exc).__name__)
        return {"error": f"daedalus_failed: {type(exc).__name__}"}


TOOL_DEFINITION: dict[str, Any] = _tool_definition()


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import time as _time
    t0 = _time.perf_counter()
    snap = index()
    built = (_time.perf_counter() - t0) * 1000
    lazy_edges = sum(1 for e in snap["edges"] if e["lazy"])
    print(f"[Daedalus] {len(snap['nodes'])} modules, {len(snap['edges'])} imports "
          f"({lazy_edges} lazy), rev {snap['revision']}, {built:.0f} ms")
    print(f"[Daedalus] enum: {len(TOOL_DEFINITION['function']['parameters']['properties']['module'].get('enum', []))} keys")
    print(handle("Atmospherics")["verbatim"]["title"])
