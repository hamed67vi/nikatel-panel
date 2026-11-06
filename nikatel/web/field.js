/* The Field test tab of Network (1.69.235: ONE screen, no scrolling, drawn like a Mac app; the data comes from services/field.py): what the Iran test kit saw from REAL networks - a SIM's hotspot, a home line.
   A slim bar (networks, findings, newest run, the runs), a card per network (configs that work, the direct configs, the subscription names, the panel addresses - only what fails is named), one line per finding
   (click it for what would help) and every config as a list with a dot and the delay per network (hover for all its numbers). Routes: GET /api/field, /api/field/runs, /api/field/runs/{id}. Nothing is changed from here.
   Uses the helpers of app.js: $, api, esc, ago, when, renderSub, PAGES. */
const fd = {d: null, runs: [], html: "", open: {}, err: "", all: false, exp: {}, pop: false};
const FD_STATE = {ok: ["ok", "works"], partial: ["warn", "some sites cut"], slow: ["warn", "slow"], stall: ["bad", "stalls"], closed: ["bad", "opens nothing"], skipped: ["bad", "does not answer"]};
const FD_DIRECT = {ok: "reachable", ipblock: "IP blocked", allports: "all ports filtered", port: "port dropped", reset: "port reset", refused: "server refuses", tls: "handshake dropped", sni: "name filtered"};
const FD_KINDS = [["entry", "Entry"], ["cdn", "CDN"], ["direct", "Direct"], ["ai", "AI"], ["lab", "Lab"]];
const fdMore = () => innerHeight >= 1000 ? 8 : innerHeight >= 900 ? 6 : 4;          // (findings shown before "+N more": as many as the screen has room for, so nothing scrolls)

const fdN = (v, d = 1) => v == null ? "—" : String(+Number(v).toFixed(d));
const fdTier = m => m == null ? "" : m >= 25 ? "4K" : m >= 16 ? "4K light" : m >= 8 ? "1440p" : m >= 5 ? "1080p" : m >= 2.5 ? "720p" : "under 720p";
const fdText = t => String(t || "").replace(/^[^:()]+\([^)]*\):\s*/, "");          // ("Irancell (Tabriz): ..." - the network is the first column)
const fdAbbr = n => n.operator.length <= 4 ? n.operator : n.operator.slice(0, 3);
const fdNetName = n => `${n.operator} · ${n.city} · ${n.connection}`;

PAGES.field = async () => {
  renderSub();
  try {
    const [d, r] = await Promise.all([api("/api/field"), api("/api/field/runs?limit=20")]);
    fd.d = d; fd.runs = r.runs || []; fd.err = "";
  } catch (e) { fd.err = e.message; }
  fdDraw();
};

function fdDraw() {
  const box = $("#page"); if (!box) return;
  const html = fd.err ? `<div class="fd-app"><div class="fd-glass fd-empty"><b>${esc(fd.err)}</b></div></div>` : fdPage();
  if (html === fd.html) return;          // (nothing changed: nothing is redrawn - a redraw is what makes a page jump)
  fd.html = html;
  box.innerHTML = html;
}

function fdPage() {
  const d = fd.d;
  if (!d || !d.networks.length) return `<div class="fd-app"><div class="fd-glass fd-empty"><b>No runs yet</b><span>The Iran test kit sends its result here by itself.</span></div></div>`;
  return `<div class="fd-app" style="--n:${d.networks.length}">${fdBar(d)}<div class="fd-cards">${d.networks.map(fdCard).join("")}</div>${fdPatterns(d)}${fdFindings(d)}${fdGrid(d)}</div>`;
}

function fdBar(d) {
  const fs = d.networks.flatMap(n => n.findings), bad = fs.some(f => f.level === "bad"), tone = bad ? "bad" : fs.length ? "warn" : "ok";
  const newest = Math.min(...d.networks.map(n => n.age_s));
  return `<div class="fd-bar">
    <span class="fd-chip"><i class="fd-dot ok"></i><b>${d.networks.length}</b><small>network${d.networks.length === 1 ? "" : "s"}</small></span>
    <span class="fd-chip"><i class="fd-dot ${tone}"></i><b>${fs.length}</b><small>finding${fs.length === 1 ? "" : "s"}</small></span>
    <span class="fd-chip"><small>newest</small><b>${ago(Date.now() / 1000 - newest)}</b></span>
    <span class="fd-sp"></span>
    <span class="fd-leg"><i class="fd-dot ok"></i>works<i class="fd-dot warn"></i>partial<i class="fd-dot bad"></i>fails</span>
    <button class="fd-btn${fd.pop ? " on" : ""}" onclick="fdPop()">Runs <small>${fd.runs.length}</small></button>
    ${fd.pop ? fdRuns() : ""}
  </div>`;
}

