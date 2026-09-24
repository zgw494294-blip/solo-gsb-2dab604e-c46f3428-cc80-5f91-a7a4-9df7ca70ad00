"""End-to-end API smoke tests (run locally; not shipped in the image)."""
import json
import os
import tempfile

tmp = tempfile.mkdtemp()
os.environ["STAGECUE_DB"] = os.path.join(tmp, "test.db")
os.environ["STAGECUE_SEED"] = "0"

from app.server import create_app

app = create_app()
c = app.test_client()

fails = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name} {detail}")
    if not cond:
        fails.append(name)


# --- seed list is empty
r = c.get("/api/shows")
check("empty show list", r.status_code == 200 and r.get_json() == [])

# --- create show
r = c.post("/api/shows", json={"name": "测试演出", "description": "d"})
check("create show", r.status_code == 201)
sid = r.get_json()["id"]

def add_cue(payload):
    return c.post(f"/api/shows/{sid}/cues", json=payload)

def update_cue(cid, payload):
    return c.put(f"/api/cues/{cid}", json=payload)

# L1 fixed at 0, duration 30
r = add_cue({"number": "L1", "cue_type": "light", "title": "暖光",
             "start_mode": "fixed", "fixed_seconds": 0,
             "duration_seconds": 30})
check("add fixed cue L1", r.status_code == 201, r.data[:120].decode())
l1 = r.get_json()["id"]

# S1 after L1 + 0s, duration 60 -> start 30, end 90
r = add_cue({"number": "S1", "cue_type": "sound", "title": "音乐",
             "start_mode": "after", "predecessor_id": l1,
             "delay_seconds": 0, "duration_seconds": 60})
s1 = r.get_json()["id"]
check("add after cue S1", r.status_code == 201, r.data[:120].decode())

# L2 after S1 + 5 -> start 95
r = add_cue({"number": "L2", "cue_type": "light", "title": "蓝光",
             "start_mode": "after", "predecessor_id": s1,
             "delay_seconds": 5, "duration_seconds": 10})
l2 = r.get_json()["id"]

r = c.get(f"/api/shows/{sid}")
data = r.get_json()
times = {x["number"]: (x["start"], x["end"]) for x in data["cues"]}
check("chain times L1", times["L1"] == (0.0, 30.0), str(times["L1"]))
check("chain times S1", times["S1"] == (30.0, 90.0), str(times["S1"]))
check("chain times L2", times["L2"] == (95.0, 105.0), str(times["L2"]))
check("no cycle", data["cycle"] is None)

# --- drag L1 fixed time from 0 to 100 -> S1 130-190, L2 195-205
r = update_cue(l1, {"number": "L1", "cue_type": "light", "title": "暖光",
                    "start_mode": "fixed", "fixed_seconds": 100,
                    "predecessor_id": None, "delay_seconds": 0,
                    "duration_seconds": 30})
check("drag L1 to 100", r.status_code == 200, r.data[:150].decode())
r = c.get(f"/api/shows/{sid}")
times = {x["number"]: (x["start"], x["end"]) for x in r.get_json()["cues"]}
check("cascade after drag S1", times["S1"] == (130.0, 190.0), str(times["S1"]))
check("cascade after drag L2", times["L2"] == (195.0, 205.0), str(times["L2"]))

# --- resize S1 duration 60 -> 100: L2 start 235
r = update_cue(s1, {"number": "S1", "cue_type": "sound", "title": "音乐",
                    "start_mode": "after", "predecessor_id": l1,
                    "delay_seconds": 0, "duration_seconds": 100})
check("resize S1", r.status_code == 200)
r = c.get(f"/api/shows/{sid}")
times = {x["number"]: (x["start"], x["end"]) for x in r.get_json()["cues"]}
check("cascade after resize L2", times["L2"] == (235.0, 245.0), str(times["L2"]))

# --- create direct cycle L1 -> after L2, rejected 409 with full chain
r = update_cue(l1, {"number": "L1", "cue_type": "light", "title": "暖光",
                    "start_mode": "after", "predecessor_id": l2,
                    "delay_seconds": 0, "duration_seconds": 30})
