"""
test_process_launches.py — every place Felhaven starts a program, or hands a
name to the shell, is listed here with where its arguments come from.

A program reads an argument that starts with "-" as an option, and the shell's
"open" RUNS an .exe, .bat or .lnk it is handed. So each such call is a place
where a value from the network, the model or a config file could become a
command. This guard parses every tracked runtime module and finds them all.
Each one must have a line in _LAUNCHES, written by someone who traced its
arguments; a new one fails until that is done, and a stale line fails too.

What counts (CONVENTIONS §12): something outside Python acts on a name we hand
it. That is
  * a program: subprocess.*, os.system / popen / exec* / spawn* /
    posix_spawn*, asyncio's subprocesses, pty.spawn, and the Win32
    CreateProcess* / WinExec;
  * the shell's open verb: os.startfile, webbrowser.open*, ShellExecute*;
  * a shell file operation: SHFileOperation* (the Recycle Bin);
  * Tcl's own `exec` (this is a Tk app): `.call("exec", ...)`, a `.call` whose
    first argument isn't a constant, and any `.eval(...)`;
  * and, in its own test class, every command written to mpv's IPC pipe: mpv's
    `run` and `subprocess` commands start programs and `load-script` runs code,
    so only the commands and properties Morpheus uses today are allowed.

Every REFERENCE counts, not only calls, so `f = subprocess.run` and
`partial(os.startfile, p)` are sites too. Every spelling of the import counts:
`import subprocess as sp`, `from subprocess import run as r`,
`from webbrowser import open`. Win32 names are matched on the last attribute
of any chain (`ctypes.windll.shell32.X`, or `_sh = ...; _sh.X`), so a shell32
query like IsUserAnAdmin is not a launch. `getattr(subprocess, "run")`,
`getattr(os, name)` with a name that isn't a constant, and
`importlib.import_module("subprocess")` are sites as well.

A site is keyed by file and enclosing function (`file::Class.method`), never
by line, and its entry lists the launchers that function uses. A second call
in a listed function, or a launcher swapped for another, changes that tuple
and fails. Lines appear only in failure messages.

Each entry says which program runs, where its arguments come from (SOURCES),
and a verdict: `trusted` when every source is a constant or the app's own
path, `checked` when something untrusted arrives and `reason` names the check.
A private helper whose check lives in its caller lists those callers, and a
new caller anywhere fails: neither this guard nor semgrep (which looks inside
one function at a time) would otherwise see a second caller skip the check.

What it cannot see, written down rather than pretended:
  * a module imported by a string that isn't a constant, and exec/eval of
    Python source;
  * a changed argument INSIDE a call that is already listed (semgrep's
    tripwire and review are for that);
  * files outside metis_toolbox/ and launchers that aren't Python
    (Felhaven.bat, publish-snapshot.sh), and anything untracked (sandbox/).

Files come from `git ls-files`, not the disk: a new file is invisible here
until it is staged (CONVENTIONS §10).

The public snapshot leaves out what publish/private_paths.txt lists (Clio), so
there a line naming a missing file is expected, and _drop_absent() drops it.
Only there: the private repo tracks publish/, and keeps every line strict.

Run from the package root:
    python -X utf8 -m unittest tests.test_process_launches
"""

import ast
import os
import subprocess
import unittest
from typing import NamedTuple

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── What counts as a launch ───────────────────────────────────────────────────

_MODULE_LAUNCHERS: dict[str, frozenset[str]] = {
    "subprocess": frozenset({"run", "Popen", "call", "check_call", "check_output",
                             "getoutput", "getstatusoutput"}),
    "os": frozenset({"system", "popen", "startfile",
                     "execl", "execle", "execlp", "execlpe",
                     "execv", "execve", "execvp", "execvpe",
                     "spawnl", "spawnle", "spawnlp", "spawnlpe",
                     "spawnv", "spawnve", "spawnvp", "spawnvpe",
                     "posix_spawn", "posix_spawnp"}),
    "webbrowser": frozenset({"open", "open_new", "open_new_tab"}),
    "asyncio": frozenset({"create_subprocess_exec", "create_subprocess_shell"}),
    "pty": frozenset({"spawn"}),
}
_WIN32_LAUNCHERS = frozenset({
    "ShellExecuteW", "ShellExecuteA", "ShellExecuteExW", "ShellExecuteExA",
    "SHFileOperationW", "SHFileOperationA",
    "CreateProcessW", "CreateProcessA", "CreateProcessAsUserW",
    "CreateProcessWithLogonW", "CreateProcessWithTokenW", "WinExec",
})

