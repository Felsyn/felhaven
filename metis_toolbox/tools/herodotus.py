"""
herodotus.py — The Father of History (Markdown archive)
========================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Manage a local archive of Markdown documents.
             Named for Herodotus of Halicarnassus, who gathered what was known,
             wrote it down, and kept it so "the deeds of men not be erased by
             time" — the first archivist (historical-figure naming per
             CONVENTIONS §12, the Zeno/Hypatia/Kepler/Callimachus precedent).

Contract:    MULTI-TOOL module (second after callimachus) — exports
             TOOL_DEFINITIONS (a list of three), TOOL_META, and three
             like-named handlers:
               • read_archive(action, query="",
                              filename="", offset=0)  -> one of the three reads below
               • write_document(filename, content,
                                source="")             -> {"filename", "size", "overwrote"}
               • edit_document(filename, operation,
                               target="", content="")  -> {"filename", "size", "operation"}
             read_archive's action picks a read, each still a public function:
               • list_documents()                      -> {"documents": [...]}
               • search_documents(query)               -> {"matches": [...]}
               • read_document(filename, offset=0)     -> {"filename", "content", "size",
                                                           "offset", "truncated"
                                                           [, "next_offset"]}
             Until 2026-11-14 those three were tools of their own; one read
             tool keeps the model's choice to read / write / edit (§12). The
             two writers stay separate tools on purpose: a change to a file is
             never one action among reads.
             Request-driven (the zeno/eudoxus row of CONVENTIONS §2): no
             fetch(), no Kairos worker, no panel. No handler ever raises; every
             failure is an {"error": ...} dict with a stable identifier first,
             detail second (the Midas/Callimachus pattern).

             This is the toolbox's FIRST MUTATING brain tool — write_document
             and edit_document change files on disk. That is deliberate and is
             the whole point: the LLM thinks, Herodotus acts, and knowledge the
             LLM gathers (e.g. a Callimachus fetch worth keeping) survives the
             session. The safety rails below are the price of admission.
             There is deliberately NO delete tool — the archive only grows or
             is edited; destruction stays a human act in a file manager. Do not
             "complete" the CRUD set later without a design conversation.

Archive:     One flat directory of UTF-8 .md files at <app root>/
             herodotus_archive/, anchored to __file__ per §1, created lazily on
             first write, gitignored (personal STATE, not config — §9). The
             word "vault" is never used for it: in this stack Vault means the
             Cerberus secrets store, and Herodotus keeps prose, not secrets.

Law:         The five rails, enforced on every operation:
               1. Hardcoded root — every path resolves inside _ARCHIVE_ROOT or
                  is refused (resolve-then-contain, belt and braces on top of
                  the filename allowlist). list and search skip an entry that
                  resolves elsewhere, a symlink out (search had read through
                  one, measured 2026-11-23), as read and edit refuse it. A hard
                  link is the file itself and can't be told apart; making
                  either needs write access to the archive folder, which is
                  outside what these rails defend.
               2. Bare .md filenames only — no separators, no traversal, no
                  dotfiles, no Windows reserved device names (CON, NUL, ...).
               3. UTF-8 only, both directions — a non-UTF-8 file reads as a
                  not_utf8 error, never mojibake.
               4. 1 MB cap per document, read and write.
               5. Atomic writes — tempfile in the same directory + os.replace,
                  so a crash mid-write can never leave a half-document.
             No shell, no subprocess, no network — pathlib, os and open() only.

Front matter: write_document prepends a YAML front-matter block (title /
             created / updated / source / tags) when the content doesn't
             already start with one; edit_document bumps the updated: line if a
             leading block has one. `source` is kept to one line: a newline in
             it closed the block early (measured 2026-11-23). Hand-rolled emit and line-scan — simple
             enough that a YAML dependency would be a §11 violation for no gain.

Not:         No web search (Callimachus), no encryption or PIN (Cerberus), no
             embeddings, no vector database, no LLM routing — search is plain
             case-insensitive substring, and that boringness is a feature.
             No subfolders — a flat archive keeps the filename law auditable.
             No delete — see Contract.

Upstream:    pythia.py (registration + dispatch — the sole registry consumer;
             the archive mutates only through Pythia's typed chat. A second,
             voice-side registry once existed in metis_toolbox/__init__.py but
             was retired with voice input, exactly like callimachus).
Downstream:  none. Herodotus knows nothing about the rest of the stack.

Requires:    stdlib only — datetime, logging, os, pathlib, re, tempfile, typing.

2026-07-24:  walkthrough audit — the four read/write/list error dicts stopped
             interpolating the caught OSError (its message carries the absolute
             archive path into the LLM context — §2); now only the class name
             travels, the path stays in the log. search_documents grew the same
             OSError guard list_documents already had, so its "never raises" is
             now whole. Regression tests added; off the output-contract allowlist.

2026-11-14:  list_documents, search_documents and read_document stop being
             tools and become read_archive's three actions (CONVENTIONS §12).
             The functions, their slugs and read_document's paging are
             unchanged; only what the model is shown is merged.

2026-11-16:  the Pythia tool audit (CONVENTIONS §12). read_document reads a
             long document in parts (T8: truncated, next_offset; the audit's
             H3); its three reads became read_archive's actions (2026-11-14,
             above); write_document and edit_document run only after the user
             says yes (Pythia's confirm gate, CONVENTIONS §8). All three
             descriptions follow §3's grammar. TOOL_META says archive / local,
             and the two writers change state. Its slugs use the shared
             no_results, not_found, bad_arguments and unknown_action (T9,
             2026-11-16); a failed listing or search joins read_failed.

V0.03:       Walked through 2026-11-23 (the V0.03 walkthrough, S32; born
             2026-07-07). Each rail was tried against the code. One document
             dated before 1970 or past year 9999 failed the whole listing
             (Windows can't convert its time), so a document now lists with
             "modified": "" instead; a document list_documents can't stat is
             skipped, as search skips one it can't read. search read a file
             outside the archive through a symlink; list and search now skip
             what resolves elsewhere. An infinite offset raised out of
             read_document, and a newline in `source` broke the front matter.
             Contract, Law and Requires caught up.
"""

