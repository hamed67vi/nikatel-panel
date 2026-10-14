/* PasarGuard Hosts: the window of ONE host (app.js hands over to pgHostOpen for a PasarGuard panel; id 0 = a new host), with every field the panel has - where it points, how it is
   secured, which users get it - and the advanced settings (extra headers, transport, mux, fragment, noise, ECH, final mask, WireGuard overrides, the xray subscription template) as
   JSON text that the server checks key by key (the panel drops an unknown key without a word). Clone makes a copy that is switched off; Delete and every change save the host to a
   file first (Backups can make it again). Everything goes through /api/panels/{id}/pg/hosts. */
const pgH = {id: 0, hid: 0, d: null};
const pgHAdv = [
  ["http_headers", "HTTP headers", "Extra headers the client sends.", '{"X-Real-IP": "1"}'],
  ["transport_settings", "Transport", "Keys: websocket_settings, grpc_settings, kcp_settings, tcp_settings, xhttp_settings.", '{"websocket_settings": {"heartbeatPeriod": 30}}'],
  ["mux_settings", "Mux", "Keys: xray, sing_box, clash.", '{"xray": {"enabled": true, "concurrency": 8, "xudp_concurrency": 16, "xudp_proxy_udp_443": "allow"}}'],
  ["fragment_settings", "Fragment", "Keys: xray, sing_box.", '{"xray": {"packets": "tlshello", "length": "100-200", "interval": "10-20"}}'],
  ["noise_settings", "Noise", "Key: xray (a list of noise packets).", '{"xray": [{"type": "rand", "packet": "10-20", "delay": "10-16"}]}'],
  ["ech", "ECH", "Keys: xray, mihomo, sing_box.", '{"xray": {"config_list": "", "query_strategy": "none"}}'],
  ["final_mask_settings", "Final mask", "Keys: tcp, udp, quicParams.", '{"tcp": [{"type": "fragment", "settings": {"packets": "1-3"}}]}'],
  ["wireguard_overrides", "WireGuard", "Keys: allowed_ips, mtu, reserved, keepalive_seconds, dns.", '{"mtu": 1280, "dns": ["1.1.1.1"]}'],
  ["subscription_templates", "Subscription template", "Key: xray, the id of an xray client template.", '{"xray": 2}'],
];
const pgHSt = ["active", "disabled", "limited", "expired", "on_hold"];

window.pgHostsMenu = () => pnType() !== "pasarguard" ? "" : `${window.pgKindBtn ? pgKindBtn() : ""}<button class="sm pri" onclick="pgHostOpen(+pn.id, 0)" title="Make a new host">＋ New host</button><button class="sm" onclick="pgHostBackups()" title="Hosts saved here before a change or a delete">Backups</button>`;

