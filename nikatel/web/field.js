/* The Field test tab of Network (1.69.234; the data comes from services/field.py): what the Iran test kit saw from REAL networks - a SIM's hotspot, a home line - that no datacenter can show. Per network the newest run and
   what the Doctor found in it; a table of every config against every network with the numbers behind each cell (delay, speed, the video it plays); whether the IP, a port or the name of the direct configs is filtered;
   which subscription names and panel addresses open; and the list of the runs the kit sent. Routes: GET /api/field, GET /api/field/runs, GET /api/field/runs/{id}. Nothing is changed from here.
   Uses the helpers of app.js: $, api, esc, ago, when, renderSub, PAGES. */
const fd = {d: null, runs: [], html: "", open: {}, err: ""};
const FD_STATE = {ok: ["ok", "works"], partial: ["warn", "some sites cut"], slow: ["warn", "slow"], stall: ["bad", "stalls"], closed: ["bad", "opens nothing"], skipped: ["bad", "does not answer"]};
const FD_DIRECT = {ok: ["ok", "reachable"], ipblock: ["bad", "IP blocked"], allports: ["bad", "all ports filtered"], port: ["warn", "port dropped"], reset: ["warn", "port reset"], refused: ["warn", "server refuses"], tls: ["warn", "handshake dropped"], sni: ["warn", "name filtered"]};
const FD_LEVEL = {bad: ["bad", "serious"], warn: ["warn", "look at it"]};

const fdN = (v, d = 1) => v == null ? "—" : String(+Number(v).toFixed(d));
const fdNet = n => `${esc(n.operator)} <span class="mut">· ${esc(n.city)} · ${esc(n.connection)}</span>`;
const fdTier = m => m == null ? "" : m >= 25 ? "4K" : m >= 16 ? "4K light" : m >= 8 ? "1440p" : m >= 5 ? "1080p" : m >= 2.5 ? "720p" : "under 720p";
const fdAge = ts => ago(ts) + " ago";
const fdText = t => String(t || "").replace(/^[^:()]+\([^)]*\):\s*/, "");          // ("Irancell (Tabriz): ..." - the network is the first column)

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
  const html = fd.err ? `<div class="card"><div class="err">${esc(fd.err)}</div></div>` : fdPage();
  if (html === fd.html) return;          // (nothing changed: nothing is redrawn - a redraw is what makes a page jump)
  fd.html = html;
  box.innerHTML = html;
}

function fdPage() {
  const d = fd.d;
  if (!d || !d.networks.length) return `<div class="card fd-empty"><h3>No field test yet</h3>
    <p>The kit is a folder a person in Iran runs on a laptop, connected to the phone's hotspot: it measures, from THAT network, what the datacenters cannot see - which configs open Instagram and YouTube without lag, whether the IP of a direct config is filtered, whether the subscription names open - and sends the result here by itself.</p>
    <p class="mut">Nothing arrived yet. When it does, every network appears here as a card, with what the Doctor found, the table of every config against every network, and the runs.</p></div>`;
  return `${fdTiles(d)}<div class="fd-cards">${d.networks.map(fdCard).join("")}</div>${fdFindings(d)}${fdMatrix(d)}${fdPanel(d)}${fdRuns()}`;
}

function fdTiles(d) {
  const fs = d.networks.flatMap(n => n.findings), bad = fs.filter(f => f.level === "bad").length, warn = fs.length - bad;
  const best = Math.min(...d.networks.map(n => n.age_s));
  return `<div class="fd-tiles">
    <div class="fd-tile"><small>Networks measured</small><b>${d.networks.length}</b><span>the newest run of each, last 14 days</span></div>
    <div class="fd-tile ${bad ? "bad" : warn ? "warn" : "ok"}"><small>What the Doctor found</small><b>${fs.length}</b><span>${bad} serious · ${warn} to look at</span></div>
    <div class="fd-tile"><small>Newest run</small><b>${ago(Date.now() / 1000 - best)}</b><span>ago</span></div>
  </div>`;
}

