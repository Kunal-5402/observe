"use strict";

// Category order is fixed: it sets lane order and the color slot of each category.
const CATS = {
  bash: "Bash",
  file_write: "File writes",
  file_read: "File reads",
  search: "Search",
  mcp: "MCP",
  web: "Web",
  agent: "Subagents",
  other: "Other",
};
const ORDER = Object.keys(CATS);
const AGENT_NAMES = { claude: "Claude Code", codex: "Codex" };
const STATUS = {
  ok: ["✓", "OK"],
  error: ["!", "Error"],
  running: ["…", "No result"],
  interrupted: ["–", "Interrupted"],
};
const SVG_NS = "http://www.w3.org/2000/svg";

const state = {
  agent: new URLSearchParams(location.search).get("agent") || "",
  sessions: [],
  current: null,
  data: null,
  tab: ["timeline", "graph", "events"].includes(new URLSearchParams(location.search).get("tab"))
    ? new URLSearchParams(location.search).get("tab") : "timeline",
  zoom: 1,
  catFilter: "",
};

// ---------- small helpers ----------

function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "class") node.className = v;
    else if (k === "style") node.style.cssText = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) node.append(c instanceof Node ? c : String(c));
  return node;
}

function s(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
}

const catColor = (c) => `var(--cat-${CATS[c] ? c : "other"})`;
const swatch = (c) => h("span", { class: "sw", style: `background:${catColor(c)}` });

function fmtDur(ms) {
  if (ms == null || ms < 0) return "–";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const sec = ms / 1000;
  if (sec < 60) return `${sec.toFixed(sec < 10 ? 1 : 0)}s`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m}m ${String(Math.round(sec % 60)).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

function fmtNum(n) {
  if (n == null) return "–";
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e4) return `${Math.round(n / 1e3)}K`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return n.toLocaleString();
}

const fmtClock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const fmtDate = (t) => new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

function relTime(t) {
  const d = Date.now() / 1000 - t;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return fmtDate(t);
}

const basename = (p) => (p || "").split("/").filter(Boolean).pop() || p || "";
const truncate = (str, n) => (str && str.length > n ? str.slice(0, n - 1) + "…" : str || "");

