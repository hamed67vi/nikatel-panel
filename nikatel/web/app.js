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
  if (opts.method && String(opts.method).toUpperCase() !== "GET") state.setCache = null;      // something changed: the settings page reads again
  let r, body = null;
  try {
    r = await fetch(path, Object.assign({headers: {"Content-Type": "application/json"}}, opts, ctl ? {signal: ctl.signal} : {}));
    try { body = await r.json(); } catch (e) { if (e.name === "AbortError") throw e; /* no json body */ }
  } catch (e) { if (e.name === "AbortError") return new Promise(() => {}); e.message = e.message + " — " + apiWhat(path, opts); throw e; }
  if (r.status === 401) { showLogin(); throw new Error("not logged in"); }
  if (!r.ok) {
    const rid = r.headers.get("X-Request-Id");
    let msg = (body && (body.error || body.detail)) || "HTTP " + r.status;      // docs/api.md: every error is {ok:false, error, code}
    if (Array.isArray(msg)) msg = msg.map(x => (x.loc || []).slice(-1)[0] + ": " + x.msg).join("; ");
    const bare = /^HTTP \d+$/.test(msg);
    if (bare && [502, 503, 504].includes(r.status)) msg = "The panel did not answer (HTTP " + r.status + "): it is most likely restarting after an update. Wait about 10 seconds and try again";   // the server said nothing: name what was being done, so the message is never just a number
    throw new Error(msg + (bare || /^The panel did not answer/.test(msg) ? " — " + apiWhat(path, opts) : "") + (rid && r.status >= 500 ? " · id " + rid : ""));
  }
  return body;
}
/* a script error of this page is told to the panel - ONE line in its log with the request id (at most 5 a page load, the same one once), so what the owner saw can be found (review 2026-10-16: they vanished). A plain fetch: api() would show the sign-in page on a 401. */
(() => {
  let sent = 0; const seen = new Set();
  const tell = (message, source, line, stack) => {
    const key = String(message).slice(0, 80) + "|" + line; if (sent >= 5 || seen.has(key) || message === "Script error." || /^ResizeObserver loop/.test(String(message))) return;      // (a cross-origin error says nothing; "ResizeObserver loop ..." is the browser's harmless note when a box is measured while it is drawn) seen.add(key); sent++;
    let page = ""; try { page = String(state.page || ""); } catch (e) { /* the state is not made yet */ }
    try { fetch("/api/client-error", {method: "POST", headers: {"Content-Type": "application/json"}, keepalive: true, body: JSON.stringify({message: String(message).slice(0, 400), source: String(source || "").slice(0, 200), line: +line || 0, stack: String(stack || "").slice(0, 1500), page: page.slice(0, 60)})}).catch(() => {}); } catch (e) { /* nothing more can be done about it */ }
  };
  window.addEventListener("error", e => tell(e.message || "error", e.filename, e.lineno, e.error && e.error.stack));
  window.addEventListener("unhandledrejection", e => { const r = e.reason; if (r && r.name === "AbortError") return; if (/Failed to fetch|NetworkError|Load failed|not logged in/i.test(String((r && r.message) || r))) return; tell("unhandled rejection: " + ((r && r.message) || r), "", 0, r && r.stack); });
})();
/* Messages of the panel go to a status bar at the bottom (not into a pop-up in the corner): bright for a few seconds, then quiet but still readable. Click it for the last ones. */
let toastT;
const MSGS = [];
function toast(text, cls, undo) {      // undo: a function; the status bar then shows an Undo button for as long as the message is bright
  const bar = $("#statusbar"); if (!bar) return;
  MSGS.unshift({t: Date.now(), text: String(text), cls: cls || ""}); MSGS.length = Math.min(MSGS.length, 40);      // the log keeps every message
  const hm0 = $("#modal .modal > .hmsg");      // a window is open: the message shows in the window ONLY; the status bar behind it stays still (a message there changed its height and made the page behind jump)
  if (!hm0 || undo) {
  bar.querySelectorAll(".sb-undo").forEach(b => b.remove());
  bar.className = "statusbar live " + (cls || "");
  $(".sb-text", bar).textContent = String(text); $(".sb-time", bar).textContent = new Date().toLocaleTimeString(undefined, {hour12: false});
  if (undo) { const b = document.createElement("button"); b.type = "button"; b.className = "sb-undo"; b.textContent = "Undo"; b.onclick = e => { e.stopPropagation(); b.remove(); undo(); }; $(".sb-text", bar).after(b); }
  clearTimeout(toastT); toastT = setTimeout(() => { bar.classList.remove("live"); bar.querySelectorAll(".sb-undo").forEach(b => b.remove()); }, undo ? 9000 : 7000);
  }
  const hm = hm0;      // a window that is open shows the outcome small, on its top edge (a notch over the border): it never covers a button and never changes the size of the window
  if (hm) {
    const stamp = String(Date.now()); hm.dataset.t = stamp; hm.className = "hmsg " + (cls || "info"); hm.title = String(text);
    hm.innerHTML = `<span onclick="this.parentElement.classList.toggle('open')">${esc(String(text))}</span><time>${new Date().toLocaleTimeString(undefined, {hour12: false})}</time><button type="button" class="mm-x" title="Dismiss" aria-label="Dismiss" onclick="this.parentElement.innerHTML = ''; this.parentElement.className = 'hmsg'">×</button>`;
    if (cls !== "bad") setTimeout(() => { if (hm.isConnected && hm.dataset.t === stamp) { hm.innerHTML = ""; hm.className = "hmsg"; } }, 12000);
    return;
  }
  const mb = $("#modal .modal-body");          // a window with no room in its header shows the outcome at its top: nobody can read the bar behind it
  if (mb) {      // laid over the top of the window (not in its flow): the window never grows or shrinks because a message came; a small × removes it, a good one goes by itself
    let w = mb.querySelector(".modal-msg-wrap"); if (!w) { w = document.createElement("div"); w.className = "modal-msg-wrap"; w.innerHTML = '<div class="modal-msg"></div>'; mb.prepend(w); }
    const m = w.firstElementChild, stamp = String(Date.now()); w.dataset.t = stamp;
    m.className = "modal-msg " + (cls || "info"); m.innerHTML = `<span>${esc(String(text))}</span><time>${new Date().toLocaleTimeString(undefined, {hour12: false})}</time><button type="button" class="mm-x" title="Dismiss" aria-label="Dismiss" onclick="this.closest('.modal-msg-wrap').remove()">×</button>`;
    if (cls !== "bad") setTimeout(() => { if (w.isConnected && w.dataset.t === stamp) w.remove(); }, 12000);
  }
}
/* the log box (a click on the status bar): ONE fixed size; the tab "This session" lists what the panel said since the page was opened, the tab "History" the events the panel wrote earlier (kept on the server) */
window.sbHistory = (tab, only) => {
  tab = tab === "history" ? "history" : "now"; state.sbOnly = only === undefined ? state.sbOnly || "all" : only;
  const head = `<div class="tabs lg-tabs"><button class="${tab === "now" ? "on" : ""}" onclick="sbHistory('now')">This session<i class="cnt">${MSGS.length}</i></button><button class="${tab === "history" ? "on" : ""}" onclick="sbHistory('history')">History</button>${tab === "history" ? `<span class="sp"></span><button class="${state.sbOnly === "all" ? "on" : ""}" onclick="sbHistory('history', 'all')">All</button><button class="${state.sbOnly === "bad" ? "on" : ""}" onclick="sbHistory('history', 'bad')">Problems</button>` : ""}</div>`;
  const now = MSGS.length ? MSGS.map(m => `<div class="msg-row ${m.cls === "bad" ? "bad" : m.cls === "ok" ? "ok" : "info"}"><time>${new Date(m.t).toLocaleTimeString(undefined, {hour12: false})}</time><i></i><span>${esc(m.text)}</span></div>`).join("") : '<div class="empty">Nothing yet since this page was opened. Older events are in History.</div>';
  showModal("<b>Messages</b>", head + `<div class="lg-list" id="lg-list">${tab === "now" ? now : '<div class="empty">Loading the history…</div>'}</div>`, "wide logwin");
  if (tab === "history") api("/api/events?limit=300").then(ev => { const el = $("#lg-list"); if (!el || state.sbTab === "now") return; const list = state.sbOnly === "bad" ? ev.events.filter(e => e.level !== "info" && e.level !== "ok") : ev.events; el.innerHTML = eventsHtml(list); }).catch(e => { const el = $("#lg-list"); if (el) el.innerHTML = `<div class="empty">${esc(e.message)}</div>`; });
  state.sbTab = tab;
};

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
/* the public demo (NIKATEL_DEMO): a small pill that says the data is invented and nothing can be changed */
function demoBanner() {
  if (document.getElementById("demo-pill")) return;
  const d = document.createElement("div");
  d.id = "demo-pill";
  d.innerHTML = "<b>Demo</b> · نسخه نمایشی · داده‌ها ساختگی و فقط‌خواندنی است";
  document.body.appendChild(d);
  if (window.I18N && I18N.lang !== "en") I18N.set("en");      // the Persian text is not ready for the demo yet: English only, and no switch
  document.querySelectorAll(".lang-btn").forEach(b => b.remove());
  document.querySelectorAll("#nav a[data-page=bot]").forEach(a => a.remove());      // the shop bot needs a real bot server: nothing to show in the demo
  document.querySelectorAll("#logout, .signout, [onclick*=logout]").forEach(b => b.remove());      // there is no sign-in to sign out of
}
function showApp() { $("#login").classList.add("hidden"); $("#app").classList.remove("hidden"); api("/api/settings/nodes").then(r => { window.NODE_CERT = r.marzban_cert || ""; }).catch(() => {}); }

/* sign-in has three steps: password -> (authenticator code) ; or "forgot the password". The page is built from scratch in the look of the UI kit (the owner, 2026-10-16): a card, one step at a time, an eye (show / hide) on every password box, a Caps Lock hint,
   the button shows it is working and cannot be pressed twice, the message is a box (red = wrong, green = done). No inline handlers: the page works with a strict Content-Security-Policy. */
const ls = {step: "pass", ticket: "", busy: false};
const lgMsg = (text, kind) => { const el = $("#login-err"); if (!el) return; el.textContent = text || ""; el.className = "lg-msg" + (text ? " " + (kind || "bad") : ""); };
function lgEye(b, on) {      // the eye of a password box: on = the password is shown
  const inp = b.closest(".lg-in").querySelector("input"), t = on ? "Hide password" : "Show password";
  inp.type = on ? "text" : "password"; b.setAttribute("aria-pressed", on ? "true" : "false"); b.title = t; b.setAttribute("aria-label", t);
}
function loginStep(s) {
  ls.step = s;
  for (const k of ["pass", "2fa", "recover"]) $("#ls-" + k).classList.toggle("hidden", k !== s);
  lgMsg("");
  document.querySelectorAll("#login-form .lg-eye").forEach(b => lgEye(b, false));      // every eye is shut again: a password never stays on show from one step to the next
  const caps = $("#lg-caps"); if (caps) caps.classList.add("hidden");
  const first = $("#ls-" + s + " input:not([type=hidden])"); if (first) setTimeout(() => first.focus(), 30);
}
const lgPress = (el, fn) => { el.addEventListener("click", fn); el.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(); } }); };      // a link that acts as a button: Enter and Space work too
document.querySelectorAll("#login-form .lg-eye").forEach(b => b.addEventListener("click", () => lgEye(b, b.getAttribute("aria-pressed") !== "true")));
document.querySelectorAll("#login-form input").forEach(i => { if (i.dataset.pw !== undefined || i.type === "password") { i.dataset.pw = "1"; const caps = e => { const on = !!(e.getModifierState && e.getModifierState("CapsLock")), c = $("#lg-caps"); if (c && ls.step === "pass") c.classList.toggle("hidden", !on); }; i.addEventListener("keydown", caps); i.addEventListener("keyup", caps); i.addEventListener("blur", () => { const c = $("#lg-caps"); if (c) c.classList.add("hidden"); }); } });
lgPress($("#to-recover"), async () => { loginStep("recover"); try { const o = await fetch("/api/auth/recovery-options").then(r => r.json()); $("#rc-tg").classList.toggle("hidden", !o.telegram); } catch (e) { /* offline */ } });
document.querySelectorAll("#login-form [data-back]").forEach(a => lgPress(a, () => { ls.ticket = ""; loginStep("pass"); }));
$("#rc-tg").addEventListener("click", async () => { try { await api("/api/auth/recover/telegram", {method: "POST"}); lgMsg("A code was sent to your Telegram (valid 10 minutes).", "ok"); } catch (err) { lgMsg(err.message, "bad"); } });
$("#lg-lang").addEventListener("click", () => { if (window.toggleLang) toggleLang(); });
$("#login-form").onsubmit = async e => {
  e.preventDefault(); if (ls.busy) return;      // (a second press while the first answer is awaited does nothing)
  const form = e.target, f = new FormData(form), btns = [...form.querySelectorAll(".lg-go")];
  lgMsg("");
  ls.busy = true; form.dataset.busy = "1"; btns.forEach(b => { b.disabled = true; });
  try {
    if (ls.step === "pass") {
      const r = await api("/api/auth/login", {method: "POST", body: JSON.stringify({user: f.get("user"), password: f.get("password")})});
      if (r.need_2fa) { ls.ticket = r.ticket; loginStep("2fa"); return; }
    } else if (ls.step === "2fa") {
      await api("/api/auth/login/2fa", {method: "POST", body: JSON.stringify({ticket: ls.ticket, code: f.get("code")})});
    } else {
      if (f.get("rnew") !== f.get("rnew2")) { lgMsg("the two passwords differ", "bad"); return; }
      await api("/api/auth/recover", {method: "POST", body: JSON.stringify({code: f.get("rcode"), new: f.get("rnew")})});
      form.reset(); loginStep("pass"); lgMsg("✓ The password is changed. Sign in with the new one.", "ok"); return;
    }
    ls.ticket = ""; form.reset(); loginStep("pass");
    showApp(); go(state.page);
  } catch (er) {
    lgMsg(er.message, "bad");
    const field = ls.step === "pass" ? form.elements.password : ls.step === "2fa" ? form.elements.code : null; if (field) { field.focus(); field.select(); }      // the wrong one is selected: typing again replaces it
  } finally { ls.busy = false; delete form.dataset.busy; btns.forEach(b => { b.disabled = false; }); }
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
const state = {page: localStorage.getItem("nk.page") || "dashboard", tab: localStorage.getItem("nk.tab") || "iran", q: "", open: null, edit: null, logs: {level: "", domain: "", q: "", origin: ""}};
const PAGES = {};
const INFO_GW = "Tunnels through your own configs, for when Iran is cut off from the world: the configs are shared by the ones that are alive, and a port on each chosen Iran server leads to each chosen foreign server and port.";
const INFO = {
  dashboard: ["Dashboard", "What needs attention right now, and what happened."],
  nodes: ["Servers", "Your profiles - Iran servers, foreign servers and PasarGuard nodes - with their state and resources. Everything starts with Add profile; the servers the panel already has are picked there."],
  servers: ["Servers", "Every Iran and foreign server: reachable, load, memory, disk. Sampled every 2 minutes over SSH."],
  tunnels: ["Network", "The CDN records, every tunnel between an Iran server and a foreign server, the Tunnel Doctor that repairs them, and the outbound pool."],
  events: ["Events", "One plain sentence per thing that happened. Newest first."],
  jobs: ["Jobs", "The background jobs of the panel and whether they succeed."],
  logs: ["Logs", "The panel's own log, newest first. Secrets are masked before they are written."],
  settings: ["Settings", "Account, Telegram and backup, the probe, appearance and about, AI and automation."],
  pgpanel: ["Panel", "Everything of the Marzban or PasarGuard panel you pick, in tabs: users, hosts, access, core, admins and the settings of the panel."],
};

/* top-level pages and their sub-tabs: the sidebar shows the page, the bar under the header the sub-tab */
const NAV_ALIAS = {servers: "nodes", gateway: "tunnels", monitor: "nodes", actions: "tunnels/repairs", events: "nodes", jobs: "nodes", logs: "nodes",      // an old page name opens the page that holds it ("page/tab": on that tab)
  pghosts: "pgpanel/configs", pgaccess: "pgpanel/configs", pgcore: "pgpanel/configs", pgadmins: "pgpanel/admins"};      // (1.47.0: the sections of PasarGuard are the tabs of the Panel page; 1.54.0: Hosts, Access and Core are ONE section, Configs)
const NAV_PN = {pghosts: "hosts", pgaccess: "groups", pgcore: "cores"};      // (and an old name opens the inner tab of Configs it used to be)
if (state.sub && state.sub.nodes === "monitor") state.sub.nodes = "lab";
const SERVER_DOCTOR = false;   // the Server Doctor tab of Servers is switched off for now (PAGES.serverDoctor stays); set true to bring it back
const OUTBOUND_UI = false;      // the Outbound tab of Network is hidden (the owner, 2026-10-15: "do not show Outbound and the Shop bot in the panel until we get to them later"); true brings it back
const SHOPBOT_UI = false;       // the Shop bot item of the menu is hidden the same way (its page opens the Dashboard while this is false)
const CONFIGS_UI = false;      // the Configs list is switched off and hidden (no tab, no card, no problem rows) until the project grows (the owner, 2026-10-14, roadmap #98); the probe and the routes keep running; set true to bring it back
Object.assign(NAV_ALIAS, CONFIGS_UI ? {panels: "configs"} : {panels: "pgpanel/users", configs: "pgpanel/users"});      // (the Users of the panels are the first tab of the Panel page while the Configs list is off)
const SUB = {
  nodes: [["lab", "All", "Every profile you added - Iran servers, foreign servers and PasarGuard nodes - in one list with their state, load and the last 24 hours. Open one for Overview, Node and Monitor; everything that can be done to it is in its ⋯ menu."], ["add", "＋ Add profile", "Add a profile: a server with its name, its tag (the profile it is listed under) and its address.", "act"], ...(SERVER_DOCTOR ? [["doctor", "🩺 Server Doctor", "Finds why a server is off or under pressure, in plain words, and says what to do: its SSH login, a changed identity, the load, a failing GRE link, a filtered address."]] : [])],
  tunnels: [["list", "⇄ Tunnel manager", "Every tunnel between an Iran and a foreign server: units, GRE link, peer ping, forwarded ports. Checked every minute, grouped by type. Create a new one with the button."], ["repairs", "🩺 Tunnel Doctor", "Finds why a tunnel is down and fixes it; in the mode that moves servers, the tunnels and DNS of a dead Iran server go to another Iran server by themselves."], ["dns", "🌐 CDN", "The DNS records of your domains in every account (ArvanCloud, Cloudflare) and which server each one points at; point one at another Iran server by hand. The Doctor re-points them when it moves a server."], ...(OUTBOUND_UI ? [["gateway", "🌉 Outbound", INFO_GW]] : [])],      // (the owner, 2026-10-17: "bring the CDN down, under the Tunnel Doctor": the Tunnel manager is the first tab again; 2026-10-14 it was CDN, Tunnel manager, Tunnel Doctor) Outbound and the Shop bot are HIDDEN for now (the owner, 2026-10-15: "do not show them until we get to them later"; roadmap #92 / #79): OUTBOUND_UI / SHOPBOT_UI = true brings them back
  configs: [...(CONFIGS_UI ? [["configs", "⚡ Configs", "Every client config the probe keeps alive and tests from inside Iran: reachable, ping, daily speed. Add links or import a subscription."]] : []), ["panels", "🧩 Users", "The users of your Marzban and PasarGuard panels, and everything else the panels hold: hosts, groups, nodes, cores, admins and settings. Pick a panel in the bar."]],      // the page is called Users; with the Configs tab off it has one tab, so the tab bar is not drawn
};
/* the Panel page (1.47.0, the owner: "when we click Panel, show everything as tabs"): ONE page for the panel you pick, with a tab for each section of PasarGuard that was a page of its own (what PG_SUBS says about the tabs inside each) */
SUB.pgpanel = [
  ["settings", "General", "The panel you pick as a whole: its usage and its settings (Telegram, webhook, notifications, events, subscription, HWID, general and the user template)."],      // (the first tab, 2026-10-14: it was "Panel settings", the last one)
  ["users", "Users", "The users of the panel you pick: search, status, edit, bulk actions and the tools of the More… menu. Pick a panel in the bar."],
  ["configs", "Configs", "Everything a link of the panel you pick is made of, in one place: Add (choose a protocol: the inbound, the group, the host and a template are made for you), the Hosts (address, port and TLS of every link), the Groups (which inbounds a user gets) and the Core (the Xray config, with a backup before every change)."],
  ["admins", "Admins", "The admins of the panel you pick, their roles and their API keys."],
];
/* a tab's 4th element: "off" = switched off (grey, cannot be chosen) or "act" = not a tab but a door to an action (the Lab's "Add profile": it is at the END of its list in the menu, with a "+" after its name, and a button in the bar;
   it never is the open tab; SUB_ACT holds what it does) */
const SUB_ACT = {};
window.subAct = (page, k) => { const f = SUB_ACT[page + "/" + k]; if (f) f(); };
const subBarOn = page => !!SUB[page] && SUB[page].filter(x => x[3] !== "act").length > 1;      // a page with a single tab has no tab bar (a door to an action is no tab)
state.sub = JSON.parse(localStorage.getItem("nk.sub") || "{}");
function sub(page) { const list = (SUB[page] || []).filter(x => x[3] !== "act"); if (!list.length) return ""; const cur = state.sub[page]; return list.some(x => x[0] === cur && x[3] !== "off") ? cur : (list.find(x => x[3] !== "off") || list[0])[0]; }       // a tab that is switched off is never the current one
function renderSub() {
  const list = SUB[state.page], box = $("#subnav");
  if (!list) return;
  const cur = sub(state.page);
  const quiet = t => String(t).replace(/^((?:\p{Extended_Pictographic}\uFE0F?|[\u21C4\u26A1\u2713\u2714])\s*)/u, (m, ic) => `<span class="tab-ic">${ic.trim()}</span> `);      // the emoji of a tab is drawn in one quiet tone (see .tab-ic)
  const nop = k => k !== "settings" && state.page === "pgpanel" && window.labNoPanel && labNoPanel();      // (no panel yet: those tabs are dim and quiet)
  morphHtml(box, `<div class="tabs sub-tabs">${list.map(([k, label, , off]) => nop(k) ? `<button data-k="${k}" class="sub-off" disabled aria-disabled="true">${quiet(label)}</button>` : off === "off" ? `<button data-k="${k}" class="sub-off" disabled aria-disabled="true" title="Switched off for now: the important work comes first (roadmap #92)">${quiet(label)}</button>` : off === "act" ? `<button data-k="${k}" class="sub-act" onclick="subAct('${state.page}', '${k}')">${quiet(label)}</button>` : `<button data-k="${k}" class="${k === cur ? "on" : ""}" onclick="setSub('${state.page}', '${k}')">${quiet(label)}</button>`).join("")}</div>`);
  $("#subtitle").textContent = list.find(x => x[0] === cur)[2];
}
function subSet(page, k) {      // what changes when a tab of a page is chosen (setSub = this and a redraw; the left menu calls it through go)
  if (page === "nodes") state.fastRender = true; if (state.sub[page] !== k) { state.stab = "account"; state.sstab = null; if (typeof pn !== "undefined") { pn.tab = "users"; pn.uoff = 0; } state.mtab = "all"; } state.sub[page] = k; localStorage.setItem("nk.sub", JSON.stringify(state.sub)); state.open = null; state.edit = null; state.build = null; state.q = "";
}
window.setSub = (page, k) => {      // a tab of the page (the owner, 2026-10-16: "the page jumps when I switch"): what the old tab still waited for is dropped - a late answer must never paint the old tab over the new one - and the new tab starts at the top
  if (state.sub[page] !== k || state.page !== page) { navCtl.abort(); navCtl = new AbortController(); navSeq++; state.resetScroll = true; const m = $(".main"); if (m) m.scrollTop = 0; }
  subSet(page, k); refresh();
};
const TOP_TABS = () => { Object.assign(sb, {tab: "overview", tool: "miniapp", payState: "confirm", payPage: 0}); localStorage.setItem("nk.sb.tab", "overview"); state.stab = "account"; state.sstab = null; localStorage.setItem("nk.stab", "account"); if (typeof pn !== "undefined") { pn.tab = "users"; pn.uoff = 0; } state.mtab = "all"; state.wtab = "overview"; };
/* a small animated drawing beside the title of the main pages: lights of a server, packets in a tunnel, bars of a panel, the blink of the shop bot */
const TITLE_ANIM = {
  nodes: `<svg class="t-anim srv-anim" viewBox="0 0 32 30" width="30" height="28" aria-hidden="true"><rect x="3" y="2" width="26" height="7.5" rx="2.2"/><rect x="3" y="11.2" width="26" height="7.5" rx="2.2"/><rect x="3" y="20.4" width="26" height="7.5" rx="2.2"/><circle class="l1" cx="8" cy="5.75" r="1.5"/><circle class="l2" cx="13" cy="5.75" r="1.5"/><circle class="l3" cx="8" cy="14.95" r="1.5"/><circle class="l1" cx="13" cy="14.95" r="1.5"/><circle class="l2" cx="8" cy="24.15" r="1.5"/><circle class="l3" cx="13" cy="24.15" r="1.5"/><path d="M20 5.75h6M20 14.95h6M20 24.15h6"/></svg>`,
  tunnels: `<svg class="t-anim tun-anim" viewBox="0 0 48 26" width="46" height="26" aria-hidden="true"><circle class="nd" cx="5" cy="13" r="4"/><circle class="nd" cx="43" cy="13" r="4"/><path class="tube" d="M10 8.5h28M10 17.5h28"/><circle class="pk r" cx="12" cy="8.5" r="1.9"/><circle class="pk r d" cx="12" cy="8.5" r="1.9"/><circle class="pk l" cx="36" cy="17.5" r="1.9"/><circle class="pk l d" cx="36" cy="17.5" r="1.9"/></svg>`,
  configs: `<svg class="t-anim pan-anim" viewBox="0 0 32 28" width="32" height="28" aria-hidden="true"><rect class="win" x="2" y="2" width="28" height="24" rx="3.5"/><path class="bar0" d="M2 8h28"/><rect class="b b1" x="7" y="12" width="4" height="10" rx="1"/><rect class="b b2" x="14" y="12" width="4" height="10" rx="1"/><rect class="b b3" x="21" y="12" width="4" height="10" rx="1"/><circle class="dt" cx="6" cy="5" r="1"/><circle class="dt" cx="10" cy="5" r="1"/></svg>`,
  settings: `<svg class="t-anim gear-anim" viewBox="0 0 30 30" width="28" height="28" aria-hidden="true"><g class="gear"><path d="M15 3.5l2 .4.7 3 2.2 1 2.6-1.6 2.7 2.7-1.6 2.6 1 2.2 3 .7.4 2-.4 2-3 .7-1 2.2 1.6 2.6-2.7 2.7-2.6-1.6-2.2 1-.7 3-2 .4-2-.4-.7-3-2.2-1-2.6 1.6-2.7-2.7 1.6-2.6-1-2.2-3-.7-.4-2 .4-2 3-.7 1-2.2-1.6-2.6 2.7-2.7 2.6 1.6 2.2-1 .7-3z" transform="translate(0 0) scale(.9) translate(1.7 1.7)"/><circle cx="15" cy="15" r="4"/></g></svg>`,
  bot: `<svg class="t-anim bot-anim" viewBox="0 0 30 30" width="30" height="30" aria-hidden="true"><path class="ant" d="M15 3v4"/><circle class="ap" cx="15" cy="3" r="1.8"/><rect class="hd" x="4" y="8" width="22" height="17" rx="5"/><ellipse class="eye" cx="11" cy="16" rx="2.3" ry="2.6"/><ellipse class="eye" cx="19" cy="16" rx="2.3" ry="2.6"/><path class="mo" d="M11 21.2h8"/></svg>`,
};const TABBED = new Set(["tunnels", "pgpanel", "configs"]);      // the pages whose first box is a toolbar the tabs sit on
function go(page, tab) {      // `tab`: a section of the left menu - the page opens on that tab
  let aliased = false;
  if (NAV_PN[page] && typeof pn !== "undefined") pn.want = NAV_PN[page];
  if (NAV_ALIAS[page]) {   aliased = true;               // an old page name: open the page that holds it, on the right sub-tab
    const [target, want] = NAV_ALIAS[page].split("/"), k = want || page;
    if ((SUB[target] || []).some(x => x[0] === k)) { state.sub[target] = k; localStorage.setItem("nk.sub", JSON.stringify(state.sub)); }      // (kept: a reload comes back to the same tab)
    page = target;
  }
  if (!PAGES[page] || (page === "bot" && !SHOPBOT_UI)) page = "dashboard";      // the Shop bot section is hidden for now (roadmap #79)
  if (page === "pgpanel" && window.labNoPanel && labNoPanel() && (tab || sub("pgpanel")) !== "settings") tab = "settings";      // (no panel yet: Users, Configs and Admins are closed - General opens instead, with no message)
  if (page === "pgpanel" && window.labLocked && labLocked()) { labLockMsg(); return; }      // the Panel needs a connected panel: while there is none it says so instead of opening (lab.js)
  if (state.page !== page && !aliased) { delete state.sub[page]; TOP_TABS(); }   // a tab you come back to always opens on its first sub-tab (callers that want another one set it right after go())
  if (state.page !== page) {   // leaving a page: drop what it still waits for, start the new one at the top and show at once that it is loading (the old page never stays on screen)
    navCtl.abort(); navCtl = new AbortController(); navSeq++; retries = 0; clearTimeout(retryT); state.resetScroll = true;
    const m = $(".main"); if (m) m.scrollTop = 0;
    const pg = $("#page"); if (pg) { if (pg.childElementCount && !pg.querySelector(".loading")) { pg.style.minHeight = pg.offsetHeight + "px"; pg.classList.add("busy"); } else pg.innerHTML = loadingBox(page); pg.classList.remove("enter"); }      // the old page stays (dimmed, still) until the new one has its data: nothing collapses, nothing jumps
    hideModal();
  }   // what the page we leave still waits for is dropped; the new page starts at the top
  if (tab) { if (page === "settings") tabSet(tab); else if (SUB[page]) subSet(page, tab); }      // (after the reset above: the tab asked for is the one that stays)
  state.page = page; state.open = null; state.edit = null; state.q = ""; localStorage.setItem("nk.page", page);
  navMark(true);
  const navOn = $("#nav a.on"); if (navOn && isPhone()) navOn.scrollIntoView({inline: "center", block: "nearest"});      // the bottom bar of a phone scrolls: the item of the open page stays in view
  $("#title").textContent = INFO[page][0]; $("#subtitle").textContent = INFO[page][1];
  if (TITLE_ANIM[page]) $("#title").insertAdjacentHTML("beforeend", TITLE_ANIM[page]);
  if (TABBED.has(page)) document.body.classList.add("tabbed");      // the sub tabs stick to the box under them: set now when the page has them, so the bar never changes shape when the data lands; it is taken away only when the page that has none is drawn (refresh() decides) - never while the old page, still on the screen, would shift
  refresh();
}
/* ---- Server Doctor: what is wrong with a server, in plain words, and what to do about it */
function srvDiagnose(s, tl) {
  const f = s.facts || {}, x = s.extra || {}, err = String(s.last_error || ""), finds = [], steps = [];
  const mineT = tl.filter(t => t.iran_name === s.name || t.foreign_name === s.name).map(t => t.name);
  const add = (lvl, text) => finds.push({lvl, text});
  const step = (label, click, pri = false) => steps.push({label, click, pri});
  if (s.online === false) {
    if (/host key|identification/i.test(err)) { add("bad", "Its SSH identity changed: the server was probably reinstalled, so the panel no longer trusts it."); step("↺ Trust the new server", `forgetKey(${s.id}, '${esc(s.name)}')`, true); }
    else if (noLogin(s)) { add("bad", "Wrong SSH password: the server does not accept the login."); step("🔑 Enter the right password", `srvPassword(${s.id}, '${esc(s.name)}')`, true); step("↻ Check again", `checkServer(${s.id})`); }      // (the owner, 2026-10-15: say the password is wrong and give the button for the right one)
    else if (/timeout|timed out|refused|no route|unreachable|lost|network/i.test(err)) { const why = (err.match(/No route to host|Connection timed out|Connection refused|Network is unreachable|Operation timed out/i) || [""])[0]; add("bad", `SSH cannot reach the server${why ? ": " + why.toLowerCase() : ""} - it is off, or its address / port is wrong or blocked. A password would not help.`); step("↻ Check again", `checkServer(${s.id})`, true); step("✎ Change address / port", `editServer(${s.id})`); }
    else { add("bad", "It does not answer" + (err ? ": " + err.slice(0, 90) : "") + "."); step("↻ Check again", `checkServer(${s.id})`, true); }
    if (mineT.length) add("warn", `${mineT.length} tunnel${mineT.length === 1 ? "" : "s"} depend on it: nothing is done to them until it is back.`);
  } else {
    if ((f.cpu || 0) >= 92) add("bad", `The CPU is at ${Math.round(f.cpu)} %${f.top ? " - the busiest process is " + f.top : ""}.`); else if ((f.cpu || 0) >= 80) add("warn", `The CPU is high: ${Math.round(f.cpu)} %${f.top ? " (" + f.top + ")" : ""}.`);
    if ((f.ram || 0) >= 92) add("bad", `The memory is almost full: ${Math.round(f.ram)} %.`); else if ((f.ram || 0) >= 85) add("warn", `The memory is high: ${Math.round(f.ram)} %.`);
    if ((f.disk || 0) >= 95) add("bad", `The disk is almost full: ${Math.round(f.disk)} %.`); else if ((f.disk || 0) >= 85) add("warn", `The disk is filling up: ${Math.round(f.disk)} %.`);
    const tDown = tl.filter(t => (t.iran_name === s.name || t.foreign_name === s.name) && t.state !== "up");      // a GRE ping can be dropped on a route while the data passes: it is a fault only when a tunnel of this server is not up
    if (x.gre && x.gre.links && !x.gre.ok && tDown.length) { add("bad", `A GRE link of this server does not answer and ${tDown.length === 1 ? "its tunnel " + tDown[0].name + " is" : tDown.length + " of its tunnels are"} not up.`); step("⇄ Test GRE links", `docOpen(${s.id})`); }
    const flt = x.iran_filter; if (flt && ["filtered", "blocked"].includes(flt.level)) { add("bad", "This address is filtered from Iran: users cannot reach it."); step("🛰 See the details", `docOpen(${s.id})`); }
    const rch = (x.reach_ir || {}).level || ""; if (rch === "bad") add("warn", "Its route from Iran is bad: most Iran servers lose packets to it.");
  }
  if (finds.length) step("🖥 Open the server", `docOpen(${s.id})`);
  return {s, finds, steps, bad: finds.some(i => i.lvl === "bad")};
}
window.docOpen = (id, sec) => { go("nodes", "lab"); state.wsec = sec || "overview"; state.wtab = null; state.open = id; state.edit = null; refresh(); };      // the window of a server, on its section (Overview, Node, Monitor): on the Lab page (lab.js draws it, whether or not the server was put on the page)
PAGES.serverDoctor = async () => {
  renderSub();
  const [list, tl] = await Promise.all([api("/api/servers"), api("/api/tunnels").catch(() => [])]);
  state.serversCache = list; state.tunnelsCache = tl;
  const live = list.filter(s => s.enabled !== false), dx = live.map(s => srvDiagnose(s, tl)), sick = dx.filter(d => d.finds.length).sort((a, b) => b.bad - a.bad), fine = dx.filter(d => !d.finds.length);
  const card = d => `<div class="ap-iss ${d.bad ? "off" : ""}"><div class="ap-ih"><span class="ap-flag">${d.s.role === "iran" ? "🇮🇷" : "🌍"}</span><b>${d.s.role === "iran" ? "Iran server" : "Foreign server"} ${esc(d.s.name)}</b><span class="pill ${d.bad ? "err" : "warn"}">${d.s.online === false ? "OFF" : d.bad ? "PROBLEM" : "WATCH"}</span></div>
      <div class="ap-fl">${d.finds.map(i => `<div class="ap-it"><b class="${i.lvl === "bad" ? "err" : "warn"}">${i.lvl === "bad" ? "✕" : "!"}</b> ${esc(i.text)}</div>`).join("")}</div>
      <div class="ap-do dr-steps">${d.steps.map(t => `<button class="sm ${t.pri ? "pri" : ""}" onclick="${t.click}">${t.label}</button>`).join("")}</div></div>`;
  const row = d => { const f = d.s.facts || {}, pc = v => v == null ? "—" : Math.round(v) + "%"; return `<div class="dr-row" title="${esc(d.s.host)}"><i class="st up"></i><b>${esc(d.s.name)}</b><small class="mut">${d.s.role === "iran" ? "Iran" : "foreign"}</small><span class="mut">cpu ${pc(f.cpu)} · ram ${pc(f.ram)}</span></div>`; };
  $("#page").innerHTML = `<div class="grid2 ap-two ap-pair dr-pair"><div class="card"><h3>🩺 Needs a look <span class="sp"></span><span class="pill ${sick.length ? "err" : "ok"}">${sick.length ? sick.length + " to look at" : "all healthy"}</span></h3>
      <div class="ap-att dr-list">${sick.map(card).join("") || '<div class="mut" style="padding:8px 4px">Every server answers and none is under pressure. When one is off, cannot be logged in to, or runs short of CPU, memory or disk, it shows here with the cause in plain words and what to do.</div>'}</div></div>
    <div class="card"><h3>✓ Healthy <span class="sp"></span><small class="mut">${fine.length} of ${live.length} servers</small></h3><div class="ap-hist dr-ok">${fine.map(row).join("") || '<div class="mut" style="padding:8px 4px">none</div>'}</div></div></div>`;
};PAGES.nodes = async () => {
  renderSub();
  const s = sub("nodes");
  if (s === "doctor") return PAGES.serverDoctor();
  if (!PAGES.lab && document.readyState !== "complete") await new Promise(r => addEventListener("load", r, {once: true}));      // (a reload on this page draws it as soon as app.js has been read, before lab.js - a later script - has run)
  if (PAGES.lab) return PAGES.lab();      // the Lab (lab.js): profiles, the quiet list, the window of a server
  $("#page").innerHTML = '<div class="card"><div class="err">The files of the Lab page (lab.js) did not load. Reload the page; if this stays, the upgrade of the panel was not complete.</div></div>';
};
document.querySelectorAll("#nav a").forEach(a => { a.onclick = () => { if (!a.classList.contains("nav-off")) go(a.dataset.page, a.dataset.sub); }; });
document.querySelectorAll('#nav a[data-page="bot"]').forEach(a => { a.hidden = !SHOPBOT_UI; });      // (the Shop bot item: hidden, not greyed, while SHOPBOT_UI is false)
/* the groups of the left menu (the owner, 2026-10-14: like the sidebar of PasarGuard). A group is a head (a page) with a list of sections under it. The little arrow at the right of the head opens and closes
   the list (what was chosen is kept in this browser), the head opens its page AND the list, and a page of a group opens it by itself (and it closes again when another page is opened). The sections of a group are the
   tabs the page already has (Servers: Foreign, Iran...; Tunnels; Panel: General, Users, Hosts, Access, Core, Admins; Settings), made by navBuild at the end of this file. The rail and the phone draw a group as
   its head alone (the tabs are in the bar under the header there); while a group is open in a wide window its tabs are in the menu and the bar is not drawn (app.css). */
const navPlain = t => String(t).replace(/<[^>]*>/g, "").replace(/^[^\p{L}\p{N}]+/u, "").trim();      // the name of a tab without its icon
const navOpenMap = () => { try { const m = JSON.parse(localStorage.getItem("nk.nav.g") || "{}"); return m && typeof m === "object" ? m : {}; } catch (e) { return {}; } };
function navGroupSet(g, open, keep) {      // `keep`: the owner chose it with the arrow (remembered, and the group stays as it is); else it was opened by a page and closes again when another page is opened
  g.dataset.open = open ? "1" : "0";
  const b = g.querySelector(".nav-tg"); if (b) b.setAttribute("aria-expanded", open ? "true" : "false");
  if (keep) { delete g.dataset.auto; const m = navOpenMap(); m[g.dataset.page] = open ? 1 : 0; try { localStorage.setItem("nk.nav.g", JSON.stringify(m)); } catch (e) { /* kept for this visit only */ } }
}
function navGroup(g, open, keep) {      // ONE list is open at a time (the owner, 2026-10-15): opening a group closes the one that was open
  if (open) document.querySelectorAll("#nav .nav-g").forEach(o => { if (o !== g && o.dataset.open === "1") navGroupSet(o, false, true); });
  navGroupSet(g, open, keep);
  navSubBar();
}
const NAV_BAR_TOO = new Set(["tunnels"]);      // Network (the owner, 2026-10-15): its three tabs (Tunnel manager, Tunnel Doctor, CDN) stand under the header even while its list is open in the menu
function navSubBar() {      // while the group of the open page is open its tabs are in the menu: the bar under the header is not drawn then (a wide window; app.css)
  const g = document.querySelector(`#nav .nav-g[data-kind="tabs"][data-page="${state.page}"]`);
  document.documentElement.dataset.navsub = g && SUB[state.page] && g.dataset.open === "1" && !NAV_BAR_TOO.has(state.page) ? "1" : "";
}
function navMark(openIt) {      // which items are the open page (and tab); `openIt` (a page was opened) also opens the group it is in, a redraw of the same page never does
  const page = state.page, tab = page === "settings" ? state.stab : SUB[page] ? sub(page) : "";
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("on", a.dataset.page === page && (a.dataset.sub === undefined || a.dataset.sub === tab)));
  document.querySelectorAll("#nav .nav-g").forEach(g => {
    const inside = g.dataset.page === page;
    g.classList.toggle("has-on", !!g.querySelector(".nav-sub a.on"));
    if (!openIt) return;
    if (inside && g.dataset.open !== "1") { navGroup(g, true, false); g.dataset.auto = "1"; }      // a page of the group opens it by itself ...
    else if (!inside && g.dataset.auto === "1") { navGroup(g, false, false); delete g.dataset.auto; }      // ... and it closes again when another page is opened (a group the owner opened with the arrow stays open)
  });
  navSubBar();
}
const navIcons = () => {      // the icons of the tabs of the menu (lab.js draws the kinds of the Lab with the same ones)
  const globe = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3.2 3.4 3.2 14.6 0 18M12 3c-3.2 3.4-3.2 14.6 0 18"/></svg>';
  return {foreign: globe, iran: '<svg viewBox="0 0 24 24"><path d="M5 21V4M5 4h11l-2.2 3.6L16 11H5"/></svg>', node: SI.cube,
    list: SI.swap, dns: SI.cloud, repairs: SI.pulse, lab: '<svg viewBox="0 0 24 24"><path d="M4 6.5h16M4 12h16M4 17.5h16"/></svg>',
    users: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M9.5 4v16M3 9.5h18"/></svg>', hosts: globe, access: '<svg viewBox="0 0 24 24"><path d="M12 3.5l8.5 4.7L12 13 3.5 8.2z"/><path d="M3.5 12.2L12 17l8.5-4.8M3.5 16.2L12 21l8.5-4.8"/></svg>',
    core: SI.cpu, configs: SI.cpu, admins: '<svg viewBox="0 0 24 24"><path d="M12 3l7 3v5.2c0 4.5-3 7.9-7 9.8-4-1.9-7-5.3-7-9.8V6z"/><path d="M9 12l2.2 2.2L15.2 10"/></svg>', settings: SI.cog};      // (the tab of the Panel page that is called settings is General: the settings of the panel)
};
function navTabs() {      // the tabs that hang under their page: [key, name, icon]
  const ic = navIcons();
  const of = page => (SUB[page] || []).map(t => [t[0], navPlain(t[1]), ic[t[0]] || SI.doc, t[3] === "off" ? "off" : t[3] === "act" ? "act" : false]);      // (a tab that is switched off is in the menu too, grey like Shop bot: the owner asked to see the rest of the tabs of Tunnels; an "act" is a door to an action, drawn in the accent colour with a + after its name, never the open tab)
  return {nodes: of("nodes"), tunnels: of("tunnels"), pgpanel: of("pgpanel"), settings: Object.entries(SET_TABS).filter(([k]) => !SET_OFF.has(k)).map(([k, v]) => [k, navPlain(v[0]), TAB_SVG[k] || SI.gear])};
}
function navBuild() {
  const nav = $("#nav"); if (!nav) return;
  for (const [page, list] of Object.entries(navTabs())) {
    const head = nav.querySelector(`:scope > a[data-page="${page}"]`); if (!head || list.length < 2) continue;
    const g = document.createElement("div"), gh = document.createElement("div"), sub = document.createElement("div"), tg = document.createElement("button");
    g.className = "nav-g"; g.dataset.page = page; g.dataset.kind = "tabs"; g.dataset.open = "0"; gh.className = "nav-gh"; sub.className = "nav-sub"; sub.id = "nav-sub-" + page; tg.type = "button"; tg.className = "nav-tg";
    sub.innerHTML = list.map(([k, name, icon, off]) => off === "act"
      ? `<a class="nav-act" data-page="${page}" data-sub="${k}" data-act="1"><i class="ic"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg></i><span>${esc(name)}</span></a>`      // (an action: the + in front of its name is its icon - "+ Add profile" - and it is the last of the list)
      : `<a data-page="${page}" data-sub="${k}"${off === "off" ? ' class="nav-off" aria-disabled="true" title="Switched off for now: the important work comes first (roadmap #92)"' : ""}><i class="ic">${icon}</i><span>${esc(name)}</span></a>`).join("");
    head.replaceWith(g); gh.append(head, tg); g.append(gh, sub);
    sub.querySelectorAll("a:not(.nav-off)").forEach(a => { a.onclick = () => a.classList.contains("nav-nopanel") ? null : a.dataset.act ? subAct(a.dataset.page, a.dataset.sub) : go(a.dataset.page, a.dataset.sub); });      // (an "act" does what SUB_ACT says: the Lab's Add profile opens the choice of what to add)
  }
  const saved = navOpenMap();
  nav.querySelectorAll(".nav-g").forEach(g => {
    const tg = g.querySelector(".nav-tg"), name = g.querySelector(".nav-gh > a > span").textContent.trim();
    tg.setAttribute("aria-controls", g.querySelector(".nav-sub").id); tg.setAttribute("aria-label", `Show or hide the sections of ${name}`); tg.title = `Show or hide the sections of ${name}`;
    tg.onclick = () => navGroup(g, g.dataset.open !== "1", true);
    if (saved[g.dataset.page] === 1 && !nav.querySelector(".nav-g[data-open='1']")) navGroup(g, true, false); else navGroupSet(g, false, false);      // (several lists remembered as open: only the first stays)
  });
  navTitles(); matchMedia("(max-width: 1180px) and (not ((max-width: 760px) and (pointer: coarse)))").addEventListener("change", navTitles);
  navMark(false);
}
function navTitles() {      // the name of an item is its tooltip only where the words are not on the screen (the rail of icons); in a wide window a tooltip with the same word would only get in the way
  const rail = matchMedia("(max-width: 1180px) and (not ((max-width: 760px) and (pointer: coarse)))").matches;
  document.querySelectorAll("#nav a:not(.nav-off)").forEach(a => { if (rail) a.title = a.querySelector("span").textContent.trim(); else a.removeAttribute("title"); });
}


/* every list (a table or the config cards) sits in a box of a fixed height with its own scrollbar and a header that stays on top; done once for whatever a page draws, also what loads later */
let pendScroll = null;   // the scroll positions a refresh wants back: the list boxes are made a moment after the page is drawn, so they are put back once those exist
/* A phone (a touch screen up to 760 px) has the layout of its own in mobile.css: one column, a tab bar at the bottom, tables as cards. A desktop window that is only made narrow does NOT:
   it keeps the desktop layout and everything shrinks (fluid.css: boxes get smaller, rows stay rows, columns stay columns). */
function isPhone() { return matchMedia("(max-width: 760px) and (pointer: coarse)").matches; }
(() => {
  const pg = $("#page"); if (!pg) return;
  let q = 0;
  const fit = () => { if (isPhone()) return; const fixed = !!pg.querySelector(".cf-top, .lab-root"); /* (the Lab list is a box of ONE fixed height too, whatever the number of its rows: the owner, 2026-10-15) */ pg.querySelectorAll(".lbox").forEach(b => { if (b.matches(".fx-box")) return; const mh = Math.max(150, window.innerHeight - b.getBoundingClientRect().top - 24); b.style.maxHeight = mh + "px"; if (fixed) { b.style.height = mh + "px"; b.style.minHeight = ""; } }); };      // the configs box keeps ONE height for every group, so switching groups never makes it jump   // the box ends exactly at the bottom of the window: the page itself never scrolls, only the list does
  const labelCells = () => { if (!isPhone()) return; pg.querySelectorAll("table.tbl").forEach(tb => { const hs = [...tb.querySelectorAll("thead th")].map(h => h.textContent.trim()); if (!hs.length) return; tb.querySelectorAll("tbody tr").forEach(tr => { let i = 0; for (const td of tr.children) { if (td.tagName !== "TD") continue; if (!td.hasAttribute("data-l")) td.setAttribute("data-l", hs[i] || ""); i += +td.getAttribute("colspan") || 1; } }); }); };
  const run = () => { q = 0; pg.querySelectorAll("table.tbl, .cf-grid").forEach(tb => { if (tb.closest(".lbox, .modal, .card, .ibox, .gw-out, .fix, .sbox")) return; const w = document.createElement("div"); w.className = "lbox" + (tb.classList.contains("joined") ? "" : " full"); tb.replaceWith(w); w.appendChild(tb); }); fit(); labelCells(); if (pendScroll) { scrollRestore(pendScroll); pendScroll = null; } };
  new MutationObserver(() => { if (!q) q = requestAnimationFrame(run); }).observe(pg, {childList: true, subtree: true});
  window.addEventListener("resize", () => requestAnimationFrame(fit));
})();
/* A refresh rebuilds the page from fresh data. Every scrolling box keeps its position through it (the page, a window, the lists inside the dashboard, the logs),
   so nothing jumps back to the top when new data arrives. Opening another page starts at the top. */
const SCROLLERS = [".main", ".lbox", ".modal-body", ".act-list", ".pb-list", ".hl-list", ".tt-log", ".tt-res", ".hp-list", ".sub-nodes", ".ob-inbounds", ".fix"];
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
/* every tab, sub-tab and range switch of the panel: the content that follows comes in softly (see .soft-in, and the animation of a tab panel in app.css) instead of jumping */
document.addEventListener("click", e => { if (e.target.closest && e.target.closest(".tabs button, #subnav button, .set-tabs button, .tf-tabs button, .srv-tabs button, .act-cats button, .ap-ttabs button")) state.softSwap = true; }, true);
async function refresh() {      // one draw at a time for the page and tab that are open: a second call while that draw runs (the ticker, the Lab's busy timer, a window that closed) asks for ONE more draw after it, with the newest state - never two at once, never out of order (review 2026-10-16)
  if (refresh.p && refresh.seq === navSeq) { refresh.again = true; return refresh.p; }
  const seq = navSeq, p = (async () => { do { refresh.again = false; await refreshNow(); } while (refresh.again && seq === navSeq); })();
  refresh.p = p; refresh.seq = seq;
  try { await p; } finally { if (refresh.p === p) refresh.p = null; }
}
async function refreshNow() {
  routeWrite();      // (the address of the section that is open: a string compare)
  navMark(false);      // (the open tab may have changed: the menu shows it)
  if (window.labLockSync) labLockSync();      // whether a panel is connected (the Panel item of the menu is grey with a lock only while none is): asked at most every 30 s
  const snap = state.resetScroll ? [] : scrollSnapshot();
  state.resetScroll = false;
  const seq = navSeq, pg0 = $("#page"), wasBusy = !!(pg0 && pg0.classList.contains("busy"));
  if (pg0 && pg0.childElementCount && !pg0.querySelector(".loading") && !wasBusy) pg0.style.minHeight = pg0.offsetHeight + "px";      // the page keeps its height while it is redrawn: switching a tab does not make it jump
  const release = () => { setTimeout(() => { if (pg0) pg0.style.minHeight = ""; }, 90); };      // a timer, not an animation frame: it also runs when the tab is in the background
  const ae = document.activeElement, focus = ae && ae.id && ae.matches("input, textarea") ? {id: ae.id, a: ae.selectionStart, b: ae.selectionEnd} : null;   // the box being typed in
  try {
    await PAGES[state.page]();
    if (seq !== navSeq) { release(); return; }                 // another page was opened meanwhile: this one is history
    $("#subnav").classList.toggle("hidden", !subBarOn(state.page));      // the bar of tabs shows (or goes) together with the page it belongs to - not a moment before, when the old page, still on the screen, shifted under it (the owner, 2026-10-16)
    if (focus) { const el = document.getElementById(focus.id); if (el && el !== document.activeElement) { el.focus(); try { el.setSelectionRange(focus.a, focus.b); } catch (e) { /* not a text box */ } } }   // a re-drawn page must not take the cursor out of the search box
    if (retries) toast("✓ Connected again", "ok");
    retries = 0; clearTimeout(retryT);
    if (pg0 && pg0.classList.contains("busy")) { pg0.classList.remove("busy"); void pg0.offsetWidth; pg0.classList.add("enter"); }      // a new page: it fades in over the old one's place
    if (state.softSwap && pg0 && !pg0.classList.contains("enter")) { pg0.classList.remove("soft-in"); void pg0.offsetWidth; pg0.classList.add("soft-in"); }      // a tab was pressed: the new content comes in softly
    state.softSwap = false;
    scrollRestore(snap); pendScroll = snap.length ? snap : null; setTimeout(() => { pendScroll = null; }, 500);
    document.body.classList.toggle("tabbed", !!SUB[state.page] && ((c) => !!c && c.matches(".toolbar, .fstrip, .tiles, .ap-pair, .ap-alert, .ap-intro, .tt-frame, .dr-head, .dr-top, .set-top"))([...$("#page").children].find(c => getComputedStyle(c).display !== "none")));   // the sub tabs stick to the box under them
    if (pg0 && pg0.querySelector(".fx-box")) fxFit();      // (the bar above the page just took its final shape: the boxes fitted to the window are measured again)
    $("#updated").textContent = "updated " + new Date().toLocaleTimeString(undefined, {hour12: false});
    release();
  } catch (e) {
    release();
    if (e.message === "not logged in" || seq !== navSeq) return;
    if (pg0) pg0.classList.remove("busy");
    const transient = /HTTP 50\d|Failed to fetch|NetworkError|Load failed|network/i.test(e.message);
    const pg = $("#page");
    if (pg && (!pg.childElementCount || pg.querySelector(".loading, .page-err") || wasBusy)) pg.innerHTML = errorBox(e.message, transient);   // nothing of this page is on screen yet: say so instead of leaving the old page
    toast("✕ " + e.message, "bad");
    if (transient) { clearTimeout(retryT); retryT = setTimeout(() => { if (seq === navSeq) refresh(); }, Math.min(8000, 1500 * 2 ** Math.min(retries++, 3))); }
  }
}
let lastTick = Date.now();
setInterval(() => { if (Date.now() - lastTick < (state.page === "nodes" ? 8000 : 15000)) return; if (!document.hidden && !$("#app").classList.contains("hidden") && !state.edit && !state.open && !state.nodeWin && !state.build && !["bot", "settings"].includes(state.page) && !cf.open && !cf.edit && !cf.add && !cf.sub && !cf.subEdit && !document.activeElement.matches("input, textarea, select") && !document.querySelector(".nk-dd, .lab-menu, .pn-uchk:checked, .pn-hchk:checked")) { lastTick = Date.now(); refresh(); } }, 2000);   // the servers page follows the pulse (every 10 s on the server)
/* the glass window: one thing opened from a row or a card; closed with ✕, Esc or a click outside */
/* a window that is drawn again changes only what is different, in place: a value that changed is rewritten where it stands, everything else is not touched - so nothing blinks or jumps when Health, Quality, a check ... end */
function morphNode(a, b) {
  if (a.nodeType !== b.nodeType || a.nodeName !== b.nodeName) return false;
  if (a.nodeType === 3 || a.nodeType === 8) { if (a.nodeValue !== b.nodeValue) a.nodeValue = b.nodeValue; return true; }
  if (a.nodeType !== 1) return true;
  if (a === document.activeElement && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) return true;      // what is being typed is never touched
  for (const at of [...a.attributes]) if (!b.hasAttribute(at.name)) a.removeAttribute(at.name);
  for (const at of [...b.attributes]) if (a.getAttribute(at.name) !== at.value) a.setAttribute(at.name, at.value);
  if (a.tagName === "TEXTAREA" || a.tagName === "SELECT" || a.tagName === "INPUT") { if (a.tagName !== "INPUT" || a.type !== "checkbox") return true; a.checked = b.checked; return true; }
  const ak = [...a.childNodes], bk = [...b.childNodes], n = Math.min(ak.length, bk.length);
  for (let i = 0; i < n; i++) if (!morphNode(ak[i], bk[i])) a.replaceChild(bk[i].cloneNode(true), ak[i]);
  for (let i = n; i < bk.length; i++) a.appendChild(bk[i].cloneNode(true));
  while (a.childNodes.length > bk.length) a.removeChild(a.lastChild);
  return true;
}
function morphHtml(el, html) { const t = document.createElement("div"); t.innerHTML = html; if (!el.firstChild) { el.innerHTML = html; return; } const ak = [...el.childNodes], bk = [...t.childNodes], n = Math.min(ak.length, bk.length); for (let i = 0; i < n; i++) if (!morphNode(ak[i], bk[i])) el.replaceChild(bk[i].cloneNode(true), ak[i]); for (let i = n; i < bk.length; i++) el.appendChild(bk[i].cloneNode(true)); while (el.childNodes.length > bk.length) el.removeChild(el.lastChild); }
function showModal(title, body, cls = "", acts = "") {
  let m = $("#modal"); if (!m) { m = document.createElement("div"); m.id = "modal"; document.body.appendChild(m); }
  const head = `${title}<span class="sp"></span>${acts}<button class="sm min-btn" onclick="minModal()" title="Minimise: it keeps running; click its chip at the bottom to come back">—</button><button class="sm" onclick="closeModal()" aria-label="Close">✕<span class="bt"> Close</span></button>`;
  const cur = m.querySelector(".modal"), mb = cur && cur.querySelector(".modal-body");
  if (cur && mb && cur.className.replace(/\s*\bediting\b/, "").trim() === ("modal " + cls).trim()) {      // the same window: only what is inside changes - it does not fly in again and it does not shrink (a tab of the window no longer makes the page jump)
    const keep = mb.scrollTop, msg = mb.querySelector(".modal-msg-wrap"); cur.style.minHeight = cur.offsetHeight + "px";
    morphHtml(cur.querySelector(".modal-head"), head); morphHtml(mb, body); mb.scrollTop = keep;      // (the message on the top edge lives outside the header and the body: a redraw leaves it as it is)
    if (msg && Date.now() - (+msg.dataset.t || 0) < 20000) mb.prepend(msg);      // the outcome of what was just done stays readable after the redraw
    return;
  }
  const still = cur ? " style=\"animation:none\"" : "";      // another window in place of this one (details ↔ edit form): it is already on screen, so it does not fly in again
  m.innerHTML = `<div class="overlay"${still} onmousedown="if(event.target===this && !this.querySelector('.modal.keep'))closeModal()"><div class="modal ${cls}"${still}><div class="modal-head">${head}</div><div class="hmsg"></div><div class="modal-body">${body}</div></div></div>`;
}
function hideModal() { const m = $("#modal"); if (m) m.innerHTML = ""; state.nodeWin = null; }      // (state.nodeWin: a window of the nodes that is not a server's is open - the Servers page does not close it, see pg_nodes.js)
/* the kit menu for a select (the owner, 2026-10-16: "grouped like a MacBook, the same style and colours"): a select with data-dd opens a one-column menu - a heading for every group, a tick before the chosen item, the highlighted one in blue - instead of the browser's own list. The select stays in the form as it is (it holds the value and fires its own change), so nothing that reads it changes. */
let nkDdEl = null, nkDdSel = null, nkDdAnchor = null;
function nkDdClose() {
  const el = nkDdEl; nkDdEl = null; if (nkDdSel) nkDdSel.removeAttribute("aria-expanded"); nkDdSel = null; nkDdAnchor = null;
  document.removeEventListener("mousedown", nkDdOut, true); document.removeEventListener("keydown", nkDdKey, true); document.removeEventListener("scroll", nkDdScroll, true); window.removeEventListener("resize", nkDdClose);
  if (el) { el.classList.remove("open"); setTimeout(() => el.remove(), 140); }
}
const nkDdOut = e => { if (nkDdEl && !nkDdEl.contains(e.target) && !(nkDdSel && nkDdSel.contains(e.target)) && !(nkDdAnchor && nkDdAnchor.contains(e.target))) nkDdClose(); };
const nkDdScroll = e => { if (nkDdEl && !nkDdEl.contains(e.target)) nkDdClose(); };
const nkDdRows = () => nkDdEl ? [...nkDdEl.querySelectorAll(".nk-dd-i:not([disabled])")] : [];
function nkDdKey(e) {
  if (!nkDdEl) return; const rows = nkDdRows(), hl = rows.findIndex(r => r.classList.contains("hl"));
  const go = i => { rows.forEach(r => r.classList.remove("hl")); const r = rows[(i + rows.length) % rows.length]; if (r) { r.classList.add("hl"); r.scrollIntoView({block: "nearest"}); } };
  if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); const s = nkDdSel; nkDdClose(); if (s) s.focus(); }
  else if (e.key === "ArrowDown") { e.preventDefault(); e.stopPropagation(); go(hl + 1); } else if (e.key === "ArrowUp") { e.preventDefault(); e.stopPropagation(); go(hl < 0 ? -1 : hl - 1); }
  else if (e.key === "Home") { e.preventDefault(); go(0); } else if (e.key === "End") { e.preventDefault(); go(rows.length - 1); }
  else if (e.key === "Enter" || e.key === " " || e.key === "Tab") { if (e.key !== "Tab") { e.preventDefault(); e.stopPropagation(); } if (hl >= 0) nkDdPick(+rows[hl].dataset.i); else nkDdClose(); }
}
function nkDdPick(i) {
  const s = nkDdSel; nkDdClose(); if (!s || !s.options[i] || s.options[i].disabled) return;
  if (s.__combo) { s.__combo.value = s.options[i].value; s.__combo.dispatchEvent(new Event("input", {bubbles: true})); s.__combo.focus(); return; }      // (a text field with a list: the chosen name is written into the field)
  if (s.selectedIndex !== i) { s.selectedIndex = i; s.dispatchEvent(new Event("input", {bubbles: true})); s.dispatchEvent(new Event("change", {bubbles: true})); }
  s.focus();
}
const NKDD_IC = {swap: '<path d="M17 4l3 3-3 3M20 7H8M7 20l-3-3 3-3M4 17h12"/>', nodes: '<circle cx="12" cy="5.5" r="2.5"/><circle cx="5.5" cy="17" r="2.5"/><circle cx="18.5" cy="17" r="2.5"/><path d="M12 8v3M10.4 12.4L7 15M13.6 12.4L17 15"/>', wifi: '<path d="M4.5 9.5a11 11 0 0115 0M7.5 12.8a6.8 6.8 0 019 0M10.5 16a2.6 2.6 0 013 0M12 19h.01"/>',
  shield: '<path d="M12 3l7 3v5.2c0 4.5-3 7.9-7 9.8-4-1.9-7-5.3-7-9.8V6z"/>', eyeoff: '<path d="M3 3l18 18M10.6 6.2A9.7 9.7 0 0112 6c4.5 0 8 3.6 9.5 6a14 14 0 01-3 3.6M6.5 7.3A14 14 0 002.5 12c1.5 2.4 5 6 9.5 6 1.5 0 2.9-.4 4.1-1M10 10a3 3 0 004 4"/>', layers: '<path d="M12 3l9 5-9 5-9-5 9-5zM3 12.5l9 5 9-5M3 16.5l9 5 9-5"/>',
  gamepad: '<rect x="3" y="8" width="18" height="10" rx="5"/><path d="M8 11v4M6 13h4M15.5 12.5h.01M18 14.5h.01"/>', gauge: '<path d="M4 17a9 9 0 1116 0M12 17l4-6"/><circle cx="12" cy="17" r="1"/>', globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.2 3 14.8 0 18M12 3c-3 3.2-3 14.8 0 18"/>',
  link: '<path d="M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1"/>', lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/>', antenna: '<circle cx="12" cy="10" r="2"/><path d="M12 12l-3.5 9M12 12l3.5 9M6.5 6.5a8 8 0 000 7M17.5 6.5a8 8 0 010 7M3.5 4a12 12 0 000 12M20.5 4a12 12 0 010 12"/>',
  server: '<rect x="3" y="3" width="18" height="7.5" rx="2.2"/><rect x="3" y="13.5" width="18" height="7.5" rx="2.2"/><path d="M7 6.8h.01M7 17.2h.01"/>', spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>', cube: '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5L12 12l8-4.5M12 12v9"/>'};
function nkDdIc(name, title, g) {      // the little picture of an item (the owner's picture, 2026-10-16): the way of a tunnel, a server, a model; a list with no picture gets none
  if (name === "ttype") {
    if (/TLS Mux/i.test(title)) return g === "Backhaul over GRE" ? ["shield", ""] : ["layers", "violet"];
    if (/TCP Mux/i.test(title)) return ["swap", "blue"];
    if (/^TCP$/i.test(title)) return ["nodes", ""];
    if (/Stealth/i.test(title)) return ["eyeoff", ""];
    if (/PCK/i.test(title)) return ["layers", ""];
    if (/KCP|FEC/i.test(title)) return ["gamepad", ""];
    if (/QUIC/i.test(title)) return ["gauge", ""];
    if (/xDi|ICMP/i.test(title)) return ["antenna", ""];
    if (/SNI/i.test(title)) return ["shield", ""];
    if (/WebSocket Mux/i.test(title)) return [g === "Backhaul over GRE" ? "wifi" : "link", ""];
    if (/TLS WebSocket|WebSocket TLS/i.test(title)) return ["lock", ""];
    if (/WebSocket/i.test(title)) return ["globe", ""];
    if (/UDP/i.test(title)) return ["wifi", ""];
    return ["swap", ""];
  }
  if (name === "iran_id" || name === "foreign_id") return [/Smart/i.test(title) ? "spark" : "server", /Smart/i.test(title) ? "blue" : ""];
  if (name === "model") return ["cube", ""];
  return null;
}
function nkDdItem(o, g, name) {
  let text = o.textContent.trim(); if (g && text.startsWith(g + " · ")) text = text.slice(g.length + 3);      // (the group says it already: "Backhaul over GRE · TCP Mux" is "TCP Mux" under its heading)
  const k = text.indexOf(" — "), title = k > 0 ? text.slice(0, k) : text, note = k > 0 ? text.slice(k + 3) : "", ic = nkDdIc(name, title, g);
  const star = '<i class="nk-dd-star"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/></svg></i>';
  const noteHtml = esc(note).replace(/\s*⭐/, " " + star);
  return `<button type="button" role="option" class="nk-dd-i${o.selected ? " on hl" : ""}${ic ? "" : " noic"}" data-i="${o.index}" aria-selected="${o.selected}" ${o.disabled ? "disabled" : ""} onclick="nkDdPick(${o.index})">${ic ? `<i class="nk-dd-ic ${ic[1]}"><svg viewBox="0 0 24 24" aria-hidden="true">${NKDD_IC[ic[0]]}</svg></i>` : ""}<span class="nk-dd-tx"><span class="nk-dd-t">${esc(title)}</span>${note ? `<span class="nk-dd-n">${noteHtml}</span>` : ""}</span><svg class="nk-dd-ck" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg></button>`;
}function nkDdOpen(sel, anchor) {
  if ((anchor && nkDdAnchor === anchor) || (!anchor && nkDdSel === sel)) { nkDdClose(); return; } nkDdClose(); nkDdAnchor = anchor || null;
  const html = [...sel.children].map(ch => ch.tagName === "OPTGROUP" ? `<div class="nk-dd-g" role="presentation">${esc(ch.label)}</div>` + [...ch.children].map(o => nkDdItem(o, ch.label, sel.__combo ? "model" : sel.name)).join("") : nkDdItem(ch, "", sel.__combo ? "model" : sel.name)).join("");
  const m = document.createElement("div"); m.className = "nk-dd"; m.setAttribute("role", "listbox"); m.innerHTML = html; document.body.appendChild(m); nkDdEl = m; nkDdSel = sel; sel.setAttribute("aria-expanded", "true");
  const r = (anchor || sel).getBoundingClientRect(), w = Math.min(innerWidth - 16, Math.max(r.width, +sel.dataset.ddw || 0));
  m.style.width = w + "px"; m.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + "px";
  const below = innerHeight - r.bottom - 14, above = r.top - 14, h = Math.min(m.scrollHeight + 14, Math.floor(innerHeight * 0.64)), up = below < Math.min(h, 260) && above > below;
  m.style.maxHeight = Math.max(160, Math.min(h, up ? above : below)) + "px"; m.style.top = up ? Math.max(8, r.top - 6 - Math.min(h, above)) + "px" : r.bottom + 6 + "px";
  const on = m.querySelector(".nk-dd-i.on"); if (on) on.scrollIntoView({block: "center"});
  void m.offsetWidth; m.classList.add("open");
  document.addEventListener("mousedown", nkDdOut, true); document.addEventListener("keydown", nkDdKey, true); document.addEventListener("scroll", nkDdScroll, true); window.addEventListener("resize", nkDdClose);
}
/* a text field with a list (the owner, 2026-10-16: "any model the user wants can be added; the list drops down"): type any name, or open the list - the models the panel suggests for that AI, the ones its account really has (read with its key) and the ones you used before (kept in this browser) */
const AI_MODELS = [["claude-fable-5-1", "Fable 5.1 · the most capable"], ["claude-opus-5-5", "Opus 5.5 · deep work"], ["claude-sonnet-5-5", "Sonnet 5.5 · balanced"], ["claude-haiku-5-5", "Haiku 5.5 · fast and light"]];
const AI_MAIN = ["anthropic", "openai", "gemini", "grok", "deepseek"];      // the AIs that always have a row on Settings → Api & Key (and in the first-run card); the others come with "Add another AI", or when they have a key
const AIM_N = {anthropic: "Anthropic (Claude)", openai: "OpenAI (ChatGPT)", gemini: "Google Gemini", grok: "xAI Grok", deepseek: "DeepSeek"};
const AIM_C = {anthropic: "#d97757", openai: "#10a37f", gemini: "#4285f4", grok: "#aab2c5", deepseek: "#4d6bfe", mistral: "#fa520f", groq: "#f55036", openrouter: "#6d7cff", together: "#2f7bff", qwen: "#7b6cf6", kimi: "#2b8cff", perplexity: "#20b8cd", glm: "#4a6bff", custom: "#98a2bd"};      // the colour of the tile of each AI
const AIM_M = {anthropic: "Cl", openai: "GP", gemini: "Ge", grok: "Gk", deepseek: "DS", mistral: "Mi", groq: "Gq", openrouter: "OR", together: "Tg", qwen: "Qw", kimi: "Ki", perplexity: "Px", glm: "GL", custom: "＋"};      // the two letters in it
const aiLive = {};      // the live lists of models, read once per visit of the page: {id: {live, error}}
const aiMineKey = pid => "nk.aimodels" + (pid && pid !== "anthropic" ? "." + pid : "");
const aiMine = pid => { try { return JSON.parse(localStorage.getItem(aiMineKey(pid)) || "[]").filter(x => typeof x === "string"); } catch (e) { return []; } };
const aiRemember = (pid, m) => { m = String(m || "").trim(); if (!m) return; try { localStorage.setItem(aiMineKey(pid), JSON.stringify([m, ...aiMine(pid).filter(x => x !== m)].slice(0, 12))); } catch (e) { /* private window */ } };
window.nkCombo = async btn => {
  const box = btn.closest(".nk-combo"), inp = box && box.querySelector("input"); if (!inp) return;
  const pid = box.dataset.pid || "anthropic", cat = ((state.setCache && state.setCache.data && state.setCache.data[10]) || {}).catalog || [], c = cat.find(x => x.id === pid), known = c ? c.models : AI_MODELS;
  if (c && c.has_key && !aiLive[pid]) { btn.classList.add("busy"); try { aiLive[pid] = await api(`/api/settings/ai/providers/${encodeURIComponent(pid)}/models`); } catch (e) { aiLive[pid] = {live: [], error: e.message}; } btn.classList.remove("busy"); }
  const cur = inp.value.trim(), have = new Set(known.map(m => m[0])), live = ((aiLive[pid] || {}).live || []).filter(v => !have.has(v)), mine = aiMine(pid).filter(x => !have.has(x) && !live.includes(x));
  const sel = document.createElement("select"), opt = (v, note, g) => { const o = document.createElement("option"); o.value = v; o.textContent = note ? `${v} — ${note}` : v; if (v === cur) o.selected = true; g.appendChild(o); };
  const grp = (label, items) => { if (!items.length) return; const g = document.createElement("optgroup"); g.label = label; items.forEach(([v, n]) => opt(v, n, g)); sel.appendChild(g); };
  grp("Suggested", known); grp("On your account", live.map(v => [v, ""])); grp("Yours", [...new Set([...(cur && !have.has(cur) && !live.includes(cur) ? [cur] : []), ...mine])].map(v => [v, ""]));
  if (!sel.children.length) { toast((aiLive[pid] || {}).error || "No list yet: type the model name", ""); return; }
  sel.dataset.ddw = "360"; sel.__combo = inp; nkDdOpen(sel, box);
};
document.addEventListener("mousedown", e => { const s = e.target.closest && e.target.closest("select[data-dd]"); if (s && !s.disabled && e.button === 0) { e.preventDefault(); s.focus(); nkDdOpen(s); } }, true);
document.addEventListener("keydown", e => { const s = e.target; if (!nkDdEl && s && s.matches && s.matches("select[data-dd]") && ["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); nkDdOpen(s); } }, true);
/* glass confirm / prompt dialogs (replace the browser's own boxes). They sit above every window. */
const ASK_VERB = /^(Restore|Forget|Remove|Delete|Reboot|Forget|Update|Install|Restart|Import|Repair|Rebuild|Point|Dump|Clear|Put|Copy|Speed test|Add)\b/i;
function glassAsk(text, {input = false, value = "", danger, password = false, check = null, generate = true} = {}) {      // check = {label, note}: a tick under the text; the answer is then {checked} (or false on Cancel)
  return new Promise(resolve => {
    const old = $("#ask"); if (old) old.remove();
    const el = document.createElement("div"); el.id = "ask";
    const verb = (String(text).match(ASK_VERB) || [""])[0], bad = danger != null ? danger : /^(Remove|Delete|Reboot|Forget|Rebuild|Clear|Import)\b/i.test(text) || /cannot be undone|type [A-Z]+ to confirm/i.test(text);
    const label = input ? "OK" : verb ? verb[0].toUpperCase() + verb.slice(1).toLowerCase() : "OK";
    el.innerHTML = `<div class="overlay ask-ov"><div class="modal ask-box"><div class="ask-ic ${bad ? "bad" : ""}">${input ? "✎" : bad ? "⚠" : "?"}</div><div class="ask-text">${esc(text)}</div>${check ? `<label class="ask-check"><input type="checkbox" class="ask-cb"><span>${esc(check.label)}${check.note ? `<small>${esc(check.note)}</small>` : ""}</span></label>` : ""}${input ? `<div class="ask-field ${password ? "pw" : ""}">${password ? `<i class="ask-lk">${SI.lock(false)}</i>` : ""}<input class="ask-in" type="${password ? "password" : "text"}" value="${esc(value)}" autocomplete="${password ? "new-password" : "off"}" data-lpignore="true" data-1p-ignore spellcheck="false">${password ? `<button type="button" class="ask-eye" title="Show the password" aria-label="Show or hide the password"><svg viewBox="0 0 24 24"><path class="eye-o" d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle class="eye-o" cx="12" cy="12" r="3"/><path class="eye-x" d="M3 3l18 18"/></svg></button>${generate === false ? "" : '<button type="button" class="ask-gen" title="Make a strong password and show it">Generate</button>'}` : ""}</div>${password && generate !== false ? `<small class="ask-hint">At least 8 characters. Empty = the panel makes a strong one and shows it once.</small>` : ""}` : ""}<div class="ask-act"><button class="ask-no">Cancel</button><button class="ask-yes ${bad ? "danger-fill" : "pri"}">${esc(label)}</button></div></div></div>`;
    document.body.appendChild(el);
    const inp = $(".ask-in", el), done = v => { document.removeEventListener("keydown", key, true); el.remove(); resolve(v); };
    const cb = $(".ask-cb", el), yes = () => done(input ? inp.value : cb ? {checked: cb.checked} : true), no = () => done(input ? null : false);
    function key(e) { if (e.key === "Escape") { e.stopPropagation(); no(); } else if (e.key === "Enter" && (input || e.target.tagName !== "BUTTON")) { e.preventDefault(); e.stopPropagation(); yes(); } }
    document.addEventListener("keydown", key, true);
    $(".ask-yes", el).onclick = yes; $(".ask-no", el).onclick = no;
    const eye = $(".ask-eye", el), gen = $(".ask-gen", el), fld = $(".ask-field", el);
    if (eye) eye.onclick = () => { const show = inp.type === "password"; inp.type = show ? "text" : "password"; fld.classList.toggle("shown", show); eye.title = show ? "Hide the password" : "Show the password"; inp.focus(); };
    if (gen) gen.onclick = () => { const a = new Uint32Array(20), cs = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"; crypto.getRandomValues(a); inp.value = [...a].map(x => cs[x % cs.length]).join(""); inp.type = "text"; fld.classList.add("shown"); if (eye) eye.title = "Hide the password"; inp.focus(); inp.select(); };
    $(".ask-ov", el).onmousedown = e => { if (e.target.classList.contains("ask-ov")) no(); };
    setTimeout(() => { if (inp) { inp.focus(); inp.select(); } else $(".ask-yes", el).focus(); }, 30);
  });
}
/* a question with several answers: resolves to the chosen value, or null on Cancel / Esc / a click outside. `o.html`: the body as ready HTML (every dynamic part already esc()-ed by the caller) in place of the plain text -
   used where the text has parts of its own (the plan of "Move the tunnels": each part is translated by itself) */
function glassChoice(text, choices, o) {
  return new Promise(resolve => {
    const old = $("#ask"); if (old) old.remove();
    const el = document.createElement("div"); el.id = "ask";
    el.innerHTML = `<div class="overlay ask-ov"><div class="modal ask-box"><div class="ask-ic">?</div>${o && o.html ? `<div class="ask-text mv-plan">${o.html}</div>` : `<div class="ask-text">${esc(text)}</div>`}<div class="ask-act ask-choices">${choices.map((c, i) => `<button class="${c.cls || ""}" data-i="${i}">${esc(c.label)}${c.note ? `<small>${esc(c.note)}</small>` : ""}</button>`).join("")}<button class="ask-no">Cancel</button></div></div></div>`;
    document.body.appendChild(el);
    const done = v => { document.removeEventListener("keydown", key, true); el.remove(); resolve(v); };
    function key(e) { if (e.key === "Escape") { e.stopPropagation(); done(null); } }
    document.addEventListener("keydown", key, true);
    el.querySelectorAll("button[data-i]").forEach(b => { b.onclick = () => done(choices[+b.dataset.i].v); });
    $(".ask-no", el).onclick = () => done(null);
    $(".ask-ov", el).onmousedown = e => { if (e.target.classList.contains("ask-ov")) done(null); };
  });
}
window.gconfirm = (text, o) => glassAsk(text, o);
window.gprompt = (text, value = "", o = {}) => glassAsk(text, Object.assign({input: true, value}, o));
/* a window of a server or a tunnel is open: the page behind it is NOT drawn again while an operation runs (nothing behind the window moves); it is brought up to date once, when the window closes */
const pageTarget = () => { if (state.open != null && !state.build && document.querySelector("#modal .modal")) { state.pageStale = true; return document.createElement("div"); } return $("#page"); };
window.closeModal = () => {
  state.keepWin = null; state.open = null; state.edit = null; state.con = null; state.flt = null; state.wtab = null; state.build = document.querySelector("#pg-kind-page #pg-kind-form") ? "kinds" : null; cf.open = null;      // (the Add form of Configs stays held while it is on the page: closing a window - a config's Modify, a protocol of your own - must not let the page draw itself again over what was typed)
  cf.edit = null; cf.subEdit = null; cf.add = null; if (!cf.edit) cf.sub = null;
  hideModal();            // only the window goes: the page behind is left exactly as it is (what a window changes, it redraws itself)
  if (state.pageStale) { state.pageStale = false; setTimeout(() => { if (state.open == null && !state.build) refresh(); }, 280); }      // what was held back while the window was open
};
document.addEventListener("keydown", e => { if (e.key === "Escape" && $("#modal") && $("#modal").innerHTML && !$("#modal .modal.keep")) closeModal(); /* a form marked keep (Add account, the record form) stays until it is saved or its Close button is pressed */ });

/* ---- dashboard */
/* the small search of the dashboard: a few letters, and the servers / tunnels / configs whose name (or a part of it) starts with them are listed; a click opens it */
state.dq = ""; let dqSel = 0;
function dqSections() {   // every section of the panel by its name: the pages, their tabs, the tabs of Settings and of the shop bot
  const strip = s => String(s).replace(/<[^>]*>/g, "").replace(/^[^A-Za-z0-9]+/, "").trim(), out = [], pages = {};
  document.querySelectorAll("#nav a:not(.nav-off):not([data-sub])").forEach(a => { const n = strip(a.querySelector("span").textContent); pages[a.dataset.page] = n; out.push({page: a.dataset.page, name: n, sub: "Section"}); });      // (the pages; the tabs that hang under them in the menu come from SUB and SET_TABS below)
  Object.entries(SUB).forEach(([page, list]) => list.forEach(([k, label, , off]) => { if (off !== "off") out.push({page, subKey: k, name: strip(label), sub: pages[page] || page}); }));
  Object.entries(SET_TABS).forEach(([k, v]) => out.push({page: "settings", setTab: k, name: strip(v[0]), sub: "Settings"}));
  if (SHOPBOT_UI) SB_TABS.forEach(([k, l]) => out.push({page: "bot", sbTab: k, name: l, sub: "Shop bot"}));
  return out;
}
function dqItems(q) {   // the sections whose name (or a word of it) starts with what was typed - names of servers, tunnels or configs are not searched here
  q = q.trim().toLowerCase(); if (!q) return [];
  return dqSections().filter(s => nkHit(s.name + " " + s.sub, q)).sort((a, b) => (nkHit(b.name, q) - nkHit(a.name, q)) || (String(a.name).toLowerCase().startsWith(q) ? 0 : 1) - (String(b.name).toLowerCase().startsWith(q) ? 0 : 1)).slice(0, 8);      // a name that contains the words first
}const dqHtml = q => { const it = dqItems(q); if (!q.trim()) return ""; return it.length ? it.map((x, i) => `<a class="${i === dqSel ? "on" : ""}" onmousedown="dqPick(${i})"><span class="dq-go">›</span>${esc(x.name)}<small>${esc(x.sub)}</small></a>`).join("") : '<div class="none">No section matches that.</div>'; };
const dashSearch = () => `<div class="dsearch"><input id="dq" type="search" placeholder="Go to a section…" value="${esc(state.dq)}" oninput="dqType(this.value)" onkeydown="dqKey(event)" onblur="setTimeout(()=>{const l=$('#dq-list');if(l)l.innerHTML=''},150)" autocomplete="off" spellcheck="false"><div class="dq-list" id="dq-list">${dqHtml(state.dq)}</div></div>`;
window.dqType = v => { state.dq = v; dqSel = 0; $("#dq-list").innerHTML = dqHtml(v); };
window.dqKey = e => {
  const n = dqItems(state.dq).length;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); if (n) { dqSel = (dqSel + (e.key === "ArrowDown" ? 1 : n - 1)) % n; $("#dq-list").innerHTML = dqHtml(state.dq); } }
  else if (e.key === "Enter") { e.preventDefault(); dqPick(dqSel); }
  else if (e.key === "Escape") { state.dq = ""; e.target.value = ""; $("#dq-list").innerHTML = ""; }
};
window.dqPick = i => {
  const x = dqItems(state.dq)[i]; if (!x) return;
  state.dq = "";
  go(x.page);                                         // a page opens on its first tab: the one that was asked for is set right after
  if (x.subKey) { state.sub[x.page] = x.subKey; localStorage.setItem("nk.sub", JSON.stringify(state.sub)); }
  if (x.setTab) { state.stab = x.setTab; state.sstab = null; localStorage.setItem("nk.stab", x.setTab); }
  if (x.sbTab) { sb.tab = x.sbTab; localStorage.setItem("nk.sb.tab", x.sbTab); }
  refresh();
};
/* the numbers at a glance, beside the search; and a row of free slots under the cards, kept empty for whatever is added to the dashboard later */
const dashKpis = (o, cfg, cUp, su) => {
  const nProb = (o.tunnels.down || 0) + (o.tunnels.degraded || 0) + (o.servers.offline || 0) + (CONFIGS_UI ? cfg.configs.length - cUp : 0);
  const k = (label, val, cls, to) => `<a class="kpi" ${to ? `onclick="go('${to}')"` : ""}><i class="dash-dot ${cls}"></i><span><b>${val}</b><small>${label}</small></span></a>`;
  return `<div class="dash-kpis">${k("Tunnels up", `${o.tunnels.up}/${o.tunnels.total}`, o.tunnels.down ? "red" : o.tunnels.degraded ? "yellow" : "green", "tunnels")}${k("Servers online", `${o.servers.online}/${o.servers.total}`, o.servers.offline ? "red" : "green", "nodes")}${CONFIGS_UI ? k("Configs up", `${cUp}/${cfg.configs.length}`, cfg.configs.length && cUp === cfg.configs.length ? "green" : cUp ? "yellow" : "red", "configs") : ""}${k("Problems", nProb, nProb ? "red" : "green", "")}${su && su.dismissed && su.done < su.total ? `<a class="kpi" onclick="setupShow()" title="Show the setup checklist again"><i class="dash-dot yellow"></i><span><b>${su.done}/${su.total}</b><small>Setup</small></span></a>` : ""}</div>`;
};
/* the first-run checklist (the owner, 2026-10-16: "a new user who installs the panel is asked for the API keys at the start"): a card above the three boxes while the panel is not set up; GET /api/setup says what is done.
   Open for a new panel (fewer than three done), a thin strip after that, hidden when the owner hides it (a small Setup chip then brings it back). The API keys come first and are typed right here. */
const SU_IC = {panel: () => SI.cube, telegram: () => SI.send, ai: () => SI.bot, dns: () => SI.cloud, servers: () => SI.server, probe: () => SI.probe, backup: () => SI.save, security: () => SI.shield(false)};
const suIn = (name, ph, type = "text") => `<input name="${name}" type="${type}" placeholder="${ph}" autocomplete="off" spellcheck="false" data-lpignore="true">`;
function setupCard(su) {
  if (!su || su.dismissed || su.done >= su.total) return "";
  const open = state.setupOpen != null ? state.setupOpen : su.done < 3, pct = Math.round(su.done * 100 / su.total); state.setupShown = open;
  const head = `<div class="su-h"><i class="su-ic">${SI.check}</i><div class="su-ht"><b>Getting started</b><small>${su.done} of ${su.total} done · the API keys first</small></div><i class="su-bar"><u style="width:${pct}%"></u></i><span class="sp"></span><button type="button" class="sm" onclick="setupToggle()">${open ? "Collapse" : "Continue setup"}</button><button type="button" class="sm ghost" onclick="setupHide()" title="Hide this card - everything in it stays in Settings">Hide</button></div>`;
  if (!open) return `<div class="dash-setup shut">${head}</div>`;
  const act = {
    panel: `<button type="button" class="pri sm" onclick="pnAddWin()">＋ Add a panel</button>`,
    dns: `<button type="button" class="pri sm" onclick="dnsAccForm()">＋ Add an account</button>`,
    servers: `<button type="button" class="pri sm" onclick="SUB_ACT['nodes/add']()">＋ Add a server</button>`,
    probe: `<button type="button" class="sm" onclick="setupGo('ai')">Set it in Api &amp; Key</button>`,
    backup: `<button type="button" class="sm" onclick="setupGo('backup')">Set the passphrase</button>`,
    security: `<button type="button" class="sm" onclick="setupGo('account')">Turn it on</button>`,
    telegram: `<form class="su-f" onsubmit="return setupSave(event, 'telegram')">${suIn("token", "bot token 123456789:AA…", "password")}${suIn("chat_id", "chat id -100…")}<button class="pri sm" type="submit">Save</button></form>`,
    ai: `<form class="su-f" onsubmit="return setupSave(event, 'ai')"><select name="provider" data-dd title="Which AI the key is for">${AI_MAIN.map(k => `<option value="${k}">${esc(AIM_N[k])}</option>`).join("")}</select>${suIn("api_key", "API key", "password")}<button class="pri sm" type="submit">Save</button></form>`,
  };
  const card = it => `<div class="su-i ${it.done ? "done" : ""}"><span class="su-t">${it.done ? SI.check : (SU_IC[it.key] || (() => SI.cube))()}</span><div class="su-b"><b>${esc(it.title)}</b><small title="${esc(it.hint)}">${esc(it.done ? it.detail : it.hint)}</small>${it.done ? "" : `<div class="su-a">${act[it.key] || ""}</div>`}</div></div>`;
  return `<div class="dash-setup">${head}<div class="su-grid">${[...su.items.filter(i => !i.done), ...su.items.filter(i => i.done)].map(card).join("")}</div></div>`;      // (what is still to do first - the API keys at the top - and what is done after it)
}
window.setupToggle = () => { state.setupOpen = !state.setupShown; refresh(); };
window.setupHide = async () => { try { await api("/api/setup/dismiss", {method: "POST"}); toast("Hidden - the Setup chip at the top brings it back", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.setupShow = async () => { try { await api("/api/setup/reopen", {method: "POST"}); state.setupOpen = true; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.setupGo = tab => { go("settings"); state.stab = tab; state.sstab = null; localStorage.setItem("nk.stab", tab); refresh(); };      // (the tab of Settings that holds it)
window.setupSave = async (e, kind) => {
  e.preventDefault(); const f = e.target, b = f.querySelector("button[type=submit]"); if (b) b.disabled = true;
  try {
    if (kind === "telegram") {
      if (!f.token.value.trim() || !f.chat_id.value.trim()) { toast("Type the bot token and the chat id", "bad"); return false; }
      await api("/api/settings/telegram", {method: "PUT", body: JSON.stringify({enabled: true, token: f.token.value.trim(), chat_id: f.chat_id.value.trim()})}); toast("✓ Telegram saved - the alerts go to that chat", "ok");
    } else {
      if (!f.api_key.value.trim()) { toast("Paste the API key of the AI", "bad"); return false; }
      await api(`/api/settings/ai/providers/${encodeURIComponent(f.provider ? f.provider.value : "anthropic")}`, {method: "PUT", body: JSON.stringify({api_key: f.api_key.value.trim()})}); toast("✓ AI key saved - the panel tests it now", "ok");
    }
    refresh();
  } catch (err) { toast("✕ " + err.message, "bad"); } finally { if (b) b.disabled = false; }
  return false;
};
PAGES.dashboard = async () => {
  const [o, ev, cfg, srv, tl, su] = await Promise.all([api("/api/overview"), api("/api/events?limit=" + state.actLimit), CONFIGS_UI ? api("/api/configs").catch(() => ({configs: []})) : {configs: []}, api("/api/servers").catch(() => []), api("/api/tunnels").catch(() => []), api("/api/setup").catch(() => null)]);
  if (!CONFIGS_UI) ev.events = ev.events.filter(e => e.domain !== "configs");      // what the probe says about configs is not shown while the Configs list is off
  state.tunnelsCache = tl; state.cfgCache = cfg.configs; state.serversCache = srv;
  $("#version").textContent = "v" + o.version; $("#ro-badge").classList.toggle("hidden", !o.read_only);
 
  const cUp = cfg.configs.filter(c => c.status === "up").length, foreign = srv.filter(s => s.role === "foreign"), checked = foreign.filter(s => (s.extra || {}).iran_filter);
  const clean = checked.filter(s => s.extra.iran_filter.level === "clean"), bad = checked.filter(s => ["filtered", "blocked"].includes(s.extra.iran_filter.level));
  const hd = await api("/api/health/detail").catch(() => null);      // the panel itself: what to look at when it does not run well (database, jobs, SSH key, disk, backup, errors)
  const dot = cls => `<i class="dash-dot ${cls}"></i>`;
  const hrow = (name, sub, val, cls) => `<div class="dash-row">${dot(cls)}<div class="dash-name">${name}<small title="${sub}">${sub}</small></div><b class="${cls === "red" ? "err" : cls === "green" ? "gw-ok" : ""}">${val}</b></div>`;
  $("#page").innerHTML = `
    <div class="dash-top">${dashSearch()}${dashKpis(o, cfg, cUp, su)}</div>
    ${setupCard(su)}
    <div class="dash-cols ${state.actBig ? "exp" : ""}"><div class="card health"><h3>Infrastructure health</h3><div class="hl-list">
      ${hrow("Iran ↔ Foreign tunnels", o.tunnels.down ? o.tunnels.down + " down" : (o.tunnels.degraded ? o.tunnels.degraded + " unstable" : "all carrying users"), `${o.tunnels.up} / ${o.tunnels.total}`, o.tunnels.down ? "red" : o.tunnels.degraded ? "yellow" : "green")}
      ${hrow("SSH connected nodes", o.servers.offline ? o.servers.offline + " offline" : o.servers.iran + " Iran · " + o.servers.foreign + " foreign", `${o.servers.online} / ${o.servers.total}`, o.servers.offline ? "red" : "green")}
      ${CONFIGS_UI ? hrow("VLESS / VMess connectivity", cfg.configs.length - cUp ? (cfg.configs.length - cUp) + " down on the probe" : "all answer from inside Iran", `${cUp} / ${cfg.configs.length}`, cfg.configs.length && cUp === cfg.configs.length ? "green" : cUp ? "yellow" : cfg.configs.length ? "red" : "") : ""}
      ${hrow("Reachable from Iran", "clean filter check", `${clean.length} / ${foreign.length}`, bad.length ? "red" : clean.length === foreign.length && foreign.length ? "green" : "yellow")}
      ${hrow("Last filter checks", checked.length ? checked.sort((a, b) => b.extra.iran_filter.checked_at - a.extra.iran_filter.checked_at).slice(0, 3).map(s => esc(s.name) + (s.extra.iran_filter.level === "clean" ? " ✓" : " ✕")).join(" · ") : "none yet", `${bad.length} filtered`, bad.length ? "red" : checked.length ? "green" : "")}
      ${hrow("Background jobs", o.jobs.failing.length ? "failing: " + esc(o.jobs.failing.join(", ")) : o.jobs.stale.length ? "stale: " + esc(o.jobs.stale.join(", ")) : "all running", `${o.jobs.total}`, o.jobs.failing.length ? "red" : o.jobs.stale.length ? "yellow" : "green")}
       ${hd ? hrow("The panel itself", hd.problems.length ? esc(hd.problems.map(p => p.text).join(" · ")) : "database, jobs, SSH key, disk, backup and log are fine", hd.ok ? "ok" : hd.problems.length + " to look at", hd.ok ? "green" : "yellow") : ""}</div></div>
    ${problemsCard(o, tl, srv, cfg.configs, bad)}
    <div class="card act ${state.actBig ? "big" : ""}"><h3>Activity <span class="sp"></span><a class="mut" onclick="actBig()" title="${state.actBig ? "Back to the three boxes" : "The activity alone, the whole width"}">${state.actBig ? "⤡ Collapse" : "⤢ Expand"}</a></h3>${actHead(ev.events)}<div class="act-list">${eventsHtml(actFilter(ev.events))}</div>${actFoot(ev)}</div></div>
`;
};
/* ---- the Problems box: only what is wrong right now (read from the live state, so a solved problem leaves it by itself), each with its way to fix it */
/* the raw error of a check in plain words (the full text stays as the tooltip) */
const plainErr = e => { e = String(e || ""); if (!e) return ""; if (/Could not resolve|resolve host/i.test(e)) return "the domain does not resolve"; if (/SSL|TLS|handshake/i.test(e)) return "TLS handshake failed (blocked, or the TLS settings are wrong)"; if (/timed out|timeout/i.test(e)) return "no answer within 5 seconds"; if (/refused/i.test(e)) return "the port refuses the connection"; if (/reset by peer|closed/i.test(e)) return "the connection is cut while it is being made"; if (/flaps/i.test(e)) return "the link keeps dropping"; const m = e.match(/(\d+(?:\.\d+)?)\s*% (?:packet )?loss/i); if (m) return m[1] + " % of the packets are lost"; if (/not listening/i.test(e)) return "the port is closed on the server"; if (/no data/i.test(e)) return "connects, but no data comes back"; if (/Permission denied|key/i.test(e)) return "the panel can not log in over SSH"; if (/Iran servers lose packets/.test(e)) return e.slice(0, 140); return e.replace(/^curl: \(\d+\) /, "").slice(0, 90); };
state.actBig = false; state.actLimit = 60;
window.actBig = () => { state.actBig = !state.actBig; refresh(); };
window.actMore = () => { state.actLimit = Math.min(500, state.actLimit + 60); refresh(); };
const actFoot = ev => { const n = actFilter(ev.events).length, tot = ev.total || ev.events.length, more = ev.events.length < tot && state.actLimit < 500; return `<div class="act-foot"><span>Showing ${n} of ${fmt(tot)}${state.actProb || state.actCat !== "all" ? " (filtered)" : ""}</span>${more ? `<button class="sm" onclick="actMore()">Load more</button>` : ""}</div>`; };
state.pbAll = false; window.pbAll = () => { state.pbAll = !state.pbAll; refresh(); };
function problemsCard(o, tl, srv, configs, filtered) {
  const rows = [];
  const row = (lvl, title, sub, btn, open) => rows.push(`<div class="pb-row ${open ? "click" : ""}" ${open ? `onclick="${open}" title="Open it"` : ""}><i class="dash-dot ${lvl === "bad" ? "red" : "yellow"}"></i><div class="pb-t"><b>${title}</b><small title="${esc(String(sub || ""))}">${esc(plainErr(sub))}</small></div>${btn ? `<span onclick="event.stopPropagation()">${btn}</span>` : ""}</div>`);
  const fix = (fn, id) => `<button class="sm pri" onclick="${fn}(${id}${fn === "apDiagnose" ? ", {auto: true}" : ""})" title="Finds the cause and repairs it by itself where that is possible">✨ Fix with AI</button>`;
  tl.filter(t => t.enabled !== false && (t.state === "down" || t.state === "degraded")).sort((a, b) => (a.state === "down" ? 0 : 1) - (b.state === "down" ? 0 : 1))
    .forEach(t => row(t.state === "down" ? "bad" : "warn", `Tunnel ${esc(t.name)} <span class="mut">${esc(stName(t.state))}</span>`, t.last_error || `${t.iran_name} ⇄ ${t.foreign_name}`, fix("apDiagnose", t.id), `go('tunnels','list');state.open=${t.id};refresh()`));
  srv.filter(srvProblem).forEach(s => { const f = s.facts || {}; row(s.online === false ? "bad" : "warn", `Server ${esc(s.name)} <span class="mut">${s.online === false ? "offline" : "overloaded"}</span>`, s.online === false ? (s.last_error || "does not answer over SSH") : `CPU ${f.cpu ?? "—"} % · RAM ${f.ram ?? "—"} % · disk ${f.disk ?? "—"} %`, `<button class="sm" onclick="checkServer(${s.id})" title="Ask the server now">Check now</button>`, `docOpen(${s.id})`); });
  const down = CONFIGS_UI ? configs.filter(c => c.status === "down") : [];
  down.slice(0, state.pbAll ? 999 : 5).forEach(c => row("bad", `Config ${esc(c.name)} <span class="mut">down</span>`, c.last_error || "the probe gets no answer through it", fix("cfDiagnose", c.id), `go('configs');cf.open=${c.id};refresh()`));
  if (down.length > 5) rows.push(`<div class="pb-more"><a onclick="pbAll()">${state.pbAll ? "show fewer" : `and ${down.length - 5} more configs are down → show them`}</a> · <a onclick="go('configs')">open the list</a></div>`);
  srv.filter(s => s.role === "foreign" && s.enabled && ((s.extra || {}).reach_ir || {}).level === "bad").forEach(s => { const r = s.extra.reach_ir; row("bad", `Server ${esc(s.name)} <span class="mut">bad route from Iran</span>`, `${r.bad} of ${r.total} Iran servers lose packets toward it (worst ${r.worst} %) - the tunnels on it suffer; the fault is this server or its provider`, `<button class="sm" onclick="go('tunnels','list')" title="The tunnels of this server: compare them in the Tunnel manager">Test</button>`, `docOpen(${s.id})`); });
  filtered.forEach(s => row("warn", `Server ${esc(s.name)} <span class="mut">filtered from Iran</span>`, "its address cannot be reached from inside Iran", `<button class="sm" onclick="srvFilter(${s.id})" title="Check again from the Iran servers">Re-check</button>`, `docOpen(${s.id})`));
  (o.jobs.failing || []).forEach(n => row("bad", `Job ${esc(n)} <span class="mut">failing</span>`, "a background job keeps failing", `<button class="sm" onclick="runJob('${esc(n)}')">Run now</button>`));
  (o.jobs.stale || []).forEach(n => row("warn", `Job ${esc(n)} <span class="mut">stale</span>`, "a background job did not run on time", `<button class="sm" onclick="runJob('${esc(n)}')">Run now</button>`));
  const n = rows.length - (down.length > 5 ? 1 : 0) + Math.max(0, down.length - 5);
  const brokenT = tl.filter(t => t.enabled !== false && (t.state === "down" || t.state === "degraded")).length;
  return `<div class="card problems"><h3>Problems <span class="sp"></span>${brokenT ? `<button class="sm pri" onclick="go('tunnels','repairs');setTimeout(apFixAll, 600)" title="Repair every broken tunnel, one after the other">✨ Fix all</button>` : ""}<span class="pill ${n ? "err" : "ok"}">${n ? n + " now" : "all clear"}</span></h3><div class="pb-list">${rows.join("") || '<div class="empty" style="padding:18px 0">Nothing is wrong right now.</div>'}</div></div>`;
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
      <div class="ibox checked" style="margin-top:10px"><h5><i>🔎</i>What was checked</h5><div class="chk-list">${d.findings.map(f => `<div class="chk-row ${f.ok ? "ok" : "bad"}"><b>${f.ok ? "✓" : "✕"}</b><span>${esc(f.text)}</span></div>`).join("")}</div></div>
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
const SERVER_FORM = (s, role) => {   // one small window (1.55.15, the owner: "design this box again"): the server, the login, what to install after - in three light sections; the foot has the button
  const f3 = (l, inner, cls = "") => `<label class="f3 ${cls}"><span>${l}</span>${inner}</label>`;
  return `<form class="frm srv-form2 srv-f3" onsubmit="return saveServer(event, ${s ? s.id : "null"})">
    <section class="f3-sec"><h5 class="f3-h">Server</h5>
      <div class="f3-g2">${f3("Name", `<input name="name" required pattern="[A-Za-z0-9][A-Za-z0-9._\\-]*" value="${esc(s ? s.name : ((state.prefill || {}).name || ""))}" placeholder="ir7" title="Letters, digits . _ - (a name is never used twice)" oninput="pgSrvMirror(this.form)">`)}${!s && window.labTagInput ? f3("Profile", labTagInput()) : ""}</div>
      ${f3("Host / IP", `<input name="host" required pattern="[A-Za-z0-9][A-Za-z0-9.\\-]*" value="${esc(s ? s.host : ((state.prefill || {}).host || ""))}" placeholder="203.0.113.10" onchange="srvWhere(this.form)" onblur="srvWhere(this.form)">`)}
      <div class="f3-where">Where <input type="hidden" name="role" value="${esc(s ? s.role : "")}"><span class="srv-where" data-host="${esc(s ? s.host : "")}">${s ? `<span class="pill ${s.role === "iran" ? "ok" : "info"}">${s.role === "iran" ? "🇮🇷 Iran" : "🌍 foreign"}</span><a onclick="srvFlip(this.closest('form'))">change</a>` : '<span class="mut">found from the address once you type it</span>'}</span></div></section>
    <section class="f3-sec"><h5 class="f3-h">SSH login</h5>
      <div class="f3-g3">${f3("User", `<input name="ssh_user" value="${esc(s ? s.ssh_user : "root")}">`)}${f3("Port", `<input name="ssh_port" type="number" min="1" max="65535" value="${s ? s.ssh_port : 22}">`)}${f3("Password", `<input name="password" type="password" autocomplete="new-password" placeholder="${s ? "fill it to install the panel's key now" : "empty = the panel's key is already there"}" title="With a password the panel logs in once, installs its own key and forgets the password">`)}</div>
      <p class="f3-note">${s ? "A password here installs the panel's key now." : "With a password the panel logs in once, installs its key and forgets it. Without one, the panel's key must already be on the server (Settings shows it)."}</p>
      ${s ? "" : `<label class="chk f3-cores" title="After the login the panel reads the server, checks its GRE links and puts the tunnel cores on it by itself, from its own cache - the first time it takes a few minutes. Every step shows on the row of the server."><input name="x_cores" type="checkbox"><span>Put the tunnel cores on it now <small class="mut">otherwise all of them come with the first tunnel built on it</small></span></label>`}</section>
    ${s ? `<input type="hidden" name="provider" value="${esc(s.provider)}">` : ""}
    ${!s && window.pgSrvBox ? `<section class="f3-sec srv-after f3-pg" ${typeof labIsPgTag === "function" && labIsPgTag(typeof labTagCur === "function" ? labTagCur() : "") ? "" : "hidden"}><h5 class="f3-h">PasarGuard node <small>for this profile</small></h5>
      <div class="f3-opts srv-opts"><label class="chk" title="iptables DROP rules for tracker addresses, refreshed daily (Heclalava/blockpublictorrent-iptables)"><input name="x_bt" type="checkbox"> Block public torrents</label>${window.pgSrvTick ? pgSrvTick() : ""}</div>
      ${pgSrvBox()}</section>` : ""}    <div class="f3-foot"><input name="enabled" type="checkbox" style="display:none" ${!s || s.enabled ? "checked" : ""}><span class="sp"></span>${s ? `<button type="button" onclick="state.edit = null; refresh()" title="Back to the details of the server without saving">← Back</button>` : ""}<button class="pri" type="submit">${s ? "Save" : "Add server"}</button></div>
  </form>`;
};
/* a server the panel cannot log in to: not online, and its last error is about the login (password / key), or it never answered */
/* severity colours of the rows: red = fully down (or a server that cannot be reached / is critical), yellow = everything else that is wrong - packet loss of any size, degraded, slow, loaded */
const srvSev = s => { if (!s.enabled) return ""; if (srvBusy(s)) return "sev-blue"; const f = s.facts || {}; if (s.online === false) return "sev-red"; return noLogin(s) || /^SSH does not answer/.test(s.last_error || "") || (f.cpu || 0) >= 80 || (f.ram || 0) >= 85 || (f.disk || 0) >= 85 || s.online == null ? "sev-yellow" : ""; };      // red = the server is cut (off); a server that is on but has a problem (a full disk, a high CPU, no login, SSH failing) is yellow
const SHOW_LOSS = 20;   // packet loss under this is normal for the Iran-abroad paths: not shown, not coloured
const LOSS_SEVERE = 50;   // a lossy link at or above this is drawn as down in the Tunnel Doctor (below it: unstable) - the numbers of the loss live HERE, not inside the rows
/* the packet loss of a tunnel as a person must read it: every ping lost while the tunnel carries data means the provider drops ICMP - it is NOT lost data (the Doctor's page said "100 % of the data is lost on this link" for a tunnel that worked) */
const tunLoss = t => { const d = t.detail || {}, l = d.loss_pct; if (l != null && l >= 100 && t.state === "up") return {v: null, blocked: true}; return {v: l == null ? null : l, blocked: !!d.ping_blocked}; };
const LOSS_OVER = {link: "the GRE link of this tunnel", iface: "the link of this tunnel", servers: "the path between its two servers (ping)", udp: "the GRE link of this tunnel, counted with UDP packets (the provider drops ping on this route; measured by the bandwidth test)", tcp: "the control port of this tunnel, counted with ten TCP handshakes (the provider drops ping on this route)"};
const lossTip = t => tunLoss(t).blocked ? "Ping does not pass on this path (the provider drops it) - the data does: it was checked through the tunnel itself" : `Packets lost on ${LOSS_OVER[(t.detail || {}).ping_over] || LOSS_OVER.link}`;
const tunSev = t => { const loss = tunLoss(t).v || 0; if (t.state === "down") return "sev-red"; return t.state === "unknown" || t.state === "starting" || loss >= SHOW_LOSS || ["drop", "slow"].includes((t.bw || {}).state) ? "sev-yellow" : ""; };
/* the bandwidth of a tunnel (what the Doctor keeps: services/bandwidth.py): the download it can carry, in the plainest words - a number, a bar against its usual level, and "▼ usual" while it is dropped */
const LINK_TAG = {wg: "WireGuard", fou: "GRE/UDP", gue: "GRE/GUE", ipip: "IPIP", ipipfou: "IPIP/UDP", ipipgue: "IPIP/GUE", ipsec: "IPsec", ipsecnat: "IPsec/UDP"};      // what a Backhaul tunnel runs over besides GRE (record.link: services/builder.py `link_scripts`)
const linkTag = t => LINK_TAG[(t.record || {}).link] || "GRE";
const bwFmt = v => v == null ? "—" : bwNum(v) + " Mb/s";      // (everything about a tunnel's bandwidth is in Mb/s, never Gb/s - the owner, 2026-10-17)
const uxTip = t => { const u = t.ux || {}; return u.grade && u.grade !== "unknown" ? ` · Users: ${u.grade}${u.known ? " (" + u.known + " config" + (u.known === 1 ? "" : "s") + ")" : ""}` : ""; };      // what the users get through it (the probe's tests of the configs that ride the tunnel; services/userq.py)
const bwPending = t => !(t.bw || {}).error && (!!(t.record || {}).test_port || t.type === "backhaul" || t.type === "gre");      // a tunnel the Doctor can measure and has not yet: its turn comes (a few a minute), a dash would say it never will
const bwTip = t => {
  const b = t.bw || {};
  if (b.down == null) return (b.error ? "Could not be measured: " + b.error + " - " : "") + ((t.record || {}).test_port ? "The Doctor measures it with its quality test every half hour - nothing yet" : (t.type === "backhaul" || t.type === "gre") ? "The Doctor measures it with two light connections over the GRE link, about every 90 minutes - nothing yet" : "This tunnel has no link or test door to measure") + uxTip(t);
  const parts = [`Capacity - what this tunnel can carry (download): ${bwFmt(b.down)}`, b.ref ? `usually ${bwFmt(b.ref)}` : "the usual level is still being learnt", `the Doctor's last test, ${ago(b.at)} ago (a light test every ${(t.record || {}).test_port ? "half hour" : "90 minutes"}; it cannot be live)`];
  if (b.state === "drop") parts.push((b.down < BW_STALL ? "NO DATA moved in the test" : "DROPPED") + ((b.why || {}).text ? ": " + b.why.text : ""));
  const day = useDay(t); if (day) parts.push(`USERS, last 24 hours: peak ${bwFmt(day.peak)}, average ${bwFmt(day.avg)}` + (day.peak > b.down ? " - more than the little test shows, so the number in the row is that peak: the real capacity is at least what the users got" : ""));
  if (b.state === "slow") parts.push("VERY SLOW: the last two tests gave under " + BW_SLOW + " Mb/s - it did not drop, it never gave more" + ((b.why || {}).text ? ". " + b.why.text : ""));
  return parts.join(" · ") + uxTip(t);
};
const uxWhy = t => { const u = t.ux || {}, bad = u.bad || [], list = bad.slice(0, 3).map(b => `${b.config} ${b.text}`).join("; "), why = (u.why || {}).text; return why ? (list ? list + ". " : "") + why : bad.length ? `${bad.length} of the ${u.known} configs through it ${bad.length === 1 ? "has" : "have"} a problem: ${list}.` : "Its users get a poor connection."; };      // (the Doctor's sentence already counts the configs: it follows the list, it does not repeat it)
const BW_STALL = 0.5, BW_SLOW = 20;      // the Doctor's own lines (services/bandwidth.py STALL_MBPS and SLOW_MBPS; tests/test_bandwidth.py keeps them the same): a test under the first moved NOTHING (red), under the second twice in a row is VERY SLOW (amber) - 1.69.14: the page painted red under 1 Mb/s while the Doctor saw a stall only under 0.5
const bwWhy = t => { const b = t.bw || {}, d = b.drop || {}; return (b.state === "slow" ? `Very slow: ${bwFmt(b.down)} in the last two tests (it never gave more, so it did not drop).` : b.down < BW_STALL ? "No data moves through this tunnel under load (the test carried 0 Mb/s)." : `Bandwidth dropped to ${bwFmt(b.down)} (usually ${bwFmt(d.from || b.ref)}).`) + ((b.why || {}).text ? " " + b.why.text : " The Doctor is looking at why."); };
/* the Bandwidth column holds TWO numbers (the owner, 2026-10-17: "show the capacity, and the bandwidth the users use - real, reliable and live"): the CAPACITY (what the tunnel can carry: the Doctor's last test) and what the USERS use of it
   now (the counters of the tunnel's own link on its Iran server, read every few seconds while this page is open: services/livebw.py), with a bar that is the part of the capacity they use. A number that cannot be read is a dash, never a made-up zero. */
const bwNum = v => v >= 10 ? String(Math.round(v)) : v >= 1 ? v.toFixed(1) : v >= 0.05 ? v.toFixed(2) : "0";      // (always Mb/s - never Gb/s - and the unit is written ONCE, in the header of the column, not beside every number: the owner, 2026-10-17, "so that it does not look busy")
const liveAge = l => l.age + (Date.now() - (state.liveAt || Date.now())) / 1000;      // (the server says how old a number was when it answered; the page adds the time since: no clock of the browser is compared with the server's)
const useOf = t => {      // what the users use of this tunnel: the live reader's number, else the one of the last check (about a minute old, marked as not live), else nothing
  const l = (state.live || {})[t.id];
  if (l && liveAge(l) <= 120) return {down: l.down, up: l.up, age: liveAge(l), live: liveAge(l) <= 20, via: l.via || "link"};
  if (t.rx_mbps != null && t.last_check_at) return {down: t.rx_mbps, up: t.tx_mbps, age: Date.now() / 1000 - t.last_check_at, live: false, via: "link"};
  return null;
};
/* what the tunnel can carry, as the row says it: the Doctor's little test - or, when the users carried MORE than the test reads through it in the last 24 hours, that peak (the capacity is at least what the users got; the owner, 2026-10-19: "0.40 while the users use 6") */
const useDay = t => (state.use24 || {})[t.id] || null;
const capOf = t => { const d = (t.bw || {}).down; if (d == null) return null; const u = useDay(t); return u && u.peak > d ? u.peak : d; };
const bwUtil = t => { const u = useOf(t), c = capOf(t); return u && c > 0 ? Math.min(1, u.down / c) : null; };      // the part of the capacity the users use
const useTip = (t, u) => !u ? "What the users use of this tunnel cannot be read now: its Iran server does not answer, or the tunnel has no forwarded port to count the users' connections on" : `Users use it now: ↓ ${bwFmt(u.down)} (download) ↑ ${bwFmt(u.up)} (upload) - counted ${u.via === "users" ? "from the users' own connections to the ports of the tunnel (what the Iran server sends them is their download)" : u.via === "connections" ? "from the TCP connections of the tunnel (with the framing of the tunnel: a few percent more than the users' own data)" : "from the link of the tunnel"} on its Iran server over about 15 seconds; ${u.live ? "live, read " + Math.round(u.age) + " s ago" : "from the last check, " + Math.round(u.age) + " s ago (the live reading starts in a few seconds)"}${bwUtil(t) != null ? " · " + Math.round(bwUtil(t) * 100) + " % of its capacity" : ""}`;
const bwUse = t => { const u = useOf(t); if (!u) return `<span class="bwuse none" title="${esc(useTip(t, null))}">—</span>`; return `<span class="bwuse ${u.live ? "live" : "old"}" title="${esc(useTip(t, u))}"><i>↓</i><b>${bwNum(u.down)}</b></span>`; };      // one number - the download, what the users actually get (the owner, 2026-10-17: "instead of showing upload and download, show one number"); both are still in the tunnel window ("In use now") and this tooltip
const bwBar = t => { const f = bwUtil(t); return `<i class="bwbar" title="${f == null ? "" : esc("The part of its capacity the users use now: " + Math.round(f * 100) + " %")}"><u style="width:${f == null ? 0 : f > 0 ? Math.max(2, Math.round(f * 100)) : 0}%"></u></i>`; };
const bwCap = t => {      // the capacity: the download speed the Doctor measured, ONE number in the colour of its state - green/healthy, amber/low, red/dropped or stalled (the owner, 2026-10-17: "these red numbers, what are they, not clear" - two numbers side by side, one a drop arrow, read as noise). The usual level, when it dropped and why are the tooltip and the tunnel window (Link -> Capacity), not crowded into the row.
  const b = t.bw || {}, stall = b.down != null && capOf(t) < BW_STALL;
  if (b.down == null) return bwPending(t) ? `<span class="bwcap none pend" title="${esc(bwTip(t))}"><i class="spin"></i>measuring…</span>` : `<span class="bwcap none" title="${esc(bwTip(t))}">—</span>`;
  return `<span class="bwcap ${stall || b.state === "drop" ? "drop" : b.state === "slow" ? "slow" : "ok"}" title="${esc(bwTip(t))}"><b>${bwNum(capOf(t))}</b></span>`;      // (a "low" capacity - 60-80 % of the usual level - is NOT coloured: only a real drop or a stall is; the owner, 2026-10-17: "do not show these yellow numbers unless there is really a problem or a real drop")
};
/* the numbers of what the users use change by themselves: while the Tunnels page is on screen they are asked every 4 s and the cells change in place (nothing is drawn again) */
let tunLiveT = null;
const tunLivePaint = () => { const byId = new Map((state.tunnelsCache || []).map(t => [t.id, t])); document.querySelectorAll(".tun-tbl td.bwc[data-tid]").forEach(td => { const t = byId.get(+td.dataset.tid), r = td.querySelector(".bwx .bwr"), bar = td.querySelector(".bwx .bwbar"); if (!t || !r) return; r.innerHTML = bwCap(t) + '<i class="bwsep"></i>' + bwUse(t); if (bar) bar.outerHTML = bwBar(t); }); };
const tunLiveStop = () => { clearInterval(tunLiveT); tunLiveT = null; };
const tunLiveTick = async () => {
  if (state.page !== "tunnels" || document.hidden || !document.querySelector(".tun-tbl")) { tunLiveStop(); return; }
  let r; try { r = await api("/api/tunnels/live"); } catch (e) { return; }      // (a read that failed leaves what is shown: only the next number is late)
  state.live = r.live || {}; state.use24 = r.use24 || state.use24 || {}; state.liveAt = Date.now(); tunLivePaint();
};
const tunLiveStart = () => { if (!tunLiveT) { tunLiveT = setInterval(tunLiveTick, 4000); tunLiveTick(); } };
document.addEventListener("visibilitychange", () => { if (!document.hidden && state.page === "tunnels") tunLiveStart(); });
/* the panel is connecting to a server or installing something on it: the list says so instead of a dash (and looks again every 3 s) */
const srvBusy = s => { const b = (s.extra || {}).busy; return b && b.what && Date.now() / 1000 - b.at < 900 ? b : null; };
const noLogin = s => s.enabled && s.online !== true && /permission denied|publickey|login failed|authentication|wrong user|password/i.test(s.last_error || "");
/* a server added in the last hour is marked "new" (Nodes, and the server lists of Create tunnel / Tunnel test) */
const isNew = s => s && s.created_at && Date.now() / 1000 - s.created_at < 900;      // "new" stays 15 minutes (the owner, 2026-10-15)
const srvProblem = s => { const f = s.facts || {}; return s.enabled && (s.online === false || (f.cpu || 0) > 85 || (f.ram || 0) > 90 || (f.disk || 0) > 85); };
function srvModal(s) {   // while the edit form is open the header keeps only the name and the switch: the form has Save and Back
  const editing = state.edit === s.id, on = s.enabled !== false;
  const sw = `<label class="srv-sw ${on ? "on" : ""}" title="${on ? "Enabled: the panel checks this server. Switch off to disable it: it is not checked and gives no alerts (nothing changes on the server)." : "Disabled: the panel does not check this server. Switch on to enable it."}"><input type="checkbox" ${on ? "checked" : ""} onchange="srvToggle(${s.id})" aria-label="Enable or disable ${esc(s.name)}"><i></i><b>${on ? "Enabled" : "Disabled"}</b></label>`;
  const who = `<span class="mh-who"><span class="mh-flag">${flagImg(s.role === "iran" ? "IR" : s.country) || `<small class="flgt">${esc(String(s.country || "").toUpperCase())}</small>`}<span class="st ${s.online ? "online" : s.online === false ? "offline" : ""}"></span></span><span class="mh-txt"><b>${esc(s.name)}</b><span class="mh-meta"><span class="mono ip-copy mh-ip" onclick="copyFrom(this, '${esc(s.host)}')" title="Click to copy the address">${esc(s.host)}</span>${window.pgSrvChips ? pgSrvChips(s) : ""}</span></span></span>`;      // (no role tag and no host name here any more: the flag says it, and a long line went under the switch)
  const acts = `${sw}${editing ? "" : `<i class="mh-sep"></i><button class="sm" ${s.online === false && s.enabled !== false ? 'disabled title="The server is off or does not answer"' : `onclick="srvConsole(${s.id})" title="Run a command on the server, with presets"`}><i class="bi">${SI.term}</i><span class="bt">Console</span></button><button class="sm" onclick="editServer(${s.id})" title="Name, address, SSH user, port" aria-label="Edit"><i class="bi">${SI.edit}</i><span class="bt">Edit</span></button><button class="sm danger" onclick="deleteServer(${s.id}, '${esc(s.name)}')" title="Remove it from the panel (nothing changes on the server)" aria-label="Delete"><i class="bi">${SI.trash}</i><span class="bt">Delete</span></button>`}<i class="mh-sep"></i>`;
  showModal(who, `<div id="srv-detail-${s.id}">${editing ? SERVER_FORM(s) : serverDetailHtml(s)}</div>`, "wide srvwin", window.labOn && labOn() && window.labActs ? labActs(s, sw, editing) : acts);      // (the Lab swaps Delete for "Out of profile")      // details and edit form are ONE window of ONE fixed size (see .srvwin): the charts area is reserved, so nothing grows when the charts arrive, and Edit / Back / Console never shrink it
  const mdl = $("#modal .modal"); if (mdl) { mdl.classList.toggle("editing", editing); const w = state.winH && state.winH.id === s.id ? state.winH : (state.winH = {id: s.id, h: 0}); w.h = Math.max(w.h, mdl.offsetHeight); mdl.style.minHeight = w.h + "px"; }      // Edit / Back / Console never make the window shorter than it has been
}
const TUN_SECS = [["overview", "Overview"], ["monitor", "Monitor"]];
const tunOffServers = t => ["iran", "foreign"].map(r => (state.serversCache || []).find(x => x.name === t[r + "_name"])).filter(x => x && x.enabled !== false && x.online === false).map(x => x.name).join(", ");      // the servers of this tunnel that are off
/* a tunnel window built EXACTLY like the window of a server (the owner, 2026-10-16: "rebuild it and make it like the server box"): the avatar with the state, the name, its two servers and its type; the alerts switch and ONE "⋯" menu in the header (Fix, Dry run, Health check, Restart, History, Mute, Rename, Ports, Rebuild, Remove); the tabs Overview | Monitor; the same banner, boxes and strip */
function tunModal(t) {
  const muted = (state.mutedSet || new Set()).has(t.name), nm = esc(t.name);
  const who = `<span class="mh-who"><span class="mh-flag mh-tun"><i class="mh-tic">${SI.swap}</i><span class="st ${t.state}"></span></span><span class="mh-txt"><b>${nm}</b><span class="mh-meta"><span class="mono mh-ip">${esc(t.iran_name)} ⇄ ${esc(t.foreign_name)}</span><span class="mchip">${esc(t.type)}${t.transport ? " · " + esc(t.transport) : ""}</span></span></span></span>`;
  const sw = `<label class="srv-sw ${muted ? "" : "on"}" title="${muted ? "Muted: no Telegram alerts about this tunnel. Switch on to get them again." : "Alerts on: Telegram tells you when this tunnel goes down. Switch off to mute it."}"><input type="checkbox" ${muted ? "" : "checked"} data-n="${nm}" onchange="muteRef(this.dataset.n, ${!muted})" aria-label="Alerts about ${nm}"><i></i><b>${muted ? "Muted" : "Alerts"}</b></label>`;
  showModal(who, `<div id="tun-detail-${t.id}">${tunWinHtml(t)}</div>`, "wide srvwin tunwin", `${sw}<i class="mh-sep"></i>${typeof labKebab === "function" ? labKebab("tun", t.id, "Actions of " + t.name) : ""}<i class="mh-sep"></i>`);
}
function tunWinHtml(t) {
  const sec = state.tsec === "monitor" ? "monitor" : "overview", c = (state.chartCache || {})["t" + t.id + ":" + state.mrange];
  const bar = `<div class="tabs lab-sec">${TUN_SECS.map(([k, l]) => `<button class="${k === sec ? "on" : ""}" onclick="tunWtab('${k}')">${l}</button>`).join("")}</div>`;
  const body = sec === "monitor" ? `<div class="wsec"><div class="${c ? "mon" : "charts"}" id="tun-charts-${t.id}">${c || '<div class="mut">loading…</div>'}</div></div>` : tunBanner(t) + tunStandby(t) + tunGrid(t) + `<div class="sgrid">${tunUsersBox(t)}${tunUpBox(t)}${tunResBox(t)}</div>`;
  return bar + `<div class="lab-body" data-sec="${sec}">${body}</div><div id="fix-${t.id}" class="fix hidden"></div>`;
}
/* the warm standby (the owner, 2026-10-17: "bring the delay down to the minimum"; services/standby.py): a second tunnel of another type between the same two servers, built BEFORE a cut and kept idle on shifted ports; when this tunnel is cut
   the users' ports are handed to it in seconds, and they go back when this tunnel has held for a minute. A switch and a state in the window - the standby is not a row of the list. t.standby = {on, state, kind, since, why, ports, up} */
const SB_WORD = {off: "Off", building: "Building…", ready: "Ready", in_use: "In use", mending: "Being mended", waiting: "Waiting", failed: "Not built", removing: "Removing…"};
function tunStandby(t) {
  const s = t.standby || {}, st = s.state || "off", id = t.id, tone = st === "in_use" ? "warn" : st === "ready" ? "ok" : st === "failed" ? "bad" : st === "off" ? "" : "warn", clock = ts => ts ? new Date(ts * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : "";
  if (st === "ready" || st === "off") return "";      // (1.69.23: the standby is on by itself for every tunnel, so "off" is not drawn either - only the owner's explicit switch-off leaves it off, and that has no bar to turn it back on: the API does) (1.69.16, the owner: a standby that is ready and idle shows nothing - the handover is automatic when the tunnel is cut; the bar comes back while it is built, mended or in use)
  const b = (label, click, pri, tip) => `<button class="sm ${pri ? "pri" : ""}" onclick="${click}" ${tip ? `title="${esc(tip)}"` : ""}>${label}</button>`;
  const meta = st === "off" ? "A second tunnel of another type waits beside this one. If this one is cut, its users' ports are handed to it in seconds." :
    st === "ready" ? `${esc(s.kind)} · ports ${esc(s.ports)} · idle, looked at every 15 seconds` :
    st === "in_use" ? `The users' ports are on the standby (${esc(s.kind)}) since ${clock(s.since)} - they go back to this tunnel when it has held for a minute` :
    esc(s.why || SB_WORD[st] || "");
  const acts = st === "off" ? b("Turn on", `tunStandbySet(${id}, true)`, false, "Build the standby now (about a minute); nothing changes for the users")
    : st === "in_use" ? b("Back now", `tunStandbyBack(${id})`, true, "Take the users' ports off the standby now") + b("Turn off", `tunStandbySet(${id}, false)`, false, "Give the ports back and remove the standby")
    : st === "ready" ? b("Switch now", `tunStandbyNow(${id})`, false, "Hand the users' ports to the standby now (a drill; they stay there until Back now)") + b("Turn off", `tunStandbySet(${id}, false)`, false, "Remove the standby")
    : st === "removing" ? "" : b("Turn off", `tunStandbySet(${id}, false)`, false, "Remove the standby");
  return `<div class="lab-ban ${tone}" data-b="standby" title="The warm standby: another tunnel type between the same two servers, on shifted ports, built before a cut"><i class="lb-i">${st === "in_use" ? "⇆" : "⧉"}</i><div class="lb-t"><b>Standby · ${SB_WORD[st] || st}</b><span class="lb-m" title="${esc(s.why || "")}">${meta}</span></div><div class="lb-a">${acts}</div></div>`;
}
function tunBanner(t) {      // one box that says how the tunnel is, in plain words, with the buttons that fix it
  const offs = tunOffServers(t), d = t.detail || {}, up = t.state === "up";
  const btn = (label, click, pri) => offs ? `<button class="sm" disabled title="${esc("A server of this tunnel is off (" + offs + "): " + label + " works when it is back")}">${label}</button>` : `<button class="sm ${pri ? "pri" : ""}" onclick="${click}">${label}</button>`;
  if (up) return `<div class="lab-ban ok"><i class="lb-i">✓</i><div class="lb-t"><b>Healthy</b><span class="lb-m">Both ends answer and data passes${d.loss_pct != null ? ` · ${d.loss_pct} % lost` : ""}${t.rtt_ms != null ? ` · ${Math.round(t.rtt_ms)} ms` : ""}${t.state_since ? " · up " + ago(t.state_since) : ""}. Nothing needs attention.</span></div><div class="lb-a">${btn("Health check", `healthTunnel(${t.id})`)}</div></div>`;
  const bad = t.state === "down", head = bad ? "Needs attention" : t.state === "degraded" ? "Unstable" : t.state === "starting" ? "Starting" : "Not checked yet";
  const msg = offs ? `${offs} is off or does not answer: nothing can be repaired until it is back.` : (t.last_error ? outWhy(t.last_error) : "The tunnel has not answered its last check.");
  return `<div class="lab-ban ${bad ? "bad" : "warn"}"><i class="lb-i">${bad ? "✕" : "!"}</i><div class="lb-t"><b>${head}</b><span class="lb-m ${bad ? "err" : ""}" title="${esc(t.last_error || "")}">${esc(msg)}</span></div><div class="lb-a">${btn("Fix", `fixTunnel(${t.id}, false)`, true)}${btn("Dry run", `fixTunnel(${t.id}, true)`)}${btn("Health check", `healthTunnel(${t.id})`)}</div></div>`;
}
/* two boxes under the grid (the owner, 2026-10-17: the quality of the users' internet and the stability of the tunnel "inside their own box, not in the rows of the list"):
   Users' internet = what the Doctor keeps of the configs that ride the tunnel (t.ux: services/userq.py); Stability = GET /api/tunnels/{id}/stability (the checks of the last 7 days, the analyst's rules) */
const UX_WORD = {good: "Good", fair: "Fair", poor: "Poor", bad: "Bad"};
const tunUsersBox = t => {
  const u = t.ux || {}, known = u.known || 0, bad = u.bad || [], has = !!UX_WORD[u.grade] && known > 0, tone = ["poor", "bad"].includes(u.grade) ? "err" : u.grade === "fair" ? "warn" : "gw-ok";
  const kv = (l, v) => `<div class="kv2"><span>${l}</span><div>${v}</div></div>`, none = '<span class="mut">—</span>', n = re => bad.filter(b => re.test(b.issue)).length;
  const parts = [[n(/^slow/), "slow"], [n(/^unstable$/), "unstable"], [n(/^down$/), "down"]].filter(p => p[0]).map(p => p[0] + " " + p[1]);
  const list = bad.slice(0, 2).map(b => `<div class="us-line" title="${esc(b.config + " " + b.text)}"><b>${esc(b.config)}</b><span>${esc(b.text)}</span></div>`).join("") + (bad.length > 2 ? `<div class="us-line mut">+${bad.length - 2} more</div>` : "");
  const why = (u.why || {}).text && ["poor", "bad"].includes(u.grade) ? `<div class="bw-why" title="${esc(u.why.text)}">${esc(u.why.text)}</div>` : "";
  return `<div class="sbox" data-b="users" title="What the users get through this tunnel: the Doctor reads the tests of the configs that ride it (status, ping, speed), each against its own usual level"><h5><i>${SI.users}</i>Users' internet<small class="mut">${u.at ? ago(u.at) + " ago" : ""}</small></h5>
    ${kv("Quality", has ? `<b class="${tone}">${UX_WORD[u.grade]}</b> <small class="mut">${known - bad.length} of ${known} config${known === 1 ? "" : "s"} fine</small>` : '<span class="mut">not known yet</span>')}${kv("Problems", has ? (parts.join(" · ") || "none") : none)}
    ${has ? (list || '<div class="us-note mut">Every config through this tunnel answers as it usually does.</div>') + why : '<div class="us-note mut">No config of the probe rides this tunnel yet, or there is too little data. Tie the configs to their tunnels in the Doctor page.</div>'}</div>`;
};
const tunUpBox = t => `<div class="sbox" data-b="stability" title="How steady the tunnel is: the checks of the panel (one a minute); a cut is two failed checks in a row"><h5><i>${SI.pulse}</i>Stability<small id="tun-ov-pct-${t.id}" class="mut"></small></h5>
  ${["Answered", "Cuts", "Last cut", "Up for"].map((l, i) => `<div class="kv2"><span>${l}</span><div id="tun-st-${i}-${t.id}"><span class="mut">…</span></div></div>`).join("")}<div class="lab-ups" id="tun-ov-${t.id}"></div></div>`;
function tunResBox(t) {      // the last results: the third box of the second row, built like the boxes above it (the owner, 2026-10-17: six boxes in two rows of three)
  const q = t.quality || {}, d = t.detail || {}, kv = (l, v, tip) => `<div class="kv2"><span>${l}</span><div ${tip ? `title="${esc(tip)}"` : ""}>${v}</div></div>`;
  return `<div class="sbox" data-b="results"><h5><i>${SI.doc}</i>Last results</h5>${kv("Last check", t.last_check_at ? `${ago(t.last_check_at)} ago` : '<span class="mut">never</span>')}${kv("Quality", q.score != null ? `<b class="${q.score >= 70 ? "gw-ok" : q.score >= 40 ? "warn" : "err"}">${q.score} / 100</b>${q.at ? ` <small class="mut">${ago(q.at)} ago</small>` : ""}` : '<span class="mut">not measured yet</span>', q.score != null ? "" : "Health check measures it")}${kv("Last error", t.last_error ? `<span class="err">${esc(outWhy(t.last_error))}</span>` : '<span class="mut">none</span>', t.last_error || "")}${d.iran_error ? kv("Iran side", `<span class="err">${esc(d.iran_error)}</span>`, d.iran_error) : ""}${d.foreign_error ? kv("Foreign side", `<span class="err">${esc(d.foreign_error)}</span>`, d.foreign_error) : ""}</div>`;
}
const UP_WORD = {stable: ["Stable", "gw-ok"], watch: ["Watch", "warn"], chronic: ["Unstable", "err"]};
async function tunStrip(id) {      // the Stability box (GET /api/tunnels/{id}/stability): the share that answered, the cuts, the last cut, how long it has been up, and the strip of the last 24 hours (48 cells: blue = answered, yellow = now and then, red = no answer)
  const box = document.getElementById("tun-ov-" + id); if (!box) return;
  const put = (i, v) => { const e = document.getElementById(`tun-st-${i}-${id}`); if (e) e.innerHTML = v; };
  try {
    const d = await api(`/api/tunnels/${id}/stability`), N = d.strip.length, span = d.strip_to - d.strip_from, w = span / N, s0 = d.strip_from;
    const pc = a => a == null ? '<span class="mut">collecting…</span>' : `<b class="${a >= 99 ? "gw-ok" : a >= 95 ? "warn" : "err"}">${a}</b> %`, cell = i => { const c = d.strip[i];
      return `<i class="c${c < 0 ? "n" : c}" title="${esc(fmtT(s0 + i * w, span) + " - " + fmtT(s0 + (i + 1) * w, span) + ": " + (c < 0 ? "no check" : c === 2 ? "answered" : c === 1 ? "answered now and then" : "no answer"))}"></i>`; };
    put(0, `${pc(d.d1.avail)} <small class="mut">24 h</small> · ${pc(d.d7.avail)} <small class="mut">7 d</small>`);
    put(1, `<span title="${esc(d.d7.cuts ? "The longest cut in 7 days: " + durText(d.d7.longest_secs) + " · in all " + durText(d.d7.down_secs) + " without an answer" : "No cut in 7 days")}"><b>${d.d1.cuts}</b> <small class="mut">24 h</small> · <b>${d.d7.cuts}</b> <small class="mut">7 d</small></span>`);
    put(2, d.last ? (d.last.ongoing ? `<b class="err">now</b> <small class="mut">${durText(d.last.secs)} so far</small>` : `${ago(d.last.start)} ago <small class="mut">${durText(d.last.secs)}</small>`) : '<span class="mut">none in 7 days</span>');
    put(3, d.up_for != null ? durText(d.up_for) : '<span class="mut">not up</span>');
    box.innerHTML = `<div class="lab-ups-row">${Array.from({length: N}, (_, i) => cell(i)).join("")}</div><div class="lab-ups-x"><span>${esc(fmtT(s0, span))}</span><span>now</span></div>`;
    const pv = document.getElementById("tun-ov-pct-" + id), v = UP_WORD[d.verdict]; if (pv) pv.innerHTML = v ? `<span class="${v[1]}">${v[0]}</span>` : "collecting data";
  } catch (e) { box.innerHTML = `<span class="mut">${esc(e.message)}</span>`; }
}
function tunAfter(t) { if (state.tsec === "monitor") loadTunnelCharts(t.id); else tunStrip(t.id); }      // what a section loads by itself
window.tunWtab = k => { state.tsec = k === "monitor" ? "monitor" : "overview"; const t = (state.tunnelsCache || []).find(x => x.id === state.open), body = $("#modal .modal-body"); if (!t || !body) { refresh(); return; } body.innerHTML = `<div id="tun-detail-${t.id}">${tunWinHtml(t)}</div>`; tunAfter(t); };
/* small flag images (Windows has no flag emoji); the text code stays as the fallback when the image cannot load */
const flagImg = cc => cc && /^[A-Za-z]{2}$/.test(cc) ? `<img class="flg" src="/flags/${cc.toLowerCase()}.png" width="24" height="24" alt="${cc.toUpperCase()}" title="${cc.toUpperCase()}" onerror="this.outerHTML='<small class=flgt>${cc.toUpperCase()}</small>'">` : "";
const flag = cc => cc && /^[A-Z]{2}$/.test(cc) ? flagImg(cc) + " " + cc : "";
const irow = (label, val) => `<div class="irow"><span>${label}</span><div>${val}</div></div>`;
const ibox = (icon, title, rows) => `<div class="ibox"><h5><i>${icon}</i>${title}</h5>${rows.filter(Boolean).join("")}</div>`;
/* a server window, built from scratch: ONE bar with every button, tight boxes with the same number of rows (nothing hollow), a line of tunnels, a line of the last results, the charts.
   Console, Edit and Delete are in the window header. The console is the only other view, with a Back link. */
function serverDetailHtml(s) {
  if (window.labOn && labOn() && window.labWinHtml) return labWinHtml(s);      // the Lab: four sections (lab.js)
  if (state.wtab === "node" && window.pgNodeHost) return pgNodeHost(s);      // the Node view (pg_nodes.js): what the panel knows about the node of this server and everything that can be done to it
  if (state.wtab === "console") return `<div class="wback"><button class="sm" onclick="wtab('all')">← Back</button></div>` + serverPartHtml(s, "console");
  if (state.wtab === "install") return `<div class="wback"><button class="sm" onclick="wtab('all')">← Back</button></div>` + installHtml(s);
  return serverBar(s) + (window.pgSrvLine ? pgSrvLine(s) : "") + serverGridHtml(s) + `<div class="wsec"><h4>Last 24 hours</h4>${serverPartHtml(s, "charts")}</div>`;
}
function serverBar(s) {
  const f = s.facts || {}, ab = (s.extra || {}).abuse || {}, abuseOn = ab.installed || (f.abuse_rules || 0) > 0, nm = esc(s.name);
  const off = s.online === false && s.enabled !== false;      // a server that is off: what needs it is shown off
  const b = (icon, title, tip, click, cls = "", always = false) => `<button class="sm abtn ${cls}" ${off && !always ? `disabled title="${esc("The server is off or does not answer: " + title + " works when it is back")}"` : `onclick="${click}" title="${esc(tip)}"`}><i class="bi">${{"⇄": SI.swap, "🛰": SI.probe, "🛡": SI.shield(false), "🔐": SI.lock(false), "↺": SI.restore, "⏻": SI.power}[icon] || icon}</i>${title}</button>`;
  const hostKeyChanged = /host key/i.test(s.last_error || "");        // only when the panel refuses the server because it was reinstalled: then its old identity must be forgotten
  const groups = [
    [b("⇄", "GRE test", "Do its GRE links answer?", `greCheck(${s.id})`)],
    [...(s.role === "foreign" ? [b("🛰", "Filtering", "Is this server filtered in Iran? Pings from your Iran servers + check-host.net", `srvFilter(${s.id})`)] : []),
      ...(window.pgNodeHost && ((window.pgSrvOn && pgSrvOn(s).length) || (s.role === "foreign" && window.pgSrvPanels && pgSrvPanels().length)) ? [b(SI.cube, "Node", "The node of a Marzban or PasarGuard panel that runs here: its state, log, usage, certificate, restart, update ... (or make this server a node)", `wtab('node')`, "", true)] : []),
      b("📦", "Install", "Install a Marzban or PasarGuard node or the Abuse-Defender, or update the server", `wtab('install')`)],
    [b("🔐", "Change password", "Set a new SSH login password on the server (or a strong generated one)", `srvPassword(${s.id}, '${nm}')`),
     `<select class="sm abtn abtn-open" title="Open this server in a program on your computer" onchange="if(this.value){srvOpen(${s.id}, this.value);this.selectedIndex=0}"><option value="">🖥 Open in…</option><option value="ssh">Windows ssh</option><option value="putty">PuTTY</option><option value="mobaxterm">MobaXterm</option></select>`,
     ...(hostKeyChanged ? [b("↺", "Trust the new server", "The server was reinstalled and its SSH identity changed: forget the old one so the panel can connect again", `forgetKey(${s.id}, '${nm}')`, "", true)] : [])],
    [b("⏻", "Reboot", "Its tunnels are down until it is back", `srvReboot(${s.id}, '${nm}')`, "danger")],
  ].filter(g => g.length);
  return `<div class="sbar">${groups.map(g => `<span class="sg">${g.join("")}</span>`).join('<i class="sep"></i>')}</div>`;
}
/* everything about a server in THREE boxes of six rows: System, Resources, and Access & status (SSH, provider, country / site, filtering, GRE, tunnels). Nothing is said twice: whether the Abuse-Defender is on is the label of its button. */
function serverGridHtml(s) {
  const f = s.facts || {}, x = s.extra || {}, fl = x.iran_filter, g = x.gre, geo = x.geo || {}, pg = (s.extra || {}).geo || {};
  const mine = (state.tunnelsCache || []).filter(t => t.iran_name === s.name || t.foreign_name === s.name);
  const kv = (l, v, id) => `<div class="kv2"><span>${l}</span><div ${id ? `id="res-${id}"` : ""}>${v}</div></div>`;
  const mb = v => v == null ? "" : v >= 1024 ? (v / 1024).toFixed(1).replace(/\.0$/, "") + " GB" : v + " MB";
  const rr = (l, v, warn, bad, detail, kind) => `<div class="rrow r-${kind || "x"}"><span>${l}</span><i class="rbar"><u class="${v >= bad ? "bad" : v >= warn ? "warn" : ""}" style="width:${v == null ? 0 : Math.min(100, v)}%"></u></i><em><b>${v == null ? "—" : v + "%"}</b>${detail ? `<small>${detail}</small>` : ""}</em></div>`;
  const rt = (l, v, id) => `<div class="rrow"><span>${l}</span><i></i><em ${id ? `id="res-${id}"` : ""}><b>${v}</b></em></div>`;
  const box = (icon, title, rows) => `<div class="sbox" data-b="${title.split(" ")[0].toLowerCase()}"><h5><i>${icon}</i>${title}</h5>${rows.join("")}</div>`;
  const dom = pg.domain || "", pname = pg.provider || s.provider || "";
  if (dom || pname) setTimeout(() => provSite(s.name), 0);
  const EXT = '<svg class="ic-ext" viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 2.5h4.5V7M13.5 2.5L7.5 8.5M12 10v2.5a1 1 0 01-1 1H3.5a1 1 0 01-1-1V5a1 1 0 011-1H6"/></svg>', CP = '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.6"/><path d="M10.5 3.5v-.4a1 1 0 00-1-1h-6a1 1 0 00-1 1v6a1 1 0 001 1h.4"/></svg>';
  const provider = dom || pname ? (dom ? `<a class="ext" href="https://${esc(dom)}" target="_blank" rel="noopener" title="${esc(dom)}">${EXT}${esc(pname || dom)}</a>` : `<span>${esc(pname)}</span>`) : '<span class="mut">not found yet</span>';
  const site = `<span class="prov-site" data-srv="${esc(s.name)}"><small class="mut">checking…</small></span>`;
  const lv = fl ? (FLT_LEVEL[fl.level] || ["warn", "⚠ " + esc(fl.level || "unknown")]) : null;
  const gre = g ? `<i class="dotc ${g.ok ? "ok" : g.links ? "bad" : ""}"></i>${g.links ? `<b class="${g.ok ? "gw-ok" : "err"}">${g.ok ? "GRE ok" : "failing"}</b>` : '<span class="mut">no GRE links</span>'} <small class="mut">${g.via ? "via " + esc(g.via) + " · " : ""}${ago(g.at)} ago</small>` : '<i class="dotc"></i><span class="mut">not tested</span>';
  const tunnels = mine.length ? mine.map(t => `<span class="tchip"><i class="st ${t.state}"></i>${esc(t.name)}</span>`).join("") : '<span class="mut">none</span>';
  const sys = box(SI.server, "System", [kv("Host", `<b>${esc(f.hostname || x.hostname || "—")}</b>`), kv("Kernel", `<span class="mono">${esc(f.kernel || "—")}</span>`), kv("Uptime", f.uptime ? Math.round(f.uptime / 86400) + " d" : "—"),
    kv("Load", f.load1 != null ? f.load1 + " / " + f.load5 + " / " + f.load15 : "—"), kv("Top process", esc(f.top || "—")), kv("Connections", f.conn != null ? f.conn : "—")]);
  const res = box(SI.bars, "Resources", [rr("CPU", f.cpu, 80, 92, f.cores ? f.cores + (f.cores === 1 ? " core" : " cores") : "", "cpu"), rr("RAM", f.ram, 80, 90, f.ram_used_mb != null ? mb(f.ram_used_mb) + " / " + mb(f.ram_total_mb) : "", "ram"), rr("Swap", f.swap, 50, 80, f.swap_total_mb != null ? mb(f.swap_used_mb) + " / " + mb(f.swap_total_mb) : "", "swap"),
    rr("Disk", f.disk, 85, 95, f.disk_used_gb != null ? f.disk_used_gb + " / " + f.disk_total_gb + " GB" : "", "disk"), rt("Traffic", f.rx_mbps != null ? `<span class="trf"><i class="ta d">↓</i>${typeof labRate === "function" ? labRate(f.rx_mbps) : f.rx_mbps}</span><span class="trf"><i class="ta u">↑</i>${typeof labRate === "function" ? labRate(f.tx_mbps) : f.tx_mbps}</span>` : "—"), rt("Last sample", s.last_seen_at ? ago(s.last_seen_at) + " ago" : "never", "sample")]);
  const acc = box(SI.key, "Access & status", [kv("SSH", `<span class="mono">${esc(s.ssh_user)}@${esc(s.host)}:${s.ssh_port}</span><a class="cp" title="Copy" onclick="copyText('${esc(s.ssh_user)}@${esc(s.host)}')">${CP}</a>`), kv("Provider", provider),
    s.role === "foreign" ? kv("Country", `<b class="cty">${esc(s.country || "—")}</b> ${esc(geo.city || "")}`) : kv("Site", site),
    s.role === "foreign" ? kv("Filtering", fl ? `<span class="flt-tip" title="${esc((fl.nodes || []).map(n => (n.pending ? "…" : n.ok + "/" + n.total) + " " + n.city + (n.avg_ms != null ? " " + n.avg_ms + " ms" : "")).join(", "))}"><b class="${lv[0]}">${lv[1]}</b> <small class="mut">${ago(fl.checked_at)} ago</small></span>` : '<span class="mut">not checked</span>', "flt") : kv("Status", s.enabled === false ? '<i class="dotc"></i><span class="mut">disabled</span>' : s.online ? '<i class="dotc ok"></i><b>online</b>' : '<i class="dotc bad"></i><b class="err">offline</b>'),
    kv("GRE", gre, "gre"), kv("Tunnels", tunnels)]);
  const pings = false && fl && state.flt === s.id ? `<div class="gw-out"><b>Pings to ${esc(s.host)} from Iran</b><div class="ob-inbounds">${fl.nodes.map(n => `<span><b class="${n.pending ? "mut" : n.ok === n.total ? "gw-ok" : n.ok ? "" : "err"}">${n.pending ? "…" : n.ok + "/" + n.total}</b> ${esc(n.city)} <small class="mut">${esc(n.asn)}${n.avg_ms != null ? " · " + n.avg_ms + " ms" : ""}</small></span>`).join("")}</div>${fl.note ? `<small class="mut">${esc(fl.note)}</small>` : ""}</div>` : "";
  return `<div class="sgrid">${sys}${res}${acc}</div>${pings}`;
}
function serverLinesHtml() { return ""; }
function serverPartHtml(s, tab) {
  const f = s.facts || {}, x = s.extra || {}, ab = x.abuse || {}, fl = x.iran_filter, geo = x.geo || {};
  const abuseOn = ab.installed || (f.abuse_rules || 0) > 0;
  const tabs = "";
  const tool = (icon, title, sub, click, cls = "") => `<a class="wtool ${cls}" onclick="${click}" title="${esc(sub)}"><i>${icon}</i><b>${title}</b></a>`;   // the note is a tooltip: the tiles stay small
  // every tile = icon + title + one short line; what a test found is shown only in "Last results", so the tiles stay the same size
  if (tab === "tools") return tabs + `<div class="wgroup"><h4>Manage</h4><div class="wtools uni">${tool("⇄", "GRE test", "do its GRE links answer?", `greCheck(${s.id})`)}${tool("🔑", "Connect SSH", "install the panel's key with the password", `srvConnect(${s.id}, '${esc(s.name)}')`)}${tool("🔐", "Change SSH password", "set a new login password on the server (or a strong generated one)", `srvPassword(${s.id}, '${esc(s.name)}')`)}${tool("↺", "Forget host key", "after a reinstall of this server", `forgetKey(${s.id}, '${esc(s.name)}')`)}</div></div>
    ${s.role === "foreign" ? `<div class="wgroup"><h4>Security</h4><div class="wtools uni">${tool("🛰", "Check filtering in Iran", "ping from your Iran servers + check-host.net", `srvFilter(${s.id})`)}${tool("🛡", abuseOn ? "Remove Abuse-Defender" : "Install Abuse-Defender", abuseOn ? "stop dropping traffic to abused ranges" : "drop traffic to abused ranges", `srvAbuse(${s.id}, '${abuseOn ? "remove" : "install"}')`)}</div></div>` : ""}
    ${resBox(s)}
    <div class="wgroup"><h4>Open on my computer</h4><div class="wtools uni">${tool("🖥", "Windows ssh", "a .bat with your own key", `srvOpen(${s.id}, 'ssh')`)}${tool("🐱", "PuTTY", "launcher", `srvOpen(${s.id}, 'putty')`)}${tool("🧰", "MobaXterm", "launcher", `srvOpen(${s.id}, 'mobaxterm')`)}</div></div>
    <div class="wgroup"><h4>Danger</h4><div class="wtools uni">${tool("⏻", "Reboot", "its tunnels are down until it is back", `srvReboot(${s.id}, '${esc(s.name)}')`, "danger")}</div></div>`;
  if (tab === "console") { if (state.con !== s.id) state.con = s.id; return tabs + conHtml(s); }
  if (tab === "charts") { const c = (state.chartCache || {})[s.id + ":" + state.mrange]; return tabs + `<div class="${c ? "mon" : "charts"}" id="srv-charts-${s.id}">${c || '<div class="mut">loading 24 h…</div>'}</div><div class="kv" id="srv-peaks-${s.id}"></div>`; }      // a window that is drawn again shows the charts it had at once: nothing flashes to "loading" and nothing jumps
  const mine = (state.tunnelsCache || []).filter(t => t.iran_name === s.name || t.foreign_name === s.name);
  const boxes = [
    ibox(SI.server, "System", [irow("Host", `<b>${esc(f.hostname || x.hostname || "—")}</b>`), irow("Kernel", `<span class="mono">${esc(f.kernel || "—")}</span>`), irow("Uptime", f.uptime ? Math.round(f.uptime / 86400) + " d" : "—"), irow("Load", f.load1 != null ? f.load1 + " / " + f.load5 + " / " + f.load15 : "—"), irow("Connections", f.conn != null ? f.conn : "—"), irow("Busiest process", esc(f.top || "—"))]),
    ibox(SI.bars, "Resources", [irow("CPU", `${pct(f.cpu, 80, 92)} <small class="mut">${f.cores ? f.cores + (f.cores === 1 ? " core" : " cores") : ""}</small>`), irow("RAM", `${pct(f.ram, 80, 90)} <small class="mut">${f.ram_used_mb != null ? f.ram_used_mb + " / " + f.ram_total_mb + " MB" : ""}</small>`), irow("Swap", f.swap != null ? f.swap + "%" : "—"), irow("Disk", `${pct(f.disk, 85, 95)} <small class="mut">${f.disk_used_gb != null ? f.disk_used_gb + " / " + f.disk_total_gb + " GB" : ""}</small>`), irow("Traffic", f.rx_mbps != null ? `↓ ${f.rx_mbps} ↑ ${f.tx_mbps} Mb/s` : "—")]),
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
  document.querySelectorAll(".prov-site").forEach(el => { if (el.dataset.srv === name) el.innerHTML = !st || st.ok == null ? '<small class="mut">site not checked</small>' : st.ok ? `<i class="chk ok">✓</i><span class="two"><span><b class="gw-ok">site answers</b> <small class="mut">${st.ms} ms</small></span>${st.via ? `<small class="mut">from ${esc(st.via)}</small>` : ""}</span>` : `<i class="chk bad">✕</i><span class="two"><b class="err">site does not answer</b>${st.via ? `<small class="mut">from ${esc(st.via)}</small>` : ""}</span>`; });
}
/* a window tab only redraws the window body - no API call, the list behind it stays as it is */
window.copyText = t => { const done = () => toast("✓ Copied " + t, "ok"); if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(t).then(done, () => {}); else { const ta = document.createElement("textarea"); ta.value = t; document.body.appendChild(ta); ta.select(); try { document.execCommand("copy"); done(); } catch (e) { /* no clipboard */ } ta.remove(); } };
window.wtab = k => {
  if (window.labOn && labOn() && window.labWtab) { labWtab(k); return; }      // the Lab has sections of its own (lab.js)
  state.wtab = k;
  const body = $("#modal .modal-body");
  if (body && state.page === "nodes") { const s = (state.serversCache || []).find(x => x.id === state.open); if (s) { const mdl = body.closest(".modal"); if (mdl) { const w = state.winH && state.winH.id === s.id ? state.winH : (state.winH = {id: s.id, h: 0}); w.h = Math.max(w.h, mdl.offsetHeight); mdl.style.minHeight = w.h + "px"; }      // the window never gets shorter while its tabs change, so Console → Back does not jump
    body.innerHTML =`<div id="srv-detail-${s.id}">${serverDetailHtml(s)}</div>`; if (k !== "console" && k !== "install" && k !== "node") loadServerCharts(s.id); if (k === "console") { const c = $(`#con-cmd-${s.id}`); if (c) c.focus(); } return; } }
  if (body && state.page === "tunnels") { const t = (state.tunnelsCache || []).find(x => x.id === state.open); if (t) { body.innerHTML = `<div id="tun-detail-${t.id}">${tunWinHtml(t)}</div>`; tunAfter(t); return; } }
  if (body && state.page === "configs" && cf.open) { const c = (cf.cache || []).find(x => x.id === cf.open); if (c) { body.innerHTML = `<div id="cf-detail-${c.id}">${cfDetailHtml(c)}</div>`; cfLoadChart(c.id); return; } }
  refresh();
};
let CON_PRESETS = {};
api("/api/nodes/presets").then(d => { d.presets.forEach(p => CON_PRESETS[p.key] = [p.label, p.command]); }).catch(() => {});
state.conOut = {};
/* ---- the console, for someone who is not an expert: big plain questions to click (the panel asks the server and shows the answer; nothing is changed), and the old command line for those who want it, folded away */
const CON_TILES = [["health", "💚", "Is it healthy?", "Load, memory and disk in one look"], ["failed", "🛠", "Is anything broken?", "Stopped services and the tunnel units"], ["logs", "📜", "What happened lately?", "The last 60 lines of the server's log"],
  ["top", "🔥", "What is slow?", "The programs that use the most CPU"], ["disk", "💾", "Is the disk full?", "Free space and the biggest folders"], ["net", "🌐", "Network and open ports", "Addresses, routes, what the server listens on"]];
function conHtml(s) {
  state.conOut = state.conOut || {}; state.conMeta = state.conMeta || {};
  const m = state.conMeta[s.id] || {}, tile = ([k, ic, t, d]) => `<div role="button" tabindex="0" class="con-tile ${m.key === k ? "on" : ""}" onclick="conAsk(${s.id}, '${k}')" onkeydown="if(event.key==='Enter')conAsk(${s.id}, '${k}')" title="${esc(d)}"><i>${ic}</i><b>${t}</b><small>${d}</small></div>`;
  return `<div class="con2" id="con-${s.id}"><div class="con-top"><b>What do you want to know about ${esc(s.name)}?</b><small>Click a question: the panel asks the server and shows the answer below. Nothing is changed on the server.</small></div>
    <div class="con-tiles">${CON_TILES.map(tile).join("")}</div>
    <div class="con-res ${m.bad ? "bad" : ""}"><div class="con-rh">${m.title ? `<b>${esc(m.title)}</b><span class="mut">${esc(m.note || "")}</span>` : `<span class="mut">The answer shows here.</span>`}</div><pre class="gl-out" id="con-out-${s.id}">${esc(state.conOut[s.id] || "")}</pre></div>
    <details class="con-adv"><summary>Advanced: type your own command</summary><div class="frm-act"><input id="con-cmd-${s.id}" style="flex:1" placeholder="command to run as ${esc(s.ssh_user)}…" onkeydown="if(event.key==='Enter')srvRun(${s.id})"><button class="pri sm" onclick="srvRun(${s.id})">Run</button></div></details></div>`;
}
window.conAsk = async (id, key) => {
  const t = CON_TILES.find(x => x[0] === key); state.conOut = state.conOut || {}; state.conMeta = state.conMeta || {};
  state.conMeta[id] = {key, title: t ? t[2] : key, note: "asking the server…"}; state.conOut[id] = ""; if (state.wtab === "console") wtab("console");
  try { const r = await api(`/api/servers/${id}/console`, {method: "POST", body: JSON.stringify({command: key})}); state.conOut[id] = (r.output || "").trim() || "(the server answered with nothing)"; state.conMeta[id] = {key, title: t ? t[2] : key, note: `${r.ms} ms${r.code ? " · did not finish well (exit " + r.code + ")" : ""}`, bad: !!r.code}; }
  catch (e) { state.conOut[id] = e.message; state.conMeta[id] = {key, title: t ? t[2] : key, note: "could not ask", bad: true}; }
  if (state.wtab === "console") wtab("console");
};
/* ---- the Install view of a server window: what can be put on this server, each as a card in plain words */
/* the Install view, after the operator's design: two node cards, then one row per tool; every button has its icon */
const INS_IC = {
  dl: '<path d="M12 4v10m0 0l-4-4m4 4l4-4M5 19h14"/>', copy: '<rect x="9" y="9" width="11" height="11" rx="2.5"/><path d="M5 15V6.5A2.5 2.5 0 017.5 4H15"/>', doc: '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4M10 12h5M10 16h5"/>',
  eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>', shield: '<path d="M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6z"/>', trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"/>',
  refresh: '<path d="M20 11a8 8 0 00-14-4L4 9M4 4v5h5M4 13a8 8 0 0014 4l2-2M20 20v-5h-5"/>', up: '<path d="M12 20V5m0 0l-6 6m6-6l6 6"/>'
};
const insIc = k => `<svg class="ins-svg" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${INS_IC[k]}</svg>`;
function installHtml(s) {
  const ab = ((s.extra || {}).abuse || {}), abuseOn = ab.installed || ((s.facts || {}).abuse_rules || 0) > 0;
  const btn = (ic, label, click, cls = "", off = false) => `<button type="button" class="ins-btn ${cls}" ${off ? "disabled" : `onclick="${click}"`}>${insIc(ic)}${label}</button>`;
  const row = (ic, title, chip, btns, tip) => `<div class="ins-row" title="${esc(tip)}"><span class="ins-ico">${insIc(ic)}</span><b>${title}</b>${chip}<span class="sp"></span>${btns}</div>`;
  return `<div class="ins3">
    <div class="ins-two">
      <div class="ins-node"><div class="ins-h"><span class="ins-sq mz"></span><b>Marzban node</b></div>
        <div class="ins-field"><textarea id="ins-cert-${s.id}" rows="3" spellcheck="false" placeholder="Certificate from the Marzban panel (Nodes → Add node), from BEGIN to END">${esc(window.NODE_CERT || "")}</textarea><button type="button" class="ins-mini" title="Paste the certificate from the clipboard" onclick="insPaste(${s.id})">${insIc("doc")}</button></div>
        <div class="ins-act">${btn("copy", "Copy", `insCopy(${s.id})`)}<span class="sp"></span>${btn("dl", "Install node", `srvInstall(${s.id}, 'marzban_node')`, "pri")}</div></div>
      ${window.pgSrvCard ? pgSrvCard(s) : ""}
    </div>
    <div class="ins-rows">
      ${s.role === "foreign" ? row("shield", "Abuse-Defender", `<span class="pill ins-chip ${abuseOn ? "ok" : "off"}"><i></i>${abuseOn ? "Installed" : "Not installed"}</span>`, btn("dl", "Install", `srvAbuse(${s.id}, 'install')`, "", abuseOn) + btn("trash", "Remove", `srvAbuse(${s.id}, 'remove')`, "danger", !abuseOn), "Drops traffic to abused address ranges") : ""}
      ${row("up", "Update the server", '<span class="pill ins-chip off">apt</span>', btn("refresh", "Update", `srvInstall(${s.id}, 'os_update')`, "pri"), "apt update and upgrade of the system packages; the server is not rebooted")}
    </div></div>`;
}
window.insCopy = id => { const t = (($(`#ins-cert-${id}`) || {}).value || "").trim(); if (!t) { toast("Nothing to copy: paste the certificate first", "bad"); return; } try { navigator.clipboard.writeText(t); toast("✓ Copied", "ok"); } catch (e) { toast("✕ The browser did not allow the copy", "bad"); } };
window.insPaste = async id => { try { const t = await navigator.clipboard.readText(); const a = $(`#ins-cert-${id}`); if (a && t) a.value = t.trim(); } catch (e) { toast("✕ The browser did not allow reading the clipboard: paste with Ctrl+V", "bad"); } };
const INS_LABEL = {marzban_node: "Marzban node", pasarguard_node: "PasarGuard node", os_update: "system update"};
window.srvPgCert = async id => {      // the public certificate the PasarGuard node made: shown to copy into the PasarGuard panel
  try { const r = await api(`/api/servers/${id}/pasarguard-cert`); const sel = $(`#ins-pgp-${id}`); const box = sel && sel.closest(".ins-node"); if (!box) return;
    let out = box.querySelector(".ins-cert"); if (!out) { out = document.createElement("textarea"); out.className = "ins-cert"; out.readOnly = true; out.rows = 5; box.querySelector(".ins-act").before(out); }
    out.value = r.cert; out.select(); try { navigator.clipboard.writeText(r.cert); } catch (e) { /* the box is selected: copy it by hand */ } toast("✓ Certificate copied: set it in the PasarGuard panel", "ok");
  } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.srvInstall = async (id, item) => {
  const s = (state.serversCache || []).find(x => x.id === id); if (!s) return;
  let cert = ""; if (item === "marzban_node") { cert = ($(`#ins-cert-${id}`) || {}).value || ""; if (!/BEGIN CERTIFICATE/.test(cert)) { toast("✕ Paste the certificate of the Marzban panel first", "bad"); return; } }
  if (!await gconfirm(`Install: ${INS_LABEL[item]} on ${s.name}? It runs on the server for a few minutes.`)) return;
  try { const r = await api(`/api/servers/${id}/install`, {method: "POST", body: JSON.stringify({items: [item], cert})}); showModal(`<b>⬇ ${INS_LABEL[item]} on ${esc(s.name)}</b>`, '<div id="ap-story" class="fix"><div class="fx-line run"><i></i>Starting…</div></div>', "wide"); followStory(r.id, $("#ap-story"), 400).then(() => refresh()); }
  catch (e) { toast("✕ " + e.message, "bad"); }
};window.srvConsole = id => { state.con = id; if (state.open === id && $("#modal .modal-body")) { wtab("console"); return; } state.open = id; state.wtab = "console"; refresh(); };
window.srvRun = async id => { const cmd = $(`#con-cmd-${id}`).value.trim(); if (!cmd) return; const out = $(`#con-out-${id}`); out.textContent = "running…"; try { const r = await api(`/api/servers/${id}/console`, {method: "POST", body: JSON.stringify({command: cmd})}); state.conOut[id] = `$ ${cmd}\n${r.output}${r.code ? "\n[exit " + r.code + "]" : ""}\n(${r.ms} ms)`; } catch (e) { state.conOut[id] = "✕ " + e.message; } out.textContent = state.conOut[id]; };
window.srvConnect = async (id, name) => { const pw = await gprompt(`SSH password of ${name} (used once to install the panel's key, not stored):`); if (!pw) return; toast("Connecting…"); try { const r = await api(`/api/servers/${id}/connect`, {method: "POST", body: JSON.stringify({password: pw})}); toast("✓ Key installed on " + r.hostname, "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.srvOpen = (id, app) => { toast("Preparing… your key is installed on the server first"); const a = document.createElement("a"); a.href = `/api/servers/${id}/launcher?app=${app}`; a.download = ""; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => toast("✓ Launcher downloaded. Once: put your key from Servers → 'Download my SSH key' at %USERPROFILE%\\.ssh\\nikatel_user", "ok"), 1500); };
window.srvAbuse = async (id, action) => { if (!await gconfirm(action === "install" ? "Install Abuse-Defender on this foreign server? Outgoing traffic to a public list of abused ranges is dropped; your own servers, tunnel links, DNS and gateway stay allowed. Takes a minute." : "Remove Abuse-Defender from this server?")) return; const s = (state.serversCache || []).find(x => x.id === id);
  try { const r = await api(`/api/servers/${id}/abuse`, {method: "POST", body: JSON.stringify({action, stream: true})}); showModal(`<b>🛡 Abuse-Defender: ${action === "install" ? "install" : "remove"} on ${esc(s ? s.name : "the server")}</b>`, '<div id="ap-story" class="fix"><div class="fx-line run"><i></i>Starting…</div></div>', "wide"); followStory(r.id, $("#ap-story"), 400).then(() => refresh()); }      // (the owner, 2026-10-19: what is installed shows its log in a box - the steps, and what it failed with - not a toast that goes by)
  catch (e) { toast("✕ " + e.message, "bad"); } };
/* the result of the GRE test and of the filtering check is written into the Tools window itself */
const setRes = (key, html) => { for (const id of ["res-" + key, "tile-" + key]) { const el = $("#" + id); if (el) el.innerHTML = html; } };   // the tool tile and the results box
const FLT_LEVEL = {clean: ["gw-ok", "✓ Clean"], filtered: ["err", "✕ Filtered"], blocked: ["err", "✕ Blocked"]};
const greTxt = g => g ? `<b class="${g.ok || !g.links ? "gw-ok" : "err"}">${g.ok ? "✓ GRE OK" : g.links ? "✕ GRE" : "✓ no GRE links"}</b> · ${esc(g.detail)} · ${ago(g.at)} ago` : "do the GRE links of this server answer?";
const fltTxt = f => { if (!f) return "ping from your Iran servers + check-host.net"; const lv = FLT_LEVEL[f.level] || ["warn", "⚠ " + esc(f.level || "unknown")]; return `<b class="${lv[0]}">${lv[1]}</b> · ${esc(f.verdict || "")} · ${ago(f.checked_at)} ago`; };
const SPF_LEVEL = {yes: ["gw-ok", "✓ Can spoof"], part: ["warn", "⚠ Partly"], no: ["err", "✕ Does not spoof"], blind: ["warn", "⚠ No answer"], norewrite: ["warn", "⚠ Not rewritten"]};
const spfTxt = p => { const lv = SPF_LEVEL[p.level] || ["warn", "⚠ " + esc(p.level || "unknown")]; return `<b class="${lv[0]}" title="${esc(p.verdict || "")}">${lv[1]}</b> · to ${esc(p.target)} as ${esc(p.spoof_ip)}: ${p.forged} of ${p.sent} arrived · ${ago(p.at)} ago`; };      // (the spoof test of the owner's method: ⋯ → Tests → Spoof test…)
function resBox(s) {
  const x = s.extra || {}, g = x.gre, f = x.iran_filter, fc = s.facts || {}, ab = x.abuse || {};
  const abuseOn = ab.installed || (fc.abuse_rules || 0) > 0, rules = fc.abuse_rules || ab.rules || 0;
  const row = (label, key, body) => `<div class="res-row"><span class="mut">${label}</span><span id="res-${key}">${body}</span></div>`;
  return `<div class="wgroup"><h4>Last results</h4><div class="ibox res-box">${row("GRE", "gre", g ? greTxt(g) : '<span class="mut">not tested yet</span>')}${row("Spoofing", "spf", x.spoof ? spfTxt(x.spoof) : '<span class="mut">not tested yet</span>')}
    ${s.role === "foreign" ? row("Filtering in Iran", "flt", f ? fltTxt(f) : '<span class="mut">not checked yet</span>') + row("Abuse-Defender", "abuse", abuseOn ? `<b class="gw-ok">✓ Installed</b> · ${rules} ranges blocked${ab.updated_at ? " · list " + ago(ab.updated_at) + " ago" : ""}` : '<span class="mut">not installed</span>') : ""}
    ${row("Last sample", "sample", s.last_seen_at ? `${ago(s.last_seen_at)} ago${fc.uptime ? " · up " + esc(String(fc.uptime)) : ""}` : '<span class="mut">never</span>')}</div></div>`;
}window.greCheck = async id => {      // Does GRE work to this server? An Iran server of the nodes builds a temporary GRE link to it, pings the inner address and removes the link
  toast("Testing GRE from an Iran server… (up to a minute)");
  try { const r = await api(`/api/servers/${id}/gre-probe`, {method: "POST"}); toast((r.ok ? "✓ GRE works: " : "✕ GRE does not work: ") + r.detail, r.ok ? "ok" : "bad"); } catch (e) { toast("✕ " + e.message, "bad"); }
  refresh();
};
window.srvFilter = async id => { setRes("flt", '<span class="mut">⏳ pinging from your Iran servers… (about 30 s)</span>'); toast("Pinging from Iran… (about 30 s)"); try { const r = await api(`/api/servers/${id}/filter-check`, {method: "POST"}); toast((r.level === "clean" ? "✓ " : "⚠ ") + r.verdict, r.level === "clean" ? "ok" : "bad"); state.flt = id; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.srvReboot = async (id, name) => { if (!await gconfirm(`Reboot ${name}? Its tunnels are down until it is back (usually under a minute).`)) return; try { await api(`/api/servers/${id}/reboot`, {method: "POST"}); toast("✓ Reboot requested", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } };
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
  return `<div class="mcard" data-c="${o.col || ""}"><h6>${o.ic ? `<i class="mc-ic">${o.ic}</i>` : ""}${title}<span class="mval ${tone}">${fmt(cur)}${unit}</span></h6>
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
window.mrange = h => { state.mrange = h; localStorage.setItem("nk.mrange", h); const sb = document.querySelector('[id^="srv-charts-"]'), tb = document.querySelector('[id^="tun-charts-"]'), cb = document.querySelector('[id^="cf-charts-"]'); if (sb) { loadServerCharts(+sb.id.split("-").pop()); if (window.labOn && labOn() && typeof labStrip === "function") labStrip(+sb.id.split("-").pop()); } else if (tb) loadTunnelCharts(+tb.id.split("-").pop()); else if (cb) cfLoadChart(+cb.id.split("-").pop()); };
async function loadServerCharts(id) {
  const box = document.getElementById("srv-charts-" + id);
  if (!box) return;
  state.chartCache = state.chartCache || {};
  const cached = state.chartCache[id + ":" + state.mrange];
  if (cached && !box.firstElementChild) { box.className = "mon"; box.innerHTML = cached; }          // what was drawn last time is back at once, so the window keeps its height; the fresh data replaces it in place
  const d = await api(`/api/servers/${id}/samples?hours=${state.mrange}`);
  const pk = document.getElementById("srv-peaks-" + id); if (pk) pk.remove();
  const ts = d.samples.map(r => r[0]), col = n => d.samples.map(r => r[d.columns.indexOf(n)]), series = n => ts.map((t, i) => [t, col(n)[i]]);
  CH_reset();
  const cpu = nums(col("cpu")), ram = nums(col("ram")), disk = nums(col("disk")), rx = nums(col("rx_mbps")), tx = nums(col("tx_mbps")), conn = nums(col("conn"));
  const f1 = v => v == null ? "—" : (Math.round(v * 10) / 10), pc = v => f1(v) + "%";
  box.className = "mon";
  const chartsHtml = `<div class="mhead">${rangeTabs()}<small class="mut">${d.samples.length} samples · one every 2 minutes · hover a chart for exact values</small></div>
    <div class="mtiles">${mtile("CPU", pc(avgOf(cpu)), "peak " + pc(cpu.length ? Math.max(...cpu) : null), avgOf(cpu) > 85 ? "bad" : "")}${mtile("RAM", pc(avgOf(ram)), "peak " + pc(ram.length ? Math.max(...ram) : null), avgOf(ram) > 90 ? "bad" : "")}${mtile("Disk", pc(disk.length ? disk[disk.length - 1] : null), "now", disk.length && disk[disk.length - 1] > 85 ? "bad" : "")}${mtile("Traffic", "↓" + f1(avgOf(rx)) + " ↑" + f1(avgOf(tx)), "Mb/s avg")}${mtile("Connections", conn.length ? Math.round(avgOf(conn)) : "—", "peak " + (conn.length ? Math.max(...conn) : "—"))}</div>
    <div class="mgrid">${chartCard("CPU", series("cpu"), {col: "blue", ic: SI.cpu, max: 100, unit: "%", warn: 70, bad: 85, fmt: v => Math.round(v)})}${chartCard("RAM", series("ram"), {col: "purple", ic: SI.ram, max: 100, unit: "%", warn: 80, bad: 90, fmt: v => Math.round(v)})}${chartCard("Load (1 min)", series("load1"), {col: "blue", ic: SI.pulse, fmt: v => (Math.round(v * 100) / 100)})}${chartCard("Download ↓", series("rx_mbps"), {col: "green", ic: SI.down, unit: " Mb/s"})}${chartCard("Upload ↑", series("tx_mbps"), {col: "orange", ic: SI.arrowUp, unit: " Mb/s"})}${chartCard("Connections", series("conn"), {col: "cyan", ic: SI.users, fmt: v => Math.round(v)})}</div>`;
  morphHtml(box, chartsHtml);      // in place: only the numbers that changed are rewritten
  state.chartCache[id + ":" + state.mrange] = box.innerHTML;
}
function CH_reset() { for (const k of Object.keys(CH)) delete CH[k]; }
let qT;
/* ONE rule for every search box: each word of what you type may sit anywhere in the text (the middle of a name too), several words must all be found, and the separators do not matter (a dash, a dot, a space and nothing are the same) */
const nkCompact = s => String(s).replace(/[\s._\-:\/@]+/g, "");
const nkHit = (hay, q) => { const h = String(hay || "").toLowerCase(), words = String(q || "").toLowerCase().split(/\s+/).filter(Boolean); if (!words.length) return true; const hc = nkCompact(h); return words.every(w => h.includes(w) || hc.includes(nkCompact(w))); };
/* the search box of Nodes and Tunnels sits in its own bar and hides rows at once: nothing is redrawn, so nothing around it moves */
const searchSeg = () => `<span class="tb-search"><input id="q" type="search" placeholder="Search…" value="${esc(state.q)}" oninput="if(document.activeElement===this)setQ(this.value)" autocomplete="off" name="nk-find" readonly onfocus="this.removeAttribute('readonly')" data-lpignore="true" data-1p-ignore spellcheck="false"></span>`;
const searchBar = () => `<div class="searchbar"><input id="q" type="search" placeholder="Search…" value="${esc(state.q)}" oninput="if(document.activeElement===this)setQ(this.value)" autocomplete="off" name="nk-find" readonly onfocus="this.removeAttribute('readonly')" data-lpignore="true" data-1p-ignore spellcheck="false"></div>`;
function applyQ() {
  const q = ((state.page === "configs" ? cf.q : state.q) || "").trim(); let shown = 0, total = 0;
  document.querySelectorAll("#page [data-q]").forEach(tr => { const hit = nkHit(tr.dataset.q, q); tr.classList.toggle("q-hide", !hit); total++; if (hit) shown++; });      // every word may sit anywhere in the name, the address, the provider ...
  document.querySelectorAll("#page .nk-ghead").forEach(h => { const tb = h.nextElementSibling; if (tb && tb.matches("table")) h.classList.toggle("q-hide", !!q && !tb.querySelector("tr[data-q]:not(.q-hide)")); });
  document.querySelectorAll("#page table.tbl").forEach(tb => tb.classList.toggle("q-hide", !!q && !!tb.querySelector("tr[data-q]") && !tb.querySelector("tr[data-q]:not(.q-hide)")));
  document.querySelectorAll("#page .dr-g").forEach(g => { const any = !!g.querySelector(".dr-rec:not(.q-hide)"); g.classList.toggle("q-hide", !any); g.classList.toggle("q-open", !!q && any); });      // a search opens the groups that have a match
  const sb = $(".searchbar"); if (sb) sb.dataset.n = q ? shown + " / " + total : "";
}
const holdBoxes = () => document.querySelectorAll("#page .lbox").forEach(b => { if (!b.style.minHeight) b.style.minHeight = b.offsetHeight + "px"; });      // the list box keeps its height while the rows are filtered
window.setQ = v => { holdBoxes(); state.q = v.trim().toLowerCase(); applyQ(); };
window.checkServer = async id => { toast("Checking…"); try { await api(`/api/servers/${id}/check`, {method: "POST"}); toast("✓ Checked", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.srvToggle = id => {      // the switch of the server window: it flips at once (smoothly), the work follows in the background, and it cannot be pressed twice meanwhile
  const s = (state.serversCache || []).find(x => x.id === id), sw = document.querySelector("#modal .srv-sw"); if (!s) return;
  if (sw && sw.dataset.busy) { const i = sw.querySelector("input"); if (i) i.checked = !i.checked; return; }
  const on = s.enabled === false;
  if (sw) { sw.dataset.busy = "1"; sw.classList.toggle("on", on); const t = sw.querySelector("b"); if (t) t.textContent = on ? "Enabled" : "Disabled"; const i = sw.querySelector("input"); if (i) i.checked = on; }
  srvSet(id, on);
};
/* enable / disable a server: enabled = the panel checks it (the look at the server runs in the background: the switch never waits for it), disabled = it is not checked and gives no alerts; nothing changes on the server itself. The status bar offers Undo. */
window.srvSet = async (id, on, undoing) => {
  const s = (state.serversCache || []).find(x => x.id === id); if (!s) return;
  const undo = () => srvSet(id, !on, true);
  try {
    await api(`/api/servers/${id}`, {method: "PUT", body: JSON.stringify({name: s.name, host: s.host, role: s.role, ssh_user: s.ssh_user, ssh_port: s.ssh_port, provider: s.provider || "", enabled: on})});
    s.enabled = on;
    toast(on ? (undoing ? `${s.name} is enabled again.` : `${s.name} is enabled. Looking at it now…`) : (undoing ? `${s.name} is disabled again.` : `${s.name} is disabled. It is no longer checked.`), "ok", undoing ? null : undo);
    refresh();
    if (on) api(`/api/servers/${id}/check`, {method: "POST"}).then(() => { if (!undoing) toast(`${s.name} is enabled and online.`, "ok"); }).catch(e => toast(`${s.name} is enabled, but it does not answer yet: ${String(e.message).slice(0, 90)}`, "bad")).finally(() => refresh());
  } catch (e) { toast("✕ " + e.message, "bad"); refresh(); }
};
window.editServer = id => { state.edit = id; state.open = id; state.wtab = "overview"; refresh(); };      // Edit leaves the console: Back from the form returns to the main window, not to the console
window.cancelEdit = () => { state.edit = null; if (state.build) { closeModal(); return; } refresh(); };
window.openBuild = async () => {      // the window opens ON the page that is there, with no redraw of the page behind it first (the owner, 2026-10-16: the page and its background jumped when Create tunnel was pressed); closing leaves the page as it is, too
  if (state.build && $("#modal .modal")) return;
  if (Array.isArray(state.serversCache) && state.serversCache.length < 2) { toast("A tunnel is built between two servers: add an Iran server and a foreign server first", "bad"); return; }
  state.open = null;
  if (state.page === "tunnels" && $("#page .tun-box")) { state.build = true; try { showModal(`<b>＋ Create tunnel</b>`, await buildFormHtml(), "wide"); } catch (e) { state.build = null; toast("✕ " + e.message, "bad"); } return; }
  state.build = true; refresh();
};
window.newTunnel = () => { if (state.page !== "tunnels" || sub("tunnels") !== "list") { go("tunnels", "list"); state.build = true; refresh(); } else openBuild(); };      // (go names the tab: a first draw of another tab must never land over the Tunnel manager - the CDN was the first tab from 2026-10-14 to 2026-10-17, now it is the last)
/* the SSH password was wrong (or the server did not answer): ask for it again, three tries, instead of leaving a server that silently has no login */
async function connectWithPrompt(id, name, firstError) {
  const noAnswer = m => /no route|timed out|timeout|refused|unreachable|could not resolve|name or service|network is/i.test(m), short = m => String(m).replace(/ ?· id [0-9a-f]+/i, "").replace(/^.*?(No route to host|Connection timed out|Connection refused|Network is unreachable|Operation timed out)/i, "$1").slice(0, 100);
  let why = firstError;
  for (let i = 0; i < 3; i++) {
    if (noAnswer(why)) { await glassChoice(`${name} does not answer on SSH (${short(why)}). Check its address and port; then use 🔑 on its row.`, [{label: "OK", v: 1, cls: "pri"}]); refresh(); return false; }      // (the server cannot be reached: a password would not help - the owner, 2026-10-15)
    const pw = await gprompt(`SSH password of ${name}? (Cancel: later, with 🔑 on its row)`, "", {password: true, generate: false});
    if (pw == null || pw === "") break;
    try { const r = await api(`/api/servers/${id}/connect`, {method: "POST", body: JSON.stringify({password: pw})}); toast("✓ Connected: " + r.hostname, "ok"); refresh(); return true; }
    catch (err) { why = err.message; if (!/password|user|denied/i.test(why) && !noAnswer(why)) break; }
  }
  toast(`✕ ${name} is added but has no login yet: ${short(why)}. Use 🔑 on its row.`, "bad"); refresh();
  return false;
}window.serverPassword = (id, name, why) => connectWithPrompt(id, name, why || "enter the SSH password");
window.saveServer = async (e, id) => {
  e.preventDefault();
  const f = new FormData(e.target);
  let role = f.get("role") || "";
  if (!role && !id && window.labRoleOf) role = labRoleOf(f.get("tag"));      // (the tag Iran / Foreign of the Lab says where a new server is)
  if (!role) { role = await srvWhere(e.target, true); if (!role) { await srvFlip(e.target); role = (e.target.querySelector("[name=role]") || {}).value || ""; } }
  if (!role) { toast("✕ Say where the server is (Iran or foreign) first", "bad"); return false; }
  const body = {name: f.get("name").trim(), host: f.get("host").trim(), role, ssh_user: f.get("ssh_user").trim() || "root", ssh_port: +f.get("ssh_port") || 22, provider: (f.get("provider") || "").trim(), enabled: f.get("enabled") === "on", tag: String(f.get("tag") || "").trim()}, pw = f.get("password") || "";
  const items = [f.get("x_bt") === "on" ? "btblock" : "", f.get("x_node") === "on" ? "marzban_node" : ""].filter(Boolean), cert = f.get("x_cert") || "";
  if (items.includes("marzban_node") && !/BEGIN CERTIFICATE/.test(cert)) { toast("✕ Paste the certificate of the Marzban panel first (or untick Marzban node)", "bad"); return false; }
  if (items.includes("marzban_node") && f.get("x_cert_save") === "on" && cert.trim() !== (window.NODE_CERT || "").trim()) api("/api/settings/nodes", {method: "PUT", body: JSON.stringify({marzban_cert: cert})}).then(r => { window.NODE_CERT = r.marzban_cert; }).catch(() => {});
  let pgNode = null; try { pgNode = window.pgSrvAsk ? pgSrvAsk(f) : null; } catch (er) { toast("✕ " + er.message, "bad"); return false; }      // the PasarGuard node (tick): checked before anything is added
  if (pgNode && role === "iran" && !await gconfirm(`${body.name} is an Iran server. A PasarGuard node normally runs on a foreign server (the installer needs Docker and GitHub, which Iran often blocks). Go on?`)) return false;
  const cores = !id && f.get("x_cores") === "on", later = !!(pgNode || items.length), submit = e.target.querySelector("button[type=submit]");      // (the cores come last when a node or extras were asked for: they are heavy on the same server)
  if (submit) { submit.disabled = true; submit.dataset.was = submit.textContent; submit.textContent = id ? "Saving…" : "Adding…"; }
  try {
    const saved = await api(id ? `/api/servers/${id}` : "/api/servers", {method: id ? "PUT" : "POST", body: JSON.stringify(body)});
    state.edit = null;
    if (!id && window.labOn && labOn()) closeModal();      // (the box of the Lab closes: the new row appears on the page)
    if (!id) { refresh(); if (window.labFollow) labFollow(30); }          // the new row appears at once and says what the panel is doing with it ("Waiting for the first reading…", then every step); the page follows it quietly - rows rewritten where they stand, no redraw of the page (the owner, 2026-10-16: "the page jumps several times" and "the status is not clear: show what is being done from the very first moment")
    const prep = steps => api(`/api/servers/${saved.id}/onboard`, {method: "POST", body: JSON.stringify({steps})}).then(() => { if (window.labFollow) labFollow(20); }).catch(() => {});      // read it now, check its GRE links, put the cores on it: in the background, each step written on its row
    const first = id ? ["gre"] : ["read", "gre"], rest = cores && !later ? ["cores"] : [];      // (1.69.23, the owner: "when a server is added the cores are not installed - only when its first tunnel is made, and then all of them": the box is NOT ticked any more; see onboard.ensure_all_cores)
    let connected = true;
    if (id && !pw) toast("✓ Saved", "ok");
    else if (pw) {   // a password: the panel logs in once with it and installs its own key (the password is not kept)
      toast(`Logging in to ${body.name}…`);
      try { const r = await api(`/api/servers/${saved.id}/connect`, {method: "POST", body: JSON.stringify({password: pw})}); toast((id ? "✓ Saved and connected: " : "✓ Added and connected: ") + r.hostname + (id ? "" : " - the steps are on its row"), "ok"); await prep([...first, ...rest]); }
      catch (err) { connected = await connectWithPrompt(saved.id, body.name, err.message); if (connected) await prep([...first, ...rest]); }
    } else {          // no password: the panel's key must already be on the server - try it (the check IS the first reading)
      try { const r = await api(`/api/servers/${saved.id}/check`, {method: "POST"}); if (r.online === true) { toast("✓ Server added", "ok"); await prep(["gre", ...rest]); } else { connected = false; toast("Added, but the panel's key does not log in yet: " + String(r.last_error || "the server does not answer").slice(0, 160), "bad"); } } catch (err) { connected = false; toast("Added, but the panel's key does not log in yet: " + err.message, "bad"); }
    }
    state.prefill = null; if (id) refresh();          // (an edit draws the page again; a new server is followed quietly)
    if (pgNode && connected) { await pgSrvMake(saved.id, body.name, pgNode); refresh(); }      // the node of the PasarGuard panel FIRST (it is what was asked for; the steps are on the row of the server)
    else if (pgNode) toast("The server is added, but it does not log in yet, so the node is not made: fix the login, then use Install → PasarGuard node in the window of the server", "bad");
    if (items.length && connected) {   // then the extras (the torrent block applies thousands of rules: minutes); the row says "installing"
      const r = await api(`/api/servers/${saved.id}/install`, {method: "POST", body: JSON.stringify({items, cert})});
      toast(`Installing on ${body.name}: the steps show on its row`); refresh();
      const fin = await followStory(r.id, document.createElement("div"), 1800), lastLine = fin && fin.story ? [...fin.story].reverse().find(l => l && l.trim() && !/^Done/.test(l.trim())) : "";
      toast(fin && fin.ok ? `✓ Installed on ${body.name}` : "✕ " + String(lastLine || "Not finished").trim().slice(0, 200), fin && fin.ok ? "ok" : "bad"); refresh();
    } else if (items.length) toast("Not installed: the server does not log in yet - fix that, then add the server again or use its console", "bad");
    if (later && cores && connected) await prep(["cores"]);
    if (window.labFollow) labFollow(3);
  } catch (err) { toast("✕ " + err.message, "bad"); }
  finally { if (submit && submit.isConnected) { submit.disabled = false; submit.textContent = submit.dataset.was || (id ? "Save" : "Add server"); } }      // (a refusal - the name is taken - leaves the box open: its button works again)
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
const X_SVG = '<svg viewBox="0 0 10 10" aria-hidden="true"><path d="M1.5 1.5l7 7M8.5 1.5l-7 7"/></svg>';      // the cross of the faint "remove" buttons (accounts, panels, servers)
window.deleteServer = async (id, name) => {
  const nodes = window.pgSrvOn ? pgSrvOn({id}).map(x => ({...x})) : [], pg = nodes.filter(x => x.type === "pasarguard" && x.node_id != null);      // the nodes of the panels that run on it (read before it is gone)
  const ans = await gconfirm(`Remove ${name} from the panel? Nothing changes on the server itself.`, pg.length ? {danger: true, check: {label: `Also delete the node ${pg.map(x => x.name).join(", ")} from the PasarGuard panel itself`}} : {danger: true});      // (a tick in the same box, the owner 2026-10-15: the node goes from the PasarGuard panel too - or stays there)
  if (!ans) return;
  const alsoNode = !!(ans && ans.checked);
  const gone = () => { toast("✓ Removed", "ok"); state.edit = null; state.open = null; refresh(); };
  try { await api(`/api/servers/${id}`, {method: "DELETE"}); gone(); } catch (e) {
    if (!/uses this server/.test(e.message)) { toast("✕ " + e.message, "bad"); return; }
    const used = (state.tunnelsCache || []).filter(t => t.iran_name === name || t.foreign_name === name).map(t => t.name), list = used.length ? used.join(", ") : e.message.replace(/^the tunnels? | uses this server.*$/g, "");
    if (!await gconfirm(`${name} cannot be removed while the tunnel ${list} uses it. Remove the tunnel too, and then the server? The tunnel goes to the trash. A server that is off is not asked: its files are purged if it ever comes back.`, {danger: true})) return;      // (the owner, 2026-10-19: a server that went off could not be deleted - the refusal was a toast that went by; now it is a question with the way out)
    try { await api(`/api/servers/${id}?tunnels=true`, {method: "DELETE"}); gone(); } catch (e2) { toast("✕ " + e2.message, "bad"); return; }
  }
  if (nodes.length && window.pgSrvAfterDelete) await pgSrvAfterDelete(name, nodes, alsoNode);
};window.forgetKey = async (id, name) => {
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
  if (s === "gateway") return PAGES.gateway();
  if (s === "dns") return PAGES.dns();
  if (s === "create") { state.sub.tunnels = "list"; state.build = true; }
  return PAGES.tunnelsList();
};
PAGES.configs = async () => { renderSub(); const s = sub("configs"); return s === "panels" ? PAGES.panels() : PAGES.configsList(); };
const tt = {job: null};      // the id of the temporary tunnel test of the Create tunnel window (the page of the tunnel test is gone)
window.renameTunnel = async (id, cur) => {
  const v = await gprompt("New name of this tunnel (lowercase letters, digits and -, up to 15). Only the name in the panel changes; on the servers nothing is touched:", cur); if (v == null || !v.trim() || v.trim() === cur) return;
  try { const r = await api(`/api/tunnels/${id}/name`, {method: "PUT", body: JSON.stringify({name: v.trim()})}); toast("✓ Renamed to " + r.name, "ok"); closeModal(); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.editPorts = async (id, cur) => { const v = await gprompt("Forwarded ports of this tunnel (Iran → foreign), e.g. 443, 443=2096, 10000-10009:", cur); if (v == null || !v.trim()) return; toast("Changing the ports… (the tunnel service restarts)"); try { const r = await api(`/api/tunnels/${id}/ports`, {method: "PUT", body: JSON.stringify({ports: v})}); toast("✓ Ports: " + r.mapping, "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.healthTunnel = async id => {      // ONE button: the check (up / down, data passes) and the quality measure (latency, jitter, loss, score) together, one message
  toast("Checking and measuring… (about 10 s)");
  try { await api(`/api/tunnels/${id}/check`, {method: "POST"}); const r = await api(`/api/tunnels/${id}/quality`, {method: "POST"}); toast(`✓ ${r.name}: ${r.latency_ms} ms · jitter ${r.jitter_ms} · loss ${r.loss_pct}% · score ${r.score}`, "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); refresh(); }
};
const tunRedraw = async id => {      // the window of this tunnel, drawn again with what the server says now (no page refresh: the window stays where it is)
  try { const t = await api(`/api/tunnels/${id}`), i = (state.tunnelsCache || []).findIndex(x => x.id === id); if (i >= 0) state.tunnelsCache[i] = t; const body = $("#modal .modal-body"); if (state.open === id && body) { body.innerHTML = `<div id="tun-detail-${id}">${tunWinHtml(t)}</div>`; tunAfter(t); } } catch (e) { /* the window was closed meanwhile */ }
};
let tunSbT = null;
const tunStandbyWatch = id => {      // while the standby is being built or mended the window looks again every 4 s, until it is ready (or off, or in use)
  clearInterval(tunSbT); let n = 0;
  tunSbT = setInterval(async () => {
    if (state.open !== id || !document.getElementById("tun-detail-" + id) || ++n > 90) { clearInterval(tunSbT); return; }
    await tunRedraw(id); const s = ((state.tunnelsCache || []).find(x => x.id === id) || {}).standby || {};
    if (["ready", "off", "failed", "in_use"].includes(s.state)) clearInterval(tunSbT);
  }, 4000);
};
window.tunStandbySet = async (id, on) => {
  if (!on && !await gconfirm("Remove the standby of this tunnel? If the users' ports are on it they are given back to the tunnel first.", {danger: true})) return;
  try { await api(`/api/tunnels/${id}/standby`, {method: "PUT", body: JSON.stringify({on})}); toast(on ? "✓ Standby on - it is being built beside the tunnel (about a minute); nothing changes for the users" : "✓ Standby off", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
  await tunRedraw(id); tunStandbyWatch(id);
};
window.tunStandbyNow = async id => {
  if (!await gconfirm("Hand the users' ports of this tunnel to its standby now? New connections go through the standby until you press Back now (a drill of what happens when the tunnel is cut).")) return;
  try { const r = await api(`/api/tunnels/${id}/standby/handover`, {method: "POST"}); toast(`✓ The users' ports are on the standby (${r.kind})`, "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
  await tunRedraw(id);
};
window.tunStandbyBack = async id => {
  try { await api(`/api/tunnels/${id}/standby/back`, {method: "POST"}); toast("✓ The users' ports are on the tunnel again; open connections end on the standby by themselves", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
  await tunRedraw(id); refresh();
};
window.qualityOne = async id => { toast("Measuring… (about 10 s)"); try { const r = await api(`/api/tunnels/${id}/quality`, {method: "POST"}); toast(`✓ ${r.name}: ${r.latency_ms} ms · jitter ${r.jitter_ms} · loss ${r.loss_pct}% · score ${r.score}`, "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.checkAll = async () => {      // Check all: it works right there on the button (no window); the answer is one line in the status bar and the list is drawn again
  if (state.tunChecking) return; state.tunChecking = true;
  const btn = document.querySelector('button[onclick="checkAll()"]'); if (btn) { btn.disabled = true; btn.innerHTML = '<i class="spin"></i>Checking…'; }
  try {
    const r = await api("/api/tunnels/full-check", {method: "POST"}), bad = r.rows.filter(x => x.state !== "up" || x.loss === "high").map(x => x.name);
    toast(`${r.up} up · ${r.degraded} unstable · ${r.down} down${r.high_loss ? " · " + r.high_loss + " with high packet loss" : ""}${bad.length ? ": " + bad.slice(0, 4).join(", ") + (bad.length > 4 ? "…" : "") : ""}`, r.down || r.high_loss ? "bad" : "ok");
  } catch (e) { toast("✕ " + e.message, "bad"); }
  state.tunChecking = false; refresh();
};
const LOSS_HIGH_PCT = 10;window.tunFilter = m => { localStorage.setItem("nk.tf", m); refresh(); };
/* long work on the server (202 + action_id, docs/api.md): followed here until it is done; resolves with the status of the action */
async function followAction(id, every = 2000, limitSeconds = 900) {
  for (let i = 0; i < limitSeconds * 1000 / every; i++) { await new Promise(r => setTimeout(r, every)); const s = await api(`/api/actions/${id}`); if (s.done) return s; }
  return {done: false, ok: false, story: ["still running - see the History of the Tunnel Doctor"]};
}
const lastLine = s => (s.story || []).slice(-1)[0] || "";
window.healthAll = async () => { toast("Checking every tunnel… (up to a minute)"); try { const r = await api("/api/tunnels/health-all", {method: "POST"}); const s = await followAction(r.action_id); toast(lastLine(s), s.ok ? "ok" : "bad"); } catch (e) { toast("✕ " + e.message, "bad"); } refresh(); };
window.restartTunnel = async (id, name) => { toast(`Restarting ${name}…`); try { const r = await api(`/api/tunnels/${id}/restart`, {method: "POST"}); toast(`${name}: ${r.state}` + (r.why ? " - " + r.why : ""), r.state === "up" ? "ok" : "bad"); } catch (e) { toast("✕ " + e.message, "bad"); } refresh(); };
window.restartAll = async () => { if (!await gconfirm("Restart every tunnel on both servers? Each is down for a few seconds.")) return; toast("Restarting every tunnel… (about two minutes)"); try { const r = await api("/api/tunnels/restart-all", {method: "POST"}); const s = await followAction(r.action_id); toast(lastLine(s), s.ok ? "ok" : "bad"); } catch (e) { toast("✕ " + e.message, "bad"); } refresh(); };
window.bwMeasure = async id => { toast("Measuring the bandwidth… (a few seconds)"); try { const r = await api(`/api/tunnels/${id}/bandwidth`, {method: "POST"}); const s = await followAction(r.action_id, 1500, 120); toast(lastLine(s), s.ok ? "ok" : "bad"); } catch (e) { toast("✕ " + e.message, "bad"); } refresh(); };
window.qualityAll = async () => { toast("Measuring every tunnel… (10 s each)"); try { const r = await api("/api/tunnels/quality-all", {method: "POST"}); const s = await followAction(r.action_id); toast(lastLine(s), s.ok ? "ok" : "bad"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
async function enginesLoad() {
  const box = $("#eng-box"); if (!box) return;
  const d = await api("/api/engines");
  const eng = d.engines || {};
  // one row per core: the version your servers run, the newest release, and ONE cell for the state: "Up to date", or - when a newer release is out - the Update button in its place (the owner, 2026-10-17: no Install button, and "an Update button instead of Up to date")
  const row = (k, e) => {
    const nodes = e.nodes || [], have = nodes.filter(n => n.version && n.version !== "none");
    const outdated = e.outdated || 0, unreach = nodes.filter(n => n.state === "unreachable").length;
    const upd = `<button class="pri sm eng-upd" onclick="engineUpdate('${k}')" title="${esc(`Update ${outdated} server${outdated > 1 ? "s" : ""} to ${e.latest}: its tunnel units restart (a few seconds of downtime per server)`)}">⬆ Update</button><small class="mut">${outdated} server${outdated > 1 ? "s" : ""}</small>`;
    const status = e.error ? `<span class="err">${esc(e.error)}</span>` : !nodes.length ? '<span class="mut">checking…</span>' : !have.length ? '<span class="mut">not installed</span>' : outdated ? upd : `<b class="gw-ok">✓ Up to date</b>${unreach ? ` <small class="mut">· ${unreach} unreachable</small>` : ""}`;
    return `<div class="eng-row"><span class="eng-name"><b>${esc(e.title)}</b><small class="mut" title="${have.length} of ${nodes.length} servers have it">${have.length}/${nodes.length}</small></span><span class="eng-latest">${e.latest ? esc(e.latest) : "—"}${e.page ? ` <a class="mut" href="${esc(e.page)}" target="_blank" rel="noopener" title="Release notes">↗</a>` : ""}</span><span class="eng-st" title="${esc(nodes.length ? nodes.map(n => n.name + ": " + (n.version && n.version !== "none" ? n.version : "not installed")).join(", ") : "")}">${status}</span></div>`;
  };
  box.innerHTML = `<div class="eng-list"><div class="eng-head" title="${esc(d.checked_at ? "checked " + ago(d.checked_at) + " ago - the servers are asked by themselves; nothing updates by itself: Update asks you first" : "the servers are asked by themselves")}"><span>Core</span><span>Newest</span><span>Status${d.checking ? '<i class="spin" title="checking every server…"></i>' : ""}</span></div>
    ${Object.entries(eng).map(([k, e]) => row(k, e)).join("") || '<div class="empty">Comparing the cores on your servers with the newest releases - it happens by itself, in a moment.</div>'}</div>
    ${d.job ? `<div class="mut" style="margin-top:6px">Last ${d.job.kind === "install" ? "install" : "update"}: <b>${esc(d.job.engine)} ${esc(d.job.version || "")}</b> ${d.job.done ? `— ${(d.job.items || []).filter(i => i.state === "ok").length} of ${(d.job.items || []).length} servers ${d.job.kind === "install" ? "got it" : "updated"}${(d.job.items || []).some(i => i.state !== "ok") ? ` <span class="err">(${(d.job.items || []).filter(i => i.state !== "ok").map(i => esc(i.name)).join(", ")} failed)</span>` : ""}` : "(running…)"}${d.job.error ? ` <span class="err">${esc(d.job.error)}</span>` : ""}</div>` : ""}`;
  if (d.checking) { if (!box._follow) { box._follow = true; engineFollow(); } }      // (the flag lives on the box: a GET cut by a navigation never resolves, so a flag on the function stayed on for ever - review 2026-10-16)
  else if (!d.checked_at || Date.now() / 1000 - d.checked_at > 600) { if (!enginesLoad.busy) { enginesLoad.busy = true; api("/api/engines/check", {method: "POST"}).catch(() => {}).finally(() => { enginesLoad.busy = false; }).then(() => engineFollow()); } }   // checked by itself when the last check is older than ten minutes (the owner, 2026-10-16: no Check now button); the page never waits for it
};
async function engineFollow() {   // the check runs on the server; the box is redrawn when it is done (polled every 5 s, up to two minutes)
  try { for (let i = 0; i < 24; i++) { await new Promise(r => setTimeout(r, 5000)); if (!$("#eng-box")) return; const d = await api("/api/engines").catch(() => null); if (!d || !d.checking) { enginesLoad().catch(() => {}); return; } } } finally { const b = $("#eng-box"); if (b) b._follow = false; }
}
window.engineCheck = async () => { toast("Asking every server… (up to a minute)"); try { await api("/api/engines/check", {method: "POST"}); enginesLoad(); engineFollow(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.engineUpdate = async k => {      // Update = the servers that have an older build (the Install button is gone, the owner, 2026-10-17: a server added from now on gets the cores at once, and "Tunnel cores…" in the menu of a server puts them on one that has not got them)
  if (!await gconfirm(`Update ${k} on every outdated server? Each server's tunnel units restart (a few seconds of downtime per server).`)) return;
  toast("Updating… (minutes)");
  try { const r = await api(`/api/engines/${k}/update`, {method: "POST"}); toast(r.error ? "✕ " + r.error : `✓ ${r.items.filter(i => i.state === "ok").length} updated`, r.error ? "bad" : "ok"); api("/api/engines/check", {method: "POST"}).catch(() => {}).then(() => engineFollow()); enginesLoad(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
/* A tunnel the Doctor REPLACES (the old one is removed, the new one is not recorded yet: a minute or more) was gone from the list, and every row under it moved up and back down - "the page jumps when a tunnel is repaired"
   (the owner, 2026-10-19; measured on the real page: 46 px for each row below, at the start and again at the end of every swap). Its last row stays where it was, as a placeholder (no buttons, "repairing…"), while a repair works on it.
   `cache`: name -> the last real row seen (kept between draws; a tunnel that is gone and not being repaired is forgotten). Returns the placeholders. */
const tunGhosts = (list, working, cache) => {
  const here = new Set(list.map(t => t.name)), busy = new Set((working || []).map(w => w && w.name));
  list.forEach(t => { if (t.id > 0) cache.set(t.name, t); });
  [...cache.keys()].forEach(n => { if (!here.has(n) && !busy.has(n)) cache.delete(n); });
  return [...busy].filter(n => n && !here.has(n) && cache.has(n)).map((n, i) => ({...cache.get(n), id: -(1000 + i), state: "starting", last_error: "", rtt_ms: null, rx_mbps: null, tx_mbps: null, bw: null, quality: null, detail: {}, ghost: true}));
};
PAGES.tunnelsList = async () => {
  const [list, trashRes, outageRes, srvAll] = await Promise.all([api("/api/tunnels"), api("/api/tunnel-trash").catch(() => ({items: []})), api("/api/autopilot/outage").catch(() => null), api("/api/servers").catch(() => null)]);
  if (srvAll) state.serversCache = srvAll;
  const nSrv = (srvAll || state.serversCache || []).length;      // a tunnel is built between TWO servers: Create tunnel waits for the second one (the owner, 2026-10-16)
  state.tunnelsCache = list; state.trashCache = trashRes.items || [];
  try { const al = await api("/api/settings/alerts"); state.mutedSet = new Set(al.muted || []); } catch (e) { /* the bell then shows unmuted */ }
  const all = list.concat(tunGhosts(list, (outageRes || {}).working, state.tunRowCache || (state.tunRowCache = new Map())));      // (the list as drawn: the real rows and the placeholders of the tunnels a repair has just removed)
  const tmode = localStorage.getItem("nk.tf") === "problems" ? "problems" : "all", nProblems = all.filter(tunSev).length;
  const workList = (outageRes || {}).working || [], fixing = new Set(workList.filter(w => w.mode !== "look").map(w => w.name)), workMap = new Map(workList.map(w => [w.name, w]));
  if (workList.length) { clearTimeout(window.__workT); window.__workT = setTimeout(() => { if (state.page === "tunnels" && !state.open && !state.build) refresh(); }, 4000); }
  const shortWhy = t => { const e = t.last_error || "", l = (t.detail || {}).loss_pct; return /flaps|lost packets/.test(e) ? "keeps dropping" : /loss/.test(e) ? `${l || ""}% packets lost` : /not listening/.test(e) ? "port closed" : /no data/.test(e) ? "no data" : e.slice(0, 24); };
  const fcount = {}; all.forEach(t => { fcount[t.foreign_name] = (fcount[t.foreign_name] || 0) + 1; });   // how many tunnels end on the same foreign server
  const dupMark = t => fcount[t.foreign_name] > 1 ? `<span class="dup" title="This foreign server ends ${fcount[t.foreign_name]} tunnels (${esc(all.filter(x => x.foreign_name === t.foreign_name).map(x => x.name).join(", "))}). Is that on purpose?">⚠ ×${fcount[t.foreign_name]}</span>` : "";
  const sortType = t => tunType(t.id < 0 && state.tunRowCache && state.tunRowCache.get(t.name) || t);      // (a placeholder of a tunnel that is being built again as ANOTHER type stays in the group it was in; the new row takes its own group when it is there)
  const wrench = t => fixing.has(t.name) ? '<span class="wrench" title="the Tunnel Doctor is working on this tunnel right now">🔧</span>' : "";
  const rows = all.filter(t => tmode === "all" || tunSev(t));
  const newest = new Set([...list].filter(t => t.created_at && Date.now() / 1000 - t.created_at < 1800).sort((x, y) => y.created_at - x.created_at).slice(0, 2).map(t => t.id));      // the faint "new" tag: only the two newest tunnels, and only for 30 minutes
  /* the problem of a cut tunnel in two or three words, in place of the state */
/* what is cut, in two words, as a tiny tag (the owner, 2026-10-16: not a red dot and "GRE link cut" but a small red tag that says WHICH END: "GRE foreign") */
const CUT_TAGS = [[/both servers do not answer/i, "both servers off"], [/the foreign server does not answer/i, "foreign server off"], [/the Iran server does not answer/i, "Iran server off"],
  [/no answer from the foreign end over the GRE link/i, "GRE foreign"], [/GRE interface .* on the Iran server/i, "GRE Iran"], [/^Iran: nikatel-gre-\S+ is /i, "GRE Iran"], [/^foreign: nikatel-gre-\S+ is /i, "GRE foreign"],
  [/^Iran: \S+ is (inactive|failed|dead|unknown)/i, "Iran service"], [/^foreign: \S+ is (inactive|failed|dead|unknown)/i, "foreign service"], [/blocked in front of the server/i, "port blocked"],
  [/not listening on the Iran server/i, "port closed"], [/no data (comes back|passes|moves)|no answer through/i, "no data"], [/flaps|keeps dropping/i, "flapping"], [/GRE/i, "GRE cut"], [/timeout|timed out/i, "no answer"]];
const cutTag = t => { const e = t.last_error || ""; for (const [re, s] of CUT_TAGS) if (re.test(e)) return s; return e.length > 20 ? e.slice(0, 18) + "…" : (e || stText(t).toLowerCase()); };
const stText = t => t.state === "up" ? "ACTIVE" : t.state === "down" ? "DOWN" : t.state === "degraded" ? "UNSTABLE" : t.state === "starting" ? "STARTING" : "UNKNOWN";
  const ports = t => String((t.record || {}).port_mapping || (t.forwarded_ports || []).join(",")).split(",").map(x => x.trim()).filter(Boolean);
  /* the Bandwidth cell (it took the place of the Status column, whose lamp now sits before the name): the number and its bar; while the Doctor works on the tunnel, or the tunnel is cut, what is going on in two or three words */
  const bwCell = t => {      // (a cut tunnel: only the tag that says which end; a tunnel that runs: the capacity | what the users use now, and the bar of how much of it they use - the owner, 2026-10-17)
    if (workMap.has(t.name)) return `<span class="busy-chip" title="the Tunnel Doctor is working on this tunnel"><i class="spin"></i>${workMap.get(t.name).mode === "look" ? "checking" : "repairing"}…</span>`;
    if (!["up", "degraded"].includes(t.state)) return `<span class="cut-tag ${t.state === "down" ? "" : "unk"}" title="${esc(t.last_error || stText(t))}">${esc(t.last_error && t.state !== "starting" ? cutTag(t) : stText(t).toLowerCase())}</span>`;
    const b = t.bw || {}, stall = b.down != null && b.down < BW_STALL, why = t.state === "degraded" && t.last_error && !stall ? `<small class="mut td-err" title="${esc(t.last_error)}">${esc(shortWhy(t))}</small>` : "";
    return `<span class="bwx"><span class="bwr">${bwCap(t)}<i class="bwsep"></i>${bwUse(t)}</span>${bwBar(t)}</span>${why}`;
  };
  /* the Ping / Loss cell after the owner's picture (2026-10-17): the ping above, an arrow at each end of a dotted line, the loss below; a cut link is red with a cross and says nothing above or below */
  const ARR_L = '<svg class="ah" viewBox="0 0 22 14" aria-hidden="true"><path d="M21 7H2M8 1L2 7l6 6"/></svg>', ARR_R = '<svg class="ah" viewBox="0 0 22 14" aria-hidden="true"><path d="M1 7h19M14 1l6 6-6 6"/></svg>';
  const pingCell = t => {
    const cut = t.state === "down" || (!["up", "degraded", "starting"].includes(t.state) && !!t.last_error), l = tunLoss(t);
    return `<div class="tlink ${t.state}${cut ? " cut" : ""}" title="Tunnel ${esc(t.name)}: ${esc(t.iran_name)} ⇄ ${esc(t.foreign_name)} (${esc(stText(t).toLowerCase())})"><b class="ms ${cut ? "" : t.rtt_ms == null ? "mut" : t.rtt_ms > 150 ? "warn" : ""}">${cut ? "" : t.rtt_ms != null ? t.rtt_ms.toFixed(0) + " ms" : "—"}</b><i class="wire">${ARR_L}<u class="dots"></u>${ARR_R}${cut ? '<em class="xx">✕</em>' : ""}</i><small class="loss ${cut ? "" : l.v >= SHOW_LOSS ? "hi" : l.blocked ? "blk" : "ok"}" title="${esc(lossTip(t))}">${cut ? "" : l.v != null ? l.v + "%" : l.blocked ? "no ping" : "—"}</small></div>`;
  };
  const row = t => { const gre = t.type === "backhaul" || t.type === "gre", d = t.detail || {}; return `<tr class="${t.state} ${tunSev(t)}${t.id < 0 ? "" : " clk"}" ${t.id < 0 ? "" : `onclick="toggleDetail(${t.id})" title="Click for the details"`} data-q="${esc([t.name, t.iran_name, t.foreign_name, t.type, t.transport, t.state, (t.iran_host || ""), (t.foreign_host || ""), (t.forwarded_ports || []).join(" "), ...[t.iran_name, t.foreign_name].map(n => { const sv = (state.serversCache || []).find(x => x.name === n) || {}; return [sv.provider, ((sv.extra || {}).geo || {}).provider, ((sv.extra || {}).geo || {}).domain, sv.country].filter(Boolean).join(" "); })].filter(Boolean).join(" ").toLowerCase())}">
      <td class="name"><i class="tun-lamp ${esc(t.state)}" title="${esc(stText(t) + (t.last_error && t.state !== "up" ? " - " + t.last_error : ""))}"></i>${esc(t.name)}${wrench(t)}${newest.has(t.id) ? '<span class="newtag" title="One of the two newest tunnels, built in the last 30 minutes">new</span>' : ""}</td>
      <td><span class="td-type ty-${esc(tunType(t))}">${esc((t.type || "").toUpperCase())}${t.transport ? " · " + esc(t.transport.replace("l3:", "")) : ""}</span><small class="mut"> ${gre ? linkTag(t) : ["direct", "forward"].includes((t.record || {}).direction) ? (t.record || {}).direction : "public"}</small></td>
      <td class="bwc" data-tid="${t.id}">${bwCell(t)}</td>
      <td class="td-node ir"><b>${esc(t.iran_name)}</b><span class="mono mut ip-copy" onclick="event.stopPropagation();copyFrom(this, '${esc(t.iran_host)}')" title="Click to copy the address">${esc(t.iran_host)}</span></td>
      <td class="td-arrow">${pingCell(t)}</td>
      <td class="td-node"><b>${esc(t.foreign_name)}${dupMark(t)}</b><span class="mono mut ip-copy" onclick="event.stopPropagation();copyFrom(this, '${esc(t.foreign_host)}')" title="Click to copy the address">${esc(t.foreign_host)}</span></td>
      <td><div class="td-ports">${ports(t).slice(0, 6).map(p => `<i class="${d.listen && d.listen[parseInt(p)] === false ? "bad" : ""}">${esc(p)}</i>`).join("")}${ports(t).length > 6 ? `<i>+${ports(t).length - 6}</i>` : ""}${ports(t).length ? "" : '<span class="mut">—</span>'}</div></td>
      <td class="tun-act"><div class="td-act">${t.id < 0 ? `<span class="busy-chip" title="the tunnel is being rebuilt"><i class="spin"></i></span>` : typeof labKebab === "function" ? labKebab("tun", t.id, "Actions of " + t.name) : ""}</div></td></tr>`; };      // (the end of a row is the same "⋯" button as in the server list: the actions of the tunnel window - the owner, 2026-10-17)
  const creating = false;
  pageTarget().innerHTML = `<div class="toolbar joined${list.length ? "" : " is-empty"}"><button class="cta cta-tunnel" onclick="openBuild()" ${nSrv >= 2 ? "" : "disabled"} title="${nSrv >= 2 ? "Build a tunnel between an Iran and a foreign server" : "A tunnel is built between two servers: add an Iran server and a foreign server first (Servers)"}"><i class="cta-ic">＋</i><span class="bt">Create tunnel</span></button><button onclick="checkAll()" ${state.tunChecking || !list.length ? "disabled" : ""} title="Checks every tunnel for real - up or down, does data pass - and measures its packet loss and latency. Only shows; nothing is repaired">${state.tunChecking ? '<i class="spin"></i><span class="bt">Checking…</span>' : '<span class="bt">Check all</span>'}</button><button onclick="restartAll()" ${list.length ? "" : "disabled"} title="Restart the tunnel service of every tunnel, four at a time"><span class="bt">Restart all</span></button><span class="tcount"><span class="tc up"><i></i><b>${list.filter(t => t.state === "up").length}</b><span class="lab"> up</span></span><span class="tc down"><i></i><b>${list.filter(t => t.state === "down").length}</b><span class="lab"> down</span></span><span class="tc unstable"><i></i><b>${list.filter(t => t.state === "degraded").length}</b><span class="lab"> unstable</span></span></span><span class="sp"></span><div class="tabs set-tabs tf-tabs"><button class="${tmode === "all" ? "on" : ""}" onclick="tunFilter('all')">All <i class="cnt">${all.length}</i></button><button class="${tmode === "problems" ? "on" : ""}" onclick="tunFilter('problems')">Problems ${nProblems ? `<i class="cnt">${nProblems}</i>` : ""}</button></div>${searchSeg()}</div>
    <div class="lbox tun-box fx-box">${trashBox()}
    ${tmode === "problems" && !rows.length ? '<div class="empty">No tunnel has a problem - every one is up with little packet loss.</div>' : ""}
    ${rows.length ? `<table class="tbl lift tun-tbl"><colgroup><col style="width:13%"><col style="width:14%"><col style="width:10%"><col style="width:9%"><col style="width:11%"><col style="width:11%"><col style="width:8%"><col style="width:56px"></colgroup>
      <thead><tr><th>Tunnel</th><th>Type</th><th class="bwh" data-prio="1" title="Capacity | in use. Capacity (blue): what this tunnel can carry - the download speed of the Doctor's last light test, which cannot be live. In use: what the users use of it now, down and up - the counters of the tunnel's own link on its Iran server, read every few seconds while this page is open. The bar is the part of the capacity they use. A dash: it cannot be read for this tunnel. The lamp before the name is the state of the tunnel."><span class="bwh-in">Bandwidth<em>Mb/s</em></span></th><th class="r">Iran node</th><th class="c" title="Round-trip time / packets lost on the link">ping/loss%</th><th>Foreign node</th><th>Ports</th><th></th></tr></thead>
      <tbody>${[...rows].sort((x, y) => TUN_TYPES.indexOf(sortType(x)) - TUN_TYPES.indexOf(sortType(y)) || String(x.name).localeCompare(String(y.name))).map(row).join("")}</tbody></table>` : '<div class="empty">No tunnel.</div>'}</div>`;      // ONE table for every tunnel of every type: a new type only brings its own tag (no header, no table of its own)
  applyQ(); fxFit(); tunLiveStart();      // (the numbers of what the users use are asked every 4 s from here on)
  const openT = list.find(t => t.id === state.open);
  if (openT) tunModal(openT);
 
  else if (state.build) { const story = $("#build-story"); const keep = story && !story.classList.contains("hidden") ? story.innerHTML : ""; showModal(`<b>＋ Create tunnel</b>`, await buildFormHtml(), "wide"); if (keep) { const s2 = $("#build-story"); s2.classList.remove("hidden"); s2.innerHTML = keep; } }
  else if (!state.keepWin) hideModal();      // (state.keepWin: the box that follows a move stays on the screen while the page behind is drawn again - only its Close button closes it)
  if (state.open) { const ot = (state.tunnelsCache || []).find(x => x.id === state.open); if (ot) tunAfter(ot); }
};
function tunGrid(t) {
  const d = t.detail || {}, u = t.units || {}, rec = t.record || {}, q = t.quality || {}, gre = t.type === "backhaul" || t.type === "gre";
  const kv = (l, v) => `<div class="kv2"><span>${l}</span><div>${v}</div></div>`;
  const box = (icon, title, rows) => `<div class="sbox" data-b="${title.split(" ")[0].toLowerCase()}"><h5><i>${icon}</i>${title}</h5>${rows.join("")}</div>`;
  const chips = (side, names) => (names || []).map(n => { const ok = (d[side + "_units"] || {})[n] === "active", short = /^backhaul/.test(n) ? "backhaul" : /gre/.test(n) ? "GRE" : n.replace(/\.service$/, "");
    return `<span class="tchip" title="${esc(n)}: ${esc((d[side + "_units"] || {})[n] || "?")}"><i class="st ${ok ? "up" : "down"}"></i>${esc(short)}</span>`; }).join("") || "—";
  const err = t.last_error && t.state !== "up" ? ` <small class="err" title="${esc(t.last_error)}">${esc(t.last_error.slice(0, 60))}</small>` : "";
  const servers = box(SI.swap, "Servers", [kv("Iran", `<b>${esc(t.iran_name)}</b> <small class="mut mono">${esc(t.iran_host)}</small>`), kv("Foreign", `<b>${esc(t.foreign_name)}</b> <small class="mut mono">${esc(t.foreign_host)}</small>`),
    kv("Type", `${esc((t.type || "").toUpperCase())}${t.transport ? " · " + esc(t.transport.replace("l3:", "")) : ""}`), kv("Direction", esc(rec.direction || "reverse")),
    kv("Ports", `<div class="td-ports">${(t.forwarded_ports || []).map(p => `<i class="${d.listen && d.listen[p] === false ? "bad" : ""}">${p}</i>`).join("") || "—"}</div>`), kv("Control port", t.port || "—")]);
  const status = box(SI.clock, "Status", [kv("State", `<span class="st ${t.state}">${esc(stName(t.state))}</span>${err}`), kv("Since", t.state_since ? ago(t.state_since) + " ago" : "—"), kv("Checked", when(t.last_check_at)), kv("Created", when(t.created_at)),
    kv("In use now", useOf(t) ? `↓ ${bwFmt(useOf(t).down)} ↑ ${bwFmt(useOf(t).up)}${bwUtil(t) != null ? ` <small class="mut">${Math.round(bwUtil(t) * 100)} % of the capacity</small>` : ""}` : '<span class="mut">cannot be read for this tunnel</span>'),
    kv("Score", q.score != null ? `<b class="${q.score >= 70 ? "gw-ok" : q.score >= 40 ? "warn" : "err"}">${q.score} / 100</b> <small class="mut">${q.at ? ago(q.at) + " ago" : ""}</small>` : '<span class="mut">not measured</span>')]);
  const link = box(SI.link, "Link", [kv("Latency", t.rtt_ms != null ? `${t.rtt_ms.toFixed(0)} ms${q.jitter_ms != null ? ` <small class="mut">jitter ${q.jitter_ms} ms</small>` : ""}` : "—"), kv("Packet loss", `<span title="${esc(lossTip(t))}">${tunLoss(t).v != null ? tunLoss(t).v + " %" : tunLoss(t).blocked ? "ping blocked <small class=\"mut\">(the data passes)</small>" : "—"}</span>`),
    kv("Speed", q.down_mbps != null ? `↓ ${q.down_mbps} ↑ ${q.up_mbps} Mb/s` : "—"),
    kv("Capacity", ((t.bw || {}).down != null ?`<b class="${t.bw.state === "drop" ? "err" : ["low", "slow"].includes(t.bw.state) ? "warn" : ""}" title="${esc(bwTip(t))}">${bwFmt(t.bw.down)}</b> <small class="mut">usually ${t.bw.ref ? bwFmt(t.bw.ref) : "…"} · ${ago(t.bw.at)} ago</small>${["drop", "slow"].includes(t.bw.state) ? `<div class="bw-why">${esc(bwWhy(t))}</div>` : ""}` : '<span class="mut">not measured yet</span>') + ` <a class="mini" onclick="bwMeasure(${t.id})" title="Two light connections (120 MB at most) over the link; a tunnel with a test door: its quality test">measure now</a>`),
    kv(gre ? "Link" : "Engine", gre ? (t.iran_tunnel_ip ? `${linkTag(t)} <span class="mono">${esc(t.iran_tunnel_ip)} ⇄ ${esc(t.foreign_tunnel_ip)}</span>${(t.record || {}).link_port ? ` <small class="mut">UDP ${esc((t.record || {}).link_port)}</small>` : ""}` : "—") : esc(rec.transport_label || t.type)), kv("Iran side", chips("iran", u.iran)), kv("Foreign side", chips("foreign", u.foreign))]);
  return `<div class="sgrid">${servers}${status}${link}</div>`;
}
/* the "times the tunnel was down" box, in plain words: when, how long, and why (the technical message stays in the tooltip) */
const outWhen = ts => { const d = new Date(ts * 1000); return d.toLocaleDateString(undefined, {month: "short", day: "numeric"}) + ", " + d.toLocaleTimeString(undefined, {hour: "2-digit", minute: "2-digit", hour12: false}); };
const OUT_WHY = [[/both servers do not answer/i, "Both servers did not answer"], [/foreign server does not answer/i, "The foreign server did not answer"], [/iran server does not answer/i, "The Iran server did not answer"], [/GRE/i, "The link between the servers was cut"], [/loss/i, "Many packets were lost"], [/not active|inactive|failed/i, "The tunnel service had stopped"], [/no data passes|no answer|timeout|timed out/i, "No data passed through the tunnel"]];
const outWhy = e => { e = String(e || ""); for (const [re, s] of OUT_WHY) if (re.test(e)) return s; return e ? e.slice(0, 60) : "No answer from the tunnel"; };
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
  const chartsHtml = `<div class="mhead">${rangeTabs()}<small class="mut">${rows.length} checks · one every minute · hover a chart for exact values</small></div>
    <div class="mtiles">${mtile("Uptime", upPct == null ? "—" : f1(upPct) + "%", `${up} of ${rows.length}`, upPct != null && upPct < 95 ? "bad" : upPct != null && upPct < 99.5 ? "warn" : "ok")}${mtile("Outages", out.length, out.length ? "down " + durText(down) : "none", out.length ? "warn" : "ok")}${mtile("Latency", rtt.length ? Math.round(avgOf(rtt)) + " ms" : "—", rtt.length ? "best " + Math.round(Math.min(...rtt)) : "")}${mtile("Traffic", "↓" + f1(avgOf(rx)) + " ↑" + f1(avgOf(tx)), "Mb/s avg")}</div>
    <div class="mcard mwide"><h6>Up / down timeline</h6>${strip(oks)}</div>
    <div class="mgrid">${chartCard("Latency (RTT)", series("rtt_ms"), {col: "blue", ic: SI.pulse, unit: " ms", warn: 150, bad: 300, fmt: v => Math.round(v)})}${chartCard("Download ↓", series("rx_mbps"), {col: "green", ic: SI.down, unit: " Mb/s"})}${chartCard("Upload ↑", series("tx_mbps"), {col: "orange", ic: SI.arrowUp, unit: " Mb/s"})}
      <div class="mcard mout"><h6>Times the tunnel was down <span class="mval">${out.length === 1 ? "1 time" : out.length + " times"}</span></h6>${out.length ? `<div class="mout-h"><span>When</span><span>How long</span><span>Why</span></div><div class="mout-list">` + out.slice(-30).reverse().map(o => `<div class="mout-r${o.open ? " now" : ""}" title="${esc(o.err || "")}"><time>${outWhen(o.from)}</time><b>${o.open ? "still down" : durText(o.to - o.from)}</b><span>${esc(outWhy(o.err))}</span></div>`).join("") + "</div>" : '<div class="mut chart-empty">The tunnel did not go down in this time range.</div>'}</div></div>`;
  morphHtml(box, chartsHtml);
  state.chartCache = state.chartCache || {}; state.chartCache["t" + id + ":" + state.mrange] = box.innerHTML;
}/* Create tunnel: five engines, each with its transports and options; the form keeps its values when the engine changes */
const bk = {kinds: null, kind: localStorage.getItem("nk.bk.kind") || "backhaul", transport: "", v: {}, guide: true};
const BK_ICON = {auto: "★", backhaul: "⇄", backpack: "🎒", backpack_direct: "🧭", paqet: "📦", chisel: "⛏"};
const BK_AUTO = {key: "auto", title: "Automatic", transports: [{key: "best", label: "Best of the day"}]};      // (the owner, 2026-10-19: "a tunnel mode that puts the best tunnel of the day on it": the panel picks the type - knowledge.best_kind - when Build is pressed)
/* what to pick, and why - the defaults below every engine are already the many-users settings (mux, 8 mux channels, 16-24 pool, keepalive 75 s, heartbeat 20 s, BBR + fq on both kernels) */
window.bkPick = (k, t) => { bk.kind = k; bk.transport = t; localStorage.setItem("nk.bk.kind", k); };
const BK_GUIDE = [
  ["⭐ Many users, clean route", "Backhaul over GRE · TCP Mux", "backhaul", "tcpmux", "hundreds of users over a few connections; the GRE link hides the real traffic. The default for every tunnel in this panel."],
  ["GRE filtered between the two servers", "Backhaul · TCP Mux over WireGuard", "backhaul", "wg:tcpmux", "the same Backhaul, but over an encrypted WireGuard link on one UDP port instead of GRE: it passes where protocol 47 is dropped and the kernel carries it. (GRE over UDP is the same without encryption.) Needs wireguard-tools on both servers: building it from here installs it."],
  ["GRE filtered, UDP also blocked", "BackPack · TCP Mux", "backpack", "tcpmux", "same multiplexing as backhaul, but over a plain TCP control port - works where GRE and UDP are dropped."],
  ["Heavy filtering / DPI", "BackPack · TCP + Stealth or WebSocket TLS Mux", "backpack", "stealth", "Stealth looks like random bytes; WSS Mux looks like HTTPS and can sit behind a CDN."],
  ["Voice, games, low latency", "BackPack Direct · UDP or QUIC", "backpack_direct", "udp", "an L3 link the Iran side dials; no TCP-over-TCP, lowest latency - UDP must be open."],
  ["TCP throttled, a whitelisted domain exists", "BackPack Direct · PCK / SNI spoofing", "backpack_direct", "sni", "raw TCP frames that look like a normal flow; SNI spoofing opens with a FAKE TLS hello that names the allowed domain - a filter that only reads the name lets it pass (the rest is not TLS)."],
  ["Traffic is counted or blocked by source address", "BackPack Direct · IP spoofing", "backpack_direct", "spoof", "raw packets whose SOURCE address you choose (udp, icmp, tcp, ipip, gre ... profiles). Works only where the provider lets forged sources out: Servers → Tests → Spoof test says whether it does. Build it unforged first, then set the forged address."],
  ["Provider stalls ordinary TCP", "Paqet · KCP over raw sockets", "paqet", "kcp", "one KCP session in raw packets, aes-128-gcm; good on lossy routes, needs raw sockets on both sides."],
  ["Small group, simplest setup", "Chisel · TLS WebSocket", "chisel", "wss", "one HTTPS port on the Iran server, key pinned; fine for tens of users, CDN-able."],
  ["Customers' downloads are slow", "Chisel · Forward WebSocket", "chisel", "fws", "the Iran server dials the foreign one. On the route we measured, the data that flows away from the side that opened the connection is slowed: all customers of a reverse tunnel together got 20 - 130 Mb/s down, a forward one 400 - 730. Test your pair first."],
  ["Everything else is blocked", "BackPack · xDi (ICMP)", "backpack", "xdi", "KCP inside ping packets; slow, last resort."],
];
const SPOOF_PROFILES = [["udp", "plain datagrams · the default"], ["icmp", "for paths that filter UDP"], ["tcp", "looks like a TCP flow"], ["icmpv6", "ICMPv6 echo in an IPv4 packet"], ["proto58", "bare protocol 58"], ["ipip", "IP-in-IP (protocol 4)"], ["gre", "GRE (protocol 47)"]];      // (the packet profiles of BackPack's IP spoofing carrier: docs/ip-spoofing.md of the project)
/* two or three words per type, shown in the drop-down */
const BK_FOR = {"auto:best": "the type that connects best between these two servers today", "backhaul:tcpmux": "many users, normal route ⭐", "backhaul:tcp": "few users, plain route", "backhaul:wsmux": "behind a CDN, clear", "backhaul:wssmux": "behind a CDN, TLS",
  "backhaul:wg:tcpmux": "GRE filtered, UDP open, encrypted", "backhaul:wg:tcp": "GRE filtered, UDP open, few users", "backhaul:fou:tcpmux": "GRE filtered, UDP open, no encryption", "backhaul:gue:tcpmux": "GRE filtered, UDP open, GUE shape", "backhaul:ipip:tcpmux": "GRE filtered, the lightest link", "backhaul:ipipfou:tcpmux": "GRE filtered, IPIP in UDP", "backhaul:ipipgue:tcpmux": "GRE filtered, IPIP in UDP (GUE)", "backhaul:ipsec:tcpmux": "GRE filtered, ESP open, encrypted", "backhaul:ipsecnat:tcpmux": "GRE filtered, UDP open, IPsec",
  "backpack:tcpmux": "many users, GRE blocked", "backpack:tcp": "few users, GRE blocked", "backpack:stealth": "very heavy filtering", "backpack:pck": "Iran stalls plain TCP (root)", "backpack:kcp": "lossy route, games", "backpack:udp": "UDP-only services", "backpack:quic": "UDP open, wants QUIC", "backpack:ws": "behind your own TLS", "backpack:wsmux": "WebSocket + many users", "backpack:wss": "must look like HTTPS", "backpack:wssmux": "heavy filtering + many users", "backpack:xdi": "Iran blocks almost everything",
  "backpack_direct:udp": "low latency, voice, games", "backpack_direct:quic": "low latency, UDP open", "backpack_direct:pck": "Iran throttles TCP (root)", "backpack_direct:sni": "fake TLS hello: only a domain passes (root)", "backpack_direct:spoof": "forged source address (root)", "backpack_direct:xdi": "UDP and TCP both blocked",
  "paqet:kcp": "ordinary TCP stalls, raw sockets", "chisel:ws": "small group, simplest", "chisel:wss": "small group, looks like HTTPS",
  "chisel:fws": "Iran dials: fast downloads", "chisel:fwss": "Iran dials: fast downloads, TLS"};
async function buildFormHtml() {
  const [servers, kd, av, dacc] = await Promise.all([api("/api/servers"), bk.kinds ? Promise.resolve({kinds: bk.kinds}) : api("/api/tunnels/kinds"), api("/api/settings/arvan").catch(() => ({})), api("/api/dns/accounts").catch(() => ({accounts: []}))]);
  bk.kinds = kd.kinds.some(k => k.key === "auto") ? kd.kinds : [BK_AUTO, ...kd.kinds]; bk.arvan = ARVAN_UI && (!!av.has_api_key || (dacc.accounts || []).length > 0); bk.arvanMode = av.mode || "off";      // the DNS names can be in any DNS account (ArvanCloud or Cloudflare)
  const K = bk.kinds.find(k => k.key === bk.kind) || bk.kinds[0]; bk.kind = K.key;
  const tr = K.transports.find(t => t.key === bk.transport) || K.transports[0]; bk.transport = tr.key;
  const srvOpt = role => servers.filter(s => s.role === role && s.enabled);
  void srvOpt;
  const v = bk.v, val = (n, d) => esc(v[n] != null ? v[n] : d);
  const opt = (role, name) => servers.filter(s => s.role === role).map(s => `<option value="${s.id}" ${s.enabled ? "" : "disabled"} ${s.enabled && String(s.id) === String(v[name]) ? "selected" : ""}>${esc(s.name)}${isNew(s) ? " · new" : ""} — ${esc(s.host)}${!s.enabled ? " (disabled: enable it in Servers → Edit)" : s.online === false ? " (offline)" : ""}</option>`).join("");
  const num = (name, label, d, min, max, small) => `<label>${label}${small ? ` <small>${small}</small>` : ""}<input name="${name}" type="number" min="${min}" max="${max}" value="${val(name, d)}"></label>`;
  const chk = (name, label, small, dflt = false) => `<label class="chk"><input name="${name}" type="checkbox" ${(name in bk.chk ? bk.chk[name] : dflt) ? "checked" : ""}> ${label}${small ? ` <small class="mut">${small}</small>` : ""}</label>`;
  const nodelay = chk("nodelay", "TCP no-delay", "lower latency for small packets", true);
  const sel = (name, label, list, dflt) => `<label>${label}<select name="${name}" data-dd>${list.map(p => `<option ${val(name, dflt) === p ? "selected" : ""}>${p}</option>`).join("")}</select></label>`;
  const muxed = BP_MUXED.includes(tr.key.split(":").pop());          // ("wg:tcpmux" is TCP Mux over WireGuard)
  let options = "";
  if (K.key === "auto") options = '<small class="mut">Best of the day is built with the default settings of the type it picks: the one that connects best between these two servers - what the Doctor measured on this pair, a fresh full measurement first, then the score. Nothing measured yet: the first choice of the Doctor for a new pair. Pick the Iran server first (the smart choice cannot know the pair).</small>'; else if (K.key === "backhaul") options = num("mtu", "GRE MTU", 0, 0, 1476, "(0 = measure)") + num("keepalive", "Keepalive (s)", 75, 10, 600) + num("heartbeat", "Heartbeat (s)", 20, 5, 120) + num("connection_pool", "Connection pool", muxed ? 24 : 32, 1, 256, "(client connections kept ready)") + (muxed ? num("mux_con", "Mux channels", 8, 1, 64) : "") + num("channel_size", "Channel size", 8192, 1024, 65536, "(buffer)") + num("dial_timeout", "Dial timeout (s)", 10, 3, 60) + num("retry_interval", "Retry interval (s)", 2, 1, 30) + sel("log_level", "Log level", ["warn", "error", "info", "debug"], "warn") + nodelay + chk("aggressive_pool", "Aggressive pool", "reconnect lost pool connections at once") + chk("tune", "Tune the kernel", "BBR + fq + big buffers on both servers", true);
  else if (K.key === "backpack") options = `<label>Preset<select name="preset" data-dd>${["turbo", "balanced", "stable"].map(p => `<option ${val("preset", "turbo") === p ? "selected" : ""}>${p}</option>`).join("")}</select></label>` + num("keepalive", "Keepalive (s)", 75, 10, 600) + num("heartbeat", "Heartbeat (s)", 20, 5, 120) + num("connection_pool", "Connection pool", BP_MUXED.includes(tr.key) ? 16 : 24, 1, 256, "(client connections kept ready)") + (BP_MUXED.includes(tr.key) ? num("mux_con", "Mux channels", 8, 1, 64) : "") + num("channel_size", "Channel size", 8192, 1024, 65536, "(buffer)") + nodelay + chk("accept_udp", "Forward UDP too", "the same ports over UDP");
  else if (K.key === "backpack_direct") options = (tr.key === "sni" ? `<label>Fake SNI <small>(the domain the fake TLS hello names: one your route allows)</small><input name="sni_domain" value="${val("sni_domain", "")}" placeholder="cdn.example.com" required></label>` : "")
    + (tr.key === "spoof" ? `<label>Packet profile <small>(both ends use it)</small><select name="spoof_profile" data-dd>${SPOOF_PROFILES.map(([p, n]) => `<option value="${p}" ${val("spoof_profile", "udp") === p ? "selected" : ""}>${p} — ${n}</option>`).join("")}</select></label><label>Forged source IPv4 <small>(empty = do not forge: the right first build - test the path, then set it)</small><input name="spoof_src_ip" value="${val("spoof_src_ip", "")}" placeholder="10.10.10.10" autocomplete="off"></label><label>Network interface <small>(only for a server with several)</small><input name="spoof_interface" value="${val("spoof_interface", "")}" placeholder="eth0" autocomplete="off"></label>` : "")
    + (tr.key === "pck" || tr.key === "sni" ? num("mtu", "Carrier MTU", 1380, 1000, 1500) : "") + chk("accept_udp", "Forward UDP too");
  else if (K.key === "paqet") options = `<label>KCP mode<select name="kcp_mode" data-dd>${["fast", "normal", "fast2", "fast3"].map(p => `<option ${val("kcp_mode", "fast") === p ? "selected" : ""}>${p}</option>`).join("")}</select></label>` + num("conn", "Connections", 4, 1, 32) + num("mtu", "KCP MTU", 1350, 500, 1400, "(1450 and more carry nothing)") + num("window", "Window (packets)", 4096, 128, 65535, "(the long way round needs a big one)") + num("buffer_mb", "Buffer (MB)", 8, 1, 128, "(of one stream)") + chk("accept_udp", "Forward UDP too");
  else options = chk("accept_udp", "Forward UDP too", "the same ports over UDP");
  const typeSel = `<select name="ttype" data-dd data-ddw="520" onchange="bkType(this.value)">${bk.kinds.map(k => `<optgroup label="${esc(k.title)}">${k.transports.map(t => `<option value="${k.key}|${t.key}" ${k.key === K.key && t.key === tr.key ? "selected" : ""}>${esc(k.title)} · ${esc(t.label)} — ${esc(BK_FOR[k.key + ":" + t.key] || BK_FOR[k.key] || "")}</option>`).join("")}</optgroup>`).join("")}</select>`;
  const fl = (label, inner) => `<label class="kv2 kf bf"><span>${label}</span>${inner}</label>`;
  const forHint = BK_FOR[K.key + ":" + tr.key] || "";
  return `<form class="frm build" onsubmit="return startBuild(event)" oninput="bkKeep(this)">
    <div class="sgrid bld-grid">
      <div class="sbox"><h5><i>🖧</i>Servers</h5>
        ${fl(`Iran <a class="bf-auto" onclick="autoIran(this.closest('form'))" title="Ping the foreign server from every Iran server, look at load and how many tunnels each one carries, pick the best">auto</a>`, `<select name="iran_id" required data-dd data-ddw="340" onchange="bkCheck(this.closest('form'))"><optgroup label="Automatic"><option value="0" ${String(v.iran_id) === "0" ? "selected" : ""}>✨ Smart — the best one</option></optgroup>${opt("iran", "iran_id") ? `<optgroup label="Iran servers">${opt("iran", "iran_id")}</optgroup>` : ""}</select>`)}
        ${fl("Foreign", `<select name="foreign_id" required data-dd data-ddw="340" onchange="bkCheck(this.closest('form'))">${opt("foreign", "foreign_id") ? `<optgroup label="Foreign servers">${opt("foreign", "foreign_id")}</optgroup>` : ""}</select>`)}
        <div id="auto-iran" class="mut bf-note"></div></div>
      <div class="sbox"><h5><i>⇄</i>Tunnel</h5>
        ${fl("Type", typeSel)}
        ${fl("Ports", `<input name="ports" required placeholder="443, 443=2096, 10000-10009" title="Ports to forward: 443, 443=2096, 10000-10009" value="${val("ports", "")}">`)}
        <div class="bf-note mut">${esc(forHint) || "the defaults are the many-users settings"}</div></div>
      <div class="sbox bld-check"><h5><i>🛡</i>Check</h5><div id="build-check" class="bcheck"></div></div>
    </div>
    ${bk.arvan ? `<div class="bdns"><label class="chk"><input type="checkbox" name="dns_set" ${bk.chk.dns_set ? "checked" : ""} onchange="bkDns(this)"> After the build, point these DNS names at the new Iran server <small class="mut">(any of your DNS accounts: ArvanCloud or Cloudflare · comma separated; pick from the list)</small></label><div class="sugg-wrap"><input name="dns_names" class="${bk.chk.dns_set ? "" : "hidden"}" value="${val("dns_names", "")}" placeholder="ca.example.ir, ca2.example.ir" autocomplete="off" oninput="sugShow(this)" onfocus="sugShow(this)" onblur="sugHide(this)" onkeydown="sugKey(event, this)"><div class="sugg hidden"></div></div></div>` : ""}
    <div class="sbox badv"><h5 class="adv-h" onclick="bkAdv()"><i>⚙</i>Advanced settings<small>${bk.adv ? "change a value only when you measured a reason" : "keepalive, heartbeat, MTU, preset, UDP…"}</small><span class="sp"></span><em>${bk.adv ? "▾" : "▸"}</em></h5>${bk.adv ? `<div class="adv-grid">${options}</div>` : ""}</div>
    <div class="sbar bld-foot"><button class="pri" type="submit" ${bk.run ? "disabled" : ""}>Build</button><button type="button" onclick="startTest(event)" ${bk.run ? "disabled" : ""} title="Build a temporary tunnel of this kind between these two servers, measure it, remove it">Test this pair first</button><button type="button" class="danger ${bk.run ? "" : "hidden"}" id="bld-stop" onclick="stopBuild()" title="Stop now: what was made so far is removed again">■ Stop</button><span class="sp"></span><small class="mut">Every default is the many-users setting; a test builds a temporary tunnel, measures it and removes it.</small></div>
  </form><div id="build-story" class="fix bld-log"></div>`;
}
/* the Create window redraws only its own body: no refresh of the page behind it, the typed values stay */
async function bkRedraw() { const body = $("#modal .modal-body"); if (!body) return refresh(); const form = $("#modal form.build"); if (form) bkKeep(form); const story = $("#build-story"), keep = story && !story.classList.contains("hidden") ? story.innerHTML : ""; body.innerHTML = await buildFormHtml(); { const fm = $("#modal form.build"); if (fm) bkCheck(fm, true); } if (keep) { const s2 = $("#build-story"); s2.classList.remove("hidden"); s2.innerHTML = keep; } }
window.bkAdv = () => { bk.adv = !bk.adv; bkRedraw(); };
/* the A records of the ArvanCloud account, for the name lists (fetched once per page load, the server keeps them for five minutes) */
/* the DNS names to pick: a list of our own under the field (the name and its address), filtered while you type, chosen with the mouse or the arrow keys; the field takes several names, comma separated */
async function dnsRowsLoad() { if (!bk.dnsRows) { try { const d = await api("/api/dns/records"), accs = Object.fromEntries((d.accounts || []).map(a => [a.id, a])); bk.dnsRows = (d.records || []).filter(r => r.type === "A").map(r => ({...r, _prov: (accs[r.account] || {}).provider || "arvan", _acc: (accs[r.account] || {}).name || ""})); } catch (e) { bk.dnsRows = []; toast("✕ " + e.message, "bad"); } } return bk.dnsRows; }
const sugBox = inp => inp.parentElement.querySelector(".sugg");
window.sugShow = async inp => {
  const box = sugBox(inp); if (!box) return; const rows = await dnsRowsLoad(), one = inp.dataset.sug === "one";
  const parts = inp.value.split(","), cur = (one ? inp.value : parts[parts.length - 1]).trim().toLowerCase(), have = new Set(parts.map(x => x.trim().toLowerCase()));
  const chosen = !one && parts.some(x => x.trim() && x.trim() !== inp.value.split(",").pop().trim());      // nothing is shown until something is typed; once a name is chosen the list stays, to add more
  if (!cur && !chosen) { box.classList.add("hidden"); box.innerHTML = ""; return; }
  const list = rows.filter(r => r.type === undefined || r.type === "A").filter(r => (one || !have.has(r.fqdn.toLowerCase())) && (!cur || nkHit(r.fqdn + " " + (r.ips || []).join(" ") + " " + (r._acc || "") + " " + (r._prov || ""), cur))).slice(0, 80);      // the search finds a part of the name, the address or the account, separators do not matter
  box.innerHTML = list.map((r, i) => `<div class="sg-i ${i === 0 ? "on" : ""}" data-v="${esc(r.fqdn)}" onmousedown="event.preventDefault(); sugPick(this)"><i class="pv pv-${esc(r._prov || "arvan")}"></i><b>${esc(r.fqdn)}</b><small class="mono">${esc((r.ips || []).join(", "))}</small><small class="sg-acc">${esc(r._acc || "")}</small></div>`).join("") || (cur ? `<div class="sg-none">No record of this name</div>` : "");
  box.classList.toggle("hidden", !box.innerHTML);
};
window.sugHide = inp => setTimeout(() => { const b = sugBox(inp); if (b) b.classList.add("hidden"); }, 120);
window.sugPick = el => {
  const box = el.parentElement, inp = box.parentElement.querySelector("input"), one = inp.dataset.sug === "one";
  if (one) inp.value = el.dataset.v; else { const parts = inp.value.split(",").map(x => x.trim()).filter(Boolean); if (inp.value.trim() && !/,\s*$/.test(inp.value)) parts.pop(); parts.push(el.dataset.v); inp.value = parts.join(", ") + ", "; }
  inp.focus(); if (one) box.classList.add("hidden"); else sugShow(inp);      // for several names the list stays open: pick the next one at once
};
window.sugKey = (e, inp) => {
  const box = sugBox(inp); if (!box || box.classList.contains("hidden")) return; const items = [...box.querySelectorAll(".sg-i")]; if (!items.length) return;
  let i = items.findIndex(x => x.classList.contains("on"));
  if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); items[i]?.classList.remove("on"); i = (i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length; items[i].classList.add("on"); items[i].scrollIntoView({block: "nearest"}); }
  else if (e.key === "Enter" && i >= 0) { e.preventDefault(); sugPick(items[i]); }
  else if (e.key === "Escape") box.classList.add("hidden");
};
window.bkDns = cb => { bk.chk.dns_set = cb.checked; const inp = cb.closest(".bdns").querySelector("input[name=dns_names]"); inp.classList.toggle("hidden", !cb.checked); if (cb.checked) inp.focus(); };
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
  const inConf = new Set(c.conflicts.map(t => t.id));      // a tunnel already offered below (same foreign server) is not offered twice
  const busy = c.busy.filter(b => !(b.id && inConf.has(b.id))).map(b => b.id
    ? `<div class="bconf"><label class="chk"><input type="checkbox" name="replace_${b.id}" ${bk.chk["replace_" + b.id] ? "checked" : ""} onchange="bkBtn(this.closest('form'))"> ⚠ Port ${b.ports.join(", ")} is used on ${esc(c.iran)} by ${esc(b.tunnel)} (${esc(b.state)}) — replace it: it is removed first${(b.all_ports || []).length > b.ports.length ? ` (its other ports ${b.all_ports.filter(x => !b.ports.includes(x)).join(", ")} stop too)` : ""}, restorable for 30 days from <i>Recently removed</i></label></div>`
    : `<div class="err">⛔ Port ${b.port} is already used on ${esc(c.iran)} by ${esc(b.by + (b.state ? ", " + b.state : ""))}. Choose another Iran server (or ✨ Smart) or another port.</div>`).join("");
  const conf = c.conflicts.map(t => `<div class="bconf"><label class="chk"><input type="checkbox" name="replace_${t.id}" ${t.replaceable ? "checked" : ""}> ⚠ Port ${t.ports.join(", ")} already goes to this foreign server through ${esc(t.name)} (${esc(t.iran)}, ${esc(stName(t.state))}${t.state !== "up" && t.since ? " for " + ago(t.since) : ""}) — remove it first and replace it</label></div>`).join("");
  const dns = c.conflicts.length && bk.arvan ? `<label class="chk"><input type="checkbox" name="dns_follow" checked> then point the CDN records of the replaced tunnel at the new Iran server <small class="mut">${bk.arvanMode === "auto" ? "(automatic)" : "(asks first on Tunnels → CDN)"}</small></label>` : "";
  const stale = (c.stale || []).map(s => `<div class="bconf"><label class="chk"><input type="checkbox" name="stale_${esc(s.unit)}" checked> ⚠ Port ${s.port} on ${esc(c.iran)} is held by an old tunnel (${esc(s.unit)}): ${esc(s.why)} — remove it and use the port</label></div>`).join("");
  box.innerHTML = busy + stale + conf + dns + (c.error ? `<div class="mut">${esc(c.error)}</div>` : "");
  bkBtn(form);
}
/* the Build button waits while a busy port has no answer: a process we cannot replace, or a tunnel whose replace tick is off */
const bkBlocked = form => (bk.ck ? bk.ck.busy : []).filter(b => !(b.id && form.querySelector(`input[name=replace_${b.id}]:checked`)));
window.bkBtn = form => { const btn = form && form.querySelector("button.pri"); if (btn) btn.disabled = bk.ck ? !!bkBlocked(form).length : false; };
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
  else for (const k of ["preset", "keepalive", "heartbeat", "sni_domain", "spoof_profile", "spoof_src_ip", "spoof_interface", "mtu", "kcp_mode", "conn", "connection_pool", "mux_con", "channel_size"]) if (f.get(k) != null && f.get(k) !== "") body.opts[k] = isNaN(+f.get(k)) ? f.get(k) : +f.get(k);
  if (bk.kind === "backpack") body.opts.nodelay = $("#modal [name=nodelay]") ? f.get("nodelay") === "on" : bk.chk.nodelay !== false;
  if (bk.kind === "paqet") {      // one window for both directions and one buffer size for a stream (the SMUX buffer is twice that)
    const w = +f.get("window"), mb = +f.get("buffer_mb");
    if (w) body.opts.rcvwnd = body.opts.sndwnd = w;
    if (mb) { body.opts.streambuf = mb * 1048576; body.opts.smuxbuf = mb * 2097152; }
  }
  if (f.get("accept_udp") === "on") body.opts.accept_udp = true;
  return body;
}
window.autoIran = async form => { const f = new FormData(form), box = $("#auto-iran"); box.innerHTML = '<div class="fx-line run"><i></i>Pinging the foreign server from every Iran server…</div>'; try { const r = await api("/api/tunnels/auto-iran", {method: "POST", body: JSON.stringify({foreign_id: +f.get("foreign_id"), ports: f.get("ports") || ""})}); const best = r.ranking.find(x => x.score > 0); if (best) form.querySelector("[name=iran_id]").value = best.id; box.innerHTML = `<div class="ob-inbounds">${r.ranking.map((x, i) => `<span><b class="${x.score >= 70 ? "gw-ok" : x.score ? "" : "err"}">${i + 1}. ${esc(x.name)} · ${x.score}</b> <small class="mut">${esc(x.reason)}</small></span>`).join("")}</div>${best ? `<small>Chosen: <b>${esc(best.name)}</b> — change it above if you prefer another.</small>` : '<small class="err">No Iran server can carry this tunnel right now.</small>'}`; } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; } };
/* while a build or a test runs the buttons are off and Stop is there; Stop removes what was made so far */
window.bkRun = run => { bk.run = run; const st = $("#bld-stop"); if (st) st.classList.toggle("hidden", !run); document.querySelectorAll("#modal .bld-foot button:not(#bld-stop)").forEach(b => { b.disabled = !!run; }); };
window.stopBuild = async () => { const run = bk.run; if (!run) return; try { await api(run.type === "build" ? `/api/tunnels/builds/${run.id}/stop` : `/api/tunnel-test/compare/${run.id}/stop`, {method: "POST"}); toast("Stopping: what was made so far is removed again", "info"); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* a tunnel that is being built: a click on its card brings the Create window back with the story of the build so far, and the story goes on in it */
window.openBuilding = async t => {
  let id = bk.run && bk.run.type === "build" ? bk.run.id : 0;
  if (!id) { try { const a = (await api("/api/actions?limit=10&kinds=build,create")).actions.find(x => !x.done); id = a ? a.id : 0; } catch (e) { id = 0; } }
  if (!id) return false;
  state.build = true; state.open = null; state.edit = null; await refresh();
  const box = $("#build-story"); if (!box) return false;
  bkRun({type: "build", id}); followStory(id, box, 600).finally(() => bkRun(null)); return true;
};
window.startTest = async e => {
  const form = e.target.closest("form"), f = new FormData(form);
  const trn = bk.transport || f.get("transport") || "tcpmux";
  const kind = bk.kind === "backhaul" ? trn : bk.kind === "backpack" ? "bp:" + trn : bk.kind === "backpack_direct" ? "bpd:" + trn : bk.kind === "paqet" ? "paqet" : ({wss: "chisel:tls", fws: "chisel:fwd", fwss: "chisel:fwdtls"}[trn] || "chisel");
  const box = $("#build-story"); box.classList.remove("hidden");
  if (bk.kind === "auto") { box.innerHTML = '<div class="fx-line plan"><b>›</b>Best of the day is chosen when you press Build; to test one type between these two servers, pick that type.</div>'; return; }
  let iranId = +f.get("iran_id") || 0;
  try {
    if (!iranId) {   // Smart: the Iran servers are tested one after the other, best first; a poor one is left for the next
      box.innerHTML = '<div class="fx-line run"><i></i>Smart test: starting…</div>';
      const r = await api("/api/tunnels/smart-test", {method: "POST", body: JSON.stringify({foreign_id: +f.get("foreign_id"), ports: f.get("ports") || "", kind})});
      const s = await followStory(r.id, box, 900);
      const win = s && (s.story || []).slice().reverse().find(l => l.startsWith("Best server: "));
      if (win) { const name = win.replace("Best server: ", ""), sel = form.querySelector("[name=iran_id]"), opt = [...sel.options].find(o => o.textContent.trim().startsWith(name + " ")); if (opt) { sel.value = opt.value; bkKeep(form); } }
      return;
    }
    box.innerHTML = '<div class="fx-line run"><i></i>Building a temporary tunnel…</div>';
    const r = await api("/api/tunnel-test/compare", {method: "POST", body: JSON.stringify({iran_id: iranId, foreign_id: +f.get("foreign_id"), kinds: [kind]})}); tt.job = {id: r.id, kinds: [kind], results: [], current: kind, done: false}; bkRun({type: "test", id: r.id});
    box.innerHTML = '<div class="fx-line run"><i></i>Testing this pair: building a temporary tunnel, measuring it, removing it…</div>';   // the window stays here: the progress and the result are shown in it
    for (let i = 0; i < 400; i++) {
      await new Promise(res => setTimeout(res, 3000));
      if (!$("#build-story")) return;                       // the window was closed
      const j = await api(`/api/tunnel-test/compare/${r.id}`); tt.job = j;
      const lines = (j.story || []).map(l => `<div class="fx-line do"><b>·</b><span>${esc(l)}</span></div>`).join("");
      const res = (j.results || [])[0];
      if (j.done) { box.innerHTML = lines + (res ? `<div class="fx-line ${res.ok ? "ok" : "bad"}"><b>${res.ok ? "✓" : "✕"}</b><span>${res.ok ? "Works: " : "Not good: "}${res.latency_ms != null ? res.latency_ms + " ms, " : ""}${res.loss_pct != null ? res.loss_pct + " % loss, " : ""}${res.down_mbps != null ? "↓" + res.down_mbps + " Mb/s, " : ""}score ${res.score != null ? res.score : "—"}/100${res.error || res.verdict ? " - " + esc(res.error || res.verdict) : ""}</span></div>` : '<div class="fx-line bad"><b>✕</b><span>The test gave no result.</span></div>'); return; }
      box.innerHTML = lines + '<div class="fx-line run"><i></i>Testing…</div>';
    }
  } catch (err) { box.innerHTML = `<div class="fx-line bad"><b>!</b>${esc(err.message)}</div>`; } finally { bkRun(null); }
};
window.startBuild = async e => {
  e.preventDefault();
  const form = e.target, f = new FormData(form), body = bkBody(f);
  const box = $("#build-story"); box.classList.remove("hidden");
  try {
    const seen = bk.ckKey === bkKey(f) && !!bk.ck;        // the notice under the form already shows the conflicts for exactly these fields
    box.innerHTML = '<div class="fx-line run"><i></i>Checking the ports…</div>';
    const c = await api("/api/tunnels/precheck", {method: "POST", body: JSON.stringify({iran_id: body.iran_id, foreign_id: body.foreign_id, ports: body.ports, live: !!body.iran_id})});
    bk.ck = c; bk.ckKey = bkKey(f); bkCheckRender(form);
    const stuck = bkBlocked(form);
    if (stuck.length) { box.innerHTML = `<div class="fx-line bad"><b>!</b>Port ${stuck[0].port} is already in use on ${esc(c.iran)} (${esc(stuck[0].by)}): ${stuck[0].id ? "tick “replace it” above, or " : ""}choose another Iran server or another port. Nothing was built.</div>`; return false; }
    if (c.conflicts.length && !seen) { box.innerHTML = '<div class="fx-line plan"><b>›</b>A tunnel already carries this port to the foreign server: decide above (replace it or keep it), then press Build again.</div>'; return false; }
    body.replace_ids = [...form.querySelectorAll("input[name^=replace_]:checked")].map(x => +x.name.slice(8));
    body.replace_units = [...form.querySelectorAll("input[name^=stale_]:checked")].map(x => x.name.slice(6));
    const dup = (c.same_foreign || []).filter(x => !body.replace_ids.includes(x.id));        // the foreign server already ends a tunnel that is not being replaced: ask what to do with it
    if (dup.length) {
      const what = await glassChoice(`${c.foreign} already has ${dup.length === 1 ? "a tunnel" : dup.length + " tunnels"}: ${dup.map(x => `${x.name} (${x.iran}, ${x.state}, port ${x.ports.join(",") || "-"})`).join("; ")}.`,
        [{v: "replace", label: "Remove it and replace it", cls: "danger-fill", note: "the old one is removed first, then the new one is built"}, {v: "second", label: "Add as a second tunnel", cls: "pri", note: "the old one stays; both run"}]);
      if (!what) { box.innerHTML = '<div class="fx-line plan"><b>›</b>Nothing was built.</div>'; return false; }
      if (what === "replace") body.replace_ids = [...new Set([...body.replace_ids, ...dup.map(x => x.id)])].slice(0, 5);
    }
    body.dns_follow = body.replace_ids.length > 0 && !!form.querySelector("input[name=dns_follow]:checked");
    body.dns_names = form.querySelector("input[name=dns_set]:checked") ? String(f.get("dns_names") || "").split(/[,\s]+/).filter(Boolean) : [];
    box.innerHTML = '<div class="fx-line run"><i></i>Starting…</div>';
    const r = await api("/api/tunnels", {method: "POST", body: JSON.stringify(body)});
    if (r.why) toast("★ Best of the day: " + r.why, "ok");
    bkRun({type: "build", id: r.id});
    await followStory(r.id, box, 600);
  } catch (err) { box.innerHTML = `<div class="fx-line bad"><b>!</b>${esc(err.message)}</div>`; } finally { bkRun(null); }
  return false;
};
const FOLLOWING = new Map();      // action id -> the poll that follows it: a second window on the same action shares it, so pollers never stack (review 2026-10-16) - every 1.5 s while its box is on the screen, every 6 s once the window was closed (what waits for the end still gets it)
async function followStory(aid, box, maxPolls) {
  if (FOLLOWING.has(aid)) { const f = FOLLOWING.get(aid); f.box = box; return f.p; }
  const f = {box};
  f.p = (async () => { try {
    for (let i = 0; i < maxPolls; i++) {
      const b0 = f.box, on = !!(b0 && b0.isConnected);
      await new Promise(res => setTimeout(res, on ? 1500 : 6000));
      const s = await api(`/api/actions/${aid}`), b = f.box;
      if (b && b.isConnected) { const atEnd = b.scrollTop + b.clientHeight >= b.scrollHeight - 12; b.innerHTML = storyHtml(s); if (atEnd) b.scrollTop = b.scrollHeight; }      // a fixed box that follows the end of the log, unless you scrolled up to read
      if (s.done) return s;
    }
  } finally { FOLLOWING.delete(aid); } })();
  FOLLOWING.set(aid, f); return f.p;
}
/* removed tunnels: a copy is kept for 30 days; Restore builds the tunnel again (same name, same servers, same ports) */
const TRASH_SHOW = 600;      // the removed tunnel stays on the page for ten minutes, and only the newest one
const trashBox = () => { const now = Date.now() / 1000, all = (state.trashCache || []).filter(x => now - x.at < TRASH_SHOW), it = all.length ? [all.reduce((a, b) => b.at > a.at ? b : a)] : []; clearTimeout(window.__trT); if (!it.length) return ""; window.__trT = setTimeout(() => { if (state.page === "tunnels" && !state.open && !state.build) refresh(); }, Math.max(1000, (TRASH_SHOW - (now - it[0].at)) * 1000 + 500)); return `<div class="trashbox"><h5><i>♻</i>Recently removed<small>shown for 10 minutes</small></h5>${it.map(x => `<div class="trow"><span class="tn"><b>${esc(x.title)}</b><small class="mut">${esc(x.sub)} · ports ${esc(x.ports || "—")}</small></span><span class="mut tw">removed ${esc(ago(x.at))} ago</span><span class="ta"><button class="sm pri" onclick="trashRestore('${esc(x.id)}')" title="Build it again: same name, same servers, same ports">Restore</button><button class="sm" onclick="trashForget('${esc(x.id)}')" title="Drop the copy; it can not be restored after that">Forget</button></span></div>`).join("")}</div>`; };
window.tunHistory = async (id, name) => {      // the history opens INSIDE the tunnel window (Back returns), the window never closes
  const body = $("#modal .modal-body"); if (!body) return;
  body.innerHTML = `<div class="wback"><button class="sm" onclick="tunBack(${id})">← Back</button></div><div class="mut" style="padding:10px 4px">Loading…</div>`;
  try { const d = await api(`/api/actions?ref=${encodeURIComponent(name)}&limit=40`), rows = (d.actions || []).filter(a => !(a.dry_run) );
    body.innerHTML = `<div class="wback"><button class="sm" onclick="tunBack(${id})">← Back</button><b style="margin-left:10px">History of ${esc(name)}</b></div>` + (rows.length ? `<div class="th-list">${rows.map(a => { const tx = ((a.story || []).slice(-1)[0] || "").trim(), d = new Date(a.ts * 1000); return `<div class="th-row ${a.ok === false ? "bad" : "ok"}" title="${esc(tx)}"><time>${d.toLocaleDateString(undefined, {month: "2-digit", day: "2-digit"})} ${d.toLocaleTimeString(undefined, {hour12: false})}</time><i></i><b>${esc(a.action)}</b><span>${esc(tx)}</span></div>`; }).join("")}</div>` : '<div class="empty">Nothing was done to this tunnel yet.</div>'); }
  catch (e) { body.innerHTML = `<div class="wback"><button class="sm" onclick="tunBack(${id})">← Back</button></div><div class="err">${esc(e.message)}</div>`; }
};
window.tunBack = id => { const t = (state.tunnelsCache || []).find(x => x.id === id); if (t) { tunModal(t); tunAfter(t); } };
/* the speed a CUSTOMER gets (the owner, 2026-10-16: "a speed test in the tunnel menu: what speed does a user in Iran get - the closest possible to what the customer holds"): opens INSIDE the tunnel window (Back returns). Two ways: through a config of the tunnel on the probe (the whole path, one connection like a phone), or through the test door of the tunnel (the tunnel alone) */
const spdN = v => v == null ? "—" : +v >= 100 ? String(Math.round(+v)) : (+v).toFixed(1);
const SPD_GRADE = {smooth: "Smooth", good: "Good", fair: "Fair", slow: "Slow", bad: "Very slow"};
function tunSpeedResult(r, cap) {
  if (!r) return '<div class="spd-none">No test yet - run one, and the numbers stay here until the next.</div>';
  const runs = (r.runs || []).filter(x => x.ok !== false && x.down_mbps != null), cd = cap && cap.down_mbps ? +cap.down_mbps : 0, max = Math.max(1, cd, ...runs.map(x => +x.down_mbps || 0));
  const bar = (label, v, cls, tip = "") => `<div class="spd-b ${cls}"${tip ? ` title="${esc(tip)}"` : ""}><span class="ell">${esc(label)}</span><i><u style="width:${Math.max(2, Math.min(100, (+v || 0) / max * 100)).toFixed(0)}%"></u></i><b>${spdN(v)} <small>Mb/s</small></b></div>`;
  const how = r.mode === "customer" ? `through <b>${esc(r.via)}</b>: the whole path a customer has - this server, the tunnel, the foreign server and the internet` : "through the test door: the tunnel alone, no internet";
  return `<div class="spd-res ${esc(r.grade)}"><div class="spd-top"><div class="spd-n"><small>Download</small><b>${spdN(r.down_mbps)}</b><em>Mb/s</em></div><div class="spd-n"><small>Upload</small><b>${spdN(r.up_mbps)}</b><em>Mb/s</em></div><div class="spd-n"><small>Ping</small><b>${r.ping_ms == null ? "—" : Math.round(r.ping_ms)}</b><em>ms</em></div><span class="spd-g">${esc(SPD_GRADE[r.grade] || r.grade)}</span></div>
    <div class="spd-bars">${runs.map(x => bar(x.name, x.down_mbps, x.name === r.via || r.mode === "door" ? "me" : "", `${x.name}: ↓ ${spdN(x.down_mbps)} ↑ ${spdN(x.up_mbps)} Mb/s`)).join("")}${cd ? bar("the tunnel can carry", cd, "cap", "Eight connections together through the test door (the last quality test)") : ""}</div>
    <p class="spd-v">${esc(r.verdict)}</p><small class="mut">${ago(r.at)} ago · ${how}</small></div>`;
}
window.tunSpeed = async (id, name) => {
  const body = $("#modal .modal-body"); if (!body) return;
  const back = `<div class="wback"><button class="sm" onclick="tunBack(${id})">← Back</button><b style="margin-left:10px">Speed test · ${esc(name)}</b></div>`;
  body.innerHTML = back + '<div class="mut" style="padding:10px 4px">Loading…</div>';
  let o; try { o = await api(`/api/tunnels/${id}/speed`); } catch (e) { body.innerHTML = back + `<div class="err">${esc(e.message)}</div>`; return; }
  const c = o.customer, d = o.door, pick = c.ok ? "customer" : d.ok ? "door" : "", n = c.configs.length;
  const opt = (v, title, text, ok, why) => `<label class="spd-opt${ok ? "" : " off"}"><input type="radio" name="sp-mode" value="${v}"${v === pick ? " checked" : ""}${ok ? "" : " disabled"}><span class="spd-ot"><b>${title}</b><small>${ok ? text : esc(why)}</small></span></label>`;
  body.innerHTML = back + `<div class="spd"><div class="spd-opts">${opt("customer", "What a customer gets", `The probe in Iran downloads 25 MB and uploads 10 MB from the internet through ${n === 1 ? `<b>${esc(c.configs[0].name)}</b>` : `${n} configs of this tunnel`}: one connection, like a phone. The whole path, with the real protocol.`, c.ok, c.why)}${opt("door", "The tunnel alone", "One and then four connections through the test door of the tunnel, with random data. No internet, no config: only the tunnel.", d.ok, d.why)}</div>
    <div class="spd-act"><button class="pri" id="spd-run" onclick="tunSpeedRun(${id})"${pick ? "" : " disabled"}>Run the test</button><small class="mut">${pick ? "about a minute" : "nothing can be measured on this tunnel yet"}</small></div>
    <div id="spd-story" class="fix hidden"></div><div id="spd-res">${tunSpeedResult(o.last, o.capacity)}</div></div>`;
};
window.tunSpeedRun = async id => {
  const mode = (document.querySelector('input[name="sp-mode"]:checked') || {}).value, btn = $("#spd-run"), box = $("#spd-story"); if (!mode || !btn || !box) return;
  btn.disabled = true; box.classList.remove("hidden"); box.innerHTML = '<div class="fx-line run"><i></i>Starting…</div>';
  try {
    const r = await api(`/api/tunnels/${id}/speed`, {method: "POST", body: JSON.stringify({mode})}), s = await followStory(r.action_id, box, 400), o = await api(`/api/tunnels/${id}/speed`), res = $("#spd-res");
    if (res) res.innerHTML = tunSpeedResult(o.last, o.capacity);
    if (s && s.ok === false) toast("✕ " + lastLine(s), "bad"); else box.classList.add("hidden");      // (the numbers are the answer: the story folds away when it went well, and stays when it did not)
  } catch (e) { box.innerHTML = `<div class="fx-line bad"><b>!</b>${esc(e.message)}</div>`; }
  const b = $("#spd-run"); if (b) b.disabled = false;
};
window.trashRestore = async id => {
  const it = (state.trashCache || []).find(x => x.id === id); if (!it) return;
  if (!await gconfirm(`Restore the tunnel ${it.title}? It is built again between ${it.sub} with the ports ${it.ports} (a new GRE key; the services start by themselves; about a minute).`)) return;
  showModal(`<b>♻ Restoring ${esc(it.title)}</b>`, '<div id="trash-story" class="fix"><div class="fx-line run"><i></i>Starting…</div></div>', "wide");
  try { const r = await api(`/api/tunnel-trash/${encodeURIComponent(id)}/restore`, {method: "POST"}); await followStory(r.id, $("#trash-story"), 600); } catch (e) { const b = $("#trash-story"); if (b) b.innerHTML = `<div class="fx-line bad"><b>!</b>${esc(e.message)}</div>`; }
  refresh();
};
window.trashForget = async id => {
  const it = (state.trashCache || []).find(x => x.id === id); if (!it) return;
  try { await api(`/api/tunnel-trash/${encodeURIComponent(id)}`, {method: "DELETE"}); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.deleteTunnel = async (id, name) => {
  if (!await gconfirm(`Remove the tunnel ${name} from BOTH servers? Users on it lose their connection. This cannot be undone.`)) return;
  toast("Removing…");
  try { const r = await api(`/api/tunnels/${id}`, {method: "DELETE"}); toast("✓ " + r.results.join(" · ") + " — you can restore it from “Recently removed” above the list", "ok"); state.open = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
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
  if (state.page === "nodes") { const s = (state.serversCache || []).find(x => x.id === id); if (s) { srvModal(s); loadServerCharts(id); return; } }      // the window is one view: its charts load with it
  if (state.page === "tunnels") { const t = (state.tunnelsCache || []).find(x => x.id === id); if (t && t.state === "starting") { state.open = null; openBuilding(t).then(ok => { if (!ok) { state.open = id; state.tsec = null; tunModal(t); tunAfter(t); } }); return; } if (t) { state.tsec = null; tunModal(t); tunAfter(t); return; } }
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
/* why a build or a repair did not succeed, in plain words (the owner, 2026-10-16: "when it says it was not built, write the reason simply"): the summary of the job when it has one, else the last line of the story that says what went wrong - told in one simple sentence */
const apWhy = s => {
  const sm = s.summary || {}, lines = (s.story || []).filter(l => !/did not succeed|^Done\.?$|^Starting|^Working|^Trying|^Building/i.test(l));
  const raw = String(sm.hint || sm.problem || lines.slice(-1)[0] || "").trim(); if (!raw) return "";
  const P = [[/no Iran server is online/i, "None of your Iran servers is online right now: the panel cannot reach them over SSH. Turn one on, or fix its login, then build again."],
    [/No Iran server can carry/i, "No Iran server can take this tunnel right now - " + raw.replace(/^.*?right now:?\s*/i, "") + "."],
    [/already in use|port .*(busy|used)|is busy/i, "The port is already used on the server: pick another port, or replace the tunnel that holds it."],
    [/read-only/i, "The panel runs read-only (NIKATEL_READ_ONLY): nothing is built until that is switched off."],
    [/license/i, raw],
    [/timed out|no route|refused|unreachable|ssh|does not answer/i, "The server did not answer over SSH: it is off, a firewall is in the way, or its login is wrong."],
    [/GRE/i, "The GRE link between the two servers could not be made: the route blocks GRE. Try a BackPack type."],
    [/did not work/i, "The tunnel was built but did not pass data (" + raw.replace(/\s*-\s*trying.*$/i, "") + ")."]];
  const hit = P.find(([re]) => re.test(raw)); return hit ? hit[1] : raw;
};
const apWhyHtml = s => { const w = apWhy(s); return w ? `<em class="fx-why">${esc(w)}</em>` : ""; };
function storyHtml(s) {
  const cls = l => /healthy|works|finished|succeeded/i.test(l) ? "ok" : /not repaired|failed|still|does not answer|No safe|read-only|Stopped/i.test(l) ? "bad" : /^Plan:|^\[dry run\]/.test(l) ? "plan" : "do";
  return s.story.map(l => `<div class="fx-line ${cls(l)}"><b>${{ok: "✓", bad: "!", plan: "›", do: "·"}[cls(l)]}</b><span>${esc(l)}</span></div>`).join("") +
    (s.done ? `<div class="fx-line ${s.ok ? "ok" : "bad"}"><b>${s.ok ? "✓" : "✕"}</b><span>${["autopilot", "fix"].includes(s.action) && !s.dry_run ? (s.ok ? "All done - the problem is fixed." : "Repair did not succeed." + apWhyHtml(s)) : `${s.dry_run ? "Dry run" : s.action === "build" ? "Build" : s.action === "create" ? "Create" : s.action === "delete" ? "Removal" : s.action === "observe" ? "Look" : s.action === "pgupdate" ? "Update" : s.action === "pgnode" ? "Making the node" : s.action === "install" ? "Install" : s.action === "speed" ? "Speed test" : s.action === "move" ? "Move" : "Repair"} ${s.ok ? "finished" : "did not succeed"}.${s.ok ? "" : apWhyHtml(s)}`}</span></div>` : '<div class="fx-line run"><i></i>Working…</div>');
}
/* ---- Tunnel Doctor: what needs attention, the network advisor ("which tunnel is best today"), the history ---- */
const ARVAN_UI = true;    // the DNS accounts (ArvanCloud, Cloudflare) live under Tunnels → CDN; the Doctor (Tunnels → Tunnel Doctor) decides when a server is replaced

const AP_LABEL = {tcpmux: "Backhaul · TCP Mux (GRE)", "bp:tcpmux": "BackPack · TCP Mux", "bp:wssmux": "BackPack · WebSocket TLS Mux", "chisel:tls": "Chisel · TLS WebSocket", "bpd:udp": "BackPack Direct · UDP", tcp: "Backhaul · TCP", wsmux: "Backhaul · WS Mux", wssmux: "Backhaul · WSS Mux", paqet: "Paqet · KCP", chisel: "Chisel · WebSocket", "chisel:fwd": "Chisel · forward WebSocket", "chisel:fwdtls": "Chisel · forward TLS WebSocket", "bp:stealth": "BackPack · Stealth", "bp:tcp": "BackPack · TCP", "bp:wss": "BackPack · WebSocket TLS", "bp:ws": "BackPack · WebSocket", "bp:wsmux": "BackPack · WebSocket Mux", "bp:kcp": "BackPack · UDP + KCP", "bp:quic": "BackPack · UDP + QUIC", "bp:pck": "BackPack · TCP + PCK", "bp:xdi": "BackPack · xDi (ICMP)", "bpd:quic": "BackPack Direct · QUIC", "bpd:pck": "BackPack Direct · PCK", "bpd:xdi": "BackPack Direct · xDi (ICMP)"};
const apName = k => AP_LABEL[k] || k;
/* the history lists only PROBLEMS and how they ended: a look that found nothing, a dry run and a tunnel that needed nothing are not shown */
const histRows = list => list.filter(a => !a.dry_run && (["fix", "autopilot", "balance", "underuse", "move"].includes(a.action) || (a.action === "speed" && /Where the problem is/.test((a.story || []).join(" ")))) && !/nothing to do|nothing to repair|is healthy|only watching|looks fine|nothing to look/i.test((a.story || []).slice(-2).join(" ")));      // (a look at a dropped bandwidth is history too: it says where the problem is)
/* the two lists of the Tunnel Doctor, written for someone who is not an expert: what is being fixed now, and what was done with the result */
const TK_ORIGIN = {outside: "Outside the panel", server: "A service on your server", panel: "The panel itself", unknown: ""};
const tkSimple = a => { const s = a.summary || {}, ok = a.ok === true, stale = a.ok == null && !a.done && Date.now() / 1000 - a.ts > 3600, run = a.ok == null && !stale;
  const mv = a.action === "move", last = esc((a.story || []).slice(-1)[0] || ""), did = (s.tried || []).length ? s.tried.map(esc).join(" → ") : mv ? last : (ok ? "The Doctor fixed it by itself." : "Nothing more could be done from the panel.");
  const org = stale ? ["panel", "The panel was restarted"] : !ok && !run && s.origin && s.origin !== "unknown" ? [s.origin, TK_ORIGIN[s.origin]] : null;      // ONE line says what matters; the rest is under "details"
  const one = stale ? "This job was cut short when the panel restarted. If the tunnel is still down, the Doctor starts again by itself." : ok ? did : run ? "Working on it…" : (s.hint || s.problem || did);
  return `<div class="tk ${ok ? "ok" : run ? "run" : "bad"}"><div class="tk-head"><span class="tk-res">${mv ? (ok ? "✓ Moved" : run ? "… Moving" : stale ? "✕ Stopped" : "✕ Not moved") : ok ? "✓ Fixed" : run ? "… Working" : stale ? "✕ Stopped" : "✕ Not fixed"}</span><b>${esc(a.ref)}</b>${org ? `<span class="tk-or o-${esc(org[0])}">${esc(org[1])}</span>` : ""}<time title="${when(a.ts)}">${ago(a.ts)} ago</time><a class="mut tk-more" onclick="this.closest('.tk').querySelector('.fix').classList.toggle('hidden')">details</a></div>
    <div class="tk-one">${esc(one)}</div>
    <div class="fix hidden">${s.problem ? `<div class="tk-line"><em>Problem</em><span>${esc(s.problem)}</span></div>` : ""}<div class="tk-line"><em>What was done</em><span>${did}</span></div>${(a.story || []).map(l => `<div class="fx-line do"><b>·</b><span>${esc(l)}</span></div>`).join("")}</div></div>`; };
const tkWorking = w => `<div class="tk run"><div class="tk-head"><span class="tk-res">… Fixing it now</span><b>${esc(w.name)}</b></div><div class="tk-line"><em>Right now</em><span>${esc(w.line || (w.mode === "look" ? "looking at it" : "repairing"))}</span></div></div>`;
window.apTasksTab = k => { try { localStorage.setItem("nk.aptab", k); } catch (e) { /* private window */ } const card = $(".ap-tasks"); if (!card) return; card.querySelectorAll(".ap-ttabs button").forEach(b => b.classList.toggle("on", b.dataset.k === k)); ["now", "done", "moves", "ana", "know"].forEach(p => { const el = $("#ap-t-" + p); if (el) el.classList.toggle("hidden", p !== k); }); const bd = $("#ap-t-" + k); if (bd) bd.scrollTop = 0; fxFit(); if (k === "ana") anaLoad(); if (k === "know") knLoad(); };
/* ---- Tunnel Doctor → Best today (a tab of the Doctor's box): the tunnels ranked by how they behaved (a check every minute, the latency / jitter / loss measured again every half hour) ---- */
state.bestHours = +localStorage.getItem("nk.best.h") || 24;
window.bestRange = h => { state.bestHours = h; try { localStorage.setItem("nk.best.h", h); } catch (e) { /* private window */ } document.querySelectorAll(".bt-range button").forEach(b => b.classList.toggle("on", b.textContent.trim() === ({6: "6 hours", 24: "24 hours", 168: "7 days"})[h])); bestLoad(); };
window.bestLoad = async () => {
  const box = $("#ana-best"); if (!box) return;
  const d = await api(`/api/besttoday?hours=${state.bestHours}`).catch(() => null); if (!d || !$("#ana-best")) return;
  const rows = d.rows, ranked = rows.filter(r => r.score != null), top = ranked.slice(0, 3), medal = ["🥇", "🥈", "🥉"];
  const tone = s => s >= 80 ? "ok" : s >= 55 ? "mid" : "low", dot = r => `<i class="st ${r.state === "up" ? "up" : r.state === "down" ? "down" : "degraded"}" title="${esc(r.state)}"></i>`;
  const num = (v, u) => v == null ? "—" : v + u;
  const pods = top.map((r, i) => `<div class="bt-pod p${i + 1}"><div class="bt-m">${medal[i]}</div><b>${esc(r.name)}</b><small>${esc(r.iran)} ⇄ ${esc(r.foreign)}</small><div class="bt-s ${tone(r.score)}">${r.score}<em>/ 100</em></div><div class="bt-chips"><span>${r.avail}% up</span><span>${num(r.rtt_ms, " ms")}</span><span>${r.outages} break${r.outages === 1 ? "" : "s"}</span></div></div>`).join("");
  const row = r => `<div class="bt-row ${r.score == null ? "dim" : ""}"><span class="bt-pl">${r.place || "·"}</span><span class="bt-nm">${dot(r)}<b>${esc(r.name)}</b>${r.best_for ? `<span class="pill ok bt-bf" title="The best of the tunnels that lead to ${esc(r.foreign)}">best to ${esc(r.foreign)}</span>` : ""}<small>${esc(r.iran)} ⇄ ${esc(r.foreign)}</small></span>
    <span class="bt-sc">${r.score == null ? '<small class="mut">collecting data…</small>' : `<i class="bt-track"><u class="${tone(r.score)}" style="width:${r.score}%"></u></i><b>${r.score}</b>`}</span>
    <span>${r.score == null ? "—" : r.avail + " %"}</span><span>${num(r.rtt_ms, " ms")}</span><span>${num(r.jitter_ms, " ms")}</span><span>${r.loss_pct == null ? "—" : r.loss_pct + " %"}</span><span>${r.score == null ? "—" : r.outages}</span></div>`;
  const up = $("#bt-up"); if (up) up.textContent = `${ranked.length} of ${rows.length} ranked · ${new Date(d.at * 1000).toLocaleTimeString(undefined, {hour12: false})}`;
  box.innerHTML = `<div class="bt-wrap">${top.length ? `<div class="bt-pods">${pods}</div>` : '<div class="tk-empty"><b>Collecting data… the ranking appears after about 20 checks of each tunnel.</b></div>'}
    <div class="bt-list"><div class="bt-row bt-head"><span>#</span><span>Tunnel</span><span>Score</span><span>Up</span><span>Latency</span><span>Jitter</span><span>Loss</span><span>Breaks</span></div>${rows.map(row).join("") || '<div class="empty">No tunnel.</div>'}</div></div>`;
  clearTimeout(window.__btT); window.__btT = setTimeout(() => { if (state.page === "tunnels" && $("#ana-best")) bestLoad(); }, 60000);
};
/* the icons and small helpers of the Tunnel Doctor page (the owner's design picture, 2026-10-16) */
const AP_IC = {bars: '<path d="M7 20V10M12 20V4M17 20v-7"/>', wrench: '<path d="M14.7 6.3a1 1 0 000 1.4l1.6 1.6a1 1 0 001.4 0l3.77-3.77a6 6 0 01-7.94 7.94l-6.91 6.91a2.12 2.12 0 01-3-3l6.91-6.91a6 6 0 017.94-7.94l-3.76 3.76z"/>',
  server: '<rect x="3" y="3" width="18" height="7.5" rx="2.2"/><rect x="3" y="13.5" width="18" height="7.5" rx="2.2"/><path d="M7 6.8h.01M7 17.2h.01"/>', check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>', sync: '<path d="M20 11a8 8 0 00-14-4L4 9M4 4v5h5M4 13a8 8 0 0014 4l2-2M20 20v-5h-5"/>',
  wifi: '<path d="M5 12.5a10 10 0 0114 0M8.5 16a5 5 0 017 0M12 19.5h.01"/>', chat: '<path d="M5 5h14a2 2 0 012 2v8a2 2 0 01-2 2h-7l-4 3.5V17H5a2 2 0 01-2-2V7a2 2 0 012-2z"/>',
  spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>', ring: '<circle cx="12" cy="12" r="6.5"/>', chev: '<path d="M6 9l6 6 6-6"/>', up: '<path d="M7 14l5-5 5 5"/>', down: '<path d="M7 10l5 5 5-5"/>',
  inbox: '<path d="M3 13h5l1.5 3h5L16 13h5"/><path d="M5.5 6.5L3 13v5a2 2 0 002 2h14a2 2 0 002-2v-5l-2.5-6.5a2 2 0 00-1.9-1.4H7.4a2 2 0 00-1.9 1.4z"/><path d="M17.5 1.5v3M16 3h3M5.5 1.5v2M4.5 2.5h2"/>'};
const apIc = (k, cls = "") => `<svg${cls ? ` class="${cls}"` : ""} viewBox="0 0 24 24" aria-hidden="true">${AP_IC[k] || ""}</svg>`;
let apCfgT = 0;
window.apStep = (btn, dir) => { const inp = btn.closest(".dr-num").querySelector("input"); if (!inp || inp.disabled) return; const lo = +inp.min || 0, hi = +inp.max || 1e9; inp.value = Math.min(hi, Math.max(lo, (+inp.value || lo) + dir * (+inp.dataset.st || 1))); clearTimeout(apCfgT); apCfgT = setTimeout(apCfg, 700); };      // (the little arrows of a number: saved when you stop pressing)
PAGES.actions = async () => {
  // Tunnel Doctor is about tunnels only: configs and servers have their own places (the dashboard's Problems box, Panels, Servers)
  const outage = await api("/api/autopilot/outage").catch(() => null), nw = await api("/api/netwatch").catch(() => null);
  const [d, tl, srvs, rp, dc] = await Promise.all([api("/api/actions?limit=80&kinds=fix,autopilot,balance,underuse,move,speed"), api("/api/tunnels"), api("/api/servers").catch(() => []), api("/api/settings/repair").catch(() => ({})), api("/api/doctor").catch(() => null)]);
  state.tunnelsCache = tl;
  const mode = rp.mode || (rp.auto ? "fix" : "off"), on = mode !== "off", lic = dc ? dc.licensed !== false : rp.licensed_move !== false, ro = !!d.read_only;
  const bad = tl.filter(t => t.state !== "up"), lossy = tl.filter(t => t.state === "up" && (tunLoss(t).v || 0) >= SHOW_LOSS), slow = tl.filter(t => t.state === "up" && ["drop", "slow"].includes((t.bw || {}).state) && !lossy.includes(t)), users = tl.filter(t => t.state === "up" && ["poor", "bad"].includes((t.ux || {}).grade) && !lossy.includes(t) && !slow.includes(t));      // slow: the bandwidth dropped (the Doctor measures it and looks at why by itself); users: the probe says the configs that ride the tunnel do badly
  const row = (t, kind) => `<div class="ap-row"><span class="st ${kind === "lossy" ? ((tunLoss(t).v || 0) >= LOSS_SEVERE ? "down" : "degraded") : kind === "slow" ? "degraded" : kind === "users" ? (t.ux.grade === "bad" ? "down" : "degraded") : t.state}">${kind === "lossy" ? "lossy" : kind === "slow" ? "slow" : kind === "users" ? "users" : esc(stName(t.state))}</span><b>${esc(t.name)}</b>${(() => { const w = outage && (outage.working || []).find(x => x.name === t.name); return w ? `<span class="wrench" title="the Tunnel Doctor is working on this tunnel right now">🔧</span><span class="work-chip">${w.mode === "look" ? "looking at it" : "repairing"}</span>` : ((outage && outage.by_tunnel && outage.by_tunnel[t.name]) ? `<span class="mut work-chip watch" title="A server of this tunnel is off: the repair does nothing here until it is back">waiting for the server</span>` : `<span class="mut work-chip watch" title="the Tunnel Doctor watches it and starts by itself when needed">watching</span>`); })()}<span class="mut">${esc(t.iran_name)} ⇄ ${esc(t.foreign_name)} · ${esc(t.type)}${t.transport ? " · " + esc(t.transport.replace("l3:", "")) : ""}</span>${(() => { const off = outage && outage.by_tunnel && outage.by_tunnel[t.name], loss = tunLoss(t).v || 0, why = off ? `A server of this tunnel is off (${off.join(", ")}): nothing is done until it is back.` : kind === "lossy" ? `${Math.round(loss)} % of the packets are lost on this link.` : kind === "slow" ? bwWhy(t) : kind === "users" ? uxWhy(t) : t.state === "down" ? "The tunnel does not answer." : "Part of the link works, part does not."; return `<span class="ap-why ${off ? "mut" : ""}" title="${esc(why)}">${esc(why)}</span>` + (off || t.last_error || kind === "lossy" || kind === "slow" || kind === "users" ? `<a class="ap-detail" onclick="apDetail(${t.id})" title="The full reason and the log">Detail</a>` : ""); })()}${on ? `<button class="sm pri" onclick="apLive(${t.id}, '${esc(t.name)}')" title="What the Tunnel Doctor is doing with this tunnel right now - its live log">${apIc("wifi")}<span>Live</span></button>` : `<button class="sm" onclick="apDiagnose(${t.id})">Diagnose</button><button class="sm pri" onclick="apDiagnose(${t.id}, {auto: true})">${apIc("spark")}<span>Fix with AI</span></button>`}</div>`;
  const n = bad.length + lossy.length + slow.length + users.length;
  let apTab = "now"; try { const v = localStorage.getItem("nk.aptab"); apTab = ["done", "moves", "ana", "know"].includes(v) ? v : v === "cpu" ? "ana" : "now"; } catch (e) { /* private window */ }
  const working = (outage && outage.working) || [], inList = w => bad.concat(lossy, slow, users).some(t => t.name === w.name), workingRows = working.filter(w => !inList(w)).map(tkWorking).join(""), nowN = bad.length + lossy.length + slow.length + users.length + working.filter(w => !inList(w)).length;
  const doneList = histRows(d.actions), doneN = doneList.length, doneRows = doneList.map(tkSimple).join("");
  const mineOf = nm => tl.filter(t => t.iran_name === nm || t.foreign_name === nm).map(t => t.name);
  const issues = [...(srvs || []).filter(s => s.enabled && s.online === false).map(s => ({kind: "off", role: s.role, name: s.name, why: s.last_error || "", tunnels: mineOf(s.name)})),
    ...((nw && nw.items) || []).filter(i => !(srvs || []).some(s => s.name === i.server && s.enabled && s.online === false)).map(i => ({kind: "net", role: i.kind === "iran_server" ? "iran" : "foreign", name: i.server, text: i.text, tunnels: i.tunnels || []}))];
  const issueCard = i => `<div class="ap-iss ${i.kind}"><div class="ap-ih"><span class="ap-flag">${i.role === "iran" ? "🇮🇷" : "🌍"}</span><b>${i.role === "iran" ? "Iran server" : "Foreign server"} ${esc(i.name)}</b><span class="pill ${i.kind === "off" ? "err" : "warn"}">${i.kind === "off" ? "OFF" : "NETWORK DISTURBED"}</span></div>
      <div class="ap-it">${i.kind === "off" ? `It is off or does not answer${i.why ? ` <small class="mut">(${esc(String(i.why).slice(0, 80))})</small>` : ""}.` : `${esc(i.text.replace(/^.*?: /, ""))}.`}</div>
      ${i.tunnels.length ? `<div class="ap-tl"><small class="mut">${i.tunnels.length} tunnel${i.tunnels.length === 1 ? "" : "s"}:</small>${i.tunnels.map(n => `<span class="tchip">${esc(n)}</span>`).join("")}</div>` : ""}
      <div class="ap-do">${i.kind === "off" ? (i.role === "iran" && dc && dc.can_move ? `Its tunnels move to another Iran server after ${rp.server_minutes || 10} minutes off (the DNS follows); they come back after 3 healthy hours.` : "Nothing is done to its tunnels until it is back on: nothing to restart or rebuild.") : "The repair only watches: rebuilding would not help. It carries on by itself when the server answers well again."}</div></div>`;
  const disturb = issues.length ? `<div class="card ap-alert"><h3>⚠ Servers and network <span class="sp"></span><span class="pill err">${issues.length}</span></h3><div class="ap-issues">${issues.map(issueCard).join("")}</div></div>` : "";
  const storm = outage && outage.storm ? `<div class="storm-banner">⚠ <b>Mass disruption:</b> ${outage.servers_offline.length} server(s) offline, ${outage.tunnels_down.length} of ${outage.tunnels} tunnels down. While it lasts nothing is deleted or rebuilt; the repair waits for the servers, then restarts what did not come back by itself.</div>` : "";
  /* the Doctor's mode (the owner's picture, 2026-10-16): three cards, the chosen one lit with a tick - Doctor Mode (the "Off" of the picture: it only watches), Auto Fix Tunnels, Auto Move to Iran Server; the number of a mode sits in its own card and is saved as soon as it changes */
  const irn = (srvs || []).filter(s => s.role === "iran" && s.enabled);
  const cfg = (name, before, after, val, min, max, st) => `<div class="dr-nf" onclick="event.stopPropagation()"><span>${before}</span><span class="dr-num"><input name="${name}" type="number" min="${min}" max="${max}" step="1" data-st="${st}" value="${val}" ${ro ? "disabled" : ""} onchange="apCfg()"><span class="dr-st"><button type="button" tabindex="-1" aria-label="More" ${ro ? "disabled" : ""} onclick="apStep(this, 1)">${apIc("up")}</button><button type="button" tabindex="-1" aria-label="Less" ${ro ? "disabled" : ""} onclick="apStep(this, -1)">${apIc("down")}</button></span></span><span>${after}</span></div>`;
  const mcard = (k, ic, title, text, extra, locked) => { const dis = ro && k !== "off" ? "The Doctor needs NIKATEL_READ_ONLY=false on the panel server first" : locked ? "The mode that moves servers needs the failover license (Settings → About → License)" : "";
    return `<div class="dr-card ${mode === k ? "on" : ""}${locked ? " locked" : ""}${dis ? " dis" : ""}" data-k="${k}" role="button" tabindex="0" ${dis ? `title="${dis}" aria-disabled="true"` : `onclick="apMode('${k}')" onkeydown="if (event.key === 'Enter') apMode('${k}')"`}><i class="dr-ic">${apIc(ic)}</i><div class="dr-tx"><div class="dr-h"><b>${title}</b>${k === "off" ? '<span class="dr-off">Off</span>' : ""}</div><p>${text}</p></div>${extra ? `<div class="dr-cf">${extra}</div>` : ""}<em class="dr-chk">${mode === k ? apIc("check") : locked ? "🔒" : ""}</em></div>`; };
  const modes = `<div class="dr-modes" data-mode="${mode}">
      ${mcard("off", "bars", "Doctor Mode", "Only monitors and reports. You diagnose and fix manually, or let AI suggest solutions.", "")}
      ${mcard("fix", "wrench", "Auto Fix Tunnels", "Automatically restarts, rebuilds and changes GRE links or tunnel type if needed.", `<div class="dr-nf" onclick="event.stopPropagation()"><span>The moment a tunnel is down the Doctor starts - no waiting time.</span></div>`)}
      ${mcard("move", "server", "Auto Move to Iran Server", "If the main Iran server is down, move tunnels and DNS to another Iran server automatically.", cfg("server_minutes", "If an Iran server is down for", "minutes, move to another.", rp.server_minutes || 10, 2, 60, 1), !lic)}</div>`;
  const drEmpty = (title, text) => `<div class="dr-empty"><i>${apIc("inbox")}</i><b>${title}</b><p>${text}</p></div>`;
  const tabBtn = (k, ic, label, cnt, tip) => `<button type="button" data-k="${k}" class="${apTab === k ? "on" : ""}" onclick="apTasksTab('${k}')"${tip ? ` title="${tip}"` : ""}>${apIc(ic)}<span>${label}</span>${cnt ? `<i class="cnt">${cnt}</i>` : ""}</button>`;
  const acts = `<span class="dr-tr">${bad.length && !on ? `<button type="button" class="dr-pill pri" onclick="apFixAll()" title="Diagnose and repair every tunnel that is down or degraded, one after the other">${apIc("spark")}<span>Fix all (${bad.length})</span></button>` : ""}<button type="button" class="dr-pill" onclick="apExplain()" title="The AI reads what the panel knows and says what to do first (needs the key in Settings)">${apIc("chat")}<span>Explain</span></button><span class="dr-pill dr-state ${n ? "err" : "ok"}">${n ? n + " to look at" : "all up"}</span></span>`;
  /* what the Doctor would do in the mode "move" but did not (a poor route, an exhausted server): one click does it */
  const sugs = (dc && dc.suggestions) || [];
  const sugRow = s => `<div class="ap-row ap-sug"><span class="st degraded">${s.kind === "type" ? "speed" : s.kind === "route" ? "route" : "load"}</span><b>${esc(s.tunnel)}</b><span class="ap-ft"><em>${esc(s.from)}</em> → <em>${esc(s.to || "?")}</em></span><span class="ap-why mut" title="${esc(s.why)}">${esc(s.why)}</span><time title="${when(s.at)}">${ago(s.at)} ago</time>${s.kind === "type" ? `<button class="sm pri" onclick="apSugApply('${esc(s.id)}')" title="Build this tunnel again as ${esc(s.to)} (same name, same ports): the users are cut for a minute or two, and the old type is put back if the new one does not build">${apIc("sync")}<span>Change type</span></button>` : s.to_id ? `<button class="sm pri" onclick="apSugApply('${esc(s.id)}')" title="Build it on ${esc(s.to)} first, point its DNS, then remove the old one">${apIc("sync")}<span>Move now</span></button>` : `<button class="sm" onclick="apPickMove(${s.tunnel_id}, '${esc(s.tunnel)}')" title="Choose the Iran server yourself">Choose a server</button>`}<button class="sm" onclick="apSugDrop('${esc(s.id)}')">Dismiss</button></div>`;
  const sugBox = sugs.length ? `<div class="nw-box sug-box"><b class="nw-title">💡 The Doctor suggests${mode === "fix" && sugs.some(s => s.kind !== "type") ? ' <small class="mut">(in the mode “Fix + move” it would do this by itself)</small>' : ""}</b>${sugs.map(sugRow).join("")}</div>` : "";
  /* the ledger of moves: what went where and why, with the way back */
  const REASON = {down: "server off", resources: "exhausted", route: "bad route", return: "return", manual: "by hand", replace: "replaced", standby: "standby"};
  const mv = (dc && dc.moves) || [], back = (dc && dc.back) || {}, rails = (dc && dc.rails) || {max_per_day: 3, cooldown_hours: 2, back_after_hours: 3};
  const mvRow = m => { const names = (m.tunnels || []).length ? m.tunnels : (m.asked || []), canBack = m.ok !== false && !m.returned && m.reason !== "return" && (m.tunnels || []).length && m.to_id;
    return `<div class="mv-row ${m.ok === false ? "bad" : m.returned ? "back" : m.reason === "return" ? "ret" : "ok"}"><time title="${when(m.at)}">${ago(m.at)} ago</time><span class="mv-k k-${esc(m.reason)}">${REASON[m.reason] || esc(m.reason)}</span><span class="mv-t">${names.map(x => `<span class="tchip">${esc(x)}</span>`).join("") || '<span class="mut">—</span>'}</span><span class="mv-ft"><em>${esc(m.from)}</em> → <em>${esc(m.to || "?")}</em></span><span class="mv-why mut" title="${esc(m.why)}">${esc(m.why)}</span><span class="mv-dns mut" title="DNS records re-pointed">${m.reason === "standby" ? "" : m.dns + " DNS"}</span><span class="pill ${m.ok === false ? "err" : m.returned ? "" : "ok"}">${m.ok === false ? "failed" : m.returned ? "returned" : m.reason === "standby" ? "on standby" : "done"}</span><span class="mv-act">${m.reason === "standby" && !m.returned ? `<button class="sm" onclick="apStandbyBack('${esc((m.asked || [])[0] || "")}')" title="Take the users' ports off the standby now: they are the tunnel's again (open connections end on the standby by themselves)">Back now</button>` : ""}${canBack ? `<button class="sm" onclick="apReturn('${esc(m.id)}')" title="Bring these tunnels back to ${esc(m.from)} now (built there first, the DNS follows)">↩ Return now</button>` : ""}${m.dns && m.ok !== false ? `<button class="sm" onclick="foUndo('${esc(m.id)}')" title="Point only the DNS records back at ${esc(m.from_ip)}; the tunnels stay">DNS back</button>` : ""}</span></div>`; };
  const backChips = irn.filter(s => back[String(s.id)] && mv.some(m => m.from_id === s.id && m.reason === "down" && !m.returned && m.ok !== false)).map(s => { const h = (Date.now() / 1000 - back[String(s.id)]) / 3600; return `<span class="pill ${h >= rails.back_after_hours ? "ok" : ""}" title="Its tunnels come back by themselves after ${rails.back_after_hours} healthy hours">${esc(s.name)}: back for ${h < 1 ? Math.round(h * 60) + " min" : h.toFixed(1) + " h"}</span>`; }).join("");
  const openN = mv.filter(m => !m.returned && m.ok !== false && m.reason !== "return").length;
  const movesTab = `<div class="mv-bar"><span class="mv-plan"><span class="mut">What if</span><select id="fo-srv">${irn.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join("")}</select><span class="mut">went off?</span><button type="button" class="sm" onclick="foPreview()" title="The tunnels that would move, the server that would take them, the DNS records of every account that would change - nothing is changed">👁 Show the plan</button></span>${backChips}<span class="sp"></span><small class="mut">At most ${rails.max_per_day} moves a day per tunnel, ${rails.cooldown_hours} hours between two, one at a time; Telegram tells before and after.</small></div>
    <div class="mv-list">${mv.map(mvRow).join("") || drEmpty("No moves yet", "When the Doctor moves the tunnels of an Iran server (or you do, from a tunnel window), it is written here with a way back.")}</div>`;

  const shown = $(".ap-tasks .ap-tbody:not(.hidden)"), keep = shown && shown.id === "ap-t-" + apTab ? shown.scrollTop : -1;          // the same tab is drawn again (the ticker, every 15 s): its scroll is kept and its fade-in is not played again - the page used to jump to the top and flash
  $("#page").innerHTML = `<div class="dr-top">${storm}${disturb}${modes}</div>
    <div class="card ap-tasks fx-box">
      <div class="ap-thead"><div class="ap-ttabs">${tabBtn("now", "wrench", "Active Tasks", nowN, "")}${tabBtn("done", "check", "Completed", doneN, "")}${tabBtn("moves", "sync", "Auto Moves", openN, "Every move of tunnels between Iran servers, with Return and DNS back")}${tabBtn("ana", "bars", "Analytics", 0, "What the history says about the tunnels and the servers, and the CPU cores")}${tabBtn("know", "spark", "Knowledge", 0, "What the Doctor already knows about every pair of servers - which tunnel types connect - and why tunnels were cut before")}</div>${acts}</div>
      <div class="ap-tbody ${apTab === "now" ? "" : "hidden"}" id="ap-t-now">${sugBox}<div class="ap-att">${workingRows}${bad.map(t => row(t, "bad")).join("")}${lossy.map(t => row(t, "lossy")).join("")}${slow.map(t => row(t, "slow")).join("")}${users.map(t => row(t, "users")).join("")}
        ${nowN || sugs.length ? "" : drEmpty("No active fixes right now", "Everything is running smoothly. Active tunnel issues and automatic fixes will appear here in real time.")}</div>${usersNote(dc && dc.users)}
        <div id="ap-all" class="fix hidden" style="margin-top:10px"></div></div>
      <div class="ap-tbody ${apTab === "done" ? "" : "hidden"}" id="ap-t-done"><div class="ap-hist">${doneRows || drEmpty("No completed fixes yet", "Every fix the Doctor finishes, and how it ended, is listed here.")}</div></div>
      <div class="ap-tbody ${apTab === "moves" ? "" : "hidden"}" id="ap-t-moves">${movesTab}</div>
      <div class="ap-tbody ${apTab === "ana" ? "" : "hidden"}" id="ap-t-ana"><div id="ana-box"><div class="loading"><i class="spin"></i><span>Reading the history…</span></div></div></div>
      <div class="ap-tbody ${apTab === "know" ? "" : "hidden"}" id="ap-t-know"><div id="kn-box">${(window.knHtml && knHtml()) || `<div class="loading"><i class="spin"></i><span>Reading what the Doctor knows…</span></div>`}</div></div>
    </div>
    `;
  if (keep >= 0) { const tb = $(".ap-tasks .ap-tbody:not(.hidden)"); if (tb) { tb.classList.add("still"); tb.scrollTop = keep; } }
  if (apTab === "ana") anaLoad();
  if (apTab === "know") knLoad();          // the Analyst is the fourth list of the Doctor's box (In progress · Finished · Moves · Analyst); the CPU cores live under it
  apFit(null); fxFit();
};
/* the mode cards: a click on a card chooses the mode; a change of the number inside a card saves it at once (with the mode as it is) */
const apNum = (nm, dflt) => { const el = document.querySelector(`.dr-modes [name=${nm}]`); return el && +el.value ? +el.value : dflt; };
window.apMode = async k => {
  const box = $(".dr-modes"), was = box && box.dataset.mode; if (box && was === k) return;
  if (box) apModeDraw(box, k);      // the card is lit at once and nothing is redrawn (the owner, 2026-10-16: the page jumped when a mode was chosen); the server is told in the background
  try { await api("/api/settings/repair", {method: "PUT", body: JSON.stringify({mode: k, server_minutes: apNum("server_minutes", 10)})}); toast({off: "The Doctor is off - you fix by hand (Diagnose, Fix with AI)", fix: "✓ The Doctor fixes the tunnels by itself", move: "✓ The Doctor fixes the tunnels and replaces a dead Iran server by itself"}[k], "ok"); }
  catch (e) { toast("✕ " + e.message, "bad"); if (box) apModeDraw(box, was); return; }
  if (state.page === "tunnels" && $(".dr-modes")) PAGES.actions();      // what depends on the mode (the buttons of a row, Fix all) is drawn again in place - no dimming, no loading box
};
const apModeDraw = (box, k) => { box.dataset.mode = k; box.querySelectorAll(".dr-card").forEach(c => { const on = c.dataset.k === k, chk = c.querySelector(".dr-chk"); c.classList.toggle("on", on); if (chk && !c.classList.contains("locked")) chk.innerHTML = on ? apIc("check") : ""; }); };window.apCfg = async () => {
  const box = $(".dr-modes"), mode = (box && box.dataset.mode) || "fix";
  try { await api("/api/settings/repair", {method: "PUT", body: JSON.stringify({mode, server_minutes: apNum("server_minutes", 10)})}); toast("✓ Doctor settings saved", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); refresh(); }
};
window.apSugApply = async id => { try { const r = await api("/api/doctor/suggestions/" + encodeURIComponent(id) + "/apply", {method: "POST"}); showModal("<b>🔁 The Doctor's suggestion</b>", '<div id="ap-story" class="fix"><div class="fx-line run"><i></i>Starting…</div></div>', "wide"); followStory(r.id, $("#ap-story"), 900).then(() => refresh()); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.apSugDrop = async id => { try { await api("/api/doctor/suggestions/" + encodeURIComponent(id) + "/dismiss", {method: "POST"}); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* configs that ride no known tunnel: the Doctor cannot say what their users get until each one is tied to its tunnel (the Configs page is switched off, so it is done here) */
const usersNote = u => u && u.total > u.linked ? `<div class="ap-note mut">🔗 ${u.total - u.linked} of the ${u.total} configs ride no known tunnel, so the Doctor cannot say what their users get through it. <button class="sm" onclick="apLinkConfigs()" title="Tie each config to the tunnel it goes through">Link them…</button></div>` : "";
window.apLinkConfigs = async () => {
  showModal("<b>🔗 Link configs to tunnels</b>", '<div class="loading"><i class="spin"></i><span>Reading the configs…</span></div>');
  let dc, cfgs, tl; try { [dc, cfgs, tl] = await Promise.all([api("/api/doctor"), api("/api/configs"), api("/api/tunnels")]); } catch (e) { closeModal(); toast("✕ " + e.message, "bad"); return; }
  const un = new Set(((dc || {}).users || {}).unlinked || []), list = (cfgs.configs || []).filter(c => c.enabled && un.has(c.name)), opts = tl.filter(t => t.enabled !== false).map(t => `<option value="${esc(t.name)}">${esc(t.name)}</option>`).join("");
  const rows = list.map(c => { const m = c.meta || {}; return `<div class="ap-row"><b title="${esc(c.name)}">${esc(c.name)}</b><span class="mut">${esc(m.address || "")}${m.port ? ":" + esc(m.port) : ""}</span><select data-cid="${c.id}" style="margin-left:auto"><option value="">—</option>${opts}</select></div>`; }).join("");
  showModal("<b>🔗 Link configs to tunnels</b>", `<div class="mut">Pick the tunnel each config goes through. The Doctor then compares the probe tests of the config with its own last week and says what its users get through that tunnel.</div>
    <div class="ap-att" style="margin-top:8px">${rows || '<div class="mut">Nothing to link.</div>'}</div>
    <div class="frm-act"><button class="sm pri" onclick="apLinkSave()">Save</button></div>`, "wide");
};
window.apLinkSave = async () => {
  const picks = [...document.querySelectorAll("select[data-cid]")].filter(s => s.value); let done = 0;
  for (const s of picks) { try { await api("/api/configs/" + s.dataset.cid, {method: "PUT", body: JSON.stringify({tunnel: s.value})}); done++; } catch (e) { toast("✕ " + e.message, "bad"); } }
  closeModal(); if (done) toast(`✓ ${done} config${done === 1 ? "" : "s"} linked - the Doctor looks at ${done === 1 ? "it" : "them"} within five minutes`, "ok"); refresh();
};
window.apReturn = async id => {
  if (!await gconfirm("Bring these tunnels back to the server they left? They are built there first, the DNS follows, and the old ones go after the TTL.", {danger: true})) return;
  try { const r = await api("/api/doctor/moves/" + encodeURIComponent(id) + "/return", {method: "POST"}); showModal("<b>↩ Return</b>", '<div id="ap-story" class="fix"><div class="fx-line run"><i></i>Starting…</div></div>', "wide"); followStory(r.id, $("#ap-story"), 900).then(() => refresh()); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.apStandbyBack = async name => {      // the "Back now" of a handover to a warm standby (the entry of the Moves tab)
  try { const t = (await api("/api/tunnels")).find(x => x.name === name); if (!t) throw new Error("this tunnel is not in the list any more"); await api(`/api/tunnels/${t.id}/standby/back`, {method: "POST"}); toast("✓ The users' ports are on " + name + " again", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
  refresh();
};
window.apPickMove = async (tid, name) => {
  const irn = (await api("/api/servers").catch(() => [])).filter(s => s.role === "iran" && s.enabled && s.online !== false);
  const v = await glassChoice(`Move ${name} to which Iran server?`, irn.map(s => ({label: s.name, v: s.id, note: s.host})));
  if (!v) return;
  showModal(`<b>🔁 ${esc(name)}</b>`, '<div id="ap-story" class="fix"></div>', "wide");
  apMoveTo(tid, +v, (irn.find(s => s.id === +v) || {}).name || "");
};
/* the Tunnel Doctor page fits the window: its height is what is left under the top, the log and the history scroll inside their own boxes */
function apFit(fo) { const pg = $("#page"); if (!pg) return; if (fo || !pg.querySelector(".ap-pair")) { pg.style.removeProperty("--ap-h"); return; } pg.style.setProperty("--ap-h", Math.max(440, Math.floor(mainBottom() - pg.getBoundingClientRect().top - 44)) + "px"); }
addEventListener("resize", () => { const pg = $("#page"); if (pg && pg.querySelector(".ap-pair") && pg.style.getPropertyValue("--ap-h")) apFit(false); });
/* "What if this Iran server went off?": the plan the Doctor would run - the tunnels, the target, the DNS records of every account (nothing is changed) */
window.foPreview = async () => {
  const id = ($("#fo-srv") || {}).value; if (!id) return;
  showModal("<b>👁 If it went off</b>", '<div class="loading"><i class="spin"></i><span>Looking at the tunnels and the DNS records of every account…</span></div>');
  try { const p = await api("/api/doctor/plan/" + id);
    showModal(`<b>👁 If ${esc(p.server)} (${esc(p.ip)}) went off</b>`, `<div class="mut">${p.target ? `${p.tunnels.length} tunnel(s) (${p.tunnels.map(t => esc(t.name)).join(", ") || "none"}) would move to <b>${esc(p.target.name)}</b> (${esc(p.target.ip)}).` : '<span class="err">No other Iran server could carry all of its tunnels.</span>'}</div>
      <div style="margin-top:8px">${p.dns.length ? "DNS records that would be changed:" + p.dns.map(d => `<div class="ap-row"><b>${esc(d.fqdn)}</b><span class="mut">${esc(d.account_name || d.account || "")}</span><span class="mut">${esc(d.ip)} → ${p.target ? esc(p.target.ip) : "?"}</span></div>`).join("") : "No DNS record points at this server."}</div>${p.problems.length ? `<div class="err" style="margin-top:8px">${p.problems.map(esc).join("<br>")}</div>` : ""}<small class="mut">Nothing was changed.</small>`); }
  catch (e) { showModal("<b>👁 If it went off</b>", `<div class="err">${esc(e.message)}</div>`); }
};
window.foUndo = async id => { if (!await gconfirm("Point the DNS records back at the old server address? The tunnels stay where they are.", {danger: true})) return; try { const r = await api("/api/failover/" + encodeURIComponent(id) + "/undo", {method: "POST"}); toast("✓ " + r.changed.length + " record(s) pointed back", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };/* a page in tabs: every top-level element of #page carries data-tab; the bar at the top shows one group at a time (the choice is kept in this browser) */
function pageTabs(key, tabs, dflt, counts = {}) {
  const saved = localStorage.getItem("nk.tab." + key), cur = tabs.some(t => t[0] === saved) ? saved : dflt;
  document.querySelectorAll("#page > [data-tab]").forEach(el => el.classList.toggle("hidden", el.dataset.tab !== cur));
  if (tabs.length > 1) $("#page").insertAdjacentHTML("afterbegin", `<div class="fstrip"><div class="tabs set-tabs">${tabs.map(([k, l]) => `<button class="${k === cur ? "on" : ""}" onclick="localStorage.setItem('nk.tab.${key}','${k}');refresh()">${l}${counts[k] ? ` <i class="cnt">${counts[k]}</i>` : ""}</button>`).join("")}</div></div>`);
  return cur;
}
/* DNS changes (next to the history): every record the failover re-pointed in the CDN, each with its own Restore */
const dnsCard = (fo, av = {}, irn = []) => {
  const rows = (fo && fo.dns_log) || [];
  const point = av.has_api_key ? `<div class="dns-point"><small class="mut">Point a name at a server now:</small><span class="sugg-wrap"><input id="dns-point-name" data-sug="one" placeholder="ca.example.ir" autocomplete="off" oninput="sugShow(this)" onfocus="sugShow(this)" onblur="sugHide(this)" onkeydown="sugKey(event, this)"><div class="sugg hidden"></div></span><select id="dns-point-srv">${irn.map(s => `<option value="${s.id}">${esc(s.name)} — ${esc(s.host)}</option>`).join("")}</select><button class="sm pri" onclick="foPoint()">Apply</button></div>` : "";
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
window.apDetail = id => {
  const t = (state.tunnelsCache || []).find(x => x.id === id); if (!t) return;
  const d = t.detail || {}, lines = [`Tunnel: ${t.name}`, `State: ${t.state}`, t.last_error ? `Last error: ${t.last_error}` : "", tunLoss(t).v != null ? `Packet loss: ${tunLoss(t).v} %` : tunLoss(t).blocked ? "Packet loss: not measurable (ping is blocked on this path; the data passes)" : "", t.rtt_ms != null ? `Delay: ${Math.round(t.rtt_ms)} ms` : "", (t.bw || {}).down != null ? `Bandwidth: ${bwFmt(t.bw.down)}${t.bw.ref ? ` (usually ${bwFmt(t.bw.ref)})` : ""}` : "", ["drop", "slow"].includes((t.bw || {}).state) ? bwWhy(t) : "", ["poor", "bad"].includes((t.ux || {}).grade) ? uxWhy(t) : ""].filter(Boolean);
  showModal(`<b>${esc(t.name)}</b>`, `<pre class="gl-out">${esc(lines.join("\n"))}</pre><div class="frm-act"><button class="sm" onclick="closeModal();apLive(${t.id}, '${esc(t.name)}')">📡 Live log</button><button class="sm" onclick="closeModal();apDiagnose(${t.id})">Diagnose</button></div>`, "mid");
};
window.apLive = async (id, name) => {
  /* the live log of what the Tunnel Doctor does with this tunnel: the running repair when there is one, else the last one (with a button to look again) */
  showModal(`<b>📡 ${esc(name)}</b>`, '<div id="ap-story" class="fix"><div class="fx-line run"><i></i>Looking for the repair of this tunnel…</div></div><div class="frm-act" style="margin-top:8px"><button class="sm" onclick="apDiagnose('+id+')">🩺 Check the tunnel now</button></div>', "wide");
  try {
    const list = (await api("/api/actions?limit=80&kinds=fix,autopilot,balance,underuse,move,speed")).actions.filter(a => a.ref === name && ["autopilot", "observe", "fix", "build", "create", "move", "speed"].includes(a.action));
    const box = $("#ap-story"), a = list.find(x => !x.done) || list[0];
    if (!a) { box.innerHTML = '<div class="mut" style="padding:8px">Nothing to show yet. As soon as this tunnel shows packet loss (5 % or more) the Tunnel Doctor looks at it and writes what it sees here; it acts by itself only when the loss is high enough for users to feel (15 %) or the tunnel is down.</div>'; return; }
    box.innerHTML = storyHtml(a);
    if (!a.done) followStory(a.id, box, 900).then(() => refresh());
  } catch (e) { const box = $("#ap-story"); if (box) box.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
};
window.apFixAll = async () => {
  try { const r = await api("/api/autopilot/fix-all", {method: "POST", body: JSON.stringify({level: "rebuild"})}); const box = $("#ap-all"); box.classList.remove("hidden"); box.innerHTML = '<div class="fx-line run"><i></i>Starting…</div>'; followStory(r.id, box, 900).then(() => refresh()); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.apExplain = async tid => {
  showModal("<b>💬 The AI's reading of the situation</b>", '<div class="loading"><i class="spin"></i><span>Collecting the facts and asking the AI… (about 20 s)</span></div>');
  try { const r = await api("/api/autopilot/explain", {method: "POST", body: JSON.stringify({tunnel_id: tid || null})}); showModal("<b>💬 The AI's reading of the situation</b>", `<div class="ai-say">${esc(r.text).replace(/\n/g, "<br>")}</div><small class="mut">Based on: ${r.facts.this_tunnel ? "this tunnel's diagnosis and " : ""}${(r.facts.everything_else || r.facts).tunnels.length} tunnel(s) not healthy, ${(r.facts.everything_else || r.facts).servers_offline.length} server(s) offline, ${(r.facts.everything_else || r.facts).configs_down.length} config(s) down.</small>`); }
  catch (e) { showModal("<b>💬 Explain with AI</b>", `<div class="err">${esc(e.message)}</div>${/no AI key/.test(e.message) ? '<div style="margin-top:10px"><button class="sm" onclick="closeModal();go(\'settings\');setTab(\'ai\')">Open Settings → Api & Key</button></div>' : ""}`); }
};
window.cfDiagnoseByName = name => { const c = (state.cfgCache || []).find(x => x.name === name); if (c) cfDiagnose(c.id); else api("/api/configs").then(d => { const x = d.configs.find(y => y.name === name); if (x) cfDiagnose(x.id); }); };
INFO.actions = ["Tunnel Doctor", "Finds why a tunnel is down and fixes it: restart, rebuild, another tunnel type or another Iran server - and says which tunnel is best on today's network."];
window.apUse = async (kind, tid) => {
  if (!tid) return;
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
      <div class="ibox checked" style="margin-top:10px"><h5><i>🔎</i>What was checked</h5><div class="chk-list">${d.findings.map(f => `<div class="chk-row ${f.ok ? "ok" : "bad"}"><b>${f.ok ? "✓" : "✕"}</b><span>${esc(f.text)}</span></div>`).join("")}</div></div>
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
window.apTune = id => { go("tunnels", "repairs"); };      // (the Network advisor of the diagnosis window: the Tunnel test page is gone, the Doctor is the place)
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
INFO.configs = ["Users", "The users of your Marzban and PasarGuard panels, and everything else the panels hold: hosts, groups, nodes, cores, admins and settings. Pick a panel in the bar."];      // the page is called Users (the Configs list is off: CONFIGS_UI)
const cf = {open: null, edit: null, sub: null, q: "", group: localStorage.getItem("nk.cfgroup") || "__all"};
const FLAGS = {de: "de", germany: "de", fi: "fi", finland: "fi", nl: "nl", netherlands: "nl", us: "us", usa: "us", unitedstates: "us", fr: "fr", france: "fr", gb: "gb", uk: "gb", unitedkingdom: "gb", england: "gb", tr: "tr", turkey: "tr", ae: "ae", uae: "ae", emirates: "ae", dubai: "ae", se: "se", sweden: "se", pl: "pl", poland: "pl", ru: "ru", russia: "ru", ir: "ir", iran: "ir", ca: "ca", canada: "ca", jp: "jp", japan: "jp", sg: "sg", singapore: "sg", hk: "hk", hongkong: "hk", it: "it", italy: "it", es: "es", spain: "es", at: "at", austria: "at", ch: "ch", switzerland: "ch", kz: "kz", kazakhstan: "kz", am: "am", armenia: "am", az: "az", ge: "ge", georgia: "ge", qa: "qa", qatar: "qa", bh: "bh", bahrain: "bh", om: "om", oman: "om", lt: "lt", lv: "lv", ee: "ee", cz: "cz", czech: "cz", czechia: "cz", ro: "ro", romania: "ro", bg: "bg", bulgaria: "bg", gr: "gr", greece: "gr", pt: "pt", portugal: "pt", ie: "ie", ireland: "ie", no: "no", norway: "no", dk: "dk", denmark: "dk", be: "be", belgium: "be", lu: "lu", ua: "ua", ukraine: "ua", md: "md", rs: "rs", serbia: "rs", in: "in", india: "in", kr: "kr", korea: "kr", au: "au", australia: "au", br: "br", brazil: "br", mx: "mx", za: "za", cn: "cn", china: "cn", tw: "tw", taiwan: "tw", vn: "vn", vietnam: "vn", th: "th", thailand: "th", my: "my", malaysia: "my", id: "id", indonesia: "id", ph: "ph", eg: "eg", egypt: "eg", sa: "sa", saudi: "sa", kw: "kw", kuwait: "kw", iq: "iq", iraq: "iq", pk: "pk", pakistan: "pk", il: "il", cy: "cy", cyprus: "cy", mt: "mt", malta: "mt", hu: "hu", hungary: "hu", sk: "sk", si: "si", hr: "hr", croatia: "hr", fin: "fi", ger: "de", ned: "nl", fra: "fr", tur: "tr", ita: "it", swe: "se", pol: "pl", can: "ca", aus: "au", usa2: "us"};
function cfCode(name) { for (const t of String(name || "").toLowerCase().split(/[^a-z]+/)) if (FLAGS[t]) return FLAGS[t].toLowerCase(); return ""; }      // the country of a config name, "" when none
function cfFlag(name) { for (const t of String(name || "").toLowerCase().split(/[^a-z]+/)) if (FLAGS[t]) return `<small class="cc" title="${esc(FLAGS[t].toUpperCase())}">${esc(FLAGS[t].toUpperCase())}</small> `; return ""; }   // the country code as small text instead of a flag picture: calmer lists
const pingCls = v => v == null ? "mut" : v < 800 ? "ok" : v < 1000 ? "warn" : "bad";   // config delay: under 800 ms green, 800-999 yellow, above red
const mb = v => v == null ? "—" : Number(v).toFixed(v >= 100 ? 0 : 1);
const stName = st => st === "degraded" ? "unstable" : st === "starting" ? "building tunnel" : st;      // what the screen calls the state "degraded"
const SRV_IC = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect class="b" x="3" y="3.5" width="18" height="7" rx="2.2"/><rect class="b" x="3" y="13.5" width="18" height="7" rx="2.2"/><path class="b" d="M12 7h6M12 17h6"/><circle class="led" cx="7" cy="7" r="1.15"/><circle class="led l2" cx="7" cy="17" r="1.15"/></svg>';
PAGES.configsList = async () => {
  const [d, subs, tl] = await Promise.all([api("/api/configs"), api("/api/subscriptions"), api("/api/tunnels").catch(() => [])]);
  
  const names = [...new Set([...subs.subscriptions.map(s => s.name), ...d.configs.map(c => c.group).filter(Boolean)])].sort((a, b) => a.localeCompare(b));
  if (cf.group !== "__all" && !names.includes(cf.group)) cf.group = "__all";
  const searched = d.configs;     // the search hides rows / cards at once (applyQ), it does not redraw
  const list = cf.group === "__all" ? searched : cf.group === "__manual" ? searched.filter(c => !c.group) : searched.filter(c => c.group === cf.group);
  const groups = [null];   // one flat list in every view; in "All" the group badge says where a config belongs
  const tunnelSel = c => `<select class="cf-tun" title="Tunnel this config goes through" onclick="event.stopPropagation()" onchange="event.stopPropagation();cfSetTunnel(${c.id}, this.value)"><option value="">no tunnel</option>${tl.map(t => `<option value="${esc(t.name)}" ${t.name === c.tunnel ? "selected" : ""}>${esc(t.name)}</option>`).join("")}${c.tunnel && !tl.some(t => t.name === c.tunnel) ? `<option selected>${esc(c.tunnel)}</option>` : ""}</select>`;
  const cdnIn = c => `<input class="cf-cdn" inputmode="numeric" maxlength="5" placeholder="CDN port" title="CDN port: type it and press Enter" value="${esc(c.cdn_port || "")}" data-orig="${esc(c.cdn_port || "")}" onclick="event.stopPropagation()" onkeydown="if(event.key==='Enter')this.blur()" onchange="cfSetCdn(${c.id}, this)">`;
  const live = c => `<span class="live ${c.status === "up" ? "up" : c.status === "down" ? "down" : "unk"}">● ${c.status === "up" ? "ONLINE" : c.status === "down" ? "OFFLINE" : "UNKNOWN"}</span>`;
  const shown = c => { const g = [c.group, ...names].filter(Boolean).find(x => c.name.startsWith(x + " ")); return g ? c.name.slice(g.length + 1) : c.name; };   // the group has its own column: never repeat it in the name
  const title = c => `${cfFlag(c.name)}${esc(shown(c))}${c.enabled ? "" : '<span class="pill">disabled</span>'}`;
  const inAll = cf.group === "__all";
  const gcell = c => inAll ? `<td class="cf-sub">${c.group ? esc(c.group) : "—"}</td>` : "";
  const acts = c => `<button class="sm" onclick="event.stopPropagation();cfSpeed(${c.id})" title="Speed test now">Test</button><button class="sm danger" onclick="event.stopPropagation();cfDel(${c.id}, '${esc(c.name)}')">Delete</button>`;
  const MBs = v => v == null ? "—" : mb(v / 8);   // the table speaks MB/s like a v2ray client; the stat boxes elsewhere stay in Mb/s
  const row = c => { const m = c.meta || {}; return `<tr class="${c.status === "up" ? "up" : c.status === "down" ? "down" : ""} click" data-q="${esc([c.name, c.group, c.address, c.port, c.status, c.protocol, c.network, c.security, c.tunnel, c.cdn_port].filter(x => x != null && x !== "").join(" ").toLowerCase())}" onclick="cfToggle(${c.id})">
      <td><span class="td-type">${esc((m.proto || "—").toUpperCase())}</span></td>
      <td class="name"><div class="cf-nm"><i class="sdot ${c.status}" title="${esc(c.status)}"></i>${title(c)}</div></td>
      ${gcell(c)}
      <td class="mono" title="${esc(m.address || "")}">${esc(m.address || "—")}</td>
      <td class="num">${m.port || "—"}</td>
      <td>${esc(m.network || "—")}</td>
      <td>${m.security && m.security !== "none" ? `<b class="sec-${esc(m.security)}">${esc(m.security)}</b>` : '<span class="mut">none</span>'}</td>
      <td class="num"><b class="${pingCls(c.status === "up" ? c.ping_ms : null)}">${c.ping_ms != null && c.status === "up" ? Math.round(c.ping_ms) : "—"}</b> <a class="mut cf-rf" title="Refresh ping" onclick="event.stopPropagation();cfSync()">↻</a></td>
      <td class="num">${c.down_mbps != null ? "↓" + MBs(c.down_mbps) + " ↑" + MBs(c.up_mbps) : "—"}</td>
      <td class="cf-acts">${acts(c)}</td></tr>`; };  const rowC = c => `<tr class="${c.status === "up" ? "up" : c.status === "down" ? "down" : ""} click" data-q="${esc([c.name, c.group, c.address, c.port, c.status, c.protocol, c.network, c.security, c.tunnel, c.cdn_port].filter(x => x != null && x !== "").join(" ").toLowerCase())}" onclick="cfToggle(${c.id})">
      <td class="name">${title(c)}</td>${gcell(c)}<td>${live(c)}</td>
      <td class="num"><b class="${pingCls(c.status === "up" ? c.ping_ms : null)}">${c.ping_ms != null && c.status === "up" ? Math.round(c.ping_ms) + " ms" : "—"}</b></td>
      <td class="num hide-sm">${c.down_mbps != null ? "↓" + mb(c.down_mbps) + " ↑" + mb(c.up_mbps) : "—"}</td>
      
      <td class="mut" title="${esc(c.last_error)}">${c.last_error && c.status === "down" ? esc(c.last_error).slice(0, 40) : (c.checked_at ? ago(c.checked_at) + " ago" : "—")}</td>
      <td class="cf-acts">${acts(c)}</td></tr>`;
  const up = d.configs.filter(c => c.status === "up"), down = d.configs.filter(c => c.status === "down"), pings = up.filter(c => c.ping_ms != null).map(c => c.ping_ms);
  cf.view = cf.view || localStorage.getItem("nk.cfview") || "list";
  const card = c => `<div class="cf-card ${c.status}" data-q="${esc([c.name, c.group, c.address, c.port, c.status, c.protocol, c.network, c.security, c.tunnel, c.cdn_port].filter(x => x != null && x !== "").join(" ").toLowerCase())}" onclick="cfToggle(${c.id})"><div class="cf-top"><b>${title(c)}</b>${live(c)}</div>${inAll && c.group ? `<small class="cf-sub">${esc(c.group)}</small>` : ""}
      <div class="cf-stats"><div class="cf-stat"><b class="${pingCls(c.status === "up" ? c.ping_ms : null)}">${c.ping_ms != null && c.status === "up" ? Math.round(c.ping_ms) : "—"}<small>ms</small></b><span>PING</span></div><div class="cf-stat"><b>${mb(c.down_mbps)}</b><span>DOWN Mbps</span></div><div class="cf-stat"><b>${mb(c.up_mbps)}</b><span>UP Mbps</span></div></div>
      <div class="cf-acts">${acts(c)}<span class="sp"></span><small class="mut">${c.checked_at ? ago(c.checked_at) + " ago" : "—"}</small></div></div>`;
  const sub = names.includes(cf.group) ? subs.subscriptions.find(s => s.name === cf.group) : null;
  const chip = (label, n, key, removable) => `<span class="gchip ${cf.group === key ? "on" : ""}"><button onclick="cfGroup('${esc(key)}')">${esc(label)}<small>${n}</small></button>${removable ? `<a title="Remove group" onclick="cfGroupDel('${esc(key)}')">×</a>` : ""}</span>`;
  const gbar = names.length ? `<div class="gbar">${chip("All", searched.length, "__all", false)}<i class="gsep"></i>${names.map(n => chip(n, searched.filter(c => c.group === n).length, n, true)).join("")}
      ${names.includes(cf.group) ? `<span class="sp"></span><button class="sm" onclick="cfSubRefresh(${sub ? sub.id : 0}, '${esc(cf.group)}')" title="Add the new nodes of this subscription at once"><i class="bi">${SI.restore}</i>Sync</button><button class="sm" onclick="cfSubManage('${esc(cf.group)}')" title="Tick which nodes of this group are kept on the probe"><i class="bi">${SI.edit}</i>Edit</button>` : ""}</div>` : "";
  $("#page").innerHTML = `<div class="toolbar cf-top">
      <button class="${cf.edit === "new" ? "on" : ""}" onclick="cfNew()"><i class="bi">${SI.plus}</i>Add</button><button data-t="Ping all" onclick="cfSync()" title="Ask the probe for the live state of every config"><i class="bi">${SI.restore}</i>Ping all</button><button data-t="Test all" onclick="cfSpeedAll()" title="Speed test of every config in this view, one after the other" ${list.length ? "" : "disabled"}><i class="bi">${SI.bolt}</i>Test all</button><button class="danger" onclick="cfDeleteAll()" ${list.length ? "" : "disabled"}><i class="bi">${SI.trash}</i>Delete all</button>
      <span class="tcount pstat cf-stats"><span class="tc info" title="${names.length} groups · probe: ${esc(d.probe.active || "none")}">Total <b>${d.configs.length}</b></span><span class="tc ${up.length ? "ok" : "bad"}" title="Answer from inside Iran">Online <b>${up.length}</b></span><span class="tc bad" title="${down.length ? esc(down.slice(0, 6).map(c => c.name).join(", ") + (down.length > 6 ? "…" : "")) : "none"}">Offline <b>${down.length}</b></span><span class="tc ${!pings.length ? "" : (pings.reduce((a, b) => a + b, 0) / pings.length) >= 1000 ? "bad" : (pings.reduce((a, b) => a + b, 0) / pings.length) >= 800 ? "warn" : "ok"}" title="Average over ${pings.length} online configs: under 800 ms green, under 1000 yellow, above red">Avg ping <b>${pings.length ? Math.round(pings.reduce((a, b) => a + b, 0) / pings.length) + " ms" : "—"}</b></span></span>
      <span class="sp"></span><div class="tabs" title="Display">${[["grid", "Grid", SI.vgrid], ["compact", "Compact", SI.vcompact], ["list", "List", SI.vlist]].map(([k, l, ic]) => `<button class="${cf.view === k ? "on" : ""}" onclick="cfView('${k}')"><i class="bi">${ic}</i>${l}</button>`).join("")}</div><span class="tb-search"><input id="cq" type="search" placeholder="Search…" value="${esc(cf.q)}" oninput="cfQ(this.value)" autocomplete="off" spellcheck="false"></span></div>
    ${gbar}
    ${cf.sub && cf.edit !== "new" ? cfSubHtml() : ""}
    ${list.length ? groups.map(() => `${cf.view === "grid" ? `<div class="cf-grid">${list.map(card).join("")}</div>`
      : cf.view === "compact" ? `<table class="tbl lift compact"><colgroup>${inAll ? '<col style="width:22%"><col style="width:14%">' : '<col style="width:30%">'}<col style="width:9%"><col style="width:8%"><col style="width:12%"><col><col style="width:150px"></colgroup>
      <thead><tr><th>Config</th>${inAll ? "<th>Subs group</th>" : ""}<th>Status</th><th>Ping</th><th>Speed</th><th>Note</th><th></th></tr></thead><tbody>${list.map(rowC).join("")}</tbody></table>`
      : `<table class="tbl lift cf-list srv-tbl">${inAll ? '<colgroup><col style="width:70px"><col style="width:17%"><col style="width:12%"><col style="width:18%"><col style="width:5%"><col style="width:6%"><col style="width:6%"><col style="width:7%"><col style="width:10%"><col style="width:150px"></colgroup>' : '<colgroup><col style="width:6%"><col style="width:22%"><col style="width:22%"><col style="width:6%"><col style="width:8%"><col style="width:7%"><col style="width:7%"><col style="width:12%"><col style="width:150px"></colgroup>'}
      <thead><tr><th>Type</th><th>Remarks</th>${inAll ? "<th>Subs group</th>" : ""}<th>Address</th><th>Port</th><th>Transport</th><th>TLS</th><th>Delay (ms)</th><th>Speed (MB/s)</th><th style="text-align:right">Actions</th></tr></thead>
      <tbody>${list.map(row).join("")}</tbody></table>`}`).join("") : `<div class="empty">${d.configs.length ? "No configs in this view" : "No config yet. Add a link or import a subscription."}</div>`}
    ${subs.subscriptions.length && cf.group === "__all" ? `<div class="card" style="margin-top:14px"><h3>Subscriptions</h3>${subs.subscriptions.map(s => `<div class="ev info"><time>${s.last_import ? ago(s.last_import) + " ago" : "—"}</time><i></i><small>${s.nodes.length} nodes</small><span><b>${esc(s.name)}</b> <span class="mut">${esc(s.url_host)}</span> <a class="mut" onclick="cfSubRefresh(${s.id}, '${esc(s.name)}')">import new nodes</a> · <a class="mut" onclick="cfSubDel(${s.id})">forget</a></span></div>`).join("")}</div>` : ""}`;
  applyQ();
  cf.names = names; cf.cache = d.configs;
  const openC = d.configs.find(c => c.id === cf.open);
  if (cf.add) cfAddModal();
  else if (openC) showModal(`<span class="st ${openC.status}"></span><b>${esc(shown(openC))}</b> <span class="pill">${esc(openC.group || "manual")}</span>${cf.edit === openC.id ? '<span class="pill">editor</span>' : ""}`, `<div id="cf-detail-${openC.id}">${cf.edit === openC.id ? '<div class="mut">loading the link…</div>' : cfDetailHtml(openC)}</div>`, "wide",
    cf.edit === openC.id ? "" : `<button class="sm" onclick="cfEdit(${openC.id})" title="Change the link field by field, rename, group"><i class="bi">${SI.edit}</i>Edit</button><button class="sm danger" onclick="cfDel(${openC.id}, '${esc(openC.name)}')" title="Delete it from the probe; its history goes too"><i class="bi">${SI.trash}</i>Delete</button>`); else hideModal();
  if (cf.open && cf.edit === cf.open) cfLoadEditor(cf.open);
  else if (cf.open) cfLoadChart(cf.open);
};
/* a config window is ONE view, like the server and tunnel windows: one bar of tests, three boxes of six rows, the monitoring; Edit and Delete are in its header */
function cfDetailHtml(c) {
  return cfPartHtml(c, "actions") + cfPartHtml(c, "info") + `<div class="wsec"><h4>Monitoring</h4>${cfPartHtml(c, "charts")}</div>`;
}
function cfPartHtml(c, tab) {
  const m = c.meta || {};
  const b = (icon, title, tip, click) => `<button class="sm abtn" data-t="${title}" onclick="${click}" title="${esc(tip)}"><i class="bi">${{"⚡": SI.bolt, "↻": SI.restore, "🔗": SI.link}[icon] || icon}</i>${title}</button>`;
  if (tab === "charts") return `<div id="cf-charts-${c.id}"><div class="mut">loading…</div></div>`;
  if (tab === "actions") return `<div class="sbar cfbar">${b("⚡", "Speed test", "Download and upload through this config from inside Iran (1-3 min)", `cfSpeed(${c.id})`)}${b("↻", "Ping now", "Ask the probe for the live state of every config", "cfSync()")}${b("🔗", "Copy link", "The vless:// vmess:// trojan:// ss:// link", `cfCopy(${c.id})`)}</div>`;
  const kv = (l, v) => `<div class="kv2"><span>${l}</span><div>${v}</div></div>`, box = (icon, title, rows) => `<div class="sbox" data-b="${title.split(" ")[0].toLowerCase()}"><h5><i>${icon}</i>${title}</h5>${rows.join("")}</div>`;
  const ok = c.status === "up";
  return `<div class="sgrid">${[
    box('<svg viewBox="0 0 16 16"><path d="M6 2.5h4v2.2a1.5 1.5 0 110 2.6V9.5H6V7.3a1.5 1.5 0 110-2.6zM3 9.5h10v4H3z"/></svg>', "Config", [kv("Type", esc((m.proto || "—").toUpperCase())), kv("Address", `<span class="mono">${esc(m.address || "—")}</span>`), kv("Port", m.port || "—"), kv("Transport", esc(m.network || "—")), kv("TLS", m.security && m.security !== "none" ? `<b class="sec-${esc(m.security)}">${esc(m.security)}</b>` : "none"), kv("Group", c.group ? esc(c.group) : "—")]),
    box(SI.clock, "Status", [kv("State", `<span class="st ${c.status}">${esc(c.status)}</span>`), kv("Since", c.status_since ? ago(c.status_since) + " ago" : "—"), kv("Delay", c.ping_ms != null && ok ? `<b class="${pingCls(c.ping_ms)}">${Math.round(c.ping_ms)} ms</b>` : "—"), kv("Last check", c.checked_at ? ago(c.checked_at) + " ago" : "—"),
      kv("Error", c.last_error ? `<span class="err" title="${esc(c.last_error)}">${esc(c.last_error.slice(0, 44))}</span>` : "—"), kv("Tunnel", c.tunnel ? esc(c.tunnel) : "—")]),
    box(SI.bolt, "Speed & routing", [kv("Download", c.down_mbps != null ? `${mb(c.down_mbps / 8)} MB/s <small class="mut">${mb(c.down_mbps)} Mb/s</small>` : "not measured"), kv("Upload", c.up_mbps != null ? `${mb(c.up_mbps / 8)} MB/s <small class="mut">${mb(c.up_mbps)} Mb/s</small>` : "—"), kv("Measured", esc((c.speed_at || "").slice(0, 16).replace("T", " ") || "—")),
      kv("CDN port", esc(c.cdn_port || "—")), kv("Probe port", c.port || "—"), kv("Slug", `<span class="mono">${esc(c.slug || "—")}</span>`)]),
  ].join("")}</div>`;
}async function cfLoadChart(id) {
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
    <div class="mgrid">${chartCard("Delay", rows.map(r => [r[0], r[1] ? r[2] : null]), {col: "blue", ic: SI.pulse, unit: " ms", warn: 800, bad: 1000, fmt: v => Math.round(v)})}
      <div class="mcard"><h6>Outages <span class="mval">${out.length}</span></h6>${out.length ? out.slice(-4).reverse().map(o => `<div class="irow"><span>${fmtT(o.from, 1)}</span><div class="err">${durText(o.to - o.from)}${o.open ? " (still down)" : ""}</div></div>`).join("") : '<div class="mut chart-empty">no outage in this range</div>'}</div></div>`;
}window.cfQ = v => { holdBoxes(); cf.q = v.trim().toLowerCase(); applyQ(); };
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
INFO.panels = ["Users", "Your Marzban and PasarGuard panels: are they up, are their nodes connected, their users, the hosts in the subscription links, and the Xray outbounds of the core — every core change is backed up first."];
const GB = 1024 ** 3, gb = v => v == null ? "∞" : v === 0 ? "∞" : (v / GB).toFixed(v / GB >= 100 ? 0 : 1) + " GB";
/* ---- the PasarGuard sections are the tabs of the Panel page (1.47.0; in 1.45.0 they were pages of the left menu, roadmap #102): every tab shows the bar of the panels (which panel) and its own tab(s) of what was one page
   with ten tabs - PG_SUBS says which tabs (of pgTabs) each tab of the Panel page holds. Nodes are a tab of Servers (the server beside the node it runs). pn.want = the tab a link asked for; pnShow() settles pn.tab
   for the part that is open. */
const PG_SUBS = {users: ["users"], configs: ["kinds", "hosts", "groups", "cores"], admins: ["admins"], settings: ["usage", "settings", "templates"]};      // (Templates moved from Access to General and became a section of Settings, and the System tab went - its Users box is in Usage > Users: the owner, 2026-10-15)
const PG_SUB_OF = {}; Object.entries(PG_SUBS).forEach(([s, tabs]) => tabs.forEach(t => { PG_SUB_OF[t] = s; }));
const pnPageTabs = () => state.page === "pgpanel" ? PG_SUBS[sub("pgpanel")] || ["users"] : ["users"];      // the tabs of the PasarGuard part that is on the screen
const pnIsUsers = () => (state.page === "pgpanel" && sub("pgpanel") === "users") || (state.page === "configs" && sub("configs") === "panels");
const pnManage = () => state.page !== "pgpanel" || sub("pgpanel") === "settings";      // General is where a panel is added, edited and removed (the owner, 2026-10-15); every other tab only SWITCHES between the PasarGuard panels, in ONE row with what the tab has
const pnShow = tabs => { if (pn.want && tabs.includes(pn.want)) pn.tab = pn.want; else if (!tabs.includes(pn.tab)) pn.tab = tabs[0]; pn.want = null; };
PAGES.panels = async () => {
  const every = (await api("/api/panels")).panels; state.panelsCache = every;
  pnShow(pnPageTabs());
  const manage = pnManage(), list = manage ? every : every.filter(x => x.type === "pasarguard");      // outside General the bar switches between the PasarGuard panels only
  if (pn.id === "*" && list.length > 1 && pnIsUsers()) return pnAllRender(list);       // "All panels": the accounts over every panel (roadmap M12)
  if (pn.id === "*") pn.id = "";
  const sel = list.find(p => String(p.id) === pn.id) || list[0];
  if (sel && String(sel.id) !== pn.id) { pn.id = String(sel.id); pn.users = pn.hosts = pn.core = pn.user = pn.logs = null; }
  const chip = p => p.status === "up" ? `<span class="pill ok" title="${esc((p.version ? "version " + p.version + " · " : "") + (p.checked_at ? "checked " + ago(p.checked_at) + " ago" : ""))}">●<span class="up"> up ·</span> ${p.latency_ms} ms</span>` : p.status === "down" ? `<span class="pill err" title="${esc(p.last_error || "")}">● down</span>` : '<span class="pill">not checked yet</span>';
  if (!list.length) { $("#page").innerHTML = manage ? pnBar([], "", "") + pnFirstCard() : pnEmptyPage(); if (!manage) pnFuse(); if (typeof fxFit === "function") fxFit(); return; }      // (no panel: the boxes of the tab stay, empty, with the way to add one - the owner, 2026-10-15)
  const p = sel, s = p.stats || {};
  const nodesOk = (p.nodes || []).filter(n => n.fails === 0 || ["connected", "healthy"].includes(n.status)).length, st = (label, val, tip = "", cls = "", click = "") => `<span class="dns-stat ${cls}" ${click} title="${esc(tip)}"><i class="lab">${label}</i> <b>${val}</b></span>`;
  const facts = `${chip(p)}${pnAuthPill(p)}<span class="dns-stats">${st("Users", (+s.users_total || 0).toLocaleString(), `${s.users_active ?? "—"} active · ${s.users_online ?? "—"} online right now`, "st-users")}${st("Nodes", `${nodesOk} / ${(p.nodes || []).length}`, "Open the nodes of this panel (green: all connected)", "st-nodes " + (nodesOk === (p.nodes || []).length ? "ok" : "bad"), 'onclick="pnNodesWin()"')}${st("Traffic", s.bw_in != null ? fmtB(s.bw_in + (s.bw_out || 0)) : "—", `↓${s.bw_in != null ? fmtB(s.bw_in) : "—"} ↑${s.bw_out != null ? fmtB(s.bw_out) : "—"}`, "st-traffic")}</span>`;
  const ob = $("#pn-body"), changed = pn.lastTab !== pn.tab || pn.lastId !== String(p.id); pn.lastTab = pn.tab; pn.lastId = String(p.id);
  const keep = ob && !ob.querySelector(":scope > .loading") ? (() => { const c = ob.cloneNode(true); c.querySelectorAll(":scope > .pn-bars").forEach(x => x.remove()); return c.innerHTML; })() : "";      // what the tab that was open showed stays on the screen - dimmed when another tab or panel was chosen - until the new one has its data: no spinner between the tabs of the Panel (the owner, 2026-10-16)
  $("#page").innerHTML = pnBar(list, p.id, manage ? facts : "", manage ? "manage" : "pick") + `    ${p.last_error && p.status === "down" ? `<div class="gw-out bad">✕ ${esc(p.last_error)}</div>` : ""}
    <div id="pn-body" class="${keep && changed ? "pn-busy" : ""}"><div class="pn-bars">${pnTabs()}</div>${keep || '<div class="loading"><i class="spin"></i><span>Loading…</span></div>'}</div><div id="pn-foot" class="fstrip pn-foot"></div>`;
  pnFuseWatch();
  const tdef = window.pgTabs && window.pgTabs[pn.tab], here = $("#pn-body");
  if (here && tdef && tdef.types && !tdef.types.includes(p.type)) { here.innerHTML = `<div class="pn-bars">${pnTabs()}</div><div class="empty">${esc(tdef.label)} is a part of PasarGuard panels; ${esc(p.name)} is a ${esc(p.type)} panel. Pick a PasarGuard panel in the bar above.</div>`; return; }
  // the heavy lists are read from the panel only when their tab is open (and kept until something changes)
  if (pn.tab === "users") pnUsersLoad(p.id);
  if (pn.tab === "hosts") pnHostsLoad(p.id);
  if (window.pgTabs && window.pgTabs[pn.tab]) { const box = $("#pn-body"); if (box) window.pgTabs[pn.tab].render(p.id, box).catch(e => pgError(box, e)); }      // a tab of a pg_*.js file draws itself
};
function pnOverviewHtml(p) {
  const s = p.stats || {}, nodes = p.nodes || [], bad = nodes.filter(n => !["connected", "healthy", "disabled"].includes(n.status));
  return `<div class="card"><h3>Nodes <span class="sp"></span><span class="pill ${bad.length ? "err" : "ok"}">${bad.length ? bad.length + " not connected" : nodes.length ? "all connected" : "none"}</span></h3>${pnNodesHtml(p)}</div>
    <div class="card"><h3>About this panel</h3><div class="kv"><div><span>type</span> ${esc(p.type)} · <span>version</span> ${esc(p.version || "—")}</div><div><span>users</span> ${s.users_total ?? "—"} total · ${s.users_active ?? "—"} active · ${s.users_online ?? "—"} online right now</div>
      ${CONFIGS_UI ? `<div><span>test config</span> ${s.test_user ? `<b class="gw-ok">✓</b> <span class="mono">${esc(s.test_user)}</span> — kept alive and measured on the probe as the group <b>${esc(p.name)}</b>` : "none yet — press <b>Test config on probe</b> above"}</div>` : ""}
      <div><span>checked</span> ${p.checked_at ? ago(p.checked_at) + " ago" : "never"} · every 2 minutes${p.last_error ? ` · <span class="err">${esc(p.last_error)}</span>` : ""}</div></div>
      <small class="mut">Open <b>Users</b> for the user list (search, status filter, edit, bulk actions) and <b>Hosts</b> for the address:port of every subscription link — they are read from the panel only when you open them.</small></div>`;
}
function fmtB(b) { if (b == null) return "—"; const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; } return b.toFixed(i >= 3 ? 2 : 0) + " " + u[i]; }
function pnNodesHtml(p) {
  const rows = (p.nodes || []).map(n => { const ok = ["connected", "healthy", "disabled"].includes(n.status); return `<tr class="${ok ? (n.status === "disabled" ? "" : "up") : "down"}"><td class="name">${esc(n.name)}</td><td><span class="st ${ok ? (n.status === "disabled" ? "" : "up") : "down"}">${esc(n.status || "?")}</span></td><td class="mut">${esc(n.address)}</td><td class="mut">${esc(n.xray || "")}</td><td class="mut" title="${esc(n.message)}">${esc(n.message).slice(0, 60)}${n.fails ? ` <span class="pill err">${n.fails} checks</span>` : ""}</td>
      <td><button class="sm" onclick="pnNodeLogs(${p.id}, ${n.id})">Log</button> <button class="sm" onclick="pnNodeReconnect(${p.id}, ${n.id})">Reconnect</button></td></tr>`; }).join("");
  return `<table class="tbl"><colgroup><col style="width:18%"><col style="width:12%"><col style="width:18%"><col style="width:10%"><col><col style="width:200px"></colgroup><thead><tr><th>Node</th><th>Status</th><th>Address</th><th>Xray</th><th>Message</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="6" class="mut">no nodes</td></tr>'}</tbody></table>
    ${pn.logs ? `<div class="card"><h3>Log of ${esc(pn.logs.name)} <span class="sp"></span><button class="sm" onclick="pn.logs=null;refresh()">Close</button></h3><pre class="gl-out">${esc(pn.logs.text || "(empty)")}</pre></div>` : ""}`;
}
window.pnPick = v => { pn.id = v; localStorage.setItem("nk.pn", v); pn.users = pn.hosts = pn.core = pn.user = pn.logs = null; pn.usersKey = pn.hostsKey = ""; pn.edit = pn.add = false; refresh(); };      // (another panel: the page and its tab stay)
window.pnTab = t => {      // a tab of the part that is open stays here; a tab that belongs to another tab of the Panel page opens that
  pn.user = null; pn.hostEdit = null; if (t !== "kinds") state.build = null;
  if (pnPageTabs().includes(t)) { pn.tab = t; refresh(); return; }
  if (PG_SUB_OF[t]) { pn.want = t; go("pgpanel", PG_SUB_OF[t]); return; }
  pn.tab = t; refresh();
};
PAGES.pgpanel = async () => { if (window.labLockNow && await labLockNow()) return; renderSub(); return PAGES.panels(); };      // the Panel page: the bar of tabs under the header, then the panel you pick and what the tab holds
window.pnNodesWin = () => { const pl = Array.isArray(state.panelsCache) ? state.panelsCache : (state.panelsCache || {}).panels || [], p = pl.find(x => String(x.id) === pn.id); if (!p) return; if (window.pgSrvPanelNodes) pgSrvPanelNodes(p.id); else showModal(`<b>Nodes of ${esc(p.name)}</b>`, pnNodesHtml(p), "wide"); };      // (the nodes of a panel, each beside the server it runs on: pg_srvnode.js)
window.pnReload = () => { pn.users = pn.hosts = null; pn.usersKey = pn.hostsKey = ""; refresh(); };
window.pnCheck = async id => { toast("Asking the panel…"); try { const r = await api(`/api/panels/${id}/check`, {method: "POST"}); toast(r.ok ? `✓ answers in ${r.latency_ms} ms · ${r.nodes.length} nodes` : "✕ " + r.error, r.ok ? "ok" : "bad"); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* How a panel is connected (docs/panels.md): the panel is given a credential, not a password. PasarGuard has API keys (pg_key_…, header X-Api-Key, their own end date, revocable);
   both panels accept the admin's access token. "Sign in once" gets a token with a username and password and forgets the password. */
const PN_HINT = {
  pasarguard: {key: "In the PasarGuard dashboard open <b>API Keys</b> and make one: it is shown only once. A key has its own end date and can be revoked there; nothing else is stored here.",
               login: "The password is used once to get a token, then forgotten. That token lasts only as long as the panel's JWT setting (24 h by default): an <b>API key</b> is the better way."},
  marzban: {key: "Marzban has no API keys: paste an access token (the panel's sign-in, <i>POST /api/admin/token</i>, gives one). It lasts as long as the panel's JWT setting says.",
            login: "The password is used once to get a token, then forgotten: only the token is kept. It lasts as long as the panel's JWT setting says."}
};
const pnSpan = s => { s = Math.abs(s); const d = Math.floor(s / 86400); return s < 3600 ? Math.max(1, Math.floor(s / 60)) + " min" : s < 86400 ? Math.floor(s / 3600) + " h" : d + (d === 1 ? " day" : " days"); };
const pnHint = f => { const h = f.querySelector(".pn-hint"), t = f.elements.type ? f.elements.type.value : f.dataset.type; if (h) h.innerHTML = (PN_HINT[t] || PN_HINT.marzban)[f.dataset.mode || "key"]; };
window.pnMode = (f, mode) => { f.dataset.mode = mode; f.querySelectorAll(".pn-mode button").forEach(b => b.classList.toggle("on", b.dataset.m === mode)); pnHint(f); const i = f.querySelector(mode === "key" ? "input[name=token]" : "input[name=username]"); if (i) i.focus(); };
const pnCredHtml = edit => `<div class="pn-wide tabs pn-mode"><button type="button" class="on" data-m="key" onclick="pnMode(this.form, 'key')">${edit ? "New API key / token" : "API key / token"}</button><button type="button" data-m="login" onclick="pnMode(this.form, 'login')">Sign in once</button></div>
      <label class="pn-wide pn-key">${edit ? "New API key or token <small>(empty keeps the current one)</small>" : "API key or access token"}<input name="token" type="password" autocomplete="off" spellcheck="false" placeholder="pg_key_…   or   xxxxx.yyyyy.zzzzz"></label>
      <label class="pn-login">Admin user<input name="username" autocomplete="off"></label><label class="pn-login">Password<input name="password" type="password" autocomplete="new-password"></label>
      <small class="pn-wide pn-hint"></small>`;
window.pnAddWin = () => {
  showModal("<b>Add a panel</b>", `<form class="frm pn-win" data-mode="key" onsubmit="return pnAdd(event)">
      <label>Name<input name="name" required placeholder="shop" autocomplete="off"></label><label>Type<select name="type" onchange="pnHint(this.form)"><option value="pasarguard">PasarGuard</option><option value="marzban">Marzban</option></select></label><label class="pn-wide">Address<input name="url" required placeholder="https://panel.example:8000" autocomplete="off"></label>
      ${pnCredHtml(false)}
      <label class="chk"><input name="verify_tls" type="checkbox" checked> verify the TLS certificate</label>
      <div class="frm-act"><button class="pri" type="submit">Connect and add</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`, "narrow");
  const f = document.querySelector("#modal .pn-win"); if (f) pnHint(f);
  setTimeout(() => { const i = document.querySelector("#modal .pn-win input[name=name]"); if (i) i.focus(); }, 40);
};
const pnConnHtml = p => {
  const a = p.auth || {}, tone = a.level === "bad" ? "err" : a.level === "warn" ? "warn" : "ok";
  const what = a.kind === "api_key" ? `API key${a.key_name ? ` “${esc(a.key_name)}”` : ""}` : a.kind === "token" ? "Access token" : a.kind === "login" ? "Saved login" : "No credential";
  const who = a.admin ? ` · admin <b>${esc(a.admin)}</b>${a.access ? ` (${esc(a.access)})` : ""}` : "";
  const sub = a.kind === "login" ? "Only a login is kept: the panel signs in by itself. Give it a key or token below and the login can be forgotten." : a.login ? "A login is also saved here: it renews the key / token if that ends." : a.kind === "none" ? "Paste an API key or a token below." : "";
  const btn = a.login ? `<button type="button" class="sm danger" onclick="pnForgetAsk(${p.id}, '${esc(p.name)}')" title="Wipe the password from this panel; it keeps the ${a.kind === "login" ? "token the login gives now" : "key / token it has"}">${a.kind === "login" ? "Keep only a token" : "Forget the saved login"}</button>` : "";
  return `<div class="pn-conn-t"><span><b>${what}</b>${who}<span class="pill ${tone}">${esc(a.note || "")}</span></span>${sub ? `<small>${sub}</small>` : ""}</div>${btn}`;
};
const pnAuthPill = p => {      // the small mark in the bar of a panel: how it is connected (grey = only a saved login), amber / red when a key or token is about to end
  const a = p.auth || {}, tone = a.level === "bad" ? "err" : a.level === "warn" ? "warn" : a.kind === "login" ? "" : "ok";
  const what = a.kind === "api_key" ? "API key" : a.kind === "token" ? "token" : a.kind === "login" ? "saved login" : "no credential";
  const left = a.level !== "ok" && a.left_s != null && !a.login ? " · " + (a.left_s <= 0 ? "ended" : pnSpan(a.left_s)) : "";
  const tip = (a.note || "") + (a.admin ? " · admin " + a.admin + (a.access ? " (" + a.access + ")" : "") : "") + (a.key_name ? " · key “" + a.key_name + "”" : "") + " — click to change how this panel is connected";
  return `<span class="pill ${tone} pn-auth" onclick="pnEditWin()" title="${esc(tip)}">${what}${left}</span>`;
};
window.pnEditWin = () => {
  const p = (state.panelsCache || []).find(x => String(x.id) === pn.id); if (!p) return;
  showModal(`<b>Edit ${esc(p.name)}</b>`, `<form class="frm pn-win" data-mode="key" data-type="${esc(p.type)}" onsubmit="return pnEdit(event, ${p.id})">
      <label>Name<input name="name" value="${esc(p.name)}" autocomplete="off"></label><label>Address<input name="url" value="${esc(p.url)}" autocomplete="off"></label>
      <div class="pn-wide pn-conn">${pnConnHtml(p)}</div>
      ${pnCredHtml(true)}
      <label class="chk"><input name="verify_tls" type="checkbox" ${p.verify_tls ? "checked" : ""}> verify the TLS certificate</label><label class="chk"><input name="enabled" type="checkbox" ${p.enabled ? "checked" : ""}> enabled (checked every 2 min)</label>
      <div class="frm-act"><button class="pri" type="submit">Save</button><button type="button" onclick="closeModal()">Cancel</button><span class="sp"></span><button type="button" class="danger" onclick="closeModal();pnDel(${p.id}, '${esc(p.name)}')">Remove from the list</button></div></form>`, "narrow");
  const f = document.querySelector("#modal .pn-win"); if (f) { pnHint(f); if (f.elements.username) f.elements.username.value = p.username || ""; }
};
window.pnAdd = async e => {
  e.preventDefault(); const f = new FormData(e.target), mode = e.target.dataset.mode || "key", body = {name: f.get("name").trim(), type: f.get("type"), url: f.get("url").trim(), verify_tls: !!f.get("verify_tls")};
  if (mode === "key") { body.token = (f.get("token") || "").trim(); if (!body.token) { toast("✕ Paste the API key or the access token first", "bad"); return false; } }
  else { body.username = (f.get("username") || "").trim(); body.password = f.get("password") || ""; body.keep_login = false; if (!body.username || !body.password) { toast("✕ Type the admin's username and password", "bad"); return false; } }
  toast(mode === "key" ? "Trying it on the panel…" : "Signing in…");
  try {
    const r = await api("/api/panels", {method: "POST", body: JSON.stringify(body)}), a = r.panel.auth || {};
    toast(`✓ ${r.panel.name} added${a.level !== "ok" ? " — but " + a.note : ""}${CONFIGS_UI ? " — making its test config on the probe…" : ""}`, a.level === "ok" ? "ok" : "bad");
    closeModal(); pn.id = String(r.panel.id); refresh(); if (CONFIGS_UI) pnTestConfig(r.panel.id, r.panel.name, true);      // the test user (and its configs on the probe) belongs to the Configs list: nothing is made on the panel while that is off
  } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};
window.pnTestConfig = async (id, name, quiet) => { if (!quiet) toast("Creating the test user and importing its subscription… (up to a minute)"); try { const r = await api(`/api/panels/${id}/test-config`, {method: "POST"}); if (r.existing) { toast(`✓ ${name}: its subscription is already in Configs (group ${r.existing}, ${r.existing_count} configs) — nothing added`, "ok"); return refresh(); } toast(`✓ ${name}: ${r.added.length} configs added to the probe${r.skipped.length ? ", " + r.skipped.length + " already there" : ""} (user ${r.username})`, "ok"); refresh(); } catch (e) { toast("✕ test config of " + name + ": " + e.message, "bad"); } };
window.pnEdit = async (e, id) => {
  e.preventDefault(); const f = new FormData(e.target), mode = e.target.dataset.mode || "key", body = {name: f.get("name").trim(), url: f.get("url").trim(), verify_tls: !!f.get("verify_tls"), enabled: !!f.get("enabled")};
  let again = false;      // a new key / token / sign-in: it is tried on the panel first and replaces the old one only when the panel accepts it
  if (mode === "key" && (f.get("token") || "").trim()) { body.token = f.get("token").trim(); again = true; }
  if (mode === "login" && f.get("password")) { body.username = (f.get("username") || "").trim(); body.password = f.get("password"); body.keep_login = false; again = true; }
  if (again) toast("Trying it on the panel…");
  try { const r = await api(`/api/panels/${id}`, {method: "PATCH", body: JSON.stringify(body)}); toast(again ? `✓ Saved — connected with the new ${r.panel.auth.kind === "api_key" ? "API key" : "token"}` : "✓ Saved", "ok"); closeModal(); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};
window.abtDiag = async () => {      // Settings -> About: one report of the panel's state, to paste where help is asked for
  try {
    const d = await api("/api/system/diagnostics"), txt = JSON.stringify(d, null, 1);
    try { await navigator.clipboard.writeText(txt); toast(`✓ Diagnostics copied (${d.recent_problems.length} recent problems, ${d.jobs.length} jobs) - paste them where you ask for help`, "ok"); }
    catch (e) { showModal("<b>Diagnostics</b>", `<p class="mut">Select it all (Ctrl+A) and copy it.</p><textarea class="mono" readonly rows="18" style="width:100%" onclick="this.select()">${esc(txt)}</textarea>`, "wide"); }
  } catch (e) { toast("✕ " + e.message, "bad"); }
};window.pnForgetAsk = async (id, name) => { if (!await gconfirm(`Forget the saved login of ${name}? The password is wiped from this panel and it goes on with its key / token only. To use a login again you type it again.`, {danger: true})) return; pnForget(id, false); };
window.pnForget = async (id, force) => {
  try { const r = await api(`/api/panels/${id}/forget-login`, {method: "POST", body: JSON.stringify({force})}); toast(`✓ ${r.panel.name}: the saved login is forgotten — connected with its ${r.panel.auth.kind === "api_key" ? "API key" : "token"} only`, "ok"); closeModal(); refresh(); }
  catch (err) { if (!force && /lasts only/.test(err.message) && await gconfirm(err.message + "\n\nForget the login anyway? You will have to paste a new token yourself when this one ends.", {danger: true})) return pnForget(id, true); toast("✕ " + err.message, "bad"); }
};
window.apiPanelEdit = id => { state.panelsCache = window.__apiPanels || []; pn.id = String(id); pnEditWin(); };      // (Edit from Settings → Api & Key: the same window as on the Panel page)
window.pnDel = async (id, name) => { if (!await gconfirm(`Remove the panel ${name} from this list? Nothing on the panel itself changes; its saved key or login is forgotten here.`, {danger: true})) return; try { await api(`/api/panels/${id}`, {method: "DELETE"}); toast("✓ Panel removed from the list", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } pn.id = ""; pn.edit = false; refresh(); };
window.pnNodeReconnect = async (id, nid) => { try { await api(`/api/panels/${id}/nodes/${nid}/reconnect`, {method: "POST"}); toast("✓ Reconnect requested", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.pnNodeLogs = async (id, nid) => { const pl = Array.isArray(state.panelsCache) ? state.panelsCache : (state.panelsCache || {}).panels || [], nm = ((pl.find(x => x.id === id) || {}).nodes || []).find(n => n.id === nid), name = nm ? nm.name : "node " + nid; try { pn.logs = {name, text: (await api(`/api/panels/${id}/nodes/${nid}/logs`)).log}; } catch (e) { pn.logs = {name, text: "✕ " + e.message}; } refresh(); };

/* ---- All panels: one account = the same person on every panel (same username, or a link made by hand); every panel is asked through its API when you look */
/* the bar on top of the Panels pages is the one of the CDN page: a row of chips (one per panel, "All panels", and the dashed "＋ Add panel" chip) and a row of facts and buttons */
const pnFirstCard = () => `<div class="card pn-first fx-box"><div class="pn-first-ic">${SI.cube}</div><h2>No panel yet</h2><p class="mut">Add one with its API key or access token: its usage, its settings and its users then show here.</p><button type="button" class="cta cta-server" onclick="pnAddWin()" title="Add a Marzban or PasarGuard panel"><i class="cta-ic">＋</i>Add panel</button></div>`;      // (the empty General page: ONE card of a fixed size with the way to add a panel - the owner, 2026-10-15)
const pnEmptyPage = () => pnBar([], "", "", "pick") + `<div id="pn-body"><div class="card pn-first fx-box"><div class="pn-first-ic">${SI.cube}</div><h2>No panel connected</h2><p class="mut">Panels are added in General (Panel > General). This section fills in as soon as one is connected.</p></div></div>`;      // a tab of the Panel page while no PasarGuard panel is connected: ONE card of a fixed size, nothing to press in the middle of the page (the owner, 2026-10-15)
const pnBar = (list, cur, row2, mode = "manage") => {      // the bar of the panels, built like the bar of the profiles in the Lab (the owner, 2026-10-15: "like the Profile section, no edit button, a locked box"): the panels are a segmented control (the open one lifted), "+" adds a panel, the bin takes the open one off the list; mode "pick": only switches between the panels, and the room beside them (#pn-ctl) holds what the tab has (pnFuse)
  const pick = mode === "pick", dot = p => p.status === "up" ? "ok" : p.status === "down" ? "bad" : "";
  const IB = d => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`, curP = list.find(p => String(p.id) === String(cur));
  const tabs = list.map(p => `<button type="button" class="${String(p.id) === String(cur) ? "on" : ""}" onclick="pnPick('${p.id}')" title="${esc(p.url || "")}${p.status === "down" ? " · down" : ""}"><i class="dot ${dot(p)}"></i><span class="lab-kn">${esc(p.name)}</span></button>`).join("");
  const acts = pick ? "" : `<span class="lab-tabact"><button type="button" class="lab-ib" onclick="pnAddWin()" title="Add a Marzban or PasarGuard panel" aria-label="Add panel">${IB('<path d="M12 5v14M5 12h14"/>')}</button>${curP ? `<button type="button" class="lab-ib danger" onclick="pnDel(${curP.id}, '${esc(curP.name)}')" title="Take the panel ${esc(curP.name)} off this list (nothing changes on the panel itself)" aria-label="Remove ${esc(curP.name)}">${IB('<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"/>')}</button>` : ""}</span>`;
  const box = list.length ? `<span class="lab-ksw pn-ksw"><div class="tabs lab-kinds pn-seg">${tabs}</div>${acts}</span>` : '<span class="pn-none mut">No panel yet</span>';      // (no panel yet: the page says it in one card, the bar only names it)
  if (pick) return `<div class="toolbar joined srv-bar dns-bar pn-top pn-bar pn-pick"><div class="dns-r dns-accbar pn-one">${box}<div id="pn-ctl" class="pn-ctl"></div></div></div>`;
  return `<div class="toolbar joined srv-bar dns-bar pn-top pn-bar"><div class="dns-r dns-accbar pn-one">${box}<span class="sp"></span><div class="pn-facts">${row2}</div></div></div>`;
};
/* outside General what a tab paints at its top - the strip of its tab buttons (.pn-bars) and its own bar of search, filters and buttons (.pg-bar) - is moved into the room beside the panels, so the top is ONE row (the owner, 2026-10-15) */
function pnFuse() {
  const slot = $("#pn-ctl"), body = $("#pn-body"); if (!slot || !body) return;
  const bars = body.querySelector(":scope > .pn-bars"), tool = body.querySelector(".pg-bar:not(.pg-usage .pg-bar)");
  if (bars) { slot.replaceChildren(...bars.childNodes); bars.remove(); }
  if (tool) { slot.querySelectorAll(":scope > .pg-bar").forEach(x => x.remove()); slot.append(tool); }
}
function pnFuseWatch() { const body = $("#pn-body"); if (!body || !$("#pn-ctl")) return; new MutationObserver(pnFuse).observe(body, {childList: true}); pnFuse(); }
window.pnFuse = pnFuse;const pnSelect = (list, cur) => `<select onchange="pnPick(this.value)">${list.length > 1 ? `<option value="*" ${cur === "*" ? "selected" : ""}>★ All panels</option>` : ""}${list.map(x => `<option value="${x.id}" ${x.id === cur ? "selected" : ""}>${esc(x.name)} — ${x.type}</option>`).join("")}</select>`;
const pa = {q: "", data: null, seq: 0};
function pnAllRender(list) {
  $("#page").innerHTML = pnBar(list, "*", `<span class="dns-stats"><span class="dns-stat">Panels <b>${list.length}</b></span></span><button class="sm pri" onclick="paNew()" title="Make the same new user on several panels at once">＋ New account</button><div class="toolbar pn-sb"><input id="pa-q" type="search" placeholder="Search a user on every panel…" value="${esc(pa.q)}" oninput="paQ(this.value)" autocomplete="off" spellcheck="false"></div>`) + `    <div id="pa-body"><div class="loading"><i class="spin"></i><span>Asking every panel…</span></div></div>`;
  paLoad();
}
window.paQ = v => { pa.q = v.trim(); clearTimeout(qT); qT = setTimeout(paLoad, 350); };
async function paLoad() {
  const my = ++pa.seq, box = $("#pa-body"); if (!box) return;
  let d; try { d = await api("/api/accounts?search=" + encodeURIComponent(pa.q)); } catch (e) { if (my === pa.seq && $("#pa-body")) $("#pa-body").innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  if (my !== pa.seq || !$("#pa-body")) return;      // a newer question is on its way
  pa.data = d;
  const dot = s => s === "active" ? "ok" : s === "disabled" ? "" : "bad";
  const chip = m => `<span class="pa-chip" title="${esc(m.panel + " · " + m.username + " · " + m.status)}"><i class="pa-dot ${dot(m.status)}"></i>${esc(m.panel)}</span>`;
  const row = a => `<tr class="clk" onclick="paOpen('${esc(a.key)}')" title="Open this account"><td class="name"><div class="usr-wrap"><i class="usr-av ${a.members.every(m => m.status === "active") ? "active" : "disabled"}">${SI.user}</i><div class="usr-nm"><b>${esc(a.name)}</b>${a.link ? '<small class="mut">linked by hand</small>' : a.panels > 1 ? '<small class="mut">same username</small>' : ""}</div></div></td><td class="pa-ch">${a.members.map(chip).join("")}</td><td title="${a.limit ? "used of the sum of the limits on all panels" : "at least one panel has no limit"}">${fmtB(a.used)}${a.limit ? `<small class="mut"> / ${gb(a.limit)}</small>` : ""}</td><td>${expPill({expire_date: a.expire_date})}</td></tr>`;
  const bad = d.panels.filter(p => !p.ok);
  box.innerHTML = `${bad.length ? `<div class="gw-out bad">${bad.map(p => `✕ ${esc(p.name)}: ${esc(p.error)}`).join(" · ")}</div>` : ""}
    <div class="pa-box"><table class="tbl lift pa-tbl"><colgroup><col style="width:26%"><col><col style="width:14%"><col style="width:14%"></colgroup><thead><tr><th>Account</th><th>On panels</th><th>Used</th><th>Expires</th></tr></thead>
      <tbody>${d.accounts.map(row).join("") || `<tr><td colspan="4" class="mut">${pa.q ? "nobody matches" : "no user"}</td></tr>`}</tbody></table></div>
    <small class="mut pa-foot">${d.accounts.length} account${d.accounts.length === 1 ? "" : "s"} · up to ${d.per_panel} users from each panel (the newest first) — type a name to search all of them.</small>`;
}
const paFind = key => ((pa.data || {}).accounts || []).find(a => a.key === key);
window.paOpen = key => {
  const a = paFind(key); if (!a) return;
  const mrow = m => { const left = m.limit ? Math.max(0, m.limit - (m.used || 0)) : null;
    return `<div class="ap-row pa-m"><span class="spill ${m.status}">${esc(String(m.status).replace("_", " "))}</span><b>${esc(m.panel)}</b><span class="mut">${esc(m.type)}</span><span class="mono">${esc(m.username)}</span><span class="sp"></span><span class="mut">${fmtB(m.used || 0)}${m.limit ? " / " + gb(m.limit) : " · unlimited"}</span>${expPill(m)}<button class="sm" onclick="closeModal();pnPick('${m.panel_id}');setTimeout(() => pnUserOpen(${m.panel_id}, '${esc(m.username)}'), 400)" title="Open this user in its panel">Open</button></div>`; };
  const have = new Set(a.members.map(m => m.panel_id)), free = (state.panelsCache || []).filter(p => !have.has(p.id));
  const add = free.length ? `<form class="frm pa-add" onsubmit="return paAdd(event, '${esc(a.key)}')"><span class="mut">Link a user with another name:</span><select name="panel">${free.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select><input name="username" required placeholder="username on that panel" autocomplete="off"><button class="sm pri" type="submit">Link</button></form>` : "";
  showModal(`<b>${esc(a.name)}</b> <span class="pill">${a.panels} panel${a.panels === 1 ? "" : "s"}</span>`, `<div class="pa-ms">${a.members.map(mrow).join("")}</div>${add}<div class="frm-act pa-act"><select class="sm" onchange="paAct('${esc(a.key)}', this)" title="The same action on the user of every panel of this account"><option value="">On every panel…</option><option value="enable">Enable</option><option value="disable">Disable</option><option value="renew">Add days…</option><option value="limit">Set the data limit…</option><option value="reset">Reset the usage</option><option value="revoke">Revoke the subscription link</option><option value="delete">Delete…</option></select><button class="sm" onclick="paLink('${esc(a.key)}')" title="One subscription link that holds the configs of every panel of this account">Merged link</button><span class="sp"></span>${a.link ? `<button class="sm danger" onclick="paUnlink('${esc(a.link)}')" title="Undo the link: nothing changes on the panels">Unlink</button>` : ""}</div><div id="pa-link"></div><small class="mut">${a.limit ? `${fmtB(a.used)} used of ${gb(a.limit)} in total` : `${fmtB(a.used)} used in total`} · the earliest end is ${a.expire_date ? esc(a.expire_date) : "never"}.</small>`, "wide");
};
window.paAdd = async (e, key) => {
  e.preventDefault(); const a = paFind(key), f = new FormData(e.target); if (!a) return false;
  const members = a.members.map(m => ({panel_id: m.panel_id, username: m.username})).concat([{panel_id: +f.get("panel"), username: f.get("username").trim()}]), old = a.link ? ((await api("/api/accounts/links")).links.find(x => x.id === a.link)) : null;
  try {
    if (old) await api("/api/accounts/links/" + encodeURIComponent(old.id), {method: "DELETE"});
    try { await api("/api/accounts/links", {method: "POST", body: JSON.stringify({members, name: a.link ? a.name : ""})}); }
    catch (err) { if (old) await api("/api/accounts/links", {method: "POST", body: JSON.stringify({members: old.members, name: old.name})}).catch(() => {}); throw err; }
    toast("✓ Linked", "ok"); closeModal(); paLoad();
  } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};
window.paAct = async (key, sel) => {
  const v = sel.value; sel.selectedIndex = 0; const a = paFind(key); if (!v || !a) return;
  const members = a.members.map(m => ({panel_id: m.panel_id, username: m.username})), n = `${a.panels} panel${a.panels === 1 ? "" : "s"}`;
  let value = null;
  if (v === "renew") { const d = await gprompt(`Add how many days on ${n}?`, "30"); if (!d) return; value = +d; }
  else if (v === "limit") { const g = await gprompt(`Data limit in GB on ${n} (0 = unlimited):`, "50"); if (g == null || g === false || g === "") return; value = +g; }
  else if (v === "delete") { if (!await gconfirm(`Delete ${a.name} on ${n}? This cannot be undone.`)) return; }
  else if (v === "revoke") { if (!await gconfirm(`Revoke the subscription link of ${a.name} on ${n}? The old links stop working.`)) return; }
  try {
    const r = await api("/api/accounts/action", {method: "POST", body: JSON.stringify({members, action: v, value, confirm: v === "delete"})});
    toast(r.failed ? `✕ ${r.done} done, ${r.failed} failed: ` + r.results.filter(x => !x.ok).map(x => `${x.panel} (${x.error})`).join(", ") : `✓ Done on ${r.done} panel${r.done === 1 ? "" : "s"}`, r.failed ? "bad" : "ok");
    closeModal(); paLoad();
  } catch (e) { toast("✕ " + e.message, "bad"); }
};
const paLinkHtml = path => `<div class="tok-show"><input id="pa-url" class="mono" readonly value="${esc(location.origin + path)}" onclick="this.select()"><button class="sm pri" onclick="navigator.clipboard.writeText($('#pa-url').value).then(() => toast('✓ Link copied', 'ok'))">Copy</button></div><small class="mut">Give this to the person: it holds the configs of every panel, each once, and the traffic and end date of all of them added up. It works without a login. To cancel every link made so far, press <a onclick="paLinkReset()">reset all links</a>.</small>`;
window.paLink = async key => {
  const a = paFind(key), box = $("#pa-link"); if (!a || !box) return;
  try {
    const r = await api("/api/accounts/sub-link", {method: "POST", body: JSON.stringify({members: a.members.map(m => ({panel_id: m.panel_id, username: m.username}))})});
    box.innerHTML = paLinkHtml(r.path); $("#pa-url").select();
  } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.paNew = async () => {
  const ps = (state.panelsCache || []).filter(p => p.enabled);
  if (!ps.length) { toast("Add a panel first", "bad"); return; }
  const opts = {};
  await Promise.all(ps.filter(p => p.type === "pasarguard").map(async p => { try { opts[p.id] = (await api(`/api/panels/${p.id}/options`)).groups || []; } catch (e) { opts[p.id] = []; } }));
  const row = p => `<label class="chk pa-np"><input type="checkbox" name="p${p.id}" checked> <b>${esc(p.name)}</b> <small class="mut">${esc(p.type)}</small>${(opts[p.id] || []).length > 1 ? `<select name="g${p.id}" title="The group the user gets on this panel">${opts[p.id].map(g => `<option value="${g.id}">${esc(g.name)}</option>`).join("")}</select>` : ""}</label>`;
  showModal("<b>New account</b>", `<form class="frm" onsubmit="return paCreate(event)"><label>Username<input name="username" required minlength="3" maxlength="32" autocomplete="off" placeholder="3-32 letters, digits or _"></label><label>Data limit in GB (0 = unlimited)<input name="gb" type="number" min="0" step="any" value="50"></label><label>Days (0 = no end)<input name="days" type="number" min="0" max="3650" value="30"></label><div class="pa-nps">${ps.map(row).join("")}</div><div class="frm-act"><button class="pri" type="submit">Create on the chosen panels</button><button type="button" onclick="closeModal()">Close</button></div></form><div id="pa-res"></div>`, "narrow");
  setTimeout(() => { const i = document.querySelector("#modal input[name=username]"); if (i) i.focus(); }, 40);
};
window.paCreate = async e => {
  e.preventDefault(); const f = new FormData(e.target), ps = (state.panelsCache || []).filter(p => f.get("p" + p.id));
  if (!ps.length) { toast("Choose at least one panel", "bad"); return false; }
  const body = {username: f.get("username").trim(), limit_gb: +f.get("gb") || 0, days: +f.get("days") || 0, panels: ps.map(p => ({panel_id: p.id, group_ids: f.get("g" + p.id) ? [+f.get("g" + p.id)] : null}))};
  try {
    const r = await api("/api/accounts/create", {method: "POST", body: JSON.stringify(body)});
    $("#pa-res").innerHTML = `<div class="pa-ms">${r.results.map(x => `<div class="ap-row pa-m"><span class="spill ${x.ok ? "active" : "disabled"}">${x.ok ? "done" : "failed"}</span><b>${esc(x.panel)}</b><span class="mut">${esc(x.error)}</span></div>`).join("")}</div>${r.path ? paLinkHtml(r.path) : ""}`;
    if (r.done) e.target.querySelector("button[type=submit]").disabled = true;
    toast(r.failed ? `✕ ${r.done} done, ${r.failed} failed` : `✓ Done on ${r.done} panel${r.done === 1 ? "" : "s"}`, r.failed ? "bad" : "ok");
    paLoad();
  } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};
window.paLinkReset = async () => { if (!await gconfirm("Every merged link made so far stops working. Continue?")) return; try { await api("/api/accounts/sub-link/reset", {method: "POST"}); toast("✓ All merged links are cancelled", "ok"); const b = $("#pa-link"); if (b) b.innerHTML = ""; } catch (e) { toast("✕ " + e.message, "bad"); } };
window.paUnlink = async id => { try { await api("/api/accounts/links/" + encodeURIComponent(id), {method: "DELETE"}); toast("✓ Unlinked", "ok"); closeModal(); paLoad(); } catch (e) { toast("✕ " + e.message, "bad"); } };

/* users */
async function pnUsersLoad(id) {
  const box = $("#pn-body"); if (!box) return;
  const ps = pnPS(); pn.uoff = Math.floor(pn.uoff / ps) * ps;       // a page holds exactly what fits the window: the list never scrolls
  const key = `${id}|${pn.uq}|${pn.ustatus}|${pn.uoff}|${ps}`;
  if (!pn.users || pn.usersKey !== key) {   // read from the panel only when the question changed, not on every refresh
    try { pn.users = await api(`/api/panels/${id}/users?search=${encodeURIComponent(pn.uq)}&status=${pn.ustatus}&offset=${pn.uoff}&limit=${ps}&sort=-created_at`); pn.usersKey = key; } catch (e) { box.innerHTML = `<div class="pn-bars">${pnTabs()}</div><div class="err">${esc(e.message)}</div>`; return; }
  }
  if (!box.isConnected || String(pn.id) !== String(id) || pn.tab !== "users") return;      // (the panel or the tab changed while the list was read: a late answer must not paint the old one - the guard pg_admin.js has)
  box.classList.remove("pn-busy");
  const u = pn.users, now = Date.now() / 1000;
  const exp = x => !x.expire_date ? "∞" : (typeof x.expire === "number" ? (x.expire < now ? '<span class="err">expired</span>' : Math.ceil((x.expire - now) / 86400) + " d") : esc(x.expire_date));
  box.innerHTML = `<div class="pn-bars">${pnTabs()}<div class="toolbar pn-sb"><input id="pn-uq" type="search" placeholder="Search user…" value="${esc(pn.uq)}" oninput="pnUQ(this.value)" autocomplete="off" spellcheck="false"></div><div class="toolbar"><select class="sm pn-st-sel" title="Show the users of one status" onchange="pn.ustatus=this.value;pn.uoff=0;pnUsersLoad(+pn.id)">${[["", "All statuses"], ["active", "Active"], ["disabled", "Disabled"], ["limited", "Limited"], ["expired", "Expired"], ["on_hold", "On hold"]].map(([v, l]) => `<option value="${v}" ${pn.ustatus === v ? "selected" : ""}>${l}</option>`).join("")}</select><span class="sp"></span>
      <span class="pn-acts"><button class="sm pn-actbtn" disabled onclick="pnBulk(${id}, 'enable')" title="Switch the ticked users on">Enable</button><button class="sm pn-actbtn" disabled onclick="pnBulk(${id}, 'disable')" title="Switch the ticked users off">Disable</button><button class="sm pn-actbtn" disabled onclick="pnBulkRenew(${id})" title="Add days to the ticked users">＋ Days…</button></span>${window.pgUsersMenu ? pgUsersMenu() : ""}</div></div>
    <div class="lbox full usr-box"><table class="tbl lift usr-tbl"><colgroup><col style="width:64px"><col style="width:30%"><col style="width:12%"><col style="width:36%"><col></colgroup>
      <thead><tr><th><input type="checkbox" class="ck" id="pn-all" title="Select every user of this page" onchange="pnSelAll(this.checked)"></th><th>User</th><th>Status</th><th title="Used so far, what is left, and the volume of the account">Traffic <small>used · left · account</small></th><th>Expires</th></tr></thead>
      <tbody>${u.users.map(x => `<tr class="${x.status === "active" ? "up" : x.status === "disabled" ? "" : "down"} clk" onclick="pnUserOpen(${id}, '${esc(x.username)}')" title="Click to open the user"><td onclick="event.stopPropagation()"><input type="checkbox" class="ck pn-uchk" value="${esc(x.username)}" onchange="pnSelCount()"></td><td class="name"><div class="usr-wrap"><i class="usr-av ${x.status}">${SI.user}</i><div class="usr-nm"><b>${esc(x.username)}</b>${x.note ? `<small class="mut" title="${esc(x.note)}">${esc(String(x.note).slice(0, 60))}</small>` : ""}</div></div></td><td><span class="spill ${x.status}">${esc(String(x.status).replace("_", " "))}</span></td>
        <td class="trf">${pnTraffic(x)}</td><td>${expPill(x)}</td></tr>
`).join("") || '<tr><td colspan="5" class="mut">nobody matches</td></tr>'}</tbody></table></div>
`;
  pnFuse();
  const ft = $("#pn-foot"); if (ft) ft.innerHTML = pnFoot(pnPager(u.total, ps));
  pnLiveStart(id);
  pn.curPS = ps; pnFit(ps); pnFitSoon(ps);       // after the list was put into its box
  const q = $("#pn-uq"); if (q && pn.uq) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
  const rows = box.querySelectorAll("tbody tr");                 // the first time, the real height of a row is measured and the page size follows it
  if (!pnRow.done && rows.length > 2) { pnRow.done = true; const pitch = (rows[rows.length - 1].getBoundingClientRect().bottom - rows[0].getBoundingClientRect().top) / rows.length; if (pitch > 20 && Math.abs(pitch - pnRow.h) > 0.5) { pnRow.h = pitch; if (pnPS() !== ps) pnUsersLoad(id); } }
}
/* LIVE: an account made in the panel shows here by itself, without reading the list again and again. Every 15 s (only while this tab is on screen, the Users list is open and the page is not hidden)
   ONE small question is asked: the newest user and the total (limit 1). When something new is there and the list is the first page with no search, no filter and nothing ticked, the list is
   read once; otherwise a short notice says that a new account exists. */
pn.live = {t: null, id: 0, top: null, total: 0};
function pnLiveStop() { clearInterval(pn.live.t); pn.live.t = null; }
function pnLiveStart(id) { if (pn.live.id !== id) Object.assign(pn.live, {id, top: null, total: 0}); if (!pn.live.t) pn.live.t = setInterval(() => pnLiveTick(id), 15000); }
async function pnLiveTick(id) {
  if (!pnIsUsers() || pn.tab !== "users" || String(pn.id) !== String(id) || !$("#pn-body .usr-tbl")) { pnLiveStop(); return; }      // left the list: no more questions
  if (document.hidden || $("#modal .modal")) return;
  let r; try { r = await api(`/api/panels/${id}/users?limit=1&sort=-created_at`); } catch (e) { return; }
  const top = (r.users[0] || {}).username || "", total = +r.total || 0, l = pn.live;
  const fresh = l.top !== null && (top !== l.top || total > l.total);
  const added = Math.max(1, total - l.total);
  Object.assign(l, {top, total});
  if (!fresh) return;
  const quiet = pn.uoff === 0 && !pn.uq && !pn.ustatus && !document.querySelector(".pn-uchk:checked");
  toast(`＋ New account in the panel: ${top}${added > 1 ? ` (and ${added - 1} more)` : ""}`, "ok");
  if (quiet) { pn.users = null; pnUsersLoad(id); }
}
const pnRow = {h: 46, done: true};
/* a fixed box (.fx-box): it fills the page down to the bottom of the window and keeps that size, whatever is inside; the list scrolls inside it (the owner: "a fixed box") */
function fxFit() { document.querySelectorAll("#page .fx-box").forEach(b => { if (isPhone()) { b.style.removeProperty("height"); return; } b.style.height = Math.max(360, Math.floor(mainBottom() - b.getBoundingClientRect().top - 14)) + "px"; }); }
addEventListener("resize", fxFit);
const mainBottom = () => { const m = document.querySelector(".main"); return m ? Math.min(innerHeight, m.getBoundingClientRect().bottom) : innerHeight; };
const pnPS = () => { if (isPhone()) return 10; const b = $("#pn-body"), top = b ? b.getBoundingClientRect().top : 270; return Math.max(5, Math.min(100, Math.floor((mainBottom() - top - ($("#pn-ctl") ? 0 : 56) - 52 - 14 - 10 - 36 - 6) / pnRow.h))); };
/* the list box is one fixed box from its top down to the pager, which sits on the bottom edge: the rows are stretched to fill it exactly, so nothing scrolls and no empty strip is left */
function pnFitSoon(ps, n = 0) { clearTimeout(pnFitSoon.t); pnFitSoon.t = setTimeout(() => { if (document.querySelector("#pn-body .usr-tbl") && !document.querySelector("#pn-body .lbox") && n < 20) return pnFitSoon(ps, n + 1); pnFit(ps); }, n ? 60 : 30); }      // waits until the list sits in its box
function pnFit(ps) {
  if (isPhone()) return;
  const box = document.querySelector("#pn-body .lbox"), foot = $("#pn-foot"); if (!box) return;
  const tb = box.querySelector("table"), th = box.querySelector("thead"); if (!tb || !th) return;
  box.style.height = ""; box.style.maxHeight = "none"; box.style.overflow = "hidden"; box.style.minHeight = "0";
  const H = Math.floor(mainBottom() - box.getBoundingClientRect().top - (foot ? foot.offsetHeight + 6 : 0) - 10);
  box.style.height = H + "px";
  const inner = () => box.clientHeight - 14, set = h => box.style.setProperty("--rh", h + "px");
  let h = Math.floor((inner() - th.offsetHeight - 6 - ps * 6) / ps);
  const key = ps + "|" + H, rows = tb.querySelectorAll("tbody tr").length;
  if (rows < ps) { set(pnFit.k === key ? pnFit.h : Math.floor(h * 10) / 10); return; }      // a short list (a filter with few users) keeps the row height of a full page: the rows never grow to fill the box
  for (let k = 0; k < 3; k++) { set(h); const over = tb.offsetHeight - inner(); if (Math.abs(over) < 1) break; h -= over / ps; }
  pnFit.k = key; pnFit.h = Math.floor(h * 10) / 10; set(pnFit.h);
}
let pnFitT; addEventListener("resize", () => { clearTimeout(pnFitT); pnFitT = setTimeout(() => { if (!$("#pn-body .usr-tbl")) return; if (pnPS() !== pn.curPS) pnUsersLoad(+pn.id); else pnFitSoon(pn.curPS); }, 150); });
const pnType = () => ((state.panelsCache || []).find(p => String(p.id) === pn.id) || {}).type || "";
/* the tabs of the panel: Users, Hosts, then every tab a pg_*.js file registered in pgTabs for this type of panel (PasarGuard: groups, templates, nodes, cores, admins, settings, usage) */
const pnExtraTabs = () => Object.entries(window.pgTabs || {}).filter(([, t]) => !t.types || t.types.includes(pnType())).sort((a, b) => (a[1].order || 99) - (b[1].order || 99)).map(([k, t]) => [k, t.label]);
const pnTabBtns = () => { const mine = pnPageTabs(), list = [["users", "Users"], ["hosts", "Hosts"], ...pnExtraTabs()].filter(([t]) => mine.includes(t) && t !== "templates").sort((a, b) => mine.indexOf(a[0]) - mine.indexOf(b[0])); return list.length > 1 ? `<div class="tabs">${list.map(([t, l]) => `<button class="${pn.tab === t || (t === "settings" && pn.tab === "templates") ? "on" : ""}" onclick="pnTab('${t}')">${l}</button>`).join("")}</div>` : ""; };      // (the user templates are no button of General: they are the last section of Settings, and Settings stays the open button while they show)
const pnTabs = () => { const b = pnTabBtns(); return b ? `<div class="toolbar pn-tabs">${b}</div>` : ""; };      // only the tabs of the page that is open; a page with one tab has no strip
const pnFoot = mid => mid;
function pnPager(total, ps, off = pn.uoff, fn = "pnGo") {
  const pages = Math.max(1, Math.ceil(total / ps)), cur = Math.floor(off / ps), last = pages - 1;
  let slots;      // always seven slots (numbers or "…") of one width, so the buttons never move when the page changes
  if (pages <= 7) slots = Array.from({length: pages}, (_, i) => i);
  else if (cur <= 3) slots = [0, 1, 2, 3, 4, -1, last];
  else if (cur >= last - 3) slots = [0, -1, last - 4, last - 3, last - 2, last - 1, last];
  else slots = [0, -1, cur - 1, cur, cur + 1, -1, last];
  const btns = slots.map(i => i < 0 ? `<span class="pgn-gap">…</span>` : `<button class="sm pgn ${i === cur ? "on" : ""}" onclick="${fn}(${i})">${i + 1}</button>`).join("");
  return `<span class="sp"></span><span class="pgs"><button class="sm pgn" ${cur ? "" : "disabled"} onclick="${fn}(${cur - 1})">‹</button>${btns}<button class="sm pgn" ${cur + 1 < pages ? "" : "disabled"} onclick="${fn}(${cur + 1})">›</button></span><span class="sp"></span>`;
}
window.pnGo = i => { pn.uoff = Math.max(0, i) * pnPS(); pnUsersLoad(+pn.id); };      // the page changes from the bottom of the list and the list is shown from its top again
/* the inbounds of a user (Marzban): one tick per inbound, grouped by protocol; a protocol with no tick is removed from the user */
const ibHtml = u => {
  const opts = (u.options || {}).inbounds || []; if (!opts.length || !u.inbounds) return "";
  const by = {}; opts.forEach(i => { (by[i.protocol] = by[i.protocol] || []).push(i); });
  const on = (p, tag) => ((u.inbounds || {})[p] || []).includes(tag);
  return `<div class="ue-ibs">${Object.entries(by).map(([p, lst]) => `<details class="ue-ibp" onchange="ibCount(this)"><summary><input type="checkbox" class="ib-master" title="Tick or untick every ${esc(p)} inbound" onchange="ibMaster(this)"><b>${esc(p)}</b><small class="mut ib-n"></small></summary>
      <div class="ue-ibl">${lst.map(i => `<label class="chk" title="${esc(i.tag)}${i.port ? " · port " + i.port : ""}"><input type="checkbox" name="ib_${esc(p)}__${esc(i.tag)}" ${on(p, i.tag) ? "checked" : ""}><span>${esc(i.tag)}</span>${i.port ? `<em>:${i.port}</em>` : ""}</label>`).join("")}</div></details>`).join("")}</div>`;
};
window.ibCount = d => { const xs = [...d.querySelectorAll(".ue-ibl input")], n = xs.filter(x => x.checked).length, c = d.querySelector(".ib-n"), m = d.querySelector(".ib-master"); if (c) c.textContent = `${n} of ${xs.length}`; if (m) { m.checked = n > 0 && n === xs.length; m.indeterminate = n > 0 && n < xs.length; } d.classList.toggle("none", !n); };
window.ibMaster = m => { const d = m.closest("details"); d.querySelectorAll(".ue-ibl input").forEach(x => { x.checked = m.checked; }); ibCount(d); };
function pnUserHtml(id, u) {   // the user window: the actions on top, three boxes (usage, account, inbounds), the save row at the bottom
  if (u.sub && u.sub.startsWith("/")) { const pp = (state.panelsCache || []).find(x => String(x.id) === String(id)); if (pp) u = {...u, sub: String(pp.url || "").replace(/\/+$/, "") + u.sub}; }
  const nm = esc(u.username), used = u.used || 0, lim = u.limit || 0, pct = lim ? Math.min(100, Math.round(used * 100 / lim)) : 0, left = u.expire ? Math.ceil((u.expire - Date.now() / 1000) / 86400) : null;
  const kv = (l, v) => `<div class="kv2"><span>${l}</span><div>${v}</div></div>`;
  const fld = (l, inner) => `<label class="kv2 kf"><span>${l}</span>${inner}</label>`;
  const box = (icon, title, rows, cls = "") => `<div class="sbox ${cls}"><h5><i>${icon}</i>${title}</h5>${rows}</div>`;
  const b = (icon, title, tip, click, cls = "") => `<button type="button" class="sm abtn ${cls}" onclick="${click}" title="${esc(tip)}"><i>${icon}</i>${title}</button>`;
  const off = u.status === "disabled";
  const bar = `<div class="sbar ue-acts"><span class="sg">${b("↺", "Reset usage", "Set the used traffic back to zero", `pnUAct(${id}, '${nm}', 'reset')`)}${b("🔑", "Revoke subscription", "A new subscription link: the old one stops working", `pnUAct(${id}, '${nm}', 'revoke')`)}</span><i class="sep"></i>
    <span class="sg">${b(off ? "▶" : "⏸", off ? "Enable" : "Disable", off ? "Let the user connect again" : "Stop the user from connecting", `pnUAct(${id}, '${nm}', '${off ? "enable" : "disable"}')`)}</span><span class="sp"></span>
    ${b("🗑", "Delete user", "Remove the user from the panel", `(async()=>{if(await gconfirm('Delete ${nm} from the panel?'))pnUAct(${id}, '${nm}', 'delete')})()`, "danger")}</div>`;
  const usage = box("📊", "Usage", `<div class="ue-big"><b>${fmtB(used)}</b><span>${lim ? "of " + gb(lim) + " · " + pct + " %" : "unlimited"}</span></div>${lim ? `<div class="ue-bar"><i class="${pct >= 95 ? "bad" : pct >= 80 ? "warn" : ""}" style="width:${pct}%"></i></div>` : ""}`
    + kv("Left", lim ? fmtB(Math.max(0, lim - used)) : "—") + kv("Lifetime", fmtB(u.lifetime || 0)) + kv("Expires", u.expire_date ? esc(u.expire_date) + (left != null ? ` <small class="${left < 0 ? "err" : "mut"}">${left < 0 ? "expired" : left + " d"}</small>` : "") : "never") + kv("Created", esc(String(u.created_at || "").slice(0, 10)) || "—") + kv("Online", esc(String(u.online_at || "never").slice(0, 16).replace("T", " "))), "ue-usage");
  const account = box("👤", "Account", fld("Status", `<select name="status">${["active", "disabled", "on_hold"].map(s => `<option ${u.status === s ? "selected" : ""}>${s}</option>`).join("")}</select>`)
    + fld("Data limit", `<input name="limit_gb" type="number" step="0.1" min="0" value="${u.limit ? (u.limit / GB).toFixed(1) : 0}" title="GB, 0 = unlimited"><em>GB</em>`)
    + fld("Reset usage", `<select name="reset">${["no_reset", "day", "week", "month", "year"].map(s => `<option ${u.reset === s ? "selected" : ""}>${s}</option>`).join("")}</select>`)
    + fld("Expires", `<input name="expire_date" type="date" value="${esc(u.expire_date)}" title="empty = never">`)
    + fld("Note", `<input name="note" value="${esc(u.note)}" placeholder="optional">`)
    + (u.sub ? kv("Link", `<span class="mono ell ue-link" title="Click to copy the subscription link" onclick="copyFrom(this, '${esc(u.sub)}')">${esc(u.sub)}</span>`) : ""), "ue-account");
  const ib = ibHtml(u), inbounds = ib ? box("🔌", "Inbounds", ib, "ue-inb") : "";
  return `${bar}<form class="frm ue-form" onsubmit="return pnUserSave(event, ${id}, '${nm}')"><div class="sgrid ue-grid ${ib ? "" : "two"}">${usage}${account}${inbounds}</div>
    <div class="frm-act ue-save"><button class="pri" type="submit">Save</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`;
}window.pnUQ = v => { pn.uq = v.trim(); pn.uoff = 0; clearTimeout(qT); qT = setTimeout(() => pnUsersLoad(+pn.id), 300); };
window.pnUserOpen = async (id, name) => { if (pnType() === "pasarguard" && window.pgUserOpen) return pgUserOpen(id, name); try { pn.user =(await api(`/api/panels/${id}/users/${encodeURIComponent(name)}`)).user; const u = pn.user; showModal(`<span class="st ${u.status === "active" ? "online" : u.status === "disabled" ? "" : "offline"}"></span><b>${esc(u.username)}</b> <span class="pill">${esc(u.status)}</span>`, pnUserHtml(id, u), "wide ue"); document.querySelectorAll("#modal .ue-ibp").forEach(ibCount); } catch (e) { toast("✕ " + e.message, "bad"); } };
const ibBody = form => { const xs = [...form.querySelectorAll('input[name^="ib_"]')]; if (!xs.length) return {}; const inbounds = {}; xs.forEach(x => { const [p, tag] = x.name.slice(3).split("__"); inbounds[p] = inbounds[p] || []; if (x.checked) inbounds[p].push(tag); }); return {inbounds}; };
window.pnUserSave = async (e, id, name) => { e.preventDefault(); const f = new FormData(e.target); try { await api(`/api/panels/${id}/users/${encodeURIComponent(name)}`, {method: "PUT", body: JSON.stringify(Object.assign({status: f.get("status"), expire_date: f.get("expire_date"), limit_gb: +f.get("limit_gb"), reset: f.get("reset"), note: f.get("note")}, ibBody(e.target)))}); toast("✓ Saved", "ok"); pn.user = null; closeModal(); pn.users = null; refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.pnUAct = async (id, name, action, value) => { if (action === "renew" && !value) return; try { await api(`/api/panels/${id}/users/${encodeURIComponent(name)}/action`, {method: "POST", body: JSON.stringify({action, value})}); toast("✓ " + action + " done", "ok"); pn.user = null; closeModal(); pn.users = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.pnBulk = async (id, action, value) => { const names = [...document.querySelectorAll(".pn-uchk:checked")].map(x => x.value); if (!names.length) { toast("Tick some users first", "bad"); return; } try { const r = await api(`/api/panels/${id}/users-bulk`, {method: "POST", body: JSON.stringify({action, value, usernames: names})}); toast(`✓ ${r.done} done${r.failed_count ? ", " + r.failed_count + " failed" : ""}`, r.failed_count ? "bad" : "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
const pnTraffic = x => {
  const used = x.used || 0, lim = x.limit || 0, left = Math.max(0, lim - used), pct = lim ? Math.min(100, Math.round(used * 1000 / lim) / 10) : 0, cls = pct >= 95 ? "bad" : pct >= 80 ? "warn" : "";
  return lim ? `<div class="trf2"><div class="trf2-t"><span><b>${fmtB(used)}</b> <small>used</small></span><span class="${pct >= 95 ? "err" : ""}"><b>${fmtB(left)}</b> <small>left</small></span></div><div class="trf2-b" title="Account volume ${gb(lim)}"><i class="rbar"><u class="${cls}" style="width:${pct}%"></u></i><small>${pct}% of ${gb(lim)}</small></div></div>`
    : `<div class="trf2"><div class="trf2-t"><span><b>${fmtB(used)}</b> <small>used</small></span><span class="mut">unlimited</span></div></div>`; };
const expPill = x => { if (!x.expire_date) return '<span class="epill none">∞ never</span>'; const d = typeof x.expire === "number" ? Math.ceil((x.expire - Date.now() / 1000) / 86400) : null; const cls = d == null ? "" : d < 0 ? "bad" : d <= 7 ? "warn" : "ok"; return `<span class="exp-c"><span class="epill ${cls}" title="${esc(x.expire_date)}">${d == null ? esc(x.expire_date) : d < 0 ? "expired" : d + " d"}</span>${d == null ? "" : `<small class="exp-d">${esc(String(x.expire_date).slice(0, 10))}</small>`}</span>`; };
/* copy a text and say so where it was clicked: a corner badge in a box, a solid mark on the address in a table cell */
window.copyFrom = async (el, text) => {
  try { await navigator.clipboard.writeText(text); } catch (e) { const t = document.createElement("textarea"); t.value = text; document.body.appendChild(t); t.select(); try { document.execCommand("copy"); } catch (e2) { /* no clipboard at all */ } t.remove(); }
  const box = el.closest(".sbox");
  if (!box) { el.classList.remove("ip-copied"); void el.offsetWidth; el.classList.add("ip-copied"); clearTimeout(el.__ct); el.__ct = setTimeout(() => el.classList.remove("ip-copied"), 1500); return; }
  box.querySelectorAll(".copied").forEach(x => x.remove());
  const b = document.createElement("span"); b.className = "copied"; b.textContent = "✓ Copied"; box.appendChild(b); setTimeout(() => b.remove(), 1600);
};
window.pnSelCount = () => {
  const all = [...document.querySelectorAll(".pn-uchk, .pn-hchk")], n = all.filter(x => x.checked).length;
  const a = $("#pn-act"); if (a) a.disabled = !n;
  document.querySelectorAll(".pn-actbtn").forEach(b => { b.disabled = !n; b.title = b.title.replace(/ \(\d+ ticked\)$/, "") + (n ? ` (${n} ticked)` : ""); });          // the buttons of the ticked users / hosts light up as soon as something is ticked (and say how many in their tip)
  const h = $("#pn-all"); if (h) { h.checked = n > 0 && n === all.length; h.indeterminate = n > 0 && n < all.length; }
  const s = document.querySelector(".pn-acts"); if (s) s.classList.toggle("on", n > 0);
};
window.pnSelAll = on => { document.querySelectorAll(".pn-uchk, .pn-hchk").forEach(x => { x.checked = on; }); pnSelCount(); };
window.pnBulkAct = (id, sel) => { const v = sel.value; sel.selectedIndex = 0; if (v === "renew") pnBulkRenew(id); else if (v) pnBulk(id, v); };
window.pnBulkRenew = async id => { const d = await gprompt("Add how many days to the ticked users?", "30"); if (d) pnBulk(id, "renew", +d); };

/* hosts */
async function pnHostsLoad(id) {
  const box = $("#pn-body"); if (!box) return;
  if (!pn.hosts || pn.hostsKey !== String(id)) {
    try { pn.hosts = (await api(`/api/panels/${id}/hosts`)).hosts; pn.hostsKey = String(id); } catch (e) { box.innerHTML = `<div class="pn-bars">${pnTabs()}</div><div class="err">${esc(e.message)}</div>`; return; }
  }
  const tags = {}; pn.hosts.forEach(h => { tags[h.tag] = (tags[h.tag] || 0) + 1; });
  const q = (pn.hq || "").toLowerCase();
  const rows = pn.hosts.filter(h => (!pn.hinb || h.tag === pn.hinb) && (!pn.hstate || (pn.hstate === "off") === !!h.disabled) && (!q || nkHit([h.remark, h.tag, h.address, h.port, h.disabled ? "off disabled" : "on", ...(h.sni ? [h.sni] : []), ...(h.host ? [h.host] : [])].join(" "), q)));
  const byPort = {}; rows.forEach(h => { const k = h.port == null ? "" : String(h.port); (byPort[k] = byPort[k] || []).push(h); });      // one group per port: click a port to see its hosts and inbounds
  const ports = Object.keys(byPort).sort((x, y) => (x === "") - (y === "") || (+x || 0) - (+y || 0));
  pn.hopen = pn.hopen || {};
  const hostRow = h => `<div class="hp-row ${h.disabled ? "off" : ""}"><input type="checkbox" class="ck pn-hchk" value="${esc(h.key)}" onchange="pnSelCount();hpCount(this.closest('details'))"><span class="td-type" title="Inbound">${esc(h.tag)}</span><span class="name ell" title="${esc(h.remark)}">${esc(h.remark)}</span><span class="mut ell">${esc(h.address)}</span><span class="st ${h.disabled ? "" : "up"}">${h.disabled ? "disabled" : "on"}</span>
      <span class="hp-act"><button class="sm" onclick="pnHostOpen(${id}, '${esc(h.key)}')">Edit</button><button class="sm" onclick="pnHostClone(${id}, '${esc(h.key)}')">Clone</button><button class="sm danger" onclick="pnHostDel(${id}, '${esc(h.key)}', '${esc(h.remark)}')">✕</button></span></div>${pn.hostEdit && pn.hostEdit.key === h.key ? `<div class="hp-edit">${pnHostHtml(id, pn.hostEdit)}</div>` : ""}`;
  const group = k => { const g = byPort[k], on = g.filter(h => !h.disabled).length, open = pn.hopen[k] || (pn.hostEdit && g.some(h => h.key === pn.hostEdit.key)), pk = esc(k);
    const tg = [...new Set(g.map(h => h.tag))], pct = Math.round(on * 100 / g.length);
    return `<details class="hp ${on ? "" : "alloff"}" data-port="${pk}" ${open ? "open" : ""} ontoggle="pn.hopen['${pk}'] = this.open"><summary><input type="checkbox" class="ck hp-master" title="Tick every host of this port" onclick="event.stopPropagation()" onchange="hpMaster(this)"><b class="hp-port">${k === "" ? "inbound port" : ":" + pk}</b><small class="mut hp-n"></small>
      <span class="hp-bar" title="${on} of ${g.length} hosts are on"><i class="${pct === 100 ? "full" : ""}" style="width:${pct}%"></i></span><span class="hp-st"><b>${on}</b> on${g.length - on ? ` · <b>${g.length - on}</b> off` : ""}</span>
      <span class="hp-tags">${tg.slice(0, 3).map(t => `<span class="td-type" title="Inbound">${esc(t)}</span>`).join("")}${tg.length > 3 ? `<small class="mut" title="${esc(tg.slice(3).join(", "))}">+${tg.length - 3}</small>` : ""}</span></summary><div class="hp-rows">${g.map(hostRow).join("")}</div></details>`; };  box.innerHTML = `<div class="pn-bars">${pnTabs()}<div class="toolbar pn-sb"><input id="pn-hq" type="search" placeholder="Search remark, address, port…" value="${esc(pn.hq || "")}" oninput="pnHQ(this.value)" autocomplete="off" spellcheck="false"></div><div class="toolbar"><select onchange="pn.hinb=this.value;refresh()" title="Show the hosts of one inbound">${[`<option value="">all inbounds · ${pn.hosts.length}</option>`].concat(Object.keys(tags).sort().map(t => `<option value="${esc(t)}" ${pn.hinb === t ? "selected" : ""}>${esc(t)} · ${tags[t]}</option>`)).join("")}</select><select onchange="pn.hstate=this.value;refresh()">${[["", "all states"], ["on", "on"], ["off", "disabled"]].map(([v, l]) => `<option value="${v}" ${pn.hstate === v ? "selected" : ""}>${l}</option>`).join("")}</select><span class="mut pn-cnt">${rows.length} hosts · ${ports.length} ports</span><button class="sm" onclick="pnReload()" title="Read the hosts from the panel again">↻</button><span class="sp"></span>
      <span class="pn-acts"><button class="sm pn-actbtn" disabled onclick="pnHostsToggle(${id}, false)" title="Switch the ticked hosts on">Enable</button><button class="sm pn-actbtn" disabled onclick="pnHostsToggle(${id}, true)" title="Switch the ticked hosts off">Disable</button><button class="sm pn-actbtn" disabled onclick="pnHostTls(${id})" title="A TLS copy (port 443) of each ticked plain WebSocket host, tested first">🔒 TLS copies…</button></span>${window.pgHostsMenu ? pgHostsMenu(id) : ""}</div></div>
    <div class="hp-list">${ports.map(group).join("") || '<div class="mut" style="padding:16px">no host matches</div>'}</div>`;
  pnFuse(); box.querySelectorAll(".hp").forEach(hpCount);
  const hl = box.querySelector(".hp-list"); if (hl) hl.style.maxHeight = Math.max(160, innerHeight - hl.getBoundingClientRect().top - 74) + "px";   // the list ends above the bottom strip: only it scrolls
  const ft = $("#pn-foot"); if (ft) ft.innerHTML = pnFoot("");
  const sq = $("#pn-hq"); if (sq && pn.hq) { sq.focus(); sq.setSelectionRange(sq.value.length, sq.value.length); }
}
window.hpCount = d => { if (!d) return; const xs = [...d.querySelectorAll(".pn-hchk")], n = xs.filter(x => x.checked).length, m = d.querySelector(".hp-master"), c = d.querySelector(".hp-n"); if (c) c.textContent = n ? `${n} of ${xs.length} ticked` : `${xs.length} host${xs.length === 1 ? "" : "s"}`; if (m) { m.checked = n > 0 && n === xs.length; m.indeterminate = n > 0 && n < xs.length; } };
window.hpMaster = m => { const d = m.closest("details"); d.querySelectorAll(".pn-hchk").forEach(x => { x.checked = m.checked; }); hpCount(d); pnSelCount(); };
window.pnHQ = v => { pn.hq = v.trim(); clearTimeout(qT); qT = setTimeout(refresh, 250); };
window.pnHGo = i => { pn.hoff = Math.max(0, i) * pnPS(); refresh(); };
window.pnHostAct = (id, sel) => { const v = sel.value; sel.selectedIndex = 0; if (v === "tls") pnHostTls(id); else if (v) pnHostsToggle(id, v === "disable"); };function pnHostHtml(id, h) {
  const txt = ["remark", "address", "port", "sni", "host", "path", "alpn", "fingerprint"], bools = ["allowinsecure", "mux_enable", "random_user_agent", "use_sni_as_host", "is_disabled"];
  return `<form class="frm" onsubmit="return pnHostSave(event, ${id}, '${esc(h.key)}')">${txt.map(k => `<label>${k}${k === "port" ? " <small>(empty = inbound port)</small>" : ""}<input name="${k}" value="${esc(h[k] == null ? "" : h[k])}"></label>`).join("")}
    <label>security<select name="security">${["inbound_default", "none", "tls"].map(s => `<option ${h.security === s ? "selected" : ""}>${s}</option>`).join("")}</select></label>
    ${bools.map(k => `<label class="chk"><input type="checkbox" name="${k}" ${h[k] ? "checked" : ""}> ${k}</label>`).join("")}
    <div class="frm-act"><button class="pri" type="submit">Save host</button><button type="button" onclick="pn.hostEdit=null;refresh()">Close</button></div></form>`;
}
window.pnHostOpen = async (id, key) => { if (pnType() === "pasarguard" && window.pgHostOpen) return pgHostOpen(id, +key); try { pn.hostEdit =(await api(`/api/panels/${id}/hosts/${encodeURIComponent(key)}`)).host; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.pnHostSave = async (e, id, key) => { e.preventDefault(); const f = new FormData(e.target), body = {}; ["remark", "address", "port", "sni", "host", "path", "alpn", "fingerprint", "security"].forEach(k => body[k] = f.get(k)); ["allowinsecure", "mux_enable", "random_user_agent", "use_sni_as_host", "is_disabled"].forEach(k => body[k] = !!f.get(k)); try { await api(`/api/panels/${id}/hosts/${encodeURIComponent(key)}`, {method: "PUT", body: JSON.stringify(body)}); toast("✓ Host saved", "ok"); pn.hostEdit = null; pn.hosts = null; refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.pnHostsToggle = async (id, disable) => { const keys = [...document.querySelectorAll(".pn-hchk:checked")].map(x => x.value); if (!keys.length) { toast("Tick some hosts first", "bad"); return; } try { const r = await api(`/api/panels/${id}/hosts-toggle`, {method: "POST", body: JSON.stringify({keys, disable})}); toast(`✓ ${r.changed} hosts ${disable ? "disabled" : "enabled"}`, "ok"); pn.hosts = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* TLS copies of the ticked plain WebSocket hosts: a copy with port 443, security tls and the Host header as SNI; a real test from the panel server decides whether it is on. The originals are never touched. */
const tls = {id: 0, keys: [], items: [], opts: {port: 443, alpn: "http/1.1", fingerprint: "chrome"}, failed: "disabled"};
window.pnHostTls = async id => {
  const keys = [...document.querySelectorAll(".pn-hchk:checked")].map(x => x.value);
  if (!keys.length) { toast("Tick some hosts first", "bad"); return; }
  Object.assign(tls, {id, keys, items: []});
  showModal("<b>🔒 TLS copies on 443</b>", '<div id="tls-body"></div>', "wide");
  tlsRun();
};
const tlsOpt = (id, list, cur) => `<select id="${id}">${list.map(([v, l]) => `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
function tlsPaint(state, done = 0) {
  const body = $("#tls-body"); if (!body) return;
  const o = tls.opts, opts = `<div class="frm tls-opts"><label>Port<input id="tls-port" type="number" min="1" max="65535" value="${o.port}"></label><label>ALPN${tlsOpt("tls-alpn", [["http/1.1", "http/1.1"], ["h2,http/1.1", "h2, http/1.1"], ["", "none"]], o.alpn)}</label><label>Fingerprint${tlsOpt("tls-fp", [["chrome", "chrome"], ["firefox", "firefox"], ["safari", "safari"], ["ios", "ios"], ["random", "random"], ["", "none"]], o.fingerprint)}</label><button class="sm" ${state === "running" ? "disabled" : ""} onclick="tlsRun()">Test again</button></div>`;
  if (state === "running") { body.innerHTML = `${opts}<div class="loading"><i class="spin"></i><span>Testing from the panel server: ${done} of ${tls.keys.length} hosts (TCP, TLS with the SNI, WebSocket)…</span></div>`; return; }
  const news = tls.items.filter(i => i.status === "new"), pass = news.filter(i => i.check && i.check.ok);
  const res = i => i.status === "made_on" ? '<span class="pill ok">made · ON</span>' : i.status === "made_off" ? '<span class="pill warn">made · OFF</span>' : i.status === "not_made" ? '<span class="pill">not made</span>' : i.status === "exists" ? '<span class="pill">has a copy</span>' : i.status === "skip" ? '<span class="pill">skipped</span>' : (i.check && i.check.ok ? '<span class="pill ok">will be ON</span>' : '<span class="pill warn">will be OFF</span>');
  const test = i => i.check ? (i.check.ok ? `<span class="gw-ok">✓ ${esc(i.check.detail)}</span>` : `<span class="err" title="${esc(i.check.detail)}">✕ ${esc(i.check.stage)}: ${esc(i.check.detail)}</span>`) : `<span class="mut">${esc(i.reason || "")}</span>`;
  const row = i => `<tr><td class="name"><b>${esc(i.remark || i.key)}</b><small class="mut">${esc(i.tag || "")}</small></td><td class="mono mut">${i.twin ? esc(i.address) + ":" + esc(String(i.port == null ? "" : i.port)) + " → :" + i.twin.port + "<br>SNI " + esc(i.twin.sni) : ""}</td><td>${test(i)}</td><td>${res(i)}</td></tr>`;
  const made = tls.items.some(i => /^made|not_made/.test(i.status));
  body.innerHTML = `${made ? "" : opts}<div class="tls-sum"><span class="pill">${tls.items.length} ticked</span><span class="pill ok">${pass.length} pass the test</span>${news.length - pass.length ? `<span class="pill warn">${news.length - pass.length} fail</span>` : ""}${tls.items.length - news.length && !made ? `<span class="pill">${tls.items.length - news.length} skipped or done</span>` : ""}</div>
    <div class="lbox full tls-box"><table class="tbl lift"><colgroup><col style="width:28%"><col style="width:28%"><col><col style="width:14%"></colgroup><thead><tr><th>Host</th><th>Address → copy</th><th>Real test from the panel server</th><th>${made ? "Result" : "Would be"}</th></tr></thead><tbody>${tls.items.map(row).join("")}</tbody></table></div>
    ${made ? `<div class="frm-act"><button class="pri" onclick="closeModal()">Done</button><span class="mut">Saved before the change: ${esc(tls.backup || "")} (data folder / host-backups)</span></div>`
      : `<div class="frm-act"><button class="pri" ${news.length ? "" : "disabled"} onclick="tlsMake()">Make ${news.length} cop${news.length === 1 ? "y" : "ies"}</button><select id="tls-failed" title="What to do with a copy whose test failed">${[["disabled", "a copy that fails is made OFF"], ["skip", "a copy that fails is not made"]].map(([v, l]) => `<option value="${v}" ${tls.failed === v ? "selected" : ""}>${l}</option>`).join("")}</select><button onclick="closeModal()">Close</button><span class="sp"></span><a class="mut" onclick="pnHostTlsRemove()">Remove the copies made earlier</a></div>`}
    <small class="mut">The originals are never touched; the hosts are saved to a file before the change. A copy is ON only when its test passed from this server: this test does not tell what the filter inside Iran does with the SNI, so give a copy to a few users first.</small>`;
}
window.tlsRun = async () => {
  const g = (i, d) => ($(i) || {}).value ?? d;
  tls.opts = {port: +g("#tls-port", 443) || 443, alpn: g("#tls-alpn", "http/1.1"), fingerprint: g("#tls-fp", "chrome")};
  tls.items = []; tlsPaint("running", 0);
  let done = 0; const batches = []; for (let i = 0; i < tls.keys.length; i += 6) batches.push(tls.keys.slice(i, i + 6));
  await Promise.all(batches.map(async b => {
    try { tls.items.push(...(await api(`/api/panels/${tls.id}/hosts-tls/plan`, {method: "POST", body: JSON.stringify({keys: b, ...tls.opts})})).items); } catch (e) { b.forEach(k => tls.items.push({key: k, status: "skip", remark: k, reason: e.message})); }
    done += b.length; tlsPaint("running", done);
  }));
  tls.items.sort((a, b) => tls.keys.indexOf(a.key) - tls.keys.indexOf(b.key)); tlsPaint("ready");
};
window.tlsMake = async () => {
  tls.failed = ($("#tls-failed") || {}).value || "disabled";
  const keys = tls.items.filter(i => i.status === "new").map(i => i.key), out = [];
  tlsPaint("running", 0);
  try {
    for (let i = 0; i < keys.length; i += 6) { const r = await api(`/api/panels/${tls.id}/hosts-tls`, {method: "POST", body: JSON.stringify({keys: keys.slice(i, i + 6), ...tls.opts, failed: tls.failed})}); out.push(...r.items); tls.backup = r.backup || tls.backup; tlsPaint("running", Math.min(keys.length, i + 6)); }
    const by = Object.fromEntries(out.map(x => [x.key, x])); tls.items = tls.items.map(i => by[i.key] || i);
    toast(`✓ ${out.filter(x => x.status === "made_on").length} copies made and ON, ${out.filter(x => x.status === "made_off").length} made OFF`, "ok"); pn.hosts = null; refresh();
  } catch (e) { toast("✕ " + e.message, "bad"); }
  tlsPaint("ready");
};
window.pnHostTlsRemove = async () => {
  if (!await gconfirm("Remove every TLS copy made by this tool (the hosts whose name ends with TLS and whose security is tls)? The hosts are saved to a file first. The original hosts stay.", {danger: true})) return;
  try { const r = await api(`/api/panels/${tls.id}/hosts-tls/remove`, {method: "POST"}); toast(`✓ ${r.removed} copies removed`, "ok"); closeModal(); pn.hosts = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.pnHostClone = async (id, key) => { if (pnType() === "pasarguard" && window.pgHostClone) return pgHostClone(id, +key); try { await api(`/api/panels/${id}/hosts/${encodeURIComponent(key)}/clone`, {method: "POST"}); toast("✓ Cloned", "ok"); pn.hosts = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.pnHostDel = async (id, key, name) => { if (pnType() === "pasarguard" && window.pgHostDelete) return pgHostDelete(id, +key, name); if (!await gconfirm(`Delete host ${name} from the panel? Users lose that address in their links.`)) return; try { await api(`/api/panels/${id}/hosts/${encodeURIComponent(key)}`, {method: "DELETE"}); toast("✓ Deleted", "ok"); pn.hosts = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };

/* (the Xray outbounds of a panel's core are no longer edited here: the Outbound page is the one place; the API /api/panels/{id}/core stays) */

/* ---- shop bot (PHP bot on its own server, over SSH) */
INFO.bot = ["Shop bot", "Your sales bot, run from here: payments to confirm, orders, users, plans and prices, the panels it sells from, discounts and gift codes, broadcasts, tickets, every text and switch."];
const sb = {tab: localStorage.getItem("nk.sb.tab") || "overview", info: null, infoAt: 0, uq: "", table: "users", tpage: 0, tq: "", sql: "", qres: null, payState: "confirm", payPage: 0, payQ: "", ordQ: "", ordPage: 0, ordActive: false,   // tool defaults to the Mini App card
            tkState: "open", tkPage: 0, txKind: "text", txQ: "", txData: null, tool: "miniapp", stData: null};
const SB_TABS = [["overview", "Overview"], ["payments", "Payments"], ["orders", "Orders"], ["users", "Users"], ["partners", "Partners"], ["plans", "Plans"], ["categories", "Categories"], ["servers", "Servers"], ["discounts", "Discounts"], ["gifts", "Gift codes"], ["broadcast", "Broadcast"], ["tickets", "Tickets"], ["texts", "Texts"], ["settings", "Settings"], ["tools", "Tools"]];
const sbDisMap = () => { try { return JSON.parse(localStorage.getItem("nk.sb.dis") || "{}"); } catch (e) { return {}; } };      // what you closed in "Needs attention": kept with the count it had; it comes back when the count grows
const sbDis = (k, n) => !!n && (sbDisMap()[k] || 0) >= n;
window.sbDismiss = (k, n) => { const m = sbDisMap(); m[k] = n; try { localStorage.setItem("nk.sb.dis", JSON.stringify(m)); } catch (e) { /* the item just stays */ } refresh(); };
window.sbDismissClear = () => { try { localStorage.removeItem("nk.sb.dis"); } catch (e) { /* nothing to clear */ } refresh(); };
const SB_COL = {overview: "#8b93a7", payments: "#4da3ff", orders: "#d0607f", plans: "#d99a52", categories: "#3aa6b9", discounts: "#bf8cff", gifts: "#32d74b", users: "#4da3ff", partners: "#64d2ff", tickets: "#ffd60a", broadcast: "#d0607f", servers: "#4da3ff", texts: "#8b93a7", settings: "#9b72e0", tools: "#d99a52"};      // the icon colours of the design
const SB_QUICK = false;      // the quick-action cards of the overview: switched off for now (a calmer page); true brings them back
const SB_GROUPS = [["Home", ["overview"]], ["Sell", ["payments", "orders", "plans", "categories", "discounts", "gifts"]], ["People", ["users", "partners", "tickets", "broadcast"]], ["Setup", ["servers", "texts", "settings", "tools"]]];      // the tabs by what you do with them
const SB_IC = () => ({overview: SI.home, payments: SI.card, orders: SI.cart, users: SI.users, partners: SI.partner, plans: SI.doc, categories: SI.gridi, servers: SI.server, discounts: SI.tag, gifts: SI.gift, broadcast: SI.megaphone, tickets: SI.ticket, texts: SI.textt, settings: SI.rules, tools: SI.wrench});
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
  const chip = (label, on, extra = "") => `<button type="button" class="sb-chip ${on ? "ok" : "off"}" ${extra}><i></i>${label}</button>`;
  const unit = k => k ? chip(`${esc(k)} ${esc(u[k])}`, u[k] === "active") : chip("missing", false);
  const cronChip = d.cron ? chip(`cron ${d.cron} jobs`, true, `title="Click to choose which scheduled jobs run" onclick="sbCron()"`) : chip(`cron off<span class="sb-start">Start</span>`, false, `title="Cron is off. Click to choose the jobs to start." onclick="sbCron()"`);
  const n = {pending: sbDis("payments", c.pending_pays || 0) ? 0 : c.pending_pays || 0, tickets: sbDis("tickets", d.tickets || 0) ? 0 : d.tickets || 0, queue: sbDis("queue", d.queue || 0) ? 0 : d.queue || 0};
  const attn = (d.stage_users != null && d.stage_users > (c.users || 0) && !sbDis("stage", d.stage_users)) ? 1 : 0;
  const tabLabel = Object.fromEntries(SB_TABS), badge = k => k === "payments" && n.pending ? n.pending : k === "tickets" && n.tickets ? n.tickets : k === "overview" && attn ? "!" : "";
  const stat = (l, v, unitT, sub) => `<div class="sb-card sb-stat"><span class="sb-lab">${l}</span><span class="sb-sv"><b>${v}</b>${unitT ? `<em>${unitT}</em>` : ""}</span><span class="sb-ss">${sub}</span></div>`;
  const inc = x => fmt((x || {}).income || 0);
  pg.innerHTML = `<div class="sb-head"><div class="sb-card sb-svc"><span class="sb-lab">Services</span><div class="sb-chips">${chip(d.installed ? "installed" : "not installed", !!d.installed)}${unit(php)}${unit(dbu)}${unit(web)}${cronChip}</div></div>
      <div class="sb-stat4">${stat("Users", fmt(c.users), "", `+${s.new_users_today ?? "—"} today`)}${stat("Today", inc(s.today), "T", `${(s.today || {}).orders ?? "—"} orders`)}${stat("7 days", inc(s.week), "T", `${(s.week || {}).orders ?? "—"} orders`)}${stat("30 days", inc(s.month), "T", `${(s.month || {}).orders ?? "—"} orders`)}</div></div>
    <nav class="sb-nav">${SB_GROUPS.map(([g, ks]) => `<div class="sb-ng"><span class="sb-gl">${g}</span><div class="sb-gbox">${ks.map(k => `<button type="button" class="sb-nt ${sb.tab === k ? "on" : ""}" style="--ic:${SB_COL[k]}" onclick="sbTab('${k}')"><i class="ti">${SB_IC()[k]}</i>${tabLabel[k]}${badge(k) ? `<small>${badge(k)}</small>` : ""}</button>`).join("")}</div></div>`).join("")}</nav>
    <div id="sb-body"><div class="loading"><i class="spin"></i><span>Loading…</span></div></div>`;
  sbFitBody();
    const load = {overview: sbOverview, payments: sbPays, orders: sbOrders, users: sbUsersLoad, partners: sbPartners, plans: sbPlans, categories: sbCategories, servers: sbServers, discounts: sbDiscounts, gifts: sbGifts, broadcast: sbBroadcast, tickets: sbTickets, texts: sbTexts, settings: sbSettings, tools: sbTools}[sb.tab] || sbOverview;
  try { await load(); } catch (e) { const b = $("#sb-body"); if (b) b.innerHTML = `<div class="page-err"><b>This tab could not be loaded</b><span>${esc(e.message)}</span><button class="sm" onclick="refresh()">Try again</button></div>`; }
};
const sbBody = html => {      // a search box that is typed in keeps its focus and caret when its tab draws again: typing never jumps
  const b = $("#sb-body"); if (!b) return;
  const a = document.activeElement, key = a && b.contains(a) && a.matches("input[type=search], input[type=text], textarea") ? a.id || a.getAttribute("placeholder") || a.name : "", pos = key ? [a.selectionStart, a.selectionEnd] : null;
  b.innerHTML = html;
  if (!key) return;
  const n = [...b.querySelectorAll("input[type=search], input[type=text], textarea")].find(x => (x.id || x.getAttribute("placeholder") || x.name) === key);
  if (n) { n.focus({preventScroll: true}); try { n.setSelectionRange(pos[0], pos[1]); } catch (e) { /* a type without a caret */ } }
};
/* ---- overview */
/* ---- partners: the users the bot treats as agents (is_agent) - their discount, wallet and what they bought ---- */
const SB_PT = {marzban: ["Marzban", "#4da3ff"], sanaei: ["Sanaei", "#32d74b"], xui: ["3x-ui", "#32d74b"], "3xui": ["3x-ui", "#32d74b"], pasarguard: ["PasarGuard", "#bf8cff"], marzneshin: ["Marzneshin", "#ff9f0a"]};      // the panel kinds the bot can sell from
/* panels with the same kind and address are one line with their count: nine ids on one address were nine identical rows */
const sbPanelRows = list => {
  const g = {}; list.forEach(p => { const k = p.type + "|" + p.host; (g[k] = g[k] || {type: p.type, host: p.host, ids: []}).ids.push(p.id); });
  const rows = Object.values(g).sort((a, b) => b.ids.length - a.ids.length);
  return rows.map(r => { const [name, col] = SB_PT[String(r.type).toLowerCase()] || [String(r.type || "?").replace(/^./, c => c.toUpperCase()), "#9aa4b8"]; return `<div class="sb-prow"><span class="sb-ptype" style="--pc:${col}">${esc(name)}</span><span class="mono">${esc(r.host)}</span>${r.ids.length > 1 ? `<span class="sb-pn2" title="${esc(r.ids.map(i => "#" + i).join(", "))}">×${r.ids.length}</span>` : `<b class="mut">#${esc(r.ids[0])}</b>`}<button type="button" class="sb-cp" onclick="sbCopy(this, '${esc(r.host)}')">Copy</button></div>`; }).join("") || '<div class="mut">none yet — add one in Servers</div>';
};async function sbOverview() {
  const d = sb.info, c = d.counts || {}, raw = {pending: c.pending_pays || 0, tickets: d.tickets || 0, queue: d.queue || 0}, rawErrs = (d.logs.errors || []).length, hid = [];
  const n = {pending: sbDis("payments", raw.pending) ? (hid.push(1), 0) : raw.pending, tickets: sbDis("tickets", raw.tickets) ? (hid.push(1), 0) : raw.tickets, queue: sbDis("queue", raw.queue) ? (hid.push(1), 0) : raw.queue};
  const errs = sbDis("errors", rawErrs) ? (hid.push(1), 0) : rawErrs, hasStage = d.stage_users != null && d.stage_users > (c.users || 0) && (sb.imp ? true : !sbDis("stage", d.stage_users) || (hid.push(1), false));
  const need = [];
  if (n.pending) need.push(["warn", SI.card, `<b>${n.pending}</b> ${n.pending > 1 ? "payments" : "payment"} to confirm`, "People sent a receipt: approve or decline each one.", `<button type="button" class="sb-btn pri" onclick="sbTab('payments')">Open payments</button>`, ["payments", n.pending]]);
  if (n.tickets) need.push(["warn", SI.ticket, `<b>${n.tickets}</b> open ${n.tickets > 1 ? "tickets" : "ticket"}`, "Answer the users who wrote to you.", `<button type="button" class="sb-btn" onclick="sbTab('tickets')">Open tickets</button>`, ["tickets", n.tickets]]);
  if (n.queue) need.push(["info", SI.megaphone, `<b>${n.queue}</b> broadcast running`, "A broadcast is being sent to the users.", `<button type="button" class="sb-btn" onclick="sbTab('broadcast')">Open broadcast</button>`, ["queue", n.queue]]);
  if (hasStage) {
    const ph = sb.imp || "ask";
    need.push([ph === "busy" ? "info" : ph === "confirm" ? "bad" : "info", SI.db, ph === "busy" ? "Importing the real database" : ph === "confirm" ? "Replace the live database?" : "Real database waiting",
      ph === "busy" ? `Dumping the live database, then copying ${fmt(d.stage_users)} users. Keep this page open.` : ph === "confirm" ? `The live database is dumped first as a backup. Broadcast queues are not copied. The bot has ${fmt(c.users)} users now; the copy has ${fmt(d.stage_users)}.` : `The staging copy on the bot server holds ${fmt(d.stage_users)} users, the live bot has ${fmt(c.users)}. The live database is dumped first; broadcast queues are not copied.`,
      ph === "busy" ? `<span class="sb-busy"><i class="spin"></i>Importing…</span>` : ph === "confirm" ? `<button type="button" class="sb-btn" onclick="sbImportStep('ask')">Cancel</button><button type="button" class="sb-btn red" onclick="sbImportGo()">Dump live DB, then import</button>` : `<button type="button" class="sb-btn pri" onclick="sbImportStep('confirm')"><i class="bi">${SI.down}</i>Import the real database now</button>`, ph === "ask" ? ["stage", d.stage_users] : null]);
  }
  if (errs) need.push(["info", SI.doc, `<b>${errs}</b> ${errs > 1 ? "errors" : "error"} in the bot log`, "Look at them below; the log keeps the latest ones.", `<button type="button" class="sb-btn" onclick="const l=document.querySelector('.sb-logd');l.open=true;l.scrollIntoView({behavior:'smooth',block:'center'})">Show the log</button>`, ["errors", errs]]);
  const row = ([tone, ic, t, sub, btn, dis]) => `<div class="sb-ar ${tone}"><i class="ss-ic">${ic}</i><div class="si-t"><b>${t}</b><small>${sub}</small></div><span class="si-a">${btn}</span>${dis ? `<button type="button" class="sb-x" aria-label="Dismiss" title="Hide this until it changes" onclick="sbDismiss('${dis[0]}', ${dis[1]})"><svg viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8"/></svg></button>` : ""}</div>`;
  const attn = need.length ? need.map(row).join("") : row(["ok", SI.check, "All clear", "No payment, ticket or broadcast is waiting.", ""]);
  const when = f => { const m = /(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(f); return m ? `${+m[3]} ${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][+m[2] - 1]}, ${m[4]}:${m[5]}` : ""; };
  const stamp = f => { const m = /(\d{8}-\d{6})/.exec(f); return m ? m[1] : ""; };
  const bks = (d.backups || []).slice().sort((a, b) => stamp(b).localeCompare(stamp(a))).slice(0, 6);
  sbBody(`<div class="sb-card sb-attn"><div class="sb-ch"><span class="sb-lab">Needs attention</span><span class="sb-count ${need.length ? "on" : ""}">${need.length}</span>${hid.length ? `<button type="button" class="sb-link" onclick="sbDismissClear()">Show hidden (${hid.length})</button>` : ""}</div>${attn}</div>
    <div class="sb-two"><div class="sb-card"><div class="sb-ch"><i class="sb-ci" style="color:#4da3ff">${SI.send}</i><span class="sb-lab">Telegram</span><span id="sb-wh" class="sb-whs"></span></div><div id="sb-tg" class="sb-tgrid"><div class="mut">asking Telegram…</div></div></div>
    <div class="sb-card"><div class="sb-ch"><i class="sb-ci" style="color:#d99a52">${SI.gridi}</i><span class="sb-lab">Panels the bot sells from</span><span class="sb-pc">${(d.panels || []).length}</span><button type="button" class="sb-btn sm" onclick="sbTab('servers')">Manage</button></div><div class="sb-plist">${sbPanelRows(d.panels || [])}</div></div></div>
    <div class="sb-card"><div class="sb-ch"><i class="sb-ci" style="color:#9aa4b8">${SI.doc}</i><span class="sb-lab">Bot log</span><span class="sb-lchip ${errs ? "warn" : "ok"}"><i></i>${errs ? errs + " errors" : "No errors logged"}</span><span class="sb-right">Backups (newest first)</span></div>
      <div class="sb-bks">${bks.map(f => `<span class="sb-bk"><b>${when(f)}</b><span class="mono">${esc(f)}</span></span>`).join("") || '<span class="mut">no backup yet</span>'}</div>
      <details class="sb-logd"><summary>Show the log</summary><pre class="gl-out">${esc((d.logs.errors || []).join("\n") || "(no errors logged)")}</pre>${(d.logs.php || []).length ? `<pre class="gl-out">${esc(d.logs.php.join("\n"))}</pre>` : ""}</details></div>`);
  try { const t = await api("/api/shopbot/telegram"); const box = $("#sb-tg"), wh = $("#sb-wh"), url = t.webhook.url || "";
    if (wh) wh.className = "sb-whs " + (t.webhook.last_error ? "warn" : url ? "ok" : "bad"), wh.innerHTML = `<i></i>${t.webhook.last_error ? "Webhook error" : url ? "Webhook set" : "Webhook not set"}`;
    if (box) box.innerHTML = `<div class="sb-kv"><span>Bot</span><span class="sb-v"><b>@${esc(t.bot.username || "?")}</b><button type="button" class="sb-cp" onclick="sbCopy(this, '@${esc(t.bot.username || "")}')">Copy</button></span><small>${esc(t.bot.name || "")}</small></div><div class="sb-kv"><span>Webhook</span><span class="sb-v"><span class="mono">${esc(url || "not set")}</span>${url ? `<button type="button" class="sb-cp" onclick="sbCopy(this, '${esc(url)}')">Copy</button>` : ""}</span>${t.webhook.last_error ? `<small class="err">${esc(t.webhook.last_error)} · ${ago(t.webhook.last_error_at)} ago</small>` : ""}</div><div class="sb-kv"><span>Pending updates</span><span class="sb-pn">${t.webhook.pending}</span></div>`; }
  catch (e) { const box = $("#sb-tg"); if (box) box.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}
window.sbCopy = (b, text) => { try { navigator.clipboard.writeText(text); } catch (e) { /* the label says what happened */ } b.textContent = "Copied"; b.classList.add("on"); setTimeout(() => { b.textContent = "Copy"; b.classList.remove("on"); }, 1400); };
window.sbImportStep = st => { sb.imp = st; sbOverview(); };
window.sbImportGo = async () => { sb.imp = "busy"; sbOverview(); try { const r = await api("/api/shopbot/import-stage", {method: "POST"}); toast(`✓ Imported: ${r.tables.users} users, ${r.tables.orders_list} orders · backup ${r.backup.split("/").pop()}`, "ok"); sb.imp = null; sb.infoAt = 0; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); sb.imp = "ask"; sbOverview(); } };
/* ---- payments */
/* the payments tab like Payments.dc.html: filter tabs with counts, a search, a pager, rows with Approve / Decline that ask inside the row */
Object.assign(sb, {payAsk: null, payData: null});
const SB_PTYPE = {INCREASE_WALLET: "Wallet top-up", BUY_SUB: "Subscription", RENEW_ACCOUNT: "Renewal", INCREASE_DAY: "Extra days", INCREASE_VOLUME: "Extra volume"};
async function sbPays() {
  const defs = [["confirm", "To confirm"], ["pending", "Awaiting receipt"], ["approved", "Approved"], ["declined", "Declined"], ["all", "All"]];
  sbBody(`<div class="sb-card sb-ptool"><div id="pay-tabs" class="sb-ptabs">${defs.map(([k, l]) => `<button type="button" class="sb-ptab ${sb.payState === k ? "on" : ""}" onclick="sb.payState='${k}';sb.payPage=0;sbPaysLoad()">${l}<span id="pc-${k}">…</span></button>`).join("")}</div>
      <label class="sb-psearch"><i>${SI.search}</i><input id="pay-q" type="text" placeholder="User id, name, hash…" value="${esc(sb.payQ)}" oninput="sb.payQ=this.value.trim();sb.payPage=0;clearTimeout(qT);qT=setTimeout(sbPaysLoad,350)" autocomplete="off" spellcheck="false"></label>
      <span class="sb-pager"><small id="pay-pg"></small></span></div>
    <div class="sb-card sb-ptable"><div class="sb-phead"><span>#</span><span>User</span><span class="r">Amount</span><span>Type</span><span>Asked</span><span>State</span><span class="r">Action</span></div><div id="pay-rows" class="sb-prows"></div><div class="sb-pfoot"><span id="pay-pager" class="pgbar"></span></div></div>`);
  sbPayFit(); if (sb.payCounts) Object.entries(sb.payCounts).forEach(([k, v]) => { const e = $("#pc-" + k); if (e) e.textContent = fmt(v); });
  await sbPaysLoad();
}
/* the rows of a page fill the window exactly (56 px each): the list never scrolls and a short last page keeps the same box, so nothing moves */
const sbPayRowH = 46;
function sbFitBox(id, base = 46, foot = 44, pad = 24) {      // whole rows, a little taller if needed, so the list box ends at the bottom of the page and never scrolls
  const box = $("#" + id); if (!box) return 5;
  if (isPhone()) { box.style.height = ""; box.style.removeProperty("--prh"); return 8; }      // a phone: rows are cards of their own height, eight to a page, the page scrolls
  const avail = Math.floor(mainBottom() - box.getBoundingClientRect().top - foot - pad), ps = Math.max(5, Math.floor(avail / base)), rh = Math.floor(avail / ps * 10) / 10;
  box.style.height = ps * rh + "px"; box.style.setProperty("--prh", rh + "px"); return ps;
}
function sbPayFit() { sb.payPS = sbFitBox("pay-rows", sbPayRowH); }
/* the tabs that are not lists of their own keep their content in one fixed box down to the bottom of the page (it scrolls inside only when it is longer) */
function sbFitBody() {
  const b = $("#sb-body"); if (!b) return;
  if (isPhone()) { b.style.height = ""; b.classList.remove("fit", "self"); return; }      // a phone: the page scrolls as usual
  b.classList.add("fit"); b.classList.toggle("self", sbSelf.includes(sb.tab));
  b.style.height = Math.max(240, Math.floor(mainBottom() - b.getBoundingClientRect().top - 24)) + "px";
}
addEventListener("resize", () => { clearTimeout(sbFitBody.t); sbFitBody.t = setTimeout(() => {
  if (!document.querySelector("#page .sb-nav")) return;
  sbFitBody();
  if (sb.tab === "orders" && $("#ord-rows")) { const old = sb.ordPS; sb.ordPS = sbFitBox("ord-rows"); if (sb.ordPS !== old) { sb.ordPage = 0; sbOrdLoad(); } }
  if (sbLR.cur && $("#ls-rows")) { const L = sbLR.cur, was = L.ps, from = L.page * was; L.ps = sbFitBox("ls-rows", L.base || 46); if (L.ps !== was) sbListLoad(Math.floor(from / L.ps)); }
  if (sb.tab === "plans" && $("#pl-rows")) { const old = sb.pl.ps; sb.pl.ps = sbFitBox("pl-rows", 44); if (sb.pl.ps !== old) sbPlDraw(); }
}, 200); });
addEventListener("resize", () => { clearTimeout(sbPayFit.t); sbPayFit.t = setTimeout(() => { if (sb.tab === "payments" && $("#pay-rows")) { const old = sb.payPS; sbPayFit(); if (sb.payPS !== old) { sb.payPage = 0; sbPaysLoad(); } } }, 200); });
async function sbPaysLoad() {
  const r = await api(`/api/shopbot/pays?state=${sb.payState}&page=${sb.payPage}&q=${encodeURIComponent(sb.payQ)}&limit=${sb.payPS || 0}`);
  sb.payData = r; sb.payAsk = null; sbPaysDraw();
}
function sbPaysDraw() {
  const r = sb.payData; if (!r || !$("#pay-rows")) return;
  sb.payCounts = r.counts || sb.payCounts; Object.entries(sb.payCounts || {}).forEach(([k, v]) => { const e = $("#pc-" + k); if (e) e.textContent = fmt(v); });
  document.querySelectorAll("#pay-tabs .sb-ptab").forEach((b, i) => b.classList.toggle("on", ["confirm", "pending", "approved", "declined", "all"][i] === sb.payState));
  const per = r.per || 40, from = r.page * per + 1, to = r.page * per + r.rows.length, pg = $("#pay-pg"), pbar = $("#pay-pager");
  if (pg) pg.textContent = r.total === 0 ? "0 rows" : r.total <= per ? `${r.total} rows` : `${from}–${to} of ${fmt(r.total)}`;
  if (pbar) pbar.innerHTML = r.total > per ? `<small class="mut">${r.total === 0 ? "" : `Page ${r.page + 1} of ${Math.ceil(r.total / per)}`}</small>${pnPager(r.total, per, r.page * per, "sbPayPage")}` : "";
  const chip = s => s === "have_sent" ? ["receipt sent", "warn"] : s === "pending" ? ["awaiting receipt", "wait"] : s === "approved" ? ["approved", "ok"] : s === "declined" ? ["declined", "bad"] : [String(s || "").replace(/_/g, " "), "wait"];
  const dt = v => { const n = +v; if (!(n > 1000000000)) return [esc(v || "—"), ""]; const d = new Date(n * 1000); return [d.toLocaleDateString("en-GB", {day: "numeric", month: "short", year: "numeric"}), d.toLocaleTimeString("en-GB")]; };
  const ask = sb.payAsk;
  $("#pay-rows").innerHTML = r.rows.map(p => {
    const [sl, sc] = chip(p.state), [d1, d2] = dt(p.request_date), open = p.state === "have_sent" || p.state === "pending", asking = ask && ask.id === String(p.id), appr = asking && ask.act === "approve";
    const money = fmt(p.price) + " T", sub = p.username && p.username !== "NULL" && p.username !== "ندارد" ? "@" + esc(p.username) : esc(p.user_id);
    const acts = asking ? `<span class="sb-pask">${appr ? `Credit ${money}?` : `<input id="pay-why" class="sb-why" placeholder="Reason for the user (optional)" maxlength="200">`}</span><button type="button" class="sb-btn sm0" onclick="sbPayAsk(null)">Cancel</button><button type="button" class="sb-btn ${appr ? "pri" : "red"} sm0" onclick="sbPayGo(${p.id}, '${ask.act}')">${appr ? "Approve" : "Decline"}</button>`
      : p.state === "have_sent" ? `<button type="button" class="sb-btn pri sm0" aria-label="Approve payment ${p.id}" onclick="sbPayAsk('${p.id}', 'approve')"><svg viewBox="0 0 16 16"><path d="M3.5 8.5l3 3L12.5 5"/></svg>Approve</button><button type="button" class="sb-btn dec sm0" aria-label="Decline payment ${p.id}" onclick="sbPayAsk('${p.id}', 'decline')"><svg viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8"/></svg>Decline</button>`
      : `<span class="sb-pdone">${p.state === "pending" ? "Waiting for the user to send a receipt" : p.state === "approved" ? `Approved${p.approved_by && p.approved_by !== "NULL" ? " · " + esc(p.approved_by) : ""}` : p.state === "declined" ? "Declined" : ""}</span>`;
    return `<div class="sb-prow2 ${asking ? (appr ? "ask-ok" : "ask-no") : ""}"><span class="mono">${esc(p.id)}</span><span class="sb-pu"><b dir="auto" onclick="sbUser('${esc(p.user_id)}')">${esc(p.name && p.name !== "NULL" ? p.name : p.user_id)}</b><small dir="auto">${sub}</small></span><span class="r sb-pa"><b>${fmt(p.price)}</b> <em>T</em></span><span class="sb-pt"><b>${esc(SB_PTYPE[p.type] || String(p.type || "—").replace(/_/g, " ").toLowerCase().replace(/^./, c => c.toUpperCase()))}</b><small class="mono">${esc(p.type || "")}</small></span><span class="sb-pd"><b>${d1}</b><small>${d2}</small></span><span><span class="sb-pc2 ${sc}">${sl}</span></span><span class="sb-pact">${acts}</span></div>`;
  }).join("") || `<div class="sb-pempty"><i>${SI.check}</i>${sb.payQ ? `No payment matches “${esc(sb.payQ)}”.` : sb.payState === "confirm" ? "Nothing to confirm. All receipts are handled." : "No payments in this list."}</div>`;
}
window.sbPayAsk = (id, act) => { sb.payAsk = id ? {id, act} : null; sbPaysDraw(); };
window.sbPayGo = async (id, act) => {
  const body = act === "decline" ? {reason: ($("#pay-why") || {}).value || ""} : {};
  try { const r = await api(`/api/shopbot/pays/${id}/${act}`, {method: "POST", body: JSON.stringify(body)}); toast(act === "approve" ? `✓ Approved #${id} — wallet now ${money(r.wallet)}` : `✓ Declined #${id}`, "ok"); sb.infoAt = 0; sb.payAsk = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};window.sbPayPage = p => { sb.payPage = Math.max(0, p); sbPaysLoad(); };
window.sbPayDo = async (id, act) => {
  let body = {};
  if (act === "approve") { if (!await gconfirm(`Approve payment #${id}? The amount goes to the user's wallet and the user is told on Telegram.`)) return; }
  else { const why = await gprompt("Why is it declined? (the user reads this)", ""); if (why == null) return; body = {reason: why}; }
  try { const r = await api(`/api/shopbot/pays/${id}/${act}`, {method: "POST", body: JSON.stringify(body)}); toast(act === "approve" ? `✓ Approved — wallet now ${money(r.wallet)}` : "✓ Declined", "ok"); sb.infoAt = 0; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
/* ---- one engine for the lists of the shop bot: whole rows that fit the window, a numbered pager at the bottom, no scroll, and nothing moves when you type, click or add a button ---- */
const sbLR = {cur: null};
const sbSelf = ["payments", "orders", "plans", "users", "partners", "categories", "servers", "discounts", "gifts", "broadcast", "tickets", "texts", "settings", "tools"];      // the tabs that are a list: their box is the list itself
const sbDate = v => { const n = +v; return n > 1000000000 ? new Date(n * 1000).toLocaleDateString("en-GB", {day: "numeric", month: "short", year: "numeric"}) : "—"; };
const sbMinW = cols => { let w = 28, n = 0; for (const m of cols.matchAll(/minmax\((\d+)px[^)]*\)|(\d+)px|[\d.]+fr/g)) { w += +(m[1] || m[2] || 0); n++; } return w + Math.max(0, n - 1) * 12; };      // the narrowest a row may be: the fixed columns, the minimum of the flexible ones and the gaps
const sbListCard = (cols, minw, heads) => `<div class="sb-card sb-ptable" style="--minw:${sbMinW(cols)}px"><div class="sb-lh" style="--cols:${cols}">${heads.map(([l, c]) => `<span class="${c || ""}">${l}</span>`).join("")}</div><div id="ls-rows" class="sb-lrows"></div><div class="sb-pfoot"><span id="ls-pager" class="pgbar"></span></div></div>`;
const sbPlusIc = '<svg viewBox="0 0 16 16"><path d="M8 3v10M3 8h10"/></svg>';
const sbSw = (on, click, label, title = "") => `<span class="sb-swc"><button type="button" class="sb-tog ${on ? "on" : ""}" role="switch" aria-checked="${on}" aria-label="${esc(label)}" title="${esc(title)}" onclick="${click}"><i></i></button><em class="${on ? "ok" : ""}">${on ? "On" : "Off"}</em></span>`;
const sbAskRow = (first, msg, n, acts) => `${first}<span class="sb-pask" style="grid-column:2 / ${n}">${msg}</span><span class="sb-pact">${acts}</span>`;      // a row that asks "sure?" inside itself: nothing opens, nothing moves
const sbClient = get => async (page, ps) => { const a = get(); return {items: a.slice(page * ps, page * ps + ps), total: a.length}; };
async function sbListInit(o) {      // o: {cols, base, fetch(page, ps) -> {items, total}, row(item, ask) -> cells, cls(item), key(item), empty(), after(r)}
  const L = sbLR.cur = Object.assign({page: 0, ps: 0, ask: 0, last: null, seq: 0}, o);
  L.ps = sbFitBox("ls-rows", L.base || 46);
  await sbListLoad(0);
}
async function sbListLoad(page) {
  const L = sbLR.cur; if (!L || !$("#ls-rows")) return;
  if (page != null) L.page = Math.max(0, page);
  const seq = ++L.seq; let r;
  try { r = await L.fetch(L.page, L.ps); } catch (e) { if (L === sbLR.cur && $("#ls-rows")) $("#ls-rows").innerHTML = `<div class="sb-pempty">${esc(e.message)}</div>`; return; }
  if (L !== sbLR.cur || seq !== L.seq || !$("#ls-rows")) return;      // another request or another tab came first
  const last = Math.max(0, Math.ceil(r.total / L.ps) - 1);
  if (L.page > last) { L.page = last; return sbListLoad(); }
  if (r.cols) { L.cols = r.cols; L.columns = r.columns; const h = $(".sb-ptable .sb-lh"); if (h) { h.style.setProperty("--cols", r.cols); h.innerHTML = (r.heads || []).map(([l, c]) => `<span class="${c || ""}">${l}</span>`).join(""); h.closest(".sb-ptable").style.setProperty("--minw", sbMinW(r.cols) + "px"); } }
  L.last = r; L.ask = 0; sbListDraw(); if (L.after) L.after(r);
}
function sbListDraw() {
  const L = sbLR.cur, r = L && L.last, box = $("#ls-rows"); if (!r || !box) return;
  const from = L.page * L.ps, key = it => String(L.key ? L.key(it) : it.id);
  box.innerHTML = r.items.map(it => { const ask = !!L.ask && key(it) === String(L.ask); return `<div class="sb-lr ${ask ? "ask-no" : ""} ${L.cls ? L.cls(it) : ""}" style="--cols:${L.cols}">${L.row(it, ask)}</div>`; }).join("")
    || `<div class="sb-pempty"><i>${SI.check}</i>${L.empty ? L.empty() : "Nothing here."}</div>`;
  $("#ls-pager").innerHTML = r.total ? `<small class="mut">${r.total > L.ps ? `${from + 1}–${from + r.items.length} of ${fmt(r.total)}` : `${fmt(r.total)} rows`}</small>${r.total > L.ps ? pnPager(r.total, L.ps, from, "sbListPage") : ""}` : "";
}
window.sbListPage = p => sbListLoad(p);
/* a phone: every cell of a list row gets the column name of its header as a small label (the header row itself is hidden there), so a
   bare number still says what it is. Runs on any redraw inside the shop bot page; does nothing on a wide screen. */
function sbPhoneLabels() {
  if (!isPhone()) return;
  document.querySelectorAll("#page .sb-ptable").forEach(tbl => {
    const heads = [...tbl.querySelectorAll(":scope > .sb-lh > span, :scope > .sb-phead > span")].map(s => s.textContent.trim());
    if (!heads.length) return;
    tbl.querySelectorAll(".sb-lr, .sb-prow2").forEach(row => [...row.children].forEach((c, i) => {
      if (c.dataset.h !== undefined) return;
      const h = heads[i] || "";
      c.dataset.h = c.querySelector("button, .sb-tog") || c.classList.contains("sb-pask") || c.classList.contains("sb-pu") || h === "#" ? "" : h;
    }));
  });
}
new MutationObserver(() => { clearTimeout(sbPhoneLabels.t); sbPhoneLabels.t = setTimeout(sbPhoneLabels, 60); }).observe(document.body, {childList: true, subtree: true});
addEventListener("resize", () => { clearTimeout(sbPhoneLabels.r); sbPhoneLabels.r = setTimeout(sbPhoneLabels, 250); });
window.sbAsk = id => { const L = sbLR.cur; if (L) { L.ask = id; sbListDraw(); } };

/* ---- users ---- */
Object.assign(sb, {uq: "", uKind: "all", uCounts: null});
const U_COLS = "110px minmax(170px, 1.4fr) 110px 64px 80px 70px 110px 230px";
async function sbUsersLoad() {
  const tabs = [["all", "All"], ["agents", "Partners"], ["banned", "Banned"]];
  sbBody(`<div class="sb-card sb-ptool"><div class="sb-ptabs" id="u-tabs">${tabs.map(([k, l]) => `<button type="button" class="sb-ptab ${sb.uKind === k ? "on" : ""}" data-k="${k}" onclick="sbUKind('${k}')">${l}<span id="uc-${k}">${sb.uCounts ? fmt(sb.uCounts[k]) : "…"}</span></button>`).join("")}</div>
      <label class="sb-psearch"><i>${SI.search}</i><input id="sb-uq" type="text" placeholder="Telegram id, username, name or phone…" value="${esc(sb.uq)}" oninput="sbUQ(this.value)" autocomplete="off" spellcheck="false"></label></div>
    ${sbListCard(U_COLS, 1040, [["Telegram id"], ["Name"], ["Wallet", "r"], ["Orders", "r"], ["Partner"], ["Discount"], ["Joined"], ["Action", "r"]])}`);
  await sbListInit({cols: U_COLS, base: 46, empty: () => sb.uq ? `No user matches “${esc(sb.uq)}”.` : "No user here.",
    fetch: async (page, ps) => { const r = await api(`/api/shopbot/userlist?q=${encodeURIComponent(sb.uq)}&kind=${sb.uKind}&page=${page}&limit=${ps}`); return {items: r.rows, total: r.total, counts: r.counts}; },
    after: r => { sb.uCounts = r.counts; Object.entries(r.counts || {}).forEach(([k, v]) => { const e = $("#uc-" + k); if (e) e.textContent = fmt(v); }); },
    cls: u => u.step === "banned" ? "dim" : "", key: u => u.userid,
    row: u => { const nm = u.name && u.name !== "NULL" ? u.name : u.userid, un = u.username && u.username !== "NULL" && u.username !== "ندارد" ? "@" + esc(u.username) : "", ban = u.step === "banned", id = esc(u.userid);
      return `<span class="mono">${id}</span><span class="sb-pu"><b dir="auto" onclick="sbUser('${id}')">${esc(nm)}</b><small dir="auto">${un}</small></span><span class="r sb-pa"><b>${fmt(u.wallet)}</b> <em>T</em></span><span class="r">${fmt(u.orders)}</span><span>${u.is_agent === "1" ? '<span class="sb-pc2 ok">partner</span>' : ""}</span><span>${disc(u.discount_percent)}%</span><span class="sb-cut">${sbDate(u.date)}</span><span class="sb-pact"><button type="button" class="sb-btn sm0" onclick="sbUser('${id}')">More</button><button type="button" class="sb-btn sm0" onclick="sbUWallet('${id}')">± wallet</button><button type="button" class="sb-btn sm0 ${ban ? "" : "dec"}" onclick="sbU('${id}', '${ban ? "unban" : "ban"}')">${ban ? "Unban" : "Ban"}</button></span>`; }});
}
window.sbUKind = k => { sb.uKind = k; document.querySelectorAll("#u-tabs .sb-ptab").forEach(b => b.classList.toggle("on", b.dataset.k === k)); sbListLoad(0); };
window.sbUQ = v => { sb.uq = v.trim(); clearTimeout(qT); qT = setTimeout(() => sbListLoad(0), 300); };
window.sbUWallet = async uid => sbU(uid, "wallet", await gprompt("Add to the wallet (Toman, negative to take)", "10000"));

/* ---- partners ---- */
Object.assign(sb, {paQ: "", paSort: "recent"});
const PA_COLS = "minmax(170px, 1.4fr) 110px 100px 130px 70px 130px 210px";
async function sbPartners() {
  const sorts = [["recent", "Newest"], ["sales", "Top buyers"], ["wallet", "Biggest wallet"]];
  sbBody(`<div class="sb-card sb-ptool"><button type="button" class="sb-btn pri sm0" onclick="sbPartnerNew()">${sbPlusIc}Add partner</button>
      <span class="sb-mini"><small>Partners</small><b id="pa-n">…</b></span><span class="sb-mini"><small>Wallets</small><b id="pa-w">…</b><em>T</em></span><span class="sb-mini"><small>Partner orders</small><b id="pa-o">…</b></span>
      <div class="sb-ptabs" id="pa-sorts">${sorts.map(([k, l]) => `<button type="button" class="sb-ptab ${sb.paSort === k ? "on" : ""}" data-k="${k}" onclick="sbPaSort('${k}')">${l}</button>`).join("")}</div>
      <label class="sb-psearch"><i>${SI.search}</i><input type="text" placeholder="search the partners…" value="${esc(sb.paQ)}" oninput="sbPaQ(this.value)" autocomplete="off" spellcheck="false"></label></div>
    ${sbListCard(PA_COLS, 0, [["Partner"], ["Since"], ["Discount"], ["Wallet", "r"], ["Orders", "r"], ["Spent", "r"], ["Action", "r"]])}`);
  await sbListInit({cols: PA_COLS, base: 46, key: x => x.userid, empty: () => sb.paQ ? `No partner matches “${esc(sb.paQ)}”.` : "No partner here.",
    fetch: async (page, ps) => { const r = await api(`/api/shopbot/partners?q=${encodeURIComponent(sb.paQ)}&page=${page}&sort=${sb.paSort}&limit=${ps}`); return {items: r.rows, total: r.total, wallet: r.wallet, orders: r.orders}; },
    after: r => { const set = (id, v) => { const e = $(id); if (e) e.textContent = v; }; set("#pa-n", fmt(r.total)); set("#pa-w", fmt(r.wallet)); set("#pa-o", fmt(r.orders)); },
    row: (x, ask) => { const id = esc(x.userid), first = `<span class="sb-pu"><b dir="auto" onclick="sbUser('${id}')">${esc(x.name || x.userid)}</b><small dir="auto">${x.username && x.username !== "NULL" ? "@" + esc(x.username) : id}</small></span>`;
      if (ask) return sbAskRow(first, "Remove from the partners? The user keeps the account and the wallet.", 7, `<button type="button" class="sb-btn sm0" onclick="sbAsk(0)">Keep</button><button type="button" class="sb-btn red sm0" onclick="sbPartnerOff('${id}')">Remove</button>`);
      return `${first}<span class="sb-cut">${x.agent_date && x.agent_date !== "0" ? sbDate(x.agent_date) : "—"}</span><span><span class="sb-disc"><input type="number" min="0" max="100" value="${x.discount}" onchange="sbPartnerDiscount('${id}', this)" title="The percent this partner takes off every plan"><em>%</em></span></span><span class="r sb-pa"><b>${fmt(x.wallet)}</b> <em>T</em></span><span class="r">${fmt(x.orders)}</span><span class="r sb-pa">${+x.spent ? `<b>${fmt(x.spent)}</b> <em>T</em>` : "—"}</span><span class="sb-pact"><button type="button" class="sb-btn sm0" onclick="sbUser('${id}')">Open</button><button type="button" class="sb-btn dec sm0" onclick="sbAsk('${id}')">Remove partner</button></span>`; }});
}
window.sbPartnerNew = () => showModal("<b>Add a partner</b>", `<div class="qp"><p class="qp-sub">Search the bot's users, then make one a partner: they buy at their own discount.</p><div class="qp-f"><label for="pa-add">User</label><input id="pa-add" class="qp-in" type="text" placeholder="user id, name or @username" oninput="sbPartnerFind(this.value)" autocomplete="off" spellcheck="false" autofocus></div><div id="pa-res" class="pa-resbox"></div></div>`, "plan-add cat-add");window.sbPaSort = k => { sb.paSort = k; document.querySelectorAll("#pa-sorts .sb-ptab").forEach(b => b.classList.toggle("on", b.dataset.k === k)); sbListLoad(0); };
window.sbPaQ = v => { sb.paQ = v.trim(); clearTimeout(qT); qT = setTimeout(() => sbListLoad(0), 300); };
window.sbPartnerDiscount = async (uid, inp) => { const v = Math.max(0, Math.min(100, Math.round(+inp.value || 0))); inp.value = v; try { await api("/api/shopbot/users/action", {method: "POST", body: JSON.stringify({action: "discount", userid: uid, value: v})}); toast(`✓ Discount ${v}% saved`, "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.sbPartnerOff = async uid => { try { await api("/api/shopbot/users/action", {method: "POST", body: JSON.stringify({action: "agent_off", userid: uid})}); toast("✓ Removed from the partners", "ok"); sbListLoad(); } catch (e) { toast("✕ " + e.message, "bad"); sbAsk(0); } };
window.sbPartnerOn = async uid => { try { await api("/api/shopbot/users/action", {method: "POST", body: JSON.stringify({action: "agent_on", userid: uid})}); toast("✓ Now a partner", "ok"); closeModal(); sbListLoad(); } catch (e) { toast("✕ " + e.message, "bad"); } };
let paT; window.sbPartnerFind = v => { clearTimeout(paT); const box = $("#pa-res"); if (!box) return; if (v.trim().length < 2) { box.innerHTML = ""; return; } paT = setTimeout(async () => { try { const r = await api("/api/shopbot/users?q=" + encodeURIComponent(v.trim())); box.innerHTML = r.users.slice(0, 5).map(u => `<div class="sb-pr"><span><b>${esc(u.name || u.userid)}</b> <small class="mut">${u.username && u.username !== "NULL" ? "@" + esc(u.username) : ""} ${esc(u.userid)}</small></span>${u.is_agent === "1" ? '<span class="pill ok">partner</span>' : `<button class="sm pri" onclick="sbPartnerOn('${esc(u.userid)}')">Make partner</button>`}</div>`).join("") || '<small class="mut">nobody found</small>'; } catch (e) { box.innerHTML = `<small class="err">${esc(e.message)}</small>`; } }, 300); };

/* ---- categories (Categories.dc.html): the groups the user picks from before the plans ---- */
Object.assign(sb, {cf: null, catFlash: 0});
const CAT_COLS = "56px minmax(180px, 1.4fr) 180px 80px 92px 1fr 200px";
async function sbCategories() {
  const r = await api("/api/shopbot/categories"); sb.cats = r.categories;
  sbBody(`<div class="sb-card sb-ptool"><button type="button" class="sb-btn pri sm0" onclick="sbCatEdit(null)">${sbPlusIc}Add category</button><span class="sb-hint">The groups the user picks from before the plans.</span></div>
    ${sbListCard(CAT_COLS, 860, [["#"], ["Title"], ["Inside"], ["Plans", "r"], ["On"], [""], ["Action", "r"]])}`);
  await sbListInit({cols: CAT_COLS, base: 42, fetch: sbClient(() => sb.cats || []), empty: () => "No categories yet. Add the first one with the button above.",
    cls: c => (c.active === "1" ? "" : "dim") + (+sb.catFlash === +c.id ? " flash" : ""),
    row: (c, ask) => { const list = sb.cats || [], par = c.parent && c.parent !== "0" ? list.find(x => String(x.id) === String(c.parent)) : null, kids = list.filter(k => String(k.parent) === String(c.id)).length, n = +c.plans || 0, on = c.active === "1";
      const first = `<span class="mono">${esc(c.id)}</span>`, title = `<span class="sb-ct sb-cut" dir="auto" title="${esc(c.title)}">${esc(c.title)}</span>`;
      if (ask) return sbAskRow(first + title, n ? `Cannot delete: ${n} plans inside. Move or delete them first.` : kids ? `Cannot delete: ${kids} sub-categories inside.` : "Delete this category?", 7,
        n || kids ? `<button type="button" class="sb-btn sm0" onclick="sbAsk(0)">OK</button>` : `<button type="button" class="sb-btn sm0" onclick="sbAsk(0)">Keep</button><button type="button" class="sb-btn red sm0" onclick="sbCatDel(${c.id})">Delete</button>`);
      return `${first}${title}<span class="sb-cut" dir="auto">${par ? esc(par.title) : '<span class="mut">top level</span>'}</span><span class="r">${fmt(n)}</span><span>${sbSw(on, `sbCatToggle(${c.id}, ${on ? 0 : 1})`, `${on ? "Hide" : "Show"} ${c.title} in the bot`)}</span><span></span><span class="sb-pact"><button type="button" class="sb-btn sm0" onclick="sbCatEdit(${c.id})">Edit</button><button type="button" class="sb-btn dec sm0" aria-label="Delete category ${esc(c.title)}" onclick="sbAsk(${c.id})">Delete</button></span>`; }});
}
window.sbCatEdit = id => {
  const c = id ? (sb.cats || []).find(x => +x.id === +id) : null;
  sb.cf = {id: id || 0, title: c ? c.title : "", parent: c && c.parent && c.parent !== "0" ? String(c.parent) : "", err: ""};
  const f = sb.cf, list = sb.cats || [], kids = id ? list.some(k => String(k.parent) === String(id)) : false, opts = kids ? [] : list.filter(x => (!x.parent || x.parent === "0") && +x.id !== +id);
  showModal(`<b>${id ? "Edit category" : "New category"}</b>`, `<form class="qp" onsubmit="return sbCatSave(event)" novalidate>
      <div class="qp-grid one"><div class="qp-f"><label for="cf-title">Title</label><input id="cf-title" class="qp-in" type="text" dir="auto" autocomplete="off" placeholder="For example: یک ماهه" value="${esc(f.title)}" oninput="sb.cf.title=this.value;sbCatErr('')" autofocus></div>
        <div class="qp-f"><label for="cf-par">Inside</label><span class="qp-sw"><select id="cf-par" class="qp-sel" onchange="sb.cf.parent=this.value;sbCatErr('')"><option value="">Top level</option>${opts.map(x => `<option value="${esc(x.id)}" ${String(x.id) === f.parent ? "selected" : ""}>${esc(x.title)}</option>`).join("")}</select>${qpChev}</span></div></div>
      <div class="qp-prev"><span role="alert" id="qp-err"></span></div>
      <div class="qp-act"><button type="button" class="qp-ghost" onclick="closeModal()">Cancel</button><button type="submit" class="qp-pri">${id ? "Save changes" : "Add"}</button></div></form>`, "plan-add cat-add");
};
window.sbCatErr = m => { sb.cf.err = m; const e = $("#qp-err"); if (e) e.textContent = m; };
window.sbCatSave = async e => {
  e.preventDefault(); const f = sb.cf, t = f.title.trim(), list = sb.cats || [], fail = m => { sbCatErr(m); return false; };
  if (!t) return fail("Type a title for the category.");
  if (list.some(c => c.title.trim() === t && +c.id !== +f.id && String(+c.parent || "") === String(+f.parent || ""))) return fail("A category with this title already exists there.");
  const cur = f.id ? list.find(c => +c.id === +f.id) : null, body = {title: t, parent: +f.parent || 0, active: cur ? cur.active === "1" : true};
  try {
    const r = await api(f.id ? `/api/shopbot/categories/${f.id}` : "/api/shopbot/categories", {method: f.id ? "PUT" : "POST", body: JSON.stringify(body)});
    closeModal(); toast(f.id ? `✓ Saved “${t}”` : `✓ Category “${t}” added. It shows in the bot once it has plans.`, "ok");
    sb.catFlash = f.id ? 0 : r.id; await sbCategories();
    if (sb.catFlash) setTimeout(() => { sb.catFlash = 0; sbListDraw(); }, 2600);
  } catch (err) { fail(err.message); } return false;
};
window.sbCatToggle = async (id, on) => {
  const c = (sb.cats || []).find(x => +x.id === +id); if (!c) return;
  try { await api(`/api/shopbot/categories/${id}`, {method: "PUT", body: JSON.stringify({title: c.title, parent: +c.parent || 0, active: !!on})}); c.active = on ? "1" : "0"; sbListDraw(); toast(on ? `✓ “${c.title}” is visible in the bot` : `✓ “${c.title}” is hidden from the bot`, "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.sbCatDel = async id => { try { await api(`/api/shopbot/categories/${id}`, {method: "DELETE"}); sb.cats = (sb.cats || []).filter(x => +x.id !== +id); toast("✓ Category deleted", "ok"); sbListLoad(); } catch (e) { toast("✕ " + e.message, "bad"); sbAsk(0); } };

/* ---- servers (the panels the bot sells from) ---- */
const SV_COLS = "50px minmax(170px, 1.4fr) 90px minmax(120px, 1.2fr) 84px 84px 92px 160px";
const svOn = s => s.active === "1" && s.state === "1";
async function sbServers() {
  const r = await api("/api/shopbot/servers"); sb.servers = r.servers; sb.knownPanels = r.panels;
  sbBody(`<div class="sb-card sb-ptool"><button type="button" class="sb-btn pri sm0" onclick="sbSrvEdit(null)">${sbPlusIc}Add server</button>${r.panels.length ? `<button type="button" class="sb-btn sm0" onclick="sbSrvFromPanel()" title="Make a bot server from a panel this control panel already manages">From a managed panel</button>` : ""}<span class="sb-hint">a server = a Marzban / PasarGuard panel the bot creates accounts on</span></div>
    ${sbListCard(SV_COLS, 1050, [["#"], ["Title"], ["Type"], ["Panel"], ["Capacity", "r"], ["Active subs", "r"], ["On"], ["Action", "r"]])}`);
  await sbListInit({cols: SV_COLS, base: 44, fetch: sbClient(() => sb.servers || []), empty: () => "No server yet.", cls: s => svOn(s) ? "" : "dim",
    row: (s, ask) => { const on = svOn(s), first = `<span class="mono">${esc(s.id)}</span><span class="sb-pu"><b dir="auto" title="${esc(s.title)}">${esc(s.flag && s.flag !== "NULL" ? s.flag + " " : "")}${esc(s.title)}</b>${s.remark && s.remark !== "NULL" ? `<small dir="auto">${esc(s.remark)}</small>` : ""}</span>`;
      if (ask) return sbAskRow(first, "Remove this server and its plans? Refused while subscriptions live on it.", 8, `<button type="button" class="sb-btn sm0" onclick="sbAsk(0)">Keep</button><button type="button" class="sb-btn red sm0" onclick="sbSrvDel(${s.id})">Delete</button>`);
      return `${first}<span>${esc(s.type || "?")}</span><span class="mono sb-cut" title="${esc(s.panel_url || "")}">${esc((s.panel_url || "").replace(/^https?:\/\//, "").slice(0, 40))}</span><span class="r">${fmt(s.ucount)}</span><span class="r">${fmt(s.active_orders)}</span><span>${sbSw(on, `sbSrvToggle(${s.id}, ${on ? 0 : 1})`, `${on ? "Turn off" : "Turn on"} ${s.title}`, on ? "On: click to turn it off" : "Off: click to turn it on")}</span><span class="sb-pact"><button type="button" class="sb-btn sm0" onclick="sbSrvEdit(${s.id})">Edit</button><button type="button" class="sb-btn dec sm0" onclick="sbAsk(${s.id})">Delete</button></span>`; }});
}

/* ---- discounts ---- */
/* ---- gift codes ---- */
const SB_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const sbDateTime = v => { const n = +v; if (!(n > 1000000000)) return "—"; const d = new Date(n * 1000), p = x => String(x).padStart(2, "0"); return `${d.getDate()} ${SB_MON[d.getMonth()]} ${d.getFullYear()}, ${p(d.getHours())}:${p(d.getMinutes())}`; };
const toLocalInput = t => { if (!(+t > 0)) return ""; const d = new Date(+t * 1000), p = n => String(n).padStart(2, "0"); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`; };
const sbDigits = (el, cb) => { const v = el.value.replace(/[۰-۹]/g, c => "۰۱۲۳۴۵۶۷۸۹".indexOf(c)).replace(/[٠-٩]/g, c => "٠١٢٣٤٥٦٧٨٩".indexOf(c)).replace(/\D/g, ""); if (v !== el.value) el.value = v; return v; };

/* ---- discounts (Discounts.dc.html) ---- */
const DC_COLS = "120px 100px minmax(120px, 1fr) 215px 84px 64px 92px 160px";
Object.assign(sb, {dcFlash: 0, df: null});
const dcUntil = d => {
  const t = +d.expire_date; if (!t) return '<span class="sb-cut mut">no end</span>';
  const now = Date.now() / 1000, gone = t < now, days = Math.ceil((t - now) / 86400);
  return `<span class="sb-until"><span class="${gone ? "mut" : ""}">${sbDateTime(t)}</span>${gone ? '<em class="sb-tag bad">expired</em>' : days <= 7 ? `<em class="sb-tag warn">${days <= 0 ? "today" : days === 1 ? "1 day left" : days + " days left"}</em>` : ""}</span>`;
};
async function sbDiscounts() {
  const r = await api("/api/shopbot/discounts"); sb.discounts = r.discounts;
  sbBody(`<div class="sb-card sb-ptool"><button type="button" class="sb-btn pri sm0" onclick="sbDiscEdit(null)">${sbPlusIc}Add code</button><span class="sb-hint" id="dc-n"></span></div>
    ${sbListCard(DC_COLS, 1050, [["Code"], ["Off"], ["Who"], ["Until"], ["Uses", "r"], ["Per user", "r"], ["On"], ["Action", "r"]])}`);
  await sbListInit({cols: DC_COLS, base: 56, fetch: sbClient(() => sb.discounts || []), empty: () => "No discount codes yet. Add the first one with the button above.",
    after: r => { const e = $("#dc-n"); if (e) e.textContent = `${r.total} ${r.total === 1 ? "code" : "codes"}`; },
    cls: d => (d.is_active === "1" ? "" : "dim") + (+sb.dcFlash === +d.id ? " flash" : ""),
    row: (d, ask) => { const on = d.is_active === "1", first = `<span class="mono sb-cut"><b>${esc(d.hash_id)}</b></span>`;
      if (ask) return sbAskRow(first, "Delete this code?", 8, `<button type="button" class="sb-btn sm0" onclick="sbAsk(0)">Keep</button><button type="button" class="sb-btn red sm0" onclick="sbDiscDel(${d.id})">Delete</button>`);
      const extra = [+d.max_discount ? `max ${money(d.max_discount)}` : "", +d.min_price ? `from ${money(d.min_price)}` : ""].filter(Boolean).join(" · "), tgt = d.target_type !== "all" ? `${esc(d.target_type)} #${esc(d.target_id)}` : "";
      return `${first}<span class="sb-pu"><b>${d.type === "percent" ? esc(d.amount) + " %" : money(d.amount)}</b><small>${esc(extra)}</small></span><span class="sb-pu"><b dir="auto">${esc(AUD[d.audience_type] || d.audience_type)}${d.audience_type === "user" ? " " + esc(d.audience_user_id) : ""}</b><small>${tgt}</small></span>${dcUntil(d)}<span class="r">${esc(d.uses)}${+d.expire_count ? " / " + esc(d.expire_count) : " / ∞"}</span><span class="r">${esc(d.can_use)}</span><span>${sbSw(on, `sbDiscToggle(${d.id}, ${on ? 0 : 1})`, `${on ? "Turn off" : "Turn on"} ${d.hash_id}`)}</span><span class="sb-pact"><button type="button" class="sb-btn sm0" onclick="sbDiscEdit(${d.id})">Edit</button><button type="button" class="sb-btn dec sm0" aria-label="Delete code ${esc(d.hash_id)}" onclick="sbAsk(${d.id})">Delete</button></span>`; }});
}
window.sbDiscToggle = async (id, on) => { const d = (sb.discounts || []).find(x => +x.id === +id); try { await api(`/api/shopbot/discounts/${id}/active`, {method: "PUT", body: JSON.stringify({active: !!on})}); if (d) d.is_active = on ? "1" : "0"; sbListDraw(); toast(on ? `✓ Code ${d ? d.hash_id : ""} is on` : `✓ Code ${d ? d.hash_id : ""} is off. Nobody can use it.`, "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.sbDiscDel = async id => { const d = (sb.discounts || []).find(x => +x.id === +id); try { await api(`/api/shopbot/discounts/${id}`, {method: "DELETE"}); sb.discounts = (sb.discounts || []).filter(x => +x.id !== +id); toast(`✓ Deleted code ${d ? d.hash_id : ""}`, "ok"); sbListLoad(); } catch (e) { toast("✕ " + e.message, "bad"); sbAsk(0); } };
window.sbDiscEdit = id => {
  const d = id ? (sb.discounts || []).find(x => +x.id === +id) : null;
  sb.df = {id: id || 0, hash_id: d ? d.hash_id : "", type: d ? d.type : "percent", amount: d ? String(d.amount) : "", audience_type: d ? d.audience_type : "all", audience_user_id: d && +d.audience_user_id ? String(d.audience_user_id) : "", until: d ? toLocalInput(d.expire_date) : "",
    max: d && +d.expire_count ? String(d.expire_count) : "", per: d ? String(d.can_use || 1) : "1", target_type: d ? d.target_type : "all", target_id: d && +d.target_id ? String(d.target_id) : "", min_price: d && +d.min_price ? String(d.min_price) : "", max_discount: d && +d.max_discount ? String(d.max_discount) : "", is_active: d ? d.is_active === "1" : true, err: ""};
  dcDraw();
};
function dcDraw() {
  const f = sb.df, pct = f.type === "percent", user = f.audience_type === "user", tgt = f.target_type !== "all";
  const txt = (k, label, ph, cls = "", dis = false) => `<div class="qp-f"><label for="df-${k}">${label}</label><input id="df-${k}" class="qp-in ${cls}" type="text" autocomplete="off" spellcheck="false" placeholder="${ph}" value="${esc(f[k])}" ${dis ? "disabled" : ""} oninput="sb.df.${k}=this.value;sbDfErr('')"></div>`;
  const num = (k, label, ph, dis = false) => `<div class="qp-f"><label for="df-${k}">${label}</label><input id="df-${k}" class="qp-in" type="text" inputmode="numeric" autocomplete="off" placeholder="${ph}" value="${esc(f[k])}" ${dis ? "disabled" : ""} oninput="sb.df.${k}=sbDigits(this);sbDfErr('')"></div>`;
  const slt = (k, label, opts) => `<div class="qp-f"><label for="df-${k}">${label}</label><span class="qp-sw"><select id="df-${k}" class="qp-sel" onchange="sbDfSel('${k}', this.value)">${opts.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(f[k]) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>${qpChev}</span></div>`;
  showModal(`<b>${f.id ? "Edit discount code" : "New discount code"}</b>`, `<form class="qp" onsubmit="return sbDcSave(event)" novalidate>
      <div class="qp-sec">Code and value</div><div class="qp-grid three">${txt("hash_id", "Code", "summer10", "mono")}${slt("type", "Kind", [["percent", "Percent off"], ["amount", "Fixed amount off (Toman)"]])}${num("amount", pct ? "Off (%)" : "Off (Toman)", pct ? "10" : "50000")}</div>
      <div class="qp-sec">Who and until when</div><div class="qp-grid three">${slt("audience_type", "Who may use it", Object.entries(AUD))}${num("audience_user_id", "User id (for “one user”)", "123456789", !user)}
        <div class="qp-f"><label for="df-until">Until (empty = no end)</label><input id="df-until" class="qp-in" type="datetime-local" value="${esc(f.until)}" oninput="sb.df.until=this.value;sbDfErr('')"></div></div>
      <div class="qp-sec">Limits</div><div class="qp-grid three">${num("max", "Max uses (empty = unlimited)", "unlimited")}${num("per", "Per user", "1")}${slt("target_type", "Applies to", [["all", "Every plan"], ["plan", "One plan"], ["category", "One category"], ["server", "One server"]])}
        ${num("target_id", "Plan / category / server id", "0", !tgt)}${num("min_price", "Minimum price (Toman)", "0")}${num("max_discount", "Maximum discount (Toman, 0 = none)", "0", !pct)}</div>
      <div class="qp-prev"><button type="button" class="qp-chk" role="checkbox" aria-checked="${f.is_active}" onclick="sb.df.is_active=!sb.df.is_active;dcDraw()"><span class="${f.is_active ? "on" : ""}">${f.is_active ? '<svg viewBox="0 0 16 16"><path d="M3.5 8.5l3 3L12.5 5"/></svg>' : ""}</span>Active (people can use it)</button><span role="alert" id="qp-err"></span></div>
      <div class="qp-act"><button type="button" class="qp-ghost" onclick="closeModal()">Cancel</button><button type="submit" class="qp-pri">${f.id ? "Save changes" : "Add code"}</button></div></form>`, "plan-add dc-edit");
  sbDfErr(f.err);
}
window.sbDfSel = (k, v) => { sb.df[k] = v; sb.df.err = ""; dcDraw(); };
window.sbDfErr = m => {
  if (sb.df) sb.df.err = m; const e = $("#qp-err"); if (e) e.textContent = m;
  const map = {Code: "hash_id", Off: "amount", User: "audience_user_id", Until: "until", Max: "max", Per: "per", Minimum: "min_price"}, key = map[(m || "").split(/[ :]/)[0]];
  document.querySelectorAll("#modal .qp-in").forEach(i => { const bad = !!m && i.id === "df-" + key; i.classList.toggle("bad", bad); i.setAttribute("aria-invalid", bad); });
};
window.sbDcSave = async e => {
  e.preventDefault(); const f = sb.df, fail = m => { sbDfErr(m); return false; }, code = f.hash_id.trim(), off = parseInt(f.amount, 10), per = parseInt(f.per, 10), max = f.max === "" ? 0 : parseInt(f.max, 10);
  const until = f.until ? Date.parse(f.until) : 0, was = f.id ? (sb.discounts || []).find(c => +c.id === +f.id) || {} : {};
  if (!/^[A-Za-z0-9_-]{3,40}$/.test(code)) return fail("Code: 3 to 40 letters, digits, - or _.");
  if ((sb.discounts || []).some(c => String(c.hash_id).toLowerCase() === code.toLowerCase() && +c.id !== +f.id)) return fail("Code already exists.");
  if (f.type === "percent" ? !(off >= 1 && off <= 100) : !(off >= 1)) return fail(f.type === "percent" ? "Off must be 1 to 100." : "Off must be 1 Toman or more.");
  if (f.audience_type === "user" && !/^\d{3,20}$/.test(f.audience_user_id)) return fail("User id: digits only.");
  if (until && until <= Date.now() && !(f.id && toLocalInput(was.expire_date) === f.until)) return fail("Until must be in the future.");
  if (f.max !== "" && !(max >= 1)) return fail("Max uses must be 1 or more, or empty.");
  if (!(per >= 1)) return fail("Per user must be 1 or more.");
  const fields = {hash_id: code, type: f.type, amount: off, expire_at: until ? Math.floor(until / 1000) : 0, expire_count: max, can_use: per, audience_type: f.audience_type, audience_user_id: +f.audience_user_id || 0, target_type: f.target_type, target_id: f.target_type === "all" ? 0 : +f.target_id || 0, min_price: +f.min_price || 0, max_discount: f.type === "percent" ? +f.max_discount || 0 : 0, is_active: f.is_active ? 1 : 0};
  try {
    const r = await api(f.id ? `/api/shopbot/discounts/${f.id}` : "/api/shopbot/discounts", {method: f.id ? "PUT" : "POST", body: JSON.stringify({fields})});
    closeModal(); toast(f.id ? `✓ Saved code ${code}` : `✓ Code ${code} added: ${f.type === "percent" ? off + "%" : money(off)} off`, "ok");
    sb.dcFlash = f.id ? 0 : r.id; await sbDiscounts(); if (sb.dcFlash) setTimeout(() => { sb.dcFlash = 0; sbListDraw(); }, 2600);
  } catch (err) { fail(err.message); } return false;
};

/* ---- gift codes (GiftCodes.dc.html) ---- */
const GF_COLS = "minmax(170px, 1fr) 100px 84px 215px 135px minmax(90px, 1fr) 140px";
Object.assign(sb, {gfFlash: 0, gf: null});
const gfState = g => +g.used >= +g.max_uses ? "used up" : +g.expires && +g.expires < Date.now() / 1000 ? "expired" : "active";
async function sbGifts() {
  const r = await api("/api/shopbot/gifts"); sb.gifts = r.gifts;
  sbBody(`<div class="sb-card sb-ptool"><button type="button" class="sb-btn pri sm0" onclick="sbGiftNew()">${sbPlusIc}Add gift code</button><span class="sb-hint">A gift code puts money in the wallet of whoever redeems it.</span><span class="sb-hint" id="gf-n"></span></div>
    ${sbListCard(GF_COLS, 1030, [["Code"], ["Amount", "r"], ["Used"], ["Expires"], ["Made"], ["Note"], ["Action", "r"]])}`);
  await sbListInit({cols: GF_COLS, base: 56, fetch: sbClient(() => sb.gifts || []), empty: () => "No gift codes yet. Add the first one with the button above.",
    after: r => { const e = $("#gf-n"); if (e) e.textContent = `${r.total} ${r.total === 1 ? "gift code" : "gift codes"}`; },
    cls: g => (gfState(g) === "active" ? "" : "dim") + (+sb.gfFlash === +g.id ? " flash" : ""),
    row: (g, ask) => { const st = gfState(g), code = esc(g.code), left = (+g.max_uses - +g.used) * +g.amount, pc = Math.min(100, Math.round(+g.used / Math.max(1, +g.max_uses) * 100));
      const first = `<span class="sb-gc"><span class="mono"><b>${code}</b></span><button type="button" class="sb-cp" aria-label="Copy gift code ${code}" onclick="sbCopy(this, '${code}')">Copy</button></span>`;
      if (ask) return sbAskRow(first, st === "active" ? `Delete ${code}? ${fmt(left)} T of unredeemed value ceases to exist.` : `Delete ${code}? It can no longer be redeemed, so nothing is lost.`, 7, `<button type="button" class="sb-btn sm0" onclick="sbAsk(0)">Keep</button><button type="button" class="sb-btn red sm0" onclick="sbGiftDel(${g.id})">Delete</button>`);
      return `${first}<span class="r sb-pa"><b>${fmt(g.amount)}</b> <em>T</em></span><span class="sb-pu"><b>${esc(g.used)} / ${esc(g.max_uses)}</b><i class="sb-bar"><i style="width:${pc}%"></i></i></span><span class="sb-until"><span class="${st === "expired" ? "mut" : ""}">${+g.expires ? sbDateTime(g.expires) : "never"}</span><em class="sb-tag ${st === "active" ? "ok" : st === "expired" ? "bad" : "mutd"}">${st}</em></span><span class="sb-cut">${sbDateTime(g.created)}</span><span class="sb-cut" dir="auto" title="${esc(g.note && g.note !== "NULL" ? g.note : "")}">${esc(g.note && g.note !== "NULL" ? g.note : "—")}</span><span class="sb-pact"><button type="button" class="sb-btn dec sm0" aria-label="Delete gift code ${code}" onclick="sbAsk(${g.id})">Delete</button></span>`; }});
}
window.sbGiftNew = () => { sb.gf = {amount: "", uses: "1", expires: "", note: "", err: ""}; gfDraw(); };
function gfDraw() {
  const f = sb.gf;
  showModal(`<b>New gift code</b>`, `<form class="qp" onsubmit="return sbGiftSave(event)" novalidate>
      <p class="qp-sub">The code itself is generated for you. Copy it from the list and send it.</p>
      <div class="qp-grid"><div class="qp-f"><label for="gf-amount">Amount (Toman)</label><input id="gf-amount" class="qp-in" type="text" inputmode="numeric" autocomplete="off" placeholder="50000" value="${esc(f.amount)}" oninput="sb.gf.amount=sbDigits(this);sbGfErr('')" autofocus></div>
        <div class="qp-f"><label for="gf-uses">Can be used by (people)</label><input id="gf-uses" class="qp-in" type="text" inputmode="numeric" autocomplete="off" placeholder="1" value="${esc(f.uses)}" oninput="sb.gf.uses=sbDigits(this);sbGfErr('')"></div>
        <div class="qp-f"><label for="gf-expires">Expires (empty = never)</label><input id="gf-expires" class="qp-in" type="datetime-local" value="${esc(f.expires)}" oninput="sb.gf.expires=this.value;sbGfErr('')"></div>
        <div class="qp-f"><label for="gf-note">Note (only you see it)</label><input id="gf-note" class="qp-in" type="text" dir="auto" autocomplete="off" placeholder="What is it for?" value="${esc(f.note)}" oninput="sb.gf.note=this.value"></div></div>
      <div class="qp-prev"><span role="alert" id="qp-err"></span></div>
      <div class="qp-act"><button type="button" class="qp-ghost" onclick="closeModal()">Cancel</button><button type="submit" class="qp-pri">Create code</button></div></form>`, "plan-add cat-add");
}
window.sbGfErr = m => { sb.gf.err = m; const e = $("#qp-err"); if (e) e.textContent = m; const key = {Amount: "amount", Uses: "uses", Expires: "expires"}[(m || "").split(" ")[0]]; document.querySelectorAll("#modal .qp-in").forEach(i => { const bad = !!m && i.id === "gf-" + key; i.classList.toggle("bad", bad); i.setAttribute("aria-invalid", bad); }); };
window.sbGiftSave = async e => {
  e.preventDefault(); const f = sb.gf, fail = m => { sbGfErr(m); return false; }, amount = parseInt(f.amount, 10), uses = parseInt(f.uses, 10), exp = f.expires ? Date.parse(f.expires) : 0;
  if (!(amount > 0)) return fail("Amount must be more than 0.");
  if (!(uses >= 1)) return fail("Uses must be 1 or more.");
  if (exp && exp <= Date.now()) return fail("Expires must be in the future.");
  const mk = () => "GIFT" + [...crypto.getRandomValues(new Uint8Array(4))].map(b => b.toString(16).padStart(2, "0")).join("").toUpperCase();
  let code = mk(); while ((sb.gifts || []).some(g => g.code === code)) code = mk();
  try {
    const r = await api("/api/shopbot/gifts", {method: "POST", body: JSON.stringify({code, amount, max_uses: uses, expires: exp ? Math.floor(exp / 1000) : 0, note: f.note.trim()})});
    closeModal(); toast(`✓ Gift code ${code} created: ${fmt(amount)} T for ${uses} ${uses === 1 ? "person" : "people"}. Copy it and send it.`, "ok");
    sb.gfFlash = r.id; await sbGifts(); setTimeout(() => { sb.gfFlash = 0; sbListDraw(); }, 2600);
  } catch (err) { fail(err.message); } return false;
};
window.sbGiftDel = async id => { const g = (sb.gifts || []).find(x => +x.id === +id); try { await api(`/api/shopbot/gifts/${id}`, {method: "DELETE"}); sb.gifts = (sb.gifts || []).filter(x => +x.id !== +id); toast(`✓ Deleted gift code ${g ? g.code : ""}`, "ok"); sbListLoad(); } catch (e) { toast("✕ " + e.message, "bad"); sbAsk(0); } };

/* ---- broadcast: send and cancel ---- */
window.sbBcSend = async e => { e.preventDefault(); const f = formJson(e); if (!await gconfirm(`Send this message to ${f.group === "all" ? "EVERY user of the bot" : "the chosen users"}? It cannot be unsent.`, {danger: true})) return false; try { const r = await api("/api/shopbot/broadcasts", {method: "POST", body: JSON.stringify(f)}); toast(`✓ Broadcast #${r.id} queued`, "ok"); closeModal(); sb.infoAt = 0; sbBroadcast(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.sbBcCancel = async id => { try { await api(`/api/shopbot/broadcasts/${id}/cancel`, {method: "POST"}); toast("✓ Cancelled", "ok"); sbBroadcast(); } catch (e) { toast("✕ " + e.message, "bad"); sbAsk(0); } };
/* ---- servers: switch and delete (a row that asks inside itself) ---- */
window.sbSrvToggle = async (id, on) => {
  const s = (sb.servers || []).find(x => +x.id === +id); if (!s) return;
  try { await api(`/api/shopbot/servers/${id}`, {method: "PUT", body: JSON.stringify({info: on ? {active: 1, state: 1} : {active: 0}, config: {}})}); s.active = on ? "1" : "0"; if (on) s.state = "1"; sb.infoAt = 0; sbListDraw(); toast(on ? `✓ ${s.title} is on` : `✓ ${s.title} is off`, "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.sbSrvDel = async id => { try { await api(`/api/shopbot/servers/${id}`, {method: "DELETE"}); sb.servers = (sb.servers || []).filter(x => +x.id !== +id); sb.infoAt = 0; toast("✓ Removed", "ok"); sbListLoad(); } catch (e) { toast("✕ " + e.message, "bad"); sbAsk(0); } };
/* ---- broadcast ---- */
const BC_COLS = "50px minmax(140px, 1fr) 120px 90px 100px 110px minmax(160px, 1.4fr) 110px";
async function sbBroadcast() {
  const r = await api("/api/shopbot/broadcasts"); sb.bc = r;
  sbBody(`<div class="sb-card sb-ptool"><button type="button" class="sb-btn pri sm0" onclick="sbBcNew()">${sbPlusIc}New broadcast</button><span class="sb-hint">the bot's cron sends it, one user at a time, within minutes</span></div>
    ${sbListCard(BC_COLS, 980, [["#"], ["Title"], ["To"], ["State"], ["Sent", "r"], ["Made"], ["Text"], ["Action", "r"]])}`);
  await sbListInit({cols: BC_COLS, base: 44, fetch: sbClient(() => (sb.bc || {}).broadcasts || []), empty: () => "No broadcast yet.",
    row: (b, ask) => { const st = BC_STATE[b.state] || [b.state, ""], first = `<span class="mono">${esc(b.id)}</span><span class="sb-cut" dir="auto" title="${esc(b.title || "")}">${esc(b.title || "")}</span>`;
      if (ask) return sbAskRow(first, `Cancel broadcast #${esc(b.id)}? Messages already sent stay.`, 8, `<button type="button" class="sb-btn sm0" onclick="sbAsk(0)">Keep</button><button type="button" class="sb-btn red sm0" onclick="sbBcCancel(${b.id})">Cancel it</button>`);
      return `${first}<span class="sb-cut">${esc(r.groups[b.target_group] || b.target_group)}</span><span><span class="sb-pc2 ${st[1] === "ok" ? "ok" : st[1] === "err" ? "bad" : "wait"}">${esc(st[0])}</span></span><span class="r">${esc(b.sent || 0)}${+b.total_cnt ? " / " + esc(b.total_cnt) : ""}</span><span class="sb-cut">${sbDate(b.created_at)}</span><span class="sb-cut" dir="auto" title="${esc(b.text || "")}">${esc((b.text || "").replace(/\s+/g, " ").slice(0, 80))}</span><span class="sb-pact">${+b.state <= 2 ? `<button type="button" class="sb-btn dec sm0" onclick="sbAsk(${b.id})">Cancel</button>` : ""}</span>`; }});
}
/* ---- tickets ---- */
const TK_COLS = "50px minmax(160px, 1fr) minmax(180px, 1.4fr) 110px 60px 110px 90px";
async function sbTickets() {
  const tabs = [["open", "Open"], ["closed", "Closed"], ["all", "All"]];
  sbBody(`<div class="sb-card sb-ptool"><div class="sb-ptabs" id="tk-tabs">${tabs.map(([k, l]) => `<button type="button" class="sb-ptab ${sb.tkState === k ? "on" : ""}" data-k="${k}" onclick="sbTkKind('${k}')">${l}<span id="tkc-${k}">${sb.tkCounts ? fmt(sb.tkCounts[k]) : "…"}</span></button>`).join("")}</div></div>
    ${sbListCard(TK_COLS, 900, [["#"], ["User"], ["Title"], ["Category"], ["Msgs", "r"], ["Last"], ["Action", "r"]])}`);
  await sbListInit({cols: TK_COLS, base: 46, empty: () => sb.tkState === "open" ? "No open ticket. All answered." : "No ticket.",
    fetch: async (page, ps) => { const r = await api(`/api/shopbot/tickets?state=${sb.tkState}&page=${page}&limit=${ps}`); sb.tickets = r.rows; return {items: r.rows, total: r.total, counts: r.counts}; },
    after: r => { sb.tkCounts = r.counts; Object.entries(r.counts || {}).forEach(([k, v]) => { const e = $("#tkc-" + k); if (e) e.textContent = fmt(v); }); },
    cls: t => t.state === "1" ? "" : "dim",
    row: t => `<span class="mono">${esc(t.id)}</span><span class="sb-pu"><b dir="auto" onclick="sbTicket(${t.id})">${esc(t.name || t.user_id)}</b><small dir="auto">${t.username ? "@" + esc(t.username) : esc(t.user_id)}</small></span><span class="sb-cut" dir="auto" title="${esc(t.title || "")}">${esc(t.title || "")}</span><span class="sb-cut" dir="auto">${esc(t.category || "")}</span><span class="r">${esc(t.messages)}</span><span class="sb-cut">${ago(+t.last_at || +t.create_date)} ago</span><span class="sb-pact"><button type="button" class="sb-btn sm0" onclick="sbTicket(${t.id})">Open</button></span>`});
}
window.sbTkKind = k => { sb.tkState = k; document.querySelectorAll("#tk-tabs .sb-ptab").forEach(b => b.classList.toggle("on", b.dataset.k === k)); sbListLoad(0); };
/* ---- orders: a list like the payments one - a page holds what fits the window, a numbered pager, nothing jumps */
Object.assign(sb, {ordPS: 0, ordCounts: null});
const ORD_COLS = "64px minmax(150px, 1fr) minmax(130px, 1fr) 120px 100px 150px 70px 100px";
async function sbOrders() {
  sbBody(`<div class="sb-card sb-ptool"><div class="sb-ptabs"><button type="button" class="sb-ptab ${sb.ordActive ? "" : "on"}" onclick="sb.ordActive=false;sb.ordPage=0;sbOrdLoad()">All<span id="oc-all">…</span></button><button type="button" class="sb-ptab ${sb.ordActive ? "on" : ""}" onclick="sb.ordActive=true;sb.ordPage=0;sbOrdLoad()">Active<span id="oc-active">…</span></button></div>
      <label class="sb-psearch"><i>${SI.search}</i><input type="text" placeholder="User id, name, remark…" value="${esc(sb.ordQ)}" oninput="sb.ordQ=this.value.trim();sb.ordPage=0;clearTimeout(qT);qT=setTimeout(sbOrdLoad,350)" autocomplete="off" spellcheck="false"></label><span class="sb-pager"><small id="ord-pg"></small></span></div>
    <div class="sb-card sb-ptable" style="--minw:${sbMinW(ORD_COLS)}px"><div class="sb-lh" style="--cols:${ORD_COLS}"><span>#</span><span>User</span><span>Remark</span><span>Server</span><span class="r">Paid</span><span>Expires</span><span>State</span><span class="r">Action</span></div><div id="ord-rows" class="sb-lrows"></div><div class="sb-pfoot"><span id="ord-pager" class="pgbar"></span></div></div>`);
  sb.ordPS = sbFitBox("ord-rows");
  if (sb.ordCounts) { $("#oc-all").textContent = fmt(sb.ordCounts.all); $("#oc-active").textContent = fmt(sb.ordCounts.active); }
  await sbOrdLoad();
}
async function sbOrdLoad() {
  const r = await api(`/api/shopbot/orders?q=${encodeURIComponent(sb.ordQ)}&page=${sb.ordPage}&active=${sb.ordActive}&limit=${sb.ordPS || 0}`);
  sb.ordCounts = r.counts || sb.ordCounts; if (!$("#ord-rows")) return;
  if (sb.ordCounts) { $("#oc-all").textContent = fmt(sb.ordCounts.all); $("#oc-active").textContent = fmt(sb.ordCounts.active); }
  const per = r.per || 40, pg = $("#ord-pg"), pbar = $("#ord-pager");
  if (pg) pg.textContent = r.total === 0 ? "0 rows" : r.total <= per ? `${r.total} rows` : `${r.page * per + 1}–${r.page * per + r.rows.length} of ${fmt(r.total)}`;
  if (pbar) pbar.innerHTML = r.total > per ? `<small class="mut">Page ${r.page + 1} of ${Math.ceil(r.total / per)}</small>${pnPager(r.total, per, r.page * per, "sbOrdPage")}` : "";
  $("#ord-rows").innerHTML = r.rows.map(o => { const on = o.status === "1", un = o.username && o.username !== "NULL" && o.username !== "ندارد" ? "@" + esc(o.username) : esc(o.userid);
    return `<div class="sb-lr" style="--cols:${ORD_COLS}"><span class="mono">${esc(o.id)}</span><span class="sb-pu"><b dir="auto" onclick="sbUser('${esc(o.userid)}')">${esc(o.name && o.name !== "NULL" ? o.name : o.userid)}</b><small dir="auto">${un}</small></span><span class="mono sb-cut" title="${esc(o.remark || "")}">${esc(o.remark || "")}</span><span class="sb-cut" dir="auto">${esc(o.server || o.server_id || "")}</span><span class="r sb-pa"><b>${fmt(o.amount)}</b> <em>T</em></span><span class="sb-pd"><b>${tsFa(o.expire_date)}</b></span><span><span class="sb-pc2 ${on ? "ok" : "wait"}">${on ? "active" : "off"}</span></span><span class="sb-pact"><button type="button" class="sb-btn sm0 ${on ? "dec" : ""}" onclick="sbOrderStatus(${o.id}, ${on ? "false" : "true"})">${on ? "Disable" : "Enable"}</button></span></div>`; }).join("") || `<div class="sb-pempty"><i>${SI.check}</i>${sb.ordQ ? `No order matches “${esc(sb.ordQ)}”.` : "No order."}</div>`;
}
window.sbOrdPage = p => { sb.ordPage = Math.max(0, p); sbOrdLoad(); };window.sbOrderStatus = async (id, active) => { try { await api(`/api/shopbot/orders/${id}/status`, {method: "PUT", body: JSON.stringify({active})}); toast("✓ Changed", "ok"); sbOrdLoad(); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* ---- users */
window.sbU = async (uid, action, value) => { if ((action === "wallet" || action === "discount" || action === "wallet_set") && (value == null || value === "")) return; try { await api("/api/shopbot/users/action", {method: "POST", body: JSON.stringify({action, userid: uid, value})}); toast("✓ " + action.replace("_", " ") + " done", "ok"); if ($("#modal .sb-user")) sbUser(uid); if ($("#ls-rows")) sbListLoad(); else if (!$("#modal .sb-user")) refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.sbMsg = async uid => { const text = await gprompt(`Message to ${uid} (sent by the bot):`, ""); if (!text) return; try { await api(`/api/shopbot/users/${encodeURIComponent(uid)}/message`, {method: "POST", body: JSON.stringify({text})}); toast("✓ Sent", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* ---- plans */
/* the plans (Plans.dc.html): filters (status, category, server, search, sort), a fixed list that fits the window with a numbered pager, and the "Add plan" window */
Object.assign(sb, {pl: {r: null, status: "active", cat: "", server: "", q: "", sort: "cat", page: 0, ps: 0, ask: 0, flash: 0, pin: {}}});
const PL_COLS = "minmax(140px, 1.3fr) 110px minmax(110px, 1fr) 64px 74px 100px 48px 92px 150px";
const plHas = v => v && v !== "NULL" && v !== "0";
async function sbPlans() {
  const r = await api("/api/shopbot/plans"); sb.cats = r.categories; sb.servers = r.servers; sb.plans = r.plans; sb.pl.r = r;
  const all = r.plans, cn = p => plHas(p.catid) ? String(p.catid) : "0", nCat = id => all.filter(p => cn(p) === String(id)).length, nSrv = id => all.filter(p => String(p.server_id) === String(id)).length;
  sbBody(`<div class="sb-card sb-ptool sb-pltool"><div class="sb-ptabs">${[["active", "Active"], ["off", "Off"], ["all", "All"]].map(([k, l]) => `<button type="button" class="sb-ptab ${sb.pl.status === k ? "on" : ""}" data-k="${k}" onclick="sb.pl.status='${k}';sb.pl.page=0;sb.pl.ask=0;sb.pl.pin={};sbPlDraw()">${l}<span id="plc-${k}">…</span></button>`).join("")}</div>
      <select class="sb-sel" aria-label="Category" onchange="sb.pl.cat=this.value;sb.pl.page=0;sb.pl.ask=0;sb.pl.pin={};sbPlDraw()"><option value="">All categories (${all.length})</option>${(r.categories || []).map(c => `<option value="${esc(c.id)}" ${String(sb.pl.cat) === String(c.id) ? "selected" : ""}>${esc(c.title)} (${nCat(c.id)})</option>`).join("")}${nCat(0) ? `<option value="0" ${sb.pl.cat === "0" ? "selected" : ""}>No category (${nCat(0)})</option>` : ""}</select>
      <select class="sb-sel" aria-label="Server" onchange="sb.pl.server=this.value;sb.pl.page=0;sb.pl.ask=0;sb.pl.pin={};sbPlDraw()"><option value="">All servers</option>${(r.servers || []).map(s => `<option value="${esc(s.id)}" ${String(sb.pl.server) === String(s.id) ? "selected" : ""}>${esc(s.title)} (${nSrv(s.id)})</option>`).join("")}</select>
      <label class="sb-psearch"><i>${SI.search}</i><input type="text" placeholder="Search plans…" value="${esc(sb.pl.q)}" oninput="sb.pl.q=this.value.trim().toLowerCase();sb.pl.page=0;sb.pl.ask=0;sb.pl.pin={};sbPlDraw()" autocomplete="off" spellcheck="false"></label>
      <select class="sb-sel" aria-label="Sort" onchange="sb.pl.sort=this.value;sb.pl.page=0;sbPlDraw()">${[["cat", "Sort: category"], ["price", "Sort: price"], ["sold", "Sort: most sold"], ["new", "Sort: newest"]].map(([k, l]) => `<option value="${k}" ${sb.pl.sort === k ? "selected" : ""}>${l}</option>`).join("")}</select>
      <span class="sb-ract"><button type="button" class="sb-btn pri sm0" onclick="sbPlanNew()"><svg viewBox="0 0 16 16"><path d="M8 3v10M3 8h10"/></svg>Add plan</button></span></div>
    <div class="sb-card sb-ptable" style="--minw:${sbMinW(PL_COLS)}px"><div class="sb-lh" style="--cols:${PL_COLS}"><span>Title</span><span>Category</span><span>Server</span><span>Time</span><span>Volume</span><span class="r">Price</span><span class="r">Sold</span><span>On</span><span class="r">Action</span></div><div id="pl-rows" class="sb-lrows"></div><div class="sb-pfoot"><span id="pl-pager" class="pgbar"></span></div></div>`);
  sb.pl.ps = sbFitBox("pl-rows", 44); sbPlDraw();
}
function sbPlDraw() {
  const r = sb.pl.r, box = $("#pl-rows"); if (!r || !box) return;
  const f = sb.pl, cn = p => plHas(p.catid) ? String(p.catid) : "0", num = v => +v || 0;
  ["active", "off", "all"].forEach(k => { const e = $("#plc-" + k); if (e) e.textContent = fmt(r.plans.filter(p => k === "all" || (k === "active") === (p.active === "1")).length); });
  document.querySelectorAll(".sb-pltool .sb-ptab").forEach(b => b.classList.toggle("on", b.dataset.k === f.status));
  const list = r.plans.filter(p => (f.status === "all" || (f.status === "active") === (p.active === "1") || f.pin[p.id]) && (!f.cat || cn(p) === f.cat) && (!f.server || String(p.server_id) === f.server)
    && (!f.q || nkHit(p.title + " " + (p.descr || "") + " " + (p.category || "") + " " + (p.server || "") + " " + (p.price || "") + " " + (p.volume || "") + " " + (p.days || ""), f.q)));
  list.sort(f.sort === "price" ? (a, b) => num(a.price) - num(b.price) : f.sort === "sold" ? (a, b) => num(b.sold) - num(a.sold) : f.sort === "new" ? (a, b) => num(b.id) - num(a.id)
    : (a, b) => String(plHas(a.catid) ? a.category || "" : "￿").localeCompare(String(plHas(b.catid) ? b.category || "" : "￿")) || num(a.days) - num(b.days) || num(a.price) - num(b.price));
  /* sorted by category the list is grouped: a header row for each category (repeated at the top of the next page when a group goes on), the pages are filled with whole rows */
  const ps = f.ps || 10, pagesArr = [];
  if (f.sort === "cat") {
    let cur = [], last = null;
    list.forEach(p => {
      const g = cn(p);
      if (cur.length && (cur.length >= ps || (g !== last && cur.length > ps - 2))) { pagesArr.push(cur); cur = []; }
      if (g !== last || !cur.length) cur.push({hdr: g, title: plHas(p.catid) ? p.category || "" : "", n: list.filter(x => cn(x) === g).length});
      cur.push(p); last = g;
    });
    if (cur.length) pagesArr.push(cur);
  } else for (let i = 0; i < list.length; i += ps) pagesArr.push(list.slice(i, i + ps));
  const pages = Math.max(1, pagesArr.length); f.page = Math.min(f.page, pages - 1);
  const rows = pagesArr[f.page] || [], pbar = $("#pl-pager");
  if (pbar) pbar.innerHTML = list.length ? `<small class="mut">${fmt(list.length)} plans</small>${pages > 1 ? pnPager(pages, 1, f.page, "sbPlPage") : ""}` : "";
  box.innerHTML = rows.map(p => {
    if (p.hdr !== undefined) return `<div class="sb-lr sb-gh" style="--cols:${PL_COLS}"><span class="sb-gt" dir="auto">${p.title ? esc(p.title) : "No category"}<small>${p.n}</small></span></div>`;
    const d = p.descr && p.descr !== "NULL" ? p.descr : "", ask = +f.ask === +p.id, on = p.active === "1";
    const head = `<span class="sb-pu"><b dir="auto" title="${esc(p.title)}">${esc(p.title)}</b><small class="sb-sub"><b>#${esc(p.id)}</b>${d ? `<span dir="auto">${esc(d.split("\n")[0].slice(0, 70))}</span>` : ""}</small></span>`;
    const cells = ask ? `<span class="sb-pask" style="grid-column:2 / 9">Delete this plan? Subscriptions already sold are not touched.</span>`
      : `<span class="sb-cut" dir="auto">${plHas(p.catid) ? esc(p.category || "") : '<span class="mut">No category</span>'}</span><span class="sb-cut" dir="auto" title="${esc(p.server || "")}">${esc(p.server || p.server_id || "")}</span><span>${num(p.days) >= 1000 ? "∞" : num(p.days) ? esc(p.days) + " days" : "∞"}</span><span>${num(p.volume) ? esc(p.volume) + " GB" : "Unlimited"}</span><span class="r sb-pa"><b>${num(p.price) ? fmt(p.price) : "—"}</b>${num(p.price) ? " <em>T</em>" : ""}</span><span class="r">${fmt(p.sold || 0)}</span><span>${sbSw(on, `sbPlanToggle(${p.id}, ${on ? 0 : 1})`, `Show plan ${p.title} in the bot`, on ? "Shown in the bot: click to hide it" : "Hidden from the bot: click to show it")}</span>`;
    const acts = ask ? `<button type="button" class="sb-btn sm0" onclick="sb.pl.ask=0;sbPlDraw()">Keep</button><button type="button" class="sb-btn red sm0" onclick="sbPlanDel(${p.id})">Delete</button>`
      : `<button type="button" class="sb-btn sm0" onclick="sbPlanEdit(${p.id})">Edit</button><button type="button" class="sb-btn dec sm0" aria-label="Delete plan ${esc(p.title)}" onclick="sb.pl.ask=${p.id};sbPlDraw()">Delete</button>`;
    return `<div class="sb-lr ${ask ? "ask-no" : ""} ${on ? "" : "dim"} ${+f.flash === +p.id ? "flash" : ""}" style="--cols:${PL_COLS}">${head}${cells}<span class="sb-pact">${acts}</span></div>`; }).join("")
    || `<div class="sb-pempty"><i>${SI.check}</i>${f.q ? "No plan matches the search." : f.status === "active" && r.plans.length ? "No active plan here. Add one with the button above." : r.plans.length ? "No plan here." : "No plan yet. Add one with the button above."}</div>`;
}
window.sbPlPage = p => { sb.pl.page = Math.max(0, p); sb.pl.ask = 0; sbPlDraw(); };
const sel = (name, options, cur, blank) => `<select name="${name}">${blank ? `<option value="0">${blank}</option>` : ""}${options.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(cur) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
/* the Edit plan window (Plans.dc.html): title, category, server, days, volume, price, stock, devices, active, description, panel JSON */
Object.assign(sb, {pe: null});
window.sbPlanEdit = id => {
  const p = id ? (sb.plans || []).find(x => +x.id === +id) || {} : {}, has = v => v && v !== "NULL";
  sb.pe = {id: id || 0, title: p.title || "", catid: String(has(p.catid) ? p.catid : 0), server_id: String(p.server_id || ((sb.servers || [])[0] || {}).id || ""), days: String(id ? +p.days || 0 : 30), volume: String(id ? +p.volume || 0 : 20), price: id ? String(+p.price || "") : "", acount: String(id ? +p.acount || 0 : 0), limitip: String(id ? +p.limitip || 0 : 1),
    active: !id || p.active === "1", descr: has(p.descr) ? p.descr : "", json: has(p.custom_sni) ? p.custom_sni : "", err: "", apply: false};
  peDraw(); sbPeInb();
};
function peDraw() {
  const f = sb.pe, cats = sb.cats || [], srv = sb.servers || [], opt = (v, l, cur) => `<option value="${esc(v)}" ${String(v) === String(cur) ? "selected" : ""}>${esc(l)}</option>`;
  const txt = (k, label, mode = "text") => `<div class="qp-f"><label for="pe-${k}">${label}</label><input id="pe-${k}" class="qp-in" type="text" ${mode === "num" ? 'inputmode="numeric"' : ""} autocomplete="off" value="${esc(f[k])}" oninput="sbPe('${k}', this, '${mode}')"></div>`;
  showModal(`<b>${f.id ? "Edit plan #" + f.id : "New plan"}</b>`, `<form class="qp" onsubmit="return sbPlanSave(event)" novalidate>
      <div class="qp-grid three">${txt("title", "Title")}
        <div class="qp-f"><label for="pe-catid">Category</label><span class="qp-sw"><select id="pe-catid" class="qp-sel" onchange="sb.pe.catid=this.value;sbPeErr('')">${opt(0, "— none —", f.catid)}${cats.map(c => opt(c.id, c.title, f.catid)).join("")}</select>${qpChev}</span></div>
        <div class="qp-f"><label for="pe-server_id">Server (panel)</label><span class="qp-sw"><select id="pe-server_id" class="qp-sel" onchange="sb.pe.server_id=this.value;sb.pe.json='';sb.pe.apply=false;sbPeErr('');peDraw();sbPeInb()">${opt(0, "— none —", f.server_id)}${srv.map(s => opt(s.id, s.title + (s.type ? " · " + s.type : ""), f.server_id)).join("")}</select>${qpChev}</span></div>
        ${txt("days", "Days", "num")}${txt("volume", "Volume (GB, 0 = unlimited)", "dec")}${txt("price", "Price (Toman)", "num")}${txt("acount", "Stock (0 = unlimited)", "num")}${txt("limitip", "Max devices (IP limit, 0 = no limit)", "num")}
        <div class="qp-f"><label>&nbsp;</label><button type="button" class="qp-chk" role="checkbox" aria-checked="${f.active}" onclick="sb.pe.active=!sb.pe.active;peDraw()"><span class="${f.active ? "on" : ""}">${f.active ? '<svg viewBox="0 0 16 16"><path d="M3.5 8.5l3 3L12.5 5"/></svg>' : ""}</span>Active (shown in the bot)</button></div></div>
      <div class="qp-f"><label for="pe-descr">Description</label><textarea id="pe-descr" class="qp-ta" dir="auto" rows="2" oninput="sb.pe.descr=this.value">${esc(f.descr)}</textarea></div>
      <div class="qp-f"><label>Inbounds <small>what the account gets on the panel</small></label><div id="pe-pick" class="pe-pick"></div></div>
      <details class="pe-adv"><summary>Panel settings (JSON)</summary><div class="qp-f"><label for="pe-json"><small class="mut">Which groups or inbounds the account gets. PasarGuard: <code>{"group_ids":[1]}</code> &middot; Marzban: <code>{"inbounds":{"vless":["VLESS TCP"]}}</code></small></label><textarea id="pe-json" class="qp-ta mono" rows="2" spellcheck="false" oninput="sb.pe.json=this.value;sbPeErr('')">${esc(f.json)}</textarea></div></details>
      <div class="qp-act"><span class="qp-lerr"><button type="submit" class="qp-pri">Save</button><span role="alert" id="qp-err"></span></span></div></form>`, "plan-add plan-edit");
  sbPeErr(f.err); sbPeInbDraw();
}
window.sbPe = (k, el, mode) => { const v = mode === "text" ? el.value : mode === "dec" ? el.value.replace(/[۰-۹]/g, c => "۰۱۲۳۴۵۶۷۸۹".indexOf(c)).replace(/[^0-9.]/g, "") : sbDigits(el); if (v !== el.value) el.value = v; sb.pe[k] = v; sbPeErr(""); };
window.sbPeErr = m => {
  sb.pe.err = m; const e = $("#qp-err"); if (e) e.textContent = m;
  const key = {Type: "title", Days: "days", Volume: "volume", Price: "price", Stock: "stock", Max: "limitip", Panel: "json"}[(m || "").split(" ")[0]], id = {stock: "acount"}[key] || key;
  document.querySelectorAll("#modal .qp-in, #modal .qp-ta").forEach(i => { const bad = !!m && i.id === "pe-" + id; i.classList.toggle("bad", bad); i.setAttribute("aria-invalid", bad); });
};
/* the inbounds of a plan: pick them from what the plan's panel offers; one tick gives the change to the accounts already bought as well */
const PE_PROXY = {vmess: [], vless: {flow: ""}, trojan: [], shadowsocks: {method: "chacha20-ietf-poly1305"}};
window.sbPeInb = async () => {
  const f = sb.pe, sid = f.server_id; f.inb = null; f.count = null; sbPeInbDraw();
  if (!sid || sid === "0") return;
  try { const r = await api(`/api/shopbot/plan-inbounds?server_id=${sid}`); if (sb.pe !== f || f.server_id !== sid) return; f.inb = r; } catch (e) { f.inb = {panel_id: null, error: e.message}; }
  if (f.id) { try { f.count = (await api(`/api/shopbot/plans/${f.id}/accounts`)).count; } catch (e) { f.count = null; } }
  if (sb.pe !== f) return;
  let cur = {}; try { cur = JSON.parse(f.json || "{}") || {}; } catch (e) { /* a text that is not JSON: the picker starts empty */ }
  f.sel = {}; Object.entries(cur.inbounds || {}).forEach(([p, t]) => { f.sel[p] = [...t]; }); f.gsel = [...(cur.group_ids || [])];
  sbPeInbDraw();
};
function sbPeInbDraw() {
  const box = $("#pe-pick"), f = sb.pe; if (!box || !f) return; const r = f.inb;
  if (!r) { box.innerHTML = `<div class="mut">${f.server_id && f.server_id !== "0" ? "Reading the panel…" : "Pick a server first."}</div>`; return; }
  if (r.panel_id == null) { box.innerHTML = `<div class="pe-note">${r.error ? esc(r.error) + " · " : ""}This server's panel is not in <b>Users</b> (the panels page), so its inbounds cannot be listed. Add the panel there, or write the settings in the JSON box below.</div>`; return; }
  let body;
  if (r.type === "pasarguard") {
    body = `<div class="pe-grp"><div class="pe-gh"><b>Groups</b><span class="sp"></span></div><div class="pe-chs">${(r.groups || []).map((g, i) => `<button type="button" class="pe-ch ${f.gsel.includes(g.id) ? "on" : ""}" role="checkbox" aria-checked="${f.gsel.includes(g.id)}" onclick="sbPeGroup(${i})">${esc(g.name)}<small>#${esc(g.id)}</small></button>`).join("") || '<span class="mut">no group on the panel</span>'}</div></div>`;
  } else {
    f.protos = Object.entries((r.inbounds || []).reduce((a, i) => { (a[i.protocol] = a[i.protocol] || []).push(i); return a; }, {}));
    body = f.protos.map(([p, list], pi) => { const on = (f.sel[p] || []).length; return `<div class="pe-grp"><div class="pe-gh"><b>${esc(p)}</b><small>${on} / ${list.length}</small><span class="sp"></span><button type="button" class="pe-lk" onclick="sbPeAll(${pi}, 1)">all</button><button type="button" class="pe-lk" onclick="sbPeAll(${pi}, 0)">none</button></div>
      <div class="pe-chs">${list.map((i, ti) => { const ck = (f.sel[p] || []).includes(i.tag); return `<button type="button" class="pe-ch ${ck ? "on" : ""}" role="checkbox" aria-checked="${ck}" onclick="sbPeTog(${pi}, ${ti})">${esc(i.tag)}${i.port ? `<small>${esc(i.port)}</small>` : ""}</button>`; }).join("")}</div></div>`; }).join("") || '<span class="mut">no inbound on the panel</span>';
  }
  box.innerHTML = `<div class="pe-box">${body}</div>${f.id ? qpChk(!!f.apply, "sbPeApplyTog()", `Also change the accounts already bought with this plan${f.count != null ? ` <small>(${f.count} active)</small>` : ""}`) : ""}`;
}
function sbPeBuild() {      // the picker writes the JSON the bot reads (the other keys and the proxy settings of the plan stay)
  const f = sb.pe, r = f.inb; let cur = {}; try { cur = JSON.parse(f.json || "{}") || {}; } catch (e) { cur = {}; }
  if (r.type === "pasarguard") cur.group_ids = [...f.gsel];
  else { const inb = Object.fromEntries(Object.entries(f.sel).filter(([, t]) => t.length)); cur.inbounds = inb; cur.proxies = Object.fromEntries(Object.keys(inb).map(p => [p, (cur.proxies || {})[p] ?? PE_PROXY[p] ?? {}])); }
  f.json = JSON.stringify(cur); const t = $("#pe-json"); if (t) t.value = f.json; sbPeErr("");
}
window.sbPeTog = (pi, ti) => { const f = sb.pe, [p, list] = f.protos[pi], tag = list[ti].tag, s = f.sel[p] = f.sel[p] || []; const k = s.indexOf(tag); if (k < 0) s.push(tag); else s.splice(k, 1); sbPeBuild(); sbPeInbDraw(); };
window.sbPeAll = (pi, on) => { const f = sb.pe, [p, list] = f.protos[pi]; f.sel[p] = on ? list.map(i => i.tag) : []; sbPeBuild(); sbPeInbDraw(); };
window.sbPeGroup = i => { const f = sb.pe, id = f.inb.groups[i].id, k = f.gsel.indexOf(id); if (k < 0) f.gsel.push(id); else f.gsel.splice(k, 1); sbPeBuild(); sbPeInbDraw(); };
window.sbPeApplyTog = () => { sb.pe.apply = !sb.pe.apply; sbPeInbDraw(); };
async function sbPeRun(id) {
  try {
    const r = await api(`/api/shopbot/plans/${id}/apply`, {method: "POST"}); toast(`Changing ${r.total} accounts on the panel…`);
    const t = setInterval(async () => { try { const j = await api(`/api/shopbot/jobs/${r.job}`); if (j.finished) { clearInterval(t); toast(`✓ ${j.done} of ${j.total} accounts changed${j.failed ? ` · ${j.failed} failed: ${j.errors[0] || ""}` : ""}`, j.failed ? "bad" : "ok"); } } catch (e) { clearInterval(t); toast("✕ " + e.message, "bad"); } }, 2500);
  } catch (e) { toast("✕ " + e.message, "bad"); }
}
window.sbPlanSave = async e => {
  e.preventDefault(); const f = sb.pe, fail = m => { sbPeErr(m); return false; }, n = k => f[k] === "" ? NaN : parseFloat(f[k]);
  if (!f.title.trim()) return fail("Type a title.");
  if (!(n("days") >= 1)) return fail("Days must be 1 or more.");
  if (!(n("volume") >= 0)) return fail("Volume must be 0 (unlimited) or more.");
  if (!(n("price") > 0)) return fail("Price must be more than 0.");
  if (!(n("acount") >= 0)) return fail("Stock must be 0 (unlimited) or more.");
  if (!(n("limitip") >= 0)) return fail("Max devices must be 0 or more.");
  if (f.json.trim()) { try { JSON.parse(f.json); } catch (x) { return fail("Panel settings must be valid JSON."); } }
  if (f.apply && f.id && !await gconfirm(`Change the inbounds of ${f.count != null ? f.count : "all the"} accounts already bought with this plan, on the panel?\n\nTheir links, traffic and expiry stay; only the inbounds change. New purchases get them anyway.`)) return false;
  const fields = {title: f.title.trim(), catid: +f.catid || 0, server_id: +f.server_id || 0, days: n("days"), volume: n("volume"), price: Math.round(n("price")), acount: Math.round(n("acount")), limitip: Math.round(n("limitip")), active: f.active ? 1 : 0, descr: f.descr, custom_sni: f.json.trim()};
  try {
    const r = await api(f.id ? `/api/shopbot/plans/${f.id}` : "/api/shopbot/plans", {method: f.id ? "PUT" : "POST", body: JSON.stringify({fields})});
    closeModal(); toast(f.id ? `✓ Saved plan #${f.id}.${f.active ? "" : " It is hidden from the bot."}` : "✓ Plan added to the bot", "ok");
    sb.pl.flash = r.id; await sbPlans(); setTimeout(() => { if (+sb.pl.flash === +r.id) { sb.pl.flash = 0; sbPlDraw(); } }, 2600);
    if (f.apply && f.id) sbPeRun(f.id);
  } catch (err) { fail(err.message); } return false;
};
const FA = n => String(n).replace(/\d/g, d => "۰۱۲۳۴۵۶۷۸۹"[d]);
/* "Add plan" window (Plans.dc.html): category + server + time + volume + price -> a complete plan; the title is made from the choices, panel settings and type are copied from a plan of the same server */
const QP_DAYS = [1, 7, 15, 30, 60, 90, 180, 365], QP_GB = [1, 5, 10, 20, 30, 50, 100, 200, 500];
const qpTitle = (vol, days) => (vol ? FA(vol) + " گیگ" : "نامحدود") + " " + (days && days < 1000 ? FA(days) + " روز" : "بدون محدودیت زمان");      // 1000+ days = no time limit (the bot's own plans use 2000)
const qpChev = '<svg viewBox="0 0 16 16"><path d="M3.5 6l4.5 4.5L12.5 6"/></svg>';
window.sbPlanNew = () => {
  const r = sb.pl.r || {}, last = (r.plans || []).reduce((a, p) => +p.id > +(a.id || 0) ? p : a, {}), srv = r.servers || [];
  sb.qp = {catid: plHas(last.catid) ? String(last.catid) : "0", server_id: String(last.server_id || (srv[0] || {}).id || ""), days: 30, volume: 20, price: "", err: "", newCat: false, catName: ""};
  qpDraw();
};
function qpDraw() {
  const q = sb.qp, r = sb.pl.r || {categories: [], servers: []}, opt = (v, l, cur) => `<option value="${esc(v)}" ${String(v) === String(cur) ? "selected" : ""}>${esc(l)}</option>`;
  showModal(`<b>Add a plan to the bot</b>`, `<form class="qp" onsubmit="return sbQpSubmit(event)" novalidate>
      <p class="qp-sub">Pick the category, time and volume. It appears in the Telegram bot and the Mini App at once.</p>
      <div class="qp-grid">
        <div class="qp-f"><label for="qp-cat">Category</label><div class="qp-row"><span class="qp-sw"><select id="qp-cat" class="qp-sel" onchange="sbQp('catid', this.value)">${opt(0, "— none —", q.catid)}${(r.categories || []).map(c => opt(c.id, c.title, q.catid)).join("")}</select>${qpChev}</span><button type="button" class="qp-plus" aria-label="New category" aria-expanded="${q.newCat}" onclick="sbQpCat(${!q.newCat})"><svg viewBox="0 0 16 16"><path d="M8 3v10M3 8h10"/></svg></button></div></div>
        <div class="qp-f"><label for="qp-srv">Server</label><span class="qp-sw"><select id="qp-srv" class="qp-sel" onchange="sbQp('server_id', this.value)">${(r.servers || []).map(s => opt(s.id, s.title, q.server_id)).join("")}</select>${qpChev}</span></div>
        <div class="qp-f"><label for="qp-days">Time</label><span class="qp-sw"><select id="qp-days" class="qp-sel" onchange="sbQp('days', +this.value)">${QP_DAYS.map(d => opt(d, d + " days", q.days)).join("")}${opt(2000, "No time limit", q.days)}</select>${qpChev}</span></div>
        <div class="qp-f"><label for="qp-gb">Volume</label><span class="qp-sw"><select id="qp-gb" class="qp-sel" onchange="sbQp('volume', +this.value)">${QP_GB.map(g => opt(g, g + " GB", q.volume)).join("")}${opt(0, "Unlimited", q.volume)}</select>${qpChev}</span></div>
        <div class="qp-f"><label for="qp-price">Price (Toman)</label><input id="qp-price" class="qp-in" type="text" inputmode="numeric" autocomplete="off" placeholder="150000" value="${esc(q.price)}" oninput="sbQpPrice(this)" autofocus></div>
      </div>
      ${q.newCat ? `<div class="qp-newcat"><span>New category</span><input id="qp-cn" class="qp-in" type="text" aria-label="New category name" autocomplete="off" placeholder="For example: Gaming" value="${esc(q.catName)}" oninput="sb.qp.catName=this.value" onkeydown="if(event.key==='Enter'){event.preventDefault();sbQpCatSave()}"><button type="button" class="qp-pri sm" onclick="sbQpCatSave()">Save category</button><button type="button" class="qp-ghost" onclick="sbQpCat(false)">Cancel</button></div>` : ""}
      <div class="qp-prev"><span>Title will be</span><b dir="rtl" id="qp-title"></b><span id="qp-ptxt"></span><span role="alert" id="qp-err"></span></div>
      <small class="qp-hint">Use Edit afterwards for a custom title, description or stock. Panel settings are copied from the other plans of the same server.</small>
      <div class="qp-act"><button type="button" class="qp-ghost" onclick="closeModal();sbPlanEdit(null)">Detailed form…</button><button type="submit" class="qp-pri"><svg viewBox="0 0 16 16"><path d="M8 3v10M3 8h10"/></svg>Add</button></div></form>`, "plan-add keep");
  qpPrev(); if (q.newCat) { const i = $("#qp-cn"); if (i) i.focus(); }
}
function qpPrev() {
  const q = sb.qp, t = $("#qp-title"); if (!t) return;
  const p = +q.price || 0, pi = $("#qp-price"), bad = /price/i.test(q.err);
  t.textContent = qpTitle(+q.volume, +q.days); $("#qp-ptxt").textContent = p ? fmt(p) + " Toman" : ""; $("#qp-err").textContent = q.err;
  if (pi) { pi.setAttribute("aria-invalid", bad); pi.classList.toggle("bad", bad); }
}
window.sbQp = (k, v) => { sb.qp[k] = v; sb.qp.err = ""; qpPrev(); };
window.sbQpPrice = el => { const d = el.value.replace(/[۰-۹]/g, c => "۰۱۲۳۴۵۶۷۸۹".indexOf(c)).replace(/[٠-٩]/g, c => "٠١٢٣٤٥٦٧٨٩".indexOf(c)).replace(/\D/g, ""); if (d !== el.value) el.value = d; sbQp("price", d); };
window.sbQpCat = open => { sb.qp.newCat = open; sb.qp.catName = ""; sb.qp.err = ""; qpDraw(); };
window.sbQpCatSave = async () => {
  const q = sb.qp, name = (q.catName || "").trim(), fail = m => { q.err = m; qpPrev(); };
  if (!name) return fail("Type a name for the new category.");
  if ((sb.cats || []).some(c => String(c.title).trim().toLowerCase() === name.toLowerCase())) return fail("That category already exists.");
  try {
    const res = await api("/api/shopbot/categories", {method: "POST", body: JSON.stringify({title: name, parent: 0, active: true})});
    sb.cats = (await api("/api/shopbot/categories")).categories; if (sb.pl.r) sb.pl.r.categories = sb.cats;
    q.catid = String(res.id); q.newCat = false; q.err = ""; q.catName = ""; qpDraw(); toast(`✓ Category “${name}” added and selected`, "ok");
    if (sb.tab === "plans") sbPlans();      // the filter in the list learns the new category too
  } catch (e) { fail(e.message); }
};
window.sbQpSubmit = async e => {
  e.preventDefault(); const q = sb.qp, price = +q.price || 0, r = sb.pl.r || {plans: []}, fail = m => { q.err = m; qpPrev(); return false; };
  if (!q.server_id) return fail("Pick the server (panel) the account is made on.");
  if (!(price > 0)) return fail("Enter a price in Toman, digits only.");
  if ((r.plans || []).some(p => String(plHas(p.catid) ? p.catid : 0) === String(q.catid) && String(p.server_id) === String(q.server_id) && +p.days === +q.days && +p.volume === +q.volume)) return fail("A plan with this category, server, time and volume already exists.");
  const sid = +q.server_id, srv = (sb.servers || []).find(s => +s.id === sid) || {}, tpl = (sb.plans || []).find(p => +p.server_id === sid && p.custom_sni && p.custom_sni !== "NULL") || {};
  const fields = {title: qpTitle(+q.volume, +q.days), catid: +q.catid, server_id: sid, days: +q.days, volume: +q.volume, price, acount: 1000, limitip: 0, active: 1,
    type: tpl.type || (["marzban", "pasarguard", "marzneshin"].includes(srv.type) ? "marzban" : "normal"), protocol: tpl.protocol || "vless", inbound_id: 0, custom_sni: tpl.custom_sni || ""};
  try {
    const res = await api("/api/shopbot/plans", {method: "POST", body: JSON.stringify({fields})});
    closeModal(); toast(`✓ Plan added: ${fields.title} · ${fmt(price)} Toman. It is live in the bot and the Mini App.`, "ok");
    Object.assign(sb.pl, {status: "active", cat: "", server: "", q: "", sort: "new", page: 0, ask: 0, flash: res.id}); await sbPlans();
    setTimeout(() => { if (+sb.pl.flash === +res.id) { sb.pl.flash = 0; sbPlDraw(); } }, 2600);
  } catch (err) { fail(err.message); } return false;
};
window.sbPlanToggle = async (id, active) => {      // flips the switch in place: no reload, nothing jumps
  const p = ((sb.pl.r || {}).plans || []).find(x => +x.id === +id);
  try { await api(`/api/shopbot/plans/${id}`, {method: "PUT", body: JSON.stringify({fields: {active}})}); if (p) { p.active = String(active); sb.pl.pin[p.id] = 1; } sbPlDraw(); toast(active ? "✓ Shown in the bot" : "✓ Hidden from the bot. It stays in the list until you change the filter.", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.sbPlanDel = async id => {
  try { await api(`/api/shopbot/plans/${id}`, {method: "DELETE"}); const r = sb.pl.r; if (r) r.plans = r.plans.filter(x => +x.id !== +id); sb.plans = r ? r.plans : sb.plans; sb.pl.ask = 0; sbPlDraw(); toast("✓ Plan deleted", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
};
/* ---- categories */
/* ---- servers (the panels the bot sells from) */
/* ---- discounts */
const AUD = {all: "everyone", agents: "agents", non_agents: "non-agents", buyers: "buyers", non_buyers: "never bought", has_active_order: "has an active subscription", no_active_order: "no active subscription", user: "one user"};
/* ---- gift codes */
/* ---- broadcast */
const BC_STATE = {0: ["draft", ""], 1: ["queued", ""], 2: ["sending", "ok"], 3: ["done", "ok"], 4: ["cancelled", "err"]};
/* ---- tickets */
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
/* ---- settings: one group at a time, switches for on/off, small two-line cells; what you change is counted and written together ---- */
/* ---- settings: every group on one page, side by side; the few settings people touch show first, the rest opens with the arrow of its group ---- */
Object.assign(sb, {st: null, stOpen: {}});
const ST_PAY = [["bankAccount", "Card number"], ["holderName", "Card holder"], ["zarinpal", "ZarinPal merchant id"], ["nextpay", "NextPay key"], ["nowpayment", "NowPayments key"], ["tronwallet", "TRON wallet address"]];
const ST_NOTE = "Every switch the bot reads on each message. Only what you change is written.";
const ST_MAIN = new Set(["botState", "sellState", "renewAccountState", "testAccount", "agencyState", "agencyPlanDiscount", "walletState", "cartToCartState", "cartToCartAutoAcceptState", "aiReceiptState", "zarinpal", "nextpay", "gbPrice", "dayPrice", "cashbackPercent", "supportID", "rewardChannel", "lockChannel",
  "requirePhone", "requireIranPhone", "firstUseState", "miniAppState", "subLinkState", "configLinkState", "qrSubState", "qrConfigState", "wheelState", "giftState", "lotteryState", "renewAutoState", "pk:bankAccount", "pk:holderName"]);
async function sbSettings() {
  const d = await api("/api/shopbot/settings");
  const groups = d.groups.map(g => ({title: g.title, items: g.items.map(i => ({key: i.key, label: i.label, hint: i.hint || "", kind: i.kind, value: i.kind === "onoff" ? (i.value === "on" ? "on" : "off") : String(i.value ?? "")}))}));
  groups.push({title: "Payment details", items: ST_PAY.map(([k, l]) => ({key: "pk:" + k, label: l, hint: "", kind: "text", value: String(d.payment_keys[k] || "")}))});
  const other = Object.entries(d.other || {}); if (other.length) groups.push({title: "Other keys", items: other.map(([k, v]) => ({key: k, label: k, hint: "", kind: "text", mono: true, value: String(v)}))});
  const was = {}; groups.forEach(g => g.items.forEach(i => { was[i.key] = i.value; }));
  sb.st = {groups, was, vals: {...was}};
  sbBody(`<div class="sb-card sb-ptool"><button type="button" class="sb-btn pri sm0" id="st-save" onclick="sbStSave()">Save changed<span class="chg" id="st-n"></span></button><button type="button" class="sb-btn sm0" id="st-discard" onclick="sbStDiscard()">Discard</button><span class="sb-hint">${ST_NOTE}</span></div><div class="sb-card sb-stbox" id="st-box"></div>`);
  sbStDraw();
}
const sbStChanged = gr => gr.items.filter(i => sb.st.vals[i.key] !== sb.st.was[i.key]).length;
const sbStCell = i => {
  const st = sb.st, v = st.vals[i.key], k = esc(i.key), chg = v !== st.was[i.key], tip = esc(i.hint ? `${i.label}: ${i.hint}` : i.label);
  const ctl = i.kind === "onoff" ? sbSw(v === "on", `sbStSw('${k}')`, i.label) : `<input class="st-in ${i.mono ? "mono" : ""}" type="text" ${i.kind === "number" ? 'inputmode="numeric"' : ""} value="${esc(v)}" aria-label="${esc(i.label)}" oninput="sbStIn('${k}', this)" autocomplete="off" spellcheck="false">`;
  return `<div class="st-it ${chg ? "chg" : ""}" title="${tip}"><b>${esc(i.label)}</b><div class="st-c">${ctl}</div></div>`;
};
function sbStDraw() {
  const st = sb.st;
  $("#st-box").innerHTML = `<div class="st-secs">${st.groups.map((g, gi) => {
    let main = g.items.filter(i => ST_MAIN.has(i.key)), more = g.items.filter(i => !ST_MAIN.has(i.key));
    if (!main.length) { main = more; more = []; }      // a group with nothing in front shows all of it
    const open = !!sb.stOpen[gi], moreChg = more.filter(i => st.vals[i.key] !== st.was[i.key]).length;
    return `<section class="st-sec"><div class="st-sh"><b>${esc(g.title)}</b><span class="sp"></span>${more.length ? `<button type="button" class="st-more ${open ? "open" : ""}" aria-expanded="${open}" onclick="sb.stOpen[${gi}]=!sb.stOpen[${gi}];sbStDraw()"><svg viewBox="0 0 16 16"><path d="M3.5 6l4.5 4.5L12.5 6"/></svg>More<small>${more.length}</small>${moreChg && !open ? `<span class="chg">${moreChg}</span>` : ""}</button>` : ""}</div>
      <div class="st-grid">${main.map(sbStCell).join("")}${open ? more.map(sbStCell).join("") : ""}</div></section>`; }).join("")}</div>`;
  sbStMark();
}
function sbStMark() {
  const total = sb.st.groups.reduce((n, gr) => n + sbStChanged(gr), 0), n = $("#st-n"), s = $("#st-save"), d = $("#st-discard");
  if (n) n.textContent = total || ""; if (s) s.disabled = !total; if (d) d.disabled = !total;
}
window.sbStSw = k => { sb.st.vals[k] = sb.st.vals[k] === "on" ? "off" : "on"; sbStDraw(); };
window.sbStIn = (k, el) => { sb.st.vals[k] = el.value; el.closest(".st-it").classList.toggle("chg", el.value !== sb.st.was[k]); sbStMark(); };      // typing marks the cell and the counter without drawing again: the box keeps its focus
window.sbStDiscard = () => { sb.st.vals = {...sb.st.was}; sbStDraw(); };
window.sbStSave = async () => {
  const st = sb.st, states = {}, keys = {};
  Object.keys(st.vals).forEach(k => { if (st.vals[k] !== st.was[k]) { if (k.startsWith("pk:")) keys[k.slice(3)] = st.vals[k]; else states[k] = st.vals[k]; } });
  if (!Object.keys(states).length && !Object.keys(keys).length) { toast("Nothing changed"); return; }
  try { if (Object.keys(states).length) await api("/api/shopbot/settings", {method: "PUT", body: JSON.stringify({changes: states})}); if (Object.keys(keys).length) await api("/api/shopbot/payment-keys", {method: "PUT", body: JSON.stringify({changes: keys})}); toast("✓ Saved: " + [...Object.keys(states), ...Object.keys(keys)].join(", "), "ok"); sb.infoAt = 0; await sbSettings(); } catch (err) { toast("✕ " + err.message, "bad"); }
};

/* ---- one user: a smaller window; payments in a small scrolling box; the discount is edited right in the account box ---- */
window.sbUser = async uid => {
  showModal(`<b>User ${esc(uid)}</b>`, '<div class="sb-user loading"><i class="spin"></i><span>Reading…</span></div>', "user-box");
  try {
    const d = await api(`/api/shopbot/users/${encodeURIComponent(uid)}`), u = d.user, id = esc(uid), ban = u.step === "banned", agent = u.is_agent === "1";
    const act = (ic, label, click, cls = "") => `<button type="button" class="ua ${cls}" onclick="${click}"><i>${ic}</i>${label}</button>`, none = '<div class="mut ie">none</div>';
    const pays = d.pays.map(p => irow(`<b>#${esc(p.id)}</b> <small class="mut">${esc(p.type || "")}</small>`, `${money(p.price)} · ${esc(p.state)}`)), extras = [...d.wheel.map(w => irow("Wheel prize", money(w.prize))), ...d.cashback.map(c => irow("Cashback", money(c.cashback) + " on " + money(c.order_amount)))];
    showModal(`<span class="st ${ban ? "down" : "up"}"></span><b>${esc(u.name || uid)}</b> <span class="mono mut">${id}</span> ${u.username ? `<span class="pill">@${esc(u.username)}</span>` : ""}${agent ? '<span class="pill ok">agent</span>' : ""}${ban ? '<span class="pill err">banned</span>' : ""}`, `<div class="sb-user">
      <div class="ugrid"><div class="ibox"><h5><i>👤</i>Account</h5>${[irow("Wallet", `<b>${money(u.wallet)}</b>`), irow("Phone", esc(u.phone && u.phone !== "NULL" ? u.phone : "—")), irow("Joined", tsFa(u.date)),
        irow("Discount", `<span class="sb-disc"><input type="number" min="0" max="100" value="${disc(u.discount_percent)}" onchange="sbUDisc('${id}', this)" title="The percent this user takes off every plan. It is saved when you leave the box." aria-label="Discount percent"><em>%</em></span>`),
        irow("Referred by", esc(u.refered_by && u.refered_by !== "NULL" && u.refered_by !== "0" ? u.refered_by : "—")), irow("Brought in", esc(d.referred) + " users"), irow("Free trial", esc(u.freetrial || "—"))].join("")}</div>
        <div class="ibox"><h5><i>🛠</i>Actions</h5><div class="uacts">${act("💰", "± Wallet", `(async()=>sbU('${id}', 'wallet', await gprompt('Add to the wallet (Toman, negative to take)', '10000')))()`)}${act("🎯", "Set wallet", `(async()=>sbU('${id}', 'wallet_set', await gprompt('Set the wallet to (Toman)', '${esc(u.wallet || 0)}')))()`)}${act("🤝", agent ? "Agent off" : "Make agent", `sbU('${id}', '${agent ? "agent_off" : "agent_on"}')`)}${act("✉", "Message", `sbMsg('${id}')`)}${act("⛔", ban ? "Unban" : "Ban", `sbU('${id}', '${ban ? "unban" : "ban"}')`, ban ? "" : "danger")}</div></div></div>
      <div class="ibox"><h5><i>🧾</i>Subscriptions <small class="mut">${d.orders.length}</small></h5><div class="iscroll">${d.orders.map(o => irow(`<b>${esc(o.remark || "#" + o.id)}</b> <small class="mut">${esc(o.server || "")}</small>`, `${money(o.amount)} · until ${tsFa(o.expire_date)} · ${o.status === "1" ? '<b class="gw-ok">active</b>' : '<span class="mut">off</span>'}`)).join("") || none}</div></div>
      <div class="ugrid" style="margin-top:10px"><div class="ibox"><h5><i>💳</i>Payments <small class="mut">${d.pays.length}</small></h5><div class="iscroll">${pays.join("") || none}</div></div><div class="ibox"><h5><i>🎁</i>Extras</h5><div class="iscroll">${extras.join("") || none}</div></div></div></div>`, "user-box");
  } catch (e) { showModal(`<b>User ${esc(uid)}</b>`, `<div class="err">${esc(e.message)}</div>`, "user-box"); }
};
window.sbUDisc = async (uid, el) => { const v = Math.max(0, Math.min(100, Math.round(+el.value || 0))); el.value = v; try { await api("/api/shopbot/users/action", {method: "POST", body: JSON.stringify({action: "discount", userid: uid, value: v})}); toast(`✓ Discount ${v}% saved`, "ok"); if ($("#ls-rows")) sbListLoad(); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* ---- texts and buttons: a list of fixed rows with the text in an own box, a pager, one Save for everything changed on every page ---- */
Object.assign(sb, {txEditK: {text: {}, button: {}}});
const TX_COLS = "minmax(180px, .9fr) minmax(320px, 3fr) 88px";
const sbTxData = () => { const d = sb.txData, t = sb.txKind === "text"; return {base: t ? d.texts : d.buttons, ov: t ? d.text_overrides : d.button_overrides, ed: sb.txEditK[sb.txKind]}; };
const sbTxList = () => { const {base, ov} = sbTxData(), q = sb.txQ.toLowerCase(); return Object.keys(base).filter(k => !q || nkHit(k + " " + base[k] + " " + (ov[k] || ""), q)).map(k => ({k, base: base[k], ov: ov[k]})); };
async function sbTexts() {
  if (!sb.txData) sb.txData = await api("/api/shopbot/texts");
  const d = sb.txData;
  sbBody(`<div class="sb-card sb-ptool"><button type="button" class="sb-btn pri sm0" id="tx-save" onclick="sbTextsSave()">Save changed<span class="chg" id="tx-n"></span></button>${d.hooked || sb.txKind === "button" ? "" : '<span class="sb-hint">First save: the bot is made to read the panel\'s text file (one line in its config, checked before it is kept).</span>'}
      <div class="sb-ptabs" id="tx-tabs">${[["text", "Texts", Object.keys(d.texts).length], ["button", "Buttons", Object.keys(d.buttons).length]].map(([k, l, n]) => `<button type="button" class="sb-ptab ${sb.txKind === k ? "on" : ""}" data-k="${k}" onclick="sbTxKind('${k}')">${l}<span>${n}</span></button>`).join("")}</div>
      <label class="sb-psearch"><i>${SI.search}</i><input type="text" placeholder="search a key or a text…" value="${esc(sb.txQ)}" oninput="sbTxQ(this.value)" autocomplete="off" spellcheck="false"></label>
      <button type="button" class="sb-btn sm0" title="Read them again from the bot" onclick="sb.txData=null;sb.txEditK={text: {}, button: {}};sbTexts()">↻</button></div>
    ${sbListCard(TX_COLS, 0, [["Key"], ["Text"], [""]])}`);
  sbTxCount();
  await sbListInit({cols: TX_COLS, base: 80, key: x => x.k, fetch: sbClient(sbTxList), empty: () => "No text matches.",
    row: x => { const {ed} = sbTxData(), cur = ed[x.k] ?? (x.ov ?? x.base), k = esc(x.k);
      return `<span class="sb-pu"><b class="mono" title="${k}">${k}</b><small>${x.ov !== undefined ? "changed" : ""}</small></span><textarea class="st-ta" rows="2" spellcheck="false" dir="auto" aria-label="${k}" oninput="sbTxIn('${k}', this)">${esc(cur)}</textarea><span class="sb-pact">${x.ov !== undefined ? `<button type="button" class="sb-btn sm0" onclick="sbTextReset('${k}')">Reset</button>` : ""}</span>`; }});
}
function sbTxCount() {
  const {base, ov, ed} = sbTxData(), n = Object.keys(ed).filter(k => ed[k] !== (ov[k] ?? base[k])).length, e = $("#tx-n"); if (e) e.textContent = n || "";
  const b = $("#tx-save"); if (b) b.classList.toggle("quiet", !n);
}
window.sbTxIn = (k, el) => { sbTxData().ed[k] = el.value; sbTxCount(); };
window.sbTxKind = k => { sb.txKind = k; document.querySelectorAll("#tx-tabs .sb-ptab").forEach(b => b.classList.toggle("on", b.dataset.k === k)); sbTxCount(); sbListLoad(0); };
window.sbTxQ = v => { sb.txQ = v; clearTimeout(qT); qT = setTimeout(() => sbListLoad(0), 250); };
window.sbTextsSave = async () => {
  const {base, ov, ed} = sbTxData(), ch = {};
  Object.keys(ed).forEach(k => { if (ed[k] !== (ov[k] ?? base[k])) ch[k] = ed[k] === base[k] ? "" : ed[k]; });
  if (!Object.keys(ch).length) { toast("Nothing changed"); return; }
  try { await api("/api/shopbot/texts", {method: "PUT", body: JSON.stringify({kind: sb.txKind, changes: ch})}); toast(`✓ ${Object.keys(ch).length} saved — the bot uses them from the next message`, "ok"); sb.txEditK[sb.txKind] = {}; sb.txData = null; await sbTexts(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.sbTextReset = async k => { try { await api("/api/shopbot/texts", {method: "PUT", body: JSON.stringify({kind: sb.txKind, changes: {[k]: ""}})}); delete sbTxData().ed[k]; sb.txData = null; await sbTexts(); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* ---- tools: apps list, top-up prices, backups, tables, SQL, stats, service actions */
/* ---- the windows of servers, broadcast and cron: the same design as the Add plan window ---- */
const qpCheck = '<svg viewBox="0 0 16 16"><path d="M3.5 8.5l3 3L12.5 5"/></svg>';
const qpChk = (on, click, label) => `<button type="button" class="qp-chk" role="checkbox" aria-checked="${on}" onclick="${click}"><span class="${on ? "on" : ""}">${on ? qpCheck : ""}</span>${label}</button>`;
Object.assign(sb, {sv: null});
window.sbSrvEdit = id => {
  const s = id ? (sb.servers || []).find(x => +x.id === +id) || {} : {}, has = v => v && v !== "NULL";
  sb.sv = {id: id || 0, title: s.title || "", flag: has(s.flag) ? s.flag : "", remark: has(s.remark) ? s.remark : "", type: s.type || "pasarguard", panel_url: has(s.panel_url) ? s.panel_url : "", username: has(s.username) ? s.username : "", password: "", sni: has(s.sni) ? s.sni : "",
    ucount: String(id ? +s.ucount || 0 : 1000), active: !id || s.active === "1", state: !id || s.state === "1", err: ""};
  svDraw();
};
function svDraw() {
  const f = sb.sv, inp = (k, label, ph = "", mode = "text", extra = "") => `<div class="qp-f ${extra}"><label for="sv-${k}">${label}</label><input id="sv-${k}" class="qp-in" type="${mode === "password" ? "password" : "text"}" ${mode === "num" ? 'inputmode="numeric"' : ""} autocomplete="${mode === "password" ? "new-password" : "off"}" spellcheck="false" placeholder="${esc(ph)}" value="${esc(f[k])}" oninput="sbSv('${k}', this, '${mode}')"></div>`;
  showModal(`<b>${f.id ? "Edit server #" + f.id : "New server"}</b>`, `<form class="qp" onsubmit="return sbSrvSave(event)" novalidate>
      <div class="qp-grid three">${inp("title", "Title", "Germany 1")}${inp("flag", "Flag / emoji", "🇩🇪")}${inp("remark", "Note")}
        <div class="qp-f"><label for="sv-type">Type</label><span class="qp-sw"><select id="sv-type" class="qp-sel" onchange="sb.sv.type=this.value">${[["pasarguard", "PasarGuard"], ["marzban", "Marzban"], ["marzneshin", "Marzneshin"], ["sanaei", "3x-ui (sanaei)"], ["alireza", "x-ui (alireza)"]].map(([v, l]) => `<option value="${v}" ${f.type === v ? "selected" : ""}>${l}</option>`).join("")}</select>${qpChev}</span></div>
        ${inp("panel_url", "Panel address", "https://panel.example.com:8000", "text", "span2")}
        ${inp("username", "Panel user")}${inp("password", "Panel password <small>(empty keeps)</small>", "", "password")}${inp("sni", "SNI <small>(optional)</small>")}
        ${inp("ucount", "Capacity (accounts)", "1000", "num")}<div class="qp-f"><label>&nbsp;</label>${qpChk(f.active, "sb.sv.active=!sb.sv.active;svDraw()", "Active")}</div><div class="qp-f"><label>&nbsp;</label>${qpChk(f.state, "sb.sv.state=!sb.sv.state;svDraw()", "Selling (state)")}</div></div>
      <div class="qp-act"><span class="qp-lerr"><button type="submit" class="qp-pri">${f.id ? "Save" : "Add server"}</button><span role="alert" id="qp-err"></span></span></div></form>`, "plan-add plan-edit");
  sbSvErr(f.err);
}
window.sbSv = (k, el, mode) => { const v = mode === "num" ? sbDigits(el) : el.value; if (v !== el.value) el.value = v; sb.sv[k] = v; sbSvErr(""); };
window.sbSvErr = m => {
  sb.sv.err = m; const e = $("#qp-err"); if (e) e.textContent = m; const key = {Title: "title", Address: "panel_url", User: "username", Password: "password", Capacity: "ucount"}[(m || "").split(" ")[0]];
  document.querySelectorAll("#modal .qp-in").forEach(i => { const bad = !!m && i.id === "sv-" + key; i.classList.toggle("bad", bad); i.setAttribute("aria-invalid", bad); });
};
window.sbSrvSave = async e => {
  e.preventDefault(); const f = sb.sv, fail = m => { sbSvErr(m); return false; };
  if (!f.title.trim()) return fail("Title is needed.");
  if (!/^https?:\/\/\S+$/.test(f.panel_url.trim())) return fail("Address must start with http:// or https://.");
  if (!f.id && !f.username.trim()) return fail("User is needed for a new server.");
  if (!f.id && !f.password) return fail("Password is needed for a new server.");
  if (!(parseInt(f.ucount, 10) >= 0)) return fail("Capacity must be 0 or more.");
  const info = {title: f.title.trim(), flag: f.flag.trim(), remark: f.remark.trim(), ucount: parseInt(f.ucount, 10), active: f.active ? 1 : 0, state: f.state ? 1 : 0}, config = {type: f.type, panel_url: f.panel_url.trim(), username: f.username.trim(), password: f.password, sni: f.sni.trim()};
  try { await api(f.id ? `/api/shopbot/servers/${f.id}` : "/api/shopbot/servers", {method: f.id ? "PUT" : "POST", body: JSON.stringify({info, config})}); closeModal(); toast("✓ Server saved", "ok"); sb.infoAt = 0; await sbServers(); } catch (err) { fail(err.message); } return false;
};
window.sbSrvFromPanel = () => {
  const ps = sb.knownPanels || [];
  showModal("<b>Server from a managed panel</b>", `<form class="qp" onsubmit="return sbSrvFromPanelGo(event)" novalidate><p class="qp-sub">The panel's address, user and password are copied into the bot's database on the bot server.</p>
      <div class="qp-grid"><div class="qp-f" style="grid-column:1/-1"><label for="fp-panel">Panel</label><span class="qp-sw"><select id="fp-panel" class="qp-sel">${ps.map(p => `<option value="${esc(p.id)}">${esc(p.name)} · ${esc(p.type)}</option>`).join("")}</select>${qpChev}</span></div>
        <div class="qp-f"><label for="fp-title">Title in the bot</label><input id="fp-title" class="qp-in" type="text" dir="auto" autocomplete="off" placeholder="same as the panel"></div><div class="qp-f"><label for="fp-ucount">Capacity</label><input id="fp-ucount" class="qp-in" type="text" inputmode="numeric" value="1000" oninput="sbDigits(this)"></div></div>
      <div class="qp-prev"><span role="alert" id="qp-err"></span></div><div class="qp-act"><button type="button" class="qp-ghost" onclick="closeModal()">Cancel</button><button type="submit" class="qp-pri">Add</button></div></form>`, "plan-add cat-add");
};
window.sbSrvFromPanelGo = async e => {
  e.preventDefault(); const panel_id = +$("#fp-panel").value, ucount = parseInt($("#fp-ucount").value, 10) || 0, title = $("#fp-title").value.trim();
  if (!panel_id) { $("#qp-err").textContent = "Pick a panel."; return false; }
  try { await api("/api/shopbot/servers/from-panel", {method: "POST", body: JSON.stringify({panel_id, title, ucount})}); closeModal(); toast("✓ Server added", "ok"); sb.infoAt = 0; await sbServers(); } catch (err) { $("#qp-err").textContent = err.message; } return false;
};

window.sbBcNew = () => {
  const g = (sb.bc || {}).groups || {};
  showModal(`<b>New broadcast</b>`, `<form class="qp" onsubmit="return sbBcSend(event)" novalidate>
      <div class="qp-grid"><div class="qp-f"><label for="bc-title">Title (for this list)</label><input id="bc-title" class="qp-in" name="title" type="text" dir="auto" autocomplete="off" placeholder="October news"></div>
        <div class="qp-f"><label for="bc-group">To</label><span class="qp-sw"><select id="bc-group" class="qp-sel" name="group">${Object.entries(g).map(([v, l]) => `<option value="${esc(v)}" ${v === "all" ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>${qpChev}</span></div></div>
      <div class="qp-f"><label for="bc-text">Message <small>(Telegram HTML allowed: &lt;b&gt;, &lt;i&gt;, &lt;code&gt;)</small></label><textarea id="bc-text" class="qp-ta" name="text" dir="auto" rows="5" placeholder="سلام 👋 …" autofocus></textarea></div>
      <div class="qp-act"><small class="qp-hint">The bot's cron sends it, one user at a time, within minutes.</small><button type="submit" class="qp-pri">📣 Send</button></div></form>`, "plan-add dc-edit");
};

/* ---- the bot's scheduled jobs: one window with every job, how often it runs, and the bot settings it reads ---- */
Object.assign(sb, {cr: null});
const CR_PRESETS = [["* * * * *", "Every minute"], ["*/2 * * * *", "Every 2 minutes"], ["*/5 * * * *", "Every 5 minutes"], ["*/10 * * * *", "Every 10 minutes"], ["*/15 * * * *", "Every 15 minutes"], ["*/30 * * * *", "Every 30 minutes"], ["0 * * * *", "Every hour"], ["0 */6 * * *", "Every 6 hours"], ["35 23 * * *", "Every day at 23:35"], ["5 0 * * *", "Every day at 00:05"]];
window.sbCron = () => sbCronBox();
window.sbCronBox = async () => {
  showModal("<b>Bot scheduled jobs</b>", '<div class="loading"><i class="spin"></i><span>Reading…</span></div>', "plan-add cr-box");
  try {
    const [c, s] = await Promise.all([api("/api/shopbot/cron"), api("/api/shopbot/settings")]), items = {};
    s.groups.forEach(g => g.items.forEach(i => { items[i.key] = {...i, value: i.kind === "onoff" ? (i.value === "on" ? "on" : "off") : String(i.value ?? "")}; }));
    const was = Object.fromEntries(Object.values(items).map(i => [i.key, i.value]));
    sb.cr = {state: c.state, jobs: c.jobs.map(j => ({...j, on: j.enabled})), items, was, vals: {...was}};
    crDraw();
  } catch (e) { showModal("<b>Bot scheduled jobs</b>", `<div class="sb-pempty">${esc(e.message)}</div>`, "plan-add cr-box"); }
};
function crDraw() {
  const c = sb.cr, running = c.state === "on";
  const sched = j => `${CR_PRESETS.map(([v, l]) => `<option value="${v}" ${v === j.schedule ? "selected" : ""}>${l}</option>`).join("")}${CR_PRESETS.some(([v]) => v === j.schedule) ? "" : `<option value="${esc(j.schedule)}" selected>${esc(j.schedule)}</option>`}`;
  const cell = key => { const i = c.items[key]; if (!i) return ""; const v = c.vals[key], k = esc(key);
    return `<div class="cr-s ${v !== c.was[key] ? "chg" : ""}" title="${esc(i.hint || i.label)}"><b>${esc(i.label)}</b>${i.kind === "onoff" ? sbSw(v === "on", `sbCrSw('${k}')`, i.label) : `<input class="cr-in" type="text" ${i.kind === "number" ? 'inputmode="numeric"' : ""} value="${esc(v)}" aria-label="${esc(i.label)}" oninput="sb.cr.vals['${k}']=this.value;this.closest('.cr-s').classList.toggle('chg', this.value !== sb.cr.was['${k}'])" autocomplete="off" spellcheck="false">`}</div>`; };
  showModal("<b>Bot scheduled jobs</b>", `<form class="qp" onsubmit="return sbCronSave(event, false)" novalidate>
      <p class="qp-sub">${running ? "Some jobs run now. Change which ones run, how often, and the bot settings each job reads." : "Nothing runs now. Tick the jobs to start; each one acts on the bot's real data (warnings, auto renew, reports)."}</p>
      <div class="cr-list">${c.jobs.map((j, n) => `<div class="cr-job ${j.on ? "on" : ""}"><div class="cr-h">${qpChk(j.on, `sbCrOn(${n})`, `<b>${esc(j.title)}</b>`)}<span class="qp-sw cr-sch"><select class="qp-sel" aria-label="How often: ${esc(j.title)}" onchange="sb.cr.jobs[${n}].schedule=this.value">${sched(j)}</select>${qpChev}</span></div>
        <p class="cr-d">${esc(j.descr)}</p><div class="cr-set">${(j.settings || []).map(cell).join("")}</div></div>`).join("")}</div>
      <div class="qp-prev"><span role="alert" id="qp-err"></span></div>
      <div class="qp-act">${running ? `<button type="button" class="qp-ghost cr-stop" onclick="sbCronSave(null, true)">Pause all jobs</button>` : "<span></span>"}<span class="cr-btns"><button type="button" class="qp-ghost" onclick="closeModal()">Cancel</button><button type="submit" class="qp-pri">${running ? "Save" : "Start the selected jobs"}</button></span></div></form>`, "plan-add cr-box");
}
window.sbCrOn = n => { sb.cr.jobs[n].on = !sb.cr.jobs[n].on; crDraw(); };
window.sbCrSw = k => { sb.cr.vals[k] = sb.cr.vals[k] === "on" ? "off" : "on"; crDraw(); };
window.sbCronSave = async (e, pauseAll) => {
  if (e) e.preventDefault(); const c = sb.cr, fail = m => { const el = $("#qp-err"); if (el) el.textContent = m; return false; };
  const jobs = c.jobs.map(j => ({key: j.key, schedule: j.schedule, enabled: pauseAll ? false : j.on}));
  if (!pauseAll && c.state !== "on" && !jobs.some(j => j.enabled)) return fail("Tick at least one job to start.");
  const changes = {}; Object.keys(c.vals).forEach(k => { if (c.vals[k] !== c.was[k]) changes[k] = c.vals[k]; });
  try {
    if (!pauseAll && Object.keys(changes).length) await api("/api/shopbot/settings", {method: "PUT", body: JSON.stringify({changes})});
    const r = await api("/api/shopbot/cron", {method: "PUT", body: JSON.stringify({jobs})});
    closeModal(); toast(r.active ? `✓ ${r.active} ${r.active === 1 ? "job runs" : "jobs run"} now` : "✓ All the bot's jobs are paused", "ok"); sb.infoAt = 0; refresh();
  } catch (err) { fail(err.message); } return false;
};
/* ---- tools: Mini App, backups, apps list, top-up prices, tables, SQL, stats; the bot's own actions sit in one menu ---- */
const SB_TOOLS = [["miniapp", "Mini App"], ["backups", "Backups"], ["apps", "Apps list"], ["topups", "Top-up prices"], ["tables", "Tables"], ["sql", "SQL"], ["stats", "Stats"]];
async function sbTools() {
  sbBody(`<div class="sb-card sb-ptool" id="tool-bar"><div class="sb-ptabs">${SB_TOOLS.map(([k, l]) => `<button type="button" class="sb-ptab ${sb.tool === k ? "on" : ""}" data-k="${k}" onclick="sbToolGo('${k}')">${l}</button>`).join("")}</div>
      <details class="sb-menu" id="tool-menu"><summary class="sb-btn sm0">Bot actions<svg viewBox="0 0 16 16"><path d="M3.5 6l4.5 4.5L12.5 6"/></svg></summary><div class="sb-pop"><button type="button" onclick="sbAct('webhook')">Set webhook</button><button type="button" onclick="sbCommands()" title="The / command menu of the bot">Set commands</button><button type="button" onclick="sbAct('restart')">Restart PHP</button><button type="button" onclick="sbAct('backup')">Dump DB</button><button type="button" onclick="sbAct('clearlogs')">Clear logs</button></div></details></div>
    <div id="sb-tool" class="sb-tool"></div>`);
  await sbToolBody();
}
window.sbToolGo = k => { sb.tool = k; document.querySelectorAll("#tool-bar .sb-ptab").forEach(b => b.classList.toggle("on", b.dataset.k === k)); sbToolBody(); };
async function sbToolBody() {
  const m = $("#tool-menu"); if (m) m.open = false;
  sbLR.cur = null; const t = $("#sb-tool"); if (t) t.classList.toggle("free", !["backups", "apps", "tables", "sql"].includes(sb.tool));
  try { await ({miniapp: sbMiniApp, backups: sbBackups, apps: sbApps, topups: sbTopups, tables: sbTableLoad, sql: sbSqlHtml, stats: sbStatsLoad}[sb.tool] || sbMiniApp)(); }
  catch (e) { sbTool(`<div class="sb-card"><div class="sb-pempty">${esc(e.message)}</div></div>`); }
}
const sbTool = html => { const b = $("#sb-tool"); if (b) b.innerHTML = html; };
async function sbMiniApp() {
  const m = await api("/api/shopbot/miniapp");
  const state = !m.installed ? '<span class="sb-pc2 wait">not installed</span>' : m.php_ok === false ? '<span class="sb-pc2 bad">api.php does not parse</span>' : m.up_to_date ? `<span class="sb-pc2 ok">installed · ${esc(m.version || "?")} · up to date</span>` : `<span class="sb-pc2 warn">installed ${esc(m.version || "")} · the panel has ${esc(m.local_version || "a newer version")}</span>`;
  sbTool(`<div class="sb-two"><div class="sb-card sb-ma"><div class="sb-ch"><i class="sb-ci" style="color:#4da3ff">${SI.send}</i><span class="sb-lab">Telegram Mini App</span><span class="sb-ract-r">${state}</span></div>
      <p class="sb-p">The customers' app inside Telegram: services with live usage, buying from the wallet, card-to-card top-up with the receipt sent to you for ✅ / ❌, renew, extra volume and days, tickets, referral link, apps, gift codes and the wheel. It is opened from the bot's <b>Mini App</b> button${m.mini_app_switch === false ? ' — <b class="sb-bad">that button is switched off</b> (Settings → Users → Mini App button)' : ""}.</p>
      ${m.url ? `<div class="sb-kv"><span>Address</span><span class="sb-v"><span class="mono">${esc(m.url)}</span><a class="sb-cp" href="${esc(m.url)}" target="_blank" rel="noopener">Open</a></span></div>` : ""}
      <div class="sb-mrow"><button type="button" class="sb-btn pri sm0" onclick="sbMiniAppDeploy()">${m.installed ? "Update the Mini App" : "Install the Mini App"}</button>${m.installed ? `<span class="sb-hint">puts the panel's version of the four files next to the bot (the old ones are kept as .prev)</span>` : ""}</div>
      ${m.errors ? `<div class="sb-bad sb-p">${m.errors} line(s) in the app's error log · last: <span class="mono">${esc(m.last_error)}</span></div>` : ""}</div>
    <div class="sb-card sb-ma"><div class="sb-ch"><i class="sb-ci" style="color:#d99a52">${SI.doc}</i><span class="sb-lab">Files on the bot server</span></div>
      <div class="sb-frow sb-fh"><span>File</span><span class="r">Size</span><span>Same as the panel's</span></div>${(m.files || []).map(f => `<div class="sb-frow"><span class="mono">${esc(f.name)}</span><span class="r">${f.size ? (f.size / 1024).toFixed(1) + " KB" : "—"}</span><span>${!f.hash ? '<span class="sb-tag bad">missing</span>' : f.current ? '<span class="sb-tag ok">yes</span>' : '<span class="sb-tag warn">no — update</span>'}</span></div>`).join("") || '<div class="sb-p mut">nothing installed yet</div>'}
      <p class="sb-p mut">Requirements on the bot server: PHP 8+, the bot's <span class="mono">baseInfo.php</span> with <span class="mono">$botUrl</span>, and <span class="mono">BOT_STATES.miniAppState = on</span>.</p></div></div>`);
}
window.sbMiniAppDeploy = async () => { if (!await gconfirm("Put the panel's Mini App files (api.php, index.html, app.js, app.css) next to the bot?\n\napi.php is syntax-checked before it replaces the old one; the previous files are kept as .prev.")) return; toast("Installing…"); try { const m = await api("/api/shopbot/miniapp/deploy", {method: "POST"}); toast(m.up_to_date ? "✓ Mini App " + (m.version || "") + " installed" : "✕ Installed, but the files still differ", m.up_to_date ? "ok" : "bad"); sbToolBody(); } catch (e) { toast("✕ " + e.message, "bad"); } };

const BK_COLS = "minmax(260px, 2fr) 110px 190px";
async function sbBackups() {
  const r = await api("/api/shopbot/backups"); sb.bk = r.backups;
  sbTool(`<div class="sb-card sb-ptool"><span class="sb-hint">On the bot server in /opt/nikabot/backups; the live database is dumped before every import as well.</span></div>${sbListCard(BK_COLS, 0, [["Dump"], ["Size", "r"], ["Made"]])}`);
  await sbListInit({cols: BK_COLS, base: 40, key: b => b.name, fetch: sbClient(() => sb.bk || []), empty: () => "No dump yet. Use Bot actions → Dump DB.",
    row: b => `<span class="mono sb-cut" title="${esc(b.name)}">${esc(b.name)}</span><span class="r">${(b.size / 1024).toFixed(0)} KB</span><span class="sb-cut">${b.at ? sbDateTime(b.at) : "—"}</span>`});
}

const AP_COLS = "minmax(160px, 1fr) minmax(260px, 2.4fr) 92px 150px";
async function sbApps() {
  const r = await api("/api/shopbot/apps"); sb.apps = r.apps;
  sbTool(`<div class="sb-card sb-ptool"><button type="button" class="sb-btn pri sm0" onclick="sbAppEdit(null)">${sbPlusIc}Add app</button><span class="sb-hint">the download links the bot offers under “apps”</span></div>${sbListCard(AP_COLS, 0, [["Title"], ["Link"], ["On"], ["Action", "r"]])}`);
  await sbListInit({cols: AP_COLS, base: 44, fetch: sbClient(() => sb.apps || []), empty: () => "No app link yet.", cls: a => a.status === "1" ? "" : "dim",
    row: (a, ask) => { const on = a.status === "1", first = `<span class="sb-cut" dir="auto" title="${esc(a.title)}"><b>${esc(a.title)}</b></span>`;
      if (ask) return sbAskRow(first, "Delete this app link?", 4, `<button type="button" class="sb-btn sm0" onclick="sbAsk(0)">Keep</button><button type="button" class="sb-btn red sm0" onclick="sbAppDel(${a.id})">Delete</button>`);
      return `${first}<span class="mono sb-cut" title="${esc(a.link)}">${esc(a.link)}</span><span>${sbSw(on, `sbAppToggle(${a.id}, ${on ? 0 : 1})`, `${on ? "Hide" : "Show"} ${a.title}`)}</span><span class="sb-pact"><button type="button" class="sb-btn sm0" onclick="sbAppEdit(${a.id})">Edit</button><button type="button" class="sb-btn dec sm0" onclick="sbAsk(${a.id})">Delete</button></span>`; }});
}
window.sbAppToggle = async (id, on) => { const a = (sb.apps || []).find(x => +x.id === +id); if (!a) return; try { await api(`/api/shopbot/apps/${id}`, {method: "PUT", body: JSON.stringify({title: a.title, link: a.link, status: !!on})}); a.status = on ? "1" : "0"; sbListDraw(); toast(on ? "✓ Shown in the bot" : "✓ Hidden from the bot", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.sbAppEdit = id => {
  const a = id ? (sb.apps || []).find(x => +x.id === +id) || {} : {};
  showModal(`<b>${id ? "Edit app" : "New app"}</b>`, `<form class="qp" onsubmit="return sbAppSave(event, ${id || 0})" novalidate><div class="qp-grid one"><div class="qp-f"><label for="ap-title">Title</label><input id="ap-title" class="qp-in" type="text" dir="auto" autocomplete="off" placeholder="v2rayNG (Android)" value="${esc(a.title || "")}" autofocus></div>
      <div class="qp-f"><label for="ap-link">Link</label><input id="ap-link" class="qp-in" type="text" autocomplete="off" spellcheck="false" placeholder="https://…" value="${esc(a.link || "")}"></div></div>
      <div class="qp-prev"><button type="button" class="qp-chk" role="checkbox" aria-checked="${!id || a.status === "1"}" id="ap-on" data-on="${!id || a.status === "1" ? 1 : 0}" onclick="sbAppOn(this)"><span class="${!id || a.status === "1" ? "on" : ""}">${!id || a.status === "1" ? '<svg viewBox="0 0 16 16"><path d="M3.5 8.5l3 3L12.5 5"/></svg>' : ""}</span>Shown in the bot</button><span role="alert" id="qp-err"></span></div>
      <div class="qp-act"><button type="button" class="qp-ghost" onclick="closeModal()">Cancel</button><button type="submit" class="qp-pri">${id ? "Save changes" : "Add"}</button></div></form>`, "plan-add cat-add");
};
window.sbAppOn = b => { const on = b.dataset.on !== "1"; b.dataset.on = on ? 1 : 0; b.setAttribute("aria-checked", on); const s = b.querySelector("span"); s.className = on ? "on" : ""; s.innerHTML = on ? '<svg viewBox="0 0 16 16"><path d="M3.5 8.5l3 3L12.5 5"/></svg>' : ""; };
window.sbAppSave = async (e, id) => {
  e.preventDefault(); const title = $("#ap-title").value.trim(), link = $("#ap-link").value.trim(), fail = m => { $("#qp-err").textContent = m; return false; };
  if (!title) return fail("Type a title.");
  if (!/^https?:\/\//.test(link)) return fail("The link starts with http:// or https://.");
  try { await api(id ? `/api/shopbot/apps/${id}` : "/api/shopbot/apps", {method: id ? "PUT" : "POST", body: JSON.stringify({title, link, status: $("#ap-on").dataset.on === "1"})}); closeModal(); toast("✓ Saved", "ok"); await sbApps(); } catch (err) { fail(err.message); } return false;
};
window.sbAppDel = async id => { try { await api(`/api/shopbot/apps/${id}`, {method: "DELETE"}); sb.apps = (sb.apps || []).filter(x => +x.id !== +id); toast("✓ Deleted", "ok"); sbListLoad(); } catch (e) { toast("✕ " + e.message, "bad"); sbAsk(0); } };

/* top-up prices: two short lists side by side */
Object.assign(sb, {tp: null, tpAsk: ""});
async function sbTopups() {
  sb.tp = await api("/api/shopbot/topups"); sb.tpAsk = "";
  sbTool(`<div class="sb-two" id="tp-wrap"></div>`); sbTpDraw();
}
function sbTpDraw() {
  const r = sb.tp, card = (kind, rows, unit) => `<div class="sb-card sb-tpc"><div class="sb-ch"><span class="sb-lab">Extra ${unit}</span><span class="sb-pc">${rows.length}</span><button type="button" class="sb-btn pri sm0" onclick="sbTopupNew('${kind}')">${sbPlusIc}Add</button></div>
    <div class="tp-list">${rows.map(x => { const k = kind + ":" + x.id, ask = sb.tpAsk === k;
      return `<div class="tp-row ${ask ? "ask-no" : ""}"><b>${esc(kind === "volume" ? x.volume : x.days)} ${unit}</b>${ask ? `<span class="sb-pask">Delete this price?</span><span class="sb-pact"><button type="button" class="sb-btn sm0" onclick="sbTpAsk('')">Keep</button><button type="button" class="sb-btn red sm0" onclick="sbTopupDel('${kind}', ${x.id})">Delete</button></span>`
        : `<span class="sb-pa"><b>${fmt(x.price)}</b> <em>T</em></span><span class="sb-pact"><button type="button" class="sb-btn dec sm0" onclick="sbTpAsk('${k}')">Delete</button></span>`}</div>`; }).join("") || '<div class="sb-pempty">None yet.</div>'}</div></div>`;
  const w = $("#tp-wrap"); if (w) w.innerHTML = card("volume", r.volume, "GB") + card("days", r.days, "days");
}
window.sbTpAsk = k => { sb.tpAsk = k; sbTpDraw(); };
window.sbTopupNew = kind => {
  showModal(`<b>${kind === "volume" ? "New extra volume price" : "New extra days price"}</b>`, `<form class="qp" onsubmit="return sbTopupSave(event, '${kind}')" novalidate><div class="qp-grid"><div class="qp-f"><label for="tp-amount">${kind === "volume" ? "How many GB?" : "How many days?"}</label><input id="tp-amount" class="qp-in" type="text" inputmode="numeric" autocomplete="off" value="${kind === "volume" ? "10" : "30"}" oninput="sbDigits(this)" autofocus></div>
      <div class="qp-f"><label for="tp-price">Price (Toman)</label><input id="tp-price" class="qp-in" type="text" inputmode="numeric" autocomplete="off" placeholder="50000" oninput="sbDigits(this)"></div></div>
      <div class="qp-prev"><span role="alert" id="qp-err"></span></div><div class="qp-act"><button type="button" class="qp-ghost" onclick="closeModal()">Cancel</button><button type="submit" class="qp-pri">Add</button></div></form>`, "plan-add cat-add");
};
window.sbTopupSave = async (e, kind) => {
  e.preventDefault(); const amount = parseFloat($("#tp-amount").value), price = parseInt($("#tp-price").value, 10), fail = m => { $("#qp-err").textContent = m; return false; };
  if (!(amount > 0)) return fail("The amount must be more than 0.");
  if (!(price > 0)) return fail("Price must be more than 0.");
  try { await api("/api/shopbot/topups", {method: "POST", body: JSON.stringify({kind, price, amount})}); closeModal(); toast("✓ Added", "ok"); await sbTopups(); } catch (err) { fail(err.message); } return false;
};
window.sbTopupDel = async (kind, id) => { try { await api(`/api/shopbot/topups/${kind}/${id}`, {method: "DELETE"}); toast("✓ Deleted", "ok"); await sbTopups(); } catch (e) { toast("✕ " + e.message, "bad"); sbTpAsk(""); } };

/* tables and SQL: the columns come with the data, so the header is drawn from the answer */
const SB_TABLES = ["users", "orders_list", "pays", "server_plans", "server_config", "server_categories", "discounts", "setting", "send_list", "add_link", "increase_order", "gift_codes", "wheel_log", "cashback_log"];
const sbDynCols = n => n ? Array(n).fill("minmax(110px, 1fr)").join(" ") : "1fr";
const sbDynRow = x => ((sbLR.cur && sbLR.cur.columns) || []).map(c => `<span class="sb-cut mono" title="${esc(x[c] ?? "")}">${x[c] == null || x[c] === "NULL" ? '<span class="mut">NULL</span>' : esc(x[c])}</span>`).join("");
async function sbTableLoad() {
  sbTool(`<div class="sb-card sb-ptool"><select class="sb-sel" aria-label="Table" onchange="sbTblPick(this.value)">${SB_TABLES.map(x => `<option ${x === sb.table ? "selected" : ""}>${x}</option>`).join("")}</select>
      <label class="sb-psearch"><i>${SI.search}</i><input type="text" id="sb-tq" placeholder="filter…" value="${esc(sb.tq)}" oninput="sbTQ(this.value)" autocomplete="off" spellcheck="false"></label></div>${sbListCard("1fr", 0, [[""]])}`);
  await sbListInit({cols: "1fr", base: 38, empty: () => sb.tq ? `No row matches “${esc(sb.tq)}”.` : "The table is empty.", row: sbDynRow,
    fetch: async (page, ps) => { const t = await api(`/api/shopbot/table/${sb.table}?page=${page}&q=${encodeURIComponent(sb.tq)}&limit=${ps}`); return {items: t.rows, total: t.total, cols: sbDynCols(t.columns.length), columns: t.columns, heads: t.columns.map(c => [esc(c)])}; }});
}
window.sbTblPick = v => { sb.table = v; sb.tq = ""; const i = $("#sb-tq"); if (i) i.value = ""; sbListLoad(0); };
window.sbTQ = v => { sb.tq = v.trim(); clearTimeout(qT); qT = setTimeout(() => sbListLoad(0), 300); };
function sbSqlHtml() {
  const r = sb.qres;
  sbTool(`<div class="sb-card sb-sqlbox"><textarea id="sb-sql" class="st-ta mono" rows="3" spellcheck="false" placeholder='SELECT userid, name, wallet FROM users WHERE wallet > 100000 ORDER BY wallet DESC'>${esc(sb.sql)}</textarea><div class="sb-sqlact"><button type="button" class="sb-btn pri sm0" onclick="sbRun()">Run</button><span class="sb-hint">Read-only: one SELECT or SHOW; string literals in double quotes; 200 rows max</span></div></div>${sbListCard("1fr", 0, [[""]])}`);
  sbListInit({cols: "1fr", base: 38, row: sbDynRow, empty: () => r ? (r.error ? esc(r.error) : "No rows.") : "Write a query and press Run.",
    fetch: async (page, ps) => { if (!r || r.error) return {items: [], total: 0}; return {items: r.rows.slice(page * ps, page * ps + ps), total: r.rows.length, cols: sbDynCols(r.columns.length), columns: r.columns, heads: r.columns.map(c => [esc(c)])}; }});
}
window.sbRun = async () => { sb.sql = $("#sb-sql").value; try { sb.qres = await api("/api/shopbot/query", {method: "POST", body: JSON.stringify({sql: sb.sql})}); } catch (e) { sb.qres = {error: e.message}; } sbSqlHtml(); };

async function sbStatsLoad() {
  const s = await api("/api/shopbot/stats"), list = (title, rows, f) => `<div class="sb-card"><div class="sb-ch"><span class="sb-lab">${title}</span></div>${rows.slice(0, 8).map(r => `<div class="sb-srow">${f(r)}</div>`).join("") || '<div class="sb-p mut">nothing</div>'}</div>`;
  sbTool(`<div class="sb-card"><div class="sb-ch"><span class="sb-lab">Income per day, 30 days</span><span class="sb-ract-r sb-hint">${s.daily.length ? s.daily[0].day + " → " + s.daily[s.daily.length - 1].day : ""}</span></div>${spark(s.daily.map(d => +d.income / 1000), {fmt: v => v.toFixed(0) + " k"})}</div>
    <div class="sb-two">${list("Top buyers", s.top_buyers, r => `<b dir="auto">${esc(r.name || r.userid)}</b><small>${r.orders} orders</small><span class="sb-pa"><b>${fmt(r.spent)}</b> <em>T</em></span>`)}${list("Biggest wallets", s.top_wallets, r => `<b dir="auto">${esc(r.name || r.userid)}</b><small></small><span class="sb-pa"><b>${fmt(r.wallet)}</b> <em>T</em></span>`)}</div>`);
}
window.sbAct = async act => { const msgs = {webhook: "Point the Telegram webhook at the bot's public URL?", restart: "Restart PHP on the bot server?", backup: "Dump the bot's database to /opt/nikabot/backups?", clearlogs: "Clear the bot's log files?"}; const m = $("#tool-menu"); if (m) m.open = false; if (!await gconfirm(msgs[act])) return; toast("Working…"); try { const r = await api(`/api/shopbot/action/${act}`, {method: "POST"}); toast((r.done ? "✓ " : "✕ ") + (r.message || act), r.done ? "ok" : "bad"); sb.infoAt = 0; if ($("#sb-tool")) sbToolBody(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.sbCommands = async () => { const m = $("#tool-menu"); if (m) m.open = false; if (!await gconfirm("Set the bot's / command menu (start, buy, mysubs, wallet, support, help)?")) return; try { const r = await api("/api/shopbot/commands", {method: "POST"}); toast("✓ Commands set: " + r.commands.join(", "), "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.sbImport = async () => { if (!await gconfirm("Import the real database over the live one?\n\nThe live database is dumped first. Users, orders, payments, plans, panels and settings are replaced; broadcast queues are not copied. The bot then serves the real customers.")) return; toast("Dumping and importing… (1–3 minutes)"); try { const r = await api("/api/shopbot/import-stage", {method: "POST"}); toast(`✓ Imported: ${r.tables.users} users, ${r.tables.orders_list} orders · backup ${r.backup.split("/").pop()}`, "ok"); sb.infoAt = 0; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };

/* ---- gateway (Xray on an Iran server, borrowed configs for internet cuts) */
INFO.gateway = ["Outbound", INFO_GW];
PAGES.gateway = async () => {
  for (let i = 0; i < 100 && typeof obRender !== "function"; i++) await new Promise(r => setTimeout(r, 40));       // outbound.js is the last script of the page: on a hard reload the first render can come before it has run
  return obRender("");       // outbound.js: the pool of exits, the relay, the Iran servers that carry it, the forwards, the users
};

/* ---- events */
PAGES.events = async () => {
  const ev = await api("/api/events?limit=" + state.actLimit);
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
      <td class="mut" title="${esc(x.last_error || (x.last_failure ? "last failure " + ago(x.last_failure_at) + " ago: " + x.last_failure : ""))}">${esc(x.last_error || (x.last_failure && x.failures ? "last failure " + ago(x.last_failure_at) + " ago: " + x.last_failure : "")).slice(0, 60)} ${x.failures ? `<span class="pill">${x.failures}/${x.runs} failed</span>` : ""}</td>
      <td><button class="sm" onclick="runJob('${esc(x.name)}')">Run now</button> <button class="sm" onclick="jobEnabled('${esc(x.name)}', ${!x.enabled})">${x.enabled ? "Pause" : "Resume"}</button></td></tr>`).join("")}</tbody></table>`;
};
window.runJob = async n => { try { await api(`/api/jobs/${n}/run`, {method: "POST"}); toast("✓ " + n + " runs now", "ok"); setTimeout(refresh, 1500); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.jobEnabled = async (n, on) => { try { await api(`/api/jobs/${n}/enabled?enabled=${on}`, {method: "POST"}); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };

/* ---- logs */
PAGES.logs = async () => {
  const L = state.logs;
  const [d, sm] = await Promise.all([api(`/api/logs?lines=400&level=${encodeURIComponent(L.level)}&domain=${encodeURIComponent(L.domain)}&q=${encodeURIComponent(L.q)}&origin=${encodeURIComponent(L.origin || "")}`), api("/api/logs/summary?hours=24").catch(() => null)]);
  const domains = ["", "http", "auth", "servers", "tunnels", "ssh", "jobs", "db", "events", "import", "configs", "alerts", "underuse", "analyst", "housekeeping"];
  const OR = {panel: "The panel itself", server: "A service on your server", outside: "Outside the panel", unknown: "Not determined"};
  const orChip = o => o ? `<span class="tk-or o-${esc(o)}" title="${esc(OR[o] || "")}">${esc(({panel: "panel", server: "your server", outside: "outside", unknown: "?"})[o] || o)}</span>` : "";
  const sum = sm && sm.total ? `<div class="card log-sum"><h3>What went wrong in the last 24 hours <span class="sp"></span><small class="mut">${sm.total} warning${sm.total === 1 ? "" : "s"} and errors, grouped by where the problem is</small></h3>
      <div class="ls-chips">${["panel", "server", "outside", "unknown"].map(o => `<button type="button" class="ls-chip o-${o} ${L.origin === o ? "on" : ""}" onclick="setLog('origin', '${L.origin === o ? "" : o}')"><b>${sm.by_origin[o] || 0}</b><span>${OR[o]}</span></button>`).join("")}</div>
      <div class="ls-list">${sm.groups.slice(0, 6).map(g => `<div class="ls-row"><span class="ls-n">${g.count}×</span>${orChip(g.origin)}<span class="ls-t" title="${esc(g.sample)}">${esc(g.sample)}</span><a class="mut" onclick="state.logs.q = ${esc(JSON.stringify(g.sample.slice(0, 40)))}; refresh()">show</a><small class="mut ls-h">${esc(g.hint)}</small></div>`).join("")}</div></div>` : `<div class="card log-sum"><h3>✓ Nothing went wrong in the last 24 hours</h3></div>`;
  $("#page").innerHTML = `${sum}<div class="toolbar">
      <select onchange="setLog('level', this.value)">${["", "INFO", "WARNING", "ERROR", "DEBUG"].map(l => `<option value="${l}" ${L.level === l ? "selected" : ""}>${l || "all levels"}</option>`).join("")}</select>
      <select onchange="setLog('domain', this.value)">${domains.map(x => `<option value="${x}" ${L.domain === x ? "selected" : ""}>${x || "all domains"}</option>`).join("")}</select>
      <select onchange="setLog('origin', this.value)" title="Where the problem is">${[["", "everywhere"], ["panel", "the panel itself"], ["server", "a service on your server"], ["outside", "outside the panel"], ["unknown", "not determined"]].map(([v, l]) => `<option value="${v}" ${(L.origin || "") === v ? "selected" : ""}>${l}</option>`).join("")}</select>
      <input type="search" id="lq" placeholder="text or request id…" value="${esc(L.q)}" oninput="setLogQ(this.value)"><span class="sp"></span><span class="mut">${d.lines.length} lines</span></div>
    <div class="card log">${d.lines.map(l => `<div class="ll ${l.level}"><time>${esc(l.ts.slice(11, 19))}</time><b>${esc(l.level)}</b><i>${esc(l.domain)}</i><span>${esc(l.text)}</span>${orChip(l.origin) || "<em></em>"}<small>${esc(l.rid)}</small></div>`).join("") || '<div class="empty">No matching log line.</div>'}</div>`;
  const q = $("#lq"); if (q && L.q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
};
window.setLog = (k, v) => { state.logs[k] = v; refresh(); };
let lqT; window.setLogQ = v => { state.logs.q = v.trim(); clearTimeout(lqT); lqT = setTimeout(refresh, 250); };

/* ---- settings */
/* ---- Settings: one frame per tab, as wide as the row of tabs; inside, grouped lists like a phone's settings:
   a group = header (icon, name, state) + rows (label left, control right) + an action row. The groups fill two columns with no gaps. */
const SI = {   // line icons
  bell: '<svg viewBox="0 0 24 24"><path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/></svg>',
  send: '<svg viewBox="0 0 24 24"><path d="M21 4L3 11l6 2.5L11.5 20l3-4.5L19 18z"/><path d="M9 13.5L21 4"/></svg>',
  doc: '<svg viewBox="0 0 24 24"><rect x="4" y="3.5" width="16" height="17" rx="2.4"/><path d="M8 8.5h8M8 12h8M8 15.5h5"/></svg>',
  rules: '<svg viewBox="0 0 24 24"><path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2.2"/><circle cx="8" cy="17" r="2.2"/></svg>',
  clock: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg>',
  probe: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="2.2"/><path d="M7.8 7.8a6 6 0 0 0 0 8.4M16.2 7.8a6 6 0 0 1 0 8.4M5 5a10 10 0 0 0 0 14M19 5a10 10 0 0 1 0 14"/></svg>',
  down: '<svg viewBox="0 0 24 24"><path d="M12 4v11M7.5 10.5L12 15l4.5-4.5M5 19h14"/></svg>',
  up: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M12 16.5v-9M8.5 11l3.5-3.5 3.5 3.5"/></svg>',
  bot: '<svg viewBox="0 0 24 24"><rect x="4" y="7" width="16" height="12" rx="3"/><path d="M12 4v3M9 13h.5M14.5 13h.5"/></svg>',
  cart: '<svg viewBox="0 0 24 24"><path d="M3 4h2.5l2 11h10l2-7H7"/><circle cx="9.5" cy="19" r="1.3"/><circle cx="16.5" cy="19" r="1.3"/></svg>',
  key: '<svg viewBox="0 0 24 24"><circle cx="8" cy="15" r="3.6"/><path d="M10.7 12.6L19 4.5M15.5 8l2.5 2.5M13.2 10.3l2 2"/></svg>',
  box: '<svg viewBox="0 0 24 24"><path d="M4 8l8-4 8 4v8l-8 4-8-4z"/><path d="M4 8l8 4 8-4M12 12v8"/></svg>',
  save: '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="2.5"/><path d="M8 4v5h8V4M8 20v-6h8v6"/></svg>',
  paint: '<svg viewBox="0 0 24 24"><path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.2 0 1.8-.8 1.8-1.6 0-.9-.6-1.2-.6-2 0-.9.7-1.4 1.6-1.4h1.7a4 4 0 0 0 4-4C20.5 7 16.7 3.5 12 3.5z"/><circle cx="8" cy="11" r="1.1"/><circle cx="10.5" cy="7.5" r="1.1"/><circle cx="15" cy="7.5" r="1.1"/></svg>',
  cpu: '<svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3"/></svg>',
  gear: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8"/></svg>',
  cog: '<svg viewBox="0 0 24 24"><path d="M10.21 5.34L10.55 2.81L13.45 2.81L13.79 5.34A6.9 6.9 0 0 1 15.45 6.02L17.47 4.48L19.52 6.53L17.98 8.55A6.9 6.9 0 0 1 18.66 10.21L21.19 10.55L21.19 13.45L18.66 13.79A6.9 6.9 0 0 1 17.98 15.45L19.52 17.47L17.47 19.52L15.45 17.98A6.9 6.9 0 0 1 13.79 18.66L13.45 21.19L10.55 21.19L10.21 18.66A6.9 6.9 0 0 1 8.55 17.98L6.53 19.52L4.48 17.47L6.02 15.45A6.9 6.9 0 0 1 5.34 13.79L2.81 13.45L2.81 10.55L5.34 10.21A6.9 6.9 0 0 1 6.02 8.55L4.48 6.53L6.53 4.48L8.55 6.02A6.9 6.9 0 0 1 10.21 5.34Z"/><circle cx="12" cy="12" r="2.7"/></svg>',      // a cog wheel (eight teeth): the General tab of the Panel page
  shield: on => `<svg viewBox="0 0 24 24"><path d="M12 3l7 3v5c0 4.6-3 8-7 10-4-2-7-5.4-7-10V6z"/>${on ? '<path d="M8.6 12.2l2.4 2.4 4.4-4.6"/>' : '<path d="M12 8.2v4.4M12 15.6v.4"/>'}</svg>`,
  db: '<svg viewBox="0 0 24 24"><ellipse cx="12" cy="6" rx="7" ry="2.8"/><path d="M5 6v6c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8V6M5 12v6c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8v-6"/></svg>',
  cloud: '<svg viewBox="0 0 24 24"><path d="M7 18a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 9.5 4 4 0 0 1 17.5 18"/><path d="M12 12v8M9 15l3-3 3 3"/></svg>',
  restore: '<svg viewBox="0 0 24 24"><path d="M5 12a7 7 0 1 0 2.1-5"/><path d="M5 4v4h4"/></svg>',
  folder: '<svg viewBox="0 0 24 24"><path d="M3.5 7.5A1.5 1.5 0 0 1 5 6h4l2 2.5h8a1.5 1.5 0 0 1 1.5 1.5v7.5A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5z"/></svg>',
  link: '<svg viewBox="0 0 24 24"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/></svg>',
  bolt: '<svg viewBox="0 0 24 24"><path d="M13 3L5 13.5h6L10 21l9-11.5h-6z"/></svg>',
  arrowUp: '<svg viewBox="0 0 24 24"><path d="M12 19V5M6 11l6-6 6 6"/></svg>',
  moon: '<svg viewBox="0 0 24 24"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/></svg>',
  aa: '<svg viewBox="0 0 24 24"><path d="M3 18l5-12 5 12M5 14h6M15 18l3.5-8 3.5 8M16.3 15.5h4.4"/></svg>',
  pulse: '<svg viewBox="0 0 24 24"><path d="M3 12h4l2.5-6 4 12 2.5-6H21"/></svg>',
  reset: '<svg viewBox="0 0 24 24"><path d="M19 12a7 7 0 1 1-2.1-5"/><path d="M19 4v4h-4"/></svg>',
  server: '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="6.5" rx="2"/><rect x="4" y="13.5" width="16" height="6.5" rx="2"/><path d="M8 7.2h.01M8 16.7h.01"/></svg>',
  play: '<svg viewBox="0 0 24 24"><path d="M7 5l12 7-12 7z"/></svg>',  user: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8.5" r="3.6"/><path d="M5 20c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5"/></svg>',
  hash: '<svg viewBox="0 0 24 24"><path d="M9 4L7.5 20M16.5 4L15 20M4.5 9h15.5M4 15h15.5"/></svg>',
  file: '<svg viewBox="0 0 24 24"><path d="M6 3.5h8l4 4V20a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1z"/><path d="M14 3.5V8h4.5"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l.8 12.5h9.4L17.5 7M10 11v5M14 11v5"/></svg>',
  cube: '<svg viewBox="0 0 24 24"><path d="M12 3l8 4.2v9.6L12 21l-8-4.2V7.2z"/><path d="M4 7.2l8 4.3 8-4.3M12 11.5V21"/></svg>',  swap: '<svg viewBox="0 0 24 24"><path d="M4 8h13m0 0-3-3m3 3-3 3M20 16H7m0 0 3-3m-3 3 3 3"/></svg>',
  power: '<svg viewBox="0 0 24 24"><path d="M12 3.5v8M7.2 6.8a7.5 7.5 0 1 0 9.6 0"/></svg>',
  term: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M7 10l3 2-3 2M12.5 15h4"/></svg>',
  edit: '<svg viewBox="0 0 24 24"><path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17z"/><path d="M13.5 8l3 3"/></svg>',
  bars: '<svg viewBox="0 0 24 24"><path d="M6 20v-8M12 20V5M18 20v-5" stroke-width="2.6"/></svg>',
  ram: '<svg viewBox="0 0 24 24"><rect x="3" y="7" width="18" height="9" rx="2"/><path d="M7 16v2.5M11 16v2.5M15 16v2.5M7.5 10.5h.01M12 10.5h.01M16.5 10.5h.01"/></svg>',
  users: '<svg viewBox="0 0 24 24"><circle cx="9" cy="9" r="3"/><path d="M3.5 19c.6-3 2.8-4.6 5.5-4.6s4.9 1.6 5.5 4.6M16 6.4a3 3 0 0 1 0 5.2M17.5 14.6c1.8.5 3 1.9 3.4 4.4"/></svg>',  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
  vgrid: '<svg viewBox="0 0 16 16"><path d="M3 3h4v4H3zM9 3h4v4H9zM3 9h4v4H3zM9 9h4v4H9z"/></svg>',
  vcompact: '<svg viewBox="0 0 16 16"><path d="M2.5 4h11M2.5 8h11M2.5 12h11"/></svg>',
  vlist: '<svg viewBox="0 0 16 16"><path d="M5.5 4h8M5.5 8h8M5.5 12h8M2.5 4v.01M2.5 8v.01M2.5 12v.01"/></svg>',  home: '<svg viewBox="0 0 24 24"><path d="M4 11l8-7 8 7v8.5a1 1 0 0 1-1 1h-4.5v-6h-5v6H5a1 1 0 0 1-1-1z"/></svg>',
  card: '<svg viewBox="0 0 24 24"><rect x="3" y="5.5" width="18" height="13" rx="2.5"/><path d="M3 10h18M7 15h3"/></svg>',
  tag: '<svg viewBox="0 0 24 24"><path d="M3.5 12.2V4.5h7.7l9.3 9.3a1.4 1.4 0 0 1 0 2l-5.7 5.7a1.4 1.4 0 0 1-2 0z"/><circle cx="8" cy="9" r="1.2"/></svg>',
  gift: '<svg viewBox="0 0 24 24"><rect x="3.5" y="9" width="17" height="11" rx="1.8"/><path d="M12 9v11M3.5 13h17M12 9c-2.5 0-4.5-.8-4.5-2.7S10 3.5 12 7c2-3.5 4.5-1.6 4.5-.7S14.500 9 12 9z"/></svg>',
  megaphone: '<svg viewBox="0 0 24 24"><path d="M4 10v4h3l8 4.5v-13L7 10zM18.500 9.500a3.500 3.500 0 0 1 0 5"/><path d="M7 14l1.200 4.500"/></svg>',
  ticket: '<svg viewBox="0 0 24 24"><path d="M4 7.5h16v3a1.8 1.8 0 0 0 0 3.500V17H4v-3a1.8 1.8 0 0 0 0-3.500z"/><path d="M14 7.500v9" stroke-dasharray="1.800 2"/></svg>',
  textt: '<svg viewBox="0 0 24 24"><path d="M5 6.500h14M12 6.500V19M9 19h6"/></svg>',
  wrench: '<svg viewBox="0 0 24 24"><path d="M14.500 6.500a4 4 0 0 0-5 5L4 17l3 3 5.500-5.500a4 4 0 0 0 5-5l-2.500 2.500-2.500-.5-.5-2.500z"/></svg>',
  gridi: '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="6.500" height="6.500" rx="1.600"/><rect x="13.500" y="4" width="6.500" height="6.500" rx="1.600"/><rect x="4" y="13.500" width="6.500" height="6.500" rx="1.600"/><rect x="13.500" y="13.500" width="6.500" height="6.500" rx="1.600"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="M5 12.500l4.500 4.500L19 7.500"/></svg>',  partner: '<svg viewBox="0 0 24 24"><rect x="3.5" y="7.5" width="17" height="12" rx="2.2"/><path d="M9 7.5V6a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 6v1.5M3.5 12.5h17M10.5 12.5v1.500h3v-1.500"/></svg>',
  search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.500"/><path d="M16 16l4.500 4.500"/></svg>',
  buoy: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3.4"/><path d="M6 6l3.6 3.6M18 6l-3.6 3.6M6 18l3.6-3.6M18 18l-3.6-3.6"/></svg>',
  lock: on => `<svg viewBox="0 0 24 24"><rect x="5" y="10.5" width="14" height="10" rx="2.4"/><path d="${on ? "M8.2 10.5V8a3.8 3.8 0 0 1 7.6 0v2.5" : "M8.2 10.5V8a3.8 3.8 0 0 1 7.2-1.7"}"/><path d="M12 14.6v2.2"/></svg>`,
};
/* the building blocks */
const tip = t => t ? ` title="${String(t).replace(/<[^>]*>/g, "").replace(/"/g, "&quot;")}"` : "";      // the explanation of a row is shown only when the mouse rests on it
const sGrp = (o) => `<${o.form ? `form class="ss ${o.box ? "" : "flat"} ${o.cls || ""}" onsubmit="${o.form}"` : `div class="ss ${o.box ? "" : "flat"} ${o.cls || ""}"`}><div class="ss-h ${o.ok === true ? "ok" : o.ok === false ? "off" : ""}"${tip(o.desc)}><i class="ss-ic">${o.icon}</i>${o.sub ? `<div class="ss-hb"><div class="ss-h1"><b>${o.title}</b>${o.pill || ""}</div><small>${o.sub}</small></div>` : `<b>${o.title}</b>${o.pill || ""}`}</div><div class="ss-rows">${o.rows}</div>${o.acts ? `<div class="ss-a">${o.acts}</div>` : ""}</${o.form ? "form" : "div"}>`;
const sRow = (label, control, hint = "") => `<label class="sr"${tip(hint)}><span>${label}</span><div class="v">${control}</div></label>`;
const sText = (label, text, hint = "") => `<div class="sr"${tip(hint)}><span>${label}</span><div class="v t">${text}</div></div>`;
const sSw = (name, on, text, hint = "", extra = "") => `<label class="sr"${tip(hint)}><span>${text}</span><div class="v"><input type="checkbox" class="sw" name="${name}" ${on ? "checked" : ""} ${extra}></div></label>`;
const sItem = o => `<div class="si ${o.ok === true ? "ok" : o.ok === false ? "off" : ""} ${o.chev ? "chev" : ""}"><i class="ss-ic">${o.icon}</i><div class="si-b"><div class="si-t"><span class="si-n"><b>${o.title}</b>${o.pill || ""}</span>${o.desc ? `<small>${o.desc}</small>` : ""}</div>${o.field ? `<div class="si-r ${o.cc || ""}">${o.field}${o.acts ? `<span class="si-a">${o.acts}</span>` : ""}</div>` : ""}</div>${!o.field && o.acts ? `<span class="si-a si-x">${o.acts}</span>` : ""}</div>`;      // an item with no text box has its buttons right after its name
const EYE = '<svg viewBox="0 0 24 24"><path class="open" d="M2.8 12.2C4.6 8.3 8 6 12 6s7.4 2.3 9.2 6.2c-1.8 3.9-5.2 6.2-9.2 6.2s-7.4-2.3-9.2-6.2z"/><circle class="open" cx="12" cy="12.2" r="2.9"/><path class="shut" d="M3.5 5.5l17 13"/></svg>';
const pwBox = (cls, name, ph, ac, extra = "") => `<span class="pwi"><i class="pwl">${SI.lock(false)}</i><input class="si-f ${cls}" name="${name}" type="password" required placeholder="${ph}" autocomplete="${ac}"${extra}><button type="button" class="eye" aria-label="Show or hide the password" onclick="pwToggle(this)" tabindex="-1">${EYE}</button></span>`;
window.pwToggle = b => { const i = b.previousElementSibling; i.type = i.type === "password" ? "text" : "password"; b.classList.toggle("on", i.type === "text"); };
const sIc = (ic, sel) => `<span class="sic">${ic}${sel}</span>`;      // a select with its own small icon
const fIc = (ic, inner, eye) => `<span class="fic ${eye ? "pwi" : ""}"><i class="fi-i">${ic}</i>${inner}${eye ? `<button type="button" class="eye" aria-label="Show or hide the password" onclick="pwToggle(this)" tabindex="-1">${EYE}</button>` : ""}</span>`;
const sSub = (ic, t) => `<div class="sc sub"><i class="ss-ic">${ic}</i><b>${t}</b></div>`;      // a sub-group inside a card
const sSw2 = (name, on, text, sub, hint = "") => `<label class="sr sw2"${tip(hint)}><span>${text}</span><div class="v"><input type="checkbox" class="sw" name="${name}" ${on ? "checked" : ""}></div><small>${sub}</small></label>`;
const sCat = t => `<div class="sc">${t}</div>`;      // a small caption that groups the rows under it
const sWide = inner => `<div class="sr x">${inner}</div>`;
const sHero = (cls, icon, title, sub, right = "") => `<div class="ss-hero ${cls}"${tip(sub)}><i class="ss-ic">${icon}</i><div class="ss-ht"><b>${title}</b><small>${sub}</small></div><div class="ss-hr">${right}</div></div>`;
const abBox = o => { const head = `<i class="ss-ic">${o.icon}</i><div class="ab-t"${o.tip ? tip(o.tip) : ""}><span class="ab-n"><b>${o.title}</b>${o.pill || ""}</span>${o.sub ? `<small>${o.sub}</small>` : ""}</div><div class="ab-ctl">${o.ctl || ""}</div>`, f = o.form, body = o.body ? `<div class="ab-b${o.flush ? " flush" : ""}">${o.body}</div>` : "";
  return f ? `<form id="${f.id}" class="ab ab-f" onsubmit="return ${f.submit}"><div class="ab-h">${head}</div>${body}</form>` : `<div class="ab"><div class="ab-h">${head}</div>${body}</div>`; };      // an Api & Key box (the owner, 2026-10-16: "put each part in a box so it is more orderly"): a head - the tile, the name with its state, the buttons on the right (o.ctl) - and under it the fields (o.body); a box with o.form is one form from its head to its last field
const abF = (label, inner, cls = "") => `<label class="abf${cls ? " " + cls : ""}"><span>${label}</span>${inner}</label>`;      // a field with its name above it
const sFrame = (name, desc, pill, inner) => `<div class="card sf" data-name="${esc(name)}"><h3${tip(desc)}>${esc(name)}<span class="sp"></span>${pill || ""}</h3>${inner}</div>`;
const sSeg = flags => `<span class="sec-seg">${flags.map(f => `<i class="${f ? "on" : ""}"></i>`).join("")}</span>`;
const sSel = (name, opts, cur, attrs = "") => `<select name="${name}" ${attrs}>${opts.map(([v, l]) => `<option value="${v}" ${String(cur) === String(v) ? "selected" : ""}>${l}</option>`).join("")}</select>`;

async function setData() {      // one read for all the tabs of Settings and the windows of the alerts (a tab switch does not ask thirteen routes again); a change through the API forgets it
  if (!state.setCache || Date.now() - state.setCache.at > 20000) state.setCache = {at: Date.now(), data: await Promise.all([api("/api/settings"), api("/api/settings/telegram"), api("/api/settings/alerts"), api("/api/settings/report"), api("/api/settings/backup"), api("/api/maintenance"), api("/api/settings/repair"), api("/api/settings/probe"), api("/api/servers?role=iran"), api("/api/settings/shopbot"), api("/api/settings/ai"), api("/api/dns/accounts").catch(() => ({accounts: []})), api("/api/panels").catch(() => ({panels: []}))])};
  return state.setCache.data;
}
PAGES.settings = async () => {
  const [s, tg, al, rp, bk, mw, rp_, pr, iranList, sb, ai_, dacc, pnl] = await setData();
  const dt = ts => new Date(ts * 1000).toISOString().slice(0, 16);
  const srvOpts = (cur, none) => (none ? [["0", "—"]] : []).concat(iranList.map(x => [x.id, x.name])), ro = s.read_only;

  /* Telegram (the owner, 2026-10-16: the Alerts tab went - its Telegram part is an API connection and lives in Settings → API; the rules, the daily report and the maintenance windows are buttons of it that open small windows) */
  const tgOn = !!(tg.enabled && tg.has_token && tg.chat_id);
  /* backups */
  const kb = b => b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB";
  const topic = (k, p) => !bk.telegram_ready ? '<span class="mut">Telegram is not set up (Settings → Api & Key)</span>' : p.topic ? `<span class="pill ok" title="Its backups go to this topic of the group"><i class="bi">${SI.folder}</i>${esc(p.topic_name || "topic")}</span> <a class="mut" onclick="bkTopicOff('${k}')"><i class="bi">${SI.link}</i>unlink</a>` : `<button class="sm" type="button" onclick="bkTopic('${k}')" title="Make a separate page (topic) for it in the Telegram group">＋ topic</button>`;
  /* Backups (the owner, 2026-10-16: "make it a box and one piece, unified, MacBook style"): ONE box, a grouped list - a row for the panel, a row for the backup password, then the schedule's rows - a hairline between the rows, the name and its line of help on the left, the controls on the right */
  const part = ([k, p]) => `<div class="bk1-r bk1-p ${p.enabled && p.ready ? "ok" : "off"}"><i class="ss-ic">${k === "panel" ? SI.db : SI.cart}</i><div class="bk1-t"><span class="bk1-n"><b>${p.label}</b></span><small>${esc(p.what)}${p.ready ? "" : ' · <span class="err">set the bot server first</span>'}</small><span class="bk1-s"><span class="bk1-l"><i class="bi">${SI.clock}</i>${p.last ? `latest ${ago(p.last.at)} ago, ${kb(p.last.bytes)}` : "no backup yet"}</span>${topic(k, p)}</span></div><div class="bk1-a">${pgSw(!!p.enabled, `bkPart('${k}', this.checked)`, `title="${p.enabled ? "In the daily backup" : "Not in the daily backup"}"`)}<button class="sm pri" type="button" onclick="backupNow('${k}')" ${p.ready && bk.has_passphrase ? "" : "disabled"}><i class="bi">${SI.cloud}</i>Back up now</button><button class="sm" type="button" onclick="bkRestoreOpen('${k}')"><i class="bi">${SI.restore}</i>Restore</button>${p.last ? `<a class="sm btn" href="/api/backup/download/${esc(p.last.file)}" title="${esc(p.last.file)}"><i class="bi">${SI.down}</i>Download</a>` : ""}</div></div>`;
  const everyOpts = [[0, "day"], [15, "15 min"], [30, "30 min"], [60, "hour"], [180, "3 h"], [360, "6 h"], [720, "12 h"]], tgOpts = [["all", "every backup"], ["daily", "once a day"], ["off", "never"]];
  const passRow = `<div class="bk1-r bk1-k ${bk.has_passphrase ? "ok" : "off"}"><i class="ss-ic">${SI.key}</i><div class="bk1-t"><span class="bk1-n"><b>Backup password</b>${bk.has_passphrase ? '<span class="pill ok">set</span>' : '<span class="pill err">not set</span>'}</span><small>the files are encrypted with it; you type it only when you restore - keep it somewhere safe</small></div><div class="bk1-a">${pgSw(!!bk.has_passphrase, "bkPassToggle(this)", 'title="' + (bk.has_passphrase ? "Replace the password" : "Set the password") + '"')}</div></div>`
    + `<form class="bpw-form hidden" id="bpw-form" data-has="${bk.has_passphrase ? 1 : 0}" onsubmit="return savePass(event)">${bk.has_passphrase ? pwBox("", "passphrase", "New passphrase", "new-password", " minlength=\"12\"") : '<input class="si-f" name="passphrase" type="text" minlength="12" required placeholder="at least 12 characters" autocomplete="off" spellcheck="false">'}${bk.has_passphrase ? "" : '<button type="button" class="sm" onclick="bkGen(this.form)">🎲 Generate</button>'}<button class="pri sm" type="submit">${bk.has_passphrase ? "Replace passphrase" : "Set the password"}</button><small class="mut">at least 12 characters${bk.has_passphrase ? "; files made before keep the old one" : ""}</small></form>`;
  const fRow = (label, ctl, cls = "") => `<label class="bk1-r bk1-f ${cls}"><span>${label}</span><span class="bk1-c">${ctl}</span></label>`;
  const sched = `<form class="bk1-sc bk-sched" onsubmit="return saveBackup(event)"><div class="bk1-r bk1-h ${bk.enabled ? "ok" : "off"}"><i class="ss-ic">${SI.clock}</i><div class="bk1-t"><span class="bk1-n"><b>Schedule</b>${bk.enabled ? '<span class="pill ok">on</span>' : '<span class="pill">off</span>'}</span><small>backs up what is switched on above, by itself</small></div><div class="bk1-a">${pgSw(!!bk.enabled, "", 'name="enabled" title="Automatic backups"')}</div></div>`
    + fRow("Every", sSel("every_minutes", everyOpts, bk.every_minutes || 0, `data-dd onchange="this.form.querySelector('.bk-hour').classList.toggle('hidden', this.value !== '0')"`))
    + fRow("At hour", `<input name="hour" type="number" min="0" max="23" value="${bk.hour}">`, `bk-hour ${(bk.every_minutes || 0) ? "hidden" : ""}`)
    + fRow("Keep for", `<input name="keep_days" type="number" min="1" max="365" value="${bk.keep_days}"><em>days</em>`)
    + fRow("Send to Telegram", sSel("tg", tgOpts, bk.send_telegram ? (bk.tg_every || "all") : "off", "data-dd"))
    + `<div class="bk1-r bk1-x"><button class="pri sm" type="submit">Save</button></div></form>`;
  const backups = sFrame("Backups", "Encrypted copies of the panel: kept here and sent to Telegram.", bk.has_passphrase ? '<span class="pill ok">🔒 encrypted</span>' : '<span class="pill err">no passphrase</span>',
    `<div class="bk1">${Object.entries(bk.parts).filter(([k]) => k !== "bot").map(part).join("")}${passRow}${sched}</div>`);

  /* Api & Key (the owner, 2026-10-16: "every part of the API tab one box, compact"): each connection by an API key is ONE box - a head (a tile, the name, its state, the buttons) and one or two rows under it: this panel's API with its tokens, the AI assistant, Telegram, the probe, the DNS accounts, the Marzban / PasarGuard panels */
  const prOn = !!pr.state.active && !pr.state.fails, st = ai_.status || {};
  const accts = (dacc && dacc.accounts) || [], pls = (pnl && pnl.panels) || [], plUp = pls.filter(p => p.status === "up").length;
  window.__apiPanels = pls;      // (the edit window of a panel reads them)
  state.dnsAccounts = accts;      // (the edit window of a DNS account reads them)
  const ib = (ic, t) => `<i class="bi">${ic}</i>${t}`;      // a button with its small picture
  /* Api & Key (the owner, 2026-10-16: "every part of the API tab one box, as elegant and compact as you can"): ONE slim box for each connection - a tile, the name, its state, and its fields and buttons on the SAME line (they fold under the name only when the page is narrow); no explaining text, the words are in the tooltips */
  const apiBox = abBox({icon: SI.link, title: "This panel's API", sub: `${esc(location.origin)}/api`, ctl: `<a class="sm btn" href="/api/docs" target="_blank" rel="noopener" title="The description of every route, shown with your session">${ib(SI.link, "Open /api/docs")}</a>`});
  const tokBox = abBox({icon: SI.key, title: "API tokens", pill: '<span class="pill" id="api-cnt">…</span>', tip: "A token signs a script, a bot or an AI in; it is shown once, when it is made, and the panel keeps only its hash",
    body: `<div class="ab-make"><input class="si-f" id="tok-name" placeholder="token name (what will use it)" maxlength="40" autocomplete="off"><select id="tok-scope" data-dd title="read: GET only · act: also start checks, repairs, builds · admin: everything"><option value="read">read</option><option value="act">act</option><option value="admin">admin</option></select><button type="button" class="sm pri" onclick="tokCreate()">${ib(SI.plus, "Make")}</button></div><div id="api-box"></div>`});
  /* the AI models (the owner, 2026-10-16: "add the other AI models too: DeepSeek, ChatGPT, Grok, Gemini and more"): one row for each AI - its key, its model, and whether it is the one in use; the five best known always have a row, the others come with "Add another AI" or when they have a key */
  const aiCat = ai_.catalog || [], aiOpen = state.aiOpen || (state.aiOpen = new Set()), CHEVD = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>';
  const aiShown = aiCat.filter(c => c.has_key || AI_MAIN.includes(c.id) || aiOpen.has(c.id)), aiMore = aiCat.filter(c => !aiShown.includes(c)), aiOn = aiCat.find(c => c.active && c.has_key);
  const aiRow = c => {
    const s_ = c.status || {}, live = c.active && c.has_key;
    const pill = !c.has_key ? '<span class="pill">no key</span>' : !s_.at ? '<span class="pill">not tested</span>' : s_.ok ? '<span class="pill ok">● connected</span>' : '<span class="pill err">● not working</span>';
    const sub = !c.has_key ? (c.custom ? "any service that speaks the OpenAI protocol" : "key from " + esc(c.site)) : !s_.at ? "key …" + esc(c.key_tail || "saved") : s_.ok ? `${esc(s_.model || c.model)} · ${s_.ms} ms · ${ago(s_.at)} ago` : esc(s_.error || "no answer");
    return `<form class="aim${live ? " on" : ""}" id="aim-${c.id}" onsubmit="return aiSave(event, '${c.id}')"><i class="aim-i" style="--c:${AIM_C[c.id] || "#98a2bd"}">${AIM_M[c.id] || "AI"}</i>
      <div class="aim-t"><span class="aim-n"><b>${esc(c.name)}</b>${pill}</span><small class="${c.has_key && s_.at && !s_.ok ? "err" : ""}" title="${sub.replace(/<[^>]*>/g, "").replace(/"/g, "&quot;")}">${sub}</small></div>
      <div class="aim-a">${c.has_key ? (live ? '<span class="aim-use" title="The panel asks this AI">● In use</span>' : `<button type="button" class="sm" onclick="aiUse('${c.id}')" title="Make this the AI the panel asks">Use</button>`) + `<button type="button" class="sm" onclick="aiTest('${c.id}')" title="Ask it one word (a key typed here is saved first)">${ib(SI.play, "Test")}</button><button type="button" class="sm danger" onclick="aiForget('${c.id}', '${esc(c.name)}')" title="Remove the saved key">${ib(SI.trash, "")}</button>` : ""}</div>
      <div class="aim-f">${fIc(SI.key, `<input name="api_key" type="password" placeholder="${c.has_key ? "•••••••• saved" : "API key"}" autocomplete="off" title="empty keeps the saved one">`, true)}${c.custom ? fIc(SI.link, `<input name="base_url" value="${esc(c.base_url)}" placeholder="https://host/v1" autocomplete="off" spellcheck="false" title="The address that comes before /chat/completions">`) : ""}${fIc(SI.cube, `<span class="nk-combo" data-pid="${c.id}"><input name="model" value="${esc(c.model)}" placeholder="${esc(c.default_model || "model name")}" autocomplete="off" spellcheck="false" title="Any model name; the arrow lists the ones this AI has"><button type="button" class="nk-combo-b" aria-label="Choose a model" title="The models you can choose" onmousedown="event.preventDefault(); nkCombo(this)">${CHEVD}</button></span>`)}<button class="pri sm" type="submit">${ib(SI.save, "Save")}</button></div></form>`;
  };
  const aiBox = abBox({icon: SI.bot, title: "AI models", flush: true, pill: aiOn ? `<span class="pill ok">● ${esc(aiOn.short)} in use</span>` : '<span class="pill err">no key</span>', sub: aiOn ? esc(((aiOn.status || {}).ok ? aiOn.model + " · " + aiOn.status.ms + " ms" : aiOn.model)) : "paste the key of any one of them: the panel tests it at once",
    ctl: aiMore.length ? `<button type="button" class="sm" onclick="aiAddMenu(this, event)" title="Mistral, Groq, OpenRouter, Together, Qwen, Kimi, Perplexity, GLM - or any service that speaks the OpenAI protocol">${ib(SI.plus, "Add another AI")}</button>` : "",
    body: `<div class="aim-list">${aiShown.map(aiRow).join("")}</div>`});
  const tgBox = abBox({icon: SI.send, title: "Telegram bot", pill: tg.has_token ? (tgOn ? '<span class="pill ok">● sending</span>' : '<span class="pill warn">token saved</span>') : '<span class="pill err">no token</span>', form: {id: "tg-form", submit: "saveTelegram(event)"},
    ctl: `<button type="button" class="sm" onclick="testTelegram()" title="Send a test message to the chat">${ib(SI.play, "Test")}</button><button class="pri sm" type="submit">${ib(SI.save, "Save")}</button>`,
    body: `<div class="ab-grid">${abF("Bot token", fIc(SI.key, `<input name="token" type="password" placeholder="${tg.has_token ? "•••••••••• saved" : "123456789:AA…"}" autocomplete="off" title="from @BotFather; empty keeps the saved one">`, true))}${abF("Chat id", fIc(SI.send, `<input name="chat_id" value="${esc(tg.chat_id)}" placeholder="-1001234567890" autocomplete="off" title="your id, a negative group id, or @channel">`))}<div class="abf"><span>Send alerts, reports and backups</span><span class="ab-sw">${pgSw(!!tg.enabled, "", 'name="enabled"')}</span></div></div>`
      + `<div class="ab-row ab-btns"><button class="sm" type="button" onclick="alertsWin('rules')" title="When a tunnel, a server or a config is called down, and the reminders">${ib(SI.rules, "Rules")}</button><button class="sm" type="button" onclick="alertsWin('report')" title="The daily report: on / off, the hour, a preview, send now">${ib(SI.clock, "Daily report")}</button><button class="sm" type="button" onclick="alertsWin('windows')" title="Maintenance windows: no alert is sent inside one">${ib(SI.wrench, "Maintenance windows")}</button></div>`});
  const probeBox = abBox({icon: SI.probe, title: "Probe", pill: prOn ? '<span class="pill ok">connected</span>' : pr.state.active ? '<span class="pill err">not answering</span>' : '<span class="pill">no probe set</span>',
    ctl: `<button type="button" class="sm" onclick="testProbe()" title="Ask the probe">${ib(SI.play, "Test")}</button><button class="pri sm" type="submit" form="probe-form">${ib(SI.save, "Save")}</button>`,
    body: `<form id="probe-form" class="ab-grid" onsubmit="return saveProbe(event)">${abF("Address", fIc(SI.link, `<input name="api_url" value="${esc(pr.api_url)}" placeholder="http://ip:18080" title="the address of the primary probe">`))}${abF("Token", fIc(SI.key, `<input name="api_token" type="password" placeholder="${pr.has_api_token ? "•••••••• saved" : "speed-api.token on the probe"}" autocomplete="off" title="empty keeps the saved one">`, true))}${abF("Iran server", sIc(SI.server, sSel("server_id", srvOpts(pr.server_id, true), pr.server_id || 0, "data-dd")))}${abF("Standby probes", `<textarea name="standby" rows="2" spellcheck="false" placeholder="one per line: name,http://ip:18080,token" title="an empty token keeps the saved one">${(pr.standby || []).map(x => `${x.name},${x.api_url},`).join("\n")}</textarea>`, "ab-wide")}</form>`});
  const instBox = abBox({icon: SI.down, title: "Install a probe", form: {id: "inst-form", submit: "installProbe(event)"}, tip: "on an Iran server: copies the code and Xray, registers it as standby (or primary when there is none)",
    ctl: `<button class="sm pri" type="submit" ${ro ? "disabled" : ""}>${ib(SI.down, "Install probe")}</button>`,
    body: `<div class="ab-grid">${abF("Iran server", sIc(SI.server, sSel("server_id", srvOpts(0, false), iranList[0] && iranList[0].id, "data-dd")))}</div>`});
  const dnsBox = abBox({icon: SI.cloud, title: "DNS accounts", pill: accts.length ? `<span class="pill ok">${accts.length}</span>` : '<span class="pill err">none</span>', sub: accts.length ? "" : "add an ArvanCloud key or a Cloudflare token", ctl: `<button class="pri sm" type="button" onclick="dnsAccForm()">${ib(SI.plus, "Add account")}</button>`,
    body: accts.map(a => { const s = a.status || {}; return `<div class="ab-li"><span class="ab-ln"><b>${esc(a.name)}</b><small>${esc(a.provider_name || a.provider)}${a.key_tail ? " · •••• " + esc(a.key_tail) : ""}${a.main ? " · main (the failover uses it)" : ""}</small></span><span class="pill ${s.ok ? "ok" : s.error ? "err" : ""}">${s.ok ? (s.domains || 0) + " domains" : s.error ? "not working" : "not tested"}</span><button class="sm" type="button" onclick="dnsAccTest('${esc(a.id)}')" title="Try the key (read only)">Test</button><button class="sm" type="button" onclick="dnsAccForm('${esc(a.id)}')" title="Change the name or the key">Edit</button><button class="sm danger" type="button" onclick="dnsAccDel('${esc(a.id)}', '${esc(a.name)}')" title="Remove this account from the panel (nothing changes at the provider)">Remove</button></div>`; }).join("")});
  const panelsBox = abBox({icon: SI.cube, title: "Marzban / PasarGuard panels", pill: pls.length ? `<span class="pill ${plUp === pls.length ? "ok" : "warn"}">${plUp} / ${pls.length} up</span>` : '<span class="pill err">none</span>', sub: pls.length ? "" : "add your PasarGuard or Marzban panel with its API key", ctl: `<button class="pri sm" type="button" onclick="pnAddWin()">${ib(SI.plus, "Add panel")}</button>`,
    body: pls.map(p => { const a = p.auth || {}, what = a.kind === "api_key" ? "API key" : a.kind === "token" ? "access token" : a.kind === "login" ? "saved login" : "no credential"; return `<div class="ab-li"><span class="ab-ln"><b>${esc(p.name)}</b><small>${esc(p.type)} · <span class="mono">${esc(p.url)}</span> · ${what}</small></span><span class="pill ${p.status === "up" ? "ok" : p.status === "down" ? "err" : ""}">${p.status === "up" ? "up" : p.status === "down" ? "down" : "not checked"}</span><button class="sm" type="button" onclick="apiPanelEdit(${p.id})" title="Change the name, the address or the key">Edit</button><button class="sm danger" type="button" onclick="pnDel(${p.id}, '${esc(p.name)}')" title="Take the panel off this list (nothing changes on the panel itself)">Remove</button></div>`; }).join("")});
  const apiFrame = sFrame("Api & Key", "This panel's own API - tokens and docs - and every connection that works by an API key: the AI models, Telegram, the probe, the DNS accounts and the panels, each in its own box.", '<span class="pill" id="api-pill">…</span>', `<div class="ab-list">${apiBox}${tokBox}${aiBox}${tgBox}${probeBox}${instBox}${dnsBox}${panelsBox}</div>`);
  /* shop bot */
  const shop = sFrame("Shop bot", "The server that runs the bot and how the panel reaches it (with its SSH key).", sb.host ? '<span class="pill ok">● server set</span>' : '<span class="pill">not set</span>',
    `<form class="sl" onsubmit="return saveShopbot(event)">`
    + sGrp({ok: !!sb.host, icon: SI.cart, title: "Bot server", sub: "Configure the server that runs the shop bot.",
        rows: sSub(SI.link, "Connection") + sRow("Server", fIc(SI.server, `<input name="host" value="${esc(sb.host)}" placeholder="ip or host">`), "IP or host") + sRow("SSH user", fIc(SI.user, `<input name="ssh_user" value="${esc(sb.ssh_user)}">`)) + sRow("SSH port", fIc(SI.hash, `<input name="ssh_port" type="number" value="${sb.ssh_port}">`)) + sSub(SI.folder, "Bot files") + sRow("Bot directory", fIc(SI.folder, `<input name="dir" value="${esc(sb.dir)}">`)) + sRow("DB env file", fIc(SI.file, `<input name="env" value="${esc(sb.env)}">`)) + sRow("Public webhook URL", fIc(SI.link, `<input name="public_url" value="${esc(sb.public_url)}" placeholder="https://host/path/bot.php">`))})
    + `<div class="sf-act"><button class="pri sm" type="submit"><i class="bi">${SI.save}</i>Save</button></div></form>`);
  /* appearance */
  const look = sFrame("Appearance", "Theme, day / night, text size, animations — kept in this browser.", "",
    sGrp({icon: SI.paint, title: "Theme", cls: "x flat", rows: sWide(`<div class="thm">${THEMES.map(thCard).join("")}</div>`)})
    + sGrp({icon: SI.gear, title: "Options", cls: "two flat",
        rows: sRow("Day / night", sIc(SI.moon, sSel("mode", [["dark", "Night"], ["light", "Day"], ["auto", "Follow my device"]], LOOK.mode, `onchange="lookSet('mode', this.value)"`))) + sRow("Text size", sIc(SI.aa, sSel("size", [["small", "Small"], ["normal", "Normal"], ["large", "Large"]], LOOK.size, `onchange="lookSet('size', this.value)"`))) + sRow("Animations", sIc(SI.pulse, sSel("anim", [["calm", "Calm (default)"], ["rich", "Rich: moving backgrounds"], ["off", "Off (lightest)"]], LOOK.anim, `onchange="lookSet('anim', this.value)"`)), "Calm: nothing moves by itself"),
        acts: '<button type="button" class="sm" onclick="lookReset()"><i class="bi">' + SI.reset + '</i>Reset the look</button>'}));

  /* about & update (the owner's design picture, 2026-10-16): four boxes of ONE fixed size - this panel, its license, the tunnel cores, the PasarGuard panels and nodes - each with a big icon, and under them what the service runs with, every fact with its little icon; what is longer scrolls inside its box */
  const GH = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 19c-4 1.5-4-2-6-2.5m12 5v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 00-1.3-3.2 4.2 4.2 0 00-.1-3.2s-1.1-.3-3.5 1.3a12.3 12.3 0 00-6.2 0C6.5 2.8 5.4 3.1 5.4 3.1a4.2 4.2 0 00-.1 3.2A4.6 4.6 0 004 9.5c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2V21"/></svg>', CHEV = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>', COPY = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 012-2h9"/></svg>';
  const fi = d => `<i class="abt-fi"><svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg></i>`;
  const facts = [[fi('<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14"/>'), "Works by", "API based"], [fi('<circle cx="12" cy="12" r="9"/><path d="M12 8v4.5M12 16h.01"/>'), "Mode", ro ? "read-only" : "read-write"], [fi('<circle cx="6" cy="6" r="2.2"/><circle cx="18" cy="6" r="2.2"/><circle cx="12" cy="18" r="2.2"/><path d="M8.2 6h7.6M7 8l4 8M17 8l-4 8"/>'), "Tunnel checks", `every ${s.tunnel_check_interval} s`], [fi('<path d="M5 12a7 7 0 0112-5l2 2M19 12a7 7 0 01-12 5l-2-2M19 5v4h-4M5 19v-4h4"/>'), "Server checks", `every ${s.server_sample_interval} s`], [fi('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'), "Session", `${s.session_hours} h`]];      // only what is of use to the one who uses the panel (the owner, 2026-10-16: the rest - SSH workers, log level, port, the env file - is not shown; the env file stays in the tooltip)
  const about = sFrame("About & update", "This panel, its license, the tunnel cores on your servers, the PasarGuard panels and their nodes, and what the service runs with.", "",
    `<div class="abt-wrap"><div class="abt-grid">`
    + `<div class="abt-card" id="about-hero"><div class="abt-ch"><i class="abt-ti ok">${SI.up}</i><div class="abt-ct"><div class="abt-t1"><b>About &amp; Update</b><span class="pill">v${esc(s.version)}</span></div></div><span class="abt-pill" id="about-pill">checking…</span></div><div class="abt-cb"><div id="about-box" class="abt-line"></div><button type="button" class="abt-row" onclick="panelUpdate()" disabled title="Looking for a newer version…"><i class="abt-ti sm">${GH}</i><b>Update from GitHub</b><i class="abt-chev">${CHEV}</i></button></div></div>`
    + `<div class="abt-card"><div class="abt-ch"><i class="abt-ti">${SI.shield(false)}</i><div class="abt-ct"><div class="abt-t1"><b>License</b></div><small id="lic-head">loading…</small></div></div><div class="abt-cb" id="lic-box"><div class="mut">loading…</div></div></div>`
    + `<div class="abt-card"><div class="abt-ch"><i class="abt-ti">${SI.cpu}</i><div class="abt-ct"><div class="abt-t1"><b>Tunnel cores</b></div></div></div><div class="abt-cb" id="eng-box"><div class="mut">loading…</div></div></div>`
    + `${window.pgUpdBody ? pgUpdBody() : ""}</div>`
    + `<div class="abt-foot" title="set in ${esc(s.env_file)} on the panel server, then systemctl restart nikatel">${facts.map(([ic, k, v]) => `<span>${ic}<span class="abt-fv"><em>${k}</em><b>${v}</b></span></span>`).join("")}<button type="button" class="sm abt-diag" onclick="abtDiag()" title="Copy a report of this panel's state to paste where you ask for help: versions, health, every job, the last problems with their request ids - no key, no password, no server address"><i class="bi">${COPY}</i><span class="bt">Copy diagnostics</span></button></div></div>`);
  /* security: filled by secLoad */
  const security = sFrame("Security", "How the panel is protected, and your password.", '<span class="pill" id="sec-pill">…</span>', '<div id="sec-box"><div class="mut">loading…</div></div>');

  $("#page").innerHTML = `<div class="grid2 set-wrap">${security}${backups}${apiFrame}${shop}${look}${about}</div>`;
  settingsTabs();
  if (state.stab === "update") { aboutLoad(); enginesLoad(); licLoad(); if (window.pgUpdLoad) pgUpdLoad(); }
  if ($("#sec-box")) secLoad();
  if ($("#api-box")) apiLoad();
};/* ---- security: two-step sign-in (authenticator app), recovery codes, the secret entry path, the security log */
const sec = {setup: null, codes: null};
async function secLoad() {
  const box = $("#sec-box"); if (!box) return;
  const s = await api("/api/auth/security");
  const pill = $("#sec-pill"); if (pill) { pill.textContent = s.totp_enabled ? "2-step on" : "2-step OFF"; pill.className = "pill " + (s.totp_enabled ? "ok" : "err"); }
  const url = s.entry_path ? location.origin + "/" + s.entry_path : "";
  const on2 = !!s.totp_enabled, onR = (s.recovery_left || 0) > 0, onP = !!url, score = [on2, onR, onP].filter(Boolean).length;
  const codes = "";
  const setup = sec.setup ? sGrp({icon: SI.shield(false), title: "Turn on two-step sign-in", cls: "x",
      rows: sWide(`<div class="sec-qr">${sec.setup.qr_svg || ""}<div style="flex:1;min-width:240px"><b>1.</b> Open Google Authenticator (or any TOTP app) → add an account → scan the picture, or type this key:<div class="sec-key">${esc(sec.setup.secret.replace(/(.{4})/g, "$1 ").trim())}</div><div style="margin-top:10px"><b>2.</b> Type the 6-digit code the app shows:</div><div class="frm-act"><input id="sec-code" inputmode="numeric" maxlength="7" placeholder="123456" style="width:140px" onkeydown="if(event.key==='Enter')secEnable()"><button class="pri sm" onclick="secEnable()">Turn on</button><button class="sm" onclick="sec.setup=null;secLoad()">Cancel</button></div></div></div>`)}) : "";
  const hero = sHero("s" + score, SI.shield(score === 3), score === 3 ? "Fully protected" : score === 0 ? "Only the password protects the panel" : `${score} of 3 protections are on`, score === 3 ? "Two-step sign-in, recovery codes and a secret path are all on." : "Turn on the rest so a stolen or guessed password is not enough.", sSeg([on2, onR, onP]));
  const g2 = sItem({ok: on2, icon: SI.shield(on2), title: "Two-step sign-in", pill: on2 ? '<span class="pill ok">on</span>' : '<span class="pill err">off</span>', desc: on2 ? "every sign-in asks for the code of your authenticator app" : "a second code from your phone on every sign-in",
      acts: `<input type="checkbox" class="sw" ${on2 ? "checked" : ""} title="${on2 ? "Turn two-step sign-in off" : "Turn two-step sign-in on"}" onchange="this.checked = !this.checked; ${on2 ? "secOff()" : "secSetup()"}">`});      // (the switch was only drawn since 1.17.38 and did nothing: it shows the real state and starts the turn-on (QR + code) or the turn-off (password + code); the state is drawn again when it is done)
  const gR = sItem({ok: onR, icon: SI.buoy, title: "Recovery codes", pill: `<span class="pill ${onR ? "ok" : "err"}">${s.recovery_left} left</span>`, desc: "sign in without the app, or reset a forgotten password",
      field: `<input class="si-f mono" readonly placeholder="the new codes show here, once" value="${sec.codes ? esc(sec.codes.join("  ")) : ""}" onclick="this.select()">`,
      acts: `<button class="sm" onclick="secNewCodes()">Make new codes</button>${sec.codes ? '<button class="sm" onclick="secDownloadCodes()">⬇ Download</button><button class="sm pri" onclick="sec.codes=null;secLoad()">I saved them</button>' : ""}`});
  const gP = sItem({ok: onP, icon: SI.lock(onP), title: "Secret entry path", pill: onP ? '<span class="pill ok">on</span>' : '<span class="pill err">off</span>', desc: "the panel answers 404 until the secret address is opened once",
      field: `<input class="si-f" id="sec-path" placeholder="${onP ? esc(s.entry_path) : "your own path (8-64 letters, digits, - _)"}" value="" autocomplete="off" spellcheck="false" onkeydown="if(event.key==='Enter')secPathSet()">`,
      acts: `<button class="sm pri" onclick="secPathSet()">Set</button><button class="sm" onclick="secPath(true)">Random</button>${onP ? '<button class="sm danger" onclick="secPathOff()">Off</button>' : ""}`});
  const gW = `<form class="sl" onsubmit="return changePassword(event)"><div class="si pw"><i class="ss-ic">${SI.key}</i><div class="si-b"><div class="si-t"><span class="si-n"><b>Password</b></span><small>at least 10 characters</small></div><div class="pwf-f">${pwBox("cur", "current", "Current password", "current-password")}${pwBox("nw", "new", "New password", "new-password", " minlength=\"10\"")}${pwBox("nw", "again", "Repeat the new one", "new-password", " minlength=\"10\"")}<div class="pwf-btns"><button class="pri pwf-chg" type="submit"><i class="bi">${SI.lock(false)}</i>Change password</button><button type="button" class="pwf-out on" aria-pressed="true" onclick="pwOutToggle(this)" title="On: when the password is changed, every other device is signed out. Off: they stay signed in."><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2"/><path class="lk-o" d="M8 11V8a4 4 0 017.6-1.7"/><path class="lk-c" d="M8 11V8a4 4 0 018 0v3"/></svg>Sign out of other devices</button></div></div></div></div></form>`;  box.innerHTML = `${codes}${hero}${setup}<div class="sl">${sec.setup ? "" : g2}${gR}${gP}</div>${gW}`;
}
async function apiLoad() {      // Settings → Api & Key: the tokens of this panel's API - the line that makes one is in the head of its box, the tokens are listed under it
  const box = $("#api-box"); if (!box) return;
  let tk = {tokens: []}; try { tk = await api("/api/tokens"); } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  const toks = tk.tokens || [];
  const pill = $("#api-pill"); if (pill) { pill.textContent = toks.length ? `${toks.length} token${toks.length === 1 ? "" : "s"}` : "no token"; pill.className = "pill " + (toks.length ? "ok" : ""); }
  const cnt = $("#api-cnt"); if (cnt) { cnt.textContent = toks.length; cnt.className = "pill " + (toks.length ? "ok" : ""); }
  box.innerHTML = toks.length ? `<div class="tok-list">${toks.map(t => `<div class="tok-row"><b>${esc(t.name)}</b><span class="pill">${esc(t.scope)}</span><code>${esc(t.prefix)}…</code><small class="mut">${t.last_used_at ? "used " + ago(t.last_used_at) + " ago" : "never used"} · made ${ago(t.created_at)} ago</small><button type="button" class="sm danger" onclick="tokRevoke(${t.id}, this.closest('.tok-row').querySelector('b').textContent)">Revoke</button></div>`).join("")}</div>` : "";
}
window.tokCreate = async () => {
  const name = ($("#tok-name") || {}).value || "", scope = ($("#tok-scope") || {}).value || "read";
  if (!name.trim()) { toast("Give the token a name first", "bad"); return; }
  const pw = await askPw(); if (!pw) return;
  try {
    const r = await api("/api/tokens", {method: "POST", body: JSON.stringify({name: name.trim(), scope, password: pw})});
    showModal("<b>Your new API token</b>", `<p>This is the only time it is shown. Copy it now and keep it where the script or bot reads it.</p><div class="tok-show"><code id="tok-new">${esc(r.token)}</code><button class="sm pri" onclick="navigator.clipboard.writeText(document.getElementById('tok-new').textContent).then(() => toast('Copied', 'ok'))">Copy</button></div>
      <p class="mut">Use it as <code>Authorization: Bearer ${esc(r.prefix)}…</code> on every request. Scope <b>${esc(r.scope)}</b>: ${r.scope === "read" ? "GET only" : r.scope === "act" ? "reads, and the routes that start checks, repairs and builds; not the settings" : "everything"}.</p>`, "narrow");
    apiLoad();
  } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.tokRevoke = async (id, name) => { if (!await gconfirm(`Revoke the token ${name}? Whatever uses it stops working at once.`)) return; try { await api(`/api/tokens/${id}`, {method: "DELETE"}); toast("Token revoked", "ok"); apiLoad(); } catch (e) { toast("✕ " + e.message, "bad"); } };const askPw = () => gprompt("Your password, to confirm:", "", {password: true, generate: false});      // (your own password is typed, never made: no Generate button and no "make a strong one" hint, the owner, 2026-10-16)
window.secSetup = async () => { const pw = await askPw(); if (!pw) return; try { sec.setup = await api("/api/auth/2fa/setup", {method: "POST", body: JSON.stringify({password: pw})}); secLoad(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.secEnable = async () => { try { const r = await api("/api/auth/2fa/enable", {method: "POST", body: JSON.stringify({password: "", code: $("#sec-code").value})}); sec.setup = null; sec.codes = r.recovery_codes; toast("✓ Two-step sign-in is on", "ok"); secLoad(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.secOff = async () => { const pw = await askPw(); if (!pw) return; const code = await gprompt("A code from the app, or a recovery code:"); if (!code) return; try { await api("/api/auth/2fa/disable", {method: "POST", body: JSON.stringify({password: pw, code})}); toast("Two-step sign-in is off", "ok"); secLoad(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.secNewCodes = async () => { if (!await gconfirm("Make new recovery codes? The old ones stop working.", {danger: false})) return; const pw = await askPw(); if (!pw) return; try { sec.codes = (await api("/api/auth/recovery/regenerate", {method: "POST", body: JSON.stringify({password: pw})})).recovery_codes; secLoad(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.secDownloadCodes = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob(["Nikatel recovery codes (each works once)\n\n" + (sec.codes || []).join("\n") + "\n"], {type: "text/plain"})); a.download = "nikatel-recovery-codes.txt"; document.body.appendChild(a); a.click(); a.remove(); };
window.secPathSet = () => { const v = ($("#sec-path") || {}).value || ""; if (!v.trim()) { toast("Type the path first (8-64 letters, digits, - and _), or press Random", "bad"); return; } secPath(false, v.trim()); };
window.secPath = async (random, typed) => { let path = ""; if (random) { const b = new Uint8Array(9); crypto.getRandomValues(b); path = "nk-" + [...b].map(x => x.toString(36).padStart(2, "0")).join("").slice(0, 14); } else { path = typed || await gprompt("The secret path (8-64 letters, digits, - and _):"); if (!path) return; } const pw = await askPw(); if (!pw) return; try { const r = await api("/api/auth/entry-path", {method: "PUT", body: JSON.stringify({path, password: pw})}); toast("✓ The panel now lives at /" + r.path, "ok"); secLoad(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.secPathOff = async () => { if (!await gconfirm("Turn the secret path off? The panel answers on its normal address again.")) return; const pw = await askPw(); if (!pw) return; try { await api("/api/auth/entry-path", {method: "PUT", body: JSON.stringify({path: "", password: pw})}); toast("Secret path off", "ok"); secLoad(); } catch (e) { toast("✕ " + e.message, "bad"); } };
const aiCur = () => (state.setCache && state.setCache.data && state.setCache.data[10]) || {};      // (what is saved; the page keeps it for the tabs of Settings)
const aiPath = pid => `/api/settings/ai/providers/${encodeURIComponent(pid)}`;
const aiSend = f => ({api_key: f.api_key.value.trim(), model: f.model.value.trim(), ...(f.base_url ? {base_url: f.base_url.value.trim()} : {})});
window.aiSave = async (e, pid) => {      // the key and the model of one AI: the panel asks it one word at once and the row shows the answer (a saved key puts the AI on; the first one saved is the one in use)
  e.preventDefault(); const f = e.target, b = f.querySelector("button[type=submit]"); if (b) b.disabled = true;
  try { await api(aiPath(pid), {method: "PUT", body: JSON.stringify(aiSend(f))}); aiRemember(pid, f.model.value); delete aiLive[pid]; toast("✓ Saved", "ok"); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); if (b) b.disabled = false; }
  return false;
};
window.aiTest = async pid => {      // a key typed in the row is saved first (the test asks the SAVED key), and the page is drawn again so the state shows
  const f = document.getElementById("aim-" + pid); toast("Asking…");
  try { if (f && f.api_key.value.trim()) await api(aiPath(pid), {method: "PUT", body: JSON.stringify(aiSend(f))}); const r = await api(aiPath(pid) + "/test", {method: "POST"}); toast(`✓ ${r.provider} answers (${r.ms} ms)`, "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
  refresh();
};
window.aiUse = async pid => { try { await api("/api/settings/ai/use", {method: "POST", body: JSON.stringify({provider: pid})}); toast("✓ The panel asks this AI now", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.aiForget = async (pid, name) => { if (!await gconfirm(`Remove the saved key of ${name}?`)) return; try { await api(aiPath(pid), {method: "DELETE"}); delete aiLive[pid]; toast("✓ Key removed", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.aiAddMenu = (btn, ev) => {      // "Add another AI": the ones without a row yet, in the menu of the kit; the row opens with its key box ready
  const open = state.aiOpen || (state.aiOpen = new Set()), more = (aiCur().catalog || []).filter(c => !c.has_key && !AI_MAIN.includes(c.id) && !open.has(c.id));
  nkMenu(btn, [labGrp("Add another AI"), ...more.map(c => labItem(c.name, () => { open.add(c.id); refresh(); let n = 0; const t = setInterval(() => { const f = document.getElementById("aim-" + c.id); if (f || ++n > 25) { clearInterval(t); if (f) { f.scrollIntoView({block: "center", behavior: "smooth"}); const i = f.querySelector("input[name=api_key]"); if (i) i.focus({preventScroll: true}); } } }, 120); }, {ic: `<i class="aim-mono" style="--c:${AIM_C[c.id] || "#98a2bd"}">${AIM_M[c.id] || "AI"}</i>`}))], ev);
};
let aboutAuto = null, aboutV0 = "", aboutTimer = null;      // the timer of the automatic look for a new version, the version that runs, and the timer of the progress of an update
const aboutPill = (txt, cls, tip) => { const p = $("#about-pill"); if (p) { p.className = "abt-pill" + (cls ? " " + cls : ""); p.textContent = txt; p.title = tip || ""; } };
const aboutBtn = (off, tip) => { const b = document.querySelector("#about-hero .abt-row"); if (b) { b.disabled = off; b.title = tip; } };      // (the owner, 2026-10-16: the button is off while this is the newest version)
window.aboutLoad = async () => { const box = $("#about-box"); if (!box || aboutTimer) return; clearTimeout(aboutAuto); aboutAuto = setTimeout(() => { if ($("#about-box")) aboutLoad(); }, 60000);      // (it looks again by itself every minute: no Check button - the owner, 2026-10-15)
  try {
    const u = await api("/api/system/update"); aboutV0 = u.version || aboutV0;
    const p = u.progress || {};
    if (p.state === "running") { aboutPill(p.to ? "Updating to " + p.to : "Updating", "busy"); aboutBtn(true, "An update is running"); aboutWatch(p); return; }      // (opened or refreshed in the middle of an update: the bar goes on from where the SERVER says it is, and while it runs the card says neither "up to date" nor "a new version" - the owner, 2026-10-17)
    const failed = p.state === "failed" && Date.now() / 1000 - (p.at || 0) < 86400;
    if (!u.checked) aboutPill("Could not reach GitHub", "warn", "The panel could not ask GitHub for the newest version just now; it asks again by itself");
    else if (u.up_to_date) aboutPill("Up to date");
    else aboutPill(u.newer && u.latest ? "Version " + u.latest + " is available" : "A newer build is available", "warn");
    aboutBtn(!!u.up_to_date, u.up_to_date ? "This panel is already the newest version" : "Update this panel from GitHub now");
    box.innerHTML = (failed ? `<div class="err" title="${esc(u.last_log || "")}">The last update did not finish (${ago(p.at)} ago): ${esc(p.reason || "it stopped")}. The version that was running stays.</div>` : "") + (!u.up_to_date && u.newer && u.notes_url ? `<a class="about-notes" href="${esc(u.notes_url)}" target="_blank" rel="noopener">What changed in v${esc(u.latest)} on GitHub ↗</a>` : "");
  } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; aboutBtn(false, "Update this panel from GitHub now"); } };
window.abtCopy = id => { const el = document.getElementById(id); if (el && navigator.clipboard) navigator.clipboard.writeText(el.textContent.trim()).then(() => toast("Copied", "ok"), () => toast("✕ could not copy", "bad")); };window.panelUpdate = async () => { if (!await gconfirm("Update the panel from GitHub now? It restarts for a few seconds; a version that fails its health check is rolled back.")) return; try { const r = await api("/api/system/update", {method: "POST"}); if (!r.ok) { toast("✕ " + r.error, "bad"); return; } aboutPill(r.to ? "Updating to " + r.to : "Updating", "busy"); aboutWatch({percent: 2}); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.aboutWatch = p0 => {      // the update runs: only its percentage, moving smoothly like the update of a MacBook (the owner, 2026-10-15). The percentage is the SERVER's - it counts by its own clock and keeps it in a file - so a refreshed page, a second tab, or the restart of the panel in the middle of the run all show the same number and nothing starts again from 1 (the owner, 2026-10-17); the page only eases towards it
  if (aboutTimer) return; let server = (p0 && p0.percent) || 2, shown = server, down = 0, end = "", why = ""; const t0 = Date.now();
  document.querySelectorAll("#about-hero button").forEach(b => { b.disabled = true; });
  const paint = (txt, cls) => { const b = $("#about-box"); if (!b) return false; let bar = b.querySelector(".about-bar u"); if (!bar) { b.innerHTML = `<span></span><i class="about-bar"><u></u></i>`; bar = b.querySelector(".about-bar u"); } const sp = b.querySelector("span"); sp.className = cls; sp.textContent = txt; bar.style.width = shown.toFixed(2) + "%"; return true; };
  const stop = () => { clearInterval(aboutTimer.frame); clearTimeout(aboutTimer.poll); aboutTimer = null; };
  const poll = async () => {
    let u = null; try { u = await api("/api/system/update/progress"); down = 0; } catch (e) { down++; /* the panel restarts: the bar waits */ }
    const p = u && u.progress;
    if (p && p.state === "running") server = Math.max(server, p.percent); else if (p && p.state === "done") end = "done"; else if (p && p.state === "failed") { end = "failed"; why = p.reason || ""; }
    if (u && u.version && aboutV0 && u.version !== aboutV0 && !end) end = "done";      // (the panel came back as ANOTHER version: the update is done)
    if (aboutTimer) aboutTimer.poll = setTimeout(poll, 1000);
  };
  const frame = () => {
    if (down) server = Math.min(Math.max(server, 90), server + 0.02);      // (the panel restarts: the bar waits, and creeps a little so that it does not look frozen)
    shown += ((end === "done" ? 100 : end === "failed" ? shown : server) - shown) * (end === "done" ? 0.2 : 0.12);
    if (end === "failed") { stop(); aboutLoad(); return; }      // (the card draws what went wrong from the same file, and the button is free again)
    if (end === "done" && shown > 99.4) { shown = 100; paint("Updated 100%", "gw-ok"); stop(); setTimeout(() => location.reload(), 900); return; }
    if (!end && Date.now() - t0 > 600000) { stop(); aboutLoad(); return; }      // (ten minutes without an end: look again at what the server says)
    if (!paint(`Updating… ${Math.round(shown)}%`, "mut")) stop();
  };
  aboutTimer = {frame: setInterval(frame, 100), poll: setTimeout(poll, 300)};
};
/* the look: theme, day/night, text size, animations - kept in this browser */
const THEMES = [   // key, name, note, page colour, accent, second accent, flat preview
  ["calm", "Pro slate", "the new look: slate boxes, soft blue tiles, flat quiet buttons, compact and aligned", "#0c1222", "#7aa7f0", "#33405c"],
  ["aurora", "Aurora", "frosted glass, soft aurora", "#0b1030", "rgba(124,131,255,.95)", "rgba(56,217,245,.8)"],
  ["mono", "Mono", "black and white, frosted", "#101010", "rgba(255,255,255,.9)", "rgba(161,161,170,.7)"],
  ["ocean", "Ocean", "deep water, slow waves", "#062b3d", "rgba(45,212,191,.95)", "rgba(56,189,248,.8)"],
  ["emerald", "Emerald", "forest green, light beams", "#07271b", "rgba(16,185,129,.95)", "rgba(163,230,53,.7)"],
  ["sunset", "Sunset", "rose, amber and violet", "#2a1020", "rgba(244,63,94,.95)", "rgba(251,146,60,.85)"],
  ["royal", "Royal", "indigo night with stars", "#160d33", "rgba(124,58,237,.95)", "rgba(240,171,252,.75)"],
  ["gold", "Gold", "dark with gold shimmer", "#1c1608", "rgba(230,184,92,.95)", "rgba(246,217,138,.75)"],
  ["neon", "Cyber", "magenta, cyan, grid floor", "#0a0620", "rgba(255,43,214,.9)", "rgba(25,245,255,.8)"],
  ["graphite", "Graphite", "quiet pro dark, blue", "#1a1e26", "rgba(96,165,250,.8)", "rgba(148,163,184,.55)"],
  ["classic", "Classic", "plain admin, flat blue", "#1c2638", "#2563eb", "#33415a", true],
  ["minimal", "Minimal", "almost no decoration", "#1a1c21", "rgba(156,163,175,.6)", "rgba(203,213,225,.4)"],
  ["soft", "Soft", "clean white, raised glossy buttons", "#e8edf7", "#0a7cff", "#9cc7ff"],
];
const thLight = hex => { const m = /^#?([0-9a-f]{6})$/i.exec(hex || ""); if (!m) return false; const n = parseInt(m[1], 16); return ((n >> 16) * 299 + ((n >> 8) & 255) * 587 + (n & 255) * 114) / 1000 > 150; };      // a light page colour: its little picture is drawn in dark ink
const thCard = ([k, name, desc, bg, a, c, flat]) => `<div class="thc${flat ? " flat" : ""}${LOOK.theme === k ? " on" : ""}" role="button" tabindex="0" title="${esc(desc)}" aria-pressed="${LOOK.theme === k}" onclick="lookSet('theme', '${k}'); refresh()" onkeydown="if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); this.click(); }"><i class="thc-pv${thLight(bg) ? " lt" : ""}" style="--tb:${bg};--ta:${a};--tc:${c}" aria-hidden="true"><s class="sb"></s><s class="tp"></s><s class="c1"></s><s class="c2"></s><s class="bt"></s></i><span class="thc-n"><b>${esc(name)}</b><small>${esc(desc.split(",")[0].replace(/^the new look: /, ""))}</small></span></div>`;      // (the owner, 2026-10-16: "make the theme previews prettier"): the picture is a small panel in the colours of the theme - a sidebar, a top bar, two cards and a button
const LOOK = Object.assign({theme: "aurora", mode: "dark", size: "normal", anim: "calm"}, JSON.parse(localStorage.getItem("nk.look") || "{}"));
if (!localStorage.getItem("nk.calm.v1")) { try { localStorage.setItem("nk.calm.v1", "1"); LOOK.theme = "calm"; localStorage.setItem("nk.look", JSON.stringify(LOOK)); } catch (e) { /* private window */ } }      // once: the quiet "Calm slate" look; Settings → Look has the others
if (!["calm", "rich", "off"].includes(LOOK.anim)) LOOK.anim = "calm";      // the old "on" is the calm default now: nothing moves by itself, only what you touch
function lookApply() { const h = document.documentElement; h.dataset.theme = LOOK.theme; h.dataset.size = LOOK.size; h.dataset.anim = LOOK.anim; const light = LOOK.mode === "light" || (LOOK.mode === "auto" && matchMedia("(prefers-color-scheme: light)").matches); h.dataset.mode = light ? "light" : "dark"; const b = $("#mode-btn"); if (b) b.textContent = light ? "🌙" : "☀"; const lg = document.querySelector(".logo-orb svg"); if (lg && lg.unpauseAnimations) lg.unpauseAnimations(); }      // the logo moves in every animation mode
window.lookSet = (k, v) => { LOOK[k] = v; localStorage.setItem("nk.look", JSON.stringify(LOOK)); lookApply(); };
window.lookReset = () => { localStorage.removeItem("nk.look"); Object.assign(LOOK, {theme: "aurora", mode: "dark", size: "normal", anim: "calm"}); lookApply(); refresh(); };
window.toggleMode = () => lookSet("mode", document.documentElement.dataset.mode === "light" ? "dark" : "light");
lookApply();
/* settings are grouped in tabs; each tab has one line that says what it is for */
const TAB_SVG = {account: SI.shield(false), backup: SI.save, ai: SI.link, bot: SI.cart, look: SI.paint, update: SI.arrowUp};      // the tabs whose emoji is drawn as a line icon
const SET_TABS = {
  account: ["🛡 Security", "Two-step sign-in, recovery codes, the secret entry path and your password.", ["Security"]],
  backup: ["💾 Backups", "Encrypted backups of the panel: passphrase, schedule, the latest files.", ["Backups"]],
  ai: ["🔌 Api & Key", "Everything that connects by API: this panel's own tokens and docs, the AI assistant's key, and the state of every other API connection - Telegram, DNS accounts, the probe, the panels.", ["Api & Key"]],
  bot: ["🛒 Shop bot", "The server that runs the shop bot and how the panel reaches it.", ["Shop bot"]],
  look: ["🎨 Appearance", "Theme, day / night, text size, animations.", ["Appearance"]],
  update: ["⬆ About & update", "Update this panel from GitHub with one click, the tunnel cores on your servers, the PasarGuard panels and their nodes, and the settings the service runs with.", ["About & update"]],
};
const SET_OFF = new Set(["bot"]);      // the tabs of Settings that are switched off for now - not in the menu, not in the bar (the owner, 2026-10-16: everything of the Shop bot is off and seen nowhere in the menu)
function settingsTabs() {
  const cur = SET_TABS[state.stab] && !SET_OFF.has(state.stab) ? state.stab : "account";
  state.stab = cur;
  const bar = `<div class="set-top"><div class="tabs set-tabs">${Object.entries(SET_TABS).filter(([k]) => !SET_OFF.has(k)).map(([k, v]) => `<button class="${k === cur ? "on" : ""}" onclick="setTab('${k}')">${TAB_SVG[k] ? `<i class="ti">${TAB_SVG[k]}</i>${v[0].replace(/^\S+\s*/, "")}` : v[0]}</button>`).join("")}</div></div><div class="set-desc">${SET_TABS[cur][1]}</div>`;
  $("#page").insertAdjacentHTML("afterbegin", bar);
  const grid = $("#page .grid2"), byName = {};
  grid.querySelectorAll(":scope > .card").forEach(c => { const h = c.querySelector("h3"); const nm = c.dataset.name || (h ? h.childNodes[0].textContent.trim() : ""); c.dataset.name = nm; byName[nm] = c; c.classList.add("hidden"); });
  const names = SET_TABS[cur][2].filter(n => byName[n]);
  let show = names;
  if (names.length > 1) {   // several cards in a tab: a second row of tabs, one card at a time
    const pick = names.includes(state.sstab) ? state.sstab : names[0];
    state.sstab = pick; show = [pick];
    $("#page .set-desc").insertAdjacentHTML("afterend", `<div class="fstrip"><div class="tabs set-sub">${names.map(n => `<button class="${n === pick ? "on" : ""}" onclick="state.sstab='${esc(n)}';settingsTabs.only()">${esc(n)}</button>`).join("")}</div></div>`);
  }
  show.forEach(n => { const c = byName[n]; c.classList.remove("hidden"); grid.appendChild(c); });
  grid.classList.add("one");
  setW();
}
/* the frame of every settings tab ends exactly where the row of tabs ends - but never narrower than SET_W_MIN: with the Alerts, Probe and Shop bot tabs gone the row got shorter and the frames shrank (the owner, 2026-10-16: "make the frame bigger, back to the old size") */
const SET_W_MIN = 880;
const setW = () => {   // the frame ends a little past the right edge of the LAST tab (the bar itself stretches across the page)
  const t = $("#page .set-tabs"); if (!t) return;
  const left = t.getBoundingClientRect().left, right = Math.max(...[...t.querySelectorAll("button")].map(b => b.getBoundingClientRect().right));
  $("#page").style.setProperty("--set-w", Math.round(Math.max(SET_W_MIN, right - left + 40, [...t.querySelectorAll("button")].reduce((a, b) => a + b.offsetWidth + 8, 0) + 40)) + "px");
};
addEventListener("resize", () => { clearTimeout(setW.t); setW.t = setTimeout(setW, 120); });
settingsTabs.only = () => {   // switch the card without touching the server or the forms: hide / show and redraw the small tab row
  const grid = $("#page .grid2"); if (!grid) return refresh();
  const old = $("#page .set-sub"); if (old) old.remove();
  const byName = {};
  grid.querySelectorAll(":scope > .card").forEach(c => { const nm = c.dataset.name || ""; byName[nm] = c; c.classList.add("hidden"); });
  const names = SET_TABS[state.stab][2].filter(n => byName[n]), pick = names.includes(state.sstab) ? state.sstab : names[0];
  $("#page .set-desc").insertAdjacentHTML("afterend", `<div class="fstrip"><div class="tabs set-sub">${names.map(n => `<button class="${n === pick ? "on" : ""}" onclick="state.sstab='${esc(n)}';settingsTabs.only()">${esc(n)}</button>`).join("")}</div></div>`);
  byName[pick].classList.remove("hidden");
  if (pick === "About & update") aboutLoad(); if (pick === "Core update") enginesLoad();
};
function tabSet(k) { if (SET_OFF.has(k)) return; state.stab = k; state.sstab = null; localStorage.setItem("nk.stab", k); }      // (the tab of Settings that is open; the left menu calls it through go)
window.setTab = k => { tabSet(k); refresh(); };
state.stab = localStorage.getItem("nk.stab") || "account";
window.onLangChange = () => { try { refresh(); } catch (e) { /* not signed in yet */ } };
const formJson = (e, nums = [], bools = []) => { const f = new FormData(e.target), o = {}; for (const [k, v] of f.entries()) o[k] = v; nums.forEach(k => o[k] = +o[k]); bools.forEach(k => o[k] = f.get(k) === "on"); return o; };
const put = async (e, path, body, okText, close) => { e.preventDefault(); try { await api(path, {method: "PUT", body: JSON.stringify(body)}); toast(okText, "ok"); if (close) closeModal(); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
/* the windows of the alerts (Settings → API → Telegram bot): the rules, the daily report, the maintenance windows */
window.alertsWin = async kind => {
  let d; try { d = await setData(); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  const [, , al, rp, , mw] = d, dt = ts => new Date(ts * 1000).toISOString().slice(0, 16), now = Math.floor(Date.now() / 1000);
  if (kind === "rules") {
    const muted = (al.muted || []).length ? `<div class="mut">Muted: ${(al.muted || []).map(m => `<code>${esc(m)}</code> <a onclick="muteRef('${esc(m)}', false); closeModal()">unmute</a>`).join(" · ")}</div>` : "";
    showModal("<b>Alert rules</b>", `<form class="frm alerts-win" onsubmit="return saveRules(event)"><p class="mut">The panel confirms a fault itself first (a few seconds), then waits this long before it tells you; a thing that works again is told after it has held this long. Lower = faster, but a very short blip also gets a message.</p>
      <label>Tell me it is down after <small>seconds</small><input name="down_seconds" type="number" min="0" max="3600" value="${al.down_seconds == null ? 10 : al.down_seconds}"></label>
      <label>Tell me it is up again after <small>seconds</small><input name="up_seconds" type="number" min="0" max="3600" value="${al.up_seconds == null ? 15 : al.up_seconds}"></label>
      <label>Remind every (h) <small>while still down; 0 = never</small><input name="remind_hours" type="number" min="0" max="168" value="${al.remind_hours == null ? 0 : al.remind_hours}"></label>${muted}
      ${[["flap_max", al.flap_max == null ? 3 : al.flap_max], ["flap_quiet_hours", al.flap_quiet_hours == null ? 6 : al.flap_quiet_hours]].map(([k, v]) => `<input type="hidden" name="${k}" value="${v}">`).join("")}
      <div class="frm-act"><button class="pri" type="submit">Save</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`, "narrow");
  } else if (kind === "report") {
    showModal("<b>Daily report</b>", `<form class="frm alerts-win" onsubmit="return saveReport(event)"><label class="chk"><input type="checkbox" name="enabled" ${rp.enabled ? "checked" : ""}> Send a daily report</label>
      <label>At hour <small>local time</small><input name="hour" type="number" min="0" max="23" value="${rp.hour}"></label><input type="hidden" name="tz_offset_minutes" value="${rp.tz_offset_minutes}"><pre id="report-prev" class="gl-out hidden"></pre>
      <div class="frm-act"><button class="pri" type="submit">Save</button><button type="button" onclick="previewReport()">Preview</button><button type="button" onclick="sendReport()">Send now</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`, "narrow");
  } else {
    showModal("<b>Maintenance windows</b>", `<div class="alerts-win"><p class="mut">No alert is sent inside a window.</p>
      ${mw.windows.map(w => `<div class="ap-row"><span>${when(w.start).slice(0, 16)} → ${when(w.end).slice(5, 16)}</span><span class="mut">${esc(w.note)}${w.active ? " · active now" : ""}</span><a class="mut" onclick="delWindow(${w.id})">remove</a></div>`).join("") || '<div class="mut">No window yet.</div>'}
      <form class="frm" onsubmit="return addWindow(event)"><label>From (UTC)<input name="start" type="datetime-local" required value="${dt(now)}"></label><label>To (UTC)<input name="end" type="datetime-local" required value="${dt(now + 3600)}"></label><label>Note<input name="note" placeholder="upgrade of …"></label>
      <div class="frm-act"><button class="pri" type="submit">Add window</button><button type="button" onclick="closeModal()">Close</button></div></form></div>`, "narrow");
  }
};
window.saveTelegram = e => put(e, "/api/settings/telegram", formJson(e, [], ["enabled"]), "✓ Telegram saved");
window.testTelegram = async () => { toast("Sending…"); try { await api("/api/settings/telegram/test", {method: "POST"}); toast("✓ Test message sent", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.saveRules = e => put(e, "/api/settings/alerts", formJson(e, ["down_seconds", "up_seconds", "remind_hours", "flap_max", "flap_quiet_hours"]), "✓ Rules saved", true);
window.muteRef = async (ref, on) => { try { const r = await api(`/api/settings/alerts/mute?ref=${encodeURIComponent(ref)}&muted=${on}`, {method: "POST"}); state.mutedSet = new Set(r.muted || []); toast(on ? "✓ Muted " + ref : "✓ Unmuted " + ref, "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.addWindow = async e => { e.preventDefault(); const f = new FormData(e.target); const b = {start: Math.floor(new Date(f.get("start") + "Z").getTime() / 1000), end: Math.floor(new Date(f.get("end") + "Z").getTime() / 1000), note: f.get("note")}; try { await api("/api/maintenance", {method: "POST", body: JSON.stringify(b)}); toast("✓ Window added", "ok"); refresh(); if ($("#modal .alerts-win")) alertsWin("windows"); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.delWindow = async id => { try { await api(`/api/maintenance/${id}`, {method: "DELETE"}); refresh(); if ($("#modal .alerts-win")) alertsWin("windows"); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.saveProbe = e => { const f = new FormData(e.target); const standby = f.get("standby").split("\n").map(l => l.trim()).filter(Boolean).map(l => { const [name, api_url, api_token] = l.split(",").map(x => (x || "").trim()); return {name, api_url, api_token: api_token || ""}; }); return put(e, "/api/settings/probe", {api_url: f.get("api_url").trim(), api_token: f.get("api_token"), server_id: +f.get("server_id") || 0, standby}, "✓ Probe settings saved"); };
window.testProbe = async () => { toast("Asking the probe…"); try { const r = await api("/api/settings/probe/test", {method: "POST"}); toast(`✓ ${r.probe.active} answers · ${r.configs} configs`, "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.installProbe = async e => { e.preventDefault(); const sid = +new FormData(e.target).get("server_id"); if (!await gconfirm("Install the probe on this Iran server? It copies the probe code and Xray (about 65 MB) over SSH and starts three services. Nothing else on the server is touched.")) return false; toast("Installing… (2–5 minutes)"); try { const r = await api(`/api/settings/probe/install?server_id=${sid}`, {method: "POST"}); toast(`✓ Probe on ${r.server} registered as ${r.role}`, "ok"); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.saveShopbot = e => { const f = new FormData(e.target); return put(e, "/api/settings/shopbot", {host: f.get("host").trim(), ssh_user: f.get("ssh_user").trim() || "root", ssh_port: +f.get("ssh_port") || 22, dir: f.get("dir").trim(), env: f.get("env").trim(), public_url: f.get("public_url").trim()}, "✓ Shop bot settings saved"); };
/* the Doctor's mode and its two settings are saved by apMode / saveDoctor (the Tunnel Doctor page) */
/* CPU on the cores (a tab of the Analyst): a busy server whose network work sits on ONE core gets RPS (the packets are steered to all cores); the Doctor does it by itself when this is on */
const balBody = (bl, srvs) => {
  if (!bl) return "";
  const rows = Object.entries(bl.servers || {}).filter(([, v]) => v && v.name).sort((a, b) => (b[1].skewed - a[1].skewed) || (b[1].at - a[1].at));
  const row = ([id, v]) => `<div class="ap-row bal-row"><b>${esc(v.name)}</b><span class="mut">${v.cores} cores</span><span class="bal-bars" title="${esc((v.busy || []).map((b, i) => "core " + i + ": " + b + " %").join(", "))}">${(v.busy || []).map(b => `<i style="height:${Math.max(6, b)}%" class="${b >= 85 ? "bad" : b >= 60 ? "warn" : ""}"></i>`).join("")}</span>
      <span class="pill ${v.rps_on ? "ok" : v.skewed ? "err" : ""}">${v.rps_on ? "spread (RPS on)" : v.skewed ? "ONE core" : "balanced"}</span><span class="ap-why ${v.rps_on ? "good" : v.skewed ? "" : "calm"}" title="${esc(v.why || "")}">${esc(v.why || "")}</span><small class="mut">${v.at ? ago(v.at) + " ago" : ""}</small>
      ${v.skewed && !v.rps_on ? `<button class="sm pri" onclick="balApply(${id}, true)" title="Steer the network packets to all cores (RPS): at once, no connection is dropped, undone with Undo">⚖ Spread now</button>` : v.rps_on ? `<button class="sm" onclick="balApply(${id}, false)" title="Put the packet steering back as it was">Undo</button>` : ""}<button class="sm" onclick="balScan(${id})" title="Look at this server again (read only)">↻</button></div>`;
  const ir = (srvs || []).filter(s => s.enabled !== false && s.online !== false);
  return `<div class="ana-bar bal-bar"><label class="chk" title="On: a busy server whose network work sits on one core gets RPS from the Tunnel Doctor by itself (every 10 minutes, one server at a time, kept only when it helped). Off: only you do it."><input type="checkbox" ${bl.auto ? "checked" : ""} ${bl.read_only ? "disabled" : ""} onchange="balAuto(this)"> The Doctor spreads it by itself</label><span class="sp"></span>
      <select id="bal-srv" class="sm">${ir.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join("")}</select><button class="sm" onclick="balScan(+$('#bal-srv').value)" title="Look at one server: how its cores share the network work (read only)">🔍 Check a server</button></div>
    <div class="ap-bal-list">${rows.map(row).join("") || '<div class="empty">No busy server yet. A server whose network packets all sit on one core shows here; the Doctor spreads them.</div>'}</div>`;
};
window.balAuto = async cb => { try { await api("/api/balance", {method: "PUT", body: JSON.stringify({auto: cb.checked})}); toast(cb.checked ? "✓ The Doctor spreads the network work by itself" : "The Doctor leaves it to you", "ok"); } catch (e) { cb.checked = !cb.checked; toast("✕ " + e.message, "bad"); } };
window.balScan = async id => { if (!id) return; toast("Looking at the server…"); try { const r = await api(`/api/balance/${id}/scan`, {method: "POST"}); toast(`${r.name}: ${r.why}`, r.skewed ? "bad" : "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.balApply = async (id, on) => {
  if (!await gconfirm(on ? "Steer the network packets of this server to all its cores (RPS)? It takes effect at once, no connection is dropped, and the Doctor checks the result and puts it back if the server gets worse." : "Put the packet steering of this server back as it was?", {danger: false})) return;
  try { const r = await api(`/api/balance/${id}/apply`, {method: "POST", body: JSON.stringify({on})}); showModal("<b>⚖ CPU on the cores</b>", '<div id="ap-story" class="fix"><div class="fx-line run"><i></i>Starting…</div></div>', "wide"); followStory(r.action_id, $("#ap-story"), 120).then(() => refresh()); } catch (e) { toast("✕ " + e.message, "bad"); }
};

window.saveReport = e => put(e, "/api/settings/report", formJson(e, ["hour", "tz_offset_minutes"], ["enabled"]), "✓ Report settings saved", true);
window.previewReport = async () => { const r = await api("/api/report/preview"); const p = $("#report-prev"); p.textContent = r.text; p.classList.remove("hidden"); };
window.sendReport = async () => { toast("Sending…"); try { await api("/api/report/send", {method: "POST"}); toast("✓ Report sent (if Telegram is configured)", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* Restore: pick a backup kept on this server or a file from the computer. The panel part is put in place by the next start (the panel restarts, sign in again); the bot part is loaded into the bot database at once, after a safety dump of it. */
const bkKb = b => b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB";
window.bkRestoreOpen = async part => {
  let d; try { d = await api("/api/settings/backup"); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  const list = (d.backups || []).filter(b => b.part === part).slice(0, 8), label = (d.parts[part] || {}).label || part;
  showModal(`<b>↩ Restore — ${esc(label)}</b>`, `<div class="mut bk-rnote">${part === "panel" ? "Replaces everything the panel knows (servers, tunnels, configs, settings) with the backup, then the panel restarts. What runs now is saved first." : "Replaces the shop bot database (users, orders, payments, plans) with the backup. The current data is dumped on the bot server first."}</div>
    <div class="bk-rlist">${list.map(b => `<div class="bk-rrow"><span>${ago(b.at)} ago</span><small class="mut">${bkKb(b.bytes)}</small><span class="mono ell" title="${esc(b.file)}">${esc(b.file)}</span><button class="sm danger" onclick="bkRestore('${esc(b.file)}')">Restore</button></div>`).join("") || '<div class="mut" style="padding:8px 2px">No backup file of this part on this server yet.</div>'}</div>
    <label class="sm btn bk-rfile" title="A backup you downloaded or got from Telegram (.tar.gz.enc)">📂 Choose a backup file from your computer…<input type="file" hidden accept=".enc" onchange="bkRestoreFile(this)"></label>`);
};
const bkRestoreWarn = file => /^nikatel-bot-/.test(file)
  ? `Restore the shop bot database from ${file}? The bot's current users, orders and payments are replaced by the backup. A dump of the current data is kept on the bot server first.`
  : `Restore the panel from ${file}? Servers, tunnels, configs and settings are replaced by the backup, then the panel restarts and you sign in again. What runs now is saved first (…-before-restore.tar.gz.enc).`;
window.bkRestore = async (file, pw = "", blob = null) => {
  if (!pw && !await gconfirm(bkRestoreWarn(file), {danger: true})) return;
  try {
    toast("Restoring…");
    const r = blob ? await api("/api/backup/restore-upload", {method: "POST", headers: {"Content-Type": "application/octet-stream", "X-Backup-Passphrase": pw}, body: blob})
      : await api("/api/backup/restore", {method: "POST", body: JSON.stringify({file, passphrase: pw})});
    closeModal(); bkDone(r);
  } catch (e) {
    if (/passphrase/i.test(e.message)) { const p = await gprompt("The saved passphrase does not open this file. Type the passphrase it was made with:", ""); if (p) return bkRestore(file, p, blob); return; }
    toast("✕ " + e.message, "bad");
  }
};
window.bkRestoreFile = async inp => { const f = inp.files[0]; if (!f) return; if (f.size > 120e6) { toast("✕ The file is too big for a backup", "bad"); return; } bkRestore(f.name, "", await f.arrayBuffer()); };
function bkDone(r) {
  if (!r.restarts) { toast("✓ " + r.message, "ok"); refresh(); return; }
  showModal("<b>↩ Restored</b>", `<div class="loading"><i class="spin"></i><span>${esc(r.message)}</span></div>`);
  let n = 0, down = false;
  const t = setInterval(async () => {
    n++;
    try { const h = await fetch("/api/health", {cache: "no-store"}); if (h.ok && (down || n > 25)) { clearInterval(t); location.reload(); } else if (!h.ok) down = true; } catch (e) { down = true; }
    if (n > 90) clearInterval(t);
  }, 2000);
}window.bkGen = f => { const a = new Uint8Array(15); crypto.getRandomValues(a); f.passphrase.value = [...a].map(b => "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789".charAt(b % 55)).join("").replace(/(.{5})/g, "$1-").replace(/-$/, ""); f.passphrase.focus(); f.passphrase.select(); toast("Copy this password somewhere safe, then press Set", "ok"); };
window.bkPassToggle = el => { const form = $("#bpw-form"); if (!form) return; if (form.dataset.has === "1") { el.checked = true; form.classList.toggle("hidden"); } else { form.classList.toggle("hidden", !el.checked); const i = form.querySelector("input"); if (el.checked && i) i.focus(); } };      // (a password that is set cannot be switched off - backups are always encrypted: the switch opens the field to REPLACE it; one that is not set opens the field to set it)
window.bkPart = async (k, on) => { try { await api(`/api/settings/backup/part/${k}`, {method: "POST", body: JSON.stringify({enabled: on})}); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); refresh(); } };
window.bkTopic = async k => { toast("Making the topic…"); try { await api(`/api/backup/topic/${k}`, {method: "POST"}); toast("✓ Topic made: its backups go there now", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.bkTopicOff = async k => { if (!await gconfirm("Unlink the topic? Its backups go to the main page of the group again (the topic itself stays in Telegram).")) return; try { await api(`/api/backup/topic/${k}`, {method: "DELETE"}); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };window.savePass = async e => { e.preventDefault(); try { await api("/api/settings/backup/passphrase", {method: "POST", body: JSON.stringify({passphrase: new FormData(e.target).get("passphrase")})}); toast("✓ Passphrase set", "ok"); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.saveBackup = e => { const f = new FormData(e.target), tg = f.get("tg"); return put(e, "/api/settings/backup", {enabled: f.get("enabled") === "on", hour: +f.get("hour") || 0, keep_days: +f.get("keep_days") || 14, every_minutes: +f.get("every_minutes") || 0, send_telegram: tg !== "off", tg_every: tg === "daily" ? "daily" : "all"}, "✓ Backup settings saved"); };
window.backupNow = async part => { toast("Backing up…"); try { const r = await api("/api/backup/now" + (part ? "?part=" + part : ""), {method: "POST"}); toast("✓ " + r.file + (r.sent ? " · sent to Telegram" : ""), "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.pwOutToggle = b => { b.classList.toggle("on"); b.setAttribute("aria-pressed", b.classList.contains("on") ? "true" : "false"); };      // (Sign out of other devices: a button that stays pressed; read by changePassword)
window.changePassword = async e => {
  e.preventDefault();
  const f = new FormData(e.target), out = !!e.target.querySelector(".pwf-out.on");      // (the button Sign out of other devices: on = the other devices are signed out after the change)
  if (f.get("new") !== f.get("again")) { toast("✕ the two new passwords differ", "bad"); return false; }
  try { await api("/api/auth/password", {method: "POST", body: JSON.stringify({current: f.get("current"), new: f.get("new"), logout_others: out})}); toast("✓ Password changed" + (out ? " - every other session was signed out" : ""), "ok"); e.target.reset(); }
  catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};

/* ---- an address for every section (the owner, 2026-10-18: "each section should have its own address - unless it makes the panel slow"): #/tunnels/repairs, #/settings/update, #/pgpanel/users ...
   The address follows the page and its tab (one string compare at every draw - nothing is fetched, nothing is drawn twice), the back button walks back through the sections, a copied link opens the same section, and a link is kept through the sign-in page
   (the page it names is the one the sign-in goes on to). A tab that does not exist opens the page on its first tab; an address that names no page opens the page that was open last. */
function routeOf() { const p = state.page, t = p === "settings" ? state.stab : SUB[p] ? sub(p) : ""; return "#/" + p + (t ? "/" + t : ""); }
function routeWrite() {      // called at the start of every draw: the address says where the panel is
  const h = routeOf(), first = !routeWrite.seen; routeWrite.seen = true;      // (the first draw only SETS the address of the page that was opened - it replaces, it does not add a step; the later ones add a step each)
  if (location.hash.toLowerCase() === h) return;
  try { history[first || routeWrite.fix ? "replaceState" : "pushState"](null, "", h); } catch (e) { /* a browser that refuses: the panel works without an address */ }
}
function routeApply(boot) {      // the address was typed, pasted or walked back to: open its section (false = it names none); `boot`: the panel was just opened - the page is always set up, also when the saved page is the one named
  const m = /^#\/([a-z0-9]+)(?:\/([a-z0-9_-]+))?$/i.exec(location.hash);
  if (!m) return false;
  const page = m[1].toLowerCase(), tab = (m[2] || "").toLowerCase(), alias = NAV_ALIAS[page] ? NAV_ALIAS[page].split("/")[0] : page;
  if (!PAGES[alias] || (alias === "bot" && !SHOPBOT_UI)) return false;
  if (!boot && location.hash.toLowerCase() === routeOf() && state.page === alias) return true;      // (already there: the pop of an address this page wrote itself)
  const ok = tab && (alias === "settings" ? !!SET_TABS[tab] && !SET_OFF.has(tab) : (SUB[alias] || []).some(x => x[0] === tab && x[3] !== "off" && x[3] !== "act"));
  routeWrite.fix = true; try { go(page, ok ? tab : undefined); } finally { routeWrite.fix = false; }      // (an address that was corrected replaces itself in the history, it does not add a step)
  return true;
}
const routeBack = () => { if (routeApply()) return; routeWrite.fix = true; try { routeWrite(); } finally { routeWrite.fix = false; } };      // (an address that names no page is put right in place; never pass the event on: the first argument of routeApply is "the panel was just opened")
window.addEventListener("popstate", routeBack); window.addEventListener("hashchange", routeBack);

/* ---- boot */
navBuild();      // the groups of the left menu: the tabs of Servers, Tunnels and Settings hang under their pages (everything they need is defined above)
(() => {
  const me = api("/api/auth/me").then(r => { showApp(); if (r && r.demo) demoBanner(); }, () => showLogin());
  Promise.resolve().then(() => { if (!routeApply(true)) go(state.page); });      // the page asks for its data at once, beside the sign-in check (a 401 answer shows the sign-in page): one round trip less before the first paint; it starts when the whole file has been read
  return me;
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
  state.open = null; state.edit = null; state.con = null; state.flt = null; state.wtab = null; state.build = null; cf.open = null; cf.edit = null; cf.subEdit = null; cf.add = null; cf.sub = null;
  dockRender(); refresh();
};
window.dockOpen = id => {
  const i = dock.items.findIndex(x => x.id === id); if (i < 0) return;
  const it = dock.items.splice(i, 1)[0], m = $("#modal") || (() => { const x = document.createElement("div"); x.id = "modal"; document.body.appendChild(x); return x; })();
  if (m.firstElementChild) minModal();            // what is open now goes into the bar first
  m.innerHTML = ""; it.ov.classList.remove("docked"); m.appendChild(it.ov); dockRender();
};
window.dockDrop = id => { dock.items = dock.items.filter(x => x.id !== id); dockRender(); };

/* ---- smooth scrolling in every scroll box: the wheel moves a target and the box glides to it (ease-out), the nearest box that can still move takes the wheel, and a box at its end passes it on to the one around it.
   Lightweight: one wheel listener, one animation frame loop that runs only while something glides; off with the animations setting or reduced motion. */
(() => {
  const glide = new WeakMap();      // box -> {to, raf}
  const can = (el, dy) => { const s = getComputedStyle(el); return /(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 1 && (dy < 0 ? el.scrollTop > 0 : el.scrollTop + el.clientHeight < el.scrollHeight - 1); };
  const step = el => {
    const g = glide.get(el); if (!g) return;
    const d = g.to - el.scrollTop;
    if (Math.abs(d) < 0.6) { el.scrollTop = g.to; glide.delete(el); return; }
    el.scrollTop += d * 0.2;
    g.raf = requestAnimationFrame(() => step(el));
  };
  addEventListener("wheel", e => {
    if (e.ctrlKey || e.shiftKey || e.defaultPrevented || document.documentElement.dataset.anim === "off" || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
    const t = e.target; if (!(t instanceof Element) || t.closest("input[type=number], select, [data-nosmooth]")) return;
    const dy = e.deltaMode === 1 ? e.deltaY * 40 : e.deltaMode === 2 ? e.deltaY * innerHeight : e.deltaY;
    let el = t; while (el && el !== document.body && !can(el, dy)) el = el.parentElement;
    if (!el || el === document.body) return;
    e.preventDefault();
    const g = glide.get(el) || {to: el.scrollTop, raf: 0};
    g.to = Math.max(0, Math.min(el.scrollHeight - el.clientHeight, g.to + dy));
    const was = glide.has(el); glide.set(el, g);
    if (!was) g.raf = requestAnimationFrame(() => step(el));
  }, {passive: false});
})();

/* the net of nodes and lines at the foot of the side menu: it drifts softly and small signals run along the lines */
(() => {
  const mk = () => {
    const side = document.querySelector(".side"); if (!side || side.querySelector(".side-net")) return;
    const NS = "http://www.w3.org/2000/svg", W = 360, H = 700, el = (n, a = {}) => { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]); return e; };
    let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    const svg = el("svg", {class: "side-net", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "xMidYMax slice", "aria-hidden": "true"});
    const defs = el("defs"), g = el("linearGradient", {id: "sn-g", x1: 0, y1: 0, x2: 1, y2: 1}); g.append(el("stop", {offset: 0, style: "stop-color:var(--acc-2)"}), el("stop", {offset: 1, style: "stop-color:var(--acc)"}));
    const glow = el("filter", {id: "sn-b", x: "-80%", y: "-80%", width: "260%", height: "260%"}); glow.append(el("feGaussianBlur", {stdDeviation: 2.2})); defs.append(g, glow); svg.append(defs);
    const nodes = []; const cols = 4, rows = 8;
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) { if (rnd() < .18) continue; nodes.push({bx: (c + .5) * W / cols + (rnd() - .5) * 46, by: 250 + (r + .5) * (H - 270) / rows + (rnd() - .5) * 30, ax: 7 + rnd() * 12, ay: 7 + rnd() * 12, sx: .45 + rnd() * .5, sy: .4 + rnd() * .5, px: rnd() * 6.28, py: rnd() * 6.28, r: 2.2 + rnd() * 2.6}); }
    const edges = [], seen = new Set();
    nodes.forEach((a, i) => { nodes.map((b, j) => ({j, d: Math.hypot(a.bx - b.bx, a.by - b.by)})).filter(x => x.j !== i).sort((x, y) => x.d - y.d).slice(0, 3).forEach(({j}) => { const k = i < j ? i + "-" + j : j + "-" + i; if (!seen.has(k)) { seen.add(k); edges.push([i, j]); } }); });
    const lg = el("g", {stroke: "url(#sn-g)", "stroke-width": 1.1, fill: "none", opacity: .55}), ng = el("g", {fill: "url(#sn-g)"}), pg = el("g");
    const lines = edges.map(() => lg.appendChild(el("line"))), dots = nodes.map(n => ng.appendChild(el("circle", {r: n.r})));
    const pulses = Array.from({length: 7}, () => ({e: Math.floor(rnd() * edges.length), t: rnd(), v: .1 + rnd() * .12, halo: pg.appendChild(el("circle", {r: 4.5, fill: "url(#sn-g)", opacity: .5, filter: "url(#sn-b)"})), core: pg.appendChild(el("circle", {r: 1.7, fill: "#fff", opacity: .9}))}));
    svg.append(lg, ng, pg); side.prepend(svg);
    let last = 0;
    const frame = now => {
      requestAnimationFrame(frame);
      if (document.hidden || document.documentElement.dataset.anim !== "rich" || innerWidth <= 900) { if (!last) draw(0); return; }      // the net only drifts in the Rich look
      if (last && now - last < 33) return; const dt = Math.min(.08, (now - (last || now)) / 1000); last = now; draw(now / 1000, dt);      // 30 pictures a second are plenty for a slow drift
    };
    const pos = (n, t) => [n.bx + Math.sin(t * n.sx + n.px) * n.ax, n.by + Math.cos(t * n.sy + n.py) * n.ay];
    const P = nodes.map(() => [0, 0]);
    function draw(t, dt = 0) {
      nodes.forEach((n, i) => { P[i] = pos(n, t); dots[i].setAttribute("cx", P[i][0].toFixed(1)); dots[i].setAttribute("cy", P[i][1].toFixed(1)); });
      edges.forEach(([a, b], i) => { const l = lines[i]; l.setAttribute("x1", P[a][0].toFixed(1)); l.setAttribute("y1", P[a][1].toFixed(1)); l.setAttribute("x2", P[b][0].toFixed(1)); l.setAttribute("y2", P[b][1].toFixed(1)); });
      pulses.forEach(p => { p.t += p.v * dt; if (p.t >= 1) { p.t = 0; p.e = Math.floor(rnd() * edges.length); } const [a, b] = edges[p.e], x = P[a][0] + (P[b][0] - P[a][0]) * p.t, y = P[a][1] + (P[b][1] - P[a][1]) * p.t, o = Math.sin(p.t * Math.PI); p.halo.setAttribute("cx", x.toFixed(1)); p.halo.setAttribute("cy", y.toFixed(1)); p.core.setAttribute("cx", x.toFixed(1)); p.core.setAttribute("cy", y.toFixed(1)); p.halo.setAttribute("opacity", (.55 * o).toFixed(2)); p.core.setAttribute("opacity", (.95 * o).toFixed(2)); });
    }
    draw(0); requestAnimationFrame(frame);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mk); else mk();
})();

/* ---- Tunnels → DNS · IP switch, like the DNS table of ArvanCloud itself: Type · Name · Value · TTL · Cloud · edit / delete, for every domain of every account (ArvanCloud, several accounts; Cloudflare).
   Under one toolbar (Add record, the numbers, the domain chooser, the tabs, the search) ONE box that ends at the bottom of the window.
   Records: all types are listed, the A records can be made, changed, deleted and put back; the cloud switch is the orange cloud. Changes: every record changed by hand (or by the failover) with its Restore.
   Accounts: the API keys. Failover: the preview of a plan and the earlier failovers (the automatic part is paused while it is tested). ---- */
state.dnsTab = localStorage.getItem("nk.dns.tab") || "records";
window.dnsProvPick = btn => { const f = btn.closest("form"), sel = f.elements.provider; sel.value = btn.dataset.v; btn.parentElement.querySelectorAll("button").forEach(b => b.classList.toggle("on", b === btn)); dnsAccHint(f); };
window.dnsPickAcc = id => { try { localStorage.setItem("nk.dns.acc", id); localStorage.setItem("nk.dns.sel", "*"); } catch (e) { /* private window */ } refresh(); };      // switch between the accounts (ArvanCloud, another ArvanCloud, Cloudflare ...)
window.dnsSetTab = t => { state.dnsTab = t; try { localStorage.setItem("nk.dns.tab", t); } catch (e) { /* private window */ } refresh(); };
const dnsKey = r => `${r.account}|${r.domain}|${r.id}`;
const dnsTtl = v => { v = +v; if (v === 1) return "Auto"; if (!v) return "—"; if (v < 3600) return v / 60 + " min"; if (v < 86400) return v / 3600 + " h"; return v / 86400 + " d"; };
const DNS_CLOUD = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 18a4.5 4.5 0 01-.6-8.96A6 6 0 0117.8 8.6 4.7 4.7 0 0117 18H7z"/></svg>';
PAGES.dns = async () => {
  renderSub();
  const fresh = state.dnsFresh ? "?fresh=true" : ""; state.dnsFresh = false;
  const [rr, accs, fo, all] = await Promise.all([api("/api/dns/records" + fresh).catch(e => ({err: e.message, records: [], accounts: []})), api("/api/dns/accounts").catch(() => ({accounts: [], providers: {}})), api("/api/failover").catch(() => null), api("/api/servers").catch(() => [])]);
  const byIp = {}; all.forEach(s => { byIp[s.host] = s; });
  let recs = (rr.records || []).filter(r => (r.editable !== false || r.deletable) && !(r.type === "NS" && (r.name === "@" || r.name === ""))), log = (fo && fo.dns_log) || [], accounts = accs.accounts || [], live = rr.accounts || [];      // the records that cannot be changed (MX, NS ...) are not listed
  const accPick = (() => { let v = "*"; try { v = localStorage.getItem("nk.dns.acc") || "*"; } catch (e) { /* private window */ } return accounts.some(a => a.id === v) ? v : (accounts[0] ? accounts[0].id : "*"); })();      // one account at a time (the first one until you choose)      // the account chosen in the bar above ("*" = all of them)
  if (accPick !== "*") recs = recs.filter(r => r.account === accPick);
  const tab = !accounts.length ? "accounts" : (["records", "changes", "accounts", "failover"].includes(state.dnsTab) ? state.dnsTab : "records");      // without an account only the Accounts tab works
  const nA = recs.filter(r => r.type === "A").length, onPanel = recs.filter(r => r.type === "A" && r.ips.some(ip => byIp[ip])).length, nDom = new Set(recs.map(r => r.account + "|" + r.domain)).size;
  const chip = (cls, label, val, tip = "") => `<span class="tc ${cls}" title="${esc(tip)}">${label} <b>${val}</b></span>`;
  const bad = live.filter(a => !a.ok), connected = accounts.length > 0 && live.some(a => a.ok);
  const conn = !accounts.length ? chip("warn", "DNS", "no account") : bad.length ? chip("bad", "Accounts", `${live.length - bad.length} of ${live.length} connected`, bad.map(a => a.name + ": " + a.error).join("\n")) : chip("ok", "Accounts", accounts.length + " connected");
  state.dnsAccounts = live; state.dnsServers = all;
  // the domain chooser: grouped by account
  const sel = localStorage.getItem("nk.dns.sel") || "";
  const opts = live.filter(a => a.ok && (accPick === "*" || a.id === accPick)).map(a => ({a, doms: (a.domains || []).slice().sort()})).filter(x => x.doms.length);
  const valid = new Set(["*", ...opts.flatMap(x => x.doms.map(d => x.a.id + "|" + d))]), cur = valid.has(sel) ? sel : (opts[0] ? opts[0].a.id + "|" + opts[0].doms[0] : "*");
  const cnt = (id, d) => recs.filter(r => r.account === id && r.domain === d).length;
  const provOf = (() => { const id = cur === "*" ? (accPick !== "*" ? accPick : (live.find(a => a.ok) || {}).id) : String(cur).split("|")[0]; return (accounts.find(a => a.id === id) || {}).provider || "arvan"; })();      // the icon of the chooser is the cloud of the provider of the chosen domain
  const domSel = `<label class="dns-dsel" data-prov="${provOf}" title="Choose a domain: its records are listed below"><span>Domain</span><select onchange="dnsPickDom(this.value)">${opts.map(({a, doms}) => `<optgroup label="${esc(a.name)}">${doms.map(d => `<option value="${esc(a.id + "|" + d)}" ${cur === a.id + "|" + d ? "selected" : ""}>${esc(d)} · ${cnt(a.id, d)}</option>`).join("")}</optgroup>`).join("")}<option value="*" ${cur === "*" ? "selected" : ""}>All domains · ${recs.length}</option></select></label>`;
  const addBtn = tab === "records" && connected ? `<button class="cta cta-server" onclick="dnsForm('')" title="Make a new A record in one of your domains"><i class="cta-ic">＋</i>Add record</button><i class="sep"></i>` : "";      // (Add account is in the bar of the accounts above, on every tab: one button, not two)
  const tabs = [["records", "Records", recs.length], ["changes", "Changes", log.length], ["accounts", "Accounts", accounts.length], ["failover", "Failover", 0]];
  const dstat = (label, val, tip = "") => `<span class="dns-stat" title="${esc(tip)}"><span class="lab">${label} </span><b>${val}</b></span>`;
  const accChips = accounts.map(a => { const l = live.find(x => x.id === a.id) || {}, ok = l.ok !== undefined ? l.ok : (a.status || {}).ok; return `<span class="dns-cw"><button type="button" class="dns-chip ${accPick === a.id ? "on" : ""}" onclick="dnsPickAcc('${esc(a.id)}')" title="${esc(ok ? a.name : (l.error || "this account does not answer"))}"><i class="pv pv-${esc(a.provider)}"></i><b>${esc(a.name)}</b>${ok && l.domains ? `<span class="cnt" title="${l.domains.length} domains">${l.domains.length}</span>` : ""}<i class="dot ${ok ? "ok" : "bad"}" title="${ok ? "connected" : "does not answer"}"></i></button><button type="button" class="dns-x" onclick="dnsAccDel('${esc(a.id)}', '${esc(a.name)}')" title="Remove the account ${esc(a.name)} from this panel (nothing changes at the provider)" aria-label="Remove ${esc(a.name)}">${X_SVG}</button></span>`; }).join("");      // each account box has a faint cross: it takes the account off the list (after a confirmation)
  const bar = `<div class="toolbar joined srv-bar dns-bar"><div class="dns-r dns-accbar"><span class="dns-lab">Accounts</span>${accChips}<button type="button" class="cta cta-server dns-add" onclick="dnsAccForm('')" title="Add an ArvanCloud account (another one) or a Cloudflare account" aria-label="Add account"><i class="cta-ic">＋</i>Add account</button></div>
    <div class="dns-r dns-main">${addBtn}${tab === "records" && connected ? domSel : ""}<span class="dns-stats" title="The panel never changes an IP by itself while this is being tested. You can still change a record by hand.">${connected ? dstat("Domains", nDom) + dstat("A records", nA) + dstat("On the panel", onPanel, "A records that point at a server added to this panel") : conn}</span>
      <span class="sp"></span><div class="tabs srv-tabs">${tabs.map(([k, l, n]) => `<button class="${k === tab ? "on" : ""}" ${!accounts.length && k !== "accounts" ? 'disabled title="Add an account first"' : ""} onclick="dnsSetTab('${k}')">${l}${n ? ` <i class="cnt">${n}</i>` : ""}</button>`).join("")}</div>${tab === "records" && connected ? searchSeg() : ""}</div></div>`;
  let body = "";

  if (tab === "records") body = !accounts.length ? dnsEmpty() : dnsRecords(recs, rr.err, byIp, cur, live);
  else if (tab === "changes") body = dnsChanges(log, accounts);
  else if (tab === "accounts") body = dnsAccounts(accounts, live);
  else body = dnsFailover(fo, all.filter(s => s.role === "iran"), connected);

  $("#page").innerHTML = bar + `<div class="lbox full fx-box dns-box">${body}</div>`;
  applyQ(); fxFit();
};
const dnsEmpty = () => `<div class="card dns-pane"><h3>🌐 No DNS account yet</h3><div class="mut">Add the API key of your ArvanCloud account (or a Cloudflare token) with <b>Add account</b> above: the panel then lists the DNS records of your domains like the account's own DNS page, and lets you add, change and delete A records.</div></div>`;
function dnsRecords(recs, err, byIp, cur, live) {
  const failed = live.filter(a => !a.ok).map(a => `<div class="err" style="margin:0 0 6px">${esc(a.name)}: ${esc(a.error)}</div>`).join("");
  const list = (cur === "*" ? recs : recs.filter(r => r.account + "|" + r.domain === cur)).slice().sort((a, b) => (a.type === "A" ? 0 : 1) - (b.type === "A" ? 0 : 1) || a.fqdn.localeCompare(b.fqdn) || a.type.localeCompare(b.type));
  state.dnsRecs = {}; recs.forEach(r => { state.dnsRecs[dnsKey(r)] = r; });
  const multi = new Set(recs.map(r => r.account)).size > 1, accName = id => (live.find(a => a.id === id) || {}).name || id;
  const row = r => { const k = esc(dnsKey(r)), ed = r.editable, short = cur === "*" ? r.fqdn : r.name;
    const val = r.type === "A" ? r.ips.map(ip => `<span class="mono">${esc(ip)}</span>${byIp[ip] ? ` <span class="mchip srvtag ${byIp[ip].role === "iran" ? "ok" : ""}" title="A server of this panel">${esc(byIp[ip].name)}</span>` : ""}`).join(" · ") : `<span class="mut">${esc(r.value)}</span>`;
    return `<tr class="dr-rec ${ed ? "" : "ro"}" data-q="${esc([r.type, r.fqdn, r.value, r.ips.map(ip => byIp[ip] ? byIp[ip].name : "").join(" "), accName(r.account)].join(" ").toLowerCase())}">
      <td class="ty"><span class="tybadge">${esc(r.type)}</span></td>
      <td class="name" dir="ltr" title="${esc(r.fqdn)}">${esc(short)}${multi && cur === "*" ? ` <small class="mut">${esc(accName(r.account))}</small>` : ""}</td>
      <td class="val">${val}</td>
      <td class="dns-port">${r.port ? `<span class="port-pill" title="Port">${esc(r.port)}</span>` : '<span class="mut">—</span>'}</td>
      <td class="mut">${dnsTtl(r.ttl)}</td>
      <td class="cl">${r.cloud === null ? "" : `<span class="cl-ic ${r.cloud ? "on" : ""}" title="${r.cloud ? "Through the CDN" : "DNS only"}">${DNS_CLOUD}</span>${ed ? sbSw(!!r.cloud, `dnsCloud('${k}')`, "Cloud", "Turn the CDN (orange cloud) on or off for this record").replace(/<em[^>]*>.*?<\/em>/, "") : ""}`}</td>
      <td class="dr-act">${ed ? `<button type="button" class="ic" title="Edit this record" onclick="dnsForm('${k}')">✎</button><button type="button" class="ic danger" title="Delete this record (kept under Changes with a Restore)" onclick="dnsDel('${k}')">🗑</button>` : r.deletable ? `<button type="button" class="ic danger" title="Delete this record (kept under Changes with a Restore). To change it, delete it and make it again." onclick="dnsDel('${k}')">🗑</button>` : ""}</td></tr>`; };
  return `${failed}<table class="tbl lift srv-tbl dns-tbl joined"><colgroup><col style="width:7%"><col style="width:21%"><col style="width:19%"><col style="width:8%"><col style="width:12%"><col style="width:20%"><col style="width:13%"></colgroup>
    <thead><tr><th>Type</th><th>Name</th><th>Value</th><th>Port</th><th>TTL</th><th>Cloud</th><th></th></tr></thead><tbody>${list.map(row).join("") || '<tr><td colspan="7" class="empty">No record in this domain.</td></tr>'}</tbody></table>`;
}
window.dnsPickDom = d => { try { localStorage.setItem("nk.dns.sel", d); } catch (e) { /* private window */ } refresh(); };
window.dnsCloud = async k => {
  const r = (state.dnsRecs || {})[k]; if (!r || !r.editable) return;
  try { await api(`/api/dns/records/${encodeURIComponent(r.account)}/${encodeURIComponent(r.domain)}/${encodeURIComponent(r.id)}`, {method: "PUT", body: JSON.stringify({account: r.account, domain: r.domain, name: r.name, ips: r.ips, ttl: +r.ttl || 120, cloud: !r.cloud})}); toast(r.cloud ? "Cloud turned off" : "Cloud turned on", "ok"); state.dnsFresh = true; refresh(); }
  catch (e) { toast("✕ " + e.message, "bad"); refresh(); }
};
/* make / change / delete an A record by hand (each kept under Changes with a Restore) */
window.dnsForm = k => {
  const r = k ? (state.dnsRecs || {})[k] : null, accs = (state.dnsAccounts || []).filter(a => a.ok);
  if (k && !r) { toast("Read the records again (↻) first", "bad"); return; }
  const sel = (localStorage.getItem("nk.dns.sel") || "").split("|"), acc = r ? r.account : (accs.find(a => a.id === (localStorage.getItem("nk.dns.acc") || "")) || accs.find(a => a.id === sel[0]) || accs[0] || {}).id, a0 = accs.find(a => a.id === acc) || {}, dom = r ? r.domain : (sel[1] && (a0.domains || []).includes(sel[1]) ? sel[1] : (a0.domains || [])[0] || "");
  const ttls = (a0.ttls || [[120, "2 minutes"]]), ttl = r && r.ttl ? +r.ttl : (ttls[0] ? ttls[0][0] : 120), servers = (state.dnsServers || []).filter(s => s.enabled), chev = '<svg viewBox="0 0 16 16"><path d="M3.5 6l4.5 4.5L12.5 6"/></svg>';
  const sw = (inner, extra = "") => `<div class="qp-sw" ${extra}>${inner}${chev}</div>`;
  const fld = (cls, label, inner, tip = "") => `<div class="qp-f ${cls}"${tip ? ` title="${esc(tip)}"` : ""}><label>${label}</label>${inner}</div>`;
  showModal(`<b>${r ? "✎ Edit record" : "＋ Add record"}</b>`, `<form class="qp qp2" onsubmit="return dnsSave(event, ${r ? `'${esc(k)}'` : "''"})">
    <div class="qp2-grid">
      ${fld("c2", "Account", sw(`<select class="qp-sel" name="account" ${r ? "disabled" : ""} onchange="dnsFormAcc(this.form)">${accs.map(a => `<option value="${esc(a.id)}" ${a.id === acc ? "selected" : ""}>${esc(a.name)}${/arvan|cloudflare/i.test(a.name) ? "" : " (" + (a.provider === "arvan" ? "ArvanCloud" : "Cloudflare") + ")"}</option>`).join("")}</select>`))}
      ${fld("c2", "Domain", sw(`<select class="qp-sel" name="domain" ${r ? "disabled" : ""} onchange="dnsPrev(this.form)">${(a0.domains || []).map(d => `<option ${d === dom ? "selected" : ""}>${esc(d)}</option>`).join("")}</select>`))}
      ${fld("c2", "TTL", sw(`<select class="qp-sel" name="ttl">${ttls.map(([v, l]) => `<option value="${v}" ${v === ttl ? "selected" : ""}>${l}</option>`).join("")}${ttls.some(t => t[0] === ttl) ? "" : `<option value="${ttl}" selected>${ttl} s</option>`}</select>`))}
      ${fld("c1", "Type", sw(`<select class="qp-sel" name="rtype" ${r ? "disabled" : ""} onchange="dnsFormAcc(this.form)">${["A", "AAAA", "CNAME", "TXT"].map(t => `<option ${((r && r.type) || "A") === t ? "selected" : ""}>${t}</option>`).join("")}</select>`), "What kind of record: A (IPv4), AAAA (IPv6), CNAME (an alias) or TXT (a text)")}
      ${fld("c3", "Name", `<div class="qp2-name"><input class="qp-in" name="name" value="${esc(r ? r.name : "")}" required maxlength="120" placeholder="sub  (@ = the domain itself)" autocomplete="off" oninput="dnsPrev(this.form)"><span class="qp2-suf" id="dns-suf"></span></div>`)}
      ${fld("c2 dns-cloudf", "Cloud", `<label class="qp2-cloud" title="Through the CDN (the orange cloud)"><input type="checkbox" class="sw" name="cloud" ${(r ? r.cloud : true) ? "checked" : ""}><span>CDN</span></label>`)}
      ${fld("c4", `<span id="dns-addrlab">Address</span> <small id="dns-ipnote"></small>`, `<input class="qp-in" name="ips" value="${esc(r ? (r.type === "A" ? r.ips.join(", ") : r.value) : "")}" required placeholder="203.0.113.10  (several: separate with commas)" autocomplete="off" dir="ltr" oninput="dnsPrev(this.form)">`, "Several addresses of an A record, separated by commas, share the traffic.")}
      ${fld("c2 dns-arv", "Port", `<input class="qp-in" name="port" type="number" min="1" max="65535" value="${r && r.port ? esc(r.port) : ""}" data-orig="${r && r.port ? esc(r.port) : ""}" placeholder="none" autocomplete="off" dir="ltr">`, "The port of the address. Empty = none.")}
      ${fld("c3 dns-ip", "Or a server of the panel", sw(`<select class="qp-sel" onchange="if(this.value){this.form.elements.ips.value=this.value;dnsPrev(this.form);this.selectedIndex=0}"><option value="">choose a server…</option>${servers.map(s => `<option value="${esc(s.host)}">${esc(s.name)} — ${esc(s.host)}</option>`).join("")}</select>`))}
      ${fld("c3 dns-arv", "Protocol to the origin", sw(`<select class="qp-sel" name="upstream" onchange="dnsFormSync(this.form)" data-orig="${esc((r && r.upstream) || "default")}">${[["default", "Default"], ["auto", "Auto"], ["http", "HTTP"], ["https", "HTTPS"]].map(([v, l]) => `<option value="${v}" ${((r && r.upstream) || "default") === v ? "selected" : ""}>${l}</option>`).join("")}</select>`), "How ArvanCloud talks to your server when the cloud is on.")}
    </div>
    <div class="qp2-foot"><div class="qp-prev" id="dns-prev"></div><button class="qp-pri" type="submit">${r ? "Save" : "Make the record"}</button></div></form>`, "plan-add keep");
  setTimeout(() => { const f = document.querySelector(".modal.plan-add form"); if (f) { dnsFormAcc(f, true); dnsFormSync(f); dnsPrev(f); f.elements.name.focus(); } }, 60);
  if (r && a0.provider === "arvan") api(`/api/dns/records/${encodeURIComponent(r.account)}/${encodeURIComponent(r.domain)}/${encodeURIComponent(r.id)}`).then(d => {      // the list may not carry the origin protocol: the record is read fresh and the two fields are filled in
    const f = document.querySelector(".modal.plan-add form"), row = d.record || {}; if (!f) return;
    const up = f.elements.upstream, po = f.elements.port;
    if (up && row.upstream && up.value === up.dataset.orig) { up.value = row.upstream; up.dataset.orig = row.upstream; }
    if (po && po.value === (po.dataset.orig || "") && row.port) { po.value = row.port; po.dataset.orig = String(row.port); }
  }).catch(() => { /* the form still works with what the list had */ });
};
window.dnsFormAcc = (f, keep) => {      // the account decides the domains and the TTL choices
  const a = (state.dnsAccounts || []).find(x => x.id === f.elements.account.value) || {}, d = f.elements.domain, t = f.elements.ttl;
  if (!keep && !d.disabled) { d.innerHTML = (a.domains || []).map(x => `<option>${esc(x)}</option>`).join(""); t.innerHTML = (a.ttls || []).map(([v, l]) => `<option value="${v}">${l}</option>`).join(""); }
  const n = $("#dns-ipnote"); if (n) n.textContent = a.provider === "cloudflare" ? "(Cloudflare: ONE address per record)" : "";
  dnsFormSync(f);
  dnsPrev(f);
};
window.dnsFormSync = f => {      // which fields a record needs depends on its type and on the provider
  const prov = (state.dnsAccounts || []).find(x => x.id === f.elements.account.value) || {}, t = f.elements.rtype ? f.elements.rtype.value : "A";
  const show = (sel, on) => f.querySelectorAll(sel).forEach(e => { e.style.display = on ? "" : "none"; });
  show(".dns-arv", prov.provider !== "cloudflare" && t === "A"); show(".dns-ip", t === "A"); show(".dns-cloudf", t !== "TXT");
  const po = f.elements.port, up = f.elements.upstream, web = !!up && /^https?$/.test(up.value);      // a port is only for a record that talks HTTP or HTTPS to the origin
  if (po) { po.disabled = !web; po.placeholder = web ? "none" : "HTTP / HTTPS only"; po.title = web ? "" : "The port is used only when the protocol to the origin is HTTP or HTTPS"; }
  const lab = $("#dns-addrlab"); if (lab) lab.textContent = {A: "Address", AAAA: "IPv6 address", CNAME: "Points to (host name)", TXT: "Text"}[t];
  f.elements.ips.placeholder = {A: "203.0.113.10  (several: separate with commas)", AAAA: "2001:db8::1", CNAME: "target.example.com", TXT: "the text of the record"}[t];
};
window.dnsPrev = f => { const suf = $("#dns-suf"); if (suf && f) suf.textContent = "." + f.elements.domain.value; const el = $("#dns-prev"); if (!el || !f) return; const n = f.elements.name.value.trim().toLowerCase(), d = f.elements.domain.value, ips = (f.elements.rtype && /^(CNAME|TXT)$/.test(f.elements.rtype.value) ? [f.elements.ips.value.trim()] : f.elements.ips.value.split(/[,\s]+/)).filter(Boolean); el.innerHTML = n && ips.length ? `<b dir="ltr">${esc(n === "@" ? d : n + "." + d)}</b> <span class="mut">→</span> <span class="mono">${esc(ips.join(" · "))}</span>` : ""; };
window.dnsSave = async (e, k) => {
  e.preventDefault(); const f = e.target, r = k ? (state.dnsRecs || {})[k] : null, body = {account: r ? r.account : f.elements.account.value, domain: r ? r.domain : f.elements.domain.value, name: f.elements.name.value.trim(), ips: f.elements.ips.value.split(/[,\s]+/).filter(Boolean), ttl: +f.elements.ttl.value, cloud: f.elements.cloud.checked};
  const rt = f.elements.rtype ? f.elements.rtype.value : "A"; body.type = rt; if (rt === "CNAME" || rt === "TXT") { body.value = f.elements.ips.value.trim(); body.ips = []; if (rt === "TXT") body.cloud = false; }
  const po = f.elements.port, up = f.elements.upstream;      // only what was changed is sent: a record with several ports keeps them when you leave the port alone
  if (po && !po.disabled && po.value !== (po.dataset.orig || "")) body.port = po.value === "" ? 0 : +po.value;
  if (up && up.value !== (up.dataset.orig || "default")) body.upstream = up.value;
  const btn = f.querySelector(".qp-pri"); btn.disabled = true; formErr(f, "");
  try { await api(r ? `/api/dns/records/${encodeURIComponent(r.account)}/${encodeURIComponent(r.domain)}/${encodeURIComponent(r.id)}` : "/api/dns/records", {method: r ? "PUT" : "POST", body: JSON.stringify(body)}); closeModal(); toast(r ? "✓ Record changed" : "✓ Record made", "ok"); state.dnsFresh = true; refresh(); }
  catch (err) { btn.disabled = false; formErr(f, err.message); }
  return false;
};
window.dnsDel = async k => {
  const r = (state.dnsRecs || {})[k]; if (!r) return;
  if (!await gconfirm(`Delete ${r.fqdn} (${r.value})? It is kept under Changes: Restore makes it again.`, {danger: true})) return;
  try { await api(`/api/dns/records/${encodeURIComponent(r.account)}/${encodeURIComponent(r.domain)}/${encodeURIComponent(r.id)}`, {method: "DELETE"}); toast("✓ Record deleted", "ok"); state.dnsFresh = true; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
function dnsChanges(log, accounts) {
  const an = id => (accounts.find(a => a.id === (id || "arvan")) || {}).name || "ArvanCloud", opName = {create: "made", edit: "changed", delete: "deleted"};
  const row = e => `<tr class="${e.restored ? "" : "up"}"><td class="mut" title="${when(e.at)}">${ago(e.at)} ago</td><td class="name" dir="ltr">${esc(e.fqdn)}</td><td class="mono">${e.op ? esc((e.old || "—") + " → " + (e.new || "—")) : esc(e.old) + " → " + esc(e.new)}</td><td>${e.op ? esc(an(e.account)) + " · by hand · " + opName[e.op] : esc(e.server) + (e.target && e.target !== e.server ? " → " + esc(e.target) : "")}</td>
    <td>${e.restored ? `<span class="pill" title="${e.restored_at ? when(e.restored_at) : ""}">restored</span>` : '<span class="pill ok">active</span>'}</td>
    <td>${e.restored ? "" : `<button class="sm" onclick="foRestore('${esc(e.id)}')" title="Put this change back">↩ Restore</button>`}</td></tr>`;
  return `<table class="tbl lift srv-tbl joined"><colgroup><col style="width:11%"><col style="width:24%"><col style="width:22%"><col style="width:23%"><col style="width:9%"><col style="width:11%"></colgroup>
    <thead><tr><th>When</th><th>Name</th><th>Address</th><th>Who</th><th>State</th><th></th></tr></thead>
    <tbody>${log.map(row).join("") || '<tr><td colspan="6" class="empty">No record has been changed yet. Every record you make, change or delete (or the failover re-points) is listed here and can be put back with one click.</td></tr>'}</tbody></table>`;
}
function dnsAccounts(accounts, live) {
  const row = a => { const l = live.find(x => x.id === a.id) || {}, s = a.status || {}, ok = l.ok !== undefined ? l.ok : s.ok;
    return `<tr><td><span class="tybadge ${a.provider}">${esc(a.provider_name)}</span></td><td class="name">${esc(a.name)}${a.main ? ' <small class="mut">(main · the failover uses it)</small>' : ""}</td><td class="mono">${a.key_tail ? "•••• " + esc(a.key_tail) : "••••"}</td>
      <td>${ok ? (l.domains ? l.domains.length : s.domains || 0) + " domains" : ""}</td><td>${ok ? '<span class="pill ok">connected</span>' : `<span class="pill err" title="${esc(l.error || s.error || "")}">${l.error || s.error ? "not working" : "not tested"}</span>`}</td>
      <td class="dr-act"><button class="sm" onclick="dnsAccTest('${esc(a.id)}')" title="Try the key (read only)">Test</button><button class="ic" title="Change the name or the key" onclick="dnsAccForm('${esc(a.id)}')">✎</button><button class="ic danger" title="Remove this account from the panel (nothing changes at the provider)" onclick="dnsAccDel('${esc(a.id)}', '${esc(a.name)}')">🗑</button></td></tr>`; };
  return `<table class="tbl lift srv-tbl joined"><colgroup><col style="width:13%"><col style="width:27%"><col style="width:12%"><col style="width:14%"><col style="width:14%"><col style="width:20%"></colgroup>
    <thead><tr><th>Provider</th><th>Name</th><th>Key</th><th>Domains</th><th>State</th><th></th></tr></thead>
    <tbody>${accounts.map(row).join("") || '<tr><td colspan="6" class="empty">No account yet. Add an ArvanCloud account or a Cloudflare token with “＋ Add account”.</td></tr>'}</tbody></table>`;
}
window.dnsAccForm = id => {
  const a = id ? (state.dnsAccounts || []).find(x => x.id === id) : null, chev = '<svg viewBox="0 0 16 16"><path d="M3.5 6l4.5 4.5L12.5 6"/></svg>';
  showModal(`<b>${a ? "✎ Edit account" : "＋ Add account"}</b>`, `<form class="qp" onsubmit="return dnsAccSave(event, '${esc(id || "")}')">
    <p class="qp-sub">${a ? "Change the name, or paste a new key (it is tried first)." : "The key is tried before it is saved (the domains are listed - read only). It stays on this server and never comes back to the browser."}</p>
    <div class="qp-grid">
      ${a ? "" : `<div class="qp-f"><label>Provider</label><select class="hidden" name="provider" onchange="dnsAccHint(this.form)"><option value="arvan">ArvanCloud</option><option value="cloudflare">Cloudflare</option></select><div class="prov-pick"><button type="button" class="on" data-v="arvan" onclick="dnsProvPick(this)"><i class="pv pv-arvan"></i>ArvanCloud</button><button type="button" data-v="cloudflare" onclick="dnsProvPick(this)"><i class="pv pv-cloudflare"></i>Cloudflare</button></div></div>`}
      <div class="qp-f" ${a ? 'style="grid-column:1/-1"' : ""}><label>Name <small>(to tell the accounts apart)</small></label><input class="qp-in" name="name" value="${esc(a ? a.name : "")}" maxlength="40" placeholder="${a ? "" : "e.g. My second account"}" autocomplete="off"></div>
      <div class="qp-f" style="grid-column:1/-1"><label id="dns-keylab">API key</label><input class="qp-in" name="key" type="password" ${a ? "" : "required"} placeholder="${a ? "empty keeps the saved key" : "paste the key here"}" autocomplete="new-password" data-lpignore="true" dir="ltr"></div></div>
    <div class="qp-prev" id="dns-keyhint"></div>
    <div class="qp-act"><span class="qp-hint"></span><button class="qp-pri" type="submit">${a ? "Save" : "Add the account"}</button></div></form>`, "plan-add keep");
  setTimeout(() => { const f = document.querySelector(".modal.plan-add form"); if (f && f.elements.provider) dnsAccHint(f); }, 60);
};
window.dnsAccHint = f => { const cf = f.elements.provider.value === "cloudflare"; $("#dns-keylab").textContent = cf ? "Cloudflare API token" : "ArvanCloud API key"; $("#dns-keyhint").innerHTML = cf ? "Cloudflare dashboard → My Profile → <b>API Tokens</b> → Create Token → <b>Edit zone DNS</b> template (permissions: Zone · DNS · Edit, Zone · Zone · Read) for the zones you want." : "panel.arvancloud.ir → profile → <b>Machine Users</b> → give it the <b>CDN</b> service with a manager role → create an access key and paste it (shown once)."; };
/* an error of a form shows INSIDE the form, in full, in a box above the button (not as a clipped note on the edge of the window) */
window.formErr = (f, msg) => { let b = f.querySelector(".qp-err"); if (!msg) { if (b) b.remove(); return; } if (!b) { b = document.createElement("div"); b.className = "qp-err"; b.setAttribute("role", "alert"); const anchor = f.querySelector(".qp2-foot, .qp-act"); if (anchor) anchor.before(b); else f.append(b); } b.innerHTML = `<i>!</i><span>${esc(String(msg))}</span>`; };
window.dnsAccSave = async (e, id) => {
  e.preventDefault(); const f = e.target, btn = f.querySelector(".qp-pri"); btn.disabled = true; formErr(f, "");
  try {
    if (id) await api("/api/dns/accounts/" + encodeURIComponent(id), {method: "PUT", body: JSON.stringify({name: f.elements.name.value, key: f.elements.key.value})});
    else await api("/api/dns/accounts", {method: "POST", body: JSON.stringify({provider: f.elements.provider.value, name: f.elements.name.value, key: f.elements.key.value})});
    closeModal(); toast(id ? "✓ Account saved" : "✓ Account added - its domains are listed", "ok"); state.dnsFresh = true; refresh();
  } catch (err) { btn.disabled = false; formErr(f, err.message); }
  return false;
};
window.dnsAccTest = async id => { toast("Asking the provider (read only)…"); try { const r = await api(`/api/dns/accounts/${encodeURIComponent(id)}/test`, {method: "POST"}); toast(`✓ Connected: ${r.domains.length} domain(s), ${r.records} record(s)`, "ok"); state.dnsFresh = true; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); refresh(); } };
window.dnsAccDel = async (id, name) => { if (!await gconfirm(`Remove the account ${name} from the panel? Nothing changes at the provider; its records are no longer listed here.`, {danger: true})) return; try { await api("/api/dns/accounts/" + encodeURIComponent(id), {method: "DELETE"}); toast("✓ Account removed", "ok"); state.dnsFresh = true; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
/* CDN → Failover: the Doctor's mode decides (Tunnels → Tunnel Doctor); here only the preview and what was done */
function dnsFailover(fo, irn, connected) {
  const his = h => `<div class="ev ${h.ok === true ? "ok" : h.ok === false ? "bad" : "warn"}"><time title="${when(h.at)}">${ago(h.at)} ago</time><i></i><small>${h.reason && h.reason !== "down" ? esc(h.reason) + " · " : ""}${h.undone ? "undone" : "done"}</small><span><b>${esc(h.server)}</b> → ${h.target ? esc(h.target.name) : "?"}: ${h.moved}/${h.tunnels} tunnels, ${h.dns.length} DNS record(s)${h.errors.length ? ", errors: " + esc(h.errors.join("; ")) : ""} ${h.dns.length && !h.undone ? `<a class="mut" onclick="foUndo('${esc(h.id)}')">↩ point the DNS back</a>` : ""}</span></div>`;
  const mode = (fo && fo.mode) || "off", can = !!(fo && fo.can_move), label = {off: "off", fix: "fix tunnels", move: "fix + move the Iran server"}[mode] || mode;
  return `<div class="dns-pane dns-fo">
    <div class="card"><h3>🩺 The Tunnel Doctor decides <span class="sp"></span><span class="pill ${can ? "ok" : "warn"}">${can ? "moves by itself" : "does not move servers"}</span></h3>
      <div class="mut">Its mode is <b>${label}</b>. ${can ? `An Iran server that is off for ${fo.confirm_minutes} minutes gives its tunnels to another Iran server, and the records that carried its address are re-pointed in every account (ArvanCloud, Cloudflare); each change is under Changes with a Restore.` : "Only in the mode “Fix + move the Iran server” does the panel move tunnels and re-point records by itself. Change it on Tunnels → Tunnel Doctor."}</div>
      <div class="frm-act"><button type="button" class="sm" onclick="go('tunnels','repairs')">Open the Tunnel Doctor</button></div></div>
    <div class="card"><h3>👁 Preview <span class="sp"></span><small class="mut">what a move would do - nothing is changed</small></h3>
      ${connected ? `<div class="frm-act"><select id="fo-srv">${irn.map(s => `<option value="${s.id}">${esc(s.name)} — ${esc(s.host)}</option>`).join("")}</select><button type="button" class="sm pri" onclick="foPreview()">Show the plan if it went off</button></div>` : '<div class="mut">Add a DNS account first (Accounts tab).</div>'}
      <small class="mut" style="display:block;margin-top:8px">It lists the tunnels that would move, the server that would take them and the DNS records of every account that would change.</small></div>
    <div class="card"><h3>🗒 Earlier moves</h3>${((fo && fo.history) || []).map(his).join("") || '<div class="mut">None yet.</div>'}</div></div>`;
}

/* ---- Settings → About: the license (owner / licensed / free), the features it switches on, the install id for a bound token ---- */
async function licLoad() {
  const box = $("#lic-box"); if (!box) return;
  try {
    const l = await api("/api/license");
    const feats = Object.entries(l.all_features || {}).map(([k, d]) => `<span class="pill ${l.features.includes(k) ? "ok" : ""}" title="${esc(d)}">${l.features.includes(k) ? "✓" : "✕"} ${k}</span>`).join(" ");
    const head = l.mode === "owner" ? "Owner install: every feature is on." : l.mode === "licensed" ? `Licensed to ${esc(l.holder || "—")}${l.expires ? " · until " + esc(when(l.expires).slice(0, 10)) : " · no end"}` : "Free tier: Backhaul TCP Mux tunnels. The other engines, the AI, the DNS failover and the shop bot pages need a license.";
    const hd = $("#lic-head"); if (hd) hd.innerHTML = head;
    box.innerHTML = `<div class="lic-feats">${feats}</div>${l.error ? `<div class="err">${esc(l.error)}</div>` : ""}<div class="abt-iid"><span>Install id</span><code id="lic-iid">${esc(l.install_id)}</code><button type="button" class="abt-copy" onclick="abtCopy('lic-iid')" title="Copy the install id"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 012-2h9"/></svg></button></div>
      ${l.mode === "owner" ? "" : `<form class="lic-form" onsubmit="return licSave(event)"><textarea name="token" class="ob-raw" rows="2" placeholder="NK1.… paste the license token here" spellcheck="false"></textarea><div class="frm-act"><button class="pri sm" type="submit">Save the license</button>${l.has_token ? '<button type="button" class="sm" onclick="licSave(null, true)">Remove the license</button>' : ""}</div></form>`}`;  } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}
window.licSave = async (e, remove) => {
  if (e) e.preventDefault();
  const ta = document.querySelector(".lic-form textarea"), tok = remove ? "" : (ta ? ta.value.trim() : "");
  if (!remove && !tok) { toast("Paste the token first", "bad"); return false; }
  if (remove && !await gconfirm("Remove the license? The panel falls back to the free tier (Backhaul TCP Mux only).", {danger: true})) return false;
  try { await api("/api/license", {method: "PUT", body: JSON.stringify({token: tok})}); toast(remove ? "✓ License removed" : "✓ License saved", "ok"); licLoad(); } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};

/* where a server is: asked from the geo services once the address is typed; the role follows (Iran or foreign) and can be flipped by hand */
window.srvWhere = async (form, force) => {
  const host = (form.querySelector("[name=host]") || {}).value || "", box = form.querySelector(".srv-where"), roleIn = form.querySelector("[name=role]");
  if (!box || !roleIn || !host.trim() || (!force && box.dataset.host === host.trim())) return roleIn ? roleIn.value : "";
  box.dataset.host = host.trim(); box.innerHTML = '<span class="mut">⏳ looking up where it is…</span>';
  try {
    const w = await api("/api/servers/where?host=" + encodeURIComponent(host.trim()));
    roleIn.value = w.role || "";
    const place = [w.country, w.city].filter(Boolean).join(", ");
    box.innerHTML = w.role ? `<span class="pill ${w.role === "iran" ? "ok" : "info"}">${w.role === "iran" ? "🇮🇷 Iran" : "🌍 foreign"}</span><span class="mut">${esc(place || w.country_code)}</span><a onclick="srvFlip(this.closest('form'))">not right?</a>` : `<span class="pill warn">unknown</span><span class="mut">no geo service answered</span><a onclick="srvFlip(this.closest('form'))">choose</a>`;
  } catch (e) { roleIn.value = ""; box.innerHTML = `<span class="pill warn">unknown</span><span class="mut">${esc(e.message)}</span><a onclick="srvFlip(this.closest('form'))">choose</a>`; }
  return roleIn.value;
};
window.srvFlip = async form => {
  const roleIn = form.querySelector("[name=role]"), box = form.querySelector(".srv-where");
  const v = await glassChoice("Where is this server?", [{label: "🇮🇷 Iran", v: "iran"}, {label: "🌍 Foreign", v: "foreign"}]);
  if (!v) return;
  roleIn.value = v; box.innerHTML = `<span class="pill ${v === "iran" ? "ok" : "info"}">${v === "iran" ? "🇮🇷 Iran" : "🌍 foreign"}</span><span class="mut">chosen by you</span><a onclick="srvFlip(this.closest('form'))">change</a>`;
};

