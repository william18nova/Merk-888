@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto usepython
py -3.12 scripts\pilot_launcher.py
goto finished
:usepython
python scripts\pilot_launcher.py
:finished
if errorlevel 1 echo No se completo la operacion. No borres los datos. Revisa el mensaje anterior.
pause
