/* Mini App of the shop bot - one small SPA: a view stack, Telegram theme and buttons, api.php behind it.
   Tabs: home، buy، services، wallet، more. Sub-views are pushed on the stack; Telegram's BackButton pops. */
"use strict";
const tg = window.Telegram && window.Telegram.WebApp;
const INIT = tg ? tg.initData : "";
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const T = n => Number(n || 0).toLocaleString("fa-IR");
const GB = b => { const g = Number(b || 0) / 1073741824; return (g >= 100 ? g.toFixed(0) : g.toFixed(g >= 10 ? 1 : 2)).replace(/\.0+$/, ""); };
const day = ts => ts ? new Date(ts * 1000).toLocaleDateString("fa-IR") : "—";
const when = ts => ts ? new Date(ts * 1000).toLocaleString("fa-IR", {dateStyle: "short", timeStyle: "short"}) : "—";
const left = ts => { if (!ts) return "نامحدود"; const d = Math.ceil((ts * 1000 - Date.now()) / 86400000); return d < 0 ? "منقضی" : d === 0 ? "امروز" : T(d) + " روز"; };
const vol = g => (Number(g) >= 1000 ? T(Number(g) / 1000) + " ترابایت" : T(g) + " گیگ");

const S = {me: null, feat: {}, amounts: [], bot: "", support: "", stack: [{v: "home"}], plans: null, services: null, live: {}, tickets: null, topup: null, busy: false};
try { S.topup = JSON.parse(localStorage.getItem("nk.topup") || "null"); if (S.topup && Date.now() - S.topup.at > 3600e3) S.topup = null; } catch (e) { S.topup = null; }

