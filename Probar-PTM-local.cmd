@echo off
cd /d "%~dp0"
"build\hybrid\native-runtime-venv\Scripts\python.exe" scripts\ptm_lab.py
pause
