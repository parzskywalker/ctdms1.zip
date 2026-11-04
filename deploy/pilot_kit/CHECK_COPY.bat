@echo off
REM CTDMS pilot - after copying the folder from the USB drive: is every file there and unchanged?
setlocal
cd /d "%~dp0"
title CTDMS - check the copy
call "%~dp0_ctdms_env.bat"
if not exist "%PY%" (
  echo  The program files are missing: copy the whole CTDMS folder again from the USB drive.
  pause
  exit /b 1
)
"%PY%" "%~dp0kit_tools.py" verify "%~dp0."
echo.
pause
