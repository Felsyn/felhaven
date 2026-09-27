"""
test_palette_guard.py — colour values live in palette.py and nowhere else.

CONVENTIONS §7 has always said colours come from theme.C, and it held — except
where it quietly didn't. tools/demeter.py carried four RGB tuple copies of the
palette (kept honest by a lockstep test), and panels/pythia_face.py blended
toward a hardcoded white. A rule with nothing behind it is a suggestion; this
is the guard. Since 2026-09-26 the values live in palette.py (Tk-free, so a
headless tools/ module can import it), and theme.py re-exports them.

Four rules, each over every .py in the app root, tools/, panels/ and tests/
(palette.py itself excepted):

  1. HEX — a string that IS a Tk hex colour (#RGB up to #RRRRGGGGBBBB) is a
     colour by construction. Everywhere, tests included: a test asserting a
     widget is "#7CFC7C" is a second copy of the palette, and would have to be
     edited when the palette is retuned. It must assert against C[...].
  2. NAMED — a string in a colour slot (fg=, fill=, outline=, a {"bg": ...}
     option dict...) that Tk itself resolves as a colour. Tk is the authority,
     not a hand-kept list of names: pack(fill="x") passes, fg="white" fails.
     C["red"] is a key lookup, not a colour, and is never flagged.
  3. TRIPLE — three int literals in 0..255, as a tuple or list. App code only:
     in tests a triple is pixel DATA fed to a decoder (test_artemis_ppm_spike),
     and must NOT follow the palette. A triple on the right of `in` is a set of
     alternatives (`m in (3, 4, 5)`), never a colour, and is skipped. What is
     left and still isn't a colour goes in _NOT_COLOURS with its reason.
  4. PALETTE — inside palette.py: every value is #RRGGBB (rgb() parses nothing
     else); an alias in _SAME_AS points at a key with its own value and is not
     ALSO a literal (the update would silently overwrite it); and no two
     literal keys share a value unless _COINCIDENCES says why. Two keys with
     one value are either the same colour on purpose (identity — calculate one
     from the other in _SAME_AS) or by accident (coincidence — declare it
     here). Undeclared, a reader cannot tell which, and a retune has to guess.

What it cannot see, by construction: a colour computed at runtime (fine when
it is computed FROM the palette, as pythia_face's ramps are), a colour name
passed positionally, and a colour nobody wrote at all — a Tk default. The
ones found so far (the Windows-blue selection, the black text cursor) are now
set app-wide by theme._init_widget_defaults and held by test_theme; a Tk
default nobody has noticed yet still gets past this file.

The allowlists are the house pattern (test_tool_output_contract): each entry
carries its reason, and an entry that no longer matches anything fails —
a stale exemption is exactly the drift this file exists to remove.

Run from the package root:
    python -X utf8 -m unittest tests.test_palette_guard
"""

import ast
import os
import re
import subprocess
import sys
import tkinter as tk
import unittest

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _APP_ROOT)

import palette

_PALETTE_FILE = "palette.py"

# Where a Felhaven .py can live — flat, like test_tool_output_contract.
# test_every_tracked_py_is_scanned fails if a tracked file lives elsewhere, so
# a new subfolder cannot slip past the scan unnoticed.
_SCANNED_DIRS = ("", "tools", "panels", "tests")
_APP_DIRS = ("", "tools", "panels")          # rule 3 only

# Tk's hex forms: 1, 2, 3 or 4 hex digits per channel.
_HEX = re.compile(r"#(?:[0-9A-Fa-f]{3}){1,4}")
_PALETTE_VALUE = re.compile(r"#[0-9A-Fa-f]{6}")

# Keyword arguments and option-dict keys that take a colour: Tk widget options,
# canvas item options, and theme.Card's own dot_color.
_COLOUR_OPTIONS = frozenset({
    "bg", "background", "fg", "foreground",
    "activebackground", "activeforeground", "disabledforeground",
    "disabledbackground", "readonlybackground",
    "highlightbackground", "highlightcolor",
    "insertbackground", "selectbackground", "selectforeground",
    "inactiveselectbackground", "selectcolor", "troughcolor",
    "fill", "outline", "activefill", "activeoutline",
    "disabledfill", "disabledoutline",
    "dot_color", "color", "colour",
})

# Rule 3 exemptions: (file, source text) -> why it is not a colour.
_NOT_COLOURS: dict[tuple[str, str], str] = {
    ("tools/artemis.py", "(1, 2, 4)"): "ZOOM_STOPS — the map's display scales",
}

# Literal palette keys that share a value by coincidence, not identity.
_COINCIDENCES: dict[frozenset[str], str] = {
    frozenset({"verbatim", "map_water"}):
        "the chat's quotation cyan borrowed the rivers' value, but retuning "
        "the rivers must never recolour the chat (§12, 2026-09-26)",
}


# ─────────────────────────────────────────────────────────────────────────────
#  The three detectors — pure functions of (tree, source), so the vacuity
#  tests below can feed them snippets and prove each one actually fires.
# ─────────────────────────────────────────────────────────────────────────────

