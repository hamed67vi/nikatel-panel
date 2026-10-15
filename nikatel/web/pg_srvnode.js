/* Servers <-> PasarGuard nodes (the owner, 2026-10-14). A server of ours and a node of a panel are one machine, so the two pages are one (1.48.0: the Nodes tab is gone):
   - the Servers list shows the nodes that run on each server (every panel, Marzban too, read from what the health job saw: no call to a panel) as the name of the panel in the row; a click opens the
     window of the server on its Node view (pg_nodes.js: what the panel knows about the node and everything that can be done to it)
   - once a server carries a PasarGuard node the list has a tick in front of each server and a Nodes... menu: what is done to the nodes of the ticked servers, what concerns a whole panel, the nodes
     that run on no server of ours (a window of their own, with the same Node view)
   - "Add server" has a tick "PasarGuard node": the server is added, then made a node of the chosen panel (install over SSH, read the certificate, add it to the panel, wait); the API key is made by this panel
   - a foreign server that is no node yet can be made one from its window; removing a server asks whether its node goes from the PasarGuard panel too
   Routes used: GET /api/servers/panel-nodes, GET /api/panels/{id}/pg/cores, POST /api/panels/{id}/pg/nodes/make[/plan], POST /api/panels/{id}/pg/nodes/bulk, DELETE /api/panels/{id}/pg/nodes/{node}?confirm=true.
   Uses the helpers of app.js: $, api, esc, toast, showModal, closeModal, gconfirm, followStory, go, refresh, docOpen, wtab, state, pn and pg_nodes.js. */
const pgSrv = {nodes: {}, outside: [], at: 0, cores: {}};
state.srvSel = new Set();      // the servers that are ticked in the list
window.pgSrvPanels = () => ((state.panelsCache || {}).panels || []).filter(p => p.type === "pasarguard");         // the PasarGuard panels this panel manages (the Servers page keeps the list)
window.pgSrvOn = s => ((pgSrv.nodes || {})[s.id] || []);                                                          // the nodes (of every panel) that run on one server
const pgSrvCls = x => x.status === "connected" ? "ok" : x.status === "disabled" || x.status === "connecting" ? "" : "bad";
const pgSrvIsPg = x => x.type === "pasarguard" && x.node_id != null;      // a node that something can be done to
const pgSrvKey = x => `${x.panel_id}:${x.node_id}`;

/* ---- what runs on each server: read once per look at the page (the answer is small and comes from this panel's own database) */
window.pgSrvLoad = async () => {
  try { const r = await api("/api/servers/panel-nodes"); pgSrv.nodes = r.servers || {}; pgSrv.outside = r.outside || []; pgSrv.at = Date.now(); } catch (e) { /* the list stays as it was: the page works without the chips */ }
  return pgSrv.nodes;
};
const pgSrvTip = x => `${x.type === "pasarguard" ? "PasarGuard" : "Marzban"} node ${x.name} of the panel ${x.panel}: ${x.status || "?"}${x.message ? " (" + x.message + ")" : ""}${x.match === "dns" ? " - matched through the DNS" : ""}`;
window.pgSrvChips = s => pgSrvOn(s).map(x => `<span class="mchip ${pgSrvCls(x)}" onclick="event.stopPropagation();pgSrvOpenNode(${s.id}, '${pgSrvKey(x)}')" title="${esc(pgSrvTip(x))} - click to open it">${esc(x.panel)}</span>`).join("");
window.pgSrvOpenNode = (sid, key) => {      // the window of the server, on its Node section (the node `key` when the server carries more than one)
  if (key) { pgNodeV.key = key; pgNodeV.err = null; }
  if (window.labOn && labOn()) { if (state.open === sid && $("#modal .srvwin")) wtab("node"); else labOpenServer(sid, "node"); return; }      // the Lab page opens the window itself (lab.js)
  if (state.open === sid && state.page === "nodes" && $("#modal .srvwin")) { wtab("node"); return; }
  if (!(state.serversCache || []).some(x => x.id === sid)) return;
  docOpen(sid, "node");      // (docOpen goes to the Lab page and opens the window there, on its Node section)
};
window.pgSrvOpenPanel = (pid, pg) => {      // the panel itself: the Panel page, on General (a PasarGuard panel: pg is 1) or on Users (a Marzban panel has no General)
  pn.id = String(pid); pn.users = pn.hosts = pn.core = pn.user = pn.logs = null;
  go("pgpanel", pg ? "settings" : "users");
};
/* the list and the window show a change at once: a window that is open is updated where it stands (its chips and its Node view), the list behind it when the window closes */
window.pgSrvRedraw = () => {
  if (state.page !== "nodes") return;
  if (state.open == null) { if (!state.nodeWin) refresh(); return; }
  const s = (state.serversCache || []).find(x => x.id === state.open), m = $("#modal .mh-meta");
  if (s && m) { m.querySelectorAll(".mchip").forEach(e => e.remove()); m.insertAdjacentHTML("beforeend", pgSrvChips(s)); }
  state.pageStale = true;      // (the list behind is drawn again when the window closes)
  if (typeof pgNodeViewDraw === "function") pgNodeViewDraw();
};

