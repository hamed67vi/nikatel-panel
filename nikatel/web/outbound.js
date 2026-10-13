/* The Outbound page (Tunnels -> Outbound), one page, three steps: 1 your configs, 2 tunnels (Iran servers x foreign servers x ports, made through the configs), 3 the addresses to put behind the CDN (by hand).
   Everything else is under "Advanced". Loaded after app.js and uses its helpers ($, api, esc, state, refresh, toast, showModal, closeModal, gconfirm, followStory, ago). */
const ob = {d: null, paste: "", res: null, busy: "", adv: false, rec: false, plan: null, q: {iran: "", foreign: ""}, only: {iran: false, foreign: false}, sort: {iran: ["name", 1], foreign: ["name", 1]}};
const OB_MODES = [["spread", "Spread", "Every new connection goes through the next alive exit: the load is shared by all of them, a dead exit is skipped by itself."],
  ["fast", "Fastest", "Everything goes through the quickest alive exit."], ["auto", "Direct first", "Direct while the Iran server can reach abroad; the pool only when it cannot."]];

/* line icons of the page */
const OBI = {
  server: '<svg viewBox="0 0 24 24"><rect x="3.5" y="3.5" width="17" height="7" rx="2"/><rect x="3.5" y="13.5" width="17" height="7" rx="2"/><path d="M7 7h.01M7 17h.01M11 7h6M11 17h6"/></svg>',
  globe: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.6 2.4 3.9 5.2 3.9 8.5s-1.3 6.1-3.9 8.5c-2.6-2.4-3.9-5.2-3.9-8.5S9.4 5.9 12 3.5z"/></svg>',
  search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/></svg>',
  filter: '<svg viewBox="0 0 24 24"><path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2.2"/><circle cx="9" cy="17" r="2.2"/></svg>',
  link: '<svg viewBox="0 0 24 24"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/></svg>',
  play: '<svg viewBox="0 0 24 24" class="fill"><path d="M7 4.5v15l12-7.5z"/></svg>',
  bolt: '<svg viewBox="0 0 24 24"><path d="M13 3L5 13.5h6L10 21l8-10.5h-6z"/></svg>',
  gear: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 3.5v2.2M12 18.3v2.2M3.5 12h2.2M18.3 12h2.2M6 6l1.6 1.6M16.4 16.4L18 18M6 18l1.6-1.6M16.4 7.6L18 6"/></svg>',
  stack: '<svg viewBox="0 0 24 24"><ellipse cx="12" cy="6" rx="7.5" ry="2.8"/><path d="M4.5 6v6c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8V6M4.5 12v6c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8v-6"/></svg>',
  info: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.8h.01"/></svg>',
  chev: '<svg viewBox="0 0 24 24"><path d="M9 5l7 7-7 7"/></svg>',
  copy: '<svg viewBox="0 0 24 24"><rect x="8.5" y="8.5" width="11" height="11" rx="2.2"/><path d="M15.5 8.5V6.7a2.2 2.2 0 0 0-2.2-2.2H6.7a2.2 2.2 0 0 0-2.2 2.2v6.6a2.2 2.2 0 0 0 2.2 2.2h1.8"/></svg>',
  dots: '<svg viewBox="0 0 24 24" class="fill"><circle cx="5.5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="18.5" cy="12" r="1.6"/></svg>',
  up: '<svg viewBox="0 0 24 24"><path d="M7 14l5-5 5 5"/></svg>', down: '<svg viewBox="0 0 24 24"><path d="M7 10l5 5 5-5"/></svg>', both: '<svg viewBox="0 0 24 24"><path d="M8 9l4-4 4 4M8 15l4 4 4-4"/></svg>',
  split: '<svg viewBox="0 0 24 24"><path d="M3.5 12h17M7 8.5L3.5 12 7 15.5M17 8.5l3.5 3.5-3.5 3.5"/></svg>',
};
const obIp = h => String(h).split(".").map(x => String(+x || 0).padStart(5, "0")).join(".");       // every part is padded to five digits, so an address with a short first part sorts before a longer one
/* the servers of one list as the search, the filter and the sort of its header show them */
function obList(kind) {
  const d = ob.d, q = (ob.q[kind] || "").trim().toLowerCase(), [key, dir] = ob.sort[kind];
  return (kind === "iran" ? d.servers : d.foreign).filter(s => (!q || (s.name + " " + s.host).toLowerCase().includes(q)) && (!ob.only[kind] || s.chosen))
    .sort((a, b) => dir * (key === "host" ? obIp(a.host).localeCompare(obIp(b.host)) : a.name.localeCompare(b.name, undefined, {numeric: true, sensitivity: "base"})));
}
function obRowsHtml(kind) {
  const d = ob.d, ro = d.read_only, rows = obList(kind), hubs = new Set(d.hubs.map(h => h.server_id));
  const row = s => `<tr class="${s.chosen ? "sel" : ""} clk" onclick="obTick(event, this)"><td><label class="srv-sw ob2-sw ${s.chosen ? "on" : ""}"><input type="checkbox" ${s.chosen ? "checked" : ""} ${ro ? "disabled" : ""} onchange="obSet('${kind}', [${s.id}], this.checked)"><i></i></label></td>
      <td class="name"><i class="ob2-dot ${s.online === false ? "bad" : s.online ? "ok" : "unk"}" title="${s.online === false ? "offline" : s.online ? "online" : "not checked yet"}"></i><b>${esc(s.name)}</b>${hubs.has(s.id) ? '<span class="pill ok">relay</span>' : s.installed ? '<span class="pill" title="Xray is installed on this server">Xray</span>' : ""}</td>
      <td class="mono mut">${esc(s.host)}</td><td class="ob5-dots"><button class="ob5-more" onclick="obMenu(event, '${kind}', ${s.id})" title="More">${OBI.dots}</button></td></tr>`;
  return rows.map(row).join("") || `<tr><td colspan="4" class="mut ob5-none">${(ob.q[kind] || ob.only[kind]) ? "No server matches." : kind === "iran" ? "No Iran server." : "No foreign server."}</td></tr>`;
}
const obSortIc = (kind, key) => { const [k, dir] = ob.sort[kind]; return k === key ? (dir > 0 ? OBI.up : OBI.down) : OBI.both; };
function obPane(kind) {
  const d = ob.d, ro = d.read_only, list = kind === "iran" ? d.servers : d.foreign, vis = obList(kind), all = vis.length && vis.every(s => s.chosen);
  return `<div class="ob5-pane"><div class="ob5-ph"><i class="ob5-pi">${kind === "iran" ? OBI.server : OBI.globe}</i><b>${kind === "iran" ? "From" : "To"}</b><span class="mut">${kind === "iran" ? "Iran servers" : "foreign servers"}</span></div>
    <div class="ob5-search"><i>${OBI.search}</i><input type="search" placeholder="Search server or address..." value="${esc(ob.q[kind])}" oninput="obFind('${kind}', this.value)" autocomplete="off" spellcheck="false"><button class="${ob.only[kind] ? "on" : ""}" onclick="obOnly('${kind}')" title="${ob.only[kind] ? "Showing only the ticked servers: click to show all" : "Show only the ticked servers"}">${OBI.filter}</button></div>
    <div class="lbox full ob4-box"><table class="tbl lift"><colgroup><col style="width:64px"><col><col style="width:118px"><col style="width:32px"></colgroup>
      <thead><tr><th><label class="srv-sw ob2-sw ${all ? "on" : ""}" title="Switch on every server shown"><input type="checkbox" ${all ? "checked" : ""} ${ro || !vis.length ? "disabled" : ""} onchange="obAll('${kind}', this.checked)"><i></i></label></th><th class="srt" onclick="obSort('${kind}', 'name')">Server<i>${obSortIc(kind, "name")}</i></th><th class="srt" onclick="obSort('${kind}', 'host')">Address<i>${obSortIc(kind, "host")}</i></th><th></th></tr></thead>
      <tbody id="ob-tb-${kind}">${obRowsHtml(kind)}</tbody></table></div></div>`;
}
window.obFind = (kind, v) => { ob.q[kind] = v; const tb = $("#ob-tb-" + kind); if (tb) tb.innerHTML = obRowsHtml(kind); };
window.obOnly = kind => { ob.only[kind] = !ob.only[kind]; const p = $("#ob-pane-" + kind); if (p) p.innerHTML = obPane(kind); };
window.obSort = (kind, key) => { const [k, dir] = ob.sort[kind]; ob.sort[kind] = [key, k === key ? -dir : 1]; const p = $("#ob-pane-" + kind); if (p) p.innerHTML = obPane(kind); };
window.obAll = (kind, on) => obSet(kind, obList(kind).map(s => s.id), on);
/* the ticked Iran servers / foreign servers are kept as a list of ids: a search that hides a ticked server never unticks it */
window.obSet = (kind, ids, on) => {
  const d = ob.d, cur = new Set((kind === "iran" ? d.servers : d.foreign).filter(s => s.chosen).map(s => s.id));
  ids.forEach(i => { if (on) cur.add(i); else cur.delete(i); });
  const call = kind === "iran" ? ["/api/outbound/gateways", {server_ids: [...cur]}] : ["/api/outbound/targets", {foreign_ids: [...cur], ports: ($("#ob-ports") || {}).value || ""}];
  obDo(() => api(call[0], {method: "PUT", body: JSON.stringify(call[1])}), "✓ Saved - press Start");
};
window.obTargets = () => obSet("foreign", [], true);
window.obPick = () => obSet("iran", [], true);
/* the "..." menu of a row */
window.obMenuClose = () => { document.querySelectorAll(".ob5-menu").forEach(m => m.remove()); document.removeEventListener("click", obMenuClose, true); };
window.obMenu = (ev, kind, id) => {
  ev.stopPropagation(); obMenuClose();
  const d = ob.d, s = (kind === "iran" ? d.servers : d.foreign).find(x => x.id === id); if (!s) return;
  const items = [["Copy the address", `obCopy('${esc(s.host)}')`]];
  if (kind === "iran" && s.installed) items.push(["Take the outbound off this server", `obRemoveSrv(${s.id}, '${esc(s.name)}')`, "danger"]);
  if (kind === "foreign" && !d.hubs.some(h => h.server_id === id) && !d.read_only) items.push(["Use as the relay", `obHubUse(${id})`]);
  const b = ev.currentTarget.getBoundingClientRect(), m = document.createElement("div");
  m.className = "ob5-menu"; m.innerHTML = items.map(([l, fn, cls]) => `<button class="${cls || ""}" onclick="obMenuClose();${fn}">${l}</button>`).join("");
  m.style.top = Math.min(innerHeight - 20 - items.length * 34, b.bottom + 4) + "px"; m.style.left = Math.max(8, b.right - 210) + "px";
  document.body.appendChild(m); setTimeout(() => document.addEventListener("click", obMenuClose, true), 0);
};
window.obHubUse = id => obDo(() => api("/api/outbound/hubs", {method: "PUT", body: JSON.stringify({server_ids: [...ob.d.hubs.map(h => h.server_id), id]})}), "✓ Relay chosen - press Start to install it");

