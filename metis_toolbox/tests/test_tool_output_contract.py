"""
test_tool_output_contract.py — one error vocabulary across every tool.

CONVENTIONS S2 has always said "on any error return {"error": ...}", and six
competing styles grew anyway: bare slugs (no_results), slug-plus-detail
(too_large: 4.2 MB), human sentences ("search failed"), raw exception text
(str(e)), computed strings (f"HTTP {status}"), and named constants (midas's
ERR_*). Those strings are read by the LLM and branched on by panels, so the
inconsistency is functional, not cosmetic. A doc rule with nothing behind it is
a suggestion — this is the guard.

Why AST and not a regex over source: the worst offender is `str(e)`, which no
text search can distinguish from a legitimate literal. Parsing also lets a
Name resolve through its module-level constant, so midas's ERR_* pattern —
the *recommended* shape for a module with several codes — is not flagged.

Amended 2026-07-23 (session 2): a literal 'slug: ' head no longer excuses an
f-string that splices the caught exception in after it — f"slug: {exc}" and
f"slug: {str(exc)}" are flagged. The blind spot was found in session 1;
callimachus, its one live case, joined the allowlist until session 13.

The allowlist is the point. It is seeded with every module that violates the
standard today, so this test is green from the day it lands rather than waiting
thirty audit sessions to exist. Each session deletes its own line. A module
that no longer violates but is still listed ALSO fails: a stale allowlist entry
is drift of exactly the kind this audit exists to remove.

Run from the package root:
    python -X utf8 -m unittest tests.test_tool_output_contract
"""

import ast
import os
import re
import unittest

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A bare slug, or a slug followed by human detail. The slug is the stable
# contract (panels and the model branch on it); the text after ": " is for
# humans and may change freely.
_SLUG = re.compile(r"^[a-z][a-z0-9_]*$")
_SLUG_DETAIL = re.compile(r"^[a-z][a-z0-9_]*: .+", re.DOTALL)
# For f-strings: the literal head must carry the slug before any interpolation,
# so the identifier stays greppable and matchable no matter what is spliced in.
_SLUG_PREFIX = re.compile(r"^[a-z][a-z0-9_]*: ")

# Modules that do not meet the standard yet. Delete a line when its audit
# session normalizes it — see the module walkthrough plan. Every entry here is
# a promise to come back, not a permanent exemption. Seeded 2026-07-21 from a
# real run: 20 sites across 5 modules, out of 97 error sites in total — the
# rest of the toolbox already conforms. Emptied 2026-07-25 (session 17):
# morpheus's "search failed" ×3 → "search_failed" was the last entry. Kept as a
# typed empty set so a future violation has a home; test_allowlist_has_no_stale_
# entries guards it against silently going stale (mirrors the version set below).
_EXPECTED_VIOLATIONS: set[str] = set()

# Files naming a version other than V0.02 in their header docstring. Emptied
# 2026-07-24 (session 6): ammit was tagged V0.02, the last non-audit version
# line in the toolbox. Kept as a named (typed) set so a future stray version
# line has a home — and test_version_allowlist_has_no_stale_entries guards it
# against silently going stale, exactly as the slug allowlist above is guarded.
_EXPECTED_VERSION_VIOLATIONS: set[str] = set()

_VERSION_LINE = re.compile(r"^\s*[Vv](\d+\.\d+):", re.MULTILINE)


def _source_files():
    """Every tool and app-root logic module, as paths relative to the app root.

    Panels are excluded on purpose: they *consume* error identifiers (mapping
    them to human text) rather than produce them. tests/ is excluded for the
    same reason — a test asserting on a bad identifier is evidence, not a
    violation to fix here.
    """
    out = []
    tools = os.path.join(_APP_ROOT, "tools")
    for name in sorted(os.listdir(tools)):
        if name.endswith(".py") and name != "__init__.py":
            out.append(f"tools/{name}")
    for name in sorted(os.listdir(_APP_ROOT)):
        if name.endswith(".py") and name != "__init__.py":
            out.append(name)
    return out


