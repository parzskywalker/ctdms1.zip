@echo off
REM CTDMS one-click local start (Windows). Needs Python 3.11+ from python.org - tick "Add python.exe to PATH".
REM Safe to run every day: demo data is loaded ONLY into an empty database; existing data is never reset.
setlocal
cd /d "%~dp0"

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 (
  echo.
  echo  Python 3.11 or newer was not found on this computer.
  echo  Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
  echo  then run start_demo.bat again.
  goto :fail
)

if not exist .venv\Scripts\python.exe (
  echo Creating the Python environment - first run only...
  python -m venv .venv
  if errorlevel 1 goto :fail
)
call .venv\Scripts\activate.bat

echo Checking required packages...
python -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo.
  echo  Installing the required packages failed. Check the internet connection and try again.
  goto :fail
)

python -m app.cli prepare-demo
if errorlevel 1 goto :fail

echo.
echo  CTDMS is starting at http://127.0.0.1:8000
echo  Keep this window open while you use CTDMS. Close it to stop the app.
start "" http://127.0.0.1:8000
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
if errorlevel 1 (
  echo.
  echo  CTDMS stopped with an error - see the message above.
  echo  If it says the address is already in use, CTDMS is probably already running in another window.
  goto :fail
)
goto :eof

:fail
echo.
echo  CTDMS did not start. Read the message above, fix it, then run start_demo.bat again.
pause
exit /b 1
