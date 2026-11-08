# Aegis

Vouch for the programs in bin/ before Felhaven runs them.

- **UI:** none — Morpheus, Echo and Orpheus find mpv, yt-dlp and ffmpeg through it; their tabs say when it refuses one
- **Brain tool:** none
- **Files:** `tools/aegis.py`, `config/bin_manifest.json`
- **Test:** `python -X utf8 -m unittest tests.test_aegis tests.test_bin_manifest`

Details: see the docstring in `tools/aegis.py`. Recording an update: SETUP.md section 5.
