@echo off
REM Starts CTDMS v1.6 on the DEMONSTRATION database (port 8004). Your real database (data\ctdms.db) is not used.
REM Demonstration logins: admin / Master@2026   and   demo.checker / Demo@Pass2026
setlocal
cd /d "%~dp0\..\..\.."
set "DEMO=%~dp0demo_database"
set "DB=%DEMO%\ctdms_v16_demo.db"
set "DB=%DB:\=/%"
set "CTDMS_DATABASE_URL=sqlite:///%DB%"
set "CTDMS_DATA_DIR=%DEMO%\appdata"
set "CTDMS_UPLOAD_DIR=%DEMO%\appdata\uploads"
set "CTDMS_BACKUP_DIR=%DEMO%\appdata\backups"
set "CTDMS_LOG_DIR=%DEMO%\appdata\logs"
set CTDMS_ENVIRONMENT=development
set CTDMS_SHOW_DEMO_LOGINS=false
set CTDMS_SEED_DEMO=false
set PYTHONUTF8=1
if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")
echo.
echo  CTDMS v1.6 DEMONSTRATION is starting on http://127.0.0.1:8004  (close this window to stop)
echo.
start "" http://127.0.0.1:8004/login
"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8004
