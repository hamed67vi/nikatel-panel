/* PasarGuard > Usage > Configs: the usage of every CONFIG (host) and inbound of the panel as ONE compact bar chart, and the client apps beside it. (The owner, 2026-10-21: "which configs are little used or
   falling" - then, seeing the first page: "show the usage of all of them as a chart, compact, no extra information".) So the page is: a bar per config (the window chosen: 24 h / 7 days / 30 days), its number, its trend
   arrow and, only when it is true, a small "little used" / "falling" mark; everything else (the three windows, the peak connections, where the number comes from) is in the tip of the row.
   PasarGuard records usage per user and per node only, so this panel measures it: a counting table on the servers whose meter the owner switched on (services/cfgmeter.py) and the CDN reports of the DNS accounts
   (services/cfgcdn.py). The page asks GET /api/panels/{id}/pg/configs (services/cfgstats.py does the judging) and only draws; the pure helpers come first and use no DOM so a test can run them in node.
   It hooks into pg_usage.js (the section "configs", the card of the overview, pgUsageKick) and is loaded after it. */

/* ---- pure */
const PGC_FILTERS = [["all", "All"], ["little", "Little used"], ["falling", "Falling"], ["nodata", "No data"]];
const PGC_WINS = [["24h", "24 h"], ["7d", "7 days"], ["30d", "30 days"]];
const pgCfgFmt = n => typeof fmtB === "function" ? fmtB(n) : String(n);
const pgCfgNum = v => (Number(v) || 0).toLocaleString("en-US");
/* a trend of one window: {glyph, cls, text} - "▲ +12 %" in green, "▼ -35 %" in red, "▬ 3 %" grey; "" when there is nothing to compare, with the days still missing in `need` */
function pgCfgTrend(t) {
  if (!t) return {glyph: "", cls: "", text: "", need: 0};
  if (t.needs > 0) return {glyph: "", cls: "", text: "", need: Math.ceil(t.needs)};
  if (t.pct == null) return {glyph: "", cls: "", text: "", need: 0};
  const p = Math.round(t.pct), glyph = t.arrow === "up" ? "▲" : t.arrow === "down" ? "▼" : "▬";
  return {glyph, cls: t.arrow || "flat", text: (p > 0 ? "+" : "") + p + " %", need: 0};
}
/* the bytes of a row in a window; null = nothing on record (such rows are always put last) */
const pgCfgVal = (r, win) => r && r.view && r.view.has ? r.view.bytes[win] : null;
function pgCfgSort(rows, win, dir) {
  const d = dir > 0 ? 1 : -1, val = r => pgCfgVal(r, win);
  return [...(Array.isArray(rows) ? rows : [])].sort((x, y) => {
    const p = val(x), q = val(y);
    if ((p === null) !== (q === null)) return p === null ? 1 : -1;
    return d * ((p || 0) - (q || 0)) || String(x.remark || x.tag).localeCompare(String(y.remark || y.tag));
  });
}
function pgCfgFilter(rows, f) {
  return (Array.isArray(rows) ? rows : []).filter(r => f === "all" || !f || (r.badges || []).includes(f));
}
function pgCfgCounts(rows) {
  const c = {all: 0, little: 0, falling: 0, nodata: 0, shared: 0};
  for (const r of Array.isArray(rows) ? rows : []) { c.all++; for (const k of ["little", "falling", "nodata", "shared"]) if ((r.badges || []).includes(k)) c[k]++; }
  return c;
}
/* the configs that are on the chart: a host that is switched off is not a config the users get, so it is not listed (nor counted) */
const pgCfgHosts = d => ((d && d.hosts) || []).filter(r => !r.disabled);
/* "5 min ago" for a Unix time (0 = never) */
function pgCfgAgo(ts, now) {
  if (!ts) return "never";
  const s = Math.max(0, (now || Math.floor(Date.now() / 1000)) - ts);
  return s < 90 ? "just now" : s < 5400 ? Math.round(s / 60) + " min ago" : s < 129600 ? Math.round(s / 3600) + " h ago" : Math.round(s / 86400) + " days ago";
}

