@echo off
setlocal
cd /d "%~dp0"
echo ===================================================
echo   Local Clone Studio - Iniciando
echo ===================================================
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set EXIT_CODE=%ERRORLEVEL%
if %EXIT_CODE% neq 0 (
    echo.
    echo O Local Clone Studio encerrou com codigo %EXIT_CODE%.
    pause
)
exit /b %EXIT_CODE%
