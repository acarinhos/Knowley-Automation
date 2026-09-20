@echo off
title Knowley Control Room
chcp 65001 >nul
echo Knowley Uretim Kontrol Odasi baslatiliyor...
"%~dp0.venv\Scripts\python.exe" "%~dp0ui_tkinter.py"
if errorlevel 1 (
    echo.
    echo Bir hata olustu. Cikmak icin bir tusa basin...
    pause >nul
)
