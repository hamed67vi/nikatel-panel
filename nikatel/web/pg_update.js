/* Settings → About & update, its right column (the owner, 2026-10-14: "add the update of the nodes and of PasarGuard itself to Settings"; 2026-10-16: "move it to About & update"): the newest releases of PasarGuard, its node and Xray (GitHub, kept an hour
   by the panel), every PasarGuard panel with the version it runs and the server that runs it, and every node with its software and Xray - each marked when it is behind. Nothing is automatic: an update is a
   click and a question. Nodes are updated ONE AT A TIME (each must be connected again with the new version before the next; one that does not come back stops the run), the panel itself on its own server
   with the official command (a backup first, the name of the panel typed back). Both are actions of the panel with a story that this file follows.
   Routes used: GET /api/pg-updates, GET /api/pg-updates/panels/{id}/plan, POST /api/pg-updates/panels/{id}, POST /api/pg-updates/nodes, GET /api/actions/{id}.
   Uses the helpers of app.js: $, api, esc, toast, showModal, closeModal, gconfirm, gprompt, followStory, go, state, SI, sFrame, sHero, sGrp, sItem, sWide. */
const pgu = {data: null, at: 0};
window.pgUpdBody = () => `<div class="abt-card abt-pg"><div class="abt-ch"><i class="abt-ti">${SI.box}</i><div class="abt-ct"><div class="abt-t1"><b>PasarGuard</b></div></div><span class="abt-pill" id="pgu-pill">…</span></div><div class="abt-cb"><div id="pgu-box"><div class="mut" style="padding:8px 4px">reading the panels and GitHub…</div></div></div></div>`;      // the fourth box of Settings → About & update (app.js), filled by pgUpdLoad

