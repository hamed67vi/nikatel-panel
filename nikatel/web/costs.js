/* The Finance page (the owner, 2026-10-31: "a system where I tell it the price of each server and each TB, and it tells me how much I spent this month" - "what each 1 GB costs me"; then "the price of each section in its own tab,
   ONE main page that writes the total of everything", "all the rates updated by themselves from chand" and "the whole section is called Finance, with two general subcategories: income, expenses").
   Finance has an Overview (the total of everything: income, expenses, profit, the price of one GB), Income (the shop bot, the partners) and Expenses (all of them, and a tab for each section: foreign servers, Iran servers,
   CDN, domains, the rest). You type the prices once (a server's rent, a price per TB, a domain per year, one invoice); the page adds them up for a month, divides by the traffic the nodes really carried and shows what
   one GB costs. The internal name of the page stays "costs" (its routes, its menu key).
   Routes used: GET /api/costs, GET /api/costs/summary?month=&recompute=, PUT /api/costs/settings, POST /api/costs/rates/fetch, POST|PUT|DELETE /api/costs/items[/id]. Nothing here changes a server or a panel.
   Uses the helpers of app.js: $, api, esc, toast, gconfirm, showModal, hideModal, refresh, PAGES. */
const cs = {d: null, s: null, month: "", top: "overview", sub: "all", isub: "bot"};
const CS_TOPS = [["overview", "Overview"], ["income", "Income"], ["expenses", "Expenses"]];
const CS_SECTIONS = [["all", "All expenses", null], ["foreign", "Foreign servers", ["foreign"]], ["iran", "Iran servers", ["iran"]], ["cdn", "CDN and traffic", ["cdn"]], ["domain", "Domains", ["domain"]], ["other", "Other", ["software", "other"]]];
const CS_ISECTIONS = [["bot", "Shop bot"], ["partners", "Partners"]];
const CS_KINDS = [["monthly", "Price per month"], ["yearly", "Price per year"], ["once", "One payment (an invoice, a fee)"], ["per_tb", "Price per TB of traffic"], ["per_gb", "Price per GB of traffic"]];
const CS_CUR = {IRT: "toman", USD: "USD", EUR: "EUR", TRY: "lira"};
const CS_SYM = {USD: "$", EUR: "€", TRY: "₺"};

const csN = (v, d = 0) => Number(v).toLocaleString("en-US", {maximumFractionDigits: d});
const csUsd = v => cs.s && cs.s.usd_rate && v != null ? ` <small class="cs-usd">≈ $${csN(v / cs.s.usd_rate, v / cs.s.usd_rate < 1 ? 4 : 2)}</small>` : "";
const csMoney = (v, per, plain) => v == null ? "—" : `${csN(v, per && Math.abs(v) < 100 ? 1 : 0)} <small>toman</small>${plain ? "" : csUsd(v)}`;      // (plain: no dollars beside it - the tables are narrow)
const csShort = v => Math.abs(v) >= 1e9 ? csN(v / 1e9, 2) + " B" : Math.abs(v) >= 1e6 ? csN(v / 1e6, 1) + " M" : Math.abs(v) >= 1e3 ? csN(v / 1e3, 0) + " K" : csN(v);      // 56.6 M: the amount on a tab
const csGb = gb => gb >= 1024 ? `${csN(gb / 1024, 2)} TB` : `${csN(gb, 1)} GB`;
const csShift = (key, n) => { const [y, m] = key.split("-").map(Number), i = y * 12 + (m - 1) + n; return String(Math.floor(i / 12)).padStart(4, "0") + "-" + String(i % 12 + 1).padStart(2, "0"); };
const csAgo = t => { const m = Math.round((Date.now() / 1000 - t) / 60); return m < 1 ? "just now" : m < 90 ? `${m} min ago` : `${Math.round(m / 60)} h ago`; };
const csErr = e => toast("✕ " + e.message, "bad");
const csTaxed = it => it.amount * (1 + (it.tax || 0) / 100);                                  // (the tax is in every price the page shows: it is not written, it is just counted)
const csAmt = (v, cur) => `${csN(v, v < 100 ? 2 : 0)} ${CS_CUR[cur] || cur}`;
const csPrice = it => {
  const unit = {monthly: "/month", yearly: "/year", once: it.months > 1 ? ` over ${it.months} months` : "", per_tb: "/TB", per_gb: "/GB"}[it.kind] || "";
  const free = it.included ? ` · first ${csN(it.included, 2)} ${it.kind === "per_tb" ? "TB" : "GB"} free` : "";
  const dirs = it.kind === "per_tb" || it.kind === "per_gb" ? (it.dirs === "both" ? " · both ways" : " · sent only") : "";
  const quota = it.quota ? ` · ${csN(it.quota, 2)} TB in the plan` : "";
  return `${csAmt(csTaxed(it), it.cur)}${unit}${free}${dirs}${quota}`;
};
const csGroupSum = (s, keys) => keys.reduce((a, k) => { const g = s.by_group[k] || {so_far: 0, month: 0}; return {so_far: a.so_far + g.so_far, month: a.month + g.month}; }, {so_far: 0, month: 0});
const csSectionOf = group => (CS_SECTIONS.find(t => t[2] && t[2].includes(group)) || CS_SECTIONS[0])[0];
const csTile = (label, value, sub, cls = "", vcls = "") => `<div class="cs-tile ${cls}"><small>${label}</small><b class="${vcls}">${value}</b><span>${sub}</span></div>`;      // (vcls: a class of the figure itself - a loss is red)