/* ---- state */
const pgCfgState = {id: 0, d: null, at: 0, err: "", tok: 0, view: "hosts", filter: "all", win: "7d", dir: -1, busy: ""};
function pgCfgReset(id) { Object.assign(pgCfgState, {id, d: null, at: 0, err: "", filter: "all", busy: "", view: "hosts"}); pgCfgState.tok++; }
const pgCfgBadge = (cls, text, tip) => `<span class="pg-cfg-bd ${cls}" title="${esc(tip)}">${esc(text)}</span>`;

/* ---- one row of the chart: name and marks, the bar, the number, the trend; the tip of the row holds the rest */
function pgCfgMarks(r, d) {
  const rules = (d && d.rules) || {}, b = r.badges || [], f = r.view && r.view.fall, out = [];
  if (b.includes("little")) out.push(pgCfgBadge("little", "little used", `A day of its last week carries under ${Math.round((rules.little_factor || 0.1) * 100)} % of what the average measured config carries${rules.little_threshold_bytes_per_day ? ` (under ${pgCfgFmt(rules.little_threshold_bytes_per_day)} a day)` : ""}`));
  if (b.includes("falling")) out.push(pgCfgBadge("falling", "falling", `The last ${f ? f.window : 7} complete days carried ${f && f.pct != null ? Math.abs(f.pct) + " % less" : "much less"} than the ${f ? f.window : 7} days before them`));
  if (b.includes("shared")) out.push(pgCfgBadge("shared", `×${(r.shared_with || []).length + 1}`, r.source === "origin" ? "Behind a CDN, and the CDN gives no number for this host: this is the origin port that every host of the inbound shares" : "Another host ends on the same server and port: no counter can tell them apart, so the number is for all of them and no verdict is given"));
  if (r.rare) out.push(pgCfgBadge("rare", "◆", `${r.network || r.protocol} is a newer transport: an old version of an app may not open it`));
  return out.join("");
}
function pgCfgTip(r) {
  const v = r.view || {}, parts = [r.remark || r.tag || "(no name)"];
  if (v.has) {
    parts.push(...PGC_WINS.map(([k, l]) => `${l}: ${pgCfgFmt(v.bytes[k])}`));
    if (v.peak && v.peak["7d"] != null) parts.push(`Peak connections: ${pgCfgNum(v.peak["7d"])}`);
    const t = pgCfgTrend(v.trend && v.trend["7d"]);
    if (!t.glyph && t.need) parts.push(`needs ${t.need} more days on record`);
  }
  if (r.source_text) parts.push(r.source_text);
  return parts.join(" · ");
}
function pgCfgRow(r, d, win, max) {
  const v = r.view, val = pgCfgVal(r, win), t = v && v.has ? pgCfgTrend(v.trend && v.trend[win]) : null;
  const w = val != null && max > 0 ? Math.max(val > 0 ? 1.5 : 0, val / max * 100) : 0, b = r.badges || [];
  const cls = b.includes("falling") ? "falling" : b.includes("little") ? "little" : b.includes("shared") ? "shared" : "";
  return `<div class="pg-cfg-r ${r.disabled ? "dis" : ""} ${val == null ? "none" : ""}" title="${esc(pgCfgTip(r))}"><span class="pg-cfg-n"><span class="ell">${esc(r.remark || r.tag || "(no name)")}</span>${pgCfgMarks(r, d)}</span>`
    + `<span class="pg-cfg-bar"><i class="${cls}" style="width:${w.toFixed(1)}%"></i></span><b class="pg-cfg-v">${val == null ? "—" : esc(pgCfgFmt(val))}</b>`
    + `<span class="pg-cfg-tr ${t && t.glyph ? t.cls : ""}">${t && t.glyph ? `${t.glyph} ${esc(t.text)}` : ""}</span></div>`;
}

