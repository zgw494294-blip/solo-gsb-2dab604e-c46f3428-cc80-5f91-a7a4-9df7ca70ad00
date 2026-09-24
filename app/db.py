"""SQLite connection management and schema initialization."""
import os
import sqlite3
from flask import g

DB_PATH = os.environ.get("STAGECUE_DB", "/data/stagecue.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS shows (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cues (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    show_id           INTEGER NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
    number            TEXT NOT NULL,
    cue_type          TEXT NOT NULL CHECK (cue_type IN ('light','sound','scene')),
    title             TEXT NOT NULL,
    notes             TEXT NOT NULL DEFAULT '',
    start_mode        TEXT NOT NULL CHECK (start_mode IN ('fixed','after')),
    fixed_seconds     REAL,
    predecessor_id    INTEGER REFERENCES cues(id) ON DELETE SET NULL,
    delay_seconds     REAL NOT NULL DEFAULT 0,
    duration_seconds  REAL NOT NULL DEFAULT 1,
    position          INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cues_show ON cues(show_id);
"""

# Demo show used to make a fresh instance self-explanatory.
SEED_SHOW = ("示例演出：《夜航》", "内置演示数据，可随意修改或删除。")
SEED_CUES = [
    # number, type, title, notes, mode, fixed, predecessor(1-based), delay, duration
    ("L1", "light", "开场暖光", "观众入场，面光渐亮至 60%", "fixed", 0.0, None, 0.0, 30.0),
    ("S1", "sound", "开场音乐", "环境音乐淡入", "after", None, 1, 0.0, 60.0),
    ("L2", "light", "起航光位", "冷蓝侧光，暖光淡出", "after", None, 1, 5.0, 8.0),
    ("SC1", "scene", "升起船帆布景", "两名换景员，黑场中完成", "after", None, 3, 2.0, 15.0),
    ("S2", "sound", "汽笛音效", "船帆就位后鸣笛", "after", None, 4, 0.0, 4.0),
]


def get_db():
    """Return a per-request SQLite connection."""
    if "db" not in g:
        os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(seed=True):
    """Create schema and (on a brand new database) insert demo data."""
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    first_time = not os.path.exists(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    if first_time and seed:
        _seed(conn)
    conn.commit()
    conn.close()


def _seed(conn):
    cur = conn.execute(
        "INSERT INTO shows (name, description) VALUES (?, ?)", SEED_SHOW
    )
    show_id = cur.lastrowid
    ids = []
    for idx, (number, ctype, title, notes, mode, fixed,
              pred_idx, delay, duration) in enumerate(SEED_CUES):
        cur = conn.execute(
            """INSERT INTO cues (show_id, number, cue_type, title, notes,
                   start_mode, fixed_seconds, predecessor_id, delay_seconds,
                   duration_seconds, position)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (show_id, number, ctype, title, notes, mode, fixed,
             None, delay, duration, idx),
        )
        ids.append(cur.lastrowid)
    for row_idx, (_, _, _, _, mode, _, pred_idx, _, _) in enumerate(SEED_CUES):
        if mode == "after" and pred_idx is not None:
            conn.execute(
                "UPDATE cues SET predecessor_id = ? WHERE id = ?",
                (ids[pred_idx - 1], ids[row_idx]),
            )


def init_app(app):
    app.teardown_appcontext(close_db)
