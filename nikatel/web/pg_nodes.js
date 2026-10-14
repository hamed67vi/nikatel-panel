/* PasarGuard tab: the nodes of a PasarGuard panel, joined with the servers of this panel (by address). Registers itself in pgTabs (see pg_common.js).
   ONE list: every node of the panel beside the server of ours it runs on (matched by address, on the server side), then the foreign servers that are not a node yet, each with the
   button that makes it one. Everything is a route first (/api/panels/{id}/pg/nodes...); this file only calls them. Uses followStory of app.js for the long action. */
const pgNodesS = {id: 0, key: "", at: 0, data: null, runs: [], bk: [], q: "", fit: false, fitT: 0};
const pgNodesGB = 1024 ** 3;
const pgNodesHere = id => typeof pn !== "undefined" && pn.tab === "nodes" && String(pn.id) === String(id) && !!$("#pn-body");      // the tab on screen is still this one
const pgNodesNode = id => ((pgNodesS.data || {}).nodes || []).find(n => n.id === +id);
const pgNodesFreeSrv = id => ((pgNodesS.data || {}).free || []).find(s => s.id === +id);
const pgNodesSt = {connected: "up", error: "down", connecting: "degraded", limited: "degraded", disabled: ""};
const pgNodesKind = {edit: "before an edit", delete: "before a delete", cert: "before a certificate change", restore: "before a restore"};
const pgNodesV = (f, k) => f.elements[k] ? f.elements[k].value : "";      // a field of a form by its name (a field called "name" or "action" would hide the form's own properties)
const pgNodesUuid = () => crypto.randomUUID ? crypto.randomUUID() : "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, c => { const r = crypto.getRandomValues(new Uint8Array(1))[0] & 15; return (c === "x" ? r : (r & 3) | 8).toString(16); });
const pgNodesDate = ts => ts ? new Date(ts * 1000).toISOString().slice(0, 10) : "—";
const pgNodesCopy = async text => {
  try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* no clipboard permission: the old way */ }
  const t = document.createElement("textarea"); t.value = text; document.body.appendChild(t); t.select();
  let ok = false; try { ok = document.execCommand("copy"); } catch (e) { ok = false; } t.remove(); return ok;
};

/* The page redraws itself every 15 seconds and calls render again: what was read last is drawn at once (no spinner, no flicker) and the panel is asked again only when that is older than 45 seconds.
   The runs of "make this server a node" (one action each) are read from this panel's own database every time: it is cheap, and a run must be found again after its window was closed. */
pgTabs.nodes = {label: "Nodes", order: 40, types: ["pasarguard"], render: async (id, box) => {
  pgNodesS.id = id;
  const have = pgNodesS.data && pgNodesS.key === String(id);
  if (have) pgNodesDraw(); else pgLoading(box, "Reading the nodes…");
  pgNodesRuns().then(() => { if (pgNodesHere(id)) pgNodesRun(); });
  if (!have || Date.now() - pgNodesS.at > 45000) await pgNodesLoad(id);
}};

