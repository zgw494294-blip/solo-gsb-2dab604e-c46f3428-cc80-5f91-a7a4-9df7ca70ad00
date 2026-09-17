"""SQLite 连接、建表初始化与示例数据。"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

import click
from flask import Flask, current_app, g

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS show (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS cue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    show_id    INTEGER NOT NULL REFERENCES show(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL DEFAULT 0,
    cue_type   TEXT    NOT NULL DEFAULT 'lighting',
    name       TEXT    NOT NULL,
    note       TEXT    NOT NULL DEFAULT '',
    mode       TEXT    NOT NULL DEFAULT 'fixed',
    fixed_at   REAL,
    depends_on INTEGER REFERENCES cue(id) ON DELETE SET NULL,
    delay      REAL    NOT NULL DEFAULT 0,
    duration   REAL    NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_cue_show ON cue(show_id, position);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(current_app.config["DATABASE_PATH"], timeout=10)
        conn.row_factory = sqlite3.Row
        # WAL 允许 gunicorn 多 worker 并发读写；busy_timeout 避免短时写锁报错。
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(exc: object = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(seed: bool = True) -> None:
    """建表；``seed=True`` 且库中没有任何演出时写入示例提示单。"""
    db_path = current_app.config["DATABASE_PATH"]
    parent = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(parent, exist_ok=True)
    db = get_db()
    db.executescript(SCHEMA)
    db.commit()
    if seed and db.execute("SELECT COUNT(*) FROM show").fetchone()[0] == 0:
        _seed(db)
    db.commit()


def _seed(db: sqlite3.Connection) -> None:
    now = _now()
    cur = db.execute(
        "INSERT INTO show (name, description, created_at, updated_at)"
        " VALUES (?, ?, ?, ?)",
        ("示例：小剧场话剧《夜航》", "首次启动自动生成的示例提示单，可随时删除。", now, now),
    )
    show_id = cur.lastrowid

    # (cue_type, name, note, mode, fixed_at, depends_rel, delay, duration)
    # depends_rel: 相对本条之前插入的第几条提示（1 基），None 表示固定。
    rows = [
        ("sound",    "开场前乐",   "观众入场轻音乐",        "fixed", 0.0,   None, 0,  30),
        ("lighting", "场灯暗",     "观众席灯光缓灭 3 秒",   "after", None, 1,    0,  3),
        ("scene",    "布景区就位", "舞台保持黑场",          "after", None, 2,    1,  10),
        ("lighting", "第一束面光", "打在船长位",            "after", None, 3,    2,  2),
        ("sound",    "海浪音效",   "循环底噪，音量 -18dB",  "fixed", 50.0,  None, 0,  120),
        ("lighting", "月光冷蓝",   "顶光 + 侧逆光",         "after", None, 4,    5,  8),
        ("scene",    "换景：甲板", "黑场换景，勿出声",      "after", None, 6,    0,  20),
        ("sound",    "汽笛一声",   "短促，注意吓观众风险",  "after", None, 5,    10, 4),
        ("lighting", "全场亮·谢幕", "暖白 100%",             "after", None, 7,    0,  15),
    ]
    ids: list[int] = []
    for position, (cue_type, name, note, mode, fixed_at, rel, delay, duration) in enumerate(rows):
        depends_on = ids[rel - 1] if rel else None
        cur = db.execute(
            "INSERT INTO cue (show_id, position, cue_type, name, note, mode,"
            " fixed_at, depends_on, delay, duration)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (show_id, position, cue_type, name, note, mode, fixed_at,
             depends_on, delay, duration),
        )
        ids.append(cur.lastrowid)


@click.command("init-db")
@click.option("--no-seed", is_flag=True, help="不写入示例演出")
def init_db_command(no_seed: bool) -> None:
    """建库建表并（可选）写入示例数据。"""
    init_db(seed=not no_seed)
    click.echo("数据库已初始化：%s" % current_app.config["DATABASE_PATH"])


def init_app(app: Flask) -> None:
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
