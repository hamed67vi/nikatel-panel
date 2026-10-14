/* PasarGuard tab: the cores (Xray configs) of a PasarGuard panel with backup and rollback. Registers itself in pgTabs (see pg_common.js).
   The config is edited as text. Its secrets arrive as <secret:…> placeholders and the server puts the real ones back, so a key never reaches this page.
   The pure functions at the top (the line diff and the structure check) have twins in services/pg_cores.py: tests/test_pg_cores.py runs both on the same inputs. */

/* ---- pure: the line diff (Myers, shortest edit script); the Python twin is pg_cores.line_diff */
const pgCoresMaxD = 1500, pgCoresMaxRows = 3000;

function pgCoresMyers(a, b, maxD) {
  const n = a.length, m = b.length;
  if (n === 0) return b.map(x => ["+", x]);
  if (m === 0) return a.map(x => ["-", x]);
  const top = Math.min(n + m, maxD), off = top + 1, v = new Int32Array(2 * top + 3), trace = [];
  for (let d = 0; d <= top; d++) {
    trace.push(v.slice(off - d, off + d + 1));                                          // the state before round d, for k = -d .. d
    for (let k = -d; k <= d; k += 2) {
      let x = (k === -d || (k !== d && v[off + k - 1] < v[off + k + 1])) ? v[off + k + 1] : v[off + k - 1] + 1, y = x - k;
      while (x < n && y < m && a[x] === b[y]) { x++; y++; }
      v[off + k] = x;
      if (x >= n && y >= m) return pgCoresWalkBack(a, b, trace, d);
    }
  }
  return null;                                                                          // more than maxD lines differ
}

function pgCoresWalkBack(a, b, trace, last) {
  let x = a.length, y = b.length;
  const out = [];
  for (let d = last; d > 0; d--) {
    const s = trace[d], k = x - y, pk = (k === -d || (k !== d && s[k - 1 + d] < s[k + 1 + d])) ? k + 1 : k - 1, px = s[pk + d], py = px - pk;
    while (x > px && y > py) { out.push(["=", a[x - 1]]); x--; y--; }
    out.push(x === px ? ["+", b[y - 1]] : ["-", a[x - 1]]);
    x = px; y = py;
  }
  while (x > 0) { out.push(["=", a[x - 1]]); x--; }                                     // round 0 is a plain run of equal lines
  return out.reverse();
}

/* the edit script from a to b (arrays of lines): [["=" | "-" | "+", line], …] and whether the diff gave up (then the middle part is shown as removed and added as a whole) */
function pgCoresDiffOps(a, b, maxD = pgCoresMaxD) {
  const n = a.length, m = b.length;
  let s = 0, e = 0;
  while (s < n && s < m && a[s] === b[s]) s++;
  while (e < n - s && e < m - s && a[n - 1 - e] === b[m - 1 - e]) e++;
  const ma = a.slice(s, n - e), mb = b.slice(s, m - e);
  let mid = pgCoresMyers(ma, mb, maxD);
  const coarse = mid === null;
  if (coarse) mid = ma.map(x => ["-", x]).concat(mb.map(x => ["+", x]));
  return {ops: a.slice(0, s).map(x => ["=", x]).concat(mid, a.slice(n - e).map(x => ["=", x])), coarse};
}

/* the compact form: changed lines with `ctx` unchanged lines around them; a longer unchanged run becomes ["~", how many lines] */
function pgCoresDiffRows(ops, ctx = 2) {
  const n = ops.length, keep = new Array(n).fill(false), rows = [];
  let hidden = 0;
  ops.forEach((o, i) => { if (o[0] !== "=") for (let j = Math.max(0, i - ctx); j < Math.min(n, i + ctx + 1); j++) keep[j] = true; });
  ops.forEach((o, i) => {
    if (keep[i]) { if (hidden) { rows.push(["~", hidden]); hidden = 0; } rows.push([o[0], o[1]]); } else hidden++;
  });
  if (hidden) rows.push(["~", hidden]);
  return rows;
}

/* {same, added, removed, coarse, truncated, rows} for two texts, exactly what the server's line_diff answers */
function pgCoresDiffText(oldText, newText, ctx = 2) {
  const {ops, coarse} = pgCoresDiffOps(String(oldText).split("\n"), String(newText).split("\n"));
  const added = ops.filter(o => o[0] === "+").length, removed = ops.filter(o => o[0] === "-").length, rows = pgCoresDiffRows(ops, ctx);
  return {same: added === 0 && removed === 0, added, removed, coarse, truncated: rows.length > pgCoresMaxRows, rows: rows.slice(0, pgCoresMaxRows)};
}

/* ---- pure: the structure of a config; the Python twin is pg_cores.check_config (the messages are the same) */
const pgCoresIsObj = v => v !== null && typeof v === "object" && !Array.isArray(v);

