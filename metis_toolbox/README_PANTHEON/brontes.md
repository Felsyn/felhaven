# Brontes

Read the NVIDIA GPU's name, temperature, memory and load through NVML. The
library is the one every NVIDIA driver installs, called through ctypes and
loaded by absolute path from System32, so a file of the same name beside
Felhaven can't be loaded in its place.

- **UI:** Moderati card → HEPHAESTUS tab → GPU (through Hephaestus)
- **Brain tool:** none — Hephaestus carries the reading to Pythia, through
  `get_machine_status` (part=vitals)
- **Files:** `tools/brontes.py`
- **Test:** `python -X utf8 -m unittest tests.test_brontes`

A machine without an NVIDIA card reads as `no_nvidia_gpu`, logged once, and the
GPU section says so in one calm line.

Details: see the docstring in `tools/brontes.py`.
