# Helios

Report sunrise, sunset, golden-hour windows, and day length. Computes them from
the Settings location and this computer's clock, so they work offline.

- **UI:** Atmospherics card → NOW tab (the Helios sub-widget)
- **Brain tool:** none — reaches the model through `get_sky_report`
- **Files:** `tools/helios.py`, `panels/aura_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_helios`

Details: see the docstring in `tools/helios.py`.
