@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM Сборка Montash.exe (Windows).
REM Временные файлы и кэш pip — рядом с программой, а не на системном диске.
if not exist "data\tmp" mkdir "data\tmp" 2>nul
if exist "data\tmp" (
    set "TEMP=%~dp0data\tmp"
    set "TMP=%~dp0data\tmp"
    set "PIP_CACHE_DIR=%~dp0data\pip-cache"
)
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
  --collect-all tkinterdnd2 ^
  --collect-submodules yt_dlp ^
  --collect-all yt_dlp_ejs ^
  main.py
if errorlevel 1 goto :fail
if exist fonts xcopy /E /I /Y fonts dist\Montash\fonts >nul
REM JS-движок Deno нужен yt-dlp для YouTube: кладём его рядом с exe.
for /f "delims=" %%i in ('%PY% -c "import deno; print(deno.find_deno_bin())"') do copy /Y "%%i" dist\Montash\ >nul
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