async function obRender(head) {
  const d = await api("/api/outbound"); ob.d = d;
  const dot = c => !c.measured ? "unk" : c.alive === c.measured ? "ok" : c.alive ? "warn" : "bad";
  const cfg = c => `<div class="ob2-row ${c.enabled ? "" : "off"}"><i class="ob2-dot ${dot(c)}" title="${esc(c.measured ? c.alive + " of " + c.measured + " Iran server(s) reach it" : "not measured yet")}"></i>
      <span class="ob2-nm"><b title="${esc(c.note || c.info)}">${esc(c.note || c.info)}</b><small class="mut" title="${esc(c.exit ? (c.exit.country || "") + " " + (c.exit.ip || "") : "")}">${esc(c.info)}${c.exit ? " · " + esc((c.exit.country || "") + " " + (c.exit.ip || "")) : ""}</small></span><span class="ob2-ms">${c.ms != null ? c.ms + " ms" : "—"}</span>
      <label class="srv-sw ob2-sw ${c.enabled ? "on" : ""}" title="${c.enabled ? "In use" : "Switched off: not used"}"><input type="checkbox" ${c.enabled ? "checked" : ""} ${d.read_only ? "disabled" : ""} onchange="obCfg('${c.id}', {enabled: this.checked})"><i></i></label><button class="sm danger" ${d.read_only ? "disabled" : ""} onclick="obCfgDel('${c.id}')" title="Remove this exit">✕</button></div>`;
  const VERDICT = {ok: ["ok", "all exits answer"], degraded: ["warn", "some exits are down"], down: ["err", "no exit answers"], unknown: ["", "not measured yet"], off: ["", "not used"]};
  const chosen = d.servers.filter(s => s.chosen), worst = chosen.some(s => s.verdict === "down") ? "down" : chosen.some(s => s.verdict === "degraded") ? "degraded" : chosen.length && chosen.every(s => s.verdict === "ok") ? "ok" : "unknown";
  const top = VERDICT[chosen.length ? worst : "off"], ro = d.read_only, fchosen = d.foreign.filter(f => f.chosen);
  const res = ob.res ? `<div class="ob2-res">${ob.res.added.length ? `<span class="gw-ok">✓ ${ob.res.added.length} added</span>` : ""}${ob.res.skipped.map(x => `<span class="err" title="${esc(x.link)}">${esc(x.link)} — ${esc(x.why)}</span>`).join("")}</div>` : "";
  const step = (cls, n, title, sub, body, right = "") => `<section class="card ob3-step ${cls}"><h3><i class="ob3-n">${n}</i><b>${title}</b><small class="mut">${sub}</small><span class="sp"></span>${right}</h3>${body}</section>`;
  const hub = d.hubs[0], sug = d.suggested_relay;
  /* the relay is ONE line: its name, the server, and (when none is chosen yet) the choice; the explanation is the tooltip */
  const relayTip = "The relay is a foreign server behind the exits: the traffic is wrapped on its way out of the exits, so an exit cannot read it or send it somewhere else." + (hub ? "" : ` Start picks the quietest foreign server (now ${sug ? sug.name : "none"}) unless you choose one.`);
  const relayBody = hub ? obRelayChip(d, hub)
    : `<span class="pill ok">${sug ? "chosen for you: " + esc(sug.name) : "none available"}</span>${d.foreign.length ? `<select id="ob-hub" ${ro ? "disabled" : ""}><option value="">choose myself…</option>${d.foreign.map(f => `<option value="${f.id}">${esc(f.name)} - ${esc(f.host)}</option>`).join("")}</select><button class="sm" ${ro ? "disabled" : ""} onclick="obHubAdd()">Use</button>` : ""}`;
  const rt = r => `<div class="ob2-row ${r.enabled ? "" : "off"}"><span class="ob2-port mono">:${r.listen}</span><span class="ob2-nm"><b class="mono">${esc(r.target_host)}:${r.target_port}</b>${r.name && r.name !== r.target_host + ":" + r.target_port ? `<small class="mut">${esc(r.name)}</small>` : ""}</span><span></span>
      <label class="srv-sw ob2-sw ${r.enabled ? "on" : ""}"><input type="checkbox" ${r.enabled ? "checked" : ""} ${ro ? "disabled" : ""} onchange="obRoute('${r.id}', this.checked)"><i></i></label><button class="sm danger" ${ro ? "disabled" : ""} onclick="obRouteDel('${r.id}')">✕</button></div>`;
  const sv = id => d.servers.find(s => s.id === id), fname = host => (d.foreign.find(f => f.host === host) || {}).name || host;
  const paths = d.routes.filter(r => r.source === "path" && r.enabled);
  const path = r => { const s = sv(r.server_id) || {name: "?", host: "", installed: false}, addr = s.host + ":" + r.listen;
    return `<tr><td><span class="ob3-addr"><i class="ob2-dot ${s.installed ? "ok" : "unk"}" title="${s.installed ? "on the server" : "press Start"}"></i><b class="mono">${esc(addr)}</b><button class="ob5-copy" onclick="obCopy('${esc(addr)}')" title="Copy the address">${OBI.copy}</button></span></td><td class="name" title="${esc(s.host)} port ${r.listen} reaches ${esc(r.target_host)}:${r.target_port} through your configs"><b>${esc(s.name)} → ${esc(fname(r.target_host))}</b> <span class="mono mut">:${r.target_port}</span></td></tr>`; };
  const ports = (d.ports || []).join(", "), sugP = (d.default_ports || []).join(", ");
  const n = chosen.length * fchosen.length * (d.ports || []).length;
  const s1 = step("ob4-s1", 1, "Your configs", "the ones that still have internet", `${d.configs.length ? `<div class="ob2-list ob3-list">${d.configs.map(cfg).join("")}</div>` : ""}
      <div class="ob5-paste"><textarea id="ob-paste" rows="1" placeholder="Paste your configs here, one per line: vless://  vmess://  trojan://  ss://" spellcheck="false" ${ro ? "disabled" : ""} oninput="ob.paste = this.value; obGrow(this)">${esc(ob.paste)}</textarea><button class="pri" ${ro ? "disabled" : ""} onclick="obAddCfg()">＋ Add</button></div>${res}`, `<span class="pill">${d.configs.filter(c => c.enabled).length} of ${d.configs.length}</span>`);
  const s2 = step("ob4-s2", 2, "Tunnels", "from which Iran server to which foreign server, on which ports", `<div class="ob5-two"><div id="ob-pane-iran">${obPane("iran")}</div><div class="ob5-div"><span></span><i>${OBI.split}</i><span></span></div><div id="ob-pane-foreign">${obPane("foreign")}</div></div>
      <div class="ob4-foot ob5-foot"><div class="ob4-ports"><div class="ob5-lbl"><i>${OBI.link}</i><b>Ports</b><span class="mut">the ports of the foreign servers your users connect to</span></div><div class="ob5-ports"><input id="ob-ports" value="${esc(ports)}" placeholder="${esc(sugP || "8075, 80")}" autocomplete="off" ${ro ? "disabled" : ""} onchange="obTargets()">${sugP && sugP !== ports ? `<button ${ro ? "disabled" : ""} onclick="obUsePorts('${esc(sugP)}')" title="The ports the tunnels of the chosen Iran servers serve today">Use ${esc(sugP)}</button>` : ""}</div></div>
        <div class="ob4-go"><button class="pri ob3-start" onclick="obApply()" ${ro || ob.busy ? "disabled" : ""} title="The tunnels, the relay and Xray on the servers: everything is made and checked"><i class="ob5-bi">${OBI.play}</i>Start</button><button onclick="obCheck()" ${ob.busy ? "disabled" : ""} title="Look at every exit from the chosen Iran servers now"><i class="ob5-bi">${OBI.bolt}</i>Check now</button></div></div>
      <small class="mut ob3-hint">${chosen.length && fchosen.length && ports ? `Start makes ${n} tunnel(s): ${chosen.length} Iran × ${fchosen.length} foreign × ${(d.ports || []).length} port(s).` : chosen.length ? "Choose the foreign servers and the ports. With none chosen, Start uses the foreign servers and ports of the Iran servers' own tunnels." : "Tick at least one Iran server."} ${d.binary ? "" : "The panel has no Xray binary cached: run scripts/fetch-xray.sh on the panel server first."} No CDN record is touched.</small>`, `<span class="pill ${top[0]}">${chosen.length ? top[1] : "not started"}</span><span class="pill">${chosen.length} – ${fchosen.length}</span>`);
  const s3 = step("ob4-s3", 3, "Behind the CDN", "put these addresses there by hand", `${paths.length ? `<div class="lbox full ob4-pbox"><table class="tbl lift"><colgroup><col style="width:62%"><col></colgroup><thead><tr><th>Address</th><th>Tunnel</th></tr></thead><tbody>${paths.map(path).join("")}</tbody></table></div>` : `<div class="ob5-note"><i>${OBI.info}</i><span>The addresses appear here after Start: an Iran server address and a port for every tunnel.</span></div>`}
      ${paths.length ? "" : `<small class="mut ob3-hint">In your CDN record (Tunnels → CDN) the address is the Iran server and the origin port is the number after the colon. The panel does not change a record by itself.</small>`}`, `<span class="pill" title="In your CDN record (Tunnels → CDN) the address is the Iran server and the origin port is the number after the colon. The panel does not change a record by itself.">${paths.length}</span>`);
  const s4 = `<section class="card ob3-step ob4-s4 ob5-line" title="${esc(relayTip)}"><i class="ob3-n">${OBI.globe}</i><b>Relay</b>${relayBody}</section>`;
  const adv = `<details class="card ob5-bar" ${ob.adv ? "open" : ""} ontoggle="ob.adv = this.open"><summary><i class="ob5-chev">${OBI.chev}</i><i class="ob5-bi">${OBI.gear}</i><b>Advanced</b><small class="mut">how the exits are used, every forward</small></summary>
      <div class="ob5-bbody"><div class="ob3-advrow"><b>How the exits are used</b><div class="tabs ob2-modes">${OB_MODES.map(([k, l, tip]) => `<button class="${d.mode === k ? "on" : ""}" ${ro ? "disabled" : ""} onclick="obMode('${k}')" title="${esc(tip)}">${l}</button>`).join("")}</div></div>
      <div class="ob3-advrow"><b>All forwards</b><span class="pill">${d.routes.filter(r => r.enabled).length} of ${d.routes.length}</span><span class="sp"></span><button class="sm" ${ro ? "disabled" : ""} onclick="obFromTunnels()" title="Forwards for every port of every tunnel of every Iran server">＋ From the tunnels</button><button class="sm" ${ro ? "disabled" : ""} onclick="obSuggest()" title="Forwards for the nodes and ports of your Marzban / PasarGuard panels">＋ From the panels</button></div>
      <div class="ob2-list ob3-list">${d.routes.map(rt).join("") || '<div class="mut ob3-empty">No forward yet.</div>'}</div>
      <div class="ob2-add one"><input id="ob-h" placeholder="node address" autocomplete="off" ${ro ? "disabled" : ""}><input id="ob-p" type="number" min="1" max="65535" placeholder="port" ${ro ? "disabled" : ""}><button class="sm" ${ro ? "disabled" : ""} onclick="obAddRoute()">Add a forward by hand</button></div>
      ${d.servers.filter(s => s.installed).map(s => `<div class="ob3-advrow"><b>${esc(s.name)}</b><span class="mut">Xray installed</span><span class="sp"></span><button class="sm danger" onclick="obRemoveSrv(${s.id}, '${esc(s.name)}')">Take the outbound off</button></div>`).join("")}</div></details>
    <details id="ob-rec" class="card ob5-bar" ${ob.rec ? "open" : ""} ontoggle="obRecToggle(this)"><summary><i class="ob5-chev">${OBI.chev}</i><i class="ob5-bi">${OBI.stack}</i><b>CDN records that go through a tunnel</b><small class="mut">move them to the pool by hand</small></summary><div id="ob-rec-body" class="ob5-bbody"></div></details>`;
  $("#page").innerHTML = `${head}${ro ? '<div class="gw-out bad">The panel is read-only: you can look, not change. Set NIKATEL_READ_ONLY=false on the panel server to change the outbound.</div>' : ""}<div class="ob4"><div class="ob4-col ob4-left">${s1}${s3}${s4}${adv}</div><div class="ob4-col ob4-right">${s2}</div></div>`;
  if (ob.rec) { const el = $("#ob-rec"); if (el) { el.dataset.loaded = "1"; obRecords(false); } }
}

