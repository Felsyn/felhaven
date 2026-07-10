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
             TOOL_DEFINITIONS (a list of five) and five like-named handlers:
               • list_documents()                      -> {"documents": [...]}
               • search_documents(query)               -> {"matches": [...]}
               • read_document(filename)               -> {"filename", "content", "size"}
               • write_document(filename, content,
                                source="")             -> {"filename", "size", "overwrote"}
               • edit_document(filename, operation,
                               target="", content="")  -> {"filename", "size", "operation"}
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
                  the filename allowlist).
               2. Bare .md filenames only — no separators, no traversal, no
                  dotfiles, no Windows reserved device names (CON, NUL, ...).
               3. UTF-8 only, both directions — a non-UTF-8 file reads as a
                  not_utf8 error, never mojibake.
               4. 1 MB cap per document, read and write.
               5. Atomic writes — tempfile in the same directory + os.replace,
                  so a crash mid-write can never leave a half-document.
             No shell, no subprocess, no network — pathlib and open() only.

Front matter: write_document prepends a YAML front-matter block (title /
             created / updated / source / tags) when the content doesn't
             already start with one; edit_document bumps the updated: line if a
             leading block has one. Hand-rolled emit and line-scan — simple
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

Requires:    stdlib only — datetime, logging, os, pathlib, re, tempfile.
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
_MAX_FILENAME_LEN  = 100         # generous; guards pathological tool calls
_SEARCH_MAX_FILES  = 10          # search reports at most this many documents
_SEARCH_MAX_LINES  = 3           # ... with at most this many snippet lines each
_SNIPPET_CHARS     = 200         # per snippet line

# Bare name, must start alphanumeric, then a conservative allowlist, .md suffix.
# No separators of any kind — traversal is impossible by construction; the
# resolve-then-contain check in _resolve() backstops it anyway.
_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]*\.md$", re.IGNORECASE)

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
        return {"error": f"read_failed: {exc}"}
    if size > _MAX_DOC_BYTES:
        return {"error": f"too_large: {size} bytes exceeds the {_MAX_DOC_BYTES} cap"}
    try:
        return path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        return {"error": "not_utf8: file is not valid UTF-8 text"}
    except OSError as exc:
        return {"error": f"read_failed: {exc}"}


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
        return {"error": f"write_failed: {exc}"}
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
        return {"error": "missing_target: heading operations need a heading text in 'target'"}
    hits = []
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m and m.group(2).strip().lower() == want:
            hits.append(i)
    if not hits:
        return {"error": f"heading_not_found: no heading matches {target!r}"}
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
        return {"error": f"missing_content: {operation} needs non-empty 'content'"}

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
            return {"error": "missing_target: replace needs the exact text to replace in 'target'"}
        n = text.count(target)
        if n == 0:
            return {"error": "target_not_found: that exact text is not in the document"}
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
        f"bad_operation: {operation!r} — must be one of {', '.join(_EDIT_OPERATIONS)}"
    )}


# ── Public API — five tools, one job ──────────────────────────────────────────

def list_documents() -> "dict[str, Any]":
    """List every document in the archive. Never raises.

    Success:  {"documents": [{"filename", "size", "modified"}, ...]}  (sorted;
              empty list for an empty or not-yet-created archive)
    Errors:   {"error": "list_failed: <detail>"}
    """
    root = _root()
    if not root.is_dir():
        return {"documents": []}
    docs: "list[dict[str, Any]]" = []
    try:
        for p in sorted(root.iterdir(), key=lambda q: q.name.lower()):
            if p.is_file() and p.suffix.lower() == ".md":
                st = p.stat()
                docs.append({
                    "filename": p.name,
                    "size": st.st_size,
                    "modified": datetime.datetime.fromtimestamp(
                        st.st_mtime
                    ).isoformat(timespec="seconds"),
                })
    except OSError as exc:
        log.warning("Herodotus.list_documents: %s", exc)
        return {"error": f"list_failed: {exc}"}
    return {"documents": docs}


def search_documents(query: str) -> "dict[str, Any]":
    """Case-insensitive substring search across every document. Never raises.

    Success:  {"matches": [{"filename", "matches", "snippets": [str, ...]}, ...]}
    Errors:   {"error": "no_matches" | "missing_query"}
    """
    q = (query or "").strip().lower()
    if not q:
        return {"error": "missing_query"}
    root = _root()
    if not root.is_dir():
        return {"error": "no_matches"}

    out: "list[dict[str, Any]]" = []
    for p in sorted(root.iterdir(), key=lambda r: r.name.lower()):
        if not (p.is_file() and p.suffix.lower() == ".md"):
            continue
        body = _read_utf8(p)
        if isinstance(body, dict):        # unreadable file degrades to a skip
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
    if not out:
        return {"error": "no_matches"}
    return {"matches": out}


