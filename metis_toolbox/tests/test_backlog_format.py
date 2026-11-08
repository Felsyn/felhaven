"""
test_backlog_format.py — hold BACKLOG.md to its shape.

BACKLOG.md is the one home for things found but not fixed (2026-09-29). It
stays true only because a fix deletes its line in the same commit. The two
ways it would quietly rot are the ones checked here:

  - it grows a "done" section or strikethrough, turning into a second
    changelog. CHANGELOG.md already records fixes, and N copies drift at N×
    the rate (CONVENTIONS §12);
  - items lose their provenance. A line with no "found" date can't be judged
    stale, and one with no "needs" can't be picked up.

It also pins the owner's rule at the top of the file: security issues never
go in the backlog, they are fixed in the session that finds them. Deleting
that sentence should take a deliberate edit to this test too.

Shape only, like test_changelog_format.py: no judging whether an item is well
written, and no count. An empty backlog is a good day, not a failure.

    python -X utf8 -m unittest tests.test_backlog_format
"""

import os
import re
import unittest

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_APP_ROOT)
_BACKLOG = os.path.join(_REPO_ROOT, "BACKLOG.md")

_FOUND = re.compile(r"· found \d{4}-\d{2}-\d{2}\b")
_NEEDS = re.compile(r"— needs: \S")
_SECURITY_RULE = "Security issues never go here."
_DONE_HEADING = re.compile(r"^#+ .*\b(done|fixed|closed|resolved)\b", re.IGNORECASE)


def _lines():
    with open(_BACKLOG, encoding="utf-8") as fh:
        return [ln.rstrip("\n") for ln in fh]


def _split(lines):
    """(header lines, body lines). The header's own rules are bullets too, so
    items are only read below the first '---' rule."""
    for i, line in enumerate(lines):
        if line.strip() == "---":
            return lines[:i], lines[i + 1:]
    return lines, []


def _items(body):
    """Each top-level bullet with its indented continuation lines, joined."""
    items, current = [], None
    for line in body:
        if line.startswith("- "):
            current = [line]
            items.append(current)
        elif current is not None and line.startswith("  ") and line.strip():
            current.append(line.strip())
        else:
            current = None
    return [" ".join(parts) for parts in items]


class TestBacklogFormat(unittest.TestCase):

    def setUp(self):
        self.header, self.body = _split(_lines())

    def test_has_a_header_rule_and_a_body(self):
        self.assertTrue(self.body or any(l.strip() == "---" for l in self.header),
                        "BACKLOG.md needs a '---' rule between its rules and its items")

    def test_the_security_rule_is_in_the_header(self):
        self.assertIn(_SECURITY_RULE, "\n".join(self.header))

    def test_every_item_says_what_it_needs_and_when_it_was_found(self):
        for item in _items(self.body):
            with self.subTest(item=item[:70]):
                self.assertRegex(item, _NEEDS)
                self.assertRegex(item, _FOUND)

    def test_nothing_is_kept_as_done(self):
        # A fixed item is deleted, not crossed out: CHANGELOG records the fix.
        for n, line in enumerate(self.body, start=1):
            with self.subTest(line=n):
                self.assertNotIn("~~", line)
                self.assertNotRegex(line, r"\[[ xX]\]")
                self.assertNotRegex(line, _DONE_HEADING)


if __name__ == "__main__":
    unittest.main(verbosity=2)
