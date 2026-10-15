/* PasarGuard tab: usage and live statistics of a panel (READ ONLY: nothing here changes anything on the panel). Registers itself in pgTabs (see pg_common.js).
   The pure functions (Jalali calendar, Tehran time, SVG bars, sorting) come first and use no DOM, so a test can run them in node; the page code follows.
   Days are Tehran days (UTC+3:30, no daylight saving): the server (services/pg_usage.py) decides where a day starts, this file only shows the dates.
   The page (rebuilt 1.55.0, "very professional"): one bar (range buttons + dates), a band of counters, then two columns of cards - traffic and top users | users over time, subscription apps and the lookup. */

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
/* the top of a chart scale: the next 1, 2 or 5 times a power of ten above the biggest value (0 -> 1) */
function pgUsageNice(m) {
  if (!(m > 0)) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(m))), f = m / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}
/* the SVG text of a bar chart with a scale (4 grid lines and their numbers); series = [{label, gb, jalali}] (`gb` is the value, whatever it counts); every bar has a tooltip; no library.
   o.valueText(v) writes a value for the tooltip, o.axisText(v) a number of the scale, o.int = the values are whole numbers (the scale steps are whole too), o.none the note of a chart with nothing in it */
function pgUsageBarsSvg(series, o = {}) {
  const W = o.width || 720, H = o.height || 190, L = 46, R = 8, T = 10, B = 22;
  const pts = (Array.isArray(series) ? series : []).filter(p => p && Number.isFinite(+p.gb));
  const vt = typeof o.valueText === "function" ? o.valueText : v => v.toFixed(2) + " GB", none = pts.length ? (o.none || "No traffic in this range") : "No data";
  const at = typeof o.axisText === "function" ? o.axisText : v => String(+v.toFixed(v >= 100 ? 0 : 2));
  const head = `<svg class="pg-usage-svg" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="usage per ${esc(o.unit || "day")}">`;
  const max = Math.max(0, ...pts.map(p => +p.gb)); let top = pgUsageNice(max); if (o.int) top = Math.max(4, Math.ceil(max / 4) * 4);
  const pw = W - L - R, ph = H - T - B, step = pw / pts.length, bw = Math.max(1, Math.min(step * 0.72, 46)), every = Math.ceil(pts.length / 10);
  const grid = [0, 1, 2, 3, 4].map(k => { const y = T + ph - ph * k / 4; return `<line class="${k ? "pg-usage-gl" : "pg-usage-line"}" x1="${L}" y1="${y.toFixed(1)}" x2="${W - R}" y2="${y.toFixed(1)}"/><text class="pg-usage-ax" x="${L - 6}" y="${(y + 3.5).toFixed(1)}" text-anchor="end">${esc(at(top * k / 4))}</text>`; }).join("");
  const bars = pts.map((p, i) => {
    const v = +p.gb, h = max > 0 && v > 0 ? Math.max(2, v / top * ph) : 0, x0 = L + i * step, x = x0 + (step - bw) / 2;
    const tip = `${p.label}${p.jalali ? " · " + p.jalali : ""}: ${vt(v)}`;
    return `<g><title>${esc(tip)}</title><rect class="pg-usage-hit" x="${x0.toFixed(1)}" y="${T}" width="${step.toFixed(1)}" height="${ph}"/>${h ? `<rect class="pg-usage-bar${v === max ? " top" : ""}" x="${x.toFixed(1)}" y="${(T + ph - h).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="3"/>` : ""}</g>`;
  }).join("");
  const labels = pts.map((p, i) => i % every === 0 ? `<text class="pg-usage-ax" x="${(L + i * step + step / 2).toFixed(1)}" y="${H - 6}" text-anchor="middle">${esc(p.label)}</text>` : "").join("");
  return `${head}${grid}${bars}${labels}${max > 0 ? "" : `<text class="pg-usage-none" x="${(L + pw / 2).toFixed(1)}" y="${(T + ph / 2).toFixed(1)}" text-anchor="middle">${esc(none)}</text>`}</svg>`;
}

