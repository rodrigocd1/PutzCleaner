@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0"
set "ROOT=%~dp0"

rem --- Detecta chamada pelo launcher para suprimir o pause de sucesso ---
set "FROM_LAUNCHER="
if /I "%~1"=="--from-launcher" set "FROM_LAUNCHER=1"

echo ============================================================
echo   PutzCleaner - instalacao
echo ============================================================
echo.

rem ============================================================
rem  1) Python 3.11 x64 com Tkinter
rem ============================================================
set "PYEXE="

call :try_python py -3.11
if defined PYEXE goto :python_ok
call :try_python python
if defined PYEXE goto :python_ok

echo.
echo Python 3.11 de 64 bits com Tkinter nao foi encontrado.
echo Instale o Python pelo site oficial e execute setup.bat novamente.
goto :fail

:python_ok
echo Python encontrado: "%PYEXE%"
echo.

rem ============================================================
rem  2) Ambiente virtual
rem ============================================================
set "VENV_PY=%ROOT%.venv\Scripts\python.exe"

if exist "%VENV_PY%" goto :venv_ready

if exist "%ROOT%.venv\pyvenv.cfg" (
    echo Reparando ambiente virtual existente...
    "%PYEXE%" -m venv --upgrade "%ROOT%.venv"
    if errorlevel 1 goto :fail
) else (
    if exist "%ROOT%.venv" (
        echo A pasta .venv existe mas nao parece um ambiente virtual valido.
        echo Remova-a manualmente e execute setup.bat novamente.
        goto :fail
    )
    echo Criando ambiente virtual...
    "%PYEXE%" -m venv "%ROOT%.venv"
    if errorlevel 1 goto :fail
)

:venv_ready
if not exist "%VENV_PY%" (
    echo Falha ao preparar o ambiente virtual.
    goto :fail
)

set "PYTHONNOUSERSITE=1"
set "PIP_NO_CACHE_DIR=1"

echo Atualizando pip...
"%VENV_PY%" -m pip install --upgrade pip --disable-pip-version-check --no-input
if errorlevel 1 goto :fail

echo Instalando dependencias (requirements.txt)...
"%VENV_PY%" -m pip install -r "%ROOT%requirements.txt" --disable-pip-version-check --no-input
if errorlevel 1 goto :fail

echo Validando imports...
"%VENV_PY%" -c "import tkinter; from faster_whisper import WhisperModel; print('imports ok')"
if errorlevel 1 goto :fail
echo.

rem ============================================================
rem  3) FFmpeg local
rem ============================================================
set "FFDIR=%ROOT%tools\ffmpeg\bin"
if exist "%FFDIR%\ffmpeg.exe" if exist "%FFDIR%\ffprobe.exe" (
    echo FFmpeg local ja existe; reutilizando.
    goto :ffmpeg_ok
)

call :install_ffmpeg
if errorlevel 1 goto :fail

:ffmpeg_ok
echo.

rem ============================================================
rem  4) Pre-carregar modelo small
rem ============================================================
if not exist "%ROOT%models" mkdir "%ROOT%models"
set "PUTZCLEANER_MODEL_DIR=%ROOT%models"
set "HF_HOME=%ROOT%models\.hf"
set "HF_HUB_CACHE=%ROOT%models\.hf\hub"
set "HF_HUB_DISABLE_TELEMETRY=1"

echo Pre-carregando o modelo small (pode baixar na primeira vez)...
"%VENV_PY%" -c "import os; from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8', download_root=os.environ['PUTZCLEANER_MODEL_DIR']); print('modelo small ok')"
if errorlevel 1 (
    echo Falha ao carregar o modelo small. Verifique a conexao de internet e tente novamente.
    goto :fail
)
echo.

rem ============================================================
rem  5) Marcador de setup completo
rem ============================================================
set "MARKER=%ROOT%.venv\.putzcleaner_setup_complete"
> "%MARKER%" echo PutzCleaner setup OK
>> "%MARKER%" echo python=%PYEXE%
if not exist "%MARKER%" goto :fail

echo ============================================================
echo   Instalacao concluida com sucesso
echo ============================================================
popd
if not defined FROM_LAUNCHER pause
endlocal
exit /b 0

rem ============================================================
rem  Sub-rotinas
rem ============================================================

