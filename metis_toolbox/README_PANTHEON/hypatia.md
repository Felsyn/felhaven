# Hypatia

Map where every catalog star and planet sits in the sky right now. Planets come
via Kepler; stars from the loaded catalog.

- **UI:** Celestarium view
- **Brain tool:** `get_sky_tonight`
- **Files:** `tools/hypatia.py`, `panels/hypatia_panel.py`, `hypatia_stars.json`,
  `hypatia_constellations.json`, `config/hypatia_lore.json`
- **Test:** `python -X utf8 -m unittest tests.test_hypatia tests.test_hypatia_panel_smoke`

Details: see the docstring in `tools/hypatia.py`.
