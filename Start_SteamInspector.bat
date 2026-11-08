@echo off
chcp 65001 >nul
title Steam Universal Inspector & Inventory Checker
color 0b

echo ================================================================
echo       STEAM UNIVERSAL INSPECTOR ^& INVENTORY CHECKER
echo ================================================================
echo.
echo [1/2] Проверка зависимостей Python...

set "PYTHON_EXE=C:\Users\sd0fe\AppData\Local\Programs\Python\Python312\python.exe"

if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python"
)

"%PYTHON_EXE%" -c "import flask, requests" 2>nul
if %errorlevel% neq 0 (
    echo [!] Установка библиотек Flask и Requests...
    "%PYTHON_EXE%" -m pip install flask requests
)

echo [2/2] Запуск сервера Steam Inspector на порту 5001...
echo.
echo Веб-интерфейс доступен по адресу: http://127.0.0.1:5001
echo.
echo Для остановки нажмите Ctrl + C
echo ================================================================

start http://127.0.0.1:5001

"%PYTHON_EXE%" "%~dp0app.py"

pause
