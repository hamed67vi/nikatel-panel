# Shop bot Mini App

A Telegram Mini App for the customers of the PHP shop bot. It lives in `<bot dir>/app/` next to the
bot and is opened from the bot's **Mini App** button (`BOT_STATES.miniAppState = on`; the bot builds the
button from `$botUrl` in `baseInfo.php`). Four files, no build step:

| file | |
|---|---|
| `api.php` | the API: verifies Telegram's `initData` (HMAC with the bot token), reads and writes the bot's MariaDB, talks to the panels |
| `index.html`, `app.js`, `app.css` | the app: RTL Persian, follows the Telegram theme, bottom tabs خانه · خرید · سرویس‌ها · کیف پول · بیشتر |

Deploy: `bash scripts/deploy-miniapp.sh root@bot-host /var/www/<bot>` (or `--local` on the bot's own
server). It keeps the previous files in `app/.prev/` and runs `php -l`.

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

Rules kept from the bot: agents pay their `discount_percent`; the wallet can never go negative (row
lock + transaction, the panel call happens inside it and a refusal rolls everything back); on-hold
plans start at the first connection; a new user without payment history sees the gift-card / USDT
text instead of the card number; three receipts waiting = no new top-up.

## Security

* Only requests with a valid, fresh (< 24 h) `initData` signature are answered; the user id comes
  from the signature, never from the client.
* Every query is a prepared statement; uploads are checked by size (6 MB) and real MIME type and are
  sent to Telegram, never stored on disk.
* Errors go to `settings/miniapp_error.log`; the client only sees a sentence.
* The bot token, database password and panel credentials stay in `baseInfo.php` / the database on the
  bot's server. Nothing in this directory identifies a real installation.
