"""
test_ci_pins.py — every GitHub Action CI runs is pinned to a commit.

A step written `uses: actions/checkout@v7` runs whatever commit the tag `v7`
points at on the day of the run. Tags move: the owner moves them on every
release, and so could anyone who gained control of that repository. A full
commit SHA can't be moved, so CI keeps running the code that was checked
when the pin was set (CONVENTIONS §12).

The release tag stays beside the SHA as a comment (`# v7.0.1`), because a
bare SHA tells a reader nothing about which release it is or how old.

Workflows are found with `git ls-files`, not the disk, so an untracked
scratch file can't make this pass or fail. Finding no `uses:` line at all is
a failure: a guard that matched nothing would pass forever.

    python -X utf8 -m unittest tests.test_ci_pins
"""

import os
import re
import subprocess
import unittest

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_APP_ROOT)

_USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)(.*)$")
_PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
_TAG_COMMENT = re.compile(r"#\s*v\d+(\.\d+)*\b")


def _workflows():
    out = subprocess.run(["git", "ls-files", ".github/workflows"], cwd=_REPO_ROOT,
                         check=True, capture_output=True, text=True).stdout
    return [p for p in out.splitlines() if p.endswith((".yml", ".yaml"))]


class TestCiPins(unittest.TestCase):

    def test_every_action_is_a_commit_sha_with_its_tag(self):
        seen = 0
        for rel in _workflows():
            with open(os.path.join(_REPO_ROOT, rel), encoding="utf-8") as fh:
                for n, line in enumerate(fh, start=1):
                    m = _USES.match(line)
                    if m is None or m.group(1).startswith("./"):
                        continue  # not a step, or an action in this repo
                    seen += 1
                    with self.subTest(where=f"{rel}:{n}"):
                        self.assertRegex(m.group(1), _PINNED)
                        self.assertRegex(m.group(2), _TAG_COMMENT)
        self.assertGreater(seen, 0, "no 'uses:' line found in any tracked workflow")


if __name__ == "__main__":
    unittest.main(verbosity=2)
