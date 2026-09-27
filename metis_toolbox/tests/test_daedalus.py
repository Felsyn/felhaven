"""
test_daedalus.py — the module index: nodes, edges, the cache, and the tool.

Every fixture test points daedalus._APP_ROOT at a fresh temp tree (the module
reads it at call time, and rebuilds when it changes), so the real tree is only
read by the class that says so. What earns a test here:

  * NOTHING IS EVER IMPORTED. boom.py writes a sentinel and raises the moment
    it runs; the index must build without either happening.
  * THE DOCSTRING IS VERBATIM — equal to ast.get_docstring(clean=True) for
    every fixture module, through both docstring() and handle().
  * THE CACHE PARSES ONLY WHAT MOVED — counted, not assumed.
  * THE ENUM IS THE TREE — on the real code, the schema's enum is exactly the
    index's keys, so a new module cannot be invisible to Pythia.

Run from the package root:
    python -X utf8 -m unittest tests.test_daedalus
"""

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import daedalus

_REAL_ROOT = daedalus._APP_ROOT


def _fixture_files(sentinel: str) -> dict:
    """relative path -> source, one file per case the index must handle."""
    return {
        "core_a.py": '''
            """
            core_a.py — the alpha
            =====================
            Job:         Hold the fixture together. Second sentence is not shown.
            Upstream:    nothing
            """
            import os
            import requests
            import core_b
            from tools import tool_a, tool_b
            import tools.tool_c
            from tools.tool_d import thing
            from panels.panel_a import Widget
            from panels import panel_b
            ''',
        # No docstring; imports core_a both lazily and at module level (module
        # level wins), tool_a only lazily, and `import tool_d` — a bare name,
        # which means a CORE module and must not land on tools/tool_d.py.
        "core_b.py": '''
            import core_a
            import tool_d

            def later():
                import core_a
                from tools import tool_a
            ''',
        "boom.py": f'''
            """If this runs, the index imported it."""
            import pathlib
            pathlib.Path({sentinel!r}).write_text("ran")
            raise SystemExit("daedalus executed boom.py")
            ''',
        "broken.py": '''
            """Never parses."""
            def (:
            ''',
        "notes.txt": "not python",
        "tools/__init__.py": "",
        "tools/tool_a.py": '''
            """
            Job:         Report the thing.
            """
            class Holder:
                import core_a
            ''',
        "tools/tool_b.py": '''
            """Tool B — a docstring with no Job field.

            Detail at the margin.
                indented detail survives cleaning
            """
            from . import tool_a
            ''',
        "tools/tool_c.py": '"""Job: Be imported as tools.tool_c."""\n',
        "tools/tool_d.py": '"""Job: Be imported with from tools.tool_d."""\n',
        "tools/sub/deep.py": '"""Too deep to be indexed."""\n',
        "panels/__init__.py": "",
        "panels/panel_a.py": '''
            """Job: Draw A."""
            class Widget:
                def __init__(self):
                    import core_a
                    self._build_tabs([
                        ("tool_a", "THE TOOL", self, C["teal"]),
                        ("nomatch", "NOBODY", self, C["red"]),
                        ("tool_c", "TOOL_C", self, C["red"]),
                    ], default="tool_a")
            ''',
        "panels/panel_b.py": '''
            """Job: Hold a sidebar."""
            class Sidebar:
                _ROWS = [
                    ("core_a", "Core Alpha", "sub"),
                    ("widget", "Widgetry", "sub"),
                    ("group", "Group", "sub"),
                ]
                _GROUPS = {"group": [("tool_d", "Delta Deck", "sub")]}
            ''',
        "panels/widget_panel.py": '"""Job: Be found through its view key."""\n',
        ".venv/lib/site.py": '"""Never walked."""\n',
    }


