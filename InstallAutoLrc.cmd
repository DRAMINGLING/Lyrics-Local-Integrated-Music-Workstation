@echo off
set "ROOT=%~dp0"
if /i "%~1"=="--cuda" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%auto-lrc-engine\setup.ps1" -Cuda
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%auto-lrc-engine\setup.ps1"
)
if errorlevel 1 (
  echo.
  echo Installation did not finish. Read the message above, then run this file again.
  pause
  exit /b 1
)
echo.
if /i "%~1"=="--cuda" (
  echo CUDA runtime setup complete. Double-click AutoLrcAligner.pyw to start the app.
) else (
  echo CPU runtime setup complete. Double-click AutoLrcAligner.pyw to start the app.
)
pause