/* ---- the server window (1.45.0): under its buttons, a line for every node that runs on the server (the panel, its state, one click to the Node view) - or the offer to make a foreign server a node */
window.pgSrvLine = s => {
  const on = pgSrvOn(s), ps = pgSrvPanels();
  const rows = on.map(x => `<div class="pg-nl"><i class="dotc ${pgSrvCls(x)}"></i><b>${x.type === "pasarguard" ? "PasarGuard" : "Marzban"} node</b><span class="mchip ${pgSrvCls(x)}" title="${esc(pgSrvTip(x))}">${esc(x.panel)}</span><small class="mut">${esc(x.name)} · ${esc(x.status || "?")}${x.message ? " · " + esc(x.message) : ""}</small><span class="sp"></span><button class="sm" onclick="pgSrvOpenNode(${s.id}, '${pgSrvKey(x)}')" title="The Node view of this window: log, usage, certificate, edit, restart, update, delete">Manage</button></div>`);
  if (!on.some(x => x.type === "pasarguard") && s.role === "foreign" && ps.length) rows.push(`<div class="pg-nl off"><i class="dotc"></i><b>PasarGuard node</b><small class="mut">this server is not a node of any panel</small><span class="sp"></span><button class="sm" onclick="wtab('node')" title="Install the node here and add it to a PasarGuard panel">Make a node…</button></div>`);
  return rows.length ? `<div class="pg-nline">${rows.join("")}</div>` : "";
};

