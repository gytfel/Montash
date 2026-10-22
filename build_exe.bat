@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM Сборка Montash.exe (Windows).
call scripts\find_python.bat
if errorlevel 1 (
    pause
    exit /b 1
)
%PY% -m pip install --disable-pip-version-check --no-warn-script-location -r requirements.txt pyinstaller
if errorlevel 1 goto :fail
%PY% -m PyInstaller --noconfirm --onedir --windowed --name Montash ^
  --collect-all customtkinter ^
  --collect-all faster_whisper ^
  --collect-data imageio_ffmpeg ^
  --collect-submodules anthropic ^
  main.py
if errorlevel 1 goto :fail
if exist fonts xcopy /E /I /Y fonts dist\Montash\fonts >nul
echo.
echo Готово: dist\Montash\Montash.exe
echo Для скорости положите ffmpeg.exe и ffprobe.exe рядом с Montash.exe (иначе используется встроенный ffmpeg).
pause
exit /b 0

:fail
echo.
echo Сборка не удалась. Сделайте скриншот этого окна.
pause
exit /b 1