PAGES.costs = async () => {
  const [d, s] = await Promise.all([api("/api/costs"), api("/api/costs/summary" + (cs.month ? "?month=" + encodeURIComponent(cs.month) : ""))]);
  cs.d = d; cs.s = s; csDraw();
};

/* ---- the bar: the month, the calendar, the rates of the day (read from chand.online by themselves) */
function csRatesStatus(d) {
  const age = d.rates_checked ? Date.now() / 1000 - d.rates_checked : null;
  if (!d.rates_auto) return {cls: "off", text: "Typed by you", sub: "automatic reading is off"};
  if (d.rates_error) return {cls: "warn", text: "chand.online did not answer", sub: `the rates of ${d.rates_at || "before"} stay`};
  if (age == null) return {cls: "warn", text: "Waiting for the first read", sub: "a few minutes"};
  if (d.rates_stale || age > 5400) return {cls: "warn", text: "Live · the prices are old", sub: `checked ${csAgo(d.rates_checked)}`};
  return {cls: "ok", text: "Live from chand.online", sub: `checked ${csAgo(d.rates_checked)}${d.rates_at ? ` · price of ${d.rates_at}` : ""}`};
}
const csPct = p => p ? `<em class="${p > 0 ? "up" : "dn"}">${p > 0 ? "▲" : "▼"} ${csN(Math.abs(p), 2)}%</em>` : "";
function csToday(d, s) {                                                                       // today in both calendars: the one the month is counted in first
  if (!s.today_j) return "";
  const g = d.calendar === "gregorian", a = g ? s.today_g : s.today_j, b = g ? s.today_j : s.today_g;
  return `<div class="cs-today" title="Today, in both calendars"><small>Today · ${esc(s.weekday || "")}</small><b>${esc(a)}</b><small>${esc(b)}</small></div>`;
}
function csBar(d, s) {
  const isNow = s.month === s.current, st = csRatesStatus(d), info = d.rates_info || {};
  const chip = code => { const v = d.rates[code], i = info[code];
    return `<div class="cs-chip" title="1 ${code} in toman${i && i.at ? " · the price of " + esc(i.at) : ""}. A higher rate makes what you pay in ${code} cost more toman."><span class="cs-sym">${CS_SYM[code]}</span><b>${v ? csN(v) : "—"}</b><small>${code}</small>${csPct(i && i.pct)}</div>`; };
  const cross = d.rates.USD && d.rates.TRY ? d.rates.USD / d.rates.TRY : 0;                      // lira for one dollar = (toman of a dollar) / (toman of a lira)
  const pu = info.USD && info.USD.pct, pt = info.TRY && info.TRY.pct;
  const crossPct = typeof pu === "number" && typeof pt === "number" ? ((1 + pu / 100) / (1 + pt / 100) - 1) * 100 : 0;
  const crossChip = cross ? `<div class="cs-chip" title="How many lira one dollar buys: the dollar in toman divided by the lira in toman. A higher number is a weaker lira."><span class="cs-sym">$ = ₺</span><b>${csN(cross, 2)}</b><small>USD/TRY</small>${csPct(crossPct).replace(/class="(up|dn)"/, 'class="flat"')}</div>` : "";
  const other = d.calendar === "gregorian" ? s.span_j : s.span_g;                                // the days of this month in the other calendar
  return `<div class="card cs-bar"><div class="cs-nav"><button class="sm" onclick="csMonth(-1)" aria-label="The month before">◀</button><span class="cs-mon"><b>${esc(s.label)}</b>${other ? `<small title="The same days in the ${d.calendar === "gregorian" ? "Jalali" : "Gregorian"} calendar">${esc(other)}</small>` : ""}</span><button class="sm" onclick="csMonth(1)" aria-label="The month after" ${isNow ? "disabled" : ""}>▶</button>`
    + `${isNow ? "" : '<button class="sm" onclick="csOpen(\'\')">This month</button>'}${!s.open && s.frozen ? '<button class="sm" onclick="csRecompute()" title="Make this month again from the prices as they are now">Recalculate</button>' : ""}</div>`
    + `<div class="cs-seg" role="group" aria-label="The calendar of a month"><button class="${d.calendar === "jalali" ? "on" : ""}" onclick="csSetCal('jalali')">Jalali</button><button class="${d.calendar === "gregorian" ? "on" : ""}" onclick="csSetCal('gregorian')">Gregorian</button></div>`
    + csToday(d, s)
    + `<span class="cs-fill"></span><div class="cs-rates">${chip("USD")}${chip("EUR")}${chip("TRY")}${crossChip}<div class="cs-live"><i class="cs-dot ${st.cls}"></i><span><b>${esc(st.text)}</b><small>${esc(st.sub)}</small></span></div>`
    + `<button class="sm cs-ic" onclick="csFetchRates()" title="Read today's rates from chand.online now" aria-label="Read the rates now">↻</button><button class="sm cs-ic" onclick="csSettings()" title="Rates, the shop bot" aria-label="Settings">⚙</button></div></div>`;
}