async function api(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

// ---------- tooltip ----------

const tip = document.getElementById("tooltip");

function showTip(evt, title, body, meta, cat) {
  tip.replaceChildren(
    h("div", { class: "t-title" }, cat ? swatch(cat) : null, title),
    body ? h("div", { class: "t-body" }, truncate(body, 400)) : null,
    meta ? h("div", { class: "t-meta" }, meta) : null,
  );
  tip.classList.add("show");
  moveTip(evt);
}

function moveTip(evt) {
  const pad = 14;
  const r = tip.getBoundingClientRect();
  let x = evt.clientX + pad;
  let y = evt.clientY + pad;
  if (x + r.width > innerWidth - 8) x = evt.clientX - r.width - pad;
  if (y + r.height > innerHeight - 8) y = evt.clientY - r.height - pad;
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${Math.max(8, y)}px`;
}

const hideTip = () => tip.classList.remove("show");

function statusLabel(st) {
  const [icon, label] = STATUS[st] || ["", st || ""];
  return h("span", { class: `status ${st}` }, h("i", { "aria-hidden": "true" }, icon), label);
}

// ---------- sessions ----------

async function loadSessions() {
  const q = state.agent ? `?agent=${state.agent}` : "";
  state.sessions = await api(`/api/sessions${q}`);
  renderSessionList();
  const wanted = location.hash.slice(1);
  if (wanted && wanted !== state.current) return openSession(wanted);
  if (!state.current && state.sessions.length) return openSession(state.sessions[0].id);
  if (!state.sessions.length) renderEmpty();
}

function renderSessionList() {
  const list = document.getElementById("session-list");
  const query = document.getElementById("filter").value.trim().toLowerCase();
  const rows = state.sessions.filter(
    (x) => !query || [x.title, x.cwd, x.id, x.model].some((v) => (v || "").toLowerCase().includes(query)),
  );
  list.replaceChildren(
    ...rows.map((x) =>
      h(
        "li",
        { "aria-current": x.id === state.current ? "true" : "false", onclick: () => openSession(x.id) },
        h("div", { class: "s-title" }, x.title || `Session ${x.id.slice(0, 8)}`),
        h(
          "div",
          { class: "s-meta" },
          h("span", { class: "badge" }, AGENT_NAMES[x.agent] || x.agent),
          h("span", {}, basename(x.cwd) || "–"),
          h("span", {}, relTime(x.started_at)),
          h("span", { class: "num" }, `${x.tool_call_count} tools`),
        ),
      ),
    ),
  );
  if (!rows.length) list.append(h("li", { class: "rank-empty" }, "No sessions match."));
}

function renderEmpty() {
  document.getElementById("main").replaceChildren(
    h(
      "div",
      { class: "empty" },
      h("p", {}, "No sessions recorded yet."),
      h("p", {}, "Run ", h("code", {}, "observe install"), ", then start a new Claude Code or Codex session."),
    ),
  );
}

async function openSession(id) {
  state.current = id;
  if (location.hash.slice(1) !== id) history.replaceState(null, "", `${location.search}#${id}`);
  renderSessionList();
  try {
    state.data = await api(`/api/sessions/${encodeURIComponent(id)}`);
  } catch {
    document.getElementById("main").replaceChildren(h("div", { class: "empty" }, "Session not found."));
    return;
  }
  renderMain();
}

// ---------- session view ----------

function activeSegments(events) {
  // Group event timestamps that are at most GAP seconds apart. Longer gaps become fixed-width breaks,
  // so a tool call that waits a day for the user does not flatten the rest of the timeline.
  const GAP = 60;
  const points = events.flatMap((e) => (e.ended_at != null ? [e.started_at, e.ended_at] : [e.started_at]));
  points.sort((a, b) => a - b);
  const segs = [];
  for (const t of points) {
    const last = segs[segs.length - 1];
    if (last && t <= last.end + GAP) last.end = t;
    else segs.push({ start: t, end: t });
  }
  return segs;
}

function renderMain() {
  const { session: se, events, summary } = state.data;
  const segs = activeSegments(events);
  const activeMs = segs.reduce((a, x) => a + (x.end - x.start), 0) * 1000;
  const wallMs = ((se.ended_at || se.started_at) - se.started_at) * 1000;
  const tokensIn = se.input_tokens + se.cache_read_tokens + se.cache_write_tokens;

  const tile = (label, value, note) =>
    h("div", { class: "tile" }, h("div", { class: "tile-label" }, label), h("div", { class: "tile-value" }, value),
      note ? h("div", { class: "tile-note" }, note) : null);

  const tabs = [["timeline", "Timeline"], ["graph", "Graph"], ["events", "Events"]];
  const panel = h("div", { class: "panel", id: "panel" });

  const tools = h("div", { class: "tools" });
  if (state.tab === "timeline") {
    tools.append(
      h("div", { class: "seg", role: "group", "aria-label": "Zoom" },
        ...[1, 2, 4, 8].map((z) =>
          h("button", { "aria-pressed": String(state.zoom === z), onclick: () => { state.zoom = z; renderMain(); } }, `${z}×`))),
    );
  } else if (state.tab === "events") {
    const sel = h("select", { class: "input", style: "width:auto;padding:3px 8px", "aria-label": "Category",
      onchange: (e) => { state.catFilter = e.target.value; renderPanel(panel); } },
      h("option", { value: "" }, "All events"),
      ...ORDER.filter((c) => summary.categories[c]).map((c) =>
        h("option", { value: c, selected: state.catFilter === c }, CATS[c])));
    tools.append(sel);
  }

  document.getElementById("main").replaceChildren(
    h("div", { class: "s-head" },
      h("h1", {}, truncate(se.title, 140) || `Session ${se.id.slice(0, 8)}`),
      h("div", { class: "s-sub" },
        h("span", { class: "badge" }, AGENT_NAMES[se.agent] || se.agent),
        se.model ? h("span", {}, se.model) : null,
        h("span", {}, fmtDate(se.started_at)),
        se.cwd ? h("span", { class: "mono", title: se.cwd }, se.cwd) : null,
        h("span", { class: "mono", title: "Session id" }, se.id.slice(0, 8)))),
    h("div", { class: "tiles" },
      tile("Active time", fmtDur(activeMs), `wall clock ${fmtDur(wallMs)}`),
      tile("Tool calls", fmtNum(se.tool_call_count), `${se.prompt_count} prompts`),
      tile("Errors", fmtNum(se.error_count), se.tool_call_count ? `${Math.round((100 * se.error_count) / se.tool_call_count)}% of calls` : null),
      tile("Files changed", fmtNum(summary.files_written_total), `${summary.files_read_total} read`),
      tile("Tokens in", fmtNum(tokensIn), se.cache_read_tokens ? `${fmtNum(se.cache_read_tokens)} from cache` : "from transcript"),
      tile("Tokens out", fmtNum(se.output_tokens), null)),
    h("div", { class: "card" },
      h("div", { class: "tabs", role: "tablist" },
        ...tabs.map(([key, label]) =>
          h("button", { role: "tab", "aria-selected": String(state.tab === key),
            onclick: () => { state.tab = key; renderMain(); } }, label)),
        tools),
      panel),
    renderSummary(summary),
  );
  renderPanel(panel);
}

function renderPanel(panel) {
  const { events } = state.data;
  panel.replaceChildren();
  if (state.tab === "timeline") renderTimeline(panel, events);
  else if (state.tab === "graph") renderGraph(panel, state.data.graph);
  else renderTable(panel, events);
}

function legend(cats, ...items) {
  return h("div", { class: "legend" }, ...cats.map((c) => h("span", {}, swatch(c), CATS[c])), ...items);
}

// ---------- timeline ----------

function renderTimeline(host, events) {
  const calls = events.filter((e) => e.kind === "tool_call");
  const marks = events.filter((e) => ["prompt", "interrupt", "compact", "subagent"].includes(e.kind));
  if (!calls.length) {
    host.append(h("div", { class: "empty" }, "No tool calls in this session."));
    return;
  }

  const LABEL = 128, RIGHT = 16, AXIS = 30, ROW = 18, BAR = 12, LANE_PAD = 8, GAPW = 18, MINW = 10;
  const baseW = Math.max(host.clientWidth - 32, 640) * state.zoom;
  const segs = activeSegments([...calls, ...marks]);
  const totalSec = segs.reduce((a, x) => a + (x.end - x.start), 0);
  const avail = baseW - LABEL - RIGHT - GAPW * (segs.length - 1) - MINW * segs.length;
  const k = totalSec > 0 ? Math.max(avail, totalSec * 0.5) / totalSec : 0;
  let cursor = LABEL;
  for (const g of segs) {
    g.x0 = cursor;
    g.x1 = cursor + (g.end - g.start) * k + MINW;
    cursor = g.x1 + GAPW;
  }
  const width = cursor - GAPW + RIGHT;
  const tx = (t) => {
    for (let i = 0; i < segs.length; i++) {
      const g = segs[i];
      if (t <= g.end) {
        if (t >= g.start || i === 0) return g.x0 + Math.max(0, t - g.start) * k;
        const prev = segs[i - 1]; // t falls in an idle gap: spread it over the break
        return prev.x1 + ((t - prev.end) / (g.start - prev.end)) * (g.x0 - prev.x1);
      }
    }
    return segs[segs.length - 1].x1 - MINW;
  };

  // Pack each category lane into sub-rows so that overlapping calls do not hide each other.
  const lanes = ORDER.filter((c) => calls.some((e) => (e.category || "other") === c)).map((c) => ({ cat: c, rows: [] }));
  const laneOf = Object.fromEntries(lanes.map((l) => [l.cat, l]));
  const bars = [];
  for (const e of [...calls].sort((a, b) => a.started_at - b.started_at)) {
    const lane = laneOf[e.category || "other"];
    const x0 = tx(e.started_at);
    const x1 = Math.max(tx(e.ended_at ?? e.started_at), x0 + 3);
    let row = lane.rows.findIndex((end) => end + 2 <= x0);
    if (row < 0) row = lane.rows.push(0) - 1;
    lane.rows[row] = x1;
    bars.push({ e, lane, row, x0, x1 });
  }
  let y = AXIS + 6;
  for (const l of lanes) {
    l.y = y;
    l.h = l.rows.length * ROW + LANE_PAD;
    y += l.h;
  }
  const height = y + 6;

  const svg = s("svg", { width, height, role: "img", "aria-label": "Tool call timeline" });

  // Idle gaps.
  segs.slice(1).forEach((g, i) => {
    const prev = segs[i];
    const gx = prev.x1 + 2, gw = GAPW - 4;
    const idle = (g.start - prev.end) * 1000;
    svg.append(
      s("rect", { x: gx, y: AXIS - 4, width: gw, height: height - AXIS, fill: "var(--surface-2)", rx: 2,
        onmousemove: (ev) => showTip(ev, "Idle", null, `${fmtDur(idle)} with no new events`),
        onmouseleave: hideTip }),
    );
  });

  // Lane labels and separators.
  for (const l of lanes) {
    const count = calls.filter((e) => (e.category || "other") === l.cat).length;
    svg.append(
      s("line", { x1: 0, x2: width, y1: l.y + l.h, y2: l.y + l.h, stroke: "var(--grid)" }),
      s("rect", { x: 0, y: l.y + 4, width: 10, height: 10, rx: 3, fill: catColor(l.cat) }),
      s("text", { x: 16, y: l.y + 13, "font-size": 12, fill: "var(--ink)" }, CATS[l.cat]),
      s("text", { x: LABEL - 12, y: l.y + 13, "font-size": 11, fill: "var(--muted)", "text-anchor": "end" }, count),
    );
  }

  // Axis: a clock label at each segment start, then more labels inside long segments.
  svg.append(s("line", { x1: LABEL, x2: width - RIGHT, y1: AXIS, y2: AXIS, stroke: "var(--axis)" }));
  let lastLabel = -Infinity;
  const step = 140 / (k || 1);
  for (const g of segs) {
    const ticks = [g.start];
    if (k > 0) for (let t = g.start + step; t < g.end; t += step) ticks.push(t);
    for (const t of ticks) {
      const x = tx(t);
      if (x - lastLabel < 96) continue;
      lastLabel = x;
      svg.append(
        s("line", { x1: x, x2: x, y1: AXIS, y2: AXIS + 4, stroke: "var(--axis)" }),
        s("text", { x, y: AXIS - 10, "font-size": 11, fill: "var(--muted)", class: "num",
          "text-anchor": x > width - 90 ? "end" : "start" }, fmtClock(t)),
      );
    }
  }

  // Prompt and other point markers.
  for (const m of marks) {
    const x = tx(m.started_at);
    const isPrompt = m.kind === "prompt";
    svg.append(
      s("line", { x1: x, x2: x, y1: AXIS, y2: height - 6, stroke: "var(--ink-2)", "stroke-opacity": isPrompt ? 0.35 : 0.2 }),
      s("circle", { cx: x, cy: AXIS, r: 4.5, fill: isPrompt ? "var(--ink)" : "var(--muted)", stroke: "var(--surface)", "stroke-width": 2,
        style: "cursor:pointer",
        onmousemove: (ev) => showTip(ev, isPrompt ? "Prompt" : m.summary, isPrompt ? m.summary : null, fmtClock(m.started_at)),
        onmouseleave: hideTip, onclick: () => openEvent(m.id) }),
    );
  }

  // Tool call bars, each with a larger invisible hit target.
  for (const b of bars) {
    const by = b.lane.y + b.row * ROW + (ROW - BAR) / 2 + 2;
    const e = b.e;
    const meta = `${fmtClock(e.started_at)} · ${fmtDur(e.duration_ms)} · ${(STATUS[e.status] || [, e.status])[1]}`;
    const enter = (ev) => showTip(ev, e.tool_name, e.target, meta, e.category);
    const g = s("g", { onmousemove: enter, onmouseleave: hideTip, onclick: () => openEvent(e.id) });
    g.append(
      s("rect", { class: "tl-hit", x: b.x0 - 3, y: by - 3, width: b.x1 - b.x0 + 6, height: BAR + 6 }),
      s("rect", { class: "tl-bar", x: b.x0, y: by, width: b.x1 - b.x0, height: BAR, rx: 3, fill: catColor(e.category),
        "fill-opacity": e.status === "running" ? 0.4 : 1 }),
    );
    if (e.status === "error") {
      g.append(s("circle", { cx: b.x1 + 1, cy: by, r: 4, fill: "var(--critical)", stroke: "var(--surface)", "stroke-width": 2 }));
    }
    svg.append(g);
  }

  host.append(
    legend(lanes.map((l) => l.cat),
      h("span", {}, h("span", { class: "sw", style: "background:var(--ink);border-radius:50%" }), "Prompt"),
      h("span", {}, h("span", { class: "sw", style: "background:var(--critical);border-radius:50%" }), "Error"),
      h("span", {}, h("span", { class: "sw", style: "background:var(--surface-2);box-shadow:inset 0 0 0 1px var(--axis)" }), "Idle gap over 60s (compressed)")),
    h("div", { class: "timeline-scroll" }, svg),
  );
}

// ---------- graph ----------

function renderGraph(host, graph) {
  if (graph.nodes.length <= 1) {
    host.append(h("div", { class: "empty" }, "No tool calls in this session."));
    return;
  }
  const W = Math.max(host.clientWidth - 32, 480);
  const H = Math.max(520, Math.min(820, 260 + graph.nodes.length * 4));
  const nodes = graph.nodes.map((n) => ({ ...n, vx: 0, vy: 0 }));
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const links = graph.links.map((l) => ({ ...l, s: byId[l.source], t: byId[l.target] })).filter((l) => l.s && l.t);

  const radius = (n) =>
    n.type === "session" ? 14 : n.type === "hub" ? Math.min(22, 7 + 1.6 * Math.sqrt(n.count)) :
    n.type === "group" ? Math.min(16, 6 + 1.4 * Math.sqrt(n.count)) : Math.min(12, 4 + 1.2 * Math.sqrt(n.count));
  nodes.forEach((n) => (n.r = radius(n)));

  // Start layout: hubs on a circle, children near their parent.
  const parentOf = {};
  links.forEach((l) => (parentOf[l.t.id] ??= l.s));
  const hubs = nodes.filter((n) => n.type === "hub");
  hubs.forEach((n, i) => {
    const a = (2 * Math.PI * i) / hubs.length;
    n.x = Math.cos(a) * 150;
    n.y = Math.sin(a) * 150;
  });
  const session = byId.session;
  session.x = session.y = 0;
  let seed = 1;
  const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647) - 0.5;
  for (const n of nodes) {
    if (n.x != null) continue;
    const p = parentOf[n.id] && parentOf[n.id].x != null ? parentOf[n.id] : session;
    const a = Math.atan2(p.y, p.x) + rand() * 2;
    n.x = p.x * 1.4 + Math.cos(a) * 60;
    n.y = p.y * 1.4 + Math.sin(a) * 60;
  }

  // Force layout: pairwise repulsion, springs on links, weak gravity. Session stays at the center.
  const ITER = 320;
  for (let it = 0; it < ITER; it++) {
    const alpha = 1 - it / ITER;
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        let dx = b.x - a.x, dy = b.y - a.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = rand(); dy = rand(); d2 = 1; }
        const min = a.r + b.r + 10;
        const f = (1400 * alpha) / d2 + (d2 < min * min ? 0.5 : 0);
        const d = Math.sqrt(d2);
        a.vx -= (dx / d) * f; a.vy -= (dy / d) * f;
        b.vx += (dx / d) * f; b.vy += (dy / d) * f;
      }
    }
    for (const l of links) {
      const dx = l.t.x - l.s.x, dy = l.t.y - l.s.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const len = l.s.type === "session" ? 140 : 36 + l.s.r + l.t.r;
      const f = (d - len) * 0.06 * alpha;
      l.s.vx += (dx / d) * f; l.s.vy += (dy / d) * f;
      l.t.vx -= (dx / d) * f; l.t.vy -= (dy / d) * f;
    }
    for (const n of nodes) {
      n.vx -= n.x * 0.004 * alpha;
      n.vy -= n.y * 0.004 * alpha;
      if (n === session) { n.vx = n.vy = 0; continue; }
      n.x += n.vx; n.y += n.vy;
      n.vx *= 0.55; n.vy *= 0.55;
    }
  }

  // Fit the layout into the view box, leaving room for labels.
  const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const PAD = 70;
  const scale = Math.min((W - 2 * PAD) / (maxX - minX || 1), (H - 2 * PAD) / (maxY - minY || 1), 2.5);
  const ox = W / 2 - ((minX + maxX) / 2) * scale, oy = H / 2 - ((minY + maxY) / 2) * scale;
  nodes.forEach((n) => { n.px = n.x * scale + ox; n.py = n.y * scale + oy; });

  // Label every hub and group, plus the busiest leaves.
  const labeled = new Set(nodes.filter((n) => n.type !== "leaf").map((n) => n.id));
  nodes.filter((n) => n.type === "leaf").sort((a, b) => b.count - a.count).slice(0, 36).forEach((n) => labeled.add(n.id));

  const svg = s("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Session resource graph" });
  const linkLayer = s("g"), nodeLayer = s("g"), labelLayer = s("g", { "pointer-events": "none" });
  svg.append(linkLayer, nodeLayer, labelLayer);

  for (const l of links) {
    l.el = s("line", { stroke: "var(--axis)", "stroke-width": Math.min(4, 1 + Math.log2(l.count)), "stroke-linecap": "round" });
    linkLayer.append(l.el);
  }
  const place = () => {
    for (const l of links) {
      l.el.setAttribute("x1", l.s.px); l.el.setAttribute("y1", l.s.py);
      l.el.setAttribute("x2", l.t.px); l.el.setAttribute("y2", l.t.py);
    }
    for (const n of nodes) {
      n.el.setAttribute("transform", `translate(${n.px},${n.py})`);
      if (n.label_el) {
        n.label_el.setAttribute("x", n.px + (n.px >= W / 2 ? n.r + 5 : -n.r - 5));
        n.label_el.setAttribute("y", n.py + 4);
        n.label_el.setAttribute("text-anchor", n.px >= W / 2 ? "start" : "end");
      }
    }
  };

  for (const n of nodes) {
    const fill = n.type === "session" ? "var(--ink)" : n.type === "more" ? "var(--surface-2)" : catColor(n.category);
    const circle = s("circle", { r: n.r, fill, stroke: n.type === "more" ? "var(--axis)" : "var(--surface)", "stroke-width": 2 });
    n.el = s("g", { class: "node" }, circle, s("circle", { r: Math.max(n.r + 4, 10), fill: "transparent" }));
    const title = n.type === "session" ? "Session" : n.type === "hub" ? n.label : n.label;
    const meta = n.type === "session" ? n.label : `${n.count} call${n.count === 1 ? "" : "s"}`;
    n.el.addEventListener("mousemove", (ev) => showTip(ev, truncate(title, 80), n.type === "leaf" && n.label.length > 30 ? n.label : null, meta,
      n.type === "session" || n.type === "more" ? null : n.category));
    n.el.addEventListener("mouseleave", hideTip);
    dragNode(svg, n, place);
    nodeLayer.append(n.el);
    if (labeled.has(n.id) && n.type !== "session") {
      const text = n.type === "leaf" && n.category.startsWith("file") ? basename(n.label) : truncate(n.label, 28);
      n.label_el = s("text", { "font-size": n.type === "hub" ? 12 : 11, "font-weight": n.type === "hub" ? 600 : 400,
        fill: n.type === "hub" ? "var(--ink)" : "var(--ink-2)", "paint-order": "stroke", stroke: "var(--surface)",
        "stroke-width": 3, "stroke-linejoin": "round" }, text);
      labelLayer.append(n.label_el);
    }
  }
  place();

  const cats = ORDER.filter((c) => nodes.some((n) => n.type === "hub" && n.category === c));
  host.append(legend(cats), h("div", { class: "graph" }, svg));
}

