# Plutus

Track stock buys and sells; derive shares held and cost invested. Bookkeeping,
not market data.

- **UI:** Dynastic Vault view → LEDGER tab
- **Brain tool:** none — it mutates a real-money record, so only UI action changes it
- **Files:** `tools/plutus.py`, `panels/midas_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_plutus`

Details: see the docstring in `tools/plutus.py`.
