# Eos

Report space weather: NOAA's storm scales, the Kp index, and the aurora's
reach. Two small reads every 10 minutes from NOAA's Space Weather Prediction
Center (`noaa-scales.json`, the Kp forecast), through Hermes's bounded reader.
The aurora estimate is computed on the PC from the Settings coordinates, which
are never sent.

- **UI:** Atmospherics view → NOW → the SPACE WEATHER fold row; Navigation →
  Celestarium → the aurora row; Home → the aurora notice (only with Settings
  "Aurora notice too"); Moderati card → SETTINGS tab → ALERTS → Space weather
- **Brain tool:** none
- **Files:** `tools/eos.py`, `panels/eos_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_eos tests.test_eos_panel_smoke`

The aurora line is a rough guide, and its tooltip says so: NOAA's rule of thumb
(the oval's edge is about 66° magnetic latitude at Kp 0, moving 2° per Kp),
read from a dipole magnetic latitude, with 4° allowed for seeing it low on the
horizon.

Details: see the docstrings in `tools/eos.py` and `panels/eos_panel.py`.
