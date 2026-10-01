@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ==========================================
echo       Zeiad English Coach
echo       Developer: Zeiad Yazaji
echo ==========================================
echo.

where py >nul 2>&1
if errorlevel 1 (
  echo Python 3.11 or newer was not found.
  echo Install Python from https://www.python.org/downloads/windows/
  echo Then run this file again.
  pause
  exit /b 1
)

set "PYVER="
py -3.13 -c "import sys" >nul 2>&1 && set "PYVER=-3.13"
if not defined PYVER py -3.12 -c "import sys" >nul 2>&1 && set "PYVER=-3.12"
if not defined PYVER py -3.11 -c "import sys" >nul 2>&1 && set "PYVER=-3.11"

if not defined PYVER (
  echo Python launcher was found, but Python 3.11, 3.12, or 3.13 is not installed.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating local Python environment with %PYVER%...
  py %PYVER% -m venv .venv
  if errorlevel 1 (
    echo Failed to create the Python environment.
    pause
    exit /b 1
  )
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :error

python -m pip install -e .
if errorlevel 1 goto :error

python -m thirtytutors
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo Zeiad English Coach could not start.
echo Review the error above and send it to Zeiad Yazaji.
pause
exit /b 1
