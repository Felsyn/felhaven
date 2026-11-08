# Selene

Report the moon's phase, illumination, moonrise, and moonset. Computes them from
the Settings location and this computer's clock, so they work offline.

- **UI:** Atmospherics card → NOW tab (the Selene sub-widget)
- **Brain tool:** `get_moon_phase`
- **Files:** `tools/selene.py`, `panels/aura_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_selene`

Details: see the docstring in `tools/selene.py`.