const pguShort = v => String(v || "").replace(/^v/, "");
function pgUpdHtml(d) {
  const L = d.latest || {}, lv = k => (L[k] && L[k].version) || "", bad = Object.entries(L).filter(([, v]) => v && v.error).map(([k, v]) => `${k}: ${v.error}`);
  const nodes = d.nodes || [], panels = d.panels || [];
  const swN = nodes.filter(n => n.behind === true && n.status === "connected"), xrN = nodes.filter(n => n.xray_behind === true && n.status === "connected");
  const total = panels.filter(p => p.behind === true).length + nodes.filter(n => n.behind === true || n.xray_behind === true).length;
  const unknown = nodes.filter(n => n.behind === null).length + panels.filter(p => p.behind === null && !p.error).length;      // a node that reports no version (it is not connected) is not "up to date": nothing is known about it
  const hero = `<div class="pgu-top ${total ? "warn" : unknown ? "mut" : "ok"}"><i class="pgu-ti">${SI.arrowUp}</i><span class="pgu-tt"><b>${total ? `${total} update${total === 1 ? "" : "s"} available` : unknown ? "Nothing is known to be behind" : "Everything is up to date"}</b><small title="${esc(bad.length ? "GitHub did not answer for " + bad.join("; ") : "the newest releases on GitHub - asked again by itself every ten minutes")}">panel ${esc(lv("panel") || "?")} · node ${esc(lv("node") || "?")} · Xray ${esc(lv("xray") || "?")}${unknown ? ` · ${unknown} with no version` : ""}${bad.length ? ' · <span class="err">GitHub did not answer</span>' : ""}</small></span></div>`;
  const ro = !!d.read_only, roWhy = "The panel is read-only (NIKATEL_READ_ONLY)";
  const panelLeft = panels.some(p => p.behind === true), nodesLeft = swN.length > 0 || xrN.length > 0;
  const btn = (scope, label, left, tip) => `<button class="sm ${left && !ro ? "pri" : ""}" ${left && !ro ? "" : "disabled"} onclick="pgUpdSafe('${scope}')" title="${esc(ro ? roWhy : left ? tip : "Nothing to update: " + (scope === "panel" ? "the panel" : "every connected node") + " is up to date")}">${label}</button>`;
  const go = `<div class="pgu-go">${btn("panel", "Safe update panel…", panelLeft, "Update the PasarGuard panel on its own server: you see the plan first - a backup, the check that no node would stop its core while the panel is away, then the official update. The nodes are not touched.")}${btn("nodes", "Safe update nodes…", nodesLeft, "Update the nodes: the quietest first, one at a time, each looked at before and after with its old version kept to go back to. You see the plan first. The panel is not touched.")}</div>`;      // (the owner, 2026-10-31: the two buttons are the safe ones, and off when there is nothing to update)
  return hero + go;      // (the owner, 2026-10-31: "this box should be removed entirely" - the headline and the two buttons are the card; what each node would get is in the window of Safe update)
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

/* ---- Safe update (the owner, 2026-10-31): two buttons, one for the panel and one for the nodes, both safe - the plan first (the nodes in the order they will go, the quietest first, what is skipped and why), then one
   confirmation (the name of the panel typed back when the panel is part of it). A button is off when there is nothing to update. Routes: GET /api/pg-updates/safe/plan?scope=, POST /api/pg-updates/safe. */
window.pgUpdSafe = async scope => {
  const title = scope === "panel" ? "Safe update - the panel" : "Safe update - the nodes";
  showModal(`<b>${title}</b>`, '<div class="loading"><i class="spin"></i><span>Looking at the panel, the nodes and how busy each one is (a few seconds)…</span></div>', "wide");
  try {
    const r = await api(`/api/pg-updates/safe/plan?scope=${scope}`);
    const nodes = r.nodes.map((n, i) => `<li><b>${i + 1}. ${esc(n.name)}</b> <small class="mut">on ${esc(n.server.name)} · ${esc(n.usage_text)} now</small> - ${n.do.map(esc).join(" + ")}</li>`).join("");
    const pans = r.panels.map(p => `<li><b>Panel ${esc(p.name)}</b> <small class="mut">${esc(pguShort(p.version))} → ${esc(pguShort(p.latest))}</small> - ${p.skip ? `<span class="err">will NOT be updated: ${esc(p.skip)}</span>${p.host ? "" : ` <a class="ue-link" onclick="closeModal(); pgUpdAddHost(${p.id})">add its server</a>`}` : "a backup first, then the official update"}</li>`).join("");
    const skipped = r.skipped.map(s => `<li><b>${esc(s.name)}</b> - ${esc(s.why)}</li>`).join("");
    const need = (r.panels.find(p => !p.skip) || {}).name;
    showModal(`<b>${title}</b>`, `
      <div class="sbox pg-nodes-plan"><h5><i>🛡</i>What this does</h5>
        ${r.steps.length ? `<ol class="pg-nodes-steps">${r.steps.map(x => `<li>${esc(x)}</li>`).join("")}</ol>` : ""}
        ${nodes ? `<h5>Order - the quietest first</h5><ol class="pg-nodes-steps">${nodes}</ol>` : ""}
        ${pans ? `<h5>The panel</h5><ul class="pg-nodes-steps">${pans}</ul>` : ""}
        ${skipped ? `<h5>Not touched</h5><ul class="pg-nodes-steps">${skipped}</ul>` : ""}
        ${r.nothing && !r.problems.length && !r.panels.some(p => p.skip) ? '<div class="pg-nodes-note">There is nothing to update now: everything that can be updated is up to date.</div>' : ""}
        <div class="pg-nodes-note">${esc(r.note)}</div>
        ${r.problems.map(x => `<div class="err pg-nodes-note">✕ ${esc(x)}</div>`).join("")}</div>
      <form class="frm pg-nodes-form" onsubmit="return pgUpdSafeGo(event, '${scope}')">
        ${need ? `<label style="grid-column: 1 / -1">Type the name of the panel to confirm <small>(${esc(need)})</small><input name="typed" required autocomplete="off" spellcheck="false" placeholder="${esc(need)}"></label>` : ""}
        <div class="frm-act"><button class="danger" type="submit" ${r.can_start ? "" : "disabled"}>Start Safe update</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`, "wide");
  } catch (e) { showModal(`<b>${title}</b>`, `<div class="err">${esc(e.message)}</div>`, "wide"); }
};
window.pgUpdSafeGo = async (e, scope) => {
  e.preventDefault(); const f = e.target;
  try {
    const r = await api("/api/pg-updates/safe", {method: "POST", body: JSON.stringify({scope, typed: f.elements.typed ? f.elements.typed.value.trim() : ""})});
    pgUpdFollow(scope === "panel" ? "Safe update - the panel" : "Safe update - the nodes", r.action_id, 3000);
  } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};
