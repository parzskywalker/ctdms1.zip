@echo off
REM CTDMS pilot - run ONCE on the host PC, as Administrator (right-click > Run as administrator).
REM It does three things, all shown on screen:
REM   1. lets the OTHER computers of this office network reach CTDMS (Windows Firewall rule, port 8000, Private / Domain networks only)
REM   2. makes sure Windows treats this network as "Private" (asks first), otherwise the rule does not apply
REM   3. stops this PC from going to sleep while it is plugged in (a sleeping host PC = CTDMS unreachable)
setlocal
title CTDMS - one-time network setup
if not defined CTDMS_PORT set "CTDMS_PORT=8000"

fltmc >nul 2>nul
if errorlevel 1 (
  echo.
  echo  This step needs Administrator rights.
  echo  Close this window, then RIGHT-CLICK OPEN_FIREWALL_ONCE.bat and choose "Run as administrator".
  echo.
  pause
  exit /b 1
)

echo.
echo  1/3  Firewall: allowing computers of this office network to open CTDMS on port %CTDMS_PORT%
netsh advfirewall firewall delete rule name="CTDMS pilot" >nul 2>nul
netsh advfirewall firewall add rule name="CTDMS pilot" dir=in action=allow protocol=TCP localport=%CTDMS_PORT% profile=private,domain
if errorlevel 1 (
  echo  The firewall rule could not be added.
  pause
  exit /b 1
)

rem  Windows makes its own per-program rules when its pop-up asks about Python (Cancel = a BLOCK rule that beats the rule above).
rem  Remove every rule for THIS kit's python.exe, then add one allow rule for it.
netsh advfirewall firewall delete rule name=all dir=in program="%~dp0python\python.exe" >nul 2>nul
netsh advfirewall firewall add rule name="CTDMS pilot Python" dir=in action=allow program="%~dp0python\python.exe" enable=yes profile=private,domain >nul

echo.
echo  2/3  Network type of this PC:
powershell -NoProfile -Command "Get-NetConnectionProfile | ForEach-Object { '       ' + $_.Name + '  =  ' + $_.NetworkCategory }"
powershell -NoProfile -Command "if (Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Public' }) { exit 1 } else { exit 0 }"
if not errorlevel 1 goto :power
echo.
echo  A network is marked "Public". The firewall rule does not apply to Public networks, so other computers
echo  could not open CTDMS. Only change it if this is your OFFICE network, not a cafe or hotel Wi-Fi.
set "yn="
set /p yn= Change the Public network to Private now? Type Y and press Enter, or just press Enter to leave it:
if /i "%yn%"=="Y" powershell -NoProfile -Command "Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Public' } | Set-NetConnectionProfile -NetworkCategory Private"

:power
echo.
echo  3/3  Power: never sleep or hibernate while plugged in
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0

echo.
echo  Done. Now start CTDMS (START_CTDMS.bat) and test from a second computer:
echo  open  http://THIS-PC-ADDRESS:%CTDMS_PORT%  in its browser (START_CTDMS.bat shows the address).
echo.
pause
