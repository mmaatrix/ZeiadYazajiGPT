@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo.
echo ========================================
echo   Zeiad English Coach
echo ========================================
echo.
echo Starting local server at http://127.0.0.1:8765
echo Press Ctrl+C to stop.
echo.

start "" "http://127.0.0.1:8765"
"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8765

pause