function fdCard(n) {
  const cls = n.findings.some(f => f.level === "bad") ? "bad" : n.findings.length ? "warn" : "ok";
  const direct = n.direct.map(x => { const [c, t] = FD_DIRECT[x.code] || ["warn", x.code]; return `<span class="fd-pill ${c}" title="${esc(x.words)}">${esc((x.labels || []).slice(0, 2).join(", "))} · ${t}</span>`; }).join("");
  const names = (n.panel.rows || []).map(r => `<span class="fd-pill ${r.state === "ok" ? "ok" : "bad"}" title="${esc(r.why)}">${esc(r.name)}</span>`).join("");
  const addr = Object.entries(n.panel.addresses || {}).map(([k, v]) => `<span class="fd-pill ${v === "ok" ? "ok" : "bad"}" title="${v === "ok" ? "answers on 443" : "does not answer on 443"}">${esc(k)}</span>`).join("");
  return `<div class="fd-card ${cls}">
    <div class="fd-head"><b>${fdNet(n)}</b><span class="mut" title="${esc(when(n.ts))}">${fdAge(n.ts)}</span></div>
    <div class="fd-big"><b>${n.ok}</b> of ${n.configs} configs work <span class="mut">· ${n.findings.length} finding${n.findings.length === 1 ? "" : "s"}</span></div>
    ${direct ? `<div class="fd-row"><small>Direct configs</small><div>${direct}</div></div>` : ""}
    ${names ? `<div class="fd-row"><small>Subscription names</small><div>${names}</div></div>` : ""}
    ${addr ? `<div class="fd-row"><small>Panel addresses</small><div>${addr}</div></div>` : ""}
    <div class="fd-foot mut">${esc(n.mode)} run · kit ${esc(n.kit || "?")}${n.manifest ? " · list " + esc(n.manifest) : ""}</div>
  </div>`;
}

function fdFindings(d) {
  const rows = d.networks.flatMap(n => n.findings.map(f => ({n, f}))).sort((a, b) => (a.f.level === "bad" ? 0 : 1) - (b.f.level === "bad" ? 0 : 1));
  if (!rows.length) return `<div class="card"><h3 class="fd-h">What the Doctor found</h3><p class="mut">Nothing: every config passes on every network measured.</p></div>`;
  return `<div class="card"><h3 class="fd-h">What the Doctor found</h3><div class="fd-finds">
    ${rows.map(({n, f}) => { const [c, t] = FD_LEVEL[f.level] || ["warn", f.level]; return `<div class="fd-find ${c}"><div class="fd-fnet">${fdNet(n)}</div><div class="fd-fpill"><span class="fd-pill ${c}">${t}</span></div><div class="fd-ftext">${esc(fdText(f.text))}</div>${f.hint ? `<div class="fd-fhint mut">What would help: ${esc(f.hint)}</div>` : ""}</div>`; }).join("")}
  </div></div>`;          // (a list, not a table: two long sentences per row do not fit columns on a phone)
}

function fdCell(m) {
  if (!m) return `<td class="fd-c none">—</td>`;
  const [c, t] = FD_STATE[m.state] || ["warn", m.state];
  const tip = [t, `${m.sites[0]} of ${m.sites[1]} sites open`, m.lat ? `delay ${fdN(m.lat[0], 0)} ms, slowest ${fdN(m.lat[1], 0)} ms` : "", m.dl != null ? `quick download ${fdN(m.dl)} Mb/s` : "", m.bulk != null ? `long download ${fdN(m.bulk)} Mb/s${m.stalled ? " - STALLED" : ""}` : "",
    m.vid != null ? `video plays without stopping up to ${fdN(m.vid)} Mb/s (${fdTier(m.vid)})` : "", m.par != null ? `six pictures at once ${fdN(m.par)} Mb/s` : "", m.up != null ? `upload ${fdN(m.up)} Mb/s` : "", m.exit ? `exit ${m.exit}` : ""].filter(Boolean).join("\n");
  const sub = [m.lat ? `${fdN(m.lat[0], 0)} ms` : "", m.dl != null ? `${fdN(m.dl)} Mb/s` : "", m.vid != null ? fdTier(m.vid) : ""].filter(Boolean).join(" · ");
  return `<td class="fd-c ${c}" title="${esc(tip)}"><i class="fd-dot ${c}"></i>${m.state === "ok" ? "" : `<span>${esc(t)}</span>`}<small>${esc(sub)}</small></td>`;          // (a config that works shows only its numbers: what does not work stands out)
}