/* ---- the three parts of Finance, and the tabs inside Income and Expenses */
function csTopTabs(s) {
  const badge = k => k === "income" ? (s.income ? csShort(s.income.month) : "") : k === "expenses" ? csShort(s.total) : "";
  return `<div class="cs-top" role="tablist">${CS_TOPS.map(([k, name]) => `<button role="tab" class="${cs.top === k ? "on" : ""}" onclick="csTop('${k}')">${esc(name)}${badge(k) ? `<small>${badge(k)}</small>` : ""}</button>`).join("")}</div>`;
}
function csSubTabs(list, cur, fn, s) {
  return `<div class="cs-tabs" role="tablist">${list.map(([k, name, keys]) => {
    const g = keys && s ? csGroupSum(s, keys) : null;
    return `<button role="tab" class="${cur === k ? "on" : ""}" onclick="${fn}('${k}')">${esc(name)}${g && g.month ? `<small>${csShort(g.month)}</small>` : ""}</button>`;
  }).join("")}</div>`;
}

/* ---- the tiles */
function csExpTiles(s) {
  return `<div class="cs-tiles">`
    + csTile("Expenses so far", csMoney(s.so_far), `${s.elapsed} of ${s.days} days${s.frozen ? " · saved" : ""}`)
    + csTile(s.open ? "Expenses, the whole month" : "Expenses of the month", csMoney(s.total), s.open ? "if the traffic goes on like this" : "final")
    + csTile("Traffic of the customers", s.traffic_ok ? csGb(s.gb_est != null ? s.gb_est : s.gb) : "—", s.traffic_ok && s.coverage != null && s.coverage < 1 ? `${csGb(s.gb)} recorded, the first days of the month counted at the average` : s.traffic_ok && s.open && s.gb ? `about ${csGb(s.gb_month)} in the month` : "counted by the panel")
    + csTile("One GB costs you", csMoney(s.per_gb, true), s.per_gb_month != null && s.open ? `${csN(s.per_gb_month, s.per_gb_month < 100 ? 1 : 0)} toman at the end of the month` : "so far this month", "cs-big") + `</div>`;
}
function csFinTiles(s) {
  const inc = s.income; if (!inc) return csExpTiles(s);
  const neg = v => v < 0 ? "cs-neg" : "", gbT = s.traffic_ok ? csGb(s.gb_est != null ? s.gb_est : s.gb) : "—";
  return `<div class="cs-tiles">`
    + csTile("Income so far", csMoney(inc.so_far), `shop bot · ${csN(inc.bot.count)} payments · ${csN(inc.bot.payers)} payers`)
    + csTile("Expenses so far", csMoney(s.so_far), `${s.elapsed} of ${s.days} days${s.frozen ? " · saved" : ""}`)
    + csTile("Profit so far", csMoney(inc.profit_so_far), "before the partners' income", "", neg(inc.profit_so_far))
    + csTile("Traffic of the customers", gbT, s.traffic_ok && s.coverage != null && s.coverage < 1 ? `${csGb(s.gb)} recorded, the first days counted at the average` : s.open && s.gb ? `about ${csGb(s.gb_month)} in the month` : "counted by the panel") + `</div>`
    + `<div class="cs-tiles">`
    + csTile(s.open ? "Income, the whole month" : "Income of the month", csMoney(inc.month), s.open ? "at this pace" : "final")
    + csTile(s.open ? "Expenses, the whole month" : "Expenses of the month", csMoney(s.total), s.open ? "if the traffic goes on like this" : "final")
    + csTile(s.open ? "Profit, the whole month" : "Profit of the month", csMoney(inc.profit_month), s.open ? "at this pace" : "final", "", neg(inc.profit_month))
    + csTile("Profit of one GB", csMoney(inc.profit_per_gb, true), `it costs you ${csN(s.per_gb || 0, (s.per_gb || 0) < 100 ? 1 : 0)} toman, before the partners' income`, "cs-big", inc.profit_per_gb != null && inc.profit_per_gb < 0 ? "cs-neg" : "") + `</div>`;
}
const csWarn = s => s.warnings.concat((s.errors || []).map(x => "Panel: " + x)).map(w => `<div class="cs-warn">${esc(w)}</div>`).join("");
const IK = [["buy", "New services"], ["renew", "Renewals"], ["wallet", "Wallet top-ups"], ["other", "Extra volume and days"]];
const csIncRows = inc => IK.map(([k, nm]) => `<div class="cs-gr"><span>${nm} <small class="mut">${csN(inc.bot.by_kind[k].n)}</small></span><i style="--w:${inc.bot.count && inc.so_far ? Math.round(inc.bot.by_kind[k].sum * 100 / inc.so_far) : 0}%"></i><b>${csMoney(inc.bot.by_kind[k].sum, false, true)}</b></div>`).join("");

