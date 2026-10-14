/* Lab (the owner, 2026-10-14): the servers page, built on profiles. The section of the menu is called Lab; its list of profiles is the page of these servers (the old Foreign and Iran lists are out of the project - a copy of them
   is kept outside, the owner may want them elsewhere - and nothing moves the servers the panel already has onto the page: Add profile → Already in the panel picks them). Three ideas:
   - a PROFILE comes first: nothing is on the page until one is added, and the add-profile section has what the owner named: Iran server, Foreign server, PasarGuard node. One list, whatever the kinds,
     with the tabs Any | Iran | Foreign | PasarGuard in place of the Iran / Foreign tabs (a tab of a kind is there only while a profile of that kind is: nobody is made to have a PasarGuard node). A PasarGuard node asks for the API key of its panel (or a username and password) first.
   - the list is quiet: one line per profile - who, what it serves (nodes, tunnels), how it is (one state, a strip of the last 24 hours), its load - and nothing else; the rest is one click away
   - the window of a server has four sections: Overview, Node (everything PasarGuard can do to the node that runs here), Monitor (charts and the strip of the last days) and Tools; a node profile opens the Node view
   Routes used: GET/POST /api/profiles, DELETE /api/profiles/{id}, GET /api/profiles/strips, GET /api/servers/{id}/uptime, POST /api/servers, POST /api/panels, and the node routes of pg_nodes.js.
   Uses the helpers of app.js (api, esc, toast, state, showModal, srvModal, serverGridHtml, serverPartHtml, installHtml, loadServerCharts, srvDiagnose ...) and pg_srvnode.js / pg_nodes.js. */
const LAB = {kind: localStorage.getItem("nk.lab.k") || "any", profiles: [], strips: {}};
const LAB_KINDS = [["any", "Any"], ["foreign", "Foreign"], ["iran", "Iran"], ["node", "PasarGuard"]];      // ("Any": every kind at once - the owner chose the name: any kind of server can be added)
const LAB_SECS = [["overview", "Overview"], ["node", "Node"], ["monitor", "Monitor"]];      // (what can be done is in the "⋯" menu, not in a section of buttons)
const LAB_DOTS = '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.9"/><circle cx="12" cy="12" r="1.9"/><circle cx="19" cy="12" r="1.9"/></svg>';
const labKebab = (ctx, id, label) => `<button type="button" class="lab-kb" onclick="labMenu(this, '${ctx}', ${id}, event)" aria-haspopup="menu" aria-label="${esc(label)}" title="${esc(label)}">${LAB_DOTS}</button>`;
window.labOn = () => state.page === "nodes" && sub("nodes") === "lab";
const labServer = p => (state.serversCache || []).find(s => s.id === p.server_id) || null;
const labProfileOf = sid => LAB.profiles.find(p => p.server_id === sid) || null;
const labFree = kind => (state.serversCache || []).filter(s => s.role === kind && !labProfileOf(s.id)).sort((a, b) => a.name.localeCompare(b.name, undefined, {sensitivity: "base"}));      // the servers the panel already has that are not on the page: nothing moves them there by itself - Add profile → "Already in the panel" lists them
const labNodeRows = () => [...(typeof pgSrv !== "undefined" ? pgSrv.outside || [] : []), ...Object.values(typeof pgSrv !== "undefined" ? pgSrv.nodes || {} : {}).flat()];
const labNodeRow = p => labNodeRows().find(x => x.type === "pasarguard" && x.panel_id === p.panel_id && x.node_id === p.node_id) || null;      // what the health job saw (a call to no panel)
const labNodeData = p => { const c = typeof pgNodesC !== "undefined" ? pgNodesC[p.panel_id] : null; return c && c.data ? (c.data.nodes || []).find(n => n.id === p.node_id) || null : null; };      // what the panel said when it was asked (the load, the speed)
const labMbps = b => b == null ? "—" : (b * 8 / 1e6).toFixed(1);

/* ---------------------------------------------------------------- what is wrong with a server, as a short list (the worst first) */
function labIssues(s) {
  const f = s.facts || {}, x = s.extra || {}, out = [], add = (sev, txt, tip) => out.push({sev, txt, tip: tip || txt});
  if (!s.enabled) return out;
  if (noLogin(s)) add("warn", "No login", s.last_error);
  else if (s.online === false) add("bad", "Offline", s.last_error || "The server does not answer");
  else if (s.online && /^SSH does not answer/.test(s.last_error || "")) add("warn", "SSH ✕", s.last_error);
  const mine = (state.tunnelsCache || []).filter(t => t.iran_name === s.name || t.foreign_name === s.name), gre = mine.filter(t => (t.type === "backhaul" || t.type === "gre") && t.state === "down");
  if (gre.length) add("bad", gre.length === mine.length ? "GRE ✕" : "GRE ✓/✕", gre.map(t => t.name + ": " + t.state).join(", "));
  else if (!mine.length && x.gre && !x.gre.ok && x.gre.links) add("bad", "GRE ✕", x.gre.detail);
  const down = mine.filter(t => t.state === "down" && !gre.includes(t));
  if (down.length) add("bad", `${down.length} tunnel${down.length === 1 ? "" : "s"} down`, down.map(t => t.name).join(", "));
  const flt = x.iran_filter; if (flt && (flt.level === "filtered" || flt.level === "blocked")) add("bad", "Filtered", "Not reachable from inside Iran");
  if ((x.reach_ir || {}).level === "bad") add("bad", "Bad route", `${x.reach_ir.bad} of ${x.reach_ir.total} Iran servers lose packets toward it`);
  if ((x.net || {}).level === "down") add("bad", "Network cut", "This Iran server cannot reach the internet"); else if ((x.net || {}).level === "bad") add("warn", "Network ⚠", `internet ${x.net.anchor}%, other Iran servers ${x.net.domestic}%, foreign servers ${x.net.foreign}% lost`);
  if (s.online !== false) for (const [v, w, b, l] of [[f.cpu, 80, 92, "CPU"], [f.ram, 85, 92, "RAM"], [f.disk, 85, 95, "Disk"]]) if (v != null && v >= w) add(v >= b ? "bad" : "warn", `${l} ${Math.round(v)}%`, `${l} is at ${Math.round(v)}%`);
  for (const n of (window.pgSrvOn ? pgSrvOn(s) : [])) if (n.status && !["connected", "disabled", "connecting"].includes(n.status)) add("warn", "Node ✕", `${n.panel}: ${n.status}${n.message ? " (" + n.message + ")" : ""}`);
  return out.sort((a, b) => (a.sev === "bad" ? 0 : 1) - (b.sev === "bad" ? 0 : 1));
}
const labSrvTone = s => !s.enabled ? "off" : srvBusy(s) ? "busy" : s.online == null ? "off" : labIssues(s).some(i => i.sev === "bad") ? "bad" : labIssues(s).length ? "warn" : "ok";
function labNodeInfo(p) {      // a PasarGuard node profile: what the health job saw, and - once the panel was asked - what it reports about the node
  const r = labNodeRow(p), d = labNodeData(p), st = (d && d.status) || (r && r.status) || p.status || "";
  return {r, d, st, live: (d && d.live) || null, msg: (d && d.message) || (r && r.message) || "", tone: p.missing || !r ? "bad" : st === "connected" ? "ok" : st === "disabled" ? "off" : st === "connecting" ? "busy" : "bad"};
}
const labTone = p => p.kind === "node" ? labNodeInfo(p).tone : (s => s ? labSrvTone(s) : "bad")(labServer(p));
const labRank = p => ({bad: 0, warn: 1, busy: 2, ok: 3, off: 4})[labTone(p)];
function labStateHtml(s) {
  const b = srvBusy(s); if (b) return `<span class="lab-st busy" title="${esc(b.what + (b.detail ? ": " + b.detail : ""))}"><i class="spin"></i>${esc(b.what)}…</span>`;
  if (!s.enabled) return '<span class="lab-st off" title="Disabled: the panel does not check it and gives no alerts"><i></i>Disabled</span>';
  if (s.online == null) return '<span class="lab-st off" title="The panel has not looked at it yet"><i></i>Not checked yet</span>';
  const is = labIssues(s); if (!is.length) return `<span class="lab-st ok" title="Answers over SSH${s.last_seen_at ? ", last seen " + ago(s.last_seen_at) + " ago" : ""}"><i></i>Online</span>`;
  return `<span class="lab-st ${is.some(i => i.sev === "bad") ? "bad" : "warn"}" title="${esc(is.map(i => i.tip).join("\n"))}"><i></i>${esc(is[0].txt)}${is.length > 1 ? `<em>+${is.length - 1}</em>` : ""}</span>`;
}
const labCells = (strip, tip) => `<span class="lab-strip" title="${esc(tip)}">${[...(strip || "")].map(c => `<i class="c${c === "-" ? "n" : c}"></i>`).join("")}</span>`;
const labBar = (v, warn, bad) => v == null ? '<span class="mut">—</span>' : `<span class="bar"><i class="${v >= bad ? "bad" : v >= warn ? "warn" : ""}" style="width:${Math.min(100, v)}%"></i></span><b class="${v >= bad ? "bad" : v >= warn ? "warn" : ""}">${Math.round(v)}%</b>`;
const labNum = (v, warn, bad) => v == null ? "—" : `<span class="${v >= bad ? "bad" : v >= warn ? "warn" : ""}">${Math.round(v)}%</span>`;

