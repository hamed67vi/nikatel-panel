/* PasarGuard: ONE window makes a whole KIND of account (the owner, 2026-10-15: "we only choose the kind of the account and the panel builds it").
   What was three jobs in three tabs - an inbound in Core, a group in Access, a host in Hosts - is one form here: pick the kind, give a name and an address, press Check (nothing is written: the three steps are
   checked together), then Make it (the server does the three in order, reads each back, and takes the earlier ones back when one fails). Registers nothing in pgTabs: the button of the window sits in the bars
   of Access, Hosts and Core (pgKindBtn). Uses pg_common.js (pgApi), app.js (showModal, gconfirm, toast, esc, pn, refresh) and the cached state of pg_cores.js / pg_access.js to draw those tabs again. */
const pgKind = {id: 0, o: null, k: "", res: null};
window.pgKindBtn = () => pnType() !== "pasarguard" ? "" : `<button class="sm pri" onclick="pgKindNew()" title="Make a whole kind of account in one step: the inbound, the group and the host together">＋ Account type</button>`;
const pgKindRand = () => Math.random().toString(36).slice(2, 8);
const pgKindOf = id => ((pgKind.o || {}).kinds || []).find(k => k.id === id) || {};
window.pgKindNew = async () => {
  const id = +pn.id; if (!id) return;
  Object.assign(pgKind, {id, o: null, k: "", res: null});
  showModal("<b>＋ Account type</b>", '<div class="loading"><i class="spin"></i><span>Reading the panel…</span></div>', "wide");
  try { pgKind.o = await pgApi(id, "/kinds"); } catch (e) { showModal("<b>＋ Account type</b>", `<div class="err">${esc(e.message)}</div>`, "wide"); return; }
  pgKind.path = "/" + pgKindRand(); pgKind.svc = "g" + pgKindRand();
  pgKindDraw(); pgKindPick("vless-reality");
};
function pgKindDraw() {
  const o = pgKind.o, opt = (v, t, sel) => `<option value="${esc(v)}" ${sel ? "selected" : ""}>${esc(t)}</option>`, tls = o.tls || {};
  const chips = o.kinds.map(k => `<button type="button" class="pg-kind-chip" data-k="${k.id}" onclick="pgKindPick('${k.id}')" title="${esc(k.note)}"><b>${esc(k.title)}</b><small>${esc(k.note)}</small></button>`).join("");
  showModal("<b>＋ Account type</b>", `<div class="pg-kind-chips">${chips}</div>
    <form class="frm pg-cores-form pg-kind-form" id="pg-kind-form" onsubmit="return false" onchange="pgKindStale(event)" oninput="pgKindStale(event)">
      <label>Name <small>the inbound, the group and the link are named from it</small><input name="name" maxlength="64" placeholder="Reality home" autocomplete="off" required></label>
      <label>Address <small>domain or IP in the link</small><input name="address" list="pg-kind-addr" placeholder="${esc(o.addresses[0] || "cdn.example.com")}" autocomplete="off" required><datalist id="pg-kind-addr">${o.addresses.map(a => `<option value="${esc(a)}">`).join("")}</datalist></label>
      <label>Port <small id="pg-kind-portnote"></small><input name="port" type="number" min="1" max="65535" required></label>
      <label>Group <small>who gets it</small><select name="group">${opt("new", "A new group, named like this", true)}${o.groups.map(g => opt("g" + g.id, `Add to “${g.name}” (${g.tags.length} inbound${g.tags.length === 1 ? "" : "s"})`)).join("")}${opt("none", "No group (nobody gets it yet)")}</select></label>
      <label data-need="path">Path <small>of the WebSocket</small><input name="path" value="${esc(pgKind.path)}" autocomplete="off"></label>
      <label data-need="service">Service name <small>of gRPC</small><input name="service" value="${esc(pgKind.svc)}" autocomplete="off"></label>
      <label class="wide" data-need="tls">Certificate and key <small>${tls.guess ? "a guess: no TLS inbound of this panel to copy it from - they must exist on every node" : "the files the TLS inbounds of this panel use"}</small><span class="pg-kind-two"><input name="tls_cert" value="${esc(tls.cert || "")}" autocomplete="off" spellcheck="false"><input name="tls_key" value="${esc(tls.key || "")}" autocomplete="off" spellcheck="false"></span></label>
      <label class="wide" data-need="reality">The site REALITY imitates<span class="pg-ib-dest"><input name="reality_dest" value="${esc((o.reality || {}).dest || "")}" placeholder="www.example.com:443" autocomplete="off"><button type="button" class="sm" onclick="pgKindScan()" title="Ask the panel whether this site is a good target: TLS 1.3, h2, x25519">Scan…</button></span></label>
      ${o.cores.length > 1 ? `<label>Core<select name="core_id">${o.cores.map((c, i) => opt(c.id, `${c.name} (#${c.id})`, i === 0)).join("")}</select></label>` : `<input type="hidden" name="core_id" value="${o.cores[0] ? o.cores[0].id : ""}">`}
      <details class="wide pg-kind-more"><summary>More: the name of the link, the port in the link, SNI, Host header, REALITY names</summary><div class="pg-cores-form">
        <label class="wide">Name of the link <small>what the user sees; {USERNAME} is replaced by the panel</small><input name="link_name" maxlength="256" placeholder="Reality home ({USERNAME})" autocomplete="off"></label>
        <label>Port in the link <small>empty = the port of the inbound (a CDN may show another)</small><input name="host_port" type="number" min="1" max="65535" autocomplete="off"></label>
        <label data-need="tls">SNI <small>empty = the address</small><input name="sni" autocomplete="off"></label>
        <label data-need="path">Host header <small>empty = the address</small><input name="host_header" autocomplete="off"></label>
        <label data-need="reality">Server names <small>comma separated; empty = the site</small><input name="reality_names" autocomplete="off"></label>
        <label data-need="reality">Short ids <small>empty = one is made</small><input name="reality_short_ids" autocomplete="off"></label></div></details>
      <label class="chk wide"><input type="checkbox" name="restart"> Restart Xray on the nodes of the core now <small>(cuts their customers for a moment; without it the nodes use the new inbound after their next restart)</small></label></form>
    <div id="pg-kind-res"></div>
    <div class="frm-act"><button class="sm" onclick="pgKindCheck()" title="Check the three steps together; nothing is written">Check</button><span class="sp"></span><button class="pri" id="pg-kind-go" onclick="pgKindMake()">Make it…</button><button onclick="closeModal()">Cancel</button></div>`, "wide");
}
window.pgKindPick = k => {
  const f = $("#pg-kind-form"); if (!f) return;
  pgKind.k = k; pgKind.res = null;
  document.querySelectorAll(".pg-kind-chip").forEach(b => b.classList.toggle("on", b.dataset.k === k));
  const kd = pgKindOf(k);
  f.querySelectorAll("[data-need]").forEach(l => { const on = kd.needs.includes(l.dataset.need); l.hidden = !on; l.querySelectorAll("input, select, button").forEach(i => { i.disabled = !on; }); });
  pgKindPort();
  const n = f.elements.name; if (!n.value.trim()) n.placeholder = kd.title.replace(/ · /g, " ");
  const r = $("#pg-kind-res"); if (r) r.innerHTML = "";
};
const pgKindPort = (keep) => {      // the first usual port of the kind that no inbound of the chosen core uses (not touched when the owner typed one: keep)
  const f = $("#pg-kind-form"), kd = pgKindOf(pgKind.k), core = pgKind.o.cores.find(c => String(c.id) === String(f.elements.core_id.value)) || {ports: []};
  if (!keep) f.elements.port.value = kd.ports.find(p => !core.ports.includes(p)) || kd.ports[0];
  $("#pg-kind-portnote").textContent = core.ports.includes(+f.elements.port.value) ? "in use in this core" : "free in this core";
};
window.pgKindStale = ev => { pgKind.res = null; if (!$("#pg-kind-form")) return; pgKindPort(!(ev && ev.target && ev.target.name === "core_id")); };const pgKindBody = () => {
  const f = $("#pg-kind-form"), b = {kind: pgKind.k}, g = f.elements.group.value;
  [...f.elements].forEach(el => { if (!el.name || el.disabled || el.name === "group" || el.name === "restart") return; const v = el.type === "number" ? (el.value === "" ? "" : +el.value) : el.value.trim(); if (v !== "") b[el.name] = v; });
  b.group_mode = g === "new" ? "new" : g === "none" ? "none" : "existing"; if (g.startsWith("g")) b.group_id = +g.slice(1);
  return b;
};
window.pgKindScan = async () => {
  const f = $("#pg-kind-form"), target = f.elements.reality_dest.value.trim(), res = $("#pg-kind-res"); if (!target) { toast("✕ Write the site first", "bad"); return; }
  res.innerHTML = '<div class="loading"><i class="spin"></i><span>Asking the panel to look at the site (a few seconds)…</span></div>';
  try {
    const r = await pgApi(pgKind.id, "/cores/reality-scan", {method: "POST", body: {target}}), yn = (b, t) => `<span class="pill ${b ? "ok" : "warn"}">${b ? "✓" : "✕"} ${t}</span>`;
    res.innerHTML = `<div class="pg-cores-stat"><b>${esc(r.host || target)}</b>${yn(r.feasible, "usable as a target")}${yn(r.tls13, "TLS 1.3")}${yn(r.h2, "h2")}${r.latency_ms != null ? `<span class="mut">${r.latency_ms} ms</span>` : ""}</div>`;
  } catch (e) { res.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
};
const pgKindStepsHtml = p => `<div class="pg-kind-steps">${p.steps.map((s, i) => `<div class="pg-kind-step ${s.ok ? "ok" : "bad"}"><i>${s.ok ? "✓" : "✕"}</i><div><b>${i + 1}. ${esc(s.title)}</b>${s.errors.map(e => `<div class="err">${esc(e)}</div>`).join("")}${s.warnings.map(w => `<div class="pg-kind-warn">⚠ ${esc(w)}</div>`).join("")}</div></div>`).join("")}</div>
  ${p.inbound ? `<details class="pg-cores-full"><summary>The inbound as it will be stored (secrets hidden)</summary><pre class="pg-cores-pre">${esc(JSON.stringify(p.inbound, null, 2))}</pre></details>` : ""}`;
window.pgKindCheck = async () => {
  const res = $("#pg-kind-res"), f = $("#pg-kind-form"); if (!f.reportValidity()) return null;
  res.innerHTML = '<div class="loading"><i class="spin"></i><span>Checking the three steps…</span></div>';
  try { const p = await pgApi(pgKind.id, "/kinds/plan", {method: "POST", body: pgKindBody()}); res.innerHTML = pgKindStepsHtml(p); toast(p.valid ? "✓ The three steps pass" : "✕ " + (p.steps.find(s => !s.ok).errors[0] || "a step does not pass"), p.valid ? "ok" : "bad"); return p; }
  catch (e) { res.innerHTML = `<div class="err">${esc(e.message)}</div>`; return null; }
};
window.pgKindMake = async () => {
  const p = await pgKindCheck(); if (!p || !p.valid) return;
  const f = $("#pg-kind-form"), restart = f.elements.restart.checked, warns = p.steps.flatMap(s => s.warnings);
  if (!await gconfirm(`Make this account type?\n\n${p.steps.map((s, i) => `${i + 1}. ${s.title}`).join("\n")}\n\nThe core is backed up first; if a step fails the earlier ones are taken back.${restart ? "\nXray is restarted on the nodes of the core: their customers are cut for a moment." : "\nThe nodes use it after their next restart."}${warns.length ? `\n\n${warns.join("\n")}` : ""}`, {danger: restart || p.needs_confirm})) return;
  const go = $("#pg-kind-go"); go.disabled = true;
  try {
    const out = await pgApi(pgKind.id, "/kinds/make", {method: "POST", body: {...pgKindBody(), confirm: restart || p.needs_confirm, restart}});
    pgKindDone(out);
  } catch (e) { $("#pg-kind-res").innerHTML = `<div class="err">${esc(e.message)}</div>`; toast("✕ " + e.message, "bad"); go.disabled = false; }
};
function pgKindDone(out) {
  if (typeof pgCoresSt !== "undefined") Object.assign(pgCoresSt, {list: null, d: null, dry: null, dryFor: "", notice: null});      // the Core tab reads the core again
  if (typeof pgAccess !== "undefined") Object.assign(pgAccess, {g: null, t: null});                                                // and the Access tab the groups
  pn.hosts = null; pn.hostsKey = "";
  const ok = out.checks.every(c => c.ok);
  showModal(`<b>${ok ? "✓" : "⚠"} Account type made</b>`, `<div class="pg-kind-steps">
      <div class="pg-kind-step ok"><i>✓</i><div><b>Core:</b> the inbound <b>${esc(out.tag)}</b> is in the core (backup <span class="mono">${esc(out.core.backup)}</span>)</div></div>
      ${out.group ? `<div class="pg-kind-step ok"><i>✓</i><div><b>Access:</b> the group <b>${esc(out.group.name)}</b> ${out.group.created ? "is new and holds it" : "holds it now"}</div></div>` : ""}
      <div class="pg-kind-step ok"><i>✓</i><div><b>Hosts:</b> the link <b>${esc(out.host.remark)}</b> points at ${esc(out.host.address.join(", "))}${out.host.port ? ":" + out.host.port : ""}</div></div></div>
    ${out.checks.map(c => `<div class="pg-kind-chk ${c.ok ? "ok" : "bad"}">${c.ok ? "✓" : "✕"} ${esc(c.what)} <span class="mut">(read back from the panel)</span></div>`).join("")}
    ${out.core.public_key ? `<div class="pg-kind-chk">REALITY public key <code class="mono">${esc(out.core.public_key)}</code> <button class="sm" onclick="navigator.clipboard.writeText('${esc(out.core.public_key)}').then(() => toast('✓ Copied', 'ok'))">Copy</button> <span class="mut">(the private key stays on the panel)</span></div>` : ""}
    <div class="pg-kind-chk ${out.restarted ? "ok" : out.restart_error ? "bad" : ""}">${out.restarted ? "✓ Xray was restarted on the nodes: it works now." : out.restart_error ? `✕ Saved, but the restart failed: ${esc(out.restart_error)}` : "The nodes use it after their next restart (Core → Restart nodes…)."}</div>
    ${out.group ? '<div class="pg-kind-chk mut">Next: make a user (Users → ＋ New user) and give them the group, or put the group in a template.</div>' : '<div class="pg-kind-chk mut">No group holds it yet: nobody gets this link until a group does (Access).</div>'}
    <div class="frm-act"><span class="sp"></span><button class="pri" onclick="closeModal();refresh()">Done</button></div>`, "wide");
}
