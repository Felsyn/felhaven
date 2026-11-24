# Argus

Report what this computer is communicating with on the network. Visibility, not
enforcement.

- **UI:** Moderati card → ARGUS tab
- **Brain tool:** none — reaches the model through `get_machine_status` (part=network)
- **Files:** `tools/argus.py`, `panels/argus_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_argus tests.test_argus_panel_smoke`

Details: see the docstring in `tools/argus.py`.
