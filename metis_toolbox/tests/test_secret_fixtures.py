"""
test_secret_fixtures.py — no real PIN or key becomes a fixture, and every key a
tool reads has a row in the Cerberus tab.

A test file is published with the snapshot, and a value written there is
public for good: deleting it later doesn't unpublish it. That already happened
once, with a real PIN (CONVENTIONS §12, 2026-09-30). These guards make the
same mistake fail before it can be committed:

  1. Fixture PINs and values. In every test that touches Cerberus, a string
     literal handed to a PIN or key sink (unlock, verify, set_pin, vault_set,
     the test helpers that wrap them, or text typed into an entry box) must
     contain "fake". A real PIN or key can't pass by accident, and a reader can
     see at a glance that the value means nothing.

  2. Key shapes, everywhere. No string literal in any tracked .py file, and no
     string in a tracked config/*.json, may look like an API key: 16+ letters
     and digits with at least two of each (Finnhub's and OpenTopography's
     keys are that shape), or Brave's "BSA" prefix. 40- and 64-character hex
     (git SHAs, sha256 digests) are not keys, and a literal that says "fake"
     is allowed. On 2026-09-30 the whole tree had no match, so a match is new.
     The providers don't publish their formats; these shapes are the
     conservative version, and a failure names file, line and shape, never
     the value, so the guard can't leak what it finds into a CI log.

  3. KNOWN_KEYS lockstep. Every `_VAULT_KEY_NAME` in tools/ is in
     cerberus.KNOWN_KEYS and the reverse (§10: two live sources, no counts),
     so a new key-using tool can't ship without its row.

Asserted against `git ls-files`, not the disk (§10).

    python -X utf8 -m unittest tests.test_secret_fixtures
"""

import ast
import json
import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cerberus

_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Sinks: callee name -> index of the positional argument that carries the PIN
# or the value. Names are not secrets; values and PINs are.
_PIN_SINKS = {"unlock": 0, "verify": 0, "set_pin": 0, "_unlock": 0}
_VALUE_SINKS = {"vault_set": 1, "_seed": 1, "_submit": 1}
_TYPED = ("entry", "editor")          # x._entry.insert(0, "...") and friends

_KEYLIKE = re.compile(r"[A-Za-z0-9]{16,}")
_BRAVE = re.compile(r"BSA[A-Za-z0-9_\-]{20,}")
_HEX_DIGEST = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def _tracked(*patterns):
    out = subprocess.run(["git", "ls-files", *patterns], cwd=_APP,
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr)
    return out.stdout.split()


def _callee(node):
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr, f.value
    if isinstance(f, ast.Name):
        return f.id, None
    return None, None