/* ---------------------------------------------------------------- the rows */
function labServes(s) {      // what the server serves: the nodes that run on it and its tunnels (nothing = a quiet dash)
  const nodes = window.pgSrvChips ? pgSrvChips(s) : "", mine = (state.tunnelsCache || []).filter(t => t.iran_name === s.name || t.foreign_name === s.name);
  const worst = mine.some(t => t.state === "down") ? "bad" : mine.some(t => t.state !== "up") ? "warn" : "ok";
  const tun = mine.length ? `<span class="mchip ${worst === "ok" ? "ok" : worst === "bad" ? "bad" : ""}" title="${esc(mine.map(t => t.name + ": " + t.state).join("\n"))}">⇄ ${mine.length}</span>` : "";
  return nodes + tun || '<span class="mut">—</span>';
}
function labServerRow(p) {
  const s = labServer(p); if (!s) return labGoneRow(p);
  const f = s.facts || {}, tone = labSrvTone(s), st = LAB.strips[s.id], on = s.enabled, cc = s.role === "iran" ? "ir" : /^[A-Za-z]{2}$/.test(s.country || "") ? s.country.toLowerCase() : "";
  const q = [p.label, s.name, s.host, s.provider, s.role, s.country, f.hostname, ((s.extra || {}).geo || {}).provider, s.online === false ? "offline off down" : s.online ? "online up" : ""].filter(Boolean).join(" ").toLowerCase();
  /* the card of a row and its icons are the ones of the old Servers list (srv-tbl: the flag over the name, the server with its two lights); what is IN the columns is the new design */
  return `<tr class="${labTrCls(tone, on, s.online)} clk" data-id="${p.id}" data-q="${esc(q)}" onclick="labOpen(${p.id})" title="${esc(f.top ? "Busiest process: " + f.top + " · " : "")}Click for the details">
    <td class="name ${cc ? "has-flag" : ""}" ${cc ? `style="--flag:url(/flags/${cc}.png)" title="${cc.toUpperCase()}"` : ""}><div class="lab-nmw"><i class="srv-ic" title="${!on ? "disabled" : s.online ? "online" : s.online === false ? "offline" : "not checked yet"}">${SRV_IC}</i><div class="lab-nm"><b>${esc(s.name)}${isNew(s) ? '<span class="pill new" title="Added in the last hour">new</span>' : ""}</b><small class="mono" title="${esc(s.host)}">${esc(s.host)}${s.provider ? " · " + esc(s.provider) : ""}</small></div></div></td>
    <td class="lab-serves">${labServes(s)}</td>
    <td><div class="lab-stw">${labStateHtml(s)}${on && st ? labCells(st.strip, `Last 24 hours, heard over SSH ${st.up == null ? "—" : st.up + " %"} of the time (one cell = one hour)`) : ""}</div></td>
    <td>${on && s.online !== false && f.cpu != null ? `<div class="lab-ldw"><span class="lab-cpu">${labBar(f.cpu, 80, 92)}</span><small>RAM ${labNum(f.ram, 85, 92)} · Disk ${labNum(f.disk, 85, 95)}</small></div>` : '<span class="mut">—</span>'}</td>
    <td class="lab-trf">${on && f.rx_mbps != null ? `<span class="trf"><i class="ta d">↓</i>${f.rx_mbps}</span><span class="trf"><i class="ta u">↑</i>${f.tx_mbps}</span>` : '<span class="mut">—</span>'}</td>
    <td><div class="td-act">${labKebab("row", p.id, "Actions of " + s.name)}</div></td></tr>`;
}
const labTrCls = (tone, on, online) => (!on ? "srv-off" : online ? "up" : online === false ? "down" : "") + " " + ({bad: "sev-red", warn: "sev-yellow", busy: "sev-blue"})[tone];      // (the classes the old list gave its rows: they colour the icon and the edge of the card)
const LAB_NODE_IC = '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="b" d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path class="b" d="M4 7.5l8 4.5 8-4.5M12 12v9"/><circle class="led" cx="12" cy="12" r="1.2"/></svg>';      // (a node, drawn like the server of the old list: the same strokes, the same light)
function labNodeRowHtml(p) {
  const I = labNodeInfo(p), d = I.d, r = I.r || {}, live = I.live, onSrv = Object.entries(typeof pgSrv !== "undefined" ? pgSrv.nodes || {} : {}).find(([, rows]) => rows.some(x => x.panel_id === p.panel_id && x.node_id === p.node_id)), srv = onSrv ? (state.serversCache || []).find(s => s.id === +onSrv[0]) : null;
  const addr = d ? `${d.address}:${d.port}` : p.address || r.address || "", ver = d && d.version ? " · v" + d.version : "";
  const stTxt = p.missing || !I.r ? "Gone from the panel" : ({connected: "Connected", connecting: "Connecting", disabled: "Disabled", error: "Error"})[I.st] || (I.st ? I.st[0].toUpperCase() + I.st.slice(1) : "Unknown");
  const q = ["pasarguard node", p.name, p.address, p.panel, I.st, srv && srv.name].filter(Boolean).join(" ").toLowerCase();
  return `<tr class="${labTrCls(I.tone, I.tone !== "off", I.tone === "ok" ? true : I.tone === "bad" ? false : null)} clk" data-id="${p.id}" data-q="${esc(q)}" onclick="labOpen(${p.id})" title="Click for the node">
    <td class="name"><div class="lab-nmw"><i class="srv-ic" title="PasarGuard node">${LAB_NODE_IC}</i><div class="lab-nm"><b>${esc(p.name)}</b><small class="mono" title="${esc(addr)}">${esc(addr)}${esc(ver)}</small></div></div></td>
    <td class="lab-serves"><span class="mchip ${I.tone === "ok" ? "ok" : I.tone === "bad" ? "bad" : ""}" onclick="event.stopPropagation();pgSrvOpenPanel(${p.panel_id}, 1)" title="The panel ${esc(p.panel)}: click to open it">${esc(p.panel)}</span>${srv ? `<span class="mchip" title="It runs on the server ${esc(srv.name)}">on ${esc(srv.name)}</span>` : ""}</td>
    <td><div class="lab-stw"><span class="lab-st ${I.tone}" title="${esc(I.msg || stTxt)}"><i></i>${esc(stTxt)}</span>${I.d && I.d.xray ? `<small class="mut lab-sub">Xray ${esc(I.d.xray)}</small>` : ""}</div></td>
    <td>${live && live.cpu != null ? `<div class="lab-ldw"><span class="lab-cpu">${labBar(live.cpu, 80, 92)}</span><small>RAM ${labNum(live.ram, 85, 92)}${live.cores ? " · " + live.cores + (live.cores === 1 ? " core" : " cores") : ""}</small></div>` : '<span class="mut">—</span>'}</td>
    <td class="lab-trf">${live && live.rx != null ? `<span class="trf"><i class="ta d">↓</i>${labMbps(live.rx)}</span><span class="trf"><i class="ta u">↑</i>${labMbps(live.tx)}</span>` : '<span class="mut">—</span>'}</td>
    <td><div class="td-act">${labKebab("row", p.id, "Actions of " + p.name)}</div></td></tr>`;
}
const labGoneRow = p => `<tr class="down sev-red clk" data-id="${p.id}" data-q="${esc((p.name + " gone").toLowerCase())}"><td class="name"><div class="lab-nmw"><i class="srv-ic">${p.kind === "node" ? LAB_NODE_IC : SRV_IC}</i><div class="lab-nm"><b>${esc(p.name)}</b><small>not in the panel any more</small></div></div></td><td></td><td><div class="lab-stw"><span class="lab-st bad"><i></i>Gone</span></div></td><td></td><td></td><td><div class="td-act">${labKebab("row", p.id, "Actions of " + p.name)}</div></td></tr>`;
const labRowOf = p => p.kind === "node" ? labNodeRowHtml(p) : labServerRow(p);

