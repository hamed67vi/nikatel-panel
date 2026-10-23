/* Settings → Api & Key → "Bale · customer notices" (the owner, 2026-10-23: "when the customer's internet in Iran is cut, as in the war, and many protocols are closed, I want a way to talk to them" - "go on with the domestic messengers").
   Telegram and every server abroad are out of reach in a cut, Bale (a messenger inside Iran) and an Iran server are not. The box installs a small Bale bot (relay/bale_relay.py) on ONE Iran server: customers start it and link their
   subscription, and it tells everybody who started it - by itself when that server loses the outside world for a few minutes, and again when it is back, or when you send a notice from here.
   Routes used: GET|PUT /api/bale, GET /api/bale/status, POST /api/bale/install, POST /api/bale/send, POST /api/bale/remove, GET /api/servers?role=iran.
   Uses the helpers of app.js: $, api, esc, toast, gconfirm, ago, when, tip, SI, abBox, abF, fIc, sIc, sSel, pgSw. */
const bale = {d: null, servers: [], st: null};
const BALE_TEXTS = [["welcome", "Welcome (/start)", "the first message, and the help"], ["linked", "Subscription linked", "{username} becomes the name of the subscription"], ["bad_link", "Link not known", "a link from another site, or one the panel does not know"],
  ["cannot_check", "Cannot check now", "the panel did not answer (or too many tries)"], ["no_links", "Nothing linked yet", "/status without a subscription"], ["stopped", "After /stop", "the customer stopped the notices"],
  ["unlinked", "After /unlink", "the subscriptions were taken off"], ["support_ack", "Customer wrote to you", "the answer to a customer who writes to you here (once in ten minutes)"], ["support_fail", "Could not pass it on", "when none of your admin ids could be reached"], ["cut", "The cut notice (sent by itself)", "sent when the server has lost the outside world for the time set above"], ["restored", "The back notice (sent by itself)", "sent when it has been back for a while"]];

window.baleBoxHtml = () => abBox({icon: SI.send, title: "Bale · customer notices", pill: '<span class="pill" id="bale-pill">…</span>', sub: "reaches the customers while the international internet is cut",
  tip: "Telegram is out of reach in a cut, Bale and an Iran server are not. A small Bale bot runs on one Iran server: customers start it, and it tells them when the internet is cut and when it is back.",
  body: '<div id="bale-box"><div class="mut">loading…</div></div>'});

const baleErr = e => toast("✕ " + e.message, "bad");
const baleIds = v => String(v || "").split(/[\s,;]+/).filter(Boolean);

window.baleLoad = async () => {
  const box = $("#bale-box"); if (!box) return;
  try { [bale.d, bale.servers] = await Promise.all([api("/api/bale"), api("/api/servers?role=iran")]); } catch (e) { box.innerHTML = `<div class="mut err">${esc(e.message)}</div>`; return; }
  baleDraw();
  if (bale.d.installed) baleStatus();
};

