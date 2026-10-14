/* PasarGuard Users: the window of ONE user (app.js hands over to pgUserOpen for a PasarGuard panel) and the tools of the Users list (the "More…" menu).
   The window: usage, the account (status, data limit, reset, end date or ON HOLD days, devices limit, owner, note), the groups, the NEXT PLAN, and the activity (devices, the last
   fetches of the subscription, what the subscription answers in each format, where the user is online now). Only what differs is sent; a next plan the user has goes back with every save (the panel deletes it otherwise).
   The tools: for the ticked users (owner, template, reset, new links, delete) and for many (shift end dates or data limits, or set the Shadowsocks method, by a filter - counted first -, clean up expired / limited /
   on hold / disabled users, new users from a template). Everything goes through /api/panels/{id}/pg/user and /pg/user-tools. */
const pgU = {id: 0, d: null, act: "devices", cache: {}, fmt: "links"};
const pgUMs = v => typeof v === "number" ? v * 1000 : Date.parse(v || "") || 0;
const pgUAgo = v => { const t = pgUMs(v); return t ? ago(t / 1000) + " ago" : "never"; };
const pgUWhen = v => { const t = pgUMs(v); return t ? new Date(t).toLocaleString([], {dateStyle: "medium", timeStyle: "short"}) : "—"; };
const pgUEnc = () => encodeURIComponent(pgU.d.user.username);

