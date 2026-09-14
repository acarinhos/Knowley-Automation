@echo off
title Knowley Control Room
chcp 65001 >nul
echo Knowley Uretim Kontrol Odasi baslatiliyor...
python ui_tkinter.py
if errorlevel 1 (
    echo.
    echo Bir hata olustu. Cikmak icin bir tusa basin...
    pause >nul
)