# ── Where an argument comes from, and what the verdict may be ────────────────

SOURCES = frozenset({
    "constant",   # a literal in the code
    "app path",   # a path the code builds from its own folders
    "owner",      # typed or clicked by the person at the keyboard
    "config",     # read from a file in config/ (hand-editable, per-user)
    "network",    # anything a server sent
    "llm",        # an argument the model chose
})
_TRUSTED_SOURCES = frozenset({"constant", "app path"})
VERDICTS = ("trusted", "checked")


class Launch(NamedTuple):
    calls: tuple[str, ...]       # the launchers this function uses, one per reference
    program: str                 # what runs, or what the shell is handed to
    sources: frozenset[str]      # where its arguments come from (SOURCES)
    verdict: str                 # "trusted" or "checked" (VERDICTS)
    reason: str                  # why that verdict holds; for "checked", the check
    callers: tuple[str, ...] = ()  # a helper's only callers, when its check is theirs


_LAUNCHES: dict[str, Launch] = {
    "tools/morpheus.py::_ensure_mpv": Launch(
        ("subprocess.Popen",),
        "mpv, headless (bin/, vouched for by aegis)",
        frozenset({"constant", "app path"}), "trusted",
        "Every option is a literal; the paths are the app's own (the pipe, the "
        "watch-later folder, mpv's log) and aegis's resolved yt-dlp. URLs reach "
        "mpv later, over IPC, through play()."),
    "tools/morpheus.py::search": Launch(
        ("subprocess.run",),
        "yt-dlp (bin/, vouched for by aegis)",
        frozenset({"llm", "owner", "constant"}), "checked",
        "The query (play_music's argument, or the search box) is embedded in "
        "'ytsearch{n}:{query}', so the argument always starts with 'ytsearch' "
        "and is never read as an option; argv is a list, no shell."),
    "tools/morpheus.py::describe": Launch(
        ("subprocess.run",),
        "yt-dlp (bin/, vouched for by aegis)",
        frozenset({"network", "constant"}), "checked",
        "A search row's url, from yt-dlp's own JSON. It must fullmatch a YouTube "
        "watch URL, and yt-dlp gets the URL rebuilt from its 11-character id, "
        "after '--'."),
    "tools/echo.py::_encode_opus": Launch(
        ("subprocess.run",),
        "ffmpeg (bin/, vouched for by aegis)",
        frozenset({"owner", "app path", "constant"}), "checked",
        "The output name is typed in the ECHO tab and reduced by "
        "sanitize_filename() to a bare name under local_audio/; the WAV is a "
        "mkstemp path. Both must be absolute or nothing runs, so neither can "
        "start with '-' or name a protocol.",
        callers=("tools/echo.py::text_to_audio",)),
    "tools/clio.py::render_face": Launch(
        ("subprocess.Popen", "subprocess.Popen"),
        "the face renderer (this Python, -m panels.clio_face) piped into ffmpeg "
        "(bin/, vouched for by aegis)",
        frozenset({"constant", "app path", "owner"}), "checked",
        "The renderer is sys.executable with literal flags and module name. Its one "
        "argument, the track, and ffmpeg's output are paths under videos/build/, "
        "anchored to __file__ so absolute, in a folder named by the episode (and, for "
        "a short, short-<tag>), names parse_episode refuses unless each is a _SLUG: "
        "neither path can start with '-' or "
        "name a protocol. ffmpeg's input is '-' (the pipe); the rest are literals "
        "and the face's constant size and rate."),
    "tools/clio.py::capture": Launch(
        ("subprocess.Popen",),
        "ffmpeg's gdigrab (bin/, vouched for by aegis), recording one window",
        frozenset({"constant", "app path", "owner", "config"}), "checked",
        "The window title is clio_config's window_title (one line, checked by "
        "_config_text) and is passed as 'title=<it>', so it can't be read as an "
        "option; it names a window to record, never a file or program. The output "
        "is videos/captures/<episode>/<clip>.part, absolute (anchored to "
        "__file__), the episode a _SLUG and the clip a bare name ending .mp4, "
        ".mkv or .mov, both checked by parse_episode. The rest are literals and "
        "numbers."),
    "tools/clio.py::_run_ffmpeg": Launch(
        ("subprocess.run",),
        "ffmpeg (bin/, vouched for by aegis)",
        frozenset({"constant", "app path", "owner"}), "checked",
        "Every path arrives as a Path and is refused unless absolute, so none can be "
        "read as an option or a protocol; videos/ paths carry the episode name, a "
        "_SLUG, and a capture's clip name, a bare file name parse_episode checked. "
        "Every str is a caller's option literal or a number it formatted. "
        "Script text never reaches argv: drawtext reads it from textfiles named in "
        "a filtergraph file, with expansion=none.",
        callers=("tools/clio.py::measure_loudness", "tools/clio.py::normalize_audio",
                 "tools/clio.py::_encode", "tools/clio.py::final_hold")),
    "tools/orpheus.py::_probe_duration": Launch(
        ("subprocess.run",),
        "ffmpeg (bin/, vouched for by aegis)",
        frozenset({"app path", "constant"}), "trusted",
        "A file name listed from local_audio/, joined to that folder's absolute "
        "path; a path that isn't absolute is refused before ffmpeg runs.",
        callers=("tools/orpheus.py::_file_rows",)),
    "tools/orpheus.py::_decode": Launch(
        ("subprocess.run",),
        "ffmpeg (bin/, vouched for by aegis)",
        frozenset({"owner", "app path", "constant"}), "checked",
        "The file clicked in the ORPHEUS tab must be the bare name of a file in "
        "local_audio/ (_safe_path in play_file), joined to that folder's "
        "absolute path; a path that isn't absolute is refused before ffmpeg runs.",
        callers=("tools/orpheus.py::play_file",)),
    "tools/orpheus.py::_send_to_recycle_bin": Launch(
        ("SHFileOperationW",),
        "the Recycle Bin (shell32 SHFileOperationW)",
        frozenset({"owner", "app path"}), "checked",
        "The clicked name must be the bare name of a file in local_audio/ "
        "(_safe_path in delete_file, the only caller); pFrom is double-NUL "
        "terminated.",
        callers=("tools/orpheus.py::delete_file",)),
    "tools/artemis.py::_send_to_recycle_bin": Launch(
        ("SHFileOperationW",),
        "the Recycle Bin (shell32 SHFileOperationW), a whole folder",
        frozenset({"owner", "app path"}), "checked",
        "delete_area(), the only caller, takes a bare area name only, never "
        "'_reference' or a '.partial', and its real path's parent must be the "
        "cache folder, so a junction can't lead out.",
        callers=("tools/artemis.py::delete_area",)),
    "tools/aegis.py::_ytdlp_version": Launch(
        ("subprocess.run",),
        "bin/yt-dlp.exe --version (the record command, never at runtime)",
        frozenset({"app path", "constant"}), "trusted",
        "Runs only after record_ytdlp(), its only caller, found the file's hash "
        "in the latest release's published SHA2-256SUMS, so nothing unvouched "
        "runs.",
        callers=("tools/aegis.py::record_ytdlp",)),
    "tools/aether.py::_check_wifi": Launch(
        ("subprocess.run",),
        "netsh wlan show interfaces (System32)",
        frozenset({"constant"}), "trusted",
        "Constants only. A bare name: planting a netsh earlier on the search "
        "path needs write access to the app folder, which can edit the code."),
    "tools/argus.py::_firewall": Launch(
        ("subprocess.run",),
        "netsh advfirewall show allprofiles state (System32)",
        frozenset({"constant"}), "trusted",
        "Constants only; the bare name is the same case as Aether's netsh."),
    "tools/argus.py::_dns": Launch(
        ("subprocess.run",),
        "ipconfig /displaydns (System32)",
        frozenset({"constant"}), "trusted",
        "Constants only; the bare name is the same case as Aether's netsh."),
    "sphynx_panel.py::SphynxPanel._launch_felhaven": Launch(
        ("subprocess.Popen",),
        "this Python, running felhaven.py",
        frozenset({"constant", "app path"}), "trusted",
        "sys.executable and the felhaven.py beside this file, with the app "
        "folder as the working directory."),
    "panels/daedalus_panel.py::DaedalusPanel._open_in_notepad": Launch(
        ("subprocess.Popen",),
        "notepad.exe (System32)",
        frozenset({"app path"}), "trusted",
        "A module path from Daedalus's own map of the source tree, checked again "
        "to be an existing .py under the app folder. Never os.startfile, which "
        "would RUN a .py."),
    "panels/cerberus_panel.py::_open_path": Launch(
        ("os.startfile", "subprocess.Popen", "subprocess.Popen"),
        "the default editor for a .json, or the file explorer for the config "
        "folder (open / xdg-open off Windows)",
        frozenset({"config", "app path"}), "checked",
        "Custody manifest names: custody_open() needs the session and an entry "
        "that is listed, resolves inside config/, ends in .json and exists; the "
        "folder is the manifest's config_dir, resolved inside config/ too. "
        "startfile RUNS an .exe or .lnk, so nothing else may reach it.",
        callers=("panels/cerberus_panel.py::CerberusPanel._open_config",
                 "panels/cerberus_panel.py::CerberusPanel._open_folder")),
    "panels/pheme_panel.py::PhemePanel._build_story_row._open": Launch(
        ("webbrowser.open",),
        "the default browser",
        frozenset({"network"}), "checked",
        "A feed item's link. Only links starting http:// or https:// are "
        "clickable (case-sensitive, so anything else fails closed), and "
        "Python's webbrowser refuses a leading '-' itself."),
}

