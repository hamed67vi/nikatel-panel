/* PasarGuard nodes, in the window of the server they run on (1.48.0; the owner, 2026-10-14: "merge Foreign and Nodes: one row for every foreign server, and the node's data and all its operations
   in the window of the server"). The Servers page lists the servers; a server that carries a node shows the panel's name as a chip in its row, and its window has a Node view: what the panel knows about the
   node, and everything that can be done to it (log, usage, curves, certificate, edit, restart, enable / disable, update, delete). The log, the usage, a form ... open INSIDE the window with a Back link.
   A node that runs on no server of ours is in "Nodes outside our servers" (the Nodes... menu of the Servers page, pg_srvnode.js); the same Node view is its window.
   Everything is a route first (/api/panels/{id}/pg/nodes...); this file only calls them. ONE context: pgNodesS = the panel the actions work on (pgNodesUse sets it) and what was read from it.
   Uses the helpers of app.js: $, api, esc, toast, showModal, closeModal, morphHtml, gconfirm, gprompt, followStory, fmtB, ago, SI, state and of pg_common.js (pgApi, pgDo). */
const pgNodesS = {id: 0, data: null, runs: [], bk: []};
const pgNodesC = {};      // panel id -> {at, data}: the answer of GET .../pg/nodes of each panel (the Node view reads again when it is older than 20 s)
const pgNodesGB = 1024 ** 3;
const pgNodesNode = id => ((pgNodesS.data || {}).nodes || []).find(n => n.id === +id);
const pgNodesFreeSrv = id => ((pgNodesS.data || {}).free || []).find(s => s.id === +id);
const pgNodesKind = {edit: "before an edit", delete: "before a delete", cert: "before a certificate change", restore: "before a restore"};
const pgNodesV = (f, k) => f.elements[k] ? f.elements[k].value : "";      // a field of a form by its name (a field called "name" or "action" would hide the form's own properties)
const pgNodesUuid = () => crypto.randomUUID ? crypto.randomUUID() : "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, c => { const r = crypto.getRandomValues(new Uint8Array(1))[0] & 15; return (c === "x" ? r : (r & 3) | 8).toString(16); });
const pgNodesDate = ts => ts ? new Date(ts * 1000).toISOString().slice(0, 10) : "—";
const pgNodesCopy = async text => {
  try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* no clipboard permission: the old way */ }
  const t = document.createElement("textarea"); t.value = text; document.body.appendChild(t); t.select();
  let ok = false; try { ok = document.execCommand("copy"); } catch (e) { ok = false; } t.remove(); return ok;
};

/* ---- reading: the panel is asked for its nodes when a Node view opens and again when what was read is older than 20 seconds (and after every change) */
window.pgNodesUse = pid => { pgNodesS.id = +pid; const c = pgNodesC[+pid]; pgNodesS.data = c ? c.data : null; };      // the actions below work on this panel
window.pgNodesLoad = async (pid, force) => {
  pid = +pid; const c = pgNodesC[pid];
  if (!force && c && c.data && Date.now() - c.at < 20000) return c.data;
  const d = await pgApi(pid, "/nodes");
  pgNodesC[pid] = {at: Date.now(), data: d}; if (pgNodesS.id === pid) pgNodesS.data = d;
  return d;
};
const pgNodesRuns = async () => { try { pgNodesS.runs = (await api("/api/actions?kinds=pgnode&limit=3")).actions || []; } catch (e) { pgNodesS.runs = []; } };
const pgNodesRunHtml = name => {      // the run of "make this server a node" that is going for this server, or the one that ended in the past quarter of an hour: one line, with the way back into its story
  const now = Date.now() / 1000, a = (pgNodesS.runs || []).find(x => x.ref === `node ${name}` && (x.done ? now - x.finished_at < 900 : now - x.ts < 2400)); if (!a) return "";      // (a run that never ended - the panel was restarted in the middle - is not "running" after 40 minutes)
  const last = ((a.story || []).filter(x => x.trim()).slice(-1)[0] || "").trim();
  return `<div class="pg-nodes-run ${a.done ? (a.ok ? "ok" : "bad") : ""}">${a.done ? (a.ok ? "✓" : "✕") : '<i class="spin"></i>'}<b>${esc(a.ref)}</b><span class="mut" title="${esc(last)}">${esc(last.slice(0, 150))}</span><button class="sm" onclick="pgNodesStory(${a.id})">${a.done ? "Read" : "Follow"}</button></div>`;
};
window.pgNodesStory = async aid => {      // the story of a run, followed until it is done (also one that is done: it shows at once)
  pgNodesModal("<b>Making a server a node</b>", '<div id="pg-nodes-story" class="fix pg-nodes-story"><div class="fx-line run"><i></i>Reading…</div></div><div class="frm-act"><small class="mut">It keeps running if you close this window: the Node view of the server shows it until it is done.</small><span class="sp"></span><button class="sm" onclick="closeModal()">Close</button></div>', "wide");
  const fin = await followStory(aid, $("#pg-nodes-story"), 1000);
  await pgNodesRuns();
  pgNodesAfter();
  return fin;
};
/* after a change: read the panel again now (that also refreshes the copy the Servers page shows), and once more when the node has had time to connect */
const pgNodesAfter = async () => {
  const pid = pgNodesS.id, again = async () => { if (pgNodesS.id !== pid) return; try { await pgNodesLoad(pid, true); } catch (e) { /* the view says so */ } pgNodeViewDraw(); if (window.pgSrvLoad) pgSrvLoad().then(() => pgSrvRedraw()); };
  await again(); setTimeout(again, 6000);
};

/* ---- the windows of the node views: inside the Node view of a server window (a Back link under the window's header), else a window of their own */
window.pgNodesModal = (title, body, cls = "wide") => { if (state.open == null) state.nodeWin = state.nodeWin || {}; showModal(title, body, cls); };      // (state.nodeWin: the Servers page must not close a window that is not a server's: see PAGES.servers)
window.pgNodesWin = (title, body, cls = "wide") => {
  const el = $("#node-pane");
  if (!el) { pgNodesModal(title, body, cls); return; }
  el.dataset.sub = "1"; el.innerHTML = `<div class="wback np-back"><button class="sm" onclick="pgNodesBack()">← Back</button><span class="np-t">${title}</span></div>${body}`;
  const mb = el.closest(".modal-body"); if (mb && !el.dataset.kept) mb.scrollTop = 0;
};
window.pgNodesBack = () => { const el = $("#node-pane"); if (el) { delete el.dataset.sub; pgNodeViewDraw(); } else closeModal(); };
window.pgNodesLeave = () => { if ($("#node-pane")) pgNodesBack(); else if (state.nodeWin && state.nodeWin.list && window.pgSrvOutside) pgSrvOutside(); else closeModal(); };
/* a change made from a window: when it worked the window goes back and the status bar says so (a refusal stays in the window, with the panel's own reason) */
const pgNodesDoClose = async (fn, okText) => {
  try { const r = await fn(); pgNodesLeave(); toast("✓ " + okText, "ok"); pgNodesAfter(); return r; } catch (e) { toast("✕ " + e.message, "bad"); return null; }
};

