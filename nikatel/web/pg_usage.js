/* PasarGuard tab: usage and live statistics of a panel (READ ONLY: nothing here changes anything on the panel). Registers itself in pgTabs (see pg_common.js).
   The pure functions (Jalali calendar, Tehran time, SVG bars, sorting) come first and use no DOM, so a test can run them in node; the page code follows.
   Days are Tehran days (UTC+3:30, no daylight saving): the server (services/pg_usage.py) decides where a day starts, this file only shows the dates. */

/* ---- pure: Gregorian <-> Jalali (the same 33-year-cycle arithmetic as services/pg_usage.py) */
function pgUsageG2J(gy, gm, gd) {
  const cum = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334], gy2 = gm > 2 ? gy + 1 : gy;
  let days = 355666 + 365 * gy + Math.floor((gy2 + 3) / 4) - Math.floor((gy2 + 99) / 100) + Math.floor((gy2 + 399) / 400) + gd + cum[gm - 1];
  let jy = -1595 + 33 * Math.floor(days / 12053);
  days %= 12053;
  jy += 4 * Math.floor(days / 1461);
  days %= 1461;
  if (days > 365) { jy += Math.floor((days - 1) / 365); days = (days - 1) % 365; }
  return days < 186 ? [jy, 1 + Math.floor(days / 31), 1 + days % 31] : [jy, 7 + Math.floor((days - 186) / 30), 1 + (days - 186) % 30];
}
function pgUsageJ2G(jy, jm, jd) {
  jy += 1595;
  let days = -355668 + 365 * jy + Math.floor(jy / 33) * 8 + Math.floor((jy % 33 + 3) / 4) + jd + (jm < 7 ? (jm - 1) * 31 : (jm - 7) * 30 + 186);
  let gy = 400 * Math.floor(days / 146097);
  days %= 146097;
  if (days > 36524) { days--; gy += 100 * Math.floor(days / 36524); days %= 36524; if (days >= 365) days++; }
  gy += 4 * Math.floor(days / 1461);
  days %= 1461;
  if (days > 365) { gy += Math.floor((days - 1) / 365); days = (days - 1) % 365; }
  let gd = days + 1;
  const leap = (gy % 4 === 0 && gy % 100 !== 0) || gy % 400 === 0, len = [0, 31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  let gm = 0;
  while (gm < 13 && gd > len[gm]) { gd -= len[gm]; gm++; }
  return [gy, gm, gd];
}
/* '2026-10-14' -> '1405/07/22'; '' for anything that is not a date */
function pgUsageJalaliOf(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
  if (!m) return "";
  const [y, mo, d] = [+m[1], +m[2], +m[3]];
  if (mo < 1 || mo > 12 || d < 1 || d > 31) return "";
  const j = pgUsageG2J(y, mo, d), p = n => String(n).padStart(2, "0");
  return `${String(j[0]).padStart(4, "0")}/${p(j[1])}/${p(j[2])}`;
}

/* ---- pure: Tehran time, numbers, sorting, the bar chart */
/* an ISO moment (UTC; a moment without a zone is taken as UTC, as Marzban sends it) as "YYYY-MM-DD HH:MM" in Tehran time (UTC+3:30, no daylight saving); "" when it is not a moment */
function pgUsageTehran(iso) {
  const s = String(iso || "").trim(), t = Date.parse(/(Z|[+-]\d\d:?\d\d)$/.test(s) ? s : s + "Z");
  if (!s || !Number.isFinite(t)) return "";
  const d = new Date(t + 12600000), p = n => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}
function pgUsageGbText(v) {
  const n = Number(v);
  return (Number.isFinite(n) ? n : 0).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2}) + " GB";
}
/* rows sorted by one key: text keys A-Z, numbers big to small (dir -1) or small to big (dir 1); the input array is not changed */
function pgUsageSortRows(rows, key, dir) {
  const d = dir < 0 ? -1 : 1;
  return [...(Array.isArray(rows) ? rows : [])].sort((a, b) => {
    const x = a == null ? null : a[key], y = b == null ? null : b[key];
    if (typeof x === "string" || typeof y === "string") return d * String(x == null ? "" : x).localeCompare(String(y == null ? "" : y));
    return d * ((Number(x) || 0) - (Number(y) || 0));
  });
}
/* the SVG text of a small bar chart; series = [{label, gb, jalali}] (`gb` is the value, whatever it counts); every bar has a tooltip; no library.
   o.valueText(v) writes a value for the tooltip, o.maxText(max) the note in the corner and o.none the note of a chart with nothing in it (the defaults are for GB) */