# ── mpv's IPC pipe ────────────────────────────────────────────────────────────
# Commands Morpheus may write, each with why it is harmless. mpv also has `run`
# and `subprocess` (start a program) and `load-script` (run code), and options
# that name programs (script-opts sets the yt-dlp the ytdl hook runs), so the
# commands that change state are pinned to the properties and options used.
_MPV_COMMANDS: dict[str, str] = {
    "loadfile": "plays a URL that play() has checked",
    "stop": "stops playback",
    "quit": "asks mpv to exit",
    "write-watch-later-config": "saves the resume position",
    "playlist-next": "moves within the loaded playlist",
    "playlist-prev": "moves within the loaded playlist",
    "get_property": "reads only",
    "cycle": "toggles one of _MPV_WRITABLE",
    "set_property": "sets one of _MPV_WRITABLE",
}
_MPV_WRITABLE = frozenset({"pause", "volume"})
_MPV_LOADFILE_OPTIONS = frozenset({"start"})
_MPV_PIPE_OWNER = "tools/morpheus.py"

# ── The semgrep tripwire's sinks ──────────────────────────────────────────────
# tests/semgrep/felhaven_sinks.yml traces untrusted values into these same
# launchers. Its sinks block is GENERATED from the tables above and held equal
# to them here, by text: no YAML parser is involved (none is in the lock, and a
# guard that reads a file differently from its real reader can pass a file the
# reader rejects). semgrep itself checks the rule, with `semgrep --test`.
_SEMGREP_RULE = "tests/semgrep/felhaven_sinks.yml"
_SINKS_BEGIN = "      # BEGIN sinks, generated by tests/test_process_launches.py"
_SINKS_END = "      # END sinks"


