# Cerberus — The Guardian of Secrets

*Anti-Legion: THREE HEADS, ONE GUARDIAN*

Cerberus is the Metis Toolbox's **sole** encryption/secrets authority. Anything
that needs to store or read a secret defers to it — nothing else in the toolbox
rolls its own crypto.

## The three heads

| Head | Job |
|---|---|
| **Vault** | Encrypt/decrypt API keys and secrets at rest. The only place encryption logic lives. |
| **Custody** | A manifest-driven list of config files, each with a one-line description; click one to open it in your OS editor. |
| **Ledger** | Access log — what was read/opened, when. Vault reads dedupe per unlock session; Custody opens log in full. |

One Cerberus PIN gates all three. It is **separate** from Sphynx's app-gate PIN
(they never share a hash file or module).

## Threat model

Same class as Sphynx: protects against **accidental exposure and unauthorized
local access** — kids at the keyboard, casual snooping, screen-share slips. It is
**not** a defense against a determined attacker with tools and time. So: no OS
keychain, no HSM, no screenshot-blocking. Masking + reveal-on-demand is the full
extent of the visual protection.

## Crypto (stdlib only)

No `cryptography` dependency — the stack stays flash-drive-portable.

- **PBKDF2-HMAC-SHA256** (600k iterations, once per unlock) derives the Vault key
  from the PIN.
- At-rest cipher: **HMAC-SHA256 counter-mode keystream + encrypt-then-MAC**
  (integrity-checked with a constant-time compare).
- **Two separate salts in two files**: a lightweight verify-salt in
  `cerberus_data.json` (the access gate) and a KDF-salt in `cerberus_vault.json`
  (the encryption key). Verifying a PIN and deriving a key are separate ops.

**Losing the PIN means losing the Vault contents** — accepted, because the
initial contents are re-issuable API keys.

## Files

| File | Committed? | Purpose |
|---|---|---|
| `cerberus.py` | yes | Core logic — no tkinter, independently unit-testable. |
| `cerberus_data.json` | **no** (gitignored) | PIN hash + verify-salt. Per-user now — no longer shipped; first-run setup writes it (see below). |
| `cerberus_manifest.json` | yes | Custody config list (`config_dir` + `{file, desc}` rows) — stays committed as a template. |
| `cerberus_vault.json` | **no** (gitignored) | Encrypted secrets (salt + ciphertext). Ships uninitialized. |
| `cerberus_ledger.json` | **no** (gitignored) | Access log — metadata only, never secret values. |
| `panels/cerberus_panel.py` | yes | UI: the CERBERUS tab in Moderati. |

## Using it

**First run** — on a fresh clone `cerberus_data.json` doesn't exist, so the
**CERBERUS** tab shows a *set-a-PIN* prompt (choose + confirm); it writes the
file and opens straight into your empty Vault. No inherited PIN, no CLI step
required.

**PIN** — set at first run; change it any time:

```
python cerberus.py setpin <new-pin>
```

**Store / read a secret** (the explicit setup step; the Vault ships empty):

```
python cerberus.py set <pin> <name> <value>
python cerberus.py get <pin> <name>
python cerberus.py status
```

**In the dashboard** — open the **CERBERUS** tab (4th tab in Moderati), enter the
PIN, and the three sections unfold: Vault (values masked, reveal on demand,
plus a generic add/update form to write a new or changed secret), Custody
(click a config to edit it in your OS editor + Open Folder), Ledger (access
log, newest first).

The Vault's add/update form is one name field + one masked value field + a
SAVE action — not per-row inline editing, and it works for any key, present
or new. Submitting an existing name overwrites it silently, same as the CLI
`set` command; each write (name + action, never the value) is logged to the
Ledger, and unlike reads, writes are never deduped.

## Consumers

- **Dynastic Vault (Midas)** — the whole panel is sealed behind the Cerberus PIN,
  and its gate calls `cerberus.unlock()` (not just a PIN check), because
  Midas reads its Finnhub key from this same Vault (`finnhub_api_key`).
  Because Cerberus's session is module-level, unlocking Midas's gate also
  unlocks the Cerberus tab, and vice versa — "one guardian, one session."
- **Future** — Sibyl (cloud-LLM gateway) will call into `cerberus.py` for its
  crypto too, never its own.

## Tests

```
python -X utf8 -m unittest tests.test_cerberus \
    tests.test_cerberus_panel_smoke tests.test_midas_panel_smoke
```