/* ---- the chart card */
function pgCfgChartHtml(d) {
  const s = pgCfgState, hosts = s.view === "hosts", live = pgCfgHosts(d), c = pgCfgCounts(live), win = s.win;
  const all = hosts ? live : d.inbounds.map(i => ({...i, remark: i.tag, badges: [], shared_with: []}));
  const rows = pgCfgSort(hosts ? pgCfgFilter(all, s.filter) : all, win, s.dir), max = Math.max(0, ...all.map(r => pgCfgVal(r, win) || 0));
  const seg = (list, cur, fn) => `<div class="pg-useg">${list.map(([k, l]) => `<button type="button" class="${cur === k ? "on" : ""}" onclick="${fn}('${k}')">${l}</button>`).join("")}</div>`;
  const chips = hosts ? `<div class="pg-cfg-chips">${PGC_FILTERS.map(([k, l]) => `<button type="button" class="pg-cfg-chip ${s.filter === k ? "on" : ""} ${k}" onclick="pgCfgFilterSet('${k}')">${esc(l)} <b>${c[k]}</b></button>`).join("")}</div>` : "";
  const body = rows.map(r => pgCfgRow(r, d, win, max)).join("") || `<div class="pg-rc-none mut">${hosts ? "No config matches" : "No inbound"}</div>`;
  const since = d.since ? `On record since ${pgUsageTehran(new Date(d.since * 1000).toISOString()).slice(0, 10)}` : "";
  const total = d.total && d.total.has ? `<span class="pg-usage-total" title="${esc(since)}"><b>${esc(pgCfgFmt(d.total.bytes[win]))}</b></span>` : "";
  const dir = `<button type="button" class="pg-cfg-dir" onclick="pgCfgDir()" title="${s.dir < 0 ? "Most used first" : "Least used first"}" aria-label="Sort">${s.dir < 0 ? "▼" : "▲"}</button>`;
  return pgUsageCard(hosts ? "Usage of the configs" : "Usage of the inbounds", `${hosts ? live.length + " configs" : d.inbounds.length + " inbounds"}`,
    `<div class="pg-usage-row">${seg([["hosts", "Configs"], ["inbounds", "Inbounds"]], s.view, "pgCfgView")}${seg(PGC_WINS, win, "pgCfgWin")}${dir}${total}</div>${chips}<div class="pg-cfg-chart" data-sk="cfg" onscroll="pgUsageState.scroll[this.dataset.sk] = this.scrollTop">${body}</div>`, false, "pg-cfg-card");
}

