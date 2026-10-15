#!/bin/sh
# Wait for the database, run migrations, optionally load demo data, then start the server.
set -e
echo "CTDMS: waiting for database..."
python - <<'PY'
import time, sys
from sqlalchemy import create_engine, text
from app.config import get_settings
url = get_settings().database_url
for i in range(60):
    try:
        with create_engine(url).connect() as c:
            c.execute(text("SELECT 1"))
        sys.exit(0)
    except Exception as e:  # noqa
        print("  db not ready:", str(e).splitlines()[0]); time.sleep(2)
sys.exit("database not reachable")
PY
python -m app.cli init-db
if [ "${CTDMS_SEED_DEMO:-false}" = "true" ]; then
  python -m app.cli seed-demo --if-empty
fi
python -m app.cli check || true
exec "$@"
