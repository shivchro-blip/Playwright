@echo off
rem Runs setup.ps1 for this run only with the PowerShell execution policy bypassed (Windows refuses to run a
rem downloaded .ps1 file directly), and clears the "downloaded from the internet" mark on this folder's scripts.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0.' -File | Unblock-File"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
pause
