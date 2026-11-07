/* The L2TP tab of Network (1.69.241; the data comes from services/l2tp.py): the VPN account (L2TP over IPsec, no app on the phone) the shop sells. The entry (an Iran server) holds the accounts, the exit (a foreign server) lets the traffic out,
   a protected tunnel joins them. One bar (entry, exit, tunnel, accounts, online), one table of accounts (cards on a phone), and the work of an account in windows of their own, never over the table: New account, Edit, Details (the message to send a customer)
   and Servers (the gear). Routes: GET /api/l2tp, GET and PUT /api/l2tp/config, POST /api/l2tp/accounts, POST .../renew, PUT, DELETE and POST .../kick on /api/l2tp/accounts/{name}, GET .../profile.
   Uses the helpers of app.js: $, api, esc, ago, when, renderSub, PAGES, state, toast, showModal, hideModal. */
const l2 = {d: null, err: "", html: "", cfg: null, e: null, q: "", only: "", timer: 0, asked: false};
const L2_NAME = /^[a-z][a-z0-9_]{2,23}$/;
const l2B = n => { n = +n || 0; return n >= 1e9 ? (n / 1e9).toFixed(2) + " GB" : n >= 1e6 ? (n / 1e6).toFixed(1) + " MB" : n >= 1e3 ? Math.round(n / 1e3) + " KB" : n + " B"; };
const l2Mb = v => v ? (+v) + " Mbit/s" : "—";
const l2Gb = q => q ? (+(q / 1e9).toFixed(2)) + " GB" : "";
const l2Date = ts => ts ? new Date(ts * 1000).toISOString().slice(0, 10) : "—";
const l2Day = ts => ts ? new Date(ts * 1000).toISOString().slice(0, 10) : "";          // (the value of a date field)
const l2DayEnd = s => Math.floor(Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10), 23, 59, 59) / 1000) - 12600;          // (the end of that day in Tehran, +03:30)
const l2Dot = ok => ok === true ? "ok" : ok === false ? "bad" : "warn";
const l2V = id => { const e = document.getElementById(id); return e ? String(e.value).trim() : ""; };
const l2Open = () => !!document.querySelector("#modal .modal");

PAGES.l2tp = async () => {
  renderSub();
  await l2Load();
  l2Draw();
  clearInterval(l2.timer);
  l2.timer = setInterval(async () => {          // (while the tab is open and no window is: the page follows the entry by itself)
    if (!state.sub || state.sub.tunnels !== "l2tp" || state.page !== "tunnels") { clearInterval(l2.timer); l2.timer = 0; return; }
    if (l2Open() || document.hidden) return;
    await l2Load(); l2Draw();
  }, 15000);
};

async function l2Load(fresh) {
  try { l2.d = await api("/api/l2tp" + (fresh ? "?fresh=1" : "")); l2.err = ""; } catch (e) { l2.err = e.message; }
}

function l2Draw() {
  const box = $("#page"); if (!box) return;
  const html = l2Page();
  if (html !== l2.html) { l2.html = html; box.innerHTML = html; }          // (nothing changed: nothing is redrawn - a redraw is what makes a page jump)
  if (l2.d && !l2.d.configured && !l2.asked && !l2Open()) { l2.asked = true; l2ConfOpen(); }          // (nothing chosen yet: the servers window opens by itself, once)
}

function l2Page() {
  const d = l2.d;
  if (l2.err) return `<div class="l2-app"><div class="l2-glass l2-empty"><b>${esc(l2.err)}</b></div></div>`;
  if (!d) return `<div class="l2-app"><div class="l2-glass l2-empty"><b>Loading…</b></div></div>`;
  return `<div class="l2-app">${l2Bar(d)}${l2Notes(d)}${d.configured ? l2Table(d) : `<div class="l2-glass l2-empty"><b>Choose the servers first</b><span>Pick the Iran server that is the entry and the foreign server that is the exit.</span><button class="l2-btn on" onclick="l2ConfOpen()">Choose servers</button></div>`}</div>`;
}

