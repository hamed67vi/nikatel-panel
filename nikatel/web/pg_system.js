/* PasarGuard tab "System": the machine the panel runs on, the users by state and the traffic, the two internal workers, the WireGuard address pools and the inbounds the panel knows -
   the page one opens first in the panel's own dashboard. Read only. Registers itself in pgTabs (see pg_common.js); everything comes from /api/panels/{id}/pg/system in one answer, read
   when the tab is opened and again when it is older than 20 seconds (the page redraws itself every ~15 s and draws what it has at once). */
const pgSysS = {id: 0, d: null, at: 0, busy: false};
const pgSysUp = s => { if (s == null) return "—"; const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`; };
const pgSysBar = (v, label, detail) => `<div class="pg-sys-row"><span>${label}</span><div class="pbar"><span class="bar"><i class="${v == null ? "" : v >= 90 ? "bad" : v >= 75 ? "warn" : ""}" style="width:${v == null ? 0 : Math.min(100, v)}%"></i></span><b>${v == null ? "—" : Math.round(v) + "%"}</b></div><small class="mut">${detail || ""}</small></div>`;
const pgSysSt = {ok: "ok", up: "ok", healthy: "ok", running: "ok", disabled: "", unknown: ""};      // "disabled": the all-in-one panel runs these inside itself - not a fault

pgTabs.system = {label: "System", order: 90, types: ["pasarguard"], render: async (id, box) => {
  const S = pgSysS; if (S.id !== id) Object.assign(S, {id, d: null, at: 0});
  const have = !!S.d;
  if (have) pgSysDraw(box); else pgLoading(box, "Reading the system numbers…");
  if (!have || Date.now() - S.at > 20000) await pgSysLoad(id, box);
}};
async function pgSysLoad(id, box) {
  const S = pgSysS; if (S.busy) return; S.busy = true;
  try { const d = await pgApi(id, "/system"); if (S.id === id) { S.d = d; S.at = Date.now(); } } catch (e) { if (!S.d && box.isConnected) pgError(box, e); } finally { S.busy = false; }
  if (box.isConnected && pn.tab === "system" && String(pn.id) === String(id) && S.d) pgSysDraw(box);
}
window.pgSysReload = () => { pgSysS.d = null; refresh(); };

function pgSysDraw(box) {
  const d = pgSysS.d, m = d.machine, u = d.users, bytes = v => (v == null ? "—" : fmtB(v));
  const left = '<b>System</b><span class="mut">the machine the panel runs on, and what it is carrying</span>';
  const right = `<span class="mut">${pgSysS.at ? "read " + ago(pgSysS.at / 1000) + " ago" : ""}</span><button class="sm" onclick="pgSysReload()" title="Read it from the panel again">↻</button>`;
  const machine = m ? `<div class="card pg-sys-card"><h3>Panel <span class="sp"></span><span class="pill">v${esc(m.version || "?")}</span></h3>
      ${pgSysBar(m.cpu_percent, "CPU", m.cpu_cores ? m.cpu_cores + (m.cpu_cores === 1 ? " core" : " cores") : "")}${pgSysBar(m.mem_percent, "Memory", m.mem_total ? `${bytes(m.mem_used)} of ${bytes(m.mem_total)}` : "")}${pgSysBar(m.disk_percent, "Disk", m.disk_total ? `${bytes(m.disk_used)} of ${bytes(m.disk_total)}` : "")}
      <div class="pg-sys-row"><span>Uptime</span><b>${pgSysUp(m.uptime_seconds)}</b><small class="mut"></small></div></div>` : "";
  const users = u ? `<div class="card pg-sys-card"><h3>Users <span class="sp"></span><span class="pill">${(u.total ?? 0).toLocaleString()} in all</span></h3>
      <div class="pg-sys-pills"><span class="pill ok">${u.online ?? "—"} online</span><span class="pill">${u.active ?? "—"} active</span><span class="pill">${u.on_hold ?? "—"} on hold</span><span class="pill ${u.limited ? "warn" : ""}">${u.limited ?? "—"} limited</span><span class="pill ${u.expired ? "warn" : ""}">${u.expired ?? "—"} expired</span><span class="pill">${u.disabled ?? "—"} disabled</span></div>
      <div class="pg-sys-row"><span>Traffic</span><b>↓ ${bytes(u.bytes_in)} · ↑ ${bytes(u.bytes_out)}</b><small class="mut">since the counters were last reset</small></div></div>` : "";
  const workers = d.workers.length ? `<div class="card pg-sys-card"><h3>Workers</h3>${d.workers.map(w => `<div class="pg-sys-row"><span>${esc(w.name)}</span><span class="pill ${pgSysSt[w.status] ?? "err"}">${esc(w.status)}</span><small class="mut">${w.ms != null ? w.ms + " ms" : ""}${w.error ? " · " + esc(w.error) : ""}</small></div>`).join("")}</div>` : "";
  const wg = d.wireguard.length ? `<div class="card pg-sys-card"><h3>WireGuard pools</h3>${d.wireguard.map(p => pgSysBar(p.percent, esc(p.subnet), `${p.used ?? "?"} of ${p.capacity ?? "?"} addresses · ${p.free ?? "?"} free${p.tags.length ? " · " + esc(p.tags.join(", ")) : ""}`)).join("")}</div>` : "";
  const ib = `<div class="lbox full pg-sys-ib"><table class="tbl lift"><colgroup><col><col style="width:140px"><col style="width:120px"></colgroup><thead><tr><th>Inbound the panel knows</th><th>Protocol</th><th>Network</th></tr></thead>
      <tbody>${d.inbounds.map(r => `<tr><td class="name">${esc(r.tag)}</td><td>${esc(r.protocol || "—")}</td><td>${esc(r.network || "—")}</td></tr>`).join("") || '<tr><td colspan="3" class="mut">the panel knows no inbound (a core without inbounds, or the read was refused)</td></tr>'}</tbody></table></div>`;
  pgPaint(box, pgBar(left, right) + `${d.warnings.map(w => `<div class="pg-nodes-warn">${esc(w)}</div>`).join("")}<div class="pg-sys-grid">${machine}${users}${workers}${wg}</div>${ib}`);
}
