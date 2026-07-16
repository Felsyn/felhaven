# Contributing to Felhaven

Thanks for looking! First, an honest heads-up about what this repository is.

## This is a read-only public snapshot

`Felsyn/felhaven` is a **frozen, portable snapshot** (FOE v.01) of Felhaven — a
personal, local-first dashboard built on the Metis Toolbox. **Active development
happens in a separate private repository;** this repo is a point-in-time copy
published so others can read, run, and learn from it. It has no ongoing commit
history and is not where the project is worked on day to day.

## Issues are welcome

If you run it and something breaks — especially a **portability problem on a
different machine**, a step in [`SETUP.md`](SETUP.md) that doesn't work, or a
plain bug — please **open an issue**. That feedback is genuinely valuable and is
the main reason this snapshot is public.

A good issue includes: your OS and Python version, what you ran, what you
expected, and what actually happened (error text or a screenshot).

## Pull requests

You're welcome to **fork and experiment**. But because development lives in the
private repo, **pull requests opened here may not be merged** — there is no
pipeline from this snapshot back into the private line. If you have a change
worth making, **open an issue describing it** instead; that's the path that can
actually be folded into the project.

## Running & testing

See [`SETUP.md`](SETUP.md) to install and launch. The hermetic test suite (no
network, no audio, no models needed) runs from `metis_toolbox/`:

```
python -X utf8 -m unittest discover -s tests -p "test_*.py"
```

## License

This snapshot is released under the **MIT License** — see
[`LICENSE`](LICENSE). You're free to use, copy, modify, and redistribute it,
including in your own projects, as long as the copyright notice and license text
travel with it. It's provided as-is, with no warranty. (Development still lives
in a separate private repo — see above — so this covers *reuse* of the code, not
a contribution pipeline back into the project.)