import datetime
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger("METIS.herodotus")

# ── Config — module constants (aether/zeno/callimachus convention) ────────────

# The one directory Herodotus may touch. Anchored to __file__ (CONVENTIONS §1):
# tools/herodotus.py → app root is two dirname hops up. Tests patch this.
_ARCHIVE_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "herodotus_archive",
)

_MAX_DOC_BYTES     = 1_048_576   # 1 MB cap per document (Callimachus precedent)
# What ONE read_document hands the model — a separate budget from the storage
# cap above. The cap says what a file may hold; this says what fits in Pythia's
# context window. A whole 1 MB note is ~250k tokens; fetch_page's 4000-char
# `truncated` budget is the precedent. Characters, not bytes, so an offset
# never lands inside a multi-byte character.
_READ_CHARS        = 6000
_MAX_FILENAME_LEN  = 100         # generous; guards pathological tool calls
_SEARCH_MAX_FILES  = 10          # search reports at most this many documents
_SEARCH_MAX_LINES  = 3           # ... with at most this many snippet lines each
_SNIPPET_CHARS     = 200         # per snippet line

# Bare name, must start alphanumeric, then a conservative allowlist, .md suffix.
# No separators of any kind — traversal is impossible by construction; the
# resolve-then-contain check in _resolve() backstops it anyway. re.ASCII
# because IGNORECASE alone lets [A-Za-z] match four non-ASCII letters that
# case-fold to ASCII ones (U+0130, U+0131, U+017F, U+212A).
_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]*\.md$",
                          re.IGNORECASE | re.ASCII)

# Windows reserves these device names regardless of extension ("CON.md" can
# still misbehave on Win10). Flash-drive portability (§11) says refuse them.
_RESERVED_STEMS = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# ATX heading: 1-6 hashes, a space, the text (optional trailing hashes ignored).
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")

_EDIT_OPERATIONS = (
    "append", "prepend", "replace",
    "insert_after_heading", "insert_before_heading", "replace_heading",
)


# ── Path law ──────────────────────────────────────────────────────────────────

def _root() -> Path:
    """The archive root as a Path. Read at call time so tests can patch
    _ARCHIVE_ROOT."""
    return Path(_ARCHIVE_ROOT)


