/* The L2TP tab of Network (1.69.241; the data comes from services/l2tp.py): the VPN account (L2TP over IPsec, no app on the phone) the shop sells. The entry (an Iran server) holds the accounts, the exit (a foreign server) lets the traffic out,
   a protected tunnel joins them. One bar (entry, exit, tunnel, accounts, online), one table of accounts (cards on a phone), and the work of an account in windows of their own, never over the table: New account, Edit, Details (the message to send a customer)
   and Servers (the gear). Routes: GET /api/l2tp (?fresh=1 = read it all now, ?live=1 = the light read of an open tab: only the entry), GET and PUT /api/l2tp/config, POST /api/l2tp/accounts, POST .../renew, PUT, DELETE and POST .../kick on /api/l2tp/accounts/{name}, GET .../profile;
   the shop side (1.69.245): GET /api/l2tp/shop, PUT .../shop/stage, POST /api/l2tp/plans, PUT and DELETE .../plans/{id}, PUT .../plans/{id}/active;
   backup and restore (1.69.248; the same routes as the Backups card, part "l2tp"): GET /api/settings/backup, POST .../settings/backup/part/l2tp, POST .../backup/now?part=l2tp, POST and DELETE .../backup/topic/l2tp, POST .../backup/restore and .../backup/restore-upload (mode: missing | all), POST .../settings/backup/passphrase.
   Uses the helpers of app.js: $, api, esc, ago, when, renderSub, PAGES, state, toast, showModal, hideModal. */
const l2 = {d: null, err: "", html: "", cfg: null, e: null, q: "", view: "", timer: 0, asked: false, fast: 0, at: 0, shop: null, tab: "plans", vis: false};
const L2_NAME = /^[a-z][a-z0-9_]{2,23}$/;
const L2_BRAND = "nklt_";          // (the owner, 2026-11-07: the name of every account starts with nklt_ - first it was nikatelshop_; the shop bot names its accounts the same way; the accounts made before keep their names)
const l2B = n => { n = +n || 0; return n >= 1e9 ? (n / 1e9).toFixed(2) + " GB" : n >= 1e6 ? (n / 1e6).toFixed(1) + " MB" : n >= 1e3 ? Math.round(n / 1e3) + " KB" : n + " B"; };
const l2Mb = v => v ? (+v) + " Mbit/s" : "—";
const l2Gb = q => q ? (+(q / 1e9).toFixed(2)) + " GB" : "";
const l2Date = ts => ts ? new Date(ts * 1000).toISOString().slice(0, 10) : "—";
const l2Day = ts => ts ? new Date(ts * 1000).toISOString().slice(0, 10) : "";          // (the value of a date field)
const l2DayEnd = s => Math.floor(Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10), 23, 59, 59) / 1000) - 12600;          // (the end of that day in Tehran, +03:30)
const l2Dot = ok => ok === true ? "ok" : ok === false ? "bad" : "warn";
const l2V = id => { const e = document.getElementById(id); return e ? String(e.value).trim() : ""; };
const l2Open = () => !!document.querySelector("#modal .modal");
const l2Here = () => state.page === "tunnels" && !!state.sub && state.sub.tunnels === "l2tp";          // (is this tab still the one on screen: an answer that comes late never draws over another page)
const L2_KEEP = "nk.l2tp.last";
const l2Last = () => { try { return JSON.parse(sessionStorage.getItem(L2_KEEP) || "null"); } catch (e) { return null; } };
const l2Keep = d => { try { sessionStorage.setItem(L2_KEEP, JSON.stringify(d)); } catch (e) { /* no storage (a private window): the tab still works, it only opens without the last answer */ } };
const L2_ICO = {          // (the line icons of the buttons, drawn in the colour of the text)
  out: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>`,
  bin: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>`,
  gear: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>`,
  find: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><line x1="20" y1="20" x2="16.5" y2="16.5"/></svg>`,
  chev: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>`,
};

PAGES.l2tp = async () => {          // (the tab is drawn AT ONCE - with the last answer if there is one - and the entry is read behind it: it is read over SSH and can take seconds; the owner, 2026-11-07: "it loads very late, the menu must open in a moment", and once the spinner never ended)
  renderSub();
  l2.err = "";
  if (!l2.d) l2.d = l2Last();
  l2Draw();
  l2Pull(true);
  clearInterval(l2.timer);
  l2.timer = setInterval(() => {          // (while the tab is open and no window is: the page follows the entry by itself - the light read, only the entry, a few seconds after the last one; right after a Disconnect it follows closely, so the light is seen to go out and to come back when the phone connects again)
    if (!l2Here()) { clearInterval(l2.timer); l2.timer = 0; return; }
    if (l2Open() || document.hidden) return;
    if (Date.now() - l2.at < (Date.now() < l2.fast ? 900 : 4500)) return;
    l2Pull(false, true);
  }, 1000);
  if (!l2.vis) { l2.vis = true; document.addEventListener("visibilitychange", () => { if (!document.hidden && l2Here() && !l2Open()) l2Pull(false, true); }); }          // (the owner comes back to the window: it is read at once, never an old picture)
};

async function l2Load(fresh, live) {
  try { l2.d = await api("/api/l2tp" + (fresh ? "?fresh=1" : live ? "?live=1" : "")); l2.err = ""; l2Keep(l2.d); } catch (e) { l2.err = e.message; }
}

async function l2Pull(mark, live) {          // (a read behind the drawn page; `mark` = the small "reading" chip while it waits - not on the rounds, a redraw there would take the cursor out of the search box)
  if (l2.pulling) return;
  l2.pulling = true; l2.busy = !!mark;
  if (mark && l2Here()) l2Draw();
  try { await l2Load(false, live); } finally { l2.pulling = false; l2.busy = false; l2.at = Date.now(); }
  if (l2Here()) l2Draw();          // (only if this tab is still the open one: a late answer never draws over another page)
}

function l2Draw() {
  const box = $("#page"); if (!box) return;
  const html = l2Page();
  if (html !== l2.html) {          // (nothing changed: nothing is redrawn - a redraw is what makes a page jump)
    const a = document.activeElement, typing = a && a.classList && a.classList.contains("l2-q") ? a.selectionStart : -1;
    l2.html = html; box.innerHTML = html;
    if (typing >= 0) { const n = $(".l2-q"); if (n) { n.focus(); n.setSelectionRange(typing, typing); } }          // (the figures move while a device is online: the person who is typing in the search box keeps the cursor)
  }
  if (l2.d && !l2.d.configured && !l2.asked && !l2Open()) { l2.asked = true; l2ConfOpen(); }          // (nothing chosen yet: the servers window opens by itself, once)
}

function l2Page() {
  const d = l2.d;
  if (!d && l2.err) return `<div class="l2-app"><div class="l2-glass l2-empty"><b>${esc(l2.err)}</b><button class="l2-btn on" onclick="l2Refresh()">Try again</button></div></div>`;
  if (!d) return `<div class="l2-app"><div class="l2-glass l2-empty"><b>Reading the entry…</b><span>It takes a few seconds the first time.</span></div></div>`;
  return `<div class="l2-app">${l2Bar(d)}${l2Notes(d)}${d.configured ? l2Table(d) : `<div class="l2-glass l2-empty"><b>Choose the servers first</b><span>Pick the Iran server that is the entry and the foreign server that is the exit.</span><button class="l2-btn on" onclick="l2ConfOpen()">Choose servers</button></div>`}</div>`;
}

function l2Bar(d) {
  const en = d.entry, ex = d.exit, tu = d.tunnel;
  const chip = (label, ok, text, tip) => `<span class="l2-chip" title="${esc(tip || "")}"><i class="l2-dot ${l2Dot(ok)}"></i><small>${label}</small><b>${esc(text)}</b></span>`;
  return `<div class="l2-bar l2-glass"><div class="l2-stat">
    ${en ? chip("Entry", en.ok, en.ok ? `${en.units && en.units.strongswan === "active" ? "up" : "check"}${en.load != null ? " · load " + en.load : ""}` : "not ready", en.error || `IPsec ${en.units ? en.units.strongswan : "?"}, L2TP ${en.units ? en.units.xl2tpd : "?"}, timer ${en.units ? en.units.tick : "?"}, pool ${en.pool || "?"}`) : ""}
    ${d.exit ? chip("Exit", ex.ok, ex.ok ? "ready" : "not ready", ex.error || `${ex.rules} rules, routes ${(ex.routes || []).join(" ")}`) : ""}
    ${tu ? chip("Tunnel", tu.state === "up" ? true : tu.state === "down" ? false : null, `${tu.name}${tu.rtt_ms != null ? " · " + Math.round(tu.rtt_ms) + " ms" : ""}${tu.protected ? " · protected" : ""}`, tu.error || "the tunnel between the entry and the exit") : ""}
    ${d.configured ? `<span class="l2-chip l2-cnt"><small>accounts</small><b>${d.accounts.length}</b></span><span class="l2-chip"><small>online</small><b>${d.online}</b></span>` : ""}
    ${l2.busy ? `<span class="l2-chip l2-busy" title="The entry is being read over SSH; what is shown is the last answer"><i class="l2-dot warn"></i><small>reading…</small></span>` : ""}
  </div><div class="l2-tools">
    ${d.configured ? `<label class="l2-search" title="Search by name or note">${L2_ICO.find}<input class="l2-q" placeholder="Search" value="${esc(l2.q)}" oninput="l2.q = this.value; l2Redraw()"></label>` : ""}
    ${d.configured ? l2Filters(d) : ""}
    ${d.configured ? `<span class="l2-seg l2-tb"><button type="button" title="Back up the accounts (sent to the Telegram group every hour) and restore them" onclick="l2BkOpen()">Backup</button><button type="button" title="The plans the Mini App sells, who can buy, and the sales" onclick="l2ShopOpen()">Plans &amp; sales</button></span>` : ""}
    ${d.configured ? `<button type="button" class="l2-btn on l2-pri" onclick="l2NewOpen()">＋ New account</button>` : ""}
    <button type="button" class="l2-ico" title="The entry and the exit, the connection name" aria-label="Servers" onclick="l2ConfOpen()">${L2_ICO.gear}</button>
  </div></div>`;
}

function l2Notes(d) {
  const n = [];
  if (l2.err) n.push(["warn", `The entry could not be read just now (${l2.err}); this is the last answer. It tries again by itself.`]);
  if (d.entry && d.entry.error) n.push(["bad", `Entry: ${d.entry.error}`]);
  if (d.exit && d.exit.error) n.push(["bad", `Exit: ${d.exit.error}`]);
  if (d.tunnel && d.tunnel.state === "down") n.push(["bad", `The tunnel ${d.tunnel.name} is down: nobody can use the product until it is back.`]);
  if (d.tunnel && !d.tunnel.protected) n.push(["warn", `The tunnel ${d.tunnel.name} is not protected: the Doctor may move or replace it, and the product would lose its link. Turn Protected on in the tunnel's menu.`]);
  if (d.entry && d.entry.installed && d.entry.quota === false) n.push(["warn", "The account manager on the entry is older than version 3: it cannot keep a traffic limit, so the Traffic field does nothing until it is updated."]);
  if (d.configured && !d.tunnel) n.push(["warn", "No tunnel chosen: the page cannot tell whether the way between the entry and the exit is up (the gear)."]);
  return n.length ? `<div class="l2-notes">${n.map(([c, t]) => `<div class="l2-note ${c}"><i class="l2-dot ${c}"></i>${esc(t)}</div>`).join("")}</div>` : "";
}

