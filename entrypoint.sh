#!/bin/sh
# 容器启动流程：初始化 SQLite（建表 + 首次启动写入示例演出），然后启动 Web 服务。
set -e

echo "[stagecue] 初始化数据库: ${DATABASE_PATH:-/data/stagecue.db}"
mkdir -p "$(dirname "${DATABASE_PATH:-/data/stagecue.db}")"
python -m flask --app wsgi.py init-db

echo "[stagecue] 启动 gunicorn，监听 0.0.0.0:${PORT:-8000}"
exec gunicorn \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${GUNICORN_WORKERS:-2}" \
    --timeout "${GUNICORN_TIMEOUT:-60}" \
    --access-logfile - \
    --error-logfile - \
    wsgi:app
