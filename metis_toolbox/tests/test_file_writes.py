"""
test_file_writes.py — every place Felhaven creates, changes, moves or deletes a
file is listed here with where its path comes from.

A write whose path someone else can steer lands wherever this account may
write: over a config file, beside the code, into a folder that runs things.
And a delete driven by a string can take a whole tree with it. So each such
call is a place where a value from the network, the model or a config file
could become a path. This guard parses every tracked runtime module and finds
them all. Each one must have a line in _WRITES, written by someone who traced
its path; a new one fails until that is done, and a stale line fails too. It
is the twin of test_process_launches.py and reads the same way.

What counts (CONVENTIONS §12): anything that creates, changes, moves or
deletes a file or folder.
  * open, io.open, codecs.open and os.fdopen with a write mode (w, a, x, +)
    or a mode that isn't a constant; os.open, whatever its flags;
  * os.remove / unlink / rmdir / removedirs / rename / renames / replace /
    makedirs / mkdir / utime / chmod / truncate / link / symlink;
  * shutil.rmtree / move / copy* / copytree / make_archive / unpack_archive;
  * tempfile.mkstemp / mkdtemp / NamedTemporaryFile / TemporaryDirectory /
    TemporaryFile / SpooledTemporaryFile;
  * wave, gzip, bz2, lzma and tarfile opened for writing, zipfile.ZipFile in
    a write mode, and extract / extractall on anything;
  * logging's file handlers, and basicConfig(filename=...);
  * sqlite3.connect, shelve.open, dbm.open, urllib.request.urlretrieve,
    numpy's save / savez / savez_compressed / savetxt, and .tofile on anything;
  * a method of these names on any object, since pathlib's arrive on any
    expression (`STATE_FILE.write_text`, `path.parent.mkdir`): write_text,
    write_bytes, touch, mkdir, unlink, rmdir, symlink_to, hardlink_to, chmod,
    lchmod; replace and rename only with exactly one argument (str.replace
    takes two, datetime.replace only keywords); open only with a visible
    write mode;
  * the Win32 file calls (DeleteFile*, MoveFile*, CopyFile*, ReplaceFile*,
    CreateFile*, CreateDirectory*, RemoveDirectory*, SetFileAttributes*,
    CreateHardLink*, CreateSymbolicLink*), matched on the last attribute of
    any chain, as the launch guard matches shell32;
  * Tcl's own file command (this is a Tk app): `.call("file", ...)`.

Every REFERENCE counts, not only calls, through any import alias, so
`f = os.remove` and `partial(shutil.rmtree, p)` are sites too; annotations
don't. `getattr(os, "remove")`, `getattr(shutil, name)` with a name that isn't
a constant, and `importlib.import_module("shutil")` are sites as well.

Writing INTO a handle (`fh.write`, `json.dump(x, fh)`) is not a site: the call
that opened the handle is. Writes made by a program we start (mpv's
watch-later files and log, ffmpeg's .opus) and the Recycle Bin's deletes are
launches: test_process_launches.py lists them with the checks on their paths.

A site is keyed by file and enclosing function (`file::Class.method`), never
by line, and its entry lists the operations that function uses. A second
write in a listed function, or an operation swapped for another, changes
that tuple and fails. Lines appear only in failure messages.

Each entry says which folders it writes in (ROOTS), where the PATH comes from
(SOURCES, the launch guard's six words) and a verdict: `trusted` when every
source is a constant or the app's own path, `checked` when something
untrusted shapes the path and `reason` names the check. A private helper
whose check lives in its caller lists those callers, and a new caller
anywhere fails.

And content: a function that writes a file's content must also put it in
place with os.replace (or a one-argument .replace) in the same function, so a
crash mid-write leaves the old file whole. One that doesn't is listed in
_IN_PLACE with why that's fine; a new one fails until it is.

What it cannot see, written down rather than pretended:
  * a path built in another function and handed in, beyond one call up (the
    `callers` field is for that);
  * Path.open() with its mode in a variable, and a write method on an object
    the AST can't type (Tk's PhotoImage.write);
  * a module imported by a string that isn't a constant, and exec/eval of
    Python source;
  * files outside metis_toolbox/ and writers that aren't Python, and anything
    untracked (sandbox/).

Files come from `git ls-files`, not the disk: a new file is invisible here
until it is staged (CONVENTIONS §10).

In the public snapshot, which leaves Clio out, _drop_absent() drops the lines
naming its files; the private repo keeps them strict (test_process_launches).

Run from the package root:
    python -X utf8 -m unittest tests.test_file_writes
"""

import ast
import re
import unittest
from typing import NamedTuple

from tests.test_process_launches import (SOURCES, VERDICTS, _TRUSTED_SOURCES,
                                         _callers, _drop_absent, _read,
                                         _tracked_runtime_files)

# ── What counts as a write ────────────────────────────────────────────────────

_MODULE_WRITERS: dict[str, frozenset[str]] = {
    "os": frozenset({"remove", "unlink", "rmdir", "removedirs", "rename", "renames",
                     "replace", "makedirs", "mkdir", "utime", "chmod", "lchmod",
                     "chown", "lchown", "truncate", "link", "symlink", "mkfifo",
                     "mknod", "open", "fdopen"}),
    "shutil": frozenset({"rmtree", "move", "copy", "copy2", "copyfile", "copytree",
                         "copymode", "copystat", "chown", "make_archive",
                         "unpack_archive"}),
    "tempfile": frozenset({"mkstemp", "mkdtemp", "NamedTemporaryFile",
                           "TemporaryDirectory", "TemporaryFile",
                           "SpooledTemporaryFile"}),
    "io": frozenset({"open"}),
    "codecs": frozenset({"open"}),
    "wave": frozenset({"open"}),
    "gzip": frozenset({"open", "GzipFile"}),
    "bz2": frozenset({"open", "BZ2File"}),
    "lzma": frozenset({"open", "LZMAFile"}),
    "tarfile": frozenset({"open", "TarFile"}),
    "zipfile": frozenset({"ZipFile"}),
    "logging": frozenset({"FileHandler", "basicConfig"}),
    "logging.handlers": frozenset({"RotatingFileHandler", "TimedRotatingFileHandler",
                                   "WatchedFileHandler"}),
    "sqlite3": frozenset({"connect"}),
    "shelve": frozenset({"open"}),
    "dbm": frozenset({"open"}),
    "urllib.request": frozenset({"urlretrieve"}),
    "numpy": frozenset({"save", "savez", "savez_compressed", "savetxt"}),
}
# These write only in some modes; the value is where a positional mode sits.
# Called without one they read, so only a writing mode (or a mode that isn't a
# constant) makes the call a site; a bare reference is always one.
_MODE_AT = {
    "io.open": 1, "codecs.open": 1, "os.fdopen": 1, "wave.open": 1,
    "gzip.open": 1, "gzip.GzipFile": 1, "bz2.open": 1, "bz2.BZ2File": 1,
    "lzma.open": 1, "lzma.LZMAFile": 1, "tarfile.open": 1, "tarfile.TarFile": 1,
    "zipfile.ZipFile": 1,
}
# A method of these names on any object the AST can't resolve to a module.
_METHOD_WRITERS = frozenset({"write_text", "write_bytes", "touch", "mkdir", "unlink",
                             "rmdir", "symlink_to", "hardlink_to", "chmod", "lchmod",
                             "extract", "extractall", "tofile"})
