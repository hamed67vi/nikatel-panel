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
  $("#page").innerHTML = `<div class="toolbar"><span class="mut">${list.filter(t => t.state === "up").length} up · ${list.filter(t => t.state === "down").length} down · ${list.filter(t => t.state === "degraded").length} degraded</span><span class="sp"></span><input id="q" placeholder="Search…" value="${esc(state.q)}" oninput="setQ(this.value)"></div>
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
    <div class="frm-act"><button class="sm pri" onclick="fixTunnel(${t.id}, false)" title="Diagnose and repair step by step (needs read-write mode)">🛠 Fix</button><button class="sm" onclick="fixTunnel(${t.id}, true)" title="Show what Fix would do, change nothing">Dry run</button><button class="sm" onclick="muteRef('${esc(t.name)}', true)" title="No Telegram alerts for this tunnel until unmuted in Settings">Mute alerts</button><span class="sp"></span><a class="mut" onclick="go('actions')">repair history →</a></div>
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
    (s.done ? `<div class="fx-line ${s.ok ? "ok" : "bad"}"><b>${s.ok ? "✓" : "✕"}</b><span>${s.dry_run ? "Dry run" : "Repair"} ${s.ok ? "finished" : "did not succeed"}.</span></div>` : '<div class="fx-line run"><i></i>Working…</div>');
}
PAGES.actions = async () => {
  const d = await api("/api/actions?limit=100");
  $("#page").innerHTML = `<div class="toolbar"><span class="mut">${d.read_only ? "Read-only mode: only dry runs happen. Set NIKATEL_READ_ONLY=false on the panel server to allow repairs." : "Read-write mode: repairs change servers."}</span></div>
    <div class="card">${d.actions.map(a => `<div class="ev ${a.ok === true ? "ok" : a.ok === false ? "bad" : "warn"}"><time title="${when(a.ts)}">${ago(a.ts)} ago</time><i></i><small>${esc(a.source)}${a.dry_run ? " · dry" : ""}</small>
      <span><b>${esc(a.ref)}</b> — ${esc(a.story[a.story.length - 1] || "")} <a class="mut" onclick="this.nextElementSibling.classList.toggle('hidden')">details</a><div class="fix hidden">${a.story.map(l => `<div class="fx-line do"><b>·</b><span>${esc(l)}</span></div>`).join("")}</div></span></div>`).join("") || '<div class="empty">No repair yet.</div>'}</div>`;
};
INFO.actions = ["Repairs", "Every repair the panel planned or performed, with its full story."];
window.checkTunnel = async id => { toast("Checking both servers…"); try { await api(`/api/tunnels/${id}/check`, {method: "POST"}); toast("✓ Checked", "ok"); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };

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
  const [s, tg, al, rp, bk, mw, rp_] = await Promise.all([api("/api/settings"), api("/api/settings/telegram"), api("/api/settings/alerts"), api("/api/settings/report"), api("/api/settings/backup"), api("/api/maintenance"), api("/api/settings/repair")]);
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
