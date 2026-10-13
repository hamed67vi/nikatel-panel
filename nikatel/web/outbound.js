/* The Outbound page (Panels -> Outbound): the pool of exit configs, the Iran servers that carry it, the forwards to the foreign nodes.
   Loaded after app.js and uses its helpers ($, api, esc, state, refresh, toast, showModal, closeModal, gconfirm, followStory, ago). */
const ob = {d: null, paste: "", res: null, busy: ""};
const OB_MODES = [["spread", "Spread", "Every new connection goes through the next alive exit: the load is shared by all of them, a dead exit is skipped by itself."],
  ["fast", "Fastest", "Everything goes through the quickest alive exit."], ["auto", "Direct first", "Direct while the Iran server can reach abroad; the pool only when it cannot."]];

async function obRender(head) {
  const d = await api("/api/outbound"); ob.d = d;
  const dot = c => !c.measured ? "unk" : c.alive === c.measured ? "ok" : c.alive ? "warn" : "bad";
  const cfg = c => `<div class="ob2-row ${c.enabled ? "" : "off"}"><i class="ob2-dot ${dot(c)}" title="${esc(c.measured ? c.alive + " of " + c.measured + " Iran server(s) reach it" : "not measured yet")}"></i>
      <span class="ob2-nm"><b title="${esc(c.note || c.info)}">${esc(c.note || c.info)}</b><small class="mut">${esc(c.info)}</small></span><span class="ob2-ex mut">${c.exit ? esc((c.exit.country || "") + " " + (c.exit.ip || "")) : ""}</span><span class="ob2-ms">${c.ms != null ? c.ms + " ms" : "—"}</span>
      <label class="srv-sw ob2-sw ${c.enabled ? "on" : ""}" title="${c.enabled ? "In use" : "Switched off: not used"}"><input type="checkbox" ${c.enabled ? "checked" : ""} ${d.read_only ? "disabled" : ""} onchange="obCfg('${c.id}', {enabled: this.checked})"><i></i></label><button class="sm danger" ${d.read_only ? "disabled" : ""} onclick="obCfgDel('${c.id}')" title="Remove this exit">✕</button></div>`;
  const VERDICT = {ok: ["ok", "all exits answer"], degraded: ["warn", "some exits are down"], down: ["err", "no exit answers"], unknown: ["", "not measured yet"], off: ["", "not used"]};
  const srv = s => { const v = VERDICT[s.verdict] || VERDICT.unknown, sm = s.summary;
    return `<div class="ob2-row ob2-srv ${s.chosen ? "" : "off"}"><label class="chk ob2-pick" title="Let this Iran server carry the outbound"><input type="checkbox" ${s.chosen ? "checked" : ""} ${d.read_only ? "disabled" : ""} onchange="obPick()" value="${s.id}"><b>${esc(s.name)}</b></label>
      <span class="pill ${v[0]}" title="${esc(sm && sm.error ? sm.error : sm ? sm.alive + " of " + sm.total + " exits answer" + (sm.ms != null ? " · " + sm.ms + " ms as users see it" : "") : "")}">${v[1]}</span>
      <small class="mut ob2-h">${esc(s.host)}${s.installed ? " · Xray" : ""}${s.checked_at ? " · " + ago(s.checked_at) + " ago" : ""}</small>${s.installed ? `<button class="sm" onclick="obRemoveSrv(${s.id}, '${esc(s.name)}')" title="Take the outbound off this server">Remove</button>` : "<span></span>"}</div>`; };
  const rt = r => `<div class="ob2-row ${r.enabled ? "" : "off"}"><span class="ob2-port mono">:${r.listen}</span><span class="ob2-nm"><b class="mono">${esc(r.target_host)}:${r.target_port}</b>${r.name && r.name !== r.target_host + ":" + r.target_port ? `<small class="mut">${esc(r.name)}</small>` : ""}</span><span></span>
      <label class="srv-sw ob2-sw ${r.enabled ? "on" : ""}"><input type="checkbox" ${r.enabled ? "checked" : ""} ${d.read_only ? "disabled" : ""} onchange="obRoute('${r.id}', this.checked)"><i></i></label><button class="sm danger" ${d.read_only ? "disabled" : ""} onclick="obRouteDel('${r.id}')">✕</button></div>`;
  const chosen = d.servers.filter(s => s.chosen), worst = chosen.some(s => s.verdict === "down") ? "down" : chosen.some(s => s.verdict === "degraded") ? "degraded" : chosen.length && chosen.every(s => s.verdict === "ok") ? "ok" : "unknown";
  const top = VERDICT[chosen.length ? worst : "off"];
  const res = ob.res ? `<div class="ob2-res">${ob.res.added.length ? `<span class="gw-ok">✓ ${ob.res.added.length} added</span>` : ""}${ob.res.skipped.map(x => `<span class="err" title="${esc(x.link)}">${esc(x.link)} — ${esc(x.why)}</span>`).join("")}</div>` : "";
  $("#page").innerHTML = `${head}<div class="toolbar ob2-top"><div class="tabs ob2-modes">${OB_MODES.map(([k, l, tip]) => `<button class="${d.mode === k ? "on" : ""}" ${d.read_only ? "disabled" : ""} onclick="obMode('${k}')" title="${esc(tip)}">${l}</button>`).join("")}</div><span class="pill ${top[0]}" title="What the Iran servers that carry the outbound see">${chosen.length ? top[1] : "no Iran server chosen"}</span><span class="sp"></span>
      <button onclick="obCheck()" ${ob.busy ? "disabled" : ""} title="Look at every exit from the chosen Iran servers now">⚡ Check now</button><button class="pri" onclick="obApply()" ${d.read_only || ob.busy ? "disabled" : ""} title="Put the pool and the forwards on every chosen Iran server (Xray is installed where it is missing)">Apply to the servers</button></div>
    ${d.read_only ? '<div class="gw-out bad">The panel is read-only: you can look, not change. Set NIKATEL_READ_ONLY=false on the panel server to change the outbound.</div>' : ""}
    <div class="ob2-grid">
      <div class="card ob2-card"><h3>Exits <span class="pill">${d.configs.filter(c => c.enabled).length} of ${d.configs.length}</span><span class="sp"></span><small class="mut">up to ${d.limits.configs}</small></h3><div class="ob2-list">${d.configs.map(cfg).join("") || '<div class="tk-empty"><b>No exit yet</b><span>Paste the configs that still have internet; the panel shares the load between them.</span></div>'}</div>
        <div class="ob2-add"><textarea id="ob-paste" rows="3" placeholder="vless://…  vmess://…  trojan://…  ss://…   (one per line)" spellcheck="false" ${d.read_only ? "disabled" : ""} oninput="ob.paste = this.value">${esc(ob.paste)}</textarea><button class="pri sm" ${d.read_only ? "disabled" : ""} onclick="obAddCfg()">Add</button></div>${res}</div>
      <div class="card ob2-card"><h3>Iran servers <span class="pill">${chosen.length}</span><span class="sp"></span><small class="mut">they carry the outbound</small></h3><div class="ob2-list">${d.servers.map(srv).join("") || '<div class="tk-empty"><b>No Iran server</b></div>'}</div>
        <small class="mut ob2-note">${d.binary ? "Xray is copied to a server the first time you apply (about 35 MB over SSH)." : "The panel has no Xray binary cached: run scripts/fetch-xray.sh on the panel server first."}</small></div>
      <div class="card ob2-card"><h3>Forwards <span class="pill">${d.routes.filter(r => r.enabled).length} of ${d.routes.length}</span><span class="sp"></span><button class="sm" ${d.read_only ? "disabled" : ""} onclick="obSuggest()" title="Forwards for the nodes and ports of your Marzban / PasarGuard panels">＋ From the panels</button></h3><div class="ob2-list">${d.routes.map(rt).join("") || '<div class="tk-empty"><b>No forward yet</b><span>A forward is a port on the Iran server that leads to a foreign node: users connect to it.</span></div>'}</div>
        <div class="ob2-add one"><input id="ob-h" placeholder="node address" autocomplete="off" ${d.read_only ? "disabled" : ""}><input id="ob-p" type="number" min="1" max="65535" placeholder="port" ${d.read_only ? "disabled" : ""}><button class="sm" ${d.read_only ? "disabled" : ""} onclick="obAddRoute()">Add</button></div></div>
    </div>`;
}

