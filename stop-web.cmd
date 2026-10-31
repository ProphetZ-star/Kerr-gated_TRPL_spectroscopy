@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-sim.ps1" -Stop -NoBrowser %*
set "KERR_EXIT=%ERRORLEVEL%"
if not "%KERR_EXIT%"=="0" pause
exit /b %KERR_EXIT%
