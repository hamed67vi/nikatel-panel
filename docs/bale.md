# Bale: a channel to the customers while the international internet is cut

When Iran's international internet is cut (as in the war) Telegram and every server abroad are out of reach, and so is the panel's own Telegram bot. What stays up is the national network: Iran servers, Iranian sites and the Iranian messengers. This is the channel
that uses it: a small **Bale bot that runs on one Iran server** of the inventory. Customers start it once (while everything works), and from then on it can tell them what is happening, by hand or by itself.

The panel is only the console. Once installed the relay needs nothing from the panel: it keeps its own list of customers, watches the outside world by itself and sends the notice by itself. A cut that also hides the panel from the Iran server changes nothing.

## What the customer sees
* They open the bot (`https://ble.ir/<bot name>`), press *Start* and get a short Persian welcome.
* They may paste their **subscription link**. The relay checks it against the panel (`<link>/info`, which Marzban and PasarGuard both answer), keeps the user name and the state, and from then on `/status` (or the *وضعیت اشتراک* button) shows the days and volume left. In a cut, when the panel cannot be asked, it shows the **last known state with its age** ("2 hours ago"), never an error.
* They can **write to you** in the same chat: any text, photo, file or voice that is not a command goes to your admin chat (see below), and your answer comes back to them. They get one "received" in ten minutes, not one for every line.
* `/stop` stops the notices, `/start` brings them back, `/unlink` takes the subscriptions off, `/id` tells their Bale number.
* When the server has lost the whole outside world for a few minutes the bot sends **the cut notice** to everybody who started it; when the outside world has been back for a while, **the back notice**. Both texts are yours to change.

## What you do
1. In Bale, talk to **@botfather**, make a bot, copy its token.
2. *Settings → Api & Key → Bale · customer notices*: paste the token, check the **hosts of the subscription links** (the first guess is the host of your Marzban / PasarGuard panels; put the subscription domain if it is another one), choose the **Iran server**, press *Install on the server*.
   The panel needs to be writable (`NIKATEL_READ_ONLY=false`), like for every install.
3. Send `/id` to your bot in Bale and put the number in *Your Bale ids* (admins) and press *Save*. An admin can send a notice from Bale itself: `/send text…` (and `/stats`, `/test`).
4. Tell the customers to start the bot **now**, while the internet works: a Telegram broadcast of the shop bot, the text of its welcome message, the subscription page. The link is shown in the box once it runs. Bale's rule is the same as Telegram's: a bot can write only to people who started it, so the list has to exist before the cut.
5. *Test to the admins* shows how a notice looks; *Send to every customer* sends one now.

