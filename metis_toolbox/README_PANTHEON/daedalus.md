# Daedalus

Map Felhaven's own modules, their docstrings and their imports, from the source.
Reads every file with `ast` and never imports one; a docstring reaches the
transcript word for word, never through the model.

- **UI:** Moderati card → DAEDALUS tab
- **Brain tool:** `get_module_docstring`
- **Files:** `tools/daedalus.py`, `panels/daedalus_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_daedalus tests.test_daedalus_panel_smoke tests.test_pythia_verbatim`

Details: see the docstring in `tools/daedalus.py`.
