/* Settings → About & update, its right column (the owner, 2026-10-14: "add the update of the nodes and of PasarGuard itself to Settings"; 2026-10-16: "move it to About & update"): the newest releases of PasarGuard, its node and Xray (GitHub, kept an hour
   by the panel), every PasarGuard panel with the version it runs and the server that runs it, and every node with its software and Xray - each marked when it is behind. Nothing is automatic: an update is a
   click and a question. Nodes are updated ONE AT A TIME (each must be connected again with the new version before the next; one that does not come back stops the run), the panel itself on its own server
   with the official command (a backup first, the name of the panel typed back). Both are actions of the panel with a story that this file follows.
   Routes used: GET /api/pg-updates, GET /api/pg-updates/panels/{id}/plan, POST /api/pg-updates/panels/{id}, POST /api/pg-updates/nodes, GET /api/actions/{id}.
   Uses the helpers of app.js: $, api, esc, toast, showModal, closeModal, gconfirm, gprompt, followStory, go, state, SI, sFrame, sHero, sGrp, sItem, sWide. */
const pgu = {data: null, at: 0};
window.pgUpdBody = () => `<div class="abt-card abt-pg"><div class="abt-ch"><i class="abt-ti">${SI.box}</i><div class="abt-ct"><div class="abt-t1"><b>PasarGuard</b></div></div><span class="abt-pill" id="pgu-pill">…</span></div><div class="abt-cb"><div id="pgu-box"><div class="mut" style="padding:8px 4px">reading the panels and GitHub…</div></div></div></div>`;      // the fourth box of Settings → About & update (app.js), filled by pgUpdLoad

const pguShort = v => String(v || "").replace(/^v/, "");
const pguVer = (now, latest, behind) => behind === true ? `<span class="pgu-v warn" title="${esc(`runs ${now || "?"}, the newest is ${latest}`)}">${esc(pguShort(now) || "?")} <i>→</i> <b>${esc(pguShort(latest))}</b></span>`
  : behind === false ? `<span class="pgu-v ok" title="the newest release">${esc(pguShort(now))}</span>` : `<span class="pgu-v mut" title="${now ? "the newest release is not known (GitHub did not answer)" : "the node reports no version (it is not connected)"}">${esc(pguShort(now) || "—")}</span>`;
const pguDot = st => `<i class="dotc ${st === "connected" ? "ok" : st === "disabled" || st === "connecting" ? "" : "bad"}"></i>`;

