@echo off
title Dyson Crucible - Diagnostics
cd /d "%~dp0"

REM Writes a diagnostics report to diagnostics.txt for support, even if the app
REM will not open. Prefers the running app's live report; falls back to a static one.

set OUT=%~dp0diagnostics.txt
set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=py

echo Collecting diagnostics...
"%PY%" "%~dp0tools\diagnostics.py" > "%OUT%" 2>&1

echo.
echo Saved to: %OUT%
echo Send that file to whoever is helping you set up.
echo.
start "" notepad "%OUT%"
pause
