/* Nikatel panel front-end: plain JS, one function per page, data from /api. */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const ago = ts => { if (!ts) return "—"; const d = Math.max(0, Date.now() / 1000 - ts); return d < 60 ? Math.round(d) + "s" : d < 3600 ? Math.round(d / 60) + "m" : d < 86400 ? Math.round(d / 3600) + "h" : Math.round(d / 86400) + "d"; };
const when = ts => ts ? new Date(ts * 1000).toLocaleString(undefined, {hour12: false}) : "—";
const mbps = v => v == null ? "—" : v >= 1000 ? (v / 1000).toFixed(1) + " Gb/s" : v.toFixed(1) + " Mb/s";
const pct = (v, warn = 80, bad = 92) => v == null ? "—" : `<span class="bar"><i class="${v >= bad ? "bad" : v >= warn ? "warn" : ""}" style="width:${Math.min(100, v)}%"></i></span>${v}%`;

/* Reading a page's data (GET) is tied to the page: leaving the page cancels what it is still waiting for, and a cancelled read never answers,
   so a slow page can no longer finish later and paint itself over the page the user moved to. Actions (POST/PUT/DELETE) and the progress polls are never cancelled. */
let navCtl = new AbortController();
const KEEP_ALIVE = /^\/api\/(actions\/\d+|tunnel-test\/)/;
/* "while loading tunnels" / "while saving settings": what a failed request was doing, from its address */
const API_NAMES = {tunnels: "tunnels", servers: "servers", configs: "configs", panels: "panels", shopbot: "the shop bot", settings: "settings", actions: "repairs", autopilot: "smart repair", events: "events", overview: "the dashboard", logs: "the logs", jobs: "the jobs", gateway: "the gateway", nodes: "the server tools", subscriptions: "subscriptions", engines: "the core versions", system: "the update", auth: "sign-in", alerts: "alerts", report: "the report", backup: "the backup", datacentres: "the data centres", radar: "the radar", maintenance: "maintenance windows", ai: "the AI"};
function apiWhat(path, opts) {
  const seg = String(path).split("?")[0].replace(/^\/api\//, "").split("/").filter(Boolean), name = API_NAMES[seg[0]] || ("/" + seg.join("/")), m = String((opts && opts.method) || "GET").toUpperCase();
  return m === "GET" ? "while loading " + name : m === "DELETE" ? "while deleting in " + name : "while saving in " + name;
}
async function api(path, opts = {}) {
  const ctl = (!opts.method || String(opts.method).toUpperCase() === "GET") && !KEEP_ALIVE.test(path) ? navCtl : null;
  let r, body = null;
  try {
    r = await fetch(path, Object.assign({headers: {"Content-Type": "application/json"}}, opts, ctl ? {signal: ctl.signal} : {}));
    try { body = await r.json(); } catch (e) { if (e.name === "AbortError") throw e; /* no json body */ }
  } catch (e) { if (e.name === "AbortError") return new Promise(() => {}); e.message = e.message + " — " + apiWhat(path, opts); throw e; }
  if (r.status === 401) { showLogin(); throw new Error("not logged in"); }
  if (!r.ok) {
    const rid = r.headers.get("X-Request-Id");
    let msg = (body && (body.detail || body.error)) || "HTTP " + r.status;
    if (Array.isArray(msg)) msg = msg.map(x => (x.loc || []).slice(-1)[0] + ": " + x.msg).join("; ");
    const bare = /^HTTP \d+$/.test(msg);
    if (bare && [502, 503, 504].includes(r.status)) msg = "The panel did not answer (HTTP " + r.status + "): it is most likely restarting after an update. Wait about 10 seconds and try again";   // the server said nothing: name what was being done, so the message is never just a number
    throw new Error(msg + (bare || /^The panel did not answer/.test(msg) ? " — " + apiWhat(path, opts) : "") + (rid && r.status >= 500 ? " · id " + rid : ""));
  }
  return body;
}

/* Messages of the panel go to a status bar at the bottom (not into a pop-up in the corner): bright for a few seconds, then quiet but still readable. Click it for the last ones. */
let toastT;
const MSGS = [];
function toast(text, cls) {
  const bar = $("#statusbar"); if (!bar) return;
  MSGS.unshift({t: Date.now(), text: String(text), cls: cls || ""}); MSGS.length = Math.min(MSGS.length, 40);
  bar.className = "statusbar live " + (cls || "");
  $(".sb-text", bar).textContent = String(text); $(".sb-time", bar).textContent = new Date().toLocaleTimeString(undefined, {hour12: false});
  clearTimeout(toastT); toastT = setTimeout(() => bar.classList.remove("live"), 7000);
}
window.sbHistory = () => showModal("<b>Messages</b>", MSGS.length ? MSGS.map(m => `<div class="msg-row ${m.cls === "bad" ? "bad" : m.cls === "ok" ? "ok" : "info"}"><time>${new Date(m.t).toLocaleTimeString(undefined, {hour12: false})}</time><i></i><span>${esc(m.text)}</span></div>`).join("") : '<div class="empty">No message yet.</div>');

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
function showApp() { $("#login").classList.add("hidden"); $("#app").classList.remove("hidden"); api("/api/settings/nodes").then(r => { window.NODE_CERT = r.marzban_cert || ""; }).catch(() => {}); }

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
api("/api/health").then(h => { const v = $("#version"); if (v && h && h.version && !v.textContent) v.textContent = "v" + h.version; }).catch(() => {});
$("#logout").onclick = async () => {
  /* the way out: the panel folds away in 3D, a light sweeps over it, then the sign-in page rises */
  const quiet = document.documentElement.dataset.anim === "off" || matchMedia("(prefers-reduced-motion: reduce)").matches, root = document.documentElement;
  if (!quiet) root.classList.add("bye");
  await Promise.all([api("/api/auth/logout", {method: "POST"}).catch(() => {}), new Promise(r => setTimeout(r, quiet ? 0 : 900))]);
  showLogin(); root.classList.remove("bye");
};

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
window.setSub = (page, k) => { if (state.sub[page] !== k) { state.stab = "account"; state.sstab = null; if (typeof pn !== "undefined") { pn.tab = "users"; pn.uoff = 0; } state.mtab = "all"; } state.sub[page] = k; localStorage.setItem("nk.sub", JSON.stringify(state.sub)); state.open = null; state.edit = null; state.build = null; state.q = ""; refresh(); };
const TOP_TABS = () => { Object.assign(sb, {tab: "overview", tool: "miniapp", payState: "confirm", payPage: 0}); localStorage.setItem("nk.sb.tab", "overview"); state.stab = "account"; state.sstab = null; localStorage.setItem("nk.stab", "account"); if (typeof pn !== "undefined") { pn.tab = "users"; pn.uoff = 0; } state.mtab = "all"; state.wtab = "overview"; };
function go(page) {
  let aliased = false;
  if (NAV_ALIAS[page]) {   aliased = true;               // an old page name: open the page that holds it, on the right sub-tab
    const target = NAV_ALIAS[page];
    if ((SUB[target] || []).some(x => x[0] === page)) state.sub[target] = page;
    page = target;
  }
  if (!PAGES[page]) page = "dashboard";
  if (state.page !== page && !aliased) { delete state.sub[page]; TOP_TABS(); }   // a tab you come back to always opens on its first sub-tab (callers that want another one set it right after go())
  if (state.page !== page) {   // leaving a page: drop what it still waits for, start the new one at the top and show at once that it is loading (the old page never stays on screen)
    navCtl.abort(); navCtl = new AbortController(); navSeq++; retries = 0; clearTimeout(retryT); state.resetScroll = true;
    const m = $(".main"); if (m) m.scrollTop = 0;
    const pg = $("#page"); if (pg) { pg.innerHTML = loadingBox(page); pg.classList.remove("enter"); void pg.offsetWidth; pg.classList.add("enter"); }
    hideModal();
  }   // what the page we leave still waits for is dropped; the new page starts at the top
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


/* A refresh rebuilds the page from fresh data. Every scrolling box keeps its position through it (the page, a window, the lists inside the dashboard, the logs),
   so nothing jumps back to the top when new data arrives. Opening another page starts at the top. */
const SCROLLERS = [".main", ".modal-body", ".act-list", ".pb-list", ".hl-list", ".tt-log", ".sub-nodes", ".ob-inbounds", ".fix"];
const scrollSnapshot = () => SCROLLERS.map(sel => { const el = $(sel); return el ? [sel, el.scrollTop, el.scrollTop + el.clientHeight >= el.scrollHeight - 12 && sel === ".tt-log"] : null; }).filter(Boolean);
const scrollRestore = snap => snap.forEach(([sel, top, atEnd]) => { const el = $(sel); if (el) el.scrollTop = atEnd ? el.scrollHeight : top; });
/* The page is painted only when its HTML really changed: a refresh that brings the same data touches nothing (no flicker, no lost hover or selection).
   Pages assign `$("#page").innerHTML` as before; this setter on the element does the comparison. */
(() => {
  const pg = $("#page"), d = Object.getOwnPropertyDescriptor(Element.prototype, "innerHTML");
  if (!pg || !d) return;
  let last = null, lastDom = null;
  // the same HTML again is skipped (no flicker) - but only while the page has not changed its own DOM since (Settings, Tools, lazy boxes do)
  Object.defineProperty(pg, "innerHTML", {get() { return d.get.call(pg); }, set(v) { if (v === last && lastDom !== null && d.get.call(pg) === lastDom && pg.childElementCount && !pg.querySelector(".loading, .page-err")) return; last = v; d.set.call(pg, v); lastDom = d.get.call(pg); }});
})();
let navSeq = 0, retryT = null, retries = 0;
const loadingBox = page => `<div class="loading"><i class="spin"></i><span>Loading ${esc((INFO[page] || ["…"])[0])}…</span></div>`;
const errorBox = (msg, transient) => `<div class="page-err"><b>${transient ? "The panel does not answer right now" : "This page could not be loaded"}</b><span>${esc(msg)}</span><small class="mut">${transient ? "It is probably restarting after an update; trying again by itself…" : ""}</small><button class="sm" onclick="retries=0;refresh()">Try again</button></div>`;
async function refresh() {
  const snap = state.resetScroll ? [] : scrollSnapshot();
  state.resetScroll = false;
  const seq = navSeq;
  try {
    await PAGES[state.page]();
    if (seq !== navSeq) return;                 // another page was opened meanwhile: this one is history
    if (retries) toast("✓ Connected again", "ok");
    retries = 0; clearTimeout(retryT);
    scrollRestore(snap);
    $("#updated").textContent = "updated " + new Date().toLocaleTimeString(undefined, {hour12: false});
  } catch (e) {
    if (e.message === "not logged in" || seq !== navSeq) return;
    const transient = /HTTP 50\d|Failed to fetch|NetworkError|Load failed|network/i.test(e.message);
    const pg = $("#page");
    if (pg && (!pg.childElementCount || pg.querySelector(".loading, .page-err"))) pg.innerHTML = errorBox(e.message, transient);   // nothing of this page is on screen yet: say so instead of leaving the old page
    toast("✕ " + e.message, "bad");
    if (transient) { clearTimeout(retryT); retryT = setTimeout(() => { if (seq === navSeq) refresh(); }, Math.min(8000, 1500 * 2 ** Math.min(retries++, 3))); }
  }
}
setInterval(() => { if (!document.hidden && !$("#app").classList.contains("hidden") && !state.edit && !state.open && !state.build && !state.detect && !cf.open && !cf.edit && !cf.add && !cf.sub && !cf.subEdit && !document.activeElement.matches("input, textarea, select")) refresh(); }, 15000);
/* the glass window: one thing opened from a row or a card; closed with ✕, Esc or a click outside */
function showModal(title, body, cls = "", acts = "") { let m = $("#modal"); if (!m) { m = document.createElement("div"); m.id = "modal"; document.body.appendChild(m); } m.innerHTML = `<div class="overlay" onmousedown="if(event.target===this)closeModal()"><div class="modal ${cls}"><div class="modal-head">${title}<span class="sp"></span>${acts}<button class="sm min-btn" onclick="minModal()" title="Minimise: it keeps running; click its chip at the bottom to come back">—</button><button class="sm" onclick="closeModal()">✕ Close</button></div><div class="modal-body">${body}</div></div></div>`; }
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
  const [o, ev, cfg, srv, tl] = await Promise.all([api("/api/overview"), api("/api/events?limit=300"), api("/api/configs").catch(() => ({configs: []})), api("/api/servers").catch(() => []), api("/api/tunnels").catch(() => [])]);
  state.tunnelsCache = tl; state.cfgCache = cfg.configs;
  $("#version").textContent = "v" + o.version; $("#ro-badge").classList.toggle("hidden", !o.read_only);
  $("#n-servers").textContent = o.servers.total; $("#n-tunnels").textContent = o.tunnels.total; $("#n-configs").textContent = cfg.configs.length;
  const cUp = cfg.configs.filter(c => c.status === "up").length, foreign = srv.filter(s => s.role === "foreign"), checked = foreign.filter(s => (s.extra || {}).iran_filter);
  const clean = checked.filter(s => s.extra.iran_filter.level === "clean"), bad = checked.filter(s => ["filtered", "blocked"].includes(s.extra.iran_filter.level));
  const dot = cls => `<i class="dash-dot ${cls}"></i>`;
  const hrow = (name, sub, val, cls) => `<div class="dash-row">${dot(cls)}<div class="dash-name">${name}<small>${sub}</small></div><b class="${cls === "red" ? "err" : cls === "green" ? "gw-ok" : ""}">${val}</b></div>`;
  $("#page").innerHTML = `
    <div class="dash-cols"><div class="card health"><h3>Infrastructure health</h3><div class="hl-list">
      ${hrow("Iran ↔ Foreign tunnels", o.tunnels.down ? o.tunnels.down + " down" : (o.tunnels.degraded ? o.tunnels.degraded + " degraded" : "all carrying users"), `${o.tunnels.up} / ${o.tunnels.total}`, o.tunnels.down ? "red" : o.tunnels.degraded ? "yellow" : "green")}
      ${hrow("SSH connected nodes", o.servers.offline ? o.servers.offline + " offline" : o.servers.iran + " Iran · " + o.servers.foreign + " foreign", `${o.servers.online} / ${o.servers.total}`, o.servers.offline ? "red" : "green")}
      ${hrow("VLESS / VMess connectivity", cfg.configs.length - cUp ? (cfg.configs.length - cUp) + " down on the probe" : "all answer from inside Iran", `${cUp} / ${cfg.configs.length}`, cfg.configs.length && cUp === cfg.configs.length ? "green" : cUp ? "yellow" : cfg.configs.length ? "red" : "")}
      ${hrow("Reachable from Iran", "clean filter check", `${clean.length} / ${foreign.length}`, bad.length ? "red" : clean.length === foreign.length && foreign.length ? "green" : "yellow")}
      ${hrow("Last filter checks", checked.length ? checked.sort((a, b) => b.extra.iran_filter.checked_at - a.extra.iran_filter.checked_at).slice(0, 3).map(s => esc(s.name) + (s.extra.iran_filter.level === "clean" ? " ✓" : " ✕")).join(" · ") : "none yet", `${bad.length} filtered`, bad.length ? "red" : checked.length ? "green" : "")}
      ${hrow("Background jobs", o.jobs.failing.length ? "failing: " + esc(o.jobs.failing.join(", ")) : o.jobs.stale.length ? "stale: " + esc(o.jobs.stale.join(", ")) : "all running", `${o.jobs.total}`, o.jobs.failing.length ? "red" : o.jobs.stale.length ? "yellow" : "green")}</div></div>
    ${problemsCard(o, tl, srv, cfg.configs, bad)}
    <div class="card act ${state.actBig ? "big" : ""}"><h3>Activity <span class="sp"></span><a class="mut" onclick="actBig()" title="${state.actBig ? "Same height as the health box" : "Taller log box"}">${state.actBig ? "⤡ Shrink" : "⤢ Expand"}</a></h3>${actHead(ev.events)}<div class="act-list">${eventsHtml(actFilter(ev.events))}</div></div></div>`;
};
/* ---- the Problems box: only what is wrong right now (read from the live state, so a solved problem leaves it by itself), each with its way to fix it */
function problemsCard(o, tl, srv, configs, filtered) {
  const rows = [];
  const row = (lvl, title, sub, btn) => rows.push(`<div class="pb-row"><i class="dash-dot ${lvl === "bad" ? "red" : "yellow"}"></i><div class="pb-t"><b>${title}</b><small>${esc(String(sub || "").slice(0, 110))}</small></div>${btn || ""}</div>`);
  const fix = (fn, id) => `<button class="sm pri" onclick="${fn}(${id}${fn === "apDiagnose" ? ", {auto: true}" : ""})" title="Finds the cause and repairs it by itself where that is possible">✨ Fix with AI</button>`;
  tl.filter(t => t.enabled !== false && (t.state === "down" || t.state === "degraded")).sort((a, b) => (a.state === "down" ? 0 : 1) - (b.state === "down" ? 0 : 1))
    .forEach(t => row(t.state === "down" ? "bad" : "warn", `Tunnel ${esc(t.name)} <span class="mut">${esc(t.state)}</span>`, t.last_error || `${t.iran_name} ⇄ ${t.foreign_name}`, fix("apDiagnose", t.id)));
  srv.filter(srvProblem).forEach(s => { const f = s.facts || {}; row(s.online === false ? "bad" : "warn", `Server ${esc(s.name)} <span class="mut">${s.online === false ? "offline" : "overloaded"}</span>`, s.online === false ? (s.last_error || "does not answer over SSH") : `CPU ${f.cpu ?? "—"} % · RAM ${f.ram ?? "—"} % · disk ${f.disk ?? "—"} %`, `<button class="sm" onclick="checkServer(${s.id})" title="Ask the server now">Check now</button>`); });
  const down = configs.filter(c => c.status === "down");
  down.slice(0, 5).forEach(c => row("bad", `Config ${esc(c.name)} <span class="mut">down</span>`, c.last_error || "the probe gets no answer through it", fix("cfDiagnose", c.id)));
  if (down.length > 5) rows.push(`<div class="pb-more"><a onclick="go('configs')">and ${down.length - 5} more configs are down → open the list</a></div>`);
  filtered.forEach(s => row("warn", `Server ${esc(s.name)} <span class="mut">filtered from Iran</span>`, "its address cannot be reached from inside Iran", `<button class="sm" onclick="srvFilter(${s.id})" title="Check again from the Iran servers">Re-check</button>`));
  (o.jobs.failing || []).forEach(n => row("bad", `Job ${esc(n)} <span class="mut">failing</span>`, "a background job keeps failing", `<button class="sm" onclick="runJob('${esc(n)}')">Run now</button>`));
  (o.jobs.stale || []).forEach(n => row("warn", `Job ${esc(n)} <span class="mut">stale</span>`, "a background job did not run on time", `<button class="sm" onclick="runJob('${esc(n)}')">Run now</button>`));
  const n = rows.length - (down.length > 5 ? 1 : 0) + Math.max(0, down.length - 5);
  const brokenT = tl.filter(t => t.enabled !== false && (t.state === "down" || t.state === "degraded")).length;
  return `<div class="card problems"><h3>Problems <span class="sp"></span>${brokenT ? `<button class="sm pri" onclick="go('tunnels');setSub('tunnels','repairs');setTimeout(apFixAll, 600)" title="Repair every broken tunnel, one after the other">✨ Fix all</button>` : ""}<span class="pill ${n ? "err" : "ok"}">${n ? n + " now" : "all clear"}</span></h3><div class="pb-list">${rows.join("") || '<div class="empty" style="padding:18px 0">Nothing is wrong right now.</div>'}</div></div>`;
}
/* ---- the activity box: categories and problems only */
const EV_CATS = {tunnels: "Tunnels", configs: "Configs", servers: "Servers", actions: "Repairs", alerts: "Alerts", system: "System", jobs: "Jobs", panels: "Panels", gateway: "Gateway"};
state.actCat = localStorage.getItem("nk.actcat") || "all"; state.actProb = localStorage.getItem("nk.actprob") === "1";
const evProblem = e => e.level === "bad" || e.level === "warn";
const actFilter = list => list.filter(e => (state.actCat === "all" || e.domain === state.actCat) && (!state.actProb || evProblem(e)));
function actHead(list) {
  const cnt = {}; list.forEach(e => { cnt[e.domain] = (cnt[e.domain] || 0) + 1; });
  const cats = Object.keys(cnt).sort((a, b) => cnt[b] - cnt[a]), prob = list.filter(evProblem).length;
  return `<div class="act-cats"><button class="${state.actCat === "all" ? "on" : ""}" onclick="actSet('all')">All <small>${list.length}</small></button>${cats.map(c => `<button class="${state.actCat === c ? "on" : ""}" onclick="actSet('${esc(c)}')">${esc(EV_CATS[c] || c.charAt(0).toUpperCase() + c.slice(1))} <small>${cnt[c]}</small></button>`).join("")}<span class="sp"></span><button class="${state.actProb ? "on" : ""}" onclick="actProb()" title="Only warnings and failures">⚠ Problems <small>${prob}</small></button></div>`;
}
window.actSet = c => { state.actCat = c; localStorage.setItem("nk.actcat", c); refresh(); };
window.actProb = () => { state.actProb = !state.actProb; localStorage.setItem("nk.actprob", state.actProb ? "1" : "0"); refresh(); };
window.cfDiagnose = async id => {
  showModal("<b>✨ Diagnosis</b>", '<div class="mut" style="padding:20px;text-align:center">Looking at the config, the others behind the same address and its tunnel…</div>', "wide");
  try {
    const d = await api(`/api/autopilot/diagnose-config/${id}`), tn = d.tunnel_info;
    showModal(`<span class="st ${d.status === "up" ? "up" : "down"}"></span><b>${esc(d.name)}</b> <span class="mut">${esc(d.address)}${d.port ? ":" + d.port : ""}</span>`, `
      <div class="ibox"><h5><i>🩺</i>Cause</h5><div style="padding:6px 0;font-size:13.5px"><b class="${d.status === "up" ? "gw-ok" : "err"}">${esc(d.summary)}</b></div></div>
      ${d.ai ? `<div class="ibox" style="margin-top:10px"><h5><i>✨</i>AI</h5><div style="padding:6px 0;font-size:13px">${esc(d.ai)}</div></div>` : ""}
      <div class="ibox" style="margin-top:10px"><h5><i>🔎</i>What was checked</h5>${d.findings.map(f => irow(esc(f.text), f.ok ? '<b class="gw-ok">✓</b>' : '<b class="err">✕</b>')).join("")}</div>
      <div class="wgroup" style="margin-top:12px"><h4>What can be done</h4><div class="wtools">
        ${tn && tn.state !== "up" ? `<a class="wtool" onclick="apDiagnose(${tn.id}, {auto: true})"><i>🛠</i><b>Fix the tunnel ${esc(tn.name)}</b><small>diagnose it and repair it by itself</small></a>` : ""}</div>
        ${tn && tn.state !== "up" ? "" : '<div class="mut" style="padding:6px 0;font-size:12.5px">Nothing on your servers can change this: when an address is cut from inside Iran, the config needs another address (another foreign server or a CDN).</div>'}</div>`, "wide");
  } catch (e) { showModal("<b>✨ Diagnosis</b>", `<div class="err">${esc(e.message)}</div>`, "wide"); }
};
function eventsHtml(list, open) {
  if (!list.length) return '<div class="empty">Nothing happened yet.</div>';
  return list.map(e => `<div class="ev ${e.level}${open ? " click" : ""}"${open ? ` onclick="evOpen(${e.id})" title="Click for details and Fix with AI"` : ""}><time title="${when(e.ts)}">${ago(e.ts)} ago</time><i></i><small>${esc(e.domain)}</small><span>${esc(e.text)}</span></div>`).join("");
}

/* ---- servers */
window.NODE_CERT = "";   // the default Marzban certificate (Settings of the panel, loaded after sign-in)
const SERVER_FORM = (s, role) => `<form class="frm" onsubmit="return saveServer(event, ${s ? s.id : "null"})">
    <label>Name<input name="name" required pattern="[A-Za-z0-9][A-Za-z0-9._-]*" value="${esc(s ? s.name : "")}" placeholder="ir7"></label>
    <label>Host / IP<input name="host" required pattern="[A-Za-z0-9][A-Za-z0-9.\\-]*" value="${esc(s ? s.host : "")}" placeholder="203.0.113.10"></label>
    <label>Role<select name="role">${["iran", "foreign"].map(r => `<option ${(s ? s.role : role) === r ? "selected" : ""}>${r}</option>`).join("")}</select></label>
    <label>SSH user<input name="ssh_user" value="${esc(s ? s.ssh_user : "root")}"></label>
    <label>SSH port<input name="ssh_port" type="number" min="1" max="65535" value="${s ? s.ssh_port : 22}"></label>
    ${s ? `<input type="hidden" name="provider" value="${esc(s.provider)}">` : ""}
    <label>SSH password <small>(optional)</small><input name="password" type="password" autocomplete="new-password" placeholder="${s ? "fill it to (re)install the panel's key now" : "empty = the panel's key is already on the server"}"></label>
    <label class="chk"><input name="enabled" type="checkbox" ${!s || s.enabled ? "checked" : ""}> enabled (checked by the panel)</label>
    ${`<div class="srv-extra"><b>${s ? "Install after saving" : "Install after adding"}</b> <small class="mut">(optional, needs root; the server is ${s ? "saved" : "added"} first, then these run and tell what they did)</small>
      <label class="chk"><input name="x_bt" type="checkbox"> Block public torrents <small class="mut">(iptables DROP rules for tracker addresses, refreshed daily - Heclalava/blockpublictorrent-iptables)</small></label>
      <label class="chk"><input name="x_node" type="checkbox" onchange="this.closest('.srv-extra').querySelectorAll('.x-cert').forEach(e => e.classList.toggle('hidden', !this.checked))"> Marzban node <small class="mut">(Docker + the official Gozargah installer; ports 62050 / 62051)</small></label>
      <label class="x-cert hidden">Certificate of the Marzban panel <small>(Marzban → Nodes → Add node → the certificate to copy; paste all of it)</small><textarea name="x_cert" rows="5" spellcheck="false" placeholder="-----BEGIN CERTIFICATE-----&#10;…&#10;-----END CERTIFICATE-----">${esc(window.NODE_CERT || "")}</textarea></label>
      <label class="chk x-cert hidden"><input name="x_cert_save" type="checkbox" ${window.NODE_CERT ? "" : "checked"}> remember this certificate as the default for the next servers</label></div>`}
    <div class="frm-act"><button class="pri" type="submit">${s ? "Save" : "Add server"}</button>
      ${s ? `<span class="sp"></span><button type="button" onclick="state.edit = null; refresh()" title="Back to the server's details without saving">← Back</button>` : ""}</div>
    <small class="mut">${s ? "Changing a server here only changes how the panel looks at it; a password or an install below acts on the server itself." : "With a password the panel logs in once, installs its own key and forgets the password. Without a password the panel's key must already be in the server's <code>authorized_keys</code> (Settings shows the key)."}</small>
  </form>`;

/* a server the panel cannot log in to: not online, and its last error is about the login (password / key), or it never answered */
/* severity colours of the rows: red = fully down (or a server that cannot be reached / is critical), yellow = everything else that is wrong - packet loss of any size, degraded, slow, loaded */
const srvSev = s => { if (!s.enabled) return ""; const f = s.facts || {}; if (s.online === false || noLogin(s) || (f.cpu || 0) >= 92 || (f.ram || 0) >= 92 || (f.disk || 0) >= 95) return "sev-red"; return (f.cpu || 0) >= 80 || (f.ram || 0) >= 85 || (f.disk || 0) >= 85 || s.online == null ? "sev-yellow" : ""; };
const SHOW_LOSS = 20;   // packet loss under this is normal for the Iran-abroad paths: not shown, not coloured
const tunSev = t => { const d = t.detail || {}, loss = d.loss_pct || 0; if (t.state === "down") return "sev-red"; return t.state === "unknown" || t.state === "starting" || loss >= SHOW_LOSS ? "sev-yellow" : ""; };
/* the panel is connecting to a server or installing something on it: the list says so instead of a dash (and looks again every 3 s) */
const srvBusy = s => { const b = (s.extra || {}).busy; return b && b.what && Date.now() / 1000 - b.at < 900 ? b : null; };
const noLogin = s => s.enabled && s.online !== true && /permission denied|publickey|login failed|authentication|wrong user|password/i.test(s.last_error || "");
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
      return g.ok ? `<b class="gw-ok" style="cursor:pointer" title="${esc(tip)}" onclick="event.stopPropagation();greCheck(${s.id})">GRE ✓</b> <small class="mut">${g.links}</small>` : g.links ? `<b class="err" style="cursor:pointer" title="${esc(tip)}" onclick="event.stopPropagation();greCheck(${s.id})">GRE ✕</b>` : `<span style="cursor:pointer" title="${esc(tip)}" onclick="event.stopPropagation();greCheck(${s.id})"><b class="gw-ok">GRE ✓</b> <small class="mut">0</small></span>`;
    }
    const up = ts.filter(t => t.state === "up").length, bad = ts.filter(t => t.state === "down").length;
    const cls = bad === ts.length ? "err" : bad || up < ts.length ? "warn" : "gw-ok", txt = bad === ts.length ? "GRE ✕" : bad ? "GRE ✓/✕" : up === ts.length ? "GRE ✓" : "GRE ~";
    return `<b class="${cls}" title="${esc(ts.map(t => t.name + ": " + t.state).join(", "))}">${txt}</b> <small class="mut">${ts.length}</small>`;
  };
  // Marzban / PasarGuard nodes that sit on this server's address: "Panel ✓ name" chips next to the status
  const pnl = await api("/api/panels").catch(() => ({panels: []}));
  const byIp = {};
  (pnl.panels || []).forEach(p => (p.nodes || []).forEach(n => { const ip = String(n.address || "").trim(); if (ip) (byIp[ip] = byIp[ip] || []).push({panel: p.name, status: n.status, name: n.name}); }));
  const chips = s => (byIp[s.host] || []).map(x => { const ok = x.status === "connected"; return `<span class="mchip ${ok ? "ok" : "bad"}" title="Node ${esc(x.name)} in panel ${esc(x.panel)}: ${esc(x.status || "?")}">${esc(x.panel)}</span>`; }).join("");
  const pbar = (v, warn, bad) => v == null ? '<span class="mut">—</span>' : `<div class="pbar"><span class="bar"><i class="${v >= bad ? "bad" : v >= warn ? "warn" : ""}" style="width:${Math.min(100, v)}%"></i></span><b>${v}%</b></div>`;
  const foreignTab = state.tab === "foreign";
  if (list.some(srvBusy)) { clearTimeout(window.__busyT); window.__busyT = setTimeout(() => { if (state.page === "nodes") refresh(); }, 3000); }
  const row = s => {   // the old panel's column names, fixed columns so nothing moves: Server | Status | (Filtered) | Panel | IPv4 | GRE | RAM | CPU | Disk
    const f = s.facts || {}, flt = (s.extra || {}).iran_filter, blocked = flt && (flt.level === "filtered" || flt.level === "blocked");
    const cls = (!s.enabled ? "" : s.online ? "up" : s.online === false ? "down" : "") + " " + srvSev(s);
    return `<tr class="${cls}" title="${esc(f.top ? "Busiest process: " + f.top : "")}">
      <td class="name">${s.role === "foreign" && s.country ? flagImg(s.country) + " " : ""}${esc(s.name)}<span class="pill">${esc(s.provider || s.asn || "")}</span>${s.enabled ? "" : '<span class="pill">disabled</span>'}${((s.extra || {}).net || {}).level === "down" ? '<span class="pill err" title="This Iran server cannot reach the internet">network cut</span>' : ((s.extra || {}).net || {}).level === "bad" ? `<span class="pill warn" title="${esc("internet " + s.extra.net.anchor + "%, other Iran servers " + s.extra.net.domestic + "%, foreign servers " + s.extra.net.foreign + "% lost")}">network ⚠</span>` : ""}</td>
      <td>${srvBusy(s) ? `<span class="busy-chip" title="${esc(srvBusy(s).what + (srvBusy(s).detail ? ": " + srvBusy(s).detail : ""))}"><i class="spin"></i>${esc(srvBusy(s).what)}…${srvBusy(s).detail ? `<span class="busy-detail">${esc(srvBusy(s).detail)}</span>` : ""}</span>` : noLogin(s) ? `<span class="st offline" title="${esc(s.last_error)}">no login</span><small class="err td-err" title="${esc(s.last_error)}">${esc(s.last_error).replace(/^SSH login failed: /, "").slice(0, 40)}</small>` : `<span class="st ${s.online ? "online" : s.online === false ? "offline" : ""}">${s.online ? "online" : s.online === false ? "offline" : "—"}</span>`}</td>
      ${foreignTab ? `<td>${blocked ? '<span class="mchip bad" title="Not reachable from inside Iran">filtered</span>' : ""}</td>` : ""}
      <td class="pcell">${chips(s) || '<span class="mut">—</span>'}</td>
      <td class="mono">${esc(s.host)}</td><td>${greCell(s)}</td>
      <td>${pbar(f.ram, 80, 90)}</td><td>${pbar(f.cpu, 80, 92)}</td><td>${pbar(f.disk, 85, 95)}</td>
      <td><div class="td-act">${noLogin(s) ? `<button class="sm pri" onclick="event.stopPropagation();serverPassword(${s.id}, '${esc(s.name)}', '${esc(s.last_error.replace(/^SSH login failed: /, ""))}')" title="The panel cannot log in to this server: enter its SSH password and the panel installs its key">🔑 Password</button>` : ""}<button class="sm" onclick="toggleDetail(${s.id})" title="Resources, charts, console and tools of this server">More</button><button class="sm" onclick="event.stopPropagation();editServer(${s.id})" title="Change name, address, SSH user or port">Edit</button><button class="sm danger" onclick="event.stopPropagation();deleteServer(${s.id}, '${esc(s.name)}')" title="Remove it from the panel (nothing changes on the server)">Delete</button></div></td></tr>`;
  };  const openSrv = list.find(s => s.id === state.open);
  if (openSrv) srvModal(openSrv); else if (state.edit === "new" || state.edit === "many") addModal(); else hideModal();
  $("#page").innerHTML = `<div class="tiles">
      <div class="tile ${online < mine.length ? "bad" : "ok"}"><small>Online</small><b>${online} / ${mine.length}</b><span>${mine.length - online ? (mine.length - online) + " not answering" : "every server answers"}</span></div>
      <div class="tile ${nProb ? "warn" : ""}"><small>Problems</small><b>${nProb}</b><span>offline, or CPU > 85 %, RAM > 90 %, disk > 85 %</span></div>
      <div class="tile"><small>Average load</small><b>${avg("cpu") ?? "—"}% <small style="display:inline;font-size:12px;letter-spacing:0;text-transform:none">cpu</small></b><span>ram ${avg("ram") ?? "—"}% · disk ${avg("disk") ?? "—"}%</span></div>
      <div class="tile"><small>Traffic now</small><b>${sumNet.toFixed(0)} Mb/s</b><span>sum of ↓ and ↑ over all ${state.tab} servers</span></div></div>
    <div class="toolbar">
      <button onclick="newServer()" title="One server, or several at once with one password">+ Add server</button><button onclick="greCheckAll()" title="Look for GRE links on every server of this tab and ping their other end">GRE check all</button>${state.tab === "foreign" ? '<button class="sm" onclick="srvGeo()" title="Country of every foreign server, by majority vote of four services">🌍 Countries</button>' : ""}<a class="sm mut" href="/api/nodes/user-key" download="nikatel_user" title="Your own private key for PuTTY / ssh / MobaXterm — keep it at %USERPROFILE%\\.ssh\\nikatel_user">🔑 My SSH key</a>
      <span class="sp"></span><div class="tabs"><button class="${state.mtab !== "problems" ? "on" : ""}" onclick="state.mtab='all';refresh()">All (${mine.length})</button><button class="${state.mtab === "problems" ? "on" : ""}" onclick="state.mtab='problems';refresh()">⚠ Problems (${nProb})</button></div><input id="q" placeholder="Search…" value="${esc(state.q)}" oninput="setQ(this.value)"></div>
    <table class="tbl lift srv-tbl"><colgroup>${foreignTab ? '<col style="width:13%"><col style="width:6%"><col style="width:6%"><col style="width:13%"><col style="width:9%"><col style="width:6%"><col style="width:10%"><col style="width:10%"><col style="width:10%"><col style="width:17%">' : '<col style="width:15%"><col style="width:7%"><col style="width:15%"><col style="width:10%"><col style="width:7%"><col style="width:10%"><col style="width:10%"><col style="width:10%"><col style="width:16%">'}</colgroup>
      <thead><tr><th>Server</th><th>Status</th>${foreignTab ? "<th>Filtered</th>" : ""}<th>Panel</th><th>IPv4</th><th>GRE</th><th>RAM</th><th>CPU</th><th>Disk</th><th></th></tr></thead>
      <tbody>${rows.map(row).join("") || `<tr><td colspan="${foreignTab ? 10 : 9}" class="empty">No server.</td></tr>`}</tbody></table>`;;
  const q = $("#q"); if (q && state.q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
  if (state.open && state.edit !== state.open) loadServerCharts(state.open);
  if (state.con && state.open) { const c = $(`#con-cmd-${state.con}`); if (c) c.focus(); }
};
function srvModal(s) {   // while the edit form is open the header keeps only the name: the form has Save and Back
  const f = s.facts || {}, editing = state.edit === s.id;
  showModal(`<span class="st ${s.online ? "online" : s.online === false ? "offline" : ""}"></span><b>${esc(s.name)}</b> <span class="mono mut">${esc(s.host)}</span> <span class="pill">${esc(s.role)}</span>${f.hostname ? `<span class="pill">${esc(f.hostname)}</span>` : ""}`,
    `<div id="srv-detail-${s.id}">${editing ? SERVER_FORM(s) : serverDetailHtml(s)}</div>`, "wide" + (editing ? " editing" : ""),
    editing ? "" : `<button class="sm" onclick="editServer(${s.id})" title="Name, address, SSH user, port">✎ Edit</button><button class="sm danger" onclick="deleteServer(${s.id}, '${esc(s.name)}')" title="Remove it from the panel (nothing changes on the server)">Delete</button>`);
}
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
  // every tile = icon + title + one short line; what a test found is shown only in "Last results", so the tiles stay the same size
  if (tab === "tools") return tabs + `<div class="wgroup"><h4>Manage</h4><div class="wtools uni">${tool("⇄", "GRE test", "do its GRE links answer?", `greCheck(${s.id})`)}${tool("⌨", "Console", "run a command with presets", `srvConsole(${s.id})`)}${tool("🔑", "Connect SSH", "install the panel's key with the password", `srvConnect(${s.id}, '${esc(s.name)}')`)}${tool("🔐", "Change SSH password", "set a new login password on the server (or a strong generated one)", `srvPassword(${s.id}, '${esc(s.name)}')`)}${tool("↺", "Forget host key", "after a reinstall of this server", `forgetKey(${s.id}, '${esc(s.name)}')`)}</div></div>
    ${s.role === "foreign" ? `<div class="wgroup"><h4>Security</h4><div class="wtools uni">${tool("🛰", "Check filtering in Iran", "ping from your Iran servers + check-host.net", `srvFilter(${s.id})`)}${tool("🛡", abuseOn ? "Remove Abuse-Defender" : "Install Abuse-Defender", abuseOn ? "stop dropping traffic to abused ranges" : "drop traffic to abused ranges", `srvAbuse(${s.id}, '${abuseOn ? "remove" : "install"}')`)}</div></div>` : ""}
    ${resBox(s)}
    <div class="wgroup"><h4>Open on my computer</h4><div class="wtools uni">${tool("🖥", "Windows ssh", "a .bat with your own key", `srvOpen(${s.id}, 'ssh')`)}${tool("🐱", "PuTTY", "launcher", `srvOpen(${s.id}, 'putty')`)}${tool("🧰", "MobaXterm", "launcher", `srvOpen(${s.id}, 'mobaxterm')`)}</div></div>
    <div class="wgroup"><h4>Danger</h4><div class="wtools uni">${tool("⏻", "Reboot", "its tunnels are down until it is back", `srvReboot(${s.id}, '${esc(s.name)}')`, "danger")}</div></div>`;
  if (tab === "console") { if (state.con !== s.id) state.con = s.id; return tabs + `<div class="fix" id="con-${s.id}"><div class="frm-act"><select id="con-preset-${s.id}" onchange="$('#con-cmd-${s.id}').value=this.value">${["", ...Object.keys(CON_PRESETS)].map(k => `<option value="${k ? esc(CON_PRESETS[k][1]) : ""}">${k ? esc(CON_PRESETS[k][0]) : "— preset —"}</option>`).join("")}</select><input id="con-cmd-${s.id}" style="flex:1" placeholder="command to run as ${esc(s.ssh_user)}…" onkeydown="if(event.key==='Enter')srvRun(${s.id})"><button class="pri sm" onclick="srvRun(${s.id})">Run</button></div><pre class="gl-out" id="con-out-${s.id}">${esc(state.conOut[s.id] || "")}</pre></div>`; }
  if (tab === "charts") return tabs + `<div class="charts" id="srv-charts-${s.id}"><div class="mut">loading 24 h…</div></div><div class="kv" id="srv-peaks-${s.id}"></div>`;
  const mine = (state.tunnelsCache || []).filter(t => t.iran_name === s.name || t.foreign_name === s.name);
  const boxes = [
    ibox("🖥", "System", [irow("Host", `<b>${esc(f.hostname || x.hostname || "—")}</b>`), irow("Kernel", `<span class="mono">${esc(f.kernel || "—")}</span>`), irow("Uptime", f.uptime ? Math.round(f.uptime / 86400) + " d" : "—"), irow("Load", f.load1 != null ? f.load1 + " / " + f.load5 + " / " + f.load15 : "—"), irow("Connections", f.conn != null ? f.conn : "—"), irow("Busiest process", esc(f.top || "—"))]),
    ibox("📊", "Resources", [irow("CPU", pct(f.cpu, 80, 92)), irow("RAM", `${pct(f.ram, 80, 90)} <small class="mut">${f.ram_used_mb != null ? f.ram_used_mb + " / " + f.ram_total_mb + " MB" : ""}</small>`), irow("Swap", f.swap != null ? f.swap + "%" : "—"), irow("Disk", `${pct(f.disk, 85, 95)} <small class="mut">${f.disk_used_gb != null ? f.disk_used_gb + " / " + f.disk_total_gb + " GB" : ""}</small>`), irow("Traffic", f.rx_mbps != null ? `↓ ${f.rx_mbps} ↑ ${f.tx_mbps} Mb/s` : "—")]),
    ibox("🔑", "Access", [irow("SSH", `<span class="mono">${esc(s.ssh_user)}@${esc(s.host)}:${s.ssh_port}</span>`), irow("Last seen", when(s.last_seen_at)), irow("Role", esc(s.role)), irow("Provider", provCell(s))]),
    s.role === "foreign" ? ibox("🛡", "Location & security", [irow("Country", `${flag(s.country) || "—"} ${esc(geo.city || "")} ${geo.agree ? `<small class="mut">(${esc(geo.agree)} agree)</small>` : ""}`), irow("Abuse-Defender", abuseOn ? `<b class="gw-ok">on · ${f.abuse_rules || ab.rules || "?"} ranges</b>` : "off"), irow("Filtered in Iran?", fl ? `<b class="${fl.level === "clean" ? "gw-ok" : fl.level === "unknown" ? "" : "err"}">${esc(fl.verdict)}</b> <small class="mut">${ago(fl.checked_at)} ago</small>` : "not checked yet")]) : "",
  ].join("");
  return tabs + `<div class="iboxes">${boxes}</div>    ${fl && state.flt === s.id ? `<div class="gw-out"><b>Pings to ${esc(s.host)} from Iran</b><div class="ob-inbounds">${fl.nodes.map(n => `<span><b class="${n.pending ? "mut" : n.ok === n.total ? "gw-ok" : n.ok ? "" : "err"}">${n.pending ? "…" : n.ok + "/" + n.total}</b> ${esc(n.city)} <small class="mut">${esc(n.asn)}${n.avg_ms != null ? " · " + n.avg_ms + " ms" : ""}</small></span>`).join("")}</div>${fl.note ? `<small class="mut">${esc(fl.note)}</small>` : ""}</div>` : ""}
    ${mine.length ? `<div class="ibox wide-box"><h5><i>⇄</i>Tunnels on this server <small>${mine.length}</small></h5><div class="ichips">${mine.map(t => `<span><span class="st ${t.state}">${esc(t.name)}</span> <small class="mut">${esc(t.iran_name === s.name ? "⇄ " + t.foreign_name : "⇄ " + t.iran_name)}${t.rtt_ms != null ? " · " + t.rtt_ms.toFixed(0) + " ms" : ""}</small></span>`).join("")}</div></div>` : ""}`;
}
/* the hosting provider of a server: its website (a link), its registry name, and whether the site answers (read lazily, cached by the panel) */
function provCell(s) {
  const g = (s.extra || {}).geo || {}, name = g.provider || s.provider || "", dom = g.domain || "";
  if (!name && !dom) return '<span class="mut">not found yet (looked up once an hour)</span>';
  setTimeout(() => provSite(s.name), 0);
  return `${dom ? `<a href="https://${esc(dom)}" target="_blank" rel="noopener">${esc(dom)} ↗</a>` : ""} <small class="mut">${esc(name)}${g.asn ? " · AS" + esc(g.asn) : ""}</small> <span class="prov-site" data-srv="${esc(s.name)}"><small class="mut">checking site…</small></span>`;
}
async function provSite(name) {
  let d = state.dcs; if (!d || !d.rows) { try { d = state.dcs = await api("/api/datacentres"); } catch (e) { d = null; } }
  const row = d && d.rows && d.rows.find(r => r.servers.some(x => x.name === name)), st = row && row.site;
  document.querySelectorAll(".prov-site").forEach(el => { if (el.dataset.srv === name) el.innerHTML = !st || st.ok == null ? '<small class="mut">site not checked</small>' : st.ok ? `<b class="gw-ok">✓ site answers</b> <small class="mut">${st.ms} ms${st.via ? " from " + esc(st.via) : ""}</small>` : `<b class="err">✕ site does not answer</b>${st.via ? ` <small class="mut">from ${esc(st.via)}</small>` : ""}`; });
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
/* the result of the GRE test and of the filtering check is written into the Tools window itself */
const setRes = (key, html) => { for (const id of ["res-" + key, "tile-" + key]) { const el = $("#" + id); if (el) el.innerHTML = html; } };   // the tool tile and the results box
const FLT_LEVEL = {clean: ["gw-ok", "✓ Clean"], filtered: ["err", "✕ Filtered"], blocked: ["err", "✕ Blocked"]};
const greTxt = g => g ? `<b class="${g.ok || !g.links ? "gw-ok" : "err"}">${g.ok ? "✓ GRE OK" : g.links ? "✕ GRE" : "✓ no GRE links"}</b> · ${esc(g.detail)} · ${ago(g.at)} ago` : "do the GRE links of this server answer?";
const fltTxt = f => { if (!f) return "ping from your Iran servers + check-host.net"; const lv = FLT_LEVEL[f.level] || ["warn", "⚠ " + esc(f.level || "unknown")]; return `<b class="${lv[0]}">${lv[1]}</b> · ${esc(f.verdict || "")} · ${ago(f.checked_at)} ago`; };
function resBox(s) {
  const x = s.extra || {}, g = x.gre, f = x.iran_filter, fc = s.facts || {}, ab = x.abuse || {};
  const abuseOn = ab.installed || (fc.abuse_rules || 0) > 0, rules = fc.abuse_rules || ab.rules || 0;
  const row = (label, key, body) => `<div class="res-row"><span class="mut">${label}</span><span id="res-${key}">${body}</span></div>`;
  return `<div class="wgroup"><h4>Last results</h4><div class="ibox res-box">${row("GRE", "gre", g ? greTxt(g) : '<span class="mut">not tested yet</span>')}
    ${s.role === "foreign" ? row("Filtering in Iran", "flt", f ? fltTxt(f) : '<span class="mut">not checked yet</span>') + row("Abuse-Defender", "abuse", abuseOn ? `<b class="gw-ok">✓ Installed</b> · ${rules} ranges blocked${ab.updated_at ? " · list " + ago(ab.updated_at) + " ago" : ""}` : '<span class="mut">not installed</span>') : ""}
    ${row("Last sample", "sample", s.last_seen_at ? `${ago(s.last_seen_at)} ago${fc.uptime ? " · up " + esc(String(fc.uptime)) : ""}` : '<span class="mut">never</span>')}</div></div>`;
}window.greCheck = async id => { setRes("gre", '<span class="mut">⏳ checking… (up to 30 s)</span>'); toast("Checking GRE… (up to 30 s)"); try { const r = await api(`/api/servers/${id}/gre-check`, {method: "POST"}); toast((r.ok ? "✓ " : "✕ ") + r.detail, r.ok ? "ok" : "bad"); } catch (e) { toast("✕ " + e.message, "bad"); } refresh(); };
window.srvFilter = async id => { setRes("flt", '<span class="mut">⏳ pinging from your Iran servers… (about 30 s)</span>'); toast("Pinging from Iran… (about 30 s)"); try { const r = await api(`/api/servers/${id}/filter-check`, {method: "POST"}); toast((r.level === "clean" ? "✓ " : "⚠ ") + r.verdict, r.level === "clean" ? "ok" : "bad"); state.flt = id; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.srvReboot = async (id, name) => { if (!await gconfirm(`Reboot ${name}? Its tunnels are down until it is back (usually under a minute).`)) return; try { await api(`/api/servers/${id}/reboot`, {method: "POST"}); toast("✓ Reboot requested", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.greCheckAll = async () => { toast("Checking GRE on every server… (up to a minute)"); try { const r = await api("/api/servers/gre-check-all?role=" + encodeURIComponent(state.tab), {method: "POST"}); toast(`GRE: ${r.ok} ok · ${r.no_gre} without GRE · ${r.bad.length} not OK` + (r.bad.length ? " — " + r.bad.slice(0, 3).join("; ") : ""), r.bad.length ? "bad" : "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } refresh(); };
window.srvGeo = async () => { toast("Looking up countries…"); try { const r = await api("/api/servers/geo-refresh", {method: "POST"}); toast(`✓ ${r.changes.length} changed`, "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.srvMany = () => { state.edit = "many"; state.open = null; refresh(); };
window.addTab = k => { state.edit = k; addModal(); };
function addModal() {   // one window for adding: a tab for one server, a tab for several at once
  const one = state.edit === "new";
  showModal(`<b>+ Add ${esc(state.tab)} server</b>`, `<div class="tabs" style="margin-bottom:12px"><button class="${one ? "on" : ""}" onclick="addTab('new')">One server</button><button class="${one ? "" : "on"}" onclick="addTab('many')">Several at once</button></div>${one ? SERVER_FORM(null, state.tab) : MANY_FORM()}`);
}
window.srvManyGo = async e => { e.preventDefault(); const f = new FormData(e.target); toast("Adding… (a few seconds per server)"); try { const r = await api("/api/servers/add-many", {method: "POST", body: JSON.stringify({role: state.tab, lines: f.get("lines"), password: f.get("password"), ssh_user: f.get("ssh_user").trim() || "root", ssh_port: +f.get("ssh_port") || 22})}); state.manyRes = r.items; const ok = r.items.filter(i => i.ok).length; toast(`${ok ? "✓" : "✕"} ${ok} of ${r.items.length} added and connected`, ok === r.items.length ? "ok" : "bad"); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
const MANY_FORM = () => `<form class="frm" onsubmit="return srvManyGo(event)"><label style="grid-column:1/-1">Servers, one per line: <small>name host [user] [port] — e.g. <code>de4 203.0.113.9</code></small><textarea name="lines" class="ob-raw" required placeholder="de4 203.0.113.9&#10;nl2 203.0.113.10 root 22"></textarea></label>
    <label>SSH user<input name="ssh_user" value="root"></label><label>SSH port<input name="ssh_port" type="number" value="22"></label><label>SSH password <small>(the same for all; used once to install the panel's key — leave empty if the key is already there)</small><input name="password" type="password" autocomplete="new-password"></label>
    <div class="frm-act"><button class="pri" type="submit">Add ${state.tab} servers</button></div>
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
/* the SSH password was wrong (or the server did not answer): ask for it again, three tries, instead of leaving a server that silently has no login */
async function connectWithPrompt(id, name, firstError) {
  let why = firstError;
  for (let i = 0; i < 3; i++) {
    const pw = await gprompt(`${name}: ${why}. Enter the SSH password again (Cancel to do it later with the 🔑 Password button).`, "", {password: true});
    if (pw == null || pw === "") break;
    try { const r = await api(`/api/servers/${id}/connect`, {method: "POST", body: JSON.stringify({password: pw})}); toast("✓ Connected: " + r.hostname, "ok"); refresh(); return true; }
    catch (err) { why = err.message; if (!/password|user|denied/i.test(why)) break; }
  }
  toast(`✕ ${name} is added but has no login yet: ${why}. Use 🔑 Password on its row.`, "bad"); refresh();
  return false;
}
window.serverPassword = (id, name, why) => connectWithPrompt(id, name, why || "enter the SSH password");
window.saveServer = async (e, id) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = {name: f.get("name").trim(), host: f.get("host").trim(), role: f.get("role"), ssh_user: f.get("ssh_user").trim() || "root", ssh_port: +f.get("ssh_port") || 22, provider: (f.get("provider") || "").trim(), enabled: f.get("enabled") === "on"}, pw = f.get("password") || "";
  const items = [f.get("x_bt") === "on" ? "btblock" : "", f.get("x_node") === "on" ? "marzban_node" : ""].filter(Boolean), cert = f.get("x_cert") || "";
  if (items.includes("marzban_node") && !/BEGIN CERTIFICATE/.test(cert)) { toast("✕ Paste the certificate of the Marzban panel first (or untick Marzban node)", "bad"); return false; }
  if (items.includes("marzban_node") && f.get("x_cert_save") === "on" && cert.trim() !== (window.NODE_CERT || "").trim()) api("/api/settings/nodes", {method: "PUT", body: JSON.stringify({marzban_cert: cert})}).then(r => { window.NODE_CERT = r.marzban_cert; }).catch(() => {});
  try {
    const saved = await api(id ? `/api/servers/${id}` : "/api/servers", {method: id ? "PUT" : "POST", body: JSON.stringify(body)});
    state.edit = null;
    if (!id) refresh();          // the new row appears at once; it says "connecting…" while the password login runs
    let connected = true;
    if (id && !pw) toast("✓ Saved", "ok");
    else if (pw) {   // a password: the panel logs in once with it and installs its own key (the password is not kept)
      toast("Server added — connecting with the password…");
      try { const r = await api(`/api/servers/${saved.id}/connect`, {method: "POST", body: JSON.stringify({password: pw})}); toast((id ? "✓ Saved and connected: " : "✓ Added and connected: ") + r.hostname, "ok"); }
      catch (err) { connected = await connectWithPrompt(saved.id, body.name, err.message); }
    } else {          // no password: the panel's key must already be on the server - try it
      try { await api(`/api/servers/${saved.id}/check`, {method: "POST"}); toast("✓ Server added", "ok"); } catch (err) { connected = false; toast("Added, but the panel's key does not log in yet: " + err.message, "bad"); }
    }
    refresh();
    if (items.length && connected) {   // the extras run in one story the operator can follow (and minimise)
      const r = await api(`/api/servers/${saved.id}/install`, {method: "POST", body: JSON.stringify({items, cert})});
      showModal(`<b>⬇ Installing on ${esc(body.name)}</b>`, '<div id="ap-story" class="fix"><div class="fx-line run"><i></i>Starting…</div></div>', "wide");
      followStory(r.id, $("#ap-story"), 400).then(() => refresh());
    } else if (items.length) toast("Not installed: the server does not log in yet - fix that, then add the server again or use its console", "bad");
  } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};
window.srvPassword = async (id, name) => {
  const typed = await gprompt(`New SSH password for ${name} (8 characters or more). Leave empty to generate a strong one.`, "", {password: true});
  if (typed == null) return;
  if (typed !== "" && typed.length < 8) { toast("✕ The password must be at least 8 characters", "bad"); return; }
  let pw = typed;
  if (!pw) { const a = new Uint32Array(20), cs = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"; crypto.getRandomValues(a); pw = [...a].map(x => cs[x % cs.length]).join(""); }
  try {
    await api(`/api/servers/${id}/password`, {method: "POST", body: JSON.stringify({password: pw})});
    toast("✓ SSH password changed on " + name, "ok");
    if (!typed) showModal(`<b>🔐 New password of ${esc(name)}</b>`, `<div class="ob-inbounds"><span><b class="mono" id="newpw" style="user-select:all;font-size:15px">${esc(pw)}</b></span></div><div class="frm-act"><button class="sm pri" onclick="navigator.clipboard.writeText($('#newpw').textContent).then(() => toast('✓ Copied', 'ok'))">Copy</button></div><small class="mut">Shown only now - the panel does not keep it (it logs in with its own key).</small>`);
  } catch (e) { toast("✕ " + e.message, "bad"); }
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
  const [servers, kd, tl] = await Promise.all([api("/api/servers"), tt.kinds ? Promise.resolve({kinds: tt.kinds}) : api("/api/tunnel-test/kinds"), api("/api/tunnels").catch(() => [])]);
  tt.kinds = kd.kinds;
  const opt = (role, cur) => servers.filter(s => s.role === role && s.enabled).map(s => `<option value="${s.id}" ${String(s.id) === cur ? "selected" : ""}>${esc(s.name)} — ${esc(s.host)}${s.online === false ? " (offline)" : ""}</option>`).join("");
  const j = tt.job, running = j && !j.done;
  $("#page").innerHTML = `<div class="card" data-tab="test"><h3>🧪 Tunnel test <span class="sp"></span><span id="tt-run">${ttRunPill()}</span></h3>
    <div class="frm">
      <label>Iran server<select id="tt-iran" ${running ? "disabled" : ""} onchange="tt.iran=this.value;localStorage.setItem('nk.tt.iran',this.value)">${opt("iran", tt.iran)}</select></label>
      <label>Foreign server<select id="tt-foreign" ${running ? "disabled" : ""} onchange="tt.foreign=this.value;localStorage.setItem('nk.tt.foreign',this.value)">${opt("foreign", tt.foreign)}</select></label>
      <div class="frm-act">${running ? `<button class="danger" onclick="ttStop()">Stop</button>` : `<button class="pri" onclick="ttStart()">Start test</button>`}<small class="mut">${running ? "the current transport finishes, then the test stops; everything temporary is removed" : "each ticked transport is built as a temporary tunnel, measured, and removed again — one after the other"}</small></div>
    </div>
    <div class="ob-sec" id="tt-kinds"><h4>What to try <small class="mut">— tick the engines; Advanced opens every transport so single ones can be unticked</small><span class="sp"></span><a class="adv-btn" onclick="ttAdv()">Advanced ${tt.adv ? "▴" : "▾"}</a></h4>
      <div class="tteng">${[...new Set(tt.kinds.map(k => k.group))].map((g, i) => `<label class="tte"><input type="checkbox" data-eng="${esc(g)}" ${running ? "disabled" : ""} onchange="ttGroup('${esc(g)}', this.checked)"><b>${esc(g)}</b><small class="mut" id="tte-n-${i}"></small></label>`).join("")}</div>
      <div class="tta ${tt.adv ? "" : "hidden"}" id="tt-adv">${[...new Set(tt.kinds.map(k => k.group))].map(g => `<div class="tta-row"><span class="tta-g">${esc(g)}</span><div class="tta-chips">${tt.kinds.filter(k => k.group === g).map(k => `<label data-g="${esc(g)}" class="ttk" title="${esc(k.note)}"><input type="checkbox" value="${k.key}" ${running ? "disabled" : ""} onchange="ttSyncEng()" ${(j ? j.kinds.includes(k.key) : k.default) ? "checked" : ""}> ${esc(k.label.replace(/^[^·]+· /, ""))}</label>`).join("")}</div></div>`).join("")}</div></div>
    <div class="ob-sec"><h4>Which tunnel for which situation <small class="mut">— click one to open Create tunnel with it</small></h4>
      <div class="bguide">${BK_GUIDE.map(([when, what, k, t, why]) => `<a class="bg-row" title="${esc(why)}" onclick="bkPick('${k}', '${t}');state.sub.tunnels='list';state.build=true;refresh()"><b>${esc(when)}</b><span>${esc(what)}</span><small>${esc(why)}</small></a>`).join("")}</div>
      <small class="mut">Every engine ships with its many-users settings (multiplexing, 8 mux channels, 16-24 connection pool, keepalive 75 s, heartbeat 20 s, BBR + fq on both kernels). Backhaul is measured with 30 pings over GRE and a download; the other engines through their test door, both ways. Nothing is added to your list; the story of each run stays in Repairs.</small></div></div>
    ${apAdvisorCard(tl, tl.find(x => String(x.id) === ap.tid) || tl.find(x => x.state !== "up") || tl[0])}
    <div class="card tt-logcard" data-tab="test"><h3>Live log <span class="sp"></span><small class="mut">fixed box: new lines come in here, the page stays where it is</small></h3><div class="tt-log" id="tt-log">${ttLogHtml()}</div></div>
    <div id="tt-results" data-tab="test">${ttResultsHtml()}</div>`;
  const lg = $("#tt-log"); if (lg) lg.scrollTop = lg.scrollHeight;
  ttSyncEng();
  apAdvisor();
  pageTabs("tunneltest", [["test", "🧪 Tunnel test"], ["adv", "🧭 Network advisor"]], "test", {adv: ap.job && !ap.job.done ? 1 : 0});
  if (running) { clearTimeout(tt.timer); tt.timer = setTimeout(ttPoll, 3000); }
};
const ttRunPill = () => { const j = tt.job; return j && !j.done ? `<span class="pill ok">running: ${esc(j.current || "…")} · ${j.results.length} / ${j.kinds.length} done</span>` : ""; };
const ttLogHtml = () => { const j = tt.job; return j && j.story && j.story.length ? j.story.map(l => `<div class="fx-line"><b>·</b><span>${esc(l)}</span></div>`).join("") : `<div class="mut" style="padding:8px 2px">${j && !j.done ? "starting…" : "Start a test and its story shows up here."}</div>`; };
function ttResultsHtml() {
  const j = tt.job; if (!j) return "";
  const running = !j.done, best = j.results.filter(r => r.ok && r.score != null).sort((a, b) => b.score - a.score)[0];
  const row = r => { const sc = r.score == null ? null : r.score; return `<tr class="${r.ok ? "up" : "down"}"><td class="name">${esc((tt.kinds.find(k => k.key === r.kind) || {}).label || r.kind)}</td><td><span class="st ${r.ok ? "up" : "down"}">${r.error ? "failed" : r.ok ? "works" : "poor / failed"}</span></td>
      <td class="num">${r.latency_ms != null ? r.latency_ms + " ms" : "—"}</td><td class="num">${r.jitter_ms != null ? r.jitter_ms + " ms" : "—"}</td><td class="num">${r.loss_pct != null ? r.loss_pct + "%" : "—"}</td><td class="num">${r.down_mbps != null ? "↓" + r.down_mbps + (r.up_mbps != null ? " ↑" + r.up_mbps : "") + " Mb/s" : "—"}</td>
      <td><b class="${sc == null ? "mut" : sc >= 75 ? "gw-ok" : sc >= 50 ? "" : "err"}">${sc == null ? "—" : sc + " / 100"}</b></td><td class="mut" title="${esc(r.error || r.verdict || "")}">${esc((r.error || r.verdict || "").slice(0, 60))}</td></tr>`; };
  return `<div class="card"><h3>Results <span class="sp"></span>${best ? `<span class="pill ok">best: ${esc((tt.kinds.find(k => k.key === best.kind) || {}).label || best.kind)} · ${best.score} / 100</span>` : ""}</h3>
    <table class="tbl"><colgroup><col style="width:24%"><col style="width:10%"><col style="width:10%"><col style="width:10%"><col style="width:8%"><col style="width:12%"><col style="width:11%"><col></colgroup>
      <thead><tr><th>Transport</th><th>State</th><th>Latency</th><th>Jitter</th><th>Loss</th><th>Download</th><th>Score</th><th>Verdict</th></tr></thead>
      <tbody>${[...j.results].sort((a, b) => (b.score || 0) - (a.score || 0)).map(row).join("")}${running ? `<tr><td class="name">${esc(j.current)}</td><td colspan="7" class="mut">measuring…</td></tr>` : ""}</tbody></table></div>`;
}
/* a poll repaints only the pill, the results and the log box: the page itself is not rebuilt, so nothing jumps */
function ttPaint() {
  const r = $("#tt-run"), res = $("#tt-results"), lg = $("#tt-log"); if (!r || !res || !lg) return false;
  const atEnd = lg.scrollTop + lg.clientHeight >= lg.scrollHeight - 12, keep = lg.scrollTop;
  r.innerHTML = ttRunPill(); res.innerHTML = ttResultsHtml(); lg.innerHTML = ttLogHtml();
  lg.scrollTop = atEnd ? lg.scrollHeight : keep;
  return true;
}
window.ttGroup = (g, on) => { document.querySelectorAll(`#tt-kinds label[data-g="${g}"] input`).forEach(x => { if (!x.disabled) x.checked = on; }); ttSyncEng(); };
window.ttAdv = () => { tt.adv = !tt.adv; const el = $("#tt-adv"); if (el) el.classList.toggle("hidden", !tt.adv); const b = $("#tt-kinds .adv-btn"); if (b) b.textContent = "Advanced " + (tt.adv ? "▴" : "▾"); };
window.ttSyncEng = () => document.querySelectorAll("#tt-kinds .tte input").forEach((e, i) => {   // an engine is ticked when all its transports are, half-ticked when some are
  const all = [...document.querySelectorAll(`#tt-kinds label[data-g="${e.dataset.eng}"] input`)], on = all.filter(x => x.checked).length;
  e.checked = on === all.length; e.indeterminate = on > 0 && on < all.length;
  const n = $("#tte-n-" + i); if (n) n.textContent = `${on} / ${all.length}`;
});
window.ttStart = async () => {
  const kinds = [...document.querySelectorAll("#tt-kinds .ttk input:checked")].map(x => x.value);
  if (!kinds.length) { toast("Tick at least one transport", "bad"); return; }
  try { const r = await api("/api/tunnel-test/compare", {method: "POST", body: JSON.stringify({iran_id: +$("#tt-iran").value, foreign_id: +$("#tt-foreign").value, kinds})}); tt.job = {id: r.id, kinds, results: [], current: kinds[0], done: false}; toast("Test started — about 2 minutes per transport"); refresh(); }
  catch (e) { toast("✕ " + e.message, "bad"); }
};
window.ttPoll = async () => {
  if (!tt.job || tt.job.done) return;
  try { tt.job = await api(`/api/tunnel-test/compare/${tt.job.id}`); } catch (e) { tt.job.done = true; }
  if (state.page !== "tunnels" || sub("tunnels") !== "test") return;
  if (tt.job.done) { refresh(); return; }          // finished: rebuild once so the controls come back
  if (!ttPaint()) { refresh(); return; }
  clearTimeout(tt.timer); tt.timer = setTimeout(ttPoll, 3000);
};
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
window.checkAll = async () => {
  showModal("<b>🔍 Checking every tunnel</b>", '<div class="loading"><i class="spin"></i><span>Is each tunnel up, and how many packets does it lose? (about a minute)</span></div>', "wide");
  try {
    const r = await api("/api/tunnels/full-check", {method: "POST"});
    const loss = x => x.loss_pct == null ? '<span class="mut">—</span>' : `<b class="${x.loss ? "warn" : "gw-ok"}">${x.loss_pct}%</b>${x.loss === "high" ? ' <small class="warn">users feel it</small>' : x.loss === "some" ? ' <small class="warn">some</small>' : ""}`;
    showModal(`<b>🔍 All tunnels</b> <span class="pill ${r.down || r.high_loss ? "err" : "ok"}">${r.up} up · ${r.degraded} degraded · ${r.down} down${r.unknown ? " · " + r.unknown + " unknown" : ""}</span>${r.high_loss ? ` <span class="pill err">${r.high_loss} with high packet loss</span>` : ""}`,
      `<table class="tbl"><thead><tr><th>Tunnel</th><th>State</th><th>Iran ⇄ Foreign</th><th>Latency</th><th>Packet loss</th></tr></thead><tbody>${r.rows.map(x => `<tr class="${x.state}"><td class="name">${esc(x.name)}</td><td><span class="st ${x.state}">${esc(x.state)}</span>${x.why ? `<small class="err td-err" title="${esc(x.why)}">${esc(x.why).slice(0, 44)}</small>` : ""}</td><td class="mut">${esc(x.iran)} ⇄ ${esc(x.foreign)}</td><td class="num">${x.latency_ms != null ? Math.round(x.latency_ms) + " ms" : '<span class="mut">—</span>'}</td><td class="num">${loss(x)}</td></tr>`).join("")}</tbody></table>
      <small class="mut">Packet loss of ${LOSS_HIGH_PCT} % or more is felt by users (stalls, slow pages); it is only shown here - the smart repair acts on it by itself when it is the route.</small>`, "wide");
  } catch (e) { showModal("<b>🔍 All tunnels</b>", `<div class="err">${esc(e.message)}</div>`); }
  refresh();
};
const LOSS_HIGH_PCT = 10;window.tunFilter = m => { localStorage.setItem("nk.tf", m); refresh(); };
window.healthAll = async () => { toast("Checking every tunnel… (up to a minute)"); try { const r = await api("/api/tunnels/health-all", {method: "POST"}); toast(`Health: ${r.up} up · ${r.degraded} degraded · ${r.down} down` + (r.unknown ? ` · ${r.unknown} unknown` : ""), r.down || r.degraded ? "bad" : "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } refresh(); };
window.restartTunnel = async (id, name) => { if (!await gconfirm(`Restart ${name} on both servers? It is down for a few seconds.`)) return; toast(`Restarting ${name}…`); try { const r = await api(`/api/tunnels/${id}/restart`, {method: "POST"}); toast(`${name}: ${r.state}` + (r.why ? " - " + r.why : ""), r.state === "up" ? "ok" : "bad"); } catch (e) { toast("✕ " + e.message, "bad"); } refresh(); };
window.restartAll = async () => { if (!await gconfirm("Restart every tunnel on both servers? Each is down for a few seconds.")) return; toast("Restarting every tunnel… (about two minutes)"); try { const r = await api("/api/tunnels/restart-all", {method: "POST"}); toast(`Restarted: ${r.up} up · ${r.degraded} degraded · ${r.down} down` + (r.unknown ? ` · ${r.unknown} unknown` : ""), r.down || r.degraded ? "bad" : "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } refresh(); };
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
  const tmode = localStorage.getItem("nk.tf") === "problems" ? "problems" : "all", nProblems = list.filter(tunSev).length;
  const workList = ((await api("/api/autopilot/outage").catch(() => null)) || {}).working || [], fixing = new Set(workList.filter(w => w.mode !== "look").map(w => w.name)), workMap = new Map(workList.map(w => [w.name, w]));
  if (workList.length) { clearTimeout(window.__workT); window.__workT = setTimeout(() => { if (state.page === "tunnels" && !state.open && !state.build) refresh(); }, 4000); }
  const shortWhy = t => { const e = t.last_error || "", l = (t.detail || {}).loss_pct; return /flaps|lost packets/.test(e) ? "keeps dropping" : /loss/.test(e) ? `${l || ""}% packets lost` : /not listening/.test(e) ? "port closed" : /no data/.test(e) ? "no data" : e.slice(0, 24); };
  const wrench = t => fixing.has(t.name) ? '<span class="wrench" title="The smart repair is working on this tunnel right now">🔧</span>' : "";
  const rows = list.filter(t => (!state.q || (t.name + t.iran_name + t.foreign_name + t.type).toLowerCase().includes(state.q)) && (tmode === "all" || tunSev(t)));
  const stText = t => t.state === "up" ? "ACTIVE" : t.state === "down" ? "DOWN" : t.state === "degraded" ? "UNSTABLE" : t.state === "starting" ? "STARTING" : "UNKNOWN";
  const ports = t => String((t.record || {}).port_mapping || (t.forwarded_ports || []).join(",")).split(",").map(x => x.trim()).filter(Boolean);
  const row = t => { const q = t.quality || {}, gre = t.type === "backhaul" || t.type === "gre", d = t.detail || {}; return `<tr class="${t.state} ${tunSev(t)}">
      <td class="name">${esc(t.name)}${wrench(t)}</td>
      <td><span class="td-type">${esc((t.type || "").toUpperCase())}${t.transport ? " · " + esc(t.transport.replace("l3:", "")) : ""}</span><small class="mut"> ${gre ? "GRE" : (t.record || {}).direction === "direct" ? "direct" : "public"}</small></td>
      <td>${workMap.has(t.name) ? `<span class="busy-chip" title="The smart repair is working on this tunnel"><i class="spin"></i>${workMap.get(t.name).mode === "look" ? "checking" : "repairing"}…</span>` : `<span class="st ${t.state}">${stText(t)}</span>${t.last_error && t.state !== "up" ? `<small class="${t.state === "down" ? "err" : "mut"} td-err" title="${esc(t.last_error)}">${esc(t.state === "degraded" ? shortWhy(t) : t.last_error.slice(0, 40))}</small>` : ""}`}</td>
      <td class="td-node"><b>${esc(t.iran_name)}</b><span class="mono mut">${esc(t.iran_host)}</span></td>
      <td class="td-node"><b>${esc(t.foreign_name)}</b><span class="mono mut">${esc(t.foreign_host)}</span></td>
      <td><div class="td-ports">${ports(t).slice(0, 6).map(p => `<i class="${d.listen && d.listen[parseInt(p)] === false ? "bad" : ""}">${esc(p)}</i>`).join("")}${ports(t).length > 6 ? `<i>+${ports(t).length - 6}</i>` : ""}${ports(t).length ? "" : '<span class="mut">—</span>'}</div></td>
      <td class="num"><b class="${t.rtt_ms == null ? "mut" : t.rtt_ms > 150 ? "warn" : ""}">${t.rtt_ms != null ? t.rtt_ms.toFixed(0) + " ms" : "—"}</b>${d.loss_pct >= SHOW_LOSS ? `<small class="loss hi" title="Packets lost on the GRE link of this tunnel"> ${d.loss_pct}% loss</small>` : ""}</td>
      <td class="num hide-sm" title="${esc(q.latency_ms != null ? "quality test: " + q.latency_ms + " ms · jitter " + q.jitter_ms + " · loss " + q.loss_pct + "% · score " + q.score : "")}">${t.rx_mbps != null ? "↓" + mbps(t.rx_mbps) + " ↑" + mbps(t.tx_mbps) : "—"}</td>
      <td><div class="td-act"><button class="sm" onclick="toggleDetail(${t.id})" title="Details, charts and actions of this tunnel">More</button><button class="sm" onclick="event.stopPropagation();checkTunnel(${t.id})" title="Health check now">Health</button><button class="sm" onclick="event.stopPropagation();restartTunnel(${t.id}, '${esc(t.name)}')" title="Restart the tunnel service on both servers (the GRE link stays)">Restart</button><button class="sm danger" onclick="event.stopPropagation();deleteTunnel(${t.id}, '${esc(t.name)}')">Delete</button></div></td></tr>`; };
  const creating = false;
  $("#page").innerHTML = `<div class="toolbar"><button class="pri" onclick="openBuild()">＋ Create tunnel</button><button onclick="checkAll()" title="Checks every tunnel for real - up or down, does data pass - and measures its packet loss and latency. Only shows; nothing is repaired">🔍 Check all</button><button onclick="restartAll()" title="Restart the tunnel service of every tunnel, four at a time">Restart all</button><button onclick="detectTunnels()" title="Scan every server for tunnels that are not in this list - complete pairs can be added, incomplete leftovers removed">🔍 Detect on servers</button><span class="mut">${list.filter(t => t.state === "up").length} up · ${list.filter(t => t.state === "down").length} down · ${list.filter(t => t.state === "degraded").length} degraded</span><span class="sp"></span><input id="q" placeholder="Search…" value="${esc(state.q)}" oninput="setQ(this.value)"></div>
    <div class="tabs set-tabs tf-tabs"><button class="${tmode === "all" ? "on" : ""}" onclick="tunFilter('all')">All <i class="cnt">${list.length}</i></button><button class="${tmode === "problems" ? "on" : ""}" onclick="tunFilter('problems')">Problems ${nProblems ? `<i class="cnt">${nProblems}</i>` : ""}</button></div>
    ${tmode === "problems" && !rows.length ? '<div class="empty">No tunnel has a problem - every one is up with little packet loss.</div>' : ""}
    ${TUN_TYPES.filter(ty => rows.some(t => tunType(t) === ty)).map(ty => `<div class="nk-ghead"><b>${esc(TUN_LABEL[ty] || ty)}</b><span>${rows.filter(t => tunType(t) === ty).length}</span></div>
    <table class="tbl lift tun-tbl"><colgroup><col style="width:12%"><col style="width:11%"><col style="width:12%"><col style="width:10%"><col style="width:10%"><col style="width:9%"><col style="width:8%"><col style="width:9%"><col style="width:290px"></colgroup>
      <thead><tr><th>Tunnel</th><th>Type</th><th>Status</th><th>Iran node</th><th>Foreign node</th><th>Ports</th><th>Latency</th><th>Traffic</th><th style="text-align:right">Actions</th></tr></thead>
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
  const [servers, kd, av] = await Promise.all([api("/api/servers"), bk.kinds ? Promise.resolve({kinds: bk.kinds}) : api("/api/tunnels/kinds"), api("/api/settings/arvan").catch(() => ({}))]);
  bk.kinds = kd.kinds; bk.arvan = ARVAN_UI && !!av.has_api_key; bk.arvanMode = av.mode || "off";
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
      <label>Iran server <small><a onclick="autoIran(this.closest('form'))" title="Ping the foreign server from every Iran server, look at load and how many tunnels each one carries, pick the best">choose for me</a></small><select name="iran_id" required onchange="bkCheck(this.closest('form'))"><option value="0" ${String(v.iran_id) === "0" ? "selected" : ""}>✨ Smart — the best Iran server for this foreign server</option>${opt("iran", "iran_id")}</select></label>
      <label>Foreign server<select name="foreign_id" required onchange="bkCheck(this.closest('form'))">${opt("foreign", "foreign_id")}</select></label>
      <label>Ports to forward <small>(443, 443=2096, 10000-10009)</small><input name="ports" required placeholder="8072" value="${val("ports", "")}"></label>
      <label>Tunnel type <small>(the defaults are the many-users settings)</small>${typeSel}</label></div>
    <div id="auto-iran" class="mut"></div><div id="build-check" class="bcheck"></div>
    ${bk.arvan ? `<div class="bdns"><label class="chk"><input type="checkbox" name="dns_set" ${bk.chk.dns_set ? "checked" : ""} onchange="bkDns(this)"> After the build, point these DNS names at the new Iran server (ArvanCloud) <small class="mut">(comma separated; pick from the list)</small></label><input name="dns_names" list="dns-names" class="${bk.chk.dns_set ? "" : "hidden"}" value="${val("dns_names", "")}" placeholder="ca.example.ir, ca2.example.ir" autocomplete="off"><datalist id="dns-names"></datalist></div>` : ""}
    <div class="badv"><a onclick="bkAdv()">${bk.adv ? "▾ Advanced settings" : "▸ Advanced settings"} <small class="mut">${bk.adv ? "— change a value only when you measured a reason" : "— keepalive, heartbeat, MTU, preset, UDP…"}</small></a>${bk.adv ? `<div class="bgrid" style="margin-top:8px">${options}</div>` : ""}</div>
    <div class="frm-act"><button class="pri" type="submit">Build</button><button type="button" onclick="startTest(event)" title="Build a temporary tunnel of this kind between these two servers, measure it, remove it">Test this pair first</button></div>
  </form><div id="build-story" class="fix hidden"></div>`;
}
/* the Create window redraws only its own body: no refresh of the page behind it, the typed values stay */
async function bkRedraw() { const body = $("#modal .modal-body"); if (!body) return refresh(); const form = $("#modal form.build"); if (form) bkKeep(form); const story = $("#build-story"), keep = story && !story.classList.contains("hidden") ? story.innerHTML : ""; body.innerHTML = await buildFormHtml(); { const fm = $("#modal form.build"); if (fm) bkCheck(fm, true); } if (keep) { const s2 = $("#build-story"); s2.classList.remove("hidden"); s2.innerHTML = keep; } }
window.bkAdv = () => { bk.adv = !bk.adv; bkRedraw(); };
/* the A records of the ArvanCloud account, for the name lists (fetched once per page load, the server keeps them for five minutes) */
async function dnsListLoad(id) {
  const dl = document.getElementById(id); if (!dl) return;
  try { bk.dnsRows = bk.dnsRows || (await api("/api/arvan/records")).records; dl.innerHTML = bk.dnsRows.map(r => `<option value="${esc(r.fqdn)}">${esc(r.ips.join(", "))}</option>`).join(""); } catch (e) { toast("✕ " + e.message, "bad"); }
}
window.bkDns = cb => { bk.chk.dns_set = cb.checked; const inp = cb.closest(".bdns").querySelector("input[name=dns_names]"); inp.classList.toggle("hidden", !cb.checked); if (cb.checked) { dnsListLoad("dns-names"); inp.focus(); } };
window.bkType = v => { const [k, t] = v.split("|"); bk.kind = k; bk.transport = t; localStorage.setItem("nk.bk.kind", k); bkRedraw(); };
const BP_MUXED = ["tcpmux", "wsmux", "wssmux"];
bk.chk = {};   // every checkbox the form ever showed -> its last state (a box that is not on screen keeps its default)
window.bkKeep = form => { const f = new FormData(form); bk.v = {__t: "1"}; for (const [k, x] of f.entries()) bk.v[k] = x; form.querySelectorAll("input[type=checkbox]").forEach(cb => { bk.chk[cb.name] = cb.checked; }); clearTimeout(bk.ckT); bk.ckT = setTimeout(() => bkCheck(form), 500); };
/* the port check under the form: a port already used on the chosen Iran server (by a tunnel of the panel; on Build also by any process) stops the build,
   a tunnel that already carries the port to the foreign server can be replaced (removed first), and its CDN records can follow the new server */
const bkKey = f => (+f.get("iran_id") || 0) + "|" + (+f.get("foreign_id") || 0) + "|" + (f.get("ports") || "").trim();
window.bkCheck = async (form, force) => {
  const f = new FormData(form), box = $("#build-check"); if (!box) return;
  const key = bkKey(f), ports = (f.get("ports") || "").trim();
  if (!+f.get("foreign_id") || !ports) { box.innerHTML = ""; bk.ckKey = ""; return; }
  if (!force && key === bk.ckKey) return;
  bk.ckKey = key; bk.ck = null;
  try { bk.ck = await api("/api/tunnels/precheck", {method: "POST", body: JSON.stringify({iran_id: +f.get("iran_id") || 0, foreign_id: +f.get("foreign_id"), ports, live: false})}); }
  catch (e) { if (key === bk.ckKey) box.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  if (key === bk.ckKey) bkCheckRender(form);
};
function bkCheckRender(form) {
  const box = $("#build-check"), c = bk.ck, btn = form.querySelector("button.pri"); if (!box || !c) return;
  const busy = c.busy.map(b => `<div class="err">⛔ Port ${b.port} is already used on ${esc(c.iran)} by ${esc(b.by + (b.state ? ", " + b.state : ""))}. Choose another Iran server (or ✨ Smart) or another port.</div>`).join("");
  const conf = c.conflicts.map(t => `<div class="bconf"><label class="chk"><input type="checkbox" name="replace_${t.id}" ${t.replaceable ? "checked" : ""}> ⚠ Port ${t.ports.join(", ")} already goes to this foreign server through ${esc(t.name)} (${esc(t.iran)}, ${esc(t.state)}${t.state !== "up" && t.since ? " for " + ago(t.since) : ""}) — remove it first and replace it</label></div>`).join("");
  const dns = c.conflicts.length && bk.arvan ? `<label class="chk"><input type="checkbox" name="dns_follow" checked> then point the CDN records of the replaced tunnel at the new Iran server <small class="mut">${bk.arvanMode === "auto" ? "(automatic)" : "(asks first on Smart repair → Failover)"}</small></label>` : "";
  box.innerHTML = busy + conf + dns + (c.error ? `<div class="mut">${esc(c.error)}</div>` : "");
  if (btn) btn.disabled = !!c.busy.length;
}
window.bkKind = k => { bk.kind = k; bk.transport = ""; localStorage.setItem("nk.bk.kind", k); bkRedraw(); };
window.bkTransport = t => { bk.transport = t; bkRedraw(); };
function bkBody(f) {
  const body = {iran_id: +f.get("iran_id"), foreign_id: +f.get("foreign_id"), ports: (f.get("ports") || "").trim(), kind: bk.kind, transport: bk.transport || f.get("transport") || "", opts: {}};   // the type select keeps kind|transport in bk, not in a field
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
  const form = e.target.closest("form"), f = new FormData(form);
  const trn = bk.transport || f.get("transport") || "tcpmux";
  const kind = bk.kind === "backhaul" ? trn : bk.kind === "backpack" ? "bp:" + trn : bk.kind === "backpack_direct" ? "bpd:" + trn : bk.kind === "paqet" ? "paqet" : (trn === "wss" ? "chisel:tls" : "chisel");
  const box = $("#build-story"); box.classList.remove("hidden");
  let iranId = +f.get("iran_id") || 0;
  try {
    if (!iranId) {   // the smart choice: rank the Iran servers for this foreign server first
      box.innerHTML = '<div class="fx-line run"><i></i>Choosing the Iran server…</div>';
      const r = await api("/api/tunnels/auto-iran", {method: "POST", body: JSON.stringify({foreign_id: +f.get("foreign_id"), ports: f.get("ports") || ""})});
      const best = r.ranking.find(x => x.score > 0);
      if (!best) { box.innerHTML = '<div class="fx-line bad"><b>!</b>No Iran server can carry this tunnel right now.</div>'; return; }
      iranId = best.id; form.querySelector("[name=iran_id]").value = best.id; bkKeep(form);
    }
    box.innerHTML = '<div class="fx-line run"><i></i>Building a temporary tunnel…</div>';
    const r = await api("/api/tunnel-test/compare", {method: "POST", body: JSON.stringify({iran_id: iranId, foreign_id: +f.get("foreign_id"), kinds: [kind]})}); tt.job = {id: r.id, kinds: [kind], results: [], current: kind, done: false}; toast("Test started — the result shows in Tunnel test"); setSub("tunnels", "test");
  } catch (err) { box.innerHTML = `<div class="fx-line bad"><b>!</b>${esc(err.message)}</div>`; }
};
window.newTunnel = () => { state.edit = "newtunnel"; state.open = null; refresh(); };
window.startBuild = async e => {
  e.preventDefault();
  const form = e.target, f = new FormData(form), body = bkBody(f);
  const box = $("#build-story"); box.classList.remove("hidden");
  try {
    const seen = bk.ckKey === bkKey(f) && !!bk.ck;        // the notice under the form already shows the conflicts for exactly these fields
    box.innerHTML = '<div class="fx-line run"><i></i>Checking the ports…</div>';
    const c = await api("/api/tunnels/precheck", {method: "POST", body: JSON.stringify({iran_id: body.iran_id, foreign_id: body.foreign_id, ports: body.ports, live: !!body.iran_id})});
    bk.ck = c; bk.ckKey = bkKey(f); bkCheckRender(form);
    if (c.busy.length) { box.innerHTML = `<div class="fx-line bad"><b>!</b>Port ${c.busy[0].port} is already in use on ${esc(c.iran)} (${esc(c.busy[0].by)}): choose another Iran server or another port. Nothing was built.</div>`; return false; }
    if (c.conflicts.length && !seen) { box.innerHTML = '<div class="fx-line plan"><b>›</b>A tunnel already carries this port to the foreign server: decide above (replace it or keep it), then press Build again.</div>'; return false; }
    body.replace_ids = [...form.querySelectorAll("input[name^=replace_]:checked")].map(x => +x.name.slice(8));
    body.dns_follow = body.replace_ids.length > 0 && !!form.querySelector("input[name=dns_follow]:checked");
    body.dns_names = form.querySelector("input[name=dns_set]:checked") ? String(f.get("dns_names") || "").split(/[,\s]+/).filter(Boolean) : [];
    box.innerHTML = '<div class="fx-line run"><i></i>Starting…</div>';
    const r = await api("/api/tunnels", {method: "POST", body: JSON.stringify(body)});
    await followStory(r.id, box, 600);
  } catch (err) { box.innerHTML = `<div class="fx-line bad"><b>!</b>${esc(err.message)}</div>`; }
  return false;
};
async function followStory(aid, box, maxPolls) {
  for (let i = 0; i < maxPolls; i++) {
    await new Promise(res => setTimeout(res, 1500));
    const s = await api(`/api/actions/${aid}`);
    const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 12;
    box.innerHTML = storyHtml(s);
    if (atEnd) box.scrollTop = box.scrollHeight;      // a fixed box that follows the end of the log, unless you scrolled up to read
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
      const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 12;
    box.innerHTML = storyHtml(s);
    if (atEnd) box.scrollTop = box.scrollHeight;      // a fixed box that follows the end of the log, unless you scrolled up to read
      if (s.done) break;
    }
  } catch (e) { box.innerHTML = `<div class="fx-line bad"><b>!</b>${esc(e.message)}</div>`; }
  state.edit = null;
};
function storyHtml(s) {
  const cls = l => /healthy|works|finished|succeeded/i.test(l) ? "ok" : /not repaired|failed|still|does not answer|No safe|read-only|Stopped/i.test(l) ? "bad" : /^Plan:|^\[dry run\]/.test(l) ? "plan" : "do";
  return s.story.map(l => `<div class="fx-line ${cls(l)}"><b>${{ok: "✓", bad: "!", plan: "›", do: "·"}[cls(l)]}</b><span>${esc(l)}</span></div>`).join("") +
    (s.done ? `<div class="fx-line ${s.ok ? "ok" : "bad"}"><b>${s.ok ? "✓" : "✕"}</b><span>${["autopilot", "fix"].includes(s.action) && !s.dry_run ? (s.ok ? "All done - the problem is fixed." : "Repair did not succeed.") : `${s.dry_run ? "Dry run" : s.action === "build" ? "Build" : s.action === "create" ? "Create" : s.action === "delete" ? "Removal" : s.action === "observe" ? "Look" : "Repair"} ${s.ok ? "finished" : "did not succeed"}.`}</span></div>` : '<div class="fx-line run"><i></i>Working…</div>');
}
/* ---- Smart repair: what needs attention, the network advisor ("which tunnel is best today"), the history ---- */
const ARVAN_UI = false;   // the ArvanCloud failover is switched off for now (also server side: failover.ENABLED); flip both to bring it back
const ap = {job: null, tid: localStorage.getItem("nk.ap.tid") || "", timer: null};
const AP_LABEL = {tcpmux: "Backhaul · TCP Mux (GRE)", "bp:tcpmux": "BackPack · TCP Mux", "bp:wssmux": "BackPack · WebSocket TLS Mux", "chisel:tls": "Chisel · TLS WebSocket", "bpd:udp": "BackPack Direct · UDP", tcp: "Backhaul · TCP", wsmux: "Backhaul · WS Mux", wssmux: "Backhaul · WSS Mux", paqet: "Paqet · KCP", chisel: "Chisel · WebSocket", "bp:stealth": "BackPack · Stealth", "bp:tcp": "BackPack · TCP"};
const apName = k => AP_LABEL[k] || k;
PAGES.actions = async () => {
  // Smart repair is about tunnels only: configs and servers have their own places (the dashboard's Problems box, Panels, Nodes)
  const outage = await api("/api/autopilot/outage").catch(() => null), nw = await api("/api/netwatch").catch(() => null);
  const [d, tl, rp, fo, av, irn] = await Promise.all([api("/api/actions?limit=60"), api("/api/tunnels"), api("/api/settings/repair").catch(() => ({})), ARVAN_UI ? api("/api/failover").catch(() => null) : null, ARVAN_UI ? api("/api/settings/arvan").catch(() => ({})) : {}, ARVAN_UI ? api("/api/servers?role=iran").catch(() => []) : []]);
  state.tunnelsCache = tl;
  const bad = tl.filter(t => t.state !== "up"), lossy = tl.filter(t => t.state === "up" && ((t.detail || {}).loss_pct || 0) >= SHOW_LOSS), pick = tl.find(t => String(t.id) === ap.tid) || bad[0] || tl[0];
  const row = (t, kind) => `<div class="ap-row"><span class="st ${kind === "lossy" ? "degraded" : t.state}">${kind === "lossy" ? "lossy" : esc(t.state)}</span><b>${esc(t.name)}</b>${(() => { const w = outage && (outage.working || []).find(x => x.name === t.name); return w ? `<span class="wrench" title="The smart repair is working on this tunnel right now">🔧</span><span class="work-chip">${w.mode === "look" ? "looking at it" : "repairing"}</span>` : '<span class="mut work-chip watch" title="The smart repair watches it and starts by itself when needed">watching</span>'; })()}<span class="mut">${esc(t.iran_name)} ⇄ ${esc(t.foreign_name)} · ${esc(t.type)}${t.transport ? " · " + esc(t.transport.replace("l3:", "")) : ""}</span><span class="mut ap-err">${esc(kind === "lossy" ? ((t.detail || {}).loss_pct || 0) + " % packet loss on the link" : (t.last_error || "").slice(0, 70))}</span><span class="sp"></span>${rp.auto ? `<button class="sm pri" onclick="apLive(${t.id}, '${esc(t.name)}')" title="What the smart repair is doing with this tunnel right now - its live log">📡 Live</button>` : `<button class="sm" onclick="apDiagnose(${t.id})">🩺 Diagnose</button><button class="sm pri" onclick="apDiagnose(${t.id}, {auto: true})">✨ Fix with AI</button>`}</div>`;
  const n = bad.length + lossy.length;
  const auto = rp.auto ? `automatic: level <b>${esc(rp.level || "restart")}</b> after ${esc(rp.after_seconds || 180)} s` : "automatic repair is <b>off</b>";
  const work = outage && (outage.working || []).length ? `<div class="nw-box work-box"><b class="nw-title">🔧 Working now</b>${outage.working.map(w => `<div class="nw-row"><span class="wrench">🔧</span><b>${esc(w.name)}</b><span class="mut">${w.mode === "look" ? "looking at it" : "repairing"}</span><span class="work-line">${esc(w.line)}</span><button class="sm" onclick="apLive(${(state.tunnelsCache || []).find(t => t.name === w.name)?.id || 0}, '${esc(w.name)}')">📡 Live</button></div>`).join("")}</div>` : "";
  const disturb = nw && nw.items.length ? `<div class="nw-box"><b class="nw-title">🌐 Network disturbances</b>${nw.items.map(i => `<div class="nw-row"><span class="st degraded">network</span><span>${esc(i.text)}${(i.tunnels || []).length ? ` <small class="mut">(${i.tunnels.map(esc).join(", ")})</small>` : ""}</span></div>`).join("")}<small class="mut">A server's own network is disturbed: rebuilding its tunnels would not help, so the repair only watches. It clears by itself when the server answers well again.</small></div>` : "";
  const storm = outage && outage.storm ? `<div class="storm-banner">⚠ <b>Mass disruption:</b> ${outage.servers_offline.length} server(s) offline, ${outage.tunnels_down.length} of ${outage.tunnels} tunnels down. While it lasts nothing is deleted or rebuilt; the repair waits for the servers, then restarts what did not come back by itself.</div>` : "";
  $("#page").innerHTML = `<div class="card" data-tab="att">${storm}${disturb}<h3>🔧 Working now <span class="sp"></span><label class="chk ap-auto" title="On: the smart repair restarts, rebuilds, builds the link again and tries other tunnel types by itself. Off: you diagnose and fix by hand."><input type="checkbox" ${rp.auto ? "checked" : ""} ${d.read_only ? "disabled" : ""} onchange="apAuto(this)"> Automatic repair</label>${bad.length && !rp.auto ? `<button class="sm pri" onclick="apFixAll()" title="Diagnose and repair every tunnel that is down or degraded, one after the other">✨ Fix all with AI (${bad.length})</button>` : ""}<button class="sm" onclick="apExplain()" title="The AI reads what the panel knows and says what to do first (needs the key in Settings)">💬 Explain with AI</button><span class="pill ${n ? "err" : "ok"}">${n ? n + " to look at" : "all up"}</span></h3>
      <div class="ap-att">${bad.map(t => row(t, "bad")).join("")}${lossy.map(t => row(t, "lossy")).join("")}
      ${n ? "" : '<div class="mut" style="padding:6px 0">Every tunnel is up and none loses packets. When one fails it shows here: Diagnose finds the cause (servers, services, the route, GRE, the control port, the data path) and Fix with AI runs the repair ladder - restart, rebuild, another tunnel type - and tells you when only a move to another Iran server can help. Configs and servers are watched on the dashboard, under Panels and under Nodes.</div>'}
      </div>
      <small class="mut ap-note">${d.read_only ? "Read-only mode: nothing is changed. Set NIKATEL_READ_ONLY=false on the panel server." : `Repairs: ${auto} (<a class="mut" onclick="go('settings');setTab('ai')">Settings → Repairs</a>). Restart, rebuild, a new GRE link and another tunnel type happen by themselves, without asking.`}</small><div id="ap-all" class="fix hidden" style="margin-top:10px"></div></div>
    ${foCard(fo, av, irn)}
    <div class="${ARVAN_UI ? "grid2 ap-two" : ""}" data-tab="att">
    <div class="card"><h3>🗒 History <span class="sp"></span><small class="mut">every repair, test and build with its full story</small></h3><div class="ap-hist">${d.actions.map(a => `<div class="ev ${a.ok === true ? "ok" : a.ok === false ? "bad" : "warn"}"><time title="${when(a.ts)}">${ago(a.ts)} ago</time><i></i><small>${esc(a.source)}${a.dry_run ? " · dry" : ""}</small>
      <span><b>${esc(a.ref)}</b> — ${esc(a.story[a.story.length - 1] || "")} <a class="mut" onclick="this.nextElementSibling.classList.toggle('hidden')">details</a><div class="fix hidden">${a.story.map(l => `<div class="fx-line do"><b>·</b><span>${esc(l)}</span></div>`).join("")}</div></span></div>`).join("") || '<div class="empty">No repair yet.</div>'}</div></div>
    ${ARVAN_UI ? dnsCard(fo, av, irn) : ""}</div>`;
  const nPend = (fo && fo.pending.length) || 0;
  pageTabs("actions", [["att", "🔧 Working now"], ["fo", "🔁 Failover · CDN"]].filter(t => t[0] !== "fo" || fo), nPend ? "fo" : "att", {att: n, fo: nPend});
};
const foForm = av => `<form class="frm" onsubmit="return saveArvan(event)">
    <label>ArvanCloud API key <small>(empty keeps the saved one; stays on this server)</small><input name="api_key" type="password" placeholder="${av.has_api_key ? "•••••••• saved" : "paste the key here"}" autocomplete="off"></label>
    <label>When an Iran server is down<select name="mode">${[["off", "Do nothing (failover off)"], ["approve", "Prepare a plan and ask me first"], ["auto", "Do it by itself (only clean plans)"]].map(([k, l]) => `<option value="${k}" ${(av.mode || "off") === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>
    <label>…after it has been down for (minutes)<input name="confirm_minutes" type="number" min="2" max="120" value="${av.confirm_minutes || 5}"></label>
    <label>Only these domains <small>(empty = all, comma separated)</small><input name="domains" value="${esc((av.domains || []).join(", "))}" placeholder="all domains of the account"></label>
    <div class="frm-act"><button class="pri" type="submit">Save</button><button type="button" onclick="testArvan()">Test the key (read only)</button></div></form>
  <details class="mut" style="margin-top:6px"><summary>How to get the key</summary><ol style="margin:6px 18px;line-height:1.7"><li>panel.arvancloud.ir → profile menu → <b>Machine Users</b> → create one.</li><li>Open it → <b>Access rules</b> and give it the <b>CDN</b> service with a manager role (not only “server manager”).</li><li>Create an access key for it, copy it at once (shown once) and paste it above.</li></ol></details>`;
const foCard = (fo, av = {}, irn = []) => {
  if (!fo) return "";
  const st = av.status || {}, conn = st.ok ? `<div class="ai-stat ok"><div class="ai-head">● Connected · ArvanCloud CDN</div><div class="ai-grid"><span class="mut">Domains</span><span>${st.domains}</span><span class="mut">DNS records</span><span>${st.records}</span></div></div>` : av.has_api_key ? `<div class="ai-stat bad"><div class="ai-head">● Not connected · ArvanCloud CDN</div><div class="err">${esc(st.error || "press “Test the key”")}</div></div>` : "";  const plan = p => `<div class="ap-row" style="flex-wrap:wrap;gap:6px"><span class="st down">${p.reason && p.reason !== "down" ? "route" : "down"}</span><b>${esc(p.server)}</b><span class="mut">${esc(p.ip)}${p.why ? " · " + esc(p.why) : ""}</span><span class="sp"></span><button class="sm pri" onclick="foApprove('${esc(p.id)}')" ${p.target ? "" : "disabled"}>✓ Approve</button><button class="sm" onclick="foDismiss('${esc(p.id)}')">Dismiss</button>
    <div class="mut" style="flex-basis:100%">${p.target ? `Move ${p.tunnels.length} tunnel(s) (${p.tunnels.map(t => esc(t.name)).join(", ") || "none"}) to <b>${esc(p.target.name)}</b> (${esc(p.target.ip)}).` : '<span class="err">No other Iran server can carry these tunnels.</span>'}
    ${(p.pulled || []).length ? ` Also moved to keep the DNS consistent: <b>${p.pulled.map(esc).join(", ")}</b>.` : ""}
    ${p.dns.length ? ` Then point ${p.dns.length} DNS record(s) at ${p.target ? esc(p.target.ip) : "the new server"}: ${p.dns.map(d => `<b>${esc(d.fqdn)}</b>${d.configs.length ? " (" + d.configs.length + " config" + (d.configs.length > 1 ? "s" : "") + ")" : ""}`).join(", ")}.` : " No DNS record points at this server (or no ArvanCloud key)."}
    ${(p.left_alone || []).length ? ` Left alone (no known config uses them, or they serve tunnels that stay): ${p.left_alone.map(d => esc(d.fqdn)).join(", ")}.` : ""}
    ${p.problems.length ? `<div class="err">${p.problems.map(esc).join("<br>")}</div>` : ""}</div></div>`;
  const his = h => `<div class="ev ${h.ok === true ? "ok" : h.ok === false ? "bad" : "warn"}"><time title="${when(h.at)}">${ago(h.at)} ago</time><i></i><small>${h.reason && h.reason !== "down" ? "route · " : ""}${h.dismissed ? "dismissed" : h.undone ? "undone" : "done"}</small><span><b>${esc(h.server)}</b> ${h.dismissed ? "" : `→ ${h.target ? esc(h.target.name) : "?"}: ${h.moved}/${h.tunnels} tunnels, ${h.dns.length} DNS record(s)${h.errors.length ? ", errors: " + esc(h.errors.join("; ")) : ""}`} ${h.dns.length && !h.undone ? `<a class="mut" onclick="foUndo('${esc(h.id)}')">↩ point the DNS back</a>` : ""}</span></div>`;
  const label = {off: "off", approve: "asks first", auto: "automatic"}[fo.mode] || fo.mode;
  return `<div class="card" data-tab="fo"><h3>🔁 Iran server failover · CDN <span class="sp"></span><span class="pill ${fo.mode === "off" ? "" : "ok"}">${label}</span></h3>
    ${fo.pending.map(plan).join("") || `<div class="mut" style="padding:4px 0">${fo.mode === "off" ? "Off. " : `Watching the Iran servers: one that stays down for ${fo.confirm_minutes} minutes gets a plan here. `}</div>`}
    ${fo.history.map(his).join("")}
    <small class="mut" style="display:block;margin:8px 0">CDN is ArvanCloud's service that holds the DNS records of your domains - the names your configs use. When an Iran server dies, the panel changes the IP of its records there, so users reach the new server without touching their config.</small>
    ${conn}
    ${st.ok ? `<div class="frm-act" style="margin-top:8px"><select id="fo-srv">${irn.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join("")}</select><button type="button" class="sm" onclick="foPreview()">👁 Preview: what would happen if it went down</button></div>` : ""}
    <details ${st.ok ? "" : "open"} style="margin-top:8px"><summary class="mut">⚙ Settings of the failover</summary>${foForm(av)}</details></div>`;
};
window.foPreview = async () => {
  const id = ($("#fo-srv") || {}).value; if (!id) return;
  showModal("<b>👁 Failover preview</b>", '<div class="loading"><i class="spin"></i><span>Looking at the tunnels and the DNS records…</span></div>');
  try { const p = await api("/api/failover/plan/" + id);
    showModal(`<b>👁 If ${esc(p.server)} (${esc(p.ip)}) went down</b>`, `<div class="mut">${p.target ? `${p.tunnels.length} tunnel(s) (${p.tunnels.map(t => esc(t.name)).join(", ") || "none"}) would move to <b>${esc(p.target.name)}</b> (${esc(p.target.ip)}).` : '<span class="err">No other Iran server could carry all of its tunnels.</span>'}</div>
      <div style="margin-top:8px">${p.dns.length ? "DNS records that would be changed:" + p.dns.map(d => `<div class="ap-row"><b>${esc(d.fqdn)}</b><span class="mut">${esc(d.ip)} → ${p.target ? esc(p.target.ip) : "?"}</span></div>`).join("") : "No DNS record points at this server."}</div>${p.problems.length ? `<div class="err" style="margin-top:8px">${p.problems.map(esc).join("<br>")}</div>` : ""}<small class="mut">Nothing was changed.</small>`); }
  catch (e) { showModal("<b>👁 Failover preview</b>", `<div class="err">${esc(e.message)}</div>`); }
};window.foApprove = async id => {
  if (!await gconfirm("Move the tunnels of this server to the replacement and change the ArvanCloud DNS records? Users reconnect after about a minute.", {danger: true})) return;
  try { const r = await api("/api/failover/" + encodeURIComponent(id) + "/approve", {method: "POST"}); showModal("<b>🔁 Failover</b>", '<div id="ap-story" class="fix"><div class="fx-line run"><i></i>Starting…</div></div>', "wide"); followStory(r.action_id, $("#ap-story"), 900).then(() => refresh()); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.foDismiss = async id => { try { await api("/api/failover/" + encodeURIComponent(id) + "/dismiss", {method: "POST"}); toast("Plan dismissed", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.foUndo = async id => { if (!await gconfirm("Point the DNS records back at the old server address? The tunnels stay where they are.", {danger: true})) return; try { const r = await api("/api/failover/" + encodeURIComponent(id) + "/undo", {method: "POST"}); toast("✓ " + r.changed.length + " record(s) pointed back", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };/* a page in tabs: every top-level element of #page carries data-tab; the bar at the top shows one group at a time (the choice is kept in this browser) */
function pageTabs(key, tabs, dflt, counts = {}) {
  const saved = localStorage.getItem("nk.tab." + key), cur = tabs.some(t => t[0] === saved) ? saved : dflt;
  document.querySelectorAll("#page > [data-tab]").forEach(el => el.classList.toggle("hidden", el.dataset.tab !== cur));
  $("#page").insertAdjacentHTML("afterbegin", `<div class="tabs set-tabs">${tabs.map(([k, l]) => `<button class="${k === cur ? "on" : ""}" onclick="localStorage.setItem('nk.tab.${key}','${k}');refresh()">${l}${counts[k] ? ` <i class="cnt">${counts[k]}</i>` : ""}</button>`).join("")}</div>`);
  return cur;
}
/* DNS changes (next to the history): every record the failover re-pointed in the CDN, each with its own Restore */
const dnsCard = (fo, av = {}, irn = []) => {
  const rows = (fo && fo.dns_log) || [];
  const point = av.has_api_key ? `<div class="dns-point"><small class="mut">Point a name at a server now:</small><input id="dns-point-name" list="dns-names2" placeholder="ca.example.ir" autocomplete="off" onfocus="dnsListLoad('dns-names2')"><datalist id="dns-names2"></datalist><select id="dns-point-srv">${irn.map(s => `<option value="${s.id}">${esc(s.name)} — ${esc(s.host)}</option>`).join("")}</select><button class="sm pri" onclick="foPoint()">Apply</button></div>` : "";
  const row = e => `<div class="dns-row ${e.restored ? "back" : "live"}"><time title="${when(e.at)}">${ago(e.at)} ago</time><i></i><span class="dns-t"><b>${esc(e.fqdn)}</b><small class="mut">${esc(e.old)} → ${esc(e.new)} · ${esc(e.server)} → ${esc(e.target)}${(e.configs || []).length ? " · " + e.configs.length + " config(s)" : ""}</small></span>${e.restored ? `<span class="pill" title="${e.restored_at ? when(e.restored_at) : ""}">restored</span>` : `<span class="pill ok">active</span><button class="sm" onclick="foRestore('${esc(e.id)}')" title="Point this record back at ${esc(e.old)}">↩ Restore</button>`}</div>`;
  return `<div class="card"><h3>🌐 DNS changes · CDN <span class="sp"></span><small class="mut">every record the failover re-pointed</small></h3>
    <div class="dns-list">${rows.map(row).join("") || '<div class="empty">No DNS record has been changed yet. When a failover moves the tunnels of a server, every record it re-points shows here and can be put back with one click.</div>'}</div>${point}</div>`;
};
window.foPoint = async () => {
  const names = String(($("#dns-point-name") || {}).value || "").split(/[,\s]+/).filter(Boolean), sel = $("#dns-point-srv");
  if (!names.length || !sel) { toast("Type a DNS name first", "bad"); return; }
  if (!await gconfirm(`Point ${names.join(", ")} at ${sel.options[sel.selectedIndex].textContent} now? Every change is listed below with a Restore.`, {danger: true})) return;
  try { const r = await api("/api/failover/point", {method: "POST", body: JSON.stringify({names, iran_id: +sel.value})}); showModal("<b>🌐 DNS</b>", '<div id="ap-story" class="fix"><div class="fx-line run"><i></i>Starting…</div></div>'); followStory(r.action_id, $("#ap-story"), 120).then(() => refresh()); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.foRestore = async id => {
  if (!await gconfirm("Point this DNS record back at its previous address? The tunnels stay where they are, so users of this name reach the old server again.", {danger: true})) return;
  try { const r = await api("/api/failover/dns/" + encodeURIComponent(id) + "/restore", {method: "POST"}); toast("✓ " + r.fqdn + " points at " + r.old + " again", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.apLive = async (id, name) => {
  /* the live log of what the smart repair does with this tunnel: the running repair when there is one, else the last one (with a button to look again) */
  showModal(`<b>📡 ${esc(name)}</b>`, '<div id="ap-story" class="fix"><div class="fx-line run"><i></i>Looking for the repair of this tunnel…</div></div><div class="frm-act" style="margin-top:8px"><button class="sm" onclick="apDiagnose('+id+')">🩺 Check the tunnel now</button></div>', "wide");
  try {
    const list = (await api("/api/actions?limit=60")).actions.filter(a => a.ref === name && ["autopilot", "observe", "fix", "build", "create"].includes(a.action));
    const box = $("#ap-story"), a = list.find(x => !x.done) || list[0];
    if (!a) { box.innerHTML = '<div class="mut" style="padding:8px">Nothing to show yet. As soon as this tunnel shows packet loss (5 % or more) the smart repair looks at it and writes what it sees here; it acts by itself only when the loss is high enough for users to feel (15 %) or the tunnel is down.</div>'; return; }
    box.innerHTML = storyHtml(a);
    if (!a.done) followStory(a.id, box, 900).then(() => refresh());
  } catch (e) { const box = $("#ap-story"); if (box) box.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
};
window.apAuto = async cb => {
  try {
    const cur = await api("/api/settings/repair");
    await api("/api/settings/repair", {method: "PUT", body: JSON.stringify({auto: cb.checked, after_seconds: cur.after_seconds || 180, level: cur.level || "switch"})});
    toast(cb.checked ? "✓ Automatic repair is on" : "Automatic repair is off - repair by hand (Diagnose, Fix with AI)", "ok");
  } catch (e) { cb.checked = !cb.checked; toast("✕ " + e.message, "bad"); }
  refresh();
};
window.apFixAll = async () => {
  try { const r = await api("/api/autopilot/fix-all", {method: "POST", body: JSON.stringify({level: "rebuild"})}); const box = $("#ap-all"); box.classList.remove("hidden"); box.innerHTML = '<div class="fx-line run"><i></i>Starting…</div>'; followStory(r.id, box, 900).then(() => refresh()); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.apExplain = async tid => {
  showModal("<b>💬 The AI's reading of the situation</b>", '<div class="loading"><i class="spin"></i><span>Collecting the facts and asking the AI… (about 20 s)</span></div>');
  try { const r = await api("/api/autopilot/explain", {method: "POST", body: JSON.stringify({tunnel_id: tid || null})}); showModal("<b>💬 The AI's reading of the situation</b>", `<div class="ai-say">${esc(r.text).replace(/\n/g, "<br>")}</div><small class="mut">Based on: ${r.facts.this_tunnel ? "this tunnel's diagnosis and " : ""}${(r.facts.everything_else || r.facts).tunnels.length} tunnel(s) not healthy, ${(r.facts.everything_else || r.facts).servers_offline.length} server(s) offline, ${(r.facts.everything_else || r.facts).configs_down.length} config(s) down.</small>`); }
  catch (e) { showModal("<b>💬 Explain with AI</b>", `<div class="err">${esc(e.message)}</div>${/no AI key/.test(e.message) ? '<div style="margin-top:10px"><button class="sm" onclick="closeModal();go(\'settings\');setTab(\'ai\')">Open Settings → Repairs & AI</button></div>' : ""}`); }
};
window.cfDiagnoseByName = name => { const c = (state.cfgCache || []).find(x => x.name === name); if (c) cfDiagnose(c.id); else api("/api/configs").then(d => { const x = d.configs.find(y => y.name === name); if (x) cfDiagnose(x.id); }); };
INFO.actions = ["Smart repair", "Finds why a tunnel is down and fixes it: restart, rebuild, another tunnel type or another Iran server - and says which tunnel is best on today's network."];
const apAdvisorCard = (tl, pick) => `<div class="card" data-tab="adv"><h3>🧭 Network advisor — which tunnel is best today <span class="sp"></span></h3>
      <div class="frm"><label>Tunnel <small>(its two servers are tested)</small><select id="ap-tun" onchange="ap.tid=this.value;localStorage.setItem('nk.ap.tid',this.value)">${tl.map(t => `<option value="${t.id}" ${pick && t.id === pick.id ? "selected" : ""}>${esc(t.name)} — ${esc(t.iran_name)} ⇄ ${esc(t.foreign_name)} (${esc(t.type)})</option>`).join("")}</select></label>
        <div class="frm-act" style="align-self:end"><button class="pri" onclick="apRecommend()" ${tl.length ? "" : "disabled"}>Find the best tunnel today</button></div></div>
      <small class="mut">Builds a temporary tunnel of each type between the two servers (TCP Mux over GRE, BackPack TCP Mux, BackPack WS TLS Mux, Chisel TLS, BackPack Direct UDP), measures latency and loss, removes it again. About a minute per type. Nothing of your real tunnel changes until you press “Use”.</small>
      <div id="ap-adv"></div></div>`;
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
window.apDiagnose = async (id, opts = {}) => {
  showModal("<b>🩺 Diagnosis</b>", '<div class="mut" style="padding:20px;text-align:center">Looking at both servers, the route and the tunnel… (about 15 s)</div>', "wide");
  try {
    const d = await api(`/api/autopilot/diagnose/${id}`);
    const sw = d.can_switch && d.cause !== "healthy", mv = d.can_move && d.cause !== "healthy";
    showModal(`<span class="st ${d.state}"></span><b>${esc(d.name)}</b> <span class="mut">${esc(d.iran_name)} ⇄ ${esc(d.foreign_name)}</span>`, `
      <div class="ibox"><h5><i>🩺</i>Cause</h5><div style="padding:6px 0;font-size:13.5px"><b class="${d.cause === "healthy" ? "gw-ok" : "err"}">${esc(d.summary)}</b></div></div>
      ${d.ai ? `<div class="ibox" style="margin-top:10px"><h5><i>✨</i>AI</h5><div style="padding:6px 0;font-size:13px">${esc(d.ai)}</div></div>` : ""}
      <div class="ibox" style="margin-top:10px"><h5><i>🔎</i>What was checked</h5>${d.findings.map(f => irow(esc(f.text), f.ok ? '<b class="gw-ok">✓</b>' : '<b class="err">✕</b>')).join("")}</div>
      ${d.cause === "healthy" ? "" : `<div class="wgroup" style="margin-top:12px"><h4>What can be done</h4><div class="wtools">
        ${d.ladder.includes("restart") || d.ladder.includes("relink") ? `<a class="wtool" onclick="apRun(${id}, '${d.ladder.includes("switch") ? "switch" : "rebuild"}')"><i>🛠</i><b>Smart fix</b><small>${d.ladder.includes("restart") ? "restart what is not running, then rebuild if needed" : "build the link again with a new inner network and key, then try the other types"}</small></a>` : ""}
        ${sw ? `<a class="wtool" onclick="apRun(${id}, 'switch')"><i>🔀</i><b>Switch the tunnel type</b><small>test the other types on this route today and keep the best that works</small></a>` : ""}
        ${mv ? `<a class="wtool" onclick="apMove(${id})"><i>🚚</i><b>Move to another Iran server</b><small>when the route to this foreign server is cut from that Iran server</small></a>` : ""}
        <a class="wtool" onclick="closeModal();apTune(${id})"><i>🧭</i><b>Network advisor</b><small>rank every tunnel type on today's network</small></a>
        <a class="wtool" onclick="apExplain(${id})"><i>💬</i><b>Ask the AI</b><small>what it makes of this diagnosis and what to do</small></a>
        <a class="wtool" onclick="apDiagnose(${id})"><i>↻</i><b>Check again</b><small>run the diagnosis once more</small></a></div></div>`}
      <div id="ap-story" class="fix hidden" style="margin-top:10px"></div>`, "wide");
    if (opts.auto && d.cause !== "healthy") {   // "Fix with AI": no further question - restart/rebuild (or switch the type when the network cuts this one)
      const steps = d.ladder.filter(s => s !== "move");
      if (steps.length) apStart(id, steps.includes("switch") ? "switch" : "rebuild");
      else toast("This one needs your choice: another Iran server (Move), or the servers must come back first", "bad");
    }
  } catch (e) { showModal("<b>🩺 Diagnosis</b>", `<div class="err">${esc(e.message)}</div>`, "wide"); }
};
async function apStart(id, level) {
  try { const r = await api("/api/autopilot/smart-fix", {method: "POST", body: JSON.stringify({tunnel_id: id, level})}); const box = $("#ap-story"); box.classList.remove("hidden"); box.innerHTML = '<div class="fx-line run"><i></i>Starting…</div>'; followStory(r.id, box, 700); } catch (e) { toast("✕ " + e.message, "bad"); }
}
window.apTune = id => { ap.tid = String(id); localStorage.setItem("nk.ap.tid", ap.tid); refresh(); };
window.apRun = async (id, level) => {
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
function cfFlag(name) { for (const t of String(name || "").toLowerCase().split(/[^a-z]+/)) if (FLAGS[t]) return `<small class="cc" title="${esc(FLAGS[t].toUpperCase())}">${esc(FLAGS[t].toUpperCase())}</small> `; return ""; }   // the country code as small text instead of a flag picture: calmer lists
const pingCls = v => v == null ? "mut" : v < 800 ? "ok" : v < 1000 ? "warn" : "bad";   // config delay: under 800 ms green, 800-999 yellow, above red
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
      ${names.includes(cf.group) ? `<span class="sp"></span><button class="sm" onclick="cfSubRefresh(${sub ? sub.id : 0}, '${esc(cf.group)}')" title="Add the new nodes of this subscription at once">⟳ Sync</button><button class="sm" onclick="cfSubManage('${esc(cf.group)}')" title="Tick which nodes of this group are kept on the probe">✎ Edit</button>` : ""}</div>` : "";
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
    <div class="mgrid">${chartCard("Delay", rows.map(r => [r[0], r[1] ? r[2] : null]), {unit: " ms", warn: 800, bad: 1000, fmt: v => Math.round(v)})}
      <div class="mcard"><h6>Outages <span class="mval">${out.length}</span></h6>${out.length ? out.slice(-8).reverse().map(o => `<div class="irow"><span>${fmtT(o.from, 1)}</span><div class="err">${durText(o.to - o.from)}${o.open ? " (still down)" : ""}</div></div>`).join("") : '<div class="mut chart-empty">no outage in this range</div>'}</div></div>`;
}window.cfQ = v => { cf.q = v.trim().toLowerCase(); clearTimeout(qT); qT = setTimeout(refresh, 150); };
window.cfView = v => { cf.view = v; localStorage.setItem("nk.cfview", v); refresh(); };
window.cfGroup = g => { cf.group = g; localStorage.setItem("nk.cfgroup", g); refresh(); };
async function cfInView() { const d = await api("/api/configs"); return d.configs.filter(c => cf.group === "__all" ? true : cf.group === "__manual" ? !c.group : c.group === cf.group); }
window.cfSpeedAll = async () => { const ids = (await cfInView()).filter(c => c.enabled).map(c => c.id); if (!ids.length) return; if (!await gconfirm(`Speed test ${ids.length} configs one after the other? About a minute each — the page keeps working meanwhile.`)) return; let done = 0, bad = 0; for (const id of ids) { try { await api(`/api/configs/${id}/speedtest`, {method: "POST"}); done++; } catch (e) { bad++; } toast(`Speed tests: ${done} done, ${bad} failed, ${ids.length - done - bad} to go`); } toast(`✓ Speed tests finished: ${done} ok, ${bad} failed`, bad ? "bad" : "ok"); refresh(); };
window.cfDeleteAll = async () => { const mine = await cfInView(); if (!mine.length) return; const where = cf.group === "__all" ? "ALL" : `all ${cf.group === "__manual" ? "manual" : cf.group}`; if (!await gconfirm(`Delete ${where} ${mine.length} configs from the probe?`)) return; try { const r = await api("/api/configs/delete-many", {method: "POST", body: JSON.stringify({ids: mine.map(c => c.id)})}); toast(`✓ ${r.deleted} configs deleted`, "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } refresh(); };
window.cfGroupDel = async g => { const [d, subs] = await Promise.all([api("/api/configs"), api("/api/subscriptions")]); const mine = d.configs.filter(c => (c.group || "") === g); const s = subs.subscriptions.find(x => x.name === g); const a = mine.length ? await gprompt(`Remove the group "${g}" (${mine.length} configs)?\nType DELETE to delete its configs from the probe too, or KEEP to only forget the group and keep the configs as manual ones:`) : "DELETE"; if (a !== "DELETE" && a !== "KEEP") return; if (a === "DELETE" && mine.length) { try { await api("/api/configs/delete-many", {method: "POST", body: JSON.stringify({ids: mine.map(c => c.id)})}); } catch (e) { toast("✕ " + e.message, "bad"); } } else for (const c of mine) { try { await api(`/api/configs/${c.id}`, {method: "PUT", body: JSON.stringify({group: ""})}); } catch (e) { /* keep going */ } } if (s) { try { await api(`/api/subscriptions/${s.id}`, {method: "DELETE"}); } catch (e) { /* fine */ } } if (cf.group === g) cfGroup("__all"); toast("✓ Group removed", "ok"); refresh(); };
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
  if (k === "link") return showModal("<b>🔗 Add a config</b>", `<form class="frm" onsubmit="return cfAdd(event)"><label style="grid-column:1/-1">Link<textarea name="uri" class="ob-raw" required placeholder="vless://…  vmess://…  trojan://…  ss://…"></textarea></label><div class="frm-act"><button class="pri" type="submit">Add to the probe</button><button type="button" onclick="cfNew()">← Back</button></div></form>`);
  if (k === "sub") return showModal("<b>📡 Import a subscription</b>", cfSubHtml(true));
}
window.cfCancel = () => { cf.edit = null; cf.sub = null; cf.add = null; refresh(); };
window.cfSync = async () => { toast("Asking the probe… (up to a minute)"); try { await api("/api/configs/sync", {method: "POST"}); toast("✓ Synced", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.cfAdd = async e => { e.preventDefault(); const f = new FormData(e.target); try { const r = await api("/api/configs", {method: "POST", body: JSON.stringify({uri: f.get("uri").trim(), name: "", group: ""})}); toast("✓ " + r.name + " added — the probe tests it within a minute", "ok"); cf.edit = null; cf.add = null; refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.cfDel = async (id, name) => {   // one config goes at once, no question asked (Delete all still asks)
  const row = document.querySelector(`[data-cf="${id}"]`); if (row) row.remove();
  try { await api(`/api/configs/${id}`, {method: "DELETE"}); toast(`✓ ${name} deleted`, "ok"); cf.open = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); refresh(); } };
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
    const [p, d, subs] = await Promise.all([api("/api/subscriptions/preview", {method: "POST", body: JSON.stringify({name: group, url: ""})}), api("/api/configs"), api("/api/subscriptions")]);
    const mine = d.configs.filter(c => c.group === group), sub = subs.subscriptions.find(s => s.name === group);
    cf.subEdit = {group, excluded: (sub && sub.excluded) || [], nodes: p.nodes.map(n => ({...n, cfg: mine.find(c => c.name === n.name || c.name === group + " " + n.name) || null}))};
    cfSubRender();
  } catch (e) { toast("✕ " + e.message, "bad"); }
};
/* the nodes of a subscription fall into categories by themselves: country nodes, then families like TCP-1…5 or Cloud-1…4, single ones under Other */
function subCategory(name) {
  if (cfFlag(name)) return "Countries";
  const stem = String(name || "").replace(/[\s_-]*\d+(\.\d+)?\s*(GB|TB|MB)?$/i, "").replace(/[\s_-]+$/, "").trim();
  return stem || "Other";
}
function cfSubRender() {
  const s = cf.subEdit; if (!s) return;
  const on = n => !s.excluded.includes(n.name);      // ticked = wanted: stays ticked after a delete, a sync loads it again
  const cats = {};
  s.nodes.forEach((n, i) => { const c = subCategory(n.name); (cats[c] = cats[c] || []).push(i); });
  Object.keys(cats).forEach(c => { if (c !== "Countries" && c !== "Other" && cats[c].length < 2) { (cats.Other = cats.Other || []).push(...cats[c]); delete cats[c]; } });
  const order = Object.keys(cats).sort((a, b) => (a === "Countries" ? -1 : b === "Countries" ? 1 : a === "Other" ? 1 : b === "Other" ? -1 : cats[b].length - cats[a].length || a.localeCompare(b)));
  const node = i => { const n = s.nodes[i]; return `<label><input type="checkbox" data-i="${i}" ${on(n) ? "checked" : ""}> ${cfFlag(n.name)}${esc(n.name)} <small class="mut">${esc(n.proto)}${n.cfg ? ` · <b class="${n.cfg.status === "up" ? "gw-ok" : "err"}">${n.cfg.status}</b>${n.cfg.ping_ms != null && n.cfg.status === "up" ? " " + Math.round(n.cfg.ping_ms) + " ms" : ""}` : " · not on the probe"}</small></label>`; };
  const group = c => `<div class="sub-cat" data-cat="${esc(c)}"><h4>${esc(c)} <small class="mut">${cats[c].filter(i => on(s.nodes[i])).length} of ${cats[c].length}</small><span class="sp"></span><a class="mut" onclick="subCatTick(this, true)">tick all</a><a class="mut" onclick="subCatTick(this, false)">untick all</a></h4><div class="ob-inbounds sub-nodes">${cats[c].map(node).join("")}</div></div>`;
  showModal(`<b>✎ ${esc(s.group)}</b> <span class="pill">${s.nodes.filter(on).length} of ${s.nodes.length} wanted · ${s.nodes.filter(n => n.cfg).length} on the probe</span>`,
    `<div class="toolbar"><a class="mut" onclick="document.querySelectorAll('#sub-nodes input').forEach(x=>x.checked=true)">tick all</a> · <a class="mut" onclick="document.querySelectorAll('#sub-nodes input').forEach(x=>x.checked=false)">untick all</a><span class="sp"></span><small class="mut">ticked = wanted: the group follows the subscription and keeps these. Unticked ones are removed and never come back with a sync.</small></div>
     <div id="sub-nodes" class="sub-cats" style="max-height:56vh;overflow:auto">${order.map(group).join("")}</div>
     <div class="frm-act"><button class="pri" onclick="cfSubApply()">Apply</button></div>`, "wide");
}
window.subCatTick = (a, val) => a.closest(".sub-cat").querySelectorAll("input").forEach(x => x.checked = val);
window.cfSubApply = async () => {
  const s = cf.subEdit; if (!s) return;
  const ticked = []; document.querySelectorAll("#sub-nodes input").forEach(x => { ticked[+x.dataset.i] = x.checked; });   // by index: the boxes are shown in categories, not in subscription order
  const unticked = s.nodes.filter((n, i) => !ticked[i]);
  const dropping = unticked.filter(n => n.cfg).length;
  if (dropping && !await gconfirm(`Remove ${dropping} configs of ${s.group} from the probe? They stay unticked and a sync will not bring them back.`)) return;
  toast("Applying…");
  try {
    await api("/api/subscriptions/excluded", {method: "PUT", body: JSON.stringify({name: s.group, excluded: unticked.map(n => n.name)})});
    const r = await api("/api/subscriptions/sync", {method: "POST", body: JSON.stringify({name: s.group})});
    toast(`✓ ${r.added.length} added · ${r.updated.length} updated · ${r.removed} removed` + (r.failed.length ? ` · ${r.failed.length} failed: ${r.failed[0]}` : ""), r.failed.length ? "bad" : "ok");
  } catch (e) { toast("✕ " + e.message, "bad"); }
  cf.subEdit = null; closeModal();
};
window.cfSubPreview = async e => { e.preventDefault(); const f = new FormData(e.target); cf.sub.name = f.get("name").trim(); cf.sub.url = f.get("url").trim(); toast("Downloading…"); try { cf.sub.nodes = (await api("/api/subscriptions/preview", {method: "POST", body: JSON.stringify({name: cf.sub.name, url: cf.sub.url})})).nodes; toast("✓ " + cf.sub.nodes.length + " nodes", "ok"); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.cfSubImport = async () => { const picks = [...document.querySelectorAll(".ob-inbounds input:checked")].map(x => x.value); if (!picks.length) { toast("Tick at least one node", "bad"); return; } toast("Adding " + picks.length + " nodes… (a few seconds each)"); try { const r = await api("/api/subscriptions/import", {method: "POST", body: JSON.stringify({name: cf.sub.name, url: cf.sub.url, picks})}); toast(`✓ ${r.added.length} added, ${r.skipped.length} skipped, ${r.failed.length} failed`, r.failed.length ? "bad" : "ok"); cf.sub = null; cf.add = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* Sync: no window. The saved link is read and every node that is not on the probe yet is added in one go. */
window.cfSubRefresh = async (id, name) => {
  if (!id) { cf.sub = {name, url: "", nodes: null, saved: false}; cf.edit = null; refresh(); return; }   // no saved link: ask for it once
  try {
    const r = await api("/api/subscriptions/sync", {method: "POST", body: JSON.stringify({name})});
    const same = !r.added.length && !r.updated.length && !r.removed && !r.failed.length;
    toast(same ? "✓ The list already matches the subscription" : `✓ ${r.added.length} added · ${r.updated.length} updated · ${r.removed} removed` + (r.failed.length ? ` · ${r.failed.length} failed: ${r.failed[0]}` : ""), r.failed.length ? "bad" : "ok");
  } catch (e) { toast("✕ " + e.message, "bad"); }
  refresh();
};
window.cfSubDel = async id => { if (!await gconfirm("Forget this subscription record? The configs stay.")) return; await api(`/api/subscriptions/${id}`, {method: "DELETE"}); refresh(); };

/* ---- panels (Marzban / PasarGuard): health, nodes, users, hosts, outbounds */
const pn = {id: localStorage.getItem("nk.pn") || "", tab: "users", add: false, edit: false, users: null, usersKey: "", uq: "", ustatus: "", uoff: 0, user: null, hosts: null, hostsKey: "", hostEdit: null, core: null, raw: false, logs: null};
INFO.panels = ["Panels", "Your Marzban and PasarGuard panels: are they up, are their nodes connected, their users, the hosts in the subscription links, and the Xray outbounds of the core — every core change is backed up first."];
const GB = 1024 ** 3, gb = v => v == null ? "∞" : v === 0 ? "∞" : (v / GB).toFixed(v / GB >= 100 ? 0 : 1) + " GB";
PAGES.panels = async () => {
  const list = (await api("/api/panels")).panels; state.panelsCache = list;
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
      <div class="tile click" onclick="pnNodesWin()" title="Open the nodes of this panel"><small>Nodes</small><b>${(p.nodes || []).filter(n => n.fails === 0 || ["connected", "healthy"].includes(n.status)).length} / ${(p.nodes || []).length}</b><span>connected</span></div>
      <div class="tile"><small>Load</small><b>${s.cpu != null ? Math.round(s.cpu) + "%" : "—"}</b><span>cpu · ram ${s.mem_pct ?? "—"}%</span></div>
      <div class="tile"><small>Traffic</small><b>${s.bw_in != null ? fmtB(s.bw_in + (s.bw_out || 0)) : "—"}</b><span>↓${s.bw_in != null ? fmtB(s.bw_in) : "—"} ↑${s.bw_out != null ? fmtB(s.bw_out) : "—"}</span></div></div>
    <div class="tabs" style="margin-bottom:10px">${[["users", `Users${s.users_total != null ? ` <small>${s.users_total}</small>` : ""}`], ["hosts", "Hosts"]].map(([t, l]) => `<button class="${pn.tab === t ? "on" : ""}" onclick="pnTab('${t}')">${l}</button>`).join("")}</div>
    <div id="pn-body"><div class="loading"><i class="spin"></i><span>Loading…</span></div></div>`;
  // the heavy lists are read from the panel only when their tab is open (and kept until something changes)
  if (pn.tab === "users") pnUsersLoad(p.id);
  if (pn.tab === "hosts") pnHostsLoad(p.id);
};
function pnOverviewHtml(p) {
  const s = p.stats || {}, nodes = p.nodes || [], bad = nodes.filter(n => !["connected", "healthy", "disabled"].includes(n.status));
  return `<div class="card"><h3>Nodes <span class="sp"></span><span class="pill ${bad.length ? "err" : "ok"}">${bad.length ? bad.length + " not connected" : nodes.length ? "all connected" : "none"}</span></h3>${pnNodesHtml(p)}</div>
    <div class="card"><h3>About this panel</h3><div class="kv"><div><span>type</span> ${esc(p.type)} · <span>version</span> ${esc(p.version || "—")}</div><div><span>users</span> ${s.users_total ?? "—"} total · ${s.users_active ?? "—"} active · ${s.users_online ?? "—"} online right now</div>
      <div><span>test config</span> ${s.test_user ? `<b class="gw-ok">✓</b> <span class="mono">${esc(s.test_user)}</span> — kept alive and measured on the probe as the group <b>${esc(p.name)}</b>` : "none yet — press <b>Test config on probe</b> above"}</div>
      <div><span>checked</span> ${p.checked_at ? ago(p.checked_at) + " ago" : "never"} · every 2 minutes${p.last_error ? ` · <span class="err">${esc(p.last_error)}</span>` : ""}</div></div>
      <small class="mut">Open <b>Users</b> for the user list (search, status filter, edit, bulk actions) and <b>Hosts</b> for the address:port of every subscription link — they are read from the panel only when you open them.</small></div>`;
}
function fmtB(b) { if (b == null) return "—"; const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; } return b.toFixed(i >= 3 ? 2 : 0) + " " + u[i]; }
function pnNodesHtml(p) {
  const rows = (p.nodes || []).map(n => { const ok = ["connected", "healthy", "disabled"].includes(n.status); return `<tr class="${ok ? (n.status === "disabled" ? "" : "up") : "down"}"><td class="name">${esc(n.name)}</td><td><span class="st ${ok ? (n.status === "disabled" ? "" : "up") : "down"}">${esc(n.status || "?")}</span></td><td class="mut">${esc(n.address)}</td><td class="mut">${esc(n.xray || "")}</td><td class="mut" title="${esc(n.message)}">${esc(n.message).slice(0, 60)}${n.fails ? ` <span class="pill err">${n.fails} checks</span>` : ""}</td>
      <td><button class="sm" onclick="pnNodeLogs(${p.id}, ${n.id}, '${esc(n.name)}')">Log</button> <button class="sm" onclick="pnNodeReconnect(${p.id}, ${n.id})">Reconnect</button></td></tr>`; }).join("");
  return `<table class="tbl"><colgroup><col style="width:18%"><col style="width:12%"><col style="width:18%"><col style="width:10%"><col><col style="width:200px"></colgroup><thead><tr><th>Node</th><th>Status</th><th>Address</th><th>Xray</th><th>Message</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="6" class="mut">no nodes</td></tr>'}</tbody></table>
    ${pn.logs ? `<div class="card"><h3>Log of ${esc(pn.logs.name)} <span class="sp"></span><button class="sm" onclick="pn.logs=null;refresh()">Close</button></h3><pre class="gl-out">${esc(pn.logs.text || "(empty)")}</pre></div>` : ""}`;
}
window.pnPick = v => { pn.tab = "users"; pn.id = v; localStorage.setItem("nk.pn", v); pn.users = pn.hosts = pn.core = pn.user = pn.logs = null; pn.usersKey = pn.hostsKey = ""; pn.edit = pn.add = false; refresh(); };
window.pnTab = t => { pn.tab = t; pn.user = null; pn.hostEdit = null; refresh(); };
window.pnNodesWin = () => { const p = (state.panelsCache || []).find(x => String(x.id) === pn.id); if (p) showModal(`<b>Nodes of ${esc(p.name)}</b>`, pnNodesHtml(p), "wide"); };
window.pnReload = () => { pn.users = pn.hosts = null; pn.usersKey = pn.hostsKey = ""; refresh(); };
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
  const key = `${id}|${pn.uq}|${pn.ustatus}|${pn.uoff}`;
  if (!pn.users || pn.usersKey !== key) {   // read from the panel only when the question changed, not on every refresh
    try { pn.users = await api(`/api/panels/${id}/users?search=${encodeURIComponent(pn.uq)}&status=${pn.ustatus}&offset=${pn.uoff}&limit=40&sort=-created_at`); pn.usersKey = key; } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  }
  const u = pn.users, now = Date.now() / 1000;
  const exp = x => !x.expire_date ? "∞" : (typeof x.expire === "number" ? (x.expire < now ? '<span class="err">expired</span>' : Math.ceil((x.expire - now) / 86400) + " d") : esc(x.expire_date));
  box.innerHTML = `<div class="toolbar"><input id="pn-uq" placeholder="Search user…" value="${esc(pn.uq)}" oninput="pnUQ(this.value)"><select onchange="pn.ustatus=this.value;pn.uoff=0;refresh()">${["", "active", "disabled", "limited", "expired", "on_hold"].map(s => `<option value="${s}" ${pn.ustatus === s ? "selected" : ""}>${s || "all statuses"}</option>`).join("")}</select><span class="mut">${u.total} users</span><button class="sm" onclick="pnReload()" title="Read the list from the panel again">↻</button><span class="sp"></span>
      <button class="sm" onclick="pnBulk(${id}, 'disable')">Disable ticked</button><button class="sm" onclick="pnBulk(${id}, 'enable')">Enable ticked</button><button class="sm" onclick="pnBulkRenew(${id})">Renew ticked…</button>
      <button class="sm" ${pn.uoff ? "" : "disabled"} onclick="pn.uoff=Math.max(0,pn.uoff-40);refresh()">‹</button><button class="sm" ${pn.uoff + 40 < u.total ? "" : "disabled"} onclick="pn.uoff+=40;refresh()">›</button></div>
    <table class="tbl"><colgroup><col style="width:30px"><col style="width:22%"><col style="width:10%"><col style="width:16%"><col style="width:10%"><col><col style="width:170px"></colgroup>
      <thead><tr><th></th><th>User</th><th>Status</th><th>Traffic</th><th>Expires</th><th>Note</th><th></th></tr></thead>
      <tbody>${u.users.map(x => `<tr class="${x.status === "active" ? "up" : x.status === "disabled" ? "" : "down"}"><td><input type="checkbox" class="pn-uchk" value="${esc(x.username)}"></td><td class="name">${esc(x.username)}</td><td><span class="st ${x.status === "active" ? "up" : x.status === "disabled" ? "" : "down"}">${esc(x.status)}</span></td>
        <td class="num">${fmtB(x.used || 0)} / ${gb(x.limit)}</td><td class="num">${exp(x)}</td><td class="mut">${esc(x.note)}</td>
        <td><button class="sm" onclick="pnUserOpen(${id}, '${esc(x.username)}')">Edit</button> <button class="sm" onclick="pnUAct(${id}, '${esc(x.username)}', '${x.status === "active" ? "disable" : "enable"}')">${x.status === "active" ? "Disable" : "Enable"}</button> <button class="sm" onclick="pnUAct(${id}, '${esc(x.username)}', 'reset')">Reset</button></td></tr>
`).join("") || '<tr><td colspan="7" class="mut">nobody matches</td></tr>'}</tbody></table>`;
  const q = $("#pn-uq"); if (q && pn.uq) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
}
function pnUserHtml(id, u) {   // the user window, laid out like the Marzban dashboard's: usage on top, the fields, the subscription, the actions
  if (u.sub && u.sub.startsWith("/")) { const pp = (state.panelsCache || []).find(x => String(x.id) === String(id)); if (pp) u = {...u, sub: String(pp.url || "").replace(/\/+$/, "") + u.sub}; }
  const nm = esc(u.username), pct = u.limit ? Math.min(100, Math.round((u.used || 0) * 100 / u.limit)) : 0, left = u.expire ? Math.ceil((u.expire - Date.now() / 1000) / 86400) : null;
  return `<div class="ue-top"><div class="ue-use"><div class="row2"><b>${fmtB(u.used || 0)}</b><span class="mut">of ${u.limit ? gb(u.limit) : "unlimited"}${u.limit ? " · " + pct + " %" : ""}</span></div>${u.limit ? `<div class="ue-bar"><i class="${pct >= 95 ? "bad" : pct >= 80 ? "warn" : ""}" style="width:${pct}%"></i></div>` : ""}</div>
      <div class="ue-facts"><div><span>Lifetime</span><b>${fmtB(u.lifetime || 0)}</b></div><div><span>Expires</span><b>${u.expire_date ? esc(u.expire_date) + (left != null ? ` <small class="${left < 0 ? "err" : "mut"}">${left < 0 ? "expired" : left + " d"}</small>` : "") : "never"}</b></div><div><span>Created</span><b>${esc(String(u.created_at || "").slice(0, 10)) || "—"}</b></div><div><span>Online</span><b>${esc(String(u.online_at || "never").slice(0, 16).replace("T", " "))}</b></div></div></div>
    <form class="frm ue-form" onsubmit="return pnUserSave(event, ${id}, '${nm}')">
      <label>Status<select name="status">${["active", "disabled", "on_hold"].map(s => `<option ${u.status === s ? "selected" : ""}>${s}</option>`).join("")}</select></label>
      <label>Data limit (GB) <small>(0 = unlimited)</small><input name="limit_gb" type="number" step="0.1" min="0" value="${u.limit ? (u.limit / GB).toFixed(1) : 0}"></label>
      <label>Reset usage every<select name="reset">${["no_reset", "day", "week", "month", "year"].map(s => `<option ${u.reset === s ? "selected" : ""}>${s}</option>`).join("")}</select></label>
      <label>Expires <small>(empty = never)</small><input name="expire_date" type="date" value="${esc(u.expire_date)}"></label>
      <label class="ue-note">Note<input name="note" value="${esc(u.note)}" placeholder="optional"></label>
      <div class="frm-act ue-save"><button class="pri" type="submit">Save</button><button type="button" onclick="closeModal()">Cancel</button></div></form>
    ${u.sub ? `<div class="ue-sub"><span class="mut">Subscription</span><span class="mono ell">${esc(u.sub)}</span><button class="sm" onclick="navigator.clipboard.writeText('${esc(u.sub)}');toast('✓ copied','ok')">Copy</button></div>` : ""}
    <div class="ue-acts"><button class="sm" onclick="(async()=>pnUAct(${id}, '${nm}', 'renew', await gprompt('Add how many days?', '30')))()">+ Add days</button><button class="sm" onclick="pnUAct(${id}, '${nm}', 'reset')">Reset usage</button><button class="sm" onclick="pnUAct(${id}, '${nm}', 'revoke')">Revoke subscription</button><button class="sm" onclick="pnUAct(${id}, '${nm}', '${u.status === "disabled" ? "enable" : "disable"}')">${u.status === "disabled" ? "Enable" : "Disable"}</button><span class="sp"></span><button class="sm danger" onclick="(async()=>{if(await gconfirm('Delete ${nm} from the panel?'))pnUAct(${id}, '${nm}', 'delete')})()">Delete user</button></div>`;
}window.pnUQ = v => { pn.uq = v.trim(); pn.uoff = 0; clearTimeout(qT); qT = setTimeout(refresh, 300); };
window.pnUserOpen = async (id, name) => { try { pn.user = (await api(`/api/panels/${id}/users/${encodeURIComponent(name)}`)).user; const u = pn.user; showModal(`<span class="st ${u.status === "active" ? "online" : u.status === "disabled" ? "" : "offline"}"></span><b>${esc(u.username)}</b> <span class="pill">${esc(u.status)}</span>`, pnUserHtml(id, u), "wide ue"); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.pnUserSave = async (e, id, name) => { e.preventDefault(); const f = new FormData(e.target); try { await api(`/api/panels/${id}/users/${encodeURIComponent(name)}`, {method: "PUT", body: JSON.stringify({status: f.get("status"), expire_date: f.get("expire_date"), limit_gb: +f.get("limit_gb"), reset: f.get("reset"), note: f.get("note")})}); toast("✓ Saved", "ok"); pn.user = null; closeModal(); pn.users = null; refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.pnUAct = async (id, name, action, value) => { if (action === "renew" && !value) return; try { await api(`/api/panels/${id}/users/${encodeURIComponent(name)}/action`, {method: "POST", body: JSON.stringify({action, value})}); toast("✓ " + action + " done", "ok"); pn.user = null; closeModal(); pn.users = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.pnBulk = async (id, action, value) => { const names = [...document.querySelectorAll(".pn-uchk:checked")].map(x => x.value); if (!names.length) { toast("Tick some users first", "bad"); return; } try { const r = await api(`/api/panels/${id}/users-bulk`, {method: "POST", body: JSON.stringify({action, value, usernames: names})}); toast(`✓ ${r.done} done${r.failed_count ? ", " + r.failed_count + " failed" : ""}`, r.failed_count ? "bad" : "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.pnBulkRenew = async id => { const d = await gprompt("Add how many days to the ticked users?", "30"); if (d) pnBulk(id, "renew", +d); };

/* hosts */
async function pnHostsLoad(id) {
  const box = $("#pn-body"); if (!box) return;
  if (!pn.hosts || pn.hostsKey !== String(id)) {
    try { pn.hosts = (await api(`/api/panels/${id}/hosts`)).hosts; pn.hostsKey = String(id); } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  }
  box.innerHTML = `<div class="toolbar"><span class="mut">${pn.hosts.length} hosts — the address:port that goes into every subscription link</span><button class="sm" onclick="pnReload()" title="Read the hosts from the panel again">↻</button><span class="sp"></span><button class="sm" onclick="pnHostsToggle(${id}, true)">Disable ticked</button><button class="sm" onclick="pnHostsToggle(${id}, false)">Enable ticked</button></div>
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
INFO.bot = ["Shop bot", "Your sales bot, run from here: payments to confirm, orders, users, plans and prices, the panels it sells from, discounts and gift codes, broadcasts, tickets, every text and switch."];
const sb = {tab: localStorage.getItem("nk.sb.tab") || "overview", info: null, infoAt: 0, uq: "", table: "users", tpage: 0, tq: "", sql: "", qres: null, payState: "confirm", payPage: 0, payQ: "", ordQ: "", ordPage: 0, ordActive: false,   // tool defaults to the Mini App card
            tkState: "open", tkPage: 0, txKind: "text", txQ: "", txData: null, tool: "miniapp", stData: null};
const SB_TABS = [["overview", "Overview"], ["payments", "Payments"], ["orders", "Orders"], ["users", "Users"], ["plans", "Plans"], ["categories", "Categories"], ["servers", "Servers"], ["discounts", "Discounts"], ["gifts", "Gift codes"], ["broadcast", "Broadcast"], ["tickets", "Tickets"], ["texts", "Texts"], ["settings", "Settings"], ["tools", "Tools"]];
const fmt = n => (+n || 0).toLocaleString();
const disc = v => { if (v == null || v === "" || v === "NULL") return 0; try { const j = JSON.parse(v); return +(j && j.normal) || 0; } catch (e) { return +v || 0; } };   // the bot keeps the discount as JSON
const money = v => v == null ? "—" : fmt(v) + " T";
const tsFa = v => { const n = +v; return n > 1000000000 ? when(n) : (v || "—"); };
const sbT = (k, v) => `<td title="${esc(v == null ? "" : v)}">${esc(v == null || v === "NULL" ? "" : v)}</td>`;
const sbPager = (page, total, fn) => total > 40 ? `<span class="sp"></span><small class="mut">${fmt(total)} rows</small><button class="sm" ${page ? "" : "disabled"} onclick="${fn}(${page - 1})">‹</button><button class="sm" ${(page + 1) * 40 < total ? "" : "disabled"} onclick="${fn}(${page + 1})">›</button>` : `<span class="sp"></span><small class="mut">${fmt(total)} rows</small>`;
window.sbTab = t => { if (sb.tab !== t) { sb.tool = "miniapp"; sb.payState = "confirm"; sb.payPage = 0; } sb.tab = t; localStorage.setItem("nk.sb.tab", t); refresh(); };
PAGES.bot = async () => {
  const pg = $("#page");
  if (!sb.info || Date.now() - sb.infoAt > 60000) {   // the bot's state comes over SSH: kept a minute so the tabs switch at once
    try { sb.info = await api("/api/shopbot"); sb.infoAt = Date.now(); } catch (e) { pg.innerHTML = `<div class="empty">${esc(e.message)}${/Settings/.test(e.message) ? ' — <a onclick="go(\'settings\')">open Settings</a>' : ""}</div>`; return; }
  }
  const d = sb.info, c = d.counts || {}, s = d.sales || {}, u = d.units || {};
  const php = Object.keys(u).find(k => /php/.test(k)), dbu = Object.keys(u).find(k => /mariadb|mysql/.test(k)), web = Object.keys(u).find(k => /caddy|nginx/.test(k));
  const unit = k => k ? `<span class="pill ${u[k] === "active" ? "ok" : "err"}">${esc(k)} ${esc(u[k])}</span>` : '<span class="pill err">missing</span>';
  const n = {pending: c.pending_pays || 0, tickets: d.tickets || 0, queue: d.queue || 0};
  pg.innerHTML = `<div class="toolbar sb-top">${d.installed ? '<span class="pill ok">● installed</span>' : '<span class="pill err">● not installed</span>'}${unit(php)}${unit(dbu)}${unit(web)}<span class="pill ${d.cron ? "ok" : "err"}">cron ${d.cron ? d.cron + " jobs" : "off"}</span><span class="sp"></span>
      ${n.pending ? `<button class="sm pri" onclick="sbTab('payments')">💳 ${n.pending} payment${n.pending > 1 ? "s" : ""} to confirm</button>` : ""}${n.tickets ? `<button class="sm" onclick="sbTab('tickets')">🎫 ${n.tickets} open ticket${n.tickets > 1 ? "s" : ""}</button>` : ""}${n.queue ? `<button class="sm" onclick="sbTab('broadcast')">📣 ${n.queue} broadcast running</button>` : ""}<button class="sm" onclick="sb.infoAt=0;refresh()" title="Read the bot's state again">↻</button></div>
    <div class="tiles sb-tiles">
      <div class="tile"><small>Users</small><b>${fmt(c.users)}</b><span>+${s.new_users_today ?? "—"} today · +${s.new_users_week ?? "—"} this week</span></div>
      <div class="tile ok"><small>Today</small><b>${money((s.today || {}).income)}</b><span>${(s.today || {}).orders ?? "—"} orders</span></div>
      <div class="tile"><small>7 days</small><b>${money((s.week || {}).income)}</b><span>${(s.week || {}).orders ?? "—"} orders</span></div>
      <div class="tile"><small>30 days</small><b>${money((s.month || {}).income)}</b><span>${(s.month || {}).orders ?? "—"} orders · ${fmt(c.orders)} in all</span></div></div>
    ${d.stage_users != null && d.stage_users > (c.users || 0) ? `<div class="gw-out"><b>Real database waiting:</b> the staging copy on the bot server holds ${fmt(d.stage_users)} users, the live bot has ${fmt(c.users)}. <button class="pri sm" onclick="sbImport()">Import the real database now</button> <small class="mut">(the live database is dumped first; broadcast queues are not copied)</small></div>` : ""}
    <div class="tabs sb-tabs">${SB_TABS.map(([k, label]) => `<button class="${sb.tab === k ? "on" : ""}" onclick="sbTab('${k}')">${label}${k === "payments" && n.pending ? ` <small>${n.pending}</small>` : k === "tickets" && n.tickets ? ` <small>${n.tickets}</small>` : ""}</button>`).join("")}</div>
    <div id="sb-body"><div class="loading"><i class="spin"></i><span>Loading…</span></div></div>`;
  const load = {overview: sbOverview, payments: sbPays, orders: sbOrders, users: sbUsersLoad, plans: sbPlans, categories: sbCategories, servers: sbServers, discounts: sbDiscounts, gifts: sbGifts, broadcast: sbBroadcast, tickets: sbTickets, texts: sbTexts, settings: sbSettings, tools: sbTools}[sb.tab] || sbOverview;
  try { await load(); } catch (e) { const b = $("#sb-body"); if (b) b.innerHTML = `<div class="page-err"><b>This tab could not be loaded</b><span>${esc(e.message)}</span><button class="sm" onclick="refresh()">Try again</button></div>`; }
};
const sbBody = html => { const b = $("#sb-body"); if (b) b.innerHTML = html; };
/* ---- overview */
async function sbOverview() {
  const d = sb.info;
  sbBody(`<div class="grid2"><div class="card"><h3>Telegram</h3><div id="sb-tg" class="kv"><div class="mut">asking Telegram…</div></div></div>
    <div class="card"><h3>Panels the bot sells from <span class="sp"></span><a class="mut" onclick="sbTab('servers')">manage</a></h3>${(d.panels || []).map(p => `<div class="gw-row"><span><b>#${esc(p.id)}</b> ${esc(p.type)}</span><span class="mut">${esc(p.host)}</span><span></span></div>`).join("") || '<div class="mut">none yet — add one in Servers</div>'}</div></div>
    <div class="card"><h3>Latest bot errors <span class="sp"></span><small class="mut">${(d.backups || []).length ? "backups: " + esc(d.backups.join(", ")) : ""}</small></h3><pre class="gl-out">${esc((d.logs.errors || []).join("\n") || "(no errors logged)")}</pre>${(d.logs.php || []).length ? `<pre class="gl-out">${esc(d.logs.php.join("\n"))}</pre>` : ""}</div>`);
  try { const t = await api("/api/shopbot/telegram"); const box = $("#sb-tg"); if (box) box.innerHTML = `<div><span>bot</span> @${esc(t.bot.username || "?")} ${esc(t.bot.name || "")}</div><div><span>webhook</span> ${esc(t.webhook.url || "not set")}</div><div><span>pending updates</span> ${t.webhook.pending}</div>${t.webhook.last_error ? `<div><span>last error</span> <b class="err">${esc(t.webhook.last_error)}</b> ${ago(t.webhook.last_error_at)} ago</div>` : ""}`; }
  catch (e) { const box = $("#sb-tg"); if (box) box.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}
/* ---- payments */
async function sbPays() {
  const r = await api(`/api/shopbot/pays?state=${sb.payState}&page=${sb.payPage}&q=${encodeURIComponent(sb.payQ)}`);
  const st = s => s === "have_sent" ? '<span class="pill warn">receipt sent</span>' : s === "pending" ? '<span class="pill">awaiting receipt</span>' : s === "approved" ? '<span class="pill ok">approved</span>' : s === "declined" ? '<span class="pill err">declined</span>' : `<span class="pill">${esc(s)}</span>`;
  sbBody(`<div class="toolbar"><div class="tabs">${[["confirm", "To confirm"], ["pending", "Awaiting receipt"], ["approved", "Approved"], ["declined", "Declined"], ["all", "All"]].map(([k, l]) => `<button class="${sb.payState === k ? "on" : ""}" onclick="sb.payState='${k}';sb.payPage=0;refresh()">${l}</button>`).join("")}</div><input placeholder="user id, name, hash…" value="${esc(sb.payQ)}" oninput="sb.payQ=this.value.trim();sb.payPage=0;clearTimeout(qT);qT=setTimeout(refresh,350)">${sbPager(r.page, r.total, "sbPayPage")}</div>
    <table class="tbl"><colgroup><col style="width:6%"><col style="width:22%"><col style="width:12%"><col style="width:10%"><col style="width:16%"><col style="width:10%"><col></colgroup><thead><tr><th>#</th><th>User</th><th>Amount</th><th>Type</th><th>Asked</th><th>State</th><th></th></tr></thead>
    <tbody>${r.rows.map(p => `<tr class="${p.state === "have_sent" || p.state === "pending" ? "" : p.state === "approved" ? "up" : "down"}"><td class="num">${esc(p.id)}</td><td class="name"><a class="mut" onclick="sbUser('${esc(p.user_id)}')">${esc(p.name || p.user_id)}</a> <small class="mut">${p.username ? "@" + esc(p.username) : esc(p.user_id)}</small></td><td class="num"><b>${money(p.price)}</b></td><td>${esc(p.type || "")}</td><td class="mut">${tsFa(p.request_date)}</td><td>${st(p.state)}</td>
      <td><div class="td-act">${p.state === "have_sent" || p.state === "pending" ? `<button class="sm pri" onclick="sbPayDo(${p.id}, 'approve')">✓ Approve</button><button class="sm danger" onclick="sbPayDo(${p.id}, 'decline')">✕ Decline</button>` : p.approved_by && p.approved_by !== "NULL" ? `<small class="mut">by ${esc(p.approved_by)}</small>` : ""}</div></td></tr>`).join("") || '<tr><td colspan="7" class="empty">Nothing here.</td></tr>'}</tbody></table>`);
}
window.sbPayPage = p => { sb.payPage = p; refresh(); };
window.sbPayDo = async (id, act) => {
  let body = {};
  if (act === "approve") { if (!await gconfirm(`Approve payment #${id}? The amount goes to the user's wallet and the user is told on Telegram.`)) return; }
  else { const why = await gprompt("Why is it declined? (the user reads this)", ""); if (why == null) return; body = {reason: why}; }
  try { const r = await api(`/api/shopbot/pays/${id}/${act}`, {method: "POST", body: JSON.stringify(body)}); toast(act === "approve" ? `✓ Approved — wallet now ${money(r.wallet)}` : "✓ Declined", "ok"); sb.infoAt = 0; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
/* ---- orders */
async function sbOrders() {
  const r = await api(`/api/shopbot/orders?q=${encodeURIComponent(sb.ordQ)}&page=${sb.ordPage}&active=${sb.ordActive}`);
  sbBody(`<div class="toolbar"><input placeholder="user id, name, remark…" value="${esc(sb.ordQ)}" oninput="sb.ordQ=this.value.trim();sb.ordPage=0;clearTimeout(qT);qT=setTimeout(refresh,350)"><label class="chk"><input type="checkbox" ${sb.ordActive ? "checked" : ""} onchange="sb.ordActive=this.checked;sb.ordPage=0;refresh()"> active only</label>${sbPager(r.page, r.total, "sbOrdPage")}</div>
    <table class="tbl"><colgroup><col style="width:6%"><col style="width:20%"><col style="width:16%"><col style="width:12%"><col style="width:10%"><col style="width:14%"><col style="width:8%"><col></colgroup><thead><tr><th>#</th><th>User</th><th>Remark</th><th>Server</th><th>Paid</th><th>Expires</th><th>State</th><th></th></tr></thead>
    <tbody>${r.rows.map(o => `<tr class="${o.status === "1" ? "up" : "down"}"><td class="num">${esc(o.id)}</td><td class="name"><a class="mut" onclick="sbUser('${esc(o.userid)}')">${esc(o.name || o.userid)}</a> <small class="mut">${o.username ? "@" + esc(o.username) : ""}</small></td><td class="mono">${esc(o.remark || "")}</td><td>${esc(o.server || o.server_id || "")}</td><td class="num">${money(o.amount)}</td><td class="mut">${tsFa(o.expire_date)}</td><td>${o.status === "1" ? '<span class="pill ok">active</span>' : '<span class="pill">off</span>'}</td>
      <td><div class="td-act"><button class="sm ${o.status === "1" ? "danger" : ""}" onclick="sbOrderStatus(${o.id}, ${o.status === "1" ? "false" : "true"})">${o.status === "1" ? "Disable" : "Enable"}</button></div></td></tr>`).join("") || '<tr><td colspan="8" class="empty">No order.</td></tr>'}</tbody></table>`);
}
window.sbOrdPage = p => { sb.ordPage = p; refresh(); };
window.sbOrderStatus = async (id, active) => { try { await api(`/api/shopbot/orders/${id}/status`, {method: "PUT", body: JSON.stringify({active})}); toast("✓ Changed", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* ---- users */
async function sbUsersLoad() {
  const rows = sb.uq ? (await api("/api/shopbot/users?q=" + encodeURIComponent(sb.uq)).catch(e => { toast("✕ " + e.message, "bad"); return {users: []}; })).users : [];
  sbBody(`<div class="toolbar"><input id="sb-uq" placeholder="Telegram id, username, name or phone…" value="${esc(sb.uq)}" oninput="sbUQ(this.value)"><span class="mut">${sb.uq ? rows.length + " found" : "type to search the bot's users"}</span></div>
    ${rows.length ? `<table class="tbl"><colgroup><col style="width:13%"><col style="width:22%"><col style="width:11%"><col style="width:7%"><col style="width:8%"><col style="width:9%"><col></colgroup><thead><tr><th>Telegram id</th><th>Name</th><th>Wallet</th><th>Orders</th><th>Agent</th><th>Discount</th><th></th></tr></thead>
      <tbody>${rows.map(r => `<tr class="${r.step === "banned" ? "down" : "up"}"><td class="num"><a class="mut" onclick="sbUser('${esc(r.userid)}')">${esc(r.userid)}</a></td><td class="name"><a onclick="sbUser('${esc(r.userid)}')">${esc(r.name || "")}</a> <small class="mut">${r.username ? "@" + esc(r.username) : ""}</small></td><td class="num">${fmt(r.wallet)}</td><td class="num">${esc(r.orders)}</td><td>${r.is_agent === "1" ? '<span class="pill ok">agent</span>' : ""}</td><td class="num">${disc(r.discount_percent)}%</td>
        <td><div class="td-act"><button class="sm" onclick="sbUser('${esc(r.userid)}')">More</button><button class="sm" onclick="(async()=>sbU('${esc(r.userid)}', 'wallet', await gprompt('Add to the wallet (Toman, negative to take)', '10000')))()">± wallet</button><button class="sm ${r.step === "banned" ? "" : "danger"}" onclick="sbU('${esc(r.userid)}', '${r.step === "banned" ? "unban" : "ban"}')">${r.step === "banned" ? "Unban" : "Ban"}</button></div></td></tr>`).join("")}</tbody></table>` : ""}`);
  const q = $("#sb-uq"); if (q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
}
window.sbUQ = v => { sb.uq = v.trim(); clearTimeout(qT); qT = setTimeout(refresh, 350); };
window.sbU = async (uid, action, value) => { if ((action === "wallet" || action === "discount" || action === "wallet_set") && (value == null || value === "")) return; try { await api("/api/shopbot/users/action", {method: "POST", body: JSON.stringify({action, userid: uid, value})}); toast("✓ " + action.replace("_", " ") + " done", "ok"); if ($("#modal .sb-user")) sbUser(uid); else refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.sbUser = async uid => {
  showModal(`<b>User ${esc(uid)}</b>`, '<div class="sb-user loading"><i class="spin"></i><span>Reading…</span></div>', "wide");
  try {
    const d = await api(`/api/shopbot/users/${encodeURIComponent(uid)}`), u = d.user;
    showModal(`<span class="st ${u.step === "banned" ? "down" : "up"}"></span><b>${esc(u.name || uid)}</b> <span class="mono mut">${esc(uid)}</span> ${u.username ? `<span class="pill">@${esc(u.username)}</span>` : ""}${u.is_agent === "1" ? '<span class="pill ok">agent</span>' : ""}${u.step === "banned" ? '<span class="pill err">banned</span>' : ""}`, `<div class="sb-user">
      <div class="grid2">${ibox("👤", "Account", [irow("Wallet", `<b>${money(u.wallet)}</b>`), irow("Phone", esc(u.phone || "—")), irow("Joined", tsFa(u.date)), irow("Discount", disc(u.discount_percent) + " %"), irow("Referred by", esc(u.refered_by && u.refered_by !== "NULL" && u.refered_by !== "0" ? u.refered_by : "—")), irow("Brought in", esc(d.referred) + " users"), irow("Free trial", esc(u.freetrial || "—")), irow("Step", `<small class="mono">${esc((u.step || "").slice(0, 40))}</small>`)])}
      ${ibox("🛠", "Actions", [`<div class="wtools sb-acts"><a class="wtool" onclick="(async()=>sbU('${esc(uid)}', 'wallet', await gprompt('Add to the wallet (Toman, negative to take)', '10000')))()"><i>💰</i><b>± Wallet</b><small>add or take</small></a><a class="wtool" onclick="(async()=>sbU('${esc(uid)}', 'wallet_set', await gprompt('Set the wallet to (Toman)', '${esc(u.wallet || 0)}')))()"><i>🎯</i><b>Set wallet</b><small>exact amount</small></a><a class="wtool" onclick="(async()=>sbU('${esc(uid)}', 'discount', await gprompt('Discount percent', '${disc(u.discount_percent)}')))()"><i>%</i><b>Discount</b><small>on every plan</small></a><a class="wtool" onclick="sbU('${esc(uid)}', '${u.is_agent === "1" ? "agent_off" : "agent_on"}')"><i>🤝</i><b>${u.is_agent === "1" ? "Agent off" : "Make agent"}</b><small>reseller prices</small></a><a class="wtool" onclick="sbMsg('${esc(uid)}')"><i>✉</i><b>Message</b><small>send a Telegram message</small></a><a class="wtool ${u.step === "banned" ? "" : "danger"}" onclick="sbU('${esc(uid)}', '${u.step === "banned" ? "unban" : "ban"}')"><i>⛔</i><b>${u.step === "banned" ? "Unban" : "Ban"}</b><small>${u.step === "banned" ? "let the user back in" : "the bot ignores the user"}</small></a></div>`])}</div>
      <div class="ibox" style="margin-top:10px"><h5><i>🧾</i>Subscriptions <small class="mut">${d.orders.length}</small></h5>${d.orders.map(o => irow(`<b>${esc(o.remark || "#" + o.id)}</b> <small class="mut">${esc(o.server || "")}</small>`, `${money(o.amount)} · until ${tsFa(o.expire_date)} · ${o.status === "1" ? '<b class="gw-ok">active</b>' : '<span class="mut">off</span>'}`)).join("") || '<div class="mut">none</div>'}</div>
      <div class="grid2" style="margin-top:10px">${ibox("💳", "Payments", d.pays.map(p => irow(`#${esc(p.id)} ${esc(p.type || "")}`, `${money(p.price)} · ${esc(p.state)} · ${tsFa(p.request_date)}`)).length ? d.pays.map(p => irow(`#${esc(p.id)} ${esc(p.type || "")}`, `${money(p.price)} · ${esc(p.state)}`)) : ['<div class="mut">none</div>'])}${ibox("🎁", "Extras", [...d.wheel.map(w => irow("Wheel prize", money(w.prize) + " · " + tsFa(w.at))), ...d.cashback.map(c => irow("Cashback", money(c.cashback) + " on " + money(c.order_amount)))].length ? [...d.wheel.map(w => irow("Wheel prize", money(w.prize))), ...d.cashback.map(c => irow("Cashback", money(c.cashback) + " on " + money(c.order_amount)))] : ['<div class="mut">none</div>'])}</div></div>`, "wide");
  } catch (e) { showModal(`<b>User ${esc(uid)}</b>`, `<div class="err">${esc(e.message)}</div>`); }
};
window.sbMsg = async uid => { const text = await gprompt(`Message to ${uid} (sent by the bot):`, ""); if (!text) return; try { await api(`/api/shopbot/users/${encodeURIComponent(uid)}/message`, {method: "POST", body: JSON.stringify({text})}); toast("✓ Sent", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* ---- plans */
async function sbPlans() {
  const r = await api("/api/shopbot/plans");
  sb.cats = r.categories; sb.servers = r.servers;
  const lastP = r.plans[r.plans.length - 1] || {}, pre = (list, cur) => list.includes(String(cur)) ? "" : "selected";
  const DAYS = [1, 7, 15, 30, 60, 90, 180, 365], GBS = [1, 5, 10, 20, 30, 50, 100, 200, 500];
  sbBody(`<div class="card sb-quick"><h3>Add a plan to the bot <small class="mut">pick the category, time and volume — it appears in the Telegram bot and the Mini App at once</small></h3>
      <form class="qa-row" onsubmit="return sbQuickPlan(event)">
        <label>Category<span class="qa-cat">${sel("catid", (r.categories || []).map(c => [c.id, c.title]), lastP.catid, "— none —")}<button type="button" class="sm" onclick="sbCatQuick()" title="Make a new category">＋</button></span></label>
        <label>Server${sel("server_id", (r.servers || []).map(s => [s.id, s.title]), lastP.server_id)}</label>
        <label>Time<select name="days">${DAYS.map(d => `<option value="${d}" ${d === 30 ? "selected" : ""}>${d} days</option>`).join("")}<option value="0">no limit</option></select></label>
        <label>Volume<select name="volume">${GBS.map(g => `<option value="${g}" ${g === 20 ? "selected" : ""}>${g} GB</option>`).join("")}<option value="0">unlimited</option></select></label>
        <label>Price (Toman)<input name="price" type="number" min="0" step="1000" required placeholder="150000"></label>
        <button class="pri" type="submit">＋ Add</button></form>
      <small class="mut">The title is made for you (“۲۰ گیگ · ۳۰ روز”); use Edit afterwards for a custom title, description or stock. The panel settings are copied from the other plans of the same server.</small></div>
    <div class="toolbar"><span class="mut">${r.plans.length} plans · ${r.plans.filter(p => p.active === "1").length} active</span><span class="sp"></span><button class="sm" onclick="sbPlanEdit(null)">Detailed form…</button></div>
    <table class="tbl"><colgroup><col style="width:5%"><col style="width:22%"><col style="width:13%"><col style="width:13%"><col style="width:7%"><col style="width:7%"><col style="width:10%"><col style="width:6%"><col style="width:5%"><col style="width:190px"></colgroup><thead><tr><th>#</th><th>Plan</th><th>Category</th><th>Server</th><th>Days</th><th>GB</th><th>Price</th><th>Sold</th><th>On</th><th></th></tr></thead>
    <tbody>${r.plans.map(p => `<tr class="${p.active === "1" ? "up" : "down"}"><td class="num">${esc(p.id)}</td><td class="name">${esc(p.title)}${p.descr && p.descr !== "NULL" ? ` <small class="mut" title="${esc(p.descr)}">${esc(p.descr.slice(0, 40))}</small>` : ""}</td><td>${p.category && p.category !== "NULL" ? esc(p.category) : '<span class="mut">— none —</span>'}</td><td>${esc(p.server || p.server_id)}</td><td class="num">${+p.days ? esc(p.days) : "∞"}</td><td class="num">${+p.volume ? esc(p.volume) : "∞"}</td><td class="num"><b>${money(p.price)}</b></td><td class="num">${esc(p.sold)}</td><td>${p.active === "1" ? '<b class="gw-ok">✓</b>' : '<span class="mut">off</span>'}</td>
      <td><div class="td-act"><button class="sm" onclick="sbPlanEdit(${p.id})">Edit</button><button class="sm" onclick="sbPlanToggle(${p.id}, ${p.active === "1" ? 0 : 1})">${p.active === "1" ? "Off" : "On"}</button><button class="sm danger" onclick="sbPlanDel(${p.id}, '${esc(p.title)}')">Delete</button></div></td></tr>`).join("") || '<tr><td colspan="10" class="empty">No plan yet.</td></tr>'}</tbody></table>`);
  sb.plans = r.plans;
}
const sel = (name, options, cur, blank) => `<select name="${name}">${blank ? `<option value="0">${blank}</option>` : ""}${options.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(cur) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
window.sbPlanEdit = id => {
  const p = (sb.plans || []).find(x => +x.id === +id) || {};
  showModal(`<b>${id ? "Edit plan #" + id : "New plan"}</b>`, `<form class="frm" onsubmit="return sbPlanSave(event, ${id || "null"})">
    <label>Title<input name="title" required value="${esc(p.title || "")}" placeholder="30 days · 50 GB"></label>
    <label>Category${sel("catid", (sb.cats || []).map(c => [c.id, c.title]), p.catid, "— none —")}</label>
    <label>Server (panel)${sel("server_id", (sb.servers || []).map(s => [s.id, s.title + " · " + (s.type || "")]), p.server_id, "— none —")}</label>
    <label>Days<input name="days" type="number" min="0" step="1" required value="${esc(p.days || 30)}"></label>
    <label>Volume (GB, 0 = unlimited)<input name="volume" type="number" min="0" step="0.5" required value="${esc(p.volume || 0)}"></label>
    <label>Price (Toman)<input name="price" type="number" min="0" step="1000" required value="${esc(p.price || 0)}"></label>
    <label>Stock (0 = unlimited)<input name="acount" type="number" min="0" value="${esc(p.acount || 0)}"></label>
    <label>Max devices (IP limit)<input name="limitip" type="number" min="0" value="${esc(p.limitip || 1)}"></label>
    <label class="chk"><input name="active" type="checkbox" ${!id || p.active === "1" ? "checked" : ""}> active (shown in the bot)</label>
    <label style="grid-column:1/-1">Description<textarea name="descr" class="ob-raw" style="min-height:70px">${esc(p.descr && p.descr !== "NULL" ? p.descr : "")}</textarea></label>
    <label style="grid-column:1/-1">Panel settings (JSON) <small>PasarGuard: {"group_ids":[1]} · Marzban: {"inbounds":{"vless":["VLESS TCP"]}} - which groups / inbounds the account gets</small><textarea name="custom_sni" class="ob-raw" style="min-height:44px" spellcheck="false">${esc(p.custom_sni && p.custom_sni !== "NULL" ? p.custom_sni : "")}</textarea></label>
    <div class="frm-act"><button class="pri" type="submit">${id ? "Save" : "Add plan"}</button></div></form>`);
};
window.sbPlanSave = async (e, id) => { e.preventDefault(); const f = formJson(e, ["catid", "server_id", "days", "volume", "price", "acount", "limitip"], ["active"]); try { await api(id ? `/api/shopbot/plans/${id}` : "/api/shopbot/plans", {method: id ? "PUT" : "POST", body: JSON.stringify({fields: f})}); toast("✓ Plan saved", "ok"); closeModal(); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
const FA = n => String(n).replace(/\d/g, d => "۰۱۲۳۴۵۶۷۸۹"[d]);
window.sbQuickPlan = async e => {   // category + server + time + volume + price -> a complete plan (title, panel settings and type copied from a plan of the same server)
  e.preventDefault(); const f = formJson(e, ["catid", "server_id", "days", "volume", "price"]);
  if (!f.server_id) { toast("✕ Pick the server (panel) the account is made on", "bad"); return false; }
  const srv = (sb.servers || []).find(s => +s.id === f.server_id) || {}, tpl = (sb.plans || []).find(p => +p.server_id === f.server_id && p.custom_sni && p.custom_sni !== "NULL") || {};
  const fields = {title: `${f.volume ? FA(f.volume) + " گیگ" : "نامحدود"} · ${f.days ? FA(f.days) + " روز" : "بدون محدودیت زمان"}`, catid: f.catid, server_id: f.server_id, days: f.days, volume: f.volume, price: f.price, acount: 1000, limitip: 0, active: 1,
    type: tpl.type || (["marzban", "pasarguard", "marzneshin"].includes(srv.type) ? "marzban" : "normal"), protocol: tpl.protocol || "vless", inbound_id: 0, custom_sni: tpl.custom_sni || ""};
  try { await api("/api/shopbot/plans", {method: "POST", body: JSON.stringify({fields})}); toast("✓ Plan added to the bot", "ok"); sb.plans = null; refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false;
};
window.sbCatQuick = async () => { const title = await gprompt("Name of the new category (what the customer sees):", ""); if (!title) return; try { await api("/api/shopbot/categories", {method: "POST", body: JSON.stringify({title, parent: 0, active: true})}); toast("✓ Category added", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.sbPlanToggle = async (id, active) => { try { await api(`/api/shopbot/plans/${id}`, {method: "PUT", body: JSON.stringify({fields: {active}})}); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.sbPlanDel = async (id, title) => { if (!await gconfirm(`Delete the plan ${title}? Existing subscriptions are not touched.`)) return; try { await api(`/api/shopbot/plans/${id}`, {method: "DELETE"}); toast("✓ Deleted", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* ---- categories */
async function sbCategories() {
  const r = await api("/api/shopbot/categories"); sb.cats = r.categories;
  sbBody(`<div class="toolbar"><button class="pri" onclick="sbCatEdit(null)">＋ Add category</button><span class="mut">the groups the user picks from before the plans</span></div>
    <table class="tbl"><colgroup><col style="width:6%"><col style="width:40%"><col style="width:18%"><col style="width:10%"><col style="width:8%"><col></colgroup><thead><tr><th>#</th><th>Title</th><th>Inside</th><th>Plans</th><th>On</th><th></th></tr></thead>
    <tbody>${r.categories.map(c => `<tr class="${c.active === "1" ? "up" : "down"}"><td class="num">${esc(c.id)}</td><td class="name">${esc(c.title)}</td><td>${c.parent !== "0" ? esc((r.categories.find(x => x.id === c.parent) || {}).title || c.parent) : '<span class="mut">top level</span>'}</td><td class="num">${esc(c.plans)}</td><td>${c.active === "1" ? '<b class="gw-ok">✓</b>' : '<span class="mut">off</span>'}</td>
      <td><div class="td-act"><button class="sm" onclick="sbCatEdit(${c.id})">Edit</button><button class="sm danger" onclick="sbCatDel(${c.id}, '${esc(c.title)}')">Delete</button></div></td></tr>`).join("") || '<tr><td colspan="6" class="empty">No category yet.</td></tr>'}</tbody></table>`);
}
window.sbCatEdit = id => { const c = (sb.cats || []).find(x => +x.id === +id) || {}; showModal(`<b>${id ? "Edit category" : "New category"}</b>`, `<form class="frm" onsubmit="return sbCatSave(event, ${id || "null"})"><label>Title<input name="title" required value="${esc(c.title || "")}" placeholder="Germany"></label><label>Inside${sel("parent", (sb.cats || []).filter(x => +x.id !== +id).map(x => [x.id, x.title]), c.parent, "— top level —")}</label><label class="chk"><input name="active" type="checkbox" ${!id || c.active === "1" ? "checked" : ""}> active</label><div class="frm-act"><button class="pri" type="submit">${id ? "Save" : "Add"}</button></div></form>`); };
window.sbCatSave = async (e, id) => { e.preventDefault(); const f = formJson(e, ["parent"], ["active"]); try { await api(id ? `/api/shopbot/categories/${id}` : "/api/shopbot/categories", {method: id ? "PUT" : "POST", body: JSON.stringify(f)}); toast("✓ Saved", "ok"); closeModal(); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.sbCatDel = async (id, title) => { if (!await gconfirm(`Delete the category ${title}?`)) return; try { await api(`/api/shopbot/categories/${id}`, {method: "DELETE"}); toast("✓ Deleted", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* ---- servers (the panels the bot sells from) */
async function sbServers() {
  const r = await api("/api/shopbot/servers"); sb.servers = r.servers; sb.knownPanels = r.panels;
  sbBody(`<div class="toolbar"><button class="pri" onclick="sbSrvEdit(null)">＋ Add server</button>${r.panels.length ? `<button onclick="sbSrvFromPanel()" title="Make a bot server from a panel this control panel already manages">＋ From a managed panel</button>` : ""}<span class="mut">a server = a Marzban / PasarGuard panel the bot creates accounts on</span></div>
    <table class="tbl"><colgroup><col style="width:5%"><col style="width:20%"><col style="width:11%"><col style="width:24%"><col style="width:9%"><col style="width:9%"><col style="width:8%"><col></colgroup><thead><tr><th>#</th><th>Title</th><th>Type</th><th>Panel</th><th>Capacity</th><th>Active subs</th><th>On</th><th></th></tr></thead>
    <tbody>${r.servers.map(s => `<tr class="${s.active === "1" && s.state === "1" ? "up" : "down"}"><td class="num">${esc(s.id)}</td><td class="name">${esc(s.flag || "")} ${esc(s.title)}${s.remark && s.remark !== "NULL" ? ` <small class="mut">${esc(s.remark)}</small>` : ""}</td><td>${esc(s.type || "?")}</td><td class="mono" title="${esc(s.panel_url || "")}">${esc((s.panel_url || "").replace(/^https?:\/\//, "").slice(0, 40))}</td><td class="num">${esc(s.ucount)}</td><td class="num">${esc(s.active_orders)}</td><td>${s.active === "1" && s.state === "1" ? '<b class="gw-ok">✓</b>' : '<span class="mut">off</span>'}</td>
      <td><div class="td-act"><button class="sm" onclick="sbSrvEdit(${s.id})">Edit</button><button class="sm" onclick="sbSrvToggle(${s.id}, ${s.active === "1" ? 0 : 1})">${s.active === "1" ? "Off" : "On"}</button><button class="sm danger" onclick="sbSrvDel(${s.id}, '${esc(s.title)}')">Delete</button></div></td></tr>`).join("") || '<tr><td colspan="8" class="empty">No server yet.</td></tr>'}</tbody></table>`);
}
window.sbSrvEdit = id => {
  const s = (sb.servers || []).find(x => +x.id === +id) || {};
  showModal(`<b>${id ? "Edit server #" + id : "New server"}</b>`, `<form class="frm" onsubmit="return sbSrvSave(event, ${id || "null"})">
    <label>Title<input name="title" required value="${esc(s.title || "")}" placeholder="Germany 1"></label><label>Flag / emoji<input name="flag" value="${esc(s.flag && s.flag !== "NULL" ? s.flag : "")}" placeholder="🇩🇪"></label><label>Note<input name="remark" value="${esc(s.remark && s.remark !== "NULL" ? s.remark : "")}"></label>
    <label>Type${sel("type", [["pasarguard", "PasarGuard"], ["marzban", "Marzban"], ["marzneshin", "Marzneshin"], ["sanaei", "3x-ui (sanaei)"], ["alireza", "x-ui (alireza)"]], s.type || "pasarguard")}</label>
    <label style="grid-column:span 2">Panel address<input name="panel_url" value="${esc(s.panel_url && s.panel_url !== "NULL" ? s.panel_url : "")}" placeholder="https://panel.example.com:8000"></label>
    <label>Panel user<input name="username" value="${esc(s.username && s.username !== "NULL" ? s.username : "")}"></label><label>Panel password <small>(empty keeps)</small><input name="password" type="password" autocomplete="new-password"></label><label>SNI <small>(optional)</small><input name="sni" value="${esc(s.sni && s.sni !== "NULL" ? s.sni : "")}"></label>
    <label>Capacity (accounts)<input name="ucount" type="number" min="0" value="${esc(s.ucount || 1000)}"></label><label class="chk"><input name="active" type="checkbox" ${!id || s.active === "1" ? "checked" : ""}> active</label><label class="chk"><input name="state" type="checkbox" ${!id || s.state === "1" ? "checked" : ""}> selling (state)</label>
    <div class="frm-act"><button class="pri" type="submit">${id ? "Save" : "Add server"}</button></div></form>`);
};
window.sbSrvSave = async (e, id) => { e.preventDefault(); const f = formJson(e, ["ucount"], ["active", "state"]); const info = {title: f.title, flag: f.flag, remark: f.remark, ucount: f.ucount, active: f.active ? 1 : 0, state: f.state ? 1 : 0}, config = {type: f.type, panel_url: f.panel_url, username: f.username, password: f.password, sni: f.sni}; try { await api(id ? `/api/shopbot/servers/${id}` : "/api/shopbot/servers", {method: id ? "PUT" : "POST", body: JSON.stringify({info, config})}); toast("✓ Server saved", "ok"); closeModal(); sb.infoAt = 0; refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.sbSrvToggle = async (id, active) => { try { await api(`/api/shopbot/servers/${id}`, {method: "PUT", body: JSON.stringify({info: {active}, config: {}})}); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.sbSrvDel = async (id, title) => { if (!await gconfirm(`Remove the server ${title} from the bot, with its plans? Refused while subscriptions live on it.`)) return; try { await api(`/api/shopbot/servers/${id}`, {method: "DELETE"}); toast("✓ Removed", "ok"); sb.infoAt = 0; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.sbSrvFromPanel = () => showModal("<b>Server from a managed panel</b>", `<form class="frm" onsubmit="return sbSrvFromPanelGo(event)"><label>Panel${sel("panel_id", (sb.knownPanels || []).map(p => [p.id, p.name + " · " + p.type]), "")}</label><label>Title in the bot<input name="title" placeholder="same as the panel"></label><label>Capacity<input name="ucount" type="number" value="1000"></label><div class="frm-act"><button class="pri" type="submit">Add</button></div><small class="mut">The panel's address, user and password are copied into the bot's database on the bot server.</small></form>`);
window.sbSrvFromPanelGo = async e => { e.preventDefault(); const f = formJson(e, ["panel_id", "ucount"]); try { await api("/api/shopbot/servers/from-panel", {method: "POST", body: JSON.stringify(f)}); toast("✓ Server added", "ok"); closeModal(); sb.infoAt = 0; refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
/* ---- discounts */
const AUD = {all: "everyone", agents: "agents", non_agents: "non-agents", buyers: "buyers", non_buyers: "never bought", has_active_order: "has an active subscription", no_active_order: "no active subscription", user: "one user"};
async function sbDiscounts() {
  const r = await api("/api/shopbot/discounts"); sb.discounts = r.discounts;
  sbBody(`<div class="toolbar"><button class="pri" onclick="sbDiscEdit(null)">＋ Add code</button><span class="mut">${r.discounts.length} codes</span></div>
    <table class="tbl"><colgroup><col style="width:14%"><col style="width:12%"><col style="width:20%"><col style="width:14%"><col style="width:10%"><col style="width:8%"><col style="width:6%"><col></colgroup><thead><tr><th>Code</th><th>Off</th><th>Who</th><th>Until</th><th>Uses</th><th>Per user</th><th>On</th><th></th></tr></thead>
    <tbody>${r.discounts.map(d => `<tr class="${d.is_active === "1" ? "up" : "down"}"><td class="mono"><b>${esc(d.hash_id)}</b></td><td>${d.type === "percent" ? esc(d.amount) + " %" : money(d.amount)}${+d.max_discount ? ` <small class="mut">max ${money(d.max_discount)}</small>` : ""}${+d.min_price ? ` <small class="mut">from ${money(d.min_price)}</small>` : ""}</td><td>${esc(AUD[d.audience_type] || d.audience_type)}${d.audience_type === "user" ? " " + esc(d.audience_user_id) : ""}${d.target_type !== "all" ? ` <small class="mut">· ${esc(d.target_type)} #${esc(d.target_id)}</small>` : ""}</td><td class="mut">${+d.expire_date ? tsFa(d.expire_date) : "no end"}</td><td class="num">${esc(d.uses)}${+d.expire_count ? " / " + esc(d.expire_count) : ""}</td><td class="num">${esc(d.can_use)}</td><td>${d.is_active === "1" ? '<b class="gw-ok">✓</b>' : '<span class="mut">off</span>'}</td>
      <td><div class="td-act"><button class="sm" onclick="sbDiscEdit(${d.id})">Edit</button><button class="sm danger" onclick="sbDiscDel(${d.id}, '${esc(d.hash_id)}')">Delete</button></div></td></tr>`).join("") || '<tr><td colspan="8" class="empty">No discount code yet.</td></tr>'}</tbody></table>`);
}
window.sbDiscEdit = id => {
  const d = (sb.discounts || []).find(x => +x.id === +id) || {};
  const daysLeft = +d.expire_date ? Math.max(0, Math.ceil((+d.expire_date - Date.now() / 1000) / 86400)) : 0;
  showModal(`<b>${id ? "Edit discount code" : "New discount code"}</b>`, `<form class="frm" onsubmit="return sbDiscSave(event, ${id || "null"})">
    <label>Code<input name="hash_id" required pattern="[A-Za-z0-9_-]+" value="${esc(d.hash_id || "")}" placeholder="SPRING20"></label><label>Kind${sel("type", [["percent", "percent off"], ["amount", "fixed amount off (Toman)"]], d.type || "percent")}</label><label>Amount<input name="amount" type="number" min="1" required value="${esc(d.amount || 10)}"></label>
    <label>Valid for (days, 0 = no end)<input name="days" type="number" min="0" value="${daysLeft}"></label><label>Total uses (0 = unlimited)<input name="expire_count" type="number" min="0" value="${esc(d.expire_count || 0)}"></label><label>Uses per user<input name="can_use" type="number" min="1" value="${esc(d.can_use || 1)}"></label>
    <label>Who may use it${sel("audience_type", Object.entries(AUD), d.audience_type || "all")}</label><label>User id (for "one user")<input name="audience_user_id" value="${esc(d.audience_user_id && d.audience_user_id !== "0" ? d.audience_user_id : "")}"></label>
    <label>Applies to${sel("target_type", [["all", "every plan"], ["plan", "one plan"], ["category", "one category"], ["server", "one server"]], d.target_type || "all")}</label><label>Plan / category / server id<input name="target_id" type="number" min="0" value="${esc(d.target_id || 0)}"></label>
    <label>Minimum price<input name="min_price" type="number" min="0" value="${esc(d.min_price || 0)}"></label><label>Maximum discount (Toman, 0 = none)<input name="max_discount" type="number" min="0" value="${esc(d.max_discount || 0)}"></label>
    <label class="chk"><input name="is_active" type="checkbox" ${!id || d.is_active === "1" ? "checked" : ""}> active</label>
    <div class="frm-act"><button class="pri" type="submit">${id ? "Save" : "Add code"}</button></div></form>`);
};
window.sbDiscSave = async (e, id) => { e.preventDefault(); const f = formJson(e, ["amount", "days", "expire_count", "can_use", "target_id", "min_price", "max_discount"], ["is_active"]); f.is_active = f.is_active ? 1 : 0; try { await api(id ? `/api/shopbot/discounts/${id}` : "/api/shopbot/discounts", {method: id ? "PUT" : "POST", body: JSON.stringify({fields: f})}); toast("✓ Saved", "ok"); closeModal(); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.sbDiscDel = async (id, code) => { if (!await gconfirm(`Delete the discount code ${code}?`)) return; try { await api(`/api/shopbot/discounts/${id}`, {method: "DELETE"}); toast("✓ Deleted", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* ---- gift codes */
async function sbGifts() {
  const r = await api("/api/shopbot/gifts");
  sbBody(`<div class="toolbar"><button class="pri" onclick="sbGiftNew()">＋ Add gift code</button><span class="mut">a gift code puts money in the wallet of whoever redeems it</span></div>
    <table class="tbl"><colgroup><col style="width:18%"><col style="width:14%"><col style="width:12%"><col style="width:16%"><col style="width:16%"><col><col style="width:90px"></colgroup><thead><tr><th>Code</th><th>Amount</th><th>Used</th><th>Expires</th><th>Made</th><th>Note</th><th></th></tr></thead>
    <tbody>${r.gifts.map(g => `<tr class="${+g.used >= +g.max_uses || (+g.expires && +g.expires < Date.now() / 1000) ? "down" : "up"}"><td class="mono"><b>${esc(g.code)}</b></td><td class="num">${money(g.amount)}</td><td class="num">${esc(g.used)} / ${esc(g.max_uses)}</td><td class="mut">${+g.expires ? tsFa(g.expires) : "never"}</td><td class="mut">${tsFa(g.created)}</td><td>${esc(g.note && g.note !== "NULL" ? g.note : "")}</td><td><div class="td-act"><button class="sm danger" onclick="sbGiftDel(${g.id}, '${esc(g.code)}')">Delete</button></div></td></tr>`).join("") || '<tr><td colspan="7" class="empty">No gift code yet.</td></tr>'}</tbody></table>`);
}
window.sbGiftNew = () => showModal("<b>New gift code</b>", `<form class="frm" onsubmit="return sbGiftSave(event)"><label>Code <small>(empty = random)</small><input name="code" pattern="[A-Za-z0-9_-]*" placeholder="WELCOME50"></label><label>Amount (Toman)<input name="amount" type="number" min="1000" step="1000" required value="50000"></label><label>How many users may use it<input name="max_uses" type="number" min="1" value="1"></label><label>Valid for (days, 0 = no end)<input name="days" type="number" min="0" value="30"></label><label style="grid-column:1/-1">Note<input name="note" placeholder="spring campaign"></label><div class="frm-act"><button class="pri" type="submit">Add</button></div></form>`);
window.sbGiftSave = async e => { e.preventDefault(); const f = formJson(e, ["amount", "max_uses", "days"]); try { await api("/api/shopbot/gifts", {method: "POST", body: JSON.stringify(f)}); toast("✓ Gift code added", "ok"); closeModal(); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.sbGiftDel = async (id, code) => { if (!await gconfirm(`Delete the gift code ${code}?`)) return; try { await api(`/api/shopbot/gifts/${id}`, {method: "DELETE"}); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* ---- broadcast */
const BC_STATE = {0: ["draft", ""], 1: ["queued", ""], 2: ["sending", "ok"], 3: ["done", "ok"], 4: ["cancelled", "err"]};
async function sbBroadcast() {
  const r = await api("/api/shopbot/broadcasts");
  sbBody(`<div class="card"><h3>New broadcast <small class="mut">— the bot's cron sends it, one user at a time, within minutes</small></h3><form class="frm" onsubmit="return sbBcSend(event)">
      <label>Title (for this list)<input name="title" placeholder="October news"></label><label>To${sel("group", Object.entries(r.groups), "all")}</label>
      <label style="grid-column:1/-1">Message <small>(Telegram HTML allowed: &lt;b&gt;, &lt;i&gt;, &lt;code&gt;)</small><textarea name="text" class="ob-raw" style="min-height:110px" required placeholder="سلام 👋 …"></textarea></label>
      <div class="frm-act"><button class="pri" type="submit">📣 Send to ${r.groups.all ? "the chosen users" : "users"}</button></div></form></div>
    <table class="tbl"><colgroup><col style="width:6%"><col style="width:20%"><col style="width:12%"><col style="width:14%"><col style="width:12%"><col style="width:16%"><col><col style="width:90px"></colgroup><thead><tr><th>#</th><th>Title</th><th>To</th><th>State</th><th>Sent</th><th>Made</th><th>Text</th><th></th></tr></thead>
    <tbody>${r.broadcasts.map(b => { const st = BC_STATE[b.state] || [b.state, ""]; return `<tr><td class="num">${esc(b.id)}</td><td class="name">${esc(b.title || "")}</td><td>${esc(r.groups[b.target_group] || b.target_group)}</td><td><span class="pill ${st[1]}">${esc(st[0])}</span></td><td class="num">${esc(b.sent || 0)}${+b.total_cnt ? " / " + esc(b.total_cnt) : ""}</td><td class="mut">${tsFa(b.created_at)}</td><td class="mut" title="${esc(b.text || "")}">${esc((b.text || "").slice(0, 60))}</td><td><div class="td-act">${+b.state <= 2 ? `<button class="sm danger" onclick="sbBcCancel(${b.id})">Cancel</button>` : ""}</div></td></tr>`; }).join("") || '<tr><td colspan="8" class="empty">No broadcast yet.</td></tr>'}</tbody></table>`);
}
window.sbBcSend = async e => { e.preventDefault(); const f = formJson(e); if (!await gconfirm(`Send this message to ${f.group === "all" ? "EVERY user of the bot" : "the chosen users"}? It cannot be unsent.`, {danger: true})) return false; try { const r = await api("/api/shopbot/broadcasts", {method: "POST", body: JSON.stringify(f)}); toast(`✓ Broadcast #${r.id} queued`, "ok"); sb.infoAt = 0; refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.sbBcCancel = async id => { if (!await gconfirm(`Cancel broadcast #${id}? Messages already sent stay.`)) return; try { await api(`/api/shopbot/broadcasts/${id}/cancel`, {method: "POST"}); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* ---- tickets */
async function sbTickets() {
  const r = await api(`/api/shopbot/tickets?state=${sb.tkState}&page=${sb.tkPage}`);
  sbBody(`<div class="toolbar"><div class="tabs">${["open", "closed", "all"].map(k => `<button class="${sb.tkState === k ? "on" : ""}" onclick="sb.tkState='${k}';sb.tkPage=0;refresh()">${k[0].toUpperCase() + k.slice(1)}</button>`).join("")}</div>${sbPager(r.page, r.total, "sbTkPage")}</div>
    <table class="tbl"><colgroup><col style="width:6%"><col style="width:22%"><col style="width:30%"><col style="width:12%"><col style="width:8%"><col style="width:14%"><col></colgroup><thead><tr><th>#</th><th>User</th><th>Title</th><th>Category</th><th>Msgs</th><th>Last</th><th></th></tr></thead>
    <tbody>${r.rows.map(t => `<tr class="${t.state === "1" ? "" : "down"} click" onclick="sbTicket(${t.id})"><td class="num">${esc(t.id)}</td><td class="name">${esc(t.name || t.user_id)} <small class="mut">${t.username ? "@" + esc(t.username) : ""}</small></td><td>${esc(t.title || "")}</td><td class="mut">${esc(t.category || "")}</td><td class="num">${esc(t.messages)}</td><td class="mut">${ago(+t.last_at || +t.create_date)} ago</td><td><div class="td-act"><button class="sm" onclick="event.stopPropagation();sbTicket(${t.id})">Open</button></div></td></tr>`).join("") || '<tr><td colspan="7" class="empty">No ticket.</td></tr>'}</tbody></table>`);
  sb.tickets = r.rows;
}
window.sbTkPage = p => { sb.tkPage = p; refresh(); };
window.sbTicket = async id => {
  const t = (sb.tickets || []).find(x => +x.id === +id) || {};
  showModal(`<b>🎫 #${id} ${esc(t.title || "")}</b> <span class="mut">${esc(t.name || t.user_id || "")}</span>`, '<div class="loading"><i class="spin"></i><span>Reading…</span></div>', "wide", t.state === "1" ? `<button class="sm" onclick="sbTicketClose(${id})">Close ticket</button>` : "");
  try {
    const r = await api(`/api/shopbot/tickets/${id}`);
    showModal(`<b>🎫 #${id} ${esc(t.title || "")}</b> <span class="mut">${esc(t.name || t.user_id || "")}</span>`, `<div class="tk-msgs">${r.messages.map(m => `<div class="tk-msg ${m.msg_type === "ADMIN" ? "me" : ""}"><small class="mut">${m.msg_type === "ADMIN" ? "support" : "user"} · ${tsFa(m.sent_date)}</small><div>${esc(m.text)}</div></div>`).join("") || '<div class="mut">no message</div>'}</div>
      ${t.state === "1" ? `<form class="frm one" onsubmit="return sbTicketReply(event, ${id})"><label>Reply<textarea name="text" class="ob-raw" style="min-height:80px" required></textarea></label><div class="frm-act"><button class="pri" type="submit">Send reply</button></div></form>` : '<div class="mut" style="margin-top:8px">This ticket is closed.</div>'}`, "wide", t.state === "1" ? `<button class="sm" onclick="sbTicketClose(${id})">Close ticket</button>` : "");
    const box = $(".tk-msgs"); if (box) box.scrollTop = box.scrollHeight;
  } catch (e) { showModal(`<b>🎫 #${id}</b>`, `<div class="err">${esc(e.message)}</div>`); }
};
window.sbTicketReply = async (e, id) => { e.preventDefault(); const f = formJson(e); try { await api(`/api/shopbot/tickets/${id}/reply`, {method: "POST", body: JSON.stringify(f)}); toast("✓ Reply sent", "ok"); sbTicket(id); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.sbTicketClose = async id => { if (!await gconfirm(`Close ticket #${id}? The user is told.`)) return; try { await api(`/api/shopbot/tickets/${id}/close`, {method: "POST"}); toast("✓ Closed", "ok"); closeModal(); sb.infoAt = 0; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* ---- texts and buttons */
async function sbTexts() {
  if (!sb.txData) sb.txData = await api("/api/shopbot/texts");
  const d = sb.txData, base = sb.txKind === "text" ? d.texts : d.buttons, ov = sb.txKind === "text" ? d.text_overrides : d.button_overrides, q = sb.txQ.toLowerCase();
  const keys = Object.keys(base).filter(k => !q || k.toLowerCase().includes(q) || String(base[k]).toLowerCase().includes(q) || String(ov[k] || "").toLowerCase().includes(q));
  sbBody(`<div class="toolbar"><div class="tabs"><button class="${sb.txKind === "text" ? "on" : ""}" onclick="sb.txKind='text';refresh()">Texts <small>${Object.keys(d.texts).length}</small></button><button class="${sb.txKind === "button" ? "on" : ""}" onclick="sb.txKind='button';refresh()">Buttons <small>${Object.keys(d.buttons).length}</small></button></div><input placeholder="search a key or a text…" value="${esc(sb.txQ)}" oninput="sb.txQ=this.value;clearTimeout(qT);qT=setTimeout(refresh,250)"><span class="mut">${Object.keys(ov).length} changed</span><span class="sp"></span><button class="sm" onclick="sb.txData=null;refresh()">↻</button><button class="pri sm" onclick="sbTextsSave()">Save changed</button></div>
    ${d.hooked || sb.txKind === "button" ? "" : '<div class="gw-out"><b>First save:</b> the bot is made to read the panel\'s text file (one line in its config, checked before it is kept).</div>'}
    <div class="tx-list">${keys.slice(0, 400).map(k => `<div class="tx-row ${k in ov ? "changed" : ""}"><div class="tx-key"><code>${esc(k)}</code>${k in ov ? `<a class="mut" onclick="sbTextReset('${esc(k)}')">reset</a>` : ""}</div><textarea data-key="${esc(k)}" spellcheck="false" rows="${Math.min(8, Math.max(1, (String(ov[k] ?? base[k]).match(/\n/g) || []).length + 1))}">${esc(ov[k] ?? base[k])}</textarea></div>`).join("")}${keys.length > 400 ? '<div class="mut">narrow the search to see the rest</div>' : ""}</div>`);
}
window.sbTextsSave = async () => {
  const d = sb.txData, base = sb.txKind === "text" ? d.texts : d.buttons, ov = sb.txKind === "text" ? d.text_overrides : d.button_overrides, ch = {};
  document.querySelectorAll(".tx-list textarea").forEach(t => { const k = t.dataset.key, v = t.value; if (v !== (ov[k] ?? base[k])) ch[k] = v === base[k] ? "" : v; });
  if (!Object.keys(ch).length) { toast("Nothing changed"); return; }
  try { await api("/api/shopbot/texts", {method: "PUT", body: JSON.stringify({kind: sb.txKind, changes: ch})}); toast(`✓ ${Object.keys(ch).length} saved — the bot uses them from the next message`, "ok"); sb.txData = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.sbTextReset = async k => { try { await api("/api/shopbot/texts", {method: "PUT", body: JSON.stringify({kind: sb.txKind, changes: {[k]: ""}})}); sb.txData = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* ---- settings (switches, prices, payment details) */
async function sbSettings() {
  const d = await api("/api/shopbot/settings"); sb.stData = d;
  const field = i => i.kind === "onoff" ? `<select name="${i.key}" data-was="${esc(i.value)}"><option value="on" ${i.value === "on" ? "selected" : ""}>on</option><option value="off" ${i.value === "off" || !i.value ? "selected" : ""}>off</option></select>` : `<input name="${i.key}" data-was="${esc(i.value)}" value="${esc(i.value)}" ${i.kind === "number" ? 'inputmode="numeric"' : ""}>`;
  sbBody(`<form class="sb-set" onsubmit="return sbSettingsSave(event)"><div class="toolbar" style="margin-bottom:6px"><span class="mut">Every switch the bot reads on each message. Only what you change is written.</span><span class="sp"></span><button class="pri sm" type="submit">Save changed</button></div>
    <div class="grid2">${d.groups.map(g => `<div class="card"><h3>${esc(g.title)}</h3><div class="sb-grid">${g.items.map(i => `<label title="${esc(i.hint)}"><span>${esc(i.label)}${i.hint ? ` <small class="mut">${esc(i.hint)}</small>` : ""}</span>${field(i)}</label>`).join("")}</div></div>`).join("")}
    <div class="card"><h3>Payment details <small class="mut">— what the bot shows for card to card, and the gateway keys</small></h3><div class="sb-grid">${[["bankAccount", "Card number"], ["holderName", "Card holder"], ["zarinpal", "ZarinPal merchant id"], ["nextpay", "NextPay key"], ["nowpayment", "NowPayments key"], ["tronwallet", "TRON wallet address"]].map(([k, l]) => `<label><span>${l}</span><input name="pk:${k}" data-was="${esc(d.payment_keys[k] || "")}" value="${esc(d.payment_keys[k] || "")}"></label>`).join("")}</div></div>
    ${Object.keys(d.other).length ? `<div class="card"><h3>Other keys</h3><div class="sb-grid">${Object.entries(d.other).map(([k, v]) => `<label><span><code>${esc(k)}</code></span><input name="${esc(k)}" data-was="${esc(v)}" value="${esc(v)}"></label>`).join("")}</div></div>` : ""}</div></form>`);
}
window.sbSettingsSave = async e => {
  e.preventDefault(); const states = {}, keys = {};
  e.target.querySelectorAll("input, select").forEach(el => { if (el.value !== el.dataset.was) { if (el.name.startsWith("pk:")) keys[el.name.slice(3)] = el.value; else states[el.name] = el.value; } });
  if (!Object.keys(states).length && !Object.keys(keys).length) { toast("Nothing changed"); return false; }
  try { if (Object.keys(states).length) await api("/api/shopbot/settings", {method: "PUT", body: JSON.stringify({changes: states})}); if (Object.keys(keys).length) await api("/api/shopbot/payment-keys", {method: "PUT", body: JSON.stringify({changes: keys})}); toast("✓ Saved: " + [...Object.keys(states), ...Object.keys(keys)].join(", "), "ok"); sb.infoAt = 0; refresh(); } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};
/* ---- tools: apps list, top-up prices, backups, tables, SQL, stats, service actions */
async function sbTools() {
  const tabs = [["miniapp", "Mini App"], ["backups", "Backups"], ["apps", "Apps list"], ["topups", "Top-up prices"], ["tables", "Tables"], ["sql", "SQL"], ["stats", "Stats"]];
  sbBody(`<div class="toolbar"><div class="tabs">${tabs.map(([k, l]) => `<button class="${sb.tool === k ? "on" : ""}" onclick="sb.tool='${k}';refresh()">${l}</button>`).join("")}</div><span class="sp"></span>
      <button class="sm" onclick="sbAct('webhook')">Set webhook</button><button class="sm" onclick="sbCommands()" title="The / command menu of the bot">Set commands</button><button class="sm" onclick="sbAct('restart')">Restart PHP</button><button class="sm" onclick="sbAct('backup')">Dump DB</button><button class="sm" onclick="sbAct('clearlogs')">Clear logs</button></div><div id="sb-tool"></div>`);
  await ({miniapp: sbMiniApp, backups: sbBackups, apps: sbApps, topups: sbTopups, tables: sbTableLoad, sql: sbSqlHtml, stats: sbStatsLoad}[sb.tool] || sbMiniApp)();
}
const sbTool = html => { const b = $("#sb-tool"); if (b) b.innerHTML = html; };
async function sbMiniApp() {
  let m; try { m = await api("/api/shopbot/miniapp"); } catch (e) { sbTool(`<div class="err">${esc(e.message)}</div>`); return; }
  const state = !m.installed ? '<span class="pill">not installed</span>' : m.php_ok === false ? '<span class="pill err">✗ api.php does not parse</span>' : m.up_to_date ? `<span class="pill ok">● installed · ${esc(m.version || "?")} · up to date</span>` : `<span class="pill warn">● installed ${esc(m.version || "")} · the panel has ${esc(m.local_version || "a newer version")}</span>`;
  sbTool(`<div class="grid2">
    <div class="card"><h3>Telegram Mini App <span class="sp"></span>${state}</h3>
      <p class="mut">The customers' app inside Telegram: services with live usage, buying from the wallet, card-to-card top-up with the receipt sent to you for ✅ / ❌, renew, extra volume and days, tickets, referral link, apps, gift codes and the wheel. It is opened from the bot's <b>Mini App</b> button${m.mini_app_switch === false ? " — <b class='err'>that button is switched off</b> (Settings → Extras → Mini App button)" : ""}.</p>
      ${m.url ? `<div class="gw-row"><span>Address</span><span class="mono">${esc(m.url)}</span><span><a class="sm" href="${esc(m.url)}" target="_blank" rel="noopener">open ↗</a></span></div>` : ""}
      <div class="frm-act"><button class="pri" onclick="sbMiniAppDeploy()">${m.installed ? "Update the Mini App" : "Install the Mini App"}</button>${m.installed ? `<small class="mut">puts the panel's version of the four files next to the bot (the old ones are kept as .prev)</small>` : ""}</div>
      ${m.errors ? `<div class="err" style="margin-top:8px">${m.errors} line(s) in the app's error log · last: <span class="mono">${esc(m.last_error)}</span></div>` : ""}</div>
    <div class="card"><h3>Files on the bot server</h3><table class="tbl"><colgroup><col><col style="width:22%"><col style="width:30%"></colgroup><thead><tr><th>File</th><th>Size</th><th>Same as the panel's</th></tr></thead><tbody>${(m.files || []).map(f => `<tr class="${f.current ? "up" : "down"}"><td class="mono">${esc(f.name)}</td><td class="num">${f.size ? (f.size / 1024).toFixed(1) + " KB" : "—"}</td><td>${!f.hash ? '<span class="err">missing</span>' : f.current ? '<b class="gw-ok">✓ yes</b>' : '<span class="mut">no — update</span>'}</td></tr>`).join("") || '<tr><td colspan="3" class="empty">nothing installed yet</td></tr>'}</tbody></table>
      <small class="mut">Requirements on the bot server: PHP 8+, the bot's <span class="mono">baseInfo.php</span> with <span class="mono">$botUrl</span>, and <span class="mono">BOT_STATES.miniAppState = on</span>.</small></div></div>`);
}
window.sbMiniAppDeploy = async () => { if (!await gconfirm("Put the panel's Mini App files (api.php, index.html, app.js, app.css) next to the bot?\n\napi.php is syntax-checked before it replaces the old one; the previous files are kept as .prev.")) return; toast("Installing…"); try { const m = await api("/api/shopbot/miniapp/deploy", {method: "POST"}); toast(m.up_to_date ? "✓ Mini App " + (m.version || "") + " installed" : "✕ Installed, but the files still differ", m.up_to_date ? "ok" : "bad"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
async function sbBackups() {
  const r = await api("/api/shopbot/backups");
  sbTool(`<table class="tbl"><colgroup><col style="width:50%"><col style="width:20%"><col></colgroup><thead><tr><th>Dump</th><th>Size</th><th>Made</th></tr></thead><tbody>${r.backups.map(b => `<tr><td class="mono">${esc(b.name)}</td><td class="num">${(b.size / 1024).toFixed(0)} KB</td><td class="mut">${b.at ? when(b.at) : "—"}</td></tr>`).join("") || '<tr><td colspan="3" class="empty">No dump yet — press Dump DB.</td></tr>'}</tbody></table><small class="mut">On the bot server in /opt/nikabot/backups; the live database is dumped before every import as well.</small>`);
}
async function sbApps() {
  const r = await api("/api/shopbot/apps"); sb.apps = r.apps;
  sbTool(`<div class="toolbar"><button class="pri sm" onclick="sbAppEdit(null)">＋ Add app</button><span class="mut">the download links the bot offers under “apps”</span></div><table class="tbl"><colgroup><col style="width:30%"><col><col style="width:8%"><col style="width:120px"></colgroup><thead><tr><th>Title</th><th>Link</th><th>On</th><th></th></tr></thead><tbody>${r.apps.map(a => `<tr class="${a.status === "1" ? "up" : "down"}"><td class="name">${esc(a.title)}</td><td class="mono"><a href="${esc(a.link)}" target="_blank" rel="noopener">${esc(a.link)}</a></td><td>${a.status === "1" ? '<b class="gw-ok">✓</b>' : '<span class="mut">off</span>'}</td><td><div class="td-act"><button class="sm" onclick="sbAppEdit(${a.id})">Edit</button><button class="sm danger" onclick="sbAppDel(${a.id})">Delete</button></div></td></tr>`).join("") || '<tr><td colspan="4" class="empty">No app yet.</td></tr>'}</tbody></table>`);
}
window.sbAppEdit = id => { const a = (sb.apps || []).find(x => +x.id === +id) || {}; showModal(`<b>${id ? "Edit app" : "New app"}</b>`, `<form class="frm" onsubmit="return sbAppSave(event, ${id || "null"})"><label>Title<input name="title" required value="${esc(a.title || "")}" placeholder="v2rayNG (Android)"></label><label>Link<input name="link" required value="${esc(a.link || "")}" placeholder="https://…"></label><label class="chk"><input name="status" type="checkbox" ${!id || a.status === "1" ? "checked" : ""}> shown</label><div class="frm-act"><button class="pri" type="submit">${id ? "Save" : "Add"}</button></div></form>`); };
window.sbAppSave = async (e, id) => { e.preventDefault(); const f = formJson(e, [], ["status"]); try { await api(id ? `/api/shopbot/apps/${id}` : "/api/shopbot/apps", {method: id ? "PUT" : "POST", body: JSON.stringify(f)}); toast("✓ Saved", "ok"); closeModal(); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.sbAppDel = async id => { if (!await gconfirm("Delete this app link?")) return; try { await api(`/api/shopbot/apps/${id}`, {method: "DELETE"}); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
async function sbTopups() {
  const r = await api("/api/shopbot/topups");
  const list = (kind, rows, unit) => `<div class="card"><h3>Extra ${unit} <span class="sp"></span><button class="sm pri" onclick="sbTopupNew('${kind}')">＋ Add</button></h3>${rows.map(x => `<div class="gw-row"><span><b>${esc(kind === "volume" ? x.volume : x.days)} ${unit}</b></span><span>${money(x.price)}</span><span><button class="sm danger" onclick="sbTopupDel('${kind}', ${x.id})">Delete</button></span></div>`).join("") || '<div class="mut">none — the bot cannot sell extra ' + unit + '</div>'}</div>`;
  sbTool(`<div class="grid2">${list("volume", r.volume, "GB")}${list("days", r.days, "days")}</div>`);
}
window.sbTopupNew = async kind => { const amount = await gprompt(kind === "volume" ? "How many GB?" : "How many days?", kind === "volume" ? "10" : "30"); if (!amount) return; const price = await gprompt("Price (Toman)", "50000"); if (!price) return; try { await api("/api/shopbot/topups", {method: "POST", body: JSON.stringify({kind, price: +price, amount: +amount})}); toast("✓ Added", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.sbTopupDel = async (kind, id) => { try { await api(`/api/shopbot/topups/${kind}/${id}`, {method: "DELETE"}); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
async function sbTableLoad() {
  const box = $("#sb-tool"); if (!box) return;
  const tables = ["users", "orders_list", "pays", "server_plans", "server_config", "server_categories", "discounts", "setting", "send_list", "add_link", "increase_order", "gift_codes", "wheel_log", "cashback_log"];
  let t; try { t = await api(`/api/shopbot/table/${sb.table}?page=${sb.tpage}&q=${encodeURIComponent(sb.tq)}`); } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  box.innerHTML = `<div class="toolbar"><select onchange="sb.table=this.value;sb.tpage=0;sb.tq='';refresh()">${tables.map(x => `<option ${x === sb.table ? "selected" : ""}>${x}</option>`).join("")}</select><input id="sb-tq" placeholder="filter…" value="${esc(sb.tq)}" oninput="sbTQ(this.value)"><span class="mut">${t.total} rows</span><span class="sp"></span><button class="sm" ${sb.tpage ? "" : "disabled"} onclick="sb.tpage--;refresh()">‹</button><button class="sm" ${(sb.tpage + 1) * 40 < t.total ? "" : "disabled"} onclick="sb.tpage++;refresh()">›</button></div>
    <div style="overflow:auto"><table class="tbl" style="table-layout:auto;min-width:100%"><thead><tr>${t.columns.map(c => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${t.rows.map(r => `<tr>${t.columns.map(c => sbT(c, r[c])).join("")}</tr>`).join("") || `<tr><td class="mut">empty</td></tr>`}</tbody></table></div>`;
  const q = $("#sb-tq"); if (q && sb.tq) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
}
window.sbTQ = v => { sb.tq = v.trim(); sb.tpage = 0; clearTimeout(qT); qT = setTimeout(refresh, 350); };
function sbSqlHtml() {
  const box = $("#sb-tool"); if (!box) return;
  const r = sb.qres;
  box.innerHTML = `<div class="card"><h3>Read-only SQL <small class="mut">— one SELECT or SHOW; string literals in double quotes; 200 rows max</small></h3><textarea id="sb-sql" class="ob-raw" spellcheck="false" placeholder='SELECT userid, name, wallet FROM users WHERE wallet > 100000 ORDER BY wallet DESC'>${esc(sb.sql)}</textarea><div class="frm-act"><button class="pri" onclick="sbRun()">Run</button></div></div>
    ${r ? (r.error ? `<div class="err">${esc(r.error)}</div>` : `<div class="mut">${r.rows.length} rows</div><div style="overflow:auto"><table class="tbl" style="table-layout:auto;min-width:100%"><thead><tr>${r.columns.map(c => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${r.rows.map(x => `<tr>${r.columns.map(c => sbT(c, x[c])).join("")}</tr>`).join("")}</tbody></table></div>`) : ""}`;
}
window.sbRun = async () => { sb.sql = $("#sb-sql").value; try { sb.qres = await api("/api/shopbot/query", {method: "POST", body: JSON.stringify({sql: sb.sql})}); } catch (e) { sb.qres = {error: e.message}; } sbSqlHtml(); };
async function sbStatsLoad() {
  const box = $("#sb-tool"); if (!box) return;
  let s; try { s = await api("/api/shopbot/stats"); } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  const list = (title, rows, f) => `<div class="card"><h3>${title}</h3>${rows.map(r => `<div class="gw-row"><span>${f(r)}</span><span></span><span></span></div>`).join("") || '<div class="mut">nothing</div>'}</div>`;
  box.innerHTML = `<div class="card"><h3>Income per day, 30 days</h3>${spark(s.daily.map(d => +d.income / 1000), {fmt: v => v.toFixed(0) + " k"})}<div class="mut">${s.daily.length ? s.daily[0].day + " → " + s.daily[s.daily.length - 1].day : ""}</div></div>
    <div class="grid2">${list("Top buyers", s.top_buyers, r => `<b>${esc(r.name || r.userid)}</b> <small class="mut">${r.orders} orders · ${money(r.spent)}</small>`)}${list("Biggest wallets", s.top_wallets, r => `<b>${esc(r.name || r.userid)}</b> <small class="mut">${money(r.wallet)}</small>`)}</div>`;
}
window.sbAct = async act => { const msgs = {webhook: "Point the Telegram webhook at the bot's public URL?", restart: "Restart PHP on the bot server?", backup: "Dump the bot's database to /opt/nikabot/backups?", clearlogs: "Clear the bot's log files?"}; if (!await gconfirm(msgs[act])) return; toast("Working…"); try { const r = await api(`/api/shopbot/action/${act}`, {method: "POST"}); toast((r.done ? "✓ " : "✕ ") + (r.message || act), r.done ? "ok" : "bad"); sb.infoAt = 0; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.sbCommands = async () => { if (!await gconfirm("Set the bot's / command menu (start, buy, mysubs, wallet, support, help)?")) return; try { const r = await api("/api/shopbot/commands", {method: "POST"}); toast("✓ Commands set: " + r.commands.join(", "), "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.sbImport = async () => { if (!await gconfirm("Import the real database over the live one?\n\nThe live database is dumped first. Users, orders, payments, plans, panels and settings are replaced; broadcast queues are not copied. The bot then serves the real customers.")) return; toast("Dumping and importing… (1–3 minutes)"); try { const r = await api("/api/shopbot/import-stage", {method: "POST"}); toast(`✓ Imported: ${r.tables.users} users, ${r.tables.orders_list} orders · backup ${r.backup.split("/").pop()}`, "ok"); sb.infoAt = 0; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };


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
        <label>Config: failed checks in a row before DOWN <input name="config_fails" type="number" min="1" max="20" value="${al.config_fails == null ? 3 : al.config_fails}"></label>
        <label>…and down for at least (minutes) <small>(a blip shorter than this says nothing)</small><input name="min_down_minutes" type="number" min="0" max="1440" value="${al.min_down_minutes == null ? 4 : al.min_down_minutes}"></label>
        <label>UP message only after stable for (seconds) <input name="up_stable_seconds" type="number" min="0" max="86400" value="${al.up_stable_seconds}"></label>
        <label>Remind while still down every (hours) <small>(0 = never: one DOWN, one UP, nothing in between)</small><input name="remind_hours" type="number" min="0" max="168" value="${al.remind_hours == null ? 0 : al.remind_hours}"></label>
        <label>Flapping guard: outages within an hour before going quiet <small>(0 = off)</small><input name="flap_max" type="number" min="0" max="50" value="${al.flap_max == null ? 3 : al.flap_max}"></label>
        <label>…then say nothing about it for (hours) <input name="flap_quiet_hours" type="number" min="1" max="168" value="${al.flap_quiet_hours == null ? 6 : al.flap_quiet_hours}"></label>
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
      <small class="mut">At most ${rp_.max_per_hour} restarts per tunnel per hour; never when a server is unreachable. The deeper steps (rebuild, another tunnel type) run by themselves once the restarts failed, and again after 30 minutes if needed - nothing asks for approval. Details on the Smart repair page. ${rp_.read_only ? "Repairs need <code>NIKATEL_READ_ONLY=false</code> in the env file first." : ""}</small></div>

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

    <div class="card"><h3>AI assistant <span class="sp"></span>${aiPill(ai_)}</h3>
      ${aiStatus(ai_)}
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
const aiPill = a => !a.has_api_key ? '<span class="pill">no key</span>' : !a.status || !a.status.at ? '<span class="pill">key saved - not tested</span>' : a.status.ok ? '<span class="pill ok">● connected</span>' : '<span class="pill err">● not working</span>';
const aiStatus = a => {   // which AI is behind the key, whether it answers, how fast, and what it is used for
  const s = a.status || {};
  if (!a.has_api_key) return '<div class="ai-stat"><span class="mut">No key saved. Paste an Anthropic API key below and the panel tests it at once.</span></div>';
  if (!s.at) return '<div class="ai-stat"><span class="mut">Key ...' + esc(a.api_key_tail || "") + ' saved but not tested yet.</span> <button type="button" class="sm" onclick="testAi()">Test now</button></div>';
  const rows = [["AI", esc(s.provider || "Anthropic") + " · Claude"], ["Model", `<b>${esc(s.model || a.model)}</b>`], ["Key", "..." + esc(a.api_key_tail || "")], ["Answer time", s.ok ? s.ms + " ms" : "—"], ["Tested", ago(s.at) + " ago"], ["Used for", a.enabled ? "diagnosis inside Fix" + (a.actions ? " + safe repair steps" : "") : "<span class=\"mut\">switched off (tick the first box)</span>"]];
  return `<div class="ai-stat ${s.ok ? "ok" : "bad"}"><div class="ai-head">${s.ok ? "● Connected" : "● Not working"}</div>${s.ok ? "" : `<div class="err">${esc(s.error || "no answer")}</div>`}<div class="ai-grid">${rows.map(r => `<span class="mut">${r[0]}</span><span>${r[1]}</span>`).join("")}</div></div>`;
};
window.saveAi = e => put(e, "/api/settings/ai", formJson(e, [], ["enabled", "actions"]), "✓ AI settings saved");
window.testAi = async () => { toast("Asking Anthropic…"); try { const r = await api("/api/settings/ai/test", {method: "POST"}); toast("✓ The key works (" + r.answer + ")", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.forgetAi = async () => { if (!await gconfirm("Remove the saved API key?")) return; await api("/api/settings/ai/key", {method: "DELETE"}); toast("✓ Key removed", "ok"); refresh(); };
window.saveArvan = e => { const j = formJson(e, ["confirm_minutes"], []); j.domains = String(j.domains || "").split(/[,\s]+/).filter(Boolean); return put(e, "/api/settings/arvan", j, "✓ ArvanCloud settings saved (the key is tested right away)"); };
window.testArvan = async () => { toast("Asking ArvanCloud (read only)…"); try { const r = await api("/api/settings/arvan/test", {method: "POST"}); toast("✓ Connected: " + r.domains.length + " domain(s), " + r.records + " record(s)", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); refresh(); } };window.aboutLoad = async () => { const box = $("#about-box"); if (!box) return; try { const u = await api("/api/system/update"); box.innerHTML = `<div><span>Installed</span> <b>${esc(u.installed || "?")}</b> · <span>newest on GitHub</span> <b>${esc(u.newest || "?")}</b> · ${u.up_to_date ? '<b class="gw-ok">up to date</b>' : '<b class="err">update available</b>'}</div>`; const lg = $("#about-log"); if (u.last_log) { lg.textContent = u.last_log; lg.classList.remove("hidden"); } } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; } };
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
  grid.querySelectorAll(":scope > .card").forEach(c => { const h = c.querySelector("h3"); const nm = c.dataset.name || (h ? h.childNodes[0].textContent.trim() : ""); c.dataset.name = nm; byName[nm] = c; c.classList.add("hidden"); });
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
  grid.querySelectorAll(":scope > .card").forEach(c => { const nm = c.dataset.name || ""; byName[nm] = c; c.classList.add("hidden"); });
  const names = SET_TABS[state.stab][2].filter(n => byName[n]), pick = names.includes(state.sstab) ? state.sstab : names[0];
  $("#page .set-desc").insertAdjacentHTML("afterend", `<div class="tabs set-sub">${names.map(n => `<button class="${n === pick ? "on" : ""}" onclick="state.sstab='${esc(n)}';settingsTabs.only()">${esc(n)}</button>`).join("")}</div>`);
  byName[pick].classList.remove("hidden");
  if (pick === "About") aboutLoad(); if (pick === "Core update") enginesLoad();
};
window.setTab = k => { state.stab = k; state.sstab = null; localStorage.setItem("nk.stab", k); refresh(); };
state.stab = localStorage.getItem("nk.stab") || "account";
window.onLangChange = () => { try { refresh(); } catch (e) { /* not signed in yet */ } };
const formJson = (e, nums = [], bools = []) => { const f = new FormData(e.target), o = {}; for (const [k, v] of f.entries()) o[k] = v; nums.forEach(k => o[k] = +o[k]); bools.forEach(k => o[k] = f.get(k) === "on"); return o; };
const put = async (e, path, body, okText) => { e.preventDefault(); try { await api(path, {method: "PUT", body: JSON.stringify(body)}); toast(okText, "ok"); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.saveTelegram = e => put(e, "/api/settings/telegram", formJson(e, [], ["enabled"]), "✓ Telegram saved");
window.testTelegram = async () => { toast("Sending…"); try { await api("/api/settings/telegram/test", {method: "POST"}); toast("✓ Test message sent", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.saveRules = e => put(e, "/api/settings/alerts", formJson(e, ["tunnel_fails", "server_fails", "config_fails", "min_down_minutes", "up_stable_seconds", "remind_hours", "flap_max", "flap_quiet_hours"]), "✓ Rules saved");
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

/* the AI buttons (anything with ✨ in it) get the glass-gold look and the magic wand on hover (see .ai-btn in app.css) */
(() => { let q = false; const mark = () => { q = false; document.querySelectorAll("button:not(.ai-btn)").forEach(b => { if (b.textContent.includes("✨")) b.classList.add("ai-btn"); }); }; new MutationObserver(() => { if (!q) { q = true; requestAnimationFrame(mark); } }).observe(document.documentElement, {childList: true, subtree: true, characterData: true}); mark(); })();

/* ---- the task dock: a window (a repair, a build, a test) can be minimised to a chip at the bottom; it keeps running and the chip shows how far it is; a click brings the window back */
const dock = {items: [], n: 0, timer: null};
function dockStatus(it) {
  const lines = [...it.ov.querySelectorAll(".fx-line, .tt-log > *")], run = !!it.ov.querySelector(".fx-line.run, .loading"), last = lines.filter(l => !l.classList.contains("run")).pop();
  const bad = !run && !!it.ov.querySelector(".fx-line.bad:last-child, .err");
  const text = last ? ((last.querySelector("span") || last).textContent || "").trim().replace(/\s+/g, " ").slice(0, 90) : run ? "working…" : "open";   // the text only, without the "·" marker
  return {run, bad, text};
}
function dockRender() {
  let el = $("#dock");
  if (!dock.items.length) { if (el) el.remove(); clearInterval(dock.timer); dock.timer = null; return; }
  if (!el) { el = document.createElement("div"); el.id = "dock"; document.body.appendChild(el); }
  // a chip is created once (its entry animation plays once) and then only its text and colour are updated in place: no jump every tick
  [...el.children].forEach(c => { if (!dock.items.some(it => String(it.id) === c.dataset.id)) c.remove(); });
  dock.items.forEach(it => {
    const s = dockStatus(it);
    let c = el.querySelector(`.dk[data-id="${it.id}"]`);
    if (!c) {
      c = document.createElement("div"); c.className = "dk"; c.dataset.id = it.id; c.onclick = () => dockOpen(it.id);
      c.innerHTML = `<i class="dk-ic"></i><span class="dk-t"><b>${esc(it.title)}</b><small></small></span><button class="dk-x" title="Remove from the bar">✕</button>`;
      c.querySelector(".dk-x").onclick = e => { e.stopPropagation(); dockDrop(it.id); };
      el.appendChild(c);
    }
    const cls = "dk " + (s.run ? "run" : s.bad ? "bad" : "ok"), ic = s.run ? "" : s.bad ? "!" : "✓", icEl = c.querySelector(".dk-ic"), sm = c.querySelector("small");
    if (c.className !== cls) c.className = cls;
    if (icEl.textContent !== ic) icEl.textContent = ic;
    if (sm.textContent !== s.text) { sm.textContent = s.text; c.title = s.text; }
  });
  if (!dock.timer) dock.timer = setInterval(dockRender, 1500);
}
window.minModal = () => {
  const m = $("#modal"), ov = m && m.firstElementChild;
  if (!ov) return;
  const title = ((ov.querySelector(".modal-head b") || {}).textContent || "Task").replace(/^[^\w\u0600-\u06FF]+/, "").trim() || "Task";
  m.removeChild(ov); ov.classList.add("docked");
  dock.items.push({id: ++dock.n, title, ov});
  state.open = null; state.edit = null; state.con = null; state.flt = null; state.wtab = null; state.build = null; state.detect = null; cf.open = null; cf.edit = null; cf.subEdit = null; cf.add = null; cf.sub = null;
  dockRender(); refresh();
};
window.dockOpen = id => {
  const i = dock.items.findIndex(x => x.id === id); if (i < 0) return;
  const it = dock.items.splice(i, 1)[0], m = $("#modal") || (() => { const x = document.createElement("div"); x.id = "modal"; document.body.appendChild(x); return x; })();
  if (m.firstElementChild) minModal();            // what is open now goes into the bar first
  m.innerHTML = ""; it.ov.classList.remove("docked"); m.appendChild(it.ov); dockRender();
};
window.dockDrop = id => { dock.items = dock.items.filter(x => x.id !== id); dockRender(); };