// runs app.js of the Mini App in a bare vm with a fake document and a fake api, draws every view and bottom sheet with awkward data,
// and prints what a browser would trip over: tags that do not parse, inline handlers that are not JavaScript, and a few values of the helpers
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[2], "utf8");
const HTML = [], els = {};
function mk(id) {
  if (els[id]) return els[id];
  let html = "";
  const cl = new Set();
  const e = { id, value: "", textContent: "", dataset: {}, style: {}, scrollTop: 0, scrollHeight: 0, disabled: false,
    classList: { add: x => cl.add(x), remove: x => cl.delete(x), toggle: x => cl.has(x) ? cl.delete(x) : cl.add(x), contains: x => cl.has(x) },
    querySelector: () => null, querySelectorAll: () => [], appendChild() {}, remove() {}, focus() {}, select() {} };
  Object.defineProperty(e, "innerHTML", { get: () => html, set: v => { html = String(v); HTML.push(html); } });
  Object.defineProperty(e, "outerHTML", { set: v => { HTML.push(String(v)); } });
  return els[id] = e;
}
const NOW = Math.floor(Date.now() / 1000), BADNAME = "a\"b'c<d>&e";
const R = {
  me: { ok: true, user: { id: 1, name: "🔥 Ali", username: "a", wallet: 500000, agent: false, services: 1, pending_topups: 1, since: NOW - 86400 * 90 },
    features: { sell: true, wallet: true, card: true, currency: true, gift: true, wheel: true, report: true, renew: true, addvolume: true, adddays: true, apps: true, hold: true, referral: true }, amounts: [100000, 200000], min_topup: 20000, support: "sup_port", bot: "my_bot", start: "" },
  services: { ok: true, services: [{ id: 7, remark: BADNAME, server: "🇩🇪 S\"1", expire: NOW + 5 * 86400, active: true }, { id: 8, remark: "old", server: "S2", expire: NOW - 3600, active: false }] },
  live: { ok: true, id: 7, live: { used: 1073741824 * 8.5, limit: 1073741824 * 50, expire: NOW + 86400, status: "active", hold: 0 }, status_fa: "فعال" },
  service: { ok: true, service: { id: 7, remark: BADNAME, server: "S\"1", expire: NOW + 5 * 86400, active: true, plan: { id: 1, title: "p\"q'r" }, status_fa: "فعال",
    live: { used: 1073741824, limit: 1073741824 * 2, expire: NOW + 86400 * 5, status: "active", hold: 0 }, sub: "https://h.example/sub/a\"b?x=1&y=2", sub_alt: ["https://alt1.example/sub/tok", "https://alt2.example/sub/to\"k'1"], links: ["vless://x\"y@h.example:443?a=1&b=2#n'1", "vmess://zzz"] } },
  quote: { ok: true, renew: { price: 130000, volume: 50, days: 30, title: "t" }, volume: { gb_price: 2500, presets: [{ gb: 5, price: 12000 }] }, days: { day_price: 1500, presets: [{ days: 7, price: 9000 }] }, wallet: 500000 },
  plans: { ok: true, plans: [{ id: 11, title: "P\"1", descr: "d\nx", price: 130000, list_price: 150000, discount: 10, volume: 0, days: 0, users: 1, server: "S\"1", category: "C\"1", sold_out: false }], sell: true, cashback: 5 },
  history: { ok: true, pays: [
    { type: "شارژ کیف پول", kind: "INCREASE_WALLET", price: 100, state: "approved", state_fa: "تأیید شد", at: NOW, note: "" },
    { type: "شارژ کیف پول", kind: "INCREASE_WALLET", price: 200, state: "declined", state_fa: "رد شد", at: NOW, note: "" },
    { type: "خرید سرویس", kind: "BUY_SUB", price: 300, state: "paid_with_wallet", state_fa: "از کیف پول", at: NOW, note: "n\"1" },
    { type: "خرید سرویس", kind: "BUY_SUB", price: 400, state: "low_wallet", state_fa: "موجودی کم", at: NOW, note: "" }], cashback: [{ at: NOW, order: 1000, back: 50 }] },
  referral: { ok: true, link: "https://t.me/my_bot?start=1", invited: 3, buyers: 1, reward: 5000, text: "hello <b>x</b>" },
  apps: { ok: true, apps: [{ title: "t\"1", link: "https://x.example/?a=\"b&c='d", rec: false, os: "" }, { title: "AAA plain iOS", link: "https://apps.apple.com/app/aaa/id1", rec: false, os: "ios" }, { title: "Karing iPhone", link: "https://apps.apple.com/app/karing/id2", rec: true, os: "ios" }, { title: "Karing android x\"y", link: "https://github.com/KaringX/karing/releases/latest", rec: true, os: "android" }, { title: "v2rayN win", link: "https://github.com/2dust/v2rayN/releases/latest", rec: false, os: "windows" }, { title: "Happ mac", link: "https://apps.apple.com/app/happ/id3", rec: false, os: "mac" }] },
  tickets: { ok: true, tickets: [{ id: 3, title: "t\"x", state: 0, created: NOW, last: { from: "ADMIN", text: "hi\"" } }], categories: ["a\"b", "c"] },
  ticket_messages: { ok: true, messages: [{ text: "m\"1", from: "USER", at: NOW }] },
};
const calls = [];
const ctx = {
  console, setTimeout: () => 0, clearTimeout() {}, Date, Math, JSON, Number, String, Array, Object, Promise, parseInt, encodeURIComponent, Intl, Set,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} }, navigator: {}, confirm: () => true, FormData: function () { this.append = () => {}; },
  document: { getElementById: mk, querySelector: () => mk("q"), querySelectorAll: () => [], createElement: () => mk("c"), documentElement: { dataset: {} }, body: mk("body") },
  fetch: async (url) => { const a = /a=(\w+)/.exec(url)[1]; calls.push(a); return { json: async () => (R[a] || { ok: false, error: "no " + a }) }; },
  scrollTo() {}, open() {},
};
ctx.window = ctx; ctx.scrollTo = () => {};
// "payflow": what the page sends with a payment (a key per sheet) and when it keeps or drops it
const bodies = [];
if (process.argv[3] === "payflow" || process.argv[3] === "discount") ctx.fetch = async (url, opt) => { const a = /a=(\w+)/.exec(url)[1]; let b = {}; try { b = JSON.parse(opt.body); } catch (e) {} bodies.push({ a, b }); if (R.__net) throw new Error("net"); return { json: async () => (R[a] || { ok: false, error: "no " + a }) }; };
if (process.argv[3] === "noconnect") R.me = { ok: false, error: "down" };
// "hostile": the support id and the bot name come from the database / baseInfo.php; a quote in them must stay inside the string of the handler (not end it and run the rest)
const HOSTILE = "x');alert(1);('";
if (process.argv[3] === "buygroups") {      // the staged first step of the Buy page: three servers, two time categories on the first
  R.me.features.buy_groups = true;
  const pl = (id, sid, srv, cat, price) => ({ id, title: "plan" + id, descr: "", price, list_price: price, discount: 0, volume: 10, days: 30, users: 1, server: srv, server_id: sid, category: cat, sold_out: false });
  R.plans = { ok: true, plans: [pl(11, 1, "S1 \"x\"", "c1", 100000), pl(12, 1, "S1 \"x\"", "c2", 90000), pl(13, 2, "S2", "c1", 70000), pl(14, 3, "S3", "c1", 50000), pl(15, 3, "S3", "c1", 40000)], sell: true, cashback: 0 };
}
if (process.argv[3] === "buymerge" || process.argv[3] === "buymerge1") {      // the bot merged server 2 into server 1: the Buy page shows ONE kind (S1) holding the plans of both, S3 stays apart
  R.me.features.buy_groups = true;
  const pl = (id, sid, gid, grp, srv, cat, price) => ({ id, title: "plan" + id, descr: "", price, list_price: price, discount: 0, volume: 10, days: 30, users: 1, server: srv, server_id: sid, group_id: gid, group: grp, category: cat, sold_out: false });
  R.plans = { ok: true, plans: [pl(11, 1, 1, "S1", "S1", "c1", 100000), pl(12, 1, 1, "S1", "S1", "c2", 90000), pl(13, 2, 1, "S1", "S2", "c1", 70000), pl(16, 2, 1, "S1", "S2", "UNLIM", 300000), pl(14, 3, 3, "S3", "S3", "c1", 50000)], sell: true, cashback: 0 };
  if (process.argv[3] === "buymerge1") R.plans.plans.pop();      // the main bot today: the merged pair is the only kind
}
if (process.argv[3] === "history") {      // the wallet page: the last three rows at once, the older ones (and the cashbacks) behind one button
  R.history.pays = [1, 2, 3, 4, 5].map(i => ({ type: "خرید سرویس", kind: "BUY_SUB", price: 1000 * i, state: "paid_with_wallet", state_fa: "از کیف پول", at: NOW - i, note: "" }));
}
if (process.argv[3] === "nodiscount") R.me.features.discount_code = false;      // the owner switched the discount-code field off: no field in the buy sheet
if (process.argv[3] === "discount") R.discount_check = { ok: true, code: HOSTILE, discount: 30000, price: 100000, list_price: 130000 };
if (process.argv[3] === "noticket") R.me.features.ticket_new = false;      // the owner switched new tickets off in the Mini App for now: no "new ticket" button on the support page
if (process.argv[3] === "nochat") R.me.features.support_chat = false;      // the admin switched the direct chat off: no card that opens the operator's chat
if (process.argv[3] === "hostile") { R.me.support = HOSTILE; R.me.bot = HOSTILE; R.me.features.card = false; }
ctx.Telegram = { WebApp: { initData: "x=1", initDataUnsafe: {}, colorScheme: "dark", ready() {}, expand() {}, onEvent() {}, setHeaderColor() {}, setBackgroundColor() {}, enableClosingConfirmation() {}, BackButton: { onClick() {}, show() {}, hide() {} } } };
vm.createContext(ctx);
vm.runInContext(src + "\n;globalThis.__X = Object.fromEntries(['VIEWS','AFTER','S','esc','js','T','GB','left','leftTx','vol','ltr','boot','setTab','renewSheet','extraSheet','reportSheet','buySheet','render','doBuy','doRenew','doExtra','RID','buyPick','buyBack','buyCatPick','histMore','dcApply','dcClear','homeSearch','homeFind','homeMore','svcSearch','svcFind','back','qrOf','qrMany','cfgSheet','planMore','updateSheet','doUpdate'].map(n => [n, (() => { try { return eval(n); } catch (e) { return undefined; } })()]));", ctx);
const X = ctx.__X;
const out = { values: {}, errors: [], calls };
const tick = () => new Promise(r => setImmediate(r));
(async () => {
  await tick(); await tick();
  if (process.argv[3] === "noconnect") {
    const before = calls.length; X.setTab("wallet"); await tick(); await tick();
    out.values.noconnect = { me: calls.filter(a => a === "me").length, history: calls.filter(a => a === "history").length };
    console.log(JSON.stringify(out)); return;
  }
  if (process.argv[3] === "buygroups") {
    X.S.stack = [{ v: "buy" }]; await X.render(); await tick(); await tick();
    const step1 = els["b-list"].innerHTML, seg1 = els["b-seg"].innerHTML;
    X.buyPick("1");
    const step2 = els["b-list"].innerHTML, seg2 = els["b-seg"].innerHTML;
    X.buyPick("3");
    const step3 = els["b-list"].innerHTML;
    X.buyBack();
    const back = els["b-list"].innerHTML;
    out.values.buygroups = { cards1: (step1.match(/onclick="buyPick\(/g) || []).length, plans1: (step1.match(/buySheet\(/g) || []).length, seg1: seg1 === "", title1: step1.includes("نوع سرویس را انتخاب کن"), from3: step1.includes("از ۴۰٬۰۰۰ تومان") || step1.includes("از ۴۰,۰۰۰ تومان"),
      plans2: (step2.match(/buySheet\(/g) || []).length, only1: step2.includes("plan11") && !step2.includes("plan13") && !step2.includes("plan14"), backBtn: step2.includes("buyBack()"), tabs2: (seg2.match(/buyCatPick/g) || []).length,
      plans3: (step3.match(/buySheet\(/g) || []).length, tabs3: 0, again: back === step1 };
    console.log(JSON.stringify(out)); return;
  }
  if (process.argv[3] === "discount" || process.argv[3] === "nodiscount") {
    R.buy = { ok: true, order_id: 9, remark: "r", wallet: 1, cashback: 0, sub: "", links: [], expire: 0, discount: 30000 };
    X.S.plans = R.plans.plans; X.S.me = R.me.user;      // plan 11 costs 130000, the wallet has 500000
    X.buySheet(11); const sheet0 = els["sheet-body"].innerHTML;
    if (process.argv[3] === "nodiscount") { out.values.nodiscount = { field: sheet0.includes("dc-code") || sheet0.includes("dcApply") }; console.log(JSON.stringify(out)); return; }
    mk("dc-code").value = "  " + HOSTILE + "  "; await X.dcApply(11);
    const sheet1 = els["sheet-body"].innerHTML;
    await X.doBuy(11);
    X.buySheet(11); const sheet2 = els["sheet-body"].innerHTML;
    const buy = bodies.filter(b => b.a === "buy").map(b => b.b), chk = bodies.filter(b => b.a === "discount_check").map(b => b.b);
    out.values.discount = { field: sheet0.includes('id="dc-code"') && sheet0.includes("dcApply(11)") && !sheet0.includes("readonly"), plain: sheet0.includes("پرداخت " + X.T(130000) + " تومان"),
      checkBody: chk.length === 1 && chk[0].plan_id === 11 && chk[0].code === HOSTILE, applied: sheet1.includes("readonly") && sheet1.includes("dcClear(11)") && sheet1.includes('value="' + X.esc(HOSTILE) + '"'),
      newPrice: sheet1.includes("پرداخت " + X.T(100000) + " تومان") && sheet1.includes('<s class="mut">' + X.T(130000) + "</s>") && sheet1.includes(X.T(30000)),
      buyBody: buy.length === 1 && buy[0].plan_id === 11 && buy[0].code === HOSTILE && typeof buy[0].rid === "string", after: sheet2.includes('id="dc-code"') && !sheet2.includes("readonly") };
    console.log(JSON.stringify(out)); return;
  }  if (process.argv[3] === "noticket" || process.argv[3] === "ticket") {
    X.S.stack = [{ v: "tickets" }]; await X.render(); await tick(); await tick();
    out.values.ticket = { newButton: HTML.some(h => h.includes("go('ticket_new'")), list: HTML.some(h => h.includes("tk-list")), chat: HTML.some(h => h.includes("sup_port")) };
    console.log(JSON.stringify(out)); return;
  }  if (process.argv[3] === "nochat" || process.argv[3] === "chat") {
    for (const v of ["home", "more", "tickets"]) { X.S.stack = [{ v }]; await X.render(); await tick(); }
    out.values.chat = { cards: HTML.filter(h => h.includes("sup_port")).length, tickets: HTML.some(h => h.includes("tk-list")) };
    console.log(JSON.stringify(out)); return;
  }  if (process.argv[3] === "buymerge1") {      // one kind only (a merged pair): no first step, the page is the time tabs, and no plan has a heading of the merged server
    X.S.stack = [{ v: "buy" }]; await X.render(); await tick(); await tick();
    const page = els["b-list"].innerHTML, seg = els["b-seg"].innerHTML;
    X.buyCatPick({ dataset: { c: "UNLIM" } });
    const unlim = els["b-list"].innerHTML;
    out.values.buymerge1 = { noStepOne: !page.includes("buyPick("), tabs: (seg.match(/buyCatPick/g) || []).length, plans: (page.match(/buySheet\(/g) || []).length, heads: (page.match(/<h3>/g) || []).length, head: page.includes("<h3>S1</h3>") && !page.includes("S2"), byPrice: page.indexOf("plan13") < page.indexOf("plan11"), unlim: unlim.includes("plan16") && unlim.includes("<h3>S1</h3>") && !unlim.includes("S2") };
    console.log(JSON.stringify(out)); return;
  }
  if (process.argv[3] === "buymerge") {
    X.S.stack = [{ v: "buy" }]; await X.render(); await tick(); await tick();
    const step1 = els["b-list"].innerHTML;
    X.buyPick("1");
    const step2 = els["b-list"].innerHTML, seg2 = els["b-seg"].innerHTML;
    X.buyCatPick({ dataset: { c: "UNLIM" } });
    const unlim = els["b-list"].innerHTML;
    out.values.buymerge = { cards1: (step1.match(/onclick="buyPick\(/g) || []).length, noS2: !step1.includes("S2"), s1n: /[4۴] پلن/.test(step1), s3: step1.includes("S3"), order: step1.indexOf("S1") < step1.indexOf("S3"),
      tabs2: (seg2.match(/buyCatPick/g) || []).length, c1plans: (step2.match(/buySheet\(/g) || []).length, c1both: step2.includes("plan11") && step2.includes("plan13") && !step2.includes("plan14"), byPrice: step2.indexOf("plan13") < step2.indexOf("plan11"),
      cards2: (step2.match(/<h3>/g) || []).length, head2: step2.includes("<h3>S1</h3>") && !step2.includes("S2"), unlimOnly: unlim.includes("plan16") && !unlim.includes("plan11") && unlim.includes("<h3>S1</h3>") && !unlim.includes("S2") };
    console.log(JSON.stringify(out)); return;
  }
  if (process.argv[3] === "search") {      // the small search button of "my services": the home card and the services page look through ALL services (name, server), active or not
    R.services.services = [{ id: 7, remark: BADNAME, server: "🇩🇪 S\"1", expire: NOW + 5 * 86400, active: true }, { id: 8, remark: "old", server: "S2", expire: NOW - 3600, active: false }]
      .concat([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map(i => ({ id: 20 + i, remark: "tak_" + (100 + i), server: "Tak ی کا", expire: NOW + 86400 * i, active: true })));
    X.S.services = null; X.S.stack = [{ v: "home" }]; await X.render(); await tick(); await tick();      // (the app's own start already cached the two services of the default data)
    const rows = h => (h.match(/onclick="go\('service'/g) || []).length;
    const home0 = els["h-svc"].innerHTML, box = mk("h-sbox"), inp = mk("h-q"), res = {};
    box.hidden = true;      // (the page draws it with the hidden attribute)
    res.header = HTML.some(h => h.includes('onclick="homeSearch()"') && h.includes("🔍") && h.includes('id="h-count"') && h.includes('id="h-q"'));
    res.default3 = rows(home0) === 3 && home0.includes("setTab('services')");      // (three active services and the "all" button, as before)
    X.homeSearch(); res.opened = box.hidden === false;
    inp.value = "old"; X.homeFind(); res.inactiveFound = rows(els["h-svc"].innerHTML) === 1 && els["h-svc"].innerHTML.includes("old");
    inp.value = "  OLD  "; X.homeFind(); res.caseAndBlanks = rows(els["h-svc"].innerHTML) === 1;
    inp.value = "zzz"; X.homeFind(); res.none = rows(els["h-svc"].innerHTML) === 0 && els["h-svc"].innerHTML.includes("پیدا نشد");
    inp.value = "tak"; X.homeFind(); const many = els["h-svc"].innerHTML; res.many = rows(many) === 8 && many.includes("homeMore()") && many.includes(X.T(12));
    inp.value = "۱۰۳"; X.homeFind(); res.persianDigits = rows(els["h-svc"].innerHTML) === 1 && els["h-svc"].innerHTML.includes("tak_103");
    inp.value = "ي كا"; X.homeFind(); res.arabicLetters = rows(els["h-svc"].innerHTML) === 8;      // (typed with an Arabic yeh and kaf - an Arabic keyboard - it finds the Persian letters of the server name)
    inp.value = "a\"b"; X.homeFind(); res.hostileQuery = rows(els["h-svc"].innerHTML) === 1;      // (a quote in the query is just text)
    const isDefault = h => rows(h) === 3 && h.includes("setTab('services')");      // (the same three rows and the "all" button; the live numbers already loaded may differ)
    inp.value = ""; X.homeFind(); res.backToDefault = isDefault(els["h-svc"].innerHTML);
    inp.value = "tak"; X.homeFind(); X.homeSearch(); res.closed = box.hidden === true && inp.value === "" && isDefault(els["h-svc"].innerHTML);
    inp.value = "tak"; X.homeMore(); await tick(); await tick();
    res.moreOpensServices = X.S.stack.length === 1 && X.S.stack[0].v === "services" && X.S.stack[0].q === "tak";
    const page = HTML.filter(h => h.includes('id="s-q"')).pop() || "";
    res.pagePrefilled = page.includes('value="tak"') && page.includes('id="s-sbox"') && !/id="s-sbox"\s+hidden/.test(page) && page.includes('onclick="svcSearch()"');
    res.pageList = rows(els["s-list"].innerHTML) === 12 && els["s-count"].textContent.includes(X.T(12) + " نتیجه");
    const sq = mk("s-q"); sq.value = "old"; X.svcFind(); res.pageInactive = rows(els["s-list"].innerHTML) === 1 && X.S.stack[0].q === "old";      // (the active tab is on, the search still finds the inactive one)
    sq.value = "nothing"; X.svcFind(); res.pageNone = rows(els["s-list"].innerHTML) === 0 && els["s-list"].innerHTML.includes("پیدا نشد") && !els["s-list"].innerHTML.includes("خرید سرویس");
    const sbox = mk("s-sbox"); sbox.hidden = false; X.svcSearch(); res.pageClosed = sbox.hidden === true && !X.S.stack[0].q && rows(els["s-list"].innerHTML) === 13;      // (13 = the active ones)
    X.S.stack = [{ v: "services", q: BADNAME }]; await X.render(); await tick(); await tick();
    const hostile = HTML.filter(h => h.includes('id="s-q"')).pop() || "";
    res.escaped = hostile.includes('value="' + X.esc(BADNAME) + '"') && !hostile.includes('value="' + BADNAME + '"');
    X.S.stack = [{ v: "services" }, { v: "service", p: { id: 7 } }]; X.S.stack[0].q = "tak"; X.back(); await tick(); await tick();
    res.keptOnBack = (HTML.filter(h => h.includes('id="s-q"')).pop() || "").includes('value="tak"');      // (a tap on a service and back keeps the typed words)
    X.setTab("wallet"); await tick(); X.setTab("services"); await tick(); await tick();
    res.freshOnTab = (HTML.filter(h => h.includes('id="s-q"')).pop() || "").includes('value=""');      // (a tab switch starts empty)
    out.values.search = res;
    console.log(JSON.stringify(out)); return;
  }
  if (process.argv[3] === "qr") {      // the link cards: a QR button (a real QR icon) where the copy button was, one in the heading row of the helper links, the QR in a sheet with the link under it
    const card = (h, head) => { const i = h.indexOf("<h3>" + head); return i < 0 ? "" : h.slice(i, h.indexOf("</h3>", i) + 5); };
    const res = {};
    X.S.stack = [{ v: "services" }, { v: "service", p: { id: 7 } }]; await X.render(); await tick(); await tick();
    const page = els["svc-detail"].innerHTML, head = card(page, "لینک اشتراک");
    res.mainHeader = head.includes('onclick="qrOf(') && head.includes('class="qri"') && head.includes("QR</button>") && !head.includes("کپی") && !head.includes("copy(");      // (no copy button any more: the QR button is in its place)
    res.iconNotGlyph = !page.includes("▦") && head.includes("<svg") && head.includes('fill="currentColor"');      // (a real QR picture, not the grid character that looked like a table)
    res.linkStillCopies = /<div class="code" onclick="copy\(/.test(page.slice(page.indexOf(head), page.indexOf("لینک کمکی")));      // (a tap on the link itself still copies it)
    res.noInlineQr = !page.includes('id="qr"') && !page.includes("showQr(");
    res.appsButton = page.includes("go('apps')");
    const alt = page.slice(page.indexOf("<h3>📎 لینک کمکی اشتراک"), page.indexOf("کانفیگ‌ها <span")), altHead = card(page, "📎 لینک کمکی اشتراک");
    res.helperHeader = altHead.includes('onclick="qrMany(') && altHead.includes('class="qri"') && altHead.includes("QR</button>") && !altHead.includes("copy(");      // (the QR button is in the row of the card's name, like the main link's)
    res.helperRows = !alt.includes("copyrow") && (alt.match(/<div class="code mb" onclick="copy\(/g) || []).length === 2 && (alt.match(/onclick="qrMany\(/g) || []).length === 1 && alt.includes("لینک کمکی')");
    const manage = page.slice(page.indexOf("مدیریت سرویس"));
    res.manageNoDuplicate = !manage.includes("qrOf(") && !manage.includes("qrMany(") && !manage.includes('class="qri"');
    X.S.feat = Object.assign({}, X.S.feat, { svc_tools: true }); R.service.service.tools = { update: true };      // (the people who have the new tools: the header button is the same, the manage card has no second one)
    X.S.stack = [{ v: "services" }, { v: "service", p: { id: 7 } }]; await X.render(); await tick(); await tick();
    const page2 = els["svc-detail"].innerHTML, manage2 = page2.slice(page2.indexOf("مدیریت سرویس"));
    res.toolsSame = card(page2, "لینک اشتراک").includes('onclick="qrOf(') && !manage2.includes("qrOf(") && !manage2.includes('class="qri"') && page2.includes("cfgSheet()");
    res.cfgRowsHaveIcon = (() => { X.cfgSheet(); const s = els["sheet-body"].innerHTML; return s.includes('onclick="cfgQr(0)" aria-label="QR"><svg') && !s.includes("▦"); })();
    const bought = await X.VIEWS.bought({ ok: true, order_id: 7, remark: BADNAME, cashback: 0, sub: "https://h.example/sub/a\"b", sub_alt: ["https://alt1.example/sub/a\"b", "https://alt2.example/sub/c"], links: ["vless://a"] });
    const bh = card(bought, "لینک اشتراک");
    res.boughtHeader = bh.includes('onclick="qrOf(') && !bh.includes("کپی") && !bought.includes('id="qr"') && !bought.includes("showQr(") && (bought.match(/onclick="qrOf\(/g) || []).length === 1 && (bought.match(/onclick="qrMany\(/g) || []).length === 1;      // (the main link + the helper links' one)
    // the sheets
    ctx.qrcode = function () { return { addData() {}, make() {}, getModuleCount: () => 25, createDataURL: () => "data:image/gif;base64,AAAA" }; };
    X.qrOf("https://h.example/sub/a\"b<c", "لینک کمکی"); const s1 = els["sheet-body"].innerHTML;
    res.sheetWithQr = s1.includes('<img alt="QR" src="data:image/gif;base64,AAAA">') && s1.includes('class="qri"') && s1.includes("QR لینک کمکی") && s1.includes("a&quot;b&lt;c") && !s1.includes("a\"b<c") && /onclick="copy\(/.test(s1) && s1.includes("closeSheet()");
    X.qrOf("https://h.example/sub/x"); res.defaultTitle = els["sheet-body"].innerHTML.includes("QR لینک اشتراک");
    X.qrMany("https://alt1.example/sub/a\nhttps://alt2.example/sub/b", "لینک کمکی"); const s2 = els["sheet-body"].innerHTML;
    res.manyLinks = (s2.match(/<img alt="QR"/g) || []).length === 2 && s2.includes("alt1.example/sub/a") && s2.includes("alt2.example/sub/b") && (s2.match(/onclick="copy\(/g) || []).length === 2 && s2.includes("QR لینک کمکی");
    X.qrMany("https://alt1.example/sub/only", "لینک کمکی"); const s3 = els["sheet-body"].innerHTML;
    res.oneLink = (s3.match(/<img alt="QR"/g) || []).length === 1 && s3.includes("alt1.example/sub/only");
    els["sheet-body"].innerHTML = "keep"; X.qrMany("", "x"); res.emptyList = els["sheet-body"].innerHTML === "keep";
    ctx.qrcode = function () { throw new Error("too long"); }; X.qrOf("x".repeat(5000)); res.tooLong = els["sheet-body"].innerHTML.includes("خیلی بلند است") && !els["sheet-body"].innerHTML.includes("<img");
    delete ctx.qrcode; X.qrOf("https://h.example/sub/y"); res.noLibrary = els["sheet-body"].innerHTML.includes("بارگذاری نشد") && els["sheet-body"].innerHTML.includes("h.example/sub/y");
    out.values.qr = res;
    console.log(JSON.stringify(out)); return;
  }
  if (process.argv[3] === "plans") {      // the plan cards of the Buy page: name + badge, chips, the description as a short list, price + buy button
    const mkPlan = (id, o) => Object.assign({ id, title: "plan" + id, descr: "", price: 130000, list_price: 130000, discount: 0, volume: 50, days: 30, users: 1, server: "S1", server_id: 1, category: "c1", sold_out: false }, o);
    const BIG = ["📦 حجم: 200 گیگابایت", "⏳ مدت اشتراک: یک ماهه (31 روز)", "", "✨ اشتراک ویژه مولتی‌لوکیشن با تانل آی‌پی ثابت", "📶 اتصال پایدار و آپتایم بالا روی همهٔ اپراتورها", "🎮 مناسب گیم، استریم، ترید، وب‌گردی و شبکه‌های اجتماعی", "", "⚠️ در شرایط اینترنت ملی، ضمانت اتصال وجود ندارد.", "بدون ایموجی"].join("\n");
    R.plans = { ok: true, sell: true, cashback: 0, plans: [
      mkPlan(1, { title: "200 گیگ ماهانه", descr: BIG, price: 2200000, list_price: 2200000, volume: 200, days: 31 }),
      mkPlan(2, { title: "یک ماهه 50 گیگ", descr: "🚀 سرعت بالا\n🔒 امن", price: 130000, list_price: 150000, discount: 10 }),
      mkPlan(3, { title: "تمام‌شده", descr: "🚀 x", sold_out: true }),
      mkPlan(4, { title: "بدون توضیح", days: 0, volume: 0, users: 0 }),
      mkPlan(5, { title: "P\"1<b>x</b>", descr: "<script>alert(1)</script>\n🔥 a\"b'c&d" }),
    ] };
    X.S.plans = null; X.S.stack = [{ v: "buy" }]; await X.render(); await tick(); await tick();
    const html = els["b-list"].innerHTML, res = {};
    if (process.env.PLANS_OUT) fs.writeFileSync(process.env.PLANS_OUT, html);      // (for a look at the real markup in a browser)
    const parts = html.split(/(?=<div class="plan)/).filter(s => s.startsWith('<div class="plan'));
    const p1 = parts[0] || "", p2 = parts[1] || "", p3 = parts[2] || "", p4 = parts[3] || "", p5 = parts[4] || "";
    res.five = parts.length === 5;
    res.titleAndChips = p1.includes('<div class="pt">200 گیگ ماهانه</div>') && (p1.match(/<div class="pc">(<span>[^<]*<\/span>)+<\/div>/) || [""])[0].split("<span>").length === 4 && p1.includes("📦 ") && p1.includes("⏳ " + X.T(31) + " روز") && p1.includes("👤 " + X.T(1) + " کاربر");
    res.bigListClamped = p1.includes('<ul class="pf clamp">') && (p1.match(/<li>/g) || []).length === 7 && p1.includes('onclick="planMore(this, event)"') && p1.includes("نمایش بیشتر");
    res.emojiBullets = p1.includes("<li><i>✨</i><span>اشتراک ویژه مولتی‌لوکیشن با تانل آی‌پی ثابت</span></li>") && p1.includes('<li><i class="dot"></i><span>بدون ایموجی</span></li>') && p1.includes("<li><i>⚠️</i>");
    res.blankLinesDropped = !p1.includes("<li><i class=\"dot\"></i><span></span></li>");
    res.price = p1.includes("<b>" + X.T(2200000) + "</b><small>تومان</small>") && !p1.includes("<s>") && !p1.includes("تخفیف") && p1.includes("خرید ‹") && p1.includes("buySheet(1)");
    res.discount = p2.includes("<s>" + X.T(150000) + "</s>") && p2.includes(X.T(13) + "٪ تخفیف") && p2.includes("<b>" + X.T(130000) + "</b>");
    res.shortList = p2.includes('<ul class="pf">') && (p2.match(/<li>/g) || []).length === 2 && !p2.includes("planMore");
    res.soldOut = p3.startsWith('<div class="plan off"') && p3.includes('onclick=""') && p3.includes("تمام شد") && p3.includes("ناموجود") && !p3.includes("buySheet(");
    res.noDescr = !p4.includes("<ul") && p4.includes("بدون محدودیت زمان") && !p4.includes("👤");
    res.escaped = p5.includes("P&quot;1&lt;b&gt;x&lt;/b&gt;") && p5.includes("&lt;script&gt;alert(1)&lt;/script&gt;") && !p5.includes("<script>") && !p5.includes("<b>x</b>") && p5.includes("a&quot;b&#39;c&amp;d");
    res.oneBuyHandlerPerPlan = (html.match(/buySheet\(/g) || []).length === 4;      // (the sold-out one has none)
    R.plans.sell = false; X.S.plans = null; X.S.stack = [{ v: "buy" }]; await X.render(); await tick(); await tick();
    const off = els["b-list"].innerHTML; res.sellOff = !off.includes("buySheet(") && (off.match(/class="plan off"/g) || []).length === 5 && off.includes("—");
    // the "show more" button: opens and closes the list of its own card and never lets the tap reach the card
    const cl = new Set(), ul = { classList: { contains: x => cl.has(x), toggle: (x, f) => { if (f) cl.add(x); else cl.delete(x); } } };
    let stopped = 0; const btn = { previousElementSibling: ul, textContent: "نمایش بیشتر ⌄" }, ev = { stopPropagation() { stopped++; } };
    X.planMore(btn, ev); const a = [cl.has("open"), btn.textContent]; X.planMore(btn, ev); const b = [cl.has("open"), btn.textContent]; X.planMore(btn);
    res.moreToggles = a[0] === true && a[1] === "نمایش کمتر ⌃" && b[0] === false && b[1] === "نمایش بیشتر ⌄" && cl.has("open") && stopped === 2;
    out.values.plans = res;
    console.log(JSON.stringify(out)); return;
  }
  if (process.argv[3] === "svctools") {      // the manage card of the service page: update (the stage's tools), disable / delete (the agents only), support (whoever has no tools)
    const res = {};
    const page = async (tools, agent) => {
      R.service.service.tools = tools; R.service.service.agent = agent; X.S.feat = Object.assign({}, X.S.feat, { svc_tools: !!tools });
      X.S.stack = [{ v: "services" }, { v: "service", p: { id: 7 } }]; await X.render(); await tick(); await tick();
      const h = els["svc-detail"].innerHTML; return h.slice(h.indexOf("مدیریت سرویس"));
    };
    const has = (m, x) => m.includes(x);
    const A = await page(null, null);      // a customer
    res.customer = !has(A, "updateSheet(") && !has(A, "linkSheet(") && !has(A, "toggleSheet(") && !has(A, "deleteSheet(") && has(A, "go('tickets'") && has(A, "renewSheet(");
    const B = await page({ update: true }, null);      // the stage's tools, not an agent (an admin)
    res.toolsOnly = has(B, "updateSheet(7)") && has(B, "linkSheet(7)") && has(B, "cfgSheet()") && !has(B, "toggleSheet(") && !has(B, "deleteSheet(") && !has(B, "go('tickets'");
    const C = await page(null, { toggle: "disable", delete: true });      // an agent while the tools are still the admins' only
    res.agentOnly = has(C, "toggleSheet(7,") && has(C, "⏸ غیرفعال‌سازی") && has(C, "deleteSheet(7)") && has(C, "go('tickets'") && !has(C, "updateSheet(") && !has(C, "linkSheet(") && !has(C, "cfgSheet()");
    const D = await page({ update: true }, { toggle: "enable", delete: true });      // both: an agent's service he switched off
    res.both = has(D, "updateSheet(7)") && has(D, "linkSheet(7)") && has(D, "▶️ فعال‌سازی") && has(D, "deleteSheet(7)") && !has(D, "go('tickets'");
    const E = await page(null, { toggle: null, delete: true });      // an agent's service the panel does not answer for, or an admin disabled: no switch, delete stays
    res.agentNoSwitch = !has(E, "toggleSheet(") && has(E, "deleteSheet(7)");
    const F = await page({ update: false }, null);      // the panel did not answer: no update button (the link button needs the live data too)
    res.noUpdateWithoutPanel = !has(F, "updateSheet(");
    res.danger = has(D, 'class="btn danger" onclick="deleteSheet(7)"');
    // the update sheet and its call
    X.updateSheet(7); const sh = els["sheet-body"].innerHTML;
    res.updateSheet = sh.includes("بروزرسانی اشتراک") && sh.includes('onclick="doUpdate(7)"') && sh.includes("closeSheet()");
    R.service_update = { ok: true, count: 4 }; X.S.live[7] = { used: 1 }; const n0 = calls.length;
    await X.doUpdate(7); await tick(); await tick();
    res.updateCall = calls.slice(n0).includes("service_update") && calls.slice(n0).includes("service") && X.S.live[7] === undefined && X.S.busy === false;      // (it asks, drops the cached live numbers and draws the page again)
    R.service_update = { ok: false, error: "no" }; X.updateSheet(7); const n1 = calls.length; const btn = mk("do-update");
    await X.doUpdate(7); await tick();
    res.updateRefused = calls.slice(n1).includes("service_update") && !calls.slice(n1).includes("service") && btn.disabled === false && btn.textContent === "دوباره" && X.S.busy === false;      // (a refusal leaves the sheet open with a retry)
    out.values.svctools = res;
    console.log(JSON.stringify(out)); return;
  }
  if (process.argv[3] === "history") {
    X.setTab("wallet"); await tick(); await tick(); await tick();
    const h = els["hist"].innerHTML, cut = h.indexOf('id="hist-more"'), before = h.slice(0, cut), after = h.slice(cut);
    const items = s => (s.match(/class="item static"/g) || []).length;
    const res = { shown: items(before), hidden: items(after), hiddenAtFirst: after.includes('id="hist-more" style="display:none"'), button: after.includes('id="hist-btn"') && after.includes("نمایش بقیه‌ی تاریخچه"), cashbackBehind: after.includes("کش‌بک") && !before.includes("کش‌بک"), newestFirst: before.indexOf("۱٬۰۰۰") > -1 || before.indexOf("1,000") > -1 };
    const more = mk("hist-more"), btn = mk("hist-btn"); more.style.display = "none"; btn.textContent = "نمایش بقیه‌ی تاریخچه";
    X.histMore(); res.opened = more.style.display === "" && btn.textContent === "بستن";
    X.histMore(); res.closed = more.style.display === "none" && btn.textContent === "نمایش بقیه‌ی تاریخچه";
    R.history.pays = R.history.pays.slice(0, 3); R.history.cashback = [];      // three rows or fewer: nothing to hide, no button
    X.setTab("services"); await tick(); X.setTab("wallet"); await tick(); await tick(); await tick();
    const h3 = els["hist"].innerHTML; res.fewRows = items(h3); res.fewButton = h3.includes("hist-btn") || h3.includes("hist-more");
    out.values.history = res;
    console.log(JSON.stringify(out)); return;
  }
  if (process.argv[3] === "payflow") {
    R.buy = { ok: true, order_id: 9, remark: "r", wallet: 1, cashback: 0, sub: "", links: [], expire: 0 };
    R.renew = { ok: true, expire: 1, wallet: 1, cashback: 0 };
    X.S.plans = R.plans.plans; X.S.me = R.me.user;
    X.buySheet(11);
    R.__net = false; await X.doBuy(11); await X.doBuy(11);                  // two purchases: two keys
    R.__net = true; await X.doBuy(11); await X.doBuy(11);                   // the answer was lost twice: the SAME key again
    R.__net = false; await X.doBuy(11); await X.doBuy(11);                  // an answer at last: that key once more, then it is dropped; the next purchase has a new one
    R.__net = true; await X.doRenew(7); R.__net = false; await X.doRenew(7);
    out.values.rids = bodies.filter(x => x.a === "buy").map(x => x.b.rid);
    out.values.renewRids = bodies.filter(x => x.a === "renew").map(x => x.b.rid);
    out.values.left = Object.keys(X.RID);
    console.log(JSON.stringify(out)); return;
  }
  out.values.avatar = els.ava.textContent; out.values.hello = els.hello.textContent;
  const t = ms => NOW - ms;
  out.values.left = { expired: X.left(NOW - 3600), three: X.left(NOW + 3 * 86400 - 600), none: X.left(0) };
  out.values.leftTx = X.leftTx ? { hold: X.leftTx(0, 86400 * 30), days: X.leftTx(NOW + 3 * 86400 - 600, 0), expired: X.leftTx(NOW - 3600, 0), none: X.leftTx(0, 0) } : null;
  out.values.GB = { a: X.GB(1073741824 * 8.5), b: X.GB(1073741824 * 50), c: X.GB(1073741824 * 1234.56) };
  out.values.vol = { zero: X.vol(0), g: X.vol(50), t: X.vol(2000) };
  out.values.ltr = X.ltr ? X.ltr("@sup_port") : null;
  out.values.js = X.js ? X.js("a\"b'c") : null;
  out.values.min_topup = X.S.min_topup;
  const S = X.S;
  await X.render();                                  // home
  for (const v of ["home", "buy", "services", "wallet", "more", "gift", "apps", "referral", "wheel", "tickets", "ticket_new"]) { S.stack = [{ v, p: { svc: BADNAME } }]; await X.render(); await tick(); }
  S.stack = [{ v: "services" }, { v: "service", p: { id: 7 } }]; await X.render(); await tick(); await tick();
  S.stack = [{ v: "tickets" }, { v: "ticket", p: { id: 3, title: BADNAME } }]; await X.render(); await tick();
  S.plans = R.plans.plans; X.buySheet(11);
  await X.renewSheet(7); await X.extraSheet(7, "volume"); await X.extraSheet(7, "days"); X.reportSheet(7);
  for (const kind of ["detail", "usdt"]) { S.topup = { kind, amount: 150000, exact: 150357, hash: "h", card: "6037997512345678", holder: "h\"1", usdt_wallet: "TXyZ\"abc", usdt: 3.4, text: "<b>x</b>" }; HTML.push(await X.VIEWS.topup()); }
  for (const kind of ["gift", "currency"]) { S.topup = { v: 2, kind, amount: 150000, exact: 150357, hash: "h", support: "sup_p\"ort", usdt_wallet: kind === "currency" ? "0xAb\"c" : "", usdt: kind === "currency" ? 3.4 : 0, text: "<b>x</b>" }; HTML.push(await X.VIEWS.topup()); }      // (the currency page and the "message support" page of the card_text change)
  HTML.push(await X.VIEWS.bought({ ok: true, order_id: 7, remark: BADNAME, cashback: 5, sub: "https://h.example/sub/a\"b", sub_alt: ["https://alt1.example/sub/a\"b"], links: ["vless://a\"b", "vmess://c"] }));
  HTML.push(await X.VIEWS.topup_done({ amount: 5 }));
  out.values.htmlParts = HTML.length;
  // 1) every tag must parse the way a browser parses it: name, then attributes that are bare or name="value" (a stray quote ends the value early and the rest becomes junk attributes)
  const tag = /<[a-zA-Z][a-zA-Z0-9]*(?:\s+[\w:-]+(?:="[^"]*")?)*\s*\/?>/y;
  const handlers = [];
  for (const h of HTML) {
    for (const m of h.matchAll(/<[a-zA-Z]/g)) {
      tag.lastIndex = m.index; const r = tag.exec(h);
      if (!r) { out.errors.push("tag does not parse: " + h.slice(m.index, m.index + 110).replace(/\s+/g, " ")); continue; }
      for (const a of r[0].matchAll(/\s(on\w+)="([^"]*)"/g)) handlers.push([a[1], a[2].replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&")]);
    }
  }
  // 2) every inline handler must be JavaScript
  for (const [name, code] of handlers) { try { new vm.Script(code); } catch (e) { out.errors.push(name + " is not JavaScript: " + code.slice(0, 90)); } }
  out.values.handlers = handlers.length;
  const appsHtml = HTML.filter(h => h.includes("⭐ پیشنهادی"))[0] || "";
  out.values.apps = { groups: appsHtml.match(/📱 آیفون|🤖 اندروید|🪟 ویندوز|💻 مک|📚 آموزش/g), pills: (appsHtml.match(/⭐ پیشنهادی/g) || []).length, recFirst: appsHtml.indexOf("Karing iPhone") > -1 && appsHtml.indexOf("Karing iPhone") < appsHtml.indexOf("AAA plain iOS"), noMarker: !appsHtml.includes("(پیشنهادی)") };
  out.values.altBoxes = HTML.filter(h => h.includes("لینک کمکی اشتراک")).length;
  out.values.altCopy = handlers.filter(h => /^copy\(.*alt\d\.example/.test(h[1])).length;
  // 3) a handler calls only the functions of the app: strip the string literals, list what is called; `alert(1)` after a quote that ended the string shows up here
  const known = new Set([...src.matchAll(/(?:^|\n)(?:async\s+)?function\s+(\w+)/g)].map(m => m[1]).concat(["if", "querySelectorAll", "forEach", "remove"]));
  const called = new Set();
  for (const [, code] of handlers) for (const m of code.replace(/"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`/g, '""').matchAll(/([A-Za-z_$][\w$]*)\s*\(/g)) called.add(m[1]);
  out.values.unknownCalls = [...called].filter(c => !known.has(c)).sort();
  out.values.openLinks = handlers.filter(h => /^openLink\(/.test(h[1])).map(h => h[1].slice(0, 80));
  out.values.copyHandlers = handlers.filter(h => /^copy\(/.test(h[1])).map(h => h[1].slice(0, 80));
  out.values.hist = HTML.filter(h => h.includes("item static")).join("").match(/<b class="ltr amt[^"]*">[^<]*<\/b>/g);
  out.values.svcRow = (HTML.filter(h => h.includes('id="svc-8"'))[0] || "").match(/<span>[^<]*<\/span><\/div>/g);
  console.log(JSON.stringify(out));
})().catch(e => { console.log(JSON.stringify({ crash: String(e && e.stack || e) })); });
