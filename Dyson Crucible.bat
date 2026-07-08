@echo off
title Dyson Crucible
cd /d "%~dp0"

REM ---------------------------------------------------------------
REM  Double-click this file to install (first time) and run the app.
REM  No commands to type. It opens in your web browser when ready.
REM ---------------------------------------------------------------

if not exist ".venv\Scripts\python.exe" goto :install
goto :start

:install
echo.
echo   First-time setup. This downloads several GB (Python, the AI engine,
echo   and art models) and can take a while. Safe to close and re-run.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1"
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   Setup did not finish. Scroll up for the message in yellow/red,
  echo   or just double-click this file again to resume.
  echo.
  pause
  exit /b 1
)

:start
echo.
echo   Starting Dyson Crucible... your browser will open in a moment.
echo   Keep this black window open while you use the app.
echo   Close it to stop the app.
echo.
REM open the browser a few seconds later, once the server is up
start "" cmd /c "ping -n 6 127.0.0.1 >nul & start "" http://127.0.0.1:7860"
".venv\Scripts\python.exe" conductor\server.py
echo.
echo   The app has stopped. You can close this window.
pause
