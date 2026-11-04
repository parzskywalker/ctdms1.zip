@echo off
REM CTDMS pilot - create the administrator, or reset its password. The password is typed on THIS screen (hidden)
REM and is never written anywhere in clear text. START_CTDMS.bat runs this by itself on the very first start.
setlocal
cd /d "%~dp0"
call "%~dp0_ctdms_env.bat"

if not exist "%PY%" (
  echo  The program files are missing: this is not a complete CTDMS folder.
  goto :fail
)
"%PY%" -m app.cli init-db >nul
if errorlevel 1 goto :fail

echo.
echo  CREATE THE CTDMS ADMINISTRATOR
echo  You type the password twice. The letters do not show on the screen - that is normal.
echo  Rules: at least 8 characters, with a letter and a digit, not a common password, not the username.
echo.
set "ADM=admin"
set /p ADM= Administrator username [admin]:
set "FULL=Administrator"
set /p FULL= Full name shown on screen [Administrator]:
"%PY%" -m app.cli create-admin --username "%ADM%" --full-name "%FULL%"
if errorlevel 1 goto :fail
echo.
echo  Done. Sign in to CTDMS with this username and the password you just typed.
if /i not "%~1"=="fromstart" pause
exit /b 0

:fail
echo.
echo  The administrator was not created. Read the message above and try again.
if /i not "%~1"=="fromstart" pause
exit /b 1
