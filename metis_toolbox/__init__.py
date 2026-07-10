"""
metis_toolbox/__init__.py — package marker (intentionally empty)
================================================================
Metis Toolbox

This file once held the VOICE-SIDE tool registry: a hardcoded TOOLS list and a
dispatch() over the original 9 tools, consumed by the Metis voice loop (Apollo →
dispatch → Calliope) through the "package import regime".

That whole layer was retired when Felhaven went output-only (voice INPUT removed,
Calliope refactored to on-demand TTS). With Apollo and Metis.py gone there is no
untrusted spoken command surface left to route, so the frozen 9-tool allowlist
this file guarded no longer has anything to guard.

The one live registry now is Pythia's, built by reflection in pythia.py
(_TOOL_MODULES → TOOLS/_DISPATCH) — typed input, so it can't drift from its
handlers. See README_PANTHEON/README.md ("Two ways in, two registries").

Felhaven itself runs with this directory on sys.path and imports its modules
top-level (`from tools import ...`, `import pythia`), so nothing depends on this
file exporting anything — it exists only to keep `metis_toolbox` importable as a
package.
"""
