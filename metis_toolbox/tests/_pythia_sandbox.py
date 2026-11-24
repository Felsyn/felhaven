"""
_pythia_sandbox.py — Pythia's per-machine inputs, pointed away from this checkout.

Every ask() reads three things that belong to the machine it runs on:

  - the owner's model, thinking and temperature (themis,
    felhaven_settings.json);
  - the Machine Spirit prompt override (machine_spirit_config.json);
  - with thinking on, what the model can do, asked of Ollama itself
    (talos.capabilities, an /api/show request).

A test that leaves them alone reads the owner's settings on the owner's
checkout and the defaults everywhere else. Measured 2026-11-19 (V0.03 S17)
with copies of the owner's two files: 35 tests in three files made 42
requests to the real Ollama, and 110 ran on the owner's prompt instead of the
default.

Use it for a whole module: start() in setUpModule, stop() in tearDownModule.
Both files then sit in a fresh scratch folder that is never written, so
themis and machine_spirit read their defaults, and talos.capabilities answers
as a thinking model without asking anyone. A test that needs another answer
patches it inside the test, and that patch wins.

Leading underscore => unittest's discover() does not collect this as a test
module.
"""

import tempfile
from pathlib import Path
from typing import Any, Optional
from unittest import mock

import machine_spirit
import themis
from tools import talos

# The model these tests were written against: one that can think.
THINKING_MODEL = ("completion", "tools", "thinking")


class PythiaSandbox:
    """themis and machine_spirit on an empty scratch folder, talos answered."""

    def __init__(self) -> None:
        self._dir: Optional[tempfile.TemporaryDirectory[str]] = None
        self._patches: list[Any] = []

    def start(self) -> None:
        self._dir = tempfile.TemporaryDirectory(prefix="pythia_sandbox_")
        folder = Path(self._dir.name)
        self._patches = [
            mock.patch.object(themis, "_DATA_PATH",
                              folder / "felhaven_settings.json"),
            mock.patch.object(machine_spirit, "_DATA_PATH",
                              folder / "machine_spirit_config.json"),
            mock.patch.object(talos, "capabilities",
                              return_value=list(THINKING_MODEL)),
        ]
        for patch in self._patches:
            patch.start()

    def stop(self) -> None:
        for patch in reversed(self._patches):
            patch.stop()
        self._patches = []
        if self._dir is not None:
            self._dir.cleanup()
            self._dir = None
