/* The Knowledge tab of the Tunnel Doctor: what it already knows about every pair of servers (which tunnel types connect between them, how well, which ones failed), what it remembers of the cuts of
   the tunnels (why, and what healed them) and the night survey that keeps it up to date - read from /api/doctor/knowledge. Loaded after app.js and uses its helpers ($, api, esc, toast, ago, when,
   apName, bwFmt, refresh). */

const knKind = r => `<span class="kn-k${r.measure === "full" ? " full" : ""}" title="${esc(apName(r.kind) + " - " + (r.score != null ? "score " + r.score : "it connects") + (r.latency_ms != null ? ", " + Math.round(r.latency_ms) + " ms" : "") + (r.loss_pct ? ", " + r.loss_pct + " % loss" : "") + (r.down_mbps != null ? ", download " + bwFmt(r.down_mbps) : "") + " (tested " + ago(r.at) + " ago)")}">${esc(apName(r.kind))} <b>${r.down_mbps != null ? bwFmt(r.down_mbps) : r.latency_ms != null ? Math.round(r.latency_ms) + " ms" : "works"}</b></span>`;

window.knLoad = async () => {
  const box = $("#kn-box"); if (!box) return;
  let k; try { k = await api("/api/doctor/knowledge"); } catch (e) { if (!state.kn) box.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  const was = state.kn ? JSON.stringify(state.kn) : "";
  state.kn = k;
  if (was && was === JSON.stringify(k) && box.querySelector(".kn-bar")) return;          // nothing changed: nothing is redrawn (a redraw is what made the page jump)
  knDraw();
};

/* the page is drawn again every 15 s: the box is filled from what was read last time at once (no "Reading..." box that shrinks the page and throws the scroll to the top), then read again */
window.knHtml = () => state.kn ? knMarkup(state.kn) : null;

function knDraw() {
  const box = $("#kn-box"); if (!box || !state.kn) return;
  const sc = box.closest(".ap-tbody"), top = sc ? sc.scrollTop : 0;
  box.innerHTML = knMarkup(state.kn);
  if (sc) sc.scrollTop = top;
}

function knMarkup(k) {
  const run = k.running, pairName = id => { const p = k.pairs.find(x => x.iran_id === id); return p ? `${p.iran} ⇄ ${p.foreign}` : "a pair"; };
  const hour = (nm, v) => `<input class="kn-hour" name="${nm}" type="number" min="0" max="23" step="1" value="${v}" onchange="knSet({${nm}: +this.value})">`;
  const bar = `<div class="kn-bar"><label class="chk" title="On: the pairs of servers that carry a tunnel are tested at night - one pair at a time, only on quiet servers, never while a repair needs them - so that the Doctor already knows what connects when a route is cut."><input type="checkbox" ${k.on ? "checked" : ""} onchange="knSet({on: this.checked})"> Test the pairs at night</label>
    <span class="mut">from</span>${hour("from_hour", k.from_hour)}<span class="mut">to</span>${hour("to_hour", k.to_hour)}<span class="mut" title="Equal hours mean any time. The clock is the panel's report time zone.">${k.window_open ? "· it is that time now" : "· not now"}</span><span class="sp"></span>
    ${run ? `<span class="busy-chip"><i class="spin"></i>${esc(pairName(run.iran_id))} · ${esc(apName(run.kind || "…"))} · ${run.done} of ${run.total}</span><button class="sm" onclick="knStop()">Stop</button>` : `<small class="mut">${k.ready.length} tunnel types per pair at night (${k.kinds} by hand)</small>`}</div>`;
  const rows = k.pairs.map(p => `<div class="ap-row kn-row"><b>${esc(p.iran)} ⇄ ${esc(p.foreign)}</b><span class="mut kn-n" title="Tunnels that run on this pair now">${p.tunnels ? p.tunnels + " tunnel" + (p.tunnels === 1 ? "" : "s") : "no tunnel"}</span>
      <span class="kn-best">${p.best.slice(0, 4).map(knKind).join("") || '<span class="mut">nothing tested yet</span>'}${p.best.length > 4 ? `<small class="mut">+${p.best.length - 4}</small>` : ""}</span>
      <span class="kn-bad" title="${esc(p.blocked.map(b => apName(b.kind) + (b.error ? ": " + b.error : "")).join("\n"))}">${p.blocked.length ? `✕ ${p.blocked.slice(0, 3).map(b => esc(apName(b.kind))).join(", ")}${p.blocked.length > 3 ? " +" + (p.blocked.length - 3) : ""}` : ""}</span>
      ${p.cuts ? `<span class="pill warn kn-cuts" title="Times this pair was cut in the last 7 days">${p.cuts} cut${p.cuts === 1 ? "" : "s"}</span>` : ""}
      <time class="mut" title="${p.at ? when(p.at) : ""}">${p.at ? ago(p.at) + " ago" : "never"}</time>
      <button class="sm" ${run ? "disabled" : ""} onclick="knSurvey(${p.iran_id}, ${p.foreign_id})" title="Test every tunnel type between these two servers now, one after the other (temporary tunnels, removed again): about 40 minutes">Test now</button></div>`).join("");
  const REASON = {gre_blocked: "GRE filtered", route_cut: "route cut", port_blocked: "port blocked", units_down: "service stopped", unknown: "no data", lossy_route: "lossy route", slow: "slow"};
  const inc = k.incidents.map(e => `<div class="ap-row kn-inc"><time class="mut" title="${when(e.at)}">${ago(e.at)} ago</time><b>${esc(e.tunnel)}</b><span class="mut">${esc(e.pair_name)}</span><span class="pill ${e.healed ? "ok" : "err"}">${esc(REASON[e.cause] || e.cause)}</span>
      <span class="kn-fix">${e.healed ? `healed by ${esc(e.kind ? apName(e.kind) : e.fix)} in ${e.secs} s` : `not healed (${e.steps.map(s => esc(s.step)).join(" → ") || "nothing tried"})`}</span></div>`).join("");
  return `${bar}<div class="kn-list">${rows || '<div class="dr-empty"><b>No pair yet</b><p>A pair appears here when a tunnel runs between two servers.</p></div>'}</div>
    <h4 class="kn-h">What the Doctor remembers of cuts</h4><div class="kn-list">${inc || '<div class="mut kn-none">No cut yet. When a tunnel is cut the Doctor writes here why it was cut and what healed it, and starts there the next time.</div>'}</div>`;
}

window.knSet = async body => { try { await api("/api/doctor/knowledge", {method: "PUT", body: JSON.stringify(body)}); toast("✓ Saved", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } knLoad(); };
window.knSurvey = async (i, f) => {
  if (!await gconfirm("Test every tunnel type between these two servers now? Temporary tunnels are built one after the other and removed again - about 40 minutes. A repair that needs the place stops it.")) return;
  try { await api("/api/doctor/knowledge/survey", {method: "POST", body: JSON.stringify({iran_id: i, foreign_id: f})}); toast("✓ The test has started", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
  knLoad();
};
window.knStop = async () => { try { await api("/api/doctor/knowledge/stop", {method: "POST"}); toast("✓ Stopping: the tunnel it tests is removed", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); } knLoad(); };
