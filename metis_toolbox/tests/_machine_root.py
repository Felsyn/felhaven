"""
_machine_root.py — find the app root whose bin/ and manifest this machine runs.

A worktree has no bin/ of its own (bin/ and config/bin_manifest.json are per
machine and gitignored, tools/aegis.py), so a test that needs a real program
must look in the main checkout. Every test that needs bin/ finds it here,
so no two can disagree about where it is.

Worked out from git, never hardcoded. main_app_root() raises when git can't
answer, because returning nothing would turn into a skip, and a skip on the
machine that runs Felhaven is exactly what must not happen quietly.
"""

import os
import subprocess

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=APP_ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()


def main_app_root() -> str:
    """The main checkout's app root: this one, unless this is a worktree.
    The app root's place inside this checkout, joined onto the main checkout."""
    try:
        common = _git("rev-parse", "--path-format=absolute", "--git-common-dir")
        top = _git("rev-parse", "--show-toplevel")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"git could not locate the main checkout ({exc}); not "
                           "skipping, because a skip here could hide a mismatch") from exc
    rel = os.path.relpath(APP_ROOT, top)
    return os.path.normpath(os.path.join(os.path.dirname(common), rel))


def machine_root() -> str:
    """This checkout's app root if it has a bin/, else the main checkout's."""
    if os.path.isdir(os.path.join(APP_ROOT, "bin")):
        return APP_ROOT
    return main_app_root()
