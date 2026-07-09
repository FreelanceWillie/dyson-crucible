@echo off
title Dyson Crucible
cd /d "%~dp0"

REM ---------------------------------------------------------------
REM  Double-click this file to install (first time) and run the app.
REM  No commands to type. It opens in your web browser when ready.
REM ---------------------------------------------------------------

REM Run the full installer unless everything is in place:
REM  - .dc_installed marker (bootstrap finished, ComfyUI + models present)
REM  - a working venv with the core deps importable
if not exist ".dc_installed" goto :install
if not exist ".venv\Scripts\python.exe" goto :install
".venv\Scripts\python.exe" -c "import yaml, PIL, requests" >nul 2>&1
if errorlevel 1 goto :install
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
echo   Close it to stop the app. (Do not click inside it.)
echo.
REM Stop any previous copy still holding the port. A leftover/wedged server
REM would make the new one fail to start and the browser show a blank page.
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*conductor*server.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
REM open the browser a few seconds later, once the server is up
start "" cmd /c "ping -n 9 127.0.0.1 >nul & start "" http://127.0.0.1:7860"
REM Send the server's output to a log file, NOT this console. Writing to the
REM console can freeze the app if you click inside this window (Windows
REM "QuickEdit" pauses a program until you press a key) -- which looks exactly
REM like a blank / broken page. Logging to a file avoids that completely.
if not exist "logs" mkdir "logs"
".venv\Scripts\python.exe" conductor\server.py > "logs\server.log" 2>&1
echo.
echo   The app has stopped. Details are in  logs\server.log
pause
