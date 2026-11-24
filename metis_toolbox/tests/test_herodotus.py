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
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_the_four_case_folding_letters_are_refused(self) -> None:
        # Under IGNORECASE alone, [A-Za-z] also matched U+0130, U+0131, U+017F
        # and U+212A (they case-fold to ASCII letters). re.ASCII keeps the
        # allowlist to the 52 letters it names, first character and after.
        for code in (0x130, 0x131, 0x17F, 0x212A):
            for name in (f"{chr(code)}notes.md", f"no{chr(code)}tes.md"):
                result = herodotus.write_document(name, "body")
                self.assertTrue(
                    result.get("error", "").startswith("invalid_filename"),
                    msg=f"U+{code:04X} in {name!r} -> {result}",
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
                         "bad_arguments: content is empty")

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


class TestReadBudget(HerodotusBase):
    """The model-facing read budget, separate from the 1 MB storage cap. A
    whole 1 MB note is ~250k tokens against Pythia's context window; one read
    now returns at most _READ_CHARS, and says where to continue (fetch_page's
    `truncated` pattern)."""

    def test_a_short_document_comes_back_whole(self) -> None:
        herodotus.write_document("short.md", "Just a line.")
        out = herodotus.read_document("short.md")
        self.assertIn("Just a line.", out["content"])
        self.assertEqual(out["offset"], 0)
        self.assertFalse(out["truncated"])
        self.assertNotIn("next_offset", out)

    def test_a_long_document_comes_back_in_parts_that_rejoin(self) -> None:
        budget = herodotus._READ_CHARS
        body = "".join(chr(ord("a") + i % 26) for i in range(budget * 2 + budget // 2))
        self.raw("long.md", body.encode("utf-8"))
        parts, offset = [], 0
        while True:
            out = herodotus.read_document("long.md", offset=offset)
            self.assertNotIn("error", out)
            self.assertLessEqual(len(out["content"]), budget)
            self.assertEqual(out["size"], len(body))    # the WHOLE file, in bytes
            parts.append(out["content"])
            if not out["truncated"]:
                self.assertNotIn("next_offset", out)
                break
            offset = out["next_offset"]
        self.assertEqual(len(parts), 3)
        self.assertEqual("".join(parts), body)

    def test_the_offset_counts_characters_not_bytes(self) -> None:
        body = "Ἡ" * (herodotus._READ_CHARS + 10)        # 2 bytes each
        self.raw("greek_long.md", body.encode("utf-8"))
        first = herodotus.read_document("greek_long.md")
        self.assertEqual(first["next_offset"], herodotus._READ_CHARS)
        rest = herodotus.read_document("greek_long.md", offset=first["next_offset"])
        self.assertEqual(rest["content"], "Ἡ" * 10)

    def test_an_offset_past_the_end_is_a_slug(self) -> None:
        herodotus.write_document("short.md", "Just a line.")
        out = herodotus.read_document("short.md", offset=10_000)
        self.assertTrue(out["error"].startswith("bad_arguments: "), out)

    def test_a_negative_offset_reads_from_the_start(self) -> None:
        herodotus.write_document("short.md", "Just a line.")
        self.assertEqual(herodotus.read_document("short.md", offset=-5)["offset"], 0)

    def test_a_non_numeric_offset_is_a_slug_not_a_raise(self) -> None:
        herodotus.write_document("short.md", "Just a line.")
        out = herodotus.read_document("short.md", offset="later")  # type: ignore[arg-type]
        self.assertTrue(out["error"].startswith("bad_arguments: "), out)

    def test_the_budget_is_not_the_storage_cap(self) -> None:
        self.assertLess(herodotus._READ_CHARS, herodotus._MAX_DOC_BYTES)


class TestListAndSearch(HerodotusBase):
    def test_missing_archive_is_empty_not_error(self) -> None:
        herodotus._ARCHIVE_ROOT = os.path.join(self.tmp, "never_created")
        self.assertEqual(herodotus.list_documents(), {"documents": []})
        self.assertEqual(herodotus.search_documents("x")["error"], "no_results")

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
        self.assertEqual(herodotus.search_documents("babylon")["error"], "no_results")
        self.assertEqual(herodotus.search_documents("  ")["error"],
                         "bad_arguments: search needs a query")

    def test_search_skips_unreadable_file(self) -> None:
        herodotus.write_document("good.md", "findable text")
        self.raw("bad.md", b"\xff\x80 binary")
        with self.assertLogs("METIS.herodotus", "WARNING"):
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
            "not_found")
        self.assertEqual(
            herodotus.edit_document("doc.md", "replace", target="body",
                                    content="x")["error"].split(":")[0],
            "target_ambiguous")
        self.assertEqual(
            herodotus.edit_document("doc.md", "replace", content="x")["error"].split(":")[0],
            "bad_arguments")
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
            "not_found")
        herodotus.edit_document("doc.md", "append", content="\n## Beta\n")
        self.assertEqual(
            herodotus.edit_document("doc.md", "insert_after_heading",
                                    target="Beta", content="x")["error"].split(":")[0],
            "heading_ambiguous")

    def test_bad_operation_and_missing_content(self) -> None:
        self.assertEqual(
            herodotus.edit_document("doc.md", "delete")["error"].split(":")[0],
            "bad_arguments")   # content check fires first, by design
        self.assertEqual(
            herodotus.edit_document("doc.md", "delete", content="x")["error"].split(":")[0],
            "unknown_action")
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