def _module_constants(tree):
    """{NAME: "value"} for module-level `NAME = "literal"` assignments.

    This is what keeps midas's ERR_* constants — the pattern S2 recommends
    once a module has three or more codes — from being reported as
    unverifiable Names.
    """
    consts = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            consts[node.targets[0].id] = node.value.value
    return consts


def _describe(value, consts, handler_names=frozenset()):
    """None if this error value meets the standard, else why it doesn't.

    `handler_names` are the file's `except ... as <name>` bindings: a literal
    'slug: ' head is not enough if what follows splices in the caught
    exception itself — f"search_failed: {exc}" is str(exc) with a hat on
    (the session-1 blind spot, closed 2026-07-23). Deliberately narrow: only
    a bare `{exc}` or `{str(exc)}` is flagged. Attribute reads pass, because
    `{exc.detail}` (zeno's own class, safe by construction) and `{exc.reason}`
    (a foreign message, NOT safe) look identical from here — attributes stay
    a human-audit concern, on the module's own session.
    """
    # A plain string literal (adjacent literals are already joined by the parser).
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        text = value.value
        if _SLUG.match(text) or _SLUG_DETAIL.match(text):
            return None
        return f"{text!r} is not a slug — use snake_case, or 'slug: detail'"

    # A module-level constant, e.g. midas's ERR_NO_KEY.
    if isinstance(value, ast.Name):
        if value.id in consts:
            text = consts[value.id]
            if _SLUG.match(text) or _SLUG_DETAIL.match(text):
                return None
            return f"{value.id} = {text!r} is not a slug"
        return (f"{value.id} is a name this test cannot resolve — point it at a "
                f"module-level string constant so the identifier is verifiable")

    # An f-string: the slug must come before any interpolation.
    if isinstance(value, ast.JoinedStr):
        head = value.values[0] if value.values else None
        if not (isinstance(head, ast.Constant) and isinstance(head.value, str)
                and _SLUG_PREFIX.match(head.value)):
            return ("f-string does not start with a literal 'slug: ' prefix — "
                    "the identifier must precede anything interpolated")
        for part in value.values:
            if not isinstance(part, ast.FormattedValue):
                continue
            expr = part.value
            if isinstance(expr, ast.Name) and expr.id in handler_names:
                return (f"f-string splices in the caught exception "
                        f"{{{expr.id}}} — that is str(exc) behind a slug, and "
                        f"it can still leak paths or key material; use "
                        f"{{type({expr.id}).__name__}} at most")
            if (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name)
                    and expr.func.id == "str" and len(expr.args) == 1
                    and isinstance(expr.args[0], ast.Name)
                    and expr.args[0].id in handler_names):
                return (f"f-string splices in str({expr.args[0].id}) — the "
                        f"caught exception's message; use "
                        f"{{type({expr.args[0].id}).__name__}} at most")
        return None

    # str(e), a bare local, a call, a conditional...
    kind = type(value).__name__
    return (f"{kind} — an error identifier must be a literal slug, not a "
            f"computed value (str(exc) can leak paths or key material into "
            f"the LLM context and the UI)")


