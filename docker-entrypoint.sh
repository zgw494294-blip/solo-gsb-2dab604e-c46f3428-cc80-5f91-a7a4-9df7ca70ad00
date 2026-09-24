#!/bin/sh
# Container entrypoint:
#   1. ensure the data directory exists (volume may be empty)
#   2. initialize the SQLite schema / seed demo data (idempotent)
#   3. serve via gunicorn
set -e

PORT="${PORT:-8000}"
STAGECUE_DB="${STAGECUE_DB:-/data/stagecue.db}"
export STAGECUE_DB

DATA_DIR="$(dirname "$STAGECUE_DB")"
mkdir -p "$DATA_DIR"

echo "[stagecue] initializing database at $STAGECUE_DB ..."
python -c "from app.db import init_db; import os; init_db(seed=os.environ.get('STAGECUE_SEED','1') not in ('0','false','False',''))"

echo "[stagecue] starting gunicorn on 0.0.0.0:${PORT}"
exec gunicorn \
    --bind "0.0.0.0:${PORT}" \
    --workers "${GUNICORN_WORKERS:-1}" \
    --threads "${GUNICORN_THREADS:-4}" \
    --timeout "${GUNICORN_TIMEOUT:-60}" \
    "app.server:create_app()"