class _FixtureCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="daedalus_")
        self.sentinel = os.path.join(self.root, "SENTINEL")
        for rel, src in _fixture_files(self.sentinel).items():
            path = os.path.join(self.root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(textwrap.dedent(src).lstrip("\n"))
        daedalus._APP_ROOT = self.root
        self.addCleanup(self._restore)
        # broken.py warns on every fresh build; the one test that is ABOUT
        # that warning switches the logger back on.
        quiet = mock.patch.object(daedalus.log, "disabled", True)
        quiet.start()
        self.addCleanup(quiet.stop)

    def _restore(self):
        daedalus._APP_ROOT = _REAL_ROOT
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel, src):
        path = os.path.join(self.root, *rel.split("/"))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src)
        return path


class TestNodes(_FixtureCase):
    def test_scope_is_three_flat_folders_minus_package_markers(self):
        nodes = daedalus.index()["nodes"]
        self.assertEqual(
            set(nodes),
            {"core_a", "core_b", "boom", "broken", "tool_a", "tool_b", "tool_c",
             "tool_d", "panel_a", "panel_b", "widget_panel"})
        self.assertEqual(nodes["core_a"]["group"], "core")
        self.assertEqual(nodes["tool_a"]["group"], "tools")
        self.assertEqual(nodes["panel_a"]["group"], "panels")
        self.assertEqual(nodes["tool_a"]["path"], "tools/tool_a.py")

    def test_gloss_is_the_job_first_sentence_or_the_first_line(self):
        nodes = daedalus.index()["nodes"]
        self.assertEqual(nodes["core_a"]["gloss"], "Hold the fixture together.")
        self.assertEqual(nodes["tool_b"]["gloss"],
                         "Tool B — a docstring with no Job field.")
        self.assertIsNone(nodes["core_b"]["gloss"])

    def test_display_names_come_from_sidebar_and_tab_bars(self):
        nodes = daedalus.index()["nodes"]
        self.assertEqual(nodes["core_a"]["display"], ["Core Alpha"])
        self.assertEqual(nodes["tool_a"]["display"], ["The Tool"])      # title-cased
        self.assertEqual(nodes["tool_d"]["display"], ["Delta Deck"])    # a group child
        self.assertEqual(nodes["widget_panel"]["display"], ["Widgetry"])  # key + _panel
        self.assertEqual(nodes["tool_c"]["display"], [])   # a label that restates the key

    def test_a_file_that_will_not_parse_is_a_node_without_edges(self):
        daedalus.log.disabled = False
        with self.assertLogs("METIS.daedalus", "WARNING") as logged:
            snap = daedalus.index()
        self.assertTrue(any("broken.py does not parse" in line
                            for line in logged.output))
        broken = snap["nodes"]["broken"]
        self.assertEqual(broken["parse_error"], "syntax_error")
        self.assertEqual(broken["error_line"], 2)
        self.assertFalse(broken["has_docstring"])
        self.assertFalse([e for e in snap["edges"] if "broken" in (e["source"], e["target"])])
        self.assertEqual(daedalus.docstring("broken")["error"],
                         "parse_failed: broken.py line 2")


class TestEdges(_FixtureCase):
    def test_every_import_form_resolves_and_nothing_else_does(self):
        edges = {(e["source"], e["target"]): e["lazy"]
                 for e in daedalus.index()["edges"]}
        self.assertEqual(edges, {
            ("core_a", "core_b"): False,      # import x
            ("core_a", "tool_a"): False,      # from tools import x, y
            ("core_a", "tool_b"): False,
            ("core_a", "tool_c"): False,      # import tools.x
            ("core_a", "tool_d"): False,      # from tools.x import Y
            ("core_a", "panel_a"): False,     # from panels.x import Y
            ("core_a", "panel_b"): False,     # from panels import x
            ("core_b", "core_a"): False,      # lazy AND module-level: module wins
            ("core_b", "tool_a"): True,       # only inside a function
            ("tool_a", "core_a"): False,      # a class body runs at import
            ("panel_a", "core_a"): True,      # inside a method
        })
        # os / requests name no node; `import tool_d` is a CORE name and lands
        # nowhere; tool_b's relative import is skipped rather than guessed.