/* ================================================================ the Node view */
const pgNodeV = {key: "", err: null};      // key: the node shown when a server carries more than one ("panel id:node id"); err: what the panel answered when it could not be read
const pgNodeKey = r => `${r.panel_id}:${r.node_id}`;
const pgNodeCls = st => st === "connected" ? "ok" : st === "disabled" || st === "connecting" ? "" : "bad";
const pgNodeIs = r => !!r && r.type === "pasarguard" && r.node_id != null;      // a node of a PasarGuard panel that has an id: something can be done to it
const pgNodeSpan = s => s >= 86400 ? `${Math.floor(s / 86400)} d ${Math.floor(s % 86400 / 3600)} h` : s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.floor(s % 3600 / 60)} min` : `${Math.max(1, Math.floor(s / 60))} min`;
function pgNodeSubject() {      // what the pane on screen is about: a server (its nodes) or one node that runs on no server of ours
  const el = $("#node-pane"); if (!el) return null;
  if (el.dataset.sid) { const s = (state.serversCache || []).find(x => x.id === +el.dataset.sid); return s ? {el, s, rows: pgSrvOn(s)} : null; }
  if (el.dataset.out) { const [p, n] = el.dataset.out.split(":").map(Number); return {el, out: true, from: el.dataset.from === "1", rows: [...(pgSrv.outside || []), ...Object.values(pgSrv.nodes || {}).flat()].filter(x => x.panel_id === p && x.node_id === n).slice(0, 1)}; }      // (a node outside our servers, or - in the Lab page, which opens a node profile - one that runs on a server of ours)
  return null;
}
const pgNodeCurrent = rows => rows.find(r => pgNodeKey(r) === pgNodeV.key) || rows.find(pgNodeIs) || rows[0] || null;
window.pgNodeHost = s => { setTimeout(() => { pgNodesRuns().then(() => pgNodeViewDraw()); pgNodeViewLoad(); }, 0); return `<div id="node-pane" class="np" data-sid="${s.id}">${pgNodeMain({s, rows: pgSrvOn(s)})}</div>`; };      // the Node view of a server window (serverDetailHtml)
window.pgNodeOut = (pid, nid, fromList) => {      // the window of a node that runs on no server of ours
  const r = (pgSrv.outside || []).find(x => x.panel_id === +pid && x.node_id === +nid); if (!r) return;
  pgNodeV.key = pgNodeKey(r); state.nodeWin = {out: true, list: !!fromList};
  showModal(`<span class="st ${pgNodeCls(r.status) === "ok" ? "up" : pgNodeCls(r.status) === "bad" ? "down" : ""}"></span><b>${esc(r.name)}</b> <span class="mut">${esc(r.address)}</span> <span class="pill">${esc(r.panel)}</span>`, `<div id="node-pane" class="np" data-out="${r.panel_id}:${r.node_id}" data-from="${fromList ? 1 : 0}">${pgNodeMain({out: true, rows: [r], from: !!fromList})}</div>`, "wide nodewin");
  pgNodeViewLoad();
};
window.pgNodePick = key => { pgNodeV.key = key; pgNodeV.err = null; pgNodeViewDraw(); pgNodeViewLoad(); };
window.pgNodeViewLoad = async force => {      // read the panel of the node on screen (when what was read is old) and draw the view again
  const sub = pgNodeSubject(); if (!sub) return;
  const cur = pgNodeCurrent(sub.rows); if (!pgNodeIs(cur)) { pgNodeViewDraw(); return; }
  pgNodesUse(cur.panel_id);
  try { await pgNodesLoad(cur.panel_id, !!force); pgNodeV.err = null; } catch (e) { pgNodeV.err = {key: pgNodeKey(cur), text: e.message}; }
  pgNodeViewDraw();
};
window.pgNodeViewDraw = () => {      // from what was read: in place, so nothing blinks (a sub-view that is open - a log, a form - is left alone)
  const sub = pgNodeSubject(); if (!sub || sub.el.dataset.sub === "1") return;
  const cur = pgNodeCurrent(sub.rows); if (pgNodeIs(cur)) pgNodesUse(cur.panel_id);
  morphHtml(sub.el, pgNodeMain(sub));
};
window.pgNodesGo = (pid, id, v) => { pgNodesUse(pid); return pgNodesDo(id, v); };      // a button of the bar: the panel is set first (a server can carry nodes of two panels)

function pgNodeMain(sub) {
  const rows = sub.rows, s = sub.s, ps = window.pgSrvPanels ? pgSrvPanels() : [];
  const back = s ? '<button class="sm" onclick="wtab(\'all\')">← Back</button>' : sub.from ? '<button class="sm" onclick="pgSrvOutside()">← Nodes outside our servers</button>' : "";
  const head = back ? `<div class="wback">${back}</div>` : "";
  if (!rows.length) {      // no node on this server: the offer to make it one (a foreign server and a PasarGuard panel to add it to), or just the fact
    const mine = ps.length && s && s.role === "foreign";
    const body = mine ? `<div class="np-empty">This server is not a node of any panel. Making it one installs the PasarGuard node here over SSH (or finds the one that is here), reads its certificate and adds it to the panel.
        <div class="np-make">${ps.length > 1 ? `<select id="np-mk-panel" title="The PasarGuard panel the node is added to">${ps.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select>` : ""}<button class="sm pri" onclick="pgSrvWin(${s.id})" title="Install the node here (or find the one that is here), read its certificate, add it to the panel">Make a node…</button></div></div>`
      : `<div class="np-empty mut">${ps.length ? "No node of any panel runs on this server." : "No PasarGuard panel yet: add one on the Panel page and a foreign server can be made its node."}</div>`;
    return head + pgNodesRunHtml(s ? s.name : "") + `<div class="sbox" data-b="node"><h5><i>${SI.cube}</i>Node</h5>${body}</div>`;
  }
  const cur = pgNodeCurrent(rows); pgNodeV.key = pgNodeKey(cur);
  const pick = rows.length > 1 ? `<div class="np-pick">${rows.map(r => `<button class="sm np-chip ${pgNodeKey(r) === pgNodeV.key ? "on" : ""}" onclick="pgNodePick('${pgNodeKey(r)}')" title="${esc(`${r.type === "pasarguard" ? "PasarGuard" : "Marzban"} node of the panel ${r.panel}: ${r.status || "?"}`)}"><i class="dotc ${pgNodeCls(r.status)}"></i>${esc(r.name)} <small>${esc(r.panel)}</small></button>`).join("")}</div>` : "";
  const kv = (l, v) => `<div class="kv2"><span>${l}</span><div>${v}</div></div>`;
  const rr = (l, v, warn, bad, detail) => `<div class="rrow"><span>${l}</span><i class="rbar"><u class="${v >= bad ? "bad" : v >= warn ? "warn" : ""}" style="width:${v == null ? 0 : Math.min(100, v)}%"></u></i><em><b>${v == null ? "—" : Math.round(v) + "%"}</b>${detail ? `<small>${detail}</small>` : ""}</em></div>`;
  const box = (icon, title, body) => `<div class="sbox" data-b="${title.split(" ")[0].toLowerCase()}"><h5><i>${icon}</i>${title}</h5>${body.join("")}</div>`;
  const wait = '<span class="mut">reading…</span>', none = '<span class="mut">—</span>';
  const chip = `<span class="mchip ${pgNodeCls(cur.status)}">${esc(cur.panel)}</span>`;
  const stat = (st, msg) => `<span class="np-st" ${msg ? `title="${esc(msg)}"` : ""}><i class="dotc ${pgNodeCls(st)}"></i><b class="${pgNodeCls(st) === "bad" ? "err" : ""}">${esc(st || "?")}</b></span>`;      // (the panel's sentence about it is a line under the boxes: the row is too short for it)
  const run = pgNodesRunHtml(s ? s.name : cur.name);
  if (!pgNodeIs(cur)) {      // a Marzban node (or a node without an id): what the health job saw, and the way to its panel
    const man = cur.type !== "pasarguard";
    return head + run + pick + `<div class="sgrid np-grid two">${box(SI.cube, "Node", [kv("Panel", chip), kv("Type", man ? "Marzban" : "PasarGuard"), kv("Status", stat(cur.status, cur.message)), kv("Address", `<span class="mono">${esc(cur.address)}</span>`), kv("Xray", cur.xray ? esc(cur.xray) : none), kv("Server", cur.match === "dns" ? "matched through the DNS" : cur.match ? "same address" : "no server of ours")])}
      <div class="sbox np-note"><h5><i>${SI.gear}</i>Managed elsewhere</h5><div class="np-empty mut">${man ? "A Marzban node is changed in its Marzban panel: its log, its restart and its certificate are there." : "The panel gave this node no id: it cannot be changed from here."}</div><div class="np-make"><button class="sm" onclick="pgSrvOpenPanel(${cur.panel_id}, ${cur.type === "pasarguard" ? 1 : 0})">Open the panel</button></div></div></div>`;
  }
  const C = pgNodesC[cur.panel_id], data = C && C.data, d = data ? (data.nodes || []).find(n => n.id === cur.node_id) || null : null, live = (d && d.live) || null;
  const bad = pgNodeV.err && pgNodeV.err.key === pgNodeKey(cur) ? pgNodeV.err.text : "";
  const core = d && data ? ((data.cores || []).find(c => c.id === d.core_config_id) || {}).name : "";
  const lost = data && !d ? `<div class="pg-nodes-warn err">The panel has no node ${esc(cur.name)} any more: it was deleted there. The list behind is brought up to date when this window is closed.</div>` : "";
  const why = (d ? d.message : cur.message) || "", state_ = d ? d.status : cur.status;
  const warn = (why && pgNodeCls(state_) === "bad" ? `<div class="pg-nodes-warn err"><b>${esc(cur.name)}</b>: ${esc(why)}</div>` : "") + (data && data.warnings || []).map(w => `<div class="pg-nodes-warn">${esc(w)}</div>`).join("");      // (what the panel says about a node that is not connected, then what it could not read)
  const traffic = n => `<span class="trf"><i class="ta d">↓</i>${fmtB(n.down)}</span><span class="trf"><i class="ta u">↑</i>${fmtB(n.up)}</span>`;
  const b1 = box(SI.cube, "Node", [kv("Panel", chip), kv("Status", stat(d ? d.status : cur.status, d ? d.message : cur.message)),
    kv("Address", d ? `<span class="mono">${esc(d.address)}:${d.port}</span>` : `<span class="mono">${esc(cur.address)}</span>`), kv("API", d ? `port ${d.api_port} <small class="mut">${esc(String(d.connection_type).toUpperCase())}</small>` : wait),
    kv("Xray", d ? esc(d.xray || "?") : (cur.xray ? esc(cur.xray) : wait)), kv("Software", d ? esc(d.version || "?") : wait)]);      // (the node software and the Xray core: two rows, a long value would be cut at its left end)
  const b2 = box(SI.bars, "Traffic", [kv("Since reset", d ? traffic({down: d.downlink, up: d.uplink}) : wait), kv("All time", d ? traffic({down: d.lifetime_downlink, up: d.lifetime_uplink}) : wait),
    kv("Data limit", d ? (d.data_limit ? `${fmtB(d.data_limit)} <small class="mut">${esc(d.reset_strategy === "no_reset" ? "never reset" : "reset every " + d.reset_strategy)}</small>` : "no limit") : wait),
    kv("Coefficient", d ? `×${+d.coefficient}` : wait), kv("Keep-alive", d ? `${d.keep_alive} s` : wait), kv("Core config", d ? esc(core || "#" + d.core_config_id) : wait)]);
  const spd = v => v == null ? none : fmtB(v) + "/s";
  const match = n => n.server ? (n.match === "dns" ? "server, through the DNS" : "server, same address") : '<span class="mut">no server of ours</span>';
  const b3 = box(SI.cpu, "Reported by the node", live ? [rr("CPU", live.cpu, 80, 92, live.cores ? live.cores + (live.cores === 1 ? " core" : " cores") : ""), rr("RAM", live.ram, 80, 90, live.mem_total ? fmtB(live.mem_total) : ""), kv("Speed", `<span class="trf"><i class="ta d">↓</i>${spd(live.rx)}</span><span class="trf"><i class="ta u">↑</i>${spd(live.tx)}</span>`),
    kv("Running", live.uptime ? pgNodeSpan(live.uptime) : none), kv("Matched", match(d)), kv("Read", `${ago(Math.floor(C.at / 1000))} ago`)]
    : [rr("CPU", null, 80, 92, ""), rr("RAM", null, 80, 90, ""), kv("Speed", none), kv("Running", none), kv("Matched", d ? match(d) : wait), kv("Read", d ? `<span class="mut" title="A node that is not connected reports nothing">the node reports nothing</span>` : (bad ? '<span class="err">not read</span>' : wait))]);
  const id = cur.node_id, pid = cur.panel_id, go = v => `pgNodesGo(${pid}, ${id}, '${v}')`;
  const ab = (ic, label, tip, v, cls = "") => `<button class="sm abtn ${cls}" ${d ? `onclick="${go(v)}"` : "disabled"} title="${esc(tip)}"><i class="bi">${ic}</i>${label}</button>`;
  const off = d && d.status === "disabled";
  const more = `<select class="sm abtn abtn-open np-more" ${d ? "" : "disabled"} onchange="pgNodesMoreGo(${pid}, ${id}, this)" title="More actions on this node"><option value="">More…</option><option value="sync">Resync users</option><option value="reset">Reset counters</option><option value="update">Update node…</option><option value="core">Update Xray…</option><option value="geo">Update geo files…</option><option value="delete">Delete…</option></select>`;
  const bar = `<div class="sbar np-bar"><span class="sg">${ab(SI.term, "Log", "The log of the node", "logs")}${ab(SI.bars, "Usage", "The traffic of the node by hour, day or month", "usage")}${ab(SI.cpu, "Resources", "CPU, memory and speed curves, as the node reports them", "stats")}${ab(SI.pulse, "Latency", "The delay of the outbounds the node tests", "latency")}${ab(SI.lock(false), "Certificate", "Is the certificate the panel holds the one the node made?", "cert")}</span><i class="sep"></i><span class="sg">${ab(SI.edit, "Edit", "Change the node (name, address, ports, key, certificate ...)", "edit")}${ab(SI.restore, "Restart", "Stop and start Xray on the node: its users drop for a few seconds", "reconnect")}${ab(SI.power, off ? "Enable" : "Disable", off ? "Connect the panel to the node again" : "Disconnect the panel from the node: its users are cut", off ? "enable" : "disable", off ? "" : "danger")}</span><i class="sep"></i><span class="sg">${more}</span></div>`;
  return head + run + pick + (bad ? `<div class="pg-nodes-warn err">${esc(bad)}</div>` : "") + lost + warn + `<div class="sgrid np-grid">${b1}${b2}${b3}</div>` + bar;
}

/* ================================================================ what can be done to a node (the buttons of the bar and the More… menu) */
window.pgNodesMoreGo = (pid, id, sel) => { const v = sel.value; sel.selectedIndex = 0; if (v) pgNodesGo(pid, id, v); };      // the More… select of the bar
window.pgNodesDo = async (id, v) => {
  const n = pgNodesNode(id); if (!v || !n) return;
  const pid = pgNodesS.id, path = `/nodes/${id}`, post = (p, body) => pgApi(pid, path + p, {method: "POST", body: body || {}});
  if (v === "logs") return pgNodesLogs(id);
  if (v === "usage") return pgNodesUsage(id);
  if (v === "stats") return pgNodesStats(id);
  if (v === "latency") return pgNodesLatency(id);
  if (v === "cert") return pgNodesCert(id);
  if (v === "edit") return pgNodesEdit(id);
  if (v === "sync") { if (await gconfirm(`Resync the users of ${n.name}? The panel sends the list of users to the node again. Nothing restarts and nobody is cut; on a big panel it takes a few seconds.`)) pgDo(() => post("/sync"), `Users sent to ${n.name} again`, pgNodesAfter); return; }
  if (v === "reconnect") { if (await gconfirm(`Restart node ${n.name}? The panel stops and starts Xray on it: the users connected through this node drop for a few seconds and reconnect by themselves. Nothing is deleted.`)) pgDo(() => post("/reconnect"), `${n.name} is restarting`, pgNodesAfter); return; }
  if (v === "disable") { if (await gconfirm(`Disable node ${n.name}? The panel disconnects from it: everybody connected through this node is cut until you enable it again. The users and their links stay as they are.`, {danger: true})) pgDo(() => post("/state", {enabled: false}), `${n.name} is disabled`, pgNodesAfter); return; }
  if (v === "enable") { if (await gconfirm(`Enable node ${n.name}? The panel connects to it again.`)) pgDo(() => post("/state", {enabled: true}), `${n.name} is enabled`, pgNodesAfter); return; }
  if (v === "reset") { if (await gconfirm(`Set the traffic counters of ${n.name} to zero? Now: ↓ ${fmtB(n.downlink)} · ↑ ${fmtB(n.uplink)}. The panel keeps a log row with these numbers; a node stopped by its data limit connects again.`)) pgDo(() => post("/reset-usage"), `Counters of ${n.name} set to zero`, pgNodesAfter); return; }
  if (v === "update") { if (await gconfirm(`Update the software of node ${n.name}? The node updates itself and restarts: the users connected through it drop for a minute or so. Do one node at a time.`, {danger: true})) pgDo(() => post("/maintenance", {what: "node"}), `${n.name}: update asked`, pgNodesAfter); return; }
  if (v === "core") {
    const ver = ((await gprompt(`Xray version for ${n.name} ("latest" or like 25.8.31). Now: ${n.xray || "?"}`, "latest")) || "").trim(); if (!ver) return;
    if (!/^(latest|v?\d+\.\d+\.\d+)$/.test(ver)) { toast('✕ The version is "latest" or like 25.8.31', "bad"); return; }
    if (await gconfirm(`Update Xray on ${n.name} to ${ver}? The core is replaced and started again on the node: its users drop for a moment, and a wrong version can stop the node from working.`, {danger: true})) pgDo(() => post("/maintenance", {what: "core", core_version: ver.trim()}), `${n.name}: Xray ${ver} asked`, pgNodesAfter);
    return;
  }
  if (v === "geo") {
    const region = ((await gprompt(`Geo files for ${n.name}: iran, china or russia?`, "iran")) || "").trim().toLowerCase(); if (!region) return;
    if (!["iran", "china", "russia"].includes(region)) { toast("✕ The region is iran, china or russia", "bad"); return; }
    pgDo(() => post("/maintenance", {what: "geofiles", region}), `${n.name}: geo files (${region}) asked`, pgNodesAfter);
    return;
  }
  if (v === "delete" && await gconfirm(`Delete node ${n.name} from the panel? The panel stops using it and drops its traffic history (↓ ${fmtB(n.downlink)} · ↑ ${fmtB(n.uplink)}). The users are not deleted, but everybody connected through this node is cut. The node is saved to a file first: Backups can make it again (with a new id and no history). The server itself is not touched.`, {danger: true}))
    pgDo(() => pgApi(pid, path + "?confirm=true", {method: "DELETE"}), `${n.name} deleted (saved in Backups)`, pgNodesAfter);
};

/* ---- the log, the usage, the certificate */
window.pgNodesLogs = async (id, lines = 200) => {
  const n = pgNodesNode(id); if (!n) return;
  const draw = (body, note) => pgNodesWin(`<b>Log of ${esc(n.name)}</b>`, `<div class="frm-act pg-nodes-tools"><select onchange="pgNodesLogs(${id}, +this.value)" title="How many lines">${[100, 200, 500].map(x => `<option value="${x}" ${x === lines ? "selected" : ""}>last ${x} lines</option>`).join("")}</select><button class="sm" onclick="pgNodesLogs(${id}, ${lines})">↻ Read again</button><span class="sp"></span><small class="mut">${esc(note)}</small></div>${body}`);
  draw('<div class="loading"><i class="spin"></i><span>Reading the log from the panel (a few seconds)…</span></div>', "");
  try {
    const r = await pgApi(pgNodesS.id, `/nodes/${id}/logs?lines=${lines}`);
    draw(`<pre class="gl-out pg-nodes-log">${esc(r.lines.join("\n") || "(the node has no log lines)")}</pre>`, `${r.lines.length} lines · the node sends what it kept, then the news`);
    const pre = $("#modal .pg-nodes-log"); if (pre) pre.scrollTop = pre.scrollHeight;      // the newest lines are at the bottom: that is where a log is read
  } catch (e) { draw(`<div class="err">${esc(e.message)}</div>`, ""); }
};
window.pgNodesUsage = async (id, period = "day", days = 7) => {
  const n = pgNodesNode(id); if (!n) return;
  const opts = [["hour", 1, "last 24 hours"], ["hour", 7, "last 7 days, by hour"], ["day", 7, "last 7 days"], ["day", 30, "last 30 days"], ["month", 365, "last 12 months"]];
  const draw = body => pgNodesWin(`<b>Usage of ${esc(n.name)}</b>`, `<div class="frm-act pg-nodes-tools"><select onchange="const o = this.value.split(':'); pgNodesUsage(${id}, o[0], +o[1])">${opts.map(([p, d, l]) => `<option value="${p}:${d}" ${p === period && d === days ? "selected" : ""}>${l}</option>`).join("")}</select><span class="sp"></span></div>${body}`);
  draw('<div class="loading"><i class="spin"></i><span>Reading the usage…</span></div>');
  try {
    const r = await pgApi(pgNodesS.id, `/nodes/${id}/usage?period=${period}&days=${days}`), at = t => period === "month" ? t.slice(0, 7) : period === "day" ? t.slice(0, 10) : t.slice(5, 16).replace("T", " ");
    draw(`<div class="pg-nodes-tot"><span class="pill">↓ ${fmtB(r.total_down)}</span><span class="pill">↑ ${fmtB(r.total_up)}</span><span class="pill">${fmtB(r.total_down + r.total_up)} in all</span></div>
      <div class="lbox full pg-nodes-ubox"><table class="tbl lift"><colgroup><col style="width:34%"><col style="width:22%"><col style="width:22%"><col></colgroup><thead><tr><th>${period === "month" ? "Month" : period === "day" ? "Day" : "Hour (UTC)"}</th><th>Down</th><th>Up</th><th>Total</th></tr></thead><tbody>${r.rows.slice().reverse().map(x => `<tr><td class="num">${esc(at(x.t))}</td><td>${fmtB(x.down)}</td><td>${fmtB(x.up)}</td><td class="mut">${fmtB(x.down + x.up)}</td></tr>`).join("") || '<tr><td colspan="4" class="mut">no traffic in this time</td></tr>'}</tbody></table></div>`);
  } catch (e) { draw(`<div class="err">${esc(e.message)}</div>`); }
};
const pgNodesCertLine = (label, c) => `<div class="kv2"><span>${label}</span><div>${c ? `<b>${esc(c.subject || "—")}</b> · ends ${pgNodesDate(c.not_after)} <small class="${c.days_left < 30 ? "err" : "mut"}">${c.days_left} days left</small><br><small class="mut mono">${esc(c.fingerprint.slice(0, 24))}…</small>` : '<span class="mut">—</span>'}</div></div>`;
window.pgNodesCert = async id => {
  const n = pgNodesNode(id); if (!n) return;
  const draw = body => pgNodesWin(`<b>Certificate of ${esc(n.name)}</b>`, body);
  draw('<div class="loading"><i class="spin"></i><span>Comparing with the node on its server (read-only)…</span></div>');
  try {
    const r = await pgApi(pgNodesS.id, `/nodes/${id}/cert`), same = r.same === true, diff = r.same === false;
    draw(`<div class="sbox"><h5><i>🔒</i>${same ? "The panel holds the certificate the node made" : diff ? "The certificate in the panel is NOT the one on the server" : "Cannot compare"}</h5>
      ${pgNodesCertLine("In the panel", r.in_panel)}${pgNodesCertLine(r.server ? "On " + esc(r.server.name) : "On the server", r.on_server)}
      ${r.problem ? `<div class="err pg-nodes-note">${esc(r.problem)}</div>` : ""}${diff ? '<div class="pg-nodes-note">A node that made a new certificate (a re-install, <b>pg-node renew-cert</b>) cannot be reached by the panel until the panel holds the new one.</div>' : ""}</div>
      <div class="frm-act">${diff ? `<button class="pri" onclick="pgNodesCertApply(${id})">Copy the server's certificate into the panel</button>` : ""}${r.server ? `<button class="sm" onclick="pgNodesCertCopy(${r.server.id})" title="The public certificate text, to paste somewhere by hand">Copy the certificate text</button>` : ""}<button class="sm" onclick="pgNodesBack()">Close</button></div>`);
  } catch (e) { draw(`<div class="err">${esc(e.message)}</div>`); }
};
window.pgNodesCertApply = async id => {
  const n = pgNodesNode(id); if (!n) return;
  if (!await gconfirm(`Replace the certificate the panel holds for ${n.name} with the one the node made? Saving makes the panel start Xray on the node again: its users drop for a moment. The node is saved to a file first (Backups).`, {danger: true})) return;
  pgNodesDoClose(() => pgApi(pgNodesS.id, `/nodes/${id}/cert`, {method: "POST"}), `Certificate of ${n.name} copied into the panel`);
};
window.pgNodesCertCopy = async sid => {
  try { const r = await pgApi(pgNodesS.id, `/nodes/servers/${sid}/cert`); toast(await pgNodesCopy(r.cert) ? `✓ Certificate of ${r.server.name} copied (ends ${pgNodesDate(r.not_after)})` : "✕ The browser did not allow the copy", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
};

/* ---- forms: add and edit */
const pgNodesCoreOpts = (cores, cur) => (cores || []).map(c => `<option value="${c.id}" ${c.id === cur ? "selected" : ""}>${esc(c.name)}${c.type && c.type !== "xray" ? " (" + esc(c.type) + ")" : ""}</option>`).join("") || '<option value="">no core config</option>';
const pgNodesKeyField = (label, hint, required) => `<label style="grid-column: span 2">${label} <small>${hint}</small><span class="pg-nodes-key"><input name="api_key" type="password" ${required ? "required" : ""} autocomplete="new-password" spellcheck="false"><button type="button" class="sm" onclick="pgNodesKeyShow(this)">Show</button><button type="button" class="sm" onclick="pgNodesKeyGen(this)" title="Make a new random key. The node must be told the same key">Generate</button></span></label>`;
window.pgNodesKeyShow = b => { const i = b.parentNode.querySelector("input"); i.type = i.type === "password" ? "text" : "password"; b.textContent = i.type === "password" ? "Show" : "Hide"; };
window.pgNodesKeyGen = b => { const i = b.parentNode.querySelector("input"); i.value = pgNodesUuid(); i.type = "text"; b.parentNode.querySelector("button").textContent = "Hide"; };
window.pgNodesAdd = () => {
  const d = pgNodesS.data || {}, free = d.free || [], pn_ = ((window.pgSrvPanels && pgSrvPanels()) || []).find(p => p.id === pgNodesS.id);
  pgNodesWin(`<b>＋ Add a node</b>${pn_ ? ` <span class="pill">${esc(pn_.name)}</span>` : ""}`, `<form class="frm pg-nodes-form" onsubmit="return pgNodesAddSave(event)">
    <label>One of our servers <small>(fills the address)</small><select name="server" onchange="pgNodesPick(this)"><option value="">— none —</option>${free.map(s => `<option value="${s.id}">${esc(s.name)} · ${esc(s.host)}</option>`).join("")}</select></label>
    <label>Name<input name="name" required maxlength="40" autocomplete="off" placeholder="node-a"></label><label>Address<input name="address" required autocomplete="off" placeholder="203.0.113.5 or node.example.test"></label>
    <label>Port<input name="port" type="number" min="1" max="65535" value="62050"></label><label>API port<input name="api_port" type="number" min="1" max="65535" value="62051"></label><label>Connection<select name="connection_type"><option value="grpc">gRPC</option><option value="rest">REST</option></select></label>
    <label>Usage coefficient<input name="usage_coefficient" type="number" step="0.1" min="0" value="1"></label><label>Keep-alive (s)<input name="keep_alive" type="number" min="0" value="60"></label><label>Core config<select name="core_config_id">${pgNodesCoreOpts(d.cores)}</select></label>
    ${pgNodesKeyField("API key", "(the key the node was installed with)", true)}
    <label style="grid-column: 1 / -1">Certificate of the node <small>(public; from BEGIN to END)</small><textarea name="server_ca" rows="5" required spellcheck="false"></textarea></label>
    <div class="frm-act"><button class="pri" type="submit">Add node</button><button type="button" class="sm" onclick="pgNodesPickCert(this.form)" title="Read the certificate the node made from the chosen server (read-only over SSH)">Read the certificate from the chosen server</button><button type="button" onclick="pgNodesLeave()">Cancel</button></div></form>`);
};
window.pgNodesPick = sel => { const s = pgNodesFreeSrv(sel.value), f = sel.form; if (!s) return; f.elements.address.value = s.host; if (!f.elements.name.value) f.elements.name.value = s.name; };
window.pgNodesPickCert = async (f, sid) => {
  sid = sid || pgNodesV(f, "server"); if (!sid) { toast("✕ Choose one of our servers first", "bad"); return; }
  try { const r = await pgApi(pgNodesS.id, `/nodes/servers/${sid}/cert`); f.elements.server_ca.value = r.cert.trim(); toast(`✓ Certificate read from ${r.server.name} (ends ${pgNodesDate(r.not_after)})`, "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.pgNodesAddSave = async e => {
  e.preventDefault(); const f = e.target, v = k => pgNodesV(f, k);
  const body = {name: v("name").trim(), address: v("address").trim(), port: +v("port"), api_port: +v("api_port"), connection_type: v("connection_type"), usage_coefficient: +v("usage_coefficient"), keep_alive: +v("keep_alive"), core_config_id: +v("core_config_id"), api_key: v("api_key").trim(), server_ca: v("server_ca").trim()};
  pgNodesDoClose(() => pgApi(pgNodesS.id, "/nodes", {method: "POST", body}), `Node ${body.name} added: the panel is connecting to it`);
  return false;
};
window.pgNodesEdit = id => {
  const n = pgNodesNode(id); if (!n) return; const d = pgNodesS.data || {};
  const gbv = n.data_limit ? +(n.data_limit / pgNodesGB).toFixed(2) : 0;
  pgNodesWin(`<b>Edit ${esc(n.name)}</b>`, `<form class="frm pg-nodes-form" onsubmit="return pgNodesEditSave(event, ${id})">
    <label>Name<input name="name" required maxlength="40" value="${esc(n.name)}" autocomplete="off"></label><label>Address<input name="address" required value="${esc(n.address)}" autocomplete="off"></label><label>Core config<select name="core_config_id">${pgNodesCoreOpts(d.cores, n.core_config_id)}</select></label>
    <label>Port<input name="port" type="number" min="1" max="65535" value="${n.port}"></label><label>API port<input name="api_port" type="number" min="1" max="65535" value="${n.api_port}"></label><label>Connection<select name="connection_type"><option value="grpc" ${n.connection_type === "grpc" ? "selected" : ""}>gRPC</option><option value="rest" ${n.connection_type === "rest" ? "selected" : ""}>REST</option></select></label>
    <label>Usage coefficient<input name="usage_coefficient" type="number" step="0.1" min="0" value="${+n.coefficient}"></label><label>Keep-alive (s)<input name="keep_alive" type="number" min="0" value="${n.keep_alive}"></label><label>Data limit (GB, 0 = none)<input name="data_limit_gb" type="number" step="0.1" min="0" value="${gbv}"></label>
    <label>Reset period<select name="data_limit_reset_strategy">${["no_reset", "day", "week", "month", "year"].map(x => `<option ${n.reset_strategy === x ? "selected" : ""}>${x}</option>`).join("")}</select></label>
    ${pgNodesKeyField("New API key", "(empty = keep the key; the node must have the same one)", false)}
    <label style="grid-column: 1 / -1">New certificate <small>(empty = keep the one the panel has; the key and the certificate are never shown here)</small><textarea name="server_ca" rows="3" spellcheck="false" placeholder="-----BEGIN CERTIFICATE-----"></textarea></label>
    <small>Saving makes the panel start Xray on the node again: the users connected through it drop for a moment. The node is saved to a file first (Backups).</small>
    <div class="frm-act"><button class="pri" type="submit">Save</button>${n.server ? `<button type="button" class="sm" onclick="pgNodesPickCert(this.form, ${n.server.id})" title="Read the certificate the node made from ${esc(n.server.name)}">Read the certificate from ${esc(n.server.name)}</button>` : ""}<button type="button" onclick="pgNodesLeave()">Cancel</button></div></form>`);
};
window.pgNodesEditSave = async (e, id) => {
  e.preventDefault(); const f = e.target, v = k => pgNodesV(f, k), n = pgNodesNode(id); if (!n) return false;
  const body = {}, put = (k, val, was) => { if (String(val) !== String(was)) body[k] = val; };      // only what differs goes to the panel
  put("name", v("name").trim(), n.name); put("address", v("address").trim(), n.address); put("port", +v("port"), n.port); put("api_port", +v("api_port"), n.api_port); put("connection_type", v("connection_type"), n.connection_type);
  put("usage_coefficient", +v("usage_coefficient"), +n.coefficient); put("keep_alive", +v("keep_alive"), n.keep_alive); put("core_config_id", +v("core_config_id"), n.core_config_id); put("data_limit_reset_strategy", v("data_limit_reset_strategy"), n.reset_strategy);
  if (Math.abs(+v("data_limit_gb") * pgNodesGB - n.data_limit) > 1e6) body.data_limit_gb = +v("data_limit_gb");
  if (v("api_key").trim()) body.api_key = v("api_key").trim();
  if (v("server_ca").trim()) body.server_ca = v("server_ca").trim();
  if (!Object.keys(body).length) { toast("Nothing to change", "bad"); return false; }
  const effect = n.status === "disabled" ? "The node is disabled: the change is only saved, nothing is started." : "The panel starts Xray on it again: the users connected through it drop for a moment and reconnect by themselves.";
  if (!await gconfirm(`Update node ${n.name} (${Object.keys(body).map(k => k.replace(/_/g, " ")).join(", ")})? ${effect} The node is saved to a file first.`)) return false;
  pgNodesDoClose(() => pgApi(pgNodesS.id, `/nodes/${id}`, {method: "PUT", body}), `${n.name} saved (the old values are in Backups)`);
  return false;
};

/* ---- backups */
window.pgNodesBackups = async () => {
  const draw = body => pgNodesWin("<b>Backups of the nodes</b>", body);
  draw('<div class="loading"><i class="spin"></i><span>Reading…</span></div>');
  try {
    const r = await pgApi(pgNodesS.id, "/nodes/backups"); pgNodesS.bk = r.backups;      // the button gets the NUMBER of the backup: a node name from the panel is text, never code
    draw(`<div class="lbox full pg-nodes-ubox"><table class="tbl lift"><colgroup><col style="width:14%"><col style="width:26%"><col style="width:22%"><col style="width:22%"><col></colgroup><thead><tr><th>When</th><th>What was about to happen</th><th>Node</th><th>Address</th><th></th></tr></thead><tbody>${r.backups.map((b, i) => `<tr><td class="mut" title="${esc(new Date(b.at * 1000).toLocaleString())}">${ago(b.at)} ago</td><td>${esc(pgNodesKind[b.kind] || b.kind)}</td><td class="name">${esc(b.node)}</td><td class="mut">${esc(b.address)}</td><td><button class="sm" onclick="pgNodesRestore(${i})">Restore</button></td></tr>`).join("") || '<tr><td colspan="5" class="mut">nothing saved yet: a copy of a node is saved before it is edited or deleted</td></tr>'}</tbody></table></div>
      <small class="mut">The last 20 are kept, in files only this server can read. A deleted node is made again with a new id and no traffic history; an edited one gets its old values back (the state before is saved first).</small>`);
  } catch (e) { draw(`<div class="err">${esc(e.message)}</div>`); }
};
window.pgNodesRestore = async i => {
  const b = (pgNodesS.bk || [])[i]; if (!b) return;
  const name = b.name, kind = b.kind, node = b.node;
  const text = kind === "delete" ? `Make node ${node} again from this backup? It gets a new id and no traffic history; the panel connects to it with the saved key and certificate.` : `Put node ${node} back as it was ${pgNodesKind[kind] || ""}? The state now is saved first. The panel then starts Xray on the node again: its users drop for a moment.`;
  if (!await gconfirm(text)) return;
  pgNodesDoClose(() => pgApi(pgNodesS.id, `/nodes/restore/${encodeURIComponent(name)}`, {method: "POST"}), `${node} restored`);
};

/* ---- the unified flow: make a server of ours a node (install if needed, read the certificate, add to the panel, wait) */
window.pgNodesMake = async sid => {      // from the Node view of a server window or its Install view (pg_srvnode.js sets the panel with pgNodesUse first)
  const s = pgNodesFreeSrv(sid) || (state.serversCache || []).find(x => x.id === +sid); if (!s) return;
  pgNodesWin(`<b>Make ${esc(s.name)} a node</b> <span class="pill">${esc(s.host)}</span>`, '<div class="loading"><i class="spin"></i><span>Looking at the server (read-only) and at the panel…</span></div>');
  try { pgNodesMakePaint(sid, await pgApi(pgNodesS.id, "/nodes/make/plan", {method: "POST", body: {server_id: sid}})); } catch (e) { pgNodesWin(`<b>Make ${esc(s.name)} a node</b>`, `<div class="err">${esc(e.message)}</div>`); }
};
function pgNodesMakePaint(sid, plan, vals) {
  const s = pgNodesFreeSrv(sid) || {name: plan.server.name, host: plan.server.host}, o = plan.options, v = vals || {};
  const installed = plan.installed === true, cores = plan.cores || [], needCore = !o.core_config_id && cores.length > 1;
  pgNodesWin(`<b>Make ${esc(s.name)} a node</b> <span class="pill">${esc(s.host)}</span>`, `
    <div class="sbox pg-nodes-plan"><h5><i>🧭</i>What this does</h5><ol class="pg-nodes-steps">${plan.steps.map(x => `<li>${esc(x)}</li>`).join("")}</ol>
      <div class="pg-nodes-note"><span class="pill ${installed ? "ok" : ""}">${installed ? "a PasarGuard node is on the server" : plan.installed === false ? "no node on the server yet" : "server not reachable"}</span> ${installed ? "Its certificate is read from the server. If you installed it by hand, give the API key you used." : "The API key is made by this panel from its own secret: you do not type it and it is never shown."}</div>
      ${plan.problems.map(x => `<div class="err pg-nodes-note">✕ ${esc(x)}</div>`).join("")}${plan.warnings.map(x => `<div class="pg-nodes-note pg-nodes-amber">⚠ ${esc(x)}</div>`).join("")}</div>
    <form class="frm pg-nodes-form" onsubmit="return pgNodesMakeGo(event, ${sid}, ${installed})">
      <label>Node name<input name="name" required maxlength="40" value="${esc(v.name ?? o.name)}" autocomplete="off"></label>
      <label>Usage coefficient<input name="usage_coefficient" type="number" step="0.1" min="0" value="${v.usage_coefficient ?? o.usage_coefficient}"></label>
      <label>Core config<select name="core_config_id">${needCore ? '<option value="">choose…</option>' : ""}${pgNodesCoreOpts(cores, o.core_config_id)}</select></label>
      ${installed ? `<label>Port<input name="port" type="number" min="1" max="65535" value="${v.port ?? o.port}"></label><label>API port<input name="api_port" type="number" min="1" max="65535" value="${v.api_port ?? o.api_port}"></label><label>Connection<select name="connection_type"><option value="grpc" ${o.connection_type === "grpc" ? "selected" : ""}>gRPC</option><option value="rest" ${o.connection_type === "rest" ? "selected" : ""}>REST</option></select></label>
        ${pgNodesKeyField("API key", "(empty = the key this panel would have made)", false)}` : ""}
      <div class="frm-act"><button class="pri" type="submit" ${plan.problems.length ? "disabled" : ""}>${installed ? "Add it to the panel" : "Install and add"}</button>
        <button type="button" class="sm" onclick="pgNodesMakeReplan(${sid}, this.form, ${installed})" title="Look again with these choices">Check again</button>${installed ? `<button type="button" class="sm" onclick="pgNodesCertCopy(${sid})" title="The public certificate text, to add the node by hand">Copy the certificate</button>` : ""}<button type="button" onclick="pgNodesLeave()">Cancel</button>
        ${plan.read_only && !installed ? '<small class="err">The panel is read-only (NIKATEL_READ_ONLY): it cannot install on a server.</small>' : ""}</div></form>`);
}
const pgNodesMakeBody = (f, sid, installed) => {
  const v = k => pgNodesV(f, k), b = {server_id: sid, name: v("name").trim(), usage_coefficient: +v("usage_coefficient")};
  if (v("core_config_id")) b.core_config_id = +v("core_config_id");
  if (installed) { b.port = +v("port"); b.api_port = +v("api_port"); b.connection_type = v("connection_type"); if (v("api_key").trim()) b.api_key = v("api_key").trim(); }
  return b;
};
window.pgNodesMakeReplan = async (sid, f, installed) => {
  const body = pgNodesMakeBody(f, sid, installed); delete body.api_key;      // the key is not part of the plan
  try { pgNodesMakePaint(sid, await pgApi(pgNodesS.id, "/nodes/make/plan", {method: "POST", body}), body); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.pgNodesMakeGo = async (e, sid, installed) => {
  e.preventDefault(); const s = pgNodesFreeSrv(sid) || (state.serversCache || []).find(x => x.id === +sid), body = pgNodesMakeBody(e.target, sid, installed);
  if (installed) { pgNodesDoClose(() => pgApi(pgNodesS.id, "/nodes/register", {method: "POST", body}), `${body.name} added to the panel: it is connecting`); return false; }
  if (!await gconfirm(`Install the PasarGuard node on ${s ? s.name : "this server"} and add it to the panel as ${body.name}? The installer runs on the server for a few minutes (Docker is installed when it is missing). Nothing changes in the panel until the node runs, and nobody is cut: no user is on a new node.`)) return false;
  try {
    const r = await pgApi(pgNodesS.id, "/nodes/make", {method: "POST", body});
    const fin = await pgNodesStory(r.action_id);
    if (fin) toast(fin.ok ? `✓ ${body.name} is a node now` : "✕ Not finished: read the last line", fin.ok ? "ok" : "bad");
  } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};

/* ================================================================ what concerns the whole panel (the Nodes… menu of the Servers page) */
window.pgNodesPanelDo = async (pid, what) => {
  pgNodesUse(pid); try { await pgNodesLoad(pid); pgNodesUse(pid); } catch (e) { /* the windows below say what the panel answers */ }
  if (what === "add") return pgNodesAdd();
  if (what === "backups") return pgNodesBackups();
  if (what === "counts") return pgNodesCounts();
  if (what === "clear") return pgNodesClear();
  if (what === "reconnect") {
    if (!await gconfirm("Restart Xray on EVERY node of this panel? Everyone connected through any node drops for a few seconds and reconnects by itself. Nothing is deleted.", {danger: true})) return;
    pgDo(() => pgApi(pgNodesS.id, "/nodes/reconnect-all", {method: "POST", body: {confirm: true}}), "Every node is restarting", pgNodesAfter);
  }
};

/* ---- charts: the curves of a node, the users online over time */
const pgNodesSpark = (vals, max, cls = "") => {
  const w = 360, h = 52, n = vals.length, ok = vals.filter(v => v != null), m = max || Math.max(...ok, 1);
  if (n < 2 || !ok.length) return `<svg class="pg-nodes-spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><text x="8" y="30" fill="currentColor" opacity=".5" font-size="11">no data in this time</text></svg>`;
  const pts = vals.map((v, i) => `${(i / (n - 1) * w).toFixed(1)},${(h - 3 - Math.min(1, (v == null ? 0 : v) / m) * (h - 6)).toFixed(1)}`).join(" ");
  return `<svg class="pg-nodes-spark ${cls}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="currentColor" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
};
const pgNodesSpans = [["hour", 24, "last 24 hours"], ["hour", 168, "last 7 days, by hour"], ["day", 720, "last 30 days"], ["month", 8760, "last 12 months"]];
const pgNodesSpanSel = (cur, fn) => `<select onchange="const o = this.value.split(':'); ${fn}(+o[1], o[0])">${pgNodesSpans.map(([p, h, l]) => `<option value="${p}:${h}" ${p === cur[0] && h === cur[1] ? "selected" : ""}>${l}</option>`).join("")}</select>`;
window.pgNodesStats = async (id, hours = 24, period = "hour") => {
  const n = pgNodesNode(id); if (!n) return;
  const draw = body => pgNodesWin(`<b>Resources of ${esc(n.name)}</b>`, `<div class="frm-act pg-nodes-tools">${pgNodesSpanSel([period, hours], `pgNodesStats.bind(null, ${id})`)}<span class="sp"></span><small class="mut">as the node itself reports them to the panel</small></div>${body}`);
  draw('<div class="loading"><i class="spin"></i><span>Reading the curves…</span></div>');
  try {
    const r = await pgApi(pgNodesS.id, `/nodes/${id}/stats?period=${period}&hours=${hours}`), s = r.summary, spd = v => (v == null ? "—" : fmtB(v) + "/s"), pc = v => (v == null ? "—" : v + " %");
    const row = (label, key, max, fmt, cls) => `<div class="pg-nodes-cv"><span>${label}</span>${pgNodesSpark(r.rows.map(x => x[key]), max, cls)}<small class="mut">highest ${fmt(s[key].max)} · average ${fmt(s[key].avg)}</small></div>`;
    draw(`<div class="pg-nodes-curves">${row("CPU", "cpu", 100, pc, "cpu")}${row("Memory", "mem", 100, pc, "mem")}${row("Download", "rx", null, spd, "rx")}${row("Upload", "tx", null, spd, "tx")}</div><small class="mut">${r.rows.length} points. The speed is what the node measured at that moment, not a total.</small>`);
  } catch (e) { draw(`<div class="err">${esc(e.message)}</div>`); }
};
window.pgNodesLatency = async id => {
  const n = pgNodesNode(id); if (!n) return;
  const draw = body => pgNodesWin(`<b>Outbound latency of ${esc(n.name)}</b>`, `<div class="frm-act pg-nodes-tools"><button class="sm" onclick="pgNodesLatency(${id})">↻ Test again</button><span class="sp"></span><small class="mut">the panel asks the node to test its outbounds (a few seconds)</small></div>${body}`);
  draw('<div class="loading"><i class="spin"></i><span>The node is testing its outbounds…</span></div>');
  try {
    const r = await pgApi(pgNodesS.id, `/nodes/${id}/latency`);
    draw(`<div class="pg-nodes-tot"><span class="pill ${r.alive === r.total ? "ok" : "warn"}">${r.alive} of ${r.total} alive</span></div><div class="lbox full pg-nodes-ubox"><table class="tbl lift"><colgroup><col style="width:28%"><col style="width:90px"><col style="width:90px"><col><col style="width:90px"></colgroup><thead><tr><th>Outbound</th><th>State</th><th>Delay</th><th>Goes to</th><th>Source</th></tr></thead>
      <tbody>${r.rows.map(x => `<tr><td class="name">${esc(x.name)}</td><td><span class="st ${x.alive ? "up" : "down"}">${x.alive ? "alive" : "dead"}</span></td><td class="num">${x.alive ? x.ms + " ms" : "—"}</td><td class="mono mut">${esc(x.target || "—")}</td><td class="mut">${esc(x.source)}</td></tr>`).join("") || '<tr><td colspan="5" class="mut">the node tests no outbound</td></tr>'}</tbody></table></div><small class="mut">Only the address of each outbound is shown: its credentials never leave the panel.</small>`);
  } catch (e) { draw(`<div class="err">${esc(e.message)}</div>`); }
};
window.pgNodesCounts = async (metric = "online", hours = 24, period = "hour") => {
  const draw = body => pgNodesWin("<b>Users over time</b>", `<div class="frm-act pg-nodes-tools"><select onchange="pgNodesCounts(this.value, ${hours}, '${period}')">${["online", "expired", "limited"].map(m => `<option ${m === metric ? "selected" : ""}>${m}</option>`).join("")}</select>${pgNodesSpanSel([period, hours], `pgNodesCounts.bind(null, '${metric}')`)}<span class="sp"></span></div>${body}`);
  draw('<div class="loading"><i class="spin"></i><span>Reading…</span></div>');
  try {
    const r = await pgApi(pgNodesS.id, `/nodes/user-counts?metric=${metric}&period=${period}&hours=${hours}`), vals = r.rows.map(x => x.n);
    draw(`<div class="pg-nodes-curves"><div class="pg-nodes-cv"><span>${esc(metric)}</span>${pgNodesSpark(vals, null, "cpu")}<small class="mut">highest ${vals.length ? Math.max(...vals) : "—"} · ${r.count} during the period</small></div></div><small class="mut">${r.rows.length} points.</small>`);
  } catch (e) { draw(`<div class="err">${esc(e.message)}</div>`); }
};
window.pgNodesClear = () => {
  pgNodesWin("<b>Clear usage data</b>", `<form class="frm pg-nodes-form" onsubmit="return pgNodesClearGo(event)"><small style="grid-column: 1 / -1" class="err">This removes the traffic history of the panel for good: nothing here can bring it back. The counters of the users and of the nodes stay as they are; only the history behind the charts goes.</small>
    <label>Table<select name="table"><option value="node_usages">node_usages - the traffic of each node over time</option><option value="node_user_usages">node_user_usages - the traffic of each user on each node</option></select></label>
    <label>From <small>empty = from the start</small><input name="start" type="date"></label><label>To <small>empty = to now</small><input name="end" type="date"></label>
    <label style="grid-column: 1 / -1">Type the name of the table to confirm<input name="typed" required autocomplete="off" placeholder="node_usages" spellcheck="false"></label>
    <div class="frm-act"><button class="danger" type="submit">Clear for good</button><button type="button" onclick="pgNodesLeave()">Cancel</button></div></form>`);
};
window.pgNodesClearGo = async e => {
  e.preventDefault(); const f = e.target, body = {table: f.elements.table.value, typed: f.elements.typed.value.trim()};
  if (f.elements.start.value) body.start = f.elements.start.value; if (f.elements.end.value) body.end = f.elements.end.value;
  try { await pgApi(pgNodesS.id, "/nodes/clear-usage", {method: "POST", body}); pgNodesLeave(); toast(`✓ ${body.table} cleared`, "ok"); } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};
