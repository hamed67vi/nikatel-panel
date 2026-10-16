/* PasarGuard: ONE window makes a whole KIND of account (the owner, 2026-10-15: "we only choose the kind of the account and the panel builds it" - "I only give the protocol, you do the rest").
   What was three jobs in three tabs - an inbound in Core, a group in Access, a host in Hosts - is one choice here: pick the protocol and press Make it. The name, the address, the port, the path, the keys,
   the certificate, the group, the host and a user template are chosen by the server (the details are folded away: open them to change something). Check shows the steps and what was chosen (nothing is
   written); Make it does them in order, reads each back, and takes the earlier ones back when one fails. Registers nothing in pgTabs: the button of the window sits in the bars of Access, Hosts and Core
   (pgKindBtn). Uses pg_common.js (pgApi), app.js (showModal, gconfirm, toast, esc, pn, refresh) and the cached state of pg_cores.js / pg_access.js to draw those tabs again. */
const pgKind = {id: 0, o: null, k: "", res: null};
const PG_KINDS_UI = false;      // the button "＋ Account type" is not shown in Access, Hosts and Core (the owner, 2026-10-15: "remove this completely"); the window and its routes stay: true brings the button back
window.pgKindBtn = () => !PG_KINDS_UI || pnType() !== "pasarguard" ? "" : `<button class="sm pri" onclick="pgKindNew()" title="Make a whole kind of account in one step: choose the protocol, the panel makes the inbound, the group, the host and a user template">＋ Account type</button>`;
const pgKindRand = () => Math.random().toString(36).slice(2, 8);
const pgKindShort = k => String((k && (k.short || k.title)) || "");      // the short name on the button (ws+tls); the long name is its tooltip
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
  ["file", '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4M9.5 14h5"/>'], ["upload", '<path d="M12 16V5M7.5 9.5L12 5l4.5 4.5M5 15v3.5a1.5 1.5 0 001.5 1.5h11a1.5 1.5 0 001.5-1.5V15"/>'], ["shield", '<path d="M12 3l7 3v5.2c0 4.5-3 7.9-7 9.8-4-1.9-7-5.3-7-9.8V6z"/>'], ["lock", '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/>'], ["info", '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>'],
  ["plus", '<path d="M12 5v14M5 12h14"/>'],
  /* the pictures of the drawers (the owner, 2026-10-16: "emojis in the MacBook style, black and white"): thin line icons of the kit, one colour (the text colour) */
  ["bolt", '<path d="M13.5 3L5.5 13.5h6l-1 7.5 8-10.5h-6z"/>'], ["horse", '<path d="M15 3.4l1.7 2.9c2.1 1.5 3 4.2 2.7 7.4L18.6 20.6h-8l.5-3.7c-1.2.1-2.2.5-3.3 1.2-.9.6-2 .1-2.2-.9l-.3-1.7c-.1-.9.2-1.7.9-2.3l4.2-3.5c.2-1.8 1.2-3.5 2.9-4.7z"/><path d="M12.9 8.9h.01M7.1 15.3l1.6-.4"/>'],
  ["chat", '<path d="M5.2 5.5h13.6A1.7 1.7 0 0120.5 7.2v7.6a1.7 1.7 0 01-1.7 1.7h-7.6L7 20.2v-3.7H5.2a1.7 1.7 0 01-1.7-1.7V7.2a1.7 1.7 0 011.7-1.7z"/><path d="M8.2 11h.01M12 11h.01M15.8 11h.01"/>'], ["ghost", '<path d="M5.5 20.5V10.5a6.5 6.5 0 0113 0v10l-2.2-1.7-2.1 1.7-2.2-1.7-2.2 1.7-2.1-1.7z"/><path d="M9.6 10.8h.01M14.4 10.8h.01"/>'],
  ["rocket", '<path d="M12 3c3 2.2 4.5 5.3 4.5 9v3l2 2.3v1.9l-3.3-1.2L12 21l-3.2-3-3.3 1.2v-1.9l2-2.3v-3c0-3.7 1.5-6.8 4.5-9z"/><circle cx="12" cy="10.8" r="1.6"/>'],
  ["flame", '<path d="M12 3.2c.5 3-2.9 4.6-2.9 8 0 1.1.4 2 1.1 2.6-.2-1.7.9-2.9 1.9-4 1.7 1.6 3.4 3.1 3.4 5.7a4.8 4.8 0 01-4.8 4.8 4.8 4.8 0 01-4.8-4.8c0-4.6 4.1-6.5 4.1-12.3z"/>'], ["star", '<path d="M12 3.6l2.5 5.2 5.7.8-4.1 4 1 5.7-5.1-2.7-5.1 2.7 1-5.7-4.1-4 5.7-.8z"/>'],
  ["cloud", '<path d="M7.5 18.5a4.2 4.2 0 01-.5-8.4 5.5 5.5 0 0110.6 1.2 3.7 3.7 0 01-.6 7.2z"/>'], ["wifi", '<path d="M3.5 9.6a12 12 0 0117 0M6.6 12.9a7.6 7.6 0 0110.8 0M9.6 16a3.3 3.3 0 014.8 0M12 19.2h.01"/>'],
  ["key", '<circle cx="8.5" cy="14.5" r="4"/><path d="M11.4 11.6L19.5 3.5M16 7l2.5 2.5M13.5 9.5L15 11"/>']
].forEach(([k, d]) => { PKI[k] = `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`; });
const pgKindIc = k => { const id = k.custom ? ["k", k.protocol, k.network, k.security === "tls" && k.own_cert ? "pin" : k.security, k.flow ? "vision" : "", (k.saved || {}).header === "http" ? "tcp-http-header" : ""].join("-") : String(k.id || ""); const n = /mlkem|shadowsocks/.test(id) ? "lock" : /xhttp.*reality/.test(id) ? "shield" : /xhttp.*tls|httpupgrade/.test(id) ? "upload" : /xhttp/.test(id) ? "code" : /vision/.test(id) ? "eye" : /reality/.test(id) ? "globe" : /pin/.test(id) ? "cert" : /ws/.test(id) ? "link" : /grpc/.test(id) ? "server" : /tcp.*(http|header)|http-header/.test(id) ? "file" : /tcp/.test(id) ? "lock" : "globe"; return `<i class="pg-kc-i">${PKI[n]}</i>`; };
document.addEventListener("toggle", e => { const d = e.target; if (d && d.matches && d.matches(".pg-kind-dr") && d.open) document.querySelectorAll(".pg-kind-dr[open]").forEach(o => { if (o !== d) o.open = false; }); }, true);      // (one drawer open at a time, in every browser)
const pgKindFamIcon = f => f.icon && PKI[f.icon] ? PKI[f.icon] : f.emoji ? `<span class="pg-dr-e">${esc(f.emoji)}</span>` : PKI.cube;      // the picture of a drawer: a thin line icon of the kit; an emoji of the owner's own is shown in black and white (the owner, 2026-10-16: "the MacBook style, black and white")
function pgKindDrawers(o) {      // the table: one drawer per protocol (the owner, 2026-10-15: "VLESS in a drawer"; 2026-10-16: "every protocol its own picture" and "names short, e.g. ws+tls"), one open at a time, the last row adds a protocol of your own
  const chip = k => `<button type="button" class="pg-kind-chip" data-k="${esc(k.id)}" onclick="pgKindPick('${esc(k.id)}')" title="${esc(k.title + (k.note ? " — " + k.note : ""))}">${pgKindIc(k)}<span>${esc(pgKindShort(k))}</span></button>`;
  const rows = (o.families || []).map(f => { const ks = o.kinds.filter(k => k.family === f.id); return ks.length ? `<details class="pg-kind-dr" name="pg-kind-acc" data-p="${esc(f.id)}"${f.id === "vless" ? " open" : ""}><summary><i class="pg-dr-i" aria-hidden="true">${pgKindFamIcon(f)}</i><span class="pg-dr-n">${esc(f.label)}</span><i class="pg-dr-c">${ks.length}</i></summary><div class="pg-kind-row">${ks.map(chip).join("")}</div></details>` : ""; }).join("");
  return rows + `<button type="button" class="pg-kind-addp" onclick="pgKindCustom()" title="Add a protocol of your own to this table: pick its transport and security, or paste the JSON of an inbound"><i class="pg-dr-i">${PKI.plus}</i><span>Add protocol</span></button>`;
}
function pgKindFormHtml(inline) {      // the whole form: in a window (the old button) or as the Add tab of Configs (inline: no Cancel); 1.54.7: the protocols in rows by family, one row of fields, the rest folded
  const o = pgKind.o, opt = (v, t, sel) => `<option value="${esc(v)}" ${sel ? "selected" : ""}>${esc(t)}</option>`, tls = o.tls || {};
  const chips = pgKindDrawers(o);
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
  const id = +pn.id, paint = html => { const l = $("#pg-kind-subs .pg-ks-list"); if (l) { const top = l.scrollTop; l.innerHTML = html; l.scrollTop = top; } };      // (the list keeps its place when it is drawn again after a switch, a copy or a delete)
  try { if (!pn.hosts || pn.hostsKey !== String(id)) { pn.hosts = (await api(`/api/panels/${id}/hosts`)).hosts; pn.hostsKey = String(id); } } catch (e) { paint(`<div class="err">${esc(e.message)}</div>`); return; }
  const hs = pn.hosts || [], h = $("#pg-kind-subs .pg-ks-h"); if (!h) return;
  h.querySelectorAll(".pg-dr-c").forEach(n => n.remove()); h.insertAdjacentHTML("beforeend", `<i class="pg-dr-c">${hs.length}</i>`);
  paint(hs.length ? hs.map(pgKindRow).join("") : '<div class="mut pg-ks-none">No config yet: make an account type and its link shows here.</div>');
};
/* one subscription config = one host (the owner, 2026-10-16: "for every config an on/off switch, edit and delete", and his picture of the menu: Modify, Disable, Duplicate, a line, Remove in red). The buttons carry the number of the host, never its name */
const pgKindRow = x => `<div class="pg-ks-r${x.disabled ? " off" : ""}" data-k="${esc(x.key)}" title="${esc(`${x.remark} · ${x.address}${x.port ? ":" + x.port : ""} · ${x.tag}`)}"><i class="pg-kc-i">${PKI.link}</i><span class="pg-ks-t"><b>${esc(x.remark || "(no name)")}</b><small>${esc(x.address || "—")}${x.port ? ":" + esc(x.port) : ""} · ${esc(x.tag || "—")}</small></span>${pgSw(!x.disabled, "pgKindHostSw(this)", 'title="On / off: the config is left out of the links of the users while it is off"')}<button type="button" class="lab-kb" onclick="pgKindHostMenu(this, event)" aria-haspopup="menu" aria-label="Actions of this config" title="Modify, disable, duplicate, remove">${LAB_DOTS}</button></div>`;
const pgKindHostOf = el => { const r = el && el.closest ? el.closest(".pg-ks-r") : null; return r ? (pn.hosts || []).find(x => String(x.key) === r.dataset.k) || null : null; };
const pgKindHostsAgain = () => { pn.hosts = null; return pgKindSubs(); };      // the hosts are read again and only this list is drawn (the form on the left keeps what was typed)
window.pgKindHostSw = async box => {
  const x = pgKindHostOf(box), on = box.checked; if (!x) return;
  const row = box.closest(".pg-ks-r"); row.classList.toggle("off", !on); box.closest("label").classList.toggle("on", on); box.disabled = true;      // (the row follows the switch at once; it is put back when the panel refuses)
  try { await api(`/api/panels/${+pn.id}/hosts-toggle`, {method: "POST", body: JSON.stringify({keys: [x.key], disable: !on})}); x.disabled = !on; toast(`✓ ${x.remark || "The config"} is ${on ? "on" : "off"}`, "ok"); }
  catch (e) { box.checked = !on; row.classList.toggle("off", on); box.closest("label").classList.toggle("on", !on); toast("✕ " + e.message, "bad"); }
  box.disabled = false;
};
window.pgKindHostMenu = (btn, ev) => {
  const x = pgKindHostOf(btn); if (!x) return;
  nkMenu(btn, [labIt("Modify", "edit", () => pgHostOpen(+pn.id, +x.key)), labIt(x.disabled ? "Enable" : "Disable", x.disabled ? "on" : "off", () => pgKindHostSet(x, !!x.disabled)), labIt("Duplicate", "copy", () => pgHostClone(+pn.id, +x.key)),
    labSep, labIt("Remove", "trash", () => pgHostDelete(+pn.id, +x.key, x.remark || "#" + x.key), {danger: true})], ev);
};
const pgKindHostSet = async (x, on) => {
  try { await api(`/api/panels/${+pn.id}/hosts-toggle`, {method: "POST", body: JSON.stringify({keys: [x.key], disable: !on})}); toast(`✓ ${x.remark || "The config"} is ${on ? "on" : "off"}`, "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
  pgKindHostsAgain();
};
window.pgKindHostsDone = () => { if (!$("#pg-kind-subs")) return false; pgKindHostsAgain(); return true; };      // pg_hosts.js asks this after a save, a copy, a delete: on the Add tab only this list is read again, not the whole page
window.pgKindPick = k => {
  const f = $("#pg-kind-form"); if (!f) return;
  pgKind.k = k; pgKind.res = null;
  document.querySelectorAll(".pg-kind-chip").forEach(b => b.classList.toggle("on", b.dataset.k === k));
  const kd = pgKindOf(k), nt = $("#pg-kind-note");
  if (nt) nt.innerHTML = `<span class="pg-kn-t">${esc(kd.note || (kd.custom ? kd.title : ""))}</span>` + (kd.custom ? `<span class="pg-kn-a"><button type="button" class="sm" onclick="pgKindCustom(pgKind.k)">Edit</button><button type="button" class="sm danger" onclick="pgKindCustomDel(pgKind.k)">Delete</button></span>` : "");      // (a protocol of your own: Edit and Delete sit on its note)
  f.querySelectorAll("[data-need]").forEach(l => { const on = l.dataset.need.split(" ").some(n => kd.needs.includes(n)); l.hidden = !on; l.querySelectorAll("input, select, button").forEach(i => { i.disabled = !on; }); });
  f.querySelectorAll("[data-sec]").forEach(l => { const on = kd.security !== "none"; l.hidden = !on; l.querySelectorAll("select, input").forEach(i => { i.disabled = !on; }); });
  f.querySelectorAll("[data-auto]").forEach(i => { i.value = ""; delete i.dataset.auto; });      // what the last check chose for another kind goes: this kind is chosen again
  document.querySelectorAll(".pg-kind-dr").forEach(d => { const own = kd.family === d.dataset.p; if (own) d.open = true; });      // the drawer of the chosen kind opens (the others shut); it does not repeat the name of the kind (the owner, 2026-10-16: the REALITY beside VLESS is gone)
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
const pgKindLinkOwn = p => { const proto = p.proto === "ss" ? "shadowsocks" : p.proto, k = ((pgKind.o || {}).kinds || []).find(x => x.custom && x.protocol === proto && x.network === (p.net || "tcp") && x.security === (p.sec || "none") && !!x.flow === !!p.flow); return k ? k.id : null; };      // a protocol of your own that has this protocol, transport and security
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
  const kid = pgKindLinkKind(p) || pgKindLinkOwn(p); if (!kid) { toast(`✕ No kind for ${p.proto} · ${p.net} · ${p.sec} yet: add it to the table with ＋ Add protocol`, "bad"); return; }
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
/* ---- a protocol of your own in the table (the owner, 2026-10-16: "let the user add other protocols to the table too"): the row "＋ Add protocol" opens this form. A protocol of your own is made with the BUILDER (protocol, transport,
   security ... : the same rules as a built-in kind) or with the JSON of an inbound (for what the builder does not make); it is a button in the drawer of its group, works like a built-in kind, and has Edit and Delete on its note.
   It is kept on this server (Settings), for every panel. A new group is a new drawer with a name and an emoji of its own. */
const PG_CUST_FAMS = ["vless", "vmess", "trojan", "shadowsocks"], PG_CUST_ICONS = ["bolt", "horse", "chat", "ghost", "rocket", "flame", "star", "cloud", "wifi", "key", "shield", "globe", "lock", "cube", "spark"];      // (the icons a drawer of your own can have: the same list as ICONS of services/pg_kinds.py)
const PG_CUST_USUAL = {none: "80, 8080, 2082", tls: "443, 8443, 2053", "tls-pin": "32001, 32002, 32003", reality: "443, 8443, 2053", shadowsocks: "8388, 2375, 9443"};
const pgCustSample = '{\n  "protocol": "vless",\n  "settings": {"clients": [], "decryption": "none"},\n  "streamSettings": {"network": "tcp", "security": "none"}\n}';
window.pgKindCustom = kid => {
  const o = pgKind.o; if (!o || !$("#pg-kind-form")) return;
  const s = kid ? pgKindOf(kid).saved : null; if (kid && !s) return;
  const cur = s || {}, fam0 = cur.family || pgKindOf(pgKind.k).family || "vless", via = cur.via || "builder", sec0 = cur.security === "tls" && cur.cert === "self" ? "tls-pin" : cur.security || "none";
  pgKind.cust = {id: s ? s.id : "", via};
  const opt = (v, t, on) => `<option value="${esc(v)}"${on ? " selected" : ""}>${esc(t)}</option>`, val = k => esc(cur[k] == null ? "" : cur[k]), famLabel = (o.families.find(f => f.id === cur.family) || {}).label;
  const ownTitle = cur.title && cur.title !== `${famLabel} · ${cur.short}` ? esc(cur.title) : "";      // (the full name made from the group and the short name is not shown as if it were typed: it follows them when they change)
  const sel = (name, list, now) => `<select name="${name}" data-dd>${list.map(([v, t]) => opt(v, t, v === now)).join("")}</select>`;
  const body = `<form class="pg-kind-form pg-cust" id="pg-cust-form" onsubmit="return pgCustSave(event)" onchange="pgCustSync()"><div class="pg-kind-quick">
      <label>Group <small>the drawer it goes into</small><select name="family" data-dd>${o.families.map(f => opt(f.id, `${f.emoji} ${f.label}`, f.id === fam0)).join("")}${opt("", "＋ A new group…", false)}</select></label>
      <label data-nf>New group <small>its name</small><input name="family_label" maxlength="24" placeholder="Hysteria2" autocomplete="off"></label>
      <div class="pg-cust-ico wide" data-nf><span>Picture <small>one of these icons, or an emoji of your own (shown in black and white)</small></span><div class="pg-icos">${PG_CUST_ICONS.map(k => `<button type="button" class="pg-ico${k === "cube" ? " on" : ""}" data-i="${k}" onclick="pgCustIcon('${k}')" title="${k}" aria-label="${k}">${PKI[k]}</button>`).join("")}<input name="family_emoji" maxlength="12" placeholder="or an emoji" autocomplete="off" oninput="pgCustIcon('')" aria-label="An emoji of your own"></div><input type="hidden" name="family_icon" value="cube"></div>
      <label>Short name <small>on the button, like ws+tls</small><input name="short" maxlength="24" value="${val("short")}" placeholder="grpc+reality" autocomplete="off"></label>
      <div class="pg-cust-via wide"><span>Made with</span><div class="tabs pn-mode" role="tablist"><button type="button" role="tab" data-via="builder" class="${via === "builder" ? "on" : ""}" onclick="pgCustVia('builder')">Builder</button><button type="button" role="tab" data-via="json" class="${via === "json" ? "on" : ""}" onclick="pgCustVia('json')">JSON</button></div></div>
      <label data-vf="builder">Protocol${sel("protocol", [["vless", "VLESS"], ["vmess", "VMess"], ["trojan", "Trojan"], ["shadowsocks", "Shadowsocks"]], cur.protocol || fam0)}</label>
      <label data-vf="builder">Transport${sel("network", [["tcp", "TCP"], ["ws", "WebSocket"], ["grpc", "gRPC"], ["httpupgrade", "HTTPUpgrade"], ["xhttp", "xHTTP"]], cur.network || "tcp")}</label>
      <label data-vf="builder">Security${sel("security", [["none", "none"], ["tls", "TLS (certificate files)"], ["tls-pin", "TLS (own certificate, pinned)"], ["reality", "REALITY"]], sec0)}</label>
      <label data-vf="builder" data-row="flow">Flow${sel("flow", [["", "none"], ["xtls-rprx-vision", "xtls-rprx-vision"]], cur.flow || "")}</label>
      <label data-vf="builder" data-row="header">HTTP header${sel("header", [["", "none"], ["http", "http (a path)"]], cur.header || "")}</label>
      <label data-vf="builder" data-row="xmode">xHTTP mode${sel("xmode", [["auto", "auto"], ["packet-up", "packet-up"], ["stream-up", "stream-up"], ["stream-one", "stream-one"]], cur.xmode || "auto")}</label>
      <label data-vf="builder">Usual ports <small>the first free one is offered</small><input name="ports" value="${esc((cur.ports || []).join(", "))}" autocomplete="off"></label>
      <label class="wide" data-vf="json">Inbound JSON <small>ONE inbound; its tag and port are put in when an account is made. No secret in it</small><textarea name="text" rows="9" spellcheck="false" oninput="pgHAdvCheck(this)" placeholder='{"protocol": "...", "settings": {...}, "streamSettings": {...}}'>${esc(cur.text ? JSON.stringify(JSON.parse(cur.text), null, 2) : "")}</textarea><small class="err pgh-e" hidden></small><button type="button" class="sm pg-cust-ex" onclick="pgCustExample()">Example</button></label>
      <label class="wide">Full name <small>empty = the group, then the short name</small><input name="title" maxlength="80" value="${ownTitle}" autocomplete="off"></label>
      <label class="wide">Note <small>shown over the form when this button is chosen</small><input name="note" maxlength="200" value="${val("note")}" autocomplete="off"></label>
    </div><div id="pg-cust-err"></div>
    <div class="frm-act"><button class="pri" type="submit" id="pg-cust-go">Save</button><button type="button" onclick="closeModal()">Cancel</button><span class="sp"></span>${s ? '<button type="button" class="danger" onclick="pgKindCustomDel(pgKind.cust.id)">Delete</button>' : ""}</div></form>`;
  showModal(`<b>${s ? "Edit protocol" : "＋ Add protocol"}</b>`, body, "wide pg-cust-win"); pgCustSync();
};
window.pgCustIcon = k => {      // a picture for a new drawer: an icon of the list (the emoji box is emptied) or none (the owner is typing an emoji)
  const f = $("#pg-cust-form"); if (!f) return;
  f.elements.family_icon.value = k; if (k) f.elements.family_emoji.value = "";
  f.querySelectorAll(".pg-ico").forEach(b => b.classList.toggle("on", b.dataset.i === k));
};
window.pgCustVia = v => { pgKind.cust.via = v; document.querySelectorAll("#pg-cust-form .pg-cust-via button").forEach(b => b.classList.toggle("on", b.dataset.via === v)); pgCustSync(); };
window.pgCustExample = () => { const t = $("#pg-cust-form textarea[name=text]"); if (t) { t.value = pgCustSample; pgHAdvCheck(t); } };
window.pgCustSync = () => {      // what the form shows follows what is chosen: a group of the four holds its own protocol only; flow, header and xHTTP mode appear where they can be used
  const f = $("#pg-cust-form"); if (!f) return;
  const e = f.elements, via = pgKind.cust.via, fam = e.family.value, builtin = PG_CUST_FAMS.includes(fam);
  f.querySelectorAll("[data-nf]").forEach(l => { l.hidden = fam !== ""; });
  f.querySelectorAll("[data-vf]").forEach(l => { l.hidden = l.dataset.vf !== via; });
  if (builtin) e.protocol.value = fam; e.protocol.disabled = builtin;
  const proto = e.protocol.value, net = e.network.value, sec = e.security.value, show = (n, on) => { const l = f.querySelector(`[data-row="${n}"]`); if (!l) return; l.hidden = via !== "builder" || !on; if (!on) e[n].value = n === "xmode" ? "auto" : ""; };
  show("flow", proto === "vless" && net === "tcp" && sec !== "none"); show("header", net === "tcp"); show("xmode", net === "xhttp");
  e.ports.placeholder = PG_CUST_USUAL[proto === "shadowsocks" ? "shadowsocks" : sec];
};
window.pgCustSave = async ev => {
  if (ev) ev.preventDefault();
  const f = $("#pg-cust-form"); if (!f) return false;
  const g = n => (f.elements[n] ? String(f.elements[n].value) : "").trim(), via = pgKind.cust.via, err = $("#pg-cust-err"), go = $("#pg-cust-go");
  const b = {id: pgKind.cust.id, family: g("family"), family_label: g("family_label"), family_emoji: g("family_emoji"), family_icon: g("family_icon"), short: g("short"), title: g("title"), note: g("note"), via};
  if (via === "builder") Object.assign(b, {protocol: g("protocol"), network: g("network"), security: g("security"), flow: g("flow"), header: g("header"), xmode: g("xmode"), ports: g("ports")}); else b.text = f.elements.text.value;
  if (!b.short) { err.innerHTML = '<div class="err">Write the short name first (like ws+tls)</div>'; return false; }
  if (f.querySelector("textarea.bad")) { err.innerHTML = '<div class="err">Fix the JSON first</div>'; return false; }
  go.disabled = true; err.innerHTML = "";
  try {
    const r = await pgApi(pgKind.id, "/kinds/custom", {method: "POST", body: b});
    Object.assign(pgKind.o, {kinds: r.kinds, families: r.families}); closeModal(); pgKindRedraw(r.id); toast(`✓ ${b.short} is in the table`, "ok");
  } catch (x) { err.innerHTML = `<div class="err">${esc(x.message)}</div>`; go.disabled = false; }
  return false;
};
window.pgKindCustomDel = async kid => {
  const k = pgKindOf(kid); if (!k.custom) return;
  if (!await gconfirm(`Take “${k.short}” out of the table? The accounts already made from it stay in the panel.`, {danger: true})) return;
  try {
    const r = await pgApi(pgKind.id, `/kinds/custom/${encodeURIComponent(kid)}`, {method: "DELETE"});
    Object.assign(pgKind.o, {kinds: r.kinds, families: r.families}); closeModal(); pgKindRedraw(pgKind.k === kid ? "vless-reality" : pgKind.k); toast(`✓ ${k.short} is out of the table`, "ok");
  } catch (e) { toast("✕ " + e.message, "bad"); }
};
const pgKindRedraw = sel => {      // the drawers are drawn again from the new table (the form beside them keeps what was typed) and a kind is chosen
  const g = $("#pg-kind-page .pg-kind-groups"); if (!g) return;
  g.innerHTML = pgKindDrawers(pgKind.o);
  pgKindPick(pgKindOf(sel).id ? sel : "vless-reality");
};