/* ---- Telegram glue */
if (tg) {
  tg.ready(); tg.expand();
  try { const dark = tg.colorScheme !== "light"; tg.setHeaderColor(dark ? "#070a17" : "#eef1f8"); tg.setBackgroundColor(dark ? "#070a17" : "#eef1f8"); } catch (e) {}      // the glass look has its own deep background
  tg.onEvent("themeChanged", applyTheme);
  tg.BackButton.onClick(back);
  try { tg.enableClosingConfirmation(); } catch (e) {}
}
function applyTheme() { const light = !!(tg && tg.colorScheme === "light"); document.documentElement.dataset.light = light ? "1" : ""; try { if (tg) { tg.setHeaderColor(light ? "#eef1f8" : "#070a17"); tg.setBackgroundColor(light ? "#eef1f8" : "#070a17"); } } catch (e) {} }
applyTheme();
const hap = kind => { try { if (!tg || !tg.HapticFeedback) return; if (kind === "ok" || kind === "bad") tg.HapticFeedback.notificationOccurred(kind === "ok" ? "success" : "error"); else tg.HapticFeedback.impactOccurred(kind || "light"); } catch (e) {} };
function toast(t, kind) { const e = $("toast"); e.textContent = t; e.className = "toast show " + (kind || ""); clearTimeout(e._t); e._t = setTimeout(() => e.classList.remove("show"), 2800); if (kind) hap(kind); }
function confirmTg(text) { return new Promise(res => { if (tg && tg.showConfirm) tg.showConfirm(text, ok => res(!!ok)); else res(window.confirm(text)); }); }
function copy(t, msg) { const done = () => toast(msg || "کپی شد ✓", "ok"); if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(t).then(done, () => fallbackCopy(t, done)); else fallbackCopy(t, done); }
function fallbackCopy(t, done) { const ta = document.createElement("textarea"); ta.value = t; ta.style.position = "fixed"; ta.style.opacity = "0"; document.body.appendChild(ta); ta.select(); try { document.execCommand("copy"); done(); } catch (e) { toast("کپی نشد", "bad"); } ta.remove(); }
function openLink(u) { if (tg && /^https:\/\/t\.me\//.test(u)) tg.openTelegramLink(u); else if (tg) tg.openLink(u); else window.open(u, "_blank"); }

/* ---- api */
async function api(a, body, form) {
  try {
    let r;
    if (form) { form.append("initData", INIT); form.append("a", a); r = await fetch("api.php?a=" + a, {method: "POST", body: form}); }
    else r = await fetch("api.php?a=" + a, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(Object.assign({initData: INIT}, body || {}))});
    const j = await r.json();
    if (j && j.ok && typeof j.wallet === "number") setWallet(j.wallet);
    return j || {ok: false, error: "پاسخ نامعتبر"};
  } catch (e) { return {ok: false, error: "ارتباط برقرار نشد؛ اینترنت را بررسی کن."}; }
}
function setWallet(w) { if (S.me) S.me.wallet = w; $("wallet").textContent = T(w); }

/* ---- navigation: a stack of views; the first entry is the tab */
const ROOTS = ["home", "buy", "services", "wallet", "more"];
function cur() { return S.stack[S.stack.length - 1]; }
function setTab(t) { if (cur().v === t && S.stack.length === 1) { render(); return; } S.stack = [{v: t}]; hap(); render(); }
function go(v, p) { S.stack.push({v, p: p || {}}); hap(); render(); }
function back() { if (S.stack.length > 1) { S.stack.pop(); render(); } else if (cur().v !== "home") setTab("home"); }
function replace(v, p) { S.stack[S.stack.length - 1] = {v, p: p || {}}; render(); }
async function render() {
  const c = cur(), root = S.stack[0].v;
  document.querySelectorAll(".nav button").forEach(b => b.classList.toggle("on", b.dataset.t === root));
  if (tg) { if (S.stack.length > 1) tg.BackButton.show(); else tg.BackButton.hide(); }
  closeSheet();
  const fn = VIEWS[c.v] || VIEWS.home;
  const app = $("app");
  app.innerHTML = `<div class="page">${await fn(c.p || {})}</div>`;
  window.scrollTo(0, 0);
  if (AFTER[c.v]) AFTER[c.v](c.p || {});
}
function sheet(html) { $("sheet-body").innerHTML = html; $("sheet").classList.add("on"); hap(); }
function closeSheet() { $("sheet").classList.remove("on"); }

/* ---- boot */
async function boot() {
  if (!INIT) { $("app").innerHTML = `<div class="card c"><div style="font-size:40px">🤖</div><h3 style="justify-content:center">این صفحه فقط از داخل تلگرام باز می‌شود</h3><div class="mut sm">مینی‌اپ را از دکمه‌ی داخل ربات باز کن.</div></div>`; $("sub").textContent = "خارج از تلگرام"; return; }
  const r = await api("me");
  if (!r.ok) { $("app").innerHTML = `<div class="card c"><div style="font-size:40px">⚠️</div><div class="mt">${esc(r.error)}</div><button class="btn mt" onclick="boot()">دوباره</button></div>`; $("sub").textContent = "خطا"; return; }
  S.me = r.user; S.feat = r.features || {}; S.amounts = r.amounts || []; S.bot = r.bot || ""; S.support = r.support || "";
  $("hello").textContent = "سلام " + (S.me.name || "") + " 👋";
  $("ava").textContent = (S.me.name || "👤").trim().charAt(0).toUpperCase() || "👤";
  $("sub").textContent = S.me.agent ? "همکار فروش" : (S.me.services ? T(S.me.services) + " سرویس فعال" : "خوش آمدی");
  setWallet(S.me.wallet);
  $("walbtn").classList.toggle("hide", !S.feat.wallet);
  document.querySelector('.nav [data-t="wallet"]').classList.toggle("hide", !S.feat.wallet);
  document.querySelector('.nav [data-t="buy"]').classList.toggle("hide", !S.feat.sell);
  badge("wallet", S.me.pending_topups);
  // deep links from the bot: start_param like svc_12، plan_3، wallet، support، buy
  const sp = r.start || (tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param) || "";
  let m;
  if ((m = /^svc_(\d+)$/.exec(sp))) { S.stack = [{v: "services"}, {v: "service", p: {id: +m[1]}}]; }
  else if ((m = /^plan_(\d+)$/.exec(sp))) { S.stack = [{v: "buy", p: {plan: +m[1]}}]; }
  else if (sp === "wallet" || sp === "topup") S.stack = [{v: "wallet"}];
  else if (sp === "support") S.stack = [{v: "more"}, {v: "tickets"}];
  else if (sp === "buy") S.stack = [{v: "buy"}];
  render();
}
function badge(tab, n) { const b = document.querySelector(`.nav [data-t="${tab}"]`); if (!b) return; let d = b.querySelector(".dot"); if (!n) { if (d) d.remove(); return; } if (!d) { d = document.createElement("span"); d.className = "dot"; b.appendChild(d); } d.textContent = n; }

/* ================================================================ views */
const VIEWS = {}, AFTER = {};

/* ---- home */
VIEWS.home = async () => {
  const F = S.feat;
  const qa = [
    F.sell && ["🛍", "خرید سرویس", "setTab('buy')"],
    ["📦", "سرویس‌ها", "setTab('services')"],
    F.wallet && ["💳", "شارژ کیف پول", "setTab('wallet')"],
    ["🎫", "پشتیبانی", "go('tickets')"],
    F.gift && ["🎁", "کد هدیه", "go('gift')"],
    F.wheel && ["🎡", "گردونه", "go('wheel')"],
    ["👥", "دعوت دوستان", "go('referral')"],
    F.apps && ["📱", "اپلیکیشن‌ها", "go('apps')"],
  ].filter(Boolean);
  const hero = F.wallet ? `<div class="card hero"><small>موجودی کیف پول</small><div class="bal">${T(S.me ? S.me.wallet : 0)}<small>تومان</small></div>${S.me && S.me.pending_topups ? `<span class="pill warn">${T(S.me.pending_topups)} رسید در انتظار تأیید</span>` : `<span class="pill ok">آماده‌ی خرید</span>`}
      <div class="hbtns">${F.card ? `<button class="btn pri" onclick="setTab('wallet')">💳 شارژ کیف پول</button>` : ""}${F.sell ? `<button class="btn" onclick="setTab('buy')">🛍 خرید سرویس</button>` : ""}</div></div>` : "";
  return `${hero}
    <div class="card"><h3>سرویس‌های من <span class="pill" id="h-count">${S.me ? T(S.me.services) : "—"}</span></h3><div id="h-svc" class="list"><div class="sk" style="height:58px"></div></div></div>
    <div class="card"><h3>دسترسی سریع</h3><div class="qa">${qa.map(q => `<button onclick="${q[2]}"><i>${q[0]}</i>${q[1]}</button>`).join("")}</div></div>
    ${S.support ? `<div class="card tap" onclick="openLink('https://t.me/${esc(S.support)}')"><div class="row"><div><b>ارتباط مستقیم با پشتیبانی</b><div class="sm mut">@${esc(S.support)}</div></div><span class="chev">‹</span></div></div>` : ""}`;
};
AFTER.home = async () => {
  const r = await loadServices();
  const box = $("h-svc"); if (!box) return;
  if (!r.ok) { box.innerHTML = `<div class="empty">${esc(r.error)}</div>`; return; }
  const act = r.services.filter(s => s.active).slice(0, 3);
  $("h-count").textContent = T(r.services.filter(s => s.active).length);
  box.innerHTML = act.length ? act.map(svcRow).join("") + (r.services.length > 3 ? `<button class="btn w ghost sm" onclick="setTab('services')">همه‌ی سرویس‌ها (${T(r.services.length)})</button>` : "")
    : `<div class="empty">هنوز سرویسی نداری.${S.feat.sell ? `<br><button class="btn pri mt" onclick="setTab('buy')">🛍 خرید اولین سرویس</button>` : ""}</div>`;
  loadLive(act.map(s => s.id));
};

/* ---- services */
async function loadServices(force) { if (S.services && !force) return {ok: true, services: S.services}; const r = await api("services"); if (r.ok) S.services = r.services; return r; }
function svcRow(s) {
  const L = S.live[s.id];
  const pct = L && L.limit ? Math.min(100, Math.round(L.used * 100 / L.limit)) : 0;
  const st = L ? (L.status === "active" ? "ok" : L.status === "on_hold" ? "info" : "bad") : (s.active ? "ok" : "bad");
  const stTxt = L ? (L.status_fa || L.status) : (s.active ? "فعال" : "غیرفعال");
  return `<div class="item" id="svc-${s.id}" onclick="go('service',{id:${s.id}})"><div class="tx"><div class="row"><b class="ell">${esc(s.remark)}</b><span class="pill ${st}">${esc(stTxt)}</span></div><small>${esc(s.server)}</small>
    ${L ? `<div class="bar"><i class="${pct >= 95 ? "bad" : pct >= 80 ? "warn" : ""}" style="width:${pct}%"></i></div><div class="row xs mut"><span>${GB(L.used)} از ${L.limit ? GB(L.limit) + " گیگ" : "نامحدود"}</span><span>${left(L.expire || s.expire)} مانده</span></div>`
      : (L === null ? `<div class="row xs mut"><span>انقضا: ${day(s.expire)}</span><span>${left(s.expire)}</span></div>` : `<div class="sk mt" style="height:7px"></div>`)}</div></div>`;
}
async function loadLive(ids) {   // the live numbers from the panels, three at a time
  const todo = ids.filter(id => S.live[id] === undefined);
  let i = 0;
  const worker = async () => { while (i < todo.length) { const id = todo[i++]; const r = await api("live", {id}); S.live[id] = r.ok && r.live ? Object.assign(r.live, {status_fa: r.status_fa}) : null; const el = $("svc-" + id), s = (S.services || []).find(x => x.id === id); if (el && s) el.outerHTML = svcRow(s); } };
  await Promise.all([worker(), worker(), worker()]);
}
VIEWS.services = async () => `<div class="title">سرویس‌های من <small id="s-count"></small></div><div class="seg" id="s-seg"><button class="on" onclick="svcFilter('active',this)">فعال</button><button onclick="svcFilter('all',this)">همه</button></div><div id="s-list" class="list"><div class="loading"><i class="spin"></i>در حال بارگذاری…</div></div>`;
let svcMode = "active";
function svcFilter(m, btn) { svcMode = m; document.querySelectorAll("#s-seg button").forEach(b => b.classList.toggle("on", b === btn)); paintServices(); }
function paintServices() {
  const all = S.services || [], list = svcMode === "all" ? all : all.filter(s => s.active);
  $("s-count").textContent = T(list.length) + " سرویس";
  $("s-list").innerHTML = list.length ? list.map(svcRow).join("") : `<div class="empty">${svcMode === "all" ? "هنوز سرویسی نداری." : "سرویس فعالی نداری."}${S.feat.sell ? `<br><button class="btn pri mt" onclick="setTab('buy')">🛍 خرید سرویس</button>` : ""}</div>`;
  loadLive(list.map(s => s.id));
}
AFTER.services = async () => { const r = await loadServices(true); if (!r.ok) { $("s-list").innerHTML = `<div class="empty">${esc(r.error)}</div>`; return; } paintServices(); };

VIEWS.service = async p => `<div id="svc-detail"><div class="loading"><i class="spin"></i>در حال خواندن از سرور…</div></div>`;
AFTER.service = async p => {
  const r = await api("service", {id: p.id});
  const box = $("svc-detail"); if (!box) return;
  if (!r.ok) { box.innerHTML = `<div class="card"><div class="empty">${esc(r.error)}</div></div>`; return; }
  const s = r.service, L = s.live || null, pct = L && L.limit ? Math.min(100, Math.round(L.used * 100 / L.limit)) : 0;
  const st = L ? (L.status === "active" ? "ok" : L.status === "on_hold" ? "info" : "bad") : (s.active ? "ok" : "bad");
  const F = S.feat;
  box.innerHTML = `
    <div class="title"><span class="ell">${esc(s.remark)}</span><span class="pill ${st}">${esc(r.service.status_fa || (s.active ? "فعال" : "غیرفعال"))}</span></div>
    <div class="card">
      <div class="sm mut">${esc(s.server)}${s.plan ? "، " + esc(s.plan.title) : ""}</div>
      ${L ? `<div class="bar"><i class="${pct >= 95 ? "bad" : pct >= 80 ? "warn" : ""}" style="width:${pct}%"></i></div>` : ""}
      <div class="grid mt">
        <div class="tile"><small>مصرف</small><b>${L ? GB(L.used) + " گیگ" : "—"}</b></div><div class="tile"><small>حجم کل</small><b>${L ? (L.limit ? GB(L.limit) + " گیگ" : "نامحدود") : "—"}</b></div>
        <div class="tile"><small>انقضا</small><b>${L && L.hold && !L.expire ? "از اولین اتصال" : day((L && L.expire) || s.expire)}</b></div><div class="tile"><small>باقی‌مانده</small><b>${L && L.hold && !L.expire ? T(Math.round(L.hold / 86400)) + " روز" : left((L && L.expire) || s.expire)}</b></div>
      </div>
      ${L === null && s.active ? `<div class="note warn mt">ارتباط با پنل این سرور برقرار نشد؛ اعداد زنده فعلاً در دسترس نیست.</div>` : ""}
    </div>
    ${s.sub ? `<div class="card"><h3>لینک اشتراک <button class="btn sm" onclick="copy(${JSON.stringify(s.sub)})">📋 کپی</button></h3><div class="code" onclick="copy(${JSON.stringify(s.sub)})">${esc(s.sub)}<span class="cp">📋</span></div><div class="qr hide" id="qr"></div><div class="btns"><button class="btn sm" onclick="showQr(${JSON.stringify(s.sub)})">▦ QR</button>${F.apps ? `<button class="btn sm" onclick="go('apps')">📱 برنامه‌ی اتصال</button>` : ""}</div></div>` : ""}
    ${s.links && s.links.length ? `<div class="card"><h3>کانفیگ‌ها <span class="pill">${T(s.links.length)}</span></h3>${s.links.slice(0, 12).map(l => `<div class="code mb" onclick="copy(${JSON.stringify(l)})">${esc(l.length > 110 ? l.slice(0, 110) + "…" : l)}<span class="cp">📋</span></div>`).join("")}${s.links.length > 1 ? `<button class="btn w sm" onclick="copy(${JSON.stringify(s.links.join("\n"))}, 'همه‌ی کانفیگ‌ها کپی شد ✓')">📋 کپی همه</button>` : ""}</div>` : ""}
    <div class="card"><h3>مدیریت سرویس</h3><div class="grid">
      ${F.renew ? `<button class="btn" onclick="renewSheet(${s.id})">🔄 تمدید</button>` : ""}
      ${F.addvolume ? `<button class="btn" onclick="extraSheet(${s.id},'volume')">➕ حجم اضافه</button>` : ""}
      ${F.adddays ? `<button class="btn" onclick="extraSheet(${s.id},'days')">⏳ زمان اضافه</button>` : ""}
      ${F.report ? `<button class="btn" onclick="reportSheet(${s.id})">🛠 گزارش اختلال</button>` : ""}
      <button class="btn" onclick="go('tickets',{svc:${JSON.stringify(s.remark)}})">🎫 پشتیبانی</button>
    </div></div>`;
};
function showQr(text) {
  const box = $("qr"); if (!box) return;
  if (typeof qrcode !== "function") { toast("کتابخانه‌ی QR بارگذاری نشد", "bad"); return; }
  if (!box.classList.contains("hide")) { box.classList.add("hide"); return; }
  try { const q = qrcode(0, "M"); q.addData(text); q.make(); box.innerHTML = `<img alt="QR" src="${q.createDataURL(5, 0)}">`; box.classList.remove("hide"); } catch (e) { toast("لینک برای QR خیلی بلند است", "bad"); }
}
async function renewSheet(id) {
  sheet(`<div class="loading"><i class="spin"></i>محاسبه‌ی قیمت…</div>`);
  const q = await api("quote", {id});
  if (!q.ok) { sheet(`<div class="empty">${esc(q.error)}</div>`); return; }
  if (!q.renew) { sheet(`<h3>تمدید</h3><div class="note">پلن این سرویس دیگر فعال نیست؛ از بخش خرید یک سرویس جدید بگیر.</div><div class="btns"><button class="btn w" onclick="closeSheet()">باشه</button></div>`); return; }
  const R = q.renew, short = R.price - q.wallet;
  sheet(`<h3>تمدید سرویس</h3>
    <div class="grid"><div class="tile"><small>پلن</small><b>${esc(R.title)}</b></div><div class="tile"><small>قیمت</small><b>${T(R.price)} تومان</b></div><div class="tile"><small>حجم</small><b>${vol(R.volume)}</b></div><div class="tile"><small>مدت</small><b>${T(R.days)} روز</b></div></div>
    <div class="note mt">تمدید = همان پلن از نو: حجم به ${vol(R.volume)} برمی‌گردد، مصرف صفر می‌شود و ${T(R.days)} روز به انقضای فعلی (یا امروز، اگر تمام شده) اضافه می‌شود.</div>
    ${short > 0 ? `<div class="note bad mt">موجودی کیف پول ${T(q.wallet)} تومان است؛ ${T(short)} تومان کم داری.</div><div class="btns"><button class="btn pri" onclick="closeSheet();setTab('wallet');topupStart(${short})">💳 شارژ کیف پول</button><button class="btn" onclick="closeSheet()">بعداً</button></div>`
      : `<div class="btns"><button class="btn pri" id="do-renew" onclick="doRenew(${id})">پرداخت ${T(R.price)} تومان از کیف پول</button><button class="btn" onclick="closeSheet()">انصراف</button></div>`}`);
}
async function doRenew(id) {
  if (S.busy) return; S.busy = true; const b = $("do-renew"); if (b) { b.disabled = true; b.innerHTML = '<i class="spin"></i> در حال تمدید…'; }
  const r = await api("renew", {id}); S.busy = false;
  if (!r.ok) { toast(r.error, "bad"); if (b) { b.disabled = false; b.textContent = "دوباره"; } return; }
  closeSheet(); toast("سرویس تمدید شد ✓" + (r.cashback ? "، کش‌بک " + T(r.cashback) + " تومان" : ""), "ok"); delete S.live[id]; S.services = null; render();
}
async function extraSheet(id, kind) {
  sheet(`<div class="loading"><i class="spin"></i>محاسبه‌ی قیمت…</div>`);
  const q = await api("quote", {id});
  if (!q.ok) { sheet(`<div class="empty">${esc(q.error)}</div>`); return; }
  const isVol = kind === "volume", D = isVol ? q.volume : q.days;
  if (!D) { sheet(`<h3>${isVol ? "حجم اضافه" : "زمان اضافه"}</h3><div class="note">این امکان فعلاً فعال نیست.</div><div class="btns"><button class="btn w" onclick="closeSheet()">باشه</button></div>`); return; }
  const unit = isVol ? D.gb_price : D.day_price;
  const presets = (D.presets || []).map(p => isVol ? {n: p.gb, price: p.price} : {n: p.days, price: p.price});
  if (!presets.length && unit > 0) [1, 5, 10, 20, 30, 50].forEach(n => presets.push({n, price: n * unit}));
  S.extra = {id, kind, unit, presets, wallet: q.wallet, n: presets[0] ? presets[0].n : 0, price: presets[0] ? presets[0].price : 0};
  sheet(`<h3>${isVol ? "➕ حجم اضافه" : "⏳ زمان اضافه"}</h3>
    <div class="amounts" id="ex-presets">${presets.map(p => `<button class="${p.n === S.extra.n ? "on" : ""}" onclick="extraPick(${p.n},${p.price},this)">${T(p.n)} ${isVol ? "گیگ" : "روز"}<br><small class="mut xs">${T(p.price)} ت</small></button>`).join("")}</div>
    ${unit > 0 ? `<label class="lb">یا مقدار دلخواه (${T(unit)} تومان هر ${isVol ? "گیگ" : "روز"})</label><input class="input num" id="ex-n" type="number" min="1" inputmode="numeric" placeholder="${isVol ? "گیگ" : "روز"}" oninput="extraCustom(this.value)">` : ""}
    <div class="row mt"><span class="mut">مبلغ</span><b id="ex-price">${T(S.extra.price)} تومان</b></div>
    <div class="row"><span class="mut">کیف پول</span><b>${T(q.wallet)} تومان</b></div>
    <div class="btns"><button class="btn pri" id="do-extra" onclick="doExtra()">پرداخت از کیف پول</button><button class="btn" onclick="closeSheet()">انصراف</button></div>`);
}
function extraPick(n, price, btn) { S.extra.n = n; S.extra.price = price; document.querySelectorAll("#ex-presets button").forEach(b => b.classList.toggle("on", b === btn)); const i = $("ex-n"); if (i) i.value = ""; $("ex-price").textContent = T(price) + " تومان"; }
function extraCustom(v) { const n = Math.max(0, parseInt(v, 10) || 0); S.extra.n = n; S.extra.price = n * S.extra.unit; document.querySelectorAll("#ex-presets button").forEach(b => b.classList.remove("on")); $("ex-price").textContent = T(S.extra.price) + " تومان"; }
async function doExtra() {
  const E = S.extra; if (!E || !E.n || !E.price) { toast("مقدار را انتخاب کن", "bad"); return; }
  if (E.price > E.wallet) { closeSheet(); toast("موجودی کافی نیست؛ " + T(E.price - E.wallet) + " تومان کم داری", "bad"); setTab("wallet"); topupStart(E.price - E.wallet); return; }
  if (S.busy) return; S.busy = true; const b = $("do-extra"); b.disabled = true; b.innerHTML = '<i class="spin"></i> در حال اعمال…';
  const r = await api(E.kind === "volume" ? "addvolume" : "adddays", {id: E.id, amount: E.n}); S.busy = false;
  if (!r.ok) { toast(r.error, "bad"); b.disabled = false; b.textContent = "دوباره"; return; }
  closeSheet(); toast((E.kind === "volume" ? T(E.n) + " گیگ" : T(E.n) + " روز") + " اضافه شد ✓", "ok"); delete S.live[E.id]; render();
}
function reportSheet(id) {
  sheet(`<h3>🛠 گزارش اختلال</h3><div class="sm mut mb">کوتاه بنویس چه مشکلی داری (مثلاً: وصل نمی‌شود، سرعت پایین، قطع و وصل).</div><textarea class="input" id="rp-text" placeholder="توضیح مشکل…"></textarea><div class="btns"><button class="btn pri" onclick="doReport(${id})">ارسال گزارش</button><button class="btn" onclick="closeSheet()">انصراف</button></div>`);
  setTimeout(() => $("rp-text") && $("rp-text").focus(), 150);
}
async function doReport(id) { const r = await api("report", {id, text: $("rp-text").value}); if (!r.ok) { toast(r.error, "bad"); return; } closeSheet(); toast("گزارش ثبت شد؛ پشتیبانی بررسی می‌کند ✓", "ok"); }

/* ---- buy */
VIEWS.buy = async () => `<div class="title">خرید سرویس</div><div class="seg" id="b-seg"></div><div id="b-list"><div class="loading"><i class="spin"></i>در حال بارگذاری پلن‌ها…</div></div>${S.feat.gift ? `<div class="card"><h3>🎁 کد هدیه</h3><div class="row"><input class="input grow" id="gift" placeholder="کد را وارد کن" autocomplete="off"><button class="btn pri" onclick="redeem()">اعمال</button></div></div>` : ""}`;
let buyCat = "";
AFTER.buy = async p => {
  const r = await api("plans");
  const box = $("b-list"); if (!box) return;
  if (!r.ok) { box.innerHTML = `<div class="card"><div class="empty">${esc(r.error)}</div></div>`; return; }
  S.plans = r.plans; S.sell = r.sell; S.cashback = r.cashback;
  const cats = [...new Set(r.plans.map(x => x.category || x.server))];
  if (!cats.includes(buyCat)) buyCat = cats[0] || "";
  $("b-seg").innerHTML = cats.length > 1 ? cats.map(c => `<button class="${c === buyCat ? "on" : ""}" data-c="${esc(c)}" onclick="buyCatPick(this)">${esc(c)}</button>`).join("") : "";
  paintPlans();
  if (p.plan) { const pl = r.plans.find(x => x.id === p.plan); if (pl) buySheet(pl.id); }
};
function buyCatPick(btn) { buyCat = btn.dataset.c || ""; document.querySelectorAll("#b-seg button").forEach(b => b.classList.toggle("on", b === btn)); hap(); paintPlans(); }      // the name sits in a data attribute: a quote in a category name must not break the button
function paintPlans() {
  const box = $("b-list"), all = S.plans || [];
  const list = all.filter(x => (x.category || x.server) === buyCat || !buyCat);
  if (!all.length) { box.innerHTML = `<div class="card"><div class="empty">فعلاً پلنی تعریف نشده.</div></div>`; return; }
  const by = {}; list.forEach(x => (by[x.server] = by[x.server] || []).push(x));
  box.innerHTML = (!S.sell ? `<div class="note warn mb">فروش موقتاً متوقف است؛ سرویس‌های فعلی کار می‌کنند.</div>` : "") + (S.cashback > 0 ? `<div class="note ok mb">🎁 ${T(S.cashback)}٪ هر خرید به‌صورت کش‌بک به کیف پولت برمی‌گردد.</div>` : "") +
    Object.keys(by).map(srv => `<div class="card"><h3>${esc(srv)}</h3>${by[srv].map(x => `<div class="plan ${x.sold_out || !S.sell ? "off" : ""}" onclick="${x.sold_out || !S.sell ? "" : `buySheet(${x.id})`}"><div class="grow"><div class="pt">${esc(x.title)}</div><div class="pd">${vol(x.volume)}، ${x.days ? T(x.days) + " روز" : "بدون محدودیت زمان"}${x.users > 0 ? "، " + T(x.users) + " کاربر" : ""}${x.sold_out ? "، <b>تمام شد</b>" : ""}</div>${x.descr ? `<div class="pd">${esc(x.descr)}</div>` : ""}</div><div class="pr"><b>${T(x.price)}</b>${x.discount ? `<s>${T(x.list_price)}</s> ` : ""}<small>تومان</small></div></div>`).join("")}</div>`).join("");
}
function buySheet(id) {
  const x = (S.plans || []).find(p => p.id === id); if (!x) return;
  const wallet = S.me ? S.me.wallet : 0, short = x.price - wallet;
  sheet(`<h3>${esc(x.title)}</h3>
    <div class="grid"><div class="tile"><small>سرور</small><b>${esc(x.server)}</b></div><div class="tile"><small>قیمت</small><b>${T(x.price)} تومان</b></div><div class="tile"><small>حجم</small><b>${vol(x.volume)}</b></div><div class="tile"><small>مدت</small><b>${x.days ? T(x.days) + " روز" : "نامحدود"}</b></div></div>
    ${x.descr ? `<div class="note mt">${esc(x.descr)}</div>` : ""}
    ${x.discount ? `<div class="note ok mt">تخفیف همکار: ${T(x.discount)}٪ (قیمت اصلی ${T(x.list_price)} تومان)</div>` : ""}
    ${S.feat.hold ? `<div class="note mt">⏱ زمان سرویس از اولین اتصال شروع می‌شود.</div>` : ""}
    <div class="row mt"><span class="mut">کیف پول</span><b>${T(wallet)} تومان</b></div>
    ${short > 0 ? `<div class="note bad mt">${T(short)} تومان کم داری.</div><div class="btns"><button class="btn pri" onclick="closeSheet();setTab('wallet');topupStart(${short})">💳 شارژ کیف پول</button><button class="btn" onclick="closeSheet()">بعداً</button></div>`
      : `<div class="btns"><button class="btn pri" id="do-buy" onclick="doBuy(${x.id})">پرداخت ${T(x.price)} تومان از کیف پول</button><button class="btn" onclick="closeSheet()">انصراف</button></div>`}`);
}
async function doBuy(id) {
  if (S.busy) return; S.busy = true; const b = $("do-buy"); b.disabled = true; b.innerHTML = '<i class="spin"></i> در حال ساخت سرویس…';
  const r = await api("buy", {plan_id: id}); S.busy = false;
  if (!r.ok) { toast(r.error, "bad"); b.disabled = false; b.textContent = "دوباره تلاش کن"; if (r.short) { closeSheet(); setTab("wallet"); topupStart(r.short); } return; }
  closeSheet(); hap("ok"); S.services = null; if (S.me) S.me.services++;
  S.stack = [{v: "services"}, {v: "bought", p: r}]; render();
}
VIEWS.bought = async r => `
  <div class="card c"><div style="font-size:44px">🎉</div><h3 style="justify-content:center">سرویس شما ساخته شد</h3><div class="sm mut">نام سرویس: <b>${esc(r.remark)}</b>${r.cashback ? `، کش‌بک ${T(r.cashback)} تومان به کیف پولت برگشت` : ""}</div></div>
  ${r.sub ? `<div class="card"><h3>لینک اشتراک <button class="btn sm" onclick="copy(${JSON.stringify(r.sub)})">📋 کپی</button></h3><div class="code" onclick="copy(${JSON.stringify(r.sub)})">${esc(r.sub)}<span class="cp">📋</span></div><div class="qr hide" id="qr"></div><div class="btns"><button class="btn sm" onclick="showQr(${JSON.stringify(r.sub)})">▦ QR</button>${S.feat.apps ? `<button class="btn sm" onclick="go('apps')">📱 برنامه‌ی اتصال</button>` : ""}</div><div class="note mt">لینک اشتراک را در برنامه‌ی اتصال (v2rayNG، Streisand، Hiddify…) وارد کن؛ کانفیگ‌ها خودکار می‌آیند. همین لینک در چت ربات هم برایت فرستاده شد.</div></div>` : ""}
  ${r.links && r.links.length ? `<div class="card"><h3>کانفیگ‌ها <span class="pill">${T(r.links.length)}</span></h3>${r.links.slice(0, 8).map(l => `<div class="code mb" onclick="copy(${JSON.stringify(l)})">${esc(l.length > 110 ? l.slice(0, 110) + "…" : l)}<span class="cp">📋</span></div>`).join("")}</div>` : ""}
  <div class="btns"><button class="btn pri" onclick="S.stack=[{v:'services'},{v:'service',p:{id:${r.order_id}}}];render()">📦 جزئیات سرویس</button><button class="btn" onclick="setTab('home')">خانه</button></div>`;
async function redeem() { const code = ($("gift").value || "").trim(); if (!code) return; const r = await api("gift", {code}); if (r.ok) { toast(T(r.amount) + " تومان به کیف پولت اضافه شد 🎁", "ok"); $("gift").value = ""; } else toast(r.error, "bad"); }

/* ---- wallet */
VIEWS.wallet = async () => {
  const F = S.feat, tp = S.topup;
  return `<div class="title">کیف پول</div>
    <div class="card"><div class="row"><div><small class="mut">موجودی</small><div style="font-size:26px;font-weight:800">${T(S.me ? S.me.wallet : 0)} <small class="mut" style="font-size:12px">تومان</small></div></div><div class="c">${S.me && S.me.pending_topups ? `<span class="pill warn">${T(S.me.pending_topups)} رسید در انتظار تأیید</span>` : `<span class="pill ok">آماده</span>`}</div></div></div>
    ${tp ? `<div class="card" id="tp-resume"><h3>شارژ ناتمام <span class="pill warn">${T(tp.amount)} تومان</span></h3><div class="sm mut">هنوز رسید این شارژ را نفرستاده‌ای.</div><div class="btns"><button class="btn pri" onclick="go('topup',{resume:1})">ادامه و ارسال رسید</button><button class="btn" onclick="topupCancel()">لغو</button></div></div>` : ""}
    ${F.wallet && F.card ? `<div class="card"><h3>شارژ کیف پول</h3><div class="amounts" id="amounts">${(S.amounts.length ? S.amounts : [100000, 200000, 500000]).map(a => `<button onclick="amountPick(${a},this)">${T(a)}<br><small class="mut xs">تومان</small></button>`).join("")}</div>
      <label class="lb">یا مبلغ دلخواه (تومان)</label><input class="input num" id="tp-amount" type="number" inputmode="numeric" min="${S.min_topup || 10000}" step="1000" placeholder="مثلاً 150000" oninput="document.querySelectorAll('#amounts button').forEach(b=>b.classList.remove('on'))">
      <button class="btn pri w mt" onclick="topupStart()">💳 ادامه (کارت‌به‌کارت)</button></div>` : F.wallet ? `<div class="card"><div class="note">شارژ از این‌جا فعلاً فعال نیست؛ از منوی ربات کیف پول را شارژ کن.</div><button class="btn w mt" onclick="openLink('https://t.me/${esc(S.bot)}?start=wallet')">🤖 باز کردن ربات</button></div>` : ""}
    <div class="card"><h3>تاریخچه</h3><div id="hist" class="list"><div class="sk" style="height:46px"></div></div></div>`;
};
function amountPick(a, btn) { document.querySelectorAll("#amounts button").forEach(b => b.classList.toggle("on", b === btn)); $("tp-amount").value = a; }
AFTER.wallet = async () => {
  const r = await api("history"); const box = $("hist"); if (!box) return;
  if (!r.ok) { box.innerHTML = `<div class="empty">${esc(r.error)}</div>`; return; }
  const cls = s => ({approved: "ok", auto_cart_approved: "ok", paid: "ok", paid_with_wallet: "info", have_sent: "warn", pending: "warn", declined: "bad", low_wallet: "bad", expired_have_sent: "bad", expired_pending: "bad"}[s] || "");
  const rows = r.pays.filter(p => p.state !== "pending" || p.kind !== "INCREASE_WALLET");
  box.innerHTML = rows.length ? rows.slice(0, 25).map(p => `<div class="item static"><div class="ic">${p.kind === "INCREASE_WALLET" ? "💳" : p.kind === "BUY_SUB" ? "🛍" : "➕"}</div><div class="tx"><div class="row"><b>${esc(p.type)}</b><span class="pill ${cls(p.state)}">${esc(p.state_fa)}</span></div><small>${when(p.at)}${p.note ? "، " + esc(p.note) : ""}</small></div><b class="ltr">${p.kind === "INCREASE_WALLET" ? "+" : "−"}${T(p.price)}</b></div>`).join("")
    + (r.cashback.length ? `<div class="sm mut mt">کش‌بک‌ها</div>` + r.cashback.slice(0, 5).map(c => `<div class="item static"><div class="ic">🎁</div><div class="tx"><b>کش‌بک خرید</b><small>${when(c.at)}، خرید ${T(c.order)} تومان</small></div><b class="ltr">+${T(c.back)}</b></div>`).join("") : "")
    : `<div class="empty">هنوز تراکنشی نداری.</div>`;
};
async function topupStart(prefill) {
  if (prefill) { setTab("wallet"); await new Promise(r => setTimeout(r, 60)); const i = $("tp-amount"); if (i) i.value = Math.ceil(prefill / 10000) * 10000; return; }
  const i = $("tp-amount"), amount = parseInt(i && i.value, 10) || 0;
  if (amount < (S.min_topup || 10000)) { toast("مبلغ را انتخاب کن (حداقل " + T(S.min_topup || 10000) + " تومان)", "bad"); return; }
  const btn = document.querySelector("#app .btn.pri.w"); if (btn) { btn.disabled = true; btn.innerHTML = '<i class="spin"></i> ثبت درخواست…'; }
  const r = await api("topup", {amount});
  if (!r.ok) { toast(r.error, "bad"); if (btn) { btn.disabled = false; btn.textContent = "💳 ادامه (کارت‌به‌کارت)"; } return; }
  S.topup = Object.assign({at: Date.now()}, r); try { localStorage.setItem("nk.topup", JSON.stringify(S.topup)); } catch (e) {}
  go("topup", {});
}
function topupCancel() { if (S.topup) api("cancel_topup", {hash: S.topup.hash}); S.topup = null; try { localStorage.removeItem("nk.topup"); } catch (e) {} render(); }
VIEWS.topup = async () => {
  const t = S.topup; if (!t) return `<div class="card"><div class="empty">درخواست شارژی در جریان نیست.</div><button class="btn w" onclick="setTab('wallet')">کیف پول</button></div>`;
  const detail = t.kind === "detail";
  return `<div class="title">پرداخت کارت‌به‌کارت</div><div class="steps"><i class="on"></i><i class="on"></i><i id="st3"></i></div>
    <div class="card"><h3>۱. مبلغ دقیق را واریز کن</h3>
      <div class="copyrow" onclick="copy('${t.exact}','مبلغ کپی شد ✓')"><div><small class="mut xs">مبلغ دقیق واریز (تومان)</small><br><b>${T(t.exact)}</b></div><span class="btn sm">📋 کپی</span></div>
      ${detail ? `<div class="copyrow" onclick="copy(${JSON.stringify(t.card)},'شماره کارت کپی شد ✓')"><div><small class="mut xs">شماره کارت، ${esc(t.holder)}</small><br><b>${esc(t.card.replace(/(\d{4})(?=\d)/g, "$1 "))}</b></div><span class="btn sm">📋 کپی</span></div>` : ""}
      ${t.usdt_wallet && !detail ? `<div class="copyrow" onclick="copy(${JSON.stringify(t.usdt_wallet)},'آدرس کپی شد ✓')"><div><small class="mut xs">USDT (TRC20)${t.usdt ? "، " + t.usdt + " USDT" : ""}</small><br><b style="font-size:12px">${esc(t.usdt_wallet)}</b></div><span class="btn sm">📋 کپی</span></div>` : ""}
      <div class="note mt">${t.text || "مبلغ را دقیقاً با همان رقم‌های آخر واریز کن تا تأیید سریع‌تر انجام شود."}</div>
      <div class="xs mut mt">⏱ این درخواست یک ساعت اعتبار دارد. مبلغ انتخابی: ${T(t.amount)} تومان؛ رقم‌های آخر برای شناسایی واریز اضافه شده‌اند و همان ${T(t.amount)} تومان شارژ می‌شود.</div>
    </div>
    <div class="card"><h3>۲. تصویر رسید را بفرست</h3>
      <label class="drop" id="drop"><input type="file" id="rc-file" accept="image/*" onchange="receiptPicked(this)"><div style="font-size:30px">🧾</div><div>برای انتخاب تصویر رسید ضربه بزن</div><small class="xs">JPG یا PNG، حداکثر ۶ مگابایت</small></label>
      <div class="btns"><button class="btn pri" id="rc-send" disabled onclick="receiptSend()">ارسال رسید برای تأیید</button><button class="btn danger" onclick="topupCancel()">لغو درخواست</button></div>
    </div>`;
};
let receiptFile = null;
function receiptPicked(inp) {
  const f = inp.files && inp.files[0]; receiptFile = f || null;
  const d = $("drop"); $("rc-send").disabled = !f;
  if (!f) return;
  if (f.size > 6 * 1024 * 1024) { toast("حجم تصویر بیشتر از ۶ مگابایت است", "bad"); receiptFile = null; $("rc-send").disabled = true; return; }
  const url = URL.createObjectURL(f); d.classList.add("has"); d.innerHTML = `<input type="file" id="rc-file" accept="image/*" onchange="receiptPicked(this)"><img src="${url}" alt=""><small class="xs mut">${esc(f.name)}، ${(f.size / 1024 / 1024).toFixed(2)} MB، برای تغییر ضربه بزن</small>`;
}
async function receiptSend() {
  if (!receiptFile || !S.topup || S.busy) return;
  S.busy = true; const b = $("rc-send"); b.disabled = true; b.innerHTML = '<i class="spin"></i> در حال ارسال…';
  const fd = new FormData(); fd.append("hash", S.topup.hash); fd.append("photo", receiptFile, receiptFile.name || "receipt.jpg");
  const r = await api("receipt", null, fd); S.busy = false;
  if (!r.ok) { toast(r.error, "bad"); b.disabled = false; b.textContent = "دوباره تلاش کن"; return; }
  const amount = S.topup.amount; S.topup = null; receiptFile = null; try { localStorage.removeItem("nk.topup"); } catch (e) {}
  if (S.me) S.me.pending_topups = (S.me.pending_topups || 0) + 1; badge("wallet", S.me && S.me.pending_topups);
  hap("ok"); $("st3") && $("st3").classList.add("on");
  replace("topup_done", {amount});
}
VIEWS.topup_done = async p => `<div class="card c"><div style="font-size:44px">✅</div><h3 style="justify-content:center">رسید ارسال شد</h3><div class="sm mut">درخواست شارژ <b>${T(p.amount)} تومان</b> در صف بررسی پشتیبانی است؛ بعد از تأیید، کیف پولت شارژ می‌شود و پیام آن را در ربات می‌گیری.</div><div class="btns"><button class="btn pri" onclick="setTab('wallet')">کیف پول</button><button class="btn" onclick="setTab('home')">خانه</button></div></div>`;

/* ---- more */
VIEWS.more = async () => {
  const F = S.feat;
  const rows = [
    ["🎫", "پشتیبانی و تیکت‌ها", "سؤال یا مشکل داری؟ همین‌جا بنویس", "go('tickets')"],
    ["👥", "دعوت دوستان", F.referral ? "با هر خرید دوستانت جایزه بگیر" : "لینک دعوت شخصی تو", "go('referral')"],
    F.apps && ["📱", "اپلیکیشن‌های اتصال", "برنامه‌های پیشنهادی برای هر دستگاه", "go('apps')"],
    F.gift && ["🎁", "کد هدیه", "کد را وارد کن و کیف پولت را شارژ کن", "go('gift')"],
    F.wheel && ["🎡", "گردونه شانس", "هر روز یک شانس", "go('wheel')"],
    S.support && ["💬", "ارتباط مستقیم با پشتیبان", "@" + S.support, `openLink('https://t.me/${esc(S.support)}')`],
    ["🤖", "باز کردن ربات", "منوی کامل ربات", `openLink('https://t.me/${esc(S.bot)}')`],
  ].filter(Boolean);
  return `<div class="title">بیشتر</div><div class="list">${rows.map(r => `<div class="item" onclick="${r[3]}"><div class="ic">${r[0]}</div><div class="tx"><b>${r[1]}</b><small>${esc(r[2])}</small></div><span class="chev">‹</span></div>`).join("")}</div>
    <div class="card sm mut c">شناسه‌ی تو: <b class="ltr">${S.me ? S.me.id : ""}</b>${S.me && S.me.since ? "، عضو از " + day(S.me.since) : ""}</div>`;
};
VIEWS.gift = async () => `<div class="title">🎁 کد هدیه</div><div class="card"><div class="sm mut mb">کد هدیه را وارد کن؛ مبلغ آن بلافاصله به کیف پولت اضافه می‌شود.</div><div class="row"><input class="input grow" id="gift" placeholder="کد هدیه" autocomplete="off"><button class="btn pri" onclick="redeem()">اعمال</button></div></div>`;
VIEWS.apps = async () => `<div class="title">📱 اپلیکیشن‌های اتصال</div><div id="apps" class="list"><div class="loading"><i class="spin"></i>…</div></div><div class="note">لینک اشتراک سرویس را کپی کن و در برنامه، گزینه‌ی «افزودن از کلیپ‌بورد / Import from clipboard» را بزن.</div>`;
AFTER.apps = async () => { const r = await api("apps"); const box = $("apps"); if (!box) return; box.innerHTML = r.ok && r.apps.length ? r.apps.map(a => `<div class="item" onclick="openLink(${JSON.stringify(a.link)})"><div class="ic">⬇️</div><div class="tx"><b>${esc(a.title)}</b><small class="ell ltr">${esc(a.link)}</small></div><span class="chev">‹</span></div>`).join("") : `<div class="empty">${r.ok ? "برنامه‌ای ثبت نشده." : esc(r.error)}</div>`; };
VIEWS.referral = async () => `<div class="title">👥 دعوت دوستان</div><div id="ref"><div class="loading"><i class="spin"></i>…</div></div>`;
AFTER.referral = async () => {
  const r = await api("referral"); const box = $("ref"); if (!box) return;
  if (!r.ok) { box.innerHTML = `<div class="card"><div class="empty">${esc(r.error)}</div></div>`; return; }
  const share = "https://t.me/share/url?url=" + encodeURIComponent(r.link) + "&text=" + encodeURIComponent("با این لینک عضو شو 👇");
  box.innerHTML = `<div class="card"><div class="grid"><div class="tile"><small>دعوت‌شده‌ها</small><b>${T(r.invited)}</b></div><div class="tile"><small>خریدار شده</small><b>${T(r.buyers)}</b></div></div>
    ${r.reward ? `<div class="note ok mt">🎁 برای هر خرید دوستانت (به‌جز پرداخت با کیف پول) ${T(r.reward)} تومان به کیف پولت اضافه می‌شود.</div>` : ""}${r.text ? `<div class="note mt">${r.text}</div>` : ""}
    <label class="lb">لینک دعوت تو</label><div class="code" onclick="copy(${JSON.stringify(r.link)})">${esc(r.link)}<span class="cp">📋</span></div>
    <div class="btns"><button class="btn pri" onclick="openLink(${JSON.stringify(share)})">📤 اشتراک‌گذاری</button><button class="btn" onclick="copy(${JSON.stringify(r.link)})">📋 کپی</button></div></div>`;
};
VIEWS.wheel = async () => `<div class="title">🎡 گردونه شانس <small id="wh-state"></small></div><div class="card"><div class="wheel" id="wheel"><b>🎰</b></div><button class="btn pri w" id="spin" disabled onclick="spin()">بچرخان</button><div class="sm mut c mt" id="wh-note">…</div></div>`;
let rot = 0, spinning = false;
function paintWheel(prizes) {
  const w = $("wheel"); if (!w || !prizes) return;
  const n = prizes.length, seg = 360 / n, cols = ["#6366f1", "#22d3ee", "#a78bfa", "#34d399", "#f472b6", "#fbbf24", "#60a5fa", "#fb7185"];
  w.style.background = `conic-gradient(${prizes.map((p, i) => `${cols[i % cols.length]} ${i * seg}deg ${(i + 1) * seg}deg`).join(",")})`;
  w.querySelectorAll("span").forEach(s => s.remove());
  prizes.forEach((p, i) => { const s = document.createElement("span"); s.textContent = p > 0 ? T(p) : "—"; const a = (i * seg + seg / 2) - 90; s.style.transform = `rotate(${a}deg) translate(70px) rotate(${-a}deg) translate(-50%,-50%)`; w.appendChild(s); });
}
AFTER.wheel = async () => {
  const r = await api("wheel", {check: 1}); if (!$("spin")) return;
  if (!r.ok) { $("wh-note").textContent = r.error; return; }
  paintWheel(r.prizes); $("spin").disabled = r.left > 0; $("wh-state").textContent = r.left > 0 ? "بعداً" : "آماده";
  $("wh-note").textContent = r.left > 0 ? "گردونه بعدی: " + T(Math.floor(r.left / 3600)) + " ساعت و " + T(Math.floor(r.left % 3600 / 60)) + " دقیقه دیگر" : "هر " + T(r.hours) + " ساعت یک بار می‌توانی بچرخانی";
};
async function spin() {
  if (spinning) return; spinning = true; $("spin").disabled = true;
  const r = await api("wheel");
  if (!r.ok) { toast(r.error, "bad"); spinning = false; AFTER.wheel(); return; }
  paintWheel(r.prizes);
  const n = r.prizes.length, seg = 360 / n; rot += 1800 + (360 - (r.index * seg + seg / 2)) - (rot % 360);
  $("wheel").style.transform = `rotate(${rot}deg)`; hap("medium");
  setTimeout(() => { $("wheel").querySelector("b").textContent = r.prize > 0 ? "🎉" : "😅"; toast(r.prize > 0 ? "برنده شدی: " + T(r.prize) + " تومان" : "این بار چیزی نشد؛ دفعه‌ی بعد!", r.prize > 0 ? "ok" : undefined); spinning = false; AFTER.wheel(); }, 3400);
}

/* ---- tickets */
VIEWS.tickets = async p => `<div class="title">🎫 پشتیبانی <button class="btn sm pri" onclick="go('ticket_new',{svc:${JSON.stringify(p.svc || "")}})">+ تیکت جدید</button></div>${S.support ? `<div class="card tap" onclick="openLink('https://t.me/${esc(S.support)}')"><div class="row"><div><b>گفتگوی مستقیم</b><div class="sm mut">@${esc(S.support)}</div></div><span class="chev">‹</span></div></div>` : ""}<div id="tk-list" class="list"><div class="loading"><i class="spin"></i>…</div></div>`;
AFTER.tickets = async () => {
  const r = await api("tickets"); const box = $("tk-list"); if (!box) return;
  if (!r.ok) { box.innerHTML = `<div class="empty">${esc(r.error)}</div>`; return; }
  S.tcats = r.categories || [];
  box.innerHTML = r.tickets.length ? r.tickets.map(t => `<div class="item" onclick="go('ticket',{id:${t.id},title:${JSON.stringify(t.title)}})"><div class="ic">${t.state == 2 ? "🔒" : t.state == 1 ? "💬" : "⏳"}</div><div class="tx"><div class="row"><b class="ell">${esc(t.title)}</b><span class="pill ${t.state == 2 ? "" : t.state == 1 ? "ok" : "warn"}">${t.state == 2 ? "بسته" : t.state == 1 ? "پاسخ داده شد" : "در انتظار"}</span></div><small class="ell">${t.last ? (t.last.from === "ADMIN" ? "پشتیبانی: " : "شما: ") + esc(t.last.text) : when(t.created)}</small></div></div>`).join("") : `<div class="empty">تیکتی نداری.</div>`;
};
VIEWS.ticket_new = async p => `<div class="title">تیکت جدید</div><div class="card">${S.tcats && S.tcats.length ? `<label class="lb">موضوع</label><select class="input" id="tk-cat">${S.tcats.map(c => `<option>${esc(c)}</option>`).join("")}</select>` : ""}<label class="lb">عنوان</label><input class="input" id="tk-title" placeholder="مثلاً: سرویس وصل نمی‌شود" value="${p.svc ? "سرویس " + esc(p.svc) : ""}"><label class="lb">متن</label><textarea class="input" id="tk-text" placeholder="مشکل یا سؤال خود را کامل بنویس…"></textarea><div class="btns"><button class="btn pri" id="tk-send" onclick="sendTicket()">ارسال</button><button class="btn" onclick="back()">انصراف</button></div></div>`;
async function sendTicket() {
  const b = $("tk-send"); b.disabled = true;
  const r = await api("ticket_new", {title: $("tk-title").value, text: $("tk-text").value, category: $("tk-cat") ? $("tk-cat").value : ""});
  if (!r.ok) { toast(r.error, "bad"); b.disabled = false; return; }
  toast("تیکت ثبت شد ✓", "ok"); S.stack.pop(); go("ticket", {id: r.id, title: $("tk-title").value});
}
VIEWS.ticket = async p => `<div class="title ell">${esc(p.title || "تیکت")}</div><div class="card"><div class="msgs" id="tk-msgs"><div class="loading"><i class="spin"></i>…</div></div><div class="row mt" id="tk-reply-row"><input class="input grow" id="tk-reply" placeholder="پاسخ…" onkeydown="if(event.key==='Enter')replyTicket(${p.id})"><button class="btn pri" onclick="replyTicket(${p.id})">↩︎</button></div></div>`;
AFTER.ticket = async p => {
  const r = await api("ticket_messages", {id: p.id}); const box = $("tk-msgs"); if (!box) return;
  box.innerHTML = r.ok ? (r.messages.length ? r.messages.map(m => `<div class="msg ${m.from === "ADMIN" ? "a" : "u"}">${esc(m.text)}<small>${m.from === "ADMIN" ? "پشتیبانی، " : ""}${when(m.at)}</small></div>`).join("") : `<div class="empty">پیامی نیست.</div>`) : `<div class="empty">${esc(r.error)}</div>`;
  box.scrollTop = box.scrollHeight;
};
async function replyTicket(id) { const t = ($("tk-reply").value || "").trim(); if (!t) return; const r = await api("ticket_reply", {id, text: t}); if (!r.ok) { toast(r.error, "bad"); return; } $("tk-reply").value = ""; AFTER.ticket({id}); }

boot();