def _listable(p: Path, root: Path) -> bool:
    """Whether list and search may show this directory entry: an .md file
    that resolves inside the archive (`root` already resolved). A symlink out
    is skipped, as _resolve refuses it for read and edit; search had read the
    file at the far end of one (measured 2026-11-23)."""
    try:
        return (p.is_file() and p.suffix.lower() == ".md"
                and p.resolve().parent == root)
    except (OSError, RuntimeError):     # a symlink loop, or a vanished entry
        return False


def _modified(mtime: float) -> str:
    """A document's modification time as local ISO, or "" when the platform
    can't convert it. On Windows a time before 1970 or past year 9999 raises
    OSError (ValueError or OverflowError elsewhere), and one such document
    failed the whole listing (measured 2026-11-23)."""
    try:
        return datetime.datetime.fromtimestamp(mtime).isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return ""


def _resolve(filename: str) -> "Path | dict[str, Any]":
    """Validate a bare .md filename and return its resolved path inside the
    archive, or an {"error": ...} dict. Every public handler goes through
    this — there is no second door."""
    name = (filename or "").strip()
    if not name:
        return {"error": "invalid_filename: empty"}
    if len(name) > _MAX_FILENAME_LEN:
        return {"error": f"invalid_filename: longer than {_MAX_FILENAME_LEN} chars"}
    if not _FILENAME_RE.match(name):
        return {"error": (
            "invalid_filename: must be a bare name ending in .md — letters, "
            "digits, spaces, dots, hyphens, underscores only; no folders or "
            "path separators"
        )}
    stem = name.rsplit(".", 1)[0].split(".")[0].strip()
    if stem.upper() in _RESERVED_STEMS:
        return {"error": f"invalid_filename: {stem!r} is a reserved Windows device name"}

    root = _root().resolve()
    path = (root / name).resolve()
    # Belt and braces: the allowlist already forbids separators, but the law is
    # "resolve paths before use; refuse access outside the archive", so verify.
    if path.parent != root:
        return {"error": "invalid_filename: resolves outside the archive"}
    return path


def _read_utf8(path: Path) -> "str | dict[str, Any]":
    """Read one archive file under the size cap, strictly UTF-8."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        return {"error": f"read_failed: {type(exc).__name__}"}
    if size > _MAX_DOC_BYTES:
        return {"error": f"too_large: {size} bytes exceeds the {_MAX_DOC_BYTES} cap"}
    try:
        return path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        return {"error": "not_utf8: file is not valid UTF-8 text"}
    except OSError as exc:
        return {"error": f"read_failed: {type(exc).__name__}"}


def _atomic_write(path: Path, text: str) -> "dict[str, Any] | None":
    """Write text to path atomically: tempfile in the same directory, fsync,
    os.replace. Returns an error dict on failure, None on success."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=".herodotus-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except OSError as exc:
        log.error("Herodotus: write failed for %s: %s", path.name, exc)
        return {"error": f"write_failed: {type(exc).__name__}"}
    return None


# ── Front matter ──────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _front_matter(title: str, source: str) -> str:
    stamp = _now()
    return (
        "---\n"
        f"title: {title}\n"
        f"created: {stamp}\n"
        f"updated: {stamp}\n"
        f"source: {source}\n"
        "tags:\n"
        "---\n\n"
    )


def _front_matter_span(lines: "list[str]") -> "tuple[int, int] | None":
    """(start, end) line indices of a LEADING front-matter block — lines[start]
    and lines[end] are the '---' fences — or None if the file has none."""
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return (0, i)
    return None


def _bump_updated(text: str) -> str:
    """If a leading front-matter block contains an 'updated:' line, rewrite it
    to now. Anything else — no block, no line — passes through untouched."""
    lines = text.split("\n")
    span = _front_matter_span(lines)
    if span is None:
        return text
    for i in range(span[0] + 1, span[1]):
        if lines[i].startswith("updated:"):
            lines[i] = f"updated: {_now()}"
            break
    return "\n".join(lines)


# ── Edit engine ───────────────────────────────────────────────────────────────

def _find_heading(lines: "list[str]", target: str) -> "int | dict[str, Any]":
    """Index of the ONE heading line whose text matches target (leading hashes
    and surrounding whitespace ignored, case-insensitive), or an error dict."""
    want = target.lstrip("#").strip().lower()
    if not want:
        return {"error": "bad_arguments: heading operations need a heading text in 'target'"}
    hits = []
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m and m.group(2).strip().lower() == want:
            hits.append(i)
    if not hits:
        return {"error": f"not_found: no heading matches {target!r}"}
    if len(hits) > 1:
        return {"error": f"heading_ambiguous: {len(hits)} headings match {target!r}"}
    return hits[0]


