# Scribe — Tasks & Notes

*Anti-Legion: ONE JOB*

Scribe is **local CRUD for tasks and notes** — a to-do list and a free-text notes
field, persisted to disk. It's the default **SCRIBE** tab in the gray
**Cogitator — tasks & tools** card. No network, no cleverness: add a task, toggle
it done, delete it, edit notes.

## Load → mutate → save → snapshot

Every public function follows the same shape: read the file, change it, write it
back, return a fresh snapshot. Nothing is held in memory between calls — the JSON
file is the state.

| Function | Does |
|---|---|
| `handle()` | read-only full snapshot |
| `add_task(text)` / `toggle_task(i)` / `delete_task(i)` | task CRUD |
| `set_notes(text)` / `append_note(line)` | notes |
| `load_data()` / `save_data(data)` | the raw persistence layer |

The snapshot adds derived counts (`open_count`, `done_count`) and the absolute
`data_file` path. `save_data` **never crashes** — a write failure logs and moves
on, because losing the app to a failed notes save would be absurd.

## The one that's *not* on the tool bus

Worth being precise here, because Scribe looks like the other tools but isn't
wired like them:

- It lives at the **package root** (`scribe.py`), **not** in `tools/`.
- It has a `handle()` and a docstring headed "METIS TOOL INTERFACE" — but it is
  **not registered** in Pythia's tool list (nor in the retired voice-side
  registry that once lived in `metis_toolbox/__init__.py`). The LLM cannot
  currently call it.
- That interface was written for the **archived** `metis_brain`. Today Scribe is
  effectively **panel-driven only**: Felhaven owns the task/notes data dict and
  hands it to the Cogitator with a save callback.

So if you're wondering "why isn't Scribe one of Pythia's tools?" — it's a
deliberate current state, not a missing wire. Re-registering it would be a small,
safe change if you ever want the LLM to manage your to-do list. (Contrast
[Plutus](../Vault/Plutus.md), which is kept off the bus *on principle*;
Scribe is merely off it *for now*.)

## Shared data, debounced save

Unlike Zeno and Eudoxus (self-contained), the `ScribePanel` receives the shared
`data` dict + an `on_save` callback from Felhaven. Edits are **debounced** (500 ms)
and flushed on close, so rapid typing doesn't hammer the disk and a fast quit
can't drop an edit.

## Files

| File | Committed? | Purpose |
|---|---|---|
| `scribe.py` | yes | The task/notes state layer. stdlib only, no tkinter. |
| `scribe_data.json` | **no** (runtime) | `{tasks: [...], notes: str}`; absent → clean default. |
| `panels/scribe_panel.py` → `ScribePanel` | yes | The **SCRIBE** tab. |

## Using it

**In the dashboard** — the **SCRIBE** tab of the **Cogitator** card (the default
tab): add tasks, check them off, jot notes.

**Standalone** — Scribe is a headless state module; import it or exercise it from
a REPL:

```python
python -c "import scribe; print(scribe.add_task('test'))"
```

## Tests

Not part of the shared handle suite (it isn't registered as an LLM tool). Its
persistence is exercised indirectly through the Felhaven home/Cogitator flow.
