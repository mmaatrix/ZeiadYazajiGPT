@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ==========================================
echo       Zeiad English Coach
echo ==========================================
echo.

where py >nul 2>&1
if errorlevel 1 (
  echo Python 3.11+ was not found.
  echo Install Python from https://www.python.org/downloads/windows/
  echo Then run this file again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating local Python environment...
  py -3.11 -m venv .venv
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

python -m thirtytutors setup
if errorlevel 1 goto :error

python -m thirtytutors
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo Zeiad English Coach could not start.
echo Review the error above.
pause
exit /b 1