const l2Soon = a => a.state === "active" && a.days_left != null && a.days_left <= 3;
const l2Views = [["", "All", () => true], ["online", "Connected", a => a.online > 0], ["soon", "Ending soon", l2Soon], ["ended", "Ended", a => a.state !== "active"], ["bot", "Shop bot", a => a.bot]];

function l2Rows(d) {
  const q = l2.q.trim().toLowerCase(), v = l2Views.find(x => x[0] === l2.view) || l2Views[0];
  return d.accounts.filter(a => (!q || a.user.includes(q) || (a.note || "").toLowerCase().includes(q)) && v[2](a));
}

function l2Filters(d) {          // (the owner, 2026-11-07: he wants to see at a glance who is connected, whose time is nearly over, whose is over; 2026-11-08: "put it in a drop-down box in the top row" - a Mac-style popup button in the bar, the count of each view beside its name)
  return `<span class="l2-filt" title="Which accounts the table shows"><select aria-label="Show" onchange="l2.view = this.value; l2Redraw()">${l2Views.map(([k, t, f]) => `<option value="${k}"${l2.view === k ? " selected" : ""}>${t} · ${d.accounts.filter(f).length}</option>`).join("")}</select><i>${L2_ICO.chev}</i></span>`;
}

function l2Since(ts) {
  if (!ts) return "";
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  return s < 90 ? "a moment ago" : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 129600 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`;
}

function l2Table(d) {
  const rows = l2Rows(d);
  if (!d.accounts.length) return `<div class="l2-glass l2-empty"><b>No accounts yet</b><span>Make the first one, or the shop bot makes them when a customer buys.</span><button class="l2-btn on" onclick="l2NewOpen()">＋ New account</button></div>`;
  return `${rows.length ? `<div class="l2-glass l2-tbl"><div class="l2-th"><span>Account</span><span>Devices</span><span>Speed</span><span>Traffic</span><span>Ends</span><span></span></div>${rows.map(l2Row).join("")}</div>` : `<div class="l2-glass l2-empty"><b>Nothing matches</b></div>`}`;          // (the owner, 2026-11-08: "this extra explanation goes" - no text under the table; the lights and the buttons explain themselves in their tooltips)
}

/* (the owner, 2026-11-08, of the rows: "the tags - bot, Ended and the rest - go"; "the state of the subscription shows HERE (the light): connected green, expired red, offline grey"; "the Ends column writes only the days left") */
const L2_WHY = {over: "The traffic is used up: it cannot connect", expired: "The time is over: it cannot connect", disabled: "Switched off: it cannot connect"};
function l2Row(a) {
  const ended = a.days_left != null && a.days_left <= 0, left = a.days_left == null ? "no end" : ended ? "ended" : a.days_left < 1 ? "under a day" : l2Plural(Math.floor(a.days_left), "day");
  const u = esc(a.user), pct = a.quota ? Math.min(100, Math.round(100 * a.used / a.quota)) : 0, tone = !a.quota ? "" : a.over ? "bad" : pct >= 80 ? "warn" : "ok";
  const traffic = a.quota ? `${l2B(a.used)}<i class="mut"> of ${l2Gb(a.quota)}</i><span class="l2-meter ${tone}"><b style="width:${pct}%"></b></span>` : `${l2B(a.used)}<i class="mut"> · no limit</i>`;
  const light = a.online > 0 ? `<i class="l2-dot ok l2-live" title="${esc(`Connected now: ${l2Plural(a.online, "device")}${a.since ? " · connected " + l2Since(a.since) : ""}`)}"></i>`
    : `<i class="l2-dot ${a.state === "expired" || a.state === "over" ? "bad" : a.state === "disabled" ? "warn" : "off"}" title="${esc(L2_WHY[a.state] || "Not connected")}"></i>`;
  return `<div class="l2-tr"><span class="l2-n">${light}<b title="${esc(a.note ? a.note + (a.bot ? " (made by the shop bot)" : "") : "")}">${u}</b></span>
    <span class="${a.online ? "l2-on" : ""}"${a.since && a.online ? ` title="${esc("Connected " + l2Since(a.since))}"` : ""}>${a.online}<i class="mut"> / ${a.limit}</i></span>
    <span>${a.down || a.up ? `${l2Mb(a.down)}<i class="mut"> / ${l2Mb(a.up)}</i>` : `<span class="mut">no limit</span>`}</span>
    <span class="l2-tf" title="${esc(`download ${l2B(a.tx)} · upload ${l2B(a.rx)}${a.over ? " · the traffic is used up" : ""}`)}">${traffic}</span>
    <span class="${ended ? "l2-ended" : l2Soon(a) ? "l2-soon" : ""}" title="${a.expires ? esc("Ends " + when(a.expires)) : "No end date"}">${left}</span>
    <span class="l2-acts"><span class="l2-seg"><button type="button" title="The message to send the customer" onclick="l2Details('${u}')">Details</button><button type="button" title="Add days" onclick="l2Renew('${u}')">Renew</button><button type="button" title="Devices, speed, traffic, end date, password, on or off" onclick="l2EditOpen('${u}')">Edit</button></span><button type="button" class="l2-ico${a.online ? "" : " idle"}" title="${a.online ? `Disconnect the ${l2Plural(a.online, "device")} connected now (they can connect again)` : "Nobody is connected now. Press it anyway to cut a device that connected a moment ago"}" aria-label="Disconnect" onclick="l2Kick('${u}')">${L2_ICO.out}</button><button type="button" class="l2-ico l2-del" title="Delete the account" aria-label="Delete" onclick="l2Del('${u}', ${a.bot ? 1 : 0})">${L2_ICO.bin}</button></span></div>`;
}

/* ---------------------------------------------------------------- the windows (one look: a quiet lead line, sections with a heading, fields with a unit and quick choices, a summary line, the buttons at the foot) */
const l2Field = (label, inner, hint, cls = "") => `<div class="l2-fld ${cls}"><label class="l2-fl">${label}</label>${inner}${hint ? `<small class="l2-fh">${hint}</small>` : ""}</div>`;
const l2In = (id, attrs, unit, extra = "") => `<span class="l2-in"><input id="${id}" ${attrs}>${unit ? `<i>${unit}</i>` : ""}${extra}</span>`;
const l2Chips = (ids, vals) => `<span class="l2-chips">${vals.map(([v, t]) => `<button type="button" class="l2-chp" data-f="${ids}" data-v="${v}" onclick="l2Pick('${ids}', '${v}')">${t}</button>`).join("")}</span>`;
const l2Sec = (title, inner) => `<section class="l2-sec"><h4>${title}</h4>${inner}</section>`;
const l2Foot = (left, ok) => `<div class="l2-act">${left || ""}<span class="l2-sp"></span><button type="button" class="l2-btn" onclick="closeModal()">Cancel</button><button type="submit" class="l2-btn on l2-pri">${ok}</button></div>`;
const l2Gen = () => { const a = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789", r = crypto.getRandomValues(new Uint32Array(12)); return [...r].map(x => a[x % a.length]).join(""); };
const l2Plural = (n, w) => `${n} ${w}${+n === 1 ? "" : "s"}`;

window.l2Pick = (ids, v) => { ids.split(",").forEach(id => { const i = document.getElementById(id); if (i) { i.value = v; i.dispatchEvent(new Event("input", {bubbles: true})); } }); l2Sync(); };
window.l2Pw = id => { const i = document.getElementById(id); if (i) { i.value = l2Gen(); i.type = "text"; } };
window.l2Rand = () => { const i = document.getElementById("n-name"); if (i) { i.value = L2_BRAND + Math.random().toString(36).slice(2, 8); l2Sync(); } };
window.l2Sync = () => {          // (the chips light up for the value in their field; the summary line says what the window will do)
  document.querySelectorAll("#modal .l2-chp[data-f]").forEach(b => { const ids = b.dataset.f.split(","); b.classList.toggle("on", ids.every(x => { const i = document.getElementById(x); return !!i && String(i.value) === String(b.dataset.v); })); });
  const n = document.getElementById("n-sum");
  if (n) { const d = l2V("n-days") || "30", dev = l2V("n-dev") || "1", gb = l2V("n-gb"), dn = l2V("n-down"), up = l2V("n-up"); n.textContent = `${l2Plural(d, "day")} · ${l2Plural(dev, "device")} · ${gb && +gb ? gb + " GB" : "unlimited traffic"} · ${(+dn || +up) ? `${+dn || "∞"} / ${+up || "∞"} Mbit/s` : "no speed limit"}`; }
  const nm = document.getElementById("n-name");
  if (nm) { const h = nm.closest(".l2-fld").querySelector(".l2-fh"), v = nm.value.trim(); if (h) h.classList.toggle("bad", !!v && !L2_NAME.test(v)); }          // (the hint under the name turns red while the name cannot be used)
};

/* New account */
window.l2NewOpen = () => {
  const body = `<form class="l2-dlg" onsubmit="l2Create(); return false" autocomplete="off">
    <p class="l2-lead">Make a VPN account for a customer. Everything here can be changed later.</p>
    ${l2Sec("Account", `<div class="l2-two">
      ${l2Field("Username", l2In("n-name", `maxlength="24" placeholder="${L2_BRAND}ali" value="${L2_BRAND}" autocapitalize="off" spellcheck="false" oninput="this.value = this.value.toLowerCase(); l2Sync()"`, "", `<button type="button" class="l2-ghost" onclick="l2Rand()">Random</button>`), "3-24 characters: a-z, 0-9 and _; it starts with nklt_")}
      ${l2Field("Password", l2In("n-pass", `maxlength="64" placeholder="Made for you" spellcheck="false"`, "", `<button type="button" class="l2-ghost" onclick="l2Pw('n-pass')">Generate</button>`), "Leave it empty and a strong one is made")}
    </div>${l2Field("Note", l2In("n-note", `maxlength="80" placeholder="Who it is for (optional)"`, ""), "", "l2-top")}`)}
    ${l2Sec("Limits", `<div class="l2-two">
      ${l2Field("Time", l2In("n-days", `type="number" min="1" max="3650" value="30" oninput="l2Sync()"`, "days") + l2Chips("n-days", [[7, "7"], [30, "30"], [90, "90"], [365, "365"]]))}
      ${l2Field("Devices at once", l2In("n-dev", `type="number" min="1" max="10" value="1" oninput="l2Sync()"`, "devices") + l2Chips("n-dev", [[1, "1"], [2, "2"], [3, "3"], [5, "5"]]))}
      ${l2Field("Traffic", l2In("n-gb", `type="number" min="0" step="1" placeholder="Unlimited" oninput="l2Sync()"`, "GB") + l2Chips("n-gb", [["", "∞"], [10, "10"], [50, "50"], [100, "100"], [500, "500"]]), "Download and upload together")}
      ${l2Field("Speed", `<span class="l2-pair">${l2In("n-down", `type="number" min="0" step="0.5" placeholder="∞" oninput="l2Sync()"`, "↓ Mbit/s")}${l2In("n-up", `type="number" min="0" step="0.5" placeholder="∞" oninput="l2Sync()"`, "↑ Mbit/s")}</span>` + l2Chips("n-down,n-up", [["", "∞"], [3, "3"], [5, "5"], [10, "10"], [20, "20"]]), "Download and upload; empty = no limit")}
    </div>`)}
    <div class="l2-sum"><i class="l2-dot ok"></i><span id="n-sum"></span></div>
    ${l2Foot("", "Create account")}
  </form>`;
  showModal("New account", body, "l2-modal");
  l2Sync();
  setTimeout(() => { const i = $("#n-name"); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }, 80);          // (the cursor stands after "nklt_": the person types the rest)
};

window.l2Create = async () => {
  const name = l2V("n-name");
  if (name === L2_BRAND) { toast(`✕ Add something after ${L2_BRAND} - who it is for, or press Random.`, "bad"); const i = $("#n-name"); if (i) i.focus(); return; }
  if (!L2_NAME.test(name)) { toast("✕ The username is 3-24 characters: lower-case letters, digits and _, starting with a letter.", "bad"); const i = $("#n-name"); if (i) i.focus(); return; }
  const body = {name, days: +l2V("n-days") || 30, devices: +l2V("n-dev") || 1, password: l2V("n-pass"), note: l2V("n-note")};
  if (l2V("n-gb") !== "") body.gb = +l2V("n-gb");
  if (l2V("n-down") !== "") body.down = +l2V("n-down");
  if (l2V("n-up") !== "") body.up = +l2V("n-up");
  try { await api("/api/l2tp/accounts", {method: "POST", body: JSON.stringify(body)}); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  await l2Load(true); l2.html = ""; l2Draw(); toast("✓ Account made", "ok");
  l2Details(name);          // (the window of the new account is replaced by its details: the message to send)
};

/* Edit */
window.l2EditOpen = name => {
  const a = (l2.d.accounts || []).find(x => x.user === name); if (!a) return;
  const gb = a.quota ? +(a.quota / 1e9).toFixed(2) : "", ends = l2Day(a.expires), pct = a.quota ? Math.min(100, Math.round(100 * a.used / a.quota)) : 0, tone = !a.quota ? "" : a.over ? "bad" : pct >= 80 ? "warn" : "ok";
  l2.e = {user: a.user, was: {devices: String(a.limit), down: String(a.down || ""), up: String(a.up || ""), gb: String(gb), ends, note: a.note || "", off: a.state === "disabled"}};
  /* (the owner, 2026-11-08, of this window: "much more compact, and those numbers go" - no quick-number chips, no lines of help under the fields (the help is the tooltip of each field), the used traffic on the name line) */
  const body = `<form class="l2-dlg l2-cmp" onsubmit="l2Save(); return false" autocomplete="off">
    <div class="l2-who"><b>${esc(a.user)}</b>${a.bot ? `<em class="l2-tag" title="made by the shop bot">bot</em>` : ""}<span class="l2-pill ${a.state === "active" ? "ok" : a.state === "disabled" ? "warn" : "bad"}">${a.state === "over" ? "traffic used up" : a.state === "expired" ? "ended" : a.state === "disabled" ? "switched off" : "active"}</span><span class="l2-sp"></span><span class="mut" title="${esc(`download ${l2B(a.tx)} · upload ${l2B(a.rx)}`)}">${a.online} of ${a.limit} online · used ${esc(l2B(a.used))}${a.quota ? ` of ${esc(l2Gb(a.quota))}` : ""}</span></div>
    ${a.quota ? `<span class="l2-meter l2-top0 ${tone}"><b style="width:${pct}%"></b></span>` : ""}
    ${l2Sec("Limits", `<div class="l2-two">
      ${l2Field("Ends", l2In("e-ends", `type="date" value="${esc(ends)}" title="The account stops at the end of that day (Tehran)" oninput="l2Sync()"`, ""))}
      ${l2Field("Devices at once", l2In("e-dev", `type="number" min="1" max="10" value="${esc(a.limit)}" title="A device past the limit is refused (a device that no longer answers gives its place first)" oninput="l2Sync()"`, "devices"))}
      ${l2Field("Traffic", l2In("e-gb", `type="number" min="0" step="1" placeholder="Unlimited" value="${esc(gb)}" title="Download and upload together, in GB; empty = no limit" oninput="l2Sync()"`, "GB"))}
      ${l2Field("Speed", `<span class="l2-pair">${l2In("e-down", `type="number" min="0" step="0.5" placeholder="∞" value="${esc(a.down || "")}" title="Download, Mbit/s; empty = no limit. Reaches the devices that are online at once" oninput="l2Sync()"`, "↓ Mbit/s")}${l2In("e-up", `type="number" min="0" step="0.5" placeholder="∞" value="${esc(a.up || "")}" title="Upload, Mbit/s; empty = no limit" oninput="l2Sync()"`, "↑ Mbit/s")}</span>`)}
    </div>`)}
    ${l2Sec("Access", `<div class="l2-two">
      ${l2Field("New password", l2In("e-pass", `maxlength="64" placeholder="Leave empty to keep" spellcheck="false"`, "", `<button type="button" class="l2-ghost" onclick="l2Pw('e-pass')">Generate</button>`))}
      ${l2Field("Note", l2In("e-note", `maxlength="80" value="${esc(a.note || "")}"`, ""))}
    </div>
    <div class="l2-row2"><label class="l2-chk" title="A new period: the traffic is counted from zero again (used now ${esc(l2B(a.used))})"><input id="e-reset" type="checkbox"> Count the traffic from zero</label><label class="l2-chk" title="Its devices are cut and it cannot connect until it is switched on again"><input id="e-off" type="checkbox" ${a.state === "disabled" ? "checked" : ""}> Switched off</label></div>`)}
    ${l2Foot(`<button type="button" class="l2-btn" title="Cut its devices now; they can connect again" onclick="l2Kick('${esc(a.user)}')">⏏ Disconnect devices</button>`, "Save changes")}
  </form>`;
  showModal(`Edit · ${esc(a.user)}`, body, "l2-modal");
  l2Sync();
};

window.l2Save = async () => {
  const e = l2.e, w = e.was, body = {};
  if (l2V("e-pass")) body.password = l2V("e-pass");
  if (l2V("e-dev") !== w.devices) body.devices = +l2V("e-dev") || 1;
  if (l2V("e-down") !== w.down) body.down = +l2V("e-down") || 0;
  if (l2V("e-up") !== w.up) body.up = +l2V("e-up") || 0;
  if (l2V("e-gb") !== w.gb) body.gb = +l2V("e-gb") || 0;
  if (l2V("e-ends") && l2V("e-ends") !== w.ends) body.expires = l2DayEnd(l2V("e-ends"));
  if (document.getElementById("e-reset").checked) body.reset_usage = true;
  if (document.getElementById("e-off").checked !== w.off) body.disabled = document.getElementById("e-off").checked;
  if (l2V("e-note") !== w.note) body.note = l2V("e-note");
  if (!Object.keys(body).length) { hideModal(); return; }
  try { await api(`/api/l2tp/accounts/${encodeURIComponent(e.user)}`, {method: "PUT", body: JSON.stringify(body)}); } catch (x) { toast("✕ " + x.message, "bad"); return; }
  hideModal(); await l2Load(true); l2.html = ""; l2Draw(); toast(`✓ ${e.user} saved`, "ok");
};

/* Details: the message for the customer */
window.l2Details = async name => {
  try {
    const p = await api(`/api/l2tp/accounts/${encodeURIComponent(name)}/profile`);
    showModal(`Connection · ${esc(name)}`, `<div class="l2-dlg"><p class="l2-lead">This is the message for the customer. It holds the password and the shared key: send it only to them.</p><textarea id="l2-text" class="l2-text" readonly spellcheck="false" onclick="this.select()">${esc(p.text)}</textarea><div class="l2-act"><span class="l2-sp"></span><button type="button" class="l2-btn" onclick="closeModal()">Close</button><button type="button" class="l2-btn on l2-pri" onclick="l2Copy()">Copy the message</button></div></div>`, "l2-modal");
  } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.l2Copy = async () => {
  const t = $("#l2-text"); if (!t) return;
  try { await navigator.clipboard.writeText(t.value); toast("✓ Copied", "ok"); } catch (e) { t.select(); document.execCommand("copy"); toast("✓ Copied", "ok"); }
};

/* Servers (the gear) */
window.l2ConfOpen = async () => {
  try { l2.cfg = await api("/api/l2tp/config"); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  const v = l2.cfg.conf, ch = l2.cfg.choices, opt = (list, cur, none) => `<option value="0"${!cur ? " selected" : ""}>${none}</option>` + list.map(s => `<option value="${s.id}"${s.id === cur ? " selected" : ""}>${esc(s.name)}</option>`).join("");
  const body = `<form class="l2-dlg" onsubmit="l2SaveConf(); return false" autocomplete="off">
    <p class="l2-lead">Which servers carry the product, and the name the customer sees on the phone. Installing the entry and the exit is done outside this page.</p>
    ${l2Sec("Servers", `<div class="l2-two">
      ${l2Field("Entry (Iran server)", `<select id="l2c-entry" class="l2-sel">${opt(ch.iran, v.entry_id, "— choose —")}</select>`, "The customers connect to it; it holds the accounts")}
      ${l2Field("Exit (foreign server)", `<select id="l2c-exit" class="l2-sel">${opt(ch.foreign, v.exit_id, "— not told —")}</select>`, "The traffic leaves the internet from here")}
    </div>${l2Field("Tunnel between them", `<select id="l2c-tun" class="l2-sel">${`<option value="0"${!v.tunnel_id ? " selected" : ""}>— not told —</option>` + ch.tunnels.map(t => `<option value="${t.id}"${t.id === v.tunnel_id ? " selected" : ""}>${esc(t.name)} (${esc(t.iran)} ⇄ ${esc(t.foreign)})</option>`).join("")}</select>`, "Only to show its state here; keep it Protected", "l2-top")}`)}
    ${l2Sec("What the customer sees", `<div class="l2-two">
      ${l2Field("Connection name", l2In("l2c-title", `maxlength="40" value="${esc(v.title)}"`, ""), "The name of the VPN on the phone")}
      ${l2Field("Server address", l2In("l2c-addr", `maxlength="253" placeholder="the entry's own address" value="${esc(v.address)}"`, ""), "A name or an IP; empty = the entry's own address")}
    </div>`)}
    ${l2Foot("", "Save")}
  </form>`;
  showModal("Servers", body, "l2-modal");
};

window.l2SaveConf = async () => {
  const body = {entry_id: +l2V("l2c-entry") || 0, exit_id: +l2V("l2c-exit") || 0, tunnel_id: +l2V("l2c-tun") || 0, title: l2V("l2c-title"), address: l2V("l2c-addr")};
  if (!body.entry_id) { toast("✕ Choose the Iran server that is the entry.", "bad"); return; }
  try { await api("/api/l2tp/config", {method: "PUT", body: JSON.stringify(body)}); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  hideModal(); await l2Load(true); l2.html = ""; l2Draw(); toast("✓ Saved", "ok");
};

/* the rest */
window.l2Redraw = () => { l2.html = ""; l2Draw(); };          // (l2Draw gives the cursor back to the search box when it was in it)
window.l2Refresh = async () => { await l2Load(true); l2.html = ""; l2Draw(); toast("✓ Read again", "ok"); };

const l2Do = async (path, opts, okText) => {          // (one change: the entry is read again at once, so the table is the truth; `okText` may be a function of the answer)
  try { const r = await api(path, opts); await l2Load(true); l2.html = ""; l2Draw(); if (okText) toast("✓ " + (typeof okText === "function" ? okText(r) : okText), "ok"); return r; }
  catch (e) { toast("✕ " + e.message, "bad"); return null; }
};
/* (the owner, 2026-11-07, of the browser's grey "monitor... says" box: "make this box more professional" - the questions of a row are the panel's own glass windows now, the ones every other page uses: gconfirm, and a window of its own for the renewal) */
window.l2Renew = name => {          // (Renew = time and traffic together, the way a customer's month is renewed: days to add, the count from zero, more traffic)
  const a = (l2.d.accounts || []).find(x => x.user === name); if (!a) return;
  l2.r = {user: a.user, quota: a.quota || 0, used: a.used, ends: a.expires || 0};
  const body = `<form class="l2-dlg" onsubmit="l2RenewGo(); return false" autocomplete="off">
    <div class="l2-who"><b>${esc(a.user)}</b>${a.bot ? `<em class="l2-tag" title="made by the shop bot">bot</em>` : ""}<span class="l2-sp"></span><span class="mut">${a.expires ? "ends " + l2Date(a.expires) : "no end date"}</span></div>
    ${l2Sec("Time", l2Field("Add", l2In("r-days", `type="number" min="1" max="3650" value="30" oninput="l2RSync()"`, "days") + l2Chips("r-days", [[7, "7"], [30, "30"], [90, "90"], [365, "365"]]), "It counts from today, or from the end date when that is later"))}
    ${l2Sec("Traffic", a.quota
      ? `<div class="l2-use"><span>Used <b>${esc(l2B(a.used))}</b> of <b>${esc(l2Gb(a.quota))}</b></span></div>
         <label class="l2-chk"><input id="r-reset" type="checkbox" checked onchange="l2RSync()"> Count the traffic from zero again <span class="mut">(a new period)</span></label>
         ${l2Field("Add more traffic", l2In("r-add", `type="number" min="0" step="1" placeholder="None" oninput="l2RSync()"`, "GB") + l2Chips("r-add", [["", "none"], [10, "+10"], [50, "+50"], [100, "+100"]]), "Raises the limit of this account by that much")}`
      : `<p class="l2-lead">This account has no traffic limit, so there is nothing to count again. Set a limit in Edit if it should have one.</p>`)}
    <div class="l2-sum"><i class="l2-dot ok"></i><span id="r-sum"></span></div>
    ${l2Foot("", "Renew")}
  </form>`;
  showModal(`Renew · ${esc(a.user)}`, body, "l2-modal");
  l2RSync();
};
window.l2RSync = () => {
  const r = l2.r; if (!r) return;
  document.querySelectorAll("#modal .l2-chp[data-f]").forEach(b => { const i = document.getElementById(b.dataset.f); b.classList.toggle("on", !!i && String(i.value) === String(b.dataset.v)); });
  const days = parseInt(l2V("r-days"), 10) || 0, add = +l2V("r-add") || 0, reset = !!document.getElementById("r-reset") && document.getElementById("r-reset").checked;
  const base = Math.max(r.ends, Date.now() / 1000), n = document.getElementById("r-sum");
  if (n) n.textContent = days >= 1 ? `Ends ${r.ends ? l2Date(r.ends) + " → " : ""}${l2Date(base + days * 86400)}${r.quota ? ` · ${reset ? "traffic from zero" : "keeps the count"}${add ? ` · limit ${l2Gb(r.quota)} → ${+((r.quota / 1e9) + add).toFixed(2)} GB` : ""}` : ""}` : "Days is a number from 1 to 3650";
};
window.l2RenewGo = async () => {
  const r = l2.r, days = parseInt(l2V("r-days"), 10) || 0, add = +l2V("r-add") || 0, el = document.getElementById("r-reset"), reset = !!el && el.checked, enc = encodeURIComponent(r.user);
  if (!(days >= 1 && days <= 3650)) { toast("✕ Days is a number from 1 to 3650", "bad"); return; }
  try {
    await api(`/api/l2tp/accounts/${enc}/renew`, {method: "POST", body: JSON.stringify({days, reset_usage: reset})});
    if (add > 0 && r.quota) await api(`/api/l2tp/accounts/${enc}`, {method: "PUT", body: JSON.stringify({gb: +((r.quota / 1e9) + add).toFixed(2)})});
  } catch (e) { toast("✕ " + e.message, "bad"); await l2Load(true); l2.html = ""; l2Draw(); return; }
  hideModal(); await l2Load(true); l2.html = ""; l2Draw();
  toast(`✓ ${r.user}: ${days} days added${reset && r.quota ? ", traffic from zero" : ""}${add > 0 && r.quota ? `, +${add} GB` : ""}`, "ok");
};
window.l2Kick = async name => {
  const a = (l2.d.accounts || []).find(x => x.user === name), enc = encodeURIComponent(name);
  const ask = a && a.online ? `Disconnect the ${l2Plural(a.online, "device")} of ${name} now? They can connect again at once.` : `Nobody is connected to ${name} right now. Disconnect anyway, in case a device connected a moment ago?`;
  if (!(await gconfirm(ask, {danger: false}))) return;
  const r = await l2Do(`/api/l2tp/accounts/${enc}/kick`, {method: "POST"}, x => x.kicked ? `${name}: ${l2Plural(x.kicked, "device")} disconnected - ${x.kicked === 1 ? "it" : "they"} can connect again` : `${name}: nobody was connected`);
  if (r) l2.fast = Date.now() + 45000;          // (for the next 45 seconds the page follows the entry closely: the light goes out now, and comes back when the phone connects again)
};
window.l2Del = async (name, bot) => { if (await gconfirm(`Delete the account ${name}? Its devices are disconnected and it cannot be brought back.${bot ? " The shop bot made this one: the customer's order will point at an account that is gone." : ""}`, {danger: true})) { hideModal(); l2Do(`/api/l2tp/accounts/${encodeURIComponent(name)}`, {method: "DELETE"}, `${name} deleted`); } };

/* ---------------------------------------------------------------- the shop side (1.69.245): who sees L2TP in the Mini App, the plans it sells, the sales (the owner, 2026-11-07: "make creating, editing, managing the purchase and the renewal of L2TP complete") */
const l2Toman = n => (+n || 0).toLocaleString("en-US") + " toman";
const L2_PAY = {paid_with_wallet: ["ok", "Paid"], approved: ["ok", "Paid"], auto_cart_approved: ["ok", "Paid"], paid: ["ok", "Paid"], pending: ["warn", "Waiting"], have_sent: ["warn", "Waiting"], sent: ["warn", "Waiting"], low_wallet: ["bad", "Wallet too low"], declined: ["bad", "Declined"]};
const l2Pay = s => L2_PAY[s] || (/^expired/.test(s) ? ["bad", "Expired"] : ["", s || "—"]);

window.l2ShopOpen = async tab => {          // (the window opens AT ONCE - with the last answer, or a line that says it is reading - and the bot's server is read behind it: a read of that server takes a few seconds)
  if (tab) l2.tab = tab;
  if (l2.shop) l2ShopDraw(); else showModal("Plans & sales", `<div class="l2-dlg"><div class="l2-glass l2-empty"><b>Reading the shop…</b><span>The plans and the sales are read from the bot's server: a few seconds.</span></div></div>`, "l2-modal l2-wide");
  try { l2.shop = await api("/api/l2tp/shop"); } catch (e) { if (!l2.shop) hideModal(); toast("✕ " + e.message, "bad"); return; }
  const h = document.querySelector("#modal .modal-head");
  if (h && h.innerText.startsWith("Plans & sales")) l2ShopDraw();          // (only if that window is still the one on screen: a plan window or another window opened meanwhile is left alone)
};
function l2ShopDraw() { showModal("Plans & sales", l2ShopBody(), "l2-modal l2-wide"); }

