# Security Policy

## Scope — please read this first

Felhaven is a **personal, local-first desktop dashboard**, not security software.
Two of its modules *look* security-shaped and are **soft gates by design**, as
their own documentation states:

- **Sphynx** (the boot PIN/riddle gate) is explicitly *"family-misclick theater,"*
  not real access control.
- **Cerberus** (the secrets vault) protects against **accidental exposure and
  casual local snooping** — kids at the keyboard, a screen-share slip — **not** a
  determined attacker with tools and time. It uses stdlib crypto sized to that
  threat model, not an OS keychain or HSM.

So "the PIN is brute-forceable" or "the vault isn't a real password manager" are
**known, documented properties**, not vulnerabilities.

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
