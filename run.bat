@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM Запуск Montash Studio из исходников.
call scripts\find_python.bat
if errorlevel 1 (
    pause
    exit /b 1
)
echo Найден:
%PY% --version
echo.
echo Проверка библиотек. В первый раз они скачиваются, это несколько минут...
%PY% -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo.
    echo Не удалось установить библиотеки. Сделайте скриншот этого окна.
    pause
    exit /b 1
)
echo.
echo Запуск Montash Studio. Не закрывайте это окно, пока работаете в программе.
%PY% main.py
if errorlevel 1 pause
