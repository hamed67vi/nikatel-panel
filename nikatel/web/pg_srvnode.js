/* Servers <-> PasarGuard nodes (the owner, 2026-10-14). A server of ours and a node of a PasarGuard panel are one machine, so the two pages work together:
   - "Add server" has a tick "PasarGuard node": the server is added, then made a node of the chosen panel (install over SSH, read the certificate, add it to the panel, wait) - the same
     flow as the Nodes tab; the API key is made by this panel and never typed
   - the Servers list shows the nodes that run on each server (every panel, Marzban too, read from what the health job saw: no call to a panel); a click opens that panel
   - the server window can make a server a node of a panel; removing a server asks whether its node goes from the PasarGuard panel too; the Nodes tab can add the machine of a node to the servers
   Routes used: GET /api/servers/panel-nodes, GET /api/panels/{id}/pg/cores, POST /api/panels/{id}/pg/nodes/make[/plan], DELETE /api/panels/{id}/pg/nodes/{node}?confirm=true.
   Uses the helpers of app.js: $, api, esc, toast, showModal, closeModal, gconfirm, followStory, go, refresh, state, pn and the Nodes tab (pg_nodes.js). */
const pgSrv = {nodes: {}, at: 0, cores: {}};
window.pgSrvPanels = () => ((state.panelsCache || {}).panels || []).filter(p => p.type === "pasarguard");         // the PasarGuard panels this panel manages (the Servers page keeps the list)
window.pgSrvOn = s => ((pgSrv.nodes || {})[s.id] || []);                                                          // the nodes (of every panel) that run on one server

/* ---- what runs on each server: read once per look at the page (the answer is small and comes from this panel's own database) */
window.pgSrvLoad = async () => {
  try { pgSrv.nodes = (await api("/api/servers/panel-nodes")).servers || {}; pgSrv.at = Date.now(); } catch (e) { /* the list stays as it was: the page works without the chips */ }
  return pgSrv.nodes;
};
const pgSrvTip = x => `${x.type === "pasarguard" ? "PasarGuard" : "Marzban"} node ${x.name} of the panel ${x.panel}: ${x.status || "?"}${x.message ? " (" + x.message + ")" : ""}${x.match === "dns" ? " - matched through the DNS" : ""}`;
window.pgSrvChips = s => pgSrvOn(s).map(x => `<span class="mchip ${x.status === "connected" ? "ok" : x.status === "disabled" || x.status === "connecting" ? "" : "bad"}" onclick="event.stopPropagation();pgSrvOpenPanel(${x.panel_id}, '${esc(x.type)}')" title="${esc(pgSrvTip(x))}">${esc(x.panel)}</span>`).join("");
window.pgSrvOpenPanel = (pid, type) => {      // the panel the node belongs to: its Nodes tab (PasarGuard) or its nodes window (Marzban)
  go("configs"); pn.id = String(pid); pn.tab = type === "pasarguard" ? "nodes" : "users"; pn.users = pn.hosts = pn.core = pn.user = pn.logs = null; refresh();
  if (type !== "pasarguard") setTimeout(() => { if (typeof pnNodesWin === "function") pnNodesWin(); }, 900);
};

