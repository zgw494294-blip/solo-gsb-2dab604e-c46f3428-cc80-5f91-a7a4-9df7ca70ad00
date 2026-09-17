'use strict';

/* ============================================================
 * 舞台提示单前端（原生 JS）
 * 时间编排算法与后端 app/scheduler.py 保持一致，在浏览器内
 * 实时计算；保存时以后端校验为准（循环依赖会返回完整冲突链）。
 * ============================================================ */

// ---------------- 常量与状态 ----------------

const LANES = [
  { key: 'lighting', label: '灯光' },
  { key: 'sound', label: '音效' },
  { key: 'scene', label: '换景' },
];
const LANE_KEYS = LANES.map((l) => l.key);
const TYPE_LABEL = { lighting: '灯光', sound: '音效', scene: '换景' };

const HEADER_H = 34;
const LANE_H = 74;
const GUTTER_W = 56;
const BASE_SCALE = 16;   // 100% = 每秒 16px
const MIN_SCALE = 2;
const MAX_SCALE = 240;
const TICK_STEPS = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600];

const state = {
  shows: [],
  currentShowId: null,
  show: null,
  cues: [],
  schedule: { items: {}, cycles: [], max_end: null },
  scale: BASE_SCALE,
  selectedCueId: null,
  loading: false,
};

const NEW_ID = '__new_cue__';
const LS_SHOW = 'stagecue.currentShowId';

// ---------------- 小工具 ----------------

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
  ));
}

function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

function parseNum(input, fallback = 0) {
  const v = parseFloat(input);
  return Number.isFinite(v) && v >= 0 ? v : fallback;
}

/* 秒数 -> 可读时间，如 42.0 / 1:05.5 / 1:02:03 */
function fmtTime(sec) {
  if (sec == null || !Number.isFinite(sec)) return '—';
  sec = Math.round(sec * 10) / 10;
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec - h * 3600 - m * 60;
  const sText = s.toFixed(1);
  const sTextPadded = sText.padStart(4, '0');
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${sTextPadded}`;
  if (m > 0) return `${m}:${sTextPadded}`;
  return `${sText}s`;
}

/* 时间轴刻度的紧凑标签 */
function fmtTick(sec) {
  if (sec < 60) return `${sec % 1 ? sec.toFixed(1) : sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec - m * 60;
  if (m < 60) return s ? `${m}:${String(s).padStart(2, '0')}` : `${m}:00`;
  const h = Math.floor(m / 60);
  return `${h}:${String(m % 60).padStart(2, '0')}`;
}

let toastTimer = null;
function toast(message, kind = '') {
  const el = $('toast');
  el.textContent = message;
  el.className = kind ? `toast ${kind}` : 'toast';
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, kind === 'error' ? 5200 : 2800);
}

async function api(method, url, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const resp = await fetch(url, opt);
  let data = null;
  try { data = await resp.json(); } catch (_) { /* 非 JSON */ }
  if (!resp.ok) {
    const err = new Error((data && data.error) || `请求失败（${resp.status}）`);
    err.status = resp.status;
    err.data = data || {};
    throw err;
  }
  return data;
}

// ---------------- 调度算法（后端镜像） ----------------

function buildGraph(cues) {
  const byId = new Map();
  for (const c of cues) byId.set(c.id, c);
  const successors = new Map();
  for (const id of byId.keys()) successors.set(id, []);
  for (const c of cues) {
    if (c.mode === 'after' && c.depends_on != null && byId.has(c.depends_on)) {
      successors.get(c.depends_on).push(c.id);
    }
  }
  return { byId, successors };
}