function l2ShopBody() {
  const s = l2.shop, tab = l2.tab === "sales" ? "sales" : "plans";
  const stage = l2Sec("Who sees L2TP in the Mini App", `<div class="l2-opts">${s.stages.map(o => `<button type="button" class="l2-opt${s.stage === o.key ? " on" : ""}" onclick="l2Stage('${o.key}')"><b>${esc(o.label)}</b><small>${esc(o.note)}</small></button>`).join("")}</div>`);
  const tabs = `<div class="l2-tabs" role="group"><button type="button" class="${tab === "plans" ? "on" : ""}" onclick="l2.tab = 'plans'; l2ShopDraw()">Plans<i>${s.plans.length}</i></button><button type="button" class="${tab === "sales" ? "on" : ""}" onclick="l2.tab = 'sales'; l2ShopDraw()">Sales<i>${s.sales.count}</i></button></div>`;
  return `<div class="l2-dlg">${stage}${tabs}${tab === "plans" ? l2PlansSec(s) : l2SalesSec(s.sales)}<div class="l2-act"><span class="l2-sp"></span><button type="button" class="l2-btn" onclick="closeModal()">Close</button></div></div>`;
}

function l2PlansSec(s) {
  /* (1.69.249: the Mini App's own "sold out" flag - the plan stays in its list as «ناموجود», nobody can buy it, its accounts can still be renewed; the owner, 2026-11-08: the plans must be managed from here) */
  const rows = s.plans.map(p => `<div class="l2-plan${p.active ? "" : " off"}${p.soldout ? " sold" : ""}"><div class="l2-pn"><b>${esc(p.title)}</b>${p.active ? "" : `<em class="l2-st warn" title="Not offered to anybody, and its accounts cannot be renewed">Off</em>`}${p.soldout ? `<em class="l2-st" title="Listed as «ناموجود»: nobody can buy it; the accounts bought on it can still be renewed">Sold out</em>` : ""}${!p.price ? `<em class="l2-st" title="A free plan is a test plan: the Mini App hides it from the customers">Test</em>` : ""}<small class="mut">${p.accounts ? l2Plural(p.accounts, "account") + " bought" : "nobody bought it yet"}</small></div>
      <div class="l2-pf"><span>${l2Plural(p.days, "day")}</span><span>${l2Plural(p.devices, "device")}</span><span>${p.gb ? +p.gb + " GB" : "unlimited traffic"}</span><span>${p.down || p.up ? `${+p.down || "∞"} / ${+p.up || "∞"} Mbit/s` : "no speed limit"}</span></div>
      <div class="l2-pp">${p.price ? esc(l2Toman(p.price)) : "Free"}</div>
      <div class="l2-acts"><span class="l2-seg"><button type="button" onclick="l2PlanOpen(${p.id})">Edit</button><button type="button" title="${p.soldout ? "Offer it again" : "Keep it in the list as «ناموجود»: nobody can buy it, its accounts can still be renewed"}" onclick="l2PlanSold(${p.id}, ${p.soldout ? 0 : 1})">${p.soldout ? "Available" : "Sold out"}</button><button type="button" title="${p.active ? "Take it out of the list: nobody can buy it and its accounts cannot be renewed" : "Offer it again"}" onclick="l2PlanOn(${p.id}, ${p.active ? 0 : 1})">${p.active ? "Switch off" : "Switch on"}</button></span><button type="button" class="l2-ico l2-del" title="${p.accounts ? "Accounts were bought on this plan: it cannot be deleted (switching it off stops their renewal too)" : "Delete the plan"}" aria-label="Delete" onclick="l2PlanDel(${p.id})">${L2_ICO.bin}</button></div></div>`).join("");
  return l2Sec("Plans", `${rows || `<p class="l2-lead">No plan yet. A plan is what a customer buys: time, devices, traffic, speed and a price.</p>`}<div class="l2-act"><span class="l2-sp"></span><button type="button" class="l2-btn on l2-pri" onclick="l2PlanOpen(0)">＋ New plan</button></div>
    <p class="l2-lead">A renewal in the Mini App always uses the plan's CURRENT price, time and traffic: a change here reaches the next purchase and every renewal of the accounts bought on that plan (a running account keeps its own end date and limit until then). <b>Sold out</b> keeps the plan in the list as «ناموجود»: nobody can buy it, its accounts can still be renewed. <b>Switched off</b> takes it out of the list: nobody can buy it, and its accounts cannot be renewed. A free plan is a test plan the customers do not see.</p>`);
}