/* ---- Overview: the page that writes the total of everything */
function csExpRows(d, s, click) {
  const groups = d.groups.map(g => ({key: g.key, name: g.name, v: s.by_group[g.key] || {so_far: 0, month: 0}})).filter(g => g.v.month || g.v.so_far);
  const maxG = Math.max(1, ...groups.map(g => g.v.month));
  if (!groups.length) return '<div class="mut">Nothing is counted in this month yet. Open Expenses and press “＋ Add a price”: the rent of a server, a price per TB, a domain or an invoice.</div>';
  return groups.map(g => `<${click ? `a class="cs-gr cs-link" onclick="csGo('expenses', '${csSectionOf(g.key)}')" title="Open its tab · so far ${csN(g.v.so_far)} toman"` : 'div class="cs-gr"'}><span>${esc(g.name)} <small class="mut">${s.total ? csN(g.v.month * 100 / s.total, 1) : 0}%</small></span><i style="--w:${Math.round(g.v.month * 100 / maxG)}%"></i><b>${csMoney(g.v.month)}</b></${click ? "a" : "div"}>`).join("")
    + `<div class="cs-gr cs-total"><span>All expenses</span><i></i><b>${csMoney(s.total)}</b></div><div class="cs-gr cs-sub"><span>so far in the month</span><i></i><b>${csMoney(s.so_far)}</b></div>`;
}
function csOverview(d, s) {
  const inc = s.income;
  const left = `<div class="card"><h3>Where the money goes<span class="sp"></span><small class="mut">expenses of the whole month · click a line</small></h3>${csExpRows(d, s, true)}</div>`;
  const right = inc ? `<div class="card"><h3>Where the money comes from<span class="sp"></span><small class="mut">the shop bot · so far</small></h3>${csIncRows(inc)}<div class="cs-gr cs-total"><span>Income so far</span><i></i><b>${csMoney(inc.so_far)}</b></div></div>`
    : `<div class="card"><h3>Income</h3><div class="mut">${d.income && d.income.bot_server_id ? "The shop bot did not answer just now." : "Choose the server of the shop bot in the settings (⚙) and the page adds its sales and the profit."}</div></div>`;
  const hist = d.history.length ? d.history.map(h => `<a class="cs-h" onclick="csOpen('${h.month}')"><span>${esc(h.label)}</span><b>${csMoney(h.total)}</b><small>${csGb(h.gb)}</small><em>${h.per_gb != null ? csMoney(h.per_gb, true) + " / GB" : ""}</em></a>`).join("") : '<div class="mut">A finished month is saved here the first time you look at it.</div>';
  return csFinTiles(s) + csWarn(s) + `<div class="cs-two">${left}${right}</div>` + `<div class="card"><h3>Earlier months<span class="sp"></span><small class="mut">click a month to open it</small></h3><div class="cs-hist">${hist}</div></div>`;
}