function pgUsageBarsSvg(series, o = {}) {
  const W = o.width || 640, H = o.height || 150, L = 8, R = 8, T = 18, B = 20;
  const pts = (Array.isArray(series) ? series : []).filter(p => p && Number.isFinite(+p.gb));
  const vt = typeof o.valueText === "function" ? o.valueText : v => v.toFixed(2) + " GB", mt = typeof o.maxText === "function" ? o.maxText : m => "max " + pgUsageGbText(m), none = o.none || "no traffic in this range";
  const head = `<svg class="pg-usage-svg" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="usage per ${esc(o.unit || "day")}">`;
  if (!pts.length) return `${head}<text class="pg-usage-ax" x="${W / 2}" y="${H / 2}" text-anchor="middle">No data</text></svg>`;
  const max = Math.max(0, ...pts.map(p => +p.gb)), pw = W - L - R, ph = H - T - B, step = pw / pts.length, bw = Math.max(1, step * 0.74), every = Math.ceil(pts.length / 8);
  const bars = pts.map((p, i) => {
    const v = +p.gb, h = max > 0 && v > 0 ? Math.max(1, v / max * ph) : 0, x0 = L + i * step, x = x0 + (step - bw) / 2;
    const tip = `${p.label}${p.jalali ? " · " + p.jalali : ""}: ${vt(v)}`;
    return `<g><title>${esc(tip)}</title><rect class="pg-usage-hit" x="${x0.toFixed(1)}" y="${T}" width="${step.toFixed(1)}" height="${ph}"/>${h ? `<rect class="pg-usage-bar${v === max ? " top" : ""}" x="${x.toFixed(1)}" y="${(T + ph - h).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="2"/>` : ""}</g>`;
  }).join("");
  const labels = pts.map((p, i) => i % every === 0 ? `<text class="pg-usage-ax" x="${(L + i * step + step / 2).toFixed(1)}" y="${H - 6}" text-anchor="middle">${esc(p.label)}</text>` : "").join("");
  return `${head}<line class="pg-usage-line" x1="${L}" y1="${T + ph}" x2="${W - R}" y2="${T + ph}"/><text class="pg-usage-ax" x="${L}" y="11">${max > 0 ? esc(mt(max)) : esc(none)}</text>${bars}${labels}</svg>`;
}

/* ---- state: what the tab knows. The Panels page draws itself again every 15 s and calls render() each time: render paints from this state AT ONCE (no "loading" flash, the
   scroll position stays) and asks the server only for what is older than the server's own cache (counters 15 s, ranges and top users 30 s). */
const pgUsageState = {id: 0, type: "pasarguard", ranges: null, rangesAt: 0, preset: "last7", from: "", to: "", ov: null, ovAt: 0, range: null, rangeKey: "", rangeAt: 0, top: null, topKey: "", topAt: 0, admins: null,
  look: null, lookBusy: false, lookErr: "", lookStale: false, kind: "admin", name: "", topSort: "used_traffic", topAdmin: "", err: {}, scroll: {}, sort: {range: {key: "gb", dir: -1}, look: {key: "gb", dir: -1}},
  counts: null, countsKey: "", countsAt: 0, metric: "online", countsAdmin: "", apps: null, appsKey: "", appsAt: 0, appsAdmin: ""};
const pgUsageTok = {};            // one counter per kind of question: a newer question drops the answer of an older one
const pgUsageMemo = new WeakMap();
const pgUsageIsPg = () => pgUsageState.type === "pasarguard";
const pgUsageQuery = () => { const s = pgUsageState; return `preset=${encodeURIComponent(s.preset)}` + (s.preset === "custom" ? `&from=${encodeURIComponent(s.from)}&to=${encodeURIComponent(s.to)}` : ""); };
const pgUsageTopKey = () => `${pgUsageState.topSort}|${pgUsageState.topAdmin}`;
const pgUsageTick = k => (pgUsageTok[k] = (pgUsageTok[k] || 0) + 1);
const pgUsageWait = text => `<div class="loading"><i class="spin"></i><span>${esc(text)}</span></div>`;
const pgUsageErr = msg => msg ? `<div class="err">${esc(msg)}</div>` : "";
/* put html into a section: nothing is touched when it is the same as last time, and the lists inside (data-sk) keep their scroll position */
function pgUsagePut(sel, html) {
  const el = $(sel); if (!el || pgUsageMemo.get(el) === html) return;
  el.innerHTML = html; pgUsageMemo.set(el, html);
  el.querySelectorAll("[data-sk]").forEach(b => { b.scrollTop = pgUsageState.scroll[b.dataset.sk] || 0; });
}
function pgUsageReset(id, type) {
  const s = pgUsageState;
  Object.assign(s, {id, type, ov: null, ovAt: 0, range: null, rangeKey: "", rangeAt: 0, top: null, topKey: "", topAt: 0, admins: null, look: null, lookBusy: false, lookErr: "", lookStale: false,
    kind: "admin", name: "", topSort: "used_traffic", topAdmin: "", err: {}, scroll: {}, preset: type === "marzban" ? "last30" : "last7",      // a month is the useful default for an admin's usage on Marzban
    counts: null, countsKey: "", countsAt: 0, metric: "online", countsAdmin: "", apps: null, appsKey: "", appsAt: 0, appsAdmin: ""});
  Object.keys(pgUsageTok).forEach(k => pgUsageTok[k]++);        // what is still on its way for the old panel is dropped
}