def _hex_hits(tree):
    """[(lineno, detail)] for every string literal that is a hex colour."""
    return [(n.lineno, f"hex colour {n.value!r}")
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and _HEX.fullmatch(n.value)]


def _named_hits(tree, is_colour):
    """[(lineno, detail)] for a Tk colour name in a colour slot."""
    lookups = {id(n.slice) for n in ast.walk(tree) if isinstance(n, ast.Subscript)}

    def names_in(expr, slot):
        for n in ast.walk(expr):
            if (isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and id(n) not in lookups
                    and not _HEX.fullmatch(n.value)       # rule 1's already
                    and is_colour(n.value)):
                yield (n.lineno, f"colour name {n.value!r} in {slot}")

    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in _COLOUR_OPTIONS:
                    hits.extend(names_in(kw.value, f"{kw.arg}="))
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value in _COLOUR_OPTIONS:
                    hits.extend(names_in(value, f"{{{key.value!r}: ...}}"))
    return hits


def _triple_hits(tree, src):
    """[(lineno, source text)] for three int literals in 0..255."""
    alternatives = {id(comp) for n in ast.walk(tree) if isinstance(n, ast.Compare)
                    for op, comp in zip(n.ops, n.comparators)
                    if isinstance(op, (ast.In, ast.NotIn))}
    hits = []
    for n in ast.walk(tree):
        if (isinstance(n, (ast.Tuple, ast.List)) and len(n.elts) == 3
                and id(n) not in alternatives
                and all(isinstance(e, ast.Constant) and type(e.value) is int
                        and 0 <= e.value <= 255 for e in n.elts)):
            hits.append((n.lineno, ast.get_source_segment(src, n)))
    return hits


# ─────────────────────────────────────────────────────────────────────────────
#  The scan
# ─────────────────────────────────────────────────────────────────────────────

def _scanned_files():
    """Every .py in the scanned folders, relative to the app root, '/'-joined."""
    out = []
    for folder in _SCANNED_DIRS:
        path = os.path.join(_APP_ROOT, folder)
        for name in sorted(os.listdir(path)):
            if name.endswith(".py"):
                out.append(f"{folder}/{name}" if folder else name)
    return out


def _parse(rel):
    with open(os.path.join(_APP_ROOT, rel), encoding="utf-8") as fh:
        src = fh.read()
    return ast.parse(src), src


