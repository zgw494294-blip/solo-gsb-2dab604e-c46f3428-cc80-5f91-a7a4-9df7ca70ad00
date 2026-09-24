"""Execution-time computation and dependency-cycle detection.

A cue either starts at a *fixed* wall-clock second (measured from the start
of the show) or starts ``delay_seconds`` after another cue *ends*.
End = start + duration.
"""


class CycleError(Exception):
    """Raised when "start after another cue" dependencies form a cycle."""

    def __init__(self, chain):
        # chain: list of cue dicts, first == last cue, describing the full loop
        self.chain = chain
        super().__init__("依赖循环：" + " -> ".join(c["number"] for c in chain))


class DependencyError(Exception):
    """Raised for a reference that points at a non-existent / foreign cue."""

    def __init__(self, cue, predecessor_id):
        self.cue = cue
        self.predecessor_id = predecessor_id
        super().__init__(
            f"提示 {cue['number']} 引用了不存在的前置提示 (id={predecessor_id})"
        )


def describe_chain(chain):
    """Return the full conflict chain as readable text, e.g. 'A -> B -> A'."""
    return " -> ".join(
        f"{c['number']}（{c['title']}）" for c in chain
    )


def compute_schedule(cues):
    """Compute start/end seconds for every cue.

    :param cues: list of dicts with keys id, number, title, cue_type,
                 start_mode, fixed_seconds, predecessor_id, delay_seconds,
                 duration_seconds (extra keys are passed through).
    :return: dict {cue_id: {"start": float|None, "end": float|None}}.
             Unreachable cues (missing predecessor) get None.
    :raises CycleError: with the complete chain when a dependency loop exists.
    """
    by_id = {c["id"]: c for c in cues}
    result = {}

    # Iterative DFS with an explicit path so that, on back-edge, the exact
    # chain participating in the loop can be reported.
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {c["id"]: WHITE for c in cues}

    def resolve(cue, path_ids):
        cid = cue["id"]
        if color[cid] == BLACK:
            return result.get(cid, {"start": None, "end": None})
        if color[cid] == GRAY:
            start = path_ids.index(cid)
            chain = [by_id[i] for i in path_ids[start:]]
            chain.append(cue)
            raise CycleError(chain)

        color[cid] = GRAY
        path_ids.append(cid)

        if cue["start_mode"] == "fixed":
            start = _num(cue["fixed_seconds"])
        else:
            pid = cue["predecessor_id"]
            pred = by_id.get(pid)
            if pred is None:
                raise DependencyError(cue, pid)
            pred_times = resolve(pred, path_ids)
            if pred_times["end"] is None:
                start = None
            else:
                start = pred_times["end"] + _num(cue["delay_seconds"])

        path_ids.pop()
        color[cid] = BLACK

        if start is None:
            result[cid] = {"start": None, "end": None}
        else:
            start = float(start)
            result[cid] = {
                "start": start,
                "end": start + max(0.0, float(cue["duration_seconds"])),
            }
        return result[cid]

    for cue in cues:
        resolve(cue, [])
    return result


def _num(value):
    if value is None:
        return None
    return float(value)