## You, abroad, with nothing but Bale (the owner's case: in Turkey, on roaming, only Bale opens)
In a cut you reach neither the Iran servers nor the panel, but you do reach Bale, and so does the relay (it is on an Iran server, Bale's servers are in Iran too). So **everything you need in a cut is done from your own Bale chat with the bot** - set your Bale number as an admin (*Your Bale ids*) **before** the cut:
* A customer's message arrives in your chat as `💬 #<number> · <name>` (and the user names of the subscriptions he linked), then his text. A photo, a file or a voice arrives as a header and the copy. **Reply to it** (swipe / reply) with text, a photo, a file or a voice: it is copied to him, and you see `✓ رسید` when it arrived (or that he left Bale / Bale did not answer).
* `/r <number> text` answers without a reply. `/mute <number>` and `/unmute <number>` stop and bring back one customer's messages (a flood is also cut by itself: 15 in ten minutes). Several admins all get the messages; whoever answers first answers.
* `/send text` sends a notice to everybody; `/cut` and `/back` send the ready "cut" / "back" texts now; `/info` says whether the server sees the outside world (and for how long it has not), whether Bale answers, how many customers, what waits; `/stats` the numbers; `/test` shows the cut text to you only; `/admin` lists all of this.
* Your own plain messages in that chat are not sent to support (you would be writing to yourself); they get the list above.
Switch it off with *Customers can write to you here*; without an admin id, or with it off, a customer who writes gets the welcome as before.

## How it decides that the internet is cut
Every 15 s the relay opens a TCP connection to up to three of your **foreign servers** (their SSH ports) and, last, to `1.1.1.1`, `8.8.8.8` and `9.9.9.9` (443). If **none** answers (a refused connection counts as an answer: the road is open) for *Say it after* seconds (180 by default), the cut notice is queued, once; the next one waits 6 hours. When something answers again for *Back after* seconds (120), the back notice is queued, once. A loss of a minute is not a cut.
What it cannot see is a *partial* block (some protocols closed, the internet still there): a TCP connection to a foreign server goes through. For that the **Send** box is yours.

## Built for the cut itself
* **Bale's name may not resolve.** `tapi.bale.ai` is under `.ai`; a resolver inside Iran may have no answer for it while the national network is the only thing up. The relay keeps the last good addresses of the API host (`data/dns.json`) and connects to them directly (the name is still used for the TLS check), so it goes on after a restart in the middle of a cut.
* **A broadcast that Bale cannot take** (Bale itself unreachable) is **not** counted as failed: it stays in the outbox, remembers where it stopped, and goes on a minute later. A user who blocked the bot is marked once and not tried again.
* **A link is fetched only from the hosts you named.** A customer cannot make the Iran server request an address of their choosing: the link must be on one of the listed hosts (and port), with a plain path, no query, no `@`.
* **Light on the panel.** A status asks the panel again at most once a minute per subscription, and stops asking for two minutes after the panel did not answer (a cut), so the next customers are not kept waiting. Link tries are limited (4 a minute per customer, 40 for all).

## Where things are
| | |
|---|---|
| Program | `relay/bale_relay.py` (Python standard library only, Python 3.8+), copied to `/opt/nikatel-relay/bale_relay.py` |
| Service | `nikatel-relay.service` (restarts by itself; no new privileges, read-only system, writes only in its folder) |
| Config | `/opt/nikatel-relay/config.json` (0600: it holds the bot token). The panel writes it; the relay reads it again when it changes - no restart |
| Customers | `/opt/nikatel-relay/data/relay.db` (SQLite, 0600). *Remove* keeps it, so a later install finds the customers again |
| Notices | `/opt/nikatel-relay/outbox/*.msg`; `python3 /opt/nikatel-relay/bale_relay.py send "text"` queues one from the server's own shell, `… status` prints what the relay says about itself |
| Panel | `services/bale.py`, `api/bale.py` (`GET|PUT /api/bale`, `GET /api/bale/status`, `POST /api/bale/install|send|remove`), the box in `web/bale.js`; reviewed actions `relay_prepare`, `relay_remove`, the read `relay_status` in `ssh.py` |

## What is proved, and what is not
* **Proved (tests):** the program against a fake Bale and a fake panel on one machine - the welcome, linking, the host rules, `/status` with and without the panel, throttling, `/stop`, the customer-to-owner chat (text, photo, voice, answered by a reply, by the header, by `/r`; mute; flood; the admin unreachable), admin-only commands, a notice to everybody / to the admins only / resumed after a stop / kept while Bale is down, the cut and back notices from the watch with a fake clock, the kept addresses, the config with wrong shapes, and the program run as a real process (answers, sends a queued notice, writes its status, stops on SIGTERM); the panel side against a fake SSH that applies the real rules of every action.
* **Not proved:** a run against the **real Bale API** (it needs a bot token; the calls follow Bale's published Bot API: `getMe`, `getUpdates`, `sendMessage`, `copyMessage`, a reply keyboard, `reply_to_message`) and on a **real Iran server in a real cut**. Bale does not publish numeric limits for broadcasts: the relay sends at most about ten messages a second and obeys a `429`'s `retry_after`. Do the first run with *Test to the admins* before the first real notice.
