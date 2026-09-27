# Euterpe

Remember what music has played, and answer two questions about it: "have we
heard this recently?" and "what does the listener actually like?".

- **UI:** Vox Array → TUNES tab — the ledger as editable JSON
- **Brain tools:** none (out of LLM scope — the no-repeat rule is a guard the model must not be able to write around)
- **Files:** `tools/euterpe.py`, `panels/euterpe_panel.py`, `config/euterpe_config.json`
- **Test:** `python -X utf8 -m unittest tests.test_euterpe`

The log itself (`euterpe_ledger.json`, app root) is gitignored per-user state —
a fresh clone starts with no history, which is the design.

It holds the songs the **listener** chose, and only those. Awen filed its own
picks here until 2026-09-17 and now keeps them in memory for the run: this is a
list somebody curates, and the DJ reads it without adding to it.

One row per song. Playing something already on the list moves its row rather
than copying it; a duplicate typed into TUNES is refused by name. Identity
ignores the order across the dash — `Linkin Park - In The End` and `In The End -
Linkin Park` are one song — and the Unicode normal form, so `Gün` spelled with a
combining accent keys the same as `Gün` spelled with one codepoint.

A row written from a listener's play carries the video id of the upload that
played, so the guard can match the exact upload and not only the title.

The TUNES tab edits that log by hand and is the only write path besides
`record()`. It is not a `clear()`: the model still has no write handle, because
this module is still absent from `pythia._TOOL_MODULES`.

Two backups, two jobs. `euterpe_ledger.json.bak` is automatic and rolls;
`euterpe_ledger.json.base` is taken deliberately and nothing replaces it.

Details: see the docstring in `tools/euterpe.py`.