/* ---- pure: round stats (1.56.24, the owner: "easy on the eye, like a round stats menu"). A ring of parts, a one-value gauge, a bar strip with no axis, the biggest N rows + "Other". No DOM, no library. */
/* the SVG text of a ring (donut): parts = [{v, cls, label}] (a part of 0 is not drawn); o.total fixes the whole (a gauge: 100), o.big / o.small are the two lines in the middle; every arc has a tooltip */
function pgUsageRingSvg(parts, o = {}) {
  const size = o.size || 120, sw = o.stroke || 13, r = (size - sw) / 2, c = 2 * Math.PI * r, m = size / 2;
  const list = (Array.isArray(parts) ? parts : []).filter(p => p && Number.isFinite(+p.v) && +p.v > 0), tot = o.total > 0 ? o.total : list.reduce((a, p) => a + +p.v, 0);
  let off = 0;
  const arcs = tot > 0 ? list.map(p => {
    const len = Math.min(+p.v, tot) / tot * c, gap = list.length > 1 ? Math.min(3, len / 3) : 0, dash = Math.max(0.01, len - gap);
    const el = `<circle class="pg-ring-arc ${esc(p.cls || "")}" cx="${m}" cy="${m}" r="${r.toFixed(2)}" stroke-width="${sw}" stroke-dasharray="${dash.toFixed(2)} ${(c - dash).toFixed(2)}" stroke-dashoffset="${(-off).toFixed(2)}" transform="rotate(-90 ${m} ${m})"><title>${esc(p.label || "")}</title></circle>`;
    off += len; return el;
  }).join("") : "";
  const mid = (o.big != null ? `<text class="pg-ring-big" x="${m}" y="${o.small ? m + 2 : m + 6}" text-anchor="middle">${esc(o.big)}</text>` : "") + (o.small ? `<text class="pg-ring-small" x="${m}" y="${m + 16}" text-anchor="middle">${esc(o.small)}</text>` : "");
  return `<svg class="pg-ring" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" role="img" aria-label="${esc(o.label || "")}"><circle class="pg-ring-track" cx="${m}" cy="${m}" r="${r.toFixed(2)}" stroke-width="${sw}"/>${arcs}${mid}</svg>`;
}
/* a gauge: ONE percentage on a ring that turns amber and red (warn / bad are the limits) */
function pgUsageGaugeSvg(pct, o = {}) {
  const n = pct == null || pct === "" ? NaN : +pct, v = Number.isFinite(n) ? Math.max(0, Math.min(100, n)) : 0, warn = o.warn == null ? 75 : o.warn, bad = o.bad == null ? 90 : o.bad;
  return pgUsageRingSvg([{v, cls: v > bad ? "bad" : v > warn ? "warn" : "ok", label: `${o.name || ""} ${v}%`.trim()}], {size: o.size || 84, stroke: o.stroke || 9, total: 100, big: Number.isFinite(n) ? Math.round(v) + "%" : "—", label: o.name});
}
/* a strip of bars with no scale (the shape of a range at a glance); series = [{label, gb}] */
function pgUsageSparkSvg(series, o = {}) {
  const pts = (Array.isArray(series) ? series : []).filter(p => p && Number.isFinite(+p.gb)), max = Math.max(0, ...pts.map(p => +p.gb));
  if (!pts.length) return "";
  const H = o.height || 52, step = 14, W = pts.length * step, vt = typeof o.valueText === "function" ? o.valueText : v => pgUsageGbText(v);
  const bars = pts.map((p, i) => { const v = +p.gb, h = max > 0 && v > 0 ? Math.max(3, v / max * (H - 2)) : 2; return `<rect class="pg-spark-b${v === max && max > 0 ? " top" : ""}" x="${(i * step + 2).toFixed(1)}" y="${(H - h).toFixed(1)}" width="${step - 4}" height="${h.toFixed(1)}" rx="2.5"><title>${esc(p.label)}: ${esc(vt(v))}</title></rect>`; }).join("");
  return `<svg class="pg-spark" viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="${esc(o.label || "usage")}">${bars}</svg>`;
}
/* the biggest n rows of [{name, v}] and one "Other" row with the rest (rows of 0 are left out) */
function pgUsageTopN(rows, n) {
  const s = (Array.isArray(rows) ? rows : []).filter(r => r && Number(r.v) > 0).sort((a, b) => b.v - a.v);
  if (s.length <= n) return s;
  return [...s.slice(0, n - 1), {name: "Other", v: s.slice(n - 1).reduce((a, r) => a + Number(r.v), 0), other: true}];
}
/* GB as "12.3 GB" / "1.40 TB" in two pieces, for the middle of a ring: [number, unit] */
function pgUsageGbParts(v) {
  const n = Number.isFinite(+v) ? +v : 0;
  return n >= 1024 ? [(n / 1024).toFixed(2), "TB"] : [n >= 100 ? n.toFixed(0) : n.toFixed(1), "GB"];
}

/* ---- state: what the tab knows. The Panels page draws itself again every 15 s and calls render() each time: render paints from this state AT ONCE (no "loading" flash, the
   scroll position stays) and asks the server only for what is older than the server's own cache (counters 15 s, ranges and top users 30 s). */
