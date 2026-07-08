@echo off
title Dyson Crucible - Update
cd /d "%~dp0"
echo   Updating Dyson Crucible (keeps your settings)...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1"
echo.
echo   Done. Double-click "Dyson Crucible.bat" to start the updated app.
pause
