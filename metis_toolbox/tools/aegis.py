"""
aegis.py — The Aegis (the shield of Zeus and Athena)
=====================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Vouch for the programs in bin/ before Felhaven runs them.
             Each one must match the sha256 recorded for it in
             config/bin_manifest.json, beside where it came from. Named for
             the aegis, the shield that guarded what was behind it.

Contract:    Plain functions, not a brain tool:
               • resolve(name) -> Resolution(path, problem). The one way
                 Morpheus, Echo and Orpheus find mpv, yt-dlp and ffmpeg:
                   bin/<name>.exe present and matching   -> (path, None)
                   bin/<name>.exe present, NOT matching,
                     or with no manifest entry           -> (None, "binary_unverified: ...")
                   not in bin/, found on PATH            -> (path, None), unverified
                   nowhere                               -> (None, None)
                 A bin/ copy that fails is REFUSED and never falls through to
                 PATH: otherwise breaking the bin/ copy would be a way to
                 swap in whatever PATH holds. Never raises.
               • CLI (python -m tools.aegis ...):
                   verify                 check every manifest entry now
                   record yt-dlp          after `bin\\yt-dlp.exe -U`: hash it,
                                          check it against the LATEST release's
                                          published SHA2-256SUMS before it ever
                                          runs, then read --version, check that
                                          release's sums too, and write the
                                          entry (refuses on a mismatch)
                   record <name> --version V --source URL --expect SHA256
                          [--archive FILE [--extracted EXE]]
                                          for mpv and ffmpeg: --expect is the
                                          publisher's checksum, of the exe or
                                          of the archive it came in

Decisions:   (owner, 2026-10-01; reasons in CONVENTIONS §12)
               • A mismatch REFUSES (an ERROR for Emanon, a slug the panel
                 maps to text). A warning would be the routine people click
                 past, and then it is the cheapest path.
               • A program found only on PATH is allowed, unverified, with
                 one WARNING per process: it is the owner's own install,
                 outside the manifest's reach.
               • Hashing happens once per program per process (cached by
                 path, size and mtime), not on every launch of it. Measured
                 2026-10-01: all three, 237.7 MB, hash in 1.28 s.

Upstream:    tools/morpheus.py, tools/echo.py, tools/orpheus.py (resolve);
             the owner, at a terminal (the CLI).
Downstream:  config/bin_manifest.json (read; written only by `record`),
             tools/hermes.py (the published SHA2-256SUMS, `record` only).

Requires:    stdlib only — argparse, datetime, hashlib, json, logging, os,
             re, shutil, subprocess, tempfile, threading, zipfile; plus
             tools/hermes.py.
"""

import argparse
import datetime
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from typing import Any, NamedTuple, Optional

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import hermes

log = logging.getLogger("METIS.aegis")

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_DIR = os.path.join(_APP_ROOT, "bin")
MANIFEST_PATH = os.path.join(_APP_ROOT, "config", "bin_manifest.json")

ERR_UNVERIFIED = "binary_unverified"
_FIELDS = ("file", "version", "source_url", "published_sha256", "published_for",
           "checksum_source", "sha256", "recorded")
_SHA256 = re.compile(r"[0-9a-f]{64}")

# yt-dlp's own release layout: the exe and its published checksum file.
_YTDLP_RELEASE = "https://github.com/yt-dlp/yt-dlp/releases/download/{version}/"
# GitHub redirects this to the newest release's assets (measured 2026-10-03:
# via release-assets.githubusercontent.com, 1,595 B, https all the way).
_YTDLP_LATEST = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/"
_SUMS_MAX_BYTES = 64 * 1024     # measured 2026-10-01: SHA2-256SUMS is 1,595 B
_SUMS_TIMEOUT = 10
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Resolution(NamedTuple):
    path: Optional[str]         # what to run, or None
    problem: Optional[str]      # a §2 slug when a bin/ copy was refused


# ── Verification (cached per process) ─────────────────────────────────────────

_lock = threading.Lock()
_verdicts: dict[tuple[str, int, int], Optional[str]] = {}   # stat key -> problem
_path_warned: set[str] = set()


