@echo off
REM CTDMS pilot - STOP at the end of the day. Takes a last backup, then stops the server (it is not restarted).
setlocal
cd /d "%~dp0"
call "%~dp0_ctdms_env.bat"

netstat -ano | findstr /r /c:":%CTDMS_PORT% .*LISTENING" >nul
if errorlevel 1 (
  echo  CTDMS is not running.
  goto :done
)
echo  Taking a last backup...
"%PY%" -m app.cli backup --keep 90
echo stop> "data\stop.flag"
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%CTDMS_PORT% .*LISTENING"') do taskkill /PID %%p /T /F >nul 2>nul
ping -n 4 127.0.0.1 >nul
del "data\stop.flag" >nul 2>nul
netstat -ano | findstr /r /c:":%CTDMS_PORT% .*LISTENING" >nul
if errorlevel 1 (
  echo.
  echo  CTDMS has been stopped. Tomorrow double-click START_CTDMS.bat again.
) else (
  echo.
  echo  CTDMS is still running. Close the minimised window "CTDMS server" on the taskbar.
)
:done
echo.
pause
