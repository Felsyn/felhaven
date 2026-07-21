# Themis

Own the per-install user preferences (location, temperature unit, clock format).
Aura, Hypatia, and Horai read it at fetch time.

- **UI:** Moderati card → SETTINGS tab
- **Brain tool:** none — configuration, not something the model sets
- **Files:** `themis.py`, `panels/themis_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_themis tests.test_themis_panel_smoke`

Details: see the docstring in `themis.py`.
