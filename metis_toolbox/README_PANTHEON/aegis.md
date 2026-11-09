# Aegis

Vouch for the programs in bin/ before Felhaven runs them.

- **UI:** none — Morpheus, Echo and Orpheus find mpv, yt-dlp and ffmpeg through it; their tabs say when it refuses one
- **Brain tool:** none
- **Files:** `tools/aegis.py`
- **Test:** `python -X utf8 -m unittest tests.test_aegis tests.test_bin_manifest`

The record itself (`config/bin_manifest.json`) is gitignored per-machine state,
like `bin/`. A fresh clone has none until `record` writes it.

Details: see the docstring in `tools/aegis.py`. Recording a program: SETUP.md section 5.