def _violations():
    """[(relpath, lineno, reason)] for every non-conforming error value."""
    found = []
    for rel in _source_files():
        path = os.path.join(_APP_ROOT, rel.replace("/", os.sep))
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:                      # pragma: no cover
            found.append((rel, exc.lineno or 0, f"could not parse: {exc.msg}"))
            continue
        consts = _module_constants(tree)
        # Every `except ... as <name>` binding in the file. File-wide rather
        # than per-scope is a deliberate over-approximation: a name like `exc`
        # doubling as something other than an exception inside an error
        # f-string would be a false positive worth renaming anyway.
        handler_names = frozenset(
            h.name for h in ast.walk(tree)
            if isinstance(h, ast.ExceptHandler) and h.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if not (isinstance(key, ast.Constant) and key.value == "error"):
                    continue
                reason = _describe(value, consts, handler_names)
                if reason:
                    found.append((rel, getattr(value, "lineno", node.lineno),
                                  reason))
    return found


def _error_literal_count():
    """How many {"error": ...} sites the walk sees at all."""
    total = 0
    for rel in _source_files():
        path = os.path.join(_APP_ROOT, rel.replace("/", os.sep))
        with open(path, encoding="utf-8") as fh:
            try:
                tree = ast.parse(fh.read())
            except SyntaxError:                          # pragma: no cover
                continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                total += sum(1 for k in node.keys
                             if isinstance(k, ast.Constant) and k.value == "error")
    return total


class TestTheGuardIsNotVacuous(unittest.TestCase):
    """A walk that finds nothing passes for the wrong reason."""

    def test_the_walk_sees_the_error_sites(self):
        self.assertGreater(
            _error_literal_count(), 40,
            "the AST walk found almost no {\"error\": ...} sites — the return "
            "shape changed and this guard has gone blind")


class TestErrorIdentifiersAreSlugs(unittest.TestCase):
    def test_no_unexpected_violations(self):
        offenders = {rel for rel, _line, _why in _violations()}
        unexpected = offenders - _EXPECTED_VIOLATIONS
        if not unexpected:
            return
        detail = "\n".join(
            f"  {rel}:{line} — {why}"
            for rel, line, why in sorted(_violations())
            if rel in unexpected)
        self.fail(
            f"error identifiers must be a snake_case slug, optionally followed "
            f"by ': human detail' (CONVENTIONS S2):\n{detail}")

    def test_allowlist_has_no_stale_entries(self):
        offenders = {rel for rel, _line, _why in _violations()}
        stale = _EXPECTED_VIOLATIONS - offenders
        self.assertFalse(
            stale,
            f"these are listed as expected violations but now conform: "
            f"{sorted(stale)} — delete them from _EXPECTED_VIOLATIONS. A stale "
            f"allowlist is the same drift this audit exists to remove")


class TestVersionTagIsSingular(unittest.TestCase):
    """A module names one version, and it is the audit's.

    Deliberately does NOT assert every module *has* V0.02 — that would be red
    for the whole audit and tell you nothing you didn't already know.
    """

    def test_no_module_names_another_version(self):
        bad = []
        for rel in _source_files():
            path = os.path.join(_APP_ROOT, rel.replace("/", os.sep))
            with open(path, encoding="utf-8") as fh:
                doc = ast.get_docstring(ast.parse(fh.read())) or ""
            for m in _VERSION_LINE.finditer(doc):
                if m.group(1) != "0.02":
                    bad.append((rel, m.group(0).strip()))
        unexpected = [(rel, tag) for rel, tag in bad
                      if rel not in _EXPECTED_VERSION_VIOLATIONS]
        self.assertFalse(
            unexpected,
            f"a module's docstring names a version other than V0.02: "
            f"{unexpected} — the audit tag replaces any other version a file "
            f"carries")

    def test_version_allowlist_has_no_stale_entries(self):
        named_bad = set()
        for rel in _source_files():
            path = os.path.join(_APP_ROOT, rel.replace("/", os.sep))
            with open(path, encoding="utf-8") as fh:
                doc = ast.get_docstring(ast.parse(fh.read())) or ""
            if any(m.group(1) != "0.02" for m in _VERSION_LINE.finditer(doc)):
                named_bad.add(rel)
        stale = _EXPECTED_VERSION_VIOLATIONS - named_bad
        self.assertFalse(
            stale,
            f"these are listed as expected version violations but now name no "
            f"non-V0.02 version: {sorted(stale)} — delete them from "
            f"_EXPECTED_VERSION_VIOLATIONS. A stale allowlist is the same drift "
            f"this audit exists to remove (mirrors the slug guard above)")


if __name__ == "__main__":
    unittest.main()
