# Security Policy

## Scope — please read this first

Felhaven is a **personal, local-first desktop dashboard**, not security software.
Two of its modules *look* security-shaped. Here is exactly what each one does:

- **Sphynx** (the boot PIN/riddle gate) is explicitly *"family-misclick theater,"*
  not real access control.
- **Cerberus** keeps the API keys Felhaven uses (market prices, web search,
  terrain) **encrypted to your Windows account** with Windows DPAPI, and puts a
  **PIN child lock** over *using* them.
  - **What it protects:** the keys at rest. A copy of the vault file (a backup,
    a synced folder, a thumb drive, malware that copies it and leaves) can't be
    decrypted on another Windows account or another PC. Each key is bound to its
    own entry name, so moving one into another's slot fails, and a key that
    won't decrypt is logged and shown, never silently skipped.
  - **What the PIN does:** it decides when Felhaven's tools may use the keys and
    when its UI may change them. It **decrypts nothing**, so knowing the PIN
    yields no key. It is a child lock: it keeps people at the keyboard from
    clicking into things, and it locks itself when Felhaven closes, when Windows
    is locked, when the PC sleeps, and after an hour with no input.
  - **What it does not protect against:** anyone who can sign in to your
    Windows account, and any program running as you. Such a program can decrypt
    the keys at any time, PIN or no PIN, vault open or not.

So "the PIN is short" or "a program running as the user can read the keys" are
**known, documented properties**, not vulnerabilities. A copy of the vault file
that could be read on another account or PC **would** be one.

## Reporting a vulnerability

If you find something genuinely worth reporting privately — for example, the app
leaking data it shouldn't, or a problem in the install/portability path — please
use **GitHub's private vulnerability reporting**:

> the repo's **Security** tab → **Report a vulnerability**

That opens a private channel to the maintainer (**@Felsyn**). Please use it
instead of a public issue for anything sensitive. (The maintainer's GitHub
address is a no-reply, so this private advisory flow is the reliable way to reach
them — email won't be received.)

## What to expect

This is a snapshot of a personal project maintained in spare time — there is no
SLA. Reports will be read, but fixes land in the private development line and may
appear in a future snapshot rather than in this repository.
