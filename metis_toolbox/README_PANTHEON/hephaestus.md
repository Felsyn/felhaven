# Hephaestus

Report the machine's vitals: CPU, RAM, disks, uptime and the GPU. The disks
are the boot drive and the drive Felhaven lives on; the GPU reading comes from
Brontes.

- **UI:** Moderati card → HEPHAESTUS tab: one scrolling column of SYSTEM, GPU,
  OLLAMA (Talos's loaded-model line) and AETHER (Aether's strip)
- **Brain tool:** none — reaches the model through `get_machine_status` (part=vitals)
- **Files:** `tools/hephaestus.py`, `panels/hephaestus_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_hephaestus tests.test_hephaestus_panel_smoke`

Details: see the docstring in `tools/hephaestus.py`.
