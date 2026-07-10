@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
set "ROOT=%~dp0"

echo ============================================================
echo   PutzCleaner - suporte a GPU (NVIDIA CUDA 12)
echo ============================================================
echo.
echo Este passo baixa as bibliotecas CUDA (cuBLAS e cuDNN), cerca de 1,3 GB.
echo Requer uma placa NVIDIA com drivers atualizados.
echo.

set "VENV_PY=%ROOT%.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo Ambiente virtual nao encontrado. Execute setup.bat primeiro.
    pause
    endlocal
    exit /b 1
)

set "PYTHONNOUSERSITE=1"
set "PIP_NO_CACHE_DIR=1"

echo Instalando bibliotecas CUDA...
"%VENV_PY%" -m pip install -r "%ROOT%requirements-gpu.txt" --disable-pip-version-check --no-input
if errorlevel 1 (
    echo.
    echo Falha ao instalar o suporte a GPU. Verifique a conexao e tente novamente.
    pause
    endlocal
    exit /b 1
)

echo.
echo Verificando a GPU...
"%VENV_PY%" -c "import sys; sys.path.insert(0,'src'); import main; main._configure_cuda_dll_search(); from transcriber import Transcriber; print('GPU CUDA detectada:', Transcriber.cuda_available())"

echo.
echo ============================================================
echo   Suporte a GPU instalado.
echo   Na janela do PutzCleaner, escolha "Processar em: cuda" (ou auto).
echo ============================================================
pause
endlocal
exit /b 0
