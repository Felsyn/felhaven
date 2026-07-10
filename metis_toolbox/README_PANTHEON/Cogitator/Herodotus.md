# Herodotus — The Father of History

*Anti-Legion: ONE JOB*

Herodotus **manages a local archive of Markdown documents**. Named for
Herodotus of Halicarnassus, who gathered what was known, wrote it down, and
kept it so the deeds of men would not be erased by time — the first archivist.

Like [Callimachus](Callimachus.md), it has **no tab and no panel** — it is a
Pythia-only tool. The LLM reaches for it; the archive quietly grows. The
division of labor is exact: **the LLM thinks, Herodotus acts.** Herodotus knows
nothing about web search, encryption, embeddings, vector databases, or prompts.
It moves UTF-8 Markdown in and out of one directory, safely, and that is all.

## Five tools, one job

The toolbox's second multi-tool module (plural `TOOL_DEFINITIONS`, CONVENTIONS
§2/§3). All five faces look at the same archive:

| Tool | Does |
|---|---|
| **`list_documents()`** | Every `.md` in the archive — name, size, modified |
| **`search_documents(query)`** | Case-insensitive substring search → filenames + snippet lines |
| **`read_document(filename)`** | One document's full text, front matter included |
| **`write_document(filename, content, source="")`** | Create or overwrite, atomically; new docs get front matter |
| **`edit_document(filename, operation, target, content)`** | Surgical edits: `append` · `prepend` · `replace` · `insert_after_heading` · `insert_before_heading` · `replace_heading` |

The natural loop with its sibling: Callimachus fetches something worth keeping →
Pythia calls `write_document(..., source=<url>)` → the knowledge outlives the
session. Pull the web once; consult the archive forever.

## The first mutating brain tool — and its rails

`write_document` and `edit_document` change files on disk, which no brain tool
has done before (Plutus and Morpheus are out of LLM scope for exactly that
reason). Here mutation **is** the job, so the rails are strict and enforced on
every call:

1. **One hardcoded root** — `<app root>/herodotus_archive/`, anchored to
   `__file__` (§1). Every path is resolved and must land inside it.
2. **Bare `.md` filenames only** — an allowlist regex; no separators, so
   traversal is impossible by construction (and the resolve-check backstops
   it). Windows reserved device names (`CON.md`, `NUL.md`…) are refused too.
3. **UTF-8 only, both directions** — a binary or mis-encoded file is a
   `not_utf8` error, never mojibake.
4. **1 MB cap per document**, read and write.
5. **Atomic writes** — tempfile in the same directory + `os.replace`; a crash
   mid-write can never leave a half-document.

No shell, no subprocess, no network. And **no delete tool, deliberately** —
the archive grows or is edited; destruction stays a human act in a file
manager. `replace` demands its target appear **exactly once** (more context or
none — never a guess), and heading operations refuse ambiguous headings the
same way.

## Front matter — provenance for free

A new document that doesn't already open with a `---` fence gets one:

```yaml
---
title: <from the filename>
created: 2026-07-07T19:27:58
updated: 2026-07-07T19:27:58
source: <the optional source argument — e.g. the URL it came from>
tags:
---
```

`edit_document` bumps `updated:` when the block has one. Hand-rolled emit and
line-scan — no YAML dependency (§11), because Herodotus only ever needs to
write this one simple shape and touch one line of it.

## Vocabulary note

The archive directory is never called a *vault*: in this stack, **Vault means
Cerberus** — the secrets store. Herodotus keeps prose, not secrets, and the
two must never blur. The directory is gitignored (personal *state*, not
config — §9).

## Wiring

Pythia-only, exactly like Callimachus: add `herodotus` to
`pythia._TOOL_MODULES` and it registers itself through `_module_tools()`.
Keeping the archive out of any spoken command path was one reason the retired
voice-side registry (`metis_toolbox/__init__.py`) existed; with voice input gone,
the archive mutates only through Pythia's typed chat.
