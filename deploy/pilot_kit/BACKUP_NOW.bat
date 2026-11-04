@echo off
REM CTDMS pilot - BACKUP NOW. Safe while CTDMS is running. Optionally copies the newest backup to a USB drive.
setlocal
cd /d "%~dp0"
call "%~dp0_ctdms_env.bat"

"%PY%" -m app.cli backup --keep 90
if errorlevel 1 (
  echo.
  echo  The backup did not work - see the message above.
  pause
  exit /b 1
)
echo.
set "drv="
set /p drv= To ALSO copy the newest backup to a USB drive, type its drive letter (for example E) and press Enter. Just press Enter to skip:
if not defined drv goto :done
set "drv=%drv:~0,1%"
if not exist "%drv%:\" (
  echo  There is no drive %drv%: - nothing was copied.
  goto :done
)
set "newest="
for /f "delims=" %%f in ('dir /b /o-d "data\backups\ctdms-*.sqlite3"') do (
  set "newest=%%f"
  goto :copy
)
:copy
if not defined newest (
  echo  No backup file was found.
  goto :done
)
if not exist "%drv%:\CTDMS_backups" mkdir "%drv%:\CTDMS_backups"
copy /y "data\backups\%newest%" "%drv%:\CTDMS_backups\" >nul
if errorlevel 1 (
  echo  Copying to %drv%: failed.
) else (
  echo  Copied %newest% to %drv%:\CTDMS_backups
)
:done
echo.
pause
