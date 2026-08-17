# Demeter

Fetch, resample and cache local elevation, and render the terrain raster.

- **UI:** Navigation view → Cartograph (rendered by the Artemis panel)
- **Brain tool:** none
- **Files:** `tools/demeter.py`
- **Test:** `python -X utf8 -m unittest tests.test_demeter tests.test_artemis_palette`

Needs an OpenTopography key in the Cerberus vault
(`python cerberus.py set <PIN> opentopography_api_key <key>`); without one the
chart still caches roads and water and simply has no terrain.

Details: see the docstring in `tools/demeter.py`.
