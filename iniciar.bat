@echo off
rem Flow Execution Debugger - Copyright (C) 2026 samcall22 (https://github.com/samcall22) - GPL-3.0-or-later
rem Launches Flow Execution Debugger. First run creates a local .venv and installs the dependencies.
rem Use "iniciar.bat debug" from a console to see errors.
setlocal
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"
set "VENV_PYW=.venv\Scripts\pythonw.exe"

rem Kept outside an if-block: %PYEXE% would be expanded before :find_python sets it.
if exist "%VENV_PYW%" goto :deps
call :find_python || goto :no_python
echo Preparando el entorno de Python (solo la primera vez)...
%PYEXE% -m venv .venv || goto :error

:deps
fc /b requirements.txt ".venv\requirements.installed" >nul 2>&1
if errorlevel 1 (
    echo Instalando dependencias...
    "%VENV_PY%" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :error
    copy /y requirements.txt ".venv\requirements.installed" >nul
)

if /i "%~1"=="debug" (
    "%VENV_PY%" main.py
    pause
    exit /b 0
)

start "" "%VENV_PYW%" main.py
exit /b 0

:find_python
set "PYEXE="
py -3 -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PYEXE=py -3"
if defined PYEXE exit /b 0
python -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PYEXE=python"
if defined PYEXE exit /b 0
exit /b 1

:no_python
echo.
echo No se ha encontrado Python 3.10 o superior.
echo Instalalo desde https://www.python.org/downloads/ marcando "Add python.exe to PATH"
echo o desde una terminal con:  winget install Python.Python.3.12
echo Despues vuelve a ejecutar este archivo.
echo.
pause
exit /b 1

:error
echo.
echo Se ha producido un error preparando el entorno. Revisa los mensajes anteriores.
echo Si estas detras de un proxy corporativo, consulta la seccion "Problemas frecuentes" de README.md.
echo.
pause
exit /b 1
