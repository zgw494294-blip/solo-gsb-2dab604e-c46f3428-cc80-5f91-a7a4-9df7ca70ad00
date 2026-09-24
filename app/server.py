"""HTTP API and static page serving for the stage cue sheet app."""
import math
import os

from flask import Flask, jsonify, request, send_from_directory, Response

from .db import get_db, init_db, init_app
from .scheduler import (
    compute_schedule,
    CycleError,
    DependencyError,
    describe_chain,
)

CUE_TYPES = ("light", "sound", "scene")
START_MODES = ("fixed", "after")

app = Flask(__name__, static_folder="static", static_url_path="/static")
init_app(app)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ValidationError(Exception):
    def __init__(self, message, field=None):
        super().__init__(message)
        self.message = message
        self.field = field


@app.errorhandler(ValidationError)
def _on_validation_error(err):
    return jsonify({"error": err.message, "field": err.field}), 400


@app.errorhandler(404)
def _on_404(err):
    return jsonify({"error": "资源不存在"}), 404


@app.errorhandler(Exception)
def _on_http_exception(err):
    # Serialize werkzeug HTTPExceptions (409 cycle, 400, etc.) as JSON.
    from werkzeug.exceptions import HTTPException
    if isinstance(err, HTTPException):
        payload = {"error": err.description}
        data = getattr(err, "data", None)
        if data:
            payload = data
        return jsonify(payload), err.code
    raise err


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

CUE_FIELDS = (
    "id", "show_id", "number", "cue_type", "title", "notes", "start_mode",
    "fixed_seconds", "predecessor_id", "delay_seconds", "duration_seconds",
    "position", "created_at", "updated_at",
)


def cue_to_dict(row):
    return {key: row[key] for key in CUE_FIELDS}


def fetch_show(show_id):
    row = get_db().execute(
        "SELECT * FROM shows WHERE id = ?", (show_id,)
    ).fetchone()
    if row is None:
        from flask import abort
        abort(404)
    return row


def fetch_cues(show_id):
    return get_db().execute(
        "SELECT * FROM cues WHERE show_id = ? ORDER BY position, id", (show_id,)
    ).fetchall()


def show_payload(row):
    db = get_db()
    cues = [cue_to_dict(r) for r in fetch_cues(row["id"])]
    schedule, cycle = _safe_schedule(cues)
    end = max(
        (t["end"] for t in schedule.values() if t and t["end"] is not None),
        default=0.0,
    )
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "cue_count": len(cues),
        "duration": None if cycle else round(end, 3),
    }


def _safe_schedule(cues):
    """Return (schedule, cycle_payload). Never raises for cyclic data."""
    try:
        return compute_schedule(cues), None
    except CycleError as exc:
        return None, {
            "message": str(exc),
            "chain": [
                {"id": c["id"], "number": c["number"], "title": c["title"]}
                for c in exc.chain
            ],
        }
    except DependencyError as exc:
        return None, {"message": str(exc), "chain": []}


# ---------------------------------------------------------------------------
# Field validation
# ---------------------------------------------------------------------------


def _as_str(data, field, *, max_len, required=True, default=""):
    value = data.get(field, default)
    if value is None:
        value = default
    if not isinstance(value, str):
        raise ValidationError(f"字段 {field} 必须是字符串", field)
    value = value.strip()
    if required and not value:
        raise ValidationError(f"字段 {field} 不能为空", field)
    if len(value) > max_len:
        raise ValidationError(f"字段 {field} 长度不能超过 {max_len}", field)
    return value


def _as_number(data, field, *, minimum=None, required=True, default=None):
    value = data.get(field, default)
    if value is None or value == "":
        if required:
            raise ValidationError(f"字段 {field} 必填", field)
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"字段 {field} 必须是数字", field)
    value = float(value)
    if not math.isfinite(value):
        raise ValidationError(f"字段 {field} 必须是有限数字", field)
    if minimum is not None and value < minimum:
        raise ValidationError(f"字段 {field} 不能小于 {minimum}", field)
    if value > 1e9:
        raise ValidationError(f"字段 {field} 数值过大", field)
    return value


def validate_show_fields(data, partial=False):
    out = {}
    if "name" in data or not partial:
        out["name"] = _as_str(data, "name", max_len=200)
    if "description" in data or not partial:
        out["description"] = _as_str(
            data, "description", max_len=2000, required=False
        )
    return out


