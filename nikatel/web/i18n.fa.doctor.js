/* Persian texts of the Tunnel Doctor stories, tunnel tests, network watch events and installs.
   Same rules as i18n.js: {} stands for a part that changes; the parts are translated by themselves. */
I18N.add({
/* ---- repair results and network watch (events) */
"Smart repair of {} ({} ⇄ {}) succeeded.": "دکتر تانل: تعمیر {} ({} ⇄ {}) موفق بود.", "Smart repair of {} ({} ⇄ {}) did not heal it.": "دکتر تانل: تعمیر {} ({} ⇄ {}) آن را درمان نکرد.",
"Iran server {}: its network is disturbed (internet {}%, other Iran servers {}%, foreign servers {}% lost).": "سرور ایران {}: شبکه‌اش مختل است (اینترنت {}٪، سایر سرورهای ایران {}٪، سرورهای خارج {}٪ بسته از دست رفت).",
"Iran server {}: its network is weak again.": "سرور ایران {}: شبکه‌اش دوباره ضعیف شد.", "Iran server {}: its network is fine again.": "سرور ایران {}: شبکه‌اش دوباره سالم شد.",
"Foreign server {}: the route from Iran is bad - {} of {} Iran servers lose {} % or more of their packets toward it (worst {} %). The tunnels on it suffer; the fault is this server or its provider.": "سرور خارج {}: مسیر از ایران بد است - {} از {} سرور ایران {}٪ یا بیشتر از بسته‌هایشان را به آن گم می‌کنند (بدترین {}٪). تانل‌های روی آن آسیب می‌بینند؛ ایراد از همین سرور یا ارائه‌دهنده‌ی آن است.",
"Foreign server {}: the route from Iran is fine again.": "سرور خارج {}: مسیر از ایران دوباره سالم شد.",
"Installed on {}: torrent block {}, Marzban node {}": "روی {} نصب شد: بلاک تورنت {}، نود مرزبان {}", "Installed on {}: torrent block {}": "روی {} نصب شد: بلاک تورنت {}", "Installed on {}: Marzban node {}": "روی {} نصب شد: نود مرزبان {}",
"Panel backup {} made ({} KB), sent to Telegram.": "پشتیبان پنل {} ساخته شد ({} کیلوبایت) و به تلگرام ارسال شد.", "Shop bot backup {} made ({} KB), sent to Telegram.": "پشتیبان ربات فروش {} ساخته شد ({} کیلوبایت) و به تلگرام ارسال شد.",
"Panel backup {} made ({} KB).": "پشتیبان پنل {} ساخته شد ({} کیلوبایت).", "Shop bot backup {} made ({} KB).": "پشتیبان ربات فروش {} ساخته شد ({} کیلوبایت).",

/* ---- Tunnel Doctor: the checks */
"{}: the tunnel works.": "{}: تانل کار می‌کند.", "{}: works again.": "{}: دوباره کار می‌کند.", "{}: still not up. A human should look at it.": "{}: هنوز بالا نیامده. یک انسان باید نگاهش کند.",
"Iran server answers over SSH": "سرور ایران روی SSH پاسخ می‌دهد", "Foreign server answers over SSH": "سرور خارج روی SSH پاسخ می‌دهد", "Tunnel services are active on both sides": "سرویس‌های تانل در هر دو طرف فعالند",
"Last 10 checks: {} passed, {} failed": "۱۰ بررسی آخر: {} موفق، {} ناموفق", "Last 10 checks: {} passed, {} failed - the link flaps": "۱۰ بررسی آخر: {} موفق، {} ناموفق - لینک نوسان دارد",
"From {}: port {} answers (HTTP {}, {} ms)": "از {}: پورت {} پاسخ می‌دهد (HTTP {}، {} میلی‌ثانیه)", "On the Iran server: port {} answers (HTTP {})": "روی سرور ایران: پورت {} پاسخ می‌دهد (HTTP {})",
"On the Iran server: port {} does not answer through the tunnel": "روی سرور ایران: پورت {} از مسیر تانل پاسخ نمی‌دهد", "From {}: port {} does not answer - the users' path is blocked": "از {}: پورت {} پاسخ نمی‌دهد - مسیر کاربران بسته است",
"this pair, Iran → foreign: {}": "همین جفت، ایران ← خارج: {}", "this pair, foreign → Iran: {}": "همین جفت، خارج ← ایران: {}", "Iran server → other foreign server: {}": "سرور ایران ← سرورهای خارج دیگر: {}", "other Iran server → this foreign server: {}": "سرورهای ایران دیگر ← این سرور خارج: {}",
"Iran server → internet: {}": "سرور ایران ← اینترنت: {}", "foreign server → internet: {}": "سرور خارج ← اینترنت: {}", "Iran server → other Iran server: {}": "سرور ایران ← سرورهای ایران دیگر: {}",
"Where the loss is: {}": "محل اتلاف بسته: {}",
"the loss is only on the route between this pair: other servers reach each of them fine, so it is the peering between their networks": "اتلاف فقط روی مسیر بین همین دو سرور است: سرورهای دیگر هر کدام را سالم می‌بینند، پس مشکل از اتصال (peering) بین شبکه‌های آن‌هاست",
"no packet loss found between the servers or toward other servers": "هیچ اتلاف بسته‌ای بین سرورها یا به سمت سرورهای دیگر پیدا نشد",
"the loss is on the IRAN side: this Iran server loses packets toward several foreign servers, not only toward this foreign server (its own network or upstream)": "اتلاف سمت ایران است: این سرور ایران به چند سرور خارج بسته گم می‌کند، نه فقط به این سرور خارج (شبکه‌ی خودش یا بالادستی)",
"the loss is on the IRAN side: this Iran server loses packets toward the internet, not only toward this foreign server (its own network or upstream)": "اتلاف سمت ایران است: این سرور ایران به اینترنت هم بسته گم می‌کند، نه فقط به این سرور خارج (شبکه‌ی خودش یا بالادستی)",
"the loss is on the FOREIGN side: other Iran servers lose packets toward this foreign server too (the foreign server or its provider)": "اتلاف سمت خارج است: سرورهای ایران دیگر هم به این سرور خارج بسته گم می‌کنند (خود سرور خارج یا ارائه‌دهنده‌ی آن)",
"loss on both ends: this Iran server loses toward other servers and the foreign server loses toward other Iran servers": "اتلاف در هر دو سر است: این سرور ایران به سرورهای دیگر و سرور خارج به سرورهای ایران دیگر بسته گم می‌کند",
"{}: the services run and the servers answer, but no data passes: no reason found.": "{}: سرویس‌ها کار می‌کنند و سرورها پاسخ می‌دهند، اما داده‌ای عبور نمی‌کند: دلیلی پیدا نشد.",
"{}: a server does not answer over SSH: restarting or rebuilding cannot help until it is back.": "{}: یک سرور روی SSH پاسخ نمی‌دهد: تا وقتی برنگردد، ری‌استارت یا ساخت دوباره کمکی نمی‌کند.",
"Iran server: timeout after {}": "سرور ایران: مهلت {} تمام شد", "Foreign server: timeout after {}": "سرور خارج: مهلت {} تمام شد", "GRE link: no answer": "لینک GRE: پاسخی نیست",
"Still running {} seconds later: stable": "{} ثانیه بعد هم کار می‌کند: پایدار", "Still running {} seconds later: {}": "{} ثانیه بعد هم کار می‌کند: {}",
"{}: the link flaps: {} of the last 10 checks failed - the route is unstable, a restart does not cure that.": "{}: لینک نوسان دارد: {} از ۱۰ بررسی آخر ناموفق بود - مسیر ناپایدار است و ری‌استارت آن را درمان نمی‌کند.",
"{}: the link flaps: {} of the last 10 checks failed - the route is unstable, a restart does not cure that. Where: {}.": "{}: لینک نوسان دارد: {} از ۱۰ بررسی آخر ناموفق بود - مسیر ناپایدار است و ری‌استارت آن را درمان نمی‌کند. محل مشکل: {}.",
"{}: plain ping between the servers passes both ways ({} / {} loss) but GRE packets do not (inner link {} loss): GRE (protocol 47) is filtered on this route.": "{}: پینگ ساده بین دو سرور در هر دو جهت رد می‌شود (اتلاف {} / {}) ولی بسته‌های GRE نه (اتلاف لینک داخلی {}): GRE (پروتکل ۴۷) روی این مسیر فیلتر است.",
"{}: port {} answers on the Iran server but not from another Iran server: it is blocked in front of the server, only another Iran server helps.": "{}: پورت {} روی خود سرور ایران پاسخ می‌دهد ولی از یک سرور ایران دیگر نه: جلوی سرور بسته شده است و فقط یک سرور ایران دیگر کمک می‌کند.",
"{}: tunnel services are not running: {} ({}).": "{}: سرویس‌های تانل اجرا نیستند: {} ({}).", "Not active: {} ({})": "فعال نیست: {} ({})",
"The loss is not on this pair's route but on one server's own network (see above): another tunnel type or a new link would suffer the same, so nothing is built or swapped. The tunnel stays and is watched; the repair looks again later.": "اتلاف روی مسیر این دو سرور نیست و از شبکه‌ی خود یک سرور است (بالا را ببینید): نوع دیگر تانل یا لینک تازه هم همین را می‌بیند، پس چیزی ساخته یا عوض نمی‌شود. تانل می‌ماند و زیر نظر است؛ تعمیر بعداً دوباره نگاه می‌کند.",
"Found: {} (state {})": "یافته: {} (وضعیت {})", "everything looks fine on both servers": "همه‌چیز روی هر دو سرور سالم به نظر می‌رسد", "no answer from the foreign end over the GRE link": "از سمت خارج روی لینک GRE پاسخی نیست",
"foreign: {} is inactive": "خارج: {} غیرفعال است", "iran: {} is inactive": "ایران: {} غیرفعال است", "foreign: {} is deactivating": "خارج: {} در حال توقف است", "Iran: {} is deactivating": "ایران: {} در حال توقف است", "foreign: {} is activating": "خارج: {} در حال راه‌اندازی است", "Iran: {} is activating": "ایران: {} در حال راه‌اندازی است", "foreign: {} is failed": "خارج: {} از کار افتاده است", "Iran: {} is failed": "ایران: {} از کار افتاده است", "Iran: {} is inactive": "ایران: {} غیرفعال است", "Iran: {} is active": "ایران: {} فعال است", "foreign: {} is active": "خارج: {} فعال است",
"{}: restarted {} because {} → {}": "{}: {} ری‌استارت شد چون {} → {}", "{}: restart {}": "{}: ری‌استارت {}",
"the GRE link does not pass traffic": "لینک GRE ترافیک عبور نمی‌دهد", "restart the tunnel service on the Iran side": "ری‌استارت سرویس تانل سمت ایران", "restart the tunnel service on the foreign side": "ری‌استارت سرویس تانل سمت خارج",
"{} not active on the foreign server": "{} روی سرور خارج فعال نیست", "{} not active on the Iran server": "{} روی سرور ایران فعال نیست",
"iran": "ایران", "foreign": "خارج",
"The build did not succeed.": "ساخت موفق نبود.", "Built; measuring…": "ساخته شد؛ در حال اندازه‌گیری…",
"Moving the tunnel to another Iran server is switched off for now: the tunnel stays where it is.": "جابه‌جایی تانل به سرور ایران دیگر فعلاً خاموش است: تانل همان‌جا که هست می‌ماند.",
"Testing the foreign server from another Iran server ({}): a temporary tunnel is built, measured and removed (it is not shown in the cards)": "تست سرور خارج از یک سرور ایران دیگر ({}): یک تانل موقت ساخته، اندازه‌گیری و حذف می‌شود (در کارت‌ها نشان داده نمی‌شود).",
"The test from {} gave no result: nothing can be said about {} from there.": "تست از {} نتیجه‌ای نداد: از آنجا نمی‌شود درباره‌ی {} چیزی گفت.",
"Stopped: another tunnel test is still running": "متوقف شد: تست تانل دیگری هنوز در حال اجراست",
"Choosing the Iran server: pinging {} from every Iran server, looking at load, tunnels and free ports…": "انتخاب سرور ایران: پینگ {} از همه‌ی سرورهای ایران، بررسی بار، تانل‌ها و پورت‌های آزاد…",
"Smart choice: {} ({} ms, {}% loss, {} tunnels, cpu {}%, score {})": "انتخاب هوشمند: {} ({} میلی‌ثانیه، اتلاف {}٪، {} تانل، پردازنده {}٪، امتیاز {})",
"Building {} ⇄ {}, ports {}": "ساخت {} ⇄ {}، پورت‌ها {}", "Trying {} ⇄ {}, ports {}": "امتحان {} ⇄ {}، پورت‌ها {}", "{}: skipped - a tunnel between these two servers is already being built": "{}: رد شد - تانلی بین این دو سرور هم‌اکنون در حال ساخت است",
"Quality: {} ms, jitter {} ms, loss {}% → score {}": "کیفیت: {} میلی‌ثانیه، جیتر {} میلی‌ثانیه، اتلاف {}٪ → امتیاز {}",
"Chisel · reverse tunnel over TLS WebSocket": "چیزل · تانل معکوس روی TLS WebSocket",
"BackPack · Direct · {}: the Iran server dials the foreign server": "بک‌پک · مستقیم · {}: سرور ایران به سرور خارج وصل می‌شود",
"Leftovers of an interrupted earlier run removed first on the Iran and foreign side": "بقایای یک اجرای قبلی نیمه‌کاره ابتدا در سمت ایران و خارج پاک شد",

/* ---- re-tuning */
"Re-tuning {} tunnel(s), one at a time, at quiet moments; each is checked and rolled back if it gets worse.": "تنظیم دوباره‌ی {} تانل، یکی‌یکی و در لحظه‌های کم‌ترافیک؛ هر کدام بررسی می‌شود و اگر بدتر شد برمی‌گردد.",
"{}: applying (connections {}, stream buffer {} KB)…": "{}: در حال اعمال (اتصال {}، بافر stream {} کیلوبایت)…", "{}: kept - ping {}→{} ms, loss {}→{} %": "{}: نگه داشته شد - پینگ {}←{} میلی‌ثانیه، اتلاف {}←{}٪",
"{}: worse or not up ({}; ping {}→{} ms, loss {}→{} %): putting the original config back": "{}: بدتر شد یا بالا نیامد ({}؛ پینگ {}←{} میلی‌ثانیه، اتلاف {}←{}٪): تنظیم اصلی برمی‌گردد",
"{}: original config back, tunnel is up": "{}: تنظیم اصلی برگشت و تانل بالاست", "{}: skipped - it stays busy; tried again at the next run": "{}: رد شد - مشغول می‌ماند؛ در اجرای بعدی دوباره امتحان می‌شود",
"{}: nothing to change (already tuned, or not a multiplexed tunnel)": "{}: چیزی برای تغییر نیست (قبلاً تنظیم شده یا تانل مولتی‌پلکس نیست)",
"Waiting for a quiet moment to swap it: {} Mb/s flow now (under 1.5 Mb/s is quiet), so customers are not cut.": "منتظر لحظه‌ی کم‌ترافیک برای تعویض: اکنون {} مگابیت بر ثانیه جریان دارد (زیر ۱٫۵ آرام است) تا مشتری‌ها قطع نشوند.",
"Finished: {} tuned, {} rolled back, {} skipped, {} already fine.": "پایان: {} تنظیم شد، {} برگشت، {} رد شد، {} از قبل سالم بود.",

/* ---- installs on a server */
"Installing: {} (they keep running on the server even if the panel restarts)": "در حال نصب: {} (حتی اگر پنل ری‌استارت شود، روی سرور ادامه می‌یابد)", "torrent block, Marzban node": "بلاک تورنت و نود مرزبان", "torrent block": "بلاک تورنت", "Marzban node": "نود مرزبان",
"torrent block: starting (a tracker list and thousands of rules; a few minutes)": "بلاک تورنت: شروع (فهرست ترکرها و هزاران قانون؛ چند دقیقه)", "Marzban node: starting (Docker, the node image and its service; a few minutes)": "نود مرزبان: شروع (داکر، ایمیج نود و سرویس آن؛ چند دقیقه)",
"The panel started again: following the install…": "پنل دوباره بالا آمد: نصب دنبال می‌شود…", "torrent block: failed - the server stopped answering: {}": "بلاک تورنت: ناموفق - سرور دیگر پاسخ نداد: {}",
"Marzban node: still working… ({})": "نود مرزبان: هنوز در حال انجام است… ({})", "torrent block: still working… ({})": "بلاک تورنت: هنوز در حال انجام است… ({})",
"torrent block: the panel was restarted; the install kept running, following it again…": "بلاک تورنت: پنل ری‌استارت شد؛ نصب ادامه داشت و دوباره دنبال می‌شود…",
"Torrent block: installed (drop rules={}); cron refreshes the rules every day.": "بلاک تورنت: نصب شد (قانون‌های drop={})؛ cron هر روز قانون‌ها را تازه می‌کند.",
"The panel was restarted in the middle of this install (before installs were made restart-proof); look at the server or install again.": "پنل وسط این نصب ری‌استارت شد (پیش از آنکه نصب‌ها در برابر ری‌استارت مقاوم شوند)؛ سرور را ببینید یا دوباره نصب کنید.",
"Pull: {}": "دریافت: {}",
});

