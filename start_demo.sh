#!/usr/bin/env bash
# CTDMS one-click local start (macOS / Linux). Needs Python 3.11+.
# Safe to run every day: demo data is loaded ONLY into an empty database; existing data is never reset.
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
fail() { echo; echo " CTDMS did not start: $1"; exit 1; }
"$PY" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null \
  || fail "Python 3.11 or newer was not found (set PYTHON=/path/to/python3.11 if it is installed elsewhere)."
if [ ! -x .venv/bin/python ]; then echo "Creating the Python environment - first run only..."; "$PY" -m venv .venv || fail "could not create .venv"; fi
. .venv/bin/activate
echo "Checking required packages..."
python -m pip install -q --disable-pip-version-check -r requirements.txt || fail "installing the required packages failed (internet connection?)."
python -m app.cli prepare-demo || fail "see the message above."
echo
echo " CTDMS is starting at http://127.0.0.1:8000"
exec python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
