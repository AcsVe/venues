@echo off
chcp 65001 >nul
cd /d "%~dp0"
call scripts\find_python.bat
set PYTHONUTF8=1
REM Point 2.iajaward.org back to THIS PC right now (no data is moved)
"%PY%" ops\cf.py server
echo.
pause