def semgrep_sinks():
    """The rule's pattern-sinks entries, one line each, as the rule must hold
    them."""
    lines = [f"      - pattern: {module}.{name}(...)"
             for module in sorted(_MODULE_LAUNCHERS)
             for name in sorted(_MODULE_LAUNCHERS[module])]
    lines += ["      - patterns:",
              "          - pattern-either:",
              "              - pattern: $LIB.$FN(...)",
              "              - pattern: $FN(...)",
              "          - metavariable-regex:",
              "              metavariable: $FN",
              f"              regex: ^({'|'.join(sorted(_WIN32_LAUNCHERS))})$",
              '      - pattern: $TK.call("exec", ...)',
              "      - pattern: $TK.eval(...)"]
    return lines


# ── The detector ──────────────────────────────────────────────────────────────

class Site(NamedTuple):
    qualname: str    # enclosing Class.function, or "<module>"
    nested: bool     # the function is defined inside another function
    call: str        # what was referenced, e.g. "subprocess.run"
    line: int


def _aliases(tree):
    """Names that stand for a launcher module, and names that stand for one
    launcher, bound anywhere in the file (scope-blind: it can only over-report,
    and an over-report is a loud failure someone reviews)."""
    modules, names, star = {}, {}, []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _MODULE_LAUNCHERS:
                    modules[alias.asname or alias.name] = alias.name
                elif alias.name.split(".")[0] in _MODULE_LAUNCHERS and not alias.asname:
                    modules[alias.name.split(".")[0]] = alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module in _MODULE_LAUNCHERS:
            for alias in node.names:
                if alias.name == "*":
                    star.append(node)
                elif alias.name in _MODULE_LAUNCHERS[node.module]:
                    names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return modules, names, star


