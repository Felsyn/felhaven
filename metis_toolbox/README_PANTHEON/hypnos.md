# Hypnos

Tell whether the person has stepped away: locked, asleep or idle. Locked is
Windows locked, asleep is the PC asleep, and idle is no keyboard or mouse input
for a while. It only reads; Cerberus decides what to do.

- **UI:** none — Cerberus's worker reads it every 2 s
- **Brain tool:** none
- **Files:** `tools/hypnos.py`
- **Test:** `python -X utf8 -m unittest tests.test_hypnos`

Details: see the docstring in `tools/hypnos.py`.