function dragNode(svg, n, place) {
  n.el.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    hideTip();
    n.el.setPointerCapture(ev.pointerId);
    const pt = svg.createSVGPoint();
    const move = (e) => {
      pt.x = e.clientX; pt.y = e.clientY;
      const p = pt.matrixTransform(svg.getScreenCTM().inverse());
      n.px = p.x; n.py = p.y;
      place();
    };
    const up = () => {
      n.el.removeEventListener("pointermove", move);
      n.el.removeEventListener("pointerup", up);
    };
    n.el.addEventListener("pointermove", move);
    n.el.addEventListener("pointerup", up);
  });
}

// ---------- table ----------

function renderTable(host, events) {
  const start = state.data.session.started_at;
  const rows = events.filter((e) => !state.catFilter || e.category === state.catFilter);
  const kindLabel = { prompt: "Prompt", session_start: "Session", session_end: "Session", stop: "Turn end",
    subagent: "Subagent", compact: "Compaction", notification: "Notification", interrupt: "Interrupt", permission: "Permission" };
  host.append(
    h("div", { class: "table-wrap" },
      h("table", {},
        h("thead", {}, h("tr", {}, ...["Time", "+", "Type", "Tool", "Target", "Duration", "Status"].map((t) => h("th", {}, t)))),
        h("tbody", {},
          ...rows.map((e) =>
            h("tr", { onclick: () => openEvent(e.id) },
              h("td", { class: "num" }, fmtClock(e.started_at)),
              h("td", { class: "num", style: "color:var(--muted)" }, fmtDur((e.started_at - start) * 1000)),
              h("td", {}, e.kind === "tool_call"
                ? h("span", { class: "status" }, swatch(e.category), CATS[e.category] || e.category)
                : kindLabel[e.kind] || e.kind),
              h("td", {}, e.tool_name || ""),
              h("td", { class: "target", title: e.target || e.summary || "" }, e.target || (e.kind !== "tool_call" ? e.summary : "") || ""),
              h("td", { class: "num" }, e.kind === "tool_call" ? fmtDur(e.duration_ms) : ""),
              h("td", {}, e.kind === "tool_call" ? statusLabel(e.status) : "")))))),
  );
}