/* ---------------------------------------------------------------- the page */
PAGES.lab = async () => {
  const [pr, list, tl, pnl, st] = await Promise.all([api("/api/profiles"), api("/api/servers"), api("/api/tunnels").catch(() => []), api("/api/panels").catch(() => null), api("/api/profiles/strips?hours=24&buckets=24").catch(() => null)]);
  state.serversCache = list; state.tunnelsCache = tl; state.panelsCache = pnl || state.panelsCache || {panels: []}; LAB.profiles = pr.profiles;
  if (window.pgSrvLoad) await pgSrvLoad();
  if (!labOn()) return;      // another tab or page was chosen while this one was reading: it is not drawn over it
  if (pnl) labLockSet((pnl.panels || []).length > 0);      // (the Panel item is open while a panel is connected; a failed read changes nothing)
  LAB.strips = {}; if (st) for (const [k, v] of Object.entries(st.servers || {})) LAB.strips[+k] = v;
  if (!LAB_KINDS.some(k => k[0] === LAB.kind)) LAB.kind = "any";
  const openSrv = list.find(s => s.id === state.open);      // (any server: a shortcut of the dashboard or a diagnosis opens one that was taken off the page too)
  const showWin = () => { try { if (openSrv) srvModal(openSrv); else if (state.nodeWin && document.querySelector("#modal .modal")) { /* a window of the Lab or of a node is open: it stays */ } else hideModal(); } catch (e) { toast("✕ The window could not be drawn: " + e.message, "bad"); } };
  if (list.some(srvBusy)) { clearTimeout(window.__busyT); window.__busyT = setTimeout(() => { if (labOn() && !state.open && !state.nodeWin) refresh(); }, 3000); }
  pageTarget().innerHTML = LAB.profiles.length ? labPage() : labFirst();
  showWin();
  applyQ();
  if (openSrv && state.edit !== openSrv.id) labAfter(openSrv);
  labLoadNodes();
  labNavSync();      // (the menu: the kinds that have a profile, and which one is open)
  if (LAB.pending) { const k = LAB.pending; LAB.pending = null; if (k === "add") labAdd(); else if (k === "connect") labConnect(); else if (typeof k === "object") labServerStep(k.kind || "foreign", "new", k); else labAddKind(k); }      // "Add profile" of the menu, "Add a PasarGuard profile" of the message the Panel item gives, a node outside our servers to add: the add flow opens at once
};
function labFirst() {      // no profile yet: nothing else works until one is added
  const have = labFree("iran").length + labFree("foreign").length;
  return `<div class="lab-first"><div class="lab-first-card"><div class="lab-first-ic">${SI.server}</div><h2>Start with a profile</h2>
    <p>Everything on this page starts with a profile - in place of the Iran and Foreign tabs. Choose what you are adding:</p>${labChooser()}${have ? `<p class="mut lab-first-note">The panel has ${have} server${have === 1 ? "" : "s"} already. Nothing puts them here by itself: their information (name, address, provider) is under <b>Already in the panel</b>, where you pick some - or all - of them.</p>` : ""}</div></div>`;
}
const labHasPg = () => !!(window.pgSrvPanels && pgSrvPanels().length);      // a PasarGuard panel is connected: only then can a node be added (the owner: "this option is active when we have added a PasarGuard panel")
function labChooser() {
  const noPg = !labHasPg();
  const card = (k, title, text, ic) => { const n = k === "node" ? 0 : labFree(k).length, off = k === "node" && noPg;      // (a div, like the tiles of the console: the buttons of a window have a stack of rules of their own)
    return `<div role="button" tabindex="0" class="lab-card${off ? " off" : ""}" ${off ? 'aria-disabled="true" title="Connect a PasarGuard panel first"' : ""} onclick="labAddKind('${k}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();labAddKind('${k}')}"><i>${ic}</i><b>${title}</b><small>${text}${off ? '<em class="lab-have">Connect a PasarGuard panel first</em>' : n ? `<em class="lab-have">${n} already in the panel</em>` : ""}</small></div>`; };
  return `<div class="lab-choose">${card("iran", "Iran server", "A server inside Iran: the end of your tunnels, watched over SSH.", flagImg("IR") || "IR")}${card("foreign", "Foreign server", "A server abroad: a tunnel's other end, a node, the filtering check.", "🌍")}${card("node", "PasarGuard node", "A node of your PasarGuard panel, managed through the panel's API.", SI.cube)}</div>`;
}
/* the tabs are not made in advance (the owner: "the sections must not be built beforehand"): a tab of a kind is there only while a profile of that kind is, and the bar is there only when there are two kinds or more - one kind is the whole list */
const labKinds = () => LAB_KINDS.filter(([k]) => k !== "any" && LAB.profiles.some(p => p.kind === k)).map(k => k[0]);
const labKindNow = () => labKinds().length > 1 && labKinds().includes(LAB.kind) ? LAB.kind : "any";
function labShown() { const k = labKindNow(); return k === "any" ? LAB.profiles : LAB.profiles.filter(p => p.kind === k); }
function labListInner(shown) {
  if (!shown.length) return `<div class="lab-empty"><b>Nothing here yet.</b><span class="mut">Add the first one.</span><button class="pri" onclick="labAdd()">＋ Add profile</button></div>`;
  const rows = shown.slice().sort((a, b) => labRank(a) - labRank(b) || a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name, undefined, {sensitivity: "base"}));
  return `<table class="tbl lift srv-tbl joined lab-tbl"><colgroup><col style="width:27%"><col style="width:16%"><col style="width:21%"><col style="width:17%"><col style="width:12%"><col style="width:7%"></colgroup>
    <thead><tr><th>Profile</th><th data-prio="3">Serves</th><th data-prio="1">State · last 24 h</th><th data-prio="2">Load</th><th data-prio="4">Traffic Mb/s</th><th></th></tr></thead><tbody>${rows.map(labRowOf).join("")}</tbody></table>`;      // (the table of the old Servers list: its cards, its icons, and the fluid layer that drops the less important columns when the box is narrow)
}
function labPage() {
  const all = LAB.profiles, shown = labShown(), srvs = shown.filter(p => p.kind !== "node").map(labServer).filter(Boolean), nodes = shown.filter(p => p.kind === "node");
  const live = srvs.filter(s => s.enabled), up = live.filter(s => s.online === true).length, nLive = nodes.filter(p => labNodeInfo(p).tone !== "off"), nUp = nLive.filter(p => labNodeInfo(p).tone === "ok").length;
  const bad = shown.filter(p => labTone(p) === "bad").length, warn = shown.filter(p => labTone(p) === "warn").length;
  const ids = new Set(srvs.map(s => s.id)), tuns = (state.tunnelsCache || []).filter(t => ids.has(t.iran_server_id) || ids.has(t.foreign_server_id) || srvs.some(s => s.name === t.iran_name || s.name === t.foreign_name)), tdown = tuns.filter(t => t.state === "down").length;
  const cpuv = live.filter(s => s.online && s.facts && s.facts.cpu != null).map(s => s.facts.cpu), cpu = cpuv.length ? Math.round(cpuv.reduce((a, b) => a + b, 0) / cpuv.length) : null;
  const chip = (cls, id, ic, lab, val, tip) => `<span class="lab-chip ${cls} c-${id}" data-ic="${ic}" title="${esc(tip)}"><span>${lab}</span><b>${val}</b></span>`;
  const cnt = k => k === "any" ? all.length : all.filter(p => p.kind === k).length;
  const kinds = labKinds(), cur = labKindNow(), tabs = kinds.length > 1 ? `<div class="tabs lab-kinds">${LAB_KINDS.filter(([k]) => k === "any" || kinds.includes(k)).map(([k, l]) => `<button class="${cur === k ? "on" : ""}" onclick="labKind('${k}')">${l}<i class="cnt">${cnt(k)}</i></button>`).join("")}</div>` : "";
  /* ONE row (the owner: "fit everything in one row", "take responsiveness into account"): the tabs, what the page says in four small chips, the search and the two buttons; as the box narrows the chips go one by one
     (CPU, Tunnels), then lose their words, then go; the buttons keep their icons only; nothing wraps (lab.css) */
  return `<div class="lab-root"><div class="lab-bar">${tabs}<span class="lab-stats">${chip(up + nUp < live.length + nLive.length ? "bad" : "ok", "on", "●", "Online", `${up + nUp}/${live.length + nLive.length}`, "Servers that answer and nodes that are connected, of those that are switched on")}${chip(bad ? "bad" : warn ? "warn" : "ok", "pb", "!", "Problems", bad + warn, "Profiles with something wrong (red: cut or critical, yellow: watch)")}${srvs.length ? chip(tdown ? "bad" : "ok", "tun", "⇄", "Tunnels", `${tuns.length - tdown}/${tuns.length}`, "Tunnels that touch these servers: up / all") : ""}${srvs.length ? chip((cpu ?? 0) >= 80 ? "bad" : (cpu ?? 0) >= 60 ? "warn" : "ok", "cpu", "◔", "CPU", cpu == null ? "—" : cpu + "%", "Average CPU of the servers that answer") : ""}</span><span class="lab-sp"></span>${searchSeg()}<button class="pri sm lab-add" onclick="labAdd()" title="Add a profile: an Iran server, a foreign server or a PasarGuard node" aria-label="Add profile"><span class="lab-ic">＋</span><span class="lab-lb">Add profile</span></button><button class="sm lab-chk" onclick="labCheck()" title="Look at every profile on the page now (one SSH look at each server, one question to each panel)" aria-label="Check"><span class="lab-ic">✓</span><span class="lab-lb">Check</span></button></div>
    <div id="lab-list" class="${shown.length ? "" : "lab-list"}">${labListInner(shown)}</div></div>`;
}
window.labKind = k => { LAB.kind = k; localStorage.setItem("nk.lab.k", k); const pg = $("#page"); if (pg && labOn()) { pageTarget().innerHTML = labPage(); applyQ(); } labNavSync(); };      // the tabs filter what is already read: no call, no wait (the menu marks the same kind)
async function labLoadNodes() {      // the panels of the node profiles are asked (what they report about each node: its load and speed), then the list is drawn again with it
  const pids = [...new Set(LAB.profiles.filter(p => p.kind === "node").map(p => p.panel_id))];
  if (!pids.length || typeof pgNodesLoad !== "function") return;
  await Promise.all(pids.map(pid => pgNodesLoad(pid).catch(() => null)));
  const el = $("#lab-list"); if (el && labOn() && !state.open && !state.nodeWin) { const sh = labShown(); el.className = sh.length ? "" : "lab-list"; el.innerHTML = labListInner(sh); applyQ(); }
}