def _apply_edit(
    text: str, operation: str, target: str, content: str
) -> "str | dict[str, Any]":
    """Pure function: apply one edit operation to the document text. Returns
    the new text, or an error dict. Deterministic on purpose — same inputs,
    same output, no cleverness."""
    if operation != "replace" and not content:
        return {"error": f"bad_arguments: {operation} needs non-empty 'content'"}

    if operation == "append":
        joiner = "" if (not text or text.endswith("\n")) else "\n"
        return text + joiner + content

    if operation == "prepend":
        # Prepending ABOVE a front-matter block would corrupt it, so a leading
        # block is respected: content lands immediately after the closing
        # fence. A file without front matter gets a true prepend.
        lines = text.split("\n")
        span = _front_matter_span(lines)
        if span is None:
            joiner = "" if content.endswith("\n") else "\n"
            return content + joiner + text
        head = lines[: span[1] + 1]
        tail = lines[span[1] + 1 :]
        return "\n".join(head + content.split("\n") + tail)

    if operation == "replace":
        if not target:
            return {"error": "bad_arguments: replace needs the exact text to replace in 'target'"}
        n = text.count(target)
        if n == 0:
            return {"error": "not_found: that exact text is not in the document"}
        if n > 1:
            return {"error": f"target_ambiguous: text appears {n} times; include more surrounding context"}
        return text.replace(target, content, 1)

    if operation in ("insert_after_heading", "insert_before_heading", "replace_heading"):
        lines = text.split("\n")
        idx = _find_heading(lines, target)
        if isinstance(idx, dict):
            return idx
        if operation == "insert_after_heading":
            new_lines = lines[: idx + 1] + content.split("\n") + lines[idx + 1 :]
        elif operation == "insert_before_heading":
            new_lines = lines[:idx] + content.split("\n") + lines[idx:]
        else:  # replace_heading — rename the heading LINE, level preserved.
            m = _HEADING_RE.match(lines[idx])
            assert m is not None  # _find_heading only returns heading lines
            new_lines = lines[:]
            new_lines[idx] = f"{m.group(1)} {content.strip()}"
        return "\n".join(new_lines)

    return {"error": (
        f"unknown_action: {operation!r} — must be one of {', '.join(_EDIT_OPERATIONS)}"
    )}


# ── Public API — one job: three reads and two writers ─────────────────────────
# The reads are reached through read_archive (below); the writers are tools.

def list_documents() -> "dict[str, Any]":
    """List every document in the archive. Never raises.

    Success:  {"documents": [{"filename", "size", "modified"}, ...]}  (sorted;
              empty list for an empty or not-yet-created archive; "modified"
              is "" for a time the platform can't convert)
    Errors:   {"error": "read_failed: <detail>"}   (the folder can't be listed)

    A document it can't stat is skipped and logged, the way search skips one
    it can't read, so one bad file never hides the rest.
    """
    root = _root()
    if not root.is_dir():
        return {"documents": []}
    docs: "list[dict[str, Any]]" = []
    try:
        inside = root.resolve()
        for p in sorted(root.iterdir(), key=lambda q: q.name.lower()):
            if not _listable(p, inside):
                continue
            try:
                st = p.stat()
            except OSError as exc:
                log.warning("Herodotus.list: skipping %s (%s)", p.name, type(exc).__name__)
                continue
            docs.append({
                "filename": p.name,
                "size": st.st_size,
                "modified": _modified(st.st_mtime),
            })
    except OSError as exc:
        log.warning("Herodotus.list_documents: %s", exc)
        return {"error": f"read_failed: {type(exc).__name__}"}
    return {"documents": docs}