function pgCoresCheck(cfg, type = "xray") {
  if (!pgCoresIsObj(cfg)) return ["the config must be a JSON object"];
  if (type !== "xray") return Object.keys(cfg).length ? [] : ["the config must not be empty"];
  const errors = [], ib = cfg.inbounds, ob = cfg.outbounds;
  if (!Array.isArray(ib)) errors.push("“inbounds” must be an array");
  else if (!ib.length) errors.push("the config has no inbounds: the panel refuses it");
  else {
    const seen = new Map();
    ib.forEach((x, i) => {
      const n = i + 1;
      if (!pgCoresIsObj(x)) { errors.push(`inbound #${n} is not an object`); return; }
      const tag = x.tag;
      if (typeof tag !== "string" || !tag.trim()) { errors.push(`inbound #${n} has no tag`); return; }
      if (tag.includes(",") || tag.includes("<=>")) errors.push(`the tag “${tag}” of inbound #${n} has a character the panel does not allow (a comma or <=>)`);
      if (seen.has(tag)) errors.push(`the tag “${tag}” is used by inbound #${seen.get(tag)} and inbound #${n}: every tag must be unique`);
      else seen.set(tag, n);
    });
  }
  if (!Array.isArray(ob)) errors.push("“outbounds” must be an array");
  else if (!ob.length) errors.push("the config has no outbounds: the panel refuses it");
  else ob.forEach((o, i) => {
    if (!pgCoresIsObj(o)) errors.push(`outbound #${i + 1} is not an object`);
    else if (typeof o.tag !== "string" || !o.tag.trim()) errors.push(`outbound #${i + 1} has no tag: the panel refuses it`);
  });
  return errors;
}

/* {ok, value} or {ok: false, error, line, col}: the browsers word a JSON error differently, so the position is taken from the message when it has one */
function pgCoresParse(text) {
  try { return {ok: true, value: JSON.parse(text)}; }
  catch (e) {
    const msg = String((e && e.message) || e), lc = /line (\d+) column (\d+)/.exec(msg), pos = /position (\d+)/.exec(msg);
    let line = 0, col = 0;
    if (lc) { line = +lc[1]; col = +lc[2]; }
    else if (pos) { const before = text.slice(0, +pos[1]).split("\n"); line = before.length; col = before[before.length - 1].length + 1; }
    return {ok: false, error: msg.replace(/ in JSON at position \d+/, "").replace(/ \(line \d+ column \d+\)/, "") + (line ? ` (line ${line}, column ${col})` : ""), line, col};
  }
}

/* a text the way the diff compares it: parsed and printed again, so spaces and the order of digits do not count as changes; a text that is not JSON is compared as it is */
function pgCoresShown(text) {
  const p = pgCoresParse(text);
  return p.ok ? JSON.stringify(p.value, null, 2) : text;
}