/* ---------------------------------------------------------------- adding a profile: Iran server | Foreign server | PasarGuard node */
const labModal = (title, body) => { state.nodeWin = state.nodeWin || {lab: 1}; showModal(title, body, "keep"); };
window.labAdd = () => labModal("<b>Add a profile</b>", `<div class="lab-pfwin"><p class="mut lab-lead">What are you adding?</p>${labChooser()}</div>`);
window.labAddKind = k => { if (k === "node") { if (labHasPg()) labNodePanel(); else labPgFirst(); } else labServerStep(k, labFree(k).length ? "have" : "new"); };      // (servers the panel already has: they are listed first; a node needs a connected PasarGuard panel)
window.labPgFirst = async () => {      // the option of a node is grey while no PasarGuard panel is connected: asking for it says why and leads to connecting one
  const v = await glassChoice("A PasarGuard node belongs to a PasarGuard panel, so the panel comes first: connect it with its API key (or its username and password). This option opens as soon as one is connected.", [{label: "Connect a PasarGuard panel", v: "go", cls: "pri"}]);
  if (v === "go") labConnect();
};
window.labConnect = () => labNodePanel(true);      // only the panel: the same screen as the node's first step, and no node is asked for
const labBack = '<div class="wback"><button type="button" class="sm" onclick="labAdd()">← Back</button></div>';
window.labServerStep = (kind, tab, fill) => {
  const noun = kind === "iran" ? "Iran" : "foreign", free = labFree(kind);
  const lb = (full, short) => `<span class="lb-f">${full}</span><span class="lb-s">${short}</span>`;      // (a phone shows the short name: the three tabs stay one row)
  const tabs = `<div class="tabs lab-addtabs"><button type="button" class="${tab === "new" ? "on" : ""}" onclick="labServerStep('${kind}', 'new')">${lb("A new server", "New")}</button><button type="button" class="${tab === "many" ? "on" : ""}" onclick="labServerStep('${kind}', 'many')">${lb("Several at once", "Several")}</button><button type="button" class="${tab === "have" ? "on" : ""}" onclick="labServerStep('${kind}', 'have')">${lb("Already in the panel", "In the panel")}<i class="cnt">${free.length}</i></button></div>`;
  const form = `<form class="lab-pf" onsubmit="return labSaveServer(event, '${kind}')" autocomplete="off">
    <div class="lab-pf-two"><label><span>Name</span><input name="name" required maxlength="64" pattern="[A-Za-z0-9][A-Za-z0-9._\\-]*" placeholder="${kind === "iran" ? "ir7" : "de4"}" title="Letters, digits . _ - (a name is never used twice)" value="${esc((fill || {}).name || "")}"></label><label><span>Address</span><input name="host" required maxlength="128" pattern="[A-Za-z0-9][A-Za-z0-9.\\-]*" placeholder="203.0.113.10" value="${esc((fill || {}).host || "")}"></label></div>
    <div class="lab-pf-two"><label><span>SSH user</span><input name="ssh_user" value="root" maxlength="32"></label><label><span>SSH port</span><input name="ssh_port" type="number" min="1" max="65535" value="22"></label></div>
    <label><span>SSH password <small>(optional)</small></span><input name="password" type="password" autocomplete="new-password" placeholder="empty = the panel's key is already on the server"></label>
    <small class="mut">With a password the panel logs in once, installs its own key and forgets the password. Without one, the panel's key must already be in the server's authorized_keys (Settings shows the key).</small>
    <div class="lab-pf-act"><button class="pri" type="submit">Add ${noun} server</button></div></form>`;
  const many = `<form class="lab-pf" onsubmit="return labSaveMany(event, '${kind}')" autocomplete="off">
    <label><span>One server per line</span><textarea name="lines" required rows="6" spellcheck="false" placeholder="de4 203.0.113.9&#10;nl2 203.0.113.10 root 22"></textarea></label>
    <small class="mut">name address [user] [port] - the user and the port are optional</small>
    <div class="lab-pf-two"><label><span>SSH user</span><input name="ssh_user" value="root" maxlength="32"></label><label><span>SSH port</span><input name="ssh_port" type="number" min="1" max="65535" value="22"></label></div>
    <label><span>SSH password <small>(the same for all, optional)</small></span><input name="password" type="password" autocomplete="new-password" placeholder="empty = the panel's key is already on the servers"></label>
    <div class="lab-pf-act"><button class="pri" type="submit">Add the ${noun} servers</button><small class="mut">With a password the panel logs in to each once, installs its key and forgets it.</small></div></form>`;
  const have = free.length ? `<div class="lab-pick-bar"><input type="search" class="lab-pick-q" placeholder="Search…" oninput="labPickFilter(this.value)" autocomplete="off" spellcheck="false"><label class="lab-all"><input type="checkbox" onchange="labPickAll(this.checked)"> Select all</label></div><div class="lab-pick-list">${free.map(s => `<label class="lab-pick-row" data-q="${esc((s.name + " " + s.host + " " + (s.provider || "")).toLowerCase())}"><input type="checkbox" value="${s.id}" onchange="labPickCount()"><span class="lab-fl">${flagImg(s.role === "iran" ? "IR" : s.country) || ""}</span><b>${esc(s.name)}</b><span class="mono mut">${esc(s.host)}</span><span class="mut">${esc(s.provider || "")}</span></label>`).join("")}</div>
    <div class="lab-pf-act"><button class="pri" id="lab-pick-go" onclick="labPickGo('${kind}')" disabled>Add</button><small class="mut">Nothing is done to a server: it only joins the page.</small></div>` : `<div class="lab-empty"><span class="mut">Every ${noun} server of the panel is on the page already.</span></div>`;
  labModal(`<b>Add ${kind === "iran" ? "an Iran" : "a foreign"} server</b>`, `<div class="lab-pfwin lab-wide">${labBack}${tabs}${tab === "new" ? form : tab === "many" ? many : have}</div>`);
  if (tab === "new" || tab === "many") setTimeout(() => { const i = document.querySelector(".lab-pf input[name=name], .lab-pf textarea"); if (i) i.focus(); }, 60);
};
window.labAddWith = async (name, host) => {      // a node outside our servers ("Add to servers"): Add profile opens on a new server with its name and address filled in; where it is (Iran or foreign) is found from the address
  const fill = {kind: "foreign", name: String(name || "").replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[^A-Za-z0-9]+/, "").slice(0, 60), host: String(host || "").replace(/^\[|\]$/g, "")};
  try { const w = await api("/api/servers/where?host=" + encodeURIComponent(fill.host)); if (w.role === "iran") fill.kind = "iran"; } catch (e) { /* no geo answer: abroad is the usual place of a node; the form says which kind it is and Back changes it */ }
  LAB.pending = fill; if (labOn()) refresh(); else go("nodes", "lab");
};
window.labPickFilter = v => { document.querySelectorAll(".lab-pick-row").forEach(r => r.classList.toggle("q-hide", !nkHit(r.dataset.q, v))); labPickCount(); };
window.labPickAll = on => { document.querySelectorAll(".lab-pick-row:not(.q-hide):not(.off) input:not(:disabled)").forEach(i => { i.checked = on; }); labPickCount(); };      // (the rows the search shows)
window.labPickCount = () => {
  const n = document.querySelectorAll(".lab-pick-row input:checked").length, b = $("#lab-pick-go"), all = document.querySelector(".lab-all input"), open = [...document.querySelectorAll(".lab-pick-row:not(.q-hide):not(.off) input:not(:disabled)")];
  if (b) { b.disabled = !n; b.textContent = n ? `Add ${n} ${b.dataset.noun || "server"}${n === 1 ? "" : "s"}` : "Add"; }
  if (all) all.checked = open.length > 0 && open.every(i => i.checked);      // ("Select all" follows the boxes: it is ticked only while every shown row is)
};
window.labPickGo = async kind => {
  const ids = [...document.querySelectorAll(".lab-pick-row input:checked")].map(i => +i.value); if (!ids.length) return;
  try { const r = await api("/api/profiles", {method: "POST", body: JSON.stringify({kind, server_ids: ids})}); toast("✓ Added: " + r.added.map(x => x.name).join(", ") + (r.skipped.length ? ` (${r.skipped.length} skipped: ${r.skipped[0].why})` : ""), "ok"); closeModal(); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); }
};
window.labSaveServer = async (e, kind) => {
  e.preventDefault(); const f = new FormData(e.target), v = k => String(f.get(k) || "").trim(), pw = f.get("password") || "";
  const body = {name: v("name"), host: v("host"), role: kind, ssh_user: v("ssh_user") || "root", ssh_port: +v("ssh_port") || 22, provider: "", enabled: true};
  const have = (state.serversCache || []).find(s => s.host === body.host || s.name.toLowerCase() === body.name.toLowerCase());
  if (have) { toast(`✕ The panel knows ${have.name} (${have.host}) already: pick it in "Already in the panel"`, "bad"); labServerStep(kind, "have"); return false; }
  try {
    const saved = await api("/api/servers", {method: "POST", body: JSON.stringify(body)});      // (a server is on the page the moment it is made: the panel gives it its profile)
    closeModal(); refresh();      // the new row appears at once; it says "connecting…" while the password login runs
    if (pw) { toast("Server added - connecting with the password…"); try { const r = await api(`/api/servers/${saved.id}/connect`, {method: "POST", body: JSON.stringify({password: pw})}); toast("✓ Added and connected: " + r.hostname, "ok"); } catch (err) { await connectWithPrompt(saved.id, body.name, err.message); } }
    else { try { await api(`/api/servers/${saved.id}/check`, {method: "POST"}); toast("✓ Server added", "ok"); } catch (err) { toast("Added, but the panel's key does not log in yet: " + err.message, "bad"); } }
    refresh();
  } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};
window.labSaveMany = async (e, kind) => {      // several at once: the lines are made servers (and so profiles), each logged in with the same password; what did not work is listed
  e.preventDefault(); const f = new FormData(e.target); toast("Adding… (a few seconds per server)");
  try {
    const r = await api("/api/servers/add-many", {method: "POST", body: JSON.stringify({role: kind, lines: f.get("lines"), password: f.get("password") || "", ssh_user: String(f.get("ssh_user") || "root").trim() || "root", ssh_port: +f.get("ssh_port") || 22})});
    const ok = r.items.filter(i => i.ok).length, all = ok === r.items.length;
    toast(`${all ? "✓" : "✕"} ${ok} of ${r.items.length} added and connected`, all ? "ok" : "bad"); refresh();
    if (all) closeModal(); else labModal("<b>Several at once</b>", `<div class="lab-pfwin lab-wide"><div class="sbox srv-after"><h5><i>📋</i>Result</h5>${r.items.map(i => `<div class="ev ${i.ok ? "ok" : "bad"}"><time></time><i></i><small>${esc(i.host)}</small><span><b>${esc(i.name)}</b> - ${i.ok ? "✓ " + esc(i.detail || "connected") : "✕ " + esc(i.error || "")}</span></div>`).join("")}</div><small class="mut">A server that was added but did not log in is on the page already: use its menu (Connect SSH) when its password is known.</small></div>`);
  } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};

