@echo off
REM CTDMS pilot - a read-only look at this PC: Windows version, free space, network, firewall, port. Changes nothing.
setlocal
title CTDMS - check this PC
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\WindowsPowerShell\v1.0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0CHECK_THIS_PC.ps1"
echo.
pause
