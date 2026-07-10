@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
set "ROOT=%~dp0"

rem --- Caches locais e isolamento ---
set "PYTHONNOUSERSITE=1"
set "HF_HOME=%ROOT%models\.hf"
set "HF_HUB_CACHE=%ROOT%models\.hf\hub"
set "HF_HUB_DISABLE_TELEMETRY=1"

set "PYW=%ROOT%.venv\Scripts\pythonw.exe"
set "MARKER=%ROOT%.venv\.putzcleaner_setup_complete"

if not exist "%PYW%" goto :need_setup
if not exist "%MARKER%" goto :need_setup
goto :launch

:need_setup
echo Preparando o PutzCleaner pela primeira vez...
call "%ROOT%setup.bat" --from-launcher
if errorlevel 1 (
    echo.
    echo A instalacao nao foi concluida. O PutzCleaner nao pode ser aberto.
    pause
    endlocal
    exit /b 1
)

:launch
start "" /D "%ROOT%" "%PYW%" "%ROOT%src\main.py"
endlocal
exit /b 0
