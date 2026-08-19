@echo off
setlocal
cd /d "%~dp0"
echo ===================================================
echo   Local Clone Studio - Instalador para Windows
echo ===================================================
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set EXIT_CODE=%ERRORLEVEL%
if %EXIT_CODE% neq 0 (
    echo.
    echo ===================================================
    echo   A instalacao finalizou com avisos ou pendencias.
    echo ===================================================
)
echo.
pause
exit /b %EXIT_CODE%
