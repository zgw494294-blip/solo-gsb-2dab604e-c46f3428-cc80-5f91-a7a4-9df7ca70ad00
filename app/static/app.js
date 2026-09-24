/* Stage Cue — front-end application (vanilla JS). */
(function () {
  "use strict";

  const { computeSchedule, safeSchedule, formatTime, typeLabel, CycleError } =
    window.StageScheduler;

  const GUTTER = 130;
  const BASE_PPS = 20; // 100% zoom == 20px per second
  const MIN_PPS = 2;
  const MAX_PPS = 400;

  const TYPE_ORDER = ["light", "sound", "scene"];

  // ---------------------------------------------------------------- state
  const state = {
    shows: [],
    show: null, // {id,name,description,cues:[raw cue rows]}
    times: null, // Map id -> {start,end}
    cycle: null,
    pps: Number(localStorage.getItem("stagecue.pps")) || BASE_PPS,
    drag: null,
  };

  const $ = (sel) => document.querySelector(sel);

  // ------------------------------------------------------------- API layer
  async function api(method, url, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(url, opts);
    const text = await resp.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch (_) {
      data = { error: text };
    }
    if (!resp.ok) {
      const err = new Error((data && data.error) || `请求失败 (${resp.status})`);
      err.status = resp.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  function toast(message, kind) {
    const el = $("#toast");
    el.textContent = message;
    el.className = "toast" + (kind ? " " + kind : "");
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.hidden = true; }, 3200);
  }

  // -------------------------------------------------------------- bootstrap
  async function init() {
    bindStaticEvents();
    bindModalEvents();
    bindShowModalEvents();
    bindTimelineEvents();
    try {
      state.shows = await api("GET", "/api/shows");
    } catch (err) {
      toast("无法连接服务器：" + err.message, "error");
      return;
    }
    const savedId = Number(localStorage.getItem("stagecue.showId"));
    const pick = state.shows.find((s) => s.id === savedId) || state.shows[0];
    if (pick) {
      await selectShow(pick.id);
    } else {
      renderAll();
    }
  }

  // -------------------------------------------------------------- show flow
  async function selectShow(id) {
    try {
      state.show = await api("GET", `/api/shows/${id}`);
      localStorage.setItem("stagecue.showId", String(id));
    } catch (err) {
      toast(err.message, "error");
      state.show = null;
    }
    recompute();
    renderAll();
  }

  function recompute() {
    if (!state.show) {
      state.times = null;
      state.cycle = null;
      return;
    }
    const { schedule, cycle } = safeSchedule(state.show.cues);
    state.times = schedule;
    state.cycle = cycle;
  }

  function cueById(id) {
    return state.show.cues.find((c) => c.id === id);
  }

  // ----------------------------------------------------------------- render
  function renderAll() {
    renderShowBar();
    renderCueTable();
    renderTimeline();
  }

  function renderShowBar() {
    const sel = $("#showSelect");
    sel.innerHTML = "";
    for (const s of state.shows) {
      const opt = document.createElement("option");
      opt.value = String(s.id);
      opt.textContent = s.name + `（${s.cue_count} 条）`;
      sel.appendChild(opt);
    }
    if (state.show) sel.value = String(state.show.id);

    const nameEl = $("#currentShowName");
    const descEl = $("#showDescription");
    const meta = $("#showMeta");
    if (state.show) {
      nameEl.textContent = state.show.name;
      if (state.show.description) {
        descEl.hidden = false;
        descEl.textContent = state.show.description;
      } else {
        descEl.hidden = true;
      }
      const dur = state.shows.find((s) => s.id === state.show.id);
      const endText = dur && dur.duration != null ? formatTime(dur.duration) : "—";
      meta.textContent = `共 ${state.show.cues.length} 条 · 预计时长 ${endText}`;
      $("#btnAddCue").disabled = false;
      $("#btnExportShow").classList.remove("disabled");
    } else {
      nameEl.textContent = "提示列表";
      descEl.hidden = true;
      meta.textContent = "请先新建一场演出";
      $("#btnAddCue").disabled = true;
    }
  }

  function renderCueTable() {
    const tbody = $("#cueTbody");
    tbody.innerHTML = "";
    $("#cuesEmpty").hidden = !state.show || state.show.cues.length > 0;
    if (!state.show) return;

    const rows = state.show.cues.slice().sort((a, b) => {
      const ta = state.times ? state.times.get(a.id) : null;
      const tb = state.times ? state.times.get(b.id) : null;
      const sa = ta ? ta.start : null;
      const sb = tb ? tb.start : null;
      if (sa === null && sb !== null) return 1;
      if (sa !== null && sb === null) return -1;
      if (sa !== sb) return sa - sb;
      return a.position - b.position;
    });

    for (const cue of rows) {
      const t = state.times ? state.times.get(cue.id) : null;
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><strong>${escapeHtml(cue.number)}</strong></td>
        <td><span class="badge ${cue.cue_type}">${typeLabel(cue.cue_type)}</span></td>
        <td>
          <div class="cue-title">${escapeHtml(cue.title)}</div>
          ${cue.notes ? `<div class="cue-notes" title="${escapeHtml(cue.notes)}">${escapeHtml(cue.notes)}</div>` : ""}
        </td>
        <td class="trigger-text">${triggerSummary(cue)}</td>
        <td class="time-cell">${t && t.start !== null ? formatTime(t.start) : '<span class="time-none">—</span>'}</td>
        <td class="time-cell">${t && t.end !== null ? formatTime(t.end) : '<span class="time-none">—</span>'}</td>
        <td>
          <span class="row-btns">
            <button class="btn ghost small" data-act="edit">编辑</button>
            <button class="btn danger ghost small" data-act="del">删除</button>
          </span>
        </td>`;
      tr.querySelector('[data-act="edit"]').addEventListener("click", () => openCueModal(cue));
      tr.querySelector('[data-act="del"]').addEventListener("click", () => deleteCue(cue));
      tbody.appendChild(tr);
    }

    // Cycle banner
    const banner = $("#globalCycle");
    if (state.cycle instanceof CycleError) {
      banner.hidden = false;
      banner.querySelector("strong").textContent = "⚠ 当前提示单存在循环依赖：";
      const chain = state.cycle.chain;
      $("#globalCycleText").textContent =
        chain.slice(0, -1).map((c) => c.number).join(" → ") + " → 回到起点";
      $("#globalCycleChain").textContent = chain
        .map((c) => `${c.number}（${c.title}）`)
        .join("  →  ");
    } else if (state.cycle) {
      banner.hidden = false;
      banner.querySelector("strong").textContent = "⚠ 有提示无法排程：";
      $("#globalCycleText").textContent = state.cycle.message;
      $("#globalCycleChain").textContent = "请编辑该提示并选择有效的触发方式";
    } else {
      banner.hidden = true;
    }
  }

  function triggerSummary(cue) {
    if (cue.start_mode === "fixed") {
      return `<span class="tag">固定</span>${formatTime(cue.fixed_seconds)}`;
    }
    const pred = cueById(cue.predecessor_id);
    const predText = pred ? `${pred.number} 结束后` : "（前置已缺失）";
    const delay = Number(cue.delay_seconds);
    return `<span class="tag">跟随</span>${escapeHtml(predText)}${delay ? " + " + delay + "s" : ""}`;
  }

  // -------------------------------------------------------------- timeline
  function niceTickStep(pps) {
    const steps = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
    for (const s of steps) {
      if (s * pps >= 90) return s;
    }
    return 3600;
  }

  function renderTimeline() {
    const lanes = $("#tlLanes");
    const ruler = $("#tlRuler");
    lanes.innerHTML = "";
    ruler.innerHTML = "";
    $("#timelineInfo").textContent = "";
    if (!state.show) return;

    let maxEnd = 0;
    const unscheduled = [];
    for (const cue of state.show.cues) {
      const t = state.times ? state.times.get(cue.id) : null;
      if (t && t.end !== null) maxEnd = Math.max(maxEnd, t.end);
      else unscheduled.push(cue);
    }
    const totalSeconds = Math.max(maxEnd + 20, 60);
    const contentW = Math.max(totalSeconds * state.pps, 1);
    const inner = $("#timelineInner");
    inner.style.width = GUTTER + contentW + "px";
    inner.style.minWidth = "100%";

    // Ruler
    const step = niceTickStep(state.pps);
    for (let t = 0; t <= totalSeconds + 0.001; t += step) {
      const tick = document.createElement("div");
      tick.className = "tl-tick";
      tick.style.left = t * state.pps + "px";
      const label = document.createElement("span");
      label.textContent = formatTime(Math.round(t * 10) / 10);
      tick.appendChild(label);
      ruler.appendChild(tick);
    }

    // Grid background (aligned to ruler ticks)
    const gridPx = step * state.pps;
    const gridImage =
      `repeating-linear-gradient(to right, transparent 0, transparent ${gridPx - 1}px, #262a3d ${gridPx - 1}px, #262a3d ${gridPx}px)`;

    // Pack cues into tracks per type.
    for (const type of TYPE_ORDER) {
      const cues = state.show.cues
        .filter((c) => c.cue_type === type)
        .filter((c) => {
          const t = state.times.get(c.id);
          return t && t.start !== null;
        })
        .sort((a, b) => state.times.get(a.id).start - state.times.get(b.id).start);

      const tracks = []; // each: array of end-seconds
      const placement = new Map(); // cueId -> track index
      for (const cue of cues) {
        const t = state.times.get(cue.id);
        let placed = -1;
        for (let i = 0; i < tracks.length; i++) {
          if (tracks[i] <= t.start + 1e-6) {
            placed = i;
            break;
          }
        }
        if (placed === -1) {
          placed = tracks.length;
          tracks.push(0);
        }
        tracks[placed] = t.end;
        placement.set(cue.id, placed);
      }
      const trackCount = Math.max(tracks.length, 1);

      for (let trackIdx = 0; trackIdx < trackCount; trackIdx++) {
        const row = document.createElement("div");
        row.className = "tl-lane-row";

        const gutter = document.createElement("div");
        gutter.className = "tl-gutter";
        const dot = document.createElement("i");
        dot.className = "lane-kind " + type;
        gutter.appendChild(dot);
        const label = document.createElement("span");
        label.textContent =
          (trackIdx === 0 ? typeLabel(type) : "") +
          (trackCount > 1 ? ` 轨道 ${trackIdx + 1}` : "");
        gutter.appendChild(label);
        row.appendChild(gutter);

        const track = document.createElement("div");
        track.className = "lane-track";
        track.style.setProperty("--grid-image", gridImage);
        row.appendChild(track);

        for (const cue of cues) {
          if (placement.get(cue.id) !== trackIdx) continue;
          track.appendChild(buildBar(cue, state.times.get(cue.id)));
        }
        lanes.appendChild(row);
      }
    }

    // Unscheduled lane (missing predecessor / part of a cycle)
    if (unscheduled.length) {
      const row = document.createElement("div");
      row.className = "tl-lane-row";
      const gutter = document.createElement("div");
      gutter.className = "tl-gutter";
      gutter.innerHTML = `<i class="lane-kind" style="background:#a8756c"></i><span>⚠ 未排程</span>`;
      row.appendChild(gutter);
      const track = document.createElement("div");
      track.className = "lane-track";
      row.appendChild(track);
      unscheduled.forEach((cue, i) => {
        const bar = document.createElement("div");
        bar.className = `tl-bar after unscheduled ${cue.cue_type}`;
        bar.style.left = 4 + i * 128 + "px";
        bar.style.width = "122px";
        bar.title = "该提示的时间无法计算（前置缺失或处于循环中）";
        bar.innerHTML = `<span class="bar-num">${escapeHtml(cue.number)}</span><span class="bar-title">${escapeHtml(cue.title)}</span>`;
        bar.addEventListener("click", () => openCueModal(cue));
        track.appendChild(bar);
      });
      lanes.appendChild(row);
    }

    $("#zoomLabel").textContent =
      Math.round((state.pps / BASE_PPS) * 100) + "%";
    if (state.cycle instanceof CycleError) {
      $("#timelineInfo").textContent = "存在循环依赖，部分提示无法排程";
      $("#timelineInfo").style.color = "#ff9b9b";
    } else {
      $("#timelineInfo").style.color = "";
    }
  }

  function buildBar(cue, t) {
    const isFixed = cue.start_mode === "fixed";
    const bar = document.createElement("div");
    bar.className = `tl-bar ${cue.cue_type} ${isFixed ? "fixed" : "after"}`;
    const left = Math.max(0, t.start) * state.pps;
    const width = Math.max(6, (t.end - t.start) * state.pps);
    bar.style.left = left + "px";
    bar.style.width = width + "px";
    bar.dataset.cueId = String(cue.id);
    bar.title = `${cue.number} ${cue.title}\n开始 ${formatTime(t.start)} · 结束 ${formatTime(t.end)} · 时长 ${cue.duration_seconds}s`;

    const num = document.createElement("span");
    num.className = "bar-num";
    num.textContent = cue.number;
    bar.appendChild(num);
    const titleEl = document.createElement("span");
    titleEl.className = "bar-title";
    titleEl.textContent = cue.title;
    bar.appendChild(titleEl);
    const timeEl = document.createElement("span");
    timeEl.className = "bar-time";
    timeEl.textContent = formatTime(t.start);
    bar.appendChild(timeEl);

    if (width < 70) titleEl.style.display = "none";
    if (width < 120) timeEl.style.display = "none";

    const handle = document.createElement("div");
    handle.className = "resize-handle";
    handle.title = "拖动修改持续时长";
    bar.appendChild(handle);

    bar.addEventListener("mousedown", (e) => onBarMouseDown(e, cue));
    bar.addEventListener("click", () => {
      if (!state.drag || state.drag.moved < 4) openCueModal(cue);
    });
    return bar;
  }

  // -------------------------------------------------- drag move / resize
  function onBarMouseDown(e, cue) {
    if (e.button !== 0) return;
    const resize = e.target.classList.contains("resize-handle");
    e.preventDefault();
    const t = state.times.get(cue.id);
    state.drag = {
      cueId: cue.id,
      mode: resize ? "resize" : "move",
      startX: e.clientX,
      lastX: e.clientX,
      moved: 0,
      origFixed: Number(cue.fixed_seconds || 0),
      origDuration: Number(cue.duration_seconds),
      origStart: t.start,
      previewFixed: cue.fixed_seconds,
      previewDuration: cue.duration_seconds,
    };
    window.addEventListener("mousemove", onDragMove);
    window.addEventListener("mouseup", onDragUp);
  }

  function onDragMove(e) {
    const d = state.drag;
    if (!d) return;
    d.moved = Math.max(d.moved, Math.abs(e.clientX - d.startX));
    d.lastX = e.clientX;
    const cue = cueById(d.cueId);
    const dxSec = (e.clientX - d.startX) / state.pps;

    if (d.mode === "move") {
      d.previewFixed = Math.max(0, Math.round((d.origFixed + dxSec) * 10) / 10);
      cue.fixed_seconds = d.previewFixed;
    } else {
      d.previewDuration = Math.max(
        0.1,
        Math.round((d.origDuration + dxSec) * 10) / 10
      );
      cue.duration_seconds = d.previewDuration;
    }

    recompute();
    renderTimeline();
    showDragTip(e, cue, d);
  }

  function showDragTip(e, cue, d) {
    const tip = $("#tlDragTip");
    const t = state.times.get(cue.id);
    if (d.mode === "move") {
      tip.textContent = `固定时间 → ${formatTime(d.previewFixed)}`;
    } else {
      tip.textContent =
        `时长 ${d.previewDuration}s · 结束 ${t ? formatTime(t.end) : "—"}`;
    }
    const rect = $("#timelineScroll").getBoundingClientRect();
    tip.hidden = false;
    tip.style.left = Math.min(e.clientX - rect.left + 12, rect.width - 130) + "px";
    tip.style.top = e.clientY - rect.top + 14 + "px";
  }

  async function onDragUp() {
    const d = state.drag;
    window.removeEventListener("mousemove", onDragMove);
    window.removeEventListener("mouseup", onDragUp);
    $("#tlDragTip").hidden = true;
    state.drag = null;
    if (!d || d.moved < 4) {
      renderTimeline();
      return;
    }
    const cue = cueById(d.cueId);
    const payload = {
      number: cue.number,
      cue_type: cue.cue_type,
      title: cue.title,
      notes: cue.notes,
      start_mode: cue.start_mode,
      fixed_seconds: cue.fixed_seconds,
      predecessor_id: cue.predecessor_id,
      delay_seconds: cue.delay_seconds,
      duration_seconds: cue.duration_seconds,
    };
    try {
      await api("PUT", `/api/cues/${cue.id}`, payload);
      await refreshShow({ keepScroll: true });
      toast(
        d.mode === "move"
          ? `已更新固定时间：${formatTime(payload.fixed_seconds)}，后继提示已联动重排`
          : `已更新时长：${payload.duration_seconds}s，后继提示已联动重排`,
        "success"
      );
    } catch (err) {
      toast(err.message, "error");
      await refreshShow({ keepScroll: true });
    }
  }

  async function refreshShow(opts = {}) {
    if (!state.show) return;
    const sl = $("#timelineScroll");
    const left = sl.scrollLeft;
    const id = state.show.id;
    state.shows = await api("GET", "/api/shows");
    state.show = await api("GET", `/api/shows/${id}`);
    recompute();
    renderAll();
    if (opts.keepScroll) sl.scrollLeft = left;
  }

  // ---------------------------------------------------------------- zoom
  function zoomAt(clientX, factor) {
    const sl = $("#timelineScroll");
    const rect = sl.getBoundingClientRect();
    const xRel = clientX == null ? rect.width / 2 : clientX - rect.left;
    const anchorTime =
      (sl.scrollLeft + xRel - GUTTER) / state.pps;
    state.pps = Math.min(
      MAX_PPS,
      Math.max(MIN_PPS, state.pps * factor)
    );
    localStorage.setItem("stagecue.pps", String(state.pps));
    renderTimeline();
    sl.scrollLeft = anchorTime * state.pps - (xRel - GUTTER);
  }

  function bindTimelineEvents() {
    const sl = $("#timelineScroll");
    sl.addEventListener(
      "wheel",
      (e) => {
        if (e.ctrlKey || e.metaKey) {
          e.preventDefault();
          const factor = Math.exp(-e.deltaY * 0.0015);
          zoomAt(e.clientX, factor);
        } else if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
          e.preventDefault();
          sl.scrollLeft += e.deltaY;
        }
      },
      { passive: false }
    );
    $("#btnZoomIn").addEventListener("click", () => zoomAt(null, 1.25));
    $("#btnZoomOut").addEventListener("click", () => zoomAt(null, 1 / 1.25));
    $("#btnZoomReset").addEventListener("click", () => {
      state.pps = BASE_PPS;
      localStorage.setItem("stagecue.pps", String(state.pps));
      renderTimeline();
    });
  }

  // ------------------------------------------------------------- cue modal
  let editingCueId = null;

  function bindStaticEvents() {
    $("#showSelect").addEventListener("change", (e) =>
      selectShow(Number(e.target.value))
    );
    $("#btnNewShow").addEventListener("click", () => openShowModal("create"));
    $("#btnRenameShow").addEventListener("click", () => openShowModal("rename"));
    $("#btnDeleteShow").addEventListener("click", deleteCurrentShow);
    $("#btnAddCue").addEventListener("click", () => openCueModal(null));

    $("#btnImport").addEventListener("click", () => $("#fileImport").click());
    $("#fileImport").addEventListener("change", importFile);
    $("#btnExportAll").addEventListener("click", () => {
      window.location.href = "/api/export";
    });
    $("#btnExportShow").addEventListener("click", () => {
      if (state.show) window.location.href = `/api/shows/${state.show.id}/export`;
    });
  }

  function buildPredecessorOptions(selectedId) {
    const sel = $("#fPredecessor");
    sel.innerHTML = "";
    const cues = (state.show ? state.show.cues : [])
      .filter((c) => c.id !== editingCueId)
      .slice()
      .sort((a, b) => a.position - b.position);
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "— 请选择前置提示 —";
    sel.appendChild(placeholder);
    for (const c of cues) {
      const opt = document.createElement("option");
      opt.value = String(c.id);
      opt.textContent = `${c.number}｜${typeLabel(c.cue_type)}｜${c.title}`;
      sel.appendChild(opt);
    }
    if (selectedId != null) sel.value = String(selectedId);
  }

  function openCueModal(cue) {
    editingCueId = cue ? cue.id : null;
    $("#cueModalTitle").textContent = cue ? `编辑提示 ${cue.number}` : "新增提示";
    $("#cueFormError").hidden = true;

    $("#fNumber").value = cue ? cue.number : "";
    $("#fType").value = cue ? cue.cue_type : "light";
    $("#fTitle").value = cue ? cue.title : "";
    $("#fNotes").value = cue ? cue.notes : "";
    const mode = cue ? cue.start_mode : "fixed";
    document.querySelectorAll('input[name="startMode"]').forEach((r) => {
      r.checked = r.value === mode;
    });
    $("#fFixed").value = cue && cue.fixed_seconds != null ? cue.fixed_seconds : 0;
    buildPredecessorOptions(cue ? cue.predecessor_id : null);
    $("#fDelay").value = cue ? cue.delay_seconds : 0;
    $("#fDuration").value = cue ? cue.duration_seconds : 1;
    toggleModePanes();
    updateLivePreview();
    $("#cueModal").hidden = false;
    setTimeout(() => $("#fTitle").focus(), 30);
  }

  function closeCueModal() {
    $("#cueModal").hidden = true;
    editingCueId = null;
  }

  function toggleModePanes() {
    const mode = document.querySelector('input[name="startMode"]:checked').value;
    $("#modeFixed").hidden = mode !== "fixed";
    $("#modeAfter").hidden = mode !== "after";
  }

  function readCueForm() {
    const mode = document.querySelector('input[name="startMode"]:checked').value;
    return {
      number: $("#fNumber").value.trim(),
      cue_type: $("#fType").value,
      title: $("#fTitle").value.trim(),
      notes: $("#fNotes").value.trim(),
      start_mode: mode,
      fixed_seconds: Number($("#fFixed").value),
      predecessor_id: $("#fPredecessor").value
        ? Number($("#fPredecessor").value)
        : null,
      delay_seconds: Number($("#fDelay").value),
      duration_seconds: Number($("#fDuration").value),
    };
  }

  function updateLivePreview() {
    const live = $("#cueLive");
    const form = readCueForm();
    if (!form.title) {
      live.className = "live-preview";
      live.textContent = "填写标题后，预计开始/结束时间将在此实时显示";
      return;
    }
    const others = state.show.cues.filter((c) => c.id !== editingCueId);
    const tentative = {
      id: editingCueId || -1,
      number: form.number || "(新提示)",
      title: form.title,
      start_mode: form.start_mode,
      fixed_seconds: form.fixed_seconds,
      predecessor_id: form.predecessor_id,
      delay_seconds: form.delay_seconds,
      duration_seconds: form.duration_seconds,
    };
    try {
      const map = computeSchedule(others.concat([tentative]));
      const t = map.get(tentative.id);
      if (!t || t.start === null) {
        live.className = "live-preview err";
        live.textContent = "无法计算时间：请选择前置提示";
      } else {
        live.className = "live-preview ok";
        live.textContent =
          `预计开始 ${formatTime(t.start)} · 结束 ${formatTime(t.end)} · 时长 ${form.duration_seconds}s`;
      }
    } catch (err) {
      live.className = "live-preview err";
      if (err instanceof CycleError) {
        live.textContent =
          "⚠ 会形成循环：" +
          err.chain.map((c) => c.number || "(新提示)").join(" → ");
      } else {
        live.textContent = "⚠ " + err.message;
      }
    }
  }

  function bindModalEvents() {
    $("#cueModalClose").addEventListener("click", closeCueModal);
    $("#cueCancel").addEventListener("click", closeCueModal);
    $("#cueModal").addEventListener("click", (e) => {
      if (e.target.id === "cueModal") closeCueModal();
    });
    document.querySelectorAll('input[name="startMode"]').forEach((r) =>
      r.addEventListener("change", updateLivePreview)
    );
    [
      "#fNumber", "#fType", "#fTitle", "#fNotes", "#fFixed",
      "#fPredecessor", "#fDelay", "#fDuration",
    ].forEach((sel) =>
      $(sel).addEventListener("input", updateLivePreview)
    );
    document.querySelectorAll('input[name="startMode"]').forEach((r) =>
      r.addEventListener("change", () => { toggleModePanes(); updateLivePreview(); })
    );
    $("#cueForm").addEventListener("submit", onCueSubmit);
  }

  async function onCueSubmit(e) {
    e.preventDefault();
    const form = readCueForm();
    const errEl = $("#cueFormError");
    errEl.hidden = true;

    if (!form.title) {
      showCueFormError("标题不能为空");
      return;
    }
    if (!(form.duration_seconds >= 0.1)) {
      showCueFormError("持续时长必须 ≥ 0.1 秒");
      return;
    }
    if (form.start_mode === "fixed" && !(form.fixed_seconds >= 0)) {
      showCueFormError("固定时间必须是 ≥ 0 的数字");
      return;
    }
    if (form.start_mode === "after" && form.predecessor_id == null) {
      showCueFormError("请选择一个前置提示");
      return;
    }
    if (!(form.delay_seconds >= 0)) {
      showCueFormError("延迟秒数必须 ≥ 0");
      return;
    }

    try {
      if (editingCueId) {
        await api("PUT", `/api/cues/${editingCueId}`, form);
      } else {
        await api("POST", `/api/shows/${state.show.id}/cues`, form);
      }
      closeCueModal();
      await refreshShow();
      toast("已保存，时间轴已更新", "success");
    } catch (err) {
      // 409 cycle: keep the modal open and preserve all entered values.
      if (err.status === 409 && err.data && err.data.type === "cycle") {
        const chainText =
          err.data.chain_text ||
          (err.data.chain || []).map((c) => c.number).join(" → ");
        showCueFormError(
          `保存失败：该依赖会形成循环，服务器已拒绝写入。冲突链：${chainText}`,
          (err.data.chain || []).map((c) => `${c.number}（${c.title}）`).join("  →  ")
        );
      } else {
        showCueFormError("保存失败：" + err.message);
      }
    }
  }

  function showCueFormError(message, chain) {
    const errEl = $("#cueFormError");
    errEl.innerHTML = "";
    const p = document.createElement("div");
    p.textContent = message;
    errEl.appendChild(p);
    if (chain) {
      const c = document.createElement("div");
      c.className = "chain";
      c.textContent = chain;
      errEl.appendChild(c);
    }
    errEl.hidden = false;
  }

  async function deleteCue(cue) {
    if (!confirm(`确定删除提示 ${cue.number}「${cue.title}」？`)) return;
    try {
      await api("DELETE", `/api/cues/${cue.id}`);
      await refreshShow();
      toast("已删除", "success");
    } catch (err) {
      toast(err.message, "error");
    }
  }

  // ------------------------------------------------------------- show CRUD
  let showModalMode = "create";

  function openShowModal(mode) {
    if (mode === "rename" && !state.show) return;
    showModalMode = mode;
    $("#showModalTitle").textContent = mode === "create" ? "新建演出" : "编辑演出信息";
    $("#sName").value = mode === "rename" ? state.show.name : "";
    $("#sDescription").value = mode === "rename" ? state.show.description : "";
    $("#showFormError").hidden = true;
    $("#showModal").hidden = false;
    setTimeout(() => $("#sName").focus(), 30);
  }

  function closeShowModal() {
    $("#showModal").hidden = true;
  }

  function bindShowModalEvents() {
    $("#showModalClose").addEventListener("click", closeShowModal);
    $("#showCancel").addEventListener("click", closeShowModal);
    $("#showModal").addEventListener("click", (e) => {
      if (e.target.id === "showModal") closeShowModal();
    });
    $("#showForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const errEl = $("#showFormError");
      const payload = {
        name: $("#sName").value.trim(),
        description: $("#sDescription").value.trim(),
      };
      if (!payload.name) {
        errEl.textContent = "演出名称不能为空";
        errEl.hidden = false;
        return;
      }
      try {
        if (showModalMode === "create") {
          const created = await api("POST", "/api/shows", payload);
          state.shows = await api("GET", "/api/shows");
          await selectShow(created.id);
        } else {
          await api("PUT", `/api/shows/${state.show.id}`, payload);
          await refreshShow();
        }
        closeShowModal();
        toast("已保存", "success");
      } catch (err) {
        errEl.textContent = err.message;
        errEl.hidden = false;
      }
    });
  }

  async function deleteCurrentShow() {
    if (!state.show) return;
    if (!confirm(`确定删除整场演出「${state.show.name}」及其全部提示？此操作不可撤销。`)) return;
    const id = state.show.id;
    try {
      await api("DELETE", `/api/shows/${id}`);
      localStorage.removeItem("stagecue.showId");
      state.shows = await api("GET", "/api/shows");
      state.show = null;
      if (state.shows[0]) await selectShow(state.shows[0].id);
      else { recompute(); renderAll(); }
      toast("演出已删除", "success");
    } catch (err) {
      toast(err.message, "error");
    }
  }

  // ------------------------------------------------------ import / export
  async function importFile(e) {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    let json;
    try {
      json = JSON.parse(await file.text());
    } catch (_) {
      toast("文件不是合法的 JSON", "error");
      return;
    }
    try {
      const result = await api("POST", "/api/import", json);
      toast(
        `导入成功：${result.imported.map((s) => `《${s.name}》(${s.cue_count} 条)`).join("，")}`,
        "success"
      );
      state.shows = await api("GET", "/api/shows");
      if (result.imported[0]) await selectShow(result.imported[0].id);
      else renderAll();
    } catch (err) {
      if (err.status === 409 && err.data && err.data.type === "cycle") {
        const chain =
          err.data.chain_text ||
          (err.data.chain || []).map((c) => c.number).join(" → ");
        toast(`导入被拒绝，文件中存在循环依赖：${chain}（未写入任何数据）`, "error");
      } else {
        toast("导入失败：" + err.message, "error");
      }
    }
  }

  // ------------------------------------------------------------ utilities
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  document.addEventListener("DOMContentLoaded", init);
})();
