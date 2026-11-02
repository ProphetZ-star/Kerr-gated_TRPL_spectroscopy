@echo off
setlocal
title TRPL - Preparing native Qt environment
powershell.exe -NoProfile -NoLogo -ExecutionPolicy Bypass -File "%~dp0start-app.ps1" %*
set "KERR_EXIT=%ERRORLEVEL%"
if not "%KERR_EXIT%"=="0" pause
exit /b %KERR_EXIT%
