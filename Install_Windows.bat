@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================
echo   Zeiad English Coach - Windows Installer
echo   Developer: Zeiad Yazaji
echo ============================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
  py -3.13 -c "import sys" >nul 2>nul
  if %errorlevel%==0 (
    set "PY=py -3.13"
  ) else (
    py -3.12 -c "import sys" >nul 2>nul
    if %errorlevel%==0 (
      set "PY=py -3.12"
    ) else (
      set "PY=py -3.11"
    )
  )
) else (
  set "PY=python"
)

echo Using Python:
%PY% --version
if errorlevel 1 (
  echo.
  echo Python 3.11+ was not found.
  echo Install Python from https://www.python.org/downloads/windows/
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Creating isolated environment...
  %PY% -m venv .venv
  if errorlevel 1 goto :fail
)

echo.
echo Updating pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :fail

echo.
echo Installing Zeiad English Coach...
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto :fail

echo.
echo Installing hands-free support and downloading ThirtyTutors-compatible assets...
".venv\Scripts\thirtytutors.exe" setup
if errorlevel 1 goto :fail

echo.
echo ============================================
echo Installation completed successfully.
echo Run: Run_Zeiad_English_Coach.bat
echo ============================================
pause
exit /b 0

:fail
echo.
echo Installation failed. Copy the error shown above and send it to the developer.
pause
exit /b 1
