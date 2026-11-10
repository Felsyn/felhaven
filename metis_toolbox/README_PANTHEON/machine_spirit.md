# Machine Spirit

Own Pythia's system prompt — the default text plus an optional per-install
override.

- **UI:** Moderati card → MACHINE SPIRIT tab. The tab also carries the narration
  voice (`voices.py`) and Pythia's THINKING and TEMPERATURE (`themis.py`), each
  saved on click and applied to her next question
- **Brain tool:** none
- **Files:** `machine_spirit.py`, `panels/machine_spirit_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_machine_spirit tests.test_machine_spirit_panel_smoke`

Details: see the docstring in `machine_spirit.py`.