function pgUpdHtml(d) {
  const L = d.latest || {}, lv = k => (L[k] && L[k].version) || "", bad = Object.entries(L).filter(([, v]) => v && v.error).map(([k, v]) => `${k}: ${v.error}`);
  const nodes = d.nodes || [], panels = d.panels || [];
  const swN = nodes.filter(n => n.behind === true && n.status === "connected"), xrN = nodes.filter(n => n.xray_behind === true && n.status === "connected");
  const total = panels.filter(p => p.behind === true).length + nodes.filter(n => n.behind === true || n.xray_behind === true).length;
  const unknown = nodes.filter(n => n.behind === null).length + panels.filter(p => p.behind === null && !p.error).length;      // a node that reports no version (it is not connected) is not "up to date": nothing is known about it
  const hero = `<div class="pgu-top ${total ? "warn" : unknown ? "mut" : "ok"}"><i class="pgu-ti">${SI.arrowUp}</i><span class="pgu-tt"><b>${total ? `${total} update${total === 1 ? "" : "s"} available` : unknown ? "Nothing is known to be behind" : "Everything is up to date"}</b><small title="${esc(bad.length ? "GitHub did not answer for " + bad.join("; ") : "the newest releases on GitHub - asked again by itself every ten minutes")}">panel ${esc(lv("panel") || "?")} · node ${esc(lv("node") || "?")} · Xray ${esc(lv("xray") || "?")}${unknown ? ` · ${unknown} with no version` : ""}${bad.length ? ' · <span class="err">GitHub did not answer</span>' : ""}</small></span></div>`;
  const prow = p => {
    const can = p.behind !== false && p.host && !d.read_only && !p.error, why = d.read_only ? "The panel is read-only (NIKATEL_READ_ONLY)" : p.error ? p.error : !p.host ? "The server that runs this panel is not in the Servers list" : p.behind === false ? "This panel is the newest" : "Update this panel on its server";
    const ver = p.error ? `<span class="pill err" title="${esc(p.error)}">not read</span>` : `<span class="pill ${p.behind === true ? "warn" : p.behind === false ? "ok" : ""}">${pguVer(p.version, p.latest, p.behind)}</span>`;
    const where = p.host ? `<small class="mut" title="${esc(p.url)}">on ${esc(p.host.name)}${p.host.online === false ? ' <span class="err">(does not answer)</span>' : ""}</small>` : `<a class="ue-link" onclick="pgUpdAddHost(${p.id})" title="${esc(p.host_name)} is not in the Servers list: add it and this panel can update itself over SSH">add its server</a>`;
    return `<div class="pgu-prow"><b>${esc(p.name)}</b>${ver}${where}<span class="sp"></span><button class="sm ${can && p.behind ? "pri" : ""}" ${can ? "" : "disabled"} onclick="pgUpdPanel(${p.id})" title="${esc(why)}">Update…</button></div>`;
  };
  const nrow = n => `<div class="pgu-row ${n.status === "connected" ? "" : "off"}"><span class="pgu-n" title="${esc(n.address)}">${pguDot(n.status)}<b>${esc(n.name)}</b><small class="mut">${esc(n.panel)}</small></span>
      <span class="pgu-s mut" title="${n.server ? "runs on " + esc(n.server.name) : "no server of ours has this address"}">${n.server ? esc(n.server.name) : esc(n.address)}</span>
      <span class="pgu-c" title="the software of the node">${pguVer(n.version, lv("node"), n.behind)}</span><span class="pgu-c" title="the Xray core on the node">${pguVer(n.xray, lv("xray"), n.xray_behind)}</span>
      <span class="pgu-a"><button class="sm" ${n.behind === true && n.status === "connected" ? "" : "disabled"} onclick="pgUpdNodes([[${n.panel_id}, ${n.node_id}, 'node']])" title="Update the software of this node (it restarts: its users drop for a moment)">Update</button><button class="sm" ${n.xray_behind === true && n.status === "connected" ? "" : "disabled"} onclick="pgUpdNodes([[${n.panel_id}, ${n.node_id}, 'core']])" title="Update Xray on this node (it restarts the core: its users drop for a moment)">Xray</button></span></div>`;
  const nodesHtml = nodes.length ? `<div class="pgu-tbl"><div class="pgu-row pgu-hd"><span>Node</span><span>Server</span><span>Software</span><span>Xray</span><span></span></div>${nodes.map(nrow).join("")}</div>` : '<div class="pgu-none mut">No node is registered in a PasarGuard panel.</div>';
  const acts = (swN.length ? `<button class="sm pri" onclick="pgUpdAll('node')" title="Update the software of every connected node that is behind, one at a time">Update ${swN.length} node${swN.length === 1 ? "" : "s"}…</button>` : "") + (xrN.length ? `<button class="sm" onclick="pgUpdAll('core')" title="Update Xray on every connected node that is behind, one at a time">Xray on ${xrN.length} node${xrN.length === 1 ? "" : "s"}…</button>` : "");      // (the buttons are there only when there is something to update)
  const ro = !!d.read_only, roWhy = "The panel is read-only (NIKATEL_READ_ONLY)";
  const safeBtn = `<button class="sm pri pgu-safe" ${ro ? "disabled" : ""} onclick="pgUpdSafe()" title="${esc(ro ? roWhy : "One button for everything that is behind: the nodes one at a time, the least used first, each looked at before and after with its old version kept to go back to - then the panel. You see the whole plan first and nothing is changed before you confirm.")}">Safe update…</button>`;      // (the owner, 2026-10-31: one button for the panel and the nodes, least used node first)
  const go = `<div class="pgu-go">${safeBtn}<button class="sm ${panels.some(p => p.behind === true) ? "pri" : ""}" ${ro ? "disabled" : ""} onclick="pgUpdPanelTop()" title="${esc(ro ? roWhy : "Update the PasarGuard panel itself, on its own server: a backup first, and the name of the panel typed back. The nodes are not touched.")}">Update panel…</button><button class="sm ${swN.length || xrN.length ? "pri" : ""}" ${ro ? "disabled" : ""} onclick="pgUpdNodesOnly()" title="${esc(ro ? roWhy : "Update only the nodes: the software and Xray of every connected node that is behind, one at a time. The panel is not touched.")}">Update nodes only…</button></div>`;      // (the owner, 2026-10-18: one option for the panel and one for only the nodes - always there; what stands in the way is said when one is clicked)
  return hero + go + `<div class="pgu-list"><div class="pgu-cap">Panels</div>${panels.map(prow).join("") || '<div class="pgu-none mut">No PasarGuard panel yet: add one on the Panel page.</div>'}<div class="pgu-cap">Nodes (${nodes.length})<span class="sp"></span>${acts}</div>${nodesHtml}</div>`;      // (the owner, 2026-10-16: the panels and the nodes sit in ONE box of a fixed size that scrolls inside)
}
window.pgUpdLoad = async force => {
  const box = $("#pgu-box"); if (!box) return;
  clearTimeout(pgu.timer); pgu.timer = setTimeout(() => { if ($("#pgu-box")) pgUpdLoad(true); }, 600000);      // (no Check button - the owner, 2026-10-16: GitHub and the panels are asked again by themselves every ten minutes)
  if (pgu.data && Date.now() - pgu.at > 600000) force = true;
  if (!pgu.data) box.innerHTML = '<div class="loading"><i class="spin"></i><span>Reading the panels and GitHub…</span></div>';
  try {
    pgu.data = await api(`/api/pg-updates${force ? "?force=true" : ""}`); pgu.at = Date.now();
    const b = $("#pgu-box"); if (!b) return; b.innerHTML = pgUpdHtml(pgu.data);
    const left = (pgu.data.panels || []).filter(p => p.behind === true).length + (pgu.data.nodes || []).filter(n => n.behind === true || n.xray_behind === true).length, pill = $("#pgu-pill");
    if (pill) { pill.textContent = left ? `${left} behind` : "Up to date"; pill.className = "abt-pill" + (left ? " warn" : ""); }
  } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
};
window.pgUpdAddHost = pid => { const p = ((pgu.data || {}).panels || []).find(x => x.id === pid); if (p) pgSrvAddServer(p.name, p.host_name); };      // the Add server form with the address of the panel filled in

