"""
test_virgil.py — Virgil's words: module glosses through Daedalus, and the
glossary.

What earns a test here:

  * A MODULE TIP IS THE MODULE'S OWN GLOSS — recomputed from daedalus.index()
    for every module, never hardcoded, so a docstring edit can't leave a stale
    copy anywhere.
  * THE SHIPPED GLOSSARY KEEPS ITS RULES — no duplicate keys (plain json.load
    would silently keep the last), snake_case keys, a label, a text that ends
    a sentence within 240 characters, and no key that names a module.
  * A MISS IS NONE AND ONE WARNING — never an exception, never fifty lines.
  * AN EDIT SHOWS UP — the glossary is re-read when the file moves, and only
    then.
  * NOTHING IS IMPORTED TO BE READ — virgil.py never touches importlib,
    inspect or __doc__.

Run from the package root:
    python -X utf8 -m unittest tests.test_virgil
"""

import ast
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import daedalus, virgil

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SHIPPED = os.path.join(_APP_ROOT, "config", "virgil_glossary.json")
_SOURCE = os.path.join(_APP_ROOT, "tools", "virgil.py")


def _reset():
    """Forget the cached glossary and which keys have already warned."""
    virgil._cache = virgil._Cache()
    virgil._warned.clear()


class _VirgilCase(unittest.TestCase):
    def setUp(self):
        _reset()
        self.addCleanup(_reset)


def _strict_pairs(pairs):
    """json object hook that refuses a repeated key at any depth."""
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _shipped_terms():
    with open(_SHIPPED, encoding="utf-8") as fh:
        return json.load(fh, object_pairs_hook=_strict_pairs)["terms"]


# ── Module tips ───────────────────────────────────────────────────────────────

class TestModuleTips(_VirgilCase):
    def test_a_module_tip_is_the_modules_own_gloss(self):
        nodes = daedalus.index()["nodes"]
        shown = 0
        for key, node in nodes.items():
            if not node["gloss"]:
                continue
            with self.subTest(module=key):
                words = virgil.text_for(virgil.module(key))
                self.assertIsNotNone(words)
                self.assertEqual(words.body, node["gloss"])
                self.assertTrue(words.verbatim)
                shown += 1
        self.assertTrue(shown, "no module had a gloss to show")

    def test_the_title_is_the_key_spaced_and_capitalized(self):
        self.assertEqual(virgil.text_for(virgil.module("machine_spirit")).title,
                         "Machine Spirit")
        self.assertEqual(virgil.text_for(virgil.module("euterpe")).title,
                         "Euterpe")

    def test_an_unknown_module_is_none_and_warns_once(self):
        with self.assertLogs("METIS.virgil", "WARNING") as logged:
            self.assertIsNone(virgil.text_for(virgil.module("no_such_module")))
            self.assertIsNone(virgil.text_for(virgil.module("no_such_module")))
        self.assertEqual(len(logged.records), 1, logged.output)

    def test_a_daedalus_failure_is_none_not_an_exception(self):
        with mock.patch.object(daedalus, "gloss", side_effect=RuntimeError("x")):
            with self.assertLogs("METIS.virgil", "WARNING"):
                self.assertIsNone(virgil.text_for(virgil.module("aura")))


# ── The shipped glossary ──────────────────────────────────────────────────────

class TestShippedGlossary(_VirgilCase):
    def test_no_key_is_repeated(self):
        try:
            _shipped_terms()
        except ValueError as exc:
            self.fail(f"config/virgil_glossary.json: {exc}")

    def test_entries_keep_the_rules(self):
        for key, entry in _shipped_terms().items():
            with self.subTest(key=key):
                self.assertRegex(key, virgil.KEY_SHAPE, "keys are lowercase snake_case")
                self.assertIsInstance(entry, dict)
                self.assertEqual(set(entry), {"label", "text"})
                self.assertIsInstance(entry["label"], str)
                self.assertTrue(entry["label"].strip(), "the label is empty")
                text = entry["text"]
                self.assertIsInstance(text, str)
                self.assertTrue(text.rstrip().endswith((".", "!", "?")),
                                f"the text must end a sentence: {text!r}")
                self.assertLessEqual(len(text), virgil.MAX_TEXT,
                                     f"{len(text)} characters")

    def test_no_key_names_a_module(self):
        """A module is described by its docstring only. A glossary entry with
        its key would be a second copy, and copies drift."""
        modules = set(daedalus.index()["nodes"])
        clash = sorted(set(_shipped_terms()) & modules)
        self.assertFalse(clash, f"glossary keys that are module keys: {clash}")

    def test_the_loader_keeps_every_shipped_entry(self):
        terms = _shipped_terms()
        self.assertEqual(list(virgil.glossary()), list(terms))   # file order
        for key, entry in terms.items():
            with self.subTest(key=key):
                self.assertEqual(virgil.text_for(virgil.term(key)),
                                 virgil.Words(entry["label"], entry["text"], False))

    def test_glossary_hands_out_a_copy(self):
        first = virgil.glossary()
        first["cpu"]["text"] = "changed"
        first.clear()
        self.assertNotEqual(virgil.glossary()["cpu"]["text"], "changed")


# ── Term tips against a glossary of the test's own ────────────────────────────

