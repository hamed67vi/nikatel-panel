/* Nikatel panel front-end: plain JS, one function per page, data from /api. */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const ago = ts => { if (!ts) return "—"; const d = Math.max(0, Date.now() / 1000 - ts); return d < 60 ? Math.round(d) + "s" : d < 3600 ? Math.round(d / 60) + "m" : d < 86400 ? Math.round(d / 3600) + "h" : Math.round(d / 86400) + "d"; };
const when = ts => ts ? new Date(ts * 1000).toLocaleString(undefined, {hour12: false}) : "—";
const mbps = v => v == null ? "—" : v >= 1000 ? (v / 1000).toFixed(1) + " Gb/s" : v.toFixed(1) + " Mb/s";
const pct = (v, warn = 80, bad = 92) => v == null ? "—" : `<span class="bar"><i class="${v >= bad ? "bad" : v >= warn ? "warn" : ""}" style="width:${Math.min(100, v)}%"></i></span>${v}%`;

async function api(path, opts = {}) {
  const r = await fetch(path, Object.assign({headers: {"Content-Type": "application/json"}}, opts));
  let body = null;
  try { body = await r.json(); } catch (e) { /* no json body */ }
  if (r.status === 401) { showLogin(); throw new Error("not logged in"); }
  if (!r.ok) {
    const rid = r.headers.get("X-Request-Id");
    let msg = (body && (body.detail || body.error)) || "HTTP " + r.status;
    if (Array.isArray(msg)) msg = msg.map(x => (x.loc || []).slice(-1)[0] + ": " + x.msg).join("; ");
    throw new Error(msg + (rid && r.status >= 500 ? " · id " + rid : ""));
  }
  return body;
}

let toastT;
function toast(text, cls) { const t = $("#toast"); t.textContent = text; t.className = "toast " + (cls || ""); clearTimeout(toastT); toastT = setTimeout(() => t.classList.add("hidden"), 3500); }

/* a tiny inline chart: values -> svg polyline, optional threshold colouring */
function spark(values, opts = {}) {
  const v = values.filter(x => x != null);
  if (v.length < 2) return '<span class="mut">not enough data yet</span>';
  const w = opts.w || 220, h = opts.h || 36, max = opts.max != null ? opts.max : Math.max(...v) * 1.05 || 1, min = opts.min != null ? opts.min : 0;
  const pts = v.map((x, i) => `${(i / (v.length - 1) * w).toFixed(1)},${(h - (Math.min(max, Math.max(min, x)) - min) / (max - min) * h).toFixed(1)}`).join(" ");
  const last = v[v.length - 1];
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" preserveAspectRatio="none"><polyline points="${pts}"/></svg><b class="spark-v">${opts.fmt ? opts.fmt(last) : last}</b>`;
}
function strip(flags) {   /* up/down history as a strip of small blocks */
  if (!flags.length) return '<span class="mut">no checks yet</span>';
  return `<span class="strip">${flags.slice(-120).map(f => `<i class="${f ? "ok" : "bad"}"></i>`).join("")}</span>`;
}

/* ---- auth */
function showLogin() { $("#login").classList.remove("hidden"); $("#app").classList.add("hidden"); }
function showApp() { $("#login").classList.add("hidden"); $("#app").classList.remove("hidden"); }

/* sign-in has three steps: password -> (authenticator code) ; or "forgot the password" */
const ls = {step: "pass", ticket: ""};
function loginStep(s) {
  ls.step = s;
  for (const k of ["pass", "2fa", "recover"]) $("#ls-" + k).classList.toggle("hidden", k !== s);
  $("#login-err").textContent = "";
  const first = $("#ls-" + s + " input:not([type=hidden])"); if (first) setTimeout(() => first.focus(), 30);
}
$("#to-recover").onclick = async () => { loginStep("recover"); try { const o = await fetch("/api/auth/recovery-options").then(r => r.json()); $("#rc-tg").classList.toggle("hidden", !o.telegram); } catch (e) { /* offline */ } };
document.querySelectorAll("#login-form [data-back]").forEach(a => a.onclick = () => { ls.ticket = ""; loginStep("pass"); });
$("#rc-tg").onclick = async () => { try { await api("/api/auth/recover/telegram", {method: "POST"}); $("#login-err").textContent = "A code was sent to your Telegram (valid 10 minutes)."; } catch (err) { $("#login-err").textContent = err.message; } };
$("#login-form").onsubmit = async e => {
  e.preventDefault();
  const f = new FormData(e.target), err = $("#login-err");
  err.textContent = "";
  try {
    if (ls.step === "pass") {
      const r = await api("/api/auth/login", {method: "POST", body: JSON.stringify({user: f.get("user"), password: f.get("password")})});
      if (r.need_2fa) { ls.ticket = r.ticket; loginStep("2fa"); return; }
    } else if (ls.step === "2fa") {
      await api("/api/auth/login/2fa", {method: "POST", body: JSON.stringify({ticket: ls.ticket, code: f.get("code")})});
    } else {
      if (f.get("rnew") !== f.get("rnew2")) { err.textContent = "the two passwords differ"; return; }
      await api("/api/auth/recover", {method: "POST", body: JSON.stringify({code: f.get("rcode"), new: f.get("rnew")})});
      loginStep("pass"); err.textContent = "✓ The password is changed. Sign in with the new one."; err.style.color = "var(--ok)"; return;
    }
    ls.ticket = ""; e.target.reset(); loginStep("pass");
    showApp(); go(state.page);
  } catch (er) { err.style.color = ""; err.textContent = er.message; }
};
$("#logout").onclick = async () => { await api("/api/auth/logout", {method: "POST"}); showLogin(); };

/* ---- router */
const state = {page: localStorage.getItem("nk.page") || "dashboard", tab: localStorage.getItem("nk.tab") || "iran", q: "", open: null, edit: null, logs: {level: "", domain: "", q: ""}};
const PAGES = {};
const INFO_GW = "A plain Xray on an Iran server: users reach a port there, the traffic goes on to your foreign node — directly while that works, through a borrowed config when Iran is cut off.";
const INFO = {
  dashboard: ["Dashboard", "What needs attention right now, and what happened."],
  nodes: ["Nodes", "Your Iran and foreign servers with their resources, the Iran gateway, events, jobs and logs."],
  servers: ["Servers", "Every Iran and foreign server: reachable, load, memory, disk. Sampled every 2 minutes over SSH."],
  tunnels: ["Tunnels", "Every tunnel between an Iran server and a foreign server: service units, GRE link, peer ping, forwarded ports. Checked every minute."],
  events: ["Events", "One plain sentence per thing that happened. Newest first."],
  jobs: ["Jobs", "The background jobs of the panel and whether they succeed."],
  logs: ["Logs", "The panel's own log, newest first. Secrets are masked before they are written."],
  settings: ["Settings", "Account, Telegram and backup, the probe, appearance and about, AI and automation."],
};

/* top-level pages and their sub-tabs: the sidebar shows the page, the bar under the header the sub-tab */
const NAV_ALIAS = {servers: "nodes", gateway: "nodes", monitor: "nodes", actions: "tunnels", events: "nodes", jobs: "nodes", logs: "nodes", panels: "configs"};
if (state.sub && state.sub.nodes === "monitor") state.sub.nodes = "iran";
const SUB = {
  nodes: [["iran", "🇮🇷 Iran", "Iran servers: reachable, load, memory, disk. Sampled every 2 minutes over SSH."], ["foreign", "🌍 Foreign", "Foreign servers: reachable, load, memory, disk. Sampled every 2 minutes over SSH."], ["gateway", "🌐 Gateway & outbounds", INFO_GW]],
  tunnels: [["list", "⇄ Tunnel manager", "Every tunnel between an Iran and a foreign server: units, GRE link, peer ping, forwarded ports. Checked every minute, grouped by type. Create a new one with the button."], ["test", "🧪 Tunnel test", "Before you build: pick two servers; the panel builds a temporary tunnel, measures latency, jitter, loss and speed, then removes it."], ["repairs", "🩺 Smart repair", "Finds why a tunnel is down and fixes it, and says which tunnel is best on today's network."]],
  configs: [["configs", "⚡ Configs", "Every client config the probe keeps alive and tests from inside Iran: reachable, ping, daily speed. Add links or import a subscription."], ["panels", "🧩 Panels", "Your Marzban and PasarGuard panels: status, nodes, users, hosts and core outbounds. Each panel can keep a test config on the probe."]],
};
state.sub = JSON.parse(localStorage.getItem("nk.sub") || "{}");
function sub(page) { const list = SUB[page]; if (!list) return ""; const cur = state.sub[page]; return list.some(x => x[0] === cur) ? cur : list[0][0]; }
function renderSub() {
  const list = SUB[state.page], box = $("#subnav");
  box.classList.toggle("hidden", !list);
  if (!list) return;
  const cur = sub(state.page);
  box.innerHTML = `<div class="tabs">${list.map(([k, label]) => `<button class="${k === cur ? "on" : ""}" onclick="setSub('${state.page}', '${k}')">${label}</button>`).join("")}</div>`;
  $("#subtitle").textContent = list.find(x => x[0] === cur)[2];
}
window.setSub = (page, k) => { state.sub[page] = k; localStorage.setItem("nk.sub", JSON.stringify(state.sub)); state.open = null; state.edit = null; state.build = null; state.q = ""; refresh(); };
function go(page) {
  if (NAV_ALIAS[page]) {               // an old page name: open the page that holds it, on the right sub-tab
    const target = NAV_ALIAS[page];
    if ((SUB[target] || []).some(x => x[0] === page)) state.sub[target] = page;
    page = target;
  }
  if (!PAGES[page]) page = "dashboard";
  state.page = page; state.open = null; state.edit = null; state.q = ""; localStorage.setItem("nk.page", page);
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("on", a.dataset.page === page));
  $("#title").textContent = INFO[page][0]; $("#subtitle").textContent = INFO[page][1];
  $("#subnav").classList.toggle("hidden", !SUB[page]);
  refresh();
}
PAGES.nodes = async () => {
  renderSub();
  const s = sub("nodes");
  if (s === "gateway") return PAGES.gateway();
  state.tab = s;
  return PAGES.servers(false);
};
document.querySelectorAll("#nav a").forEach(a => a.onclick = () => go(a.dataset.page));


async function refresh() {
  try {
    await PAGES[state.page]();
    $("#updated").textContent = "updated " + new Date().toLocaleTimeString(undefined, {hour12: false});
  } catch (e) { if (e.message !== "not logged in") toast("✕ " + e.message, "bad"); }
}
setInterval(() => { if (!document.hidden && !$("#app").classList.contains("hidden") && !state.edit && !state.open && !state.build && !state.detect && !cf.open && !cf.edit && !cf.add && !cf.sub && !cf.subEdit && !document.activeElement.matches("input, textarea, select")) refresh(); }, 15000);
/* the glass window: one thing opened from a row or a card; closed with ✕, Esc or a click outside */
function showModal(title, body, cls = "") { let m = $("#modal"); if (!m) { m = document.createElement("div"); m.id = "modal"; document.body.appendChild(m); } m.innerHTML = `<div class="overlay" onmousedown="if(event.target===this)closeModal()"><div class="modal ${cls}"><div class="modal-head">${title}<span class="sp"></span><button class="sm" onclick="closeModal()">✕ Close</button></div><div class="modal-body">${body}</div></div></div>`; }
function hideModal() { const m = $("#modal"); if (m) m.innerHTML = ""; }
/* glass confirm / prompt dialogs (replace the browser's own boxes). They sit above every window. */
const ASK_VERB = /^(Remove|Delete|Reboot|Forget|Update|Install|Restart|Import|Repair|Rebuild|Point|Dump|Clear|Put|Copy|Speed test|Add)\b/i;
function glassAsk(text, {input = false, value = "", danger, password = false} = {}) {
  return new Promise(resolve => {
    const old = $("#ask"); if (old) old.remove();
    const el = document.createElement("div"); el.id = "ask";
    const verb = (String(text).match(ASK_VERB) || [""])[0], bad = danger != null ? danger : /^(Remove|Delete|Reboot|Forget|Rebuild|Clear|Import)\b/i.test(text) || /cannot be undone|type [A-Z]+ to confirm/i.test(text);
    const label = input ? "OK" : verb ? verb[0].toUpperCase() + verb.slice(1).toLowerCase() : "OK";
    el.innerHTML = `<div class="overlay ask-ov"><div class="modal ask-box"><div class="ask-ic ${bad ? "bad" : ""}">${input ? "✎" : bad ? "⚠" : "?"}</div><div class="ask-text">${esc(text)}</div>${input ? `<input class="ask-in" type="${password ? "password" : "text"}" value="${esc(value)}" autocomplete="${password ? "current-password" : "off"}" spellcheck="false">` : ""}<div class="ask-act"><button class="ask-no">Cancel</button><button class="ask-yes ${bad ? "danger-fill" : "pri"}">${esc(label)}</button></div></div></div>`;
    document.body.appendChild(el);
    const inp = $(".ask-in", el), done = v => { document.removeEventListener("keydown", key, true); el.remove(); resolve(v); };
    const yes = () => done(input ? inp.value : true), no = () => done(input ? null : false);
    function key(e) { if (e.key === "Escape") { e.stopPropagation(); no(); } else if (e.key === "Enter" && (input || e.target.tagName !== "BUTTON")) { e.preventDefault(); e.stopPropagation(); yes(); } }
    document.addEventListener("keydown", key, true);
    $(".ask-yes", el).onclick = yes; $(".ask-no", el).onclick = no;
    $(".ask-ov", el).onmousedown = e => { if (e.target.classList.contains("ask-ov")) no(); };
    setTimeout(() => { if (inp) { inp.focus(); inp.select(); } else $(".ask-yes", el).focus(); }, 30);
  });
}
window.gconfirm = (text, o) => glassAsk(text, o);
window.gprompt = (text, value = "", o = {}) => glassAsk(text, Object.assign({input: true, value}, o));
window.closeModal = () => {
  const quiet = (state.page === "nodes" || state.page === "tunnels") && !state.edit && !state.build && !state.detect;   // just close: the list behind is still current
  state.open = null; state.edit = null; state.con = null; state.flt = null; state.wtab = null; state.build = null; state.detect = null; cf.open = null; cf.edit = null; cf.subEdit = null; cf.add = null; if (!cf.edit) cf.sub = null;
  hideModal();
  if (!quiet) refresh();
};
document.addEventListener("keydown", e => { if (e.key === "Escape" && $("#modal") && $("#modal").innerHTML) closeModal(); });

/* ---- dashboard */
PAGES.dashboard = async () => {
  const [o, ev, cfg, srv, tl] = await Promise.all([api("/api/overview"), api("/api/events?limit=40"), api("/api/configs").catch(() => ({configs: []})), api("/api/servers").catch(() => []), api("/api/tunnels").catch(() => [])]);
  $("#version").textContent = "v" + o.version; $("#ro-badge").classList.toggle("hidden", !o.read_only);
  $("#n-servers").textContent = o.servers.total; $("#n-tunnels").textContent = o.tunnels.total; $("#n-configs").textContent = cfg.configs.length;
  const cUp = cfg.configs.filter(c => c.status === "up").length, foreign = srv.filter(s => s.role === "foreign"), checked = foreign.filter(s => (s.extra || {}).iran_filter);
  const clean = checked.filter(s => s.extra.iran_filter.level === "clean"), bad = checked.filter(s => ["filtered", "blocked"].includes(s.extra.iran_filter.level));
  const dot = cls => `<i class="dash-dot ${cls}"></i>`;
  const hrow = (name, sub, val, cls) => `<div class="dash-row">${dot(cls)}<div class="dash-name">${name}<small>${sub}</small></div><b class="${cls === "red" ? "err" : cls === "green" ? "gw-ok" : ""}">${val}</b></div>`;
  const q = (icon, title, sub, page, subtab) => `<a class="dash-action" onclick="go('${page}')${subtab ? `;setSub('${page}','${subtab}')` : ""}"><i>${icon}</i><b>${title}</b><small>${sub}</small></a>`;
  const quick = `<div class="dash-quick">${q("⚡", "Ping and speed tests", "every config, from inside Iran", "configs", "configs")}${q("🛰", "Check filtering in Iran", "is a foreign server reachable?", "nodes", "foreign")}${q("🖥", "Manage Iran nodes", "resources, console, tools", "nodes", "iran")}${q("⇄", "Create and monitor", "tunnels between your servers", "tunnels", "list")}</div>`;
  $("#page").innerHTML = `${quick}
    <div class="dash-cols"><div class="card health"><h3>Infrastructure health</h3>
      ${hrow("Iran ↔ Foreign tunnels", o.tunnels.down ? o.tunnels.down + " down" : (o.tunnels.degraded ? o.tunnels.degraded + " degraded" : "all carrying users"), `${o.tunnels.up} / ${o.tunnels.total}`, o.tunnels.down ? "red" : o.tunnels.degraded ? "yellow" : "green")}
      ${hrow("SSH connected nodes", o.servers.offline ? o.servers.offline + " offline" : o.servers.iran + " Iran · " + o.servers.foreign + " foreign", `${o.servers.online} / ${o.servers.total}`, o.servers.offline ? "red" : "green")}
      ${hrow("VLESS / VMess connectivity", cfg.configs.length - cUp ? (cfg.configs.length - cUp) + " down on the probe" : "all answer from inside Iran", `${cUp} / ${cfg.configs.length}`, cfg.configs.length && cUp === cfg.configs.length ? "green" : cUp ? "yellow" : cfg.configs.length ? "red" : "")}
      ${hrow("Reachable from Iran", "clean filter check", `${clean.length} / ${foreign.length}`, bad.length ? "red" : clean.length === foreign.length && foreign.length ? "green" : "yellow")}
      ${hrow("Last filter checks", checked.length ? checked.sort((a, b) => b.extra.iran_filter.checked_at - a.extra.iran_filter.checked_at).slice(0, 3).map(s => esc(s.name) + (s.extra.iran_filter.level === "clean" ? " ✓" : " ✕")).join(" · ") : "none yet", `${bad.length} filtered`, bad.length ? "red" : checked.length ? "green" : "")}
      ${hrow("Background jobs", o.jobs.failing.length ? "failing: " + esc(o.jobs.failing.join(", ")) : o.jobs.stale.length ? "stale: " + esc(o.jobs.stale.join(", ")) : "all running", `${o.jobs.total}`, o.jobs.failing.length ? "red" : o.jobs.stale.length ? "yellow" : "green")}</div>
    <div class="card act ${state.actBig ? "big" : ""}"><h3>Activity <span class="sp"></span><a class="mut" onclick="actBig()" title="${state.actBig ? "Same height as the health box" : "Taller log box"}">${state.actBig ? "⤡ Shrink" : "⤢ Expand"}</a></h3><div class="act-list">${eventsHtml(ev.events)}</div></div></div>`;
  const h = $("#page .card.health"), a = $("#page .card.act");
  if (h && a && !state.actBig) a.style.height = h.offsetHeight + "px";   // the log box lines up with the health box
};
state.actBig = localStorage.getItem("nk.actbig") === "1";
window.actBig = () => { state.actBig = !state.actBig; localStorage.setItem("nk.actbig", state.actBig ? "1" : "0"); refresh(); };
function eventsHtml(list) {
  if (!list.length) return '<div class="empty">Nothing happened yet.</div>';
  return list.map(e => `<div class="ev ${e.level}"><time title="${when(e.ts)}">${ago(e.ts)} ago</time><i></i><small>${esc(e.domain)}</small><span>${esc(e.text)}</span></div>`).join("");
}

/* ---- servers */
const SERVER_FORM = (s, role) => `<form class="frm" onsubmit="return saveServer(event, ${s ? s.id : "null"})">
    <label>Name<input name="name" required pattern="[A-Za-z0-9][A-Za-z0-9._-]*" value="${esc(s ? s.name : "")}" placeholder="ir7"></label>
    <label>Host / IP<input name="host" required pattern="[A-Za-z0-9][A-Za-z0-9.\\-]*" value="${esc(s ? s.host : "")}" placeholder="203.0.113.10"></label>
    <label>Role<select name="role">${["iran", "foreign"].map(r => `<option ${(s ? s.role : role) === r ? "selected" : ""}>${r}</option>`).join("")}</select></label>
    <label>SSH user<input name="ssh_user" value="${esc(s ? s.ssh_user : "root")}"></label>
    <label>SSH port<input name="ssh_port" type="number" min="1" max="65535" value="${s ? s.ssh_port : 22}"></label>
    <label>Provider<input name="provider" value="${esc(s ? s.provider : "")}" placeholder="optional"></label>
    <label class="chk"><input name="enabled" type="checkbox" ${!s || s.enabled ? "checked" : ""}> enabled (checked by the panel)</label>
    <div class="frm-act"><button class="pri" type="submit">${s ? "Save" : "Add server"}</button>${s ? "" : '<button type="button" onclick="cancelEdit()">Cancel</button>'}
      ${s ? `<span class="sp"></span><button type="button" class="danger" onclick="deleteServer(${s.id}, '${esc(s.name)}')">Remove from panel</button>` : ""}</div>
    <small class="mut">Adding a server here only tells the panel to look at it. Put the panel's public key in its <code>authorized_keys</code> first (Settings shows the key).</small>
  </form>`;