/* ---- reading and drawing */
window.pgNodesLoad = async id => {
  try {
    const [d] = await Promise.all([pgApi(id, "/nodes"), pgNodesRuns()]); if (!pgNodesHere(id)) return;
    Object.assign(pgNodesS, {data: d, key: String(id), at: Date.now()}); pgNodesDraw();
  } catch (e) { if (pgNodesHere(id)) pgError($("#pn-body"), e); }
};
const pgNodesRuns = async () => { try { pgNodesS.runs = (await api("/api/actions?kinds=pgnode&limit=3")).actions || []; } catch (e) { pgNodesS.runs = []; } };
function pgNodesRun() {      // the run that is going, or the last one of the past quarter of an hour: a line above the lists, with the way back into its story
  const el = $("#pg-nodes-run"); if (!el) return;
  const runs = pgNodesS.runs || [], now = Date.now() / 1000, a = runs.find(x => !x.done && now - x.ts < 2400) || runs.find(x => x.done && now - x.finished_at < 900);      // a run that never ended (the panel was restarted in the middle) is not "running" after 40 minutes
  if (!a) { el.innerHTML = ""; return; }
  const last = ((a.story || []).filter(x => x.trim()).slice(-1)[0] || "").trim();
  el.innerHTML = `<div class="pg-nodes-run ${a.done ? (a.ok ? "ok" : "bad") : ""}">${a.done ? (a.ok ? "✓" : "✕") : '<i class="spin"></i>'}<b>${esc(a.ref)}</b><span class="mut" title="${esc(last)}">${esc(last.slice(0, 150))}</span><button class="sm" onclick="pgNodesStory(${a.id})">${a.done ? "Read" : "Follow"}</button></div>`;
}
window.pgNodesStory = async aid => {      // the story of a run, followed until it is done (also one that is done: it shows at once)
  showModal("<b>Making a server a node</b>", '<div id="pg-nodes-story" class="fix pg-nodes-story"><div class="fx-line run"><i></i>Reading…</div></div><div class="frm-act"><small class="mut">It keeps running if you close this window: the Nodes tab shows it until it is done.</small><span class="sp"></span><button class="sm" onclick="closeModal()">Close</button></div>', "wide");
  const fin = await followStory(aid, $("#pg-nodes-story"), 1000);
  await pgNodesRuns(); if (pgNodesHere(pgNodesS.id)) pgNodesRun();
  pgNodesAfter();
  return fin;
};
window.pgNodesReload = () => { pgNodesS.data = null; pgLoading($("#pn-body"), "Reading the nodes…"); pgNodesLoad(pgNodesS.id); };
const pgNodesAfter = async () => {      // after a change: read again now, and once more when the node has had time to connect
  await pgNodesLoad(pgNodesS.id);
  setTimeout(() => { if (pgNodesHere(pgNodesS.id)) pgNodesLoad(pgNodesS.id); }, 6000);
};
window.pgNodesOpenSrv = id => { if (typeof docOpen === "function") docOpen(id); };      // the window of the server, on the Servers page
/* a change made from a window: when it worked the window closes and the status bar says so (a refusal stays in the window, with the panel's own reason) */
const pgNodesDoClose = async (fn, okText) => {
  try { const r = await fn(); closeModal(); toast("✓ " + okText, "ok"); pgNodesAfter(); return r; } catch (e) { toast("✕ " + e.message, "bad"); return null; }
};

