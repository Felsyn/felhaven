# Eratosthenes

Resolve a typed location query to a latitude and longitude.

- **UI:** Navigation view → Cartograph (the GO TO box)
- **Brain tool:** none
- **Files:** `tools/eratosthenes.py`
- **Test:** `python -X utf8 -m unittest tests.test_eratosthenes`

Coordinates and saved waypoints resolve offline; street addresses go to the
Census geocoder and need a connection. Waypoints live in a gitignored
`config/artemis_waypoints.json` — a list of `{"name", "lat", "lon"}`.

Details: see the docstring in `tools/eratosthenes.py`.
