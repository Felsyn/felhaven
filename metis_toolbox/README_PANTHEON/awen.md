# Awen

When a song ends with Auto-DJ on, choose and play the next one and introduce it.
Pythia speaks the introduction.

- **UI:** Home view → Hestia's vox box → Auto-DJ toggle; the status line's queue count opens the queue window
- **Brain tools:** none — it is a job, not a tool. A job the model could start would be the model scheduling its own unprompted work.
- **Files:** `awen.py`, `panels/queue_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_awen`

Details: see the docstring in `awen.py`.