/* ---- the range bar: a preset (with its dates in both calendars) or two dates of your own, each with its Jalali date beside it */
function pgUsagePresetInfo(key) { const r = pgUsageState.ranges; return r && r.presets.find(p => p.preset === key); }
function pgUsageBarHtml() {
  const s = pgUsageState, r = s.ranges || {presets: []}, cur = pgUsagePresetInfo(s.preset);
  const opt = (v, label) => `<option value="${v}" ${s.preset === v ? "selected" : ""}>${esc(label)}</option>`;
  const names = {today: "Today", last7: "Last 7 days", last30: "Last 30 days"}, ml = k => { const p = pgUsagePresetInfo(k); return p ? p.label : ""; };
  const sel = `<select id="pg-usage-preset" onchange="pgUsagePreset(this.value)" title="Whole Tehran days: a day starts at 00:00 Tehran = 20:30 UTC of the day before">${opt("today", names.today)}${opt("last7", names.last7)}${opt("last30", names.last30)}${opt("jmonth", ml("jmonth") + " (this month)")}${opt("last_jmonth", ml("last_jmonth") + " (last month)")}${opt("custom", "Custom")}</select>`;
  const day = (id, iso) => `<input type="date" id="${id}" value="${esc(iso)}" max="${esc(r.tehran_today || "")}" onchange="pgUsageDates()"><span class="pg-usage-j mono" id="${id}-j">${esc(pgUsageJalaliOf(iso))}</span>`;
  const dates = s.preset === "custom"
    ? `${day("pg-usage-from", s.from)}<span class="mut">→</span>${day("pg-usage-to", s.to)}`
    : cur ? `<span class="pg-usage-dates mono">${esc(cur.from)} <small>${esc(cur.from_j)}</small> <span class="mut">→</span> ${esc(cur.to)} <small>${esc(cur.to_j)}</small></span><span class="pill">${cur.days} day${cur.days === 1 ? "" : "s"}</span>` : "";
  return pgBar(`${sel}${dates}`, `<span id="pg-usage-age" class="mut"></span><button class="sm" onclick="pgUsageRefresh()" title="Read everything again (the panel itself is asked at most every 15 s)">↻ Refresh</button>`);
}
function pgUsageDrawAge() {
  const a = $("#pg-usage-age"), s = pgUsageState; if (!a) return;
  const age = s.ov ? s.ov.age_s + (Date.now() - s.ovAt) / 1000 : 0;
  a.textContent = !s.ov ? "" : age >= 1 ? `counters ${Math.round(age)} s old` : "counters just read";
}
window.pgUsagePreset = v => {
  const s = pgUsageState;
  if (v === "custom" && (!s.from || !s.to)) { const p = pgUsagePresetInfo("last7") || {}; s.from = p.from || ""; s.to = p.to || ""; }
  s.preset = v;
  pgUsagePut("#pg-usage-bar", pgUsageBarHtml()); pgUsageDrawAge(); pgUsageDrawRange(); pgUsageDrawPair(); pgUsageKick(false); pgUsageRelook();
};
window.pgUsageDates = () => {
  const f = $("#pg-usage-from"), t = $("#pg-usage-to"), s = pgUsageState; if (!f || !t) return;
  $("#pg-usage-from-j").textContent = pgUsageJalaliOf(f.value); $("#pg-usage-to-j").textContent = pgUsageJalaliOf(t.value);
  if (!f.value || !t.value) return;
  if (f.value > t.value) { toast("✕ The first day is after the last day", "bad"); return; }
  s.from = f.value; s.to = t.value;
  pgUsageDrawRange(); pgUsageDrawPair(); pgUsageKick(false); pgUsageRelook();
};

/* ---- the counters */
function pgUsageCardsHtml(ov) {
  const n = v => v == null ? null : Number(v).toLocaleString("en-US"), pct = v => v == null ? null : v + "%", up = (v, c) => v > 0 ? c : "";
  const tiles = [["Users", n(ov.total), ""], ["Online", n(ov.online), up(ov.online, "ok")], ["Active", n(ov.active), ""], ["Limited", n(ov.limited), up(ov.limited, "warn")], ["Expired", n(ov.expired), up(ov.expired, "bad")], ["Disabled", n(ov.disabled), ""], ["On hold", n(ov.on_hold), ""],
    ["CPU", pct(ov.cpu_pct), ov.cpu_pct > 85 ? "bad" : ""], ["Memory", pct(ov.mem_pct), ov.mem_pct > 90 ? "bad" : ""], ["Disk", pct(ov.disk_pct), ov.disk_pct > 90 ? "bad" : ""],
    ["Speed ↓ / ↑", ov.speed_in == null && ov.speed_out == null ? null : `${fmtB(ov.speed_in)}/s / ${fmtB(ov.speed_out)}/s`, "wide"],
    ["Traffic ↓ / ↑", ov.traffic_in == null && ov.traffic_out == null ? null : `${fmtB(ov.traffic_in)} / ${fmtB(ov.traffic_out)}`, "wide"]];
  const ver = ov.version ? `<small class="mut pg-usage-ver">${esc(ov.panel_type === "marzban" ? "Marzban" : "PasarGuard")} ${esc(ov.version)}${ov.uptime_s ? " · up " + Math.floor(ov.uptime_s / 86400) + " d" : ""}</small>` : "";
  return `<div class="tiles pg-usage-tiles">${tiles.filter(t => t[1] != null).map(([l, v, c]) => `<div class="tile ${c}"><small>${esc(l)}</small><b>${esc(v)}</b></div>`).join("")}</div>${ver}`;
}
function pgUsageDrawCards() {
  const s = pgUsageState;
  pgUsagePut("#pg-usage-cards", pgUsageErr(s.err.cards) + (s.ov ? pgUsageCardsHtml(s.ov) : s.err.cards ? "" : pgUsageWait("Reading the counters…")));
  pgUsageDrawAge();
}
async function pgUsageLoadCards() {
  const s = pgUsageState, id = s.id, mine = pgUsageTick("cards");
  try { const ov = await pgApi(id, "/usage/overview"); if (s.id !== id || mine !== pgUsageTok.cards) return; s.ov = ov; s.ovAt = Date.now(); s.err.cards = ""; }
  catch (e) { if (s.id !== id || mine !== pgUsageTok.cards) return; s.err.cards = (e && e.message) || String(e); }
  pgUsageDrawCards();
}

