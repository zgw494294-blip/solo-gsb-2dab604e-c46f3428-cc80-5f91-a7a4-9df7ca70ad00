"""端到端 API 冒烟测试：python tests/smoke_api.py"""

import os
import tempfile

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "smoke.db")

from app import create_app
from app.db import init_db

app = create_app()
with app.app_context():
    init_db(seed=True)

client = app.test_client()


def must(resp, code=200):
    assert resp.status_code == code, f"{resp.status_code}: {resp.get_data(as_text=True)}"
    return resp.get_json()


# 1. 列出演出示例
shows = must(client.get("/api/shows"))["shows"]
assert len(shows) >= 1
sid = shows[0]["id"]

# 2. 新建演出 + 三个提示：A 固定 0s/10s，B after A delay 0，C after B delay 2
s = must(client.post("/api/shows", json={"name": "冒烟场"}), 201)
sid = s["show"]["id"]
a = must(client.post(f"/api/shows/{sid}/cues", json={
    "name": "A", "cue_type": "lighting", "mode": "fixed",
    "fixed_at": 0, "duration": 10, "position": 0}), 201)
aid = a["cue"]["id"]
b = must(client.post(f"/api/shows/{sid}/cues", json={
    "name": "B", "cue_type": "sound", "mode": "after",
    "depends_on": aid, "delay": 0, "duration": 5, "position": 1}), 201)
bid = b["cue"]["id"]
c = must(client.post(f"/api/shows/{sid}/cues", json={
    "name": "C", "cue_type": "scene", "mode": "after",
    "depends_on": bid, "delay": 2, "duration": 1, "position": 2}), 201)
cid = c["cue"]["id"]

detail = must(client.get(f"/api/shows/{sid}"))
assert detail["schedule"]["items"][str(aid)]["start"] == 0
assert detail["schedule"]["items"][str(bid)]["start"] == 10
assert detail["schedule"]["items"][str(cid)]["start"] == 17  # A 10 + B 5 + 延迟 2
print("1) 基础链式时间 OK:", {k: (v["start"], v["end"]) for k, v in detail["schedule"]["items"].items()})

# 3. 拖动 A 到 100s → B 110、C 117
detail = must(client.put(f"/api/cues/{aid}", json={
    "name": "A", "cue_type": "lighting", "mode": "fixed",
    "fixed_at": 100, "duration": 10, "position": 0}))
assert detail["schedule"]["items"][str(bid)]["start"] == 110
assert detail["schedule"]["items"][str(cid)]["start"] == 117
print("2) 固定提示移动后联动 OK")

# 4. 改 B 时长 5 -> 20 → C 起点变为 132
detail = must(client.put(f"/api/cues/{bid}", json={
    "name": "B", "cue_type": "sound", "mode": "after",
    "depends_on": aid, "delay": 0, "duration": 20, "position": 1}))
assert detail["schedule"]["items"][str(cid)]["start"] == 132
print("3) 修改时长后继联动 OK, C.start =", detail["schedule"]["items"][str(cid)]["start"])

# 5. 制造循环：让 A 依赖 C → 应 409 且返回完整链，数据不变
r = client.put(f"/api/cues/{aid}", json={
    "name": "A", "cue_type": "lighting", "mode": "after",
    "depends_on": cid, "delay": 0, "duration": 10, "position": 0})
assert r.status_code == 409, r.status_code
body = r.get_json()
assert body["cycles"], body
chain = body["cycles"][0]
assert chain[0] == chain[-1] and set(chain[:-1]) == {aid, bid, cid}
print("4) 循环依赖被拒绝 (409)，完整冲突链:",
      " → ".join(n["name"] for n in body["conflict_chains"][0]))

# 6. 数据未被破坏，A 仍是 fixed@100
detail = must(client.get(f"/api/shows/{sid}"))
assert detail["cues"][0]["mode"] == "fixed" and detail["cues"][0]["fixed_at"] == 100
print("5) 保存失败后原数据保持不变 OK")

# 7. 非法输入
assert client.post(f"/api/shows/{sid}/cues", json={"name": ""}).status_code == 400
assert client.put(f"/api/cues/{bid}", json={
    "name": "B", "mode": "after", "depends_on": bid, "duration": 1}).status_code == 400
print("6) 参数校验 OK")

# 8. 删除被依赖的提示 → 409 + dependents
r = client.delete(f"/api/cues/{bid}")
assert r.status_code == 409 and r.get_json()["dependents"][0]["id"] == cid
# 删除叶子 C → 200
must(client.delete(f"/api/cues/{cid}"))
print("7) 删除保护与叶子删除 OK")

# 9. 导出 -> 再导入（含环文件应被拒）
exported = must(client.get(f"/api/shows/{sid}/export"))
assert exported["format"] == "stagecue/v1"
assert any(q["depends_on_index"] is not None for q in exported["cues"])
imp = must(client.post("/api/import", json=exported), 201)
assert len(imp["cues"]) == len(exported["cues"])
assert imp["schedule"]["max_end"] is not None
print("8) 导出/导入往返 OK, 新演出 id =", imp["show"]["id"], "max_end =", imp["schedule"]["max_end"])

bad = {"show": {"name": "坏数据"}, "cues": [
    {"name": "x", "mode": "after", "depends_on_index": 1, "duration": 1},
    {"name": "y", "mode": "after", "depends_on_index": 0, "duration": 1},
]}
r = client.post("/api/import", json=bad)
assert r.status_code == 409 and len(r.get_json()["cycles"]) == 1
print("9) 含环导入被拒绝 (409) OK")

# 10. 健康检查与首页
assert must(client.get("/api/health"))["ok"] is True
assert client.get("/").status_code == 200
assert b"app.js" in client.get("/").data
print("10) 健康检查与首页 OK")

print("\n全部冒烟断言通过 ✔")
