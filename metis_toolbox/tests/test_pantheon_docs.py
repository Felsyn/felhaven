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

import ast
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


def _source_file(module, text):
    """The .py file a stub is *about*, read off its own Files field.

    CONVENTIONS S0 puts the gloss in the module's header docstring, so the test
    needs module -> file. Deriving it from the Files field the stub already
    maintains (pick the entry whose basename is <module>.py) means no fourth
    hand-written list to drift: a moved file fails the Files guard above first.
    """
    m = _FILES_FIELD.search(text)
    if m is None:
        return None
    for rel in _BACKTICKED.findall(m.group(1)):
        if os.path.basename(rel) == module + ".py":
            return os.path.join(_APP_ROOT, rel.replace("/", os.sep))
    return None


def _first_sentence(text):
    """Leading sentence of a blob, whitespace collapsed.

    The gloss is the FIRST sentence: S0 says later ones elaborate and are not
    shown, so only this much has to agree across the three copies.
    """
    flat = " ".join(text.split())
    m = re.match(r"(.*?[.!?])(\s|$)", flat)
    return m.group(1) if m else flat


def _docstring_gloss(path):
    """First sentence of the module docstring's `Job:` field, or None.

    Parsed with ast rather than imported: importing every module would drag in
    psutil, requests and a Tk root just to read a string.
    """
    with open(path, encoding="utf-8") as fh:
        doc = ast.get_docstring(ast.parse(fh.read()))
    if not doc:
        return None
    m = re.search(r"^Job:\s*(.+?)(?=^\w[\w ]*:|\Z)", doc, re.MULTILINE | re.DOTALL)
    return _first_sentence(m.group(1)) if m else None


def _index_rows():
    """{module: Job cell} from the README_PANTHEON index table."""
    with open(os.path.join(_PANTHEON, "README.md"), encoding="utf-8") as fh:
        body = fh.read()
    return dict(re.findall(r"^\| \[.+?\]\((\w+)\.md\) \| (.+?) \|$", body,
                           re.MULTILINE))


class TestJobGlossStaysInLockstep(unittest.TestCase):
    """The gloss exists three times; only one copy sits next to the code.

    CONVENTIONS S0 makes the `Job:` first sentence the text any hover/tooltip
    surface reads verbatim, and it is duplicated into each stub's opening line
    and the index table. That is a derivable claim in prose, which S12 says to
    delete or generate rather than trust -- so it is asserted here instead.
    The docstring is the authority: it cannot drift from the code silently.
    """

    def test_stub_and_index_match_the_docstring(self):
        index = _index_rows()
        for module, text in _stubs().items():
            with self.subTest(module=module):
                path = _source_file(module, text)
                self.assertIsNotNone(
                    path, f"{module}.md lists no file named {module}.py, so the "
                          f"gloss cannot be traced back to a docstring")

                doc_gloss = _docstring_gloss(path)
                self.assertIsNotNone(
                    doc_gloss,
                    f"{os.path.relpath(path, _APP_ROOT)} has no `Job:` field — "
                    f"CONVENTIONS S0 requires one in every header docstring")

                # Body of the stub = everything after the "# Title" line.
                stub_body = text.split("\n", 1)[1].lstrip("\n")
                self.assertEqual(
                    _first_sentence(stub_body), doc_gloss,
                    f"\n{module}.md opens with a different gloss than "
                    f"{os.path.relpath(path, _APP_ROOT)}. The docstring wins.")

                self.assertIn(module, index,
                              f"{module}.md exists but has no row in the "
                              f"README_PANTHEON index table")
                self.assertEqual(
                    _first_sentence(index[module]), doc_gloss,
                    f"\nThe index row for {module} differs from "
                    f"{os.path.relpath(path, _APP_ROOT)}. The docstring wins.")


if __name__ == "__main__":
    unittest.main()