/* ---- ticks and the Nodes... menu of the list */
window.pgSrvTicks = () => Object.values(pgSrv.nodes || {}).some(rows => rows.some(pgSrvIsPg));      // the list has ticks once a server carries a PasarGuard node
window.pgSrvCk = s => { const has = pgSrvOn(s).some(pgSrvIsPg); return `<input type="checkbox" class="ck srv-ck" ${state.srvSel.has(s.id) ? "checked" : ""} ${has ? "" : "disabled"} onclick="event.stopPropagation()" onchange="pgSrvSel(${s.id}, this.checked)" aria-label="Tick ${esc(s.name)}" title="${has ? "Tick it: the Nodes… menu acts on its node" : "No PasarGuard node runs on this server"}">`; };
window.pgSrvPrune = list => { for (const id of [...state.srvSel]) if (!list.some(s => s.id === id)) state.srvSel.delete(id); };
const pgSrvTicked = () => [...state.srvSel].flatMap(id => pgSrvOn({id}).filter(pgSrvIsPg).map(x => ({...x, sid: id})));      // the nodes of the ticked servers
window.pgSrvSel = (id, on) => { if (on) state.srvSel.add(id); else state.srvSel.delete(id); const w = $("#srv-nodes-wrap"); if (w) w.outerHTML = pgSrvMenu(); };
window.pgSrvMenu = () => {
  const ps = pgSrvPanels(); if (!ps.length) return '<span id="srv-nodes-wrap"></span>';
  const t = pgSrvTicked(), n = state.srvSel.size, out = (pgSrv.outside || []).length, opt = (v, l, off) => `<option value="${v}" ${off ? "disabled" : ""}>${l}</option>`;
  const bulk = [["reconnect", "Restart"], ["enable", "Enable"], ["disable", "Disable"], ["reset", "Reset counters"], ["update", "Update software…"], ["delete", "Delete the nodes…"]];
  const ticked = n ? `<optgroup label="Ticked servers (${n}${t.length ? ` · ${t.length} node${t.length === 1 ? "" : "s"}` : " · no node"})">${bulk.map(([v, l]) => opt("bulk:" + v, l, !t.length)).join("")}${opt("untick", "Untick all")}</optgroup>` : "";
  const panels = ps.map(p => `<optgroup label="Panel ${esc(p.name)}">${opt(`panel:${p.id}:add`, "Add a node by hand…")}${opt(`panel:${p.id}:counts`, "Users online over time…")}${opt(`panel:${p.id}:backups`, "Backups of the nodes…")}${opt(`panel:${p.id}:reconnect`, "Restart every node…")}${opt(`panel:${p.id}:clear`, "Clear usage data…")}</optgroup>`).join("");
  const tip = `The PasarGuard nodes: what is done to the nodes of the ticked servers, what concerns a whole panel, the nodes outside our servers${n ? ` - ${n} server${n === 1 ? "" : "s"} ticked` : ""}${out ? ` - ${out} node${out === 1 ? "" : "s"} of your panels run${out === 1 ? "s" : ""} on a machine that is none of the servers in this list` : ""}`;
  return `<span id="srv-nodes-wrap" class="srv-nodes"><select class="sm srv-nodes-sel ${out ? "warn" : ""}" onchange="pgSrvMenuGo(this)" title="${esc(tip)}"><option value="">${n ? `Nodes (${n})…` : "Nodes…"}</option>${ticked}${panels}<optgroup label="Servers">${opt("tick", "Tick every server with a node")}${opt("outside", `Nodes outside our servers (${out})…`, !out)}</optgroup></select></span>`;
};
window.pgSrvMenuGo = sel => {
  const v = sel.value; sel.selectedIndex = 0; if (!v) return;
  if (v === "tick") { Object.keys(pgSrv.nodes).forEach(id => { if (pgSrvOn({id: +id}).some(pgSrvIsPg)) state.srvSel.add(+id); }); document.querySelectorAll(".srv-ck:not(:disabled)").forEach(c => { c.checked = true; }); const w = $("#srv-nodes-wrap"); if (w) w.outerHTML = pgSrvMenu(); return; }
  if (v === "untick") { state.srvSel.clear(); document.querySelectorAll(".srv-ck").forEach(c => { c.checked = false; }); const w = $("#srv-nodes-wrap"); if (w) w.outerHTML = pgSrvMenu(); return; }
  if (v === "outside") return pgSrvOutside();
  if (v.startsWith("bulk:")) return pgSrvBulk(v.slice(5));
  const [, pid, what] = v.split(":"); if (pid) pgNodesPanelDo(+pid, what);
};