/* ---- by node (sortable: click a header) */
function pgUsageNodeTable(which, rows) {
  const s = pgUsageState.sort[which], extra = rows.some(r => r.down_gb != null), tip = "The traffic of the node itself: all of it, not only the users' usage";
  const cols = [["name", "Node", ""], ["gb", "Users GB", ""], ["share", "Share", ""], ...(extra ? [["down_gb", "Node ↓ GB", tip], ["up_gb", "Node ↑ GB", tip]] : [])];
  const th = ([k, l, t]) => `<th class="pg-usage-th ${s.key === k ? "on" : ""}" onclick="pgUsageSortBy('${which}', '${k}')" title="${esc(t || "Click to sort")}">${esc(l)}${s.key === k ? (s.dir < 0 ? " ▼" : " ▲") : ""}</th>`;
  const body = pgUsageSortRows(rows, s.key, s.dir).map(r => `<tr><td class="name">${esc(r.name)}</td><td class="pg-usage-num">${esc(pgUsageGbText(r.gb))}</td><td class="pg-usage-nw"><span class="pg-usage-share" title="${(r.share * 100).toFixed(1)}%"><i style="width:${Math.min(100, r.share * 100).toFixed(1)}%"></i></span> <small class="mut">${(r.share * 100).toFixed(1)}%</small></td>${extra ? `<td class="pg-usage-num mut">${esc(pgUsageGbText(r.down_gb))}</td><td class="pg-usage-num mut">${esc(pgUsageGbText(r.up_gb))}</td>` : ""}</tr>`).join("");
  return `<div class="lbox full pg-usage-nodes" data-sk="${which}" onscroll="pgUsageState.scroll[this.dataset.sk] = this.scrollTop"><table class="tbl lift"><thead><tr>${cols.map(th).join("")}</tr></thead><tbody>${body || `<tr><td colspan="${cols.length}" class="mut">No node has traffic in this range</td></tr>`}</tbody></table></div>`;
}
window.pgUsageSortBy = (which, key) => {
  const s = pgUsageState.sort[which], d = pgUsageState[which];
  s.dir = s.key === key ? -s.dir : (key === "name" ? 1 : -1); s.key = key;
  const box = $(which === "range" ? "#pg-usage-range-nodes" : "#pg-usage-look-nodes");
  if (box && d) box.innerHTML = pgUsageNodeTable(which, d.by_node || []);
};

/* ---- usage of the range (PasarGuard) */
function pgUsageTotalHtml(d, what) {
  const tb = d.total.gb >= 1024 ? ` <small class="mut">${(d.total.gb / 1024).toFixed(2)} TB</small>` : "";
  return `<div class="pg-usage-head"><span class="pg-usage-big">${esc(pgUsageGbText(d.total.gb))}</span>${tb}<span class="mut">${esc(what)} · ${esc(d.range.from)} <small>${esc(d.range.from_j)}</small> → ${esc(d.range.to)} <small>${esc(d.range.to_j)}</small> · ${d.range.days} day${d.range.days === 1 ? "" : "s"} · avg ${esc(pgUsageGbText(d.avg_per_day_gb))} per day</span></div>`;
}
function pgUsageRangeHtml(d, busy) {
  const unit = d.granularity === "hour" ? "hour" : "day";
  return `<div class="card pg-usage-card ${busy ? "pg-usage-busy" : ""}"><h3>Usage of all users <span class="sp"></span><small class="mut">per ${unit}, Tehran time${d.cached && d.age_s >= 1 ? " · cached " + Math.round(d.age_s) + " s" : ""}</small></h3>${pgUsageTotalHtml(d, "all users")}
    <div class="pg-usage-chart">${pgUsageBarsSvg(d.series, {unit})}</div>${(d.notes || []).map(x => `<small class="mut">${esc(x)}</small>`).join("")}
    <div id="pg-usage-range-nodes">${pgUsageNodeTable("range", d.by_node || [])}</div></div>`;
}
function pgUsageDrawRange() {
  const s = pgUsageState;
  if (!pgUsageIsPg()) { pgUsagePut("#pg-usage-range", '<div class="card pg-usage-card"><h3>Usage of the range</h3><div class="mut">Marzban answers the usage of all users only by timing out on a big panel, so it is not asked. Look up an admin (or one user) below: that works for the range chosen above.</div></div>'); return; }
  pgUsagePut("#pg-usage-range", pgUsageErr(s.err.range) + (s.range ? pgUsageRangeHtml(s.range, s.rangeKey !== pgUsageQuery()) : s.err.range ? "" : pgUsageWait("Reading the usage…")));
}
const pgUsageRangeIsOld = () => !!pgUsageState.range && Date.parse(pgUsageState.range.end) < Date.now() - 7200000;       // a range that ended long ago does not change: it is not asked again
async function pgUsageLoadRange() {
  const s = pgUsageState, id = s.id, key = pgUsageQuery(), mine = pgUsageTick("range");
  try { const d = await pgApi(id, `/usage/range?${key}`); if (s.id !== id || mine !== pgUsageTok.range) return; s.range = d; s.rangeKey = key; s.rangeAt = Date.now(); s.err.range = ""; }
  catch (e) { if (s.id !== id || mine !== pgUsageTok.range) return; s.err.range = (e && e.message) || String(e); if (s.rangeKey !== pgUsageQuery()) s.range = null; }
  pgUsageDrawRange();
}