_ONE_ARG_METHODS = frozenset({"replace", "rename"})      # pathlib's take one
_WIN32_WRITERS = frozenset({
    "DeleteFileW", "DeleteFileA", "MoveFileW", "MoveFileA", "MoveFileExW",
    "MoveFileExA", "MoveFileWithProgressW", "CopyFileW", "CopyFileA", "CopyFileExW",
    "CopyFileExA", "CopyFile2", "ReplaceFileW", "ReplaceFileA", "CreateFileW",
    "CreateFileA", "CreateFile2", "CreateDirectoryW", "CreateDirectoryA",
    "CreateDirectoryExW", "RemoveDirectoryW", "RemoveDirectoryA",
    "SetFileAttributesW", "SetFileAttributesA", "CreateHardLinkW", "CreateHardLinkA",
    "CreateSymbolicLinkW", "CreateSymbolicLinkA", "SHCreateDirectoryExW",
})
_MODE_RE = re.compile(r"[rwxabtU+]{1,4}")

# Operations that write a file's CONTENT where it stands, and the ones that put
# a finished file in place in one step.
_CONTENT_OPS = frozenset({
    "open", "io.open", "codecs.open", "os.open", "os.fdopen", "os.truncate",
    ".write_text", ".write_bytes", ".open", ".tofile",
    "wave.open", "gzip.open", "gzip.GzipFile", "bz2.open", "bz2.BZ2File",
    "lzma.open", "lzma.LZMAFile", "tarfile.open", "tarfile.TarFile",
    "zipfile.ZipFile", "logging.FileHandler", "logging.basicConfig",
    "logging.handlers.RotatingFileHandler", "logging.handlers.TimedRotatingFileHandler",
    "logging.handlers.WatchedFileHandler", "sqlite3.connect", "shelve.open",
    "dbm.open", "urllib.request.urlretrieve", "numpy.save", "numpy.savez",
    "numpy.savez_compressed", "numpy.savetxt", "shutil.copy", "shutil.copy2",
    "shutil.copyfile", "shutil.move",
})
_REPLACE_OPS = frozenset({"os.replace", ".replace"})

# ── Where it writes, where the path comes from, and the verdict ──────────────

ROOTS = frozenset({
    "app folder",              # metis_toolbox/ itself: the per-user state files
    "config/",
    "logs/",
    "artemis_cache/",
    "herodotus_archive/",
    "local_audio/",
    "morpheus_watch_later/",
    "videos/",                 # Clio's voice cache, builds and finished videos
    "%TEMP%",
    "mpv's pipe",              # \\.\pipe\mpv-felhaven: opened "r+b", not a file
})


class Write(NamedTuple):
    ops: tuple[str, ...]         # the write operations this function uses, one per reference
    roots: frozenset[str]        # where they land (ROOTS)
    sources: frozenset[str]      # where the PATH comes from (SOURCES)
    verdict: str                 # "trusted" or "checked" (VERDICTS)
    reason: str                  # why that verdict holds; for "checked", the check
    callers: tuple[str, ...] = ()  # a helper's only callers, when its path is theirs


_ATOMIC = ("tempfile.mkstemp", "os.fdopen", "os.replace", "os.unlink")