window.pgHostOpen = async (id, hid) => {
  let d; try { d = await pgApi(id, hid ? `/hosts/${hid}` : "/hosts/new"); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  Object.assign(pgH, {id, hid, d}); pgHostShow();
};

function pgHostShow() {
  const {host: h, options: o} = pgH.d, hid = pgH.hid, id = pgH.id;
  const fld = (l, inner, tip = "", tag = "label") => `<${tag} class="kv2 kf"${tip ? ` title="${esc(tip)}"` : ""}><span>${l}</span>${inner}</${tag}>`;      // (a row of ticks is a div: a label would tick the first one when its caption is clicked)
  const box = (icon, title, rows, cls = "") => `<div class="sbox ${cls}"><h5><i>${icon}</i>${title}</h5>${rows}</div>`;
  const sel = (name, opts, cur) => `<select name="${name}">${opts.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(cur) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
  const tags = o.inbounds.map(i => [i.tag, i.tag + (i.protocol ? ` · ${i.protocol}${i.network ? "/" + i.network : ""}` : "")]); if (h.inbound_tag && !o.inbounds.some(i => i.tag === h.inbound_tag)) tags.unshift([h.inbound_tag, h.inbound_tag + " (not in the cores)"]);
  const chips = (name, list, on, label = x => x) => `<div class="pgh-chips">${list.map(x => `<label class="chk"><input type="checkbox" name="${name}" value="${esc(x)}" ${on.includes(x) ? "checked" : ""}><span>${esc(label(x))}</span></label>`).join("")}</div>`;
  const where = box("📍", "Where", fld("Remark", `<input name="remark" maxlength="256" value="${esc(h.remark)}" required placeholder="shown in the client; {USERNAME} and friends work" autocomplete="off">`, "The name the client shows. The panel fills {USERNAME}, {DATA_LEFT}, {DAYS_LEFT} and the like.")
    + fld("Inbound", sel("inbound_tag", [["", "(none)"], ...tags], h.inbound_tag), "The inbound of a core that this host leads to")
    + fld("Address", `<textarea name="address" rows="2" spellcheck="false" placeholder="one or more, separated by commas or lines">${esc(h.address.join(", "))}</textarea>`, "Where the client connects: a domain or an IP. Several = the client gets one line for each")
    + fld("Port", `<input name="port" type="number" min="1" max="65535" value="${h.port ?? ""}" placeholder="the inbound's port">`)
    + fld("Priority", `<input name="priority" type="number" min="0" step="1" value="${h.priority}" title="The order of the hosts in the subscription: the lowest first">`)
    + fld("For users", chips("st", pgHSt, h.status || [], x => x.replace("_", " ")), "Only users in these statuses get this host. None ticked = every user", "div")
    + `<div class="kv2 kf"><span>Enabled</span>${pgSw(!h.is_disabled, "", 'name="enabled"')}</div>`, "pgh-where");
  const sec = box("🔒", "Security", fld("Security", sel("security", o.securities.map(s => [s, s.replace("_", " ")]), h.security), "inbound default = what the inbound itself says")
    + fld("SNI", `<input name="sni" value="${esc(h.sni.join(", "))}" placeholder="server name, comma separated" autocomplete="off">`)
    + fld("Host header", `<input name="host" value="${esc(h.host.join(", "))}" placeholder="the Host header / CDN name" autocomplete="off">`)
    + fld("Path", `<input name="path" maxlength="1024" value="${esc(h.path)}" placeholder="/ws" autocomplete="off">`)
    + fld("ALPN", chips("alpn", o.alpns, h.alpn), "", "div")
    + fld("Fingerprint", sel("fingerprint", o.fingerprints.map(f => [f, f || "(none)"]), h.fingerprint))
    + fld("Insecure", sel("allowinsecure", [["", "default"], ["true", "allow"], ["false", "do not allow"]], h.allowinsecure == null ? "" : String(h.allowinsecure)), "Allow a certificate that does not verify")
    + `<div class="kv2 kf"><span>SNI as host</span>${pgSw(h.use_sni_as_host, "", 'name="use_sni_as_host" title="Use the SNI as the Host header too"')}</div>`
    + `<div class="kv2 kf"><span>Random UA</span>${pgSw(h.random_user_agent, "", 'name="random_user_agent" title="A random User-Agent in the client config"')}</div>`, "pgh-sec");
  const more = `<details class="sbox pgh-more pgh-row"><summary><b>More TLS options</b><small class="mut"> VLESS route · cipher suites · pinned certificate · verify by name</small></summary>
    ${fld("VLESS route", `<input name="vless_route" maxlength="128" value="${esc(h.vless_route)}" autocomplete="off">`)}${fld("Cipher suites", `<input name="cipher_suites" maxlength="1024" value="${esc(h.cipher_suites)}" autocomplete="off">`)}
    ${fld("Pinned cert", `<input name="pinned_peer_cert_sha256" maxlength="256" value="${esc(h.pinned_peer_cert_sha256)}" placeholder="sha256 of the certificate" autocomplete="off">`)}${fld("Verify by name", `<input name="verify_peer_cert_by_name" value="${esc(h.verify_peer_cert_by_name.join(", "))}" placeholder="names, comma separated" autocomplete="off">`)}</details>`;
  const adv = `<details class="sbox pgh-adv pgh-row"><summary><b>Advanced settings</b><small class="mut"> ${pgHAdv.filter(([k]) => h.json[k]).map(a => a[1]).join(" · ") || "none set"}</small></summary>
    ${pgHAdv.map(([k, label, hint, ex]) => `<div class="pgh-a"><div class="pgh-ah"><b>${label}</b><small class="mut">${esc(hint)}</small>${k === "subscription_templates" && o.templates.length ? `<select class="sm" onchange="pgHTpl(this)" title="Fill the box with one of the xray templates"><option value="">template…</option>${o.templates.map(t => `<option value="${t.id}">${esc(t.name)} (#${t.id})</option>`).join("")}</select>` : ""}<button type="button" class="sm" onclick="pgHAdvEx('${k}')" title="Put an example in the box">example</button></div>
      <textarea name="adv_${k}" rows="${h.json[k] ? Math.min(12, h.json[k].split("\n").length + 1) : 2}" spellcheck="false" data-ex="${esc(ex)}" data-was="${esc(h.json[k])}" placeholder="empty = nothing set" oninput="pgHAdvCheck(this)">${esc(h.json[k])}</textarea><small class="err pgh-e" hidden></small></div>`).join("")}</details>`;
  const acts = hid ? `<button type="button" class="sm" onclick="pgHostClone(${id}, ${hid})" title="A copy of this host, switched off">Clone</button><button type="button" class="sm danger" onclick="pgHostDelete(${id}, ${hid})">Delete</button>` : "";
  showModal(hid ? `<span class="st ${h.is_disabled ? "" : "online"}"></span><b>${esc(h.remark)}</b> <span class="pill">${esc(h.inbound_tag || "no inbound")}</span> <span class="pill">#${hid}</span>` : "<b>New host</b>",
    `<form class="pg-admin-form2 pgh-form" onsubmit="return pgHostSave(event)"><div class="sgrid ue-grid two">${where}${sec}</div>${more}${adv}
    <div class="frm-act"><button class="pri" type="submit">${hid ? "Save" : "Make the host"}</button><button type="button" onclick="closeModal()">Cancel</button><span class="sp"></span>${acts}</div></form>`, "wide pgh");
}
window.pgHAdvEx = k => { const t = document.querySelector(`#modal textarea[name="adv_${k}"]`); if (!t) return; t.value = JSON.stringify(JSON.parse(t.dataset.ex), null, 2); t.rows = Math.min(12, t.value.split("\n").length + 1); pgHAdvCheck(t); };
window.pgHTpl = sel => { if (!sel.value) return; const t = document.querySelector('#modal textarea[name="adv_subscription_templates"]'); if (t) { t.value = JSON.stringify({xray: +sel.value}); pgHAdvCheck(t); } sel.value = ""; };
window.pgHAdvCheck = t => { const e = t.parentElement.querySelector(".pgh-e"); let bad = ""; if (t.value.trim()) { try { const j = JSON.parse(t.value); if (!j || typeof j !== "object" || Array.isArray(j)) bad = "a JSON object is expected, like { … }"; } catch (x) { bad = "not valid JSON yet: " + String(x.message).slice(0, 70); } } if (e) { e.hidden = !bad; e.textContent = bad; } t.classList.toggle("bad", !!bad); };

