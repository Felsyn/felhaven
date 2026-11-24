# Selene

Report the moon's phase, illumination, moonrise, and moonset. Computes them from
the Settings location and this computer's clock, so they work offline.

- **UI:** Atmospherics card → NOW tab (the Selene sub-widget); Celestarium view (the moon row)
- **Brain tool:** none — reaches the model through `get_sky_report`
- **Files:** `tools/selene.py`, `panels/aura_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_selene`

Details: see the docstring in `tools/selene.py`.