class TestNoExecution(_FixtureCase):
    def test_indexing_never_runs_a_module(self):
        snap = daedalus.index()
        self.assertIn("boom", snap["nodes"])
        self.assertFalse(os.path.exists(self.sentinel),
                         "boom.py ran — the index imported a module")
        self.assertNotIn("boom", sys.modules)
        self.assertEqual(daedalus.docstring("boom")["docstring"],
                         "If this runs, the index imported it.")


class TestVerbatim(_FixtureCase):
    def test_docstring_is_exactly_what_ast_reads(self):
        nodes = daedalus.index()["nodes"]
        for key, node in nodes.items():
            if node["parse_error"]:
                continue
            with self.subTest(module=key):
                with open(os.path.join(self.root, *node["path"].split("/")),
                          encoding="utf-8") as fh:
                    expected = ast.get_docstring(ast.parse(fh.read()), clean=True)
                self.assertEqual(daedalus.docstring(key)["docstring"], expected)
                if expected is not None:
                    self.assertEqual(daedalus.handle(key)["verbatim"]["text"], expected)

    def test_clean_keeps_the_relative_indentation(self):
        text = daedalus.docstring("tool_b")["docstring"]
        self.assertIn("\n    indented detail survives cleaning", text)

    def test_handle_marks_the_text_for_verbatim_delivery(self):
        out = daedalus.handle("tool_a")
        self.assertEqual(out["module"], "tool_a")
        self.assertEqual(out["path"], "tools/tool_a.py")
        self.assertEqual(out["verbatim"]["label"], "daedalus")
        self.assertEqual(out["verbatim"]["title"], "tool_a · tools/tool_a.py")
        self.assertEqual(out["verbatim"]["ack"], "Here is the docstring for tool a.")

    def test_no_docstring_is_an_answer_not_an_error(self):
        self.assertIsNone(daedalus.docstring("core_b")["docstring"])
        out = daedalus.handle("core_b")
        self.assertNotIn("error", out)
        self.assertEqual(out["verbatim"]["text"], "(no module docstring)")
        self.assertEqual(out["verbatim"]["ack"], "core b has no module docstring.")


class TestResolution(_FixtureCase):
    def test_forgiving_spellings_and_display_names_resolve(self):
        for asked, key in (("tool_a", "tool_a"), ("TOOLS/Tool_A.py", "tool_a"),
                           ("tools.tool_a", "tool_a"), ("tool-a", "tool_a"),
                           ("widget panel", "widget_panel"),
                           ("The Tool", "tool_a"), ("core alpha", "core_a"),
                           ("Widgetry", "widget_panel"),
                           # a label spelled like a key — the live model sent
                           # "vox_array" for the "Vox Array" sidebar row
                           ("delta_deck", "tool_d"), ("Delta-Deck", "tool_d")):
            with self.subTest(asked=asked):
                self.assertEqual(daedalus.handle(asked)["module"], key)

    def test_an_unknown_name_is_a_slug_with_suggestions(self):
        out = daedalus.handle("toool_a")
        self.assertEqual(out["error"], "unknown_module: 'toool_a'")
        self.assertIn("tool_a", out["did_you_mean"])
        self.assertLessEqual(len(out["did_you_mean"]), 3)

    def test_suggestions_reach_through_display_names(self):
        self.assertIn("core_a", daedalus.handle("Core Alfa")["did_you_mean"])

    def test_garbage_never_raises(self):
        for asked in ("", "   ", None, 42, ["aura"], "../../etc/passwd"):
            with self.subTest(asked=asked):
                out = daedalus.handle(asked)
                self.assertTrue(out["error"].startswith("unknown_module: "))

    def test_an_internal_failure_is_a_slug(self):
        daedalus.log.disabled = False
        with mock.patch("tools.daedalus.docstring", side_effect=RuntimeError("secret")), \
                self.assertLogs("METIS.daedalus", "ERROR") as logged:
            out = daedalus.handle("tool_a")
        # The class name is logged; the exception's message never leaves.
        self.assertNotIn("secret", "".join(logged.output))
        self.assertEqual(out, {"error": "daedalus_failed: RuntimeError"})