/* ---- the story of an update: a window that follows the action until it is done (it keeps running when the window is closed) */
const pgUpdFollow = async (title, aid, polls) => {
  showModal(`<b>${esc(title)}</b>`, '<div id="pgu-story" class="fix pg-nodes-story"><div class="fx-line run"><i></i>Starting…</div></div><div class="frm-act"><small class="mut">It keeps running if you close this window; its story is kept in the history of the Tunnel Doctor.</small><span class="sp"></span><button class="sm" onclick="closeModal()">Close</button></div>', "wide");
  const fin = await followStory(aid, $("#pgu-story"), polls);
  toast(fin && fin.ok ? "✓ Done" : "✕ Not finished: read the last line of the story", fin && fin.ok ? "ok" : "bad");
  pgUpdLoad(true);
  return fin;
};

/* ---- nodes: items are [panel id, node id, "node" | "core"] */
window.pgUpdNodes = async items => {
  const all = (pgu.data && pgu.data.nodes) || [], find = it => all.find(n => n.panel_id === it[0] && n.node_id === it[1]) || {name: "#" + it[1]};
  const names = [...new Set(items.map(it => find(it).name))], kinds = new Set(items.map(it => it[2])), what = kinds.size > 1 ? "the software and Xray" : items[0][2] === "node" ? "the software" : "Xray";      // (a node may be in the list twice: its software, then its Xray)
  const many = names.length > 1, text = `Update ${what} of ${many ? names.length + " nodes (" + names.slice(0, 5).join(", ") + (names.length > 5 ? ", …" : "") + "), one at a time" : "node " + names[0]}? Each node restarts and the users connected through it drop for a moment. ${many ? "The next node starts only when the one before is connected again with the new version, and a node that does not come back stops the run (the nodes after it stay as they are). " : ""}It takes a minute or two per node.`;
  if (!await gconfirm(text, {danger: true})) return;
  try {
    const r = await api("/api/pg-updates/nodes", {method: "POST", body: JSON.stringify({items: items.map(([panel_id, node_id, w]) => ({panel_id, node_id, what: w}))})});
    pgUpdFollow(names.length === 1 ? `Updating ${names[0]}` : `Updating ${names.length} nodes, one at a time`, r.action_id, 3000);
  } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.pgUpdAll = what => {
  const list = ((pgu.data && pgu.data.nodes) || []).filter(n => n.status === "connected" && (what === "node" ? n.behind === true : n.xray_behind === true));
  if (list.length) pgUpdNodes(list.map(n => [n.panel_id, n.node_id, what]));
};

/* ---- the two buttons under the headline (the owner, 2026-10-18): the panel, and only the nodes */
window.pgUpdNodesOnly = () => {
  const nodes = ((pgu.data && pgu.data.nodes) || []).filter(n => n.status === "connected"), items = [];
  for (const n of nodes) { if (n.behind === true) items.push([n.panel_id, n.node_id, "node"]); if (n.xray_behind === true) items.push([n.panel_id, n.node_id, "core"]); }      // (a node's software first, then its Xray: the run goes node by node)
  if (items.length) return pgUpdNodes(items);
  toast(nodes.length ? "Every connected node is up to date" : "No node is connected", nodes.length ? "ok" : "bad");
};
window.pgUpdPanelTop = () => {
  const panels = (pgu.data && pgu.data.panels) || [], can = panels.filter(p => p.behind !== false && p.host && !p.error);
  if (can.length === 1) return pgUpdPanel(can[0].id);
  if (can.length > 1) return showModal("<b>Update a PasarGuard panel</b>", `<div class="pgu-pick">${can.map(p => `<div class="pgu-prow" style="display:flex;align-items:center;gap:10px;padding:7px 0"><b>${esc(p.name)}</b><span class="pill ${p.behind === true ? "warn" : ""}">${pguVer(p.version, p.latest, p.behind)}</span><small class="mut">on ${esc(p.host.name)}</small><span class="sp"></span><button class="sm ${p.behind ? "pri" : ""}" onclick="closeModal(); pgUpdPanel(${p.id})">Update…</button></div>`).join("")}</div><div class="frm-act"><span class="sp"></span><button type="button" onclick="closeModal()">Cancel</button></div>`, "wide");
  const noHost = panels.find(p => !p.host && !p.error);      // a panel whose server is not in the Servers list cannot update itself: the form that adds it opens, with the address filled in
  if (noHost) { toast(`The server that runs ${noHost.name} is not in the Servers list: add it first`, "bad"); return pgUpdAddHost(noHost.id); }
  if (panels.length && panels.every(p => p.behind === false)) return toast("The panel is the newest already", "ok");
  toast(!panels.length ? "No PasarGuard panel yet: add one on the Panel page" : (panels.find(p => p.error) || {}).error || "No panel can be updated now", "bad");
};

/* ---- Safe update (the owner, 2026-10-31): the plan first - the nodes in the order they will go (the quietest first), the panel last, what is skipped and why - then one confirmation */
window.pgUpdSafe = async () => {
  showModal("<b>Safe update</b>", '<div class="loading"><i class="spin"></i><span>Looking at the nodes and how busy each one is (a few seconds)…</span></div>', "wide");
  try {
    const r = await api("/api/pg-updates/safe/plan");
    const nodes = r.nodes.map((n, i) => `<li><b>${i + 1}. ${esc(n.name)}</b> <small class="mut">on ${esc(n.server.name)} · ${esc(n.usage_text)} now</small> - ${n.do.map(esc).join(" + ")}</li>`).join("");
    const pans = r.panels.map(p => `<li><b>Panel ${esc(p.name)}</b> <small class="mut">${esc(pguShort(p.version))} → ${esc(pguShort(p.latest))}</small> - ${p.skip ? `<span class="err">will NOT be updated: ${esc(p.skip)}</span>` : "last, after the nodes (a backup first)"}</li>`).join("");
    const skipped = r.skipped.map(s => `<li><b>${esc(s.name)}</b> - ${esc(s.why)}</li>`).join("");
    const need = (r.panels.find(p => !p.skip) || {}).name;
    showModal("<b>Safe update</b>", `
      <div class="sbox pg-nodes-plan"><h5><i>🛡</i>What this does</h5>
        ${r.steps.length ? `<ol class="pg-nodes-steps">${r.steps.map(x => `<li>${esc(x)}</li>`).join("")}</ol>` : ""}
        ${nodes ? `<h5>Order - the quietest first</h5><ol class="pg-nodes-steps">${nodes}</ol>` : ""}
        ${pans ? `<h5>The panel</h5><ul class="pg-nodes-steps">${pans}</ul>` : ""}
        ${skipped ? `<h5>Not touched</h5><ul class="pg-nodes-steps">${skipped}</ul>` : ""}
        ${r.nothing && !r.problems.length ? '<div class="pg-nodes-note">There is nothing to update now: everything that can be updated is up to date.</div>' : ""}
        <div class="pg-nodes-note">${esc(r.note)}</div>
        ${r.problems.map(x => `<div class="err pg-nodes-note">✕ ${esc(x)}</div>`).join("")}</div>
      <form class="frm pg-nodes-form" onsubmit="return pgUpdSafeGo(event)">
        ${need ? `<label style="grid-column: 1 / -1">Type the name of the panel to confirm <small>(${esc(need)})</small><input name="typed" required autocomplete="off" spellcheck="false" placeholder="${esc(need)}"></label>` : ""}
        <div class="frm-act"><button class="danger" type="submit" ${r.can_start ? "" : "disabled"}>Start Safe update</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`, "wide");
  } catch (e) { showModal("<b>Safe update</b>", `<div class="err">${esc(e.message)}</div>`, "wide"); }
};
window.pgUpdSafeGo = async e => {
  e.preventDefault(); const f = e.target;
  try {
    const r = await api("/api/pg-updates/safe", {method: "POST", body: JSON.stringify({typed: f.elements.typed ? f.elements.typed.value.trim() : ""})});
    pgUpdFollow("Safe update", r.action_id, 3000);
  } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};

/* ---- the panel itself: the plan first (what it will do, what stands in the way), then the name typed back */
window.pgUpdPanel = async pid => {
  showModal("<b>Update a PasarGuard panel</b>", '<div class="loading"><i class="spin"></i><span>Looking at the panel and its server…</span></div>', "wide");
  try {
    const r = await api(`/api/pg-updates/panels/${pid}/plan`), p = r.panel;
    showModal(`<b>Update ${esc(p.name)}</b> <span class="pill">${esc(pguShort(p.version) || "?")} → ${esc(pguShort(r.latest) || "newest")}</span>`, `
      <div class="sbox pg-nodes-plan"><h5><i>🧭</i>What this does${r.host ? ` on ${esc(r.host.name)}` : ""}</h5><ol class="pg-nodes-steps">${r.steps.map(x => `<li>${esc(x)}</li>`).join("")}</ol>
        <div class="pg-nodes-note">The panel is away for a minute or two; its nodes keep serving the users that are connected. It is the command you would type over SSH - <b>pasarguard update</b> - and nothing else.</div>
        ${r.problems.map(x => `<div class="err pg-nodes-note">✕ ${esc(x)}</div>`).join("")}</div>
      <form class="frm pg-nodes-form" onsubmit="return pgUpdPanelGo(event, ${pid})">
        <label class="chk" style="grid-column: 1 / -1"><input name="backup" type="checkbox" checked> Back up first (the <b>pasarguard backup</b> command, when it has one - a backup that fails stops everything before anything is changed)</label>
        <label style="grid-column: 1 / -1">Type the name of the panel to confirm <small>(${esc(p.name)})</small><input name="typed" required autocomplete="off" spellcheck="false" placeholder="${esc(p.name)}" ${r.problems.length ? "disabled" : ""}></label>
        <div class="frm-act"><button class="danger" type="submit" ${r.problems.length ? "disabled" : ""}>Update the panel</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`, "wide");
  } catch (e) { showModal("<b>Update a PasarGuard panel</b>", `<div class="err">${esc(e.message)}</div>`, "wide"); }
};
window.pgUpdPanelGo = async (e, pid) => {
  e.preventDefault(); const f = e.target, name = (((pgu.data || {}).panels || []).find(x => x.id === pid) || {}).name || "the panel";
  try {
    const r = await api(`/api/pg-updates/panels/${pid}`, {method: "POST", body: JSON.stringify({backup: f.elements.backup.checked, typed: f.elements.typed.value.trim()})});
    pgUpdFollow(`Updating the panel ${name}`, r.action_id, 1600);
  } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};