function fdRuns() {
  return `<div class="fd-pop fd-glass">${fd.runs.map(r => `<div class="fd-run"><div class="fd-rr" onclick="fdOpen(${r.id})"><b>${esc(r.operator)}</b><span class="mut">${esc(r.city)} · ${esc(r.connection)}</span><span class="fd-sp"></span><span class="fd-num ${r.findings ? "warn" : "ok"}">${r.findings}</span><span class="mut" title="${esc(when(r.ts))}">${ago(r.ts)}</span><span class="fd-chev">${fd.open[r.id] ? "⌃" : "⌄"}</span></div>${fd.open[r.id] ? `<pre>${esc(fd.open[r.id])}</pre>` : ""}</div>`).join("") || `<div class="mut">No runs.</div>`}</div>`;
}

function fdCard(n) {
  const cls = n.findings.some(f => f.level === "bad") ? "bad" : n.findings.length ? "warn" : "ok";
  const dir = n.direct || [], names = n.panel.rows || [], addrs = Object.entries(n.panel.addresses || {});
  const fx = [];          // (what fails, named once, in one line under the numbers)
  const stat = (label, items, isOk, nameOf, tip) => {
    if (!items.length) return "";
    const ok = items.filter(isOk).length, fails = items.filter(x => !isOk(x));
    if (fails.length) fx.push(`${label}: ${fails.map(nameOf).join(", ")}`);
    return `<div class="fd-s ${ok === items.length ? "ok" : "bad"}" title="${esc(tip)}"><small>${label}</small><b>${ok}<i>/${items.length}</i></b></div>`;
  };
  return `<div class="fd-card fd-glass ${cls}" title="${esc(`${n.mode} run · kit ${n.kit || "?"}${n.manifest ? " · list " + n.manifest : ""} · ${when(n.ts)}`)}">
    <div class="fd-ch"><b>${esc(n.operator)}</b><span class="mut">${esc(n.city)} · ${esc(n.connection)}</span><span class="fd-sp"></span><span class="mut">${ago(n.ts)}</span></div>
    <div class="fd-cb">
      <div class="fd-big ${cls}"><b>${n.ok}</b><span>/${n.configs}</span><small>configs</small></div>
      <div class="fd-ss">
        ${stat("Direct", dir, x => x.code === "ok", x => `${(x.labels || []).slice(0, 2).join(", ")} ${FD_DIRECT[x.code] || x.code}`, dir.map(x => `${(x.labels || []).join(", ")}: ${x.words || FD_DIRECT[x.code] || x.code}`).join("\n"))}
        ${stat("Names", names, r => r.state === "ok", r => r.name, names.map(r => `${r.name}: ${r.why}`).join("\n"))}
        ${stat("Panel", addrs, ([, v]) => v === "ok", ([k]) => k, addrs.map(([k, v]) => `${k}: ${v === "ok" ? "answers on 443" : "does not answer on 443"}`).join("\n"))}
      </div>
    </div>
    ${fx.length ? `<div class="fd-fx" title="${esc(fx.join("\n"))}">${esc(fx.join(" · "))}</div>` : ""}
  </div>`;
}

/* what the networks have in common: a config that fails on EVERY network measured is the config's own problem (the server, the route, the exit); one that fails only on the networks of one city (or on one network) is a local filter. Needs two networks. */
function fdPatterns(d) {
  const nets = d.networks;
  if (nets.length < 2) return "";
  const cities = [...new Set(nets.map(n => n.city))], every = [], only = {};
  for (const l of d.labels) {
    const ms = nets.map(n => [n, (d.metrics[l] || {})[n.key]]).filter(([, m]) => m), bad = ms.filter(([, m]) => m.state !== "ok");
    if (ms.length < 2 || !bad.length) continue;
    if (bad.length === ms.length) { every.push(l); continue; }
    let where = "";
    if (cities.length > 1) {          // (a city where every network measured fails it, while another city does not)
      const bc = cities.filter(c => ms.filter(([n]) => n.city === c).length && ms.filter(([n]) => n.city === c).every(([, m]) => m.state !== "ok"));
      if (bc.length && bc.length < cities.length) where = bc.join(" + ");
    }
    if (!where && bad.length <= Math.floor(ms.length / 2)) where = bad.map(([n]) => n.operator).join(" + ");
    if (where) (only[where] = only[where] || []).push(l);
  }
  const chips = (ls, c) => ls.slice(0, 6).map(l => `<span class="fd-pc ${c}">${esc(l)}</span>`).join("") + (ls.length > 6 ? `<span class="fd-pc mut">+${ls.length - 6}</span>` : "");
  const parts = [];
  if (every.length) parts.push(`<span class="fd-pk">Fails on every network</span>${chips(every, "bad")}`);
  for (const [where, ls] of Object.entries(only)) parts.push(`<span class="fd-pk">Only on ${esc(where)}</span>${chips(ls, "warn")}`);
  return parts.length ? `<div class="fd-pat fd-glass">${parts.join("")}</div>` : "";
}

