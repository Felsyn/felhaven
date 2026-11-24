# Artemis

Own the local-chart cache: its projection, area manifest and shared vector
format. It also holds the county reference.

- **UI:** Navigation view → Cartograph; Atmospherics' PLACE dropdown lists
  the cached areas
- **Brain tool:** none
- **Files:** `tools/artemis.py`, `panels/artemis_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_artemis tests.test_artemis_panel_smoke tests.test_artemis_ppm_spike tests.test_artemis_offline tests.test_artemis_palette`

Details: see the docstring in `tools/artemis.py`.
