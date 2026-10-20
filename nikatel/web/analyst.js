/* The analyst tab of the Tunnel Doctor: what the history says (the best tunnels of the day, the profile of every tunnel, what is at risk, how the servers are used,
   the CPU cores) - read only, short. Loaded after app.js and uses its helpers ($, api, esc, state, refresh, toast, balBody). */
state.anaTab = "best";      // the Analyst opens on the best tunnels of the day

const anaDur = s => s == null ? "—" : s < 90 ? s + " s" : s < 5400 ? Math.round(s / 60) + " min" : (s / 3600).toFixed(1) + " h";
const anaChip = v => `<span class="ana-v ${esc(String(v).replace(" ", "-"))}">${esc(v)}</span>`;

window.anaLoad = async () => {
  const box = $("#ana-box"); if (!box) return;
  state.bal = null;           // the cores are read again when their tab is opened
  try { state.ana = await api("/api/analyst?days=" + (state.anaDays || 7)); } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  anaDraw();
};
window.anaPick = t => { state.anaTab = t; anaDraw(); };
window.anaDays = d => { state.anaDays = +d; anaLoad(); };

/* one line per tunnel: how often it was there, how often it broke, its latency, the verdict */
function anaTunnels(a) {
  const rows = a.tunnels.map(p => `<tr class="ana-${esc(p.verdict.replace(" ", "-"))}" data-q="${esc((p.name + " " + p.iran + " " + p.foreign + " " + p.verdict + " " + p.kind).toLowerCase())}"><td class="name">${esc(p.name)}</td><td class="mut">${esc(p.iran)} ⇄ ${esc(p.foreign)}</td><td>${p.availability == null ? "—" : p.availability + " %"}</td>
      <td>${p.outages}${p.outages ? ` <small class="mut">${p.outages_per_day}/day</small>` : ""}</td><td>${p.rtt_base == null ? "—" : Math.round(p.rtt_base) + " ms"}${p.rtt_ratio && p.rtt_ratio >= 1.3 ? ` <small class="bad-t">now ${Math.round(p.rtt_now)}</small>` : ""}</td><td>${anaChip(p.verdict)}${p.rising ? ' <small class="bad-t">rising</small>' : ""}</td></tr>`).join("");
  return `<table class="tbl lift ana-tbl"><thead><tr><th>Tunnel</th><th>Route</th><th>Available</th><th>Outages</th><th>Latency</th><th>Verdict</th></tr></thead><tbody>${rows || '<tr><td colspan="6" class="empty">No data yet.</td></tr>'}</tbody></table>`;
}

function anaRisk(a) {
  const risk = a.risks.map(r => `<div class="ana-line ${esc(r.level)}"><b>${esc(r.tunnel)}</b><span class="mut">${esc(r.iran)} ⇄ ${esc(r.foreign)}</span><span>${r.why.map(esc).join(" · ")}</span></div>`).join("") +
    a.common.map(c => `<div class="ana-line high"><b>${esc(c.server)}</b><span class="mut">${c.tunnels.map(esc).join(", ")}</span><span>${esc(c.text)}</span></div>`).join("");
  return `<div class="ana-list">${risk || '<div class="empty">Nothing stands out right now.</div>'}</div>`;
}

/* one line per server: where, how busy at the peak, how many tunnels, the verdict (idle / light / redundant / busy), what the look at its own network found */
function anaServers(a) {
  const hl = u => !u ? '<span class="mut">not looked at yet</span>' : u.error ? `<span class="ana-v watch" title="${esc(u.error)}">no answer</span>` : `<span class="ana-v ${u.verdict === "healthy" ? "stable" : u.verdict === "tunable" ? "watch" : "chronic"}" title="${esc((u.findings || []).map(f => f.text).join("\n") || "nothing wrong found")}">${u.verdict === "provider" ? "provider / route" : esc(u.verdict)}</span>${u.tune === "kept" ? ' <small class="ok-t">tuned</small>' : u.tune === "rolled back" ? ' <small class="bad-t">tuning undone</small>' : ""}`;
  const sv = a.servers.map(s => `<tr class="ana-${esc(s.verdict)}" data-q="${esc((s.name + " " + (s.city || "") + " " + (s.provider || "") + " " + s.verdict + " " + s.role).toLowerCase())}"><td class="name">${esc(s.name)}</td><td class="mut">${s.role === "iran" ? "Iran" : "foreign"}${s.city ? " · " + esc(s.city) : ""}</td><td>${s.peak_cpu_p95 == null ? "—" : s.peak_cpu_p95 + " %"}</td><td>${s.peak_mbps} Mbit/s</td><td>${s.tunnels}</td><td>${anaChip(s.verdict)}</td>
      <td>${hl(s.underuse)}</td><td class="ana-act"><button class="sm" onclick="anaCheck(${s.id}, this)" title="Look at this server's own network now (about ten seconds; read only)">🔍</button>${s.underuse && (s.underuse.findings || []).some(f => f.fix === "tune") ? `<button class="sm pri" onclick="anaTune(${s.id})" title="Fix the kernel settings (BBR, fq, buffers): measured before and after, put back if it did not help">Fix</button>` : ""}${s.underuse && s.underuse.tuned ? `<button class="sm" onclick="anaUntune(${s.id})" title="Put the kernel settings back as they were">Undo</button>` : ""}</td></tr>
      ${s.underuse && (s.underuse.findings || []).filter(f => f.id !== "nospeed").length ? `<tr class="ana-why"><td></td><td colspan="7"><small>${s.underuse.findings.filter(f => f.id !== "nospeed").map(f => `${f.fix ? "🔧" : "⚠"} ${esc(f.text)}`).join(" &nbsp;·&nbsp; ")}</small></td></tr>` : ""}`).join("");
  return `<div class="ana-bar"><label class="chk" title="On: the Tunnel Doctor looks at the idle and light servers (two every 30 minutes, each at most every 12 hours) and fixes by itself what the kernel settings can fix. A fault of the provider's network is only reported."><input type="checkbox" ${a.underuse_auto ? "checked" : ""} onchange="anaUnderAuto(this)"> The Doctor fixes the underused servers by itself</label><span class="sp"></span><button class="sm" onclick="anaCheckAll(this)" title="Look at every idle, light and redundant server now (about ten seconds each)">🔍 Check the underused now</button></div>
    <table class="tbl lift ana-tbl"><thead><tr><th>Server</th><th>Where</th><th>CPU at the peak</th><th>Traffic at the peak</th><th>Tunnels</th><th>Use</th><th>Its own network</th><th></th></tr></thead><tbody>${sv}</tbody></table>`;
}

