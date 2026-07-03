@echo off
REM Full-auto path for Task Scheduler: Playwright download + import into HA.
REM Uses the in-folder venv + in-folder Playwright browser if present, else falls
REM back to the system "python". Seed the connector session first (once, headed):
REM     seed.bat   (or:  python elexicon_download.py --login)

set "FOLDER=%~dp0"
set "PLAYWRIGHT_BROWSERS_PATH=%FOLDER%pw-browsers"
if exist "%FOLDER%.venv\Scripts\python.exe" (set "PYTHON=%FOLDER%.venv\Scripts\python.exe") else (set "PYTHON=python")

"%PYTHON%" "%FOLDER%elexicon_download.py" --import >> "%FOLDER%autodownload.log" 2>&1
