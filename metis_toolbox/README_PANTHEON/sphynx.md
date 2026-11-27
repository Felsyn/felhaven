# Sphynx

Verify a PIN against the stored check, and track how many attempts remain. Soft
boot gate, not real security.

- **UI:** the boot gate, shown before the dashboard launches
- **Brain tool:** none
- **Files:** `sphynx.py`, `sphynx_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_sphynx tests.test_sphynx_panel_smoke`

Details: see the docstring in `sphynx.py`.