/* ---- top users (the panel sorts and limits; 20 rows at most) */
function pgUsageTopHtml(d, busy) {
  const s = pgUsageState, labels = {used_traffic: "Most used", online_at: "Last online", data_limit: "Biggest limit", created_at: "Newest"};
  const stCls = v => v === "active" ? "ok" : v === "on_hold" ? "" : "err";
  const sorts = (d.sorts || ["used_traffic"]).map(k => `<option value="${k}" ${s.topSort === k ? "selected" : ""}>${labels[k] || k}</option>`).join("");
  const adm = `<option value="">All admins</option>` + (s.admins || []).map(a => `<option ${s.topAdmin === a ? "selected" : ""}>${esc(a)}</option>`).join("");
  const rows = (d.users || []).slice(0, 8).map(u => `<tr class="clk" data-u="${esc(u.username)}" onclick="pgUsageLookupUser(this.dataset.u)" title="Show this user's usage in the chosen range"><td class="name">${esc(u.username)}</td><td class="pg-usage-num">${esc(pgUsageGbText(u.used_gb))}</td><td class="pg-usage-num mut">${u.limit_gb == null ? "∞" : esc(pgUsageGbText(u.limit_gb))}${u.percent == null ? "" : ` <small>${u.percent}%</small>`}</td><td><span class="pill ${stCls(u.status)}">${esc(u.status)}</span></td><td class="mut pg-usage-x">${esc(u.admin)}</td><td class="mut mono pg-usage-x" title="${esc(pgUsageJalaliOf(u.expire))}">${esc(u.expire || "—")}</td><td class="mut mono" title="${esc(pgUsageJalaliOf(pgUsageTehran(u.online_at).slice(0, 10)))}">${esc(pgUsageTehran(u.online_at) || "—")}</td></tr>`).join("");
  return `<div class="card pg-usage-card"><h3>Top users <span class="sp"></span><small class="mut">${d.users.length} of ${Number(d.total).toLocaleString("en-US")}${d.cached && d.age_s >= 1 ? " · cached " + Math.round(d.age_s) + " s" : ""}</small></h3>
    <div class="pg-usage-row"><select onchange="pgUsageTopSet('topSort', this.value)">${sorts}</select><select onchange="pgUsageTopSet('topAdmin', this.value)">${adm}</select><small class="mut">the panel's own counters, not the range</small></div>
    <div class="lbox full pg-usage-topbox ${busy ? "pg-usage-busy" : ""}" data-sk="top" onscroll="pgUsageState.scroll[this.dataset.sk] = this.scrollTop"><table class="tbl lift"><thead><tr><th>User</th><th>Used</th><th>Limit</th><th>Status</th><th class="pg-usage-x">Admin</th><th class="pg-usage-x">Expires</th><th title="Tehran time">Last online</th></tr></thead><tbody>${rows || '<tr><td colspan="7" class="mut">No users</td></tr>'}</tbody></table></div></div>`;
}
function pgUsageDrawTop() {
  const s = pgUsageState;
  pgUsagePut("#pg-usage-top", pgUsageErr(s.err.top) + (s.top ? pgUsageTopHtml(s.top, s.topKey !== pgUsageTopKey()) : s.err.top ? "" : pgUsageWait("Reading the top users…")));
}
async function pgUsageLoadTop() {
  const s = pgUsageState, id = s.id, key = pgUsageTopKey(), mine = pgUsageTick("top");
  try { const d = await pgApi(id, `/usage/top-users?sort=${encodeURIComponent(s.topSort)}&limit=20${s.topAdmin ? "&admin=" + encodeURIComponent(s.topAdmin) : ""}`); if (s.id !== id || mine !== pgUsageTok.top) return; s.top = d; s.topKey = key; s.topAt = Date.now(); s.err.top = ""; }
  catch (e) { if (s.id !== id || mine !== pgUsageTok.top) return; s.err.top = (e && e.message) || String(e); if (s.topKey !== pgUsageTopKey()) s.top = null; }
  pgUsageDrawTop();
}
window.pgUsageTopSet = (k, v) => { pgUsageState[k] = v; pgUsageDrawTop(); pgUsageLoadTop(); };
async function pgUsageLoadAdmins() {
  const s = pgUsageState, id = s.id, mine = pgUsageTick("admins");
  let list = [];
  try { list = (await pgApi(id, "/usage/admins")).admins || []; } catch (e) { list = []; }          // the names only help typing; the tab works without them
  if (s.id !== id || mine !== pgUsageTok.admins) return;
  s.admins = list;
  const dl = $("#pg-usage-admins"); if (dl) dl.innerHTML = list.map(a => `<option value="${esc(a)}"></option>`).join("");
  pgUsageDrawTop(); pgUsageDrawPair();
}