def read_document(filename: str) -> "dict[str, Any]":
    """Return one document's full text (front matter included). Never raises.

    Success:  {"filename": str, "content": str, "size": int}
    Errors:   {"error": "invalid_filename: ..." | "not_found" | "not_utf8: ..."
                       | "too_large: ..." | "read_failed: ..."}
    """
    path = _resolve(filename)
    if isinstance(path, dict):
        return path
    if not path.is_file():
        return {"error": "not_found"}
    body = _read_utf8(path)
    if isinstance(body, dict):
        return body
    return {"filename": path.name, "content": body, "size": len(body.encode("utf-8"))}


def write_document(filename: str, content: str, source: str = "") -> "dict[str, Any]":
    """Create or overwrite one document, atomically. Never raises.

    When the content doesn't already begin with a '---' front-matter fence, a
    block is prepended: title (from the filename), created/updated (now),
    source (the optional provenance argument — e.g. the URL a Callimachus
    fetch came from), empty tags.

    Success:  {"filename": str, "size": int, "overwrote": bool}
    Errors:   {"error": "invalid_filename: ..." | "missing_content"
                       | "too_large: ..." | "write_failed: ..."}
    """
    path = _resolve(filename)
    if isinstance(path, dict):
        return path
    if not (content or "").strip():
        return {"error": "missing_content"}

    text = content
    if not text.lstrip().startswith("---"):
        text = _front_matter(path.stem, (source or "").strip()) + text
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
                       | "too_large: ..." | "bad_operation: ..."
                       | "missing_target: ..." | "missing_content: ..."
                       | "target_not_found: ..." | "target_ambiguous: ..."
                       | "heading_not_found: ..." | "heading_ambiguous: ..."
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

LIST_TOOL_DEFINITION: "dict[str, Any]" = {
    "type": "function",
    "function": {
        "name": "list_documents",
        "description": (
            "List every Markdown document in the local knowledge archive, with "
            "sizes and modified times. Call this first when you're unsure what "
            "the archive contains or what a document is called."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

SEARCH_TOOL_DEFINITION: "dict[str, Any]" = {
    "type": "function",
    "function": {
        "name": "search_documents",
        "description": (
            "Search the local knowledge archive for documents containing some "
            "text (case-insensitive). Returns matching filenames with snippet "
            "lines. Call this to find saved notes on a topic before answering "
            "from memory; then read_document the best hit."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to look for."},
            },
            "required": ["query"],
        },
    },
}

READ_TOOL_DEFINITION: "dict[str, Any]" = {
    "type": "function",
    "function": {
        "name": "read_document",
        "description": (
            "Read one Markdown document from the local knowledge archive by "
            "its exact filename (e.g. 'notes.md'). Use list_documents or "
            "search_documents first if you don't know the filename."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Bare .md filename."},
            },
            "required": ["filename"],
        },
    },
}

WRITE_TOOL_DEFINITION: "dict[str, Any]" = {
    "type": "function",
    "function": {
        "name": "write_document",
        "description": (
            "Create or fully overwrite one Markdown document in the local "
            "knowledge archive. Use this to save new knowledge worth keeping "
            "(e.g. a useful web result). New documents get a front-matter "
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
            "Edit part of an existing archive document without rewriting it. "
            "operation is one of: 'append' or 'prepend' (content only); "
            "'replace' (target = exact existing text, occurring once; content "
            "= its replacement); 'insert_after_heading' / "
            "'insert_before_heading' (target = a heading's text, content = "
            "lines to insert); 'replace_heading' (target = current heading "
            "text, content = its new text)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename":  {"type": "string", "description": "Bare .md filename."},
                "operation": {
                    "type": "string",
                    "enum": list(_EDIT_OPERATIONS),
                    "description": "Which edit to perform.",
                },
                "target":  {"type": "string", "description": "Existing text or heading to anchor on (not used by append/prepend)."},
                "content": {"type": "string", "description": "New text (may be empty only for replace, to delete the target)."},
            },
            "required": ["filename", "operation"],
        },
    },
}

# Plural export: the registry (pythia.py) splats these in and binds each tool
# name to the like-named module function. See CONVENTIONS §2/§3.
TOOL_DEFINITIONS: "list[dict[str, Any]]" = [
    LIST_TOOL_DEFINITION,
    SEARCH_TOOL_DEFINITION,
    READ_TOOL_DEFINITION,
    WRITE_TOOL_DEFINITION,
    EDIT_TOOL_DEFINITION,
]


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
