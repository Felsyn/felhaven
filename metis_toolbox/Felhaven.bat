@echo off
REM Felhaven launcher - starts the Sphynx boot gate with no console window.
REM Sphynx spawns felhaven.py itself once the riddle is answered.
REM %~dp0 is this .bat's own folder (metis_toolbox\, trailing backslash included),
REM so the repo runs from wherever it is cloned - no absolute path to edit.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" sphynx_panel.py