/* ---- the counters (meters) and the client apps: small */
function pgCfgMetersHtml(d) {
  const now = d.now, rows = d.servers.map(s => {
    const st = s.enabled ? (s.last_error ? "bad" : s.stale || s.differs ? "wait" : "ok") : "off";
    const tip = s.enabled ? (s.last_error || `counting ${s.ports} port${s.ports === 1 ? "" : "s"} · last read ${pgCfgAgo(s.last_ok, now)}${s.differs ? " · The ports to count changed: the next turn (within five minutes) loads the new list" : ""}`) : `${s.plan_ports} port${s.plan_ports === 1 ? "" : "s"} to count`;
    const btn = s.enabled ? `<button type="button" class="sm" onclick="pgCfgMeter(${s.id}, false)" title="Delete the counting table from this server (the numbers already collected stay)">Remove</button>` : `<button type="button" class="pri sm" onclick="pgCfgMeter(${s.id}, true)" title="Put the counting table on this server: it only counts bytes">Turn on</button>`;
    return `<li class="pg-cfg-srv" title="${esc(tip)}"><i class="pg-cfg-dot ${st}"></i><b class="ell">${esc(s.name)}</b>${btn}</li>`;
  }).join("");
  const cdn = d.cdn.map(a => {
    const s = a.state || {}, st = s.ok === false ? "bad" : s.verified ? "ok" : "off", tip = s.ok === false ? (s.error || s.text || "the report could not be read") : s.verified ? `last read ${pgCfgAgo(s.at, now)}` : s.filter === false ? (s.text || "per-host numbers are not available") : "not read yet";
    return `<li class="pg-cfg-srv" title="${esc(tip)}"><i class="pg-cfg-dot ${st}"></i><b class="ell">${esc(a.name)}</b><button type="button" class="sm" data-a="${esc(a.id)}" data-f="${esc(a.sample)}" onclick="pgCfgCdnTest(this.dataset.a, this.dataset.f)" title="Read the report of one name now and say what the CDN answered">Test</button></li>`;
  }).join("");
  const any = d.servers.some(s => s.enabled), all = d.servers.length && d.servers.every(s => s.enabled);
  const bar = d.servers.length ? `<div class="pg-cfg-mbar">${all ? "" : '<button type="button" class="pri sm" onclick="pgCfgMeterAll(true)" title="Put the counting table on every server of the list">Turn on all</button>'}${any ? '<button type="button" class="sm" onclick="pgCfgMeterAll(false)" title="Delete the counting table from every server where it is on">Remove all meters</button>' : ""}</div>` : "";
  const list = rows ? `<ul class="pg-cfg-sl">${rows}</ul>` : '<div class="mut pg-usage-hint">No server of ours carries a node of this panel or a host address, so there is nothing to count on a server</div>';
  const cdnBox = cdn ? `<div class="pg-usage-sub">CDN reports <label class="pg-cfg-sw" title="Read the traffic report of each CDN domain of the DNS accounts once an hour (read only)"><input type="checkbox" ${d.cdn_on ? "checked" : ""} onchange="pgCfgCdnSet(this.checked)"><span>read them</span></label></div><ul class="pg-cfg-sl">${cdn}</ul>` : "";
  return pgUsageCard("Counters", `${d.servers.filter(s => s.enabled).length} of ${d.servers.length} servers`, `${bar}${list}${cdnBox}`, false, "pg-cfg-card");
}
function pgCfgAppsHtml() {
  const a = pgUsageState.apps, r = typeof pgUsageRangeLabel === "function" ? pgUsageRangeLabel() : "";
  let body;
  if (!a) body = '<div class="pg-rc-none mut">Reading the apps…</div>';
  else {
    const rows = pgUsageTopN((a.apps || []).map(x => ({name: x.name || "unknown app", v: +x.count || 0})), 6), tot = +a.total || rows.reduce((s, x) => s + x.v, 0);
    const col = rows.map((x, i) => ({...x, cls: x.other ? "po" : PGU_PAL[i % PGU_PAL.length], pct: tot > 0 ? Math.round(x.v / tot * 100) : 0}));
    const ring = pgUsageRingSvg(col.map(x => ({v: x.v, cls: x.cls, label: `${x.name}: ${pgCfgNum(x.v)} · ${x.pct}%`})), {size: 110, stroke: 12, big: pgCfgNum(tot) || "0", small: "fetches", label: "Subscription apps"});
    body = col.length ? `<div class="pg-rc-b">${ring}${pgUsageLeg(col.map(x => ({name: x.name, cls: x.cls, val: pgCfgNum(x.v), pct: x.pct + "%"})))}</div>` : '<div class="pg-rc-none mut">No fetch in this range</div>';
  }
  return pgUsageCard("Client apps", `subscription fetches · ${esc(r)}`, body, false, "pg-cfg-card pg-cfg-apps");
}
function pgCfgSecHtml() {
  const s = pgCfgState, d = s.d;
  if (!d) return s.err ? `<div class="err">${esc(s.err)}</div>` : pgUsageWait("Reading the configs…");
  const fresh = !d.since && !d.servers.some(x => x.enabled);          // nothing is counted yet: the counters come first (that is the one thing to do), else the chart and, under it, the counters and the apps
  return `${s.err ? `<div class="err">${esc(s.err)}</div>` : ""}<div class="pg-cfg-stack">${fresh ? pgCfgMetersHtml(d) : ""}${pgCfgChartHtml(d)}<div class="pg-cfg-below">${fresh ? "" : pgCfgMetersHtml(d)}${pgCfgAppsHtml()}</div></div>`;
}
/* the card of the overview: the counts and the first few configs that need a look */
function pgCfgRc(d) {
  const live = pgCfgHosts(d), c = pgCfgCounts(live), look = pgCfgSort(live.filter(r => (r.badges || []).some(b => b === "falling" || b === "little")), "7d", 1).slice(0, 4), measured = live.filter(r => r.state === "ok").length;
  const rows = look.map(r => `<li onclick="pgUsageSec('configs')" title="${esc(r.remark)}"><span class="ell">${esc(r.remark)}</span>${pgCfgMarks({...r, badges: (r.badges || []).filter(b => b === "falling" || b === "little"), rare: false}, d)}</li>`).join("");
  const sub = measured ? `${measured} of ${live.length} measured` : `${live.length} configs`;
  const body = `<div class="pg-cfg-rc"><div class="pg-cfg-rcn"><span><b>${c.little}</b><small>little used</small></span><span><b>${c.falling}</b><small>falling</small></span><span><b>${c.nodata}</b><small>no data yet</small></span></div>${rows ? `<ul class="pg-cfg-rcl">${rows}</ul>` : `<div class="pg-rc-none mut">${measured ? "Nothing little used or falling" : "Turn on the meters to see the usage"}</div>`}</div>`;
  return pgUsageRc("Configs", sub, body, "configs");
}