def _tracked_py():
    """Tracked .py paths relative to the app root, or None without git."""
    try:
        out = subprocess.run(["git", "ls-files", "*.py"], cwd=_APP_ROOT,
                             check=True, capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [line.replace("\\", "/") for line in out.splitlines() if line]


def _palette_literal_keys():
    """The keys written as literals in palette.py's `C = {...}`, read from
    source — so an alias that is ALSO a literal is visible, not overwritten."""
    tree, _ = _parse(_PALETTE_FILE)
    for node in tree.body:
        target = (node.target if isinstance(node, ast.AnnAssign)
                  else node.targets[0] if isinstance(node, ast.Assign)
                  else None)
        if (isinstance(target, ast.Name) and target.id == "C"
                and isinstance(node.value, ast.Dict)):
            return [k.value for k in node.value.keys]
    raise AssertionError("palette.py has no `C = {...}` literal")


def _fmt(found):
    return "\n".join(f"  {rel}:{line}  {detail}" for rel, line, detail in found)


class TestNoColourOutsideThePalette(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        cache = {}

        def is_colour(text):
            if text not in cache:
                try:
                    cls.root.winfo_rgb(text)
                    cache[text] = True
                except tk.TclError:
                    cache[text] = False
            return cache[text]

        cls.hex, cls.named, cls.triples = [], [], []
        for rel in _scanned_files():
            if rel == _PALETTE_FILE:
                continue
            tree, src = _parse(rel)
            cls.hex += [(rel, ln, d) for ln, d in _hex_hits(tree)]
            cls.named += [(rel, ln, d) for ln, d in _named_hits(tree, is_colour)]
            if os.path.dirname(rel) in _APP_DIRS:
                cls.triples += [(rel, ln, d) for ln, d in _triple_hits(tree, src)]

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_no_hex_colour_outside_the_palette(self):
        self.assertFalse(self.hex, "hex colour literals outside palette.py — "
                         "use C[...] (add a key to palette.py if none fits):\n"
                         + _fmt(self.hex))

    def test_no_named_colour_in_a_colour_slot(self):
        self.assertFalse(self.named, "Tk colour names outside palette.py — "
                         "use C[...] (add a key to palette.py if none fits):\n"
                         + _fmt(self.named))

    def test_no_rgb_triple_in_app_code(self):
        unexplained = [(rel, ln, d) for rel, ln, d in self.triples
                       if (rel, d) not in _NOT_COLOURS]
        self.assertFalse(unexplained, "RGB-shaped triples in app code — a "
                         "colour belongs in palette.py (convert with "
                         "palette.rgb); if this is not a colour, add it to "
                         "_NOT_COLOURS with the reason:\n" + _fmt(unexplained))

    def test_not_colours_has_no_stale_entries(self):
        seen = {(rel, d) for rel, _ln, d in self.triples}
        stale = [key for key in _NOT_COLOURS if key not in seen]
        self.assertFalse(stale, f"_NOT_COLOURS entries that match nothing "
                         f"any more — delete them: {stale}")


class TestThePaletteItself(unittest.TestCase):

    def test_every_value_is_six_digit_hex(self):
        for key, value in palette.C.items():
            with self.subTest(key=key):
                self.assertTrue(_PALETTE_VALUE.fullmatch(value),
                                f"C[{key!r}] = {value!r} is not #RRGGBB, "
                                f"which is all rgb() parses")

    def test_aliases_point_at_a_colour_of_their_own(self):
        literal = set(_palette_literal_keys())
        for alias, target in palette._SAME_AS.items():
            with self.subTest(alias=alias):
                self.assertFalse(alias in literal,
                                 f"{alias!r} is both a literal and an alias "
                                 f"of {target!r} — the update overwrites the "
                                 f"literal silently. Keep one.")
                self.assertTrue(target in literal,
                                f"{alias!r} -> {target!r}: the target must "
                                f"be a key with its own value, not another "
                                f"alias")
                self.assertEqual(palette.C[alias], palette.C[target])

    def _shared_values(self):
        """frozensets of literal keys that share one value."""
        by_value = {}
        for key in _palette_literal_keys():
            by_value.setdefault(palette.C[key].upper(), set()).add(key)
        return {frozenset(keys) for keys in by_value.values() if len(keys) > 1}

    def test_no_undeclared_shared_value(self):
        undeclared = [sorted(keys) for keys in self._shared_values()
                      if keys not in _COINCIDENCES]
        self.assertFalse(undeclared, f"literal palette keys sharing a value: "
                         f"{undeclared}. Same colour on purpose? Move one into "
                         f"palette._SAME_AS. By accident? Declare it in "
                         f"_COINCIDENCES with the reason.")

    def test_coincidences_have_no_stale_entries(self):
        stale = [sorted(keys) for keys in _COINCIDENCES
                 if keys not in self._shared_values()]
        self.assertFalse(stale, f"_COINCIDENCES entries whose keys no longer "
                         f"share a value — delete them: {stale}")


class TestTheGuardIsReal(unittest.TestCase):
    """A scan that looks at nothing, or detectors that fire on nothing, pass
    for the wrong reason. Each detector is shown to fire before it is trusted."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _is_colour(self, text):
        try:
            self.root.winfo_rgb(text)
            return True
        except tk.TclError:
            return False

    def test_the_scan_reaches_every_kind_of_file(self):
        scanned = set(_scanned_files())
        for rel in (_PALETTE_FILE, "theme.py", "tools/demeter.py",
                    "panels/pythia_face.py", "tests/test_palette_guard.py"):
            with self.subTest(file=rel):
                self.assertTrue(rel in scanned, f"{rel} is not scanned")

    def test_every_tracked_py_is_scanned(self):
        tracked = _tracked_py()
        if tracked is None:
            self.skipTest("git unavailable — cannot list tracked files")
        scanned = set(_scanned_files())
        outside = [rel for rel in tracked if rel not in scanned]
        self.assertFalse(outside, f"tracked .py files the guard never reads — "
                         f"add their folder to _SCANNED_DIRS: {outside}")

    def test_hex_fires_on_the_palette_itself(self):
        tree, _ = _parse(_PALETTE_FILE)
        self.assertGreaterEqual(len(_hex_hits(tree)),
                                len(_palette_literal_keys()))

    def test_named_fires_in_a_slot_and_nowhere_else(self):
        tree = ast.parse('tk.Label(fg="white", bg=C["red"] if x else "gray")\n'
                         'w.pack(fill="x")\n'
                         'c.create_line(0, 0, 1, 1, fill=C["green"])\n'
                         'opts = {"bg": "black"}\n'
                         'status = "red"\n')
        found = sorted(d for _ln, d in _named_hits(tree, self._is_colour))
        self.assertEqual(found, ["colour name 'black' in {'bg': ...}",
                                 "colour name 'gray' in bg=",
                                 "colour name 'white' in fg="])

    def test_triple_fires_on_a_colour_and_not_on_alternatives(self):
        src = ("RGB_VALLEY = (0x0B, 0x2E, 0x18)\n"
               "WHITE = [255, 255, 255]\n"
               "if m in (3, 4, 5): pass\n"
               "OFFSET = (0, -1, 2)\n")
        found = [d for _ln, d in _triple_hits(ast.parse(src), src)]
        self.assertEqual(found, ["(0x0B, 0x2E, 0x18)", "[255, 255, 255]"])


if __name__ == "__main__":
    unittest.main()