class _Finder(ast.NodeVisitor):
    """Walks one module, recording each launcher reference with its scope.
    Annotations are skipped: `x: subprocess.Popen` names a type, not a launch."""

    def __init__(self, modules, names):
        self.modules = modules
        self.names = names
        self.scope = []          # [(name, is_function)]
        self.found = []

    def _hit(self, call, node):
        qual = ".".join(name for name, _ in self.scope) or "<module>"
        nested = any(is_fn for _, is_fn in self.scope[:-1])
        self.found.append(Site(qual, nested, call, node.lineno))

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
        base = node.value
        if isinstance(base, ast.Name) and base.id in self.modules:
            module = self.modules[base.id]
            if node.attr in _MODULE_LAUNCHERS[module]:
                self._hit(f"{module}.{node.attr}", node)
        if node.attr in _WIN32_LAUNCHERS:
            self._hit(node.attr, node)
        self.generic_visit(node)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            if node.id in self.names:
                self._hit(self.names[node.id], node)
            elif node.id in _WIN32_LAUNCHERS:
                self._hit(node.id, node)

    def visit_Call(self, node):
        func = node.func
        first = node.args[0] if node.args else None
        first_str = (first.value if isinstance(first, ast.Constant)
                     and isinstance(first.value, str) else None)
        called = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)

        # getattr(subprocess, "run"), or getattr(os, <anything not a constant>)
        if called == "getattr" and isinstance(func, ast.Name) and len(node.args) >= 2:
            target, name = node.args[0], node.args[1]
            named = (name.value if isinstance(name, ast.Constant)
                     and isinstance(name.value, str) else None)
            if isinstance(target, ast.Name) and target.id in self.modules:
                module = self.modules[target.id]
                if named is None:
                    self._hit(f"getattr({module}, ?)", node)
                elif named in _MODULE_LAUNCHERS[module]:
                    self._hit(f"{module}.{named}", node)
            if named in _WIN32_LAUNCHERS:
                self._hit(named, node)

        # a launcher module imported by a string
        if called in ("__import__", "import_module") and first_str is not None:
            if first_str.split(".")[0] in _MODULE_LAUNCHERS:
                self._hit(f"import {first_str}", node)

        # Tcl: .eval(...) runs any Tcl, and .call("exec", ...) starts a program
        on_module = isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
            and func.value.id in self.modules
        if isinstance(func, ast.Attribute) and not on_module:
            if func.attr == "eval":
                self._hit("tcl eval", node)
            elif func.attr == "call" and first is not None and (
                    first_str is None or first_str == "exec"):
                self._hit("tcl call", node)
        self.generic_visit(node)


def launch_sites(source):
    """Every launcher reference in `source`, as Site tuples."""
    tree = ast.parse(source)
    modules, names, star = _aliases(tree)
    finder = _Finder(modules, names)
    finder.visit(tree)
    for node in star:
        finder.found.append(Site("<module>", False, f"from {node.module} import *",
                                 node.lineno))
    return finder.found


def _tracked_runtime_files():
    """Tracked .py files under the app root, tests excluded, as app-root
    relative paths with forward slashes."""
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=_APP_ROOT,
                         capture_output=True, text=True, check=True).stdout
    return sorted(p for p in out.splitlines() if p and not p.startswith("tests/"))


