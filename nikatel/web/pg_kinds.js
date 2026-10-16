/* PasarGuard: ONE window makes a whole KIND of account (the owner, 2026-10-15: "we only choose the kind of the account and the panel builds it" - "I only give the protocol, you do the rest").
   What was three jobs in three tabs - an inbound in Core, a group in Access, a host in Hosts - is one choice here: pick the protocol and press Make it. The name, the address, the port, the path, the keys,
   the certificate, the group, the host and a user template are chosen by the server (the details are folded away: open them to change something). Check shows the steps and what was chosen (nothing is
   written); Make it does them in order, reads each back, and takes the earlier ones back when one fails. Registers nothing in pgTabs: the button of the window sits in the bars of Access, Hosts and Core
   (pgKindBtn). Uses pg_common.js (pgApi), app.js (showModal, gconfirm, toast, esc, pn, refresh) and the cached state of pg_cores.js / pg_access.js to draw those tabs again. */
const pgKind = {id: 0, o: null, k: "", res: null};
const PG_KINDS_UI = false;      // the button "＋ Account type" is not shown in Access, Hosts and Core (the owner, 2026-10-15: "remove this completely"); the window and its routes stay: true brings the button back
window.pgKindBtn = () => !PG_KINDS_UI || pnType() !== "pasarguard" ? "" : `<button class="sm pri" onclick="pgKindNew()" title="Make a whole kind of account in one step: choose the protocol, the panel makes the inbound, the group, the host and a user template">＋ Account type</button>`;
const pgKindRand = () => Math.random().toString(36).slice(2, 8);
const pgKindShort = k => String((k && k.title) || "").replace(/^(VLESS|Trojan|VMess) · /, "");
const pgKindOf = id => ((pgKind.o || {}).kinds || []).find(k => k.id === id) || {};
window.pgKindNew = async () => {
  const id = +pn.id; if (!id) return;
  Object.assign(pgKind, {id, o: null, k: "", res: null, inline: false});
  showModal("<b>＋ Account type</b>", '<div class="loading"><i class="spin"></i><span>Reading the panel…</span></div>', "wide");
  try { pgKind.o = await pgApi(id, "/kinds"); } catch (e) { showModal("<b>＋ Account type</b>", `<div class="err">${esc(e.message)}</div>`, "wide"); return; }
  pgKind.path = "/" + pgKindRand(); pgKind.svc = "g" + pgKindRand();
  pgKindDraw(); pgKindPick("vless-reality");
};
const PKI = {};      // the little pictures of the Add form (the owner's picture, 2026-10-16)
[["user", '<circle cx="12" cy="8" r="3.6"/><path d="M4.5 20c.8-3.8 3.8-6 7.5-6s6.7 2.2 7.5 6"/>'], ["server", '<rect x="4" y="4" width="16" height="7" rx="2.2"/><rect x="4" y="13" width="16" height="7" rx="2.2"/><path d="M8 7.5h.01M8 16.5h.01"/>'], ["cube", '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5L12 12l8-4.5M12 12v9"/>'], ["grid", '<rect x="4" y="4" width="6.5" height="6.5" rx="1.6"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.6"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.6"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.6"/>'],
  ["folder", '<path d="M3.5 7a2 2 0 012-2h4l2 2.5h7a2 2 0 012 2V17a2 2 0 01-2 2h-13a2 2 0 01-2-2z"/>'], ["code", '<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14"/>'], ["search", '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>'], ["spark", '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM18.5 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/>'], ["chev", '<path d="M9 6l6 6-6 6"/>'],
  ["globe", '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.2 3 14.8 0 18M12 3c-3 3.2-3 14.8 0 18"/>'], ["eye", '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>'], ["cert", '<rect x="4" y="3" width="13" height="15" rx="2"/><path d="M8 8h5M8 12h3"/><circle cx="17" cy="17" r="3"/><path d="M15.5 20l-.5 2 2-1 2 1-.5-2"/>'], ["link", '<path d="M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1"/>'],
  ["file", '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4M9.5 14h5"/>'], ["upload", '<path d="M12 16V5M7.5 9.5L12 5l4.5 4.5M5 15v3.5a1.5 1.5 0 001.5 1.5h11a1.5 1.5 0 001.5-1.5V15"/>'], ["shield", '<path d="M12 3l7 3v5.2c0 4.5-3 7.9-7 9.8-4-1.9-7-5.3-7-9.8V6z"/>'], ["lock", '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/>'], ["info", '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>']
].forEach(([k, d]) => { PKI[k] = `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`; });
const pgKindIc = k => { const id = String(k.id || ""); const n = /mlkem|shadowsocks/.test(id) ? "lock" : /xhttp.*reality/.test(id) ? "shield" : /xhttp.*tls|httpupgrade/.test(id) ? "upload" : /xhttp/.test(id) ? "code" : /vision/.test(id) ? "eye" : /reality/.test(id) ? "globe" : /pin/.test(id) ? "cert" : /ws/.test(id) ? "link" : /grpc/.test(id) ? "server" : /tcp.*(http|header)|http-header/.test(id) ? "file" : /tcp/.test(id) ? "lock" : "globe"; return `<i class="pg-kc-i">${PKI[n]}</i>`; };
document.addEventListener("toggle", e => { const d = e.target; if (d && d.matches && d.matches(".pg-kind-dr") && d.open) document.querySelectorAll(".pg-kind-dr[open]").forEach(o => { if (o !== d) o.open = false; }); }, true);      // (one drawer open at a time, in every browser)
function pgKindFormHtml(inline) {      // the whole form: in a window (the old button) or as the Add tab of Configs (inline: no Cancel); 1.54.7: the protocols in rows by family, one row of fields, the rest folded
  const o = pgKind.o, opt = (v, t, sel) => `<option value="${esc(v)}" ${sel ? "selected" : ""}>${esc(t)}</option>`, tls = o.tls || {};
  const chip = k => `<button type="button" class="pg-kind-chip" data-k="${k.id}" onclick="pgKindPick('${k.id}')" title="${esc(k.note)}">${pgKindIc(k)}<span>${esc(pgKindShort(k))}</span></button>`;
  const drawers = [["VLESS", "vless"], ["Trojan", "trojan"], ["VMess", "vmess"], ["Shadowsocks", "shadowsocks"]].map(([label, proto]) => { const ks = o.kinds.filter(k => k.protocol === proto); return ks.length ? `<details class="pg-kind-dr" name="pg-kind-acc" data-p="${proto}"${proto === "vless" ? " open" : ""}><summary><i class="pg-dr-i">${PKI.cube}</i><span class="pg-dr-n">${label}</span><i class="pg-dr-c">${ks.length}</i></summary><div class="pg-kind-row">${ks.map(chip).join("")}</div></details>` : ""; }).join("");      // (the owner, 2026-10-15: "VLESS in a drawer"): one drawer per protocol, one open at a time, the chosen kind shown on the drawer even when it is shut
  const chips = drawers;
  const subs = `<div class="pg-kind-subs" id="pg-kind-subs"><div class="pg-ks-h"><i class="pg-kf-i">${PKI.link}</i><b>Subscription configs</b></div><div class="pg-ks-list"><div class="loading"><i class="spin"></i><span>Reading the hosts…</span></div></div></div>`;      // (the owner, 2026-10-16: "the right side is empty - show the list of subscription configs"): the hosts of the panel, the lines a user's link carries
  return `${inline ? '<div class="pg-kind-frame">' : ""}<div class="pg-kind-paste"><i class="pg-kf-i">${PKI.code}</i><input id="pg-kind-link" placeholder="Your own config: paste a vless:// trojan:// vmess:// or ss:// link to copy its type" autocomplete="off" spellcheck="false" onkeydown="if (event.key === 'Enter') pgKindUseLink()"><button type="button" class="sm" onclick="pgKindUseLink()">Use</button></div><div class="pg-kind-cols"><div class="pg-kind-groups">${chips}</div><div class="pg-kind-side"><div class="pg-kind-note" id="pg-kind-note"></div>
    <form class="frm pg-kind-form" id="pg-kind-form" onsubmit="return false" onchange="pgKindStale(event)" oninput="pgKindStale(event)">
    <div class="pg-kind-quick">
      <label>Name<span class="pg-kf"><i class="pg-kf-i">${PKI.user}</i><input name="name" maxlength="64" placeholder="chosen for you" autocomplete="off" title="The inbound, the group and the link are named from it"></span></label>
      <label>Address<span class="pg-kf"><i class="pg-kf-i">${PKI.server}</i><input name="address" list="pg-kind-addr" placeholder="${esc(o.addresses[0] ? "the first node: " + o.addresses[0] : "write it")}" autocomplete="off" title="Domain or IP written into the link"></span><datalist id="pg-kind-addr">${o.addresses.map(a => `<option value="${esc(a)}">`).join("")}</datalist></label>
      <label>Core<span class="pg-kf"><i class="pg-kf-i">${PKI.cube}</i><select name="core_id" data-dd title="The core (Xray config) the inbound is written into">${o.cores.length ? o.cores.map((c, i) => opt(c.id, `${c.name} (#${c.id})`, i === 0)).join("") : opt("", "no core yet")}</select></span></label>
      <label>Port<span class="pg-kf"><i class="pg-kf-i">${PKI.grid}</i><input name="port" type="number" min="1" max="65535"></span><small id="pg-kind-portnote"></small></label>
      <label>Group<span class="pg-kf"><i class="pg-kf-i">${PKI.folder}</i><select name="group" data-dd>${opt("new", "A new group", true)}${o.groups.map(g => opt("g" + g.id, `Add to “${g.name}” (${g.tags.length})`)).join("")}${opt("none", "No group")}</select></span></label>
      <label class="pg-kind-tpl"><span class="chk"><input type="checkbox" name="template" checked> User template</span><span class="pg-kind-tplnum" data-tpl><input name="tpl_gb" type="number" min="0" step="any" value="50" title="Data limit, GB (0 = unlimited)"> GB <input name="tpl_days" type="number" min="0" value="30" title="Days (0 = no end)"> days</span></label>
      <label class="wide" data-need="mlkem">Decryption <small>the server string of <code>xray vlessenc</code>, for the inbound</small><input name="decryption" placeholder="mlkem768x25519plus.native.600s…" autocomplete="off" spellcheck="false"></label>
      <label class="wide" data-need="mlkem">Encryption <small>the client string of <code>xray vlessenc</code>, written into the links</small><input name="encryption" placeholder="mlkem768x25519plus.native.0rtt…" autocomplete="off" spellcheck="false"></label>
    </div>
    <details class="pg-kind-adv"><summary>More (everything here is chosen for you)</summary><div class="pg-cores-form">
      <label data-need="path">Path <small>of the WebSocket / xHTTP / HTTP header</small><input name="path" value="${esc(pgKind.path)}" autocomplete="off"></label>
      <label data-need="service">Service name <small>of gRPC</small><input name="service" value="${esc(pgKind.svc)}" autocomplete="off"></label>
      <label class="wide" data-need="tls">Certificate and key <small>${tls.guess ? "a guess: no TLS inbound of this panel to copy it from - they must exist on every node" : "the files the TLS inbounds of this panel use"}</small><span class="pg-kind-two"><input name="tls_cert" value="${esc(tls.cert || "")}" autocomplete="off" spellcheck="false"><input name="tls_key" value="${esc(tls.key || "")}" autocomplete="off" spellcheck="false"></span></label>
      <label class="wide" data-need="tls pin">SNI <small>the name the network sees; empty = chosen for you (a domain of your own links when the certificate is the panel's own, else the address)</small><input name="sni" list="pg-kind-snis" placeholder="chosen for you" autocomplete="off"><datalist id="pg-kind-snis">${(o.snis || []).map(a => `<option value="${esc(a)}">`).join("")}</datalist></label>
      <label class="wide" data-need="reality">The site REALITY imitates <small>empty = the panel's own REALITY site, else the first usual site it finds usable</small><span class="pg-ib-dest"><input name="reality_dest" value="${esc((o.reality || {}).dest || "")}" placeholder="chosen for you" autocomplete="off"><button type="button" class="sm" onclick="pgKindScan()" title="Ask the panel whether this site is a good target: TLS 1.3, h2, x25519">Scan…</button></span></label>
      <label data-need="spx">SpiderX path <small>empty = none (a link may carry /api)</small><input name="reality_spx" placeholder="/" autocomplete="off"></label>
      <label data-sec="fp">Fingerprint <small>of the client, in the link</small><select name="fingerprint" data-dd><option value="">default</option>${(o.fingerprints || []).map(x => opt(x, x)).join("")}</select></label>
      <label class="wide">Name of the link <small>what the user sees; {USERNAME} is replaced by the panel</small><input name="link_name" maxlength="256" placeholder="the name, then ({USERNAME})" autocomplete="off"></label>
      <label>Port in the link <small>empty = the port of the inbound (a CDN may show another)</small><input name="host_port" type="number" min="1" max="65535" autocomplete="off"></label>
      <label data-need="path">Host header <small>empty = the address</small><input name="host_header" autocomplete="off"></label>
      <label data-need="reality">Server names <small>comma separated; empty = the site</small><input name="reality_names" autocomplete="off"></label>
      <label data-need="reality">Short ids <small>empty = one is made</small><input name="reality_short_ids" autocomplete="off"></label>
      <label class="chk wide"><input type="checkbox" name="restart"> Restart Xray on the nodes of the core now <small>(cuts their customers for a moment; without it the nodes use the new inbound after their next restart)</small></label></div></details></form>
    <div id="pg-kind-res"></div>
    <div class="frm-act"><button class="sm" onclick="pgKindCheck()" title="Check the steps and see what is chosen for you; nothing is written">${PKI.search}<span>Check</span></button><span class="sp"></span><button class="pri" id="pg-kind-go" onclick="pgKindMake()">${PKI.spark}<span>Make it…</span>${PKI.chev}</button>${inline ? "" : '<button onclick="closeModal()">Cancel</button>'}</div></div>${inline ? subs : ""}</div>${inline ? "</div>" : ""}`;
}
function pgKindDraw() { showModal("<b>＋ Account type</b>", pgKindFormHtml(false), "wide"); }
/* the Add tab of Configs: the same form, in the page (the Panel page redraws itself every 15 s: while this form is on, state.build holds that back) */
window.pgTabs.kinds = {label: "Add", order: 20, types: ["pasarguard"], render: async (id, box) => {
  Object.assign(pgKind, {id, o: null, k: "", res: null, inline: true});
  pgLoading(box, "Reading the panel…");
  try { pgKind.o = await pgApi(id, "/kinds"); } catch (e) { pgError(box, e); return; }
  if (!box.isConnected || pn.tab !== "kinds") return;
  pgKind.path = "/" + pgKindRand(); pgKind.svc = "g" + pgKindRand();
  pgPaint(box, `<div class="pg-kind-page" id="pg-kind-page">${pgKindFormHtml(true)}</div>`); state.build = "kinds";
  pgKindPick("vless-reality"); pgKindSubs();
}};
/* the third column of the Add tab: the hosts of the panel = the configs a user's subscription carries (read from the same cache as the Hosts tab) */
window.pgKindSubs = async () => {
  const id = +pn.id, paint = html => { const l = $("#pg-kind-subs .pg-ks-list"); if (l) l.innerHTML = html; };
  try { if (!pn.hosts || pn.hostsKey !== String(id)) { pn.hosts = (await api(`/api/panels/${id}/hosts`)).hosts; pn.hostsKey = String(id); } } catch (e) { paint(`<div class="err">${esc(e.message)}</div>`); return; }
  const hs = pn.hosts || [], h = $("#pg-kind-subs .pg-ks-h"); if (!h) return;
  h.querySelectorAll(".pg-dr-c").forEach(n => n.remove()); h.insertAdjacentHTML("beforeend", `<i class="pg-dr-c">${hs.length}</i>`);
  paint(hs.length ? hs.map(x => `<div class="pg-ks-r${x.disabled ? " off" : ""}" title="${esc(`${x.remark} · ${x.address}${x.port ? ":" + x.port : ""} · ${x.tag}`)}"><i class="pg-kc-i">${PKI.link}</i><span class="pg-ks-t"><b>${esc(x.remark || "(no name)")}</b><small>${esc(x.address || "—")}${x.port ? ":" + esc(x.port) : ""} · ${esc(x.tag || "—")}</small></span><em class="pg-ks-s">${x.disabled ? "off" : "on"}</em></div>`).join("") : '<div class="mut pg-ks-none">No config yet: make an account type and its link shows here.</div>');
};
window.pgKindPick = k => {
  const f = $("#pg-kind-form"); if (!f) return;
  pgKind.k = k; pgKind.res = null;
  document.querySelectorAll(".pg-kind-chip").forEach(b => b.classList.toggle("on", b.dataset.k === k));
  const kd = pgKindOf(k), nt = $("#pg-kind-note"); if (nt) nt.textContent = kd.note || "";
  f.querySelectorAll("[data-need]").forEach(l => { const on = l.dataset.need.split(" ").some(n => kd.needs.includes(n)); l.hidden = !on; l.querySelectorAll("input, select, button").forEach(i => { i.disabled = !on; }); });
  f.querySelectorAll("[data-sec]").forEach(l => { const on = kd.security !== "none"; l.hidden = !on; l.querySelectorAll("select, input").forEach(i => { i.disabled = !on; }); });
  f.querySelectorAll("[data-auto]").forEach(i => { i.value = ""; delete i.dataset.auto; });      // what the last check chose for another kind goes: this kind is chosen again
  document.querySelectorAll(".pg-kind-dr").forEach(d => { const own = kd.protocol === d.dataset.p; if (own) d.open = true; });      // the drawer of the chosen kind opens (the others shut); it does not repeat the name of the kind (the owner, 2026-10-16: the REALITY beside VLESS is gone)
  pgKindPort(); pgKindTplSync();
  const r = $("#pg-kind-res"); if (r) r.innerHTML = "";
};
const pgKindPort = (keep) => {      // the first usual port of the kind that no inbound of the chosen core uses (not touched when the owner typed one: keep)
  const f = $("#pg-kind-form"), kd = pgKindOf(pgKind.k), core = pgKind.o.cores.find(c => String(c.id) === String(f.elements.core_id.value)) || {ports: []};
  if (!keep) f.elements.port.value = kd.first_port ? kd.ports[0] : kd.ports.find(p => !core.ports.includes(p)) || kd.ports[0];      // (the plain WebSocket always starts at 80, the owner, 2026-10-16: the note under the field and the check say when another inbound holds it)
  $("#pg-kind-portnote").textContent = core.ports.includes(+f.elements.port.value) ? "in use in this core" : "free in this core";
};
const pgKindTplSync = () => {      // a template gives a group: with no group there is none; its two numbers show only while it is wanted
  const f = $("#pg-kind-form"), none = f.elements.group.value === "none";
  if (none) f.elements.template.checked = false;
  f.elements.template.disabled = none;
  f.querySelectorAll("[data-tpl]").forEach(l => { l.hidden = !f.elements.template.checked; l.querySelectorAll("input").forEach(i => { i.disabled = l.hidden; }); });
};window.pgKindStale = ev => {
  pgKind.res = null; if (!$("#pg-kind-form")) return;
  if (ev && ev.target && ev.target.dataset) delete ev.target.dataset.auto;      // a value the owner touched is his, not the one chosen for him
  pgKindPort(!(ev && ev.target && ev.target.name === "core_id")); pgKindTplSync();
};
const pgKindFill = auto => {      // what the server chose is put into the details, so the owner sees it (and can change it); it is marked, so another kind clears it again
  const f = $("#pg-kind-form");
  Object.entries(auto || {}).forEach(([key, v]) => { const el = f.elements[key]; if (!el || el.disabled || key === "port" || (el.value && !el.dataset.auto)) return; el.value = v; el.dataset.auto = "1"; });
};
const pgKindBody = () => {
  const f = $("#pg-kind-form"), b = {kind: pgKind.k}, g = f.elements.group.value;
  [...f.elements].forEach(el => { if (!el.name || el.disabled || ["group", "restart", "template"].includes(el.name)) return; const v = el.type === "number" ? (el.value === "" ? "" : +el.value) : el.value.trim(); if (v !== "") b[el.name] = v; });
  b.group_mode = g === "new" ? "new" : g === "none" ? "none" : "existing"; if (g.startsWith("g")) b.group_id = +g.slice(1);
  b.template = f.elements.template.checked;
  return b;
};
/* ---- your own config: paste a client link (vless:// trojan:// vmess:// ss://) and the form is filled with its type, port, path, Host, SNI, fingerprint and the rest; the keys and ids are made new (a link holds only the public half of a REALITY key) */
const pgKindLinkKind = p => {      // the kind that has this protocol, transport and security (null = no kind for it)
  const {proto, net, sec, hdr, flow, pcs, enc} = p;
  if (proto === "ss") return "shadowsocks";
  if (proto === "vmess") return net === "ws" && sec === "tls" ? "vmess-ws-tls" : null;
  if (proto === "trojan") return net === "ws" && sec === "tls" ? "trojan-ws-tls" : net === "tcp" && sec === "tls" ? (pcs ? "trojan-tcp-pin" : "trojan-tcp-tls") : null;
  if (proto !== "vless") return null;
  if (enc && enc.startsWith("mlkem768x25519plus.")) return "vless-mlkem";
  if (sec === "reality") return net === "xhttp" ? "vless-xhttp-reality" : net === "tcp" ? (flow ? "vless-reality-vision" : "vless-reality") : null;
  if (net === "ws") return sec === "tls" ? (pcs ? "vless-ws-pin" : "vless-ws-tls") : "vless-ws";
  if (net === "xhttp") return sec === "tls" ? "vless-xhttp-tls" : "vless-xhttp";
  if (net === "httpupgrade" && sec === "tls") return "vless-httpupgrade-tls";
  if (net === "grpc" && sec === "tls") return "vless-grpc-tls";
  if (net === "tcp" && sec === "none") return hdr === "http" ? "vless-tcp-http" : null;
  return null;
};
const pgKindParseLink = text => {
  const s = String(text || "").trim(); let m;
  if ((m = /^vmess:\/\/(.+)$/i.exec(s))) {
    let j; try { j = JSON.parse(decodeURIComponent(escape(atob(m[1].replace(/\s/g, ""))))); } catch (e) { return null; }
    return {proto: "vmess", net: j.net || "tcp", sec: j.tls || "none", address: j.add, port: +j.port, path: j.path || "", host: j.host || "", sni: j.sni || "", fp: j.fp || "", name: j.ps || ""};
  }
  if ((m = /^ss:\/\/(?:[^@]+@)?([^:/?#]+):(\d+)/i.exec(s))) return {proto: "ss", net: "tcp", sec: "none", address: m[1], port: +m[2], name: decodeURIComponent((s.split("#")[1] || ""))};
  let u; try { u = new URL(s); } catch (e) { return null; }
  const proto = u.protocol.replace(":", "").toLowerCase(); if (!["vless", "trojan"].includes(proto)) return null;
  const q = k => u.searchParams.get(k) || "";
  return {proto, net: q("type") || "tcp", sec: q("security") || "none", hdr: q("headerType"), flow: q("flow"), pcs: q("pcs") || q("pinSHA256"), enc: q("encryption"), address: u.hostname, port: +u.port || 443, path: q("path"), host: q("host"), sni: q("sni").split(",")[0], snis: q("sni"), fp: q("fp"), spx: q("spx"), alpn: q("alpn"), mode: q("mode"), name: decodeURIComponent((u.hash || "").replace(/^#/, ""))};
};
window.pgKindUseLink = () => {
  const box = $("#pg-kind-link"), res = $("#pg-kind-res"), p = pgKindParseLink(box && box.value); if (!p) { toast("✕ That is not a vless:// trojan:// vmess:// or ss:// link", "bad"); return; }
  const kid = pgKindLinkKind(p); if (!kid) { toast(`✕ No kind for ${p.proto} · ${p.net} · ${p.sec} yet`, "bad"); return; }
  pgKindPick(kid); const f = $("#pg-kind-form"), set = (n, v) => { const el = f.elements[n]; if (el && !el.disabled && v !== undefined && v !== "") { el.value = v; delete el.dataset.auto; } };
  const clean = String(p.name || "").replace(/[^\w .\-]/g, " ").replace(/\s+/g, " ").trim().slice(0, 48);
  set("name", clean.length >= 3 ? clean : ""); set("address", p.address); set("port", p.port); set("path", p.path); set("host_header", p.host); set("sni", p.sni && p.sni !== p.address ? p.sni : ""); set("fingerprint", p.fp);
  set("reality_spx", p.spx); if (p.sec === "reality" && p.sni) { set("reality_dest", p.sni + ":443"); set("reality_names", p.snis || p.sni); }
  if (p.enc && p.enc.startsWith("mlkem768x25519plus.")) set("encryption", p.enc);
  pgKindPort(true);
  if (res) res.innerHTML = `<div class="pg-kind-notes"><b>Filled from the link:</b> ${esc(pgKindOf(kid).title)}, ${esc(p.address)}:${p.port}${p.path ? ", path " + esc(p.path) : ""}${p.host ? ", Host " + esc(p.host) : ""}. The keys and the user ids are new; press Check, then Make it.</div>`;
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
  ${(p.notes || []).length ? `<div class="pg-kind-notes"><b>Chosen for you:</b>${p.notes.map(n => `<div>• ${esc(n)}</div>`).join("")}</div>` : ""}
  ${p.inbound ? `<details class="pg-cores-full"><summary>The inbound as it will be stored (secrets hidden)</summary><pre class="pg-cores-pre">${esc(JSON.stringify(p.inbound, null, 2))}</pre></details>` : ""}`;
window.pgKindCheck = async () => {
  const res = $("#pg-kind-res"); if (!$("#pg-kind-form")) return null;
  res.innerHTML = '<div class="loading"><i class="spin"></i><span>Checking the steps…</span></div>';
  try { const p = await pgApi(pgKind.id, "/kinds/plan", {method: "POST", body: pgKindBody()}); pgKindFill(p.auto); res.innerHTML = pgKindStepsHtml(p); toast(p.valid ? "✓ The steps pass" : "✕ " + (p.steps.find(s => !s.ok).errors[0] || "a step does not pass"), p.valid ? "ok" : "bad"); return p; }
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
  state.build = null;
  if (typeof pgCoresSt !== "undefined") Object.assign(pgCoresSt, {list: null, d: null, dry: null, dryFor: "", notice: null});      // the Core tab reads the core again
  if (typeof pgAccess !== "undefined") Object.assign(pgAccess, {g: null, t: null});                                                // and the Access tab the groups
  pn.hosts = null; pn.hostsKey = "";
  const ok = out.checks.every(c => c.ok), inline = pgKind.inline && $("#pg-kind-page");
  const body = `<div class="pg-kind-steps">
      <div class="pg-kind-step ok"><i>✓</i><div><b>Core:</b> the inbound <b>${esc(out.tag)}</b> is in the core (backup <span class="mono">${esc(out.core.backup)}</span>)</div></div>
      ${out.group ? `<div class="pg-kind-step ok"><i>✓</i><div><b>Access:</b> the group <b>${esc(out.group.name)}</b> ${out.group.created ? "is new and holds it" : "holds it now"}</div></div>` : ""}
      ${out.template ? `<div class="pg-kind-step ok"><i>✓</i><div><b>Access:</b> the user template <b>${esc(out.template.name)}</b> (${out.template.data_limit_gb ? out.template.data_limit_gb + " GB" : "unlimited data"}, ${out.template.expire_days ? out.template.expire_days + " days" : "no end"}) gives the group</div></div>` : ""}
      <div class="pg-kind-step ok"><i>✓</i><div><b>Hosts:</b> the link <b>${esc(out.host.remark)}</b> points at ${esc(out.host.address.join(", "))}${out.host.port ? ":" + out.host.port : ""}</div></div></div>
    ${(out.notes || []).length ? `<div class="pg-kind-notes"><b>Chosen for you:</b>${out.notes.map(n => `<div>• ${esc(n)}</div>`).join("")}</div>` : ""}
    ${out.checks.map(c => `<div class="pg-kind-chk ${c.ok ? "ok" : "bad"}">${c.ok ? "✓" : "✕"} ${esc(c.what)} <span class="mut">(read back from the panel)</span></div>`).join("")}
    ${out.core.public_key ? `<div class="pg-kind-chk">REALITY public key <code class="mono">${esc(out.core.public_key)}</code> <button class="sm" onclick="navigator.clipboard.writeText('${esc(out.core.public_key)}').then(() => toast('✓ Copied', 'ok'))">Copy</button> <span class="mut">(the private key stays on the panel)</span></div>` : ""}
    <div class="pg-kind-chk ${out.restarted ? "ok" : out.restart_error ? "bad" : ""}">${out.restarted ? "✓ Xray was restarted on the nodes: it works now." : out.restart_error ? `✕ Saved, but the restart failed: ${esc(out.restart_error)}` : "The nodes use it after their next restart (Core → Restart nodes…)."}</div>
    ${out.template ? `<div class="pg-kind-chk mut">Next: a user of this kind is one choice - Users → More… → New user from a template → <b>${esc(out.template.name)}</b>.</div>` : out.group ? '<div class="pg-kind-chk mut">Next: make a user and give them the group (Users → More… → Apply a template…, or Access → Groups).</div>' : '<div class="pg-kind-chk mut">No group holds it yet: nobody gets this link until a group does (Access).</div>'}
    <div class="frm-act"><span class="sp"></span>${inline ? '<button class="pri" onclick="refresh()">Make another</button>' : '<button class="pri" onclick="closeModal();refresh()">Done</button>'}</div>`;
  if (inline) $("#pg-kind-page").innerHTML = `<h3 class="pg-kind-done">${ok ? "✓" : "⚠"} Account type made</h3>` + body;
  else showModal(`<b>${ok ? "✓" : "⚠"} Account type made</b>`, body, "wide");
}