// ---------- summary cards ----------

function renderSummary(sum) {
  const card = (title, items, total, cat) => {
    const max = Math.max(1, ...items.map((i) => i.count));
    return h("div", { class: "card" },
      h("h3", {}, title, h("span", {}, total != null ? `${total} total` : "")),
      items.length
        ? h("ul", { class: "rank" }, ...items.map((i) =>
            h("li", { title: i.name },
              h("span", { class: "name" }, i.name),
              h("span", { class: "count" }, i.count),
              h("div", { class: "bar" }, h("div", { style: `width:${(100 * i.count) / max}%;background:${catColor(cat)}` })))))
        : h("div", { class: "rank-empty" }, "None"));
  };
  return h("div", { class: "summary" },
    card("Files changed", sum.files_written, sum.files_written_total, "file_write"),
    card("Files read", sum.files_read, sum.files_read_total, "file_read"),
    card("Commands", sum.commands, null, "bash"),
    card("MCP servers", sum.mcp_servers, null, "mcp"));
}

// ---------- event drawer ----------

const drawer = document.getElementById("drawer");

async function openEvent(id) {
  hideTip();
  const e = await api(`/api/events/${id}`);
  const d = e.detail || {};
  const pre = (v) => h("pre", {}, typeof v === "string" ? v : JSON.stringify(v, null, 2));
  const rows = [
    ["Type", e.kind === "tool_call" ? h("span", { class: "status" }, swatch(e.category), CATS[e.category] || e.category) : e.kind],
    ["Tool", e.tool_name],
    ["Target", e.target],
    ["Started", `${fmtClock(e.started_at)} (${new Date(e.started_at * 1000).toLocaleDateString()})`],
    ["Duration", e.kind === "tool_call" ? fmtDur(e.duration_ms) : null],
    ["Status", e.kind === "tool_call" ? statusLabel(e.status) : null],
    ["Source", e.source === "transcript" ? "Transcript (no hook for this tool)" : null],
    ["Tool use id", e.tool_use_id],
  ].filter(([, v]) => v != null && v !== "");
  document.getElementById("drawer-title").textContent = e.kind === "tool_call" ? e.tool_name : e.summary || e.kind;
  document.getElementById("drawer-body").replaceChildren(
    h("dl", {}, ...rows.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
    e.files.length ? h("h3", {}, "Files") : null,
    e.files.length ? pre(e.files.map((f) => `${f.op.padEnd(6)} ${f.path}`).join("\n")) : null,
    d.prompt ? h("h3", {}, "Prompt") : null,
    d.prompt ? pre(d.prompt) : null,
    d.input !== undefined ? h("h3", {}, "Input") : null,
    d.input !== undefined ? pre(d.input) : null,
    d.error ? h("h3", {}, "Error") : null,
    d.error ? pre(d.error) : null,
    d.response != null ? h("h3", {}, "Response (excerpt)") : null,
    d.response != null ? pre(d.response) : null,
    e.kind !== "tool_call" && !d.prompt && Object.keys(d).length ? h("h3", {}, "Payload") : null,
    e.kind !== "tool_call" && !d.prompt && Object.keys(d).length ? pre(d) : null,
  );
  drawer.setAttribute("aria-hidden", "false");
}

document.getElementById("drawer-close").addEventListener("click", () => drawer.setAttribute("aria-hidden", "true"));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") drawer.setAttribute("aria-hidden", "true"); });

// ---------- controls and live refresh ----------

document.querySelectorAll("#agent-filter button").forEach((b) => {
  b.setAttribute("aria-pressed", String(b.dataset.agent === state.agent));
  b.addEventListener("click", () => {
    state.agent = b.dataset.agent;
    document.querySelectorAll("#agent-filter button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    const q = state.agent ? `?agent=${state.agent}` : "";
    state.current = null;
    history.replaceState(null, "", `${location.pathname}${q}`);
    loadSessions();
  });
});
document.getElementById("filter").addEventListener("input", renderSessionList);
document.getElementById("refresh").addEventListener("click", () => refresh(true));
window.addEventListener("hashchange", () => { const id = location.hash.slice(1); if (id && id !== state.current) openSession(id); });

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => state.data && renderPanel(document.getElementById("panel")), 150);
});

async function refresh(force) {
  const before = state.sessions.find((x) => x.id === state.current);
  await loadSessions();
  const after = state.sessions.find((x) => x.id === state.current);
  const changed = before && after && (before.ended_at !== after.ended_at || before.tool_call_count !== after.tool_call_count);
  if (state.current && (force || changed)) {
    state.data = await api(`/api/sessions/${encodeURIComponent(state.current)}`);
    renderMain();
  }
}

setInterval(() => {
  if (document.getElementById("live").checked && !document.hidden && drawer.getAttribute("aria-hidden") === "true") refresh(false);
}, 5000);

loadSessions().catch((err) => {
  document.getElementById("main").replaceChildren(h("div", { class: "empty" }, `Could not load sessions: ${err.message}`));
});
