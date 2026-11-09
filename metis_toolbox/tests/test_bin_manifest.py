"""
test_bin_manifest.py — each program in bin/ matches what this machine recorded
for it in config/bin_manifest.json.

The manifest describes this machine's bin/, so like bin/ it is per machine and
gitignored (tools/aegis.py). Two kinds of check:
  * EVERYWHERE (CI, a fresh clone, a worktree): the programs the code asks
    aegis for are exactly aegis.PROGRAMS (parsed from the tracked source, not
    a hardcoded list); only aegis looks programs up on PATH; the manifest is
    not committed; and the main checkout can be found.
  * The REAL PROGRAMS, wherever bin/ exists: each program in bin/ has a
    well-formed entry and matches it. A worktree has no bin/ of its own, so
    the main checkout's bin/ and manifest are used, always as a pair.

With no bin/ at all (CI, or a clone that runs without the optional programs,
SETUP.md section 5) the real-program check skips. That skip can't hide a
mismatch: a mismatch needs a bin/, and looking for bin/ can't quietly fail.
Finding the main checkout raises when git can't answer, and
test_the_main_checkout_is_found proves the lookup lands on a real app root.

Run from the package root:
    python -X utf8 -m unittest tests.test_bin_manifest
"""

import ast
import datetime
import json
import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import aegis

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MANIFEST_REL = os.path.join("config", "bin_manifest.json")


def _git(*args):
    return subprocess.run(["git", *args], cwd=_APP_ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()


def _names_the_code_asks_for():
    """String literals passed to aegis.resolve(...) or a module's _resolve(...)
    in tracked tools/ files."""
    names = set()
    for rel in _git("ls-files", "tools/*.py").split():
        with open(os.path.join(_APP_ROOT, rel), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                continue
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if called in ("_resolve", "resolve") and (
                    called == "_resolve" or (isinstance(func, ast.Attribute)
                                             and getattr(func.value, "id", "") == "aegis")):
                names.add(node.args[0].value)
    return names


def _main_app_root():
    """The main checkout's app root: this one, unless this is a worktree.

    Worked out from git, never hardcoded: the app root's place inside this
    checkout, joined onto the main checkout. Raises when git can't answer,
    because returning nothing here would turn into a skip, and a skip on the
    machine that runs Felhaven is exactly what must not happen quietly."""
    try:
        common = _git("rev-parse", "--path-format=absolute", "--git-common-dir")
        top = _git("rev-parse", "--show-toplevel")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"git could not locate the main checkout ({exc}); not "
                           "skipping, because a skip here could hide a mismatch") from exc
    rel = os.path.relpath(_APP_ROOT, top)
    return os.path.normpath(os.path.join(os.path.dirname(common), rel))


def _machine_root():
    """The app root whose bin/ and manifest this machine runs: this
    checkout's if it has a bin/, else the main checkout's."""
    if os.path.isdir(os.path.join(_APP_ROOT, "bin")):
        return _APP_ROOT
    return _main_app_root()


class WhatHoldsEverywhere(unittest.TestCase):

    def test_the_code_asks_for_exactly_aegis_programs(self):
        self.assertEqual(_names_the_code_asks_for(), set(aegis.PROGRAMS))

    def test_only_aegis_looks_programs_up_on_path(self):
        """A module that calls shutil.which itself has found a program aegis
        never vouched for, and would run it whether or not bin/ was refused."""
        offenders = []
        for rel in _git("ls-files", "*.py").split():
            if rel.startswith("tests/") or rel == "tools/aegis.py":
                continue
            with open(os.path.join(_APP_ROOT, rel), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "which"
                        and getattr(node.func.value, "id", "") == "shutil"):
                    offenders.append(f"{rel}:{node.lineno}")
        self.assertEqual(offenders, [], "find programs through tools/aegis.py")

    def test_the_manifest_is_not_committed(self):
        """Asked of git, not the filesystem: the file exists on Obelisk either
        way. Committed, it would hand one machine's hashes to every clone."""
        rel = "config/bin_manifest.json"
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", rel],
                                 cwd=_APP_ROOT, capture_output=True).returncode == 0
        ignored = subprocess.run(["git", "check-ignore", "-q", rel],
                                 cwd=_APP_ROOT, capture_output=True).returncode == 0
        self.assertFalse(tracked, f"{rel} is committed; it is per machine (git rm --cached it)")
        self.assertTrue(ignored, f"{rel} is not gitignored; a `record` would show as a change")

    def test_the_main_checkout_is_found(self):
        """What makes the real-program skip trustworthy: the lookup lands on a
        real app root, so 'no bin/ there' means there is no bin/."""
        root = _main_app_root()
        self.assertTrue(os.path.isfile(os.path.join(root, "felhaven.py")),
                        f"{root} is not a Felhaven app root")


class TheRealProgramsMatch(unittest.TestCase):

    def setUp(self):
        self.root = _machine_root()
        self.bin = os.path.join(self.root, "bin")
        if not os.path.isdir(self.bin):
            self.skipTest(f"no bin/ at {self.root}: the programs are optional "
                          "(SETUP.md section 5); the checks that hold everywhere still ran")
        self.present = [n for n in aegis.PROGRAMS
                        if os.path.isfile(os.path.join(self.bin, f"{n}.exe"))]

    def _programs(self):
        path = os.path.join(self.root, _MANIFEST_REL)
        if not os.path.isfile(path):
            if self.present:
                self.fail(f"bin/ holds {', '.join(self.present)} but nothing is recorded "
                          f"({path} is missing), so Felhaven refuses them. Record each: "
                          f"python -m tools.aegis record {self.present[0]} (SETUP.md section 5)")
            return {}
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)["programs"]

    def test_each_program_in_bin_is_recorded_and_matches(self):
        programs = self._programs()
        for name in self.present:
            with self.subTest(name=name):
                entry = programs.get(name)
                self.assertIsNotNone(entry, f"bin/{name}.exe is not recorded: "
                                            f"python -m tools.aegis record {name}")
                path = os.path.join(self.bin, f"{name}.exe")
                self.assertEqual(aegis.sha256_of(path), entry["sha256"],
                                 f"bin/{name}.exe does not match what was recorded. "
                                 f"Updated on purpose? python -m tools.aegis record {name}")

    def test_every_entry_is_well_formed(self):
        for name, entry in self._programs().items():
            with self.subTest(name=name):
                self.assertIn(name, aegis.PROGRAMS)
                self.assertEqual(set(entry), set(aegis._FIELDS))
                self.assertEqual(entry["file"], f"{name}.exe")
                self.assertTrue(entry["version"].strip())
                self.assertRegex(entry["sha256"], _SHA256)
                self.assertRegex(entry["published_sha256"], _SHA256)
                self.assertTrue(entry["source_url"].startswith("https://"))
                self.assertTrue(entry["checksum_source"].startswith("https://"))
                datetime.date.fromisoformat(entry["recorded"])


if __name__ == "__main__":
    unittest.main()
