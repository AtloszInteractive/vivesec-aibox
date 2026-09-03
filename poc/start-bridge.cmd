@echo off
setlocal
title ViVeSec RAG bridge
cd /d "%~dp0"

rem --- Locate a Python 3 interpreter (full path first, then launchers) ---
set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [ERROR] Python 3 not found. Install Python 3.12+ from https://python.org and retry.
  pause
  exit /b 1
)

set "PYTHONUNBUFFERED=1"
set "PYTHONIOENCODING=utf-8"

echo ViVeSec RAG bridge
echo   Python : %PY%
echo   Needs  : Ollama running on http://127.0.0.1:11434
echo   Listens: http://127.0.0.1:8000   (press Ctrl+C to stop)
echo.
%PY% "%~dp0server.py"

echo.
echo Bridge stopped. Press any key to close this window.
pause >nul