/* a click anywhere on a server's row ticks it */
window.obTick = (ev, tr) => { if (ev.target.closest("input, .srv-sw, button, a")) return; const c = tr.querySelector("input[type=checkbox]"); if (c && !c.disabled) { c.checked = !c.checked; c.dispatchEvent(new Event("change", {bubbles: true})); } };
/* the paste box is one line high and grows (up to six lines) when something is pasted */
window.obGrow = el => { el.style.height = "44px"; el.style.height = Math.min(el.scrollHeight, 150) + "px"; };
window.obCopy = t => { const done = () => toast("✓ Copied " + t, "ok"); if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(t).then(done, () => toast("✕ The browser did not allow the copy", "bad")); else toast("✕ The browser did not allow the copy", "bad"); };
window.obUsePorts = p => { const i = $("#ob-ports"); if (i) { i.value = p; obTargets(); } };
const obRelayChip = (d, h) => `<span class="ob2-hub ${h.installed && h.active !== false ? "ok" : h.installed ? "bad" : ""}" title="${esc(h.host + ":" + h.port)}${h.installed ? (h.active === false ? " - not running" : " - running") : " - applied with the next Start"}"><i class="ob2-dot ${h.installed ? (h.active === false ? "bad" : "ok") : "unk"}"></i><b>${esc(h.name)}</b><small class="mono">:${h.port}</small><button class="sm danger" ${d.read_only ? "disabled" : ""} onclick="obHubDel(${h.server_id}, '${esc(h.name)}')" title="Change the relay: take it off this server">✕</button></span>`;
/* the relay: a foreign server behind the exits (Shadowsocks), so that an exit cannot read the Host / SNI of the traffic and dial that name instead of the node */
window.obHubAdd = () => { const sel = $("#ob-hub"); if (!sel || !sel.value) { toast("Choose a server first", "bad"); return; } obDo(() => api("/api/outbound/hubs", {method: "PUT", body: JSON.stringify({server_ids: [...ob.d.hubs.map(h => h.server_id), +sel.value]})}), "✓ Relay chosen - press Start to install it"); };
window.obHubDel = async (id, name) => { if (!await gconfirm(`Take the relay off ${name}? The exits then carry the traffic without it until another relay is applied.`, {danger: true})) return; obDo(() => api(`/api/outbound/hubs/${id}/remove`, {method: "POST"}), "✓ Relay removed from " + name); };
const obDo = async (fn, ok) => { try { const r = await fn(); if (ok) toast(ok, "ok"); refresh(); return r; } catch (e) { toast("✕ " + e.message, "bad"); refresh(); } };
window.obMode = k => obDo(() => api("/api/outbound/mode", {method: "PUT", body: JSON.stringify({mode: k})}), "✓ Mode saved - press Start to put it on the servers");
window.obCfg = (id, body) => obDo(() => api("/api/outbound/configs/" + id, {method: "PATCH", body: JSON.stringify(body)}));
window.obCfgDel = async id => { if (!await gconfirm("Remove this exit from the pool?")) return; obDo(() => api("/api/outbound/configs/" + id, {method: "DELETE"})); };
window.obAddCfg = async () => { const text = ($("#ob-paste") || {}).value || ""; if (!text.trim()) { toast("Paste at least one link first", "bad"); return; }
  try { ob.res = await api("/api/outbound/configs", {method: "POST", body: JSON.stringify({text})}); ob.paste = ob.res.skipped.length ? text : ""; if (ob.res.added.length && !ob.res.skipped.length) ob.res = null; toast(ob.res ? "Some links were skipped" : "✓ Added", ob.res ? "bad" : "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } refresh(); };
window.obRoute = (id, on) => obDo(() => api("/api/outbound/routes/" + id, {method: "PATCH", body: JSON.stringify({enabled: on})}));
window.obRouteDel = async id => { if (!await gconfirm("Remove this forward? Users whose link goes through its port lose it once the change is applied.", {danger: true})) return; obDo(() => api("/api/outbound/routes/drop", {method: "POST", body: JSON.stringify({ids: [id]})})); };
window.obAddRoute = () => { const h = $("#ob-h").value.trim(), p = +$("#ob-p").value; if (!h || !(p >= 1)) { toast("Fill the node address and the port", "bad"); return; } obDo(() => api("/api/outbound/routes", {method: "POST", body: JSON.stringify({routes: [{target_host: h, target_port: p}]})}), "✓ Forward added - press Start"); };
window.obRemoveSrv = async (id, name) => { if (!await gconfirm(`Take the outbound off ${name}? Xray is stopped and removed there; the pool and the forwards stay in the panel.`, {danger: true})) return; obDo(() => api(`/api/outbound/servers/${id}/remove`, {method: "POST"}), "✓ Removed from " + name); };
window.obCheck = async () => { ob.busy = "check"; toast("Looking at every exit from the Iran servers…"); try { await api("/api/outbound/check", {method: "POST"}); toast("✓ Checked", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } ob.busy = ""; refresh(); };
window.obApply = async () => {
  const d = ob.d, n = d.servers.filter(s => s.chosen).length;
  if (!n) { toast("Choose an Iran server first", "bad"); return; }
  if (!d.enabled_configs) { toast("Add a config first", "bad"); return; }
  const f = d.foreign.filter(x => x.chosen).length, pc = (d.ports || []).length;
  if (!await gconfirm(`Start the outbound on ${n} Iran server(s) with ${d.enabled_configs} config(s)? ${f && pc ? `${n * f * pc} tunnel(s) are made (${n} Iran × ${f} foreign × ${pc} port(s)); a tunnel that is no longer chosen is removed. ` : "The tunnels of the Iran servers get their forwards. "}The relay is picked for you and Xray is installed where it is missing. No CDN record is touched.`)) return;
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
  try { const r = await api("/api/outbound/routes/auto", {method: "POST", body: JSON.stringify({addresses, ports})}); closeModal(); toast(`✓ ${r.added} forward(s) made${r.skipped ? `, ${r.skipped} already existed` : ""} - press Start`, "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
/* the CDN records whose origin port is the port of a tunnel that has a forward: point them at the pool by hand, or back at the tunnel. Loaded only when this part is opened (it reads ArvanCloud). */
window.obFromTunnels = () => obDo(async () => { const r = await api("/api/outbound/routes/tunnels", {method: "POST"}); toast(`✓ ${r.added} forward(s) made${r.updated ? `, ${r.updated} updated` : ""} - press Start`, "ok"); }, "");
window.obRecToggle = el => { ob.rec = el.open; if (el.open && !el.dataset.loaded) { el.dataset.loaded = "1"; obRecords(false); } };
async function obRecords(fresh) {
  const box = $("#ob-rec-body"); if (!box) return;
  box.innerHTML = '<div class="loading"><i class="spin"></i><span>Looking at the records…</span></div>';
  let p; try { p = await api("/api/outbound/connect" + (fresh ? "?fresh=true" : "")); } catch (e) { if ($("#ob-rec-body")) $("#ob-rec-body").innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  if (!$("#ob-rec-body")) return;
  ob.plan = p;
  const ro = ob.d.read_only;
  const row = r => `<tr class="${r.state === "bridged" ? "up" : ""}"><td><input type="checkbox" class="ob-rk" value="${esc(r.key)}" ${ro ? "disabled" : ""} onchange="obRkCount()"></td><td class="name"><b>${esc(r.fqdn)}</b><small class="mut">${esc(r.account_name)}</small></td><td>${esc(r.server)}</td>
    <td class="mono">:${r.iran_port} → :${r.listen}</td><td class="mut">${esc(r.tunnel)} → ${esc(r.target)}</td><td>${r.state === "bridged" ? '<span class="pill ok">through the pool</span>' : r.ready ? '<span class="pill">through the tunnel</span>' : '<span class="pill warn" title="The forward is not on the server yet: press Start">press Start first</span>'}</td></tr>`;
  $("#ob-rec-body").innerHTML = `<div class="toolbar ob2-top"><span class="pill">${p.records.length} record(s)</span><span class="pill ${p.bridged ? "ok" : ""}">${p.bridged} through the pool</span><span class="sp"></span><span id="ob-rk" class="mut"></span><small class="mut" title="The records come from the copy the DNS page keeps; ↻ asks ArvanCloud now">read ${ago(p.cached_at)} ago</small>
      <button onclick="obRecords(true)" title="Ask ArvanCloud again">↻</button><button ${ro ? "disabled" : ""} onclick="obConnect(false)" title="Put the ticked records back on their tunnels">Back to the tunnels</button><button class="pri" ${ro ? "disabled" : ""} onclick="obConnect(true)" title="Point the ticked records at the forwards">Connect to the pool</button></div>
    ${p.errors.length ? `<div class="gw-out bad">${p.errors.map(esc).join(" · ")}</div>` : ""}
    <table class="tbl lift"><colgroup><col style="width:38px"><col style="width:30%"><col style="width:12%"><col style="width:15%"><col><col style="width:16%"></colgroup><thead><tr><th><input type="checkbox" onchange="document.querySelectorAll('.ob-rk:not(:disabled)').forEach(x => { x.checked = this.checked; }); obRkCount()"></th><th>Record</th><th>Iran server</th><th>Origin port</th><th>Tunnel → node</th><th>Now</th></tr></thead>
      <tbody>${p.records.map(row).join("") || `<tr><td colspan="6" class="mut">${p.forwards ? "No ArvanCloud record leads to a tunnel that has a forward." : "No forward from the tunnels yet: press “＋ From the tunnels” under Advanced, then Start."}</td></tr>`}</tbody></table>
    <small class="mut ob2-note">A record is an ArvanCloud name (proxied, one Iran server address) whose origin port is the port of a tunnel. “Connect to the pool” changes only that origin port to the forward's port: your users keep their links, the tunnel stays as it is, and every change is in the DNS log (Tunnels → CDN → Changes) with a Restore. The panel never does this by itself: the automatic switch is off while it is being tested.</small>`;
}
window.obRkCount = () => { const n = document.querySelectorAll(".ob-rk:checked").length, el = $("#ob-rk"); if (el) el.textContent = n ? n + " ticked" : ""; };
window.obConnect = async on => {
  const keys = [...document.querySelectorAll(".ob-rk:checked")].map(x => x.value);
  if (!keys.length) { toast("Tick some records first", "bad"); return; }
  if (!await gconfirm(on ? `Point ${keys.length} record(s) at the forwards? Their users reach the nodes through the pool; the tunnels stay untouched and every change can be put back.` : `Put ${keys.length} record(s) back on their tunnels?`, {danger: on})) return;
  try { const r = await api(on ? "/api/outbound/connect" : "/api/outbound/connect/undo", {method: "POST", body: JSON.stringify({keys})}); showModal(`<b>${on ? "🌉 Connect to the pool" : "↩ Back to the tunnels"}</b>`, '<div id="ap-story" class="fix"><div class="fx-line run"><i></i>Starting…</div></div>', "wide"); followStory(r.id, $("#ap-story"), 400).then(() => refresh()); } catch (e) { toast("✕ " + e.message, "bad"); }
};
