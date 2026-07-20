@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo  PREFIX v2.1.2 - Compilacao e validacao do executavel
echo ============================================================
echo.

rem Prioriza Python 3.14, mas aceita qualquer Python 3 disponivel.
set "PY="
where py >nul 2>&1
if not errorlevel 1 (
    py -3.14 -c "import sys" >nul 2>&1 && set "PY=py -3.14"
    if not defined PY py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 (
        python -c "import sys; raise SystemExit(0 if sys.version_info.major == 3 else 1)" >nul 2>&1
        if not errorlevel 1 set "PY=python"
    )
)
if not defined PY (
    echo [ERRO] Nenhuma instalacao do Python 3 foi encontrada.
    echo Instale o Python 3, preferencialmente 3.14 de 64 bits, e marque Add Python to PATH.
    pause
    exit /b 1
)

echo Interpretador selecionado:
%PY% -c "import sys; print(sys.executable); print(sys.version)"
if errorlevel 1 goto :erro
echo.

if exist ".venv_build" rmdir /s /q ".venv_build"
echo Criando ambiente isolado de compilacao com %PY%...
%PY% -m venv .venv_build
if errorlevel 1 goto :erro

call ".venv_build\Scripts\activate.bat"
if errorlevel 1 goto :erro

python -m pip install --disable-pip-version-check --upgrade pip
if errorlevel 1 goto :erro
python -m pip install --disable-pip-version-check -r requirements-build.txt
if errorlevel 1 goto :erro

python -m ruff check renomeador_core.py renomeador_prefixo.py tests
if errorlevel 1 goto :erro
python -m pytest
if errorlevel 1 goto :erro

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
python -m PyInstaller --clean --noconfirm PREFIX.spec
if errorlevel 1 goto :erro
if not exist "dist\PREFIX.exe" goto :erro

echo Executando autoteste com verificacao real de janela visivel...
pushd dist
if exist PREFIX_selftest_error.txt del /q PREFIX_selftest_error.txt
if exist PREFIX_selftest_ok.txt del /q PREFIX_selftest_ok.txt
start "" /wait "PREFIX.exe" --self-test
set "SELFTEST_RESULT=%ERRORLEVEL%"
if exist PREFIX_selftest_error.txt type PREFIX_selftest_error.txt
if not "%SELFTEST_RESULT%"=="0" goto :erro_popd
if not exist PREFIX_selftest_ok.txt goto :erro_popd
findstr /c:"JANELA VISIVEL" PREFIX_selftest_ok.txt >nul
if errorlevel 1 goto :erro_popd
popd

del /q "dist\PREFIX_selftest_error.txt" 2>nul
del /q "dist\PREFIX_selftest_ok.txt" 2>nul

echo.
echo ============================================================
echo  APROVADO: executavel abriu uma janela visivel no autoteste.
echo  Executavel: %cd%\dist\PREFIX.exe
echo ============================================================
pause
exit /b 0

:erro_popd
popd
:erro
echo.
echo [FALHA] O executavel nao foi aprovado.
echo Consulte mensagens acima e %%LOCALAPPDATA%%\PREFIX\startup_error.log.
pause
exit /b 1
