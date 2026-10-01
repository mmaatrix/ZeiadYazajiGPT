@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ==========================================
echo   Zeiad English Coach - Local Free Setup
echo ==========================================
echo.

where ollama >nul 2>&1
if errorlevel 1 (
  echo Ollama was not found.
  echo Install it first, then run this file again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo The app Python environment does not exist yet.
  echo Run Start_Zeiad_English_Coach.bat once, close the app, then run this setup again.
  pause
  exit /b 1
)

call ".venv\Scripts\activate.bat"

echo Installing local speech recognition...
python -m pip install -e ".[local]"
if errorlevel 1 goto :error

echo.
echo Making sure Qwen3 8B is installed in Ollama...
ollama pull qwen3:8b
if errorlevel 1 goto :error

echo.
echo Downloading/loading the local Whisper speech model once...
python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8'); print('Whisper local model is ready.')"
if errorlevel 1 goto :error

echo.
echo Local Free mode is ready.
echo Start Zeiad English Coach and choose:
echo Settings ^> Account ^> Local Free - Ollama / Qwen3 8B
pause
exit /b 0

:error
echo.
echo Local Free setup did not finish successfully.
echo Review the error above.
pause
exit /b 1
