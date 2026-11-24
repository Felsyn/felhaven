# Euterpe

Remember the songs the listener chose, and what they seem to like. It answers
two questions: "have we heard this recently?" and "what does the listener
actually like?".

- **UI:** Vox Array → TUNES tab — the ledger as editable JSON
- **Brain tools:** none (out of LLM scope — the no-repeat rule is a guard the model must not be able to write around)
- **Files:** `tools/euterpe.py`, `panels/euterpe_panel.py`, `config/euterpe_config.json`
- **Test:** `python -X utf8 -m unittest tests.test_euterpe`

Details: see the docstring in `tools/euterpe.py`.
