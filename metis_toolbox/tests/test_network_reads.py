"""
test_network_reads.py — every runtime network read goes through hermes.

tools/hermes.py bounds a read: a byte cap, and a deadline for the whole
request rather than for each socket operation. That only protects the readers
that use it, so this guard parses every tracked runtime module and fails on a
network library used anywhere else:

  * an import of requests, urllib.request, http.client or urllib3 (however
    spelled: `import x`, `import x as y`, `from x import y`,
    `from urllib import request`);
  * a call that opens a connection itself: socket.socket(),
    socket.create_connection(), socket.create_server(), socket.getaddrinfo().
    Plain `import socket` is fine; Argus reads its AF_/SOCK_ constants.

Two lists say who may. _ALLOWED is permanent, each entry with its reason.
_NOT_YET_MIGRATED is the migration's to-do list: one module comes off per
commit, and it ends empty. A stale entry in either fails as loudly as a new
offender (the tests/test_tool_output_contract.py pattern), so the guard is
honest at every commit.

Files come from `git ls-files`, not the disk: gitignored local files must not
pass here and fail in CI (CONVENTIONS §10).

Run from the package root:
    python -X utf8 -m unittest tests.test_network_reads
"""

import ast
import os
import subprocess
import unittest

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_NETWORK_MODULES = ("requests", "urllib.request", "http.client", "urllib3")
_SOCKET_OPENERS = {"socket", "create_connection", "create_server", "getaddrinfo"}

# Permanent. Each one says why it may talk to the network itself.
_ALLOWED = {
    "tools/hermes.py": "the bounded reader itself",
    "tools/callimachus.py": (
        "its own opener holds the address guard (public internet only, one "
        "resolved IP); it reads through hermes's deadline, dial and cap"),
    "pythia.py": (
        "the Ollama chat stream on loopback: long by nature, ended by its own "
        "Stop button, not a deadline"),
    "tools/dev_build_hypatia_catalog.py": (
        "a dev-only build script, run by hand and never imported by Felhaven"),
}

# The migration's to-do list, one module off per commit. Emptied 2026-10-01
# when Demeter, the last of the seven readers, moved onto hermes. Kept as a
# typed empty set so a future exception has a home, and the stale-entry test
# keeps it honest (the test_tool_output_contract.py pattern).
_NOT_YET_MIGRATED: set[str] = set()


def _tracked_runtime_files():
    """Tracked .py files under the app root, tests excluded, as app-root
    relative paths with forward slashes."""
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=_APP_ROOT,
                         capture_output=True, text=True, check=True).stdout
    return sorted(p for p in out.splitlines()
                  if p and not p.startswith("tests/"))


def _is_network_module(name):
    return any(name == m or name.startswith(m + ".") for m in _NETWORK_MODULES)


def offences(source):
    """(line, what) for each network use in `source` that hermes should own."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_network_module(alias.name):
                    found.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom) and node.module:
            if _is_network_module(node.module):
                found.append((node.lineno, f"from {node.module} import ..."))
            for alias in node.names:
                full = f"{node.module}.{alias.name}"
                if _is_network_module(full):
                    found.append((node.lineno, f"from {node.module} import {alias.name}"))
                if node.module == "socket" and alias.name in _SOCKET_OPENERS:
                    found.append((node.lineno, f"from socket import {alias.name}"))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and isinstance(node.func.value, ast.Name)
              and node.func.value.id == "socket"
              and node.func.attr in _SOCKET_OPENERS):
            found.append((node.lineno, f"socket.{node.func.attr}()"))
    return found


def _offenders():
    result = {}
    for rel in _tracked_runtime_files():
        with open(os.path.join(_APP_ROOT, rel), encoding="utf-8") as fh:
            hits = offences(fh.read())
        if hits:
            result[rel] = hits
    return result


class TheDetectorSeesEverySpelling(unittest.TestCase):
    """The guard is only as good as offences(); test it on its own."""

    def test_these_are_offences(self):
        for src in ("import requests", "import requests as r",
                    "import urllib.request", "from urllib.request import urlopen",
                    "from urllib import request", "import http.client",
                    "from http import client", "import http.client as hc",
                    "from requests.adapters import HTTPAdapter", "import urllib3",
                    "socket.create_connection(('h', 1))", "socket.socket()",
                    "socket.getaddrinfo('h', 1)", "from socket import create_connection"):
            with self.subTest(src=src):
                self.assertTrue(offences(src), src)

    def test_these_are_not(self):
        for src in ("import socket", "x = socket.AF_INET6", "import urllib.parse",
                    "from urllib.parse import urlencode", "import urllib.error",
                    "import json", "from tools import hermes",
                    "hermes.get('https://x', cap=1, timeout=1)"):
            with self.subTest(src=src):
                self.assertFalse(offences(src), src)


class EveryRuntimeReadGoesThroughHermes(unittest.TestCase):

    def test_no_module_reads_the_network_on_its_own(self):
        offenders = _offenders()
        unexpected = {f: hits for f, hits in offenders.items()
                      if f not in _ALLOWED and f not in _NOT_YET_MIGRATED}
        self.assertFalse(
            unexpected,
            "These modules use a network library directly. Read through "
            "tools/hermes.py (get(), with a measured cap) instead, or, if there "
            f"is a real reason, add it to _ALLOWED with that reason: {unexpected}")

    def test_no_list_entry_is_stale(self):
        offenders = _offenders()
        stale = sorted(f for f in (*_ALLOWED, *_NOT_YET_MIGRATED) if f not in offenders)
        self.assertFalse(
            stale,
            f"{stale} no longer use the network directly: delete them from "
            "_ALLOWED / _NOT_YET_MIGRATED. A stale entry would let the next "
            "direct call in that file through unnoticed.")

    def test_the_two_lists_do_not_overlap(self):
        self.assertFalse(set(_ALLOWED) & _NOT_YET_MIGRATED)

    def test_every_allowance_says_why(self):
        for name, why in _ALLOWED.items():
            with self.subTest(name=name):
                self.assertGreater(len(why.split()), 3)


if __name__ == "__main__":
    unittest.main()
