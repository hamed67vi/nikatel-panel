/* The analyst tab of the Tunnel Doctor: what the history says (profiles of the tunnels, what is at risk, what fixes what, how the servers are used) - read only.
   Loaded after app.js and uses its helpers ($, api, esc, state, refresh, toast). */
state.anaTab = "best";      // the Analyst opens on the best tunnels of the day

const anaDur = s => s == null ? "—" : s < 90 ? s + " s" : s < 5400 ? Math.round(s / 60) + " min" : (s / 3600).toFixed(1) + " h";
const anaChip = v => `<span class="ana-v ${esc(String(v).replace(" ", "-"))}">${esc(v)}</span>`;

window.anaLoad = async () => {
  const box = $("#ana-box"); if (!box) return;
  try { state.ana = await api("/api/analyst?days=" + (state.anaDays || 7)); } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  anaDraw();
};
window.anaPick = t => { state.anaTab = t; anaDraw(); };
window.anaDays = d => { state.anaDays = +d; anaLoad(); };

function anaTunnels(a) {
  const rows = a.tunnels.map(p => `<tr class="ana-${esc(p.verdict.replace(" ", "-"))}" data-q="${esc((p.name + " " + p.iran + " " + p.foreign + " " + p.verdict + " " + p.kind).toLowerCase())}"><td class="name">${esc(p.name)}</td><td class="mut">${esc(p.iran)} ⇄ ${esc(p.foreign)}</td><td>${p.availability == null ? "—" : p.availability + " %"}</td>
      <td>${p.outages}${p.outages ? ` <small class="mut">${p.outages_per_day}/day</small>` : ""}</td><td>${anaDur(p.mttr_secs)}</td><td>${p.rtt_base == null ? "—" : Math.round(p.rtt_base) + " ms"}${p.rtt_ratio && p.rtt_ratio >= 1.3 ? ` <small class="bad-t">now ${Math.round(p.rtt_now)}</small>` : ""}</td>
      <td class="mut">${(p.weak_hours || []).length ? p.weak_hours.map(h => String(h).padStart(2, "0")).join(" ") : "—"}</td><td>${anaChip(p.verdict)}${p.rising ? " <small class=\"bad-t\">rising</small>" : ""}</td></tr>`).join("");
  return `<table class="tbl ana-tbl"><thead><tr><th>Tunnel</th><th>Route</th><th>Available</th><th>Outages</th><th>Mean repair</th><th>Latency</th><th>Weak hours</th><th>Verdict</th></tr></thead><tbody>${rows || '<tr><td colspan="8" class="empty">No data yet.</td></tr>'}</tbody></table>`;
}

function anaRisk(a) {
  const risk = a.risks.map(r => `<div class="ana-line ${esc(r.level)}"><b>${esc(r.tunnel)}</b><span class="mut">${esc(r.iran)} ⇄ ${esc(r.foreign)}</span><span>${r.why.map(esc).join(" · ")}</span></div>`).join("") +
    a.common.map(c => `<div class="ana-line high"><b>${esc(c.server)}</b><span class="mut">${c.tunnels.map(esc).join(", ")}</span><span>${esc(c.text)}</span></div>`).join("");
  return `<div class="ana-list">${risk || '<div class="empty">Nothing stands out: no tunnel that is up shows a rising latency, a rising failure rate or a weak hour right now, and no outages come together.</div>'}</div>`;
}

function anaRepairs(a) {
  const rep = a.repairs, steps = Object.entries(rep.by_step || {}).sort((x, y) => y[1].attempts - x[1].attempts);

  if (!steps.length) return '<div class="empty">No repair has been recorded yet. Every smart repair from now on is kept here step by step; the plain restarts of the last 30 days are counted already.</div>';

  const cause = Object.entries(rep.by_cause || {}).flatMap(([c, d]) => Object.entries(d).map(([k, v]) => `<tr><td class="name">${esc(c)}</td><td>${esc(k)}</td><td>${v.attempts}</td><td>${v.rate == null ? "—" : v.rate + " %"}</td></tr>`)).join("");
  return `<table class="tbl ana-tbl"><thead><tr><th>Step</th><th>Tried</th><th>Healed</th><th>Rate</th><th>Usually in</th></tr></thead><tbody>${steps.map(([k, v]) => `<tr><td class="name">${esc(k)}</td><td>${v.attempts}</td><td>${v.healed}</td><td>${v.rate == null ? "—" : v.rate + " %"}</td><td>${anaDur(v.avg_secs)}</td></tr>`).join("")}</tbody></table>
    <div class="ana-sub">By cause</div><table class="tbl ana-tbl"><thead><tr><th>Cause</th><th>Step</th><th>Tried</th><th>Rate</th></tr></thead><tbody>${cause}</tbody></table>
    <small class="mut">The smart repair reads this before it climbs: a restart or a rebuild that failed 3 times in a row on a tunnel, or that healed under a tenth of the cases for a cause, is skipped and the story says so. A move or a switch is never skipped.</small>`;
}