/* ---- users over time (online / expired / limited) and the apps that fetch the subscription: two cards side by side, PasarGuard only */
const pgUsageMetrics = [["online", "Online"], ["expired", "Expired"], ["limited", "Limited"]];
const pgUsageCountsKey = () => `${pgUsageState.metric}|${pgUsageState.countsAdmin}|${pgUsageQuery()}`;
const pgUsageAppsKey = () => `${pgUsageState.appsAdmin}|${pgUsageQuery()}`;
const pgUsageEnded = d => !!d && !!d.range && Date.parse(d.range.end) < Date.now() - 7200000;          // a range that ended long ago does not change: it is not asked again
const pgUsageAdminOpts = (cur, first) => `<option value="">${first}</option>` + (pgUsageState.admins || []).map(a => `<option value="${esc(a)}" ${cur === a ? "selected" : ""}>${esc(a)}</option>`).join("");
function pgUsageCountsHtml(d, busy) {
  const s = pgUsageState, unit = d.period === "hour" ? "hour" : "day", n = v => Number(v).toLocaleString("en-US");
  const seg = pgUsageMetrics.map(([k, l]) => `<button type="button" class="${s.metric === k ? "on" : ""}" onclick="pgUsageMetric('${k}')">${l}</button>`).join("");
  const pts = d.series.map(p => { const tz = pgUsageTehran(p.t), hm = tz.slice(11, 16); return {label: unit === "hour" ? hm : tz.slice(5, 10), jalali: pgUsageJalaliOf(tz.slice(0, 10)) + (unit === "hour" ? " " + hm : ""), gb: p.count}; });
  const what = {online: "users were online", expired: "users expired", limited: "users ran out of data"}[d.metric] || "users";
  return `<div class="card pg-usage-card ${busy ? "pg-usage-busy" : ""}"><h3>Users over time <span class="sp"></span><small class="mut">per ${unit}, Tehran time${d.cached && d.age_s >= 1 ? " · cached " + Math.round(d.age_s) + " s" : ""}</small></h3>
    <div class="pg-usage-row"><span class="pg-admin-seg">${seg}</span><select onchange="pgUsageCountsAdmin(this.value)" title="Count the users of one admin only">${pgUsageAdminOpts(s.countsAdmin, "All admins")}</select></div>
    <div class="pg-usage-head"><span class="pg-usage-big">${n(d.count)}</span><span class="mut">${esc(what)} · ${esc(d.range.from)} <small>${esc(d.range.from_j)}</small> → ${esc(d.range.to)} <small>${esc(d.range.to_j)}</small></span></div>
    <div class="pg-usage-chart">${pgUsageBarsSvg(pts, {unit, valueText: v => n(v) + (v === 1 ? " user" : " users"), maxText: m => "peak " + n(m), none: "none in this range"})}</div></div>`;
}
function pgUsageAppsHtml(d, busy) {
  const s = pgUsageState, n = v => Number(v).toLocaleString("en-US");
  const rows = d.apps.map(a => `<tr><td class="name"><div class="ell" title="${esc(a.name)}">${esc(a.name || "unknown app")}</div></td><td class="pg-usage-nw"><span class="pg-usage-share" title="${a.percent}%"><i style="width:${Math.min(100, a.percent)}%"></i></span> <small class="mut">${a.percent}%</small></td><td class="pg-usage-num">${n(a.count)}</td></tr>`).join("");
  return `<div class="card pg-usage-card ${busy ? "pg-usage-busy" : ""}"><h3>Subscription apps <span class="sp"></span><small class="mut">fetches of the subscription link${d.cached && d.age_s >= 1 ? " · cached " + Math.round(d.age_s) + " s" : ""}</small></h3>
    <div class="pg-usage-row"><select onchange="pgUsageAppsAdmin(this.value)" title="Whose fetches are counted">${pgUsageAdminOpts(s.appsAdmin, "All users")}</select></div>
    <div class="pg-usage-head"><span class="pg-usage-big">${n(d.total)}</span><span class="mut">fetches · ${esc(d.range.from)} <small>${esc(d.range.from_j)}</small> → ${esc(d.range.to)} <small>${esc(d.range.to_j)}</small></span></div>
    <div class="lbox full pg-usage-nodes"><table class="tbl lift"><colgroup><col><col style="width:130px"><col style="width:86px"></colgroup><thead><tr><th data-prio="0">App</th><th>Share</th><th>Fetches</th></tr></thead><tbody>${rows || '<tr><td colspan="3" class="mut">No fetch in this range</td></tr>'}</tbody></table></div></div>`;
}
function pgUsageDrawPair() {
  const s = pgUsageState, pair = $("#pg-usage-pair"); if (!pair) return;
  pair.hidden = !pgUsageIsPg(); if (pair.hidden) return;
  pgUsagePut("#pg-usage-counts", pgUsageErr(s.err.counts) + (s.counts ? pgUsageCountsHtml(s.counts, s.countsKey !== pgUsageCountsKey()) : s.err.counts ? "" : pgUsageWait("Reading the user counts…")));
  pgUsagePut("#pg-usage-apps", pgUsageErr(s.err.apps) + (s.apps ? pgUsageAppsHtml(s.apps, s.appsKey !== pgUsageAppsKey()) : s.err.apps ? "" : pgUsageWait("Reading the apps…")));
}
async function pgUsageLoadCounts() {
  const s = pgUsageState, id = s.id, key = pgUsageCountsKey(), mine = pgUsageTick("counts");
  try { const d = await pgApi(id, `/usage/counts?metric=${encodeURIComponent(s.metric)}&${pgUsageQuery()}${s.countsAdmin ? "&admin=" + encodeURIComponent(s.countsAdmin) : ""}`); if (s.id !== id || mine !== pgUsageTok.counts) return; s.counts = d; s.countsKey = key; s.countsAt = Date.now(); s.err.counts = ""; }
  catch (e) { if (s.id !== id || mine !== pgUsageTok.counts) return; s.err.counts = (e && e.message) || String(e); if (s.countsKey !== pgUsageCountsKey()) s.counts = null; }
  pgUsageDrawPair();
}
async function pgUsageLoadApps() {
  const s = pgUsageState, id = s.id, key = pgUsageAppsKey(), mine = pgUsageTick("apps");
  try { const d = await pgApi(id, `/usage/sub-apps?${pgUsageQuery()}${s.appsAdmin ? "&admin=" + encodeURIComponent(s.appsAdmin) : ""}`); if (s.id !== id || mine !== pgUsageTok.apps) return; s.apps = d; s.appsKey = key; s.appsAt = Date.now(); s.err.apps = ""; }
  catch (e) { if (s.id !== id || mine !== pgUsageTok.apps) return; s.err.apps = (e && e.message) || String(e); if (s.appsKey !== pgUsageAppsKey()) s.apps = null; }
  pgUsageDrawPair();
}
window.pgUsageMetric = k => { pgUsageState.metric = k; pgUsageDrawPair(); pgUsageLoadCounts(); };
window.pgUsageCountsAdmin = v => { pgUsageState.countsAdmin = v; pgUsageDrawPair(); pgUsageLoadCounts(); };
window.pgUsageAppsAdmin = v => { pgUsageState.appsAdmin = v; pgUsageDrawPair(); pgUsageLoadApps(); };

