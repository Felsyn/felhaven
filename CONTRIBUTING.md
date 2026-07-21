# Contributing to Felhaven

Thanks for looking! An honest heads-up about how this repository works.

## Code flows one way — the conversation doesn't

`Felsyn/felhaven` is a **public mirror** of the **Felhaven Operating Environment
(FOE)**, a personal, local-first dashboard built on the Metis Toolbox. Active
development happens in a separate private repository, so **code only travels
outward**: releases land here, and nothing merges back.

**That's a constraint on pull requests, not on talking to me.** Issues and
Discussions are both open, and both get read.

The mirror is **refreshed periodically, not frozen**: each release lands as a
single clean commit, and it is **never force-pushed** — so if you have already
cloned it, **`git pull` brings you the latest release**. What you won't find is
the private repo's day-to-day commit history, only the published snapshots.

## Where to put what

| You have | Use |
|---|---|
| A bug, a crash, or a [`SETUP.md`](SETUP.md) step that doesn't work | **Issues** |
| A portability problem — it misbehaves on your OS or hardware | **Issues** (the most valuable kind, and the main reason this is public) |
| A question about how something works, or why it's built that way | **Discussions → Q&A** |
| A feature idea or design suggestion | **Discussions → Ideas** |
| Something you built on top of it, or a screenshot of it running | **Discussions → Show and tell** |
| A security concern | **Neither** — see [`SECURITY.md`](SECURITY.md) and use GitHub's private vulnerability reporting |

A good issue includes: your OS and Python version, what you ran, what you
expected, and what actually happened (error text or a screenshot).

## Pull requests

You're welcome to **fork and experiment**. But because development lives in the
private repo, **pull requests opened here may not be merged** — there is no
pipeline from this snapshot back into the private line. If you have a change
worth making, describe it in an issue or a Discussion; that's the path that can
actually be folded into the project.

## Running & testing

See [`SETUP.md`](SETUP.md) — it covers install, launch, and the hermetic test
suite (no network, no audio, no models needed).

## License

This snapshot is released under the **MIT License** — see
[`LICENSE.md`](LICENSE.md). You're free to use, copy, modify, and redistribute it,
including in your own projects, as long as the copyright notice and license text
travel with it. It's provided as-is, with no warranty. (Development still lives
in a separate private repo — see above — so this covers *reuse* of the code, not
a contribution pipeline back into the project.)
