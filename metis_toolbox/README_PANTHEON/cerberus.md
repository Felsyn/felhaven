# Cerberus

Keep the toolbox's API keys encrypted to this Windows account, and gate their use
behind a PIN child lock. Vault, Custody, and Ledger are its three heads.

- **UI:** Moderati card → CERBERUS tab; the lamp beside Scraptoken Flux on Home;
  the Dynastic Vault's gate
- **Brain tool:** none — deliberately out of LLM scope
- **Files:** `cerberus.py`, `panels/cerberus_panel.py`, `config/cerberus_manifest.json`
- **Test:** `python -X utf8 -m unittest tests.test_cerberus tests.test_cerberus_panel_smoke tests.test_secret_fixtures`

The keys are encrypted with Windows DPAPI, so a copied vault file can't be read on
another account or PC. The PIN decrypts nothing: it decides when tools may use the
keys. Anything running as you can read them; `SECURITY.md` says what that means.

Details: see the docstring in `cerberus.py`.