def _clear_cache() -> None:
    """Forget every verdict and warning. For tests."""
    with _lock:
        _verdicts.clear()
        _path_warned.clear()


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _load_manifest() -> dict[str, Any]:
    """The `programs` table. Raises on a missing or malformed file, and the
    caller fails closed: no manifest means nothing in bin/ is vouched for."""
    with open(MANIFEST_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    programs = data.get("programs") if isinstance(data, dict) else None
    if not isinstance(programs, dict):
        raise ValueError("no 'programs' table")
    return programs


def _check(name: str, path: str) -> Optional[str]:
    """None if `path` matches the manifest entry for `name`, else the slug."""
    try:
        entry = _load_manifest().get(name)
    except (OSError, ValueError) as exc:
        return f"{ERR_UNVERIFIED}: the bin manifest is unreadable ({type(exc).__name__})"
    if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str):
        return f"{ERR_UNVERIFIED}: {os.path.basename(path)} has no manifest entry"
    try:
        actual = sha256_of(path)
    except OSError as exc:
        return f"{ERR_UNVERIFIED}: {os.path.basename(path)} could not be read ({type(exc).__name__})"
    if actual != entry["sha256"].lower():
        return f"{ERR_UNVERIFIED}: {os.path.basename(path)} does not match its manifest entry"
    return None


def resolve(name: str) -> Resolution:
    """Find `name` (no .exe), vouched for. See the module docstring."""
    local = os.path.join(BIN_DIR, f"{name}.exe")
    try:
        st = os.stat(local)
    except OSError:
        st = None
    if st is not None:
        key = (local, st.st_size, st.st_mtime_ns)
        with _lock:
            if key in _verdicts:
                problem = _verdicts[key]
            else:
                problem = _check(name, local)
                _verdicts[key] = problem
                if problem:
                    log.error("Aegis: refusing bin/%s.exe — %s. If it was updated on "
                              "purpose, record it: python -m tools.aegis record %s",
                              name, problem, name)
        return Resolution(None, problem) if problem else Resolution(local, None)

    found = shutil.which(name)
    if found:
        with _lock:
            if name not in _path_warned:
                _path_warned.add(name)
                log.warning("Aegis: %s is not in bin/; using %s from PATH, unverified",
                            name, found)
    return Resolution(found, None)


# ── Recording (the CLI) ───────────────────────────────────────────────────────

def _write_manifest(programs: dict[str, Any]) -> None:
    """Atomic: tempfile beside it, then os.replace (CONVENTIONS §9)."""
    try:
        with open(MANIFEST_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data["programs"] = dict(sorted(programs.items()))
    folder = os.path.dirname(MANIFEST_PATH)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, MANIFEST_PATH)
    except BaseException:
        os.unlink(tmp)
        raise


def _ytdlp_version(path: str) -> str:
    proc = subprocess.run([path, "--version"], capture_output=True, timeout=30,
                          creationflags=_NO_WINDOW)
    version = proc.stdout.decode("ascii", "replace").strip()
    if proc.returncode != 0 or not re.fullmatch(r"[0-9][0-9.]*", version):
        raise RecordError(f"yt-dlp --version gave no usable version ({version[:40]!r})")
    return version


def _published_sum(sums_text: str, filename: str) -> str:
    for line in sums_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == filename:
            return parts[0].lower()
    raise RecordError(f"{filename} is not listed in the published sums")


class RecordError(Exception):
    """`record` refused; the message says why."""


def _entry(name: str, path: str, version: str, source_url: str,
           published: str, published_for: str, checksum_source: str) -> dict[str, str]:
    return {
        "file": os.path.basename(path),
        "version": version,
        "source_url": source_url,
        "published_sha256": published,
        "published_for": published_for,
        "checksum_source": checksum_source,
        "sha256": sha256_of(path),
        "recorded": datetime.date.today().isoformat(),
    }


def _published_ytdlp_sum(base: str) -> str:
    reply = hermes.get(base + "SHA2-256SUMS", cap=_SUMS_MAX_BYTES, timeout=_SUMS_TIMEOUT)
    reply.raise_for_status()
    return _published_sum(reply.text, "yt-dlp.exe")


def record_ytdlp() -> dict[str, str]:
    """After `bin\\yt-dlp.exe -U`: check the new file against its release's
    published SHA2-256SUMS, then record it. Refuses on any mismatch.

    The file never runs before something has vouched for it. Its version,
    which names its release, is only known by running it, so the file is
    first checked against the LATEST release's published sums (what `-U`
    installs); only a match runs `--version`. That version's own published sum
    must agree too, since it is what the manifest records. A file that is not
    the latest release is refused without running (run `-U` again)."""
    path = os.path.join(BIN_DIR, "yt-dlp.exe")
    if not os.path.isfile(path):
        raise RecordError("bin/yt-dlp.exe is missing")
    actual = sha256_of(path)
    latest = _published_ytdlp_sum(_YTDLP_LATEST)
    if actual != latest:
        raise RecordError(f"bin/yt-dlp.exe ({actual[:12]}...) is not the latest "
                          f"release's yt-dlp.exe ({latest[:12]}...); it was not run. "
                          "Update it with `bin\\yt-dlp.exe -U`, then record again")
    version = _ytdlp_version(path)
    base = _YTDLP_RELEASE.format(version=version)
    published = _published_ytdlp_sum(base)
    if actual != published:
        raise RecordError(f"bin/yt-dlp.exe ({actual[:12]}...) is not the published "
                          f"{version} yt-dlp.exe ({published[:12]}...)")
    return _store("yt-dlp", _entry("yt-dlp", path, version, base + "yt-dlp.exe",
                                   published, "yt-dlp.exe", base + "SHA2-256SUMS"))