/* ---- Expenses: all of them, and a tab for each section of the cost */
function csItemsTable(d, s, keys, pick) {
  const list = s.items.filter(r => keys.includes(r.group) && (!pick || pick(r))), many = keys.length > 1;
  if (!list.length) return '<div class="mut">No prices here yet. Press “＋ Add a price”.</div>';
  const rows = list.map(r => {
    const it = d.items.find(x => x.id === r.id) || r;
    const q = r.quota ? `<small class="cs-note ${r.quota.pct >= 100 ? "cs-over" : r.quota.pct >= 90 ? "cs-near" : ""}" title="The traffic the node carries this month at this pace, against the traffic in its plan">uses ${r.quota.pct}% of ${csN(r.quota.tb, 2)} TB</small>` : "";
    return `<tr class="${r.state === "idle" ? "cs-idle" : ""}"><td><b>${esc(r.name)}</b>${it.note ? `<small class="cs-note">${esc(it.note)}</small>` : ""}${q}</td>${many ? `<td>${esc((d.groups.find(g => g.key === r.group) || {}).name || r.group)}</td>` : ""}`
      + `<td>${esc(csPrice(it))}</td><td>${r.server || r.node ? `${esc(r.server)}${r.node && r.node !== r.server ? `${r.server ? " <small>→</small> " : ""}<small>${esc(r.node === "*" ? "all nodes" : r.node)}</small>` : ""}` : "—"}</td>`
      + `<td class="r">${r.state === "no_rate" ? '<span class="pill err" title="The rate of this currency is not there yet">no rate</span>' : r.state === "needs_node" ? '<span class="pill warn" title="Choose the node whose traffic is priced">choose a node</span>' : r.state === "idle" ? '<span class="mut">not active</span>' : csMoney(r.so_far, false, true)}</td>`
      + `<td class="r">${r.state === "ok" ? csMoney(r.month, false, true) : ""}</td><td class="r"><span class="cs-act"><button class="sm" onclick="csEdit(${r.id})" title="Change this price">Edit</button><button class="sm danger" onclick="csDel(${r.id})" title="Remove this price">✕</button></span></td></tr>`;
  }).join("");
  return `<table class="tbl cs-items"><thead><tr><th>What</th>${many ? "<th>Group</th>" : ""}<th>Price</th><th>Server</th><th class="r">So far</th><th class="r">Month</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
}
const csNodesTable = s => s.by_node.length ? `<div class="cs-scroll"><table class="tbl"><thead><tr><th>Node</th><th class="r">Traffic</th><th class="r">Cost so far</th><th class="r">1 GB</th></tr></thead><tbody>${s.by_node.map(n => `<tr><td><b>${esc(n.name)}</b></td><td class="r">${csGb(n.gb)}</td><td class="r">${n.priced ? csMoney(n.so_far, false, true) : '<span class="cs-none" title="No price is tied to this node yet: add one and choose this server or node">no price yet</span>'}</td><td class="r">${n.per_gb != null && n.priced ? csMoney(n.per_gb, true, true) : "—"}</td></tr>`).join("")}</tbody></table></div>` : '<div class="mut">No traffic was recorded in this month.</div>';
const csServersTable = s => (s.by_server || []).length ? `<div class="cs-scroll"><table class="tbl"><thead><tr><th>Server</th><th class="r">Sent</th><th class="r">Received</th><th class="r">Cost so far</th><th class="r">1 GB sent</th></tr></thead><tbody>${s.by_server.map(n => `<tr><td><b>${esc(n.name)}</b></td><td class="r">${csGb(n.out_gb)}</td><td class="r">${csGb(n.in_gb)}</td><td class="r">${n.priced ? csMoney(n.so_far, false, true) : '<span class="cs-none" title="No price is tied to this server yet">no price yet</span>'}</td><td class="r">${n.per_gb != null ? csMoney(n.per_gb, true, true) : "—"}</td></tr>`).join("")}</tbody></table></div>` : '<div class="mut">No Iran server has samples yet.</div>';
/* a card for each server: its rent, its price per TB, and what the month cost (the traffic it carried + the rent) */
const csIsTraffic = r => r.kind === "per_tb" || r.kind === "per_gb";
const csSum = (rows, f) => rows.reduce((a, r) => a + (r[f] || 0), 0);
function csCardRows(s, keys) {                                      // -> {cards: [[server name, its rows]], rest: the rows that belong to no server}, the dearest server first
  const list = s.items.filter(r => keys.includes(r.group)), alias = {}, by = new Map(), rest = [];
  list.forEach(r => { if (r.server && r.node && r.node !== "*") alias[r.node.toLowerCase()] = r.server; });          // (a price tied to the node of a server goes on that server's card)
  list.forEach(r => {
    const name = r.server || (r.node && r.node !== "*" ? alias[r.node.toLowerCase()] || r.node : "");
    if (!name) { rest.push(r); return; }
    const k = name.toLowerCase(); if (!by.has(k)) by.set(k, [name, []]);
    by.get(k)[1].push(r);
  });
  return {cards: [...by.values()].sort((a, b) => csSum(b[1], "month") - csSum(a[1], "month")), rest};
}
function csCard(d, s, name, rows) {
  const rent = rows.filter(r => !csIsTraffic(r)), tr = rows.filter(csIsTraffic), f = s.open && s.elapsed ? s.days / s.elapsed : 1;
  const itOf = r => d.items.find(x => x.id === r.id) || r;
  const acts = r => `<span class="cs-act"><button class="sm" onclick="csEdit(${r.id})" title="Change this price">Edit</button><button class="sm danger" onclick="csDel(${r.id})" title="Remove this price">✕</button></span>`;
  const toman = (it, k = 1) => it.cur === "IRT" || !d.rates[it.cur] ? "" : ` <small class="cs-usd">≈ ${csN(csTaxed(it) * k * d.rates[it.cur])} toman</small>`;
  const tip = it => `${csAmt(it.amount, it.cur)}${it.tax ? ` + ${csN(it.tax, 2)}% tax` : ""}`;                  // (what was typed, only as a tooltip)
  const mark = r => r.state === "no_rate" ? ' <span class="pill err" title="The rate of this currency is not there yet">no rate</span>' : r.state === "needs_node" ? ' <span class="pill warn" title="Choose the node whose traffic is priced">choose a node</span>' : r.state === "idle" ? ' <span class="mut">not active</span>' : "";
  const rentHtml = rent.length ? rent.map(r => { const it = itOf(r);
    return `<div class="cs-ln"><span><b title="${esc(tip(it))}">${esc(csPrice(it))}</b>${toman(it)}${rent.length > 1 ? `<small class="cs-note">${esc(r.name)}</small>` : ""}${it.note ? `<small class="cs-note">${esc(it.note)}</small>` : ""}${r.quota ? `<small class="cs-note ${r.quota.pct >= 100 ? "cs-over" : r.quota.pct >= 90 ? "cs-near" : ""}">uses ${r.quota.pct}% of ${csN(r.quota.tb, 2)} TB</small>` : ""}${mark(r)}</span>${acts(r)}</div>`; }).join("") : '<div class="mut">No rent price yet</div>';
  const trHtml = tr.length ? tr.map(r => { const it = itOf(r), k = it.kind === "per_gb" ? 1000 : 1;
    return `<div class="cs-ln"><span><b title="${esc(tip(it))}">${esc(csAmt(csTaxed(it) * k, it.cur))} / TB</b>${toman(it, k)}<small class="cs-note">${it.dirs === "both" ? "sent and received" : "sent only"}${it.included ? ` · first ${csN(it.included, 2)} ${it.kind === "per_tb" ? "TB" : "GB"} free` : ""}</small>${mark(r)}</span>${acts(r)}</div>`; }).join("") : '<div class="mut">Not priced by traffic</div>';
  const names = [name, ...rows.map(r => r.node)].map(x => String(x || "").toLowerCase()), sr = (s.by_server || []).find(x => names.includes(x.name.toLowerCase())), nr = (s.by_node || []).find(x => names.includes(x.name.toLowerCase()));
  const gb = tr.length ? Math.max(0, ...tr.map(r => r.gb || 0)) : nr ? nr.gb : null;                  // (a server nobody prices by traffic still shows what its node carried)
  const t = {sf: csSum(tr, "so_far"), mo: csSum(tr, "month")}, r0 = {sf: csSum(rent, "so_far"), mo: csSum(rent, "month")}, all = {sf: t.sf + r0.sf, mo: t.mo + r0.mo};
  const line = (label, a, b, cls = "") => `<tr class="${cls}"><td>${label}</td><td class="r">${a}</td><td class="r">${b}</td></tr>`;
  const month = `<table class="cs-mini"><thead><tr><th></th><th class="r">So far</th><th class="r">${s.open ? "Whole month" : "Month"}</th></tr></thead><tbody>`
    + line("Traffic used", gb == null ? "—" : csGb(gb), gb == null ? "—" : csGb(gb * f))
    + line("Traffic cost", csN(t.sf), csN(t.mo)) + line("Rent", csN(r0.sf), csN(r0.mo)) + line("Total · toman", csN(all.sf), csN(all.mo), "cs-sum") + `</tbody></table>`
    + (sr ? `<small class="cs-note">sent ${csGb(sr.out_gb)} · received ${csGb(sr.in_gb)} (estimated)</small>` : "");
  return `<div class="card cs-srv"><div class="cs-srv-h"><h3>${esc(name)}</h3><span class="cs-srv-t"><b>${csMoney(all.mo)}</b><small>${s.open ? "the whole month" : "the month"}</small></span></div>`
    + `<div class="cs-srv-g"><div class="cs-blk"><small>Server</small>${rentHtml}</div><div class="cs-blk"><small>Price per TB of traffic</small>${trHtml}</div><div class="cs-blk"><small>Traffic and cost</small>${month}</div></div></div>`;
}
const CS_HINT = {cdn: "Type each invoice or monthly price of your CDN here (a plan per domain, the traffic it bills).", domain: "A domain is a price per year: the page counts a twelfth of it every month.", other: "Services, salaries, the unexpected: anything that is paid every month or once."};
function csSection(d, s, sec) {
  const [k, name, keys] = sec, g = csGroupSum(s, keys), share = s.total ? g.month * 100 / s.total : 0;
  const gbPrice = s.traffic_ok && s.gb_est > 0 ? g.so_far / s.gb_est : null;
  const tiles = `<div class="cs-tiles">`
    + csTile(`${esc(name)} · so far`, csMoney(g.so_far), `${s.elapsed} of ${s.days} days`)
    + csTile(`${esc(name)} · ${s.open ? "the whole month" : "the month"}`, csMoney(g.month), s.open ? "if the traffic goes on like this" : "final")
    + csTile("Share of all expenses", `${csN(share, 1)}%`, `of ${csShort(s.total)} toman`)
    + csTile("Of the price of one GB", csMoney(gbPrice, true), "what this section adds to one GB so far", "cs-big") + `</div>`;
  const add = `<button class="pri sm" onclick="csEdit(0, '${keys[0]}')">＋ Add a price</button>`;

  if (k === "foreign" || k === "iran") {                                       // a card for each server; what has no server (and the traffic nobody prices) under them
    const {cards, rest} = csCardRows(s, keys);
    const none = k === "foreign" ? s.by_node.filter(n => !n.priced) : (s.by_server || []).filter(n => !n.priced);
    const head = `<div class="card cs-addbar"><h3>${esc(name)}<span class="sp"></span><small class="mut">${k === "iran" ? "the traffic is an estimate from the panel's samples of the last days" : "a card for each server"}</small>${add}</h3></div>`;
    return tiles + csWarn(s) + head + (cards.length ? `<div class="cs-cards">${cards.map(([n, rows]) => csCard(d, s, n, rows)).join("")}</div>` : '<div class="card mut">No prices here yet. Press “＋ Add a price”: the rent of a server, then its price per TB.</div>')
      + (rest.length ? `<div class="card"><h3>Other prices · ${esc(name)}</h3>${csItemsTable(d, s, keys, r => rest.includes(r))}</div>` : "")
      + (none.length ? `<div class="card"><h3>Traffic with no price yet<span class="sp"></span><small class="mut">add the rent or the price per TB of these servers</small></h3>${k === "foreign" ? csNodesTable({by_node: none}) : csServersTable({by_server: none})}</div>` : "");
  }

  return tiles + csWarn(s) + `<div class="card"><h3>Your prices · ${esc(name)}<span class="sp"></span>${add}</h3>${CS_HINT[k] ? `<div class="mut cs-sec-hint">${CS_HINT[k]}</div>` : ""}${csItemsTable(d, s, keys)}</div>`;
}
function csExpenses(d, s) {
  const sec = CS_SECTIONS.find(t => t[0] === cs.sub) || CS_SECTIONS[0];
  const head = csSubTabs(CS_SECTIONS, sec[0], "csSub", s);
  if (sec[0] !== "all") return head + csSection(d, s, sec);
  const all = d.groups.map(g => g.key);
  return head + csExpTiles(s) + csWarn(s) + `<div class="card"><h3>Where the money goes<span class="sp"></span><small class="mut">the whole month · click a line for its tab</small></h3>${csExpRows(d, s, true)}</div>`
    + `<div class="card"><h3>All your prices<span class="sp"></span><button class="pri sm" onclick="csEdit(0, 'foreign')">＋ Add a price</button></h3>${csItemsTable(d, s, all)}</div>`;
}

/* ---- Income: the shop bot and the partners */
function csIncome(d, s) {
  const head = csSubTabs(CS_ISECTIONS, cs.isub, "csISub", null);
  if (cs.isub === "partners") return head + `<div class="card"><h3>Partners</h3><div class="mut">The income from your partners' panel is not in yet. Tell the panel how they pay you - a price for each GB they use, or a fixed amount a month - and it will be here and in the profit.</div></div>`;
  const inc = s.income;
  if (!inc) return head + `<div class="card"><h3>Shop bot</h3><div class="mut">${d.income && d.income.bot_server_id ? "The shop bot did not answer just now (see the warning below)." : "Choose the server of the shop bot in the settings (⚙) and the page adds its sales and the profit."}</div>${d.income && d.income.bot_server_id ? "" : '<div class="cs-btns"><button class="pri sm" onclick="csSettings()">Open the settings</button></div>'}</div>` + csWarn(s);
  return head + `<div class="cs-tiles">`
    + csTile("Income so far", csMoney(inc.so_far), `${csN(inc.bot.count)} payments · ${csN(inc.bot.payers)} payers`)
    + csTile(s.open ? "Income, the whole month" : "Income of the month", csMoney(inc.month), s.open ? "at this pace" : "final")
    + csTile("Per payment", csMoney(inc.bot.count ? inc.so_far / inc.bot.count : null), "the average of the payments so far")
    + csTile("Per payer", csMoney(inc.bot.payers ? inc.so_far / inc.bot.payers : null), "what one customer paid so far", "cs-big") + `</div>`
    + csWarn(s) + `<div class="card"><h3>Income of the shop bot<span class="sp"></span><small class="mut">payments the bot approved in the days of the month</small></h3>${csIncRows(inc)}<div class="cs-gr cs-total"><span>Income so far</span><i></i><b>${csMoney(inc.so_far)}</b></div></div>`;
}

