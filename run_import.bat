@echo off
REM Wrapper for Windows Task Scheduler. Runs the Elexicon -> HA import.
REM Edit PYTHON and FOLDER if you deploy this on a different machine.

set "FOLDER=%~dp0"
set "PYTHON=python"

cd /d "%FOLDER%"
"%PYTHON%" "%FOLDER%elexicon_to_ha.py" >> "%FOLDER%import.log" 2>&1