function l2SalesSec(x) {
  const tile = (t, v) => `<div class="l2-tile"><small>${t}</small><b>${esc(l2Toman(v))}</b></div>`;
  const rows = x.orders.map(o => { const p = l2Pay(o.state); return `<div class="l2-ord"><span class="mut" title="${esc(when(o.at))}">${esc(when(o.at))}</span><span class="l2-ob"><b>${esc(o.name || o.user_id)}</b><small class="mut">${esc(o.user_id)}</small></span><span><em class="l2-tag">${o.type === "RENEW_L2TP" ? "Renew" : "Buy"}</em> ${esc(o.plan || o.text)}</span><span>${o.price ? esc(l2Toman(o.price)) : "Free"}</span><span><em class="l2-st ${p[0]}">${esc(p[1])}</em></span></div>`; }).join("");
  return l2Sec("Sales", `<div class="l2-tiles">${tile("Today", x.today)}${tile("Last 7 days", x.week)}${tile("Last 30 days", x.month)}${tile("All time", x.total)}</div>
    <p class="l2-lead">${l2Plural(x.buys, "purchase")} and ${l2Plural(x.renews, "renewal")} paid so far. Only paid orders are counted in the money above. A customer pays an L2TP order from the wallet; a card or currency payment is first a top-up of the wallet in the bot, so it shows there and not here.</p>
    ${rows ? `<div class="l2-ords"><div class="l2-ord l2-oh"><span>When</span><span>Customer</span><span>Order</span><span>Price</span><span>State</span></div>${rows}</div>` : `<p class="l2-lead">No order yet.</p>`}`);
}