const srvProblem = s => { const f = s.facts || {}; return s.enabled && (s.online === false || (f.cpu || 0) > 85 || (f.ram || 0) > 90 || (f.disk || 0) > 85); };
PAGES.servers = async () => {
  const [list, tl] = await Promise.all([api("/api/servers"), api("/api/tunnels").catch(() => [])]);
  state.tunnelsCache = tl; state.serversCache = list;
  const mine = list.filter(s => s.role === state.tab);
  const rows = mine.filter(s => (state.mtab !== "problems" || srvProblem(s)) && (!state.q || (s.name + s.host + s.provider).toLowerCase().includes(state.q)));
  const nProb = mine.filter(srvProblem).length, online = mine.filter(s => s.online).length;
  const avg = k => { const v = mine.filter(s => s.online && s.facts && s.facts[k] != null).map(s => s.facts[k]); return v.length ? Math.round(v.reduce((a, b) => a + b, 0) / v.length) : null; };
  const sumNet = mine.reduce((a, s) => a + ((s.facts || {}).rx_mbps || 0) + ((s.facts || {}).tx_mbps || 0), 0);
  const greCell = s => {   // the GRE tunnels this server carries: all up / some down / all down / none
    const ts = (state.tunnelsCache || []).filter(t => (t.iran_name === s.name || t.foreign_name === s.name) && (t.type === "backhaul" || t.type === "gre"));
    if (!ts.length) {   // no GRE tunnel of the panel on this server: a check button, then the answer
      const g = (s.extra || {}).gre;
      if (!g) return `<button class="sm" onclick="event.stopPropagation();greCheck(${s.id})" title="Look for GRE links on this server and ping their other end">check</button>`;
      const tip = `${g.detail} · checked ${ago(g.at)} ago - click to check again`;
      return g.ok ? `<b class="gw-ok" style="cursor:pointer" title="${esc(tip)}" onclick="event.stopPropagation();greCheck(${s.id})">GRE ✓</b>` : g.links ? `<b class="err" style="cursor:pointer" title="${esc(tip)}" onclick="event.stopPropagation();greCheck(${s.id})">GRE ✕ not OK</b>` : `<span class="mut" style="cursor:pointer" title="${esc(tip)}" onclick="event.stopPropagation();greCheck(${s.id})">no GRE</span>`;
    }
    const up = ts.filter(t => t.state === "up").length, bad = ts.filter(t => t.state === "down").length;
    const cls = bad === ts.length ? "err" : bad || up < ts.length ? "warn" : "gw-ok", txt = bad === ts.length ? "GRE ✕" : bad ? "GRE ✓/✕" : up === ts.length ? "GRE ✓" : "GRE ~";
    return `<b class="${cls}" title="${esc(ts.map(t => t.name + ": " + t.state).join(", "))}">${txt}</b> <small class="mut">${ts.length}</small>`;
  };
  // Marzban / PasarGuard nodes that sit on this server's address: "Panel ✓ name" chips next to the status
  const pnl = await api("/api/panels").catch(() => ({panels: []}));
  const byIp = {};
  (pnl.panels || []).forEach(p => (p.nodes || []).forEach(n => { const ip = String(n.address || "").trim(); if (ip) (byIp[ip] = byIp[ip] || []).push({panel: p.name, status: n.status, name: n.name}); }));
  const chips = s => (byIp[s.host] || []).map(x => { const ok = x.status === "connected"; return `<span class="mchip ${ok ? "ok" : "bad"}" title="Node ${esc(x.name)} in panel ${esc(x.panel)}: ${esc(x.status || "?")}">${ok ? "Panel ✓" : "Panel ✕"} ${esc(x.panel)}</span>`; }).join("");
  const pbar = (v, warn, bad) => v == null ? '<span class="mut">—</span>' : `<div class="pbar"><span class="bar"><i class="${v >= bad ? "bad" : v >= warn ? "warn" : ""}" style="width:${Math.min(100, v)}%"></i></span><b>${v}%</b></div>`;
  const foreignTab = state.tab === "foreign";
  const row = s => {   // the old panel's column names, fixed columns so nothing moves: Server | Status | (Filtered) | Panel | IPv4 | GRE | RAM | CPU | Disk
    const f = s.facts || {}, flt = (s.extra || {}).iran_filter, blocked = flt && (flt.level === "filtered" || flt.level === "blocked");
    const cls = !s.enabled ? "" : s.online ? "up" : s.online === false ? "down" : "";
    return `<tr class="${cls} click" onclick="toggleDetail(${s.id})" title="${esc(f.top ? "Busiest process: " + f.top : "")}">
      <td class="name">${s.role === "foreign" && s.country ? flagImg(s.country) + " " : ""}${esc(s.name)}<span class="pill">${esc(s.provider || s.asn || "")}</span>${s.enabled ? "" : '<span class="pill">disabled</span>'}</td>
      <td><span class="st ${s.online ? "online" : s.online === false ? "offline" : ""}">${s.online ? "online" : s.online === false ? "offline" : "—"}</span></td>
      ${foreignTab ? `<td>${blocked ? '<span class="mchip bad" title="Not reachable from inside Iran">filtered</span>' : ""}</td>` : ""}
      <td class="pcell">${chips(s) || '<span class="mut">—</span>'}</td>
      <td class="mono">${esc(s.host)}</td><td>${greCell(s)}</td>
      <td>${pbar(f.ram, 80, 90)}</td><td>${pbar(f.cpu, 80, 92)}</td><td>${pbar(f.disk, 85, 95)}</td></tr>`;
  };  const openSrv = list.find(s => s.id === state.open);
  if (openSrv) srvModal(openSrv); else hideModal();
  $("#page").innerHTML = `<div class="tiles">
      <div class="tile ${online < mine.length ? "bad" : "ok"}"><small>Online</small><b>${online} / ${mine.length}</b><span>${mine.length - online ? (mine.length - online) + " not answering" : "every server answers"}</span></div>
      <div class="tile ${nProb ? "warn" : ""}"><small>Problems</small><b>${nProb}</b><span>offline, or CPU > 85 %, RAM > 90 %, disk > 85 %</span></div>
      <div class="tile"><small>Average load</small><b>${avg("cpu") ?? "—"}% <small style="display:inline;font-size:12px;letter-spacing:0;text-transform:none">cpu</small></b><span>ram ${avg("ram") ?? "—"}% · disk ${avg("disk") ?? "—"}%</span></div>
      <div class="tile"><small>Traffic now</small><b>${sumNet.toFixed(0)} Mb/s</b><span>sum of ↓ and ↑ over all ${state.tab} servers</span></div></div>
    <div class="toolbar">
      <div class="tabs"><button class="${state.mtab !== "problems" ? "on" : ""}" onclick="state.mtab='all';refresh()">All (${mine.length})</button><button class="${state.mtab === "problems" ? "on" : ""}" onclick="state.mtab='problems';refresh()">⚠ Problems (${nProb})</button></div>
      <span class="sp"></span><input id="q" placeholder="Search…" value="${esc(state.q)}" oninput="setQ(this.value)"><button onclick="newServer()">+ Add server</button><button onclick="srvMany()" title="Several servers at once, connected with one password">+ Several</button>${state.tab === "foreign" ? '<button class="sm" onclick="srvGeo()" title="Country of every foreign server, by majority vote of four services">🌍 Countries</button>' : ""}<a class="sm mut" href="/api/nodes/user-key" download="nikatel_user" title="Your own private key for PuTTY / ssh / MobaXterm — keep it at %USERPROFILE%\\.ssh\\nikatel_user">🔑 My SSH key</a></div>
    ${state.edit === "new" ? `<div class="card">${SERVER_FORM(null, state.tab)}</div>` : state.edit === "many" ? `<div class="card">${MANY_FORM()}</div>` : ""}
    <table class="tbl lift srv-tbl"><colgroup>${foreignTab ? '<col style="width:17%"><col style="width:6%"><col style="width:6%"><col style="width:17%"><col style="width:10%"><col style="width:7%"><col style="width:12%"><col style="width:12%"><col style="width:13%">' : '<col style="width:19%"><col style="width:7%"><col style="width:19%"><col style="width:11%"><col style="width:8%"><col style="width:12%"><col style="width:12%"><col style="width:12%">'}</colgroup>
      <thead><tr><th>Server</th><th>Status</th>${foreignTab ? "<th>Filtered</th>" : ""}<th>Panel</th><th>IPv4</th><th>GRE</th><th>RAM</th><th>CPU</th><th>Disk</th></tr></thead>
      <tbody>${rows.map(row).join("") || `<tr><td colspan="${foreignTab ? 9 : 8}" class="empty">No server.</td></tr>`}</tbody></table>`;;
  const q = $("#q"); if (q && state.q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
  if (state.open && state.edit !== state.open) loadServerCharts(state.open);
  if (state.con && state.open) { const c = $(`#con-cmd-${state.con}`); if (c) c.focus(); }
};
function srvModal(s) { const f = s.facts || {}; showModal(`<span class="st ${s.online ? "online" : s.online === false ? "offline" : ""}"></span><b>${esc(s.name)}</b> <span class="mono mut">${esc(s.host)}</span> <span class="pill">${esc(s.role)}</span>${f.hostname ? `<span class="pill">${esc(f.hostname)}</span>` : ""}`, `<div id="srv-detail-${s.id}">${state.edit === s.id ? SERVER_FORM(s) : serverDetailHtml(s)}</div>`, "wide"); }
function tunModal(t) { showModal(`<span class="st ${t.state}"></span><b>${esc(t.name)}</b> <span class="mut">${esc(t.iran_name)} ⇄ ${esc(t.foreign_name)}</span> <span class="pill">${esc(t.type)}${t.transport ? " · " + esc(t.transport) : ""}</span>`, detailRow(t), "wide"); }
/* small flag images (Windows has no flag emoji); the text code stays as the fallback when the image cannot load */
const flagImg = cc => cc && /^[A-Za-z]{2}$/.test(cc) ? `<img class="flg" src="https://flagcdn.com/20x15/${cc.toLowerCase()}.png" srcset="https://flagcdn.com/40x30/${cc.toLowerCase()}.png 2x" width="20" height="15" alt="${cc.toUpperCase()}" title="${cc.toUpperCase()}" onerror="this.outerHTML='<small class=flgt>${cc.toUpperCase()}</small>'">` : "";
const flag = cc => cc && /^[A-Z]{2}$/.test(cc) ? flagImg(cc) + " " + cc : "";
const irow = (label, val) => `<div class="irow"><span>${label}</span><div>${val}</div></div>`;
const ibox = (icon, title, rows) => `<div class="ibox"><h5><i>${icon}</i>${title}</h5>${rows.filter(Boolean).join("")}</div>`;
function serverDetailHtml(s) {
  const f = s.facts || {}, x = s.extra || {}, ab = x.abuse || {}, fl = x.iran_filter, geo = x.geo || {};
  const abuseOn = ab.installed || (f.abuse_rules || 0) > 0;
  const tab = state.wtab || "overview";
  const tabs = `<div class="tabs wtabs">${[["overview", "Overview"], ["charts", "Charts 24 h"], ["tools", "Tools"], ["console", "⌨ Console"]].map(([k, l]) => `<button class="${tab === k ? "on" : ""}" onclick="wtab('${k}')">${l}</button>`).join("")}</div>`;
  const tool = (icon, title, sub, click, cls = "") => `<a class="wtool ${cls}" onclick="${click}"><i>${icon}</i><b>${title}</b><small>${sub}</small></a>`;
  if (tab === "tools") return tabs + `<div class="wgroup"><h4>Manage</h4><div class="wtools">${tool("♥", "Health check", "sample this server now", `checkServer(${s.id})`)}${tool("✎", "Edit", "name, address, SSH user, port", `editServer(${s.id})`)}${tool("⌨", "Console", "run a command with presets", `srvConsole(${s.id})`)}${tool("🔑", "Connect SSH", "install the panel's key with the password", `srvConnect(${s.id}, '${esc(s.name)}')`)}${tool("↺", "Forget host key", "after a reinstall of this server", `forgetKey(${s.id}, '${esc(s.name)}')`)}</div></div>
    <div class="wgroup"><h4>Open on my computer</h4><div class="wtools">${tool("🖥", "Windows ssh", "a .bat with your own key", `srvOpen(${s.id}, 'ssh')`)}${tool("🐱", "PuTTY", "launcher", `srvOpen(${s.id}, 'putty')`)}${tool("🧰", "MobaXterm", "launcher", `srvOpen(${s.id}, 'mobaxterm')`)}</div></div>
    ${s.role === "foreign" ? `<div class="wgroup"><h4>Security</h4><div class="wtools">${tool("🛰", "Check filtering in Iran", "ping from your Iran servers + check-host.net", `srvFilter(${s.id})`)}${tool("🛡", abuseOn ? "Remove Abuse-Defender" : "Install Abuse-Defender", abuseOn ? `${f.abuse_rules || ab.rules || "?"} ranges blocked now` : "drop traffic to abused ranges", `srvAbuse(${s.id}, '${abuseOn ? "remove" : "install"}')`)}</div></div>` : ""}
    <div class="wgroup"><h4>Danger</h4><div class="wtools">${tool("⏻", "Reboot", "its tunnels are down until it is back", `srvReboot(${s.id}, '${esc(s.name)}')`, "danger")}</div></div>`;
  if (tab === "console") { if (state.con !== s.id) state.con = s.id; return tabs + `<div class="fix" id="con-${s.id}"><div class="frm-act"><select id="con-preset-${s.id}" onchange="$('#con-cmd-${s.id}').value=this.value">${["", ...Object.keys(CON_PRESETS)].map(k => `<option value="${k ? esc(CON_PRESETS[k][1]) : ""}">${k ? esc(CON_PRESETS[k][0]) : "— preset —"}</option>`).join("")}</select><input id="con-cmd-${s.id}" style="flex:1" placeholder="command to run as ${esc(s.ssh_user)}…" onkeydown="if(event.key==='Enter')srvRun(${s.id})"><button class="pri sm" onclick="srvRun(${s.id})">Run</button></div><pre class="gl-out" id="con-out-${s.id}">${esc(state.conOut[s.id] || "")}</pre></div>`; }
  if (tab === "charts") return tabs + `<div class="charts" id="srv-charts-${s.id}"><div class="mut">loading 24 h…</div></div><div class="kv" id="srv-peaks-${s.id}"></div>`;
  const mine = (state.tunnelsCache || []).filter(t => t.iran_name === s.name || t.foreign_name === s.name);
  const boxes = [
    ibox("🖥", "System", [irow("Host", `<b>${esc(f.hostname || x.hostname || "—")}</b>`), irow("Kernel", `<span class="mono">${esc(f.kernel || "—")}</span>`), irow("Uptime", f.uptime ? Math.round(f.uptime / 86400) + " d" : "—"), irow("Load", f.load1 != null ? f.load1 + " / " + f.load5 + " / " + f.load15 : "—"), irow("Connections", f.conn != null ? f.conn : "—"), irow("Busiest process", esc(f.top || "—"))]),
    ibox("📊", "Resources", [irow("CPU", pct(f.cpu, 80, 92)), irow("RAM", `${pct(f.ram, 80, 90)} <small class="mut">${f.ram_used_mb != null ? f.ram_used_mb + " / " + f.ram_total_mb + " MB" : ""}</small>`), irow("Swap", f.swap != null ? f.swap + "%" : "—"), irow("Disk", `${pct(f.disk, 85, 95)} <small class="mut">${f.disk_used_gb != null ? f.disk_used_gb + " / " + f.disk_total_gb + " GB" : ""}</small>`), irow("Traffic", f.rx_mbps != null ? `↓ ${f.rx_mbps} ↑ ${f.tx_mbps} Mb/s` : "—")]),
    ibox("🔑", "Access", [irow("SSH", `<span class="mono">${esc(s.ssh_user)}@${esc(s.host)}:${s.ssh_port}</span>`), irow("Last seen", when(s.last_seen_at)), irow("Role", esc(s.role)), irow("Provider", esc(s.provider || s.asn || "—"))]),
    s.role === "foreign" ? ibox("🛡", "Location & security", [irow("Country", `${flag(s.country) || "—"} ${esc(geo.city || "")} ${geo.agree ? `<small class="mut">(${esc(geo.agree)} agree)</small>` : ""}`), irow("Abuse-Defender", abuseOn ? `<b class="gw-ok">on · ${f.abuse_rules || ab.rules || "?"} ranges</b>` : "off"), irow("Filtered in Iran?", fl ? `<b class="${fl.level === "clean" ? "gw-ok" : fl.level === "unknown" ? "" : "err"}">${esc(fl.verdict)}</b> <small class="mut">${ago(fl.checked_at)} ago</small>` : "not checked yet")]) : "",
  ].join("");
  return tabs + `<div class="iboxes">${boxes}</div>    ${fl && state.flt === s.id ? `<div class="gw-out"><b>Pings to ${esc(s.host)} from Iran</b><div class="ob-inbounds">${fl.nodes.map(n => `<span><b class="${n.pending ? "mut" : n.ok === n.total ? "gw-ok" : n.ok ? "" : "err"}">${n.pending ? "…" : n.ok + "/" + n.total}</b> ${esc(n.city)} <small class="mut">${esc(n.asn)}${n.avg_ms != null ? " · " + n.avg_ms + " ms" : ""}</small></span>`).join("")}</div>${fl.note ? `<small class="mut">${esc(fl.note)}</small>` : ""}</div>` : ""}
    ${mine.length ? `<div class="ibox wide-box"><h5><i>⇄</i>Tunnels on this server <small>${mine.length}</small></h5><div class="ichips">${mine.map(t => `<span><span class="st ${t.state}">${esc(t.name)}</span> <small class="mut">${esc(t.iran_name === s.name ? "⇄ " + t.foreign_name : "⇄ " + t.iran_name)}${t.rtt_ms != null ? " · " + t.rtt_ms.toFixed(0) + " ms" : ""}</small></span>`).join("")}</div></div>` : ""}`;
}
/* a window tab only redraws the window body - no API call, the list behind it stays as it is */
window.copyText = t => { const done = () => toast("✓ Copied " + t, "ok"); if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(t).then(done, () => {}); else { const ta = document.createElement("textarea"); ta.value = t; document.body.appendChild(ta); ta.select(); try { document.execCommand("copy"); done(); } catch (e) { /* no clipboard */ } ta.remove(); } };
window.wtab = k => {
  state.wtab = k;
  const body = $("#modal .modal-body");
  if (body && state.page === "nodes") { const s = (state.serversCache || []).find(x => x.id === state.open); if (s) { body.innerHTML = `<div id="srv-detail-${s.id}">${serverDetailHtml(s)}</div>`; if (k === "charts") loadServerCharts(s.id); if (k === "console") { const c = $(`#con-cmd-${s.id}`); if (c) c.focus(); } return; } }
  if (body && state.page === "tunnels") { const t = (state.tunnelsCache || []).find(x => x.id === state.open); if (t) { body.innerHTML = detailRow(t); if (k === "charts") loadTunnelCharts(t.id); return; } }
  if (body && state.page === "configs" && cf.open) { const c = (cf.cache || []).find(x => x.id === cf.open); if (c) { body.innerHTML = `<div id="cf-detail-${c.id}">${cfDetailHtml(c)}</div>`; if (k === "charts") cfLoadChart(c.id); return; } }
  refresh();
};
let CON_PRESETS = {};
api("/api/nodes/presets").then(d => { d.presets.forEach(p => CON_PRESETS[p.key] = [p.label, p.command]); }).catch(() => {});
state.conOut = {};
window.srvConsole = id => { state.con = id; if (state.open === id && $("#modal .modal-body")) { wtab("console"); return; } state.open = id; state.wtab = "console"; refresh(); };
window.srvRun = async id => { const cmd = $(`#con-cmd-${id}`).value.trim(); if (!cmd) return; const out = $(`#con-out-${id}`); out.textContent = "running…"; try { const r = await api(`/api/servers/${id}/console`, {method: "POST", body: JSON.stringify({command: cmd})}); state.conOut[id] = `$ ${cmd}\n${r.output}${r.code ? "\n[exit " + r.code + "]" : ""}\n(${r.ms} ms)`; } catch (e) { state.conOut[id] = "✕ " + e.message; } out.textContent = state.conOut[id]; };
window.srvConnect = async (id, name) => { const pw = await gprompt(`SSH password of ${name} (used once to install the panel's key, not stored):`); if (!pw) return; toast("Connecting…"); try { const r = await api(`/api/servers/${id}/connect`, {method: "POST", body: JSON.stringify({password: pw})}); toast("✓ Key installed on " + r.hostname, "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.srvOpen = (id, app) => { toast("Preparing… your key is installed on the server first"); const a = document.createElement("a"); a.href = `/api/servers/${id}/launcher?app=${app}`; a.download = ""; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => toast("✓ Launcher downloaded. Once: put your key from Nodes → 'My SSH key' at %USERPROFILE%\\.ssh\\nikatel_user", "ok"), 1500); };
window.srvAbuse = async (id, action) => { if (!await gconfirm(action === "install" ? "Install Abuse-Defender on this foreign server? Outgoing traffic to a public list of abused ranges is dropped; your own servers, tunnel links, DNS and gateway stay allowed. Takes a minute." : "Remove Abuse-Defender from this server?")) return; toast("Working… (up to a minute)"); try { const r = await api(`/api/servers/${id}/abuse`, {method: "POST", body: JSON.stringify({action})}); toast(r.installed ? `✓ Abuse-Defender on: ${r.rules} ranges blocked` : "✓ Abuse-Defender removed", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.greCheck = async id => { toast("Checking GRE… (up to 30 s)"); try { const r = await api(`/api/servers/${id}/gre-check`, {method: "POST"}); toast((r.ok ? "✓ " : "✕ ") + r.detail, r.ok ? "ok" : "bad"); } catch (e) { toast("✕ " + e.message, "bad"); } refresh(); };
window.srvFilter = async id => { toast("Pinging from Iran… (about 30 s)"); try { const r = await api(`/api/servers/${id}/filter-check`, {method: "POST"}); toast((r.level === "clean" ? "✓ " : "⚠ ") + r.verdict, r.level === "clean" ? "ok" : "bad"); state.flt = id; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.srvReboot = async (id, name) => { if (!await gconfirm(`Reboot ${name}? Its tunnels are down until it is back (usually under a minute).`)) return; try { await api(`/api/servers/${id}/reboot`, {method: "POST"}); toast("✓ Reboot requested", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.srvGeo = async () => { toast("Looking up countries…"); try { const r = await api("/api/servers/geo-refresh", {method: "POST"}); toast(`✓ ${r.changes.length} changed`, "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.srvMany = () => { state.edit = "many"; state.open = null; refresh(); };
window.srvManyGo = async e => { e.preventDefault(); const f = new FormData(e.target); toast("Adding… (a few seconds per server)"); try { const r = await api("/api/servers/add-many", {method: "POST", body: JSON.stringify({role: state.tab, lines: f.get("lines"), password: f.get("password"), ssh_user: f.get("ssh_user").trim() || "root", ssh_port: +f.get("ssh_port") || 22})}); state.manyRes = r.items; const ok = r.items.filter(i => i.ok).length; toast(`${ok ? "✓" : "✕"} ${ok} of ${r.items.length} added and connected`, ok === r.items.length ? "ok" : "bad"); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
const MANY_FORM = () => `<form class="frm" onsubmit="return srvManyGo(event)"><label style="grid-column:1/-1">Servers, one per line: <small>name host [user] [port] — e.g. <code>de4 203.0.113.9</code></small><textarea name="lines" class="ob-raw" required placeholder="de4 203.0.113.9&#10;nl2 203.0.113.10 root 22"></textarea></label>
    <label>SSH user<input name="ssh_user" value="root"></label><label>SSH port<input name="ssh_port" type="number" value="22"></label><label>SSH password <small>(the same for all; used once to install the panel's key — leave empty if the key is already there)</small><input name="password" type="password" autocomplete="new-password"></label>
    <div class="frm-act"><button class="pri" type="submit">Add ${state.tab} servers</button><button type="button" onclick="cancelEdit()">Cancel</button></div>
    ${state.manyRes ? `<div style="grid-column:1/-1">${state.manyRes.map(i => `<div class="ev ${i.ok ? "ok" : "bad"}"><time></time><i></i><small>${esc(i.host)}</small><span><b>${esc(i.name)}</b> — ${i.ok ? "✓ " + esc(i.detail || "connected") : "✕ " + esc(i.error || "")}</span></div>`).join("")}</div>` : ""}</form>`;
/* ---- monitoring: real charts with axes, a hover read-out, min / avg / max, a range switch (1 h / 6 h / 24 h / 7 d) ---- */
const CH = {}; let chSeq = 0;
const RANGES = [[1, "1 h"], [6, "6 h"], [24, "24 h"], [168, "7 days"]];
state.mrange = +localStorage.getItem("nk.mrange") || 24;
const avgOf = a => a.length ? a.reduce((x, y) => x + y, 0) / a.length : null;
const nums = a => a.filter(v => v != null);
const fmtT = (ts, span) => { const d = new Date(ts * 1000), hm = d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", hour12: false}); return span > 36 * 3600 ? d.toLocaleDateString([], {day: "2-digit", month: "short"}) + " " + hm : hm; };
const durText = s => s < 90 ? Math.round(s) + " s" : s < 5400 ? Math.round(s / 60) + " min" : s < 172800 ? (s / 3600).toFixed(1) + " h" : (s / 86400).toFixed(1) + " d";
function chartCard(title, pts, o = {}) {
  const v = pts.filter(p => p[1] != null), fmt = o.fmt || (x => String(Math.round(x * 10) / 10)), unit = o.unit || "";
  if (v.length < 2) return `<div class="mcard"><h6>${title}</h6><div class="mut chart-empty">not enough data yet</div></div>`;
  const vals = v.map(p => p[1]), cur = vals[vals.length - 1], mn = Math.min(...vals), mx = Math.max(...vals), av = avgOf(vals);
  const W = 460, H = 130, pl = 40, pr = 8, pt = 10, pb = 20, t0 = pts[0][0], t1 = pts[pts.length - 1][0], span = t1 - t0 || 1;
  const top = o.max != null ? o.max : (mx * 1.15 || 1), bot = o.min != null ? o.min : 0;
  const X = t => pl + (t - t0) / span * (W - pl - pr), Y = x => pt + (1 - (Math.min(top, Math.max(bot, x)) - bot) / (top - bot || 1)) * (H - pt - pb);
  let line = "", gap = true;
  pts.forEach(p => { if (p[1] == null) { gap = true; return; } line += (gap ? "M" : "L") + X(p[0]).toFixed(1) + " " + Y(p[1]).toFixed(1); gap = false; });
  const area = line + `L${X(v[v.length - 1][0]).toFixed(1)} ${H - pb}L${X(v[0][0]).toFixed(1)} ${H - pb}Z`;
  const tone = o.bad != null && cur >= o.bad ? "bad" : o.warn != null && cur >= o.warn ? "warn" : "";
  const grid = [0, .5, 1].map(f => { const y = (pt + (1 - f) * (H - pt - pb)).toFixed(1); return `<line x1="${pl}" x2="${W - pr}" y1="${y}" y2="${y}" class="lg"/><text x="${pl - 5}" y="${(+y + 3).toFixed(1)}" class="lt" text-anchor="end">${fmt(bot + f * (top - bot))}</text>`; }).join("");
  const xl = [0, .5, 1].map(f => `<text x="${(pl + f * (W - pl - pr)).toFixed(1)}" y="${H - 4}" class="lt" text-anchor="${f === 0 ? "start" : f === 1 ? "end" : "middle"}">${fmtT(t0 + f * span, span)}</text>`).join("");
  const id = "c" + (++chSeq); CH[id] = {v, t0, span, W, pl, pr, X, Y, fmt, unit};
  return `<div class="mcard"><h6>${title}<span class="mval ${tone}">${fmt(cur)}${unit}</span></h6>
    <div class="lc-wrap"><svg class="lc ${tone}" data-ch="${id}" viewBox="0 0 ${W} ${H}">${grid}${xl}<path class="la" d="${area}"/><path class="ll" d="${line}"/><line class="lc-cur" y1="${pt}" y2="${H - pb}" x1="-9" x2="-9"/><circle class="lc-dot" r="3.5" cx="-9" cy="-9"/></svg><div class="lc-tip"></div></div>
    <div class="mstat"><span>min <b>${fmt(mn)}${unit}</b></span><span>avg <b>${fmt(av)}${unit}</b></span><span>max <b>${fmt(mx)}${unit}</b></span></div></div>`;
}
document.addEventListener("mousemove", e => {
  const s = e.target.closest ? e.target.closest("svg.lc") : null;
  document.querySelectorAll(".lc-tip.on").forEach(t => { if (!s || t.parentNode !== s.parentNode) t.classList.remove("on"); });
  if (!s) return;
  const c = CH[s.dataset.ch]; if (!c) return;
  const r = s.getBoundingClientRect(), t = c.t0 + ((e.clientX - r.left) / r.width * c.W - c.pl) / (c.W - c.pl - c.pr) * c.span;
  let b = c.v[0]; for (const p of c.v) if (Math.abs(p[0] - t) < Math.abs(b[0] - t)) b = p;
  const x = c.X(b[0]), y = c.Y(b[1]), cur = s.querySelector(".lc-cur"), dot = s.querySelector(".lc-dot"), tip = s.parentNode.querySelector(".lc-tip");
  cur.setAttribute("x1", x); cur.setAttribute("x2", x); dot.setAttribute("cx", x); dot.setAttribute("cy", y);
  tip.textContent = `${c.fmt(b[1])}${c.unit} · ${fmtT(b[0], c.span)}`; tip.classList.add("on");
  tip.style.left = Math.min(Math.max(x / c.W * 100, 18), 82) + "%";
});
const rangeTabs = () => `<div class="tabs mrange">${RANGES.map(([h, l]) => `<button class="${state.mrange === h ? "on" : ""}" onclick="mrange(${h})">${l}</button>`).join("")}</div>`;
const mtile = (label, big, sub, cls = "") => `<div class="mtile ${cls}"><small>${label}</small><b>${big}</b><span>${sub}</span></div>`;
window.mrange = h => { state.mrange = h; localStorage.setItem("nk.mrange", h); const sb = document.querySelector('[id^="srv-charts-"]'), tb = document.querySelector('[id^="tun-charts-"]'), cb = document.querySelector('[id^="cf-charts-"]'); if (sb) loadServerCharts(+sb.id.split("-").pop()); else if (tb) loadTunnelCharts(+tb.id.split("-").pop()); else if (cb) cfLoadChart(+cb.id.split("-").pop()); };
async function loadServerCharts(id) {
  const box = document.getElementById("srv-charts-" + id);
  if (!box) return;
  const d = await api(`/api/servers/${id}/samples?hours=${state.mrange}`);
  const pk = document.getElementById("srv-peaks-" + id); if (pk) pk.remove();
  const ts = d.samples.map(r => r[0]), col = n => d.samples.map(r => r[d.columns.indexOf(n)]), series = n => ts.map((t, i) => [t, col(n)[i]]);
  CH_reset();
  const cpu = nums(col("cpu")), ram = nums(col("ram")), disk = nums(col("disk")), rx = nums(col("rx_mbps")), tx = nums(col("tx_mbps")), conn = nums(col("conn"));
  const f1 = v => v == null ? "—" : (Math.round(v * 10) / 10), pc = v => f1(v) + "%";
  box.className = "mon";
  box.innerHTML = `<div class="mhead">${rangeTabs()}<small class="mut">${d.samples.length} samples · one every 2 minutes · hover a chart for exact values</small></div>
    <div class="mtiles">${mtile("CPU", pc(avgOf(cpu)), "peak " + pc(cpu.length ? Math.max(...cpu) : null), avgOf(cpu) > 85 ? "bad" : "")}${mtile("RAM", pc(avgOf(ram)), "peak " + pc(ram.length ? Math.max(...ram) : null), avgOf(ram) > 90 ? "bad" : "")}${mtile("Disk", pc(disk.length ? disk[disk.length - 1] : null), "now", disk.length && disk[disk.length - 1] > 85 ? "bad" : "")}${mtile("Traffic", "↓ " + f1(avgOf(rx)), "↑ " + f1(avgOf(tx)) + " Mb/s avg · peak ↓ " + f1(rx.length ? Math.max(...rx) : null) + " ↑ " + f1(tx.length ? Math.max(...tx) : null))}${mtile("Connections", conn.length ? Math.round(avgOf(conn)) : "—", "peak " + (conn.length ? Math.max(...conn) : "—"))}</div>
    <div class="mgrid">${chartCard("CPU", series("cpu"), {max: 100, unit: "%", warn: 70, bad: 85, fmt: v => Math.round(v)})}${chartCard("RAM", series("ram"), {max: 100, unit: "%", warn: 80, bad: 90, fmt: v => Math.round(v)})}${chartCard("Disk", series("disk"), {max: 100, unit: "%", warn: 75, bad: 85, fmt: v => Math.round(v)})}${chartCard("Load (1 min)", series("load1"), {fmt: v => (Math.round(v * 100) / 100)})}${chartCard("Download ↓", series("rx_mbps"), {unit: " Mb/s"})}${chartCard("Upload ↑", series("tx_mbps"), {unit: " Mb/s"})}${chartCard("Connections", series("conn"), {fmt: v => Math.round(v)})}</div>`;
}
function CH_reset() { for (const k of Object.keys(CH)) delete CH[k]; }window.setTab = t => { state.tab = t; state.open = null; state.edit = null; localStorage.setItem("nk.tab", t); refresh(); };
let qT; window.setQ = v => { state.q = v.trim().toLowerCase(); clearTimeout(qT); qT = setTimeout(refresh, 150); };
window.checkServer = async id => { toast("Checking…"); try { await api(`/api/servers/${id}/check`, {method: "POST"}); toast("✓ Checked", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.newServer = () => { state.edit = "new"; state.open = null; refresh(); };
window.editServer = id => { state.edit = id; state.open = id; refresh(); };
window.cancelEdit = () => { state.edit = null; if (state.build) { closeModal(); return; } refresh(); };
window.openBuild = () => { state.build = true; state.open = null; refresh(); };
window.newTunnel = () => { if (state.page !== "tunnels" || sub("tunnels") !== "list") { state.build = true; go("tunnels"); state.sub.tunnels = "list"; refresh(); } else openBuild(); };
window.saveServer = async (e, id) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = {name: f.get("name").trim(), host: f.get("host").trim(), role: f.get("role"), ssh_user: f.get("ssh_user").trim() || "root", ssh_port: +f.get("ssh_port") || 22, provider: f.get("provider").trim(), enabled: f.get("enabled") === "on"};
  try {
    await api(id ? `/api/servers/${id}` : "/api/servers", {method: id ? "PUT" : "POST", body: JSON.stringify(body)});
    toast(id ? "✓ Saved" : "✓ Server added", "ok"); state.edit = null; refresh();
  } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};
window.deleteServer = async (id, name) => {
  if (!await gconfirm(`Remove ${name} from the panel? Nothing changes on the server itself.`)) return;
  try { await api(`/api/servers/${id}`, {method: "DELETE"}); toast("✓ Removed", "ok"); state.edit = null; state.open = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.forgetKey = async (id, name) => {
  if (!await gconfirm(`Forget the SSH host key of ${name}? Do this only after the server was reinstalled.`)) return;
  try { await api(`/api/servers/${id}/forget-hostkey`, {method: "POST"}); toast("✓ Host key forgotten", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
};

/* ---- tunnels */
const TUN_TYPES = ["backhaul", "gre", "backpack", "chisel", "paqet", "other"];
const TUN_LABEL = {backhaul: "Backhaul over GRE", gre: "Plain GRE", backpack: "Backpack", chisel: "Chisel", paqet: "Paqet", other: "Other"};
const tunType = t => TUN_TYPES.includes((t.type || "").toLowerCase()) ? (t.type || "").toLowerCase() : "other";
PAGES.tunnels = async () => {
  renderSub();
  const s = sub("tunnels");
  if (s === "repairs") return PAGES.actions();
  if (s === "test") return PAGES.tunnelTest();
  if (s === "create") { state.sub.tunnels = "list"; state.build = true; }
  return PAGES.tunnelsList();
};
PAGES.configs = async () => { renderSub(); return sub("configs") === "panels" ? PAGES.panels() : PAGES.configsList(); };
const tt = {job: null, kinds: null, iran: localStorage.getItem("nk.tt.iran") || "", foreign: localStorage.getItem("nk.tt.foreign") || ""};
PAGES.tunnelTest = async () => {
  const [servers, kd] = await Promise.all([api("/api/servers"), tt.kinds ? Promise.resolve({kinds: tt.kinds}) : api("/api/tunnel-test/kinds")]);
  tt.kinds = kd.kinds;
  const opt = (role, cur) => servers.filter(s => s.role === role && s.enabled).map(s => `<option value="${s.id}" ${String(s.id) === cur ? "selected" : ""}>${esc(s.name)} — ${esc(s.host)}${s.online === false ? " (offline)" : ""}</option>`).join("");
  const j = tt.job, running = j && !j.done;
  const row = r => { const sc = r.score == null ? null : r.score; return `<tr class="${r.ok ? "up" : "down"}"><td class="name">${esc((tt.kinds.find(k => k.key === r.kind) || {}).label || r.kind)}</td><td><span class="st ${r.ok ? "up" : "down"}">${r.error ? "failed" : r.ok ? "works" : "poor / failed"}</span></td>
      <td class="num">${r.latency_ms != null ? r.latency_ms + " ms" : "—"}</td><td class="num">${r.jitter_ms != null ? r.jitter_ms + " ms" : "—"}</td><td class="num">${r.loss_pct != null ? r.loss_pct + "%" : "—"}</td><td class="num">${r.down_mbps != null ? "↓" + r.down_mbps + (r.up_mbps != null ? " ↑" + r.up_mbps : "") + " Mb/s" : "—"}</td>
      <td><b class="${sc == null ? "mut" : sc >= 75 ? "gw-ok" : sc >= 50 ? "" : "err"}">${sc == null ? "—" : sc + " / 100"}</b></td><td class="mut" title="${esc(r.error || r.verdict || "")}">${esc((r.error || r.verdict || "").slice(0, 60))}</td></tr>`; };
  const best = j && j.results.filter(r => r.ok && r.score != null).sort((a, b) => b.score - a.score)[0];
  $("#page").innerHTML = `<div class="card"><h3>🧪 Tunnel test <span class="sp"></span>${running ? `<span class="pill ok">running: ${esc(j.current || "…")} · ${j.results.length} / ${j.kinds.length} done</span>` : ""}</h3>
    <div class="frm">
      <label>Iran server<select id="tt-iran" ${running ? "disabled" : ""} onchange="tt.iran=this.value;localStorage.setItem('nk.tt.iran',this.value)">${opt("iran", tt.iran)}</select></label>
      <label>Foreign server<select id="tt-foreign" ${running ? "disabled" : ""} onchange="tt.foreign=this.value;localStorage.setItem('nk.tt.foreign',this.value)">${opt("foreign", tt.foreign)}</select></label>
      <div class="frm-act">${running ? `<button class="danger" onclick="ttStop()">Stop</button>` : `<button class="pri" onclick="ttStart()">Start test</button>`}<small class="mut">${running ? "the current transport finishes, then the test stops; everything temporary is removed" : "each ticked transport is built as a temporary tunnel, measured, and removed again — one after the other"}</small></div>
    </div>
    <div class="ob-sec" id="tt-kinds"><h4>What to try <small class="mut">— tick a whole engine or single transports; each one is built as a temporary tunnel, measured, and removed</small></h4>
      <div class="ttgrid">${[...new Set(tt.kinds.map(k => k.group))].map(g => `<div class="ttg"><div class="ttg-head"><b>${esc(g)}</b><span class="sp"></span><a onclick="ttGroup('${esc(g)}', true)">all</a><a onclick="ttGroup('${esc(g)}', false)">none</a></div>${tt.kinds.filter(k => k.group === g).map(k => `<label data-g="${esc(g)}" class="ttk"><input type="checkbox" value="${k.key}" ${running ? "disabled" : ""} ${(j ? j.kinds.includes(k.key) : k.default) ? "checked" : ""}> <b>${esc(k.label.replace(/^[^·]+· /, ""))}</b><small class="mut">${esc(k.note)}</small></label>`).join("")}</div>`).join("")}</div></div>
    <div class="ob-sec"><h4>Which tunnel for which situation <small class="mut">— click one to open Create tunnel with it</small></h4>
      <div class="bguide">${BK_GUIDE.map(([when, what, k, t, why]) => `<a class="bg-row" onclick="bkPick('${k}', '${t}');state.sub.tunnels='list';state.build=true;refresh()"><b>${esc(when)}</b><span>${esc(what)}</span><small>${esc(why)}</small></a>`).join("")}</div>
      <small class="mut">Every engine ships with its many-users settings (multiplexing, 8 mux channels, 16-24 connection pool, keepalive 75 s, heartbeat 20 s, BBR + fq on both kernels). Backhaul is measured with 30 pings over GRE and a download; the other engines through their test door, both ways. Nothing is added to your list; the story of each run stays in Repairs.</small></div></div>
    ${j ? `<div class="card"><h3>Results <span class="sp"></span>${best ? `<span class="pill ok">best: ${esc((tt.kinds.find(k => k.key === best.kind) || {}).label || best.kind)} · ${best.score} / 100</span>` : ""}</h3>
      <table class="tbl"><colgroup><col style="width:24%"><col style="width:10%"><col style="width:10%"><col style="width:10%"><col style="width:8%"><col style="width:12%"><col style="width:11%"><col></colgroup>
        <thead><tr><th>Transport</th><th>State</th><th>Latency</th><th>Jitter</th><th>Loss</th><th>Download</th><th>Score</th><th>Verdict</th></tr></thead>
        <tbody>${[...j.results].sort((a, b) => (b.score || 0) - (a.score || 0)).map(row).join("")}${running ? `<tr><td class="name">${esc(j.current)}</td><td colspan="7" class="mut">measuring…</td></tr>` : ""}</tbody></table>
      ${running && j.story && j.story.length ? `<div class="fix">${j.story.map(l => `<div class="fx-line"><b>·</b><span>${esc(l)}</span></div>`).join("")}</div>` : ""}</div>` : ""}`;
  if (running) { clearTimeout(tt.timer); tt.timer = setTimeout(ttPoll, 3000); }
};
window.ttGroup = (g, on) => document.querySelectorAll(`#tt-kinds label[data-g="${g}"] input`).forEach(x => { if (!x.disabled) x.checked = on; });
window.ttStart = async () => {
  const kinds = [...document.querySelectorAll("#tt-kinds input:checked")].map(x => x.value);
  if (!kinds.length) { toast("Tick at least one transport", "bad"); return; }
  try { const r = await api("/api/tunnel-test/compare", {method: "POST", body: JSON.stringify({iran_id: +$("#tt-iran").value, foreign_id: +$("#tt-foreign").value, kinds})}); tt.job = {id: r.id, kinds, results: [], current: kinds[0], done: false}; toast("Test started — about 2 minutes per transport"); refresh(); }
  catch (e) { toast("✕ " + e.message, "bad"); }
};
window.ttPoll = async () => { if (!tt.job || tt.job.done) return; try { tt.job = await api(`/api/tunnel-test/compare/${tt.job.id}`); } catch (e) { tt.job.done = true; } if (state.page === "tunnels" && sub("tunnels") === "test") refresh(); };
window.ttStop = async () => { if (!tt.job) return; try { await api(`/api/tunnel-test/compare/${tt.job.id}/stop`, {method: "POST"}); toast("Stopping after the current transport…"); } catch (e) { toast("✕ " + e.message, "bad"); } };
PAGES.tunnelQuality = async () => {
  const list = await api("/api/tunnels");
  const q = t => t.quality || {};
  const cls = v => v == null ? "" : v >= 70 ? "gw-ok" : v >= 40 ? "" : "err";
  $("#page").innerHTML = `<div class="toolbar"><span class="mut">Latency, jitter and loss over the GRE link of each tunnel (30 pings from the Iran side); speed is measured by the pair test.</span><span class="sp"></span><button class="pri" onclick="qualityAll()">Test all & compare</button></div>
    <table class="tbl lift"><colgroup><col style="width:24%"><col style="width:10%"><col style="width:20%"><col style="width:10%"><col style="width:10%"><col style="width:10%"><col><col style="width:110px"></colgroup>
      <thead><tr><th>Tunnel</th><th>State</th><th>Iran ⇄ Foreign</th><th>Latency</th><th>Jitter</th><th>Loss</th><th>Score</th><th></th></tr></thead>
      <tbody>${list.sort((a, b) => (q(b).score || 0) - (q(a).score || 0)).map(t => `<tr class="${t.state}"><td class="name">${esc(t.name)}</td><td><span class="st ${t.state}">${esc(t.state)}</span></td><td>${esc(t.iran_name)} <span class="mut">⇄</span> ${esc(t.foreign_name)}</td>
        <td class="num">${q(t).latency_ms != null ? q(t).latency_ms + " ms" : "—"}</td><td class="num">${q(t).jitter_ms != null ? q(t).jitter_ms + " ms" : "—"}</td><td class="num ${(q(t).loss_pct || 0) >= 15 ? "err" : ""}">${q(t).loss_pct != null ? q(t).loss_pct + "%" : "—"}</td>
        <td><b class="${cls(q(t).score)}">${q(t).score != null ? q(t).score + " / 100" : "not measured"}</b> <small class="mut">${q(t).at ? ago(q(t).at) + " ago" : ""}</small></td>
        <td><button class="sm" onclick="qualityOne(${t.id})">Measure</button></td></tr>`).join("") || '<tr><td colspan="8" class="empty">No tunnel.</td></tr>'}</tbody></table>`;
};
window.editPorts = async (id, cur) => { const v = await gprompt("Forwarded ports of this tunnel (Iran → foreign), e.g. 443, 443=2096, 10000-10009:", cur); if (v == null || !v.trim()) return; toast("Changing the ports… (the tunnel service restarts)"); try { const r = await api(`/api/tunnels/${id}/ports`, {method: "PUT", body: JSON.stringify({ports: v})}); toast("✓ Ports: " + r.mapping, "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.detectTunnels = async () => { state.detect = {loading: true}; refresh(); try { state.detect = await api("/api/tunnels/detect"); } catch (e) { state.detect = {error: e.message}; } refresh(); };
window.adoptPair = async i => { const p = state.detect.pairs[i]; try { const r = await api("/api/tunnels/adopt", {method: "POST", body: JSON.stringify(p)}); toast("✓ " + r.name + " added to the panel", "ok"); state.detect = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* Detect on servers: opens as a window. Complete pairs can be added to the panel; incomplete ones
   (an Iran-side backhaul config whose foreign side no longer exists) can be removed from that server. */
function detectHtml() {
  const d = state.detect; if (!d) { return ""; }
  let body;
  if (d.loading) body = `<div class="fx-line run"><i></i>Scanning every server for tunnels…</div>`;
  else if (d.error) body = `<div class="err">${esc(d.error)}</div>`;
  else {
    const rows = d.pairs.filter(p => !p.in_panel), orphans = rows.filter(p => !p.paired);
    body = `<div class="toolbar"><span class="mut">${d.scanned} servers scanned · ${d.pairs.length} tunnels found · <b>${rows.length}</b> not in the panel · <b class="${orphans.length ? "err" : ""}">${orphans.length}</b> incomplete</span><span class="sp"></span>${orphans.length ? `<button class="sm danger" onclick="detectCleanAll()">🗑 Remove all incomplete (${orphans.length})</button>` : ""}<button class="sm" onclick="detectTunnels()">↻ Scan again</button></div>
      <small class="mut">A complete pair = an Iran server config + the foreign client that dials it. Incomplete = only the Iran side is left (the foreign side was removed or reinstalled); it holds a control port and a GRE link for nothing and can be removed from that server.</small>
      ${d.errors.length ? `<div class="err">${d.errors.map(e => esc(e.server) + ": " + esc(e.error)).join(" · ")}</div>` : ""}
      ${rows.map(p => `<div class="gw-row"><span><b>${esc(p.name)}</b> <small class="mut">${esc(p.transport)} · control ${p.port} · ports ${esc(p.ports.join(", ") || "—")} · GRE ${esc(p.iran_tunnel_ip || "?")} ⇄ ${esc(p.foreign_tunnel_ip || "?")}</small></span><span>${esc(p.iran_name)} ⇄ ${p.paired ? esc(p.foreign_name) : '<span class="err">no matching foreign side</span>'}</span><span class="ob-act">${p.paired ? `<button class="sm pri" onclick="adoptPair(${d.pairs.indexOf(p)})">Add to panel</button>` : `<button class="sm danger" onclick="detectClean(${d.pairs.indexOf(p)})" title="Remove this incomplete tunnel from ${esc(p.iran_name)}">Remove</button>`}</span></div>`).join("") || '<div class="mut">Everything on the servers is already in the panel.</div>'}`;
  }
  showModal(`<b>🔍 Tunnels found on the servers</b>`, body, "wide");
  return "";
}
window.detectClean = async i => { const p = state.detect.pairs[i]; if (!await gconfirm(`Remove the incomplete tunnel ${p.name} (${p.transport} ${p.port}) from ${p.iran_name}? Its units, config and GRE link go.`)) return; try { const r = await api("/api/tunnels/detect/clean", {method: "POST", body: JSON.stringify({iran_id: p.iran_id, transport: p.transport, port: p.port, name: p.name})}); toast("✓ " + r.results.join(" · "), "ok"); detectTunnels(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.detectCleanAll = async () => { const list = state.detect.pairs.filter(p => !p.in_panel && !p.paired); if (!list.length) return; if (!await gconfirm(`Remove ${list.length} incomplete tunnels from their Iran servers?`)) return; let ok = 0; for (const p of list) { try { await api("/api/tunnels/detect/clean", {method: "POST", body: JSON.stringify({iran_id: p.iran_id, transport: p.transport, port: p.port, name: p.name})}); ok++; } catch (e) { toast("✕ " + p.name + ": " + e.message, "bad"); } } toast(`✓ ${ok} of ${list.length} removed`, ok === list.length ? "ok" : "bad"); detectTunnels(); };
window.qualityOne = async id => { toast("Measuring… (about 10 s)"); try { const r = await api(`/api/tunnels/${id}/quality`, {method: "POST"}); toast(`✓ ${r.name}: ${r.latency_ms} ms · jitter ${r.jitter_ms} · loss ${r.loss_pct}% · score ${r.score}`, "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.qualityAll = async () => { toast("Measuring every tunnel… (10 s each)"); try { const r = await api("/api/tunnels/quality-all", {method: "POST"}); toast(`✓ ${r.results.filter(x => !x.error).length} measured`, "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
async function enginesLoad() {
  const box = $("#eng-box"); if (!box) return;
  const d = await api("/api/engines");
  const eng = d.engines || {};
  // one row per core: the version your servers run, the newest release, and an Update button only when one is out
  const row = (k, e) => {
    const nodes = e.nodes || [], have = nodes.filter(n => n.version && n.version !== "none"), vers = {};
    have.forEach(n => vers[n.version] = (vers[n.version] || 0) + 1);
    const installed = Object.entries(vers).sort((a, b) => b[1] - a[1]).map(([v, c]) => `${esc(v)}${Object.keys(vers).length > 1 ? ` <small class="mut">×${c}</small>` : ""}`).join(", ");
    const outdated = e.outdated || 0, unreach = nodes.filter(n => n.state === "unreachable").length;
    const status = e.error ? `<span class="err">${esc(e.error)}</span>` : !nodes.length ? '<span class="mut">press Check</span>' : outdated ? `<b class="warn">New version ${esc(e.latest)} is out</b> <small class="mut">· ${outdated} server${outdated > 1 ? "s" : ""} to update</small>` : `<b class="gw-ok">Up to date</b>${unreach ? ` <small class="mut">· ${unreach} unreachable</small>` : ""}`;
    return `<div class="eng-row"><span class="eng-name"><b>${esc(e.title)}</b><small class="mut">${have.length} of ${nodes.length} servers have it</small></span><span class="eng-ver" title="${esc(nodes.map(n => n.name + ": " + (n.version || "—")).join(", "))}">${installed || '<span class="mut">not installed</span>'}</span><span class="eng-latest">${e.latest ? `latest ${esc(e.latest)}` : "—"}${e.page ? ` <a class="mut" href="${esc(e.page)}" target="_blank" rel="noopener">notes</a>` : ""}</span><span class="eng-st">${status}</span><span class="eng-act">${outdated ? `<button class="pri sm" onclick="engineUpdate('${k}')" title="Update ${outdated} server${outdated > 1 ? "s" : ""} to ${esc(e.latest)}">⬆ Update</button>` : ""}</span></div>`;
  };
  box.innerHTML = `<div class="toolbar"><span class="mut">${d.checked_at ? "checked " + ago(d.checked_at) + " ago" : "checking…"} · nothing updates by itself: when a new version is out, Update asks you first</span><span class="sp"></span><button class="sm" onclick="engineCheck()">↻ Check now</button></div>
    <div class="eng-head"><span>Core</span><span>Installed</span><span>Newest</span><span>Status</span><span></span></div>
    ${Object.entries(eng).map(([k, e]) => row(k, e)).join("") || '<div class="empty">Press “Check for updates” to compare the cores on your servers with the newest releases.</div>'}
    ${d.job ? `<div class="mut" style="margin-top:6px">Last update: <b>${esc(d.job.engine)} ${esc(d.job.version || "")}</b> ${d.job.done ? `— ${(d.job.items || []).filter(i => i.state === "ok").length} of ${(d.job.items || []).length} servers updated${(d.job.items || []).some(i => i.state !== "ok") ? ` <span class="err">(${(d.job.items || []).filter(i => i.state !== "ok").map(i => esc(i.name)).join(", ")} failed)</span>` : ""}` : "(running…)"}${d.job.error ? ` <span class="err">${esc(d.job.error)}</span>` : ""}</div>` : ""}`;
  if (!d.checked_at || Date.now() / 1000 - d.checked_at > 3600) { if (!enginesLoad.busy) { enginesLoad.busy = true; api("/api/engines/check", {method: "POST"}).then(() => enginesLoad()).catch(() => {}).finally(() => { enginesLoad.busy = false; }); } }   // checked by itself when the last check is old
};
window.engineCheck = async () => { toast("Asking every server… (up to a minute)"); try { await api("/api/engines/check", {method: "POST"}); toast("✓ Checked", "ok"); enginesLoad(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.engineUpdate = async k => { if (!await gconfirm(`Update ${k} on every outdated server? Each server's tunnel units restart (a few seconds of downtime per server).`)) return; toast("Updating… (minutes)"); try { const r = await api(`/api/engines/${k}/update`, {method: "POST"}); toast(r.error ? "✕ " + r.error : `✓ ${r.items.filter(i => i.state === "ok").length} updated`, r.error ? "bad" : "ok"); enginesLoad(); } catch (e) { toast("✕ " + e.message, "bad"); } };
PAGES.tunnelsList = async () => {
  const list = await api("/api/tunnels");
  state.tunnelsCache = list;
  const rows = list.filter(t => !state.q || (t.name + t.iran_name + t.foreign_name + t.type).toLowerCase().includes(state.q));
  const stText = t => t.state === "up" ? "ACTIVE" : t.state === "down" ? "DOWN" : t.state === "degraded" ? "DEGRADED" : t.state === "starting" ? "STARTING" : "UNKNOWN";
  const ports = t => String((t.record || {}).port_mapping || (t.forwarded_ports || []).join(",")).split(",").map(x => x.trim()).filter(Boolean);
  const row = t => { const q = t.quality || {}, gre = t.type === "backhaul" || t.type === "gre", d = t.detail || {}; return `<tr class="${t.state} click" onclick="toggleDetail(${t.id})">
      <td class="name">${esc(t.name)}</td>
      <td><span class="td-type">${esc((t.type || "").toUpperCase())}${t.transport ? " · " + esc(t.transport.replace("l3:", "")) : ""}</span><small class="mut"> ${gre ? "GRE" : (t.record || {}).direction === "direct" ? "direct" : "public"}</small></td>
      <td><span class="st ${t.state}">${stText(t)}</span></td>
      <td class="td-node"><b>${esc(t.iran_name)}</b><span class="mono mut">${esc(t.iran_host)}</span></td>
      <td class="td-node"><b>${esc(t.foreign_name)}</b><span class="mono mut">${esc(t.foreign_host)}</span></td>
      <td><div class="td-ports">${ports(t).slice(0, 6).map(p => `<i class="${d.listen && d.listen[parseInt(p)] === false ? "bad" : ""}">${esc(p)}</i>`).join("")}${ports(t).length > 6 ? `<i>+${ports(t).length - 6}</i>` : ""}${ports(t).length ? "" : '<span class="mut">—</span>'}</div></td>
      <td class="num"><b class="${t.rtt_ms == null ? "mut" : t.rtt_ms > 150 ? "warn" : ""}">${t.rtt_ms != null ? t.rtt_ms.toFixed(0) + " ms" : "—"}</b>${d.loss_pct ? `<small class="mut"> ${d.loss_pct}% loss</small>` : ""}</td>
      <td class="num hide-sm">${t.rx_mbps != null ? "↓" + mbps(t.rx_mbps) + " ↑" + mbps(t.tx_mbps) : "—"}</td>
      <td class="num" title="${esc(t.last_error || (q.latency_ms != null ? q.latency_ms + " ms · jitter " + q.jitter_ms + " · loss " + q.loss_pct + "%" : ""))}">${q.score != null ? `<b class="${q.score >= 70 ? "gw-ok" : q.score >= 40 ? "warn" : "err"}">${q.score}</b>` : '<span class="mut">—</span>'}${t.last_error ? `<small class="err"> ${esc(t.last_error).slice(0, 30)}</small>` : ""}</td>
      <td><div class="td-act"><button class="sm" onclick="event.stopPropagation();checkTunnel(${t.id})" title="Health check now">Health</button><button class="sm" onclick="event.stopPropagation();qualityOne(${t.id})" title="Latency, jitter, loss, speed, score">Quality</button><button class="sm danger" onclick="event.stopPropagation();deleteTunnel(${t.id}, '${esc(t.name)}')">Delete</button></div></td></tr>`; };
  const creating = false;
  $("#page").innerHTML = `<div class="toolbar"><button class="pri" onclick="openBuild()">＋ Create tunnel</button><button onclick="qualityAll()" title="Latency, jitter, loss and a score of every tunnel (10 s each)">📊 Quality test all</button><button onclick="detectTunnels()" title="Scan every server for tunnels that are not in this list - complete pairs can be added, incomplete leftovers removed">🔍 Detect on servers</button><span class="mut">${list.filter(t => t.state === "up").length} up · ${list.filter(t => t.state === "down").length} down · ${list.filter(t => t.state === "degraded").length} degraded</span><span class="sp"></span><input id="q" placeholder="Search…" value="${esc(state.q)}" oninput="setQ(this.value)"></div>
    ${TUN_TYPES.filter(ty => rows.some(t => tunType(t) === ty)).map(ty => `<div class="nk-ghead"><b>${esc(TUN_LABEL[ty] || ty)}</b><span>${rows.filter(t => tunType(t) === ty).length}</span></div>
    <table class="tbl lift tun-tbl"><colgroup><col style="width:13%"><col style="width:12%"><col style="width:8%"><col style="width:11%"><col style="width:11%"><col style="width:12%"><col style="width:9%"><col style="width:10%"><col><col style="width:250px"></colgroup>
      <thead><tr><th>Tunnel</th><th>Type</th><th>Status</th><th>Iran node</th><th>Foreign node</th><th>Ports</th><th>Latency</th><th>Traffic</th><th>Score</th><th style="text-align:right">Actions</th></tr></thead>
      <tbody>${rows.filter(t => tunType(t) === ty).map(row).join("")}</tbody></table>`).join("") || '<div class="empty">No tunnel.</div>'}`;
  const openT = list.find(t => t.id === state.open);
  if (openT) tunModal(openT);
  else if (state.detect) detectHtml();
  else if (state.build) { const story = $("#build-story"); const keep = story && !story.classList.contains("hidden") ? story.innerHTML : ""; showModal(`<b>＋ Create tunnel</b>`, await buildFormHtml(), "wide"); if (keep) { const s2 = $("#build-story"); s2.classList.remove("hidden"); s2.innerHTML = keep; } }
  else hideModal();
  if (state.open) loadTunnelCharts(state.open);
};
function detailRow(t) {
  const d = t.detail || {}, u = t.units || {};
  const unit = (side, name) => `<div><span>${esc(name)}</span> <b class="${(d[side + "_units"] || {})[name] === "active" ? "" : "err"}">${esc((d[side + "_units"] || {})[name] || "?")}</b></div>`;
  const tab = state.wtab || "info";
  const tabs = `<div class="tabs wtabs">${[["info", "Info"], ["charts", "Charts 24 h"], ["actions", "Actions"]].map(([k, l]) => `<button class="${tab === k ? "on" : ""}" onclick="wtab('${k}')">${l}</button>`).join("")}</div>`;
  const tool = (icon, title, sub, click, cls = "") => `<a class="wtool ${cls}" onclick="${click}"><i>${icon}</i><b>${title}</b><small>${sub}</small></a>`;
  if (tab === "charts") return tabs + `<div class="charts" id="tun-charts-${t.id}"><div class="mut">loading 24 h…</div></div>`;
  if (tab === "actions") return tabs + `<div class="wgroup"><h4>Repair</h4><div class="wtools">${t.state === "up" ? tool("🛠", "Fix", "nothing to fix: the tunnel is healthy", "", "disabled") + tool("👁", "Dry run", "nothing to fix: the tunnel is healthy", "", "disabled") : tool("🛠", "Fix", "diagnose and repair step by step: restarts only what is not working", `fixTunnel(${t.id}, false)`) + tool("👁", "Dry run", "show what Fix would do, change nothing", `fixTunnel(${t.id}, true)`)}${tool("📊", "Quality", "latency, jitter, loss, speed, score", `qualityOne(${t.id})`)}${tool("🔔", "Mute alerts", "no Telegram alerts until unmuted in Settings", `muteRef('${esc(t.name)}', true)`)}</div></div>
    <div class="wgroup"><h4>Change</h4><div class="wtools">${t.type === "backhaul" ? tool("⇅", "Ports", "change the forwarded ports", `editPorts(${t.id}, '${esc((t.record || {}).port_mapping || (t.forwarded_ports || []).join(","))}')`) : ""}${tool("♻", "Rebuild", "remove and build again, same name and ports", `rebuildTunnel(${t.id}, '${esc(t.name)}')`)}${tool("📜", "History", "every repair of this tunnel", "go('actions')")}</div></div>
    <div class="wgroup"><h4>Danger</h4><div class="wtools">${tool("🗑", "Remove", "from both servers; users lose their connection", `deleteTunnel(${t.id}, '${esc(t.name)}')`, "danger")}</div></div>
    <div id="fix-${t.id}" class="fix hidden"></div>`;
  const rec = t.record || {}, q = t.quality || {}, gre = t.type === "backhaul" || t.type === "gre";
  const unitRow = (side, label, name) => irow(label, `<span class="mono">${esc(name)}</span> <b class="${(d[side + "_units"] || {})[name] === "active" ? "gw-ok" : "err"}">${esc((d[side + "_units"] || {})[name] || "?")}</b>`);
  const boxes = [
    ibox("⇄", "Servers", [irow("Iran", `<b>${esc(t.iran_name)}</b> <span class="mono mut">${esc(t.iran_host)}</span>`), irow("Foreign", `<b>${esc(t.foreign_name)}</b> <span class="mono mut">${esc(t.foreign_host)}</span>`), irow("Type", `${esc((t.type || "").toUpperCase())}${t.transport ? " · " + esc(t.transport.replace("l3:", "")) : ""}`), irow("Direction", esc(rec.direction || "reverse"))]),
    ibox("📈", "Quality", q.score != null ? [irow("Score", `<b class="${q.score >= 70 ? "gw-ok" : q.score >= 40 ? "warn" : "err"}">${q.score} / 100</b>`), irow("Latency", q.latency_ms + " ms"), irow("Jitter / loss", `${q.jitter_ms} ms · ${q.loss_pct}%`), irow("Speed", q.down_mbps != null ? `↓ ${q.down_mbps} ↑ ${q.up_mbps} Mb/s` : "—"), irow("Measured", q.at ? ago(q.at) + " ago" : "—")] : [irow("Not measured yet", '<button class="sm" onclick="qualityOne(' + t.id + ')">Measure now</button>')]),
    ibox("🔗", "Link", [gre ? irow("GRE", `${esc(u.iface || "—")} ${d.iface ? "· " + esc(d.iface) : ""}`) : irow("Engine", esc(rec.transport_label || t.type)), irow("Tunnel IPs", t.iran_tunnel_ip ? `<span class="mono">${esc(t.iran_tunnel_ip)} ⇄ ${esc(t.foreign_tunnel_ip)}</span>` : "—"), irow("Control port", t.port || "—"), irow("Ports", `<div class="td-ports">${(t.forwarded_ports || []).map(p => `<i class="${d.listen && d.listen[p] === false ? "bad" : ""}">${p}</i>`).join("") || "—"}</div>`), irow("Packet loss", d.loss_pct != null ? d.loss_pct + "%" : "—")]),
    ibox("🕒", "Status", [irow("State", `<span class="st ${t.state}">${esc(t.state)}</span>`), irow("Since", t.state_since ? ago(t.state_since) + " ago" : "—"), irow("Checked", when(t.last_check_at)), irow("Created", when(t.created_at)), irow("RTT", t.rtt_ms != null ? t.rtt_ms.toFixed(0) + " ms" : "—"), irow("Traffic", t.rx_mbps != null ? `↓ ${mbps(t.rx_mbps)} ↑ ${mbps(t.tx_mbps)}` : "—"), d.iran_error ? irow("Iran error", `<span class="err">${esc(d.iran_error)}</span>`) : "", d.foreign_error ? irow("Foreign error", `<span class="err">${esc(d.foreign_error)}</span>`) : "", t.last_error ? irow("Last error", `<span class="err">${esc(t.last_error)}</span>`) : ""]),
    ibox("⚙", "Services", [...(u.iran || []).map(n => unitRow("iran", "Iran", n)), ...(u.foreign || []).map(n => unitRow("foreign", "Foreign", n))]),
  ].join("");
  return tabs + `<div class="iboxes">${boxes}</div>    <div id="fix-${t.id}" class="fix hidden"></div>`;
}
async function loadTunnelCharts(id) {
  const box = document.getElementById("tun-charts-" + id);
  if (!box) return;
  const d = await api(`/api/tunnels/${id}/checks?hours=${state.mrange}`);
  const ci = n => d.columns.indexOf(n), rows = d.checks, ts = rows.map(r => r[0]), col = n => rows.map(r => r[ci(n)]), series = n => ts.map((t, i) => [t, col(n)[i]]);
  CH_reset();
  const oks = col("ok"), up = oks.filter(Boolean).length, upPct = oks.length ? up * 100 / oks.length : null, rtt = nums(col("rtt_ms")), rx = nums(col("rx_mbps")), tx = nums(col("tx_mbps"));
  // outages: runs of failed checks -> start, length, first error
  const out = []; let cur = null;
  rows.forEach(r => { if (!r[ci("ok")]) { if (!cur) cur = {from: r[0], to: r[0], err: r[ci("error")] || ""}; cur.to = r[0]; if (!cur.err) cur.err = r[ci("error")] || ""; } else if (cur) { cur.to = r[0]; out.push(cur); cur = null; } });
  if (cur) { cur.open = true; cur.to = Date.now() / 1000; out.push(cur); }
  const down = out.reduce((a, o) => a + (o.to - o.from), 0), f1 = v => v == null ? "—" : (Math.round(v * 10) / 10);
  box.className = "mon";
  box.innerHTML = `<div class="mhead">${rangeTabs()}<small class="mut">${rows.length} checks · one every minute · hover a chart for exact values</small></div>
    <div class="mtiles">${mtile("Uptime", upPct == null ? "—" : f1(upPct) + "%", `${up} of ${rows.length} checks up`, upPct != null && upPct < 95 ? "bad" : upPct != null && upPct < 99.5 ? "warn" : "ok")}${mtile("Outages", out.length, out.length ? "down " + durText(down) + " in total" : "none in this range", out.length ? "warn" : "ok")}${mtile("Latency", rtt.length ? Math.round(avgOf(rtt)) + " ms" : "—", rtt.length ? "best " + Math.round(Math.min(...rtt)) + " · worst " + Math.round(Math.max(...rtt)) + " ms" : "")}${mtile("Traffic", "↓ " + f1(avgOf(rx)), "↑ " + f1(avgOf(tx)) + " Mb/s avg · peak ↓ " + f1(rx.length ? Math.max(...rx) : null) + " ↑ " + f1(tx.length ? Math.max(...tx) : null))}</div>
    <div class="mcard mwide"><h6>Up / down timeline</h6>${strip(oks)}</div>
    <div class="mgrid">${chartCard("Latency (RTT)", series("rtt_ms"), {unit: " ms", warn: 150, bad: 300, fmt: v => Math.round(v)})}${chartCard("Download ↓", series("rx_mbps"), {unit: " Mb/s"})}${chartCard("Upload ↑", series("tx_mbps"), {unit: " Mb/s"})}
      <div class="mcard"><h6>Outages <span class="mval">${out.length}</span></h6>${out.length ? out.slice(-8).reverse().map(o => `<div class="irow"><span>${fmtT(o.from, 1)} · ${durText(o.to - o.from)}${o.open ? " (still down)" : ""}</span><div class="err" title="${esc(o.err)}">${esc((o.err || "no answer").slice(0, 46))}</div></div>`).join("") : '<div class="mut chart-empty">no outage in this range</div>'}</div></div>`;
}/* Create tunnel: five engines, each with its transports and options; the form keeps its values when the engine changes */
const bk = {kinds: null, kind: localStorage.getItem("nk.bk.kind") || "backhaul", transport: "", v: {}, guide: true};
const BK_ICON = {backhaul: "⇄", backpack: "🎒", backpack_direct: "🧭", paqet: "📦", chisel: "⛏"};
/* what to pick, and why - the defaults below every engine are already the many-users settings (mux, 8 mux channels, 16-24 pool, keepalive 75 s, heartbeat 20 s, BBR + fq on both kernels) */
window.bkPick = (k, t) => { bk.kind = k; bk.transport = t; localStorage.setItem("nk.bk.kind", k); };
const BK_GUIDE = [
  ["⭐ Many users, clean route", "Backhaul over GRE · TCP Mux", "backhaul", "tcpmux", "hundreds of users over a few connections; the GRE link hides the real traffic. The default for every tunnel in this panel."],
  ["GRE filtered between the two servers", "BackPack · TCP Mux", "backpack", "tcpmux", "same multiplexing as backhaul, but over a plain TCP control port - works where GRE is dropped."],
  ["Heavy filtering / DPI", "BackPack · TCP + Stealth or WebSocket TLS Mux", "backpack", "stealth", "Stealth looks like random bytes; WSS Mux looks like HTTPS and can sit behind a CDN."],
  ["Voice, games, low latency", "BackPack Direct · UDP or QUIC", "backpack_direct", "udp", "an L3 link the Iran side dials; no TCP-over-TCP, lowest latency - UDP must be open."],
  ["TCP throttled, a whitelisted domain exists", "BackPack Direct · PCK / SNI", "backpack_direct", "sni", "raw TCP frames that look like a normal flow; SNI adds a TLS hello naming the allowed domain."],
  ["Provider stalls ordinary TCP", "Paqet · KCP over raw sockets", "paqet", "kcp", "one KCP session in raw packets, aes-128-gcm; good on lossy routes, needs raw sockets on both sides."],
  ["Small group, simplest setup", "Chisel · TLS WebSocket", "chisel", "wss", "one HTTPS port on the Iran server, key pinned; fine for tens of users, CDN-able."],
  ["Everything else is blocked", "BackPack · xDi (ICMP)", "backpack", "xdi", "KCP inside ping packets; slow, last resort."],
];
/* two or three words per type, shown in the drop-down */
const BK_FOR = {"backhaul:tcpmux": "many users, normal route ⭐", "backhaul:tcp": "few users, plain route", "backhaul:wsmux": "behind a CDN, clear", "backhaul:wssmux": "behind a CDN, TLS",
  "backpack:tcpmux": "many users, GRE blocked", "backpack:tcp": "few users, GRE blocked", "backpack:stealth": "very heavy filtering", "backpack:pck": "Iran stalls plain TCP (root)", "backpack:kcp": "lossy route, games", "backpack:udp": "UDP-only services", "backpack:quic": "UDP open, wants QUIC", "backpack:ws": "behind your own TLS", "backpack:wsmux": "WebSocket + many users", "backpack:wss": "must look like HTTPS", "backpack:wssmux": "heavy filtering + many users", "backpack:xdi": "Iran blocks almost everything",
  "backpack_direct:udp": "low latency, voice, games", "backpack_direct:quic": "low latency, UDP open", "backpack_direct:pck": "Iran throttles TCP (root)", "backpack_direct:sni": "Iran only allows a domain", "backpack_direct:xdi": "UDP and TCP both blocked",
  "paqet:kcp": "ordinary TCP stalls, raw sockets", "chisel:ws": "small group, simplest", "chisel:wss": "small group, looks like HTTPS"};
async function buildFormHtml() {
  const [servers, kd] = await Promise.all([api("/api/servers"), bk.kinds ? Promise.resolve({kinds: bk.kinds}) : api("/api/tunnels/kinds")]);
  bk.kinds = kd.kinds;
  const K = bk.kinds.find(k => k.key === bk.kind) || bk.kinds[0]; bk.kind = K.key;
  const tr = K.transports.find(t => t.key === bk.transport) || K.transports[0]; bk.transport = tr.key;
  const srvOpt = role => servers.filter(s => s.role === role && s.enabled);
  void srvOpt;
  const v = bk.v, val = (n, d) => esc(v[n] != null ? v[n] : d);
  const opt = (role, name) => servers.filter(s => s.role === role && s.enabled).map(s => `<option value="${s.id}" ${String(s.id) === String(v[name]) ? "selected" : ""}>${esc(s.name)} — ${esc(s.host)}${s.online === false ? " (offline)" : ""}</option>`).join("");
  const num = (name, label, d, min, max, small) => `<label>${label}${small ? ` <small>${small}</small>` : ""}<input name="${name}" type="number" min="${min}" max="${max}" value="${val(name, d)}"></label>`;
  const chk = (name, label, small, dflt = false) => `<label class="chk"><input name="${name}" type="checkbox" ${(name in bk.chk ? bk.chk[name] : dflt) ? "checked" : ""}> ${label}${small ? ` <small class="mut">${small}</small>` : ""}</label>`;
  const nodelay = chk("nodelay", "TCP no-delay", "lower latency for small packets", true);
  const sel = (name, label, list, dflt) => `<label>${label}<select name="${name}">${list.map(p => `<option ${val(name, dflt) === p ? "selected" : ""}>${p}</option>`).join("")}</select></label>`;
  const muxed = BP_MUXED.includes(tr.key);
  let options = "";
  if (K.key === "backhaul") options = num("mtu", "GRE MTU", 0, 0, 1476, "(0 = measure)") + num("keepalive", "Keepalive (s)", 75, 10, 600) + num("heartbeat", "Heartbeat (s)", 20, 5, 120) + num("connection_pool", "Connection pool", muxed ? 16 : 32, 1, 256, "(client connections kept ready)") + (muxed ? num("mux_con", "Mux channels", 8, 1, 64) : "") + num("channel_size", "Channel size", 8192, 1024, 65536, "(buffer)") + num("dial_timeout", "Dial timeout (s)", 10, 3, 60) + num("retry_interval", "Retry interval (s)", 2, 1, 30) + sel("log_level", "Log level", ["warn", "error", "info", "debug"], "warn") + nodelay + chk("aggressive_pool", "Aggressive pool", "reconnect lost pool connections at once") + chk("tune", "Tune the kernel", "BBR + fq + big buffers on both servers", true);
  else if (K.key === "backpack") options = `<label>Preset<select name="preset">${["turbo", "balanced", "stable"].map(p => `<option ${val("preset", "turbo") === p ? "selected" : ""}>${p}</option>`).join("")}</select></label>` + num("keepalive", "Keepalive (s)", 75, 10, 600) + num("heartbeat", "Heartbeat (s)", 20, 5, 120) + num("connection_pool", "Connection pool", BP_MUXED.includes(tr.key) ? 16 : 24, 1, 256, "(client connections kept ready)") + (BP_MUXED.includes(tr.key) ? num("mux_con", "Mux channels", 8, 1, 64) : "") + num("channel_size", "Channel size", 8192, 1024, 65536, "(buffer)") + nodelay + chk("accept_udp", "Forward UDP too", "the same ports over UDP");
  else if (K.key === "backpack_direct") options = (tr.key === "sni" ? `<label>SNI domain <small>(a domain your route allows)</small><input name="sni_domain" value="${val("sni_domain", "")}" placeholder="cdn.example.com" required></label>` : "") + (tr.key === "pck" || tr.key === "sni" ? num("mtu", "Carrier MTU", 1380, 1000, 1500) : "") + chk("accept_udp", "Forward UDP too");
  else if (K.key === "paqet") options = `<label>KCP mode<select name="kcp_mode">${["fast", "normal", "fast2", "fast3"].map(p => `<option ${val("kcp_mode", "fast") === p ? "selected" : ""}>${p}</option>`).join("")}</select></label>` + num("conn", "Connections", 4, 1, 32) + num("mtu", "KCP MTU", 1350, 500, 1500) + chk("accept_udp", "Forward UDP too");
  else options = chk("accept_udp", "Forward UDP too", "the same ports over UDP");
  const typeSel = `<select name="ttype" onchange="bkType(this.value)">${bk.kinds.map(k => `<optgroup label="${esc(k.title)}">${k.transports.map(t => `<option value="${k.key}|${t.key}" ${k.key === K.key && t.key === tr.key ? "selected" : ""}>${esc(k.title)} · ${esc(t.label)} — ${esc(BK_FOR[k.key + ":" + t.key] || BK_FOR[k.key] || "")}</option>`).join("")}</optgroup>`).join("")}</select>`;
  return `<form class="frm build" onsubmit="return startBuild(event)" oninput="bkKeep(this)">
    <div class="bgrid">
      <label>Iran server <small><a onclick="autoIran(this.closest('form'))" title="Ping the foreign server from every Iran server, look at load and how many tunnels each one carries, pick the best">choose for me</a></small><select name="iran_id" required>${opt("iran", "iran_id")}</select></label>
      <label>Foreign server<select name="foreign_id" required>${opt("foreign", "foreign_id")}</select></label>
      <label>Ports to forward <small>(443, 443=2096, 10000-10009)</small><input name="ports" required placeholder="8072" value="${val("ports", "")}"></label>
      <label>Tunnel type <small>(the defaults are the many-users settings)</small>${typeSel}</label></div>
    <div id="auto-iran" class="mut"></div>
    <div class="badv"><a onclick="bkAdv()">${bk.adv ? "▾ Advanced settings" : "▸ Advanced settings"} <small class="mut">${bk.adv ? "— change a value only when you measured a reason" : "— keepalive, heartbeat, MTU, preset, UDP…"}</small></a>${bk.adv ? `<div class="bgrid" style="margin-top:8px">${options}</div>` : ""}</div>
    <div class="frm-act"><button class="pri" type="submit">Build</button><button type="button" onclick="startTest(event)" title="Build a temporary tunnel of this kind between these two servers, measure it, remove it">Test this pair first</button></div>
  </form><div id="build-story" class="fix hidden"></div>`;
}
/* the Create window redraws only its own body: no refresh of the page behind it, the typed values stay */
async function bkRedraw() { const body = $("#modal .modal-body"); if (!body) return refresh(); const form = $("#modal form.build"); if (form) bkKeep(form); const story = $("#build-story"), keep = story && !story.classList.contains("hidden") ? story.innerHTML : ""; body.innerHTML = await buildFormHtml(); if (keep) { const s2 = $("#build-story"); s2.classList.remove("hidden"); s2.innerHTML = keep; } }
window.bkAdv = () => { bk.adv = !bk.adv; bkRedraw(); };
window.bkType = v => { const [k, t] = v.split("|"); bk.kind = k; bk.transport = t; localStorage.setItem("nk.bk.kind", k); bkRedraw(); };
const BP_MUXED = ["tcpmux", "wsmux", "wssmux"];
bk.chk = {};   // every checkbox the form ever showed -> its last state (a box that is not on screen keeps its default)
window.bkKeep = form => { const f = new FormData(form); bk.v = {__t: "1"}; for (const [k, x] of f.entries()) bk.v[k] = x; form.querySelectorAll("input[type=checkbox]").forEach(cb => { bk.chk[cb.name] = cb.checked; }); };
window.bkKind = k => { bk.kind = k; bk.transport = ""; localStorage.setItem("nk.bk.kind", k); bkRedraw(); };
window.bkTransport = t => { bk.transport = t; bkRedraw(); };
function bkBody(f) {
  const body = {iran_id: +f.get("iran_id"), foreign_id: +f.get("foreign_id"), ports: (f.get("ports") || "").trim(), kind: bk.kind, transport: f.get("transport") || "", opts: {}};
  if (bk.kind === "backhaul") {
    body.mtu = +f.get("mtu") || 0; body.keepalive = +f.get("keepalive") || 75;
    body.nodelay = $("#modal [name=nodelay]") ? f.get("nodelay") === "on" : bk.chk.nodelay !== false;
    for (const k of ["heartbeat", "connection_pool", "mux_con", "channel_size", "dial_timeout", "retry_interval"]) if (f.get(k)) body.opts[k] = +f.get(k);
    if (f.get("log_level")) body.opts.log_level = f.get("log_level");
    body.opts.aggressive_pool = $("#modal [name=aggressive_pool]") ? f.get("aggressive_pool") === "on" : !!bk.chk.aggressive_pool;
    body.opts.tune = $("#modal [name=tune]") ? f.get("tune") === "on" : bk.chk.tune !== false;
  }
  else for (const k of ["preset", "keepalive", "heartbeat", "sni_domain", "mtu", "kcp_mode", "conn", "connection_pool", "mux_con", "channel_size"]) if (f.get(k) != null && f.get(k) !== "") body.opts[k] = isNaN(+f.get(k)) ? f.get(k) : +f.get(k);
  if (bk.kind === "backpack") body.opts.nodelay = $("#modal [name=nodelay]") ? f.get("nodelay") === "on" : bk.chk.nodelay !== false;
  if (f.get("accept_udp") === "on") body.opts.accept_udp = true;
  return body;
}
window.autoIran = async form => { const f = new FormData(form), box = $("#auto-iran"); box.innerHTML = '<div class="fx-line run"><i></i>Pinging the foreign server from every Iran server…</div>'; try { const r = await api("/api/tunnels/auto-iran", {method: "POST", body: JSON.stringify({foreign_id: +f.get("foreign_id"), ports: f.get("ports") || ""})}); const best = r.ranking.find(x => x.score > 0); if (best) form.querySelector("[name=iran_id]").value = best.id; box.innerHTML = `<div class="ob-inbounds">${r.ranking.map((x, i) => `<span><b class="${x.score >= 70 ? "gw-ok" : x.score ? "" : "err"}">${i + 1}. ${esc(x.name)} · ${x.score}</b> <small class="mut">${esc(x.reason)}</small></span>`).join("")}</div>${best ? `<small>Chosen: <b>${esc(best.name)}</b> — change it above if you prefer another.</small>` : '<small class="err">No Iran server can carry this tunnel right now.</small>'}`; } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; } };
window.startTest = async e => {
  const f = new FormData(e.target.closest("form"));
  const kind = bk.kind === "backhaul" ? (f.get("transport") || "tcpmux") : bk.kind === "backpack" ? "bp:" + f.get("transport") : bk.kind === "backpack_direct" ? "bpd:" + f.get("transport") : bk.kind === "paqet" ? "paqet" : (f.get("transport") === "wss" ? "chisel:tls" : "chisel");
  const box = $("#build-story"); box.classList.remove("hidden"); box.innerHTML = '<div class="fx-line run"><i></i>Building a temporary tunnel…</div>';
  try { const r = await api("/api/tunnel-test/compare", {method: "POST", body: JSON.stringify({iran_id: +f.get("iran_id"), foreign_id: +f.get("foreign_id"), kinds: [kind]})}); tt.job = {id: r.id, kinds: [kind], results: [], current: kind, done: false}; toast("Test started — the result shows in Tunnel test"); setSub("tunnels", "test"); }
  catch (err) { box.innerHTML = `<div class="fx-line bad"><b>!</b>${esc(err.message)}</div>`; }
};
window.newTunnel = () => { state.edit = "newtunnel"; state.open = null; refresh(); };
window.startBuild = async e => {
  e.preventDefault();
  const body = bkBody(new FormData(e.target));
  const box = $("#build-story"); box.classList.remove("hidden"); box.innerHTML = '<div class="fx-line run"><i></i>Starting…</div>';
  try {
    const r = await api("/api/tunnels", {method: "POST", body: JSON.stringify(body)});
    await followStory(r.id, box, 400);
  } catch (err) { box.innerHTML = `<div class="fx-line bad"><b>!</b>${esc(err.message)}</div>`; }
  return false;
};
async function followStory(aid, box, maxPolls) {
  for (let i = 0; i < maxPolls; i++) {
    await new Promise(res => setTimeout(res, 1500));
    const s = await api(`/api/actions/${aid}`);
    box.innerHTML = storyHtml(s);
    if (s.done) return s;
  }
}
window.deleteTunnel = async (id, name) => {
  if (!await gconfirm(`Remove the tunnel ${name} from BOTH servers? Users on it lose their connection. This cannot be undone.`)) return;
  if (await gprompt(`Type the tunnel name to confirm:`) !== name) { toast("Not removed", "bad"); return; }
  toast("Removing…");
  try { const r = await api(`/api/tunnels/${id}`, {method: "DELETE"}); toast("✓ " + r.results.join(" · "), "ok"); state.open = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.rebuildTunnel = async (id, name) => {
  if (!await gconfirm(`Rebuild ${name}: remove it from both servers and build it again with the same name and ports? It is down while this runs (about a minute).`)) return;
  const box = document.getElementById("fix-" + id); if (!box) return;
  box.classList.remove("hidden"); box.innerHTML = '<div class="fx-line run"><i></i>Removing and rebuilding…</div>'; state.edit = "fix";
  try { const r = await api(`/api/tunnels/${id}/rebuild`, {method: "POST"}); await followStory(r.id, box, 400); } catch (e) { box.innerHTML = `<div class="fx-line bad"><b>!</b>${esc(e.message)}</div>`; }
  state.edit = null;
};
/* opening a window uses what the page already holds - no API call, no redraw of the list behind it */
window.toggleDetail = id => {
  state.edit = null; state.wtab = null;
  if (state.open === id) { state.open = null; hideModal(); return; }
  state.open = id;
  if (state.page === "nodes") { const s = (state.serversCache || []).find(x => x.id === id); if (s) { srvModal(s); return; } }
  if (state.page === "tunnels") { const t = (state.tunnelsCache || []).find(x => x.id === id); if (t) { tunModal(t); return; } }
  refresh();
};
window.fixTunnel = async (id, dry) => {
  if (!dry && !await gconfirm("Repair this tunnel now? The panel restarts only the units that are not working, one at a time, and checks after each step.")) return;
  const box = document.getElementById("fix-" + id);
  if (!box) return;
  box.classList.remove("hidden"); box.innerHTML = '<div class="fx-line run"><i></i>Starting…</div>';
  state.edit = "fix";   // pause auto-refresh while the story is running
  try {
    const r = await api(`/api/tunnels/${id}/fix?dry_run=${dry}`, {method: "POST"});
    for (let i = 0; i < 150; i++) {
      await new Promise(res => setTimeout(res, 1500));
      const s = await api(`/api/actions/${r.id}`);
      box.innerHTML = storyHtml(s);
      if (s.done) break;
    }
  } catch (e) { box.innerHTML = `<div class="fx-line bad"><b>!</b>${esc(e.message)}</div>`; }
  state.edit = null;
};
function storyHtml(s) {
  const cls = l => /healthy|works|finished|succeeded/i.test(l) ? "ok" : /not repaired|failed|still|does not answer|No safe|read-only|Stopped/i.test(l) ? "bad" : /^Plan:|^\[dry run\]/.test(l) ? "plan" : "do";
  return s.story.map(l => `<div class="fx-line ${cls(l)}"><b>${{ok: "✓", bad: "!", plan: "›", do: "·"}[cls(l)]}</b><span>${esc(l)}</span></div>`).join("") +
    (s.done ? `<div class="fx-line ${s.ok ? "ok" : "bad"}"><b>${s.ok ? "✓" : "✕"}</b><span>${s.dry_run ? "Dry run" : s.action === "build" ? "Build" : s.action === "delete" ? "Removal" : "Repair"} ${s.ok ? "finished" : "did not succeed"}.</span></div>` : '<div class="fx-line run"><i></i>Working…</div>');
}
/* ---- Smart repair: what needs attention, the network advisor ("which tunnel is best today"), the history ---- */
const ap = {job: null, tid: localStorage.getItem("nk.ap.tid") || "", timer: null};
const AP_LABEL = {tcpmux: "Backhaul · TCP Mux (GRE)", "bp:tcpmux": "BackPack · TCP Mux", "bp:wssmux": "BackPack · WebSocket TLS Mux", "chisel:tls": "Chisel · TLS WebSocket", "bpd:udp": "BackPack Direct · UDP", tcp: "Backhaul · TCP", wsmux: "Backhaul · WS Mux", wssmux: "Backhaul · WSS Mux", paqet: "Paqet · KCP", chisel: "Chisel · WebSocket", "bp:stealth": "BackPack · Stealth", "bp:tcp": "BackPack · TCP"};
const apName = k => AP_LABEL[k] || k;
PAGES.actions = async () => {
  const [d, tl] = await Promise.all([api("/api/actions?limit=60"), api("/api/tunnels")]);
  const bad = tl.filter(t => t.state !== "up"), pick = tl.find(t => String(t.id) === ap.tid) || bad[0] || tl[0];
  const row = t => `<div class="ap-row"><span class="st ${t.state}">${esc(t.state)}</span><b>${esc(t.name)}</b><span class="mut">${esc(t.iran_name)} ⇄ ${esc(t.foreign_name)} · ${esc(t.type)}${t.transport ? " · " + esc(t.transport.replace("l3:", "")) : ""}</span><span class="mut ap-err">${esc((t.last_error || "").slice(0, 70))}</span><span class="sp"></span><button class="sm pri" onclick="apDiagnose(${t.id})">🩺 Diagnose</button></div>`;
  $("#page").innerHTML = `<div class="card"><h3>🩺 Needs attention <span class="sp"></span><span class="pill ${bad.length ? "err" : "ok"}">${bad.length ? bad.length + " of " + tl.length + " not healthy" : "all " + tl.length + " healthy"}</span></h3>
      ${bad.length ? bad.map(row).join("") : '<div class="mut" style="padding:6px 0">Every tunnel is up. Nothing to repair. When one fails, it shows here: Diagnose finds the cause (servers, services, the route, GRE, the control port) and offers the fix - restart, rebuild, another tunnel type, or another Iran server.</div>'}
      <small class="mut">${d.read_only ? "Read-only mode: nothing is changed. Set NIKATEL_READ_ONLY=false on the panel server." : "A panel cannot change a server's public IP (the provider does that). When the route between two servers is cut, “Move” carries the tunnel on another Iran server."}</small></div>
    <div class="card"><h3>🧭 Network advisor — which tunnel is best today <span class="sp"></span></h3>
      <div class="frm"><label>Tunnel <small>(its two servers are tested)</small><select id="ap-tun" onchange="ap.tid=this.value;localStorage.setItem('nk.ap.tid',this.value)">${tl.map(t => `<option value="${t.id}" ${pick && t.id === pick.id ? "selected" : ""}>${esc(t.name)} — ${esc(t.iran_name)} ⇄ ${esc(t.foreign_name)} (${esc(t.type)})</option>`).join("")}</select></label>
        <div class="frm-act" style="align-self:end"><button class="pri" onclick="apRecommend()" ${tl.length ? "" : "disabled"}>Find the best tunnel today</button></div></div>
      <small class="mut">Builds a temporary tunnel of each type between the two servers (TCP Mux over GRE, BackPack TCP Mux, BackPack WS TLS Mux, Chisel TLS, BackPack Direct UDP), measures latency and loss, removes it again. About a minute per type. Nothing of your real tunnel changes until you press “Use”.</small>
      <div id="ap-adv"></div></div>
    <div class="card"><h3>🗒 History <span class="sp"></span><small class="mut">every repair, test and build with its full story</small></h3>${d.actions.map(a => `<div class="ev ${a.ok === true ? "ok" : a.ok === false ? "bad" : "warn"}"><time title="${when(a.ts)}">${ago(a.ts)} ago</time><i></i><small>${esc(a.source)}${a.dry_run ? " · dry" : ""}</small>
      <span><b>${esc(a.ref)}</b> — ${esc(a.story[a.story.length - 1] || "")} <a class="mut" onclick="this.nextElementSibling.classList.toggle('hidden')">details</a><div class="fix hidden">${a.story.map(l => `<div class="fx-line do"><b>·</b><span>${esc(l)}</span></div>`).join("")}</div></span></div>`).join("") || '<div class="empty">No repair yet.</div>'}</div>`;
  apAdvisor();
};
INFO.actions = ["Smart repair", "Finds why a tunnel is down and fixes it: restart, rebuild, another tunnel type or another Iran server - and says which tunnel is best on today's network."];
function apAdvisor() {
  const box = $("#ap-adv"), j = ap.job; if (!box || !j) return;
  const rs = [...(j.results || [])].sort((a, b) => (b.score || 0) - (a.score || 0)), best = rs.find(r => r.ok);
  const run = !j.done;
  box.innerHTML = `<table class="tbl" style="margin-top:10px"><thead><tr><th>Tunnel type</th><th>State</th><th>Latency</th><th>Jitter</th><th>Loss</th><th>Score</th><th></th></tr></thead><tbody>
    ${rs.map(r => `<tr class="${r === best ? "up" : r.ok ? "" : "down"}"><td class="name">${esc(apName(r.kind))}${r === best ? ' <span class="pill ok">best today</span>' : ""}</td><td><span class="st ${r.ok ? "up" : "down"}">${r.error ? "failed" : r.ok ? "works" : "poor"}</span></td><td class="num">${r.latency_ms != null ? r.latency_ms + " ms" : "—"}</td><td class="num">${r.jitter_ms != null ? r.jitter_ms + " ms" : "—"}</td><td class="num">${r.loss_pct != null ? r.loss_pct + "%" : "—"}</td><td><b class="${r.score >= 70 ? "gw-ok" : r.score >= 50 ? "" : "err"}">${r.score != null ? r.score + " / 100" : "—"}</b></td><td>${r.ok ? `<button class="sm ${r === best ? "pri" : ""}" onclick="apUse('${esc(r.kind)}')">Use for this tunnel</button>` : `<span class="mut" title="${esc(r.error || r.verdict || "")}">${esc((r.error || r.verdict || "").slice(0, 36))}</span>`}</td></tr>`).join("")}
    ${run ? `<tr><td class="name">${esc(apName(j.current))}</td><td colspan="6" class="mut">testing… ${j.results.length} of ${j.kinds.length} done</td></tr>` : ""}</tbody></table>
    ${!run && !best ? '<div class="err" style="margin-top:8px">No tunnel type works well between these two servers right now.</div>' : ""}${!run && best ? `<div class="mut" style="margin-top:8px">Best today: <b>${esc(apName(best.kind))}</b>, score ${best.score}. “Use” replaces this tunnel with that type (same name, same ports); if the new one fails, the old type is built again.</div>` : ""}`;
  clearTimeout(ap.timer);
  if (run) ap.timer = setTimeout(apPoll, 3000);
}
async function apPoll() { if (!ap.job || ap.job.done || state.page !== "tunnels") return; try { ap.job = await api(`/api/tunnel-test/compare/${ap.job.id}`); } catch (e) { ap.job.done = true; } apAdvisor(); }
window.apRecommend = async () => {
  const t = (state.tunnelsCache || []).find(x => String(x.id) === ($("#ap-tun") || {}).value) || (await api("/api/tunnels")).find(x => String(x.id) === ($("#ap-tun") || {}).value);
  if (!t) return;
  const sv = (await api("/api/servers")), iran = sv.find(s => s.name === t.iran_name), foreign = sv.find(s => s.name === t.foreign_name);
  try { const r = await api("/api/autopilot/recommend", {method: "POST", body: JSON.stringify({iran_id: iran.id, foreign_id: foreign.id})}); ap.job = {id: r.id, kinds: ["…", "…", "…", "…", "…"], results: [], current: "starting", done: false}; apAdvisor(); apPoll(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.apUse = async kind => {
  const tid = +($("#ap-tun") || {}).value; if (!tid) return;
  if (!await gconfirm(`Replace this tunnel with ${apName(kind)}? It is removed and built again (same name, same ports); users on it reconnect after about a minute. If the new one fails, the old type is built again.`, {danger: true})) return;
  try { const r = await api("/api/autopilot/replace", {method: "POST", body: JSON.stringify({tunnel_id: tid, kind})}); apStoryWindow(r.id, "Replacing the tunnel"); } catch (e) { toast("✕ " + e.message, "bad"); }
};
function apStoryWindow(aid, title) {
  showModal(`<b>${esc(title)}</b>`, '<div id="ap-story" class="fix"><div class="fx-line run"><i></i>Starting…</div></div>', "wide");
  followStory(aid, $("#ap-story"), 600).then(() => refresh());
}
window.apDiagnose = async id => {
  showModal("<b>🩺 Diagnosis</b>", '<div class="mut" style="padding:20px;text-align:center">Looking at both servers, the route and the tunnel… (about 15 s)</div>', "wide");
  try {
    const d = await api(`/api/autopilot/diagnose/${id}`);
    const sw = d.can_switch && d.cause !== "healthy", mv = d.can_move && d.cause !== "healthy";
    showModal(`<span class="st ${d.state}"></span><b>${esc(d.name)}</b> <span class="mut">${esc(d.iran_name)} ⇄ ${esc(d.foreign_name)}</span>`, `
      <div class="ibox"><h5><i>🩺</i>Cause</h5><div style="padding:6px 0;font-size:13.5px"><b class="${d.cause === "healthy" ? "gw-ok" : "err"}">${esc(d.summary)}</b></div></div>
      <div class="ibox" style="margin-top:10px"><h5><i>🔎</i>What was checked</h5>${d.findings.map(f => irow(esc(f.text), f.ok ? '<b class="gw-ok">✓</b>' : '<b class="err">✕</b>')).join("")}</div>
      ${d.cause === "healthy" ? "" : `<div class="wgroup" style="margin-top:12px"><h4>What can be done</h4><div class="wtools">
        ${d.ladder.includes("restart") ? `<a class="wtool" onclick="apRun(${id}, 'rebuild')"><i>🛠</i><b>Smart fix</b><small>restart what is not running, then rebuild if needed</small></a>` : ""}
        ${sw ? `<a class="wtool" onclick="apRun(${id}, 'switch')"><i>🔀</i><b>Switch the tunnel type</b><small>test the other types on this route today and keep the best that works</small></a>` : ""}
        ${mv ? `<a class="wtool" onclick="apMove(${id})"><i>🚚</i><b>Move to another Iran server</b><small>when the route to this foreign server is cut from that Iran server</small></a>` : ""}
        <a class="wtool" onclick="closeModal();apTune(${id})"><i>🧭</i><b>Network advisor</b><small>rank every tunnel type on today's network</small></a></div></div>`}
      <div id="ap-story" class="fix hidden" style="margin-top:10px"></div>`, "wide");
  } catch (e) { showModal("<b>🩺 Diagnosis</b>", `<div class="err">${esc(e.message)}</div>`, "wide"); }
};
window.apTune = id => { ap.tid = String(id); localStorage.setItem("nk.ap.tid", ap.tid); refresh(); };
window.apRun = async (id, level) => {
  if (!await gconfirm(level === "switch" ? "Test the other tunnel types and replace this tunnel with the best one that works? Users reconnect after about a minute. If the new one fails, the old type is built again." : "Repair this tunnel now? Restarts first, then a rebuild when restarting is not enough.", {danger: true})) return;
  try { const r = await api("/api/autopilot/smart-fix", {method: "POST", body: JSON.stringify({tunnel_id: id, level})}); const box = $("#ap-story"); box.classList.remove("hidden"); box.innerHTML = '<div class="fx-line run"><i></i>Starting…</div>'; followStory(r.id, box, 700); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.apMove = async id => {
  const box = $("#ap-story"); box.classList.remove("hidden"); box.innerHTML = '<div class="fx-line run"><i></i>Ranking the Iran servers against this foreign server…</div>';
  try {
    const r = await api(`/api/autopilot/ranking/${id}`);
    box.innerHTML = `<div class="ibox"><h5><i>🚚</i>Iran servers that could carry it</h5>${r.ranking.map(x => irow(`<b>${esc(x.name)}</b> <small class="mut">${esc(x.reason || "")}</small>`, x.score > 0 ? `<b class="${x.score >= 70 ? "gw-ok" : ""}">${x.score}</b> <button class="sm pri" onclick="apMoveTo(${id}, ${x.id}, '${esc(x.name)}')">Move here</button>` : '<span class="err">no</span>')).join("") || '<div class="mut">No other Iran server is online.</div>'}</div>`;
  } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
};
window.apMoveTo = async (id, iranId, name) => {
  if (!await gconfirm(`Move this tunnel to ${name}? It is removed from its Iran server and built again on ${name} (same name, same ports, same type); users reconnect after about a minute. If it fails, the old one is built again.`, {danger: true})) return;
  try { const r = await api("/api/autopilot/move", {method: "POST", body: JSON.stringify({tunnel_id: id, iran_id: iranId})}); const box = $("#ap-story"); box.innerHTML = '<div class="fx-line run"><i></i>Starting…</div>'; followStory(r.id, box, 700); } catch (e) { toast("✕ " + e.message, "bad"); }
};window.checkTunnel = async id => { toast("Checking both servers…"); try { await api(`/api/tunnels/${id}/check`, {method: "POST"}); toast("✓ Checked", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };

/* ---- configs (what the probe keeps alive and tests) */
INFO.configs = ["Panels", "Every client config the probe keeps alive and tests from inside Iran: reachable, ping, daily speed. Add links or import a subscription; edit a link field by field."];
const cf = {open: null, edit: null, sub: null, q: "", group: localStorage.getItem("nk.cfgroup") || "__all"};
const FLAGS = {de: "de", germany: "de", fi: "fi", finland: "fi", nl: "nl", netherlands: "nl", us: "us", usa: "us", unitedstates: "us", fr: "fr", france: "fr", gb: "gb", uk: "gb", unitedkingdom: "gb", england: "gb", tr: "tr", turkey: "tr", ae: "ae", uae: "ae", emirates: "ae", dubai: "ae", se: "se", sweden: "se", pl: "pl", poland: "pl", ru: "ru", russia: "ru", ir: "ir", iran: "ir", ca: "ca", canada: "ca", jp: "jp", japan: "jp", sg: "sg", singapore: "sg", hk: "hk", hongkong: "hk", it: "it", italy: "it", es: "es", spain: "es", at: "at", austria: "at", ch: "ch", switzerland: "ch", kz: "kz", kazakhstan: "kz", am: "am", armenia: "am", az: "az", ge: "ge", georgia: "ge", qa: "qa", qatar: "qa", bh: "bh", bahrain: "bh", om: "om", oman: "om", lt: "lt", lv: "lv", ee: "ee", cz: "cz", czech: "cz", czechia: "cz", ro: "ro", romania: "ro", bg: "bg", bulgaria: "bg", gr: "gr", greece: "gr", pt: "pt", portugal: "pt", ie: "ie", ireland: "ie", no: "no", norway: "no", dk: "dk", denmark: "dk", be: "be", belgium: "be", lu: "lu", ua: "ua", ukraine: "ua", md: "md", rs: "rs", serbia: "rs", in: "in", india: "in", kr: "kr", korea: "kr", au: "au", australia: "au", br: "br", brazil: "br", mx: "mx", za: "za", cn: "cn", china: "cn", tw: "tw", taiwan: "tw", vn: "vn", vietnam: "vn", th: "th", thailand: "th", my: "my", malaysia: "my", id: "id", indonesia: "id", ph: "ph", eg: "eg", egypt: "eg", sa: "sa", saudi: "sa", kw: "kw", kuwait: "kw", iq: "iq", iraq: "iq", pk: "pk", pakistan: "pk", il: "il", cy: "cy", cyprus: "cy", mt: "mt", malta: "mt", hu: "hu", hungary: "hu", sk: "sk", si: "si", hr: "hr", croatia: "hr", fin: "fi", ger: "de", ned: "nl", fra: "fr", tur: "tr", ita: "it", swe: "se", pol: "pl", can: "ca", aus: "au", usa2: "us"};
function cfFlag(name) { for (const t of String(name || "").toLowerCase().split(/[^a-z]+/)) if (FLAGS[t]) return flagImg(FLAGS[t]) + " "; return ""; }
const pingCls = v => v == null ? "mut" : v < 150 ? "ok" : v < 400 ? "warn" : "bad";
const mb = v => v == null ? "—" : Number(v).toFixed(v >= 100 ? 0 : 1);
PAGES.configsList = async () => {
  const [d, subs, tl] = await Promise.all([api("/api/configs"), api("/api/subscriptions"), api("/api/tunnels").catch(() => [])]);
  $("#n-configs").textContent = d.configs.length;
  const names = [...new Set([...subs.subscriptions.map(s => s.name), ...d.configs.map(c => c.group).filter(Boolean)])].sort((a, b) => a.localeCompare(b));
  if (cf.group !== "__all" && !names.includes(cf.group)) cf.group = "__all";
  const searched = d.configs.filter(c => !cf.q || (c.name + (c.group || "")).toLowerCase().includes(cf.q));
  const list = cf.group === "__all" ? searched : cf.group === "__manual" ? searched.filter(c => !c.group) : searched.filter(c => c.group === cf.group);
  const groups = [null];   // one flat list in every view; in "All" the group badge says where a config belongs
  const tunnelSel = c => `<select class="cf-tun" title="Tunnel this config goes through" onclick="event.stopPropagation()" onchange="event.stopPropagation();cfSetTunnel(${c.id}, this.value)"><option value="">no tunnel</option>${tl.map(t => `<option value="${esc(t.name)}" ${t.name === c.tunnel ? "selected" : ""}>${esc(t.name)}</option>`).join("")}${c.tunnel && !tl.some(t => t.name === c.tunnel) ? `<option selected>${esc(c.tunnel)}</option>` : ""}</select>`;
  const cdnIn = c => `<input class="cf-cdn" inputmode="numeric" maxlength="5" placeholder="CDN port" title="CDN port: type it and press Enter" value="${esc(c.cdn_port || "")}" data-orig="${esc(c.cdn_port || "")}" onclick="event.stopPropagation()" onkeydown="if(event.key==='Enter')this.blur()" onchange="cfSetCdn(${c.id}, this)">`;
  const live = c => `<span class="live ${c.status === "up" ? "up" : c.status === "down" ? "down" : "unk"}">● ${c.status === "up" ? "ONLINE" : c.status === "down" ? "OFFLINE" : "UNKNOWN"}</span>`;
  const shown = c => { const g = [c.group, ...names].filter(Boolean).find(x => c.name.startsWith(x + " ")); return g ? c.name.slice(g.length + 1) : c.name; };   // the group has its own column: never repeat it in the name
  const title = c => `${cfFlag(c.name)}${esc(shown(c))}${c.enabled ? "" : '<span class="pill">disabled</span>'}`;
  const inAll = cf.group === "__all";
  const gcell = c => inAll ? `<td class="cf-sub">${c.group ? esc(c.group) : "—"}</td>` : "";
  const acts = c => `<button class="sm" onclick="event.stopPropagation();cfEdit(${c.id})">Edit</button><button class="sm" onclick="event.stopPropagation();cfSpeed(${c.id})" title="Speed test now">Test</button><button class="sm danger" onclick="event.stopPropagation();cfDel(${c.id}, '${esc(c.name)}')">Delete</button>`;
  const MBs = v => v == null ? "—" : mb(v / 8);   // the table speaks MB/s like a v2ray client; the stat boxes elsewhere stay in Mb/s
  const row = c => { const m = c.meta || {}; return `<tr class="${c.status === "up" ? "up" : c.status === "down" ? "down" : ""} click" onclick="cfToggle(${c.id})">
      <td><span class="td-type">${esc((m.proto || "—").toUpperCase())}</span></td>
      <td class="name"><div class="cf-nm"><i class="sdot ${c.status}" title="${esc(c.status)}"></i>${title(c)}</div></td>
      ${gcell(c)}
      <td class="mono" title="${esc(m.address || "")}">${esc(m.address || "—")}</td>
      <td class="num">${m.port || "—"}</td>
      <td>${esc(m.network || "—")}</td>
      <td>${m.security && m.security !== "none" ? `<b class="gw-ok">${esc(m.security)}</b>` : '<span class="mut">none</span>'}</td>
      <td class="num"><b class="${pingCls(c.status === "up" ? c.ping_ms : null)}">${c.ping_ms != null && c.status === "up" ? Math.round(c.ping_ms) : "—"}</b> <a class="mut cf-rf" title="Refresh ping" onclick="event.stopPropagation();cfSync()">↻</a></td>
      <td class="num">${c.down_mbps != null ? "↓" + MBs(c.down_mbps) + " ↑" + MBs(c.up_mbps) : "—"}</td>
      <td>${cdnIn(c)}</td>
      <td class="cf-acts">${acts(c)}</td></tr>`; };  const rowC = c => `<tr class="${c.status === "up" ? "up" : c.status === "down" ? "down" : ""} click" onclick="cfToggle(${c.id})">
      <td class="name">${title(c)}</td>${gcell(c)}<td>${live(c)}</td>
      <td class="num"><b class="${pingCls(c.status === "up" ? c.ping_ms : null)}">${c.ping_ms != null && c.status === "up" ? Math.round(c.ping_ms) + " ms" : "—"}</b></td>
      <td class="num hide-sm">${c.down_mbps != null ? "↓" + mb(c.down_mbps) + " ↑" + mb(c.up_mbps) : "—"}</td>
      <td class="mut hide-sm">${c.cdn_port ? ":" + esc(c.cdn_port) : "—"}</td>
      <td class="mut" title="${esc(c.last_error)}">${c.last_error && c.status === "down" ? esc(c.last_error).slice(0, 40) : (c.checked_at ? ago(c.checked_at) + " ago" : "—")}</td>
      <td class="cf-acts">${acts(c)}</td></tr>`;
  const up = d.configs.filter(c => c.status === "up"), down = d.configs.filter(c => c.status === "down"), pings = up.filter(c => c.ping_ms != null).map(c => c.ping_ms);
  cf.view = cf.view || localStorage.getItem("nk.cfview") || "list";
  const card = c => `<div class="cf-card ${c.status}" onclick="cfToggle(${c.id})"><div class="cf-top"><b>${title(c)}</b>${live(c)}</div>${inAll && c.group ? `<small class="cf-sub">${esc(c.group)}</small>` : ""}
      <div class="cf-stats"><div class="cf-stat"><b class="${pingCls(c.status === "up" ? c.ping_ms : null)}">${c.ping_ms != null && c.status === "up" ? Math.round(c.ping_ms) : "—"}<small>ms</small></b><span>PING</span></div><div class="cf-stat"><b>${mb(c.down_mbps)}</b><span>DOWN Mbps</span></div><div class="cf-stat"><b>${mb(c.up_mbps)}</b><span>UP Mbps</span></div></div>
      <div class="cf-meta">${cdnIn(c)}</div><div class="cf-acts">${acts(c)}<span class="sp"></span><small class="mut">${c.checked_at ? ago(c.checked_at) + " ago" : "—"}</small></div></div>`;
  const sub = names.includes(cf.group) ? subs.subscriptions.find(s => s.name === cf.group) : null;
  const chip = (label, n, key, removable) => `<span class="gchip ${cf.group === key ? "on" : ""}"><button onclick="cfGroup('${esc(key)}')">${esc(label)}<small>${n}</small></button>${removable ? `<a title="Remove group" onclick="cfGroupDel('${esc(key)}')">×</a>` : ""}</span>`;
  const gbar = names.length ? `<div class="gbar">${chip("All", searched.length, "__all", false)}<i class="gsep"></i>${names.map(n => chip(n, searched.filter(c => c.group === n).length, n, true)).join("")}
      ${names.includes(cf.group) ? `<span class="sp"></span><button class="sm" onclick="cfSubRefresh(${sub ? sub.id : 0}, '${esc(cf.group)}')" title="Import new nodes of this subscription">⟳ Sync</button><button class="sm" onclick="cfSubManage('${esc(cf.group)}')" title="Tick which nodes of this subscription are kept on the probe">☑ Subscription</button>` : ""}</div>` : "";
  $("#page").innerHTML = `<div class="tiles">
      <div class="tile"><small>Total configs</small><b>${d.configs.length}</b><span>${names.length} groups · probe: ${esc(d.probe.active || "none")}</span></div>
      <div class="tile ok"><small>Online</small><b>${up.length}</b><span>answer from inside Iran</span></div>
      <div class="tile ${down.length ? "bad" : ""}"><small>Offline</small><b>${down.length}</b><span>${down.length ? down.slice(0, 3).map(c => esc(c.name)).join(", ") + (down.length > 3 ? "…" : "") : "none"}</span></div>
      <div class="tile"><small>Average live ping</small><b>${pings.length ? Math.round(pings.reduce((a, b) => a + b, 0) / pings.length) + " ms" : "—"}</b><span>over ${pings.length} online configs</span></div></div>
    <div class="toolbar"><input id="cq" placeholder="Search…" value="${esc(cf.q)}" oninput="cfQ(this.value)">
      <button class="${cf.edit === "new" ? "on" : ""}" onclick="cfNew()">＋ Add</button><button onclick="cfSync()" title="Ask the probe for the live state of every config">↻ Ping all</button><button onclick="cfSpeedAll()" title="Speed test of every config in this view, one after the other" ${list.length ? "" : "disabled"}>⚡ Test all</button><button class="danger" onclick="cfDeleteAll()" ${list.length ? "" : "disabled"}>🗑 Delete all</button><button onclick="go('settings');setTab('alerts')">🔔 Alerts</button>
      <span class="sp"></span><div class="tabs" title="Display">${[["grid", "▦ Grid"], ["compact", "▤ Compact"], ["list", "☰ List"]].map(([k, l]) => `<button class="${cf.view === k ? "on" : ""}" onclick="cfView('${k}')">${l}</button>`).join("")}</div></div>
    ${gbar}
    ${cf.sub && cf.edit !== "new" ? cfSubHtml() : ""}
    ${list.length ? groups.map(() => `${cf.view === "grid" ? `<div class="cf-grid">${list.map(card).join("")}</div>`
      : cf.view === "compact" ? `<table class="tbl lift compact"><colgroup>${inAll ? '<col style="width:22%"><col style="width:14%">' : '<col style="width:30%">'}<col style="width:9%"><col style="width:8%"><col style="width:12%"><col style="width:9%"><col><col style="width:150px"></colgroup>
      <thead><tr><th>Config</th>${inAll ? "<th>Subs group</th>" : ""}<th>Status</th><th>Ping</th><th>Speed</th><th>CDN port</th><th>Note</th><th></th></tr></thead><tbody>${list.map(rowC).join("")}</tbody></table>`
      : `<table class="tbl lift cf-list srv-tbl">${inAll ? '<colgroup><col style="width:5%"><col style="width:17%"><col style="width:13%"><col style="width:18%"><col style="width:5%"><col style="width:6%"><col style="width:6%"><col style="width:6%"><col style="width:10%"><col style="width:6%"><col style="width:150px"></colgroup>' : '<colgroup><col style="width:6%"><col style="width:22%"><col style="width:22%"><col style="width:6%"><col style="width:8%"><col style="width:7%"><col style="width:7%"><col style="width:12%"><col style="width:7%"><col style="width:150px"></colgroup>'}
      <thead><tr><th>Type</th><th>Remarks</th>${inAll ? "<th>Subs group</th>" : ""}<th>Address</th><th>Port</th><th>Transport</th><th>TLS</th><th>Delay (ms)</th><th>Speed (MB/s)</th><th>CDN port</th><th style="text-align:right">Actions</th></tr></thead>
      <tbody>${list.map(row).join("")}</tbody></table>`}`).join("") : `<div class="empty">${d.configs.length ? "No configs in this view" : "No config yet. Add a link or import a subscription."}</div>`}
    ${subs.subscriptions.length && cf.group === "__all" ? `<div class="card" style="margin-top:14px"><h3>Subscriptions</h3>${subs.subscriptions.map(s => `<div class="ev info"><time>${s.last_import ? ago(s.last_import) + " ago" : "—"}</time><i></i><small>${s.nodes.length} nodes</small><span><b>${esc(s.name)}</b> <span class="mut">${esc(s.url_host)}</span> <a class="mut" onclick="cfSubRefresh(${s.id}, '${esc(s.name)}')">import new nodes</a> · <a class="mut" onclick="cfSubDel(${s.id})">forget</a></span></div>`).join("")}</div>` : ""}`;
  const q = $("#cq"); if (q && cf.q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
  cf.names = names; cf.cache = d.configs;
  const openC = d.configs.find(c => c.id === cf.open);
  if (cf.add) cfAddModal();
  else if (openC) showModal(`<span class="st ${openC.status}"></span><b>${esc(shown(openC))}</b> <span class="pill">${esc(openC.group || "manual")}</span>${cf.edit === openC.id ? '<span class="pill">editor</span>' : ""}`, `<div id="cf-detail-${openC.id}">${cf.edit === openC.id ? '<div class="mut">loading the link…</div>' : cfDetailHtml(openC)}</div>`, "wide"); else hideModal();
  if (cf.open && cf.edit === cf.open) cfLoadEditor(cf.open);
  else if (cf.open) cfLoadChart(cf.open);
};
function cfDetailHtml(c) {
  const m = c.meta || {}, tab = state.wtab || "info";
  const tabs = `<div class="tabs wtabs">${[["info", "Info"], ["charts", "Monitoring"], ["actions", "Actions"]].map(([k, l]) => `<button class="${tab === k ? "on" : ""}" onclick="wtab('${k}')">${l}</button>`).join("")}</div>`;
  const tool = (icon, title, sub, click, cls = "") => `<a class="wtool ${cls}" onclick="${click}"><i>${icon}</i><b>${title}</b><small>${sub}</small></a>`;
  if (tab === "charts") return tabs + `<div id="cf-charts-${c.id}"><div class="mut">loading…</div></div>`;
  if (tab === "actions") return tabs + `<div class="wgroup"><h4>Test</h4><div class="wtools">${tool("⚡", "Speed test now", "download and upload through this config from inside Iran (1-3 min)", `cfSpeed(${c.id})`)}${tool("↻", "Ping now", "ask the probe for the live state of every config", "cfSync()")}${tool("🔗", "Copy link", "the vless:// vmess:// trojan:// ss:// link", `cfCopy(${c.id})`)}</div></div>
    <div class="wgroup"><h4>Change</h4><div class="wtools">${tool("✎", "Edit", "change the link field by field, rename, group", `cfEdit(${c.id})`)}</div></div>
    <div class="wgroup"><h4>Danger</h4><div class="wtools">${tool("🗑", "Delete from the probe", "its history goes too", `cfDel(${c.id}, '${esc(c.name)}')`, "danger")}</div></div>`;
  const sp = c.down_mbps != null ? `↓ ${mb(c.down_mbps / 8)} ↑ ${mb(c.up_mbps / 8)} MB/s <small class="mut">(${mb(c.down_mbps)} / ${mb(c.up_mbps)} Mb/s)</small>` : "not measured";
  return tabs + `<div class="iboxes">${[
    ibox("🧩", "Config", [irow("Type", esc((m.proto || "—").toUpperCase())), irow("Address", `<span class="mono">${esc(m.address || "—")}</span>`), irow("Port", m.port || "—"), irow("Transport", esc(m.network || "—")), irow("TLS", m.security && m.security !== "none" ? `<b class="gw-ok">${esc(m.security)}</b>` : "none")]),
    ibox("🕒", "Status", [irow("State", `<span class="st ${c.status}">${esc(c.status)}</span>`), irow("Since", c.status_since ? ago(c.status_since) + " ago" : "—"), irow("Delay", c.ping_ms != null && c.status === "up" ? `<b class="${pingCls(c.ping_ms)}">${Math.round(c.ping_ms)} ms</b>` : "—"), irow("Last check", c.checked_at ? ago(c.checked_at) + " ago" : "—"), c.last_error ? irow("Error", `<span class="err">${esc(c.last_error.slice(0, 60))}</span>`) : ""]),
    ibox("📡", "Subscription & routing", [irow("Subs group", c.group ? esc(c.group) : "—"), irow("CDN port", esc(c.cdn_port || "—")), irow("Probe port", `${c.port || "—"} <small class="mut">slug ${esc(c.slug)}</small>`)]),
    ibox("⚡", "Speed", [irow("Last test", sp), irow("Measured", esc((c.speed_at || "").slice(0, 16).replace("T", " ") || "—"))]),
  ].join("")}</div>`;
}
async function cfLoadChart(id) {
  const box = document.getElementById("cf-charts-" + id); if (!box) return;
  const d = await api(`/api/configs/${id}/samples?hours=${state.mrange}`);
  const rows = d.samples, ups = rows.map(r => r[1]), pings = nums(rows.map(r => r[2]).filter((v, i) => ups[i])), upn = ups.filter(Boolean).length;
  const pct = ups.length ? upn * 100 / ups.length : null, f1 = v => v == null ? "—" : Math.round(v * 10) / 10;
  const out = []; let cur = null;
  rows.forEach(r => { if (!r[1]) { if (!cur) cur = {from: r[0], to: r[0]}; cur.to = r[0]; } else if (cur) { cur.to = r[0]; out.push(cur); cur = null; } });
  if (cur) { cur.open = true; cur.to = Date.now() / 1000; out.push(cur); }
  CH_reset();
  box.className = "mon";
  box.innerHTML = `<div class="mhead">${rangeTabs()}<small class="mut">${rows.length} checks · hover a chart for exact values</small></div>
    <div class="mtiles">${mtile("Uptime", pct == null ? "—" : f1(pct) + "%", `${upn} of ${rows.length} checks up`, pct != null && pct < 90 ? "bad" : pct != null && pct < 99 ? "warn" : "ok")}${mtile("Delay", pings.length ? Math.round(avgOf(pings)) + " ms" : "—", pings.length ? "best " + Math.round(Math.min(...pings)) + " · worst " + Math.round(Math.max(...pings)) + " ms" : "")}${mtile("Outages", out.length, out.length ? "down " + durText(out.reduce((a, o) => a + (o.to - o.from), 0)) + " in total" : "none in this range", out.length ? "warn" : "ok")}</div>
    <div class="mcard mwide"><h6>Up / down timeline</h6>${strip(ups)}</div>
    <div class="mgrid">${chartCard("Delay", rows.map(r => [r[0], r[1] ? r[2] : null]), {unit: " ms", warn: 400, bad: 800, fmt: v => Math.round(v)})}
      <div class="mcard"><h6>Outages <span class="mval">${out.length}</span></h6>${out.length ? out.slice(-8).reverse().map(o => `<div class="irow"><span>${fmtT(o.from, 1)}</span><div class="err">${durText(o.to - o.from)}${o.open ? " (still down)" : ""}</div></div>`).join("") : '<div class="mut chart-empty">no outage in this range</div>'}</div></div>`;
}window.cfQ = v => { cf.q = v.trim().toLowerCase(); clearTimeout(qT); qT = setTimeout(refresh, 150); };
window.cfView = v => { cf.view = v; localStorage.setItem("nk.cfview", v); refresh(); };
window.cfGroup = g => { cf.group = g; localStorage.setItem("nk.cfgroup", g); refresh(); };
async function cfInView() { const d = await api("/api/configs"); return d.configs.filter(c => cf.group === "__all" ? true : cf.group === "__manual" ? !c.group : c.group === cf.group); }
window.cfSpeedAll = async () => { const ids = (await cfInView()).filter(c => c.enabled).map(c => c.id); if (!ids.length) return; if (!await gconfirm(`Speed test ${ids.length} configs one after the other? About a minute each — the page keeps working meanwhile.`)) return; let done = 0, bad = 0; for (const id of ids) { try { await api(`/api/configs/${id}/speedtest`, {method: "POST"}); done++; } catch (e) { bad++; } toast(`Speed tests: ${done} done, ${bad} failed, ${ids.length - done - bad} to go`); } toast(`✓ Speed tests finished: ${done} ok, ${bad} failed`, bad ? "bad" : "ok"); refresh(); };
window.cfDeleteAll = async () => { const mine = await cfInView(); if (!mine.length) return; const where = cf.group === "__all" ? "ALL" : `all ${cf.group === "__manual" ? "manual" : cf.group}`; if (await gprompt(`Delete ${where} ${mine.length} configs from the probe? Type DELETE to confirm:`) !== "DELETE") return; let n = 0; for (const c of mine) { try { await api(`/api/configs/${c.id}`, {method: "DELETE"}); n++; } catch (e) { /* keep going */ } } toast(`✓ ${n} configs deleted`, "ok"); refresh(); };
window.cfGroupDel = async g => { const [d, subs] = await Promise.all([api("/api/configs"), api("/api/subscriptions")]); const mine = d.configs.filter(c => (c.group || "") === g); const s = subs.subscriptions.find(x => x.name === g); const a = mine.length ? await gprompt(`Remove the group "${g}" (${mine.length} configs)?\nType DELETE to delete its configs from the probe too, or KEEP to only forget the group and keep the configs as manual ones:`) : "DELETE"; if (a !== "DELETE" && a !== "KEEP") return; for (const c of mine) { try { if (a === "DELETE") await api(`/api/configs/${c.id}`, {method: "DELETE"}); else await api(`/api/configs/${c.id}`, {method: "PUT", body: JSON.stringify({group: ""})}); } catch (e) { /* keep going */ } } if (s) { try { await api(`/api/subscriptions/${s.id}`, {method: "DELETE"}); } catch (e) { /* fine */ } } if (cf.group === g) cfGroup("__all"); toast("✓ Group removed", "ok"); refresh(); };
window.cfSetTunnel = async (id, v) => { try { await api(`/api/configs/${id}`, {method: "PUT", body: JSON.stringify({tunnel: v})}); toast(v ? "✓ Tunnel set to " + v : "✓ Tunnel cleared", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } refresh(); };
window.cfSetCdn = async (id, inp) => { const v = inp.value.trim(); if (v === (inp.dataset.orig || "")) return; const n = Number(v); if (v && (!/^\d+$/.test(v) || n < 1 || n > 65535)) { toast("CDN port must be a number from 1 to 65535", "bad"); inp.value = inp.dataset.orig || ""; return; } try { await api(`/api/configs/${id}`, {method: "PUT", body: JSON.stringify({cdn_port: v})}); inp.dataset.orig = v; toast(v ? "✓ CDN port " + v + " saved" : "✓ CDN port cleared", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); inp.value = inp.dataset.orig || ""; } };
window.cfSubOpen = async name => { const subs = await api("/api/subscriptions"); cf.sub = {name, url: "", nodes: null, saved: subs.subscriptions.some(s => s.name === name)}; cf.edit = null; refresh(); };
window.cfToggle = id => { cf.open = cf.open === id ? null : id; cf.edit = null; state.wtab = null; if (!cf.open) hideModal(); refresh(); };
/* Add: first a choice (one link / a subscription), then a glass window with that form */
window.cfNew = () => { cf.add = "choose"; cf.edit = null; cf.open = null; cf.sub = null; cfAddModal(); };
window.cfAddPick = k => { cf.add = k; cf.sub = k === "sub" ? {name: "", url: "", nodes: null} : null; cfAddModal(); };
function cfAddModal() {
  const k = cf.add;
  if (k === "choose") return showModal("<b>＋ Add configs</b>", `<div class="addpick"><a onclick="cfAddPick('link')"><i>🔗</i><b>A single config</b><small>paste one vless:// vmess:// trojan:// or ss:// link</small></a><a onclick="cfAddPick('sub')"><i>📡</i><b>A subscription</b><small>import every node of a subscription link as a group; pick which ones to keep</small></a></div>`);
  if (k === "link") return showModal("<b>🔗 Add a config</b>", `<form class="frm" onsubmit="return cfAdd(event)"><label style="grid-column:1/-1">Link<textarea name="uri" class="ob-raw" required placeholder="vless://…  vmess://…  trojan://…  ss://…"></textarea></label><label>Name <small>(empty = the link's own name)</small><input name="name"></label><label>Group<input name="group" placeholder="optional" value="${esc((cf.names || []).includes(cf.group) ? cf.group : "")}"></label><div class="frm-act"><button class="pri" type="submit">Add to the probe</button><button type="button" onclick="cfNew()">← Back</button></div></form>`);
  if (k === "sub") return showModal("<b>📡 Import a subscription</b>", cfSubHtml(true));
}
window.cfCancel = () => { cf.edit = null; cf.sub = null; cf.add = null; refresh(); };
window.cfSync = async () => { toast("Asking the probe… (up to a minute)"); try { await api("/api/configs/sync", {method: "POST"}); toast("✓ Synced", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.cfAdd = async e => { e.preventDefault(); const f = new FormData(e.target); try { const r = await api("/api/configs", {method: "POST", body: JSON.stringify({uri: f.get("uri").trim(), name: f.get("name").trim(), group: f.get("group").trim()})}); toast("✓ " + r.name + " added — the probe tests it within a minute", "ok"); cf.edit = null; cf.add = null; refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.cfDel = async (id, name) => { if (!await gconfirm(`Delete ${name} from the probe? Its history goes too.`)) return; try { await api(`/api/configs/${id}`, {method: "DELETE"}); toast("✓ Deleted", "ok"); cf.open = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.cfSpeed = async id => { toast("Measuring… (1–3 minutes)"); try { await api(`/api/configs/${id}/speedtest`, {method: "POST"}); toast("✓ Measured", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.cfCopy = async id => { try { const r = await api(`/api/configs/${id}/uri`); await navigator.clipboard.writeText(r.uri); toast("✓ Link copied", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.cfEdit = id => { cf.open = id; cf.edit = id; refresh(); };

/* v2rayN-style editor: link <-> fields */
const cfeNets = ["tcp", "ws", "grpc", "httpupgrade", "xhttp", "h2", "kcp", "quic"];
function b64d(s) { s = String(s || "").replace(/-/g, "+").replace(/_/g, "/").replace(/\s+/g, ""); while (s.length % 4) s += "="; try { return decodeURIComponent(escape(atob(s))); } catch (e) { return atob(s); } }
function b64e(s) { return btoa(unescape(encodeURIComponent(s))); }
function cfeParse(uri) {
  uri = String(uri || "").trim(); const proto = (uri.match(/^([a-z0-9]+):\/\//i) || [])[1]; if (!proto) throw new Error("not a config link");
  const p = proto.toLowerCase(), f = {proto: p, alias: "", address: "", port: "", id: "", flow: "", enc: "none", net: "tcp", htype: "", host: "", path: "", service: "", mode: "", seed: "", sec: "none", sni: "", fp: "", alpn: "", insecure: "", pbk: "", sid: "", spx: "", aid: "0", method: "", extra: {}};
  if (p === "vmess") { const j = JSON.parse(b64d(uri.slice(8))); Object.assign(f, {alias: j.ps || "", address: j.add || "", port: String(j.port || ""), id: j.id || "", aid: String(j.aid || 0), enc: j.scy || "auto", net: j.net || "tcp", htype: j.type || "", host: j.host || "", path: j.path || "", sec: j.tls === "tls" ? "tls" : "none", sni: j.sni || "", fp: j.fp || "", alpn: j.alpn || ""}); if (f.net === "grpc") { f.service = f.path; f.path = ""; } return f; }
  if (p === "ss") { let body = uri.slice(5), tag = ""; const h = body.indexOf("#"); if (h >= 0) { tag = body.slice(h + 1); body = body.slice(0, h); } const q = body.indexOf("?"); if (q >= 0) body = body.slice(0, q); if (body.indexOf("@") < 0) body = b64d(body); const at = body.lastIndexOf("@"); let cred = body.slice(0, at); const server = body.slice(at + 1); if (cred.indexOf(":") < 0) cred = b64d(cred); const ci = cred.indexOf(":"); f.method = cred.slice(0, ci); f.id = decodeURIComponent(cred.slice(ci + 1)); const m = server.match(/^\[?(.*?)\]?:(\d+)$/); f.address = m ? m[1] : server; f.port = m ? m[2] : ""; f.alias = decodeURIComponent(tag); return f; }
  const u = new URL(uri), g = k => u.searchParams.get(k) || "";
  Object.assign(f, {alias: decodeURIComponent(u.hash.replace(/^#/, "")), address: u.hostname.replace(/^\[|\]$/g, ""), port: u.port, id: decodeURIComponent(u.username), flow: g("flow"), enc: g("encryption") || "none", net: g("type") || "tcp", htype: g("headerType"), host: g("host"), path: g("path"), service: g("serviceName"), mode: g("mode"), seed: g("seed"), sec: g("security") || (p === "trojan" ? "tls" : "none"), sni: g("sni") || g("peer"), fp: g("fp"), alpn: g("alpn"), insecure: g("allowInsecure") || g("insecure"), pbk: g("pbk"), sid: g("sid"), spx: g("spx")});
  const known = ["encryption", "flow", "type", "headerType", "host", "path", "serviceName", "mode", "seed", "security", "sni", "fp", "alpn", "allowInsecure", "insecure", "pbk", "sid", "spx", "peer"];
  u.searchParams.forEach((v, k) => { if (known.indexOf(k) < 0) f.extra[k] = v; });
  return f;
}
function cfeBuild(f) {
  const name = encodeURIComponent(f.alias || ""), host = f.address.indexOf(":") >= 0 && f.address[0] !== "[" ? "[" + f.address + "]" : f.address;
  if (f.proto === "vmess") return "vmess://" + b64e(JSON.stringify({v: "2", ps: f.alias, add: f.address, port: String(f.port), id: f.id, aid: String(f.aid || 0), scy: f.enc || "auto", net: f.net, type: f.htype || "none", host: f.host, path: f.net === "grpc" ? f.service : f.path, tls: f.sec === "tls" ? "tls" : "", sni: f.sni, alpn: f.alpn, fp: f.fp}));
  if (f.proto === "ss") return "ss://" + b64e(f.method + ":" + f.id) + "@" + host + ":" + f.port + "#" + name;
  const q = new URLSearchParams(), put = (k, v) => { if (v !== undefined && v !== null && String(v) !== "") q.set(k, v); };
  if (f.proto === "vless") put("encryption", f.enc || "none");
  put("security", f.sec); put("type", f.net); put("headerType", f.htype); put("flow", f.flow); put("host", f.host); put("path", f.path); put("serviceName", f.service); put("mode", f.mode); put("seed", f.seed); put("sni", f.sni); put("fp", f.fp); put("alpn", f.alpn); put("allowInsecure", f.insecure); put("pbk", f.pbk); put("sid", f.sid); put("spx", f.spx);
  Object.keys(f.extra || {}).forEach(k => put(k, f.extra[k]));
  return f.proto + "://" + encodeURIComponent(f.id) + "@" + host + ":" + f.port + "?" + q.toString() + "#" + name;
}
function cfeFields(f) {
  const rows = [], add = (sec, key, label, kind, opts) => rows.push({sec, key, label, kind: kind || "text", opts: opts || []});
  add("Server", "alias", "Alias (remarks)"); add("Server", "address", "Address"); add("Server", "port", "Port");
  if (f.proto === "vless") { add("Server", "id", "UUID (id)"); add("Server", "flow", "Flow", "select", ["", "xtls-rprx-vision", "xtls-rprx-vision-udp443"]); add("Server", "enc", "Encryption"); }
  else if (f.proto === "vmess") { add("Server", "id", "UUID (id)"); add("Server", "aid", "Alter ID"); add("Server", "enc", "Security", "select", ["auto", "aes-128-gcm", "chacha20-poly1305", "none", "zero"]); }
  else if (f.proto === "trojan") { add("Server", "id", "Password"); add("Server", "flow", "Flow", "select", ["", "xtls-rprx-vision"]); }
  else if (f.proto === "ss") { add("Server", "id", "Password"); add("Server", "method", "Method", "select", ["aes-128-gcm", "aes-256-gcm", "chacha20-ietf-poly1305", "2022-blake3-aes-128-gcm", "2022-blake3-aes-256-gcm", "none"]); return rows; }
  const net = f.net; add("Transport", "net", "Transport protocol (network)", "select", cfeNets.indexOf(net) < 0 ? cfeNets.concat([net]) : cfeNets);
  if (net === "tcp" || net === "kcp" || net === "quic") add("Transport", "htype", "Header type", "select", net === "tcp" ? ["", "none", "http"] : ["", "none", "srtp", "utp", "wechat-video", "dtls", "wireguard"]);
  if (["ws", "httpupgrade", "xhttp", "h2"].indexOf(net) >= 0 || (net === "tcp" && f.htype === "http")) { add("Transport", "host", "Host"); add("Transport", "path", "Path"); }
  if (net === "xhttp") add("Transport", "mode", "Mode", "select", ["", "auto", "packet-up", "stream-up", "stream-one"]);
  if (net === "grpc") { add("Transport", "service", "Service name"); add("Transport", "mode", "Mode", "select", ["", "gun", "multi"]); }
  if (net === "kcp") add("Transport", "seed", "Seed");
  add("TLS", "sec", "Security", "select", ["none", "tls", "reality"]);
  if (f.sec === "tls" || f.sec === "reality") { add("TLS", "sni", "SNI"); add("TLS", "fp", "Fingerprint", "select", ["", "chrome", "firefox", "safari", "ios", "android", "edge", "random", "randomized"]); }
  if (f.sec === "tls") { add("TLS", "alpn", "ALPN"); add("TLS", "insecure", "Allow insecure", "select", ["", "0", "1"]); }
  if (f.sec === "reality") { add("TLS", "pbk", "Public key"); add("TLS", "sid", "Short ID"); add("TLS", "spx", "SpiderX"); }
  return rows;
}
function cfeWarn(f) {
  const w = [];
  if (!f.address) w.push("Address is empty"); if (!/^\d+$/.test(String(f.port)) || +f.port < 1 || +f.port > 65535) w.push("Port must be 1–65535"); if (!f.id) w.push(f.proto === "ss" || f.proto === "trojan" ? "Password is empty" : "UUID is empty");
  if (f.proto !== "ss") { if (f.sec === "tls" && !f.sni && !f.host) w.push("TLS without SNI"); if (f.sec === "reality" && (!f.pbk || !f.sni)) w.push("Reality needs a public key and an SNI"); if (f.net === "ws" && !f.path) w.push("WebSocket without a path"); if (f.net === "grpc" && !f.service) w.push("gRPC without a service name"); }
  return w;
}
let cfe = null;
async function cfLoadEditor(id) {
  const td = document.getElementById("cf-detail-" + id); if (!td) return;
  try {
    const r = await api(`/api/configs/${id}/uri`);
    cfe = {id, f: cfeParse(r.uri), origLink: cfeBuild(cfeParse(r.uri))};
    cfeRender(td);
  } catch (e) { td.innerHTML = `<div class="err">${esc(e.message)}</div><div class="frm-act"><button class="sm" onclick="cfToggle(${id})">Close</button></div>`; }
}
function cfeRender(td) {
  const f = cfe.f, rows = cfeFields(f), secs = [...new Set(rows.map(r => r.sec))], w = cfeWarn(f), link = cfeBuild(f);
  td.innerHTML = `<div class="cfe"><div class="cfe-cols"><div>${secs.map(s => `<div class="ob-sec"><h4>${s}</h4>${rows.filter(r => r.sec === s).map(r => { const v = f[r.key] == null ? "" : f[r.key]; return `<label class="cfe-row"><span>${r.label}</span>${r.kind === "select" ? `<select data-k="${r.key}">${(r.opts.indexOf(v) < 0 ? r.opts.concat([v]) : r.opts).map(o => `<option value="${esc(o)}" ${o === v ? "selected" : ""}>${esc(o || "—")}</option>`).join("")}</select>` : `<input data-k="${r.key}" value="${esc(v)}" spellcheck="false">`}</label>`; }).join("")}</div>`).join("")}</div>
    <div><div class="ob-sec"><h4>Check</h4>${w.length ? w.map(x => `<div class="err">⚠ ${esc(x)}</div>`).join("") : '<div class="gw-ok">✓ Looks valid</div>'}</div>
      <div class="ob-sec"><h4>Link <small class="mut">(paste a whole new link here)</small></h4><textarea class="ob-raw" id="cfe-link" spellcheck="false">${esc(link)}</textarea></div></div></div>
    <div class="frm-act"><button class="pri" onclick="cfeSave()">Save to the probe</button><span class="mut">${link === cfe.origLink ? "no change yet" : "changed"}</span></div></div>`;
  td.querySelectorAll("[data-k]").forEach(el => { el.onchange = el.oninput = () => { cfe.f[el.dataset.k] = el.value.trim(); if (el.tagName === "SELECT" && ["net", "sec", "htype"].indexOf(el.dataset.k) >= 0) cfeRender(td); else { const t = td.querySelector("#cfe-link"); if (t) t.value = cfeBuild(cfe.f); } }; });
  td.querySelector("#cfe-link").oninput = e => { try { cfe.f = cfeParse(e.target.value); const v = e.target.value; cfeRender(td); td.querySelector("#cfe-link").value = v; } catch (x) { /* keep typing */ } };
}
window.cfeSave = async () => {
  const link = cfeBuild(cfe.f), hard = cfeWarn(cfe.f).filter(x => /empty|1–65535/.test(x));
  if (hard.length) { toast("✕ " + hard[0], "bad"); return; }
  toast("Saving to the probe…");
  try { await api(`/api/configs/${cfe.id}`, {method: "PUT", body: JSON.stringify({uri: link === cfe.origLink ? "" : link, new_name: cfe.f.alias})}); toast("✓ Saved — the probe restarts this config", "ok"); cf.edit = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};

/* subscriptions */
window.cfSubNew = () => cfAddPick("sub");
function cfSubHtml(inline = false) {
  if (!cf.sub) cf.sub = {name: "", url: "", nodes: null};
  const s = cf.sub;
  const body = `<form class="frm" onsubmit="return cfSubPreview(event)">
    <label>Group name<input name="name" required value="${esc(s.name)}" ${s.saved ? "readonly" : ""} placeholder="hamed11"></label><label style="grid-column:2/-1">Subscription link${s.saved ? " <small>(empty = the link saved for this group)</small>" : ""}<input name="url" ${s.saved ? "" : "required"} value="${esc(s.url)}" placeholder="${s.saved ? "Saved on the server — leave empty to reuse" : "https://…/sub/…"}"></label>
    <div class="frm-act"><button class="pri" type="submit">Preview nodes</button>${inline ? '<button type="button" onclick="cfNew()">← Back</button>' : '<button type="button" onclick="cfCancel()">Cancel</button>'}</div></form>
    ${s.nodes ? `<div class="ob-inbounds">${s.nodes.map(n => `<label><input type="checkbox" value="${esc(n.name)}" ${n.exists ? "" : "checked"} ${n.exists ? "disabled" : ""}> ${esc(n.name)} <small class="mut">${esc(n.proto)}${n.exists ? " · already there" : ""}</small></label>`).join("")}</div>
      <div class="frm-act"><button class="pri" onclick="cfSubImport()">Add the ticked nodes to the probe</button></div>` : ""}`;
  return inline ? body : `<div class="card"><h3>Import a subscription</h3>${body}</div>`;
}
/* the Subscription window of a group: tick which nodes stay on the probe; unticked ones are removed, newly ticked ones added */
window.cfSubManage = async group => {
  toast("Reading the saved subscription…");
  try {
    const [p, d] = await Promise.all([api("/api/subscriptions/preview", {method: "POST", body: JSON.stringify({name: group, url: ""})}), api("/api/configs")]);
    const mine = d.configs.filter(c => c.group === group);
    cf.subEdit = {group, nodes: p.nodes.map(n => ({...n, cfg: mine.find(c => c.name === n.name || c.name === group + " " + n.name) || null}))};
    cfSubRender();
  } catch (e) { toast("✕ " + e.message, "bad"); }
};
function cfSubRender() {
  const s = cf.subEdit; if (!s) return;
  showModal(`<b>☑ ${esc(s.group)}</b> <span class="pill">${s.nodes.filter(n => n.cfg).length} of ${s.nodes.length} on the probe</span>`,
    `<div class="toolbar"><a class="mut" onclick="document.querySelectorAll('#sub-nodes input').forEach(x=>x.checked=true)">tick all</a> · <a class="mut" onclick="document.querySelectorAll('#sub-nodes input').forEach(x=>x.checked=false)">untick all</a><span class="sp"></span><small class="mut">ticked = kept on the probe and shown in the list</small></div>
     <div class="ob-inbounds sub-nodes" id="sub-nodes" style="max-height:50vh">${s.nodes.map((n, i) => `<label><input type="checkbox" data-i="${i}" ${n.cfg ? "checked" : ""}> ${cfFlag(n.name)}${esc(n.name)} <small class="mut">${esc(n.proto)}${n.cfg ? ` · ${n.cfg.status}${n.cfg.ping_ms != null && n.cfg.status === "up" ? " " + Math.round(n.cfg.ping_ms) + " ms" : ""}` : " · not on the probe"}</small></label>`).join("")}</div>
     <div class="frm-act"><button class="pri" onclick="cfSubApply()">Apply</button></div>`, "wide");
}
window.cfSubApply = async () => {
  const s = cf.subEdit; if (!s) return;
  const ticked = [...document.querySelectorAll("#sub-nodes input")].map(x => x.checked);
  const drop = s.nodes.filter((n, i) => n.cfg && !ticked[i]), add = s.nodes.filter((n, i) => !n.cfg && ticked[i]);
  if (!drop.length && !add.length) { toast("Nothing changed"); return; }
  if (drop.length && !await gconfirm(`Remove ${drop.length} configs of ${s.group} from the probe?`)) return;
  toast(`Applying: ${add.length} to add, ${drop.length} to remove…`);
  let bad = 0;
  for (const n of drop) { try { await api(`/api/configs/${n.cfg.id}`, {method: "DELETE"}); } catch (e) { bad++; } }
  if (add.length) { try { const r = await api("/api/subscriptions/import", {method: "POST", body: JSON.stringify({name: s.group, url: "", picks: add.map(n => n.name)})}); bad += (r.failed || []).length; } catch (e) { bad += add.length; } }
  toast(bad ? `⚠ Done with ${bad} failures` : "✓ Subscription applied", bad ? "bad" : "ok");
  cf.subEdit = null; closeModal();
};
window.cfSubPreview = async e => { e.preventDefault(); const f = new FormData(e.target); cf.sub.name = f.get("name").trim(); cf.sub.url = f.get("url").trim(); toast("Downloading…"); try { cf.sub.nodes = (await api("/api/subscriptions/preview", {method: "POST", body: JSON.stringify({name: cf.sub.name, url: cf.sub.url})})).nodes; toast("✓ " + cf.sub.nodes.length + " nodes", "ok"); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.cfSubImport = async () => { const picks = [...document.querySelectorAll(".ob-inbounds input:checked")].map(x => x.value); if (!picks.length) { toast("Tick at least one node", "bad"); return; } toast("Adding " + picks.length + " nodes… (a few seconds each)"); try { const r = await api("/api/subscriptions/import", {method: "POST", body: JSON.stringify({name: cf.sub.name, url: cf.sub.url, picks})}); toast(`✓ ${r.added.length} added, ${r.skipped.length} skipped, ${r.failed.length} failed`, r.failed.length ? "bad" : "ok"); cf.sub = null; cf.add = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.cfSubRefresh = async (id, name) => { cf.sub = {name, url: "", nodes: null, saved: !!id}; cf.edit = null; if (!id) { refresh(); return; } toast("Fetching the saved subscription…"); try { cf.sub.nodes = (await api("/api/subscriptions/preview", {method: "POST", body: JSON.stringify({name, url: ""})})).nodes; const fresh = cf.sub.nodes.filter(n => !n.exists).length; toast(fresh ? `✓ ${fresh} new nodes — tick and add them` : "✓ Nothing new in this subscription", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } refresh(); };
window.cfSubDel = async id => { if (!await gconfirm("Forget this subscription record? The configs stay.")) return; await api(`/api/subscriptions/${id}`, {method: "DELETE"}); refresh(); };

/* ---- panels (Marzban / PasarGuard): health, nodes, users, hosts, outbounds */
const pn = {id: localStorage.getItem("nk.pn") || "", tab: "users", add: false, edit: false, users: null, uq: "", ustatus: "", uoff: 0, user: null, hosts: null, hostEdit: null, core: null, raw: false, logs: null};
INFO.panels = ["Panels", "Your Marzban and PasarGuard panels: are they up, are their nodes connected, their users, the hosts in the subscription links, and the Xray outbounds of the core — every core change is backed up first."];
const GB = 1024 ** 3, gb = v => v == null ? "∞" : v === 0 ? "∞" : (v / GB).toFixed(v / GB >= 100 ? 0 : 1) + " GB";
PAGES.panels = async () => {
  const list = (await api("/api/panels")).panels;
  const sel = list.find(p => String(p.id) === pn.id) || list[0];
  if (sel && String(sel.id) !== pn.id) { pn.id = String(sel.id); pn.users = pn.hosts = pn.core = pn.user = pn.logs = null; }
  const chip = p => p.status === "up" ? `<span class="pill ok">● up · ${p.latency_ms} ms</span>` : p.status === "down" ? `<span class="pill err">● down</span>` : '<span class="pill">not checked yet</span>';
  const addForm = `<div class="card"><h3>Add a panel</h3><form class="frm" onsubmit="return pnAdd(event)">
      <label>Name<input name="name" required placeholder="shop"></label><label>Type<select name="type"><option value="marzban">Marzban</option><option value="pasarguard">PasarGuard</option></select></label><label>Address<input name="url" required placeholder="https://panel.example:8000"></label>
      <label>Admin user<input name="username" required autocomplete="off"></label><label>Password<input name="password" type="password" required autocomplete="new-password"></label><label class="chk"><input name="verify_tls" type="checkbox" checked> verify the TLS certificate</label>
      <div class="frm-act"><button class="pri" type="submit">Log in and add</button><button type="button" onclick="pn.add=false;refresh()">Cancel</button></div></form></div>`;
  if (!list.length) { $("#page").innerHTML = pn.add ? addForm : '<div class="empty">No panel yet. <button onclick="pn.add=true;refresh()">+ Add a panel</button></div>'; return; }
  const p = sel, s = p.stats || {};
  const editForm = `<div class="card"><h3>Edit ${esc(p.name)}</h3><form class="frm" onsubmit="return pnEdit(event, ${p.id})">
      <label>Name<input name="name" value="${esc(p.name)}"></label><label>Address<input name="url" value="${esc(p.url)}"></label><label>Admin user<input name="username" value="${esc(p.username)}" autocomplete="off"></label>
      <label>New password <small>(empty keeps the saved one)</small><input name="password" type="password" autocomplete="new-password"></label><label class="chk"><input name="verify_tls" type="checkbox" ${p.verify_tls ? "checked" : ""}> verify the TLS certificate</label><label class="chk"><input name="enabled" type="checkbox" ${p.enabled ? "checked" : ""}> enabled (checked every 2 min)</label>
      <div class="frm-act"><button class="pri" type="submit">Save</button><button type="button" onclick="pn.edit=false;refresh()">Cancel</button><span class="sp"></span><button type="button" class="danger" onclick="pnDel(${p.id}, '${esc(p.name)}')">Remove from the list</button></div></form></div>`;
  $("#page").innerHTML = `<div class="toolbar"><select onchange="pnPick(this.value)">${list.map(x => `<option value="${x.id}" ${x.id === p.id ? "selected" : ""}>${esc(x.name)} — ${x.type}</option>`).join("")}</select>${chip(p)}
      <span class="mut">${esc(p.version || "")}${p.checked_at ? " · checked " + ago(p.checked_at) + " ago" : ""}</span><span class="sp"></span>
      <button onclick="pnCheck(${p.id})">↻ Check now</button><button onclick="pnTestConfig(${p.id}, '${esc(p.name)}')" title="A small test user on this panel whose subscription is kept and measured on the probe as the group ${esc(p.name)}">🧪 Test config on probe${s.test_user ? " ✓" : ""}</button><button onclick="pn.edit=!pn.edit;pn.add=false;refresh()">Edit</button><button onclick="pn.add=!pn.add;pn.edit=false;refresh()">+ Add panel</button></div>
    ${p.last_error && p.status === "down" ? `<div class="gw-out bad">✕ ${esc(p.last_error)}</div>` : ""}
    ${pn.add ? addForm : ""}${pn.edit ? editForm : ""}
    <div class="tiles">
      <div class="tile"><small>Users</small><b>${s.users_total ?? "—"}</b><span>${s.users_active ?? "—"} active · ${s.users_online ?? "—"} online</span></div>
      <div class="tile"><small>Nodes</small><b>${(p.nodes || []).filter(n => n.fails === 0 || ["connected", "healthy"].includes(n.status)).length} / ${(p.nodes || []).length}</b><span>connected</span></div>
      <div class="tile"><small>Load</small><b>${s.cpu != null ? Math.round(s.cpu) + "%" : "—"}</b><span>cpu · ram ${s.mem_pct ?? "—"}%</span></div>
      <div class="tile"><small>Traffic</small><b>${s.bw_in != null ? fmtB(s.bw_in + (s.bw_out || 0)) : "—"}</b><span>↓${s.bw_in != null ? fmtB(s.bw_in) : "—"} ↑${s.bw_out != null ? fmtB(s.bw_out) : "—"}</span></div></div>
    <div class="tabs" style="margin-bottom:10px">${["users", "hosts"].map(t => `<button class="${pn.tab === t ? "on" : ""}" onclick="pnTab('${t}')">${t[0].toUpperCase() + t.slice(1)}</button>`).join("")}</div>
    <div id="pn-body"><div class="mut">loading…</div></div>`;
  if (pn.tab === "users") pnUsersLoad(p.id);
  if (pn.tab === "hosts") pnHostsLoad(p.id);
};
function fmtB(b) { if (b == null) return "—"; const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; } return b.toFixed(i >= 3 ? 2 : 0) + " " + u[i]; }
function pnNodesHtml(p) {
  const rows = (p.nodes || []).map(n => { const ok = ["connected", "healthy", "disabled"].includes(n.status); return `<tr class="${ok ? (n.status === "disabled" ? "" : "up") : "down"}"><td class="name">${esc(n.name)}</td><td><span class="st ${ok ? (n.status === "disabled" ? "" : "up") : "down"}">${esc(n.status || "?")}</span></td><td class="mut">${esc(n.address)}</td><td class="mut">${esc(n.xray || "")}</td><td class="mut" title="${esc(n.message)}">${esc(n.message).slice(0, 60)}${n.fails ? ` <span class="pill err">${n.fails} checks</span>` : ""}</td>
      <td><button class="sm" onclick="pnNodeLogs(${p.id}, ${n.id}, '${esc(n.name)}')">Log</button> <button class="sm" onclick="pnNodeReconnect(${p.id}, ${n.id})">Reconnect</button></td></tr>`; }).join("");
  return `<table class="tbl"><colgroup><col style="width:18%"><col style="width:12%"><col style="width:18%"><col style="width:10%"><col><col style="width:150px"></colgroup><thead><tr><th>Node</th><th>Status</th><th>Address</th><th>Xray</th><th>Message</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="6" class="mut">no nodes</td></tr>'}</tbody></table>
    ${pn.logs ? `<div class="card"><h3>Log of ${esc(pn.logs.name)} <span class="sp"></span><button class="sm" onclick="pn.logs=null;refresh()">Close</button></h3><pre class="gl-out">${esc(pn.logs.text || "(empty)")}</pre></div>` : ""}`;
}
window.pnPick = v => { pn.id = v; localStorage.setItem("nk.pn", v); pn.users = pn.hosts = pn.core = pn.user = pn.logs = null; pn.edit = pn.add = false; refresh(); };
window.pnTab = t => { pn.tab = t; pn.user = null; pn.hostEdit = null; refresh(); };
window.pnCheck = async id => { toast("Asking the panel…"); try { const r = await api(`/api/panels/${id}/check`, {method: "POST"}); toast(r.ok ? `✓ answers in ${r.latency_ms} ms · ${r.nodes.length} nodes` : "✕ " + r.error, r.ok ? "ok" : "bad"); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.pnAdd = async e => { e.preventDefault(); const f = new FormData(e.target); toast("Logging in…"); try { const r = await api("/api/panels", {method: "POST", body: JSON.stringify({name: f.get("name").trim(), type: f.get("type"), url: f.get("url").trim(), username: f.get("username").trim(), password: f.get("password"), verify_tls: !!f.get("verify_tls")})}); toast("✓ " + r.panel.name + " added — making its test config on the probe…", "ok"); pn.add = false; pn.id = String(r.panel.id); refresh(); pnTestConfig(r.panel.id, r.panel.name, true); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.pnTestConfig = async (id, name, quiet) => { if (!quiet) toast("Creating the test user and importing its subscription… (up to a minute)"); try { const r = await api(`/api/panels/${id}/test-config`, {method: "POST"}); toast(`✓ ${name}: ${r.added.length} configs added to the probe${r.skipped.length ? ", " + r.skipped.length + " already there" : ""} (user ${r.username})`, "ok"); refresh(); } catch (e) { toast("✕ test config of " + name + ": " + e.message, "bad"); } };
window.pnEdit = async (e, id) => { e.preventDefault(); const f = new FormData(e.target); const body = {name: f.get("name").trim(), url: f.get("url").trim(), username: f.get("username").trim(), verify_tls: !!f.get("verify_tls"), enabled: !!f.get("enabled")}; if (f.get("password")) body.password = f.get("password"); try { await api(`/api/panels/${id}`, {method: "PATCH", body: JSON.stringify(body)}); toast("✓ Saved", "ok"); pn.edit = false; refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.pnDel = async (id, name) => { if (!await gconfirm(`Remove ${name} from this list? Nothing on the panel itself changes.`)) return; await api(`/api/panels/${id}`, {method: "DELETE"}); pn.id = ""; pn.edit = false; refresh(); };
window.pnNodeReconnect = async (id, nid) => { try { await api(`/api/panels/${id}/nodes/${nid}/reconnect`, {method: "POST"}); toast("✓ Reconnect requested", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.pnNodeLogs = async (id, nid, name) => { try { pn.logs = {name, text: (await api(`/api/panels/${id}/nodes/${nid}/logs`)).log}; } catch (e) { pn.logs = {name, text: "✕ " + e.message}; } refresh(); };

/* users */
async function pnUsersLoad(id) {
  const box = $("#pn-body"); if (!box) return;
  try { pn.users = await api(`/api/panels/${id}/users?search=${encodeURIComponent(pn.uq)}&status=${pn.ustatus}&offset=${pn.uoff}&limit=40&sort=-created_at`); } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  const u = pn.users, now = Date.now() / 1000;
  const exp = x => !x.expire_date ? "∞" : (typeof x.expire === "number" ? (x.expire < now ? '<span class="err">expired</span>' : Math.ceil((x.expire - now) / 86400) + " d") : esc(x.expire_date));
  box.innerHTML = `<div class="toolbar"><input id="pn-uq" placeholder="Search user…" value="${esc(pn.uq)}" oninput="pnUQ(this.value)"><select onchange="pn.ustatus=this.value;pn.uoff=0;refresh()">${["", "active", "disabled", "limited", "expired", "on_hold"].map(s => `<option value="${s}" ${pn.ustatus === s ? "selected" : ""}>${s || "all statuses"}</option>`).join("")}</select><span class="mut">${u.total} users</span><span class="sp"></span>
      <button class="sm" onclick="pnBulk(${id}, 'disable')">Disable ticked</button><button class="sm" onclick="pnBulk(${id}, 'enable')">Enable ticked</button><button class="sm" onclick="pnBulkRenew(${id})">Renew ticked…</button>
      <button class="sm" ${pn.uoff ? "" : "disabled"} onclick="pn.uoff=Math.max(0,pn.uoff-40);refresh()">‹</button><button class="sm" ${pn.uoff + 40 < u.total ? "" : "disabled"} onclick="pn.uoff+=40;refresh()">›</button></div>
    <table class="tbl"><colgroup><col style="width:30px"><col style="width:22%"><col style="width:10%"><col style="width:16%"><col style="width:10%"><col><col style="width:170px"></colgroup>
      <thead><tr><th></th><th>User</th><th>Status</th><th>Traffic</th><th>Expires</th><th>Note</th><th></th></tr></thead>
      <tbody>${u.users.map(x => `<tr class="${x.status === "active" ? "up" : x.status === "disabled" ? "" : "down"}"><td><input type="checkbox" class="pn-uchk" value="${esc(x.username)}"></td><td class="name">${esc(x.username)}</td><td><span class="st ${x.status === "active" ? "up" : x.status === "disabled" ? "" : "down"}">${esc(x.status)}</span></td>
        <td class="num">${fmtB(x.used || 0)} / ${gb(x.limit)}</td><td class="num">${exp(x)}</td><td class="mut">${esc(x.note)}</td>
        <td><button class="sm" onclick="pnUserOpen(${id}, '${esc(x.username)}')">Edit</button> <button class="sm" onclick="pnUAct(${id}, '${esc(x.username)}', '${x.status === "active" ? "disable" : "enable"}')">${x.status === "active" ? "Disable" : "Enable"}</button> <button class="sm" onclick="pnUAct(${id}, '${esc(x.username)}', 'reset')">Reset</button></td></tr>
        ${pn.user && pn.user.username === x.username ? `<tr class="detail"><td colspan="7">${pnUserHtml(id, pn.user)}</td></tr>` : ""}`).join("") || '<tr><td colspan="7" class="mut">nobody matches</td></tr>'}</tbody></table>`;
  const q = $("#pn-uq"); if (q && pn.uq) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
}
function pnUserHtml(id, u) {
  return `<form class="frm" onsubmit="return pnUserSave(event, ${id}, '${esc(u.username)}')">
    <label>Status<select name="status">${["active", "disabled", "on_hold"].map(s => `<option ${u.status === s ? "selected" : ""}>${s}</option>`).join("")}</select></label>
    <label>Expires <small>(empty = never)</small><input name="expire_date" type="date" value="${esc(u.expire_date)}"></label><label>Traffic limit GB <small>(0 = unlimited)</small><input name="limit_gb" type="number" step="0.1" min="0" value="${u.limit ? (u.limit / GB).toFixed(1) : 0}"></label>
    <label>Reset<select name="reset">${["no_reset", "day", "week", "month", "year"].map(s => `<option ${u.reset === s ? "selected" : ""}>${s}</option>`).join("")}</select></label><label style="grid-column:2/-1">Note<input name="note" value="${esc(u.note)}"></label>
    <div class="kv" style="grid-column:1/-1"><div><span>used</span> ${fmtB(u.used || 0)} · <span>lifetime</span> ${fmtB(u.lifetime || 0)}</div><div><span>created</span> ${esc(String(u.created_at || "").slice(0, 10))} · <span>online</span> ${esc(String(u.online_at || "never").slice(0, 16))}</div>${u.sub ? `<div><span>subscription</span> <a class="mut" onclick="navigator.clipboard.writeText('${esc(u.sub)}');toast('✓ copied','ok')">copy link</a></div>` : ""}</div>
    <div class="frm-act"><button class="pri" type="submit">Save</button><button type="button" onclick="pn.user=null;refresh()">Close</button><span class="sp"></span>
      <button type="button" class="sm" onclick="(async()=>pnUAct(${id}, '${esc(u.username)}', 'renew', await gprompt('Add how many days?', '30')))()">+ days</button><button type="button" class="sm" onclick="pnUAct(${id}, '${esc(u.username)}', 'revoke')">Revoke sub</button><button type="button" class="sm danger" onclick="(async()=>{if(await gconfirm('Delete ${esc(u.username)} from the panel?'))pnUAct(${id}, '${esc(u.username)}', 'delete')})()">Delete</button></div></form>`;
}
window.pnUQ = v => { pn.uq = v.trim(); pn.uoff = 0; clearTimeout(qT); qT = setTimeout(refresh, 300); };
window.pnUserOpen = async (id, name) => { try { pn.user = (await api(`/api/panels/${id}/users/${encodeURIComponent(name)}`)).user; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.pnUserSave = async (e, id, name) => { e.preventDefault(); const f = new FormData(e.target); try { await api(`/api/panels/${id}/users/${encodeURIComponent(name)}`, {method: "PUT", body: JSON.stringify({status: f.get("status"), expire_date: f.get("expire_date"), limit_gb: +f.get("limit_gb"), reset: f.get("reset"), note: f.get("note")})}); toast("✓ Saved", "ok"); pn.user = null; refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.pnUAct = async (id, name, action, value) => { if (action === "renew" && !value) return; try { await api(`/api/panels/${id}/users/${encodeURIComponent(name)}/action`, {method: "POST", body: JSON.stringify({action, value})}); toast("✓ " + action + " done", "ok"); if (action === "delete") pn.user = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.pnBulk = async (id, action, value) => { const names = [...document.querySelectorAll(".pn-uchk:checked")].map(x => x.value); if (!names.length) { toast("Tick some users first", "bad"); return; } try { const r = await api(`/api/panels/${id}/users-bulk`, {method: "POST", body: JSON.stringify({action, value, usernames: names})}); toast(`✓ ${r.done} done${r.failed_count ? ", " + r.failed_count + " failed" : ""}`, r.failed_count ? "bad" : "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.pnBulkRenew = async id => { const d = await gprompt("Add how many days to the ticked users?", "30"); if (d) pnBulk(id, "renew", +d); };

/* hosts */
async function pnHostsLoad(id) {
  const box = $("#pn-body"); if (!box) return;
  try { pn.hosts = (await api(`/api/panels/${id}/hosts`)).hosts; } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  box.innerHTML = `<div class="toolbar"><span class="mut">${pn.hosts.length} hosts — the address:port that goes into every subscription link</span><span class="sp"></span><button class="sm" onclick="pnHostsToggle(${id}, true)">Disable ticked</button><button class="sm" onclick="pnHostsToggle(${id}, false)">Enable ticked</button></div>
    <table class="tbl"><colgroup><col style="width:30px"><col style="width:22%"><col style="width:14%"><col><col style="width:8%"><col style="width:10%"><col style="width:150px"></colgroup>
      <thead><tr><th></th><th>Remark</th><th>Inbound</th><th>Address</th><th>Port</th><th>State</th><th></th></tr></thead>
      <tbody>${pn.hosts.map(h => `<tr class="${h.disabled ? "" : "up"}"><td><input type="checkbox" class="pn-hchk" value="${esc(h.key)}"></td><td class="name">${esc(h.remark)}</td><td class="mut">${esc(h.tag)}</td><td class="mut">${esc(h.address)}</td><td class="num">${h.port ?? "—"}</td><td><span class="st ${h.disabled ? "" : "up"}">${h.disabled ? "disabled" : "on"}</span></td>
        <td><button class="sm" onclick="pnHostOpen(${id}, '${esc(h.key)}')">Edit</button> <button class="sm" onclick="pnHostClone(${id}, '${esc(h.key)}')">Clone</button> <button class="sm danger" onclick="pnHostDel(${id}, '${esc(h.key)}', '${esc(h.remark)}')">✕</button></td></tr>
        ${pn.hostEdit && pn.hostEdit.key === h.key ? `<tr class="detail"><td colspan="7">${pnHostHtml(id, pn.hostEdit)}</td></tr>` : ""}`).join("") || '<tr><td colspan="7" class="mut">no hosts</td></tr>'}</tbody></table>`;
}
function pnHostHtml(id, h) {
  const txt = ["remark", "address", "port", "sni", "host", "path", "alpn", "fingerprint"], bools = ["allowinsecure", "mux_enable", "random_user_agent", "use_sni_as_host", "is_disabled"];
  return `<form class="frm" onsubmit="return pnHostSave(event, ${id}, '${esc(h.key)}')">${txt.map(k => `<label>${k}${k === "port" ? " <small>(empty = inbound port)</small>" : ""}<input name="${k}" value="${esc(h[k] == null ? "" : h[k])}"></label>`).join("")}
    <label>security<select name="security">${["inbound_default", "none", "tls"].map(s => `<option ${h.security === s ? "selected" : ""}>${s}</option>`).join("")}</select></label>
    ${bools.map(k => `<label class="chk"><input type="checkbox" name="${k}" ${h[k] ? "checked" : ""}> ${k}</label>`).join("")}
    <div class="frm-act"><button class="pri" type="submit">Save host</button><button type="button" onclick="pn.hostEdit=null;refresh()">Close</button></div></form>`;
}
window.pnHostOpen = async (id, key) => { try { pn.hostEdit = (await api(`/api/panels/${id}/hosts/${encodeURIComponent(key)}`)).host; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.pnHostSave = async (e, id, key) => { e.preventDefault(); const f = new FormData(e.target), body = {}; ["remark", "address", "port", "sni", "host", "path", "alpn", "fingerprint", "security"].forEach(k => body[k] = f.get(k)); ["allowinsecure", "mux_enable", "random_user_agent", "use_sni_as_host", "is_disabled"].forEach(k => body[k] = !!f.get(k)); try { await api(`/api/panels/${id}/hosts/${encodeURIComponent(key)}`, {method: "PUT", body: JSON.stringify(body)}); toast("✓ Host saved", "ok"); pn.hostEdit = null; refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.pnHostsToggle = async (id, disable) => { const keys = [...document.querySelectorAll(".pn-hchk:checked")].map(x => x.value); if (!keys.length) { toast("Tick some hosts first", "bad"); return; } try { const r = await api(`/api/panels/${id}/hosts-toggle`, {method: "POST", body: JSON.stringify({keys, disable})}); toast(`✓ ${r.changed} hosts ${disable ? "disabled" : "enabled"}`, "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.pnHostClone = async (id, key) => { try { await api(`/api/panels/${id}/hosts/${encodeURIComponent(key)}/clone`, {method: "POST"}); toast("✓ Cloned", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.pnHostDel = async (id, key, name) => { if (!await gconfirm(`Delete host ${name} from the panel? Users lose that address in their links.`)) return; try { await api(`/api/panels/${id}/hosts/${encodeURIComponent(key)}`, {method: "DELETE"}); toast("✓ Deleted", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };

/* outbounds (core config) */
async function pnCoreLoad(id) {
  const box = $("#pn-body"); if (!box) return;
  try { pn.core = await api(`/api/panels/${id}/core`); } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  const c = pn.core, s = c.summary;
  box.innerHTML = `<div class="gw-out">Every change is validated, the previous config is saved below, written, read back — and put back automatically when the panel did not keep it. The <b>default</b> outbound (first in the list) carries all traffic that no rule sends elsewhere.</div>
    <div class="card"><h3>Outbounds <span class="pill">${esc(c.kind)} · ${esc(c.name || "core")}</span><span class="sp"></span><button class="sm" onclick="pn.raw=!pn.raw;refresh()">${pn.raw ? "Hide" : "Raw JSON"}</button></h3>
      ${s.outbounds.map(o => `<div class="gw-row"><span><b>${esc(o.tag)}</b> ${o.tag === s.default ? '<span class="pill ok">default</span>' : ""} <small class="mut">${esc(o.protocol)}${o.target ? " → " + esc(o.target) : ""}${o.network ? " · " + esc(o.network) : ""}</small></span><span></span>
        <span class="ob-act">${o.tag !== s.default ? `<button class="sm" onclick="pnObDefault(${id}, '${esc(o.tag)}')">Make default</button>` : ""}${["freedom", "blackhole"].includes(o.protocol) ? "" : `<button class="sm danger" onclick="pnObDel(${id}, '${esc(o.tag)}')">Remove</button>`}</span></div>`).join("")}
      <textarea id="pn-ob-uri" class="ob-raw" placeholder="Add an outbound from a link: vless://…  vmess://…  trojan://…  ss://…" spellcheck="false"></textarea>
      <div class="frm-act"><input id="pn-ob-tag" placeholder="tag, e.g. fi-node" style="width:180px"><label class="chk"><input id="pn-ob-def" type="checkbox"> make it the default</label><button class="pri" onclick="pnObAdd(${id})">Add outbound</button></div></div>
    <div class="card"><h3>Routing rules</h3>${s.rules.map(r => `<div class="gw-row"><span><b>→ ${esc(r.to)}</b></span><span class="mut">${esc(r.when)}</span><span></span></div>`).join("") || '<div class="mut">no rules: everything goes to the default outbound</div>'}${s.balancers.length ? `<div class="mut">balancers: ${esc(s.balancers.join(", "))}</div>` : ""}</div>
    ${pn.raw ? `<div class="card"><h3>Raw core config <small class="mut">— for experts; saved only when valid</small></h3><textarea id="pn-core-raw" class="ob-raw" style="min-height:320px;font-family:var(--mono)" spellcheck="false">${esc(JSON.stringify(c.config, null, 2))}</textarea><div class="frm-act"><button class="pri" onclick="pnCoreRaw(${id})">Validate &amp; save</button></div></div>` : ""}
    <div class="card"><h3>Backups <small class="mut">— the config as it was before each change</small></h3>${c.backups.map(b => `<div class="gw-row"><span><b>${new Date(b.at * 1000).toLocaleString()}</b> <small class="mut">${esc(b.note)}</small></span><span class="mut">${esc(b.outbounds.join(", "))}</span><span class="ob-act"><button class="sm" onclick="pnCoreRestore(${id}, '${esc(b.file)}')">Restore</button></span></div>`).join("") || '<div class="mut">no backup yet</div>'}</div>`;
}
const pnCoreDo = async (id, fn) => { toast("Writing to the panel… (it restarts its core)"); try { const r = await fn(); toast("✓ Applied · outbounds: " + r.outbounds.join(", "), "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.pnObAdd = id => { const uri = $("#pn-ob-uri").value.trim(), tag = $("#pn-ob-tag").value.trim(), def = $("#pn-ob-def").checked; if (!uri || !tag) { toast("A link and a tag are needed", "bad"); return; } pnCoreDo(id, () => api(`/api/panels/${id}/core/outbounds`, {method: "POST", body: JSON.stringify({uri, tag, make_default: def})})); };
window.pnObDel = async (id, tag) => { if (!await gconfirm(`Remove outbound ${tag}? Rules that use it are removed too.`)) return; pnCoreDo(id, () => api(`/api/panels/${id}/core/outbounds/${encodeURIComponent(tag)}`, {method: "DELETE"})); };
window.pnObDefault = (id, tag) => pnCoreDo(id, () => api(`/api/panels/${id}/core/outbounds/${encodeURIComponent(tag)}/default`, {method: "POST"}));
window.pnCoreRestore = async (id, file) => { if (!await gconfirm("Put this backup in place of the current core config?")) return; pnCoreDo(id, () => api(`/api/panels/${id}/core/restore/${file}`, {method: "POST"})); };
window.pnCoreRaw = id => { let cfg; try { cfg = JSON.parse($("#pn-core-raw").value); } catch (e) { toast("✕ not valid JSON: " + e.message, "bad"); return; } pnCoreDo(id, () => api(`/api/panels/${id}/core`, {method: "PUT", body: JSON.stringify({config: cfg, note: "manual edit"})})); };

/* ---- shop bot (PHP bot on its own server, over SSH) */
const sb = {tab: "status", info: null, tg: null, stats: null, uq: "", users: null, table: "users", tpage: 0, tq: "", trows: null, sql: "", qres: null};
INFO.bot = ["Shop bot", "Our own dedicated sales bot: is it alive, today's sales, the switches, its users and tables — and the one-time import of the real database."];
PAGES.bot = async () => {
  const pg = $("#page");
  try { sb.info = await api("/api/shopbot"); } catch (e) { pg.innerHTML = `<div class="empty">${esc(e.message)}${/Settings/.test(e.message) ? ' — <a onclick="go(\'settings\')">open Settings</a>' : ""}</div>`; return; }
  const d = sb.info, c = d.counts || {}, s = d.sales || {}, u = d.units || {};
  const php = Object.keys(u).find(k => /php/.test(k)), dbu = Object.keys(u).find(k => /mariadb|mysql/.test(k)), web = Object.keys(u).find(k => /caddy|nginx/.test(k));
  const unit = k => k ? `<span class="pill ${u[k] === "active" ? "ok" : "err"}">${esc(k)} ${esc(u[k])}</span>` : '<span class="pill err">missing</span>';
  const money = v => v == null ? "—" : (v / 1000).toLocaleString() + " k";
  pg.innerHTML = `<div class="toolbar">${d.installed ? '<span class="pill ok">● installed</span>' : '<span class="pill err">● not installed</span>'}${unit(php)}${unit(dbu)}${unit(web)}<span class="pill ${d.cron ? "ok" : "err"}">cron ${d.cron ? d.cron + " jobs" : "off"}</span><span class="sp"></span>
      <button class="sm" onclick="sbAct('webhook')">Set webhook</button><button class="sm" onclick="sbAct('restart')">Restart PHP</button><button class="sm" onclick="sbAct('backup')">Dump DB</button><button class="sm" onclick="sbAct('clearlogs')">Clear logs</button></div>
    <div class="tiles">
      <div class="tile"><small>Users</small><b>${c.users ?? "—"}</b><span>+${s.new_users_today ?? "—"} today · +${s.new_users_week ?? "—"} this week</span></div>
      <div class="tile ok"><small>Today</small><b>${money((s.today || {}).income)}</b><span>${(s.today || {}).orders ?? "—"} orders</span></div>
      <div class="tile"><small>7 days</small><b>${money((s.week || {}).income)}</b><span>${(s.week || {}).orders ?? "—"} orders</span></div>
      <div class="tile"><small>30 days</small><b>${money((s.month || {}).income)}</b><span>${(s.month || {}).orders ?? "—"} orders · ${c.pending_pays ?? "—"} payments pending</span></div></div>
    ${d.stage_users != null && d.stage_users > (c.users || 0) ? `<div class="gw-out"><b>Real database waiting:</b> the staging copy on the bot server holds ${d.stage_users} users, the live bot has ${c.users}. <button class="pri sm" onclick="sbImport()">Import the real database now</button> <small class="mut">(the live database is dumped to /opt/nikabot/backups first; broadcast queues are not copied)</small></div>` : ""}
    <div class="tabs" style="margin-bottom:10px">${["status", "switches", "users", "tables", "sql", "stats"].map(t => `<button class="${sb.tab === t ? "on" : ""}" onclick="sb.tab='${t}';refresh()">${t[0].toUpperCase() + t.slice(1)}</button>`).join("")}</div>
    <div id="sb-body">${sb.tab === "status" ? sbStatusHtml(d) : sb.tab === "switches" ? sbSwitchesHtml(d) : '<div class="mut">loading…</div>'}</div>`;
  if (sb.tab === "status") sbTgLoad();
  if (sb.tab === "users") sbUsersLoad();
  if (sb.tab === "tables") sbTableLoad();
  if (sb.tab === "sql") sbSqlHtml();
  if (sb.tab === "stats") sbStatsLoad();
};
function sbStatusHtml(d) {
  return `<div class="grid2"><div class="card"><h3>Telegram</h3><div id="sb-tg" class="kv"><div class="mut">asking Telegram…</div></div></div>
    <div class="card"><h3>Panels the bot sells from</h3>${(d.panels || []).map(p => `<div class="gw-row"><span><b>#${esc(p.id)}</b> ${esc(p.type)}</span><span class="mut">${esc(p.host)}</span><span></span></div>`).join("") || '<div class="mut">none</div>'}</div></div>
    <div class="card"><h3>Latest bot errors <span class="sp"></span><small class="mut">${(d.backups || []).length ? "backups: " + esc(d.backups.join(", ")) : ""}</small></h3><pre class="gl-out">${esc((d.logs.errors || []).join("\n") || "(no errors logged)")}</pre>${(d.logs.php || []).length ? `<pre class="gl-out">${esc(d.logs.php.join("\n"))}</pre>` : ""}</div>`;
}
async function sbTgLoad() { const box = $("#sb-tg"); if (!box) return; try { const t = await api("/api/shopbot/telegram"); box.innerHTML = `<div><span>bot</span> @${esc(t.bot.username || "?")} ${esc(t.bot.name || "")}</div><div><span>webhook</span> ${esc(t.webhook.url || "not set")}</div><div><span>pending updates</span> ${t.webhook.pending}</div>${t.webhook.last_error ? `<div><span>last error</span> <b class="err">${esc(t.webhook.last_error)}</b> ${ago(t.webhook.last_error_at)} ago</div>` : ""}`; } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; } }
const SB_SWITCHES = [["cashbackPercent", "Cashback percent on every purchase (0 = off)"], ["wheelState", "Lucky wheel (on / off)"], ["wheelPrizes", "Wheel prizes, comma separated (Toman)"], ["wheelHours", "Hours between two spins"], ["giftState", "Gift codes (on / off)"], ["testState", "Free test accounts (on / off)"], ["botState", "Whole bot (on / off)"]];
function sbSwitchesHtml(d) {
  const st = d.states || {}, known = SB_SWITCHES.map(x => x[0]), other = Object.keys(st).filter(k => !known.includes(k)).sort();
  return `<div class="card"><h3>Switches <small class="mut">— BOT_STATES in the bot's database, read by the bot on every message</small></h3><form class="frm" onsubmit="return sbSaveStates(event)">
    ${SB_SWITCHES.map(([k, label]) => `<label>${label}<input name="${k}" value="${esc(st[k] == null ? "" : st[k])}" placeholder="not set"></label>`).join("")}
    ${other.map(k => `<label>${esc(k)}<input name="${esc(k)}" value="${esc(st[k])}"></label>`).join("")}
    <div class="frm-act"><button class="pri" type="submit">Save changed switches</button></div></form></div>`;
}
window.sbSaveStates = async e => { e.preventDefault(); const f = new FormData(e.target), st = sb.info.states || {}, ch = {}; for (const [k, v] of f.entries()) { if (String(st[k] == null ? "" : st[k]) !== String(v)) ch[k] = v; } if (!Object.keys(ch).length) { toast("nothing changed"); return false; } try { await api("/api/shopbot/states", {method: "PUT", body: JSON.stringify({changes: ch})}); toast("✓ " + Object.keys(ch).join(", ") + " saved", "ok"); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
async function sbUsersLoad() {
  const box = $("#sb-body"); if (!box) return;
  const rows = sb.uq ? (await api("/api/shopbot/users?q=" + encodeURIComponent(sb.uq)).catch(e => { toast("✕ " + e.message, "bad"); return {users: []}; })).users : [];
  box.innerHTML = `<div class="toolbar"><input id="sb-uq" placeholder="Telegram id, username, name or phone…" value="${esc(sb.uq)}" oninput="sbUQ(this.value)"><span class="mut">${sb.uq ? rows.length + " found" : "type to search the bot's users"}</span></div>
    ${rows.length ? `<table class="tbl"><colgroup><col style="width:14%"><col style="width:22%"><col style="width:12%"><col style="width:8%"><col style="width:8%"><col style="width:10%"><col></colgroup><thead><tr><th>Telegram id</th><th>Name</th><th>Wallet</th><th>Orders</th><th>Agent</th><th>Discount</th><th></th></tr></thead>
      <tbody>${rows.map(r => `<tr class="${r.step === "banned" ? "down" : "up"}"><td class="num">${esc(r.userid)}</td><td class="name">${esc(r.name || "")} <small class="mut">${r.username ? "@" + esc(r.username) : ""}</small></td><td class="num">${(+r.wallet || 0).toLocaleString()}</td><td class="num">${esc(r.orders)}</td><td>${r.is_agent === "1" ? '<span class="pill ok">agent</span>' : ""}</td><td class="num">${esc(r.discount_percent || 0)}%</td>
        <td><button class="sm" onclick="(async()=>sbU('${esc(r.userid)}', 'wallet', await gprompt('Add to the wallet (Toman, negative to take)', '10000')))()">± wallet</button> <button class="sm" onclick="(async()=>sbU('${esc(r.userid)}', 'discount', await gprompt('Discount percent', '${esc(r.discount_percent || 0)}')))()">discount</button> <button class="sm" onclick="sbU('${esc(r.userid)}', '${r.is_agent === "1" ? "agent_off" : "agent_on"}')">${r.is_agent === "1" ? "agent off" : "agent on"}</button> <button class="sm ${r.step === "banned" ? "" : "danger"}" onclick="sbU('${esc(r.userid)}', '${r.step === "banned" ? "unban" : "ban"}')">${r.step === "banned" ? "unban" : "ban"}</button></td></tr>`).join("")}</tbody></table>` : ""}`;
  const q = $("#sb-uq"); if (q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
}
window.sbUQ = v => { sb.uq = v.trim(); clearTimeout(qT); qT = setTimeout(refresh, 350); };
window.sbU = async (uid, action, value) => { if ((action === "wallet" || action === "discount") && (value == null || value === "")) return; try { await api("/api/shopbot/users/action", {method: "POST", body: JSON.stringify({action, userid: uid, value})}); toast("✓ " + action + " done", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
async function sbTableLoad() {
  const box = $("#sb-body"); if (!box) return;
  const tables = ["users", "orders_list", "pays", "server_plans", "server_config", "server_categories", "discounts", "setting", "send_list", "add_link", "increase_order", "gift_codes", "wheel_log", "cashback_log"];
  let t; try { t = await api(`/api/shopbot/table/${sb.table}?page=${sb.tpage}&q=${encodeURIComponent(sb.tq)}`); } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  box.innerHTML = `<div class="toolbar"><select onchange="sb.table=this.value;sb.tpage=0;sb.tq='';refresh()">${tables.map(x => `<option ${x === sb.table ? "selected" : ""}>${x}</option>`).join("")}</select><input id="sb-tq" placeholder="filter…" value="${esc(sb.tq)}" oninput="sbTQ(this.value)"><span class="mut">${t.total} rows</span><span class="sp"></span><button class="sm" ${sb.tpage ? "" : "disabled"} onclick="sb.tpage--;refresh()">‹</button><button class="sm" ${(sb.tpage + 1) * 40 < t.total ? "" : "disabled"} onclick="sb.tpage++;refresh()">›</button></div>
    <div style="overflow:auto"><table class="tbl" style="table-layout:auto;min-width:100%"><thead><tr>${t.columns.map(c => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${t.rows.map(r => `<tr>${t.columns.map(c => `<td title="${esc(r[c])}" style="max-width:260px">${esc(r[c] == null ? "" : r[c])}</td>`).join("")}</tr>`).join("") || `<tr><td class="mut">empty</td></tr>`}</tbody></table></div>`;
  const q = $("#sb-tq"); if (q && sb.tq) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
}
window.sbTQ = v => { sb.tq = v.trim(); sb.tpage = 0; clearTimeout(qT); qT = setTimeout(refresh, 350); };
function sbSqlHtml() {
  const box = $("#sb-body"); if (!box) return;
  const r = sb.qres;
  box.innerHTML = `<div class="card"><h3>Read-only SQL <small class="mut">— one SELECT or SHOW; string literals in double quotes; 200 rows max</small></h3><textarea id="sb-sql" class="ob-raw" spellcheck="false" placeholder='SELECT userid, name, wallet FROM users WHERE wallet > 100000 ORDER BY wallet DESC'>${esc(sb.sql)}</textarea><div class="frm-act"><button class="pri" onclick="sbRun()">Run</button></div></div>
    ${r ? (r.error ? `<div class="err">${esc(r.error)}</div>` : `<div class="mut">${r.rows.length} rows</div><div style="overflow:auto"><table class="tbl" style="table-layout:auto;min-width:100%"><thead><tr>${r.columns.map(c => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${r.rows.map(x => `<tr>${r.columns.map(c => `<td style="max-width:260px" title="${esc(x[c])}">${esc(x[c] == null ? "" : x[c])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`) : ""}`;
}
window.sbRun = async () => { sb.sql = $("#sb-sql").value; try { sb.qres = await api("/api/shopbot/query", {method: "POST", body: JSON.stringify({sql: sb.sql})}); } catch (e) { sb.qres = {error: e.message}; } sbSqlHtml(); };
async function sbStatsLoad() {
  const box = $("#sb-body"); if (!box) return;
  let s; try { s = await api("/api/shopbot/stats"); } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  const list = (title, rows, f) => `<div class="card"><h3>${title}</h3>${rows.map(r => `<div class="gw-row"><span>${f(r)}</span><span></span><span></span></div>`).join("") || '<div class="mut">nothing</div>'}</div>`;
  box.innerHTML = `<div class="card"><h3>Income per day, 30 days</h3>${spark(s.daily.map(d => +d.income / 1000), {fmt: v => v.toFixed(0) + " k"})}<div class="mut">${s.daily.length ? s.daily[0].day + " → " + s.daily[s.daily.length - 1].day : ""}</div></div>
    <div class="grid2">${list("Top buyers", s.top_buyers, r => `<b>${esc(r.name || r.userid)}</b> <small class="mut">${r.orders} orders · ${(+r.spent / 1000).toLocaleString()} k</small>`)}${list("Biggest wallets", s.top_wallets, r => `<b>${esc(r.name || r.userid)}</b> <small class="mut">${(+r.wallet / 1000).toLocaleString()} k</small>`)}</div>
    ${list("Pending payments", s.pending_pays, r => `<b>#${esc(r.id)}</b> ${esc(r.name || r.user_id)} <small class="mut">${(+r.price / 1000).toLocaleString()} k · ${esc(r.type)}</small>`)}`;
}
window.sbAct = async act => { const msgs = {webhook: "Point the Telegram webhook at the bot's public URL?", restart: "Restart PHP on the bot server?", backup: "Dump the bot's database to /opt/nikabot/backups?", clearlogs: "Clear the bot's log files?"}; if (!await gconfirm(msgs[act])) return; toast("Working…"); try { const r = await api(`/api/shopbot/action/${act}`, {method: "POST"}); toast((r.done ? "✓ " : "✕ ") + (r.message || act), r.done ? "ok" : "bad"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.sbImport = async () => { if (!await gconfirm("Import the real database over the live one?\n\nThe live database is dumped first. Users, orders, payments, plans, panels and settings are replaced; broadcast queues are not copied. The bot then serves the real customers.")) return; toast("Dumping and importing… (1–3 minutes)"); try { const r = await api("/api/shopbot/import-stage", {method: "POST"}); toast(`✓ Imported: ${r.tables.users} users, ${r.tables.orders_list} orders · backup ${r.backup.split("/").pop()}`, "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };

/* ---- gateway (Xray on an Iran server, borrowed configs for internet cuts) */
const gw = {host: localStorage.getItem("nk.gw") || "", st: null, status: null, test: null, note: "", noteCls: "", dirty: false};
INFO.gateway = ["Gateway", INFO_GW];
PAGES.gateway = async () => {
  const [srvAll, pnl] = await Promise.all([api("/api/servers?role=iran"), api("/api/panels").catch(() => ({panels: []}))]);
  const servers = srvAll.filter(s => s.enabled), panelsList = pnl.panels || [], pnSel = panelsList.find(x => String(x.id) === pn.id) || panelsList[0] || {};
  if (!servers.length) { $("#page").innerHTML = '<div class="empty">No enabled Iran server.</div>'; return; }
  if (!servers.some(s => String(s.id) === gw.host)) { gw.host = String(servers[0].id); gw.st = null; }
  if (!gw.st || gw.st.server_id !== +gw.host) { gw.st = await api(`/api/gateway/${gw.host}`); gw.status = null; gw.test = null; gw.dirty = false; }
  const st = gw.st, t = gw.test || [], tr = k => t.find(x => x.key === k);
  const cell = k => { const r = tr(k); return r ? (r.ok ? `<span class="gw-ok">✓ ${r.ms} ms</span> <small class="mut">${esc(r.ip || "")} ${esc(r.country || "")}</small>` : '<span class="err">✗ no answer</span>') : ""; };
  const chip = !st.installed ? '<span class="pill">not installed</span>' : !gw.status ? '<span class="pill">status unknown</span>' : gw.status.error ? `<span class="pill err">✗ ${esc(gw.status.error).slice(0, 60)}</span>` : gw.status.active === "active" ? `<span class="pill ok">● running · ${esc((gw.status.version || "").split("(")[0])}</span>` : `<span class="pill err">● ${esc(gw.status.active || "stopped")}</span>`;
  gw.tab = gw.tab || localStorage.getItem("nk.gwtab") || "gateway";
  const gwHead = `<div class="tabs" style="margin-bottom:12px"><button class="${gw.tab === "gateway" ? "on" : ""}" onclick="gw.tab='gateway';localStorage.setItem('nk.gwtab','gateway');refresh()">🌐 Iran gateway</button><button class="${gw.tab === "outbounds" ? "on" : ""}" onclick="gw.tab='outbounds';localStorage.setItem('nk.gwtab','outbounds');refresh()">🧩 Panel outbounds</button></div>`;
  if (gw.tab === "outbounds") {
    $("#page").innerHTML = `${gwHead}${panelsList.length ? `<div class="toolbar"><select onchange="pn.id=this.value;localStorage.setItem('nk.pn',this.value);refresh()">${panelsList.map(x => `<option value="${x.id}" ${String(x.id) === String(pnSel.id) ? "selected" : ""}>${esc(x.name)} — ${x.type}</option>`).join("")}</select><span class="mut">the Xray outbounds of this panel's core: where its users' traffic leaves</span></div><div id="pn-body"><div class="mut">loading…</div></div>` : '<div class="empty">No panel yet — add one under Panels.</div>'}`;
    if (panelsList.length) pnCoreLoad(pnSel.id);
    return;
  }
  const listen = p => gw.status && gw.status.listen && gw.status.listen[p.port] != null ? (gw.status.listen[p.port] ? '<span class="gw-ok">listening</span>' : '<span class="err">not listening</span>') : "";
  $("#page").innerHTML = `${gwHead}
    <div class="card"><h3>Iran gateway <span class="sp"></span>${chip}${st.read_only ? '<span class="badge">read-only</span>' : ""}</h3>
      <div class="frm" style="grid-template-columns: 1.2fr 1.6fr auto">
        <label>Iran server<select onchange="gwPick(this.value)">${servers.map(s => `<option value="${s.id}" ${String(s.id) === gw.host ? "selected" : ""}>${esc(s.name)} — ${esc(s.host)}</option>`).join("")}</select></label>
        <label>When to use the borrowed configs<select onchange="gwField('mode', this.value)"><option value="auto" ${st.mode === "auto" ? "selected" : ""}>Automatic — direct while it works, borrowed when it does not</option><option value="borrowed" ${st.mode === "borrowed" ? "selected" : ""}>Always through the borrowed configs</option><option value="direct" ${st.mode === "direct" ? "selected" : ""}>Direct only</option></select></label>
        <div class="frm-act" style="align-self:end">${st.installed ? `<button class="pri" onclick="gwApply()">Save &amp; apply</button><button onclick="gwStatus()">↻ Status</button><button onclick="gwTest()">⚡ Test exits</button><button class="danger sm" onclick="gwRemove()">Remove</button>` : `<button class="pri" onclick="gwInstall()" ${st.binary ? "" : "disabled"} title="${st.binary ? "" : "run scripts/fetch-xray.sh on the panel server first"}">Install Xray on this server</button>`}</div></div>
      ${gw.note ? `<div class="gw-out ${gw.noteCls}" style="margin-top:10px">${esc(gw.note)}</div>` : ""}${gw.dirty ? '<div class="gw-dirty">Unsaved changes — press “Save &amp; apply”.</div>' : ""}</div>
    <div class="grid2">
      <div class="card"><h3>Ports <span class="sp"></span><small class="mut">a port here that forwards to a foreign node</small></h3>
        ${st.ports.map((p, i) => `<div class="gw-row"><span><b>:${p.port}</b> → ${esc(p.target_host)}:${p.target_port} <small class="mut">${p.udp ? "tcp+udp" : "tcp"} ${esc(p.note || "")}</small></span><span>${listen(p)}</span><span class="ob-act"><label class="chk"><input type="checkbox" ${p.enabled !== false ? "checked" : ""} onchange="gwToggle('ports', ${i}, this.checked)"> on</label><button class="sm danger" onclick="gwDel('ports', ${i})">✕</button></span></div>`).join("") || '<div class="empty">No port yet.</div>'}
        <div class="frm" style="grid-template-columns:1fr 1.4fr 1fr;margin-top:8px"><label>Listen port<input id="gw-p" type="number" min="1024" max="65535" placeholder="28080"></label><label>Node address<input id="gw-h" placeholder="ip or host"></label><label>Node port<input id="gw-tp" type="number" min="1" max="65535" placeholder="443"></label><label>Note<input id="gw-n" placeholder="optional"></label><label class="chk"><input id="gw-udp" type="checkbox"> udp too</label><div class="frm-act"><button class="pri" onclick="gwAddPort()">Add port</button></div></div></div>
      <div class="card"><h3>Borrowed configs <span class="sp"></span><small class="mut">the fastest alive one is used during cuts</small></h3>
        ${st.borrowed.map((b, i) => `<div class="gw-row"><span><b>${esc(b.note || "config " + (i + 1))}</b> <small class="mut">${esc(b.info || "new")}</small></span><span>${b.id ? cell(b.id) : ""}</span><span class="ob-act"><label class="chk"><input type="checkbox" ${b.enabled ? "checked" : ""} onchange="gwToggle('borrowed', ${i}, this.checked)"> on</label><button class="sm danger" onclick="gwDel('borrowed', ${i})">✕</button></span></div>`).join("") || '<div class="empty">No borrowed config yet.</div>'}
        <textarea id="gw-uri" class="ob-raw" placeholder="vless://…  vmess://…  trojan://…  ss://…" spellcheck="false"></textarea>
        <div class="frm-act"><input id="gw-note" placeholder="note (optional)"><button class="pri" onclick="gwAddBorrowed()">Add this config</button></div></div></div>
    ${gw.test ? `<div class="card"><h3>Exit test <small class="mut">— from the Iran server, through every exit</small></h3>${t.map(r => `<div class="gw-row"><span><b>${esc(r.key === "direct" ? "Direct (no borrowed config)" : r.key === "gateway" ? "The gateway as users see it (current mode)" : r.name)}</b></span><span>${cell(r.key)}</span><span></span></div>`).join("")}</div>` : ""}
    ${gw.status && gw.status.log && gw.status.log.length ? `<div class="card"><h3>Latest log lines</h3><pre class="gl-out">${esc(gw.status.log.join("\n"))}</pre></div>` : ""}`;
};window.gwPick = v => { gw.host = v; localStorage.setItem("nk.gw", v); gw.st = null; gw.note = ""; refresh(); };
window.gwField = (k, v) => { gw.st[k] = v; gw.dirty = true; refresh(); };
window.gwToggle = (k, i, on) => { gw.st[k][i].enabled = on; gw.dirty = true; refresh(); };
window.gwDel = (k, i) => { gw.st[k].splice(i, 1); gw.dirty = true; refresh(); };
window.gwAddBorrowed = () => { const uri = $("#gw-uri").value.trim(), note = $("#gw-note").value.trim(); if (!uri) return; gw.st.borrowed.push({id: "", uri, note, enabled: true, info: "new"}); gw.dirty = true; refresh(); };
window.gwAddPort = () => {
  const port = +$("#gw-p").value, host = $("#gw-h").value.trim(), tp = +$("#gw-tp").value;
  if (!(port >= 1024) || !host || !(tp >= 1)) { gw.note = "✗ Fill the listen port (1024–65535), the node address and the node port."; gw.noteCls = "bad"; refresh(); return; }
  gw.st.ports.push({port, target_host: host, target_port: tp, udp: $("#gw-udp").checked, enabled: true, note: $("#gw-n").value.trim()}); gw.dirty = true; gw.note = ""; refresh();
};
async function gwSave() { const r = await api(`/api/gateway/${gw.host}`, {method: "PUT", body: JSON.stringify({mode: gw.st.mode, ports: gw.st.ports, borrowed: gw.st.borrowed.map(b => ({id: b.id, uri: b.uri || "", note: b.note, enabled: b.enabled}))})}); gw.st = Object.assign(gw.st, r); gw.dirty = false; }
window.gwInstall = async () => { if (!await gconfirm("Copy Xray to this Iran server (about 35 MB over SSH) and install it as the service nikatel-xgw? Nothing else on the server is touched.")) return; gw.note = "Installing… this can take a minute"; gw.noteCls = ""; refresh(); try { await gwSave(); const r = await api(`/api/gateway/${gw.host}/install`, {method: "POST"}); gw.note = "✓ Installed: " + r.version; gw.noteCls = "ok"; gw.st = null; } catch (e) { gw.note = "✕ " + e.message; gw.noteCls = "bad"; } refresh(); };
window.gwApply = async () => { gw.note = "Saving and applying…"; gw.noteCls = ""; refresh(); try { await gwSave(); const r = await api(`/api/gateway/${gw.host}/apply`, {method: "POST"}); gw.note = "✓ Gateway running" + (r.ports.length ? " on port " + r.ports.join(", ") : ""); gw.noteCls = "ok"; } catch (e) { gw.note = "✕ " + e.message; gw.noteCls = "bad"; } await gwStatus(); };
window.gwStatus = async () => { try { gw.status = await api(`/api/gateway/${gw.host}/status`, {method: "POST"}); } catch (e) { gw.status = {error: e.message}; } refresh(); };
window.gwTest = async () => { gw.note = "Testing every exit from the Iran server…"; gw.noteCls = ""; refresh(); try { gw.test = (await api(`/api/gateway/${gw.host}/test`, {method: "POST"})).results; gw.note = ""; } catch (e) { gw.note = "✕ " + e.message; gw.noteCls = "bad"; } refresh(); };
window.gwRemove = async () => { if (!await gconfirm("Remove the gateway from this server? Your list of ports and borrowed configs stays in the panel.")) return; try { await api(`/api/gateway/${gw.host}/remove`, {method: "POST"}); gw.note = "✓ Removed"; gw.noteCls = "ok"; gw.st = null; } catch (e) { gw.note = "✕ " + e.message; gw.noteCls = "bad"; } refresh(); };

/* ---- events */
PAGES.events = async () => {
  const ev = await api("/api/events?limit=300");
  $("#page").innerHTML = `<div class="card">${eventsHtml(ev.events)}</div>`;
};

/* ---- jobs */
PAGES.jobs = async () => {
  const j = await api("/api/jobs");
  $("#page").innerHTML = `<table class="tbl"><colgroup><col style="width:16%"><col style="width:10%"><col style="width:10%"><col style="width:14%"><col style="width:14%"><col style="width:10%"><col><col style="width:150px"></colgroup>
    <thead><tr><th>Job</th><th>Every</th><th>State</th><th>Last ok</th><th>Last start</th><th>Took</th><th>Last error</th><th></th></tr></thead>
    <tbody>${j.jobs.map(x => `<tr class="${x.last_error ? "down" : x.last_ok ? "up" : ""}">
      <td class="name">${esc(x.name)}</td><td>${x.interval}s</td>
      <td><span class="st ${x.running ? "degraded" : x.enabled ? "up" : ""}">${x.running ? "running" : x.enabled ? "on" : "off"}</span></td>
      <td class="mut">${x.last_ok ? ago(x.last_ok) + " ago" : "never"}</td><td class="mut">${x.last_start ? ago(x.last_start) + " ago" : "never"}</td>
      <td class="num">${x.last_duration_ms ? (x.last_duration_ms / 1000).toFixed(1) + "s" : "—"}</td>
      <td class="mut" title="${esc(x.last_error)}">${esc(x.last_error).slice(0, 60)} ${x.failures ? `<span class="pill">${x.failures}/${x.runs} failed</span>` : ""}</td>
      <td><button class="sm" onclick="runJob('${esc(x.name)}')">Run now</button> <button class="sm" onclick="jobEnabled('${esc(x.name)}', ${!x.enabled})">${x.enabled ? "Pause" : "Resume"}</button></td></tr>`).join("")}</tbody></table>`;
};
window.runJob = async n => { try { await api(`/api/jobs/${n}/run`, {method: "POST"}); toast("✓ " + n + " runs now", "ok"); setTimeout(refresh, 1500); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.jobEnabled = async (n, on) => { try { await api(`/api/jobs/${n}/enabled?enabled=${on}`, {method: "POST"}); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };

/* ---- logs */
PAGES.logs = async () => {
  const L = state.logs;
  const d = await api(`/api/logs?lines=400&level=${encodeURIComponent(L.level)}&domain=${encodeURIComponent(L.domain)}&q=${encodeURIComponent(L.q)}`);
  const domains = ["", "http", "auth", "servers", "tunnels", "ssh", "jobs", "db", "events", "import"];
  $("#page").innerHTML = `<div class="toolbar">
      <select onchange="setLog('level', this.value)">${["", "INFO", "WARNING", "ERROR", "DEBUG"].map(l => `<option value="${l}" ${L.level === l ? "selected" : ""}>${l || "all levels"}</option>`).join("")}</select>
      <select onchange="setLog('domain', this.value)">${domains.map(x => `<option value="${x}" ${L.domain === x ? "selected" : ""}>${x || "all domains"}</option>`).join("")}</select>
      <input id="lq" placeholder="text or rid…" value="${esc(L.q)}" oninput="setLogQ(this.value)"><span class="sp"></span><span class="mut">${d.lines.length} lines</span></div>
    <div class="card log">${d.lines.map(l => `<div class="ll ${l.level}"><time>${esc(l.ts.slice(11, 19))}</time><b>${esc(l.level)}</b><i>${esc(l.domain)}</i><span>${esc(l.text)}</span><small>${esc(l.rid)}</small></div>`).join("") || '<div class="empty">No matching log line.</div>'}</div>`;
  const q = $("#lq"); if (q && L.q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
};
window.setLog = (k, v) => { state.logs[k] = v; refresh(); };
let lqT; window.setLogQ = v => { state.logs.q = v.trim(); clearTimeout(lqT); lqT = setTimeout(refresh, 250); };

/* ---- settings */
PAGES.settings = async () => {
  const [s, tg, al, rp, bk, mw, rp_, pr, iranList, sb, ai_] = await Promise.all([api("/api/settings"), api("/api/settings/telegram"), api("/api/settings/alerts"), api("/api/settings/report"), api("/api/settings/backup"), api("/api/maintenance"), api("/api/settings/repair"), api("/api/settings/probe"), api("/api/servers?role=iran"), api("/api/settings/shopbot"), api("/api/settings/ai")]);
  const dt = ts => new Date(ts * 1000).toISOString().slice(0, 16);
  $("#page").innerHTML = `<div class="grid2">
    <div class="card"><h3>Telegram <span class="sp"></span>${tg.has_token ? '<span class="pill">token saved</span>' : '<span class="pill">no token</span>'}</h3>
      <form class="frm one" onsubmit="return saveTelegram(event)">
        <label>Bot token <small>(from @BotFather; leave empty to keep the saved one)</small><input name="token" type="password" placeholder="${tg.has_token ? "•••••••••• saved" : "123456789:AA…"}" autocomplete="off"></label>
        <label>Chat id <small>(your id, or a negative group/channel id, or @channel)</small><input name="chat_id" value="${esc(tg.chat_id)}" placeholder="-1001234567890"></label>
        <label class="chk"><input name="enabled" type="checkbox" ${tg.enabled ? "checked" : ""}> send alerts, reports and backups</label>
        <div class="frm-act"><button class="pri" type="submit">Save</button><button type="button" onclick="testTelegram()">Send a test message</button></div></form></div>

    <div class="card"><h3>Alert rules</h3>
      <form class="frm one" onsubmit="return saveRules(event)">
        <label>Tunnel: failed checks in a row before DOWN <input name="tunnel_fails" type="number" min="1" max="20" value="${al.tunnel_fails}"></label>
        <label>Server: failed samples in a row before OFFLINE <input name="server_fails" type="number" min="1" max="20" value="${al.server_fails}"></label>
        <label>UP message only after stable for (seconds) <input name="up_stable_seconds" type="number" min="0" max="86400" value="${al.up_stable_seconds}"></label>
        <label>Remind while still down every (hours) <input name="remind_hours" type="number" min="1" max="168" value="${al.remind_hours}"></label>
        <div class="frm-act"><button class="pri" type="submit">Save</button></div></form>
      <small class="mut">Muted: ${(al.muted || []).map(m => `<code>${esc(m)}</code> <a onclick="muteRef('${esc(m)}', false)">unmute</a>`).join(" · ") || "nothing"}. Mute a tunnel from its detail row.</small></div>

    <div class="card"><h3>Maintenance windows <span class="sp"></span><small class="mut">no alerts are sent inside a window</small></h3>
      <form class="frm" onsubmit="return addWindow(event)">
        <label>From (UTC)<input name="start" type="datetime-local" required value="${dt(Math.floor(Date.now() / 1000))}"></label>
        <label>To (UTC)<input name="end" type="datetime-local" required value="${dt(Math.floor(Date.now() / 1000) + 3600)}"></label>
        <label>Note<input name="note" placeholder="upgrade of …"></label>
        <div class="frm-act"><button class="pri" type="submit">Add window</button></div></form>
      ${mw.windows.map(w => `<div class="ev ${w.active ? "warn" : ""}"><time>${when(w.start).slice(0, 16)}</time><i></i><small>→ ${when(w.end).slice(0, 16)}</small><span>${esc(w.note)} ${w.active ? "<b>(active now)</b>" : ""} <a class="mut" onclick="delWindow(${w.id})">remove</a></span></div>`).join("") || '<div class="empty">No window.</div>'}</div>

    <div class="card"><h3>Probe <span class="sp"></span><span class="pill">${pr.state.active ? "active: " + esc(pr.state.active) : "none"}</span></h3>
      <form class="frm one" onsubmit="return saveProbe(event)">
        <label>Primary probe address<input name="api_url" value="${esc(pr.api_url)}" placeholder="http://ip:18080"></label>
        <label>Token <small>(empty keeps the saved one)</small><input name="api_token" type="password" placeholder="${pr.has_api_token ? "•••••••• saved" : "from speed-api.token on the probe"}" autocomplete="off"></label>
        <label>Server that runs the probe <small>(for speed tests over SSH)</small><select name="server_id"><option value="0">—</option>${iranList.map(s => `<option value="${s.id}" ${s.id === pr.server_id ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select></label>
        <label>Standby probes <small>(one per line: name,http://ip:18080,token — token may be empty to keep)</small><textarea name="standby" class="ob-raw">${(pr.standby || []).map(x => `${x.name},${x.api_url},`).join("\n")}</textarea></label>
        <div class="frm-act"><button class="pri" type="submit">Save</button><button type="button" onclick="testProbe()">Test</button></div></form>
      <form class="frm one" onsubmit="return installProbe(event)"><label>Install the probe on an Iran server <small>(code from this repo + Xray from the cache; registers it as standby, or primary when none exists)</small><select name="server_id">${iranList.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join("")}</select></label>
        <div class="frm-act"><button type="submit" ${s.read_only ? "disabled" : ""}>Install probe</button></div></form></div>

    <div class="card"><h3>Repairs <span class="sp"></span><span class="pill">${rp_.read_only ? "read-only" : "read-write"}</span></h3>
      <form class="frm one" onsubmit="return saveRepair(event)">
        <label class="chk"><input name="auto" type="checkbox" ${rp_.auto ? "checked" : ""} ${rp_.read_only ? "disabled" : ""}> repair a down tunnel automatically</label>
        <label>…after it has been down for (seconds)<input name="after_seconds" type="number" min="60" max="3600" value="${rp_.after_seconds}"></label>
        <label>How far it may go<select name="level">${[["restart", "Restart only (safe)"], ["rebuild", "+ rebuild the tunnel if restarting did not help"], ["switch", "+ switch the tunnel type when the route blocks it (GRE / port filtered)"]].map(([k, l]) => `<option value="${k}" ${(rp_.level || "restart") === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <div class="frm-act"><button class="pri" type="submit">Save</button></div></form>
      <small class="mut">At most ${rp_.max_per_hour} restarts per tunnel per hour; never when a server is unreachable. The deeper steps run only after restarts failed, once per 6 hours per tunnel; moving a tunnel to another Iran server is never automatic. Details on the Smart repair page. ${rp_.read_only ? "Repairs need <code>NIKATEL_READ_ONLY=false</code> in the env file first." : ""}</small></div>

    <div class="card"><h3>Daily report</h3>
      <form class="frm one" onsubmit="return saveReport(event)">
        <label class="chk"><input name="enabled" type="checkbox" ${rp.enabled ? "checked" : ""}> send a daily report</label>
        <label>At hour (local)<input name="hour" type="number" min="0" max="23" value="${rp.hour}"></label>
        <label>Local time offset from UTC (minutes; Tehran = 210)<input name="tz_offset_minutes" type="number" min="-720" max="840" value="${rp.tz_offset_minutes}"></label>
        <div class="frm-act"><button class="pri" type="submit">Save</button><button type="button" onclick="previewReport()">Preview</button><button type="button" onclick="sendReport()">Send now</button></div></form>
      <pre id="report-prev" class="gl-out hidden"></pre></div>

    <div class="card"><h3>Backups <span class="sp"></span>${bk.has_passphrase ? '<span class="pill">passphrase set</span>' : '<span class="pill">no passphrase</span>'}</h3>
      <form class="frm one" onsubmit="return savePass(event)">
        <label>Backup passphrase <small>(min 12 characters; needed to restore — keep it somewhere safe)</small><input name="passphrase" type="password" minlength="12" required autocomplete="new-password" placeholder="${bk.has_passphrase ? "•••••••••••• set — type to replace" : ""}"></label>
        <div class="frm-act"><button class="pri" type="submit">${bk.has_passphrase ? "Replace passphrase" : "Set passphrase"}</button></div></form>
      <form class="frm one" onsubmit="return saveBackup(event)">
        <label class="chk"><input name="enabled" type="checkbox" ${bk.enabled ? "checked" : ""}> daily backup</label>
        <label>At hour (local)<input name="hour" type="number" min="0" max="23" value="${bk.hour}"></label>
        <label>Keep (days)<input name="keep_days" type="number" min="1" max="365" value="${bk.keep_days}"></label>
        <label class="chk"><input name="send_telegram" type="checkbox" ${bk.send_telegram ? "checked" : ""}> also send to Telegram</label>
        <div class="frm-act"><button class="pri" type="submit">Save</button><button type="button" onclick="backupNow()">Back up now</button></div></form>
      ${bk.backups.slice(0, 8).map(b => `<div class="ev"><time>${ago(b.at)} ago</time><i></i><small>${Math.round(b.bytes / 1024)} KB</small><span><a href="/api/backup/download/${esc(b.file)}">${esc(b.file)}</a></span></div>`).join("") || '<div class="empty">No backup yet.</div>'}
      <small class="mut">Restore: stop the service, then <code>nikatel restore &lt;file&gt;</code> on the server.</small></div>

    <div class="card"><h3>Shop bot <span class="sp"></span><span class="pill">${sb.host ? "server set" : "not set"}</span></h3>
      <form class="frm one" onsubmit="return saveShopbot(event)">
        <label>Server that runs the bot <small>(IP or host, reached with the panel's SSH key)</small><input name="host" value="${esc(sb.host)}" placeholder="ip or host"></label>
        <label>SSH user / port<input name="ssh_user" value="${esc(sb.ssh_user)}" style="width:48%;display:inline-block"> <input name="ssh_port" type="number" value="${sb.ssh_port}" style="width:48%;display:inline-block"></label>
        <label>Bot directory<input name="dir" value="${esc(sb.dir)}"></label><label>DB env file<input name="env" value="${esc(sb.env)}"></label>
        <label>Public webhook URL<input name="public_url" value="${esc(sb.public_url)}" placeholder="https://host/path/bot.php"></label>
        <div class="frm-act"><button class="pri" type="submit">Save</button></div></form></div>

    <div class="card"><h3>AI assistant <span class="sp"></span><span class="pill">${ai_.has_api_key ? "key saved" : "no key"}</span></h3>
      <form class="frm one" onsubmit="return saveAi(event)">
        <label class="chk"><input name="enabled" type="checkbox" ${ai_.enabled ? "checked" : ""}> use the AI to diagnose broken tunnels (inside Fix)</label>
        <label class="chk"><input name="actions" type="checkbox" ${ai_.actions ? "checked" : ""}> let the AI add safe repair steps to the plan</label>
        <label>Anthropic API key <small>(empty keeps the saved one)</small><input name="api_key" type="password" placeholder="${ai_.has_api_key ? "•••••••• saved" : "your Anthropic key"}" autocomplete="off"></label>
        <label>Model<input name="model" value="${esc(ai_.model)}"></label>
        <div class="frm-act"><button class="pri" type="submit">Save</button><button type="button" onclick="testAi()">Test the key</button><button type="button" class="danger" onclick="forgetAi()">Remove the key</button></div></form></div>

    <div class="card"><h3>Appearance</h3>
      <div class="frm">
        <label>Theme<select onchange="lookSet('theme', this.value)">${[["aurora", "🌌 Aurora glass"], ["gold", "✨ Gold luxury"], ["neon", "⚡ Cyber neon"], ["minimal", "▫ Minimal"]].map(([k, l]) => `<option value="${k}" ${LOOK.theme === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <label>Day / night<select onchange="lookSet('mode', this.value)">${[["dark", "Night"], ["light", "Day"], ["auto", "Follow my device"]].map(([k, l]) => `<option value="${k}" ${LOOK.mode === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <label>Text size<select onchange="lookSet('size', this.value)">${[["small", "Small"], ["normal", "Normal"], ["large", "Large"]].map(([k, l]) => `<option value="${k}" ${LOOK.size === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <label>Animations<select onchange="lookSet('anim', this.value)">${[["on", "On"], ["off", "Off (calmer)"]].map(([k, l]) => `<option value="${k}" ${LOOK.anim === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <div class="frm-act"><button type="button" onclick="lookReset()">↺ Reset the look</button><small class="mut">kept in this browser</small></div></div></div>

    <div class="card" style="grid-column:1/-1"><h3>Core update <span class="sp"></span><small class="mut">backhaul and Xray on every server</small></h3><div id="eng-box"><div class="mut">loading…</div></div></div>

    <div class="card"><h3>About <span class="sp"></span><span class="pill">v${esc(s.version)}</span></h3>
      <div class="kv one" id="about-box"><div class="mut">checking GitHub…</div></div>
      <div class="frm-act"><button class="pri" onclick="panelUpdate()">Update from GitHub</button><button onclick="aboutLoad()">↻ Check</button><small class="mut">pull, tests, restart, health check — rolls back by itself when the new version does not start</small></div>
      <pre class="gl-out hidden" id="about-log"></pre></div>

    <div class="card"><h3>Security <span class="sp"></span><span class="pill" id="sec-pill">…</span></h3><div id="sec-box"><div class="mut">loading…</div></div></div>

    <div class="card"><h3>Password</h3>
      <form class="frm one" onsubmit="return changePassword(event)">
        <label>Current password<input name="current" type="password" required autocomplete="current-password"></label>
        <label>New password <small>(at least 10 characters)</small><input name="new" type="password" required minlength="10" autocomplete="new-password"></label>
        <label>Repeat<input name="again" type="password" required minlength="10" autocomplete="new-password"></label>
        <label class="chk"><input name="logout_others" type="checkbox" checked> sign out every other session (other browsers and devices)</label>
        <div class="frm-act"><button class="pri" type="submit">Change password</button></div></form></div>

    <div class="card"><h3>Effective settings</h3><div class="kv one">
      <div><span>Version</span> <b>${esc(s.version)}</b></div>
      <div><span>Mode</span> <b>${s.read_only ? "read-only — the panel never changes a server" : "read-write"}</b></div>
      <div><span>Server sampling</span> every ${s.server_sample_interval} s</div>
      <div><span>Tunnel checks</span> every ${s.tunnel_check_interval} s</div>
      <div><span>Parallel SSH</span> ${s.ssh_workers}</div>
      <div><span>Session</span> ${s.session_hours} h</div>
      <div><span>Log level</span> ${esc(s.log_level)}</div>
      <div><span>Port</span> ${s.listen_port}</div></div>
      <small class="mut">These come from <code>${esc(s.env_file)}</code> on the panel server (then <code>systemctl restart nikatel</code>). Read-only cannot be switched off from here on purpose.</small></div>
  </div>`;
  settingsTabs();
  if (state.stab === "update") { aboutLoad(); enginesLoad(); }
  if ($("#sec-box")) secLoad();
};
/* ---- security: two-step sign-in (authenticator app), recovery codes, the secret entry path, the security log */
const sec = {setup: null, codes: null};
async function secLoad() {
  const box = $("#sec-box"); if (!box) return;
  const s = await api("/api/auth/security");
  const pill = $("#sec-pill"); if (pill) { pill.textContent = s.totp_enabled ? "2-step on" : "2-step OFF"; pill.className = "pill " + (s.totp_enabled ? "ok" : "err"); }
  const url = s.entry_path ? location.origin + "/" + s.entry_path : "";
  const codes = sec.codes ? `<div class="sec-sec" style="border-color:var(--warn)"><h4>⚠ Your recovery codes - shown once</h4><small class="mut">Each code works one time: to sign in without the app, or to reset a forgotten password. Keep them somewhere safe and offline.</small><div class="sec-codes">${sec.codes.map(c => `<code>${esc(c)}</code>`).join("")}</div><div class="frm-act"><button class="sm" onclick="secDownloadCodes()">⬇ Download .txt</button><button class="sm pri" onclick="sec.codes=null;secLoad()">I saved them</button></div></div>` : "";
  const twofa = sec.setup ? `<div class="sec-qr">${sec.setup.qr_svg || ""}<div style="flex:1;min-width:240px"><b>1.</b> Open Google Authenticator (or any TOTP app) → add an account → scan the picture, or type this key:<div class="sec-key">${esc(sec.setup.secret.replace(/(.{4})/g, "$1 ").trim())}</div><div style="margin-top:10px"><b>2.</b> Type the 6-digit code the app shows:</div><div class="frm-act"><input id="sec-code" inputmode="numeric" maxlength="7" placeholder="123456" style="width:140px" onkeydown="if(event.key==='Enter')secEnable()"><button class="pri" onclick="secEnable()">Turn on</button><button onclick="sec.setup=null;secLoad()">Cancel</button></div></div></div>`
    : s.totp_enabled ? `<div class="irow"><span>Status</span><div><b class="gw-ok">On</b> - every sign-in asks for the app's code</div></div><div class="frm-act"><button class="sm danger" onclick="secOff()">Turn off</button></div>`
    : `<div class="irow"><span>Status</span><div><b class="err">Off</b> - only the password protects the panel</div></div><small class="mut">Strongly recommended: a stolen or guessed password is then not enough.</small><div class="frm-act"><button class="pri" onclick="secSetup()">Turn on with Google Authenticator</button></div>`;
  box.innerHTML = `${codes}
    <div class="sec-sec"><h4>Two-step sign-in</h4>${twofa}</div>
    <div class="sec-sec"><h4>Recovery <span class="sp"></span><span class="pill">${s.recovery_left} codes left</span></h4><small class="mut">If you forget the password: on the sign-in page choose “Forgot the password?” and use a recovery code or a code sent to your Telegram. Last resort on the server: <code>nikatel set-password</code>.</small><div class="frm-act"><button class="sm" onclick="secNewCodes()">Make new recovery codes</button></div></div>
    <div class="sec-sec"><h4>Secret entry path</h4><small class="mut">With a path set, the panel answers “404 Not Found” to everyone, on every address, until the browser has opened the secret address once. Scanners and bots find nothing.</small>
      ${url ? `<a class="sec-url" href="${esc(url)}" title="Open">${esc(url)}</a><small class="err">Write this address down. If you lose it: <code>nikatel entry-path --clear</code> on the server.</small>` : '<div class="irow"><span>Status</span><div><b class="err">Off</b> - the panel answers on its normal address</div></div>'}
      <div class="frm-act"><button class="sm pri" onclick="secPath(true)">${url ? "Change the path" : "Turn on with a random path"}</button>${url ? '<button class="sm" onclick="secPath(false)">Choose my own</button><button class="sm danger" onclick="secPathOff()">Turn off</button>' : '<button class="sm" onclick="secPath(false)">Choose my own</button>'}</div></div>
`;
}
const askPw = () => gprompt("Your password, to confirm:", "", {password: true});
window.secSetup = async () => { const pw = await askPw(); if (!pw) return; try { sec.setup = await api("/api/auth/2fa/setup", {method: "POST", body: JSON.stringify({password: pw})}); secLoad(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.secEnable = async () => { try { const r = await api("/api/auth/2fa/enable", {method: "POST", body: JSON.stringify({password: "", code: $("#sec-code").value})}); sec.setup = null; sec.codes = r.recovery_codes; toast("✓ Two-step sign-in is on", "ok"); secLoad(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.secOff = async () => { const pw = await askPw(); if (!pw) return; const code = await gprompt("A code from the app, or a recovery code:"); if (!code) return; try { await api("/api/auth/2fa/disable", {method: "POST", body: JSON.stringify({password: pw, code})}); toast("Two-step sign-in is off", "ok"); secLoad(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.secNewCodes = async () => { if (!await gconfirm("Make new recovery codes? The old ones stop working.", {danger: false})) return; const pw = await askPw(); if (!pw) return; try { sec.codes = (await api("/api/auth/recovery/regenerate", {method: "POST", body: JSON.stringify({password: pw})})).recovery_codes; secLoad(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.secDownloadCodes = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob(["Nikatel recovery codes (each works once)\n\n" + (sec.codes || []).join("\n") + "\n"], {type: "text/plain"})); a.download = "nikatel-recovery-codes.txt"; document.body.appendChild(a); a.click(); a.remove(); };
window.secPath = async random => { let path = ""; if (random) { const b = new Uint8Array(9); crypto.getRandomValues(b); path = "nk-" + [...b].map(x => x.toString(36).padStart(2, "0")).join("").slice(0, 14); } else { path = await gprompt("The secret path (8-64 letters, digits, - and _):"); if (!path) return; } const pw = await askPw(); if (!pw) return; try { const r = await api("/api/auth/entry-path", {method: "PUT", body: JSON.stringify({path, password: pw})}); toast("✓ The panel now lives at /" + r.path, "ok"); secLoad(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.secPathOff = async () => { if (!await gconfirm("Turn the secret path off? The panel answers on its normal address again.")) return; const pw = await askPw(); if (!pw) return; try { await api("/api/auth/entry-path", {method: "PUT", body: JSON.stringify({path: "", password: pw})}); toast("Secret path off", "ok"); secLoad(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.saveAi = e => put(e, "/api/settings/ai", formJson(e, [], ["enabled", "actions"]), "✓ AI settings saved");
window.testAi = async () => { toast("Asking Anthropic…"); try { const r = await api("/api/settings/ai/test", {method: "POST"}); toast("✓ The key works (" + r.answer + ")", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.forgetAi = async () => { if (!await gconfirm("Remove the saved API key?")) return; await api("/api/settings/ai/key", {method: "DELETE"}); toast("✓ Key removed", "ok"); refresh(); };
window.aboutLoad = async () => { const box = $("#about-box"); if (!box) return; try { const u = await api("/api/system/update"); box.innerHTML = `<div><span>Installed</span> <b>${esc(u.installed || "?")}</b> · <span>newest on GitHub</span> <b>${esc(u.newest || "?")}</b> · ${u.up_to_date ? '<b class="gw-ok">up to date</b>' : '<b class="err">update available</b>'}</div>`; const lg = $("#about-log"); if (u.last_log) { lg.textContent = u.last_log; lg.classList.remove("hidden"); } } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; } };
window.panelUpdate = async () => { if (!await gconfirm("Update the panel from GitHub now? It restarts for a few seconds; a version that fails its health check is rolled back.")) return; try { const r = await api("/api/system/update", {method: "POST"}); if (!r.ok) { toast("✕ " + r.error, "bad"); return; } toast("Updating… the page reloads in 40 s"); setTimeout(() => location.reload(), 40000); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* the look: theme, day/night, text size, animations - kept in this browser */
const LOOK = Object.assign({theme: "aurora", mode: "dark", size: "normal", anim: "on"}, JSON.parse(localStorage.getItem("nk.look") || "{}"));
function lookApply() { const h = document.documentElement; h.dataset.theme = LOOK.theme; h.dataset.size = LOOK.size; h.dataset.anim = LOOK.anim; const light = LOOK.mode === "light" || (LOOK.mode === "auto" && matchMedia("(prefers-color-scheme: light)").matches); h.dataset.mode = light ? "light" : "dark"; const b = $("#mode-btn"); if (b) b.textContent = light ? "🌙" : "☀"; }
window.lookSet = (k, v) => { LOOK[k] = v; localStorage.setItem("nk.look", JSON.stringify(LOOK)); lookApply(); };
window.lookReset = () => { localStorage.removeItem("nk.look"); Object.assign(LOOK, {theme: "aurora", mode: "dark", size: "normal", anim: "on"}); lookApply(); refresh(); };
window.toggleMode = () => lookSet("mode", document.documentElement.dataset.mode === "light" ? "dark" : "light");
lookApply();
/* settings are grouped in tabs; each tab has one line that says what it is for */
const SET_TABS = {
  account: ["👤 Account", "Your password, two-step sign-in, recovery codes, the secret entry path, and the settings the service was started with.", ["Security", "Password", "Effective settings"]],
  alerts: ["🔔 Alerts", "Where alerts go and when: Telegram, the flapping rules, maintenance windows and the daily report.", ["Telegram", "Alert rules", "Daily report", "Maintenance windows"]],
  backup: ["💾 Backups", "Encrypted backups of the panel: passphrase, schedule, the latest files.", ["Backups"]],
  probe: ["🛰 Probe", "The probe that keeps your configs alive and tests them from inside Iran: primary, standby, install on another server.", ["Probe"]],
  ai: ["🔧 Repairs & AI", "Automatic repairs of down tunnels, and the AI assistant inside Fix.", ["Repairs", "AI assistant"]],
  bot: ["🛒 Shop bot", "The server that runs the shop bot and how the panel reaches it.", ["Shop bot"]],
  look: ["🎨 Appearance", "Theme, day / night, text size, animations.", ["Appearance"]],
  update: ["⬆ Update", "Update this panel from GitHub with one click, and the tunnel cores on your servers.", ["About", "Core update"]],
};
function settingsTabs() {
  const cur = SET_TABS[state.stab] ? state.stab : "account";
  state.stab = cur;
  const bar = `<div class="tabs set-tabs">${Object.entries(SET_TABS).map(([k, v]) => `<button class="${k === cur ? "on" : ""}" onclick="setTab('${k}')">${v[0]}</button>`).join("")}</div><div class="set-desc">${SET_TABS[cur][1]}</div>`;
  $("#page").insertAdjacentHTML("afterbegin", bar);
  const grid = $("#page .grid2"), byName = {};
  grid.querySelectorAll(":scope > .card").forEach(c => { const h = c.querySelector("h3"); byName[h ? h.childNodes[0].textContent.trim() : ""] = c; c.classList.add("hidden"); });
  const names = SET_TABS[cur][2].filter(n => byName[n]);
  let show = names;
  if (names.length > 1) {   // several cards in a tab: a second row of tabs, one card at a time
    const pick = names.includes(state.sstab) ? state.sstab : names[0];
    state.sstab = pick; show = [pick];
    $("#page .set-desc").insertAdjacentHTML("afterend", `<div class="tabs set-sub">${names.map(n => `<button class="${n === pick ? "on" : ""}" onclick="state.sstab='${esc(n)}';settingsTabs.only()">${esc(n)}</button>`).join("")}</div>`);
  }
  show.forEach(n => { const c = byName[n]; c.classList.remove("hidden"); grid.appendChild(c); });
  grid.classList.add("one");
}
settingsTabs.only = () => {   // switch the card without touching the server or the forms: hide / show and redraw the small tab row
  const grid = $("#page .grid2"); if (!grid) return refresh();
  const old = $("#page .set-sub"); if (old) old.remove();
  const byName = {};
  grid.querySelectorAll(":scope > .card").forEach(c => { const h = c.querySelector("h3"); byName[h ? h.childNodes[0].textContent.trim() : ""] = c; c.classList.add("hidden"); });
  const names = SET_TABS[state.stab][2].filter(n => byName[n]), pick = names.includes(state.sstab) ? state.sstab : names[0];
  $("#page .set-desc").insertAdjacentHTML("afterend", `<div class="tabs set-sub">${names.map(n => `<button class="${n === pick ? "on" : ""}" onclick="state.sstab='${esc(n)}';settingsTabs.only()">${esc(n)}</button>`).join("")}</div>`);
  byName[pick].classList.remove("hidden");
  if (pick === "About") aboutLoad(); if (pick === "Core update") enginesLoad();
};
window.setTab = k => { state.stab = k; state.sstab = null; localStorage.setItem("nk.stab", k); refresh(); };
state.stab = localStorage.getItem("nk.stab") || "account";
const formJson = (e, nums = [], bools = []) => { const f = new FormData(e.target), o = {}; for (const [k, v] of f.entries()) o[k] = v; nums.forEach(k => o[k] = +o[k]); bools.forEach(k => o[k] = f.get(k) === "on"); return o; };
const put = async (e, path, body, okText) => { e.preventDefault(); try { await api(path, {method: "PUT", body: JSON.stringify(body)}); toast(okText, "ok"); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.saveTelegram = e => put(e, "/api/settings/telegram", formJson(e, [], ["enabled"]), "✓ Telegram saved");
window.testTelegram = async () => { toast("Sending…"); try { await api("/api/settings/telegram/test", {method: "POST"}); toast("✓ Test message sent", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.saveRules = e => put(e, "/api/settings/alerts", formJson(e, ["tunnel_fails", "server_fails", "up_stable_seconds", "remind_hours"]), "✓ Rules saved");
window.muteRef = async (ref, on) => { try { await api(`/api/settings/alerts/mute?ref=${encodeURIComponent(ref)}&muted=${on}`, {method: "POST"}); toast(on ? "✓ Muted " + ref : "✓ Unmuted " + ref, "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.addWindow = async e => { e.preventDefault(); const f = new FormData(e.target); const b = {start: Math.floor(new Date(f.get("start") + "Z").getTime() / 1000), end: Math.floor(new Date(f.get("end") + "Z").getTime() / 1000), note: f.get("note")}; try { await api("/api/maintenance", {method: "POST", body: JSON.stringify(b)}); toast("✓ Window added", "ok"); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.delWindow = async id => { try { await api(`/api/maintenance/${id}`, {method: "DELETE"}); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.saveProbe = e => { const f = new FormData(e.target); const standby = f.get("standby").split("\n").map(l => l.trim()).filter(Boolean).map(l => { const [name, api_url, api_token] = l.split(",").map(x => (x || "").trim()); return {name, api_url, api_token: api_token || ""}; }); return put(e, "/api/settings/probe", {api_url: f.get("api_url").trim(), api_token: f.get("api_token"), server_id: +f.get("server_id") || 0, standby}, "✓ Probe settings saved"); };
window.testProbe = async () => { toast("Asking the probe…"); try { const r = await api("/api/settings/probe/test", {method: "POST"}); toast(`✓ ${r.probe.active} answers · ${r.configs} configs`, "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.installProbe = async e => { e.preventDefault(); const sid = +new FormData(e.target).get("server_id"); if (!await gconfirm("Install the probe on this Iran server? It copies the probe code and Xray (about 65 MB) over SSH and starts three services. Nothing else on the server is touched.")) return false; toast("Installing… (2–5 minutes)"); try { const r = await api(`/api/settings/probe/install?server_id=${sid}`, {method: "POST"}); toast(`✓ Probe on ${r.server} registered as ${r.role}`, "ok"); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.saveShopbot = e => { const f = new FormData(e.target); return put(e, "/api/settings/shopbot", {host: f.get("host").trim(), ssh_user: f.get("ssh_user").trim() || "root", ssh_port: +f.get("ssh_port") || 22, dir: f.get("dir").trim(), env: f.get("env").trim(), public_url: f.get("public_url").trim()}, "✓ Shop bot settings saved"); };
window.saveRepair = e => put(e, "/api/settings/repair", formJson(e, ["after_seconds"], ["auto"]), "✓ Repair settings saved");
window.saveReport = e => put(e, "/api/settings/report", formJson(e, ["hour", "tz_offset_minutes"], ["enabled"]), "✓ Report settings saved");
window.previewReport = async () => { const r = await api("/api/report/preview"); const p = $("#report-prev"); p.textContent = r.text; p.classList.remove("hidden"); };
window.sendReport = async () => { toast("Sending…"); try { await api("/api/report/send", {method: "POST"}); toast("✓ Report sent (if Telegram is configured)", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.savePass = async e => { e.preventDefault(); try { await api("/api/settings/backup/passphrase", {method: "POST", body: JSON.stringify({passphrase: new FormData(e.target).get("passphrase")})}); toast("✓ Passphrase set", "ok"); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.saveBackup = e => put(e, "/api/settings/backup", formJson(e, ["hour", "keep_days"], ["enabled", "send_telegram"]), "✓ Backup settings saved");
window.backupNow = async () => { toast("Backing up…"); try { const r = await api("/api/backup/now", {method: "POST"}); toast("✓ " + r.file + (r.sent ? " · sent" : ""), "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.changePassword = async e => {
  e.preventDefault();
  const f = new FormData(e.target);
  if (f.get("new") !== f.get("again")) { toast("✕ the two new passwords differ", "bad"); return false; }
  try { await api("/api/auth/password", {method: "POST", body: JSON.stringify({current: f.get("current"), new: f.get("new"), logout_others: f.get("logout_others") === "on"})}); toast("✓ Password changed" + (f.get("logout_others") === "on" ? " - every other session was signed out" : ""), "ok"); e.target.reset(); }
  catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};

/* ---- boot */
(async () => {
  try { await api("/api/auth/me"); showApp(); go(state.page); } catch (e) { showLogin(); }
})();
