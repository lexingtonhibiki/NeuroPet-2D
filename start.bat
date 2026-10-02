@echo off
cd /d "%~dp0"
if exist "dist\NeuroPet-2D\NeuroPet-2D.exe" (
  start "" "dist\NeuroPet-2D\NeuroPet-2D.exe"
  exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" -m neuropet.main
  exit /b
)
python -m neuropet.main
if errorlevel 1 (
  echo Install Python 3.13, then run: python -m pip install -r requirements.txt
  pause
)