class TestIOErrorsAreCleanSlugs(HerodotusBase):
    """§2: an OSError's message carries the absolute archive path, and the error
    dict feeds straight into the LLM context — so only the class name may
    travel, never the message. (The session-2 guard amendment caught these.)"""

    def test_read_failure_reports_class_not_path(self) -> None:
        herodotus.write_document("real.md", "body")
        leaky = "[Errno 13] Permission denied: '/home/u/herodotus_archive/real.md'"
        with mock.patch("pathlib.Path.read_text",
                        side_effect=PermissionError(leaky)):
            result = herodotus.read_document("real.md")
        self.assertEqual(result["error"], "read_failed: PermissionError")
        self.assertNotIn("herodotus_archive", result["error"])  # path gone
        self.assertNotIn("Permission denied", result["error"])  # message gone

    def test_list_failure_reports_class_not_path(self) -> None:
        leaky = "[Errno 13] Permission denied: '/home/u/herodotus_archive'"
        with mock.patch("pathlib.Path.iterdir",
                        side_effect=PermissionError(leaky)), \
                self.assertLogs("METIS.herodotus", "WARNING"):
            result = herodotus.list_documents()
        self.assertEqual(result["error"], "read_failed: PermissionError")
        self.assertNotIn("herodotus_archive", result["error"])

    def test_search_survives_a_dir_walk_error(self) -> None:
        # Before the audit search_documents did not guard its iterdir: a
        # permission error or a mid-scan delete RAISED, breaking the "never
        # raises" contract list_documents already honored. Now it degrades.
        with mock.patch("pathlib.Path.iterdir",
                        side_effect=PermissionError("denied")), \
                self.assertLogs("METIS.herodotus", "WARNING"):
            result = herodotus.search_documents("anything")
        self.assertEqual(result["error"], "read_failed: PermissionError")


class TestToolExport(HerodotusBase):
    """The plural-export contract pythia.py relies on (CONVENTIONS §2/§3)."""

    def test_definitions_bind_to_like_named_functions(self) -> None:
        # Three since 2026-11-14: one read tool and the two writers.
        names = [d["function"]["name"] for d in herodotus.TOOL_DEFINITIONS]
        self.assertEqual(names, ["read_archive", "write_document", "edit_document"])
        for name in names:
            self.assertTrue(callable(getattr(herodotus, name)), msg=name)


class TestReadArchive(HerodotusBase):
    """read_archive picks one of the three reads by `action` and hands back
    that read's own answer, slugs included."""

    def test_each_action_routes_to_its_read(self) -> None:
        cases = (("list", "list_documents", {}),
                 ("search", "search_documents", {"query": "lich"}),
                 ("read", "read_document", {"filename": "n.md", "offset": 6}))
        for action, fn, kwargs in cases:
            with self.subTest(action=action), \
                 mock.patch.object(herodotus, fn,
                                   return_value={"via": fn}) as called:
                self.assertEqual(herodotus.read_archive(action, **kwargs),
                                 {"via": fn})
                called.assert_called_once_with(*kwargs.values())

    def test_real_reads_end_to_end(self) -> None:
        herodotus.write_document("lich.md", "A lich keeps a phylactery.")
        self.assertEqual(
            [d["filename"] for d in herodotus.read_archive("list")["documents"]],
            ["lich.md"])
        hits = herodotus.read_archive("search", query="phylactery")["matches"]
        self.assertEqual(hits[0]["filename"], "lich.md")
        self.assertIn("phylactery",
                      herodotus.read_archive("read", filename="lich.md")["content"])

    def test_missing_arguments_keep_the_reads_own_slugs(self) -> None:
        self.assertEqual(herodotus.read_archive("search"),
                         {"error": "bad_arguments: search needs a query"})
        self.assertTrue(herodotus.read_archive("read")["error"]
                        .startswith("invalid_filename"))

    def test_an_action_outside_the_enum_is_unknown_action(self) -> None:
        out = herodotus.read_archive("delete", filename="lich.md")
        self.assertTrue(out["error"].startswith("unknown_action: "))
        for action in ("list", "search", "read"):
            self.assertIn(action, out["error"])

    def test_paging_steering_survives_the_merge(self) -> None:
        # read_document's T8 steering, word for word, now on read_archive.
        desc = herodotus.READ_ARCHIVE_TOOL_DEFINITION["function"]["description"]
        self.assertIn("A long document comes back in parts: if 'truncated' is "
                      "true, call again with offset set to 'next_offset'. To "
                      "change part of a long document, use edit_document "
                      "rather than rewriting it.", desc)

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



