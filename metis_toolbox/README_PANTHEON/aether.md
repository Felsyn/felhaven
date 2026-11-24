# Aether

Report whether WiFi, the internet and Ollama are reachable.

- **UI:** Moderati card → HEPHAESTUS tab (the connectivity strip)
- **Brain tool:** none — reaches the model through `get_machine_status` (part=online)
- **Files:** `tools/aether.py`, `panels/hephaestus_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_aether tests.test_aether_widget_smoke`

Details: see the docstring in `tools/aether.py`.
