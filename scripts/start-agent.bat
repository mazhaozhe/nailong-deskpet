@echo off
setlocal
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m agent
if errorlevel 1 pause
endlocal