function baleDraw() {
  const box = $("#bale-box"), d = bale.d; if (!box) return;
  const srv = bale.servers, cur = d.server_id || (srv[0] || {}).id;
  const hosts = (d.hosts.length ? d.hosts : d.suggest_hosts).join("\n");
  const pill = $("#bale-pill"); if (pill) { pill.textContent = d.installed ? "installed" : d.has_token ? "token saved" : "not set"; pill.className = "pill " + (d.installed ? "ok" : d.has_token ? "warn" : "err"); }
  const form = `<form id="bale-form" onsubmit="return baleSave(event)"><div class="ab-grid">`
    + abF("Bot token", fIc(SI.key, `<input name="token" type="password" placeholder="${d.has_token ? "•••••••••• saved" : "123456789:AA…"}" autocomplete="off" title="make a bot with @botfather in Bale; empty keeps the saved one">`, true))
    + abF("Your Bale ids (admins)", fIc(SI.user, `<input name="admins" value="${esc(d.admins.join(", "))}" placeholder="send /id to the bot to see yours" autocomplete="off" title="your own Bale id (send /id to the bot). Customers' messages arrive in this chat and you answer them by replying; you can also run everything from it with nothing but Bale: /send, /cut, /back, /info, /stats, /mute">`))
    + abF("Iran server", sIc(SI.server, sSel("server_id", srv.map(s => [s.id, s.name]), cur, `data-dd ${d.installed ? "disabled" : ""}`)))
    + abF("Say it after (seconds)", fIc(SI.clock, `<input name="cut_after" type="number" min="30" max="3600" value="${d.cut_after}" title="how long the server must have lost the whole outside world before the customers are told">`))
    + abF("Back after (seconds)", fIc(SI.clock, `<input name="restore_after" type="number" min="10" max="3600" value="${d.restore_after}" title="how long it must have been back before the customers are told">`))
    + `<div class="abf"><span>Tell the customers by itself</span><span class="ab-sw">${pgSw(!!d.auto_notice, "", 'name="auto_notice"')}</span></div>`
    + `<div class="abf"><span>Customers can write to you here</span><span class="ab-sw">${pgSw(!!d.support, "", 'name="support"')}</span></div>`
    + abF("Hosts of the subscription links", `<textarea name="hosts" rows="2" spellcheck="false" placeholder="sub.example.com" title="one per line. A customer's link is accepted only on these hosts (the first guess is the host of your panels), so nobody can make the server fetch other addresses">${esc(hosts)}</textarea>`, "ab-wide")
    + `</div><div class="ab-row ab-btns"><button class="pri sm" type="submit"><i class="bi">${SI.save}</i>Save</button>`
    + `<button class="sm" type="button" onclick="baleInstall()" title="${d.installed ? "Copy the newest program to the server and restart it" : "Copy the program to the Iran server and start it (nothing else on the server is touched)"}"><i class="bi">${SI.down}</i>${d.installed ? "Update on the server" : "Install on the server"}</button>`
    + (d.installed ? `<button class="sm danger" type="button" onclick="baleRemove()" title="Stop it and take it off the server; the list of customers stays there"><i class="bi">${SI.trash}</i>Remove</button>` : "") + `</div></form>`;
  const texts = `<details class="ab-more" style="margin-top:12px"><summary class="mut" style="cursor:pointer">Messages the bot sends (empty = the default)</summary><form id="bale-texts" onsubmit="return baleSaveTexts(event)" style="display:grid;gap:10px;margin-top:10px">`
    + BALE_TEXTS.map(([k, name, hint]) => `<label class="abf"><span title="${esc(hint)}">${esc(name)}</span><textarea name="${k}" rows="3" dir="rtl" spellcheck="false" placeholder="${esc(d.defaults[k] || "")}">${esc((d.texts || {})[k] || "")}</textarea></label>`).join("")
    + `<div class="ab-row"><button class="pri sm" type="submit"><i class="bi">${SI.save}</i>Save the messages</button></div></form></details>`;
  const warn = d.support && !d.admins.length ? `<div class="err" style="margin-bottom:10px">Customers can write to you, but no admin id is set: their messages go nowhere. Send /id to your bot in Bale and put the number in "Your Bale ids".</div>` : "";
  box.innerHTML = `<div id="bale-live"></div>${warn}${form}${d.installed ? baleSendForm() : ""}${texts}`;
}

const baleSendForm = () => `<div class="abf" style="margin-top:14px"><span>Send a notice now</span><textarea id="bale-text" rows="3" dir="rtl" maxlength="4000" placeholder="متن پیام برای مشتریان" spellcheck="false"></textarea></div>`
  + `<div class="ab-row ab-btns" style="margin-top:8px"><button class="sm" type="button" onclick="baleSend(true)" title="Only to your admin ids, to see how it looks"><i class="bi">${SI.play}</i>Test to the admins</button>`
  + `<button class="pri sm" type="button" onclick="baleSend(false)" title="To everybody who started the bot"><i class="bi">${SI.send}</i>Send to every customer</button></div>`;

async function baleStatus() {
  const live = $("#bale-live"); if (!live) return;
  live.innerHTML = '<div class="mut" style="margin-bottom:10px">asking the server…</div>';
  let s; try { s = await api("/api/bale/status"); } catch (e) { live.innerHTML = `<div class="err" style="margin-bottom:10px">✕ ${esc(e.message)}</div>`; return; }
  const r = s.status || {}, u = r.users || {}, w = r.watch || {}, bl = r.bale || {}, bot = (r.bot || {}).username;
  const run = s.unit === "active", pill = $("#bale-pill");
  const state = !run ? ["err", "not running"] : bl.err ? ["warn", "Bale not reachable"] : s.age != null && s.age > 120 ? ["warn", "no news for " + Math.round(s.age / 60) + " min"] : ["ok", "● running"];
  if (pill) { pill.textContent = state[1]; pill.className = "pill " + state[0]; }
  const line = (k, v) => `<span style="display:inline-flex;gap:6px;margin-right:16px"><em style="font-style:normal;opacity:.7">${k}</em><b>${v}</b></span>`;
  const sent = (r.broadcasts || []).slice(-4).reverse().map(b => `<div class="ab-li"><span class="ab-ln"><b>${esc({cut: "the cut notice", restored: "the back notice", manual: "a notice", test: "a test"}[b.kind] || b.kind)}</b><small>${b.done_at ? when(b.done_at) : "sending…"} · ${b.sent} sent${b.failed ? ", " + b.failed + " failed" : ""}${b.blocked ? ", " + b.blocked + " left" : ""}</small></span></div>`).join("");
  live.innerHTML = `<div style="margin-bottom:12px;line-height:1.9">`
    + line("Server", esc(s.server)) + line("Bot", bot ? `<a href="https://ble.ir/${esc(bot)}" target="_blank" rel="noopener">@${esc(bot)}</a>` : "—")
    + line("Customers", `${u.total || 0} started · ${u.linked || 0} linked`) + (u.muted || u.blocked ? line("Left", `${u.muted || 0} stopped · ${u.blocked || 0} blocked`) : "")
    + line("Outside world", w.up === false ? `<span class="err">CUT since ${when(w.down_since)}${w.notified ? " (customers told)" : ""}</span>` : "reachable")
    + (bl.err ? `<div class="err" style="margin-top:4px">Bale: ${esc(bl.err)}${s.dns ? "" : " · this server does not resolve tapi.bale.ai"}</div>` : "")
    + (r.queue ? `<div class="mut">${r.queue} notice(s) waiting to be sent</div>` : "") + `</div>` + (sent ? `<div class="ab-list" style="margin-bottom:12px">${sent}</div>` : "");
}