def search_documents(query: str) -> "dict[str, Any]":
    """Case-insensitive substring search across every document. Never raises.

    Success:  {"matches": [{"filename", "matches", "snippets": [str, ...]}, ...]}
    Errors:   {"error": "no_results" | "bad_arguments: ..." | "read_failed: ..."}
    """
    q = (query or "").strip().lower()
    if not q:
        return {"error": "bad_arguments: search needs a query"}
    root = _root()
    if not root.is_dir():
        return {"error": "no_results"}

    out: "list[dict[str, Any]]" = []
    try:
        inside = root.resolve()
        for p in sorted(root.iterdir(), key=lambda r: r.name.lower()):
            if not _listable(p, inside):
                continue
            body = _read_utf8(p)
            if isinstance(body, dict):    # unreadable file degrades to a skip
                log.warning("Herodotus.search: skipping %s (%s)", p.name, body["error"])
                continue
            snippets: "list[str]" = []
            count = 0
            for line in body.split("\n"):
                if q in line.lower():
                    count += 1
                    if len(snippets) < _SEARCH_MAX_LINES:
                        snippets.append(line.strip()[:_SNIPPET_CHARS])
            if count:
                out.append({"filename": p.name, "matches": count, "snippets": snippets})
                if len(out) >= _SEARCH_MAX_FILES:
                    break
    except OSError as exc:
        # The directory enumeration itself failed (root deleted mid-scan, or a
        # permission error listing it). list_documents guards this same walk;
        # search must too, or its "never raises" is a half-truth. Per-file read
        # errors above still degrade to a skip — this is whole-scan failure.
        log.warning("Herodotus.search_documents: %s", exc)
        return {"error": f"read_failed: {type(exc).__name__}"}
    if not out:
        return {"error": "no_results"}
    return {"matches": out}


def read_document(filename: str, offset: int = 0) -> "dict[str, Any]":
    """Return up to _READ_CHARS of one document's text (front matter included),
    starting at character `offset`. Never raises.

    "size" is the WHOLE file in bytes. "truncated" means more text follows this
    part, and "next_offset" (present only then) is where the next read starts.
    A negative offset reads from the start; one past the end is a slug.

    Success:  {"filename": str, "content": str, "size": int, "offset": int,
               "truncated": bool[, "next_offset": int]}
    Errors:   {"error": "invalid_filename: ..." | "not_found" | "not_utf8: ..."
                       | "too_large: ..." | "read_failed: ..."
                       | "bad_arguments: ..."}
    """
    path = _resolve(filename)
    if isinstance(path, dict):
        return path
    if not path.is_file():
        return {"error": "not_found"}
    body = _read_utf8(path)
    if isinstance(body, dict):
        return body
    try:
        start = max(0, int(offset))
    except (TypeError, ValueError, OverflowError):   # OverflowError: int(inf)
        return {"error": "bad_arguments: offset must be a whole number"}
    if start and start >= len(body):
        return {"error": f"bad_arguments: offset {start} is past the end of "
                         f"this {len(body)}-character document"}
    part = body[start:start + _READ_CHARS]
    end = start + len(part)
    out: "dict[str, Any]" = {
        "filename": path.name, "content": part,
        "size": len(body.encode("utf-8")), "offset": start,
        "truncated": end < len(body),
    }
    if out["truncated"]:
        out["next_offset"] = end
    return out


_READ_ACTIONS = ("list", "search", "read")


def read_archive(action: str, query: str = "", filename: str = "",
                 offset: int = 0) -> "dict[str, Any]":
    """The one read tool (read_archive): `action` picks list_documents,
    search_documents or read_document, and their own answers and slugs come
    back unchanged, so a missing query is still bad_arguments and a missing
    filename still invalid_filename. Arguments another action uses are
    ignored. An action outside the enum reaches here (the gate leaves enum
    membership to handlers, §8) and gets unknown_action. Never raises."""
    if action == "list":
        return list_documents()
    if action == "search":
        return search_documents(query)
    if action == "read":
        return read_document(filename, offset)
    return {"error": f"unknown_action: {action!r} is not one of "
                     f"{', '.join(_READ_ACTIONS)}"}