/* ---- the tick of "Add server": a PasarGuard node (and the Marzban node next to it: one machine has one node on the ports 62050 / 62051, so the two ticks exclude each other) */
window.pgSrvTick = () => {
  const ps = pgSrvPanels();
  return `<label class="chk" title="${ps.length ? "Install the PasarGuard node (Docker) on the server and add it to a PasarGuard panel: ports 62050 / 62051" : "Add a PasarGuard panel first (the Users page): the node is added to it"}"><input name="x_pgnode" type="checkbox" ${ps.length ? "" : "disabled"} onchange="srvNodeKind(this)"> PasarGuard node</label>`;
};
window.pgSrvBox = () => {
  const ps = pgSrvPanels();
  return `<div class="x-pg off"><div class="x-pg-grid">
      <label>Panel<select name="x_pg_panel" disabled onchange="pgSrvCores(this.form)">${ps.map(p => `<option value="${p.id}" ${String(p.id) === String(pn.id) ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>
      <label>Node name<input name="x_pg_name" disabled maxlength="40" autocomplete="off" placeholder="the name of the server" oninput="this.dataset.touched = 1"></label>
      <label>Core config<select name="x_pg_core" disabled><option value="">…</option></select></label>
      <label>Usage coefficient<input name="x_pg_coef" type="number" step="0.1" min="0" value="1" disabled></label></div>
    <small class="mut">The panel installs the PasarGuard node on the server over SSH (Docker, a few minutes), reads its certificate and adds it to the chosen panel. The API key is made by this panel and never shown. Needs a working SSH login: a password above, or the panel's key already on the server.</small></div>`;
};
window.srvNodeKind = el => {      // the two ticks (Marzban node, PasarGuard node) exclude each other; each shows its own fields
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
/* after the server is added and logs in: plan, start, follow - the Nodes tab does the same with its own window */
window.pgSrvMake = async (sid, sname, o) => {
  const body = {server_id: sid, name: o.name, usage_coefficient: o.usage_coefficient}; if (o.core_config_id) body.core_config_id = o.core_config_id;
  const pname = (pgSrvPanels().find(p => p.id === o.panel_id) || {}).name || "the panel";
  try {
    const plan = await api(`/api/panels/${o.panel_id}/pg/nodes/make/plan`, {method: "POST", body: JSON.stringify(body)});
    if (plan.problems.length) { toast(`✕ ${sname} is added, but it cannot be made a node yet: ${plan.problems[0]} Use Install → PasarGuard node when that is fixed.`, "bad"); return false; }
    const r = await api(`/api/panels/${o.panel_id}/pg/nodes/make`, {method: "POST", body: JSON.stringify(body)});
    showModal(`<b>Making ${esc(sname)} a node of ${esc(pname)}</b>`, '<div id="pg-srv-story" class="fix pg-nodes-story"><div class="fx-line run"><i></i>Starting…</div></div><div class="frm-act"><small class="mut">It keeps running if you close this window.</small><span class="sp"></span><button class="sm" onclick="closeModal()">Close</button></div>', "wide");
    const fin = await followStory(r.action_id, $("#pg-srv-story"), 1000);
    toast(fin && fin.ok ? `✓ ${sname} is a node of ${pname} now` : "✕ Not finished: read the last line of the story", fin && fin.ok ? "ok" : "bad");
    pgSrvLoad().then(() => { if (state.page === "nodes") refresh(); });
    return !!(fin && fin.ok);
  } catch (e) { toast("✕ " + e.message, "bad"); return false; }
};

/* ---- the Install tab of a server window: one PasarGuard card that makes the server a node of a panel of your choice (the old card asked for an API key and a certificate by hand) */
window.pgSrvCard = s => {
  const ps = pgSrvPanels(), mine = pgSrvOn(s).filter(x => x.type === "pasarguard");
  const chip = mine.length ? mine.map(x => `<span class="pill ${x.status === "connected" ? "ok" : "err"}" title="${esc(pgSrvTip(x))}">${esc(x.name)} · ${esc(x.panel)} · ${esc(x.status || "?")}</span>`).join("") : "";
  const sel = ps.length ? `<select id="ins-pgp-${s.id}" title="The PasarGuard panel the node is added to">${ps.map(p => `<option value="${p.id}" ${mine.some(x => x.panel_id === p.id) ? "disabled" : ""}>${esc(p.name)}</option>`).join("")}</select>` : '<span class="mut">no PasarGuard panel yet: add one on the Users page</span>';
  return `<div class="ins-node"><div class="ins-h"><span class="ins-sq pg"></span><b>PasarGuard node</b>${chip}</div>
    <div class="ins-field">${sel}</div>
    <div class="ins-act"><button type="button" class="ins-btn" onclick="srvPgCert(${s.id})" title="The public certificate of the node installed on this server, to copy into a panel by hand">Show certificate</button><span class="sp"></span>${mine.length ? `<button type="button" class="ins-btn" onclick="pgSrvOpenPanel(${mine[0].panel_id}, 'pasarguard')" title="Open the Nodes tab of the panel">Open the panel</button>` : ""}<button type="button" class="ins-btn pri" ${ps.length ? "" : "disabled"} onclick="pgSrvWin(${s.id})" title="Install the node here (or find the one that is here), read its certificate, add it to the panel">Make a node…</button></div></div>`;
};
window.pgSrvWin = sid => {      // the plan of the Nodes tab, for the panel chosen on the card
  const sel = $(`#ins-pgp-${sid}`), pid = sel ? +sel.value : (pgSrvPanels()[0] || {}).id;
  if (!pid) { toast("✕ Add a PasarGuard panel first (the Users page)", "bad"); return; }
  pgNodesS.id = pid; pgNodesMake(sid);
};

/* ---- removing a server: its node stays in the panel unless the owner says it goes too (asked after the server is gone, default: stays) */
window.pgSrvAfterDelete = async (sname, nodes) => {
  for (const x of nodes) {
    if (x.type === "pasarguard" && x.node_id != null) {
      if (await gconfirm(`${sname} was the node ${x.name} of the PasarGuard panel ${x.panel}. Delete that node from the panel too? Its traffic history goes with it and whoever is connected through it is cut; the users stay, and the node is saved to a file first (the Nodes tab → Backups can make it again). OK deletes it, Cancel leaves it in the panel.`, {danger: true})) {
        try { await api(`/api/panels/${x.panel_id}/pg/nodes/${x.node_id}?confirm=true`, {method: "DELETE"}); toast(`✓ Node ${x.name} deleted from ${x.panel}`, "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
      }
    } else toast(`The node ${x.name} is still in the ${x.type === "marzban" ? "Marzban" : ""} panel ${x.panel}: remove it there if the server is gone for good.`);
  }
  pgSrvLoad();
};

/* ---- the Nodes tab: a node whose machine is not in the servers can be added to them (the form opens with the name and the address filled in) */
window.pgSrvAddFromNode = id => {
  const n = typeof pgNodesNode === "function" ? pgNodesNode(id) : null; if (!n) return;
  state.prefill = {name: String(n.name || "").replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[^A-Za-z0-9]+/, "").slice(0, 60), host: String(n.address || "").replace(/^\[|\]$/g, "")};
  go("nodes"); state.edit = "new"; state.open = null; refresh();
  setTimeout(() => { const f = document.querySelector(".srv-form2"); if (f && f.elements.host && f.elements.host.value && typeof srvWhere === "function") srvWhere(f); }, 900);      // where is it (Iran or foreign): found from the address
};