function csDraw() {
  const d = cs.d, s = cs.s, page = $("#page"); if (!page || state.page !== "costs") return;
  const body = cs.top === "income" ? csIncome(d, s) : cs.top === "expenses" ? csExpenses(d, s) : csOverview(d, s);
  page.innerHTML = `<div class="costs">${csBar(d, s)}${csTopTabs(s)}${body}</div>`;
}

const csTop0 = () => { const m = $(".main"); if (m) m.scrollTop = 0; };
window.csTop = k => { cs.top = k; csDraw(); csTop0(); };
window.csSub = k => { cs.sub = k; csDraw(); };
window.csISub = k => { cs.isub = k; csDraw(); };
window.csGo = (top, sub) => { cs.top = top; if (sub) cs.sub = sub; csDraw(); csTop0(); };
window.csMonth = n => { cs.month = csShift(cs.s.month, n); refresh(); };
window.csOpen = key => { cs.month = key; cs.top = "overview"; refresh(); };
window.csRecompute = async () => {
  try { cs.s = await api("/api/costs/summary?month=" + encodeURIComponent(cs.s.month) + "&recompute=true"); toast("✓ Calculated again", "ok"); csDraw(); } catch (e) { csErr(e); }
};
window.csSetCal = async cal => {
  if (cs.d.calendar === cal) return;
  document.querySelectorAll(".cs-seg button").forEach(b => b.classList.toggle("on", b.textContent.trim().toLowerCase() === cal));          // (at once: the month is read again, it can take a few seconds)
  const bar = $(".cs-bar"); if (bar) bar.classList.add("cs-wait");
  try { await api("/api/costs/settings", {method: "PUT", body: JSON.stringify({calendar: cal})}); cs.month = ""; refresh(); } catch (e) { if (bar) bar.classList.remove("cs-wait"); csDraw(); csErr(e); }
};
window.csFetchRates = async () => {
  try { const r = await api("/api/costs/rates/fetch", {method: "POST"}); toast(`✓ 1 USD = ${csN(r.rates.USD)} · 1 EUR = ${csN(r.rates.EUR)} · 1 lira = ${csN(r.rates.TRY)} toman`, "ok"); refresh(); } catch (e) { csErr(e); }
};