const pgNodesBar = v => v == null ? '<span class="mut">—</span>' : `<div class="pbar"><span class="bar"><i class="${v >= 90 ? "bad" : v >= 75 ? "warn" : ""}" style="width:${Math.min(100, v)}%"></i></span><b>${Math.round(v)}%</b></div>`;
const pgNodesRank = n => ({error: 0, connecting: 1, limited: 1, disabled: 2}[n.status] ?? 3);      // what needs a look comes first
function pgNodesServerCell(n) {
  const s = n.server;
  if (!s) return `<span class="mut" title="No server of this panel has the address ${esc(n.address)}">${n.hint ? `no server here · <b>${esc(n.hint.name)}</b> has another address` : "no server of ours"}</span>`;
  const dot = s.online ? "up" : s.online === false ? "down" : "";
  return `<span class="pg-nodes-srv" title="${esc(s.host)} · ${esc(s.role)}${s.sample_ts ? " · sample " + ago(s.sample_ts) + " ago" : ""}${n.match === "dns" ? " · matched through the DNS" : ""}${s.online === false ? " · " + esc(s.last_error || "offline") : ""}"><span class="st ${dot}"></span><a class="pg-nodes-link" onclick="pgNodesOpenSrv(${s.id})"><b>${esc(s.name)}</b></a><span class="pill">${esc(s.role)}</span>${n.match === "dns" ? '<span class="pill">DNS</span>' : ""}</span>`;
}
function pgNodesRow(n) {
  const s = n.server, live = n.live || {};
  const cpu = s && s.cpu != null ? s.cpu : live.cpu, ram = s && s.ram != null ? s.ram : live.ram;      // our own sample first; what the node says about itself when no server of ours matches
  const src = s && s.cpu != null ? "sampled by this panel" : "reported by the node";
  const more = `<select class="sm pg-nodes-more" onchange="pgNodesMenu(${n.id}, this)" title="More actions on this node"><option value="">More…</option><option value="usage">Usage…</option><option value="cert">Certificate…</option><option value="sync">Resync users</option><option value="reconnect">Restart</option><option value="${n.status === "disabled" ? "enable" : "disable"}">${n.status === "disabled" ? "Enable" : "Disable"}</option><option value="reset">Reset counters</option><option value="update">Update node…</option><option value="core">Update Xray…</option><option value="geo">Update geo files…</option><option value="delete">Delete…</option></select>`;
  return `<tr class="${pgNodesSt[n.status] ?? ""}"><td class="name"><b>${esc(n.name)}</b> <small class="mut">${esc(n.address)}${n.port !== 62050 ? ":" + n.port : ""}</small></td>
    <td><span class="st ${pgNodesSt[n.status] ?? ""}" title="${esc(n.message || n.status)}">${esc(n.status || "?")}</span></td><td>${pgNodesServerCell(n)}</td>
    <td title="CPU ${esc(src)}">${pgNodesBar(cpu)}</td><td title="RAM ${esc(src)}">${pgNodesBar(ram)}</td>
    <td class="mut" title="Xray ${esc(n.xray || "?")} · node software ${esc(n.version || "?")}">${esc(n.xray || "—")} <small>${esc(n.version || "")}</small></td>
    <td class="mut" title="Since the last reset: ↓ ${fmtB(n.downlink)} · ↑ ${fmtB(n.uplink)}. Ever: ↓ ${fmtB(n.lifetime_downlink)} · ↑ ${fmtB(n.lifetime_uplink)}">↓ ${fmtB(n.downlink)} <small>↑ ${fmtB(n.uplink)}</small></td>
    <td class="mut" title="Usage coefficient: how much of the traffic is counted">×${+n.coefficient}</td>
    <td><span class="pg-nodes-act"><button class="sm" onclick="pgNodesLogs(${n.id})" title="The log of the node">Log</button><button class="sm" onclick="pgNodesEdit(${n.id})" title="Change the node">Edit</button>${more}</span></td></tr>`;
}
function pgNodesFreeRow(s) {
  const dot = s.online ? "up" : s.online === false ? "down" : "", other = s.elsewhere || [];
  const otherTxt = other.map(e => `${e.node || "a node"} of ${e.panel} (${e.type})`).join(", ");
  const off = !s.enabled || s.online === false || other.length > 0;
  const why = !s.enabled ? "This server is turned off in this panel" : s.online === false ? "This server does not answer" : other.length ? `This server already carries ${otherTxt}: that node listens on the ports 62050 and 62051 that a new node would use` : "";
  const where = other.length ? other.slice(0, 2).map(e => `<span class="pill warn" title="${esc(`${e.node || "a node"} of ${e.panel} (${e.type}): ${e.status || "?"}`)}">${esc(e.type)} node · ${esc(e.panel)}</span>`).join("") : esc([s.country ? s.country.toUpperCase() : "", s.provider].filter(Boolean).join(" · ") || "—");
  return `<tr class="${dot} ${other.length ? "pg-nodes-else" : ""}"><td class="name"><span class="pg-nodes-srv"><span class="st ${dot}" title="${s.online ? "online" : s.online === false ? "offline" : "not checked yet"}"></span><a class="pg-nodes-link" onclick="pgNodesOpenSrv(${s.id})" title="Open this server"><b>${esc(s.name)}</b></a></span> <small class="mut">${esc(s.host)}</small></td>
    <td class="mut">${where}</td>
    <td title="CPU sampled by this panel">${pgNodesBar(s.cpu)}</td><td title="RAM sampled by this panel">${pgNodesBar(s.ram)}</td>
    <td><span class="pg-nodes-act"><button class="sm pri" onclick="pgNodesMake(${s.id})" title="${esc(why || "Install the PasarGuard node here (or find the one that is here), read its certificate, add it to the panel")}" ${off ? "disabled" : ""}>Make a node</button></span></td></tr>`;
}
function pgNodesRows() {
  const d = pgNodesS.data, q = pgNodesS.q; if (!d) return;
  const ns = d.nodes.filter(n => nkHit([n.name, n.address, n.status, n.xray, n.version, n.server && n.server.name, n.server && n.server.host, n.server && n.server.role].filter(Boolean).join(" "), q)).sort((a, b) => pgNodesRank(a) - pgNodesRank(b) || a.name.localeCompare(b.name));
  const fs = d.free.filter(s => nkHit([s.name, s.host, s.provider, s.country, s.role].filter(Boolean).join(" "), q));
  const a = $("#pg-nodes-a"), b = $("#pg-nodes-b"); if (!a || !b) return;
  a.innerHTML = ns.map(pgNodesRow).join("") || `<tr><td colspan="9" class="mut">${d.nodes.length ? "no node matches" : "this panel has no node yet"}</td></tr>`;
  b.innerHTML = fs.map(pgNodesFreeRow).join("") || `<tr><td colspan="5" class="mut">${d.free.length ? "no server matches" : "every foreign server of this panel is a node"}</td></tr>`;
  const nb = $("#pg-nodes-nb"); if (nb) nb.textContent = fs.length;
  pgNodesFit();
}
function pgNodesDraw() {
  const box = $("#pn-body"), d = pgNodesS.data; if (!box || !d) return;
  const typing = document.activeElement && document.activeElement.id === "pg-nodes-q", at = typing ? document.activeElement.selectionStart : 0;      // a redraw must not take the search box from someone who is typing in it
  const t = d.totals, bad = t.nodes - t.connected;
  const left = `<span class="pg-nodes-sum"><span class="pill">${t.nodes} node${t.nodes === 1 ? "" : "s"}</span><span class="pill ${bad ? "err" : "ok"}">${bad ? bad + " not connected" : "all connected"}</span><span class="pill" title="Nodes whose address is the address of a server of this panel">${t.with_server} on our servers</span>${d.min_node_version ? `<span class="pill" title="The oldest node software this panel accepts">min ${esc(d.min_node_version)}</span>` : ""}</span>
    <input id="pg-nodes-q" class="pg-nodes-q" type="search" placeholder="Search nodes and servers" value="${esc(pgNodesS.q)}" oninput="pgNodesQ(this.value)" autocomplete="off" spellcheck="false">`;
  const right = `<button class="sm" onclick="pgNodesBackups()" title="The nodes saved to files before an edit or a delete">Backups</button><button class="sm" onclick="pgNodesReload()" title="Read the nodes from the panel again">↻</button><button class="sm pri" onclick="pgNodesAdd()" title="Add a node by hand: address, key and certificate">＋ Add node</button>`;
  const cols = ["16%", "10%", "16%", "8%", "8%", "9%", "11%", "4%", "18%"].map(w => `<col style="width:${w}">`).join(""), fcols = ["34%", "22%", "10%", "10%", ""].map(w => w ? `<col style="width:${w}">` : "<col>").join("");
  pgPaint(box, `${pgBar(left, right)}<div id="pg-nodes-run"></div>${(d.warnings || []).map(w => `<div class="pg-nodes-warn">${esc(w)}</div>`).join("")}
    <div class="lbox full pg-nodes-box pg-nodes-a"><table class="tbl lift pg-nodes-tbl"><colgroup>${cols}</colgroup><thead><tr><th>Node</th><th>Status</th><th>Our server</th><th>CPU</th><th>RAM</th><th>Xray</th><th>Traffic</th><th>Coef.</th><th></th></tr></thead><tbody id="pg-nodes-a"></tbody></table></div>
    <div class="pg-nodes-h"><b>Servers that are not a node</b><span class="pill" id="pg-nodes-nb">0</span><small class="mut">foreign servers that no node of this PasarGuard panel runs on${t.free - t.candidates ? ` · ${t.free - t.candidates} of them carry a node of another panel (dimmed: a new node would clash with it)` : ""}</small></div>
    <div class="lbox full pg-nodes-box pg-nodes-b"><table class="tbl lift pg-nodes-tbl"><colgroup>${fcols}</colgroup><thead><tr><th>Server</th><th>Where</th><th>CPU</th><th>RAM</th><th></th></tr></thead><tbody id="pg-nodes-b"></tbody></table></div>`);
  pgNodesRun(); pgNodesRows();
  if (typing) { const q = $("#pg-nodes-q"); if (q) { q.focus(); q.setSelectionRange(at, at); } }
  if (!pgNodesS.fit) { pgNodesS.fit = true; addEventListener("resize", () => { clearTimeout(pgNodesS.fitT); pgNodesS.fitT = setTimeout(() => { if (pgNodesHere(pgNodesS.id)) pgNodesFit(); }, 150); }); }
}
/* the two boxes share what is left of the window: 60 % and 40 %, each scrolls inside (the page itself never does). The page's own fitting makes EVERY list box end at the bottom of the window, so the
   sizes are given as CSS variables that the stylesheet applies with !important */