_WRITES: dict[str, Write] = {
    # ── the app folder's own state, each through the house's atomic shape ──
    "cerberus.py::_atomic_write_json": Write(
        _ATOMIC, frozenset({"app folder"}), frozenset({"constant", "app path"}),
        "trusted",
        "Cerberus's three files (the PIN check, the DPAPI vault, the Ledger), each a "
        "constant beside this file; mkstemp in the same folder, fsync, os.replace.",
        callers=("cerberus.py::set_pin", "cerberus.py::_vault_write",
                 "cerberus.py::dismiss_alarm", "cerberus.py::_ledger_write")),
    "cerberus.py::retire_vault": Write(
        ("os.rename",), frozenset({"app folder"}), frozenset({"constant", "app path"}),
        "trusted",
        "Renames the vault file beside itself as '<stem>.<kind>-<timestamp>.old'; "
        "kind is one of two words and the stamp comes from the clock. Refuses to "
        "overwrite an existing name."),
    "machine_spirit.py::save": Write(
        _ATOMIC, frozenset({"app folder"}), frozenset({"constant", "app path"}),
        "trusted",
        "machine_spirit_config.json beside this file; mkstemp in the same folder, "
        "os.replace."),
    "machine_spirit.py::revert": Write(
        ("os.unlink",), frozenset({"app folder"}), frozenset({"constant", "app path"}),
        "trusted", "Deletes machine_spirit_config.json, a constant beside this file."),
    "scribe.py::save_data": Write(
        _ATOMIC, frozenset({"app folder"}), frozenset({"constant", "app path"}),
        "trusted", "scribe_data.json beside this file; mkstemp in the same folder, "
        "os.replace."),
    "themis.py::save": Write(
        _ATOMIC, frozenset({"app folder"}), frozenset({"constant", "app path"}),
        "trusted", "felhaven_settings.json beside this file; mkstemp in the same "
        "folder, os.replace."),
    "voices.py::save_pythia_voice": Write(
        _ATOMIC, frozenset({"app folder"}), frozenset({"constant", "app path"}),
        "trusted", "voice_config.json beside this file; mkstemp in the same folder, "
        "os.replace."),
    "panels/pheme_panel.py::_save_sections": Write(
        _ATOMIC, frozenset({"app folder"}), frozenset({"constant", "app path"}),
        "trusted", "pheme_sections.json in the app folder; mkstemp in the same "
        "folder, os.replace."),
    "tools/pheme.py::_atomic_write_json": Write(
        _ATOMIC, frozenset({"app folder"}), frozenset({"constant", "app path"}),
        "trusted", "pheme_read.json in the app folder (the read marks; their URLs "
        "are content, written by json, never part of the path); mkstemp, os.replace.",
        callers=("tools/pheme.py::_save_read",)),
    "tools/ammit.py::_save": Write(
        (".write_text", "os.replace"), frozenset({"app folder"}),
        frozenset({"constant", "app path"}), "trusted",
        "timer_state.json in the app folder, written to '<name>.tmp' beside it, "
        "then os.replace."),
    "tools/argus.py::_save_timeline": Write(
        ("open", "os.replace"), frozenset({"app folder"}),
        frozenset({"constant", "app path"}), "trusted",
        "argus_timeline.json in the app folder, written to '<name>.tmp' beside it, "
        "then os.replace."),
    "tools/plutus.py::_save_events": Write(
        ("open", "os.replace"), frozenset({"app folder"}),
        frozenset({"constant", "app path"}), "trusted",
        "plutus_ledger.json in the app folder, written to '<name>.tmp' beside it, "
        "then os.replace."),
    "tools/euterpe.py::_save": Write(
        ("os.makedirs",) + _ATOMIC, frozenset({"app folder"}),
        frozenset({"constant", "app path"}), "trusted",
        "euterpe_ledger.json in the app folder; mkstemp in the same folder, "
        "os.replace."),
    "tools/euterpe.py::_backup": Write(
        ("shutil.copy2",), frozenset({"app folder"}), frozenset({"constant", "app path"}),
        "trusted", "Copies euterpe_ledger.json to '<name>.bak' beside it, both "
        "constants."),
    "tools/euterpe.py::_bump_mtime": Write(
        ("os.utime",), frozenset({"app folder"}), frozenset({"constant", "app path"}),
        "trusted", "Sets euterpe_ledger.json's modified time; the path is a constant."),
    "tools/euterpe.py::take_base": Write(
        ("tempfile.mkstemp", "os.fdopen", "os.replace"), frozenset({"app folder"}),
        frozenset({"constant", "app path"}), "trusted",
        "'euterpe_ledger.json.base' in the app folder; mkstemp in the same folder, "
        "os.replace."),
    "tools/dev_build_hypatia_catalog.py::_atomic_write_json": Write(
        ("tempfile.mkstemp", "os.fdopen", "os.replace", "os.remove"),
        frozenset({"app folder"}), frozenset({"constant", "app path"}), "trusted",
        "A developer's command, never run by the app: hypatia_stars.json and "
        "hypatia_constellations.json, constants in the app folder; mkstemp, "
        "os.replace."),

    # ── config/ ──
    "sphynx.py::create": Write(
        _ATOMIC, frozenset({"config/"}), frozenset({"constant", "app path"}), "trusted",
        "config/sphynx_data.json, a constant; mkstemp in the same folder, os.replace."),
    "tools/aegis.py::_write_manifest": Write(
        _ATOMIC, frozenset({"config/"}), frozenset({"constant", "app path"}), "trusted",
        "config/bin_manifest.json, a constant, written only by the `record` "
        "command; mkstemp in the same folder, os.replace."),
    "tools/morpheus.py::_save_playlists": Write(
        ("open", "os.replace"), frozenset({"config/"}), frozenset({"constant", "app path"}),
        "trusted", "config/morpheus_playlists.json, written to '<name>.tmp' beside "
        "it, then os.replace. Playlist names and URLs are content, never the path."),

    # ── logs/ ──
    "metis_logging.py::setup_logging": Write(
        (".mkdir", "logging.handlers.RotatingFileHandler"), frozenset({"logs/"}),
        frozenset({"constant", "app path"}), "trusted",
        "logs/<program>.log beside this file; the program name is a constant in "
        "each entry point. The CONTENT carries text from anywhere, which is why the "
        "formatter keeps every record to one parseable line."),
    "tools/morpheus.py::_write_failure_report": Write(
        ("os.makedirs", "open"), frozenset({"logs/"}), frozenset({"constant", "app path"}),
        "trusted", "logs/mpv/last_failure.log, a constant. Holds the failed URL and "
        "mpv's error lines as plain text for a person to read; nothing parses it."),
    "tools/morpheus.py::_discard_mpv_log": Write(
        ("os.remove",), frozenset({"logs/"}), frozenset({"constant", "app path"}),
        "trusted", "Deletes logs/mpv/mpv.log, a constant, after mpv has exited."),
    "tools/morpheus.py::_ensure_mpv": Write(
        ("os.makedirs", "os.makedirs", "open"),
        frozenset({"morpheus_watch_later/", "logs/", "mpv's pipe"}),
        frozenset({"constant", "app path"}), "trusted",
        "Makes the watch-later and mpv log folders (constants; mpv itself writes "
        "inside them, a launch) and opens mpv's IPC pipe, a constant name, 'r+b'."),
    "tools/morpheus.py::_ipc": Write(
        ("open",), frozenset({"mpv's pipe"}), frozenset({"constant"}), "trusted",
        "Opens mpv's IPC pipe, a constant name, 'r+b'; what is written there is "
        "held to known commands by the launch guard."),
    "tools/morpheus.py::_get_props": Write(
        ("open",), frozenset({"mpv's pipe"}), frozenset({"constant"}), "trusted",
        "Opens mpv's IPC pipe, a constant name, 'r+b', to read properties."),

    # ── artemis_cache/: every folder name is area_id(lat, lon) ──
    "tools/artemis.py::_atomic_write_bytes": Write(
        ("os.makedirs",) + _ATOMIC, frozenset({"artemis_cache/"}),
        frozenset({"app path", "owner", "config", "network"}), "checked",
        "Every caller writes a constant file name under CACHE_DIR, in an area "
        "folder named by area_id(lat, lon): rounded integers formatted with n/s/e/w "
        "and p, so no coordinate, typed, saved or geocoded, can add a separator; a "
        "non-finite one raises before a path exists. mkstemp in the same folder, "
        "fsync, os.replace.",
        callers=("tools/artemis.py::write_vec", "tools/artemis.py::write_index",
                 "tools/artemis.py::write_manifest", "tools/artemis.py::ensure_reference",
                 "tools/demeter.py::write_bin", "tools/demeter.py::write_derived")),
    "tools/artemis.py::ingest_area": Write(
        ("os.makedirs",), frozenset({"artemis_cache/"}),
        frozenset({"app path", "owner", "config", "network"}), "checked",
        "Makes '<area>.partial' under CACHE_DIR, where area is area_id(lat, lon), "
        "computed right here: digits, n/s/e/w and p only."),
    "tools/artemis.py::discard_partial": Write(
        ("shutil.rmtree",), frozenset({"artemis_cache/"}),
        frozenset({"app path", "owner", "config", "network"}), "checked",
        "rmtree of '<area>.partial' under CACHE_DIR. It checks nothing itself, so "
        "its only caller must pass area_id()'s output. Measured: rmtree refuses a "
        "folder that is a junction and unlinks one inside without following it.",
        callers=("tools/artemis.py::ingest_area",)),
    "tools/artemis.py::commit_area": Write(
        ("shutil.rmtree", "os.replace"), frozenset({"artemis_cache/"}),
        frozenset({"app path", "owner", "config", "network"}), "checked",
        "Replaces '<area>' with '<area>.partial' under CACHE_DIR. It checks nothing "
        "itself, so its only caller must pass area_id()'s output; an area folder "
        "that is a junction makes rmtree raise and the ingest fail.",
        callers=("tools/artemis.py::ingest_area",)),
    "tools/artemis.py::_carry_terrain": Write(
        ("shutil.copy2",), frozenset({"artemis_cache/"}),
        frozenset({"app path", "owner", "config", "network"}), "checked",
        "Copies terrain.bin from '<area>' into '<area>.partial', both from "
        "area_id() in its only caller.",
        callers=("tools/artemis.py::ingest_area",)),
    "tools/artemis.py::_send_to_recycle_bin": Write(
        ("shutil.rmtree",), frozenset({"artemis_cache/"}), frozenset({"owner", "app path"}),
        "checked",
        "Off Windows only (on Windows the Recycle Bin takes it, a launch): "
        "delete_area(), the only caller, takes a bare area name, never '_reference' "
        "or a '.partial', whose real path's parent must be the cache folder.",
        callers=("tools/artemis.py::delete_area",)),

    # ── herodotus_archive/ ──
    "tools/herodotus.py::_atomic_write": Write(
        (".mkdir",) + _ATOMIC, frozenset({"herodotus_archive/"}),
        frozenset({"llm", "app path"}), "checked",
        "The model names the file. _resolve() in both callers allows a bare name "
        "of letters, digits, space, dot, hyphen and underscore ending in .md, at "
        "most 100 characters, no Windows device name, and its resolved path's "
        "parent must be the archive; every write is confirmed by the owner first. "
        "mkstemp in the same folder, fsync, os.replace.",
        callers=("tools/herodotus.py::write_document",
                 "tools/herodotus.py::edit_document")),
    "tools/herodotus.py::<module>": Write(
        ("tempfile.mkdtemp", "shutil.rmtree"), frozenset({"%TEMP%"}),
        frozenset({"constant", "app path"}), "trusted",
        "The `python -m` demo only: makes a temp archive, points the module at it, "
        "and removes that same folder at the end."),

    # ── local_audio/ and %TEMP% (Echo, Orpheus, Calliope) ──
    "tools/echo.py::text_to_audio": Write(
        ("os.makedirs", "tempfile.mkstemp", "os.remove"),
        frozenset({"local_audio/", "%TEMP%"}), frozenset({"constant", "app path"}),
        "trusted",
        "Makes local_audio/ (a constant) and a temp WAV in %TEMP% with mkstemp, "
        "removed in a finally. The typed name reaches only ffmpeg's output, a "
        "launch, through sanitize_filename()."),
    "tools/echo.py::_encode_opus": Write(
        ("os.remove",), frozenset({"local_audio/"}), frozenset({"owner", "app path"}),
        "checked",
        "Removes a half-written .opus after ffmpeg fails. Its name was typed in the "
        "ECHO tab and reduced by sanitize_filename() in the only caller to a bare "
        "name (no separators, colon or control characters, no device name) under "
        "local_audio/; the path must be absolute or nothing runs.",
        callers=("tools/echo.py::text_to_audio",)),
    "calliope.py::save_wav": Write(
        ("wave.open",), frozenset({"%TEMP%", "videos/"}), frozenset({"app path"}),
        "trusted",
        "Writes the WAV to the path its caller made a moment before with "
        "mkstemp: Echo's in %TEMP%, Clio's beside its target in videos/.",
        callers=("tools/echo.py::text_to_audio", "tools/clio.py::_save_wav_atomic")),
    "tools/clio.py::_save_wav_atomic": Write(
        ("os.makedirs", "tempfile.mkstemp", "os.replace", "os.unlink"),
        frozenset({"videos/"}), frozenset({"constant", "app path", "owner"}),
        "checked",
        "videos/cache/voice/<sha256 hex>.wav, or videos/build/<episode>/"
        "[short-<tag>/]narration.wav where the episode name is the file's "
        "'episode' field and the tag a segment's 'short', each "
        "refused by parse_episode unless it matches _SLUG ([a-z0-9][a-z0-9_-]*): "
        "no separator, no '..', no drive. mkstemp beside it, then os.replace.",
        callers=("tools/clio.py::line_audio", "tools/clio.py::render_voice")),
    "tools/clio.py::_write_json_atomic": Write(
        ("os.makedirs", "tempfile.mkstemp", "os.fdopen", "os.replace", "os.unlink"),
        frozenset({"videos/"}), frozenset({"constant", "app path", "owner"}),
        "checked",
        "videos/build/<episode>/[short-<tag>/]timing.json and face_track.json, "
        "and a capture's "
        "note, videos/captures/<episode>/<clip>.clio.json; the episode name and a "
        "short's tag are _SLUGs and the clip a bare file name, all checked by "
        "parse_episode, so "
        "neither can leave its folder. mkstemp beside it, then os.replace.",
        callers=("tools/clio.py::render_voice", "tools/clio.py::render_face",
                 "tools/clio.py::capture")),
    "tools/clio.py::capture": Write(
        ("os.makedirs", "os.replace"), frozenset({"videos/"}),
        frozenset({"constant", "app path", "owner"}), "checked",
        "Makes videos/captures/<episode>/ and moves ffmpeg's <clip>.part there "
        "onto <clip>; the episode is a _SLUG and the clip a bare name with a "
        "video suffix, both checked by parse_episode. An existing clip is only "
        "replaced when --replace was given."),
    "tools/clio.py::_discard": Write(
        ("os.unlink",), frozenset({"videos/"}),
        frozenset({"constant", "app path", "owner"}), "checked",
        "Removes a half-written <clip>.part beside the clip capture() was "
        "recording; the path is capture()'s own, built as above.",
        callers=("tools/clio.py::capture",)),
    "tools/clio.py::_write_text_atomic": Write(
        ("os.makedirs", "tempfile.mkstemp", "os.fdopen", "os.replace", "os.unlink"),
        frozenset({"videos/"}), frozenset({"constant", "app path", "owner"}),
        "checked",
        "videos/build/<episode>/[short-<tag>/]<video>_graph.txt and "
        "text/<video>/<name>.txt, where <video> is long, short-<tag> or either with "
        "-zones, and a name is a constant (footer, card.0.title...) or a segment id "
        "plus a line number; episode ids, segment ids and tags are _SLUGs checked "
        "by parse_episode. mkstemp beside it, "
        "then os.replace.",
        callers=("tools/clio.py::_video_chain", "tools/clio.py::_encode")),
    "tools/clio.py::render_face": Write(
        ("os.unlink", "os.replace"), frozenset({"videos/"}),
        frozenset({"constant", "app path", "owner"}), "checked",
        "build/<episode>/[short-<tag>/]face.part.mkv, which ffmpeg wrote (a "
        "launch), moved onto face.mkv, or removed when the render failed; the "
        "episode name and a short's tag are _SLUGs."),
    "tools/clio.py::normalize_audio": Write(
        ("os.replace", "os.unlink"), frozenset({"videos/"}),
        frozenset({"constant", "app path", "owner"}), "checked",
        "build/<episode>/[short-<tag>/]narration_norm.part.wav, which ffmpeg wrote "
        "(a launch), moved onto narration_norm.wav, or removed on failure; the "
        "episode name and a short's tag are _SLUGs."),
    "tools/clio.py::quality_check": Write(
        ("os.replace",), frozenset({"videos/"}),
        frozenset({"constant", "app path", "owner"}), "checked",
        "Renames a finished video that failed QC to <stem>.failed-qc.mp4 in the "
        "same folder. The videos are _encode's own outputs under videos/out/ "
        "(episode and tag _SLUGs), handed in by main(); the new name only adds a "
        "constant suffix to the stem.",
        callers=("tools/clio.py::main",)),
    "tools/clio.py::_encode": Write(
        ("os.makedirs", "os.replace", "os.unlink"), frozenset({"videos/"}),
        frozenset({"constant", "app path", "owner"}), "checked",
        "Makes videos/out/<episode>/ and moves ffmpeg's .part.mp4 there onto "
        "<episode>-<name>.mp4, or removes it on failure; the episode name is a "
        "_SLUG and <name> is long, or short-<tag> (the tag a _SLUG one of the "
        "episode's segments carries), either with -zones.",
        callers=("tools/clio.py::compose_short", "tools/clio.py::compose_long")),
    "tools/orpheus.py::_send_to_recycle_bin": Write(
        ("os.remove",), frozenset({"local_audio/"}), frozenset({"owner", "app path"}),
        "checked",
        "Off Windows only (on Windows the Recycle Bin takes it, a launch): the "
        "clicked name must be the bare name of a file in local_audio/ (_safe_path "
        "in delete_file, the only caller).",
        callers=("tools/orpheus.py::delete_file",)),
}
_WRITES = _drop_absent(_WRITES)

