/* Settings → About & update, its right column (the owner, 2026-10-14: "add the update of the nodes and of PasarGuard itself to Settings"; 2026-10-16: "move it to About & update"): the newest releases of PasarGuard, its node and Xray (GitHub, kept an hour
   by the panel), every PasarGuard panel with the version it runs and the server that runs it, and every node with its software and Xray - each marked when it is behind. Nothing is automatic: an update is a
   click and a question. Nodes are updated ONE AT A TIME (each must be connected again with the new version before the next; one that does not come back stops the run), the panel itself on its own server
   with the official command (a backup first, the name of the panel typed back). Both are actions of the panel with a story that this file follows.
   Routes used: GET /api/pg-updates, GET /api/pg-updates/panels/{id}/plan, POST /api/pg-updates/panels/{id}, POST /api/pg-updates/nodes, GET /api/actions/{id}; the nightly switch (the owner, 2026-11-03): POST /api/pg-updates/auto,
   POST /api/pg-updates/auto/rehearse.
   Uses the helpers of app.js: $, api, esc, toast, showModal, closeModal, gconfirm, gprompt, followStory, go, state, SI, sFrame, sHero, sGrp, sItem, sWide, ago, pgSw. */
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
  // the nightly switch (the owner, 2026-11-03: "a tick box so the panel, its nodes and Xray update by themselves at 03:00 Iran time, test first, very safe, roll back or fix on a problem"): the Safe update once a night with a time budget, the users looked at after every node, the panel kept and put back when it does not hold
  const au = d.auto || {}, lr = au.last || {}, hh = String(au.hour == null ? 3 : au.hour).padStart(2, "0");
  const heldBack = Object.entries(au.bad || {}).filter(([, v]) => v && v.version).map(([k, v]) => `${k === "node" ? "node software" : k === "xray" ? "Xray" : "panel"} ${pguShort(v.version)} (did not hold on ${v.where || "?"}, ${v.day || "?"})`);
  // one line: the title, a small (i) that opens the explanation (and the rehearsal), the switch - no paragraph on the card (the owner, 2026-11-04, with a picture of the row: "remove these extra explanations and put a neat little i in a corner instead")
  const autoRow = `<div class="eng-auto pgu-auto"><span class="eng-auto-t"><b>Update automatically every night at ${hh}:00 (Iran time)<i class="pgu-i" role="button" tabindex="0" title="${esc(pgUpdAutoTip(au))}" onclick="pgUpdAutoInfo()" onkeydown="if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); pgUpdAutoInfo(); }">i</i></b>${heldBack.length ? `<small class="err" title="A release that did not hold and was put back is never tried again by itself; the Safe update buttons still try it">Held back: ${esc(heldBack.join("; "))}</small>` : ""}${lr.text && lr.day ? `<small class="mut" title="${esc(lr.day)}">Last night${lr.at ? " " + ago(lr.at) + " ago" : ""}: ${esc(lr.text)}</small>` : ""}</span>${pgSw(!!au.auto, "pgUpdAuto(this.checked)", 'title="Switch the nightly update on or off"')}</div>`;
  return hero + go + autoRow;      // (the owner, 2026-10-31: "this box should be removed entirely" - the headline and the two buttons are the card; what each node would get is in the window of Safe update)
}
const pgUpdAutoTip = au => `Once a night at ${String(au.hour == null ? 3 : au.hour).padStart(2, "0")}:00 (Iran time) the Safe update runs by itself: the nodes one at a time, the quietest first, each looked at before and after (connected with the new version, and the configs that ride it still pass) with its old version kept to go back to; then the panel, its database and compose files kept first, looked at after and put back when it does not answer. Nothing new is started after ${au.budget_minutes || 60} minutes; the rest waits for the next night. Click for the details and a rehearsal.`;
window.pgUpdAutoInfo = () => {      // the (i) of the nightly switch: what a night does, in order, and the rehearsal button (nothing is changed by the rehearsal)
  const au = (pgu.data || {}).auto || {}, hh = String(au.hour == null ? 3 : au.hour).padStart(2, "0");
  const steps = [
    `Once a night at ${hh}:00 (Iran time), when the switch is on, the same Safe update as the two buttons runs by itself.`,
    "The nodes first, one at a time, the quietest first: each is looked at before (its server, read-only) and after (connected with the new version, watched for 45 s), and its old version is kept to go back to.",
    "After every node its users are looked at: the configs of the Configs section that ride it and were up before must pass again in two fresh tests of the probe. One that does not means the new version does not serve them: the old Xray and the old image come back, and the run stops there.",
    "Then the panel on its own server: its image, its compose files and a dump of its database are kept, the new image is pulled and the panel service alone is recreated. After it the panel must answer, sign in, answer at its subscription front door and have every node connected again; its service is restarted once when it does not answer, and then the old image is put back.",
    `A time budget: no node is started after ${au.budget_minutes || 60} minutes, the panel only in the first 45 minutes of its hour; the rest waits for the next night. The panel is never touched outside its hour.`,
    "A release that did not hold is held back and never tried again by itself (the Safe update buttons still try it).",
    "Every night is a story in the history of the Tunnel Doctor, one line in the feed, and one message on Telegram: what was updated, what came back, what was left, what needs you."];
  showModal("<b>Update automatically every night</b>", `<div class="sbox pg-nodes-plan"><h5><i>🌙</i>What a night does</h5><ol class="pg-nodes-steps">${steps.map(s => `<li>${esc(s)}</li>`).join("")}</ol><div class="pg-nodes-note">A node that is updated restarts: the connections through it drop for about 10-20 seconds, once for its software and once more for Xray. It cannot be made zero - it is made as small as it can be.</div></div>
    <div class="frm-act"><button type="button" class="sm pri" onclick="closeModal(); pgUpdRehearse()" title="Walk tonight's run now without changing anything: what would be updated in which order, what would be checked after each node, what the panel step would do, what is left out and why">Rehearse tonight's run</button><button type="button" class="sm" onclick="closeModal()">Close</button></div>`, "wide");
};
window.pgUpdAuto = async on => {      // the nightly update switch (the owner, 2026-11-03)
  const hh = String(pgu.data && pgu.data.auto && pgu.data.auto.hour != null ? pgu.data.auto.hour : 3).padStart(2, "0");
  try { await api("/api/pg-updates/auto", {method: "POST", body: JSON.stringify({enabled: !!on})}); toast(on ? `✓ PasarGuard updates by itself every night at ${hh}:00 (Iran time)` : "The nightly update is off", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
  pgUpdLoad(true);
};
window.pgUpdRehearse = async () => {      // tonight's run walked now, nothing changed: the story in a window
  try { const r = await api("/api/pg-updates/auto/rehearse", {method: "POST"}); pgUpdFollow("Rehearsal of the nightly update", r.action_id, 3000); } catch (e) { toast("✕ " + e.message, "bad"); }
};
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
