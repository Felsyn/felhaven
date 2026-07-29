"""
test_changelog_format.py — hold CHANGELOG.md to one line per change.

The changelog was trimmed from 63 prose entries to 135 one-liners on
2026-07-21 (15,391 -> 2,274 words, -85%). Nothing stopped it drifting back:
the rule lived only in a note at the top of the file, while the git history
is full of paragraph-shaped entries that read as examples worth imitating.
This is the guard — the CONVENTIONS S10 answer of enforcing a doc rule
rather than asking nicely, the same job test_pantheon_docs.py does for the
per-module stubs.

What it deliberately does NOT do: judge whether an entry is well written, or
count entries (a count rots red for the wrong reason). It checks shape only —
one line per change, newest first — because shape is the part a future
session can violate without noticing.

Run from the package root:
    python -X utf8 -m unittest tests.test_changelog_format
"""

import datetime
import os
import re
import unittest

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_APP_ROOT)
_CHANGELOG = os.path.join(_REPO_ROOT, "CHANGELOG.md")

# - `YYYY-MM-DD` — <text>.  The separator is an em dash, not a hyphen: the
# whole file uses one, and a hyphen here would read as a second list marker.
_ENTRY = re.compile("^- `(\\d{4}-\\d{2}-\\d{2})` — (\\S.*)$")

# A policy, not a fact derived from the code, so it cannot go stale the way a
# hardcoded count would — but it still needs a defensible number. Calibrated
# 2026-07-21: the longest honest entry is 210 chars (the five panel renames,
# genuinely one change), and the prose entries this replaced ran 250-5,825.
# Any cap in that gap separates them; 250 leaves room for a real list without
# admitting a paragraph. Raise it only if a *single* change honestly needs it.
_MAX_ENTRY_CHARS = 250


def _lines():
    """Every line of CHANGELOG.md, newline-stripped, 1-indexed by position."""
    with open(_CHANGELOG, encoding="utf-8") as fh:
        return [ln.rstrip("\n") for ln in fh]


def _first_entry_index(lines):
    """Index of the first entry line — everything above it is the header note.

    Returns None when the file contains no entries at all, which the vacuity
    test below turns into a failure rather than letting every other test pass
    over an empty list.
    """
    for i, line in enumerate(lines):
        if _ENTRY.match(line):
            return i
    return None


def _entries():
    """[(line_number, date_str, text)] for every entry, in file order."""
    lines = _lines()
    out = []
    for i, line in enumerate(lines):
        m = _ENTRY.match(line)
        if m:
            out.append((i + 1, m.group(1), m.group(2)))
    return out


class TestTheGuardIsNotVacuous(unittest.TestCase):
    """A shape test that matches nothing passes for the wrong reason."""

    def test_changelog_exists_and_has_entries(self):
        self.assertTrue(os.path.exists(_CHANGELOG),
                        f"{_CHANGELOG} is missing — it is tracked source, so "
                        f"every clone should have it")
        self.assertGreater(len(_entries()), 50,
                           "CHANGELOG.md parsed to almost no entries — the "
                           "entry format changed and this guard went blind")


class TestOneLinePerChange(unittest.TestCase):
    def test_every_line_below_the_header_is_a_single_entry(self):
        """No wrapped prose, no nested bullets, no stray paragraphs.

        The header note at the top is free text; from the first entry down,
        a non-blank line that isn't an entry is prose creeping back in.
        """
        lines = _lines()
        start = _first_entry_index(lines)
        self.assertIsNotNone(start, "CHANGELOG.md has no entries at all")

        for i, line in enumerate(lines[start:], start=start + 1):
            if not line.strip():
                continue
            self.assertRegex(
                line, _ENTRY,
                f"CHANGELOG.md:{i} is below the first entry but is not one. "
                f"Every change gets its own `- `YYYY-MM-DD` — one line` row; "
                f"a date with four changes gets four rows, not a paragraph. "
                f"(The separator is an em dash '—', not a hyphen — that is the "
                f"usual reason an otherwise-fine line fails here.)\n"
                f"  {line[:120]}")

    def test_no_entry_grows_into_a_paragraph(self):
        for lineno, _date, text in _entries():
            self.assertLessEqual(
                len(text), _MAX_ENTRY_CHARS,
                f"CHANGELOG.md:{lineno} is {len(text)} chars, over the "
                f"{_MAX_ENTRY_CHARS} cap. Either it is describing more than "
                f"one change (split it) or it is carrying reasoning — which "
                f"belongs in CONVENTIONS.md S12, not here.\n"
                f"  {text[:120]}...")


class TestEntriesStayOrdered(unittest.TestCase):
    def test_dates_are_real_dates(self):
        for lineno, date, _text in _entries():
            try:
                datetime.date.fromisoformat(date)
            except ValueError:
                self.fail(f"CHANGELOG.md:{lineno} has date {date!r}, which is "
                          f"not a real calendar date")

    def test_newest_first(self):
        """The header promises newest-first; a claim in prose is a claim."""
        entries = _entries()
        for (prev_no, prev_date, _p), (no, date, _t) in zip(entries,
                                                            entries[1:]):
            self.assertGreaterEqual(
                datetime.date.fromisoformat(prev_date),
                datetime.date.fromisoformat(date),
                f"CHANGELOG.md:{no} ({date}) sorts newer than the entry above "
                f"it at line {prev_no} ({prev_date}) — the file is newest-first")


if __name__ == "__main__":
    unittest.main()