function pgHostBody(form) {
  const f = form.elements, split = v => String(v || "").split(/[,\n]/).map(x => x.trim()).filter(Boolean), body = {};
  body.remark = f.remark.value.trim(); body.inbound_tag = f.inbound_tag.value || null; body.address = split(f.address.value); body.port = f.port.value === "" ? null : +f.port.value; body.priority = f.priority.value === "" ? 0 : +f.priority.value;
  body.status = [...form.querySelectorAll('input[name="st"]:checked')].map(x => x.value); body.is_disabled = !f.enabled.checked;
  body.security = f.security.value; body.sni = split(f.sni.value); body.host = split(f.host.value); body.path = f.path.value.trim(); body.alpn = [...form.querySelectorAll('input[name="alpn"]:checked')].map(x => x.value);
  body.fingerprint = f.fingerprint.value; body.allowinsecure = f.allowinsecure.value === "" ? null : f.allowinsecure.value === "true"; body.use_sni_as_host = f.use_sni_as_host.checked; body.random_user_agent = f.random_user_agent.checked;
  body.vless_route = f.vless_route.value; body.cipher_suites = f.cipher_suites.value; body.pinned_peer_cert_sha256 = f.pinned_peer_cert_sha256.value; body.verify_peer_cert_by_name = split(f.verify_peer_cert_by_name.value);
  pgHAdv.forEach(([k]) => { const t = f["adv_" + k]; if (t && (pgH.hid ? t.value.trim() !== t.dataset.was.trim() : t.value.trim())) body[k] = t.value; });      // only an advanced setting that was touched (or is set on a new host)
  return body;
}
window.pgHostSave = async e => {
  e.preventDefault(); const form = e.target, hid = pgH.hid, btn = form.querySelector("button[type=submit]");
  if (form.querySelector("textarea.bad")) { toast("✕ Fix the JSON boxes marked red first", "bad"); return false; }
  btn.disabled = true;
  try {
    const body = pgHostBody(form), r = await pgApi(pgH.id, hid ? `/hosts/${hid}` : "/hosts", {method: hid ? "PUT" : "POST", body});
    toast(hid ? `✓ ${r.host.remark} saved (${r.changed.map(x => x.replace(/_/g, " ")).join(", ")})` : `✓ ${r.host.remark} made`, "ok"); pn.hosts = null; closeModal(); refresh();
  } catch (err) { toast(/nothing to change/i.test(err.message) ? "Nothing changed" : "✕ " + err.message, /nothing to change/i.test(err.message) ? "" : "bad"); btn.disabled = false; }
  return false;
};
window.pgHostClone = async (id, hid) => { try { const r = await pgApi(id, `/hosts/${hid}/clone`, {method: "POST"}); toast(`✓ ${r.host.remark} made (switched off)`, "ok"); pn.hosts = null; closeModal(); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.pgHostDelete = async (id, hid, name) => {
  if (name == null) name = (pgH.d && pgH.d.host && pgH.d.host.remark) || "#" + hid;
  if (!await gconfirm(`Delete the host “${name}”? Users lose that address in their links. The panel cannot undo it; the host is saved here first (Backups can make it again).`, {danger: true})) return;
  try { await pgApi(id, `/hosts/${hid}?confirm=true`, {method: "DELETE"}); toast("✓ Host deleted (saved in Backups)", "ok"); pn.hosts = null; closeModal(); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};

/* backups: every host that was changed or deleted from here, newest first */
window.pgHostBackups = async () => {
  const id = +pn.id, draw = body => showModal("<b>Backups of the hosts</b>", body, "wide pgh-bk");
  draw('<div class="loading"><i class="spin"></i><span>Reading…</span></div>');
  try {
    const r = await pgApi(id, "/hosts/backups"); pgH.bk = r.backups;
    draw(`<div class="lbox full pg-admin-box"><table class="tbl lift"><colgroup><col style="width:190px"><col><col style="width:70px"><col style="width:170px"><col style="width:110px"></colgroup><thead><tr><th>When</th><th>Host</th><th>#</th><th>Why</th><th></th></tr></thead><tbody>${r.backups.map((b, i) => `<tr><td class="mut">${b.at ? esc(new Date(b.at * 1000).toLocaleString()) : "—"}</td><td class="name">${esc(b.remark || "#" + b.host_id)}</td><td class="mut">${b.host_id}</td><td class="mut">${esc(b.why)}</td><td><button class="sm" onclick="pgHostRestore(${i})">Restore…</button></td></tr>`).join("") || '<tr><td colspan="5" class="mut">nothing saved yet: a host is saved before it is changed or deleted</td></tr>'}</tbody></table></div>
      <small class="mut">Restoring writes the host as it was over the host that has its number now (saved first), or makes it again with a new number when it was deleted.</small>`);
  } catch (e) { draw(`<div class="err">${esc(e.message)}</div>`); }
};
window.pgHostRestore = async i => {
  const b = (pgH.bk || [])[i]; if (!b) return;
  if (!await gconfirm(`Put the host “${b.remark || "#" + b.host_id}” back as it was ${b.at ? new Date(b.at * 1000).toLocaleString() : ""}? It replaces the host that has number ${b.host_id} now (saved first), or is made again when it was deleted.`, {danger: true})) return;
  try { const r = await pgApi(+pn.id, `/hosts/restore/${encodeURIComponent(b.file)}`, {method: "POST"}); toast(r.made_again ? `✓ ${r.host.remark} made again` : `✓ ${r.host.remark} put back`, "ok"); pn.hosts = null; closeModal(); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