/* ---- the settings: the rates (by themselves, or typed) and the server of the shop bot */
window.csSettings = () => {
  const d = cs.d, auto = d.rates_auto;
  const num = (name, label) => `<label class="cs-f"><span>${label}</span><input name="${name}" type="number" min="0" step="any" value="${esc(d.rates[name] || "")}" placeholder="toman" ${auto ? "disabled" : ""}></label>`;
  const body = `<form id="cs-set" class="cs-form" onsubmit="return csSaveSettings(event)">`
    + `<label class="cs-f cs-wide cs-check"><span>Read the rates by themselves</span><input type="checkbox" name="rates_auto" ${auto ? "checked" : ""} onchange="csAuto()"></label>`
    + `<div class="cs-help cs-wide">The panel reads the dollar, the euro and the lira from chand.online every 30 minutes and keeps the day's change. Switch it off to type the rates yourself.</div>`
    + num("USD", "1 USD = (toman)") + num("EUR", "1 EUR = (toman)") + num("TRY", "1 lira = (toman)")
    + `<label class="cs-f"><span>Shop bot server</span><select name="bot_server_id" title="The panel reads its sales from there through a restricted key (counts and sums only)"><option value="0">— not set —</option>${d.servers.map(x => `<option value="${x.id}" ${(d.income && d.income.bot_server_id) === x.id ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></label>`
    + `<div class="cs-btns"><button class="pri" type="submit">Save</button><button type="button" onclick="hideModal()">Cancel</button></div></form>`;
  showModal("<b>Rates and shop bot</b>", body);
};
window.csAuto = () => { const f = $("#cs-set"); if (f) ["USD", "EUR", "TRY"].forEach(k => { f[k].disabled = f.rates_auto.checked; }); };
window.csSaveSettings = async e => {
  e.preventDefault(); const f = e.target, auto = f.rates_auto.checked, o = {rates_auto: auto, bot_server_id: +f.bot_server_id.value || 0};
  if (!auto) o.rates = {USD: +f.USD.value || 0, EUR: +f.EUR.value || 0, TRY: +f.TRY.value || 0};
  try { await api("/api/costs/settings", {method: "PUT", body: JSON.stringify(o)}); hideModal(); toast("✓ Saved", "ok"); refresh(); } catch (err) { csErr(err); }
  return false;
};

/* ---- one price */
window.csEdit = (id, group) => {
  const d = cs.d, it = id ? d.items.find(x => x.id === id) : {kind: "monthly", cur: "IRT", group: group || "foreign", months: 1, included: 0};
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