/* the steps of a plan and the order of Iran servers are lists inside one text */
I18N.explain((c) => {
  let m = /^Plan: (.+ → .+|.+: restart .+)$/s.exec(c);
  if (m && / → |: restart /.test(m[1])) return "برنامه: " + m[1].split(" → ").map(s => I18N.tr(s)).join(" ← ");
  m = /^Order \(quality first, then free resources\): (.+)$/s.exec(c);
  if (m) {
    const items = m[1].split(/\),\s*/).map(x => x.endsWith(")") ? x : x + ")");
    return "ترتیب (اول کیفیت، بعد منابع آزاد): " + items.map(x => x.replace(/^(\S+) \(([\d.]+) ms, ([\d.]+)% loss, (\d+) tunnels?, cpu ([\d.]+)%\)$/, (_, n, ms, loss, t, cpu) => `${n} (${ms} میلی‌ثانیه، اتلاف ${loss}٪، ${t} تانل، پردازنده ${cpu}٪)`)).join("، ");
  }
  return null;
});

/* ---- errors and notes the panel's own code raises */
I18N.add({
"the foreign server does not answer ping from the Iran server (ICMP is filtered); this tunnel has no GRE link to measure, its data path is checked by the health check instead": "سرور خارج به پینگ سرور ایران جواب نمی‌دهد (ICMP فیلتر است)؛ این تانل لینک GRE برای اندازه‌گیری ندارد و مسیر داده‌اش با بررسی سلامت سنجیده می‌شود",
"this tunnel has no GRE link or test door to measure": "این تانل لینک GRE یا درگاه تستی برای اندازه‌گیری ندارد", "the panel is read-only": "پنل در حالت فقط‌خواندنی است", "bad username": "نام کاربری نامعتبر است", "bad name": "نام نامعتبر است", "bad app": "برنامه نامعتبر است", "bad field name": "نام فیلد نامعتبر است",
"test config made by the Nikatel panel; it is kept alive and measured on the probe": "کانفیگ آزمایشی ساخته‌شده توسط پنل نیکاتل؛ روی پروب زنده نگه داشته و اندازه‌گیری می‌شود",
"Panel {}: its subscription is already in Configs as group {} ({} configs); nothing added.": "پنل {}: اشتراکش از قبل در کانفیگ‌ها با نام گروه {} هست ({} کانفیگ)؛ چیزی اضافه نشد.",
"the port must be 1-65535 (empty = the port of the inbound)": "پورت باید بین ۱ تا ۶۵۵۳۵ باشد (خالی = پورت اینباند)", "security must be inbound_default, none or tls": "امنیت باید inbound_default، none یا tls باشد", "type a command": "یک دستور بنویسید",
"the password must be 8 to 128 characters, without control characters": "گذرواژه باید ۸ تا ۱۲۸ نویسه باشد و نویسه‌ی کنترلی نداشته باشد", "check failed: {}": "بررسی ناموفق بود: {}",
"Failover of {} ({}): {} tunnel(s) → {}, {} DNS record(s) → {}": "جابه‌جایی خودکار {} ({}): {} تانل ← {}، {} رکورد DNS ← {}",
"{} of {} tunnels moved: the DNS is NOT changed (records must not point at a server that does not carry all of their tunnels).": "{} از {} تانل جابه‌جا شد: DNS تغییر نکرد (رکورد نباید به سروری اشاره کند که همه‌ی تانل‌هایش را ندارد).",
"there is no replacement server for this plan": "برای این طرح سرور جایگزین نیست", "A failover plan for {} is already waiting for approval on Tunnels → DNS · IP switch.": "یک طرح جابه‌جایی خودکار برای {} از قبل منتظر تأیید شما در تانل‌ها ← DNS · تعویض IP است.",
"DNS ({}): {} → {}": "DNS ({}): {} ← {}", "DNS {}: {} → {}": "DNS {}: {} ← {}", "DNS {}: {} → {} ({})": "DNS {}: {} ← {} ({})", "DNS {}: not changed: {}": "DNS {}: تغییر نکرد: {}", "DNS {}: already points at {}": "DNS {}: از قبل به {} اشاره می‌کند",
"this plan is no longer pending": "این طرح دیگر در انتظار نیست", "this DNS change is not in the list any more": "این تغییر DNS دیگر در فهرست نیست", "{} already points at {} again": "{} دوباره به {} اشاره می‌کند",
"DNS {} points at {} ({}) again; the tunnels stay on {}.": "DNS {} دوباره به {} ({}) اشاره می‌کند؛ تانل‌ها روی {} می‌مانند.", "nothing to undo for this entry": "برای این مورد چیزی برای برگرداندن نیست", "choose an Iran server": "یک سرور ایران انتخاب کنید", "No failover plan: {}": "طرح جابه‌جایی خودکار نیست: {}",
"DNS: {} still point at {}; the plan to point them at {} waits for your approval on Tunnels → DNS · IP switch.": "DNS: {} هنوز به {} اشاره می‌کنند؛ طرح اشاره به {} منتظر تأیید شما در تانل‌ها ← DNS · تعویض IP است.",
"DNS: {} still point at {} (the failover is off: change them yourself, or switch it on under Tunnels → DNS · IP switch).": "DNS: {} هنوز به {} اشاره می‌کنند (جابه‌جایی خودکار خاموش است: خودتان عوض کنید یا در تانل‌ها ← DNS · تعویض IP روشنش کنید).",
"DNS: ArvanCloud did not answer: {}": "DNS: آروان‌کلود پاسخ نداد: {}", "DNS {}: not in any domain of this ArvanCloud account ({})": "DNS {}: در هیچ دامنه‌ی این حساب آروان‌کلود نیست ({})", "DNS: {} record(s) now point at {} ({}): {}.": "DNS: {} رکورد اکنون به {} ({}) اشاره می‌کنند: {}.", "{}: not moved: {}": "{}: جابه‌جا نشد: {}",
"paste a vless://, vmess://, trojan:// or ss:// link": "یک لینک vless://‎، vmess://‎، trojan:// یا ss:// بچسبانید", "the payment has no user": "این پرداخت کاربر ندارد", "kind is text or button": "نوع باید text یا button باشد", "kind is volume or days; price and amount are above zero": "نوع باید volume یا days باشد؛ قیمت و مقدار باید بیشتر از صفر باشند", "kind is volume or days": "نوع باید volume یا days باشد", "{} is not a number": "{} عدد نیست", "unknown key {}": "کلید ناشناخته {}",
"{}: original config back, tunnel is {}": "{}: تنظیم اصلی برگشت، تانل {}", "{}: skipped - its config could not be read ({})": "{}: رد شد - تنظیمش خوانده نشد ({})", "{}: the roll-back needs a human: {}": "{}: برگرداندن نیاز به دخالت انسان دارد: {}", "Stopped: two roll-backs in a row - something else is going on, a human should look.": "متوقف شد: دو برگشت پشت‌سرهم - مشکل دیگری هست، یک انسان باید نگاه کند.", "{}: error - {}": "{}: خطا - {}", "Stopped: {}": "متوقف شد: {}",
"no ArvanCloud API key saved (Settings → Repairs & AI → ArvanCloud)": "کلید API آروان‌کلود ذخیره نشده (تنظیمات ← آروان‌کلود)", "ArvanCloud answered {}: {}": "آروان‌کلود پاسخ {} داد: {}", "{}.{} does not point at {} any more - left alone": "{}.{} دیگر به {} اشاره نمی‌کند - دست‌نخورده ماند", "ArvanCloud did not keep the new address for {}.{}": "آروان‌کلود آدرس تازه را برای {}.{} نگه نداشت",
"the panel is read-only; set NIKATEL_READ_ONLY=false on the panel server to manage gateways": "پنل فقط‌خواندنی است؛ برای مدیریت گیت‌وی در سرور پنل NIKATEL_READ_ONLY=false بگذارید",
"{} Replacing {}: {} → {}": "{} جایگزینی {}: {} ← {}", "No other Iran server is free for a test from there.": "سرور ایران دیگری برای تست از آنجا آزاد نیست.", "every tunnel is up: nothing to repair": "همه‌ی تانل‌ها بالا هستند: چیزی برای تعمیر نیست",
"Waiting for a quiet moment to swap it: {} Mb/s flow now (under {} Mb/s is quiet), so customers are not cut.": "منتظر لحظه‌ی کم‌ترافیک برای تعویض: اکنون {} مگابیت بر ثانیه جریان دارد (زیر {} آرام است) تا مشتری‌ها قطع نشوند.",
"No test from another Iran server: {}": "تست از سرور ایران دیگر انجام نشد: {}", "{}: stopped: {}": "{}: متوقف شد: {}", "Another tunnel test is running (one at a time): waiting for my turn": "تست تانل دیگری در حال اجراست (یکی‌یکی): منتظر نوبت", "The test from {} could not start: {}": "تست از {} شروع نشد: {}",
"{} would be better (score {}), but the tunnel is busy: it is swapped at the next quiet moment.": "{} بهتر است (امتیاز {}) ولی تانل مشغول است: در اولین لحظه‌ی آرام عوض می‌شود.", "The current tunnel could not be measured ({}): any working candidate counts.": "تانل فعلی اندازه‌گیری نشد ({}): هر گزینه‌ی سالمی قبول است.",
"the config is not an object": "تنظیم یک شیء (object) نیست", "the panel is read-only; set NIKATEL_READ_ONLY=false to change a core config": "پنل فقط‌خواندنی است؛ برای تغییر تنظیم هسته NIKATEL_READ_ONLY=false بگذارید", "a routing rule is not an object": "یک قانون مسیریابی شیء (object) نیست", "the balancer “{}” selects “{}”, which matches no outbound": "بالانسر «{}» «{}» را انتخاب می‌کند که با هیچ اوت‌باندی نمی‌خواند",
"unreachable": "در دسترس نیست", "disconnected": "قطع شده", "{}: failed - {}": "{}: ناموفق - {}", "paste the whole certificate of the Marzban panel (from -----BEGIN CERTIFICATE----- to -----END CERTIFICATE-----)": "کل گواهی پنل مرزبان را بچسبانید (از -----BEGIN CERTIFICATE----- تا -----END CERTIFICATE-----)",
"{}: not started - {}": "{}: شروع نشد - {}", "choose something to install": "چیزی برای نصب انتخاب کنید", "{}: the panel was restarted; the install kept running, following it again": "{}: پنل ری‌استارت شد؛ نصب ادامه داشت و دوباره دنبال می‌شود", "{}: could not be started: {}": "{}: شروع نشد: {}",
"this copy is not there any more (restored already, forgotten, or older than 30 days)": "این نسخه دیگر نیست (قبلاً بازیابی شده، فراموش شده یا از ۳۰ روز قدیمی‌تر است)", "the copy has no ports": "این نسخه پورتی ندارد", "a tunnel named {} exists already": "تانلی به نام {} از قبل هست", "one of its servers is not in the panel any more or is disabled": "یکی از سرورهایش دیگر در پنل نیست یا غیرفعال است",
"stopped by you": "توسط شما متوقف شد", "backhaul install unit ended {}": "یونیت نصب بک‌هال پایان یافت: {}", "the control connection dropped again a few seconds after it was established: the tunnel is not stable": "اتصال کنترل چند ثانیه پس از برقرار شدن دوباره قطع شد: تانل پایدار نیست",
"a tunnel name is lowercase letters, digits and - (1-15 characters, starting with a letter or digit)": "نام تانل حروف کوچک، رقم و - است (۱ تا ۱۵ نویسه، شروع با حرف یا رقم)", "port '{}': use 443, 443=2096 or 10000-10009": "پورت '{}': از 443، 443=2096 یا 10000-10009 استفاده کنید",
"the backhaul service on the {} server stopped again after it had started ({}): {}": "سرویس بک‌هال روی سرور {} بعد از شروع دوباره متوقف شد ({}): {}", "another tunnel already has this name": "تانل دیگری از قبل این نام را دارد",
"Another build is using one of these servers: waiting for it to finish (builds on the same server run one after the other)": "ساخت دیگری از یکی از این سرورها استفاده می‌کند: منتظر پایان آن (ساخت‌ها روی یک سرور یکی‌یکی انجام می‌شوند)", "The build takes too long; look at its own entry in the history.": "ساخت زیادی طول کشید؛ ورودی خودش را در تاریخچه ببینید.",
"Smart test: ranking the Iran servers for {} (ping, load, tunnels, free ports)": "تست هوشمند: رتبه‌بندی سرورهای ایران برای {} (پینگ، بار، تانل‌ها، پورت‌های آزاد)", "a tunnel to replace is not in the list any more": "تانلی که باید جایگزین شود دیگر در فهرست نیست", "Testing {} ({}/{}): a temporary tunnel is built, measured and removed": "آزمایش {} ({}/{}): یک تانل موقت ساخته، اندازه‌گیری و حذف می‌شود",
"No server scored {}+; the best of those tested is {} ({}/100).": "هیچ سروری {}+ نگرفت؛ بهترین سرور آزمایش‌شده {} است ({}/100).", "Best server: {}": "بهترین سرور: {}", "No server could be tested.": "هیچ سروری آزمایش نشد.", "{} {} · {} {} ⇄ {}, ports {}": "{} {} · {} {} ⇄ {}، پورت‌ها {}",
"{} is connected to a foreign server right now: it is NOT removed (the port stays busy).": "{} هم‌اکنون به یک سرور خارج وصل است: حذف نشد (پورت مشغول می‌ماند).", "Could not remove {}: {}": "{} حذف نشد: {}", "{}: skipped - {}": "{}: رد شد - {}", "DNS of {}: no plan: {}": "DNS {}: طرحی نیست: {}", "{}: not tested - {}": "{}: آزمایش نشد - {}",
"this is not a Nikatel backup": "این پشتیبان نیکاتل نیست", "the bot database dump is missing or bigger than 30 MB": "نسخه‌ی دیتابیس ربات نیست یا از ۳۰ مگابایت بزرگ‌تر است", "unknown part": "بخش ناشناخته", "the file is too big for a backup": "فایل برای پشتیبان بیش از حد بزرگ است", "no passphrase: type the passphrase this file was made with": "گذرواژه‌ای نیست: گذرواژه‌ای را که این فایل با آن ساخته شده بنویسید",
"the panel is in read-only mode: a restore is not allowed here": "پنل در حالت فقط‌خواندنی است: بازیابی اینجا مجاز نیست", "this is not a panel database": "این دیتابیس پنل نیست", "this is not a bot database dump": "این نسخه‌ی دیتابیس ربات نیست", "{} backup not made: {}": "پشتیبان {} ساخته نشد: {}",
"could not read the network interface / gateway MAC (interface '{}', ip '{}', gateway mac '{}')": "کارت شبکه / MAC گیت‌وی خوانده نشد (کارت '{}'، آی‌پی '{}'، MAC گیت‌وی '{}')", "unknown BackPack transport": "ترنسپورت ناشناخته‌ی بک‌پک", "this tunnel has no test door": "این تانل درگاه تست ندارد", "unknown BackPack Direct carrier": "حامل ناشناخته‌ی بک‌پک مستقیم",
"mode must be off, approve or auto": "حالت باید off، approve یا auto باشد", "minutes: between 2 and 120": "دقیقه: بین ۲ و ۱۲۰", "a DNS name may only have letters, digits, dots and dashes": "نام DNS فقط حرف، رقم، نقطه و خط تیره دارد", "no saved link for this group — paste the subscription link": "برای این گروه لینکی ذخیره نشده — لینک اشتراک را بچسبانید",
"the probe address looks like http://host:port": "آدرس پروب شبیه http://host:port است", "standby {}: the address looks like http://host:port": "پروب رزرو {}: آدرس شبیه http://host:port است", "no such backup part": "چنین بخش پشتیبانی نیست", "no file received": "فایلی دریافت نشد", "the host looks like an IP or a hostname": "میزبان باید آی‌پی یا نام میزبان باشد",
"the bot directory is /var/www/<name> and the env file /opt/<name>/db.env": "مسیر ربات /var/www/<نام> و فایل env همان /opt/<نام>/db.env است", "no such job": "چنین کاری نیست",
"GRE link with local IPs, backhaul on top - like the existing tunnels": "لینک GRE با آی‌پی محلی و بک‌هال روی آن - مثل تانل‌های موجود", "the foreign server dials the Iran server; twelve transports, presets, UDP": "سرور خارج به سرور ایران وصل می‌شود؛ دوازده ترنسپورت، پیش‌تنظیم و UDP",
"an L3 link the Iran server dials: UDP / QUIC / PCK / SNI / xDi carriers": "یک لینک L3 که سرور ایران برقرار می‌کند: حامل‌های UDP / QUIC / PCK / SNI / xDi", "KCP over raw sockets: the foreign server listens, the Iran server opens the ports": "KCP روی raw socket: سرور خارج شنود می‌کند و سرور ایران پورت‌ها را باز می‌کند",
"reverse tunnel over one WebSocket port on the Iran server": "تانل معکوس روی یک پورت WebSocket در سرور ایران", "modes normal / fast / fast2 / fast3": "حالت‌های normal / fast / fast2 / fast3", "clear text": "بدون رمزنگاری", "self-signed certificate, key pinned": "گواهی خودامضا، کلید پین‌شده",
});
/* ---- the Logs page: an event line is "<domain> <text>"; the text is translated, the domain word becomes Persian */
I18N.add({
"check name={} state={} judged={} loss={} rtt={} rx={} tx={}": "بررسی تانل {}: وضعیت {}، قضاوت {}، اتلاف {}، RTT {}، دریافت {}، ارسال {}",
"Filter check of {}: Not filtered - {} of {} Iranian locations answer well.": "بررسی فیلتر {}: فیلتر نیست - {} از {} نقطه‌ی ایران خوب پاسخ می‌دهند.", "Filter check of {}: Filtered in Iran - {} of {} locations lose packets.": "بررسی فیلتر {}: در ایران فیلتر است - {} از {} نقطه بسته گم می‌کنند.",
"GRE check of {}: no GRE link on this server.": "بررسی GRE روی {}: این سرور لینک GRE ندارد.", "Test config of panel {}: {} added, {} already there.": "کانفیگ آزمایشی پنل {}: {} اضافه شد، {} از قبل بود.",
"Tunnel {} ({} ⇄ {}) built: {}, ports {}, {} ms.": "تانل {} ({} ⇄ {}) ساخته شد: {}، پورت‌ها {}، {} میلی‌ثانیه.", "Tunnel {} ({} ⇄ {}) built: ports {}, {} ms over GRE.": "تانل {} ({} ⇄ {}) ساخته شد: پورت‌ها {}، {} میلی‌ثانیه روی GRE.",
"Tunnel {} ({} ⇄ {}) is degraded: {}": "تانل {} ({} ⇄ {}) ناپایدار است: {}", "Tunnel {} ({} ⇄ {}) is down: {}": "تانل {} ({} ⇄ {}) قطع است: {}", "Tunnel {} ({} ⇄ {}) works again.": "تانل {} ({} ⇄ {}) دوباره کار می‌کند.", "Tunnel {} ({} ⇄ {}) removed: {}": "تانل {} ({} ⇄ {}) حذف شد: {}",
"port {} answers on the server but not from {}: blocked in front of the server": "پورت {} روی خود سرور پاسخ می‌دهد ولی از {} نه: جلوی سرور بسته شده است", "the link flaps: {} of the last 10 checks failed, users see cuts": "لینک نوسان دارد: {} از ۱۰ بررسی آخر ناموفق بود و کاربران قطعی می‌بینند",
"Iran: removed; foreign: removed": "ایران: حذف شد؛ خارج: حذف شد", "Incomplete tunnel ({} {}) removed from {}.": "تانل ناقص ({} {}) از {} حذف شد.",
"the tunnel started but no data passes through it (no answer). Is TCP port {} open between the servers? Log: {}": "تانل بالا آمد ولی هیچ داده‌ای از آن رد نمی‌شود (بی‌پاسخ). آیا پورت TCP {} بین دو سرور باز است؟ لاگ: {}",
});
I18N.explain((c) => {
  const DOM = {configs: "کانفیگ‌ها", security: "امنیت", servers: "سرورها", tunnels: "تانل‌ها", nodes: "سرورها", actions: "تعمیر", panels: "پنل‌ها", alerts: "هشدار", backup: "پشتیبان", failover: "جابه‌جایی خودکار", gateway: "گیت‌وی", shopbot: "ربات فروش", jobs: "کارها", netwatch: "شبکه", autopilot: "دکتر تانل", extras: "نصب", builder: "ساخت", auth: "ورود", system: "سیستم", probe: "پروب", report: "گزارش", ai: "هوش مصنوعی", retune: "تنظیم دوباره", trash: "سطل بازیافت", create: "ساخت"};
  const m = /^([a-z]+) (.+)$/s.exec(c);
  if (m && DOM[m[1]]) { const t = I18N.tr(m[2]); if (t !== m[2]) return DOM[m[1]] + " · " + t; }
  return null;
});
I18N.add({
"login ok user={}": "ورود موفق: کاربر {}", "password changed user={}": "گذرواژه تغییر کرد: کاربر {}", "Failed sign-in as {} from {}.": "ورود ناموفق به‌عنوان {} از {}.", "Signed in from {}.": "ورود از {}.",
"Started server process [{}]": "فرایند سرور شروع شد [{}]", "Finished server process [{}]": "فرایند سرور پایان یافت [{}]", "Waiting for application startup.": "منتظر راه‌اندازی برنامه…", "Application startup complete.": "راه‌اندازی برنامه کامل شد.", "Waiting for application shutdown.": "منتظر خاموش شدن برنامه…", "Application shutdown complete.": "برنامه خاموش شد.", "Waiting for connections to close. (CTRL+C to force quit)": "منتظر بسته شدن اتصال‌ها (CTRL+C برای توقف اجباری)", "Uvicorn running on {} (Press CTRL+C to quit)": "سرور روی {} اجرا می‌شود (برای خروج CTRL+C)",
"tunnel is up but the inner addresses cannot ping each other (Iran→foreign {}, foreign→Iran {}). GRE is probably filtered between these two servers; try another Iran server for this foreign server.": "تانل بالاست ولی آدرس‌های داخلی به هم پینگ نمی‌دهند (ایران←خارج {}، خارج←ایران {}). احتمالاً GRE بین این دو سرور فیلتر است؛ یک سرور ایران دیگر برای این سرور خارج امتحان کنید.",
"test is still running": "تست هنوز در حال اجراست", "external probe from {} failed: timeout after {}": "پروب خارجی از {} ناموفق بود: مهلت {} تمام شد", "path scan failed": "اسکن مسیر ناموفق بود", "could not purge test leftovers on {}: timeout after {}": "بقایای تست روی {} پاک نشد: مهلت {} تمام شد",
"network does not come up: the {} carrier cannot pass between these servers (is UDP port {} open on the foreign server?)": "شبکه بالا نمی‌آید: حامل {} بین این دو سرور رد نمی‌شود (آیا پورت UDP {} روی سرور خارج باز است؟)",
"service on the Iran server ended activating: {}": "سرویس روی سرور ایران هنگام راه‌اندازی تمام شد: {}", "{} has no python3 (needed for the test door)": "{} پایتون ۳ ندارد (برای درگاه تست لازم است)", "Everything created was removed again.": "هر چه ساخته شده بود دوباره پاک شد.",
"started but no data passes through it ({}). Is {} port {} open between the servers? Log: {}": "بالا آمد ولی هیچ داده‌ای از آن رد نمی‌شود ({}). آیا پورت {} {} بین دو سرور باز است؟ لاگ: {}",
});
I18N.add({"no answer on the SSH port {}: {} quick checks in a row got no connection": "پورت SSH {} پاسخ نمی‌دهد: {} بررسی سریع پشت‌سرهم وصل نشد", "Server {} does not answer: no answer on the SSH port {}: {} quick checks in a row got no connection": "سرور {} پاسخ نمی‌دهد: پورت SSH {} جواب نمی‌دهد ({} بررسی سریع پشت‌سرهم وصل نشد)"});

