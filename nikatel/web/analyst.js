/* The analyst tab of the Tunnel Doctor: what the history says (profiles of the tunnels, what is at risk, what fixes what, how the servers are used) - read only.
   Loaded after app.js and uses its helpers ($, api, esc, state, refresh, toast). */
state.anaTab = "tunnels";

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
  const sv = a.servers.map(s => `<tr class="ana-${esc(s.verdict)}" data-q="${esc((s.name + " " + (s.city || "") + " " + (s.provider || "") + " " + s.verdict + " " + s.role).toLowerCase())}"><td class="name">${esc(s.name)}</td><td class="mut">${s.role === "iran" ? "Iran" : "foreign"} · ${esc(s.city || "")}</td><td>${s.cores}c</td><td>${s.peak_cpu_p95} %</td><td>${s.peak_mbps} <small class="mut">max ${s.peak_mbps_max}</small></td><td>${s.tunnels}${s.tunnels ? ` <small class="mut">${s.tunnels_mbps} Mbit/s</small>` : ""}</td><td>${anaChip(s.verdict)}</td><td class="mut ana-note">${esc(s.note)}</td></tr>`).join("");
  return `<table class="tbl ana-tbl"><thead><tr><th>Server</th><th>Where</th><th>Size</th><th>CPU at the peak (p95)</th><th>Mbit/s at the peak</th><th>Tunnels</th><th>Verdict</th><th>Why</th></tr></thead><tbody>${sv}</tbody></table>
    <small class="mut">Peak hours (server clock): ${a.peak_hours.map(h => String(h).padStart(2, "0") + ":00").join(", ") || "—"}, the four hours with the most traffic on the Iran servers. idle = almost nothing runs here; redundant = another server in the same city can take its load. The panel does not know prices: this says what is unused, not what it costs.</small>`;
}

function anaDraw() {
  const box = $("#ana-box"), a = state.ana; if (!box || !a) return;
  const sm = a.summary, T = state.anaTab;
  const tabs = [["tunnels", "Tunnels", sm.chronic + sm.watch], ["risk", "At risk", a.risks.length + a.common.length], ["repairs", "What fixes what", 0], ["servers", "Servers", sm.idle + sm.redundant]];
  const body = T === "tunnels" ? anaTunnels(a) : T === "risk" ? anaRisk(a) : T === "repairs" ? anaRepairs(a) : anaServers(a);
  box.innerHTML = `<div class="card ana-card"><div class="ana-head"><div class="tabs">${tabs.map(([k, l, c]) => `<button class="${k === T ? "on" : ""}" onclick="anaPick('${k}')">${l}${c ? ` <i class="cnt">${c}</i>` : ""}</button>`).join("")}</div><span class="sp"></span>
      <span class="tcount pstat"><span class="tc info">Tunnels <b>${sm.tunnels}</b></span><span class="tc ${sm.chronic ? "bad" : "ok"}">Chronic <b>${sm.chronic}</b></span><span class="tc ${sm.at_risk ? "warn" : "ok"}">At risk <b>${sm.at_risk}</b></span><span class="tc info">Available <b>${sm.availability} %</b></span></span>
      <select onchange="anaDays(this.value)" title="How many days of history to read">${[1, 3, 7, 14].map(d => `<option value="${d}" ${d === a.days ? "selected" : ""}>${d} day${d > 1 ? "s" : ""}</option>`).join("")}</select></div>
    <div class="ana-body">${body}</div></div>`;
}