window.l2Stage = async key => {
  const s = l2.shop; if (!s || s.stage === key) return;
  const open = key === "agents" || key === "on";
  if (open && !(await gconfirm(key === "on" ? "Every customer of the Mini App will see L2TP and can buy it with the wallet. The plans that are switched on go on sale now. Continue?" : "The bot's admins and its partners will see L2TP in the Mini App and can buy it. The plans that are switched on go on sale now. Continue?", {danger: false}))) return;
  try { const r = await api("/api/l2tp/shop/stage", {method: "PUT", body: JSON.stringify({stage: key})}); s.stage = r.stage; } catch (e) { toast("✕ " + e.message, "bad"); return; }
  l2ShopDraw(); toast("✓ " + (s.stages.find(o => o.key === key) || {}).label, "ok");
};
window.l2PlanOn = async (id, on) => {
  const p = l2.shop.plans.find(x => x.id === id);
  if (!on && p && p.accounts && !(await gconfirm(`Switch off "${p.title}"? ${l2Plural(p.accounts, "account")} ${p.accounts === 1 ? "was" : "were"} bought on it: while it is off nobody can buy it and ${p.accounts === 1 ? "that account cannot" : "those accounts cannot"} be renewed in the Mini App. Continue?`, {danger: false}))) return;
  try { const r = await api(`/api/l2tp/plans/${id}/active`, {method: "PUT", body: JSON.stringify({on: !!on})}); l2.shop.plans = r.plans; } catch (e) { toast("✕ " + e.message, "bad"); return; }
  l2ShopDraw(); toast(on ? "✓ The plan is on sale" : "✓ The plan is off", "ok");
};
window.l2PlanSold = async (id, on) => {
  const p = l2.shop.plans.find(x => x.id === id); if (!p) return;
  if (on && !(await gconfirm(`Mark "${p.title}" as sold out? It stays in the Mini App's list as «ناموجود»: nobody can buy it, and ${p.accounts ? `the ${l2Plural(p.accounts, "account")} bought on it can still be renewed` : "the accounts bought on it can still be renewed"}. Continue?`, {danger: false}))) return;
  try { const r = await api(`/api/l2tp/plans/${id}/soldout`, {method: "PUT", body: JSON.stringify({on: !!on})}); l2.shop.plans = r.plans; } catch (e) { toast("✕ " + e.message, "bad"); return; }
  l2ShopDraw(); toast(on ? "✓ Sold out: nobody can buy it, renewals go on" : "✓ The plan is available again", "ok");
};
window.l2PlanDel = async id => {
  const p = l2.shop.plans.find(x => x.id === id); if (!p) return;
  if (p.accounts) { toast("✕ Accounts were bought on this plan, so it cannot be deleted (switching it off stops their renewal too)", "bad"); return; }
  if (!(await gconfirm(`Delete the plan "${p.title}"? It cannot be brought back.`, {danger: true}))) return;
  try { const r = await api(`/api/l2tp/plans/${id}`, {method: "DELETE"}); l2.shop.plans = r.plans; } catch (e) { toast("✕ " + e.message, "bad"); return; }
  l2ShopDraw(); toast("✓ Plan deleted", "ok");
};