const obDo = async (fn, ok) => { try { const r = await fn(); if (ok) toast(ok, "ok"); refresh(); return r; } catch (e) { toast("✕ " + e.message, "bad"); refresh(); } };
window.obMode = k => obDo(() => api("/api/outbound/mode", {method: "PUT", body: JSON.stringify({mode: k})}), "✓ Mode saved - press Apply to put it on the servers");
window.obCfg = (id, body) => obDo(() => api("/api/outbound/configs/" + id, {method: "PATCH", body: JSON.stringify(body)}));
window.obCfgDel = async id => { if (!await gconfirm("Remove this exit from the pool?")) return; obDo(() => api("/api/outbound/configs/" + id, {method: "DELETE"})); };
window.obAddCfg = async () => { const text = ($("#ob-paste") || {}).value || ""; if (!text.trim()) { toast("Paste at least one link first", "bad"); return; }
  try { ob.res = await api("/api/outbound/configs", {method: "POST", body: JSON.stringify({text})}); ob.paste = ob.res.skipped.length ? text : ""; if (ob.res.added.length && !ob.res.skipped.length) ob.res = null; toast(ob.res ? "Some links were skipped" : "✓ Added", ob.res ? "bad" : "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } refresh(); };
window.obPick = () => { const ids = [...document.querySelectorAll(".ob2-pick input:checked")].map(x => +x.value); obDo(() => api("/api/outbound/gateways", {method: "PUT", body: JSON.stringify({server_ids: ids})}), "✓ Saved - press Apply to put the outbound on these servers"); };
window.obRoute = (id, on) => obDo(() => api("/api/outbound/routes/" + id, {method: "PATCH", body: JSON.stringify({enabled: on})}));
window.obRouteDel = async id => { if (!await gconfirm("Remove this forward? Users whose link goes through its port lose it once the change is applied.", {danger: true})) return; obDo(() => api("/api/outbound/routes/drop", {method: "POST", body: JSON.stringify({ids: [id]})})); };
window.obAddRoute = () => { const h = $("#ob-h").value.trim(), p = +$("#ob-p").value; if (!h || !(p >= 1)) { toast("Fill the node address and the port", "bad"); return; } obDo(() => api("/api/outbound/routes", {method: "POST", body: JSON.stringify({routes: [{target_host: h, target_port: p}]})}), "✓ Forward added - press Apply"); };
window.obRemoveSrv = async (id, name) => { if (!await gconfirm(`Take the outbound off ${name}? Xray is stopped and removed there; the pool and the forwards stay in the panel.`, {danger: true})) return; obDo(() => api(`/api/outbound/servers/${id}/remove`, {method: "POST"}), "✓ Removed from " + name); };
window.obCheck = async () => { ob.busy = "check"; toast("Looking at every exit from the Iran servers…"); try { await api("/api/outbound/check", {method: "POST"}); toast("✓ Checked", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } ob.busy = ""; refresh(); };
window.obApply = async () => {
  const d = ob.d, n = d.servers.filter(s => s.chosen).length;
  if (!n) { toast("Choose an Iran server first", "bad"); return; }
  if (!await gconfirm(`Put ${d.enabled_configs} exit(s) and ${d.routes.filter(r => r.enabled).length} forward(s) on ${n} Iran server(s)? Xray is installed where it is missing. Users are not touched until their links point at the forward ports.`)) return;
  try { const r = await api("/api/outbound/apply", {method: "POST"}); showModal("<b>🌉 Outbound</b>", '<div id="ap-story" class="fix"><div class="fx-line run"><i></i>Starting…</div></div>', "wide"); followStory(r.id, $("#ap-story"), 600).then(() => refresh()); } catch (e) { toast("✕ " + e.message, "bad"); }
};
/* forwards from the panels: every chosen node x every chosen port */
window.obSuggest = async () => {
  showModal("<b>＋ Forwards from the panels</b>", '<div class="loading"><i class="spin"></i><span>Asking the panels for their nodes and ports…</span></div>', "wide");
  let s; try { s = await api("/api/outbound/suggest"); } catch (e) { showModal("<b>＋ Forwards from the panels</b>", `<div class="err">${esc(e.message)}</div>`); return; }
  const blk = p => p.error ? `<div class="ob2-sg"><h5>${esc(p.panel)}</h5><div class="err">${esc(p.error)}</div></div>` : `<div class="ob2-sg"><h5>${esc(p.panel)} <small class="mut">${esc(p.type)}</small></h5>
      <div class="ob2-sgl"><b>Nodes</b>${p.nodes.map(n => `<label class="chk"><input type="checkbox" class="sg-n" value="${esc(n.address)}" checked onchange="obSugCount()"><span>${esc(n.name)}</span><small class="mut">${esc(n.address)}${n.routed ? " · " + n.routed + " forward(s)" : ""}</small></label>`).join("") || '<span class="mut">no node</span>'}</div>
      <div class="ob2-sgl"><b>Ports</b>${p.ports.map(x => `<label class="chk" title="${esc(p.inbounds.filter(i => +i.port === x).map(i => i.tag).join(", "))}"><input type="checkbox" class="sg-p" value="${x}" onchange="obSugCount()"><span class="mono">${x}</span></label>`).join("") || '<span class="mut">no inbound port</span>'}</div></div>`;
  showModal("<b>＋ Forwards from the panels</b>", `<div class="ob2-sgs">${s.panels.map(blk).join("") || '<div class="mut">No panel yet.</div>'}</div><small class="mut">A forward is made for every ticked node on every ticked port; the same node and port twice is one forward. Tick only the ports your users connect to.</small>
    <div class="frm-act"><span id="sg-count" class="mut"></span><span class="sp"></span><button class="pri" onclick="obSugMake()">Make the forwards</button><button onclick="closeModal()">Cancel</button></div>`, "wide");
  obSugCount();
};
window.obSugCount = () => { const a = new Set([...document.querySelectorAll(".sg-n:checked")].map(x => x.value)), p = new Set([...document.querySelectorAll(".sg-p:checked")].map(x => x.value)), el = $("#sg-count"); if (el) el.textContent = `${a.size} node(s) × ${p.size} port(s) = ${a.size * p.size} forward(s)`; };
window.obSugMake = async () => {
  const addresses = [...new Set([...document.querySelectorAll(".sg-n:checked")].map(x => x.value))], ports = [...new Set([...document.querySelectorAll(".sg-p:checked")].map(x => +x.value))];
  if (!addresses.length || !ports.length) { toast("Tick at least one node and one port", "bad"); return; }
  try { const r = await api("/api/outbound/routes/auto", {method: "POST", body: JSON.stringify({addresses, ports})}); closeModal(); toast(`✓ ${r.added} forward(s) made${r.skipped ? `, ${r.skipped} already existed` : ""} - press Apply`, "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
