/* The Costs page (the owner, 2026-10-31: "a system where I tell it the price of each server and each TB, and it tells me how much I spent this month" - "what each 1 GB costs me").
   You type the prices once (a server's rent, a price per TB, a domain per year, one invoice); the page adds them up for a month, divides by the traffic the nodes really carried and shows what one GB costs.
   Routes used: GET /api/costs, GET /api/costs/summary?month=&recompute=, PUT /api/costs/settings, POST|PUT|DELETE /api/costs/items[/id]. Nothing here changes a server or a panel.
   Uses the helpers of app.js: $, api, esc, toast, gconfirm, showModal, hideModal, refresh, PAGES. */
const cs = {d: null, s: null, month: ""};
const CS_KINDS = [["monthly", "Price per month"], ["yearly", "Price per year"], ["once", "One payment (an invoice, a fee)"], ["per_tb", "Price per TB of traffic"], ["per_gb", "Price per GB of traffic"]];
const CS_CUR = {IRT: "toman", USD: "USD", EUR: "EUR", TRY: "lira"};

const csN = (v, d = 0) => Number(v).toLocaleString("en-US", {maximumFractionDigits: d});
const csUsd = v => cs.s && cs.s.usd_rate && v != null ? ` <small class="cs-usd">≈ $${csN(v / cs.s.usd_rate, v / cs.s.usd_rate < 1 ? 4 : 2)}</small>` : "";
const csMoney = (v, per, plain) => v == null ? "—" : `${csN(v, per && v < 100 ? 1 : 0)} <small>toman</small>${plain ? "" : csUsd(v)}`;      // (plain: no dollars beside it - the tables are narrow)
const csGb = gb => gb >= 1024 ? `${csN(gb / 1024, 2)} TB` : `${csN(gb, 1)} GB`;
const csShift = (key, n) => { const [y, m] = key.split("-").map(Number), i = y * 12 + (m - 1) + n; return String(Math.floor(i / 12)).padStart(4, "0") + "-" + String(i % 12 + 1).padStart(2, "0"); };
const csPrice = it => {
  const unit = {monthly: "/month", yearly: "/year", once: it.months > 1 ? ` over ${it.months} months` : "", per_tb: "/TB", per_gb: "/GB"}[it.kind] || "";
  const free = it.included ? ` · first ${csN(it.included, 2)} ${it.kind === "per_tb" ? "TB" : "GB"} free` : "";
  const tax = it.tax ? ` + ${csN(it.tax, 2)}% tax` : "";
  const dirs = it.kind === "per_tb" || it.kind === "per_gb" ? (it.dirs === "both" ? " · both ways" : " · sent only") : "";
  const quota = it.quota ? ` · ${csN(it.quota, 2)} TB in the plan` : "";
  return `${csN(it.amount, it.amount < 100 ? 2 : 0)} ${CS_CUR[it.cur] || it.cur}${tax}${unit}${free}${dirs}${quota}`;
};
const csErr = e => toast("✕ " + e.message, "bad");

PAGES.costs = async () => {
  const [d, s] = await Promise.all([api("/api/costs"), api("/api/costs/summary" + (cs.month ? "?month=" + encodeURIComponent(cs.month) : ""))]);
  cs.d = d; cs.s = s; csDraw();
};