/* ---- the warm standby of a tunnel (services/standby.py) */
I18N.add({
"Standby · {}": "تانل رزرو · {}", "Building": "در حال ساخت", "Ready": "آماده", "In use": "در حال استفاده", "Being mended": "در حال ترمیم", "Waiting": "در انتظار", "Not built": "ساخته نشد", "Removing": "در حال حذف",
"A second tunnel of another type waits beside this one. If this one is cut, its users' ports are handed to it in seconds.": "یک تانل دوم از نوعی دیگر کنار این تانل آماده است. اگر این تانل قطع شود، پورت‌های کاربران در چند ثانیه به آن سپرده می‌شود.",
"{} · ports {} · idle, looked at every 15 seconds": "{} · پورت‌ها {} · بی‌کار، هر ۱۵ ثانیه بررسی می‌شود",
"The users' ports are on the standby ({}) since {} - they go back to this tunnel when it has held for a minute": "پورت‌های کاربران از ساعت {2} روی تانل رزرو ({1}) است - وقتی این تانل یک دقیقه پایدار بماند برمی‌گردند",
"Turn on": "روشن کردن", "Turn off": "خاموش کردن", "Switch now": "همین حالا جابه‌جا کن", "Back now": "همین حالا برگردان",
"Build the standby now (about a minute); nothing changes for the users": "تانل رزرو را همین حالا بساز (حدود یک دقیقه)؛ برای کاربران چیزی عوض نمی‌شود",
"Hand the users' ports to the standby now (a drill; they stay there until Back now)": "پورت‌های کاربران را همین حالا به تانل رزرو بسپار (تمرین؛ تا «همین حالا برگردان» همان‌جا می‌مانند)",
"Take the users' ports off the standby now": "پورت‌های کاربران را همین حالا از تانل رزرو بردار", "Give the ports back and remove the standby": "پورت‌ها را پس بده و تانل رزرو را حذف کن", "Remove the standby": "حذف تانل رزرو",
"The warm standby: another tunnel type between the same two servers, on shifted ports, built before a cut": "تانل رزرو آماده‌باش: نوع دیگری از تانل بین همین دو سرور، روی پورت‌های جابه‌جاشده، پیش از قطع ساخته می‌شود",
"Remove the standby of this tunnel? If the users' ports are on it they are given back to the tunnel first.": "تانل رزرو این تانل حذف شود؟ اگر پورت‌های کاربران روی آن باشد، اول به تانل پس داده می‌شود.",
"Hand the users' ports of this tunnel to its standby now? New connections go through the standby until you press Back now (a drill of what happens when the tunnel is cut).": "پورت‌های کاربران این تانل همین حالا به تانل رزرو سپرده شود؟ اتصال‌های تازه از تانل رزرو می‌گذرند تا «همین حالا برگردان» را بزنید (تمرین آنچه با قطع تانل رخ می‌دهد).",
"✓ Standby on - it is being built beside the tunnel (about a minute); nothing changes for the users": "✓ تانل رزرو روشن شد - کنار تانل ساخته می‌شود (حدود یک دقیقه)؛ برای کاربران چیزی عوض نمی‌شود", "✓ Standby off": "✓ تانل رزرو خاموش شد",
"✓ The users' ports are on the standby ({})": "✓ پورت‌های کاربران روی تانل رزرو ({}) است", "✓ The users' ports are on the tunnel again; open connections end on the standby by themselves": "✓ پورت‌های کاربران دوباره روی تانل است؛ اتصال‌های باز خودشان روی تانل رزرو تمام می‌شوند",
"✓ The users' ports are on {} again": "✓ پورت‌های کاربران دوباره روی {} است", "standby": "رزرو", "on standby": "روی رزرو",
"Take the users' ports off the standby now: they are the tunnel's again (open connections end on the standby by themselves)": "پورت‌های کاربران را همین حالا از تانل رزرو بردار: دوباره مال تانل می‌شوند (اتصال‌های باز خودشان روی تانل رزرو تمام می‌شوند)",
"Standby of {} is ready ({} on the ports {}).": "تانل رزرو {} آماده است ({} روی پورت‌های {}).",
"Standby: the users' ports of {} ({}) were handed to its standby tunnel ({}) in {} s, {} s after the cut.": "تانل رزرو: پورت‌های کاربران {1} ({2}) در {4} ثانیه به تانل رزرو ({3}) سپرده شد، {5} ثانیه بعد از قطع.",
"Standby: the users' ports of {} ({}) were handed to its standby tunnel ({}) in {} s.": "تانل رزرو: پورت‌های کاربران {1} ({2}) در {4} ثانیه به تانل رزرو ({3}) سپرده شد.",
"Standby: the users' ports of {} are on the tunnel again after {} s on its standby.": "تانل رزرو: پورت‌های کاربران {} بعد از {} ثانیه روی تانل رزرو، دوباره روی خود تانل است.",
"Standby of {}: the {} standby could not be built ({}); it is tried again with another type, the wait grows from {} minutes.": "تانل رزرو {}: تانل رزرو {} ساخته نشد ({})؛ با نوع دیگری دوباره امتحان می‌شود، فاصله از {} دقیقه شروع می‌شود و بیشتر می‌شود."
});