/* ---- several nodes at once (the nodes of the ticked servers; the panel's bulk routes - everything that cuts users or cannot be undone asks first, and the server needs the confirm too) */
const pgSrvBulkText = {
  reconnect: n => `Restart Xray on ${n}? The panel stops and starts Xray on each: whoever is connected through them drops for a few seconds and reconnects by itself. Nothing is deleted.`,
  enable: n => `Enable ${n}? The panel connects to them again.`,
  disable: n => `Disable ${n}? The panel disconnects from them: everybody connected through them is cut until they are enabled again. Users and links stay.`,
  reset: n => `Set the traffic counters of ${n} to zero? The panel keeps a log row with the old numbers; a node stopped by its data limit connects again.`,
  update: n => `Ask ${n} to update their own software? Each updates itself and restarts: its users drop for a minute or so. A big batch drops many at once - do a few at a time.`,
  delete: n => `Delete ${n} from the panel? The panel stops using them and drops their traffic history; whoever is connected through them is cut. The users are not deleted. Every node is saved to a file first: Backups can make them again (new ids, no history). The servers themselves are not touched.`,
};
window.pgSrvBulk = async action => {
  const nodes = pgSrvTicked(); if (!nodes.length) { toast("✕ Tick a server that carries a PasarGuard node first", "bad"); return; }
  const what = nodes.length === 1 ? `node ${nodes[0].name}` : `${nodes.length} nodes (${nodes.slice(0, 4).map(n => n.name).join(", ")}${nodes.length > 4 ? ", …" : ""})`;
  if (action !== "enable" && !await gconfirm(pgSrvBulkText[action](what), {danger: ["disable", "delete", "update"].includes(action)})) return;
  const by = {}; nodes.forEach(n => (by[n.panel_id] = by[n.panel_id] || []).push(n));
  let done = 0; const failed = [];
  for (const [pid, list] of Object.entries(by)) {
    try { await pgApi(+pid, "/nodes/bulk", {method: "POST", body: {action, ids: list.map(n => n.node_id), confirm: true}}); done += list.length; pgNodesLoad(+pid, true).catch(() => {}); } catch (e) { failed.push(`${list[0].panel}: ${e.message}`); }
  }
  toast(failed.length ? `✕ ${failed.join(" · ")}${done ? ` (${done} done)` : ""}` : `✓ ${action} done for ${nodes.length === 1 ? nodes[0].name : nodes.length + " nodes"}${action === "delete" ? " (saved in Backups)" : ""}`, failed.length ? "bad" : "ok");
  if (!failed.length) state.srvSel.clear();
  setTimeout(async () => { await pgSrvLoad(); refresh(); }, 1200);      // (the reads above refreshed the copy of the panels; the list shows it)
};

