@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv_build\Scripts\python.exe" (
    echo Execute primeiro gerar_executavel.bat para criar o ambiente de testes.
    pause
    exit /b 1
)
call ".venv_build\Scripts\activate.bat"
python -m ruff check renomeador_core.py renomeador_prefixo.py tests
if errorlevel 1 goto :erro
python -m pytest
if errorlevel 1 goto :erro
echo.
echo Todos os testes passaram.
pause
exit /b 0
:erro
echo.
echo Foram encontrados erros.
pause
exit /b 1