function anaServers(a) {
  const hl = u => !u ? '<span class="mut">not looked at yet</span>' : u.error ? `<span class="ana-v watch" title="${esc(u.error)}">no answer</span>` : `<span class="ana-v ${u.verdict === "healthy" ? "stable" : u.verdict === "tunable" ? "watch" : "chronic"}" title="${esc((u.findings || []).map(f => f.text).join("\n") || "nothing wrong found")}">${u.verdict === "provider" ? "provider / route" : esc(u.verdict)}</span>${u.down_mbit ? ` <small class="mut">${u.down_mbit} Mbit/s</small>` : ""}${u.tune === "kept" ? ' <small class="ok-t">tuned</small>' : u.tune === "rolled back" ? ' <small class="bad-t">tuning undone</small>' : ""}`;
  const under = s => ["idle", "redundant", "light"].includes(s.verdict);
  const sv = a.servers.map(s => `<tr class="ana-${esc(s.verdict)}" data-q="${esc((s.name + " " + (s.city || "") + " " + (s.provider || "") + " " + s.verdict + " " + s.role).toLowerCase())}"><td class="name">${esc(s.name)}</td><td class="mut">${s.role === "iran" ? "Iran" : "foreign"} · ${esc(s.city || "")}</td><td>${s.cores}c</td><td>${s.peak_cpu_p95 == null ? "—" : s.peak_cpu_p95 + " %"}</td><td>${s.peak_mbps} <small class="mut">max ${s.peak_mbps_max}</small></td><td>${s.tunnels}${s.tunnels ? ` <small class="mut">${s.tunnels_mbps} Mbit/s</small>` : ""}</td><td>${anaChip(s.verdict)}</td>
      <td>${hl(s.underuse)}</td><td class="ana-act"><button class="sm" onclick="anaCheck(${s.id}, this)" title="Look at this server's own network now (about ten seconds; read only)">🔍</button>${s.underuse && (s.underuse.findings || []).some(f => f.fix === "tune") ? `<button class="sm pri" onclick="anaTune(${s.id})" title="Fix the kernel settings (BBR, fq, buffers): measured before and after, put back if it did not help">Fix</button>` : ""}${s.underuse && s.underuse.tuned ? `<button class="sm" onclick="anaUntune(${s.id})" title="Put the kernel settings back as they were">Undo</button>` : ""}</td></tr>
      ${s.underuse && (s.underuse.findings || []).filter(f => f.id !== "nospeed").length ? `<tr class="ana-why"><td></td><td colspan="8"><small>${s.underuse.findings.filter(f => f.id !== "nospeed").map(f => `${f.fix ? "🔧" : "⚠"} ${esc(f.text)}`).join(" &nbsp;·&nbsp; ")}</small></td></tr>` : ""}`).join("");
  return `<div class="ana-bar"><label class="chk" title="On: the Tunnel Doctor looks at the idle and light servers (two every 30 minutes, each at most every 12 hours) and fixes by itself what the kernel settings can fix. A fault of the provider's network is only reported."><input type="checkbox" ${a.underuse_auto ? "checked" : ""} onchange="anaUnderAuto(this)"> The Doctor looks at the underused servers and fixes what it can</label><span class="sp"></span><button class="sm" onclick="anaCheckAll(this)" title="Look at every idle, light and redundant server now (about ten seconds each)">🔍 Check the underused now</button></div>
    <table class="tbl ana-tbl"><thead><tr><th>Server</th><th>Where</th><th>Size</th><th>CPU at the peak</th><th>Mbit/s at the peak</th><th>Tunnels</th><th>Use</th><th>Its own network</th><th></th></tr></thead><tbody>${sv}</tbody></table>
    <small class="mut">Peak hours (server clock): ${a.peak_hours.map(h => String(h).padStart(2, "0") + ":00").join(", ") || "—"}, the four hours with the most traffic on the Iran servers. idle = almost nothing runs here; redundant = another server in the same city can take its load (judged by CPU). A server that carries little may be slow: <i>Its own network</i> is what the look found (TCP settings, lost packets while it downloads, stolen CPU, the line). The panel does not know prices.</small>`;
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

function anaDraw() {
  const box = $("#ana-box"), a = state.ana; if (!box || !a) return;
  const sm = a.summary, T = state.anaTab;
  const tabs = [["best", "Best today", 0], ["tunnels", "Tunnels", sm.chronic + sm.watch], ["risk", "At risk", a.risks.length + a.common.length], ["repairs", "What fixes what", 0], ["servers", "Servers", sm.idle + sm.redundant]];
  const body = T === "best" ? '<div id="ana-best"><div class="loading"><i class="spin"></i><span>Ranking the tunnels…</span></div></div>' : T === "tunnels" ? anaTunnels(a) : T === "risk" ? anaRisk(a) : T === "repairs" ? anaRepairs(a) : anaServers(a);
  box.innerHTML = `<div class="ana-card"><div class="ana-head"><div class="tabs srv-tabs">${tabs.map(([k, l, c]) => `<button class="${k === T ? "on" : ""}" onclick="anaPick('${k}')">${l}${c ? ` <i class="cnt">${c}</i>` : ""}</button>`).join("")}</div><span class="sp"></span>
      ${T === "best" ? "" : `<span class="tcount pstat"><span class="tc info">Tunnels <b>${sm.tunnels}</b></span><span class="tc ${sm.chronic ? "bad" : "ok"}">Chronic <b>${sm.chronic}</b></span><span class="tc ${sm.at_risk ? "warn" : "ok"}">At risk <b>${sm.at_risk}</b></span><span class="tc info">Available <b>${sm.availability} %</b></span></span>`}
      ${T === "best" ? `<div class="tabs srv-tabs bt-range">${[[6, "6 hours"], [24, "24 hours"], [168, "7 days"]].map(([h, l]) => `<button class="${state.bestHours === h ? "on" : ""}" onclick="bestRange(${h})">${l}</button>`).join("")}</div><span class="mut bt-up" id="bt-up"></span>` : `<select onchange="anaDays(this.value)" title="How many days of history to read">${[1, 3, 7, 14].map(d => `<option value="${d}" ${d === a.days ? "selected" : ""}>${d} day${d > 1 ? "s" : ""}</option>`).join("")}</select>`}</div>
    <div class="ana-body">${body}</div></div>`;
  if (T === "best") bestLoad();
}