# Functions that write a file's content where it stands, and why that's fine.
_IN_PLACE: dict[str, str] = {
    "metis_logging.py::setup_logging":
        "An append-only log; RotatingFileHandler rolls it by renaming at 1 MB.",
    "tools/morpheus.py::_write_failure_report":
        "A diagnostic snapshot, rewritten whole on each failure; a torn one is only "
        "a shorter snapshot.",
    "tools/morpheus.py::_ensure_mpv": "mpv's pipe, not a file.",
    "tools/morpheus.py::_ipc": "mpv's pipe, not a file.",
    "tools/morpheus.py::_get_props": "mpv's pipe, not a file.",
    "calliope.py::save_wav":
        "A fresh mkstemp file its caller made; nothing reads it until it is closed.",
    "tools/euterpe.py::_backup":
        "The .bak copy; the ledger it copies is untouched, and the next hand edit "
        "takes a new one.",
    "tools/artemis.py::_carry_terrain":
        "Copies into the '.partial' folder, which becomes the area only through "
        "commit_area's single os.replace.",
}

# ── The detector ──────────────────────────────────────────────────────────────


class Site(NamedTuple):
    qualname: str    # enclosing Class.function, or "<module>"
    nested: bool     # the function is defined inside another function
    op: str          # what was referenced, e.g. "os.replace" or ".write_text"
    line: int