function fdMatrix(d) {
  if (!d.labels.length) return "";
  const head = d.networks.map(n => `<th data-prio="0" title="${esc(n.operator + " · " + n.city + " · " + n.connection)}">${esc(n.operator)}<small>${esc(n.city)}</small></th>`).join("");
  const kind = l => { const m = Object.values(d.metrics[l] || {})[0]; return m ? m.kind : ""; };
  return `<div class="card"><h3 class="fd-h">Every config on every network</h3>
    <p class="mut fd-note">Hover a cell for all its numbers. Delay is the median of twelve small requests; the video line is the highest bitrate that played without stopping.</p>
    <div class="fd-wrap"><table class="tbl fd-tbl fd-matrix"><thead><tr><th>Config</th>${head}</tr></thead><tbody>
    ${d.labels.map(l => `<tr><td class="name">${esc(l)} ${kind(l) ? `<small class="mut">${esc(kind(l))}</small>` : ""}</td>${d.networks.map(n => fdCell((d.metrics[l] || {})[n.key])).join("")}</tr>`).join("")}
    </tbody></table></div></div>`;
}

function fdPanel(d) {
  const names = [...new Set(d.networks.flatMap(n => (n.panel.rows || []).map(r => r.name)))];
  if (!names.length) return "";
  const cell = (n, name) => { const r = (n.panel.rows || []).find(x => x.name === name); return r ? `<td class="fd-c ${r.state === "ok" ? "ok" : "bad"}" title="${esc(r.why)}"><i class="fd-dot ${r.state === "ok" ? "ok" : "bad"}"></i><span>${r.state === "ok" ? "opens" : "fails"}</span><small>${esc(r.why)}</small></td>` : `<td class="fd-c none">—</td>`; };
  return `<div class="card"><h3 class="fd-h">Subscription names</h3>
    <p class="mut fd-note">The real subscription fetched from each name, the way a customer's app does: does the name resolve, connect, handshake and give the links? (The table of addresses is on each network's card.)</p>
    <div class="fd-wrap"><table class="tbl fd-tbl fd-matrix"><thead><tr><th>Name</th>${d.networks.map(n => `<th data-prio="0">${esc(n.operator)}<small>${esc(n.city)}</small></th>`).join("")}</tr></thead><tbody>
    ${names.map(nm => `<tr><td class="name">${esc(nm)}</td>${d.networks.map(n => cell(n, nm)).join("")}</tr>`).join("")}</tbody></table></div></div>`;
}

function fdRuns() {
  if (!fd.runs.length) return "";
  return `<div class="card"><h3 class="fd-h">The runs the kit sent</h3><div class="fd-wrap"><table class="tbl fd-tbl"><thead><tr><th>When</th><th data-prio="0">Network</th><th data-prio="3">Run</th><th data-prio="1">Findings</th><th data-prio="5">Sent by</th><th></th></tr></thead><tbody>
    ${fd.runs.map(r => `<tr><td class="mut" title="${esc(when(r.ts))}">${fdAge(r.ts)}</td><td class="name">${fdNet(r)}</td><td>${esc(r.mode)}</td><td class="num">${r.findings}</td><td class="mut">${esc(r.sender)}</td>
      <td><button class="sm" onclick="fdOpen(${r.id})">${fd.open[r.id] ? "Hide" : "Details"}</button></td></tr>
      ${fd.open[r.id] ? `<tr class="fd-detail"><td colspan="6"><pre>${esc(fd.open[r.id])}</pre></td></tr>` : ""}`).join("")}
  </tbody></table></div></div>`;
}

window.fdOpen = async id => {
  if (fd.open[id]) { delete fd.open[id]; fd.html = ""; fdDraw(); return; }
  try { const r = await api(`/api/field/runs/${id}`); fd.open[id] = JSON.stringify({findings: r.findings, run: r.data}, null, 1); } catch (e) { fd.open[id] = "✕ " + e.message; }
  fd.html = ""; fdDraw();
};
