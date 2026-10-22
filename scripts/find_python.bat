@echo off
REM Находит Python 3.10-3.14 и кладёт команду запуска в переменную PY.
REM Сначала лаунчер py (ставится с Python с python.org даже без галочки PATH),
REM потом python из PATH. Ярлык Microsoft Store вместо Python не подходит:
REM он не запускает код, поэтому проверяем выполнением, а не через where.
set "PY="
for %%v in (3.13 3.12 3.14 3.11 3.10) do (
    if not defined PY (
        py -%%v -c "import sys" >nul 2>nul && set "PY=py -%%v"
    )
)
if not defined PY (
    python -c "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 14) else 1)" >nul 2>nul && set "PY=python"
)
if defined PY exit /b 0
echo Не найден Python версии 3.10-3.14.
echo.
echo 1. Установите Python 3.13 с python.org и отметьте "Add python.exe to PATH".
echo 2. Если Python уже установлен, отключите ярлыки магазина Windows:
echo    Параметры - Приложения - Дополнительные параметры приложений -
echo    Псевдонимы выполнения приложений - выключите python.exe и python3.exe.
exit /b 1