/* ---- the lanes: a tunnel that is nearly full shares its NEW connections with its second tunnel (services/lanes.py) */
I18N.add({
"Lane · Sharing {} % + {} %": "مسیر دوم · تقسیم {} ٪ + {} ٪", "Lane · Paused": "مسیر دوم · متوقف", "Lane · Nearly full": "مسیر دوم · ظرفیت تقریباً پر",
"{} % to this tunnel · {} % to {} · by capacity {} + {} Mb/s · users now {} + {} Mb/s · since {}": "{1} ٪ به این تانل · {2} ٪ به {3} · بر اساس ظرفیت {4} + {5} مگابیت · کاربران الان {6} + {7} مگابیت · از ساعت {8}",
"{} % to this tunnel · {} % to {} · by capacity {} + {} Mb/s · since {}": "{1} ٪ به این تانل · {2} ٪ به {3} · بر اساس ظرفیت {4} + {5} مگابیت · از ساعت {6}",
"{} of {} Mb/s are used ({} % of the capacity)": "{1} از {2} مگابیت مصرف می‌شود ({3} ٪ ظرفیت)",
"The lane is paused: every new connection goes to the tunnel": "مسیر دوم متوقف است: همه‌ی اتصال‌های تازه به خود تانل می‌روند",
"Share now": "همین حالا تقسیم کن",
"Split the new connections of this tunnel with its second tunnel now, by the two capacities; nobody is cut": "اتصال‌های تازه‌ی این تانل را همین حالا بین آن و تانل دومش به نسبت ظرفیت‌ها تقسیم کن؛ کسی قطع نمی‌شود",
"New connections go to the tunnel again; the ones open on the second tunnel finish there": "اتصال‌های تازه دوباره به تانل می‌روند؛ اتصال‌های باز روی تانل دوم همان‌جا تمام می‌شوند",
"The lane: this tunnel and its second tunnel share the NEW connections by capacity; open connections are never touched": "مسیر دوم: این تانل و تانل دومش اتصال‌های «تازه» را به نسبت ظرفیت بین خود تقسیم می‌کنند؛ اتصال‌های باز هرگز دست نمی‌خورند",
"Share the new connections of this tunnel with its second tunnel now? They are split by the two capacities; the connections that are open are not touched, nobody is cut. It stays until you press Stop.": "اتصال‌های تازه‌ی این تانل همین حالا با تانل دومش تقسیم شود؟ به نسبت دو ظرفیت تقسیم می‌شوند؛ اتصال‌های باز دست نمی‌خورند و کسی قطع نمی‌شود. تا «توقف» را نزنید می‌ماند.",
"✓ The new connections are shared: {} % + {} %": "✓ اتصال‌های تازه تقسیم شد: {} ٪ + {} ٪", "✓ The new connections go to the tunnel again; the ones open on the second tunnel finish there": "✓ اتصال‌های تازه دوباره به تانل می‌روند؛ اتصال‌های باز روی تانل دوم همان‌جا تمام می‌شوند",
"Add capacity by itself": "افزایش خودکار ظرفیت", "A tunnel that is nearly full shares its new connections with its second tunnel. Open connections are never touched.": "تانلی که ظرفیتش تقریباً پر شده اتصال‌های تازه‌اش را با تانل دومش تقسیم می‌کند. اتصال‌های باز هرگز دست نمی‌خورند.",
"When a tunnel is nearly full (its users use 85 % of its capacity or more for most of ten minutes) its warm standby is put to work beside it: the new connections are split between the two by capacity. Nobody is cut.": "وقتی تانلی تقریباً پر شود (کاربرانش بیشتر ده دقیقه ۸۵ ٪ ظرفیت یا بیشتر مصرف کنند) تانل رزرو آماده‌باشش کنارش به کار گرفته می‌شود: اتصال‌های تازه به نسبت ظرفیت بین این دو تقسیم می‌شود. کسی قطع نمی‌شود.",
"✓ A tunnel that is nearly full will share its new connections with its second tunnel": "✓ تانلی که تقریباً پر شود اتصال‌های تازه‌اش را با تانل دومش تقسیم می‌کند", "✓ Lanes are off: the lanes that were put by themselves end the careful way": "✓ تقسیم خودکار خاموش شد: مسیرهایی که خودکار گذاشته شده بودند با روش محتاطانه تمام می‌شوند",
"Lane: {} shares its new connections with its second tunnel ({}): {} % + {} % (capacity {} + {} Mb/s). Open connections are not touched.": "مسیر دوم: {1} اتصال‌های تازه‌اش را با تانل دومش ({2}) تقسیم می‌کند: {3} ٪ + {4} ٪ (ظرفیت {5} + {6} مگابیت). اتصال‌های باز دست نمی‌خورند.",
"Lane of {} ended. New connections go to the tunnel; the ones open on the second tunnel finish there.": "مسیر دوم {} تمام شد. اتصال‌های تازه به تانل می‌روند؛ اتصال‌های باز روی تانل دوم همان‌جا تمام می‌شوند.",
"the second tunnel is not up: every new connection goes to the tunnel until it is": "تانل دوم بالا نیست: تا بالا بیاید همه‌ی اتصال‌های تازه به تانل می‌روند",
"{n} lane rule(s) are still on the server": "{1} قانون مسیر دوم هنوز روی سرور مانده است", "the second tunnel carried nothing in its test": "تانل دوم در آزمایش خود هیچ داده‌ای نبرد", "the second tunnel could not be measured: {}": "تانل دوم اندازه‌گیری نشد: {1}",
"the lane could not be put: {}": "مسیر دوم گذاشته نشد: {1}", "this tunnel has no standby: a lane needs a ready second tunnel": "این تانل رزرو ندارد: مسیر دوم به یک تانل دوم آماده نیاز دارد", "the tunnel is {}: a lane is put when it is up": "تانل {1} است: مسیر دوم وقتی گذاشته می‌شود که تانل بالا باشد", "this tunnel forwards no port": "این تانل هیچ پورتی را هدایت نمی‌کند",
"the lane waits until both tunnels have been up for a minute": "مسیر دوم صبر می‌کند تا هر دو تانل یک دقیقه بالا بمانند",
"the tunnel is cut: its standby carries everything (the lane waits)": "تانل قطع است: تانل رزرو همه چیز را می‌برد (مسیر دوم صبر می‌کند)",
});

I18N.add({"foreign server off": "سرور خارج خاموش", "Iran server off": "سرور ایران خاموش", "both servers off": "هر دو سرور خاموش", "GRE link cut": "لینک GRE قطع", "GRE interface missing": "اینترفیس GRE نیست", "service stopped": "سرویس متوقف", "no data passes": "داده عبور نمی‌کند", "no answer": "بی‌پاسخ"});
