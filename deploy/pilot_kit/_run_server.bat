@echo off
REM Runs the CTDMS server. Started by START_CTDMS.bat in a minimised window; if the server stops by itself it is
REM started again (up to 10 times). STOP_CTDMS.bat leaves data\stop.flag so that it is NOT restarted.
setlocal
cd /d "%~dp0"
title CTDMS server
call "%~dp0_ctdms_env.bat"
set /a tries=0

:run
"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port %CTDMS_PORT%
if exist "data\stop.flag" (
  del "data\stop.flag" >nul 2>nul
  exit 0
)
set /a tries+=1
if %tries% geq 10 goto :giveup
echo.
echo  The server stopped. Starting it again in 5 seconds (attempt %tries% of 10)...
ping -n 6 127.0.0.1 >nul
goto :run

:giveup
echo.
echo  The server keeps stopping. Close this window, then double-click START_CTDMS.bat again.
echo  If it keeps happening, keep the files in the folder "data\logs" for your support person.
pause
exit /b 1