/* ---- nodes outside our servers: a window that lists them (and the same Node view for each) */
const pgSrvRow = (x, from, i) => {
  const man = x.type !== "pasarguard", cls = pgSrvCls(x);
  return `<tr><td class="name"><i class="dotc ${cls}"></i> <b>${esc(x.name || "—")}</b>${x.hint ? `<br><small class="mut" title="A server of ours has this name but another address">${esc(x.hint.name)} has another address</small>` : ""}</td>
    <td><span class="mchip ${cls}" title="${esc(pgSrvTip(x))}">${esc(x.panel)}</span> <small class="mut">${man ? "Marzban" : "PasarGuard"}</small></td><td class="mono mut">${esc(x.address)}</td>
    <td><span class="st ${cls === "ok" ? "up" : cls === "bad" ? "down" : ""}" title="${esc(x.message || x.status)}">${esc(x.status || "?")}</span></td>
    <td class="pg-nodes-act">${pgSrvIsPg(x) ? `<button class="sm" onclick="pgNodeOut(${x.panel_id}, ${x.node_id}, ${from ? 1 : 0})" title="The Node view: log, usage, certificate, edit, restart, update, delete">Manage</button>` : `<button class="sm" onclick="pgSrvOpenPanel(${x.panel_id}, ${man ? 0 : 1})" title="${man ? "A Marzban node is changed in its panel" : "The panel gave this node no id"}">Open the panel</button>`}<button class="sm" onclick="pgSrvAddFromNode(${i})" title="Add the machine of this node to the servers (the form opens with its name and address)">Add to servers</button></td></tr>`;
};
window.pgSrvOutside = () => {
  const rows = pgSrv.outside || [], ps = pgSrvPanels();
  state.nodeWin = {list: true};
  showModal(`<b>Nodes outside our servers</b> <span class="pill">${rows.length}</span>`, `<div class="frm-act pg-nodes-tools"><small class="mut">Nodes of your panels whose address is none of the servers in the list (a server of another person, or one that was never added). Their state is what the health job saw last.</small><span class="sp"></span>${ps.length ? `<button class="sm pri" onclick="pgSrvAddNode()" title="Add a node to a PasarGuard panel by hand: address, key and certificate">＋ Add a node by hand</button>` : ""}</div>
    <div class="lbox full pg-nodes-ubox"><table class="tbl lift pg-out-tbl"><colgroup><col style="width:24%"><col style="width:20%"><col style="width:22%"><col style="width:12%"><col></colgroup><thead><tr><th>Node</th><th>Panel</th><th>Address</th><th>State</th><th></th></tr></thead>
      <tbody>${rows.map((x, i) => pgSrvRow(x, true, i)).join("") || '<tr><td colspan="5" class="mut">every node of your panels runs on one of the servers in the list</td></tr>'}</tbody></table></div>`, "wide");
};
window.pgSrvAddNode = () => {      // by hand: the panel first when there is more than one PasarGuard panel
  const ps = pgSrvPanels(); if (!ps.length) return;
  if (ps.length === 1) { pgNodesPanelDo(ps[0].id, "add"); return; }
  const pid = +(pgSrv.pick || ps[0].id); pgNodesPanelDo(pid, "add");
};
window.pgSrvPanelNodes = async pid => {      // "Nodes 2 / 2" of the bar of a panel: its nodes, each beside the server it runs on
  await pgSrvLoad();
  const p = (state.panelsCache || {}).panels ? state.panelsCache.panels.find(x => x.id === +pid) : (state.panelsCache || []).find(x => x.id === +pid); if (!p) return;
  const on = [], srvs = state.serversCache || [];
  Object.entries(pgSrv.nodes || {}).forEach(([sid, rows]) => rows.filter(x => x.panel_id === +pid).forEach(x => on.push({...x, sid: +sid, srv: srvs.find(s => s.id === +sid)})));
  const out = (pgSrv.outside || []).filter(x => x.panel_id === +pid);
  const row = x => `<tr><td class="name"><i class="dotc ${pgSrvCls(x)}"></i> <b>${esc(x.name || "—")}</b></td><td><span class="st ${pgSrvCls(x) === "ok" ? "up" : pgSrvCls(x) === "bad" ? "down" : ""}" title="${esc(x.message || x.status)}">${esc(x.status || "?")}</span></td><td class="mono mut">${esc(x.address)}</td><td class="mut">${esc(x.xray || "")}</td>
    <td class="pg-nodes-act">${x.srv ? `<button class="sm" onclick="closeModal();pgSrvOpenNode(${x.sid}, '${pgSrvKey(x)}')" title="The window of the server ${esc(x.srv.name)}, on its Node view">${esc(x.srv.name)}</button>` : pgSrvIsPg(x) ? `<button class="sm" onclick="pgNodeOut(${x.panel_id}, ${x.node_id}, 0)" title="This node runs on no server of ours">Manage</button><span class="mut">outside</span>` : '<span class="mut">outside</span>'}</td></tr>`;
  const all = [...on, ...out];
  showModal(`<b>Nodes of ${esc(p.name)}</b> <span class="pill">${all.length}</span>`, `<div class="lbox full pg-nodes-ubox"><table class="tbl lift"><colgroup><col style="width:26%"><col style="width:16%"><col style="width:26%"><col style="width:12%"><col></colgroup><thead><tr><th>Node</th><th>State</th><th>Address</th><th>Xray</th><th>Server</th></tr></thead>
    <tbody>${all.map(row).join("") || '<tr><td colspan="5" class="mut">this panel has no node</td></tr>'}</tbody></table></div><small class="mut">Each node beside the server of ours it runs on: the button opens that server's window on its Node view (log, usage, certificate, edit, restart, update).</small>`, "wide");
};

