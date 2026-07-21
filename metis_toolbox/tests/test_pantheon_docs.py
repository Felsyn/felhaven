"""
test_pantheon_docs.py — keep README_PANTHEON in lockstep with the code.

The pantheon stubs assert two things that can quietly go stale: which brain tool
a module exposes, and which files it owns. Both are derivable, so both are
guarded here rather than trusted — a renamed tool or a moved file turns a test
red instead of misleading a reader. Nothing here hardcodes a count (a count rots
red for the wrong reason); every assertion compares two live sources.

Run from the package root:
    python -X utf8 -m unittest tests.test_pantheon_docs
"""

import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pythia

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PANTHEON = os.path.join(_APP_ROOT, "README_PANTHEON")

# "- **Brain tool:** `x`" / "- **Brain tools:** `x`, `y`" — possibly wrapped onto
# a continuation line, hence the DOTALL-ish capture up to the next bullet.
_TOOL_FIELD = re.compile(r"^- \*\*Brain tools?:\*\*(.*?)(?=^- \*\*|\Z)",
                         re.MULTILINE | re.DOTALL)
_FILES_FIELD = re.compile(r"^- \*\*Files:\*\*(.*?)(?=^- \*\*|\Z)",
                          re.MULTILINE | re.DOTALL)
_BACKTICKED = re.compile(r"`([^`]+)`")


def _stubs():
    """{module_name: file text} for every stub (the index is not a stub)."""
    out = {}
    for name in os.listdir(_PANTHEON):
        if not name.endswith(".md") or name == "README.md":
            continue
        with open(os.path.join(_PANTHEON, name), encoding="utf-8") as fh:
            out[name[:-3]] = fh.read()
    return out


def _claimed_tools(text):
    """Tool names a stub claims, or [] when it says 'none'.

    A "none" field may still name a tool in prose (kepler reaches the model
    through hypatia's get_sky_tonight), so "none" short-circuits before the
    backtick scan — otherwise the explanation reads as a claim.
    """
    m = _TOOL_FIELD.search(text)
    if m is None:
        return []
    body = m.group(1).strip()
    if body.lower().startswith("none"):
        return []
    return _BACKTICKED.findall(body)


class TestEveryBrainToolModuleHasAStub(unittest.TestCase):
    def test_registered_modules_are_documented(self):
        stubs = _stubs()
        for module in pythia._TOOL_MODULES:
            short = module.__name__.rsplit(".", 1)[-1]
            self.assertIn(short, stubs,
                          f"{short} is in pythia._TOOL_MODULES but has no "
                          f"README_PANTHEON/{short}.md")


class TestToolNamesStayInLockstep(unittest.TestCase):
    def test_every_dispatched_tool_is_claimed_exactly_once(self):
        claims = {}
        for module, text in _stubs().items():
            for tool in _claimed_tools(text):
                claims.setdefault(tool, []).append(module)

        for tool in pythia._DISPATCH:
            owners = claims.get(tool, [])
            self.assertEqual(len(owners), 1,
                             f"{tool} is in pythia._DISPATCH but claimed by "
                             f"{owners or 'no stub'} — expected exactly one")

    def test_no_stub_claims_a_tool_that_does_not_exist(self):
        for module, text in _stubs().items():
            for tool in _claimed_tools(text):
                self.assertIn(tool, pythia._DISPATCH,
                              f"{module}.md claims tool {tool!r}, which is not "
                              f"in pythia._DISPATCH")


def _tracked_files():
    """Paths git tracks under the app root, relative to it — or None if git
    can't answer (no git binary, or an unpacked tarball rather than a clone).

    Tracked-ness, not existence on disk, is the right question. A gitignored
    per-user file (playlists, a PIN hash) is *state*, not source (CONVENTIONS
    S9): it is absent on a fresh clone by design, so listing one as a module's
    file makes a promise the repo doesn't keep. Checking the filesystem instead
    passes on the author's machine, where that state happens to exist, and
    fails only in CI — which is exactly how this test first shipped broken.
    """
    try:
        out = subprocess.run(["git", "ls-files"], cwd=_APP_ROOT, check=True,
                             capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return {line.replace("\\", "/") for line in out.splitlines() if line}


class TestStubFilePathsExist(unittest.TestCase):
    def test_every_files_entry_is_tracked(self):
        tracked = _tracked_files()
        if tracked is None:
            self.skipTest("git unavailable — cannot check tracked-ness")
        for module, text in _stubs().items():
            m = _FILES_FIELD.search(text)
            self.assertIsNotNone(m, f"{module}.md has no Files field")
            paths = _BACKTICKED.findall(m.group(1))
            self.assertTrue(paths, f"{module}.md lists no files")
            for rel in paths:
                # assertTrue, not assertIn: assertIn dumps the whole tracked
                # set (140+ paths) into the failure and buries the message.
                self.assertTrue(
                    rel in tracked,
                    f"{module}.md lists {rel}, which git does not track — it is "
                    f"either a wrong path or gitignored state that won't exist "
                    f"on a fresh clone")


if __name__ == "__main__":
    unittest.main()