def _aliases(tree):
    """Every module name bound by an import, a writer function imported by
    name, and star imports from writer modules. Bound anywhere in the file,
    scope-blind: it can only over-report, and an over-report is a loud failure
    someone reviews."""
    modules, names, star = {}, {}, []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    modules[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    modules[top] = top
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                full = f"{node.module}.{alias.name}"
                if alias.name == "*":
                    if node.module in _MODULE_WRITERS:
                        star.append(node)
                elif alias.name in _MODULE_WRITERS.get(node.module, ()):
                    names[alias.asname or alias.name] = full
                elif full in _MODULE_WRITERS:              # from logging import handlers
                    modules[alias.asname or alias.name] = full
    return modules, names, star


def _writes(mode):
    """A mode writes when it is a constant holding w, a, x or +, or isn't a
    constant at all (it could be anything)."""
    if mode is None:
        return False
    if isinstance(mode, ast.Constant):
        return isinstance(mode.value, str) and any(c in mode.value for c in "wax+")
    return True


def _mode(call, position):
    for kw in call.keywords:
        if kw.arg == "mode":
            return kw.value
    return call.args[position] if len(call.args) > position else None


class _Finder(ast.NodeVisitor):
    """Walks one module, recording each write reference with its scope.
    Annotations are skipped: `x: zipfile.ZipFile` names a type, not a write."""

    def __init__(self, modules, names):
        self.modules = modules
        self.names = names
        self.scope = []          # [(name, is_function)]
        self.found = []
        self.decided = set()     # ids of call targets visit_Call already judged

    def _hit(self, op, node):
        qual = ".".join(name for name, _ in self.scope) or "<module>"
        nested = any(is_fn for _, is_fn in self.scope[:-1])
        self.found.append(Site(qual, nested, op, node.lineno))

    def _dotted(self, node):
        """The module an expression names (`os`, `logging.handlers`), or None."""
        if isinstance(node, ast.Name):
            return self.modules.get(node.id)
        if isinstance(node, ast.Attribute):
            head = self._dotted(node.value)
            return f"{head}.{node.attr}" if head else None
        return None

    def _function(self, node):
        for expr in node.decorator_list:
            self.visit(expr)
        for expr in node.args.defaults + [d for d in node.args.kw_defaults if d]:
            self.visit(expr)
        self.scope.append((node.name, True))
        for stmt in node.body:
            self.visit(stmt)
        self.scope.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = _function

    def visit_ClassDef(self, node):
        for expr in node.decorator_list + node.bases:
            self.visit(expr)
        for kw in node.keywords:
            self.visit(kw.value)
        self.scope.append((node.name, False))
        for stmt in node.body:
            self.visit(stmt)
        self.scope.pop()

    def visit_AnnAssign(self, node):
        self.visit(node.target)
        if node.value is not None:
            self.visit(node.value)

    def visit_Attribute(self, node):
        if isinstance(node.ctx, ast.Load) and id(node) not in self.decided:
            module = self._dotted(node.value)
            if module is not None:
                if node.attr in _MODULE_WRITERS.get(module, ()):
                    self._hit(f"{module}.{node.attr}", node)
            elif node.attr in _METHOD_WRITERS:
                self._hit(f".{node.attr}", node)
            if node.attr in _WIN32_WRITERS:
                self._hit(node.attr, node)
        self.generic_visit(node)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and id(node) not in self.decided:
            if node.id in self.names:
                self._hit(self.names[node.id], node)
            elif node.id == "open":
                self._hit("open", node)          # the builtin, handed on: any mode
            elif node.id in _WIN32_WRITERS:
                self._hit(node.id, node)

    def visit_Call(self, node):
        func = node.func
        first = node.args[0] if node.args else None
        first_str = (first.value if isinstance(first, ast.Constant)
                     and isinstance(first.value, str) else None)

        if isinstance(func, ast.Name) and func.id not in self.names and func.id == "open":
            self.decided.add(id(func))
            if _writes(_mode(node, 1)):
                self._hit("open", node)
        elif isinstance(func, ast.Name) and self.names.get(func.id) in _MODE_AT:
            self.decided.add(id(func))
            full = self.names[func.id]
            if _writes(_mode(node, _MODE_AT[full])):
                self._hit(full, node)
        elif isinstance(func, ast.Attribute):
            module = self._dotted(func.value)
            full = f"{module}.{func.attr}" if module else None
            if full in _MODE_AT:
                self.decided.add(id(func))
                if _writes(_mode(node, _MODE_AT[full])):
                    self._hit(full, node)
            elif full == "logging.basicConfig":
                self.decided.add(id(func))
                if any(kw.arg == "filename" for kw in node.keywords):
                    self._hit(full, node)
            elif module is None and func.attr == "open":
                if any(kw.arg == "mode" for kw in node.keywords):
                    writes = _writes(_mode(node, 99))
                else:
                    writes = any(isinstance(a, ast.Constant) and isinstance(a.value, str)
                                 and _MODE_RE.fullmatch(a.value) and _writes(a)
                                 for a in node.args)
                if writes:
                    self._hit(".open", node)
            elif module is None and func.attr in _ONE_ARG_METHODS:
                if len(node.args) == 1 and not node.keywords:
                    self._hit(f".{func.attr}", node)
            elif module is None and func.attr == "call" and first_str == "file":
                self._hit("tcl file", node)

        # getattr(shutil, "rmtree"), or getattr(os, <anything not a constant>)
        if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2:
            module = self._dotted(node.args[0])
            name = node.args[1]
            named = (name.value if isinstance(name, ast.Constant)
                     and isinstance(name.value, str) else None)
            if module in _MODULE_WRITERS:
                if named is None:
                    self._hit(f"getattr({module}, ?)", node)
                elif named in _MODULE_WRITERS[module]:
                    self._hit(f"{module}.{named}", node)
            if named in _WIN32_WRITERS:
                self._hit(named, node)

        # a writer module imported by a string
        called = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        if called in ("__import__", "import_module") and first_str is not None:
            if first_str in _MODULE_WRITERS or first_str.split(".")[0] in {
                    m.split(".")[0] for m in _MODULE_WRITERS}:
                self._hit(f"import {first_str}", node)
        self.generic_visit(node)


def write_sites(source):
    """Every write reference in `source`, as Site tuples."""
    tree = ast.parse(source)
    modules, names, star = _aliases(tree)
    finder = _Finder(modules, names)
    finder.visit(tree)
    for node in star:
        finder.found.append(Site("<module>", False, f"from {node.module} import *",
                                 node.lineno))
    return finder.found


def _found():
    """{"file::qualname": {"ops": [...], "lines": [...], "nested": bool}}"""
    result = {}
    for rel in _tracked_runtime_files():
        for site in write_sites(_read(rel)):
            entry = result.setdefault(f"{rel}::{site.qualname}",
                                      {"ops": [], "lines": [], "nested": site.nested})
            entry["ops"].append(site.op)
            entry["lines"].append(site.line)
    return result


def _writes_in_place(ops):
    """The function writes content and never puts it in place in one step."""
    return bool(set(ops) & _CONTENT_OPS) and not set(ops) & _REPLACE_OPS


# ── The tests ─────────────────────────────────────────────────────────────────

class TheDetectorSeesEverySpelling(unittest.TestCase):
    """The guard is only as good as write_sites(); test it on its own."""

    def _ops(self, src):
        return [site.op for site in write_sites(src)]

    def test_these_are_writes(self):
        cases = (
            ("open(p, 'w')", "open"),
            ("open(p, 'a', encoding='utf-8')", "open"),
            ("open(p, 'r+b')", "open"),
            ("open(p, mode='x')", "open"),
            ("open(p, mode)", "open"),                      # a mode that isn't a constant
            ("f = open", "open"),                           # handed on: any mode
            ("import io\nio.open(p, 'wb')", "io.open"),
            ("import codecs\ncodecs.open(p, 'w', 'utf-8')", "codecs.open"),
            ("import os\nos.fdopen(fd, 'w')", "os.fdopen"),
            ("import os\nos.fdopen(fd, m)", "os.fdopen"),
            ("import os\nos.open(p, os.O_RDONLY)", "os.open"),
            ("import os\nos.replace(a, b)", "os.replace"),
            ("import os\nos.remove(p)", "os.remove"),
            ("import os as _os\n_os.unlink(p)", "os.unlink"),
            ("from os import remove\nremove(p)", "os.remove"),
            ("from os import rename as mv\nmv(a, b)", "os.rename"),
            ("import os\nf = os.remove", "os.remove"),
            ("import os, functools\nfunctools.partial(os.unlink, p)", "os.unlink"),
            ("import os.path\nos.makedirs(d)", "os.makedirs"),
            ("import os\nos.utime(p, None)", "os.utime"),
            ("import shutil\nshutil.rmtree(d)", "shutil.rmtree"),
            ("import shutil as sh\nsh.copy2(a, b)", "shutil.copy2"),
            ("from shutil import move\nmove(a, b)", "shutil.move"),
            ("import tempfile\ntempfile.mkstemp(dir=d)", "tempfile.mkstemp"),
            ("import tempfile\ntempfile.TemporaryDirectory()", "tempfile.TemporaryDirectory"),
            ("import wave\nwave.open(p, 'wb')", "wave.open"),
            ("import gzip\ngzip.open(p, 'wb')", "gzip.open"),
            ("import tarfile\ntarfile.open(p, mode='w:gz')", "tarfile.open"),
            ("import zipfile\nzipfile.ZipFile(p, 'w')", "zipfile.ZipFile"),
            ("from zipfile import ZipFile\nZipFile(p, 'a')", "zipfile.ZipFile"),
            ("import gzip\nf = gzip.open", "gzip.open"),
            ("zf.extractall(d)", ".extractall"),
            ("import logging\nlogging.FileHandler(p)", "logging.FileHandler"),
            ("import logging.handlers\nlogging.handlers.RotatingFileHandler(p)",
             "logging.handlers.RotatingFileHandler"),
            ("from logging import handlers\nhandlers.WatchedFileHandler(p)",
             "logging.handlers.WatchedFileHandler"),
            ("from logging.handlers import RotatingFileHandler\nRotatingFileHandler(p)",
             "logging.handlers.RotatingFileHandler"),
            ("import logging\nlogging.basicConfig(filename=p)", "logging.basicConfig"),
            ("import sqlite3\nsqlite3.connect(p)", "sqlite3.connect"),
            ("import shelve\nshelve.open(p)", "shelve.open"),
            ("import urllib.request\nurllib.request.urlretrieve(u, p)",
             "urllib.request.urlretrieve"),
            ("import numpy as np\nnp.save(p, a)", "numpy.save"),
            ("a.tofile(p)", ".tofile"),
            ("STATE_FILE.write_text(t)", ".write_text"),
            ("Path(p).write_bytes(b)", ".write_bytes"),
            ("path.parent.mkdir(parents=True)", ".mkdir"),
            ("p.unlink(missing_ok=True)", ".unlink"),
            ("p.touch()", ".touch"),
            ("p.symlink_to(t)", ".symlink_to"),
            ("p.replace(target)", ".replace"),
            ("p.rename(target)", ".rename"),
            ("Path(p).open('w')", ".open"),
            ("p.open(mode='a')", ".open"),
            ("p.open(mode=m)", ".open"),
            ("zf.open(name, 'w')", ".open"),
            ("ctypes.windll.kernel32.DeleteFileW(p)", "DeleteFileW"),
            ("k = ctypes.WinDLL('kernel32')\nk.MoveFileExW(a, b, 1)", "MoveFileExW"),
            ("CreateFileW(p, 0x40000000, 0, None, 2, 0, None)", "CreateFileW"),
            ("getattr(kernel32, 'RemoveDirectoryW')", "RemoveDirectoryW"),
            ("root.tk.call('file', 'delete', p)", "tcl file"),
            ("import shutil\ngetattr(shutil, 'rmtree')(d)", "shutil.rmtree"),
            ("import os\ngetattr(os, name)(p)", "getattr(os, ?)"),
            ("import importlib\nimportlib.import_module('shutil')", "import shutil"),
            ("__import__('tempfile')", "import tempfile"),
            ("from shutil import *\nrmtree(d)", "from shutil import *"),
        )
        for src, expected in cases:
            with self.subTest(src=src):
                self.assertIn(expected, self._ops(src))

    def test_these_are_not(self):
        for src in (
                "open(p)",
                "open(p, 'rb')",
                "open(p, encoding='utf-8')",
                "open(p, mode='r')",
                "import io\nio.open(p)",
                "import os\nos.fdopen(fd)",
                "import os\nos.path.join(a, b)",
                "import os\nos.path.exists(p)",
                "import os\nos.listdir(d)",
                "import os\nos.stat(p)",
                "import os\nos.fsync(fd)",
                "import os\nos.close(fd)",
                "import shutil\nshutil.which('mpv')",
                "import tempfile\ntempfile.gettempdir()",
                "import wave\nwave.open(p)",
                "import wave\nwave.open(p, 'rb')",
                "import gzip\ngzip.compress(b)",
                "import zipfile\nzipfile.ZipFile(io.BytesIO(raw))",
                "import zipfile\nzipfile.ZipFile(p, 'r')",
                "import logging\nlogging.basicConfig(level=logging.INFO)",
                "import logging\nlogging.getLogger('METIS.x')",
                "import json\njson.dump(data, fh)",
                "fh.write(text)",
                "text.replace('a', 'b')",
                "text.replace('a', 'b', 1)",
                "dt.replace(tzinfo=None)",
                "dt.replace(hour=0, minute=0)",
                "import os\nos.environ.get('X')",
                "webbrowser.open(u)",                 # no import: not a module, no mode
                "import webbrowser\nwebbrowser.open(u, 2)",
                "_OPENER.open(req, timeout=5)",
                "zf.open(members[0])",
                "self._canvas.move('world', dx, dy)",
                "sock.connect(addr)",
                "themis.save()",
                "root.tk.call('wm', 'attributes', '.', '-topmost', 1)",
                "import zipfile\ndef f(zf: zipfile.ZipFile) -> None: ...",
                "import zipfile\nzf: zipfile.ZipFile",
                "import importlib\nimportlib.import_module(module_path)",
                "import subprocess\nflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)",
        ):
            with self.subTest(src=src):
                self.assertEqual(self._ops(src), [])

    def test_a_site_is_keyed_by_its_enclosing_function(self):
        src = ("import os\n"
               "class Panel:\n"
               "    def build(self):\n"
               "        def _save(e):\n"
               "            os.replace(a, b)\n"
               "    def wipe(self):\n"
               "        os.remove(p)\n"
               "def helper():\n"
               "    open(p, 'w')\n"
               "os.makedirs(d)\n")
        sites = {(s.qualname, s.nested, s.op) for s in write_sites(src)}
        self.assertEqual(sites, {
            ("Panel.build._save", True, "os.replace"),
            ("Panel.wipe", False, "os.remove"),
            ("helper", False, "open"),
            ("<module>", False, "os.makedirs"),
        })


class EveryWriteIsReviewed(unittest.TestCase):

    def test_no_write_is_unreviewed(self):
        found = _found()
        new = {key: f"{', '.join(v['ops'])} (line {', '.join(map(str, v['lines']))})"
               for key, v in found.items() if key not in _WRITES}
        self.assertFalse(
            new,
            "These functions create, change, move or delete files, and nobody has "
            "reviewed them. For each: trace where its PATH comes from (constant, app "
            "path, owner, config, network, llm), make sure nothing untrusted can add "
            "a separator, '..', a drive or a device name, or point a delete outside "
            f"its folder, then add its Write(...) line to _WRITES: {new}")

    def test_no_entry_is_stale(self):
        found = _found()
        stale = sorted(key for key in _WRITES if key not in found)
        self.assertFalse(
            stale,
            f"{stale} no longer write anything (or were renamed): delete or move "
            "their lines. A stale line would let the next write in that place "
            "through unreviewed.")

    def test_each_entry_names_exactly_the_operations_its_function_uses(self):
        found = _found()
        for key, entry in _WRITES.items():
            if key not in found:
                continue        # test_no_entry_is_stale reports it
            with self.subTest(key=key):
                self.assertEqual(
                    sorted(found[key]["ops"]), sorted(entry.ops),
                    f"{key} now uses {found[key]['ops']} (lines {found[key]['lines']}); "
                    "a write was added or swapped. Trace its path again and update "
                    "the line.")

    def test_every_entry_is_filled_in(self):
        for key, entry in _WRITES.items():
            with self.subTest(key=key):
                self.assertTrue(entry.ops)
                self.assertTrue(entry.roots)
                self.assertTrue(entry.roots <= ROOTS,
                                f"unknown roots {set(entry.roots - ROOTS)}")
                self.assertTrue(entry.sources)
                self.assertTrue(entry.sources <= SOURCES,
                                f"unknown sources {set(entry.sources - SOURCES)}")
                self.assertIn(entry.verdict, VERDICTS)
                self.assertGreater(len(entry.reason.split()), 5)

    def test_a_verdict_agrees_with_its_sources(self):
        for key, entry in _WRITES.items():
            with self.subTest(key=key):
                untrusted = entry.sources - _TRUSTED_SOURCES
                if entry.verdict == "trusted":
                    self.assertFalse(
                        untrusted, f"{key} is 'trusted' but takes {set(untrusted)}: "
                        "that is 'checked', and its reason must name the check")
                else:
                    self.assertTrue(
                        untrusted, f"{key} is 'checked' but every source is trusted")

    def test_a_checked_helper_names_its_callers(self):
        """A private, non-nested helper marked 'checked' gets its path from a
        caller, so the check lives up there; listing the callers is what lets
        the next test notice a caller that skips it."""
        found = _found()
        for key, entry in _WRITES.items():
            leaf = key.split("::")[1].rsplit(".", 1)[-1]
            if (entry.verdict == "checked" and leaf.startswith("_")
                    and key in found and not found[key]["nested"]):
                with self.subTest(key=key):
                    self.assertTrue(entry.callers, f"{key}: list its callers")

    def test_listed_callers_are_the_only_callers(self):
        for key, entry in _WRITES.items():
            if not entry.callers:
                continue
            with self.subTest(key=key):
                self.assertEqual(
                    _callers(key), set(entry.callers),
                    f"{key} is called from somewhere new. Its path is built by its "
                    "callers; check the new one builds it the same way, then list it.")


class ContentIsReplacedNotRewritten(unittest.TestCase):
    """A file's content is written beside it and put in place with one
    os.replace, so a crash mid-write leaves the old file whole (CONVENTIONS
    §9). The few that write where they stand say why that's fine."""

    def test_content_is_replaced_or_listed(self):
        found = _found()
        torn = {key: v["ops"] for key, v in found.items()
                if _writes_in_place(v["ops"]) and key not in _IN_PLACE}
        self.assertFalse(
            torn,
            "These functions write a file's content where it stands, so a crash "
            "mid-write leaves it torn. Write to a temp file in the same folder and "
            "os.replace it into place (the house shape: mkstemp, write, os.replace), "
            f"or, if a torn file is harmless, list it in _IN_PLACE with why: {torn}")

    def test_no_in_place_line_is_stale(self):
        found = _found()
        stale = sorted(key for key in _IN_PLACE
                       if key not in found or not _writes_in_place(found[key]["ops"]))
        self.assertFalse(
            stale,
            f"{stale} no longer write content in place (or were renamed): delete "
            "their _IN_PLACE lines, or the next in-place write there passes unread.")

    def test_every_in_place_reason_is_filled_in(self):
        for key, reason in _IN_PLACE.items():
            with self.subTest(key=key):
                self.assertGreater(len(reason.split()), 3)


if __name__ == "__main__":
    unittest.main()
