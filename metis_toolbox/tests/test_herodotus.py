"""
tests/test_herodotus.py — the archive law, the edit engine, the degraded paths
===============================================================================
Run from metis_toolbox/:  python -X utf8 -m unittest tests.test_herodotus

Every test patches herodotus._ARCHIVE_ROOT to a fresh temp directory — the
module reads it at call time for exactly this reason. The real archive is
never touched.
"""

import os
import shutil
import tempfile
import unittest

from tools import herodotus


class HerodotusBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="herodotus_test_")
        self._saved_root = herodotus._ARCHIVE_ROOT
        herodotus._ARCHIVE_ROOT = self.tmp

    def tearDown(self) -> None:
        herodotus._ARCHIVE_ROOT = self._saved_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    # helper — write a raw file straight into the archive, bypassing the tool
    def raw(self, name: str, data: bytes) -> str:
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path


class TestFilenameLaw(HerodotusBase):
    """Rails 1 + 2: hardcoded root, bare .md names, no traversal, no devices."""

    BAD = [
        "", "   ", "notes.txt", "notes", ".hidden.md", "-flag.md",
        "../escape.md", "..\\escape.md", "sub/notes.md", "sub\\notes.md",
        "C:\\evil.md", "/etc/passwd.md", "a" * 101 + ".md",
        "CON.md", "con.md", "NUL.md", "COM1.md", "lpt9.md", "con.notes.md",
    ]

    def test_bad_names_refused_everywhere(self) -> None:
        for name in self.BAD:
            for op in (
                lambda n: herodotus.read_document(n),
                lambda n: herodotus.write_document(n, "body"),
                lambda n: herodotus.edit_document(n, "append", content="x"),
            ):
                result = op(name)
                self.assertIn("error", result, msg=f"{name!r} was accepted")
                self.assertTrue(
                    result["error"].startswith("invalid_filename"),
                    msg=f"{name!r} -> {result['error']}",
                )

    def test_nothing_escapes_the_root(self) -> None:
        # Even if a hostile name slipped the regex, resolve-then-contain holds.
        outside = os.path.join(os.path.dirname(self.tmp), "escaped.md")
        herodotus.write_document("../escaped.md", "gotcha")
        self.assertFalse(os.path.exists(outside))

    def test_good_names_accepted(self) -> None:
        # a stray trailing space from a sloppy model is normalized, not refused
        for name in ("notes.md", "My Notes 2.md", "a-b_c.d.md", "Notes.MD", "notes.md "):
            self.assertNotIn("error", herodotus.write_document(name, "body"),
                             msg=name)


