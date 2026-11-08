"""
test_bin_manifest.py — config/bin_manifest.json says where each program in
bin/ came from, and what it must hash to.

Three checks, in order of where they can run:
  * SHAPE, everywhere: every entry has its fields, sha256s are sha256s, URLs
    are https.
  * LOCKSTEP, everywhere: the programs the code asks aegis for are exactly the
    programs the manifest describes (parsed from the tracked source, not a
    hardcoded list).
  * The REAL PROGRAMS, wherever bin/ exists. bin/ is gitignored, so CI has
    none, and there the check skips by name (GITHUB_ACTIONS). Anywhere else a
    missing bin/ FAILS rather than skips, because "skipped" on Obelisk would
    hide exactly the mismatch this exists to catch. A worktree has no bin/ of
    its own, so the main checkout's is used. A machine that genuinely runs
    without bin/ can set FELHAVEN_NO_BIN=1.

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


def _manifest():
    with open(os.path.join(_APP_ROOT, "config", "bin_manifest.json"), encoding="utf-8") as fh:
        return json.load(fh)["programs"]


def _names_the_code_asks_for():
    """String literals passed to aegis.resolve(...) or a module's _resolve(...)
    in tracked tools/ files."""
    out = subprocess.run(["git", "ls-files", "tools/*.py"], cwd=_APP_ROOT,
                         capture_output=True, text=True, check=True).stdout
    names = set()
    for rel in out.split():
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


def _bin_dir():
    """This checkout's bin/, else the main checkout's (from a worktree)."""
    own = os.path.join(_APP_ROOT, "bin")
    if os.path.isdir(own):
        return own
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute",
                                 "--git-common-dir"], cwd=_APP_ROOT, capture_output=True,
                                text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    main = os.path.join(os.path.dirname(common), "metis_toolbox", "bin")
    return main if os.path.isdir(main) else None


class TheManifestIsWellFormed(unittest.TestCase):

    def test_every_entry_has_every_field(self):
        for name, entry in _manifest().items():
            with self.subTest(name=name):
                self.assertEqual(set(entry), set(aegis._FIELDS))
                self.assertEqual(entry["file"], f"{name}.exe")
                self.assertTrue(entry["version"].strip())

    def test_checksums_are_sha256(self):
        for name, entry in _manifest().items():
            with self.subTest(name=name):
                self.assertRegex(entry["sha256"], _SHA256)
                self.assertRegex(entry["published_sha256"], _SHA256)

    def test_sources_are_https(self):
        for name, entry in _manifest().items():
            with self.subTest(name=name):
                self.assertTrue(entry["source_url"].startswith("https://"))
                self.assertTrue(entry["checksum_source"].startswith("https://"))

    def test_recorded_is_a_date(self):
        for name, entry in _manifest().items():
            with self.subTest(name=name):
                datetime.date.fromisoformat(entry["recorded"])

    def test_the_code_and_the_manifest_name_the_same_programs(self):
        self.assertEqual(_names_the_code_asks_for(), set(_manifest()))

    def test_only_aegis_looks_programs_up_on_path(self):
        """A module that calls shutil.which itself has found a program aegis
        never vouched for, and would run it whether or not bin/ was refused."""
        out = subprocess.run(["git", "ls-files", "*.py"], cwd=_APP_ROOT,
                             capture_output=True, text=True, check=True).stdout
        offenders = []
        for rel in out.split():
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


class TheRealProgramsMatch(unittest.TestCase):

    def test_each_program_in_bin_matches_its_entry(self):
        folder = _bin_dir()
        if folder is None:
            if os.environ.get("GITHUB_ACTIONS") == "true":
                self.skipTest("CI has no bin/ (it is gitignored); the shape and "
                              "lockstep checks above still ran")
            if os.environ.get("FELHAVEN_NO_BIN") == "1":
                self.skipTest("FELHAVEN_NO_BIN=1: this machine runs without bin/")
            self.fail("No bin/ found here or in the main checkout. Put the programs "
                      "in bin/ (SETUP.md), or set FELHAVEN_NO_BIN=1 if this "
                      "machine runs without them.")
        for name, entry in _manifest().items():
            with self.subTest(name=name):
                path = os.path.join(folder, entry["file"])
                self.assertTrue(os.path.isfile(path), f"{path} is missing")
                self.assertEqual(aegis.sha256_of(path), entry["sha256"],
                                 f"{entry['file']} in bin/ does not match the manifest. "
                                 f"Updated on purpose? python -m tools.aegis record {name}")


if __name__ == "__main__":
    unittest.main()