/* ---- the lookup box: an admin or a user, for the range chosen at the top */
function pgUsageLookFormHtml() {
  const s = pgUsageState, adm = (s.admins || []).map(a => `<option value="${esc(a)}"></option>`).join("");
  return `<div class="pg-usage-row"><select id="pg-usage-kind" onchange="pgUsageState.kind = this.value">${["admin", "user"].map(k => `<option value="${k}" ${s.kind === k ? "selected" : ""}>${k === "admin" ? "Admin" : "User"}</option>`).join("")}</select>
    <input id="pg-usage-name" list="pg-usage-admins" placeholder="admin or user name" value="${esc(s.name)}" autocomplete="off" spellcheck="false" oninput="pgUsageState.name = this.value" onkeydown="if (event.key === 'Enter') pgUsageLookup()"><datalist id="pg-usage-admins">${adm}</datalist>
    <button class="sm pri" onclick="pgUsageLookup()">Show usage</button></div>`;
}
function pgUsageLookResultHtml(d) {
  if (!d) return '<div class="mut pg-usage-hint">Type an admin name (or click a user in the list below) to see the GB used in the range above, node by node.</div>';
  const life = d.lifetime_gb != null ? `<span class="pill" title="Everything the users of this admin ever used">lifetime ${esc(pgUsageGbText(d.lifetime_gb))}</span>` : "";
  const stale = pgUsageState.lookStale ? '<div class="pg-usage-stale">The range changed: press <b>Show usage</b> to ask again. A big admin can take up to a minute on Marzban.</div>' : "";
  return `${stale}${pgUsageTotalHtml(d, `${d.scope} ${d.name}`)}${life}${(d.notes || []).map(x => `<small class="mut">${esc(x)}</small>`).join("")}<div id="pg-usage-look-nodes">${pgUsageNodeTable("look", d.by_node || [])}</div>`;
}
function pgUsageDrawLook() {
  const s = pgUsageState;
  pgUsagePut("#pg-usage-look-res", s.lookBusy ? pgUsageWait("Asking the panel…" + (pgUsageIsPg() ? "" : " (a big admin can take up to a minute)")) : s.lookErr ? pgUsageErr(s.lookErr) : pgUsageLookResultHtml(s.look));
}
window.pgUsageLookup = async () => {
  const s = pgUsageState, id = s.id, k = $("#pg-usage-kind"), n = $("#pg-usage-name");
  if (k) s.kind = k.value;
  if (n) s.name = n.value;
  const name = s.name.trim(), kind = s.kind;
  if (!name) { toast("✕ Type a name first", "bad"); return; }
  const mine = pgUsageTick("look");
  Object.assign(s, {lookBusy: true, lookErr: "", lookStale: false}); pgUsageDrawLook();
  try { const d = await pgApi(id, `/usage/${kind}/${encodeURIComponent(name)}?${pgUsageQuery()}`); if (s.id !== id || mine !== pgUsageTok.look) return; s.look = d; }
  catch (e) { if (s.id !== id || mine !== pgUsageTok.look) return; s.look = null; s.lookErr = (e && e.message) || String(e); }
  s.lookBusy = false; pgUsageDrawLook();
};
window.pgUsageLookupUser = name => {
  const s = pgUsageState; s.kind = "user"; s.name = name;
  const k = $("#pg-usage-kind"), n = $("#pg-usage-name"); if (k) k.value = "user"; if (n) n.value = name;
  pgUsageLookup();
};
/* the range changed: PasarGuard answers a lookup in a blink, so it is asked again; a big Marzban admin takes up to a minute, so it waits for the button */
function pgUsageRelook() {
  const s = pgUsageState; if (!s.look) return;
  if (pgUsageIsPg()) { pgUsageLookup(); return; }
  s.lookStale = true; pgUsageDrawLook();
}

