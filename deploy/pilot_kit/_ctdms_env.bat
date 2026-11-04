@echo off
REM Shared by the CTDMS pilot launchers. The kit runs on its OWN Python and libraries: nothing is taken from this PC,
REM so it does not matter what is (or is not) installed here.
set "CTDMS_HOME=%~dp0"
if "%CTDMS_HOME:~-1%"=="\" set "CTDMS_HOME=%CTDMS_HOME:~0,-1%"
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\WindowsPowerShell\v1.0"
set "PYTHONHOME="
set "PYTHONPATH=%CTDMS_HOME%\site-packages"
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "PY=%CTDMS_HOME%\python\python.exe"
if not defined CTDMS_PORT set "CTDMS_PORT=8000"