def _terminal_name(node):
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _touches_cerberus(tree):
    """Imports cerberus or the sandbox: the only ways a test can hand it a PIN.
    (Importing a panel alone can't, and Home's chat box is an `_entry` too.)"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == "cerberus" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "tests._cerberus_sandbox":
            return True
    return False


def fixture_violations(tree):
    """[(line, why)] for every literal at a PIN/value sink without 'fake'."""
    bad = []

    def check(const, why):
        # A blank string can't be a secret (tests use it to check refusals).
        if (isinstance(const, ast.Constant) and isinstance(const.value, str)
                and const.value.strip() and "fake" not in const.value.lower()):
            bad.append((const.lineno, why))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name, receiver = _callee(node)
            if name in _PIN_SINKS and len(node.args) > _PIN_SINKS[name]:
                check(node.args[_PIN_SINKS[name]], f"a PIN passed to {name}()")
            if name in _VALUE_SINKS and len(node.args) > _VALUE_SINKS[name]:
                check(node.args[_VALUE_SINKS[name]], f"a value passed to {name}()")
            if (name == "insert" and receiver is not None and len(node.args) == 2
                    and any(t in _terminal_name(receiver).lower() for t in _TYPED)):
                check(node.args[1], "text typed into an entry box")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if "pin" in _terminal_name(target).lower():
                    check(node.value, "a PIN constant")
    return sorted(bad)


def looks_like_key(s):
    """Which key shape `s` has, or None. Pure, so the shape itself is tested."""
    if "fake" in s.lower():
        return None
    if _BRAVE.fullmatch(s):
        return "Brave-key shape (BSA prefix)"
    if _KEYLIKE.fullmatch(s) and not _HEX_DIGEST.fullmatch(s):
        if sum(c.isdigit() for c in s) >= 2 and sum(c.isalpha() for c in s) >= 2:
            return "random-token shape (16+ letters and digits)"
    return None


def _json_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _json_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _json_strings(v)


class TestFixturesAreFake(unittest.TestCase):

    def test_every_pin_and_value_in_a_cerberus_test_says_fake(self):
        checked = 0
        for rel in _tracked("tests/*.py"):
            with open(os.path.join(_APP, rel), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            if not _touches_cerberus(tree):
                continue
            checked += 1
            for line, why in fixture_violations(tree):
                self.fail(f"{rel}:{line}: {why} doesn't contain 'fake'. Use an "
                          f"obviously fake value (tests/_cerberus_sandbox.py has "
                          f"FAKE_PIN); a real one would be published for good.")
        self.assertTrue(checked, "found no test that touches Cerberus — the scan is broken")

    def test_the_rule_catches_what_it_should(self):
        src = (
            'cerberus.unlock("8642")\n'
            'cerberus.vault_set("k", "a-real-looking-value")\n'
            'panel._entry.insert(0, "2468")\n'
            '_PIN = "1357"\n'
            'cerberus.unlock("fake-pin")\n'
            'cerberus.vault_set("k", "fake-value")\n'
            'panel._entry.insert(0, "")\n'
            'cerberus.set_pin("   ")\n'
            'cerberus.vault_get("finnhub_api_key")\n'
        )
        self.assertEqual([line for line, _ in fixture_violations(ast.parse(src))],
                         [1, 2, 3, 4])


class TestNoKeyShapedLiterals(unittest.TestCase):

    def test_no_tracked_python_literal_looks_like_a_key(self):
        for rel in _tracked("*.py"):
            with open(os.path.join(_APP, rel), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    shape = looks_like_key(node.value)
                    if shape:
                        self.fail(f"{rel}:{node.lineno}: a string literal has a "
                                  f"{shape}. If it's a real key, revoke it at the "
                                  f"provider now; if it's a fixture, put 'fake' in it.")

    def test_no_tracked_config_string_looks_like_a_key(self):
        for rel in _tracked("config/*.json"):
            with open(os.path.join(_APP, rel), encoding="utf-8") as f:
                data = json.load(f)
            for s in _json_strings(data):
                shape = looks_like_key(s)
                if shape:
                    self.fail(f"{rel}: a string has a {shape}. If it's a real key, "
                              f"revoke it at the provider now.")

    def test_the_shapes(self):
        # Built from short parts so this file holds no key-shaped literal
        # itself (the guard above reads this file too, and caught it once).
        token = "c9" + "x" * 14 + "4q"
        hex16 = "0123456789" + "abcdef"
        self.assertTrue(looks_like_key(token))                              # Finnhub-like
        self.assertTrue(looks_like_key(hex16 * 2))                          # 32 hex
        self.assertTrue(looks_like_key("BSA" + "aB_-" * 6))                  # Brave
        self.assertIsNone(looks_like_key(hex16 * 4))                        # sha256
        self.assertIsNone(looks_like_key("0123456789" * 4))                 # 40: a SHA
        self.assertIsNone(looks_like_key("n47_123_w080_456_tile"))          # an id
        self.assertIsNone(looks_like_key("test_every_tracked_config"))      # a name
        self.assertIsNone(looks_like_key("fake" + token))                   # a fixture


class TestKnownKeysLockstep(unittest.TestCase):

    def _tool_key_names(self):
        names = {}
        for rel in _tracked("tools/*.py"):
            with open(os.path.join(_APP, rel), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in tree.body:
                if (isinstance(node, ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)
                        and node.targets[0].id == "_VAULT_KEY_NAME"
                        and isinstance(node.value, ast.Constant)):
                    names[node.value.value] = rel
        return names

    def test_every_key_a_tool_reads_has_a_row(self):
        tools = self._tool_key_names()
        self.assertTrue(tools, "found no _VAULT_KEY_NAME in tools/ — the scan is broken")
        missing = set(tools) - set(cerberus.KNOWN_KEYS)
        self.assertTrue(not missing,
                        f"read by tools but not in cerberus.KNOWN_KEYS: {sorted(missing)}")

    def test_every_row_is_a_key_some_tool_reads(self):
        stale = set(cerberus.KNOWN_KEYS) - set(self._tool_key_names())
        self.assertTrue(not stale,
                        f"in cerberus.KNOWN_KEYS but no tool reads it: {sorted(stale)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
