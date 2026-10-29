@echo off
rem Starts the AutomateNY web app and opens it in the browser. Run setup.ps1 first.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo .venv not found. Run setup.ps1 first.
    pause
    exit /b 1
)
start "" http://127.0.0.1:8080/
".venv\Scripts\python.exe" -m webapp
