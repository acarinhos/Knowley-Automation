@echo off
rem Max-speed test run: env applies to THIS window only. Pre-set any variable to override it.
chcp 65001 >nul
cd /d "%~dp0"
if not defined GENERATION_CONCURRENCY set GENERATION_CONCURRENCY=4
if not defined TRANSLATION_USE_AZURE set TRANSLATION_USE_AZURE=0
if not defined TRANSLATION_LLM_WORKERS set TRANSLATION_LLM_WORKERS=4
if not defined TRANSLATION_LLM_BATCH_SIZE set TRANSLATION_LLM_BATCH_SIZE=8
if not defined PYTHONIOENCODING set PYTHONIOENCODING=utf-8

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%~dp0..\..\..\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Virtualenv python not found next to or above this folder.
  pause
  exit /b 1
)

echo concurrency=%GENERATION_CONCURRENCY% azure=%TRANSLATION_USE_AZURE% workers=%TRANSLATION_LLM_WORKERS% batch=%TRANSLATION_LLM_BATCH_SIZE%
"%PY%" ui_tkinter.py
if errorlevel 1 (
  echo.
  echo ui_tkinter.py exited with an error.
  pause
)