const pgUsageState = {sec: (() => { try { return localStorage.getItem("nk.pgu.sec") || "ov"; } catch (e) { return "ov"; } })(), id: 0, type: "pasarguard", ranges: null, rangesAt: 0, preset: "last7", from: "", to: "", ov: null, ovAt: 0, range: null, rangeKey: "", rangeAt: 0, top: null, topKey: "", topAt: 0, admins: null,
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
const pgUsageCached = d => d && d.cached && d.age_s >= 1 ? ` · cached ${Math.round(d.age_s)} s` : "";
const pgUsageSpan = r => `${esc(r.from)} <small>${esc(r.from_j)}</small> → ${esc(r.to)} <small>${esc(r.to_j)}</small>`;
const pgUsageCard = (title, sub, body, busy, extra = "") => `<div class="card pg-usage-card ${busy ? "pg-usage-busy" : ""} ${extra}"><div class="pg-usage-ch"><h3>${esc(title)}</h3><small class="mut">${sub}</small></div>${body}</div>`;
const pgUsageStat = (label, value, note = "", cls = "") => `<div class="pg-ust ${cls}"><small>${esc(label)}</small><b>${value}</b>${note ? `<span class="mut">${note}</span>` : ""}</div>`;

/* ---- the range bar: one row - the tabs, the range buttons (with their dates in both calendars), the age of the counters and Refresh */
function pgUsagePresetInfo(key) { const r = pgUsageState.ranges; return r && r.presets.find(p => p.preset === key); }
function pgUsageDrawAge() {
  const a = $("#pg-usage-age"), s = pgUsageState; if (!a) return;
  const age = s.ov ? s.ov.age_s + (Date.now() - s.ovAt) / 1000 : 0;
  a.textContent = !s.ov || age < 25 ? "" : `counters ${Math.round(age)} s old`;      // (said only when they are old: the bar stays in one row)
}
/* ---- the range: ONE button with a small panel that opens softly under it (the owner, 2026-10-15) - tab Relative (Days / Weeks / Months and the two Jalali months), tab Absolute (two dates). The dates are whole Tehran days and one question covers at most 93 of them. */
/* pure: a date 'YYYY-MM-DD' moved by n days / the number of days from a to b (both counted) */
function pgUsageAddDays(iso, n) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || "")); if (!m) return "";
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3] + (+n || 0)));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
}
function pgUsageDaysBetween(a, b) {
  const x = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(a || "")), y = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(b || "")); if (!x || !y) return 0;
  return Math.round((Date.UTC(+y[1], +y[2] - 1, +y[3]) - Date.UTC(+x[1], +x[2] - 1, +x[3])) / 86400000) + 1;
}
const PGU_REL = [["Days", [[1, "Today"], [2, "2d"], [3, "3d"], [5, "5d"], [7, "7d"], [10, "10d"]]], ["Weeks", [[14, "2w"], [21, "3w"], [28, "4w"]]], ["Months", [[30, "1m"], [60, "2m"], [90, "3m"]]]];      // [days, what the chip says]: 93 days is all one question covers
function pgUsageCurDays() {          // how many days back from today the range is (0 = it is not "the last n days")
  const s = pgUsageState;
  if (s.preset === "custom") return s.from && s.to && s.to === (s.ranges || {}).tehran_today ? pgUsageDaysBetween(s.from, s.to) : 0;
  return s.preset === "today" ? 1 : s.preset === "last7" ? 7 : s.preset === "last30" ? 30 : 0;
}
function pgUsageRangeLabel() {
  const s = pgUsageState, n = pgUsageCurDays(), j = {jmonth: "This month", last_jmonth: "Last month"}[s.preset];
  return j || (n === 1 ? "Today" : n ? `Last ${n} days` : "Custom range");
}
const pgUsageIcon = {cal: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="5" width="17" height="15" rx="3"/><path d="M3.5 10h17M8 3v4M16 3v4"/></svg>', chev: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>'};
function pgUsageBarHtml() {
  const s = pgUsageState, cur = pgUsagePresetInfo(s.preset), span = s.preset === "custom" ? {from: s.from, to: s.to} : cur, when = span && span.from ? `${span.from} → ${span.to} · ` : "";
  const trigger = `<button type="button" class="pg-rng" id="pg-rng" aria-haspopup="dialog" aria-expanded="false" onclick="pgUsagePop(this)" title="${esc(when)}Whole Tehran days: a day starts at 00:00 Tehran = 20:30 UTC of the day before"><i class="pg-rng-i">${pgUsageIcon.cal}</i><span>${esc(pgUsageRangeLabel())}</span><i class="pg-rng-c">${pgUsageIcon.chev}</i></button>`;
  return pgBar(`${pnTabBtns()}<i class="pg-usage-sep"></i>${trigger}`, `<span id="pg-usage-age" class="mut"></span><button class="sm" onclick="pgUsageRefresh()" title="Read everything again (the panel itself is asked at most every 15 s)">↻ Refresh</button>`);
}
/* the panel itself */
let pgUsagePopEl = null, pgUsagePopBtn = null;
function pgUsagePopClose() {
  const el = pgUsagePopEl, btn = pgUsagePopBtn; pgUsagePopEl = pgUsagePopBtn = null;
  document.removeEventListener("mousedown", pgUsagePopOut, true); document.removeEventListener("keydown", pgUsagePopKey, true); window.removeEventListener("resize", pgUsagePopClose);
  if (btn) btn.setAttribute("aria-expanded", "false");
  if (el) { el.classList.remove("open"); setTimeout(() => el.remove(), 220); }
}
const pgUsagePopOut = e => { if (pgUsagePopEl && !pgUsagePopEl.contains(e.target) && !(pgUsagePopBtn && pgUsagePopBtn.contains(e.target))) pgUsagePopClose(); };
const pgUsagePopKey = e => { if (e.key === "Escape") { e.stopPropagation(); pgUsagePopClose(); } };
function pgUsagePopHtml() {
  const s = pgUsageState, n = pgUsageCurDays(), today = (s.ranges || {}).tehran_today || "", span = s.preset === "custom" ? {from: s.from, to: s.to} : (pgUsagePresetInfo(s.preset) || {from: s.from, to: s.to}), ml = k => { const p = pgUsagePresetInfo(k); return p ? p.label : ""; };
  const chip = (label, on, fn, tip = "") => `<button type="button" class="pg-pc${on ? " on" : ""}" onclick="${fn}" ${tip ? `title="${esc(tip)}"` : ""}>${esc(label)}</button>`;
  const rel = PGU_REL.map(([name, items]) => `<div class="pg-pr"><span>${name}</span><div>${items.map(([d, l]) => chip(l, n === d && !["jmonth", "last_jmonth"].includes(s.preset), `pgUsageRel(${d})`, d === 1 ? "Today so far" : `The last ${d} days, up to today`)).join("")}</div></div>`).join("")
    + `<div class="pg-pr"><span>Months<small>Jalali</small></span><div>${chip("This month", s.preset === "jmonth", "pgUsagePreset('jmonth')", ml("jmonth"))}${chip("Last month", s.preset === "last_jmonth", "pgUsagePreset('last_jmonth')", ml("last_jmonth"))}</div></div>`;
  const day = (id, iso) => `<input type="date" id="${id}" value="${esc(iso)}" max="${esc(today)}" oninput="document.getElementById('${id}-j').textContent = pgUsageJalaliOf(this.value)"><span class="pg-usage-j mono" id="${id}-j">${esc(pgUsageJalaliOf(iso))}</span>`;
  const abs = `<div class="pg-pa"><label><span>From</span>${day("pg-pop-from", span.from)}</label><label><span>To</span>${day("pg-pop-to", span.to)}</label><small class="mut">Whole Tehran days, at most 93 of them.</small><button type="button" class="pri sm" onclick="pgUsageAbs()">Show this range</button></div>`;
  return `<div class="pg-pop-tabs" role="tablist"><button type="button" role="tab" class="on" data-p="rel" onclick="pgUsagePopTab('rel')">Relative</button><button type="button" role="tab" data-p="abs" onclick="pgUsagePopTab('abs')">Absolute</button><i class="pg-pop-ul"></i></div>
    <div class="pg-pop-body"><div class="pg-pp on" data-p="rel">${rel}</div><div class="pg-pp" data-p="abs">${abs}</div></div>`;
}
window.pgUsagePop = btn => {
  if (pgUsagePopEl) { pgUsagePopClose(); return; }
  const m = document.createElement("div"); m.className = "pg-pop"; m.setAttribute("role", "dialog"); m.setAttribute("aria-label", "Range"); m.innerHTML = pgUsagePopHtml();
  document.body.appendChild(m); pgUsagePopEl = m; pgUsagePopBtn = btn; btn.setAttribute("aria-expanded", "true");
  const r = btn.getBoundingClientRect(), w = m.offsetWidth;
  m.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + "px"; m.style.top = (r.bottom + 8) + "px";
  pgUsagePopTab("rel", true);
  requestAnimationFrame(() => requestAnimationFrame(() => m.classList.add("open")));
  document.addEventListener("mousedown", pgUsagePopOut, true); document.addEventListener("keydown", pgUsagePopKey, true); window.addEventListener("resize", pgUsagePopClose);
};
window.pgUsagePopTab = (k, quiet) => {
  const m = pgUsagePopEl; if (!m) return;
  m.querySelectorAll(".pg-pop-tabs button").forEach(b => b.classList.toggle("on", b.dataset.p === k));
  m.querySelectorAll(".pg-pp").forEach(p => p.classList.toggle("on", p.dataset.p === k));
  const on = m.querySelector(".pg-pop-tabs button.on"), ul = m.querySelector(".pg-pop-ul");
  if (on && ul) { ul.style.width = on.offsetWidth + "px"; ul.style.transform = `translateX(${on.offsetLeft}px)`; }
};
function pgUsageRangeChanged() {          // the same steps for every way of choosing a range
  pgUsagePopClose();
  pgUsagePut("#pg-usage-bar", pgUsageBarHtml()); pgUsageDrawAge(); pgUsageDrawRange(); pgUsageDrawPair(); pgUsageKick(false); pgUsageRelook();
}
window.pgUsageRel = d => {          // "the last d days": today and the days before it
  const s = pgUsageState, today = (s.ranges || {}).tehran_today; if (!today) return;
  if (d === 1) { s.preset = "today"; } else if (d === 7) { s.preset = "last7"; } else if (d === 30) { s.preset = "last30"; } else { s.preset = "custom"; s.to = today; s.from = pgUsageAddDays(today, -(d - 1)); }
  pgUsageRangeChanged();
};
window.pgUsageAbs = () => {
  const f = document.getElementById("pg-pop-from"), t = document.getElementById("pg-pop-to"), s = pgUsageState; if (!f || !t) return;
  if (!f.value || !t.value) { toast("✕ Choose both days", "bad"); return; }
  if (f.value > t.value) { toast("✕ The first day is after the last day", "bad"); return; }
  if (pgUsageDaysBetween(f.value, t.value) > 93) { toast("✕ One question covers at most 93 days", "bad"); return; }
  s.preset = "custom"; s.from = f.value; s.to = t.value; pgUsageRangeChanged();
};
window.pgUsagePreset = v => {
  const s = pgUsageState;
  if (v === "custom" && (!s.from || !s.to)) { const p = pgUsagePresetInfo("last7") || {}; s.from = p.from || ""; s.to = p.to || ""; }
  s.preset = v; pgUsageRangeChanged();
};