/* ---------------------------------------------------------------- the window of one user */
window.pgUserOpen = async (id, name) => {
  let d; try { d = await pgApi(id, `/user/${encodeURIComponent(name)}`); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  Object.assign(pgU, {id, d, act: "devices", cache: {}});
  pgUserShow(); pgUserAct("devices");
};
async function pgUReload() {
  try { pgU.d = await pgApi(pgU.id, `/user/${pgUEnc()}`); pgU.cache = {}; pgUserShow(); pgUserAct(pgU.act); } catch (e) { toast("✕ " + e.message, "bad"); }
  pn.users = null; refresh();
}
const pgUPlanOf = u => { const p = u.next_plan || {}; return {template_id: p.template_id || null, data_gb: p.data_gb ?? null, expire_days: p.expire_days ?? null, add_remaining: !!p.add_remaining}; };

function pgUserShow() {
  const {user: u, options: o} = pgU.d, id = pgU.id, nm = esc(u.username), lim = u.limit || 0, used = u.used || 0, pct = lim ? Math.min(100, Math.round(used * 100 / lim)) : 0;
  const em = pgUMs(u.expire), left = em ? Math.ceil((em - Date.now()) / 86400000) : null, hold = u.status === "on_hold", off = u.status === "disabled", np = u.next_plan || {};
  const kv = (l, v) => `<div class="kv2"><span>${l}</span><div>${v}</div></div>`;
  const fld = (l, inner, fid = "", hid = false) => `<label class="kv2 kf"${fid ? ` id="${fid}"` : ""}${hid ? " hidden" : ""}><span>${l}</span>${inner}</label>`;
  const box = (icon, title, rows, cls = "") => `<div class="sbox ${cls}"><h5><i>${icon}</i>${title}</h5>${rows}</div>`;
  const b = (icon, title, tip, click, cls = "") => `<button type="button" class="sm abtn ${cls}" onclick="${click}" title="${esc(tip)}"><i>${icon}</i>${title}</button>`;
  const sel = (name, opts, cur, attrs = "") => `<select name="${name}" ${attrs}>${opts.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(cur) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
  const sub = u.sub || "", odd = !["active", "disabled", "on_hold"].includes(u.status);
  const admins = o.admins.map(a => a.name); if (u.admin && !admins.includes(u.admin)) admins.unshift(u.admin);
  const bar = `<div class="sbar ue-acts"><span class="sg">${b("↺", "Reset usage", "Set the used traffic back to zero", "pgUserDo('reset')")}${b("🔑", "Revoke subscription", "A new subscription link: the old one stops working", "pgUserDo('revoke_sub')")}</span><i class="sep"></i>
    <span class="sg">${b(off ? "▶" : "⏸", off ? "Enable" : "Disable", off ? "Let the user connect again" : "Stop the user from connecting", `pgUserDo('${off ? "enable" : "disable"}')`)}${u.next_plan ? b("⏭", "Start next plan", "Start the next plan now: its data and duration replace the current ones", "pgUserDo('active_next')") : ""}</span><span class="sp"></span>
    ${b("🗑", "Delete user", "Remove the user from the panel", "pgUserDo('delete')", "danger")}</div>`;
  const usage = box("📊", "Usage", `<div class="ue-big"><b>${fmtB(used)}</b><span>${lim ? "of " + gb(lim) + " · " + pct + " %" : "unlimited"}</span></div>${lim ? `<div class="ue-bar"><i class="${pct >= 95 ? "bad" : pct >= 80 ? "warn" : ""}" style="width:${pct}%"></i></div>` : ""}`
    + kv("Left", lim ? fmtB(Math.max(0, lim - used)) : "—")
    + kv("Expires", hold ? `after its first connection${u.on_hold_days ? ` <small class="mut">+${u.on_hold_days} d</small>` : ""}` : u.expire_day ? `${esc(u.expire_day)} <small class="${left < 0 ? "err" : "mut"}">${left < 0 ? "expired" : left + " d"}</small>` : "never")
    + kv("Online", esc(pgUAgo(u.online_at)))
    + `<small class="mut pgu-meta">Lifetime ${fmtB(u.lifetime || 0)} · created ${esc(pgUWhen(u.created_at))}${u.edit_at ? " · edited " + esc(pgUAgo(u.edit_at)) : ""}</small>`, "ue-usage");
  const account = box("👤", "Account", fld("Status", sel("status", [...(odd ? [[u.status, u.status + " (by the panel)"]] : []), ["active", "active"], ["disabled", "disabled"], ["on_hold", "on hold"]], u.status, `onchange="pgUserSt(this)"`))
    + fld("Data limit", `<input name="limit_gb" type="number" step="0.1" min="0" value="${u.limit_gb ?? 0}" title="GB, 0 = unlimited"><em>GB</em>`)
    + fld("Reset usage", sel("reset", ["no_reset", "day", "week", "month", "year"].map(s => [s, s]), u.reset))
    + fld("Expires", `<input name="expire_day" type="date" value="${esc(u.expire_day)}" title="empty = never">`, "pgu-f-exp", hold)
    + fld("On hold for", `<input name="on_hold_days" type="number" step="0.5" min="0" value="${u.on_hold_days ?? ""}" placeholder="days" title="After its first connection the user stays valid for this many days"><em>days</em>`, "pgu-f-hold", !hold)
    + fld("Starts anyway", `<input name="on_hold_timeout_day" type="date" value="${esc(u.on_hold_timeout_day)}" title="A user on hold that has not connected by this day starts counting anyway. Empty = it waits">`, "pgu-f-tmo", !hold)
    + fld("Devices", `<input name="hwid_limit" type="number" step="1" min="0" value="${u.hwid_limit ?? ""}" placeholder="panel rule" title="How many devices may use this user. Empty = the rule of the panel">`)
    + (admins.length ? fld("Owner", sel("owner", admins.map(a => [a, a]), u.admin), "", false) : "")
    + fld("Note", `<input name="note" maxlength="500" value="${esc(u.note)}" placeholder="optional">`)
    + (sub ? kv("Link", `<span class="mono ell ue-link" title="Click to copy the subscription link" onclick="copyFrom(this, '${esc(sub)}')">${esc(sub)}</span>`) : ""), "ue-account");
  const groups = box("👥", `Groups <small class="mut pgu-gn"></small>`, `<div class="pgu-groups">${o.groups.map(g => `<label class="chk" title="${esc(g.name)}"><input type="checkbox" name="grp" value="${g.id}" ${u.group_ids.includes(g.id) ? "checked" : ""} onchange="pgUserGn()"><span>${esc(g.name)}</span></label>`).join("") || '<span class="mut">this panel has no group</span>'}</div>`
    + `<small class="mut">The groups decide which inbounds (and so which hosts) the user gets.</small>`, "pgu-grp pgu-row");
  const plan = box("⏭", "Next plan", fld("Template", sel("np_tpl", [["", "own values"], ...o.templates.map(t => [t.id, t.name])], np.template_id || "", `onchange="pgUserNp(this)"`))
    + fld("Data", `<input name="np_gb" type="number" step="0.1" min="0" value="${np.data_gb ?? ""}" placeholder="GB, 0 = unlimited"><em>GB</em>`, "pgu-f-npg", !!np.template_id)
    + fld("Duration", `<input name="np_days" type="number" step="1" min="0" value="${np.expire_days ?? ""}" placeholder="days, empty = no end"><em>days</em>`, "pgu-f-npd", !!np.template_id)
    + `<div class="kv2 kf"><span>Add the rest</span>${pgSw(!!np.add_remaining, "", 'name="np_add" title="Add what is left of the current data to the new plan"')}</div>`
    + `<small class="mut">Starts by itself when the user runs out of data or time (or with “Start next plan”). Everything empty = no next plan.</small>`, "pgu-plan");
  const act = `<div class="sbox pgu-act pgu-row"><h5><i>📡</i>Activity<span class="sp"></span><span class="pg-admin-seg">${[["devices", "Devices"], ["subs", "Subscription"], ["format", "Formats"], ["online", "Online now"]].map(([k, l]) => `<button type="button" class="${pgU.act === k ? "on" : ""}" data-k="${k}" onclick="pgUserAct('${k}')">${l}</button>`).join("")}</span></h5><div id="pgu-actbody"></div></div>`;
  showModal(`<span class="st ${u.status === "active" ? "online" : u.status === "disabled" ? "" : "offline"}"></span><b>${nm}</b> <span class="pill">${esc(String(u.status).replace("_", " "))}</span>${u.admin ? ` <span class="pill" title="The admin that owns this user">${esc(u.admin)}</span>` : ""}`,
    `${bar}<form class="frm ue-form" onsubmit="return pgUserSave(event)"><div class="sgrid ue-grid two">${account}<div class="pgu-col">${usage}${plan}</div></div>${groups}${act}
    <div class="frm-act ue-save"><button class="pri" type="submit">Save</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`, "wide ue pgu");
  pgUserGn();
}
window.pgUserGn = () => { const xs = [...document.querySelectorAll('#modal input[name="grp"]')], c = document.querySelector("#modal .pgu-gn"); if (c) c.textContent = xs.length ? `${xs.filter(x => x.checked).length} of ${xs.length}` : ""; };
window.pgUserSt = sel => { const hold = sel.value === "on_hold"; [["pgu-f-exp", hold], ["pgu-f-hold", !hold], ["pgu-f-tmo", !hold]].forEach(([i, h]) => { const el = $("#" + i); if (el) el.hidden = h; }); };
window.pgUserNp = sel => { const own = !sel.value; ["pgu-f-npg", "pgu-f-npd"].forEach(i => { const el = $("#" + i); if (el) el.hidden = !own; }); };

window.pgUserSave = async e => {
  e.preventDefault();
  const form = e.target, f = form.elements, u = pgU.d.user, enc = pgUEnc(), body = {}, did = [];
  body.status = f.status.value; body.limit_gb = f.limit_gb.value === "" ? 0 : +f.limit_gb.value; body.reset = f.reset.value; body.note = f.note.value;
  body.group_ids = [...form.querySelectorAll('input[name="grp"]:checked')].map(x => +x.value);
  body.expire_day = body.status === "on_hold" ? "" : f.expire_day.value;
  if (body.status === "on_hold") { body.on_hold_days = f.on_hold_days.value === "" ? null : +f.on_hold_days.value; body.on_hold_timeout_day = f.on_hold_timeout_day.value; }
  if (f.hwid_limit.value !== "") body.hwid_limit = +f.hwid_limit.value; else if (u.hwid_limit != null) body.hwid_limit = null;      // (the panel answers that a limit is changed, not taken away)
  const plan = {template_id: f.np_tpl.value ? +f.np_tpl.value : null, data_gb: f.np_gb.value === "" ? null : +f.np_gb.value, expire_days: f.np_days.value === "" ? null : +f.np_days.value, add_remaining: f.np_add.checked};
  if (plan.template_id) { plan.data_gb = null; plan.expire_days = null; }
  const was = pgUPlanOf(u); if (was.template_id) { was.data_gb = null; was.expire_days = null; }
  if (JSON.stringify(plan) !== JSON.stringify(was)) body.next_plan = plan.template_id || plan.data_gb !== null || plan.expire_days !== null ? plan : null;      // an empty plan = none wanted
  const btn = form.querySelector("button[type=submit]"); btn.disabled = true;
  try {
    if (f.owner && f.owner.value && f.owner.value !== u.admin) { await pgApi(pgU.id, `/user/${enc}/action`, {method: "POST", body: {what: "set_owner", value: f.owner.value}}); did.push("owner"); }
    try { const r = await pgApi(pgU.id, `/user/${enc}`, {method: "PUT", body}); did.push(...r.changed); }
    catch (err) { if (!/nothing to change/i.test(err.message)) throw err; }
    if (!did.length) { toast("Nothing changed"); btn.disabled = false; return false; }
    toast(`✓ ${u.username} saved (${did.map(x => x.replace(/_/g, " ")).join(", ")})`, "ok"); pn.users = null; closeModal(); refresh();
  } catch (err) { toast("✕ " + err.message, "bad"); btn.disabled = false; if (did.length) pgUReload(); }
  return false;
};

window.pgUserDo = async what => {
  const nm = pgU.d.user.username, ask = {reset: `Set the used traffic of ${nm} back to zero?${pgU.d.user.next_plan ? " The panel also removes the next plan of the user when its usage is reset." : ""}`, revoke_sub: `Make a new subscription link for ${nm}? The old link stops working.`,
    active_next: `Start the next plan of ${nm} now? Its data and duration replace the current ones and the used traffic starts again from zero.`, delete: `Delete ${nm} from the panel? This cannot be undone.`}[what];
  if (ask && !await gconfirm(ask, {danger: what === "delete" || what === "revoke_sub"})) return;
  try {
    await pgApi(pgU.id, `/user/${pgUEnc()}/action`, {method: "POST", body: {what, confirm: what === "delete"}});
    toast(`✓ ${nm}: ${what.replace("_", " ")} done`, "ok"); pn.users = null;
    if (what === "delete") { closeModal(); refresh(); } else await pgUReload();
  } catch (e) { toast("✕ " + e.message, "bad"); }
};

/* the activity box: devices, the last fetches of the subscription, where the user is online now (each read the first time it is opened) */
const pgUDevRow = d => [d.model, d.os && (d.os + (d.version ? " " + d.version : ""))].filter(Boolean).join(" · ") || "unknown device";
window.pgUserAct = async k => {
  pgU.act = k; document.querySelectorAll("#modal .pgu-act .pg-admin-seg button").forEach(x => x.classList.toggle("on", x.dataset.k === k));
  const box = $("#pgu-actbody"); if (!box) return;
  if (k === "format") { box.innerHTML = pgUFmtHtml(); return pgUserFmtLoad(pgU.fmt); }
  const path = {devices: "devices", subs: "sub-updates", online: "online"}[k];
  if (!pgU.cache[k]) {
    box.innerHTML = '<div class="loading"><i class="spin"></i><span>Reading…</span></div>';
    try { pgU.cache[k] = await pgApi(pgU.id, `/user/${pgUEnc()}/${path}`); } catch (e) { if (pgU.act === k && box.isConnected) box.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  }
  if (pgU.act !== k || !box.isConnected) return;
  const r = pgU.cache[k], tb = (cols, rows, none) => `<table class="tbl pgu-tbl"><colgroup>${cols.map(c => `<col${c[1] ? ` style="width:${c[1]}"` : ""}>`).join("")}</colgroup><thead><tr>${cols.map(c => `<th>${c[0]}</th>`).join("")}</tr></thead><tbody>${rows || `<tr><td colspan="${cols.length}" class="mut">${none}</td></tr>`}</tbody></table>`;
  if (k === "devices") box.innerHTML = tb([["Device"], ["ID", "130px"], ["First seen", "100px"], ["Last used", "100px"], ["", "44px"]],
    r.devices.map((d, i) => `<tr><td class="name"><div class="ell" title="${esc(pgUDevRow(d))}">${esc(pgUDevRow(d))}</div></td><td class="mono mut ell" title="${esc(d.hwid)}">${esc(d.short)}</td><td class="mut">${esc(pgUAgo(d.first))}</td><td class="mut">${esc(pgUAgo(d.last))}</td><td><button type="button" class="sm danger" onclick="pgUserDev(${i})" title="Remove this device">✕</button></td></tr>`).join(""), "no device has connected yet")
    + (r.devices.length ? `<div class="pgu-foot"><button type="button" class="sm danger" onclick="pgUserDevAll()" title="Remove every device of this user">Remove all ${r.count} devices</button></div>` : "");
  else if (k === "subs") box.innerHTML = tb([["When", "150px"], ["App"], ["Address", "140px"]], r.updates.map(x => `<tr><td class="mut">${esc(pgUWhen(x.at))}</td><td class="name"><div class="ell" title="${esc(x.agent)}">${esc(x.agent || "—")}</div></td><td class="mono mut">${esc(x.ip || "—")}</td></tr>`).join(""), "the link has not been fetched yet")
    + (r.count > r.updates.length ? `<small class="mut">The last ${r.updates.length} of ${r.count} fetches.</small>` : "");
  else box.innerHTML = tb([["Node"], ["Address", "180px"], ["Connections", "110px"]], r.online.map(x => `<tr><td class="name">${esc(x.node)}</td><td class="mono">${esc(x.ip)}</td><td class="mut">${x.connections}</td></tr>`).join(""), "not connected to any node right now");
};
/* "Formats": what the subscription of this user answers in each client format, as the panel renders it (to see why a link does not work in one app, or to copy the config by hand) */
const pgUFormats = [["links", "Links"], ["links_base64", "Links (base64)"], ["xray", "Xray JSON"], ["sing_box", "sing-box JSON"], ["clash", "Clash"], ["clash_meta", "Clash Meta"], ["outline", "Outline"], ["wireguard", "WireGuard (zip)"]];
const pgUFmtHtml = () => `<div class="pgu-fmtbar"><label>Show as<select onchange="pgUserFmtLoad(this.value)">${pgUFormats.map(([v, l]) => `<option value="${v}" ${v === pgU.fmt ? "selected" : ""}>${l}</option>`).join("")}</select></label><span class="mut" id="pgu-fmtinfo"></span><span class="sp"></span><button type="button" class="sm" id="pgu-fmtcopy" disabled onclick="pgUserFmtCopy(this)">Copy</button></div><div id="pgu-fmtbody"></div>`;
window.pgUserFmtLoad = async f => {
  pgU.fmt = f; const body = $("#pgu-fmtbody"), info = $("#pgu-fmtinfo"), cp = $("#pgu-fmtcopy"); if (!body) return;
  const key = "fmt:" + f;
  if (!pgU.cache[key]) {
    body.innerHTML = '<div class="loading"><i class="spin"></i><span>Asking the panel…</span></div>'; info.textContent = ""; cp.disabled = true;
    try { pgU.cache[key] = await pgApi(pgU.id, `/user/${pgUEnc()}/subscription?format=${encodeURIComponent(f)}`); }
    catch (e) { if (pgU.fmt === f && body.isConnected) body.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  }
  if (pgU.fmt !== f || !body.isConnected) return;
  const r = pgU.cache[key], bytes = r.size.toLocaleString() + " bytes";
  info.textContent = r.binary ? bytes + " · zip file" : `${bytes} · ${r.type}${r.truncated ? " · cut" : ""}`;
  cp.disabled = r.binary || r.empty;
  body.innerHTML = r.binary ? `<div class="mut pgu-fmtnote">WireGuard is answered as a zip file (${bytes}${r.empty ? "; empty: this user has no WireGuard config" : ""}). A browser downloads it from the subscription link of the user.</div>`
    : r.empty ? '<div class="mut pgu-fmtnote">The panel answers this format with nothing: the user has no config of this kind.</div>' : `<textarea class="pgu-fmtta" readonly spellcheck="false">${esc(r.text)}</textarea>`;
};
window.pgUserFmtCopy = btn => { const r = pgU.cache["fmt:" + pgU.fmt]; if (r && r.text) copyFrom(btn, r.text); };
window.pgUserDev = async i => {
  const d = (pgU.cache.devices || {}).devices[i]; if (!d) return;
  if (!await gconfirm(`Remove the device “${pgUDevRow(d)}” of ${pgU.d.user.username}? It can connect again (and then counts as a new device).`, {danger: true})) return;
  try { await pgApi(pgU.id, `/user/${pgUEnc()}/devices?hwid=${encodeURIComponent(d.hwid)}`, {method: "DELETE"}); toast("✓ Device removed", "ok"); delete pgU.cache.devices; pgUserAct("devices"); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.pgUserDevAll = async () => {
  if (!await gconfirm(`Remove every device of ${pgU.d.user.username}? All of them can connect again (and count as new devices).`, {danger: true})) return;
  try { await pgApi(pgU.id, `/user/${pgUEnc()}/devices/reset`, {method: "POST"}); toast("✓ All devices removed", "ok"); delete pgU.cache.devices; pgUserAct("devices"); } catch (e) { toast("✕ " + e.message, "bad"); }
};

/* ---------------------------------------------------------------- the tools of the Users list */
window.pgUsersMenu = () => pnType() !== "pasarguard" ? "" : `<select class="sm pgu-tools" title="More things to do with users" onchange="pgUsersTool(this)"><option value="">More…</option>
  <optgroup label="The ticked users"><option value="owner">Set owner…</option><option value="template">Apply a template…</option><option value="reset">Reset usage…</option><option value="revoke">New subscription links…</option><option value="delete">Delete…</option></optgroup>
  <optgroup label="Many users"><option value="expire">Shift end dates…</option><option value="data">Shift data limits…</option><option value="ssmethod">Shadowsocks method…</option><option value="clean">Clean up expired…</option></optgroup>
  <optgroup label="New users"><option value="new1">New user from a template…</option><option value="newn">Many users from a template…</option></optgroup></select>`;
const pgUTicked = () => [...document.querySelectorAll(".pn-uchk:checked")].map(x => x.value);
const pgUOpts = async () => (pgUOpts.c && pgUOpts.c.id === pn.id && Date.now() - pgUOpts.c.at < 20000) ? pgUOpts.c.d : (pgUOpts.c = {id: pn.id, at: Date.now(), d: await pgApi(+pn.id, "/user-tools/options")}).d;
const pgUList = names => names.slice(0, 3).join(", ") + (names.length > 3 ? ` and ${names.length - 3} more` : "");
window.pgUsersTool = async sel => {
  const v = sel.value; sel.selectedIndex = 0; if (!v) return;
  const id = +pn.id;
  if (["expire", "data", "ssmethod"].includes(v)) return pgShiftOpen(v);
  if (v === "clean") return pgCleanOpen();
  if (v === "new1" || v === "newn") return pgNewOpen(v === "newn");
  const names = pgUTicked(); if (!names.length) { toast("Tick some users first", "bad"); return; }
  const done = async (what, value, ok) => { try { const r = await pgApi(id, "/user-tools/bulk", {method: "POST", body: {what, usernames: names, value, confirm: true}}); toast(`✓ ${ok} for ${r.count} user${r.count === 1 ? "" : "s"}`, "ok"); pn.users = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
  if (v === "reset") { if (await gconfirm(`Set the used traffic of ${names.length} user${names.length === 1 ? "" : "s"} (${pgUList(names)}) back to zero? The panel also removes the next plan of a user whose usage is reset.`)) done("reset", null, "Usage reset"); }
  else if (v === "revoke") { if (await gconfirm(`Make new subscription links for ${names.length} user${names.length === 1 ? "" : "s"} (${pgUList(names)})? The old links stop working.`, {danger: true})) done("revoke_sub", null, "New links made"); }
  else if (v === "delete") { const t = await gprompt(`Delete ${names.length} user${names.length === 1 ? "" : "s"} (${pgUList(names)}) for good? This cannot be undone. Type delete to confirm.`, "", {danger: true}); if (t && t.trim().toLowerCase() === "delete") done("delete", null, "Deleted"); else if (t !== null) toast("Not deleted: the word did not match", "bad"); }
  else if (v === "owner" || v === "template") {
    let o; try { o = await pgUOpts(); } catch (e) { toast("✕ " + e.message, "bad"); return; }
    const list = v === "owner" ? o.admins.map(a => [a.name, a.name]) : o.templates.map(t => [t.id, t.name]);
    if (!list.length) { toast(v === "owner" ? "This panel shows no admin" : "This panel has no user template", "bad"); return; }
    pgUPick(v === "owner" ? "Set the owner" : "Apply a template", `${names.length} user${names.length === 1 ? "" : "s"}: ${pgUList(names)}. ${v === "owner" ? "They become the users of this admin." : "The template replaces their data limit, end date, groups and status, and the panel removes a next plan they have."}`, list, v === "owner" ? "Set owner" : "Apply",
      val => done(v === "owner" ? "set_owner" : "apply_template", v === "owner" ? val : +val, v === "owner" ? "Owner set" : "Template applied"));
  }
};
/* a small window with one choice */
window.pgUPick = (title, text, list, okText, onOk) => {
  showModal(`<b>${esc(title)}</b>`, `<form class="pg-admin-form2" onsubmit="return pgUPickGo(event)"><p class="mut pgu-p">${esc(text)}</p><div class="sbox"><label class="kv2 kf"><span>Choose</span><select name="v">${list.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("")}</select></label></div>
    <div class="frm-act"><button class="pri" type="submit">${esc(okText)}</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`, "pgu-tool pgu-t-pick keep"); pgUPick.cb = onOk;
};
window.pgUPickGo = e => { e.preventDefault(); const v = e.target.elements.v.value, cb = pgUPick.cb; closeModal(); if (cb) cb(v); return false; };

/* shift the end dates or the data limits of many users, or set their Shadowsocks method: counted first, done after a confirm (the same "Who" for all three) */
const pgUStat = ["active", "disabled", "limited", "expired", "on_hold"];
const pgSsMethods = ["aes-128-gcm", "aes-256-gcm", "chacha20-ietf-poly1305", "xchacha20-poly1305"];      // twin of pg_users.SS_METHODS
window.pgShiftOpen = async kind => {
  let o; try { o = await pgUOpts(); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  const ss = kind === "ssmethod", days = kind === "expire", names = pgUTicked(), fld = (l, inner) => `<label class="kv2 kf"><span>${l}</span>${inner}</label>`;
  pgShift.kind = kind; pgShift.names = names; pgShift.n = null;
  const first = ss ? `<div class="sbox"><h5><i>🔐</i>Cipher of the Shadowsocks config</h5>
      ${fld("Method", `<select name="method">${pgSsMethods.map(m => `<option ${m === "chacha20-ietf-poly1305" ? "selected" : ""}>${m}</option>`).join("")}</select>`)}
      <small class="mut">Only users that have a Shadowsocks config are touched. Each keeps its own password; only the method changes, and a client takes the new one when it fetches its subscription again.</small></div>`
    : `<div class="sbox"><h5><i>${days ? "📅" : "📦"}</i>${days ? "Add days to the end date" : "Add GB to the data limit"}</h5>
      ${fld("Amount", `<input name="amount" type="number" step="${days ? 1 : 0.5}" required placeholder="${days ? "30, or -7 to take away" : "10, or -5 to take away"}"><em>${days ? "days" : "GB"}</em>`)}
      <small class="mut">${days ? "Users with no end date are not touched." : "Users with an unlimited data limit are not touched."} The panel adds the number to every user the choices below match.</small></div>`;
  showModal(`<b>${ss ? "Shadowsocks method" : days ? "Shift end dates" : "Shift data limits"}</b>`, `<form class="pg-admin-form2 pgu-shift" oninput="pgShiftDirty()" onchange="pgShiftDirty()" onsubmit="return pgShiftCount(event)">
    ${first}
    <div class="sbox"><h5><i>🎯</i>Who</h5>
      <div class="kv2 kf"><span>Status</span><div class="pgu-chips">${pgUStat.map(s => `<label class="chk"><input type="checkbox" name="st" value="${s}"><span>${s.replace("_", " ")}</span></label>`).join("")}</div></div>
      <div class="kv2 kf"><span>Groups</span><div class="pgu-groups">${o.groups.map(g => `<label class="chk"><input type="checkbox" name="grp" value="${g.id}"><span>${esc(g.name)}</span></label>`).join("") || '<span class="mut">no group</span>'}</div></div>
      ${fld("Admin", `<select name="admin"><option value="">any admin</option>${o.admins.map(a => `<option>${esc(a.name)}</option>`).join("")}</select>`)}
      ${fld("End date", `<span class="pgu-two"><input name="after" type="date" title="Ends on or after this day"><i>to</i><input name="before" type="date" title="Ends on or before this day"></span>`)}
      ${names.length ? `<label class="kv2 kf"><span>Ticked</span><span class="chk"><input type="checkbox" name="only" checked><span>only the ${names.length} ticked user${names.length === 1 ? "" : "s"} (${esc(pgUList(names))})</span></span></label>` : ""}
      <small class="mut">Nothing chosen = nobody: pick at least one of these.</small></div>
    <div class="pgu-res" id="pgu-res"></div>
    <div class="frm-act"><button class="pri" type="submit">Count the users</button><button type="button" id="pgu-go" disabled onclick="pgShiftGo()">Apply…</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`, "pgu-tool pgu-t-shift keep");
};
const pgShift ={kind: "", names: [], n: null, body: null};
function pgShiftBody(form, dry) {
  const f = form.elements, filters = {}, st = [...form.querySelectorAll('input[name="st"]:checked')].map(x => x.value), gr = [...form.querySelectorAll('input[name="grp"]:checked')].map(x => +x.value);
  if (st.length) filters.status = st; if (gr.length) filters.group_ids = gr; if (f.admin.value) filters.admins = [f.admin.value]; if (f.after.value) filters.expire_after_day = f.after.value; if (f.before.value) filters.expire_before_day = f.before.value;
  if (f.only && f.only.checked) filters.usernames = pgShift.names;
  if (pgShift.kind === "ssmethod") return {method: f.method.value, filters, dry_run: dry, confirm: !dry};
  return {what: pgShift.kind, amount: +f.amount.value, filters, dry_run: dry, confirm: !dry};
}
const pgShiftPath = () => pgShift.kind === "ssmethod" ? "/user-tools/proxy-method" : "/user-tools/shift";
window.pgShiftDirty = () => { pgShift.n = null; const go = $("#pgu-go"), r = $("#pgu-res"); if (go) go.disabled = true; if (r) r.innerHTML = ""; };
window.pgShiftCount = async e => {
  e.preventDefault(); const r = $("#pgu-res"), body = pgShiftBody(e.target, true), btn = e.target.querySelector("button[type=submit]"); btn.disabled = true;
  try { const j = await pgApi(+pn.id, pgShiftPath(), {method: "POST", body}); pgShift.n = j.count; pgShift.body = body; r.innerHTML = `<div class="pgu-count ${j.count ? "" : "none"}"><b>${j.count ?? "?"}</b> user${j.count === 1 ? "" : "s"} match${j.count === 1 ? "es" : ""}${j.count ? "" : ": nothing would change"}</div>`; $("#pgu-go").disabled = !j.count; }
  catch (err) { r.innerHTML = `<div class="err">${esc(err.message)}</div>`; }
  btn.disabled = false; return false;
};
window.pgShiftGo = async () => {
  const b = pgShift.body, n = pgShift.n; if (!b || !n) return;
  const ss = pgShift.kind === "ssmethod", what = ss ? "" : pgShift.kind === "expire" ? `${b.amount > 0 ? "add" : "take away"} ${Math.abs(b.amount)} day${Math.abs(b.amount) === 1 ? "" : "s"} ${b.amount > 0 ? "to" : "from"} the end date` : `${b.amount > 0 ? "add" : "take away"} ${Math.abs(b.amount)} GB ${b.amount > 0 ? "to" : "from"} the data limit`;
  if (!await gconfirm(ss ? `Set the Shadowsocks method of ${n} user${n === 1 ? "" : "s"} to ${b.method}? Each keeps its own password; every client has to take its subscription again to get the new method. The panel cannot undo it (set the old method again to go back).`
    : `${what[0].toUpperCase() + what.slice(1)} of ${n} user${n === 1 ? "" : "s"}? The panel cannot undo it (do the opposite amount to go back).`, {danger: true})) return;
  try { const j = await pgApi(+pn.id, pgShiftPath(), {method: "POST", body: {...b, dry_run: false, confirm: true}}); toast(`✓ Done for ${j.count ?? n} user${(j.count ?? n) === 1 ? "" : "s"}`, "ok"); pn.users = null; closeModal(); refresh(); }
  catch (e) { toast("✕ " + e.message, "bad"); }
};

/* clean up the users that are expired, limited, on hold or disabled: listed first, deleted only with the state typed back */
window.pgCleanOpen = async () => {
  let o; try { o = await pgUOpts(); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  const fld = (l, inner) => `<label class="kv2 kf"><span>${l}</span>${inner}</label>`;
  showModal("<b>Clean up users</b>", `<form class="pg-admin-form2" oninput="pgCleanDirty()" onchange="pgCleanDirty()" onsubmit="return pgCleanList(event)"><div class="sbox"><h5><i>🧹</i>Which users</h5>
      ${fld("State", `<select name="target"><option value="expired">expired</option><option value="limited">limited (out of data)</option><option value="on_hold">on hold</option><option value="disabled">disabled</option></select>`)}
      ${fld("Admin", `<select name="admin"><option value="">any admin</option>${o.admins.map(a => `<option>${esc(a.name)}</option>`).join("")}</select>`)}
      ${fld("Since", `<span class="pgu-two"><input name="after" type="date" title="From this day"><i>to</i><input name="before" type="date" title="Until this day"></span>`)}
      <small class="mut">An expired user is picked by its end date; the others by the day they reached that state. Both days empty = every user in that state.</small></div>
    <div class="pgu-res" id="pgu-res"></div>
    <div class="frm-act"><button class="pri" type="submit">List the users</button><button type="button" id="pgu-go" class="danger" disabled onclick="pgCleanGo()">Delete them…</button><button type="button" onclick="closeModal()">Close</button></div></form>`, "pgu-tool pgu-t-clean keep");
};
const pgCleanQ =form => { const f = form.elements, q = new URLSearchParams({target: f.target.value}); if (f.admin.value) q.set("admin", f.admin.value); if (f.after.value) q.set("after_day", f.after.value); if (f.before.value) q.set("before_day", f.before.value); return q; };
window.pgCleanDirty = () => { const go = $("#pgu-go"), r = $("#pgu-res"); if (go) go.disabled = true; if (r) r.innerHTML = ""; pgCleanQ.q = null; };
window.pgCleanList = async e => {
  e.preventDefault(); const r = $("#pgu-res"), btn = e.target.querySelector("button[type=submit]"), q = pgCleanQ(e.target); btn.disabled = true;
  try {
    const j = await pgApi(+pn.id, "/user-tools/expired?" + q); pgCleanQ.q = q; pgCleanQ.n = j.count;
    r.innerHTML = `<div class="pgu-count ${j.count ? "" : "none"}"><b>${j.count}</b> user${j.count === 1 ? "" : "s"}${j.count ? "" : ": nothing to clean"}</div>${j.users.length ? `<div class="pgu-names">${j.users.map(n => `<span>${esc(n)}</span>`).join("")}${j.truncated ? `<em class="mut">… and ${j.count - j.users.length} more</em>` : ""}</div>` : ""}`;
    $("#pgu-go").disabled = !j.count;
  } catch (err) { r.innerHTML = `<div class="err">${esc(err.message)}</div>`; }
  btn.disabled = false; return false;
};
window.pgCleanGo = async () => {
  const q = pgCleanQ.q, n = pgCleanQ.n; if (!q || !n) return; const target = q.get("target");
  const t = await gprompt(`Delete ${n} ${target.replace("_", " ")} user${n === 1 ? "" : "s"} for good? Their accounts and their links are gone and it cannot be undone. Type ${target} to confirm.`, "", {danger: true});
  if (t === null) return; if (t.trim() !== target) { toast("Not deleted: the word did not match", "bad"); return; }
  q.set("typed", target);
  try { const j = await pgApi(+pn.id, "/user-tools/expired?" + q, {method: "DELETE"}); toast(`✓ ${j.count} user${j.count === 1 ? "" : "s"} deleted`, "ok"); pn.users = null; closeModal(); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};

/* new users from a template: one (a name) or many (random names, or a base and a number) */
window.pgNewOpen = async many => {
  let o; try { o = await pgUOpts(); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  if (!o.templates.length) { toast("This panel has no user template: make one in Templates first", "bad"); return; }
  const fld = (l, inner, id = "", hid = false) => `<label class="kv2 kf"${id ? ` id="${id}"` : ""}${hid ? " hidden" : ""}><span>${l}</span>${inner}</label>`;
  showModal(`<b>${many ? "Many users from a template" : "New user from a template"}</b>`, `<form class="pg-admin-form2" onsubmit="return pgNewMake(event, ${many ? 1 : 0})"><div class="sbox"><h5><i>＋</i>${many ? "Users" : "User"}</h5>
      ${fld("Template", `<select name="tpl">${o.templates.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join("")}</select>`)}
      ${many ? fld("How many", `<input name="count" type="number" min="1" max="500" step="1" value="10" required><em>1 to 500</em>`)
        + fld("Names", `<select name="strategy" onchange="pgNewSt(this)"><option value="random">random</option><option value="sequence">a base and a number</option></select>`)
        + fld("Base", `<input name="base" maxlength="120" placeholder="shop-" autocomplete="off">`, "pgu-f-base", true)
        + fld("First number", `<input name="start" type="number" min="0" step="1" value="1">`, "pgu-f-start", true)
      : fld("Username", `<input name="username" required minlength="3" maxlength="128" placeholder="3 to 128 letters, digits or - _ @ ." autocomplete="off">`)}
      ${fld("Note", `<input name="note" maxlength="500" placeholder="optional">`)}
      <small class="mut">The template decides the groups, the data limit, the duration and the status; its prefix and suffix are put around the name.</small></div>
    <div class="pgu-res" id="pgu-res"></div>
    <div class="frm-act"><button class="pri" type="submit">${many ? "Make the users" : "Make the user"}</button><button type="button" onclick="closeModal()">Close</button></div></form>`, "pgu-tool pgu-t-new keep");
};
window.pgNewSt =sel => { const seq = sel.value === "sequence"; ["pgu-f-base", "pgu-f-start"].forEach(i => { const el = $("#" + i); if (el) el.hidden = !seq; }); };
window.pgNewMake = async (e, many) => {
  e.preventDefault(); const f = e.target.elements, r = $("#pgu-res"), btn = e.target.querySelector("button[type=submit]"); btn.disabled = true;
  try {
    if (many) {
      const body = {template_id: +f.tpl.value, count: +f.count.value, strategy: f.strategy.value, note: f.note.value}; if (body.strategy === "sequence") { body.base = f.base.value; body.start_number = f.start.value === "" ? null : +f.start.value; }
      const j = await pgApi(+pn.id, "/user-tools/bulk-from-template", {method: "POST", body});
      r.innerHTML = `<div class="pgu-count"><b>${j.created}</b> user${j.created === 1 ? "" : "s"} made</div><label class="pgu-links">Subscription links<textarea readonly rows="6" onfocus="this.select()">${esc(j.links.join("\n"))}</textarea></label><button type="button" class="sm" onclick="navigator.clipboard.writeText($('.pgu-links textarea').value).then(()=>toast('✓ Links copied','ok'))">Copy the links</button>`;
      toast(`✓ ${j.created} users made`, "ok");
    } else {
      const j = await pgApi(+pn.id, "/user-tools/from-template", {method: "POST", body: {template_id: +f.tpl.value, username: f.username.value.trim(), note: f.note.value}});
      r.innerHTML = `<div class="pgu-count"><b>${esc(j.user.username)}</b> made</div>${j.user.sub ? `<div class="kv2"><span>Link</span><div><span class="mono ell ue-link" title="Click to copy" onclick="copyFrom(this, '${esc(j.user.sub)}')">${esc(j.user.sub)}</span></div></div>` : ""}`;
      toast(`✓ ${j.user.username} made`, "ok"); e.target.elements.username.value = "";
    }
    pn.users = null; refresh();
  } catch (err) { r.innerHTML = `<div class="err">${esc(err.message)}</div>`; }
  btn.disabled = false; return false;
};
