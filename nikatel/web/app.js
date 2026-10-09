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

$("#login-form").onsubmit = async e => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    await api("/api/auth/login", {method: "POST", body: JSON.stringify({user: f.get("user"), password: f.get("password")})});
    $("#login-err").textContent = "";
    showApp(); go(state.page);
  } catch (err) { $("#login-err").textContent = err.message; }
};
$("#logout").onclick = async () => { await api("/api/auth/logout", {method: "POST"}); showLogin(); };

/* ---- router */
const state = {page: localStorage.getItem("nk.page") || "dashboard", tab: localStorage.getItem("nk.tab") || "iran", q: "", open: null, edit: null, logs: {level: "", domain: "", q: ""}};
const PAGES = {};
const INFO = {
  dashboard: ["Dashboard", "What needs attention right now, and what happened."],
  servers: ["Servers", "Every Iran and foreign server: reachable, load, memory, disk. Sampled every 2 minutes over SSH, read-only."],
  tunnels: ["Tunnels", "Every tunnel between an Iran server and a foreign server: service units, GRE link, peer ping, forwarded ports. Checked every minute."],
  events: ["Events", "One plain sentence per thing that happened. Newest first."],
  jobs: ["Jobs", "The background jobs of the panel and whether they succeed."],
  logs: ["Logs", "The panel's own log, newest first. Secrets are masked before they are written."],
  settings: ["Settings", "Your password and the effective settings of this panel."],
};

function go(page) {
  if (!PAGES[page]) page = "dashboard";
  state.page = page; state.open = null; state.edit = null; localStorage.setItem("nk.page", page);
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("on", a.dataset.page === page));
  $("#title").textContent = INFO[page][0]; $("#subtitle").textContent = INFO[page][1];
  refresh();
}
document.querySelectorAll("#nav a").forEach(a => a.onclick = () => go(a.dataset.page));
$("#refresh").onclick = () => refresh();

async function refresh() {
  try {
    await PAGES[state.page]();
    $("#updated").textContent = "updated " + new Date().toLocaleTimeString(undefined, {hour12: false});
  } catch (e) { if (e.message !== "not logged in") toast("✕ " + e.message, "bad"); }
}
setInterval(() => { if (!document.hidden && !$("#app").classList.contains("hidden") && !state.edit) refresh(); }, 30000);

