"""
test_sidebar_view_parity.py — the sidebar's keys and felhaven._views must agree.

Adding a view means editing two places joined only by a string key:
Sidebar._ROWS/_GROUPS (panels/sidebar.py) and FelhavenApp._views (felhaven.py).
A name in one but not the other is a runtime KeyError in _show_view — the kind
of drift the lockstep rule exists to catch, so assert the two halves are
*derived* and equal, never that either has some hardcoded count.

This is a STATIC source check: FelhavenApp.__init__ builds every panel, starts
Kairos, and prewarms Pythia/Calliope, none of which belongs in a unit test. So
the _views keys are read out of felhaven.py's source with ast instead of by
constructing the app. The cost is that it only sees a dict literal — if _views
ever stops being one, _views_keys() raises rather than silently passing.

    python -X utf8 -m unittest tests.test_sidebar_view_parity
"""

import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panels.sidebar import Sidebar

_FELHAVEN = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "felhaven.py")


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


if __name__ == "__main__":
    unittest.main()