/* ---- the tab */
const pgUsageSkeleton = () => `<div class="pg-usage" id="pg-usage"><div id="pg-usage-bar"></div><div id="pg-usage-cards"></div>
  <div class="pg-usage-grid"><div class="pg-usage-col"><div id="pg-usage-range"></div></div>
    <div class="pg-usage-col pg-usage-pair" id="pg-usage-pair" hidden><div id="pg-usage-counts"></div><div id="pg-usage-apps"></div></div>
    <div class="pg-usage-col"><div id="pg-usage-top"></div><div class="card pg-usage-card pg-usage-look"><h3>Usage of an admin or a user</h3><div id="pg-usage-look-form"></div><div id="pg-usage-look-res"></div></div></div></div></div>`;      // (one screen: the range and its nodes | users over time and apps | the top users and the lookup - no scrolling on a desktop window; a narrow one stacks them)
function pgUsageFit() {      // the tab is as tall as what is left of the window, so nothing needs to scroll
  const el = $("#pg-usage"); if (!el) return;
  el.style.height = matchMedia("(min-width: 1100px) and (min-height: 560px)").matches ? Math.max(440, innerHeight - el.getBoundingClientRect().top - 12) + "px" : "";
}
if (typeof window.addEventListener === "function") window.addEventListener("resize", () => pgUsageFit());async function pgUsageLoadRanges() {
  const s = pgUsageState, id = s.id, r = await pgApi(id, "/usage/ranges");          // the dates of the presets: no question to the panel, so it is cheap
  if (s.id !== id) return;
  s.ranges = r; s.rangesAt = Date.now();
  if (!s.from || !s.to) { const p = pgUsagePresetInfo("last7") || {}; s.from = p.from || ""; s.to = p.to || ""; }
}
/* ask for whatever is older than the cache of the server (or all of it when `force`) */
function pgUsageKick(force) {
  const s = pgUsageState, now = Date.now();
  if (force || !s.ov || now - s.ovAt > 14000) pgUsageLoadCards();
  if (pgUsageIsPg() && (force || !s.range || s.rangeKey !== pgUsageQuery() || (now - s.rangeAt > 29000 && !pgUsageRangeIsOld()))) pgUsageLoadRange();
  if (pgUsageIsPg() && (force || !s.counts || s.countsKey !== pgUsageCountsKey() || (now - s.countsAt > 29000 && !pgUsageEnded(s.counts)))) pgUsageLoadCounts();
  if (pgUsageIsPg() && (force || !s.apps || s.appsKey !== pgUsageAppsKey() || (now - s.appsAt > 29000 && !pgUsageEnded(s.apps)))) pgUsageLoadApps();
  if (force || !s.top || s.topKey !== pgUsageTopKey() || now - s.topAt > 29000) pgUsageLoadTop();
  if (s.admins == null) pgUsageLoadAdmins();
  if (force || now - s.rangesAt > 300000) pgUsageLoadRanges().then(() => pgUsagePut("#pg-usage-bar", pgUsageBarHtml())).catch(() => { /* the dates on show stay as they are */ });
}
async function pgUsageRender(id, box) {
  const s = pgUsageState, type = (typeof pnType === "function" && pnType()) || "pasarguard";
  if (s.id !== id || s.type !== type) pgUsageReset(id, type);
  if (!s.ranges) {
    pgLoading(box, "Reading the ranges…");
    await pgUsageLoadRanges();
    if (!box.isConnected || (typeof pn !== "undefined" && pn.tab !== "usage")) return;          // another tab was opened meanwhile
  }
  pgPaint(box, pgUsageSkeleton());          // from what is known, at once: the first await comes after this
  pgUsagePut("#pg-usage-bar", pgUsageBarHtml()); pgUsageDrawCards(); pgUsageDrawRange(); pgUsageDrawPair(); pgUsageDrawTop();
  pgUsagePut("#pg-usage-look-form", pgUsageLookFormHtml()); pgUsageDrawLook();
  pgUsageKick(false);
  pgUsageFit(); setTimeout(pgUsageFit, 250);
}
window.pgUsageRefresh = () => { pgUsageKick(true); };

window.pgTabs.usage = {label: "Usage", order: 80, types: ["pasarguard", "marzban"], render: pgUsageRender};
