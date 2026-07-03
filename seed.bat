@echo off
REM One-time (and re-seed) of the Green Button connector session.
REM Opens a real browser; your account number + postal code are pre-filled from
REM config.toml -- just solve the CAPTCHA and click Submit. Run this again whenever
REM an unattended run reports "Connector session expired".
set "FOLDER=%~dp0"
set "PLAYWRIGHT_BROWSERS_PATH=%FOLDER%pw-browsers"
if exist "%FOLDER%.venv\Scripts\python.exe" (set "PYTHON=%FOLDER%.venv\Scripts\python.exe") else (set "PYTHON=python")
"%PYTHON%" "%FOLDER%elexicon_download.py" --login
echo.
echo Seeding finished - you can close this window.
pause