def write_document(filename: str, content: str, source: str = "") -> "dict[str, Any]":
    """Create or overwrite one document, atomically. Never raises.

    When the content doesn't already begin with a '---' front-matter fence, a
    block is prepended: title (from the filename), created/updated (now),
    source (the optional provenance argument — e.g. the URL a Callimachus
    fetch came from — kept to one line, since a newline in it closed the
    block early), empty tags.

    Success:  {"filename": str, "size": int, "overwrote": bool}
    Errors:   {"error": "invalid_filename: ..." | "bad_arguments: ..."
                       | "too_large: ..." | "write_failed: ..."}
    """
    path = _resolve(filename)
    if isinstance(path, dict):
        return path
    if not (content or "").strip():
        return {"error": "bad_arguments: content is empty"}

    text = content
    if not text.lstrip().startswith("---"):
        text = _front_matter(path.stem, " ".join((source or "").split())) + text
    if not text.endswith("\n"):
        text += "\n"

    size = len(text.encode("utf-8"))
    if size > _MAX_DOC_BYTES:
        return {"error": f"too_large: {size} bytes exceeds the {_MAX_DOC_BYTES} cap"}

    overwrote = path.is_file()
    err = _atomic_write(path, text)
    if err is not None:
        return err
    log.info("Herodotus: %s %s (%d bytes)",
             "overwrote" if overwrote else "wrote", path.name, size)
    return {"filename": path.name, "size": size, "overwrote": overwrote}


def edit_document(
    filename: str, operation: str, target: str = "", content: str = ""
) -> "dict[str, Any]":
    """Apply one edit operation to an existing document, atomically. Never
    raises. Operations: append, prepend, replace (target must appear exactly
    once), insert_after_heading, insert_before_heading (target = heading text),
    replace_heading (renames the heading line, level preserved). If a leading
    front-matter block has an 'updated:' line, it is bumped to now.

    Success:  {"filename": str, "size": int, "operation": str}
    Errors:   {"error": "invalid_filename: ..." | "not_found" | "not_utf8: ..."
                       | "too_large: ..." | "unknown_action: ..."
                       | "bad_arguments: ..." | "not_found: ..."
                       | "target_ambiguous: ..." | "heading_ambiguous: ..."
                       | "write_failed: ..."}
    """
    path = _resolve(filename)
    if isinstance(path, dict):
        return path
    if not path.is_file():
        return {"error": "not_found"}

    body = _read_utf8(path)
    if isinstance(body, dict):
        return body

    new_text = _apply_edit(body, (operation or "").strip(), target or "", content or "")
    if isinstance(new_text, dict):
        return new_text
    new_text = _bump_updated(new_text)
    if not new_text.endswith("\n"):
        new_text += "\n"

    size = len(new_text.encode("utf-8"))
    if size > _MAX_DOC_BYTES:
        return {"error": f"too_large: edit would grow the file to {size} bytes, over the {_MAX_DOC_BYTES} cap"}

    err = _atomic_write(path, new_text)
    if err is not None:
        return err
    log.info("Herodotus: edited %s (%s, %d bytes)", path.name, operation, size)
    return {"filename": path.name, "size": size, "operation": operation}


# ── Tool definitions — the plural export (CONVENTIONS §2/§3) ──────────────────

# One read tool over the three reads. Its description keeps the retired three's
# trigger words ("saved", "what the archive contains", "something they saved
# earlier") — a merged description that drops them loses routing (§12,
# 2026-11-14, the sky merge) — and read_document's paging steering word for word.
READ_ARCHIVE_TOOL_DEFINITION: "dict[str, Any]" = {
    "type": "function",
    "function": {
        "name": "read_archive",
        "description": (
            "Reads the local knowledge archive of saved Markdown notes. list: "
            "every document. search: documents containing some text, with "
            "snippets. read: one document by its exact filename (e.g. "
            "'notes.md'). Call when the user refers to something they saved "
            "earlier or asks what the archive contains; list or search first "
            "if the filename isn't known. A long document comes back in parts: "
            "if 'truncated' is true, call again with offset set to "
            "'next_offset'. To change part of a long document, use "
            "edit_document rather than rewriting it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_READ_ACTIONS),
                    "description": ("list = every document; search = find text "
                                    "(needs query); read = one document (needs "
                                    "filename)."),
                },
                "query": {"type": "string", "description": "Text to look for (search)."},
                "filename": {"type": "string", "description": "Bare .md filename (read)."},
                "offset": {
                    "type": "integer",
                    "description": ("Where to continue reading: the next_offset "
                                    "a previous read returned. Omit to start at "
                                    "the beginning."),
                },
            },
            "required": ["action"],
        },
    },
}