/* ---- the overview (1.57.0, the owner: "short, useful, packed - the numbers easy on the eye, like a round stats menu; e.g. a section of the nodes' usage only"): rings and gauges on ONE screen,
   every card has a › that opens its own section; the sections are chips under the range bar (Overview, Traffic, Nodes, Users, Apps, Look up) and only the chosen one is on the page */
const pgUsageN = v => v == null ? null : Number(v).toLocaleString("en-US");
const PGU_PAL = ["p0", "p1", "p2", "p3", "p4", "p5"];
const pgUsageLeg = items => `<ul class="pg-leg">${items.map(i => `<li title="${esc(i.name)}: ${esc(i.val)}"><i class="${esc(i.cls)}"></i><span class="ell">${esc(i.name)}</span><b>${esc(i.val)}</b>${i.pct != null ? `<em>${esc(i.pct)}</em>` : ""}</li>`).join("")}</ul>`;
const pgUsageRc = (title, sub, body, go) => `<div class="card pg-rc"><div class="pg-rc-h"><h3>${esc(title)}</h3>${sub ? `<small class="mut">${sub}</small>` : ""}${go ? `<button type="button" class="pg-rc-go" onclick="pgUsageSec('${go}')" title="Open ${esc(title)}" aria-label="Open ${esc(title)}">›</button>` : ""}</div>${body}</div>`;
function pgUsageUsersRc(ov) {
  const n = pgUsageN, parts = [["Active", ov.active, "ok"], ["On hold", ov.on_hold, "hold"], ["Limited", ov.limited, "warn"], ["Expired", ov.expired, "bad"], ["Disabled", ov.disabled, "off"]].filter(x => x[1] != null);
  const ring = pgUsageRingSvg(parts.map(([name, v, cls]) => ({v, cls, label: `${name}: ${n(v)}`})), {size: 118, stroke: 13, big: n(ov.total) || "0", small: "users", label: "Users by status"});
  const live = ov.online > 0 ? `<span class="pg-rc-pill ok">● ${n(ov.online)} online now</span>` : '<span class="pg-rc-pill">○ nobody online</span>';
  return pgUsageRc("Users", live, `<div class="pg-rc-b">${ring}${pgUsageLeg(parts.map(([name, v, cls]) => ({name, cls, val: n(v)})))}</div>`, "users");
}
function pgUsageServerRc(ov) {
  const g = (name, v, bad) => v == null ? "" : `<div class="pg-gau">${pgUsageGaugeSvg(v, {name, warn: bad - 15, bad})}<small>${esc(name)}</small></div>`;
  const ver = ov.version ? `${ov.panel_type === "marzban" ? "Marzban" : "PasarGuard"} ${ov.version}${ov.uptime_s ? " · up " + Math.floor(ov.uptime_s / 86400) + " d" : ""}` : "";
  const tr = ov.traffic_in != null || ov.traffic_out != null ? `<span title="All the traffic of the server since it started">↓ ${esc(fmtB(ov.traffic_in))} · ↑ ${esc(fmtB(ov.traffic_out))}</span>` : "";
  const sp = ov.speed_in != null || ov.speed_out != null ? `<span title="The speed now">⚡ ↓ ${esc(fmtB(ov.speed_in))}/s · ↑ ${esc(fmtB(ov.speed_out))}/s</span>` : "";
  return pgUsageRc("Server", esc(ver), `<div class="pg-rc-gs">${g("CPU", ov.cpu_pct, 85)}${g("Memory", ov.mem_pct, 90)}${g("Disk", ov.disk_pct, 90)}</div><div class="pg-rc-foot">${tr}${sp}</div>`, null);
}
function pgUsageTrafficRc(d) {
  const unit = d.granularity === "hour" ? "hour" : "day", series = d.series || [], peak = series.reduce((a, p) => (+p.gb > (a ? +a.gb : 0) ? p : a), null), [num, u] = pgUsageGbParts(d.total.gb);
  const body = `<div class="pg-rc-tr"><span class="pg-rc-big">${esc(num)}<small> ${u}</small></span><span class="mut">avg ${esc(pgUsageGbText(d.avg_per_day_gb))} / day${peak && +peak.gb > 0 ? ` · busiest ${esc(peak.label)}: ${esc(pgUsageGbText(peak.gb))}` : ""}</span></div>${pgUsageSparkSvg(series, {label: "traffic per " + unit})}`;
  return pgUsageRc("Traffic", `${d.range.days} day${d.range.days === 1 ? "" : "s"}`, body, "traffic");
}
/* the nodes as ring parts: the biggest n-1 and "Other"; every row has its colour class and its percent of the whole */
function pgUsageNodeParts(nodes, n) {
  const tot = nodes.reduce((a, r) => a + (+r.gb || 0), 0), top = pgUsageTopN(nodes.map(r => ({name: r.name, v: +r.gb || 0})), n);
  return {tot, rows: top.map((r, i) => ({name: r.name, v: r.v, cls: r.other ? "po" : PGU_PAL[i % PGU_PAL.length], pct: tot > 0 ? Math.round(r.v / tot * 100) : 0}))};
}
function pgUsageNodesRc(d) {
  const nodes = d.by_node || [], {tot, rows} = pgUsageNodeParts(nodes, 5), [num, u] = pgUsageGbParts(tot);
  const ring = pgUsageRingSvg(rows.map(r => ({v: r.v, cls: r.cls, label: `${r.name}: ${pgUsageGbText(r.v)} · ${r.pct}%`})), {size: 118, stroke: 13, big: num, small: u, label: "Usage by node"});
  const body = rows.length ? `<div class="pg-rc-b">${ring}${pgUsageLeg(rows.map(r => ({name: r.name, cls: r.cls, val: pgUsageGbText(r.v), pct: r.pct + "%"})))}</div>` : '<div class="pg-rc-none mut">No node has traffic in this range</div>';
  return pgUsageRc("Nodes", `${nodes.length} node${nodes.length === 1 ? "" : "s"}`, body, "nodes");
}
function pgUsageAppsRc(d) {
  const rows = pgUsageTopN((d.apps || []).map(a => ({name: a.name || "unknown app", v: +a.count || 0})), 5), tot = +d.total || rows.reduce((a, r) => a + r.v, 0);
  const col = rows.map((r, i) => ({...r, cls: r.other ? "po" : PGU_PAL[i % PGU_PAL.length], pct: tot > 0 ? Math.round(r.v / tot * 100) : 0}));
  const ring = pgUsageRingSvg(col.map(r => ({v: r.v, cls: r.cls, label: `${r.name}: ${pgUsageN(r.v)} · ${r.pct}%`})), {size: 118, stroke: 13, big: pgUsageN(tot) || "0", small: "fetches", label: "Subscription apps"});
  const body = col.length ? `<div class="pg-rc-b">${ring}${pgUsageLeg(col.map(r => ({name: r.name, cls: r.cls, val: pgUsageN(r.v), pct: r.pct + "%"})))}</div>` : '<div class="pg-rc-none mut">No fetch in this range</div>';
  return pgUsageRc("Apps", "subscription fetches", body, "apps");
}
function pgUsageTopRc(d) {
  const us = (d.users || []).slice(0, 5), max = Math.max(0, ...us.map(u => +u.used_gb || 0));
  const rows = us.map(u => `<li data-u="${esc(u.username)}" onclick="pgUsageLookupUser(this.dataset.u); pgUsageSec('look')" title="Show the usage of this user in the range"><span class="ell">${esc(u.username)}</span><i class="pg-mini"><u style="width:${max > 0 ? Math.max(3, (+u.used_gb || 0) / max * 100).toFixed(0) : 0}%"></u></i><b>${esc(pgUsageGbText(u.used_gb))}</b></li>`).join("");
  return pgUsageRc("Top users", "most used, all time", rows ? `<ul class="pg-mini-l">${rows}</ul>` : '<div class="pg-rc-none mut">No user yet</div>', "users");
}
function pgUsageDrawCards() {          // the overview: what is known is drawn at once, the rest comes in as it arrives
  const s = pgUsageState, pg = pgUsageIsPg(), cards = [];
  if (s.ov) cards.push(pgUsageUsersRc(s.ov), pgUsageServerRc(s.ov));
  if (pg && s.range) cards.push(pgUsageTrafficRc(s.range), pgUsageNodesRc(s.range));
  if (pg && s.apps) cards.push(pgUsageAppsRc(s.apps));
  if (s.top) cards.push(pgUsageTopRc(s.top));
  pgUsagePut("#pg-usage-ov", pgUsageErr(s.err.cards) + (cards.length ? `<div class="pg-rcs">${cards.join("")}</div>` : s.err.cards ? "" : pgUsageWait("Reading the counters…")));
  pgUsageDrawAge();
}
/* ---- the sections: chips under the range bar; Marzban has Overview, Users and Look up only (it does not answer the usage of all users) */
const PGU_SECS = [["ov", "Overview", true], ["traffic", "Traffic", false], ["nodes", "Nodes", false], ["users", "Users", true], ["apps", "Apps", false], ["look", "Look up", true]];      // [key, name, also on Marzban]
const pgUsageSecCur = () => { const s = pgUsageState; return PGU_SECS.some(x => x[0] === s.sec && (pgUsageIsPg() || x[2])) ? s.sec : "ov"; };
function pgUsageSecBarHtml() {
  const cur = pgUsageSecCur();
  return `<div class="pg-useg pg-usec" role="tablist" aria-label="Section">${PGU_SECS.filter(x => pgUsageIsPg() || x[2]).map(([k, l]) => `<button type="button" role="tab" aria-selected="${cur === k}" class="${cur === k ? "on" : ""}" onclick="pgUsageSec('${k}')">${l}</button>`).join("")}</div>`;
}
function pgUsageApplySec() {
  const root = $("#pg-usage"); if (!root) return;
  const cur = pgUsageSecCur(); root.dataset.sec = cur;
  [...root.children].forEach(e => { if (e.dataset.for) e.hidden = e.dataset.for !== cur; });
  pgUsagePut("#pg-usage-secbar", pgUsageSecBarHtml());
}
window.pgUsageSec = k => { pgUsageState.sec = k; try { localStorage.setItem("nk.pgu.sec", k); } catch (e) { /* private window */ } pgUsageApplySec(); };
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

