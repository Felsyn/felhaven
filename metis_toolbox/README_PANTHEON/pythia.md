# Pythia

Answer a question by talking to the local LLM, using toolbox tools for live
data. The sole tool registry.

- **UI:** Felhaven home view — the chat
- **Brain tool:** none — Pythia is the caller, not a tool
- **Files:** `pythia.py`, `panels/home_panel.py`, `panels/hestia_panel.py`, `panels/rite_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_pythia tests.test_pythia_guard tests.test_tool_handles tests.test_rite_panel_smoke`

The stats payload carries `tool_records`: one diagnostic record per tool
invocation (arguments, status, timing, error slug, capped result preview).
The Rite Log window renders it; the counts in the transcript derive from it.

`prewarm()` loads the model AND prefills the system+tools prefix at
startup; `is_warming()` is the panel's cue for that window.

Details: see the docstring in `pythia.py`.