class TestSourcePath(_FixtureCase):
    def test_a_known_module_resolves_inside_the_root(self):
        path = daedalus.source_path("tool_a")
        self.assertTrue(os.path.isfile(path))
        root = os.path.realpath(self.root)
        self.assertEqual(os.path.commonpath([root, path]), root)
        self.assertTrue(path.endswith(os.path.join("tools", "tool_a.py")))

    def test_an_unknown_module_has_no_path(self):
        self.assertIsNone(daedalus.source_path("nope"))
        self.assertIsNone(daedalus.source_path("../outside"))


class TestCache(_FixtureCase):
    def _counting(self):
        return mock.patch("tools.daedalus._parse_file", wraps=daedalus._parse_file)

    def test_nothing_changed_means_nothing_reparsed(self):
        with self._counting() as parse:
            first = daedalus.index()["revision"]
            built = parse.call_count
            again = daedalus.index()["revision"]
        self.assertEqual(built, len(daedalus.index()["nodes"]))
        self.assertEqual(parse.call_count, built, "an unchanged tree was reparsed")
        self.assertEqual(first, again)

    def test_a_touched_file_is_the_only_one_reparsed(self):
        before = daedalus.index()["revision"]
        path = os.path.join(self.root, "tools", "tool_a.py")
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        with self._counting() as parse:
            after = daedalus.index()["revision"]
        self.assertEqual(parse.call_count, 1)
        self.assertEqual(parse.call_args.args[1], "tools/tool_a.py")
        self.assertGreater(after, before)

    def test_an_edit_shows_up(self):
        daedalus.index()
        self.write("tools/tool_c.py", '"""Job: Say something new."""\n' + "#" * 40)
        self.assertEqual(daedalus.docstring("tool_c")["docstring"],
                         "Job: Say something new.")

    def test_added_and_removed_files_rebuild(self):
        daedalus.index()
        self.write("tools/tool_e.py", '"""Job: Arrive late."""\n')
        with self._counting() as parse:
            nodes = daedalus.index()["nodes"]
        self.assertIn("tool_e", nodes)
        self.assertEqual(parse.call_count, 1)
        os.remove(os.path.join(self.root, "tools", "tool_e.py"))
        with self._counting() as parse:
            nodes = daedalus.index()["nodes"]
        self.assertNotIn("tool_e", nodes)
        self.assertEqual(parse.call_count, 0)


class TestLayout(_FixtureCase):
    def test_same_index_same_coordinates(self):
        snap = daedalus.index()
        self.assertEqual(daedalus._unit_layout(snap), daedalus._unit_layout(snap))

    def test_every_node_lands_inside_the_canvas(self):
        pos = daedalus.layout(400, 300)
        self.assertEqual(set(pos), set(daedalus.index()["nodes"]))
        for key, (x, y) in pos.items():
            with self.subTest(module=key):
                self.assertTrue(0 <= x <= 400 and 0 <= y <= 300)

    def test_a_resize_rescales_without_recomputing(self):
        daedalus.layout(400, 300)
        with mock.patch("tools.daedalus._unit_layout") as compute:
            small = daedalus.layout(400, 300)
            big = daedalus.layout(800, 600)
        compute.assert_not_called()
        self.assertNotEqual(small, big)


