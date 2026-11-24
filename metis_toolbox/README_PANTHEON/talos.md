# Talos

Report which models Ollama has installed and which it has loaded. Two small
reads every 5 s (`/api/tags`, `/api/ps`), plus `/api/show` once per model, all
through Hermes's bounded reader.

- **UI:** Moderati card → HEPHAESTUS tab → OLLAMA — LOADED MODEL; MACHINE
  SPIRIT tab → the MODEL row and its status line
- **Brain tool:** none
- **Files:** `tools/talos.py`
- **Test:** `python -X utf8 -m unittest tests.test_talos`

It reports and never acts: loading, unloading and chatting are Pythia's.
Pythia asks it only one thing, whether a model can think.

Details: see the docstring in `tools/talos.py`.
