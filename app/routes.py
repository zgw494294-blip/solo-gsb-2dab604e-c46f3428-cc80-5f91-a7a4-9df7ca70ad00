"""HTTP 路由：页面与 JSON API。"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from flask import Blueprint, jsonify, request, send_from_directory

from . import ApiError
from .db import get_db
from .scheduler import CycleError, assert_acyclic, compute_schedule

pages = Blueprint("pages", __name__)
api = Blueprint("api", __name__)

CUE_TYPES = {"lighting", "sound", "scene"}
MODES = {"fixed", "after"}

CUE_FIELDS = ("position", "cue_type", "name", "note", "mode", "fixed_at",
              "depends_on", "delay", "duration")


# --------------------------------------------------------------------------
# 页面
# --------------------------------------------------------------------------

@pages.get("/")
def index():
    return send_from_directory("static", "index.html")


@api.get("/health")
def health():
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _num(data: dict, key: str, *, required: bool = False,
         default: float = 0.0, minimum: float = 0.0) -> float:
    """从 JSON 取非负数；布尔、NaN、字符串一律拒绝。"""
    if key not in data or data[key] is None:
        if required:
            raise ApiError(400, f"缺少字段：{key}")
        return default
    val = data[key]
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise ApiError(400, f"字段 {key} 必须是数字")
    val = float(val)
    if math.isnan(val) or math.isinf(val):
        raise ApiError(400, f"字段 {key} 不能是 NaN 或无穷大")
    if val < minimum:
        raise ApiError(400, f"字段 {key} 不能小于 {minimum:g}")
    return val


def _text(data: dict, key: str, *, required: bool = False,
          default: str = "", max_len: int = 200) -> str:
    val = data.get(key, default)
    if val is None:
        val = default
    if not isinstance(val, str):
        raise ApiError(400, f"字段 {key} 必须是字符串")
    val = val.strip()
    if required and not val:
        raise ApiError(400, f"字段 {key} 不能为空")
    if len(val) > max_len:
        raise ApiError(400, f"字段 {key} 长度不能超过 {max_len}")
    return val


def _cue_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "show_id": row["show_id"],
        "position": row["position"],
        "cue_type": row["cue_type"],
        "name": row["name"],
        "note": row["note"],
        "mode": row["mode"],
        "fixed_at": row["fixed_at"],
        "depends_on": row["depends_on"],
        "delay": row["delay"],
        "duration": row["duration"],
    }


def _get_show_or_404(show_id: int) -> sqlite3.Row:
    row = get_db().execute("SELECT * FROM show WHERE id = ?", (show_id,)).fetchone()
    if row is None:
        raise ApiError(404, "演出不存在")
    return row


def _show_cues(show_id: int) -> List[sqlite3.Row]:
    return get_db().execute(
        "SELECT * FROM cue WHERE show_id = ? ORDER BY position, id", (show_id,)
    ).fetchall()


def _payload_to_cue_fields(data: dict, *, require_after_dep: bool = True) -> Dict[str, Any]:
    """校验并归一化单个提示的可写字段。不做图层面（依赖/环）校验。

    导入流程按数组下标解析依赖，因此用 ``require_after_dep=False``
    跳过 depends_on 字段要求，自行回填。
    """
    if not isinstance(data, dict):
        raise ApiError(400, "请求体必须是 JSON 对象")

    cue_type = data.get("cue_type", "lighting")
    if cue_type not in CUE_TYPES:
        raise ApiError(400, "cue_type 必须是 lighting / sound / scene 之一")

    mode = data.get("mode", "fixed")
    if mode not in MODES:
        raise ApiError(400, "mode 必须是 fixed / after 之一")

    fields: Dict[str, Any] = {
        "cue_type": cue_type,
        "name": _text(data, "name", required=True, max_len=200),
        "note": _text(data, "note", max_len=2000),
        "mode": mode,
        "duration": _num(data, "duration", default=0.0),
    }
    pos = data.get("position")
    if pos is not None:
        if isinstance(pos, bool) or not isinstance(pos, int) or pos < 0:
            raise ApiError(400, "position 必须是非负整数")
        fields["position"] = pos

    if mode == "fixed":
        fields["fixed_at"] = _num(data, "fixed_at", required=True)
        fields["depends_on"] = None
        fields["delay"] = 0.0
    else:
        fields["fixed_at"] = None
        fields["delay"] = _num(data, "delay", default=0.0)
        dep = data.get("depends_on")
        if dep is None or isinstance(dep, bool) or not isinstance(dep, int):
            if require_after_dep:
                raise ApiError(400, "after 模式必须提供整数 depends_on")
            fields["depends_on"] = None
        else:
            fields["depends_on"] = dep
    return fields


def _validate_graph(cues: List[Dict[str, Any]]) -> None:
    """对整组提示跑环检测，有环则以 409 返回完整冲突链。

    保存被拒绝，数据库不做任何修改；前端保留用户当前编辑内容。
    """
    try:
        assert_acyclic(cues)
    except CycleError as exc:
        by_id = {int(c["id"]): c for c in cues}
        readable = [
            [{"id": cid, "name": by_id.get(cid, {}).get("name", str(cid))}
             for cid in chain]
            for chain in exc.chains
        ]
        raise ApiError(
            409, "存在循环依赖，无法保存",
            {"cycles": exc.chains, "conflict_chains": readable},
        )


def _touch_show(show_id: int) -> None:
    get_db().execute(
        "UPDATE show SET updated_at = ? WHERE id = ?", (_now(), show_id)
    )


def _show_detail(show_id: int) -> Tuple[sqlite3.Row, Dict[str, Any]]:
    show = _get_show_or_404(show_id)
    cue_rows = _show_cues(show_id)
    cues = [_cue_to_dict(r) for r in cue_rows]
    schedule = compute_schedule(cues)
    return show, {"show": dict(show), "cues": cues, "schedule": schedule}


# --------------------------------------------------------------------------
# 演出
# --------------------------------------------------------------------------

@api.get("/shows")
def list_shows():
    rows = get_db().execute(
        "SELECT s.*, COUNT(c.id) AS cue_count FROM show s"
        " LEFT JOIN cue c ON c.show_id = s.id"
        " GROUP BY s.id ORDER BY s.updated_at DESC, s.id DESC"
    ).fetchall()
    return jsonify({"shows": [
        {"id": r["id"], "name": r["name"], "description": r["description"],
         "created_at": r["created_at"], "updated_at": r["updated_at"],
         "cue_count": r["cue_count"]}
        for r in rows
    ]})


@api.post("/shows")
def create_show():
    data = request.get_json(silent=True) or {}
    name = _text(data, "name", required=True, max_len=200) or "未命名演出"
    desc = _text(data, "description", max_len=2000)
    now = _now()
    db = get_db()
    cur = db.execute(
        "INSERT INTO show (name, description, created_at, updated_at)"
        " VALUES (?, ?, ?, ?)", (name, desc, now, now),
    )
    db.commit()
    show = db.execute("SELECT * FROM show WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify({"show": dict(show), "cues": [],
                    "schedule": {"items": {}, "cycles": [], "max_end": None}}), 201


@api.get("/shows/<int:show_id>")
def get_show(show_id: int):
    _, detail = _show_detail(show_id)
    return jsonify(detail)


@api.put("/shows/<int:show_id>")
def update_show(show_id: int):
    _get_show_or_404(show_id)
    data = request.get_json(silent=True) or {}
    name = _text(data, "name", required=True, max_len=200)
    desc = _text(data, "description", max_len=2000)
    db = get_db()
    db.execute("UPDATE show SET name = ?, description = ?, updated_at = ? WHERE id = ?",
               (name, desc, _now(), show_id))
    db.commit()
    return jsonify({"ok": True})


@api.delete("/shows/<int:show_id>")
def delete_show(show_id: int):
    _get_show_or_404(show_id)
    db = get_db()
    db.execute("DELETE FROM show WHERE id = ?", (show_id,))
    db.commit()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# 提示
# --------------------------------------------------------------------------

def _load_cues_as_dicts(show_id: int) -> List[Dict[str, Any]]:
    return [_cue_to_dict(r) for r in _show_cues(show_id)]


@api.post("/shows/<int:show_id>/cues")
def create_cue(show_id: int):
    _get_show_or_404(show_id)
    fields = _payload_to_cue_fields(request.get_json(silent=True) or {})
    db = get_db()

    if "position" not in fields:
        row = db.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM cue WHERE show_id = ?",
            (show_id,),
        ).fetchone()
        fields["position"] = row["p"]

    if fields["mode"] == "after":
        dep = db.execute(
            "SELECT id FROM cue WHERE id = ? AND show_id = ?",
            (fields["depends_on"], show_id),
        ).fetchone()
        if dep is None:
            raise ApiError(400, "depends_on 必须指向本演出中的提示")

    # 用临时负数 id 参与环检测，保证保存前图一定无环。
    tmp_id = -1
    candidate = {
        "id": tmp_id, "name": fields["name"], "mode": fields["mode"],
        "fixed_at": fields["fixed_at"],
        "depends_on": fields["depends_on"],
        "delay": fields["delay"], "duration": fields["duration"],
    }
    cues = _load_cues_as_dicts(show_id) + [candidate]
    _validate_graph(cues)

    cur = db.execute(
        "INSERT INTO cue (show_id, position, cue_type, name, note, mode,"
        " fixed_at, depends_on, delay, duration)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (show_id, fields["position"], fields["cue_type"], fields["name"],
         fields["note"], fields["mode"], fields["fixed_at"],
         fields["depends_on"], fields["delay"], fields["duration"]),
    )
    _touch_show(show_id)
    db.commit()
    row = db.execute("SELECT * FROM cue WHERE id = ?", (cur.lastrowid,)).fetchone()
    _, detail = _show_detail(show_id)
    return jsonify({"cue": _cue_to_dict(row), **detail}), 201


@api.put("/cues/<int:cue_id>")
def update_cue(cue_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM cue WHERE id = ?", (cue_id,)).fetchone()
    if row is None:
        raise ApiError(404, "提示不存在")
    show_id = row["show_id"]
    fields = _payload_to_cue_fields(request.get_json(silent=True) or {})

    if "position" not in fields:
        fields["position"] = row["position"]

    if fields["mode"] == "after":
        if fields["depends_on"] == cue_id:
            raise ApiError(400, "提示不能依赖自身")
        dep = db.execute(
            "SELECT id FROM cue WHERE id = ? AND show_id = ?",
            (fields["depends_on"], show_id),
        ).fetchone()
        if dep is None:
            raise ApiError(400, "depends_on 必须指向本演出中的提示")

    # 在内存中的整组提示上替换该提示后再做环检测，有环则整笔拒绝。
    cues = _load_cues_as_dicts(show_id)
    for cue in cues:
        if cue["id"] == cue_id:
            cue.update({k: fields[k] for k in CUE_FIELDS if k in fields})
            break
    _validate_graph(cues)

    db.execute(
        "UPDATE cue SET position = ?, cue_type = ?, name = ?, note = ?,"
        " mode = ?, fixed_at = ?, depends_on = ?, delay = ?, duration = ?"
        " WHERE id = ?",
        (fields["position"], fields["cue_type"], fields["name"],
         fields["note"], fields["mode"], fields["fixed_at"],
         fields["depends_on"], fields["delay"], fields["duration"], cue_id),
    )
    _touch_show(show_id)
    db.commit()
    _, detail = _show_detail(show_id)
    return jsonify(detail)


@api.delete("/cues/<int:cue_id>")
def delete_cue(cue_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM cue WHERE id = ?", (cue_id,)).fetchone()
    if row is None:
        raise ApiError(404, "提示不存在")
    show_id = row["show_id"]

    dependents = db.execute(
        "SELECT id, name FROM cue WHERE depends_on = ? ORDER BY position, id",
        (cue_id,),
    ).fetchall()
    if dependents:
        raise ApiError(
            409,
            f"该提示仍被 {len(dependents)} 个提示依赖，请先改派它们的依赖后再删除",
            {"dependents": [{"id": r["id"], "name": r["name"]} for r in dependents]},
        )

    db.execute("DELETE FROM cue WHERE id = ?", (cue_id,))
    _touch_show(show_id)
    db.commit()
    _, detail = _show_detail(show_id)
    return jsonify(detail)


# --------------------------------------------------------------------------
# 导入 / 导出
# --------------------------------------------------------------------------

def _export_cue(cue: Dict[str, Any], index_by_id: Dict[int, int]) -> Dict[str, Any]:
    return {
        "position": cue["position"],
        "cue_type": cue["cue_type"],
        "name": cue["name"],
        "note": cue["note"],
        "mode": cue["mode"],
        "fixed_at": cue["fixed_at"],
        "depends_on_index": (index_by_id[cue["depends_on"]]
                             if cue["mode"] == "after" and cue["depends_on"] is not None
                             else None),
        "delay": cue["delay"],
        "duration": cue["duration"],
    }


@api.get("/shows/<int:show_id>/export")
def export_show(show_id: int):
    show = _get_show_or_404(show_id)
    cues = [_cue_to_dict(r) for r in _show_cues(show_id)]
    index_by_id = {c["id"]: i for i, c in enumerate(cues)}
    payload = {
        "format": "stagecue/v1",
        "exported_at": _now(),
        "show": {"name": show["name"], "description": show["description"]},
        "cues": [_export_cue(c, index_by_id) for c in cues],
    }
    response = jsonify(payload)
    response.headers["Content-Disposition"] = (
        f'attachment; filename="show-{show_id}.json"'
    )
    return response


@api.post("/import")
def import_show():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("cues"), list):
        raise ApiError(400, "导入文件格式不正确：需要包含 cues 数组的 JSON 对象")
    if len(data["cues"]) > 1000:
        raise ApiError(400, "一次最多导入 1000 个提示")

    show_info = data.get("show") if isinstance(data.get("show"), dict) else {}
    name = _text(show_info, "name", default="", max_len=200)
    if not name:
        name = _text(data, "name", default="导入的演出", max_len=200)
    desc = _text(show_info, "description", default="", max_len=2000)

    raw_cues = data["cues"]
    # 兼容两种依赖写法：
    #   depends_on_index —— 本格式的可移植数组下标（推荐）；
    #   depends_on + id  —— 导出数组里带上原始 id 时按下标映射。
    parsed: List[Dict[str, Any]] = []
    for i, raw in enumerate(raw_cues):
        if not isinstance(raw, dict):
            raise ApiError(400, f"第 {i + 1} 个提示不是 JSON 对象")
        try:
            fields = _payload_to_cue_fields(raw, require_after_dep=False)
        except ApiError as err:
            raise ApiError(400, f"第 {i + 1} 个提示：{err.message}")

        dep_idx: Optional[int] = None
        if fields["mode"] == "after":
            if "depends_on_index" in raw and raw["depends_on_index"] is not None:
                dep_idx = raw["depends_on_index"]
                if isinstance(dep_idx, bool) or not isinstance(dep_idx, int):
                    raise ApiError(400, f"第 {i + 1} 个提示：depends_on_index 必须是整数")
            elif raw.get("depends_on") is not None and "id" in raw:
                # 仅在每个提示都带 id 时才允许旧 id 引用。
                old_id = raw["depends_on"]
                if not isinstance(old_id, int) or isinstance(old_id, bool):
                    raise ApiError(400, f"第 {i + 1} 个提示：depends_on 必须是整数")
                id_to_index: Dict[int, int] = {}
                ok = True
                for j, r in enumerate(raw_cues):
                    if not isinstance(r, dict) or not isinstance(r.get("id"), int) \
                            or isinstance(r.get("id"), bool):
                        ok = False
                        break
                    id_to_index[r["id"]] = j
                if not ok:
                    raise ApiError(400, f"第 {i + 1} 个提示：无法按旧 id 解析依赖，请改用 depends_on_index")
                if old_id not in id_to_index:
                    raise ApiError(400, f"第 {i + 1} 个提示：依赖的提示不存在于文件中")
                dep_idx = id_to_index[old_id]
            else:
                raise ApiError(400, f"第 {i + 1} 个提示：after 模式缺少 depends_on_index")
            if not (0 <= dep_idx < len(raw_cues)):
                raise ApiError(400, f"第 {i + 1} 个提示：depends_on_index 越界")

        fields["position"] = fields.get("position")
        if fields["position"] is None:
            fields["position"] = i
        fields["_dep_idx"] = dep_idx
        parsed.append(fields)

    # 用数组下标作为临时 id 做图校验：id=下标，depends_on=依赖项下标。
    candidate = [{
        "id": i,
        "mode": f["mode"],
        "fixed_at": f["fixed_at"],
        "depends_on": f["_dep_idx"] if f["mode"] == "after" else None,
        "delay": f["delay"],
        "duration": f["duration"],
    } for i, f in enumerate(parsed)]
    try:
        assert_acyclic(candidate)
    except CycleError as exc:
        by_id = {i: f["name"] for i, f in enumerate(parsed)}
        readable = [[{"index": cid, "name": by_id.get(cid, str(cid))}
                     for cid in chain] for chain in exc.chains]
        raise ApiError(409, "导入文件中存在循环依赖，已取消导入",
                       {"cycles": exc.chains, "conflict_chains": readable})

    db = get_db()
    now = _now()
    cur = db.execute(
        "INSERT INTO show (name, description, created_at, updated_at)"
        " VALUES (?, ?, ?, ?)", (name, desc, now, now),
    )
    show_id = cur.lastrowid
    new_ids: List[int] = []
    # 两遍插入：先建全部提示，再回填 depends_on，允许后插入的提示被先插入的引用。
    for i, f in enumerate(parsed):
        r = db.execute(
            "INSERT INTO cue (show_id, position, cue_type, name, note, mode,"
            " fixed_at, depends_on, delay, duration)"
            " VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)",
            (show_id, f["position"], f["cue_type"], f["name"], f["note"],
             f["mode"], f["delay"], f["duration"]),
        )
        new_ids.append(r.lastrowid)
        if f["mode"] == "fixed":
            db.execute("UPDATE cue SET fixed_at = ? WHERE id = ?",
                       (f["fixed_at"], r.lastrowid))
    for i, f in enumerate(parsed):
        if f["mode"] == "after":
            db.execute("UPDATE cue SET depends_on = ?, delay = ? WHERE id = ?",
                       (new_ids[f["_dep_idx"]], f["delay"], new_ids[i]))
    db.commit()

    _, detail = _show_detail(show_id)
    return jsonify(detail), 201