/* the CPU cores (RPS): read when the tab is opened, drawn by balBody (app.js) */
function anaCores(a) {
  if (!state.bal) {
    api("/api/balance").then(b => { state.bal = b; if (state.anaTab === "cpu") anaDraw(); }).catch(e => { const box = $("#ana-cores"); if (box) box.innerHTML = `<div class="err">${esc(e.message)}</div>`; });
    return '<div id="ana-cores"><div class="loading"><i class="spin"></i><span>Reading the cores…</span></div></div>';
  }

  return `<div id="ana-cores">${balBody(state.bal, a.servers)}</div>`;
}

window.anaCheck = async (id, btn) => {
  if (btn) { btn.disabled = true; btn.textContent = "…"; }
  try { const r = await api(`/api/analyst/server/${id}/check`, {method: "POST"}); toast(`✓ ${r.name}: ${r.verdict}` + (r.findings.length ? ` - ${r.findings.filter(f => f.id !== "nospeed").length} finding(s)` : ""), r.verdict === "healthy" ? "ok" : "bad"); }
  catch (e) { toast("✕ " + e.message, "bad"); }
  anaLoad();
};
window.anaCheckAll = async btn => {
  const list = (state.ana.servers || []).filter(s => ["idle", "redundant", "light"].includes(s.verdict) && s.online !== false);
  if (!list.length) { toast("No underused server", "ok"); return; }
  btn.disabled = true;
  for (let i = 0; i < list.length; i++) { btn.textContent = `🔍 ${i + 1} / ${list.length}…`; try { await api(`/api/analyst/server/${list[i].id}/check`, {method: "POST"}); } catch (e) { /* the next one */ } }
  toast(`✓ ${list.length} servers looked at`, "ok"); anaLoad();
};
window.anaTune = async id => {
  if (!await gconfirm("Fix the kernel settings of this server (BBR, fq, bigger buffers)? The speed is measured before and after, and the settings are put back when it did not help.")) return;
  try { const r = await api(`/api/analyst/server/${id}/tune`, {method: "POST"}); toast("The Doctor is working on it: see History", "ok"); setTimeout(anaLoad, 25000); return r; } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.anaUntune = async id => {
  if (!await gconfirm("Put the kernel settings of this server back as they were before the tuning?")) return;
  try { await api(`/api/analyst/server/${id}/untune`, {method: "POST"}); toast("Settings put back", "ok"); setTimeout(anaLoad, 6000); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.anaUnderAuto = async cb => { try { await api("/api/analyst/underuse", {method: "PUT", body: JSON.stringify({auto: cb.checked})}); toast(cb.checked ? "✓ The Doctor looks at the underused servers" : "The Doctor leaves them to you", "ok"); } catch (e) { cb.checked = !cb.checked; toast("✕ " + e.message, "bad"); } };

/* the Analyst's own row of tabs: Best today · Tunnels · At risk · Servers · CPU cores; at the right the range of Best today, or the days of history */
function anaDraw() {
  const box = $("#ana-box"), a = state.ana; if (!box || !a) return;
  const sm = a.summary, T = state.anaTab;
  const tabs = [["best", "Best today", 0], ["tunnels", "Tunnels", sm.chronic + sm.watch], ["risk", "At risk", a.risks.length + a.common.length], ["servers", "Servers", sm.idle + sm.redundant], ["cpu", "CPU cores", 0]];
  const body = T === "best" ? '<div id="ana-best"><div class="loading"><i class="spin"></i><span>Ranking the tunnels…</span></div></div>' : T === "tunnels" ? anaTunnels(a) : T === "risk" ? anaRisk(a) : T === "cpu" ? anaCores(a) : anaServers(a);
  box.innerHTML = `<div class="ana-card"><div class="ana-head"><div class="tabs srv-tabs">${tabs.map(([k, l, c]) => `<button class="${k === T ? "on" : ""}" onclick="anaPick('${k}')">${l}${c ? ` <i class="cnt">${c}</i>` : ""}</button>`).join("")}</div><span class="sp"></span>
      ${T === "best" ? `<div class="tabs srv-tabs bt-range">${[[6, "6 hours"], [24, "24 hours"], [168, "7 days"]].map(([h, l]) => `<button class="${state.bestHours === h ? "on" : ""}" data-h="${h}" onclick="bestRange(${h})">${l}</button>`).join("")}</div><span class="mut bt-up" id="bt-up"></span>` : T === "cpu" ? "" : `<select onchange="anaDays(this.value)" title="How many days of history to read">${[1, 3, 7, 14].map(d => `<option value="${d}" ${d === a.days ? "selected" : ""}>${d} day${d > 1 ? "s" : ""}</option>`).join("")}</select>`}</div>
    <div class="ana-body">${body}</div></div>`;
  if (T === "best") bestLoad();
}
