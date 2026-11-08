"""
test_public_snapshot.py — what the public snapshot leaves out stays left out.

publish/private_paths.txt lists the files the public snapshot (Felsyn/felhaven)
doesn't ship (Clio). publish/public_tree.py takes them out of the snapshot's
tree along with every pointer to them. These tests run its own functions over this tree, so a pointer added
later fails here, in the private repo, rather than as a dead link or a red CI
run on the public one.

The public snapshot has no publish/ folder, so there every test here skips.
The whole public tree is checked by `publish-snapshot.sh --dry-run`, which
builds it and runs this suite inside it.

Run from the package root:
    python -X utf8 -m unittest tests.test_public_snapshot
"""

import ast
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO = os.path.dirname(_APP_ROOT)

# Named in full by history and reasoning, which stay public by design.
_HISTORY = frozenset({"CHANGELOG.md", "CONVENTIONS.md", "BACKLOG.md"})


def _tracked():
    out = subprocess.run(["git", "ls-files"], cwd=_REPO, capture_output=True,
                         text=True, check=True).stdout
    return [p for p in out.splitlines() if p]


def _text(rel):
    with open(os.path.join(_REPO, rel), "rb") as fh:
        return fh.read().decode("utf-8")


def _load_public_tree():
    path = os.path.join(_REPO, "publish", "public_tree.py")
    spec = importlib.util.spec_from_file_location("public_tree", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _PrivateRepoOnly(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tracked = _tracked()
        if "publish/publish-snapshot.sh" not in cls.tracked:
            raise unittest.SkipTest("no publish/ here: this is the public snapshot")
        cls.pt = _load_public_tree()
        cls.private = cls.pt.private_paths(_text(cls.pt.LIST))


class TheListIsHonest(_PrivateRepoOnly):

    def test_every_listed_path_is_tracked(self):
        missing = [p for p in self.private if p not in self.tracked]
        self.assertEqual(missing, [], "listed as private but not tracked: "
                         "fix or delete the line in publish/private_paths.txt")

    def test_no_path_is_listed_twice(self):
        self.assertEqual(len(self.private), len(set(self.private)))


class NothingPublicLeansOnAPrivateFile(_PrivateRepoOnly):
    """make_public() itself runs over a copy of this tree's Markdown, the list,
    the Custody manifest and the private files, so these tests see exactly
    what the publish would produce: no second copy of its rules to drift."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.public = Path(cls._tmp.name)
        wanted = [p for p in cls.tracked if p.endswith(".md")]
        wanted += [cls.pt.LIST, cls.pt.MANIFEST] + cls.private
        for rel in wanted:
            (cls.public / rel).parent.mkdir(parents=True, exist_ok=True)
            with open(os.path.join(_REPO, rel), "rb") as src:
                (cls.public / rel).write_bytes(src.read())
        cls.pt.make_public(cls.public)       # raises on a broken marker

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()
        super().tearDownClass()

    def _public(self, suffix):
        return [p for p in self.tracked if p.endswith(suffix)
                and p not in self.private and not p.startswith(self.pt.MAINTAINER_ONLY)]

    def test_no_public_module_imports_or_launches_a_private_one(self):
        """The public tree would fail to import, or launch nothing."""
        prefix = "metis_toolbox/"
        modules = {p[len(prefix):-3].replace("/", ".") for p in self.private
                   if p.startswith(prefix) and p.endswith(".py")}
        hits = []
        for rel in self._public(".py"):
            for node in ast.walk(ast.parse(_text(rel))):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    names = [node.value]
                hits += [f"{rel}: {n}" for n in names
                         if any(n == m or n.startswith(m + ".") for m in modules)]
        self.assertEqual(hits, [])

    def test_the_private_files_are_gone(self):
        self.assertEqual([p for p in self.private if (self.public / p).exists()], [])

    def test_public_markdown_names_no_private_file(self):
        """No public page links or names a file it won't have. Wrap a passage
        in private:begin and private:end lines (see publish/public_tree.py)
        to keep it in this repo only."""
        names = {p.rsplit("/", 1)[-1] for p in self.private}
        hits = []
        for rel in self._public(".md"):
            if rel in _HISTORY:
                continue
            text = (self.public / rel).read_bytes().decode("utf-8")
            hits += [f"{rel}: {n}" for n in sorted(names) if n in text]
        self.assertEqual(hits, [])

    def test_the_custody_list_has_no_private_row_and_still_parses(self):
        names = {p.rsplit("/", 1)[-1] for p in self.private}
        entries = json.loads((self.public / self.pt.MANIFEST).read_bytes())["entries"]
        self.assertEqual([e["file"] for e in entries if e["file"] in names], [])


class TheStripperRefusesHalfMeasures(_PrivateRepoOnly):

    B, E = "<!-- private:begin -->", "<!-- private:end -->"

    def test_a_block_goes_with_the_blank_line_it_would_double(self):
        text = f"a\n\n{self.B}\nsecret\n{self.E}\n\nb\n"
        self.assertEqual(self.pt.strip_blocks(text), "a\n\nb\n")

    def test_line_endings_and_indented_markers_survive(self):
        text = f"- a\r\n  {self.B}\r\n- secret\r\n  {self.E}\r\n\r\n# b\r\n"
        self.assertEqual(self.pt.strip_blocks(text), "- a\r\n\r\n# b\r\n")

    def test_a_broken_block_is_refused(self):
        for text in (f"{self.B}\nnever closed\n",
                     f"{self.E}\n",
                     f"{self.B}\n{self.B}\n{self.E}\n",
                     f"see {self.B} here\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.pt.strip_blocks(text)

    def test_the_index_loses_only_the_private_row(self):
        text = "| [Aura](aura.md) | x |\n| [Clio](clio.md) | y |\n"
        private = ["metis_toolbox/README_PANTHEON/clio.md"]
        self.assertEqual(self.pt.drop_index_rows(text, private),
                         "| [Aura](aura.md) | x |\n")

    def test_a_custody_row_that_breaks_the_json_is_refused(self):
        private = ["metis_toolbox/config/b.json"]
        last_row = '{"entries": [\n  {"file": "a.json"},\n  {"file": "b.json"}\n]}\n'
        spread = '{"entries": [\n  {"file": "a.json"},\n  {\n    "file":\n "b.json"}\n]}\n'
        for text in (last_row, spread):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.pt.drop_manifest_rows(text, private)

    def test_make_public_on_a_small_tree(self):
        files = {
            "publish/private_paths.txt": "# x\nmetis_toolbox/tools/secret.py\n"
                                         "metis_toolbox/README_PANTHEON/secret.md\n"
                                         "metis_toolbox/config/secret.json\n",
            "metis_toolbox/tools/secret.py": "",
            "metis_toolbox/README_PANTHEON/secret.md": "",
            "metis_toolbox/config/secret.json": "{}",
            "metis_toolbox/README_PANTHEON/README.md":
                "| [Aura](aura.md) | x |\n| [Secret](secret.md) | y |\n",
            "metis_toolbox/config/cerberus_manifest.json":
                '{"entries": [\n  {"file": "secret.json"},\n  {"file": "a.json"}\n]}\n',
            "README.md": f"a\r\n\r\n{self.B}\r\nsecret.py\r\n{self.E}\r\n\r\nb\r\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, body in files.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_bytes(body.encode("utf-8"))
            removed = self.pt.make_public(root)
            self.assertEqual(len(removed), 3)
            self.assertFalse((root / "metis_toolbox/tools/secret.py").exists())
            self.assertEqual((root / "README.md").read_bytes(), b"a\r\n\r\nb\r\n")
            self.assertEqual(
                (root / "metis_toolbox/README_PANTHEON/README.md").read_text(),
                "| [Aura](aura.md) | x |\n")
            self.assertNotIn("secret", (root / "metis_toolbox/config/"
                                        "cerberus_manifest.json").read_text())
            with self.assertRaises(FileNotFoundError):     # already gone
                self.pt.make_public(root)


if __name__ == "__main__":
    unittest.main()