/* 三色 DFS 找全部环，返回完整冲突链（首尾相同） */
function findCycles(cues) {
  const { byId, successors } = buildGraph(cues);
  const WHITE = 0, GRAY = 1, BLACK = 2;
  const color = new Map();
  for (const id of byId.keys()) color.set(id, WHITE);
  const chains = [];

  function walk(node, stack, onPath) {
    color.set(node, GRAY);
    onPath.set(node, stack.length);
    stack.push(node);
    for (const succ of successors.get(node) || []) {
      if (color.get(succ) === WHITE) {
        walk(succ, stack, onPath);
      } else if (color.get(succ) === GRAY) {
        const start = onPath.get(succ);
        chains.push(stack.slice(start).concat([succ]));
      }
    }
    stack.pop();
    onPath.delete(node);
    color.set(node, BLACK);
  }

  const onPath = new Map();
  for (const id of byId.keys()) {
    if (color.get(id) === WHITE) walk(id, [], onPath);
  }

  // 旋转归一化去重
  const seen = new Set();
  return chains.filter((chain) => {
    const ring = chain.slice(0, -1);
    const rotations = ring.map((_, i) => ring.slice(i).concat(ring.slice(0, i)).join('|'));
    const key = rotations.slice().sort()[0];
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function cueState(cue, byId, memo) {
  const id = cue.id;
  if (memo.has(id)) return memo.get(id);
  if (cue.mode === 'fixed') { memo.set(id, 'ok'); return 'ok'; }
  if (cue.mode !== 'after' || cue.depends_on == null || !byId.has(cue.depends_on)) {
    memo.set(id, 'unanchored');
    return 'unanchored';
  }
  memo.set(id, 'cycle'); // 占位，环上递归时终止
  const depState = cueState(byId.get(cue.depends_on), byId, memo);
  const result = depState === 'ok' ? 'ok' : depState;
  memo.set(id, result);
  return result;
}

function computeSchedule(cues) {
  const { byId } = buildGraph(cues);
  const cycles = findCycles(cues);
  const cycleNodes = new Set(cycles.flat());
  const memo = new Map();
  for (const id of cycleNodes) memo.set(id, 'cycle');
  for (const cue of cues) cueState(cue, byId, memo);

  const items = {};
  let maxEnd = null;

  // 状态为 ok 的提示沿依赖只可能指向已就绪的提示；多趟求值直到稳定
  const pending = new Set(cues.map((c) => c.id));
  let progressed = true;
  while (pending.size && progressed) {
    progressed = false;
    for (const id of [...pending]) {
      const cue = byId.get(id);
      const st = memo.get(id);
      if (st !== 'ok') {
        items[id] = { state: st, start: null, end: null };
        pending.delete(id);
        progressed = true;
        continue;
      }
      if (cue.mode === 'fixed') {
        const start = Number(cue.fixed_at) || 0;
        const end = start + (Number(cue.duration) || 0);
        items[id] = { state: 'ok', start, end };
        pending.delete(id);
        progressed = true;
        if (maxEnd == null || end > maxEnd) maxEnd = end;
      } else {
        const depItem = items[cue.depends_on];
        if (depItem && depItem.state === 'ok') {
          const start = depItem.end + (Number(cue.delay) || 0);
          const end = start + (Number(cue.duration) || 0);
          items[id] = { state: 'ok', start, end };
          pending.delete(id);
          progressed = true;
          if (maxEnd == null || end > maxEnd) maxEnd = end;
        }
      }
    }
  }
  // 理论上不会剩下（环已被标记），保底处理
  for (const id of pending) items[id] = { state: memo.get(id) || 'cycle', start: null, end: null };

  return { items, cycles, max_end: maxEnd };
}

/* 用编辑草稿构造“候选提示集”（替换同 id 或追加新提示） */
function withDraft(cues, draft) {
  const result = cues.filter((c) => c.id !== draft.id);
  result.push(draft);
  return result;
}

// ---------------- 数据加载 ----------------

async function loadShows(selectId) {
  const data = await api('GET', '/api/shows');
  state.shows = data.shows;
  renderShowSelect();
  let target = selectId ?? (localStorage.getItem(LS_SHOW) ? Number(localStorage.getItem(LS_SHOW)) : null);
  if (!target || !state.shows.some((s) => s.id === target)) {
    target = state.shows[0]?.id ?? null;
  }
  if (target) await selectShow(target);
  else {
    state.currentShowId = null;
    state.show = null;
    state.cues = [];
    state.schedule = { items: {}, cycles: [], max_end: null };
    renderAll();
  }
}

async function selectShow(id) {
  state.loading = true;
  try {
    const data = await api('GET', `/api/shows/${id}`);
    state.currentShowId = id;
    state.show = data.show;
    applyDetail(data);
    localStorage.setItem(LS_SHOW, String(id));
    renderShowSelect();
    fitTimeline(true);
  } finally {
    state.loading = false;
  }
}

function applyDetail(data) {
  state.cues = data.cues;
  state.schedule = data.schedule;
  if (data.show) state.show = data.show;
  renderAll();
}

// ---------------- 顶部演出切换 ----------------

function renderShowSelect() {
  const sel = $('showSelect');
  sel.innerHTML = '';
  if (!state.shows.length) {
    const opt = document.createElement('option');
    opt.textContent = '（暂无演出）';
    sel.appendChild(opt);
    return;
  }
  for (const s of state.shows) {
    const opt = document.createElement('option');
    opt.value = s.id;
    opt.textContent = `${s.name}（${s.cue_count} 个提示）`;
    opt.selected = s.id === state.currentShowId;
    sel.appendChild(opt);
  }
}

// ---------------- 时间轴渲染 ----------------

function chooseTickStep(scale) {
  for (const step of TICK_STEPS) {
    if (step * scale >= 80) return step;
  }
  return TICK_STEPS[TICK_STEPS.length - 1];
}

function truncateText(text, maxPx) {
  // 中文约 11px、ASCII 约 6.5px，粗略估算用于裁剪
  let width = 0;
  let out = '';
  for (const ch of String(text)) {
    const w = ch.charCodeAt(0) > 255 ? 11 : 6.5;
    if (width + w > maxPx - 6) return out + '…';
    width += w;
    out += ch;
  }
  return out;
}

function renderTimeline() {
  const svg = $('timeline');
  const wrap = $('timelineWrap');
  const gutter = $('laneGutter');
  const scale = state.scale;
  const cues = state.cues;
  const items = state.schedule.items;

  const maxEnd = state.schedule.max_end ?? 0;
  const totalSec = Math.max(maxEnd + 20, 12);
  const contentW = Math.max(totalSec * scale, wrap.clientWidth - GUTTER_W - 10);
  const badCues = cues.filter((c) => items[c.id] && items[c.id].state !== 'ok');
  const badStripH = badCues.length ? 52 : 0;
  const contentH = HEADER_H + LANES.length * LANE_H + badStripH + 18;

  svg.setAttribute('width', contentW);
  svg.setAttribute('height', contentH);
  svg.style.marginLeft = `${GUTTER_W}px`;

  const parts = [];

  // 箭头 marker
  parts.push(`<defs>
    <marker id="arrowhead" markerWidth="8" markerHeight="8" refX="7" refY="4"
      orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L8,4 L0,8 z" fill="#6b7385"/>
    </marker>
  </defs>`);

  // 泳道背景
  LANES.forEach((lane, i) => {
    const y = HEADER_H + i * LANE_H;
    parts.push(`<rect class="tl-lane-bg ${i % 2 ? 'alt' : ''}" x="0" y="${y}"
      width="${contentW}" height="${LANE_H}"/>`);
    parts.push(`<line class="tl-grid-line" x1="0" y1="${y}" x2="${contentW}" y2="${y}"/>`);
  });

  // 时间刻度：主刻度 step，每格再分 5 个次刻度
  const step = chooseTickStep(scale);
  const minorStep = step / 5;
  const minorCount = Math.ceil(totalSec / minorStep);
  for (let i = 0; i <= minorCount; i++) {
    const t = Math.round(i * minorStep * 1000) / 1000;
    const x = t * scale;
    if (i % 5 === 0) {
      parts.push(`<line class="tl-grid-line" x1="${x}" y1="${HEADER_H}" x2="${x}"
        y2="${HEADER_H + LANES.length * LANE_H}"/>`);
      parts.push(`<text class="tl-tick-label" x="${x + 4}" y="21">${fmtTick(t)}</text>`);
    } else if (scale * minorStep >= 14) {
      parts.push(`<line class="tl-grid-line minor" x1="${x}" y1="${HEADER_H}" x2="${x}"
        y2="${HEADER_H + LANES.length * LANE_H}"/>`);
    }
  }

  // 先计算每个色块的几何位置（依赖箭头需要）
  const boxes = new Map(); // id -> {x,y,w,h, laneOrBad}
  const cueGroups = [];
  for (const cue of cues) {
    const item = items[cue.id];
    if (!item || item.state !== 'ok') continue;
    const laneIdx = LANE_KEYS.indexOf(cue.cue_type);
    const lane = laneIdx >= 0 ? laneIdx : 0;
    const y = HEADER_H + lane * LANE_H + 9;
    const h = LANE_H - 18;
    const x = item.start * scale;
    const w = Math.max(item.end * scale - x, 26);
    boxes.set(cue.id, { x, y, w, h });
    const selected = state.selectedCueId === cue.id ? 'selected' : '';
    const name = escapeHtml(truncateText(cue.name, w - 8));
    const time = `${fmtTime(item.start)} – ${fmtTime(item.end)}`;
    const depTag = cue.mode === 'fixed' ? 'fixed' : 'after';
    cueGroups.push(`
      <g class="tl-cue ${cue.cue_type} ${depTag} ${selected}" data-id="${cue.id}">
        <rect class="body" data-action="${cue.mode === 'fixed' ? 'move' : 'open'}"
          x="${x}" y="${y}" width="${w}" height="${h}"/>
        <text class="cue-name" x="${x + 7}" y="${y + 20}">${name}</text>
        <text class="cue-time" x="${x + 7}" y="${y + 38}">${escapeHtml(truncateText(time, w - 8))}</text>
        <rect class="tl-resize-handle" data-action="resize"
          x="${x + w - 6}" y="${y + 2}" width="5" height="${h - 4}" rx="2"/>
      </g>`);
  }

  // 无法落地 / 环上提示：底部横排
  const stripY = HEADER_H + LANES.length * LANE_H + 14;
  let bx = 8;
  for (const cue of badCues) {
    const item = items[cue.id];
    const w = 150;
    const x = bx;
    boxes.set(cue.id, { x, y: stripY, w, h: 38, bad: true });
    bx += w + 10;
  }
  if (badCues.length) {
    parts.push(`<line class="tl-grid-line" x1="0" y1="${HEADER_H + LANES.length * LANE_H + 4}"
      x2="${Math.max(contentW, bx)}" y2="${HEADER_H + LANES.length * LANE_H + 4}"/>`);
  }

  // 依赖箭头
  const arrows = [];
  for (const cue of cues) {
    if (cue.mode !== 'after' || cue.depends_on == null) continue;
    const from = boxes.get(cue.depends_on);
    const to = boxes.get(cue.id);
    if (!from || !to) continue;
    const bad = to.bad || from.bad;
    const x1 = from.x + from.w, y1 = from.y + from.h / 2;
    const x2 = to.x, y2 = to.y + to.h / 2;
    const dx = Math.max(20, Math.min(60, (x2 - x1) / 2));
    arrows.push(`<path class="tl-dep-line ${bad ? 'bad' : ''}"
      d="M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}"/>`);
  }
  parts.push(arrows.join(''));

  // 提示色块
  parts.push(cueGroups.join(''));

  // 底部异常提示块
  for (const cue of badCues) {
    const b = boxes.get(cue.id);
    const item = items[cue.id];
    const label = item.state === 'cycle' ? '⛔ 循环依赖' : '⚠ 缺少固定起点';
    parts.push(`<g class="tl-cue ${cue.cue_type} unanchored" data-id="${cue.id}">
      <rect class="tl-bad-rect" data-action="open" x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}"/>
      <text class="tl-bad-text" x="${b.x + 8}" y="${b.y + 16}">${escapeHtml(truncateText(cue.name, b.w - 16))}</text>
      <text class="tl-bad-text" x="${b.x + 8}" y="${b.y + 32}">${label}</text>
    </g>`);
  }

  svg.innerHTML = parts.join('');

  // 左侧泳道标签覆盖层
  const laneNames = LANES.map((l, i) => {
    const y = HEADER_H + i * LANE_H + LANE_H / 2;
    const cls = ['lane-name', l.key].join(' ');
    return `<div class="${cls}" style="top:${y}px">${l.label}</div>`;
  }).join('');
  const badLabel = badCues.length
    ? `<div class="lane-name bad" style="top:${stripY + 19}px">异常</div>` : '';
  gutter.innerHTML = laneNames + badLabel;
  gutter.style.transform = `translate(${wrap.scrollLeft}px, ${-wrap.scrollTop}px)`;

  $('timelineEmpty').hidden = cues.length > 0;
  $('zoomLabel').textContent = `${Math.round((scale / BASE_SCALE) * 100)}%`;

  renderSummary();
}

function renderSummary() {
  const cues = state.cues;
  const items = state.schedule.items;
  const cycles = state.schedule.cycles;
  const badCount = cues.filter((c) => items[c.id] && items[c.id].state === 'unanchored').length;
  const parts = [];
  if (state.schedule.max_end != null) parts.push(`总时长 ${fmtTime(state.schedule.max_end)}`);
  if (badCount) parts.push(`⚠ ${badCount} 个提示缺少固定起点`);
  if (cycles.length) parts.push(`⛔ ${cycles.length} 条循环依赖`);
  $('runtimeSummary').textContent = parts.join('　');

  const hint = $('cycleHint');
  if (cycles.length) {
    const byId = new Map(cues.map((c) => [c.id, c]));
    const chains = cycles.map((chain) =>
      chain.map((id) => (byId.get(id) ? byId.get(id).name : id)).join(' → ')).join('；');
    hint.textContent = `⛔ 存在 ${cycles.length} 条循环依赖`;
    hint.title = chains;
  } else {
    hint.textContent = '';
    hint.title = '';
  }
}

// ---------------- 右侧提示列表 ----------------

function renderCueTable() {
  const wrap = $('cueTable');
  if (!state.cues.length) {
    wrap.innerHTML = '<div class="hint" style="padding:14px 4px">暂无提示。</div>';
    return;
  }
  const items = state.schedule.items;
  const byId = new Map(state.cues.map((c) => [c.id, c]));

  const rows = state.cues.slice().sort((a, b) => {
    const ia = items[a.id], ib = items[b.id];
    const rank = { ok: 0, unanchored: 1, cycle: 2 };
    if ((rank[ia.state] || 0) !== (rank[ib.state] || 0)) {
      return (rank[ia.state] || 0) - (rank[ib.state] || 0);
    }
    if (ia.start != null && ib.start != null && Math.abs(ia.start - ib.start) > 1e-6) {
      return ia.start - ib.start;
    }
    return a.position - b.position;
  });

  wrap.innerHTML = rows.map((cue, idx) => {
    const item = items[cue.id];
    const st = item.state;
    let meta;
    if (st === 'ok') {
      const dep = cue.mode === 'after' && byId.get(cue.depends_on)
        ? `<span class="tag after">← ${escapeHtml(byId.get(cue.depends_on).name)}${cue.delay ? ` +${fmtTime(cue.delay)}` : ''}</span>`
        : '<span class="tag fixed">固定</span>';
      meta = `<span class="time">${fmtTime(item.start)} – ${fmtTime(item.end)}</span>
        <span class="tag">时长 ${fmtTime(cue.duration)}</span>${dep}`;
    } else if (st === 'unanchored') {
      meta = `<span class="bad">⚠ 依赖链上没有固定时间提示</span>`;
    } else {
      meta = `<span class="bad">⛔ 在循环依赖中</span>`;
    }
    return `<div class="cue-row ${cue.cue_type} ${st === 'ok' ? '' : st} ${state.selectedCueId === cue.id ? 'selected' : ''}"
        data-id="${cue.id}">
      <span class="cue-badge">${idx + 1}</span>
      <div class="cue-main">
        <div class="cue-title">${escapeHtml(cue.name)}</div>
        <div class="cue-meta"><span class="tag">${TYPE_LABEL[cue.cue_type] || cue.cue_type}</span>${meta}</div>
      </div>
    </div>`;
  }).join('');
}

function renderAll() {
  renderTimeline();
  renderCueTable();
}

// ---------------- 缩放 ----------------

function zoomAt(clientX, factor) {
  const wrap = $('timelineWrap');
  const rect = $('timeline').getBoundingClientRect();
  const anchorX = clientX - rect.left + wrap.scrollLeft; // svg 内部坐标（无 gutter 偏移）
  const anchorT = clamp(anchorX / state.scale, 0, Infinity);
  const newScale = clamp(state.scale * factor, MIN_SCALE, MAX_SCALE);
  if (newScale === state.scale) return;
  state.scale = newScale;
  renderTimeline();
  wrap.scrollLeft = Math.max(0, anchorT * newScale - anchorX);
}

function zoomCenter(factor) {
  const wrap = $('timelineWrap');
  zoomAt(wrap.getBoundingClientRect().left + GUTTER_W + wrap.clientWidth / 2, factor);
}

function fitTimeline(rerender = true) {
  const wrap = $('timelineWrap');
  const maxEnd = state.schedule.max_end;
  const avail = wrap.clientWidth - 24;
  if (maxEnd != null && maxEnd > 0) {
    state.scale = clamp(avail / maxEnd, MIN_SCALE, 60);
  } else {
    state.scale = BASE_SCALE;
  }
  if (rerender) renderTimeline();
  wrap.scrollLeft = 0;
}

// ---------------- 拖拽与点击 ----------------

const drag = { mode: null, cueId: null, startClientX: 0, startVal: 0, moved: false };

function svgPoint(e) {
  const wrap = $('timelineWrap');
  const rect = $('timeline').getBoundingClientRect();
  return e.clientX - rect.left + wrap.scrollLeft;
}

function onTimelinePointerDown(e) {
  const target = e.target.closest('[data-action]');
  if (!target) {
    // 按住空白处平移
    const wrap = $('timelineWrap');
    startPan(e, wrap);
    return;
  }
  const group = target.closest('.tl-cue');
  if (!group) return;
  const cueId = Number(group.dataset.id) || group.dataset.id;
  const cue = state.cues.find((c) => c.id === cueId);
  if (!cue) return;
  const action = target.dataset.action;
  state.selectedCueId = cue.id;
  renderTimeline();
  renderCueTable();

  if (action === 'open') {
    drag.mode = 'click';
    drag.cueId = cue.id;
    drag.startClientX = e.clientX;
    drag.moved = false;
  } else if (action === 'move' && cue.mode === 'fixed') {
    drag.mode = 'move';
    drag.cueId = cue.id;
    drag.startVal = cue.fixed_at;
    drag.startClientX = e.clientX;
    drag.moved = false;
    $('timeline').setPointerCapture?.(e.pointerId);
  } else if (action === 'resize') {
    drag.mode = 'resize';
    drag.cueId = cue.id;
    drag.startVal = cue.duration;
    drag.startClientX = e.clientX;
    drag.moved = false;
    $('timeline').setPointerCapture?.(e.pointerId);
  }
}

const pan = { active: false, startX: 0, startY: 0, moved: false };
function startPan(e, wrap) {
  pan.active = true;
  pan.startX = e.clientX;
  pan.startY = e.clientY;
  pan.sx = wrap.scrollLeft;
  pan.sy = wrap.scrollTop;
  pan.moved = false;
  wrap.setPointerCapture?.(e.pointerId);
  wrap.style.cursor = 'grabbing';
}

let rafPending = false;
function onPointerMove(e) {
  if (pan.active) {
    const dx = e.clientX - pan.startX;
    const dy = e.clientY - pan.startY;
    if (Math.abs(dx) + Math.abs(dy) > 3) pan.moved = true;
    const wrap = $('timelineWrap');
    wrap.scrollLeft = pan.sx - dx;
    wrap.scrollTop = pan.sy - dy;
    return;
  }
  if (!drag.mode || drag.mode === 'click') {
    if (drag.mode === 'click' && Math.abs(e.clientX - drag.startClientX) > 4) {
      drag.mode = null; // 已判定为拖动，取消点击
    }
    return;
  }
  const cue = state.cues.find((c) => c.id === drag.cueId);
  if (!cue) return;
  const dx = e.clientX - drag.startClientX;
  if (Math.abs(dx) > 3) drag.moved = true;

  if (drag.mode === 'move') {
    cue.fixed_at = Math.round(clamp(drag.startVal + dx / state.scale, 0, 1e9) * 10) / 10;
  } else if (drag.mode === 'resize') {
    cue.duration = Math.round(clamp(drag.startVal + dx / state.scale, 0, 1e9) * 10) / 10;
  }
  state.schedule = computeSchedule(state.cues);
  if (!rafPending) {
    rafPending = true;
    requestAnimationFrame(() => { rafPending = false; renderTimeline(); renderCueTable(); });
  }
}

async function onPointerUp(e) {
  if (pan.active) {
    pan.active = false;
    const wrap = $('timelineWrap');
    wrap.style.cursor = '';
    return;
  }
  if (drag.mode === 'click' && !drag.moved) {
    const cue = state.cues.find((c) => c.id === drag.cueId);
    if (cue) openEditor(cue);
  } else if (!drag.moved && (drag.mode === 'move' || drag.mode === 'resize')) {
    // 对固定提示只是点了一下（没有发生拖动）→ 打开编辑器
    const cue = state.cues.find((c) => c.id === drag.cueId);
    if (cue) openEditor(cue);
  } else if (drag.moved && (drag.mode === 'move' || drag.mode === 'resize')) {
    const cue = state.cues.find((c) => c.id === drag.cueId);
    if (cue) {
      const payload = cuePayload(cue);
      try {
        const data = await api('PUT', `/api/cues/${cue.id}`, payload);
        applyDetail(data);
      } catch (err) {
        toast(err.message, 'error');
        if (state.currentShowId) {
          const fresh = await api('GET', `/api/shows/${state.currentShowId}`);
          applyDetail(fresh);
        }
      }
    }
  }
  drag.mode = null;
}

// ---------------- 编辑器 ----------------

let editorDraft = null; // 当前编辑中的提示（含临时 id NEW_ID）

function openEditor(cue) {
  const isNew = !cue;
  editorDraft = cue
    ? JSON.parse(JSON.stringify(cue))
    : {
        id: NEW_ID,
        cue_type: 'lighting',
        name: '',
        note: '',
        mode: state.cues.length ? 'after' : 'fixed',
        fixed_at: Math.round(((state.schedule.max_end ?? 0) + 5) * 10) / 10,
        depends_on: state.cues.length ? state.cues[state.cues.length - 1].id : null,
        delay: 0,
        duration: 5,
      };

  $('editorTitle').textContent = isNew ? '新增提示' : '编辑提示';
  $('deleteCueBtn').hidden = isNew;
  $('editorConflict').hidden = true;

  // 依赖下拉：编辑时排除自身（自依赖必成环）
  const depSel = $('fDepends');
  depSel.innerHTML = state.cues
    .filter((c) => c.id !== editorDraft.id)
    .map((c) => `<option value="${c.id}">${escapeHtml(`${TYPE_LABEL[c.cue_type]} · ${c.name}`)}</option>`)
    .join('');

  $('fName').value = editorDraft.name;
  $('fType').value = editorDraft.cue_type;
  $('fDuration').value = editorDraft.duration;
  $('fFixedAt').value = editorDraft.fixed_at ?? 0;
  $('fDelay').value = editorDraft.delay;
  if (editorDraft.depends_on != null &&
      state.cues.some((c) => c.id === editorDraft.depends_on && c.id !== editorDraft.id)) {
    depSel.value = String(editorDraft.depends_on);
  }
  $('fNote').value = editorDraft.note || '';
  setMode(editorDraft.mode);

  $('editorModal').hidden = false;
  $('fName').focus();
  updateLivePreview();
}

function closeEditor() {
  $('editorModal').hidden = true;
  editorDraft = null;
}

function setMode(mode) {
  editorDraft.mode = mode;
  document.querySelectorAll('input[name="mode"]').forEach((r) => {
    r.checked = r.value === mode;
  });
  $('fixedRow').hidden = mode !== 'fixed';
  $('afterRow').hidden = mode !== 'after';
  updateLivePreview();
}

function readEditorForm() {
  const d = editorDraft;
  d.name = $('fName').value.trim();
  d.cue_type = $('fType').value;
  d.duration = parseNum($('fDuration').value, 0);
  d.note = $('fNote').value;
  d.mode = document.querySelector('input[name="mode"]:checked').value;
  d.fixed_at = parseNum($('fFixedAt').value, 0);
  d.depends_on = $('fDepends').value ? Number($('fDepends').value) : null;
  d.delay = parseNum($('fDelay').value, 0);
}

function cuePayload(cue) {
  return {
    position: cue.position ?? 0,
    cue_type: cue.cue_type,
    name: cue.name,
    note: cue.note || '',
    mode: cue.mode,
    fixed_at: cue.mode === 'fixed' ? cue.fixed_at : null,
    depends_on: cue.mode === 'after' ? cue.depends_on : null,
    delay: cue.delay,
    duration: cue.duration,
  };
}

function updateLivePreview() {
  if (!editorDraft) return;
  readEditorForm();
  const box = $('livePreview');
  const text = $('livePreviewText');
  const conflictBox = $('editorConflict');

  if (!document.querySelector('input[name="mode"]:checked')) return;

  if (editorDraft.mode === 'after' && editorDraft.depends_on == null) {
    box.hidden = false;
    box.className = 'live-preview warn';
    text.textContent = '请选择一个被依赖的提示。';
    return;
  }

  const candidate = withDraft(state.cues, editorDraft);
  const sched = computeSchedule(candidate);
  const item = sched.items[editorDraft.id];
  box.hidden = false;

  if (item.state === 'cycle') {
    box.className = 'live-preview error';
    text.textContent = '该设置会形成循环依赖，保存将被拒绝。';
  } else if (item.state === 'unanchored') {
    box.className = 'live-preview warn';
    text.textContent = '依赖链上没有任何固定时间提示，暂无法确定执行时间（可保存）。';
  } else {
    box.className = 'live-preview';
    text.textContent = `开始 ${fmtTime(item.start)}，结束 ${fmtTime(item.end)}` +
      `（修改会实时联动所有后继提示）`;
  }
  conflictBox.hidden = true;
}

function renderConflictChains(readableChains) {
  const box = $('editorConflict');
  $('editorConflictChains').innerHTML = readableChains.map((chain) => {
    const html = chain.map((node, i) => {
      // 编辑中的新提示在后端用临时 id -1 表示
      const isDraft = node.id === -1;
      const isLoop = i === chain.length - 1;
      const name = isDraft ? `（当前编辑：${editorDraft.name || '未命名'}）` : node.name;
      const cls = isLoop ? ' class="loop-mark"' : '';
      return `<span${cls}>${escapeHtml(name)}</span>`;
    }).join(' → ');
    return `<div class="conflict-chain">${html}</div>`;
  }).join('');
  box.hidden = false;
}

async function submitEditor(e) {
  e.preventDefault();
  readEditorForm();
  if (!editorDraft.name) {
    toast('请填写提示名称', 'error');
    $('fName').focus();
    return;
  }
  if (editorDraft.mode === 'after' && editorDraft.depends_on == null) {
    toast('请选择被依赖的提示', 'error');
    return;
  }

  const isNew = editorDraft.id === NEW_ID;
  const url = isNew
    ? `/api/shows/${state.currentShowId}/cues`
    : `/api/cues/${editorDraft.id}`;
  try {
    const data = await api(isNew ? 'POST' : 'PUT', url, cuePayload(editorDraft));
    applyDetail(data);
    closeEditor();
    toast(isNew ? '提示已新增' : '提示已保存', 'success');
  } catch (err) {
    // 关键需求：保存被拒时保留用户当前编辑内容，指出完整冲突链
    if (err.status === 409 && err.data.conflict_chains) {
      renderConflictChains(err.data.conflict_chains);
    } else {
      toast(err.message, 'error');
    }
  }
}

async function deleteEditingCue() {
  if (!editorDraft || editorDraft.id === NEW_ID) return;
  if (!confirm(`确定删除提示「${editorDraft.name}」？`)) return;
  try {
    const data = await api('DELETE', `/api/cues/${editorDraft.id}`);
    applyDetail(data);
    closeEditor();
    toast('提示已删除', 'success');
  } catch (err) {
    if (err.status === 409 && err.data.dependents) {
      toast(`无法删除：仍被 ${err.data.dependents.length} 个提示依赖（${
        err.data.dependents.map((d) => d.name).join('、')}），请先改派`, 'error');
    } else {
      toast(err.message, 'error');
    }
  }
}

// ---------------- 演出管理 ----------------

let showModalMode = 'new';

function openShowModal(mode) {
  showModalMode = mode;
  $('showModalTitle').textContent = mode === 'new' ? '新建演出' : '重命名演出';
  $('sfName').value = mode === 'new' ? '' : (state.show?.name || '');
  $('sfDesc').value = mode === 'new' ? '' : (state.show?.description || '');
  $('showModal').hidden = false;
  $('sfName').focus();
}

async function submitShow(e) {
  e.preventDefault();
  const name = $('sfName').value.trim();
  const description = $('sfDesc').value.trim();
  if (!name) { toast('请填写演出名称', 'error'); return; }
  try {
    if (showModalMode === 'new') {
      const data = await api('POST', '/api/shows', { name, description });
      $('showModal').hidden = true;
      await loadShows(data.show.id);
      toast('演出已创建', 'success');
    } else {
      await api('PUT', `/api/shows/${state.currentShowId}`, { name, description });
      $('showModal').hidden = true;
      await loadShows(state.currentShowId);
      toast('已保存', 'success');
    }
  } catch (err) {
    toast(err.message, 'error');
  }
}

async function deleteCurrentShow() {
  if (!state.show) return;
  if (!confirm(`确定删除演出「${state.show.name}」及其全部提示？此操作不可恢复。`)) return;
  try {
    await api('DELETE', `/api/shows/${state.currentShowId}`);
    localStorage.removeItem(LS_SHOW);
    toast('演出已删除', 'success');
    await loadShows(null);
  } catch (err) {
    toast(err.message, 'error');
  }
}

// ---------------- 导入 / 导出 ----------------

function exportShow() {
  if (!state.currentShowId) { toast('请先选择演出', 'error'); return; }
  const a = document.createElement('a');
  a.href = `/api/shows/${state.currentShowId}/export`;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function importShow() {
  $('importFile').click();
}

async function onImportFile(e) {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  let json;
  try {
    json = JSON.parse(await file.text());
  } catch (_) {
    toast('文件不是合法的 JSON', 'error');
    return;
  }
  try {
    const data = await api('POST', '/api/import', json);
    await loadShows(data.show.id);
    toast(`已导入：${data.show.name}`, 'success');
  } catch (err) {
    if (err.status === 409 && err.data.conflict_chains) {
      const chains = err.data.conflict_chains
        .map((chain) => chain.map((n) => n.name).join(' → ')).join('；');
      toast(`导入被拒绝，存在循环依赖：${chains}`, 'error');
    } else {
      toast(`导入失败：${err.message}`, 'error');
    }
  }
}

// ---------------- 事件绑定 ----------------

function bindEvents() {
  $('showSelect').addEventListener('change', (e) => selectShow(Number(e.target.value)));
  $('newShowBtn').addEventListener('click', () => openShowModal('new'));
  $('renameShowBtn').addEventListener('click', () => openShowModal('rename'));
  $('deleteShowBtn').addEventListener('click', deleteCurrentShow);
  $('showForm').addEventListener('submit', submitShow);
  $('showCancelBtn').addEventListener('click', () => { $('showModal').hidden = true; });

  $('addCueBtn').addEventListener('click', () => {
    if (!state.currentShowId) { toast('请先新建一场演出', 'error'); return; }
    openEditor(null);
  });
  $('cueForm').addEventListener('submit', submitEditor);
  $('cancelCueBtn').addEventListener('click', closeEditor);
  $('deleteCueBtn').addEventListener('click', deleteEditingCue);

  document.querySelectorAll('input[name="mode"]').forEach((r) => {
    r.addEventListener('change', () => setMode(r.value));
  });
  ['fName', 'fType', 'fDuration', 'fFixedAt', 'fDepends', 'fDelay', 'fNote'].forEach((id) => {
    $(id).addEventListener('input', updateLivePreview);
    $(id).addEventListener('change', updateLivePreview);
  });

  const svg = $('timeline');
  const wrap = $('timelineWrap');
  svg.addEventListener('pointerdown', onTimelinePointerDown);
  window.addEventListener('pointermove', onPointerMove);
  window.addEventListener('pointerup', onPointerUp);
  wrap.addEventListener('scroll', () => {
    $('laneGutter').style.transform = `translateY(${-wrap.scrollTop}px)`;
  });
  wrap.addEventListener('wheel', (e) => {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    zoomAt(e.clientX, factor);
  }, { passive: false });

  $('zoomInBtn').addEventListener('click', () => zoomCenter(1.25));
  $('zoomOutBtn').addEventListener('click', () => zoomCenter(1 / 1.25));
  $('zoomFitBtn').addEventListener('click', () => fitTimeline(true));

  $('cueTable').addEventListener('click', (e) => {
    const row = e.target.closest('.cue-row');
    if (!row) return;
    const cue = state.cues.find((c) => c.id === Number(row.dataset.id));
    if (cue) openEditor(cue);
  });

  $('exportBtn').addEventListener('click', exportShow);
  $('importBtn').addEventListener('click', importShow);
  $('importFile').addEventListener('change', onImportFile);

  // 点遮罩关闭弹窗（保留内容不保存）
  $('editorModal').addEventListener('pointerdown', (e) => {
    if (e.target === $('editorModal')) closeEditor();
  });
  $('showModal').addEventListener('pointerdown', (e) => {
    if (e.target === $('showModal')) $('showModal').hidden = true;
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (!$('editorModal').hidden) closeEditor();
      if (!$('showModal').hidden) $('showModal').hidden = true;
    }
  });

  window.addEventListener('resize', () => renderTimeline());
}

// laneGutter 元素加入 DOM（覆盖在时间轴左侧）
function ensureGutter() {
  const wrap = $('timelineWrap');
  if (!$('laneGutter')) {
    const g = document.createElement('div');
    g.id = 'laneGutter';
    g.className = 'lane-gutter';
    wrap.appendChild(g);
  }
}

// ---------------- 启动 ----------------

(async function main() {
  ensureGutter();
  bindEvents();
  try {
    await loadShows();
  } catch (err) {
    toast(`加载失败：${err.message}`, 'error');
  }
})();
