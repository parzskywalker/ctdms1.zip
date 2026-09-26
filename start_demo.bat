@echo off
REM One-command local demo (Windows). Requires Python 3.11+ from python.org (tick "Add to PATH").
cd /d "%~dp0"
if not exist .venv ( python -m venv .venv )
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
if not exist data\ctdms.db ( python -m app.cli seed-demo --reset )
echo Open http://127.0.0.1:8000  (admin / Admin@123)
start "" http://127.0.0.1:8000
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
