"""
提示时间编排核心算法。

时间模型
--------
每个提示 (cue) 有两种时间模式，互斥：

* ``fixed`` —— 固定开始时间（相对开场，秒，可为小数）。
* ``after`` —— 在另一个提示 *结束* 之后延迟 ``delay`` 秒开始，
  即 ``start(cue) = end(depends_on) + delay``，``end = start + duration``。

依赖图中每条边从被依赖的提示指向依赖它的提示（前驱 -> 后继）。
本模块只做纯计算、不触碰数据库，便于单元测试，也供 HTTP 层与
（镜像实现的）前端共同遵守同一套规则。
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional, Tuple

# 依赖链可能很长（默认递归上限 1000），适度提高以支撑 MAX_CUES 规模。
sys.setrecursionlimit(max(sys.getrecursionlimit(), 4000))

# 单张提示单允许的最大提示数量，也作为环搜索的安全上限。
MAX_CUES = 1000


class CycleError(Exception):
    """保存的提示图存在循环依赖。

    :param chains: 所有检测到的完整冲突链，每条链按依赖方向排列，
        形如 ``[A, B, C, A]``（首尾为同一提示，直观展示环回到自身）。
    """

    def __init__(self, chains: List[List[int]]):
        self.chains = chains
        super().__init__("检测到循环依赖")


def build_edges(cues: List[Dict[str, Any]]) -> Tuple[Dict[int, Dict[str, Any]], Dict[int, List[int]]]:
    """整理提示数据，返回 ``by_id`` 映射与「后继列表」邻接表。"""
    by_id: Dict[int, Dict[str, Any]] = {}
    for cue in cues:
        by_id[int(cue["id"])] = cue

    successors: Dict[int, List[int]] = {cid: [] for cid in by_id}
    for cid, cue in by_id.items():
        dep = cue.get("depends_on")
        if cue.get("mode") == "after" and dep is not None:
            dep = int(dep)
            if dep in by_id:
                successors[dep].append(cid)
    return by_id, successors


def _normalize_chains(chains: List[List[int]]) -> List[List[int]]:
    """对找到的环去重（同一组环边的不同旋转视为同一个环）。"""
    unique: Dict[Tuple[int, ...], List[int]] = {}
    for chain in chains:
        # chain 形如 [A, B, C, A]；取节点环 [A, B, C] 做旋转归一化。
        ring = chain[:-1]
        if not ring:
            continue
        n = len(ring)
        rotations = [tuple(ring[i:] + ring[:i]) for i in range(n)]
        key = min(rotations)
        if key not in unique:
            unique[key] = chain
    return list(unique.values())


def find_cycles(cues: List[Dict[str, Any]]) -> List[List[int]]:
    """返回依赖图中所有的环，每个环为按依赖方向排列的完整冲突链。

    使用带路径快照的 DFS：``stack`` 为当前递归路径，``on_path`` 标记
    路径上的节点。回边 ``u -> v`` 命中路径上的 ``v`` 时，从路径中
    切出 ``v ... u`` 并补回 ``v``，得到完整链（如 ``[v, x, u, v]``）。
    图允许存在多个独立的环，因此不做 visited 全局剪枝，而是通过
    WHITE/GRAY/BLACK 三色标记避免重复展开。
    """
    by_id, successors = build_edges(cues)
    if len(by_id) > MAX_CUES:
        raise ValueError("提示数量超出上限")

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {cid: WHITE for cid in by_id}
    chains: List[List[int]] = []

    def walk(node: int, stack: List[int], on_path: Dict[int, int]) -> None:
        color[node] = GRAY
        on_path[node] = len(stack)
        stack.append(node)
        for succ in successors.get(node, ()):
            if color[succ] == WHITE:
                walk(succ, stack, on_path)
            elif color[succ] == GRAY:
                start = on_path[succ]
                chains.append(stack[start:] + [succ])
        stack.pop()
        del on_path[node]
        color[node] = BLACK

    sys_path: Dict[int, int] = {}
    for cid in by_id:
        if color[cid] == WHITE:
            walk(cid, [], sys_path)

    return _normalize_chains(chains)


def assert_acyclic(cues: List[Dict[str, Any]]) -> None:
    """无环时静默返回；有环时抛出 :class:`CycleError`。"""
    chains = find_cycles(cues)
    if chains:
        raise CycleError(chains)


def _cue_state(cue: Dict[str, Any], by_id: Dict[int, Dict[str, Any]],
               state: Dict[int, str]) -> str:
    """记忆化计算单个提示的编排状态。

    ``ok``        —— 可以算出确定时间；
    ``unanchored`` —— 沿依赖链向上找不到任何固定时间提示，时间无法落地；
    ``cycle``     —— 自身在环上，或（传递）依赖了环上的提示。
    """
    cid = int(cue["id"])
    cached = state.get(cid)
    if cached is not None:
        return cached

    if cue.get("mode") == "fixed":
        state[cid] = "ok"
        return "ok"

    dep = cue.get("depends_on")
    if cue.get("mode") != "after" or dep is None or int(dep) not in by_id:
        # 异常数据：after 模式但依赖缺失，按无法落地处理而不是崩溃。
        state[cid] = "unanchored"
        return "unanchored"

    # 先占位再递归，保证真的出现环时能终止（理论上保存前已拦截，
    # 此分支属于防御性代码）。
    state[cid] = "cycle"
    dep_state = _cue_state(by_id[int(dep)], by_id, state)
    result = dep_state if dep_state != "ok" else "ok"
    state[cid] = result
    return result


def compute_schedule(cues: List[Dict[str, Any]]) -> Dict[str, Any]:
    """计算全部提示的执行时间。

    返回::

        {
          "items": { "<id>": {"state": ok|cycle|unanchored,
                              "start": float|null, "end": float|null,
                              "prev_chain": [id, ...]|null} },
          "cycles": [[id, id, ..., id], ...],
          "max_end": float|null,   # 全部已落地提示的最晚结束时间
        }

    ``prev_chain`` 为该提示沿 depends_on 向上的依赖链（含自身），
    用于界面提示“它的时间由谁决定”；``unanchored`` 提示也会带上链。
    """
    by_id, _ = build_edges(cues)
    cycles = find_cycles(cues)
    cycle_nodes = {cid for chain in cycles for cid in chain}

    states: Dict[int, str] = {}
    for cid, cue in by_id.items():
        if cid in cycle_nodes:
            states[cid] = "cycle"
    for cid, cue in by_id.items():
        _cue_state(cue, by_id, states)

    items: Dict[str, Dict[str, Any]] = {}
    max_end: Optional[float] = None

    for cid, cue in by_id.items():
        st = states[cid]
        if st != "ok":
            items[str(cid)] = {"state": st, "start": None, "end": None,
                               "prev_chain": _prev_chain(cid, by_id, st)}
            continue

        start: float
        if cue.get("mode") == "fixed":
            start = float(cue["fixed_at"])
        else:
            dep = int(cue["depends_on"])
            dep_item = items[str(dep)]
            # 状态为 ok 时依赖必然也已算出确定时间。
            start = float(dep_item["end"]) + float(cue.get("delay") or 0)
        end = start + float(cue.get("duration") or 0)
        if max_end is None or end > max_end:
            max_end = end
        items[str(cid)] = {"state": "ok", "start": round(start, 3),
                           "end": round(end, 3),
                           "prev_chain": _prev_chain(cid, by_id, "ok")}

    return {"items": items, "cycles": cycles,
            "max_end": round(max_end, 3) if max_end is not None else None}


def _prev_chain(cid: int, by_id: Dict[int, Dict[str, Any]],
                st: str) -> Optional[List[int]]:
    """沿 depends_on 回溯依赖链，遇到固定提示 / 缺失 / 环即停止。"""
    if by_id[cid].get("mode") == "fixed":
        return [cid]
    chain: List[int] = []
    seen = set()
    cur = cid
    while True:
        if cur in seen:
            chain.append(cur)  # 标出回到哪个节点形成环
            break
        seen.add(cur)
        chain.append(cur)
        cue = by_id.get(cur)
        if cue is None or cue.get("mode") != "after":
            break
        dep = cue.get("depends_on")
        if dep is None or int(dep) not in by_id:
            break
        cur = int(dep)
        if by_id[cur].get("mode") == "fixed":
            chain.append(cur)
            break
    return chain
