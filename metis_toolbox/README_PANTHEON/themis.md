# Themis

Own the per-install user preferences: location, units, clock, and how Pythia
answers. Aura, Hypatia, and Horai read it at fetch time; Pythia reads her
thinking and temperature at each request.

- **UI:** Moderati card → SETTINGS tab; Pythia's two on the MACHINE SPIRIT tab
- **Brain tool:** none — configuration, not something the model sets
- **Files:** `themis.py`, `panels/themis_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_themis tests.test_themis_panel_smoke`

Details: see the docstring in `themis.py`.
