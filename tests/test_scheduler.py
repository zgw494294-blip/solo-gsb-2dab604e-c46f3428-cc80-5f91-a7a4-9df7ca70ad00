"""调度算法单元测试：python -m unittest discover -s tests"""

import unittest

from app.scheduler import (
    CycleError,
    assert_acyclic,
    compute_schedule,
    find_cycles,
)


def cue(cid, mode="fixed", fixed_at=None, depends_on=None, delay=0,
        duration=0, name=None):
    return {
        "id": cid, "name": name or f"c{cid}", "mode": mode,
        "fixed_at": fixed_at, "depends_on": depends_on,
        "delay": delay, "duration": duration,
    }


class TestSchedule(unittest.TestCase):

    def test_fixed_and_chain(self):
        cues = [
            cue(1, "fixed", fixed_at=10, duration=5),
            cue(2, "after", depends_on=1, delay=2, duration=3),
            cue(3, "after", depends_on=2, delay=0, duration=4),
        ]
        s = compute_schedule(cues)
        self.assertEqual(s["items"]["1"]["start"], 10.0)
        self.assertEqual(s["items"]["1"]["end"], 15.0)
        self.assertEqual(s["items"]["2"]["start"], 17.0)   # 15 + 2
        self.assertEqual(s["items"]["2"]["end"], 20.0)
        self.assertEqual(s["items"]["3"]["start"], 20.0)
        self.assertEqual(s["items"]["3"]["end"], 24.0)
        self.assertEqual(s["max_end"], 24.0)
        self.assertEqual(s["cycles"], [])

    def test_diamond_dependency(self):
        # 两个后继并行指向同一个会合提示
        cues = [
            cue(1, "fixed", fixed_at=0, duration=10),
            cue(2, "after", depends_on=1, duration=5),
            cue(3, "after", depends_on=1, duration=20),
            cue(4, "after", depends_on=2, delay=0, duration=1),
            cue(5, "after", depends_on=3, duration=1),
        ]
        s = compute_schedule(cues)
        self.assertEqual(s["items"]["2"]["end"], 15.0)
        self.assertEqual(s["items"]["3"]["end"], 30.0)
        self.assertEqual(s["items"]["4"]["start"], 15.0)
        self.assertEqual(s["items"]["5"]["start"], 30.0)

    def test_unanchored_chain(self):
        cues = [
            cue(1, "after", depends_on=2, duration=1),
            cue(2, "after", depends_on=3, duration=1),
            cue(3, "after", depends_on=99, duration=1),  # 指向不存在的提示
        ]
        s = compute_schedule(cues)
        for cid in ("1", "2", "3"):
            self.assertEqual(s["items"][cid]["state"], "unanchored")
            self.assertIsNone(s["items"][cid]["start"])
        self.assertIsNone(s["max_end"])

    def test_simple_self_loop(self):
        cues = [cue(1, "after", depends_on=1, duration=1)]
        chains = find_cycles(cues)
        self.assertEqual(chains, [[1, 1]])
        with self.assertRaises(CycleError) as ctx:
            assert_acyclic(cues)
        self.assertEqual(ctx.exception.chains, [[1, 1]])

    def test_three_node_cycle_full_chain(self):
        cues = [
            cue(1, "after", depends_on=3),
            cue(2, "after", depends_on=1),
            cue(3, "after", depends_on=2),
        ]
        chains = find_cycles(cues)
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0][0], chains[0][-1])  # 首尾相连
        self.assertEqual(set(chains[0][:-1]), {1, 2, 3})

    def test_multiple_independent_cycles(self):
        cues = [
            cue(1, "after", depends_on=2),
            cue(2, "after", depends_on=1),
            cue(3, "fixed", fixed_at=0),
            cue(4, "after", depends_on=5),
            cue(5, "after", depends_on=4),
        ]
        chains = find_cycles(cues)
        self.assertEqual(len(chains), 2)
        s = compute_schedule(cues)
        self.assertEqual(s["items"]["3"]["state"], "ok")
        for cid in ("1", "2", "4", "5"):
            self.assertEqual(s["items"][cid]["state"], "cycle")

    def test_cycle_plus_dependent_marks_cycle(self):
        # 4 依赖环上节点，自身虽不在环里，也无法排时
        cues = [
            cue(1, "after", depends_on=2),
            cue(2, "after", depends_on=1),
            cue(4, "after", depends_on=1, delay=3, duration=2),
        ]
        s = compute_schedule(cues)
        self.assertEqual(s["items"]["4"]["state"], "cycle")
        self.assertIsNone(s["items"]["4"]["start"])

    def test_modifying_duration_cascades(self):
        cues = [
            cue(1, "fixed", fixed_at=0, duration=10),
            cue(2, "after", depends_on=1, duration=0),
        ]
        self.assertEqual(compute_schedule(cues)["items"]["2"]["start"], 10.0)
        cues[0]["duration"] = 25
        self.assertEqual(compute_schedule(cues)["items"]["2"]["start"], 25.0)

    def test_moving_fixed_cue_cascades(self):
        cues = [
            cue(1, "fixed", fixed_at=100, duration=1),
            cue(2, "after", depends_on=1, delay=5, duration=1),
        ]
        self.assertEqual(compute_schedule(cues)["items"]["2"]["start"], 106.0)
        cues[0]["fixed_at"] = 0
        self.assertEqual(compute_schedule(cues)["items"]["2"]["start"], 6.0)

    def test_empty(self):
        s = compute_schedule([])
        self.assertEqual(s["items"], {})
        self.assertIsNone(s["max_end"])


if __name__ == "__main__":
    unittest.main()
