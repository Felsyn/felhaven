# Pythia

Answer a question by talking to the local LLM, letting it call the toolbox's
tools when it needs live data. The sole tool registry.

- **UI:** Felhaven home view — the chat
- **Brain tool:** none — Pythia is the caller, not a tool
- **Files:** `pythia.py`, `panels/home_panel.py`, `panels/hestia_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_pythia tests.test_tool_handles`

Details: see the docstring in `pythia.py`.
