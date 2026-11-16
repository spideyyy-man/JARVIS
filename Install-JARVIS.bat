@echo off
rem  JARVIS one-click installer for Windows. Double-click this file.
rem  It installs Python and Ollama if they are missing, sets JARVIS up, downloads the language model,
rem  runs the diagnostics, puts a JARVIS shortcut on your desktop and starts it.
title JARVIS installer
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\windows\install.ps1" %*
if errorlevel 1 (
  echo.
  echo  Installation did not finish. Read the messages above, or send them to whoever is helping you.
)
echo.
pause
