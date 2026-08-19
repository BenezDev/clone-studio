@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0clone-studio.ps1" %*
exit /b %ERRORLEVEL%
