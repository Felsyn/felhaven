# Awen

When a song ends with Auto-DJ on, choose and play the next one and introduce it.
Pythia speaks the introduction.

- **UI:** Home view → Hestia's vox box → Auto-DJ toggle; the status line's queue count opens the queue window
- **Brain tools:** none — it is a job, not a tool. A job the model could start would be the model scheduling its own unprompted work.
- **Files:** `awen.py`, `panels/queue_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_awen`

Pythia's first unprompted job, and the stack's first "polled actor"
(CONVENTIONS §2): a `fetch()` that acts rather than reports. Most of the module
is about when *not* to fire.

It **reads** Euterpe's ledger for the taste and never adds to it — its own picks
live in `_session_plays`, in memory, for this run only. It still records the
songs the listener starts, because it is the only thing watching mpv.

Whose song it is, is decided by the video id mpv reports as loaded, not by
comparing two spellings of a title. A song it cannot attribute is filed as the
DJ's, because the two ways of being wrong cost wildly different amounts.

Details: see the docstring in `awen.py`.
