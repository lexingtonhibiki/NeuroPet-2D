@echo off
cd /d "%~dp0"
python tools\build_release.py --exclude-module numpy
exit /b %errorlevel%
