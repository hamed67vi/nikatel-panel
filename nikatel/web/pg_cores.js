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
const pgCoresSt = {id: 0, list: null, at: 0, coreId: 0, d: null, view: "edit", draft: null, dry: null, dryFor: "", notice: null, seq: 0, ts: 0, busy: false, sel: new Set()};
const pgCoresFirst = 1, pgCoresMaxMany = 20;                      // the panel's first core is its default one and is never deleted; most cores one delete call takes (twins: pg_cores.DEFAULT_CORE_ID, MAX_BULK_CORES)
let pgCoresTimer = 0;
const pgCoresCur = () => (pgCoresSt.draft === null ? pgCoresSt.d.text : pgCoresSt.draft);
const pgCoresPl = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const pgCoresFmt = {hour12: false, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"};
const pgCoresDate = iso => { if (!iso) return "—"; const t = Date.parse(iso); return isNaN(t) ? String(iso).slice(0, 16).replace("T", " ") : new Date(t).toLocaleString(undefined, pgCoresFmt); };
const pgCoresStamp = ts => ts ? new Date(ts * 1000).toLocaleString(undefined, pgCoresFmt) : "—";

pgTabs.cores = {label: "Core", order: 50, types: ["pasarguard"], render: pgCoresRender};

async function pgCoresRender(id, box) {
  const st = pgCoresSt, my = ++st.seq;
  if (st.id !== id) Object.assign(st, {id, list: null, at: 0, coreId: 0, d: null, view: "edit", draft: null, dry: null, dryFor: "", notice: null, sel: new Set()});      // another panel: nothing of the last one stays
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
  const st = pgCoresSt, can = st.list.some(c => c.id !== pgCoresFirst);
  const rows = st.list.map(c => `<tr class="${c.id === st.coreId ? "pg-cores-on" : ""}"><td class="pg-ck"><input type="checkbox" class="ck" value="${c.id}" ${st.sel.has(c.id) ? "checked" : ""} ${c.id === pgCoresFirst ? 'disabled title="The first core is the default one of the panel: it can not be deleted"' : ""} onchange="pgCoresSel(this)" aria-label="Tick ${esc(c.name || "core " + c.id)}"></td>
    <td class="name"><b>${esc(c.name || "(no name)")}</b> <small class="mut">#${c.id}</small></td><td><span class="td-type">${esc(c.type)}</span></td>
    <td class="mut">${esc(pgCoresDate(c.created_at))}</td><td class="mut">${esc(pgCoresDate(c.updated_at))}</td><td class="num">${c.inbounds}</td><td class="num">${c.outbounds}</td><td class="num">${c.rules}</td><td class="num">${c.backups}</td>
    <td class="pg-cores-go">${c.id === st.coreId ? '<span class="pill ok">open</span>' : `<button class="sm pri" onclick="${modal ? "closeModal();" : ""}pgCoresOpen(${c.id})">Open</button>`}</td></tr>`).join("");
  return `<div class="pg-selbar" id="pg-cores-selbar" hidden></div><div class="lbox full pg-cores-list"><table class="tbl lift"><colgroup><col style="width:34px"><col><col style="width:100px"><col style="width:150px"><col style="width:150px"><col style="width:78px"><col style="width:88px"><col style="width:62px"><col style="width:78px"><col style="width:84px"></colgroup>
    <thead><tr><th class="pg-ck"><input type="checkbox" class="ck" id="pg-cores-all" ${can ? "" : "disabled"} onchange="pgCoresSelAll(this.checked)" aria-label="Tick every core that can be deleted" title="Tick every core that can be deleted"></th><th>Core</th><th>Type</th><th>Created</th><th>Updated</th><th>Inbounds</th><th>Outbounds</th><th>Rules</th><th>Backups</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="10" class="mut">this panel has no core</td></tr>'}</tbody></table></div>`;
}

/* ---- several cores at once: ticks in the list, a bar with the delete button while something is ticked */
const pgCoresTicked = () => [...pgCoresSt.sel].filter(i => (pgCoresSt.list || []).some(c => c.id === i && c.id !== pgCoresFirst));      // (a core that is gone, and the first core, do not count)
function pgCoresSelSync() {
  const st = pgCoresSt, n = pgCoresTicked().length, bar = $("#pg-cores-selbar"), all = $("#pg-cores-all"), can = (st.list || []).filter(c => c.id !== pgCoresFirst), k = can.filter(c => st.sel.has(c.id)).length;
  if (bar) { bar.hidden = !n; bar.innerHTML = `<span class="mut">${pgCoresPl(n, "core")} ticked</span><button class="sm danger" onclick="pgCoresDeleteMany()" title="Delete the ticked cores in one call: all of them or none; each is saved here first">Delete ${n} selected…</button><button class="sm" onclick="pgCoresUntick()">Clear</button>`; }
  if (all) { all.checked = k > 0 && k === can.length; all.indeterminate = k > 0 && k < can.length; }
}
const pgCoresTickBoxes = on => document.querySelectorAll(".pg-cores-list input.ck[value]").forEach(i => { i.checked = on && !i.disabled; });
window.pgCoresSel = el => { const st = pgCoresSt, id = +el.value; if (el.checked) st.sel.add(id); else st.sel.delete(id); pgCoresSelSync(); };
window.pgCoresSelAll = on => { const st = pgCoresSt; st.list.forEach(c => { if (c.id === pgCoresFirst) return; if (on) st.sel.add(c.id); else st.sel.delete(c.id); }); pgCoresTickBoxes(on); pgCoresSelSync(); };
window.pgCoresUntick = () => { pgCoresSt.sel = new Set(); pgCoresTickBoxes(false); pgCoresSelSync(); };

function pgCoresHeadHtml(d) {
  const c = d.core, ex = c.exclude_inbound_tags.length, fb = c.fallbacks_inbound_tags.length;
  return `<div class="pg-cores-head"><b class="pg-cores-name">${esc(c.name || "(no name)")}</b><span class="td-type">${esc(c.type)}</span><span class="mut">created ${esc(pgCoresDate(c.created_at))}${c.updated_at ? " · updated " + esc(pgCoresDate(c.updated_at)) : ""}</span>
    <span class="mut">${pgCoresPl(d.inbounds.length, "inbound")} · ${pgCoresPl(d.outbounds.length, "outbound")} · ${pgCoresPl(d.rules, "rule")}</span>
    ${ex || fb ? `<span class="mut" title="Excluded: ${esc(c.exclude_inbound_tags.join(", "))}  Fallbacks: ${esc(c.fallbacks_inbound_tags.join(", "))}">${ex} excluded · ${fb} fallback tags</span>` : ""}
    ${d.masked ? `<span class="pill" title="Private keys and passwords stay on the server: they are shown as &lt;secret:…&gt; placeholders and put back when you save">🔒 ${d.masked} hidden</span>` : ""}
    <span class="sp"></span><button class="sm" onclick="pgCoresReloadAll()" title="Read the cores from the panel again">↻ Reload</button><button class="sm" onclick="pgCoresSettings()" title="The name of the core and its excluded / fallback inbound tags">Settings…</button><button class="sm" onclick="pgCoresDuplicate()" title="Make a new core as a copy of this one">Duplicate…</button><button class="sm danger" onclick="pgCoresRestart()" title="Restart Xray on every node of this core">Restart nodes…</button><button class="sm danger" onclick="pgCoresDelete()" title="Delete this core from the panel (a backup is kept here)">Delete…</button></div>`;
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
  const ib = d.inbounds.map((r, i) => {
    const bad = r.security === "none" && r.public && proxy.includes(r.protocol);
    return `<tr><td class="name">${esc(r.tag)}</td><td class="mono">${esc(r.port == null ? "—" : r.port)}</td><td>${esc(r.protocol)}</td><td>${esc(r.network)}</td>
      <td><span class="pill ${r.security === "none" ? (bad ? "warn" : "") : "ok"}" title="${bad ? "No TLS or REALITY and open to the outside" : ""}">${esc(r.security)}</span></td><td class="mut">${esc(r.listen || "all addresses")}</td>
      <td class="num">${r.hosts == null ? "?" : r.hosts}</td><td class="mut">${r.groups == null ? "?" : esc(r.groups.join(", ")) || "—"}</td>
      <td class="pg-cores-go"><button class="sm" onclick="pgCoresInboundEdit(${i})" title="Edit this inbound as JSON">Edit</button> <button class="sm danger" onclick="pgCoresInboundDelete(${i})" title="Take this inbound out of the config">Delete</button></td></tr>`;
  }).join("");
  const ob = d.outbounds.map(r => `<tr><td class="name">${esc(r.tag)}</td><td>${esc(r.protocol)}</td><td>${esc(r.network)}</td><td class="mono mut">${esc(r.target || "—")}</td></tr>`).join("");
  return `<div class="pg-cores-ibbar"><button class="sm pri" onclick="pgCoresInboundAdd()" title="Add an inbound: choose the protocol, the network and the security, and the panel writes the JSON">＋ Add inbound</button><span class="mut">Each change is checked first, shows what it would break (hosts and groups that use an inbound), backs the config up and is read back. Nothing restarts unless you say so.</span></div>
    <div class="lbox full pg-cores-ib"><table class="tbl lift"><colgroup><col><col style="width:70px"><col style="width:110px"><col style="width:90px"><col style="width:90px"><col style="width:120px"><col style="width:60px"><col style="width:20%"><col style="width:140px"></colgroup>
    <thead><tr><th>Inbound</th><th>Port</th><th>Protocol</th><th>Network</th><th>Security</th><th>Listens on</th><th>Hosts</th><th>Groups</th><th></th></tr></thead><tbody>${ib || '<tr><td colspan="9" class="mut">no inbound</td></tr>'}</tbody></table></div>
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
  return pgBar(`<b>Cores</b><span class="pill">${st.list.length}</span>${chips}`, `<button class="sm" onclick="pgCoresDeleted()" title="Cores that are gone from the panel but have a backup here: make one again">Deleted…</button>${st.d ? '<button class="sm" onclick="pgCoresAll()" title="All cores with their dates and counts">All cores…</button>' : ""}${window.pgKindBtn ? pgKindBtn() : ""}<button class="sm pri" onclick="pgCoresNew()" title="Make a new core (a name, a type, a config)">＋ New core</button>`);
}

window.pgCoresAll = () => { showModal("<b>Cores of this panel</b>", pgCoresListHtml(true), "wide"); pgCoresSelSync(); };

function pgCoresDraw(box) {
  const st = pgCoresSt, d = st.d;
  let html = pgCoresBarHtml();
  if (d) html += pgCoresHeadHtml(d) + pgCoresNoticeHtml() + pgCoresSubHtml(d) + `<div class="pg-cores-body" id="pg-cores-body">${st.view === "edit" ? pgCoresEditHtml(d) : st.view === "over" ? pgCoresOverHtml(d) : pgCoresBackupsHtml(d)}</div>`;
  else html += st.list.length ? pgCoresListHtml(false) + pgEmpty("Open a core to see and edit its config.") : pgEmpty("This panel has no core.");
  pgPaint(box, html);
  pgCoresSelSync();
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

/* ---- a new core, the settings of a core, delete (and make again), and ONE inbound at a time: add (a form, or the JSON), edit (the JSON), delete.
   Every change goes through the server's dry run first (the checks, who uses an inbound, the diff) and then the same save as the editor (backup, read back, optional restart). */
const pgCoresTags = s => String(s || "").split(/[,\n]/).map(x => x.trim()).filter(Boolean);
const pgCoresReloadTo = async coreId => { const st = pgCoresSt; Object.assign(st, {coreId, d: null, draft: null, dry: null, dryFor: "", view: "edit", notice: null, ts: 0}); await pgCoresAfter(st.id); };

window.pgCoresNew = async (pre = {}) => {
  const st = pgCoresSt;
  let t; try { t = await pgApi(st.id, "/cores/template?type=xray"); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  st.tpl = t;
  const copyOpts = st.list.map(c => `<option value="${c.id}" ${c.id === pre.copy ? "selected" : ""}>${esc(c.name || "(no name)")} #${c.id}</option>`).join("");
  showModal("<b>＋ New core</b>", `<form class="frm pg-cores-form" onsubmit="return pgCoresNewGo(event)">
    <label>Name<input name="name" required maxlength="256" autocomplete="off" placeholder="Iran core" value="${esc(pre.name || "")}"></label>
    <label>Type<select name="type" onchange="pgCoresNewSync(this.form)">${t.types.map(x => `<option>${esc(x)}</option>`).join("")}</select></label>
    <label>Start from<select name="from" onchange="pgCoresNewSync(this.form)"><option value="template" ${pre.copy ? "" : "selected"}>the starting config of the panel</option>${st.list.length ? `<option value="copy" ${pre.copy ? "selected" : ""}>a copy of another core</option>` : ""}<option value="text">my own JSON</option></select></label>
    <label class="pg-cores-copy">Core to copy<select name="copy_from">${copyOpts}</select></label>
    <label class="wide pg-cores-text">Config (JSON)<textarea name="text" rows="12" spellcheck="false" oninput="this.dataset.touched = 1"></textarea></label>
    <label>Excluded inbound tags <small>optional, comma separated: not given to users</small><input name="exclude" autocomplete="off"></label>
    <label>Fallback inbound tags <small>optional: only reached as a fallback</small><input name="fallbacks" autocomplete="off"></label>
    <small class="wide mut">A new core runs on no node until you choose it for one (Nodes → Edit). Nothing restarts.</small>
    <div class="frm-act"><button class="pri" type="submit">Make the core</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`, "wide");
  pgCoresNewSync($("#modal form.pg-cores-form"));
};
window.pgCoresNewSync = f => {
  const from = f.elements.from.value, type = f.elements.type.value, ta = f.elements.text;
  if (type !== "xray" && from === "template") f.elements.from.value = "text";      // only Xray has a starting config: the other types need their JSON
  const mode = f.elements.from.value;
  f.querySelector(".pg-cores-copy").hidden = mode !== "copy";
  f.querySelector(".pg-cores-text").hidden = mode === "copy" || (mode === "template" && type === "xray");
  if (mode === "text" && !ta.dataset.touched) ta.value = type === "xray" ? pgCoresSt.tpl.text : "{\n  \n}";
};
window.pgCoresDuplicate = () => { const d = pgCoresSt.d; if (d) pgCoresNew({copy: pgCoresSt.coreId, name: (d.core.name || "core") + " copy"}); };
window.pgCoresNewGo = async e => {
  e.preventDefault(); const f = e.target, v = k => (f.elements[k] ? f.elements[k].value : ""), mode = v("from"), type = v("type");
  const body = {name: v("name").trim(), type, exclude_inbound_tags: pgCoresTags(v("exclude")), fallbacks_inbound_tags: pgCoresTags(v("fallbacks"))};
  if (mode === "copy") body.copy_from = +v("copy_from");
  else if (mode === "text") { body.text = v("text"); if (!body.text.trim()) { toast("✕ Write or paste the config of the core (JSON)", "bad"); return false; } }
  try {
    const out = await pgApi(pgCoresSt.id, "/cores", {method: "POST", body});
    closeModal(); toast(`✓ Core ${body.name} made (#${out.core.id}): ${pgCoresPl(out.inbounds, "inbound")}; no node runs it yet`, "ok");
    await pgCoresReloadTo(out.core.id);
  } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};

window.pgCoresSettings = () => {
  const d = pgCoresSt.d; if (!d) return;
  const c = d.core, tags = d.inbounds.map(r => r.tag).filter(Boolean);
  showModal(`<b>Settings of ${esc(c.name || "the core")}</b>`, `<form class="frm pg-cores-form" onsubmit="return pgCoresSettingsGo(event)">
    <label class="wide">Name<input name="name" required maxlength="256" value="${esc(c.name)}" autocomplete="off"></label>
    <label class="wide">Excluded inbound tags <small>comma separated: the panel does not give these inbounds to users (they stay in the config)</small><input name="exclude" list="pg-cores-tags" value="${esc(c.exclude_inbound_tags.join(", "))}" autocomplete="off"></label>
    <label class="wide">Fallback inbound tags <small>comma separated: inbounds that are only reached as the fallback of another one (hidden from users too)</small><input name="fallbacks" list="pg-cores-tags" value="${esc(c.fallbacks_inbound_tags.join(", "))}" autocomplete="off"></label>
    <datalist id="pg-cores-tags">${tags.map(t => `<option value="${esc(t)}"></option>`).join("")}</datalist>
    <small class="wide mut">Saving writes the same config back with the new name and lists. The core is backed up first; nothing restarts.</small>
    <div class="frm-act"><button class="pri" type="submit">Save</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`, "wide");
};
window.pgCoresSettingsGo = async e => {
  e.preventDefault(); const f = e.target, st = pgCoresSt;
  try {
    await pgApi(st.id, `/cores/${st.coreId}/meta`, {method: "PATCH", body: {name: f.elements.name.value.trim(), exclude_inbound_tags: pgCoresTags(f.elements.exclude.value), fallbacks_inbound_tags: pgCoresTags(f.elements.fallbacks.value), base_rev: st.d.rev}});
    closeModal(); toast("✓ Saved (the old values are in Backups)", "ok"); await pgCoresReloadTo(st.coreId);
  } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};

/* delete: the server says what goes with the core (nodes, hosts and groups); with warnings the name must be typed */
window.pgCoresDelete = pgCoresOnce(async () => {
  const st = pgCoresSt, id = st.id, cid = st.coreId; if (!st.d) return;
  let ck; try { ck = await pgApi(id, `/cores/${cid}/delete-check`); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  if (!ck.allowed) { toast("✕ " + ck.reason, "bad"); return; }
  const ws = ck.warnings, name = ck.name || "this core";
  if (!await gconfirm(`Delete the core “${name}” (${pgCoresPl(ck.inbounds, "inbound")}) from the panel?${ws.length ? `\n\n${pgCoresPl(ws.length, "warning")}:\n${pgCoresWarnText(ws)}` : ""}\n\nThe panel cannot undo it. The whole core is saved here first: Deleted… can make a new core from it (with a new id).`, {danger: true})) return;
  if (ws.length) { const typed = await gprompt(`Type the name of the core to delete it: ${name}`, ""); if (typed == null || typed.trim() !== name.trim()) { toast("Not deleted: the name did not match"); return; } }
  try {
    const out = await pgApi(id, `/cores/${cid}?confirm=true`, {method: "DELETE"});
    toast(`✓ Core ${name} deleted (backup ${out.backup})`, "ok");
    Object.assign(st, {list: null, d: null, coreId: 0, draft: null, dry: null, dryFor: "", notice: null}); refresh();
  } catch (e) { toast("✕ " + e.message, "bad"); }
});

/* the ticked cores in ONE call (the panel's bulk delete: all or none); the question lists what they take with it, and with warnings the word delete must be typed */
window.pgCoresDeleteMany = pgCoresOnce(async () => {
  const st = pgCoresSt, id = st.id, ids = pgCoresTicked(); if (!ids.length) return;
  if (ids.length > pgCoresMaxMany) { toast(`✕ tick ${pgCoresMaxMany} cores at most for one delete`, "bad"); return; }
  let checks; try { checks = await Promise.all(ids.map(i => pgApi(id, `/cores/${i}/delete-check`))); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  const bad = checks.find(ck => !ck.allowed); if (bad) { toast("✕ " + bad.reason, "bad"); return; }
  const ws = checks.flatMap(ck => ck.warnings.map(w => ({...w, text: `${ck.name}: ${w.text}`}))), names = checks.map(ck => `“${ck.name}”`), who = `${pgCoresPl(ids.length, "core")} (${names.slice(0, 5).join(", ")}${names.length > 5 ? ", …" : ""})`;
  if (!await gconfirm(`Delete ${who} from the panel?${ws.length ? `\n\n${pgCoresPl(ws.length, "warning")}:\n${pgCoresWarnText(ws)}` : ""}\n\nThe panel cannot undo it. Every core is saved here first: Deleted… can make a new core from each (with a new id).`, {danger: true})) return;
  if (ws.length) { const typed = await gprompt(`Type delete to delete these ${ids.length} cores`, ""); if (typed == null || typed.trim().toLowerCase() !== "delete") { toast("Not deleted: the word did not match"); return; } }
  try {
    const out = await pgApi(id, "/cores/bulk-delete", {method: "POST", body: {ids, confirm: true}});
    toast(`✓ ${pgCoresPl(out.count, "core")} deleted (${pgCoresPl(out.backups.length, "backup")} kept here)`, "ok");
    Object.assign(st, {list: null, d: null, coreId: 0, draft: null, dry: null, dryFor: "", notice: null, sel: new Set()}); closeModal(); refresh();
  } catch (e) { toast("✕ " + e.message, "bad"); }
});

window.pgCoresDeleted = async () => {
  const draw = body => showModal("<b>Deleted cores</b>", body, "wide");
  draw('<div class="loading"><i class="spin"></i><span>Reading…</span></div>');
  try {
    const r = await pgApi(pgCoresSt.id, "/cores/deleted"); pgCoresSt.gone = r.cores;
    draw(`<div class="lbox full pg-cores-list"><table class="tbl lift"><colgroup><col><col style="width:90px"><col style="width:170px"><col style="width:90px"><col style="width:100px"><col style="width:150px"></colgroup>
      <thead><tr><th>Core</th><th>Type</th><th>Backup of</th><th>Inbounds</th><th>Outbounds</th><th></th></tr></thead><tbody>${r.cores.map((g, i) => `<tr><td class="name"><b>${esc(g.name || "(no name)")}</b> <small class="mut">was #${g.core}</small></td><td><span class="td-type">${esc(g.type)}</span></td><td class="mut">${esc(pgCoresStamp(g.at))}</td><td class="num">${g.inbounds}</td><td class="num">${g.outbounds}</td>
        <td class="pg-cores-go"><button class="sm pri" onclick="pgCoresRecreate(${i})" title="Make a NEW core from this backup (new id; the nodes must be pointed at it again)">Make it again</button></td></tr>`).join("") || '<tr><td colspan="6" class="mut">no core is gone: a core that is deleted here (or changed here and deleted on the panel) shows up in this list</td></tr>'}</tbody></table></div>
      <small class="mut">The newest backup of each core that is not on the panel any more. The files are kept here, in a folder only this server can read.</small>`);
  } catch (e) { draw(`<div class="err">${esc(e.message)}</div>`); }
};
window.pgCoresRecreate = async i => {
  const g = (pgCoresSt.gone || [])[i]; if (!g) return;
  if (!await gconfirm(`Make a new core “${g.name}” from the backup of ${pgCoresStamp(g.at)}? It gets a new id and runs on no node until you choose it for one.`)) return;
  try { const out = await pgApi(pgCoresSt.id, `/cores/deleted/${g.core}/${encodeURIComponent(g.file)}/recreate`, {method: "POST"}); closeModal(); toast(`✓ Core ${g.name} made again (#${out.core.id})`, "ok"); await pgCoresReloadTo(out.core.id); }
  catch (e) { toast("✕ " + e.message, "bad"); }
};

/* ---- one inbound */
const pgCoresIbNet = ["tcp", "ws", "grpc", "httpupgrade", "xhttp"], pgCoresIbMode = ["auto", "packet-up", "stream-up", "stream-one"];
const pgCoresIbOf = i => { const st = pgCoresSt, r = st.d.inbounds[i]; return r ? {row: r, ib: (st.d.config.inbounds || []).find(x => x && x.tag === r.tag)} : {}; };
window.pgCoresInboundAdd = () => {
  const o = a => a.map(x => `<option>${esc(x)}</option>`).join("");
  showModal("<b>＋ Add an inbound</b>", `<div class="tabs" style="margin-bottom:10px"><button class="on" id="pg-ib-t-form" onclick="pgCoresIbMode('form')">Form</button><button id="pg-ib-t-json" onclick="pgCoresIbMode('json')">JSON</button></div>
    <form class="frm pg-cores-form pg-ib-form" id="pg-ib-form" onsubmit="return false" onchange="pgCoresIbSync()">
      <label>Protocol<select name="protocol">${o(["vless", "vmess", "trojan", "shadowsocks"])}</select></label><label>Network<select name="network">${o(pgCoresIbNet)}</select></label>
      <label>Security<select name="security"><option>none</option><option>tls</option><option selected>reality</option></select></label>
      <label>Port<input name="port" type="number" min="1" max="65535" value="443" required></label><label>Tag <small>empty = made from the choices</small><input name="tag" maxlength="80" autocomplete="off"></label><label>Listen<input name="listen" value="0.0.0.0" autocomplete="off"></label>
      <label data-net="ws,httpupgrade,xhttp">Path<input name="path" placeholder="/" autocomplete="off"></label><label data-net="ws,httpupgrade,xhttp">Host header<input name="host" autocomplete="off"></label>
      <label data-net="grpc">Service name<input name="service" autocomplete="off"></label><label data-net="xhttp">xhttp mode<select name="mode">${o(pgCoresIbMode)}</select></label>
      <label class="wide" data-sec="tls">Certificate file on the node<input name="tls_cert" placeholder="/var/lib/pg-node/certs/ssl_cert.pem" autocomplete="off"></label><label class="wide" data-sec="tls">Key file on the node<input name="tls_key" placeholder="/var/lib/pg-node/certs/ssl_key.pem" autocomplete="off"></label><label data-sec="tls">ALPN<input name="alpn" placeholder="h2,http/1.1" autocomplete="off"></label>
      <label class="wide" data-sec="reality">The site REALITY imitates<span class="pg-ib-dest"><input name="reality_dest" placeholder="www.example.com:443" autocomplete="off"><button type="button" class="sm" onclick="pgCoresScan()" title="Ask the panel whether this site is a good target: TLS 1.3, h2, x25519">Scan…</button></span></label>
      <label data-sec="reality">Server names <small>comma separated</small><input name="reality_names" placeholder="www.example.com" autocomplete="off"></label><label data-sec="reality">Short ids <small>empty = one is made</small><input name="reality_short_ids" autocomplete="off"></label>
      <label class="wide" data-sec="reality">Private key <small>empty = a new pair is made and the public key is shown after; a key you give stays here</small><input name="reality_private_key" type="password" autocomplete="new-password" spellcheck="false"></label>
      <label class="chk wide"><input type="checkbox" name="sniffing" checked> Sniffing (http, tls, quic)</label></form>
    <div id="pg-ib-json" hidden><textarea id="pg-ib-ta" class="pg-cores-ta" rows="16" spellcheck="false" placeholder='{"tag": "My inbound", "port": 443, "protocol": "vless", "settings": {"clients": [], "decryption": "none"}, "streamSettings": {...}}'></textarea></div>
    <div id="pg-ib-res"></div>
    <div class="frm-act"><button class="sm" onclick="pgCoresIbRun('add', 'preview')">Check it</button><span class="sp"></span><button class="pri" onclick="pgCoresIbRun('add', 'save')">Add the inbound…</button><button class="danger" onclick="pgCoresIbRun('add', 'restart')" title="Add it and restart Xray on the nodes of this core">Add and restart…</button><button onclick="closeModal()">Cancel</button></div>`, "wide");
  pgCoresIbSync();
};
window.pgCoresIbMode = m => { $("#pg-ib-form").hidden = m === "json"; $("#pg-ib-json").hidden = m !== "json"; $("#pg-ib-t-form").classList.toggle("on", m !== "json"); $("#pg-ib-t-json").classList.toggle("on", m === "json"); };
window.pgCoresIbSync = () => {      // the fields that do not apply to the chosen network / security are hidden (and not sent)
  const f = $("#pg-ib-form"); if (!f) return;
  const net = f.elements.network.value, sec = f.elements.security.value;
  f.querySelectorAll("[data-net]").forEach(l => { const on = l.dataset.net.split(",").includes(net); l.hidden = !on; l.querySelectorAll("input, select").forEach(i => { i.disabled = !on; }); });
  f.querySelectorAll("[data-sec]").forEach(l => { const on = l.dataset.sec === sec; l.hidden = !on; l.querySelectorAll("input, select, button").forEach(i => { i.disabled = !on; }); });
  const ss = f.elements.protocol.value === "shadowsocks"; [...f.elements.security.options].forEach(o => { o.disabled = ss && o.value === "reality"; }); if (ss && sec === "reality") f.elements.security.value = "none";
};
const pgCoresIbSpec = () => {
  const f = $("#pg-ib-form"), spec = {};
  [...f.elements].forEach(el => { if (!el.name || el.disabled) return; spec[el.name] = el.type === "checkbox" ? el.checked : el.type === "number" ? +el.value : el.value.trim(); });
  Object.keys(spec).forEach(k => { if (spec[k] === "" && k !== "listen") delete spec[k]; });
  return spec;
};
window.pgCoresScan = async () => {
  const f = $("#pg-ib-form"), target = f.elements.reality_dest.value.trim(); if (!target) { toast("✕ Write the site first", "bad"); return; }
  const res = $("#pg-ib-res"); res.innerHTML = '<div class="loading"><i class="spin"></i><span>Asking the panel to look at the site (a few seconds)…</span></div>';
  try {
    const r = await pgApi(pgCoresSt.id, "/cores/reality-scan", {method: "POST", body: {target}});
    const yn = (b, t) => `<span class="pill ${b ? "ok" : "warn"}">${b ? "✓" : "✕"} ${t}</span>`;
    res.innerHTML = `<div class="pg-cores-stat"><b>${esc(r.host || target)}</b>${yn(r.feasible, "usable as a target")}${yn(r.tls13, "TLS 1.3")}${yn(r.h2, "h2")}${yn(r.x25519 !== false, "x25519")}${yn(r.cert_valid, "valid certificate")}${r.latency_ms != null ? `<span class="mut">${r.latency_ms} ms</span>` : ""}</div>${r.reason ? `<div class="mut">${esc(r.reason)}</div>` : ""}${(r.server_names || []).length ? `<div class="mut">Names of its certificate: ${esc(r.server_names.slice(0, 8).join(", "))} <button class="sm" onclick="pgCoresUseNames()">Use them as server names</button></div>` : ""}`;
    pgCoresSt.scan = r;
  } catch (e) { res.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
};
window.pgCoresUseNames = () => { const r = pgCoresSt.scan, f = $("#pg-ib-form"); if (r && f) f.elements.reality_names.value = (r.server_names || []).slice(0, 4).join(", "); };

/* the body of a dry run / an apply for the chosen op: the form, the JSON of an inbound, or just the tag (delete) */
const pgCoresIbBody = (op, tag) => {
  if (op === "delete") return {op, tag};
  const json = $("#pg-ib-json"), jsonMode = json && !json.hidden && $("#pg-ib-ta");
  if (op === "set" || jsonMode) { const text = $("#pg-ib-ta").value; if (!text.trim()) throw new Error("Write the JSON of the inbound first"); return {op, tag, text}; }
  return {op, spec: pgCoresIbSpec()};
};
const pgCoresIbShow = r => {      // what the dry run says, in the window
  const res = $("#pg-ib-res"); if (!res) return;
  res.innerHTML = `<div class="pg-cores-dry"><div class="pg-cores-stat"><b>Check</b>${r.valid ? (r.changed === false ? '<span class="pill">no change</span>' : '<span class="pill ok">✓ the checks pass</span>') : '<span class="pill err">✕ cannot be saved</span>'}${r.warnings.length ? `<span class="pill warn">${pgCoresPl(r.warnings.length, "warning")} · saving needs your OK</span>` : ""}${r.diff ? `<span class="mut">+${r.diff.added} −${r.diff.removed} lines</span>` : ""}</div>
    ${r.errors.map(e => `<div class="err">${esc(e)}</div>`).join("")}${pgCoresWarnHtml(r.warnings)}${r.inbound ? `<details class="pg-cores-full"><summary>The inbound as it will be stored (secrets hidden)</summary><pre class="pg-cores-pre">${esc(JSON.stringify(r.inbound, null, 2))}</pre></details>` : ""}${r.diff && !r.diff.same ? pgCoresDiffHtml(r.diff) : ""}</div>`;
};
/* how: "preview" shows the check; "save" and "restart" go on (the questions of a save, then the write) */
window.pgCoresIbRun = pgCoresOnce(async (op, how, tag) => {
  const st = pgCoresSt, id = st.id, cid = st.coreId; let body;
  try { body = pgCoresIbBody(op, tag); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  let r; try { r = await pgApi(id, `/cores/${cid}/inbounds/dry-run`, {method: "POST", body}); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  pgCoresIbShow(r);
  if (how === "preview") { toast(r.valid ? "✓ The checks pass" + (r.warnings.length ? ` · ${pgCoresPl(r.warnings.length, "warning")}` : "") : "✕ " + r.errors[0], r.valid ? "ok" : "bad"); return; }
  if (!r.valid) { toast("✕ " + r.errors[0], "bad"); return; }
  if (r.changed === false) { toast("Nothing to change"); return; }
  const restart = how === "restart", ws = r.warnings, verb = {add: "Add this inbound", set: "Save this inbound", delete: "Delete this inbound"}[op];
  if (!await gconfirm(`${verb}${restart ? " and restart Xray on the nodes" : ""}?\n${pgCoresPl(r.diff.added, "line")} added, ${r.diff.removed} removed. The current config is backed up first.${restart ? "" : "\nThe nodes keep running the old config until they restart."}${ws.length ? `\n\n${pgCoresPl(ws.length, "warning")}:\n${pgCoresWarnText(ws)}` : ""}`, {danger: ws.length > 0 || restart || op === "delete"})) return;
  if (restart && !await pgCoresSure("the change is saved first")) return;
  try {
    const out = await pgApi(id, `/cores/${cid}/inbounds/apply`, {method: "POST", body: {...body, confirm: ws.length > 0 || restart, restart, base_rev: st.d.rev}});
    closeModal();
    st.notice = pgCoresNotice(out, op === "add" ? "Inbound added" : op === "set" ? "Inbound saved" : "Inbound deleted");
    if (out.public_key) st.notice.html += ` <b>REALITY public key:</b> <code class="mono">${esc(out.public_key)}</code> <button class="sm" onclick="navigator.clipboard.writeText('${esc(out.public_key)}').then(() => toast('✓ Copied', 'ok'))">Copy</button> <span class="mut">(the private key stays on the panel)</span>`;
    toast(out.restarted ? "✓ Done and restarted" : "✓ Done: the nodes use it after their next restart", out.restart_error ? "bad" : "ok");
    await pgCoresAfter(id);
  } catch (e) {
    if (/changed on the panel after you opened/.test(e.message)) { toast("✕ The core was changed on the panel after you opened it: it is read again, make the change again", "bad"); closeModal(); await pgCoresAfter(id); }
    else toast("✕ " + e.message, "bad");
  }
});

window.pgCoresInboundEdit = i => {
  const {row, ib} = pgCoresIbOf(i); if (!ib) { toast("✕ That inbound is not in the config any more: reload the core", "bad"); return; }
  showModal(`<b>Edit the inbound ${esc(row.tag)}</b>`, `<small class="mut">The JSON of this inbound; private keys and passwords are shown as &lt;secret:…&gt; placeholders and put back when you save. Change the tag and the hosts and groups that use the old one lose it (the check says so).</small>
    <textarea id="pg-ib-ta" class="pg-cores-ta" rows="20" spellcheck="false" style="margin-top:8px">${esc(JSON.stringify(ib, null, 2))}</textarea><div id="pg-ib-res"></div>
    <div class="frm-act"><button class="sm" onclick="pgCoresIbRun('set', 'preview', pgCoresSt.ibTag)">Check it</button><span class="sp"></span><button class="pri" onclick="pgCoresIbRun('set', 'save', pgCoresSt.ibTag)">Save…</button><button class="danger" onclick="pgCoresIbRun('set', 'restart', pgCoresSt.ibTag)">Save and restart…</button><button onclick="closeModal()">Cancel</button></div>`, "wide");
  pgCoresSt.ibTag = row.tag;
};
window.pgCoresInboundDelete = i => {
  const {row} = pgCoresIbOf(i); if (!row) return;
  pgCoresIbRun("delete", "save", row.tag);
};