# ─────────────────────────────────────────────────────────────────────────────
#  The V0.03 walkthrough (S32, 2026-11-23)
# ─────────────────────────────────────────────────────────────────────────────

class TestOneOddDocumentNeverHidesTheRest(HerodotusBase):
    """One document whose time Windows can't convert (before 1970 or past
    year 9999) failed the whole listing as read_failed (measured)."""

    def test_a_time_the_platform_cant_convert_lists_as_blank(self) -> None:
        herodotus.write_document("good.md", "fine")
        herodotus.write_document("future.md", "from the far future")
        try:
            os.utime(os.path.join(self.tmp, "future.md"), (3e11, 3e11))   # year ~11476
        except OSError:
            self.skipTest("this filesystem won't hold a year-11476 time")
        try:
            out = herodotus.list_documents()
        except Exception as exc:
            self.fail(f"list_documents raised {type(exc).__name__}")
        self.assertEqual({d["filename"]: d["modified"] for d in out.get("documents", [])
                          if d["filename"] == "future.md"}, {"future.md": ""})
        self.assertIn("good.md", [d["filename"] for d in out["documents"]])

    def test_a_document_it_cant_stat_is_skipped(self) -> None:
        # The stat list_documents itself makes fails for one file (a file
        # removed mid-scan, say); the others still list.
        herodotus.write_document("good.md", "fine")
        herodotus.write_document("gone.md", "vanishing")
        real_stat = Path.stat

        def stat(path: Path, *args, **kwargs):
            if path.name == "gone.md" and sys._getframe(1).f_code.co_name == "list_documents":
                raise FileNotFoundError("gone mid-scan")
            return real_stat(path, *args, **kwargs)

        with mock.patch.object(Path, "stat", stat), \
                self.assertLogs("METIS.herodotus", "WARNING"):
            out = herodotus.list_documents()
        self.assertEqual([d["filename"] for d in out.get("documents", [])], ["good.md"])


class TestASymlinkOutIsSkipped(HerodotusBase):
    """Rail 1 says every path resolves inside the archive. read and edit
    refused a symlink out; list showed it and search read the file at its far
    end (measured)."""

    def setUp(self) -> None:
        super().setUp()
        self.outside = tempfile.mkdtemp(prefix="herodotus_outside_")
        self.addCleanup(shutil.rmtree, self.outside, True)
        secret = os.path.join(self.outside, "secret.md")
        with open(secret, "w", encoding="utf-8") as fh:
            fh.write("outside the archive\n")
        try:
            os.symlink(secret, os.path.join(self.tmp, "link.md"))
        except (OSError, NotImplementedError):
            self.skipTest("symlinks need a privilege this account lacks")
        herodotus.write_document("inside.md", "inside the archive")

    def test_list_doesnt_show_it(self) -> None:
        self.assertEqual([d["filename"] for d in herodotus.list_documents()["documents"]],
                         ["inside.md"])

    def test_search_doesnt_read_through_it(self) -> None:
        self.assertEqual(herodotus.search_documents("outside the archive"),
                         {"error": "no_results"})


class TestNeverRaisesOnAnInfiniteOffset(HerodotusBase):
    def test_an_infinite_offset_is_bad_arguments(self) -> None:
        # Pythia's gate refuses one; a direct caller of the public function
        # got OverflowError (int(inf)).
        herodotus.write_document("a.md", "text")
        try:
            out = herodotus.read_document("a.md", float("inf"))
        except OverflowError:
            self.fail("read_document raised OverflowError")
        self.assertTrue(out.get("error", "").startswith("bad_arguments"), out)


class TestSourceStaysOnOneLine(HerodotusBase):
    def test_a_newline_in_source_cant_close_the_front_matter(self) -> None:
        # It did: "x\n---\ninjected: yes" ended the block after source, and
        # the rest of the block landed in the body (measured).
        herodotus.write_document("a.md", "# Body", source="https://x.example\n---\ninjected: yes")
        with open(os.path.join(self.tmp, "a.md"), encoding="utf-8") as fh:
            lines = fh.read().split("\n")
        close = lines.index("---", 1)
        block = lines[1:close]
        self.assertIn("source: https://x.example --- injected: yes", block)
        self.assertIn("tags:", block)


if __name__ == "__main__":
    unittest.main()
