# Midas

Fetch current price and daily % change for a watchlist.

- **UI:** Dynastic Vault view → PRICES tab (behind the Cerberus PIN)
- **Brain tool:** `get_market_prices`
- **Files:** `tools/midas.py`, `panels/midas_panel.py`, `config/midas_watchlist.json`
- **Test:** `python -X utf8 -m unittest tests.test_midas tests.test_midas_panel_smoke`

Details: see the docstring in `tools/midas.py`.