:try_python
rem %* = comando candidato (ex.: "py -3.11" ou "python")
%* -c "import sys,platform,importlib; v=sys.version_info; importlib.import_module('tkinter'); importlib.import_module('venv'); sys.exit(0 if (v.major==3 and v.minor==11 and platform.python_implementation()=='CPython' and sys.maxsize>2**32) else 1)" >nul 2>&1
if errorlevel 1 goto :eof
for /f "delims=" %%E in ('%* -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%E"
goto :eof

:install_ffmpeg
set "FF_URL=https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.1.2-essentials_build.zip"
set "FF_SHA=db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec"
set "TMPDIR=%ROOT%tools\.setup-temp-%RANDOM%%RANDOM%"
set "STAGEDIR=%ROOT%tools\.ffmpeg-stage-%RANDOM%%RANDOM%"
set "ZIP=%TMPDIR%\ffmpeg.zip"

if not exist "%ROOT%tools" mkdir "%ROOT%tools"
mkdir "%TMPDIR%"
if errorlevel 1 exit /b 1

echo Baixando FFmpeg...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -Uri '%FF_URL%' -OutFile '%ZIP%' -UseBasicParsing } catch { exit 1 }"
if errorlevel 1 (
    echo Falha ao baixar o FFmpeg.
    call :cleanup_temp
    exit /b 1
)

echo Verificando integridade (SHA-256)...
set "GOTHASH="
for /f "delims=" %%H in ('powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 '%ZIP%').Hash"') do set "GOTHASH=%%H"
if /I not "%GOTHASH%"=="%FF_SHA%" (
    echo Hash do FFmpeg nao confere. Download abortado.
    echo Esperado: %FF_SHA%
    echo Obtido:   %GOTHASH%
    call :cleanup_temp
    exit /b 1
)

echo Extraindo FFmpeg...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Expand-Archive -Path '%ZIP%' -DestinationPath '%TMPDIR%\extract' -Force } catch { exit 1 }"
if errorlevel 1 (
    echo Falha ao extrair o FFmpeg.
    call :cleanup_temp
    exit /b 1
)

set "SRCFFMPEG="
set "SRCFFPROBE="
for /r "%TMPDIR%\extract" %%F in (ffmpeg.exe) do if not defined SRCFFMPEG set "SRCFFMPEG=%%F"
for /r "%TMPDIR%\extract" %%F in (ffprobe.exe) do if not defined SRCFFPROBE set "SRCFFPROBE=%%F"
if not defined SRCFFMPEG (
    echo ffmpeg.exe nao encontrado no pacote.
    call :cleanup_temp
    exit /b 1
)
if not defined SRCFFPROBE (
    echo ffprobe.exe nao encontrado no pacote.
    call :cleanup_temp
    exit /b 1
)

mkdir "%STAGEDIR%\bin"
copy /Y "%SRCFFMPEG%" "%STAGEDIR%\bin\ffmpeg.exe" >nul
copy /Y "%SRCFFPROBE%" "%STAGEDIR%\bin\ffprobe.exe" >nul

echo Validando FFmpeg baixado...
"%STAGEDIR%\bin\ffmpeg.exe" -hide_banner -encoders 2>nul | findstr /C:"libx264" >nul
if errorlevel 1 (
    echo Encoder libx264 ausente no FFmpeg baixado.
    call :cleanup_temp
    exit /b 1
)
"%STAGEDIR%\bin\ffmpeg.exe" -hide_banner -encoders 2>nul | findstr /R /C:" aac " >nul
if errorlevel 1 (
    echo Encoder aac ausente no FFmpeg baixado.
    call :cleanup_temp
    exit /b 1
)
"%STAGEDIR%\bin\ffprobe.exe" -version >nul 2>&1
if errorlevel 1 (
    echo ffprobe baixado nao funcionou.
    call :cleanup_temp
    exit /b 1
)

if exist "%ROOT%tools\ffmpeg" (
    echo A pasta tools\ffmpeg ja existe; reutilizando a instalacao anterior.
    call :cleanup_temp
    rmdir /S /Q "%STAGEDIR%" 2>nul
    exit /b 0
)

move "%STAGEDIR%" "%ROOT%tools\ffmpeg" >nul
if errorlevel 1 (
    echo Falha ao publicar o FFmpeg.
    call :cleanup_temp
    rmdir /S /Q "%STAGEDIR%" 2>nul
    exit /b 1
)

call :cleanup_temp
echo FFmpeg instalado localmente.
exit /b 0

:cleanup_temp
if defined TMPDIR if exist "%TMPDIR%" rmdir /S /Q "%TMPDIR%" 2>nul
goto :eof

:fail
echo.
echo A instalacao falhou. Corrija o problema indicado acima e execute novamente.
popd
if not defined FROM_LAUNCHER pause
endlocal
exit /b 1
