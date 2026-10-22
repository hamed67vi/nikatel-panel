# Shop bot Mini App

A Telegram Mini App for the customers of the PHP shop bot. It lives in `<bot dir>/app/` next to the
bot and is opened from the bot's **Mini App** button (`BOT_STATES.miniAppState = on`; the bot builds the
button from `$botUrl` in `baseInfo.php`). Four files, no build step:

| file | |
|---|---|
| `api.php` | the API: verifies Telegram's `initData` (HMAC with the bot token), reads and writes the bot's MariaDB, talks to the panels |
| `index.html`, `app.js`, `app.css` | the app: RTL Persian, follows the Telegram theme, bottom tabs خانه · خرید · سرویس‌ها · کیف پول · بیشتر |

Deploy: `bash scripts/deploy-miniapp.sh root@bot-host /var/www/<bot>` (or `--local` on the bot's own
server). It keeps the previous files in `<bot dir>/../.miniapp-prev-<bot dir name>/<time>/` - NEVER inside the web root, where
they would be served to anybody - and runs `php -l`.

## What the customer can do

* **Home** - active services with live usage from the panel, quick actions.
* **Buy** - plans grouped by category / server with the agent discount applied; pay from the wallet;
  the account is created on the plan's panel (Marzban, PasarGuard, Marzneshin - the plan's
  `custom_sni` JSON says which groups / inbounds / services); subscription link, QR, configs; cashback.
* **Services** - usage bar, expiry, status; copy the subscription link and every config; QR; renew
  (same plan again: volume reset, days added), more volume, more days (prices from `increase_plan` /
  `increase_day` or `gbPrice` / `dayPrice`); report an outage.
* **Wallet** - balance, card-to-card top-up like in the bot (exact amount with identifying digits,
  the card or the gift-card / USDT text depending on the user's history), receipt photo upload →
  the admins get the picture with the bot's own ✅ / ❌ buttons (`approvePayment<hash>` /
  `declineAction_<hash>`), history of payments and cashbacks, gift codes.
* **More** - tickets (new, reply, history), referral link and numbers, connection apps, lucky wheel.

## How it stays compatible with the bot

Every write mirrors the bot's own rows, so the bot's admin side and the panel's Shop bot pages see
the Mini App's activity as if it came from the bot:

* `pays` - `BUY_SUB` paid with the wallet (`processing_wallet` → `paid_with_wallet`), `INCREASE_WALLET`
  (`pending` → `have_sent` with `message_id`, `chat_id`, `admins_messages`), `INCREASE_VOLUME`,
  `INCREASE_TIME`.
* `orders_list` - the same columns the bot's `insertOrder` writes (plan id in `fileid`, links as JSON).
* `server_info.ucount` / `server_plans.acount` capacity, `increase_order`, `cashback_log`, `chats` +
  `chats_info`, `gift_codes`, `wheel_log`, `outage_reports`.
* Texts come from `settings/values.php` plus the panel's `text_overrides.json` / `button_overrides.json`.

Rules kept from the bot: agents pay their `discount_percent` (0 - 100 %, never a negative price); the wallet can never go negative (row
lock + transaction, the panel call happens inside it and a refusal rolls everything back; an account made on the panel for an order
that then could not be saved is deleted again, a panel change is put back); a renewal is written like the bot writes it
(`RENEW_ACCOUNT`, the order id, one `increase_order` row), extra volume / days as `INCREASE_VOLUME_<order>_<id>` /
`INCREASE_DAY_<order>_<id>`; a plan that costs nothing is the one-per-customer test account (`users.freetrial`); on-hold
plans start at the first connection; a new user without payment history sees the gift-card / USDT
text instead of the card number; three receipts waiting = no new top-up.

## Security

* Only requests with a valid, fresh (< 24 h) `initData` signature are answered; the user id comes
  from the signature, never from the client.
* Every query is a prepared statement; uploads are checked by size (6 MB) and real MIME type and are
  sent to Telegram, never stored on disk.
* Errors go to `settings/miniapp_error.log`; the client only sees a sentence.
* An empty bot token verifies nothing (its signing key would be public).
* A payment carries a key (`rid`, kept in `pays.payid` as `mini:<key>`): the same key again shows the first result and charges
  nothing, so a lost answer followed by "try again" does not pay twice. One receipt upload at a time per top-up (named lock),
  one top-up request at a time per customer (row lock), one slot of a server taken in one statement before the panel is called.
* Gift codes: 10 wrong codes in 15 minutes stop the guessing; a ticket reply is limited to 20 in 10 minutes.
* `tests/miniapp_security/` is the attack harness (forged and replayed initData, parallel payments, odd amounts, injection,
  uploads, a fake Telegram and a fake panel); `NIKATEL_MINIAPP_SEC=1 pytest tests/test_miniapp_security.py` runs it.
* The folder the bot lives in must not be served whole: only `app/` (index.html, app.js, app.css, api.php), `bot.php`, `pay/` and
  `settings/subLink.php` are meant to be reachable; logs, traces, backups (`*.bak*`, `*.sql`), `*.json`, `*.txt` and the cron
  scripts of `settings/` must answer 404 (Caddy `respond @secret 404`, nginx `location ~ ... { return 404; }`).
* The bot token, database password and panel credentials stay in `baseInfo.php` / the database on the
  bot's server. Nothing in this directory identifies a real installation.
