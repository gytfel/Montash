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
%PY% -m pip install --disable-pip-version-check --no-warn-script-location -r requirements.txt
if errorlevel 1 (
    echo.
    echo Не удалось установить библиотеки. Сделайте скриншот этого окна.
    pause
    exit /b 1
)
REM Сайты часто меняются и ломают скачивание по ссылке, поэтому yt-dlp обновляем
REM при каждом запуске. Без интернета просто пропускаем.
%PY% -m pip install -q --disable-pip-version-check --no-warn-script-location -U "yt-dlp[default,deno]" >nul 2>nul
echo.
echo Запуск Montash Studio. Не закрывайте это окно, пока работаете в программе.
%PY% main.py
if errorlevel 1 pause
