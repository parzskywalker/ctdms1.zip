#!/usr/bin/env bash
# One-command local demo (macOS / Linux): installs dependencies into .venv, creates the demo DB once, starts the app.
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
if [ ! -d .venv ]; then "$PY" -m venv .venv; fi
. .venv/bin/activate
pip install -q -r requirements.txt
if [ ! -f data/ctdms.db ]; then python -m app.cli seed-demo --reset; fi
echo "Open http://127.0.0.1:8000  (admin / Admin@123)"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
