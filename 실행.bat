@echo off
chcp 65001 >nul
cd /d "%~dp0"
set FOUNDER_SETUP_OPENED=1
if exist "%~dp0setup.md" start "" notepad.exe "%~dp0setup.md"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-windows.ps1"
if errorlevel 1 pause