/* ---- drawing and asking */
function pgCfgDraw() {
  if (typeof pgUsagePut !== "function" || !pgUsageIsPg()) return;
  if (pgCfgState.id !== pgUsageState.id) pgCfgReset(pgUsageState.id);
  pgUsagePut("#pg-usage-configs", pgCfgSecHtml());
}
async function pgCfgLoad(force) {
  const s = pgCfgState, id = pgUsageState.id, mine = ++s.tok;
  try { const d = await pgApi(id, "/configs" + (force ? "?refresh=true" : "")); if (s.id !== id || mine !== s.tok) return; if (!d || !Array.isArray(d.hosts)) throw new Error("the panel did not give the configs"); s.d = d; s.at = Date.now(); s.err = ""; }
  catch (e) { if (s.id !== id || mine !== s.tok) return; s.err = (e && e.message) || String(e); }
  pgCfgDraw(); if (typeof pgUsageDrawCards === "function") pgUsageDrawCards();
}
function pgCfgKick(force) {
  const s = pgCfgState;
  if (s.id !== pgUsageState.id) pgCfgReset(pgUsageState.id);
  if (force || !s.d || Date.now() - s.at > 60000) pgCfgLoad(force);
}
window.pgCfgFilterSet = k => { pgCfgState.filter = k; pgCfgDraw(); };
window.pgCfgView = k => { pgCfgState.view = k; pgCfgDraw(); };
window.pgCfgWin = k => { pgCfgState.win = k; pgCfgDraw(); };
window.pgCfgDir = () => { pgCfgState.dir = -pgCfgState.dir; pgCfgDraw(); };
async function pgCfgPost(path, body, okText) {
  const s = pgCfgState, id = s.id;
  s.busy = path;
  try { const r = await pgApi(id, path, {method: "POST", body}); toast("✓ " + (okText(r) || "done"), "ok"); return r; }
  catch (e) { toast("✕ " + ((e && e.message) || e), "bad"); return null; }
  finally { s.busy = ""; pgCfgLoad(true); }
}
window.pgCfgMeter = async (sid, on) => {
  const srv = ((pgCfgState.d || {}).servers || []).find(x => x.id === sid), name = srv ? srv.name : "this server";
  const yes = await gconfirm(on ? `Put the counting table on ${name}? It only counts the bytes and connections of its ${srv ? srv.plan_ports : 0} ports: nothing is stopped, redirected or restarted, and Remove deletes it.` : `Remove the counting table from ${name}? The numbers collected so far stay.`, {danger: false});
  if (yes) await pgCfgPost("/configs/meter", {server_id: sid, on}, r => on ? `${name}: counting ${r.ports.length} ports` : `${name}: the table is removed`);
};
window.pgCfgMeterAll = async on => {
  const d = pgCfgState.d || {}, n = (d.servers || []).filter(x => on ? !x.enabled : x.enabled).length;
  const yes = await gconfirm(on ? `Put the counting table on ${n} server${n === 1 ? "" : "s"}? It only counts the bytes and connections of the listed ports: nothing is stopped, redirected or restarted, and Remove deletes it.` : `Remove the counting table from ${n} server${n === 1 ? "" : "s"}? The numbers collected so far stay.`, {danger: false});
  if (yes) await pgCfgPost("/configs/meter", {on}, r => { const bad = Object.entries(r.results || {}).filter(([, v]) => v !== "ok"); if (bad.length) toast("✕ " + bad.map(([k, v]) => `${k}: ${v}`).join(" · "), "bad"); return `${Object.keys(r.results || {}).length - bad.length} server${Object.keys(r.results || {}).length - bad.length === 1 ? "" : "s"} done`; });
};
window.pgCfgCdnSet = async on => { await pgCfgPost("/configs/cdn", {on}, r => r.cdn_on ? "the CDN reports are read once an hour" : "the CDN reports are not read"); };
window.pgCfgCdnTest = async (account, fqdn) => { await pgCfgPost("/configs/cdn-test", {account, fqdn}, r => r.text || "tested"); };