/* ---- the tab */
const pgCoresSt = {id: 0, list: null, at: 0, coreId: 0, d: null, view: "edit", draft: null, dry: null, dryFor: "", notice: null, seq: 0, ts: 0, busy: false};
let pgCoresTimer = 0;
const pgCoresCur = () => (pgCoresSt.draft === null ? pgCoresSt.d.text : pgCoresSt.draft);
const pgCoresPl = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const pgCoresFmt = {hour12: false, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"};
const pgCoresDate = iso => { if (!iso) return "—"; const t = Date.parse(iso); return isNaN(t) ? String(iso).slice(0, 16).replace("T", " ") : new Date(t).toLocaleString(undefined, pgCoresFmt); };
const pgCoresStamp = ts => ts ? new Date(ts * 1000).toLocaleString(undefined, pgCoresFmt) : "—";

pgTabs.cores = {label: "Core", order: 50, types: ["pasarguard"], render: pgCoresRender};

async function pgCoresRender(id, box) {
  const st = pgCoresSt, my = ++st.seq;
  if (st.id !== id) Object.assign(st, {id, list: null, at: 0, coreId: 0, d: null, view: "edit", draft: null, dry: null, dryFor: "", notice: null});      // another panel: nothing of the last one stays
  if (st.list && st.draft === null && Date.now() - st.at > 300000) Object.assign(st, {list: null, d: null});      // read when the tab is opened; what is on the screen is not asked again for five minutes, and never while you edit
  try {
    if (!st.list) {
      pgLoading(box, "Reading the cores…");
      st.list = (await pgApi(id, "/cores")).cores;
      st.at = Date.now();
    }
    if (!st.coreId && st.list.length === 1) st.coreId = st.list[0].id;                   // one core: open it at once
    if (st.coreId && !st.d) {
      pgLoading(box, "Reading the core…");
      await pgCoresLoad(id, st.coreId);
    }
  } catch (e) { if (my === st.seq) pgError(box, e); return; }
  if (my === st.seq) pgCoresDraw(box);
}

async function pgCoresLoad(id, coreId) {
  const st = pgCoresSt;
  st.d = await pgApi(id, `/cores/${coreId}`);
  st.draft = null; st.dry = null; st.dryFor = ""; st.at = Date.now();
}

function pgCoresRedraw() {
  const box = $("#pn-body");
  if (box && pn.tab === "cores" && pgCoresSt.list) pgCoresDraw(box);
}

/* ---- drawing */
function pgCoresListHtml(modal) {
  const st = pgCoresSt;
  const rows = st.list.map(c => `<tr class="${c.id === st.coreId ? "pg-cores-on" : ""}"><td class="name"><b>${esc(c.name || "(no name)")}</b> <small class="mut">#${c.id}</small></td><td><span class="td-type">${esc(c.type)}</span></td>
    <td class="mut">${esc(pgCoresDate(c.created_at))}</td><td class="mut">${esc(pgCoresDate(c.updated_at))}</td><td class="num">${c.inbounds}</td><td class="num">${c.outbounds}</td><td class="num">${c.rules}</td><td class="num">${c.backups}</td>
    <td class="pg-cores-go">${c.id === st.coreId ? '<span class="pill ok">open</span>' : `<button class="sm pri" onclick="${modal ? "closeModal();" : ""}pgCoresOpen(${c.id})">Open</button>`}</td></tr>`).join("");
  return `<div class="lbox full pg-cores-list"><table class="tbl lift"><colgroup><col><col style="width:100px"><col style="width:150px"><col style="width:150px"><col style="width:78px"><col style="width:88px"><col style="width:62px"><col style="width:78px"><col style="width:84px"></colgroup>
    <thead><tr><th>Core</th><th>Type</th><th>Created</th><th>Updated</th><th>Inbounds</th><th>Outbounds</th><th>Rules</th><th>Backups</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="9" class="mut">this panel has no core</td></tr>'}</tbody></table></div>`;
}

function pgCoresHeadHtml(d) {
  const c = d.core, ex = c.exclude_inbound_tags.length, fb = c.fallbacks_inbound_tags.length;
  return `<div class="pg-cores-head"><b class="pg-cores-name">${esc(c.name || "(no name)")}</b><span class="td-type">${esc(c.type)}</span><span class="mut">created ${esc(pgCoresDate(c.created_at))}${c.updated_at ? " · updated " + esc(pgCoresDate(c.updated_at)) : ""}</span>
    <span class="mut">${pgCoresPl(d.inbounds.length, "inbound")} · ${pgCoresPl(d.outbounds.length, "outbound")} · ${pgCoresPl(d.rules, "rule")}</span>
    ${ex || fb ? `<span class="mut" title="Excluded: ${esc(c.exclude_inbound_tags.join(", "))}  Fallbacks: ${esc(c.fallbacks_inbound_tags.join(", "))}">${ex} excluded · ${fb} fallback tags</span>` : ""}
    ${d.masked ? `<span class="pill" title="Private keys and passwords stay on the server: they are shown as &lt;secret:…&gt; placeholders and put back when you save">🔒 ${d.masked} hidden</span>` : ""}
    <span class="sp"></span><button class="sm" onclick="pgCoresReloadAll()" title="Read the cores from the panel again">↻ Reload</button><button class="sm danger" onclick="pgCoresRestart()" title="Restart Xray on every node of this core">Restart nodes…</button></div>`;
}

function pgCoresNoticeHtml() {
  const n = pgCoresSt.notice;
  return n ? `<div class="gw-out ${n.cls} pg-cores-note"><div>${n.html}</div><button class="sm" onclick="pgCoresSt.notice = null; pgCoresRedraw()" title="Hide this message">✕</button></div>` : "";
}

function pgCoresSubHtml(d) {
  const tab = (k, label) => `<button class="sm ${pgCoresSt.view === k ? "pri" : ""}" onclick="pgCoresView('${k}')">${label}</button>`;
  return `<div class="pg-cores-sub">${tab("edit", "Edit config")}${tab("over", "Inbounds · outbounds")}${tab("backups", `Backups <small>${d.backups.length}</small>`)}</div>`;
}

function pgCoresWarnHtml(ws) {
  return ws.length ? `<ul class="pg-cores-ws">${ws.map(w => `<li class="${w.level === "danger" ? "danger" : "warn"}"><b>${w.level === "danger" ? "Danger" : "Warning"}</b> ${esc(w.text)}</li>`).join("")}</ul>` : "";
}

function pgCoresDiffHtml(df) {
  if (df.same) return "";
  const rows = df.rows.map(r => r[0] === "~"
    ? `<div class="pg-cores-dl gap">… ${pgCoresPl(r[1], "unchanged line")} …</div>`
    : `<div class="pg-cores-dl ${r[0] === "+" ? "add" : r[0] === "-" ? "rm" : ""}"><i>${r[0] === "+" ? "+" : r[0] === "-" ? "−" : ""}</i><code>${esc(r[1])}</code></div>`).join("");
  return `<div class="pg-cores-diff">${rows}</div>${df.coarse ? '<small class="mut">So many lines differ that the changed part is shown as removed and added as a whole.</small>' : ""}${df.truncated ? `<small class="mut">Only the first ${pgCoresMaxRows} rows are shown.</small>` : ""}`;
}

/* what the page knows by itself while you type: is it JSON, does it keep the structure, and what changed against the panel's config */
function pgCoresLiveHtml() {
  const st = pgCoresSt, cur = pgCoresCur(), p = pgCoresParse(cur), errs = p.ok ? pgCoresCheck(p.value, st.d.core.type) : [];
  const dirty = st.draft !== null, df = dirty ? pgCoresDiffText(pgCoresShown(st.d.text), pgCoresShown(cur)) : null;
  const stat = (p.ok ? '<span class="pill ok">valid JSON</span>' : '<span class="pill err">not valid JSON</span>') + (errs.length ? `<span class="pill err">${pgCoresPl(errs.length, "problem")}</span>` : "") +
    `<span class="mut">${pgCoresPl(cur.split("\n").length, "line")}</span>` + (!dirty ? '<span class="mut">no change yet</span>' : df.same ? '<span class="mut">no change against the panel</span>' : `<span class="pg-cores-add">+${df.added}</span><span class="pg-cores-rm">−${df.removed}</span><span class="mut">lines against the panel</span>`);
  const bad = !p.ok ? `<div class="err">${esc(p.error)}</div>` : errs.map(e => `<div class="err">${esc(e)}</div>`).join("");
  return `<div class="pg-cores-stat">${stat}</div>${bad}${dirty && !df.same ? pgCoresDiffHtml(df) : ""}${dirty ? "" : '<div class="mut pg-cores-hint">Edit the config. What would change against the panel shows here; Dry run asks the server to check it (structure, warnings, who uses a removed inbound). Nothing is written until you save.</div>'}`;
}

function pgCoresDryHtml() {
  const st = pgCoresSt, r = st.dry;
  if (!r) return "";
  const stale = st.dryFor !== pgCoresCur(), head = r.valid ? (r.changed === false ? '<span class="pill">the panel would keep it as it is</span>' : '<span class="pill ok">✓ the checks pass</span>') : '<span class="pill err">✕ cannot be saved</span>';
  return `<div class="pg-cores-dry ${stale ? "stale" : ""}"><div class="pg-cores-stat"><b>Dry run</b>${head}${r.warnings.length ? `<span class="pill warn">${pgCoresPl(r.warnings.length, "warning")} · saving needs your OK</span>` : ""}${stale ? '<span class="pill warn">edited since: run it again</span>' : ""}
    ${r.diff ? `<span class="mut">server diff +${r.diff.added} −${r.diff.removed}</span>` : ""}</div>${r.errors.map(e => `<div class="err">${esc(e)}</div>`).join("")}${pgCoresWarnHtml(r.warnings)}</div>`;
}

/* the editor on the left, what it would change on the right (below each other on a narrow screen) */
function pgCoresEditHtml(d) {
  const off = pgCoresSt.draft !== null ? "" : "disabled";
  return `<div class="pg-cores-split"><div class="pg-cores-left">
    <textarea id="pg-cores-ta" class="pg-cores-ta" spellcheck="false" wrap="off" autocomplete="off" oninput="pgCoresInput(this)" onscroll="pgCoresSt.ts = this.scrollTop">${esc(pgCoresCur())}</textarea>
    <div class="pg-cores-act"><button class="sm" onclick="pgCoresFormat()" title="Print the JSON with two-space indents">Format</button><button class="sm pg-cores-need" ${off} onclick="pgCoresReset()" title="Throw the edits away and show the config on the panel again">Reset</button><span class="sp"></span>
      <button class="sm pg-cores-need" ${off} onclick="pgCoresDry()" title="Check it on the server: structure, warnings, diff. Nothing is changed">Dry run</button>
      <button class="sm pri pg-cores-need" ${off} onclick="pgCoresSave(false)" title="Back the current config up, then store the new one on the panel">Save…</button>
      <button class="sm danger pg-cores-need" ${off} onclick="pgCoresSave(true)" title="Save, then restart Xray on every node of the core: customers are cut for a moment">Save and restart…</button></div></div>
    <div class="pg-cores-right"><div id="pg-cores-live">${pgCoresLiveHtml()}</div><div id="pg-cores-dry">${pgCoresDryHtml()}</div><small class="mut pg-cores-how">${esc(d.applies)}</small></div></div>`;
}

function pgCoresOverHtml(d) {
  const proxy = ["vless", "vmess", "trojan"], uk = d.usage_known;
  const ib = d.inbounds.map(r => {
    const bad = r.security === "none" && r.public && proxy.includes(r.protocol);
    return `<tr><td class="name">${esc(r.tag)}</td><td class="mono">${esc(r.port == null ? "—" : r.port)}</td><td>${esc(r.protocol)}</td><td>${esc(r.network)}</td>
      <td><span class="pill ${r.security === "none" ? (bad ? "warn" : "") : "ok"}" title="${bad ? "No TLS or REALITY and open to the outside" : ""}">${esc(r.security)}</span></td><td class="mut">${esc(r.listen || "all addresses")}</td>
      <td class="num">${r.hosts == null ? "?" : r.hosts}</td><td class="mut">${r.groups == null ? "?" : esc(r.groups.join(", ")) || "—"}</td></tr>`;
  }).join("");
  const ob = d.outbounds.map(r => `<tr><td class="name">${esc(r.tag)}</td><td>${esc(r.protocol)}</td><td>${esc(r.network)}</td><td class="mono mut">${esc(r.target || "—")}</td></tr>`).join("");
  return `<div class="lbox full pg-cores-ib"><table class="tbl lift"><colgroup><col><col style="width:70px"><col style="width:110px"><col style="width:90px"><col style="width:90px"><col style="width:120px"><col style="width:60px"><col style="width:22%"></colgroup>
    <thead><tr><th>Inbound</th><th>Port</th><th>Protocol</th><th>Network</th><th>Security</th><th>Listens on</th><th>Hosts</th><th>Groups</th></tr></thead><tbody>${ib || '<tr><td colspan="8" class="mut">no inbound</td></tr>'}</tbody></table></div>
    ${uk.hosts && uk.groups ? "" : '<small class="mut">A “?” means the panel did not answer for its hosts or groups, so who uses an inbound is not known.</small>'}
    <div class="lbox full pg-cores-ob"><table class="tbl lift"><colgroup><col><col style="width:110px"><col style="width:90px"><col style="width:30%"></colgroup>
    <thead><tr><th>Outbound</th><th>Protocol</th><th>Network</th><th>Goes to</th></tr></thead><tbody>${ob || '<tr><td colspan="4" class="mut">no outbound</td></tr>'}</tbody></table></div>`;
}

function pgCoresBackupsHtml(d) {
  if (!d.backups.length) return pgEmpty("No backup yet: one is written before every save, restore and change of this core.");
  const rows = d.backups.map(b => `<tr><td class="name">${esc(pgCoresStamp(b.at))} <small class="mut">${esc(b.at ? ago(b.at) + " ago" : "")}</small></td><td class="mut">${esc(b.note || "—")}</td><td class="num">${b.inbounds}</td><td class="num">${b.outbounds}</td>
    <td class="pg-cores-go"><button class="sm" onclick="pgCoresBackupView('${esc(b.file)}')">View</button> <button class="sm" onclick="pgCoresRestore('${esc(b.file)}')">Restore…</button></td></tr>`).join("");
  return `<div class="lbox full pg-cores-bk"><table class="tbl lift"><colgroup><col style="width:260px"><col><col style="width:84px"><col style="width:96px"><col style="width:180px"></colgroup>
    <thead><tr><th>Config as it was at</th><th>Note</th><th>Inbounds</th><th>Outbounds</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
    <small class="mut">The last 20 are kept for this core, newest first. Restoring writes a new backup of the config it replaces, so a restore can be undone too.</small>`;
}

/* the first bar: one button per core (the open one is blue); the table with dates and counts is one click away, and it is the page itself while no core is open */
function pgCoresBarHtml() {
  const st = pgCoresSt, chips = st.list.map(c => `<button class="sm ${c.id === st.coreId ? "pri" : ""}" onclick="pgCoresOpen(${c.id})" title="${esc(c.type)} core #${c.id}">${esc(c.name || "(no name)")} <small>#${c.id}</small></button>`).join("");
  return pgBar(`<b>Cores</b><span class="pill">${st.list.length}</span>${chips}`, st.d ? '<button class="sm" onclick="pgCoresAll()" title="All cores with their dates and counts">All cores…</button>' : "");
}

window.pgCoresAll = () => showModal("<b>Cores of this panel</b>", pgCoresListHtml(true), "wide");

function pgCoresDraw(box) {
  const st = pgCoresSt, d = st.d;
  let html = pgCoresBarHtml();
  if (d) html += pgCoresHeadHtml(d) + pgCoresNoticeHtml() + pgCoresSubHtml(d) + `<div class="pg-cores-body" id="pg-cores-body">${st.view === "edit" ? pgCoresEditHtml(d) : st.view === "over" ? pgCoresOverHtml(d) : pgCoresBackupsHtml(d)}</div>`;
  else html += st.list.length ? pgCoresListHtml(false) + pgEmpty("Open a core to see and edit its config.") : pgEmpty("This panel has no core.");
  pgPaint(box, html);
  pgCoresFit();
  const ta = $("#pg-cores-ta");
  if (ta) ta.scrollTop = st.ts || 0;
}

/* the page does not scroll (the lists do): the body of the open core takes exactly the room that is left below its header */
function pgCoresFit() {
  const b = $("#pg-cores-body");
  if (!b) return;
  if (innerWidth < 1100) { b.style.height = ""; return; }                                 // a narrow screen: the blocks stack and the page scrolls
  const m = document.querySelector(".main"), bottom = m ? Math.min(innerHeight, m.getBoundingClientRect().bottom) : innerHeight;
  b.style.height = Math.max(300, Math.floor(bottom - 36 - b.getBoundingClientRect().top)) + "px";
}
let pgCoresFitTimer = 0;
if (typeof addEventListener === "function") addEventListener("resize", () => { clearTimeout(pgCoresFitTimer); pgCoresFitTimer = setTimeout(pgCoresFit, 120); });

/* ---- handlers */
window.pgCoresView = v => { pgCoresSt.view = v; pgCoresRedraw(); };

window.pgCoresOpen = async coreId => {
  const st = pgCoresSt;
  if (coreId === st.coreId && st.d) return;
  if (st.draft !== null && !await gconfirm("Close this core and lose your edits of its config?", {danger: true})) return;
  Object.assign(st, {coreId, d: null, draft: null, dry: null, dryFor: "", view: "edit", notice: null, ts: 0});
  const box = $("#pn-body");
  if (box) pgLoading(box, "Reading the core…");
  try { await pgCoresLoad(st.id, coreId); } catch (e) { toast("✕ " + e.message, "bad"); st.coreId = 0; }
  pgCoresRedraw();
};

window.pgCoresReloadAll = async () => {
  const st = pgCoresSt;
  if (st.draft !== null && !await gconfirm("Reload from the panel and lose your edits of the config?", {danger: true})) return;
  Object.assign(st, {list: null, d: null, draft: null, dry: null, dryFor: ""});
  refresh();
};

window.pgCoresInput = el => {
  const st = pgCoresSt;
  st.draft = el.value === st.d.text ? null : el.value;
  clearTimeout(pgCoresTimer);
  pgCoresTimer = setTimeout(pgCoresLive, 160);
};

function pgCoresLive() {
  const live = $("#pg-cores-live");
  if (!live || !pgCoresSt.d) return;
  live.innerHTML = pgCoresLiveHtml();
  const dry = $("#pg-cores-dry"); if (dry) dry.innerHTML = pgCoresDryHtml();
  document.querySelectorAll(".pg-cores-need").forEach(b => { b.disabled = pgCoresSt.draft === null; });
}

window.pgCoresFormat = () => {
  const ta = $("#pg-cores-ta"), p = pgCoresParse(ta.value);
  if (!p.ok) { toast("✕ " + p.error, "bad"); return; }
  ta.value = JSON.stringify(p.value, null, 2);
  pgCoresInput(ta);
};

window.pgCoresReset = async () => {
  if (!await gconfirm("Reset the editor to the config on the panel and lose your edits?", {danger: true})) return;
  Object.assign(pgCoresSt, {draft: null, dry: null, dryFor: "", ts: 0});
  pgCoresRedraw();
};

/* the dry run on the server; `quiet` = the caller shows the outcome itself. Returns the answer, or null when the call failed */
window.pgCoresDry = async quiet => {
  const st = pgCoresSt, text = pgCoresCur();
  try {
    const r = await pgApi(st.id, `/cores/${st.coreId}/dry-run`, {method: "POST", body: {text}});
    st.dry = r; st.dryFor = text;
    pgCoresLive();
    if (quiet !== true) toast(r.valid ? (r.changed === false ? "The panel would keep this config as it is" : `✓ The checks pass${r.warnings.length ? ` · ${pgCoresPl(r.warnings.length, "warning")}` : ""}`) : "✕ " + r.errors[0], r.valid ? "ok" : "bad");
    return r;
  } catch (e) { toast("✕ " + e.message, "bad"); return null; }
};

const pgCoresWarnText = ws => ws.map(w => "• " + (w.level === "danger" ? "DANGER: " : "") + (w.text.length > 200 ? w.text.slice(0, 200) + "…" : w.text)).join("\n");
const pgCoresNodes = () => (((state.panelsCache || []).find(p => String(p.id) === String(pgCoresSt.id)) || {}).nodes || []).length;
const pgCoresNodeText = () => { const n = pgCoresNodes(); return n ? pgCoresPl(n, "node") : "the nodes"; };

/* the second question before anything restarts */
const pgCoresSure = what => gconfirm(`Restart Xray on ${pgCoresNodeText()} now?\nCustomers connected through them are cut for a moment and their apps reconnect.\nIf the config is wrong, a node can stay down until a good one is restored.\n(${what})`, {danger: true});

/* an action that asks questions and then writes runs once at a time: a second click on its button while it is still asking does nothing */
const pgCoresOnce = fn => async (...args) => {
  const st = pgCoresSt;
  if (st.busy) return;
  st.busy = true;
  try { await fn(...args); } finally { st.busy = false; }
};

window.pgCoresSave = pgCoresOnce(async restart => {
  const st = pgCoresSt, id = st.id, text = pgCoresCur();
  if (st.draft === null) { toast("Nothing changed yet"); return; }
  const p = pgCoresParse(text);
  if (!p.ok) { toast("✕ " + p.error, "bad"); return; }
  const local = pgCoresCheck(p.value, st.d.core.type);
  if (local.length) { toast("✕ " + local[0], "bad"); return; }
  const r = await pgCoresDry(true);                                                     // always checked on the server first, and shown on the page
  if (!r) return;
  if (!r.valid) { toast("✕ " + r.errors[0], "bad"); return; }
  if (r.changed === false) { toast("Nothing to save: the config is the same as the one on the panel"); return; }
  const ws = r.warnings, d = r.diff;
  const ask = `${restart ? "Save this config and then restart Xray on the nodes" : "Save this config on the panel"}?\n${pgCoresPl(d.added, "line")} added, ${d.removed} removed. The current config is backed up first.` +
    (restart ? "" : "\nThe nodes keep running the old config until they restart.") + (ws.length ? `\n\n${pgCoresPl(ws.length, "warning")}:\n${pgCoresWarnText(ws)}` : "");
  if (!await gconfirm(ask, {danger: ws.length > 0 || restart})) return;
  if (restart && !await pgCoresSure("the config is saved first")) return;
  pgCoresBusy(true);
  try {
    const out = await pgApi(id, `/cores/${st.coreId}`, {method: "PUT", body: {text, confirm: ws.length > 0 || restart, restart, base_rev: st.d.rev}});
    st.notice = pgCoresNotice(out, "Saved");
    toast(out.restarted ? "✓ Saved and restarted" : "✓ Saved: the nodes use it after their next restart", out.restart_error ? "bad" : "ok");
    await pgCoresAfter(id);
  } catch (e) {
    pgCoresBusy(false);
    if (/changed on the panel after you opened/.test(e.message)) await pgCoresStale(id, text);
    else toast("✕ " + e.message, "bad");
  }
});

/* somebody changed the core on the panel while this page was open: offer to read it again and keep the text of the editor */
async function pgCoresStale(id, text) {
  const st = pgCoresSt;
  if (!await gconfirm("The core was changed on the panel after you opened it.\nRead it again and keep your text in the editor? The changes shown are then your text against the new config: check that it does not undo the other change.", {danger: true})) return;
  try { await pgCoresLoad(id, st.coreId); st.draft = text === st.d.text ? null : text; } catch (e) { toast("✕ " + e.message, "bad"); }
  pgCoresRedraw();
}

/* the buttons under the editor are off while a change is being made (no second click), and on again when it failed */
function pgCoresBusy(on) {
  document.querySelectorAll(".pg-cores-act button").forEach(b => { b.disabled = on; });
  if (!on) pgCoresLive();
}

/* read the list and the core again after a change and draw them */
async function pgCoresAfter(id) {
  const st = pgCoresSt;
  try {
    st.list = (await pgApi(id, "/cores")).cores; st.at = Date.now();
    await pgCoresLoad(id, st.coreId);
  } catch (e) { toast("✕ " + e.message, "bad"); }
  pgCoresRedraw();
}

/* the message under the core header after a change: what was done (`what`), how the nodes look, and the next step */
function pgCoresNotice(out, what) {
  const nd = out.nodes, bad = nd && nd.known && (nd.problems.length > 0);
  let html = `<b>✓ ${esc(what)}.</b> ` + (out.backup ? "The config it replaced was backed up. " : "");
  if (out.restart_error) return {cls: "bad", html: html + `The restart did not work: ${esc(out.restart_error)}. The nodes still run the old config. <button class="sm danger" onclick="pgCoresRestart()">Restart nodes…</button>`};
  if (out.restarted && nd && nd.known) html += `Xray restarted: ${nd.connected} of ${pgCoresPl(nd.total, "node")} connected${nd.connecting ? `, ${nd.connecting} still connecting` : ""}.`;
  else if (out.restarted) html += "Xray restarted; the nodes could not be read to check them.";
  else html += `The nodes run it after their next restart. <button class="sm danger" onclick="pgCoresRestart()">Restart nodes…</button>`;
  if (bad) html += ` <b>Problems:</b> ${nd.problems.map(x => `${esc(x.name)} (${esc(x.status)}${x.message ? ": " + esc(x.message) : ""})`).join(", ")}. ${out.backup ? `<button class="sm danger" onclick="pgCoresRestore('${esc(out.backup)}', true)">Roll back and restart…</button>` : ""}`;
  const warn = out.warnings && out.warnings.length ? `<div class="mut">Confirmed warnings: ${out.warnings.map(w => esc(w.text)).join(" · ")}</div>` : "";
  return {cls: bad ? "bad" : "ok", html: html + warn};
}

window.pgCoresRestart = pgCoresOnce(async () => {
  const st = pgCoresSt, id = st.id;
  if (!await gconfirm(`Restart Xray on every node of the core “${st.d ? st.d.core.name : ""}”?\nThe config the panel has stored is used as it is.`, {danger: true})) return;
  if (!await pgCoresSure("nothing is saved by this")) return;
  try {
    const out = await pgApi(id, `/cores/${st.coreId}/restart`, {method: "POST", body: {confirm: true}});
    st.notice = pgCoresNotice(out, "Done");
    toast("✓ Xray restarted on the nodes", "ok");
  } catch (e) { toast("✕ " + e.message, "bad"); }
  pgCoresRedraw();
});

window.pgCoresBackupView = async file => {
  const st = pgCoresSt;
  try {
    const v = await pgApi(st.id, `/cores/${st.coreId}/backups/${encodeURIComponent(file)}`);
    const df = v.diff, sum = v.same ? '<span class="pill ok">the same as the config on the panel</span>' : `<span class="pg-cores-add">+${df.added}</span><span class="pg-cores-rm">−${df.removed}</span><span class="mut">lines that would change if you restored it</span>`;
    showModal(`<b>Backup of ${esc(pgCoresStamp(v.backup.at))}</b>`, `<div class="pg-cores-stat">${sum}<span class="mut">${esc(v.backup.note || "")}</span></div>${v.errors.map(e => `<div class="err">${esc(e)}</div>`).join("")}${pgCoresWarnHtml(v.warnings)}
      ${pgCoresDiffHtml(df)}<details class="pg-cores-full"><summary>The whole backup (secrets hidden)</summary><pre class="pg-cores-pre">${esc(v.text)}</pre></details>
      <div class="frm-act"><button class="pri" ${v.same || v.errors.length ? "disabled" : ""} onclick="closeModal();pgCoresRestore('${esc(file)}')">Restore…</button><button onclick="closeModal()">Close</button></div>`, "wide");
  } catch (e) { toast("✕ " + e.message, "bad"); }
};

/* put a backup back. `restartToo` is set by the “Roll back and restart” button after a restart that left nodes down */
window.pgCoresRestore = pgCoresOnce(async (file, restartToo) => {
  const st = pgCoresSt, id = st.id;
  let v;
  try { v = await pgApi(id, `/cores/${st.coreId}/backups/${encodeURIComponent(file)}`); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  if (v.errors.length) { toast("✕ This backup cannot be put back: " + v.errors[0], "bad"); return; }
  if (v.same) { toast("The core already has this config"); return; }
  const ws = v.warnings, text = `Restore the backup of ${pgCoresStamp(v.backup.at)}?\n${pgCoresPl(v.diff.added, "line")} added, ${v.diff.removed} removed. The config it replaces is backed up first.` + (ws.length ? `\n\n${pgCoresPl(ws.length, "warning")}:\n${pgCoresWarnText(ws)}` : "");
  let restart = !!restartToo;
  if (restart) { if (!await gconfirm(text, {danger: true})) return; }
  else {
    const pick = await glassChoice(text, [{label: "Restore", v: "store", cls: ws.length ? "danger" : "pri", note: "stored on the panel; the nodes use it after their next restart"}, {label: "Restore and restart", v: "restart", cls: "danger", note: "Xray restarts on the nodes: customers are cut for a moment"}]);
    if (!pick) return;
    restart = pick === "restart";
  }
  if (restart && !await pgCoresSure("the backup is put back first")) return;
  try {
    const out = await pgApi(id, `/cores/${st.coreId}/backups/${encodeURIComponent(file)}/restore`, {method: "POST", body: {confirm: ws.length > 0 || restart, restart}});
    st.notice = pgCoresNotice(out, "Restored");
    toast(out.restarted ? "✓ Restored and restarted" : "✓ Restored: the nodes use it after their next restart", out.restart_error ? "bad" : "ok");
    await pgCoresAfter(id);
  } catch (e) { toast("✕ " + e.message, "bad"); }
});
