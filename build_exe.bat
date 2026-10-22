@echo off
REM Сборка Montash.exe (Windows). Нужен Python 3.10+.
python -m pip install -r requirements.txt pyinstaller || exit /b 1
pyinstaller --noconfirm --onedir --windowed --name Montash ^
  --collect-all customtkinter ^
  --collect-all faster_whisper ^
  --collect-data imageio_ffmpeg ^
  --collect-submodules anthropic ^
  main.py || exit /b 1
if exist fonts xcopy /E /I /Y fonts dist\Montash\fonts >nul
echo.
echo Готово: dist\Montash\Montash.exe
echo Для скорости положите ffmpeg.exe и ffprobe.exe рядом с Montash.exe (иначе используется встроенный ffmpeg).