function l2Bar(d) {
  const en = d.entry, ex = d.exit, tu = d.tunnel;
  const chip = (label, ok, text, tip) => `<span class="l2-chip" title="${esc(tip || "")}"><i class="l2-dot ${l2Dot(ok)}"></i><small>${label}</small><b>${esc(text)}</b></span>`;
  return `<div class="l2-bar">
    ${en ? chip("Entry", en.ok, en.ok ? `${en.units && en.units.strongswan === "active" ? "up" : "check"}${en.load != null ? " · load " + en.load : ""}` : "not ready", en.error || `IPsec ${en.units ? en.units.strongswan : "?"}, L2TP ${en.units ? en.units.xl2tpd : "?"}, timer ${en.units ? en.units.tick : "?"}, pool ${en.pool || "?"}`) : ""}
    ${d.exit ? chip("Exit", ex.ok, ex.ok ? "ready" : "not ready", ex.error || `${ex.rules} rules, routes ${(ex.routes || []).join(" ")}`) : ""}
    ${tu ? chip("Tunnel", tu.state === "up" ? true : tu.state === "down" ? false : null, `${tu.name}${tu.rtt_ms != null ? " · " + Math.round(tu.rtt_ms) + " ms" : ""}${tu.protected ? " · protected" : ""}`, tu.error || "the tunnel between the entry and the exit") : ""}
    ${d.configured ? `<span class="l2-chip"><small>accounts</small><b>${d.accounts.length}</b></span><span class="l2-chip"><small>online</small><b>${d.online}</b></span>` : ""}
    <span class="l2-sp"></span>
    ${d.configured ? `<input class="l2-q" placeholder="Search" value="${esc(l2.q)}" oninput="l2.q = this.value; l2Redraw()">` : ""}
    ${d.configured ? `<button class="l2-btn${l2.only === "bot" ? " on" : ""}" title="Only the accounts the shop bot made" onclick="l2.only = l2.only === 'bot' ? '' : 'bot'; l2Redraw()">Bot</button>` : ""}
    ${d.configured ? `<button class="l2-btn on l2-pri" onclick="l2NewOpen()">＋ New account</button>` : ""}
    <button class="l2-btn" title="The entry and the exit, the connection name" onclick="l2ConfOpen()">⚙</button>
    <button class="l2-btn" title="Read the entry again now" onclick="l2Refresh()">↻</button>
  </div>`;
}

function l2Notes(d) {
  const n = [];
  if (d.entry && d.entry.error) n.push(["bad", `Entry: ${d.entry.error}`]);
  if (d.exit && d.exit.error) n.push(["bad", `Exit: ${d.exit.error}`]);
  if (d.tunnel && d.tunnel.state === "down") n.push(["bad", `The tunnel ${d.tunnel.name} is down: nobody can use the product until it is back.`]);
  if (d.tunnel && !d.tunnel.protected) n.push(["warn", `The tunnel ${d.tunnel.name} is not protected: the Doctor may move or replace it, and the product would lose its link. Turn Protected on in the tunnel's menu.`]);
  if (d.entry && d.entry.installed && d.entry.quota === false) n.push(["warn", "The account manager on the entry is older than version 3: it cannot keep a traffic limit, so the Traffic field does nothing until it is updated."]);
  if (d.configured && !d.tunnel) n.push(["warn", "No tunnel chosen: the page cannot tell whether the way between the entry and the exit is up (the gear)."]);
  return n.length ? `<div class="l2-notes">${n.map(([c, t]) => `<div class="l2-note ${c}"><i class="l2-dot ${c}"></i>${esc(t)}</div>`).join("")}</div>` : "";
}

function l2Rows(d) {
  const q = l2.q.trim().toLowerCase();
  return d.accounts.filter(a => (!q || a.user.includes(q) || (a.note || "").toLowerCase().includes(q)) && (l2.only !== "bot" || a.bot));
}

function l2Table(d) {
  const rows = l2Rows(d);
  if (!d.accounts.length) return `<div class="l2-glass l2-empty"><b>No accounts yet</b><span>Make the first one, or the shop bot makes them when a customer buys.</span><button class="l2-btn on" onclick="l2NewOpen()">＋ New account</button></div>`;
  if (!rows.length) return `<div class="l2-glass l2-empty"><b>Nothing matches</b></div>`;
  return `<div class="l2-glass l2-tbl"><div class="l2-th"><span>Account</span><span>Devices</span><span>Speed</span><span>Traffic</span><span>Ends</span><span></span></div>${rows.map(l2Row).join("")}</div>
    <div class="l2-foot mut">Traffic is download and upload together, counted live for the devices that are online; when it is used up the account is cut within 30 seconds and cannot connect until you raise the limit, reset the count or renew with a new period.</div>`;
}

