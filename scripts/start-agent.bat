@echo off
setlocal
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m apps.agent
if errorlevel 1 pause
endlocal
