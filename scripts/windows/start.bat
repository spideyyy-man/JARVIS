@echo off
cd /d "%~dp0..\.."
if not exist backend\.venv\Scripts\python.exe (
  echo Run scripts\windows\setup.ps1 first.
  pause
  exit /b 1
)
backend\.venv\Scripts\python.exe -m jarvis.desktop_app %*