/* the PasarGuard node: first the panel it belongs to - its API key, or a username and password - then the nodes of that panel */
state.labAuth = "key";
window.labNodePanel = only => {      // only: no node is asked for - just the connecting of a panel (the Panel item and the grey node option lead here)
  state.labOnly = only === true;
  const ps = window.pgSrvPanels ? pgSrvPanels() : [], list = ps.length && !state.labOnly ? `<p class="mut lab-lead">Which PasarGuard panel does the node belong to?</p><div class="lab-panels">${ps.map(p => `<div role="button" tabindex="0" class="lab-panel" onclick="labNodeList(${p.id})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();labNodeList(${p.id})}"><b>${esc(p.name)}</b><small class="mono">${esc(p.url)}</small><span class="mchip ${p.status === "up" ? "ok" : "bad"}">${esc(p.status || "?")}</span></div>`).join("")}</div>` : "";
  labModal(`<b>${state.labOnly ? "Connect a PasarGuard panel" : "Add a PasarGuard node"}</b>`, `<div class="lab-pfwin lab-wide">${state.labOnly ? "" : labBack}${list}
    <details class="lab-conn" ${ps.length && !state.labOnly ? "" : "open"}><summary>${state.labOnly ? "Connect your PasarGuard panel: its nodes are managed through its API" : ps.length ? "＋ Connect another PasarGuard panel" : "Connect your PasarGuard panel first: the node is managed through its API"}</summary>
      <form class="lab-pf" onsubmit="return labConnectPanel(event)" autocomplete="off">
        <label><span>Panel address</span><input name="url" required maxlength="200" placeholder="https://panel.example.com:8000"></label>
        <label><span>Name <small>(optional)</small></span><input name="name" maxlength="40" placeholder="taken from the address"></label>
        <div class="lab-pf-row"><span>Connect with</span><div class="tabs lab-auth"><button type="button" class="on" data-m="key" onclick="labAuth('key')">API key</button><button type="button" data-m="login" onclick="labAuth('login')">Username and password</button></div></div>
        <div class="lab-auth-key"><label><span>API key</span><input name="token" type="password" autocomplete="new-password" spellcheck="false" placeholder="pg_key_…"></label><small class="mut">Made in the dashboard of the panel under API Keys (shown once). It stays on this server and is tried on the panel before it is kept.</small></div>
        <div class="lab-auth-login hidden"><div class="lab-pf-two"><label><span>Username</span><input name="username" maxlength="64"></label><label><span>Password</span><input name="password" type="password" autocomplete="new-password" maxlength="200"></label></div>
          <label class="chk lab-chk"><input type="checkbox" name="keep"> Keep the login, so the panel renews its token by itself</label><small class="mut">Without it the password is used once to get a token and is forgotten. A PasarGuard token lasts 24 hours unless the panel was set longer - an API key is better.</small></div>
        <label class="chk lab-chk"><input type="checkbox" name="tls" checked> Check the TLS certificate of the panel</label>
        <div class="lab-pf-act"><button class="pri" type="submit">${state.labOnly ? "Connect the panel" : "Connect and choose the node"}</button></div></form></details></div>`);
};
window.labAuth = m => { state.labAuth = m; document.querySelectorAll(".lab-auth button").forEach(b => b.classList.toggle("on", b.dataset.m === m)); const k = document.querySelector(".lab-auth-key"), l = document.querySelector(".lab-auth-login"); if (k) k.classList.toggle("hidden", m !== "key"); if (l) l.classList.toggle("hidden", m !== "login"); };
window.labConnectPanel = async e => {
  e.preventDefault(); const f = new FormData(e.target), v = k => String(f.get(k) || "").trim(), m = state.labAuth, url = v("url");
  if (!/^https?:\/\/[^\s/]+/i.test(url)) { toast("✕ The address starts with http:// or https://", "bad"); return false; }
  if (m === "key" && !v("token")) { toast("✕ Paste the API key of the panel", "bad"); return false; }
  if (m === "login" && (!v("username") || !f.get("password"))) { toast("✕ Type the username and the password of the panel", "bad"); return false; }
  let host = "panel"; try { host = new URL(url).hostname; } catch (er) { /* the check above passed: this cannot fail */ }
  const body = {name: v("name") || host.slice(0, 40), type: "pasarguard", url: url.replace(/\/+$/, ""), token: m === "key" ? v("token") : "", username: m === "login" ? v("username") : "", password: m === "login" ? String(f.get("password")) : "", keep_login: m === "login" && !!f.get("keep"), verify_tls: !!f.get("tls")};
  const btn = e.target.querySelector("button[type=submit]"), label = btn ? btn.textContent : ""; if (btn) { btn.disabled = true; btn.textContent = "Trying it on the panel…"; }
  try {
    const r = await api("/api/panels", {method: "POST", body: JSON.stringify(body)});
    state.panelsCache = await api("/api/panels").catch(() => state.panelsCache); toast(`✓ Connected: ${r.panel.name}`, "ok");
    labLockSet(true);      // (a panel is connected: the Panel item of the menu opens, and the option of a node)
    if (state.labOnly) { state.labOnly = false; closeModal(); refresh(); return false; }      // (only the panel was asked for)
    labNodeList(r.panel.id);
  } catch (err) { toast("✕ " + err.message, "bad"); if (btn) { btn.disabled = false; btn.textContent = label; } }
  return false;
};
window.labNodeList = async pid => {
  const p = (window.pgSrvPanels ? pgSrvPanels() : []).find(x => x.id === pid), name = p ? esc(p.name) : "the panel", back = `<div class="wback"><button type="button" class="sm" onclick="labNodePanel()">← Back</button></div>`;
  labModal("<b>Add a PasarGuard node</b>", `<div class="lab-pfwin lab-wide">${back}<div class="mut"><i class="spin"></i> Reading the nodes of ${name}…</div></div>`);
  try {
    const d = await pgNodesLoad(pid, true), on = new Set(LAB.profiles.filter(x => x.kind === "node" && x.panel_id === pid).map(x => x.node_id)), nodes = (d.nodes || []).slice().sort((a, b) => a.name.localeCompare(b.name, undefined, {sensitivity: "base"}));
    const row = n => `<label class="lab-pick-row lab-node-row ${on.has(n.id) ? "off" : ""}" data-q="${esc((n.name + " " + n.address).toLowerCase())}"><input type="checkbox" value="${n.id}" ${on.has(n.id) ? "disabled" : ""} onchange="labPickCount()"><span class="lab-fl lab-nodeic">${SI.cube}</span><b>${esc(n.name)}</b><span class="mono mut">${esc(n.address)}:${n.port}</span><span class="mchip ${n.status === "connected" ? "ok" : n.status === "disabled" || n.status === "connecting" ? "" : "bad"}">${esc(n.status)}</span>${on.has(n.id) ? '<span class="pill">on the page</span>' : ""}</label>`;
    labModal("<b>Add a PasarGuard node</b>", `<div class="lab-pfwin lab-wide">${back}<p class="mut lab-lead">Nodes of ${name} - choose the ones to put on the page:</p>${nodes.length ? `<input type="search" class="lab-pick-q" placeholder="Search…" oninput="labPickFilter(this.value)" autocomplete="off" spellcheck="false"><div class="lab-pick-list">${nodes.map(row).join("")}</div>` : '<div class="lab-empty"><span class="mut">This panel has no node yet.</span></div>'}
      <div class="lab-pf-act"><button class="pri" id="lab-pick-go" data-noun="node" onclick="labNodeGo(${pid})" disabled>Add</button><button type="button" class="sm" onclick="closeModal();pgNodesPanelDo(${pid}, 'add')" title="Make a node in the panel by hand (the form of the Nodes… menu)">＋ A new node in the panel…</button></div></div>`);
  } catch (err) { labModal("<b>Add a PasarGuard node</b>", `<div class="lab-pfwin lab-wide">${back}<div class="err">${esc(err.message)}</div><small class="mut">The panel did not answer with this connection. Check its address, or replace the API key in the Panel page (Panel → General).</small></div>`); }
};
window.labNodeGo = async pid => {
  const ids = [...document.querySelectorAll(".lab-pick-row input:checked")].map(i => +i.value); if (!ids.length) return;
  try { const r = await api("/api/profiles", {method: "POST", body: JSON.stringify({kind: "node", panel_id: pid, node_ids: ids})}); toast("✓ Added: " + r.added.map(x => x.name).join(", ") + (r.skipped.length ? ` (${r.skipped.length} skipped: ${r.skipped[0].why})` : ""), "ok"); LAB.kind = "node"; localStorage.setItem("nk.lab.k", "node"); labLockSet(true); closeModal(); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); }
};

/* ---------------------------------------------------------------- the Panel item of the menu: grey with a lock only while NO panel is connected (the owner: "we have a panel now, it must not be locked; the lock is for when no panel is connected").
   A panel is connected when it is in the list of panels (its API key or its sign-in was tried and kept): not when a node of it is on the Lab page. For now the one that is made to be used is PasarGuard; others may come. */