function l2Row(a) {
  const c = a.state === "active" ? "ok" : a.state === "disabled" ? "warn" : "bad";
  const left = a.days_left == null ? "no end" : a.days_left <= 0 ? "ended" : a.days_left < 1 ? "under 1 day" : `${Math.floor(a.days_left)} days left`;
  const u = esc(a.user), pct = a.quota ? Math.min(100, Math.round(100 * a.used / a.quota)) : 0, tone = !a.quota ? "" : a.over ? "bad" : pct >= 80 ? "warn" : "ok";
  const traffic = a.quota ? `${l2B(a.used)}<i class="mut"> of ${l2Gb(a.quota)}</i><span class="l2-meter ${tone}"><b style="width:${pct}%"></b></span>` : `${l2B(a.used)}<i class="mut"> · no limit</i>`;
  return `<div class="l2-tr"><span class="l2-n"><i class="l2-dot ${c}" title="${esc(a.state)}"></i><b>${u}</b>${a.bot ? `<em class="l2-tag" title="made by the shop bot">bot</em>` : ""}${a.note ? `<small class="mut" title="${esc(a.note)}">${esc(a.note)}</small>` : ""}</span>
    <span class="${a.online ? "l2-on" : ""}">${a.online}<i class="mut"> / ${a.limit}</i></span>
    <span>${a.down || a.up ? `${l2Mb(a.down)}<i class="mut"> / ${l2Mb(a.up)}</i>` : `<span class="mut">no limit</span>`}</span>
    <span class="l2-tf" title="${esc(`download ${l2B(a.tx)} · upload ${l2B(a.rx)}${a.over ? " · the traffic is used up" : ""}`)}">${traffic}</span>
    <span title="${a.expires ? esc(when(a.expires)) : ""}">${l2Date(a.expires)}<i class="mut"> · ${left}</i></span>
    <span class="l2-acts"><button class="l2-btn" title="The message to send the customer" onclick="l2Details('${u}')">Details</button><button class="l2-btn" title="Add days" onclick="l2Renew('${u}')">Renew</button><button class="l2-btn" title="Devices, speed, traffic, end date, password, on or off" onclick="l2EditOpen('${u}')">Edit</button><button class="l2-btn" title="Disconnect its devices now" onclick="l2Kick('${u}')">⏏</button><button class="l2-btn l2-del" title="Delete the account" onclick="l2Del('${u}', ${a.bot ? 1 : 0})">✕</button></span></div>`;
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
window.l2Rand = () => { const i = document.getElementById("n-name"); if (i) { i.value = "vpn" + Math.random().toString(36).slice(2, 8); l2Sync(); } };
window.l2EndAdd = n => { const i = document.getElementById("e-ends"); if (!i) return; const cur = i.value ? Date.parse(i.value + "T00:00:00Z") : 0, base = Math.max(cur, Date.now()); i.value = new Date(base + n * 86400000).toISOString().slice(0, 10); l2Sync(); };
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
      ${l2Field("Username", l2In("n-name", `maxlength="24" placeholder="e.g. ali_01" autocapitalize="off" spellcheck="false" oninput="this.value = this.value.toLowerCase(); l2Sync()"`, "", `<button type="button" class="l2-ghost" onclick="l2Rand()">Random</button>`), "3-24 characters: a-z, 0-9 and _, starting with a letter")}
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
  setTimeout(() => { const i = $("#n-name"); if (i) i.focus(); }, 80);
};