def record_other(name: str, version: str, source_url: str, expect: str,
                 archive: Optional[str] = None, extracted: Optional[str] = None,
                 checksum_source: Optional[str] = None) -> dict[str, str]:
    """For a program whose publisher gives one checksum, of the exe or of the
    archive it ships in. With an archive, the exe inside it (read from a zip,
    or `extracted` by hand from anything else) must be byte-identical to bin/."""
    path = os.path.join(BIN_DIR, f"{name}.exe")
    if not os.path.isfile(path):
        raise RecordError(f"bin/{name}.exe is missing")
    expect = expect.strip().lower()
    if not _SHA256.fullmatch(expect):
        raise RecordError("--expect must be a 64-character sha256")
    if not source_url.startswith("https://"):
        raise RecordError("--source must be an https URL")
    exe_hash = sha256_of(path)
    if archive is None:
        if exe_hash != expect:
            raise RecordError(f"bin/{name}.exe does not match the published checksum")
        published_for = f"{name}.exe"
    else:
        if sha256_of(archive) != expect:
            raise RecordError(f"{os.path.basename(archive)} does not match the published checksum")
        if extracted is not None:
            inner = sha256_of(extracted)
        elif zipfile.is_zipfile(archive):
            inner = _hash_in_zip(archive, f"{name}.exe")
        else:
            raise RecordError("not a zip: extract the exe yourself and pass --extracted")
        if inner != exe_hash:
            raise RecordError(f"bin/{name}.exe is not the {name}.exe inside "
                              f"{os.path.basename(archive)}")
        published_for = os.path.basename(archive)
    return _store(name, _entry(name, path, version, source_url, expect,
                               published_for, checksum_source or source_url))


def _hash_in_zip(archive: str, exe_name: str) -> str:
    with zipfile.ZipFile(archive) as zf:
        members = [i for i in zf.infolist()
                   if i.filename.replace("\\", "/").rsplit("/", 1)[-1] == exe_name]
        if len(members) != 1:
            raise RecordError(f"{exe_name} appears {len(members)} times in the archive")
        h = hashlib.sha256()
        with zf.open(members[0]) as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()


def _store(name: str, entry: dict[str, str]) -> dict[str, str]:
    try:
        programs = _load_manifest()
    except (OSError, ValueError):
        programs = {}
    programs[name] = entry
    _write_manifest(programs)
    _clear_cache()
    return entry


def verify() -> dict[str, str]:
    """{name: "ok" | the problem} for every manifest entry, checked now."""
    _clear_cache()
    out: dict[str, str] = {}
    try:
        names = sorted(_load_manifest())
    except (OSError, ValueError) as exc:
        return {"manifest": f"unreadable ({type(exc).__name__})"}
    for name in names:
        path = os.path.join(BIN_DIR, f"{name}.exe")
        if not os.path.isfile(path):
            out[name] = "not in bin/"
            continue
        out[name] = _check(name, path) or "ok"
    return out


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.aegis",
                                     description="Vouch for the programs in bin/.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify", help="check every manifest entry now")
    rec = sub.add_parser("record", help="record a deliberate update")
    rec.add_argument("name")
    rec.add_argument("--version")
    rec.add_argument("--source", help="https URL of what was downloaded")
    rec.add_argument("--expect", help="the publisher's sha256")
    rec.add_argument("--archive", help="the downloaded archive, if --expect is its checksum")
    rec.add_argument("--extracted", help="the exe extracted from a non-zip archive")
    rec.add_argument("--checksum-source", help="where --expect was published, if not --source")
    args = parser.parse_args(argv)

    if args.command == "verify":
        results = verify()
        for name, verdict in results.items():
            print(f"{name:<8} {verdict}")
        return 0 if all(v in ("ok", "not in bin/") for v in results.values()) else 1

    try:
        if args.name == "yt-dlp" and not args.expect:
            entry = record_ytdlp()
        else:
            if not (args.version and args.source and args.expect):
                parser.error("record <name> needs --version, --source and --expect")
            entry = record_other(args.name, args.version, args.source, args.expect,
                                 args.archive, args.extracted, args.checksum_source)
    except (RecordError, hermes.NetError, OSError) as exc:
        print(f"refused: {exc}")
        return 1
    print(f"recorded {args.name} {entry['version']}: {entry['sha256']}")
    print("Commit config/bin_manifest.json with the update.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
