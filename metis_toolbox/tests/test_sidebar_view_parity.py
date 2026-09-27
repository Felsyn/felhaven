"""
test_sidebar_view_parity.py — the three places a view is named must agree.

There are THREE, not two: Sidebar._ROWS/_GROUPS (the rail), FelhavenApp._views
(the panels), and README.md's Views table (what a reader is told exists). The
first two are joined by a string key and fail loudly when they disagree; the
third is joined by nothing at all and fails silently, which is why it was the one
that rotted — the Cartograph view shipped in August 2026 and the README table
never learned about it until 2026-09-16.

The README edge is guarded by LABEL rather than by key, because the label is what
the table shows and what a reader matches against the rail.

Adding a view means editing those places, joined only by a string key.
A name in one but not the other is a runtime KeyError in _show_view — the kind
of drift the lockstep rule exists to catch, so assert the halves are *derived*
and equal, never that any of them has some hardcoded count.

This is a STATIC source check: FelhavenApp.__init__ builds every panel, starts
Kairos, and prewarms Pythia/Calliope, none of which belongs in a unit test. So
the _views keys are read out of felhaven.py's source with ast instead of by
constructing the app. The cost is that it only sees a dict literal — if _views
ever stops being one, _views_keys() raises rather than silently passing.

    python -X utf8 -m unittest tests.test_sidebar_view_parity
"""

import ast
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panels.sidebar import Sidebar

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FELHAVEN = os.path.join(_APP_ROOT, "felhaven.py")
# The README lives at the CLONE root, one above the app root (CONVENTIONS §1).
_README = os.path.join(os.path.dirname(_APP_ROOT), "README.md")

# A row of the Views table: `| **Celestarium** | Zenith star map ... |`
_VIEW_ROW = re.compile(r"^\|\s*\*\*(?P<label>[^*]+)\*\*\s*\|(?P<shows>.*)\|\s*$")


def _views_keys() -> set[str]:
    """The string keys of the `self._views = {...}` dict literal in felhaven.py."""
    with open(_FELHAVEN, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), _FELHAVEN)
    for node in ast.walk(tree):
        # `self._views: dict[...] = {...}` is an AnnAssign; a bare
        # `self._views = {...}` would be an Assign. Accept either.
        targets = ([node.target] if isinstance(node, ast.AnnAssign)
                   else node.targets if isinstance(node, ast.Assign) else [])
        for target in targets:
            if (isinstance(target, ast.Attribute) and target.attr == "_views"
                    and isinstance(node.value, ast.Dict)):
                return {k.value for k in node.value.keys}
    raise AssertionError("no `self._views = {...}` dict literal in felhaven.py")


def _selectable_keys() -> set[str]:
    """Every sidebar name that can reach on_select — leaves plus group children.
    Group headers are excluded: they toggle, they don't select."""
    leaves   = {n for n, _l, _s in Sidebar._ROWS if n not in Sidebar._GROUPS}
    children = {n for kids in Sidebar._GROUPS.values() for n, _l, _s in kids}
    return leaves | children


def _readme_view_rows() -> list[tuple[str, str]]:
    """(label, description) for each row of README.md's Views table, in order.

    Scoped to the `## Views` section rather than the whole file on purpose: the
    "How the modules connect" table above it uses the same `| **Name** |` shape,
    and a document-wide scan would silently pull Kairos and Harmonia in as
    views."""
    with open(_README, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    try:
        start = lines.index("## Views")
    except ValueError:
        raise AssertionError("README.md has no `## Views` section")
    rows = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break                      # next section — stop
        found = _VIEW_ROW.match(line)
        if found:
            rows.append((found.group("label").strip(),
                         found.group("shows").strip()))
    assert rows, "no view rows found under README.md's `## Views` section"
    return rows


def _selectable_labels() -> list[str]:
    """Every selectable row's LABEL, in the order the rail presents them —
    group children inline under their header, headers themselves omitted."""
    labels = []
    for name, label, _sub in Sidebar._ROWS:
        if name in Sidebar._GROUPS:
            labels.extend(child_label
                          for _n, child_label, _s in Sidebar._GROUPS[name])
        else:
            labels.append(label)
    return labels


class TestSidebarViewParity(unittest.TestCase):

    def test_every_selectable_row_has_a_view(self):
        missing = _selectable_keys() - _views_keys()
        self.assertEqual(missing, set(),
                         f"sidebar rows with no felhaven._views entry: {missing}")

    def test_every_view_has_a_selectable_row(self):
        unreachable = _views_keys() - _selectable_keys()
        self.assertEqual(unreachable, set(),
                         f"felhaven._views entries no sidebar row reaches: {unreachable}")

    def test_group_headers_are_not_views(self):
        # A header with a _views entry would mean it is really a view wearing a
        # header's clothes — the shape this design deliberately avoids.
        overlap = set(Sidebar._GROUPS) & _views_keys()
        self.assertEqual(overlap, set(),
                         f"group headers must not be views: {overlap}")


class TestReadmeViewsTable(unittest.TestCase):
    """The edge nothing was watching.

    The sidebar and felhaven._views disagree loudly — a missing key is a
    KeyError the moment you click the row. README.md's Views table disagrees
    SILENTLY: it is prose, nothing imports it, and a reader simply never learns
    the view exists. Cartograph shipped in August 2026 and was absent from that
    table until 2026-09-16, which is exactly how long it takes to notice.
    """

    def test_every_view_is_in_the_readme_table(self):
        listed = {label for label, _shows in _readme_view_rows()}
        missing = set(_selectable_labels()) - listed
        self.assertEqual(missing, set(),
                         f"views the README's table never mentions: "
                         f"{sorted(missing)}")

    def test_the_readme_table_invents_no_views(self):
        listed = {label for label, _shows in _readme_view_rows()}
        invented = listed - set(_selectable_labels())
        self.assertEqual(invented, set(),
                         f"README lists views the sidebar cannot reach: "
                         f"{sorted(invented)}")

    def test_the_table_is_in_rail_order(self):
        """The table's own promise is "one sidebar row per view", so a reader
        should be able to run a finger down the rail and down the table
        together. Asserted separately from membership so it can be relaxed on
        its own if that ever becomes more trouble than it is worth."""
        self.assertEqual([label for label, _shows in _readme_view_rows()],
                         _selectable_labels())

    def test_every_row_actually_says_something(self):
        """A label with an empty Shows cell is the shape a half-finished row
        takes, and it would pass every assertion above."""
        for label, shows in _readme_view_rows():
            with self.subTest(view=label):
                self.assertTrue(shows.strip(), f"{label} has an empty cell")


if __name__ == "__main__":
    unittest.main()