LAB.pg = null;      // null: not known yet (nothing is locked), true / false: whether a panel is connected
window.labLocked = () => LAB.pg === false && !document.getElementById("demo-pill");      // (the public demo has no panel to connect: its Panel page stays open)
function labLockPaint() {
  const lock = labLocked();
  document.querySelectorAll('#nav a[data-page="pgpanel"]').forEach(a => { a.classList.toggle("nav-lock", lock); if (lock) { a.title = "No panel is connected: connect one first (Lab → Add profile → PasarGuard node)"; a.setAttribute("aria-disabled", "true"); } else a.removeAttribute("aria-disabled"); });
  if (!lock && typeof navTitles === "function") navTitles();      // (the tooltips the menu normally has)
}
window.labLockSet = has => { LAB.pg = !!has; LAB.pgAt = Date.now(); labLockPaint(); };
window.labLockSync = async force => {      // asked at most every 30 s (every refresh calls it); a failure leaves what is known
  if (!force && LAB.pgAt && Date.now() - LAB.pgAt < 30000) return;
  LAB.pgAt = Date.now();
  try { const [r, p] = await Promise.all([api("/api/panels"), api("/api/profiles")]); state.panelsCache = r; labLockSet((r.panels || []).length > 0); LAB.profiles = p.profiles; labNavSync(); } catch (e) { /* not signed in, or the panel is restarting: nothing changes */ }      // (the same look also tells the menu which kinds have a profile)
};
window.labLockNow = async () => {      // the Panel page was asked for (a reload on it, a shortcut): is it open? When not, the message and the Lab instead
  if (LAB.pg === null) await labLockSync(true);
  if (!labLocked()) return false;
  go("nodes", "lab"); labLockMsg(); return true;
};
window.labLockMsg = async () => {
  const v = await glassChoice("No panel is connected yet, so the Panel section is closed. Connect your PasarGuard panel first - with its API key, or its username and password.", [{label: "Connect a PasarGuard panel", v: "add", cls: "pri"}]);
  if (v === "add") { if (labOn()) labConnect(); else { LAB.pending = "connect"; go("nodes", "lab"); } }      // (the same screen the grey node option leads to: the page asks for it once it is drawn)
};

