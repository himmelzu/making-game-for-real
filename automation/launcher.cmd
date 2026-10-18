@echo off
setlocal
cd /d "%~dp0"
"%~dp0runtime\python.exe" -I "%~dp0automation\main.py"
if errorlevel 1 pause