def validate_cue_fields(data):
    """Validate a cue payload. Returns column values (minus show_id/id)."""
    out = {
        "number": _as_str(data, "number", max_len=40, required=False),
        "cue_type": _as_str(data, "cue_type", max_len=16),
        "title": _as_str(data, "title", max_len=200),
        "notes": _as_str(data, "notes", max_len=2000, required=False),
        "start_mode": _as_str(data, "start_mode", max_len=8),
        "delay_seconds": _as_number(
            data, "delay_seconds", minimum=0, default=0
        ),
        "duration_seconds": _as_number(
            data, "duration_seconds", minimum=0.001, default=1
        ),
    }
    if out["cue_type"] not in CUE_TYPES:
        raise ValidationError("cue_type 必须是 light / sound / scene", "cue_type")
    if out["start_mode"] not in START_MODES:
        raise ValidationError(
            "start_mode 必须是 fixed / after", "start_mode"
        )

    if out["start_mode"] == "fixed":
        out["fixed_seconds"] = _as_number(
            data, "fixed_seconds", minimum=0
        )
        out["predecessor_id"] = None
        out["delay_seconds"] = out["delay_seconds"]
    else:
        out["fixed_seconds"] = None
        pred = data.get("predecessor_id")
        if pred is None or pred == "":
            raise ValidationError(
                "“在另一提示后开始”必须选择一个前置提示", "predecessor_id"
            )
        if isinstance(pred, bool) or not isinstance(pred, int):
            raise ValidationError("predecessor_id 非法", "predecessor_id")
        out["predecessor_id"] = pred
    return out


def check_schedule_or_conflict(cues):
    """Run the scheduler; translate cycles into a 409 response payload."""
    try:
        return compute_schedule(cues)
    except CycleError as exc:
        from werkzeug.exceptions import Conflict
        err = Conflict(description=str(exc))
        err.data = {
            "error": str(exc),
            "type": "cycle",
            "chain": [
                {"id": c["id"], "number": c["number"], "title": c["title"]}
                for c in exc.chain
            ],
            "chain_text": describe_chain(exc.chain),
        }
        raise err
    except DependencyError as exc:
        from werkzeug.exceptions import BadRequest
        raise BadRequest(description=str(exc))


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Shows
# ---------------------------------------------------------------------------


@app.get("/api/shows")
def list_shows():
    rows = get_db().execute("SELECT * FROM shows ORDER BY updated_at DESC, id DESC").fetchall()
    return jsonify([show_payload(r) for r in rows])


