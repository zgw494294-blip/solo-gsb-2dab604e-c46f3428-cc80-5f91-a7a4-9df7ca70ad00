/*
 * Client-side mirror of app/scheduler.py.
 * Computes cue start/end times from fixed times or "after another cue"
 * dependencies, and detects dependency cycles with the full chain.
 */
(function (global) {
  "use strict";

  class CycleError extends Error {
    constructor(chain) {
      super("依赖循环：" + chain.map((c) => c.number).join(" -> "));
      this.chain = chain;
    }
  }
  class DependencyError extends Error {
    constructor(cue, predecessorId) {
      super(`提示 ${cue.number} 未选择有效的前置提示 (id=${predecessorId})`);
      this.cue = cue;
      this.predecessorId = predecessorId;
    }
  }

  /**
   * @param {Array} cues cue objects with id/number/title/start_mode/
   *   fixed_seconds/predecessor_id/delay_seconds/duration_seconds
   * @returns {Object} map id -> {start, end}
   */
  function computeSchedule(cues) {
    const byId = new Map(cues.map((c) => [c.id, c]));
    const result = new Map();
    // 0=unvisited, 1=on-stack, 2=done
    const color = new Map(cues.map((c) => [c.id, 0]));

    function resolve(cue, path) {
      const cid = cue.id;
      if (color.get(cid) === 2) {
        return result.get(cid) || { start: null, end: null };
      }
      if (color.get(cid) === 1) {
        const start = path.indexOf(cid);
        const chain = path.slice(start).map((id) => byId.get(id));
        chain.push(cue);
        throw new CycleError(chain);
      }

      color.set(cid, 1);
      path.push(cid);

      let start;
      if (cue.start_mode === "fixed") {
        start = cue.fixed_seconds === null || cue.fixed_seconds === ""
          ? null
          : Number(cue.fixed_seconds);
      } else {
        const pred = byId.get(cue.predecessor_id);
        if (!pred) {
          path.pop();
          color.set(cid, 2);
          result.set(cid, { start: null, end: null });
          throw new DependencyError(cue, cue.predecessor_id);
        }
        const predTimes = resolve(pred, path);
        start = predTimes.end === null
          ? null
          : predTimes.end + Number(cue.delay_seconds || 0);
      }

      path.pop();
      color.set(cid, 2);

      if (start === null || Number.isNaN(start)) {
        result.set(cid, { start: null, end: null });
      } else {
        const s = Number(start);
        result.set(cid, {
          start: s,
          end: s + Math.max(0, Number(cue.duration_seconds)),
        });
      }
      return result.get(cid);
    }

    for (const cue of cues) {
      resolve(cue, []);
    }
    return result;
  }

  /** Try computing; return {schedule, cycle}. Never throws. */
  function safeSchedule(cues) {
    try {
      return { schedule: computeSchedule(cues), cycle: null };
    } catch (err) {
      if (err instanceof CycleError) {
        return { schedule: null, cycle: err };
      }
      return { schedule: null, cycle: err };
    }
  }

  /** mm:ss[.d] */
  function formatTime(seconds) {
    if (seconds === null || seconds === undefined || Number.isNaN(seconds)) {
      return "—";
    }
    const rounded = Math.round(seconds * 10) / 10;
    const m = Math.floor(rounded / 60);
    let s = rounded - m * 60;
    const frac = Math.round((s - Math.floor(s)) * 10);
    s = Math.floor(s);
    const ss = String(s).padStart(2, "0");
    return frac ? `${m}:${ss}.${frac}` : `${m}:${ss}`;
  }

  function typeLabel(t) {
    return { light: "灯光", sound: "音效", scene: "换景" }[t] || t;
  }

  global.StageScheduler = {
    CycleError,
    DependencyError,
    computeSchedule,
    safeSchedule,
    formatTime,
    typeLabel,
  };
})(window);