window.l2Create = async () => {
  const name = l2V("n-name");
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
  const body = `<form class="l2-dlg" onsubmit="l2Save(); return false" autocomplete="off">
    <div class="l2-who"><b>${esc(a.user)}</b>${a.bot ? `<em class="l2-tag" title="made by the shop bot">bot</em>` : ""}<span class="l2-pill ${a.state === "active" ? "ok" : a.state === "disabled" ? "warn" : "bad"}">${a.state === "over" ? "traffic used up" : a.state === "expired" ? "ended" : a.state === "disabled" ? "switched off" : "active"}</span><span class="l2-sp"></span><span class="mut">${a.online} of ${a.limit} online</span></div>
    <div class="l2-use"><span>Used <b>${esc(l2B(a.used))}</b>${a.quota ? ` of <b>${esc(l2Gb(a.quota))}</b>` : " · no limit"}</span>${a.quota ? `<span class="l2-meter ${tone}"><b style="width:${pct}%"></b></span>` : ""}</div>
    ${l2Sec("Limits", `<div class="l2-two">
      ${l2Field("Ends", l2In("e-ends", `type="date" value="${esc(ends)}" oninput="l2Sync()"`, "") + `<span class="l2-chips"><button type="button" class="l2-chp" onclick="l2EndAdd(7)">+7 days</button><button type="button" class="l2-chp" onclick="l2EndAdd(30)">+30 days</button><button type="button" class="l2-chp" onclick="l2EndAdd(90)">+90 days</button></span>`, "The account stops at the end of that day (Tehran)")}
      ${l2Field("Devices at once", l2In("e-dev", `type="number" min="1" max="10" value="${esc(a.limit)}" oninput="l2Sync()"`, "devices") + l2Chips("e-dev", [[1, "1"], [2, "2"], [3, "3"], [5, "5"]]), "A new device past the limit drops the oldest")}
      ${l2Field("Traffic", l2In("e-gb", `type="number" min="0" step="1" placeholder="Unlimited" value="${esc(gb)}" oninput="l2Sync()"`, "GB") + l2Chips("e-gb", [["", "∞"], [10, "10"], [50, "50"], [100, "100"], [500, "500"]]), "Download and upload together")}
      ${l2Field("Speed", `<span class="l2-pair">${l2In("e-down", `type="number" min="0" step="0.5" placeholder="∞" value="${esc(a.down || "")}" oninput="l2Sync()"`, "↓ Mbit/s")}${l2In("e-up", `type="number" min="0" step="0.5" placeholder="∞" value="${esc(a.up || "")}" oninput="l2Sync()"`, "↑ Mbit/s")}</span>` + l2Chips("e-down,e-up", [["", "∞"], [3, "3"], [5, "5"], [10, "10"], [20, "20"]]), "Reaches the devices that are online at once")}
    </div>`)}
    ${l2Sec("Access", `<div class="l2-two">
      ${l2Field("New password", l2In("e-pass", `maxlength="64" placeholder="Leave empty to keep" spellcheck="false"`, "", `<button type="button" class="l2-ghost" onclick="l2Pw('e-pass')">Generate</button>`))}
      ${l2Field("Note", l2In("e-note", `maxlength="80" value="${esc(a.note || "")}"`, ""))}
    </div>
    <label class="l2-chk"><input id="e-reset" type="checkbox"> Count the traffic from zero again <span class="mut">(a new period; used now ${esc(l2B(a.used))})</span></label>
    <label class="l2-chk"><input id="e-off" type="checkbox" ${a.state === "disabled" ? "checked" : ""}> Switched off <span class="mut">(its devices are cut and it cannot connect)</span></label>`)}
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
window.l2Redraw = () => { l2.html = ""; l2Draw(); const q = $(".l2-q"); if (q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); } };
window.l2Refresh = async () => { await l2Load(true); l2.html = ""; l2Draw(); toast("✓ Read again", "ok"); };

const l2Do = async (path, opts, okText) => {          // (one change: the entry is read again at once, so the table is the truth)
  try { const r = await api(path, opts); await l2Load(true); l2.html = ""; l2Draw(); if (okText) toast("✓ " + okText, "ok"); return r; }
  catch (e) { toast("✕ " + e.message, "bad"); return null; }
};
window.l2Renew = name => {
  const v = prompt(`Days to add to ${name}`, "30"); if (v === null) return;
  const days = parseInt(v, 10);
  if (!(days >= 1 && days <= 3650)) { toast("✕ Days is a number from 1 to 3650", "bad"); return; }
  const reset = confirm(`Start the traffic of ${name} from zero again (a new period)?\n\nOK = yes, count from zero.  Cancel = keep what it has used.`);
  l2Do(`/api/l2tp/accounts/${encodeURIComponent(name)}/renew`, {method: "POST", body: JSON.stringify({days, reset_usage: reset})}, `${name}: ${days} days added${reset ? ", traffic from zero" : ""}`);
};
window.l2Kick = name => { if (confirm(`Disconnect the devices of ${name} now? They can connect again.`)) l2Do(`/api/l2tp/accounts/${encodeURIComponent(name)}/kick`, {method: "POST"}, `${name}: disconnected`); };
window.l2Del = (name, bot) => { if (confirm(`Delete the account ${name}?${bot ? "\n\nThe shop bot made this one: the customer's order will point at an account that is gone." : ""}\n\nIts devices are disconnected and it cannot be brought back.`)) { hideModal(); l2Do(`/api/l2tp/accounts/${encodeURIComponent(name)}`, {method: "DELETE"}, `${name} deleted`); } };