/* ---- usage of the range (PasarGuard): the four numbers that matter, the chart, then the nodes */
function pgUsageTotalHtml(d, what) {
  const tb = d.total.gb >= 1024 ? ` <small class="mut">${(d.total.gb / 1024).toFixed(2)} TB</small>` : "";
  return `<div class="pg-usage-head"><span class="pg-usage-big">${esc(pgUsageGbText(d.total.gb))}</span>${tb}<span class="mut">${esc(what)} · ${pgUsageSpan(d.range)} · ${d.range.days} day${d.range.days === 1 ? "" : "s"} · avg ${esc(pgUsageGbText(d.avg_per_day_gb))} per day</span></div>`;
}
function pgUsageRangeHtml(d, busy) {
  const unit = d.granularity === "hour" ? "hour" : "day", series = d.series || [], nodes = d.by_node || [];
  const peak = series.reduce((a, p) => (+p.gb > (a ? +a.gb : 0) ? p : a), null), best = nodes.reduce((a, r) => (r.gb > (a ? a.gb : 0) ? r : a), null);
  const tb = d.total.gb >= 1024 ? `${(d.total.gb / 1024).toFixed(2)} TB` : "";
  const stats = `<div class="pg-ustats">${pgUsageStat("Total", esc(pgUsageGbText(d.total.gb)), tb, "big")}${pgUsageStat("Average per day", esc(pgUsageGbText(d.avg_per_day_gb)), `${d.range.days} day${d.range.days === 1 ? "" : "s"}`)}${pgUsageStat(`Busiest ${unit}`, peak ? esc(pgUsageGbText(peak.gb)) : "—", peak ? esc(peak.label) : "")}${pgUsageStat("Busiest node", best ? esc(best.name) : "—", best ? `${esc(pgUsageGbText(best.gb))} · ${(best.share * 100).toFixed(0)}%` : "")}</div>`;
  return pgUsageCard("Traffic", `users' usage per ${unit}, Tehran time${pgUsageCached(d)}`, `${stats}<div class="pg-usage-chart">${pgUsageBarsSvg(series, {unit})}</div>${(d.notes || []).map(x => `<small class="mut">${esc(x)}</small>`).join("")}`, busy);
}
function pgUsageNodesSecHtml(d, busy) {          // the section "Nodes": only the consumption of the nodes - a big ring with every node, and the sortable table
  const nodes = d.by_node || [], {tot, rows} = pgUsageNodeParts(nodes, 8), [num, u] = pgUsageGbParts(tot);
  const ring = pgUsageRingSvg(rows.map(r => ({v: r.v, cls: r.cls, label: `${r.name}: ${pgUsageGbText(r.v)} · ${r.pct}%`})), {size: 160, stroke: 17, big: num, small: u, label: "Usage by node"});
  const leg = rows.length ? pgUsageLeg(rows.map(r => ({name: r.name, cls: r.cls, val: pgUsageGbText(r.v), pct: r.pct + "%"}))) : "";
  return pgUsageCard("Node usage", `users' usage per node · ${pgUsageSpan(d.range)}${pgUsageCached(d)}`, `<div class="pg-ns"><div class="pg-ns-ring">${ring}${leg}</div><div id="pg-usage-range-nodes">${pgUsageNodeTable("range", nodes)}</div></div>`, busy);
}
function pgUsageDrawNodes() {
  const s = pgUsageState; if (!pgUsageIsPg()) return;
  pgUsagePut("#pg-usage-nodes", pgUsageErr(s.err.range) + (s.range ? pgUsageNodesSecHtml(s.range, s.rangeKey !== pgUsageQuery()) : s.err.range ? "" : pgUsageWait("Reading the usage…")));
}
function pgUsageDrawRange() {
  const s = pgUsageState;
  if (!pgUsageIsPg()) { pgUsagePut("#pg-usage-range", pgUsageCard("Traffic", "", '<div class="mut pg-usage-hint">Marzban answers the usage of all users only by timing out on a big panel, so it is not asked. Look up an admin (or one user) on the right: that works for the range chosen above.</div>', false)); return; }
  pgUsagePut("#pg-usage-range", pgUsageErr(s.err.range) + (s.range ? pgUsageRangeHtml(s.range, s.rangeKey !== pgUsageQuery()) : s.err.range ? "" : pgUsageWait("Reading the usage…")));
  pgUsageDrawNodes(); pgUsageDrawCards();
}
const pgUsageRangeIsOld = () => !!pgUsageState.range && Date.parse(pgUsageState.range.end) < Date.now() - 7200000;       // a range that ended long ago does not change: it is not asked again
async function pgUsageLoadRange() {
  const s = pgUsageState, id = s.id, key = pgUsageQuery(), mine = pgUsageTick("range");
  try { const d = await pgApi(id, `/usage/range?${key}`); if (s.id !== id || mine !== pgUsageTok.range) return; s.range = d; s.rangeKey = key; s.rangeAt = Date.now(); s.err.range = ""; }
  catch (e) { if (s.id !== id || mine !== pgUsageTok.range) return; s.err.range = (e && e.message) || String(e); if (s.rangeKey !== pgUsageQuery()) s.range = null; }
  pgUsageDrawRange();
}