/* ---- dashboard */
PAGES.dashboard = async () => {
  const [o, ev] = await Promise.all([api("/api/overview"), api("/api/events?limit=40")]);
  $("#version").textContent = "v" + o.version; $("#ro-badge").classList.toggle("hidden", !o.read_only);
  $("#n-servers").textContent = o.servers.total; $("#n-tunnels").textContent = o.tunnels.total;
  const tile = (label, val, sub, cls) => `<div class="tile ${cls || ""}"><small>${label}</small><b>${val}</b><span>${sub}</span></div>`;
  $("#page").innerHTML = `<div class="tiles">
      ${tile("Tunnels up", o.tunnels.up + " / " + o.tunnels.total, o.tunnels.down ? o.tunnels.down + " down" : (o.tunnels.degraded ? o.tunnels.degraded + " degraded" : "all good"), o.tunnels.down ? "bad" : o.tunnels.degraded ? "warn" : "ok")}
      ${tile("Servers online", o.servers.online + " / " + o.servers.total, o.servers.offline ? o.servers.offline + " offline" : o.servers.iran + " Iran · " + o.servers.foreign + " foreign", o.servers.offline ? "bad" : "ok")}
      ${tile("Unknown", o.tunnels.unknown, "tunnels not checked yet", o.tunnels.unknown ? "warn" : "")}
      ${tile("Jobs", o.jobs.total, o.jobs.failing.length ? "failing: " + o.jobs.failing.join(", ") : o.jobs.stale.length ? "stale: " + o.jobs.stale.join(", ") : "all running", o.jobs.failing.length ? "bad" : o.jobs.stale.length ? "warn" : "ok")}
    </div>
    <div class="card"><h3>Activity <span class="sp"></span><a onclick="go('events')" class="mut">all events →</a></h3>${eventsHtml(ev.events)}</div>`;
};

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
    <div class="frm-act"><button class="pri" type="submit">${s ? "Save" : "Add server"}</button><button type="button" onclick="cancelEdit()">Cancel</button>
      ${s ? `<span class="sp"></span><button type="button" class="danger" onclick="deleteServer(${s.id}, '${esc(s.name)}')">Remove from panel</button>` : ""}</div>
    <small class="mut">Adding a server here only tells the panel to look at it. Put the panel's public key in its <code>authorized_keys</code> first (Settings shows the key).</small>
  </form>`;

PAGES.servers = async () => {
  const list = await api("/api/servers");
  const rows = list.filter(s => s.role === state.tab && (!state.q || (s.name + s.host + s.provider).toLowerCase().includes(state.q)));
  const row = s => {
    const f = s.facts || {}, cls = !s.enabled ? "" : s.online ? "up" : s.online === false ? "down" : "";
    return `<tr class="${cls}">
      <td class="name">${esc(s.name)}<span class="pill">${esc(s.provider || s.asn || "")}</span>${s.enabled ? "" : '<span class="pill">disabled</span>'}</td>
      <td><span class="st ${s.online ? "online" : s.online === false ? "offline" : ""}">${s.online ? "online" : s.online === false ? "offline" : "—"}</span></td>
      <td class="mono">${esc(s.host)}</td>
      <td class="hide-sm">${pct(f.cpu)}</td><td class="hide-sm">${pct(f.ram)}</td><td class="hide-sm">${pct(f.disk, 85, 95)}</td>
      <td class="num hide-sm">${f.load1 != null ? f.load1 + " / " + f.cores : "—"}</td>
      <td class="num hide-sm">${f.rx_mbps != null ? "↓" + f.rx_mbps + " ↑" + f.tx_mbps : "—"}</td>
      <td class="mut" title="${esc(s.last_error)}">${s.last_error ? esc(s.last_error).slice(0, 40) : (s.last_seen_at ? ago(s.last_seen_at) + " ago" : "—")}</td>
      <td><button class="sm" onclick="toggleDetail(${s.id})">${state.open === s.id ? "Hide" : "More"}</button> <button class="sm" onclick="checkServer(${s.id})">Check</button></td></tr>
      ${state.open === s.id ? `<tr class="detail"><td colspan="10" id="srv-detail-${s.id}">${state.edit === s.id ? SERVER_FORM(s) : serverDetailHtml(s)}</td></tr>` : ""}`;
  };
  $("#page").innerHTML = `<div class="toolbar">
      <div class="tabs"><button class="${state.tab === "iran" ? "on" : ""}" onclick="setTab('iran')">🇮🇷 Iran (${list.filter(s => s.role === "iran").length})</button><button class="${state.tab === "foreign" ? "on" : ""}" onclick="setTab('foreign')">🌍 Foreign (${list.filter(s => s.role === "foreign").length})</button></div>
      <span class="sp"></span><input id="q" placeholder="Search…" value="${esc(state.q)}" oninput="setQ(this.value)"><button onclick="newServer()">+ Add server</button></div>
    ${state.edit === "new" ? `<div class="card">${SERVER_FORM(null, state.tab)}</div>` : ""}
    <table class="tbl"><colgroup><col style="width:22%"><col style="width:9%"><col style="width:13%"><col style="width:10%"><col style="width:10%"><col style="width:10%"><col style="width:8%"><col style="width:10%"><col><col style="width:120px"></colgroup>
      <thead><tr><th>Server</th><th>Status</th><th>IPv4</th><th>CPU</th><th>RAM</th><th>Disk</th><th>Load</th><th>Traffic</th><th>Seen</th><th></th></tr></thead>
      <tbody>${rows.map(row).join("") || '<tr><td colspan="10" class="empty">No server.</td></tr>'}</tbody></table>`;
  const q = $("#q"); if (q && state.q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
  if (state.open && state.edit !== state.open) loadServerCharts(state.open);
};
function serverDetailHtml(s) {
  const f = s.facts || {};
  return `<div class="kv">
      <div><span>Host</span> <b>${esc(f.hostname || "—")}</b> <span class="mono">${esc(f.kernel || "")}</span></div>
      <div><span>Uptime</span> ${f.uptime ? Math.round(f.uptime / 86400) + " d" : "—"} · <span>connections</span> ${f.conn != null ? f.conn : "—"}</div>
      <div><span>RAM</span> ${f.ram_used_mb != null ? f.ram_used_mb + " / " + f.ram_total_mb + " MB" : "—"} · <span>disk</span> ${f.disk_used_gb != null ? f.disk_used_gb + " / " + f.disk_total_gb + " GB" : "—"}</div>
      <div><span>SSH</span> ${esc(s.ssh_user)}@${esc(s.host)}:${s.ssh_port} · <span>last seen</span> ${when(s.last_seen_at)}</div>
    </div>
    <div class="charts" id="srv-charts-${s.id}"><div class="mut">loading 24 h…</div></div>
    <div class="frm-act"><button class="sm" onclick="editServer(${s.id})">Edit</button><button class="sm" onclick="forgetKey(${s.id}, '${esc(s.name)}')" title="After a reinstall of this server">Forget host key</button></div>`;
}
async function loadServerCharts(id) {
  const box = document.getElementById("srv-charts-" + id);
  if (!box) return;
  const d = await api(`/api/servers/${id}/samples?hours=24`);
  const col = n => d.samples.map(r => r[d.columns.indexOf(n)]);
  box.innerHTML = `<div><small>CPU %</small>${spark(col("cpu"), {max: 100, fmt: v => v + "%"})}</div><div><small>RAM %</small>${spark(col("ram"), {max: 100, fmt: v => v + "%"})}</div>
    <div><small>↓ Mb/s</small>${spark(col("rx_mbps"), {fmt: v => v.toFixed(1)})}</div><div><small>↑ Mb/s</small>${spark(col("tx_mbps"), {fmt: v => v.toFixed(1)})}</div>
    <div><small>Load</small>${spark(col("load1"), {fmt: v => v.toFixed(2)})}</div><div><small>Connections</small>${spark(col("conn"), {fmt: v => v})}</div>`;
}
window.setTab = t => { state.tab = t; state.open = null; state.edit = null; localStorage.setItem("nk.tab", t); refresh(); };
let qT; window.setQ = v => { state.q = v.trim().toLowerCase(); clearTimeout(qT); qT = setTimeout(refresh, 150); };
window.checkServer = async id => { toast("Checking…"); try { await api(`/api/servers/${id}/check`, {method: "POST"}); toast("✓ Checked", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.newServer = () => { state.edit = "new"; state.open = null; refresh(); };
window.editServer = id => { state.edit = id; state.open = id; refresh(); };
window.cancelEdit = () => { state.edit = null; refresh(); };
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
  if (!confirm(`Remove ${name} from the panel? Nothing changes on the server itself.`)) return;
  try { await api(`/api/servers/${id}`, {method: "DELETE"}); toast("✓ Removed", "ok"); state.edit = null; state.open = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.forgetKey = async (id, name) => {
  if (!confirm(`Forget the SSH host key of ${name}? Do this only after the server was reinstalled.`)) return;
  try { await api(`/api/servers/${id}/forget-hostkey`, {method: "POST"}); toast("✓ Host key forgotten", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
};

/* ---- tunnels */
PAGES.tunnels = async () => {
  const list = await api("/api/tunnels");
  const rows = list.filter(t => !state.q || (t.name + t.iran_name + t.foreign_name).toLowerCase().includes(state.q));
  const row = t => `<tr class="${t.state}">
      <td class="name">${esc(t.name)}<span class="pill">${esc(t.type)}${t.transport ? " · " + esc(t.transport) : ""}</span></td>
      <td><span class="st ${t.state}">${esc(t.state)}</span></td>
      <td>${esc(t.iran_name)} <span class="mut">⇄</span> ${esc(t.foreign_name)}</td>
      <td class="num">${t.port || "—"}</td>
      <td class="num">${t.rtt_ms != null ? t.rtt_ms.toFixed(0) + " ms" : "—"}</td>
      <td class="num hide-sm">${t.rx_mbps != null ? "↓" + mbps(t.rx_mbps) + " ↑" + mbps(t.tx_mbps) : "—"}</td>
      <td class="mut" title="${esc(t.last_error)}">${t.last_error ? esc(t.last_error).slice(0, 48) : (t.state === "up" ? "since " + ago(t.state_since) : "")}</td>
      <td><button class="sm" onclick="toggleDetail(${t.id})">${state.open === t.id ? "Hide" : "More"}</button> <button class="sm" onclick="checkTunnel(${t.id})">Check</button></td></tr>
      ${state.open === t.id ? detailRow(t) : ""}`;
  $("#page").innerHTML = `<div class="toolbar"><span class="mut">${list.filter(t => t.state === "up").length} up · ${list.filter(t => t.state === "down").length} down · ${list.filter(t => t.state === "degraded").length} degraded</span><span class="sp"></span><input id="q" placeholder="Search…" value="${esc(state.q)}" oninput="setQ(this.value)"><button onclick="newTunnel()">+ Create tunnel</button></div>
    ${state.edit === "newtunnel" ? `<div class="card" id="build-card">${await buildFormHtml()}</div>` : ""}
    <table class="tbl"><colgroup><col style="width:24%"><col style="width:10%"><col style="width:18%"><col style="width:7%"><col style="width:8%"><col style="width:13%"><col><col style="width:130px"></colgroup>
      <thead><tr><th>Tunnel</th><th>State</th><th>Iran ⇄ Foreign</th><th>Port</th><th>RTT</th><th>Traffic</th><th>Note</th><th></th></tr></thead>
      <tbody>${rows.map(row).join("") || '<tr><td colspan="8" class="empty">No tunnel.</td></tr>'}</tbody></table>`;
  if (state.open) loadTunnelCharts(state.open);
};
function detailRow(t) {
  const d = t.detail || {}, u = t.units || {};
  const unit = (side, name) => `<div><span>${esc(name)}</span> <b class="${(d[side + "_units"] || {})[name] === "active" ? "" : "err"}">${esc((d[side + "_units"] || {})[name] || "?")}</b></div>`;
  return `<tr class="detail"><td colspan="8"><div class="kv">
      <div><span>Iran</span> <b>${esc(t.iran_name)}</b> <span class="mono">${esc(t.iran_host)}</span></div>
      <div><span>Foreign</span> <b>${esc(t.foreign_name)}</b> <span class="mono">${esc(t.foreign_host)}</span></div>
      <div><span>GRE</span> ${esc(u.iface || "—")} ${d.iface ? "· " + esc(d.iface) : ""} · ${esc(t.iran_tunnel_ip)} ⇄ ${esc(t.foreign_tunnel_ip)}</div>
      <div><span>Loss</span> ${d.loss_pct != null ? d.loss_pct + "%" : "—"} · <span>ports</span> ${(t.forwarded_ports || []).map(p => p + (d.listen && d.listen[p] === false ? " ✗" : "")).join(", ") || "—"}</div>
      ${(u.iran || []).map(n => unit("iran", n)).join("")}${(u.foreign || []).map(n => unit("foreign", n)).join("")}
      <div><span>Checked</span> ${when(t.last_check_at)} · <span>created</span> ${when(t.created_at)}</div>
      ${d.iran_error ? `<div class="err">Iran: ${esc(d.iran_error)}</div>` : ""}${d.foreign_error ? `<div class="err">Foreign: ${esc(d.foreign_error)}</div>` : ""}
    </div><div class="charts" id="tun-charts-${t.id}"><div class="mut">loading 24 h…</div></div>
    <div class="frm-act"><button class="sm pri" onclick="fixTunnel(${t.id}, false)" title="Diagnose and repair step by step (needs read-write mode)">🛠 Fix</button><button class="sm" onclick="fixTunnel(${t.id}, true)" title="Show what Fix would do, change nothing">Dry run</button><button class="sm" onclick="muteRef('${esc(t.name)}', true)" title="No Telegram alerts for this tunnel until unmuted in Settings">Mute alerts</button><span class="sp"></span><button class="sm" onclick="rebuildTunnel(${t.id}, '${esc(t.name)}')" title="Remove and build again with the same name and ports">Rebuild</button><button class="sm danger" onclick="deleteTunnel(${t.id}, '${esc(t.name)}')">Remove</button><a class="mut" onclick="go('actions')">history →</a></div>
    <div id="fix-${t.id}" class="fix hidden"></div></td></tr>`;
}
async function loadTunnelCharts(id) {
  const box = document.getElementById("tun-charts-" + id);
  if (!box) return;
  const d = await api(`/api/tunnels/${id}/checks?hours=24`);
  const col = n => d.checks.map(r => r[d.columns.indexOf(n)]);
  const oks = col("ok"), upPct = oks.length ? Math.round(oks.filter(Boolean).length * 100 / oks.length) : null;
  box.innerHTML = `<div><small>Up / down · ${oks.length} checks${upPct != null ? " · " + upPct + "% up" : ""}</small>${strip(oks)}</div>
    <div><small>RTT ms</small>${spark(col("rtt_ms"), {fmt: v => v.toFixed(0)})}</div>
    <div><small>↓ Mb/s</small>${spark(col("rx_mbps"), {fmt: v => v.toFixed(1)})}</div><div><small>↑ Mb/s</small>${spark(col("tx_mbps"), {fmt: v => v.toFixed(1)})}</div>`;
}
async function buildFormHtml() {
  const servers = await api("/api/servers");
  const opt = role => servers.filter(s => s.role === role && s.enabled).map(s => `<option value="${s.id}">${esc(s.name)} — ${esc(s.host)}${s.online === false ? " (offline)" : ""}</option>`).join("");
  return `<form class="frm" onsubmit="return startBuild(event)">
    <label>Iran server<select name="iran_id" required>${opt("iran")}</select></label>
    <label>Foreign server<select name="foreign_id" required>${opt("foreign")}</select></label>
    <label>Ports to forward <small>(443, 443=2096, 10000-10009)</small><input name="ports" required placeholder="8072"></label>
    <label>Transport<select name="transport"><option>tcpmux</option><option>tcp</option><option>wsmux</option><option>wssmux</option></select></label>
    <label>GRE MTU <small>(0 = measure)</small><input name="mtu" type="number" min="0" max="1476" value="0"></label>
    <label>Keepalive (s)<input name="keepalive" type="number" min="10" max="600" value="75"></label>
    <div class="frm-act"><button class="pri" type="submit">Build</button><button type="button" onclick="startTest(event)" title="Build a temporary tunnel between these two servers, measure it, remove it">Test this pair first</button><button type="button" onclick="cancelEdit()">Cancel</button></div>
    <small class="mut">Backhaul over GRE, like the existing tunnels: the panel measures the MTU, tunes both kernels, brings the GRE link up and verifies it holds, then starts backhaul on both sides and tests the data path. A failed build removes everything it created. “Test this pair first” does the same with a temporary tunnel, measures latency / jitter / loss / speed, and removes it.</small>
  </form><div id="build-story" class="fix hidden"></div>`;
}
window.startTest = async e => {
  const f = new FormData(e.target.closest("form"));
  const body = {iran_id: +f.get("iran_id"), foreign_id: +f.get("foreign_id"), transport: f.get("transport")};
  const box = $("#build-story"); box.classList.remove("hidden"); box.innerHTML = '<div class="fx-line run"><i></i>Building a temporary tunnel…</div>';
  try { const r = await api("/api/tunnel-test", {method: "POST", body: JSON.stringify(body)}); await followStory(r.id, box, 400); }
  catch (err) { box.innerHTML = `<div class="fx-line bad"><b>!</b>${esc(err.message)}</div>`; }
};
window.newTunnel = () => { state.edit = "newtunnel"; state.open = null; refresh(); };
window.startBuild = async e => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = {iran_id: +f.get("iran_id"), foreign_id: +f.get("foreign_id"), ports: f.get("ports").trim(), transport: f.get("transport"), mtu: +f.get("mtu") || 0, keepalive: +f.get("keepalive") || 75};
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
  if (!confirm(`Remove the tunnel ${name} from BOTH servers? Users on it lose their connection. This cannot be undone.`)) return;
  if (prompt(`Type the tunnel name to confirm:`) !== name) { toast("Not removed", "bad"); return; }
  toast("Removing…");
  try { const r = await api(`/api/tunnels/${id}`, {method: "DELETE"}); toast("✓ " + r.results.join(" · "), "ok"); state.open = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.rebuildTunnel = async (id, name) => {
  if (!confirm(`Rebuild ${name}: remove it from both servers and build it again with the same name and ports? It is down while this runs (about a minute).`)) return;
  const box = document.getElementById("fix-" + id); if (!box) return;
  box.classList.remove("hidden"); box.innerHTML = '<div class="fx-line run"><i></i>Removing and rebuilding…</div>'; state.edit = "fix";
  try { const r = await api(`/api/tunnels/${id}/rebuild`, {method: "POST"}); await followStory(r.id, box, 400); } catch (e) { box.innerHTML = `<div class="fx-line bad"><b>!</b>${esc(e.message)}</div>`; }
  state.edit = null;
};
window.toggleDetail = id => { state.open = state.open === id ? null : id; state.edit = null; refresh(); };
window.fixTunnel = async (id, dry) => {
  if (!dry && !confirm("Repair this tunnel now? The panel restarts only the units that are not working, one at a time, and checks after each step.")) return;
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
PAGES.actions = async () => {
  const d = await api("/api/actions?limit=100");
  $("#page").innerHTML = `<div class="toolbar"><span class="mut">${d.read_only ? "Read-only mode: only dry runs happen. Set NIKATEL_READ_ONLY=false on the panel server to allow repairs." : "Read-write mode: repairs change servers."}</span></div>
    <div class="card">${d.actions.map(a => `<div class="ev ${a.ok === true ? "ok" : a.ok === false ? "bad" : "warn"}"><time title="${when(a.ts)}">${ago(a.ts)} ago</time><i></i><small>${esc(a.source)}${a.dry_run ? " · dry" : ""}</small>
      <span><b>${esc(a.ref)}</b> — ${esc(a.story[a.story.length - 1] || "")} <a class="mut" onclick="this.nextElementSibling.classList.toggle('hidden')">details</a><div class="fix hidden">${a.story.map(l => `<div class="fx-line do"><b>·</b><span>${esc(l)}</span></div>`).join("")}</div></span></div>`).join("") || '<div class="empty">No repair yet.</div>'}</div>`;
};
INFO.actions = ["Repairs", "Every repair the panel planned or performed, with its full story."];
window.checkTunnel = async id => { toast("Checking both servers…"); try { await api(`/api/tunnels/${id}/check`, {method: "POST"}); toast("✓ Checked", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };

/* ---- configs (what the probe keeps alive and tests) */
INFO.configs = ["Configs", "Every client config the probe keeps alive and tests from inside Iran: reachable, ping, daily speed. Add links or import a subscription; edit a link field by field."];
const cf = {open: null, edit: null, sub: null, q: ""};
PAGES.configs = async () => {
  const [d, subs] = await Promise.all([api("/api/configs"), api("/api/subscriptions")]);
  $("#n-configs").textContent = d.configs.length;
  const list = d.configs.filter(c => !cf.q || (c.name + c.group).toLowerCase().includes(cf.q));
  const groups = [...new Set(list.map(c => c.group || ""))].sort((a, b) => (a || "~").localeCompare(b || "~"));
  const row = c => `<tr class="${c.status === "up" ? "up" : c.status === "down" ? "down" : ""}">
      <td class="name">${esc(c.name)}${c.tunnel ? `<span class="pill">${esc(c.tunnel)}</span>` : ""}${c.enabled ? "" : '<span class="pill">disabled</span>'}</td>
      <td><span class="st ${c.status}">${esc(c.status)}</span></td>
      <td class="num">${c.ping_ms != null && c.status === "up" ? Math.round(c.ping_ms) + " ms" : "—"}</td>
      <td class="num hide-sm">${c.down_mbps != null ? "↓" + c.down_mbps.toFixed(1) + " ↑" + (c.up_mbps || 0).toFixed(1) : "—"}</td>
      <td class="mut hide-sm">${c.cdn_port || "—"}</td>
      <td class="mut" title="${esc(c.last_error)}">${c.last_error && c.status === "down" ? esc(c.last_error).slice(0, 44) : (c.checked_at ? ago(c.checked_at) + " ago" : "—")}</td>
      <td><button class="sm" onclick="cfToggle(${c.id})">${cf.open === c.id ? "Hide" : "More"}</button> <button class="sm" onclick="cfEdit(${c.id})">Edit</button></td></tr>
      ${cf.open === c.id ? `<tr class="detail"><td colspan="7" id="cf-detail-${c.id}">${cf.edit === c.id ? '<div class="mut">loading the link…</div>' : cfDetailHtml(c)}</td></tr>` : ""}`;
  $("#page").innerHTML = `<div class="toolbar"><span class="mut">${d.configs.filter(c => c.status === "up").length} up · ${d.configs.filter(c => c.status === "down").length} down · probe: ${esc(d.probe.active || "none")}</span><span class="sp"></span>
      <input id="cq" placeholder="Search…" value="${esc(cf.q)}" oninput="cfQ(this.value)"><button onclick="cfSync()">↻ Sync with the probe</button><button onclick="cfNew()">+ Add config</button><button onclick="cfSubNew()">+ Subscription</button></div>
    ${cf.edit === "new" ? `<div class="card"><h3>Add a config</h3><form class="frm" onsubmit="return cfAdd(event)"><label style="grid-column:1/-1">Link<textarea name="uri" class="ob-raw" required placeholder="vless://…  vmess://…  trojan://…  ss://…"></textarea></label><label>Name <small>(empty = the link's own name)</small><input name="name"></label><label>Group<input name="group" placeholder="optional"></label><div class="frm-act"><button class="pri" type="submit">Add to the probe</button><button type="button" onclick="cfCancel()">Cancel</button></div></form></div>` : ""}
    ${cf.sub ? cfSubHtml() : ""}
    ${subs.subscriptions.length ? `<div class="card"><h3>Subscriptions</h3>${subs.subscriptions.map(s => `<div class="ev info"><time>${s.last_import ? ago(s.last_import) + " ago" : "—"}</time><i></i><small>${s.nodes.length} nodes</small><span><b>${esc(s.name)}</b> <span class="mut">${esc(s.url_host)}</span> <a class="mut" onclick="cfSubRefresh(${s.id}, '${esc(s.name)}')">import new nodes</a> · <a class="mut" onclick="cfSubDel(${s.id})">forget</a></span></div>`).join("")}</div>` : ""}
    ${groups.map(g => `<div class="nk-ghead"><b>${esc(g || "Ungrouped")}</b><span>${list.filter(c => (c.group || "") === g).length}</span></div>
    <table class="tbl"><colgroup><col style="width:28%"><col style="width:9%"><col style="width:9%"><col style="width:14%"><col style="width:8%"><col><col style="width:110px"></colgroup>
      <thead><tr><th>Config</th><th>Status</th><th>Ping</th><th>Speed Mb/s</th><th>CDN port</th><th>Note</th><th></th></tr></thead>
      <tbody>${list.filter(c => (c.group || "") === g).map(row).join("")}</tbody></table>`).join("") || '<div class="empty">No config yet. Add a link or import a subscription.</div>'}`;
  const q = $("#cq"); if (q && cf.q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
  if (cf.open && cf.edit === cf.open) cfLoadEditor(cf.open);
  else if (cf.open) cfLoadChart(cf.open);
};
function cfDetailHtml(c) {
  return `<div class="kv"><div><span>Probe port</span> ${c.port || "—"} · <span>slug</span> ${esc(c.slug)}</div><div><span>Group</span> ${esc(c.group || "—")} · <span>tunnel</span> ${esc(c.tunnel || "—")} · <span>CDN port</span> ${esc(c.cdn_port || "—")}</div>
    <div><span>Speed</span> ${c.down_mbps != null ? "↓" + c.down_mbps + " ↑" + c.up_mbps + " Mb/s · " + esc((c.speed_at || "").slice(0, 16)) : "not measured"}</div><div><span>Since</span> ${c.status} ${ago(c.status_since)}</div></div>
    <div class="charts" id="cf-charts-${c.id}"><div class="mut">loading 24 h…</div></div>
    <div class="frm-act"><button class="sm" onclick="cfSpeed(${c.id})">Speed test now</button><button class="sm" onclick="cfCopy(${c.id})">Copy link</button><span class="sp"></span><button class="sm danger" onclick="cfDel(${c.id}, '${esc(c.name)}')">Delete from the probe</button></div>`;
}
async function cfLoadChart(id) {
  const box = document.getElementById("cf-charts-" + id); if (!box) return;
  const d = await api(`/api/configs/${id}/samples?hours=24`);
  const col = n => d.samples.map(r => r[d.columns.indexOf(n)]);
  const ups = col("up"), pct = ups.length ? Math.round(ups.filter(Boolean).length * 100 / ups.length) : null;
  box.innerHTML = `<div><small>Up / down · ${ups.length} checks${pct != null ? " · " + pct + "% up" : ""}</small>${strip(ups)}</div><div><small>Ping ms</small>${spark(col("ping_ms").map(v => v || null), {fmt: v => v.toFixed(0)})}</div>`;
}
window.cfQ = v => { cf.q = v.trim().toLowerCase(); clearTimeout(qT); qT = setTimeout(refresh, 150); };
window.cfToggle = id => { cf.open = cf.open === id ? null : id; cf.edit = null; refresh(); };
window.cfNew = () => { cf.edit = "new"; cf.open = null; cf.sub = null; refresh(); };
window.cfCancel = () => { cf.edit = null; cf.sub = null; refresh(); };
window.cfSync = async () => { toast("Asking the probe… (up to a minute)"); try { await api("/api/configs/sync", {method: "POST"}); toast("✓ Synced", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.cfAdd = async e => { e.preventDefault(); const f = new FormData(e.target); try { const r = await api("/api/configs", {method: "POST", body: JSON.stringify({uri: f.get("uri").trim(), name: f.get("name").trim(), group: f.get("group").trim()})}); toast("✓ " + r.name + " added — the probe tests it within a minute", "ok"); cf.edit = null; refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.cfDel = async (id, name) => { if (!confirm(`Delete ${name} from the probe? Its history goes too.`)) return; try { await api(`/api/configs/${id}`, {method: "DELETE"}); toast("✓ Deleted", "ok"); cf.open = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
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
    <div class="frm-act"><button class="pri" onclick="cfeSave()">Save to the probe</button><button onclick="cfToggle(${cfe.id})">Cancel</button><span class="mut">${link === cfe.origLink ? "no change yet" : "changed"}</span></div></div>`;
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
window.cfSubNew = () => { cf.sub = {name: "", url: "", nodes: null}; cf.edit = null; refresh(); };
function cfSubHtml() {
  const s = cf.sub;
  return `<div class="card"><h3>Import a subscription</h3><form class="frm" onsubmit="return cfSubPreview(event)">
    <label>Group name<input name="name" required value="${esc(s.name)}" placeholder="hamed11"></label><label style="grid-column:2/-1">Subscription link<input name="url" required value="${esc(s.url)}" placeholder="https://…/sub/…"></label>
    <div class="frm-act"><button class="pri" type="submit">Preview nodes</button><button type="button" onclick="cfCancel()">Cancel</button></div></form>
    ${s.nodes ? `<div class="ob-inbounds">${s.nodes.map(n => `<label><input type="checkbox" value="${esc(n.name)}" ${n.exists ? "" : "checked"} ${n.exists ? "disabled" : ""}> ${esc(n.name)} <small class="mut">${esc(n.proto)}${n.exists ? " · already there" : ""}</small></label>`).join("")}</div>
      <div class="frm-act"><button class="pri" onclick="cfSubImport()">Add the ticked nodes to the probe</button></div>` : ""}</div>`;
}
window.cfSubPreview = async e => { e.preventDefault(); const f = new FormData(e.target); cf.sub.name = f.get("name").trim(); cf.sub.url = f.get("url").trim(); toast("Downloading…"); try { cf.sub.nodes = (await api("/api/subscriptions/preview", {method: "POST", body: JSON.stringify({name: cf.sub.name, url: cf.sub.url})})).nodes; toast("✓ " + cf.sub.nodes.length + " nodes", "ok"); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.cfSubImport = async () => { const picks = [...document.querySelectorAll(".ob-inbounds input:checked")].map(x => x.value); if (!picks.length) { toast("Tick at least one node", "bad"); return; } toast("Adding " + picks.length + " nodes… (a few seconds each)"); try { const r = await api("/api/subscriptions/import", {method: "POST", body: JSON.stringify({name: cf.sub.name, url: cf.sub.url, picks})}); toast(`✓ ${r.added.length} added, ${r.skipped.length} skipped, ${r.failed.length} failed`, r.failed.length ? "bad" : "ok"); cf.sub = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.cfSubRefresh = (id, name) => { cf.sub = {name, url: "", nodes: null}; refresh(); toast("Paste the subscription link again to fetch new nodes"); };
window.cfSubDel = async id => { if (!confirm("Forget this subscription record? The configs stay.")) return; await api(`/api/subscriptions/${id}`, {method: "DELETE"}); refresh(); };

/* ---- panels (Marzban / PasarGuard): health, nodes, users, hosts, outbounds */
const pn = {id: localStorage.getItem("nk.pn") || "", tab: "nodes", add: false, edit: false, users: null, uq: "", ustatus: "", uoff: 0, user: null, hosts: null, hostEdit: null, core: null, raw: false, logs: null};
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
      <button onclick="pnCheck(${p.id})">↻ Check now</button><button onclick="pn.edit=!pn.edit;pn.add=false;refresh()">Edit</button><button onclick="pn.add=!pn.add;pn.edit=false;refresh()">+ Add panel</button></div>
    ${p.last_error && p.status === "down" ? `<div class="gw-out bad">✕ ${esc(p.last_error)}</div>` : ""}
    ${pn.add ? addForm : ""}${pn.edit ? editForm : ""}
    <div class="tiles">
      <div class="tile"><small>Users</small><b>${s.users_total ?? "—"}</b><span>${s.users_active ?? "—"} active · ${s.users_online ?? "—"} online</span></div>
      <div class="tile"><small>Nodes</small><b>${(p.nodes || []).filter(n => n.fails === 0 || ["connected", "healthy"].includes(n.status)).length} / ${(p.nodes || []).length}</b><span>connected</span></div>
      <div class="tile"><small>Load</small><b>${s.cpu != null ? Math.round(s.cpu) + "%" : "—"}</b><span>cpu · ram ${s.mem_pct ?? "—"}%</span></div>
      <div class="tile"><small>Traffic</small><b>${s.bw_in != null ? fmtB(s.bw_in + (s.bw_out || 0)) : "—"}</b><span>↓${s.bw_in != null ? fmtB(s.bw_in) : "—"} ↑${s.bw_out != null ? fmtB(s.bw_out) : "—"}</span></div></div>
    <div class="tabs" style="margin-bottom:10px">${["nodes", "users", "hosts", "outbounds"].map(t => `<button class="${pn.tab === t ? "on" : ""}" onclick="pnTab('${t}')">${t[0].toUpperCase() + t.slice(1)}</button>`).join("")}</div>
    <div id="pn-body">${pn.tab === "nodes" ? pnNodesHtml(p) : '<div class="mut">loading…</div>'}</div>`;
  if (pn.tab === "users") pnUsersLoad(p.id);
  if (pn.tab === "hosts") pnHostsLoad(p.id);
  if (pn.tab === "outbounds") pnCoreLoad(p.id);
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
window.pnAdd = async e => { e.preventDefault(); const f = new FormData(e.target); toast("Logging in…"); try { const r = await api("/api/panels", {method: "POST", body: JSON.stringify({name: f.get("name").trim(), type: f.get("type"), url: f.get("url").trim(), username: f.get("username").trim(), password: f.get("password"), verify_tls: !!f.get("verify_tls")})}); toast("✓ " + r.panel.name + " added", "ok"); pn.add = false; pn.id = String(r.panel.id); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.pnEdit = async (e, id) => { e.preventDefault(); const f = new FormData(e.target); const body = {name: f.get("name").trim(), url: f.get("url").trim(), username: f.get("username").trim(), verify_tls: !!f.get("verify_tls"), enabled: !!f.get("enabled")}; if (f.get("password")) body.password = f.get("password"); try { await api(`/api/panels/${id}`, {method: "PATCH", body: JSON.stringify(body)}); toast("✓ Saved", "ok"); pn.edit = false; refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.pnDel = async (id, name) => { if (!confirm(`Remove ${name} from this list? Nothing on the panel itself changes.`)) return; await api(`/api/panels/${id}`, {method: "DELETE"}); pn.id = ""; pn.edit = false; refresh(); };
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
      <button type="button" class="sm" onclick="pnUAct(${id}, '${esc(u.username)}', 'renew', prompt('Add how many days?', '30'))">+ days</button><button type="button" class="sm" onclick="pnUAct(${id}, '${esc(u.username)}', 'revoke')">Revoke sub</button><button type="button" class="sm danger" onclick="if(confirm('Delete ${esc(u.username)} from the panel?'))pnUAct(${id}, '${esc(u.username)}', 'delete')">Delete</button></div></form>`;
}
window.pnUQ = v => { pn.uq = v.trim(); pn.uoff = 0; clearTimeout(qT); qT = setTimeout(refresh, 300); };
window.pnUserOpen = async (id, name) => { try { pn.user = (await api(`/api/panels/${id}/users/${encodeURIComponent(name)}`)).user; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.pnUserSave = async (e, id, name) => { e.preventDefault(); const f = new FormData(e.target); try { await api(`/api/panels/${id}/users/${encodeURIComponent(name)}`, {method: "PUT", body: JSON.stringify({status: f.get("status"), expire_date: f.get("expire_date"), limit_gb: +f.get("limit_gb"), reset: f.get("reset"), note: f.get("note")})}); toast("✓ Saved", "ok"); pn.user = null; refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
window.pnUAct = async (id, name, action, value) => { if (action === "renew" && !value) return; try { await api(`/api/panels/${id}/users/${encodeURIComponent(name)}/action`, {method: "POST", body: JSON.stringify({action, value})}); toast("✓ " + action + " done", "ok"); if (action === "delete") pn.user = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.pnBulk = async (id, action, value) => { const names = [...document.querySelectorAll(".pn-uchk:checked")].map(x => x.value); if (!names.length) { toast("Tick some users first", "bad"); return; } try { const r = await api(`/api/panels/${id}/users-bulk`, {method: "POST", body: JSON.stringify({action, value, usernames: names})}); toast(`✓ ${r.done} done${r.failed_count ? ", " + r.failed_count + " failed" : ""}`, r.failed_count ? "bad" : "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.pnBulkRenew = id => { const d = prompt("Add how many days to the ticked users?", "30"); if (d) pnBulk(id, "renew", +d); };

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
window.pnHostDel = async (id, key, name) => { if (!confirm(`Delete host ${name} from the panel? Users lose that address in their links.`)) return; try { await api(`/api/panels/${id}/hosts/${encodeURIComponent(key)}`, {method: "DELETE"}); toast("✓ Deleted", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };

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
window.pnObDel = (id, tag) => { if (!confirm(`Remove outbound ${tag}? Rules that use it are removed too.`)) return; pnCoreDo(id, () => api(`/api/panels/${id}/core/outbounds/${encodeURIComponent(tag)}`, {method: "DELETE"})); };
window.pnObDefault = (id, tag) => pnCoreDo(id, () => api(`/api/panels/${id}/core/outbounds/${encodeURIComponent(tag)}/default`, {method: "POST"}));
window.pnCoreRestore = (id, file) => { if (!confirm("Put this backup in place of the current core config?")) return; pnCoreDo(id, () => api(`/api/panels/${id}/core/restore/${file}`, {method: "POST"})); };
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
        <td><button class="sm" onclick="sbU('${esc(r.userid)}', 'wallet', prompt('Add to the wallet (Toman, negative to take)', '10000'))">± wallet</button> <button class="sm" onclick="sbU('${esc(r.userid)}', 'discount', prompt('Discount percent', '${esc(r.discount_percent || 0)}'))">discount</button> <button class="sm" onclick="sbU('${esc(r.userid)}', '${r.is_agent === "1" ? "agent_off" : "agent_on"}')">${r.is_agent === "1" ? "agent off" : "agent on"}</button> <button class="sm ${r.step === "banned" ? "" : "danger"}" onclick="sbU('${esc(r.userid)}', '${r.step === "banned" ? "unban" : "ban"}')">${r.step === "banned" ? "unban" : "ban"}</button></td></tr>`).join("")}</tbody></table>` : ""}`;
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
window.sbAct = async act => { const msgs = {webhook: "Point the Telegram webhook at the bot's public URL?", restart: "Restart PHP on the bot server?", backup: "Dump the bot's database to /opt/nikabot/backups?", clearlogs: "Clear the bot's log files?"}; if (!confirm(msgs[act])) return; toast("Working…"); try { const r = await api(`/api/shopbot/action/${act}`, {method: "POST"}); toast((r.done ? "✓ " : "✕ ") + (r.message || act), r.done ? "ok" : "bad"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
window.sbImport = async () => { if (!confirm("Import the real database over the live one?\n\nThe live database is dumped first. Users, orders, payments, plans, panels and settings are replaced; broadcast queues are not copied. The bot then serves the real customers.")) return; toast("Dumping and importing… (1–3 minutes)"); try { const r = await api("/api/shopbot/import-stage", {method: "POST"}); toast(`✓ Imported: ${r.tables.users} users, ${r.tables.orders_list} orders · backup ${r.backup.split("/").pop()}`, "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };

/* ---- gateway (Xray on an Iran server, borrowed configs for internet cuts) */
const gw = {host: localStorage.getItem("nk.gw") || "", st: null, status: null, test: null, note: "", noteCls: "", dirty: false};
INFO.gateway = ["Gateway", "A plain Xray on an Iran server: users reach a port there, the traffic goes on to your foreign node — directly while that works, through a borrowed config when Iran is cut off."];
PAGES.gateway = async () => {
  const servers = (await api("/api/servers?role=iran")).filter(s => s.enabled);
  if (!servers.length) { $("#page").innerHTML = '<div class="empty">No enabled Iran server.</div>'; return; }
  if (!servers.some(s => String(s.id) === gw.host)) { gw.host = String(servers[0].id); gw.st = null; }
  if (!gw.st || gw.st.server_id !== +gw.host) { gw.st = await api(`/api/gateway/${gw.host}`); gw.status = null; gw.test = null; gw.dirty = false; }
  const st = gw.st, t = gw.test || [], tr = k => t.find(x => x.key === k);
  const cell = k => { const r = tr(k); return r ? (r.ok ? `<span class="gw-ok">✓ ${r.ms} ms</span> <small class="mut">${esc(r.ip || "")} ${esc(r.country || "")}</small>` : '<span class="err">✗ no answer</span>') : ""; };
  const chip = !st.installed ? '<span class="pill">not installed</span>' : !gw.status ? '<span class="pill">status unknown</span>' : gw.status.error ? `<span class="pill err">✗ ${esc(gw.status.error).slice(0, 60)}</span>` : gw.status.active === "active" ? `<span class="pill ok">● running · ${esc((gw.status.version || "").split("(")[0])}</span>` : `<span class="pill err">● ${esc(gw.status.active || "stopped")}</span>`;
  $("#page").innerHTML = `
    <div class="toolbar"><select onchange="gwPick(this.value)">${servers.map(s => `<option value="${s.id}" ${String(s.id) === gw.host ? "selected" : ""}>${esc(s.name)} — ${esc(s.host)}</option>`).join("")}</select>${chip}<span class="sp"></span>
      <button onclick="gwStatus()">↻ Status</button><button onclick="gwTest()" ${st.installed ? "" : "disabled"}>⚡ Test exits</button>
      ${st.installed ? `<button class="pri" onclick="gwApply()">Save &amp; apply</button><button class="danger" onclick="gwRemove()">Remove</button>` : `<button class="pri" onclick="gwInstall()" ${st.binary ? "" : "disabled"} title="${st.binary ? "" : "run scripts/fetch-xray.sh on the panel server first"}">Install Xray on this server</button>`}
      ${st.read_only ? '<span class="badge">read-only</span>' : ""}</div>
    ${gw.note ? `<div class="gw-out ${gw.noteCls}">${esc(gw.note)}</div>` : ""}
    <div class="card"><h3>When to use the borrowed configs</h3>
      <select onchange="gwField('mode', this.value)">
        <option value="auto" ${st.mode === "auto" ? "selected" : ""}>Automatic — direct while it works, a borrowed config when it does not (recommended)</option>
        <option value="borrowed" ${st.mode === "borrowed" ? "selected" : ""}>Always through the borrowed configs (test, or a full cut)</option>
        <option value="direct" ${st.mode === "direct" ? "selected" : ""}>Direct only (borrowed configs kept but unused)</option></select></div>
    <div class="card"><h3>Borrowed configs <small class="mut">— one is enough, several are safer: the fastest alive one is used</small></h3>
      ${st.borrowed.map((b, i) => `<div class="gw-row"><span><b>${esc(b.note || "config " + (i + 1))}</b> <small class="mut">${esc(b.info || "new")}</small></span><span>${b.id ? cell(b.id) : ""}</span>
        <span class="ob-act"><label class="chk"><input type="checkbox" ${b.enabled ? "checked" : ""} onchange="gwToggle('borrowed', ${i}, this.checked)"> on</label><button class="sm danger" onclick="gwDel('borrowed', ${i})">Delete</button></span></div>`).join("") || '<div class="empty">No borrowed config yet.</div>'}
      <textarea id="gw-uri" class="ob-raw" placeholder="vless://…  vmess://…  trojan://…  ss://…" spellcheck="false"></textarea>
      <div class="frm-act"><input id="gw-note" placeholder="note (optional)"><button class="pri" onclick="gwAddBorrowed()">Add this config</button></div></div>
    <div class="card"><h3>Ports <small class="mut">— a port on this Iran server that forwards to a foreign node</small></h3>
      ${st.ports.map((p, i) => `<div class="gw-row"><span><b>:${p.port}</b> → ${esc(p.target_host)}:${p.target_port} <small class="mut">${p.udp ? "tcp+udp" : "tcp"} ${esc(p.note || "")}</small></span><span>${gw.status && gw.status.listen && gw.status.listen[p.port] != null ? (gw.status.listen[p.port] ? '<span class="gw-ok">listening</span>' : '<span class="err">not listening</span>') : ""}</span>
        <span class="ob-act"><label class="chk"><input type="checkbox" ${p.enabled !== false ? "checked" : ""} onchange="gwToggle('ports', ${i}, this.checked)"> on</label><button class="sm danger" onclick="gwDel('ports', ${i})">Delete</button></span></div>`).join("") || '<div class="empty">No port yet.</div>'}
      <div class="frm-act"><input id="gw-p" type="number" min="1024" max="65535" placeholder="listen port"><span class="mut">→</span><input id="gw-h" placeholder="node IP or host"><input id="gw-tp" type="number" min="1" max="65535" placeholder="node port"><input id="gw-n" placeholder="note"><label class="chk"><input id="gw-udp" type="checkbox"> udp too</label><button class="pri" onclick="gwAddPort()">Add port</button></div></div>
    ${gw.test ? `<div class="card"><h3>Exit test <small class="mut">— from the Iran server, through every exit</small></h3>${t.map(r => `<div class="gw-row"><span><b>${esc(r.key === "direct" ? "Direct (no borrowed config)" : r.key === "gateway" ? "The gateway as users see it (current mode)" : r.name)}</b></span><span>${cell(r.key)}</span><span></span></div>`).join("")}</div>` : ""}
    ${gw.status && gw.status.log && gw.status.log.length ? `<div class="card"><h3>Latest log lines</h3><pre class="gl-out">${esc(gw.status.log.join("\n"))}</pre></div>` : ""}
    ${gw.dirty ? '<div class="gw-dirty">Unsaved changes — press “Save &amp; apply”.</div>' : ""}`;
};
window.gwPick = v => { gw.host = v; localStorage.setItem("nk.gw", v); gw.st = null; gw.note = ""; refresh(); };
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
window.gwInstall = async () => { if (!confirm("Copy Xray to this Iran server (about 35 MB over SSH) and install it as the service nikatel-xgw? Nothing else on the server is touched.")) return; gw.note = "Installing… this can take a minute"; gw.noteCls = ""; refresh(); try { await gwSave(); const r = await api(`/api/gateway/${gw.host}/install`, {method: "POST"}); gw.note = "✓ Installed: " + r.version; gw.noteCls = "ok"; gw.st = null; } catch (e) { gw.note = "✕ " + e.message; gw.noteCls = "bad"; } refresh(); };
window.gwApply = async () => { gw.note = "Saving and applying…"; gw.noteCls = ""; refresh(); try { await gwSave(); const r = await api(`/api/gateway/${gw.host}/apply`, {method: "POST"}); gw.note = "✓ Gateway running" + (r.ports.length ? " on port " + r.ports.join(", ") : ""); gw.noteCls = "ok"; } catch (e) { gw.note = "✕ " + e.message; gw.noteCls = "bad"; } await gwStatus(); };
window.gwStatus = async () => { try { gw.status = await api(`/api/gateway/${gw.host}/status`, {method: "POST"}); } catch (e) { gw.status = {error: e.message}; } refresh(); };
window.gwTest = async () => { gw.note = "Testing every exit from the Iran server…"; gw.noteCls = ""; refresh(); try { gw.test = (await api(`/api/gateway/${gw.host}/test`, {method: "POST"})).results; gw.note = ""; } catch (e) { gw.note = "✕ " + e.message; gw.noteCls = "bad"; } refresh(); };
window.gwRemove = async () => { if (!confirm("Remove the gateway from this server? Your list of ports and borrowed configs stays in the panel.")) return; try { await api(`/api/gateway/${gw.host}/remove`, {method: "POST"}); gw.note = "✓ Removed"; gw.noteCls = "ok"; gw.st = null; } catch (e) { gw.note = "✕ " + e.message; gw.noteCls = "bad"; } refresh(); };

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
  const [s, tg, al, rp, bk, mw, rp_, pr, iranList, sb] = await Promise.all([api("/api/settings"), api("/api/settings/telegram"), api("/api/settings/alerts"), api("/api/settings/report"), api("/api/settings/backup"), api("/api/maintenance"), api("/api/settings/repair"), api("/api/settings/probe"), api("/api/servers?role=iran"), api("/api/settings/shopbot")]);
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
        <div class="frm-act"><button class="pri" type="submit">Save</button></div></form>
      <small class="mut">At most ${rp_.max_per_hour} repairs per tunnel per hour; never when a server is unreachable. Keep this off while the old panel still repairs the same tunnels. ${rp_.read_only ? "Repairs need <code>NIKATEL_READ_ONLY=false</code> in the env file first." : ""}</small></div>

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

    <div class="card"><h3>Password</h3>
      <form class="frm one" onsubmit="return changePassword(event)">
        <label>Current password<input name="current" type="password" required autocomplete="current-password"></label>
        <label>New password<input name="new" type="password" required minlength="8" autocomplete="new-password"></label>
        <label>Repeat<input name="again" type="password" required minlength="8" autocomplete="new-password"></label>
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
};
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
window.installProbe = async e => { e.preventDefault(); const sid = +new FormData(e.target).get("server_id"); if (!confirm("Install the probe on this Iran server? It copies the probe code and Xray (about 65 MB) over SSH and starts three services. Nothing else on the server is touched.")) return false; toast("Installing… (2–5 minutes)"); try { const r = await api(`/api/settings/probe/install?server_id=${sid}`, {method: "POST"}); toast(`✓ Probe on ${r.server} registered as ${r.role}`, "ok"); refresh(); } catch (err) { toast("✕ " + err.message, "bad"); } return false; };
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
  try { await api("/api/auth/password", {method: "POST", body: JSON.stringify({current: f.get("current"), new: f.get("new")})}); toast("✓ Password changed", "ok"); e.target.reset(); }
  catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};

/* ---- boot */
(async () => {
  try { await api("/api/auth/me"); showApp(); go(state.page); } catch (e) { showLogin(); }
})();