class TestSchema(_FixtureCase):
    def test_the_enum_is_the_index(self):
        definition = daedalus._tool_definition()
        module = definition["function"]["parameters"]["properties"]["module"]
        self.assertEqual(set(module["enum"]), set(daedalus.index()["nodes"]))
        self.assertIn("tool_a — Report the thing. (The Tool)", module["description"])
        self.assertEqual(definition["function"]["parameters"]["required"], ["module"])

    def test_a_single_module_tree_has_no_enum(self):
        """One legal value carries no information (test_tool_schema_contract)."""
        lone = tempfile.mkdtemp(prefix="daedalus_lone_")
        self.addCleanup(shutil.rmtree, lone, True)
        with open(os.path.join(lone, "only.py"), "w", encoding="utf-8") as fh:
            fh.write('"""Job: Be alone."""\n')
        daedalus._APP_ROOT = lone
        module = daedalus._tool_definition()["function"]["parameters"]["properties"]["module"]
        self.assertNotIn("enum", module)

    def test_keys_only_mode_keeps_the_enum_and_the_sidebar_names(self):
        """The measured cheaper setting (_GLOSS_CHARS = 0), kept switchable."""
        with mock.patch.object(daedalus, "_GLOSS_CHARS", 0):
            module = daedalus._tool_definition()["function"]["parameters"]["properties"]["module"]
        self.assertEqual(set(module["enum"]), set(daedalus.index()["nodes"]))
        self.assertIn("The Tool = tool_a", module["description"])
        self.assertNotIn("Report the thing.", module["description"])

    def test_long_glosses_are_cut_at_a_word(self):
        self.assertEqual(daedalus._short("word " * 30)[-1], "…")
        self.assertLessEqual(len(daedalus._short("word " * 30)), daedalus._GLOSS_CHARS + 1)
        self.assertEqual(daedalus._short("short"), "short")


def _tracked_sources():
    """App-root-relative .py paths git tracks, or None without git."""
    try:
        out = subprocess.run(["git", "ls-files", "*.py"], cwd=_REAL_ROOT, check=True,
                             capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return {line.replace("\\", "/") for line in out.splitlines() if line}


class TestRealTree(unittest.TestCase):
    """The shipped code, not a fixture."""

    def setUp(self):
        daedalus._APP_ROOT = _REAL_ROOT

    def test_the_schema_enum_is_exactly_the_index(self):
        module = daedalus.TOOL_DEFINITION["function"]["parameters"]["properties"]["module"]
        nodes = daedalus.index()["nodes"]
        expected = {k for k, v in nodes.items() if v["group"] in daedalus._ENUM_GROUPS}
        self.assertEqual(set(module["enum"]), expected)

    def test_every_tracked_source_file_is_a_node(self):
        """Assert against the repo, not the disk (CONVENTIONS §10): a new
        source folder the scope does not list would otherwise be invisible."""
        tracked = _tracked_sources()
        if tracked is None:
            self.skipTest("git unavailable")
        paths = {n["path"] for n in daedalus.index()["nodes"].values()}
        missing = sorted(p for p in tracked
                         if not p.startswith("tests/")
                         and os.path.basename(p) != "__init__.py"
                         and p not in paths)
        self.assertFalse(missing, f"tracked but not indexed: {missing}")

    def test_no_two_modules_share_a_key(self):
        self.assertEqual(daedalus.index()["collisions"], {},
                         "two files share a stem — pick a naming scheme before "
                         "Daedalus has to guess one")

    def test_no_real_module_fails_to_parse(self):
        broken = {k: n["parse_error"] for k, n in daedalus.index()["nodes"].items() if n["parse_error"]}
        self.assertEqual(broken, {})

    def test_sidebar_names_reach_the_index(self):
        """If the sidebar stops being a literal, this goes red rather than
        every display name silently vanishing from the model's gloss."""
        nodes = daedalus.index()["nodes"]
        self.assertIn("Atmospherics", nodes["aura"]["display"])
        self.assertIn("Settings", nodes["themis"]["display"])

    def test_daedalus_describes_itself(self):
        out = daedalus.handle("daedalus")
        with open(os.path.join(_REAL_ROOT, "tools", "daedalus.py"), encoding="utf-8") as fh:
            expected = ast.get_docstring(ast.parse(fh.read()), clean=True)
        self.assertEqual(out["verbatim"]["text"], expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