/* ---- top users (the panel sorts and limits; 20 rows at most): the usage and the limit are ONE column with a meter */
function pgUsageTopHtml(d, busy) {
  const s = pgUsageState, labels = {used_traffic: "Most used", online_at: "Last online", data_limit: "Biggest limit", created_at: "Newest"};
  const stCls = v => v === "active" ? "ok" : v === "on_hold" ? "" : "err";
  const sorts = (d.sorts || ["used_traffic"]).map(k => `<option value="${k}" ${s.topSort === k ? "selected" : ""}>${labels[k] || k}</option>`).join("");
  const adm = `<option value="">All admins</option>` + (s.admins || []).map(a => `<option ${s.topAdmin === a ? "selected" : ""}>${esc(a)}</option>`).join("");
  const use = u => { const pct = u.percent == null ? null : Math.min(100, u.percent); return `<div class="pg-uu"><b>${esc(pgUsageGbText(u.used_gb))}</b><small class="mut">${u.limit_gb == null ? "no limit" : "of " + +Number(u.limit_gb).toFixed(2) + " GB" + (u.percent == null ? "" : " · " + u.percent + "%")}</small></div>${pct == null ? "" : `<i class="pg-ubar ${pct >= 100 ? "bad" : pct >= 85 ? "warn" : ""}"><u style="width:${pct}%"></u></i>`}`; };
  const rows = (d.users || []).map(u => `<tr class="clk" data-u="${esc(u.username)}" onclick="pgUsageLookupUser(this.dataset.u)" title="Show this user's usage in the chosen range"><td class="name"><div class="ell">${esc(u.username)}</div>${u.admin ? `<small class="mut">${esc(u.admin)}</small>` : ""}</td><td class="pg-usage-use">${use(u)}</td><td><span class="pill ${stCls(u.status)}">${esc(u.status)}</span></td><td class="mut mono" title="${esc(pgUsageJalaliOf(u.expire))}">${esc(u.expire || "—")}</td><td class="mut mono" title="${esc(pgUsageTehran(u.online_at))} Tehran · ${esc(pgUsageJalaliOf(pgUsageTehran(u.online_at).slice(0, 10)))}">${esc(pgUsageTehran(u.online_at).slice(5) || "—")}</td></tr>`).join("");
  const ctl = `<div class="pg-usage-row"><select onchange="pgUsageTopSet('topSort', this.value)">${sorts}</select><select onchange="pgUsageTopSet('topAdmin', this.value)">${adm}</select><small class="mut">the panel's own counters, not the range</small></div>`;
  return pgUsageCard("Top users", `${d.users.length} of ${Number(d.total).toLocaleString("en-US")}${pgUsageCached(d)}`, `${ctl}<div class="lbox full pg-usage-topbox" data-sk="top" onscroll="pgUsageState.scroll[this.dataset.sk] = this.scrollTop"><table class="tbl lift"><thead><tr><th>User</th><th>Usage</th><th>Status</th><th>Expires</th><th title="Tehran time">Last online</th></tr></thead><tbody>${rows || '<tr><td colspan="5" class="mut">No users</td></tr>'}</tbody></table></div>`, busy);
}
function pgUsageDrawTop() {
  const s = pgUsageState;
  pgUsagePut("#pg-usage-top", pgUsageErr(s.err.top) + (s.top ? pgUsageTopHtml(s.top, s.topKey !== pgUsageTopKey()) : s.err.top ? "" : pgUsageWait("Reading the top users…")));
  pgUsageDrawCards();
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

/* ---- users over time (online / expired / limited) and the apps that fetch the subscription: two cards of the right column, PasarGuard only */
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
  const peak = pts.reduce((a, p) => (p.gb > a ? p.gb : a), 0);
  return pgUsageCard("Users over time", `per ${unit}, Tehran time${pgUsageCached(d)}`, `<div class="pg-usage-row"><div class="pg-useg">${seg}</div><select onchange="pgUsageCountsAdmin(this.value)" title="Count the users of one admin only">${pgUsageAdminOpts(s.countsAdmin, "All admins")}</select></div>
    <div class="pg-usage-head"><span class="pg-usage-big">${n(d.count)}</span><span class="mut">${esc(what)}${peak ? ` · peak ${n(peak)} in one ${unit}` : ""}</span></div>
    <div class="pg-usage-chart">${pgUsageBarsSvg(pts, {unit, height: 150, int: true, axisText: v => String(Math.round(v)), valueText: v => n(v) + (v === 1 ? " user" : " users"), none: "None in this range"})}</div>`, busy);
}
function pgUsageAppsHtml(d, busy) {
  const s = pgUsageState, n = v => Number(v).toLocaleString("en-US");
  const rows = d.apps.map(a => `<tr><td class="name"><div class="ell" title="${esc(a.name)}">${esc(a.name || "unknown app")}</div></td><td class="pg-usage-nw"><span class="pg-usage-share" title="${a.percent}%"><i style="width:${Math.min(100, a.percent)}%"></i></span> <small class="mut">${a.percent}%</small></td><td class="pg-usage-num">${n(a.count)}</td></tr>`).join("");
  return pgUsageCard("Subscription apps", `fetches of the subscription link${pgUsageCached(d)}`, `<div class="pg-usage-row"><select onchange="pgUsageAppsAdmin(this.value)" title="Whose fetches are counted">${pgUsageAdminOpts(s.appsAdmin, "All users")}</select><span class="pg-usage-total"><b>${n(d.total)}</b> <span class="mut">fetches</span></span></div>
    <div class="lbox full pg-usage-nodes"><table class="tbl lift"><colgroup><col><col style="width:130px"><col style="width:86px"></colgroup><thead><tr><th data-prio="0">App</th><th>Share</th><th>Fetches</th></tr></thead><tbody>${rows || '<tr><td colspan="3" class="mut">No fetch in this range</td></tr>'}</tbody></table></div>`, busy);
}
function pgUsageDrawPair() {          // the users over time (section Users) and the apps (section Apps): PasarGuard only
  const s = pgUsageState;
  if (!pgUsageIsPg()) { pgUsagePut("#pg-usage-counts", ""); pgUsagePut("#pg-usage-apps", ""); pgUsageApplySec(); return; }
  pgUsagePut("#pg-usage-counts", pgUsageErr(s.err.counts) + (s.counts ? pgUsageCountsHtml(s.counts, s.countsKey !== pgUsageCountsKey()) : s.err.counts ? "" : pgUsageWait("Reading the user counts…")));
  pgUsagePut("#pg-usage-apps", pgUsageErr(s.err.apps) + (s.apps ? pgUsageAppsHtml(s.apps, s.appsKey !== pgUsageAppsKey()) : s.err.apps ? "" : pgUsageWait("Reading the apps…")));
  pgUsageDrawCards();
}async function pgUsageLoadCounts() {
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

/* ---- the lookup card: an admin or a user, for the range chosen at the top */
function pgUsageLookFormHtml() {
  const s = pgUsageState, adm = (s.admins || []).map(a => `<option value="${esc(a)}"></option>`).join("");
  const seg = ["admin", "user"].map(k => `<button type="button" class="${s.kind === k ? "on" : ""}" onclick="pgUsageKind('${k}')">${k === "admin" ? "Admin" : "User"}</button>`).join("");
  return `<div class="pg-usage-row"><div class="pg-useg" id="pg-usage-kind">${seg}</div>
    <input id="pg-usage-name" list="pg-usage-admins" placeholder="${s.kind === "user" ? "user name" : "admin name"}" value="${esc(s.name)}" autocomplete="off" spellcheck="false" oninput="pgUsageState.name = this.value" onkeydown="if (event.key === 'Enter') pgUsageLookup()"><datalist id="pg-usage-admins">${adm}</datalist>
    <button class="sm pri" onclick="pgUsageLookup()">Show usage</button></div>`;
}
window.pgUsageKind = k => {
  pgUsageState.kind = k;
  document.querySelectorAll("#pg-usage-kind button").forEach(b => b.classList.toggle("on", b.textContent.trim().toLowerCase() === k));
  const n = $("#pg-usage-name"); if (n) n.placeholder = k === "user" ? "user name" : "admin name";
};
function pgUsageLookResultHtml(d) {
  if (!d) return '<div class="mut pg-usage-hint">Pick an admin, or click a user in Top users, to see the GB used in the range above, node by node.</div>';
  const life = d.lifetime_gb != null ? `<span class="pill" title="Everything the users of this admin ever used">lifetime ${esc(pgUsageGbText(d.lifetime_gb))}</span>` : "";
  const stale = pgUsageState.lookStale ? '<div class="pg-usage-stale">The range changed: press <b>Show usage</b> to ask again. A big admin can take up to a minute on Marzban.</div>' : "";
  return `${stale}${pgUsageTotalHtml(d, `${d.scope} ${d.name}`)}${life}${(d.notes || []).map(x => `<small class="mut">${esc(x)}</small>`).join("")}<div id="pg-usage-look-nodes">${pgUsageNodeTable("look", d.by_node || [])}</div>`;
}
function pgUsageDrawLook() {
  const s = pgUsageState;
  pgUsagePut("#pg-usage-look-res", s.lookBusy ? pgUsageWait("Asking the panel…" + (pgUsageIsPg() ? "" : " (a big admin can take up to a minute)")) : s.lookErr ? pgUsageErr(s.lookErr) : pgUsageLookResultHtml(s.look));
}
window.pgUsageLookup = async () => {
  const s = pgUsageState, id = s.id, n = $("#pg-usage-name");
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
  const s = pgUsageState; s.name = name; pgUsageKind("user"); pgUsageSec("look");
  const n = $("#pg-usage-name"); if (n) n.value = name;
  pgUsageLookup();
};
/* the range changed: PasarGuard answers a lookup in a blink, so it is asked again; a big Marzban admin takes up to a minute, so it waits for the button */
function pgUsageRelook() {
  const s = pgUsageState; if (!s.look) return;
  if (pgUsageIsPg()) { pgUsageLookup(); return; }
  s.lookStale = true; pgUsageDrawLook();
}

/* ---- the tab: the page scrolls (it is a dashboard, not a list); on a wide window the cards stand in two columns, on a narrow one in one */
const pgUsageSkeleton = () => `<div class="pg-usage" id="pg-usage"><div id="pg-usage-bar"></div><div id="pg-usage-secbar"></div>
  <div data-for="ov" id="pg-usage-ov"></div>
  <div data-for="traffic" id="pg-usage-range"></div>
  <div data-for="nodes" id="pg-usage-nodes"></div>
  <div data-for="users" class="pg-usage-two"><div id="pg-usage-counts"></div><div id="pg-usage-top"></div></div>
  <div data-for="apps" id="pg-usage-apps"></div>
  <div data-for="look">${pgUsageCard("Usage of an admin or a user", "", '<div id="pg-usage-look-form"></div><div id="pg-usage-look-res"></div>', false, "pg-usage-look")}</div></div>`;
async function pgUsageLoadRanges() {
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
  pgUsagePut("#pg-usage-bar", pgUsageBarHtml()); pgUsageApplySec(); pgUsageDrawCards(); pgUsageDrawRange(); pgUsageDrawPair(); pgUsageDrawTop();
  pgUsagePut("#pg-usage-look-form", pgUsageLookFormHtml()); pgUsageDrawLook();
  pgUsageKick(false);
}
window.pgUsageRefresh = () => { pgUsageKick(true); };

window.pgTabs.usage = {label: "Usage", order: 80, types: ["pasarguard", "marzban"], render: pgUsageRender};
