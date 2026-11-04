@echo off
REM CTDMS pilot - START. Safe to run every morning: it brings the database up to date, checks that no demo data is
REM present, takes a backup and starts the server in a minimised window titled "CTDMS server".
setlocal
cd /d "%~dp0"
title CTDMS - starting
call "%~dp0_ctdms_env.bat"

if not exist "%PY%" (
  echo.
  echo  The program files are missing: this is not a complete CTDMS folder.
  echo  Copy the whole CTDMS folder again from the USB drive to C:\CTDMS.
  goto :fail
)
if /i not "%CTDMS_HOME%"=="C:\CTDMS" echo  Note: CTDMS runs from %CTDMS_HOME% - the recommended place is C:\CTDMS.

netstat -ano | findstr /r /c:":%CTDMS_PORT% .*LISTENING" >nul
if not errorlevel 1 (
  echo.
  echo  CTDMS is already running - or another program is using port %CTDMS_PORT%.
  echo  Open http://127.0.0.1:%CTDMS_PORT% in the browser. If that is not the CTDMS sign-in page,
  echo  run STOP_CTDMS.bat and then START_CTDMS.bat again.
  goto :done
)

if not exist .env (
  echo Creating the settings file .env - first start on this PC only...
  "%PY%" -c "import secrets; open('.env','w',encoding='utf-8').write('CTDMS_ENVIRONMENT=production\nCTDMS_SECRET_KEY='+secrets.token_urlsafe(48)+'\nCTDMS_SHOW_DEMO_LOGINS=false\nCTDMS_SESSION_COOKIE_SECURE=false\n')"
  if errorlevel 1 goto :fail
)

echo Bringing the database up to date...
"%PY%" -m app.cli init-db
if errorlevel 1 goto :fail

"%PY%" -m app.cli pilot-ready
if errorlevel 3 (
  echo.
  echo  First start on this PC: create the administrator first.
  call "%~dp0SET_ADMIN_PASSWORD.bat" fromstart
  "%PY%" -m app.cli pilot-ready
)
if errorlevel 1 goto :fail

"%PY%" -m app.cli pilot-verify
if errorlevel 1 (
  echo.
  echo  CTDMS will not start while demo data is present. Read the STOP lines above.
  goto :fail
)

"%PY%" -m app.cli backup --keep 90
if errorlevel 1 echo  (The backup did not work - see the message above. CTDMS still starts.)

echo.
echo  Starting the server - the first start can take a minute...
echo  If Windows asks whether to allow Python: tick Private networks and press Allow - never Cancel.
if exist "data\stop.flag" del "data\stop.flag" >nul 2>nul
start "CTDMS server" /min "%~dp0_run_server.bat"
set /a waited=0
:wait
ping -n 3 127.0.0.1 >nul
netstat -ano | findstr /r /c:":%CTDMS_PORT% .*LISTENING" >nul
if not errorlevel 1 goto :up
set /a waited+=2
if %waited% lss 90 goto :wait
echo.
echo  The server did not start within 90 seconds.
echo  Open the minimised window "CTDMS server" on the taskbar to read why, or run CHECK_THIS_PC.bat.
goto :fail

:up
echo.
echo  ==========================================================================
echo   CTDMS IS RUNNING.  On every OTHER computer open ONE of these addresses:
powershell -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | ForEach-Object { '       http://' + $_.IPAddress + ':%CTDMS_PORT%' }"
echo        http://%COMPUTERNAME%:%CTDMS_PORT%
echo   On this computer:  http://127.0.0.1:%CTDMS_PORT%
echo.
echo   Do NOT close the minimised window "CTDMS server" - closing it stops CTDMS.
echo   This window can be closed. To stop CTDMS use STOP_CTDMS.bat.
echo  ==========================================================================
if not defined CTDMS_NO_BROWSER start "" "http://127.0.0.1:%CTDMS_PORT%"
:done
echo.
pause
exit /b 0

:fail
echo.
echo  CTDMS did not start. Read the message above, fix it, then run START_CTDMS.bat again.
pause
exit /b 1