function csDraw() {
  const d = cs.d, s = cs.s, page = $("#page"); if (!page || state.page !== "costs") return;
  const tiles = `<div class="cs-tiles">`
    + `<div class="cs-tile"><small>Spent so far</small><b>${csMoney(s.so_far)}</b><span>${s.elapsed} of ${s.days} days${s.frozen ? " · saved" : ""}</span></div>`
    + `<div class="cs-tile"><small>${s.open ? "The whole month" : "The month"}</small><b>${csMoney(s.total)}</b><span>${s.open ? "if the traffic goes on like this" : "final"}</span></div>`
    + `<div class="cs-tile"><small>Traffic of the customers</small><b>${s.traffic_ok ? csGb(s.gb_est != null ? s.gb_est : s.gb) : "—"}</b><span>${s.traffic_ok && s.coverage != null && s.coverage < 1 ? `${csGb(s.gb)} recorded, the first days of the month counted at the average` : s.traffic_ok && s.open && s.gb ? `about ${csGb(s.gb_month)} in the month` : "counted by the panel"}</span></div>`
    + `<div class="cs-tile cs-big"><small>One GB costs you</small><b>${csMoney(s.per_gb, true)}</b><span>${s.per_gb_month != null && s.open ? `${csN(s.per_gb_month, s.per_gb_month < 100 ? 1 : 0)} toman at the end of the month` : "so far this month"}</span></div></div>`;
  const inc = s.income, IK = [["buy", "New services"], ["renew", "Renewals"], ["wallet", "Wallet top-ups"], ["other", "Extra volume and days"]];
  const incTiles = inc ? `<div class="cs-tiles">`
    + `<div class="cs-tile"><small>Income so far (shop bot)</small><b>${csMoney(inc.so_far)}</b><span>${csN(inc.bot.count)} payments · ${csN(inc.bot.payers)} payers</span></div>`
    + `<div class="cs-tile"><small>${s.open ? "Income, the whole month" : "Income of the month"}</small><b>${csMoney(inc.month)}</b><span>${s.open ? "at this pace" : "final"}</span></div>`
    + `<div class="cs-tile"><small>Profit so far</small><b class="${inc.profit_so_far < 0 ? "cs-neg" : ""}">${csMoney(inc.profit_so_far)}</b><span>the bot's income less the cost - the partners' income is not in yet</span></div>`
    + `<div class="cs-tile cs-big"><small>Profit of one GB</small><b class="${inc.profit_per_gb != null && inc.profit_per_gb < 0 ? "cs-neg" : ""}">${csMoney(inc.profit_per_gb, true)}</b><span>so far, before the partners' income</span></div></div>`
    + `<div class="card"><h3>Income of the shop bot<span class="sp"></span><small class="mut">payments the bot approved, by the day of the month</small></h3>${IK.map(([k, name]) => `<div class="cs-gr"><span>${name} <small class="mut">${csN(inc.bot.by_kind[k].n)}</small></span><i style="--w:${inc.bot.count && inc.so_far ? Math.round(inc.bot.by_kind[k].sum * 100 / inc.so_far) : 0}%"></i><b>${csMoney(inc.bot.by_kind[k].sum, false, true)}</b></div>`).join("")}</div>` : (d.income && d.income.bot_server_id ? "" : '<div class="cs-hint">Income: choose the server of the shop bot in the bar above (Shop bot) and the page adds its sales and the profit.</div>');
  const warn = s.warnings.concat((s.errors || []).map(x => "Panel: " + x)).map(w => `<div class="cs-warn">${esc(w)}</div>`).join("");
  const maxG = Math.max(1, ...Object.values(s.by_group).map(g => g.month));
  const groups = Object.values(s.by_group).filter(g => g.month || g.so_far).map(g => `<div class="cs-gr"><span>${esc(g.name)}</span><i style="--w:${Math.round(g.month * 100 / maxG)}%"></i><b>${csMoney(g.month)}</b></div>`).join("") || '<div class="mut">Nothing is counted in this month yet.</div>';
  const nodes = s.by_node.length ? `<div class="cs-scroll"><table class="tbl"><thead><tr><th>Node</th><th class="r">Traffic</th><th class="r">Cost so far</th><th class="r">1 GB</th></tr></thead><tbody>${s.by_node.map(n => `<tr><td><b>${esc(n.name)}</b></td><td class="r">${csGb(n.gb)}</td><td class="r">${n.priced ? csMoney(n.so_far, false, true) : '<span class="cs-none" title="No price is tied to this node yet: add one and choose this server or node">no price yet</span>'}</td><td class="r">${n.per_gb != null && n.priced ? csMoney(n.per_gb, true, true) : "—"}</td></tr>`).join("")}</tbody></table></div>` : '<div class="mut">No traffic was recorded in this month.</div>';
  const servers = (s.by_server || []).length ? `<div class="cs-scroll"><table class="tbl"><thead><tr><th>Server</th><th class="r">Sent</th><th class="r">Received</th><th class="r">Cost so far</th><th class="r">1 GB sent</th></tr></thead><tbody>${s.by_server.map(n => `<tr><td><b>${esc(n.name)}</b></td><td class="r">${csGb(n.out_gb)}</td><td class="r">${csGb(n.in_gb)}</td><td class="r">${n.priced ? csMoney(n.so_far, false, true) : '<span class="cs-none" title="No price is tied to this server yet">no price yet</span>'}</td><td class="r">${n.per_gb != null ? csMoney(n.per_gb, true, true) : "—"}</td></tr>`).join("")}</tbody></table></div>` : "";
  const rows = s.items.length ? s.items.map(r => {
    const it = d.items.find(x => x.id === r.id) || r;
    const q = r.quota ? `<small class="cs-note ${r.quota.pct >= 100 ? "cs-over" : r.quota.pct >= 90 ? "cs-near" : ""}" title="The traffic the node carries this month at this pace, against the traffic in its plan">uses ${r.quota.pct}% of ${csN(r.quota.tb, 2)} TB</small>` : "";
    return `<tr class="${r.state === "idle" ? "cs-idle" : ""}"><td><b>${esc(r.name)}</b>${it.note ? `<small class="cs-note">${esc(it.note)}</small>` : ""}${q}</td><td>${esc((d.groups.find(g => g.key === r.group) || {}).name || r.group)}</td>`
      + `<td>${esc(csPrice(it))}</td><td>${r.server || r.node ? `${esc(r.server)}${r.node && r.node !== r.server ? `${r.server ? " <small>→</small> " : ""}<small>${esc(r.node === "*" ? "all nodes" : r.node)}</small>` : ""}` : "—"}</td>`
      + `<td class="r">${r.state === "no_rate" ? '<span class="pill err" title="Type the rate of this currency above">no rate</span>' : r.state === "needs_node" ? '<span class="pill warn" title="Choose the node whose traffic is priced">choose a node</span>' : r.state === "idle" ? '<span class="mut">not active</span>' : csMoney(r.so_far, false, true)}</td>`
      + `<td class="r">${r.state === "ok" ? csMoney(r.month, false, true) : ""}</td><td class="r"><span class="cs-act"><button class="sm" onclick="csEdit(${r.id})" title="Change this price">Edit</button><button class="sm danger" onclick="csDel(${r.id})" title="Remove this price">✕</button></span></td></tr>`;
  }).join("") : "";
  const prices = rows ? `<table class="tbl cs-items"><thead><tr><th>What</th><th>Group</th><th>Price</th><th>Server</th><th class="r">So far</th><th class="r">Month</th><th></th></tr></thead><tbody>${rows}</tbody></table>`
    : '<div class="mut">No prices yet. Press “＋ Add a price” and type the rent of a server, a price per TB, a domain or an invoice - the page adds them up and divides by the traffic.</div>';
  const hist = d.history.length ? d.history.map(h => `<a class="cs-h" onclick="csOpen('${h.month}')"><span>${esc(h.label)}</span><b>${csMoney(h.total)}</b><small>${csGb(h.gb)}</small><em>${h.per_gb != null ? csMoney(h.per_gb, true) + " / GB" : ""}</em></a>`).join("") : '<div class="mut">A finished month is saved here the first time you look at it.</div>';
  const isNow = s.month === s.current;
  page.innerHTML = `<div class="costs">`
    + `<div class="card cs-bar"><div class="cs-month"><button class="sm" onclick="csMonth(-1)" aria-label="The month before">◀</button><b>${esc(s.label)}</b><button class="sm" onclick="csMonth(1)" aria-label="The month after" ${isNow ? "disabled" : ""}>▶</button>${isNow ? "" : '<button class="sm" onclick="csOpen(\'\')">This month</button>'}${!s.open && s.frozen ? '<button class="sm" onclick="csRecompute()" title="Make this month again from the prices as they are now">Recalculate</button>' : ""}</div>`
    + `<form class="cs-set" onsubmit="return csSaveSettings(event)"><label>1 USD = <input name="USD" type="number" min="0" step="any" value="${esc(d.rates.USD || "")}" placeholder="toman"></label><label>1 EUR = <input name="EUR" type="number" min="0" step="any" value="${esc(d.rates.EUR || "")}" placeholder="toman"></label><label>1 lira = <input name="TRY" type="number" min="0" step="any" value="${esc(d.rates.TRY || "")}" placeholder="toman"></label>`
    + `<label>Shop bot <select name="bot_server_id" title="The server that runs the shop bot: the panel reads its sales from there (a restricted key, counts and sums only)"><option value="0">— not set —</option>${d.servers.map(x => `<option value="${x.id}" ${(d.income && d.income.bot_server_id) === x.id ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></label>`
    + `<label>Month <select name="calendar"><option value="jalali" ${d.calendar === "jalali" ? "selected" : ""}>Jalali</option><option value="gregorian" ${d.calendar === "gregorian" ? "selected" : ""}>Gregorian</option></select></label><button class="sm" type="submit">Save</button>`
    + `<button class="sm" type="button" onclick="csFetchRates()" title="Read today's dollar, euro and lira from chand.online and save them as the rates">Today's rates</button>${d.rates_at ? `<small class="cs-usd" title="The time the site gave with its prices">from chand.online · ${esc(d.rates_at)}</small>` : ""}</form></div>`
    + tiles + warn + incTiles
    + `<div class="cs-two"><div class="card"><h3>Where it goes</h3>${groups}</div><div class="card"><h3>Each node</h3>${nodes}</div></div>`
    + (servers ? `<div class="card"><h3>Each Iran server<span class="sp"></span><small class="mut">the traffic is an estimate from the panel's samples of the last days</small></h3>${servers}</div>` : "")
    + `<div class="card"><h3>Your prices<span class="sp"></span><button class="pri sm" onclick="csEdit(0)">＋ Add a price</button></h3>${prices}</div>`
    + `<div class="card"><h3>Earlier months</h3><div class="cs-hist">${hist}</div></div></div>`;
}

window.csMonth = n => { cs.month = csShift(cs.s.month, n); refresh(); };
window.csOpen = key => { cs.month = key; refresh(); };
window.csRecompute = async () => {
  try { cs.s = await api("/api/costs/summary?month=" + encodeURIComponent(cs.s.month) + "&recompute=true"); toast("✓ Calculated again", "ok"); csDraw(); } catch (e) { csErr(e); }
};
window.csSaveSettings = async e => {
  e.preventDefault(); const f = new FormData(e.target), cal = f.get("calendar");
  try { await api("/api/costs/settings", {method: "PUT", body: JSON.stringify({rates: {USD: +f.get("USD") || 0, EUR: +f.get("EUR") || 0, TRY: +f.get("TRY") || 0}, calendar: cal, bot_server_id: +f.get("bot_server_id") || 0})}); if (cal !== cs.d.calendar) cs.month = ""; toast("✓ Saved", "ok"); refresh(); } catch (err) { csErr(err); }
  return false;
};

window.csFetchRates = async () => {
  try { const r = await api("/api/costs/rates/fetch", {method: "POST"}); toast(`✓ 1 USD = ${csN(r.rates.USD)} · 1 EUR = ${csN(r.rates.EUR)} · 1 lira = ${csN(r.rates.TRY)} toman`, "ok"); refresh(); } catch (e) { csErr(e); }
};

window.csEdit = id => {
  const d = cs.d, it = id ? d.items.find(x => x.id === id) : {kind: "monthly", cur: "IRT", group: "foreign", months: 1, included: 0};
  if (!it) return;
  const opt = (list, cur) => list.map(([v, l]) => `<option value="${esc(v)}" ${String(cur) === String(v) ? "selected" : ""}>${esc(l)}</option>`).join("");
  const field = (label, inner, show = "") => `<label class="cs-f" ${show ? `data-for="${show}"` : ""}><span>${label}</span>${inner}</label>`;
  const body = `<form id="cs-form" class="cs-form" onsubmit="return csSave(event, ${id})">`
    + field("Name", `<input name="name" maxlength="60" required value="${esc(it.name || "")}" placeholder="a server's rent, a domain, an invoice ...">`)
    + field("Group", `<select name="group">${opt(d.groups.map(g => [g.key, g.name]), it.group)}</select>`)
    + field("Kind of price", `<select name="kind" onchange="csKind()">${opt(CS_KINDS, it.kind)}</select>`)
    + field("Price", `<input name="amount" type="number" min="0" step="any" required value="${esc(it.amount == null ? "" : it.amount)}">`)
    + field("Currency", `<select name="cur">${opt(Object.entries(CS_CUR), it.cur)}</select>`)
    + field("Tax (VAT) on top, %", `<input name="tax" type="number" min="0" max="100" step="any" value="${esc(it.tax || 0)}" title="0 when the price already has its tax; 10 when the tax is added on top">`)
    + field("Server of the list", `<select name="server_id" onchange="csPickServer()">${opt([[0, "— none —"]].concat(d.servers.map(s => [s.id, s.name + (s.role === "iran" ? " (Iran)" : "")])), it.server_id || 0)}</select>`)
    + field("Node (its traffic and cost)", `<select name="node">${opt([["", "— none —"], ["*", "All nodes together"]].concat(d.nodes.map(n => [n, n])), it.node || "")}</select>`)
    + field("Free traffic in the plan (<span id=\"cs-unit\">TB</span> a month)", `<input name="included" type="number" min="0" step="any" value="${esc(it.included || 0)}">`, "per_tb per_gb")
    + field("Traffic that counts", `<select name="dirs">${opt([["out", "What it sends (the download)"], ["both", "Sent and received"]], it.dirs || "out")}</select>`, "per_tb per_gb monthly yearly")
    + field("Traffic in the plan (TB a month)", `<input name="quota" type="number" min="0" step="any" value="${esc(it.quota || 0)}" title="0 when the plan has no limit; the page then shows how much of it the node uses">`, "monthly yearly")
    + field("Spread over (months)", `<input name="months" type="number" min="1" max="120" value="${esc(it.months || 1)}">`, "once")
    + field('<span id="cs-from">From</span> <small>(optional)</small>', `<input name="starts" maxlength="10" value="${esc(it.starts || "")}" placeholder="2026-10-23 or 1405/08/01">`)
    + field("Until <small>(optional)</small>", `<input name="ends" maxlength="10" value="${esc(it.ends || "")}" placeholder="when you stop paying">`, "monthly yearly per_tb per_gb")
    + field("Note", `<input name="note" maxlength="200" value="${esc(it.note || "")}" placeholder="plan, provider, renewal day ...">`)
    + `<div class="cs-help" id="cs-help"></div><div class="cs-btns"><button class="pri" type="submit">Save</button><button type="button" onclick="hideModal()">Cancel</button></div></form>`;
  showModal(`<b>${id ? "Change a price" : "＋ Add a price"}</b>`, body);
  csKind();
};
const CS_HELP = {monthly: "Counted by the day: a server paid from the 10th is counted for the days it was there.", yearly: "A twelfth of it every month.", once: "Paid on the day typed in “From”, counted in that month and the next ones when spread.",
  per_tb: "Price × the TB the chosen server or node carried (1 TB = 1000 GB), after the free TB. Choose the server or node.", per_gb: "Price × the GB the chosen server or node carried, after the free GB. Choose the server or node (or all nodes)."};
window.csKind = () => {
  const f = $("#cs-form"); if (!f) return; const k = f.kind.value;
  f.querySelectorAll("[data-for]").forEach(el => { el.hidden = !el.dataset.for.split(" ").includes(k); });
  const u = $("#cs-unit"), from = $("#cs-from"), help = $("#cs-help"); if (u) u.textContent = k === "per_gb" ? "GB" : "TB"; if (from) from.textContent = k === "once" ? "Paid on" : "From"; if (help) help.textContent = CS_HELP[k] || "";
};
window.csPickServer = () => {      // a server whose name is also the name of a node takes that node (TR2 is both)
  const f = $("#cs-form"); if (!f || f.node.value) return;
  const s = cs.d.servers.find(x => String(x.id) === f.server_id.value), n = s && cs.d.nodes.find(x => x.toLowerCase() === s.name.toLowerCase());
  if (n) f.node.value = n;
};
window.csSave = async (e, id) => {
  e.preventDefault(); const f = new FormData(e.target), o = {};
  for (const [k, v] of f.entries()) o[k] = v;
  o.amount = +o.amount; o.tax = +o.tax || 0; o.server_id = +o.server_id || 0; o.months = +o.months || 1; o.included = +o.included || 0; o.quota = +o.quota || 0;
  try { await api(id ? "/api/costs/items/" + id : "/api/costs/items", {method: id ? "PUT" : "POST", body: JSON.stringify(o)}); hideModal(); toast("✓ Saved", "ok"); refresh(); } catch (err) { csErr(err); }
  return false;
};
window.csDel = async id => {
  const it = cs.d.items.find(x => x.id === id); if (!it) return;
  if (!await gconfirm(`Remove “${it.name}”? The months already saved keep their numbers.`)) return;
  try { await api("/api/costs/items/" + id, {method: "DELETE"}); toast("✓ Removed", "ok"); refresh(); } catch (err) { csErr(err); }
};
