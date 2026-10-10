# Shop bot administration

How the panel runs the PHP shop bot (WizWiz-based, MariaDB, on its own server) and what it expects there.
Setup of the server side is in [`operations.md`](operations.md#the-shop-bot-server); the customers' app is in
[`../bot/miniapp/README.md`](../bot/miniapp/README.md).

## Pages
| tab | what you do |
|---|---|
| Overview | service status, sales (today / 7 d / 30 d), counts, switches, import of the real database |
| Payments | *To confirm* (receipt sent) · *Awaiting receipt* · Approved · Declined · All. ✅ credits the wallet, marks the payment, edits the admins' messages and tells the user; ❌ tells the user the reason. A payment is settled once, never twice |
| Orders | all orders with status; activate / deactivate |
| Users | search; detail with wallet (add / subtract / set), agent on / off, discount percent, ban, message from the bot |
| Plans | **quick add**: category, server, time, volume, price → a complete plan with an automatic Persian title and the panel settings copied from the server's other plans; edit / on-off / delete; the detailed form has description, stock, IP limit, panel settings JSON |
| Categories | the groups customers pick before plans (nested), on / off |
| Servers | the panels the bot sells from (Marzban, PasarGuard, Marzneshin, 3x-ui); add from a panel this panel already knows |
| Discounts, Gift codes | codes with audience (all / agents / users…) and target (all / plan / category / server); gift codes with amount, uses, expiry |
| Broadcast | queue a text to all / buyers / non-buyers / agents; cancel |
| Tickets | read, reply (sent by the bot), close |
| Texts | every text and button of the bot; an override is saved as JSON (`settings/text_overrides.json`, `button_overrides.json`); empty = back to the default |
| Settings | the bot's switches grouped (Sales, Wallet and payment, Prices, Support and channels, Users, Links and configs, Extras) and the payment keys |
| Tools | **Mini App** install / update, backups, apps list, top-up prices, tables, read-only SQL, stats, webhook, command menu, restart PHP, dump, clear logs |

## Payment states
`pending` (asked, no receipt) → `have_sent` (receipt sent; an admin must approve) → `approved` / `declined`
(+ `expired_*`). Wallet purchases: `processing_wallet` → `paid_with_wallet`. Types: `INCREASE_WALLET`,
`BUY_SUB`, `INCREASE_VOLUME`, `INCREASE_TIME`.

## How it is done safely
* Reads go through the reviewed script `bot_query` (SELECT / SHOW only); writes through `bot_sql` (one
  UPDATE / INSERT / DELETE / CREATE TABLE IF NOT EXISTS). Every text value is a `FROM_BASE64("…")`
  literal, so quotes, backslashes, semicolons and newlines can never break out of the statement.
* Telegram calls (messages, keyboards, command menu, webhook) run on the bot server with the bot's own
  token via `bot_tg_json`; the token is never copied to the panel. The method list is fixed.
* The bot's texts are read with `bot_texts` (`php -r` over `values.php`); the first save of an
  override adds one `include` line to the bot's `config.php` (backup + `php -l` check).
* `users.discount_percent` is the bot's own JSON (`normal`, `plans`, `servers`); the panel changes
  only `normal` and keeps the rest.
* Every change writes an event ("Bot plan #3 changed …") so the Events log shows who did what.

## Tests
`tests/test_shopbot.py`, `tests/test_shopbot_admin.py`: a fake database checks that every statement the
panel builds matches the scripts' regexes, and the approve / decline / wallet rules hold.