@app.post("/api/shows")
def create_show():
    data = request.get_json(silent=True) or {}
    fields = validate_show_fields(data)
    db = get_db()
    cur = db.execute(
        "INSERT INTO shows (name, description) VALUES (?, ?)",
        (fields["name"], fields.get("description", "")),
    )
    db.commit()
    row = db.execute("SELECT * FROM shows WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(show_payload(row)), 201


@app.get("/api/shows/<int:show_id>")
def get_show(show_id):
    row = fetch_show(show_id)
    db = get_db()
    cues = [cue_to_dict(r) for r in fetch_cues(show_id)]
    schedule, cycle = _safe_schedule(cues)
    cue_out = []
    for c in cues:
        times = (schedule or {}).get(c["id"], {"start": None, "end": None})
        cue_out.append({**c, "start": times["start"], "end": times["end"]})
    cue_out.sort(key=lambda c: (c["start"] is None, c["start"] if c["start"] is not None else 0, c["position"]))
    return jsonify({
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "cues": cue_out,
        "cycle": cycle,
    })


@app.put("/api/shows/<int:show_id>")
def update_show(show_id):
    fetch_show(show_id)
    data = request.get_json(silent=True) or {}
    fields = validate_show_fields(data, partial=True)
    db = get_db()
    sets, params = [], []
    for key in ("name", "description"):
        if key in fields:
            sets.append(f"{key} = ?")
            params.append(fields[key])
    if sets:
        sets.append("updated_at = datetime('now')")
        params.append(show_id)
        db.execute(f"UPDATE shows SET {', '.join(sets)} WHERE id = ?", params)
        db.commit()
    return jsonify(show_payload(
        db.execute("SELECT * FROM shows WHERE id = ?", (show_id,)).fetchone()
    ))


@app.delete("/api/shows/<int:show_id>")
def delete_show(show_id):
    fetch_show(show_id)
    db = get_db()
    db.execute("DELETE FROM shows WHERE id = ?", (show_id,))
    db.commit()
    return jsonify({"deleted": show_id})


# ---------------------------------------------------------------------------
# Cues
# ---------------------------------------------------------------------------


def _validate_predecessor(show_id, predecessor_id, cue_id=None):
    row = get_db().execute(
        "SELECT id FROM cues WHERE id = ? AND show_id = ?",
        (predecessor_id, show_id),
    ).fetchone()
    if row is None:
        raise ValidationError(
            "前置提示不存在或不属于当前演出", "predecessor_id"
        )
    if cue_id is not None and row["id"] == cue_id:
        # Self loop — caught by cycle detection too, but give a clear field.
        raise ValidationError("不能将自身设为前置提示", "predecessor_id")


@app.post("/api/shows/<int:show_id>/cues")
def create_cue(show_id):
    fetch_show(show_id)
    data = request.get_json(silent=True) or {}
    fields = validate_cue_fields(data)
    if not fields["number"]:
        fields["number"] = _auto_number(show_id)
    db = get_db()
    if db.execute(
        "SELECT 1 FROM cues WHERE show_id = ? AND number = ?",
        (show_id, fields["number"]),
    ).fetchone():
        raise ValidationError(f"提示编号 {fields['number']} 已存在", "number")
    if fields["start_mode"] == "after":
        _validate_predecessor(show_id, fields["predecessor_id"])

    position = db.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM cues WHERE show_id = ?",
        (show_id,),
    ).fetchone()["p"]

    # Tentative in-memory schedule check before anything is written.
    existing = [cue_to_dict(r) for r in fetch_cues(show_id)]
    tentative_id = min((c["id"] for c in existing), default=0) - 1
    tentative = {
        "id": tentative_id,
        "number": fields["number"],
        "title": fields["title"],
        "cue_type": fields["cue_type"],
        "start_mode": fields["start_mode"],
        "fixed_seconds": fields["fixed_seconds"],
        "predecessor_id": fields["predecessor_id"],
        "delay_seconds": fields["delay_seconds"],
        "duration_seconds": fields["duration_seconds"],
    }
    check_schedule_or_conflict(existing + [tentative])

    cur = db.execute(
        """INSERT INTO cues (show_id, number, cue_type, title, notes,
               start_mode, fixed_seconds, predecessor_id, delay_seconds,
               duration_seconds, position)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (show_id, fields["number"], fields["cue_type"], fields["title"],
         fields["notes"], fields["start_mode"], fields["fixed_seconds"],
         fields["predecessor_id"], fields["delay_seconds"],
         fields["duration_seconds"], position),
    )
    db.execute("UPDATE shows SET updated_at = datetime('now') WHERE id = ?", (show_id,))
    db.commit()
    row = db.execute("SELECT * FROM cues WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(cue_to_dict(row)), 201


@app.put("/api/cues/<int:cue_id>")
def update_cue(cue_id):
    db = get_db()
    row = db.execute("SELECT * FROM cues WHERE id = ?", (cue_id,)).fetchone()
    if row is None:
        from flask import abort
        abort(404)
    show_id = row["show_id"]
    data = request.get_json(silent=True) or {}
    fields = validate_cue_fields(data)
    if fields["start_mode"] == "after":
        _validate_predecessor(show_id, fields["predecessor_id"], cue_id=cue_id)

    if db.execute(
        "SELECT 1 FROM cues WHERE show_id = ? AND number = ? AND id <> ?",
        (show_id, fields["number"], cue_id),
    ).fetchone():
        raise ValidationError(f"提示编号 {fields['number']} 已存在", "number")

    existing = [cue_to_dict(r) for r in fetch_cues(show_id) if r["id"] != cue_id]
    tentative = {
        "id": cue_id,
        "number": fields["number"],
        "title": fields["title"],
        "cue_type": fields["cue_type"],
        "start_mode": fields["start_mode"],
        "fixed_seconds": fields["fixed_seconds"],
        "predecessor_id": fields["predecessor_id"],
        "delay_seconds": fields["delay_seconds"],
        "duration_seconds": fields["duration_seconds"],
    }
    check_schedule_or_conflict(existing + [tentative])

    db.execute(
        """UPDATE cues SET number=?, cue_type=?, title=?, notes=?, start_mode=?,
               fixed_seconds=?, predecessor_id=?, delay_seconds=?,
               duration_seconds=?, updated_at=datetime('now')
           WHERE id=?""",
        (fields["number"], fields["cue_type"], fields["title"], fields["notes"],
         fields["start_mode"], fields["fixed_seconds"], fields["predecessor_id"],
         fields["delay_seconds"], fields["duration_seconds"], cue_id),
    )
    db.execute("UPDATE shows SET updated_at = datetime('now') WHERE id = ?", (show_id,))
    db.commit()
    return jsonify(cue_to_dict(
        db.execute("SELECT * FROM cues WHERE id = ?", (cue_id,)).fetchone()
    ))


@app.delete("/api/cues/<int:cue_id>")
def delete_cue(cue_id):
    db = get_db()
    row = db.execute("SELECT * FROM cues WHERE id = ?", (cue_id,)).fetchone()
    if row is None:
        from flask import abort
        abort(404)
    dependents = db.execute(
        "SELECT number FROM cues WHERE predecessor_id = ?", (cue_id,)
    ).fetchall()
    if dependents:
        names = "、".join(d["number"] for d in dependents)
        from werkzeug.exceptions import BadRequest
        raise BadRequest(
            description=f"无法删除：提示 {names} 依赖于 {row['number']}，请先修改它们的触发方式"
        )
    db.execute("DELETE FROM cues WHERE id = ?", (cue_id,))
    db.execute("UPDATE shows SET updated_at = datetime('now') WHERE id = ?", (row["show_id"],))
    db.commit()
    return jsonify({"deleted": cue_id})


def _auto_number(show_id):
    prefix = {"light": "L", "sound": "S", "scene": "SC"}
    # Generic fallback numbering Q1, Q2... unique within the show.
    n = 1
    existing = {
        r["number"]
        for r in get_db().execute(
            "SELECT number FROM cues WHERE show_id = ?", (show_id,)
        ).fetchall()
    }
    while f"Q{n}" in existing:
        n += 1
    return f"Q{n}"


# ---------------------------------------------------------------------------
# Import / export
# ---------------------------------------------------------------------------

def _export_show(row):
    cues = [cue_to_dict(r) for r in fetch_cues(row["id"])]
    by_id = {c["id"]: c for c in cues}
    out_cues = []
    for c in cues:
        pred = by_id.get(c["predecessor_id"])
        out_cues.append({
            "number": c["number"],
            "cue_type": c["cue_type"],
            "title": c["title"],
            "notes": c["notes"],
            "start_mode": c["start_mode"],
            "fixed_seconds": c["fixed_seconds"],
            "predecessor_number": pred["number"] if pred else None,
            "delay_seconds": c["delay_seconds"],
            "duration_seconds": c["duration_seconds"],
            "position": c["position"],
        })
    return {
        "name": row["name"],
        "description": row["description"],
        "cues": out_cues,
    }


@app.get("/api/export")
def export_all():
    rows = get_db().execute("SELECT * FROM shows ORDER BY id").fetchall()
    payload = {
        "format": "stagecue-json",
        "version": 1,
        "shows": [_export_show(r) for r in rows],
    }
    resp = jsonify(payload)
    resp.headers["Content-Disposition"] = (
        'attachment; filename="stagecue-shows.json"'
    )
    return resp


@app.get("/api/shows/<int:show_id>/export")
def export_show(show_id):
    row = fetch_show(show_id)
    payload = {
        "format": "stagecue-json",
        "version": 1,
        "shows": [_export_show(row)],
    }
    resp = jsonify(payload)
    safe_name = "".join(ch for ch in row["name"] if ch.isalnum() or ch in "-_") or "show"
    resp.headers["Content-Disposition"] = (
        f'attachment; filename="stagecue-{safe_name}.json"'
    )
    return resp


@app.post("/api/import")
def import_shows():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("导入文件必须是 JSON 对象")
    if data.get("format") not in (None, "stagecue-json"):
        raise ValidationError("无法识别的文件格式（format 字段不匹配）")
    shows = data.get("shows")
    if shows is None and {"name", "cues"} <= set(data.keys()):
        shows = [data]
    if not isinstance(shows, list) or not shows:
        raise ValidationError("JSON 中必须包含非空的 shows 数组")

    db = get_db()
    created = []
    try:
        for idx, show_data in enumerate(shows):
            if not isinstance(show_data, dict):
                raise ValidationError(f"第 {idx + 1} 个演出格式错误")
            show_fields = validate_show_fields(show_data)
            cue_list = show_data.get("cues", [])
            if not isinstance(cue_list, list):
                raise ValidationError("演出的 cues 必须是数组")

            cur = db.execute(
                "INSERT INTO shows (name, description) VALUES (?, ?)",
                (show_fields["name"], show_fields.get("description", "")),
            )
            new_id = cur.lastrowid

            parsed = []
            seen_numbers = set()
            for pos, cue_data in enumerate(cue_list):
                if not isinstance(cue_data, dict):
                    raise ValidationError(f"演出《{show_fields['name']}》第 {pos + 1} 条提示格式错误")
                # Import files reference predecessors by number; inject a
                # placeholder id just to pass field validation, then resolve
                # the real id after every cue is inserted.
                pred_number = cue_data.get("predecessor_number")
                data_for_validation = dict(cue_data)
                if (
                    cue_data.get("start_mode") == "after"
                    and "predecessor_id" not in data_for_validation
                    and isinstance(pred_number, str)
                    and pred_number.strip()
                ):
                    data_for_validation["predecessor_id"] = 1
                fields = validate_cue_fields(data_for_validation)
                if not fields["number"]:
                    fields["number"] = f"Q{pos + 1}"
                if fields["number"] in seen_numbers:
                    raise ValidationError(
                        f"演出《{show_fields['name']}》中提示编号 {fields['number']} 重复",
                        "number",
                    )
                seen_numbers.add(fields["number"])
                if fields["start_mode"] == "after":
                    if not isinstance(pred_number, str) or not pred_number.strip():
                        raise ValidationError(
                            f"提示 {fields['number']} 缺少 predecessor_number",
                            "predecessor_number",
                        )
                fields["predecessor_number"] = pred_number
                fields["position"] = pos
                parsed.append(fields)

            id_by_number = {}
            for f in parsed:
                c = db.execute(
                    """INSERT INTO cues (show_id, number, cue_type, title, notes,
                           start_mode, fixed_seconds, predecessor_id,
                           delay_seconds, duration_seconds, position)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (new_id, f["number"], f["cue_type"], f["title"], f["notes"],
                     f["start_mode"], f["fixed_seconds"], None,
                     f["delay_seconds"], f["duration_seconds"], f["position"]),
                )
                id_by_number[f["number"]] = c.lastrowid

            tentative = []
            for f in parsed:
                pid = None
                if f["start_mode"] == "after":
                    pid = id_by_number.get(f["predecessor_number"])
                    if pid is None:
                        raise ValidationError(
                            f"提示 {f['number']} 的前置提示 "
                            f"{f['predecessor_number']} 在本演出中不存在",
                            "predecessor_number",
                        )
                tentative.append({
                    "id": id_by_number[f["number"]],
                    "number": f["number"],
                    "title": f["title"],
                    "cue_type": f["cue_type"],
                    "start_mode": f["start_mode"],
                    "fixed_seconds": f["fixed_seconds"],
                    "predecessor_id": pid,
                    "delay_seconds": f["delay_seconds"],
                    "duration_seconds": f["duration_seconds"],
                })
                db.execute(
                    "UPDATE cues SET predecessor_id = ? WHERE id = ?",
                    (pid, id_by_number[f["number"]]),
                )
            check_schedule_or_conflict(tentative)
            created.append({"id": new_id, "name": show_fields["name"],
                            "cue_count": len(parsed)})
        db.commit()
    except Exception:
        db.rollback()
        raise
    return jsonify({"imported": created}), 201


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


def create_app():
    """Application factory: initializes schema on boot (container entrypoint)."""
    seed = os.environ.get("STAGECUE_SEED", "1") not in ("0", "false", "False", "")
    init_db(seed=seed)
    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