/* ---- the tick of "Add server": a PasarGuard node (and the Marzban node next to it: one machine has one node on the ports 62050 / 62051, so the two ticks exclude each other) */
window.pgSrvTick = () => {      // "Install the PasarGuard node": in the PasarGuard part of Add a server; labTagPick (lab.js) turns it on when a PasarGuard profile is chosen
  const ps = pgSrvPanels();
  return `<label class="chk" title="${ps.length ? "Install the PasarGuard node (Docker) on the server and add it to a PasarGuard panel: ports 62050 / 62051" : "Add a PasarGuard panel first (the Panel page): the node is added to it"}"><input name="x_pgnode" type="checkbox" disabled onchange="srvNodeKind(this)"> Install the PasarGuard node</label>`;
};window.pgSrvBox = () => {      // the owner (2026-10-15): the tick does EVERYTHING by itself - Docker and the node, a random API key (never shown), the certificate, the panel; the fields are folded under Options
  const ps = pgSrvPanels(), pick = ps.length > 1, sel = `<label class="f3"><span>Panel</span><select name="x_pg_panel" disabled onchange="pgSrvCores(this.form)">${ps.map(p => `<option value="${p.id}" ${String(p.id) === String(pn.id) ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>`;
  return `<div class="x-pg off"><div class="x-pg-auto"><i>✓</i><span>Everything is automatic: the panel installs the node over SSH (Docker, a few minutes), makes a random API key (never shown), reads the certificate and adds the node to ${pick ? "the panel chosen below" : `<b>${esc(ps[0] ? ps[0].name : "your PasarGuard panel")}</b>`}.</span></div>${pick ? sel : ""}
    <details class="x-pg-more"><summary>Options</summary><div class="x-pg-grid">${pick ? "" : sel}
      <label class="f3"><span>Node name</span><input name="x_pg_name" disabled maxlength="40" autocomplete="off" placeholder="the name of the server" oninput="this.dataset.touched = 1"></label>
      <label class="f3"><span>Core config</span><select name="x_pg_core" disabled><option value="">…</option></select></label>
      <label class="f3"><span>Usage coefficient</span><input name="x_pg_coef" type="number" step="0.1" min="0" value="1" disabled></label></div></details></div>`;
};window.srvNodeKind = el => {      // the two ticks (Marzban node, PasarGuard node) exclude each other; each shows its own fields
  const box = el.closest(".srv-after"); if (!box) return;
  const mz = box.querySelector("[name=x_node]"), pg = box.querySelector("[name=x_pgnode]");
  if (el === mz && mz.checked && pg) pg.checked = false;
  if (el === pg && pg.checked && mz) mz.checked = false;
  const show = (sel, on) => box.querySelectorAll(sel).forEach(e => { e.classList.toggle("off", !on); e.querySelectorAll("textarea, input, select").forEach(i => { i.disabled = !on; }); });
  show(".x-cert, .x-cert-save", !!(mz && mz.checked));
  show(".x-pg", !!(pg && pg.checked));
  if (pg && pg.checked) pgSrvOpen(box.closest("form"));
};
window.pgSrvOpen = form => {      // the node gets the name of the server until it is typed over; the core configs of the chosen panel are read
  pgSrv.cores = {};
  const nm = form.elements.x_pg_name; if (nm && !nm.dataset.touched) nm.value = (form.elements.name.value || "").trim();
  return pgSrvCores(form);
};
window.pgSrvMirror = form => { const nm = form.elements.x_pg_name; if (nm && !nm.disabled && !nm.dataset.touched) nm.value = (form.elements.name.value || "").trim(); };
window.pgSrvCores = async form => {
  const sel = form.elements.x_pg_core, pid = form.elements.x_pg_panel && form.elements.x_pg_panel.value; if (!sel || !pid) return;
  sel.innerHTML = '<option value="">reading the core configs…</option>';
  try {
    const all = pgSrv.cores[pid] || (pgSrv.cores[pid] = (await api(`/api/panels/${pid}/pg/cores`)).cores || []), xr = all.filter(c => !c.type || c.type === "xray");
    sel.innerHTML = xr.length ? xr.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("") : '<option value="">this panel has no core config: make one in its Core tab first</option>';
  } catch (e) { sel.innerHTML = `<option value="">${esc(e.message)}</option>`; }
};

/* what the form asks for, checked before anything is added; null when the tick is off */
window.pgSrvAsk = f => {
  if (f.get("x_pgnode") !== "on") return null;
  const pid = +f.get("x_pg_panel"), name = (f.get("x_pg_name") || "").trim() || (f.get("name") || "").trim(), core = +f.get("x_pg_core") || null, coef = parseFloat(f.get("x_pg_coef"));
  if (!pid) throw new Error("Choose the PasarGuard panel for the node (or untick PasarGuard node)");
  if (!core) throw new Error("That panel has no core config yet: make one in its Core tab first (or untick PasarGuard node)");
  if (!(coef >= 0)) throw new Error("The usage coefficient is a number from 0 up");
  return {panel_id: pid, name, core_config_id: core, usage_coefficient: coef};
};
/* after the server is added and logs in: plan, start, follow - the Node view does the same with its own window */
window.pgSrvMake = async (sid, sname, o) => {
  const body = {server_id: sid, name: o.name, usage_coefficient: o.usage_coefficient}; if (o.core_config_id) body.core_config_id = o.core_config_id;
  const pname = (pgSrvPanels().find(p => p.id === o.panel_id) || {}).name || "the panel";
  try {
    const plan = await api(`/api/panels/${o.panel_id}/pg/nodes/make/plan`, {method: "POST", body: JSON.stringify(body)});
    if (plan.problems.length) { await glassChoice(`${sname} is added, but it cannot be made a PasarGuard node yet: ${plan.problems[0]} Use its window → Node → Make a node when that is fixed.`, [{label: "OK", v: 1, cls: "pri"}]); return false; }      // (a dialog, not a toast: the owner did not see the toast and thought nothing happened)
    const portNote = (plan.warnings || []).find(w => /ports/i.test(w)); if (portNote) toast("ℹ " + portNote);      // (the usual ports are taken: the node goes to a free pair by itself)
    const r = await api(`/api/panels/${o.panel_id}/pg/nodes/make`, {method: "POST", body: JSON.stringify(body)});
    toast(`Installing the PasarGuard node on ${sname}: the steps show on its row`);      // (the owner, 2026-10-15: the steps are on the row of the server, not in a big window)
    refresh();
    const fin = await followStory(r.action_id, document.createElement("div"), 1000), lastLine = fin && fin.story ? [...fin.story].reverse().find(l => l && l.trim() && !/^Done/.test(l.trim())) : "";
    toast(fin && fin.ok ? `✓ ${sname} is a node of ${pname} now` : "✕ " + String(lastLine || "Not finished").trim().slice(0, 200), fin && fin.ok ? "ok" : "bad");
    pgNodesLoad(o.panel_id, true).catch(() => {}); pgSrvLoad().then(() => { if (state.page === "nodes" && state.open == null && !state.nodeWin) refresh(); });
    return !!(fin && fin.ok);
  } catch (e) { await glassChoice(`${sname} is added, but making it a node stopped: ${e.message}`, [{label: "OK", v: 1, cls: "pri"}]); return false; }
};

/* ---- the Install tab of a server window: one PasarGuard card that makes the server a node of a panel of your choice (the old card asked for an API key and a certificate by hand) */
window.pgSrvCard = s => {
  const ps = pgSrvPanels(), mine = pgSrvOn(s).filter(x => x.type === "pasarguard");
  const chip = mine.length ? mine.map(x => `<span class="pill ${x.status === "connected" ? "ok" : "err"}" title="${esc(pgSrvTip(x))}">${esc(x.name)} · ${esc(x.panel)} · ${esc(x.status || "?")}</span>`).join("") : "";
  const sel = ps.length ? `<select id="ins-pgp-${s.id}" title="The PasarGuard panel the node is added to">${ps.map(p => `<option value="${p.id}" ${mine.some(x => x.panel_id === p.id) ? "disabled" : ""}>${esc(p.name)}</option>`).join("")}</select>` : '<span class="mut">no PasarGuard panel yet: add one on the Panel page</span>';
  return `<div class="ins-node"><div class="ins-h"><span class="ins-sq pg"></span><b>PasarGuard node</b>${chip}</div>
    <div class="ins-field">${sel}</div>
    <div class="ins-act"><button type="button" class="ins-btn" onclick="srvPgCert(${s.id})" title="The public certificate of the node installed on this server, to copy into a panel by hand">Show certificate</button><span class="sp"></span>${mine.length ? `<button type="button" class="ins-btn" onclick="pgSrvOpenNode(${s.id}, '${pgSrvKey(mine[0])}')" title="The Node view of this window: state, log, usage, certificate, edit, restart">Manage the node</button>` : ""}<button type="button" class="ins-btn pri" ${ps.length ? "" : "disabled"} onclick="pgSrvWin(${s.id})" title="Install the node here (or find the one that is here), read its certificate, add it to the panel">Make a node…</button></div></div>`;
};
window.pgSrvWin = (sid, pid) => {      // the plan of "make a node", for the panel chosen on the card of the Install view or in the Node view (the first panel when there is only one)
  const sel = $("#np-mk-panel") || $(`#ins-pgp-${sid}`); pid = pid || (sel ? +sel.value : (pgSrvPanels()[0] || {}).id);
  if (!pid) { toast("✕ Add a PasarGuard panel first (the Panel page)", "bad"); return; }
  pgNodesUse(pid); pgNodesMake(sid);
};

/* ---- removing a server: its node stays in the panel unless the owner says it goes too (asked after the server is gone, default: stays) */
window.pgSrvAfterDelete = async (sname, nodes) => {
  for (const x of nodes) {
    if (x.type === "pasarguard" && x.node_id != null) {
      if (await gconfirm(`${sname} was the node ${x.name} of the PasarGuard panel ${x.panel}. Delete that node from the panel too? Its traffic history goes with it and whoever is connected through it is cut; the users stay, and the node is saved to a file first (Servers → Nodes… → Backups of the nodes can make it again). OK deletes it, Cancel leaves it in the panel.`, {danger: true})) {
        try { await api(`/api/panels/${x.panel_id}/pg/nodes/${x.node_id}?confirm=true`, {method: "DELETE"}); toast(`✓ Node ${x.name} deleted from ${x.panel}`, "ok"); pgNodesLoad(x.panel_id, true).catch(() => {}); } catch (e) { toast("✕ " + e.message, "bad"); }
      }
    } else toast(`The node ${x.name} is still in the ${x.type === "marzban" ? "Marzban" : ""} panel ${x.panel}: remove it there if the server is gone for good.`);
  }
  pgSrvLoad();
};

/* ---- a node outside our servers can be added to them (the form opens with the name and the address filled in) */
window.pgSrvAddFromNode = i => { const x = (pgSrv.outside || [])[i]; if (x) pgSrvAddServer(x.name, x.address); };      // a node outside our servers: its row number (the handler never carries a name that came from a panel)
window.pgSrvAddServer = (name, address) => { closeModal(); labAddWith(name, address); };      // the Lab's Add profile opens on a new server with the name and the address filled in (lab.js finds where it is, Iran or foreign, from the address)
