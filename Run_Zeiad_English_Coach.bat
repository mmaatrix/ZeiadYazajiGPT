@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\thirtytutors.exe" (
  echo Zeiad English Coach is not installed yet.
  echo Run Install_Windows.bat first.
  pause
  exit /b 1
)

".venv\Scripts\thirtytutors.exe"