/* ---------------------------------------------------------------- checking, taking off the page */
window.labCheck = async () => {
  const shown = labShown(), sids = shown.filter(p => p.kind !== "node").map(labServer).filter(s => s && s.enabled).map(s => s.id), pids = [...new Set(shown.filter(p => p.kind === "node").map(p => p.panel_id))];
  if (!sids.length && !pids.length) { toast("Nothing to look at", "bad"); return; }
  toast(`Looking at ${sids.length} server${sids.length === 1 ? "" : "s"}${pids.length ? ` and ${pids.length} panel${pids.length === 1 ? "" : "s"}` : ""}…`);
  const r = await Promise.allSettled([...sids.map(id => api(`/api/servers/${id}/check`, {method: "POST"})), ...pids.map(pid => pgNodesLoad(pid, true))]), bad = r.filter(x => x.status === "rejected").length;
  toast(bad ? `${r.length - bad} of ${r.length} answered` : `✓ All ${r.length} answered`, bad ? "bad" : "ok"); refresh();
};
window.labCheckOne = async id => {
  const p = LAB.profiles.find(x => x.id === id); if (!p) return; toast("Checking…");
  try { if (p.kind === "node") await pgNodesLoad(p.panel_id, true); else await api(`/api/servers/${p.server_id}/check`, {method: "POST"}); toast("✓ Checked", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
  refresh();
};
window.labRemove = async id => {
  const p = LAB.profiles.find(x => x.id === id); if (!p) return;
  if (!await gconfirm(`Take ${p.name} off the Lab page? ${p.kind === "node" ? "The node stays in its panel" : "The panel still watches the server and sends its alerts"}; nothing is done to it, and Add profile → Already in the panel puts it back.`)) return;
  try { await api(`/api/profiles/${id}`, {method: "DELETE"}); toast("✓ Off the page", "ok"); closeModal(); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};

/* ---------------------------------------------------------------- the window of a server profile: Overview | Node | Monitor | Tools (srvModal draws the frame, serverDetailHtml asks labWinHtml for the inside) */
function labShowServer(s, sec) {
  state.edit = null; state.wtab = null; state.nodeWin = null; state.wsec = LAB_SECS.some(x => x[0] === sec) ? sec : "overview"; state.open = s.id; state.winH = null;
  srvModal(s); labAfter(s);
}
window.labOpen = (id, sec) => {      // a profile: its window
  const p = typeof id === "number" ? LAB.profiles.find(x => x.id === id) : null; if (!p) return;
  if (p.kind === "node") { labOpenNode(p); return; }
  const s = labServer(p); if (s) labShowServer(s, sec);
};
window.labOpenServer = (sid, sec) => { const s = (state.serversCache || []).find(x => x.id === sid); if (s) labShowServer(s, sec); };      // a server (the chips of a row, a shortcut of the dashboard): its window, with or without a profile
window.labActs = (s, sw, editing) => { const p = labProfileOf(s.id); return `${sw}${editing ? "" : `<i class="mh-sep"></i>${p ? labKebab("win", p.id, "Actions of " + s.name) : labKebab("swin", s.id, "Actions of " + s.name)}`}<i class="mh-sep"></i>`; };      // (the switch, and ONE menu in place of Console, Edit, Off the page and the rest)
window.labRemoveServer = sid => { const p = labProfileOf(sid); if (p) labRemove(p.id); };
window.labAdopt = async sid => {      // a server that is on no profile (it was taken off the page): back on the page
  const s = (state.serversCache || []).find(x => x.id === sid); if (!s) return;
  try { await api("/api/profiles", {method: "POST", body: JSON.stringify({kind: s.role, server_ids: [sid]})}); toast("✓ " + s.name + " is on the page", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.labWinHtml = s => {
  if (state.wtab === "console") return `<div class="wback"><button class="sm" onclick="wtab('all')">← Back</button></div>` + serverPartHtml(s, "console");
  if (state.wtab === "install") return `<div class="wback"><button class="sm" onclick="wtab('all')">← Back</button></div>` + installHtml(s);
  const sec = LAB_SECS.some(x => x[0] === state.wsec) ? state.wsec : "overview";
  const hasNode = window.pgSrvOn && pgSrvOn(s).length;
  const bar = `<div class="tabs lab-sec">${LAB_SECS.map(([k, l]) => `<button class="${k === sec ? "on" : ""}" onclick="wtab('${k}')">${l}${k === "node" && hasNode ? '<i class="lab-sec-dot"></i>' : ""}</button>`).join("")}</div>`;
  return bar + `<div class="lab-body" data-sec="${sec}">${labSec(s, sec)}</div>`;
};
function labSec(s, sec) {
  if (sec === "node") { const cur = typeof pgNodeCurrent === "function" && window.pgSrvOn ? pgNodeCurrent(pgSrvOn(s)) : null, pg = cur && cur.type === "pasarguard" && cur.node_id != null;
    return (pg ? `<div class="lab-nodebar"><span class="mut">Everything that can be done to this node is in the menu</span>${labKebab("node", s.id, "Actions on the node")}</div>` : "") + (window.pgNodeHost ? pgNodeHost(s) : '<div class="empty">The PasarGuard files did not load.</div>') + `<div class="lab-ins" id="lab-ins-${s.id}"></div>`; }
  if (sec === "monitor") return labUpBox(s, "up") + `<div class="wsec"><h4>Charts</h4>${serverPartHtml(s, "charts")}</div>`;
  return labBanner(s) + serverGridHtml(s) + (s.enabled ? labUpBox(s, "ov") : "") + (window.pgSrvLine ? pgSrvLine(s) : "") + resBox(s);
}
const labUpBox = (s, where) => `<div class="sbox lab-upbox" data-b="uptime"><h5><i>${SI.pulse}</i>Heard over SSH<small id="lab-${where}-pct-${s.id}" class="mut"></small></h5><div class="lab-ups" id="lab-${where}-${s.id}"><span class="mut">reading…</span></div></div>`;
function labBanner(s) {      // one box that says how the server is, in plain words, with the buttons that fix it (the diagnosis of the Server Doctor)
  const d = srvDiagnose(s, state.tunnelsCache || []), f = s.facts || {};
  if (!s.enabled) return '<div class="lab-ban off"><i class="lb-i">⏾</i><div class="lb-t"><b>Disabled</b><div>The panel does not look at this server and sends no alerts for it. Switch it on at the top of this window.</div></div></div>';
  if (s.online == null) return `<div class="lab-ban off"><i class="lb-i">…</i><div class="lb-t"><b>Not checked yet</b><div>The panel has not looked at this server yet.</div></div><div class="lb-a"><button class="sm pri" onclick="checkServer(${s.id})">Check now</button></div></div>`;
  if (!d.finds.length) return `<div class="lab-ban ok"><i class="lb-i">✓</i><div class="lb-t"><b>Healthy</b><div>Answers over SSH${s.last_seen_at ? ", last seen " + ago(s.last_seen_at) + " ago" : ""}${f.uptime ? ", up " + Math.round(f.uptime / 86400) + " d" : ""}. Nothing needs attention.</div></div></div>`;
  const steps = d.steps.map(t => /Open the server/.test(t.label) ? null : /GRE/.test(t.label) && /docOpen/.test(t.click) ? {...t, click: `greCheck(${s.id})`} : /details/.test(t.label) && /docOpen/.test(t.click) ? {...t, click: `srvFilter(${s.id})`} : t).filter(Boolean);      // (a step that sent the Servers page to this server now does the test itself)
  return `<div class="lab-ban ${d.bad ? "bad" : "warn"}"><i class="lb-i">${d.bad ? "✕" : "!"}</i><div class="lb-t"><b>${d.bad ? "Needs attention" : "Worth a look"}</b>${d.finds.map(i => `<div class="${i.lvl === "bad" ? "err" : ""}">${esc(i.text)}</div>`).join("")}</div><div class="lb-a">${steps.map(t => `<button class="sm ${t.pri ? "pri" : ""}" onclick="${t.click}">${t.label}</button>`).join("")}</div></div>`;
}
/* ---------------------------------------------------------------- the menu: ONE "⋯" button in place of many buttons (the owner sent the picture of one: Overview, Rename, Power off ... Delete in red).
   The items are built when it opens and kept in LAB.menu; a click runs the item's function, so no name from a panel ever sits in a handler. */
let labMenuEl = null, labMenuBtn = null;
function labMenuClose(focus) {
  if (labMenuEl) { labMenuEl.remove(); labMenuEl = null; }
  document.removeEventListener("mousedown", labMenuOut, true); document.removeEventListener("keydown", labMenuKey, true); document.removeEventListener("scroll", labMenuScroll, true); window.removeEventListener("resize", labMenuClose);
  if (labMenuBtn) { labMenuBtn.removeAttribute("aria-expanded"); if (focus && document.body.contains(labMenuBtn)) labMenuBtn.focus(); }
  labMenuBtn = null;
}
const labMenuOut = e => { if (labMenuEl && !labMenuEl.contains(e.target) && !(labMenuBtn && labMenuBtn.contains(e.target))) labMenuClose(); };
const labMenuScroll = e => { if (labMenuEl && !labMenuEl.contains(e.target)) labMenuClose(); };
const labMenuKey = e => {
  if (!labMenuEl) return;
  if (e.key === "Escape") { e.stopPropagation(); labMenuClose(true); return; }
  if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); const bs = [...labMenuEl.querySelectorAll(".lab-mi:not(:disabled)")]; if (!bs.length) return; const i = bs.indexOf(document.activeElement); bs[(i + (e.key === "ArrowDown" ? 1 : -1) + bs.length) % bs.length].focus(); }
};
window.labMenu = (btn, ctx, id, ev) => {
  if (ev) { ev.stopPropagation(); ev.preventDefault(); }
  const same = labMenuEl && labMenuBtn === btn; labMenuClose(); if (same) return;      // the same button again closes it
  const items = labMenuItems(ctx, id); if (!items.length) return;
  LAB.menu = items; labMenuBtn = btn; btn.setAttribute("aria-expanded", "true");
  const m = document.createElement("div"); m.setAttribute("role", "menu");
  /* the items are cut into blocks at every separator and group name; a menu of more than ten items is laid in TWO columns (each block goes to the shorter column), so it stays short */
  const blocks = [{n: 0, html: ""}]; let n = 0;
  items.forEach((it, i) => {
    if (it.sep) { blocks.push({n: 0, html: ""}); return; }
    let b = blocks[blocks.length - 1]; if (it.grp && b.n) { b = {n: 0, html: ""}; blocks.push(b); }
    b.n++; if (!it.grp) n++;
    b.html += it.grp ? `<small class="lab-mgrp">${esc(it.grp)}</small>` : `<button type="button" role="menuitem" class="lab-mi${it.danger ? " danger" : ""}" ${it.off ? "disabled" : ""} ${it.tip ? `title="${esc(it.tip)}"` : ""} onclick="labMenuPick(${i})">${esc(it.t)}</button>`;
  });
  const two = n > 10, cols = [{n: 0, html: ""}, {n: 0, html: ""}];
  for (const b of blocks.filter(b => b.n)) { const c = two && cols[1].n < cols[0].n ? cols[1] : cols[0]; c.n += b.n; c.html += `<div class="lab-mg">${b.html}</div>`; }
  m.className = "lab-menu" + (two ? " two" : ""); m.innerHTML = cols.filter(c => c.n).map(c => `<div class="lab-mcol">${c.html}</div>`).join("");
  document.body.appendChild(m); labMenuEl = m;
  const r = btn.getBoundingClientRect(), w = m.offsetWidth, h = m.offsetHeight, left = Math.min(Math.max(8, r.right - w), innerWidth - w - 8);
  let top = r.bottom + 6; if (top + h > innerHeight - 8) top = Math.max(8, r.top - h - 6);
  m.style.left = left + "px"; m.style.top = top + "px"; m.style.maxHeight = (innerHeight - 16) + "px";
  document.addEventListener("mousedown", labMenuOut, true); document.addEventListener("keydown", labMenuKey, true); document.addEventListener("scroll", labMenuScroll, true); window.addEventListener("resize", labMenuClose);
  if (ev && ev.detail === 0) { const first = m.querySelector(".lab-mi:not(:disabled)"); if (first) first.focus(); }      // opened from the keyboard: the first item is ready
};
window.labMenuPick = i => { const it = (LAB.menu || [])[i]; labMenuClose(true); if (it && it.fn) it.fn(); };
const labItem = (t, fn, o = {}) => ({t, fn, ...o}), labSep = {sep: 1}, labGrp = g => ({grp: g});
function labMenuItems(ctx, id) {      // ctx "row" / "win": id = a profile; "node": id = a server whose Node section is open
  if (ctx === "node") { const s = (state.serversCache || []).find(x => x.id === id), cur = s && typeof pgNodeCurrent === "function" ? pgNodeCurrent(pgSrvOn(s)) : null; return cur && cur.type === "pasarguard" && cur.node_id != null ? labNodeItems(cur, null) : []; }
  if (ctx === "swin") { const s = (state.serversCache || []).find(x => x.id === id); return s ? labServerItems(null, s, "win") : []; }      // (a server that is on no profile: its window has the same menu, with "Put on the page" in place of "Off the page")
  const p = LAB.profiles.find(x => x.id === id); if (!p) return [];
  if (p.kind === "node") { const r = labNodeRow(p); return labNodeItems(r || {panel_id: p.panel_id, node_id: p.node_id, name: p.name}, p, ctx); }
  const s = labServer(p); return s ? labServerItems(p, s, ctx) : [labItem("Take it off the page", () => labRemove(p.id))];
}
function labServerItems(p, s, ctx) {
  const off = s.online === false && s.enabled !== false, nm = s.name, f = s.facts || {}, ab = (s.extra || {}).abuse || {}, abuseOn = ab.installed || (f.abuse_rules || 0) > 0, tip = off ? "The server is off or does not answer" : "";
  if (ctx === "row") return [labItem("Overview", () => labOpen(p.id)), labItem("Check now", () => labCheckOne(p.id)), labItem("Console", () => srvConsole(s.id), {off, tip}), labItem("Edit…", () => editServer(s.id)), labItem("Reboot…", () => srvReboot(s.id, nm), {off, tip}),
    labSep, labItem("Off the page", () => labRemove(p.id)), labItem("Delete server…", () => deleteServer(s.id, nm), {danger: true})];
  return [labItem("Check now", () => checkServer(s.id)), labItem("Console", () => srvConsole(s.id), {off, tip}), labItem("Edit…", () => editServer(s.id)),
    labSep, labGrp("Access"), labItem("Connect SSH…", () => srvConnect(s.id, nm)), labItem("Change SSH password…", () => srvPassword(s.id, nm)), labItem("Forget host key…", () => forgetKey(s.id, nm)), labItem("Open in an SSH program…", async () => { const v = await glassChoice(`Open ${nm} in:`, [{label: "Windows ssh", v: "ssh", note: "a .bat with your own key"}, {label: "PuTTY", v: "putty"}, {label: "MobaXterm", v: "mobaxterm"}]); if (v) srvOpen(s.id, v); }),
    labSep, labGrp("Tests"), labItem("GRE test", () => greCheck(s.id), {off, tip}), ...(s.role === "foreign" ? [labItem("Is it filtered in Iran?", () => srvFilter(s.id), {off, tip}), labItem(abuseOn ? "Abuse-Defender: remove…" : "Abuse-Defender: install…", () => srvAbuse(s.id, abuseOn ? "remove" : "install"), {off, tip})] : []),
    labSep, labGrp("Install"), labItem("Nodes and tools…", () => wtab("install"), {off, tip}), labItem("Update the server…", () => srvInstall(s.id, "os_update"), {off, tip}),
    labSep, labItem("Reboot…", () => srvReboot(s.id, nm), {off, tip}), p ? labItem("Off the page", () => labRemove(p.id)) : labItem("Put on the page", () => labAdopt(s.id)), labItem("Delete server…", () => deleteServer(s.id, nm), {danger: true})];
}
function labNodeItems(cur, p, ctx) {      // everything the Node view could do with its eight buttons, as items; `p` = the profile (a node profile), null in the Node section of a server
  const pid = cur.panel_id, id = cur.node_id, c = typeof pgNodesC !== "undefined" ? pgNodesC[pid] : null, d = c && c.data ? (c.data.nodes || []).find(n => n.id === id) : null, off = d && d.status === "disabled";
  const act = v => async () => { try { await pgNodesLoad(pid); } catch (e) { toast("✕ " + e.message, "bad"); return; } pgNodesGo(pid, id, v); };      // (the panel is read first when it was not: the node must be known to it)
  const head = ctx === "row" && p ? [labItem("Overview", () => labOpen(p.id)), labItem("Check now", () => labCheckOne(p.id)), labSep] : [];
  const look = ctx === "row" ? [] : [labGrp("Look"), labItem("Log", act("logs")), labItem("Usage", act("usage")), labItem("Resources", act("stats")), labItem("Latency", act("latency")), labItem("Certificate…", act("cert")), labSep];
  const change = [...(ctx === "row" ? [] : [labGrp("Change")]), labItem("Edit…", act("edit")), labItem("Restart…", act("reconnect")), labItem(off ? "Enable…" : "Disable…", act(off ? "enable" : "disable")),
    ...(ctx === "row" ? [] : [labItem("Resync users…", act("sync")), labItem("Reset counters…", act("reset")), labItem("Update the node…", act("update")), labItem("Update Xray…", act("core")), labItem("Update geo files…", act("geo"))])];
  return [...head, ...look, ...change, labSep, ...(p ? [labItem("Off the page", () => labRemove(p.id))] : []), labItem("Delete the node…", act("delete"), {danger: true})];
}

/* a section is drawn: what it loads by itself */
function labAfter(s) {
  const sec = LAB_SECS.some(x => x[0] === state.wsec) ? state.wsec : "overview";
  if (state.wtab === "console" || state.wtab === "install") return;
  if (sec === "monitor") { loadServerCharts(s.id); labStrip(s.id, "up"); }
  if (sec === "overview" && s.enabled) labStrip(s.id, "ov");
  if (sec === "node") labInsight(s);
}
window.labWtab = k => {      // the tabs of the window: a section, a sub-view (console, install) or "all" = back to the section
  const s = (state.serversCache || []).find(x => x.id === state.open), body = $("#modal .modal-body"); if (!s || !body) { refresh(); return; }
  if (k === "console" || k === "install") state.wtab = k; else { state.wtab = null; if (LAB_SECS.some(x => x[0] === k)) state.wsec = k; }
  const mdl = body.closest(".modal"); if (mdl) { const w = state.winH && state.winH.id === s.id ? state.winH : (state.winH = {id: s.id, h: 0}); w.h = Math.max(w.h, mdl.offsetHeight); mdl.style.minHeight = w.h + "px"; }
  body.innerHTML = `<div id="srv-detail-${s.id}">${serverDetailHtml(s)}</div>`;
  if (state.wtab === "console") { const c = $(`#con-cmd-${s.id}`); if (c) c.focus(); } else labAfter(s);
};

/* ---------------------------------------------------------------- the window of a node profile: the Node view of pg_nodes.js (all that the panel can do to a node) and the curves of the node */
function labOpenNode(p) {
  const r = labNodeRow(p); if (!r) { toast("The panel does not list this node any more: take it off the page", "bad"); return; }
  const I = labNodeInfo(p);
  if (typeof pgNodeV !== "undefined") { pgNodeV.key = `${r.panel_id}:${r.node_id}`; pgNodeV.err = null; }
  state.open = null; state.edit = null; state.nodeWin = {out: true, lab: 1};
  const head = `<span class="st ${I.tone === "ok" ? "up" : I.tone === "bad" ? "down" : ""}"></span><b>${esc(p.name)}</b> <span class="mut">${esc(r.address || p.address)}</span> <span class="pill">${esc(p.panel)}</span>`;
  showModal(head, `<div class="lab-body"><div id="node-pane" class="np" data-out="${r.panel_id}:${r.node_id}" data-from="0">${pgNodeMain({out: true, rows: [r], from: false})}</div><div class="lab-ins" id="lab-ins-n${p.id}"></div></div>`, "wide nodewin", `${labKebab("win", p.id, "Actions on " + p.name)}<i class="mh-sep"></i>`);
  pgNodeViewLoad(); labInsightFor(`lab-ins-n${p.id}`, r);
}

/* ---------------------------------------------------------------- Monitor: the strip of the last hours */
const LAB_RANGE = {1: [1, 30], 6: [6, 36], 24: [24, 48], 168: [168, 84]};      // hours -> [hours, cells]
async function labStrip(id, where = "up") {      // "up": the Monitor section (the range of its charts), "ov": the Overview (always the last 24 hours)
  const box = $(`#lab-${where}-${id}`); if (!box) return;
  const [h, n] = where === "ov" ? LAB_RANGE[24] : (LAB_RANGE[state.mrange] || LAB_RANGE[24]);
  try {
    const r = await api(`/api/servers/${id}/uptime?hours=${h}&buckets=${n}`), end = Date.now() / 1000, w = h * 3600 / n, s0 = end - n * w;
    const cell = (c, i) => `<i class="c${c === "-" ? "n" : c}" title="${esc(fmtT(s0 + i * w, h * 3600) + " - " + fmtT(s0 + (i + 1) * w, h * 3600) + ": " + ({2: "answered", 1: "answered now and then", 0: "no answer", "-": "not added yet"}[c]))}"></i>`;
    box.innerHTML = `<div class="lab-ups-row">${[...r.strip].map(cell).join("")}</div><div class="lab-ups-x"><span>${esc(fmtT(s0, h * 3600))}</span><span>now</span></div>`;
    const pc = $(`#lab-${where}-pct-${id}`); if (pc) pc.textContent = r.up == null ? "" : `${r.up} % of the last ${h === 168 ? "7 days" : h + " h"} · the panel looks every 2 minutes`;
  } catch (e) { box.innerHTML = `<span class="mut">${esc(e.message)}</span>`; }
}
/* (the range tabs of the charts - 1 h, 6 h, 24 h, 7 days - are drawn by app.js; its mrange asks labStrip for the strip of the same range) */

/* ---------------------------------------------------------------- Node: what the panel says about the node, beyond the Node view (traffic of the week, users online) */
const labBars = (vals, labelOf, fmt) => { const mx = Math.max(1, ...vals); return `<div class="lab-bars">${vals.map((v, i) => `<i style="height:${Math.max(3, Math.round(v / mx * 100))}%" title="${esc(labelOf(i) + ": " + fmt(v))}"></i>`).join("")}</div>`; };
function labInsight(s) {      // the window of a server: the node that is on screen in its Node view
  const cur = typeof pgNodeCurrent === "function" ? pgNodeCurrent(pgSrvOn(s)) : null;
  return labInsightFor("lab-ins-" + s.id, cur);
}
async function labInsightFor(boxId, cur) {
  const box = document.getElementById(boxId); if (!box) return;
  if (!cur || cur.type !== "pasarguard" || cur.node_id == null) { box.innerHTML = ""; return; }
  const base = `/api/panels/${cur.panel_id}/pg/nodes`, key = `${cur.panel_id}:${cur.node_id}`;
  box.innerHTML = `<div class="lab-ins-grid"><div class="sbox"><h5><i>${SI.bars}</i>Traffic · 7 days</h5><span class="mut">reading…</span></div><div class="sbox"><h5><i>${SI.users}</i>Users online · 24 hours</h5><span class="mut">reading…</span></div></div>`;
  const [use, cnt] = await Promise.all([api(`${base}/${cur.node_id}/usage?period=day&days=7`).catch(e => ({err: e.message})), api(`${base}/user-counts?metric=online&period=hour&hours=24&node_id=${cur.node_id}`).catch(e => ({err: e.message}))]);
  if (document.getElementById(boxId) !== box || `${cur.panel_id}:${cur.node_id}` !== key) return;
  const day = t => new Date(t).toLocaleDateString([], {day: "2-digit", month: "short"}), hour = t => new Date(t).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", hour12: false});
  const card = (ic, title, sub, body) => `<div class="sbox"><h5><i>${ic}</i>${title}<small class="mut">${sub}</small></h5>${body}</div>`;
  const u = use.err ? card(SI.bars, "Traffic · 7 days", "", `<span class="mut">${esc(use.err)}</span>`) : card(SI.bars, "Traffic · 7 days", `↓ ${fmtB(use.total_down)} · ↑ ${fmtB(use.total_up)}`, use.rows.length ? labBars(use.rows.map(r => r.up + r.down), i => day(use.rows[i].t), fmtB) + `<div class="lab-bars-x"><span>${day(use.rows[0].t)}</span><span>${day(use.rows[use.rows.length - 1].t)}</span></div>` : '<span class="mut">the panel has no usage rows for this node yet</span>');
  const c = cnt.err ? card(SI.users, "Users online · 24 hours", "", `<span class="mut">${esc(cnt.err)}</span>`) : card(SI.users, "Users online · 24 hours", cnt.rows.length ? `now ${cnt.rows[cnt.rows.length - 1].n} · peak ${Math.max(...cnt.rows.map(r => r.n))}` : "", cnt.rows.length ? labBars(cnt.rows.map(r => r.n), i => hour(cnt.rows[i].t), v => v + " users") + `<div class="lab-bars-x"><span>${hour(cnt.rows[0].t)}</span><span>now</span></div>` : '<span class="mut">the panel has no counts for this node yet</span>');
  box.innerHTML = `<div class="lab-ins-grid">${u}${c}</div>`;
}

/* ---------------------------------------------------------------- the menu: the section is called Lab (the owner). Its list: Any (every profile), then an item for each KIND that has a profile - Foreign, Iran, PasarGuard, only while there are two kinds
   or more, the same rule as the tabs of the page (nothing is made in advance) - and Add profile + at the END (the owner: "Add profile comes at the end of the list, no arrow, a + after it"). Add profile is not a tab but a door to an action ("act" in the tabs
   of app.js, drawn by navBuild and by the bar of the page): it opens the choice of what to add. The kinds follow the profiles: labNavSync draws them whenever the profiles were read. */
SUB_ACT["nodes/add"] = () => { if (labOn()) labAdd(); else { LAB.pending = "add"; go("nodes", "lab"); } };
const LAB_NAV = [["foreign", "Foreign"], ["iran", "Iran"], ["node", "PasarGuard"]];      // (the order of the menu is the one the owner typed)
window.labGoKind = k => { if (labOn()) labKind(k); else { LAB.kind = k; localStorage.setItem("nk.lab.k", k); go("nodes", "lab"); } };      // (an item of the menu: the Lab, on that kind)
function labNavSync() {
  const sub = document.getElementById("nav-sub-nodes"), any = sub ? sub.querySelector('a[data-sub="lab"]') : null; if (!any) return;
  sub.querySelectorAll("a.nav-kind").forEach(a => a.remove());
  const kinds = labKinds(), ic = typeof navIcons === "function" ? navIcons() : {};
  if (kinds.length > 1) for (const [k, name] of LAB_NAV.slice().reverse()) if (kinds.includes(k)) {      // (backwards: each goes right after Any, so they end up in the order of LAB_NAV)
    const a = document.createElement("a"); a.className = "nav-kind"; a.dataset.page = "nodes"; a.dataset.sub = "lab:" + k; a.dataset.kind = k;
    a.innerHTML = `<i class="ic">${ic[k] || ""}</i><span>${esc(name)}</span>`; a.onclick = () => labGoKind(k); any.after(a);
  }
  any.onclick = () => labGoKind("any");
  const cur = labOn() ? labKindNow() : null;      // (the open one is the kind the page shows)
  any.classList.toggle("on", cur === "any"); sub.querySelectorAll("a.nav-kind").forEach(a => a.classList.toggle("on", a.dataset.kind === cur));
  const g = sub.closest(".nav-g"); if (g) g.classList.toggle("has-on", !!sub.querySelector("a.on"));
  if (typeof navTitles === "function") navTitles();      // (the tooltips of the new items, where the menu is a rail of icons)
}
