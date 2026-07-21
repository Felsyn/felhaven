# Helios

Report sunrise, sunset, golden-hour windows, and day length. Interprets Aura's
astronomy dict; makes no network call of its own.

- **UI:** Atmospherics card → NOW tab (the Helios sub-widget)
- **Brain tool:** `get_sun_times`
- **Files:** `tools/helios.py`, `panels/aura_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_helios`

Details: see the docstring in `tools/helios.py`.
