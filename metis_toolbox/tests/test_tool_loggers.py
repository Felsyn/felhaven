"""
test_tool_loggers.py — every tool module names its own METIS.<name> logger.

CONVENTIONS §3 item 2 puts `log = logging.getLogger("METIS.<name>")` at the top
of every tool module, and §5 is why it matters: Emanon reads the log by logger
name and collapses the levels into a health verdict, so a module that logs under
a borrowed or missing name is invisible to it. The V0.03 audit (L5) found three
modules without one — horai, helios and selene — and a sentence in §3 had not
stopped that. This is the guard.

Parsed with ast, not grepped, so the logger must be a MODULE-LEVEL assignment:
one created inside a function is re-made on every call and is not what §3 means.
The name must be the module's own stem, since a copy-pasted "METIS.aura" in
another file would put its lines under Aura's health.

The allowlist holds what has not been decided yet, not a permanent exemption.
Horai reads only the clock and Themis and has no failure path to log, so the
audit's choice for it — add one, or write down the exemption — is still open.
A listed module that gains a logger fails too: a stale entry is the same drift.

    python -X utf8 -m unittest tests.test_tool_loggers
"""

import ast
import os
import unittest

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.join(_APP_ROOT, "tools")

# Tool modules still without a logger. Delete a line when the module gets one.
_NO_LOGGER_YET: set[str] = {"horai"}


def _tool_stems():
    return sorted(name[:-3] for name in os.listdir(_TOOLS)
                  if name.endswith(".py") and name != "__init__.py")


def _declares_own_logger(stem):
    """True if the module has `log = logging.getLogger("METIS.<stem>")` at
    module level."""
    with open(os.path.join(_TOOLS, f"{stem}.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target, call = node.targets[0], node.value
        if not (isinstance(target, ast.Name) and target.id == "log"):
            continue
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == "getLogger"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "logging"):
            continue
        if (len(call.args) == 1 and isinstance(call.args[0], ast.Constant)
                and call.args[0].value == f"METIS.{stem}"):
            return True
    return False


class TestToolLoggers(unittest.TestCase):

    def test_every_tool_module_has_its_own_logger(self):
        missing = {s for s in _tool_stems() if not _declares_own_logger(s)}
        unexpected = missing - _NO_LOGGER_YET
        self.assertFalse(
            unexpected,
            f"tool modules without a module-level "
            f'log = logging.getLogger("METIS.<name>"): {sorted(unexpected)}')

    def test_allowlist_has_no_stale_entries(self):
        stale = {s for s in _NO_LOGGER_YET
                 if s not in _tool_stems() or _declares_own_logger(s)}
        self.assertFalse(
            stale,
            f"these now have a logger (or no longer exist): {sorted(stale)} — "
            f"delete them from _NO_LOGGER_YET.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