check("cycle rejected 409", r.status_code == 409, f"got {r.status_code}")
body = r.get_json()
chain_nums = [x["number"] for x in body.get("chain", [])]
expect = {"L1", "S1", "L2"}
check("cycle full chain L1->S1->L2->L1",
      len(chain_nums) == 4 and set(chain_nums[:-1]) == expect
      and chain_nums[0] == chain_nums[-1], str(chain_nums))
check("cycle chain_text present", "L1（暖光）" in body.get("chain_text", ""),
      body.get("chain_text", ""))

# Data must be untouched after rejected save
r = c.get(f"/api/shows/{sid}")
l1row = next(x for x in r.get_json()["cues"] if x["number"] == "L1")
check("data unchanged after rejected cycle",
      l1row["start_mode"] == "fixed" and l1row["fixed_seconds"] == 100,
      str((l1row["start_mode"], l1row["fixed_seconds"])))

# self-loop
r = add_cue({"number": "X", "cue_type": "scene", "title": "t",
             "start_mode": "after", "predecessor_id": 999999,
             "duration_seconds": 1})
check("foreign predecessor rejected", r.status_code == 400)

# missing fixed time
r = add_cue({"number": "Y", "cue_type": "scene", "title": "t",
             "start_mode": "fixed", "duration_seconds": 1})
check("missing fixed rejected", r.status_code == 400)

# bad duration
r = add_cue({"number": "Z", "cue_type": "scene", "title": "t",
             "start_mode": "fixed", "fixed_seconds": 0, "duration_seconds": 0})
check("zero duration rejected", r.status_code == 400)

# duplicate number
r = add_cue({"number": "L1", "cue_type": "scene", "title": "t2",
             "start_mode": "fixed", "fixed_seconds": 1, "duration_seconds": 1})
check("duplicate number rejected", r.status_code == 400)

# --- delete blocked when dependents exist
r = c.delete(f"/api/cues/{s1}")
check("delete blocked with dependents", r.status_code == 400, r.data[:100].decode())

# --- export roundtrip
r = c.get("/api/export")
payload = r.get_json()
check("export format", payload["format"] == "stagecue-json" and len(payload["shows"]) == 1)
check("export predecessor_number",
      payload["shows"][0]["cues"][1]["predecessor_number"] == "L1")

payload["shows"][0]["name"] = "导入副本"
r = c.post("/api/import", json=payload)
check("import ok", r.status_code == 201, r.data[:150].decode())
new_id = r.get_json()["imported"][0]["id"]
r = c.get(f"/api/shows/{new_id}")
check("imported cues scheduled",
      r.get_json()["cycle"] is None and r.get_json()["cues"],
      str(r.get_json()["cycle"]))

# --- import with cyclic file must be fully rolled back
before = len(c.get("/api/shows").get_json())
cyclic = {"format": "stagecue-json", "shows": [{
    "name": "坏演出",
    "cues": [
        {"number": "A", "cue_type": "light", "title": "a", "start_mode": "after",
         "predecessor_number": "B", "duration_seconds": 1},
        {"number": "B", "cue_type": "sound", "title": "b", "start_mode": "after",
         "predecessor_number": "A", "duration_seconds": 1},
    ],
}]}
r = c.post("/api/import", json=cyclic)
check("cyclic import rejected", r.status_code == 409)
after = len(c.get("/api/shows").get_json())
check("cyclic import rolled back", before == after, f"{before} vs {after}")

# --- auto numbering
r = add_cue({"cue_type": "scene", "title": "自动编号",
             "start_mode": "fixed", "fixed_seconds": 0, "duration_seconds": 2})
check("auto number", r.status_code == 201 and r.get_json()["number"] == "Q1",
      r.data[:100].decode())

# --- partial show rename
r = c.put(f"/api/shows/{sid}", json={"name": "改名后的演出"})
check("rename show", r.status_code == 200 and r.get_json()["name"] == "改名后的演出")

# --- delete show cascades cues
r = c.delete(f"/api/shows/{sid}")
check("delete show", r.status_code == 200)

# health
check("healthz", c.get("/healthz").status_code == 200)
check("index served", c.get("/").status_code == 200)

print()
if fails:
    print(f"{len(fails)} FAILURES: {fails}")
    raise SystemExit(1)
print("ALL TESTS PASSED")
