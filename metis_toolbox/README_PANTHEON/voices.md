# Voices

Resolve the voice Felhaven should speak in.

- **UI:** Moderati card → MACHINE SPIRIT tab (narration voice, persisted) and
  Vox Array card → ECHO tab (per-conversion voice, session-only)
- **Brain tool:** none
- **Files:** `voices.py`, `panels/voice_bar_panel.py`
- **Test:** `python -X utf8 -m unittest tests.test_voices tests.test_voice_bar_smoke`

Details: see the docstring in `voices.py`.