WRITE_TOOL_DEFINITION: "dict[str, Any]" = {
    "type": "function",
    "function": {
        "name": "write_document",
        "description": (
            "Creates or fully overwrites one Markdown document in the local "
            "knowledge archive. Call when the user asks to save something, or "
            "to keep new knowledge worth keeping (e.g. a useful web result). "
            "New documents get a front-matter "
            "header automatically; pass 'source' (a URL or origin) when the "
            "knowledge came from somewhere. To change part of an existing "
            "document, prefer edit_document."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Bare .md filename, e.g. 'topic.md'."},
                "content":  {"type": "string", "description": "Full Markdown body of the document."},
                "source":   {"type": "string", "description": "Optional provenance, e.g. the source URL."},
            },
            "required": ["filename", "content"],
        },
    },
}

EDIT_TOOL_DEFINITION: "dict[str, Any]" = {
    "type": "function",
    "function": {
        "name": "edit_document",
        "description": (
            "Changes part of an existing archive document without rewriting the "
            "whole file. Call when the user wants to add to, correct, or "
            "restructure a document that already exists; prefer this over "
            "write_document, which replaces the entire file. This modifies a "
            "stored document."
        ),
        # `target` and `content` mean different things per operation, which JSON
        # Schema can't express — so that matrix lives on the parameters it
        # describes rather than in the function description above.
        "parameters": {
            "type": "object",
            "properties": {
                "filename":  {"type": "string", "description": "Bare .md filename."},
                "operation": {
                    "type": "string",
                    "enum": list(_EDIT_OPERATIONS),
                    "description": (
                        "append/prepend add content at the end/start; replace "
                        "swaps target for content; insert_after_heading and "
                        "insert_before_heading put content around the heading "
                        "named by target; replace_heading renames it."
                    ),
                },
                "target":  {"type": "string", "description": "The anchor: for replace, the exact existing text (must occur once); for the heading operations, the heading's text. Unused by append/prepend."},
                "content": {"type": "string", "description": "The new text. May be empty only for replace, which then deletes the target."},
            },
            "required": ["filename", "operation"],
        },
    },
}

# Plural export: the registry (pythia.py) splats these in and binds each tool
# name to the like-named module function. See CONVENTIONS §2/§3.
TOOL_DEFINITIONS: "list[dict[str, Any]]" = [
    READ_ARCHIVE_TOOL_DEFINITION,
    WRITE_TOOL_DEFINITION,
    EDIT_TOOL_DEFINITION,
]

# What the code reads about each tool here; the model never sees it
# (CONVENTIONS §8, "TOOL_META"). network: none = local only, cached = a poll
# or TTL cache, live = may reach the network on every call. mutates = it
# changes state outside the turn.
TOOL_META: dict[str, dict[str, Any]] = {
    "read_archive":   {"domain": "archive", "network": "none", "mutates": False},
    "write_document": {"domain": "archive", "network": "none", "mutates": True},
    "edit_document":  {"domain": "archive", "network": "none", "mutates": True},
}


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import io
    import shutil
    import sys

    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO)

    # A mutating tool's smoke test runs in a sandbox, not the real archive —
    # the one place "run the real thing" (§3) yields to "leave no side effects".
    sandbox = tempfile.mkdtemp(prefix="herodotus_demo_")
    _ARCHIVE_ROOT = sandbox
    print(f"[Herodotus] sandbox archive: {sandbox}\n")

    print("write:", write_document(
        "histories.md",
        "# Book One\n\nCroesus was Lydian by birth.\n\n# Book Two\n\nOn Egypt.",
        source="Herodotus, The Histories",
    ))
    print("list: ", list_documents())
    print("search 'croesus':", search_documents("croesus"))
    print("edit (insert_after_heading):", edit_document(
        "histories.md", "insert_after_heading",
        target="Book Two", content="\nThe Nile floods in summer.",
    ))
    print("edit (replace_heading):", edit_document(
        "histories.md", "replace_heading", target="Book One", content="Book One — Clio",
    ))
    print("traversal refused:", read_document("..\\..\\secrets.md"))
    print("reserved refused: ", write_document("CON.md", "x"))
    doc = read_document("histories.md")
    print("\n--- histories.md ---")
    print(doc["content"] if "content" in doc else doc)

    shutil.rmtree(sandbox, ignore_errors=True)
    print("[Herodotus] sandbox removed — no side effects.")
