"""
test_scroll_lanes.py — every text box's scrollbar is packed before the box.

CONVENTIONS §7. A tk.Text asks for 80 characters of width (574 px at the
base font, 658 px in Home's). Packed before its PhosphorScroll, it took the
whole row of a narrow window, and pack unmapped the scrollbar — at exactly the
size where the text overflows. Measured 2026-11-09 at the 760 px minimum
window: all five boxes (Home's chat log, Emanon's log, the Daedalus pane, the
TUNES and MACHINE SPIRIT editors) had a 1 px, unmapped scrollbar.

Read with ast, as Daedalus reads source: for every tk.Text that some
PhosphorScroll scrolls (command=<the text>.yview) in the same function, the
scrollbar's first .pack() must come before the text's. A box packed with grid
or place instead has no pack order to check, so it fails here until someone
looks at it and decides. test_machine_spirit_panel_smoke proves the behaviour
in a real, mapped window.

Run from the package root:
    python -X utf8 -m unittest tests.test_scroll_lanes
"""

import ast
import glob
import os
import unittest

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sources():
    for pattern in ("*.py", os.path.join("panels", "*.py")):
        for path in sorted(glob.glob(os.path.join(_APP_ROOT, pattern))):
            yield os.path.relpath(path, _APP_ROOT).replace(os.sep, "/"), path


def _is_tk_text(call):
    return (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr == "Text" and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "tk")


def _scrolled_by(call):
    """The widget a PhosphorScroll(...) call scrolls, as source text, or None."""
    if not isinstance(call, ast.Call):
        return None
    name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
    if name != "PhosphorScroll":
        return None
    for kw in call.keywords:
        if (kw.arg == "command" and isinstance(kw.value, ast.Attribute)
                and kw.value.attr == "yview"):
            return ast.unparse(kw.value.value)
    return None


def scroll_pairs(tree):
    """[(function, text, scroll, text's first pack line, scroll's first pack
    line)] for every Text a PhosphorScroll scrolls in the same function."""
    out = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        texts, scrolls, packs = set(), {}, {}
        for node in ast.walk(func):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = ast.unparse(node.targets[0])
                if _is_tk_text(node.value):
                    texts.add(target)
                scrolled = _scrolled_by(node.value)
                if scrolled is not None:
                    scrolls[target] = scrolled
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "pack"):
                who = ast.unparse(node.func.value)
                packs[who] = min(packs.get(who, node.lineno), node.lineno)
        for scroll, text in scrolls.items():
            if text in texts:
                out.append((func.name, text, scroll,
                            packs.get(text), packs.get(scroll)))
    return out


class TestEveryScrollLaneIsPackedFirst(unittest.TestCase):

    def test_the_scrollbar_is_packed_before_its_text(self):
        found = []
        for rel, path in _sources():
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=rel)
            for func, text, scroll, text_line, scroll_line in scroll_pairs(tree):
                found.append(rel)
                with self.subTest(file=rel, text=text):
                    self.assertIsNotNone(
                        text_line, f"{rel}:{func}: {text} is never packed — a grid "
                                   f"or place layout needs its own look (§7)")
                    self.assertIsNotNone(
                        scroll_line, f"{rel}:{func}: {scroll} is never packed")
                    self.assertLess(
                        scroll_line, text_line,
                        f"{rel}:{func}: {text} is packed (line {text_line}) before "
                        f"its scrollbar {scroll} (line {scroll_line}); on a narrow "
                        f"window the Text's 80-character request takes the row and "
                        f"pack unmaps the bar. Pack the scrollbar first (§7).")
        self.assertTrue(found, "no Text with a PhosphorScroll was found — the guard "
                               "would be vacuous")

    def test_the_reader_sees_a_pair_in_each_order(self):
        """The guard's own reader, on two snippets, so a reader that finds
        nothing can't pass the real test vacuously."""
        good = ast.parse(
            "def build(self):\n"
            "    self._log = tk.Text(row)\n"
            "    scroll = PhosphorScroll(row, command=self._log.yview)\n"
            "    scroll.pack(side='right')\n"
            "    self._log.pack(side='left')\n")
        bad = ast.parse(
            "def build(self):\n"
            "    self._log = tk.Text(row)\n"
            "    self._log.pack(side='left')\n"
            "    scroll = PhosphorScroll(row, command=self._log.yview)\n"
            "    scroll.pack(side='right')\n")
        (_, _, _, text_line, scroll_line), = scroll_pairs(good)
        self.assertLess(scroll_line, text_line)
        (_, _, _, text_line, scroll_line), = scroll_pairs(bad)
        self.assertGreater(scroll_line, text_line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