window.l2PlanOpen = id => {
  const p = id ? l2.shop.plans.find(x => x.id === id) : null; if (id && !p) return;
  l2.p = {id: id || 0};
  const v = p || {title: "", days: 30, devices: 1, gb: "", down: "", up: "", price: "", descr: "", active: true, sort: 0};
  const body = `<form class="l2-dlg" onsubmit="l2PlanSave(); return false" autocomplete="off">
    <p class="l2-lead">${p ? "Change this plan." : "Make a plan the Mini App can sell."} The customer sees the name, the description and the price; the rest is what the account gets.</p>
    ${l2Sec("Plan", `<div class="l2-two">
      ${l2Field("Name", l2In("p-title", `maxlength="80" placeholder="L2TP · 1 month" value="${esc(v.title)}"`, ""), "What the customer reads on the card")}
      ${l2Field("Price", l2In("p-price", `type="number" min="0" step="1000" placeholder="0" value="${esc(v.price)}" oninput="l2PSync()"`, "toman"), "0 = free (a test plan)")}
    </div>${l2Field("Description", `<textarea id="p-descr" class="l2-ta" maxlength="500" placeholder="A line or two about the service (optional)">${esc(v.descr)}</textarea>`, "The Mini App adds its own text about the service (secure L2TP/IPsec, no app needed) under it: do not repeat that. No quotes, backslashes or semicolons", "l2-top")}`)}
    ${l2Sec("What the account gets", `<div class="l2-two">
      ${l2Field("Time", l2In("p-days", `type="number" min="1" max="3650" value="${esc(v.days)}" oninput="l2PSync()"`, "days") + l2Chips("p-days", [[7, "7"], [30, "30"], [90, "90"], [365, "365"]]))}
      ${l2Field("Devices at once", l2In("p-dev", `type="number" min="1" max="10" value="${esc(v.devices)}" oninput="l2PSync()"`, "devices") + l2Chips("p-dev", [[1, "1"], [2, "2"], [3, "3"], [5, "5"]]))}
      ${l2Field("Traffic", l2In("p-gb", `type="number" min="0" step="1" placeholder="Unlimited" value="${esc(v.gb || "")}" oninput="l2PSync()"`, "GB") + l2Chips("p-gb", [["", "∞"], [10, "10"], [50, "50"], [100, "100"], [500, "500"]]), "Download and upload together; renewing counts it from zero")}
      ${l2Field("Speed", `<span class="l2-pair">${l2In("p-down", `type="number" min="0" step="0.5" placeholder="∞" value="${esc(v.down || "")}" oninput="l2PSync()"`, "↓ Mbit/s")}${l2In("p-up", `type="number" min="0" step="0.5" placeholder="∞" value="${esc(v.up || "")}" oninput="l2PSync()"`, "↑ Mbit/s")}</span>` + l2Chips("p-down,p-up", [["", "∞"], [3, "3"], [5, "5"], [10, "10"], [20, "20"]]), "Download and upload; empty = no limit")}
    </div>`)}
    ${l2Sec("Sale", `<div class="l2-two">
      <label class="l2-chk"><input id="p-on" type="checkbox" ${v.active ? "checked" : ""}> On sale <span class="mut">(offered to those who can see L2TP)</span></label>
      ${l2Field("Order in the list", l2In("p-sort", `type="number" min="-1000" max="1000" step="1" value="${esc(v.sort || 0)}"`, ""), "Smaller comes first")}
    </div>`)}
    <div class="l2-sum"><i class="l2-dot ok"></i><span id="p-sum"></span></div>
    ${l2Foot(`<button type="button" class="l2-btn" onclick="l2ShopDraw()">‹ Plans</button>`, p ? "Save plan" : "Create plan")}
  </form>`;
  showModal(p ? `Plan · ${esc(p.title)}` : "New plan", body, "l2-modal l2-wide");
  l2PSync();
};
window.l2PSync = () => {
  document.querySelectorAll("#modal .l2-chp[data-f]").forEach(b => { const ids = b.dataset.f.split(","); b.classList.toggle("on", ids.every(x => { const i = document.getElementById(x); return !!i && String(i.value) === String(b.dataset.v); })); });
  const n = document.getElementById("p-sum"); if (!n) return;
  const d = l2V("p-days") || "30", dev = l2V("p-dev") || "1", gb = l2V("p-gb"), dn = l2V("p-down"), up = l2V("p-up"), pr = +l2V("p-price") || 0;
  n.textContent = `${pr ? l2Toman(pr) : "Free"} · ${l2Plural(d, "day")} · ${l2Plural(dev, "device")} · ${gb && +gb ? gb + " GB" : "unlimited traffic"} · ${(+dn || +up) ? `${+dn || "∞"} / ${+up || "∞"} Mbit/s` : "no speed limit"}`;
};
const l2PlanChanges = (p, b) => {          // (what a save changes for the accounts that renew on this plan: the Mini App renews on the plan's CURRENT price, days and traffic)
  const c = [];
  if (b.price !== p.price) c.push(`price ${l2Toman(p.price)} → ${l2Toman(b.price)}`);
  if (b.days !== p.days) c.push(`time ${p.days} → ${b.days} days`);
  if ((+b.gb || 0) !== (+p.gb || 0)) c.push(`traffic ${p.gb ? +p.gb + " GB" : "unlimited"} → ${b.gb ? +b.gb + " GB" : "unlimited"}`);
  return c;
};
window.l2PlanSave = async () => {
  const id = l2.p ? l2.p.id : 0, title = l2V("p-title");
  if (title.length < 2) { toast("✕ Give the plan a name", "bad"); const i = $("#p-title"); if (i) i.focus(); return; }
  const body = {title, price: Math.round(+l2V("p-price") || 0), days: +l2V("p-days") || 30, devices: +l2V("p-dev") || 1, descr: String(($("#p-descr") || {}).value || "").trim(), active: !!document.getElementById("p-on").checked, sort: Math.round(+l2V("p-sort") || 0)};
  if (l2V("p-gb") !== "") body.gb = +l2V("p-gb");
  if (l2V("p-down") !== "") body.down = +l2V("p-down");
  if (l2V("p-up") !== "") body.up = +l2V("p-up");
  const was = id ? l2.shop.plans.find(x => x.id === id) : null;
  if (was && was.accounts) {          // (the owner is told what a save does to the accounts that renew on this plan - the Mini App session, 2026-11-07: "the panel should warn before a plan is switched off or its price or traffic changed while accounts use it")
    const ch = l2PlanChanges(was, body), off = was.active && !body.active;
    if ((ch.length || off) && !(await gconfirm(`${l2Plural(was.accounts, "account")} ${was.accounts === 1 ? "was" : "were"} bought on this plan, and the Mini App renews them at its current price, time and traffic.${ch.length ? " From now: " + ch.join(", ") + "." : ""}${off ? " The plan is switched off: nobody can buy it and those accounts cannot be renewed." : ""} Save?`, {danger: false}))) return;
  }
  try { const r = await api(id ? `/api/l2tp/plans/${id}` : "/api/l2tp/plans", {method: id ? "PUT" : "POST", body: JSON.stringify(body)}); l2.shop.plans = r.plans; } catch (e) { toast("✕ " + e.message, "bad"); return; }
  l2.tab = "plans"; l2ShopDraw(); toast(id ? "✓ Plan saved" : "✓ Plan made", "ok");
};