const baleForm = () => {      // what the form holds, as the body of PUT /api/bale (a token is sent only when one was typed)
  const f = new FormData($("#bale-form")), o = {admins: baleIds(f.get("admins")), hosts: String(f.get("hosts") || "").split(/[\n,]+/).map(x => x.trim()).filter(Boolean), cut_after: +f.get("cut_after"), restore_after: +f.get("restore_after"), auto_notice: f.get("auto_notice") === "on", support: f.get("support") === "on"};
  if (String(f.get("token") || "").trim()) o.token = String(f.get("token")).trim();
  return o;
};
window.baleSave = async e => {
  e.preventDefault(); const o = baleForm();
  try { const r = await api("/api/bale", {method: "PUT", body: JSON.stringify(o)}); toast(r.installed ? (r.pushed ? "✓ Saved and sent to the server" : "✓ Saved here, but the server did not get it: " + r.push_error) : "✓ Saved", r.installed && !r.pushed ? "bad" : "ok"); baleLoad(); } catch (err) { baleErr(err); }
  return false;
};
window.baleSaveTexts = async e => {
  e.preventDefault(); const f = new FormData(e.target), t = {}; for (const [k] of BALE_TEXTS) t[k] = String(f.get(k) || "");
  try { const r = await api("/api/bale", {method: "PUT", body: JSON.stringify({texts: t})}); toast(r.installed ? (r.pushed ? "✓ Messages saved and sent to the server" : "✓ Saved here, but the server did not get it: " + r.push_error) : "✓ Messages saved", "ok"); baleLoad(); } catch (err) { baleErr(err); }
  return false;
};
window.baleInstall = async () => {
  const sel = $("#bale-form select[name=server_id]"), sid = sel ? +sel.value : 0, d = bale.d, o = baleForm();
  if (!sid) return toast("✕ Add an Iran server first (Servers)", "bad");
  if (!d.has_token && !o.token) return toast("✕ Paste the bot token first (make a bot with @botfather in Bale)", "bad");
  if (!await gconfirm(d.installed ? "Copy the newest Bale program to the server and restart it? It is off for a second or two." : "Install the Bale bot on this Iran server? It copies one small program and starts one service. Nothing else on the server is touched.")) return;
  try {
    await api("/api/bale", {method: "PUT", body: JSON.stringify(o)});          // what the form holds is saved first: the install uses it
    toast("Installing…"); const r = await api(`/api/bale/install?server_id=${sid}`, {method: "POST"});
    toast(r.bale_error ? `✓ Installed on ${r.server}, but Bale is not reachable from it: ${r.bale_error}` : `✓ Installed on ${r.server}${r.bot ? " · bot @" + r.bot : ""}`, r.bale_error ? "bad" : "ok"); baleLoad();
  } catch (err) { baleErr(err); }
};
window.baleSend = async adminsOnly => {
  const text = ($("#bale-text").value || "").trim(); if (!text) return toast("✕ Write the notice first", "bad");
  if (!adminsOnly && !await gconfirm("Send this notice to EVERY customer who started the bot?\n\n" + text.slice(0, 300))) return;
  try { await api("/api/bale/send", {method: "POST", body: JSON.stringify({text, admins_only: !!adminsOnly})}); toast(adminsOnly ? "✓ Test queued for the admins" : "✓ Queued: the server sends it within seconds", "ok"); if (!adminsOnly) $("#bale-text").value = ""; setTimeout(baleStatus, 6000); } catch (err) { baleErr(err); }
};
window.baleRemove = async () => {
  if (!await gconfirm("Stop the Bale bot and take it off the server? The list of customers who started it stays on the server, so a later install finds them again.")) return;
  try { const r = await api("/api/bale/remove", {method: "POST", body: JSON.stringify({purge: false})}); toast(`✓ Removed from ${r.server}`, "ok"); baleLoad(); } catch (err) { baleErr(err); }
};
