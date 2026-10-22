@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM Запуск Montash Studio из исходников. Нужен Python 3.10+ в PATH.
where python >nul 2>nul || (echo Python не найден. Установите его с python.org и отметьте "Add python.exe to PATH". & pause & exit /b 1)
echo Проверка зависимостей...
python -m pip install -q -r requirements.txt || (echo Не удалось установить зависимости. & pause & exit /b 1)
start "" pythonw main.py