/* ---------------------------------------------------------------- backup and restore (1.69.248; the owner, 2026-11-08: "a backup and restore system for the L2TP section, and a section in that group where its backup is sent every hour") */
const l2Kb = b => b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB";
const L2_EVERY = [[30, "every 30 minutes"], [60, "every hour"], [180, "every 3 hours"], [360, "every 6 hours"], [720, "every 12 hours"], [1440, "once a day"], [0, "with the daily backup of the panel"]];

window.l2BkOpen = async () => {          // (opens AT ONCE with the last answer, or a line that says it is reading; the list is read behind it)
  if (l2.bk) l2BkDraw(); else showModal("L2TP backup", `<div class="l2-dlg"><div class="l2-glass l2-empty"><b>Reading the backups…</b></div></div>`, "l2-modal l2-wide");
  try { l2.bk = await api("/api/settings/backup"); } catch (e) { if (!l2.bk) hideModal(); toast("✕ " + e.message, "bad"); return; }
  const h = document.querySelector("#modal .modal-head");
  if (h && h.innerText.startsWith("L2TP backup")) l2BkDraw();
};
function l2BkDraw() { showModal("L2TP backup", l2BkBody(), "l2-modal l2-wide"); }

function l2BkBody() {
  const b = l2.bk, p = b.parts.l2tp, sc = p.sched || {}, files = (b.backups || []).filter(f => f.part === "l2tp").slice(0, 10), last = p.last || files.find(f => !/-before-restore/.test(f.file));
  const every = L2_EVERY.some(x => x[0] === p.every_minutes) ? L2_EVERY : L2_EVERY.concat([[p.every_minutes, `every ${p.every_minutes} minutes`]]);
  const notes = [];
  if (!b.has_passphrase) notes.push(["bad", "No backup password yet: nothing is backed up until you set one below."]);
  if (!b.telegram_ready) notes.push(["warn", "Telegram is not set up or switched off (Settings → Api & Key): the backups stay on this server only."]);
  if (sc.last_run && sc.ok === false) notes.push(["bad", `The last try (${ago(sc.last_run)} ago) did not work: ${sc.error || "no reason given"}. It tries again in a few minutes.`]);
  else if (p.send && b.telegram_ready && sc.last_run && !sc.sent && sc.telegram) notes.push(["warn", `The last file was not sent to the group: ${sc.telegram}`]);
  const topic = !b.telegram_ready ? `<span class="mut">needs Telegram</span>`
    : p.topic ? `<span class="l2-st ok" title="Its files go to this topic of the group">topic · ${esc(p.topic_name || "L2TP backups")}</span><button type="button" class="l2-btn" onclick="l2BkTopicOff()">Unlink</button>`
    : `<button type="button" class="l2-btn" onclick="l2BkTopic()">＋ Make the topic</button><span class="mut">made by itself at the first backup, too</span>`;
  const pass = b.has_passphrase ? "" : l2Sec("Backup password", `<p class="l2-lead">Every backup holds the account names, passwords and the shared key, so it is locked with a password. You type it only to restore a file made on another panel. Keep it somewhere safe: without it a backup cannot be opened.</p>
    <div class="l2-bkr">${l2In("bk-pass", `type="text" minlength="12" placeholder="at least 12 characters" autocomplete="off" spellcheck="false"`, "")}<button type="button" class="l2-btn" onclick="l2BkGen()">🎲 Generate</button><button type="button" class="l2-btn on l2-pri" onclick="l2BkPass()">Set the password</button></div>`);
  const rows = files.map(f => `<div class="l2-bkf"><span class="mut" title="${esc(when(f.at))}">${esc(ago(f.at))} ago</span><span class="mut">${l2Kb(f.bytes)}</span><span class="l2-nm" title="${esc(f.file)}">${esc(f.file)}</span><span class="l2-acts"><a class="l2-btn" href="/api/backup/download/${encodeURIComponent(f.file)}" title="Save this file on your computer">Download</a><button type="button" class="l2-btn" onclick="l2BkRestore('${esc(f.file)}')">Restore</button></span></div>`).join("");
  return `<div class="l2-dlg">
    ${notes.length ? `<div class="l2-notes">${notes.map(([c, t]) => `<div class="l2-note ${c}"><i class="l2-dot ${c}"></i>${esc(t)}</div>`).join("")}</div>` : ""}
    ${l2Sec("By itself", `<div class="l2-two">
        <label class="l2-chk"><input type="checkbox" ${p.enabled ? "checked" : ""} onchange="l2BkSet({enabled: this.checked})"> Back up by itself</label>
        ${l2Field("How often", `<select class="l2-sel" onchange="l2BkSet({every_minutes: +this.value})">${every.map(([v, t]) => `<option value="${v}"${v === p.every_minutes ? " selected" : ""}>${t}</option>`).join("")}</select>`)}
      </div>
      <label class="l2-chk"><input type="checkbox" ${p.send ? "checked" : ""} onchange="l2BkSet({send: this.checked})"> Send each file to the Telegram group</label>
      <div class="l2-bkr"><span class="mut">Where in the group</span>${topic}</div>
      <p class="l2-lead">${last ? `Latest backup ${esc(ago(last.at))} ago, ${l2Kb(last.bytes)}.` : "No backup yet."} Files are kept ${+b.keep_days || 14} days on this server (Settings → Backups → Keep for).</p>`)}
    ${pass}
    ${l2Sec("Backups on this server", `${rows ? `<div class="l2-bkfs">${rows}</div>` : `<p class="l2-lead">No file yet. Press Back up now.</p>`}
      <div class="l2-act"><span class="l2-bkr"><button type="button" class="l2-btn on l2-pri" ${b.has_passphrase ? "" : "disabled"} onclick="l2BkNow()">Back up now</button><button type="button" class="l2-btn" title="A file you downloaded, or got from the Telegram group (.tar.gz.enc)" onclick="document.getElementById('bk-file').click()">📂 Restore from a file…</button><input id="bk-file" type="file" hidden accept=".enc" onchange="l2BkFile(this)"></span><span class="l2-sp"></span><button type="button" class="l2-btn" onclick="closeModal()">Close</button></div>`)}
    <p class="l2-lead">A restore never changes the shared IPsec key. The shop's own tables (plans, who bought what) are not in this file; the end dates and the order notes of the accounts are.</p>
  </div>`;
}

