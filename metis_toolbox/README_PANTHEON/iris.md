# Iris

Report the National Weather Service's active alerts for Felhaven's place. One
small read a minute (`api.weather.gov/alerts/active` at the Settings
coordinates), through Hermes's bounded reader. US only: elsewhere the answer
is "outside the Weather Service's area", a checked answer, not a failure.

- **UI:** Atmospherics view → the NOW banner and the ALERTS tab; Home → the
  warning strip and the popup (read aloud by Calliope); the Atmospherics row
  in the sidebar; Moderati card → SETTINGS tab → ALERTS
- **Brain tool:** none
- **Files:** `tools/iris.py`, `panels/iris_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_iris tests.test_iris_panel_smoke`

It reports and never decides: what pops up, what is read aloud and what was
seen are the panel's (AlertWatch), and a failed check says "couldn't check",
never "none active".

Details: see the docstrings in `tools/iris.py` and `panels/iris_panel.py`.