class TestGlossaryFile(_VirgilCase):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp(prefix="virgil_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "virgil_glossary.json")
        patch = mock.patch.object(virgil, "_GLOSSARY_PATH", self.path)
        patch.start()
        self.addCleanup(patch.stop)

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def write_terms(self, terms):
        self.write(json.dumps({"_comment": "test", "terms": terms}))

    def test_an_unknown_term_is_none_and_warns_once(self):
        self.write_terms({"cpu": {"label": "CPU", "text": "The processor."}})
        with self.assertLogs("METIS.virgil", "WARNING") as logged:
            self.assertIsNone(virgil.text_for(virgil.term("gpu")))
            self.assertIsNone(virgil.text_for(virgil.term("gpu")))
        self.assertEqual(len(logged.records), 1, logged.output)

    def test_a_missing_file_is_none_and_warns(self):
        with self.assertLogs("METIS.virgil", "WARNING") as logged:
            self.assertIsNone(virgil.text_for(virgil.term("cpu")))
        self.assertTrue(any("no glossary" in line for line in logged.output))
        self.assertEqual(virgil.glossary(), {})

    def test_a_malformed_file_is_none_and_warns(self):
        for bad in ('{"terms": {"cpu": ', '[]', '{"terms": []}', '{"other": {}}'):
            with self.subTest(file=bad):
                _reset()
                self.write(bad)
                with self.assertLogs("METIS.virgil", "WARNING") as logged:
                    self.assertIsNone(virgil.text_for(virgil.term("cpu")))
                self.assertTrue(any("could not read the glossary" in line
                                    for line in logged.output), logged.output)

    def test_a_broken_entry_is_skipped_and_the_rest_kept(self):
        self.write_terms({"cpu": {"label": "CPU"},
                          "ram": {"label": "RAM", "text": "Memory."},
                          "disk": "not an object"})
        with self.assertLogs("METIS.virgil", "WARNING") as logged:
            self.assertEqual(virgil.text_for(virgil.term("ram")).body, "Memory.")
        self.assertEqual(sum("skipping glossary entry" in line
                             for line in logged.output), 2)
        self.assertEqual(list(virgil.glossary()), ["ram"])

    def test_a_repeated_key_keeps_the_first_and_warns(self):
        self.write('{"terms": {"cpu": {"label": "CPU", "text": "First."},'
                   ' "cpu": {"label": "CPU", "text": "Second."}}}')
        with self.assertLogs("METIS.virgil", "WARNING") as logged:
            self.assertEqual(virgil.text_for(virgil.term("cpu")).body, "First.")
        self.assertTrue(any("repeats the key 'cpu'" in line for line in logged.output))

    def test_an_edit_is_read_on_the_next_hover(self):
        self.write_terms({"cpu": {"label": "CPU", "text": "Old words."}})
        self.assertEqual(virgil.text_for(virgil.term("cpu")).body, "Old words.")
        stamp = os.stat(self.path).st_mtime_ns
        self.write_terms({"cpu": {"label": "CPU", "text": "New words."}})  # same size
        os.utime(self.path, ns=(stamp + 10**9, stamp + 10**9))
        self.assertEqual(virgil.text_for(virgil.term("cpu")).body, "New words.")

    def test_an_unchanged_file_is_read_once(self):
        self.write_terms({"cpu": {"label": "CPU", "text": "The processor."}})
        with mock.patch.object(virgil, "_load", wraps=virgil._load) as load:
            for _ in range(5):
                virgil.text_for(virgil.term("cpu"))
        self.assertEqual(load.call_count, 1)

    def test_a_missing_file_warns_once_not_per_hover(self):
        with self.assertLogs("METIS.virgil", "WARNING") as logged:
            for key in ("cpu", "cpu", "ram", "ram"):
                virgil.text_for(virgil.term(key))
        file_lines = [line for line in logged.output if "no glossary at" in line]
        self.assertEqual(len(file_lines), 1, logged.output)


# ── Never raises ──────────────────────────────────────────────────────────────

class TestNeverRaises(_VirgilCase):
    def test_garbage_tips_are_none(self):
        with self.assertLogs("METIS.virgil", "WARNING"):
            for tip in (virgil.Tip("widget", "cpu"), None, "cpu",
                        virgil.Tip("term", None), virgil.Tip("module", 7)):
                with self.subTest(tip=tip):
                    self.assertIsNone(virgil.text_for(tip))


# ── Never imports ─────────────────────────────────────────────────────────────

class TestNeverImports(unittest.TestCase):
    """Daedalus reads source with ast precisely so nothing is ever imported to
    be described. Virgil must not undo that with a shortcut."""

    def test_no_importlib_inspect_or_dunder_doc(self):
        with open(_SOURCE, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found += [a.name for a in node.names
                          if a.name.split(".")[0] in ("importlib", "inspect")]
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] in ("importlib", "inspect"):
                    found.append(node.module)
            elif isinstance(node, ast.Attribute) and node.attr == "__doc__":
                found.append(".__doc__")
            elif isinstance(node, ast.Name) and node.id in ("__doc__", "__import__"):
                found.append(node.id)
            elif isinstance(node, ast.Constant) and node.value in ("__doc__",
                                                                   "importlib"):
                found.append(repr(node.value))
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