window.l2BkSet = async patch => {
  try { l2.bk = await api("/api/settings/backup/part/l2tp", {method: "POST", body: JSON.stringify(patch)}); toast("✓ Saved", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); try { l2.bk = await api("/api/settings/backup"); } catch (x) { /* the window is drawn again with the last answer */ } }
  l2BkDraw();
};
window.l2BkNow = async () => {
  const p = l2.bk.parts.l2tp; toast("Backing up…");
  try {
    const r = await api(`/api/backup/now?part=l2tp&send=${p.send ? "true" : "false"}`, {method: "POST"});
    toast(`✓ ${r.file} · ${l2Kb(r.bytes)}` + (r.sent ? " · sent to the group" : p.send ? " · not sent: " + r.telegram : ""), r.sent || !p.send ? "ok" : "bad");
  } catch (e) { toast("✕ " + e.message, "bad"); }
  try { l2.bk = await api("/api/settings/backup"); } catch (e) { /* the last answer stays */ }
  l2BkDraw();
};
window.l2BkTopic = async () => {
  toast("Making the topic…");
  try { l2.bk = await api("/api/backup/topic/l2tp", {method: "POST"}); toast("✓ Topic made: the backups go there", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
  l2BkDraw();
};
window.l2BkTopicOff = async () => {
  if (!(await gconfirm("Unlink the topic? The next backup makes a new topic (this one stays in Telegram).", {danger: false}))) return;
  try { l2.bk = await api("/api/backup/topic/l2tp", {method: "DELETE"}); } catch (e) { toast("✕ " + e.message, "bad"); }
  l2BkDraw();
};
window.l2BkGen = () => { const a = new Uint8Array(15); crypto.getRandomValues(a); const i = document.getElementById("bk-pass"); if (!i) return; i.value = [...a].map(x => "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789".charAt(x % 54)).join("").replace(/(.{5})/g, "$1-").replace(/-$/, ""); i.focus(); i.select(); toast("Copy this password somewhere safe, then press Set", "ok"); };
window.l2BkPass = async () => {
  const v = l2V("bk-pass"); if (v.length < 12) { toast("✕ At least 12 characters", "bad"); return; }
  try { await api("/api/settings/backup/passphrase", {method: "POST", body: JSON.stringify({passphrase: v})}); l2.bk = await api("/api/settings/backup"); toast("✓ Backup password set - keep it somewhere safe", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
  l2BkDraw();
};

window.l2BkFile = inp => { const f = inp.files[0]; if (!f) return; if (f.size > 120e6) { toast("✕ The file is too big for a backup", "bad"); return; } f.arrayBuffer().then(buf => l2BkRestore(f.name, buf)); };
window.l2BkRestore = (file, blob) => {          // (the way to restore is chosen in a window of its own; `blob` = a file from the computer)
  l2.rs = {file, blob: blob || null, mode: "missing"};
  const body = `<form class="l2-dlg" onsubmit="l2BkGo(); return false">
    <p class="l2-lead">From <b>${esc(file)}</b>. Choose what to put back on the entry.</p>
    ${l2Sec("What to restore", `<div class="l2-opts l2-o2">
      <button type="button" class="l2-opt on" data-m="missing" onclick="l2BkMode('missing')"><b>Only the accounts that are gone</b><small>Puts back the accounts the entry does not have now. Nothing that exists is changed or removed. An account you deleted on purpose after this backup comes back too.</small></button>
      <button type="button" class="l2-opt" data-m="all" onclick="l2BkMode('all')"><b>Everything as in the backup</b><small>Every account gets its password, end date, devices, traffic and speed from the backup, and the accounts made after it are removed. Use it to go back in time.</small></button>
    </div>`)}
    ${l2Field("Password of the file", l2In("bk-pw", `type="password" placeholder="Only when it was made with another panel's backup password" autocomplete="off"`, ""), "", "l2-top")}
    <p class="l2-lead">The entry keeps a copy of its accounts first, and one is kept here too. The shared IPsec key is not touched.</p>
    ${l2Foot(`<button type="button" class="l2-btn" onclick="l2BkOpen()">‹ Backup</button>`, "Restore")}
  </form>`;
  showModal("Restore · L2TP", body, "l2-modal l2-wide");
};
window.l2BkMode = m => { if (!l2.rs) return; l2.rs.mode = m; document.querySelectorAll("#modal .l2-opt[data-m]").forEach(x => x.classList.toggle("on", x.dataset.m === m)); };
window.l2BkGo = async () => {
  const r = l2.rs; if (!r) return;
  const pw = l2V("bk-pw");
  if (r.mode === "all" && !(await gconfirm("Make the accounts on the entry exactly as in this backup? The accounts made after it are removed and their devices disconnected; the others get their old passwords, end dates and traffic back. The entry keeps a copy first.", {danger: true}))) return;
  try {
    toast("Restoring…");
    const x = r.blob ? await api("/api/backup/restore-upload", {method: "POST", headers: {"Content-Type": "application/octet-stream", "X-Backup-Passphrase": pw, "X-Backup-Mode": r.mode}, body: r.blob})
      : await api("/api/backup/restore", {method: "POST", body: JSON.stringify({file: r.file, passphrase: pw, mode: r.mode})});
    hideModal(); l2.rs = null; await l2Load(true); l2.html = ""; l2Draw(); toast("✓ " + x.message, "ok");
  } catch (e) {
    if (/passphrase/i.test(e.message)) { toast("✕ The saved backup password does not open this file: type the password it was made with", "bad"); const i = document.getElementById("bk-pw"); if (i) { i.focus(); i.select(); } return; }
    toast("✕ " + e.message, "bad");
  }
};