def _read(rel):
    with open(os.path.join(_APP_ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _found():
    """{"file::qualname": {"calls": [...], "lines": [...], "nested": bool}}"""
    result = {}
    for rel in _tracked_runtime_files():
        for site in launch_sites(_read(rel)):
            entry = result.setdefault(f"{rel}::{site.qualname}",
                                      {"calls": [], "lines": [], "nested": site.nested})
            entry["calls"].append(site.call)
            entry["lines"].append(site.line)
    return result


class _Refs(ast.NodeVisitor):
    """Where a name is referred to: as a plain name (same file) or an
    attribute (`self._x`, `module._x`), each with its enclosing scope."""

    def __init__(self, name, plain_names_count):
        self.name = name
        self.plain = plain_names_count
        self.scope = []
        self.where = []

    def _scoped(self, node):
        for expr in getattr(node, "decorator_list", []):
            self.visit(expr)
        self.scope.append(node.name)
        for stmt in node.body:
            self.visit(stmt)
        self.scope.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scoped

    def visit_Name(self, node):
        if self.plain and node.id == self.name:
            self.where.append(".".join(self.scope) or "<module>")

    def visit_Attribute(self, node):
        if node.attr == self.name:
            self.where.append(".".join(self.scope) or "<module>")
        self.generic_visit(node)


def _callers(key):
    """Every scope, in any runtime file, that refers to the function `key`
    names: by plain name in its own file, by attribute anywhere."""
    own, qual = key.split("::")
    leaf = qual.rsplit(".", 1)[-1]
    found = set()
    for rel in _tracked_runtime_files():
        refs = _Refs(leaf, plain_names_count=(rel == own))
        refs.visit(ast.parse(_read(rel)))
        found.update(f"{rel}::{where}" for where in refs.where)
    return found


def _is_private_repo():
    """Only the private repo tracks publish/; the public snapshot never has it."""
    out = subprocess.run(["git", "ls-files", "../publish/publish-snapshot.sh"],
                         cwd=_APP_ROOT, capture_output=True, text=True,
                         check=True).stdout
    return bool(out.strip())


def _drop_absent(registry):
    """In the public snapshot, the lines and callers naming a file it leaves
    out. In the private repo, the registry untouched: a missing file there is
    a stale line, and test_no_entry_is_stale says so."""
    if _is_private_repo():
        return registry
    tracked = set(_tracked_runtime_files())
    return {key: entry._replace(callers=tuple(
                c for c in entry.callers if c.split("::")[0] in tracked))
            for key, entry in registry.items() if key.split("::")[0] in tracked}


_LAUNCHES = _drop_absent(_LAUNCHES)


# ── The tests ─────────────────────────────────────────────────────────────────

class TheDetectorSeesEverySpelling(unittest.TestCase):
    """The guard is only as good as launch_sites(); test it on its own."""

    def _calls(self, src):
        return [site.call for site in launch_sites(src)]

    def test_these_are_launches(self):
        cases = (
            ("import subprocess\nsubprocess.run(['x'])", "subprocess.run"),
            ("import subprocess as sp\nsp.Popen(['x'])", "subprocess.Popen"),
            ("from subprocess import run\nrun(['x'])", "subprocess.run"),
            ("from subprocess import check_output as co\nco(['x'])",
             "subprocess.check_output"),
            ("import subprocess\nf = subprocess.call", "subprocess.call"),
            ("import os\nos.startfile(p)", "os.startfile"),
            ("import os as _os\n_os.system('x')", "os.system"),
            ("import os.path\nos.execv(p, a)", "os.execv"),
            ("from os import startfile\nstartfile(p)", "os.startfile"),
            ("import os, functools\nfunctools.partial(os.startfile, p)", "os.startfile"),
            ("import os\nos.posix_spawn(p, a, e)", "os.posix_spawn"),
            ("import webbrowser\nwebbrowser.open(u)", "webbrowser.open"),
            ("import webbrowser as wb\nwb.open_new_tab(u)", "webbrowser.open_new_tab"),
            ("from webbrowser import open\nopen(u)", "webbrowser.open"),
            ("import asyncio\nasyncio.create_subprocess_exec('x')",
             "asyncio.create_subprocess_exec"),
            ("import pty\npty.spawn('x')", "pty.spawn"),
            ("import ctypes\nctypes.windll.shell32.ShellExecuteW(0, 'open', p, 0, 0, 1)",
             "ShellExecuteW"),
            ("import ctypes\n_sh = ctypes.windll.shell32\n_sh.SHFileOperationW(op)",
             "SHFileOperationW"),
            ("k = ctypes.WinDLL('kernel32')\nk.CreateProcessW(None, c)", "CreateProcessW"),
            ("ShellExecuteW(0, 'open', p, 0, 0, 1)", "ShellExecuteW"),
            ("import subprocess\ngetattr(subprocess, 'run')(['x'])", "subprocess.run"),
            ("import os\ngetattr(os, name)(p)", "getattr(os, ?)"),
            ("getattr(shell32, 'ShellExecuteW')", "ShellExecuteW"),
            ("import importlib\nimportlib.import_module('subprocess')", "import subprocess"),
            ("__import__('os')", "import os"),
            ("root.tk.call('exec', 'calc.exe')", "tcl call"),
            ("root.call(cmd, 'x')", "tcl call"),
            ("root.tk.eval('exec calc.exe')", "tcl eval"),
            ("from subprocess import *\nrun(['x'])", "from subprocess import *"),
        )
        for src, expected in cases:
            with self.subTest(src=src):
                self.assertIn(expected, self._calls(src))

    def test_these_are_not(self):
        for src in (
                "import subprocess",
                "import subprocess\nexcept_ = subprocess.TimeoutExpired",
                "import subprocess\nflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)",
                "import subprocess\nx = subprocess.PIPE",
                "import ctypes\nctypes.windll.shell32.IsUserAnAdmin()",
                "import os\nos.path.join(a, b)",
                "import os\nos.remove(p)",
                "open(p, encoding='utf-8')",
                "root.tk.call('wm', 'attributes', '.', '-topmost', 1)",
                "import importlib\nimportlib.import_module(module_path)",
                "import subprocess\ndef f() -> subprocess.Popen: ...",
                "import subprocess\nproc: subprocess.Popen",
                "import subprocess\ndef f(p: subprocess.Popen) -> None: ...",
                "webbrowser.open(u)",       # no import: not the webbrowser module
        ):
            with self.subTest(src=src):
                self.assertEqual(self._calls(src), [])

    def test_a_site_is_keyed_by_its_enclosing_function(self):
        src = ("import subprocess\n"
               "class Panel:\n"
               "    def build(self):\n"
               "        def _open(e):\n"
               "            subprocess.run(['x'])\n"
               "    def launch(self):\n"
               "        subprocess.Popen(['y'])\n"
               "def helper():\n"
               "    subprocess.call(['z'])\n"
               "subprocess.run(['top'])\n")
        sites = {(s.qualname, s.nested, s.call) for s in launch_sites(src)}
        self.assertEqual(sites, {
            ("Panel.build._open", True, "subprocess.run"),
            ("Panel.launch", False, "subprocess.Popen"),
            ("helper", False, "subprocess.call"),
            ("<module>", False, "subprocess.run"),
        })


class EveryLaunchIsReviewed(unittest.TestCase):

    def test_no_launch_is_unreviewed(self):
        found = _found()
        new = {key: f"{', '.join(v['calls'])} (line {', '.join(map(str, v['lines']))})"
               for key, v in found.items() if key not in _LAUNCHES}
        self.assertFalse(
            new,
            "These functions start a program or hand a name to the shell, and "
            "nobody has reviewed them. For each: trace where every argument comes "
            "from (constant, app path, owner, config, network, llm), make sure "
            "nothing untrusted can start with '-' or name an unexpected program "
            "or path, add its Launch(...) line to _LAUNCHES, and run the semgrep "
            f"tripwire (the command is in {_SEMGREP_RULE}): {new}")

    def test_no_entry_is_stale(self):
        found = _found()
        stale = sorted(key for key in _LAUNCHES if key not in found)
        self.assertFalse(
            stale,
            f"{stale} no longer launch anything (or were renamed): delete or move "
            "their lines. A stale line would let the next launch in that place "
            "through unreviewed.")

    def test_each_entry_names_exactly_the_launchers_its_function_uses(self):
        found = _found()
        for key, entry in _LAUNCHES.items():
            if key not in found:
                continue        # test_no_entry_is_stale reports it
            with self.subTest(key=key):
                self.assertEqual(
                    sorted(found[key]["calls"]), sorted(entry.calls),
                    f"{key} now uses {found[key]['calls']} (lines {found[key]['lines']}); "
                    "a launcher was added or swapped. Review its arguments again "
                    "and update the line.")

    def test_every_entry_is_filled_in(self):
        for key, entry in _LAUNCHES.items():
            with self.subTest(key=key):
                self.assertTrue(entry.calls)
                self.assertGreater(len(entry.program.split()), 0)
                self.assertTrue(entry.sources)
                self.assertTrue(entry.sources <= SOURCES,
                                f"unknown sources {set(entry.sources - SOURCES)}")
                self.assertIn(entry.verdict, VERDICTS)
                self.assertGreater(len(entry.reason.split()), 5)

    def test_a_verdict_agrees_with_its_sources(self):
        for key, entry in _LAUNCHES.items():
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
        """A private, non-nested helper marked 'checked' gets its argument from
        a caller, so the check lives up there; listing the callers is what lets
        the next test notice a caller that skips it."""
        found = _found()
        for key, entry in _LAUNCHES.items():
            leaf = key.split("::")[1].rsplit(".", 1)[-1]
            if (entry.verdict == "checked" and leaf.startswith("_")
                    and key in found and not found[key]["nested"]):
                with self.subTest(key=key):
                    self.assertTrue(entry.callers, f"{key}: list its callers")

    def test_listed_callers_are_the_only_callers(self):
        for key, entry in _LAUNCHES.items():
            if not entry.callers:
                continue
            with self.subTest(key=key):
                self.assertEqual(
                    _callers(key), set(entry.callers),
                    f"{key} is called from somewhere new. Its arguments are checked "
                    "by its callers; check the new one does the same, then list it.")


class TheSemgrepRuleWatchesTheSameLaunchers(unittest.TestCase):
    """The tripwire must sink into everything this guard calls a launch."""

    def test_the_rule_is_tracked(self):
        out = subprocess.run(["git", "ls-files", _SEMGREP_RULE], cwd=_APP_ROOT,
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), _SEMGREP_RULE)

    def test_its_sinks_are_this_guards_launchers(self):
        lines = _read(_SEMGREP_RULE).splitlines()
        self.assertEqual(lines.count(_SINKS_BEGIN), 1, "the BEGIN marker is missing")
        self.assertEqual(lines.count(_SINKS_END), 1, "the END marker is missing")
        block = lines[lines.index(_SINKS_BEGIN) + 1:lines.index(_SINKS_END)]
        expected = semgrep_sinks()
        self.assertTrue(
            block == expected,
            "The semgrep rule's sinks differ from this guard's launchers. Replace "
            f"the lines between the markers in {_SEMGREP_RULE} with:\n"
            + "\n".join(expected))


class MpvIsOnlyAskedForKnownCommands(unittest.TestCase):
    """mpv will run a program, or a script, for whoever writes its pipe. Every
    command Morpheus writes must be a literal list starting with one of
    _MPV_COMMANDS, and the ones that change state may only touch the
    properties and options listed."""

    def _commands(self):
        """(line, list node) for every command Morpheus writes to the pipe: the
        first argument of each _ipc(...) call, and each {"command": [...]}
        payload built directly (in _ipc itself it is the passed-in name)."""
        tree = ast.parse(_read(_MPV_PIPE_OWNER))
        out = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name == "_ipc":
                    out.append((node.lineno, node.args[0] if node.args else None))
            elif isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and k.value == "command":
                        if not (isinstance(v, ast.Name) and v.id == "command"):
                            out.append((node.lineno, v))
        return out

    def _problem(self, node):
        if not isinstance(node, ast.List) or not node.elts:
            return "not a literal list"
        head = node.elts[0]
        if not (isinstance(head, ast.Constant) and head.value in _MPV_COMMANDS):
            return f"command {ast.unparse(head)} is not in _MPV_COMMANDS"
        if head.value in ("cycle", "set_property"):
            prop = node.elts[1] if len(node.elts) > 1 else None
            if not (isinstance(prop, ast.Constant) and prop.value in _MPV_WRITABLE):
                return f"{head.value} of {ast.unparse(prop) if prop else '?'}"
        if head.value == "loadfile":
            for elt in node.elts[1:]:
                if isinstance(elt, ast.Dict):
                    for k in elt.keys:
                        if not (isinstance(k, ast.Constant) and k.value in _MPV_LOADFILE_OPTIONS):
                            return f"loadfile option {ast.unparse(k) if k else '**'}"
        return None

    def test_every_command_is_a_known_one(self):
        bad = [f"line {line}: {self._problem(node)}" for line, node in self._commands()
               if self._problem(node)]
        self.assertFalse(bad, "mpv command not allowed (see _MPV_COMMANDS): " + "; ".join(bad))

    def test_no_allowed_command_is_stale(self):
        used, props, options = set(), set(), set()
        for _, node in self._commands():
            if isinstance(node, ast.List) and node.elts and isinstance(node.elts[0], ast.Constant):
                used.add(node.elts[0].value)
                if len(node.elts) > 1 and isinstance(node.elts[1], ast.Constant) \
                        and node.elts[0].value in ("cycle", "set_property"):
                    props.add(node.elts[1].value)
                for elt in node.elts[1:]:
                    if isinstance(elt, ast.Dict):
                        options.update(k.value for k in elt.keys if isinstance(k, ast.Constant))
        self.assertEqual(set(_MPV_COMMANDS) - used, set())
        self.assertEqual(_MPV_WRITABLE - props, set())
        self.assertEqual(_MPV_LOADFILE_OPTIONS - options, set())

    def test_only_morpheus_opens_the_pipe(self):
        holders = [rel for rel in _tracked_runtime_files() if "mpv-felhaven" in _read(rel)]
        self.assertEqual(holders, [_MPV_PIPE_OWNER])

    def test_a_forbidden_command_is_refused(self):
        for src in ('["run", "calc.exe"]', '["subprocess", {"args": ["x"]}]',
                    '["load-script", "x.lua"]', '["set_property", "script-opts", "x"]',
                    '["loadfile", u, "replace", -1, {"script-opts": "x"}]', "cmd"):
            with self.subTest(src=src):
                self.assertIsNotNone(self._problem(ast.parse(src, mode="eval").body))


if __name__ == "__main__":
    unittest.main()
