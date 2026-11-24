# Themis

Own the per-install user preferences: location, units, clock, and how Pythia
answers. Every worker that needs the place reads it at fetch time; Pythia
reads her model, thinking and temperature at each request.

- **UI:** Moderati card → SETTINGS tab; Pythia's three on the MACHINE SPIRIT tab;
  the place Atmospherics shows on its PLACE dropdown
- **Brain tool:** none — configuration, not something the model sets
- **Files:** `themis.py`, `panels/themis_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_themis tests.test_themis_panel_smoke`

Details: see the docstring in `themis.py`.
