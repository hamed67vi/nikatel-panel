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
    features: { sell: true, wallet: true, card: true, gift: true, wheel: true, report: true, renew: true, addvolume: true, adddays: true, apps: true, hold: true, referral: true }, amounts: [100000, 200000], min_topup: 20000, support: "sup_port", bot: "my_bot", start: "" },
  services: { ok: true, services: [{ id: 7, remark: BADNAME, server: "🇩🇪 S\"1", expire: NOW + 5 * 86400, active: true }, { id: 8, remark: "old", server: "S2", expire: NOW - 3600, active: false }] },
  live: { ok: true, id: 7, live: { used: 1073741824 * 8.5, limit: 1073741824 * 50, expire: NOW + 86400, status: "active", hold: 0 }, status_fa: "فعال" },
  service: { ok: true, service: { id: 7, remark: BADNAME, server: "S\"1", expire: NOW + 5 * 86400, active: true, plan: { id: 1, title: "p\"q'r" }, status_fa: "فعال",
    live: { used: 1073741824, limit: 1073741824 * 2, expire: NOW + 86400 * 5, status: "active", hold: 0 }, sub: "https://h.example/sub/a\"b?x=1&y=2", links: ["vless://x\"y@h.example:443?a=1&b=2#n'1", "vmess://zzz"] } },
  quote: { ok: true, renew: { price: 130000, volume: 50, days: 30, title: "t" }, volume: { gb_price: 2500, presets: [{ gb: 5, price: 12000 }] }, days: { day_price: 1500, presets: [{ days: 7, price: 9000 }] }, wallet: 500000 },
  plans: { ok: true, plans: [{ id: 11, title: "P\"1", descr: "d\nx", price: 130000, list_price: 150000, discount: 10, volume: 0, days: 0, users: 1, server: "S\"1", category: "C\"1", sold_out: false }], sell: true, cashback: 5 },
  history: { ok: true, pays: [
    { type: "شارژ کیف پول", kind: "INCREASE_WALLET", price: 100, state: "approved", state_fa: "تأیید شد", at: NOW, note: "" },
    { type: "شارژ کیف پول", kind: "INCREASE_WALLET", price: 200, state: "declined", state_fa: "رد شد", at: NOW, note: "" },
    { type: "خرید سرویس", kind: "BUY_SUB", price: 300, state: "paid_with_wallet", state_fa: "از کیف پول", at: NOW, note: "n\"1" },
    { type: "خرید سرویس", kind: "BUY_SUB", price: 400, state: "low_wallet", state_fa: "موجودی کم", at: NOW, note: "" }], cashback: [{ at: NOW, order: 1000, back: 50 }] },
  referral: { ok: true, link: "https://t.me/my_bot?start=1", invited: 3, buyers: 1, reward: 5000, text: "hello <b>x</b>" },
  apps: { ok: true, apps: [{ title: "t\"1", link: "https://x.example/?a=\"b&c='d" }] },
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
if (process.argv[3] === "noconnect") R.me = { ok: false, error: "down" };
ctx.Telegram = { WebApp: { initData: "x=1", initDataUnsafe: {}, colorScheme: "dark", ready() {}, expand() {}, onEvent() {}, setHeaderColor() {}, setBackgroundColor() {}, enableClosingConfirmation() {}, BackButton: { onClick() {}, show() {}, hide() {} } } };
vm.createContext(ctx);
vm.runInContext(src + "\n;globalThis.__X = Object.fromEntries(['VIEWS','AFTER','S','esc','js','T','GB','left','leftTx','vol','ltr','boot','setTab','renewSheet','extraSheet','reportSheet','buySheet','render'].map(n => [n, (() => { try { return eval(n); } catch (e) { return undefined; } })()]));", ctx);
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
  HTML.push(await X.VIEWS.bought({ ok: true, order_id: 7, remark: BADNAME, cashback: 5, sub: "https://h.example/sub/a\"b", links: ["vless://a\"b", "vmess://c"] }));
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
  out.values.copyHandlers = handlers.filter(h => /^copy\(/.test(h[1])).map(h => h[1].slice(0, 80));
  out.values.hist = HTML.filter(h => h.includes("item static")).join("").match(/<b class="ltr amt[^"]*">[^<]*<\/b>/g);
  out.values.svcRow = (HTML.filter(h => h.includes('id="svc-8"'))[0] || "").match(/<span>[^<]*<\/span><\/div>/g);
  console.log(JSON.stringify(out));
})().catch(e => { console.log(JSON.stringify({ crash: String(e && e.stack || e) })); });