function pgNodesFit() {
  const a = $(".pg-nodes-a"), b = $(".pg-nodes-b"), h = $(".pg-nodes-h"); if (!a || !b) return;
  const avail = innerHeight - a.getBoundingClientRect().top - 16 - 8 - (h ? h.offsetHeight + 8 : 0) - 8 - 22;
  let ha = a.scrollHeight, hb = b.scrollHeight;      // what each list needs; when both do not fit, the shorter one keeps all it needs and the other takes the rest (else 60 / 40)
  if (ha + hb > avail) { if (ha <= avail * 0.6) hb = avail - ha; else if (hb <= avail * 0.4) ha = avail - hb; else { ha = avail * 0.6; hb = avail * 0.4; } }
  a.style.setProperty("--pg-nodes-h", Math.max(96, Math.floor(ha)) + "px"); b.style.setProperty("--pg-nodes-h", Math.max(96, Math.floor(hb)) + "px");
}
window.pgNodesQ = v => { pgNodesS.q = v.trim(); pgNodesRows(); };

/* ---- the More menu: each action says what happens to the users on the node, and asks first when it can cut them */
window.pgNodesMenu = async (id, sel) => {
  const v = sel.value; sel.selectedIndex = 0; const n = pgNodesNode(id); if (!v || !n) return;
  const pid = pgNodesS.id, path = `/nodes/${id}`, post = (p, body) => pgApi(pid, path + p, {method: "POST", body: body || {}});
  if (v === "usage") return pgNodesUsage(id);
  if (v === "cert") return pgNodesCert(id);
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
  const draw = (body, note) => showModal(`<b>Log of ${esc(n.name)}</b>`, `<div class="frm-act pg-nodes-tools"><select onchange="pgNodesLogs(${id}, +this.value)" title="How many lines">${[100, 200, 500].map(x => `<option value="${x}" ${x === lines ? "selected" : ""}>last ${x} lines</option>`).join("")}</select><button class="sm" onclick="pgNodesLogs(${id}, ${lines})">↻ Read again</button><span class="sp"></span><small class="mut">${esc(note)}</small></div>${body}`, "wide");
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
  const draw = body => showModal(`<b>Usage of ${esc(n.name)}</b>`, `<div class="frm-act pg-nodes-tools"><select onchange="const o = this.value.split(':'); pgNodesUsage(${id}, o[0], +o[1])">${opts.map(([p, d, l]) => `<option value="${p}:${d}" ${p === period && d === days ? "selected" : ""}>${l}</option>`).join("")}</select><span class="sp"></span></div>${body}`, "wide");
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
  const draw = body => showModal(`<b>Certificate of ${esc(n.name)}</b>`, body, "wide");
  draw('<div class="loading"><i class="spin"></i><span>Comparing with the node on its server (read-only)…</span></div>');
  try {
    const r = await pgApi(pgNodesS.id, `/nodes/${id}/cert`), same = r.same === true, diff = r.same === false;
    draw(`<div class="sbox"><h5><i>🔒</i>${same ? "The panel holds the certificate the node made" : diff ? "The certificate in the panel is NOT the one on the server" : "Cannot compare"}</h5>
      ${pgNodesCertLine("In the panel", r.in_panel)}${pgNodesCertLine(r.server ? "On " + esc(r.server.name) : "On the server", r.on_server)}
      ${r.problem ? `<div class="err pg-nodes-note">${esc(r.problem)}</div>` : ""}${diff ? '<div class="pg-nodes-note">A node that made a new certificate (a re-install, <b>pg-node renew-cert</b>) cannot be reached by the panel until the panel holds the new one.</div>' : ""}</div>
      <div class="frm-act">${diff ? `<button class="pri" onclick="pgNodesCertApply(${id})">Copy the server's certificate into the panel</button>` : ""}${r.server ? `<button class="sm" onclick="pgNodesCertCopy(${r.server.id})" title="The public certificate text, to paste somewhere by hand">Copy the certificate text</button>` : ""}<button class="sm" onclick="closeModal()">Close</button></div>`);
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
  const d = pgNodesS.data || {}, free = d.free || [];
  showModal("<b>＋ Add a node</b>", `<form class="frm pg-nodes-form" onsubmit="return pgNodesAddSave(event)">
    <label>One of our servers <small>(fills the address)</small><select name="server" onchange="pgNodesPick(this)"><option value="">— none —</option>${free.map(s => `<option value="${s.id}">${esc(s.name)} · ${esc(s.host)}</option>`).join("")}</select></label>
    <label>Name<input name="name" required maxlength="40" autocomplete="off" placeholder="node-a"></label><label>Address<input name="address" required autocomplete="off" placeholder="203.0.113.5 or node.example.test"></label>
    <label>Port<input name="port" type="number" min="1" max="65535" value="62050"></label><label>API port<input name="api_port" type="number" min="1" max="65535" value="62051"></label><label>Connection<select name="connection_type"><option value="grpc">gRPC</option><option value="rest">REST</option></select></label>
    <label>Usage coefficient<input name="usage_coefficient" type="number" step="0.1" min="0" value="1"></label><label>Keep-alive (s)<input name="keep_alive" type="number" min="0" value="60"></label><label>Core config<select name="core_config_id">${pgNodesCoreOpts(d.cores)}</select></label>
    ${pgNodesKeyField("API key", "(the key the node was installed with)", true)}
    <label style="grid-column: 1 / -1">Certificate of the node <small>(public; from BEGIN to END)</small><textarea name="server_ca" rows="5" required spellcheck="false"></textarea></label>
    <div class="frm-act"><button class="pri" type="submit">Add node</button><button type="button" class="sm" onclick="pgNodesPickCert(this.form)" title="Read the certificate the node made from the chosen server (read-only over SSH)">Read the certificate from the chosen server</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`, "wide");
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
  showModal(`<b>Edit ${esc(n.name)}</b>`, `<form class="frm pg-nodes-form" onsubmit="return pgNodesEditSave(event, ${id})">
    <label>Name<input name="name" required maxlength="40" value="${esc(n.name)}" autocomplete="off"></label><label>Address<input name="address" required value="${esc(n.address)}" autocomplete="off"></label><label>Core config<select name="core_config_id">${pgNodesCoreOpts(d.cores, n.core_config_id)}</select></label>
    <label>Port<input name="port" type="number" min="1" max="65535" value="${n.port}"></label><label>API port<input name="api_port" type="number" min="1" max="65535" value="${n.api_port}"></label><label>Connection<select name="connection_type"><option value="grpc" ${n.connection_type === "grpc" ? "selected" : ""}>gRPC</option><option value="rest" ${n.connection_type === "rest" ? "selected" : ""}>REST</option></select></label>
    <label>Usage coefficient<input name="usage_coefficient" type="number" step="0.1" min="0" value="${+n.coefficient}"></label><label>Keep-alive (s)<input name="keep_alive" type="number" min="0" value="${n.keep_alive}"></label><label>Data limit (GB, 0 = none)<input name="data_limit_gb" type="number" step="0.1" min="0" value="${gbv}"></label>
    <label>Reset period<select name="data_limit_reset_strategy">${["no_reset", "day", "week", "month", "year"].map(x => `<option ${n.reset_strategy === x ? "selected" : ""}>${x}</option>`).join("")}</select></label>
    ${pgNodesKeyField("New API key", "(empty = keep the key; the node must have the same one)", false)}
    <label style="grid-column: 1 / -1">New certificate <small>(empty = keep the one the panel has; the key and the certificate are never shown here)</small><textarea name="server_ca" rows="3" spellcheck="false" placeholder="-----BEGIN CERTIFICATE-----"></textarea></label>
    <small>Saving makes the panel start Xray on the node again: the users connected through it drop for a moment. The node is saved to a file first (Backups).</small>
    <div class="frm-act"><button class="pri" type="submit">Save</button>${n.server ? `<button type="button" class="sm" onclick="pgNodesPickCert(this.form, ${n.server.id})" title="Read the certificate the node made from ${esc(n.server.name)}">Read the certificate from ${esc(n.server.name)}</button>` : ""}<button type="button" onclick="closeModal()">Cancel</button></div></form>`, "wide");
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
  const draw = body => showModal("<b>Backups of the nodes</b>", body, "wide");
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
window.pgNodesMake = async sid => {
  const s = pgNodesFreeSrv(sid); if (!s) return;
  showModal(`<b>Make ${esc(s.name)} a node</b> <span class="pill">${esc(s.host)}</span>`, '<div class="loading"><i class="spin"></i><span>Looking at the server (read-only) and at the panel…</span></div>', "wide");
  try { pgNodesMakePaint(sid, await pgApi(pgNodesS.id, "/nodes/make/plan", {method: "POST", body: {server_id: sid}})); } catch (e) { showModal(`<b>Make ${esc(s.name)} a node</b>`, `<div class="err">${esc(e.message)}</div>`, "wide"); }
};
function pgNodesMakePaint(sid, plan, vals) {
  const s = pgNodesFreeSrv(sid) || {name: plan.server.name, host: plan.server.host}, o = plan.options, v = vals || {};
  const installed = plan.installed === true, cores = plan.cores || [], needCore = !o.core_config_id && cores.length > 1;
  showModal(`<b>Make ${esc(s.name)} a node</b> <span class="pill">${esc(s.host)}</span>`, `
    <div class="sbox pg-nodes-plan"><h5><i>🧭</i>What this does</h5><ol class="pg-nodes-steps">${plan.steps.map(x => `<li>${esc(x)}</li>`).join("")}</ol>
      <div class="pg-nodes-note"><span class="pill ${installed ? "ok" : ""}">${installed ? "a PasarGuard node is on the server" : plan.installed === false ? "no node on the server yet" : "server not reachable"}</span> ${installed ? "Its certificate is read from the server. If you installed it by hand, give the API key you used." : "The API key is made by this panel from its own secret: you do not type it and it is never shown."}${plan.min_node_version ? ` <span class="mut">The panel accepts node software from ${esc(plan.min_node_version)} on.</span>` : ""}</div>
      ${plan.problems.map(x => `<div class="err pg-nodes-note">✕ ${esc(x)}</div>`).join("")}${plan.warnings.map(x => `<div class="pg-nodes-note pg-nodes-amber">⚠ ${esc(x)}</div>`).join("")}</div>
    <form class="frm pg-nodes-form" onsubmit="return pgNodesMakeGo(event, ${sid}, ${installed})">
      <label>Node name<input name="name" required maxlength="40" value="${esc(v.name ?? o.name)}" autocomplete="off"></label>
      <label>Usage coefficient<input name="usage_coefficient" type="number" step="0.1" min="0" value="${v.usage_coefficient ?? o.usage_coefficient}"></label>
      <label>Core config<select name="core_config_id">${needCore ? '<option value="">choose…</option>' : ""}${pgNodesCoreOpts(cores, o.core_config_id)}</select></label>
      ${installed ? `<label>Port<input name="port" type="number" min="1" max="65535" value="${v.port ?? o.port}"></label><label>API port<input name="api_port" type="number" min="1" max="65535" value="${v.api_port ?? o.api_port}"></label><label>Connection<select name="connection_type"><option value="grpc" ${o.connection_type === "grpc" ? "selected" : ""}>gRPC</option><option value="rest" ${o.connection_type === "rest" ? "selected" : ""}>REST</option></select></label>
        ${pgNodesKeyField("API key", "(empty = the key this panel would have made)", false)}` : ""}
      <div class="frm-act"><button class="pri" type="submit" ${plan.problems.length ? "disabled" : ""}>${installed ? "Add it to the panel" : "Install and add"}</button>
        <button type="button" class="sm" onclick="pgNodesMakeReplan(${sid}, this.form, ${installed})" title="Look again with these choices">Check again</button>${installed ? `<button type="button" class="sm" onclick="pgNodesCertCopy(${sid})" title="The public certificate text, to add the node by hand">Copy the certificate</button>` : ""}<button type="button" onclick="closeModal()">Cancel</button>
        ${plan.read_only && !installed ? '<small class="err">The panel is read-only (NIKATEL_READ_ONLY): it cannot install on a server.</small>' : ""}</div></form>`, "wide");
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
  e.preventDefault(); const s = pgNodesFreeSrv(sid), body = pgNodesMakeBody(e.target, sid, installed);
  if (installed) { pgNodesDoClose(() => pgApi(pgNodesS.id, "/nodes/register", {method: "POST", body}), `${body.name} added to the panel: it is connecting`); return false; }
  if (!await gconfirm(`Install the PasarGuard node on ${s ? s.name : "this server"} and add it to the panel as ${body.name}? The installer runs on the server for a few minutes (Docker is installed when it is missing). Nothing changes in the panel until the node runs, and nobody is cut: no user is on a new node.`)) return false;
  try {
    const r = await pgApi(pgNodesS.id, "/nodes/make", {method: "POST", body});
    const fin = await pgNodesStory(r.action_id);
    if (fin) toast(fin.ok ? `✓ ${body.name} is a node now` : "✕ Not finished: read the last line", fin.ok ? "ok" : "bad");
  } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};
