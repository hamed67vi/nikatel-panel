/* The phone assistant (/m). Plain JS, no build step, no library. Nothing is ever put into the page as HTML: every word from the server goes in as text. */
(() => {
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const API = '/api/commander';
const fa = n => Number(n).toLocaleString('fa-IR');

function h(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function put(parent, ...kids) {
  for (const k of kids.flat(Infinity)) if (k != null && k !== false) parent.append(k.nodeType ? k : document.createTextNode(String(k)));
  return parent;
}
const nm = name => h('bdi', 'nm', name);

// ------------------------------------------------------------------ words (Persian for the owner; the server speaks English and sends codes)
const STATE_FA = { up: 'وصل', down: 'قطع', degraded: 'ناپایدار', unknown: 'نامعلوم', starting: 'در حال ساخت' };
const LEVEL_FA = { restart: 'ری‌استارت', rebuild: 'ساخت دوباره', switch: 'تغییر نوع تونل' };
const TIER_FA = { read: 'فقط نگاه', auto: 'تعمیر خودکار', tap: 'با تایید تو' };
const STATUS_FA = { ok: 'انجام شد', refused: 'انجام نشد', failed: 'ناموفق', running: 'در حال انجام…' };
const REASON = {
  not_seen: 'اول باید تونل را نگاه می‌کردم.', limit_run: 'در یک دستور بیشتر از ۳ تغییر نمی‌دهم.', limit_hour: 'در این یک ساعت به اندازه‌ی کافی تغییر داده‌ام؛ کمی صبر کن.',
  limit_tunnel: 'این تونل در یک ساعت چند بار تغییر کرده؛ بهتر است خودت نگاهش کنی.', read_only: 'پنل فقط‌خواندنی است؛ تغییر ممکن نیست.',
  server_off: 'یکی از سرورهای این تونل خاموش است؛ تا برنگردد کاری روی تونل نمی‌کنم.', busy: 'دکتر همین الان دارد این تونل را تعمیر می‌کند.',
  bad_args: 'ورودی درست نبود.', no_tunnel: 'چنین تونلی نیست.', unknown_tool: 'این کار در فهرست کارهای من نیست.',
};
const ENDING = {
  stopped: 'با دکمه‌ی توقف تمام شد. تعمیری که دکتر شروع کرده بود، خودش تا پایان امن ادامه می‌دهد.', cancelled: 'لغو شد؛ چیزی تغییر نکرد.', expired: '۱۵ دقیقه جواب ندادی؛ چیزی تغییر نکرد.',
  restarted: 'پنل وسط کار ری‌استارت شد؛ ادامه ندادم.', too_long: 'دستور خیلی طول کشید و بسته شد.', too_many_steps: 'قدم‌هایم برای این دستور تمام شد و به نتیجه نرسیدم.',
  ai_limit: 'در یک ساعت اخیر زیاد از AI پرسیده‌ام؛ کمی بعد دوباره بنویس.', ai_unreadable: 'جواب AI قابل خواندن نبود.', crashed: 'دستیار خودش خطا خورد و ادامه نداد.',
};
const ERR = {
  not_signed_in: 'نشست تمام شده؛ دوباره وارد شو.', off: 'دستیار خاموش است.', ai_not_ready: 'AI پنل آماده نیست (کلید ندارد یا خاموش است).', busy: 'یک دستور دیگر هنوز در کار است؛ توقفش کن یا صبر کن.',
  empty: 'یک دستور بنویس.', too_many_requests: 'تلاش زیاد؛ چند دقیقه صبر کن.', answered: 'این کارت قبلاً جواب داده شده.', no_run: 'این دستور پیدا نشد.', read_only_demo: 'در دمو ممکن نیست.',
};

function fmtTime(ts) {
  try { return new Intl.DateTimeFormat('fa-IR', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(ts * 1000)); }
  catch (e) { return new Date(ts * 1000).toLocaleString(); }
}
function ago(ts) {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return 'همین الان';
  if (s < 3600) return fa(Math.floor(s / 60)) + ' دقیقه پیش';
  if (s < 86400) return fa(Math.floor(s / 3600)) + ' ساعت پیش';
  return fa(Math.floor(s / 86400)) + ' روز پیش';
}

// ------------------------------------------------------------------ talking to the panel
async function api(method, url, body) {
  const r = await fetch(url, { method, headers: body ? { 'content-type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined, credentials: 'same-origin' });
  let j = null;
  try { j = await r.json(); } catch (e) { /* not JSON */ }
  if (!r.ok) {
    const err = new Error((j && j.error) || r.statusText || 'error');
    err.status = r.status; err.code = (j && j.code) || '';
    throw err;
  }
  return j;
}
const say = e => ERR[e.code] || (e.status === 429 ? ERR.too_many_requests : e.message) || 'خطا';
const sleep = ms => new Promise(r => setTimeout(r, ms));
let poke = null;                                   // wakes the watching loop at once (after a tap on Confirm or Stop)
const nap = ms => new Promise(r => { poke = r; setTimeout(r, ms); });

// ------------------------------------------------------------------ views
let S = null;           // the state from the server
let following = 0;      // the order the page is watching
const threads = new Map();

function view(name) {
  for (const id of ['boot', 'login', 'main']) $('#' + id).classList.toggle('hidden', id !== name);
}
function banner(msg, kind, button) {
  const b = $('#banner');
  b.className = 'banner' + (kind ? ' ' + kind : '');
  b.textContent = '';
  if (!msg) { b.classList.add('hidden'); return; }
  put(b, h('div', '', msg));
  if (button) { const x = h('button', 'btn pri', button.text); x.type = 'button'; x.onclick = button.run; b.append(x); }
}
let flashTimer = 0;
function flash(msg) {
  banner(msg, 'warn');
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { if ($('#banner').textContent === msg) refreshBanner(); }, 6000);
}

function refreshBanner() {
  if (!S) return;
  if (!S.enabled) return banner('دستیار خاموش است. تا روشنش نکنی دستوری قبول نمی‌کند.', 'warn', { text: 'روشن کن', run: () => setConf({ enabled: true }) });
  if (!S.ai.ready) return banner('AI پنل آماده نیست (کلید نگذاشته‌ای یا خاموش است). در پنل: Settings ← Api & Key.', 'warn');
  if (S.read_only) return banner('پنل در حالت فقط‌خواندنی است: دستیار فقط نگاه می‌کند و تعمیر نمی‌کند.', 'warn');
  banner('');
}

async function setConf(patch) {
  try { await api('PUT', API + '/settings', patch); await refresh(); } catch (e) { flash(say(e)); }
}

async function refresh() {
  S = await api('GET', API + '/state');
  $('#ai-line').textContent = (S.ai.name || '') + ' · ' + (S.ai.model || '') + (S.careful ? ' · محتاط' : '');
  refreshBanner();
  setBusy(S.active);
  return S;
}

function setBusy(run) {
  const busy = !!run && (run.status === 'running' || run.status === 'waiting');
  $('#btn-stop').classList.toggle('hidden', !busy);
  $('#btn-stop').textContent = run && run.stopping ? 'در حال توقف…' : 'توقف';
  $('#btn-send').disabled = busy;
  $('#order').placeholder = busy ? 'یک دستور در کار است…' : 'دستورت را اینجا بنویس…';
}

// ------------------------------------------------------------------ a step, in words
function stepName(s) { return (s.names && s.names.tunnel) || ''; }
function titleOf(s) {
  const t = s.tool, n = nm(stepName(s)), a = s.args || {};
  switch (t) {
    case 'overview': return ['نگاه کلی به همه‌ی تونل‌ها و سرورها'];
    case 'list_tunnels': return [s.names && s.names.query ? ['جستجوی تونل «', s.names.query, '»'] : 'فهرست تونل‌ها'];
    case 'tunnel': return ['خواندن وضعیت تونل ', n];
    case 'diagnose': return ['عیب‌یابی تونل ', n, ' (حدود ۲۰ ثانیه)'];
    case 'history': return ['سابقه‌ی تعمیر تونل ', n];
    case 'servers': return ['فهرست سرورها'];
    case 'where_to_move': return ['پیدا کردن سرور ایران مناسب برای ', n];
    case 'kinds': return ['نوع‌های ممکن تونل'];
    case 'restart': return ['تعمیر ساده (ری‌استارت) تونل ', n];
    case 'repair': return ['تعمیر هوشمند تونل ', n, ' تا مرحله‌ی «', LEVEL_FA[a.level] || LEVEL_FA.restart, '»'];
    case 'move': return ['جابجایی تونل ', n, ' به سرور ایران دیگر'];
    case 'replace': return ['عوض کردن نوع تونل ', n, ' به ', nm(String(a.kind || ''))];
    case 'repair_all': return ['تعمیر هوشمند همه‌ی تونل‌های قطع تا مرحله‌ی «', LEVEL_FA[a.level] || LEVEL_FA.restart, '»'];
    default: return [t || 'کار نامشخص'];
  }
}
function parse(s) { try { return JSON.parse(s.result || ''); } catch (e) { return null; } }
function briefOf(s) {
  const r = parse(s);
  if (!r || s.status !== 'ok') return '';
  if (s.tool === 'overview') return fa(r.tunnels) + ' تونل؛ ' + fa(r.up) + ' تا وصل، ' + fa((r.not_up || []).length) + ' تا مشکل‌دار' + ((r.servers_off || []).length ? '؛ ' + fa(r.servers_off.length) + ' سرور خاموش' : '') + (r.mass_outage ? '؛ قطعی گسترده' : '');
  if (s.tool === 'list_tunnels') return fa(r.total) + ' تونل پیدا شد';
  if (s.tool === 'tunnel') return 'وضعیت: ' + (STATE_FA[r.state] || r.state) + (r.being_repaired ? ' (دکتر دارد تعمیر می‌کند)' : '');
  if (s.tool === 'servers') return fa((r.servers || []).length) + ' سرور';
  if (s.tool === 'diagnose') return 'علت: ' + (r.cause || '') + ' — ' + (r.summary || '');
  if (['restart', 'repair', 'move', 'replace', 'repair_all'].includes(s.tool)) {
    const end = r.done ? (r.ok ? 'تمام شد (موفق)' : 'تمام شد (ناموفق)') : r.stopped ? 'متوقف شد؛ تعمیر دکتر خودش ادامه دارد' : 'هنوز در کار است';
    return end + (r.state_now ? ' — وضعیت تونل الان: ' + (STATE_FA[r.state_now] || r.state_now) : '');
  }
  return '';
}

function toolNode(s) {
  const box = h('div', 'tool');
  const row = h('div', 'row');
  put(row, put(h('span', 't'), titleOf(s)), h('span', 'chip ' + (s.tier || ''), TIER_FA[s.tier] || ''));
  const st = h('span', 'st ' + (s.status || ''), STATUS_FA[s.status] || '');
  if (s.status === 'running') st.prepend(h('i', 'spin sm'), ' ');
  row.append(st);
  box.append(row);
  const why = s.status === 'refused' ? (REASON[s.code] || s.summary) : s.status === 'failed' ? s.summary : '';
  if (why) box.append(h('div', 'why', why));
  const b = briefOf(s);
  if (b) box.append(h('div', 'note', b));
  if (s.lines && s.lines.length) box.append(h('div', 'lines', s.lines.slice(-6).join('\n')));
  if (s.result) {
    const d = h('details', 'more'), sm = h('summary', '', 'جزئیات (انگلیسی)');
    const pre = h('pre'); pre.textContent = s.result;
    d.append(sm, pre); box.append(d);
  }
  return box;
}

function levelText(l) { return LEVEL_FA[l] || LEVEL_FA.restart; }
function proposalNode(s, readonly, rid) {
  const n = s.names || {}, a = s.args || {};
  const box = h('div', 'confirm' + (s.state === 'pending' ? '' : ' done'));
  let head, eff = '';
  if (s.tool === 'move') {
    head = ['جابجایی تونل ', nm(n.tunnel || ''), ' به سرور ایران ', nm(n.to || '')];
    eff = 'الان روی سرور ' + (n.iran || '') + ' است. تونل جدید اول روی سرور تازه ساخته می‌شود، رکوردهای DNS که فقط به همین تونل می‌رسند هم عوض می‌شوند، و تونل قدیمی چند دقیقه بعد برداشته می‌شود.';
  } else if (s.tool === 'replace') {
    head = ['عوض کردن نوع تونل ', nm(n.tunnel || ''), ' به ', nm(n.kind || '')];
    eff = 'تونل با نوع تازه ساخته می‌شود (همان اسم و پورت‌ها). اگر نوع تازه نساخت، قبلی برمی‌گردد. در این مدت ممکن است کاربران چند ثانیه تا چند دقیقه قطع شوند.';
  } else if (s.tool === 'repair_all') {
    head = ['تعمیر هوشمند همه‌ی تونل‌های قطع (', fa((n.tunnels || []).length), ' تا) تا مرحله‌ی «', levelText(n.level), '»'];
    eff = (n.tunnels || []).join('، ') || 'الان تونل قطعی نیست.';
  } else {
    head = [s.tool === 'restart' ? 'تعمیر ساده (ری‌استارت) تونل ' : 'تعمیر هوشمند تونل ', nm(n.tunnel || ''), s.tool === 'repair' ? [' تا مرحله‌ی «', levelText(n.level), '»'] : ''];
    eff = n.state === 'up' ? 'این تونل الان وصل است و ری‌استارت یا تعمیرش ممکن است چند ثانیه کاربران را قطع کند.' : (S && S.careful ? 'حالت «محتاط» روشن است، برای همین اول از تو می‌پرسم.' : 'قطعی گسترده است و این‌جور وقت‌ها اول از تو می‌پرسم.');
  }
  put(box, h('h3', '', s.state === 'pending' ? 'تایید تو لازم است' : 'کارت تایید'), put(h('p'), head), h('p', 'eff', eff));
  if (s.why) box.append(h('p', 'eff', 'دلیل دستیار: ' + s.why));
  if (s.state === 'pending') {
    const mins = Math.max(0, Math.round((s.expires - Date.now() / 1000) / 60));
    box.append(h('p', 'eff', 'این کارت ' + fa(mins) + ' دقیقه‌ی دیگر خودش بسته می‌شود.'));
    if (!readonly) {
      const bt = h('div', 'btns'), ok = h('button', 'btn pri', 'تایید و انجام بده'), no = h('button', 'btn', 'نه، لغو');
      ok.type = no.type = 'button';
      ok.onclick = () => decide(rid, s.pid, 'confirm', box);
      no.onclick = () => decide(rid, s.pid, 'cancel', box);
      bt.append(ok, no); box.append(bt);
    }
  } else {
    box.append(h('p', '', s.state === 'confirmed' ? '✔ تایید شد' : s.state === 'cancelled' ? '✖ لغو شد' : '⌛ بدون جواب ماند و بسته شد'));
  }
  return box;
}

function finalNode(s) {
  const good = s.ok === true, bad = s.ok === false;
  const box = h('div', 'final ' + (good ? 'good' : bad ? 'bad' : ''));
  const text = s.code === 'answer' ? s.text : s.code === 'ai_failed' ? 'AI جواب نداد. ' + (s.text || '').replace(/^The AI did not answer:\s*/, '') : (ENDING[s.code] || s.text);
  const head = { stopped: '⏹ متوقف شد', cancelled: '✖ لغو شد', expired: '⌛ بدون جواب ماند' }[s.code] || (good ? '✅ تمام شد' : bad ? '⚠️ تمام نشد' : 'ℹ️ پایان');
  put(box, h('div', 'h', head), h('div', 'txt', text));
  if (s.states && s.states.length) {
    const row = h('div', 'states');
    for (const t of s.states) row.append(put(h('span', 'sdot ' + t.state), h('i'), nm(t.name), ' ', STATE_FA[t.state] || t.state));
    box.append(row);
  }
  return box;
}

function stepNode(s, readonly, rid) {
  switch (s.k) {
    case 'say': return h('div', 'say', s.text);
    case 'ask': return h('div', 'say', '❓ ' + s.text);
    case 'note': return h('div', 'note', s.text);
    case 'tool': return toolNode(s);
    case 'proposal': return proposalNode(s, readonly, rid);
    case 'final': return finalNode(s);
    default: return null;
  }
}
const sig = s => JSON.stringify([s.k, s.status, s.state, s.summary, s.code, (s.lines || []).length, s.ok, (s.states || []).length, s.result ? s.result.length : 0, s.text]);

// ------------------------------------------------------------------ the feed
function threadFor(run, host, readonly) {
  let th = !readonly && threads.get(run.id);
  if (th) return th;
  const el = h('div', 'thread');
  const me = h('div', 'me', run.order);
  me.append(h('small', '', fmtTime(run.ts)));
  const bot = h('div', 'bot');
  el.append(me, bot);
  host.append(el);
  th = { id: run.id, el, bot, known: [], nodes: [], sigs: [], run, readonly: !!readonly, work: null };
  if (!readonly) threads.set(run.id, th);
  return th;
}

function drawSteps(th) {
  const stick = th.bot.closest('.feed') ? nearBottom() : false;
  th.known.forEach((s, i) => {
    if (!s) return;
    const sg = sig(s);
    if (th.sigs[i] === sg && th.nodes[i]) return;
    const keep = th.nodes[i] ? Array.from(th.nodes[i].querySelectorAll('details')).map(d => d.open) : [];
    const node = stepNode(s, th.readonly, th.id);
    if (!node) return;
    node.querySelectorAll('details').forEach((d, j) => { if (keep[j]) d.open = true; });
    if (th.nodes[i]) th.nodes[i].replaceWith(node);
    else {
      let next = null;
      for (let j = i + 1; j < th.nodes.length && !next; j++) next = th.nodes[j];
      th.bot.insertBefore(node, next || th.work);
    }
    th.nodes[i] = node; th.sigs[i] = sg;
  });
  const live = th.run.status === 'running' || th.run.status === 'waiting';
  if (th.work) th.work.remove();
  th.work = null;
  if (live) {
    th.work = h('div', 'working');
    put(th.work, h('i', 'spin sm'), th.run.stopping ? ' در حال توقف…' : th.run.status === 'waiting' ? ' منتظر جواب تو…' : ' دستیار دارد کار می‌کند…');
    th.bot.append(th.work);
  }
  if (stick) toBottom();
}
function nearBottom() { const f = $('#feed'); return f.scrollHeight - f.scrollTop - f.clientHeight < 140; }
function toBottom() { const f = $('#feed'); f.scrollTop = f.scrollHeight; }

function merge(th, run) {
  th.run = { ...th.run, ...run };
  for (const s of run.steps || []) th.known[s.i] = s;
  drawSteps(th);
}
function volatileFrom(th) {
  for (let i = 0; i < th.known.length; i++) {
    const s = th.known[i];
    if (!s || s.status === 'running' || s.state === 'pending') return i;
  }
  return th.known.length;
}

async function follow(rid) {
  if (following === rid) return;
  following = rid;
  let fails = 0;
  try {
    for (;;) {
      const th = threads.get(rid);
      let r;
      try { r = await api('GET', API + '/runs/' + rid + '?after=' + (th ? volatileFrom(th) : 0)); fails = 0; }
      catch (e) {
        if (e.status === 401) { signedOut(); return; }
        if (++fails > 8) { flash('ارتباط با پنل قطع شد. صفحه را دوباره باز کن.'); return; }
        await sleep(2500); continue;
      }
      const t2 = threads.get(rid);
      if (!t2) return;
      merge(t2, r);
      setBusy(r);
      if (r.status !== 'running' && r.status !== 'waiting') break;
      await nap(r.status === 'waiting' ? 3500 : 1500);
      while (document.hidden) await sleep(1000);
    }
  } finally {
    if (following === rid) following = 0;
    try { await refresh(); } catch (e) { /* the next look will say */ }
  }
}

async function decide(rid, pid, what, box) {
  box.querySelectorAll('button').forEach(b => { b.disabled = true; });
  try { await api('POST', API + '/runs/' + rid + '/' + what + '/' + pid); }
  catch (e) { flash(say(e)); box.querySelectorAll('button').forEach(b => { b.disabled = false; }); }
  follow(rid); if (poke) poke();
}

async function loadFeed() {
  $('#feed').textContent = '';
  threads.clear();
  const { runs } = await api('GET', API + '/runs?limit=6');
  const list = runs.slice().reverse();
  $('#hello').classList.toggle('hidden', list.length > 0);
  for (const r of list) {
    if (r.status === 'running' || r.status === 'waiting') {
      const full = await api('GET', API + '/runs/' + r.id);
      const th = threadFor(full, $('#feed'));
      merge(th, full); setBusy(full); follow(r.id);
    } else {
      const th = threadFor(r, $('#feed'));
      const e = r.end || {};
      th.bot.append(finalNode({ text: e.text || '', ok: e.ok, code: e.code || 'answer', states: [] }));
      const more = h('button', 'btn link', 'ریز کارها و تاریخچه‌ی این دستور'); more.type = 'button'; more.onclick = () => openRun(r.id);
      th.bot.append(more);
      th.run = r;
    }
  }
  toBottom();
}

async function send(text) {
  text = text.trim();
  if (!text) return;
  $('#btn-send').disabled = true;
  try {
    const { id } = await api('POST', API + '/runs', { text });
    $('#order').value = ''; autosize();
    $('#hello').classList.add('hidden');
    const run = { id, order: text, ts: Math.floor(Date.now() / 1000), status: 'running', who: S ? S.who : '', steps: [] };
    const th = threadFor(run, $('#feed'));
    merge(th, run); toBottom(); setBusy(run); follow(id);
  } catch (e) { flash(say(e)); $('#btn-send').disabled = false; }
}

async function stopNow() {
  const id = following || (S && S.active && S.active.id);
  if (!id) return;
  $('#btn-stop').textContent = 'در حال توقف…';
  try { await api('POST', API + '/runs/' + id + '/stop'); follow(id); if (poke) poke(); } catch (e) { flash(say(e)); }
}

// ------------------------------------------------------------------ the sheet: settings, phones, history
function sheet(title, build) {
  $('#sheet-title').textContent = title;
  const body = $('#sheet-body'); body.textContent = '';
  build(body);
  $('#sheet').classList.remove('hidden');
}
const closeSheet = () => $('#sheet').classList.add('hidden');

function switchRow(label, hint, checked, on) {
  const row = h('label', 'sw'), box = h('div');
  put(box, h('div', '', label), h('small', '', hint));
  const inp = h('input'); inp.type = 'checkbox'; inp.checked = checked;
  inp.onchange = () => on(inp.checked);
  row.append(box, inp);
  return row;
}
function list(title, items) {
  const s = h('div', 'sec'); s.append(h('h4', '', title));
  const ul = h('ul'); for (const i of items) ul.append(h('li', '', i)); s.append(ul);
  return s;
}

async function openMenu() {
  try { await refresh(); } catch (e) { flash(say(e)); return; }
  sheet('تنظیمات و تاریخچه', body => {
    body.append(switchRow('دستیار روشن باشد', 'خاموش = هیچ دستوری قبول نمی‌کند', S.enabled, v => setConf({ enabled: v }).then(openMenu)));
    body.append(switchRow('محتاط: هر تغییر با تایید من', 'حتی تعمیر ساده‌ی تونل قطع هم اول از تو می‌پرسد', S.careful, v => setConf({ careful: v }).then(openMenu)));
    const sp = h('div', 'sec'); sp.style.marginTop = '14px';
    body.append(sp,
      list('خودش، بدون پرسیدن، انجام می‌دهد', ['نگاه کردن: وضعیت، عیب‌یابی، سابقه‌ی تعمیرها', 'تعمیر تونلی که همین الان قطع یا ناپایدار است: ری‌استارت، ساخت دوباره، لینک تازه، نوع دیگر (همان کار عادی دکتر، با همان محافظ‌ها)']),
      list('فقط با دکمه‌ی «تایید» تو', ['جابجایی تونل به سرور ایران دیگر (DNS عوض می‌شود)', 'عوض کردن نوع تونل', 'تعمیر همه‌ی تونل‌ها با هم', 'هر کاری روی تونلی که الان سالم است', 'در حالت «محتاط» همه‌ی تغییرها']),
      list('از این صفحه هیچ‌وقت نمی‌شود (حتی با تایید)', ['حذف تونل یا سرور', 'پورت‌ها و رکوردهای DNS دستی', 'کاربران و پول', 'رمزها، کلیدها و تنظیمات', 'دستور مستقیم روی سرور و آپدیت پنل']),
      list('حدها', [fa(S.limits.changes_per_order) + ' تغییر در هر دستور، ' + fa(S.limits.changes_per_hour) + ' تغییر در ساعت', 'کارت تایید ' + fa(S.limits.card_minutes) + ' دقیقه اعتبار دارد و فقط یک بار کار می‌کند', 'رمز و کلید هیچ‌جا نمایش داده یا ذخیره نمی‌شود؛ IP برای AI مخفی می‌شود']));
    const ph = h('div', 'sec'); ph.append(h('h4', '', 'گوشی‌هایی که کلید دارند'));
    for (const p of S.phones) {
      const row = h('div', 'phone'), info = h('div');
      put(info, h('div', '', p.label + (p.id === S.this_phone ? ' (همین گوشی)' : '')), h('small', '', 'آخرین استفاده: ' + ago(p.seen)));
      const del = h('button', 'btn warn', 'حذف'); del.type = 'button';
      del.onclick = async () => { try { await api('DELETE', API + '/phones/' + p.id); if (p.id === S.this_phone) return signedOut(); openMenu(); } catch (e) { flash(say(e)); } };
      row.append(info, del); ph.append(row);
    }
    if (!S.phones.length) ph.append(h('p', 'note', 'گوشی دیگری کلید ندارد.'));
    body.append(ph);
    const hist = h('button', 'btn big', 'تاریخچه‌ی همه‌ی دستورها'); hist.type = 'button'; hist.onclick = openHistory; hist.style.marginBottom = '10px';
    const all = h('button', 'btn big warn', 'حذف کلید همه‌ی گوشی‌ها'); all.type = 'button';
    all.onclick = async () => { if (!confirm('کلید همه‌ی گوشی‌ها حذف شود؟ باید دوباره وارد شوی.')) return; try { await api('POST', API + '/phones/revoke-all'); signedOut(); } catch (e) { flash(say(e)); } };
    body.append(hist, all);
    if (S.this_phone) {
      const out = h('button', 'btn big', 'خروج از این گوشی'); out.type = 'button'; out.style.marginTop = '10px';
      out.onclick = async () => { try { await api('POST', API + '/logout'); } catch (e) { /* signed out anyway */ } signedOut(); };
      body.append(out);
    }
  });
}

async function openHistory() {
  const { runs } = await api('GET', API + '/runs?limit=50');
  sheet('تاریخچه‌ی دستورها', body => {
    if (!runs.length) body.append(h('p', 'note', 'هنوز دستوری نداده‌ای.'));
    for (const r of runs) {
      const b = h('button', 'run'); b.type = 'button';
      const e = r.end || {};
      put(b, h('div', 'o', r.order), h('small', '', fmtTime(r.ts) + ' · ' + (r.who === 'admin' ? 'پنل' : (r.who || '').replace('phone:', '')) + ' · ' + (r.status === 'done' ? (e.ok === true ? 'موفق' : e.ok === false ? 'تمام نشد' : 'پایان') : r.status === 'stopped' ? 'متوقف' : r.status === 'failed' ? 'ناموفق' : 'در کار')));
      b.onclick = () => openRun(r.id);
      body.append(b);
    }
  });
}

async function openRun(id) {
  let run;
  try { run = await api('GET', API + '/runs/' + id); } catch (e) { flash(say(e)); return; }
  sheet('ریز کارهای دستور', body => {
    const box = h('div', 'thread');
    box.append(h('div', 'me', run.order), put(h('div', 'note'), fmtTime(run.ts) + ' · ' + (run.who || '') + (run.ip ? ' · ' + run.ip : '') + ' · ' + fa(run.ai_calls) + ' بار پرسیدن از AI'));
    const bot = h('div', 'bot');
    for (const s of run.steps) { const n = stepNode(s, true, run.id); if (n) bot.append(n); }
    box.append(bot); body.append(box);
  });
}

// ------------------------------------------------------------------ sign-in
let ticket = '';
function signedOut() {
  following = 0; threads.clear(); S = null; closeSheet();
  ticket = ''; $('#step-pass').classList.remove('hidden'); $('#step-code').classList.add('hidden');
  $('#login-form').elements.password.value = ''; $('#login-err').textContent = '';
  view('login');
}
async function onLogin(ev) {
  ev.preventDefault();
  const f = ev.target.elements, err = $('#login-err');
  err.textContent = '';
  const btns = ev.target.querySelectorAll('button[type=submit]'); btns.forEach(b => { b.disabled = true; });
  try {
    const r = ticket ? await api('POST', '/api/auth/phone/login/2fa', { ticket, code: f.code.value.trim() }) : await api('POST', '/api/auth/phone/login', { user: f.user.value.trim(), password: f.password.value });
    if (r.need_2fa) { ticket = r.ticket; $('#step-pass').classList.add('hidden'); $('#step-code').classList.remove('hidden'); f.code.value = ''; f.code.focus(); return; }
    ticket = ''; f.password.value = ''; f.code.value = '';
    await start();
  } catch (e) {
    if (e.status === 401 && ticket && /expired/.test(e.message)) { ticket = ''; $('#step-pass').classList.remove('hidden'); $('#step-code').classList.add('hidden'); err.textContent = 'مهلت مرحله‌ی کد تمام شد؛ دوباره رمز را بزن.'; }
    else err.textContent = e.status === 401 ? (ticket ? 'کد درست نیست.' : 'نام کاربری یا رمز درست نیست.') : say(e);
  } finally { btns.forEach(b => { b.disabled = false; }); }
}

// ------------------------------------------------------------------ the composer
function autosize() { const t = $('#order'); t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, 140) + 'px'; }
function chips() {
  const c = $('#chips');
  for (const [label, text] of [['وضعیت همه‌ی تونل‌ها', 'وضعیت همه‌ی تونل‌ها را بگو'], ['کدام تونل قطع است؟', 'کدام تونل‌ها قطع یا ناپایدارند؟ علتش را هم بگو'], ['تونل قطع را درست کن', 'هر تونلی که قطع است، علتش را پیدا کن و درست کن']]) {
    const b = h('button', '', label); b.type = 'button'; b.onclick = () => send(text); c.append(b);
  }
}

async function start() {
  view('boot');
  try {
    await refresh();
    view('main');
    $('#order').disabled = true; $('#btn-send').disabled = true;          // (a word sent before the feed is drawn would be wiped by it)
    try { await loadFeed(); } finally { $('#order').disabled = false; setBusy(S && S.active); }
  } catch (e) {
    if (e.status === 401) return signedOut();
    view('main'); banner('پنل جواب نمی‌دهد: ' + say(e), 'warn', { text: 'دوباره', run: start });
  }
}

document.addEventListener('DOMContentLoaded', () => {
  $('#login-form').addEventListener('submit', onLogin);
  $('#code-back').onclick = () => { ticket = ''; $('#step-pass').classList.remove('hidden'); $('#step-code').classList.add('hidden'); };
  $('#composer').addEventListener('submit', ev => { ev.preventDefault(); send($('#order').value); });
  $('#order').addEventListener('input', autosize);
  $('#order').addEventListener('keydown', ev => { if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); send($('#order').value); } });
  $('#btn-stop').onclick = stopNow;
  $('#btn-menu').onclick = openMenu;
  $('#sheet-x').onclick = closeSheet;
  $('#sheet-bg').onclick = closeSheet;
  document.addEventListener('visibilitychange', () => { if (!document.hidden && S && !following) { refresh().then(() => { if (S && S.active) loadFeed(); }).catch(() => {}); } });
  if (window.visualViewport) {
    const fit = () => { $('#app').style.height = window.visualViewport.height + 'px'; window.scrollTo(0, 0); };
    window.visualViewport.addEventListener('resize', fit); fit();
  }
  chips();
  start();
});
})();