class TestWriteAndFrontMatter(HerodotusBase):
    def test_new_document_gets_front_matter(self) -> None:
        result = herodotus.write_document("topic.md", "Body text.", source="https://x.example")
        self.assertEqual(result.get("overwrote"), False)
        content = herodotus.read_document("topic.md")["content"]
        self.assertTrue(content.startswith("---\ntitle: topic\n"))
        self.assertIn("source: https://x.example\n", content)
        self.assertIn("created: ", content)
        self.assertIn("Body text.", content)

    def test_existing_front_matter_not_doubled(self) -> None:
        herodotus.write_document("has_fm.md", "---\ntitle: mine\n---\nBody.")
        content = herodotus.read_document("has_fm.md")["content"]
        self.assertEqual(content.count("---"), 2)
        self.assertIn("title: mine", content)

    def test_overwrite_flagged(self) -> None:
        herodotus.write_document("twice.md", "one")
        result = herodotus.write_document("twice.md", "two")
        self.assertEqual(result.get("overwrote"), True)
        self.assertIn("two", herodotus.read_document("twice.md")["content"])

    def test_empty_content_refused(self) -> None:
        self.assertEqual(herodotus.write_document("empty.md", "   ")["error"],
                         "missing_content")

    def test_size_cap_on_write(self) -> None:
        big = "x" * (herodotus._MAX_DOC_BYTES + 1)
        self.assertTrue(
            herodotus.write_document("big.md", big)["error"].startswith("too_large"))

    def test_no_tmp_droppings(self) -> None:
        herodotus.write_document("clean.md", "body")
        leftovers = [f for f in os.listdir(self.tmp) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class TestReadLaw(HerodotusBase):
    """Rails 3 + 4: UTF-8 strict, size cap; plus not_found."""

    def test_not_found(self) -> None:
        self.assertEqual(herodotus.read_document("ghost.md")["error"], "not_found")

    def test_non_utf8_refused(self) -> None:
        self.raw("binary.md", b"\xff\xfe\x00 not text \x80")
        self.assertTrue(
            herodotus.read_document("binary.md")["error"].startswith("not_utf8"))

    def test_oversize_refused(self) -> None:
        self.raw("huge.md", b"x" * (herodotus._MAX_DOC_BYTES + 1))
        self.assertTrue(
            herodotus.read_document("huge.md")["error"].startswith("too_large"))

    def test_roundtrip_utf8(self) -> None:
        herodotus.write_document("greek.md", "Ἡροδότου Ἁλικαρνησσέος ἱστορίης ἀπόδεξις")
        self.assertIn("Ἁλικαρνησσέος", herodotus.read_document("greek.md")["content"])


class TestListAndSearch(HerodotusBase):
    def test_missing_archive_is_empty_not_error(self) -> None:
        herodotus._ARCHIVE_ROOT = os.path.join(self.tmp, "never_created")
        self.assertEqual(herodotus.list_documents(), {"documents": []})
        self.assertEqual(herodotus.search_documents("x")["error"], "no_matches")

    def test_list_only_md_sorted(self) -> None:
        herodotus.write_document("b.md", "two")
        herodotus.write_document("A.md", "one")
        self.raw("ignore.txt", b"nope")
        docs = herodotus.list_documents()["documents"]
        self.assertEqual([d["filename"] for d in docs], ["A.md", "b.md"])
        self.assertIn("size", docs[0])
        self.assertIn("modified", docs[0])

    def test_search_hit_and_miss(self) -> None:
        herodotus.write_document("egypt.md", "The Nile floods in summer.\nGift of the river.")
        hit = herodotus.search_documents("NILE")
        self.assertEqual(hit["matches"][0]["filename"], "egypt.md")
        self.assertEqual(hit["matches"][0]["matches"], 1)
        self.assertIn("Nile", hit["matches"][0]["snippets"][0])
        self.assertEqual(herodotus.search_documents("babylon")["error"], "no_matches")
        self.assertEqual(herodotus.search_documents("  ")["error"], "missing_query")

    def test_search_skips_unreadable_file(self) -> None:
        herodotus.write_document("good.md", "findable text")
        self.raw("bad.md", b"\xff\x80 binary")
        result = herodotus.search_documents("findable")
        self.assertEqual(len(result["matches"]), 1)


class TestEditEngine(HerodotusBase):
    DOC = "# Alpha\n\nfirst body\n\n## Beta\n\nsecond body\n"

    def setUp(self) -> None:
        super().setUp()
        # Raw write: no front matter, so line positions are predictable.
        self.raw("doc.md", self.DOC.encode("utf-8"))

    def text(self) -> str:
        return herodotus.read_document("doc.md")["content"]

    def test_append_and_prepend(self) -> None:
        herodotus.edit_document("doc.md", "append", content="tail")
        self.assertTrue(self.text().endswith("tail\n"))
        herodotus.edit_document("doc.md", "prepend", content="head")
        self.assertTrue(self.text().startswith("head\n# Alpha"))

    def test_prepend_respects_front_matter(self) -> None:
        herodotus.write_document("fm.md", "body line")
        herodotus.edit_document("fm.md", "prepend", content="INTRO")
        content = herodotus.read_document("fm.md")["content"]
        self.assertTrue(content.startswith("---"))          # fence still first
        closing_fence = content.index("---", 3)             # second fence
        self.assertLess(closing_fence, content.index("INTRO"))
        self.assertLess(content.index("INTRO"), content.index("body line"))

    def test_replace_exactly_once(self) -> None:
        # errors first, on the pristine document ("body" appears twice)
        self.assertEqual(
            herodotus.edit_document("doc.md", "replace", target="nowhere",
                                    content="x")["error"].split(":")[0],
            "target_not_found")
        self.assertEqual(
            herodotus.edit_document("doc.md", "replace", target="body",
                                    content="x")["error"].split(":")[0],
            "target_ambiguous")
        self.assertEqual(
            herodotus.edit_document("doc.md", "replace", content="x")["error"].split(":")[0],
            "missing_target")
        herodotus.edit_document("doc.md", "replace", target="first body", content="FIRST")
        self.assertIn("FIRST", self.text())

    def test_replace_with_empty_deletes(self) -> None:
        herodotus.edit_document("doc.md", "replace", target="second body", content="")
        self.assertNotIn("second body", self.text())

    def test_heading_inserts(self) -> None:
        herodotus.edit_document("doc.md", "insert_after_heading",
                                target="Beta", content="AFTER")
        self.assertIn("## Beta\nAFTER", self.text())
        herodotus.edit_document("doc.md", "insert_before_heading",
                                target="## Beta", content="BEFORE")
        self.assertIn("BEFORE\n## Beta", self.text())

    def test_replace_heading_keeps_level(self) -> None:
        herodotus.edit_document("doc.md", "replace_heading",
                                target="beta", content="Gamma")
        self.assertIn("## Gamma", self.text())
        self.assertNotIn("Beta", self.text())

    def test_heading_errors(self) -> None:
        self.assertEqual(
            herodotus.edit_document("doc.md", "insert_after_heading",
                                    target="Omega", content="x")["error"].split(":")[0],
            "heading_not_found")
        herodotus.edit_document("doc.md", "append", content="\n## Beta\n")
        self.assertEqual(
            herodotus.edit_document("doc.md", "insert_after_heading",
                                    target="Beta", content="x")["error"].split(":")[0],
            "heading_ambiguous")

    def test_bad_operation_and_missing_content(self) -> None:
        self.assertEqual(
            herodotus.edit_document("doc.md", "delete")["error"].split(":")[0],
            "missing_content")   # content check fires first, by design
        self.assertEqual(
            herodotus.edit_document("doc.md", "delete", content="x")["error"].split(":")[0],
            "bad_operation")
        self.assertEqual(herodotus.edit_document("ghost.md", "append",
                                                 content="x")["error"], "not_found")

    def test_edit_bumps_updated(self) -> None:
        herodotus.write_document("fm.md", "body")
        before = herodotus.read_document("fm.md")["content"]
        old_line = next(l for l in before.split("\n") if l.startswith("updated:"))
        new_stamp = "updated: 1999-12-31T23:59:59"
        # Force a visibly stale stamp, then edit and confirm it changed.
        forced = before.replace(old_line, new_stamp, 1)
        self.raw("fm.md", forced.encode("utf-8"))
        herodotus.edit_document("fm.md", "append", content="more")
        after = herodotus.read_document("fm.md")["content"]
        self.assertNotIn(new_stamp, after)
        self.assertIn("updated: ", after)


class TestToolExport(HerodotusBase):
    """The plural-export contract pythia.py relies on (CONVENTIONS §2/§3)."""

    def test_definitions_bind_to_like_named_functions(self) -> None:
        self.assertEqual(len(herodotus.TOOL_DEFINITIONS), 5)
        for d in herodotus.TOOL_DEFINITIONS:
            name = d["function"]["name"]
            self.assertTrue(callable(getattr(herodotus, name)), msg=name)

    def test_no_handler_ever_raises(self) -> None:
        # Garbage in, error dict out — never an exception (§2: handle() never raises).
        cases = [
            lambda: herodotus.read_document("\x00?.md"),
            lambda: herodotus.write_document("x" * 500, "y"),
            lambda: herodotus.edit_document("a.md", "explode", target=None, content=None),  # type: ignore[arg-type]
            lambda: herodotus.search_documents(""),
        ]
        for fn in cases:
            result = fn()
            self.assertIsInstance(result, dict)
            self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