function fdFindings(d) {
  const rows = d.networks.flatMap(n => n.findings.map(f => ({n, f}))).sort((a, b) => (a.f.level === "bad" ? 0 : 1) - (b.f.level === "bad" ? 0 : 1));
  if (!rows.length) return "";
  const FD_MORE = fdMore();
  const shown = fd.all ? rows : rows.slice(0, FD_MORE);
  return `<div class="fd-fl fd-glass">${shown.map(({n, f}, i) => {
    const c = f.level === "bad" ? "bad" : "warn", t = fdText(f.text), on = fd.exp[i];
    return `<div class="fd-f ${c}" onclick="fdExp(${i})" title="${esc(t)}"><i class="fd-dot ${c}"></i><b>${esc(fdAbbr(n))}</b><span class="fd-ft">${esc(t)}</span>${f.hint ? `<span class="fd-chev">${on ? "⌃" : "⌄"}</span>` : ""}</div>${on && f.hint ? `<div class="fd-fh">${esc(f.hint)}</div>` : ""}`;
  }).join("")}${rows.length > FD_MORE ? `<div class="fd-more" onclick="fdAll()">${fd.all ? "Show fewer" : `+${rows.length - FD_MORE} more`}</div>` : ""}</div>`;          // (the +N link is the only way to a longer list)
}

function fdCellMini(label, n, m) {
  if (!m) return `<span class="fd-ce none" title="${esc(label + " · " + n.operator)}: not in this run">·</span>`;
  const [c, t] = FD_STATE[m.state] || ["warn", m.state];
  const tip = [`${label} · ${n.operator}`, t, `${m.sites[0]} of ${m.sites[1]} sites open`, m.lat ? `delay ${fdN(m.lat[0], 0)} ms, slowest ${fdN(m.lat[1], 0)} ms` : "", m.dl != null ? `quick download ${fdN(m.dl)} Mb/s` : "", m.bulk != null ? `long download ${fdN(m.bulk)} Mb/s${m.stalled ? " - STALLED" : ""}` : "",
    m.vid != null ? `video plays without stopping up to ${fdN(m.vid)} Mb/s (${fdTier(m.vid)})` : "", m.par != null ? `six pictures at once ${fdN(m.par)} Mb/s` : "", m.up != null ? `upload ${fdN(m.up)} Mb/s` : "", m.exit ? `exit ${m.exit}` : ""].filter(Boolean).join("\n");
  const txt = m.state === "ok" || m.state === "partial" || m.state === "slow" ? (m.lat ? fdN(m.lat[0], 0) : "") : m.state === "stall" ? "stall" : m.state === "closed" ? "×" : "off";
  return `<span class="fd-ce ${c}" title="${esc(tip)}"><i class="fd-dot ${c}"></i>${esc(txt)}</span>`;          // (a config that works shows its delay; what does not work shows why in one word)
}

function fdGrid(d) {
  if (!d.labels.length) return "";
  const kindOf = l => { const m = Object.values(d.metrics[l] || {})[0]; return m ? m.kind : ""; };
  const groups = FD_KINDS.map(([k, name]) => [name, d.labels.filter(l => kindOf(l) === k)]);
  const rest = d.labels.filter(l => !FD_KINDS.some(([k]) => k === kindOf(l)));
  if (rest.length) groups.push(["Other", rest]);
  const heads = d.networks.map(n => `<span class="fd-bn" title="${esc(fdNetName(n))}">${esc(fdAbbr(n))}</span>`).join("");
  const rows = ls => ls.map(l => `<div class="fd-row"><span class="fd-nm" title="${esc(l)}">${esc(l)}</span>${d.networks.map(n => fdCellMini(l, n, (d.metrics[l] || {})[n.key])).join("")}</div>`).join("");
  const big = groups.filter(([, ls]) => ls.length > 6), small = groups.filter(([, ls]) => ls.length && ls.length <= 6);
  return `<div class="fd-grid">${big.map(([name, ls]) => `<div class="fd-blk fd-glass"><div class="fd-bh"><span class="fd-bk">${name}</span>${heads}</div>${rows(ls)}</div>`).join("")}${small.length ? `<div class="fd-blk fd-glass"><div class="fd-bh"><span class="fd-bk"></span>${heads}</div>${small.map(([name, ls]) => `<div class="fd-sub">${name}</div>${rows(ls)}`).join("")}</div>` : ""}</div>`;          // (the long lists are a column each; the short ones - Direct, AI, Lab - share one column under one header, so the whole list is one row of columns)
}

window.fdPop = () => { fd.pop = !fd.pop; fd.html = ""; fdDraw(); };
window.fdAll = () => { fd.all = !fd.all; fd.html = ""; fdDraw(); };
window.fdExp = i => { fd.exp[i] = !fd.exp[i]; fd.html = ""; fdDraw(); };
window.fdOpen = async id => {
  if (fd.open[id]) { delete fd.open[id]; fd.html = ""; fdDraw(); return; }
  try { const r = await api(`/api/field/runs/${id}`); fd.open[id] = JSON.stringify({findings: r.findings, run: r.data}, null, 1); } catch (e) { fd.open[id] = "✕ " + e.message; }
  fd.html = ""; fdDraw();
};
