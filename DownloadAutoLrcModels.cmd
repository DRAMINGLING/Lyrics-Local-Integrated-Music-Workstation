@echo off
set "ROOT=%~dp0"
set "PYTHON=%ROOT%auto-lrc-engine\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo Please read README.md and install the CPU or CUDA runtime first.
  echo The runtime installation is available from the Lyrics Start Menu group.
  pause
  exit /b 1
)
"%PYTHON%" "%ROOT%auto-lrc-engine\download_auto_lrc_models.py"
if errorlevel 1 (
  echo.
  echo Model download or verification failed. Read the message above and try again.
  pause
  exit /b 1
)
echo.
echo The model files are ready for offline alignment.
pause
