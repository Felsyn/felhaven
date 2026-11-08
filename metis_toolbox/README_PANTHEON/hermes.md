# Hermes

Read from the network, bounded: a byte cap and a deadline for the whole request.

- **UI:** none — the shared network reader every runtime fetch goes through
- **Brain tool:** none
- **Files:** `tools/hermes.py`
- **Test:** `python -X utf8 -m unittest tests.test_hermes tests.test_network_reads`

Details: see the docstring in `tools/hermes.py`.
